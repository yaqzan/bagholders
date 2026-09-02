# PTJ 200-DMA Trend Theory — Comprehensive Audit — FINDINGS

**Date:** 2026-08-10 (overnight /research run, ~01:30-09:30 ET budget)
**Status:** COMPLETE — all four legs adjudicated against the pre-registered gates.
Prereg: `PREREGISTRATION.md` (locked + committed `41081764` before any outcome).
**Verdict: AUDITED IN FULL, NO SHIP (the honest outcome).** PTJ's 200-DMA rule is a
REAL crash-defense instrument that RELIABLY COSTS compounding (both substrates,
NW t ~= -2.4); its "keeps you from going to zero" protection is real but tail-only
and death-specific (median delisted outcome actually WORSENS — most delistings are
acquisitions); **no EMA/SMA variant, band, dual-MA overlap, or slope rule escapes the
tradeoff (0/504 grid cells with positive excess across SPY + the survivorship-honest
universe)**; and for OUR buy-weakness call sleeve the rule is INVERTED — entries below
the 200-DMA are the sleeve's BEST cohort (0/96 escalation on 4.15M trades), so the
axis is now closed at all three layers (selection / exits / book-level overlay).

## The theory under audit

Paul Tudor Jones (Tony Robbins interview): (1) always be with the predominant trend;
(2) "my metric for everything is the 200-day moving average of closing prices — if it
falls below, you get out, you play defense"; (3) it keeps you from "losing everything" /
riding things to zero (he cites exiting before the 1987 crash); (4) 5:1 risk/reward
sizing ("I can be wrong 80% of the time and still not lose").

User directive: comprehensively backtest/audit this theory, plus other EMAs and EMA
overlaps, on our data.

## Where the question was ALREADY closed (no re-run; the do-not-relitigate boundary)

