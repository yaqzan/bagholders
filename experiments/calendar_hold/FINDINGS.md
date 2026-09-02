# Calendar-day hold vs trading-bar hold — A/B findings (2026-06-08)

**Question:** the assessment WR7/WR15 barriers are CALENDAR days, but the portfolio
engine holds 15 *trading bars* (~21 cal days) and decays theta over 30 *trading bars*
(~42 cal days). Real Wealthsimple options are calendar-day instruments. Is a
calendar-indexed "30dte" backtest better than the trading-bar one?

**Verdict: NO — calendar is decisively WORSE on both return and drawdown.** It is a
(large) realism *fee*, not an alpha lever. Collapse-safety is preserved.

## Setup

Env-gated `CALENDAR_HOLD` branch in `monte_carlo.py` (default OFF = byte-identical
trading-bar engine). Call path only (puts off under Apex). Live v70 Apex config,
N=200 × 7 windows. Three variants:

- **A** baseline — trading-bar hold (HOLD_DAYS=15 bars), theta over 30 bars.
- **C21** calendar, hold to signal+21 cal days (~same 15-bar exposure), theta over
  TRUE calendar-days-elapsed / 30 cal DTE → **isolates theta-honesty**.
- **C15** calendar, hold to signal+15 cal days (literal halfway of a 30 DTE) + honest theta.

The smoke (`smoke.py`) proves the mechanism: at the "day-15" hard-sell the baseline
already records `cal_held=21` but charges theta as if 15/30 of life elapsed
(−29.3%); honest 30-cal-DTE theta at 21 days elapsed is −45.2%. A −16pp/slow-trade
under-statement.

## Results (N=200; MedRet = median compound; collapse = P(account→~0))

| window | A MedRet | C21 MedRet | C15 MedRet | A WorstDD | C21 ΔDD | C15 ΔDD | collapse (all) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 | +158% | +56% | **−19%** | 57.0% | +1.1 | +2.8 | 0% |
| 2024 | +4,363% | +1,129% | +687% | 43.0% | +3.5 | +4.0 | 0% |
| 2025 | +621% | +61% | +54% | 60.7% | +4.4 | +1.6 | 0% |
| dip | +281% | +109% | +99% | 41.5% | +6.1 | +3.7 | 0% |
| 22-now | +223,486% | +2,005% | +678% | 64.0% | +0.9 | +1.8 | 0% |
| 5y | +557,692% | +1,704% | +594% | 61.1% | +3.8 | +4.9 | 0% |
| 2020_crash | −40.8% | −44.6% | −46.4% | 69.1% | +0.9 | +1.8 | 0% |

## Conclusions

1. **Calendar is worse on BOTH axes** — lower return AND slightly deeper DD
   (+1 to +6pp). Honest theta makes winners less positive and losers more negative
   (cal_held ≥ bars_held always), and that EV compression compounds.

2. **~99% of the modeled compound was theta-model optimism.** 5y MedRet
   +557,692% → +1,704% (C21, theta-only) ≈ a **327×** haircut; 22-now ~111×. The
   trading-bar engine under-charges theta because it indexes a 30-CALENDAR-DTE
   option's life in 30 TRADING bars (~42 cal days).

3. **The fee is concentrated in the HOLD-to-day-15 cohort** — exactly the documented
   core of the Apex edge (wide SL, sell day 15). A HOLD strategy keeps trades alive
   in the high-theta zone where the unit bug bites hardest → the HOLD edge is partly
   a theta-accounting artifact. **Follow-up: re-test HOLD-vs-CUT under honest theta**
   (CUT exits faster = less theta exposure → honest theta narrows, maybe flips, the
   HOLD≫CUT conclusion).

4. **Collapse-safety is robust to the correction** — 0% on every cell incl 2020_crash.
   The dead-hold remains collapse-preventing under honest theta. So Stage-3 decisions
   locked on DD/collapse/relative-ranking are NOT invalidated; only the absolute
   compound headline is optimistic.

## Real-world takeaways

- Trust the trading-bar engine's **DD / collapse / relative ranking**; treat its
  **absolute compound** as heavily theta-optimistic (÷100–300 for a realistic read).
- **Buy ~30–35 calendar DTE, never 15** — so the bar-15 (~21 cal day) exit lands with
  ~9–15 days of time value left, off the final-week theta cliff. (Mechanistic reason
  behind the documented "30 DTE ≫ 15 DTE".)
- The honest-theta clock is worth considering as a permanent Stage-3 realism fix
  (no version bump) — same class as the v69 look-ahead removal / asymmetric-cost
  canon. It re-baselines every MC compound number ~100–300× lower but leaves the
  strategy and its DD/collapse profile intact.

## Reproduce

```
python experiments/calendar_hold/smoke.py            # pure-function proof (no MySQL/MC)
# A/B (queue it):
trader queue submit --priority normal --db light --cpu 6 --restartable \
  --dedup calendar-hold-ab -- python experiments/calendar_hold/run_ab.py
```

Knobs (env, default OFF): `CALENDAR_HOLD=1 HOLD_CAL_DAYS=21 NOMINAL_CAL_DTE=30`.
Raw: `experiments/calendar_hold/results/ab_combined.json`.
