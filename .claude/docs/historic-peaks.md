## Historic Peaks (historic_peaks.py)

Cache table of peak signal events — scores >=75 (CALL) or <=25 (PUT) — from the last 365 days. Multiple rows per stock: consecutive qualifying days within 7 days cluster into one event; the most extreme score in each cluster is kept.

### Event clustering

1. Load qualifying scores (>=75 or <=25) in the 365-day window for the active algorithm version.
2. Per stock, walk chronologically: start a new event when the gap from the previous qualifying day exceeds `MIN_EVENT_GAP` (7 days).
3. Pick the most extreme score (furthest from 50) within each cluster.
4. Store one `HistoricPeak` row per event.

### Key constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `WINDOW_DAYS` | 365 | Look-back window |
| `HIGH_THRESHOLD` | 75 | Min score for HIGH (call) peak |
| `LOW_THRESHOLD` | 25 | Max score for LOW (put) peak |
| `MIN_EVENT_GAP` | 7 | Days gap to start a new event for the same stock |

### DB schema

`historic_peaks`: auto-increment `id`, unique `(symbol, peak_date)`. Fully rebuilt (truncate + insert) each run.

Forward return columns per period (1d/7d/15d/30d/60d/90d, calendar days from peak):
- `fwd_peak_{p}` — direction-adjusted intraday best within the window (magnitude, always positive)
- `fwd_peak_days_{p}` — calendar days from peak_date to the bar where that best occurred
- `fwd_peak_delta_{p}` — incremental gain vs previous period (e.g. `fwd_peak_delta_30d = fwd_peak_30d - fwd_peak_15d`); ~zero = no new high in the extended window. No delta for 1d.

Barrier-touch win columns per period: `win_{p}` — 1 if vol-adjusted target hit before stop within W calendar days, 0 if stop/expire, NULL if insufficient forward history or vol data. Same formula as `assess_scores._swing_walk`.

### Stale cache risk

Denormalized cache; `peak_score` is snapshotted from `Score.overall` at build time. If scores change without rebuilding (recalc, version switch), cached values go stale. Rebuild after any score modification.

### CLI

- `trader historic-update [window]` — rebuild historic peaks cache (default 365 days)
- `trader update` — includes historic peaks rebuild automatically

---

## Cross-Window Bridge Analysis (experiments/cross_window_bridge.py)

Three cross-window metrics extending standard barrier-touch assessment (all validated 5y, v21 `aba4f5d`, 2026-04-23):

1. **Original-position continuation rate** — P(win_next | win_prior): does the ORIGINAL position also hit the next window's target? Measures trend continuation, not re-entry edge.
2. **Fresh-entry roll-up WR** — close the ITM option at the Xd win, open a fresh ATM from the day-X close with K x sigma x sqrt(W_new/30) barriers. True re-entry edge, via `bridge_walk()` on the WIN cohort.
3. **Re-entry bridge WR** — for expired (not stopped) signals, treat day-X close as a fresh ATM entry, walk forward with a new barrier. Is there edge in rolling to a fresh 30 DTE?

Run: `python experiments/cross_window_bridge.py [lookback_days]` (all 4 transition windows + roll-up section).

### Validated numbers (5y, v21 `aba4f5d`, 2026-04-23)

**Continuation rate**, calls 70+/puts <30:

| Transition | Calls 70+ | Puts <30 | N (calls) |
|---|---|---|---|
| 7d->15d | 90% | 92% | ~10,000 |
| 15d->30d | 91% | 90% | ~10,200 |
| 30d->60d | 88% | 88% | ~10,300 |
| 60d->90d | 92% | 92% | ~10,100 |

**Fresh-entry roll-up WR** (drives the `rollup` pill in `Historic.js`):

| Transition | Bridge window | Calls 70+ | Puts <30 | Clears call BE (56.3%)? |
|---|---|---|---|---|
| 7d win -> fresh entry | 8 cal days | 63% (N=10,036) | 63% (N=32,720) | +7pp |
| 15d win -> fresh entry | 15 cal days | 63% (N=10,213) | 65% (N=33,525) | +7pp |
| 30d win -> fresh entry | 30 cal days | 64% (N=10,061) | 64% (N=32,906) | +8pp |
| 60d win -> fresh entry | 30 cal days | 64% (N=9,823) | 65% (N=32,238) | +8pp |

Fresh-entry WR (63-64% calls) is below the 90%+ continuation rate but clears call BE (56.3%) by 7-8pp; puts (63-65%) clear put BE (43.5%) by 19-21pp. `rollup` tags fire for both `isCall`/`isPut`.

**Re-entry bridge WR**, fresh ATM from expire-bar close, calls 70+ only (puts too small/below BE — 7d puts 97% stopped; 15d 98% stopped; 30d N=290 at 51.4%; 60d N=226 at 41.6%, below put BE):

| Window | Expire N | Bridge WR |
|---|---|---|
| 7d expire -> 8-day bridge | 2,391 | 58% (Brg15) / 63% (Brg30) |
| 15d expire -> 15-day bridge | 2,182 | 61% |
| 30d expire -> 30-day bridge | 1,994 | 60% |
| 60d expire -> 30-day bridge | 1,980 | 59% |

### Win field encoding (`win_{p}` in historic_peaks)

