"""Phase 2 Stage 2 — MIN_ADV sweep on top-2 candidates from Stage 1.

Stage 1 v2 result: R_pnl_high promising (22-now +26%, 5y -2.6% within noise),
R_score_low has DD signal (-1.6pp on 5y) but compound regression. Other 3 fail.

Stage 2 hypothesis: higher MIN_ADV (less aggressive displacement) improves the
mediocre Stage 1 signal by being more selective about WHEN realloc fires.

Sweep:
  - 2 strategies (R_pnl_high, R_score_low)
  - MIN_ADV ∈ {0, 5, 10}
  - 2 windows (22-now, 5y)
  - N=150

12 cells. ETA ~60 min based on Stage 1 cadence × 150/80 = 1.9× per cell.

Decision rule:
  - Promote any (strategy, MIN_ADV) showing positive 5y compound AND DD ≤ baseline
  - Kill if all-negative on 5y compound across the sweep
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

# -----------------------------------------------------------------------------

VARIANTS = [
    # name, REALLOC_STRATEGY, MIN_ADV, MIN_HOLD, MAX_PER_DAY
    ('B_baseline',          '',                  0,  0, 999),
    ('R_pnl_high_adv0',     'current_pnl_high',  0,  0, 999),
    ('R_pnl_high_adv5',     'current_pnl_high',  5,  0, 999),
    ('R_pnl_high_adv10',    'current_pnl_high',  10, 0, 999),
    ('R_score_low_adv0',    'entry_score_low',   0,  0, 999),
    ('R_score_low_adv5',    'entry_score_low',   5,  0, 999),
    ('R_score_low_adv10',   'entry_score_low',   10, 0, 999),
]

WINDOWS = [
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

N_ITER = int(os.environ.get('STAGE2_N', '150'))

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f'stage2_n{N_ITER}.jsonl')


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
        print(f'  [{label}] medRet={r.get("med_ret",0):+.0f}%  worstDD={r.get("worst_dd",0):.1f}%  '
              f'meanDD={r.get("mean_dd",0):.1f}%  CTr={r.get("call_trades",0):.1f}  '
              f'PTr={r.get("put_trades",0):.1f}  dur={dur:.0f}s')
    return out


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f'Active scoring version: {version.git_commit if version else "?"}')
    print(f'Stage 2 MIN_ADV sweep: N={N_ITER}, {len(VARIANTS)} variants × {len(WINDOWS)} windows')
    print(f'Log: {LOG_PATH}')
    print('=' * 110)

    log({'event': 'start', 'n_iter': N_ITER, 'variants': [v[0] for v in VARIANTS],
         'windows': [w[0] for w in WINDOWS],
         'version': str(version.git_commit) if version else None})

    results = {}
    for name, strat, mv, mh, mpd in VARIANTS:
        print(f'\n--- {name}  strat={strat or "OFF"} adv={mv} hold={mh} max/day={mpd} ---')
        t0 = time.time()
        wr = run_variant(name, strat, mv, mh, mpd, version, WINDOWS)
        results[name] = {'strategy': strat, 'min_adv': mv, 'min_hold': mh,
                         'max_per_day': mpd, 'windows': wr,
                         'elapsed_s': time.time() - t0}
        log({'event': 'variant_done', 'name': name, 'strategy': strat,
             'min_adv': mv, 'windows': wr})

    os.environ.pop('REALLOC_STRATEGY', None)
    mc.REALLOC_STRATEGY = ''

    # ----- Comparison -----
    print('\n' + '=' * 120)
    print('STAGE 2 COMPARISON')
    print('=' * 120)
    base = results['B_baseline']['windows']
    print(f'{"variant":<22}  {"window":<8}  {"medRet%":>14}  {"Δ%":>9}  '
          f'{"worstDD%":>9}  {"Δpp":>6}  {"meanDD%":>8}  {"CTr":>6}  {"PTr":>6}')
    print('-' * 120)
    for name, *_ in VARIANTS:
        for label in [w[0] for w in WINDOWS]:
            r = results[name]['windows'][label]
            br = base[label]
            mr, bm = r['med_ret'], br['med_ret']
            if name == 'B_baseline':
                d_str = '   (base)'
                dd_d_str = '   -'
            else:
                if mr > -99 and bm > -99 and (1 + bm/100) > 0:
                    delta_pct = ((1 + mr/100) / (1 + bm/100) - 1) * 100
                    d_str = f'{delta_pct:+8.1f}%'
                else:
                    d_str = '     n/a'
                dd_d_str = f'{r["worst_dd"] - br["worst_dd"]:+5.1f}'
            print(f'{name:<22}  {label:<8}  {mr:>+14.0f}  {d_str:>9}  '
                  f'{r["worst_dd"]:>9.1f}  {dd_d_str:>6}  {r["mean_dd"]:>8.1f}  '
                  f'{r["call_trades"]:>6.1f}  {r["put_trades"]:>6.1f}')

    # ----- Decision -----
    print('\n' + '=' * 120)
    print('STAGE 2 DECISION (lock on 5y; 22-now confirmation)')
    print('=' * 120)
    candidates = []
    for name, strat, mv, _, _ in VARIANTS:
        if name == 'B_baseline':
            continue
        r5 = results[name]['windows']['5y']
        rN = results[name]['windows']['22-now']
        b5 = base['5y']; bN = base['22-now']
        d5 = ((1 + r5['med_ret']/100) / (1 + b5['med_ret']/100) - 1) * 100 if (1 + b5['med_ret']/100) > 0 else 0
        dN = ((1 + rN['med_ret']/100) / (1 + bN['med_ret']/100) - 1) * 100 if (1 + bN['med_ret']/100) > 0 else 0
        dd5 = r5['worst_dd'] - b5['worst_dd']
        ddN = rN['worst_dd'] - bN['worst_dd']
        # Promote if 5y compound positive (>5% to clear noise floor) AND DD not worse
        # OR if 5y DD improves >2pp AND 5y compound non-collapse
        wins_compound = d5 > 5 and dd5 <= 2
        wins_dd       = dd5 < -2 and d5 > -25
        if wins_compound or wins_dd:
            candidates.append((name, strat, mv, d5, dN, dd5, ddN, wins_compound, wins_dd))

    if candidates:
        print('PROMOTE TO STAGE 3:')
        for name, strat, mv, d5, dN, dd5, ddN, wc, wd in candidates:
            why = 'compound' if wc else 'DD'
            print(f'  {name}: 5y Δret={d5:+.1f}% / 22-now Δret={dN:+.1f}% / '
                  f'5y ΔDD={dd5:+.1f}pp / 22-now ΔDD={ddN:+.1f}pp  ({why})')
    else:
        print('NO SHIP CANDIDATE — kill Phase 2.')
        print('Best variants by 5y compound:')
        scored = [(name, ((1 + results[name]['windows']['5y']['med_ret']/100) /
                          (1 + base['5y']['med_ret']/100) - 1) * 100)
                  for name, *_ in VARIANTS if name != 'B_baseline']
        scored.sort(key=lambda x: -x[1])
        for n, d in scored[:5]:
            print(f'  {n}: 5y Δ={d:+.1f}%')

    log({'event': 'end', 'candidates': [(c[0], c[1], c[2]) for c in candidates]})


if __name__ == '__main__':
    main()
