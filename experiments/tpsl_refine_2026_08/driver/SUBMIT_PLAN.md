# Phase D SUBMIT_PLAN — exact queue commands (PREREG.md section 6)

Builder-authored, orchestrator-run. **Do not run these until Phase C's outcome fixes the
finalist cell lists.** Replace every `<CORE_CELLS>` / `<APEX_CELLS>` placeholder with the
actual finalist list in the Phase D `--cells` format (`tp,sl;tp,sl;...` — comma inside a
cell, semicolon between cells; the baseline `0.30,-0.70` is auto-included by every script
here regardless of whether the placeholder names it, so finalists only need to list the
NON-baseline cells, e.g. `<CORE_CELLS>` → `0.10,-0.60;0.15,-0.90`). PREREG caps finalists at
≤3/profile, so each `<..._CELLS>` placeholder expands to ≤3 `tp,sl` tokens.

**Spec-impossibility flag (STOP-rule, report not improvise):** `phaseD_run.py` and
`phaseD_cascade_parity.py` are FLAT confirmations only (stress=base). If Phase C's winner
is regime-conditional (stress != base — a breadth/MWDD/RXDD/regime-mult band offset), running
it through these two scripts using only its base `(tp,sl)` pair would silently misrepresent
it as flat. That case is NOT handled below — the orchestrator must decide separately whether
to (a) extend `phaseD_run.py`/`mc_patch`-style stress plumbing, (b) reuse Phase C's own
conditional-patch machinery for a Phase-D-scale confirmation, or (c) treat a regime-
conditional winner as ineligible for this flat-confirmation instrument. Do not run the
commands below against a regime-conditional cell's `(tp,sl)` pair and call it a flat
confirmation of that cell.

## Timing basis (and its limits — read before trusting the "under 90min" claims)

Phase B (N=300, 9 windows {2020_crash,2021,2022,2023,2024,2025,dip,22-now,5y}, 30 cells/profile,
270 (window,cell) pairs/profile) measured: core prepare=546.2s sim=1905.4s (2.02s/7.06s
per pair); apex prepare=552.9s sim=1826.9s (2.05s/6.77s per pair). Task guidance: sim time
is **~2x at N=500 vs N=300** (prepare is N-independent). Blended estimate for windows Phase B
already touched: **~2.0s prepare + ~14s sim ≈ 16s per (window,cell) pair at N=500.**

**Real gap: 2018, 2020, and 10y were NEVER run in this campaign (Phase A used
{2022,2024,22-now,2020_crash}; Phase B used the 9 above).** 10y in particular is 2x the
length of the longest window Phase B measured (5y) and has no empirical basis here at all.
The sharding below treats {2018, 2020, 10y} as **unknown-cost, isolated into their own
per-window jobs** so a surprise on one never sinks a whole batch, and the orchestrator can
react (split further) on the first wave without losing other windows' progress. Every job
is independently resumable (atomic per-job state under `driver/state/`), so a re-submit of
the identical command after a timeout/preemption picks up where it left off.

## A) Flat confirmation — `phaseD_run.py` (8 jobs: 4 windows-groups × 2 profiles)

Known-cost group (9 windows Phase B already measured) bundled into one job per profile;
{2018, 2020, 10y} each isolated. `--cpu 6` matches the PREREG section 7 frozen `MC_WORKERS=6`
pin. All `--restartable`, all `db=light` (read-only signal/price loads, `MC_NO_DB_PERSIST=1`).

