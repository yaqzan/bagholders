# MTM equity-curve divergence — diagnosis (2026-07-13)

**Status: FIXED 2026-07-14.** The one-line fix described below ("Why not fixed this
session") has been applied to `backtest_cascade.py`'s `_mtm_pnl` (the `bt_calendar_hold`
branch now reads `getattr(pos.outcome, 'dte', DEFAULT_TOTAL_DTE)` per-position, mirroring
the sibling `else` branch, instead of the run-level `bt_nominal_cal_dte` constant).
Re-ran `experiments/portfolio_engine_parity/validate.py` over the live window
(2026-06-01 -> 2026-07-13, v74, Core): **MTM curve check flipped FAIL ($276.32 delta on
2026-06-22) -> OK (max delta $0.0115 on 2026-06-08)**. The only remaining harness FAIL is
the pre-existing, unrelated ADUR premium cents-rounding note (2026-07-02, delta $0.0088,
a hair over the $0.005 tolerance) called out below — unchanged, as expected. No other
parity dimension shifted (closed-trade set, open-position set, final cost equity all
still OK). Three-command sentinel (`tests/test_strategy_config_drift.py`,
`tests/test_mechanism_registry.py`, `experiments/_dte_audit/audit.py`) all green/clean;
the DTE-field scan counts (mc30/mc15/bc30/bc15 = 301/122/202/89) are unchanged by this
edit, confirming it added no new module-level constants.

## Summary

The originally-suspected cause (`pending_requal` marking, portfolio_engine.py) is
**ruled out** by direct code inspection — it never touches any input `_mark_pnl` reads.

The real, reproducible cause: `backtest_cascade.py`'s intermediate MTM curve function
(`_mtm_pnl`) marks **every** open position's unrealized P&L using a single run-level
`NOMINAL_CAL_DTE` constant when `CALENDAR_HOLD` is on, instead of each position's own
per-call `dte`. `portfolio_engine.py`'s equivalent function (`_mark_pnl`, used for the
**live** ledger) already reads the per-call `dte` correctly. The two diverge only on
days when a position whose tenor differs from the run's nominal DTE — in practice, a
call routed through the pre-existing 15-DTE router (`DTE_ROUTER_TARGET_DTE=15`,
`score>=80 & trend<50`, at most 1/day, shipped 2026-05-28) — is open and not yet closed.

**Live-money impact: none.** The live Portfolio page (`GET /api/portfolio/state`) reads
its equity curve from `portfolio_engine`'s own (correct) snapshots, which are what get
persisted. The bug is confined to `backtest_cascade.run_cascade_backtest`'s
`equity_curve_mtm` / `max_dd_mtm` — i.e. the deterministic Backtest page
(`/api/backtest/run`, `src/pages/Backtest.js`) and the offline
`experiments/portfolio_engine_parity/` QA harness that compares the two engines.
Realized (closed-trade) P&L is unaffected on both sides — both ultimately go through
`compute_outcome`/`compute_trade_outcome`, which already use the correct per-position DTE.

## Method

Reused the existing harness rather than building a new one:
- `experiments/portfolio_engine_parity/validate.py` — fresh, read-only, stateless
  replay of `portfolio_engine._advance` (persistence monkey-patched to a capture dict)
  vs a real `backtest_cascade.run_cascade_backtest` call over the same window. Nothing
  written; the live ledger is untouched.
- `experiments/portfolio_engine_parity/dump_marks.py` — per-position mark attribution
  for one target date (pre-existing tool, written for the *previous* engine-vs-backtest
  divergence investigation — see "Tooling note" below, its own bars-based comparison
  baseline had gone stale and needed a fix to be trustworthy again).

## Evidence

### 1. `pending_requal` ruled out

`portfolio_engine.py:828-849` — `pending_requal`/`sweep_pending`/`rescued` only decide
*whether and when a holding is scheduled to exit* on a version/strategy transition. They
never touch `premium_usd`, `sigma_daily`, `entry_underlying`, `delta`, or `dte` — the
only fields `_mark_pnl` reads. `portfolio_engine.py:851-852`:

```python
def _equity_mtm(d):
    return cash + sum(p['premium_usd'] * (1.0 + _mark_pnl(p, d, ph)) for p in ledger)
```

