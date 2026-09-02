# PREREG — The selectivity frontier under honest fills

STATUS: LOCKED 2026-08-12 before any outcome exists (git commit = lock).
Owner: "Go" on the five-question program (index bar; liquidity proxy; gradient + ablation;
sparseness levers; puts). Calibrated fills are the ENGINE DEFAULT post-flip; canon unused.

## 0. Global BENCHMARK RULE (new, from owner Q1 — supersedes "positive = interesting")

Every battery table carries reference columns per window: **SPY buy-and-hold return**
(from price_history adjusted closes over the window's exact [start,end]) and **HISA 4%/yr**
compounded over the window span. A config is **"index-competitive"** ONLY if its
SURVIVOR-arm calibrated median beats the SPY column on BOTH 22-now and 5y.
"Calibrated-positive" alone is a measurement milestone, never an investment claim.
(Context anchor: Sentinel's guard-verified survivor medians ≈ 3-5% CAGR — positive,
likely NOT index-competitive; this campaign measures instead of presumes.)

## Phases and arms

**F0 — tape decomposition (report-only, no gate).** From `sentinel_guards_2026_08/out/
tapes/` + scores/components DB pulls: decompose 85+ trades vs the 75-84 population by
sub-band (85-87 vs 88+), liquidity tier, component signature, regime flag. Also: the
liquidity distribution of 85+ names vs 75-84 (are Sentinel names mostly liquid anyway?).
Priors for F1/F2 and Q2; no decisions.

**F1 — threshold blast.** Min-score ∈ {78, 80, 82, 85, 88} × Sentinel shape (cascade
0.2/0.15/0/0, mp14, gross 0.30, TP 0.10/SL −1.00, 30-DTE) × 12 windows × N=500,
calibrated. The 85 row is the identity anchor (must reproduce R1/guards Sentinel).
Report per cell: med/DD/collapse + n_signals + median trades/path + SPY/HISA columns.

**F2 — guards on carriers.** Carry rule (LOCKED): full-universe calibrated median > 0 on
BOTH 22-now and 5y AND median trades/path ≥ 30 on both. Each carrier (≤3; if more
qualify, the 3 with highest 5y survivor-expected supply, i.e. lowest thresholds) gets:
survivor arm (12w) + MISS_P-0.20 buffer (6 recent windows), N=500. PASS = survivor
medians > 0 both decision windows AND DD ≤ full +3.0pp (sentinel_guards mirror). The
**index-competitive** label then applies per §0 using the survivor medians.

**F3 — sparseness levers on F2 passers (≤3 configs).** Axes: DTE ∈ {21, 45, 60} (via
NOMINAL_CAL_DTE; 30 = anchor; CAVEAT recorded: fill-miss rates were measured at 30-DTE —
MISS_P 0.15 transfers as an assumption) and gross ∈ {0.20, 0.45} (0.30 = anchor), on
windows {22-now, 5y}, N=500 calibrated. Reading rule (LOCKED): a lever is REAL if median
improves ≥ +5.0pp on both windows with DD not worse by > 2.0pp and collapse 0; any REAL
lever then gets its own survivor check before the label sticks. Heavier sizing beyond
0.45 is BANNED from this campaign (capped-TP/fat-tail Kelly logic; DD-primary doctrine).

**F4 — put arm.** Max-score ∈ {15, 12} (bearish extreme) × put-Sentinel shape × recent
windows {2023, 2024, 2025, dip, 22-now, 5y} × N=500 calibrated. Put trading must be
enabled via existing config/env only (PUT tiers/PUT_TP already exist in strategy_config);
if clean enablement is impossible without code edits → STOP the arm, report. CAVEAT
recorded: fill fidelity was measured on CALLS; MISS_P 0.15 on puts is an untransferred
assumption (put chains likely thinner → optimistic). Verdict rule (LOCKED): the put axis
is ALIVE only if calibrated median > 0 on BOTH 22-now and 5y with trades/path ≥ 20;
else hedge-not-edge stands and the axis stays closed.

**F5 — liquidity-proxy validation (report-only, licenses a follow-up build, no 30y
rebuild tonight).** On the 2022-08+ overlap: within-era percentile of underlying
dollar-volume vs measured option liquidity (FF-3' opt_vol_30d_atm + ledger entry_volume)
— rank correlation overall + by tier + by score band. Deliverable: "rank-faithful or
not" + the misclassification table. A 30-year honesty-filter build proceeds later ONLY
if rank-faithful (Spearman ≥ 0.6 overall AND monotone across tiers — LOCKED bar).

## Out of scope (LOCKED)

Component recombination / re-scoring ("reverse-engineer the 85+ signature into 75-84")
— explicitly GATED ON the 2026-12-15 OOS re-grade per standing ruling. OTM structures —
parked axis, Dec unlock only. No portfolio ship of any kind from this campaign; findings
feed separate ship processes with their own evidence.

## AMENDMENT-1 (2026-08-12, pre-F2/F3/F4-outcome, mechanism correction + diagnostic arm)

F1's identity anchor FAILED as designed to: the ctx-removal min-score filter is NOT how
Sentinel implements selectivity. Diagnosed (SQL-verified counts; source-traced): the real
profile keeps ALL candidates in ctx (their density feeds `_opportunity_saturation_scale`
and potentially other pressure-dependent dampeners) and selects via TIER FUNDING (mid/low
zeroed) — plus CTSL promotes counter-trend picks into the funded ultra slot regardless of
raw score (~23% of Sentinel's 5y funded trades, confirmed in tapes). F1's 60 cells stand
as a LABELED different object ("pure ≥T picks, density-native context") — decision-inert
for F2 but diagnostically precious: pure-85+ picks WITHOUT the machinery read NEGATIVE
(22-now −13.4% vs anchor +37.4%). Corrections, all tightening:

1. **F1′ threshold mechanism = Sentinel-faithful:** full ctx, selectivity via tier
   funding. Thresholds are therefore TIER-NATIVE — the builder must read the actual
   `score_to_tier` band edges from source and REPORT them before running; the grid =
   the tier-quantized points available (expected ≈ {fund-all, zero-low, zero-low+mid
   (=Sentinel anchor), ultra-only}) at Sentinel's shape, 12 windows, N=500, CTSL ON
   (it is part of the shipped mechanism at every threshold). Identity gate: the
   Sentinel-equivalent point must reproduce R1 bit-exactly (same mechanism, same
   substrate — exactness expected, not a tolerance band).
2. **F1″ CTSL decomposition arm (the accidental ablation, formalized):** Sentinel shape
   × 12 windows × N=500 with CTSL promotion DISABLED — via clean config/env if one
   exists, else the sanctioned driver-subprocess monkey-patch (try/finally, patch the
   promote path to identity pre-prepare; production code untouched). Together with the
   R1 anchor and F1-as-run, this brackets Sentinel's edge into: raw-85+ picks vs CTSL
   promotions vs density-aware sizing. Report the three-way attribution arithmetic.
3. **F2 carry rule** now reads F1′ (tier-native grid). F4 puts use tier-native put
   funding (`PUT_TIER_*_OV` — already the right mechanism). F0 component table is
   RERUN with CTSL-promoted trades as their OWN row (current band means are
   contaminated by tier-vs-score bucketing).
4. **F5 verdict recorded:** rank-faithfulness FAILS (ρ=0.154 overall, every band ≤0.23,
   bar 0.6) — the 30-year underlying-volume liquidity proxy is NOT licensed. Caveat on
   record: computed on the kept-ledger population (range-restricted); a full-universe
   re-check may someday revise this, but for OUR tradeable population the answer is no.

## Stop rule

Phases as enumerated; F3 only on F2 passers; no new thresholds/axes/windows after any
outcome is seen; amendments pre-outcome or tightening-only, dated, committed. FINDINGS.md
either way. Standard equipment on every battery: substrate fingerprint + close-boundary
guards, per-cell env self-logging, subprocess-per-cell.

## Compute

F1 60 + F4 ≤12 + F2 ≤54 + F3 ≤30 + reports ≈ ≤160 cells ≈ 1-1.5h at high/--db light.
Ops note: backup #495 (idle) may be preempted by these cells and by the 04:00 update
regardless — its 9 critical tables banked at 03:0x; option_prices tail remains the known
structural item on the owner list. Do not bump priorities either way tonight.
