# v74-lean cascade retune — VALIDATE-c04, NO SHIP (2026-06-18, /research)

**Question:** the v74 LEAN ship (2026-06-15) retired the post-`pre_boost` score
tail → ≥75 supply dropped ~38% (v73 ReSim ~4.6 sig/day → v74 PRF 2.8/day), but
the live cascade is still the **v73-density-tuned c04** (ultra .20/top .15/mid
.08/low .03), applied to a 38%-sparser substrate with no v74 re-validation. The
PRF instrument (`derived_portfolio.json`) flagged a divergence — F proposes
`mid .10 / low .051` (size up). This is the documented "re-tune on the new
substrate" follow-up (v71→c14, v73→c04 reliable-Pareto pattern). Does v74's lower
supply warrant a retune?

## Verdict: NO. c04 is confirmed DD-optimal on the v74-lean substrate.

Every size-up direction (PRF's mid/low up, quality-concentration ultra/top up,
cap-raise, +MaxPos, uniform) **fails the DD-primary Stage-3 gate (T4: 5y WorstDD
≤ baseline +1.0pp)**. collapse=0 + COVID-safe on every candidate × window.

### Phase B (N=100×6) — NO Pareto; size-up DD-worse, monotonic in magnitude
ddFocusD (mean 5y+22-now WorstDD reduction vs c_base; all ≤0 = all DD-worse):

| cand | ddFocusD | med5y/base | note |
|---|---|---|---|
| **c_base (c04)** | +0.00 | 1.00 | the DD floor |
| c06_qual_u22_t17 | −1.95 | 1.01 | concentrate into rare ultra/top |
| c04_mid10 | −2.30 | 1.03 | mid .08→.10 |
| c00_prf (F's pick) | −6.75 | 0.88 | mid .10/low .051 — DD-worst + compound-worst |
| c09_prf_cap60 | −6.35 | 1.11 | size-up + cap60: +11% compound, +6.5pp DD (the velocity↔DD frontier) |

### Phase C (N=300×8) + Phase D (N=500×8 gate) — DD penalty is REAL, not noise
The two compound-gainers, tracked across N (the penalty CONVERGES, doesn't dissolve):

| cand | N=100 ddFocus | N=300 5y DD Δ | **N=500 5y DD Δ (gate)** | compound | T4? |
|---|---|---|---|---|---|
| c04_mid10 | −2.30 | +1.9pp | **+1.7pp** | +2.2% | ❌ FAIL |
| c06_qual | −1.95 | +1.4pp | **+1.2pp** | +0.1% | ❌ FAIL |

c04_mid10's 5y DD penalty: +2.7 → +1.9 → +1.7pp across N=100/300/500 — a **real
regression that converges**, not noise. The +2.2% compound doesn't justify +1.7pp
DD, and it fails T4 regardless. c06's compound gain is ~zero. collapse=0 / ddCrash
~flat (68.3) everywhere.

## Why (the mechanism)
Despite v74's −38% supply, **recycle_coverage stays 0.975** (the book is still
saturated — at MaxPos 14 + ~2.8 sig/day the 14 slots fill and hold). So the
supply drop did NOT under-deploy the book; sizing up just makes each slug bigger
under the **50% gross cap** → fewer-but-bigger concentrated positions → **G16
over-deployment** (more correlated DD, no compound recapture). The v73 c04
selectivity trim is already the cap-bound optimum.

## Lesson — PRF "size-up" extrapolation falsified by the MC
The PRF coefficients are fit on the same-engine **v70/v71** pair only (documented
caveat). On the v74-lean supply regime F extrapolated to "size up," but the MC
disposed against it: at high coverage + a hard gross cap, the supply-drop signal
F keys on does NOT mean "deploy more" — the book is already full. **A PRF
divergence flagging size-up at coverage≈0.97 + a binding gross cap is the G16
over-deployment trap; validate the direction before trusting F's extrapolation
to a new substrate.** (PRF remains a useful seed for DENSER substrates where the
book is idle — v70's 0.66 coverage — not for saturated ones.)

## Status
NO SHIP. c04 validated on v74-lean — the open "should the v74 cascade be
retuned?" question is CLOSED (it should not). Reusable harness: `sweep.py`
(driver = v69_portfolio_retune/driver.py, paired seeds, TIER_*_OV ENV_MAP);
results in `.cache/v74_cascade_retune/phase_{B,C,D}.jsonl`.