`_mark_pnl` is called for **every** position in `ledger` regardless of `sweep_pending`
state. The 2026-06-15 guess ("likely the graceful-sweep `pending_requal` mark") does
not hold up — there is no code path from that flag to the marking math.

### 2. The real defect — per-call DTE not honored in `_mtm_pnl`'s calendar branch

`portfolio_engine.py:620-624` (live engine, correct — reads the position's own `dte`):

```python
if bc.CALENDAR_HOLD:
    held, total_dte = max(0, (d - p['entry_date']).days), int(p.get('dte') or bc.NOMINAL_CAL_DTE)
else:
    held, total_dte = _bars_between(rows, p['entry_date'], d), p['dte']
```

`backtest_cascade.py:2103-2104` (run-level constants, resolved once per backtest call):

```python
bt_calendar_hold = bool(cfg.get('calendar_hold', CALENDAR_HOLD))
bt_nominal_cal_dte = int(cfg.get('nominal_cal_dte', NOMINAL_CAL_DTE))
```

`backtest_cascade.py:2179-2229` (`_mtm_pnl`, the intermediate/open-position mark —
**defect** in the calendar-hold branch):

```python
if bt_calendar_hold:
    held, total_dte = (today - pos.outcome.signal_date).days, bt_nominal_cal_dte   # <- constant, ignores pos.outcome.dte
else:
    held = _td_idx.get(today, 0) - _td_idx.get(pos.outcome.signal_date, 0)
    total_dte = getattr(pos.outcome, 'dte', DEFAULT_TOTAL_DTE)                     # <- correctly per-position
```

The non-calendar (`else`) branch already reads the position's own DTE via
`getattr(pos.outcome, 'dte', ...)`. The calendar-hold branch — the one actually active
in production (`CALENDAR_HOLD=True`) — was never given the same treatment, so it
silently substitutes the run's nominal DTE for any position whose real tenor differs.

The per-call DTE feature itself is real and deliberate — `backtest_cascade.py:1178-1179`
assigns it at entry:

```python
dte = bc.DTE_ROUTER_TARGET_DTE if routed else nominal_cal_dte
```

(`DTE_ROUTER_TARGET_DTE=15` by default, `backtest_cascade.py:145` — the pre-existing
"route at most 1 call/day to 15-DTE when score>=80 and trend<50" mechanism, unrelated
to the Apex risk-budget elbow.) `portfolio_engine.py:1178-1179`/`1475` and `:587`
thread `routed_15dte`/`dte` through the live engine's own entry and exit-barrier
resolution correctly; only the backtest's *intermediate mark* forgot it.

### 3. Quantitative reproduction (exact)

`validate.py` on the live window (2026-06-01 -> 2026-07-13, v74, Core profile):

```
MTM equity curve: 29 common dates
  FAIL MTM curve max delta $276.3195 on 2026-06-22
```

`dump_marks.py 2026-06-22` (after fixing its stale comparison baseline to mirror
production's actual current behavior — see "Tooling note" below) isolates it exactly:

```
SYM    ENTRY        dte h_cal h_bar  eng_mark   bt_mark     prem$  usd_delta
--------------------------------------------------------------------------------------
ADSK   2026-06-18    15     4     1   -0.5954   -0.5208   3703.85    -276.31
ONDS   2026-06-01    30    21    14   -1.0000   -1.0000   1438.75       0.00
NET    2026-06-01    30    21    14   -1.0000   -1.0000    323.72       0.00
AMPX   2026-06-03    30    19    12   -1.0000   -1.0000    112.65       0.00
CLS    2026-06-03    30    19    12   -1.0000   -1.0000    170.94       0.00
ANET   2026-06-03    30    19    12   -0.3864   -0.3864    119.30       0.00
SATS   2026-06-11    30    11     6   -1.0000   -1.0000   2426.34       0.00
LPTH   2026-06-11    30    11     6   -0.4113   -0.4113    485.27       0.00
TXN    2026-06-18    30     4     1    0.1562    0.1562   1388.94       0.00
ADI    2026-06-18    30     4     1    0.1893    0.1893   1481.54       0.00
FTAI/KGS/SWKS/TE  2026-06-22 (entered same day, held=0)                  0.00 (x4)
--------------------------------------------------------------------------------------
TOTAL position $delta (engine - backtest):                         -276.31

Backtest equity_curve_mtm[2026-06-22] = $38,945.94   (real)
Backtest replica equity (recomputed)  = $38,945.93   (self-check delta $0.0105 -- clean)
Observed engine-vs-backtest gap       = $-276.32     (matches TOTAL position delta above)
```

Every position whose `dte` equals the run's nominal 30 matches to the cent (`usd_delta
0.00`); the one position whose `dte` (15) differs — ADSK, entered 2026-06-18 via the
15-DTE router — accounts for the entire -$276.31 of the -$276.32 total gap. The
diagnostic tool's own self-check (backtest replica should reproduce the real
`equity_curve_mtm` value) now holds to a dime, confirming this is not an artifact of
the tool's comparison logic.

### 4. Reconciling with the original "$295 from 2026-06-08" note

The 2026-06-15 SPREAD_TILT ship note (known-issues.md) predates commit `3a585c2ed`
(2026-06-22, "Apex profile = 15-DTE risk-budget elbow + per-call DTE/SL engine
wiring"). That commit's own `_mtm_pnl` docstring records the *prior* state: before it,
the intermediate MTM curve used **trading-bar** held/DTE unconditionally (even under
`CALENDAR_HOLD`) while realized exits already used calendar theta — a broad mismatch
affecting every open position, every weekend, not just router-routed ones. `3a585c2ed`
fixed that broad case for `backtest_cascade.py`'s calendar-hold branch but, in doing so,
hardcoded the run-level `bt_nominal_cal_dte` rather than mirroring the per-position read
its own sibling `else` branch already had — trading one bug for a narrower one. The
original $295 (2026-06-08, pre-fix) and today's $276 (2026-06-22, post-fix) are most
likely two **different** bugs in the same function, not one persisting bug — the first
was fixed as a side effect of unrelated work; this diagnosis is the first time the
residual has been isolated and named.

## Why not fixed this session

The one-line fix is clear (mirror the `else` branch:
`total_dte = getattr(pos.outcome, 'dte', DEFAULT_TOTAL_DTE)` in the calendar-hold
branch too) and is display/measurement-only — it does not touch cost-basis accounting,
realized P&L, or any ship-gated parameter. It is **not applied here** because
`backtest_cascade.py` is hard-fenced hands-off for this session (frozen while queue
tasks 607/608/609/610/612 run MC/backtest sweeps against the pinned engine — editing it
mid-flight would change what those in-flight comparisons are measuring). Recommended
follow-up for whoever next has write access to `backtest_cascade.py`: apply the one-line
fix, then re-run `experiments/portfolio_engine_parity/validate.py` over a window known
to have an open router-routed position — the MTM curve check should flip from FAIL to
PASS with no other changes to closed-trade or open-position parity (both already pass
today except a $0.0088 sub-cent premium-rounding note on one closed trade, ADUR
2026-07-02, unrelated to this defect and below the harness's own $0.005 tolerance by a
hair — separate, cosmetic, not investigated further here).

## Tooling note (fixed this session, non-production)

`dump_marks.py`'s own comparison baseline (`_bt_mark`) had gone stale — it still
hardcoded the pre-`3a585c2ed` trading-bar marking unconditionally, so its self-check
("backtest replica equity should == real equity_curve_mtm") was itself failing by
~$348 on the same date, which would have produced a misleading per-position table (the
`bt_mark` column no longer represents what `backtest_cascade.py` actually computes).
Updated it to branch on `bc.CALENDAR_HOLD` the same way production does, so its
self-check now holds and the tool is trustworthy for future re-diagnosis. See
`experiments/portfolio_engine_parity/dump_marks.py`.

## Disposition

Fixed 2026-07-14 (see Status header). `known-issues.md` CURRENT SHIP STATE / OPEN WORK
should be updated to drop this from open work (was pointing here instead of the stale
`pending_requal` guess; now resolved). Parity harness (`validate.py`) now reports PASS
on the MTM curve check specifically; the harness's overall PARITY verdict still prints
FAIL solely because of the separate, pre-existing ADUR cents-rounding note (unrelated to
this defect, not investigated further here, and not expected to be — it is below-tolerance
by a hair and cosmetic).
