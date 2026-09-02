"""Stage 2 stock-barrier screen for magnitude-derived TP multipliers.

Uses the stock-only magnitude features from build_features.py and tests whether
smooth feature-driven TP-distance changes improve generic stock barrier EV.

This is deliberately not an options/IV experiment:
  - TP/SL are underlying stock moves in sigma units.
  - Sigma comes from the existing barrier cache / 60d realized volatility.
  - The score formula is untouched.

Candidate features from the first screen:
  - put stoch_edge: stronger stochastic edge had farther residual MFE.
  - call pct_above_ema200_mkt: healthy long-term market breadth had farther
    residual call MFE.

Barrier baseline:
  - calls: K=2.0 sigma target / M=5.0 sigma stop at 30d reference
  - puts:  K=1.0 sigma target / M=2.0 sigma stop at 30d reference
  - W=15 calendar days, scaled by sqrt(W/30)
"""
from __future__ import annotations

import io
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

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
W_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 15

K_CALL_BASE = 2.0
M_CALL_BASE = 5.0
K_PUT_BASE = 1.0
M_PUT_BASE = 2.0
REF_DAYS = 30


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


def _to_float(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        return float("nan")
    return x if math.isfinite(x) else float("nan")


def _prepare_signals(path: Path) -> pd.DataFrame:
    df = pl.read_parquet(path)
    assert_no_holdout_leak(df, "magnitude_predictors/sweep_tp_multipliers")
    pdf = df.to_pandas()
    for col in pdf.columns:
        if col not in {"symbol", "date", "side", "sector", "industry", "barrier_side"}:
            pdf[col] = pd.to_numeric(pdf[col], errors="coerce")

    sigma_col = f"sigma_pct_{W_DAYS}"
    pdf = pdf[np.isfinite(pdf[sigma_col]) & (pdf[sigma_col] > 0)].copy()
    pdf["is_call"] = pdf["side"].eq("call")
    pdf["stoch_edge"] = np.where(pdf["is_call"], pdf["stoch_score"], 100 - pdf["stoch_score"])
    pdf["signal_sigma"] = pdf[sigma_col]
    pdf["date"] = pdf["date"].astype(str)
    return pdf


def _load_bars(signals: pd.DataFrame, refresh: bool = False) -> dict[str, dict[str, Any]]:
    bars_path = OUT_DIR / f"bars_{LOOKBACK_DAYS}_{W_DAYS}.parquet"
    if bars_path.exists() and not refresh:
        bars = pl.read_parquet(bars_path)
    else:
        from database.trader_database import DB

        syms = sorted(signals["symbol"].dropna().unique().tolist())
        d0 = date.fromisoformat(signals["date"].min()) - timedelta(days=90)
        d1 = date.fromisoformat(signals["date"].max()) + timedelta(days=W_DAYS + 15)
        print(f"[bars] querying {len(syms):,} symbols {d0.isoformat()}..{d1.isoformat()}", flush=True)
        rows = []
        t0 = time.time()
        for i in range(0, len(syms), 100):
            batch = syms[i:i + 100]
            placeholders = ",".join(["%s"] * len(batch))
            cur = DB.execute_sql(
                f"""
                SELECT symbol, date, open, high, low, close
                FROM price_history
                WHERE symbol IN ({placeholders})
                  AND date >= %s AND date <= %s
                """,
                tuple(batch) + (d0.isoformat(), d1.isoformat()),
            )
            rows.extend(cur.fetchall())
            print(f"[bars]   {min(i + 100, len(syms)):,}/{len(syms):,} symbols; {len(rows):,} bars", flush=True)
        bars = pl.DataFrame(
            {
                "symbol": [r[0] for r in rows],
                "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
                "open": [_to_float(r[2]) for r in rows],
                "high": [_to_float(r[3]) for r in rows],
                "low": [_to_float(r[4]) for r in rows],
                "close": [_to_float(r[5]) for r in rows],
            }
        ).sort(["symbol", "date"])
        bars.write_parquet(bars_path)
        print(f"[bars] cached {len(bars):,} bars in {time.time() - t0:.1f}s", flush=True)

    by_sym: dict[str, dict[str, Any]] = {}
    for key, group in bars.group_by("symbol"):
        sym = key[0] if isinstance(key, tuple) else key
        g = group.sort("date")
        dates = g["date"].to_list()
        by_sym[sym] = {
            "dates": dates,
            "ordinals": np.array([date.fromisoformat(d).toordinal() for d in dates], dtype=np.int64),
            "open": np.array(g["open"].to_list(), dtype=np.float64),
            "high": np.array(g["high"].to_list(), dtype=np.float64),
            "low": np.array(g["low"].to_list(), dtype=np.float64),
            "close": np.array(g["close"].to_list(), dtype=np.float64),
            "date_to_idx": {d: i for i, d in enumerate(dates)},
        }
    return by_sym


def _norm_feature(values: pd.Series) -> np.ndarray:
    arr = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(arr)
    out = np.zeros(len(arr), dtype=float)
    if finite.sum() < 50:
        return out
    p10 = np.nanpercentile(arr[finite], 10)
    p90 = np.nanpercentile(arr[finite], 90)
    if not math.isfinite(p10) or not math.isfinite(p90) or p10 == p90:
        return out
    out[finite] = np.clip((arr[finite] - p10) / (p90 - p10) * 2.0 - 1.0, -1.0, 1.0)
    return out


def _multipliers(signals: pd.DataFrame, variant: str) -> np.ndarray:
    mult = np.ones(len(signals), dtype=float)
    is_call = signals["is_call"].to_numpy(dtype=bool)
    is_put = ~is_call
    overall = signals["overall"].to_numpy(dtype=float)
    put_stoch = _norm_feature(signals.loc[is_put, "stoch_edge"])
    call_breadth = _norm_feature(signals.loc[is_call, "pct_above_ema200_mkt"])

    if variant == "baseline":
        return mult
    if variant == "put_stoch_amp10":
        mult[is_put] = 1.0 + 0.10 * put_stoch
    elif variant == "put_stoch_amp20":
        mult[is_put] = 1.0 + 0.20 * put_stoch
    elif variant == "put_stoch_amp30":
        mult[is_put] = 1.0 + 0.30 * put_stoch
    elif variant == "call_breadth_amp10":
        mult[is_call] = 1.0 + 0.10 * call_breadth
    elif variant == "call_breadth_amp20":
        mult[is_call] = 1.0 + 0.20 * call_breadth
    elif variant == "call_breadth_75_89_amp10":
        tmp = np.ones(is_call.sum(), dtype=float)
        tmp[(overall[is_call] >= 75) & (overall[is_call] < 90)] = (
            1.0 + 0.10 * call_breadth[(overall[is_call] >= 75) & (overall[is_call] < 90)]
        )
        mult[is_call] = tmp
    elif variant == "call_breadth_75_89_amp20":
        tmp = np.ones(is_call.sum(), dtype=float)
        tmp[(overall[is_call] >= 75) & (overall[is_call] < 90)] = (
            1.0 + 0.20 * call_breadth[(overall[is_call] >= 75) & (overall[is_call] < 90)]
        )
        mult[is_call] = tmp
    elif variant == "call_breadth_80_89_amp20":
        tmp = np.ones(is_call.sum(), dtype=float)
        tmp[(overall[is_call] >= 80) & (overall[is_call] < 90)] = (
            1.0 + 0.20 * call_breadth[(overall[is_call] >= 80) & (overall[is_call] < 90)]
        )
        mult[is_call] = tmp
    elif variant == "call_breadth_80_89_amp30":
        tmp = np.ones(is_call.sum(), dtype=float)
        tmp[(overall[is_call] >= 80) & (overall[is_call] < 90)] = (
            1.0 + 0.30 * call_breadth[(overall[is_call] >= 80) & (overall[is_call] < 90)]
        )
        mult[is_call] = tmp
    elif variant == "combo_amp10":
        mult[is_put] = 1.0 + 0.10 * put_stoch
        mult[is_call] = 1.0 + 0.10 * call_breadth
    elif variant == "combo_amp20":
        mult[is_put] = 1.0 + 0.20 * put_stoch
        mult[is_call] = 1.0 + 0.20 * call_breadth
    elif variant == "inverse_sanity_amp20":
        mult[is_put] = 1.0 - 0.20 * put_stoch
        mult[is_call] = 1.0 - 0.20 * call_breadth
    else:
        raise ValueError(f"unknown variant {variant}")
    return np.clip(mult, 0.50, 1.50)


def _walk_one(bars: dict[str, Any], idx: int, is_call: bool, sigma: float, tp_mult: float) -> tuple[int, int, float, float]:
    entry = bars["close"][idx]
    if entry <= 0 or sigma <= 0:
        return -1, -1, float("nan"), float("nan")
    scale = math.sqrt(W_DAYS / REF_DAYS)
    if is_call:
        k = K_CALL_BASE * tp_mult * scale
        m = M_CALL_BASE * scale
        t_win = entry * (1.0 + k * sigma / 100.0)
        t_stop = entry * (1.0 - m * sigma / 100.0)
    else:
        k = K_PUT_BASE * tp_mult * scale
        m = M_PUT_BASE * scale
        t_win = entry * (1.0 - k * sigma / 100.0)
        t_stop = entry * (1.0 + m * sigma / 100.0)

    deadline = bars["ordinals"][idx] + W_DAYS
    last_close = float("nan")
    last_bars = -1
    for j in range(idx + 1, len(bars["close"])):
        if bars["ordinals"][j] > deadline:
            break
        hi = bars["high"][j]
        lo = bars["low"][j]
        last_close = bars["close"][j]
        last_bars = j - idx
        if is_call:
            if hi >= t_win:
                return 1, last_bars, (t_win - entry) / entry * 100.0, k
            if lo <= t_stop:
                return 0, last_bars, (t_stop - entry) / entry * 100.0, k
        else:
            if lo <= t_win:
                return 1, last_bars, (entry - t_win) / entry * 100.0, k
            if hi >= t_stop:
                return 0, last_bars, (entry - t_stop) / entry * 100.0, k

    if not math.isfinite(last_close):
        return -1, -1, float("nan"), float("nan")
    if is_call:
        ret = (last_close - entry) / entry * 100.0
    else:
        ret = (entry - last_close) / entry * 100.0
    return 0, last_bars, ret, k


def _score_band(side: str, overall: int) -> str:
    if side == "call":
        if overall >= 95:
            return "95+"
        if overall >= 90:
            return "90-94"
        if overall >= 85:
            return "85-89"
        if overall >= 80:
            return "80-84"
        if overall >= 75:
            return "75-79"
        return "70-74"
    if overall <= 5:
        return "0-5"
    if overall <= 10:
        return "6-10"
    if overall <= 15:
        return "11-15"
    if overall <= 20:
        return "16-20"
    return "21-25"


def _evaluate(signals: pd.DataFrame, bars_by_sym: dict[str, dict[str, Any]], variant: str) -> pd.DataFrame:
    mults = _multipliers(signals, variant)
    rows = []
    for i, row in enumerate(signals.itertuples(index=False)):
        bars = bars_by_sym.get(row.symbol)
        if bars is None:
            continue
        idx = bars["date_to_idx"].get(row.date)
        if idx is None:
            continue
        result, exit_bars, exit_ret, k_eff = _walk_one(
            bars=bars,
            idx=idx,
            is_call=bool(row.is_call),
            sigma=float(row.signal_sigma),
            tp_mult=float(mults[i]),
        )
        if result < 0 or not math.isfinite(exit_ret):
            continue
        rows.append(
            {
                "variant": variant,
                "side": row.side,
                "score_band": _score_band(row.side, int(row.overall)),
                "result": result,
                "exit_bars": exit_bars,
                "exit_return_pct": exit_ret,
                "exit_return_sigma": exit_ret / float(row.signal_sigma),
                "k_eff": k_eff,
                "tp_mult": float(mults[i]),
            }
        )
    return pd.DataFrame(rows)


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["variant", "side", "score_band"], observed=True)
    out = g.agg(
        n=("result", "size"),
        win_rate=("result", "mean"),
        avg_exit_sigma=("exit_return_sigma", "mean"),
        med_exit_sigma=("exit_return_sigma", "median"),
        avg_exit_pct=("exit_return_pct", "mean"),
        median_bars=("exit_bars", "median"),
        avg_k_eff=("k_eff", "mean"),
        avg_tp_mult=("tp_mult", "mean"),
    ).reset_index()
    out["win_rate"] *= 100.0

    all_g = df.groupby(["variant", "side"], observed=True)
    all_out = all_g.agg(
        n=("result", "size"),
        win_rate=("result", "mean"),
        avg_exit_sigma=("exit_return_sigma", "mean"),
        med_exit_sigma=("exit_return_sigma", "median"),
        avg_exit_pct=("exit_return_pct", "mean"),
        median_bars=("exit_bars", "median"),
        avg_k_eff=("k_eff", "mean"),
        avg_tp_mult=("tp_mult", "mean"),
    ).reset_index()
    all_out["score_band"] = "ALL"
    all_out["win_rate"] *= 100.0
    return pd.concat([all_out, out], ignore_index=True)


