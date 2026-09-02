"""Optimize Market Wave from ETF score, MACD, breadth, and dispersion features.

Experiment-only. Reads a parquet produced by `build_predictive_wave_cache.py`
and searches a smooth 0-100 oscillator against 7d/15d SPY drawdown tails. The
optimizer is DB-free, so it can be rerun cheaply after the v55 cache exists.
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FEATURES = [
    "soft_ema50_breadth",
    "soft_ema200_breadth",
    "daily_up_breadth",
    "soft_ema50_breadth_d3",
    "soft_ema50_breadth_d5",
    "daily_up_breadth_d3",
    "sector_score_overall_mean",
    "sector_score_overall_breadth60",
    "sector_score_overall_mean_d3",
    "sector_score_overall_mean_d5",
    "sector_score_overall_dispersion",
    "sector_score_macd_mean",
    "sector_score_macd_breadth60",
    "sector_score_macd_mean_d3",
    "sector_score_macd_mean_d5",
    "sector_macdh_z_mean",
    "sector_macdh_z_breadth_pos",
    "sector_macdh_z_velocity3",
    "sector_macdh_z_accel3",
    "sector_macdh_z_dispersion",
]


@dataclass(frozen=True)
class Params:
    weights: tuple[float, ...]
    alpha_base: float
    alpha_stress: float
    alpha_repair: float
    stress_gate: float


def normalize_features(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, dict]:
    train = df[df["date"] < "2020-01-01"]
    z = pd.DataFrame(index=df.index)
    stats = {}
    for col in feature_cols:
        s = df[col].astype(float)
        t = train[col].astype(float).dropna()
        med = float(t.median()) if len(t) else float(s.median())
        q10 = float(t.quantile(0.10)) if len(t) else float(s.quantile(0.10))
        q90 = float(t.quantile(0.90)) if len(t) else float(s.quantile(0.90))
        scale = max((q90 - q10) / 2.0, 1e-9)
        z[col] = ((s - med) / scale).clip(-4, 4)
        stats[col] = {"median": med, "scale": scale}
    return z, stats


def build_score(df: pd.DataFrame, z: pd.DataFrame, params: Params, feature_cols: list[str]) -> pd.DataFrame:
    raw = sum(w * z[col] for w, col in zip(params.weights, feature_cols))
    train_raw = raw[df["date"] < "2020-01-01"].dropna()
    center = train_raw.median()
    scale = max((train_raw.quantile(0.90) - train_raw.quantile(0.10)) / 2.0, 1e-9)
    instant = (50.0 + 50.0 * np.tanh((raw - center) / scale)).clip(0.0, 100.0)

    driver = df.get("sector_score_overall_mean", instant).astype(float)
    driver_delta = driver.diff()
    state = []
    prev = np.nan
    for inst, d1 in zip(instant, driver_delta):
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

    out = df.copy()
    out["instant_score"] = instant
    out["score"] = np.clip(state, 0.0, 100.0)
    out["signed"] = 2.0 * (out["score"] - 50.0)
    out["raw_wave"] = raw
    out["center"] = center
    out["scale"] = scale
    return out


def event_score(work: pd.DataFrame, start: str, end: str, fn: str) -> float:
    w = work[(work["date"] >= start) & (work["date"] <= end)]
    if not len(w):
        return float("nan")
    if fn == "min":
        return float(w["score"].min())
    return float(w["score"].max())


def evaluate(df: pd.DataFrame, z: pd.DataFrame, params: Params, feature_cols: list[str], mode: str) -> dict:
    wave = build_score(df, z, params, feature_cols)
    work = wave.dropna(subset=["score", "mdd7", "mdd15", "mdd30", "ret7", "ret15"]).copy()
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
    p95_delta = float(dscore.quantile(0.95))
    max_delta = float(dscore.max())
    covid_min = event_score(work, "2020-02-21", "2020-03-12", "min")
    tariff_min = event_score(work, "2025-02-21", "2025-04-07", "min")
    tariff_repair_max = event_score(work, "2025-04-08", "2025-05-09", "max")

    sanity = (
        min(1.0, max(0.0, (38.0 - covid_min) / 25.0))
        + min(1.0, max(0.0, (42.0 - tariff_min) / 30.0))
        + min(1.0, max(0.0, (tariff_repair_max - 62.0) / 25.0))
    )
    smooth_penalty = max(0.0, p95_delta - 5.5) * 0.045 + max(0.0, max_delta - 24.0) * 0.018
    event_penalty = (
        max(0.0, covid_min - 45.0) * 0.020
        + max(0.0, tariff_min - 48.0) * 0.018
        + max(0.0, 58.0 - tariff_repair_max) * 0.030
    )
    objective = (
        1.10 * auc_test
        + 0.45 * auc_train
        + 0.35 * auc7_test
        + 0.060 * test_tail_sep
        + 0.030 * tail_sep
        + 0.95 * test_bad_sep
        + 0.35 * bad_sep
        + 0.22 * sanity
        - smooth_penalty
        - event_penalty
    )
    if mode == "shipshape":
        if tariff_repair_max < 58.0 or test_bad_sep < 0.12 or auc_test < 0.56:
            objective -= 10.0
        if p95_delta > 6.0 or max_delta > 28.0:
            objective -= 10.0

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
        "p95_delta": round(p95_delta, 4),
        "max_delta": round(max_delta, 4),
        "covid_min": round(covid_min, 2),
        "tariff_min": round(tariff_min, 2),
        "tariff_repair_max": round(tariff_repair_max, 2),
        "n_low": int(len(low)),
        "n_high": int(len(high)),
        "n_test_low": int(len(test_low)),
        "n_test_high": int(len(test_high)),
        "alpha_base": params.alpha_base,
        "alpha_stress": params.alpha_stress,
        "alpha_repair": params.alpha_repair,
        "stress_gate": params.stress_gate,
        **{f"w_{c}": w for c, w in zip(feature_cols, params.weights)},
    }


def random_params(rng: random.Random, n_features: int) -> Params:
    return Params(
        weights=tuple(rng.uniform(-0.85, 0.85) for _ in range(n_features)),
        alpha_base=rng.uniform(0.04, 0.18),
        alpha_stress=rng.uniform(0.18, 0.50),
        alpha_repair=rng.uniform(0.12, 0.45),
        stress_gate=rng.uniform(3.0, 14.0),
    )


def params_from_row(row: dict, feature_cols: list[str]) -> Params:
    return Params(
        weights=tuple(float(row[f"w_{c}"]) for c in feature_cols),
        alpha_base=float(row["alpha_base"]),
        alpha_stress=float(row["alpha_stress"]),
        alpha_repair=float(row["alpha_repair"]),
        stress_gate=float(row["stress_gate"]),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trials", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20260512)
    ap.add_argument("--constraint-mode", choices=["none", "shipshape"], default="shipshape")
    ap.add_argument("--feature-cols", default=",".join(DEFAULT_FEATURES))
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args()

    feature_cols = [c.strip() for c in args.feature_cols.split(",") if c.strip()]
    df = pd.read_parquet(args.cache)
    df["date"] = pd.to_datetime(df["date"])
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df[["date"]], "sector_wave/optimize_predictive_market_wave")
    z, feature_stats = normalize_features(df, feature_cols)
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "sector_wave" / "runs" / f"predictive_wave_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "launch.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "args": vars(args),
                "feature_cols": feature_cols,
                "feature_stats": feature_stats,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    rng = random.Random(args.seed)
    rows = []
    for i in range(args.trials):
        params = random_params(rng, len(feature_cols))
        row = evaluate(df, z, params, feature_cols, args.constraint_mode)
        row["name"] = f"rand_{i + 1:04d}"
        rows.append(row)
        if (i + 1) % 50 == 0:
            ranked = sorted(rows, key=lambda r: r["objective"], reverse=True)
            pd.DataFrame(ranked).to_csv(run_dir / "predictive_wave_ranked.partial.csv", index=False)
            status = {
                "phase": "search",
                "state": "running",
                "completed": i + 1,
                "total": args.trials,
                "best": ranked[0],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            (run_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
            print(f"[search] {i + 1}/{args.trials} best={ranked[0]['name']} obj={ranked[0]['objective']:.4f}", flush=True)

    ranked = sorted(rows, key=lambda r: r["objective"], reverse=True)
    pd.DataFrame(ranked).to_csv(run_dir / "predictive_wave_ranked.csv", index=False)
    best = ranked[0]
    best_df = build_score(df, z, params_from_row(best, feature_cols), feature_cols)
    keep = ["date", "SPY_close", "instant_score", "score", "signed", "raw_wave", "ret7", "ret15", "mdd7", "mdd15", "mdd30", *feature_cols]
    best_df[keep].to_csv(run_dir / "best_predictive_wave_history.csv", index=False)
    summary = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "best": best,
        "top": ranked[:25],
        "output_paths": {
            "ranked": str(run_dir / "predictive_wave_ranked.csv"),
            "history": str(run_dir / "best_predictive_wave_history.csv"),
        },
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "done.json").write_text(json.dumps({"finished_at": summary["finished_at"], "summary": str(run_dir / "summary.json")}, indent=2), encoding="utf-8")
    (run_dir / "status.json").write_text(json.dumps({"phase": "done", "state": "done", "completed": args.trials, "total": args.trials, "best": best}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
