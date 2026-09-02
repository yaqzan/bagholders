# Gamma x IV Phase B — 4-arm paired A/B on the Polygon real-IV panel

Pre-registered 2026-07-10 (BEFORE any compute). Runner: subagent; verdict: FABLE.
Status header: this run SUBSUMES gameplan P1.3 (Phase A) + P2.3 (Phase B), dated 2026-07-10.

## Premise correction (recorded)

The P2.3 brief assumed "Phase A PASSED" and "IV_PREMIUM plug points wired in Phase A."
Repo evidence shows neither happened:

- `IV_PREMIUM` had ZERO code hits anywhere (only prose: gameplan.md:117, data-acquisition.md,
  the prototype scaffold docstring).
- Gameplan P1.3 carries no DONE tag; data-acquisition.md says the gamma engine "awaits
  IV-premium (step 1)"; traps.md calls the IV-premium workstream "active, unblocked".
- The only gamma A/B artifacts (`experiments/concentration_2x/results/sweep_drill_{goff,gon}.json`,
  2026-07-01) are the 2-arm GAMMA_AWARE-only run that PRODUCED the +1754% explosion finding.

FABLE (2026-07-10) directed: execute the scaffold's steps 2+3
(`experiments/data_ingest/polygon_iv_gamma_prototype.py`) as written — build the env-gated
IV_PREMIUM plug in the two engine files' premium plumbing ONLY (monte_carlo.py +
backtest_cascade.py), then run the 4-arm A/B on the Polygon panel. This single run therefore
subsumes P1.3 + P2.3.

## Goal

Instrument fidelity, NOT alpha mining. Question: does a REAL-IV premium kill the known
+1754% gamma explosion (documented error-cancellation unmasking: realized-vol premium too
cheap for big movers x const-delta too linear)? Reference explosion numbers (own 2-arm run,
window grid 2016-06-01..2026-04-15, 38 windows, N_ITER=150, N_paths=5,700/cell):

| cell        | arm  | P(2x) | P(2x<50dd) | collapse | med days2x | med compound | worstDD |
|-------------|------|-------|------------|----------|------------|--------------|---------|
| flat_n4_a25 | goff | 70.9% | 52.5%      | 0.0%     | 129        | +38.9%       | 82.4%   |
| flat_n4_a25 | gon  | 98.1% | 70.3%      | 0.0%     | 87         | +1753.9%     | 75.8%   |
| cascade_ref | goff | 62.4% | 59.7%      | 0.0%     | 275        | +91.6%       | 71.4%   |
| cascade_ref | gon  | 100.0%| 96.2%      | 0.0%     | 148        | +1485.2%     | 67.7%   |

## Panel + coverage degradation statement (MANDATORY in every results file)

Panel: `.cache/polygon_iv/iv_ledger_polygon.parquet` — 8,643 rows, 669 distinct symbols,
dates 2022-08-01..2026-05-15 (date column = str YYYY-MM-DD). atm_iv null/NaN = 0%;
atm_iv median 0.464, range 0.064..4.454; contract dte median 25 (20..45).

