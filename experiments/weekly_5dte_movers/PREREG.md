# PREREG -- weekly_5dte_movers (owner-directed, 2026-08-17)

Status: LOCKED before any scan ran. Analysis protocol below is binding; deviations get a
dated amendment note here, never a silent edit.

## Owner directive (2026-08-17, paraphrased from chat)

Find the highest-growing option prices in the 5-DTE weekly frame: enter on Monday or
Tuesday prints, contracts expiring that same Friday. Because Friday digs into 0-DTE
territory, measure growth by the WEEK'S HIGH as a theoretical maximum, not by Friday close.
At a suitable threshold, take the top gainers, log ALL metrics associated with the Mon/Tue
entry prints -- explicitly including SMAs and EMAs across a wide range of durations -- and
pattern-mine. Then ablate metrics out and see which patterns hold. Be comprehensive.
Owner-selected options: dual floors (raw census + tradeable cohort), mine <= 2026-06-15
holdout cutoff only, both sides (calls and puts, reported separately), v74 scores joined.

## Classification

Stage-0 descriptive census + pattern mining (lead generation). NOT a ship gate. No
portfolio or EV claims can come out of this run -- theoretical week-high growth is not
captured PnL by construction. Any lever candidate that emerges goes to
`alpha_mining/NEW_LEADS.md` and then the normal W/B/T gates with the 2026-12-15 OOS read.
Relation to the program board: `option-surface-features` (paused pending December) is
adjacent; this run is owner-directed, embargo-respecting, and its findings feed that
task's priors.

## Locked definitions

- **Week**: Mon..Fri trading week. **Expiry day** = last trading day of that week (Friday,
  or Thursday when Friday is a market holiday). Contract universe per week = all OPRA
  contracts in `day_aggs_v1` whose OCC-parsed expiry equals that week's expiry day.
  Third-Friday monthlies are kept (they are 3-4 DTE at entry like any weekly) and flagged
  `is_monthly_opex`.
- **Entry events**: (contract, entry_day) for entry_day in {Mon, Tue} of the same week
  where the contract printed that day (day_aggs row, volume > 0). Monday holiday -> Tue
  only. **Entry price = that day's `close`.** Mon and Tue entries are tracked as separate
  events (a contract can appear twice per week).
- **Outcome**: `max_future_high` = max(`high`) over the week's days STRICTLY AFTER
  entry_day through expiry day. `growth_mult = max_future_high / entry_close`.
  Entries with zero subsequent prints: unobservable -> `no_later_print=1`, counted in all
  denominators as non-winners, never in the winner cohort.
- **Date window**: entry weeks with expiry <= 2026-06-12 (last full week before the
  2026-06-15 holdout cutoff). First week = Mon 2022-08-01 (archive start, conveniently a
  Monday). ~202 expiry weeks. Enforced in code via `experiments/_holdout`
  (`pre_cutoff_filter` at build; `assert_no_holdout_leak` at read).
- **Floors** (fixed a priori, NOT swept -- FF-3 bare-floor-sweep ban):
  - RAW view: every entry event.
  - TRADEABLE view: entry_close >= $0.20 AND entry-day volume >= 100 contracts AND
    transactions >= 10 AND standard OCC root (adjusted/non-standard roots with digits
    excluded).
- **Winner threshold**: primary = `growth_mult >= 5.0` in the TRADEABLE view.
  Sensitivity ladder reported: >=3x, >=10x, top-0.5%-by-mult. The ablation battery runs
  at >=5x; robustness requires directional agreement at >=3x and >=10x.

## Metric log (per entry event -- "keep a log of all metrics")

- **F1 contract geometry**: side, strike, dte_calendar, dte_trading, moneyness
  (strike / spot_unadj - 1, plus an OTM-signed variant positive-when-OTM for both sides),
  entry_premium, premium/spot, is_monthly_opex, OCC root.
  Moneyness uses `price_history.close_unadj` (AS-TRADED) -- never adjusted close
  (traps.md: strikes are as-traded).
- **F2 tape/liquidity**: volume, transactions, dollar volume, entry-day H-L range %,
  close-vs-open, entry day-of-week.
- **F3 underlying price state** (adjusted close for returns/vol/MAs): returns
  1/2/3/5/10/20/60d, 1d gap, distance from 20d and 52w high/low, prior-week return,
  realized vol 5/10/20/60d, ATR14 %, dollar-volume ratio vs 20d average.
- **F4 MA ladder (owner-mandated wide)**: SMA and EMA, periods
  {5, 8, 10, 12, 20, 21, 26, 34, 50, 100, 150, 200} on adjusted close ->
  price/MA - 1 (24 cols), each MA's 5d slope (24 cols), MA stack count (how many of the
  24 the price is above), and cross states: sma5>sma20, sma10>sma50, sma20>sma50,
  sma50>sma200, ema12>ema26, days-since-50/200-cross (capped 250).
- **F5 stored indicators** (Indicator table): RSI, MACD hist, BB %b, Stoch, stored
  EMA50/200 -- with a reconciliation test against F4's computed values.
- **F6 score join** (active v74 version rows, where the underlying is scored): overall,
  pre_regime, pre_boost, components, bucket (>=75 / <=25 / mid), and signal age.
- **F7 market context** at entry date: regime composite/multiplier, VIX, breadth
  (McClellan/TRIN/A-D), SPY 5/20d returns.
