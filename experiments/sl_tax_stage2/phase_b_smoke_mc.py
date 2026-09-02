"""Stage 2 Phase B — Smoke MC on PUT_TP=0.20 candidate (B2 + B3 gates).

B2: 22-now N=300 worst DD ≤ baseline + 1.5pp
B3: 22-now N=300 collapse rate = 0%

Compares PUT_TP=0.20 vs baseline PUT_TP=0.35 holding all other params constant.
"""
from __future__ import annotations
import os, sys, time, io
from datetime import date

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run_one(put_tp_value: float, label: str, version):
    import monte_carlo as mc
    # Inject PUT_TP via env (read at module load by mc workers)
    os.environ['PUT_TP_OV'] = str(put_tp_value)
    os.environ['N_ITER_OVERRIDE'] = '300'
    # Patch parent process
    mc.PUT_TP = put_tp_value
    mc.N_ITER = 300
    mc.COLLISION_MODES = ['seeded']

    print(f'\n=== [{label}] PUT_TP={put_tp_value} on 22-now N=300 ===', flush=True)
    t0 = time.time()
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wr = mc.run_window('22-now', date(2022, 1, 1), date(2026, 4, 15), version)
    r = wr.get('seeded', {})
    elapsed = time.time() - t0
    print(f'  duration: {elapsed:.0f}s')
    print(f'  mean_ret  = {r.get("mean_ret", 0):+.2e}%')
    print(f'  med_ret   = {r.get("med_ret", 0):+.2e}%')
    print(f'  worst_dd  = {r.get("worst_dd", 0):.2f}%')
    print(f'  mean_dd   = {r.get("mean_dd", 0):.2f}%')
    print(f'  p_collapse= {r.get("p_coll", 0):.2f}%')
    print(f'  call_tp   = {r.get("call_tp", 0):.2f}%')
    print(f'  put_tp    = {r.get("put_tp", 0):.2f}%')
    print(f'  call_trd  = {r.get("call_trades", 0):.1f}')
    print(f'  put_trd   = {r.get("put_trades", 0):.1f}')
    return r


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'Active version: id={av.id} commit={av.git_commit}')

    # Need to ensure PUT_TP_OV is wired in monte_carlo.py
    # If not wired, fall back to direct mc.PUT_TP patch (only works in MP_NO_MP mode)
    import monte_carlo as mc
    if 'PUT_TP_OV' not in [k for k in dir(mc) if 'PUT_TP' in k]:
        print('NOTE: monte_carlo may not have PUT_TP env override wired. Workers may not see patch.')

    baseline = run_one(0.35, 'BASELINE', av)
    candidate = run_one(0.20, 'CANDIDATE_PUT_TP_020', av)

    # B2 / B3 / TS1 gates
    print('\n' + '=' * 72)
    print('STAGE 2 B-GATE EVALUATION (smoke, 22-now N=300)')
    print('=' * 72)
    base_dd = baseline.get('worst_dd', 0)
    cand_dd = candidate.get('worst_dd', 0)
    delta_dd = cand_dd - base_dd
    base_coll = baseline.get('p_coll', 0)
    cand_coll = candidate.get('p_coll', 0)
    base_pnl = baseline.get('mean_ret', 0)
    cand_pnl = candidate.get('mean_ret', 0)
    base_putp = baseline.get('put_tp', 0)
    cand_putp = candidate.get('put_tp', 0)
    base_calltp = baseline.get('call_tp', 0)
    cand_calltp = candidate.get('call_tp', 0)

    print(f'  Worst DD :  baseline {base_dd:.2f}%   candidate {cand_dd:.2f}%   Δ {delta_dd:+.2f}pp')
    b2 = delta_dd <= 1.5
    print(f'    B2 (Δdd ≤ 1.5pp): {"PASS" if b2 else "FAIL"}')
    print(f'  Collapse :  baseline {base_coll:.2f}%   candidate {cand_coll:.2f}%')
    b3 = cand_coll == 0
    print(f'    B3 (collapse = 0): {"PASS" if b3 else "FAIL"}')
    print(f'  Put TP%  :  baseline {base_putp:.2f}%   candidate {cand_putp:.2f}%   Δ {cand_putp-base_putp:+.2f}pp')
    print(f'  Call TP% :  baseline {base_calltp:.2f}%   candidate {cand_calltp:.2f}%   Δ {cand_calltp-base_calltp:+.2f}pp')
    print(f'  Compound :  baseline {base_pnl:+.2e}%  candidate {cand_pnl:+.2e}%')

    if b2 and b3:
        print('\n  ✓ Smoke gate PASS — proceed to Phase C refinement around PUT_TP=0.20')
    else:
        print('\n  ✗ Smoke gate FAIL — investigate')


if __name__ == '__main__':
    main()
