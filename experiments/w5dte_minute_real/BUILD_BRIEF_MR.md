# BUILD_BRIEF_MR -- w5dte_minute_real driver (builder agent)

Implement PREREG.md (same dir, binding -- read first). Repo root C:\Development\Trader.
py -3.11 locally; runnable via explicit
C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe. ASCII stdout.

## Deliverables

1. `minute_real.py` with `--smoke` (2 named weeks, see below) and `--full`; outputs to
   `B:\polygon_derived\weekly_5dte_movers\minute_real\` (events parquet + per-arm
   summary parquet) and machine-written `RESULTS.md` here (ASCII tables, no prose
   interpretation).
2. `MR_BUILD_REPORT.md` -- schema introspection, decisions, smoke output, open questions.
Do NOT run `--full` (orchestrator queues it). No MySQL anywhere.

## Mechanics (pins)

- REUSE the EV engine verbatim: `sys.path` the repo root, then
  `from experiments.w5dte_ev.ev_study import load_population, add_rule_masks,
  add_pricing, prepare_control_base, draw_one, SEED_BASE, TP_LEVELS, ...` (import what
  exists -- read ev_study.py's constants section first). Events:
  FAMILY & `_tp_fill_5` (plus carry `_tp_fill_10`); CONTROL draws 0/1/2 via the same
  prepare_control_base/draw_one calls (FAMILY arm), each filtered to `_tp_fill_5`.
  Assert: FAMILY TP5-fill event count equals fill_rate*n from the EV RESULTS.md Table A
  (0.192363 * 58057 -> 11,168 +/- 1).
- Level per event: P_L = L * entry_close for L in {5, 10} (only where the daily fill
  flag is true for that L).
- Needed sessions per event = sessions strictly after entry_date through expiry_day
  (reuse the archive session list via ff_common.list_session_dates('day_aggs_v1'),
  which equals the minute tier's sessions; if a minute file is missing for a needed
  session, count it in `missing_minute_files` and treat that session as contributing
  zero -- do NOT crash).
- Streaming pass: build dict session -> set of tickers needed (union over arms/events).
  For each needed session (ascending): `ff_common.read_flatfile('minute_aggs_v1', d)`
  -- introspect the schema ONCE first (expect ticker/volume/open/close/high/low/
  window_start/transactions; record exact dtypes in MR_BUILD_REPORT) -- filter to the
  session's ticker set, then for each (event, session) compute, per L:
  mins_at_above (count of bars high >= P_L), vol_at_above (sum volume in those bars),
  max_run (longest consecutive-minute run, from window_start diffs == 60e9 ns),
  first_touch_minute (min window_start of touching bars -> ET time-of-day string).
  Accumulate into a per-(arm, event, L) frame; vectorize with polars joins (event
  table joined to bars on ticker within the session), NOT python per-bar loops. A
  python loop over SESSIONS is fine (~1,000 iterations).
- Reduce: per (arm, event, L): sum mins/vol across sessions, max of max_run (runs do
  not span sessions), min first_touch (report its session + ET minute), n_touch_days,
  r0_ok = mins_at_above >= 1. Tiers R1/R2/R3 per PREREG. Then per (arm, L):
  validity rates per tier (denominator = r0_ok events; r0 failures reported
  separately), and the gated EV re-read: recompute policy EV per the EV engine's own
  pricing columns -- event kept as TP fill if tier passes, else its return becomes the
  event's `_r_EXPIRY` (already on the population frame). Produce RESULTS.md tables:
  A validity rates by arm x L x tier (+ per-year, per-rule for FAMILY); B gated EV
  table (arm x L x {ungated, R1, R2, R3}) incl. the PREREG SURVIVES line evaluated
  verbatim; C first-touch time-of-day histogram (30-min buckets) + lone-print share;
  D data-quality (r0 failures, missing files).
- Memory: never materialize a full minute file join across all sessions at once;
  per-session frames only. Target full-run wall-clock < 45 min at 12 threads
  (polars reads gz in parallel internally; a modest ThreadPool over sessions is
  allowed if needed -- measure on smoke and report the estimate).

## Smoke (--smoke, hard-asserts)

Weeks (by expiry): 2024-05-24 (NVDA earnings week) and 2023-07-28 (quiet control) --
events restricted to those two weeks, full pipeline. Checks:
1. Event-count reconciliation vs the EV masks for those weeks (recompute directly).
2. R0 >= 95% on smoke events (daily high implies a touching minute bar; report misses).
3. Hand-verification: for ONE known NVDA fill event, print its minute bars at/above
   P_5 (first 5 rows) and confirm mins/vol aggregates by manual sum in the report.
4. Monotonicity: validity_R1 >= validity_R2 >= validity_R3 per arm.
5. Control draws reproduce: draw_one(draw_idx=0) row count matches the EV study's
   control-draw parquet (`B:\polygon_derived\weekly_5dte_movers\ev\tables\` raw
   control draws file, arm==FAMILY, draw_idx==0, n_selected) exactly.
6. Holdout assert on the event frame.

## Traps (forwarded)

ASCII stdout; PYTHONIOENCODING=utf-8. polars NaN != null; fill_nan -> null once;
no .rank() on NaN. window_start is int64 epoch NANOSECONDS (UTC instant -- convert to
ET only for the time-of-day histogram; use a fixed -4h offset and NOTE the DST
imprecision for winter sessions rather than pulling tz libraries, OR use zoneinfo if
available -- report which). Year dirs 2014-2021 are EMPTY decoys -- sessions only via
list_session_dates. Never `py -3.11` in any queued command (orchestrator handles
queueing). Do not modify anything outside experiments/w5dte_minute_real/ and the
minute_real output dir. Timebox ~75 min; blockers > 15 min -> OPEN QUESTIONS +
conservative fallback.

Final message: files, smoke check results, schema found, full-run runtime estimate,
open questions.
