# PTJ 200-DMA Trend Theory — Comprehensive Audit — PREREGISTRATION

**Locked:** 2026-08-10 ~01:40 ET, before any outcome computation. Autonomous /research run
(user directive: "comprehensive backtest and audit of this theory and also other emas and
maybe overlaps of different emas ... be thorough and proceed autonomously").
**Budget:** ~8h (next open 2026-08-10 09:30 ET).

## The theory under audit (Paul Tudor Jones, via Tony Robbins interview)

1. "You always want to be with whatever the predominant trend is."
2. "My metric for everything I look at is the 200-day moving average of closing prices...
   If you use the 200-day moving average rule, then you get out. You play defense."
3. "I've seen too many things go to zero... One principle for sure would get out of
   anything that falls below the 200-day moving average."
4. (Secondary) "5:1 risk/reward... I can be wrong 80% of the time and still not lose."

## Prior art that BOUNDS this run (do-not-re-run)

- **`experiments/trend_ma_lattice/` (2026-07-14): COMPREHENSIVE NULL, axis closed.**
  EMA+SMA x {8,13,21,34,50,100,150,200} x above/below x 7 cross pairs x freshness x
  stack-alignment x kernel-divergence — 349 cells on the funded v74 75+ CALL ledger.
  **The per-signal form of "200DMA / EMA overlaps as trade-selection features for our
  options sleeve" is CLOSED and is NOT re-tested tonight.** Fresh crosses were *negative*
  EV; below-SMA21 weakly positive (buy-weakness inversion, G19/G45).
- `experiments/bearchop_trend_dd/` (2026-06-17): trend-confirm x regime Stage-3 trim =
  precondition-null (G26 sign-flips; RXDD territory).
- `experiments/vix_cycle_claim/` (2026-07-26): the market-timing claim-audit harness
  pattern (MarketRegime.spy_close 1995+, close-only state machine, B&H comparison).
- G16 capital-velocity: exit/hold overlays on OPEN option positions (trailing stops etc.)
  are closed — "get out below the MA" applied to open calls is a TSL variant; cite, no re-run.
- TP/SL asymmetry space (the 5:1 thought): exhaustively swept (monte-carlo-sweeps.md; OTM
  sprint ladder +400%TP collapsed OOS; tight-SL whipsaw documented). Documentation leg only.

## What IS open (tonight's legs)

### Leg 1 — SPY index-level PTJ rule (claim audit; descriptive + canonical adjudication)

Substrate: `MarketRegime.spy_close` (adjusted, total-return-comparable both arms),
1995-01→now. State machine: long when signal true, else cash; execution t+1 close;
costs per side; cash yield variants.

Grid (descriptive map, "other emas and overlaps"):
- family `price_ma`: kernel {SMA,EMA} x length {20,50,100,150,200,250} x hysteresis band
  {0,1,3,5%} x cost {0,5,25 bps/side} x cash {0,3%/yr}
- family `dual_ma` (overlaps): fast>slow for {(10,150),(20,100),(20,200),(50,150),(50,200),
  (100,200)} x {SMA,EMA} x cost x cash (band=0)
- family `slope`: MA{50,200} rising over k{5,20}d x kernel x cost x cash
Metrics per cell: CAGR, MaxDD, Sharpe, time-in-market, trades/yr, worst-year, per-era
returns (1995-02, 03-07, 08-12, 13-19, 20-21, 2022, 23-now), per-crash capture
(2000-02, 2008, 2020-02..03, 2022). Stats: Newey-West t on monthly excess vs B&H;
stationary block bootstrap (seed 0) CI on CAGR diff for the canonical cell.

**Canonical cell (the literal PTJ rule): SMA200, band 0, 25bps/side, cash 0%.**
- **P1-DD (predict TRUE):** canonical MaxDD <= B&H MaxDD - 10pp.
- **P1-OUT (predict FALSE):** canonical CAGR > B&H CAGR with NW t>=2.
- Multiplicity: ~400 correlated cells; NO cell other than canonical adjudicates the claim.
  Best-cell callouts must beat a sign-flip max-|t| null (200 resamples) to be named
  "better than 200SMA" rather than "grid noise".

### Leg 2 — Universe per-stock rule, SURVIVORSHIP-HONEST (the crown-jewel leg)

Substrate: rebuilt `price_history` (2026-07-29 repair), ALL symbols incl ~600 delisted,
1997+ (`.cache/ptj_trend_audit/universe_1997.parquet`, schema
[symbol, date, close, volume, delisted_date]). Adjusted closes (correct space for MA +
total-return; no strikes involved). Eligibility: name enters rules only after L+20 bars
(young-listing guard). Delisted names terminate at last bar (terminal value = last close).

Arms per cell: per-name sleeve (each name independently long-above / cash-below, t+1,
costs on transitions; equal-weight average of per-name paths) = the clean test of the
rule; portfolio mode (renormalize across in-state names; embeds a breadth-timing bet;
labeled as such). B&H = same eligibility, no filter.
Grid: kernel {SMA,EMA} x length {50,100,150,200} x band {0,3%} x cost {0,25bps} x
rebalance {daily, monthly-eval}. Canonical: **per-name, SMA200, band 0, 25bps, daily.**

- **P2a-DD (predict TRUE):** canonical portfolio-path MaxDD <= B&H - 10pp.
- **P2b-CAGR (open — no strong prior; this is the genuine empirical question):**
  BEATS if CAGR diff > +0.5pp AND NW t>=2 (monthly excess); MATCHES if within +/-0.5pp;
  COSTS otherwise.
- **P2c-ZERO (predict TRUE, mechanism pre-named):** PTJ's "keeps you from going to zero":
  on the DELISTED cohort, filtered per-name lifetime total return: median improves
  >= +15pp vs B&H AND share of names with lifetime return <= -80% falls >= 15pp
  (absolute). **Mechanism prediction:** deaths grind DOWN through the MA months before
  delisting -> the filter exits early (left-tail truncation). **Falsification:** if the
  survivor cohort shows the ~same improvement as the delisted cohort, the effect is
  market-timing beta, not death-dodging — record as falsified mechanism even if the
  headline number passes. Decompose delisted into death-like (final-126d return <= -40%)
  vs M&A-like (else); imperfect proxy, declared here, reported both ways.
- **P2d-TREND (predict TRUE pooled, era-unstable):** cross-sectional monthly panel:
  names above their 200SMA vs below, forward-1m equal-weight return spread > 0 with
  block-bootstrap t>=2 AND sign stable in >=5/7 eras. (This is "the trend is your friend"
  measured honestly on our universe.)

### Leg 3 — Stage-3 overlay mine on OUR sleeve (the only ship path tonight)

Feature: SPY vs its own {150,200} {SMA,EMA} — state, pct-distance bands, days-in-state
buckets, fresh-cross(<=10d) — computed point-in-time (recursive/rolling on daily closes,
no resample; G28) from MarketRegime.spy_close; joined to `.cache/dd_ledger/tape_*.parquet`
(2026-07-25 build, v74 engine, windows 2018/2020/2020_crash/2021-2025/dip/22-now/5y/10y)
by entry_date. Calls only. Per cohort x window: N, mean option_pnl, dEV vs window-rest,
date-cluster z, dd_conc; RXDD 2x2 (VIX 20-28 at entry vs not); MWDD 2x2 (band per
strategy_config); levers-OFF slice (VIX<20 AND |McClellan|>30); DD-active slice
(entry_dd >= 0.13).

- **P3 (predict INVERTED-or-REDUNDANT per G19/G45/G43):** below-200DMA-entry cohorts show
  EV >= rest (buy-weakness winners), or their low-EV dissolves in the levers-OFF slice.
- **Escalation gate (ALL must hold for any cohort):** (a) dEV <= -0.03 with date-cluster
  z <= -2.5; (b) dd_conc >= 1.5; (c) <=1 window contradiction at |z|>=2 across
  {2020,2021,2022,2023,2024,2025,dip,22-now}, and not crash-window-driven; (d) survives
  levers-OFF slice with dEV <= -0.02; (e) DD-active slice consistent.
  PASS -> env-gated dampener + G4 OFF-byte-identical verify + Phase B (N=100x6); C/D only
  as time allows; **out-of-time -> STAGE (G13), never rush-ship.** FAIL -> closed-null.

### Leg 4 — 5:1 risk/reward (documentation only, no compute)

Answer from the swept record: TP/SL grid history, OTM sprint ladder (+400%TP OOS
collapse), SL tightness whipsaw findings, and why 30-DTE long calls at TP+30/SL-70 +
dead-hold occupy a different (theta-constrained) geometry than PTJ's macro futures 5:1.

## Interpretation boundary (declared to prevent narrative bleed)

Legs 1-2 audit the theory as an EQUITY-HOLDING discipline (PTJ's context; relevant to
the owner's capital plan). They license NO change to the options sleeve. Sleeve changes
tonight can come ONLY from Leg 3's gate. The per-signal scoring form is closed
(trend_ma_lattice) and is not relitigated by any Leg 1/2 result.

## Holdout note

Legs 1/2/4 are descriptive claim audits (not calibration of a shippable mechanism) and
run full-era, matching the vix_cycle_claim precedent. Leg 3's mine reads the existing
tapes; any escalated sweep pins ALGORITHM_VERSION and uses the standard windows.

## Compute plan

Pull (queued, db heavy) ~15min; builders (2x Sonnet, parallel) ~45-60min; Leg 1 direct
(~1min, light); Legs 2-3 queued (`--db light`, high priority, off-market); adjudication +
escalation window ~2h; docs ~1h. All heavy compute via `trader queue submit`.
