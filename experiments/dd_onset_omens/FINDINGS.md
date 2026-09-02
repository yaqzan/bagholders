# DD-episode-onset omens → Hindenburg NULL; pre-top breadth divergence (BDIV) survivor (2026-06-10)

**/research run.** User ask: *"isolate the periods of major drawdowns and extrapolate the data to
pinpoint patterns — a lookback at what the market was doing leading up to them; maybe a Hindenburg
omen or similar market shape raises drawdown likelihood."*

Substrate: **fresh v71 tape** (first DD mine on the v71 + retuned-Apex params; all prior tapes were
v70-scored). Tape: `MC_TRADE_TAPE=1`, N=300, windows `2020` + `5y` (within MarketBreadth coverage
2019-10+), shipped config (5y med +1,660% / WorstDD 67.6 / collapse 0 = the retune Phase D numbers).
Harness: `mine_onset.py` (episode-onset event study + per-trade shippability) + `mine2_refine.py`
(divergence threshold grid + continuous-depth probe).

---

## Part A — the strategy's major drawdowns, isolated (the user's literal ask)

Clustering episode `start_date`s across 300 seeds (top-3 episodes/seed, magnitude ≥ 0.30), the
canonical major-DD onsets are:

| onset | seed-fraction | context |
|---|---|---|
| 2020-02-10 | 1.00 | COVID top |
| 2021-01-25 | 0.78 | meme-peak momentum reversal |
| 2022-04-12 / 04-20 | 0.48 / 0.47 | bear-rally top into the 2022 leg down |
| 2025-02-04 | 1.00 | 2025 dip top |

## Part B — pre-onset lookback profile: NO consistent omen signature

Feature percentiles (vs all-days baseline) in bands [-20,-11]/[-10,-6]/[-5,-1] before each onset:
**inconsistent across onsets** (e.g. churn_pct at [-5,-1]: onsets o3/o4 at 99-100th pct but o2 at
1st). The only common shape is *strength/complacency*: A-D line RISING ([-10,-6] median pct 85.5),
McClellan summation rising (83.6), VIX velocity FALLING at [-20,-11] (pct 3.4). **Drawdowns start
at tops, not after warnings** — confirms G26's diffuse-momentum-reversal read.

## Part C — forward event study: Hindenburg lift = 0.00 (literal hypothesis DEAD)

P(major onset within 10/20d | event) vs base:

| event | n days | K=20 lift (E[fwd onset]) | P(major) lift |
|---|---:|---:|---:|
| **hindenburg omen day** | 24 | **0.00** | **0.00** |
| omen in trailing 20d | 222 | 0.00 | 0.00 |
| **hindenburg confirmed** | 15 | **0.00** | **0.00** |
| confirmed in trailing 30d | 186 | 0.00 | 0.00 |
| zweig thrust active | 107 | 1.43 | **2.51** |
| breadth divergence (10d) | 872 | 1.15 | 0.99 |
| NL-at-highs divergence (10d) | 587 | 1.36 | 1.13 |
| McClellan-summation div (10d) | 900 | 1.43 | 1.23 |

ZERO of the 24 Hindenburg-omen days (and zero of the 222 trailing-window days) preceded a major
onset. The highest forward lift is the **bullish Zweig thrust** (2.5× on P(major)) — thrusts mark
the explosive runs whose TOPS the drawdowns start from. Poetic confirmation of "tops, not omens."

## Part D — per-trade shippability: omens are WINNER cohorts; one survivor

| event state (entries during) | rate vs base 0.310 | mpnl on/off | verdict |
|---|---|---|---|
| ON hindenburg-omen day | 0.226 (z −24.7) | **+0.079** / +0.022 | **winners — contracting cuts alpha (G19)** |
| ON hindenburg-confirmed day | 0.215 (z −24.1) | +0.083 / +0.022 | winners |
| trailing-30d after confirmed | 0.341 (z +24.0) | −0.020 / +0.029 | low-EV BUT **fails ortho slice** (inverts to +0.073 winner) + per-year sign-flips → G23's NH/NL death |
| zweig active | 0.263 (z −20.1) | +0.088 | winners |
| McClellan-summation div / churn / NL-spike / NL-at-highs | — | — | per-year sign-flippers → dead (G26) |
| **div_brd10 (SPY near 60d high + breadth_chg10 < −5)** | 0.315 (z +9.1) | **+0.012 / +0.037** | **survivor — see below** |

### The survivor: pre-top breadth divergence

`div_brd10`: **negative on-vs-off mpnl delta in ALL 7 year-windows** (2020 −0.067, 2021 −0.013,
2022 −0.115, 2023 −0.182, 2024 −0.007, 2025 −0.048, 2026 −0.137) — the only candidate to pass the
G26 sign-stability bar — **and survives the all-levers-off orthogonal slice** (vix<20 & |mcc|>30 &
breadth≥40 & TRIN outside 1.0-1.3): ON −0.004 vs OFF +0.117. But coverage 55% of trades = a regime
label, not a band (dd_conc only 1.29).

