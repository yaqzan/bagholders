# BUILD BRIEF -- weekly_5dte_movers, Stage A + Stage B (builder agent)

You are the implementer. The design is LOCKED: read `PREREG.md` in this directory FIRST.
This brief is authoritative on implementation detail. Do not redesign; if something in
here is impossible or contradicts reality, log it under "OPEN QUESTIONS" in
BUILD_REPORT.md and choose the most conservative interpretation that keeps the PREREG
semantics.

Repo root: `C:\Development\Trader`. Python: `py -3.11`. All scripts live in
`experiments/weekly_5dte_movers/`.

## Deliverables

1. `build_ledger.py` -- Stage A: entry/outcome ledger from the contract-day index.
2. `build_features.py` -- Stage B: underlying features + joins -> analysis parquet.
3. `smoke_test.py` -- runs both stages on the smoke scope and hard-asserts the checks
   below. Exit 0 = green.
4. `BUILD_REPORT.md` -- what you found (schemas, row counts, decisions taken, smoke
   tables, open questions). ASCII only.

Do NOT run the full 202-week window and do NOT submit anything to `trader queue` -- the
orchestrator queues full runs after auditing your code. You run ONLY the smoke scope.

## Reusable assets (verified by recon 2026-08-17 -- use these, do not rebuild)

- `experiments/flatfile_exploitation/ff_common.py`:
  - `add_opra_columns(df, ticker_col)` -- vectorized OCC parse -> underlying/expiry/cp/
    strike/strike_thousandths/adjusted (nulls on unparseable, never raises).
  - `parse_opra_ticker`, `format_opra_ticker`, `read_flatfile(tier, session_date)`,
    `list_session_dates(tier)`, `FLATFILES_ROOT`, `DERIVED_ROOT` (= B:/polygon_derived),
    `TIER_SCHEMAS`, `KNOWN_INDEX_ROOTS`.
  - Import via `sys.path.insert(0, r"C:\Development\Trader\experiments\flatfile_exploitation")`
    then `import ff_common`; assert `ff_common.__file__` points there.
