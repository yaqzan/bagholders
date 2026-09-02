"""SAW Put U-curve 15 DTE — Phase C drill (N=300 x 8-window validation).

Reads Phase B's `phase_b_results.jsonl` and runs the top-K candidates plus
baseline at N=300 over all 8 canonical windows. Applies the Stage 3 ship
gate (T1-T7) per assessment-backtest.md.

Default K=5. Override via TOPK env var.

Output:
  experiments/saw_put_ucurve_15dte/phase_c_results.jsonl
  experiments/saw_put_ucurve_15dte/phase_c.log

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/saw_put_ucurve_15dte/sweep_phase_c.py 2>&1 \\
    | tee experiments/saw_put_ucurve_15dte/phase_c.log
"""
from __future__ import annotations
import json, math, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo_15dte as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# ---------- 8-window canonical (matches 30 DTE Phase C) ----------
SWEEP_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 15)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]

PHASE_B_RESULTS = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte',
                                'phase_b_results.jsonl')


def apply_cfg(cand, n_iter):
    if not cand.get('enabled', True):
        os.environ['SAW_PUT_UCURVE_ENABLED'] = '0'
        mc.SAW_PUT_UCURVE_ENABLED = 0
    else:
        p = cand['params']
        os.environ['SAW_PUT_UCURVE_ENABLED']  = '1'
        os.environ['SAW_PUT_UCURVE_SHAPE']    = p['shape']
        os.environ['SAW_PUT_UCURVE_MIDPOINT'] = str(p['midpoint'])
        os.environ['SAW_PUT_UCURVE_HALFWIDTH']= str(p['half_width'])
        os.environ['SAW_PUT_UCURVE_FLOOR']    = str(p['floor'])
        os.environ['SAW_PUT_UCURVE_CEIL']     = str(p['ceil'])
        if p['shape'] == 'quadratic':
            os.environ['SAW_PUT_UCURVE_POWER'] = str(p['power_k'])
            os.environ['SAW_PUT_UCURVE_K']     = '5.0'
        else:
            sig_k = {1.5:3.0, 2.0:5.0, 2.5:7.0, 3.0:9.0, 4.0:12.0}[p['power_k']]
            os.environ['SAW_PUT_UCURVE_K']     = str(sig_k)
            os.environ['SAW_PUT_UCURVE_POWER'] = '2.0'
        mc.SAW_PUT_UCURVE_ENABLED   = 1
        mc.SAW_PUT_UCURVE_SHAPE     = p['shape']
        mc.SAW_PUT_UCURVE_MIDPOINT  = float(p['midpoint'])
        mc.SAW_PUT_UCURVE_HALFWIDTH = float(p['half_width'])
        mc.SAW_PUT_UCURVE_FLOOR     = float(p['floor'])
        mc.SAW_PUT_UCURVE_CEIL      = float(p['ceil'])
        if p['shape'] == 'quadratic':
            mc.SAW_PUT_UCURVE_POWER = float(p['power_k'])
        else:
            mc.SAW_PUT_UCURVE_K     = float({1.5:3.0, 2.0:5.0, 2.5:7.0, 3.0:9.0, 4.0:12.0}[p['power_k']])
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']


def run_candidate(cand, version, n_iter):
    apply_cfg(cand, n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':    float(r.get('mean_ret', 0.0)),
            'med_ret':     float(r.get('med_ret', 0.0)),
            'worst_dd':    float(r.get('worst_dd', 0.0)),
            'mean_dd':     float(r.get('mean_dd', 0.0)),
            'p_collapse':  float(r.get('p_coll', 0.0)),
            'call_tp':     float(r.get('call_tp', 0.0)),
            'put_tp':      float(r.get('put_tp', 0.0)),
            'call_trades': float(r.get('call_trades', 0.0)),
            'put_trades':  float(r.get('put_trades', 0.0)),
        }
    return out


