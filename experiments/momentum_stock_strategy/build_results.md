# Momentum Stock Strategy — Honest Reckoning vs "Just Buy MTUM"

**Date:** 2026-06-01 · **Scoring version:** v69 (active, `version_id=69`, commit `8b59206c3`, the
first look-ahead-free version) · **Research only — no scoring/production change, no commits.**

## The question

Does the v69 directional signal support a fundable momentum **STOCK** strategy that beats SPY
*and* a pure-momentum benchmark on a risk-adjusted, **cost- and tax-aware** basis — or is the
honest answer "just buy momentum (MTUM-style)"?

MTUM is not in the DB, so the bar to beat is built directly: a top-decile trailing-12-1-month
return basket, equal-weight, monthly rebalance (exactly the construction MTUM tracks). This is
construction **B**.

## Framework

One calendar-time daily-return series per construction (so Sharpe / max-DD / factor-regression
are all comparable). Universe = `sector IS NOT NULL` (765 names), 2021-01-01 → 2026-05-29 (~5.4y).

- **A** — v69 75+ score portfolio, 15-day holds, equal weight.
- **B** — PURE MOMENTUM BENCHMARK: top-decile 12-1mo return, equal-weight, monthly rebalance. **The bar to beat.**
- **C** — score-tilt-within-momentum: the v69 75+ names that ALSO sit in the top decile of momentum at entry.
- **C2** — the complement: v69 75+ names that are NOT in top-decile momentum (a non-momentum pocket?).
- **D** — momentum-coverage baseline: top-decile momentum names that the system scores at all (isolates "momentum names covered" from "the v69 signal").

**Frictions:** per-name round-trip bid-ask from market cap (mega ~2 bps → micro ~40 bps), charged on
daily turnover; then short-term-gains tax = 30% of net positive annual return (15-day holds = ordinary
income). Turnover is made explicit: A turns **18.6×/yr**, momentum **~3×/yr** — the tax asymmetry is the story.

## Results — construction returns

| construction | annGross | annNet | **annAfterTax** | Sharpe | maxDD | avgN | cost/yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: v69 75+ (15d hold) | +43.1% | +41.4% | **+29.0%** | 1.41 | 28.4% | 18 | 1.21% |
| **B: PURE MOM top-decile** | **+64.3%** | **+63.7%** | **+44.6%** | **1.49** | 36.1% | 66 | 0.39% |
| C: 75+ AND high-mom | +43.3% | +40.9% | +28.6% | 0.90 | 53.6% | 5 | 1.66% |
| C2: 75+ NOT high-mom | +27.8% | +26.4% | +18.5% | 1.04 | 39.3% | 15 | 1.11% |
| D: mom-cov top-decile | +46.7% | +46.1% | +32.3% | 1.18 | 36.1% | 67 | 0.40% |
| SPY | +16.2% | +16.2% | +11.4% | 0.97 | 25.4% | — | — |

## Results — factor regressions

**Reg 1 — net ~ α + b_mkt·SPY + b_mom·MOM-factor** (top-quintile-126d momentum factor):

| construction | αAnn | α_t | mkt-β | **mom-β** | mom-t | R² |
|---|---:|---:|---:|---:|---:|---:|
| A: v69 75+ | +12.2% | **1.43** | 0.42 | **0.49** | 18.6 | 0.54 |
| B: PURE MOM | −2.1% | −0.37 | −0.02 | 1.23 | 64.6 | 0.86 |
| C: 75+ AND high-mom | −4.2% | −0.22 | −0.01 | 1.18 | 19.0 | 0.40 |
| C2: 75+ NOT high-mom | +6.2% | 0.71 | 0.53 | 0.32 | 11.8 | 0.44 |
| D: mom-cov top-decile | −3.5% | −0.61 | 0.01 | 1.24 | 63.2 | 0.87 |

