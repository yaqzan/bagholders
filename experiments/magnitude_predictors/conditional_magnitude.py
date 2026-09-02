"""Conditional winner/near-miss magnitude screen.

The first magnitude pass mixed probability and distance. This pass separates:

1. Winner headroom:
   Among trades that already hit the generic stock TP, which features predict
   excess MFE beyond the TP barrier?

2. Loser near-misses:
   Among trades that did not hit TP first, which features frequently travelled
   most of the way to TP before failing/expiring?

Both questions are evaluated inside side + score-band cohorts so the screen
does not simply rediscover the existing probability ladder.
"""
from __future__ import annotations

import io
import math
import sys
import warnings
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import polars as pl

from experiments._holdout import assert_no_holdout_leak

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

OUT_DIR = ROOT / ".cache" / "magnitude_predictors"
LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
TARGET_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 15
MIN_SIDE_N = 250
MIN_BAND_N = 60
MIN_TAIL_N = 25

FEATURES = [
    "score_strength",
    "bb_edge",
    "trend_edge",
    "volume_edge",
    "rsi_edge",
    "macd_edge",
    "stoch_edge",
    "ta_edge",
    "component_spread",
    "volume_magnitude",
    "abs_volume_magnitude",
    "pct_from_ema50_side",
    "abs_pct_from_ema50",
    "pct_from_ema200_side",
    "bb_position_side",
    "macd_hist_side",
    "stoch_gap_side",
    "score_velocity_7d_side",
    "mcap_log10",
    "regime_composite",
    "regime_multiplier",
    "breadth_score",
    "mcclellan_osc",
    "pct_above_ema50_mkt",
    "pct_above_ema200_mkt",
    "vix_close",
    "vix_10d_change",
    "vix_score",
    "market_trend_score",
    "wi_pre_regime_strength",
    "wi_pre_boost_strength",
    "wi_w_comp_edge",
    "wi_w_bias_side",
    "wi_w_mom_side",
    "wi_w_adj_side",
    "wi_td",
    "wi_mcd_dampen",
    "wi_ern_boost",
    "days_to_earnings_abs",
    "wi_mis_stress",
    "wi_cswc_dampen",
    "wi_scw_dampen",
]


def _load_signal_path() -> Path:
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    path = OUT_DIR / f"signals_v{av.id}_{LOOKBACK_DAYS}.parquet"
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Build it first:\n"
            f"  python experiments/magnitude_predictors/build_features.py {LOOKBACK_DAYS}"
        )
    return path


