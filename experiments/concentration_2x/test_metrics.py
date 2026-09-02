#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Unit tests for the time-to-2x / survival / collapse metric core in sweep.py.

ZERO MySQL, ZERO Monte Carlo — every input is a HAND-BUILT path record where
the correct answer is known by construction. Run:

    PYTHONIOENCODING=utf-8 python experiments/concentration_2x/test_metrics.py

Exit 0 = all pass. The point of this test is to lock the first-passage,
"2x-before-50%-DD", collapse, and quantile logic BEFORE any real MC runs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweep  # noqa: E402

SC = 50_000.0  # starting cash; 2x = 100k, collapse = <=10k (20%)
BARS2CAL = sweep.CALENDAR_DAYS_PER_TRADING_DAY  # ~1.449 cal-days per trading bar

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


# ---------------------------------------------------------------------------
# A path record matches the engine's per-iter dict shape.
# ---------------------------------------------------------------------------
def rec(final, max_dd, t2x, t50, sc=SC):
    return {'final': final, 'max_dd': max_dd, 't2x_bar': t2x,
            't_50dd_bar': t50, 'starting_cash': sc}


def test_basic_reach_and_timing():
    """Two reachers, one non-reacher; days-to-2x median is exact."""
    recs = [
        rec(120_000, 0.30, t2x=10, t50=None),   # reached 2x at bar 10, never 50% dd
        rec(150_000, 0.40, t2x=30, t50=None),   # reached 2x at bar 30
        rec(80_000,  0.20, t2x=None, t50=None),  # never reached 2x
    ]
    m = sweep.compute_cell_metrics('t', recs, n_windows=1, n_iter_per_window=3,
                                   gross_pct=1.0, bars_to_calendar=1.0)
    check("n_paths==3", m.n_paths == 3)
    check("n_reach_2x==2", m.n_reach_2x == 2)
    check("p_2x_ever==2/3", approx(m.p_2x_ever, 2 / 3))
    # days-to-2x among reachers: [10, 30] (bars2cal=1.0) -> median 20
    check("median_days_2x==20", approx(m.median_days_2x, 20.0), f"got {m.median_days_2x}")
    check("p25_days_2x==15", approx(m.p25_days_2x, 15.0), f"got {m.p25_days_2x}")
    check("p75_days_2x==25", approx(m.p75_days_2x, 25.0), f"got {m.p75_days_2x}")


def test_2x_before_50dd_ordering():
    """The path-ordering test: 2x must be counted only if it precedes the first
    50% drawdown (or no 50% dd happened)."""
    recs = [
        rec(120_000, 0.60, t2x=10, t50=40),   # 2x@10 BEFORE 50%dd@40 -> counts
        rec(120_000, 0.60, t2x=40, t50=10),   # 2x@40 AFTER 50%dd@10 -> does NOT count
        rec(120_000, 0.30, t2x=20, t50=None),  # 2x@20, never 50%dd -> counts
        rec(80_000,  0.55, t2x=None, t50=5),   # never 2x (but had 50%dd) -> does NOT count
    ]
    m = sweep.compute_cell_metrics('t', recs, n_windows=1, n_iter_per_window=4,
                                   gross_pct=1.0, bars_to_calendar=1.0)
    check("n_reach_2x==3", m.n_reach_2x == 3, f"got {m.n_reach_2x}")
    check("n_2x_before_50dd==2", m.n_2x_before_50dd == 2, f"got {m.n_2x_before_50dd}")
    check("p_2x_before_50dd==2/4", approx(m.p_2x_before_50dd, 0.5), f"got {m.p_2x_before_50dd}")


def test_2x_same_bar_as_50dd_does_not_count():
    """Strict-before semantics: t2x == t50 must NOT count as 2x-before-50dd
    (a path that crossed both on the same bar is ambiguous; we require strict
    precedence). This is a boundary guard."""
    recs = [rec(120_000, 0.55, t2x=20, t50=20)]
    m = sweep.compute_cell_metrics('t', recs, 1, 1, 1.0, bars_to_calendar=1.0)
    check("same-bar t2x==t50 -> NOT before", m.n_2x_before_50dd == 0,
          f"got {m.n_2x_before_50dd}")