```bash
# CORE — known-9 (safe, ~36 pairs * 16s ~= 10min best-case, budget generously to 90min anyway)
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_known9 --timeout 90m \
  --reason "Phase D confirm: core flat N=500, 9 windows Phase B already measured" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_known9 --profile core \
    --cells "<CORE_CELLS>" --windows 2020_crash,2021,2022,2023,2024,2025,dip,22-now,5y

# CORE — 2018 (unknown cost, isolated)
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_2018 --timeout 90m \
  --reason "Phase D confirm: core flat N=500, window=2018 (never run before this campaign)" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_2018 --profile core \
    --cells "<CORE_CELLS>" --windows 2018

# CORE — 2020 (unknown cost, isolated)
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_2020 --timeout 90m \
  --reason "Phase D confirm: core flat N=500, window=2020 (never run before this campaign)" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_2020 --profile core \
    --cells "<CORE_CELLS>" --windows 2020

# CORE — 10y (biggest unknown: 2x the longest window Phase B ever measured)
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_10y --timeout 90m \
  --reason "Phase D confirm: core flat N=500, window=10y (2x longest-ever-measured window; watch first)" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_10y --profile core \
    --cells "<CORE_CELLS>" --windows 10y

# APEX — known-9
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_known9 --timeout 90m \
  --reason "Phase D confirm: apex flat N=500, 9 windows Phase B already measured" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_known9 --profile apex \
    --cells "<APEX_CELLS>" --windows 2020_crash,2021,2022,2023,2024,2025,dip,22-now,5y

# APEX — 2018
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_2018 --timeout 90m \
  --reason "Phase D confirm: apex flat N=500, window=2018" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_2018 --profile apex \
    --cells "<APEX_CELLS>" --windows 2018

# APEX — 2020
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_2020 --timeout 90m \
  --reason "Phase D confirm: apex flat N=500, window=2020" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_2020 --profile apex \
    --cells "<APEX_CELLS>" --windows 2020

# APEX — 10y
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_10y --timeout 90m \
  --reason "Phase D confirm: apex flat N=500, window=10y" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_10y --profile apex \
    --cells "<APEX_CELLS>" --windows 10y
```

## B) Fill-probe amendment — `phaseD_run.py --fill-probe` (2 jobs, one per profile)

Fixed to 22-now,5y (PREREG section 6 amendment) — no sharding needed, small (≤4 cells × 2
windows = ≤8 pairs).

```bash
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_fillprobe --timeout 45m \
  --reason "Phase D amendment: core TP_FILL_MISS_P=0.10 probe, 22-now+5y" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_confirm --profile core \
    --cells "<CORE_CELLS>" --fill-probe

trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_fillprobe --timeout 45m \
  --reason "Phase D amendment: apex TP_FILL_MISS_P=0.10 probe, 22-now+5y" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_confirm --profile apex \
    --cells "<APEX_CELLS>" --fill-probe
```

## C) Survivor-only contrast — `phaseD_run.py --survivor-only` (2 jobs, one per profile)

Fixed to 2022,22-now (PREREG section 6) — no sharding needed.

```bash
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_core_survivor --timeout 45m \
  --reason "Phase D evidence: core survivor-only universe contrast, 2022+22-now" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job core_confirm --profile core \
    --cells "<CORE_CELLS>" --survivor-only

trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex_survivor --timeout 45m \
  --reason "Phase D evidence: apex survivor-only universe contrast, 2022+22-now" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_run.py --job apex_confirm --profile apex \
    --cells "<APEX_CELLS>" --survivor-only
```

## D) Apex 2x-race harness — `phaseD_apex2x.py` (4 jobs, Apex ONLY — Core is not a 2x-race tool)

**MUST run sequentially, baseline job first**, then the (up to 3) finalist jobs may run
concurrently with each other. Reason: every job's `--cells` auto-includes the baseline arm
(PREREG: baseline is always an arm), and one arm's ~113-window monthly roll at N=500 takes
long enough (~113 × ~15-30s/subprocess ≈ 30-55min) that bundling all 4 arms into ONE job
risks exceeding 90min (4 arms serially ≈ 2-4h). Running baseline ALONE first means its
per-(arm,window) JSON files already exist on disk (`out/phaseD_apex2x_results/apex_confirm/
tp+0.30_sl-0.70/*.json`) before the finalist jobs start, so each finalist job's OWN
auto-included baseline arm is a FREE skip-if-exists resume (`_mc_pinned_runner.run_one_window`)
— zero wasted recompute, and no race on baseline's output files across concurrently-running
finalist jobs (they only ever write to their OWN, disjoint arm subdirectory).

