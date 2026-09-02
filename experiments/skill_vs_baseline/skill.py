"""Skill-vs-baseline diagnostic (weather skill score, signal level).

A forecast has skill only if it beats (1) CLIMATOLOGY (unconditional base rate)
and (2) PERSISTENCE/MOMENTUM (the trivial "trend continues" forecast). The
decisive test: does the score discriminate BEYOND momentum, at matched momentum?

Everything is measured as a CALL (up-barrier) on the generic K=2sigma/M=5sigma
barrier, so score / climatology / momentum are apples-to-apples (same barrier
walk via the version-agnostic cache). Read-only: stored v73 scores + barrier
cache. No re-score / MC / recalc.

  - Climatology   = WR15 of buying a call on a RANDOM stock-day (all scores).
  - Score skill   = WR15(score-bucket) - climatology, with z.
  - Momentum      = WR15 by pct_from_ema50 decile + matched-selectivity top-K.
  - Skill beyond momentum = WR15(score>=75) vs the MOMENTUM-MATCHED base rate
                            (the control reweighted to the 75+ momentum mix).
"""
import os
import sys
import math
from collections import namedtuple

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
START_YEAR, END_YEAR = 2016, 2027


def _build():
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, pct_from_ema50, pct_from_ema200 "
        "FROM scores WHERE version_id={vid} AND overall IS NOT NULL "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=START_YEAR, end_year=END_YEAR, vid=VID, timeout_ms=300_000,
    )
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "pct50":  [float(r[3]) if r[3] is not None else float("nan") for r in rows],
            "pct200": [float(r[4]) if r[4] is not None else float("nan") for r in rows],
        }
    )


