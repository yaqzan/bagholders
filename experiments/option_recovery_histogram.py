"""Empirical option-recovery distribution for dead-hold parameter calibration.

For each historical signal (call OR put) where the option pnl reached <= -0.50
intraday at any bar within DTE, what's the maximum subsequent recovery before
expiry?

Output: histogram of `max(option_pnl)` AFTER the worst-pnl bar, restricted to
signals where the worst pnl crossed the dead-hold trigger zone.

This calibrates the initial bounds for the (DEAD_HOLD_TRIGGER_PNL,
DEAD_HOLD_POPOUT_PNL) Bayesian sweep. If 30%+ of triggered signals recover
above -10% before expiry, dead-hold has real alpha. If <5% do, it's
defensive-only.

Usage:
    python experiments/option_recovery_histogram.py [days] [--dte 30|15] [--gap-only]

  days:       lookback in days (default 1825 = 5y)
  --dte:      DTE strategy to evaluate (default 30)
  --gap-only: restrict to signals that experienced an underlying gap-down
              >= 5% within first 3 bars (analytical filter, not optimization)
"""
from __future__ import annotations
import sys, math, time
import numpy as np
from datetime import date, timedelta
from collections import defaultdict


# --- argv parsing ----------------------------------------------------------
LOOKBACK = 1825
DTE_LABEL = '30'
GAP_ONLY = False
i = 1
while i < len(sys.argv):
    arg = sys.argv[i]
    if arg == '--gap-only':
        GAP_ONLY = True
    elif arg == '--dte':
        DTE_LABEL = sys.argv[i + 1]
        i += 1
    elif arg.startswith('--dte='):
        DTE_LABEL = arg.split('=')[-1]
    elif arg.isdigit():
        LOOKBACK = int(arg)
    i += 1

assert DTE_LABEL in ('30', '15'), f'DTE must be 30 or 15, got {DTE_LABEL}'


# --- imports (post-arg-parse so help is fast) -------------------------------
from database.trader_database import DB
DB.connect(reuse_if_open=True)
from database.models.core import AlgorithmVersion, Score
from database.models.technical import PriceHistory
from database.barrier_walk_numba import trace_option_pnl_path, warmup
from strategy_config import by_dte


CFG = by_dte(DTE_LABEL)
HOLD_DAYS = CFG.HOLD_DAYS              # 15 for 30 DTE, 7 for 15 DTE
PREMIUM_MULT = CFG.PREMIUM_MULT        # 1.82 vs 1.29
TOTAL_DTE = HOLD_DAYS                  # theta scaling — model holds to hard sell
PUT_THRESHOLD = CFG.PUT_THRESHOLD
CALL_THRESHOLD = CFG.PRIMARY_THRESHOLD


# --- load signals -----------------------------------------------------------
print(f'Recovery histogram for {DTE_LABEL} DTE strategy '
      f'(HOLD={HOLD_DAYS} bars, PREMIUM_MULT={PREMIUM_MULT})')
print(f'Lookback: {LOOKBACK} days')
print(f'Gap-only filter: {GAP_ONLY}')
print()

cutoff = date.today() - timedelta(days=LOOKBACK)
active = AlgorithmVersion.get_active_scores_version()
msg = getattr(active, 'git_message', None) or ''
print(f'Active scoring version: id={active.id}  ({msg[:60]})')

calls = list(Score
             .select(Score.symbol, Score.date, Score.overall)
             .where((Score.version == active)
                    & (Score.overall >= CALL_THRESHOLD)
                    & (Score.date >= cutoff))
             .namedtuples())
puts = list(Score
            .select(Score.symbol, Score.date, Score.overall)
            .where((Score.version == active)
                   & (Score.overall <= PUT_THRESHOLD)
                   & (Score.date >= cutoff))
            .namedtuples())
print(f'Loaded {len(calls)} call signals + {len(puts)} put signals')


# --- bulk-load price history ------------------------------------------------
syms = {s.symbol for s in calls} | {s.symbol for s in puts}
print(f'Unique symbols: {len(syms)}')
buffer_start = cutoff - timedelta(days=120)   # σ_daily 60-bar lookback + slack