def _score_band(side: str, overall: float) -> str:
    ov = int(overall)
    if side == "call":
        if ov >= 95:
            return "95+"
        if ov >= 90:
            return "90-94"
        if ov >= 85:
            return "85-89"
        if ov >= 80:
            return "80-84"
        if ov >= 75:
            return "75-79"
        return "70-74"
    if ov <= 5:
        return "0-5"
    if ov <= 10:
        return "6-10"
    if ov <= 15:
        return "11-15"
    if ov <= 20:
        return "16-20"
    return "21-25"


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    text_cols = {"symbol", "date", "side", "sector", "industry", "barrier_side"}
    for col in df.columns:
        if col not in text_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["is_call"] = df["side"].eq("call")
    side_sign = np.where(df["is_call"], 1.0, -1.0)
    df["score_band"] = [_score_band(s, o) for s, o in zip(df["side"], df["overall"])]
    df["score_strength"] = np.where(df["is_call"], df["overall"] - 50, 50 - df["overall"])

    for src, dst in [
        ("bb_score", "bb_edge"),
        ("trend_score", "trend_edge"),
        ("volume_score", "volume_edge"),
        ("rsi_score", "rsi_edge"),
        ("macd_score", "macd_edge"),
        ("stoch_score", "stoch_edge"),
        ("ta_score", "ta_edge"),
    ]:
        df[dst] = np.where(df["is_call"], df[src], 100 - df[src])

    component_cols = ["bb_edge", "trend_edge", "volume_edge", "rsi_edge", "macd_edge", "stoch_edge", "ta_edge"]
    df["component_spread"] = df[component_cols].max(axis=1) - df[component_cols].min(axis=1)
    df["abs_volume_magnitude"] = df["volume_magnitude"].abs()
    df["pct_from_ema50_side"] = df["pct_from_ema50"] * side_sign
    df["abs_pct_from_ema50"] = df["pct_from_ema50"].abs()
    df["pct_from_ema200_side"] = df["pct_from_ema200"] * side_sign
    df["bb_position_side"] = np.where(df["is_call"], df["bb_position"], 1.0 - df["bb_position"])
    df["macd_hist_side"] = df["macd_hist"] * side_sign
    df["stoch_gap_side"] = (df["stoch_raw"] - df["stoch_signal"]) * side_sign
    df["score_velocity_7d_side"] = df["score_velocity_7d"] * side_sign
    df["mcap_log10"] = np.log10(df["mcap_b"].clip(lower=0.01))
    df["wi_pre_regime_strength"] = np.where(df["is_call"], df["wi_pre_regime"] - 50, 50 - df["wi_pre_regime"])
    df["wi_pre_boost_strength"] = np.where(df["is_call"], df["wi_pre_boost"] - 50, 50 - df["wi_pre_boost"])
    df["wi_w_comp_edge"] = np.where(df["is_call"], df["wi_w_comp"], 100 - df["wi_w_comp"])
    df["wi_w_bias_side"] = df["wi_w_bias"] * side_sign
    df["wi_w_mom_side"] = df["wi_w_mom"] * side_sign
    df["wi_w_adj_side"] = df["wi_w_adj"] * side_sign
    df["days_to_earnings_abs"] = df["wi_days_to_ern"].abs()

    mfe = f"mfe_sigma_{TARGET_DAYS}"
    mae = f"mae_sigma_{TARGET_DAYS}"
    result = f"result_{TARGET_DAYS}"
    ret = f"exit_return_sigma_{TARGET_DAYS}"
    df = df[np.isfinite(df[mfe]) & np.isfinite(df[result])].copy()

    scale = math.sqrt(TARGET_DAYS / 30.0)
    df["target_sigma"] = np.where(df["is_call"], 2.0 * scale, 1.0 * scale)
    df["mfe_ratio_to_target"] = df[mfe] / df["target_sigma"]
    df["winner_excess_mfe_sigma"] = df[mfe] - df["target_sigma"]
    df["winner_reach_110"] = (df["mfe_ratio_to_target"] >= 1.10).astype(float)
    df["winner_reach_120"] = (df["mfe_ratio_to_target"] >= 1.20).astype(float)
    df["winner_reach_130"] = (df["mfe_ratio_to_target"] >= 1.30).astype(float)
    df["loser_near_50"] = (df["mfe_ratio_to_target"] >= 0.50).astype(float)
    df["loser_near_70"] = (df["mfe_ratio_to_target"] >= 0.70).astype(float)
    df["loser_near_85"] = (df["mfe_ratio_to_target"] >= 0.85).astype(float)
    df["is_winner"] = df[result].eq(1)
    df["is_loser"] = df[result].eq(0)
    df["mae_sigma_abs"] = df[mae].abs()
    df["exit_sigma"] = df[ret]
    return df


def _delta_z(hi: pd.Series, lo: pd.Series) -> tuple[float, float]:
    delta = float(hi.mean() - lo.mean())
    se = math.sqrt(hi.var(ddof=1) / len(hi) + lo.var(ddof=1) / len(lo))
    z = delta / se if se > 0 else float("nan")
    return delta, z


