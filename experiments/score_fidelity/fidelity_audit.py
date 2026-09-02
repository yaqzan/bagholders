"""Score-fidelity audit (analysis-quality, not forecast-skill): does each component score
FAITHFULLY read its raw indicator?

Joins stored v74 component scores to the raw `indicators` table and scans each
component's raw_indicator -> component_score mapping for the measurement-fidelity
failure modes that matter AT THE GATE (where 75+ membership is decided):

  1. monotonicity / inversion  — Spearman(raw, component); does bullish raw -> bullish comp?
  2. cliffs / discontinuities   — max |Δ mean component| between adjacent fine raw bins
                                  (a cliff near the gate = a fakeout / noisy membership source)
  3. saturation / info loss     — % of rows where the component is pinned at <=2 or >=98
  4. context-dependence (noise?) — within-raw-bin std of the component (high = the component
                                  reads MORE than its raw indicator; faithful push-bands OR noise)
  5. GATE-NEIGHBORHOOD focus    — (4) restricted to rows with overall in [68,77]: how much the
                                  component wobbles for a FIXED raw value right at the gate.

Read-only. Stride-symbol sample. v74 active. The funded bar (separate step): any fix must
change the 75+ gate membership for the better — within-75+ re-ranking is inert (verify_value).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import polars as pl
from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.models.core import AlgorithmVersion, Stock

VID = AlgorithmVersion.get_active_scores_version().id
STRIDE = int(os.environ.get("STRIDE", "3"))


def _syms():
    alls = [s.symbol for s in Stock.select(Stock.symbol).order_by(Stock.symbol)]
    return alls[::STRIDE]


def _build():
    syms = _syms()
    syms_in = ",".join("'" + s.replace("'", "''") + "'" for s in syms)
    print(f"  pulling scores+indicators for {len(syms)} stride-{STRIDE} symbols...", flush=True)
    srows = chunked_query_by_year(
        "SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd, s.stoch, "
        "s.technical_alignment, s.bb_position, s.pct_from_ema50 "
        "FROM scores s WHERE s.version_id={vid} AND s.overall>=50 "
        "AND s.symbol IN ({syms_in}) AND s.date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, vid=VID, syms_in=syms_in, timeout_ms=290_000,
    )
    sdf = pl.DataFrame({
        "symbol": [r[0] for r in srows],
        "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in srows],
        "overall": [int(r[2]) for r in srows],
        "c_trend": [float(r[3]) if r[3] is not None else None for r in srows],
        "c_bb":    [float(r[4]) if r[4] is not None else None for r in srows],
        "c_rsi":   [float(r[5]) if r[5] is not None else None for r in srows],
        "c_macd":  [float(r[6]) if r[6] is not None else None for r in srows],
        "c_stoch": [float(r[7]) if r[7] is not None else None for r in srows],
        "c_ta":    [float(r[8]) if r[8] is not None else None for r in srows],
        "bb_position": [float(r[9]) if r[9] is not None else None for r in srows],
        "pct_ema50":   [float(r[10]) if r[10] is not None else None for r in srows],
    })
    irows = chunked_query_by_year(
        "SELECT symbol, date, rsi, stoch, macd_hist, upper_band, lower_band, middle_band "
        "FROM indicators WHERE symbol IN ({syms_in}) AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, syms_in=syms_in, timeout_ms=290_000,
    )
    idf = pl.DataFrame({
        "symbol": [r[0] for r in irows],
        "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in irows],
        "raw_rsi":   [float(r[2]) if r[2] is not None else None for r in irows],
        "raw_stoch": [float(r[3]) if r[3] is not None else None for r in irows],
        "raw_macdh": [float(r[4]) if r[4] is not None else None for r in irows],
        "ub": [float(r[5]) if r[5] is not None else None for r in irows],
        "lb": [float(r[6]) if r[6] is not None else None for r in irows],
        "mb": [float(r[7]) if r[7] is not None else None for r in irows],
    })
    return sdf.join(idf, on=["symbol", "date"], how="inner")


def spearman(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    if len(x) < 100:
        return float("nan")
    rx = np.argsort(np.argsort(x)).astype(float); ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    d = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else float("nan")


def scan(name, raw, comp, overall, expect_sign=+1):
    m = ~(np.isnan(raw) | np.isnan(comp))
    raw, comp, ov = raw[m], comp[m], overall[m]
    n = len(raw)
    sp = spearman(raw, comp)
    sat = ((comp <= 2) | (comp >= 98)).mean() * 100
    # fine bins (40) over the raw 2-98 pctile range; mean comp per bin -> cliff = max adjacent jump
    lo, hi = np.nanpercentile(raw, [2, 98])
    edges = np.linspace(lo, hi, 41)
    means, wstd = [], []
    for i in range(40):
        b = (raw >= edges[i]) & (raw < edges[i + 1]) if i < 39 else (raw >= edges[i]) & (raw <= edges[i + 1])
        if b.sum() >= 30:
            means.append(comp[b].mean()); wstd.append(comp[b].std())
        else:
            means.append(np.nan); wstd.append(np.nan)
    means = np.array(means)
    diffs = np.abs(np.diff(means))
    cliff = np.nanmax(diffs) if np.isfinite(diffs).any() else float("nan")
    cliff_at = edges[int(np.nanargmax(diffs))] if np.isfinite(diffs).any() else float("nan")
    ctx_std = np.nanmean(wstd)  # mean within-raw-bin std of the component = context-dependence
    # gate neighborhood: rows with overall in [68,77]
    g = (ov >= 68) & (ov <= 77)
    gctx = float("nan")
    if g.sum() >= 300:
        rg, cg = raw[g], comp[g]
        ge = np.linspace(*np.nanpercentile(rg, [5, 95]), 11)
        gs = []
        for i in range(10):
            b = (rg >= ge[i]) & (rg < ge[i + 1])
            if b.sum() >= 30:
                gs.append(cg[b].std())
        gctx = float(np.mean(gs)) if gs else float("nan")
    print(f"  {name:>10} | n={n:>7,} | spearman={sp:+.3f}{'  ⚠INV' if (sp*expect_sign)<0 else '':>6} | "
          f"sat={sat:4.1f}% | maxCliff={cliff:5.1f}@raw={cliff_at:6.1f} | ctxStd={ctx_std:4.1f} | gateCtxStd={gctx:4.1f}")
    return {"name": name, "n": n, "spearman": sp, "sat_pct": sat, "max_cliff": cliff,
            "cliff_at": cliff_at, "ctx_std": ctx_std, "gate_ctx_std": gctx}


def main():
    df = pl.read_parquet(materialize_polars(f"score_fidelity_v{VID}_{STRIDE}", _build))
    print(f"\n=== SCORE-FIDELITY AUDIT  v{VID}  (joined component-score x raw-indicator rows: {df.height:,}) ===")
    print("  bb_position = raw BB %-position; pct_ema50 = raw trend proxy; raw_* = indicators table\n")
    ov = df["overall"].to_numpy().astype(float)
    print(f"  {'component':>10} | {'N':>9} | {'monotonic':>16} | {'satur':>5} | {'discontinuity':>22} | {'ctxStd':>6} | {'gateCtxStd':>10}")
    print("  " + "-" * 100)
    rows = []
    rows.append(scan("rsi",   df["raw_rsi"].to_numpy().astype(float),   df["c_rsi"].to_numpy().astype(float),   ov, expect_sign=-1))  # RSI score is mean-reversion-lensed (high RSI -> lower score)
    rows.append(scan("macd",  df["raw_macdh"].to_numpy().astype(float), df["c_macd"].to_numpy().astype(float),  ov, expect_sign=+1))
    rows.append(scan("stoch", df["raw_stoch"].to_numpy().astype(float), df["c_stoch"].to_numpy().astype(float), ov, expect_sign=+1))
    rows.append(scan("bb",    df["bb_position"].to_numpy().astype(float), df["c_bb"].to_numpy().astype(float),  ov, expect_sign=+1))
    rows.append(scan("trend", df["pct_ema50"].to_numpy().astype(float),   df["c_trend"].to_numpy().astype(float), ov, expect_sign=+1))
    print(f"\n  READ: ⚠INV = mapping runs opposite to the expected sign (audit the sign convention).")
    print(f"        sat = % pinned at <=2 or >=98 (information loss). maxCliff = biggest jump in mean")
    print(f"        component between adjacent raw bins (a large cliff = a fakeout/membership-noise source).")
    print(f"        ctxStd = component wobble at a FIXED raw value (push-band context, faithful OR noise);")
    print(f"        gateCtxStd = same but for rows AT the 68-77 gate neighborhood (the funded-relevant one).")
    import json
    with open(os.path.join(os.path.dirname(__file__), "fidelity_results.json"), "w") as f:
        json.dump(rows, f, indent=2, default=float)


if __name__ == "__main__":
    main()
