"""
Portfolio v32 — Phase 1: allocation/priority/MaxPos screening at 22-now × N=100.

User explicitly asked (2026-05-01): "compounding is absurd so maybe we can reduce
that in exchange for safer DD". This sweep explores the Pareto frontier — testing
allocation reductions, MaxPos variations, and priority modes against 22-now MC.

Top-3 by relative-improvement utility → Phase 2 4-window N=150 validation.
Top-1 → Phase 3 8-window N=300 canonical (P1-P6 ship gate).

Architecture: subprocess-per-config via op2_runner.py (Windows MP pool isolation).
All configs run in parallel via ProcessPoolExecutor (max_workers=4).

Window: 22-now (Jan 2022 → Apr 30 2026) — most diverse single window for
screening; covers bear/recovery/bull/dip; rankings here transfer to 5y at 70%+.

Utility = log(mean_ret_multiplier) - DD_PENALTY * max(0, dd - baseline_dd)²
       where baseline_dd is the actual baseline DD (not absolute 80% floor).
       This penalizes candidates that worsen DD vs baseline; rewards tighter DD.
"""
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'experiments', 'op2'))

from phase3_canonical_mc import run_one_window

WINDOW_LABEL = '22-now'
WINDOW_START = date(2022, 1, 1)
WINDOW_END   = date(2026, 4, 30)
N_ITER = 100
MAX_PARALLEL = 4

# ── Candidate config space ─────────────────────────────────────────────────
# Each candidate is a dict matching op2_runner._apply_config keys.
# Production baseline (for reference):
#   TIER_ALLOC = {ultra:0.18, top:0.12, mid:0.15, low:0.15, overflow:0.0}
#   PUT_TIER_ALLOC = {put_top:0.10, put_mid:0.12, put_low:0.12}
#   MAX_POSITIONS = 14
#   PUT_PRIORITY = 'calls_first'

CANDIDATES = []

# === Group A: BASELINE (control) ===
CANDIDATES.append({'name': 'baseline', 'group': 'control', 'cfg': {}})

# === Group B: DEFENSIVE ALLOCATION (user's "lower compounding for safer DD") ===
# Cut all tiers proportionally
CANDIDATES.append({'name': 'B_alloc_x0.85', 'group': 'defensive', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.153, 'top': 0.102, 'mid': 0.128, 'low': 0.128, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.085, 'put_mid': 0.102, 'put_low': 0.102},
}})
CANDIDATES.append({'name': 'B_alloc_x0.75', 'group': 'defensive', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.135, 'top': 0.090, 'mid': 0.113, 'low': 0.113, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.075, 'put_mid': 0.090, 'put_low': 0.090},
}})
CANDIDATES.append({'name': 'B_alloc_x0.65', 'group': 'defensive', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.117, 'top': 0.078, 'mid': 0.098, 'low': 0.098, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.065, 'put_mid': 0.078, 'put_low': 0.078},
}})

# === Group C: ULTRA-only reductions (concentrated cut) ===
CANDIDATES.append({'name': 'C_ultra_15', 'group': 'ultra', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.15, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.0},
}})
CANDIDATES.append({'name': 'C_ultra_12', 'group': 'ultra', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.12, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.0},
}})
CANDIDATES.append({'name': 'C_ultra_10', 'group': 'ultra', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.10, 'top': 0.12, 'mid': 0.15, 'low': 0.15, 'overflow': 0.0},
}})
CANDIDATES.append({'name': 'C_ultra_22', 'group': 'ultra', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.22, 'top': 0.13, 'mid': 0.15, 'low': 0.15, 'overflow': 0.0},
}})

# === Group D: MID/LOW tier reductions (broad volume cut) ===
CANDIDATES.append({'name': 'D_midlow_12', 'group': 'midlow', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
}})
CANDIDATES.append({'name': 'D_midlow_10', 'group': 'midlow', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.10, 'low': 0.10, 'overflow': 0.0},
}})
CANDIDATES.append({'name': 'D_midlow_08', 'group': 'midlow', 'cfg': {
    'TIER_ALLOC': {'ultra': 0.18, 'top': 0.12, 'mid': 0.08, 'low': 0.08, 'overflow': 0.0},
}})

