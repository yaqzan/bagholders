# MR_BUILD_REPORT -- w5dte_minute_real (minute_real.py)

Builder: Sonnet, from BUILD_BRIEF_MR.md (binding: PREREG.md). Date: 2026-08-18.
Deliverables: `minute_real.py` (--smoke / --full), this report. Both committed under
`experiments/w5dte_minute_real/`; no files touched outside that dir and (write-side)
`B:\polygon_derived\weekly_5dte_movers\minute_real\`. `--full` was NEVER invoked (hard
rule) -- only `--smoke`, run repeatedly.

## Files written

- `experiments/w5dte_minute_real/minute_real.py` -- the driver (load/mask/price reuse,
  arm+control-draw construction, session/ticker index, minute-tape streaming pass,
  tier/gated-EV/histogram/data-quality tables, smoke checks, CLI).
- `experiments/w5dte_minute_real/MR_BUILD_REPORT.md` -- this file.
- `experiments/w5dte_minute_real/RESULTS_SMOKE.md` -- machine-written smoke output
  (6/6 checks PASS). NOT `RESULTS.md` -- see decision #5.
- `B:\polygon_derived\weekly_5dte_movers\minute_real\_smoke\*.parquet/.md` -- 9 tables
  (events_metrics, arm_summary, A2, A3, B, C1, C2, D, E). `--full` will write the
  unsuffixed sibling paths (`minute_real\` + `RESULTS.md`), not yet created.

Nothing else changed. `git status` after the run shows only `experiments/w5dte_minute_real/`
as new (verified) and no modifications elsewhere in the repo.

## Minute schema found (introspected before writing the reader, per the hard rule)

Read `B:\polygon_flatfiles\us_options_opra\minute_aggs_v1\2024\05\2024-05-20.csv.gz`
directly (raw gzip text header + `ff_common.read_flatfile`) before writing any join
logic:

```
ticker,volume,open,close,high,low,window_start,transactions
O:A240621C00140000,1,15.81,15.81,15.81,15.81,1716215280000000000,1
```

Polars-inferred dtypes via `read_flatfile('minute_aggs_v1', ...)`:
`ticker=String, volume=Int64, open/close/high/low=Float64, window_start=Int64,
transactions=Int64` -- **identical** to `ff_common._AGGS_SCHEMA` (day_aggs_v1 and
minute_aggs_v1 do share a schema, confirming the module docstring). No nulls in the
sampled file (1,858,132 rows). `window_start` confirmed as UTC-instant nanosecond
epoch: min/max of that session decoded to `2024-05-20 13:30:00+00:00` .. `20:28:00+00:00`
= 09:30-16:28 ET. Consecutive same-ticker bars step by exactly `60_000_000_000` ns
(verified on the busiest ticker that day, 389/389 diffs); this is the constant used
for run-detection (`MINUTE_NS` in the script). Conclusion: `read_flatfile` needed no
special-casing for this tier -- used exactly as `ff_common` already provides it,
narrowed via `columns=["ticker","volume","high","window_start"]` (open/close/low/
transactions are unused by this study).

## Decisions taken

**1. NVDA itself is not in the FAMILY+TP5-fill event population for the 2024-05-24
smoke week -- substituted a DELL contract from the same week for the hand-check.**
PREREG/BUILD_BRIEF's smoke week is labeled "NVDA earnings week" (a volatility-driver
label, not a promise that an NVDA contract is among the rule's hits). Checked directly:
NVDA has exactly one `_family=True` row that week (`O:NVDA240524C01290000`, entry_close
0.35 -> max_future_high 0.44, i.e. it never fills TP5) and zero `_family & _tp_fill_5`
rows. The week's 22 FAMILY/TP5-fill events are ADI/BOIL/DELL/OKLO/ON/QCOM/TSLA-family
names (NVDA-sector spillover, consistent with the week's premise). Used
`O:DELL240524C00152500` (entry 2024-05-20, entry_close=1.55, max_future_high=13.9,
`_tp_fill_10=False`) for smoke check 3 -- confirmed present, unique, and a good exercise
of the multi-session accumulation logic (4 sessions scanned, 49 touching minutes across
2 of them). Not a 15-min blocker, just a confident substitution with the reasoning
inline in the script's constant comment and here.

**2. The same OPRA ticker can legitimately be TWO different events (compound
`(arm, _row_id)` key used throughout, never bare `ticker`).** Discovered while picking
the DELL hand-check ticker: `O:DELL240524C00155000` appears twice in the population --
entered 2024-05-20 (entry_close 1.04) AND entered 2024-05-21 (entry_close 1.00) -- both
satisfying `_family & _tp_fill_5` for the same week (a W5DTE contract can be a fresh
"entry" on more than one day of its own expiry week). Separately, different CONTROL
draws can independently select the same underlying population row (draws are not
mutually exclusive of each other, only internally duplicate-free). Both facts mean a
bare-ticker join/group-by would silently conflate distinct events. Every join and
group-by in the pipeline (`session_to_rows`, `per_session_level_agg`, `attach_metrics`)
keys on `(arm, _row_id)`; the minute-bar JOIN itself is still `on="ticker"` (bars don't
carry `_row_id`) but fans out correctly to however many distinct `(arm,_row_id)` rows
share that ticker in `today_df`.

**3. Gated-EV denominator = the FULL arm population (matches ev_study Table A's own
scope), not just the measured `_tp_fill_5` subset.** PREREG says "recompute the EV
study's TP-5x and TP-10x policy EVs with fills gated" -- "the EV study's ... policy EV"
was defined over the WHOLE arm (Table A: FAMILY/TP5 n=58,057, mean_r=0.023874), not just
the ~11.2k rows that filled. Only the `_tp_fill_5==True` rows can actually be re-priced
(gated in/out); every other row already has `_r_TP5 == _r_EXPIRY` by construction of
`add_pricing`, so folding a fixed r=4.0 (=5x-1) into the full 58,057-row mean for
tier-passing fills and `_r_EXPIRY` otherwise reproduces the original EV's denominator
exactly when ungated (verified: Table B's "ungated_ev" column always equals the
original `_r_TP{L}` mean, by construction). Using the narrower 11.2k-row population
instead would answer a different, upward-biased question ("EV conditional on already
having filled") rather than "does the rule's [unconditional] EV survive."

**4. For --smoke, the gated-EV BASE population is also restricted to the 2 smoke
weeks (not the full 58,057-row arm).** If the smoke run joined its ~20-40-event
measured subset onto the full unrestricted arm, ~99.9% of rows would have no measured
minute data and the "gated" EV would be statistically indistinguishable from ungated --
a mechanically-passing but meaningless check. Instead, `arm_base` for the smoke Table B
is `pop.filter(_family & expiry_day in SMOKE_EXPIRIES)` (348 rows for FAMILY this run),
self-contained within the 2-week universe. RESULTS_SMOKE.md's title and every table
are labeled "SMOKE MODE ... NOT a real verdict" for exactly this reason -- the smoke
SURVIVES line (FAIL, n=348) is a mechanics check, not a research finding. `--full` uses
the unrestricted arm (58,057-ish rows), matching PREREG's real intent.

**5. `--smoke` never writes `RESULTS.md` -- writes `RESULTS_SMOKE.md` + a `_smoke/`
output subdir instead.** Mirrors the EV study's own `_smoke` parquet-dir convention
(`features/_smoke/`). Rationale: decision #4's tiny-N smoke verdict must never be
mistaken for the real, full-population RESULTS.md the orchestrator will read after the
queued `--full` run. `--full` writes the canonical `RESULTS.md` +
`B:\...\minute_real\` (no suffix).

**6. L=10 validity/tier/histogram/data-quality reporting is scoped to the
sub-population where the DAILY `_tp_fill_10` flag was already True.** Because a
session's daily high is itself `max(that session's minute highs)`, if
`max_future_high < 10*entry_close` (daily `_tp_fill_10=False`), NO minute bar can
possibly have touched the 10x level either -- `mins_at_above_10=0` is tautological for
those rows, not a data-quality miss. Including the full `_tp_fill_5` population
(un-scoped) in L=10's R0/validity denominator would dilute it with thousands of
trivially-inapplicable rows. `_level_population()` applies this scoping once,
consistently, everywhere L=10 is reported. L=5 needs no such scoping since it applies
to 100% of the event population by construction. The minute-tape SCAN itself still
computes both P5 and P10 hit flags for every event in the same pass (BUILD_BRIEF
"measured in the same pass") -- only the reporting denominator differs.

**7. Table B includes an R3-gated column, beyond PREREG's literal "gated on R1 and on
R2."** BUILD_BRIEF's own Table B spec is explicit: "arm x L x {ungated, R1, R2, R3}".
Computed all three; the PREREG SURVIVES verdict still reads only the R2 column, per
PREREG's literal adjudication text. Flagging since it's technically beyond PREREG's
narrower "R1 and R2" wording, though a strict superset (extra information, no
change to what's gated).

**8. Table A's brief scope ("validity rates by arm x L x tier (+ per-year, per-rule
for FAMILY)") is split into three named tables (A1/A2/A3), not one merged table.**
Mirrors ev_study.py's own A/B/C/D/E multi-table convention and keeps each table's grain
homogeneous (arm-level vs. FAMILY-year-level vs. FAMILY-rule-level) rather than a wide
table with mostly-null year/rule columns for non-FAMILY / non-secondary rows. Named
output files: `arm_summary` (=A1, also doubles as the deliverable's required "per-arm
summary parquet"), `A2_validity_by_year`, `A3_validity_by_rule`. Flagging in case a
single flat table was actually wanted.

**9. Reused `df_to_md_table` from ev_study.py directly** rather than re-implementing
the same markdown-table formatter -- a generic formatting helper, not a "mask or fill"
(the reuse pin's actual concern), so importing it is in the spirit of "REUSE the EV
engine verbatim," not a violation of it.

**10. Timezone conversion: `zoneinfo(America/New_York)`, not the brief's fixed -4h
fallback.** Verified directly on this box: `tzdata` is installed and
`ZoneInfo("America/New_York")` resolves both EDT (`2024-05-20 09:30:00-04:00`) and EST
(`2023-12-20 09:30:00-05:00`) correctly. Used in preference to the documented fallback,
which would mis-convert every winter session by an hour. `_TZ_METHOD` is logged and
written into every RESULTS file so this choice is auditable if the box's tzdata is ever
missing in a different environment (the `except Exception` fallback path still exists
and is exercised only if `zoneinfo`/`tzdata` are unavailable).

**11. Session universe: `list_session_dates('day_aggs_v1')`, verified equal to
`list_session_dates('minute_aggs_v1')` at runtime (`1007 == 1007`, logged every run)
rather than assumed from BUILD_BRIEF's claim.** Also verified the population's full
entry_date/expiry_day range (2022-08-01 .. 2026-06-12) sits entirely inside the
archive's coverage (2022-08-01 .. 2026-08-05) -- zero FAMILY/TP5-fill events fall
outside the archive window, so `--full` should see `missing_minute_files == []`
structurally, not just by luck (see "Open questions" for the one caveat: this doesn't
rule out a HOLE inside the covered range, which the loop already handles gracefully
per the pin -- counted, not crashed).

## Smoke output (py -3.11 experiments/w5dte_minute_real/minute_real.py --smoke, exit 0)

Run 3 times (fresh explicit-Python311-path run, a second explicit-path run, and a
`py -3.11` launcher run) -- identical 6/6 PASS every time (fully deterministic: same
`SEED_BASE`, no wall-clock-dependent branching in the pipeline itself).

```
==============================================================================
w5dte_minute_real -- minute_real.py
mode=SMOKE  seed_base=20260818  tz_method=zoneinfo(America/New_York) -- DST-correct
==============================================================================
  fidelity check: FAMILY TP5-fill n=11168 (expected 11168 +/-1) OK
  archive sessions: day_aggs=1007 minute_aggs=1007 equal=True
  arm sizes (full, unrestricted): FAMILY=58057(tp5=11168), CONTROL0=58057(tp5=4847),
    CONTROL1=58057(tp5=4745), CONTROL2=58057(tp5=4785)
  events to process: 99 (FAMILY=39, CONTROL0=18, CONTROL1=19, CONTROL2=23)
  minute-tape scan: 8 distinct sessions needed (sum over events = 346)
  scan complete: 8 sessions, 15166864 raw bar-rows read, 0 missing files, 1.91s
    Hand-verification ticker=O:DELL240524C00152500 entry_date=2024-05-20
    expiry_day=2024-05-24 entry_close=1.55 P5=7.75 sessions_scanned=[2024-05-21,
    2024-05-22, 2024-05-23, 2024-05-24]
      2024-05-23 {volume:48, high:8.23, window_start:1716471000000000000}
      2024-05-23 {volume:4,  high:9.0,  window_start:1716471360000000000}
      2024-05-23 {volume:15, high:10.0, window_start:1716471420000000000}
      2024-05-23 {volume:3,  high:8.3,  window_start:1716471480000000000}
      2024-05-23 {volume:2,  high:8.5,  window_start:1716471540000000000}
    manual: mins=49 vol=331; pipeline: mins=49 vol=331

==============================================================================
SMOKE CHECK BLOCK
==============================================================================
[PASS] 5. Control draws reproduce (draw_idx=0 n_selected) -- reference=58057 got=58057
[PASS] 1. Event-count reconciliation (full-then-filter vs filter-then-mask) -- 39 == 39
[PASS] 6. Holdout assert on event frame -- no exception raised
[PASS] 2. R0 sanity >= 95% on smoke events (L5) -- r0_ok=99/99 (1.0000); misses=[]
[PASS] 3. Hand-verification (independent manual sum vs pipeline) -- mins=49 vol=331 both
[PASS] 4. Monotonicity validity_R1 >= R2 >= R3 per arm/L -- 8/8 (arm,L) pairs OK
  e.g. FAMILY/L5: R1=1.0000 R2=0.8718 R3=0.7436; FAMILY/L10: R1=1.0000 R2=0.8095 R3=0.5238

6/6 smoke checks passed. elapsed=2.7s-5.0s (cache-dependent)
```

Per-check detail, all PASS:

| # | Check | Result |
|---|-------|--------|
| 1 | Event-count reconciliation (full-then-filter vs filter-then-mask) | 39 == 39 |
| 2 | R0 sanity >= 95% (L5, pooled all smoke events) | 99/99 = 100.00% |
| 3 | Hand-verification (independent from-scratch recompute vs pipeline) | mins 49==49, vol 331==331 |
| 4 | Monotonicity validity_R1 >= R2 >= R3 | 8/8 (arm,L) pairs hold (mathematically guaranteed by tier nesting; also empirically confirmed) |
| 5 | Control-draw reproduction (draw_idx=0 vs EV study's stored parquet) | n_selected 58057 == 58057 |
| 6 | Holdout assert on the event frame | no exception (max entry_date well before cutoff) |

Internal consistency spot-check (not one of the 6, but confirms Table A1 <-> Table B
agree): every (arm, L) with `validity_R1==1.0` in Table A1 shows `R1_ev == ungated_ev`
in Table B (R1-gating provably changed nothing when every measured event already
passes R1) -- true for all 8 rows this run.

## --full runtime estimate

Measured on smoke (8 sessions, all from 2 real trading weeks -- not synthetic):
15,166,864 raw bar-rows read, scan wall-clock 1.91s => ~0.24s/session (dominated by
the gzip CSV read; the ticker-filtered join+aggregate is comparatively free since only
a handful of tickers match per session).

Computed the FULL run's session/event footprint as a **separate, read-only diagnostic
that touches zero minute files** (pure date-range bisect math over the already-loaded,
already-priced population -- seconds of CPU, no archive I/O, not a violation of the
"--full never run directly" rule):
- Total events (FAMILY 11,168 + CONTROL0/1/2 4847/4745/4785): **25,545**.
- Distinct sessions needed: **766 of 1007** archive sessions (76%).
- Sum of needed-sessions over events: 88,037 (avg ~3.45 sessions/event -- consistent
  with a ~1-week resting-limit window).
- Rows-needed-per-session: min 1, median 70, mean 115, max 1960.

**Estimated --full wall-clock: 766 sessions x ~0.24s/session ~= 184s (~3.1 min) for the
scan**, plus ~10-20s setup (load+mask+price the 1.34M-row population, 3 real control
draws instead of smoke's implicit pass-through) and a few seconds to write 9
tables+RESULTS.md. **Total estimate: ~3.5-5 minutes** -- roughly 9-12x under
BUILD_BRIEF's 45-minute ceiling. No ThreadPool needed; the sequential per-session loop
already has generous headroom. Queue with `--db light --cpu 12` per PREREG (pure B:
reads, no MySQL), using the explicit Python311 path (never `py -3.11` in the queue
command, per the forwarded trap).

## Open questions

1. **Decision #4 (smoke gated-EV base = smoke-week-restricted, not the full arm)** --
   confirm this is the intended smoke behavior, versus an alternative where the smoke
   Table B is skipped/labeled purely illustrative, or the full 58,057-row arm is used
   as-is (which would make smoke's "gated" EV indistinguishable from ungated for ~99.9%
   of rows -- seemed clearly worse, but flagging the alternative).
2. **Decision #7 (R3 column in Table B)** -- PREREG's literal adjudication text says
   "gated on R1 and on R2"; BUILD_BRIEF's Table B spec lists all of
   `{ungated, R1, R2, R3}`. Implemented the fuller BUILD_BRIEF spec; the PREREG
   SURVIVES verdict itself still reads only R2, so this is additive, not a deviation
   in what gates the verdict.
3. **Decision #8 (Table A split into A1/A2/A3)** -- confirm three tables (vs. one
   wide table with nullable year/rule columns) matches intent; trivial to merge if not.
4. **Missing-file handling is implemented and defensive (counted in Table D, loop
   continues) but never actually exercised** -- the smoke run's 8 sessions all had
   files present, and the full-population date-range check (decision #11) suggests
   `--full` structurally shouldn't hit any either. If `--full` DOES report a nonzero
   `missing_minute_files`, that would indicate a genuine hole inside the archive's
   nominally-covered range worth a second look (not a script bug).
5. Table C1's 30-minute bucketing and the open-auction/lone-print stats (Table C2) are
   PREREG "secondary reads" with no pinned exact definition beyond "open-auction spike
   share" and "lone-print share (mins_at_above==1)" -- implemented open-auction as
   first-touch strictly before 10:00 ET (the first 30-min bucket after the 9:30 open);
   flagging the exact boundary choice in case a different one was intended.
