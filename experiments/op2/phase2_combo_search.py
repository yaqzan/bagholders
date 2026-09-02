"""
Phase OP2 Phase 2 — combinatorial Bayesian-ish search over top axes from Phase 1.

Reads experiments/op2/phase1_results.json. Identifies axes whose best variant
beats baseline utility by a significant margin (Δutility > MIN_AXIS_LIFT).
For those axes, harvests winning value(s) and constructs combination
candidates. Runs ~30 evaluations using a 2-round greedy strategy:

  Round 1: 15 candidates spanning the chosen axes (full grid up to budget)
  Round 2: 15 candidates as perturbations around top-3 from round 1

Same utility function as Phase 1.

Outputs:
- experiments/op2/phase2_results.json
- stdout: ranked combos, top-3 advances to canonical MC

Usage:
    PYTHONIOENCODING=utf-8 python experiments/op2/phase2_combo_search.py
"""
import itertools
import json
import math
import os
import random
import sys
import time
from contextlib import contextmanager
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FROM_DATE = date(2022, 1, 1)
TO_DATE   = date.today()

MIN_AXIS_LIFT     = 1.0    # axis must lift utility by ≥ this to be considered
TOP_K_AXES        = 4      # combine up to 4 axes (Phase 1 winners: SL, HOLD, ULTRA, DD)
ROUND1_BUDGET     = 20
ROUND2_BUDGET     = 15
PERTURB_FRAC      = 0.15   # ±15% around top-K
RANDOM_SEED       = 42


@contextmanager
def _patched(module, **overrides):
    saved = {k: getattr(module, k) for k in overrides if hasattr(module, k)}
    for k, v in overrides.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(module, k, v)


def utility(final_eq, max_dd, initial=50000):
    log_ret = math.log(max(final_eq / initial, 1e-9))
    dd_excess = max(0.0, max_dd - 0.65)
    return log_ret - 200.0 * dd_excess ** 2


# Maps axis -> (param applier function, list of candidate values from Phase 1 + interpolations)
# The applier takes (val, accum_patches, accum_cfg) and mutates them. Returns nothing.
def _ax_dd(val, patches, cfg):
    patches['DD_CIRCUIT_BREAKER'] = val
def _ax_sl(val, patches, cfg):
    cfg['sl_sigma_base']   = val * 1.82 / 0.5
    cfg['sl_sigma_stress'] = (val + 0.05) * 1.82 / 0.5
    cfg['net_sl_base']     = -val + (-0.01) + (-0.013)
    cfg['net_sl_stress']   = -(val + 0.05) + (-0.01) + (-0.013)
def _ax_hold(val, patches, cfg):
    cfg['hold_calendar_days'] = val
def _ax_maxpos(val, patches, cfg):
    cfg['max_positions'] = val
def _ax_ultra(val, patches, cfg):
    cfg['tier_alloc'] = {'95+': val, '85-94': max(0.10, val - 0.06), '80-84': 0.15,
                         '75-79': 0.15, '70-74': 0.0}
def _ax_cascade_midlow(val, patches, cfg):  # symmetric mid=low
    cfg['tier_alloc'] = {'95+': 0.18, '85-94': 0.12, '80-84': val,
                         '75-79': val, '70-74': 0.0}
def _ax_prem(val, patches, cfg):
    cfg['prem_stop_loss'] = -val   # val passed positive, internally flipped
def _ax_realloc_eslow(val, patches, cfg):  # val is min_advantage
    cfg['realloc_strategy']      = 'entry_score_low'
    cfg['realloc_min_advantage'] = val
def _ax_realloc_pnllow(val, patches, cfg):
    cfg['realloc_strategy']      = 'current_pnl_low'
    cfg['realloc_min_advantage'] = val
def _ax_realloc_pnlhigh(val, patches, cfg):
    cfg['realloc_strategy']      = 'current_pnl_high'
    cfg['realloc_min_advantage'] = val
def _ax_realloc_old(val, patches, cfg):
    cfg['realloc_strategy']      = 'days_held_high'
    cfg['realloc_min_advantage'] = val
def _ax_realloc_far(val, patches, cfg):
    cfg['realloc_strategy']      = 'sigma_distance_low'
    cfg['realloc_min_advantage'] = val


# Axis registry: name -> (applier, list of seed values from Phase 1)
AXIS_REGISTRY = {
    'DD':              (_ax_dd,              [0.55, 0.60, 0.65]),
    'SL':              (_ax_sl,              [0.22, 0.25, 0.27]),    # add 0.22 (tighter than tested)
    'HOLD':            (_ax_hold,            [10, 11, 12, 13]),       # add 10, 11 (untested shorter)
    'MAXPOS':          (_ax_maxpos,          [10, 12, 14, 16]),
    'ULTRA':           (_ax_ultra,           [0.12, 0.13, 0.15, 0.18]),
    'CASCADE':         (_ax_cascade_midlow,  [0.15, 0.18, 0.20]),
    'PREM':            (_ax_prem,            [0.40, 0.50, 0.60]),    # absolute value
    'RA_eslow':        (_ax_realloc_eslow,   [3, 5, 8]),
    'RA_pnllow':       (_ax_realloc_pnllow,  [3, 5, 8]),
    'RA_pnlhigh':      (_ax_realloc_pnlhigh, [3, 5, 8]),
    'RA_old':          (_ax_realloc_old,     [3, 5, 8]),
    'RA_far':          (_ax_realloc_far,     [3, 5, 8]),
}