def _screen_subset(df: pd.DataFrame, side: str, feature: str, subset: str) -> dict[str, float | str] | None:
    sub = df[df["side"].eq(side) & np.isfinite(df[feature])]
    sub = sub[sub["is_winner"]] if subset == "winner" else sub[sub["is_loser"]]
    if len(sub) < MIN_SIDE_N:
        return None

    tails = []
    for _, band in sub.groupby("score_band", observed=True):
        if len(band) < MIN_BAND_N:
            continue
        q33 = band[feature].quantile(1 / 3)
        q67 = band[feature].quantile(2 / 3)
        if not math.isfinite(q33) or not math.isfinite(q67) or q33 == q67:
            continue
        low = band[band[feature] <= q33].assign(tail="low")
        high = band[band[feature] >= q67].assign(tail="high")
        if len(low) >= MIN_TAIL_N and len(high) >= MIN_TAIL_N:
            tails.extend([low, high])
    if not tails:
        return None

    t = pd.concat(tails, ignore_index=True)
    lo = t[t["tail"].eq("low")]
    hi = t[t["tail"].eq("high")]
    if len(lo) < MIN_SIDE_N // 4 or len(hi) < MIN_SIDE_N // 4:
        return None

    if subset == "winner":
        primary = "winner_excess_mfe_sigma"
        delta, z = _delta_z(hi[primary], lo[primary])
        row = {
            "subset": subset,
            "side": side,
            "feature": feature,
            "n_low": len(lo),
            "n_high": len(hi),
            "feature_low_med": lo[feature].median(),
            "feature_high_med": hi[feature].median(),
            "delta_excess_mfe": delta,
            "z": z,
            "low_excess_mfe": lo[primary].mean(),
            "high_excess_mfe": hi[primary].mean(),
            "delta_reach_110_pp": (hi["winner_reach_110"].mean() - lo["winner_reach_110"].mean()) * 100.0,
            "delta_reach_120_pp": (hi["winner_reach_120"].mean() - lo["winner_reach_120"].mean()) * 100.0,
            "delta_reach_130_pp": (hi["winner_reach_130"].mean() - lo["winner_reach_130"].mean()) * 100.0,
            "delta_mae_sigma": hi["mae_sigma_abs"].mean() - lo["mae_sigma_abs"].mean(),
        }
    else:
        primary = "mfe_ratio_to_target"
        delta, z = _delta_z(hi[primary], lo[primary])
        row = {
            "subset": subset,
            "side": side,
            "feature": feature,
            "n_low": len(lo),
            "n_high": len(hi),
            "feature_low_med": lo[feature].median(),
            "feature_high_med": hi[feature].median(),
            "delta_loser_mfe_ratio": delta,
            "z": z,
            "low_loser_mfe_ratio": lo[primary].mean(),
            "high_loser_mfe_ratio": hi[primary].mean(),
            "delta_near_50_pp": (hi["loser_near_50"].mean() - lo["loser_near_50"].mean()) * 100.0,
            "delta_near_70_pp": (hi["loser_near_70"].mean() - lo["loser_near_70"].mean()) * 100.0,
            "delta_near_85_pp": (hi["loser_near_85"].mean() - lo["loser_near_85"].mean()) * 100.0,
            "delta_mae_sigma": hi["mae_sigma_abs"].mean() - lo["mae_sigma_abs"].mean(),
        }
    return row


