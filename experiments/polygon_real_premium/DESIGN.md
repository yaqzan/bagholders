# polygon_real_premium — REAL option-contract price ledger

**What:** for every 75+ CALL signal under the active scoring version (v74) in
2022-08-01 .. today, the ACTUAL traded daily OHLC path of the ATM ~30-DTE call the
engine would have bought.

**Why:** the engine currently prices an option as
`premium_pct = PREMIUM_MULT(1.82) * realized_vol / 100` (`monte_carlo.py` ~2256) — a
realized-vol model, never a print. This ledger replaces that with what the contract
actually cost and actually did.

- Producer: `experiments/polygon_real_premium/pull.py`
- Output: `.cache/polygon_real_premium/real_premium_ledger.parquet` (+ `meta.json`)
- Journal: `.cache/polygon_real_premium/_ingest_progress.jsonl` (append-only, resumable)
- Spot cache: `.cache/polygon_real_premium/_unadj_spot.parquet`

Not a rebuild of prior work: `polygon_iv/iv_ledger_polygon.parquet` is BS-derived IV
with no forward path; `iv_engine_pertrade/ledger_v1.parquet` is yfinance `lastPrice`
from 2025-02 only; `bankroll_ladder_otm_4yr/*` is real OHLC but OTM-ladder shaped and
ends 2025-12.

---

## 1. Contract selection rule (locked)

For each signal, at `as_of = signal_date`:

1. `list_option_contracts(symbol, as_of=signal_date, contract_type='call')` restricted
   to expirations in `[signal_date+21, signal_date+45]`.
   `as_of` is Polygon's point-in-time mechanism and **must be used alone** — combining
   it with `expired=true` returns zero rows (verified, `polygon_client.py` ~196).
2. **Expiration:** calendar-day DTE closest to **30**, restricted to DTE in **[21, 45]**;
   tie → shorter DTE. None in band → `status='miss:no_dte_band'`.
3. **Strike:** nearest to the signal-date **as-traded** underlying close (ATM);
   tie → lower strike. If that strike yields no usable data, fall through to the 2nd
   and 3rd nearest strikes (`strike_rank` 0/1/2). All three dry → `miss:no_atm_price`.
4. **Bars:** `option_daily_bars(signal_date, min(expiration, signal_date+45cal))`.
   Since DTE ≤ 45 the walk end is always the expiration in practice.

Chain hygiene: puts dropped; **adjusted contracts dropped** (`shares_per_contract != 100`
or a non-standard OCC root such as `O:AAPL1…`) — the engine would never trade those;
dedupe on `(expiration, strike)` preferring the standard OCC ticker.

**"Usable" (deviation from the naive reading, deliberate):** a candidate strike is
accepted only if it has an entry-day print **and** at least `MIN_FORWARD_BARS = 2`
forward bars. A contract that printed once and never again carries no path
information and must not be emitted as `kept`; it falls through to the next-nearest
strike under the same rule the spec applies to a dry ATM. If every candidate fails,
the richest attempt is emitted with `status='miss:no_forward_bars'` (0 forward bars)
or `'miss:too_few_bars'` (exactly 1), so the miss is counted, never silently dropped.

---

## 2. THE SPOT TRAP — why `price_history.close` cannot pick a strike

**This is the single most important thing in this directory.**

`trader.py` pulls OHLCV with yfinance `auto_adjust=True`, so `price_history.close` is
**back-adjusted for splits AND dividends/spinoffs**. Historical option **strikes are in
as-traded dollars**. Selecting "the strike nearest the underlying close" against an
adjusted close therefore picks the wrong strike, sometimes catastrophically.

Verified on MMM 2022-08-23:

| quantity | value |
|---|---|
| `price_history.close` (adjusted) | **103.79** |
| as-traded close | **141.75** |
| strike the naive rule picked | 105 (no prints at all) |
| listed $1-increment strikes that day | 131 … 140 |
| 134C close that day | 9.44 → ~7.75 intrinsic → spot ≈ 141.7 ✓ |

Measured drift on a 20-name sample at 2022-08-23: AZN +100%, MO +35%, CALM +27%,
PFE +24%, T +23%, VNOM +22%, CVX +15%, MMM +14%, XOM +13%, JNJ +11% — and 0% for
non-dividend names (AAPL, MSFT, TSLA, OXY). Roughly half the universe was affected.

**Polygon cannot fix this.** On the Options Developer key, EQUITY aggregates and
`/v1/open-close` are entitled only ~2 years back (probed 2026-07-25: `2023-01-04` and
`2022-08-20` → 403 `NOT_AUTHORIZED`; `2024-08-01` → OK). There is no vendor unadjusted
equity close for most of the window.

**Fix used.** yfinance `auto_adjust=False` `Close` is split-adjusted but *not*
dividend-adjusted; un-applying the forward split factors recovers the as-traded close:

```
spot_unadj(t) = yf_Close(t) * PROD(split_ratio(s) for every split dated s > t)
```

