"""Screen residual stock-magnitude predictors.

Reads the parquet built by build_features.py and asks:
  Within fixed side + score band cohorts, which features still separate
  sigma-normalized favorable move (MFE)?

This keeps the experiment orthogonal to the existing score-probability ladder:
the target is residual MFE after removing side/band means.
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

OUT_DIR = ROOT / ".cache" / "magnitude_predictors"
LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
TARGET_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 15
MIN_SIDE_N = 400
MIN_BAND_N = 80
MIN_TAIL_N = 30
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

BASE_FEATURES = [
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


def _load_active_parquet() -> Path:
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    path = OUT_DIR / f"signals_v{av.id}_{LOOKBACK_DAYS}.parquet"
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Build it first:\n"
            f"  python experiments/magnitude_predictors/build_features.py {LOOKBACK_DAYS}"
        )
    return path


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

    target = f"mfe_sigma_{TARGET_DAYS}"
    df = df[np.isfinite(df[target])].copy()
    band_mean = df.groupby(["side", "score_band"], observed=True)[target].transform("mean")
    df[f"resid_{target}"] = df[target] - band_mean
    return df


def _baseline(df: pd.DataFrame) -> pd.DataFrame:
    target = f"mfe_sigma_{TARGET_DAYS}"
    result = f"result_{TARGET_DAYS}"
    mae = f"mae_sigma_{TARGET_DAYS}"
    ret = f"exit_return_sigma_{TARGET_DAYS}"
    g = df.groupby(["side", "score_band"], observed=True)
    out = g.agg(
        n=(target, "size"),
        win_rate=(result, "mean"),
        mfe_p25=(target, lambda x: float(np.nanpercentile(x, 25))),
        mfe_med=(target, "median"),
        mfe_avg=(target, "mean"),
        mfe_p75=(target, lambda x: float(np.nanpercentile(x, 75))),
        mae_med=(mae, "median"),
        exit_ret_avg=(ret, "mean"),
    ).reset_index()
    out["win_rate"] *= 100.0
    return out.sort_values(["side", "score_band"])


def _screen_one(df: pd.DataFrame, side: str, feature: str) -> dict[str, float | str] | None:
    target = f"mfe_sigma_{TARGET_DAYS}"
    resid = f"resid_{target}"
    result = f"result_{TARGET_DAYS}"
    sub = df[df["side"].eq(side) & np.isfinite(df[feature]) & np.isfinite(df[resid])]
    if len(sub) < MIN_SIDE_N:
        return None

    frames = []
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
            frames.extend([low, high])
    if not frames:
        return None

    tails = pd.concat(frames, ignore_index=True)
    lo = tails[tails["tail"].eq("low")]
    hi = tails[tails["tail"].eq("high")]
    if len(lo) < MIN_SIDE_N // 4 or len(hi) < MIN_SIDE_N // 4:
        return None

    delta = hi[resid].mean() - lo[resid].mean()
    se = math.sqrt(hi[resid].var(ddof=1) / len(hi) + lo[resid].var(ddof=1) / len(lo))
    z = delta / se if se > 0 else np.nan
    wr_delta = (hi[result].mean() - lo[result].mean()) * 100.0
    actual_delta = hi[target].mean() - lo[target].mean()

    return {
        "side": side,
        "feature": feature,
        "n_low": len(lo),
        "n_high": len(hi),
        "feature_low_med": lo[feature].median(),
        "feature_high_med": hi[feature].median(),
        "resid_mfe_delta": delta,
        "actual_mfe_delta": actual_delta,
        "wr_delta_pp": wr_delta,
        "z": z,
        "low_mfe_p25": float(np.nanpercentile(lo[target], 25)),
        "high_mfe_p25": float(np.nanpercentile(hi[target], 25)),
        "low_mfe_med": lo[target].median(),
        "high_mfe_med": hi[target].median(),
    }


def _screen(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for side in ("call", "put"):
        for feature in BASE_FEATURES:
            if feature not in df.columns:
                continue
            row = _screen_one(df, side, feature)
            if row is not None:
                rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["resid_mfe_delta"].abs().sort_values(ascending=False).index)


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
    widths = [
        max(len(str(headers[i])), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    header = "| " + " | ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))) + " |"
    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    body = [
        "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |"
        for row in rows
    ]
    return "\n".join([header, sep, *body])


def _write_findings(df: pd.DataFrame, baseline: pd.DataFrame, screen: pd.DataFrame, out_md: Path) -> None:
    target = f"mfe_sigma_{TARGET_DAYS}"
    lines = [
        f"# Magnitude Predictor Screen W{TARGET_DAYS}",
        "",
        f"Rows analyzed: {len(df):,}",
        f"Target: `{target}` residualized by `(side, score_band)`.",
        "",
        "## Baseline By Score Band",
        "",
        _markdown_table(baseline),
        "",
        "## Strongest Residual Separators",
        "",
    ]
    if screen.empty:
        lines.append("No feature cleared the minimum-N screen.")
    else:
        cols = [
            "side",
            "feature",
            "n_low",
            "n_high",
            "resid_mfe_delta",
            "wr_delta_pp",
            "z",
            "low_mfe_p25",
            "high_mfe_p25",
            "low_mfe_med",
            "high_mfe_med",
        ]
        lines.append(_markdown_table(screen, cols=cols, limit=20))
        lines.extend(
            [
                "",
                "Interpretation notes:",
                "- Positive `resid_mfe_delta` means the high-feature tercile reached farther favorable moves than the low-feature tercile after removing score-band means.",
                "- `wr_delta_pp` is included to catch probability leakage; a large MFE delta with small WR delta is the cleaner magnitude-only candidate.",
                "- `*_mfe_p25` is the conservative TP anchor: roughly 75% of that tail reached at least this many sigma.",
            ]
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    path = _load_active_parquet()
    print(f"[screen] reading {path}", flush=True)
    pl_df = pl.read_parquet(path)
    assert_no_holdout_leak(pl_df, "magnitude_predictors/screen_magnitude")
    df = _prepare(pl_df.to_pandas())
    print(f"[screen] rows with W{TARGET_DAYS} MFE sigma: {len(df):,}", flush=True)

    baseline = _baseline(df)
    screen = _screen(df)

    baseline_path = OUT_DIR / f"baseline_w{TARGET_DAYS}_{LOOKBACK_DAYS}.csv"
    screen_path = OUT_DIR / f"screen_w{TARGET_DAYS}_{LOOKBACK_DAYS}.csv"
    findings_path = OUT_DIR / f"findings_w{TARGET_DAYS}_{LOOKBACK_DAYS}.md"
    baseline.to_csv(baseline_path, index=False)
    screen.to_csv(screen_path, index=False)
    _write_findings(df, baseline, screen, findings_path)

    print(f"[screen] wrote {baseline_path}", flush=True)
    print(f"[screen] wrote {screen_path}", flush=True)
    print(f"[screen] wrote {findings_path}", flush=True)
    if not screen.empty:
        print("[screen] top residual separators:", flush=True)
        cols = ["side", "feature", "resid_mfe_delta", "wr_delta_pp", "z", "n_low", "n_high"]
        print(screen.head(12)[cols].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
