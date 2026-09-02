"""
Post-hoc rescore: simulate softening regime compression for calls on
mis-stress days using stored v21 score data + weight_info.pre_regime.

No simulator run, no plumbing changes. We just re-derive what overall
WOULD have been under a softer regime compression, then walk WR15 on
the additional calls newly admitted into >=75.

Mis-stress detector (per signal date):
    gap        = max(0, spy_wk_composite - regime_composite)
    mis_stress = min(1.0, gap / MIS_STRESS_FULL)
    # 0 on real stress (composite low, spy_wk also low) -> NO softening
    # 1 on full mis-stress (composite very low, spy_wk bullish) -> FULL softening

Softened mult (calls only, score >= 50):
    eff_mult = 1.0 + (raw_mult - 1.0) * (1 - mis_stress * CALL_DAMPEN)

Sweep CALL_DAMPEN in {0.0, 0.25, 0.50, 0.75, 1.0} across 2022/2023/2024/2025
to validate: clean win on 2023, near-no-op on 2022 (real bear) and bull years.
"""
from __future__ import annotations
import bisect
import json
import math
from collections import defaultdict, namedtuple
from datetime import date

import numpy as np

from database.trader_database import DB
from database.models.core import AlgorithmVersion


MIS_STRESS_FULL = 30.0      # gap (spy_wk - composite) >= 30 -> full mis-stress
DAMPEN_VALUES = [0.0, 0.25, 0.50, 0.75, 1.0]


def _peak_to_result_call(rows, idx, K=2.0, M=5.0, W_cal_days=15, sigma=None):
    if sigma is None or sigma <= 0:
        return None
    entry = float(rows[idx].close)
    scale = math.sqrt(W_cal_days / 30.0)
    target = entry * (1.0 + K * sigma / 100.0 * scale)
    stop = entry * (1.0 - M * sigma / 100.0 * scale)
    cutoff = rows[idx].date.toordinal() + W_cal_days
    last = idx
    for j in range(idx + 1, len(rows)):
        if rows[j].date.toordinal() > cutoff:
            break
        last = j
        hi = float(rows[j].high)
        lo = float(rows[j].low)
        if hi >= target:
            return 1
        if lo <= stop:
            return 0
    if last >= idx + 1 and rows[last].date.toordinal() > cutoff - 1:
        return 0
    return None


def _vol_pct(rows, idx, lookback=60):
    start = max(0, idx - lookback)
    closes = [float(r.close) for r in rows[start:idx + 1] if r.close]
    if len(closes) < 20:
        return None
    rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
    return float(np.std(rets, ddof=0)) * 100.0


def softened_mult(raw_mult, mis_stress, dampen):
    if raw_mult is None:
        return 1.0
    return 1.0 + (raw_mult - 1.0) * (1.0 - mis_stress * dampen)


def new_overall(pre, mult):
    if pre is None or mult is None:
        return None
    if pre >= 50:
        return int(max(0, min(100, round(50 + (pre - 50) * mult))))
    return int(max(0, min(100, round(50 + (pre - 50) * (2.0 - mult)))))


