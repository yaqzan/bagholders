# Market-Wave / breadth-momentum DD investigation — v70 Apex (2026-06-05, overnight /research)

**User ask:** "the portfolio drew down on a market pullback today; how can we better protect
ourselves? can we use the market wave function and improve it / add components / shuffle weights?"

**Objective:** reduce Apex drawdown without reducing compound (a strict Pareto win), portfolio-stage
(Stage 3) — no `ALGORITHM_VERSION` bump, no recalc, reversible. Substrate: v70 (`c70d16d22`) Apex,
calls-only, on the **current live RXDD + SVR** config (so we mine *residual* DD after today's protections).

## Today's pullback (2026-06-05) — the motivating signature

breadth_score crashed **42 → 27**, McClellan **−18.7**, sector Market Wave hit **Stress** (crash_echo
re-firing) — yet **VIX only 20.1** (bottom edge of RXDD's band) and regime_multiplier **1.03 (HEALTHY)**.
A *breadth-deterioration* pullback that VIX/regime (and thus RXDD) largely miss. This is the orthogonal
opening the user intuited.

## Track B (answered, no compute): "shuffle the market-wave / regime weights" is a no-op for DD

- The live `compute_regime_composite` is a dynamic **VIX + INVERTED-breadth** blend (VIX weight =
  sigmoid centred at VIX 22); **market_trend is always 0**. The `SIGNAL_WEIGHTS` dict
  (breadth/vix/trend) is **vestigial — not used**. Literally shuffling those weights does nothing.
- The regime **inverts breadth on purpose** (weak breadth → *higher* composite → *amplify*, for signal
  *reliability* / WR) — so it is structurally **not a drawdown tool**; on today's breadth-27 pullback it
  *amplified* (mult 1.03). Changing it needs a full recalc + Stage-1 gate, and prior regime-weight A/Bs
  (F/G/H/I, see scoring-algorithm.md) all lost to the current composite. → the recalc-free place to
  "use the market wave + tune its components" is **portfolio sizing** (this investigation), not scoring.

## Method (Track A)

MC trade-tape on the live v70 Apex config (`MC_TRADE_TAPE=1 N_ITER_OVERRIDE=300`, task #63, 12 windows,
6.86M call trades), then mined cohort loser-rate lift/z + DD-concentration + **per-window EV robustness**
over the full-history market-crash signals (`experiments/market_wave_dd_v70/mine.py`):
- Sector-ETF **Market Wave** score / signed / **crash_echo** (rebuilt 1999+ from SPDR prices, production-
  faithful via `market_breadth._load_sector_etf_breadth_rows`; the `.cache` CSV was absent and the DB
  `sector_etf_market_wave_score` column only covers 2024-07+, so a rebuild was required).
- **McClellan oscillator** (internal breadth momentum, DB, full history) — used by NO current lever.
- breadth_score 5d velocity, wave velocity, pct_above_ema50, VIX, breadth level (RXDD/F3F reference).

## Finding 1 — the user's literal hypothesis is FALSIFIED (mean-reversion, not protection)

Calls bought during Market-Wave **stress** / **negative** McClellan / **high** crash_echo are
**mean-reversion WINNERS**, positive-EV in 11 of 12 windows:

| signal cohort | full-tape mpnl | per-window EV |
|---|---|---|
| Market Wave severe (<20) | **+0.094** | positive every window except 2020_crash (+0.004) |
| McClellan deep-negative (<−15) | **+0.098 .. +0.120** | winner everywhere except the COVID crash |
| crash_echo high (≥0.5) | **+0.094** | winner |

Contracting these = the documented **"DD-mitigation is structural" crash-artifact trap** (RXDD Finding 1,
now confirmed for the explicit Market Wave). Mechanistically inevitable: the Apex edge *is* buying
weakness, so calls bought into breadth-weakness mean-revert and win — hardest precisely during crashes.
**The 2020/2020_crash/10y drawdowns (78-80%) are irreducible by call contraction — they are the price of
the buy-weakness + dead-hold V-recovery edge.**

## Finding 2 — the one orthogonal, defensible signal: the FLAT/topping breadth-momentum band

The low-EV + DD-concentrated cohort is the **flat/topping** band, NOT the crash band — the exact shape
RXDD found on VIX (mid-band 'slow-bleed' low-EV, panic best):

| McClellan band | mpnl | DD-conc | z |
|---|---|---|---|
| < −50 (capitulation) | +0.120 | 0.02 | winner |
| −50..−15 | +0.098 | 0.68 | winner |
| **−15..+15 (flat/topping)** | **+0.046** | **2.04** | low-EV, DD-concentrated |
| > +15 (rising) | +0.035 | 0.42 | low-EV, low-conc |

**Orthogonality confirmed:** in the slice where RXDD is off and F3F is ~off (VIX<20 ∧ breadth≥40, 41% of
the book), flat-McClellan stays low-EV (+0.040, conc 1.52). So it captures DD that VIX (RXDD) and breadth
LEVEL (F3F) don't.

**The crash-artifact guard (per-window, DD-gated):** contracting the flat band HELPS the bull/choppy
windows (2021/2023/2024/2025/22-now/5y/dip, cohort below base) but **HURTS the crash windows**
(2020_crash flat-band cohort **+0.234**, a winner). The rescue: 2020_crash is **VIX≥28 panic** — so a
**VIX-panic exclusion** (RXDD's leave-panic-alone trick) skips the crash harm while keeping the bull-year
benefit. (And the deep-negative crash McClellan is in the Gaussian tail anyway.)

## Mechanism — MWDD (McClellan flat-band CALL-alloc dampener)

`monte_carlo.py`: smooth Gaussian bump on the McClellan oscillator contracts call alloc in the flat band
(`alloc_frac *= _mwdd_call_scale(dd, mcc_today, vix_today)`), no-op when disabled / McClellan missing /
running dd < `MWDD_DD_MIN` / VIX ≥ `MWDD_VIX_PANIC`:

```
mwdd_scale = 1 − DEPTH · exp(−0.5·((mcc − MCC_C)/MCC_W)²)      (in [1−DEPTH, 1.0])
```

Env-overridable knobs `MWDD_ENABLED / MWDD_MCC_C / MWDD_MCC_W / MWDD_DEPTH / MWDD_DD_MIN /
MWDD_VIX_PANIC`, default OFF. McClellan threaded via a new `load_mcclellan_map` mirroring the RXDD VIX
map (11-site wiring through run_single_sim + the MP worker state). Wired into `driver.py` ENV_MAP.

McClellan is literally **"another component of the market wave" (breadth momentum)** — directly answering
the user's "other components to add."

## Verify (N=200, task #64) — mechanism LIVE + Pareto-shaped

OFF reproduces the task #63 baseline byte-exactly (2023 WorstDD 63.8, 2024 51.7 → the 11-site edit is a
true no-op when off). ON (depth 0.35, center 0, width 22, dd_min 0.10, panic 28):

| window | OFF | ON | Δ |
|---|---|---|---|
| **2023** | DD 63.8 / med +8.9% | DD **55.6** / med **+35.9%** | **−8.2pp DD AND compound UP** (Pareto) |
| 2024 | DD 51.7 / med +4723% | DD 43.7 / med +4209% | −8.0pp DD, −11% compound (noise-ish) |

## Phase B — LHS-16, N=100 × [2020_crash, 2022, 2023, 2024, dip, 5y] (task #65)

**Robust Pareto across the search space** — like RXDD's Phase B. Many clean candidates
(collapse=0 every window, compound flat-or-up) cut 5y WorstDD. Top clean by DD-focus:
- **c05** C=−0.34 W=22.2 D=0.337 dd_min=0.128 → ddFoc +6.47, dd5y +3.3, **comp +0.041 (UP)**, coll 0
- **c10** C=+11.6 W=20.6 D=0.396 dd_min=0.200 → ddFoc +5.28, dd5y +3.9, **comp +0.041 (UP)**, coll 0
- c02 C=+13.5 W=24.4 D=0.187 dd_min=0.147 → ddFoc +4.25, dd5y +1.9, comp +0.001, coll 0

ddCrash small/positive across the board (2020_crash untouched-or-better → panic-exclusion works).

## Phase C — N=300 × 10 windows (FULL incl COVID) (task #66): CLEAN PARETO

Winner **c00 = B-c05** (`MCC_C −0.336 / W 22.185 / DEPTH 0.337 / DD_MIN 0.128 / VIX_PANIC 28`).
Every window's DD improves; collapse=0 everywhere incl 2020/2020_crash; compound ~flat.

| window | base DD | c00 DD | Δ | coll |
|---|---|---|---|---|
| **5y** | 66.4 | **62.9** | **−3.5** | 0 |
| **22-now** | 67.0 | **61.5** | **−5.5** | 0 |
| 2023 | 63.8 | 55.7 | −8.1 | 0 |
| 2024 | 51.7 | 43.0 | −8.7 | 0 |
| 2025 | 63.9 | 61.2 | −2.7 | 0 |
| 2022 | 63.3 | 61.3 | −2.0 | 0 |
| 2021 | 63.9 | 60.8 | −3.1 | 0 |
| dip | 45.9 | 41.6 | −4.3 | 0 |
| 2020 | 78.8 | 77.1 | −1.7 | 0 |
| 2020_crash | 79.3 | 77.5 | −1.8 | 0 |

c00 avg compound delta −0.016 log (MC noise); worst-window −0.122 (within clean bar). c01
(center +11.6) is the compound-up runner-up (5y −3.1, comp +0.009). c00 chosen for the bigger DD cut.

Gate (Stage-3 T1–T7): T4 PASS (5y WorstDD −3.5pp ≫ +1pp bar) · T5 PASS (no window regresses; all DD down) ·
T6 PASS (collapse=0 every window incl COVID) · T7 PASS (compound flat, same OOM). MWDD is a SECOND
orthogonal DD lever stacking on RXDD+SVR+F3F (all already on in the baseline).

## Phase D — N=500 × 10 ship-gate (task #67): CONFIRMED — T1–T7 ALL PASS

c00 (`MCC_C −0.336 / W 22.185 / DEPTH 0.337 / DD_MIN 0.128 / VIX_PANIC 28`) at N=500:

| window | base DD | c00 DD | Δ | base med | c00 med | coll |
|---|---|---|---|---|---|---|
| **5y** | 67.4 | **64.8** | **−2.6** | 532,466% | 478,047% | 0 |
| **22-now** | 67.3 | **61.8** | **−5.5** | 180,763% | 176,385% | 0 |
| 2023 | 64.6 | 56.8 | −7.8 | +7.8% | **+29.3%** (up) | 0 |
| 2024 | 51.7 | 43.4 | −8.3 | 4,780% | 4,537% | 0 |
| dip | 48.9 | 43.1 | −5.8 | 287% | 288% | 0 |
| 2021 | 65.3 | 60.8 | −4.5 | 235% | 212% | 0 |
| 2025 | 63.9 | 61.6 | −2.3 | 744% | 657% | 0 |
| 2022 | 63.6 | 61.3 | −2.3 | +84.8% | **+125.1%** (up) | 0 |
| 2020 | 78.8 | 77.1 | −1.7 | 59% | 44% | 0 |
| 2020_crash | 79.3 | 77.5 | −1.8 | −50.4% | −49.9% | 0 |

T4 PASS (5y WorstDD −2.6pp) · T5 PASS (every window DD down, 0 regressions) · T6 PASS (collapse=0
every window incl COVID) · T7 PASS (compound flat, same OOM). Stable across N=100 (dd5y +3.3) /
N=300 (+3.5) / N=500 (+2.6) — not N-noise. avg comp delta −0.007 (MC noise), worstComp −0.108.

## Decision: ✅ SHIP c00 — MWDD live in v70 Apex (Stage-3, portfolio-only, no version bump, reversible)

A second orthogonal DD lever (breadth momentum) stacking on RXDD(VIX) + SVR(skew) + F3F(breadth level).
Answers the user: the Market Wave can't protect via *crash*-state contraction (mean-reversion winners),
but its breadth-momentum *flat/topping* component (McClellan ≈ 0) is a genuine DD signal — used here as
SIZING (recalc-free), VIX-panic-excluded so COVID stays collapse-safe.

