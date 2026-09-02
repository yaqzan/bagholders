"""Re-rank v2 sweep results with the user's natural-gradient priority.

The user's stated objectives in priority order:
  1. PRESERVE NATURAL GRADIENT — dampening should lessen as score drops, so
     higher tiers (80-84) get more dampening than lower tiers (70-74)
     → require SCORE_POWER >= 1.5 (concentrate dampening at high scores)
  2. PRESERVE BUCKET MONOTONICITY — dampened TPs should still satisfy
     70-74 < 75-79 < 80-84 < 85-89 < 90+
  3. PRESERVE INTER-BUCKET GAPS — at least 2pp between adjacent populated
     buckets (the 'natural scoring wave' the user described)
  4. MAXIMIZE 75+ TP% LIFT subject to the above

Read sweep_v2.jsonl, filter, re-rank, recommend.
"""
from __future__ import annotations
import io, sys, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path

LOG = Path(__file__).parent / 'sweep_v2.jsonl'


def load():
    return [json.loads(l) for l in open(LOG, encoding='utf-8') if l.strip()]


def passes_strict(r, sp_floor=1.5, min_gap_floor=2.0):
    if not r['monotonic_5y'] or not r['monotonic_10y']:
        return False
    if r['score_power'] < sp_floor:
        return False
    if r['min_gap_5y'] is None or r['min_gap_5y'] < min_gap_floor:
        return False
    if r['cum_lift_5y'] is None or r['cum_lift_5y'] < 1.0:
        return False
    if r['cum_lift_10y'] is None or r['cum_lift_10y'] < 1.0:
        return False
    if r['n_drop_5y_pct'] < -40.0:
        return False
    if r['worst_spill'] < -0.4:
        return False
    return True


def passes_relaxed(r, sp_floor=1.0, min_gap_floor=2.0):
    if not r['monotonic_5y'] or not r['monotonic_10y']:
        return False
    if r['score_power'] < sp_floor:
        return False
    if r['min_gap_5y'] is None or r['min_gap_5y'] < min_gap_floor:
        return False
    if r['cum_lift_5y'] is None or r['cum_lift_5y'] < 1.0:
        return False
    if r['cum_lift_10y'] is None or r['cum_lift_10y'] < 1.0:
        return False
    if r['n_drop_5y_pct'] < -40.0:
        return False
    if r['worst_spill'] < -0.4:
        return False
    return True


def fmt(r):
    gaps = [round(g, 2) for g in r['gaps_5y']]
    return (f"LO={r['log_lo']:>4.1f} HI={r['log_hi']:>4.1f} α={r['alpha']:.2f} "
            f"T={r['target']:>2} mp={r['mcap_power']:>3.1f} sp={r['score_power']:>3.1f} | "
            f"5y={r['cum_lift_5y']:+.2f} 10y={r['cum_lift_10y']:+.2f} "
            f"gaps={gaps} rmse5y={r['rmse_5y']:.2f} N={r['n_drop_5y_pct']:+.1f}%")


