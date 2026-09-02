# Dead-hold cut-to-redeploy investigation (v70 Apex) — /research 2026-06-06

**User's hypothesis:** "There has to be an edge where I'm holding a position down 60%
and instead of buying the 88 on the board, I'm forced to hold until 70% [SL] or
15 fasts [day-15]. Just seems counterintuitive."

In engine terms this is **cut-to-redeploy / reallocation**: when the book is at-cap
and a fresh higher-conviction signal arrives, displace a deep-underwater open position
to fund it. The lowest-current-pnl variant (`REALLOC_STRATEGY=current_pnl_low`) is
EXACTLY this, and is the **documented v32 Stage-3 NULL**. Re-tested here on the new
substrate (honest v70 + asymmetric cost + dead-hold −40/−15 + RXDD/SVR/MWDD + uncapped
Apex), per the "new condition justifies a retry" rule — AND extended with a genuinely
novel, NOT-previously-tested **regime-conditional** version (CDR).

## What the dead-hold actually is (so the framing is right)
The dead-hold does NOT "force you to hold to −70%." When a call's option pnl hits the
SL trigger (≤ −40%), instead of realizing the −70% SL it HOLDS for either a −15%
intraday popout (`dh_pop`) or day-15 (`dh_expiry`). It is the mechanism that *avoids*
the −70% realization. Documented: disabling it (`dh_off`, clean −70% SL) = **100%
collapse**; it is collapse-PREVENTING (deferring correlated crash losses).

## Mine 1 — dead-hold cohort economics (fresh 5y tape, 1.33M calls, 300 seeds)

Per-outcome (calls):
| outcome | share | mean pnl | note |
|---|---|---|---|
| tp | 71.4% | +0.322 | the compounding engine (holds ~4.4 cal days) |
| **dh_expiry** | **15.3%** | **−0.919** | deep-dying bags — the user's "dead weight" |
| **dh_pop** | **10.2%** | **−0.112** | bags down ≥40% that RECOVERED to ~−15% |
| hard | 2.8% | −0.513 | day-15 hard sell |

**The dead-hold cohort (bags down ≥40%) = 25.6% of trades: 40% recover (dh_pop), 60%
expire deep (dh_expiry).** So the user is right that a lot of capital dies — **~19% of
the strategy's (premium × days-held) capital-time is in `dh_expiry`.**

BUT dead-held bags only hold ~7.6 cal days (expiry) / ~5.0 (pop) vs **4.4 for TPs** —
only ~3 extra days. The slot turns over almost as fast as a winner's. (`hard`/`dh_open`
are the only truly long holds, 21–22 days, and together are 2.9% of trades.)

## Mine 2 — the DISCRIMINATOR points AGAINST the user's instinct
Recovery rate (dh_pop / dead-hold) is NOT flat — it is highest exactly where you'd be
tempted to cut, and exactly where the 88s fire:
- **VIX at entry:** panic 28+ → pop **0.58**; calm 15–20 → **0.36** (lowest)
- **regime:** BULL → **0.54**; STRESS → 0.33
- **breadth:** oversold 25–40 → **0.51**; high 55+ → 0.34
- **score/tier:** 80+ → **0.57–0.61**; 70–79 → 0.38
- entry_dd: 20–35% → 0.49 (highest); concur: ~flat 0.39–0.41

**Cruel irony for the user's exact scenario:** "an 88 on the board" means high-conviction
signals are firing, which clusters in weak/oversold/panic tape — *exactly* when the bag
you'd cut is most likely a RECOVERER. Cutting it to chase the 88 is double jeopardy
(kill a recoverer AND the 88 is a falling knife in that tape).

The "safe-to-cut" expirer cohort DOES exist (low-score 70–79 + calm VIX 15–20 + high
breadth → pop ~0.36), which motivates the conditional mechanism — but even there 36%
recover, so selective cutting is imprecise.

## Why per-position "CUT wins" is an illusion
Residual-stake math at the −40% trigger says CUT (realize 0.585, redeploy into +0.148
top-tier) beats HOLD (cohort mean ends −0.60, worse than the −0.40 trigger). But that
ignores:
1. **Realistic redeploy EV ≈ +0.05** (overflow/low), not +0.148 — an 88 is the rare case.
2. **The slot frees naturally in ~3 extra days anyway** — cutting doesn't create a net
   new position over the horizon; it just realizes the loser ~0.20 worse (−0.40 cut vs
   natural −0.15 pop on the 40% that recover) for a few-bars-earlier redeploy.
3. **Collapse/path-dependence:** in a crash all bags hit ≤−40% together; cutting realizes
   correlated losses + redeploys into falling knives → ruin (the `dh_off`=100%-collapse fact).

## Mechanism implemented (env-gated, default-OFF, baseline byte-identical)
`monte_carlo.py` `_try_realloc` extended with CDR gates (all default-off ⇒ no-op):
`REALLOC_MIN_LOSS` (only cut bags down ≥X), `REALLOC_MAX_VIX` (calm-tape only),
`REALLOC_MAX_DD` (shallow-DD only), `REALLOC_MAX_TARGET_SCORE` (low-conviction only),
`REALLOC_CUT_SLIP` (honest forced-exit half-spread). CDR = `current_pnl_low` + these.

