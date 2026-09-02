"""
Phase OP2 Phase 4 — RE-RUN under bimodal SL fill model (2026-04-30).

Background: under the prior bounded-fill model (commit 3432fb8, uniform within
[low, sl_level]), Phase 4 found WIDE_SL_MP10 a strict improvement EXCEPT for a
+1-3pp DD breach. The empirical SL-fill audit
(experiments/op2/sl_fill_bias_audit.py) measured 124,872 real bars and
confirmed mean fill position is 0.698 (not the uniform-expected 0.5) — bounded
fill is systematically pessimistic by ~+0.198 of (sl_level - low) range,
overstating SL-fire realized loss by ~5-15% of premium per fire.

This re-run uses the now-shipped bimodal fill model:
  - intraday-trigger SL (open on stop side of barrier): fill at sl_level
  - gap-through SL (open on loss side): sample within gap region (low,open) /
    (open,high)

Goal: determine whether (a) baseline now passes the strict 80% absolute DD
gate under the calibrated MC, OR (b) WIDE_SL_MP10 passes that gate. Either
case would justify a ship.

Focused candidate set (bounded runtime):
  - baseline (current shipped config)
  - WIDE_SL_MP10 (Phase 4 standout)
  - WIDE_SL35 (wider SL alone, to decompose)
  - MP10 (smaller MaxPos alone, to decompose)

Output: experiments/op2/phase4_bimodal_results.json
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

from phase3_canonical_mc import run_one_window, WINDOWS

N_ITER = 300
DD_FLOOR_ABS = 80.0   # strict absolute gate
DD_TOL_REL = 1.0      # cand DD ≤ baseline + 1pp tolerance
COLL_TOL = 2.0


CANDIDATES = {
    'baseline':        {},
    'WIDE_SL35':       {'SL_BASE': -0.35, 'SL_STRESS': -0.40},
    'MP10':            {'MAX_POSITIONS': 10},
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
                      f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  [{r.get('elapsed', 0):.0f}s]")
            results[label] = r
            sys.stdout.flush()
    return results


def evaluate_strict_gate(cand: dict, baseline: dict) -> dict:
    """Strict absolute gate: every window DD ≤ 80%, 0% collapse, 5y/22-now ≥ baseline,
    every annual within ±25% of baseline."""
    diag = {'gates': {}, 'pass': True}
    breaches_dd, breaches_coll, breaches_annual = [], [], []
    for w, r in cand.items():
        if 'error' in r:
            breaches_dd.append(f"{w}:err"); breaches_coll.append(f"{w}:err"); continue
        if r['worst_dd'] > DD_FLOOR_ABS:
            breaches_dd.append(f"{w}:{r['worst_dd']:.1f}%")
        if r['p_coll'] > 0:
            breaches_coll.append(f"{w}:{r['p_coll']:.1f}%")
    diag['gates']['dd_floor_80'] = {'pass': not breaches_dd, 'breaches': breaches_dd}
    diag['gates']['no_collapse'] = {'pass': not breaches_coll, 'breaches': breaches_coll}
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


def evaluate_relative_gate(cand: dict, baseline: dict) -> dict:
    """Relative gate: DD ≤ baseline+1pp, etc. (Phase 4 original gate.)"""
    diag = {'gates': {}, 'pass': True}
    breaches_dd, breaches_coll, breaches_annual = [], [], []
    for w, r in cand.items():
        b = baseline.get(w, {})
        if 'error' in r or 'error' in b:
            breaches_dd.append(f"{w}:err"); breaches_coll.append(f"{w}:err"); continue
        if r['worst_dd'] > b['worst_dd'] + DD_TOL_REL:
            breaches_dd.append(f"{w}:{r['worst_dd']:.1f}>{b['worst_dd']:.1f}+{DD_TOL_REL}")
        if r['p_coll'] > b['p_coll'] + COLL_TOL:
            breaches_coll.append(f"{w}:{r['p_coll']:.1f}>{b['p_coll']:.1f}+{COLL_TOL}")
    diag['gates']['dd_relative_+1pp'] = {'pass': not breaches_dd, 'breaches': breaches_dd}
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
    print(f"PHASE 4 BIMODAL — re-run under conditional bimodal SL fill model")
    print(f"N_ITER={N_ITER}; candidates: {len(CANDIDATES)}")
    print(f"Strict gate: every-window DD ≤ 80%, 0% collapse, 5y+22-now ≥ baseline")
    print(f"Relative gate (fallback): DD ≤ baseline+1pp")
    sys.stdout.flush()

    t0 = time.time()
    results = {}
    for name, cfg in CANDIDATES.items():
        results[name] = run_candidate(name, cfg)
        elapsed_min = (time.time() - t0) / 60
        print(f"  ({elapsed_min:.1f} min elapsed)")
        sys.stdout.flush()

    out = {'baseline': results['baseline'], 'candidates': []}
    for name, cfg in CANDIDATES.items():
        if name == 'baseline':
            continue
        cand = results[name]
        strict = evaluate_strict_gate(cand, results['baseline'])
        relative = evaluate_relative_gate(cand, results['baseline'])
        out['candidates'].append({
            'name': name, 'cfg': cfg, 'mc_results': cand,
            'strict_gate': strict, 'relative_gate': relative,
        })
        print(f"\n  {name}: STRICT {'PASS' if strict['pass'] else 'FAIL'}  RELATIVE {'PASS' if relative['pass'] else 'FAIL'}")
        for gname, gdata in strict['gates'].items():
            print(f"    [strict]   {'PASS' if gdata['pass'] else 'FAIL'} {gname}: {gdata}")
        for gname, gdata in relative['gates'].items():
            print(f"    [relative] {'PASS' if gdata['pass'] else 'FAIL'} {gname}: {gdata}")
        sys.stdout.flush()

    # Baseline self-evaluation against absolute gate (we want to know if baseline
    # alone now passes 80% under the calibrated MC).
    baseline_strict = evaluate_strict_gate(results['baseline'], results['baseline'])
    out['baseline_strict_gate'] = baseline_strict
    print(f"\n  BASELINE SELF-CHECK (strict 80% gate): {'PASS' if baseline_strict['pass'] else 'FAIL'}")
    for gname, gdata in baseline_strict['gates'].items():
        print(f"    [baseline] {'PASS' if gdata['pass'] else 'FAIL'} {gname}: {gdata}")

    out_path = os.path.join(ROOT, 'experiments', 'op2', 'phase4_bimodal_results.json')
    with open(out_path, 'w') as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nSaved -> {out_path}")
    print(f"Total elapsed: {(time.time() - t0)/60:.1f} min")

    print("\n" + "="*100)
    print("BIMODAL FILL — RE-RUN SUMMARY")
    print("="*100)
    passing_strict = [c for c in out['candidates'] if c['strict_gate']['pass']]
    passing_relative = [c for c in out['candidates'] if c['relative_gate']['pass']]
    print(f"Strict gate (absolute 80% DD): {len(passing_strict)}/{len(out['candidates'])} candidates pass")
    print(f"Relative gate (DD ≤ baseline+1pp): {len(passing_relative)}/{len(out['candidates'])} candidates pass")
    print(f"Baseline alone passes strict gate: {baseline_strict['pass']}")
    if passing_strict:
        print("\nSHIP CANDIDATES (strict gate pass):")
        for c in passing_strict:
            print(f"  - {c['name']}: {c['cfg']}")


if __name__ == '__main__':
    main()
