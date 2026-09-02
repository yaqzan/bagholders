# IV / Skew vol-path probe — SKEW IS A REAL LEAD (2026-06-03)

**Status: the first genuine new-alpha lead of the session. PROMISING, not ship-ready** (1.25y options
data, magnitude regime-concentrated). Point-in-time (same-day option chain), holdout-respecting
(≤2026-05-15). N=2,018 in-window 70+ call signals with actual forward option P&L. Artifacts:
`iv_w1_results.txt`, `skew_verify_results.txt`.

## Why this probe is different
The barrier WR (opt15/apex15) is REALIZED-vol-normalized — it's blind to the PREMIUM (implied vol) you
pay. So options-implied signals live in a place the prior 4 nulls couldn't test. Outcome here = the
ACTUAL forward option P&L from `option_prices` (reconstructed Apex trade TP+30/SL−70/HOLD-15), not a
barrier touch.

## Results
**iv_rv (variance risk premium): NULL — sign-flips across regimes.** Cheap-options→better overall (t≈−1.9)
but pre-selloff t=+2.44 vs post t=−2.72 (opposite signs). Regime artifact. Dead.

**atm_iv (IV level): HOLD-only, not actionable.** High-IV (high-vol) names have bigger uncapped HOLD-15
P&L (t=+3.29, convexity/fat tails) but the Apex TP30 cap erases it (t=−0.09). Known property, not a lead.

**SKEW = put_iv(10%-OTM) − call_iv(10%-OTM): REAL.** High put-skew → better call outcomes, monotone:
win-rate 39%→46%→49%→49%→**53%** across skew quintiles; mean Apex P&L −28%→−14%. Full-sample t=+4.04.
- **Sign-STABLE across every period & quarter** (the test iv_rv failed): pre +1.15, post +4.53; Q2'25
  +0.05, Q3 +0.63, Q4 +3.78, 2026Q1 +1.74 — magnitude varies but never flips negative.
- **Orthogonal to recent return** (adds within r20 down-tercile t=+3.87 & up-tercile t=+3.23; corr=−0.16)
  → not an oversold-bounce proxy.