def pick_top_axes(phase1_results: dict, k: int = 3) -> list[tuple[str, float]]:
    """Returns [(axis_name, axis_max_util_lift), ...] sorted desc."""
    candidates = phase1_results['candidates']
    by_axis: dict[str, list[float]] = {}
    baseline_u = next(c['utility'] for c in candidates if c['name'] == 'baseline')
    for c in candidates:
        if 'error' in c or c['axis'] in ('control',):
            continue
        by_axis.setdefault(c['axis'], []).append(c['utility'])
    axis_lifts = []
    for axis, utils in by_axis.items():
        max_u = max(utils)
        lift = max_u - baseline_u
        if lift >= MIN_AXIS_LIFT:
            axis_lifts.append((axis, lift))
    axis_lifts.sort(key=lambda x: -x[1])
    return axis_lifts[:k]


def map_phase1_axis_to_registry(phase1_axis: str) -> list[str]:
    """Phase 1 uses one axis label per group (e.g. 'REALLOC' = 5 strategies).
    Map to registry keys. For REALLOC, identify which strategy variant won
    by checking specific candidate utilities."""
    if phase1_axis == 'REALLOC':
        return ['RA_eslow', 'RA_pnllow', 'RA_pnlhigh', 'RA_old', 'RA_far']
    return [phase1_axis]


def build_candidate(axis_values: dict[str, float]) -> tuple[dict, dict]:
    """Apply each axis value; returns (patches, cfg)."""
    patches = {}
    cfg = {}
    for axis_name, val in axis_values.items():
        applier, _ = AXIS_REGISTRY[axis_name]
        applier(val, patches, cfg)
    return patches, cfg


def evaluate(axis_values: dict[str, float]):
    import backtest_cascade as bt
    from database.models.core import AlgorithmVersion
    patches, cfg = build_candidate(axis_values)
    t0 = time.time()
    with _patched(bt, **patches):
        try:
            res = bt.run_cascade_backtest(
                version_id=AlgorithmVersion.get_active_scores_version().id,
                from_date=FROM_DATE,
                to_date=TO_DATE,
                initial=50_000,
                verbose=False,
                cfg=dict(cfg),
            )
        except Exception as e:
            return {'axis_values': axis_values, 'error': str(e)}
    elapsed = time.time() - t0
    if not res or 'final_equity' not in res:
        return {'axis_values': axis_values, 'error': 'empty result'}
    return {
        'axis_values': dict(axis_values),
        'patches': dict(patches),
        'cfg': dict(cfg),
        'final_eq':   res['final_equity'],
        'max_dd_pct': res['max_dd'] * 100,
        'utility':    utility(res['final_equity'], res['max_dd']),
        'elapsed_sec': round(elapsed, 1),
    }


def perturb_value(axis_name: str, val):
    """Return a slightly-perturbed copy of val (handles int/float types)."""
    rng = random.Random()
    spec = AXIS_REGISTRY[axis_name][1]
    if isinstance(val, int):
        # round to nearest int after perturbation
        new = int(round(val * (1 + rng.uniform(-PERTURB_FRAC, PERTURB_FRAC))))
        # snap to closest seed value to avoid wild excursions
        return min(spec, key=lambda x: abs(x - new))
    return float(val * (1 + rng.uniform(-PERTURB_FRAC, PERTURB_FRAC)))


