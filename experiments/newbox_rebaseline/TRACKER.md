# TRACKER — 2026-H2 First-Week Compute Program (PM state)

Maintained by the orchestrator. Update the Status/Verdict columns as nights land;
this file is the single glanceable truth for "where is the program".

Last update: **2026-07-29 ~14:15 ET — PROGRAM COMPLETE.** Every night READ on the CLEAN
post-Sharadar substrate (contaminated first pass archived as the paired-report A-arm);
all gates PASS, no stop conditions fired, three P1.C confirmations, both user decision
packages (P0.3 sprint, Polygon) FINAL. **CLOSEOUT UPDATE 2026-08-02:** P0.E **APPLIED**
(Option B n10, user green-light — see known-issues 2026-08-02 ship block); P2.C **RESOLVED**
(user cancelled; TraderPolygonFinalTopup 08-05 + TraderSharadarFinalTopup 08-27 registered);
P0.D **INSTALLED** (TraderForwardRanksWeekly Sat 10:30, per-user — no elevation needed after
all; catch-up #232 ran); P0.C D: **RECOVERED** (daily 08-01 completed, weekly full dump 08-02
ran — cloud-copy creds remain the one LOCKED-USER residual); sibling B/C paired report DONE
(`2f61bd07`). Full arc below; runbook at [RUNBOOK.md](RUNBOOK.md).

Legend: `DONE` | `READY` (artifact committed, awaiting box-side run) | `QUEUED #id` |
`RUNNING` | `READ` (verdict adjudicated + recorded) | `PENDING (box)` | `LOCKED-USER`

