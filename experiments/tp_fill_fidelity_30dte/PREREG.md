# PREREG — 30-DTE TP-fill fidelity measurement (2026-08-10)

STATUS: **LOCKED 2026-08-10, before any outcome was computed or viewed.** This is a
descriptive fidelity MEASUREMENT (FF-4 class), not a hypothesis sweep — but the event
definitions, slices, and knob-mapping rules below are frozen now so the calibration
cannot be fit to taste after numbers appear. Amendments after outcomes: tightening /
coverage-accounting only, labeled as such.

## 0. Scope + honesty header

- Measured object: the 30-DTE engine's TP exit assumption — declaration (underlying
  sigma-barrier intrabar touch, `compute_trade_outcome` mc:2493-2509) and fill
  (default `Uniform(tp_level, high)` underlying sample, mc:3006-3007) — against real
  traded option prints from the Polygon flat-file derived ledger.
- Prior evidence, stated honestly: FF-1 Link-1 (2026-08-03) found real contract HIGH
  median 1.352 at the TP30 sigma-touch (liquid, DTE 25-38, close-only touch walk) —
  the CENTRAL TENDENCY of the sigma->option mapping is validated. What is UNMEASURED
  is the miss tail (P(no print >= barrier)), its liquidity-tier structure, the TP15
  level, gap-open share, and never-fill-by-deadline — i.e. exactly what the inert
  fill knobs parameterize.
- NOT licensed by this measurement: no knob DEFAULT flip (that is a Stage-3 ship with
  its own MC A/B), no scoring claim, no entry-timing claim, no liquidity-floor claim
  (FF-3' Stage B' owns that). Output = measured table + calibration recommendation.