def test_collapse_threshold():
    """collapse = final <= 20% of start (= $10k for $50k start)."""
    recs = [
        rec(10_000, 0.90, t2x=None, t50=5),    # exactly 20% -> collapse (<=)
        rec(9_999,  0.95, t2x=None, t50=5),    # below 20% -> collapse
        rec(10_001, 0.85, t2x=None, t50=5),    # just above -> NOT collapse
        rec(120_000, 0.30, t2x=10, t50=None),  # winner -> NOT collapse
    ]
    m = sweep.compute_cell_metrics('t', recs, 1, 4, 1.0, bars_to_calendar=1.0)
    check("n_collapse==2", m.n_collapse == 2, f"got {m.n_collapse}")
    check("p_collapse==2/4", approx(m.p_collapse, 0.5), f"got {m.p_collapse}")


def test_collapse_independent_of_2x():
    """A path can both reach 2x intraday AND end collapsed (rode up then to
    zero). It counts toward BOTH n_reach_2x and n_collapse — they are
    independent tallies."""
    recs = [rec(8_000, 0.95, t2x=15, t50=60)]  # hit 2x@15, but ended at 8k (collapsed)
    m = sweep.compute_cell_metrics('t', recs, 1, 1, 1.0, bars_to_calendar=1.0)
    check("reached 2x even though collapsed", m.n_reach_2x == 1)
    check("also collapsed", m.n_collapse == 1)
    # 2x@15 with first-50dd@60 -> 2x precedes 50dd -> counts as before
    check("2x-before-50dd counts (15<60)", m.n_2x_before_50dd == 1, f"got {m.n_2x_before_50dd}")


def test_calendar_conversion():
    """days-to-2x is reported in CALENDAR days = t2x_bar * bars_to_calendar."""
    recs = [rec(120_000, 0.30, t2x=100, t50=None)]
    m = sweep.compute_cell_metrics('t', recs, 1, 1, 1.0, bars_to_calendar=BARS2CAL)
    expect = 100 * BARS2CAL
    check("median_days_2x calendar-converted", approx(m.median_days_2x, expect),
          f"got {m.median_days_2x} expected {expect}")


def test_compound_and_worstdd_reference():
    """Reference metrics: median compound % over start, worst (max) DD %."""
    recs = [
        rec(100_000, 0.20, t2x=10, t50=None),  # +100%
        rec(50_000,  0.40, t2x=None, t50=None),  # 0%
        rec(25_000,  0.70, t2x=None, t50=20),   # -50%
    ]
    m = sweep.compute_cell_metrics('t', recs, 1, 3, 1.0, bars_to_calendar=1.0)
    # compounds: [+100, 0, -50] -> median 0
    check("median_compound==0", approx(m.median_compound_pct, 0.0),
          f"got {m.median_compound_pct}")
    check("worst_dd==70", approx(m.worst_dd_pct, 70.0), f"got {m.worst_dd_pct}")


def test_empty_paths():
    m = sweep.compute_cell_metrics('t', [], 0, 0, 1.0)
    check("empty: n_paths==0", m.n_paths == 0)
    check("empty: median_days_2x None", m.median_days_2x is None)
    check("empty: p_2x_ever==0", m.p_2x_ever == 0.0)


def test_quantile_interpolation():
    vals = sorted([10.0, 20.0, 30.0, 40.0])
    check("q0==10", approx(sweep._quantile(vals, 0.0), 10.0))
    check("q0.5==25", approx(sweep._quantile(vals, 0.5), 25.0))
    check("q1==40", approx(sweep._quantile(vals, 1.0), 40.0))
    check("q0.25==17.5", approx(sweep._quantile(vals, 0.25), 17.5),
          f"got {sweep._quantile(vals, 0.25)}")
    one = [42.0]
    check("single-element quantile", approx(sweep._quantile(one, 0.75), 42.0))


