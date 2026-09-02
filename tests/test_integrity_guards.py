"""Regression tests for the 2026-06-09 scoring-integrity audit fixes (F1-F4).

Run: python tests/test_integrity_guards.py
No DB required — everything is synthetic / temp-file based.

Covers:
  F2 — _spy_wk_last_completed returns the PRIOR week's value for mid-week dates
       (the v69 weekly look-ahead class; a Wednesday must NOT see its own week).
  F4 — compute_pit_mcap_b / build_pit_mcap_map point-in-time market-cap proxy.
  F1 — sector_breadth_wave loud guard fires when enabled+source missing, and a
       rebuilt source CSV actually produces a non-empty wave series.
  F3 — _load_cont_barrier_wins warns when requested lo_date precedes cache
       coverage (truncated-cache landmine).
"""
import io
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


def test_f2_spy_wk_last_completed():
    print("[F2] spy_wk last-completed-week lookup")
    from database.models.core import _spy_wk_last_completed, _spy_wk_on_or_before

    # Monday-keyed weekly composites: Mon 2026-06-01 (week A), Mon 2026-06-08 (week B)
    wk_map = {date(2026, 6, 1): 40.0, date(2026, 6, 8): 70.0}
    wk_dates = sorted(wk_map.keys())

    # A WEDNESDAY inside week B (2026-06-10) must see week A's value (40), not
    # week B's row (70) which holds the complete (future-containing) week.
    wed = date(2026, 6, 10)
    check("wednesday returns PRIOR week",
          _spy_wk_last_completed(wk_dates, wk_map, wed) == 40.0,
          f"got {_spy_wk_last_completed(wk_dates, wk_map, wed)}")
    # The un-lagged lookup (kept for reference) WOULD have leaked week B:
    check("un-lagged lookup confirms the leak shape",
          _spy_wk_on_or_before(wk_dates, wk_map, wed) == 70.0)

    # Monday of week B itself must also see week A (no same-week read).
    mon = date(2026, 6, 8)
    check("monday returns PRIOR week",
          _spy_wk_last_completed(wk_dates, wk_map, mon) == 40.0)

    # The NEXT Monday (week C, 2026-06-15) may see week B — it is completed.
    nxt = date(2026, 6, 15)
    check("next-week monday sees completed week B",
          _spy_wk_last_completed(wk_dates, wk_map, nxt) == 70.0)

    # Before any data -> None
    check("pre-history returns None",
          _spy_wk_last_completed(wk_dates, wk_map, date(2026, 5, 30)) is None)


def test_f4_pit_mcap():
    print("[F4] point-in-time mcap proxy")
    from database.utils.scoring import compute_pit_mcap_b, build_pit_mcap_map

    # Stock at $100B today with close 200; historical close 50 -> $25B then.
    check("scales by close ratio",
          abs(compute_pit_mcap_b(100.0, 50.0, 200.0) - 25.0) < 1e-9)
    check("today's close is identity",
          abs(compute_pit_mcap_b(100.0, 200.0, 200.0) - 100.0) < 1e-9)
    check("missing close falls back to static",
          compute_pit_mcap_b(100.0, None, 200.0) == 100.0)
    check("missing anchor falls back to static",
          compute_pit_mcap_b(100.0, 50.0, None) == 100.0)
    check("zero anchor falls back to static",
          compute_pit_mcap_b(100.0, 50.0, 0.0) == 100.0)
    check("None mcap stays None",
          compute_pit_mcap_b(None, 50.0, 200.0) is None)

    class Row:
        def __init__(self, d, c):
            self.date, self.close = d, c

    rows = [Row(date(2026, 1, 2), 50.0), Row(date(2026, 1, 5), 100.0),
            Row(date(2026, 1, 6), None), Row(date(2026, 1, 7), 200.0)]
    m = build_pit_mcap_map(100.0, rows, 200.0)
    check("map scales each date",
          abs(m[date(2026, 1, 2)] - 25.0) < 1e-9 and abs(m[date(2026, 1, 5)] - 50.0) < 1e-9)
    check("map None-close date falls back to static", m[date(2026, 1, 6)] == 100.0)
    check("map anchor date is identity", abs(m[date(2026, 1, 7)] - 100.0) < 1e-9)
    check("None mcap -> empty map", build_pit_mcap_map(None, rows, 200.0) == {})