- Expectations registered pre-outcome (so surprise is detectable): same-day miss rate
  15-40% pooled (high-basis); never-fill materially lower; monotone worse toward t1
  (illiquid); no-print misses concentrated in t1/t2; gap-open share of fills low
  single-digit % (FF-4's SL-side gap incidence was 3.6% of days); real high-mult
  median on touch days within [1.25, 1.45] at TP30 on the matched slice.

## 1. Inputs (exact artifacts; all pre-built, read-only)

- Signals: `.cache/flatfile_exploitation/signals_v74_2022_2026.parquet` (4,936).
- Contracts + real paths: `B:\polygon_derived\ledger_v2\ledger.parquet` (4,403 kept)
  + `B:\polygon_derived\ledger_v2\paths\year=*` (71,278 traded contract-days).
  Contract rule (FF-1, frozen): target 30 cal-DTE, band [18,50], nearest-ATM
  strike-rank <=3 on close_unadj, `adjusted=true` roots excluded.
- Underlying daily OHLC, ENGINE convention (price_history adjusted bars):
  `.cache/flatfile_exploitation/underlying_ohlc_2022_2026.parquet` if schema check
  passes (full OHLC + engine-matching values on a 5-symbol × 3-date spot audit vs
  MySQL price_history); else ONE queued bulk pull (2022-04-01..2026-08-08, ledger
  symbols only, --db heavy) materialized to
  `.cache/tp_fill_fidelity_30dte/underlying_ohlc.parquet`. VOL_LOOKBACK=60 requires
  bars from >=60 trading days before 2022-08-01.
- Liquidity tiers: FF-3' map via `B:\polygon_derived\liquidity_map\signal_liquidity.parquet`,
  cut at the FF-4 tier edges (`B:\polygon_derived\minute_fidelity\bindings.json`) —
  t1=least liquid .. t5=most. Echo the exact edges used to `out/bindings_echo.json`.

## 2. Engine-declaration protocol (P1)

- Env pins BEFORE `import monte_carlo`: `MC_NO_DB_PERSIST=1`, `LIQUIDITY_FLOOR=0.0`,
  `PYTHONIOENCODING=utf-8`. NO profile env diff — strategy defaults already are the
  30-DTE production config (CALENDAR_HOLD=True, HOLD_CAL_DAYS=27, NOMINAL_CAL_DTE=30,
  PREMIUM_MULT=1.82, DELTA=0.50; both live profiles share them).
- Runtime asserts (abort on failure): `mc.CALENDAR_HOLD is True`,
  `mc.HOLD_CAL_DAYS == 27`, `mc.NOMINAL_CAL_DTE == 30`, `mc.NEXT_OPEN_ANCHOR` falsy,
  `mc.TSL_ENABLED` falsy, `mc.PREM_STOP_LOSS >= 0` (premium stop disabled),
  `mc.__file__` under `C:\Development\Trader`, and after set_tpsl:
  `TP_SIGMA_BASE == TP_SIGMA_STRESS` (flat stress).
- Two arms via `experiments/tpsl_refine_2026_08/driver/mc_patch.py::set_tpsl`
  (proven full re-derivation chain):
  - **ARM-30:** TP=+0.30, SL=-0.70 — the incumbent/production cell.
  - **ARM-15:** TP=+0.15, SL=-0.90 — the leading Phase-A candidate cell, so numbers
    plug directly into the live tpsl_refine decision.
- Per ledger-kept signal (symbol, signal_date): build `sym_bars` as the engine
  expects — tuples ordered **(date, close, high, low, open)** (mc:2418-2422) from the
  underlying OHLC artifact — and call `mc.compute_trade_outcome(sym_bars,
  signal_date, stressed=False, trail=False, symbol=symbol)`. Record: kind, exit_bar,
  touch calendar date T = signal_date bar + exit_bar, fire OHLC, fire_tp_level,
  fire_sl_level, vol, premium_pct, entry (adjusted close), cal_held. None-returns
  recorded with reason (signal_date missing from bars / vol None / window too short).
- Determinism: the declaration walk is deterministic (no RNG) — no seeds involved.

## 3. Event + join definitions (P2)

- **Primary event:** declaration kind == 'tp' (clean first-touch TP). kind=='both'
  (TP and SL same bar) tabulated separately, excluded from primary rates. 'sl',
  'hard', 'prem' are non-events (counted in the declaration census).
- **Join:** event (symbol, signal_date) -> ledger contract; touch date T -> the
  contract's path row dated T.
- **Barrier (primary, contract-multiple space — immune to the adjusted-close trap):**
  `B = (1+TP) × entry_premium_real`, entry_premium_real = the ledger's real contract
  entry premium (contract close on signal_date; FF-1 convention).
- **Classification on the touch date T:**
  - FILL (high-basis): path row exists at T and `high >= B`. A resting limit sell at
    B fills when the tape prints at/above B (queue-position caveat documented, not
    modeled).
  - MISS / traded-below: path row exists, `high < B`.
  - MISS / no-print: NO path row at T (day_aggs carry traded days only — no prints,
    nothing to fill against; never forward-fill). Reported inside the miss rate AND
    as its own share.
  - UNJOINABLE (coverage, excluded from rates, fully counted): signal not in kept
    ledger; contract expired before T; path ended before T (`path_end_reason`);
    T beyond the ledger walk window.
- **Close-basis secondary:** `close >= B` on T (the close-confirm framing from the
  tpsl_refine audit).
- **Timing secondary:** first date in [T, deadline] with `high >= B`, where deadline
  = signal_date + 27 calendar days (engine hard-sell). Yields: fills-same-day,
  fills-late (days-late distribution), NEVER-FILL (no such date; includes
  path-goes-silent). Economic note, registered now: a late fill at a resting limit
  exits at the SAME premium B — the true economic loss event is NEVER-FILL, not
  same-day miss.
- **Gap-open decomposition (on fills):** gap-open fill if `open >= B` at T (limit
  filled at the opening print ~ open, better than B). Record open_mult, high_mult,
  close_mult (each /entry_premium_real).
- **Model-anchored secondary (diagnostic only):** model entry premium in dollars =
  `premium_pct × spot_unadj(signal_date)` — NEVER the adjusted close (traps.md
  2026-07-25). Compare real prints vs `(1+TP) ×` that. Reported, never the
  calibration basis (it convolves entry-premium error with path fidelity).

## 4. Metrics + cells (all fixed now)

Slices: {all-kept, DTE 25-38 matched-filter} × {pooled, tier t1..t5, year
2022p..2026p} × {ARM-30, ARM-15}. Matched-filter × pooled-and-tier is the HEADLINE
table (FF-1 precedent). Per cell:

- N declared / N joinable / coverage %; fill rate (high-basis) with Wilson 95% CI;
  miss rate + no-print share; close-basis fill rate; both-rate (census).
- Shortfall on traded-below misses: quantiles p25/p50/p75/p90 of `B_mult − high_mult`
  (premium-multiple points, e.g. 1.30 − 1.22 = 0.08 = 8pp of entry premium).
- Overshoot on fills: p25/p50/p75/p90 of `high_mult − B_mult` (the raw material the
  default Uniform(barrier, high) arm credits); gap-open share γ and
  `open_mult − B_mult` quantiles on gap-open fills.
- Never-fill rate within deadline + days-late distribution for late fills.
- Cells with N < 30: suppress quantiles, print N only. Dose accounting at every
  join step (FF house style) in `out/dose_accounting.md`.

## 5. Knob-mapping rules (pre-committed — the calibration is mechanical)

- **TP_FILL_MISS_P (economic calibration):** = pooled NEVER-FILL rate on the
  matched-filter slice (signal-weighted; cascade is liquidity-blind so no reweight),
  reported per-tier alongside. Registered rationale: the knob's miss branch forces an
  uncontrolled same-day exit — economically that approximates the never-fill path
  (limit never achieves B; position resolves adversely), while a same-day miss that
  fills later still exits AT B.
- **TP_FILL_MISS_P (pessimism bound):** = pooled same-day miss rate — the upper
  bound, for stress probes. Both values land in the table; the tpsl_refine Phase D
  probe (locked at 0.10) is GRADED against the bound: measured bound > 0.10 ->
  flag "probe understates" to the orchestrator (their prereg may only tighten).
- **Fill-price arm:** recommend `TP_FILL_GAP_AWARE` semantics if measured gap-open
  share γ >= 3% of fills (real limit mechanics include opening-print improvement),
  else `TP_FILL_AT_BARRIER` (simpler, same answer when gaps are negligible). The
  default Uniform(barrier, high) overshoot credit is quantified as
  `E[(high_mult − B_mult)/2 | fill]` vs the real limit-mechanics credit
  `γ × E[open_mult − B_mult | gap-open fill]`; the ratio is the headline
  "overshoot-credit optimism" number.
- Rounding: recommended knob values to the nearest 0.05 (probe granularity), exact
  measured values always printed beside them.

## 6. Tripwires (checked before results are read as findings)

1. Coverage: joinable >= 70% of declared 'tp' events per arm (else investigate the
   join before reading rates).
2. Link-1 band sanity: ARM-30 matched-filter+liquid real high-mult median on touch
   days within [1.20, 1.50] (loose — high-based touch days are earlier than FF-1's
   close-only walk, so exact 1.352 reproduction is NOT expected; a value far outside
   the band means a join/units bug, not a finding).
3. Arm divergence: ARM-15 declares strictly more TP events than ARM-30 (silent
   set_tpsl no-op guard, the LESSONS.md trap).
4. Smoke worked example: one event printed end-to-end (symbol, signal_date, vol,
   tp_level, touch date, contract ticker, entry premium, B, that day's real OHLC,
   classification) and hand-audited by the orchestrator before the full run is read.
5. N floor: matched-filter pooled events >= 500 per arm expected (~35-50% tp-rate on
   ~3,000 matched contracts). If < 500, the per-tier table is reported as
   UNDERPOWERED and no per-tier calibration is claimed (pooled only).

## 7. Failure conditions (what counts as "this measurement failed")

- Coverage tripwire unfixable (ledger/day-alignment structurally broken) -> park,
  document in LESSONS.md, no calibration claimed.
- The knob semantics cannot represent the measured reality (e.g. misses dominated by
  path-silence where the engine holds a position the real market never priced) ->
  report the mismatch as the finding; recommend engine-change candidate instead of a
  knob value.
- Everything else — including "fills are near-perfect, knobs stay 0" — is a valid,
  bankable outcome (it certifies the intrabar assumption absolutely, which is worth
  as much as an optimism number).

## 8. Multiplicity

Purely descriptive; fixed cell set declared in §4 before any outcome; no
data-dependent slice selection. Any post-hoc exploratory cut goes into FINDINGS.md
under an explicit "EXPLORATORY (not prereg'd)" header.

## 9. AMENDMENT A1 — 2026-08-10, PRE-OUTCOME (coverage only; adjudicated by the
## orchestrator on the builder's schema report, before any declaration or fill
## number existed — the schema gate stopped P1 before real data was touched)

Discovery: NEITHER §1 input carries an OPEN price. (a) The cached underlying OHLC
parquet is {symbol,date,close,high,low} — its builder (ff_signals.py) never selected
PriceHistory.open; §1's own fallback contingency fired (bulk pull = queue task #342).
(b) Ledger-v2 `paths\` lack the CONTRACT's open (build_ledger_v2.py PATH_COLUMNS) —
which would silently gut §3's gap-open decomposition and half of §5's fill-price arm.

Resolution (all pre-outcome, labeled):

- **Underlying open:** supplied by the §1 fallback pull (task #342). No spec change —
  the written contingency, executed.
- **Contract open:** ADD `B:\polygon_derived\contract_day_index\` (FF-0 artifact;
  per-contract-day OHLCV parsed from the same day_aggs; hive-partitioned for
  observation-date joins) as the SOLE source of the contract `open` column, joined on
  (contract ticker, touch date T). Ledger paths remain primary for entry premium /
  high / close; the index supplies ONLY `open`. Integrity cross-check required: on
  joined rows, index high/close must equal paths high/close; mismatch share goes to
  dose accounting, >1% = STOP and investigate before results are read. The T6
  non-RTH footer covers the index's opens identically (same day_aggs provenance).
- **Spot-audit scope shift:** the §1 5-symbol × 3-date MySQL value audit runs against
  the NEW fallback parquet (it guards the pull code — the specific failure mode is
  grabbing `close_unadj` instead of the engine's adjusted `close`), once, foreground,
  verdict cached to driver/state/ohlc_gate.json; the queued --full job stays DB-free.
- Classification-order fix (expiry checked before walk_end so "expired before T" is
  reachable; caught by builder selftest) is recorded as implementation correctness,
  not a spec change.

Clarifications adjudicated same-time (no semantics changed): §5 same-day miss rate =
the §3 primary classification miss rate at T; printed both_rate =
both_n/(n_tp+both_n) per slice with the FULL kind census (tp/sl/hard/both/prem/
None-reasons) required in dose_accounting.md per arm; "2022p..2026p" = calendar
years 2022-2026 with 2022/2026 flagged PARTIAL in a footnote.
