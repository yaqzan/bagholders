"""Build a v55-ready predictive Market Wave feature cache.

Experiment-only. This materializes sector ETF score/component and raw indicator
features into one parquet so wave optimization can run without repeated DB
queries. Run this after the target score version recalculation finishes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
OUT_DIR = ROOT / ".cache" / "market_wave"


def sigmoid(x: pd.Series | pd.DataFrame | np.ndarray) -> pd.Series | pd.DataFrame | np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def rolling_position(s: pd.Series, n: int) -> pd.Series:
    lo = s.rolling(n, min_periods=min(n, max(1, n // 2))).min()
    hi = s.rolling(n, min_periods=min(n, max(1, n // 2))).max()
    return (s - lo) - (hi - s)


def wide_from_rows(rows: list[dict], value_cols: list[str], prefix: str, symbols: list[str] | None = None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date"])
    symbols = symbols or SECTORS
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    pieces = []
    for col in value_cols:
        piv = df.pivot_table(index="date", columns="symbol", values=col, aggfunc="last")
        piv = piv.reindex(columns=symbols)
        piv = piv.apply(pd.to_numeric, errors="coerce")
        piv.columns = [f"{s}_{prefix}_{col}" for s in piv.columns]
        pieces.append(piv)
    out = pd.concat(pieces, axis=1).reset_index()
    return out


def load_score_rows(version_id: int, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    from database.models.core import Score

    q = (
        Score.select(Score.date, Score.symbol, Score.overall, Score.macd, Score.trend, Score.rsi)
        .where((Score.version == version_id) & (Score.symbol.in_(SECTORS)))
        .order_by(Score.date, Score.symbol)
    )
    if start_date:
        q = q.where(Score.date >= start_date)
    if end_date:
        q = q.where(Score.date <= end_date)
    return wide_from_rows(list(q.dicts()), ["overall", "macd", "trend", "rsi"], "score")


def load_raw_feature_rows(start_date: str | None, end_date: str | None) -> pd.DataFrame:
    from database.trader_database import DB

    symbols = ["SPY", *SECTORS]
    placeholders = ", ".join(["%s"] * len(symbols))
    clauses = [f"i.symbol IN ({placeholders})"]
    params: list[object] = list(symbols)
    if start_date:
        clauses.append("i.date >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("i.date <= %s")
        params.append(end_date)
    where = " AND ".join(clauses)
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    cur = DB.execute_sql(
        f"""
        SELECT i.symbol, i.date, ph.close, i.ema_50, i.ema_200, i.rsi, i.macd, i.macd_hist
        FROM indicators i
        JOIN price_history ph ON ph.symbol = i.symbol AND ph.date = i.date
        WHERE {where}
        ORDER BY i.symbol, i.date
        """,
        params,
    )
    rows = []
    for sym, d, close, ema50, ema200, rsi, macd, macd_hist in cur.fetchall():
        close_f = float(close) if close is not None else np.nan
        ema50_f = float(ema50) if ema50 is not None else np.nan
        ema200_f = float(ema200) if ema200 is not None else np.nan
        rows.append(
            {
                "symbol": sym,
                "date": d,
                "close": close_f,
                "pct_ema50": ((close_f - ema50_f) / ema50_f * 100.0) if np.isfinite(close_f) and np.isfinite(ema50_f) and ema50_f else np.nan,
                "pct_ema200": ((close_f - ema200_f) / ema200_f * 100.0) if np.isfinite(close_f) and np.isfinite(ema200_f) and ema200_f else np.nan,
                "rsi": float(rsi) if rsi is not None else np.nan,
                "macd": float(macd) if macd is not None else np.nan,
                "macd_hist": float(macd_hist) if macd_hist is not None else np.nan,
            }
        )
    wide = wide_from_rows(rows, ["close", "pct_ema50", "pct_ema200", "rsi", "macd", "macd_hist"], "", symbols)
    wide.columns = [c.replace("__", "_").rstrip("_") for c in wide.columns]
    return wide


def load_indicator_rows(start_date: str | None, end_date: str | None) -> pd.DataFrame:
    from database.models.technical import Indicator

    q = (
        Indicator.select(Indicator.date, Indicator.symbol, Indicator.macd_hist, Indicator.macd, Indicator.rsi)
        .where(Indicator.symbol.in_(SECTORS))
        .order_by(Indicator.date, Indicator.symbol)
    )
    if start_date:
        q = q.where(Indicator.date >= start_date)
    if end_date:
        q = q.where(Indicator.date <= end_date)
    return wide_from_rows(list(q.dicts()), ["macd_hist", "macd", "rsi"], "ind")


def row_count(frame: pd.DataFrame, cols: list[str]) -> pd.Series:
    return frame[cols].notna().sum(axis=1)


def add_breadth_features(out: pd.DataFrame, wide: pd.DataFrame) -> pd.DataFrame:
    dist50 = pd.concat([wide.get(f"{s}_pct_ema50", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    dist200 = pd.concat([wide.get(f"{s}_pct_ema200", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    close = pd.concat([wide.get(f"{s}_close", pd.Series(np.nan, index=wide.index)) for s in SECTORS], axis=1)
    dist50.columns = dist200.columns = close.columns = SECTORS

    count = dist50.notna().sum(axis=1)
    out["sector_count"] = count
    out["soft_ema50_breadth"] = (sigmoid(dist50 / 3.5) * 100.0).mean(axis=1).where(count >= 9)
    out["soft_ema200_breadth"] = (sigmoid(dist200 / 4.5) * 100.0).mean(axis=1).where(count >= 9)
    known_up = close.notna() & close.shift(1).notna()
    up_count = known_up.sum(axis=1)
    out["daily_up_breadth"] = (((close > close.shift(1)) & known_up).sum(axis=1) / up_count * 100.0).where(up_count >= 9)
    for col in ["soft_ema50_breadth", "daily_up_breadth"]:
        for n in [1, 3, 5, 15]:
            out[f"{col}_d{n}"] = out[col] - out[col].shift(n)
        out[f"{col}_pos10"] = rolling_position(out[col], 10)
        out[f"{col}_pos30"] = rolling_position(out[col], 30)
    return out


def add_panel_aggregates(out: pd.DataFrame, prefix: str, metric: str, cols: list[str]) -> pd.DataFrame:
    panel = out[cols]
    count = row_count(out, cols)
    base = f"sector_{prefix}_{metric}"
    out[f"{base}_mean"] = panel.mean(axis=1).where(count >= 9)
    out[f"{base}_breadth50"] = (panel.gt(50).sum(axis=1) / count * 100.0).where(count >= 9)
    out[f"{base}_breadth60"] = (panel.ge(60).sum(axis=1) / count * 100.0).where(count >= 9)
    out[f"{base}_breadth65"] = (panel.ge(65).sum(axis=1) / count * 100.0).where(count >= 9)
    out[f"{base}_dispersion"] = panel.std(axis=1).where(count >= 9)
    out[f"{base}_spread"] = (panel.max(axis=1) - panel.min(axis=1)).where(count >= 9)
    for n in [1, 3, 5, 15]:
        out[f"{base}_mean_d{n}"] = out[f"{base}_mean"] - out[f"{base}_mean"].shift(n)
    out[f"{base}_pos10"] = rolling_position(out[f"{base}_mean"], 10)
    out[f"{base}_pos30"] = rolling_position(out[f"{base}_mean"], 30)
    return out


def add_macdh_features(out: pd.DataFrame) -> pd.DataFrame:
    cols = [f"{s}_ind_macd_hist" for s in SECTORS if f"{s}_ind_macd_hist" in out.columns]
    if not cols:
        return out
    panel = out[cols].copy()
    z = panel.copy()
    for col in cols:
        med = panel[col].rolling(252, min_periods=80).median()
        q90 = panel[col].rolling(252, min_periods=80).quantile(0.90)
        q10 = panel[col].rolling(252, min_periods=80).quantile(0.10)
        scale = ((q90 - q10) / 2.0).replace(0, np.nan)
        z[col] = ((panel[col] - med) / scale).clip(-4, 4)
    count = z.notna().sum(axis=1)
    out["sector_macdh_z_mean"] = z.mean(axis=1).where(count >= 9)
    out["sector_macdh_z_breadth_pos"] = (z.gt(0).sum(axis=1) / count * 100.0).where(count >= 9)
    out["sector_macdh_z_dispersion"] = z.std(axis=1).where(count >= 9)
    for n in [1, 3, 5, 15]:
        out[f"sector_macdh_z_mean_d{n}"] = out["sector_macdh_z_mean"] - out["sector_macdh_z_mean"].shift(n)
    out["sector_macdh_z_velocity3"] = out["sector_macdh_z_mean"].diff(3)
    out["sector_macdh_z_accel3"] = out["sector_macdh_z_velocity3"].diff(3)
    return out


def add_forward_targets(out: pd.DataFrame) -> pd.DataFrame:
    for n in [7, 15, 30, 60]:
        out[f"ret{n}"] = (out["SPY_close"].shift(-n) / out["SPY_close"] - 1.0) * 100.0
        future = pd.concat([out["SPY_close"].shift(-i) for i in range(1, n + 1)], axis=1)
        out[f"mdd{n}"] = (future.min(axis=1) / out["SPY_close"] - 1.0) * 100.0
        out[f"mup{n}"] = (future.max(axis=1) / out["SPY_close"] - 1.0) * 100.0
    return out


def build_cache(version_id: int, start_date: str | None, end_date: str | None, include_holdout: bool) -> pd.DataFrame:
    raw = load_raw_feature_rows(start_date, end_date)
    raw["date"] = pd.to_datetime(raw["date"])

    score_wide = load_score_rows(version_id, start_date, end_date)
    indicator_wide = load_indicator_rows(start_date, end_date)
    out = raw[["date", "SPY_close"]].copy()
    out = add_breadth_features(out, raw)
    out = out.merge(score_wide, on="date", how="left").merge(indicator_wide, on="date", how="left")

    for metric in ["overall", "macd", "trend", "rsi"]:
        cols = [f"{s}_score_{metric}" for s in SECTORS if f"{s}_score_{metric}" in out.columns]
        if cols:
            out = add_panel_aggregates(out, "score", metric, cols)
    out = add_macdh_features(out)
    out = add_forward_targets(out)

    if not include_holdout:
        from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

        out = pre_cutoff_filter(out)
        assert_no_holdout_leak(out, "sector_wave/build_predictive_wave_cache")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version-id", type=int, required=True)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--include-holdout", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else OUT_DIR / f"predictive_market_wave_v{args.version_id}.parquet"
    df = build_cache(args.version_id, args.start_date, args.end_date, args.include_holdout)
    df.to_parquet(out_path, index=False)
    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "version_id": args.version_id,
        "rows": int(len(df)),
        "start": str(df["date"].min().date()) if len(df) else None,
        "end": str(df["date"].max().date()) if len(df) else None,
        "include_holdout": bool(args.include_holdout),
        "out": str(out_path),
    }
    out_path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
