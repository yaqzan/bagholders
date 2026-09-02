## Stock Detail Page — TP/SL Projections (PriceChart)

Price chart on the stock detail page shows projected TP/SL targets based on the highest score in the last 60 days, using assessment MAE/MFE stats scaled to realized volatility.

### Key features

1. **Signal indicator**: colored dot at the highest score's date. Date = most extreme score (>=70 or <=25) in last 60 days. Color: green = call (score>=50), red = put (<50). Price = close on that date.
2. **TP/SL projection lines**: dashed lines at 7d/15d/30d/60d DTEs. Green (TP) uses `mfe_sigma_p25` (conservative 25% target). Red (SL) uses `avg_mae_winner_sigma` (avg adverse move for winners). Opacity gradient 7d 0.35 -> 60d 0.1.
3. **Price labels**: exact TP/SL price at each DTE, positioned at matching Y/date X coordinates. Green=TP, red=SL, white=current price.

### Calculation

Entry price = close on signal date.

TP (calls only): `TP = entry_price * (1 + mfe_sigma_p25 * periodScale * realized_vol / 100)`, `periodScale = sqrt(days/30)`

SL (both): `SL = entry_price * (1 +/- abs(mae_sigma * periodScale * realized_vol) / 100)` (minus for calls, plus for puts)

Example values (90+ bucket, 30d): `mfe_sigma_p25`=2.05sigma, `avg_mae_winner_sigma`=-1.2sigma, `realized_vol`=60-day realized vol %.

### Signal selection

Highest score in last 60 days across both call (>=70) and put (<=25) signals; for calls take max, for puts take min; if both exist take whichever is furthest from 50.

### Rendering order

TP/SL computed in PriceChart (not StockDetail, needs price history) -> lines rendered first (background) -> price labels last (foreground) -> signal dot on top.

### Data flow

StockDetail finds highest score in last 60d -> passes signalDate/signalScore/assessmentData/realizedVol to PriceChart -> PriceChart looks up signal candle (entry price) -> calculates TP/SL per DTE from assessment buckets -> renders lines + labels + dot.

---

## Options P&L Analysis — Historical heuristic (v14-derived)

> Historical heuristic (v14-derived): the +25% TP / -50% hard-sell rule-of-thumb below is kept for rationale, not as live strategy parameters. Live exit logic is in `strategy_config.py` (OPT_30DTE / SHARED_OPTION). The sigma/vol mechanics above (mfe_sigma_p25, avg_mae_winner_sigma scaled to realized vol, IV-adjusted pricing) remain current.

Derived from v14 assessment data (5y lookback, ~80k peaks). Sigma-invariant: P&L% is the same regardless of stock volatility since both the underlying move and premium scale with sigma.

### ATM option premiums at entry

```
15 DTE ATM call premium ~= 1.29 x sigma_daily  (% of stock price)
30 DTE ATM call premium ~= 1.82 x sigma_daily
```
sigma_daily = 60-day realized daily stdev %.

### Empirical validation (2026-04-23, `experiments/option_price_assessment.py`)

Dataset: 194,012 ATM options (<3% moneyness, vol>=10, IV>0.01, DTE 7-40d), 117,968 calls + 76,044 puts, Feb-Apr 2025, `option_prices` joined to 60d realized vol.

Formula accuracy vs actual market prices (calls, mean actual/sigma):

| DTE Band | Actual mean | Formula | Error |
|---|---|---|---|
| 7-10d | 3.11%xS | 1.29xsigma=~4.00% | +29% overshoot |
| 11-14d | 3.88%xS | 1.29xsigma=~4.20% | +8% overshoot |
| 15-17d | 4.49%xS | 1.29xsigma=~4.31% | -4% undershoot |
| 18-22d | 4.84%xS | (interp.) | near-exact |
| 25-35d | 5.73%xS | 1.82xsigma=~5.88% | +3% overshoot |

30 DTE formula accurate to +/-3%; 15 DTE to +/-4%. 7-10d overshoots 29% (not used live, low priority). Puts price ~0.85-0.90x calls at same DTE.

IV context (Feb-Apr 2025): avg annual IV ~50%, annualized 60d RV ~50.8% -> IV premium ~0 in this period; typical market IV exceeds RV by 10-20%. The "15.62x ratio" in script output is apples-to-oranges (annual IV / daily sigma%) — ignore.

**High-IV stock skew**: meme stocks (IV 80-160%, sigma_daily 6-12%) have realized vol > implied vol (mean-reversion premium); option prices only 0.7-1.0x sigma_realized at 30 DTE. RMSE-minimizing Bayesian mults (0.97 for 30d calls, 0.62 for 30d puts) are pulled down by these outliers — do not use them; use mean actual/sigma for typical stocks (matches production formula within +/-5%).