DEGRADATION: the Polygon Developer plan's 4-year lookback cap means **2021 and H1-2022 are
ABSENT** (gameplan's "2021-25" was optimistic; reality starts 2022-08-01). Consequences:

- Canonical MC windows UNAVAILABLE for IV coverage: `2020_crash` (COVID), any 2021 window,
  the 5y-from-2021 and 10y windows, and the Jan..Jul-2022 leg of the 2022 bear.
- The decisive stress window that IS covered: **2022-08..2022-10 bear leg** (SPX Aug-17 top
  -> Oct-13 low). Panel rows: 2022-08=92, 2022-09=234, 2022-10=158 (thin vs later years:
  2024=2,527, 2025=2,858).
- 2022 total = 687 rows; coverage of traded signals in 2022 windows will be materially lower
  than 2024+ windows. Per-window IV coverage % is a REQUIRED output column.

Late-window fallback note: the sweep's price/score data ends 2026-04-15, which is BEFORE the
panel end (2026-05-15), so no entry can fall past the panel's end — misses are driven by panel
SPARSITY (symbol-date not ingested), not by date-range truncation at the tail.

## Mechanism (engine plug, built for this run per FABLE spec)

Env surface (default OFF = bit-identical to production; same pattern as GAMMA_AWARE):

- `IV_PREMIUM=1` enables; `IV_PREMIUM_PANEL=<path>` overrides the panel
  (default `.cache/polygon_iv/iv_ledger_polygon.parquet`).
- Lazy module-level loader -> dict {(symbol, date_str): atm_iv}; polars read,
  `fill_nan(None)` then `drop_nulls` on atm_iv (polars NaN!=null trap).
- ONE helper per engine file (`_iv_premium_pct`). On (symbol, signal_date) hit:
  `premium_pct = 0.4 * atm_iv * sqrt(dte/365)`. On miss/disabled: the EXISTING
  realized-vol formula's value (fallback), counted as a miss.
- Hits/misses counted per process; flushed at exit to `IV_COVERAGE_DIR` when set.

Units check (hand-verified): existing `premium_pct = PREMIUM_MULT * vol / 100` with
PREMIUM_MULT=1.82, vol=2.0 (% daily) -> 0.0364 = fraction-of-underlying. IV formula with
median atm_iv 0.464 at dte=30: 0.4 * 0.464 * sqrt(30/365) = 0.0532 = 5.3% of underlying —
same units (fraction), sane ATM 30-DTE cost. A unit test asserts both paths.

Premium sites routed through the helper (ALL per-trade option-COST sites; the K-sigma
trigger-barrier definitions stay realized-sigma per spec — IV changes option COST only):

| file | site | context | dte used by IV formula |
|------|------|---------|------------------------|
| monte_carlo.py | compute_trade_outcome (premium assign after the CALENDAR_HOLD block, lines ~1862/1871) | call premium -> prem-stop, dead-hold walk, outcome dict (resolve() + sizing caps consume the carried value) | NOMINAL_CAL_DTE if CALENDAR_HOLD else 30 |
| monte_carlo.py | compute_put_outcome (~2094) | put premium (puts OFF in all Phase-B cells; threaded for consistency) | 30 (existing put premium has no cal-DTE scaling) |
| backtest_cascade.py | compute_outcome (~1356) | call premium -> _option_aware_pnl, MTM, net returns | nominal_cal_dte if calendar_hold else total_dte |
| backtest_cascade.py | compute_put_outcome (~1598) | put premium | DEFAULT_TOTAL_DTE (=30; matches its option_pnl_pct calls) |
| backtest_cascade.py | run_backtest._mtm_pnl (~1929) | open-position MTM premium recompute | the total_dte already computed there (bt_nominal_cal_dte if calendar hold else pos.outcome.dte) — same key -> same IV value as entry |

NOT touched (K-sigma predicand / barrier geometry, per spec): mc tp_level/sl_level
(1863-64/1872-73), mc put tp/sl sigma conversions (2073/2104) and the diagnostic
fire_sl_level (2141), bc tp_price/sl_price (1352-53/1591-92). PREMIUM_MULT/DELTA remain
the barrier-space conversion constants.

Symbol threading: mc compute_trade_outcome/compute_put_outcome gain an OPTIONAL
`symbol=None` kwarg (backward compatible; None -> fallback premium); the two internal call
sites (precompute_outcomes:2202, precompute_put_outcomes:2221) pass `sig.symbol_id` (= ticker
string; Stock.symbol is a CharField primary key). bc functions already receive `symbol`.

Engine-edit scope (FABLE-lifted, exactly): monte_carlo.py + backtest_cascade.py premium
plumbing. NOT touched: option_pricing.py, strategy_config.py, scoring/core/simulator/api/
trader, gamma_validation originals. Experiment tooling: sweep.py gains ONE minimal
backward-compatible optional flag `--hist-start` (default = existing HIST_START constant)
to clip the window grid; all other files under experiments/gamma_iv_phaseb/ are NEW.

## Blast radius (manual analysis — gitnexus MCP tools NOT connected in this session;
ToolSearch returned no gitnexus tools, so gitnexus_impact could not be run; substituted
with exhaustive grep caller analysis)

