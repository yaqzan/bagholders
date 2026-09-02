# VXMD — VIX Weekly-MACD momentum DD lever (v70 Apex, 2026-06-09 /research)

**User hypothesis:** VIX is under-explored as a *dynamic* signal — "not just a static VIX
reading that affects scores, but a moving velocity/acceleration reading," with the **weekly
MACD crossover** specifically flagged. Motivated by "VIX spikes and all my calls collapse."

**Verdict (so far — mine decisive, MC screen confirming): NULL.** The honest, point-in-time
VIX-weekly-MACD-momentum cohort is DD-concentrated but **HIGH-EV** and a **crash artifact**.
The strong-looking signal in a naive resample was **within-week look-ahead**. The one robust
VIX DD signal — the *level* band — is already shipped (RXDD, 2026-06-04). VXMD is built
env-gated OFF as reversible null-infra (the DQT/EXR/CDR pattern).

---

## Context: what's already shipped, and what's genuinely untested

| lever | VIX signal | status |
|---|---|---|
| **RXDD** | VIX **LEVEL** band (20-28 'slow-bleed') CALL-alloc dampener | **shipped 2026-06-04** |
| G22 / G23 | VIX daily **velocity** (vv5) | **dead** (anti-aligned; rising VIX = high-EV bounce) |
| — | VIX **WEEKLY MACD** histogram / crossover; VIX **acceleration** (2nd deriv) | the user's framing — tested here |

The user's framings (weekly MACD, acceleration) are a *different operationalization* of the
same VIX-momentum axis. The decisive question: does any VIX-momentum cohort show **low-EV AND
DD-concentrated AND survive the orthogonal slice** (VIX<20, where RXDD doesn't fire),
**robustly across windows** (not crash-only)? The tape already has RXDD+SVR+MWDD live, so any
residual VIX-momentum signal is orthogonal-to-RXDD-level **by construction**.

## Method

`mine.py` — offline cohort mine on the live v70 Apex full-lever MC trade-tape
(`.cache/dd_ledger/tape_*.parquet`, 6.9M call trades, RXDD+SVR+MWDD live). VIX momentum
features computed from `MarketRegime.vix_close` (daily, 2021-04+), joined on-or-before by
`entry_date`. Loser-rate lift/z + **dd_conc** (dd_share / n_share) + mean option pnl, full
tape + **DD-active subset** (entry_dd>=0.13, where the levers fire, per G21) + the **VIX<20
orthogonal slice**. Features:
- raw 5d/10d/20d velocity (`vv5`...), **acceleration** (`vacc` = d(vv5)/5d, 2nd deriv)
- **true-weekly MACD** (ISO-week resample, EMA12/26/9) — *naive, has within-week look-ahead*
- **last-completed-week** weekly MACD (point-in-time, no current-week) — `wquad_pit`
- **daily-equivalent weekly MACD** (EMA60-EMA130, signal EMA45 on daily VIX) — `de_hist`;
  **the engine-faithful ship feature** (recursive EMA = point-in-time, no look-ahead)
- weekly VIX RSI(14)

## Finding 1 — raw VIX velocity / acceleration is anti-aligned (confirms G22/G23)

`vacc_accel_hi` (VIX accelerating hard) is **HIGH-EV** (mpnl +0.10), not low-EV — these are
the mean-reversion *bounce* entries. "vix<20 AND accelerating" is mpnl-positive in **every**
window (+0.05..+0.11). Raw velocity/acceleration is the wrong operationalization (the user's
intuition that "rising VIX = contract" is backwards at the entry level — rising VIX entries
catch the bounce).

## Finding 2 — the true-weekly MACD "signal" is WITHIN-WEEK LOOK-AHEAD

A naive ISO-week resample (`dt.truncate("1w")` + `vix.last()`) maps a **Monday** signal to its
week's **Friday** whist → look-ahead. Under that, `weekly-MACD pos_rising` looked great:

| cohort (DD-active dd>=0.13) | conc | dd_share | mpnl |
|---|---:|---:|---:|
| true-weekly `whist` pos_rising **(LOOK-AHEAD)** | 2.11 | **0.73** | +0.034 (looks low-EV) |

But that's a near-tautology ("weeks where VIX rose by Friday → calls bought that week lost") —
not tradeable. This is the documented weekly look-ahead trap (`reference_weekly_recalc_lookahead`).

## Finding 3 — HONEST point-in-time weekly MACD: DD-concentrated but HIGH-EV + crash artifact

Both look-ahead-free formulations agree, and **flip the conclusion**:

| cohort (DD-active dd>=0.13) | conc | dd_share | **mpnl** |
|---|---:|---:|---:|
| **de_hist>0** (daily-equiv, engine ship feature) | 1.97 | **0.63** | **+0.102 (HIGH-EV)** |
| **wquad_pit pos_rising** (last-completed-week) | 1.75 | **0.67** | **+0.076 (HIGH-EV)** |

The "building vol-momentum" cohort holds **63-70% of all drawdown-dollars** — but it is
**HIGH-EV**: it's exactly where the dead-hold mean-reversion **runners** cluster (calls bought
as VIX momentum builds catch the subsequent bounce; the *losers* in it are deferred by the
dead-hold, the *winners* run huge). Contracting it would shrink the biggest winners.