Validated: NVDA 2022-08-23 `17.18 × 10 = 171.81` (true pre-split print);
MMM `118.52 × 1.196 = 141.75` (matches the option chain, above);
AZN `133.28 × 0.5 = 66.64` (2026 ADR ratio change). Cached to
`_unadj_spot.parquet`; `--refresh-spot` rebuilds it.

Consequences for the schema:

- `entry_price` = the **adjusted** close, left untouched — it is the price space the
  engine's `realized_vol` and `premium_pct` actually live in.
- `spot_unadj` = the **as-traded** close — the *only* honest anchor for strike
  selection and `moneyness`. `spot_source` is `yf_unadj` or, when yfinance has no data
  for a symbol, the `ph_adjusted` fallback (flagged, not hidden).
- `adj_factor = spot_unadj / entry_price` — the diagnostic. 1.00 means no drift.
- `model_premium_abs = model_premium_pct * spot_unadj` — the engine's modeled option
  cost expressed in the dollars the real contract traded in.
- **`real_model_ratio = entry_premium_real / model_premium_abs`.** Note this is *not*
  `entry_premium_real / (model_premium_pct * entry_price)`: that mixes the two price
  spaces and is wrong by `adj_factor`. It was exactly this mix-up that produced the
  22.4× ratio outlier in the first smoke run.

---

## 3. Entry price and the TP/SL touch conventions

**`entry_premium_real` is the contract's CLOSE on the signal date.** Confirmed —
this matches the repo's entry-timing canon (buy near the close;
`project_entry_timing_closed`, next-open anchoring is −1.35pp and closed).

A daily close is a **print, not a fill**. There is no NBBO at the Developer tier, so
this ledger cannot tell you what you would have paid — only what the contract last
traded at that day. `entry_vwap`, `entry_volume` and `entry_trades` are carried so a
consumer can judge how representative that print is.

| column | detector | why |
|---|---|---|
| `tp30_touch_bar` | first forward offset with intraday **HIGH** ≥ `1.30 × entry` | A take-profit is a **resting limit order**. It fills the moment the market prints through it, so the intraday high is the honest detector. Using the close would systematically under-count real TP captures. |
| `sl70_touch_bar` | first forward offset with daily **CLOSE** ≤ `0.30 × entry` | Our engine models a **forced exit at end of day**, not a live intraday stop order. Using the LOW would manufacture stop-outs the engine would never have taken. |

Both are recorded independently; whichever offset is smaller happened first. This
asymmetry (high for TP, close for SL) is deliberate and mirrors
`otm_replay`/`otm_4yr`'s "honest intraday walk" plus this repo's EOD forced-exit
convention. `real_max_mult` / `real_min_mult` use the path HIGH / LOW.

---

## 4. Path offsets are MARKET trading days, not bar indices

Polygon emits **no aggregate at all** for a day an option did not trade. Numbering the
returned bars 1, 2, 3… would make `real_pnl_d10` mean "the 10th day this contract
happened to print", which on an illiquid contract can be three weeks out — silently
incomparable to the engine, which counts market trading days.

So `off` = **market trading days elapsed since the signal date**, computed against a
calendar built once from the union of `price_history` dates (16,248 days,
1962-01-02 .. today). A missing day simply has no entry, and `real_pnl_d10` is
correctly `null` rather than quietly pointing at the wrong day.

`path_end_reason` disambiguates every truncation:

| value | meaning |
|---|---|
| `expiry` | the path ran to the contract's expiration |
| `walk_limit` | the path ran to the `signal_date+45cal` cap (unreachable while `DTE_HI = 45`, kept for completeness) |
| `no_more_bars` | the contract simply stopped printing early — illiquidity, not expiry |
| `empty` | no forward bars at all (always accompanied by a `miss:*` status) |

`path_end_date` is the last bar's date. `bars_covered` is the number of forward
prints. `stale_frac` = share of the walk window with **no usable print** — missing
market trading days plus any zero-volume bars, over the expected market-trading-day
count (capped at the last calendar date, so an unexpired recent contract is not
penalised for days that have not happened yet). A `null` `real_pnl_d15` with
`path_end_reason='expiry'` and `stale_frac=0.55` means "the contract lived to expiry
but only printed on half the days" — an entirely different fact from
`path_end_reason='no_more_bars'`.

### `liquid_entry`

`liquid_entry = (entry_volume >= 5) AND (entry_trades >= 1)`.

Threshold 5 follows the repo's prior volume convention. The intent is to let a
consumer filter out the one-lot-on-a-wide-market print before computing any
real-vs-model statistic. Both conditions are required: `entry_trades` is Polygon's `n`
(number of transactions), so `v=250, n=1` is one block, not a liquid market.

---

## 5. Coverage boundary (empirically observed 2026-07-25)

| endpoint | entitlement on this Options Developer key |
|---|---|
| `/v3/reference/options/contracts` (chain) | **not time-limited** — returns 2021 contracts fine |
| `/v2/aggs/.../range/1/day` for OPTION tickers | **~4-year rolling**; 403 `NOT_AUTHORIZED` before ≈ `today − 4y` (≈ 2022-07-25) |
| `/v2/aggs` and `/v1/open-close` for EQUITY tickers | **~2-year rolling**; 2023-01-04 → 403, 2024-08-01 → OK |