| symbol | d=1 callers | risk |
|--------|-------------|------|
| mc compute_trade_outcome | mc precompute_outcomes:2202 (only) | LOW — optional kwarg, default-off branch |
| mc compute_put_outcome | mc precompute_put_outcomes:2221 (only) | LOW — same |
| mc precompute_outcomes / precompute_put_outcomes | mc _prepare_window:3657/3714 | LOW — internal call-site kwarg add, signature unchanged |
| bc compute_outcome | bc:2823 (only caller); barrier_walk_numba + n_floor check reference it in comments/mirrors only (no import) | LOW — insert-only, default-off |
| bc compute_put_outcome | bc:2856 (only) | LOW — same |
| bc run_backtest (nested _mtm_pnl) | bc:2900, bc:2988 | LOW — insert-only inside nested fn, default-off |
| sweep.py main | CLI only | LOW — new optional flag, default preserves behavior |

monte_carlo_15dte.py has its OWN unrelated compute_trade_outcome (not modified).

## Known dilution caveat (pre-registered)

Production 30-DTE config has DTE_ROUTER_ENABLED=True (strategy_config.py:1180): a small
subset of 80+ calls (~117 signals/5y historically) resolves through monte_carlo_15dte.py,
which is OUT of edit scope — those routed trades keep the realized-vol premium in the IV
arms. Identical across paired arms (same seeds, same routing), so it dilutes but does not
bias the IV delta. The per-window `routed=` counts from the PREPARE logs will be reported.

## Arms (paired seeds; the ONLY differences are the two env flags)

| arm | GAMMA_AWARE | IV_PREMIUM |
|-----|-------------|------------|
| base | unset | unset |
| gamma | 1 | unset |
| iv | unset | 1 |
| gammaiv | 1 | 1 |

Cells: `flat_n4_a25` (staged fast-2x sprint) + `cascade_ref` (production Apex cascade),
via `experiments/concentration_2x/sweep.py --stage drill`. Starting cash $50,000,
workers 6, step 3 months, horizon 730d, calendar-hold 30-DTE engine
(calendar_hold=True, nominal_cal_dte=30, hold_cal_days=27, gross/call premium caps 0.5).

## Windows (clipped to panel coverage: --hist-start 2022-08-01, panel-safe end automatic)

Quarterly grid anchored 2022-08-01, runway floor 180d, data end 2026-04-15 -> **13 windows**:

roll_2022-08-01 (DECISIVE: contains the Aug..Oct-2022 bear leg from the Aug-17 top),
roll_2022-11-01, roll_2023-02-01, roll_2023-05-01, roll_2023-08-01, roll_2023-11-01,
roll_2024-02-01, roll_2024-05-01 (end-capped 714d), roll_2024-08-01 (622d),
roll_2024-11-01 (530d), roll_2025-02-01 (438d), roll_2025-05-01 (349d),
roll_2025-08-01 (257d).

End-capped windows are identically truncated across all 4 arms (paired), matching the
original goff/gon grid's own tail truncation.

## N

N_ITER=300 per window per cell per arm -> N_paths = 13 x 300 = 3,900 per cell per arm.
Reporting rule: every cell N-labeled; any cell with N<30 is SKIPPED (not expected here).

## PASS bars (verbatim, pre-registered; inherited P1.3 -> P2.3; FABLE judges, runner only produces numbers)

"the +1754% explosion collapses to sane numbers — median compound within ~2x the base arm,
P(2x) not pinned near 100%, DD within +/-5pp of base, collapse=0 — with the
2022-08..2022-10 bear leg included as the decisive window."

PASS => gamma leg of the L3 data-buy case reopens. FAIL => gamma parked permanently at
<=$79 sunk.

## Validation-arm protocol (mandatory, queued, BEFORE the real run)

Arm `valgoff`: both flags unset, ORIGINAL goff settings (N_ITER=150, full 38-window grid
2016-06-01, step 3, cells flat_n4_a25 only, workers 6, cash 50k), tag `phaseb_valgoff`.
Comparator (`validate_repro.py`) checks per-window partials bit-exact (parsed-JSON array
equality of finals/dds/t2x/t50 per path) against the archived
`experiments/concentration_2x/results/drill_goff_partial/roll_*.json`, plus the aggregate
flat_n4_a25 metrics against `sweep_drill_goff.json`. Expect EXACT equality (seeded engine;
edits are invisible when flags off). ANY diff = STOP and investigate (distinguish edit
breakage from underlying data drift, e.g. yfinance retro-adjustments since 2026-07-01,
by checking whether diffs are uniform across windows).

