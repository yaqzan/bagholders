# v73 Stage-3 Portfolio Retune — Findings (2026-06-12)

**Problem:** v73 (dampener-retirement ship, `07e9722b5` — WCF/ICH/CWCF/CSWC/SCW
retired) raised 75+ signal supply **+77%** on top of v71's already-doubled
density (ReSim 5y 3,262 → 5,769 75+ signals; the restored CWCF/CSWC/SCW cohort
spans the whole 75-94 band, incl 85-89 N 132→220). The Apex params were fitted
to v71's density → the book runs hotter: the pre-ship MC smoke (N=300 × 7
windows, paired ReSim arms, `.cache/dampener_ablation_v72/mc_smoke_*.json`)
showed collapse=0 everywhere but **DD +5..+13pp at the UNCHANGED Apex params**
— the exact v71 signature. This retune re-fits cascade / exposure / DD-band to
the v73 density. TP/SL (Stage 2) frozen; puts stay OFF (double-nulled v70+v71);
overflow stays 0 (premise dead on dense supply, v71 finding).

Harness: `sweep.py` on `experiments/v69_portfolio_retune/driver.py`
(subprocess-per-candidate, deterministic PYTHONHASHSEED=0 paired seeds,
MC_NO_DB_PERSIST=1, active=v73). PRF-seeded per deploy.md: the instrument
(`portfolio_response.py --derive v73`, **real supply**, supply_approx=false)
proposed `low 0.03` — i.e. squeeze the over-supplied 75-79 slug further than
v71's 0.05. F proposes, MC disposes.

## Phase B (N=100 × {2021,2022,2024,dip,22-now,5y}; 2020 windows deferred to C/D pending the 10y recalc #157)

ddFocus Δ = mean(5y, 22-now) WorstDD improvement vs base; collapse=0 everywhere.

| cand | config | dd5y | dd22n | ddΔ | med5y/base |
|---|---|---|---|---|---|
| **c08_alloc065** | all tiers ×0.65 | 59.3 | 58.4 | **+7.05** | 0.92 |
| **c14_prf_dd** | low 0.03 + DD .30/.50/.40 | 58.8 | 59.0 | +7.00 | 0.86 |
| c01_low02 | low 0.02 | 59.0 | 60.3 | +6.25 | 0.93 |
| **c04_low03_mid08** | low 0.03, mid 0.08 | 60.7 | 60.6 | +5.25 | **1.10** |
| c06_topheavy_trim | top .12 mid .08 low .04 | 61.2 | 61.6 | +4.50 | 1.10 |
| c00_prf_low03 | low 0.03 (PRF) | 61.3 | 61.9 | +4.30 | 0.91 |
| c_base (live Apex) | 20/15/10/05, caps .50 | 66.6 | 65.2 | 0 | 1.00 |
| c11_cap60 | caps .60 | 68.4 | 68.6 | −2.60 | 0.94 |

Same winner class as v71: **selectivity on the over-supplied tier**. `c11_cap60`
re-confirms the capital-velocity law on the densest substrate yet (wider cap
hurts BOTH axes). The two distinct top mechanisms — uniform shrink
(`c08_alloc065`) vs targeted-selectivity (`c04_low03_mid08`) — carried to C/D.

## Phase C (N=300 × 8 incl 2020_crash) — top-5 + base

| cand | dd5y | dd22n | ddΔ | med5y/base | ddCrash |
|---|---|---|---|---|---|
| c08_alloc065 | 59.5 | 59.9 | +6.60 | 0.95 | 63.4 |
| c14_prf_dd | 59.7 | 59.7 | +6.60 | 0.72 | 67.6 |
| c01_low02 | 61.3 | 60.6 | +5.35 | 0.77 | 69.5 |
| **c04_low03_mid08** | 61.7 | 60.6 | +5.15 | **1.02** | 69.2 |
| c06_topheavy_trim | 61.7 | 61.9 | +4.50 | 1.03 | 68.7 |

`c14_prf_dd` and `c01_low02` give back compound (ratio 0.72 / 0.77) — the
earlier-DD-band and aggressive-low-cut levers shave DD but cost the
compounding engine. `c08` (bigger DD cut, ratio 0.95) and `c04_low03_mid08`
(Pareto, ratio 1.02) advanced to the ship gate.

## Phase D ship gate (N=500 × 10 windows incl 2020 + 2020_crash, paired seeds)