def test_f1_wave_guard():
    print("[F1] sector_breadth_wave missing-source guard")
    import database.utils.sector_breadth_wave as sbw

    sbw._GUARD_WARNED.clear()
    sbw._SERIES_CACHE.clear()
    params = {
        "enabled": True, "mode": "direct_market_wave", "source": "market_wave",
        "path": os.path.join(tempfile.gettempdir(), "no_such_wave_source_xyz.csv"),
        "call_k": 0.48, "call_target": 63.0, "stress_start": 35.2, "stress_full": 5.4,
        "stress_power": 2.27, "put_k": 0.36, "put_target": 28.6,
        "repair_start": 67.2, "repair_full": 74.9, "repair_power": 1.66,
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        new_score, meta = sbw.adjusted_score(80.0, "call", date(2026, 6, 9), params)
    err = buf.getvalue()
    check("missing source -> loud warning", "INERT" in err, f"stderr={err!r}")
    check("missing source -> score passes through", new_score == 80)

    # Positive path: a tiny synthetic source produces a real series + adjustment
    # machinery (90 rows trending down hard -> stress regime).
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    try:
        tmp.write("date,pct_above_ema50,pct_above_ema200,avg_rsi\n")
        from datetime import timedelta
        d0 = date(2026, 1, 5)
        for i in range(90):
            d = d0 + timedelta(days=i)
            val = max(0.0, 90.0 - i * 1.5)   # collapsing breadth -> low wave
            tmp.write(f"{d.isoformat()},{val:.2f},{val:.2f},{val:.2f}\n")
        tmp.close()
        sbw._GUARD_WARNED.clear()
        sbw._SERIES_CACHE.clear()
        params2 = dict(params, path=tmp.name)
        series = sbw.get_series(params2)
        check("rebuilt source loads non-empty series", bool(series.dates))
        check("market_wave_score computed", bool(series.wave))
        last_d = series.dates[-1]
        s_adj, meta2 = sbw.adjusted_score(80.0, "call", last_d, params2)
        check("deep-stress call gets dampened",
              s_adj < 80 and meta2.get("delta", 0) < 0,
              f"adj={s_adj} meta={meta2}")
    finally:
        os.unlink(tmp.name)


def test_f3_cont_cache_coverage_warning():
    print("[F3] continuation-echo cache coverage guard")
    import database.models.core as core

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        con = sqlite3.connect(tmp.name)
        con.execute("""CREATE TABLE barrier_outcomes
                       (symbol TEXT, side TEXT, barrier_set TEXT,
                        w_days INTEGER, date TEXT, result INTEGER)""")
        con.execute("INSERT INTO barrier_outcomes VALUES "
                    "('AAPL','low','30dte_generic',7,'2025-12-19',1)")
        con.commit()
        con.close()

        core._CONT_CACHE_COVERAGE_WARNED = False
        buf = io.StringIO()
        with redirect_stderr(buf):
            wins = core._load_cont_barrier_wins(
                'AAPL', date(2020, 1, 1), date(2026, 1, 1), cache_db=tmp.name)
        err = buf.getvalue()
        check("truncated cache -> loud warning",
              "truncated" in err and "2025-12-19" in err, f"stderr={err!r}")
        check("rows inside coverage still returned",
              date(2025, 12, 19) in wins)

        # Within coverage -> no warning
        core._CONT_CACHE_COVERAGE_WARNED = False
        buf2 = io.StringIO()
        with redirect_stderr(buf2):
            core._load_cont_barrier_wins(
                'AAPL', date(2025, 12, 19), date(2026, 1, 1), cache_db=tmp.name)
        check("in-coverage request stays quiet", buf2.getvalue() == "")
    finally:
        core._CONT_CACHE_COVERAGE_WARNED = False
        import gc
        gc.collect()   # release lingering sqlite handle (Windows file lock)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass   # temp file; OS will clean up


def main():
    test_f2_spy_wk_last_completed()
    test_f4_pit_mcap()
    test_f1_wave_guard()
    test_f3_cont_cache_coverage_warning()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("All integrity-guard checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