### Refinement grid (mine2_refine.py) — the tight form

- **Proximity-to-highs is MONOTONIC**: P0.025→P0.015→P0.01 (at G5/D0): mpnl_on +0.023 → +0.001 →
  **−0.0175**; conc 1.49 → 1.74 → **1.82**.
- **The breadth-gap is an INVERTED-U**: G5 works, G8 weakens, G12 INVERTS (mpnl_on +0.039 —
  deep/sharp breadth selloffs at highs are mean-reversion WINNERS). Same band-law as
  RXDD/MWDD/TVDD → Gaussian band on the gap, not a ramp.
- Winner cell **P0.01 G5 D0** (SPY within 1.0% of 60d high & breadth −5pts/10d, same-day): coverage
  12.1%, mpnl **−0.0175** vs +0.0286 off, z +25.0, conc 1.82, ortho gap −0.068, sign-stable 5/6
  (only flip: 2020 +0.029, small); **2022 absent BY CONSTRUCTION** (SPY never near 60d highs in the
  bear → the flag cannot fire mid-crash → the crash-trap (G19) is excluded structurally, no DD-gate
  or VIX-panic knob needed).
- The continuous depth-product (gap × proximity) is NON-monotonic (band [2,5) is a +0.072 winner)
  → don't key on a product depth; key on proximity-ramp × gap-Gaussian (the BDIV form).

## Mechanism — BDIV (env-gated OFF in monte_carlo.py)

```
scale = 1 − DEPTH × prox_ramp(spy_from60h; PROX_CUT→PROX_FULL) × gauss(−Δbreadth_10d; GAP_C, GAP_W)
```
Knobs `BDIV_ENABLED / BDIV_PROX_CUT / BDIV_PROX_FULL / BDIV_GAP_C / BDIV_GAP_W / BDIV_DEPTH`;
map loader `load_bdiv_map` (SPY 60d-high distance + breadth 10d change). **No DD-gate** — the
lever's value is PRE-onset contraction (book dd ≈ 0 at the top); the SPY-near-highs requirement
replaces the crash-gate structurally. Driver ENV_MAP wired. Default OFF = baseline byte-identical.

## Sweep

