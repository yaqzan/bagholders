"""
Design D refinement diagnostic — does the 2023 regime misclassification manufacture
false CT-PUTs that contaminate the bucket Design D was trying to lift?

Hypothesis chain:
  1. 2023 has ~140 BULL-objective days labeled STRESS (composite < 30, mult ~0.70-0.78).
  2. STRESS regime applies (2 - mult) ~1.30x to puts (amplifier).
  3. On those days, BORDERLINE-bearish per-stock scores (pre_regime maybe 30-40)
     get pulled into <=25 via the regime amplifier.
  4. Those amplified puts are predominantly counter-trend in form (TREND >= 50)
     because narrow-bull tape implies most stocks have intact uptrends — the few
     that score bearishly do so via per-stock RSI/BB/MACD weakness, not via TREND.
  5. So Design D's CT-PUT bucket on 2023 is contaminated with regime-amplified
     borderline cases that have lower true WR than genuine CT-PUT setups.

Test: split 2023 puts (overall <= 25) by:
  - regime classification (STRESS-mislabel vs correctly-labeled-stress vs neutral/bull)
  - TREND component (>=80 = CT-PUT, <=20 = AL-PUT, middle = ambient)
  - pre_regime score (from weight_info.pre_regime — was the score ALREADY <=25
    before regime amplification, or did regime push it there?)

If the misclass-amplified cohort's WR15 is materially lower than the
already-bearish-pre-regime cohort, that confirms Design D's CT-PUT contamination.

The fix direction: gate regime application on x_conf (or pre_regime distance from 25),
so borderline scores don't get amplified into <=25 via misclassified STRESS regime.
"""
from __future__ import annotations
import json
import math
from collections import defaultdict
from datetime import date

import numpy as np

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _classify_day(vix, spy, ema200, spy_10d_ret):
    if spy is None or ema200 is None or vix is None:
        return 'unknown'
    spy_up = spy > ema200
    vix_low = vix < 20
    mom_up = spy_10d_ret is not None and spy_10d_ret > 0
    bull_count = sum([spy_up, vix_low, mom_up])
    if bull_count == 3:
        return 'bull_obj'
    if bull_count == 0:
        return 'bear_obj'
    return 'mixed'


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


def _peak_to_result_put_multi(rows, idx, K_list=(1.0, 1.5, 2.0), M=2.0, W_cal_days=15, sigma=None):
    """Walk forward once, return verdict per K barrier.

    K=1.0σ matches assess_scores standard (barrier-touch WR15).
    K=1.5σ ~ option TP threshold (1.092σ underlying within 15 trading bars
            -> ~1.06σ scaled within 15 calendar days via sqrt(15/30)).
    K=2.0σ harder threshold for sanity.

    Returns: dict {K: 1|0|None}, plus 'stop_hit_first' tracking via M.
    """
    if sigma is None or sigma <= 0:
        return {K: None for K in K_list}
    entry = float(rows[idx].close)
    scale = math.sqrt(W_cal_days / 30.0)
    targets = {K: entry * (1.0 - K * sigma / 100.0 * scale) for K in K_list}
    stop = entry * (1.0 + M * sigma / 100.0 * scale)
    entry_date = rows[idx].date
    cutoff = entry_date.toordinal() + W_cal_days

    verdicts = {K: None for K in K_list}
    stopped = False
    last_in_window_idx = idx

    for j in range(idx + 1, len(rows)):
        if rows[j].date.toordinal() > cutoff:
            break
        last_in_window_idx = j
        lo = float(rows[j].low)
        hi = float(rows[j].high)
        for K in K_list:
            if verdicts[K] is None:
                if lo <= targets[K]:
                    verdicts[K] = 1
                elif (not stopped) and hi >= stop:
                    # Stop on the harder M.  Keep checking targets in case same-bar lo also hit.
                    pass
        if hi >= stop:
            stopped = True
            for K in K_list:
                if verdicts[K] is None:
                    verdicts[K] = 0
            break

    # If window expired without target or stop, mark as 0 (expire = no win)
    if last_in_window_idx >= idx + 1 and rows[last_in_window_idx].date.toordinal() > cutoff - 1:
        for K in K_list:
            if verdicts[K] is None:
                verdicts[K] = 0
    return verdicts