def _screen(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for subset in ("winner", "loser"):
        for side in ("call", "put"):
            for feature in FEATURES:
                if feature not in df.columns:
                    continue
                row = _screen_subset(df, side, feature, subset)
                if row is not None:
                    rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    primary_abs = np.where(
        out["subset"].eq("winner"),
        out["delta_excess_mfe"].abs(),
        out["delta_loser_mfe_ratio"].abs(),
    )
    return out.iloc[np.argsort(-primary_abs)].reset_index(drop=True)


def _baseline(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (side, band), sub in df.groupby(["side", "score_band"], observed=True):
        winners = sub[sub["is_winner"]]
        losers = sub[sub["is_loser"]]
        rows.append(
            {
                "side": side,
                "score_band": band,
                "n": len(sub),
                "win_rate": sub["is_winner"].mean() * 100.0,
                "winner_n": len(winners),
                "winner_excess_mfe_avg": winners["winner_excess_mfe_sigma"].mean(),
                "winner_reach_120": winners["winner_reach_120"].mean() * 100.0,
                "loser_n": len(losers),
                "loser_mfe_ratio_avg": losers["mfe_ratio_to_target"].mean(),
                "loser_near_70": losers["loser_near_70"].mean() * 100.0,
                "loser_near_85": losers["loser_near_85"].mean() * 100.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["side", "score_band"])


def _markdown_table(df: pd.DataFrame, cols: list[str] | None = None, limit: int | None = None) -> str:
    if cols is not None:
        df = df[cols]
    if limit is not None:
        df = df.head(limit)
    if df.empty:
        return "_No rows._"

    def fmt(v: object) -> str:
        if pd.isna(v):
            return ""
        if isinstance(v, (float, np.floating)):
            return f"{float(v):.3f}"
        return str(v)

    headers = list(df.columns)
    rows = [[fmt(v) for v in row] for row in df.itertuples(index=False, name=None)]
    widths = [max(len(str(headers[i])), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    header = "| " + " | ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))) + " |"
    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    body = ["| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |" for row in rows]
    return "\n".join([header, sep, *body])


def main() -> None:
    path = _load_signal_path()
    print(f"[conditional] reading {path}", flush=True)
    pl_df = pl.read_parquet(path)
    assert_no_holdout_leak(pl_df, "magnitude_predictors/conditional_magnitude")
    df = _prepare(pl_df.to_pandas())
    print(f"[conditional] rows with W{TARGET_DAYS} outcome: {len(df):,}", flush=True)

    baseline = _baseline(df)
    screen = _screen(df)

    baseline_path = OUT_DIR / f"conditional_baseline_w{TARGET_DAYS}_{LOOKBACK_DAYS}.csv"
    screen_path = OUT_DIR / f"conditional_screen_w{TARGET_DAYS}_{LOOKBACK_DAYS}.csv"
    findings_path = OUT_DIR / f"conditional_findings_w{TARGET_DAYS}_{LOOKBACK_DAYS}.md"
    baseline.to_csv(baseline_path, index=False)
    screen.to_csv(screen_path, index=False)

    winner_cols = [
        "subset", "side", "feature", "n_low", "n_high", "delta_excess_mfe", "z",
        "delta_reach_120_pp", "delta_reach_130_pp", "delta_mae_sigma",
    ]
    loser_cols = [
        "subset", "side", "feature", "n_low", "n_high", "delta_loser_mfe_ratio", "z",
        "delta_near_70_pp", "delta_near_85_pp", "delta_mae_sigma",
    ]
    winners = screen[screen["subset"].eq("winner")].copy()
    winners = winners.reindex(winners["delta_excess_mfe"].abs().sort_values(ascending=False).index)
    losers = screen[screen["subset"].eq("loser")].copy()
    losers = losers.reindex(losers["delta_loser_mfe_ratio"].abs().sort_values(ascending=False).index)

    lines = [
        f"# Conditional Magnitude Screen W{TARGET_DAYS}",
        "",
        f"Rows analyzed: {len(df):,}",
        "",
        "## Baseline",
        "",
        _markdown_table(baseline),
        "",
        "## Winner Headroom",
        "",
        _markdown_table(winners, cols=winner_cols, limit=20),
        "",
        "## Loser Near-Misses",
        "",
        _markdown_table(losers, cols=loser_cols, limit=20),
        "",
    ]
    findings_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"[conditional] wrote {baseline_path}", flush=True)
    print(f"[conditional] wrote {screen_path}", flush=True)
    print(f"[conditional] wrote {findings_path}", flush=True)
    print("[conditional] top winner headroom:", flush=True)
    print(winners[winner_cols].head(10).to_string(index=False), flush=True)
    print("[conditional] top loser near-misses:", flush=True)
    print(losers[loser_cols].head(10).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