def test_all_reach_all_survive():
    """Degenerate strong cell: every path 2x's fast, none hits 50% dd."""
    recs = [rec(110_000, 0.10, t2x=b, t50=None) for b in (5, 6, 7, 8, 9)]
    m = sweep.compute_cell_metrics('t', recs, 1, 5, 1.0, bars_to_calendar=1.0)
    check("p_2x_ever==1", approx(m.p_2x_ever, 1.0))
    check("p_2x_before_50dd==1", approx(m.p_2x_before_50dd, 1.0))
    check("p_collapse==0", approx(m.p_collapse, 0.0))
    check("median_days_2x==7", approx(m.median_days_2x, 7.0))


def test_pooled_across_windows():
    """Pooling N windows x N_iter must just be list concatenation — verify the
    aggregate counts sum correctly across heterogeneous sub-batches."""
    batch_a = [rec(120_000, 0.20, t2x=10, t50=None) for _ in range(10)]   # all reach
    batch_b = [rec(80_000, 0.30, t2x=None, t50=None) for _ in range(10)]  # none reach
    pooled = batch_a + batch_b
    m = sweep.compute_cell_metrics('t', pooled, n_windows=2, n_iter_per_window=10,
                                   gross_pct=1.0, bars_to_calendar=1.0)
    check("pooled n_paths==20", m.n_paths == 20)
    check("pooled p_2x_ever==0.5", approx(m.p_2x_ever, 0.5))
    check("pooled median_days_2x==10", approx(m.median_days_2x, 10.0))


def test_monthly_windows_rolls():
    """monthly_windows must roll start monthly, clamp end to hist_end, and drop
    windows with insufficient runway."""
    ws = sweep.monthly_windows(hist_start=date(2020, 1, 1),
                               hist_end=date(2021, 12, 31),
                               horizon_days=365, step_months=1,
                               min_horizon_days=180)
    # starts Jan 2020 .. (last start whose +365 or clamp gives >=180d runway)
    check("windows non-empty", len(ws) > 0, f"got {len(ws)}")
    # first window starts 2020-01-01
    check("first start 2020-01-01", ws[0][1] == date(2020, 1, 1), f"got {ws[0][1]}")
    # every start is the 1st of a month, monotonically increasing
    starts = [w[1] for w in ws]
    check("starts strictly increasing", all(starts[i] < starts[i + 1] for i in range(len(starts) - 1)))
    check("all starts day==1", all(s.day == 1 for s in starts))
    # every window end <= hist_end
    check("all ends <= hist_end", all(w[2] <= date(2021, 12, 31) for w in ws))
    # every window runway >= min_horizon
    check("all runways >= 180d", all((w[2] - w[1]).days >= 180 for w in ws))
    # the last valid start is ~2021-07-01 (2021-07-01 + 183d = 2021-12-31, clamped)
    check("last start has short-but-valid runway",
          (ws[-1][2] - ws[-1][1]).days >= 180)
    # a window that would start past the runway cutoff is excluded:
    check("no start later than 2021-07-01", ws[-1][1] <= date(2021, 7, 1),
          f"last start {ws[-1][1]}")


def test_grid_gross_cap():
    """build_grid must drop cells whose N*alloc > 100% and include cascade ref."""
    grid = sweep.build_grid()
    names = {c.name for c in grid}
    check("cascade_ref present", 'cascade_ref' in names)
    check("flat_n1_a50 present (gross 50%)", 'flat_n1_a50' in names)
    check("flat_n5_a20 present (gross 100%)", 'flat_n5_a20' in names)
    # gross > 100% must be dropped: n10 x a25 = 250%, n14 x a10 = 140%, n7 x a20 = 140%
    check("flat_n10_a25 dropped (250%)", 'flat_n10_a25' not in names)
    check("flat_n14_a10 dropped (140%)", 'flat_n14_a10' not in names)
    check("flat_n7_a20 dropped (140%)", 'flat_n7_a20' not in names)
    # boundary keepers
    check("flat_n2_a50 present (100%)", 'flat_n2_a50' in names)
    check("flat_n10_a10 present (100%)", 'flat_n10_a10' in names)
    # every flat cell respects the cap
    for c in grid:
        if c.kind == 'flat':
            g = c.n_positions * c.alloc_pct
            check(f"{c.name} gross<=100% ({g:.2f})", g <= 1.0 + 1e-9)


