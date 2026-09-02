"""Wave/Cycle Mine -- shared W-family calendar library (pure functions of `date`).

Implements experiments/wave_cycle_mine/PREREGISTRATION.md's W-family feature
definitions (W1-W6). SHARED by mine.py (applied to both the main study frame
and the 2016-2020 era-mirror slice) and prep_mirror.py (diagnostic use) --
per the brief: "the calendar functions are shared; factor them so both
scripts use one implementation." This is a genuine shared-library import
(NOT the peak_fakeout/trend_ma_lattice "copy not import" situation -- that
one is about a `features.py` NAME COLLISION across sibling experiment dirs
each importing their OWN local features.py; this module has a unique name
and is co-located with its only two callers, so a real `import
calendar_features` is safe and correct here).

TRADING-DAY INDEX SOURCE (coordinator correctness patch, 2026-07-16):
database/utils/trading_calendar.py's static NYSE_HOLIDAYS table only covers
2023-2026, which would mislabel days_to_opex by +-1 near ~9 holidays/year in
the 2016-2020 era-replication slice and pollute the narrow TOM/quarter-end
buckets (real in-range cases: April 2019 OPEX was Thursday 2019-04-18 since
Good Friday 2019-04-19 WAS the 3rd Friday; 2018-12-05 was a full one-off
closure, G.H.W. Bush national day of mourning; Juneteenth only from 2022).
So the index here is EMPIRICAL: a date in [start, OHLC_MAX] is a trading day
iff >= MIN_SYMBOLS_TRADING_DAY (50) distinct symbols have a bar in
.cache/intraday_overnight/ohlc.parquet that date (threshold guards against
stray bad-data bars on holidays -- observed: holiday dates carry 3-4 stray
symbols). Dates AFTER the OHLC max (2026-06-08) -- needed only for the
index tail (next-OPEX lookups near the boundary), never for any entry row --
fall back to the static is_trading_day (its table covers 2023-2026, so the
fallback region is safe). The two sources are cross-validated on their
2023-01-01..OHLC_MAX overlap and any disagreement HARD-FAILS the run --
except one pinned, externally-verified exemption (see
STATIC_TABLE_KNOWN_MISSING_CLOSURES).

All lookups are built ONCE from the precomputed trading-day index -- see
WCalendar. No look-ahead: the empirical index is derived from bar EXISTENCE
(market open/closed -- a fixed public fact of the calendar), never from
price/outcome VALUES, and every feature function is a pure function of
`date` + that index.
"""
from __future__ import annotations

import bisect
import os
import sys
import time
from datetime import date, timedelta

import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database.utils.trading_calendar import is_trading_day  # noqa: E402

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]

CAL_START = date(2015, 1, 1)
CAL_END = date(2026, 12, 31)

OHLC_PATH = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
MIN_SYMBOLS_TRADING_DAY = 50   # >= this many distinct symbols with a bar -> trading day
STATIC_VALIDATION_START = date(2023, 1, 1)  # static NYSE_HOLIDAYS coverage starts 2023

# DEVIATION (from the coordinator patch spec's literal "hard-fail if they
# disagree on any date"): the overlap window contains exactly ONE genuine
# disagreement -- 2025-01-09, the National Day of Mourning for President
# Carter, a real full NYSE closure (empirical: 4 stray bars -> CLOSED) that
# the static NYSE_HOLIDAYS table is MISSING (static: ordinary Thursday ->
# OPEN). A literal implementation can therefore never pass. The empirical
# source is authoritative for observed dates (that is this patch's entire
# premise), so this single externally-verifiable closure is pinned as an
# exemption, honored ONLY in the empirical=CLOSED/static=OPEN direction.
# Any other disagreement date, or this date in the opposite direction,
# still hard-fails. Enumerated 2026-07-16 before this code was written.
STATIC_TABLE_KNOWN_MISSING_CLOSURES = {
    date(2025, 1, 9),   # National Day of Mourning (Carter) -- NYSE closed; absent from static table
}


