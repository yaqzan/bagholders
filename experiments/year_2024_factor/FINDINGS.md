# Why is 2024 so explosive under v70 Apex? — the "IT factor" deep dive

**Date:** 2026-06-09 · **Substrate:** live full-lever Apex MC tape (`.cache/dd_ledger/tape_<year>.parquet`,
300 seeds, calls-only, RXDD+SVR+MWDD on; from the 2026-06-07 TVDD run) + read-only market regime.
Zero new heavy compute for the mechanism; per-year clean-Apex headline via queue task #91.

Scripts: `decompose.py` (drivers), `regime.py` (market), `dispersion.py` (single-stock),
`tails.py` (pnl distribution), `timing.py` (H1/H2), `run_apex_peryear.py` (headline MC).

---

## TL;DR

2024's explosiveness is **NOT index beta and NOT calm tape** — 2023 had a nearly identical S&P
(SPY +24.8% vs +24.0%, RVol 13.2 vs 12.6, VIX 16.8 vs 15.5, *more* calm-day persistence) yet the
strategy made **+56% in 2023 vs +5,426% in 2024** (a 97× gap on twin index conditions). The factor
lives **below the index**: 2024 was a **low-momentum-crash year** — the high-conviction (75+) momentum
leaders the scorer holds **trended persistently and rarely suffered the sharp synchronized reversal**
that turns option positions into −90% bags. That did two compounding things at once:

1. **Per-trade edge ~quadrupled** — dollar-weighted option P&L **+0.131** (2024) vs +0.017–0.092 (others).
   *70% of the edge is the LOSS side*: catastrophic dead-hold-expiry bags fell to **11.9%** (2024) from
   **16.4%** (2023). You make the money by *not* taking the momentum crash, not by winning bigger
   (the median winner is the +30% TP in **every** year).
2. **The portfolio almost never drew down** — avg DD at entry **0.052** (5.2%) vs 0.20–0.48; only
   **13.6%** of 2024 entries were struck while >15% underwater vs 47–84% elsewhere. For an uncapped
   geometric compounder, *never giving back* is the multiplier that turns a 4× per-trade edge into a
   ~97× compound gap.

**Reverse-engineering verdict:** the factor is **exogenous** (the market not handing the leaders a
crash) and **not sizable into ex-ante** — H1 strength does **not** predict H2 (2023-H1 was strong then
H2 collapsed; 2025-H1 was weak then H2 ripped), which is exactly why *pro-cyclical bull-boost already
failed* in the MC sweeps and *over-deployment HURTS*. The realizable inverse is **already shipped and
is the mirror**: contract the *detectably-bad* regimes (RXDD/MWDD/SVR/TVDD/F3F) and let the dead-hold
defer the loss-side — which maximizes 2024-participation by subtraction. The only place latent
"2024-ness" could be added to the *other* years is a **score-stage persist-vs-crash discriminator**
(known-hard; the open option-**skew** lead is the live candidate).

---

## 1. The compound, reconstructed per year (faithful, from the tape)

`log10(final/start)` per seed, mean over 300 seeds (live full-lever config; clean Apex is higher but
the year ranking is identical):

| year | log10 mult | ~compound | per-trade $-wtd edge | catastrophic-bag rate | avg entry-DD |
|---|---:|---:|---:|---:|---:|
| 2018 | 0.160 | +45% | +0.004 | 18.5%* | 0.250 |
| 2020 | 0.286 | +93% | +0.023 | 15.3%* | 0.477 |
| 2021 | 0.575 | +276% | +0.038 | 14.9% | 0.206 |
| 2022 | 0.420 | +163% | +0.026 | 15.7% | 0.276 |
| **2023** | **0.193** | **+56%** | **+0.017** | **16.4%** | 0.274 |
| **2024** | **1.742** | **+5,426%** | **+0.131** | **11.9%** | **0.052** |
| 2025 | 0.961 | +815% | +0.092 | 14.7% | 0.191 |

2024's p10–p90 seed band (log10 1.55–1.92) **does not overlap any other year's p90** → structural, not
seed luck. (*dh_expiry rate; cols from `decompose.py`.)

