#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Synthetic, zero-MC unit tests of the glide-path composition math
(DESIGN.md section 13, Stage 0 -- run BEFORE any queue submission).

Every input is a hand-built panel where the answer is known by construction,
mirroring experiments/lifecycle_mc/test_policy.py's discipline. Exit 0 = the
composition core is correct.
"""
from __future__ import annotations

import math
import os
import random
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.glide_path.policy import (  # noqa: E402
    sentinel_weight, crossing_day, _blended_step, simulate_glide, simulate_static,
    cell_summary, DollarDDTracker,
)
from experiments.lifecycle_mc.policy import simulate_core_only  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def win(start: date, span: int, finals: list[float], dds: list[float]) -> dict:
    return {'label': f'syn_{start.isoformat()}', 'start': start,
            'end': start + timedelta(days=span), 'span_cal': span, 'n_td': None,
            'finals': finals, 'dds': dds, 't2x_bars': [None] * len(finals),
            'starting_cash': 50_000.0}


S = date(2016, 6, 1)


def test_weights():
    T = 100_000.0
    check('hard below', sentinel_weight(99_999, T, 'hard') == 0.0)
    check('hard at', sentinel_weight(100_000, T, 'hard') == 1.0)
    check('blend below band', sentinel_weight(79_999, T, 'blend') == 0.0)
    check('blend at lo', sentinel_weight(80_000, T, 'blend') == 0.0)
    check('blend mid', abs(sentinel_weight(100_000, T, 'blend') - 0.5) < 1e-12)
    check('blend at hi', sentinel_weight(120_000, T, 'blend') == 1.0)
    check('blend clip above', sentinel_weight(500_000, T, 'blend') == 1.0)
    seq = [50_000, 70_000, 90_000, 90_000, 110_000, 130_000, 130_000]
    ws = [sentinel_weight(m, T, 'blend') for m in seq]
    check('blend monotone on ratcheting run_max', all(b >= a for a, b in zip(ws, ws[1:])))


def test_crossing_day():
    r = math.log(2) / 100.0
    check('crossing exact', crossing_day(50_000, r, 100_000, 730) == 100)
    check('crossing 4x', crossing_day(50_000, r, 200_000, 730) == 200)
    check('crossing beyond cap', crossing_day(50_000, r, 200_000, 150) is None)
    check('crossing zero rate', crossing_day(50_000, 0.0, 100_000, 730) is None)
    check('crossing negative rate', crossing_day(50_000, -r, 100_000, 730) is None)
    check('crossing already above', crossing_day(150_000, r, 100_000, 730) == 0)


def test_blended_step():
    # pure sentinel leg with ceiling: eq 2M, C 1M, sentinel mult 1.1 over the step
    r_se = math.log(1.1) / 10
    end = _blended_step(2_000_000, 1.0, 0.0, r_se, 10, 1_000_000)
    check('ceiling split', abs(end - (1_000_000 * 1.1 + 1_000_000)) < 1e-6,
          f'got {end}')
    # p=0.5 blend, no ceiling binding (eq below C)
    r_co = math.log(1.2) / 10
    end2 = _blended_step(100_000, 0.5, r_co, r_se, 10, 1_000_000)
    expect2 = 0.5 * 100_000 * 1.2 + 0.5 * 100_000 * 1.1
    check('half blend', abs(end2 - expect2) < 1e-6, f'got {end2} want {expect2}')


def test_dollar_dd_tracker():
    t = DollarDDTracker()
    for v in [100_000, 60_000, 80_000, 120_000, 90_000]:
        t.visit(v)
    check('dollar dd', abs(t.max_dd_dollars - 40_000) < 1e-9, f'got {t.max_dd_dollars}')


def test_glide_hard_end_to_end():
    # core doubles over its 730d window; sentinel gains 20%. T=2x start.
    co = [win(S, 730, [100_000.0], [0.0])]
    se = [win(S, 730, [60_000.0], [0.0])]      # r_se = ln(1.2)/730
    rng = random.Random(1)
    f, dd, day_t, late = simulate_glide(co, se, S, 1460, rng, 50_000.0,
                                        100_000.0, 'hard', ceiling=None)
    check('hard crossing day', day_t == 730, f'got {day_t}')
    check('hard terminal', abs(f - 120_000.0) < 1e-6, f'got {f}')
    check('hard whole-horizon dd zero', dd == 0.0, f'got {dd}')
    check('hard late-dd zero', late == 0.0, f'got {late}')


def test_glide_late_dd():
    # after rotation, sentinel block is flat with a 40% within-window dd
    co = [win(S, 730, [100_000.0], [0.0])]
    se = [win(S, 730, [50_000.0], [0.4])]      # r_se = 0, dd 40%
    rng = random.Random(1)
    f, dd, day_t, late = simulate_glide(co, se, S, 1460, rng, 50_000.0,
                                        100_000.0, 'hard', ceiling=None)
    check('late-dd crossing day', day_t == 730, f'got {day_t}')
    check('late-dd terminal', abs(f - 100_000.0) < 1e-6, f'got {f}')
    check('late-dd dollars', late is not None and abs(late - 40_000.0) < 1e-6, f'got {late}')
    check('late-dd whole-horizon frac', abs(dd - 0.4) < 1e-12, f'got {dd}')


def test_glide_blend_structural():
    # strong core growth; flat sentinel. Band [80k, 120k] on T=100k.
    co = [win(S, 730, [200_000.0], [0.0])]
    se = [win(S, 730, [50_000.0], [0.0])]
    rng = random.Random(1)
    f, dd, day_t, late = simulate_glide(co, se, S, 1825, rng, 50_000.0,
                                        100_000.0, 'blend', ceiling=None)
    r_co = math.log(4.0) / 730
    d_band = math.ceil(math.log(80_000 / 50_000) / r_co)
    check('blend activates after band entry', day_t is not None and day_t > d_band,
          f'day_t={day_t} band_entry={d_band}')
    check('blend late-dd present', late is not None)
    check('blend terminal above start', f > 50_000.0, f'got {f}')


def test_static_equivalence_with_lifecycle():
    co = [win(S, 730, [40_000.0, 80_000.0, 120_000.0, 65_000.0], [0.3, 0.1, 0.2, 0.5]),
          win(S + timedelta(days=730), 730, [55_000.0, 90_000.0, 30_000.0], [0.2, 0.4, 0.6])]
    for seed in (7, 42, 999):
        r1 = random.Random(seed)
        r2 = random.Random(seed)
        f_ref, dd_ref = simulate_core_only(co, S, 1825, r1)
        f_mine, dd_mine, _ = simulate_static(co, S, 1825, r2, 50_000.0, None, [])
        check(f'static equivalence seed={seed}',
              abs(f_ref - f_mine) < 1e-9 and abs(dd_ref - dd_mine) < 1e-12,
              f'ref=({f_ref},{dd_ref}) mine=({f_mine},{dd_mine})')


def test_static_ceiling_and_refs():
    # deterministic single-path window: doubles each 730d block, no dd
    co = [win(S, 730, [100_000.0], [0.0])]
    rng = random.Random(1)
    # no ceiling: 50k -> 100k -> 200k over 1460d; ref 100k crossed at day 730
    f, dd, refs = simulate_static(co, S, 1460, rng, 50_000.0, None, [100_000.0, 1_000_000.0])
    check('static no-ceiling terminal', abs(f - 200_000.0) < 1e-6, f'got {f}')
    check('static ref crossed day', refs[100_000.0][0] == 730, f'got {refs[100_000.0]}')
    check('static ref uncrossed', refs[1_000_000.0][0] is None)
    # ceiling 150k: block1 50k->100k (under C); block2 min(100k,150k)*2 = 200k... C binds partially:
    rng = random.Random(1)
    f2, _, _ = simulate_static(co, S, 2190, rng, 50_000.0, 150_000.0, [])
    # blocks: 50k->100k; min(100k,150k)=100k*2=200k; block3: min(200k,150k)*2 + 50k idle = 350k
    check('static ceiling split', abs(f2 - 350_000.0) < 1e-6, f'got {f2}')


def test_collapse_counting():
    summ = cell_summary(50_000.0, [5_000.0, 60_000.0, 9_000.0, 200_000.0],
                        [0.1, 0.2, 0.3, 0.4], {})
    check('collapse pct per-cell start', abs(summ['p_collapse_pct'] - 50.0) < 1e-9,
          f"got {summ['p_collapse_pct']}")
    summ2 = cell_summary(25_000.0, [5_000.0, 60_000.0, 9_000.0, 200_000.0],
                         [0.1, 0.2, 0.3, 0.4], {})
    check('collapse pct scales with start', abs(summ2['p_collapse_pct'] - 25.0) < 1e-9,
          f"got {summ2['p_collapse_pct']}")


def test_conditional_skip():
    refdata = {500_000.0: {'days': [None] * 40 + [100] * 10, 'ldd': [None] * 40 + [5_000.0] * 10}}
    summ = cell_summary(50_000.0, [60_000.0] * 50, [0.1] * 50, refdata)
    check('conditional SKIP under 30', summ['refs']['500k']['conditional'] == 'SKIPPED_N_LT_30')
    refdata2 = {500_000.0: {'days': [None] * 10 + [100] * 40, 'ldd': [None] * 10 + [5_000.0] * 40}}
    summ2 = cell_summary(50_000.0, [60_000.0] * 50, [0.1] * 50, refdata2)
    cond = summ2['refs']['500k']['conditional']
    check('conditional reported at 40', isinstance(cond, dict) and cond['n'] == 40)


if __name__ == '__main__':
    test_weights()
    test_crossing_day()
    test_blended_step()
    test_dollar_dd_tracker()
    test_glide_hard_end_to_end()
    test_glide_late_dd()
    test_glide_blend_structural()
    test_static_equivalence_with_lifecycle()
    test_static_ceiling_and_refs()
    test_collapse_counting()
    test_conditional_skip()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
