"""
Priority #6: 2023 signal density anomaly.

On v21, 2023 has CALL-side suppression (calls>=80: 129 vs ~400 in 2022/2024)
and PUT-side inflation (puts<=25: 14,528 vs 10-13k elsewhere). The overall
Call:Put ratio (0.17) is 3x lower than the nearest other year.

This diagnostic decomposes where in the scoring chain the suppression happens:

  pre_weekly   (components + TA score)
       |
       v  + weekly_adj
  pre_regime   (post-weekly, post-volume)
       |
       v  * regime_mult (symmetric around 50)
  overall

By looking at per-year distributions at each stage, we can tell whether:
  - Components are genuinely less bullish in 2023 (market structural)
  - Weekly is pulling calls down harder in 2023 (dampening artifact)
  - Regime is compressing extremes in 2023 (multiplier effect)

Also runs:
  - Near-miss analysis (signals in 60-75 / 25-30 by year)
  - Stock overlap: stocks that scored 70+ in 2022/2024, did they in 2023?
  - Market context: SPY vs universe breadth in 2023
  - Volatility context: mean realized vol by year
"""
from __future__ import annotations
import json
import numpy as np
from datetime import date, timedelta
from collections import Counter, defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _pct(arr, p):
    if not arr:
        return 0.0
    return float(np.percentile(np.asarray(arr, dtype=float), p))