## Trap statements

- build_iv/polygon pnl15 clamp trap: DOES NOT BIND — this A/B consumes ONLY `atm_iv` from
  the panel (premium formula); no pnl15/fwd-label field is read. (Also: the current
  polygon_iv_ingest.py already nulls <15-forward-bar labels, commit ebca1e1b7; traps.md's
  "still clamped" note is stale on that point.)
- polars NaN family: loader does fill_nan(None) -> drop_nulls before dict build; no
  rank/join/spearman on the panel.
- Holdout: panel capped 2026-05-15 < CALIBRATION_CUTOFF_DATE=2026-06-15; sweep data ends
  2026-04-15. No signal-level frame is constructed outside the engines; if any is added
  later it must pass experiments/_holdout.assert_no_holdout_leak. HOLDOUT_DISABLE never set.
- Windows MP: engine files are NOT edited while any queued job that imports them is in
  flight (checked `trader queue status` before editing; also applies to this experiment's
  own queued arms).
- Queue exit codes: terminal state read from `trader queue wait` OWN output text, never a
  piped exit code.

## Planned outputs

- `experiments/gamma_iv_phaseb/results/phaseb_results.json` + `phaseb_results.txt` (ASCII):
  per-arm x per-cell table {median compound, P(2x), P(2x<50dd), WorstDD, collapse, N,
  IV hit/miss coverage %}, per-window coverage table, the goff/gon full-span rows included
  as REFERENCE-ONLY (different window grid — labeled as such), runtime + task ids,
  the coverage degradation statement above.
- Per-arm sweep outputs: `experiments/concentration_2x/results/sweep_drill_phaseb_<arm>.json`
  (+ resumable per-window partials under `drill_phaseb_<arm>_partial/`).
- Runtime coverage counters: `experiments/gamma_iv_phaseb/results/coverage_<arm>/ivcov_*.json`.
- Per-window signal-level IV coverage: computed read-only from Score(v74, >=75) vs panel keys
  (`coverage_by_window.json`).

## Runtime estimate (pre-registered)

Original goff (38 win x 2 cells x N=150, 6 workers) = ~61 min wall; gon = ~70 min.
Phase B per arm: 13 win x 2 cells x N=300 ~= (13/38)x(300/150) x 65 min ~= 45 min worst
case, likely ~15-25 min (2022+ windows ran ~25s each at N=150 in goff).
4 arms sequential ~= 1-2.5h + validation ~35-40 min. Total well under the 6h ceiling.
Queue: --priority normal (market hours Friday; below the scheduled trader-update tier),
--db heavy, --cpu 6, --restartable, per-arm dedup keys.

## Implementation notes (executor, 2026-07-10)

Scaffolding found on disk (DESIGN.md + run_phaseb.py) was already complete and matched
this spec; no rewrite needed. What the executor actually did:

**gitnexus re-confirmed unavailable.** ToolSearch for `select:gitnexus_impact,gitnexus_context,
gitnexus_detect_changes,gitnexus_query,gitnexus_rename,gitnexus_cypher` and a bare `gitnexus`
keyword search both returned zero matches in the executor session too (not a fluke of the dead
agent's session). Blast radius below is exhaustive manual grep + direct code reading, not
`gitnexus_impact` output.

**Plug implemented exactly per spec** in monte_carlo.py and backtest_cascade.py (module-level
lazy loader + `_iv_premium_pct()` helper, `symbol=None` optional kwarg added to mc's
`compute_trade_outcome`/`compute_put_outcome`, no signature change needed in bc's
`compute_outcome`/`compute_put_outcome` since they already take `symbol` positionally).
sweep.py got the planned `--hist-start` flag (optional, default preserves `HIST_START`),
wired into both `monthly_windows()` call sites.

**Hand-verification (spec item 3), real panel row ELF/2022-08-04 (atm_iv=0.3486, dte=30):**
IV-path `_iv_premium_pct('ELF', date(2022,8,4), 30, fallback)` = 0.4*0.3486*sqrt(30/365) =
**0.03997620552552703**, matched bit-exact against an independent computation. Verified
end-to-end (not just the helper in isolation) through `mc.compute_trade_outcome` and
`mc.compute_put_outcome` with synthetic 100+-bar OHLCV series: passing `symbol='ELF'` on the
real date changes ONLY `premium_pct` (0.0206->0.0400 / 0.0233->0.0400 in the two test runs);
`fire_tp_level`/`fire_sl_level`/`vol` are bit-identical to the no-symbol call in every case
(confirms IV changes option COST only, never the K-sigma barrier geometry). OFF-path
(`IV_PREMIUM` unset) verified via full dict equality: `compute_trade_outcome(bars, sig, False)`
== `compute_trade_outcome(bars, sig, False, symbol='ELF')` (every key, including `premium_pct`)
when the flag is unset -- the new kwarg is provably inert. Same hit/fallback/None-symbol/
count=False behavior independently re-verified against bc's own copy of the helper (bc and mc
each carry a private copy, not a shared import -- matches the existing GAMMA_AWARE precedent
of per-file option-pricing consumption, though GAMMA_AWARE itself centralizes in
option_pricing.py; the IV helper is small enough that duplicating it was judged simpler/lower-risk
than adding a new shared cross-file import edge).

**Per-window coverage: implemented differently than originally planned.** The "Planned outputs"
section above describes a *separate read-only* `coverage_by_window.json` computed from
`Score(v74, >=75)` vs panel keys. Instead, the executor wired coverage directly into the engine:
`_prepare_window` (mc) snapshots `_IV_HITS`/`_IV_MISSES` before the call-outcomes precompute and
emits the delta (print + optional `IV_COVERAGE_DIR/ivwin_mc_<label>_<pid>.json`) after the
put-outcomes precompute. Rationale: `_prepare_window` runs once per window in the sweep's *main*
process (confirmed by reading `run_window_inproc`'s docstring and sweep.py's window loop -- PREPARE
runs once per window before the per-cell MP pool simulates paths), so this reflects the engine's
actual consumption (both cells, calls AND puts, any DTE-router dilution) rather than a synthetic
score-threshold proxy. Arm-level coverage = sum of a run's per-window files. bc keeps a simpler
process-level-only `atexit`-flushed total (`IV_COVERAGE_DIR/ivcov_bc_<pid>_<uuid>.json`, tag='bc')
since it has no rolling-window concept of its own.

**bc._mtm_pnl uses `count=False`.** MTM re-marks key on the position's ENTRY `(symbol, date)` --
identical every day of the hold -- so counting them would inflate hit/miss volume without adding
new coverage information (the entry call already counted that exact key once). Primary coverage
stat = per-trade, at entry.

