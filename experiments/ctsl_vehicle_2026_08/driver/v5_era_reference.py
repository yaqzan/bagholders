#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
v5_era_reference.py -- SPY/HISA reference for the V5 era windows.

The PREREG's standing rule is "SPY/HISA columns on every table", but
frontier_2026_08's spy_hisa_reference.csv only covers the 12 PHASE_D windows --
so the dot-com and GFC cells would otherwise report a blank index bar, which is
exactly the column those two windows most need.

Method is deliberately IDENTICAL to frontier_run.run_spy_hisa (adjusted `close`
from price_history, first row on/after start and last row on/before end, HISA
4%/yr over calendar days / 365.25) so the era rows are comparable to the twelve
already banked. Writes a SEPARATE file; frontier's own CSV is never touched.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_REPO_ROOT = os.path.dirname(os.path.dirname(_EXP_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
HISA_RATE = 1.04


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def spy_hisa_for(d_start, d_end, DB):
    r0 = DB.execute_sql("SELECT date, close FROM price_history WHERE symbol='SPY' AND date>=%s "
                        "ORDER BY date ASC LIMIT 1", (d_start,)).fetchone()
    r1 = DB.execute_sql("SELECT date, close FROM price_history WHERE symbol='SPY' AND date<=%s "
                        "ORDER BY date DESC LIMIT 1", (d_end,)).fetchone()
    if not r0 or not r1:
        return None
    span = (d_end - d_start).days
    return {
        'spy_actual_start': str(r0[0]), 'spy_start_close': float(r0[1]),
        'spy_actual_end': str(r1[0]), 'spy_end_close': float(r1[1]),
        'spy_return_pct': (float(r1[1]) / float(r0[1]) - 1.0) * 100.0,
        'span_days': span,
        'hisa_return_pct': (HISA_RATE ** (span / 365.25) - 1.0) * 100.0,
    }


def run(log_path, out_csv):
    from database.trader_database import DB
    from ctsl_run import V5_EXTRA_WINDOWS
    DB.connect(reuse_if_open=True)

    _tee(f"\n{'=' * 100}", log_path)
    _tee("V5 era-window SPY/HISA reference (same method as frontier's 12-window table)", log_path)

    rows = []
    for label, (d0, d1) in V5_EXTRA_WINDOWS.items():
        ds, de = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
        res = spy_hisa_for(ds, de, DB)
        if res is None:
            _tee(f"[SPY-HISA] window={label}: NO SPY price_history rows in [{ds}..{de}]", log_path)
            rows.append({'window': label, 'start_date': str(ds), 'end_date': str(de),
                         'spy_start_close': None, 'spy_end_close': None, 'spy_actual_start': None,
                         'spy_actual_end': None, 'spy_return_pct': None, 'span_days': None,
                         'hisa_return_pct': None})
            continue
        _tee(f"[SPY-HISA] window={label} [{ds}..{de}] spy=[{res['spy_actual_start']}:"
            f"{res['spy_start_close']} -> {res['spy_actual_end']}:{res['spy_end_close']}] "
            f"spy_return={res['spy_return_pct']:+.2f}% span_days={res['span_days']} "
            f"hisa_return={res['hisa_return_pct']:+.2f}%", log_path)
        rows.append({'window': label, 'start_date': str(ds), 'end_date': str(de), **res})

    os.makedirs(OUT_DIR, exist_ok=True)
    fieldnames = ['window', 'start_date', 'end_date', 'spy_start_close', 'spy_end_close',
                  'spy_actual_start', 'spy_actual_end', 'spy_return_pct', 'span_days',
                  'hisa_return_pct']
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    _tee(f"[WRITE] {out_csv} ({len(rows)} rows)", log_path)
    return 0


def selftest() -> int:
    print("=== v5_era_reference.py OFFLINE SELF-TESTS ===")
    from ctsl_run import V5_EXTRA_WINDOWS
    assert set(V5_EXTRA_WINDOWS) == {'dotcom', 'gfc'}
    print(f"  [1] era windows imported live from ctsl_run: {V5_EXTRA_WINDOWS} OK")
    # HISA over exactly one year must be 4.00%; over the dot-com span, compounded.
    one_yr = (HISA_RATE ** (365.25 / 365.25) - 1.0) * 100.0
    assert abs(one_yr - 4.0) < 1e-9, one_yr
    span = (dt.date(2002, 12, 31) - dt.date(2000, 1, 1)).days
    hisa = (HISA_RATE ** (span / 365.25) - 1.0) * 100.0
    assert 12.0 < hisa < 12.8, hisa      # ~3 years of 4% compounding
    print(f"  [2] HISA: 1yr = {one_yr:.2f}%; dot-com span ({span}d) = {hisa:.2f}% OK")
    print("=== SELFTEST PASS ===")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    a = p.parse_args()
    if a.selftest:
        return selftest()
    return run(os.path.join(LOG_DIR, 'v5.log'),
               os.path.join(OUT_DIR, 'spy_hisa_era_reference.csv'))


if __name__ == '__main__':
    sys.exit(main())