- **Primary substrate**: `B:\polygon_derived\contract_day_index\` -- hive-partitioned
  parquet `underlying=<SYM>/year=<YYYY>/`, 248.0M rows, 9,405 underlyings, sessions
  2022-08-01..2026-07-31 (3 sessions stale vs archive -- IRRELEVANT here, we need
  <= 2026-06-12; do NOT rebuild it).
  **First action: introspect its schema** (read one partition, print columns/dtypes into
  BUILD_REPORT.md). Expected: parsed contract fields + session date + tape columns
  (volume/open/close/high/low/transactions). IF tape columns are missing, fall back to
  scanning raw gzips per week via `ff_common.read_flatfile` and `add_opra_columns`
  (path pattern `B:\polygon_flatfiles\us_options_opra\day_aggs_v1\YYYY\MM\YYYY-MM-DD.csv.gz`;
  year dirs 2014-2021 exist but are EMPTY -- never glob-assume coverage; enumerate via
  `list_session_dates`). Report which path you took.
- Trading calendar for Stage A: derive from the archive itself --
  `list_session_dates('day_aggs_v1')` IS the empirical session list. No MySQL in Stage A.
- `database/bulk_cache.py`: `materialize_polars(name, build_fn)`,
  `chunked_query_by_year(...)`, `cache_path(name)`. Canonical usage:
  `experiments/dynamic_tpsl/build_features.py:111`.
- `database/models/technical.py`: `PriceHistory` (open/high/low/close/volume,
  `close_unadj`, Decimal(18,6) -- cast to float once on load). `Indicator` stored fields
  include rsi, macd/macd_signal/macd_hist, stoch/stoch_signal, upper/middle/lower_band,
  ma_9/ema_9/ma_21/ema_21/ma_50/ema_50/ma_200/ema_200, obv; canonical list =
  `Indicator._BULK_FIELDS`.
- Holdout: `experiments/_holdout.py` -- use `pre_cutoff_filter` semantics at build and
  `assert_no_holdout_leak(df, context=...)` after reads (cutoff 2026-06-15).
- OPEX: 3rd-Friday arithmetic is pure date math; `is_monthly_opex` = the week's expiry
  day falls in the 3rd-Friday week of its month (i.e. expiry_day is the last trading day
  of the week that contains the month's calendar 3rd Friday).

## Stage A -- `build_ledger.py`

Semantics (PREREG is binding; restated operationally):

1. Sessions = `list_session_dates('day_aggs_v1')`, filtered to <= 2026-06-12.
   Group into Mon-Fri weeks by ISO week-Monday. For each week:
   `expiry_day` = LAST session of the week (Friday normally; Thursday on
   holiday-Friday weeks, e.g. Good Friday 2024-03-29 -> expiry_day 2024-03-28).
2. Contract universe per week: rows whose parsed `expiry` == that week's `expiry_day`.
   Rows with expiry on OTHER days of the same week (Mon/Wed index expiries, EOM
   oddities) are EXCLUDED from the ledger but COUNTED per week into a
   `excluded_nonstandard_expiry` counter reported at the end.
3. Entry events: for entry sessions of the week whose weekday is Monday or Tuesday
   (either may be absent on holiday weeks): every contract-row on that session with
   volume > 0 and expiry == expiry_day. Entry price = that row's `close`.
   Drop rows with close <= 0 (count them).
4. Outcome per (contract, entry_date): over that contract's rows in the SAME week with
   session strictly after entry_date and <= expiry_day:
   `n_later_prints` = count; `max_future_high` = max(high); `max_high_date`;
   `close_at_expiry` = close on expiry_day if printed else null;
   `growth_mult` = max_future_high / entry_close; if n_later_prints == 0 ->
   `no_later_print = 1`, growth_mult = null.
5. Ledger columns (one row per contract x entry_date):
   ticker, underlying, root, adjusted, cp, strike_thousandths, strike, expiry,
   week_monday, expiry_day, is_monthly_opex, entry_date, entry_dow ('Mon'/'Tue'),
   entry_open, entry_close, entry_high, entry_low, entry_volume, entry_transactions,
   entry_dollar_vol (close * volume * 100), hl_range_pct, close_vs_open_pct,
   dte_calendar (expiry - entry_date days), dte_trading (sessions after entry through
   expiry), n_later_prints, max_future_high, max_high_date, close_at_expiry,
   growth_mult, no_later_print.
   Do NOT bake floors/views into the ledger -- raw ledger only; views are analysis-time.
6. Implementation: duckdb or polars over the hive index in expiry-year chunks
   (2022..2026), each chunk written atomically (tmp + os.replace) to
   `B:\polygon_derived\weekly_5dte_movers\ledger\ledger_<year>.parquet`; maintain a
   cursor in `.horizon/weekly-5dte-movers/state.json` (`phase`, `cursor` = last
   completed expiry year; update atomically). Also write a per-week counters CSV
   (entries, contracts, excluded_nonstandard_expiry, zero_close_drops) to
   `B:\polygon_derived\weekly_5dte_movers\ledger\week_counters.csv`.
   CLI: `--smoke` (the 4 smoke weeks below), `--year YYYY`, `--full`.
   Target: the full run must be a single queue submission; size worker threads via
   duckdb PRAGMA threads or polars default; no multiprocessing needed.

## Stage B -- `build_features.py`

1. Underlying daily panel: symbols = PriceHistory universe intersected with ledger
   underlyings (index roots / uncovered tickers get covered=0 and skip Stage B).
   Date range 2021-09-01 .. 2026-06-12 (warmup for 200d MAs). Pull via
   `chunked_query_by_year` + `materialize_polars` (cache name
   `w5dte_prices_2021_2026`): symbol, date, close (adjusted), close_unadj, volume.
2. Compute per symbol (sorted by date, polars):
   - Returns 1/2/3/5/10/20/60d, gap_1d (open missing? then close/close_prev - 1 only),
     prior-week return (Mon-aligned), realized vol 5/10/20/60d (stdev of 1d log
     returns, annualized sqrt(252)), ATR14_pct if OHLC pulled (pull open/high/low too
     -- they exist on PriceHistory), dist from 20d high/low and 52w high/low,
     dollar-volume 20d avg and ratio.
   - MA ladder: SMA and EMA for periods {5,8,9,10,12,20,21,26,34,50,100,150,200} on
     adjusted close -> price/MA - 1 per period; 5d slope of each MA (ma/ma_5d_ago - 1);
     ma_stack_count = number of the 26 MAs the price is strictly above; cross states
     sma5>sma20, sma10>sma50, sma20>sma50, sma50>sma200, ema12>ema26;
     days_since_50_200_cross (SMA, capped 250, sign for golden/death).
     (Period 9 is included so stored ma_9/ema_9 can reconcile.)
3. Joins onto the panel (all keyed symbol+date, entry_date):
   - Indicator: rsi, macd_hist, stoch, %b from bands ((close-lower)/(upper-lower)),
     stored ma_21/ma_50/ma_200/ema_21/ema_50/ema_200 for reconciliation only.
   - Score v74: look up the ACTIVE version id via
     `AlgorithmVersion.get_active_scores_version()` at runtime, ASSERT its label ==
     'v74' and pin the integer id into BUILD_REPORT.md; pull overall, pre_regime,
     pre_boost (+ component columns if cheap) for the panel dates; bucket
     (>=75 / <=25 / mid).
   - MarketRegime (composite/multiplier + vix if stored), MarketBreadth (McClellan,
     TRIN, A-D) by date.
   - EarningsDate: earnings_in_window flag (entry_date < earnings_date <= expiry_day),
     days_to_next_earnings, days_since_last_earnings.
   - Stock: sector; market_cap AS SNAPSHOT ONLY -> emit `mcap_snapshot_bucket` plus
     `mcap_is_snapshot=1` constant (honesty flag; PIT size proxy = dollar-volume).
4. Moneyness (per ledger row, after join): spot = close_unadj at entry_date
   (NEVER adjusted close -- strikes are as-traded); moneyness_pct = strike/spot - 1;
   otm_pct = +moneyness for calls, -moneyness for puts (positive = OTM);
   premium_over_spot = entry_close/spot. If close_unadj is null that day: use close
   ONLY if abs(close/close_unadj_prev - 1) sanity passes is NOT required -- instead set
   moneyness null + `spot_missing=1` (count them; do not silently substitute).
5. Output: `B:\polygon_derived\weekly_5dte_movers\features\underlying_panel.parquet`
   and the joined `analysis_<year>.parquet` (ledger rows + covered features;
   covered=0 rows keep F1/F2 only). Run `assert_no_holdout_leak` on the final frames.

## Smoke scope (`smoke_test.py`) -- all must hard-assert

Weeks (by expiry_day): 2024-05-24 (NVDA earnings 2024-05-22 -- expect NVDA calls
heavily represented in top growth_mult), 2023-07-28 (quiet control), 2024-03-28
(Good Friday week -- assert expiry_day is the Thursday, nonzero contracts, ~zero
contracts parsed with expiry 2024-03-29), 2025-01-24 (MLK Monday 2025-01-20 -- assert
zero Mon entries, nonzero Tue entries).

Checks:
- Ledger invariants: no expiry > 2026-06-12; entry_dow in {Mon,Tue}; growth_mult null
  iff no_later_print; max_high_date > entry_date; dte_trading in [1,4] for Mon,
  [1,3] for Tue (allow holiday-week 1 less).
- Independent-path spot check: for 3 sampled contracts in the NVDA week, re-read the
  RAW gzips via `ff_common.read_flatfile` and assert entry_close / max_future_high
  match the index-derived ledger exactly.
- Stage B on smoke weeks restricted to <= 10 symbols (NVDA + 9 others incl 1 uncovered
  index root to prove covered=0 path): MA reconciliation |computed - stored| / stored
  < 1e-3 for ma_21/ma_50/ma_200 on 100 sampled rows (report worst); EMA tolerance 2%
  (seed differences); earnings flag correct for NVDA 2024-05-22; moneyness within
  (-0.9, 10) sanity band and null-count reported.
- Print a top-20 growth_mult table for the NVDA smoke week (ticker, underlying, cp,
  strike, entry_dow, entry_close, entry_volume, max_future_high, growth_mult) into
  BUILD_REPORT.md -- ASCII table.

## Trap registry (forwarded -- violations are the expensive kind)

- Windows console: ASCII-only stdout in every script; set PYTHONIOENCODING=utf-8 and
  PYTHONUTF8=1 defensively; never print unicode box chars.
- day_aggs CSV column order is `open,close,high,low` -- NOT OHLC. `window_start` is
  int64 EPOCH NANOSECONDS; ignore it, key by session date.
- polars: `infer_schema_length=None` on any read_csv; NaN != null (use fill_nan then
  null handling explicitly; `.rank()` treats NaN as MAX -- never rank a column with
  NaNs); Decimal from MySQL -> cast float once at load.
- MySQL politeness: chunked reads only (chunked_query_by_year); no full-table scans
  during market hours; if a read times out do NOT blind-retry (zombie-query cascade).
  peewee bulk loops use `.symbol_id`, not `.symbol`.
- `price_history.close` is split+dividend ADJUSTED; `close_unadj` is AS-TRADED.
  Moneyness/strike math uses close_unadj ONLY. Returns/MAs use adjusted close ONLY.
- `adjusted` (non-standard OCC roots, digit suffix): keep rows, carry the flag; they
  are excluded from the TRADEABLE view at analysis time; never premium-compare them to
  standard siblings.
- Index roots (`KNOWN_INDEX_ROOTS`) have no PriceHistory -> covered=0, never joined.
- Year dirs 2014-2021 under the flat-file root are EMPTY decoys; enumerate sessions
  via list_session_dates, never MANIFEST.jsonl (known incomplete), never glob-assume.
- Holdout: nothing past 2026-06-15 read anywhere; assert with _holdout helpers.
- Atomic writes everywhere (tmp + os.replace); resumable by year cursor; failing loud
  with partial state intact beats silent completion.

## Timebox + report

~60-90 min. If blocked > 15 min on one issue, write it to BUILD_REPORT.md OPEN
QUESTIONS and move on (conservative fallback). Finish with: files written, smoke
results (green/red per check), row counts per smoke week, runtime estimate for the
full 202-week Stage A run, and the pinned score version id.