# === Group E: PUT cascade reductions ===
CANDIDATES.append({'name': 'E_put_x0.75', 'group': 'put_alloc', 'cfg': {
    'PUT_TIER_ALLOC': {'put_top': 0.075, 'put_mid': 0.090, 'put_low': 0.090},
}})
CANDIDATES.append({'name': 'E_put_x0.50', 'group': 'put_alloc', 'cfg': {
    'PUT_TIER_ALLOC': {'put_top': 0.050, 'put_mid': 0.060, 'put_low': 0.060},
}})
CANDIDATES.append({'name': 'E_put_concentrate', 'group': 'put_alloc', 'cfg': {
    # Heavier on extreme puts, lighter on weaker
    'PUT_TIER_ALLOC': {'put_top': 0.15, 'put_mid': 0.10, 'put_low': 0.08},
}})

# === Group F: MAX_POSITIONS variations ===
CANDIDATES.append({'name': 'F_maxpos_10', 'group': 'maxpos', 'cfg': {'MAX_POSITIONS': 10}})
CANDIDATES.append({'name': 'F_maxpos_12', 'group': 'maxpos', 'cfg': {'MAX_POSITIONS': 12}})
CANDIDATES.append({'name': 'F_maxpos_16', 'group': 'maxpos', 'cfg': {'MAX_POSITIONS': 16}})
CANDIDATES.append({'name': 'F_maxpos_18', 'group': 'maxpos', 'cfg': {'MAX_POSITIONS': 18}})

# === Group G: PRIORITY intermix ===
# Note: 'merged' = unify queue by abs(score-50). 'wr_merged' needs WR_PRIORITY_TABLE
# populated; calls_first is current production.
CANDIDATES.append({'name': 'G_pri_merged', 'group': 'priority', 'cfg': {'PUT_PRIORITY': 'merged'}})
CANDIDATES.append({'name': 'G_pri_puts_first', 'group': 'priority', 'cfg': {'PUT_PRIORITY': 'puts_first'}})

# === Group H: COMBOS — defensive alloc + smaller MaxPos (synergy hunt) ===
CANDIDATES.append({'name': 'H_x0.75_MP12', 'group': 'combo', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.135, 'top': 0.090, 'mid': 0.113, 'low': 0.113, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.075, 'put_mid': 0.090, 'put_low': 0.090},
    'MAX_POSITIONS':  12,
}})
CANDIDATES.append({'name': 'H_x0.85_MP12', 'group': 'combo', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.153, 'top': 0.102, 'mid': 0.128, 'low': 0.128, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.085, 'put_mid': 0.102, 'put_low': 0.102},
    'MAX_POSITIONS':  12,
}})
CANDIDATES.append({'name': 'H_ultra15_MP12', 'group': 'combo', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.15, 'top': 0.12, 'mid': 0.13, 'low': 0.13, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.090, 'put_mid': 0.108, 'put_low': 0.108},
    'MAX_POSITIONS':  12,
}})
CANDIDATES.append({'name': 'H_ultra12_MP10', 'group': 'combo', 'cfg': {
    'TIER_ALLOC':     {'ultra': 0.12, 'top': 0.10, 'mid': 0.12, 'low': 0.12, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.075, 'put_mid': 0.090, 'put_low': 0.090},
    'MAX_POSITIONS':  10,
}})

# === Group I: WIDE-SL combos (Phase 4 finding under bimodal MC) ===
# OP2 found WIDE_SL_MP10 had +159% to +1046% on 22-now compound but
# introduces dip-window collapse risk under unmodified bounded fill.
# Now with dead-hold shipped + bimodal fill, retest with smaller MaxPos.
CANDIDATES.append({'name': 'I_widesl_MP12', 'group': 'widesl', 'cfg': {
    'SL_BASE': -0.35, 'SL_STRESS': -0.40, 'MAX_POSITIONS': 12,
}})
CANDIDATES.append({'name': 'I_widesl_MP10', 'group': 'widesl', 'cfg': {
    'SL_BASE': -0.35, 'SL_STRESS': -0.40, 'MAX_POSITIONS': 10,
}})
CANDIDATES.append({'name': 'I_widesl_x0.85', 'group': 'widesl', 'cfg': {
    'SL_BASE': -0.35, 'SL_STRESS': -0.40,
    'TIER_ALLOC': {'ultra': 0.153, 'top': 0.102, 'mid': 0.128, 'low': 0.128, 'overflow': 0.0},
    'PUT_TIER_ALLOC': {'put_top': 0.085, 'put_mid': 0.102, 'put_low': 0.102},
}})


