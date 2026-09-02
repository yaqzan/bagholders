# Viral claim triage: "Buy stocks when VIX is 30 ($45+), sell when VIX is 14, repeat — undefeated, ensures you outperform the S&P 500 year after year"

**Date:** 2026-07-26 · **Verdict: CLAIM FALSE (the "undefeated" kernel is real but the outperformance claim inverts).** Read-only, no MC, ~15 min. Harness: `backtest.py` (literal close-only state machine on `MarketRegime.vix_close` / `spy_close`, 1995-01-03..2026-07-24, N=8,042 sessions; SPY series is the adjusted close, so both arms are total-return-comparable; cash at 0% and 3%/yr variants).

## Result (buy VIX≥30 / sell VIX≤14, cash 0%)

| | Strategy | Buy & Hold |
|---|---|---|
| Multiple (31.5y) | **x5.58** | **x27.95** |
| CAGR | 5.60% | 11.13% |
| Max drawdown | **−60.4%** | −55.2% |
| Time in market | 54.6% | 100% |
| Years underperforming SPY | **17 of 31** | — |

- **All 8 completed cycles positive** (the "undefeated" kernel) — but that is near-tautological: VIX≤14 essentially only occurs deep into extended bull markets, so the exit is structurally conditioned on recovery having already happened. It is the G52 class of self-confirming definition, at N=8.
- The win is bought with **multi-year bag-holds**: 1997→2004 (6.7y), 2007→2012 (5.0y), 2020→2023 (3.3y). Buying at VIX 30 enters crashes *early* (Aug-2007, Feb-2020) and rides the full collapse — hence maxDD **worse than buy-and-hold** despite being in the market only 55% of the time.
- The fatal cost is the **calm-grind years missed entirely in cash**: 2013 (+32.3%), 2017 (+21.7%), 1996 (+22.5%), 2006 (+15.8%), 2019, 2024. Low VIX is not "time to be out" — it is the market's highest-quality compounding state.
- VIX≥45 variant is worse (x3.90, CAGR 4.41%, 4 cycles). 3%/yr cash yield narrows nothing material (7.04% vs 11.13%).

## Consistency with the repo's evidence

The claim's kernel matches our own findings — buying panic is positive-EV per-trade (G19 buy-weakness; RXDD: VIX extremes are mean-reversion winner cohorts; the shipped lever contracts the *mid* 20-28 band, never the extremes). What the claim gets wrong is the same thing every "cut calls in collapse" idea gets wrong, mirrored: per-cycle WR ≠ portfolio outperformance; the binding constraint is time-in-market / capital velocity (G16), and exiting on low VIX systematically surrenders the best compounding regime. VIX timing beyond level-band *sizing* is a closed axis here (G22/G28/G46).

**Do not re-test.** Any future "VIX threshold cycle" claim: rerun `backtest.py` with the new thresholds (~30s).
