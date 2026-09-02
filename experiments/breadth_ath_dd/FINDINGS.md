# %-at-ATH breadth-extreme as a call-DD / capture-risk lever — NULL (inverted + redundant)

**Date:** 2026-06-26 (Fri pre-open, /research). **Outcome: NO SHIP — clean null.**
Read-only mine on the v70/v71 Apex MC tape (6.37M call trades), no MC, no recalc.

## The ask (Bagholder feedback)
"~4% at highs + 85% underperforming = froth-at-index / narrow-leadership tape that murders
capture ratio. Want a breadth+VIX multiplier de-rating long-call conviction HARD until breadth
broadens." i.e. **LOW %-at-ATH (narrow leadership) → de-rate calls.** Logarithmic predictor of
drawdown/pullback/call-risk from % of stocks at all-time-highs.

## Why this slice (G40 triage)
The "de-rate calls when breadth narrows / froth-at-highs" idea is ALREADY the live Apex sizing
stack: **BDIV** (SPY-near-60d-high × breadth-rolling-over, 2026-06-11), **MWDD** (McClellan
flat/topping band, 2026-06-05), **F3F** (breadth LEVEL), **RXDD** (VIX 20-28). The regime-
MULTIPLIER path the feedback names is a documented no-op (regime amplifies on weak breadth for
WR-reliability, not DD). NH/NL (new-52w-highs) FAILED the orthogonal slice (G23). The ONE
genuinely-untested metric: **% at ALL-TIME-HIGH** (strictly stronger than 52w-high). It is NOT
in the breadth system, so I built it from PriceHistory: % of the sectored universe within 0.5/2/5%
of expanding all-time-max close, by date (cached `.cache/breadth_ath_pct.parquet`; median
pct_ath0 = 5.0, so "~4% at highs" is NORMAL, not an extreme).

## Result — triple-confirmed, the hypothesis is INVERTED

**1. Full-tape call-EV is MONOTONIC DECREASING in %-at-ATH (opposite of the ask):**

| pct at ATH (within 0.5%) | n | loser-rate | mean opt pnl |
|---|---:|---:|---:|
| **<2% ("narrow ~4% tape")** | 1.31M | 0.269 | **+0.091** (best) |
| 2-5% | 1.65M | 0.288 | +0.061 |
| 5-10% | 1.23M | 0.317 | +0.026 |
| **10-18% (broad)** | 0.26M | 0.361 | **−0.017** (worst) |

The FEWER stocks at ATH, the BETTER our calls do. Narrow leadership = beaten-down/oversold tape
= our buy-weakness Apex calls mean-revert and WIN (the G19/G3 inversion). Broad froth = extended/
euphoric = less upside, more failures. The feedback's intuition is correct for an *unlevered
equity holder*, INVERTED for a buy-weakness leveraged-momentum options sleeve.

**2. DD-active subset (entry_dd≥0.13):** same monotone — `<2%` ATH +0.077 (best), `10-18%` −0.060
(worst). Low-%-at-ATH is NOT a DD driver; it's the lowest dd_conc winner.

**3. Per-window:** the low-%-at-ATH ("few at highs") cohort is positive-EV in 7/9 windows; mildly
low-EV only in 2022/2023 bear/chop — already covered by BDIV/MWDD/SVR. A WINNER almost everywhere.

**4a. Orthogonal slice (vix<20 & breadth≥40 & |mcc|>22 = RXDD+F3F+MWDD off):** `<2%` band +0.147
(best, loser-rate 0.200). Where every shipped lever is off, low-%-at-ATH is a strong winner.

**4b. G44 2×2 vs MWDD's firing band — the redundancy tell:** whatever weak-EV exists in the
pct_ath bands is concentrated where MWDD ALREADY fires; it INVERTS to good where MWDD is off:

| cohort | MWDD ON (flat) | MWDD off |
|---|---:|---:|
| ath 2-5% | +0.041 (low-EV) | **+0.089 (good)** |
| ath 5-10% | +0.008 (low-EV) | **+0.064 (good)** |

= the candidate's low-EV is MWDD's axis re-labeled (G44 redundancy signature). The only genuine
low-EV residual is the HIGH band (10-18% at ATH, broad froth) in strong momentum (−0.087, n=73k) —
the OPPOSITE direction from the ask, and too thin/regime-specific to be a lever.

## Verdict
The feedback is **already addressed** by the live sizing stack (BDIV+MWDD+F3F+RXDD), and the
specific %-at-ATH metric it implies is (a) directionally INVERTED for our sleeve and (b) redundant
with MWDD. The regime-multiplier de-rating it asks for would HURT (weak breadth is our higher-EV
call state). NO SHIP. Substrate caveat: v70/v71 tape, but the buy-weakness inversion (z=−65,
monotone, every window) is the core Apex edge — substrate-robust v70→v74, no fresh tape needed.

Harness: `experiments/breadth_ath_dd/{mine.py,mine_report.json}` + `.cache/breadth_ath_pct.parquet`.
