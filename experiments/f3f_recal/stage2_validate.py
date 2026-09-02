"""Stage 2 — N=300 validation of top candidates from Stage 1.

Reads stage1_results.jsonl, picks the top K candidates by utility, re-runs
each at N=300 × 8 windows under canonical MC, and reports against P1-P6
ship gate criteria from .claude/docs/assessment-backtest.md:

  P1: N >= 300 per (window x mode) - met by N=300
  P2: All 8 canonical windows - met
  P3: 5y compound >= baseline AND 22-now compound >= baseline
  P4: No annual window regresses > 25% vs baseline
  P5: P(collapse) = 0% on every cell
  P6: DD improvement vs baseline (relative; absolute 80% aspirational)

Inputs:
  TOP_K (env, default 4) — number of candidates to validate (always
    includes baseline C0 as a calibration anchor)

Outputs:
  experiments/f3f_recal/stage2_results.jsonl
  experiments/f3f_recal/stage2.log

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/f3f_recal/stage2_validate.py 2>&1 \
    | tee experiments/f3f_recal/stage2.log
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

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


def apply_cfg(params: dict, n_iter: int):
    os.environ['F3F_CALL_FLOOR']  = str(params['call_floor'])
    os.environ['F3F_CALL_LOW']    = str(params['call_low'])
    os.environ['F3F_CALL_THRESH'] = str(params['call_thresh'])
    mc.F3F_CALL_FLOOR  = float(params['call_floor'])
    mc.F3F_CALL_LOW    = float(params['call_low'])
    mc.F3F_CALL_THRESH = float(params['call_thresh'])
    os.environ['N_ITER_OVERRIDE'] = str(n_iter)
    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']


def run_config(params, version, n_iter):
    apply_cfg(params, n_iter)
    out = {}
    import io as _io, contextlib
    for label, d_start, d_end in SWEEP_WINDOWS:
        buf = _io.StringIO()
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


def p1_p6_gate(baseline_w, candidate_w):
    """Apply ship gates P3-P6. Returns dict with pass/fail per gate."""
    # P3: 5y AND 22-now compound >= baseline
    p3_5y = candidate_w['5y']['mean_ret'] >= baseline_w['5y']['mean_ret']
    p3_22n = candidate_w['22-now']['mean_ret'] >= baseline_w['22-now']['mean_ret']
    p3 = p3_5y and p3_22n

    # P4: no annual window regresses >25% vs baseline
    annual_windows = ['2021', '2022', '2023', '2024', '2025', 'dip']
    p4_violations = []
    for w in annual_windows:
        b = baseline_w[w]['mean_ret']
        c = candidate_w[w]['mean_ret']
        if b == 0:
            continue
        # % regression: (c - b) / |b|, only flag if regress > -25%
        delta_pct = (c - b) / abs(b) * 100.0
        if delta_pct < -25.0:
            p4_violations.append((w, delta_pct))
    p4 = len(p4_violations) == 0

    # P5: P(collapse) = 0% every cell
    p5 = all(candidate_w[w]['p_collapse'] == 0.0 for w in candidate_w)

    # P6: DD improvement (relative)
    base_max_dd = max(baseline_w[w]['worst_dd'] for w in baseline_w)
    cand_max_dd = max(candidate_w[w]['worst_dd'] for w in candidate_w)
    p6 = cand_max_dd <= base_max_dd

    return dict(
        p3_5y=p3_5y, p3_22n=p3_22n, p3=p3,
        p4=p4, p4_violations=p4_violations,
        p5=p5,
        p6=p6, base_max_dd=base_max_dd, cand_max_dd=cand_max_dd,
        all_pass=p3 and p4 and p5 and p6,
    )


def main():
    log_dir = os.path.join(ROOT, 'experiments', 'f3f_recal')
    in_path = os.path.join(log_dir, 'stage1_results.jsonl')
    out_path = os.path.join(log_dir, 'stage2_results.jsonl')
    if os.path.exists(out_path):
        os.remove(out_path)

    TOP_K = int(os.environ.get('TOP_K', '4'))
    N_ITER = int(os.environ.get('N_ITER', '300'))

    # Read Stage 1 results
    if not os.path.exists(in_path):
        print(f"ERROR: {in_path} not found. Run stage1_sweep.py first.")
        sys.exit(1)
    s1_evals = []
    with open(in_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('event') != 'eval':
                continue
            s1_evals.append(r)
    print(f"Loaded {len(s1_evals)} Stage 1 evaluations")

    # Pick top K by utility, always include baseline (tag=C0_baseline)
    ranked = sorted(s1_evals, key=lambda r: r['utility'], reverse=True)
    selected = []
    seen_keys = set()
    for r in ranked[:TOP_K]:
        key = tuple(sorted(r['params'].items()))
        if key not in seen_keys:
            selected.append(r)
            seen_keys.add(key)
    # Ensure baseline is there
    baseline = next((r for r in s1_evals if r.get('tag') == 'C0_baseline'), None)
    if baseline is None:
        print("ERROR: C0_baseline not found in Stage 1 results")
        sys.exit(1)
    bkey = tuple(sorted(baseline['params'].items()))
    if bkey not in seen_keys:
        selected.append(baseline)

    print(f"\nValidating {len(selected)} candidates at N={N_ITER}:")
    for r in selected:
        print(f"  {r.get('tag', '?'):>14s}  s1_util={r['utility']:+.3f}  {r['params']}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"\nActive version: {version.git_commit if version else 'NONE'}")

    # Run baseline first so we have it for P3-P6 comparison
    baseline_params = baseline['params']
    print(f"\n[baseline] {baseline_params}")
    t0 = time.time()
    baseline_w = run_config(baseline_params, version, N_ITER)
    print(f"  done in {time.time()-t0:.0f}s")

    def log(rec):
        with open(out_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

    log({'role': 'baseline', 'params': baseline_params, 'windows': baseline_w})

    # Validate each candidate
    print("\n" + "="*120)
    print("STAGE 2 VALIDATION RESULTS")
    print("="*120)
    print(f"{'tag':>14s}  {'5y':>10}  {'22n':>10}  {'maxDD':>6}  {'P3':>3}  {'P4':>3}  {'P5':>3}  {'P6':>3}  {'PASS':>4}")

    for r in selected:
        if r.get('tag') == 'C0_baseline' or tuple(sorted(r['params'].items())) == bkey:
            tag = r.get('tag', 'C0_baseline')
            cand_w = baseline_w
        else:
            tag = r.get('tag', '?')
            print(f"\n[{tag}] {r['params']}")
            t0 = time.time()
            cand_w = run_config(r['params'], version, N_ITER)
            print(f"  done in {time.time()-t0:.0f}s")
        gate = p1_p6_gate(baseline_w, cand_w)
        log({'role': 'candidate', 'tag': tag, 'params': r['params'],
             'windows': cand_w, 'gate': gate, 's1_utility': r['utility']})
        print(f"{tag:>14s}  "
              f"{cand_w['5y']['mean_ret']:>+10.0e}  "
              f"{cand_w['22-now']['mean_ret']:>+10.0e}  "
              f"{gate['cand_max_dd']:>6.1f}  "
              f"{'Y' if gate['p3'] else 'N':>3}  "
              f"{'Y' if gate['p4'] else 'N':>3}  "
              f"{'Y' if gate['p5'] else 'N':>3}  "
              f"{'Y' if gate['p6'] else 'N':>3}  "
              f"{'YES' if gate['all_pass'] else 'NO':>4}")
        if not gate['p4'] and gate['p4_violations']:
            print(f"    P4 violations: {gate['p4_violations']}")

    print("\n[Stage 2 complete] See stage2_results.jsonl for full window-by-window data.")


if __name__ == '__main__':
    main()