### 1b. The clean-Apex standalone-year result (queue #91, N=200) — even starker

Canonical Apex (puts-off, **no DD levers**, no overflow, each year standalone from $50k):

| window | 2018 | 2021 | 2022 | 2023 | **2024** | 2025 | 5y | 10y |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **MedRet** | −18.5% | −30.4% | +13.9% | −2.2% | **+2,240%** | −6.7% | +3,492% | **+17,793%** |
| WorstDD | 58.7 | 61.6 | 55.0 | 47.9 | **30.3** | 67.0 | 66.5 | 80.7 |
| Call TP% | 68.6 | 67.1 | 69.2 | 67.1 | **82.3** | 75.8 | 72.5 | 71.6 |

**2024 is the ONLY strongly-positive standalone year — every other recent year is flat-to-negative**
without the shipped levers, and 2024 is the only year with a sub-50% DD (30%) and an 82% call-TP. The
entire +17,793% continuous 10y is essentially 2024's +2,240% compounding through. Two consequences:

- **The strategy *is* the 2024 factor.** Uncapped clean momentum is a *losing* standalone bet in 4 of 6
  recent years; it works because it survives the bleed years and detonates in the rare low-crash year.
- **The shipped DD-lever stack is the measured inverse of the factor** — its whole job is the *other*
  years. The same config WITH RXDD+SVR+MWDD+TVDD+dead-hold+overflow (the §1 tape) turns 2021
  −30%→+276%, 2023 −2%→+56%, 2025 −7%→+815%: i.e. the levers stop the non-2024 bleed so the book stays
  solvent and compounding until the next 2024 lands on a healthy base. That is the realizable
  "reverse-engineering" of the 2024 factor, and it is already shipped (§5).

## 2. The market regime — 2024 ≈ 2023 at the index (the key disproof)

| year | SPY ret | RVol | maxDD | calm-run | VIX avg | %VIX<16 | McClellan avg | strategy compound |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | +28.8 | 13.0 | 5.4 | 187 | 18.5 | 10 | −1.9 | +276% |
| **2023** | **+24.8** | **13.2** | 10.3 | 121 | 16.8 | 44 | +4.2 | **+56%** |
| **2024** | **+24.0** | **12.6** | 8.4 | 98 | 15.5 | 68 | **−3.8** | **+5,426%** |
| 2025 | +16.6 | 19.5 | 19.0 | 124 | 18.9 | 25 | +1.4 | +815% |

2023 and 2024 are statistical twins at the index level, 97× apart in strategy outcome. Note 2024 had
the **most negative** McClellan (−3.8) → a **narrow** bull (index carried by the leaders), which is the
exact regime a momentum-concentrating 75+ scorer monetizes while breadth gauges look mediocre.

## 3. It's the LOSS side, not the win side (attribution vs 2023)

`tails.py` — each year's mean-pnl edge over the 2023 baseline, split win-side / loss-side:

| year | edge vs 2023 | win-side | **loss-side** |
|---|---:|---:|---:|
| **2024** | **+0.066** | +0.020 | **+0.046** (70%) |
| 2025 | +0.032 | +0.013 | +0.019 |
| 2021 | +0.025 | +0.010 | +0.015 |
| 2022 | −0.011 | −0.011 | +0.000 |

Concentration is identical across years (gini ~0.48, 2024 traded *more* names: 344 vs 282) — so it is
**not "a few monsters."** The whole 75+ cohort had higher follow-through and fewer reversals. Classic
momentum: the alpha is *avoiding the crash*.

## 4. Could you have known early? No (the reverse-engineer killer)

`timing.py` H1 vs H2 — H1 strength does not predict H2:

| | TP% | bag% | meanPnl |
|---|---:|---:|---:|
| 2024-H1 | 75.7 | 12.1 | +0.103 |  ← strong, then **stayed** strong (H2 74.5 / 11.8 / +0.101)
| 2023-H1 | 71.5 | 14.4 | +0.050 |  ← 2nd-best H1, then **collapsed** (H2 69.5 / **18.4** / +0.022)
| 2025-H1 | 70.2 | 16.3 | +0.040 |  ← weak, then **ripped** (H2 75.4 / 13.2 / +0.096)

