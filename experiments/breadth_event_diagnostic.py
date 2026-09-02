"""
Diagnostic 1 + 2 for the breadth-event hypothesis on the A050 2025 85+ regression.

Hypothesis: the A050 85+ regression in 2025 is concentrated on days when Zweig or
Hindenburg breadth events are active. These events are high-information signals
that should NOT be bypassed by conviction dampening.

Diagnostic 1: per-year event-active day counts (Zweig + Hindenburg).
Diagnostic 2: BASE vs A050 WR for 85+ peaks, split by event-active vs event-neutral.

If A050's 85+ WR drop in 2025 concentrates on event-active days, we build
A050_EVGATE (attenuate alpha when events are active).

Event-active definition: |zweig_boost| + |hindenburg_penalty| >= 5 on that date.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _realized_vol(closes):
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _barrier_walk(side, entry, bars, sigma, W):
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    if side == 'low':
        tgt = entry * (1.0 + 2.0 * sigma * scale / 100.0)
        stp = entry * (1.0 - 5.0 * sigma * scale / 100.0)
    else:
        tgt = entry * (1.0 - 1.0 * sigma * scale / 100.0)
        stp = entry * (1.0 + 2.0 * sigma * scale / 100.0)
    for (off, hi, lo) in bars:
        if off > W: return 0
        if side == 'low':
            if hi >= tgt: return 1
            if lo <= stp: return 0
        else:
            if lo <= tgt: return 1
            if hi >= stp: return 0
    return None


def _apply_mult(pre_regime, mult):
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


def _conviction(pre_regime):
    return min(1.0, abs(pre_regime - 50) / 30.0)


def _eff_mult_a050(stored_mult, pre_regime):
    c = _conviction(pre_regime)
    damp = 1.0 - c * 0.50
    return 1.0 + (stored_mult - 1.0) * damp


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}\n")

    # ---- DIAGNOSTIC 1: event density per year ----
    print("=" * 120)
    print("DIAGNOSTIC 1 — Breadth-event density per year")
    print("=" * 120)

    event_rows = DB.execute_sql(f"""
        SELECT date,
               zweig_thrust_active, zweig_boost,
               hindenburg_omen, hindenburg_confirmed, hindenburg_penalty,
               breadth_score
        FROM market_breadth
        WHERE date >= '{cutoff}'
        ORDER BY date
    """).fetchall()

    # Build map: date -> {zweig_boost, hindenburg_penalty, ...}
    event_map = {}
    for r in event_rows:
        d, zw_act, zw_boost, hi_omen, hi_conf, hi_pen, brd = r
        event_map[d] = {
            'zweig_active': bool(zw_act) if zw_act is not None else False,
            'zweig_boost': float(zw_boost) if zw_boost is not None else 0.0,
            'hindenburg_omen': bool(hi_omen) if hi_omen is not None else False,
            'hindenburg_confirmed': bool(hi_conf) if hi_conf is not None else False,
            'hindenburg_penalty': float(hi_pen) if hi_pen is not None else 0.0,
            'breadth_score': float(brd) if brd is not None else None,
        }

    def _event_strength(d):
        """Return 0.0 if no event, ramps toward 1.0 as event magnitude grows.
        Max magnitude for a single trigger: Zweig +15, Hindenburg -18. Sum of abs."""
        e = event_map.get(d, {})
        zb = abs(e.get('zweig_boost', 0.0) or 0.0)
        hp = abs(e.get('hindenburg_penalty', 0.0) or 0.0)
        return min(1.0, (zb + hp) / 18.0)

    # Per-year event-day counts
    print(f"{'year':<6} {'total_days':>10} {'zweig_active':>13} {'zweig_trigger':>14} "
          f"{'hind_omen':>10} {'hind_confirmed':>15} {'event_strong':>14} {'event_mild':>12}")
    # zweig_trigger = first day active (new event); else continuing
    # event_strong = |boost|+|pen| >= 5 (meaningful active decay)
    # event_mild = |boost|+|pen| >= 1 (any decay still in effect)
    for yr in [2021, 2022, 2023, 2024, 2025, 2026]:
        yr_dates = [d for d in event_map if d.year == yr]
        if not yr_dates:
            continue
        zweig_active = sum(1 for d in yr_dates if event_map[d]['zweig_active'])
        hind_omen = sum(1 for d in yr_dates if event_map[d]['hindenburg_omen'])
        hind_conf = sum(1 for d in yr_dates if event_map[d]['hindenburg_confirmed'])
        # trigger = first day of a boost block (boost nonzero today, zero yesterday)
        zweig_triggers = 0
        prev_zw = 0.0
        for d in sorted(yr_dates):
            curr_zw = event_map[d]['zweig_boost']
            if curr_zw > 0 and prev_zw == 0:
                zweig_triggers += 1
            prev_zw = curr_zw
        strong_days = sum(1 for d in yr_dates if _event_strength(d) >= 5.0/18.0)
        mild_days = sum(1 for d in yr_dates if _event_strength(d) >= 1.0/18.0)
        print(f"{yr:<6} {len(yr_dates):>10} {zweig_active:>13} {zweig_triggers:>14} "
              f"{hind_omen:>10} {hind_conf:>15} {strong_days:>14} {mild_days:>12}")

    # ---- DIAGNOSTIC 2: WR decomposition by event-active status ----
    print("\n" + "=" * 120)
    print("DIAGNOSTIC 2 — BASE vs A050 WR decomposition, event-active vs event-neutral")
    print("=" * 120)

    # Load peaks (pre_regime)
    print("Loading peaks...")
    peak_rows = DB.execute_sql(f"""
        SELECT symbol, date, overall, regime_multiplier, weight_info
        FROM scores
        WHERE version_id = {vid}
          AND date >= '{cutoff}'
          AND weight_info IS NOT NULL
        ORDER BY symbol, date
    """).fetchall()
    peaks = []
    for sym, d, overall, sm, wi in peak_rows:
        try:
            wi_d = json.loads(wi) if isinstance(wi, str) else wi
            pr = wi_d.get('pre_regime')
            if pr is None: continue
            pr = int(pr)
        except Exception:
            continue
        if not (pr >= 60 or pr <= 40):
            continue
        peaks.append({
            'sym': sym, 'date': d, 'pre_regime': pr,
            'stored_mult': float(sm) if sm is not None else 1.0,
            'overall_stored': int(overall),
        })
    print(f"  Loaded {len(peaks):,} candidate peaks")

    # Apply BASE and A050
    for pk in peaks:
        pr = pk['pre_regime']
        sm = pk['stored_mult']
        pk['side'] = 'call' if pr >= 50 else 'put'
        pk['overall_base'] = _apply_mult(pr, sm)
        pk['overall_a050'] = _apply_mult(pr, _eff_mult_a050(sm, pr))

    # Barrier walk only for peaks qualifying under either variant at 85+ or 80+ (call focus)
    # and for put side at <=15 or <=25
    qual_set = set()
    for pk in peaks:
        if pk['side'] == 'call':
            if pk['overall_base'] >= 80 or pk['overall_a050'] >= 80:
                qual_set.add((pk['sym'], pk['date']))
        else:
            if pk['overall_base'] <= 25 or pk['overall_a050'] <= 25:
                qual_set.add((pk['sym'], pk['date']))

    symbols_needed = set(s for s, _ in qual_set)
    print(f"  {len(qual_set):,} qualifying pairs across {len(symbols_needed):,} symbols")

    price_cache = {}
    for i, sym in enumerate(sorted(symbols_needed)):
        if i % 100 == 0:
            print(f"    ...loading {i}/{len(symbols_needed)}", flush=True)
        price_cache[sym] = DB.execute_sql(f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date >= '{cutoff}' ORDER BY date
        """).fetchall()

    print(f"\n  Computing {len(qual_set):,} barrier outcomes...")
    outcomes = {}
    for i, (sym, d) in enumerate(qual_set):
        if i % 10000 == 0:
            print(f"    ...walk {i}/{len(qual_set)}", flush=True)
        bars_list = price_cache.get(sym, [])
        d_idx = {b[0]: idx for idx, b in enumerate(bars_list)}
        idx = d_idx.get(d)
        if idx is None or idx < 30:
            continue
        closes = [float(b[1]) for b in bars_list]
        highs = [float(b[2]) for b in bars_list]
        lows = [float(b[3]) for b in bars_list]
        sigma = _realized_vol(closes[:idx + 1])
        if sigma is None:
            continue
        entry = closes[idx]
        fwd = []
        for j in range(idx + 1, len(bars_list)):
            off = (bars_list[j][0] - d).days
            if off > 40: break
            fwd.append((off, highs[j], lows[j]))
        outcomes[(sym, d)] = {
            'w15c': _barrier_walk('low', entry, fwd, sigma, 15),
            'w15p': _barrier_walk('high', entry, fwd, sigma, 15),
        }

    # Aggregate WR by (variant, window, cumulative_bucket, event_status)
    print("\nAggregating WR split by event status...")
    # event strength thresholds
    STRONG_THR = 5.0 / 18.0  # |boost|+|pen|>=5
    MILD_THR = 1.0 / 18.0    # >=1 (any decay)

    def _event_bucket(d):
        s = _event_strength(d)
        if s >= STRONG_THR:
            return 'STRONG'
        if s >= MILD_THR:
            return 'MILD'
        return 'NEUTRAL'

    wr = defaultdict(lambda: [0, 0])  # (variant, window, bucket, event_bucket) -> [wins, total]
    for pk in peaks:
        out = outcomes.get((pk['sym'], pk['date']))
        if out is None: continue
        yr = pk['date'].year
        ebkt = _event_bucket(pk['date'])
        # evaluate both variants
        for vname, ov_key in [('BASE', 'overall_base'), ('A050', 'overall_a050')]:
            ov = pk[ov_key]
            side = pk['side']
            if side == 'call' and out['w15c'] is not None:
                for cut in [70, 75, 80, 85, 90, 95]:
                    if ov >= cut:
                        for w in ('5y', yr):
                            for ekey in ('ALL', ebkt):
                                wr[(vname, w, f"{cut}+", ekey)][0] += out['w15c']
                                wr[(vname, w, f"{cut}+", ekey)][1] += 1
            elif side == 'put' and out['w15p'] is not None:
                for cut in [25, 20, 15, 10, 5]:
                    if ov <= cut:
                        for w in ('5y', yr):
                            for ekey in ('ALL', ebkt):
                                wr[(vname, w, f"<{cut}", ekey)][0] += out['w15p']
                                wr[(vname, w, f"<{cut}", ekey)][1] += 1

    # Print: per-year 85+ WR BASE vs A050 split by event status
    print("\n" + "=" * 120)
    print("PER-YEAR 85+ CALL WR15 — BASE vs A050 — split by event-active status")
    print("=" * 120)
    def _fmt(w, t):
        if t == 0: return f"{'--':>13}"
        return f"{w/t*100:5.1f}%/{t:>4}"
    for yr in [2022, 2023, 2024, 2025]:
        print(f"\n--- {yr} ---")
        print(f"{'subset':<10}  {'BASE 85+':>15} {'A050 85+':>15} {'delta':>8}   "
              f"{'BASE 80+':>15} {'A050 80+':>15} {'delta':>8}   "
              f"{'BASE 90+':>15} {'A050 90+':>15} {'delta':>8}")
        for ebkt in ('ALL', 'NEUTRAL', 'MILD', 'STRONG'):
            cells = [f"{ebkt:<10}"]
            for cut in [85, 80, 90]:
                bk = f"{cut}+"
                b_w, b_t = wr.get(('BASE', yr, bk, ebkt), [0, 0])
                a_w, a_t = wr.get(('A050', yr, bk, ebkt), [0, 0])
                cells.append(f"  {_fmt(b_w, b_t)} {_fmt(a_w, a_t)}")
                if b_t and a_t:
                    delta = (a_w / a_t - b_w / b_t) * 100
                    cells.append(f" {delta:+7.1f}")
                else:
                    cells.append(f" {'--':>7}")
            print(''.join(cells))

    # Summary: 5y aggregate
    print("\n" + "=" * 120)
    print("5Y AGGREGATE WR15 — BASE vs A050 — split by event-active status")
    print("=" * 120)
    print(f"{'subset':<10}  {'BASE 85+':>15} {'A050 85+':>15} {'delta':>8}   "
          f"{'BASE 80+':>15} {'A050 80+':>15} {'delta':>8}   "
          f"{'BASE 95+':>15} {'A050 95+':>15} {'delta':>8}")
    for ebkt in ('ALL', 'NEUTRAL', 'MILD', 'STRONG'):
        cells = [f"{ebkt:<10}"]
        for cut in [85, 80, 95]:
            bk = f"{cut}+"
            b_w, b_t = wr.get(('BASE', '5y', bk, ebkt), [0, 0])
            a_w, a_t = wr.get(('A050', '5y', bk, ebkt), [0, 0])
            cells.append(f"  {_fmt(b_w, b_t)} {_fmt(a_w, a_t)}")
            if b_t and a_t:
                delta = (a_w / a_t - b_w / b_t) * 100
                cells.append(f" {delta:+7.1f}")
            else:
                cells.append(f" {'--':>7}")
        print(''.join(cells))


if __name__ == '__main__':
    main()
