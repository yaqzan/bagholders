"""
v22 follow-up — counter-trend bucket analyzer.

The standard 70+ / <25 buckets averaged the X-conf gate's effect across all
TREND-direction signals. The PSKY-class case (#11 in CLAUDE.md) is specifically:

    overall <= 25  AND  TREND >= 70   (counter-trend put)

i.e. an extreme put score fired while TREND component is bullish — exactly the
pattern v22 is supposed to neutralize. This script slices peaks by both overall
and trend_component, and reports N + WR15 per (variant x bucket).

Buckets:
    CT-PUT:   overall <= 25  AND  trend >= 70   (the PSKY case — should DROP in N)
    AL-PUT:   overall <= 25  AND  trend <= 30   (trend-aligned puts — should HOLD)
    CT-CALL:  overall >= 70  AND  trend <= 30   (mirror — should DROP in N)
    AL-CALL:  overall >= 70  AND  trend >= 70   (trend-aligned calls — should HOLD)

Success gate (CLAUDE.md item #8 + user's gate refinement):
    CT-PUT N drops vs baseline (gate doing its job)
    CT-PUT WR15 stays >= 75% on what survives (the gate kept the genuine ones)
    AL-PUT WR15/N approximately preserved (didn't break trend-aligned signals)

Usage
-----
    python experiments/x_conf_counter_trend.py            # full universe, 22-now
    python experiments/x_conf_counter_trend.py AAPL MSFT NVDA 730    # smoke
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta

import database.utils.scoring as scoring_mod
from experiments.x_confidence_gate_sweep import make_v22, _ORIGINAL


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
    """Recompute TREND component for one (ctx, date). Cheap — uses pre-built caches."""
    return ctx.stock.calculate_trend_score(
        d, _ind_cache=ctx.ind_cache_for(d), _ph_cache=ctx.ph_map
    )


def _walk_wr15(peaks, ph_by_sym):
    """Tiny barrier-touch walk just for counting WR15. Mirrors assess_scores semantics
    at K/M/W = put-side (1.0 / 2.0 / 15) and call-side (2.0 / 5.0 / 15)."""
    import numpy as np
    from datetime import timedelta as td

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
    for p in peaks:
        rows = ph_by_sym.get(p.symbol_id, [])
        if not rows:
            continue
        # locate peak index
        idx = next((i for i, r in enumerate(rows) if r.date == p.date), None)
        if idx is None or idx == 0:
            continue
        v = vol_pct(rows, idx)
        if v is None or v <= 0:
            continue
        entry = float(rows[idx].close)
        side_high = (p.overall < 50)         # put: win = price falls
        if side_high:
            K, M = 1.0, 2.0
        else:
            K, M = 2.0, 5.0
        scale = 1.0  # W=15 reference; production uses sqrt(15/30) but we mirror v17 fixed-W=15 walk
        # Actually scaled walk uses sqrt(W/30):
        import math
        scale = math.sqrt(15.0 / 30.0)
        target = entry * (1 - K * v / 100 * scale) if side_high else entry * (1 + K * v / 100 * scale)
        stop   = entry * (1 + M * v / 100 * scale) if side_high else entry * (1 - M * v / 100 * scale)
        cutoff = p.date + td(days=15)
        # walk forward
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
            # didn't resolve in 15d — skip (matches assess_scores semantics for unresolved)
            continue
        wins += verdict
        total += 1
    return wins, total


def _classify(peaks, trend_by_key):
    """Return dict of bucket_label -> list[peak]."""
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


def _run_variant(label, alpha, x_inputs, sim, cutoff_date, windows):
    from simulator import _FakePeak
    from database.models.technical import PriceHistory

    print("\n" + "=" * 100)
    print(f"  VARIANT: {label}   alpha={alpha:.2f}   x_inputs={x_inputs}")
    print("=" * 100)

    scoring_mod.compute_overall_score = (
        _ORIGINAL if alpha == 0.0 else make_v22(alpha, x_inputs)
    )
    scores = sim.simulate()

    # Build sim peaks (non-overlap pruning, same as sweep)
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

    # Recompute TREND per peak from contexts
    trend_by_key = {}
    for p in sim_peaks:
        ctx = sim._contexts.get(p.symbol_id)
        if ctx is None:
            continue
        tr = _trend_for_peak(ctx, p.date)
        if tr is not None:
            trend_by_key[(p.symbol_id, p.date)] = tr

    # Bulk-load price history once for the walk
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

    # Per-window classify + WR15
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

    # Focused 3-variant sweep: baseline + middle + full gate
    variants = [
        ('baseline (prod)', 0.00, '5comp'),
        ('a50_5c',          0.50, '5comp'),
        ('a100_5c',         1.00, '5comp'),
    ]

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

    for label, alpha, x_inputs in variants:
        _run_variant(label, alpha, x_inputs, sim, cutoff_date, windows)

    scoring_mod.compute_overall_score = _ORIGINAL


if __name__ == '__main__':
    main()
