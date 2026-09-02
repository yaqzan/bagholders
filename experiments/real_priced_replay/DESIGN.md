# Real-Priced Ledger Replay — model P&L vs real contract prints (DESIGN, bars pinned pre-run)

**Date:** 2026-07-18 (Saturday autonomous run) · **Owner:** FABLE (architect) · **Class:**
engine-fidelity audit probe (HOSTILE_REVIEW A-1/A-3/A-9/A-11 with MARKET data), NOT alpha work,
NOT a ship gate. Proportionate ceremony: metrics + thresholds pinned in this file before any
run; no December-grade prereg needed because nothing ships on this read — its outputs license
only the commissioning of already-listed pessimism re-runs.

## 0. Question and provenance

Over the own-panel era (**2025-02-10 → run date, ~17.5 months**), what did the Core book
ACTUALLY earn and draw down if every position is priced by **real contract prints** instead of
the RV-premium + constant-delta model — same signals, same sizing, same exit rules?

**Data:** our own `options`/`option_prices` yfinance panel (~90M rows) — the only real-premium
truth-set we own. Explicitly NOT the Polygon panel: it is BS-derived IV keyed to signal dates,
and `iv_engine_pertrade/VERDICT.md` proved panel-IV and real contract premiums systematically
disagree on calm names (the M1 park). Using it here would rebuild the exact confound the park
identified.

**Holdout note:** this probe evaluates PRICING fidelity conditional on a fixed signal set, not
signal quality; scoring-holdout discipline is not implicated. Post-cutoff signals' realized
performance is already operationally visible (the live forward ledger); nothing new leaks.

## 1. Construction

- **Signals:** v74 (`version_id=74`) call signals, `overall ≥ 75`, date ∈ [2025-02-10,
  run_date − 30cd]. Puts excluded (portfolio-OFF).
- **Contract selection: REUSED VERBATIM from `experiments/iv_engine_pertrade/build_ledger.py`**
  (nearest-ATM CALL, ~30-DTE band, its liquidity classification incl. the liquid-primary
  definition). No new selection logic — the selection layer was already validated there; the
  window extends to the full era (its run stopped at the in-sample cutoff).
- **Two arms, SAME grain (the honest comparison):**
  - **MODEL-CG (close-grain model arm):** the production pricing model
    (`option_pricing.option_pnl_pct`, RV premium `1.82×σ`) with exits triggered on DAILY CLOSES
    only.
  - **REAL-CG:** the actual contract's daily price series; exits triggered on the same daily
    closes of the REAL price.
  The production INTRADAY-grain model number is reported alongside as reference, but the
  decision comparison is MODEL-CG vs REAL-CG — identical trigger grain isolates PRICING
  fidelity from grain effects (our panel has no intraday option prints; pretending otherwise
  would be fake precision).
