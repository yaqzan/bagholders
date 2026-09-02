"""
Phase OP2 Phase 4 — variance-reducing candidates with RELATIVE ship gate.

CONTEXT: Phase 3 revealed that the 80% absolute DD gate was calibrated for
the old 3-mode Conservative MC. Under bounded-fill seeded MC (commit 3432fb8),
baseline DD runs 12pp higher than the old gate could accommodate. Baseline
itself shows 87.7% DD on 5y, 22.3% collapse on 2022, etc. — fails its own
absolute gate.

This phase explores the OPPOSITE direction (less aggressive, lower variance)
with a RELATIVE gate: candidate must beat baseline on:
  - 5y compound ≥ baseline 5y AND
  - 22-now compound ≥ baseline 22-now AND
  - per-window DD ≤ baseline DD + 1pp (tolerance) AND
  - per-window collapse ≤ baseline collapse + 2pp AND
  - every annual within ±25%

Candidates target variance reduction:
  - smaller MAX_POSITIONS (less correlated DD across pool)
  - WIDER call SL (-0.35) — fewer SL fires = less bounded-fill randomness
  - looser DD_CIRCUIT_BREAKER (0.75) — let trades resolve without forced pause
  - smaller ULTRA tier (less concentration)
  - keep HOLD at 15 (longer = less SL fires)

Output: experiments/op2/phase4_results.json
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reuse Phase 3 helpers
from phase3_canonical_mc import run_one_window, WINDOWS

N_ITER = 300
DD_TOL = 1.0      # cand DD ≤ baseline DD + 1pp
COLL_TOL = 2.0    # cand collapse ≤ baseline collapse + 2pp


# ── Candidates: variance-reducing direction ──────────────────────────
CANDIDATES = {
    'baseline_v2':     {},
    'MP10':            {'MAX_POSITIONS': 10},
    'MP12':            {'MAX_POSITIONS': 12},
    'WIDE_SL35':       {'SL_BASE': -0.35, 'SL_STRESS': -0.40},
    'LOOSE_DD75':      {'DD_CIRCUIT_BREAKER': 0.75},
    'LOW_ULTRA':       {'TIER_ALLOC': {'ultra':0.13,'top':0.10,'mid':0.15,'low':0.15,'overflow':0.0}},
    'CASC2020':        {'TIER_ALLOC': {'ultra':0.18,'top':0.12,'mid':0.20,'low':0.20,'overflow':0.0}},
    'COMBO_MP10_DD60': {'MAX_POSITIONS': 10, 'DD_CIRCUIT_BREAKER': 0.60},
    'COMBO_LOWER_ALL': {'MAX_POSITIONS': 12, 'TIER_ALLOC': {'ultra':0.13,'top':0.10,'mid':0.13,'low':0.13,'overflow':0.0}, 'DD_CIRCUIT_BREAKER': 0.65},
    'WIDE_SL_MP10':    {'SL_BASE': -0.35, 'SL_STRESS': -0.40, 'MAX_POSITIONS': 10},
}


def run_candidate(name: str, cfg: dict):
    print(f"\n=== {name}: {cfg} ===")
    sys.stdout.flush()
    futs = {}
    with ProcessPoolExecutor(max_workers=4) as ex:
        for label, d_start, d_end in WINDOWS:
            futs[ex.submit(run_one_window, cfg, label, d_start, d_end, N_ITER)] = label
        results = {}
        for f in as_completed(futs):
            label = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'error': str(e)}
            if 'error' in r:
                print(f"  {label:<8} ERROR: {r['error']}")
            else:
                print(f"  {label:<8} mean_ret={r['mean_ret']:>+15,.1f}%  "
                      f"DD={r['worst_dd']:>5.1f}%  coll={r['p_coll']:>4.1f}%  "
                      f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})")
            results[label] = r
            sys.stdout.flush()
    return results


def evaluate_relative_gate(cand: dict, baseline: dict) -> dict:
    diag = {'gates': {}, 'pass': True}
    breaches_dd, breaches_coll, breaches_annual = [], [], []
    for w, r in cand.items():
        b = baseline.get(w, {})
        if 'error' in r or 'error' in b:
            breaches_dd.append(f"{w}:err"); breaches_coll.append(f"{w}:err"); continue
        if r['worst_dd'] > b['worst_dd'] + DD_TOL:
            breaches_dd.append(f"{w}:{r['worst_dd']:.1f}>{b['worst_dd']:.1f}+{DD_TOL}")
        if r['p_coll'] > b['p_coll'] + COLL_TOL:
            breaches_coll.append(f"{w}:{r['p_coll']:.1f}>{b['p_coll']:.1f}+{COLL_TOL}")
    diag['gates']['dd_relative'] = {'pass': not breaches_dd, 'breaches': breaches_dd}
    diag['gates']['collapse_relative'] = {'pass': not breaches_coll, 'breaches': breaches_coll}
    cand_5y = cand.get('5y', {}).get('mean_ret'); base_5y = baseline.get('5y', {}).get('mean_ret')
    diag['gates']['5y_compound'] = {
        'pass': cand_5y is not None and base_5y is not None and cand_5y >= base_5y,
        'cand': cand_5y, 'baseline': base_5y,
    }
    cand_22 = cand.get('22-now', {}).get('mean_ret'); base_22 = baseline.get('22-now', {}).get('mean_ret')
    diag['gates']['22-now_compound'] = {
        'pass': cand_22 is not None and base_22 is not None and cand_22 >= base_22,
        'cand': cand_22, 'baseline': base_22,
    }
    for w in ['2021','2022','2023','2024','2025']:
        cr = cand.get(w, {}).get('mean_ret'); br = baseline.get(w, {}).get('mean_ret')
        if cr is None or br is None:
            breaches_annual.append(f"{w}:err"); continue
        if br > 0 and cr < 0.75 * br:
            breaches_annual.append(f"{w}:Δ{(cr/br-1)*100:.1f}%")
    diag['gates']['annual_25pct'] = {'pass': not breaches_annual, 'breaches': breaches_annual}
    diag['pass'] = all(g['pass'] for g in diag['gates'].values())
    return diag


def main():
    print(f"PHASE 4 — variance reduction with RELATIVE ship gate")
    print(f"DD tolerance: +{DD_TOL}pp vs baseline   Collapse tolerance: +{COLL_TOL}pp vs baseline")
    print(f"Candidates: {len(CANDIDATES)}")

    print(f"\n{'='*100}\nBASELINE\n{'='*100}")
    baseline = run_candidate('baseline_v2', {})

    out = {'baseline': baseline, 'candidates': []}
    for name, cfg in CANDIDATES.items():
        if name == 'baseline_v2':
            continue
        cand = run_candidate(name, cfg)
        gate = evaluate_relative_gate(cand, baseline)
        out['candidates'].append({'name': name, 'cfg': cfg, 'mc_results': cand, 'gate': gate})
        print(f"\n  RELATIVE GATE: {'PASS' if gate['pass'] else 'FAIL'}")
        for gname, gdata in gate['gates'].items():
            print(f"    {'PASS' if gdata['pass'] else 'FAIL'} {gname}: {gdata}")
        sys.stdout.flush()

    out_path = os.path.join(ROOT, 'experiments', 'op2', 'phase4_results.json')
    with open(out_path, 'w') as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nSaved -> {out_path}")

    print("\n" + "="*100)
    print("PHASE 4 — RELATIVE GATE SUMMARY")
    print("="*100)
    passing = [c for c in out['candidates'] if c['gate']['pass']]
    print(f"Passing relative gate: {len(passing)} of {len(out['candidates'])}")
    if passing:
        for c in passing:
            print(f"  - {c['name']}: {c['cfg']}")


if __name__ == '__main__':
    main()