| ID | Item | Status | Artifact / evidence | Notes |
|---|---|---|---|---|
| R0 | Ratification landed in docs (N-ladder in process.md, headers, pointers, noise-floor supersession note) | DONE 2026-07-29 | this branch's docs commit | scope of ratification in gameplan-2026H2-DRAFT.md header |
| P0.A | Box pre-flight: scheduled-task inventory, backups fresh, drift trio green | DONE 2026-07-29 (1 RED) | RUNBOOK "Pre-flight" | GREEN: 7/7 selftests, daemon 31-core scale, 9/9 sched tasks enabled (incl born-disabled lifeline check), heartbeats, drift trio (653 constants / 16 mechanisms / DTE audit). RED: backup freshness — D: wedged, see P0.C. Adjudicated PROCEED: program has zero D: dependency |
| P0.B | MC-determinism parity gate vs archived task-610 arm | READ 2026-07-29 — DIVERGENT_CLEAN_BREAK (R1) | `PARITY_VERDICT.md` + traps.md machine-scope entry | 5/12 bit-equal, 1 near-equal, 6 FP-divergent (worst 10y d_mean_ret +24.4, d_worst_dd +1.21pp); p_collapse 0.0 BOTH boxes all 12 cells. R1: P1.A certs = new reference; cross-box deltas never citable. Program proceeds |
| P0.C | Backup chain + restore drill on new topology; cloud copy | **ESCALATED 2026-07-29** | `.codex/runs/backup_daily_2026072{8,9}_*/status.json` | **D: unresponsive at device level since <=2026-07-28 03:00** (FS probes, bare Test-Path, AND Win32_LogicalDisk CIM all hang). Last good backup 2026-07-27 03:33 (1401.9 MB). Backup runs 28th+29th wedged at phase=starting; 29th's queue task #126 killed to free db budget. USER ACTION: check D: hardware. Cloud-copy creds still LOCKED-USER |
| P0.D | forward_ranks weekly catch-up job installed | DONE 2026-08-02 | `scripts/{install_,}forward_ranks_weekly.ps1` → `TraderForwardRanksWeekly` (Sat 10:30) | per-user registration needed NO elevation (the topups-installer precedent); one-off catch-up #232 ran same day (ledger was 25d stale) |
| P0.E | Sprint 30-DTE decision (Option A n4 vs Option B n10 vs stay) | **APPLIED 2026-08-02 — Option B n10 (user green-light; apex v4, H3 seg-2 frozen per OQ-7)**; decision package was REVISED on CLEAN substrate 2026-07-29 | CLEAN ECERT (#174, READ) + task-610 T-gate + `apex_dte_dd/P03_EVIDENCE.md` + live ledger | never auto-apply. CLEAN headline: apex_n10 collapse 0/12 std (dd 47-69) vs **apex_live 50-63% collapse on 2022/22-now, 5.05% on 10y, negative mean compound on 7/12 windows**. LIVE LEDGER (looked up 13:1x): v70 Apex Live running since 06-05, **-49.7% ret / 55.9% MaxDD** — consistent with the clean-cert left tail, not an outlier. Honesty bound: E-cert measures DD/collapse; time-to-2x (the sprint's purpose metric) remains task-610's CONTAMINATED-ERA evidence, not re-measured. B-arm decomposition (sibling) will split convention vs universe effect |
| M3 | gamma+IV M3 gate completion on corrected panel (licensed 2026-07-25; flags stay OFF) | READ — GATE COMPLETE; **CLEAN RE-CONFIRM 13:25 REPRODUCES** (TP-agr +0.0398 t+1.92 N=201 noninferior-stronger; SL-FNR +0.0667 t+1.44 N_sl=30 still underpowered-worse; robustness t 2.04->1.78; ledger N=3158, holdout PASSED) | `results/pertrade_results.{json,txt}` (fresh 04:08) on ledger_v1.parquet N=3228 | M3 measured (compute_metrics.py, holdout guard PASSED): TP-agreement f2gamma noninferior +0.0189 (t +0.96, N=212); SL-FNR +6.1pp point-WORSE (t +1.39, N_sl=33 — still UNDERPOWERED primary), all-inclusive +4.1pp t +2.04 N=98 marginal-worse. Pair stays A/B-only, flags OFF (per license). Dec M3 re-read needs live-fill SL-event doubling, NOT Polygon → supports memo's Polygon-cancel default. Polygon decision package READY for user (P2.C). VERDICT.md staleness noted 07-29 (frozen 07-13; licensing stands per c4c3ce91) |
| P1.A | E-tier certificates: Core / Apex live / Apex n10 / Sentinel, N=2000x12 + deep N=1000 | **READ 2026-07-29 — CLEAN CERTS = THE R1 REFERENCE SET.** GATES: Core PASS, Sentinel PASS | CLEAN: `ECERT_SUMMARY.md` + `results_ecert/` (64 cells, #174). Contaminated arm-A archived: `pre_sharadar_contaminated/` | Core: collapse 0 all 12 std + all 4 deep; deep compound now POSITIVE on honest universe (dotcom +8.3, gfc +28.6). Sentinel: 0 everywhere (dotcom +311 at 38.7 dd). apex_n10: 0 collapse all 12 std, dd 47-69. **apex_live PHASE CHANGE vs arm-A: std collapse 2022 50.35%, 22-now 63.45%, 10y 5.05% (was 0-0.10%)** — joint delta (convention+universe, NOT attributable; B-arm decomposition pending w/ sibling); reading: survivorship discount surfaced — delisted 2022-24 deaths now in-universe at 25%x4 concentration. Digest note ratified: arm-A deltas sourced from archived per-cell JSONs (summary file was sentinel-only overwrite) — acceptable evidence. Deep cells: SCREEN-only; survivor-floor caveat applies to arm-A/B only, clean certs carry honest delisted coverage from 1997-12-31 |
| P1.B | N-ladder doctrine amendment (B=300/C=500/D=500-1000/E=2000-cert) | DONE 2026-07-29 | process.md "N-ladder doctrine" | knob search never above C |
| P1.C-1 | Pessimism-certification matrix at N=1000 | **READ 2026-07-29 — NO FLIPS (ruling)** | `PESSIMISM_N1000_SUMMARY.md` + `results_pessimism_n1000/` (126/126 clean, #176) | Ruling: Core keep-decisions HOLD — 0 hard flags (p_coll=0 all Core cells at N=1000 clean); Core DD-regression flags (next_open +7pp, combined +10pp) are the archived margin-note class, non-gating. Apex had NO clean keep-state to flip (archived cert already "NOT execution-robust"); clean quantification is much worse (combined_pessimist apex: 5y coll 81.9%, 22-now 94.7% vs archived-N300 39.3/25.7) — feeds P0.E context, triggers nothing. No named E-tier re-validation. Archive-N correction ratified: reference was N=300 (N=500 was only the flip-escalation tier; runbook's "N=500" label wrong — trust-source rule) |
| P1.C-2 | Deep crash screens at N=1000 (Core + Apex-held) | **READ 2026-07-29 — SCREEN PASS** | `DEEP_N1000_SUMMARY.md` (8/8, #177) | Core deep: 0 collapse all 4, compound positive on 3/4 (honest universe). Apex-held: dotcom 75.5% (was 100%), gfc 3.5% (was 20.7%), **2007_now 68.1% (+19.8pp vs archived)** — reported, no-hard-fail class, pre-arms Dec H5. Known cosmetic: summary-writer archive key mismatch (apex vs apex_live) left the MD's vs-N300 column blank — data intact in JSON; post-program fix note |
| P1.C-3 | v74 whole-tail re-ablation N=1000 (v73 substrate, pre-cutoff only) | **READ 2026-07-29 — CONFIRMS** | `TAIL_N1000_SUMMARY.md` (16/16 paired cells, #186, clean substrate) | 5y: notail dd 53.1 vs full 61.6 (**-8.5pp**, ~79% of the N=300 magnitude) at compound parity (+1633 vs +1590), collapse 0/0; direction replicates 7/8 windows (2024 = flat tie inside noise floor). Nuance logged: off-5y compound is mixed (full leads 2024/2025/dip/22-now, notail leads 2021/2022/2023/5y) — DD-primary read unchanged. v74 ship stands; Dec H5 stage-2 pre-armed with N=1000 clean-substrate evidence |
| P1.D | 10y refresh chain + measured runtime table | **READ 2026-07-29 — DONE** (proxy chain + pack #185) | `RUNTIME_TABLE.md` (written from sibling's queue-persisted timings) + v74 research pack (fresh 13:2x) | Pack: 15 files, 13/14 windows ready (deep_1995_now blocked by 1997-12-31 Sharadar start — permanent), assessment complete, 75+ WR15 n-weighted ~64.3% (n=4,511) on clean substrate; `/api/backtest/temporal` VERIFIED serving the fresh stress windows (pack_path match + numeric match). Anomalies logged: (i) unsuffixed `stress_windows.json` was byte-identical to SENTINEL not Core-default — **FIXED 2026-07-29**: root cause was research_pack.py:23 hardcoding `DEFAULT_PORTFOLIO_PROFILE="sentinel"` (stale since the 06-17 Core restructure); now imports `portfolio_profiles.DEFAULT_PROFILE_KEY`, v74 artifact repaired (md5 = core), drift guard green; (ii) `verify_scorecard.json` not in build scope (stale Jun-24) ; (iii) Core 22-now deterministic single-path -42.2% vs MC-aggregate +518 — known deterministic-vs-MC methodology gap, no archived comparator to quantify substrate share |
| P1.E | Noise-floor measurement (batches x tiers x 3 windows) | **READ 2026-07-29 — clean table ADOPTED** | CLEAN `NOISE_FLOOR_TABLE.md` (12x B=8, #175); contaminated version archived | Clean-substrate dispersion in the SAME band as the contaminated measurement (dd std 0.12-0.56pp; growth compound spread up to ±5% @300 [22-now widest] -> ±1-1.5% @2000; p_coll 0.0 all 96 batches both measurements) — the floor is substrate-robust. known-issues.md updated to clean values, pre-rebuild caveat dropped |
| P2.A | Sharadar $40 delisted-equity grab + survivorship chain | **PULLED 2026-07-29** — steps 0,2-4 open | `pull_sharadar.py` -> `.cache/sharadar/` (46.2M rows, 4 tables, manifest+sha256); `PULL_VERIFICATION.json`; ingest still SCAFFOLD | Purchased+pulled. Survivorship quantified: live universe covers only 408/8,820 dot-com-window tickers (4.6%); 15,627/21,934 equities delisted. NEXT: freeze survivor-only baseline (step 0, blocking) -> dry-run ingest + <1% split reconciliation KILL gate -> queued recompute -> paired report |
| P2.B | Real-fill loop watch (N>=30 fills triggers first canon verdict) | standing | weekly slippage report | trigger = fill count, not calendar |
| P2.C | Polygon cancel-vs-keep before ~Aug-6 renewal | **RESOLVED 2026-08-02 — user CANCELLED; TraderPolygonFinalTopup registered for 08-05** (flat-file archive already COMPLETE+VERIFIED per data-acquisition.md) | M3 clean re-confirm (see M3 row) + rolling-window caveat (2026-07-25 commit) | Evidence supports memo's CANCEL default: gate complete on clean data, pair stays A/B-only OFF, Dec M3 re-read needs live-fill SL events (not Polygon). Residual keep-case: only if final re-parameterized 2022-23-depth pulls are wanted (unrecoverable after cancel). User's call |

## Box ops notes (Day-0, 2026-07-29)

- **PowerShell startup is wedged while D: hangs** (pwsh enumerates drives at init) — all
  box automation runs Git-Bash + direct `python.exe` until D: recovers. Day-0 submissions
  were made by the orchestrator directly instead of via a night-runner subagent (pattern
  deviation, logged): a subagent's default pwsh habits would stack more wedged processes
  against the blocked mount manager. Revisit for Night 1 — if D: is fixed, resume the
  normal delegation pattern.
- Trap promoted to traps.md + debug-pipeline GUARDS: a wedged drive masquerades as a
  daemon livelock (queue CLI "hangs" that are really pwsh-startup hangs). Verify via Bash
  `tasklist` + direct-python `queue status` before any daemon surgery.
- **Morning acceleration (04:20 ET adjudication):** new-box MC speed (parity 4m17s, full
  core E-cert 9m25s) makes calendar-night pacing moot. Resequencing: N2 (#131) -> N3
  (sentinel + noise floor) -> N5 -> N6 all db-LIGHT this morning IN ORDER with per-night
  digests; **N4 (10y refresh) + N7 pre-step are db-HEAVY and DEFERRED to tonight
  post-close** — submitting them now would admit immediately (04:xx is off-market) and a
  multi-hour db-heavy chain would straddle the trading day against the tight-timeout
  MySQL + scheduled updates. RUNBOOK conflict note: runbook's 4-before-5/6 ordering was
  calendar packing, not a data dependency (RUNTIME_TABLE only recalibrates estimates);
  deviation recorded per trust-source rule.
- **#125 (Jul-28 close pipeline) post-mortem:** phase 1 scores+portfolio COMPLETED 16:52
  (pushes fired); process VANISHED during phase-2 options pull (~16:52-17:32, exit=None,
  "no terminal artifact") — predates the pwsh wedge; separate one-off. Loss: Jul-28
  options snapshot (point-in-time, unrecoverable) + phase-3 tail (self-heals tonight).
  No recovery action. WATCH tonight's 16:30 run for recurrence. Heartbeats 03:31+04:01
  confirm the powershell.exe(5.1) scheduled path still works despite the pwsh-7 tool
  wedge, so tonight's close pipeline is expected to launch normally.

## SUBSTRATE CONTAMINATION EVENT — 2026-07-29 ~05:15 (program-wide reclassification)

The P2.A Sharadar reconciliation found **pre-existing price_history convention contamination**
(verified from artifacts, not taken on trust): 334/757 symbols (44.1%) carry MIXED adjusted-close
conventions inside their own series (`CONVENTION_MAP.json`), external reconciliation mismatch
14.8% vs <1% gate = KILL (`RECONCILIATION.json`); 84 backfill seams inject phantom one-day crashes
(median −28.9%; e.g. ADM 2015-12-24 36.65→26.88 in DB vs 36.50 actual). The 2024-12-02 seam
(134 symbols) sits INSIDE the 5y window — this is not deep-history-only.

**Adjudication (orchestrator):**
- **N1-N3 E-certs + noise floor are RECLASSIFIED**: they are the frozen, survivor-only,
  convention-contaminated **BEFORE side** of the paired report (already committed + snapshotted to
  `experiments/data_ingest/survivor_baseline_pre_sharadar/`). NOT citable as absolute references.
  Their collapse=0 results are conservative-direction (contamination injects phantom crashes and
  Core/Sentinel still didn't collapse) but must be re-confirmed on clean data.
- **P0.B parity**: the machine-scope FINDING stands (FP divergence across microarchitectures is a
  machine property); the R1 "certs = reference" role transfers to the POST-rebuild re-runs.
- **M3 / Polygon package**: ledger_v1.parquet reads price_history → re-run build_ledger +
  compute_metrics after substrate-final (40s+1s) before the ~Aug-6 decision. Exposure is likely
  small (M3 eras 2025/2026H1 have the lowest contamination, 17.3%/7.2%) but "likely small" is not
  a verdict.
- **Resume protocol (CRITICAL — resume-guard poisoning):** the runners resume on per-cell JSON
  presence. Before ANY post-rebuild re-run, ARCHIVE (move) `results_ecert/`,
  `results_pessimism_n1000/` (46 contaminated cells), `results_noise*/` + the summary artifacts to
  a `pre_sharadar_contaminated/` subdir — otherwise the resume machinery silently splices
  contaminated cells into the clean runs.
- **Rebuild status at 05:30:** attempt #151 FAILED SAFELY (FK 1452 — delisted tickers lack stocks
  parent rows; probed AA/AAL/AAOI/ADM/ZTS row counts vs the 4.94M-row backup parquet: all equal,
  DB intact; only additive nullable `close_unadj` landed). Maintenance-mode wrappers verified
  effective (update/notify/queue scripts all read the flag). Sharadar session notified with
  diagnosis; program resumes only on their substrate-final ping.
- **Rebuild SUCCEEDED (sibling report ~06:00, attempt #3):** mixed-convention 334/757 (44.1%)
  -> 6/1562 (0.4%); rows 4.96M -> 7.13M; universe 811 -> 1,633 symbols (729 stocks rows created,
  465 delisted, provenance added); ADM seam phantom eliminated; `close_unadj` (as-traded) stored
  alongside adjusted close — closes the traps.md "strikes are AS-TRADED" gap. Backup parquet
  copied to B:\trader_rebuild_safety_2026-07-29\ sha256-verified. Remaining to substrate-final:
  indicators+weekly (~35min), recalculate --force --full --all (long pole, unmeasured), breadth/
  regime backfill, assess. NOTE for re-runs: post-rebuild certs sit on BOTH changes at once
  (clean conventions AND doubled universe) — before/after attribution is confounded by design;
  middle arm **ADOPTED by sibling (~06:15) as a P2.A step-4 spec fix**: report becomes three
  arms — A frozen contaminated/survivor (79 artifacts) -> B clean/survivor-895 -> C clean/full-
  PIT-1633; A->B = convention-repair effect, B->C = the TRUE survivorship discount (a raw A->C
  delta conflates them and is banned from the write-up). `close_unadj` in the gamma+IV ledger
  builder = post-program improvement seed, NOT for today (restraint clause). Sibling timings so
  far (queue-persisted, for RUNTIME_TABLE): pull 45s, parquet 31s, convention map 35s, symbol map
  40s, dry-run 10s, REBUILD COMMIT 3m56s (7.1M bars — chunked INSERTs, NOT bulk_build; do not
  cite as a bulk_build benchmark), verification 50s, indicators+weekly ~32m proj.
- **SUBSTRATE-FINAL 2026-07-29 11:22:48 ET (sibling ping).** Final state verified: price_history
  7.10M rows / 1,626 syms / 1997-12-31->2026-07-28; scores(v74) 7.02M rows / 1,608 syms; orphans
  0 (281k purged); stocks 1,638 (600 delisted); mixed-convention 0.4% residual. FOUR extra
  defects repaired en route — arm-A's damage axes are now THREE: (i) mixed conventions,
  (ii) DECIMAL(10,2) sub-dollar quantisation (9,672 bars <$0.10; NVDA 1999 27% error),
  (iii) 12 double-claimed vendor tickers (7 with identical bars under two symbols — breadth
  inflation + double-position risk) — plus HAR/COL local corruption restored. **CLEAN RE-RUNS
  SUBMITTED 11:3x:** #174 e-cert all-4-arms, #175 noise floor, #176 pessimism, #177 deep screens
  (db-light, running now); #178 notail pre-step, #179 M3 ledger, #180 research pack (db-heavy,
  off_market window). **N7-main HELD** until #178's parquet exists (runner exit-2 guard).
  M3 metrics (compute_metrics.py, 1s foreground) runs after #179. Contaminated results archived
  to `pre_sharadar_contaminated/` (resume-guard protocol executed; results_parity intentionally
  left — P0.B never re-runs). RUNTIME_TABLE.md WRITTEN from sibling's queue-persisted timings
  (P1.D satisfied; DTE ~85%-of-runtime lever + bulk-load caveat included). Deep-cell caveat
  UPDATE: post-rebuild deep windows now carry honest delisted coverage (1997-12-31+) — the
  "survivor-floor optimistic" annotation applies to arm-A/B only, NOT to the clean certs.
- **USER AUTHORIZATION CONFIRMED (~05:45, direct; deadline framing corrected ~05:55):**
  maintenance day authorized, AND today still gets scored — but there is NO 15:30 constraint
  (user pushback, correct): post-close scoring is time-insensitive until late evening; if the
  lift lands after the scheduled 16:30 slot (wrapper no-ops under the flag), the PM runs the
  close pipeline manually through the queue afterward — identical scores, only the notify
  pushes are late. Watch posture: quiet flag-lift watch + a 22:30 sanity check (~1.5h manual
  runway would remain). Sibling told: schedule is theirs, no deadline. Lift mechanics: remove
  `.cache/MAINTENANCE_MODE` (wrappers re-enable at next fire); daemon restart only for budget
  knobs.

## RESEARCH_PRIORITY policy (standing, 2026-07-29)

`.cache/RESEARCH_PRIORITY` (verified: exists, daemon-wired at queue_daemon.ps1:25/47): research
and cross-agent compute outrank the scheduled `trader update`; MARKET_GUARD=0; db budget stays 2
(MySQL-capacity limit, not an update reservation). Consequence for this program: the runbook's
`--window off_market` convention on db-heavy jobs is retired for the policy's duration — #178-180
(1h42m idle-waiting on an idle box) cancelled and resubmitted windowless as **#183 (notail
pre-step) / #184 (M3 ledger) / #185 (research pack)**, all high. Update-side facts from sibling:
update cpu reservation cut 8->3 (serial loop, ~90% of one core measured); vendor-ticker pull fix
landed (BF.B/BRK.B/SATS were silently failing every pull; `.cache/pull_failures.json` ledger +
heartbeat check now exist) — per-symbol freshness now auditable.

## Post-program doc adoptions (do after the artifacts land)

- [x] known-issues.md "MC Noise Floor": swap Phase-v32 figures for `NOISE_FLOOR_TABLE.md`. — DONE 2026-07-29 (measured table + retirement of the 1.6-1.8x folklore figure).
- [x] queue-ops/process runtime guidance: fold `RUNTIME_TABLE.md` measured wall-clocks in. — DONE 2026-07-29 (task-queue.md "Measured new-box wall-clocks" + CLAUDE.md core-sizing already revised by P2.A session).
- [x] gameplan-2026H2-DRAFT.md section-5 statuses + this TRACKER: mark nights as READ with verdict one-liners. — DONE 2026-07-29 (§5 EXECUTED block).
- [x] If parity verdict = DIVERGENT_CLEAN_BREAK: add the dated line to traps.md machine-scope entry (reference PARITY_VERDICT). — DONE 2026-07-29 (adjudication paragraph appended).
- [x] Research packs: night 4 rebuilds v74; confirm `/api/backtest/temporal` serves refreshed stress windows. — DONE 2026-07-29 (#185; API verified serving fresh pack_path + matching numbers).

## Standing calendar (unchanged, pre-registered)

| When | What |
|---|---|
| ~Oct 2026 | OSK forward_ranks read #1 (needs P0.D live; descriptive, non-gating) |
| 2026-12-15 | Pre-registered OOS evaluation (`experiments/holdout_oos_2026_12/run_oos_eval.py`, H1-H6 + PL pack) |
| ~Jan 2027 | OSK tilt re-read + forward_ranks read #2 |
| 2027-06-15 | Extension date for single-window miss / INSUFFICIENT_N deferrals |

## Restraint clause (in force until 2026-12-15)

No in-sample scoring churn, no new mining passes on closed axes, no holdout-cutoff
motion. This program is certificates, calibration, fidelity, and hygiene — by design.
