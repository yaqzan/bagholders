"""
Phase OP2 — RE-BASELINE under dead-hold + bimodal SL fill (2026-05-01).

Background: Phase OP2 (2026-04-30) found NO candidate passes the 80% absolute DD
gate, INCLUDING the baseline itself, under bounded+bimodal-fill MC. Baseline ran
81-88% DD on every window with 0-22% collapse rate. Three options were left
pending user decision (relax gate / fix MC / no ship).

On 2026-04-30 (evening) dead-hold post-SL mechanism shipped (Priority #20):
  - DEAD_HOLD_ENABLED=True (default)
  - DEAD_HOLD_TRIGGER_PNL=-0.50
  - DEAD_HOLD_POPOUT_PNL=-0.25 (file value; docs say -0.30 — drift to verify)

Per the Priority #20 Stage-2 validation (N=150 × 8 windows under static-pricing
MC), dead-hold reduced 30 DTE avg DD-C from 79.1% → 71.9% — pulled baseline DD
under the 80% floor on EVERY window. This re-run validates that under the now-
canonical bounded+bimodal MC with dead-hold ON, baseline's DD profile clears
the original 80% absolute gate without any portfolio knob change.

If baseline passes:
  - OP2 closes naturally as "dead-hold was the missing structural fix"
  - Original 80% DD gate is workable again under the new MC architecture
  - WIDE_SL_MP10 candidate worth re-testing for the +1681% 5y compound win

If baseline still fails:
  - Dead-hold doesn't fully resolve. Then we go to A/B/C/D path-pick.

Candidates tested:
  - baseline (current shipped config: B68/F3F=0.50/0.50/ULTRA=0.18, dead-hold ON)
  - WIDE_SL_MP10 (Phase 4 standout: SL=-0.35/-0.40, MaxPos=10, dead-hold ON)

N=300 × 8 windows × 2 candidates. Per-candidate ~1.5h with 4-window parallelism.
Total ~3h. Output: experiments/op2/phase_op2_postdh_results.json
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
    'WIDE_SL_MP10':    {'SL_BASE': -0.35, 'SL_STRESS': -0.40, 'MAX_POSITIONS': 10},
}


def run_candidate(name: str, cfg: dict):
    print(f"\n=== {name}: {cfg} ===", flush=True)
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
                print(f"  {label:<8} ERROR: {r['error']}", flush=True)
            else:
                print(f"  {label:<8} mean_ret={r['mean_ret']:>+15,.1f}%  "
                      f"DD={r['worst_dd']:>5.1f}%  coll={r['p_coll']:>4.1f}%  "
                      f"C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  [{r.get('elapsed', 0):.0f}s]",
                      flush=True)
            results[label] = r
    return results


def evaluate_baseline_gate(baseline_res: dict) -> dict:
    """Strict absolute gate on baseline alone."""
    breaches = []
    collapses = []
    for label, _, _ in WINDOWS:
        r = baseline_res.get(label, {})
        if 'error' in r:
            breaches.append(f"{label}: ERROR")
            continue
        dd = r.get('worst_dd', 100.0)
        coll = r.get('p_coll', 100.0)
        if dd > DD_FLOOR_ABS:
            breaches.append(f"{label}: DD={dd:.1f}% (>80%)")
        if coll > 0.5:
            collapses.append(f"{label}: coll={coll:.1f}%")
    return {
        'pass': len(breaches) == 0,
        'breaches': breaches,
        'collapses': collapses,
    }


def evaluate_candidate_vs_baseline(cand_res: dict, base_res: dict, name: str) -> dict:
    """Standard relative gate from phase4_bimodal."""
    breaches = []
    for label, _, _ in WINDOWS:
        rb = base_res.get(label, {})
        rc = cand_res.get(label, {})
        if 'error' in rc or 'error' in rb:
            breaches.append(f"{label}: ERROR")
            continue
        # DD: cand must be ≤ baseline + 1pp AND under 80% absolute
        dd_b = rb.get('worst_dd', 100.0)
        dd_c = rc.get('worst_dd', 100.0)
        if dd_c > DD_FLOOR_ABS:
            breaches.append(f"{label}: DD={dd_c:.1f}% (>80% abs)")
        elif dd_c > dd_b + DD_TOL_REL:
            breaches.append(f"{label}: DD={dd_c:.1f}% (base {dd_b:.1f}+{DD_TOL_REL}pp)")
        # collapse: must not exceed baseline by 2pp
        coll_b = rb.get('p_coll', 0.0)
        coll_c = rc.get('p_coll', 0.0)
        if coll_c > coll_b + COLL_TOL:
            breaches.append(f"{label}: coll={coll_c:.1f}% (base {coll_b:.1f}+{COLL_TOL}pp)")
    # 25% annual gate
    annual = ['2021', '2022', '2023', '2024', '2025']
    for label in annual:
        rb = base_res.get(label, {})
        rc = cand_res.get(label, {})
        if 'error' in rc or 'error' in rb:
            continue
        rb_ret = rb.get('mean_ret', 0.0)
        rc_ret = rc.get('mean_ret', 0.0)
        if rb_ret > 0 and rc_ret < rb_ret * 0.75:
            pct = ((rc_ret / rb_ret) - 1.0) * 100.0
            breaches.append(f"{label}: ret {pct:+.1f}% vs base (>25%)")
    return {'pass': len(breaches) == 0, 'breaches': breaches}


def main():
    t0 = time.time()
    print(f"=== Phase OP2 RE-BASELINE under dead-hold + bimodal ===", flush=True)
    print(f"N_ITER={N_ITER}, DD_FLOOR_ABS={DD_FLOOR_ABS}%", flush=True)
    print(f"DEAD_HOLD_ENABLED={os.environ.get('DEAD_HOLD_ENABLED', '1 (default)')}", flush=True)
    print(f"Candidates: {list(CANDIDATES.keys())}", flush=True)

    all_results = {}
    for name, cfg in CANDIDATES.items():
        all_results[name] = run_candidate(name, cfg)

    # Verdict reporting
    print("\n\n" + "="*72, flush=True)
    print("VERDICT", flush=True)
    print("="*72, flush=True)

    base = all_results.get('baseline', {})
    base_gate = evaluate_baseline_gate(base)
    print(f"\nBASELINE strict 80% absolute gate: {'PASS ✓' if base_gate['pass'] else 'FAIL ✗'}", flush=True)
    if base_gate['breaches']:
        print(f"  DD breaches: {base_gate['breaches']}", flush=True)
    if base_gate['collapses']:
        print(f"  Collapse breaches: {base_gate['collapses']}", flush=True)

    print("\n\nPER-WINDOW SUMMARY (mean_ret / DD / coll):", flush=True)
    print(f"{'window':<10} {'baseline':<35} {'WIDE_SL_MP10':<35}", flush=True)
    for label, _, _ in WINDOWS:
        cells = []
        for name in CANDIDATES.keys():
            r = all_results.get(name, {}).get(label, {})
            if 'error' in r:
                cells.append(f"ERROR".ljust(34))
            else:
                cells.append(f"{r.get('mean_ret', 0):+12,.0f}% {r.get('worst_dd', 0):>5.1f}% {r.get('p_coll', 0):>4.1f}%".ljust(34))
        print(f"{label:<10} {cells[0]} {cells[1] if len(cells) > 1 else ''}", flush=True)

    if 'WIDE_SL_MP10' in all_results:
        cand_gate = evaluate_candidate_vs_baseline(all_results['WIDE_SL_MP10'], base, 'WIDE_SL_MP10')
        print(f"\nWIDE_SL_MP10 vs baseline relative gate: {'PASS ✓' if cand_gate['pass'] else 'FAIL ✗'}", flush=True)
        if cand_gate['breaches']:
            for b in cand_gate['breaches']:
                print(f"  {b}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'phase_op2_postdh_results.json')
    payload = {
        'n_iter': N_ITER,
        'dead_hold': True,
        'bimodal_fill': True,
        'results': all_results,
        'baseline_gate': base_gate,
        'elapsed_min': round((time.time() - t0) / 60.0, 1),
    }
    if 'WIDE_SL_MP10' in all_results:
        payload['cand_gate'] = evaluate_candidate_vs_baseline(all_results['WIDE_SL_MP10'], base, 'WIDE_SL_MP10')
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nResults saved → {out_path}", flush=True)
    print(f"Total elapsed: {payload['elapsed_min']:.1f} min", flush=True)


if __name__ == '__main__':
    main()