**Verify:** compiles; CDR runs clean (VIX/DD in realloc scope). On the **2022 bear** CDR
fired ZERO times (byte-identical to baseline — calm/DD gates correctly block cutting in
crash tape → no collapse risk introduced). On **2024 calm bull** CDR fires (numbers
change) but N=12 compound came in LOWER (−5% med). CDR is therefore a **calm-year compound
play, not a DD play** (it's gated OFF in crashes by design) — needs proper-N to settle.

## Tests in flight (queued)
- **#71** blunt `current_pnl_low` A/B (the user's literal idea), N=150 × 7 windows incl
  2020_crash (collapse test) — reproduces/refutes the documented v32 null on new substrate.
- **#72** CDR Phase B, N=100 × 5 windows (2020_crash/2021/2024/dip/5y), 11 gate configs +
  2 ablations (no-VIX-gate, no-DD-gate).

## RESULT 1 — blunt cut (user's literal idea) is a re-confirmed null (#71, N=150 × 7 win)
`current_pnl_low`, advantage gate ∈ {5,10,15}. Monotonic harm in how often it fires:

| MIN_ADV | 5y compound vs base | DD | collapse |
|---|---|---|---|
| ≥5 (fires most)  | **−29%** | dip +2.3pp, 22-now +2.1pp (worse) | 0% |
| ≥10              | **−16%** | ~flat | 0% |
| ≥15 (fires rarely)| **−8%** | ~flat | 0% |

No benefit anywhere; least-harmful config is the one that barely cuts. Reproduces the
v32 Stage-3 null. **New-substrate update:** on honest v70 it does NOT collapse (0% incl
2020-COVID — milder than `dh_off`), because REALLOC marks the cut at model price (not the
−70% SL) and few fresh signals clear the advantage gate in a crash. It just bleeds compound.

## RESULT 2 — CDR (regime-gated) is a comprehensive null (#72 Phase B, N=100, 11 configs)
Full gate-space sweep, all collapse-safe (0% every window incl 2020-COVID), all DD flat
(d5yDD ∈ [−0.1,−0.4]pp = noise), and **ALL lose integrated 5y compound:**

| config (what it cuts) | 5y compound Δ | dip DD | note |
|---|---|---|---|
| abl_novix (deep, no VIX gate) | −0.6% | +1.3pp | closest to baseline = "does nothing" |
| deep65 (only ≥65%-down) | −3% | +2.7pp | steel-man |
| tight / mid / deep55 / score84 / abl_nodd | −6 to −7% | +2.7pp | |
| calm_strict (ultra-calm only) | −10% | +2.7pp | |
| loose (aggressive) | −15% | +2.3pp | |
| adv5 (fires most) | −26% | +1.2pp | worst |

The best config is compound-better in only **2 of 5** windows (worse in 3). Ablations:
removing the VIX gate (abl_novix) or DD gate (abl_nodd) doesn't help — the gates only
make it collapse-SAFE, never beneficial. Most configs WORSEN the dip drawdown. There is
no Pareto point: nothing cuts DD, nothing lifts compound. **#73 = N=300 confirmation of
the two least-bad configs (airtight check).**

## RESULT 3 — N=300 then N=500 confirmation: the "gains" were seed-noise (#73, #74)
Steel-man configs (deep65 = only cut ≥65%-down low-conv bags in calm/shallow-DD;
abl_novix = deep, no VIX gate), 5y compound vs baseline across iteration count:

| config | N=100 | N=300 | N=500 | 5y DD (N=500) | dip DD (N=500) |
|---|---|---|---|---|---|
| deep65    | −3% | +3.1% | **−1.0%** | +0.0pp | **+4.5pp** |
| abl_novix | −0.6% | +10.0% | **+0.7%** | +0.0pp | **+4.5pp** |

The compound delta **sign-flipped across N and collapsed to ~0 at N=500** — the
definitive signature of a noise-dominated metric (documented MC noise floor 1.6–1.8×).
The reliable metric (DD) NEVER improves and the **dip drawdown is +4.5pp WORSE**.
Collapse 0% on every window incl 2020-COVID. Even the most conservative cut is mildly
HARMFUL. No N=500 config is a Pareto point; there is nothing to ship.

## VERDICT — NULL (re-confirmed on honest-v70, N=500). NOT shipped.
Cut-to-redeploy / reallocation in EVERY form — blunt (`current_pnl_low`) and the novel
regime-gated CDR across the full gate space — does not help on the honest-v70 Apex
substrate. No DD benefit (its only plausible target, crash-DD, is where cutting is
correctly disabled — enabling it there = the documented `dh_off` 100%-collapse), and no
compound benefit (slight erosion). The user's intuition is understandable (~19% of
capital-time dies in deep bags) but cutting those bags can't beat holding them because:
(1) 40% recover and the decision-time discriminator can't cleanly separate them — and
recoverers cluster exactly where an "88" fires (panic/oversold/high-conviction tape);
(2) the slot frees itself in ~3 extra days anyway (dead bags hold 7.6d vs 4.4d for TPs),
so cutting buys almost no extra turnover; (3) cutting realizes the loser worse (−0.40+)
than its natural resolution and forfeits the recoveries; (4) the dead-hold's −15% popout
on 40% of deep bags + crash-collapse-prevention is worth more than recycling their residual.

## Prior (strong) — re-confirmed null
Weight of evidence: documented v32 REALLOC null + dh_off=100%-collapse + capital-velocity
law (HOLD≫CUT) + the discriminator (recoverers cluster where 88s fire) + slot-turnover
(~3 extra days) + N=12 calm-2024 compound drop. The MC sweep is the arbiter.