def utility(mean_ret_pct, worst_dd_pct, p_coll_pct, baseline_mean_ret, baseline_dd):
    """Relative utility vs baseline.

    log_ratio = log((1+mean_ret/100) / (1+baseline_mean_ret/100))
    dd_penalty = 50 * max(0, dd - baseline_dd)² (rel-DD penalty: DD worse than baseline)
    coll_penalty = 5 * p_coll
    """
    if mean_ret_pct is None or worst_dd_pct is None:
        return -999
    base_mult = max(1.0 + baseline_mean_ret / 100.0, 1e-9)
    cand_mult = max(1.0 + mean_ret_pct / 100.0, 1e-9)
    log_ratio = math.log(cand_mult / base_mult)
    dd_excess = max(0.0, worst_dd_pct - baseline_dd) / 100.0
    dd_penalty = 50.0 * dd_excess ** 2
    coll_penalty = 0.05 * (p_coll_pct or 0.0)
    return log_ratio - dd_penalty - coll_penalty


def main():
    print(f"Phase 1 — {len(CANDIDATES)} candidates × N={N_ITER} × {WINDOW_LABEL}")
    print(f"Parallel: {MAX_PARALLEL} workers\n", flush=True)

    t0 = time.time()
    results = {}
    out_path = os.path.join(ROOT, 'experiments', 'portfolio_v32', 'phase1_screen_results.json')

    with ProcessPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futs = {}
        for cand in CANDIDATES:
            f = ex.submit(run_one_window, cand['cfg'], WINDOW_LABEL,
                          WINDOW_START, WINDOW_END, N_ITER)
            futs[f] = cand
        completed = 0
        for f in as_completed(futs):
            cand = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'error': str(e)}
            completed += 1
            elapsed_total = time.time() - t0
            if 'error' in r:
                print(f"  [{completed:>2}/{len(CANDIDATES)}] {cand['name']:<22} "
                      f"({cand['group']:<10}) ERROR: {r['error'][:80]} "
                      f"[{elapsed_total/60:.1f}m total]", flush=True)
            else:
                print(f"  [{completed:>2}/{len(CANDIDATES)}] {cand['name']:<22} "
                      f"({cand['group']:<10}) ret={r.get('mean_ret', 0):>+15,.1f}%  "
                      f"DD={r.get('worst_dd', 0):>5.1f}%  coll={r.get('p_coll', 0):.1f}%  "
                      f"C/P=({r.get('call_tp', 0):.1f}/{r.get('put_tp', 0):.1f})  "
                      f"[{r.get('elapsed', 0):.0f}s, {elapsed_total/60:.1f}m total]",
                      flush=True)
            results[cand['name']] = {'cand': cand, 'result': r}
            with open(out_path, 'w') as fh:
                json.dump({'window': WINDOW_LABEL, 'n_iter': N_ITER, 'results': results},
                          fh, indent=2, default=str)

    # ── Analysis ─────────────────────────────────────────────────────────
    base = results.get('baseline', {}).get('result', {})
    base_ret = base.get('mean_ret', 0.0)
    base_dd = base.get('worst_dd', 0.0)

    print(f"\n{'=' * 110}")
    print(f"BASELINE: ret={base_ret:>+15,.1f}%  DD={base_dd:.1f}%  coll={base.get('p_coll', 0):.1f}%")
    print(f"{'=' * 110}")

    ranking = []
    for name, entry in results.items():
        r = entry['result']
        if 'error' in r:
            continue
        u = utility(r.get('mean_ret'), r.get('worst_dd'), r.get('p_coll', 0), base_ret, base_dd)
        ranking.append((u, name, entry['cand']['group'], r))

    ranking.sort(reverse=True)

    print(f"\n{'rank':>4} {'name':<22} {'group':<10} {'mean_ret':>16} {'DD':>6} {'coll':>5} {'util':>8}  Δret%  ΔDDpp")
    for i, (u, name, group, r) in enumerate(ranking, 1):
        ret = r.get('mean_ret', 0)
        dd = r.get('worst_dd', 0)
        coll = r.get('p_coll', 0)
        d_ret_mult = (1 + ret/100) / max(1 + base_ret/100, 1e-9) - 1
        d_dd = dd - base_dd
        print(f"  {i:>2} {name:<22} {group:<10} {ret:>+15,.1f}% {dd:>5.1f}% {coll:>4.1f}% "
              f"{u:>+8.3f}  {d_ret_mult*100:>+6.1f}%  {d_dd:>+5.1f}pp")

    print(f"\nElapsed: {(time.time() - t0)/60:.1f} min")
    print(f"Saved -> {out_path}")


if __name__ == '__main__':
    main()
