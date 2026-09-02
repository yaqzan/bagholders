"""Portfolio MC comparison for ETF-overall CALL dampening candidates.

Experiment-local only. This patches `monte_carlo.load_signals` at runtime so
stock CALL scores can be dampened when their sector ETF `Score.overall` does
not confirm. PUT signals are unchanged for ETFD_* variants.

Default variants:
  PROD              production v46
  ETFD_CONSERVE     conservative high-tier dampener, stock gate 75-92
  ETFD_WIDE         wider dampener, stock gate 68-92
  V4                raw prior sector_phase + RSU candidate, as a control
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import pickle
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.sector_etf_alpha.sector_dd_probe import build_lookup as build_v4_lookup
from experiments.sector_etf_alpha.sector_dd_probe import patch_signal_loaders as patch_v4_signal_loaders

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
SCREEN = ROOT / ".cache" / "sector_etf_screen"
OUT = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
OUT.mkdir(parents=True, exist_ok=True)

SECTOR_ETF_MAP = {
    "technology": "XLK",
    "healthcare": "XLV",
    "consumer-cyclical": "XLY",
    "industrials": "XLI",
    "basic-materials": "XLB",
    "real-estate": "XLRE",
    "financial-services": "XLF",
    "energy": "XLE",
    "consumer-defensive": "XLP",
    "utilities": "XLU",
    "communication-services": "XLC",
}


@dataclass(frozen=True)
class ETFDampParams:
    center: float
    width: float
    wave_power: float
    score_power: float
    gate_lo: float
    gate_hi: float
    alpha_neg: float
    target_down: float


CANDIDATES = {
    # Best strict ETF_TREND_DAMP_ONLY row with broad but clean high-tier gains.
    "ETFD_WIDE": ETFDampParams(
        center=55.1370,
        width=21.7117,
        wave_power=1.6881,
        score_power=1.2489,
        gate_lo=68.0,
        gate_hi=92.0,
        alpha_neg=0.7646,
        target_down=57.7896,
    ),
    # Conservative no-boost row, focused on high stock scores only.
    "ETFD_CONSERVE": ETFDampParams(
        center=57.7028,
        width=22.8578,
        wave_power=1.8200,
        score_power=2.0373,
        gate_lo=75.0,
        gate_hi=92.0,
        alpha_neg=0.7901,
        target_down=56.1145,
    ),
}


def _wave(etf_score: float, p: ETFDampParams) -> float:
    x = max(-1.0, min(1.0, (etf_score - p.center) / p.width))
    return math.copysign(abs(x) ** p.wave_power, x)


def transform_score(overall: int, etf_score: int | None, p: ETFDampParams) -> int:
    if etf_score is None or overall < p.gate_lo or overall > p.gate_hi:
        return int(overall)
    wave = _wave(float(etf_score), p)
    neg = max(0.0, -wave)
    if neg <= 0:
        return int(overall)
    score_norm = max(0.0, min(1.0, (overall - p.gate_lo) / max(1.0, p.gate_hi - p.gate_lo))) ** p.score_power
    delta = -p.alpha_neg * neg * score_norm * (overall - p.target_down)
    return int(max(0, min(100, round(overall + delta))))


def build_etfd_lookup(label: str, p: ETFDampParams, version_id: int) -> dict:
    cache = LOCAL / f"{label.lower()}_lookup_v{version_id}.pkl"
    meta = LOCAL / f"{label.lower()}_lookup_v{version_id}.json"
    if cache.exists():
        with open(cache, "rb") as f:
            lookup = pickle.load(f)
        print(f"[lookup] cache hit {label}: {len(lookup):,} entries", flush=True)
        return lookup

    peaks_path = LOCAL / f"peaks_v{version_id}_1825.parquet"
    if not peaks_path.exists():
        raise SystemExit(f"missing {peaks_path}; run build_cohort.py for v{version_id}")
    etf_scores_path = LOCAL / "etf_all_scores_v46_2021-05-12_2026-05-11.parquet"
    if not etf_scores_path.exists():
        raise SystemExit(f"missing {etf_scores_path}; run analyze_etf_score_winrates.py first")

    peaks = pl.read_parquet(peaks_path).filter(pl.col("overall") >= 70)
    stock_meta = pl.read_parquet(SCREEN / "stock_meta.parquet").select(["symbol", "sector"]).unique("symbol")
    sec_map = pl.DataFrame({"sector": list(SECTOR_ETF_MAP), "sector_etf": list(SECTOR_ETF_MAP.values())})
    etf_scores = (
        pl.read_parquet(etf_scores_path)
        .rename({"symbol": "sector_etf", "overall": "sec_etf_overall"})
        .select(["sector_etf", "date", "sec_etf_overall"])
    )

    df = (
        peaks.select(["symbol", "date", "overall"])
        .join(stock_meta, on="symbol", how="left")
        .join(sec_map, on="sector", how="left")
        .join(etf_scores, on=["sector_etf", "date"], how="left")
    )
    df = df.with_columns(
        pl.struct(["overall", "sec_etf_overall"]).map_elements(
            lambda x: transform_score(int(x["overall"]), x["sec_etf_overall"], p),
            return_dtype=pl.Int64,
        ).alias("new_overall")
    )

    lookup = {
        (sym, dt): (int(ov), int(new_ov))
        for sym, dt, ov, new_ov in df.select(["symbol", "date", "overall", "new_overall"]).iter_rows()
    }
    with open(cache, "wb") as f:
        pickle.dump(lookup, f)
    meta.write_text(
        json.dumps(
            {
                "label": label,
                "version_id": version_id,
                "params": asdict(p),
                "entries": len(lookup),
                "changed": sum(1 for ov, new_ov in lookup.values() if ov != new_ov),
                "dropped_below_70": sum(1 for ov, new_ov in lookup.values() if ov >= 70 and new_ov < 70),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[lookup] saved {label}: {len(lookup):,} entries -> {cache}", flush=True)
    return lookup


def patch_call_loader(mc, lookup: dict, overflow: int = 70):
    orig_call = mc.load_signals

    def patched_load_signals(version, d_start, d_end):
        sigs = orig_call(version, d_start, d_end)
        kept = []
        modified = 0
        dropped = 0
        for s in sigs:
            key = (s.symbol_id, s.date.isoformat() if hasattr(s.date, "isoformat") else str(s.date))
            entry = lookup.get(key)
            if entry is None:
                kept.append(s)
                continue
            orig_ov, new_ov = entry
            if new_ov >= overflow:
                if new_ov != orig_ov:
                    s.overall = new_ov
                    modified += 1
                kept.append(s)
            else:
                dropped += 1
        kept.sort(key=lambda s: (s.date, -int(s.overall)))
        print(f"[patch_call] orig={len(sigs):,} kept={len(kept):,} modified={modified:,} dropped={dropped:,}", flush=True)
        return kept

    mc.load_signals = patched_load_signals
    return orig_call


def get_version(version_id: int):
    try:
        return AlgorithmVersion.get(AlgorithmVersion.id == version_id)
    except AlgorithmVersion.DoesNotExist:
        raise SystemExit(f"AlgorithmVersion id={version_id} not found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="2022,2024,22-now,5y")
    ap.add_argument("--variants", default="PROD,ETFD_CONSERVE,ETFD_WIDE,V4")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--version-id", type=int, default=46)
    ap.add_argument("--out", default=str(OUT / "prod_vs_etfd_mc.csv"))
    ap.add_argument("--no-mp", action="store_true")
    args = ap.parse_args()

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""
    if args.no_mp:
        os.environ["MC_NO_MP"] = "1"

    import monte_carlo as mc

    windows = [x.strip() for x in args.windows.split(",") if x.strip()]
    variants = [x.strip().upper() for x in args.variants.split(",") if x.strip()]
    wanted = set(windows)
    mc_windows = [w for w in mc.WINDOWS if w[0] in wanted]
    version = get_version(args.version_id)
    out = Path(args.out).resolve()
    rows = []

    orig_call, orig_put = mc.load_signals, mc.load_put_signals
    lookup_cache: dict[str, dict] = {}

    def flush_rows() -> None:
        if rows:
            out.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(rows).write_csv(out)

    try:
        for variant in variants:
            mc.load_signals = orig_call
            mc.load_put_signals = orig_put

            if variant == "PROD":
                params = {}
            elif variant == "V4":
                lookup_cache.setdefault("V4", build_v4_lookup("V4"))
                patch_v4_signal_loaders(mc, lookup_cache["V4"])
                params = {"control": "raw V4 sector_phase + RSU"}
            elif variant in CANDIDATES:
                lookup_cache.setdefault(variant, build_etfd_lookup(variant, CANDIDATES[variant], args.version_id))
                patch_call_loader(mc, lookup_cache[variant])
                params = asdict(CANDIDATES[variant])
            else:
                raise SystemExit(f"unknown variant {variant}")

            print(f"\n[variant] {variant}", flush=True)
            for label, d_start, d_end in mc_windows:
                t0 = time.time()
                row = dict(mc.run_window(label, d_start, d_end, version)["seeded"])
                row["variant"] = variant
                row["window"] = label
                row["n"] = args.n
                row["version_id"] = args.version_id
                row["params_json"] = json.dumps(params, sort_keys=True)
                rows.append(row)
                flush_rows()
                print(
                    f"  {label}: mean={row['mean_ret']:+.1f}% med={row['med_ret']:+.1f}% "
                    f"worstDD={row['worst_dd']:.1f}% meanDD={row['mean_dd']:.1f}% "
                    f"calls={row['call_trades']:.1f} puts={row['put_trades']:.1f} "
                    f"elapsed={time.time() - t0:.0f}s",
                    flush=True,
                )
    finally:
        mc.load_signals = orig_call
        mc.load_put_signals = orig_put

    flush_rows()
    df = pl.DataFrame(rows)
    prod = df.filter(pl.col("variant") == "PROD").select(
        [
            "window",
            pl.col("mean_ret").alias("prod_mean_ret"),
            pl.col("med_ret").alias("prod_med_ret"),
            pl.col("worst_dd").alias("prod_worst_dd"),
            pl.col("mean_dd").alias("prod_mean_dd"),
        ]
    )
    delta = (
        df.filter(pl.col("variant") != "PROD")
        .join(prod, on="window")
        .with_columns(
            [
                (pl.col("mean_ret") - pl.col("prod_mean_ret")).alias("d_mean_ret"),
                (pl.col("med_ret") - pl.col("prod_med_ret")).alias("d_med_ret"),
                (pl.col("worst_dd") - pl.col("prod_worst_dd")).alias("d_worst_dd"),
                (pl.col("mean_dd") - pl.col("prod_mean_dd")).alias("d_mean_dd"),
            ]
        )
        .select(
            [
                "variant", "window", "d_mean_ret", "d_med_ret", "d_worst_dd", "d_mean_dd",
                "mean_ret", "med_ret", "worst_dd", "mean_dd",
                "prod_mean_ret", "prod_med_ret", "prod_worst_dd", "prod_mean_dd",
            ]
        )
    )
    delta_path = out.with_name(out.stem + "_deltas.csv")
    delta.write_csv(delta_path)
    print(f"\n[save] {out} rows={len(rows):,}", flush=True)
    print(f"[save] {delta_path}", flush=True)
    print(delta, flush=True)


if __name__ == "__main__":
    main()