def main() -> None:
    signal_path = _load_signal_path()
    signals = _prepare_signals(signal_path)
    print(f"[sweep] loaded {len(signals):,} signals from {signal_path}", flush=True)
    bars_by_sym = _load_bars(signals)
    print(f"[sweep] bars loaded for {len(bars_by_sym):,} symbols", flush=True)

    variants = [
        "baseline",
        "put_stoch_amp10",
        "put_stoch_amp20",
        "put_stoch_amp30",
        "call_breadth_amp10",
        "call_breadth_amp20",
        "call_breadth_75_89_amp10",
        "call_breadth_75_89_amp20",
        "call_breadth_80_89_amp20",
        "call_breadth_80_89_amp30",
        "combo_amp10",
        "combo_amp20",
        "inverse_sanity_amp20",
    ]
    frames = []
    t0 = time.time()
    for variant in variants:
        ti = time.time()
        raw = _evaluate(signals, bars_by_sym, variant)
        raw_path = OUT_DIR / f"tp_sweep_raw_{variant}_w{W_DAYS}_{LOOKBACK_DAYS}.parquet"
        pl.from_pandas(raw).write_parquet(raw_path)
        frames.append(raw)
        print(f"[sweep] {variant}: {len(raw):,} rows ({time.time() - ti:.1f}s)", flush=True)

    all_raw = pd.concat(frames, ignore_index=True)
    agg = _aggregate(all_raw)
    base = agg[agg["variant"].eq("baseline")].set_index(["side", "score_band"])
    rows = []
    for r in agg.itertuples(index=False):
        key = (r.side, r.score_band)
        b = base.loc[key]
        d = r._asdict()
        d["delta_wr_pp"] = r.win_rate - b["win_rate"]
        d["delta_avg_exit_sigma"] = r.avg_exit_sigma - b["avg_exit_sigma"]
        d["delta_median_bars"] = r.median_bars - b["median_bars"]
        rows.append(d)
    summary = pd.DataFrame(rows)
    summary = summary.sort_values(["side", "score_band", "delta_avg_exit_sigma"], ascending=[True, True, False])

    out_csv = OUT_DIR / f"tp_multiplier_summary_w{W_DAYS}_{LOOKBACK_DAYS}.csv"
    summary.to_csv(out_csv, index=False)

    focus = summary[
        summary["score_band"].eq("ALL")
        & ~summary["variant"].eq("baseline")
    ].sort_values(["side", "delta_avg_exit_sigma"], ascending=[True, False])
    print("\n[sweep] ALL-side summary vs baseline", flush=True)
    print(
        focus[
            [
                "variant",
                "side",
                "n",
                "win_rate",
                "delta_wr_pp",
                "avg_exit_sigma",
                "delta_avg_exit_sigma",
                "median_bars",
                "delta_median_bars",
                "avg_tp_mult",
            ]
        ].to_string(index=False),
        flush=True,
    )
    print(f"[sweep] wrote {out_csv}", flush=True)
    print(f"[sweep] elapsed {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