No exploitable half-year autocorrelation → you cannot reliably lever into the 2024-factor in real time.

---

## 5. Reverse-engineering — what you CAN and CANNOT do

- **CANNOT manufacture it** — it's the market not crashing the leaders. Exogenous.
- **CANNOT size into it ex-ante** — empirically: pro-cyclical bull-boost lost (Phase 13/14), exposure
  peaks ~50% (over-deployment deepens DD), and §4 shows H1 doesn't forecast H2. Asymmetry: the cost of
  levering into a chop ≫ the benefit (you already participate fully when uncapped).
- **ALREADY captured maximally** — Apex is uncapped and full-size in calm; 2024 = the leveraged-momentum
  thesis in its ideal habitat. The 10y headline being "2024-dominated" is the *expected* signature of an
  uncapped compounder meeting its best regime, not money left on the table elsewhere.
- **The correct realizable inverse = the MIRROR (shipped):** since 2024's edge is *avoiding
  loss-clustering*, the lever is *contracting the loss-clustering regimes* — RXDD (VIX 20-28 bleed),
  MWDD (flat McClellan), SVR (skew), TVDD (TRIN), F3F (breadth), and especially the **dead-hold**, which
  defers the −70% SL bags that *are* the dominant loss-side (+0.046) driver. These don't require
  predicting the good year; they avoid the clearly-bad sub-periods, which maximizes 2024-capture.
- **The only orthogonal frontier = SCORING:** a score-stage feature that separates *persist-momentum*
  (2024 leaders) from *crash-momentum* (2023/2025 leaders). This is the sole way to add 2024-like
  loss-avoidance to the *other* years. Known-hard — the closed NULLs (divergence dampener, relative
  strength, per-stock score normalization) all chased it and failed (opt15 WR ≈ 47% for *any* price
  partition; option outcome is vol-path-dominated). The live candidate is the **option-skew** lead
  (cheap-call vs expensive-call discriminator, mechanism-confirmed, currently options-data-locked;
  `semivol_r` is the 10y bridge already shipped as SVR).

---

## 6. /research follow-on (2026-06-09) — mined the persist-vs-crash discriminator → CONFIRMED but STAGED

User invoked `/research` to mine for the §5 "orthogonal lever." Outcome: the genuine unshipped edge is
**direct option-chain skew** (`put_iv−call_iv`, "buy the cheap call"), and it is a **confirmed residual to
the shipped SVR** on the tradable 75+ cohort — but **data-locked from the gate** (a stage, not a ship).

- **It's real & orthogonal:** opt_skew→win **t=+3.16 controlling for the shipped semivol_r** (which
  collapses to t=+0.15) — SVR's "skew bridge" branding is a weak proxy, not this edge. Orthogonal to the
  price score (t=+3.21) and recent return (t=+3.52), sign-stable every quarter, 23pp win-rate quintile
  spread (36%→59%), strongest in recently-pulled-back names (the persist-vs-crash sweet spot).
- **Why it can't ship (structural, not effort):** premium-dominated → the realized-vol MC is blind
  (opt_skew→underlying barrier only z≈+1.8); option-data-locked to ~1.3y (no COVID/2022/2021 → the
  Stage-3 collapse=0 floor is unreachable); the one covered window (2025-02→2026-04) is a net-negative
  selloff book, so OSK-on-top-of-SVR is +0.6-1.5pp per-trade but flat-to-−1.2pp on the portfolio there.
- **Staged** with the mechanism design + exact ship-path (data depth + a premium-aware validator + a
  framework decision on how option-implied signals are gated): **`OSK_SHIP_HANDOFF.md`**.
  Scripts: `skew_residual.py`, `skew_winrate.py`, `skew_robust.py`, `isolate_osk_vs_svr.py`.

**Net:** the 2024-factor's realizable scoring-side lever has a confirmed home — option-implied skew — but
it's gated by `option_prices` coverage + an IV-aware validator, not by ideas. The 10y-computable proxy
(SVR) is already shipped; the strong piece waits on data.
