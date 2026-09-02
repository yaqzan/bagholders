# Legacy OOS — Polygon Data Decision + Pre-2016 Backbone Validation

**Date:** 2026-05-09  
**Context:** post-ScoringConfig refactor `02b9e39`  
**Status:** designed, blocked on data access decision  
**Goal:** test whether the v37-v44 score-stage stack generalizes outside the
2016-2026 calibration distribution, using pre-2016 data that was not available
to the recent mechanism calibration loop.

## Decision Memo

The original "$30 one-month Polygon pull" assumption is not sufficient for the
2005/2011 OOS test.

Current Polygon docs/pricing split the relevant stock data this way:

| Need | Polygon surface | Plan implication |
|---|---|---|
| Point-in-time ticker reference, including delisted/inactive symbols | `/v3/reference/tickers`, ticker overview/events | Starter is enough for reference history |
| Daily OHLCV flat files across all U.S. equities | `us_stocks_sip/day_aggs_v1` | Starter = 5y, Developer = 10y, Advanced = all history |
| Pre-2016 daily OHLCV, including 2005 and 2011 | day aggregates / REST aggregates | Requires Advanced/all-history or another vendor |
| Historical tick-level / aggregate coverage start | Polygon states stocks history dates back to 2004 / flat files to 2003 | Sufficient for 2005+ if plan has all-history access |

Recommendation:

1. Run a **free-tier schema probe** first if no paid key exists. It validates
   authentication, JSON/flat-file parsing, ticker metadata shape, adjustment
   semantics, and parquet layout using recent data only.
2. If the objective remains **true pre-2016 OOS**, buy **one month of Polygon
   Stocks Advanced** or choose an alternate all-history data source. Do not buy
   Starter for this specific test; it will not reach 2005/2011 OHLCV.
3. Use **daily flat files** as the primary ingest path once all-history access
   exists. REST per-ticker calls are acceptable for probes and metadata, but the
   all-market flat files are cleaner, faster, and include delisted tickers in
   the same daily universe.

## Research Question

Does the score backbone that was calibrated on 2016-2026 retain tier ordering
and directional edge in genuinely unseen market regimes?

Primary pass/fail:

| Tier family | Pass threshold |
|---|---|
| Calls `95+ / 85+ / 75+` | WR7/WR15/WR30 within +/-3pp of the matched 2016-2026 baseline, with monotonic tier ordering preserved |
| Puts `<15 / <25` | WR7/WR15/WR30 within +/-3pp of the matched baseline, with stronger put buckets not worse than weaker buckets |
| Discrete buckets | No populated discrete bucket regresses more than 5pp vs matched baseline unless N is underpowered |

Failure is useful: a broad collapse in 2005/2011 means the stacked mechanisms
are probably encoding a post-2016 market regime. A narrow failure identifies
which mechanism family should be ablated first.

## Windows

Use market-regime windows, not one pooled "pre-2016" blob:

| Window | Purpose |
|---|---|
| 2005-01-01 to 2007-12-31 | pre-GFC bull/credit expansion |
| 2008-01-01 to 2009-12-31 | crash and high-vol bear |
| 2010-01-01 to 2012-12-31 | post-GFC recovery / Euro-crisis chop |
| 2013-01-01 to 2015-12-31 | QE bull / low-vol momentum |

Report each window independently, plus a pooled 2005-2015 summary weighted by
signal count. Keep 2016-2026 as a same-method control run so differences are
not artifacts of the legacy pipeline.

## Universe

Primary universe: **daily liquid U.S. common stocks from Polygon all-market day
aggregate files**, point-in-time by construction.

Filters:

- Common stocks only; exclude ETFs, ETNs, warrants, rights, units, preferreds,
  funds, ADRs where reference metadata identifies them.
- Price >= 5 on signal date.
- Median 60d dollar volume >= 5M on signal date.
- At least 260 prior daily bars before scoring date.
- At least 90 forward calendar days available for barrier walking, unless the
  symbol delisted earlier; delisting-terminal outcomes must be treated
  explicitly, not silently dropped.

Why not historical S&P 500/Russell 1000 as primary:

- Index membership is not the alpha question; survivorship-safe tradability is.
- Polygon gives point-in-time ticker/reference and all-market daily files, but
  not a clean historical Russell membership feed.
- A liquid common-stock universe is reproducible from the raw data and avoids
  hiding delisted losers behind constituent-source gaps.

Optional secondary universe: historical S&P 500 membership if a reliable
point-in-time source is added later. Treat it as a sensitivity check, not a
blocker.

## Mechanism Coverage Matrix

