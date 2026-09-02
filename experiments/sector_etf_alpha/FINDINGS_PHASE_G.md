# Sector ETF Alpha — Phase G DD-Aware Refinement

**Date:** 2026-05-11  
**Baseline substrate:** v46 cached cohort (`.cache/sector_etf_alpha/cohort_v46_1825.parquet`)  
**Goal:** preserve the sector ETF / stock-vs-sector alpha while eliminating the V4 correlated-DD failure.

## Starting Point

V4 had real Stage 1 WR7 alpha, but smoke MC showed a portfolio failure: oversold-sector calls were lifted together into high allocation tiers, creating same-day single-factor exposure. Phase G therefore searched variants that penalize:

- 90+ call population expansion
- 85+ call population expansion
- same-day clusters of promotions into 85+
- low-sector-RSI promotions into higher tiers

No production scoring code was modified.

## Search Design

New script: `phase_g_dd_aware.py`

Architectures searched:

| Arch | Description |
|---|---|
| G1 capped stack | SEC + RSU, but sector call-lift target capped below 85 |
| G2 dampen-only stack | SEC + RSU with sector upward call lift disabled |
| G3 RSU only | standalone stock-vs-sector relative-strength U-curve |
| G4 SEC down-only | sector phase dampener only; no RSU and no upward call lift |
| G5 limited-lift stack | allows small sector-oversold call lift near the 85 boundary |

Run:

```bash
python experiments/sector_etf_alpha/phase_g_dd_aware.py --lhs 160 --drill 120 --top 12
```

Artifacts:

- `phase_g_all.csv`
- `phase_g_top.csv`
- `phase_g_winners.json`
- `smoke_mc_variant.py`
- `.cache/sector_etf_alpha/G01_G4_SEC_down_only_lookup.pkl`
- `.cache/sector_etf_alpha/G02_G4_SEC_down_only_lookup.pkl`

## Stage 1 / Proxy Result

The DD-aware objective converged entirely to **G4_SEC_down_only**. That is the important architectural result: once the correlated-promotion penalty is included, every stacked/lift architecture loses to pure sector down-dampening.

Top candidate:

| Metric | G01 |
|---|---:|
| stage1_util | +0.516 |
| W4 / W5 / W6 | 0 / 0 / 0 |
| call 90+ dWR7 / dN | 0.00pp / 0.0% |
| call 85+ dWR7 / dN | 0.00pp / 0.0% |
| call 80+ dWR7 / dWR30 | -0.17pp / -0.59pp |
| call 75+ dWR7 | +0.32pp |
| put <=20 dWR7 | +0.23pp |
| 85+ promotions | 0 |
| 90+ promotions | 0 |

Second candidate is a lighter G4 variant:

| Metric | G02 |
|---|---:|
| stage1_util | +0.525 |
| W4 / W5 / W6 | 0 / 0 / 0 |
| call 80+ dWR7 / dWR30 | -0.06pp / -0.52pp |
| call 75+ dWR7 | +0.22pp |
| put <=20 dWR7 | +0.21pp |
| 85+ / 90+ promotions | 0 / 0 |

## Smoke MC

N=120 smoke checks, single-process, using runtime patch only.

### G01 — stronger down-only

| Window | Baseline WorstDD | G01 WorstDD | dWorstDD | Baseline MeanDD | G01 MeanDD | Return Ratio |
|---|---:|---:|---:|---:|---:|---:|
| 2024 | 58.0% | 56.9% | -1.1pp | 33.9% | 36.3% | 0.29x |
| 5y | 71.3% | 69.1% | -2.2pp | 63.1% | 63.5% | 0.23x |

Verdict: fixes tail DD, but loses too much compound/velocity.

### G02 — lighter down-only

| Window | Baseline WorstDD | G02 WorstDD | dWorstDD | Baseline MeanDD | G02 MeanDD | Return Ratio |
|---|---:|---:|---:|---:|---:|---:|
| 2024 | 58.0% | 58.3% | +0.3pp | 33.9% | 35.1% | 0.28x |
| 2022 | 66.9% | 67.8% | +0.9pp | 49.0% | 49.4% | 1.24x |
| 22-now | 69.0% | 68.9% | -0.1pp | 63.4% | 63.4% | 0.53x |
| 5y | 71.3% | 69.9% | -1.4pp | 63.1% | 63.4% | 12.6x |

Verdict: not robust. 5y improves, but 2024 velocity loss and 2022 DD regression block a ship candidate.

## Conclusion

**No ship candidate yet.**

The meaningful Phase G finding is architectural:

1. The original alpha is real, but the sector-oversold upward-lift branch is what creates the DD problem.
2. Removing upward lift fixes the promotion/clustering proxy.
3. Pure dampening leaves only a small Stage 1 edge and becomes a portfolio tradeoff: some DD improvement, but not stable enough across windows and too costly in bull-year velocity.

## Recommended Next Refinement

Do not keep tuning SEC upward lift. It repeatedly collapses into the original correlated-promotion problem.

The next Bayesian pass should isolate **stock-vs-sector relative strength as a dampener-only mechanism**, but with a different target shape than the current symmetric RSU:

- calls only at first
- no upward lifts
- no put changes
- fire only on 75-84 calls, because 85+/90+ tiers should remain untouched
- objective should include direct MC-smoke terms for 2024 and 5y, not just a cohort proxy

This targets the z=-9.58 `stock_rs_5d` signal without sector-phase clustering.
