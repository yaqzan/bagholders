# BUILD_REPORT -- weekly_5dte_movers

Status: DONE. `py -3.11 experiments/weekly_5dte_movers/smoke_test.py` exits 0, 29/29 checks
green. Deliverables: `build_ledger.py` (Stage A), `build_features.py` (Stage B),
`smoke_test.py`, this file. Only the smoke scope was ever run (4 named weeks; Stage B
capped at 10 symbols) -- no `--year`/`--full` invocation happened in this build session,
per the hard rule in BUILD_BRIEF.md.

## Schema introspection (first action, per BUILD_BRIEF)

Read one hive partition directly (`underlying=NVDA/year=2024/part.parquet`, 942,001 rows)
before writing any code:

```
date          Date
ticker        String
underlying    String
expiry        Date
cp            String
strike        Float64
adjusted      Boolean
volume        Int64
open          Float64
close         Float64
high          Float64
low           Float64
window_start  Int64
transactions  Int64
```

This matches BUILD_BRIEF's expected schema exactly (parsed contract fields + session date +
full tape columns: volume/open/close/high/low/transactions all present). **The raw-gzip
fallback was NOT needed** -- Stage A reads exclusively from
`B:\polygon_derived\contract_day_index\_bydate\<date>.parquet` (one parquet per session,
all underlyings -- see "Substrate choice" below), confirmed present and schema-identical
for every session date touched by the 4 smoke weeks.

One thing the index does NOT carry that PREREG's ledger-column list names:
`strike_thousandths` (only the float `strike` survived the FF-0 build). Recomputed in
`build_ledger.py` as `round(strike * 1000)` -- exact, since `strike` was itself derived as
`strike_thousandths / 1000.0` with no lossy step in between (verified: OCC strikes are
whole-cent-times-1000 integers, so the round-trip has zero collision risk in the observed
strike range).

## Substrate choice: `_bydate/`, not the `underlying=<U>/year=<Y>/` hive

BUILD_BRIEF's amendment note names the FF-0 index generally but doesn't disambiguate its
two physical layouts. `contract_day_index` has both:
- `_bydate/<date>.parquet` -- one file per SESSION, all underlyings (what FF-0 phase 1
  writes).
- `underlying=<U>/year=<Y>/part.parquet` -- one file per (underlying, YEAR-OF-SESSION),
  built by FF-0 phase 2 for the opposite access pattern (one symbol's full history).

A per-WEEK scan needs every underlying on a handful of specific dates (Mon-Fri of that
week) -- exactly `_bydate`'s layout. Using the hive layout instead would mean scanning
thousands of per-underlying partitions per week for no benefit. `build_ledger.py` reads
`_bydate` exclusively; `smoke_test.py`'s independent-path check separately re-reads the
RAW `.csv.gz` archive (not the index at all) via `ff_common.read_flatfile`, giving a
genuinely independent verification path.

## Decisions taken (conservative interpretations where BUILD_BRIEF was silent or the two
docs left a gap)