t0 = time.time()
ph = defaultdict(list)
syms_list = list(syms)
chunk = 100
for i in range(0, len(syms_list), chunk):
    batch = syms_list[i:i + chunk]
    rows = list(PriceHistory
                .select(PriceHistory.symbol, PriceHistory.date,
                        PriceHistory.open, PriceHistory.high,
                        PriceHistory.low, PriceHistory.close)
                .where((PriceHistory.symbol.in_(batch))
                       & (PriceHistory.date >= buffer_start))
                .order_by(PriceHistory.symbol, PriceHistory.date)
                .namedtuples())
    for r in rows:
        ph[r.symbol].append(r)

# Convert to numpy arrays per symbol
ph_np = {}
for sym, rows in ph.items():
    closes = np.array([float(r.close) for r in rows])
    highs  = np.array([float(r.high)  for r in rows])
    lows   = np.array([float(r.low)   for r in rows])
    opens  = np.array([float(r.open)  for r in rows])
    dates  = [r.date for r in rows]
    date_idx = {d: i for i, d in enumerate(dates)}
    ph_np[sym] = (closes, highs, lows, opens, dates, date_idx)
print(f'Price history loaded in {time.time()-t0:.1f}s')

print('Compiling JIT...')
warmup()


# --- walk every signal -----------------------------------------------------
print('\nWalking forward paths...')
t0 = time.time()

# Records: (side, score, worst_pnl, best_after_worst, gap_pct_first3)
records = []

def _process(signals, side_sign):
    for s in signals:
        bundle = ph_np.get(s.symbol)
        if not bundle:
            continue
        closes, highs, lows, opens, dates, date_idx = bundle
        sig_i = date_idx.get(s.date)
        if sig_i is None or sig_i < 60 or sig_i + HOLD_DAYS >= len(closes):
            continue
        prior = closes[sig_i-60:sig_i]
        rets = np.diff(prior) / prior[:-1]
        sigma_daily = float(rets.std(ddof=1))
        if sigma_daily <= 0 or sigma_daily > 0.20:
            continue
        premium_pct = PREMIUM_MULT * sigma_daily
        entry_price = float(closes[sig_i])

        n_walked, worst_pnl, best_after = trace_option_pnl_path(
            highs, lows, closes, opens, sig_i,
            entry_price, premium_pct, TOTAL_DTE, side_sign, HOLD_DAYS)

        if n_walked < HOLD_DAYS:
            continue   # ran off price history end

        # Gap filter (analytical, applied post-walk)
        gap_pct = 0.0
        for j in range(sig_i + 1, min(sig_i + 4, len(closes))):
            move = (closes[j] - entry_price) / entry_price
            adverse = -move if side_sign > 0 else move
            if adverse > gap_pct:
                gap_pct = adverse
        records.append((int(side_sign), float(s.overall), worst_pnl,
                        best_after, gap_pct))

_process(calls, +1.0)
_process(puts, -1.0)
print(f'Walked {len(records)} signals in {time.time()-t0:.1f}s')


# --- analyze ---------------------------------------------------------------
arr = np.array([(s, sc, w, b, g) for s, sc, w, b, g in records],
               dtype=[('side', 'i4'), ('score', 'f4'),
                      ('worst', 'f4'), ('best_after', 'f4'),
                      ('gap_pct', 'f4')])

if GAP_ONLY:
    mask = arr['gap_pct'] >= 0.05
    arr = arr[mask]
    print(f'\nGap-only filter: {mask.sum()}/{len(mask)} signals had >=5% adverse move within 3 bars')

# Population stats
print(f'\n=== Worst-pnl distribution (all {len(arr)} signals) ===')
for pct in [10, 25, 50, 75, 90]:
    print(f'  p{pct}: {np.percentile(arr["worst"], pct)*100:+.1f}%')

# Cohorts by worst-pnl bucket
buckets = [
    ('worst <= -100%',   arr['worst'] <= -0.999),
    ('-100% < worst <= -75%',  (arr['worst'] > -0.999) & (arr['worst'] <= -0.75)),
    ('-75% < worst <= -50%',  (arr['worst'] > -0.75) & (arr['worst'] <= -0.50)),
    ('-50% < worst <= -25%',  (arr['worst'] > -0.50) & (arr['worst'] <= -0.25)),
    ('-25% < worst <=   0%',  (arr['worst'] > -0.25) & (arr['worst'] <= 0.0)),
]