**Verify (N=200, task #120) — PASS:**
- NOOP: bare baseline − `BDIV_ENABLED=0` = (0.0, 0.0) on 2021 + 2022 → true byte-identical no-op when off.
- Fires correctly at unswept defaults (PROX 0.020/0.005, GAP 6.5/2.5, DEPTH 0.45): 2021 (the
  divergence year) WorstDD 67.3 → 65.1 (**−2.2pp**) AND med ret −36.3 → −33.3 (**Pareto direction**);
  2022 ddOFF−ON = **0.0** (the by-construction crash no-op, confirmed in-engine).
- Loader sanity: 2021-01-25 (the biggest canonical onset day) gets scale 0.625 ≈ max contraction;
  2022 fires on 4% of days (2 deep); 2021/2025 fire 39%/31% (deep ~17%).

**Phase B (LHS-16, N=100 × 6 windows incl 2020_crash, task #121) — STRONG:** 16/16 clean
(collapse=0 on every window, worstComp ≥ −0.19) and 16/16 positive ddRed — the strong-lever
signature (RXDD/TVDD-class; vs DQT's 2/16 weak pass). Coherent basin: GAP_C ~7.4-7.9, wide
GAP_W ~3.0-3.5, DEPTH 0.44-0.53. Leaders:

| cand | params (PROX_CUT/FULL · GAP_C/W · DEPTH) | ddFoc | ddAll | dd5y | comp |
|---|---|---:|---:|---:|---:|
| c02 | .0285/.0130 · 7.40/3.02 · 0.44 | **+5.00** | +3.32 | +1.7 | +0.053 |
| c14 | .0198/.0075 · 7.72/3.46 · 0.53 | **+5.00** | +3.28 | +1.5 | **+0.085** |
| c04 | .0222/.0076 · 7.85/3.35 · 0.45 | +4.85 | +3.20 | +1.0 | +0.069 |
| c11 | .0164/.0048 · 6.96/2.89 · 0.40 | +3.58 | +2.32 | +0.7 | +0.019 |

DD down AND compound UP on the focus windows (Pareto); 2020_crash ≈ untouched (−0.3..+0.4).

**Phase C (winner + 3 neighbors, N=300 × 10 windows incl COVID, task #122) — FIRMED:** 4/4 clean.
Winner **c01** (= B-c14): ddFoc +4.26, ddAll +2.20, **5y WorstDD 67.6 → 64.6 (−3.0pp)** at
**compound +21%** (5y med 1,660 → 2,014), **dip DD 40.3 → 26.3 (−14.0pp) at compound +49%**
(the 2025-02-04 divergence-led drawdown — the motivating case), 2021 DD −2.4pp with med
−36.4 → −28.5, 22-now +16%, **2022 delta exactly 0.0** (the by-construction crash no-op),
2020/2020_crash flat, collapse=0 everywhere. Cost: 2024 med −8.7% relative (the predicted
persistent-year price; well inside T7).

**Phase D (ship gate, winner + 2 neighbors, N=500 × 10, task #124) — ALL T1-T7 PASS:**
(first attempt, task #123, HUNG at the 5h timeout with an empty log — root cause: engine files
(monte_carlo/strategy_config) were edited mid-run for the ship wiring; Windows MP workers
re-import from disk per window, a worker died on a mid-edit file state and pool.map blocked
forever. Rerun with the baseline made explicit (`BDIV_ENABLED=0`) since strategy_config now
ships BDIV on. **Lesson: never edit engine/config files while a queued sweep that
subprocess-imports them is running.**)

Winner = the shipped params (PROX 0.0198/0.0075 · GAP 7.716/3.4571 · DEPTH 0.53), ddFoc +4.20,
both neighbors clean (+3.4/+3.5 — not a boundary winner); stable across N=100/300/500
(+5.0/+4.26/+4.20). Per-window (N=500, paired seeds):

| window | base DD | BDIV DD | Δ | base med | BDIV med |
|---|---:|---:|---:|---:|---:|
| **5y** | 67.6 | **64.6** | **+3.0** | 1,660.5 | **2,009.5 (+21%)** |
| **dip** | 40.3 | **26.3** | **+14.0** | 190.9 | **284.1 (+49%)** |
| 2021 | 67.4 | 64.9 | +2.5 | −36.5 | −28.6 |
| 2025 | 65.7 | 64.2 | +1.5 | 27.3 | 28.7 |
| 2023 | 58.4 | 57.8 | +0.6 | −42.4 | −41.7 |
| 22-now | 65.0 | 65.0 | 0.0 | 2,485.4 | 2,796.1 (+12.5%) |
| 2022 | 55.1 | 55.1 | **0.0 (by construction)** | 51.9 | 51.7 |
| 2020 | 72.7 | 72.7 | 0.0 | −32.7 | −34.9 |
| 2020_crash | 69.1 | 69.2 | −0.1 | −53.6 | −54.1 |
| 2024 | 35.5 | 35.6 | −0.1 | 1,022.4 | 936.1 (−8.4%, the persistent-year price) |

T4 (5y DD): −3.0pp IMPROVE ✓ · T5 (worst annual regress): 0.1pp ≪ 5pp ✓ · T6 collapse=0 every
window incl COVID ✓ · T7 (worst window comp log −0.080 ≪ ±3 OOM) ✓. **SHIPPED 2026-06-10/11.**

**Adversarial wiring review (independent agent, full git diff + runtime A/B):** NO BLOCKERS.
All five scale implementations bit-exact (14-case numeric A/B incl edges); both map loaders
0 value-diffs on 322 common dates; MP threading order verified; BDIV-off = byte-identical
no-op runtime-confirmed; puts isolated in every engine. Two WARNs:
1. **portfolio_engine.py gap (PRE-EXISTING, now 4 wide):** the live real-money Portfolio
   tracker's `_cascade_entries` applies only RXDD — SVR (06-05), MWDD (06-05), TVDD (06-07),
   and now BDIV never reach live entry sizing, and `_strategy_fingerprint` can't see their
   config. Live-vs-validated divergence since 2026-06-05. Follow-up task spawned
   (wire all four + fingerprint + re-run the bit-exact validation vs run_cascade_backtest).
   **→ RESOLVED 2026-06-11:** all four mirrored into `_open_entries_for_day` (+ fingerprint
   knobs + CT-sort <95 parity fix); bit-exact parity re-validated vs run_cascade_backtest
   over 06-01→06-10 with the scales active (SVR ≠1.0 on 11/22 fills, BDIV 0.923/0.975 on
   Jun 3-4). Harness: `experiments/portfolio_engine_parity/validate.py`. New WARN surfaced
   while verifying the sweep: the engine's 16:00 session gate can fill on a score row the
   ~16:30 close-update then rewrites (ADUR 06-10: 79→38 fakeout) — separate chip spawned.
2. **Backend restart is a hard precondition** for the frontend bundle (stale API lacking
   BDIV fields → Backtest page TypeError on tip render).

**Functional smokes (live config, BDIV on):** `trader alloc 50000` shows the BDIV line;
`trader backtest --from 2024-06-01` clean (1,320 calls, TP 73.9%); `run_cascade_backtest`
2025-H1: ON $50,173/DD 55.1% vs OFF $47,819/DD 57.4% — better on both axes, consistent
with the MC.
