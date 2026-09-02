"""Out-of-sample calibration holdout enforcement (known-issues.md Priority #11).

Provides two callsites that calibration scripts use to keep post-cutoff data
out of the in-sample window:

    from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter, CUTOFF

    # At sweep entry (defensive — catches accidental leaks):
    df = pl.read_parquet(...)
    assert_no_holdout_leak(df, context='score_velocity sweep')

    # Or at build time (preferred — sweep can't see post-cutoff data at all):
    df = pre_cutoff_filter(df)

The cutoff itself is a single source of truth in `strategy_config.py`
(`CALIBRATION_CUTOFF_DATE`). Bumping it requires a deliberate edit there.

Why this matters: every score-stage dampener shipped through v40 was
calibrated against the SAME 5-10y barrier-touch outcome distribution. Without
a holdout, the H1-H5 gate validates each change against data the change was
designed against. The 2026 forward window is the first real test of whether
the stacked dampeners generalize — but only if we don't sneakily peek at it
during calibration.

Honor system, with assertion catching accidents. Override via env var
`HOLDOUT_DISABLE=1` for live-trading evaluation contexts where seeing
post-cutoff data is intentional (DO NOT use during calibration sweeps).
"""
from __future__ import annotations
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Union

# Single source of truth lives in strategy_config.py at the project root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from strategy_config import CALIBRATION_CUTOFF_DATE as _CUTOFF_STR
except ImportError as e:
    raise ImportError(
        f"experiments._holdout requires strategy_config.CALIBRATION_CUTOFF_DATE — "
        f"could not import (root={_ROOT}): {e}"
    )

# CALIBRATION_CUTOFF_DATE history: DISABLED (None) 2026-06-04, then RE-LOCKED
# 2026-06-15 to "2026-06-15" — the v71/v72 honest-substrate forward-holdout start
# (known-issues #11; OOS re-eval ~2026-12-15). When the cutoff is a date the lock is
# ACTIVE: pre_cutoff_filter / assert_no_holdout_leak apply (unless HOLDOUT_DISABLE=1).
# A None cutoff would set CUTOFF to a far-future sentinel and DISABLED=True (no-ops).
# Read the LIVE value from strategy_config.py, not this comment. Call-sites unchanged.
if _CUTOFF_STR is None:
    CUTOFF: date = date(9999, 12, 31)
    DISABLED: bool = True
else:
    CUTOFF: date = date.fromisoformat(_CUTOFF_STR)
    DISABLED: bool = os.environ.get('HOLDOUT_DISABLE', '0') == '1'


def _coerce_date(x: Any) -> date:
    """Coerce a single value (str/date/datetime/polars-Date) to a date."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        return date.fromisoformat(x[:10])
    # polars sometimes returns its own date types — fall back to isoformat
    iso = getattr(x, 'isoformat', None)
    if callable(iso):
        return date.fromisoformat(iso()[:10])
    raise TypeError(f"cannot coerce {type(x).__name__} to date: {x!r}")


def _max_date_from(df_or_dates: Any) -> date:
    """Extract the maximum date from a DataFrame ('date' column) or iterable."""
    # polars.DataFrame
    try:
        import polars as pl  # type: ignore
        if isinstance(df_or_dates, pl.DataFrame):
            if 'date' not in df_or_dates.columns:
                raise ValueError("DataFrame has no 'date' column")
            if len(df_or_dates) == 0:
                return date.min  # empty frame trivially passes
            return _coerce_date(df_or_dates['date'].max())
    except ImportError:
        pass
    # pandas.DataFrame
    try:
        import pandas as pd  # type: ignore
        if isinstance(df_or_dates, pd.DataFrame):
            if 'date' not in df_or_dates.columns:
                raise ValueError("DataFrame has no 'date' column")
            if len(df_or_dates) == 0:
                return date.min
            return _coerce_date(df_or_dates['date'].max())
    except ImportError:
        pass
    # iterable of dates/strings
    if isinstance(df_or_dates, str):
        return _coerce_date(df_or_dates)
    try:
        seq = list(df_or_dates)
    except TypeError:
        return _coerce_date(df_or_dates)
    if not seq:
        return date.min
    return max(_coerce_date(d) for d in seq)


def assert_no_holdout_leak(df_or_dates: Any, context: str) -> None:
    """Raise AssertionError if any date in the input is past CALIBRATION_CUTOFF_DATE.

    `df_or_dates` may be a polars DataFrame (looks for a 'date' column),
    a pandas DataFrame, an iterable of date-like values, or a single
    date-like value.

    `context` is a free-text label included in the error message.

    No-op if the env var `HOLDOUT_DISABLE=1` is set.
    """
    if DISABLED:
        return
    try:
        max_d = _max_date_from(df_or_dates)
    except Exception as e:
        raise AssertionError(f"[holdout] {context}: cannot extract dates: {e}")
    if max_d > CUTOFF:
        raise AssertionError(
            f"[HOLDOUT LEAK] {context}: data extends to {max_d.isoformat()} "
            f"but calibration cutoff is {CUTOFF.isoformat()}. "
            f"Filter to date <= '{CUTOFF.isoformat()}' before calibration, "
            f"or set HOLDOUT_DISABLE=1 if this is a live-trading evaluation "
            f"(NOT a calibration sweep)."
        )


def pre_cutoff_filter(df: Any) -> Any:
    """Return df filtered to dates ≤ CALIBRATION_CUTOFF_DATE.

    Polars and pandas DataFrames supported. The 'date' column may be a native
    temporal dtype (pl.Date / pl.Datetime / datetime64) or ISO-string typed.
    The cutoff date itself is INCLUDED in the in-sample window.

    Bypasses the filter when HOLDOUT_DISABLE=1.
    """
    if DISABLED:
        return df
    try:
        import polars as pl  # type: ignore
        if isinstance(df, pl.DataFrame):
            # polars >= 1.x forbids temporal-vs-string comparisons in BOTH
            # directions, so the literal must match the column dtype.
            if 'date' in df.columns and df.schema['date'].is_temporal():
                return df.filter(pl.col('date') <= CUTOFF)
            return df.filter(pl.col('date') <= CUTOFF.isoformat())
    except ImportError:
        pass
    try:
        import pandas as pd  # type: ignore
        if isinstance(df, pd.DataFrame):
            return df[df['date'] <= CUTOFF.isoformat()]
    except ImportError:
        pass
    raise TypeError(f"pre_cutoff_filter: unsupported type {type(df).__name__}")


def cutoff_iso() -> str:
    """Convenience: return the cutoff as an ISO string for SQL queries."""
    return CUTOFF.isoformat()


__all__ = [
    'CUTOFF',
    'DISABLED',
    'assert_no_holdout_leak',
    'pre_cutoff_filter',
    'cutoff_iso',
]
