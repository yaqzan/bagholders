#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for the lifecycle-MC composition core in policy.py.

ZERO MySQL, ZERO Monte Carlo -- every panel is HAND-BUILT with the correct
answer known by construction (deterministic single-path windows so rng draws
don't introduce nondeterminism). Mirrors
experiments/concentration_2x/test_metrics.py's discipline: lock the
fallback-rule / proration / DD-bound / seeding logic BEFORE trusting it on
real MC panels. Run:

    PYTHONIOENCODING=utf-8 python experiments/lifecycle_mc/test_policy.py

Exit 0 = all pass.
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import experiments.lifecycle_mc.policy as pl  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _failures.append(name)


def approx(a, b, tol=1e-6):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol


def mkwin(label, start, end, finals, dds, t2x_bars=None):
    return {
        'label': label, 'start': start, 'end': end,
        'span_cal': max(1, (end - start).days),
        'n_td': None,
        'finals': list(finals), 'dds': list(dds),
        't2x_bars': list(t2x_bars) if t2x_bars is not None else [None] * len(finals),
        'starting_cash': pl.START,
    }


# ---------------------------------------------------------------------------
def test_bars_to_caldays():
    check("bars_to_caldays(None) is None", pl.bars_to_caldays(None) is None)
    # TD_PER_CAL = 252/365.25; 69.0 bars ~= 100 cal days
    got = pl.bars_to_caldays(69.0)
    check("bars_to_caldays(69) ~= 100", got in (99, 100, 101), f"got {got}")
    check("bars_to_caldays min-1", pl.bars_to_caldays(0.001) == 1)


def test_core_block_return_exact_and_prorated():
    w = mkwin('w', date(2020, 1, 1), date(2020, 1, 1), [65_000.0], [0.10])
    w['span_cal'] = 730
    # exact match: span_cal param == win span -> mult == final/START exactly
    mult, dd = pl.core_block_return(w, 0, 730)
    check("core_block_return exact mult", approx(mult, 65_000.0 / 50_000.0), f"got {mult}")
    check("core_block_return exact dd unscaled", approx(dd, 0.10), f"got {dd}")
    # prorated: half the window -> dd scaled by sqrt(0.5), mult by the half-log-return
    mult2, dd2 = pl.core_block_return(w, 0, 365)
    expected_mult2 = math.exp(math.log(65_000.0 / 50_000.0) / 730 * 365)
    expected_dd2 = 0.10 * math.sqrt(365 / 730)
    check("core_block_return prorated mult", approx(mult2, expected_mult2), f"got {mult2}")
    check("core_block_return prorated dd", approx(dd2, expected_dd2), f"got {dd2}")


def test_sprint_episode_outcome_doubled():
    t2x_bar = 69.0
    t2x_cal = pl.bars_to_caldays(t2x_bar)
    w = mkwin('sp', date(2020, 1, 1), date(2021, 12, 31), [130_000.0], [0.15], [t2x_bar])
    doubled, dur, mult, dd = pl.sprint_episode_outcome(w, 0, cal_budget=1000)
    check("doubled==True", doubled is True)
    check("dur==t2x_cal", dur == t2x_cal, f"got {dur} vs {t2x_cal}")
    check("end_mult==2.0 exactly", approx(mult, 2.0), f"got {mult}")
    check("dd passthrough", approx(dd, 0.15))


def test_sprint_episode_outcome_ride_to_window_end():
    w = mkwin('sp', date(2020, 1, 1), date(2022, 1, 1), [65_000.0], [0.22], [None])
    w['span_cal'] = pl.SPRINT_HORIZON_CAL_DAYS   # exactly 730 -> clean, no proration
    doubled, dur, mult, dd = pl.sprint_episode_outcome(w, 0, cal_budget=5 * 365)
    check("non-doubler doubled==False", doubled is False)
    check("non-doubler dur==730", dur == pl.SPRINT_HORIZON_CAL_DAYS, f"got {dur}")
    check("non-doubler end_mult==final/START", approx(mult, 65_000.0 / 50_000.0), f"got {mult}")


def test_sprint_episode_outcome_truncated_by_budget():
    # would double at cal-day 500 (within its own 730d window) but the
    # caller only has 200 days of lifecycle-horizon budget left -> must NOT
    # count as doubled; must prorate to 200 days off the window's OWN final.
    bars_for_500 = 500 * pl.TD_PER_CAL
    w = mkwin('sp', date(2020, 1, 1), date(2022, 1, 1), [140_000.0], [0.30], [bars_for_500])
    w['span_cal'] = 730
    doubled, dur, mult, dd = pl.sprint_episode_outcome(w, 0, cal_budget=200)
    check("budget-truncated doubled==False", doubled is False)
    check("budget-truncated dur==200", dur == 200, f"got {dur}")
    expected_mult = math.exp(math.log(140_000.0 / 50_000.0) / 730 * 200)
    check("budget-truncated end_mult prorated", approx(mult, expected_mult), f"got {mult}")


def test_dd_tracker():
    t = pl.DDTracker(50_000.0)
    t.visit_segment(50_000.0, 75_000.0, 0.10)   # trough 45k -> dd 0.10
    check("dd after seg1 == 0.10", approx(t.max_dd, 0.10), f"got {t.max_dd}")
    t.visit_segment(75_000.0, 90_000.0, 0.05)   # trough 71.25k off peak 75k -> 0.05 < 0.10
    check("dd after seg2 stays 0.10", approx(t.max_dd, 0.10), f"got {t.max_dd}")
    t2 = pl.DDTracker(50_000.0)
    t2.visit_segment(50_000.0, 40_000.0, 0.50)  # trough 25k off peak 50k -> 0.50
    check("dd deep drawdown == 0.50", approx(t2.max_dd, 0.50), f"got {t2.max_dd}")


def test_stable_seed_determinism():
    a = pl._stable_seed(1, 'roll_2020-01-01', 3, 'core_only')
    b = pl._stable_seed(1, 'roll_2020-01-01', 3, 'core_only')
    c = pl._stable_seed(1, 'roll_2020-01-01', 3, 'sprint_rotate_core')
    check("stable_seed reproducible", a == b, f"{a} != {b}")
    check("stable_seed distinct per arm", a != c)
    check("stable_seed is int", isinstance(a, int))


def test_pooled_starts_and_covid_flag():
    sp = [mkwin('a', date(2016, 1, 1), date(2017, 12, 31), [1], [0]),
          mkwin('b', date(2018, 1, 1), date(2019, 12, 31), [1], [0]),
          mkwin('c', date(2020, 1, 1), date(2021, 12, 31), [1], [0])]
    co = [mkwin('a', date(2016, 1, 1), date(2017, 12, 31), [1], [0]),
          mkwin('b', date(2018, 1, 1), date(2019, 12, 31), [1], [0]),
          mkwin('c', date(2020, 1, 1), date(2021, 12, 31), [1], [0])]
    eff_end = date(2021, 12, 31)   # min(sp[-1].end, co[-1].end)

    # A horizon exactly spanning (earliest start -> eff_end): only the
    # earliest start ('a') has that much runway; 'b'/'c' start later so the
    # SAME horizon carries them past eff_end. Computed via real date
    # subtraction (not hand-counted days) to stay leap-year-safe.
    long_horizon = (eff_end - date(2016, 1, 1)).days
    starts = pl.pooled_starts(sp, co, horizon_cal=long_horizon)
    check("pooled_starts: only the earliest start has that much runway",
          starts == [date(2016, 1, 1)], f"got {starts}")

    short_horizon = 400   # well under any window's own ~730d span -> all 3 qualify
    starts2 = pl.pooled_starts(sp, co, horizon_cal=short_horizon)
    check("pooled_starts: short horizon includes all 3 starts",
          starts2 == [date(2016, 1, 1), date(2018, 1, 1), date(2020, 1, 1)], f"got {starts2}")

    check("_covers_covid(2019-06-01, 2y) True", pl._covers_covid(date(2019, 6, 1), 365 * 2))
    check("_covers_covid(2022-01-01, 2y) False", not pl._covers_covid(date(2022, 1, 1), 365 * 2))


def test_simulate_core_only_two_full_blocks():
    w1 = mkwin('w1', date(2016, 1, 1), date(2016, 1, 1), [75_000.0], [0.10])
    w1['span_cal'] = 731
    w2 = mkwin('w2', date(2018, 1, 1), date(2018, 1, 1), [60_000.0], [0.05])
    w2['span_cal'] = 731
    windows = [w1, w2]
    horizon = w1['span_cal'] + w2['span_cal']
    eq, dd = pl.simulate_core_only(windows, date(2016, 1, 1), horizon, random.Random(1))
    expected_eq = pl.START * (75_000.0 / pl.START) * (60_000.0 / pl.START)
    check("core_only chained final == 90000", approx(eq, expected_eq, tol=1.0), f"got {eq} vs {expected_eq}")
    check("core_only chained dd == 0.10", approx(dd, 0.10, tol=1e-6), f"got {dd}")


def test_simulate_sprint_rotate_core_doubled_then_core():
    t2x_bar = 69.0
    t2x_cal = pl.bars_to_caldays(t2x_bar)
    sp = [mkwin('sp', date(2016, 1, 1), date(2017, 12, 27), [130_000.0], [0.15], [t2x_bar])]
    sp[0]['span_cal'] = 730
    co_span = 400
    co = [mkwin('co', date(2016, 1, 1), date(2016, 1, 1), [70_000.0], [0.08])]
    co[0]['span_cal'] = co_span
    horizon = t2x_cal + co_span
    eq, dd, days_in_sprint = pl.simulate_sprint_rotate_core(sp, co, date(2016, 1, 1), horizon,
                                                            random.Random(1))
    check("rotate: days_in_sprint == t2x_cal", days_in_sprint == t2x_cal, f"got {days_in_sprint}")
    expected_eq = 100_000.0 * (70_000.0 / 50_000.0)
    check("rotate: final == 140000", approx(eq, expected_eq, tol=1.0), f"got {eq}")
    check("rotate: dd == 0.15 (sprint segment dominates)", approx(dd, 0.15, tol=1e-6), f"got {dd}")


def test_simulate_ladder_four_clean_doublings():
    t2x_bar = 69.0
    t2x_cal = pl.bars_to_caldays(t2x_bar)   # ~100
    sp = [mkwin('sp', date(2016, 1, 1), date(2017, 12, 27), [999_999.0], [0.12], [t2x_bar])]
    sp[0]['span_cal'] = 730
    co = [mkwin('co', date(2016, 1, 1), date(2016, 1, 1), [52_000.0], [0.02])]
    co[0]['span_cal'] = t2x_cal   # matches ep_dur exactly -> no proration surprises
    horizon = t2x_cal * 4
    eq, dd, days_in_sprint, banked = pl.simulate_ladder(sp, co, date(2016, 1, 1), horizon,
                                                        random.Random(1))
    # hand-computed (see task notes): cold compounds at 1.04x per episode on
    # whatever was banked BEFORE this episode; banked totals 4 x 50k = 200k.
    cold_mult = 52_000.0 / 50_000.0
    cold = 0.0
    running = pl.START
    banked_expect = 0.0
    for _ in range(4):
        total = cold + running
        stake = min(pl.START, total)
        cold_before = total - stake
        cold_end = cold_before * cold_mult
        banked_expect += stake
        cold = cold_end + stake
        running = stake
    expected_eq = cold + running
    check("ladder: days_in_sprint == 4*t2x_cal", days_in_sprint == t2x_cal * 4, f"got {days_in_sprint}")
    check("ladder: banked == 200000", approx(banked, banked_expect, tol=1.0), f"got {banked} vs {banked_expect}")
    check("ladder: final matches hand-computed compounding", approx(eq, expected_eq, tol=1.0),
          f"got {eq} vs {expected_eq}")
    check("ladder: dd == 0.12 (first episode's trough dominates throughout)",
          approx(dd, 0.12, tol=1e-6), f"got {dd}")


def test_collapse_and_summarize():
    finals = [50_000.0, 5_000.0, 200_000.0, 9_999.0]  # 2 of 4 <= 10000 (20% of 50k)
    dds = [0.1, 0.9, 0.2, 0.95]
    s = pl.summarize(finals, dds)
    check("collapse frac == 50%", approx(s['p_collapse_pct'], 50.0), f"got {s['p_collapse_pct']}")
    check("worst_dd_pct == 95.0", approx(s['worst_dd_pct'], 95.0), f"got {s['worst_dd_pct']}")
    expected_median = (9_999.0 + 50_000.0) / 2.0   # avg of the 2 middle order stats of [5000,9999,50000,200000]
    check("median == avg of 2 middle order stats", approx(s['median'], expected_median, tol=1e-6),
          f"got {s['median']}")


def main():
    print("=" * 70)
    print("UNIT TESTS -- lifecycle_mc policy composition (synthetic, no MC, no MySQL)")
    print("=" * 70)
    for fn in [
        test_bars_to_caldays,
        test_core_block_return_exact_and_prorated,
        test_sprint_episode_outcome_doubled,
        test_sprint_episode_outcome_ride_to_window_end,
        test_sprint_episode_outcome_truncated_by_budget,
        test_dd_tracker,
        test_stable_seed_determinism,
        test_pooled_starts_and_covid_flag,
        test_simulate_core_only_two_full_blocks,
        test_simulate_sprint_rotate_core_doubled_then_core,
        test_simulate_ladder_four_clean_doublings,
        test_collapse_and_summarize,
    ]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 70)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == '__main__':
    main()