- **F8 events/identity**: earnings-inside-outcome-window flag
  (entry < earnings_date <= expiry), days to/since earnings, sector, mcap bucket
  (point-in-time where available, else flagged), index/ETF-underlying flag
  (SPX/SPXW/SPY/QQQ/IWM etc.).
- **F9 census-only**: for underlyings NOT in our PriceHistory universe, F1+F2 only,
  `covered=0`. The census covers the whole tape; the ablation runs on covered rows.

## Analysis protocol (locked)

- **A. Census**: growth_mult distributions by side x year; winner counts raw vs
  tradeable; top-100 gallery (eyeball surface, not statistics).
- **B. Base rates**: P(>=5x | tradeable) overall and by side / entry day / year.
- **C. Univariate screens**: per metric, winner-vs-base decile lift + z, min cell N=200
  (else merge cells). All ~120 metrics logged; count of tests reported.
- **D. Factor abstraction** ("abstract out the metrics"): Spearman correlation
  clustering, |rho| > 0.8 merges -> named orthogonal factors (e.g. the 72 MA columns are
  expected to collapse to ~2-3 factors: trend-stack, extension, slope). Patterns are
  stated at FACTOR level. Then shallow GBT (depth <= 3) + logistic on factors, AUC with
  year-blocked CV (no test-year leakage).
- **E. Ablation battery**:
  1. Leave-family-out (F1..F8): delta-AUC and delta-lift@top-decile per removed family.
  2. Leave-metric-out for the top-10 single metrics.
  3. Rule distillation: 3-6 explicit rules max; each re-tested across year slices
     (22H2/23/24/25/26H1), side, entry day, thresholds {3x,5x,10x}, floors {raw,
     tradeable}, ex-earnings-week, ex-index-underlyings.
     **HOLD = same direction in >=4/5 year slices AND both adjacent thresholds AND
     survives ex-earnings (unless the rule IS the earnings pattern).**
- **F. Honesty**: everything is in-sample 2022-08 -> 2026-06-12; ~120 metrics tested is
  stated next to every finding; no peeking past the cutoff; winners are theoretical-high
  growers, not realized trades. Expected dominant confounds to be tested explicitly:
  earnings weeks, index/0-DTE-adjacent underlyings, penny-premium artifacts (raw view).

## Falsification / evaluation (review-posture requirement)

- Deliverable: a validated factor-level pattern list -- or a clean null -- written to
  FINDINGS.md, with leads promoted to NEW_LEADS.md.
- Failure looks like: no family beats the shuffled-label AUC envelope; or every pattern
  is an earnings proxy that dies ex-earnings; or winners are untradeable dust even at
  floors. A clean null is an acceptable, publishable outcome.
- Evaluated after Stage C on the full window; a 2-week smoke run with hand-reconciled
  counts gates the full scan.

## Compute plan

- **Stage A** tape scan (no MySQL): polars per-week over
  `B:\polygon_flatfiles\us_options_opra\day_aggs_v1\`, OCC parsed vectorized; ledger
  parquet out. Queued `--db light`, high priority allowed off-market.
- **Stage B** underlying/scores join (MySQL bulk via `database.bulk_cache`): chunked
  reads, `--db heavy`, off-market window or sub-scheduled priority per market-hours rule.
- **Stage C** mining + ablation: local polars/duckdb + sklearn-lite; DSR/SVG for visuals.
- State + resume: `.horizon/weekly-5dte-movers/` (driver owns state.json; resumable by
  week cursor).

## Amendments

- **2026-08-17a** (pre-build, pre-data): MA ladder gains period 9 (both SMA and EMA) so
  the F5 reconciliation can check against stored `ma_9`/`ema_9`. Ladder =
  {5,8,9,10,12,20,21,26,34,50,100,150,200}. Also fixed operational facts from recon:
  substrate = `B:\polygon_derived\contract_day_index\` (prebuilt FF-0 index; no raw
  gzip rescan), outputs under `B:\polygon_derived\weekly_5dte_movers\`, Stage A
  calendar = the archive's own session list (no MySQL in Stage A).
- **2026-08-17c** (post-build audit, pre-data): builder deviations RATIFIED -- panel
  warmup start 2020-01-01 (EMA seed convergence; ledger window untouched); F6/F7/F8
  joined at entry-event grain (the metric log is per entry event by definition); ATR14 =
  SMA-of-TR convention; dist-from-high/low on high/low not close; mcap buckets as
  documented in BUILD_REPORT decision #7. **"Signal age" (F6) is DROPPED from v1**: no
  operational definition was locked pre-build, so rather than invent one post-hoc it is
  out of scope; if the F6 score family shows discriminative signal in Stage C, a v2
  amendment may define it (candidate: trading days since `overall` entered its current
  bucket) and add it via a daily-series score pull. Orchestrator audit also fixed two
  full-run-path defects before any full run: the holdout assert crashed on the
  `entry_date`-named frame, and `run_full` rebuilt/clobbered the underlying panel per
  year (now built once over the union of ledger underlyings).
- **2026-08-17b** (scope note): the paused program task `option-surface-features` is
  adjacent. This run proceeds anyway because it is owner-directed (2026-08-17 session),
  reads nothing past 2026-06-12, does not touch the two locked December cluster
  hypotheses, and makes no supply-cutting portfolio claim (so the exposure-matched
  control precondition is not triggered). Findings feed that task's priors when it
  opens after the December reads.
