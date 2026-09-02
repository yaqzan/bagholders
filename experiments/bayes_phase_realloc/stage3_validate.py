"""Phase 2 Stage 3 — Full validation of R_score_low_adv10.

Stage 2 winner: R_score_low_adv10 (entry_score_low strategy, MIN_ADV=10,
MIN_HOLD=0). On 5y at N=150: compound +11.1%, DD -1.3pp.

Stage 3: N=300 × 8 windows P1-P6 gate.
  - P3: 5y AND 22-now compound ≥ baseline
  - P4: no annual window regresses >25% vs baseline
  - P5: P(collapse) = 0% on every cell
  - P6: DD ≤ baseline (relative DD improvement, per OP2 reality)

If passes: escalate to N=500 to confirm; if regresses substantially, kill.
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

VARIANTS = [
    ('B_baseline',         '',                  0,  0, 999),
    ('R_score_low_adv10',  'entry_score_low',   10, 0, 999),
]

# All 8 canonical windows
WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

N_ITER = int(os.environ.get('STAGE3_N', '300'))

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f'stage3_n{N_ITER}.jsonl')


def log(event):
    event['_ts'] = time.time()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, default=str) + '\n')


def run_variant(name, strategy, min_adv, min_hold, max_per_day, version, windows):
    if strategy:
        os.environ['REALLOC_STRATEGY']      = strategy
        os.environ['REALLOC_MIN_ADVANTAGE'] = str(min_adv)
        os.environ['REALLOC_MIN_HOLD_BARS'] = str(min_hold)
        os.environ['REALLOC_MAX_PER_DAY']   = str(max_per_day)
    else:
        for k in ('REALLOC_STRATEGY', 'REALLOC_MIN_ADVANTAGE',
                  'REALLOC_MIN_HOLD_BARS', 'REALLOC_MAX_PER_DAY'):
            os.environ.pop(k, None)
    os.environ['N_ITER_OVERRIDE'] = str(N_ITER)

    mc.REALLOC_STRATEGY      = strategy
    mc.REALLOC_MIN_ADVANTAGE = float(min_adv)
    mc.REALLOC_MIN_HOLD_BARS = int(min_hold)
    mc.REALLOC_MAX_PER_DAY   = int(max_per_day)
    mc.N_ITER = N_ITER

    out = {}
    import io, contextlib
    for label, d_start, d_end in windows:
        buf = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
        dur = time.time() - t0
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':    r.get('mean_ret', 0.0),
            'med_ret':     r.get('med_ret', 0.0),
            'mean_dd':     r.get('mean_dd', 0.0),
            'worst_dd':    r.get('worst_dd', 0.0),
            'p_coll':      r.get('p_coll', 0.0),
            'call_tp':     r.get('call_tp', 0.0),
            'put_tp':      r.get('put_tp', 0.0),
            'call_trades': r.get('call_trades', 0.0),
            'put_trades':  r.get('put_trades', 0.0),
            'duration_s':  dur,
        }
        print(f'  [{label:<8}] medRet={r.get("med_ret",0):+.0f}%  worstDD={r.get("worst_dd",0):.1f}%  '
              f'meanDD={r.get("mean_dd",0):.1f}%  pcoll={r.get("p_coll",0):.1f}%  '
              f'CTr={r.get("call_trades",0):.1f}  PTr={r.get("put_trades",0):.1f}  dur={dur:.0f}s')
    return out


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f'Active scoring version: {version.git_commit if version else "?"}')
    print(f'Stage 3 P1-P6 validation: N={N_ITER}, {len(VARIANTS)} variants × {len(WINDOWS)} windows')
    print(f'Log: {LOG_PATH}')
    print('=' * 110)

    log({'event': 'start', 'n_iter': N_ITER, 'variants': [v[0] for v in VARIANTS],
         'windows': [w[0] for w in WINDOWS],
         'version': str(version.git_commit) if version else None})

    results = {}
    for name, strat, mv, mh, mpd in VARIANTS:
        print(f'\n--- {name}  strat={strat or "OFF"} adv={mv} hold={mh} ---')
        t0 = time.time()
        wr = run_variant(name, strat, mv, mh, mpd, version, WINDOWS)
        results[name] = {'strategy': strat, 'min_adv': mv, 'windows': wr,
                         'elapsed_s': time.time() - t0}
        log({'event': 'variant_done', 'name': name, 'windows': wr})

    os.environ.pop('REALLOC_STRATEGY', None)
    mc.REALLOC_STRATEGY = ''

    # ----- Comparison table -----
    print('\n' + '=' * 130)
    print('STAGE 3 COMPARISON — R_score_low_adv10 vs baseline')
    print('=' * 130)
    base = results['B_baseline']['windows']
    cand = results['R_score_low_adv10']['windows']
    print(f'{"window":<8}  {"base medRet%":>18}  {"cand medRet%":>18}  {"Δ%":>9}  '
          f'{"base DD%":>9}  {"cand DD%":>9}  {"ΔDD pp":>7}  {"baseColl":>9}  {"candColl":>9}')
    print('-' * 130)
    p3_pass = True
    p4_pass = True
    p5_pass = True
    p6_count = 0
    for label in [w[0] for w in WINDOWS]:
        br = base[label]; cr = cand[label]
        bm = br['med_ret']; cm = cr['med_ret']
        if (1 + bm/100) > 0:
            d_pct = ((1 + cm/100) / (1 + bm/100) - 1) * 100
        else:
            d_pct = float('nan')
        dd_d = cr['worst_dd'] - br['worst_dd']
        # Gates
        if label in ('5y', '22-now') and d_pct < 0:
            p3_pass = False
        if d_pct < -25:
            p4_pass = False
        if cr['p_coll'] > 0:
            p5_pass = False
        if dd_d <= 0:
            p6_count += 1
        print(f'{label:<8}  {bm:>+18.0f}  {cm:>+18.0f}  {d_pct:>+8.1f}%  '
              f'{br["worst_dd"]:>9.1f}  {cr["worst_dd"]:>9.1f}  {dd_d:>+6.1f}  '
              f'{br["p_coll"]:>9.2f}  {cr["p_coll"]:>9.2f}')

    # ----- Decision -----
    print('\n' + '=' * 130)
    print('STAGE 3 P1-P6 GATE')
    print('=' * 130)
    print(f'P1 (N={N_ITER}+):    {"PASS" if N_ITER >= 300 else "FAIL — need N>=300"}')
    print(f'P2 (8 windows): PASS')
    print(f'P3 (5y AND 22-now compound >= baseline): {"PASS" if p3_pass else "FAIL"}')
    print(f'P4 (no annual <-25% vs baseline):        {"PASS" if p4_pass else "FAIL"}')
    print(f'P5 (P(collapse)=0% on cand):             {"PASS" if p5_pass else "FAIL"}')
    print(f'P6 (DD improvement):  cand DD <= baseline DD on {p6_count}/{len(WINDOWS)} windows')

    ship = p3_pass and p4_pass and p5_pass and p6_count >= len(WINDOWS) // 2
    if ship:
        print('\n>>> CANDIDATE PASSES P1-P6 GATE <<<')
        print('Recommend: escalate to N=500 confirmation, then ship if confirmed.')
    else:
        print('\n>>> KILL PHASE 2 <<<')
        print('Stage 2 N=150 signal did not survive Stage 3 N=300 validation.')
        print('Realloc mechanism is now fully implemented for any future revisit.')

    log({'event': 'end', 'p3_pass': p3_pass, 'p4_pass': p4_pass,
         'p5_pass': p5_pass, 'p6_count': p6_count, 'ship': ship})


if __name__ == '__main__':
    main()