- **Orthogonal to `overall`** (adds within 70-74 t=+3.63 and 75+ t=+2.48).
- **Survives concentration** (drop top-5 syms UAMY/HWM/ASTS/WULF/RKLB → t=+4.24).
- Win-rate monotonicity is robust to the crude P&L reconstruction (doesn't depend on it).

## Economic mechanism (sound, timeless)
Skew is the call's price RELATIVE to the put. LOW/negative skew (call_iv ≥ put_iv = "call skew") = a
squeeze/meme/euphoria state where everyone's bidding the CALLS — you overpay for a crowded call and it
reverts (Apex −28%, win 39%). HIGH put-skew = normal/elevated crash-insurance demand, calls relatively
CHEAP and un-crowded (Apex −14%, win 53%). I.e. **avoid buying calls that are expensive relative to puts
(call-euphoria); favor calls that are cheap relative to puts.** Genuinely orthogonal to the
price-technical score, which has no options-implied input. Point-in-time + live-computable from the
current chain.

## Deepening (2026-06-03) — meme-unwind worry dead + mechanism CONFIRMED symmetric
**Sector/mcap (`deepen_sector.py`): NOT a spec-small-cap/meme artifact.** Skew→Apex P&L is strongest in
mid/large caps (≥10B t=+3.32; small 2-10B t=+3.04; mid t=+2.63; large t=+2.05; micro <2B is noise N=27),
present same-sign in 6/8 sectors (tech/industrials/financials/consumer-cyclical/materials/energy),
and robust to spec (raw diff t=+4.04, ATM-normalized skew/atm_iv t=+3.88).

**Put-side symmetry (`build_put.py`/`put_analyze.py`, N=5,820): "BUY THE CHEAP SIDE" CONFIRMED.**
Calls: HIGH put-skew (call cheap vs put) → better calls (t=+4.04). Puts: LOW skew (put cheap vs call)
→ better puts, **t=−4.62**, same sign in BOTH periods (pre −5.02, post −3.47), win-rate 38%(loSkew) vs
30%(hiSkew). The OPPOSITE sign on the two sides proves it's relative-value cheapness, NOT directional
sentiment (which would give same-sign) and NOT call-specific. **The skew measures which leg of the chain
is crowded/expensive; trading the cheap leg wins on both sides.** Textbook skew/variance relative-value,
orthogonal to the price-technical score (no options input), point-in-time, live-computable.

## Validation (2026-06-04): decomposition, premium-aware sim, proxy reconciliation
**Decomposition (`skew_decompose.py`): the edge is MOSTLY PREMIUM.** skew→ACTUAL option P&L t=+4.04,
but skew→UNDERLYING barrier (opt15/apex15, premium-blind) is only z=+1.83/+1.52 (weak). So the standard
realized-vol-premium MC is structurally BLIND to most of the edge — a full-history standard-MC gate
would only test the weak directional residual.

**Premium-aware portfolio sim (`premium_sim.py`, 1.3y actual option P&L): per-trade real, portfolio
MODEST.** The book is NET-NEGATIVE either way on this selloff-heavy window (70+ baseline −28.7%).
Filtering bottom-50% skew improves per-trade −19.9%→−15.1% and win 47%→52% (the t=+4 holds), but only
lifts the book return +4.3pp — it makes a losing book less-losing, not a winner. 1.3y is too short/
regime-concentrated for a confident portfolio claim.

**Proxy reconciliation (`build_proxy.py`/`reconcile_proxy.py`, 10y): literal proxy weak, but SEMIVOL_R
is a real 10y cousin.** Price-skew proxies barely track option skew (corr ret_skew −0.08, semivol_r
+0.12). BUT **semivol_r (downside/upside realized-vol ratio) independently captures the SAME call-P&L
edge** (overlap t=+5.00, same sign as opt_skew) AND shows a small-but-significant directional signal on
the FULL 10y underlying barrier (semivol_r→opt15 z=+4.25 / →apex15 z=+11.57; +0.8/+1.9pp). It's
computable 10y, so — unlike pure option-skew — it CAN be validated full-history through the standard MC.
(ret_skew is the inverse: high ret_skew → worse calls, z=−12 on apex15.)

## Net state
Real option skew = genuine per-trade premium-aware edge, mechanism-confirmed (buy-the-cheap-side,
symmetric), but mostly premium (MC-blind), modest portfolio benefit on 1.3y, options-data-locked for
full-history. **semivol_r** = a 10y-computable price cousin that predicts both the option P&L (overlap)
and the underlying barrier (full 10y, small magnitude) — the bridge to a full-history Stage-3 test.
Next: Stage-3 MC of a semivol_r entry-filter/sizing knob on full history (tempered: +1.9pp apex15 is
modest and may not survive portfolio gates), + re-validate real skew as options coverage deepens.

## Caveats (why it's a LEAD, not a ship)
- **1.25y of options data** (coverage starts 2025-02-10); magnitude concentrated in 2025Q4 + the
  post-selloff recovery. The pre-selloff period is same-sign but insignificant (N=237).
- Risk it's partly a 2025 meme/momentum-unwind phenomenon — but the mechanism is timeless and the sign
  never flips, which is more than any prior probe achieved.
- The Apex P&L is a crude reconstruction (SL-first, single ATM contract, no dead-hold); the win-rate
  monotonicity is the cleaner evidence.

## Next step (proposed)
Stage-3 ENTRY-FILTER / SIZING validation (no score change, portfolio-stage — it's about option pricing,
not stock direction): when a 75+ call fires, downweight/skip if skew is very low (call-euphoria),
keep/upweight if normal-high. Validate via the MC engine on the covered window for DD/return, and
re-validate as options coverage deepens. Could also test a finer skew measure (25-delta risk-reversal)
and put-side symmetry.

## Artifacts
`recon_iv.py` · `build_iv.py` (IV/RV+skew+fwd option P&L, indexed lookups, IV-sanity+OI filters) ·
`iv_w1.py` + `iv_w1_results.txt` · `skew_verify.py` + `skew_verify_results.txt`.
Ledger: `.cache/iv_skew/iv_ledger.parquet`.
