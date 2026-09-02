"""Regression tests for experiments/_holdout.py dtype handling (2026-07-13).

Run: python tests/test_holdout.py   (pytest-compatible too)
No DB required — synthetic frames only.

Locks the polars>=1.x fix in pre_cutoff_filter: a native pl.Date 'date'
column must not raise InvalidOperationError (temporal-vs-string comparison),
while ISO-string 'date' columns keep working — polars 1.x forbids the
mismatched comparison in BOTH directions, so the helper must dispatch on the
column dtype. Also covers pandas datetime64 and assert_no_holdout_leak on a
native Date column.
"""
import os
import sys
from contextlib import contextmanager
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import experiments._holdout as _holdout

FAILURES = []
FIXED_CUTOFF = date(2026, 6, 15)


def check(name, cond, detail=""):
    if cond:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


@contextmanager
def forced_lock():
    """Force the holdout lock ACTIVE at a fixed cutoff so results don't depend
    on the live strategy_config value or the HOLDOUT_DISABLE env var."""
    old = (_holdout.CUTOFF, _holdout.DISABLED)
    _holdout.CUTOFF, _holdout.DISABLED = FIXED_CUTOFF, False
    try:
        yield
    finally:
        _holdout.CUTOFF, _holdout.DISABLED = old


def test_pre_cutoff_filter_polars_native_date():
    print("[1] pre_cutoff_filter: polars native temporal 'date' column")
    import polars as pl
    df = pl.DataFrame({
        'date': [FIXED_CUTOFF - timedelta(days=1), FIXED_CUTOFF,
                 FIXED_CUTOFF + timedelta(days=1)],
        'x': [1, 2, 3],
    }).with_columns(pl.col('date').cast(pl.Date))
    with forced_lock():
        out = _holdout.pre_cutoff_filter(df)
        check("pl.Date column does not raise", True)
        check("cutoff day included, post-cutoff dropped",
              out['x'].to_list() == [1, 2], out['x'].to_list())
        out_dt = _holdout.pre_cutoff_filter(
            df.with_columns(pl.col('date').cast(pl.Datetime)))
        check("pl.Datetime column also works",
              out_dt['x'].to_list() == [1, 2], out_dt['x'].to_list())
    assert not FAILURES, FAILURES


def test_pre_cutoff_filter_polars_string_date():
    print("[2] pre_cutoff_filter: polars ISO-string 'date' column (back-compat)")
    import polars as pl
    df = pl.DataFrame({'date': ['2026-06-14', '2026-06-15', '2026-06-16'],
                       'x': [1, 2, 3]})
    with forced_lock():
        out = _holdout.pre_cutoff_filter(df)
        check("string column keeps working",
              out['x'].to_list() == [1, 2], out['x'].to_list())
    assert not FAILURES, FAILURES


def test_pre_cutoff_filter_pandas():
    print("[3] pre_cutoff_filter: pandas datetime64 'date' column")
    import pandas as pd
    df = pd.DataFrame({
        'date': pd.to_datetime(['2026-06-14', '2026-06-15', '2026-06-16']),
        'x': [1, 2, 3],
    })
    with forced_lock():
        out = _holdout.pre_cutoff_filter(df)
        check("pandas datetime64 keeps working",
              out['x'].tolist() == [1, 2], out['x'].tolist())
    assert not FAILURES, FAILURES


def test_assert_no_holdout_leak_polars_native_date():
    print("[4] assert_no_holdout_leak: polars native Date column")
    import polars as pl
    clean = pl.DataFrame({'date': [FIXED_CUTOFF]})
    leaky = pl.DataFrame({'date': [FIXED_CUTOFF, FIXED_CUTOFF + timedelta(days=1)]})
    with forced_lock():
        _holdout.assert_no_holdout_leak(clean, context='test-clean')
        check("clean frame passes", True)
        raised = False
        try:
            _holdout.assert_no_holdout_leak(leaky, context='test-leak')
        except AssertionError:
            raised = True
        check("leaky frame raises AssertionError", raised)
    assert not FAILURES, FAILURES


def main():
    test_pre_cutoff_filter_polars_native_date()
    test_pre_cutoff_filter_polars_string_date()
    test_pre_cutoff_filter_pandas()
    test_assert_no_holdout_leak_polars_native_date()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("All holdout dtype checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
