"""Focused bench — skip SQLite at large N (would take hours).

- 1,000 peaks: all 4 backends + correctness check
- 10,000 peaks: 1 SQLite run (sanity-check scaling) + all DuckDB variants
- 50,000 peaks: DuckDB variants only
"""
from __future__ import annotations
import time

from database.barrier_cache import (
    DEFAULT_BARRIER_SET, peaks_to_swing_results,
)
from experiments.duckdb_spike.bench import (
    sample_peaks, equivalence_check, FakePeak,
)
from experiments.duckdb_spike.duck_cache import (
    peaks_via_duck_attach, peaks_via_duck_native, peaks_via_duck_parquet,
)


def _time(fn, peaks, runs):
    """Return list of (seconds, n_results)."""
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        results, skipped = fn(peaks, DEFAULT_BARRIER_SET)
        out.append((time.perf_counter() - t0, len(results)))
    return out


def _sqlite_call(peaks, runs):
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        results, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set=DEFAULT_BARRIER_SET)
        out.append((time.perf_counter() - t0, len(results)))
    return out


def _print_table(title, label_to_times, baseline_label='sqlite (baseline)'):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"  {'backend':<22} {'min (s)':>10} {'mean (s)':>10} {'max (s)':>10} {'speedup':>10} {'records':>10}")
    base_min = None
    if baseline_label in label_to_times:
        base_min = min(t for t, _ in label_to_times[baseline_label])
    for label, samples in label_to_times.items():
        ts = [t for t, _ in samples]
        n = samples[0][1]
        speedup = (base_min / min(ts)) if base_min else float('nan')
        sp_str = f"{speedup:>9.2f}x" if base_min else "       -- "
        print(f"  {label:<22} {min(ts):>10.3f} {sum(ts)/len(ts):>10.3f} {max(ts):>10.3f} "
              f"{sp_str} {n:>10,}")


def main():
    # ---- N=1,000 — full comparison
    peaks_1k = sample_peaks(1000)
    # warmups
    peaks_to_swing_results(peaks_1k, verbose=False, barrier_set=DEFAULT_BARRIER_SET)
    peaks_via_duck_attach(peaks_1k); peaks_via_duck_native(peaks_1k); peaks_via_duck_parquet(peaks_1k)

    label_to_times = {}
    label_to_times['sqlite (baseline)'] = _sqlite_call(peaks_1k, runs=3)
    label_to_times['duck_attach']  = _time(peaks_via_duck_attach,  peaks_1k, runs=3)
    label_to_times['duck_native']  = _time(peaks_via_duck_native,  peaks_1k, runs=3)
    label_to_times['duck_parquet'] = _time(peaks_via_duck_parquet, peaks_1k, runs=3)
    _print_table("PEAK COUNT: 1,000  (all backends)", label_to_times)

    # Correctness
    print()
    print("  correctness vs sqlite baseline (N=1,000):")
    base_results, _ = peaks_to_swing_results(peaks_1k, verbose=False, barrier_set=DEFAULT_BARRIER_SET)
    for lbl, fn in [('duck_attach', peaks_via_duck_attach),
                    ('duck_native', peaks_via_duck_native),
                    ('duck_parquet', peaks_via_duck_parquet)]:
        cand_results, _ = fn(peaks_1k, DEFAULT_BARRIER_SET)
        ok, msg = equivalence_check(base_results, cand_results, 'sqlite', lbl)
        marker = '[OK]' if ok else '[X]'
        print(f"    {marker} {lbl:<22} {msg}")

    # ---- N=10,000 — 1 SQLite run (scaling sanity check) + DuckDB variants
    peaks_10k = sample_peaks(10000)
    # warmups (DuckDB only — SQLite warmup at 10k would cost too much)
    peaks_via_duck_attach(peaks_10k); peaks_via_duck_native(peaks_10k); peaks_via_duck_parquet(peaks_10k)

    label_to_times = {}
    print()
    print("=" * 78)
    print("PEAK COUNT: 10,000  (single SQLite run for scaling, full DuckDB)")
    print("=" * 78)
    print("  running sqlite once (could take a while)...", flush=True)
    label_to_times['sqlite (baseline)'] = _sqlite_call(peaks_10k, runs=1)
    label_to_times['duck_attach']  = _time(peaks_via_duck_attach,  peaks_10k, runs=3)
    label_to_times['duck_native']  = _time(peaks_via_duck_native,  peaks_10k, runs=3)
    label_to_times['duck_parquet'] = _time(peaks_via_duck_parquet, peaks_10k, runs=3)
    _print_table("PEAK COUNT: 10,000", label_to_times)

    # ---- N=50,000 — DuckDB only
    peaks_50k = sample_peaks(50000)
    peaks_via_duck_native(peaks_50k); peaks_via_duck_parquet(peaks_50k)

    label_to_times = {}
    label_to_times['duck_attach']  = _time(peaks_via_duck_attach,  peaks_50k, runs=2)
    label_to_times['duck_native']  = _time(peaks_via_duck_native,  peaks_50k, runs=3)
    label_to_times['duck_parquet'] = _time(peaks_via_duck_parquet, peaks_50k, runs=3)
    _print_table("PEAK COUNT: 50,000  (DuckDB only)", label_to_times,
                 baseline_label='__none__')


if __name__ == '__main__':
    main()
