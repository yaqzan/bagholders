"""
Validate the regime-misclassification hypothesis surfaced by the 2023 density
diagnostic.

Claim: the MarketRegime composite labels narrow-breadth bull tapes as STRESS
(e.g. 2023) and bear tapes as BULL (e.g. 2022) because breadth drags the
composite away from the objective market state.

Test: for each trading day in 2021..2025, compute "true market state" from
unambiguous cheap signals:
  - SPY > EMA200 (yes = uptrend)
  - VIX < 20 (yes = calm)
  - SPY_10d_ret > 0 (yes = recent momentum)

Cross-tab against regime_multiplier classification bands. The misclassification
pocket is:
  - OBJECTIVELY BULLISH DAYS (all three yes) that are classified STRESS/CAUTION
  - OBJECTIVELY BEARISH DAYS (all three no) that are classified HEALTHY/BULL

Report: per year, % of days in each cross-tab cell, and the TRUE ALPHA IMPACT —
how many call/put signals fired on misclassified days, and what their barrier-
touch WR was.

If the pattern holds, we can design a targeted override.
"""
from __future__ import annotations
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _classify_day(vix, spy, ema50, ema200, spy_10d_ret):
    """Return ('bull_obj' | 'bear_obj' | 'mixed', labels dict) based on simple signals."""
    if spy is None or ema200 is None or vix is None:
        return 'unknown', {}

    spy_up = spy > ema200
    spy_50up = ema50 is not None and spy > ema50
    vix_low = vix < 20
    mom_up = spy_10d_ret is not None and spy_10d_ret > 0

    # Three-signal agreement
    signals = [spy_up, vix_low, mom_up]
    bull_count = sum(signals)

    labels = {
        'spy_above_ema200': spy_up,
        'spy_above_ema50': spy_50up,
        'vix_low_20': vix_low,
        'spy_10d_up': mom_up,
        'bull_count': bull_count,
    }
    if bull_count == 3:
        return 'bull_obj', labels
    elif bull_count == 0:
        return 'bear_obj', labels
    else:
        return 'mixed', labels


