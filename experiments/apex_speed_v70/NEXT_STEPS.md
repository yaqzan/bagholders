# Apex speed re-tune — expanded plan (user direction 2026-06-04 night)

User added: explore static/dynamic 100% exposure, **size-conditional** ("run 100%
in early-portfolio scenarios"), **regime-conditional** exposure, sector-ETF
alignment, signal-density / call-put-ratio; and **remove the 2026-05-15 holdout
cutoff** (DONE — CALIBRATION_CUTOFF_DATE=None, _holdout no-ops, verified).

## Mining outcome (extend_mine.py, 3.87M call trades)
- **Daily call-signal supply**: rare broad-breakout days (25-60 ≥75-signals) carry
  EV **+0.25** vs typical <25-supply +0.054 (z-30). Real "broad participation =
  high EV" signal but only 0.26% of trades (median daily ≥75-supply=1, p90=5;
  overflow 70-74 dominates volume). Too rare to be a primary lever; note as a
  minor regime confirmation.
- **Call/put ratio**: NOT a clean clue — non-monotonic (balanced 1-3 ratio best EV
  +0.090; extreme-bull >8 only +0.078; cpr 3-8 worst loser z+27). No mechanism.
- **Sector-ETF breadth**: source CSV `.cache/sector_etf_screen/sector_breadth_daily_2020plus.csv`
  ABSENT (SAW falls back to 50.0 → inert). Heavy prior NULL history (Priority #13).
  DEFERRED to a next-lead; not worth building the feature this run.

## Decision: two tracks, one combined sweep
- **Track A (SAFE, pure value):** exposure-cap + DD-soft-band re-tune (Phase B
  running). Ships as a strategy_config + portfolio_profiles.json Apex value change.
- **Track B (AMBITIOUS, user's explicit ask):** EXR = size+VIX-conditional CALL
  exposure cap. Run hot while small, throttle at scale, VIX-gate the hotness so a
  small book can't blow up into a crash. New env-gated mechanism (RXDD-style).

Phase B's cap=1.00 result decides whether Track B is even needed: if static-100%
is collapse-safe AND higher median → just ship a higher cap (pure value). If
static-100% collapses (likely on bear/COVID, where the small book runs 100% into
a crash) → EXR's VIX-gate is the fix.

## EXR design (apply to monte_carlo.py AFTER Phase B finishes — do NOT edit while it runs)
Module-level (mirror RXDD getattr/env pattern, default OFF => byte-identical):
```
EXR_ENABLED (0/off) ; EXR_HOT_CAP=1.00 ; EXR_SIZE_LO=4.0 ; EXR_SIZE_HI=40.0
EXR_VIX_FADE=22.0 ; EXR_VIX_W=8.0
def _exr_call_cap(base_cap, equity, vix):
    if not EXR_ENABLED or base_cap <= 0: return base_cap
    mult = equity / STARTING_CASH
    size_f = clip((EXR_SIZE_HI - mult)/(EXR_SIZE_HI - EXR_SIZE_LO), 0, 1)   # 1 small -> 0 large
    vix_f  = 1.0 if vix is None else clip((EXR_VIX_FADE + EXR_VIX_W - vix)/EXR_VIX_W, 0, 1)  # 1 calm -> 0 panic
    return base_cap + max(0.0, (EXR_HOT_CAP - base_cap) * size_f * vix_f)
```
Integration (run_single_sim, per-day, portfolio_value@1836 + rxdd_vix_today@1877
already available BEFORE _premium_cap_remaining def@1884):
  - before the closure def: `_eff_gross_cap = _exr_call_cap(GROSS_PREMIUM_CAP, portfolio_value, rxdd_vix_today)`,
    `_eff_call_cap = _exr_call_cap(CALL_PREMIUM_CAP, portfolio_value, rxdd_vix_today)`
  - inside `_premium_cap_remaining`: use `_eff_gross_cap`/`_eff_call_cap` instead of the module consts.
  - SIZE_LO=4.0 => full hot cap for the entire 50k->200k ($200k=4x) journey; throttles 4x->40x.
driver.ENV_MAP += EXR_ENABLED/HOT_CAP/SIZE_LO/SIZE_HI/VIX_FADE/VIX_W.
Verify: OFF reproduces baseline byte-identical; ON raises 2024 median (hot small book) AND keeps
2020_crash collapse=0 (VIX-gate fades hot at COVID VIX>>22).

## Phase C (after EXR built): N=300x8 incl COVID
candidates = top-2 cap/DD-band (Phase B) + EXR LHS (~6) + a couple combined.
Rank: collapse=0 ALL windows incl 2020-COVID (HARD floor) -> max mean log-compound
on speed windows. DD reported, not constrained.

## Ship/stage
- pure-value cap/DD-band winner -> SHIP (edit STRATEGY_30DTE + portfolio_profiles.json apex, mirror;
  drift-guard; temporal-refresh --profiles all; research-pack rebuild).
- EXR winner -> SHIP if 13-consumer wiring + validation fits before 09:30, else STAGE (env-OFF) + SHIP_HANDOFF.
- Docs: known-issues + version-history + MEMORY + NEW_LEADS; mark holdout removed; self-update SKILL.

## NEXT EXPERIMENT (user 2026-06-05): trailing-stop revisit under honest v70
Hypothesis: the trailing-stop kill was under OLD high-hydration (every TP'd dollar
recycled into a waiting 75+ signal -> velocity was the alpha). Honest v70 cut 75+
supply ~27%; the 89% hydration is mostly 70-74 overflow -> when a 75+/85+ winner
exits there's usually no 75+ signal waiting -> low recycle opportunity cost ->
trailing the HIGH-CONVICTION winners (let monster runs run) may finally beat
immediate-TP. This is a null-with-NEW-condition (justified retry).
Design: env-gated trailing exit in monte_carlo (path-dependent: track peak option
value post-entry, exit on give-back X% from peak; activate at/after +TP). Variants:
trail-all vs trail-75+/85+-only + immediate-TP on overflow. Measure compound + DD +
collapse + realized hydration/avg-open-calls. Engine note: current model is
first-barrier-touch (barrier_outcomes cache) -> needs a per-position path walk
(resurrect the old monte_carlo_trail.py pattern or rebuild over option_pnl path).