def test_cell_env_flat_calls_only():
    """cell_env for a flat cell must set all 4 call tiers equal, overflow=0,
    put pool zeroed, calls-only caps, and the path/persist env flags."""
    c = sweep.Cell(name='flat_n3_a20', kind='flat', n_positions=3, alloc_pct=0.20)
    env = sweep.cell_env(c, n_iter=100, win_label='roll_x',
                         win_start=date(2022, 1, 1), win_end=date(2023, 1, 1),
                         results_json=Path('x.json'), starting_cash=50_000.0,
                         workers=4)
    check("TIER_ULTRA_OV==0.20", env['TIER_ULTRA_OV'].startswith('0.20'))
    check("TIER_TOP_OV==0.20", env['TIER_TOP_OV'].startswith('0.20'))
    check("TIER_MID_OV==0.20", env['TIER_MID_OV'].startswith('0.20'))
    check("TIER_LOW_OV==0.20", env['TIER_LOW_OV'].startswith('0.20'))
    check("TIER_OVERFLOW_OV==0.0", float(env['TIER_OVERFLOW_OV']) == 0.0)
    check("MAX_POSITIONS_OVERRIDE==3", env['MAX_POSITIONS_OVERRIDE'] == '3')
    check("MAX_POSITIONS_CALL==3", env['MAX_POSITIONS_CALL'] == '3')
    check("MAX_POSITIONS_PUT==0 (calls-only)", env['MAX_POSITIONS_PUT'] == '0')
    check("PUT_TIER_TOP_OV==0", float(env['PUT_TIER_TOP_OV']) == 0.0)
    check("MC_RETURN_PATHS==1", env['MC_RETURN_PATHS'] == '1')
    check("MC_NO_DB_PERSIST==1", env['MC_NO_DB_PERSIST'] == '1')
    check("STARTING_CASH_OV==50000", float(env['STARTING_CASH_OV']) == 50_000.0)
    check("WIN_START set", env['WIN_START'] == '2022-01-01')
    check("WIN_END set", env['WIN_END'] == '2023-01-01')
    check("MC_WORKERS==4", env['MC_WORKERS'] == '4')


def test_cell_env_cascade():
    grid = sweep.build_grid()
    casc = next(c for c in grid if c.name == 'cascade_ref')
    env = sweep.cell_env(casc, n_iter=100, win_label='roll_x',
                         win_start=date(2022, 1, 1), win_end=date(2023, 1, 1),
                         results_json=Path('x.json'), starting_cash=50_000.0,
                         workers=1)
    check("cascade ULTRA==0.20", env['TIER_ULTRA_OV'].startswith('0.20'))
    check("cascade TOP==0.15", env['TIER_TOP_OV'].startswith('0.15'))
    check("cascade MID==0.08", env['TIER_MID_OV'].startswith('0.08'))
    check("cascade LOW==0.03", env['TIER_LOW_OV'].startswith('0.03'))
    check("cascade OVERFLOW==0.0", float(env['TIER_OVERFLOW_OV']) == 0.0)
    check("cascade MaxPos==14", env['MAX_POSITIONS_OVERRIDE'] == '14')
    check("cascade workers=1 -> MC_NO_MP", env.get('MC_NO_MP') == '1')


def main():
    print("=" * 60)
    print("UNIT TESTS — time-to-2x metric core (synthetic, no MC, no MySQL)")
    print("=" * 60)
    for fn in [
        test_basic_reach_and_timing,
        test_2x_before_50dd_ordering,
        test_2x_same_bar_as_50dd_does_not_count,
        test_collapse_threshold,
        test_collapse_independent_of_2x,
        test_calendar_conversion,
        test_compound_and_worstdd_reference,
        test_empty_paths,
        test_quantile_interpolation,
        test_all_reach_all_survive,
        test_pooled_across_windows,
        test_monthly_windows_rolls,
        test_grid_gross_cap,
        test_cell_env_flat_calls_only,
        test_cell_env_cascade,
    ]:
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 60)
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == '__main__':
    main()
