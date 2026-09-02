# TAPE_BUILD_REPORT -- W5DTE forward paper tape

Date: 2026-08-18. Built per `experiments/w5dte_ev/OWNER_SPEC.md` lock #3 (conditional-YES,
triggered by the PASS in `experiments/w5dte_ev/FINDINGS.md`) and that FINDINGS.md's
"Disposition" paragraph (PROXY-FIDELITY design). Pattern precedent (not code):
`.horizon/ct15-paper-sleeve/`. Cold-boot doc: `.horizon/w5dte-paper-tape/TASK.md`.

## Deliverables

| File | Status |
| --- | --- |
| `experiments/w5dte_tape/calibrate_proxy.py` | built, run twice (see below) |
| `experiments/w5dte_tape/calibration_stats.json` | generated artifact |
| `experiments/w5dte_tape/FIDELITY.md` | generated artifact |
| `experiments/w5dte_tape/w5dte_tape.py` | built, verified (dry-run + sandboxed real writes) |
| `experiments/w5dte_tape/install_tape_task.ps1` | built, statically parsed clean, **NOT run** |
| `.horizon/w5dte-paper-tape/TASK.md` | written |
| `.horizon/w5dte-paper-tape/state.json` | written (fresh, `done: []`, `phase: "idle"`) |
| `.horizon/w5dte-paper-tape/logs/` | created, empty (populates on first real run) |
| `experiments/w5dte_tape/TAPE_BUILD_REPORT.md` | this file |

`git status` confirms only these two directories changed; nothing else in the repo was
touched.

## 1. Calibration results