**Per-window (point-in-time `de_hist>0`, vix<28, dd>=0.13 — where a lever would fire):**

| window | mpnl | read |
|---|---:|---|
| 2022 (bear) | **-0.020** | low-EV (would help) |
| 2023 | +0.051 | fine |
| 2024 (bull) | **+0.120** | HIGH-EV (would HURT) |
| 2025 | -0.031 | low-EV |
| (last-completed-week pos_rising) 2022 | **-0.175** | very low-EV |
| (last-completed-week pos_rising) 2024 | **+0.087** | HIGH-EV |

Textbook **crash artifact** (low-EV in bear, high-EV in bull) — the exact **DQT/G23 failure
mode**: the DD-gate fires in BOTH bear-2022 drawdowns (good to contract) AND bull-2024
drawdowns (bad to contract — cuts winners) and cannot separate them. Unlike RXDD's VIX-level
band (low-EV *across* windows = a genuine regime inefficiency), VIX *momentum* is a
velocity/mean-reversion axis whose low-EV is regime-conditional.

## Why this is structural, not a tuning failure

The collapse the user sees — open calls losing value on a VIX spike — is an **already-open
position** effect; **entry sizing can't fix it** (it only sizes *new* entries). That collapse
is handled by the **dead-hold** (collapse-PREVENTING — defers realization until recovery).
Meanwhile, *new* calls bought as VIX momentum rises are disproportionately the ones that catch
the mean-reversion bounce — high-EV. So "contract on rising VIX momentum" contracts the
WINNERS. The only robust VIX DD signal is the *level* band (RXDD 20-28), already shipped — and
VIX is ~19-21 right now, so RXDD is already contracting new entries.

## MC confirmatory screen (VXMD env-gated, live Apex stack)

VXMD = daily-equivalent weekly-MACD-hist (`de_hist`) smooth ramp contraction of CALL alloc,
DD-gated (dd>=DD_MIN), VIX-panic-excluded (>=28). Wired into `monte_carlo.py` env-gated OFF.

- **Verify (no-op + fires, #95):** PASS. OFF == BASE byte-identical (2022 dd58.3/med51.9,
  2024 dd45.2/med1500 — true no-op when the flag is off). ON (aggressive, dd_min=0) FIRES and
  **craters 2022 compound 51.9% → 3.5% (−93%)** while 2022 DD got slightly WORSE (+0.3pp), 2024
  compound −29% — the crash-artifact preview.

