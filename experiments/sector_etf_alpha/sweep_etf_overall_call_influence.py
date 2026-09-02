"""Sweep sector ETF overall-score influence on stock CALL scores.

This compares ETF `Score.overall` as a sector participation wave against the
existing `sector_phase` oscillator. It is experiment-local: no production
scoring code is modified.

The hot path uses NumPy arrays and process-level batching. Each worker loads the
same compact `.npz` matrix once, evaluates many candidate curves, and returns
only aggregate metrics.

Outputs:
  experiments/sector_etf_alpha/dd_probe/etf_call_influence_all_v46_1825.csv
  experiments/sector_etf_alpha/dd_probe/etf_call_influence_top_v46_1825.csv
  experiments/sector_etf_alpha/dd_probe/etf_call_influence_arch_summary_v46_1825.csv
  experiments/sector_etf_alpha/dd_probe/etf_call_influence_knobs_v46_1825.csv
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import polars as pl

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

VERSION_ID = 46
LOOKBACK_DAYS = 1825

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
OUT = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
COHORT = LOCAL / "cohort_v46_1825.parquet"
ETF_SCORES = LOCAL / "etf_all_scores_v46_2021-05-12_2026-05-11.parquet"
MATRIX = LOCAL / "etf_call_influence_matrix_v46_1825.npz"

SECTOR_ETFS = ["XLK", "XLV", "XLY", "XLI", "XLB", "XLRE", "XLF", "XLE", "XLP", "XLU", "XLC"]
THRESHOLDS = [70, 75, 80, 85, 90]
BASE_WEIGHTS = {70: 1.0, 75: 2.0, 80: 3.0, 85: 2.0, 90: 1.0}
ARCHES = [
    "ETF_TREND_BOTH",
    "ETF_TREND_DAMP_ONLY",
    "ETF_TREND_BOOST_ONLY",
    "ETF_MEANREV",
    "PHASE_MEANREV",
    "PHASE_TREND",
    "ETF_PHASE_BLEND",
]

G: dict[str, np.ndarray] = {}
BASE: dict[int, dict[str, float]] = {}


@dataclass(frozen=True)
class Candidate:
    arch: str
    center: float
    width: float
    wave_power: float
    score_power: float
    gate_lo: float
    gate_hi: float
    alpha_pos: float
    alpha_neg: float
    target_up: float
    target_down: float
    blend: float


def _load_etf_scores() -> pl.DataFrame:
    if not ETF_SCORES.exists():
        raise SystemExit(
            f"missing {ETF_SCORES}; run analyze_etf_score_winrates.py first to build ETF score cache"
        )
    return pl.read_parquet(ETF_SCORES).rename({"symbol": "sector_etf", "overall": "sec_etf_overall"})


def build_matrix(force: bool = False) -> Path:
    if MATRIX.exists() and not force:
        return MATRIX

    if not COHORT.exists():
        raise SystemExit(f"missing cohort: {COHORT}")

    cols = [
        "symbol", "date", "overall", "signal_type", "sector_etf",
        "wr_1", "wr_7", "wr_15", "wr_30",
        "sec_etf_pct_ema50", "sec_etf_rsi", "sec_etf_macd",
    ]
    df = pl.read_parquet(COHORT, columns=cols)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, context="sector ETF overall call influence matrix")

    etf = _load_etf_scores()
    df = df.join(etf.select(["sector_etf", "date", "sec_etf_overall"]), on=["sector_etf", "date"], how="left")
    df = df.filter((pl.col("signal_type") == "CALL") & pl.col("sec_etf_overall").is_not_null())
    df = df.filter(pl.col("sector_etf").is_in(SECTOR_ETFS))

    ema_phase = (pl.col("sec_etf_pct_ema50") / 8.1919).clip(-1.0, 1.0)
    rsi_phase = ((pl.col("sec_etf_rsi") - 50.0) / 22.7129).clip(-1.0, 1.0)
    phase = ((ema_phase * 0.4028) + (rsi_phase * 0.5655)) / (0.4028 + 0.5655)
    df = df.with_columns(phase.fill_null(0.0).alias("sector_phase_v4"))

    arrays = {
        "overall": df["overall"].to_numpy().astype(np.float64),
        "wr1": df["wr_1"].to_numpy().astype(np.float64),
        "wr7": df["wr_7"].to_numpy().astype(np.float64),
        "wr15": df["wr_15"].to_numpy().astype(np.float64),
        "wr30": df["wr_30"].to_numpy().astype(np.float64),
        "etf_score": df["sec_etf_overall"].to_numpy().astype(np.float64),
        "phase": df["sector_phase_v4"].to_numpy().astype(np.float64),
    }
    np.savez_compressed(MATRIX, **arrays)
    print(f"[matrix] saved {MATRIX} rows={len(df):,}", flush=True)
    return MATRIX


def _init_worker(matrix_path: str) -> None:
    global G, BASE
    data = np.load(matrix_path)
    G = {k: data[k] for k in data.files}
    BASE = _baseline_metrics(G["overall"])


def _baseline_metrics(scores: np.ndarray) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for t in THRESHOLDS:
        mask = (scores >= t) & np.isfinite(G["wr7"])
        n = int(mask.sum())
        out[t] = {
            "n": n,
            "wr1": float(np.nanmean(G["wr1"][mask])) if n else math.nan,
            "wr7": float(np.nanmean(G["wr7"][mask])) if n else math.nan,
            "wr15": float(np.nanmean(G["wr15"][mask])) if n else math.nan,
            "wr30": float(np.nanmean(G["wr30"][mask])) if n else math.nan,
        }
    return out


def _wave_for_candidate(c: Candidate) -> np.ndarray:
    if c.arch.startswith("ETF"):
        etf_wave = np.clip((G["etf_score"] - c.center) / c.width, -1.0, 1.0)
        etf_wave = np.sign(etf_wave) * (np.abs(etf_wave) ** c.wave_power)
        if c.arch == "ETF_PHASE_BLEND":
            phase_trend = -G["phase"]
            wave = c.blend * etf_wave + (1.0 - c.blend) * phase_trend
            return np.clip(wave, -1.0, 1.0)
        return etf_wave

    phase = G["phase"]
    if c.arch == "PHASE_MEANREV":
        return -phase
    if c.arch == "PHASE_TREND":
        return phase
    raise ValueError(f"unknown arch: {c.arch}")


def _new_scores(c: Candidate) -> np.ndarray:
    old = G["overall"]
    denom = max(c.gate_hi - c.gate_lo, 1.0)
    score_norm = np.clip((old - c.gate_lo) / denom, 0.0, 1.0) ** c.score_power
    in_gate = (old >= c.gate_lo) & (old <= c.gate_hi)

    wave = _wave_for_candidate(c)
    pos = np.clip(wave, 0.0, 1.0)
    neg = np.clip(-wave, 0.0, 1.0)

    if c.arch == "ETF_MEANREV":
        pos, neg = neg, pos

    alpha_pos = c.alpha_pos
    alpha_neg = c.alpha_neg
    if c.arch == "ETF_TREND_DAMP_ONLY":
        alpha_pos = 0.0
    elif c.arch == "ETF_TREND_BOOST_ONLY":
        alpha_neg = 0.0

    boost = alpha_pos * pos * score_norm * (c.target_up - old)
    damp = -alpha_neg * neg * score_norm * (old - c.target_down)
    new = old + np.where(in_gate, boost + damp, 0.0)
    return np.clip(np.rint(new), 0.0, 100.0)


def _eval_one(c: Candidate) -> dict[str, float | int | str]:
    scores = _new_scores(c)
    old = G["overall"]
    changed = scores != old
    row: dict[str, float | int | str] = {
        "arch": c.arch,
        "center": c.center,
        "width": c.width,
        "wave_power": c.wave_power,
        "score_power": c.score_power,
        "gate_lo": c.gate_lo,
        "gate_hi": c.gate_hi,
        "alpha_pos": c.alpha_pos,
        "alpha_neg": c.alpha_neg,
        "target_up": c.target_up,
        "target_down": c.target_down,
        "blend": c.blend,
        "n_changed": int(changed.sum()),
        "avg_abs_delta": float(np.abs(scores - old).mean()),
        "promoted_70": int(((old < 70) & (scores >= 70)).sum()),
        "demoted_70": int(((old >= 70) & (scores < 70)).sum()),
    }

    util = 0.0
    breaches = 0
    for t in THRESHOLDS:
        mask = (scores >= t) & np.isfinite(G["wr7"])
        n = int(mask.sum())
        base = BASE[t]
        wr1 = float(np.nanmean(G["wr1"][mask])) if n else math.nan
        wr7 = float(np.nanmean(G["wr7"][mask])) if n else math.nan
        wr15 = float(np.nanmean(G["wr15"][mask])) if n else math.nan
        wr30 = float(np.nanmean(G["wr30"][mask])) if n else math.nan
        d1 = (wr1 - base["wr1"]) * 100.0 if n else math.nan
        d7 = (wr7 - base["wr7"]) * 100.0 if n else math.nan
        d15 = (wr15 - base["wr15"]) * 100.0 if n else math.nan
        d30 = (wr30 - base["wr30"]) * 100.0 if n else math.nan
        nd = ((n - base["n"]) / base["n"] * 100.0) if base["n"] else 0.0
        row[f"call_{t}_n"] = n
        row[f"call_{t}_dN_pct"] = nd
        row[f"call_{t}_wr7"] = wr7
        row[f"call_{t}_dWR1"] = d1
        row[f"call_{t}_dWR7"] = d7
        row[f"call_{t}_dWR15"] = d15
        row[f"call_{t}_dWR30"] = d30

        if n >= 30:
            util += BASE_WEIGHTS[t] * d7
            util += 0.20 * BASE_WEIGHTS[t] * (d15 + d30)
            if t in (70, 75, 80) and d7 < -0.35:
                breaches += 1
            if t in (75, 80, 85) and n < int(base["n"] * 0.85):
                breaches += 1
            if t in (70, 75) and n > int(base["n"] * 1.35) and d7 < 0:
                breaches += 1
    util -= 5.0 * breaches
    row["breaches"] = breaches
    row["util"] = util
    return row


def _eval_batch(batch: list[Candidate]) -> list[dict[str, float | int | str]]:
    return [_eval_one(c) for c in batch]


def _sample_candidates(n: int, seed: int) -> list[Candidate]:
    rng = np.random.default_rng(seed)
    cands: list[Candidate] = []
    per_arch = max(1, n // len(ARCHES))

    for arch in ARCHES:
        for _ in range(per_arch):
            if arch.startswith("ETF"):
                center = float(rng.uniform(54.0, 66.0))
                width = float(rng.uniform(8.0, 26.0))
            else:
                center = 60.0
                width = 16.0
            alpha_pos = float(rng.uniform(0.0, 0.95))
            alpha_neg = float(rng.uniform(0.0, 0.95))
            if arch == "ETF_TREND_DAMP_ONLY":
                alpha_pos = 0.0
            elif arch == "ETF_TREND_BOOST_ONLY":
                alpha_neg = 0.0
            cands.append(
                Candidate(
                    arch=arch,
                    center=center,
                    width=width,
                    wave_power=float(rng.uniform(0.75, 2.75)),
                    score_power=float(rng.uniform(0.75, 2.50)),
                    gate_lo=float(rng.choice([68, 70, 72, 75])),
                    gate_hi=float(rng.choice([80, 84, 88, 92])),
                    alpha_pos=alpha_pos,
                    alpha_neg=alpha_neg,
                    target_up=float(rng.uniform(82.0, 94.0)),
                    target_down=float(rng.uniform(55.0, 70.0)),
                    blend=float(rng.uniform(0.0, 1.0)),
                )
            )

    # Add known anchors for interpretability.
    cands.extend(
        [
            Candidate("ETF_TREND_BOTH", 60.0, 15.0, 1.5, 1.4, 70, 84, 0.45, 0.45, 90, 62, 1.0),
            Candidate("ETF_TREND_DAMP_ONLY", 60.0, 15.0, 1.5, 1.4, 70, 84, 0.0, 0.65, 90, 65, 1.0),
            Candidate("ETF_TREND_BOOST_ONLY", 60.0, 15.0, 1.5, 1.4, 70, 84, 0.65, 0.0, 90, 65, 1.0),
            Candidate("ETF_MEANREV", 60.0, 15.0, 1.5, 1.4, 70, 84, 0.45, 0.45, 90, 62, 1.0),
            Candidate("PHASE_MEANREV", 60.0, 16.0, 1.766, 1.361, 70, 84, 0.8971, 0.5606, 90.7926, 57.3281, 0.0),
            Candidate("PHASE_TREND", 60.0, 16.0, 1.766, 1.361, 70, 84, 0.8971, 0.5606, 90.7926, 57.3281, 0.0),
            Candidate("ETF_PHASE_BLEND", 60.0, 15.0, 1.5, 1.4, 70, 84, 0.55, 0.55, 90, 62, 0.5),
        ]
    )
    return cands


def _chunks(items: list[Candidate], size: int) -> Iterable[list[Candidate]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def summarize_knobs(df: pl.DataFrame) -> pl.DataFrame:
    top = df.sort("util", descending=True).head(max(50, min(250, df.height // 20)))
    rows = []
    for arch in ARCHES:
        a = top.filter(pl.col("arch") == arch)
        if a.is_empty():
            continue
        rows.append(
            {
                "arch": arch,
                "top_n": a.height,
                "util_median": a["util"].median(),
                "util_max": a["util"].max(),
                "center_median": a["center"].median(),
                "width_median": a["width"].median(),
                "wave_power_median": a["wave_power"].median(),
                "score_power_median": a["score_power"].median(),
                "gate_lo_mode": a["gate_lo"].mode().sort().item(0),
                "gate_hi_mode": a["gate_hi"].mode().sort().item(0),
                "alpha_pos_median": a["alpha_pos"].median(),
                "alpha_neg_median": a["alpha_neg"].median(),
                "target_up_median": a["target_up"].median(),
                "target_down_median": a["target_down"].median(),
                "blend_median": a["blend"].median(),
                "call_70_dWR7_median": a["call_70_dWR7"].median(),
                "call_75_dWR7_median": a["call_75_dWR7"].median(),
                "call_80_dWR7_median": a["call_80_dWR7"].median(),
                "n_changed_median": a["n_changed"].median(),
            }
        )
    return pl.DataFrame(rows).sort("util_max", descending=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--batch-size", type=int, default=96)
    ap.add_argument("--seed", type=int, default=46060)
    ap.add_argument("--force-matrix", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    matrix_path = build_matrix(force=args.force_matrix)
    cands = _sample_candidates(args.n, args.seed)
    batches = list(_chunks(cands, args.batch_size))

    print(
        f"[sweep] candidates={len(cands):,} workers={args.workers} batches={len(batches):,} batch_size={args.batch_size}",
        flush=True,
    )
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(str(matrix_path),)) as ex:
        futures = [ex.submit(_eval_batch, b) for b in batches]
        done = 0
        for fut in as_completed(futures):
            rows.extend(fut.result())
            done += 1
            if done % max(1, len(batches) // 20) == 0 or done == len(batches):
                print(f"[sweep] {done}/{len(batches)} batches rows={len(rows):,} elapsed={time.time() - t0:.1f}s", flush=True)

    df = pl.DataFrame(rows).sort("util", descending=True)
    suffix = f"v{VERSION_ID}_{LOOKBACK_DAYS}"
    all_path = OUT / f"etf_call_influence_all_{suffix}.csv"
    top_path = OUT / f"etf_call_influence_top_{suffix}.csv"
    strict_path = OUT / f"etf_call_influence_strict_{suffix}.csv"
    arch_path = OUT / f"etf_call_influence_arch_summary_{suffix}.csv"
    knob_path = OUT / f"etf_call_influence_knobs_{suffix}.csv"

    arch = (
        df.group_by("arch")
        .agg(
            [
                pl.len().alias("variants"),
                pl.col("util").max().alias("util_max"),
                pl.col("util").mean().alias("util_mean"),
                pl.col("call_70_dWR7").max().alias("best_call_70_dWR7"),
                pl.col("call_75_dWR7").max().alias("best_call_75_dWR7"),
                pl.col("call_80_dWR7").max().alias("best_call_80_dWR7"),
                pl.col("n_changed").median().alias("n_changed_median"),
                pl.col("breaches").mean().alias("breaches_mean"),
            ]
        )
        .sort("util_max", descending=True)
    )
    knobs = summarize_knobs(df)
    strict = df.filter(
        (pl.col("breaches") == 0)
        & (pl.col("call_80_dWR7") >= 0)
        & (pl.col("call_80_dWR15") >= 0)
        & (pl.col("call_80_dWR30") >= 0)
        & (pl.col("call_85_dWR7") >= 0)
        & (pl.col("call_85_dWR15") >= 0)
        & (pl.col("call_85_dWR30") >= 0)
        & (pl.col("call_90_dWR7") >= 0)
        & (pl.col("call_80_n") >= 1000)
        & (pl.col("call_85_n") >= 300)
        & (pl.col("call_90_n") >= 70)
    ).sort("util", descending=True)

    df.write_csv(all_path)
    df.head(200).write_csv(top_path)
    strict.write_csv(strict_path)
    arch.write_csv(arch_path)
    knobs.write_csv(knob_path)
    print(f"[write] {all_path}", flush=True)
    print(f"[write] {top_path}", flush=True)
    print(f"[write] {strict_path}", flush=True)
    print(f"[write] {arch_path}", flush=True)
    print(f"[write] {knob_path}", flush=True)
    print("[top10]", flush=True)
    print(
        df.head(10).select(
            [
                "arch", "util", "breaches", "n_changed", "promoted_70", "demoted_70",
                "center", "width", "gate_lo", "gate_hi", "alpha_pos", "alpha_neg",
                "call_70_dWR7", "call_75_dWR7", "call_80_dWR7", "call_85_dWR7",
            ]
        ),
        flush=True,
    )
    print("[arch]", flush=True)
    print(arch, flush=True)


if __name__ == "__main__":
    main()