def run_window(start_str, end_str, label, vid, spy_dates, spy_vals):
    print(f"\n{'#' * 110}")
    print(f"# WINDOW: {label}  ({start_str} -> {end_str})")
    print(f"{'#' * 110}")

    # Regime map + extra fields needed for objective-bull detector
    rows = DB.execute_sql(f"""
        SELECT date, regime_composite, regime_multiplier,
               vix_close, spy_close, spy_ema200
        FROM market_regime
        WHERE date >= '{start_str}' AND date < '{end_str}'
          AND regime_multiplier IS NOT NULL
        ORDER BY date
    """).fetchall()
    regime_by_d = {}
    for r in rows:
        d, comp, mult, vix, spy, ema200 = r
        regime_by_d[d] = (
            float(comp) if comp is not None else None,
            float(mult),
            float(vix) if vix is not None else None,
            float(spy) if spy is not None else None,
            float(ema200) if ema200 is not None else None,
        )

    # SPY 10d return per date (for objective-bull momentum gate)
    # Pull SPY closes for full window + 30d preroll
    spy_pre = DB.execute_sql(f"""
        SELECT date, spy_close FROM market_regime
        WHERE date >= '{(int(start_str[:4]) - 1)}-11-01' AND date < '{end_str}'
          AND spy_close IS NOT NULL ORDER BY date
    """).fetchall()
    spy_close_dates = [r[0] for r in spy_pre]
    spy_close_vals = [float(r[1]) for r in spy_pre]
    spy_close_idx = {d: i for i, d in enumerate(spy_close_dates)}

    def spy_10d(d):
        i = spy_close_idx.get(d)
        if i is None or i < 10:
            return None
        return (spy_close_vals[i] / spy_close_vals[i - 10] - 1.0) * 100.0

    def spy_wk(d):
        idx = bisect.bisect_right(spy_dates, d) - 1
        if idx < 0:
            return None
        return spy_vals[spy_dates[idx]]

    mis_stress_by_d = {}
    for d, (comp, mult, vix, spy_c, ema200) in regime_by_d.items():
        if comp is None:
            continue
        s_wk = spy_wk(d)
        if s_wk is None:
            continue
        # Objective-bull gate: SPY > EMA200 AND VIX < 20 AND SPY_10d > 0
        # Requires current-day signals (no weekly lag).  Mis-stress only if all 3 hold.
        if spy_c is None or ema200 is None or vix is None:
            continue
        m10 = spy_10d(d)
        bull_obj = (spy_c > ema200) and (vix < 20) and (m10 is not None and m10 > 0)
        gap = max(0.0, s_wk - comp)
        raw_ms = min(1.0, gap / MIS_STRESS_FULL)
        ms = raw_ms if bull_obj else 0.0  # gate: only fire on objectively bullish days
        mis_stress_by_d[d] = (ms, comp, mult, s_wk)

    full_ms_days = sum(1 for (ms, *_) in mis_stress_by_d.values() if ms >= 0.99)
    partial_ms_days = sum(1 for (ms, *_) in mis_stress_by_d.values() if 0.5 <= ms < 0.99)
    none_ms_days = sum(1 for (ms, *_) in mis_stress_by_d.values() if ms < 0.5)
    print(f"  mis_stress days: full={full_ms_days}  partial={partial_ms_days}  none={none_ms_days}")

    # Load call candidates (overall >= 50, anyone the softener might admit at 75+)
    score_rows = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.weight_info
        FROM scores s
        WHERE s.version_id = {vid}
          AND s.date >= '{start_str}' AND s.date < '{end_str}'
          AND s.overall >= 50
    """).fetchall()

    parsed = []
    syms_in_scope = set()
    for sym, d, overall, wi_raw in score_rows:
        try:
            wi = json.loads(wi_raw) if wi_raw else {}
            pre = wi.get('pre_regime')
            if pre is None:
                continue
        except Exception:
            continue
        if d not in regime_by_d:
            continue
        ms_info = mis_stress_by_d.get(d, (0.0, None, None, None))
        ms, comp, mult, s_wk = ms_info
        parsed.append((sym, d, int(overall), int(pre), ms, mult))
        syms_in_scope.add(sym)
    print(f"  {len(parsed)} call candidates parsed; {len(syms_in_scope)} symbols")

    # Price history
    sym_list_str = ','.join(f"'{s}'" for s in syms_in_scope)
    ph_rows = DB.execute_sql(f"""
        SELECT symbol, date, close, high, low
        FROM price_history
        WHERE symbol IN ({sym_list_str})
          AND date >= '{start_str[:4]}-01-01'
          AND date < '{int(end_str[:4]) + 1}-04-01'
        ORDER BY symbol, date
    """).fetchall()
    Row = namedtuple('Row', 'date close high low')
    ph_by_sym = defaultdict(list)
    for sym, d, c, h, l in ph_rows:
        ph_by_sym[sym].append(Row(d, c, h, l))
    print(f"  {len(ph_rows)} price bars")

    vol_cache = {}

    def get_vol(sym, d):
        key = (sym, d)
        if key in vol_cache:
            return vol_cache[key]
        rows = ph_by_sym.get(sym, [])
        idx = next((i for i, r in enumerate(rows) if r.date == d), None)
        if idx is None or idx == 0:
            vol_cache[key] = (None, None, None)
            return vol_cache[key]
        sigma = _vol_pct(rows, idx)
        vol_cache[key] = (rows, idx, sigma)
        return vol_cache[key]

    def walk_cohort(cohort):
        N = R = W = 0
        for sym, d, *_ in cohort:
            rows, idx, sigma = get_vol(sym, d)
            if sigma is None or sigma <= 0:
                N += 1
                continue
            res = _peak_to_result_call(rows, idx, sigma=sigma)
            N += 1
            if res is not None:
                R += 1
                W += res
        wr = (W / R * 100) if R > 0 else 0.0
        return N, R, W, wr

    # Original 75+ cohort
    orig_cohort = [(sym, d, ms, mult, overall, pre)
                   for sym, d, overall, pre, ms, mult in parsed if overall >= 75]
    n_misstress_orig = sum(1 for c in orig_cohort if c[2] >= 0.5)
    N0, R0, W0, wr0 = walk_cohort(orig_cohort)

    print(f"\n  ORIGINAL 75+:  N={N0}  ms-cohort={n_misstress_orig}  WR15={wr0:.1f}%")
    print(f"  {'CALL_DAMPEN':>11} | {'newly_admitted':>22} | {'on_misstress':>22} | "
          f"{'on_NON-misstress':>22}")

    for dampen in DAMPEN_VALUES:
        newly_all = []
        newly_misstress = []
        newly_neutral = []
        for sym, d, overall, pre, ms, mult in parsed:
            if overall >= 75:
                continue
            if mult is None or pre is None:
                continue
            soft = softened_mult(mult, ms, dampen)
            new_ov = new_overall(pre, soft)
            if new_ov is not None and new_ov >= 75:
                rec = (sym, d, ms, mult, new_ov, pre)
                newly_all.append(rec)
                if ms >= 0.5:
                    newly_misstress.append(rec)
                else:
                    newly_neutral.append(rec)
        Nn, Rn, Wn, wrn = walk_cohort(newly_all)
        Nm, Rm, Wm, wrm = walk_cohort(newly_misstress)
        Nx, Rx, Wx, wrx = walk_cohort(newly_neutral)
        print(f"  {dampen:>11.2f} | "
              f"N+={Nn:4d} WR={wrn:5.1f}% | "
              f"N+={Nm:4d} WR={wrm:5.1f}% | "
              f"N+={Nx:4d} WR={wrx:5.1f}%")


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    print(f"Active v{av.id} ({av.git_commit[:8]})")
    print(f"MIS_STRESS_FULL = {MIS_STRESS_FULL}  (gap >= this -> full softener)")

    spy_rows = DB.execute_sql("""
        SELECT date, composite FROM weekly_scores
        WHERE symbol='SPY' AND composite IS NOT NULL
          AND date >= '2020-11-01' AND date < '2026-05-01'
        ORDER BY date
    """).fetchall()
    spy_dates = [r[0] for r in spy_rows]
    spy_vals = {r[0]: float(r[1]) for r in spy_rows}
    print(f"SPY weekly composite: {len(spy_rows)} rows, "
          f"range {spy_rows[0][0]} -> {spy_rows[-1][0]}")

    for label, s, e in [
        ('2022 (real bear)',          '2022-01-01', '2023-01-01'),
        ('2023 (narrow bull)',        '2023-01-01', '2024-01-01'),
        ('2024 (clean bull)',         '2024-01-01', '2025-01-01'),
        ('2025 (choppy)',             '2025-01-01', '2026-01-01'),
        ('22-now (worst-case start)', '2022-01-01', '2026-04-25'),
    ]:
        run_window(s, e, label, vid, spy_dates, spy_vals)

    print("\nDONE.")


if __name__ == '__main__':
    main()
