"""One-sided TP multiplier sweep from conditional magnitude findings.

The symmetric TP sweep mixed two different behaviors:
  - winner-headroom features should widen TP only on the high-headroom side;
  - loser-near-miss features should tighten TP only on the near-miss side.

This script reuses the fast in-memory OHLCV walk from sweep_tp_multipliers.py
and tests one-sided curves only.
"""
from __future__ import annotations

import io
import math
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import polars as pl

from experiments.magnitude_predictors.sweep_tp_multipliers import (
    LOOKBACK_DAYS,
    W_DAYS,
    OUT_DIR,
    _load_signal_path,
    _prepare_signals,
    _load_bars,
    _walk_one,
    _score_band,
)


def _norm(values: pd.Series) -> np.ndarray:
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


def _enrich(signals: pd.DataFrame) -> pd.DataFrame:
    out = signals.copy()
    side_sign = np.where(out["is_call"], 1.0, -1.0)
    out["wi_w_adj_side"] = pd.to_numeric(out["wi_w_adj"], errors="coerce") * side_sign
    out["wi_w_mom_side"] = pd.to_numeric(out["wi_w_mom"], errors="coerce") * side_sign
    out["macd_hist_side"] = pd.to_numeric(out["macd_hist"], errors="coerce") * side_sign
    return out


def _multipliers(signals: pd.DataFrame, variant: str) -> np.ndarray:
    mult = np.ones(len(signals), dtype=float)
    is_call = signals["is_call"].to_numpy(dtype=bool)
    is_put = ~is_call
    overall = signals["overall"].to_numpy(dtype=float)

    call_breadth_pos = np.maximum(_norm(signals.loc[is_call, "pct_above_ema200_mkt"]), 0.0)
    put_stoch_pos = np.maximum(_norm(signals.loc[is_put, "stoch_edge"]), 0.0)
    call_wadj_pos = np.maximum(_norm(signals.loc[is_call, "wi_w_adj_side"]), 0.0)
    call_wmom_pos = np.maximum(_norm(signals.loc[is_call, "wi_w_mom_side"]), 0.0)
    put_wadj_pos = np.maximum(_norm(signals.loc[is_put, "wi_w_adj_side"]), 0.0)
    put_macdh_pos = np.maximum(_norm(signals.loc[is_put, "macd_hist_side"]), 0.0)

    if variant == "baseline":
        return mult

    if variant.startswith("call_breadth_widen_pos"):
        amp = int(variant.removeprefix("call_breadth_widen_pos")) / 100.0
        mult[is_call] = 1.0 + amp * call_breadth_pos
    elif variant.startswith("call_breadth_80_89_widen_pos"):
        amp = int(variant.removeprefix("call_breadth_80_89_widen_pos")) / 100.0
        tmp = np.ones(is_call.sum(), dtype=float)
        mask = (overall[is_call] >= 80) & (overall[is_call] < 90)
        tmp[mask] = 1.0 + amp * call_breadth_pos[mask]
        mult[is_call] = tmp
    elif variant.startswith("put_stoch_widen_pos"):
        amp = int(variant.removeprefix("put_stoch_widen_pos")) / 100.0
        mult[is_put] = 1.0 + amp * put_stoch_pos
    elif variant.startswith("call_wadj_tighten_pos"):
        amp = int(variant.removeprefix("call_wadj_tighten_pos")) / 100.0
        mult[is_call] = 1.0 - amp * call_wadj_pos
    elif variant.startswith("call_wmom_tighten_pos"):
        amp = int(variant.removeprefix("call_wmom_tighten_pos")) / 100.0
        mult[is_call] = 1.0 - amp * call_wmom_pos
    elif variant.startswith("put_wadj_tighten_pos"):
        amp = int(variant.removeprefix("put_wadj_tighten_pos")) / 100.0
        mult[is_put] = 1.0 - amp * put_wadj_pos
    elif variant.startswith("put_macdh_tighten_pos"):
        amp = int(variant.removeprefix("put_macdh_tighten_pos")) / 100.0
        mult[is_put] = 1.0 - amp * put_macdh_pos
    else:
        raise ValueError(f"unknown variant {variant}")

    return np.clip(mult, 0.50, 1.50)


def _evaluate(signals: pd.DataFrame, bars_by_sym: dict, variant: str) -> pd.DataFrame:
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
    def agg(group_cols):
        g = df.groupby(group_cols, observed=True)
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
        return out

    band = agg(["variant", "side", "score_band"])
    all_side = agg(["variant", "side"])
    all_side["score_band"] = "ALL"
    return pd.concat([all_side, band], ignore_index=True)


def main() -> None:
    signals = _enrich(_prepare_signals(_load_signal_path()))
    print(f"[one-sided] loaded {len(signals):,} signals", flush=True)
    bars_by_sym = _load_bars(signals)
    print(f"[one-sided] bars loaded for {len(bars_by_sym):,} symbols", flush=True)

    variants = [
        "baseline",
        "call_breadth_widen_pos10",
        "call_breadth_widen_pos20",
        "call_breadth_widen_pos30",
        "call_breadth_80_89_widen_pos20",
        "call_breadth_80_89_widen_pos30",
        "put_stoch_widen_pos10",
        "put_stoch_widen_pos20",
        "put_stoch_widen_pos30",
        "call_wadj_tighten_pos10",
        "call_wadj_tighten_pos20",
        "call_wmom_tighten_pos10",
        "call_wmom_tighten_pos20",
        "put_wadj_tighten_pos10",
        "put_wadj_tighten_pos20",
        "put_macdh_tighten_pos10",
        "put_macdh_tighten_pos20",
    ]

    frames = []
    t0 = time.time()
    for variant in variants:
        ti = time.time()
        raw = _evaluate(signals, bars_by_sym, variant)
        frames.append(raw)
        print(f"[one-sided] {variant}: {len(raw):,} rows ({time.time() - ti:.1f}s)", flush=True)

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

    out_csv = OUT_DIR / f"one_sided_tp_summary_w{W_DAYS}_{LOOKBACK_DAYS}.csv"
    summary.to_csv(out_csv, index=False)

    focus = summary[summary["score_band"].eq("ALL") & ~summary["variant"].eq("baseline")]
    print("\n[one-sided] ALL-side summary vs baseline", flush=True)
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
        ].sort_values(["side", "delta_avg_exit_sigma"], ascending=[True, False]).to_string(index=False),
        flush=True,
    )
    print(f"[one-sided] wrote {out_csv}", flush=True)
    print(f"[one-sided] elapsed {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