def _peak_to_result_put(rows, idx, K=1.0, M=2.0, W_cal_days=15, sigma=None):
    return _peak_to_result_put_multi(rows, idx, K_list=(K,), M=M, W_cal_days=W_cal_days, sigma=sigma)[K]


def _peak_to_result_call(rows, idx, K=2.0, M=5.0, W_cal_days=15, sigma=None):
    if sigma is None or sigma <= 0:
        return None
    entry = float(rows[idx].close)
    scale = math.sqrt(W_cal_days / 30.0)
    target = entry * (1.0 + K * sigma / 100.0 * scale)
    stop = entry * (1.0 - M * sigma / 100.0 * scale)
    entry_date = rows[idx].date
    cutoff = entry_date.toordinal() + W_cal_days
    last_idx = idx
    for j in range(idx + 1, len(rows)):
        if rows[j].date.toordinal() > cutoff:
            break
        last_idx = j
        lo = float(rows[j].low)
        hi = float(rows[j].high)
        if hi >= target:
            return 1
        if lo <= stop:
            return 0
    if last_idx >= idx + 1 and rows[last_idx].date.toordinal() > cutoff - 1:
        return 0
    return None


def _vol_pct(rows, idx, lookback=60):
    start = max(0, idx - lookback)
    closes = [float(r.close) for r in rows[start:idx + 1] if r.close]
    if len(closes) < 20:
        return None
    rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
    if not rets:
        return None
    return float(np.std(rets, ddof=0)) * 100.0


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    print(f"Active version: v{av.id} ({av.git_commit[:8]})")

    # ── Step 1: Build 2023 day → (regime band, objective state) map ──
    spy_rows = DB.execute_sql("""
        SELECT date, spy_close FROM market_regime
        WHERE date >= '2022-12-15' AND date < '2024-01-01'
          AND spy_close IS NOT NULL ORDER BY date
    """).fetchall()
    spy_dates = [r[0] for r in spy_rows]
    spy_closes = [float(r[1]) for r in spy_rows]
    d_idx = {d: i for i, d in enumerate(spy_dates)}

    def spy_10d_ret(d):
        i = d_idx.get(d)
        if i is None or i < 10:
            return None
        return (spy_closes[i] / spy_closes[i - 10] - 1.0) * 100.0

    regime_rows = DB.execute_sql("""
        SELECT date, vix_close, spy_close, spy_ema50, spy_ema200,
               regime_composite, regime_multiplier
        FROM market_regime
        WHERE date >= '2023-01-01' AND date < '2024-01-01'
          AND regime_multiplier IS NOT NULL
        ORDER BY date
    """).fetchall()

    day_class = {}  # date -> (obj_state, band, mult, composite)
    for d, vix, spy, ema50, ema200, comp, mult in regime_rows:
        vix_f = float(vix) if vix is not None else None
        spy_f = float(spy) if spy is not None else None
        ema200_f = float(ema200) if ema200 is not None else None
        mom = spy_10d_ret(d)
        obj = _classify_day(vix_f, spy_f, ema200_f, mom)
        band = _regime_band(float(mult))
        day_class[d] = (obj, band, float(mult), float(comp) if comp is not None else None)

    # ── Step 2: Pull all 2023 puts (overall <= 25) and calls (overall >= 70) ──
    print("\nLoading 2023 scores ...")
    score_rows = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.weight_info
        FROM scores s
        WHERE s.version_id = {vid}
          AND s.date >= '2023-01-01' AND s.date < '2024-01-01'
          AND (s.overall <= 25 OR s.overall >= 70)
    """).fetchall()
    print(f"  loaded {len(score_rows)} signals")

    # ── Step 3: Pull price history for symbols in scope ──
    syms = sorted({r[0] for r in score_rows})
    print(f"  {len(syms)} unique symbols")

    print("Loading price history (Oct 2022 -> Mar 2024 for forward walk) ...")
    sym_list = ','.join(f"'{s}'" for s in syms)
    ph_rows = DB.execute_sql(f"""
        SELECT symbol, date, close, high, low
        FROM price_history
        WHERE symbol IN ({sym_list})
          AND date >= '2022-10-01' AND date < '2024-04-01'
        ORDER BY symbol, date
    """).fetchall()
    ph_by_sym = defaultdict(list)
    from collections import namedtuple
    Row = namedtuple('Row', 'date close high low')
    for sym, d, c, h, l in ph_rows:
        ph_by_sym[sym].append(Row(d, c, h, l))
    print(f"  loaded {len(ph_rows)} price bars")

    # ── Step 4: Walk WR15 per signal, classify by regime/trend/pre_regime ──
    # Track three K barriers in parallel for puts:
    #   K=1.0  - assess_scores standard (barrier-touch WR15)
    #   K=1.5  - option TP threshold proxy (1.092sigma underlying ~ 1.06sigma scaled)
    #   K=2.0  - harder sanity check
    K_LIST = (1.0, 1.5, 2.0)
    cohorts = defaultdict(lambda: {K: [0, 0, 0] for K in K_LIST})  # cohort -> {K: [N, resolved, wins]}
    call_cohorts = defaultdict(lambda: [0, 0, 0])  # standard barrier-touch for calls

    for sym, d, overall, trend, wi_raw in score_rows:
        if d not in day_class:
            continue
        obj, band, mult, composite = day_class[d]
        rows = ph_by_sym.get(sym, [])
        if not rows:
            continue
        idx = next((i for i, r in enumerate(rows) if r.date == d), None)
        if idx is None or idx == 0:
            continue
        sigma = _vol_pct(rows, idx)
        if sigma is None or sigma <= 0:
            continue
        is_put = overall <= 25
        if is_put:
            res_multi = _peak_to_result_put_multi(rows, idx, K_list=K_LIST, sigma=sigma)
        else:
            res = _peak_to_result_call(rows, idx, sigma=sigma)

        # Parse pre_regime
        pre_regime = None
        try:
            wi = json.loads(wi_raw) if wi_raw else {}
            pre_regime = wi.get('pre_regime')
        except Exception:
            pass

        # ── Cohort classification ──
        # For puts: bucket by trend component AND regime misclassification
        # mislabel = bull_obj AND STRESS/CAUT-
        is_mislabel_stress = (obj == 'bull_obj' and band in ('STRESS', 'CAUT-'))
        is_correct_stress = (obj != 'bull_obj' and band in ('STRESS', 'CAUT-'))
        is_neutral = (band in ('CAUT', 'HEALTHY', 'BULL'))

        if is_mislabel_stress:
            r_tag = 'mis_stress'
        elif is_correct_stress:
            r_tag = 'real_stress'
        else:
            r_tag = 'neutral'

        if is_put:
            # CT vs AL by TREND
            if trend is not None and trend >= 80:
                t_tag = 'CT_PUT'  # trend bullish, score bearish — counter-trend
            elif trend is not None and trend <= 20:
                t_tag = 'AL_PUT'  # trend bearish, score bearish — trend-aligned
            else:
                t_tag = 'AMB_PUT'

            # Was the score ALREADY <=25 pre-regime, or did regime amplify it there?
            if pre_regime is not None:
                pre_pulled = (pre_regime > 25 and overall <= 25)  # regime brought it in
                pre_native = (pre_regime <= 25)
            else:
                pre_pulled = False
                pre_native = False

            for K in K_LIST:
                res = res_multi[K]
                cohorts[(t_tag, r_tag, 'all')][K][0] += 1
                if res is not None:
                    cohorts[(t_tag, r_tag, 'all')][K][1] += 1
                    cohorts[(t_tag, r_tag, 'all')][K][2] += res
                if pre_pulled:
                    cohorts[(t_tag, r_tag, 'pulled_in')][K][0] += 1
                    if res is not None:
                        cohorts[(t_tag, r_tag, 'pulled_in')][K][1] += 1
                        cohorts[(t_tag, r_tag, 'pulled_in')][K][2] += res
                elif pre_native:
                    cohorts[(t_tag, r_tag, 'native')][K][0] += 1
                    if res is not None:
                        cohorts[(t_tag, r_tag, 'native')][K][1] += 1
                        cohorts[(t_tag, r_tag, 'native')][K][2] += res
        else:  # call
            if overall < 75:
                continue  # focus on 75+ where allocation is real
            t_tag = 'CALL75+'

            if pre_regime is not None:
                pre_dampened = (pre_regime > overall + 2)
                pre_native = (pre_regime <= overall + 2)
            else:
                pre_dampened = False
                pre_native = False

            call_cohorts[(t_tag, r_tag, 'all')][0] += 1
            if res is not None:
                call_cohorts[(t_tag, r_tag, 'all')][1] += 1
                call_cohorts[(t_tag, r_tag, 'all')][2] += res

            if pre_dampened:
                call_cohorts[(t_tag, r_tag, 'dampened')][0] += 1
                if res is not None:
                    call_cohorts[(t_tag, r_tag, 'dampened')][1] += 1
                    call_cohorts[(t_tag, r_tag, 'dampened')][2] += res

    # ── Step 5: Print results ──
    def _fmt(cell):
        N, R, W = cell
        wr = (W / R * 100) if R > 0 else 0.0
        return f"N={N:5d} WR={wr:5.1f}%"

    def _wr(cell):
        N, R, W = cell
        return (W / R * 100) if R > 0 else 0.0

    print("\n" + "=" * 110)
    print("2023 PUT SIGNALS — barrier-touch WR15 at three K barriers")
    print("  K=1.0  - assess_scores standard")
    print("  K=1.5  - option TP threshold proxy (1.092sigma underlying)")
    print("  K=2.0  - harder sanity check")
    print("=" * 110)

    for K in K_LIST:
        print(f"\n--- K = {K}sigma ---")
        print(f"\n[TREND x regime cohort x all]")
        print(f"  {'tag':<10} | {'mis_stress':>22} | {'real_stress':>22} | {'neutral':>22}")
        for t_tag in ['CT_PUT', 'AMB_PUT', 'AL_PUT']:
            cells = []
            for r_tag in ['mis_stress', 'real_stress', 'neutral']:
                cells.append(_fmt(cohorts[(t_tag, r_tag, 'all')][K]))
            print(f"  {t_tag:<10} | " + " | ".join(f"{c:>22}" for c in cells))

        print(f"\n[origin split on mis_stress days]")
        print(f"  {'tag':<10} | {'pulled_in':>22} | {'native':>22} | {'delta WR (native-pulled)':>26}")
        for t_tag in ['CT_PUT', 'AMB_PUT', 'AL_PUT']:
            pulled = cohorts[(t_tag, 'mis_stress', 'pulled_in')][K]
            native = cohorts[(t_tag, 'mis_stress', 'native')][K]
            delta = _wr(native) - _wr(pulled)
            print(f"  {t_tag:<10} | {_fmt(pulled):>22} | {_fmt(native):>22} | {delta:+25.1f}pp")

    print("\n" + "=" * 110)
    print("2023 CALL75+ SIGNALS  (standard call barrier K=2.0/M=5.0)")
    print("=" * 110)
    print(f"  {'tag':<28} | {'mis_stress':>22} | {'real_stress':>22} | {'neutral':>22}")
    cells = []
    for r_tag in ['mis_stress', 'real_stress', 'neutral']:
        cells.append(_fmt(call_cohorts[('CALL75+', r_tag, 'all')]))
    print(f"  {'CALL75+ (all)':<28} | " + " | ".join(f"{c:>22}" for c in cells))

    cells = []
    for r_tag in ['mis_stress', 'real_stress', 'neutral']:
        cells.append(_fmt(call_cohorts[('CALL75+', r_tag, 'dampened')]))
    print(f"  {'CALL75+ regime-dampened':<28} | " + " | ".join(f"{c:>22}" for c in cells))

    print("\nDONE.")


if __name__ == '__main__':
    main()
