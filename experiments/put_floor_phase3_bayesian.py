"""Phase 3: Bayesian-style adaptive sweep around K10_T35 baseline.

Strategy:
  Round 1: Broad grid around K10_T35 (~50 variants in ~60s)
  Round 2: Tight grid around Round 1 winners (~30 variants in ~30s)
  Round 3: Optional final narrow sweep if Round 2 still shows direction (~15 variants)

Utility function (encodes user preferences):
  - WR15 lift on put deep buckets (<10, <15, <25) weighted heavily
  - WR15 lift on call buckets is welcome but not required (variant should not regress calls)
  - Put N reduction toward call N parity (target ratio ~1.0)

Score = put_wr_lift_weighted - call_regression_penalty + n_parity_bonus

Variants are evaluated via FastVariantRunner — ~0.8s each.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))

from fast_variant_runner import FastVariantRunner

PUT_BUCKET_WEIGHTS = {  # weighted toward deepest buckets
    '<25': 0.10,
    '<20': 0.10,
    '<15': 0.20,
    '<10': 0.30,
    '<5':  0.30,
}
CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']
TARGET_PUT_CALL_RATIO = 1.0  # ideal


def _utility(stats, baseline):
    """Compute scalar utility delta vs baseline."""
    if not baseline:
        return 0.0
    bs = stats.get('bucketed_stats', {})
    bb = baseline.get('bucketed_stats', {})

    # Weighted WR lift on put deep buckets
    put_wr_lift = 0.0
    for bk, w in PUT_BUCKET_WEIGHTS.items():
        v = (bs.get(bk, {}).get('win_rate_15d') or 0)
        bv = (bb.get(bk, {}).get('win_rate_15d') or 0)
        put_wr_lift += w * (v - bv)

    # Call regression penalty
    call_regression = 0.0
    for bk in CALL_BUCKETS:
        v = bs.get(bk, {}).get('win_rate_15d')
        bv = bb.get(bk, {}).get('win_rate_15d')
        if v is None or bv is None:
            continue
        delta = v - bv
        if delta < call_regression:
            call_regression = delta

    # N parity bonus (closer to 1:1 put:call ratio)
    n_put = bs.get('<25', {}).get('sample_count') or 0
    n_call = bs.get('70+', {}).get('sample_count') or 0
    if n_call > 0:
        ratio = n_put / n_call
        # Penalty grows with distance from target ratio
        n_parity = -max(0, ratio - TARGET_PUT_CALL_RATIO) * 0.05
    else:
        n_parity = 0

    return put_wr_lift + 5.0 * min(0, call_regression) + n_parity


def round1_grid():
    """Broad grid around K10_T35 (~50 points)."""
    grid = []
    for k in [0.85, 0.95, 1.0]:
        for wc in [-12.0, -13.0, -14.0, -16.0, -18.0]:
            for lt in [30, 33, 35, 38, 42]:
                grid.append({'k': k, 'wadj_cutoff': wc, 'score_gate': 25,
                             'lift_target': lt, 'use_tanh_weakness': False})
    # Add tanh variants
    for wc in [-13.0, -16.0]:
        for lt in [35, 40]:
            grid.append({'k': 1.0, 'wadj_cutoff': wc, 'score_gate': 25,
                         'lift_target': lt, 'use_tanh_weakness': True})
    return grid


def round2_grid_around(top_var):
    """Tight grid around a top variant (~25 points)."""
    grid = []
    k0 = top_var['k']
    wc0 = top_var['wadj_cutoff']
    lt0 = top_var['lift_target']
    for k in [max(0.7, k0 - 0.05), k0, min(1.0, k0 + 0.05)]:
        for wc in [wc0 - 1.0, wc0, wc0 + 1.0]:
            for lt in [lt0 - 2, lt0, lt0 + 2]:
                grid.append({'k': k, 'wadj_cutoff': wc, 'score_gate': 25,
                             'lift_target': int(lt), 'use_tanh_weakness': False})
    # Score gate variants
    for sg in [25, 28, 30]:
        grid.append({'k': k0, 'wadj_cutoff': wc0, 'score_gate': sg,
                     'lift_target': lt0, 'use_tanh_weakness': False})
    return grid


def fmt_var(v):
    suffix = '_tanhW' if v.get('use_tanh_weakness') else ''
    return f"K{v['k']:.2f}_W{int(v['wadj_cutoff']):d}_T{v['lift_target']:d}_G{v['score_gate']:d}{suffix}"


def evaluate_variants(runner, variants, baseline, label='Round'):
    """Evaluate each variant, return list of (variant, util, stats) sorted by util desc."""
    print(f"\n{'='*120}")
    print(f"{label}: {len(variants)} variants")
    print('='*120)
    print(f"{'Variant':<30}  {'<10 WR':>8}  {'<15 WR':>8}  {'<25 WR':>8}  {'70+ WR':>8}  {'N <25':>8}  {'Util':>7}  {'time':>6}")
    print('-' * 120)

    results = []
    for v in variants:
        t0 = time.time()
        stats = runner.evaluate_floor(**v)
        elapsed = time.time() - t0
        util = _utility(stats, baseline)
        results.append((v, util, stats))

        bs = stats.get('bucketed_stats', {})
        wr10 = bs.get('<10', {}).get('win_rate_15d')
        wr15 = bs.get('<15', {}).get('win_rate_15d')
        wr25 = bs.get('<25', {}).get('win_rate_15d')
        wr70 = bs.get('70+', {}).get('win_rate_15d')
        n25 = bs.get('<25', {}).get('sample_count') or 0
        print(f"{fmt_var(v):<30}  {wr10 or 0:>7.1f}%  {wr15 or 0:>7.1f}%  {wr25 or 0:>7.1f}%  {wr70 or 0:>7.1f}%  {n25:>8}  {util:>+7.2f}  {elapsed:>5.2f}s")

    results.sort(key=lambda x: -x[1])
    return results


def main():
    runner = FastVariantRunner(version_id=None, lookback_days=1825)  # active version
    runner.load()

    # Baseline = K10_T35 (the new reference per user)
    print("\nEstablishing K10_T35 baseline...")
    baseline_stats = runner.evaluate_floor(k=1.0, wadj_cutoff=-13.0, score_gate=25,
                                           lift_target=35, use_tanh_weakness=False)
    bs = baseline_stats['bucketed_stats']
    print(f"  baseline (K10_T35): <10 {bs.get('<10', {}).get('win_rate_15d', 0):.1f}%, "
          f"<15 {bs.get('<15', {}).get('win_rate_15d', 0):.1f}%, "
          f"<25 {bs.get('<25', {}).get('win_rate_15d', 0):.1f}%, "
          f"70+ {bs.get('70+', {}).get('win_rate_15d', 0):.1f}%, "
          f"N <25 {bs.get('<25', {}).get('sample_count') or 0}")

    # Round 1
    r1_results = evaluate_variants(runner, round1_grid(), baseline_stats, 'Round 1')
    print(f"\nRound 1 top 5:")
    for v, u, _ in r1_results[:5]:
        print(f"  {fmt_var(v):<30}  util={u:+.2f}")

    # Round 2: tight grid around top 3 winners
    r2_grid = []
    seen_keys = set()
    for v, u, _ in r1_results[:3]:
        if u <= 0: continue  # skip if not better than baseline
        for sub in round2_grid_around(v):
            key = (sub['k'], sub['wadj_cutoff'], sub['score_gate'], sub['lift_target'], sub['use_tanh_weakness'])
            if key not in seen_keys:
                seen_keys.add(key)
                r2_grid.append(sub)

    if r2_grid:
        r2_results = evaluate_variants(runner, r2_grid, baseline_stats, 'Round 2')
        print(f"\nRound 2 top 5:")
        for v, u, _ in r2_results[:5]:
            print(f"  {fmt_var(v):<30}  util={u:+.2f}")
    else:
        r2_results = []

    # Final: combine and report top 5 overall
    all_results = r1_results + r2_results
    all_results.sort(key=lambda x: -x[1])
    print(f"\n{'='*120}")
    print(f"FINAL TOP 5 (vs K10_T35 baseline)")
    print('='*120)
    for v, u, stats in all_results[:5]:
        bs = stats['bucketed_stats']
        print(f"  {fmt_var(v):<30}  util={u:+.2f}  "
              f"<10={bs.get('<10', {}).get('win_rate_15d', 0):.1f}%  "
              f"<15={bs.get('<15', {}).get('win_rate_15d', 0):.1f}%  "
              f"<25={bs.get('<25', {}).get('win_rate_15d', 0):.1f}%  "
              f"70+={bs.get('70+', {}).get('win_rate_15d', 0):.1f}%  "
              f"N <25={bs.get('<25', {}).get('sample_count') or 0}")


if __name__ == '__main__':
    main()