def main():
    random.seed(RANDOM_SEED)
    p1_path = os.path.join(ROOT, 'experiments', 'op2', 'phase1_results.json')
    if not os.path.exists(p1_path):
        print(f"ERROR: phase1 results not found at {p1_path}. Run phase1_axis_screen.py first.")
        sys.exit(1)
    with open(p1_path) as fh:
        p1 = json.load(fh)

    top_axes_p1 = pick_top_axes(p1, k=TOP_K_AXES)
    print(f"PHASE 2 — top axes from Phase 1:")
    for axis, lift in top_axes_p1:
        print(f"  {axis:<10} (utility lift = {lift:+.2f})")

    # Map Phase 1 axes to registry keys (REALLOC expands to 5 strategies; pick best one by util)
    p1_cands = p1['candidates']
    chosen_axes: list[str] = []
    for p1_axis, _ in top_axes_p1:
        registry_keys = map_phase1_axis_to_registry(p1_axis)
        if len(registry_keys) == 1:
            chosen_axes.append(registry_keys[0])
        else:
            # Pick the registry variant whose corresponding Phase 1 candidate had the highest util
            # e.g., REALLOC -> RA_eslow if 'RA_eslow' was the highest realloc variant
            best_variant = None
            best_util = -1e18
            for c in p1_cands:
                if 'error' in c:
                    continue
                # Phase 1 candidate names: 'RA_eslow', 'RA_pnlhi', 'RA_pnllo', 'RA_old', 'RA_far'
                # Map to registry key (note: 'RA_pnlhi' vs 'RA_pnlhigh' naming difference)
                name_to_reg = {
                    'RA_eslow':  'RA_eslow',
                    'RA_pnlhi':  'RA_pnlhigh',
                    'RA_pnllo':  'RA_pnllow',
                    'RA_old':    'RA_old',
                    'RA_far':    'RA_far',
                }
                if c['name'] in name_to_reg and c['utility'] > best_util:
                    best_util = c['utility']
                    best_variant = name_to_reg[c['name']]
            if best_variant:
                chosen_axes.append(best_variant)

    chosen_axes = chosen_axes[:TOP_K_AXES]   # cap at TOP_K_AXES
    print(f"\nChosen registry axes: {chosen_axes}")
    if not chosen_axes:
        print("No axes meet MIN_AXIS_LIFT threshold. Exiting.")
        sys.exit(0)

    # ── Round 1: full grid up to budget ───────────────────────────────
    grids = [AXIS_REGISTRY[a][1] for a in chosen_axes]
    full_grid = list(itertools.product(*grids))
    if len(full_grid) > ROUND1_BUDGET:
        random.shuffle(full_grid)
        round1 = full_grid[:ROUND1_BUDGET]
    else:
        round1 = full_grid
    print(f"\n=== ROUND 1: {len(round1)} candidates ===")

    results = []
    for idx, vals in enumerate(round1):
        axis_values = dict(zip(chosen_axes, vals))
        r = evaluate(axis_values)
        if 'error' in r:
            print(f"  R1[{idx+1:>2}] {axis_values}  ERROR: {r['error']}")
        else:
            mult = r['final_eq'] / 50_000
            print(f"  R1[{idx+1:>2}] {axis_values}  eq=${r['final_eq']:>16,.0f} ({mult:.2e}×)  "
                  f"DD={r['max_dd_pct']:>5.1f}%  util={r['utility']:+.2f}  [{r['elapsed_sec']}s]")
        results.append(r)
        sys.stdout.flush()

    # ── Round 2: perturb top-3 ────────────────────────────────────────
    valid_r1 = [r for r in results if 'error' not in r]
    valid_r1.sort(key=lambda r: -r['utility'])
    top3_r1 = valid_r1[:3]
    print(f"\n=== ROUND 2: perturbations around top-3 ===")

    round2 = []
    for idx, base in enumerate(top3_r1):
        for _ in range(ROUND2_BUDGET // 3 + 1):
            new_vals = {a: perturb_value(a, base['axis_values'][a]) for a in chosen_axes}
            round2.append(new_vals)
    round2 = round2[:ROUND2_BUDGET]

    for idx, axis_values in enumerate(round2):
        r = evaluate(axis_values)
        if 'error' in r:
            print(f"  R2[{idx+1:>2}] {axis_values}  ERROR: {r['error']}")
        else:
            mult = r['final_eq'] / 50_000
            print(f"  R2[{idx+1:>2}] {axis_values}  eq=${r['final_eq']:>16,.0f} ({mult:.2e}×)  "
                  f"DD={r['max_dd_pct']:>5.1f}%  util={r['utility']:+.2f}  [{r['elapsed_sec']}s]")
        results.append(r)
        sys.stdout.flush()

    # ── Save + ranked report ──────────────────────────────────────────
    out_path = os.path.join(ROOT, 'experiments', 'op2', 'phase2_results.json')
    with open(out_path, 'w') as fh:
        json.dump({
            'window':       f'{FROM_DATE} to {TO_DATE}',
            'chosen_axes':  chosen_axes,
            'baseline_util': next(c['utility'] for c in p1_cands if c['name'] == 'baseline'),
            'candidates':   results,
        }, fh, indent=2, default=str)

    valid = [r for r in results if 'error' not in r]
    valid.sort(key=lambda r: -r['utility'])
    print("\n" + "=" * 110)
    print("PHASE 2 — TOP 10 by utility")
    print("=" * 110)
    for i, r in enumerate(valid[:10], 1):
        av_str = ', '.join(f"{k}={v}" for k, v in r['axis_values'].items())
        print(f"  {i:>3} util={r['utility']:+.2f}  eq=${r['final_eq']:>14,.0f}  "
              f"DD={r['max_dd_pct']:>5.1f}%  [{av_str}]")

    print(f"\nSaved -> {out_path}")
    print("\nTop-3 candidates advance to Phase 3 (canonical MC validation).")


if __name__ == '__main__':
    main()
