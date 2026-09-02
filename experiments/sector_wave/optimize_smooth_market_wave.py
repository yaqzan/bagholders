"""Optimize a smoother sector ETF Market Wave.

Experiment-only. This searches smooth sector trend breadth formulas against
forward SPY drawdown tails while penalizing one-day score whiplash. It does not
touch production scoring or dashboard code.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


ROOT = Path(__file__).resolve().parents[2]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]


@dataclass(frozen=True)
class WaveParams:
    width: float
    level_w: float
    level200_w: float
    d1_w: float
    d3_w: float
    d5_w: float
    d15_w: float
    pos10_w: float
    pos30_w: float
    alpha_base: float
    alpha_stress: float
    alpha_repair: float
    stress_gate: float


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def sigmoid(x: pd.Series | np.ndarray) -> pd.Series | np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def load_features() -> pd.DataFrame:
    wide = pd.read_parquet(ROOT / ".cache" / "sector_etf_screen" / "etf_features_wide.parquet")
    wide["date"] = pd.to_datetime(wide["date"])

    dist50 = pd.concat([wide.get(f"{s}_pct_ema50", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    dist200 = pd.concat([wide.get(f"{s}_pct_ema200", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    dist50.columns = SECTORS
    dist200.columns = SECTORS

    close = pd.concat([wide.get(f"{s}_close", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    close.columns = SECTORS
    count = dist50.notna().sum(axis=1)

    out = wide[["date", "SPY_close"]].copy()
    out["count"] = count
    out["hard_breadth"] = (dist50.gt(0).sum(axis=1) / count * 100.0).where(count >= 9)
    out["hard_breadth200"] = (dist200.gt(0).sum(axis=1) / dist200.notna().sum(axis=1) * 100.0).where(count >= 9)

    prev_close = close.shift(1)
    known_up = close.notna() & prev_close.notna()
    up_count = known_up.sum(axis=1)
    out["daily_up_breadth"] = (((close > prev_close) & known_up).sum(axis=1) / up_count * 100.0).where(up_count >= 9)

    for n in [7, 15, 30, 60]:
        out[f"ret{n}"] = (out["SPY_close"].shift(-n) / out["SPY_close"] - 1.0) * 100.0
        future = pd.concat([out["SPY_close"].shift(-i) for i in range(1, n + 1)], axis=1)
        out[f"mdd{n}"] = (future.min(axis=1) / out["SPY_close"] - 1.0) * 100.0
        out[f"mup{n}"] = (future.max(axis=1) / out["SPY_close"] - 1.0) * 100.0

    out.attrs["dist50"] = dist50
    out.attrs["dist200"] = dist200
    return out


def rolling_position(s: pd.Series, n: int) -> pd.Series:
    lo = s.rolling(n, min_periods=min(n, max(1, n // 2))).min()
    hi = s.rolling(n, min_periods=min(n, max(1, n // 2))).max()
    return (s - lo) - (hi - s)


def build_score(base: pd.DataFrame, params: WaveParams) -> pd.DataFrame:
    dist50: pd.DataFrame = base.attrs["dist50"]
    dist200: pd.DataFrame = base.attrs["dist200"]
    df = base.copy()
    df.attrs.clear()

    soft50 = sigmoid(dist50 / params.width) * 100.0
    soft200 = sigmoid(dist200 / (params.width * 1.35)) * 100.0
    df["soft_breadth"] = soft50.mean(axis=1).where(df["count"] >= 9)
    df["soft_breadth200"] = soft200.mean(axis=1).where(df["count"] >= 9)

    b = df["soft_breadth"]
    for n in [1, 3, 5, 15]:
        df[f"d{n}"] = b - b.shift(n)
    df["pos10"] = rolling_position(b, 10)
    df["pos30"] = rolling_position(b, 30)

    raw = (
        params.level_w * ((df["soft_breadth"] - 50.0) / 50.0)
        + params.level200_w * ((df["soft_breadth200"] - 50.0) / 50.0)
        + params.d1_w * (df["d1"] / 100.0)
        + params.d3_w * (df["d3"] / 100.0)
        + params.d5_w * (df["d5"] / 100.0)
        + params.d15_w * (df["d15"] / 100.0)
        + params.pos10_w * (df["pos10"] / 100.0)
        + params.pos30_w * (df["pos30"] / 100.0)
    )
    train_raw = raw[df["date"] < "2020-01-01"].dropna()
    center = train_raw.median()
    scale = (train_raw.quantile(0.90) - train_raw.quantile(0.10)) / 2.0
    instant = 50.0 + 50.0 * np.tanh((raw - center) / max(scale, 1e-9))
    instant = instant.clip(0.0, 100.0)

    state = []
    prev = np.nan
    for inst, d1 in zip(instant, df["d1"]):
        if not np.isfinite(inst):
            state.append(np.nan)
            continue
        if not np.isfinite(prev):
            prev = float(inst)
        else:
            impulse_down = max(0.0, -float(d1 if np.isfinite(d1) else 0.0))
            impulse_up = max(0.0, float(d1 if np.isfinite(d1) else 0.0))
            if impulse_down >= params.stress_gate:
                alpha = params.alpha_stress
            elif impulse_up >= params.stress_gate:
                alpha = params.alpha_repair
            else:
                alpha = params.alpha_base
            prev = prev + alpha * (float(inst) - prev)
        state.append(prev)

    df["instant_score"] = instant
    df["score"] = np.clip(state, 0.0, 100.0)
    df["signed"] = 2.0 * (df["score"] - 50.0)
    df["center"] = center
    df["scale"] = scale
    return df


def evaluate(base: pd.DataFrame, params: WaveParams) -> dict:
    df = build_score(base, params)
    work = df.dropna(subset=["score", "ret7", "ret15", "mdd7", "mdd15", "mdd30"]).copy()
    work["bad7"] = (work["mdd7"] <= -3.0).astype(int)
    work["bad15"] = (work["mdd15"] <= -5.0).astype(int)
    train = work[work["date"] < "2020-01-01"]
    test = work[work["date"] >= "2020-01-01"]

    if train["bad15"].nunique() < 2 or test["bad15"].nunique() < 2:
        return {"objective": -999999.0}

    auc_train = roc_auc_score(train["bad15"], -train["score"])
    auc_test = roc_auc_score(test["bad15"], -test["score"])
    auc7_test = roc_auc_score(test["bad7"], -test["score"])

    low = work[work["score"] < 35.0]
    high = work[work["score"] >= 65.0]
    test_low = test[test["score"] < 35.0]
    test_high = test[test["score"] >= 65.0]
    if len(low) < 100 or len(high) < 100 or len(test_low) < 25 or len(test_high) < 25:
        return {"objective": -999999.0}

    tail_sep = high["mdd15"].quantile(0.10) - low["mdd15"].quantile(0.10)
    test_tail_sep = test_high["mdd15"].quantile(0.10) - test_low["mdd15"].quantile(0.10)
    bad_sep = low["bad15"].mean() - high["bad15"].mean()
    test_bad_sep = test_low["bad15"].mean() - test_high["bad15"].mean()
    dscore = work["score"].diff().abs()
    p95_delta = dscore.quantile(0.95)
    max_delta = dscore.max()
    median_delta = dscore.median()

    def nearest_score(ds: str) -> float:
        i = (work["date"] - pd.Timestamp(ds)).abs().idxmin()
        return float(work.loc[i, "score"])

    def window_min(start: str, end: str) -> float:
        w = work[(work["date"] >= start) & (work["date"] <= end)]
        return float(w["score"].min()) if len(w) else nearest_score(start)

    def window_max(start: str, end: str) -> float:
        w = work[(work["date"] >= start) & (work["date"] <= end)]
        return float(w["score"].max()) if len(w) else nearest_score(start)

    covid = nearest_score("2020-02-24")
    tariff = nearest_score("2025-03-04")
    repair = nearest_score("2025-04-30")
    covid_min = window_min("2020-02-21", "2020-03-12")
    tariff_min = window_min("2025-02-21", "2025-04-07")
    tariff_repair_max = window_max("2025-04-08", "2025-05-09")
    sanity = (
        min(1.0, max(0.0, (38.0 - covid_min) / 25.0))
        + min(1.0, max(0.0, (42.0 - tariff_min) / 30.0))
        + min(1.0, max(0.0, (tariff_repair_max - 62.0) / 25.0))
    )

    event_penalty = (
        max(0.0, covid_min - 45.0) * 0.010
        + max(0.0, tariff_min - 48.0) * 0.008
        + max(0.0, 58.0 - tariff_repair_max) * 0.010
    )
    smooth_penalty = max(0.0, p95_delta - 6.0) * 0.035 + max(0.0, max_delta - 24.0) * 0.014
    objective = (
        1.10 * auc_test
        + 0.45 * auc_train
        + 0.35 * auc7_test
        + 0.055 * test_tail_sep
        + 0.030 * tail_sep
        + 0.90 * test_bad_sep
        + 0.35 * bad_sep
        + 0.20 * sanity
        - smooth_penalty
        - event_penalty
    )

    return {
        "objective": round(float(objective), 6),
        "auc_train": round(float(auc_train), 4),
        "auc_test": round(float(auc_test), 4),
        "auc7_test": round(float(auc7_test), 4),
        "tail_sep": round(float(tail_sep), 4),
        "test_tail_sep": round(float(test_tail_sep), 4),
        "bad_sep": round(float(bad_sep), 4),
        "test_bad_sep": round(float(test_bad_sep), 4),
        "low_bad15": round(float(low["bad15"].mean()), 4),
        "high_bad15": round(float(high["bad15"].mean()), 4),
        "test_low_bad15": round(float(test_low["bad15"].mean()), 4),
        "test_high_bad15": round(float(test_high["bad15"].mean()), 4),
        "median_delta": round(float(median_delta), 4),
        "p95_delta": round(float(p95_delta), 4),
        "max_delta": round(float(max_delta), 4),
        "covid_score": round(covid, 2),
        "tariff_score": round(tariff, 2),
        "repair_score": round(repair, 2),
        "covid_min": round(covid_min, 2),
        "tariff_min": round(tariff_min, 2),
        "tariff_repair_max": round(tariff_repair_max, 2),
        "n_low": int(len(low)),
        "n_high": int(len(high)),
        "n_test_low": int(len(test_low)),
        "n_test_high": int(len(test_high)),
        **asdict(params),
    }


def apply_constraints(row: dict, mode: str) -> dict:
    if row.get("objective", -999999.0) <= -999000.0 or mode == "none":
        return row

    penalties = []
    if mode in {"balanced", "shipshape"}:
        penalties.extend(
            [
                max(0.0, row["covid_min"] - 45.0) * 0.040,
                max(0.0, row["tariff_min"] - 48.0) * 0.035,
                max(0.0, 58.0 - row["tariff_repair_max"]) * 0.035,
                max(0.0, row["p95_delta"] - 5.5) * 0.050,
                max(0.0, row["max_delta"] - 24.0) * 0.020,
            ]
        )
    if mode == "shipshape":
        if row["tariff_repair_max"] < 58.0 or row["test_bad_sep"] < 0.12 or row["auc_test"] < 0.56:
            penalties.append(10.0)
        if row["n_test_low"] < 100 or row["n_test_high"] < 100:
            penalties.append(10.0)

    adjusted = dict(row)
    adjusted["raw_objective"] = row["objective"]
    adjusted["constraint_penalty"] = round(float(sum(penalties)), 6)
    adjusted["objective"] = round(float(row["objective"] - sum(penalties)), 6)
    return adjusted


def random_params(rng: random.Random) -> WaveParams:
    return WaveParams(
        width=rng.uniform(1.5, 5.5),
        level_w=rng.uniform(0.25, 0.75),
        level200_w=rng.uniform(0.00, 0.35),
        d1_w=rng.uniform(0.00, 0.45),
        d3_w=rng.uniform(0.00, 0.75),
        d5_w=rng.uniform(0.10, 0.90),
        d15_w=rng.uniform(0.05, 0.70),
        pos10_w=rng.uniform(0.00, 0.70),
        pos30_w=rng.uniform(0.00, 0.70),
        alpha_base=rng.uniform(0.04, 0.18),
        alpha_stress=rng.uniform(0.20, 0.50),
        alpha_repair=rng.uniform(0.12, 0.45),
        stress_gate=rng.uniform(6.0, 22.0),
    )


def production_like_params() -> WaveParams:
    return WaveParams(
        width=1.4,
        level_w=0.40,
        level200_w=0.15,
        d1_w=1.00,
        d3_w=0.00,
        d5_w=0.55,
        d15_w=0.35,
        pos10_w=0.35,
        pos30_w=0.35,
        alpha_base=1.00,
        alpha_stress=1.00,
        alpha_repair=1.00,
        stress_gate=0.0,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=800)
    ap.add_argument("--seed", type=int, default=20260512)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--constraint-mode", choices=["none", "balanced", "shipshape"], default="balanced")
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "sector_wave" / "runs" / f"smooth_wave_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "launch.json").write_text(json.dumps({"started_at": datetime.now().isoformat(timespec="seconds"), "args": vars(args)}, indent=2), encoding="utf-8")

    base = load_features()
    rng = random.Random(args.seed)
    rows = []

    baseline = apply_constraints(evaluate(base, production_like_params()), args.constraint_mode)
    baseline["name"] = "production_like_unsmoothed"
    rows.append(baseline)

    for i in range(args.trials):
        row = apply_constraints(evaluate(base, random_params(rng)), args.constraint_mode)
        row["name"] = f"rand_{i + 1:04d}"
        rows.append(row)
        if (i + 1) % 50 == 0:
            ranked = sorted(rows, key=lambda r: r["objective"], reverse=True)
            pd.DataFrame(ranked).to_csv(run_dir / "smooth_wave_ranked.partial.csv", index=False)
            status = {
                "phase": "search",
                "state": "running",
                "completed": i + 1,
                "total": args.trials,
                "best": ranked[0],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            (run_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
            print(f"[search] {i + 1}/{args.trials} best={ranked[0]['name']} obj={ranked[0]['objective']:.4f} auc_test={ranked[0].get('auc_test')}", flush=True)

    ranked = sorted(rows, key=lambda r: r["objective"], reverse=True)
    pd.DataFrame(ranked).to_csv(run_dir / "smooth_wave_ranked.csv", index=False)
    best = ranked[0]
    best_params = WaveParams(**{k: best[k] for k in WaveParams.__dataclass_fields__})
    best_df = build_score(base, best_params)
    keep_cols = [
        "date", "count", "hard_breadth", "daily_up_breadth", "soft_breadth",
        "soft_breadth200", "instant_score", "score", "signed", "d1", "d3",
        "d5", "d15", "pos10", "pos30", "ret7", "ret15", "ret30", "mdd7",
        "mdd15", "mdd30", "mup15",
    ]
    best_df[keep_cols].to_csv(run_dir / "best_wave_history.csv", index=False)
    summary = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "baseline": baseline,
        "best": best,
        "top": ranked[:25],
        "output_paths": {
            "ranked": str(run_dir / "smooth_wave_ranked.csv"),
            "history": str(run_dir / "best_wave_history.csv"),
        },
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "done.json").write_text(json.dumps({"finished_at": summary["finished_at"], "summary": str(run_dir / "summary.json")}, indent=2), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({"phase": "done", "state": "done", "completed": args.trials, "total": args.trials, "best": best}, indent=2), encoding="utf-8")
    print(f"[done] {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