**backtest_cascade.py's plug is INERT for this specific sweep.** `experiments/concentration_2x/
sweep.py` imports `monte_carlo` only (`import monte_carlo as mc`, no `backtest_cascade` import) --
confirmed by reading `run_window_inproc`/`main()`. So the 4-arm A/B below exercises ONLY mc's
plug; bc's plug will show zero hits/misses in this run's coverage output by construction, not
because of a bug. It is still in scope per FABLE's explicit "monte_carlo.py + backtest_cascade.py"
instruction, for whole-system fidelity: `portfolio_engine.py` (the LIVE Portfolio tracker) calls
`bc.compute_outcome`/`bc.compute_put_outcome` directly (grep-confirmed, lines ~514-516/542-554),
so the plug is real production surface, just not one this harness's arms touch. Since `IV_PREMIUM`
defaults OFF and off-path bit-identity was hand-verified above, this is zero-risk to the live
tracker today.

**Updated blast radius** (manual grep + direct read, gitnexus unavailable -- see above):

| symbol | d=1 callers found | risk |
|--------|-------------------|------|
| mc compute_trade_outcome | mc precompute_outcomes (1 site) | LOW -- optional kwarg, hand-verified bit-identical off-path (full dict equality) |
| mc compute_put_outcome | mc precompute_put_outcomes (1 site) | LOW -- same |
| mc precompute_outcomes / precompute_put_outcomes | mc _prepare_window (2 sites) | LOW -- internal kwarg add only |
| mc _prepare_window | sweep.py run_window_inproc (via `mc` module) | LOW -- added print + gated optional file write; returned ctx dict shape unchanged |
| bc compute_outcome | bc signal-loop (1 site, ~2823) + portfolio_engine.py (~line 516, LIVE tracker) | LOW -- insert-only, hand-verified bit-identical off-path; portfolio_engine risk is zero while IV_PREMIUM unset in production |
| bc compute_put_outcome | bc signal-loop (1 site, ~2856) + portfolio_engine.py (~line 514) | LOW -- same |
| bc run_backtest / nested _mtm_pnl | internal to run_backtest (_pick_displacement, equity_mtm loop) | LOW -- insert-only, default-off, count=False keeps coverage semantics clean |
| sweep.py main() | CLI only | LOW -- new optional `--hist-start`, default reproduces the original 38-window goff grid (re-verified: `monthly_windows()` with no override still starts 2016-06-01) |

No other callers of these 8 symbols exist outside `algorithm_versions/*/portfolio_sources/`
(frozen historical silo snapshots, not live imports -- untouched by this change) and
`experiments/*` test/sweep harnesses (all pass positional args unaffected by the new optional
kwarg). Repo-wide grep for `IV_PREMIUM`/`_iv_premium_pct`/`_load_iv_panel` before this session
returned only this experiment's own DESIGN.md/run_phaseb.py + the prototype script + gameplan.md
prose -- clean slate, no naming collisions.

## Do-not

No commits (FABLE commits). No verdict from the runner. No edits to option_pricing.py /
strategy_config.py / scoring / gamma_validation originals. No engine edits while queued
jobs are in flight. ASCII-only in code/logs. No HOLDOUT_DISABLE.

---

## Validation protocol amendment (2026-07-10, FABLE) — archive repro replaced by same-day inertness proof

The pre-registered validation arm (reproduce the archived 07-01 `sweep_drill_goff.json` bit-exactly)
FAILED: 8/16 aggregate fields, 0/38 windows (results/validation_repro.txt). Mismatch uniform across
all 38 windows incl 2016; run-config/structure fields match; flags-off code paths read inert.

DECISIVE TEST (queue task 586): the identical invocation on the PRISTINE tree (this session's
monte_carlo.py / backtest_cascade.py / sweep.py diffs stashed; the pre-existing option_pricing.py
GAMMA_AWARE diff kept constant — present in both reference and new runs) against the same-day DB:
EDITED == PRISTINE **bit-exactly** (16/16 aggregate fields, 38/38 window partials json-identical).

Binding conclusions:
1. The IV_PREMIUM / --hist-start edits are PROVEN inert when flags are off.
2. The valgoff-vs-archive mismatch is DATA DRIFT: the MC substrate (PriceHistory) is retro-adjusted
   by daily yfinance pulls (dividend/split back-adjustment rewrites whole histories; the deep 1995
   backfill also landed 07-01..07-06). Archived MC artifacts are NOT bit-reproducible after substrate
   drift — a 9-day-old reference moved P(2x) by -15.4pp and sign-flipped median compound on the
   4-name sprint cell.
3. The 4-arm A/B is therefore judged ONLY on same-day internal pairing (all arms against the same DB
   state, same seeds); the 07-01 goff/gon numbers are REFERENCE-ONLY context. PASS bars unchanged
   (arm-vs-arm, relative to base).
4. Repo follow-on at closeout: traps.md entry — never use an archived MC artifact as a bit-repro
   validation target; validate engine edits via a same-day pristine-vs-edited A/B.

---

## Amendment 2 (2026-07-10, FABLE): as-of IV join -- coverage defect fix

Pre-registered BEFORE any compute (the engine edits it specifies are made only after this
section is committed to the file).

### (a) Defect

The 4-arm A/B (queue tasks 587-590) proved flags-off inertness (see the validation-protocol
amendment above) but surfaced a second, independent defect in the IV_PREMIUM-enabled arms:
real-world IV coverage was **15.6%** (48,904 hits / 313,237 lookups; see the archived
`results/coverage_iv/` and `results/coverage_gammaiv/` per-window JSON files). Root cause:
`_iv_premium_pct` joined `(symbol, entry_date)` **EXACT** against a panel keyed by **signal
date**. Two effects starve the exact join:

- Cascade fills: a signal can wait for portfolio capacity and actually enter one or more
  trading days after its own signal date, so the entry date the engine looks up often has
  no panel row even though the signal's own date does.
- Window tails: entries late in a window's grid can land close to (or, combined with cascade
  delay, past) the panel's 2026-05-15 end, where panel density thins out symbol-by-symbol
  ahead of the hard cutoff.

The `iv` (589) and `gammaiv` (590) results (`sweep_drill_phaseb_iv.json` /
`sweep_drill_phaseb_gammaiv.json`, `coverage_iv/` / `coverage_gammaiv/`, plus the
`concentration_2x/results` sweep-side partials for those two tags) are **superseded** by
this fix and have been archived with a `_cov16` suffix (documenting the 15.6% coverage they
were produced under). `base` (587) and `gamma` (588) are UNAFFECTED and remain valid --
see (e).

### (b) New join spec

For each lookup `(symbol, entry_date)`:

1. Exact-date match against the `{(symbol, date): atm_iv}` dict remains the fast path
   (unchanged; O(1), 0-day-stale by definition).
2. On an exact miss, binary-search (bisect) the symbol's own sorted panel dates for the
   most recent `panel_date <= entry_date`.
3. Accept that row only if `entry_date - panel_date <= 14` calendar days. Otherwise: miss
   (falls back to the existing realized-vol premium, exactly as before).

This is **strictly backward** by construction -- the search only ever considers
`panel_date <= entry_date`, so it cannot introduce look-ahead. The 14-day cap bounds
staleness (an ATM IV reading is a reasonable proxy for up to ~2 weeks; beyond that the
existing realized-vol fallback is judged safer than a stale IV read).

### (c) Logging

Hit/miss counts unchanged in shape (mc: per-window delta via `_prepare_window`; bc:
process-level cumulative via `_flush_iv_coverage`, atexit-flushed). PLUS a staleness
histogram bucketed into `0d` (exact), `1-3d`, `4-7d`, `8-14d`, tallied at the moment of
each hit and reported alongside hits/misses in both the stdout line and the JSON coverage
artifact (new keys: `stale_0d`, `stale_1_3d`, `stale_4_7d`, `stale_8_14d`).

### (d) Pre-registered decision rule

- Post-fix coverage **>= 60%** -> judge the `iv`/`gammaiv` re-run against the registered
  PASS bars (see the "PASS bars" section above), same as originally planned.
- Post-fix coverage **< 60%** -> P2.3 closes **COVERAGE-BLOCKED**. This is distinct from a
  registered FAIL (the gamma-under-real-IV question was never actually answered at
  sufficient N), but carries the **same consequence** for the L3 data-buy case: the gamma
  leg does **NOT** reopen either way. (A COVERAGE-BLOCKED close should be read as
  "underpowered", not "tested and failed" -- a future re-attempt needs a denser panel, not
  a re-run of this join fix.)

### (e) Scope

`base` (587) and `gamma` (588) arms are **unaffected and remain valid** -- the fix lives
entirely inside the `IV_PREMIUM`-enabled branch of `_iv_premium_pct` / `_load_iv_panel`
(consulted only when `IV_PREMIUM=1`); the flags-off early-return guard
(`if not IV_PREMIUM or symbol is None`) is unchanged, and the flags-off path was
independently proven inert by the same-day pristine-vs-edited A/B above (Amendment 1).
Only `iv` (589) and `gammaiv` (590) are re-run.

Engine changes: `monte_carlo.py` + `backtest_cascade.py`. `_load_iv_panel()` gains a
per-symbol sorted-date-array side structure (built once at lazy load, alongside the
existing exact dict) for the as-of bisect fallback; `_iv_premium_pct()` gains the as-of
fallback + staleness tally. No new env vars -- `IV_ASOF_MAX_DAYS=14` is a fixed module
constant per this spec, not env-gated. No call-site signature changes (mc's 4-positional-arg
and bc's `count=`-kwarg call sites are untouched).
