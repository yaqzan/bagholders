"""Benchmark: SQLite barrier_cache vs three DuckDB backends.

Runs `peaks_to_swing_results`-equivalent on each backend across realistic
peak counts, returns timing table + correctness check.

Usage:
    python -m experiments.duckdb_spike.bench
    python -m experiments.duckdb_spike.bench --counts 1000,10000,50000
    python -m experiments.duckdb_spike.bench --warmup 1 --runs 3
"""
from __future__ import annotations
import argparse
import random
import sqlite3
import time
from dataclasses import dataclass

from database.barrier_cache import (
    CACHE_DB, DEFAULT_BARRIER_SET, peaks_to_swing_results,
)
from experiments.duckdb_spike.duck_cache import (
    materialize_duck_native, materialize_duck_parquet,
    peaks_via_duck_attach, peaks_via_duck_native, peaks_via_duck_parquet,
    DUCK_NATIVE, DUCK_PARQUET,
)


@dataclass
class FakePeak:
    """Stand-in for a database.models.core.Score with the attrs the cache reads."""
    symbol_id: str
    date: str            # ISO string; cache code handles both
    overall: int


def sample_peaks(n: int, barrier_set: str = DEFAULT_BARRIER_SET, seed: int = 7):
    """Sample N realistic (symbol, date, overall) triples from the SQLite cache.

    We pick rows where outcomes exist — guarantees JOIN hits land. Score is
    synthesized to match the side: 'high'-side rows get score 20 (put),
    'low'-side get 80 (call). The peak_keys dict only needs (sym, date, side).
    """
    conn = sqlite3.connect(str(CACHE_DB))
    cur = conn.cursor()
    # Pick a single representative w_days row per (sym, date, side) to avoid
    # 14× duplication. w_days=30 is the commonest assess query.
    cur.execute(f"""
        SELECT symbol, date, side
        FROM barrier_outcomes
        WHERE w_days = 30 AND barrier_set = ?
        ORDER BY RANDOM()
        LIMIT ?
    """, (barrier_set, n))
    rows = cur.fetchall()
    conn.close()
    rng = random.Random(seed)
    peaks = []
    for sym, dt, side in rows:
        score = rng.randint(70, 95) if side == 'low' else rng.randint(5, 25)
        peaks.append(FakePeak(symbol_id=sym, date=dt, overall=score))
    return peaks


def time_call(fn, peaks, barrier_set, runs):
    """Run fn(peaks, barrier_set) `runs` times, return list of seconds."""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        results, skipped = fn(peaks, barrier_set)
        t1 = time.perf_counter()
        times.append(t1 - t0)
    return times, len(results), skipped


def equivalence_check(a, b, label_a, label_b):
    """Compare two result lists for structural identity.

    Each list is `peaks_to_swing_results`-formatted. We check counts and
    spot-check a sample of dicts.
    """
    if len(a) != len(b):
        return False, f"len mismatch: {label_a}={len(a)}, {label_b}={len(b)}"
    if not a:
        return True, "both empty"
    # Compare by (symbol, date) → win/stop labels per period
    def _key(rec):
        return (rec['symbol'], rec['date'].isoformat() if hasattr(rec['date'], 'isoformat') else rec['date'])
    map_a = {_key(r): r for r in a}
    map_b = {_key(r): r for r in b}
    if set(map_a.keys()) != set(map_b.keys()):
        diff_a = set(map_a.keys()) - set(map_b.keys())
        diff_b = set(map_b.keys()) - set(map_a.keys())
        return False, f"key mismatch: {label_a}-only={len(diff_a)}, {label_b}-only={len(diff_b)}"
    mismatches = 0
    sample_keys = random.Random(0).sample(list(map_a.keys()), min(100, len(map_a)))
    for k in sample_keys:
        ra, rb = map_a[k], map_b[k]
        for lbl in ra['swing'].keys() | rb['swing'].keys():
            sa = ra['swing'].get(lbl, {}); sb = rb['swing'].get(lbl, {})
            if sa.get('result') != sb.get('result'):
                mismatches += 1
                break
    if mismatches:
        return False, f"{mismatches}/{len(sample_keys)} sampled records had mismatched per-period 'result'"
    return True, f"{len(map_a)} records, {len(sample_keys)} spot-checked OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--counts', default='1000,10000,50000',
                    help='comma-separated peak counts to test')
    ap.add_argument('--runs', type=int, default=3, help='timed runs per backend per count')
    ap.add_argument('--warmup', type=int, default=1, help='warmup runs (untimed)')
    ap.add_argument('--barrier-set', default=DEFAULT_BARRIER_SET)
    ap.add_argument('--skip-correctness', action='store_true')
    args = ap.parse_args()

    counts = [int(x) for x in args.counts.split(',')]

    # ---- Materialize DuckDB backends (one-time cost, reported separately)
    print("=" * 78)
    print("BACKEND PREP")
    print("=" * 78)

    t0 = time.perf_counter()
    materialize_duck_native()
    t_native = time.perf_counter() - t0
    print(f"  duck_native materialize : {t_native:>7.2f}s  ({DUCK_NATIVE.stat().st_size/1e9:.2f} GB)")

    t0 = time.perf_counter()
    materialize_duck_parquet()
    t_parquet = time.perf_counter() - t0
    print(f"  duck_parquet materialize: {t_parquet:>7.2f}s  ({DUCK_PARQUET.stat().st_size/1e9:.2f} GB)")
    print(f"  sqlite source size      : {CACHE_DB.stat().st_size/1e9:.2f} GB (existing)")

    backends = [
        ('sqlite (baseline)', peaks_to_swing_results),
        ('duck_attach',       peaks_via_duck_attach),
        ('duck_native',       peaks_via_duck_native),
        ('duck_parquet',      peaks_via_duck_parquet),
    ]

    # ---- Run benchmark
    for n in counts:
        print()
        print("=" * 78)
        print(f"PEAK COUNT: {n:,}")
        print("=" * 78)

        peaks = sample_peaks(n, barrier_set=args.barrier_set)

        # Warmups (no timing)
        for label, fn in backends:
            for _ in range(args.warmup):
                fn(peaks, args.barrier_set)

        # Timed runs
        results = {}
        for label, fn in backends:
            times, n_results, skipped = time_call(fn, peaks, args.barrier_set, args.runs)
            results[label] = {
                'min': min(times), 'mean': sum(times) / len(times), 'max': max(times),
                'n_results': n_results, 'skipped': skipped,
            }

        # Print table
        baseline = results['sqlite (baseline)']['min']
        print(f"  {'backend':<22} {'min (s)':>10} {'mean (s)':>10} {'max (s)':>10} {'speedup':>10} {'records':>10}")
        for label, _ in backends:
            r = results[label]
            speedup = baseline / r['min'] if r['min'] > 0 else float('inf')
            print(f"  {label:<22} {r['min']:>10.3f} {r['mean']:>10.3f} {r['max']:>10.3f} "
                  f"{speedup:>9.2f}x {r['n_results']:>10,}")

        # Correctness vs sqlite baseline (compare on the smaller counts only;
        # large N spot-check is slow but worth doing once)
        if not args.skip_correctness:
            print()
            print("  correctness vs sqlite baseline:")
            base_results, _ = peaks_to_swing_results(peaks, barrier_set=args.barrier_set)
            for label, fn in backends[1:]:
                cand_results, _ = fn(peaks, args.barrier_set)
                ok, msg = equivalence_check(base_results, cand_results, 'sqlite', label)
                marker = '[OK]' if ok else '[X]'
                print(f"    {marker} {label:<22} {msg}")


if __name__ == '__main__':
    main()