Ran `calibrate_proxy.py` directly (read-only over `B:\polygon_derived\contract_day_index\
_bydate\`, no MySQL, not queued, per instruction). A 6-file throughput probe first measured
~10 files/sec, which meant the FULL Mon/Tue archive population fit comfortably under the
~10 min budget with no subsampling needed (`--stride 1`, the default) -- 382 Mon/Tue
sessions, 971 in-scope sessions total (<= 2026-06-12, the w5dte_ev holdout cutoff,
`experiments._holdout` asserted). Two runs: 54.5s cold, 8.0s warm (OS file-cache), both
producing bit-identical numbers. N = 10,025,997 calibration rows (contract present on both
a Mon/Tue session and its true immediately-prior trading session, close>=$0.20,
volume>=100).

| slice | N | true_rate (hl>=127.3) | X | proxy_rate@X | agreement | precision/recall | rho |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **calls only (LIVE VALUE)** | 5,870,587 | 5.159% | **1.000000** | 5.245% | 90.28% | ~6.5% | 0.562 |
| pooled (reference) | 10,025,997 | 5.719% | 0.887500 | 5.719% | 89.88% | ~11.5% | 0.579 |
| puts only (reference) | 4,155,410 | 6.509% | 0.790625 | 6.509% | 89.94% | ~22.7% | 0.603 |

**Decision: `recommended_X` = the calls-only value (X=1.0), not pooled.** The live
`--entry` rule only ever evaluates calls, so the threshold should be calibrated on exactly
the population it's applied to. This was a deliberate correction made mid-build (the first
draft defaulted to pooled; N=5.87M for calls-only is still enormous, so there's no
statistical-power cost to narrowing it). `w5dte_tape.py` reads this value at import time
from `calibration_stats.json` rather than a second hardcoded copy of the constant.

**Honest read of the numbers:** rho~0.56-0.60 shows the close-to-close proxy IS
directionally informative (bigger proxy move does correlate with bigger true intraday
range), but precision/recall of ~6.5% (calls) means a HARD threshold gate is a noisy, not
crisp, stand-in for the true conjunct -- most contracts that clear the proxy gate would not
have cleared the true `hl_range_pct>=127.3` gate that day, and vice versa. The raw
agreement-rate number (90.3%) is misleadingly close to the trivial "always predict miss"
baseline (94.8%) given how imbalanced the true-hit rate is (~5%) -- FIDELITY.md's
"Interpretation" sections spell this out explicitly so nobody reads agreement-rate alone as
"the proxy works great." Full numbers, methodology, and standing caveats (outcome proxy =
lower bound, volume unreliable live, archive dead 2026-08-05): `FIDELITY.md`.

## 2. Key design decisions

1. **Calls-only, technology-only, single merged proxy rule** -- not a literal replication
   of R1/R2/R3/R4 as 4 separate rules. The task's own framing ("Evaluate the PROXY rule
   family") and OWNER_SPEC's "PROXY-FIDELITY design" license this: `moneyness_pct>=
   0.03958416633346662` (R1/R2/R4's full-precision threshold, PREREG amendment
   2026-08-18a) + tech sector + entry premium>=$0.20 + proxy-violence>=X, calls only.
   `is_monthly_opex` is logged on every row (R1 gates on it, R2-R4 don't) rather than
   applied as a filter, since 3 of 4 rules don't reference it.
2. **Spot resolution with a documented fallback.** Moneyness needs `price_history.
   close_unadj` (AS-TRADED). Discovered mid-build that this is chronically missing for
   fresh rows in live production right now (see section 3) -- worked around with: use
   `close_unadj` when present, else fall back to `close` IF the target date is itself
   fresh (<=5 days old at run time), citing `trader.py:280-293`'s own established
   rationale that the two conventions coincide for a bar this fresh. Every row logs which
   path was used (`spot_source`: `"unadj"` or `"close_fallback_fresh"`). An older
   `--date` with no `close_unadj` is SKIPPED, never silently substituted (that would be
   the "quiet lie" trader.py's own comment warns against).
3. **Dedup / idempotency, two layers.** `state.json`'s `done` list gates re-running a
   (mode, date) entirely (like ct15's date-cursor pattern); within a run, `--entry` also
   checks tape.jsonl's existing `(date, option_id)` pairs before appending, so `--force`
   re-running an already-logged date is a safe no-op rather than a duplicate-row bug --
   verified explicitly (Test 3 below).
4. **`--entry` appends, `--outcomes` rewrites.** Per the task's own spec: entry is a pure
   open-append (dedup-checked, no read-modify-write needed); outcomes mutates existing
   "open" rows to "closed" in place and must rewrite the whole file atomically
   (tmp + `os.replace`), since it's flipping status/adding fields on rows already on disk.
5. **`--outcomes` self-heals.** Closes any OPEN row whose `expiry <= target_date`, not
   strictly "this week's" rows only -- so a missed Friday (box asleep, etc.) gets caught up
   automatically on the next run rather than leaving permanently-stuck open rows.
6. **No queue submission.** Both modes are single-day or single-week MySQL chains against
   a ~200-name universe (`SET SESSION MAX_EXECUTION_TIME=120000` on every connection) --
   the CLAUDE.md carve-out for genuinely light foreground checks, not the sweep/recalc
   class of job the queue exists to protect. Confirmed empirically: both dry-runs below
   returned in low single-digit seconds.

## 3. A real production data-quality finding (out of scope to fix here)

While building the spot-resolution logic, found that `price_history.close_unadj` coverage
for FRESH rows has been declining sharply:

| week (YEARWEEK) | total rows | rows with close_unadj |
| --- | --- | --- |
| 2026-07 wk1-3 | ~5,370 | ~5,335 (~99%) |
| 2026 wk30 (late Jul) | 5,260 | 3,223 (~61%) |
| 2026 wk31 (early Aug) | 5,190 | 1,108 (~21%) |
| 2026 wk32 | 5,186 | 1,042 (~20%) |
| 2026 wk33 (this week, partial) | 1,983 | 763 (~38%) |

This is surprising because `trader.py:159` (`_UNADJ_FRESH_DAYS=5`) and its own comment
(lines 280-293) describe a mechanism specifically designed to write `close_unadj` on every
fresh pull ("the two conventions COINCIDE at the moment a bar is first printed"). Something
has been suppressing that write path for most symbols since late July. This tape works
around it (see decision 2 above) rather than fixing it -- fixing a live production
price-pull path is out of scope for a paper-tape build and outside the files this task is
scoped to touch. Flagging via `spawn_task` for a dedicated look, since other live
consumers of `close_unadj` (anything doing moneyness/strike-relative-to-spot work off
fresh rows) would have the same silent degradation.

## 4. A resolved concern: long-dated contracts reaching their final week

Several sampled candidates showed extreme moneyness (strike up to ~2x spot, e.g. an ADBE
$450 call against a ~$254 spot) and, on inspection, multi-month price histories (one
`option_id` had rows back to 2025-09-08) despite expiring "this week." Initially looked
like a bug. It isn't: `experiments/weekly_5dte_movers/build_ledger.py`'s own
`process_week()` filters purely on `pl.col("expiry") == expiry_day` with no minimum
contract age either -- the parent's own R1-R4 population also includes any contract
(monthly, quarterly, LEAP) that simply happens to reach its final week during the target
week. "5-DTE" describes the option's REMAINING life at entry, not how it was originally
listed. `w5dte_tape.py`'s SQL does the same thing (`o.expiration_date = <this week's
expiry>` with no age filter), so this is a faithful match to the parent's own methodology,
not a deviation. Noted here so a future reader doesn't re-discover the same false alarm.
Separately, several of these far-OTM long-lived contracts showed recently-spiked implied
vol (IV column in `option_prices`, e.g. 100-290% on names with no obvious in-story
catalyst) -- IV isn't used anywhere in the entry rule, so this doesn't affect the tape, but
it's an interesting data point left as an open question below.

## 5. Verification

### 5a. Dry-run against live production MySQL (today, 2026-08-18, an entry day)

**`--entry --date 2026-08-17`** (the most recent COMPLETE session -- this week's Monday;
today's own 2026-08-18 option chain has not posted yet, since the options pull runs
post-close and this verification ran mid-morning, ~6+ hours before that job fires):

```
ENTRY 2026-08-17 (week_monday=2026-08-17 expiry_day=2026-08-21 is_monthly_opex=True):
chain_rows=8418 no_spot=0 below_moneyness=6380 after_moneyness=2038 no_prev_snapshot=237
below_proxy=1626 -- 175 candidate(s) (proxy X=1.0000)
```

**175 real candidates.** 3 sample rows:

```
ACMR   O:ACMR260821C00090000    strike=90.00  price=1.65 prev=0.80 proxy_move=1.0625
       moneyness=0.0509 vol=0  oi=679  spot_source=close_fallback_fresh
ACN    O:ACN260821C00345000     strike=345.00 price=0.29 prev=0.04 proxy_move=6.2500
       moneyness=1.0297 vol=0  oi=92   spot_source=close_fallback_fresh
ADBE   O:ADBE260821C00435000    strike=435.00 price=0.25 prev=0.10 proxy_move=1.5000
       moneyness=0.7123 vol=10 oi=413  spot_source=close_fallback_fresh
```

(All 175 rows print with `spot_source=close_fallback_fresh` -- confirms finding #3 above:
`close_unadj` was null for effectively every tech-sector row on this date, so the entry
logic exercised its fallback path on essentially every candidate. Good coverage of that
path; the `unadj` path is exercised too whenever it IS populated, which the sandboxed test
below confirms works when present.)

**`--entry --date 2026-08-18`** (literal today, run before this day's option pull):

```
ENTRY 2026-08-18 (... ): chain_rows=0 no_spot=0 below_moneyness=0 after_moneyness=0
no_prev_snapshot=0 below_proxy=0 -- 0 candidate(s) (proxy X=1.0000)
DRY RUN -- would append 0 row(s) ...
```

Honest, correctly-diagnosed empty state (chain_rows=0 -> no option_prices rows exist yet
for today) rather than a silent/ambiguous zero. This is exactly what the real 17:40
scheduled run will NOT see (by then the day's option pull has completed) -- included here
specifically to prove the zero-data path degrades cleanly rather than erroring.

**`--outcomes --date 2026-08-18`** (real tape is empty -- no `--entry` has ever run
non-dry-run against the real path):

```
OUTCOMES 2026-08-18: 0 total tape row(s), 0 open+expired-by-today
  nothing to close
```

### 5b. Sandboxed round-trip test (real MySQL data, real write paths, throwaway location)

Ran a scratch test (not part of the deliverable -- lived in the scratchpad dir, deleted
after) that imported `w5dte_tape` and redirected `TASK_ROOT`/`STATE_PATH`/`TAPE_PATH`/
`LOG_DIR` to a temp directory, so the real `.horizon/w5dte-paper-tape/` was never touched,
then exercised the REAL (non-dry-run) code paths:

1. `run_entry(2026-08-17, dry_run=False)` -> appended 202 real rows (this was BEFORE the
   calls-only-X correction, hence 202 vs the 175 shown above at X=1.0 -- both runs
   otherwise identical code path). All `status=="open"`.
2. Re-run same date without `--force` -> correctly skipped (state.json `done`-list gate).
3. Re-run same date WITH `--force` -> per-run dedup correctly produced 0 NEW rows (all 202
   already present) -- proves `--force` can't duplicate rows.
4. Synthetic `--outcomes` test using two rows: (a) a REAL option_id (ADBE $450C) with a
   real entry_date/fake-expiry window chosen from its actual price history
   (entry 2026-08-03 @ $0.53, window through 2026-08-17 @ $2.17) -- verified
   `max_price_after_entry=2.17`, `max_price_date=2026-08-17`, `price_at_expiry=2.17`,
   `growth_mult=4.094339622641509` (== 2.17/0.53 exactly), `no_later_print=0`,
   `status="closed"`; (b) a fabricated `option_id=-1` with no real data -- verified
   `no_later_print=1`, `max_price_after_entry=None`, `growth_mult=None`,
   `status="closed"` (still closes -- no data to wait for, doesn't get stuck open).
   All 5 assertions passed.

This exercises every code path the two dry-runs above couldn't (real append, real
idempotency gate, real per-run dedup, real atomic rewrite, both the "found an outcome" and
"no later print" outcome branches) without touching production state.

## 6. Open questions

1. **`close_unadj` coverage regression** (section 3) -- root cause not investigated here
   (out of scope); flagged separately. If it self-heals, `spot_source` will show mostly
   `"unadj"` in future runs; if not, the fallback keeps the tape working but every row
   should keep logging which path was used so this stays auditable over time.
2. **Calibration is necessarily sector/moneyness-unconditioned** (`calibrate_proxy.py` has
   no MySQL access, so no `stocks.sector` join is possible) -- the fidelity numbers in
   section 1 are for the WHOLE archive, not specifically the technology+deep-OTM slice the
   live rule actually touches. Assumed representative, not independently confirmed. A
   future session COULD confirm this by joining a static historical
   symbol-\>sector snapshot onto the archive tickers, but the archive itself is dead
   (no new sessions since 2026-08-05), so this is a "nice to have," not blocking.
3. **Hard-threshold gate vs. a softer/ranked selection.** FIDELITY.md's own numbers show
   precision/recall of only ~6.5% (calls) for the hard `proxy_move>=X` gate despite
   rho~0.56 -- a rank-based selection (e.g., "top-K by proxy_move each week") might track
   the true conjunct's INTENT more faithfully than a fixed threshold, at the cost of not
   matching the task's literal spec ("proxy-violence... >= X"). Not changed here since the
   spec pins a hard threshold; worth the owner's opinion once real tape weeks accumulate
   and the candidate-count volatility (175 vs 202 just from an X of 1.0 vs 0.8875) can be
   judged against real outcomes.
4. **Elevated recent IV on some far-OTM long-lived contracts** (section 4) -- not used by
   the entry rule, so not investigated further; flagged in case it turns out to correlate
   with the `close_unadj` gap (both could share a root cause in the same upstream pull).
5. **Candidate volatility week to week is untested** -- only one real week (2026-08-17/18)
   was available to exercise `--entry` against. 175 candidates on one week is plausible
   given the parent's own 25,663-row R1 count over ~200 weeks (~130/week average across
   the FULL market, vs 175 here from ONE week's tech-only, calls-only proxy population --
   directionally consistent, but N=1 week is not a claim about typical weekly volume).

## 7. Next steps (not done by this build session)

- Orchestrator audits this report + `FIDELITY.md` + the driver source.
- Run `experiments/w5dte_tape/install_tape_task.ps1` to register the two scheduled tasks
  (statically parsed clean; deliberately not run here per the task's own instruction).
- Add a `w5dte-paper-tape` row to `.horizon/INDEX.md` (owner/orchestrator housekeeping).
- First real scheduled `--entry` fires the next Monday or Tuesday after installation;
  first real `--outcomes` the following Friday.
