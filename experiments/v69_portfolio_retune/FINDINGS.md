# v69 Honest-Substrate Portfolio Retune + SPY Reality Check (2026-05-31)

After v69 removed the weekly look-ahead (first honest, point-in-time-safe scoring
version), the portfolio params (tuned on the inflated ~63% call edge) were
re-validated and retuned on the honest ~46-52% edge. **Headline: the live params
COLLAPSE on honest scores; the retune fixes that; but the honest strategy does NOT
beat buy-and-hold SPY after realistic costs.** Shipped as system hygiene, not a
capital-allocation recommendation.

## The retune (Stage A -> B -> selectivity -> C)

Harness: `driver.py` (subprocess MC, correct TP/SL re-derivation per candidate).

- **Stage A (sizing):** current params COLLAPSE on honest v69 (5y/22-now 100%
  collapse, 92% DD) — over-leverage + net-negative puts. Calls-lean downsized
  configs survive (0 collapse). Honest call TP ~46% (marginal). Puts net-negative;
  a 10% put sleeve made everything WORSE (the bear-hedge value was itself
  look-ahead). `retune_stageA.py`.
- **Stage B (barriers):** lever is **TP_BASE 0.33 -> 0.28** (capital velocity on a
  thin edge): 5y -38% -> -1% at 75+. **Wider SL HURT** (user hypothesis wrong);
  velocity wins. Puts unrescuable (every put-barrier config collapsed; wider PUT_TP
  made puts worse). `retune_stageB.py`.
- **Selectivity (the decisive lever):** the honest edge lives ENTIRELY in **85+**
  (TP 52.2% > 49.1% BE at TP0.28/SL0.27). 80-84 (48.3%) and 75-79 (~46%) drag below
  BE. `sel80` 5y -52% vs `sel85` +86%. Dropping 75-84 rescues EVERY losing year
  (2021 -42% -> +5%, 2022 -24% -> +13%, 2023 -20% -> +2%). `retune_selectivity.py`.
- **Stage C (N=300) + N=500 ship confirm:** 85+ only, sizing sweep. Shipped
  **C2_cs65** (ULTRA 0.13 / TOP 0.0975, puts off, MaxPos 8/7/2, TP0.28):
  **N=500: 5y +130% / DD 35%, 22-now +109%, 0 collapse, 7/8 windows +, PTr=0.**
  `retune_stageC.py`, `ship_confirm.py`.

## The 3 diagnostics — does it beat SPY / is it worth mining? (NO)

Actual SPY from DB: **5y +101% (~14%/yr, ~25% DD); 22-now +56%.**

- **D1 (factor attribution, `diag1_3_signal.py`):** 85+ stocks' 15d fwd return
  +2.47% raw, **+1.03% market-adjusted but t=1.25 (NOT significant)**. Names are
  high-momentum (+34.8% trailing 6m). The stock-selection alpha is real in sign but
  statistically indistinguishable from zero -> mostly beta + momentum.
- **D3 (2022 robustness):** 2022 *underlying* signal **LOST money** (-1.06% / 15d,
  market-adj -0.84%); only 22/45 symbols positive. The strategy's 2022 "+25%" was
  option CONVEXITY on ~3 commodity/inverse names (AR +64% x4, SQM, LABD 3x-inverse).
  **Crisis alpha = convexity luck, not repeatable skill.**
- **D2 (realistic cost, `diag2_cost.py`):** per-trade gross edge is only ~+1.7% of
  premium. Modeling options bid-ask spread (cross half each way):

  | round-trip spread | 5y | 22-now |
  |---|---|---|
  | 0% (MC fantasy) | +212% | +172% |
  | 2.5% (liquid-only) | **+42%** | +63% |
  | 5% (realistic) | **-45%** | -7% |
  | 10% (illiquid) | -67% | -60% |

  Break-even ~3% spread. **Liquidity profile: 25% of 85+ signals are small/micro-cap
  (~10-15% spreads); blended ~6%.** At realistic cost the strategy LOSES; even
  liquid-only (+42%/5.3y = ~7%/yr at 59% DD) is worse than SPY on both axes.

## Verdict

**The MC's eye-popping returns lived entirely on a $0-spread assumption that is
false for options.** After realistic spreads (and before the worse short-term-gain
tax treatment), this strategy does not beat buy-and-hold SPY at any sizing. The big
edge was look-ahead (removed in v69); the residual is thin momentum-beta + convexity
luck. **Recommendation: hold SPY (or MTUM). Do not fund this. Do not open-endedly
mine — clearing a ~6% option-cost hurdle needs the directional edge to ~triple, and
the prior on that is low.** This is a valuable NEGATIVE result: it converts a fake
edge into ground truth and the diagnostics (factor attribution + cost modeling) are
reusable to vet any future idea before risking capital.

## Shipped (Stage 2+3 portfolio, NO ALGORITHM_VERSION bump)

`strategy_config.py STRATEGY_30DTE`: TIER_ALLOC ultra 0.20->0.13 / top 0.15->0.0975 /
mid 0.10->0 / low 0.10->0 ; PUT_TIER_ALLOC all->0 ; MAX_POSITIONS 14->8 / call 12->7 /
put 8->2. `OPT_30DTE.TP_BASE` 0.33->0.28. Plus `monte_carlo.py` SLIP_*_OV env knobs
(research infra for the cost diagnostic). Drift-guard 579 / registry 138 / dte-audit
all pass. Hygiene only.
