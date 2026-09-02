"""Spread-skill diagnostic: does disagreement among the 6 component scores
predict directional outcome (WR15), CONTROLLING FOR the overall score?

Weather framing: the 5 base components {trend, bb, rsi, macd, stoch} are a
6-member ensemble (technical_alignment / X is a pre-existing agreement metric).
A real ensemble has the spread-skill property: high-spread signals have higher
forecast error. If component dispersion predicts lower WR15 *at fixed score*,
that dispersion is a free, parsimonious confidence signal that could subsume a
chunk of the hand-tuned "this signal is weak" dampener family.

Round 1 uses the GENERIC barrier (K=2sigma/M=5sigma, the cached dashboard
metric) to answer "is there directional information at all". Option-predictand
(TP/SL 0.30/-0.70) is the round-2 refinement if this is positive.

Read-only: joins STORED v73 component columns to the version-agnostic
barrier_outcomes cache. No re-score, no MC, no recalculate.
"""
import os
import sys
import math
from collections import namedtuple, defaultdict

import numpy as np
import polars as pl

# Pin imports to THIS checkout (avoid the worktree-PYTHONPATH trap; harmless on main).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
START_YEAR, END_YEAR = 2016, 2027
MEMBERS = ["trend", "bb", "rsi", "macd", "stoch"]


def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment "
        "FROM scores WHERE version_id={vid} AND overall>=70 "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=START_YEAR, end_year=END_YEAR, vid=VID,
    )
    return pl.DataFrame(
        {
            "symbol":  [r[0] for r in rows],
            "date":    [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "trend":   [float(r[3]) for r in rows],
            "bb":      [float(r[4]) for r in rows],
            "rsi":     [float(r[5]) for r in rows],
            "macd":    [float(r[6]) for r in rows],
            "stoch":   [float(r[7]) for r in rows],
            "ta":      [float(r[8]) for r in rows],
        }
    )


def two_prop_z(x1, n1, x2, n2):
    """z for p1 (low-spread) vs p2 (high-spread). Positive z => low-spread wins more."""
    if n1 == 0 or n2 == 0:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def main():
    from datetime import date as _date
    parquet = materialize_polars(f"ensemble_spread_v{VID}_calls_{START_YEAR}", _build)
    df = pl.read_parquet(parquet)
    print(f"\nv{VID} calls 70+ pulled: {df.height:,} rows")

    # --- spread + agreement at the ensemble (5-member) level ---
    mean5 = sum(pl.col(c) for c in MEMBERS) / 5.0
    var5 = sum((pl.col(c) - mean5) ** 2 for c in MEMBERS) / 5.0
    frac_bull = sum((pl.col(c) > 50).cast(pl.Float64) for c in MEMBERS) / 5.0
    df = df.with_columns(
        spread=var5.sqrt(),
        frac_bull=frac_bull,
    )

    # --- barrier-touch WR15 (generic) via the cache ---
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [
        Peak(s, _date.fromisoformat(d), int(o))
        for s, d, o in zip(df["symbol"], df["date"], df["overall"])
    ]
    results, skipped = peaks_to_swing_results(peaks, verbose=False)
    win = {}
    for r in results:
        dk = r["date"]
        dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("15d")
        if sw and sw.get("result") in ("win", "stop", "expire"):
            win[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
    print(f"barrier join: {len(win):,} peaks with WR15 ({skipped:,} uncached/insufficient)")

    df = df.with_columns(
        win15=pl.struct(["symbol", "date"]).map_elements(
            lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64
        )
    ).filter(pl.col("win15") >= 0)
    print(f"usable (component + WR15): {df.height:,} rows\n")

    # ---- numpy arrays for controlled analysis ----
    sc = df["overall"].to_numpy()
    sp = df["spread"].to_numpy()
    ta = df["ta"].to_numpy()
    fb = df["frac_bull"].to_numpy()
    w = df["win15"].to_numpy().astype(float)

    bands = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]

    def tercile_table(metric, mname):
        print(f"=== WR15 by {mname} tercile, WITHIN score band  (does {mname} predict outcome at fixed score?) ===")
        print(f"{'band':>8} | {'N':>6} | {'baseWR':>7} | {'lowT WR/N':>14} | {'midT WR/N':>14} | {'hiT WR/N':>14} | {'low-hi':>7} | {'z':>6}")
        agg_x1 = agg_n1 = agg_x2 = agg_n2 = 0
        for lo, hi in bands:
            m = (sc >= lo) & (sc <= hi)
            if m.sum() < 60:
                continue
            mv, wv = metric[m], w[m]
            q1, q2 = np.quantile(mv, [1 / 3, 2 / 3])
            lowT = mv <= q1            # low metric
            hiT = mv > q2              # high metric
            midT = (~lowT) & (~hiT)
            def wr(mask):
                return (wv[mask].mean() * 100, int(mask.sum())) if mask.sum() else (float("nan"), 0)
            base = wv.mean() * 100
            (lwr, ln), (mwr, mn), (hwr, hn) = wr(lowT), wr(midT), wr(hiT)
            z = two_prop_z(wv[lowT].sum(), ln, wv[hiT].sum(), hn)
            print(f"{lo}-{hi:>3} | {int(m.sum()):>6} | {base:6.1f}% | {lwr:6.1f}% {ln:>5} | {mwr:6.1f}% {mn:>5} | {hwr:6.1f}% {hn:>5} | {lwr-hwr:+6.1f} | {z:+5.2f}")
            agg_x1 += wv[lowT].sum(); agg_n1 += ln
            agg_x2 += wv[hiT].sum(); agg_n2 += hn
        zt = two_prop_z(agg_x1, agg_n1, agg_x2, agg_n2)
        a1 = agg_x1 / agg_n1 * 100 if agg_n1 else float("nan")
        a2 = agg_x2 / agg_n2 * 100 if agg_n2 else float("nan")
        print(f"{'POOLED':>8} | {'':>6} | {'':>7} | {a1:6.1f}% {agg_n1:>5} | {'':>14} | {a2:6.1f}% {agg_n2:>5} | {a1-a2:+6.1f} | {zt:+5.2f}")
        print(f"  (low tercile = {'low '+mname}, hi tercile = high {mname}; positive low-hi => low {mname} signals win more)\n")
        return zt

    # For SPREAD: hypothesis = HIGH spread -> LOWER WR, so low-spread tercile should win MORE (positive low-hi).
    z_spread = tercile_table(sp, "SPREAD")
    # For TA (X, existing agreement metric): hypothesis = HIGH ta -> agreement -> HIGHER WR, so low-ta wins LESS (negative low-hi).
    z_ta = tercile_table(ta, "TA(X)")
    # frac_bull: more bullish members -> higher WR for calls; low frac_bull should win LESS (negative low-hi).
    z_fb = tercile_table(fb, "FRAC_BULL")

    # ---- spread-skill summary, score-residualized ----
    print("=== HEADLINE ===")
    print(f"SPREAD pooled low-vs-high z = {z_spread:+.2f}  "
          f"(|z|>=2 and positive => disagreement predicts failure at fixed score => ensemble-confidence is real)")
    print(f"TA(X)  pooled low-vs-high z = {z_ta:+.2f}  (the metric you ALREADY compute)")
    print(f"FRAC_BULL pooled z          = {z_fb:+.2f}")
    print(f"\nNOTE: generic K=2sigma/M=5sigma barrier (round 1). holdout cutoff is 2026-06-15; "
          f"data ends 2026-06-12 so this read is fully in-sample (no forward window exists yet).")


if __name__ == "__main__":
    main()
