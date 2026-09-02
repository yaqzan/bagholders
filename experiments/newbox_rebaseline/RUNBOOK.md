# RUNBOOK — 2026-H2 First-Week Compute Program (9950X3D)

**Status:** RATIFIED 2026-07-29 (user directive; scope + still-gated items in
`.claude/docs/gameplan-2026H2-DRAFT.md` header). Source plan: that file, section 5.
PM state: [TRACKER.md](TRACKER.md). Runners in this dir were authored cloud-side on
2026-07-29 and are DB-free self-testable (`--selftest`); the compute itself runs ONLY
on bookmaker via `trader queue submit`.

**Who runs this:** a Claude session on bookmaker, or the user pasting the blocks below.
One block per night. Everything is restartable and resume-safe (per-cell JSON presence
is the resume mechanism; a fresh N means a fresh results dir — already encoded in the
runners).

---

## Pre-flight (Day 0, once, before any submission) — P0.A

```
# 0. Get this program onto the box (from the repo root):
git fetch origin claude/hardware-backfill-opportunities-1xue4s
git merge --ff-only origin/claude/hardware-backfill-opportunities-1xue4s   # or merge via main once reviewed

# 1. Selftests (DB-free, seconds):
python experiments/newbox_rebaseline/run_parity_gate.py --selftest
python experiments/newbox_rebaseline/run_ecert.py --selftest
python experiments/newbox_rebaseline/run_noise_floor.py --selftest
python experiments/newbox_rebaseline/run_refresh_10y.py --selftest
python experiments/newbox_rebaseline/run_pessimism_n1000.py --selftest
python experiments/newbox_rebaseline/run_deep_screen_n1000.py --selftest
python experiments/newbox_rebaseline/run_tail_ablation_n1000.py --selftest

# 2. Queue + scheduler health:
trader queue status          # daemon green; budget line shows the 31-core scale
# PowerShell: Get-ScheduledTask | Where-Object {$_.TaskName -match 'Trader'}
#   -> ALL enabled: TraderQueueDaemon, TraderBackupDaily, TraderOpsHeartbeat,
#      TraderSkillOOSCacheRefresh, TraderOOSEvalDue2026, portfolio notify (08:45/15:30),
#      TraderSlippageReportWeekly.  (Succession brief section 0: tasks were imported
#      born-disabled during migration — this check is the December-reminder lifeline.)

# 3. Backup freshness: newest dump in the backup target < 48h; one 08:30 heartbeat
#    digest received since cutover.

# 4. Drift trio (all must be green before any evidence run):
python tests/test_strategy_config_drift.py
python tests/test_mechanism_registry.py
python experiments/_dte_audit/audit.py
```

Any red here is fixed BEFORE Day 0 compute. P0.C (restore drill on the new topology)
and P0.D (forward_ranks weekly job — mirror the TraderSkillOOSCacheRefresh install
pattern for `experiments/era_conditioning/build_forward_ranks.py`) are box-side ops
items tracked in TRACKER, not blockers for P0.B.

---

## Day 0 — P0.B MC-determinism parity gate  (BLOCKS EVERYTHING)

Every MC baseline in the corpus is an old-box number and seeded MC is machine-scoped
(traps.md 2026-07-19). Nothing new-box may be compared cross-box until this verdict.

```
trader queue submit --priority high --db light --cpu 8 --restartable \
  --dedup mc-parity-gate --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P0.B: new-box MC determinism vs archived task-610 staged_30dte_n10 N=500x12" \
  -- python experiments/newbox_rebaseline/run_parity_gate.py --cpu 8
trader queue wait <id>        # agents: run with the harness background flag
```

**Read `PARITY_VERDICT.md`:**
- `PARITY_BIT_EQUAL` -> green; historical old-box numbers remain directly comparable.
- `PARITY_FP_DRIFT` -> green with the canon caveat: same-box paired A/Bs valid;
  cross-box deltas never citable.
- `DIVERGENT_CLEAN_BREAK` -> R1 protocol (already in the verdict text): the P1.A
  certificates become the new reference; cross-box deltas are never cited as evidence.
  Do NOT stop the program — proceed to Night 1, which creates the new reference.

---

## Parallel track — TIME-SENSITIVE, run before the ~Aug-6 Polygon decision