**IV-based formula better**: `premium_pct = k * (IV_annual / sqrt(252)) * sqrt(DTE) * 100` with k=0.50 gives RMSE 3.0% vs 3.5% for best RV formula (16-27% better). Not needed for MC model, relevant for display/dashboard pricing.

**Strategy impact: none.** Live TP/SL targets are % of premium paid, so pricing-formula accuracy doesn't affect P&L calc.

### TODO: IV-adjusted option pricing for high-IV stocks

Goal: when `option_prices.iv` is available for a stock on a signal date, use the IV-based formula instead of flat RV multiplier (corrects overestimation for high-IV stocks, underestimation for low-IV).

Where: (1) `monte_carlo.py`/`backtest_cascade.py` — highest impact. At signal time look up `option_prices.iv` for nearest-DTE ATM option; `premium_pct = 0.50 * (IV_annual/sqrt(252)) * sqrt(30) * 100` when IV available, else fall back to `1.82 * sigma_daily`. (2) Dashboard/DTE Recommendation display — `StockDetail` already gets `realizedVol`; pass IV via API (`/api/stocks/<sym>` -> `latest_iv`) and use `k=0.50 * IV_daily * sqrt(DTE)`.

IV lookup (for monte_carlo.py):
```sql
SELECT iv FROM option_prices op
JOIN options o ON op.option_id = o.id
WHERE o.symbol = sym AND op.date = signal_date
  AND o.option_type = 'call'
  AND DATEDIFF(o.expiration_date, op.date) BETWEEN 25 AND 35
  AND op.volume >= 5
ORDER BY ABS(o.strike_price - stock_close) / stock_close
LIMIT 1
-- If found: premium = 0.50 * (iv/sqrt(252)) * sqrt(30) * close
-- Else: premium = 1.82 * sigma_daily/100 * close
```

Coverage caveat: `option_prices` starts Feb 2025 — pre-2025 backtest signals fall back to RV formula (accurate within +/-5%). IV adjustment mainly helps live trading (2025+).

Priority: medium. Doesn't affect historical backtest validity; improves live sizing accuracy, especially high-IV stocks (currently overshoots up to 3x).

### Assessment win barriers (underlying move needed)

Scale by sqrt(W/30) from the 30d reference (K_call=2.0sigma, K_put=1.0sigma):

| Period | Call win | Put win |
|--------|----------|---------|
| 7d | +0.97sigma | -0.48sigma |
| 15d | +1.41sigma | -0.71sigma |
| 30d | +2.00sigma | -1.00sigma |

### Option P&L at win condition

| Config | Win barrier | Hit early (day 1-3) | Hit late (end) | Avg |
|--------|------------|--------------------|--------------------------|----|
| 15 DTE, 7d window | +0.97sigma | ~+35% | ~+17% | ~+25% |
| 30 DTE, 15d window | +1.41sigma | ~+35% | ~+16% | ~+25% |

### Key finding — MAE winner ~ -50% option value

`avg_mae_winner_sigma` at each period corresponds almost exactly to the option losing ~50% of premium:

| Config | avg_mae_winner_sigma | Option P&L at MAE |
|--------|-----------------|-------------------|
| 80+ calls, 7d | -0.51sigma | -46% |
| 80+ calls, 15d | -0.74sigma | -47% |
| 80+ calls, 30d | -1.05sigma | -48% |

**Critical implication:** a -50% option stop-loss cuts half of eventual winners at their average trough. Don't use -50% as a hard stop; use it only as a time-based hard sell (midway DTE).

### Rule-of-thumb options framework — Historical heuristic (v14-derived)

> Not live exit logic — see `strategy_config.py` (OPT_30DTE/SHARED_OPTION) for current TP/SL. Kept for the underlying-sigma-threshold rationale.

**15 DTE calls (hard sell day 7-8):** TP +25% premium (underlying ~+0.97sigma); SL -60% premium (underlying ~-1.55sigma = 1.5x avg winner MAE); hard sell day 7 accepts -50% on any open position.

**30 DTE calls (hard sell day 15):** TP +25% premium (underlying ~+1.41sigma); SL -60% premium (underlying ~-2.18sigma = 1.5x avg winner MAE); hard sell day 15 accepts -50%.

Underlying sigma-thresholds (delta approximation, delta=0.5): `option_gain% ~= (0.5 * underlying_move%) / premium%`
```
15 DTE: TP at underlying +0.645sigma / SL at underlying -1.548sigma
30 DTE: TP at underlying +0.910sigma / SL at underlying -2.184sigma
```

### Puts structural note

Put signals (<25 bucket): `avg_mae_winner_sigma` = -0.35 (7d), -0.45 (15d) — tighter MAE, but `mfe_sigma_p25` = 0.26 (7d)/0.41 (15d) means 25% of put trades barely move. Combined with negative avg_ret across all periods, put options under this framework have poor risk/reward — focus on call signals (70+).
