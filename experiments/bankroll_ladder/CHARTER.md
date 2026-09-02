# Replenishing-Bankroll Ladder ($2k sleeve) — EXPLORATION CHARTER

**Date:** 2026-07-18 · **Author:** FABLE (architect) · **Status: CHARTER — groundwork only.**
No sweeps ran; the sweep program is specced for the 9950X3D (§6). User-originated objective
(2026-07-18): a $2k/month replenishing allowance, total loss acceptable (it replenishes),
stage-conditioned strategy that changes as equity grows, arbitrary starting checkpoints
2k→20k→50k→100k explicitly open to empirical revision.

---

## 1. The objective function (new — this is NOT the main book's objective)

State: equity E_t plus a contribution stream +$2,000 at each month boundary (pause rule at
high E per §5). Ruin is NON-ABSORBING: E→~0 costs one month of waiting, not the program.

**Primary objective per stage:** minimize E[time to next checkpoint], reported with
P(checkpoint within T) curves. **The null model is the savings stream:** $2k/mo reaches $20k
in 10 months with zero trading. A stage config is FUNDABLE only if it beats the savings null
with margin: median time-to-checkpoint ≤ 0.7 × null time AND P(beating the null) ≥ 60%.
(Set pre-peek; amendable via §7 before any sweep runs. A strategy that merely matches savings
is a costly hobby; one that delays it is worse than nothing.)

**Secondary/diagnostic:** distribution of months-burned (tranches lost), max
contributions-at-risk per year (structurally capped at $24k), and time-in-stage histograms.
DD is a DIAGNOSTIC here, not the primary — this inverts the main book deliberately (§3).

## 2. The $2k physics — what actually binds (why this is "a different strategy entirely")

1. **Integer contracts.** At $2k, a 25% position = $500. ATM 30-DTE premium ≈ 1.82·σ_d·S:
   a $100/2%-σ name ≈ $364/contract; $300 name ≈ $1,100. Fractional sizing is meaningless;
   position sizes are {1, 2, ...} contracts and MOST of the universe is unbuyable per-slot.
   The existing MC sizes fractionally — integer granularity is a DOCUMENTED unwired gap
   (monte-carlo.md "Integer contract sizing"). **The core new machinery is an
   integer-contract, affordability-aware, contribution-stream MC mode** (§4).
2. **Affordability defines the universe.** The tradable set at $2k = signals whose selected
   contract premium ≤ slot budget. Levers that cheapen a contract: lower-priced underlyings,
   shorter DTE (premium ~ √DTE), slightly-OTM strikes. Each lever changes the P&L physics
   (gamma/theta/winrate) — they are the SWEEP AXES, not free choices.
3. **Microstructure worsens at low premium.** A $0.05 spread on a $0.40 contract is 12.5%;
   per-contract fees (~$0.65/leg) are 0.15-0.3% of a $500 slot per round trip. The asymmetric
   -cost canon needs a harsher low-premium variant: model minimum spread FLOORS in dollars,
   not percent (§4.3).
4. **PDT/ops:** under $25k the account faces pattern-day-trader limits — the book holds
   days-to-weeks (not day-trades), so mostly inert, but same-day exit-after-entry must be
   rare in the policy; note for live ops, not for the sim.

## 3. Doctrine re-scoping — carried over vs re-scoped vs re-opened (governance, read carefully)

**Carried over unchanged:** evidence honesty (PIT, no look-ahead, full-universe, day-of-week
splits), N floors + paired seeds + staged ladder, queue discipline, holdout lock for anything
touching SCORING (this sleeve consumes v74 signals as-is — **no scoring changes ride on this
program**), the null-check discipline, FINDINGS house style.

**Re-scoped (objective-level, deliberate):** collapse=0 was derived for irreplaceable held
capital; here P(tranche loss) is a PRICED INPUT (a lost month), so stage configs carry an
explicit tranche-ruin budget instead of a zero floor. DD-primary → time-to-checkpoint-primary
(DD demoted to diagnostic). Compound remains sanity-only.

**Legitimately re-opened under the NEW objective (this is not bar-shopping — the closed
verdicts answered a DIFFERENT question; each re-open cites its original scope):**
- 15-DTE (and shorter): closed as "crash-ruin for the held collapse=0 book / router
  mandatory." Under non-absorbing ruin + affordability pressure, short DTE re-enters the
  design space on its own terms.
- Tight SL / capital-velocity settings tuned for $50k+ fractional books: the SL sweep's
  same-stock-recycling artifact and bar-1-noise findings still apply MECHANICALLY (forward
  them as traps), but the optimum can sit elsewhere when slots are integer and premium is
  small.
- OTM strikes (delta ~0.25-0.35): never swept for the main book (ATM-only doctrine); at $2k
  affordability makes OTM a first-class axis. The gamma-curve work (experiments/
  gamma_curve_calibration) directly feeds pricing fidelity here.
**NOT re-opened (their nulls are objective-independent):** entry timing (next-open worse —
mechanical), look-ahead patterns, puts-funding (signal-side null), calendar/seasonal axes,
score re-shaping (Stage-1 closed axes stay closed — this sleeve does not touch scoring).