| Mechanism / input | Legacy OOS status | Handling |
|---|---|---|
| Price/volume indicators: RSI, MACD, BB, Stoch, EMA, trend | Full | Compute from Polygon OHLCV |
| Weekly aggregate and weekly indicators | Full | Rebuild from daily OHLCV |
| Barrier outcomes WR3/5/7/15/30/60/90 | Full | Use Numba/Python walk over OHLCV |
| Market breadth/regime | Full enough | Rebuild from the legacy liquid-stock universe plus SPY where available |
| Volume amplifier | Full | Uses OHLCV-derived signals |
| PCD / CWWD / WCF / CWCF / ICH | Full | OHLCV/indicator derived |
| MCD | Partial | Prefer point-in-time market cap from financials/share counts; otherwise ablate or proxy with dollar volume/price bucket |
| EARN_BOOST / PESS | Partial to no | Requires reliable historical earnings dates; run OFF in backbone-primary, then sensitivity if earnings data is available |
| SAW / sector breadth portfolio filters | Out of scope | Portfolio-stage, not needed for Stage 1 OOS |
| CT_PROMOTE / CTSL | Out of scope for primary | Portfolio-stage or post-v44 topic; exclude from backbone OOS unless explicitly testing current production stack |

Two score stacks should be reported:

1. **Backbone-reproducible stack:** all mechanisms whose inputs are reliable in
   legacy data; MCD/PESS/EARN may be off/proxied depending on data coverage.
2. **Current-stack approximation:** adds MCD/earnings only where point-in-time
   legacy inputs pass data-quality checks.

If the backbone passes but current-stack approximation fails, the problem is
input coverage or a newer mechanism, not the technical-analysis core.

## Data Artifacts

All artifacts stay under `experiments/legacy_oos/` or `.cache/legacy_oos/`.
Do not write legacy rows into production MySQL tables.

Expected files:

| Artifact | Description |
|---|---|
| `.cache/legacy_oos/raw/day_aggs/YYYY/*.parquet` | Normalized daily OHLCV from Polygon flat files |
| `.cache/legacy_oos/raw/reference/tickers_*.parquet` | Point-in-time ticker metadata snapshots |
| `.cache/legacy_oos/universe/liquid_common_stocks.parquet` | Tradable daily universe after filters |
| `.cache/legacy_oos/features/daily_indicators.parquet` | Daily indicators, weekly rollups, breadth/regime features |
| `.cache/legacy_oos/scores/backbone.parquet` | Generated scores and mechanism-fire flags |
| `.cache/legacy_oos/outcomes/barriers.parquet` | Barrier outcomes by period and side |
| `reports/legacy_oos_summary.md` | Final pass/fail tables and ablation interpretation |

## Pipeline

### Phase 0 — Free-tier Probe

Purpose: validate API mechanics without paying for historical access.

Scope:

- Pull ticker reference for a small set: `AAPL`, `MSFT`, one inactive ticker if
  available, and one ETF for exclusion testing.
- Pull recent daily bars allowed by the free plan.
- Normalize to the intended parquet schema.
- Confirm split-adjustment semantics and missing-bar behavior.

Exit:

- `POLYGON_API_KEY` works.
- Parquet schema is stable.
- Delisted/reference fields needed for filtering are present.
- No production tables touched.

### Phase 1 — Legacy Ingest

With all-history access:

1. Download daily aggregate flat files for 2003-2016. Include 2003-2004 as
   indicator warmup for 2005 signals.
2. Build daily adjusted OHLCV parquet partitioned by year.
3. Build point-in-time reference snapshots at month or quarter cadence, plus
   exact ticker overview lookups for symbols entering the tradable universe.
4. Normalize ticker changes with Polygon ticker events where available. Preserve
   both raw ticker and stable entity key when possible.

### Phase 2 — Universe + Feature Build

1. Filter to common liquid stocks.
2. Compute daily indicators with the same formulas as production.
3. Compute weekly aggregates/indicators from daily bars.
4. Compute breadth/regime from the legacy universe, not from current `Stock`
   table membership.
5. Materialize features to parquet and assert all calibration dates are
   pre-2016. The 2026 holdout helper is still relevant for any current-era
   comparison run, but the legacy dataset is naturally before the cutoff.

### Phase 3 — Score Generation

Use an experiment-local scorer wrapper that mirrors current production scoring
but reads from legacy feature rows. Do not write `Score` rows and do not bump
`ALGORITHM_VERSION`.

Required score diagnostics:

- final overall
- pre/post mechanism overall where practical
- mechanism-fire booleans for PCD/CWWD/MCD/ICH/PESS/EARN
- missing-input flags
- side bucket

### Phase 4 — Barrier Walk + Metrics

Compute the same Stage 1 barrier windows used by current assessment:

- WR3, WR5, WR7, WR15, WR30, WR60, WR90
- HIGH/call target: rise before fall
- LOW/put target: fall before rise
- sigma: 60d realized vol at signal date
- periods are calendar days

Include delisting handling:

- If the symbol delists before the horizon, walk through the final traded date.
- If a terminal return can be inferred, record it.
- If not, mark `terminal_unknown` and report exclusion sensitivity.

### Phase 5 — Baseline-Matched Report

Compare legacy windows against a control built by the same experiment code on
2016-2026 data. This avoids comparing a new parquet scorer against production
DB assessment artifacts with slightly different universe/filters.

Report:

- cumulative tier WR by period
- discrete bucket WR by period
- N/year by bucket
- tier monotonicity
- mechanism-fire frequency by window
- missing-input rates
- ablation table: backbone, backbone-minus-MCD, backbone-minus-ICH,
  backbone-minus-PCD, earnings-off/current-approx

## Pass/Fail Interpretation

| Result | Interpretation | Next action |
|---|---|---|
| Backbone passes all windows | Core score stack generalizes; continue per-mechanism retuning on current data |
| Backbone passes except 2008-2009 | Regime-specific fragility; investigate breadth/regime interaction before changing score weights |
| Calls fail, puts pass | Recent call dampeners/weights likely post-2016-specific; ablate MCD/ICH/PCD first |
| Puts fail, calls pass | PESS/WCF/MACD gate interaction suspect; ablate earnings and weekly mechanisms |
| Both sides collapse | Evidence of broad overfit; pause new scoring ships until mechanism ablation identifies culprit |
| Current approximation fails but backbone passes | Legacy input coverage issue or MCD/earnings approximation artifact |

## Production Safety

- No production DB writes.
- No production scoring edits.
- No `ALGORITHM_VERSION` bump.
- No blind joint sweep across ScoringConfig knobs.
- No calibration on post-2026-05-15 data.
- Experiment code must keep data-pull, feature-build, score, and report phases
  separately runnable and cacheable.

## Estimated Effort

| Scope | Data cost | Engineering |
|---|---:|---:|
| Free-tier probe | $0 | ~2 hours |
| Minimum viable legacy OOS: day aggs, liquid universe, backbone technical stack, WR tiers | Advanced one-month or equivalent | 2-3 days |
| Full current-stack approximation: add MCD/share-counts, earnings sensitivity, ticker-event stitching | Advanced one-month or equivalent | 4-5 days |
| Survivorship correction for current 2020-2026 baselines | Starter/Developer may suffice depending current date window | 1-2 days once ingest exists |

## Immediate Next Step

The S&P 500 removal manifest path is already in place:

```bash
python experiments/legacy_oos/sp500_wikipedia_import.py --start 2016-01-01 --end 2021-01-01
```

This writes:

- `.cache/legacy_oos/sp500/sp500_removed_2016-01-01_2021-01-01.csv`

The current 2016-2020 manifest has 121 unique S&P removals:

| Year | Removed symbols |
|---|---:|
| 2016 | 29 |
| 2017 | 28 |
| 2018 | 23 |
| 2019 | 24 |
| 2020 | 17 |

Keep two dates distinct in every downstream artifact:

| Field | Meaning | Source |
|---|---|---|
| `sp500_removed_at` | Date the company left the S&P 500 | Wikipedia selected-changes manifest |
| `delisted_at` | True exchange delisting / inactive date when available | Polygon/Massive reference metadata such as `delisted_utc` |

Do not label an S&P removal as a true delisting unless the vendor reference
metadata confirms it. Active-but-removed names must remain eligible for price
pulls after S&P removal when testing broad liquid-stock OOS universes.

Unless the user already has an all-history key, run Phase 0 only:

```bash
set POLYGON_API_KEY=<key>
python experiments/legacy_oos/polygon_probe.py
```

After the probe passes:

1. Buy one month of Stocks Advanced if the objective remains clean 2016-2020
   and pre-2016 OOS coverage from one source.
2. Pull `us_stocks_sip/day_aggs_v1` flat files for 2015-2020, using 2015 as
   indicator warmup for 2016 signals.
3. Enrich the S&P manifest with ticker reference fields: `active`,
   `delisted_utc`, FIGI/CIK/name, locale, market, type, and primary exchange.
4. Build the point-in-time liquid-common-stock universe from flat files and
   metadata rather than from the current `Stock` table.
5. Score and barrier-walk from parquet artifacts only; production MySQL should
   remain untouched for the OOS run.

Do not scaffold the full ingest until the vendor decision is made, because the
flat-file/S3 path and REST fallback differ enough to affect implementation.