Licensed by the 2026-07-25 M1 re-read (pair RE-OPENED; "completing the gate is
licensed; flipping the flags is not"). M3 is the unmeasured leg and needs a db-heavy
ledger rebuild. The Polygon Developer window is a ROLLING 4 years — any pull needing
2022-23 depth is unrecoverable after cancellation.

```
trader queue submit --priority high --db heavy --cpu 6 --restartable --window off_market \
  --dedup ivm3-ledger --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "gamma+IV M3 completion: build_ledger re-run on the corrected panel (pre-Polygon-decision)" \
  -- python experiments/iv_engine_pertrade/build_ledger.py
# then the metrics step per experiments/iv_engine_pertrade/VERDICT.md (M3 SL-FNR read).
```

Decision input for the user (recorded in TRACKER): M3 result + whether any final
re-parameterized Polygon pulls are wanted while 2022-23 is still in the window.

---

## Nights 1-3 — E-tier certificates (P1.A) + noise floor (P1.E)

```
# Night 1 - Core certificate: N=2000 x STANDARD_12 + deep 4 x N=1000
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup e-cert-core --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.A: Core E-tier certificate N=2000x12 + deep N=1000" \
  -- python experiments/newbox_rebaseline/run_ecert.py --arms core --cpu 10

# Night 2 - both Apex arms (live elbow + staged Option-B n10) -> P0.3 decision evidence
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup e-cert-apex --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.A: Apex live-elbow + OptionB n10 E-tier, N=2000, paired seeds" \
  -- python experiments/newbox_rebaseline/run_ecert.py --arms apex_live,apex_n10 --cpu 10

# Night 3 - Sentinel certificate + the noise-floor measurement (two submissions)
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup e-cert-sentinel --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.A: Sentinel E-tier N=2000x12 + deep N=1000" \
  -- python experiments/newbox_rebaseline/run_ecert.py --arms sentinel --cpu 10
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup noise-floor-recal --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.E: measured seed-noise dispersion N in {300,500,1000,2000} x {5y,22-now,2020_crash}" \
  -- python experiments/newbox_rebaseline/run_noise_floor.py --cpu 10
```

**Reading rules (encoded in the summaries):** Core + Sentinel must be collapse=0 on
all 12 standard cells (0/2000 => true collapse <= 0.15% per cell). Apex arms report
collapse with bounds (user-owned sprint budget; no hard fail). Deep cells are SCREEN
only; the Apex held-form deep-FAIL (dot-com) is a known expected quantification.

**Post-Night-3 doc adoption (orchestrator/doc chore, tracked):** replace the Phase-v32
noise figures in known-issues.md "MC Noise Floor" with `NOISE_FLOOR_TABLE.md` values.

---

## Night 4 — 10y refresh chain (P1.D; DB-heavy, expect old-box-like times)

```
trader queue submit --priority normal --db heavy --cpu 8 --restartable --window off_market \
  --dedup recalc-10y-newbox --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.D: full 10y recalc + assess + research pack + parquet rebuild; record wall-clocks" \
  -- python experiments/newbox_rebaseline/run_refresh_10y.py
```

Emits `RUNTIME_TABLE.md` — the honest new-box runtime table. Use it to recalibrate
the remaining nights' estimates and the queue-ops guidance numbers.

---

## Nights 5-7 — power re-runs of the THREE licensed compute-truncated decisions

(Section-3 rule: a re-run requires a recorded truncation note. These three qualify;
nothing else does. Any other "while we're at it" re-run request = R7, refuse.)

```
# Night 5 - pessimism-certification matrix at N=1000 (canon is load-bearing)
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup pessimism-cert-n1000 --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.C-1: pessimism robustness matrix re-cert at N=1000 (7 arms x 2 profiles x 9 windows)" \
  -- python experiments/newbox_rebaseline/run_pessimism_n1000.py --cpu 10

# Night 6 - deep crash screens at N=1000 (SCREEN stays SCREEN)
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup deep-screen-n1000 --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.C-2: deep crash screens N=1000, Core + Apex-held" \
  -- python experiments/newbox_rebaseline/run_deep_screen_n1000.py --cpu 10

# Night 7 - v74 whole-tail re-ablation at N=1000 (v73 substrate, in-sample only)
#   pre-step (db heavy, minutes): build the v73 notail parquet if absent.
#   NOTAIL_VID_OVERRIDE=73 is REQUIRED: without it the builder targets the ACTIVE
#   version (v74), whose tail is already retired -> a meaningless v74-vs-v74
#   self-comparison (the runner's precondition check would refuse). Never /revert
#   the active pointer for this; the env override exists precisely to avoid that.
trader queue submit --priority high --db heavy --cpu 4 --restartable --window off_market \
  --dedup notail-parquet-v73 --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --env NOTAIL_VID_OVERRIDE=73 \
  --reason "P1.C-3 pre-step: notail parquet (v73 substrate via env override)" \
  -- python experiments/skill_vs_baseline/build_notail_parquet.py
trader queue submit --priority high --db light --cpu 10 --restartable --window off_market \
  --dedup v74-ablation-n1000 --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --reason "P1.C-3: whole-tail ablation N=1000x8, pre-cutoff windows only (pre-arms Dec H5)" \
  -- python experiments/newbox_rebaseline/run_tail_ablation_n1000.py --cpu 10
```

**Adjudication (orchestrator, not the runners):** a pessimism keep-decision flip ->
named E-tier re-validation of that lever BEFORE further trust (pre-committed). Deep
screens: FAIL = mandatory investigation, PASS = weak comfort. Tail ablation: v74 ship
stands either way; the artifact pre-arms December H5 stage-2.

---

## Standing rules for every submission

- Market hours: heavy jobs carry `--window off_market` or priority normal/low; `high`
  without off_market only for db-light short jobs. `critical` = emergencies only.
- Fresh results dirs per N (the per-cell resume guard has no N in the path — encoded
  in the runners, do not hand-run MC into an old dir).
- Never rename a window label between arms (label hash IS the seed pairing).
- Knob search NEVER above N=500; the E-tier is certificate-only (process.md N-ladder).
- Compute abundance is not evidence: no re-opening WHAT-NOT-TO-DO axes (R7).
- After each night: update [TRACKER.md](TRACKER.md) status + verdict pointer.

## Open user decisions this program does NOT execute (surfaced, gated)

1. **P0.3 sprint 30-DTE switch** — decision package complete after Night 2 (E-tier on
   both arms + task-610 T-gate + the live ledger's own envelope miss). Apply via
   /ship-portfolio on green-light only.
2. **Polygon cancel-vs-keep by ~Aug-6** — after M3 lands; remember the rolling-window
   unrecoverability of 2022-23 pulls.
3. **Sharadar $40** (P2.A) — user is evaluating; on landing, the ingest + recompute
   chain runs as a weekend queue job per data-acquisition.md and retroactively
   upgrades every deep-window number above from survivor-floor to honest.