## 4. Machinery to build (engine spec — new-box implementation)

4.1 **`LADDER_MODE` MC extension** (new runner over the existing outcome-precompute layer;
    do NOT fork monte_carlo.py's physics): integer contract counts; per-slot budget =
    stage-config fraction × E_t; skip-if-unaffordable with cascade-down to the next
    affordable signal; monthly +$2k contribution events; stage-switch policy E_t → config;
    per-contract fees; dollar spread floors (low-premium microstructure §2.3); tranche-ruin
    accounting (a "month burned" counter). Seeds/windows/paired-arm discipline identical to
    house MC.
4.2 **Affordability inputs:** entry premium per signal from the REAL panel where available
    (2025-02+; the real_priced_replay ledger already extracts exactly this) and the model
    premium (1.82·σ·√(DTE/30)-scaled) for pre-2025 windows — with the §2.3 floors. The
    real-vs-model premium ratio tables from real_priced_replay calibrate the historical
    model premiums' realism at the cheap end.
4.3 **Cheap-end execution model:** spread_dollars = max(PCT_SPREAD × premium, FLOOR_$);
    FLOOR_$ swept in {0.03, 0.05, 0.10}; fees $0.65/contract/leg. Pessimism arms mandatory
    (the cheap end is where fill fantasy lives).
4.4 **Affordable-supply study — FIRST READ DONE 2026-07-18** (from the real_priced_replay
    ledger, N=785 matched 75+ signals, ATM ~30-DTE, real prints; early-2025 months
    undercounted by panel coverage): contract cost median **$535** (p25 $230 / p75 $1,105);
    affordable fraction at slot budgets — **$250: 28%, $500: 49%, $1000: 71%** (80+ subsets
    60/95/128; liquid subsets 78/127/164). Monthly affordable@$500 flow ≈ 3-54 (recent
    well-covered months: ~50/mo ≈ 2.5/trading day). **Feasibility verdict: stage-1 is NOT
    supply-starved at ATM 30-DTE — ~half the signal flow is affordable at a $500 slot before
    any shorter-DTE/OTM cheapening.** Remaining for the full study (new box): the DTE {7,15}
    and OTM cheapening curves + per-underlying-price-bucket splits.

## 5. Ladder structure (hypotheses, to be placed empirically)

- **Stage boundaries = where the physics changes**, not round numbers: (a) ~$6-10k — 2-3
  contracts per slot feasible, diversification begins; (b) ~$20-30k — integer effects fade,
  fractional approximation becomes valid, PDT ceiling crossed at $25k; (c) ~$50k+ — the
  EXISTING evidence base (Core/Apex profiles) becomes the strategy; **the ladder terminates
  by HANDING OFF to the main book, it does not reinvent it.** The user's 20k/50k/100k are
  priors; sweeps place the real boundaries (checkpoint = equity where the optimal config
  changes materially).
- **Stage-1 shape (hypothesis, not a result):** few slots (1-3), cheap contracts
  (affordability-filtered 75+/80+ signals), aggressive per-slot fraction, explicit
  tranche-ruin budget, stop-at-checkpoint discipline (the Apex-sprint stop-at-2x precedent
  generalizes: P(2x in 2y)=70.5% median ~129d at 25%×4 — the sprint IS a one-stage ladder).
- **Contribution policy:** +$2k joins the bankroll while E < active-stage ceiling; above it,
  contributions PAUSE (external savings) — prevents the sim from crediting brute savings as
  strategy skill; sweepable variant: contributions always-on but the savings null uses the
  same rule (fairness symmetry, pinned before sweeps).

## 6. Sweep program (9950X3D; every job via queue; staged B=300→C=500 screens per the
   ratified ladder; paired seeds; savings-null arm computed analytically alongside)

S1 affordable-supply study (this box, free, §4.4) → S2 LADDER_MODE engine build + validation
arm (replicate a degenerate config against standard MC to prove the harness) → S3 stage-1
grid: DTE {7,15,30} × strike {ATM, 25-35Δ OTM} × slots {1,2,3} × per-slot {33%, 50%, 100%} ×
TP {+30, +50, +100%} × SL {−50, −70, dead-hold-analog} × ruin-budget observed — judged on §1
primary vs savings null, windows incl 2020_crash + 2022 (ruin pricing needs the ugly tapes)
→ S4 stage-boundary placement (config-flip map over E grid) → S5 full-ladder lifecycle sim
(2k start, 36-month horizon, stage-switch policy, P(reach 100k) / median months) → S6
pessimism certs on the winner (spread floors, fill-miss, real-premium substitution 2025+).

## 7. Amendment + status

Bars in §1 set pre-peek 2026-07-18; amendable by FABLE-tier/user sign-off logged here BEFORE
any S3 sweep runs. This charter is groundwork — nothing here changes the live book, the
profiles, or any frozen prereg. First concrete actions: §4.4 supply study (chained on this
box) + S2 engine spec review at ratification.

**Amendment log:** (empty)
