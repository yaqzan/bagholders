# Cascade Re-Sweep Session — 2026-04-17

Run after v18 shipped (asymmetric MACD gate + asymmetric weekly 1.5× puts + put cascade 15/12/12). Current locked production cascade (15/12/12/5 @ MaxPos=14) was validated on v17 pre-gate.

## TL;DR — three independent levers, combinable

| Change | Mechanism | 22-now uplift | DD-C effect |
|---|---|---:|---:|
| Drop 70-74 overflow (0% instead of 5%) | Frees slot capacity for higher-conviction signals; reduces correlated-DD | +13% | −9.8pp (fixes breach) |
| Flatten 80-84 and 75-79 to 15% (from 12%) | Larger per-trade stake on mid/low tiers | +130% | +5.9pp |
| Split 95+ into its own 25% tier | 95+ WR15=90% deserves higher weight than merged 85-94 bucket | +50% on top | −4pp |

**Combined candidate: `ULTRA=25 / TOP=15 / MID=15 / LOW=15 / OVR=0 @ MaxPos=14`**

| Metric | Production (locked) | Candidate | Change |
|---|---:|---:|---:|
| 2021 Real | +47,676% | +129,941% | +2.7× |
| 2022 (bear) Real | +1,278% | +4,960% | **+3.9×** |
| 2023 Real | +1,743% | +1,071% | −38% |
| 2024 Real | +18,124% | +60,088% | +3.2× |
| 2025 Real | +10,487% | +4,021% | −62% |
| **22-now Real** | **+944M%** | **+3,636M%** | **+3.9×** |
| **22-now DD-C** | **83.3% ✗** | **75.2% ✓** | **−8.1pp** |
| 2025 DD-C | 83.0% ✗ | 74.1% ✓ | −8.9pp |

Production breaches the 80% Conservative DD floor on 2025 AND 22-now. The candidate fixes both.

## Trade-off

Single-year regressions in 2023 (-38%) and 2025 (-62%) are real. Both are lower-call-density years where overflow-tier and fatter mid/low allocations would normally add volume. The removal hurts volume-sensitive years but the correlated-DD reduction + compounded bull-year uplift more than compensate on 22-now.

## Bounds validated

- Overflow ≥ 2% **breaches DD floor** (Phase 8) — overflow must be 0
- Ultra ≥ 28% **breaches DD floor** — ultra ceiling is 25-27%
- MaxPos 12 vs 14 is **irrelevant** when overflow=0 (cash binds first; B≡E and C≡F in Phase 6c)

## Ship requirements

Code-level: monte_carlo.py constants only. No scoring.py change, no new AlgorithmVersion.
```python
TIER_ALLOC = {'ultra': 0.25, 'top': 0.15, 'mid': 0.15, 'low': 0.15, 'overflow': 0.00}
# add 'ultra' branch to score_to_tier (>=95)
```
Also apply to `backtest_cascade.py` for historical replay parity.

## Scripts

- `experiments/bayes_phase6_call_cascade_v18.py` — initial Bayesian sweep, converged on 15/15/15/0 @ MP12
- `experiments/bayes_phase6b_validate.py` — 3-mode × N=500 validation of Phase 6 winner
- `experiments/bayes_phase6c_decompose.py` — 6-variant decomposition isolating each lever
- `experiments/bayes_phase7_95plus_split.py` — 95+ tier split sweep (6 variants)
- `experiments/bayes_phase8_fine_tune.py` — ultra-magnitude and overflow-restore sensitivity