**Reg 2 (DECISIVE) — net ~ α + b_mkt·SPY + b_B·BENCHMARK-B** (α = return *beyond the momentum basket you'd actually buy*):

| construction | **αAnn vs B** | **α_t** | mkt-β | B-β | B-t | verdict |
|---|---:|---:|---:|---:|---:|---|
| A: v69 75+ | +15.6% | **1.79** | 0.52 | 0.33 | 17.9 | pos, **not sig** |
| C: 75+ AND high-mom | +2.5% | 0.13 | 0.12 | 0.87 | 20.4 | pos, not sig |
| C2: 75+ NOT high-mom | +9.1% | 1.02 | 0.65 | 0.18 | 9.4 | pos, not sig |
| D: mom-cov top-decile | +0.1% | 0.41 | 0.00 | 1.00 | — | pos, not sig |

## The decisive answer

**NO construction beats the pure-momentum benchmark with statistically significant (t>2) alpha
after cost.** The best candidate is A at +15.6%/yr vs B, but **t=1.79 < 2** (p≈0.07). Among
high-momentum names the score adds *nothing* (C: α vs B = +2.5%, t=0.13). The score's entire
edge over SPY is the **momentum factor** (A's mom-β=0.49 at t=18.6; the +12.2% factor-alpha is
t=1.43, not significant) — identical to the prior `stock_vehicle_test.py` / `stock_alpha_sweep.py`
conclusion.

**After-tax horse race (what a taxable trader keeps):** B +44.6% > D +32.3% > A +29.0% > C +28.6%
> C2 +18.5% > SPY +11.4%. **The pure momentum benchmark wins outright.** A's 15-day holds (18.6×
turnover) bleed 12.4pp to short-term tax (41.4% net → 29.0%), while B's monthly rebalance (~3×
turnover) is far more tax-efficient — and B starts higher anyway.

## Robustness (`robustness.py`)

1. **Benchmark not cherry-picked.** All 12 momentum specs (12-1mo / 6mo / 12mo × decile/quintile ×
   monthly/quarterly) return **+30% to +51% after-tax** — every one beats construction A's +29%.
   The default 12-1 decile monthly (+44.6%) is mid-pack; strongest is 6mo-decile-monthly (+50.9%).
2. **Turnover honesty.** A = 18.6×/yr (matches the stated ~17×), momentum ~3×/yr — the tax-drag
   asymmetry is real and structural, not an artifact.
3. **A's residual alpha is insignificant AND unstable:** +6.6%/yr (t=0.66) in-sample ≤2024,
   +29.3%/yr (t=1.81) in 2025+. The 2025 jump is suggestive but still sub-t=2, and an unstable
   residual is more consistent with noise/regime than a durable edge.
4. **A's alpha doesn't survive the strongest momentum bar** (6mo-decile-monthly): α=+13.2%/yr,
   **t=1.52**, bench-β 0.33 (t=17.5). Still not significant.

## Honest verdict

**"Just buy momentum" is the honest answer.** The v69 stock vehicle beats SPY, but only because it
*is* a (worse, higher-turnover, less tax-efficient) momentum strategy. A pure top-decile momentum
basket — the thing MTUM gives you for an ETF fee with low turnover and long-term-gains tax treatment
— delivers a higher after-tax return (+44.6% vs +29.0%/yr), a higher Sharpe (1.49 vs 1.41), at the
cost of slightly more drawdown (36% vs 28%).

The score's one genuinely positive property is **lower max drawdown (28% vs B's 36%)** at a
comparable Sharpe — the 15-day-hold turnover keeps it from concentrating into a few extended-momentum
names (C, the concentrated 5-name tilt, blows up to 54% DD and Sharpe 0.90, confirming concentration
is the DD risk). But that DD edge is not worth a 15.6pp after-tax return give-up, and it is *not*
statistically-significant alpha — it is a diversification artifact of holding more names for shorter.

This is fully consistent with the OPTIONS conclusion (dead — spreads) and the prior STOCK conclusion
(mostly momentum, residual alpha t=1.35 not significant). Adding the explicit tax layer makes the
case *stronger*, not weaker: the score strategy's high turnover is a tax liability that momentum's
low turnover avoids. **No fundable edge beyond momentum. Buy MTUM (or a top-decile 12-1 basket).**

## Files

- `backtest.py` — main calendar-time backtest (A/B/C/C2/D + SPY, both factor regressions, cost+tax). → `results.json`
- `robustness.py` — benchmark-spec sweep, turnover audit, in-sample/recent alpha split, A-vs-strongest-bar.
- `results.json` — structured metrics.