def _regime_band(mult):
    if mult is None:
        return 'unknown'
    if mult < 0.80:
        return 'STRESS'
    if mult < 0.88:
        return 'CAUT-'
    if mult < 1.00:
        return 'CAUT'
    if mult < 1.05:
        return 'HEALTHY'
    return 'BULL'


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    print(f"Active version: v{av.id} ({av.git_commit[:8]})\n")

    years = [2021, 2022, 2023, 2024, 2025]
    bands = ['STRESS', 'CAUT-', 'CAUT', 'HEALTHY', 'BULL']

    # Pull regime rows with SPY/EMA + VIX + SPY 10d pct change
    # We don't have spy_10d_ret directly, so compute it from spy_close history.

    # First, get all SPY closes to compute 10d returns per regime date
    spy_rows = DB.execute_sql(f"""
        SELECT date, spy_close FROM market_regime
        WHERE date >= '{years[0]}-01-01' AND date < '{years[-1]+1}-01-01'
          AND spy_close IS NOT NULL
        ORDER BY date
    """).fetchall()
    spy_dates = [r[0] for r in spy_rows]
    spy_closes = [float(r[1]) for r in spy_rows]
    d_idx = {d: i for i, d in enumerate(spy_dates)}

    def spy_10d_ret(d):
        i = d_idx.get(d)
        if i is None or i < 10:
            return None
        prev = spy_closes[i - 10]
        cur = spy_closes[i]
        return (cur / prev - 1.0) * 100

    # Main regime row fetch with all fields
    regime_q = f"""
        SELECT date, vix_close, spy_close, spy_ema50, spy_ema200,
               regime_composite, regime_multiplier,
               vix_score, market_trend_score, internal_breadth_score
        FROM market_regime
        WHERE date >= '{years[0]}-01-01' AND date < '{years[-1]+1}-01-01'
          AND regime_multiplier IS NOT NULL
        ORDER BY date
    """
    rows = DB.execute_sql(regime_q).fetchall()

    # --- STAGE 1: Cross-tab objective state vs regime classification per year ---
    print("=" * 115)
    print("STAGE 1: OBJECTIVE MARKET STATE vs REGIME CLASSIFICATION PER YEAR")
    print("=" * 115)
    print("Objective state: SPY>EMA200, VIX<20, SPY_10d>0 (all 3 = bull_obj; none = bear_obj; else mixed)")
    print()

    for yr in years:
        print(f"\n--- {yr} ---")
        yr_rows = [r for r in rows if r[0].year == yr]
        if not yr_rows:
            print("  (no data)")
            continue

        # count by (objective state, regime band)
        crosstab = defaultdict(int)
        obj_counts = defaultdict(int)
        band_counts = defaultdict(int)
        total = 0

        mismatches = []  # (date, obj, band, reason) for printing later

        for r in yr_rows:
            d, vix, spy, ema50, ema200, comp, mult, vsc, msc, bsc = r
            vix = float(vix) if vix is not None else None
            spy = float(spy) if spy is not None else None
            ema50 = float(ema50) if ema50 is not None else None
            ema200 = float(ema200) if ema200 is not None else None
            mult = float(mult)
            mom = spy_10d_ret(d)
            obj, _ = _classify_day(vix, spy, ema50, ema200, mom)
            band = _regime_band(mult)

            crosstab[(obj, band)] += 1
            obj_counts[obj] += 1
            band_counts[band] += 1
            total += 1

            if obj == 'bull_obj' and band in ('STRESS', 'CAUT-'):
                mismatches.append(('BULL_MISLABELED_STRESS', d, vix, spy, ema200, mom, mult, band))
            elif obj == 'bear_obj' and band in ('HEALTHY', 'BULL'):
                mismatches.append(('BEAR_MISLABELED_HEALTHY', d, vix, spy, ema200, mom, mult, band))

        # Print cross-tab
        header_label = 'obj \\ band'
        print(f"{header_label:<12}  " + "  ".join(f"{b:>8}" for b in bands) + f"   {'row_total':>10}")
        for obj in ['bull_obj', 'mixed', 'bear_obj']:
            cells = []
            for b in bands:
                c = crosstab.get((obj, b), 0)
                cells.append(f"{c:>8}")
            row_total = obj_counts.get(obj, 0)
            print(f"{obj:<12}  " + "  ".join(cells) + f"   {row_total:>10}")
        col_totals = '  '.join(f"{band_counts.get(b, 0):>8}" for b in bands)
        print(f"{'col_total':<12}  {col_totals}   {total:>10}")

        # Mismatch counts
        bull_mislabeled = sum(1 for m in mismatches if m[0] == 'BULL_MISLABELED_STRESS')
        bear_mislabeled = sum(1 for m in mismatches if m[0] == 'BEAR_MISLABELED_HEALTHY')
        print(f"\n  Bull-objective days labeled STRESS or CAUT-: {bull_mislabeled} ({bull_mislabeled/total*100:.1f}%)")
        print(f"  Bear-objective days labeled HEALTHY or BULL: {bear_mislabeled} ({bear_mislabeled/total*100:.1f}%)")

    # --- STAGE 2: For 2023 specifically, show the misclassified-STRESS days' call/put signal load ---
    print("\n" + "=" * 115)
    print("STAGE 2: 2023 MISCLASSIFIED-STRESS DAYS — signal load and regime impact")
    print("=" * 115)

    mismatch_dates_2023 = []
    for r in rows:
        d, vix, spy, ema50, ema200, comp, mult, vsc, msc, bsc = r
        if d.year != 2023:
            continue
        vix = float(vix) if vix is not None else None
        spy = float(spy) if spy is not None else None
        ema50 = float(ema50) if ema50 is not None else None
        ema200 = float(ema200) if ema200 is not None else None
        mult = float(mult)
        mom = spy_10d_ret(d)
        obj, lbl = _classify_day(vix, spy, ema50, ema200, mom)
        band = _regime_band(mult)
        if obj == 'bull_obj' and band in ('STRESS', 'CAUT-'):
            mismatch_dates_2023.append(d)

    print(f"\n2023 has {len(mismatch_dates_2023)} BULL-objective days classified STRESS or CAUT-")

    if mismatch_dates_2023:
        # Count signals on those days vs rest of 2023
        d_list = ','.join(f"'{d.isoformat()}'" for d in mismatch_dates_2023)
        q_mis = f"""
            SELECT
              SUM(overall >= 70), SUM(overall >= 75), SUM(overall >= 80),
              SUM(overall <= 25), SUM(overall <= 15)
            FROM scores
            WHERE version_id = {vid}
              AND date IN ({d_list})
        """
        c70, c75, c80, p25, p15 = DB.execute_sql(q_mis).fetchone()
        q_all = f"""
            SELECT
              SUM(overall >= 70), SUM(overall >= 75), SUM(overall >= 80),
              SUM(overall <= 25), SUM(overall <= 15),
              COUNT(DISTINCT date)
            FROM scores
            WHERE version_id = {vid}
              AND date >= '2023-01-01' AND date < '2024-01-01'
        """
        ac70, ac75, ac80, ap25, ap15, tot_days = DB.execute_sql(q_all).fetchone()

        print(f"\n{'metric':<16} {'mismatch_days':>14} {'all_2023':>10} {'mis_frac':>10}")
        mis_days = len(mismatch_dates_2023)
        print(f"{'days':<16} {mis_days:>14} {tot_days:>10} {mis_days/tot_days*100:>9.1f}%")
        print(f"{'calls>=70':<16} {c70:>14} {ac70:>10} {c70/ac70*100 if ac70 else 0:>9.1f}%")
        print(f"{'calls>=75':<16} {c75:>14} {ac75:>10} {c75/ac75*100 if ac75 else 0:>9.1f}%")
        print(f"{'calls>=80':<16} {c80:>14} {ac80:>10} {c80/ac80*100 if ac80 else 0:>9.1f}%")
        print(f"{'puts<=25':<16} {p25:>14} {ap25:>10} {p25/ap25*100 if ap25 else 0:>9.1f}%")
        print(f"{'puts<=15':<16} {p15:>14} {ap15:>10} {p15/ap15*100 if ap15 else 0:>9.1f}%")

    # --- STAGE 3: sample specific misclassified dates with context ---
    print("\n" + "=" * 115)
    print("STAGE 3: SAMPLE MISCLASSIFIED DATES (2022 BEAR-MISLABELED-BULL + 2023 BULL-MISLABELED-STRESS)")
    print("=" * 115)

    def _print_samples(year, which, limit=8):
        print(f"\n{year} — {which} samples (first {limit}):")
        print(f"{'date':<12} {'VIX':>6} {'SPY':>9} {'EMA50':>9} {'EMA200':>9} {'SPY_10d':>8} "
              f"{'mult':>6} {'band':>8} {'composite':>10} {'vix_sc':>7} {'trend_sc':>9} {'brd_sc':>7}")
        count = 0
        for r in rows:
            d, vix, spy, ema50, ema200, comp, mult, vsc, msc, bsc = r
            if d.year != year or count >= limit:
                continue
            vix_f = float(vix) if vix is not None else None
            spy_f = float(spy) if spy is not None else None
            ema50_f = float(ema50) if ema50 is not None else None
            ema200_f = float(ema200) if ema200 is not None else None
            mult_f = float(mult)
            mom = spy_10d_ret(d)
            obj, lbl = _classify_day(vix_f, spy_f, ema50_f, ema200_f, mom)
            band = _regime_band(mult_f)

            if which == 'BULL-MIS-STRESS':
                hit = obj == 'bull_obj' and band in ('STRESS', 'CAUT-')
            elif which == 'BEAR-MIS-BULL':
                hit = obj == 'bear_obj' and band in ('HEALTHY', 'BULL')
            else:
                hit = False

            if hit:
                count += 1
                vs = float(vsc) if vsc is not None else 0
                ms = float(msc) if msc is not None else 0
                bs = float(bsc) if bsc is not None else 0
                print(f"{d.isoformat():<12} {vix_f:>6.2f} {spy_f:>9.2f} {ema50_f:>9.2f} {ema200_f:>9.2f} "
                      f"{mom if mom is not None else 0:>+7.2f}% {mult_f:>6.3f} {band:>8} "
                      f"{float(comp) if comp is not None else 0:>10.2f} {vs:>7.2f} {ms:>9.2f} {bs:>7.2f}")

    _print_samples(2022, 'BEAR-MIS-BULL', 10)
    _print_samples(2023, 'BULL-MIS-STRESS', 10)

    # --- STAGE 4: What drives the composite low in 2023? decompose by sub-score ---
    print("\n" + "=" * 115)
    print("STAGE 4: SUB-SCORE DECOMPOSITION PER YEAR (what drags the composite?)")
    print("=" * 115)
    print(f"{'year':<6} {'N':>6} {'composite':>11} {'vix_score':>11} {'trend_score':>12} {'breadth_score':>14} "
          f"{'vix_pop':>8} {'trend_pop':>10} {'brd_pop':>8}  (pop = % of days where score was populated)")
    for yr in years:
        yr_rows = [r for r in rows if r[0].year == yr]
        if not yr_rows:
            continue
        comps = [float(r[5]) for r in yr_rows if r[5] is not None]
        vsc = [float(r[7]) for r in yr_rows if r[7] is not None]
        msc = [float(r[8]) for r in yr_rows if r[8] is not None]
        bsc = [float(r[9]) for r in yr_rows if r[9] is not None]
        total = len(yr_rows)
        print(f"{yr:<6} {total:>6} {np.mean(comps) if comps else 0:>11.2f} "
              f"{np.mean(vsc) if vsc else 0:>11.2f} {np.mean(msc) if msc else 0:>12.2f} "
              f"{np.mean(bsc) if bsc else 0:>14.2f}  "
              f"{len(vsc)/total*100:>7.1f}% {len(msc)/total*100:>9.1f}% {len(bsc)/total*100:>7.1f}%")


if __name__ == '__main__':
    main()
