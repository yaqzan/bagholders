"""Phase 2 (Reallocation) — Stage 1 smoke screen.

Tests whether displacing held positions when slot pool is full to admit new
high-conviction signals improves portfolio returns.

Strategies:
  - entry_score_low: displace lowest-conviction held same-side position
  - current_pnl_high: lock in profits (highest mark-to-model)
  - current_pnl_low: cut losers (lowest mark-to-model)
  - days_held_high: recycle longest-held (most theta drag)
  - pnl_high_or_score_low: hybrid — lock gains > +20%, fall back to lowest score

Stage 1: N=80 × 5 strategies + 1 baseline × {22-now, 5y}.
Decision rule:
  - PROMOTE: variant shows |Δ compound| > 30% OR DD improvement >3pp on EITHER window
  - KILL: all within ±20% compound and ±3pp DD vs baseline

Runtime estimate: 6 cells × 2 windows × N=80 with mp = ~50-90 min.
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
# Variants. Single MIN_ADVANTAGE for Stage 1 (5 = "needs 5+ pt conviction edge").
# If signal exists, Stage 2 sweeps MIN_ADV ∈ {0, 3, 5, 10}.
# -----------------------------------------------------------------------------

VARIANTS = [
    # name, REALLOC_STRATEGY, MIN_ADV, MIN_HOLD, MAX_PER_DAY, description
    # Aggressive params (MIN_ADV=0, MIN_HOLD=0) per user note "previously attempted but
    # parameters too tight". Tests whether ANY signal exists in the realloc mechanism.
    # Stage 2 sweeps MIN_ADV / MIN_HOLD on whichever strategies survive.
    ('B_baseline',          '',                        0.0,   0,  999, 'production: no realloc'),
    ('R_score_low',         'entry_score_low',         0.0,   0,  999, 'displace lowest-conviction held'),
    ('R_pnl_high',          'current_pnl_high',        0.0,   0,  999, 'lock in highest realized gain'),
    ('R_pnl_low',           'current_pnl_low',         0.0,   0,  999, 'cut deepest current loser'),
    ('R_days_held',         'days_held_high',          0.0,   0,  999, 'recycle longest-held (theta drag)'),
    ('R_hybrid',            'pnl_high_or_score_low',   0.0,   0,  999, 'lock gains>+20%, else lowest-score'),
]

WINDOWS = [
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

N_ITER = int(os.environ.get('STAGE1_N', '80'))

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f'stage1_n{N_ITER}.jsonl')


def log(event: dict):
    event['_ts'] = time.time()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, default=str) + '\n')


def run_variant(name, strategy, min_adv, min_hold, max_per_day, version, windows):
    """Apply variant config and run all windows."""
    # Env (for MP workers)
    if strategy:
        os.environ['REALLOC_STRATEGY']      = strategy
        os.environ['REALLOC_MIN_ADVANTAGE'] = str(min_adv)
        os.environ['REALLOC_MIN_HOLD_BARS'] = str(min_hold)
        os.environ['REALLOC_MAX_PER_DAY']   = str(max_per_day)
    else:
        os.environ.pop('REALLOC_STRATEGY', None)
        os.environ.pop('REALLOC_MIN_ADVANTAGE', None)
        os.environ.pop('REALLOC_MIN_HOLD_BARS', None)
        os.environ.pop('REALLOC_MAX_PER_DAY', None)
    os.environ['N_ITER_OVERRIDE'] = str(N_ITER)

    # Module globals (for parent / no-mp path)
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
            'mean_ret':     r.get('mean_ret', 0.0),
            'med_ret':      r.get('med_ret', 0.0),
            'mean_dd':      r.get('mean_dd', 0.0),
            'worst_dd':     r.get('worst_dd', 0.0),
            'p_coll':       r.get('p_coll', 0.0),
            'call_tp':      r.get('call_tp', 0.0),
            'put_tp':       r.get('put_tp', 0.0),
            'call_trades':  r.get('call_trades', 0.0),
            'put_trades':   r.get('put_trades', 0.0),
            'duration_s':   dur,
        }
        print(f'  [{label}] medRet={r.get("med_ret",0):+.0f}%  worstDD={r.get("worst_dd",0):.1f}%  '
              f'meanDD={r.get("mean_dd",0):.1f}%  pcoll={r.get("p_coll",0):.1f}%  '
              f'CTr={r.get("call_trades",0):.1f}  PTr={r.get("put_trades",0):.1f}  '
              f'dur={dur:.0f}s')
    return out


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f'Active scoring version: {version.git_commit if version else "?"}')
    print(f'Stage 1 smoke screen: N={N_ITER}, {len(VARIANTS)} variants × {len(WINDOWS)} windows')
    print(f'Log: {LOG_PATH}')
    print('=' * 110)

    log({'event': 'start', 'n_iter': N_ITER, 'variants': [v[0] for v in VARIANTS],
         'windows': [w[0] for w in WINDOWS],
         'version': str(version.git_commit) if version else None})

    results = {}
    for name, strat, mv, mh, mpd, desc in VARIANTS:
        print(f'\n--- {name}  (strategy={strat or "OFF"}, min_adv={mv}, min_hold={mh})  {desc} ---')
        t0 = time.time()
        wr = run_variant(name, strat, mv, mh, mpd, version, WINDOWS)
        elapsed = time.time() - t0
        results[name] = {
            'strategy': strat, 'min_adv': mv, 'min_hold': mh, 'max_per_day': mpd,
            'desc': desc, 'windows': wr, 'elapsed_s': elapsed,
        }
        log({'event': 'variant_done', 'name': name,
             'strategy': strat, 'min_adv': mv,
             'windows': wr, 'elapsed_s': elapsed})

    # Cleanup env
    os.environ.pop('REALLOC_STRATEGY', None)
    mc.REALLOC_STRATEGY = ''

    # ----- Comparison table -----
    print('\n' + '=' * 120)
    print('STAGE 1 COMPARISON TABLE')
    print('=' * 120)
    base = results['B_baseline']['windows']
    print(f'{"variant":<14}  {"window":<8}  {"medRet%":>14}  {"Δ%":>9}  '
          f'{"worstDD%":>9}  {"Δpp":>6}  {"meanDD%":>8}  {"P(col)%":>8}  '
          f'{"CTr":>6}  {"PTr":>6}  {"CTP%":>5}  {"PTP%":>5}')
    print('-' * 120)
    for name, *_ in VARIANTS:
        for label in [w[0] for w in WINDOWS]:
            r = results[name]['windows'][label]
            br = base[label]
            mr, bm = r['med_ret'], br['med_ret']
            if name == 'B_baseline':
                d_str = '   (base)'
                dd_d = '   -'
            else:
                if mr > -99 and bm > -99 and (1 + bm/100) > 0:
                    delta_pct = ((1 + mr/100) / (1 + bm/100) - 1) * 100
                    d_str = f'{delta_pct:+8.1f}%'
                else:
                    d_str = '     n/a'
                dd_d = f'{r["worst_dd"] - br["worst_dd"]:+5.1f}'
            print(f'{name:<14}  {label:<8}  {mr:>+14.0f}  {d_str:>9}  '
                  f'{r["worst_dd"]:>9.1f}  {dd_d:>6}  {r["mean_dd"]:>8.1f}  {r["p_coll"]:>8.1f}  '
                  f'{r["call_trades"]:>6.1f}  {r["put_trades"]:>6.1f}  '
                  f'{r["call_tp"]:>5.1f}  {r["put_tp"]:>5.1f}')

    # ----- Decision rule -----
    print('\n' + '=' * 120)
    print('STAGE 1 DECISION')
    print('=' * 120)
    promote = []
    for name, *_ in VARIANTS:
        if name == 'B_baseline':
            continue
        for label in [w[0] for w in WINDOWS]:
            r = results[name]['windows'][label]
            br = base[label]
            mr, bm = r['med_ret'], br['med_ret']
            dd_d = r['worst_dd'] - br['worst_dd']
            if mr > -99 and bm > -99 and (1 + bm/100) > 0:
                d_pct = ((1 + mr/100) / (1 + bm/100) - 1) * 100
                # Signal: large move on either window OR DD improvement >3pp
                if abs(d_pct) > 30 or dd_d < -3:
                    promote.append((name, label, d_pct, dd_d))

    if promote:
        print(f'PROMOTE TO STAGE 2: {len(promote)} variant-window cells show signal:')
        for n, w, dp, dd in promote:
            print(f'  {n} on {w}: med_ret Δ={dp:+.1f}%, worstDD Δ={dd:+.1f}pp')
        promoted_names = sorted({n for n, _, _, _ in promote})
        print(f'\nVariants to advance: {promoted_names}')
    else:
        print('NO SIGNAL — kill Phase 2. All variants within 20% medRet and ±3pp DD vs baseline.')

    log({'event': 'end', 'promote': promote,
         'promoted_names': sorted({n for n, _, _, _ in promote}) if promote else []})


if __name__ == '__main__':
    main()