- **Screen (#96, N=100 × 8 windows incl 2020_crash, 6 DD-gated configs):** **NULL — no clean
  Pareto.** Baseline 5y DD 64.6 / med 2603%. Every config:

| config (depth/dd_min/whist_hi) | dd_5y | dd_2022 | comp | **comp_2024** | worstComp | coll |
|---|---:|---:|---:|---:|---:|---:|
| c00 0.3/0.10/0.8 | +0.6 | -0.6 | -0.089 | -0.122 | -0.248 | 0 |
| c01 0.5/0.10/0.5 | **+1.2** | -0.1 | **-0.180** | **-0.264** | -0.510 | 0 |
| c02 0.3/0.05/0.5 | +0.2 | -1.5 | -0.097 | -0.080 | -0.325 | 0 |
| c03 0.4/0.20/0.8 | **-0.8** | +0.3 | -0.064 | -0.045 | -0.231 | 0 |
| c04 0.3/0.10/0.4 | +0.4 | -0.3 | -0.078 | -0.095 | -0.312 | 0 |
| c05 0.5/0.15/0.8 | **+1.2** | +0.4 | -0.169 | -0.161 | -0.449 | 0 |

  - **CLEAN PARETO (coll=0, dd5y>+1.0, comp>=-0.02): NONE.** The two configs that cut 5y DD (+1.2pp,
    c01/c05) pay **−18% / −17% compound**; the cheapest config (c03, comp −0.064) **doesn't cut 5y
    DD** (−0.8 = worse). **comp_2024 is negative for ALL 6** (−0.045..−0.264) — contracting the
    building-momentum cohort cuts the bull-2024 winners. No DD-gate (incl. deep dd_min=0.20) separates
    the bear-low-EV from the bull-high-EV. Even 2022 DD is mostly *worse* (the contraction shrinks the
    position base unevenly). collapse=0 everywhere (VXMD only reduces alloc → cannot add collapse).

## VERDICT — NULL. VXMD staged env-gated OFF (reversible null-infra).

VIX *weekly-MACD momentum* (and acceleration), like VIX daily velocity (G22/G23), is **not a
Pareto DD lever** once implemented look-ahead-free: the rising-momentum cohort is where the
dead-hold mean-reversion **winners** cluster (high-EV), and contracting it cuts compound,
concentrated in bull years (crash-artifact). The look-ahead-inflated version was a non-tradeable
tautology. The only robust VIX DD signal is the **level** band — already shipped (RXDD 2026-06-04).
VXMD left env-gated OFF in `monte_carlo.py` (default OFF → baseline byte-identical, verify
NOOP=True) + `driver.py` ENV_MAP, as reversible null-infra (the DQT/EXR/CDR pattern). NOT wired into
strategy_config / backtest_cascade / registry / UI (it is not shipped). No `ALGORITHM_VERSION` bump,
no recalc, no production surface change.

**For the user's "VIX spikes → calls collapse":** that is an *open-position* effect; entry-sizing
levers (all a portfolio mechanism can do) cannot protect it — the **dead-hold** (collapse-preventing)
does. For *new* entries, the level-based **RXDD** (VIX 20-28) already contracts; with VIX ~20 today
it is already active (today's VIX weekly-MACD-hist is actually *negative* — the recent chop is
rolling over, not building — so a momentum lever would not even fire).

---

# Part 2 — the user's ACTUAL ask: a WR15 *scoring* improvement (VIX weekly readings → the post multiplier)

The user clarified they wanted a **WR15** improvement, not a DD lever: "apply different weekly readings
of the VIX (macd score / ema×macd) to the post multiplier and observe the effects at different blast
radius." This is a Stage-1 question on the **regime (post) multiplier** (`market_regime.py` → composite
→ multiplier → applied symmetrically around 50 in scoring). Read-only screen (no recalc):
`wr15_regime.py` (reuses `regime_ab_test` internals; barrier outcome per (sym,date) is
multiplier-invariant → walked ONCE, W1 + every variant are re-aggregation).

**Key structural fact:** the production composite **already uses VIX level + VIX 10d-velocity**
(`_vix_gradient` = level×0.55 + tanh(VIX_10d_change/15)×0.45). So the test is sharpened: does a smoother
*weekly-MACD* reading add WR15 signal **beyond** the level + 10d-velocity already there?

## W1 — qualifying-peak WR15 split by VIX weekly-MACD (5y v70, N=20,048 walked peaks) — FAILS

**CALL side (overall≥70):** rising/positive weekly-MACD is the **HIGHER**-WR15 state (wrong sign for a suppressor):