def _load_empirical_trading_days():
    """(empirical_days: set[date], ohlc_max: date). A date is a trading day
    iff >= MIN_SYMBOLS_TRADING_DAY distinct symbols have a bar that date in
    the OHLC parquet. Loaded ONCE per WCalendar construction (each script
    builds one WCalendar per process)."""
    t0 = time.time()
    df = pl.read_parquet(OHLC_PATH, columns=["symbol", "date"])
    counts = (df.group_by("date").agg(pl.col("symbol").n_unique().alias("n_symbols"))
                .with_columns(pl.col("date").str.to_date()))
    ohlc_max = counts["date"].max()
    n_all = counts.height
    kept = counts.filter(pl.col("n_symbols") >= MIN_SYMBOLS_TRADING_DAY)
    emp_days = set(kept["date"].to_list())
    print(f"[calendar] empirical trading-day source {OHLC_PATH}: {n_all} distinct dates, "
          f"{len(emp_days)} pass >= {MIN_SYMBOLS_TRADING_DAY}-symbol threshold "
          f"({n_all - len(emp_days)} stray-bar holiday dates rejected), max_date={ohlc_max} "
          f"({time.time()-t0:.1f}s)", flush=True)
    return emp_days, ohlc_max


def _validate_overlap(emp_days: set, ohlc_max: date) -> None:
    """Assert the empirical and static calendars AGREE on their
    [STATIC_VALIDATION_START, ohlc_max] overlap (doubles as validation of
    both sources). Hard-fails (RuntimeError) on any disagreement not pinned
    in STATIC_TABLE_KNOWN_MISSING_CLOSURES (empirical-CLOSED direction only)."""
    d = STATIC_VALIDATION_START
    one = timedelta(days=1)
    disagreements = []
    exempted = []
    n_checked = 0
    while d <= ohlc_max:
        emp_open = d in emp_days
        stat_open = is_trading_day(d)
        n_checked += 1
        if emp_open != stat_open:
            if (d in STATIC_TABLE_KNOWN_MISSING_CLOSURES) and (not emp_open) and stat_open:
                exempted.append(d)
            else:
                disagreements.append((d, "OPEN" if emp_open else "CLOSED",
                                       "OPEN" if stat_open else "CLOSED"))
        d += one
    for e in exempted:
        print(f"[calendar] overlap validation: {e.isoformat()} exempted (known one-off closure "
              f"missing from the static NYSE_HOLIDAYS table; empirical=CLOSED is correct)", flush=True)
    if disagreements:
        detail = "; ".join(f"{dd.isoformat()} empirical={a} static={b}" for dd, a, b in disagreements[:20])
        raise RuntimeError(
            f"[calendar] HARD FAIL: empirical vs static trading-calendar disagreement on "
            f"{len(disagreements)} date(s) in the {STATIC_VALIDATION_START.isoformat()}..{ohlc_max.isoformat()} "
            f"overlap (not in the pinned exemption set): {detail}. One of the two sources is wrong or the "
            f"OHLC parquet is damaged -- do NOT proceed; investigate before re-running."
        )
    print(f"[calendar] overlap validation {STATIC_VALIDATION_START.isoformat()}..{ohlc_max.isoformat()}: "
          f"{n_checked} calendar dates checked, unexplained disagreements=0 (exempted={len(exempted)}) "
          f"-> AGREE", flush=True)


def build_trading_day_index(start: date = CAL_START, end: date = CAL_END) -> list:
    """Sorted list of trading days in [start, end], inclusive. EMPIRICAL
    (OHLC-derived) through the parquet's max date, static-table fallback
    after it; the two sources are overlap-validated first (hard-fail on
    unexplained disagreement). See module docstring."""
    emp_days, ohlc_max = _load_empirical_trading_days()
    _validate_overlap(emp_days, ohlc_max)
    days = []
    d = start
    one = timedelta(days=1)
    n_fallback = 0
    while d <= end:
        if d <= ohlc_max:
            if d in emp_days:
                days.append(d)
        else:
            if is_trading_day(d):
                days.append(d)
                n_fallback += 1
        d += one
    print(f"[calendar] stitched index {start.isoformat()}..{end.isoformat()}: {len(days)} trading days "
          f"(empirical through {ohlc_max.isoformat()}, static fallback for {n_fallback} tail days)", flush=True)
    return days


