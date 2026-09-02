# Apex 50k→200k speed re-tune — diagnostic (2026-06-04)

Scope: the user asked to (1) read the **current VIX/regime** vs **v70 Apex
performance history**, (2) find a **sizing/allocation trend** to improve Apex
gains, and (3) optimize for the **current regime** to find the **fastest reliable
50k→200k (4×)** — explicitly relaxing DD (early-stage book) while keeping
collapse=0 the hard floor.

Substrate: live **v70 Apex, RXDD-on** (`ALGORITHM_VERSION=c70d16d22`). Mining tape
= `.cache/dd_ledger/tape_*.parquet` (2026-06-04, 6.45M call trades, post-overflow,
post-dead-hold-popout, RXDD-off — RXDD-off has *more* samples in the VIX 20-28 band,
better for EV mining; the structure is sizing-invariant).

---

## 1. Current market regime (2026-06-04)

| Signal | Value | Read |
|---|---|---|
| **VIX** | **15.40** (drifting down from ~18 mid-May) | calm / low-fear |
| **Regime composite / mult** | **52.2 / 0.94** | NEUTRAL band, mid (slightly cautious) |
| **Breadth** | **42** (choppy 33–60 over the month) | mid-low participation |

We sit in the **low-VIX (15–20) band** — *not* the RXDD slow-bleed band (20–28),
*not* panic (≥28). **RXDD is essentially dormant right now** (it only contracts
calls in VIX 20–28 when running DD ≥ 0.077). The current tape is a **normal-EV**
environment for calls (see §2). There is no special current-regime exploit beyond
the universal sizing law below.

## 2. EV structure (per-trade mean option pnl, 6.45M call trades)

**By entry running-DD (the headline):**

| entry_dd band | N | EV (mean pnl) | loser-rate z |
|---|---|---|---|
| dd[10,20) | 660k | **+0.071** | −9.1 |
| dd[20,35) | 603k | **+0.073** | −15.1 |
| dd[00,10) | 2.11M | +0.058 | −18.7 |
| dd[55,∞) | 2.06M | +0.046 | +18.5 |
| **dd[35,55)** | 1.01M | **+0.041** (worst) | **+19.6** |

→ **Deep-DD entries (35–55%) are the WORST cohort** — lowest EV *and* highest
loser-rate. This is exactly the band the shipped `DD_SOFT_BAND` (0.35→0.55,
floor 0.40) contracts. The data says it could be **sharper/earlier**.

**By VIX:** vix≥28 panic **+0.153** (best, uncontrollable) · vix<15 **+0.052**
(current regime) · vix 15–20 +0.049 · **vix 20–28 +0.027** (worst → RXDD).

**By tier:** top(85-94) **+0.125** · ultra(95+) +0.113 · mid(80-84) +0.088 ·
low(75-79) +0.050 · **overflow(70-74) +0.048** (worst EV, but 69% of trades by N,
fills idle capital → accretive per the 2026-06-03 overflow ship).

**By concurrency:** concur 4–6 **+0.074** · 7–9 +0.059 · **10+ +0.047** (crowded
book → diminishing marginal EV).

## 3. Explosion analysis — what makes a run a top-decile monster?

Per window, top-decile (by final_value) vs the rest. The explosive runs are
**NOT** distinguished by entry VIX, breadth, or tier mix (all ~identical). The one
robust separator across **every** window: **lower mean entry_dd**.

| window | top-decile mean entry_dd | rest |
|---|---|---|
| 2021 | 0.12 | 0.21 |
| 2023 | 0.315 | 0.449 |
| 2024 | 0.042 | 0.051 |
| dip | 0.066 | 0.102 |
| 5y | 0.286 | 0.313 |

(win-rate top vs rest is flat ~0.70 — it is not a per-trade-quality effect; it is
a **path** effect.)

## 4. The law

For the v70 Apex **leveraged-momentum compounding sleeve**, **drawdown-avoidance
*is* return-maximization** (capital-velocity law). Compounding is path-dependent:
a 50% drawdown needs a +100% gain just to recover — wasted time. The fastest
50k→200k path is the one that **keeps the book out of deep-DD holes**, *not* the
one that sizes up. This is *why* RXDD (which cut DD in the bad VIX band) also
**raised** compound (+9.4%).

**Implication for the user's "be more aggressive early" intuition:** for a modest
4× target, almost any non-collapsing config reaches 4× *eventually* — so the
discriminator is **time-to-4×**, and time-to-4× is minimized by velocity
protection, not by cranking exposure. We test both directions empirically:

- **(A) Exposure-cap scan** {0.35 … 1.00}: does loosening the 50% cap speed 4×
  (user hypothesis) or slow it (capital-velocity law)?
- **(B) DD-soft-band re-shape**: sharper/earlier contraction of the worst
  dd[35,55) pocket → faster compounding + lower DD?

All knobs are already fully wired → a winner ships as a **pure strategy_config
value change** (no new mechanism, lowest risk).

Ranking: **collapse=0 on every window incl 2020-COVID = hard floor**; then maximize
mean log-compound across the window mix (speed); don't tank any single window. DD
**reported, not constrained** (early-stage budget).

*(v70 Apex per-window baseline median/DD filled from the Phase-B baseline run.)*