def evaluate_t_gates(cand_res, base_res):
    """Apply T1-T7 Stage 3 ship gates. Returns dict of pass/fail flags."""
    cand_5y_dd = cand_res['5y']['worst_dd'] / 100.0
    base_5y_dd = base_res['5y']['worst_dd'] / 100.0
    cand_5y_ret = cand_res['5y']['mean_ret']
    base_5y_ret = base_res['5y']['mean_ret']

    max_collapse = max(r['p_collapse'] / 100.0 for r in cand_res.values())

    # T4: 5y WorstDD ≤ baseline + 1.0pp
    t4_pass = (cand_5y_dd - base_5y_dd) <= 0.010

    # T5: per-window DD stability (no annual DD regression > 5pp)
    annual_breaches = []
    for label in ['2021', '2022', '2023', '2024', '2025', 'dip']:
        c_dd = cand_res[label]['worst_dd'] / 100.0
        b_dd = base_res[label]['worst_dd'] / 100.0
        if (c_dd - b_dd) > 0.050:
            annual_breaches.append((label, c_dd - b_dd))
    t5_pass = len(annual_breaches) == 0

    # T6: 0% collapse on every cell
    t6_pass = max_collapse == 0.0

    # T7: 5y compound non-regression sanity (one-sided — only fail on
    # downside REGRESSION beyond -3 OOMs vs baseline). Upside is acceptable
    # (compound is theoretical above ~1e10%; large gains may be MC noise tail).
    if cand_5y_ret > -99.0 and base_5y_ret > -99.0:
        cand_log = math.log10(max(1.001, 1.0 + cand_5y_ret/100.0))
        base_log = math.log10(max(1.001, 1.0 + base_5y_ret/100.0))
        t7_diff = cand_log - base_log
        t7_pass = t7_diff >= -3.0   # only fail on regression beyond -3 OOMs
    else:
        t7_diff = -99.0
        t7_pass = False

    # Soft signals
    dd_5y_delta = cand_5y_dd - base_5y_dd
    dd_22n_delta = cand_res['22-now']['worst_dd'] / 100.0 - base_res['22-now']['worst_dd'] / 100.0

    return {
        'T4_5y_dd_pass': t4_pass, 'T5_annual_dd_pass': t5_pass,
        'T6_collapse_pass': t6_pass, 'T7_compound_oom_pass': t7_pass,
        'all_T_pass': t4_pass and t5_pass and t6_pass and t7_pass,
        'dd_5y_delta_pp': dd_5y_delta * 100,
        'dd_22n_delta_pp': dd_22n_delta * 100,
        'compound_oom_diff': t7_diff,
        'annual_dd_breaches': annual_breaches,
    }