`c_base` (live) vs the two finalists. **Both pass T1-T7; collapse=0 on every
cell of all three arms.**

**c08_alloc065** (all tiers ×0.65 → 0.13/0.0975/0.065/0.0325):

| window | base dd | c08 dd | ddΔ | med ratio |
|---|---|---|---|---|
| 5y | 66.3 | **59.5** | **+6.8** | 0.97 |
| 22-now | 66.9 | 59.9 | +7.0 | 0.69 |
| 2024 | 35.0 | 23.7 | +11.3 | **0.53** |
| dip | 42.5 | 30.3 | +12.2 | 0.61 |

**c04_low03_mid08** (low 0.05→0.03, mid 0.10→0.08) — **SHIPPED**:

| window | base med/dd | c04 med/dd | ddΔ |
|---|---|---|---|
| 2020_crash | −44% / 70.0 | −49% / 69.2 | +0.8 |
| 2020 | +32% / 71.0 | −7% / 70.3 | +0.7 |
| 2021 | −34% / 64.6 | −12% / 57.8 | +6.8 |
| 2022 | −24% / 61.4 | −12% / 58.1 | +3.3 |
| 2023 | −40% / 61.8 | −36% / 59.2 | +2.6 |
| 2024 | +1,320% / 35.0 | +815% / 27.6 | +7.4 |
| 2025 | +72% / 63.5 | +68% / 54.8 | +8.7 |
| dip | +194% / 42.5 | +140% / 36.1 | +6.4 |
| 22-now | +1,522% / 66.9 | +1,166% / 61.5 | +5.4 |
| **5y** | +1,280% / 66.3 | **+1,297% / 61.7** | **+4.6** |

T4 PASS (−4.6pp, well under the +1.0 tolerance) · T5 PASS (**every window's DD
improves** — zero regressions, worst +0.7pp) · T6 PASS (collapse=0 all 10 ×
both arms) · T7 PASS (5y compound +1%, same OOM).

## Decision: c04_low03_mid08 over c08_alloc065 (Apex objective)

Both are Pareto over base. The fork is the return/DD tradeoff between them:

- **c08** cuts 5y DD 2.2pp more (59.5 vs 61.7) but is the **uniform alloc
  scale-down** — the documented `c03_alloc050`-class velocity-loss signature:
  it shrinks ALL slugs (incl the high-conviction ultra/top) and badly damages
  the bull-window compounding engine (2024 ×0.53, 22-now ×0.69).
- **c04** is the **targeted selectivity** lever — squeezes ONLY the
  over-supplied 75-84 tiers (low 0.05→0.03, mid 0.10→0.08), leaves ultra/top
  full. It is the true Pareto (5y DD −4.6pp AND compound +1%) and has the
  **highest 5y median of the three** (1297 > 1280 base > 1241 c08).

The Apex objective is `max cost-adjusted return s.t. collapse=0, DD reported as
a budget` (process.md risk-budget ethos). c04 maximizes return while still
cutting DD; c08's extra DD shave is a Core/Sentinel preservation trade that
costs the engine Apex exists to run. 61.7% 5y DD is well inside Apex's 80-90%
recoverable budget. c04 is also the direct lineage of the v71 winner c14 (which
was likewise chosen as the Pareto, not the max-DD-cut), and the natural reading
of the v73 substrate: the trio-removal restored signal across 75-94, so trim
both the 75-79 (low) AND 80-84 (mid) over-supplied tiers, not the conviction
tiers.

## SHIPPED 2026-06-12 (portfolio-only, NO ALGORITHM_VERSION bump)

`STRATEGY_30DTE.TIER_ALLOC`: `low 0.05 → 0.03`, `mid 0.10 → 0.08`;
`portfolio_profiles.json` apex: `tier_low 0.03`, `tier_mid 0.08` (version 1→2).
Core/Sentinel untouched. Drift-guard 645 / registry / dte-audit green; `trader
alloc` displays the new values (80-84 $1,138, 75-79 $427 at $50k). Overflow
stays 0; puts stay off. temporal-refresh + research-pack rebuild queued at ship.

**Watch metric (FLAG-teeth, from the v73 ship adjudication):** post-ship 5y
assess 85+/80+ option-TP15. Baseline at ship: 85-89 51.2% (N=205), 80-84 49.5%
(N=1,146), 75-79 51.8% (N=3,861) — confirm the ReSim-predicted 85-89 dip stays
noise on real rows.