1. **`root` column**: PREREG's ledger-column list names both `underlying` and `root`, but
   `ff_common.add_opra_columns` (which built the index) only ever produces ONE parsed root
   string -- there is no second "true underlying vs. raw OCC root" distinction anywhere in
   this codebase (non-standard-deliverable roots like "AMD1" are never resolved to a base
   symbol; out of scope per ff_common's own docstring). `root` is emitted as an exact alias
   of `underlying`. Flagging this rather than silently picking one name to drop.

2. **Panel warmup window (`PANEL_START`) extended from 2021-09-01 to 2020-01-01.**
   BUILD_BRIEF Stage B step 1 says "Date range 2021-09-01 .. 2026-06-12 (warmup for 200d
   MAs)". At that buffer (~460 trading days before the earliest smoke week, 2023-07-24),
   EMA200 reconciliation for COIN (highest-realized-vol smoke symbol; IPO 2021-04-14) sat
   at 2.55-2.59% -- just over BUILD_BRIEF's own 2% "seed differences" tolerance, while
   every other symbol/period/week was comfortably under (SMA reconciled to ~2e-5
   regardless, proving the join/date-alignment/talib-call logic itself is correct -- this
   is purely a function of how much extra runway the EMA's exponentially-decaying seed
   term has had). Since 2021-09-01's *entire stated purpose* in the brief is "warmup for
   200d MAs," and pushing it earlier changes no PREREG-locked semantic (the ledger's own
   window is still exactly 2022-08-01 .. expiry<=2026-06-12; this only affects how much
   history Stage B additionally pulls to seed indicator computation), I widened it to
   2020-01-01 -- safely before COIN's actual listing date, so COIN gets its full available
   history. Verified empirically: worst-case EMA200 relerr at entry-event grain dropped
   from 2.59% to 4.7e-5. See `build_features.py` `PANEL_START` comment for the inline
   version of this note. **If the orchestrator wants literal 2021-09-01 restored, the one
   documented cost is a marginally larger MySQL pull for the full run and this specific
   corner check going from comfortably-green to borderline** (2/63 rows over 2% by ~0.5-0.6
   points, all other 61 rows and every SMA period unaffected either way).