def main():
    log_dir = os.path.join(ROOT, 'experiments', 'saw_put_ucurve_15dte')
    log_path = os.path.join(log_dir, 'phase_c_results.jsonl')

    TOPK = int(os.environ.get('TOPK', '5'))
    N_ITER = int(os.environ.get('N_ITER', '300'))
    print(f"SAW Put U-curve 15 DTE Phase C — TOPK={TOPK}, N_ITER={N_ITER}, "
          f"windows={len(SWEEP_WINDOWS)}")

    if not os.path.exists(PHASE_B_RESULTS):
        print(f"ERROR: Phase B results not found at {PHASE_B_RESULTS}")
        print("Run sweep_phase_b.py first.")
        sys.exit(1)

    # Load Phase B candidates
    candidates = []
    with open(PHASE_B_RESULTS, 'r') as f:
        for line in f:
            rec = json.loads(line)
            if rec['phase'] == 'baseline':
                continue
            u = rec['utility_breakdown']['utility']
            # Skip hard-cliff candidates (-1e9 sentinel)
            if u < -1e8:
                continue
            candidates.append(rec)

    # Rank by 5y DD reduction PRIMARY (most negative = best), util SECONDARY.
    # Phase B util is DD-dominated but compound tiebreaker can shuffle near-ties.
    # 5y DD is the actual ship-decision metric per Stage 3 (T4); rank on it directly.
    def _rank_key(r):
        base_5y_dd = None  # Will be loaded from baseline below
        # Find 5y DD from windows data
        cand_5y_dd = r['windows']['5y']['worst_dd']
        return (cand_5y_dd, -r['utility_breakdown']['utility'])
    candidates.sort(key=_rank_key)
    print(f"Phase B candidates available: {len(candidates)} (post-cliff filter, ranked by 5y_dd asc)")

    # Deduplicate by (shape, mid, hw, floor, ceil, pk)
    seen = set()
    unique_top = []
    for c in candidates:
        p = c['params']
        key = (p['shape'], p['midpoint'], p['half_width'], p['floor'], p['ceil'], p['power_k'])
        if key in seen:
            continue
        seen.add(key)
        unique_top.append(c)
        if len(unique_top) >= TOPK:
            break

    print(f"Drilling top {len(unique_top)} unique candidates at N={N_ITER}:")
    for i, c in enumerate(unique_top):
        p = c['params']
        u = c['utility_breakdown']
        print(f"  #{i+1}: util={u['utility']:+.2f}  iter={c['iter']}  "
              f"{p['shape']} mid={p['midpoint']} hw={p['half_width']} "
              f"f={p['floor']:.2f} c={p['ceil']:.2f} pk={p['power_k']}")

    if os.path.exists(log_path):
        os.remove(log_path)

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nActive version: {version.git_commit if version else 'NONE'}")

    def log(rec):
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    # Run baseline first
    print(f"\n[baseline] N={N_ITER} x {len(SWEEP_WINDOWS)} windows...")
    t0 = time.time()
    base_cand = {'name': 'baseline', 'enabled': False}
    base_res = run_candidate(base_cand, version, N_ITER)
    print(f"  baseline elapsed: {time.time()-t0:.0f}s")
    print(f"  baseline 5y    : ret={base_res['5y']['mean_ret']:+.2e}%  dd={base_res['5y']['worst_dd']:.1f}%")
    print(f"  baseline 22-now: ret={base_res['22-now']['mean_ret']:+.2e}%  dd={base_res['22-now']['worst_dd']:.1f}%")
    log({'name': 'baseline', 'enabled': False, 'params': None, 'res': base_res,
         'gates': None, 'elapsed_s': time.time()-t0})

    # Run each top-K candidate
    for i, c in enumerate(unique_top):
        p = c['params']
        name = (f"top{i+1}_{p['shape'][:4]}_m{p['midpoint']}_hw{p['half_width']}_"
                f"f{int(p['floor']*100)}_c{int(p['ceil']*100)}_pk{p['power_k']}")
        print(f"\n[{name}] N={N_ITER} x {len(SWEEP_WINDOWS)} windows...")
        t0 = time.time()
        cand = {'name': name, 'enabled': True, 'params': p}
        cand_res = run_candidate(cand, version, N_ITER)
        gates = evaluate_t_gates(cand_res, base_res)
        elapsed = time.time() - t0
        log({'name': name, 'enabled': True, 'params': p, 'res': cand_res,
             'gates': gates, 'elapsed_s': elapsed})

        # Summary
        verdict = 'PASS' if gates['all_T_pass'] else 'FAIL'
        print(f"  {verdict}  Δ5y_dd={gates['dd_5y_delta_pp']:+.2f}pp  "
              f"Δ22n_dd={gates['dd_22n_delta_pp']:+.2f}pp  "
              f"5y_OOM_Δ={gates['compound_oom_diff']:+.2f}  "
              f"T4={gates['T4_5y_dd_pass']} T5={gates['T5_annual_dd_pass']} "
              f"T6={gates['T6_collapse_pass']} T7={gates['T7_compound_oom_pass']}  "
              f"({elapsed:.0f}s)")
        if gates['annual_dd_breaches']:
            print(f"    annual DD breaches: {gates['annual_dd_breaches']}")

    # Final summary
    print("\n" + "=" * 110)
    print("PHASE C SUMMARY (Stage 3 T-gates):")
    print("=" * 110)
    print(f"{'cand':<35s}  {'Δ5y_dd':>7s}  {'Δ22n_dd':>8s}  {'OOM_Δ':>6s}  {'T4':>3s} {'T5':>3s} {'T6':>3s} {'T7':>3s}  {'verdict'}")
    with open(log_path, 'r') as f:
        for line in f:
            rec = json.loads(line)
            if rec['name'] == 'baseline':
                continue
            g = rec['gates']
            verdict = 'PASS' if g['all_T_pass'] else 'FAIL'
            print(f"{rec['name']:<35s}  {g['dd_5y_delta_pp']:>+6.2f}pp  "
                  f"{g['dd_22n_delta_pp']:>+7.2f}pp  {g['compound_oom_diff']:>+5.2f}  "
                  f"{'Y' if g['T4_5y_dd_pass'] else 'n':>3s} "
                  f"{'Y' if g['T5_annual_dd_pass'] else 'n':>3s} "
                  f"{'Y' if g['T6_collapse_pass'] else 'n':>3s} "
                  f"{'Y' if g['T7_compound_oom_pass'] else 'n':>3s}  {verdict}")


if __name__ == '__main__':
    main()
