"""
Phase I — floor sweep on the fl060_rc092 ship candidate.

Phase H confirmed alpha does not move the gate (floor=0.60 clamp absorbs all
variation). The actual gradient lever is the floor. Sweep floor at fixed
alpha=0.50, regime_cutoff=0.92.

Lower floor → gate bites harder on low-x_conf cases → more CT-PUT promotion,
more AL regression risk.
Higher floor → gate softer → less CT-PUT lift, less AL disturbance.

Output: experiments/ct_phase_i_floor_sweep.out
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import date, timedelta

import database.utils.scoring as scoring_mod
from experiments.ct_designs import make_ct_score, _ORIGINAL


VARIANTS = [
    ('baseline_v19',            None),
    ('fl060_rc092_REF',         make_ct_score('floored_regime', alpha=0.50, floor=0.60, regime_cutoff=0.92)),
    ('fl050_rc092',             make_ct_score('floored_regime', alpha=0.50, floor=0.50, regime_cutoff=0.92)),
    ('fl055_rc092',             make_ct_score('floored_regime', alpha=0.50, floor=0.55, regime_cutoff=0.92)),
    ('fl065_rc092',             make_ct_score('floored_regime', alpha=0.50, floor=0.65, regime_cutoff=0.92)),
    ('fl070_rc092',             make_ct_score('floored_regime', alpha=0.50, floor=0.70, regime_cutoff=0.92)),
]


def _parse_args():
    syms = None
    days = (date.today() - date(2022, 1, 1)).days
    out = []
    for a in sys.argv[1:]:
        low = a.lower()
        if low.endswith('y'):
            days = int(float(low[:-1]) * 365)
        elif low.endswith('d'):
            days = int(low[:-1])
        elif low.isdigit():
            days = int(low)
        else:
            out.append(a.upper())
    if out:
        syms = out
    return syms, days


def _trend_for_peak(ctx, d):
    return ctx.stock.calculate_trend_score(
        d, _ind_cache=ctx.ind_cache_for(d), _ph_cache=ctx.ph_map
    )


def _walk_wr15(peaks, ph_by_sym):
    import numpy as np

    def vol_pct(rows, idx, lookback=60):
        start = max(0, idx - lookback)
        closes = [float(r.close) for r in rows[start:idx + 1] if r.close]
        if len(closes) < 20:
            return None
        rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        if not rets:
            return None
        return float(np.std(rets, ddof=0)) * 100.0

    wins = total = 0
    scale = math.sqrt(15.0 / 30.0)
    for p in peaks:
        rows = ph_by_sym.get(p.symbol_id, [])
        if not rows:
            continue
        idx = next((i for i, r in enumerate(rows) if r.date == p.date), None)
        if idx is None or idx == 0:
            continue
        v = vol_pct(rows, idx)
        if v is None or v <= 0:
            continue
        entry = float(rows[idx].close)
        side_high = (p.overall < 50)
        K, M = (1.0, 2.0) if side_high else (2.0, 5.0)
        target = entry * (1 - K * v / 100 * scale) if side_high else entry * (1 + K * v / 100 * scale)
        stop   = entry * (1 + M * v / 100 * scale) if side_high else entry * (1 - M * v / 100 * scale)
        cutoff = p.date + timedelta(days=15)
        verdict = None
        for r in rows[idx + 1:]:
            if r.date > cutoff:
                break
            hi, lo = float(r.high), float(r.low)
            if side_high:
                if lo <= target:
                    verdict = 1; break
                if hi >= stop:
                    verdict = 0; break
            else:
                if hi >= target:
                    verdict = 1; break
                if lo <= stop:
                    verdict = 0; break
        if verdict is None:
            continue
        wins += verdict
        total += 1
    return wins, total


def _classify(peaks, trend_by_key):
    ct_put, al_put, ct_call, al_call = [], [], [], []
    for p in peaks:
        tr = trend_by_key.get((p.symbol_id, p.date))
        if tr is None:
            continue
        if p.overall <= 25 and tr >= 70:
            ct_put.append(p)
        elif p.overall <= 25 and tr <= 30:
            al_put.append(p)
        elif p.overall >= 70 and tr <= 30:
            ct_call.append(p)
        elif p.overall >= 70 and tr >= 70:
            al_call.append(p)
    return {
        'CT-PUT  (overall<=25, trend>=70)': ct_put,
        'AL-PUT  (overall<=25, trend<=30)': al_put,
        'CT-CALL (overall>=70, trend<=30)': ct_call,
        'AL-CALL (overall>=70, trend>=70)': al_call,
    }


def _slice_peaks(peaks, start, end):
    return [p for p in peaks if start <= p.date <= end]


def _run_variant(label, scoring_fn, sim, cutoff_date, windows):
    from simulator import _FakePeak
    from database.models.technical import PriceHistory

    print("\n" + "=" * 100)
    print(f"  VARIANT: {label}")
    print("=" * 100)

    scoring_mod.compute_overall_score = _ORIGINAL if scoring_fn is None else scoring_fn
    scores = sim.simulate()

    by_symbol = defaultdict(list)
    for (sym, d), score in scores.items():
        if d < cutoff_date:
            continue
        if score >= 70 or score <= 25:
            by_symbol[sym].append((d, score))

    sim_peaks = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            sim_peaks.append(_FakePeak(sym, d, score))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    trend_by_key = {}
    for p in sim_peaks:
        ctx = sim._contexts.get(p.symbol_id)
        if ctx is None:
            continue
        tr = _trend_for_peak(ctx, p.date)
        if tr is not None:
            trend_by_key[(p.symbol_id, p.date)] = tr

    all_syms = list({p.symbol_id for p in sim_peaks})
    rows = list(
        PriceHistory.select()
        .where(PriceHistory.symbol.in_(all_syms), PriceHistory.date >= cutoff_date)
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .namedtuples()
    )
    ph_by_sym = defaultdict(list)
    for r in rows:
        ph_by_sym[r.symbol].append(r)

    for win_label, start, end in windows:
        slice_peaks = _slice_peaks(sim_peaks, start, end)
        buckets = _classify(slice_peaks, trend_by_key)
        print(f"\n-- Window [{win_label}]  ({start} -> {end})   peaks={len(slice_peaks)}")
        for bname, plist in buckets.items():
            wins, total = _walk_wr15(plist, ph_by_sym)
            wr = (100.0 * wins / total) if total else 0.0
            print(f"  {bname:42s}  N={len(plist):5d}  resolved={total:5d}  WR15={wr:5.1f}%")


def main():
    syms, days = _parse_args()
    from simulator import ScoreSimulator

    sim = ScoreSimulator(symbols=syms, lookback_days=days, scoring_fn=None)
    cutoff_date = date.today() - timedelta(days=days)
    today = date.today()

    def clamp(d): return min(d, today)
    windows = [
        ('22-now', date(2022, 1, 1), today),
        ('2022',   date(2022, 1, 1), clamp(date(2022, 12, 31))),
        ('2023',   date(2023, 1, 1), clamp(date(2023, 12, 31))),
        ('2024',   date(2024, 1, 1), clamp(date(2024, 12, 31))),
        ('2025',   date(2025, 1, 1), clamp(date(2025, 12, 31))),
    ]

    print(f"Lookback: {days}d   cutoff={cutoff_date}   today={today}")
    print(f"Windows:  {[w[0] for w in windows]}")
    print(f"Variants: {[v[0] for v in VARIANTS]}")

    for label, scoring_fn in VARIANTS:
        _run_variant(label, scoring_fn, sim, cutoff_date, windows)

    scoring_mod.compute_overall_score = _ORIGINAL


if __name__ == '__main__':
    main()