`1` = target hit before stop within W days. `0` = stop or expire within W days. `NULL` = insufficient forward history/vol data.

`win_7d = 0 && win_15d = null` = 7d window failed (stopped/expired) and 15d hasn't elapsed — trade still alive. `days_ago` bounds in `Historic.js` guard against stale price data causing false filter matches.

### Historic.js pill logic

| Active period | Pill source | Condition |
|---|---|---|
| `'15d'` | rollup7 | `win_7d === 1 && win_15d === null` |
| `'15d'` | reentry7d | `win_7d === 0 && win_15d === null && days_ago in [7,15)` (calls only) |
| `'30d'` | rollup15 | `win_15d === 1 && win_30d === null` |
| `'30d'` | reentry15d | `win_15d === 0 && win_30d === null && days_ago in [15,30)` |
| `'60d'` | rollup30 | `win_30d === 1 && win_60d === null` |
| `'60d'` | reentry30d | `win_30d === 0 && win_60d === null && days_ago in [30,60)` |
| `'90d'` | rollup60 | `win_60d === 1 && win_90d === null` |
| `'90d'` | reentry60d | `win_60d === 0 && win_90d === null && days_ago in [60,90)` |

Pill renders left of the delta pct in the highlighted active-window cell. `isReentry` drives the "Flagged" filter; bounded `days_ago` ensures it fires only when a pill is visible.

---

## Put Rollup Variants — NULL RESULT (2026-04-23, `experiments/put_rollup_variants.py`)

**Question:** cross-window bridge found 63% fresh-entry WR for puts after a 7d confirmed win. Do (a) DELAYED (skip t=0 entry, enter only after 7d confirmation, 15 DTE) or (b) ROLLUP (baseline + 15 DTE confirmation layer after 7d wins) improve portfolio returns?

**Setup:** $50k start, N=200, 3 collision modes, 6 windows (2021-2025 + 5y), v21 `aba4f5d`. 15 DTE puts: `PREMIUM_MULT_15=1.29`, `HOLD_DAYS_15=6` bars, TP=+30%/SL=-20% -> `PUT_TP_SIGMA_15=0.774sigma`, `PUT_SL_SIGMA_15=0.516sigma`. Confirmation gate: `WIN_7D_BARS=5` bars, `WIN_7D_SIGMA=sqrt(7/30)=0.483sigma`. Confirmation rate 77-84% of put signals. Hard sell at bar 6: `NET_HARD_15~=-0.400`.

**Root cause — bridge WR != option TP rate.** Bridge WR used the assessment barrier methodology (K=1.0sigma x sqrt(8/30)=0.516sigma target, M=2.0sigma x sqrt(8/30)=1.033sigma stop). MC uses TP=+30% on premium -> 0.774sigma underlying target for 15 DTE — a 50% larger move than the bridge target while the stop equals it. Bridge WR is incompatible with option TP definition.

**15 DTE raw TP% by year (all below 43.5% BE):** 2021 35.8% (-7.7pp), 2022 38.5% (-5.0pp), 2023 36.6% (-6.9pp), 2024 35.5% (-8.0pp), 2025 37.5% (-6.0pp).

**DELAYED — KILL.** Floods slot pool with confirmed puts (2,412-2,449 trades in 5y vs 1,881 baseline), displacing calls with below-BE puts. Conservative DD floor breached 4/6 windows (5y Cons=96.6%, 5y Real=88.1%, 2024 Cons=81.9%, 2023 Cons=92.3%); P(collapse)=5% in 2023 Conservative. Returns -5k to -64k pp vs baseline every year.

**ROLLUP — Neutral (do not ship).** No DD floor violations; 5y Realistic DD improves 2.9pp (73.2% vs 76.1%). Per-year mixed (+28% 2024, +57% 2023, -21% 2022, -5% 2025); 5y compound -4.2% vs baseline. ~45 extra put trades/year at below-BE TP with minor call displacement — cost ~= benefit.

There is no 15 DTE parameter set that profitably exploits the bridge edge at the option P&L level (even TP=+20% -> 0.516sigma raises BE to ~69%, above the 63% bridge WR).

**Do NOT:** ship DELAYED or ROLLUP as put strategies; interpret the 63% bridge fresh-entry WR as an option-level TP rate for 15 DTE puts; tune the 15 DTE layer further — the bridge-vs-option-TP mismatch is structural.

---

## DTE Recommendation Algorithm (dte_recommendation.py)

**Thesis classification (priority order):**
1. REVERSAL (10-21 DTE): CLIMAX signal + magnitude >=0.6
2. BOUNCE (7-15 DTE): price <=-15% from EMA50 + ABSORPTION/REJECTION
3. TREND (35-45 DTE): score >=95 + near EMA50 + velocity >=0
4. MOMENTUM (21-35 DTE): default for mid-range signals

**DTE target within range:** score >=95 -> 42d; >=90 -> 32d; >=85 -> 25d; >=80 -> 21d; >=75 -> 14d; <75 -> 10d

**Confidence:** HIGH requires score >=95 + volume signal + magnitude >=0.5 + |pct_from_ema50| <=10%

**entry_filter integration:** `entry_filter.evaluate()` runs first; if tradeable, its DTE cell overrides the thesis ladder. Gates marked NEEDS REVALIDATION — see Known Issues.