Because the chain endpoint is not time-limited, selection succeeds pre-boundary and
only the bar walk 403s. That is caught by `_is_timeframe_403`, counted, logged once,
and emitted as `status='miss:timeframe_403'` — never a crash. `--start` defaults to
`2022-08-01` for margin against the rolling boundary. `meta.json` records
`timeframe_403_count` and `earliest_successful_bar_date`.

Smoke run (25 signals, 2022-08): earliest successful bar date **2022-08-09**, zero
timeframe-403s.

---

## 6. Known limitations

1. **Daily bars are not fills.** Every price here is a daily OHLC aggregate. There is
   no NBBO / quote data at the Developer tier, so bid-ask, slippage and the actual
   achievable fill are all invisible. `entry_premium_real` is the last print of the
   day, not what you would have paid.
2. **Zero-volume / no-print days.** Illiquid ATM contracts frequently skip days
   entirely. `stale_frac` and `path_end_reason` quantify this; filter on
   `stale_frac` and `liquid_entry` before drawing conclusions.
3. **`real_pnl_d10` / `d15` nulls are structural, not bugs.** A 21-DTE contract has
   ~14 market days of life, so `d15` cannot exist; an illiquid one may not print on
   day 10. Read them together with `path_end_reason` and `dte_cal`.
4. **`spot_unadj` reconstruction relies on yfinance's split record.** A corporate
   action yfinance recorded as a dividend rather than a split ratio will not be
   un-applied. `adj_factor` and `moneyness` are the tell — a `moneyness` far from 1.0
   at `strike_rank=0` means the anchor was wrong for that name.
5. **`vol` / `model_premium_pct` come from the adjusted series** (faithful to the
   engine). This is intentional, but it means `model_premium_pct` is a *fraction* and
   only becomes dollar-comparable via `model_premium_abs`.
6. **Signal set = `overall >= 75` CALL rows for the active version**, not the
   portfolio's actually-funded positions. It is the tradable-signal population
   (CLAUDE.md: ≥75 = call opportunity), the same population `otm_4yr` pulled. It does
   not apply monte_carlo's downstream liquidity-floor / weekly-overflow filters.
7. **`n_api_calls` counts logical endpoint invocations** (1 chain + k bar pulls).
   Chain pagination may add hidden HTTP calls; `meta.json`'s
   `total_api_calls_this_run` is the true HTTP count.
8. **No MySQL writes, ever.** Vendor data lands only under
   `.cache/polygon_real_premium/`. The `options` / `option_prices` tables are untouched.
9. **The [21, 45] DTE band is structurally empty for names without weeklies.** A
   2022-09-01 signal spans expirations 09-22 .. 10-16, which straddles both the
   09-16 and 10-21 monthlies. Symbols that list monthlies only therefore produce
   `miss:no_dte_band` on roughly half of all signal dates. This is a property of the
   locked band, not a bug — but it biases coverage toward weekly-listed (larger,
   more liquid) names, which a consumer must account for. Widening the band to
   [14, 52] would close most of it, at the cost of DTE dispersion.

### Miss taxonomy

| `status` | meaning |
|---|---|
| `kept` | usable row: entry print + >= 2 forward bars |
| `miss:no_underlying` | no signal-date close for the symbol |
| `miss:no_chain` | the symbol had NO listed calls at all in [+7, +90] days |
| `miss:no_dte_band` | listed calls exist, but none with DTE in [21, 45] |
| `miss:no_atm_price` | all 3 nearest strikes had no entry-day print |
| `miss:no_forward_bars` | entry print, but zero forward bars at any of the 3 strikes |
| `miss:too_few_bars` | entry print + exactly 1 forward bar (below `MIN_FORWARD_BARS`) |
| `miss:timeframe_403` | bars outside the ~4-year rolling aggregates entitlement |
| `miss:err:<repr>` | unexpected exception, journaled rather than crashing the run |

---

## 7. Running it

```bash
# offline: unit tests, no key / network / DB. Must be green before any network run.
python experiments/polygon_real_premium/pull.py --selftest

# smoke
python experiments/polygon_real_premium/pull.py --limit 25 --workers 4

# full pull -- QUEUE THIS, the script does NOT self-submit
trader queue submit --priority high --db light --cpu 2 --restartable \
  --dedup polygon_real_premium --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "real option premium ledger" -- \
  python experiments/polygon_real_premium/pull.py --workers 8

# rebuild the parquet from the journal, offline
python experiments/polygon_real_premium/pull.py --consolidate-only
```

Resumable: one JSONL line per `(symbol, signal_date)`; a re-run skips finished pairs
and consolidation is last-write-wins. Kill and restart freely.

All DB reads (signals, closes, realized vol, trading calendar) happen
**single-threaded before the thread pool starts** — PyMySQL is not thread-safe.
Worker threads do Polygon HTTP only.