def _third_friday(year: int, month: int) -> date:
    """Calendar 3rd Friday of (year, month) -- weekday()==4 is Friday."""
    d0 = date(year, month, 1)
    offset = (4 - d0.weekday()) % 7
    first_friday = d0 + timedelta(days=offset)
    return first_friday + timedelta(days=14)


class WCalendar:
    """Precomputed trading-day index + derived W-family lookup tables.

    Built ONCE (mine.py builds a single instance and reuses it for both the
    main frame and the era-mirror slice -- same instance, so OPEX-day /
    turn-of-month derivations are identical for both).
    """

    def __init__(self, start: date = CAL_START, end: date = CAL_END):
        self.start, self.end = start, end
        self.trading_days = build_trading_day_index(start, end)
        self.index_of = {d: i for i, d in enumerate(self.trading_days)}
        self.opex_days = self._compute_opex_days()
        self._opex_sorted = sorted(self.opex_days)
        self.week_has_opex = {self._week_key(d) for d in self.opex_days}
        self.rank_asc, self.rank_desc = self._compute_month_ranks()

    def _compute_opex_days(self) -> list:
        """One OPEX day per calendar month spanned by the trading-day index.
        OPEX day := 3rd Friday, moved to the PRECEDING trading day if that
        Friday is not itself a trading day (market holiday/closure).
        Membership is tested against the STITCHED index (self.index_of),
        NOT the static is_trading_day -- the static table only covers
        2023-2026 and would leave e.g. April 2019 OPEX on Good Friday
        2019-04-19 instead of shifting it to 2019-04-18."""
        out = []
        months = sorted({(d.year, d.month) for d in self.trading_days})
        for y, m in months:
            cand = _third_friday(y, m)
            while cand not in self.index_of:
                cand -= timedelta(days=1)
                if cand < self.start:
                    raise RuntimeError(f"[calendar] OPEX shift for {y}-{m:02d} walked past index start "
                                        f"{self.start.isoformat()} -- index is broken")
            out.append(cand)
        return out

    @staticmethod
    def _week_key(d: date) -> date:
        """Monday of the Mon-Fri calendar week containing d."""
        return d - timedelta(days=d.weekday())

    def _compute_month_ranks(self):
        """Per-month trading-day ranks: rank_asc[d]=1 for the month's first
        trading day (2nd trading day = 2, ...); rank_desc[d]=1 for the
        month's LAST trading day (2nd-to-last = 2, ...)."""
        rank_asc, rank_desc = {}, {}
        by_month = {}
        for d in self.trading_days:
            by_month.setdefault((d.year, d.month), []).append(d)
        for _key, days in by_month.items():
            n = len(days)
            for i, d in enumerate(days):
                rank_asc[d] = i + 1
                rank_desc[d] = n - i
        return rank_asc, rank_desc

    # ---- per-date lookups (W1-W6) ------------------------------------------
    def days_to_opex(self, d: date):
        """W1 raw: count of trading days strictly after d up to and
        including the next OPEX day (d itself an OPEX day -> 0). None if d
        is not a recognized trading day, or no future OPEX day is indexed."""
        idx = self.index_of.get(d)
        if idx is None:
            return None
        pos = bisect.bisect_left(self._opex_sorted, d)
        if pos >= len(self._opex_sorted):
            return None
        next_opex = self._opex_sorted[pos]
        opex_idx = self.index_of.get(next_opex)
        if opex_idx is None:
            return None
        return opex_idx - idx

    def opex_week(self, d: date) -> bool:
        """W2: d falls in the Mon-Fri calendar week containing an OPEX day."""
        return self._week_key(d) in self.week_has_opex

    def turn_of_month(self, d: date) -> bool:
        """W3: d is among the last 3 or first 2 trading days of its month."""
        ra, rd = self.rank_asc.get(d), self.rank_desc.get(d)
        if ra is None or rd is None:
            return False
        return ra <= 2 or rd <= 3

    @staticmethod
    def dow(d: date) -> str:
        """W4: weekday name, Mon..Fri."""
        return DOW_NAMES[d.weekday()]

    @staticmethod
    def month_half(d: date) -> str:
        """W5: May-Oct vs Nov-Apr."""
        return "may_oct" if 5 <= d.month <= 10 else "nov_apr"

    def quarter_end_week(self, d: date) -> bool:
        """W6: d is among the last 5 trading days of Mar/Jun/Sep/Dec."""
        if d.month not in (3, 6, 9, 12):
            return False
        rd = self.rank_desc.get(d)
        return rd is not None and rd <= 5


