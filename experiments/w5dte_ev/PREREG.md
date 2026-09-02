# PREREG -- w5dte_ev (EV study for the W5DTE rule family)

Status: LOCKED 2026-08-18 01:45 ET, before any EV number was computed. Owner locks:
OWNER_SPEC.md (same dir). Parent: experiments/weekly_5dte_movers/ (FINDINGS.md,
RESULTS_TABLES.md). Deviations = dated amendments here.

## Question

Does the W5DTE rule family's 2.3-2.4x touch-rate lift convert into positive expected
value per premium dollar under realizable exit policies -- and does it beat an
exposure-matched random control, which holding-period/side/entry-day exposure alone
would already earn?

## Substrate + population

- `B:\polygon_derived\weekly_5dte_movers\features\analysis_*.parquet` (glob; `_smoke`
  excluded). NO rescans of tape or MySQL.
- Population = the parent's ANALYSIS population verbatim: covered==1 AND
  no_later_print==0 AND entry_close>=0.20 AND entry_volume>=100 AND
  entry_transactions>=10 AND adjusted==false. Holdout: expiries <= 2026-06-12
  (assert via experiments/_holdout after entry_date->date rename).

## Rules under test (pinned EXACTLY as discovered -- no recomputation of thresholds)

R1: moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND sector=="technology" AND is_monthly_opex==false
R2: otm_pct>=0.0576 AND moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND sector=="technology"
R3: otm_pct>=0.0576 AND hl_range_pct>=127.3 AND cp=="C" AND sector=="technology"
R4: moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND cp=="C" AND sector=="technology"
R5: otm_pct>=0.0576 AND moneyness_pct>=0.03958 AND hl_range_pct>=127.3 AND is_monthly_opex==false
R6: otm_pct>=0.0576 AND hl_range_pct>=127.3 AND cp=="C" AND is_monthly_opex==false
FAMILY = R1 OR ... OR R6 (primary unit of adjudication). FAMILY_C = FAMILY AND cp=="C".
Fidelity self-test: recomputed (n, winner_rate) for each rule must match parent
RESULTS_TABLES E3 exactly (e.g. R1 n=25663, winner_rate=0.207419).

## Exit policies (owner lock #1)

Per entry event (entry at that day's close; premium P0 = entry_close):
- TP-L for L in {2,3,5,10}: the sell limit at L*P0 FILLS iff max_future_high >= L*P0
  (max_future_high is the max of STRICTLY-LATER daily highs within the week, so the
  entry day's own high never fills; TP-only longs have no SL race, daily bars suffice).
  Fill => exit proceeds L*P0, LIMIT = COST-FREE (asymmetric canon). No fill => expiry
  settlement (below).
- EXPIRY (baseline): settlement = close_at_expiry if non-null else 0 (stopped printing
  => worthless; null share reported). Settlement is a FORCED exit: proceeds =
  settle - max(0.0142*settle, 0.025) floored at 0, and applied only if settle > 0.025
  (below one half-tick: let it expire, proceeds 0). The 1.42% is half of FF-2's
  worst-tier median Roll FULL spread 2.84% (FF2_RESULTS.md); the $0.025 absolute floor
  is half a nickel-wide market -- deep-OTM sub-$1 contracts are tick-bound, and the
  percent term alone would flatter them.
- Entry is mid-at-close and free for BOTH arms (canon). Honesty note: generous in
  absolute terms for these wide contracts; identical treatment in rule and control
  arms, so the CONTRAST is cost-fair even where absolutes are optimistic.
- Per-event return r = proceeds/P0 - 1. EV = equal-weighted mean r per (unit, policy);
  premium-weighted mean also reported.

## Control (owner lock #2)

Exposure-matched random: within every (expiry_week x side x entry_dow) cell, draw
WITHOUT replacement, from the cell's population EXCLUDING the arm's own rows, exactly
the arm's row-count in that cell (cells where the complement is too small draw the full
complement; shortfall reported). 100 seeded draws (seed 20260818 + draw index), each
priced under every policy identically. Control distribution = the 100 EVs per policy.

## Adjudication (pinned before results)

- PRIMARY: FAMILY arm, TP-5x policy (matches the discovery threshold).
- **PASS (paper tape licensed, owner lock #3)** iff BOTH:
  (a) FAMILY TP-5x EV beats >= 99/100 control draws (empirical p <= 0.01), AND
  (b) FAMILY TP-5x EV > 0 after costs.
- Robustness (reported, non-gating): all 5 policies x {FAMILY, FAMILY_C, R1..R6};
  per-expiry-year EVs (5 slices); win-rate per policy; control percentile per cell.
  A PASS with negative expiry-baseline EV is still a PASS (the family is a scalp
  candidate, not a hold candidate) but FINDINGS must say so plainly.
- Capacity note (descriptive): distribution of entry dollar volume for FAMILY hits;
  share below $25k/day (a ~5-clip strains it); median entry premium.

## Compute + hygiene

One polars pass over ~1.34M rows + masks + 100 draws: minutes. Queued anyway
(`--db light --cpu 4`, explicit Python311 path -- never `py -3.11` in the queue).
ASCII stdout; fill_nan->null once; seeds pinned; outputs
`B:\polygon_derived\weekly_5dte_movers\ev\` + RESULTS.md here. Builder: Sonnet, from
BUILD_BRIEF_EV.md; orchestrator audits predicates/pricing/control before the full run.

## Amendments

- **2026-08-18a (pre-full-run, orchestrator-approved):** the rule thresholds in this
  file are the parent's ROUNDED DISPLAY labels; the discovered predicates filtered on
  unrounded P80 quantiles (e.g. moneyness P80 = 0.03958416633346662). "Pinned exactly
  as discovered" therefore means the full-precision constants, which the builder
  recovered and hardcoded; the exact-match fidelity self-test vs parent E3 counts is
  the arbiter (passes only with the recovered floats; rounded labels undercount n by
  40-90 rows/rule). Premium-weighted mean r = entry_close-weighted (confirmed intent).

## Falsification

Failure looks like: control draws straddle the FAMILY EV (exposure explains it), or
EV < 0 after costs at every TP level. That outcome moves W5DTE to NEW_LEADS null-traps
with the touch-lift preserved as descriptive knowledge -- an acceptable, publishable
end. Evaluated immediately after the queued run; no peeking mid-build.
