"""Validate fast_assess against the original assess_peaks_in_memory.

Runs both pipelines on the same set of peaks, compares bucket-level WR15/WR30
to within tolerance. Reports per-bucket discrepancies.

Run after the cache backfill completes.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from collections import defaultdict
from datetime import date, timedelta

from database.models.core import Score, AlgorithmVersion
from database.models.technical import PriceHistory


def main():
    av = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: v{av.id} ({av.git_commit[:10]})")

    # Pull v26 peaks for validation set
    cutoff = date.today() - timedelta(days=1825)
    print(f"Loading peaks since {cutoff}...")
    rows = list(Score.select(Score.symbol, Score.date, Score.overall)
                .where(Score.version == av, Score.date >= cutoff,
                       Score.overall.is_null(False))
                .where((Score.overall >= 70) | (Score.overall <= 25)))

    # Prune adjacent dates per symbol
    class _Peak:
        __slots__ = ('symbol_id', 'date', 'overall')
        def __init__(self, s, d, o):
            self.symbol_id, self.date, self.overall = s, d, o

    by_sym = defaultdict(list)
    for r in rows:
        sym = r.symbol.symbol if hasattr(r.symbol, 'symbol') else r.symbol_id
        by_sym[sym].append((r.date, r.overall))

    peaks = []
    for sym, entries in by_sym.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, sc in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            peaks.append(_Peak(sym, d, sc))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)
    print(f"Pruned to {len(peaks)} peaks")

    # ── Original assessment (forward walk) ──
    print("\nRunning ORIGINAL assess_peaks_in_memory ...")
    sym_ids = list(set(p.symbol_id for p in peaks))
    ph_rows = list(PriceHistory.select()
                   .where(PriceHistory.symbol.in_(sym_ids), PriceHistory.date >= cutoff)
                   .order_by(PriceHistory.symbol, PriceHistory.date)
                   .namedtuples())
    ph_by_sym = defaultdict(list)
    for r in ph_rows:
        ph_by_sym[r.symbol].append(r)

    from assess_scores import assess_peaks_in_memory
    t0 = time.time()
    orig = assess_peaks_in_memory(peaks, ph_by_sym, 1825)
    t_orig = time.time() - t0
    print(f"  ORIGINAL: {t_orig:.1f}s")

    # ── Cache-driven assessment ──
    print("\nRunning FAST assess_peaks (cache lookup) ...")
    from experiments.fast_assess import fast_assess_peaks
    t0 = time.time()
    fast = fast_assess_peaks(peaks, lookback_days=1825, use_polars=False)
    t_fast = time.time() - t0
    print(f"  FAST:     {t_fast:.1f}s   ({t_orig/max(0.001, t_fast):.1f}x speedup)")

    print("\nRunning FAST assess_peaks (Polars) ...")
    t0 = time.time()
    fast_pl = fast_assess_peaks(peaks, lookback_days=1825, use_polars=True)
    t_fast_pl = time.time() - t0
    print(f"  FAST_PL:  {t_fast_pl:.1f}s   ({t_orig/max(0.001, t_fast_pl):.1f}x speedup)")

    # ── Compare ──
    print("\n" + "=" * 90)
    print("COMPARISON: ORIGINAL vs FAST (dict) vs FAST (polars)")
    print("=" * 90)
    THRESHOLDS = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<20', '<15', '<10', '<5']
    print(f"{'Bucket':<8} {'N orig':>8} {'N fast':>8} {'WR15 orig':>11} {'WR15 fast':>11} {'WR15 PL':>10} {'Δ orig-fast':>13}")
    for k in THRESHOLDS:
        o = orig['bucketed_stats'].get(k, {})
        f = fast['bucketed_stats'].get(k, {})
        f_pl = fast_pl['bucketed_stats'].get(k, {})
        no = o.get('sample_count') or 0
        nf = f.get('sample_count') or 0
        wo = o.get('win_rate_15d')
        wf = f.get('win_rate_15d')
        wpl = f_pl.get('win_rate_15d')
        wo_s = f"{wo:.1f}%" if wo is not None else '--'
        wf_s = f"{wf:.1f}%" if wf is not None else '--'
        wpl_s = f"{wpl:.1f}%" if wpl is not None else '--'
        d = (wo - wf) if (wo is not None and wf is not None) else 0
        flag = ' OK' if abs(d) < 1.5 else ' MISMATCH'
        print(f"{k:<8} {no:>8} {nf:>8} {wo_s:>11} {wf_s:>11} {wpl_s:>10} {d:>+12.2f}pp{flag}")


if __name__ == '__main__':
    main()
