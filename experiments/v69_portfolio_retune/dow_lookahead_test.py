"""Day-of-week look-ahead detector across v60 / v65 / v69.

The weekly look-ahead leaks the current week's FUTURE bars into an early-week (Mon)
signal's score. So a look-ahead version's Monday 75+ cohort is selected partly on
future info -> its realized forward returns are inflated and DECAY toward Friday
(by Friday the week is ~complete, little future left to leak). An honest version is
~FLAT across weekdays (only natural effects remain).

This is the same signal that diagnosed the original bug. It reads stored scores
directly (no recompute) and measures each version's 75+ cohort forward stock return
by signal weekday. Decisive test of WHICH versions are actually look-ahead-free.

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/dow_lookahead_test.py
"""
from __future__ import annotations
import os, sys
from collections import defaultdict
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from database.models.core import Score   # noqa: E402

db = Score._meta.database
HOLD = 15          # trading-day forward window (matches the 30 DTE hold)
NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']


def main():
    print("loading universe + closes...", flush=True)
    syms = [r[0] for r in db.execute_sql(
        "SELECT symbol FROM stocks WHERE sector IS NOT NULL").fetchall()]
    symset = set(syms)
    rows_by_sym = defaultdict(list)
    for i in range(0, len(syms), 150):
        chunk = syms[i:i+150]; ph = ','.join(['%s'] * len(chunk))
        for sym, d, c in db.execute_sql(
                f"SELECT symbol,date,close FROM price_history WHERE symbol IN ({ph}) "
                f"AND date>='2015-06-01' ORDER BY symbol,date", tuple(chunk)):
            rows_by_sym[sym].append((d, float(c)))
    idx_by_sym, cl_by_sym = {}, {}
    for sym, rws in rows_by_sym.items():
        cl_by_sym[sym] = [c for _, c in rws]
        idx_by_sym[sym] = {d: i for i, (d, _) in enumerate(rws)}

    def fwd(sym, d):
        idx = idx_by_sym.get(sym)
        if not idx:
            return None
        i = idx.get(d)
        if i is None or i + HOLD >= len(cl_by_sym[sym]) or cl_by_sym[sym][i] <= 0:
            return None
        return cl_by_sym[sym][i + HOLD] / cl_by_sym[sym][i] - 1.0

    for vid, lab in [(60, 'v60 inflated'), (65, 'v65 (claimed honest)'), (69, 'v69 (shipped honest)')]:
        sigs = db.execute_sql(
            "SELECT symbol,date FROM scores WHERE version_id=%s AND overall>=75 "
            "AND date>='2016-01-01'", (vid,)).fetchall()
        byd = defaultdict(list)
        for sym, d in sigs:
            if sym not in symset:
                continue
            f = fwd(sym, d)
            if f is not None:
                byd[d.weekday()].append(f)
        print(f"\n=== v{vid}  {lab}  ===  75+ cohort, {HOLD}-trading-day forward stock return by signal weekday")
        print(f"  {'day':<5}{'N':>8}{'meanFwd':>11}{'win%':>9}")
        mon = fri = None
        for wd in range(5):
            fs = byd.get(wd, [])
            if not fs:
                continue
            a = np.array(fs); win = float((a > 0).mean() * 100)
            if wd == 0: mon = (a.mean() * 100, win)
            if wd == 4: fri = (a.mean() * 100, win)
            print(f"  {NAMES[wd]:<5}{len(fs):>8}{a.mean()*100:>+10.2f}%{win:>8.1f}%")
        if mon and fri:
            print(f"  Mon->Fri gradient: meanFwd {mon[0]:+.2f}% -> {fri[0]:+.2f}%  ({mon[0]-fri[0]:+.2f}pp), "
                  f"win {mon[1]:.1f}% -> {fri[1]:.1f}%  ({mon[1]-fri[1]:+.1f}pp)")
    print("\nRead: a LOOK-AHEAD version shows Monday >> Friday (steep positive Mon->Fri gradient).")
    print("      An HONEST version is ~flat. This says which 'honest' label is real.")


if __name__ == '__main__':
    main()