def _summary(arr, fmt='.1f'):
    if not arr:
        return "N=0"
    a = np.asarray(arr, dtype=float)
    return (f"N={len(a)} mean={a.mean():{fmt}} median={np.median(a):{fmt}} "
            f"p10={np.percentile(a,10):{fmt}} p90={np.percentile(a,90):{fmt}}")


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    print(f"Active version: v{av.id} ({av.git_commit[:8]})\n")

    years = [2021, 2022, 2023, 2024, 2025]

    # =========================================================================
    # STAGE 1: Signal density at each qualifying threshold per year
    # =========================================================================
    print("=" * 110)
    print("STAGE 1: QUALIFYING SIGNAL DENSITY (per year)")
    print("=" * 110)
    print(f"{'year':<6} {'rows':>8} {'days':>5} {'c>=70':>7} {'c>=75':>7} {'c>=80':>6} "
          f"{'c>=85':>6} {'c>=90':>6} {'p<=25':>7} {'p<=20':>7} {'p<=15':>6} "
          f"{'p<=10':>6} {'p<=5':>5}")
    for yr in years:
        q = f"""
            SELECT
              SUM(overall >= 70), SUM(overall >= 75),
              SUM(overall >= 80), SUM(overall >= 85), SUM(overall >= 90),
              SUM(overall <= 25), SUM(overall <= 20), SUM(overall <= 15),
              SUM(overall <= 10), SUM(overall <= 5),
              COUNT(DISTINCT date), COUNT(*)
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
        """
        row = DB.execute_sql(q).fetchone()
        c70, c75, c80, c85, c90, p25, p20, p15, p10, p5, nd, nr = row
        print(f"{yr:<6} {nr:>8} {nd:>5} {c70:>7} {c75:>7} {c80:>6} {c85:>6} {c90:>6} "
              f"{p25:>7} {p20:>7} {p15:>6} {p10:>6} {p5:>5}")

    # =========================================================================
    # STAGE 2: Score distribution at each stage of scoring pipeline per year
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 2: SCORE CHAIN DISTRIBUTION (pre_weekly -> pre_regime -> overall)")
    print("=" * 110)

    for yr in years:
        q = f"""
            SELECT overall, weight_info, regime_multiplier
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
              AND weight_info IS NOT NULL
        """
        rows = DB.execute_sql(q).fetchall()

        pre_weekly = []
        pre_regime = []
        weekly_adj = []
        overall_list = []
        regime_mult_list = []

        for overall, winfo_str, rmult in rows:
            overall_list.append(float(overall))
            if rmult is not None:
                regime_mult_list.append(float(rmult))
            try:
                wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                pr = wi.get('pre_regime')
                wadj = wi.get('w_adj') or wi.get('weekly_adj') or 0
                if pr is not None:
                    pre_regime.append(float(pr))
                    pre_weekly.append(float(pr) - float(wadj))
                    weekly_adj.append(float(wadj))
            except Exception:
                pass

        print(f"\n--- {yr} (n={len(rows):,}) ---")
        print(f"  pre_weekly:    {_summary(pre_weekly)}")
        print(f"  weekly_adj:    {_summary(weekly_adj, '+.2f')}")
        print(f"  pre_regime:    {_summary(pre_regime)}")
        print(f"  regime_mult:   {_summary(regime_mult_list, '.3f')}")
        print(f"  overall:       {_summary(overall_list)}")

    # =========================================================================
    # STAGE 3: Count signals qualifying at each stage per year
    # (e.g. how many would qualify 70+ at pre_weekly? at pre_regime? at overall?)
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 3: SIGNAL COUNT AT EACH STAGE OF PIPELINE (qualifying thresholds)")
    print("=" * 110)
    print(f"{'year':<6} | {'>=70 pre_weekly':>16} {'>=70 pre_regime':>16} {'>=70 overall':>13}"
          f" | {'<=25 pre_weekly':>16} {'<=25 pre_regime':>16} {'<=25 overall':>13}")
    for yr in years:
        q = f"""
            SELECT overall, weight_info
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
              AND weight_info IS NOT NULL
        """
        rows = DB.execute_sql(q).fetchall()

        c_pw70 = c_pr70 = c_ov70 = 0
        p_pw25 = p_pr25 = p_ov25 = 0

        for overall, winfo_str in rows:
            try:
                wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                pr = wi.get('pre_regime')
                wadj = wi.get('w_adj') or wi.get('weekly_adj') or 0
                if pr is None:
                    continue
                pw = float(pr) - float(wadj)
                pr = float(pr)
            except Exception:
                continue

            if pw >= 70: c_pw70 += 1
            if pr >= 70: c_pr70 += 1
            if overall >= 70: c_ov70 += 1
            if pw <= 25: p_pw25 += 1
            if pr <= 25: p_pr25 += 1
            if overall <= 25: p_ov25 += 1

        print(f"{yr:<6} | {c_pw70:>16} {c_pr70:>16} {c_ov70:>13}"
              f" | {p_pw25:>16} {p_pr25:>16} {p_ov25:>13}")

    # =========================================================================
    # STAGE 4: Regime distribution per year
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 4: REGIME DISTRIBUTION (% of days per regime band per year)")
    print("=" * 110)

    bands = [
        (0.70, 0.80, 'STRESS'),
        (0.80, 0.88, 'CAUT-'),
        (0.88, 1.00, 'CAUT'),
        (1.00, 1.05, 'HEALTHY'),
        (1.05, 1.12, 'BULL'),
    ]
    print(f"{'year':<6} " + "  ".join(f"{lbl:>10}" for _, _, lbl in bands) +
          f"   {'mean_mult':>10}  {'mean_vix':>10}  {'mean_brd':>10}")
    for yr in years:
        q = f"""
            SELECT regime_multiplier, vix_close, internal_breadth_score
            FROM market_regime
            WHERE date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
              AND regime_multiplier IS NOT NULL
        """
        rows = DB.execute_sql(q).fetchall()
        total = len(rows)
        if total == 0:
            print(f"{yr:<6}  (no regime data)")
            continue
        counts = [0] * len(bands)
        for r in rows:
            m = float(r[0])
            for i, (lo, hi, _) in enumerate(bands):
                if lo <= m < hi:
                    counts[i] += 1
                    break
        mults = [float(r[0]) for r in rows]
        vixes = [float(r[1]) for r in rows if r[1] is not None]
        brds = [float(r[2]) for r in rows if r[2] is not None]
        cell_parts = [f"{c/total*100:>9.1f}%" for c in counts]
        mm = np.mean(mults)
        mv = np.mean(vixes) if vixes else 0.0
        mb = np.mean(brds) if brds else 0.0
        print(f"{yr:<6} " + "  ".join(cell_parts) + f"   {mm:>10.3f}  {mv:>10.2f}  {mb:>10.2f}")

    # =========================================================================
    # STAGE 5: Raw component distribution per year
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 5: RAW COMPONENT DISTRIBUTION (bullish-lean breakdown per year)")
    print("=" * 110)
    print(f"{'year':<6} {'trend_mean':>11} {'bb_mean':>9} {'rsi_mean':>9} "
          f"{'macd_mean':>11} {'stoch_mean':>11} {'trend>=70':>10} {'trend<=30':>10} "
          f"{'rsi>=70':>9} {'rsi<=30':>9}")
    for yr in years:
        q = f"""
            SELECT
              AVG(trend), AVG(bb), AVG(rsi), AVG(macd), AVG(stoch),
              SUM(trend >= 70), SUM(trend <= 30),
              SUM(rsi >= 70), SUM(rsi <= 30),
              COUNT(*)
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
        """
        tr, bb, rs, mc, st, t70, t30, r70, r30, n = DB.execute_sql(q).fetchone()
        print(f"{yr:<6} {float(tr):>11.1f} {float(bb):>9.1f} {float(rs):>9.1f} "
              f"{float(mc):>11.1f} {float(st):>11.1f} {t70/n*100:>9.1f}% {t30/n*100:>9.1f}% "
              f"{r70/n*100:>8.1f}% {r30/n*100:>8.1f}%")

    # =========================================================================
    # STAGE 6: Stock overlap analysis
    # Of stocks scoring >=80 in 2022, how many also scored >=80 in 2023?
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 6: STOCK OVERLAP — stocks that scored >=80 (any day) in year X and year Y")
    print("=" * 110)

    syms_by_year = {}
    for yr in years:
        q = f"""
            SELECT DISTINCT symbol
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
              AND overall >= 80
        """
        syms_by_year[yr] = set(r[0] for r in DB.execute_sql(q).fetchall())

    print("Call-side (>=80) symbol sets:")
    for yr in years:
        print(f"  {yr}: {len(syms_by_year[yr])} unique symbols")
    print()
    print("Overlap matrix (rows: source year, cols: target year) — % of source symbols that also scored >=80 in target:")
    header = "source_yr  " + "  ".join(f"{yr:>10}" for yr in years)
    print(header)
    for src in years:
        cells = []
        for tgt in years:
            if src == tgt:
                cells.append(f"{'--':>10}")
                continue
            src_set = syms_by_year[src]
            tgt_set = syms_by_year[tgt]
            if not src_set:
                cells.append(f"{'--':>10}")
                continue
            overlap = len(src_set & tgt_set) / len(src_set) * 100
            cells.append(f"{overlap:>9.1f}%")
        print(f"{src:<10} " + "  ".join(cells))

    # =========================================================================
    # STAGE 7: Near-miss analysis — signals just below threshold
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 7: NEAR-MISS ANALYSIS (signals just below qualifying thresholds)")
    print("=" * 110)
    print(f"{'year':<6} {'c_60-69':>8} {'c_65-69':>8} {'c_65-74':>8} "
          f"{'p_26-35':>8} {'p_26-30':>8} {'p_21-30':>8}")
    for yr in years:
        q = f"""
            SELECT
              SUM(overall BETWEEN 60 AND 69),
              SUM(overall BETWEEN 65 AND 69),
              SUM(overall BETWEEN 65 AND 74),
              SUM(overall BETWEEN 26 AND 35),
              SUM(overall BETWEEN 26 AND 30),
              SUM(overall BETWEEN 21 AND 30)
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
        """
        c60, c65, c65_74, p26_35, p26_30, p21_30 = DB.execute_sql(q).fetchone()
        print(f"{yr:<6} {c60:>8} {c65:>8} {c65_74:>8} {p26_35:>8} {p26_30:>8} {p21_30:>8}")

    # =========================================================================
    # STAGE 8: SPY, VIX, breadth context per year
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 8: MARKET CONTEXT")
    print("=" * 110)
    print(f"{'year':<6} {'SPY_start':>10} {'SPY_end':>9} {'SPY_ret':>8} "
          f"{'mean_VIX':>9} {'mean_brd':>9} {'brd_p10':>8} {'brd_p90':>8}")
    for yr in years:
        q = f"""
            SELECT close FROM price_history
            WHERE symbol = 'SPY' AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
            ORDER BY date
        """
        closes = [float(r[0]) for r in DB.execute_sql(q).fetchall()]
        if len(closes) < 2:
            print(f"{yr:<6} (no SPY data)")
            continue
        spy_start = closes[0]
        spy_end = closes[-1]
        spy_ret = (spy_end / spy_start - 1.0) * 100

        q2 = f"""
            SELECT vix_close, internal_breadth_score FROM market_regime
            WHERE date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
        """
        rows = DB.execute_sql(q2).fetchall()
        vixes = [float(r[0]) for r in rows if r[0] is not None]
        brds = [float(r[1]) for r in rows if r[1] is not None]
        mv = np.mean(vixes) if vixes else 0
        mb = np.mean(brds) if brds else 0
        bp10 = np.percentile(brds, 10) if brds else 0
        bp90 = np.percentile(brds, 90) if brds else 0
        print(f"{yr:<6} {spy_start:>10.2f} {spy_end:>9.2f} {spy_ret:>+7.1f}% "
              f"{mv:>9.2f} {mb:>9.2f} {bp10:>8.2f} {bp90:>8.2f}")

    # =========================================================================
    # STAGE 9: Universe size & % positive per day (breadth proxy)
    # How many tickers do we actually score per day each year?
    # =========================================================================
    print("\n" + "=" * 110)
    print("STAGE 9: UNIVERSE SIZE & DAILY DIRECTIONAL BREADTH")
    print("=" * 110)
    print(f"{'year':<6} {'avg_tickers_per_day':>22} {'pct_pos_days':>13}  "
          f"(pct_pos_days = mean % of tickers whose score >=50 on any given day)")
    for yr in years:
        q = f"""
            SELECT date,
                   COUNT(*) AS n_tickers,
                   SUM(overall >= 50) AS n_bullish
            FROM scores
            WHERE version_id = {vid}
              AND date >= '{yr}-01-01' AND date < '{yr+1}-01-01'
            GROUP BY date
        """
        rows = DB.execute_sql(q).fetchall()
        if not rows:
            continue
        avg_tickers = np.mean([r[1] for r in rows])
        pct_bull_per_day = np.mean([r[2] / r[1] * 100 for r in rows])
        print(f"{yr:<6} {avg_tickers:>22.1f} {pct_bull_per_day:>12.1f}%")


if __name__ == '__main__':
    main()