# =============================================================================
# Fixed bucketing (locked cuts, discretion-free -- no data-dependent quantiles
# for the W-family; S-family terciles are computed separately in mine.py/
# stats.py from the pooled substrate).
# =============================================================================
def bucket_w1(days_to_opex):
    if days_to_opex is None:
        return None
    if days_to_opex <= 3:
        return "0-3"
    if days_to_opex <= 8:
        return "4-8"
    if days_to_opex <= 13:
        return "9-13"
    return "ge14"


def _yn(b):
    if b is None:
        return None
    return "yes" if b else "no"


W_FEATURE_LABELS = [
    "W1_days_to_opex", "W2_opex_week", "W3_turn_of_month",
    "W4_dow", "W5_month_half", "W6_quarter_end_week",
]


def add_w_columns(df: pl.DataFrame, cal: WCalendar, date_col: str = "date") -> pl.DataFrame:
    """Append raw + bucket (`b_`-prefixed) W-family columns to `df`, computed
    from `date_col` via the shared WCalendar. Pure function of date -- no
    look-ahead, no price/outcome dependency. Used on BOTH the main study
    frame and the 2016-2020 era-mirror slice (same WCalendar instance
    passed by the caller keeps OPEX-day derivation identical for both)."""
    dates = df[date_col].to_list()
    w1_raw = [cal.days_to_opex(d) for d in dates]
    w2_raw = [cal.opex_week(d) for d in dates]
    w3_raw = [cal.turn_of_month(d) for d in dates]
    w4_raw = [cal.dow(d) for d in dates]
    w5_raw = [cal.month_half(d) for d in dates]
    w6_raw = [cal.quarter_end_week(d) for d in dates]

    out = df.with_columns([
        pl.Series("w1_days_to_opex", w1_raw, dtype=pl.Int64),
        pl.Series("w2_opex_week", w2_raw, dtype=pl.Boolean),
        pl.Series("w3_turn_of_month", w3_raw, dtype=pl.Boolean),
        pl.Series("w4_dow", w4_raw, dtype=pl.Utf8),
        pl.Series("w5_month_half", w5_raw, dtype=pl.Utf8),
        pl.Series("w6_quarter_end_week", w6_raw, dtype=pl.Boolean),
    ])
    out = out.with_columns([
        pl.Series("b_W1_days_to_opex", [bucket_w1(x) for x in w1_raw], dtype=pl.Utf8),
        pl.Series("b_W2_opex_week", [_yn(x) for x in w2_raw], dtype=pl.Utf8),
        pl.Series("b_W3_turn_of_month", [_yn(x) for x in w3_raw], dtype=pl.Utf8),
        pl.Series("b_W4_dow", w4_raw, dtype=pl.Utf8),
        pl.Series("b_W5_month_half", w5_raw, dtype=pl.Utf8),
        pl.Series("b_W6_quarter_end_week", [_yn(x) for x in w6_raw], dtype=pl.Utf8),
    ])
    return out