| cohort | WR15 | N |
|---|---:|---:|
| whist_pos_rising (building) | **68.6%** | 5,624 |
| whist_pos_falling | 67.4% | 2,619 |
| whist_neg | 62.1% | 7,015 |

**Controlling for VIX level** (the decisive orthogonality test — the multiplier already uses level):
- vix<20: whist≥0 63.9% vs whist<0 63.2% → **z=+0.75 (flat — no orthogonal signal)**
- vix20-28: whist≥0 71.4% vs whist<0 59.2% → z=+7.90 (rising is *better*)
- vix≥28: whist≥0 72.9% vs whist<0 42.9% (N=98) → z=+6.33

→ The unconditional "whist_neg is lower" is the **VIX-level confound**; *within* VIX bands the
weekly-MACD adds ~nothing for calls (vix<20 z≈0), and in elevated-VIX the rising-momentum state is
HIGHER-WR15. Suppressing calls during rising VIX momentum REMOVES higher-WR15 signals.

**PUT side (overall≤25):** a weak suppress-direction signal, below the W1 +3 bar:
- whist_pos_rising 65.6%/1048, pos_falling 61.3%/946 (z=−3.36), whist_neg 67.7%/2796.
- Controlling: vix<20 z=−1.19; vix20-28 whist≥0 61.0% vs whist<0 67.8% → **z=−2.87 (N=639)** — real-ish
  but band-specific and sub-threshold.

## Variant sweep — fold weekly-MACD into the composite as a suppressor, at blast radius {10,20,30} + rising-only

Per-bucket WR15 Δ vs production (B_current), re-bucketing the cached outcomes:

| bucket | J shift10 | K shift20 | L shift30 | M shift20 rising-only |
|---|---:|---:|---:|---:|
| 90+ | **−5.72** | −2.94 | −4.41 | −0.86 |
| 85-89 | −0.65 | +0.10 | −0.65 | −0.88 |
| 80-84 | +0.30 | −0.82 | −1.24 | −0.36 |
| 75-79 | +0.36 | +0.03 | +0.01 | +0.22 |
| 70-74 | −0.33 | −0.62 | −0.86 | −0.50 |
| <25 | +0.06 | +0.32 | **+0.53** | −0.29 |
| <15 | −0.30 | −0.95 | −1.05 | +0.25 |
| <5 | +0.05 | +0.04 | −1.54 | +0.84 |

**No blast radius produces a clean WR15 lift.** Calls get WORSE (suppressing removes higher-WR15
signals, esp 90+ −5.72pp); puts are a wash (<25 +0.5pp at most, but <15 −1pp); the dominant effect is
just cutting N (90+ N 68→34 at shift30).

## VERDICT (Part 2) — NULL. W1 fails.

VIX weekly-MACD folded into the regime/post multiplier does **not** improve WR15. Root cause: the
production multiplier **already captures the WR15-relevant VIX signal** (level + 10d-velocity); the
smoother weekly-MACD is largely redundant (orthogonal control z≈0 for calls), and for calls — the
WR15-dominant side — rising VIX momentum is a genuinely *higher*-WR15 state, so suppressing it hurts.
The one weak put-side signal (vix20-28 rising, z=−2.87) is sub-threshold and nets to a wash in the
variant sweep. No z≥3 orthogonal signal in a shippable direction → abandon (don't calibrate noise).
Other VIX-momentum readings ("ema×macd" etc.) are correlated derivatives of the same axis and would
behave the same. Consistent with the honest-frontier doctrine + the prior regime A/B nulls (F/G/H/I,
all level-based). Read-only — no recalc, no version bump, no production change. Harness:
`wr15_regime.py` / `wr15_regime_report.json`.

## Harness
- `mine.py` — VIX-momentum cohort mine (velocity/accel/weekly-MACD/de_hist + orthogonal slice
  + DD-active + per-window). `mine_report.json` / `mine_report_ddact.json`.
- `sweep.py` — VXMD verify + screen driver (mirrors `regime_dd_v70/sweep.py`).