def main():
    results = load()
    print(f'[rerank v2] loaded {len(results):,} variants')

    # Show distribution
    sp_vals = sorted(set(r['score_power'] for r in results))
    print(f'  score_power values tested: {sp_vals}')
    mp_vals = sorted(set(r['mcap_power'] for r in results))
    print(f'  mcap_power values tested: {mp_vals}')

    # ── STRICT (sp >= 1.5, min_gap >= 2.0pp) — user's natural-gradient priority ──
    strict = [r for r in results if passes_strict(r, sp_floor=1.5, min_gap_floor=2.0)]
    strict.sort(key=lambda r: -r['cum_lift_5y'])
    print(f'\n=== STRICT (SCORE_POWER >= 1.5, min_gap >= 2.0pp): {len(strict)} pass ===')
    print('Top 15 by cum_lift_5y:')
    for r in strict[:15]:
        print(f'  {fmt(r)}')

    # ── RELAXED (sp >= 1.0, allow linear; min_gap >= 2.0pp) ──
    relaxed = [r for r in results if passes_relaxed(r, sp_floor=1.0, min_gap_floor=2.0)]
    relaxed.sort(key=lambda r: -r['cum_lift_5y'])
    print(f'\n=== RELAXED (SCORE_POWER >= 1.0, min_gap >= 2.0pp): {len(relaxed)} pass ===')
    print('Top 10 by cum_lift_5y:')
    for r in relaxed[:10]:
        print(f'  {fmt(r)}')

    # ── COMPARE shapes ──
    if strict:
        winner_strict = strict[0]
        print(f'\n=== STRICT WINNER (preserves natural gradient) ===')
        print(f'  {fmt(winner_strict)}')
        print(f"  Per-bucket 5y:")
        for label in ['70-74', '75-79', '80-84', '85-89', '90+']:
            c = winner_strict['per_bucket_5y'][label]
            print(f"    {label}: base {c['base_tp']:.2f}% → damp {c['tp']:.2f}%  Δ {c['delta']:+.2f}pp  N={c['n']}")

    # Compare to v1 standard candidate
    print('\n=== HEAD-TO-HEAD: v2 strict winner vs v1 standard candidate ===')
    print(f"  v1 standard:    LO=+0.0 HI=+2.2 α=0.30 T=65 mp=1.0 sp=1.0  →  +1.92pp 5y / +1.93pp 10y")
    if strict:
        ws = strict[0]
        print(f"  v2 strict win:  LO={ws['log_lo']:+.1f} HI={ws['log_hi']:+.1f} α={ws['alpha']:.2f} T={ws['target']} "
              f"mp={ws['mcap_power']:.1f} sp={ws['score_power']:.1f}  →  "
              f"{ws['cum_lift_5y']:+.2f}pp 5y / {ws['cum_lift_10y']:+.2f}pp 10y")

    # ── ALSO: best variant with score_power = 2.0 specifically (intuitive logarithmic-style) ──
    sp2 = [r for r in results if r['score_power'] == 2.0 and r['monotonic_5y'] and r['monotonic_10y']
           and r['min_gap_5y'] is not None and r['min_gap_5y'] >= 2.0
           and r['cum_lift_5y'] >= 1.0 and r['cum_lift_10y'] >= 1.0
           and r['n_drop_5y_pct'] >= -40.0 and r['worst_spill'] >= -0.4]
    sp2.sort(key=lambda r: -r['cum_lift_5y'])
    print(f'\n=== SP=2.0 only (clean quadratic concentration at high scores): {len(sp2)} pass ===')
    print('Top 10 by cum_lift_5y:')
    for r in sp2[:10]:
        print(f'  {fmt(r)}')

    sp3 = [r for r in results if r['score_power'] == 3.0 and r['monotonic_5y'] and r['monotonic_10y']
           and r['min_gap_5y'] is not None and r['min_gap_5y'] >= 2.0
           and r['cum_lift_5y'] >= 1.0 and r['cum_lift_10y'] >= 1.0
           and r['n_drop_5y_pct'] >= -40.0 and r['worst_spill'] >= -0.4]
    sp3.sort(key=lambda r: -r['cum_lift_5y'])
    print(f'\n=== SP=3.0 only (cubic concentration — heavy at top, near-zero at 70-74): {len(sp3)} pass ===')
    print('Top 10 by cum_lift_5y:')
    for r in sp3[:10]:
        print(f'  {fmt(r)}')

    # Best by widest gap (most natural)
    natural = [r for r in results if r['monotonic_5y'] and r['monotonic_10y']
               and r['min_gap_5y'] is not None and r['min_gap_5y'] >= 3.0
               and r['cum_lift_5y'] >= 1.0 and r['cum_lift_10y'] >= 1.0
               and r['score_power'] >= 1.5
               and r['n_drop_5y_pct'] >= -40.0 and r['worst_spill'] >= -0.4]
    natural.sort(key=lambda r: -r['cum_lift_5y'])
    print(f'\n=== NATURAL (sp >= 1.5 AND min_gap >= 3.0pp): {len(natural)} pass ===')
    print('Top 10 by cum_lift_5y:')
    for r in natural[:10]:
        print(f'  {fmt(r)}')


if __name__ == '__main__':
    main()