```bash
# Step 1 — baseline ALONE (omit --cells entirely -- parse_cells_arg gives just [0.30,-0.70]).
# WAIT for this to complete before submitting step 2.
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex2x_baseline --timeout 90m \
  --reason "Phase D Apex 2x-race: baseline arm ALONE first (so finalist jobs resume it for free)" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_apex2x.py --job apex_confirm

# Step 2 — one job PER finalist (repeat this block for each of the <=3 finalist cells;
# each still auto-includes baseline, but it resumes for free per the note above).
trader queue submit --priority high --db light --cpu 6 --restartable \
  --dedup tpsl_phaseD_apex2x_<TAG> --timeout 90m \
  --reason "Phase D Apex 2x-race: finalist <TP>,<SL>" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_apex2x.py --job apex_confirm \
    --cells "<TP>,<SL>"
```

(`<TAG>` = a filesystem-safe label for the dedup key, e.g. `tp010_slm60`; `<TP>,<SL>` = one
finalist cell, e.g. `0.10,-0.60`. If a finalist job's own wall time still threatens 90min
once running (watch its `[ARM DONE]`/per-window progress lines), split it further with
`--max-windows`/`--step-months` — the per-(arm,window) resumability makes a partial run safe
to resume as an additional job later.)

## E) Cascade parity sanity check — `phaseD_cascade_parity.py` (2 jobs, one per profile)

Cheap (single-pass deterministic backtest, no Monte Carlo) — no sharding needed.

```bash
trader queue submit --priority high --db light --cpu 2 --restartable \
  --dedup tpsl_phaseD_core_parity --timeout 30m \
  --reason "Phase D sanity: core backtest_cascade deterministic replay vs baseline" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_cascade_parity.py --job core_confirm \
    --profile core --cells "<CORE_CELLS>" --from 2022-01-01 --capital 50000

trader queue submit --priority high --db light --cpu 2 --restartable \
  --dedup tpsl_phaseD_apex_parity --timeout 30m \
  --reason "Phase D sanity: apex backtest_cascade deterministic replay vs baseline (core-cascade-sizing proxy -- see file header)" \
  --env PYTHONIOENCODING=utf-8 -- \
  python experiments/tpsl_refine_2026_08/driver/phaseD_cascade_parity.py --job apex_confirm \
    --profile apex --cells "<APEX_CELLS>" --from 2022-01-01 --capital 50000
```

## F) Formal read — `analyze_phaseD.py` (no queue needed — pure CSV/text processing, seconds)

Run ONCE all of A-E have landed (reads whatever `out/phaseD_*.csv` files exist; missing
required evidence renders FLAG per-cell rather than erroring, but the summary is only
trustworthy once every job above is done).

```bash
python experiments/tpsl_refine_2026_08/driver/analyze_phaseD.py
```

Reads `out/phaseD_summary.md` only for the verdict — never the raw per-job CSVs/logs
(token economy, PREREG section 7).

## Total campaign shape

**18 queue jobs** (A=8 flat-confirm + B=2 fill-probe + C=2 survivor-only + D=4 apex2x +
E=2 parity) + one free (no-queue) analyze pass. DB budget
is 2 concurrent light slots (shared with whatever else is running — Phase C's jobs were
still in flight as of this builder's session), so expect several waves; every individual job
targets ≤90min. Submit A/B/C freely in any order/concurrency (D's baseline-first ordering is
the one hard sequencing constraint — see section D). Orchestrator owns the cross-turn queue
watch (`trader queue wait <id>` with the harness background flag, or `trader queue list` to
monitor) per this repo's "subagent queue-wait orphan" convention — this builder's own
smoke-validation waits (below) do not extend to the real run.