- **Exit rules (both arms, on the arm's own price):** TP at ≥ +30% of entry premium; SL at
  ≤ −70% → dead-hold analog: hold until the arm's price recovers to ≥ −15% (popout) or expiry
  (this is the DIRECT market measurement of the A-1 popout assumption); day-27 hard-sell;
  expiry at terminal value. Costs: the shipped asymmetric canon applied identically to both
  arms (entry/TP free; forced exits −1.5%).
- **Sizing:** Core cascade (0.20/0.15/0.08/0.03), MaxPos 14, overflow per current Core config;
  identical capital rules both arms; $50k start.

## 2. Pinned metrics (computed for liquid-primary as headline; all-tier appendix)

1. Per-trade P&L gap distribution (REAL-CG − MODEL-CG), overall and by {liquidity tier, RV
   tercile, calendar year, VIX band at entry}.
2. Exit-class agreement matrix (model class vs real class per trade).
3. **Popout reality read (A-1):** of trades that breach −70% in each arm, the fraction
   recovering to ≥ −15% before expiry: model popout rate vs REAL popout rate, plus
   days-to-popout distributions.
4. **Entry-premium reality read (A-3):** distribution of real entry premium ÷ model premium
   (1.82×σ_daily×S), overall and by VIX band + RV tercile.
5. Portfolio: 17-month compound + MaxDD, both arms + the intraday-model reference.

## 3. Pinned flag thresholds (pre-run; they license ONLY commissioning already-listed re-runs)

Flag **FILL-MODEL-OPTIMISTIC** if ANY of: REAL-CG compound < MODEL-CG compound by > 25%
relative; REAL-CG MaxDD > MODEL-CG MaxDD by > 5pp; REAL popout success rate < model popout
rate by > 15pp absolute. Flag **PREMIUM-MODEL-CHEAP** if median(real/model entry premium) >
1.15 overall or > 1.40 in any VIX band with N ≥ 100. Either flag ⇒ escalate the corresponding
Tier-2/3 audit probes (P6/P8/P10) to certificate grade on the new box — nothing else. No flag ⇒
the canon gains its first market-data corroboration on this era; the live fill log (P2.B)
remains the final arbiter.

## 4. Owned limitations

lastPrice staleness biases both triggers and marks (mitigation: liquid-primary headline;
staleness is exactly why per-trade M1 used the same stratification); 17 months, no crash
window — this probe CANNOT test collapse=0 or crash-window claims, and must not be cited
against them; close-grain misses intraday TP touches in both arms symmetrically (disclosed;
the intraday-model reference column quantifies the grain effect on the model side); lastPrice
is a print not a mid (some spread realism is embedded in the real arm — direction: makes the
real arm HARSHER on forced exits, consistent with the probe's conservative purpose);
single-path, no MC dispersion (this is an accounting replay, not a simulation).

## 5. N floors and execution

Floor: ≥ 800 liquid-primary matched trades, else report-only (no flags). Expected N ~2.5-4k
total from the ledger precedent (2,551 matched in-sample through ~2026-06). Build: extend/reuse
`build_ledger.py` extraction (queued `--db heavy --cpu 2`, ~30-60 min) → replay walk is
seconds. Runner: `experiments/real_priced_replay/replay.py`, results table appended to this
file's RESULTS section by the runner; every invocation appends to `results/attempt_log.md`
(same rail as the gamma prereg — cheap and prevents silent re-runs).

## RESULTS

### OPEN QUESTIONS (build-time disclosures, pre-run — implementation-builder agent, 2026-07-18)

Sections 1/5 leave a few implementation details underspecified. Each is resolved below as a
documented, reviewable choice (not a blocker); flagging per the build brief's instruction to
surface genuine ambiguity here rather than silently deciding it.

1. **Forward underlying series.** Section 1 lists "entry S" (singular) as a persisted field,
   but the twin close-grain replay cannot compute MODEL-CG's day-by-day P&L — or the
   intraday-grain reference column — without the underlying's own FORWARD daily OHLC. The
   ledger additionally persists `model_dates`/`model_{open,high,low,close}` arrays over the
   same `entry..min(expiry, entry+30cd)` window as the real-contract array, sourced from
   `price_history`. This is what makes "replay walk is seconds" (section 5) literally true:
   `replay.py` performs zero MySQL access — every input is the persisted parquet or an
   imported constant.
2. **Sizing model.** Section 1 pins "Core cascade (0.20/0.15/0.08/0.03), MaxPos 14, overflow
   per current Core config; identical capital rules both arms" but does not mention the full
   production alloc-scaling stack (RXDD/MWDD/TVDD/BDIV/SVR/SPREAD_TILT/regime-slope/
   DD-soft-band/opportunity-saturation) that multiplies `TIER_ALLOC` in the live
   `monte_carlo.py` engine. Implemented: `alloc_dollars = TIER_ALLOC[tier] x portfolio_value`,
   where `portfolio_value = cash + sum(open-position cost basis)` — production's OWN
   convention (`monte_carlo.py` ~line 2993, NOT a mark-to-market curve) — capped in aggregate
   at `GROSS_PREMIUM_CAP`/`CALL_PREMIUM_CAP`=0.50 of `portfolio_value` (partial-filled to
   remaining headroom rather than skipped when a tier's full target doesn't fit), admitted in
   tier-priority order (ultra>top>mid>low) each day, MaxPos=14 hard cap, one open position per
   symbol (re-entry blocked, mirroring the shipped same-symbol rule). None of the 8 listed
   dampeners are applied — a deliberate isolation of PRICING fidelity from portfolio-mechanism
   fidelity, extending section 1's own "identical trigger grain isolates PRICING fidelity from
   grain effects" logic to the sizing layer too. Both arms get this identical simplified
   treatment, so the arm-vs-arm comparison this DESIGN cares about is unaffected; only the
   absolute compound/DD LEVELS differ from a live Core run.
3. **Popout slippage.** Section 1 states costs as a binary ("entry/TP free; forced exits
   -1.5%"). Production's `resolve()` actually pays SL-popout recoveries (`dh_pop`) via
   `DH_POP_SLIP`, which DEFAULTS TO 0.0 (a resting-limit fill, not a forced/taker exit) —
   distinct from dead-hold-exhausted-at-deadline (`SL_HELD`) and day-27/expiry (`HARD`/
   `EXPIRY`), which pay `SLIP_HARD`=-0.015 as forced/taker exits. Implemented popout as FREE
   (0), matching the current shipped default and the "entry/TP free" spirit (a popout is a
   resting-limit fill, mechanically like a TP). Flagging in case the simpler literal binary was
   intended (every SL-family exit, incl. popout, pays -1.5%) — also a defensible reading of the
   text alone; applied identically to both arms either way, so it would only widen (never flip
   the sign of) any REAL-vs-MODEL popout-rate gap.
4. **VIX bands.** Section 2 asks for stratification "by VIX band" without pinning cut points.
   Used <20 / 20-28 / >=28, matching the center/panic thresholds already shipped in RXDD
   (VIX_C=22.7) and MWDD (VIX_PANIC=28.0) — not arbitrary, but not DESIGN-pinned either. VIX
   coverage was checked (`.cache/iv_premium_model/vix_series.parquet`, 1995-01-03 to
   2026-07-10 as of this build) and fully spans the signal window, so the SKIPPED-NO-DATA path
   is implemented but not expected to fire on the real run.
5. **Intraday-grain reference column.** Section 1 describes it only as "reported alongside as
   reference" via "the standard intraday trigger path"; section 4 disclaims MC dispersion
   entirely. Production's standard intraday path fires a per-iteration RNG for the actual fill
   (Uniform-in-bar sampling) — incompatible with a single-path replay. Implemented
   deterministically: entry-fixed sigma barriers (`STRATEGY_30DTE.TP_SIGMA_BASE`/
   `SL_SIGMA_BASE`, matching `compute_trade_outcome`'s own static-threshold convention, NOT
   `option_pricing.adjusted_underlying_threshold`'s per-bar theta-adjusted version, which a
   different code path uses) trigger off intraday high/low; dead-hold popout checked off
   intraday high with an open-price floor (mirrors `_compute_dead_hold_call` exactly);
   same-bar TP+SL collisions resolve to a realized SL_HELD at the SL barrier (conservative
   tie-break, consistent with this probe's stated conservative-bias posture) rather than
   production's 50/50 coin flip. Reference/context column only — not part of the MODEL-CG vs
   REAL-CG decision comparison section 1 pins as authoritative.
6. **Signal-set count sanity check.** `overall>=75` in `[2025-02-10, run_date-30cd]` for v74
   resolved to 1,661 candidate signals when checked 2026-07-18 (small indexed query, not the
   heavy per-signal build). Plausible against section 5's "~2.5-4k total" precedent estimate,
   but that estimate was drawn from an `overall>=70` population over a similar-but-not-identical
   window, so the two counts are not directly comparable — the queued job's own coverage
   summary (N matched, liquid-primary N) is the actual arbiter against the >=800 floor.

Both scripts were unit/smoke-tested end-to-end against synthetic (non-DB) data before the
ledger build was queued: `walk_close_grain`'s 8 state-machine paths (TP / SL-then-popout /
SL-held-to-deadline / HARD / EXPIRY-before-deadline / no-data / TP-preempts-later-SL /
popout-before-either-stop), the TP/popout-free vs SL_HELD/HARD/EXPIRY-pay-1.5% slippage
mapping, `run_portfolio`'s same-symbol re-entry block and MaxPos/gross-cap admission limits,
and the full `compute_population_metrics` -> `evaluate_flags` -> `build_report_text` pipeline
including the VIX SKIPPED-NO-DATA path, all passed.

### Run 2026-07-18 10:30:45 UTC

```
Run timestamp: 2026-07-18 10:30:45 UTC
Ledger: C:\Development\Trader\.cache\real_priced_replay\replay_ledger.parquet  (built_at=2026-07-18 06:28:56, matched_n=785, liquid_primary_n=195)
VIX coverage over signal window: OK
N-floor (>= 800 liquid-primary): FAIL -- report-only, no flags (N=195)

==============================================================================
POPULATION: liquid-primary (HEADLINE)  (N=195)
==============================================================================

-- 1. Per-trade gap distribution (REAL-CG - MODEL-CG net pnl) --
  overall: n=195 mean=+16.35% median=+6.60% p25=+0.06% p75=+29.33%
  by by_liquidity:
    liquid_ge5           n=195 mean=+16.35% median=+6.60% p25=+0.06% p75=+29.33%
  by by_rv_tercile:
    T1_low               n=65 mean=+18.36% median=+7.27% p25=-6.90% p75=+32.08%
    T2_mid               n=65 mean=+12.39% median=+2.65% p25=-4.87% p75=+22.75%
    T3_high              n=65 mean=+18.29% median=+11.15% p25=+2.23% p75=+32.37%
  by by_year:
    2025                 n=93 mean=+21.92% median=+7.51% p25=-1.12% p75=+32.87%
    2026                 n=102 mean=+11.27% median=+6.03% p25=+0.49% p75=+25.00%
  by by_vix_band:
    calm(<20)            n=137 mean=+22.72% median=+7.58% p25=+0.76% p75=+34.09%
    elevated(20-28)      n=35 mean=+0.30% median=+4.29% p25=-7.06% p75=+17.30%
    panic(>=28)          n=23 mean=+2.80% median=+2.88% p25=-11.02% p75=+16.75%

-- 2. Exit-class agreement matrix (model|real: N) --
    model=TP           real=TP           N=107
    model=TP           real=SL_HELD      N=4
    model=TP           real=HARD         N=3
    model=SL_POPOUT    real=TP           N=4
    model=SL_POPOUT    real=SL_POPOUT    N=4
    model=SL_POPOUT    real=SL_HELD      N=2
    model=SL_HELD      real=TP           N=12
    model=SL_HELD      real=SL_HELD      N=49
    model=SL_HELD      real=HARD         N=1
    model=SL_HELD      real=EXPIRY       N=5
    model=HARD         real=TP           N=2
    model=HARD         real=HARD         N=2

-- 3. Popout reality read (A-1) --
  model: N_breached(SL)=77 N_popped=10 popout_rate=13.0%
    days_to_popout: n=10 mean=+10.1 median=+9.5 p25=+6.0 p75=+13.8
  real: N_breached(SL)=59 N_popped=4 popout_rate=6.8%
    days_to_popout: n=4 mean=+10.2 median=+7.5 p25=+6.0 p75=+11.8

-- 4. Entry-premium reality read (A-3): real / model entry premium --
  overall: n=195 mean=+1.099 median=+1.054 p25=+0.889 p75=+1.258
  by VIX band:
    calm(<20)            n=137 mean=+1.054 median=+1.027 p25=+0.860 p75=+1.247
    elevated(20-28)      n=35 mean=+1.196 median=+1.094 p25=+0.959 p75=+1.312
    panic(>=28)          n=23 mean=+1.226 median=+1.131 p25=+0.870 p75=+1.596
  by RV tercile:
    T1_low               n=65 mean=+1.179 median=+1.152 p25=+0.920 p75=+1.414
    T2_mid               n=65 mean=+1.102 median=+1.032 p25=+0.860 p75=+1.249
    T3_high              n=65 mean=+1.017 median=+1.006 p25=+0.854 p75=+1.136

-- 5. Portfolio: compound + MaxDD (17-month window, $50k start) --
  MODEL-CG       compound=    -41.8%  MaxDD=52.9%  trades=149  skipped(maxpos/cap/same-sym)=0/9/37  forced-open-at-end=0
  REAL-CG        compound=    +68.7%  MaxDD=48.3%  trades=151  skipped(maxpos/cap/same-sym)=2/7/35  forced-open-at-end=0
  intraday-ref   compound=     +8.3%  MaxDD=29.5%  trades=163  skipped(maxpos/cap/same-sym)=0/5/27  forced-open-at-end=0

  N unresolved (no forward data through deadline/expiry): model=0 real=0

==============================================================================
POPULATION: all-tier (APPENDIX)  (N=785)
==============================================================================

-- 1. Per-trade gap distribution (REAL-CG - MODEL-CG net pnl) --
  overall: n=785 mean=+19.50% median=+6.43% p25=-1.01% p75=+35.29%
  by by_liquidity:
    illiquid             n=560 mean=+19.72% median=+5.87% p25=-1.28% p75=+35.91%
    liquid_ge5           n=195 mean=+16.35% median=+6.60% p25=+0.06% p75=+29.33%
    liquid_gt0           n=30 mean=+35.82% median=+37.45% p25=+7.73% p75=+52.53%
  by by_rv_tercile:
    T1_low               n=262 mean=+17.43% median=+7.15% p25=-1.53% p75=+34.90%
    T2_mid               n=261 mean=+23.82% median=+5.94% p25=-1.43% p75=+40.31%
    T3_high              n=262 mean=+17.27% median=+6.55% p25=+0.20% p75=+32.47%
  by by_year:
    2025                 n=362 mean=+18.60% median=+6.67% p25=-1.42% p75=+34.96%
    2026                 n=423 mean=+20.27% median=+6.24% p25=+0.02% p75=+36.74%
  by by_vix_band:
    calm(<20)            n=508 mean=+25.53% median=+8.70% p25=+0.45% p75=+40.81%
    elevated(20-28)      n=179 mean=+10.25% median=+4.30% p25=-8.58% p75=+28.26%
    panic(>=28)          n=98 mean=+5.14% median=+2.78% p25=-11.64% p75=+25.11%

-- 2. Exit-class agreement matrix (model|real: N) --
    model=TP           real=TP           N=374
    model=TP           real=SL_HELD      N=17
    model=TP           real=HARD         N=16
    model=TP           real=EXPIRY       N=8
    model=SL_POPOUT    real=TP           N=20
    model=SL_POPOUT    real=SL_POPOUT    N=13
    model=SL_POPOUT    real=SL_HELD      N=8
    model=SL_POPOUT    real=HARD         N=6
    model=SL_POPOUT    real=EXPIRY       N=3
    model=SL_HELD      real=TP           N=40
    model=SL_HELD      real=SL_POPOUT    N=9
    model=SL_HELD      real=SL_HELD      N=205
    model=SL_HELD      real=HARD         N=22
    model=SL_HELD      real=EXPIRY       N=13
    model=HARD         real=TP           N=19
    model=HARD         real=SL_POPOUT    N=2
    model=HARD         real=SL_HELD      N=1
    model=HARD         real=HARD         N=7
    model=HARD         real=EXPIRY       N=2

-- 3. Popout reality read (A-1) --
  model: N_breached(SL)=339 N_popped=50 popout_rate=14.7%
    days_to_popout: n=50 mean=+7.9 median=+8.0 p25=+3.2 p75=+11.0
  real: N_breached(SL)=255 N_popped=24 popout_rate=9.4%
    days_to_popout: n=24 mean=+7.8 median=+7.5 p25=+5.0 p75=+10.2

-- 4. Entry-premium reality read (A-3): real / model entry premium --
  overall: n=785 mean=+1.684 median=+1.057 p25=+0.850 p75=+1.274
  by VIX band:
    calm(<20)            n=508 mean=+1.350 median=+1.027 p25=+0.838 p75=+1.226
    elevated(20-28)      n=179 mean=+2.924 median=+1.114 p25=+0.874 p75=+1.365
    panic(>=28)          n=98 mean=+1.153 median=+1.139 p25=+0.889 p75=+1.363
  by RV tercile:
    T1_low               n=262 mean=+1.243 median=+1.170 p25=+0.909 p75=+1.398
    T2_mid               n=261 mean=+2.548 median=+1.056 p25=+0.852 p75=+1.289
    T3_high              n=262 mean=+1.265 median=+0.988 p25=+0.819 p75=+1.161

-- 5. Portfolio: compound + MaxDD (17-month window, $50k start) --
  MODEL-CG       compound=    -76.8%  MaxDD=80.3%  trades=264  skipped(maxpos/cap/same-sym)=28/318/175  forced-open-at-end=0
  REAL-CG        compound=    +76.3%  MaxDD=61.6%  trades=276  skipped(maxpos/cap/same-sym)=26/325/158  forced-open-at-end=0
  intraday-ref   compound=    -19.3%  MaxDD=52.6%  trades=375  skipped(maxpos/cap/same-sym)=4/256/150  forced-open-at-end=0

  N unresolved (no forward data through deadline/expiry): model=0 real=0

==============================================================================
SECTION 3 -- PINNED FLAG EVALUATION (mechanical, liquid-primary headline)
==============================================================================
REPORT-ONLY: N-floor not met, no flags evaluated.
Reasons:
  - N-floor not met (liquid-primary N=195 < 800) -- report-only, no flags evaluated
```