1. **As trade-selection features for the funded options sleeve:**
   `experiments/trend_ma_lattice/FINDINGS.md` (2026-07-14) — EMA+SMA x
   {8,13,21,34,50,100,150,200} x above/below x 7 cross pairs x freshness x
   stack-alignment x kernel-divergence, 349 cells on the funded v74 75+ CALL ledger
   (N=5,854): **comprehensive null**; fresh golden crosses lean NEGATIVE EV (G27 —
   winners continue, a fresh cross is a reversal-chase); below-short-MA is weakly
   POSITIVE (the sleeve's buy-weakness edge, G19/G45). "Other EMAs and overlaps" as
   selection signals = closed.
2. **As an exit overlay on open option positions** ("get out when it breaks the MA"):
   the trailing-stop/exit family is closed by the capital-velocity law (G16,
   `experiments/apex_speed_v70/`): TSL variants = 100% collapse on the bulk; dead-hold
   dominates. A 200DMA exit on open calls is a TSL variant.
3. **As a trend-confirm regime trim:** `experiments/bearchop_trend_dd/` (2026-06-17)
   precondition-null — per-window sign flips (G26), RXDD territory.

## What the live system ALREADY embeds of PTJ's metric

- The **TREND score component** is built on EMA50/EMA200 stack + price position (the
  dominant driver of `overall`, corr ~+0.72 — `weather_components`). The system "is
  with the trend" by construction; its measured weakness is regime-conditional
  dominance, not construction (`trend_ma_lattice` obs. 3).
- **Market breadth** consumes %-of-universe above EMA50 (12% weight) and EMA200 (5%)
  plus EMA-based Hindenburg preconditions -> regime multiplier.
- Portfolio DD levers already key on regime state: RXDD (VIX band), MWDD (McClellan
  flat-band), BDIV (SPY-near-high x breadth rollover), TVDD (TRIN), F3F, DD-soft-band.
- So "should we USE the 200DMA?" decomposes to: (a) selection — closed null; (b) exits —
  closed (dead-hold law); (c) book-level sizing overlay — **Leg 3 tonight**; (d) as an
  equity-holding discipline (PTJ's actual context) — **Legs 1-2 tonight**.

## Leg 1 — SPY index-level PTJ rule — **DEFENSE REAL, OFFENSE FALSE (both as pre-registered)**

`spy_claim.py` (1.4s, 8,051 sessions 1995-01-03..2026-08-06, 408-cell grid, all
selftests PASS). Canonical = the literal PTJ rule: SMA200, band 0, 25bps/side, cash 0%.

| | PTJ rule (canonical) | Buy & Hold |
|---|---|---|
| CAGR | +6.30% | **+11.13%** |
| MaxDD | **-34.85%** | -55.19% |
| Sharpe | 0.565 | 0.654 |
| Time in market | 75.0% | 100% |
| Worst year | -21.3% | -36.8% |

- **P1-DD TRUE:** MaxDD cut by 20.3pp (bar was 10pp). The defense claim is REAL — and
  it is specifically CRASH defense: dotcom +19.9pp, GFC +38.5pp, COVID +15.3pp,
  2022 +7.5pp vs B&H inside the crash windows. "At the very top of the crash, I was
  flat" replicates mechanically.
- **P1-OUT FALSE:** CAGR -4.8pp/yr vs B&H; NW t = **-2.46** (excess is significantly
  NEGATIVE); circular-block-bootstrap CI95 on the CAGR diff [-9.07%, -0.36%] excludes
  zero. The cost lives in the grind years (2003-07 -72.9pp, 2013-19 -53.9pp,
  2023-now -43.7pp total-return shortfall) — the same anatomy as the VIX-cycle claim
  (`experiments/vix_cycle_claim/`): defense buys crash comfort with compounding.
- **The "other EMAs and overlaps" answer, index level: 0 of 408 cells** (SMA+EMA x
  {20..250} x bands x 6 dual-MA overlap pairs x slope rules x costs x cash) **has
  positive Newey-West excess vs B&H.** Uniform. There is no MA variant or overlap that
  turns the filter into outperformance on SPY; the best cells (EMA100-150, 5% band,
  3% cash) raise SHARPE to ~0.85-0.91 with MaxDD ~-20..-27% — i.e. the filter is a
  legitimate volatility/DD-preference instrument, never a return enhancer. (Most
  extreme cell overall: EMA20/b0/25bps at t=-8.3 — fast MAs + costs are reliably
  ruinous.)
- Multiplicity honored: sign-flip max-|t| null (200 resamples) threshold 2.83; no
  positive-excess cell exists to test against it.

## Leg 2 — Universe per-stock rule, survivorship-honest — **DEFENSE REAL, COSTS COMPOUNDING; the "go-to-zero" mechanism REAL but tail-only**

`universe_claim.py --full` (queue #319, ~13 min): 1,626 symbols incl **588 delisted**,
7.11M rows, 1997-12-31..2026-08-07, 96-cell grid, all selftests PASS (the delisted
freeze-forward selftest caught a real padding bug pre-run). Canonical = per-name
sleeve, SMA200, band 0, 25bps/side, daily.

| | PTJ rule (canonical) | Buy & Hold |
|---|---|---|
| CAGR | +7.71% | **+11.42%** |
| MaxDD | **-39.47%** | -57.35% |
| Sharpe | 0.470 | 0.443 |
| Time in market | 61.1% | 100% |
| Round trips/name/yr | 2.49 | n/a |

- **P2a TRUE:** MaxDD cut 17.9pp (bar 10pp). Defense replicates on single stocks.
- **P2b = COSTS (the open question resolves):** -3.71pp CAGR/yr, NW t **-2.41**.
  **0 of 96 cells** has positive NW excess (max t -0.84). Best cells
  (SMA150-200, 3% band, no-cost, portfolio-monthly) reach Sharpe 0.93-0.96 at MaxDD
  -47..-50% — again a Sharpe/DD-preference instrument, never a return enhancer.
- **P2c FALSE on the pre-registered compound bar — but the MECHANISM decomposition is
  the real finding:**
  - The left-tail truncation is REAL and DEATH-SPECIFIC: share of delisted names with
    lifetime return <= -80% falls **14.8% -> 3.6%** (4.1x) under the filter, while the
    SURVIVOR cohort's tail is unchanged (3.8% -> 3.8%) — this passes the pre-named
    falsification test (the benefit is death-dodging, NOT market-timing beta). PTJ's
    "I've seen too many things go to zero; the rule gets you out" is mechanically TRUE.
  - But the MEDIAN delisted outcome WORSENS under the filter: +49.9% (B&H) -> +11.7%
    (filtered), -38.2pp (MW z -3.83) — because **492 of 555 delistings are M&A-like**
    (final-126d return > -40%) vs only **63 death-like**: most "delistings" on this
    universe are acquisitions, and the rule systematically exits names being bid UP
    toward a deal (or sits in cash through the recovery). The 15pp tail bar was also
    just missed (11.2pp).
  - Net: the aphorism is literally true about ZEROS and false as a wealth statement —
    tail insurance paid for with the median outcome and -3.7pp/yr compounding.
- **P2d FALSE ("always be with the predominant trend"):** cross-sectional forward-1m
  spread of above-vs-below own 200SMA = **+0.04%/mo, t 0.16** (sign positive 6/7 eras
  but magnitude ~zero); EMA200 +0.03%/t 0.11; the 50-day variants lean NEGATIVE
  (t ~-1.2). There is no exploitable monthly relative-strength edge at MA horizons on
  this universe — weaker than even the prereg's lukewarm prediction, and the 50d
  inversion is coherent with the buy-weakness world the sleeve lives in.

## Leg 3 — Stage-3 SPY-MA overlay on OUR sleeve — **CLOSED NULL (prediction P3 CONFIRMED)**

`experiments/spy_ma_overlay/mine.py`, full run (queue #318, 56s): 12 windows, 4,145,319
funded call trades, join coverage 100.0000%, 96 cohorts (SMA/EMA x 150/200 x state/
dist-band/days-in-state/fresh-cross). **0 of 96 cohorts pass the pre-registered
escalation gate; most fail 4-5 of its 5 legs.**

The direction is the predicted INVERSION (G19/G45 buy-weakness), now confirmed on the
market-MA axis:

| cohort (SMA200 of SPY) | pooled-5y dEV | cluster z | dd_conc |
|---|---|---|---|
| state=below (the literal PTJ "get out" state) | **+0.0057** | +0.36 | **0.86** |
| pct_dist <= -10% (deep panic) | **+0.159** | **+5.25** | **0.27** |
| fresh_cross_below (<=10d) | +0.031 | +1.08 | 0.90 |
| state=above (the "safe" state, 81% of trades) | -0.0057 | -0.36 | 1.03 |

- Entries taken while SPY is BELOW its 200DMA are equal-or-BETTER EV and LESS
  DD-concentrated than the rest of the book; the deep-below band is the sleeve's single
  best cohort in the table (2018 window: below-state mpnl +0.179 vs rest -0.000, z 2.7).
- "Play defense below the 200DMA" applied to this sleeve would therefore delete its best
  entries while leaving DD concentration where it actually lives (mildly on the ABOVE
  side, dd_conc 1.03 — i.e. nowhere actionable).
- Cluster stats are honest: per-date two-sample Welch t (a market-level feature
  partitions calendar days; a paired same-date diff is degenerate — builder-caught).
  RXDD/MWDD firing bands read live from strategy_config; the levers-OFF slice and
  DD-active slice legs were evaluated per prereg and no cohort survives them either.
- Third independent confirmation of the inversion at a market-state axis (after
  %-at-ATH `breadth_ath_dd`, VIX-cycle claim, market-wave/`spy_breadth_corr_dd`) —
  the sleeve's edge IS buying weakness; market-level MA defense is structurally
  anti-aligned with it. What little mid-band weakness exists is already owned by
  RXDD/MWDD.

## Leg 4 — "5:1 risk/reward" vs our swept geometry (documentation; no new compute)

PTJ's 5:1 is a macro-futures asymmetry: tight stop, huge target, low hit-rate viable
because the instrument has no theta and unlimited time. Our instrument (30-DTE long
calls) pays theta per day and dies at expiry, which inverts the viable geometry:

- The tight-SL corner is closed: option-price SLs convert underlying noise into
  realized losses; historic sweeps repeatedly found tightening exits catastrophic
  (monte-carlo-sweeps.md: stress-TP tightening -60..-96%; SL sweeps settled at wide
  -70 with the day-15 hard exit). The live geometry is TP+30/SL-70 + dead-hold —
  the OPPOSITE of 5:1 per-trade (roughly 1:2.3 against), and it wins because hit-rate
  ~65-70% x capital velocity dominates under theta (G16).
- The 5:1-like convex corner WAS tested on our sleeve: the OTM sprint ladder
  (5-10% OTM, TP+400% — risk-1-make-5 shaped lottery; `experiments/bankroll_ladder/`,
  memory 2026-07-20): wins in-sample, **collapses OOS**; parked (underpowered, not
  killed) for the Dec-2026 OOS re-read.
- Conclusion: 5:1 is not portable to theta-bearing weeklies-to-monthlies; the
  asymmetry our system exploits is hit-rate + velocity + dead-hold convexity
  (SL only on -70 marks; expiry losses bounded at premium), not per-trade R:R.

## Multiplicity / honesty notes

Leg grids are descriptive maps; claims adjudicate ONLY on pre-named canonical cells
(prereg). Any "best cell" callout must clear the sign-flip max-|t| null. Surprises get
recorded as surprises (G53) — mechanism predictions were pre-named (P2c: left-tail
truncation, falsified if survivor cohort improves equally).

## Artifacts

- `experiments/ptj_trend_audit/{PREREGISTRATION.md, pull_universe.py, spy_claim.py,
  universe_claim.py}` + `.cache/ptj_trend_audit/{universe_1997, spy_grid,
  universe_grid, delisted_cohort, spread_panel}.parquet`
- `experiments/spy_ma_overlay/mine.py` + `.cache/spy_ma_overlay/report.json`
- Queue tasks: #317 (universe pull: 1,626 syms/588 delisted/7.11M rows), #318 (Leg-3
  mine, 56s), #319 (Leg-2 full grid, ~13 min). Builders: 2x Sonnet (~635k tokens),
  prereg commit `41081764`.

## What would change these verdicts

- Sleeve overlay: a genuinely NEW discriminator class (not another MA/kernel/band/
  market-state variant) — same bar as trend_ma_lattice.
- Claim legs: nothing pending — the decomposition is stable across 504 cells and two
  substrates; the Dec-2026 OOS unlock does not touch these (full-era descriptive audit).
- If the owner ever runs a LONG-EQUITY sleeve (capital-plan context), the Leg-1/2
  numbers quantify the instrument choice on our own data: the MA filter buys
  ~18-20pp MaxDD and the death-tail cut at ~-3.7 to -4.8pp CAGR/yr and a worsened
  median. That is an allocation-preference fact, not a recommendation.