print(f'\n=== Distribution of "max recovery after worst bar" by worst-pnl bucket ===')
print(f'{"bucket":28s}  N      mean   p10    p25    p50    p75    p90    P(rec ≥ -30%)  P(rec ≥ 0%)  P(rec ≥ +20%)')
for label, mask in buckets:
    sub = arr[mask]
    if len(sub) == 0:
        continue
    best = sub['best_after']
    pct_neg30 = (best >= -0.30).sum() / len(sub) * 100
    pct_be    = (best >= 0.0).sum()   / len(sub) * 100
    pct_pos20 = (best >= 0.20).sum()  / len(sub) * 100
    print(f'{label:28s}  {len(sub):5d}  '
          f'{best.mean()*100:+5.1f}% {np.percentile(best,10)*100:+5.1f}% '
          f'{np.percentile(best,25)*100:+5.1f}% {np.percentile(best,50)*100:+5.1f}% '
          f'{np.percentile(best,75)*100:+5.1f}% {np.percentile(best,90)*100:+5.1f}% '
          f'  {pct_neg30:5.1f}%        {pct_be:5.1f}%       {pct_pos20:5.1f}%')

# Side breakdown for the dead-hold-eligible cohort
print(f'\n=== Side breakdown for worst <= -50% cohort (dead-hold eligible) ===')
mask_eligible = arr['worst'] <= -0.50
calls_sub = arr[mask_eligible & (arr['side'] == 1)]
puts_sub  = arr[mask_eligible & (arr['side'] == -1)]
print(f'  Calls eligible: {len(calls_sub)} / {(arr["side"]==1).sum()} '
      f'({len(calls_sub)/max(1,(arr["side"]==1).sum())*100:.1f}%)')
if len(calls_sub):
    print(f'    median best-after: {np.median(calls_sub["best_after"])*100:+.1f}%')
    print(f'    P(rec ≥ -30%):  {(calls_sub["best_after"]>=-0.30).sum()/len(calls_sub)*100:.1f}%')
    print(f'    P(rec ≥ 0%):    {(calls_sub["best_after"]>=0.0).sum()/len(calls_sub)*100:.1f}%')

print(f'  Puts eligible:  {len(puts_sub)} / {(arr["side"]==-1).sum()} '
      f'({len(puts_sub)/max(1,(arr["side"]==-1).sum())*100:.1f}%)')
if len(puts_sub):
    print(f'    median best-after: {np.median(puts_sub["best_after"])*100:+.1f}%')
    print(f'    P(rec ≥ -30%):  {(puts_sub["best_after"]>=-0.30).sum()/len(puts_sub)*100:.1f}%')
    print(f'    P(rec ≥ 0%):    {(puts_sub["best_after"]>=0.0).sum()/len(puts_sub)*100:.1f}%')

# Threshold sweep — for each (TRIGGER, POPOUT) candidate, what's the
# expected dead-hold realized pnl vs accepting -100% at SL?
print(f'\n=== Dead-hold candidate threshold matrix (calls + puts pooled) ===')
print(f'TRIGGER    POPOUT     N elig     %elig    P(popout fires)   E[dead_hold_pnl]   E[delta vs -100%]')
for trigger in [-0.40, -0.50, -0.60, -0.70, -0.80]:
    for popout in [-0.40, -0.30, -0.20, -0.10, 0.0]:
        if popout < trigger + 0.05:
            continue   # popout above trigger is degenerate
        elig = arr['worst'] <= trigger
        sub = arr[elig]
        if len(sub) == 0:
            continue
        # P(popout fires) — best-after-worst >= popout
        fires = sub['best_after'] >= popout
        p_fires = fires.sum() / len(sub)
        # E[realized pnl] = popout if fires, expiry-pnl proxy if not
        # We approximate "expiry pnl when dead-hold doesn't fire" as -100%
        # (the no-recovery worst case). This is conservative.
        expected_pnl = p_fires * popout + (1 - p_fires) * (-1.0)
        # Delta vs accepting -100% at SL
        delta = expected_pnl - (-1.0)
        print(f'  {trigger:+.2f}     {popout:+.2f}     {len(sub):5d}    '
              f'{len(sub)/len(arr)*100:5.1f}%   {p_fires*100:5.1f}%            '
              f'{expected_pnl*100:+6.1f}%             {delta*100:+5.1f}pp')

print('\nDone.')