def two_prop_z(x1, n1, x2, n2):
    if n1 == 0 or n2 == 0:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def main():
    from datetime import date as _date
    parquet = materialize_polars(f"skill_v{VID}_allscores_{START_YEAR}", _build)
    df = pl.read_parquet(parquet)
    universe_n = df.height
    print(f"\nv{VID} ALL-score rows (2016+): {universe_n:,}", flush=True)
    # peaks_to_swing_results goes non-linear past ~few-hundred-k peaks; uniform-sample.
    # A uniform sample is unbiased for climatology / momentum deciles / stratified skill.
    SAMPLE_N = int(os.environ.get("SAMPLE_N", "300000"))
    if universe_n > SAMPLE_N:
        df = df.sample(n=SAMPLE_N, seed=17)
        print(f"uniform sample for barrier join: {df.height:,} of {universe_n:,} "
              f"({df.height/universe_n*100:.1f}%)", flush=True)

    # ---- call-side WR15 for EVERY stock-day (force side='low' via overall=75) ----
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    print(f"barrier join over {len(peaks):,} stock-days (forced call-side)...", flush=True)
    results, skipped = peaks_to_swing_results(peaks, verbose=False)
    win = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("15d")
        if sw and sw.get("result") in ("win", "stop", "expire"):
            win[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
    df = df.with_columns(
        win15=pl.struct(["symbol", "date"]).map_elements(
            lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64
        )
    )
    matched = (df["win15"] >= 0).sum()
    print(f"call-side barrier match: {matched:,}/{df.height:,} ({matched/df.height*100:.1f}%) "
          f"[{skipped:,} uncached]")
    df = df.filter(pl.col("win15") >= 0)
    mom_cov = df["pct50"].is_not_nan().sum()
    print(f"pct_from_ema50 coverage: {mom_cov:,}/{df.height:,} ({mom_cov/df.height*100:.1f}%)\n", flush=True)

    sc = df["overall"].to_numpy()
    mom = df["pct50"].to_numpy()
    w = df["win15"].to_numpy().astype(float)

    # ===================== 1. CLIMATOLOGY =====================
    clim_wr = w.mean() * 100
    clim_n = len(w)
    clim_x = int(w.sum())
    print("=== 1. CLIMATOLOGY (buy a call on a random stock-day, generic up-barrier) ===")
    print(f"  unconditional call WR15 = {clim_wr:.2f}%   (N={clim_n:,})")
    print("  ^ this is the base rate the score MUST beat to have any skill.\n")

    # ===================== 2. SCORE SKILL vs CLIMATOLOGY =====================
    print("=== 2. SCORE DISCRIMINATION (WR15 by cumulative bucket vs climatology) ===")
    print(f"{'bucket':>8} | {'N':>7} | {'WR15':>7} | {'skill vs clim':>13} | {'z':>6}")
    for thr in (70, 75, 80, 85, 90):
        m = sc >= thr
        if m.sum() == 0:
            continue
        bx, bn = int(w[m].sum()), int(m.sum())
        bwr = bx / bn * 100
        z = two_prop_z(bx, bn, clim_x, clim_n)
        print(f"{('>='+str(thr)):>8} | {bn:>7,} | {bwr:6.2f}% | {bwr-clim_wr:+12.2f} | {z:+5.2f}")
    print()

    # ===================== 3. MOMENTUM baseline (pct_from_ema50) =====================
    mm = ~np.isnan(mom)
    if mm.sum() > 1000:
        print("=== 3. MOMENTUM baseline: WR15 by pct_from_ema50 decile (does trend-extension alone predict?) ===")
        mvals, wvals, svals = mom[mm], w[mm], sc[mm]
        deciles = np.quantile(mvals, np.linspace(0, 1, 11))
        print(f"{'decile':>7} | {'pct50 range':>18} | {'N':>7} | {'WR15':>7}")
        for i in range(10):
            lo, hi = deciles[i], deciles[i + 1]
            dm = (mvals >= lo) & (mvals <= hi) if i == 9 else (mvals >= lo) & (mvals < hi)
            if dm.sum() == 0:
                continue
            print(f"{i+1:>7} | {lo:>8.1f}..{hi:>7.1f} | {dm.sum():>7,} | {wvals[dm].mean()*100:6.2f}%")
        print()

        # ---- matched-selectivity: top-K by momentum vs score>=75 (same # of picks) ----
        K = int((svals >= 75).sum())
        sel = K / len(svals)
        thr_mom = np.quantile(mvals, 1 - sel)
        top_mom = mvals >= thr_mom
        s75 = svals >= 75
        mwr, mwn, mwx = wvals[top_mom].mean() * 100, int(top_mom.sum()), int(wvals[top_mom].sum())
        swr, swn, swx = wvals[s75].mean() * 100, int(s75.sum()), int(wvals[s75].sum())
        z = two_prop_z(swx, swn, mwx, mwn)
        print("=== 4. MATCHED SELECTIVITY: score>=75 vs top-momentum (same # picks) ===")
        print(f"  score>=75      : WR15 {swr:.2f}%  (N={swn:,})")
        print(f"  top-{sel*100:.1f}% momentum: WR15 {mwr:.2f}%  (N={mwn:,})")
        print(f"  score - momentum = {swr-mwr:+.2f}pp   z={z:+.2f}")
        print("  ^ if ~0, the score is a momentum repackaging at this selectivity.\n")

        # ---- 5. SKILL BEYOND MOMENTUM: 75+ vs momentum-MATCHED control ----
        print("=== 5. SKILL BEYOND MOMENTUM (score>=75 vs the momentum-matched base rate) ===")
        ctrl = svals < 70  # the non-signal control pool
        exp_x = 0.0          # expected wins if 75+ were just its momentum mix
        var = 0.0
        act_x, act_n = int(wvals[s75].sum()), int(s75.sum())
        print(f"{'decile':>7} | {'N(75+)':>7} | {'WR 75+':>7} | {'WR ctrl<70':>10} | {'lift':>6}")
        for i in range(10):
            lo, hi = deciles[i], deciles[i + 1]
            dm = (mvals >= lo) & (mvals <= hi) if i == 9 else (mvals >= lo) & (mvals < hi)
            hi_m, lo_m = dm & s75, dm & ctrl
            n_hi, n_lo = int(hi_m.sum()), int(lo_m.sum())
            if n_hi == 0 or n_lo == 0:
                continue
            wr_hi, wr_lo = wvals[hi_m].mean(), wvals[lo_m].mean()
            exp_x += n_hi * wr_lo
            var += n_hi * wr_lo * (1 - wr_lo)  # binomial var of the matched expectation
            print(f"{i+1:>7} | {n_hi:>7,} | {wr_hi*100:6.2f}% | {wr_lo*100:9.2f}% | {(wr_hi-wr_lo)*100:+5.1f}")
        exp_wr = exp_x / act_n * 100
        act_wr = act_x / act_n * 100
        z_beyond = (act_x - exp_x) / math.sqrt(var) if var > 0 else float("nan")
        print(f"  ACTUAL 75+ WR15            = {act_wr:.2f}%  (N={act_n:,})")
        print(f"  momentum-MATCHED base rate = {exp_wr:.2f}%   (what 75+ would get from its momentum mix alone)")
        print(f"  SKILL BEYOND MOMENTUM      = {act_wr-exp_wr:+.2f}pp   z={z_beyond:+.2f}")
        print("  ^ this is the number that decides 'real discrimination' vs 'leveraged-momentum sleeve'.\n")
    else:
        print("=== 3-5 SKIPPED: pct_from_ema50 coverage too low; momentum baseline needs PriceHistory pull (round 2).\n")

    print("NOTE: generic K=2sigma/M=5sigma up-barrier (round 1 metric, matches assess). "
          "Momentum proxy = pct_from_ema50 (stored; a true MTUM trailing-return baseline is round 2). "
          "Holdout cutoff 2026-06-15 > data end 2026-06-12 -> fully in-sample.")


if __name__ == "__main__":
    main()
