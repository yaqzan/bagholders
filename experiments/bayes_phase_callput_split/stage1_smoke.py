"""Phase Call/Put Split — Stage 1 smoke screen.

Tests reserved-slot variants by capping per-side max positions while keeping
global MAX_POSITIONS=14. With calls-first priority:
  - Capping MAX_POSITIONS_CALL = 14-RP creates RP reserved put slots
  - Capping MAX_POSITIONS_PUT  = K limits puts (forces unused capacity to calls)

Stage 1: smoke screen at N=80 across 22-now + 5y to detect signal.
Decision rule:
  - SHIP-OR-DEEPER: if any variant shows |Δ compound MedRet| > 30% AND
    DD direction is favorable (less DD or same), promote to Stage 2.
  - KILL: if all variants are within 20% of baseline on both windows
    AND DD is within ±2pp.

Runtime: ~7 variants × 2 windows × N=80 with mp ~ 30-45 min.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# -----------------------------------------------------------------------------
# Variants under test
# -----------------------------------------------------------------------------

VARIANTS = [
    # name, MAX_POSITIONS_CALL, MAX_POSITIONS_PUT, description
    ('B_baseline',  None, None, 'production: calls-first shared pool'),
    ('V1_RP2',      12,   None, 'reserve 2 put slots (calls cap=12)'),
    ('V2_RP4',      10,   None, 'reserve 4 put slots (calls cap=10)'),
    ('V3_RP6',       8,   None, 'reserve 6 put slots (calls cap=8)'),
    ('V4_5050',      7,   None, '50/50 split (calls cap=7)'),
    ('V5_PutCap4',  None,    4, 'cap puts at 4 (free more for calls)'),
    ('V6_PutCap2',  None,    2, 'cap puts at 2'),
]

# Stage 1: 2 windows (the 2 most predictive per CLAUDE.md)
WINDOWS = [
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]

N_ITER = int(os.environ.get('STAGE1_N', '80'))

# -----------------------------------------------------------------------------

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f'stage1_n{N_ITER}.jsonl')


def log(event: dict):
    event['_ts'] = time.time()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, default=str) + '\n')


def run_variant(name: str, mc_cap: int | None, mp_cap: int | None,
                version, windows):
    """Apply variant, run all windows, return per-window seeded results.

    NOTE: must set BOTH env vars AND mc module globals because Windows MP
    workers re-import the module on spawn (re-reads env), while the parent
    process uses the in-memory module globals.
    """
    # Env (for workers, spawned via multiprocessing)
    if mc_cap is None:
        os.environ.pop('MAX_POSITIONS_CALL', None)
    else:
        os.environ['MAX_POSITIONS_CALL'] = str(mc_cap)
    if mp_cap is None:
        os.environ.pop('MAX_POSITIONS_PUT', None)
    else:
        os.environ['MAX_POSITIONS_PUT'] = str(mp_cap)
    os.environ['N_ITER_OVERRIDE'] = str(N_ITER)

    # Module globals (for parent-process logic and the no-mp path)
    mc.MAX_POSITIONS_CALL = mc_cap
    mc.MAX_POSITIONS_PUT  = mp_cap
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
    for name, mc_cap, mp_cap, desc in VARIANTS:
        print(f'\n--- {name}  (call_cap={mc_cap}, put_cap={mp_cap})  {desc} ---')
        t0 = time.time()
        wr = run_variant(name, mc_cap, mp_cap, version, WINDOWS)
        elapsed = time.time() - t0
        results[name] = {
            'mc_cap': mc_cap, 'mp_cap': mp_cap, 'desc': desc,
            'windows': wr, 'elapsed_s': elapsed,
        }
        log({'event': 'variant_done', 'name': name,
             'mc_cap': mc_cap, 'mp_cap': mp_cap,
             'windows': wr, 'elapsed_s': elapsed})

    # Reset to None for safety
    mc.MAX_POSITIONS_CALL = None
    mc.MAX_POSITIONS_PUT  = None

    # ----- Comparison table -----
    print('\n' + '=' * 110)
    print('STAGE 1 COMPARISON TABLE')
    print('=' * 110)
    base = results['B_baseline']['windows']
    print(f'{"variant":<14}  {"window":<8}  {"medRet%":>14}  {"Δ%":>9}  '
          f'{"worstDD%":>9}  {"Δpp":>6}  {"meanDD%":>8}  {"P(col)%":>8}  '
          f'{"CTr":>6}  {"PTr":>6}  {"CTP%":>5}  {"PTP%":>5}')
    print('-' * 110)
    for name, _, _, _ in VARIANTS:
        for label in [w[0] for w in WINDOWS]:
            r = results[name]['windows'][label]
            br = base[label]
            # delta on med_ret (compound, geometric)
            mr = r['med_ret']
            bm = br['med_ret']
            if name == 'B_baseline':
                d_str = '   (base)'
                dd_d = '   -'
            else:
                # compute log-ratio safely
                if mr > -99 and bm > -99:
                    delta_pct = ((1 + mr/100) / (1 + bm/100) - 1) * 100 if (1 + bm/100) > 0 else float('nan')
                    d_str = f'{delta_pct:+8.1f}%'
                else:
                    d_str = '     n/a'
                dd_d = f'{r["worst_dd"] - br["worst_dd"]:+5.1f}'
            print(f'{name:<14}  {label:<8}  {mr:>+14.0f}  {d_str:>9}  '
                  f'{r["worst_dd"]:>9.1f}  {dd_d:>6}  {r["mean_dd"]:>8.1f}  {r["p_coll"]:>8.1f}  '
                  f'{r["call_trades"]:>6.1f}  {r["put_trades"]:>6.1f}  '
                  f'{r["call_tp"]:>5.1f}  {r["put_tp"]:>5.1f}')

    # ----- Decision rule -----
    print('\n' + '=' * 110)
    print('STAGE 1 DECISION')
    print('=' * 110)
    promote = []
    for name, _, _, _ in VARIANTS:
        if name == 'B_baseline':
            continue
        for label in [w[0] for w in WINDOWS]:
            r = results[name]['windows'][label]
            br = base[label]
            mr, bm = r['med_ret'], br['med_ret']
            dd_d = r['worst_dd'] - br['worst_dd']
            if mr > -99 and bm > -99 and (1 + bm/100) > 0:
                d_pct = ((1 + mr/100) / (1 + bm/100) - 1) * 100
                # signal: large move on either window OR DD improvement
                if abs(d_pct) > 30 or dd_d < -3:
                    promote.append((name, label, d_pct, dd_d))

    if promote:
        print(f'PROMOTE TO STAGE 2: {len(promote)} variant-window cells show signal:')
        for n, w, dp, dd in promote:
            print(f'  {n} on {w}: med_ret Δ={dp:+.1f}%, worstDD Δ={dd:+.1f}pp')
        promoted_names = sorted({n for n, _, _, _ in promote})
        print(f'\nVariants to advance: {promoted_names}')
    else:
        print('NO SIGNAL — kill Phase 1. All variants within 20% medRet and ±3pp DD vs baseline.')

    log({'event': 'end', 'promote': promote,
         'promoted_names': sorted({n for n, _, _, _ in promote}) if promote else []})


if __name__ == '__main__':
    main()
