# BUILD_BRIEF_EV -- w5dte_ev pricing engine (builder agent)

You implement PREREG.md (same dir -- read it FIRST; it is binding). OWNER_SPEC.md gives
the owner context. Repo root C:\Development\Trader. Local runs: `py -3.11`; the script
must also run via the explicit
`C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe` (no py-launcher
assumptions inside). ASCII-only stdout. Seed base 20260818.

## Deliverables

1. `experiments/w5dte_ev/ev_study.py`:
   - `--selftest`: runs the self-test battery on a SMALL slice (see below), exit 0 green.
   - `--full`: full run -> writes `B:\polygon_derived\weekly_5dte_movers\ev\`
     (per-arm-policy parquet + control-draw parquet) and machine-written
     `experiments/w5dte_ev/RESULTS.md` (ASCII tables, no interpretation).
2. `experiments/w5dte_ev/EV_BUILD_REPORT.md`: decisions, self-test output, open questions.

Do NOT run `--full` (orchestrator queues it). Do NOT read MySQL at all. Only the
analysis parquets named in PREREG.

## Implementation pins (PREREG is authority; these are the mechanics)

- Load analysis_*.parquet (exclude `_smoke` dir), select only needed columns:
  ticker, underlying, cp, sector, is_monthly_opex, entry_date, entry_dow, expiry_day,
  expiry (or expiry_year), week_monday, entry_close, entry_volume, entry_transactions,
  entry_dollar_vol, adjusted, covered, no_later_print, max_future_high, close_at_expiry,
  otm_pct, moneyness_pct, hl_range_pct. fill_nan->null once. Population filter per
  PREREG. assert_no_holdout_leak (rename entry_date->date for the assert).
- Rule masks R1..R6 EXACTLY as PREREG pins them (string equality on sector/cp; nulls in
  any conjunct column => row fails that conjunct, never raises). FAMILY = any; FAMILY_C.
- Pricing (vectorized, per policy):
  tp_fill_L = max_future_high >= L * entry_close
  proceeds_tp = L * entry_close                     (fills; cost-free)
  settle = close_at_expiry.fill_null(0.0)
  haircut = max(0.0142 * settle, 0.025)
  proceeds_expiry = if settle > 0.025 then max(settle - haircut, 0.0) else 0.0
  policy TP-L: proceeds = tp_fill_L ? proceeds_tp : proceeds_expiry
  policy EXPIRY: proceeds = proceeds_expiry
  r = proceeds / entry_close - 1
- Control: cell key (week_monday, cp, entry_dow). For each arm (FAMILY, FAMILY_C,
  R1..R6): per cell, need k = arm rows in cell; sample k without replacement from cell
  rows NOT in the arm (if complement < k, take all; accumulate shortfall count).
  100 draws, rng = numpy default_rng(20260818 + draw_idx). Efficiency hint: assign each
  population row a per-draw random key with polars and take the k smallest per cell
  (avoids python loops per cell); any correct vectorized approach is fine, but 100
  draws x 8 arms must finish in minutes, not hours -- if needed, run controls only for
  FAMILY and FAMILY_C at 100 draws and R1..R6 at 25 draws, and SAY SO in the report
  (PREREG's gate only needs FAMILY at 100).
- Outputs (RESULTS.md tables):
  A. Arm x policy EV table: n, mean r (equal-weight), premium-weighted mean r, win
     rate (r>0), median r, TP fill rate.
  B. FAMILY per-expiry-year x policy EV.
  C. Control summary per arm x policy: control mean/median/p5/p95 EV, rule EV, rule
     percentile vs the 100 draws, beats_k_of_100.
  D. Adjudication line: PRIMARY (FAMILY, TP-5x): EV, beats k/100, PASS/FAIL per
     PREREG gate (print the gate verbatim).
  E. Capacity: FAMILY hit entry_dollar_vol quantiles (p10/p50/p90), share < $25k,
     median entry premium; expiry-settle null share.

## Self-tests (--selftest, hard-assert)

1. Rule fidelity: recomputed (n, winner_rate at 5x -- winner = max_future_high >=
   5*entry_close) for R1..R6 match parent RESULTS_TABLES.md E3 EXACTLY:
   R1 (25663, 0.207419), R2 (25835, 0.200542), R3 (25835, 0.200542),
   R4 (33438, 0.200072), R5 (44342, 0.193834), R6 (44342, 0.193834). Tolerance:
   n exact; rate abs diff < 5e-6.
2. Pricing on synthetic rows (hand-computed expected r): a TP-5x fill; a no-fill with
   settle 0.50; a no-fill with settle 0.02 (expires, proceeds 0); a null settle.
3. TP monotonicity on the real population: fill_rate(2x) > fill_rate(3x) >
   fill_rate(5x) > fill_rate(10x).
4. Control integrity on 3 draws: per-cell counts equal the arm's per-cell counts
   (excluding recorded shortfall cells); zero overlap with arm rows; two different
   draw seeds produce different row sets.
5. Holdout assert ran.
Print [PASS]/[FAIL] per check; exit non-zero on any FAIL.

## Traps (forwarded)

- ASCII stdout; PYTHONIOENCODING=utf-8 defensively.
- polars NaN != null (fill_nan->null once); never .rank() with NaN; no python per-row
  loops in pricing (vectorized only).
- The queue mangles `py -3.11` -- irrelevant to you (you never queue), but the script
  must not shell out to `py` for anything.
- Do not modify anything outside experiments/w5dte_ev/ and the B:\...\ev\ output dir.
- Timebox ~45 min. Blockers > 15 min -> EV_BUILD_REPORT.md OPEN QUESTIONS +
  conservative fallback.

Final message: files written, self-test results, runtime estimate for --full, open
questions. Orchestrator audits before the full run.