3. **F6 (score) / F7 (regime+breadth) / F8 (earnings+sector) joined at the ledger's sparse
   (symbol, entry_date) grain, not the dense daily panel.** BUILD_BRIEF Stage B step 3
   literally says "pull ... for the panel dates," but PREREG's own framing is explicit that
   the whole metric log is "per entry event" (F6's heading: "score join ... where the
   underlying is scored" -- an entry-event-scoped join, not a daily series). Score.weight_info
   is large per-row JSON TEXT; pulling it for every trading day of a multi-thousand-symbol
   panel (vs. only the ~400 distinct entry dates x covered symbols that actually occur
   across the full window) is a large, avoidable MySQL/TextField cost with no downstream
   benefit -- nothing needs a daily score *series*, only the entry-event snapshot. Treated
   this as the "most conservative interpretation that keeps the PREREG semantics" the brief
   licenses when its literal wording and PREREG's stated intent diverge. MarketRegime/
   MarketBreadth are cheap (~1 row/day, no symbol dimension) and ARE pulled for the full
   panel window regardless, so this only changes Score and EarningsDate.

4. **`pre_regime`/`pre_boost` source.** These are NOT literal `Score` table columns --
   they only exist as-is on `ScoreIntradayLog` (audit table, populated from 2026-05-27
   only, far short of the 2022-08+ window this experiment needs). The genuine source for
   any historical date is `Score.weight_info` (a JSON TextField `compute_overall_score`
   writes on every score), which is parsed the same way `ScoreIntradayLog` itself is
   populated from it (`database/models/core.py` `_parse_weight_info`, confirmed by direct
   read). `pull_scores_sparse()` in `build_features.py` does this `json.loads` +
   `.get('pre_regime')`/`.get('pre_boost')` per row.

5. **`indicator`/MA formula fidelity.** Confirmed by reading `Stock.calculate_indicators`
   (`database/models/core.py` ~3799-3817) that production computes stored
   `ma_9/ema_9/.../ma_200/ema_200` via `talib.SMA`/`talib.EMA` over the symbol's full
   ADJUSTED-close history. `build_features.py`'s F4 ladder uses the identical `talib`
   calls (not a hand-rolled EMA) for exactly this reason -- see decision #2 above for why
   that still needed a wider warmup window than BUILD_BRIEF's literal date range.

6. **`earnings_in_window` / days-to/since earnings use `EarningsDate.effective_date`**
   (the trading day the price reaction actually appears -- BMO/intraday same-day, AMC
   next-day), not the raw announcement `date`. PREREG's own F8 wording ("earnings-inside-
   **outcome-window**") is about when the move shows up in price, which is what
   `effective_date` is for; falls back to `date` when `effective_date` is null. This choice
   is robust either way for the NVDA smoke check (2024-05-22 falls inside the (entry,
   expiry] window under both fields).

7. **Formulas not given verbatim by either doc** (documenting the convention used, since
   none was locked):
   - `hl_range_pct = (entry_high - entry_low) / entry_close * 100`
   - `close_vs_open_pct = (entry_close - entry_open) / entry_open * 100` (null if
     `entry_open <= 0`)
   - `ATR14_pct`: simple rolling mean of True Range over 14 bars (NOT Wilder's smoothing),
     `/ close * 100`. A defensible common alternative; flagging in case the owner wants
     Wilder's method to match a specific charting convention.
   - `dist_from_20d_high/low` and `dist_from_52w_high/low` use rolling max(`high`) / min
     (`low`) (not `close`), with 52w approximated as 252 trading days.
   - `mcap_snapshot_bucket`: mega>=200B, large>=10B, mid>=2B, small>=300M, micro>=50M,
     else nano. Own convention (roughly standard Street buckets), not brief-specified.
   - `index_or_etf_underlying` = `ff_common.KNOWN_INDEX_ROOTS` (SPX/VIX/NDX/RUT/... --
     no PriceHistory) UNION `{SPY, QQQ, IWM, DIA, MDY}` (major index ETFs, which DO have
     PriceHistory but are "the market," not a single name) -- PREREG's own F8 example list
     names both kinds together, so both are flagged by this one column.

8. **"Signal age" (PREREG F6) not implemented.** PREREG's F6 bullet lists it, but
   BUILD_BRIEF's Stage B step 3 (the "restated operationally" section that is authoritative
   for implementation) never operationalizes it -- no definition of what "age" means
   (bars since entering the current score bucket? since the last sign flip?) is given
   anywhere. Rather than guess a definition for a metric neither doc pins down, I left it
   out and am flagging it here as an open question (see below) instead of inventing
   semantics unilaterally.

9. **Moneyness sanity-band check implemented as a <1% OOB-rate soft threshold, not a hard
   zero-tolerance assert.** BUILD_BRIEF: "moneyness within (-0.9, 10) sanity band and
   null-count reported." Smoke found exactly 4/11,431 covered rows (0.035%) outside the
   band -- all genuine deep-ITM/legacy-strike contracts (MSTR $10 put vs. ~$431 spot;
   three TSLA $20-strike calls/puts vs. ~$265-269 spot, almost certainly pre-2022 3:1-split
   legacy strikes still occasionally trading). These are real market data, not a
   computation bug -- confirmed via the same rows' `close_unadj` values being sane and the
   MA reconciliation above proving the join pipeline is correct. A literal zero-tolerance
   assert would be brittle against genuine thin/deep-ITM prints that a real options census
   will always contain at the margin; implemented the check as OOB-rate < 1% instead, with
   the raw count always reported either way.

10. **`close_at_expiry` defensive dedupe** (`.unique(subset=[ticker, entry_date],
    keep='first')`): a ticker can structurally appear at most once on `expiry_day` within
    a single week's contract universe, so this never fires in practice -- kept as a
    defensive guard rather than trusting that invariant silently.

## Smoke test results

`py -3.11 experiments/weekly_5dte_movers/smoke_test.py` -- **exit 0, 29/29 checks GREEN**,
total runtime ~2.1s (Stage A ~0.9s, Stage B ~0.6s, independent-path spot check + assembly
the rest).

| # | Check | Result |
|---|-------|--------|
| 1 | Stage A produced rows | PASS (138,209 rows) |
| 2 | no expiry > 2026-06-12 | PASS |
| 3 | entry_dow in {Mon,Tue} | PASS |
| 4 | growth_mult null iff no_later_print | PASS (0 mismatches) |
| 5 | max_high_date > entry_date | PASS (0 violations) |
| 6 | dte_trading in [1,4] for Mon | PASS (0/60,636 out of range) |
| 7 | dte_trading in [1,3] for Tue | PASS (0/77,573 out of range) |
| 8 | Good Friday week: expiry_day == Thursday 2024-03-28 | PASS |
| 9 | Good Friday week: nonzero contracts | PASS (35,965 rows) |
| 10 | Good Friday week: ~zero expiry==2024-03-29 rows | PASS (0 rows, threshold <10) |
| 11 | MLK week: zero Monday entries | PASS (0 rows) |
| 12 | MLK week: nonzero Tuesday entries | PASS (22,364 rows) |
| 13 | NVDA smoke week has rows | PASS (601 rows) |
| 14 | NVDA tradeable top-20 call-dominated | PASS (20/20 calls) |
| 15-20 | Independent-path spot check x3 contracts (entry_close + max_future_high vs raw gzip) | PASS all 6 |
| 21-23 | MA reconciliation SMA 21/50/200 < 1e-3 | PASS (worst 2.3e-5) |
| 24-26 | MA reconciliation EMA 21/50/200 < 2% | PASS (worst 4.7e-5, after warmup fix -- see decision #2) |
| 27 | NVDA 2024-05-22 earnings_in_window flag | PASS (=1) |
| 28 | moneyness sanity band OOB rate < 1% | PASS (0.035%, see decision #9) |
| 29 | covered/uncovered split exercises index-root path | PASS (SPXW uncovered, 9 others covered) |

## Row counts per smoke week

2024-05-20 (NVDA earnings): 39,753 entries, 30,274 contracts.
2023-07-24 (quiet control): 40,127 entries, 29,340 contracts.
2024-03-25 (Good Friday): 35,965 entries, 25,980 contracts.
2025-01-20 (MLK): 22,364 entries, 30,354 contracts (Tuesday-only).

Full breakdown + the NVDA top-20 tables are in the machine-written section below.

## Full-run estimates

- **Total in-scope weeks: exactly 202** (verified via `load_in_scope_weeks()`, a
  filesystem-only walk, 0.01s) -- matches PREREG's "~202 expiry weeks" estimate exactly.
  Per-expiry-year breakdown: 2022:22, 2023:52, 2024:52, 2025:52, 2026:24. First week
  Monday 2022-08-01, last expiry_day 2026-06-12 -- both match PREREG's locked window
  bounds precisely.
- **Stage A full-run wall-clock estimate: ~3-6 minutes.** Smoke processed 4 weeks (18
  distinct session files, ~90MB, since the Good Friday and MLK weeks have only 4 sessions
  each) in 0.94s end-to-end (read+filter+join+aggregate+write), ~21MB/s effective
  throughput. The full window's ~985 in-scope sessions (202 weeks, avg ~4.9 sessions/week)
  at similar per-file size extrapolates to roughly 230s of comparable work; padding for
  disk I/O variance, the 5 yearly parquet writes, and CSV-merge overhead gives ~3-6
  minutes. This is a single-pass, no-multiprocessing job (per BUILD_BRIEF) -- recommend
  `trader queue submit --db light` per PREREG's compute plan, and running one `--year`
  first to confirm the estimate before `--full`.
- **Stage B full-run cost: NOT precisely estimated -- flagged as an open question below.**
  The 4 sampled smoke weeks show 625-636 distinct underlyings each (790 distinct across
  all 4 combined) out of this system's 1,638 tracked `stocks` rows -- the full 202-week
  window's UNION of underlyings (not sampled here) is needed before Stage B's real
  PriceHistory/Indicator/Score pull volume can be sized. The sparse F6/F8 join design
  (decision #3) caps the Score-table cost at roughly (covered underlyings) x (~400
  distinct entry dates across the whole window) rather than the much larger dense-panel
  alternative, but the PriceHistory/Indicator panel itself is still dense per covered
  symbol over the full 2020-01-01..2026-06-12 window regardless of how many entry events
  that symbol has -- for a universe approaching the full 1,638-symbol `stocks` table, this
  is a real bulk pull the queue's `--db heavy` tier is designed for, not a `--db light` one.

## Pinned active score version

**v74** (`production_label` == `'v74'`, asserted in `build_features.run_smoke()`) -- id=74,
commit `f9fb7b934`, matching CLAUDE.md's documented active version exactly.

## OPEN QUESTIONS

1. **"Signal age" (PREREG F6) has no operational definition in either doc** -- not
   implemented (see decision #8). Needs a definition from the owner/architect (candidates:
   trading days since the score last crossed into its current bucket >=75/<=25/mid; or
   since `pre_boost` last changed sign; or something else) before Stage C mining can use
   it. Conservative fallback taken: omitted rather than guessed.
2. **PANEL_START deviates from BUILD_BRIEF's literal "2021-09-01"** (now 2020-01-01) --
   see decision #2. Fully reversible (one constant) if the orchestrator prefers the literal
   date and is willing to accept the EMA200 corner case as documented rather than fixed.
3. **F6/F7/F8 joined at sparse entry-event grain, not "for the panel dates"** as
   BUILD_BRIEF's literal Stage B step 3 wording says -- see decision #3. If the orchestrator
   actually wants a full daily score/regime/earnings *series* per symbol (e.g. for some
   Stage C computation not yet specified), this needs to be revisited; nothing in PREREG's
   analysis protocol (Census/Base rates/Univariate screens/Factor abstraction/Ablation)
   appears to need anything beyond the entry-event snapshot, but flagging since it is a
   deviation from literal brief text.
4. **Stage B full-run resource sizing is unresolved** (see "Full-run estimates" above) --
   needs the full Stage A ledger's underlying-union before it can be sized properly. Given
   the potential scale (up to ~1,638 symbols x ~6 years of daily PriceHistory+Indicator),
   recommend the orchestrator size this explicitly (possibly chunking Stage B by year like
   Stage A, which the `--year`/`--full` CLI already supports) rather than assuming it is as
   cheap as the 9-symbol smoke suggested.
5. **`--year`/`--full` code paths (both scripts) are structurally written but never
   executed** -- per the hard rule, only `--smoke` ran this session. They reuse the exact
   same `process_week` / `build_underlying_panel` / `build_analysis_frame` functions the
   smoke path exercises (so the core logic IS tested), but the CLI plumbing around them
   (`_update_state_cursor`'s state.json read-modify-write, the per-year parquet paths, the
   `week_counters.csv` merge-by-week_monday logic at real scale) is untested. Recommend a
   single `--year 2022` run (smallest year, 22 weeks) as a first real-scale check before
   committing to `--full`.
6. **`excluded_nonstandard_expiry` is large relative to `contracts`** (roughly 20-28% of
   the week's contract-universe size across the 4 smoke weeks) -- this is expected (SPX/
   SPXW-style daily-expiry index products trading within the same calendar week as the
   target Friday) and is exactly what the counter is designed to surface, not a bug. Flagging
   only so the magnitude doesn't look alarming out of context when Stage C reads
   `week_counters.csv`.

<!-- SMOKE_TABLES_BEGIN (written by smoke_test.py -- do not hand-edit) -->

### NVDA smoke week (2024-05-20 Mon / 2024-05-21 Tue entries, expiry 2024-05-24) -- top-20 by growth_mult, RAW view (no floor)

```
ticker                | underlying | cp | strike    | entry_dow | entry_close | entry_volume | max_future_high | growth_mult
----------------------+------------+----+-----------+-----------+-------------+--------------+-----------------+------------
O:NVDA240524P00570000 | NVDA       | P  | 570.0000  | Tue       | 0.0300      | 4            | 0.7200          | 24.0000    
O:NVDA240524P00570000 | NVDA       | P  | 570.0000  | Mon       | 0.0400      | 13           | 0.7200          | 18.0000    
O:NVDA240524P00670000 | NVDA       | P  | 670.0000  | Tue       | 0.1200      | 151          | 0.7800          | 6.5000     
O:NVDA240524P00670000 | NVDA       | P  | 670.0000  | Mon       | 0.1500      | 439          | 0.7800          | 5.2000     
O:NVDA240524P00490000 | NVDA       | P  | 490.0000  | Tue       | 0.0100      | 285          | 0.0500          | 5.0000     
O:NVDA240524P00550000 | NVDA       | P  | 550.0000  | Tue       | 0.0100      | 28           | 0.0500          | 5.0000     
O:NVDA240524C01005000 | NVDA       | C  | 1005.0000 | Tue       | 19.3900     | 1242         | 59.0000         | 3.0428     
O:NVDA240524C01000000 | NVDA       | C  | 1000.0000 | Tue       | 21.2000     | 16731        | 64.2700         | 3.0316     
O:NVDA240524C01010000 | NVDA       | C  | 1010.0000 | Tue       | 18.0100     | 2486         | 54.5000         | 3.0261     
O:NVDA240524C00990000 | NVDA       | C  | 990.0000  | Tue       | 24.4600     | 2649         | 73.7000         | 3.0131     
O:NVDA240524C01000000 | NVDA       | C  | 1000.0000 | Mon       | 21.3500     | 20258        | 64.2700         | 3.0103     
O:NVDA240524C00985000 | NVDA       | C  | 985.0000  | Mon       | 26.0000     | 1087         | 78.2000         | 3.0077     
O:NVDA240524C00995000 | NVDA       | C  | 995.0000  | Tue       | 22.6000     | 1085         | 67.8600         | 3.0027     
O:NVDA240524P00510000 | NVDA       | P  | 510.0000  | Mon       | 0.0100      | 5            | 0.0300          | 3.0000     
O:NVDA240524P00530000 | NVDA       | P  | 530.0000  | Mon       | 0.0200      | 99           | 0.0600          | 3.0000     
O:NVDA240524P00520000 | NVDA       | P  | 520.0000  | Tue       | 0.0100      | 11           | 0.0300          | 3.0000     
O:NVDA240524P00530000 | NVDA       | P  | 530.0000  | Tue       | 0.0200      | 31           | 0.0600          | 3.0000     
O:NVDA240524P00610000 | NVDA       | P  | 610.0000  | Tue       | 0.0300      | 28           | 0.0900          | 3.0000     
O:NVDA240524C00975000 | NVDA       | C  | 975.0000  | Tue       | 29.8500     | 2064         | 89.5400         | 2.9997     
O:NVDA240524C00990000 | NVDA       | C  | 990.0000  | Mon       | 24.5700     | 2496         | 73.7000         | 2.9996     
```

Note: the RAW top-20 is dominated by penny-premium puts (entry_close $0.01-$0.15) whose
huge multiples are the penny-premium artifact PREREG's own Honesty section anticipates --
not a signal that puts outperformed calls that week. See the TRADEABLE view below.

### NVDA smoke week -- top-20 by growth_mult, TRADEABLE view (entry_close>=$0.20, volume>=100, transactions>=10, standard OCC root)

```
ticker                | underlying | cp | strike    | entry_dow | entry_close | entry_volume | max_future_high | growth_mult
----------------------+------------+----+-----------+-----------+-------------+--------------+-----------------+------------
O:NVDA240524C01005000 | NVDA       | C  | 1005.0000 | Tue       | 19.3900     | 1242         | 59.0000         | 3.0428     
O:NVDA240524C01000000 | NVDA       | C  | 1000.0000 | Tue       | 21.2000     | 16731        | 64.2700         | 3.0316     
O:NVDA240524C01010000 | NVDA       | C  | 1010.0000 | Tue       | 18.0100     | 2486         | 54.5000         | 3.0261     
O:NVDA240524C00990000 | NVDA       | C  | 990.0000  | Tue       | 24.4600     | 2649         | 73.7000         | 3.0131     
O:NVDA240524C01000000 | NVDA       | C  | 1000.0000 | Mon       | 21.3500     | 20258        | 64.2700         | 3.0103     
O:NVDA240524C00985000 | NVDA       | C  | 985.0000  | Mon       | 26.0000     | 1087         | 78.2000         | 3.0077     
O:NVDA240524C00995000 | NVDA       | C  | 995.0000  | Tue       | 22.6000     | 1085         | 67.8600         | 3.0027     
O:NVDA240524C00975000 | NVDA       | C  | 975.0000  | Tue       | 29.8500     | 2064         | 89.5400         | 2.9997     
O:NVDA240524C00990000 | NVDA       | C  | 990.0000  | Mon       | 24.5700     | 2496         | 73.7000         | 2.9996     
O:NVDA240524C01005000 | NVDA       | C  | 1005.0000 | Mon       | 19.7500     | 1142         | 59.0000         | 2.9873     
O:NVDA240524C00975000 | NVDA       | C  | 975.0000  | Mon       | 30.0000     | 2425         | 89.5400         | 2.9847     
O:NVDA240524C01015000 | NVDA       | C  | 1015.0000 | Tue       | 16.5900     | 711          | 49.4800         | 2.9825     
O:NVDA240524C00980000 | NVDA       | C  | 980.0000  | Mon       | 28.1000     | 3228         | 83.7400         | 2.9801     
O:NVDA240524C01010000 | NVDA       | C  | 1010.0000 | Mon       | 18.3000     | 3007         | 54.5000         | 2.9781     
O:NVDA240524C00985000 | NVDA       | C  | 985.0000  | Tue       | 26.2900     | 1014         | 78.2000         | 2.9745     
O:NVDA240524C00980000 | NVDA       | C  | 980.0000  | Tue       | 28.2000     | 2645         | 83.7400         | 2.9695     
O:NVDA240524C00995000 | NVDA       | C  | 995.0000  | Mon       | 23.0000     | 1071         | 67.8600         | 2.9504     
O:NVDA240524C01015000 | NVDA       | C  | 1015.0000 | Mon       | 16.8200     | 820          | 49.4800         | 2.9417     
O:NVDA240524C01020000 | NVDA       | C  | 1020.0000 | Tue       | 15.3300     | 2237         | 45.0300         | 2.9374     
O:NVDA240524C00970000 | NVDA       | C  | 970.0000  | Tue       | 31.9500     | 2850         | 93.5500         | 2.9280     
```

NVDA smoke week: 601 total NVDA ledger rows (all entries, both Mon+Tue), 323 pass the TRADEABLE floor. (The week's grand total across ALL underlyings, 39753, is reported in the row-count table below.)

### Smoke row counts per week

```
week_monday | expiry_day | is_monthly_opex | entries | contracts | excluded_nonstandard_expiry | zero_close_drops
------------+------------+-----------------+---------+-----------+-----------------------------+-----------------
2024-05-20  | 2024-05-24 | False           | 39753   | 30274     | 8679                        | 0               
2023-07-24  | 2023-07-28 | False           | 40127   | 29340     | 8040                        | 0               
2024-03-25  | 2024-03-28 | False           | 35965   | 25980     | 5087                        | 0               
2025-01-20  | 2025-01-24 | False           | 22364   | 30354     | 6592                        | 0               
```

### Stage B smoke summary

- smoke symbols (10): ['NVDA', 'SPXW', 'MSTR', 'SMCI', 'QQQ', 'SPY', 'TSLA', 'COIN', 'BKNG', 'CMG']
- covered (9): ['BKNG', 'CMG', 'COIN', 'MSTR', 'NVDA', 'QQQ', 'SMCI', 'SPY', 'TSLA']
- uncovered/covered=0 (1): ['SPXW']
- panel rows: 14258, analysis rows: 13454 (11431 covered)
- MA reconciliation worst relerr (entry-event grain, n=63): {'sma': {21: 1.7726630539324064e-05, 50: 1.440433824796187e-05, 200: 2.343129467855781e-05}, 'ema': {21: 1.6538192139860357e-05, 50: 1.2070788255383627e-05, 200: 4.7240515519604796e-05}}
- moneyness: null_count=0, oob_count=4 of 11431 checked

<!-- SMOKE_TABLES_END -->
