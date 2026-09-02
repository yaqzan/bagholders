# Phase 0 — live-data read on entry-timing score fidelity (calls, >=75 gate).
# Uses score_intraday_logs (data since 2026-05-27) to measure, per (sym, date):
#   open_snap  = first run in [09:30, 11:00)  (market-open-ish score)
#   late_snap  = last run in [15:00, 15:55)   (the "buy in the last 30 min" score)
#   close_snap = last run of the day          (the finalized close score the backtest uses)
# Reports qualification flip rates between the three reads + last-30-min price drift.
# Cheap diagnostic only — no outcomes/WR (window too small); Phase A does that historically.
import datetime
from collections import defaultdict

from database.models.core import ScoreIntradayLog

START = datetime.date(2026, 5, 27)
QUAL = 75

rows = list(
    ScoreIntradayLog.select(
        ScoreIntradayLog.symbol,
        ScoreIntradayLog.date,
        ScoreIntradayLog.version,
        ScoreIntradayLog.logged_at,
        ScoreIntradayLog.overall,
        ScoreIntradayLog.price,
    ).where(ScoreIntradayLog.date >= START)
    .order_by(ScoreIntradayLog.symbol, ScoreIntradayLog.date, ScoreIntradayLog.logged_at)
    .namedtuples()
)
print(f"rows loaded: {len(rows)}")

groups = defaultdict(list)
for r in rows:
    groups[(r.symbol, r.date, r.version)].append(r)

# per (sym, date) keep the version group with the most snapshots (active-version proxy)
best = {}
for (sym, d, v), snaps in groups.items():
    key = (sym, d)
    if key not in best or len(snaps) > len(best[key][1]) or (
        len(snaps) == len(best[key][1]) and v > best[key][0]
    ):
        best[key] = (v, snaps)

def pick(snaps):
    open_s = late_s = None
    for s in snaps:
        t = s.logged_at.time()
        if open_s is None and datetime.time(9, 30) <= t < datetime.time(11, 0):
            open_s = s
        if datetime.time(15, 0) <= t < datetime.time(15, 55):
            late_s = s
    close_s = snaps[-1]
    return open_s, late_s, close_s

n_all = 0
n_triple = 0
open_q = 0; open_q_close_q = 0
close_q = 0; close_q_open_q = 0; close_q_late_q = 0
late_q = 0; late_q_close_q = 0
drifts = []
fakeout_examples = []
missed_examples = []
by_date = defaultdict(lambda: [0, 0])  # date -> [open_qual, died_by_close]

for (sym, d), (v, snaps) in sorted(best.items()):
    n_all += 1
    o, l, c = pick(snaps)
    if o is None or l is None or c is None or len(snaps) < 5:
        continue
    n_triple += 1
    oq, lq, cq = o.overall >= QUAL, l.overall >= QUAL, c.overall >= QUAL
    if oq:
        open_q += 1
        by_date[d][0] += 1
        if cq:
            open_q_close_q += 1
        else:
            by_date[d][1] += 1
            if len(fakeout_examples) < 12:
                fakeout_examples.append(f"{sym} {d} open={o.overall}@{o.logged_at:%H:%M} close={c.overall}")
    if cq:
        close_q += 1
        if oq:
            close_q_open_q += 1
        elif len(missed_examples) < 12:
            missed_examples.append(f"{sym} {d} open={o.overall} close={c.overall}")
        if lq:
            close_q_late_q += 1
    if lq:
        late_q += 1
        if cq:
            late_q_close_q += 1
    if l.price and c.price:
        drifts.append(abs(c.price - l.price) / l.price * 100.0)

print(f"\n(sym,date) groups: {n_all}   with open+late+close snapshots: {n_triple}")
print(f"\n--- OPEN-score entry policy (qualify >= {QUAL} at first 09:30-11:00 run) ---")
print(f"open-qualified:            {open_q}")
if open_q:
    print(f"  still >= {QUAL} at close:    {open_q_close_q}  ({100*open_q_close_q/open_q:.1f}%)")
    print(f"  DIED by close (fakeout): {open_q - open_q_close_q}  ({100*(open_q-open_q_close_q)/open_q:.1f}%)")
print(f"\n--- CLOSE-score signals (the backtest universe) ---")
print(f"close-qualified:           {close_q}")
if close_q:
    print(f"  already >= {QUAL} at open:   {close_q_open_q}  ({100*close_q_open_q/close_q:.1f}%)  <- open-entry would catch")
    print(f"  >= {QUAL} at ~15:45:         {close_q_late_q}  ({100*close_q_late_q/close_q:.1f}%)  <- last-30-min entry catches")
print(f"\n--- LAST-30-MIN fidelity (the practical close proxy) ---")
print(f"late(~15:00-15:55)-qualified: {late_q}")
if late_q:
    print(f"  still qualified at close:   {late_q_close_q}  ({100*late_q_close_q/late_q:.1f}%)  (false-positive rate {100*(late_q-late_q_close_q)/late_q:.1f}%)")
if drifts:
    drifts.sort()
    n = len(drifts)
    print(f"  |price drift| late->close: median {drifts[n//2]:.2f}%  p90 {drifts[int(n*0.9)]:.2f}%  (N={n})")

print("\nfakeout examples (open-qual, dead by close):")
for e in fakeout_examples:
    print("  " + e)
print("\nmissed-by-open examples (close-qual, not open-qual):")
for e in missed_examples:
    print("  " + e)

print("\nper-date open-qualified / died-by-close:")
for d in sorted(by_date):
    oq, died = by_date[d]
    print(f"  {d}  {oq:4d} open-qual   {died:3d} died ({(100*died/oq if oq else 0):.0f}%)")
