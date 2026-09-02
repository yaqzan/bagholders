# Trader — 2026-H2 Masterplan (9950X3D era)

> # **RATIFIED 2026-07-29** (user directive: "proceed with the gameplan").
> Ratification scope: the §2 P0-P4 restack, the §4 N-ladder amendment (landed in
> [process.md](process.md) "N-ladder doctrine"), and the §5 first-week compute program.
> Execution artifacts, runbook, PM tracker: `experiments/newbox_rebaseline/`.
> **Still individually gated (🔒 user — NOT covered by the blanket ratification):**
> P0.E/P0.3 sprint 30-DTE switch (live ledger — never auto-apply), P2.A Sharadar $40
> (user: "I'll look into it" 2026-07-29), P2.C Polygon cancel-vs-keep (decide before the
> ~Aug-6 renewal; per the 2026-07-25 finding, Developer depth is a ROLLING 4-year window —
> any pull needing 2022-23 era data is unrecoverable after cancellation, and the licensed
> gamma+IV M3 gate-completion run should land BEFORE the decision).
> Filename keeps `-DRAFT` for pointer stability; this header is the status of record.
> [gameplan.md](gameplan.md) remains the strategic layer (mission, frontier verdicts,
> decision rights, anti-goals); where the two P0-P4 stacks overlap, this file supersedes.
>
> *(Original 2026-07-17 header follows for the record: authored by FABLE as a PROPOSAL;
> nothing in it authorized spend, live-ledger changes, doctrine changes, or re-opening
> closed axes until ratification. Decision rights per gameplan.md §7 unchanged.)*

**Context:** the PC migration to the 9950X3D box (16c/32t, ~2-3× MC throughput vs the old 8-core)
was imminent; the December-2026 OOS evaluation is fully pre-registered
(`experiments/holdout_oos_2026_12/PREREGISTRATION.md`); the June-July research campaign closed
essentially every in-paradigm mining axis. This plan is about what a 2-3× compute multiple and a
clean December read are actually FOR.

---

## 1. The honest corpus state (why this restack looks the way it does)

**In-paradigm EOD price/volume/breadth mining is CLOSED** — not cold, closed, at pre-registered
bars, across every family tried (full citations in known-issues.md WHAT NOT TO DO + CLOSED archive):

- `Score.overall` re-shaping/reweighting/normalization/calibration/cliff-smoothing — DEAD (multi-layer closure 2026-06-24/25; funded value is the 70/75 gate, the gradient above it is per-trade-inert on every tested axis).
- Calendar/periodic waves (OPEX/TOM/DOW/seasonal, 17/17 cells) — NULL with era mirror (`wave_cycle_mine`).
- Peak/extension/blowoff "fakeout at the top" features and interactions — NULL 0/131 cells (`peak_fakeout`).
- Trend MA-lattice (kernels×horizons×crosses×states×sub-terms, 349 cells) — NULL (`trend_ma_lattice`).
- EOD fakeout-proneness proxies — NULL at |t|<0.3, N=46-52k (`miss_regime_fakeout`).
- Dealer-GEX (all forms) — NULL, closed permanently (`gex`).
- OSK skew: modifier BLOCKED at the faithful path; tilt PARKED at its own bar; L3 buy OFF.
- The DD-sizing-lever well — DRY after 5 shipped levers (2026-06-08); regime/breadth/VIX-momentum reformulations, puts, entry timing, cut/realloc, component reweighting all closed.

**What remains open is exactly four tracks:** (a) **Data acquisition** — money-gated ([data-acquisition.md](data-acquisition.md)): the $40 Sharadar delisted-equity grab (🔒 user), nothing else at current evidence (L3 $2k OFF, both gates died). (b) **Option-model fidelity** — gated on a NEW DATA CLASS; the P3.7 real-fill log already accrues free (re-opens the gamma+IV per-trade gate at N≥30-50 fills; also the asymmetric-cost canon's first real test). (c) **December OOS discipline** — the pre-registered read (H1-H6+PL-1..6), the most informative future event for the honest stack; protect it (no in-sample churn). (d) **Compute-scale re-validation** — the new box's 2-3× is a power upgrade, not license to re-open closed axes; its value is tighter collapse certificates, de-noised screens, and re-running the handful of decisions explicitly truncated by compute (§3, and only those).

---

## 2. P0-P4 restack (post-migration era)

Effort keys: [S] hours, [M] a day-ish, [L] multi-day. 🔒 = user green-light required. Compute via
`trader queue submit`, always.

### P0 — Migration integrity (the new box earns trust before it earns workload)

| # | Action | Effort |
|---|---|---|
| P0.A | Bring-up + scheduled-task inventory: restore/attach MySQL; migrate `config.py` (outside repo, must move by hand); re-register the full Task Scheduler set (`TraderQueueDaemon`, `TraderBackupDaily`, `TraderOpsHeartbeat`, `TraderSkillOOSCacheRefresh`, `TraderOOSEvalDue2026`, portfolio notify 08:45/15:30 ET, `TraderSlippageReportWeekly`). Verify `/health` 200, dashboard serving, queue status green, heartbeat digest, drift-guard+registry+`_dte_audit` green. A migration that silently drops scheduled tasks kills backups AND the December reminder — highest-leverage checklist on the plan. Accept: one full green heartbeat cycle + one on-schedule backup + `Get-ScheduledTask` shows every entry. | M |
| P0.B | MC determinism parity gate: before ANY new-box number is trusted, re-run a pinned old-box result (task-610 Option-B arm, N=300, same seeds/windows), diff per-window {ret,DD,collapse}. Bit-equal or within documented seed-noise → green; else STOP and diagnose (Python/BLAS/package versions, MP spawn semantics) before P1. The entire historical gate ledger is old-box — cross-box comparability is an assumption until tested. | S + 1 queued run |
| P0.C | Backup chain on the new topology: D:-array reattach, `TraderBackupDaily` re-pointed, ONE timed restore drill, cloud-copy credentials (🔒 user, still open). | S/M |
| P0.D | forward_ranks weekly catch-up job (PL-6 ops gap): weekly off-market queue submission of `experiments/era_conditioning/build_forward_ranks.py` (mirrors `TraderSkillOOSCacheRefresh`). Without it the ~Oct N≥60 OSK read has no data. Accept: parquet ≤7 days stale through January. | S |
| P0.E | 🔒 Surface the P0.3 sprint 30-DTE decision (still the user's; evidence complete — task 610: Option B n10 DD better 12/12, collapse 0/12 vs the live elbow's own ledger printing −39.7% vs envelope p05 −19.4%, non-adjudicated). H3 piecewise protocol is pre-registered, no eval-cleanliness reason to delay. This plan adds an E-tier certificate for whichever option is chosen (§5 night 2). | 🔒 |

### P1 — Compute-scale re-validation (weeks 1-2; the inaugural program)

| # | Action | Effort |
|---|---|---|
| P1.A | Inaugural E-tier certificate: Core/Apex-sprint/Sentinel at N=2000 across the canonical 12-window set + deep-screen windows at N=1000 (SCREEN not gate). Purposes: collapse-certificate tightening (0/500⇒≤0.6%, 0/2000⇒≤0.15%, a 4× tighter floor on the hard constraint); pinned new-box baselines; P0.B at scale. No knob changes ride along — certificate, not calibration. Accept: collapse=0 all cells at N=2000; DD/compound within documented noise of historical D-tier rows. | 3 queued nights |
| P1.B | N-ladder doctrine amendment (§4), ratified as a process.md edit. | S (docs) |
| P1.C | Power re-runs of explicitly compute-truncated decisions ONLY (each has a recorded truncation note): (1) pessimism-certification matrix N=300×8→N=1000 (own design named the escalation; canon is load-bearing); (2) deep crash screens N=300→N=1000 (tightens Apex held-form deep-FAIL % and Core deep-PASS, stays SCREEN); (3) v74 whole-tail re-ablation N=300×8→N=1000, in-sample rows only (hardens the standing ship, pre-arms Dec H5 stage-2 — MUST stay pre-cutoff, no OOS rows before Dec-15). Explicitly NOT re-run: DQT, P3.2 Core resweep, lifecycle-MC rotate policy (unless P0.3 flips), 45-DTE (own kill clause), glide path (real-world trigger only), any WHAT-NOT-TO-DO axis — closed at their own pre-registered bars, re-running = bar-shopping. Accept: each re-run confirms (tighter CI, no decision change) or surfaces a flip → named E-tier follow-up. | 3 queued nights |
| P1.D | 10y refresh + runtime calibration: `trader recalculate --force --full` + assess + research-pack v74 on the new box; measure/record actual wall-clocks for §5's runtime table (MySQL-bound steps won't scale like MC — recalc ≈ old-box time, MC ≈ 2-3×). | 1 queued night |
| P1.E | Noise-floor recalibration: one baseline config × paired seed batches at N∈{300,500,1000,2000}×{5y,22-now,2020_crash}, measure seed-to-seed DD/compound dispersion per tier. Replaces the inherited "±5-8pp DD at N=300/1.6-1.8× compound" (dating to Phase v32 on different hardware) with measured new-box values, parameterizing the §4 marginal-call rule. | 1 queued night |

### P2 — Data + instrument (the only money on the table; weeks 2-6)

| # | Action | Gate |
|---|---|---|
| P2.A | 🔒 Sharadar SEP $40 one-month grab → delisted-equity ingest → survivorship quantification (single highest-value paid unlock, unchanged from gameplan P2.1): freeze survivor-only baseline first, schema pre-check, dry-run ingest + split-ticker reconciliation (<1% or KILL), queued recompute chain, paired survivorship-discount report every deep claim must cite. P1.A/P1.C deep-screen numbers become materially more honest once this lands. | 🔒 $40; independent of everything else; weekend queue job |
| P2.B | Real-fill loop watch (P3.7, free, already live). At N≥30 fills: first verdict on the asymmetric-cost canon. Materially-worse read → pre-committed response: re-validate execution-conditional levers (70-74 overflow first) at N=500+, re-open gamma+IV per-trade gate, re-open liquidity-cascade under a grounded penalty model. | Trigger = fill count, not calendar |
| P2.C | 🔒 Polygon cancellation decision before ~Aug-6 renewal. Recommendation: cancel — the 4y panel is harvested (the model's calibration truth-set); both OSK re-reads and the M3 re-read run on own-panel data; nothing scheduled needs live Polygon. Re-subscribe ad hoc if a concrete need appears. | 🔒 saves $79/mo |
| P2.D | L3 buy stays OFF (both gates died: P2.4 FAIL 2026-07-07, P2.3 COVERAGE-BLOCKED 2026-07-10) — standing, restated so this plan can't be read as re-opening it. | — |

### P3 — December discipline + the parked-lead calendar (standing)

| When | What | Source |
|---|---|---|
| ~Oct 2026 | OSK forward_ranks read #1 (N≥60 forward days, needs P0.D live) — descriptive, non-gating | §11 PL-6(a) |
| 2026-12-15 | Pre-steps → `run_oos_eval.py` (H1-H6) → FABLE/user adjudication + re-lock decision → PL-1/2/3 on the fixed OOS slice → PL-4, PL-5 (re-lock-dependent) | PREREGISTRATION.md §0-§12 |
| ~Jan 2027 | OSK tilt re-read (eligible-N ~doubles only after the re-lock) + forward_ranks read #2 (N≥120) | §11 PL-6(b) |
| 2027-06-15 | Extension date for any single-window miss/INCONCLUSIVE/INSUFFICIENT_N deferral | §5 + §11 |

**Restraint clause (carried forward from gameplan §3):** between now and Dec-15, no in-sample
scoring churn, no new mining passes on closed axes, no holdout-cutoff motion. The December read +
live forward ledger are worth more than anything minable in-sample before then — work
ops/fidelity/data instead.

### P4 — Structure, product, hygiene (rolling; none block P0-P3)

- 🔒 P0.3 execution on green-light: ship-portfolio procedure, drift trio, temporal-refresh, H3 piecewise segment freeze (pre-registered), backend restart (backgrounded).
- MTM $295 fix: the diagnosed one-liner in `backtest_cascade._mtm_pnl` (per-call `dte` instead of run-level `NOMINAL_CAL_DTE`) — land in a quiet no-sweeps window post-migration, re-run parity harness.
- Tradability pill (`would_trade:{eligible,reason}` on `/api/stocks/all`+StockTable) — reduced scope per gameplan P4.
- Ledger freshness: NEW_LEADS header, VERSION_GUIDE snapshots, known-issues re-triage leftovers, `experiments/_holdout.py` polars-Date dtype fix, daemon-test isolation.
- Overnight equity sleeve: pre-test PASSED (2026-07-13), remains unbuilt pending a deliberate 🔒 product decision, not scheduled here.

---

## 3. Compute-truncated decisions — the full candidate list reviewed

Rule: a re-run requires a RECORDED truncation note in the original decision; closure at a pre-registered bar is not truncation.

| Decision | Original N | Re-run? | Why |
|---|---|---|---|
| Pessimism certification (P1.5) | N=300×8 ("any flip → N=500") | **YES → N=1000** | Own design named escalation; canon load-bearing |
| Deep crash screens (P1.2) | N=300 SCREEN | **YES → N=1000** | Screen precision; stays SCREEN |
| v74 whole-tail ablation | N=300×8 | **YES → N=1000 (in-sample only)** | Ship evidence below current D-tier; pre-arms Dec H5 stage-2 |
| Live-profile baselines | N=500 D-tier | **YES → N=2000 E-tier certificate** | Collapse-tail resolution (P1.A) |
| Lifecycle MC rotate policy | N=100 screen | conditional | Margin decisive (p10 3.2× worse); only the recorded n10-leg follow-up if P0.3 flips |
| P3.2 Core resweep stage C | N=300, own re-open clause | NO | Closed on evidence, not compute; re-open = supply shift or fill-cost change |
| DQT low-tier contraction | N=300×10 | NO | Sign-flip evidence; pre-registered null |
| 45-DTE probe | N=100, own kill clause | NO | "Close the DTE axis permanently" was the pre-registered kill |
| Glide path | N=100-300 | NO | Closed at screen-1 per own else-clause; re-open trigger is real-world (~$500k equity) |
| Anything in WHAT NOT TO DO | — | NO | The null wall stands; compute abundance is not evidence |

---

## 4. Compute-heuristics rewrite — N-ladder amendment (ratified into process.md)

Old-box ladder was B(N=100)→C(N=300)→D(N=500×8-12), noise floor documented at N=300 as ±5-8pp
DD/1.6-1.8× compound, N=500=ship. This amends the ladder itself through the sanctioned path
(draft→ratification→process.md edit).

- **P-1 — Screen tiers promote one rung; the ship gate does not move.** New ladder: **B=N=300** (probe/screen; old B=100 retired — at 2-3× throughput 300 paths cost what 100 did, kills the "N=100 winner reverses at N=300" churn class), **C=N=500** (drill), **D=N=500-1000** (ship gate — N=500 remains the minimum ship standard for historical-ledger comparability, run 1000 when wall-clock permits), **E=N=2000 certificate** (new, confirmatory-only).
- **P-2 — E-tier is a certificate, never a search surface.** Runs only: (i) collapse-tail resolution (collapse-sensitive ships, annual live-profile re-cert), (ii) marginal T-gate calls (|ΔDD| inside the measured N=500 noise band on ≥3 windows), (iii) inaugural/post-migration baselines. **Knob search never runs above C** — guards against compute-funded overfitting (more paths on a searched surface fits seed structure, not truth).
- **P-3 — Collapse arithmetic explicit.** 0/500⇒≤0.6% (95%), 0/1000⇒≤0.3%, 0/2000⇒≤0.15% per (window×arm) cell. Ship language uses the bound, not just the zero.
- **P-4 — Window doctrine unchanged in role; deep screens attach by default.** 5y lock primary, 22-now confirmation-only, 2020_crash in EVERY phase unchanged. New: deep-screen windows (ltcm_1998, dotcom, GFC, 2007-now) attach by default at D/E tiers (cheap now); SCREEN-not-GATE unchanged (never calibrate on survivor-only windows). 10y row mandatory-reported at D (was intermittent).
- **P-5 — Noise floors measured, not inherited.** P1.E's empirical dispersion table (per N-tier, per window, current engine/box) replaces the Phase-v32-era figures; the P-2(ii) marginal-call rule reads the measured band.
- **P-6 — Unchanged (restated so this isn't read as loosening):** DD-primary/compound-sanity, collapse=0 hard floor, paired seeds, staged ladder discipline (no stage-skipping on a strong cohort z), queue-everything, holdout lock, FLAG-teeth, the W/B/T three-stage gate structure, and a per-trade cohort z is never itself a portfolio result.

**Also proposed for process.md's "Compute resource maximization" table:** default MC job sizing on
32 threads = `--cpu 8-12` per queued job (two heavy jobs + `trader update` headroom coexist under
queue admission — raw parallelism outside the queue still banned); ReSim shard budgeting doubles
(18min/shard-arm baseline, re-measured in P1.D).

---

## 5. First-week compute program (EXECUTED — see results below; commands kept for reference)

> **EXECUTED 2026-07-29 — ALL NIGHTS READ, program complete in ONE day** (new-box MC ~10-40×
> estimates; then re-run once on the clean post-Sharadar substrate after the P2.A rebuild — full
> narrative in `experiments/newbox_rebaseline/TRACKER.md`). Results:
> - **D0 P0.B:** DIVERGENT_CLEAN_BREAK (R1 executed; 5/12 bit-equal, p_coll 0.0 both boxes; traps.md canon).
> - **N1-N3 P1.A:** clean certs = reference set — Core PASS, Sentinel PASS (collapse 0 incl deep; Core deep compound POSITIVE on honest universe); apex_n10 0/12 std; apex_live 50-63% collapse on 2022/22-now (survivorship surfaced; feeds P0.E user decision).
> - **N3 P1.E:** measured noise floor adopted into known-issues (dd std ≲0.6pp all tiers; substrate-robust).
> - **N4 P1.D:** satisfied by proxy from P2.A chain's queue-persisted timings → RUNTIME_TABLE.md (--dte ≈85% of recalc runtime; 10y 38m without, ~6.7h with).
> - **N5 P1.C-1:** NO keep-decision flips (Core certified N=1000 clean; Apex quantified-worse, no clean state to flip).
> - **N6 P1.C-2:** SCREEN PASS (Apex-held dotcom 75.5%, gfc 3.5%, 2007_now 68.1% — honest-universe quantification).
> - **N7 P1.C-3:** CONFIRMS v74 tail retirement (−8.5pp 5y DD at N=1000 clean, collapse 0/0; Dec H5 pre-armed).
> - **M3 (parallel track):** gate complete + clean re-confirm reproduces → Polygon package FINAL (cancel default stands).
> R3 RESOLVED (Sharadar purchased+ingested same day); R8 package delivered early.

Program was 8 queued jobs (D0 parity gate, N1-N3 E-tier certs for Core/Apex/Sentinel + noise-floor
measurement, N4 10y refresh chain, N5-N7 power re-runs of the three compute-truncated decisions),
all via `trader queue submit --priority high --db light --cpu 10 --restartable --window off_market`
with per-job `--dedup`/`--reason` tags and `--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`.
Exact command skeletons are archived in `experiments/newbox_rebaseline/TRACKER.md` — re-derive
against the current `queue-ops`/`run-monte-carlo` skills rather than copying stale flags from here.

**Standing weekly (verify after migration):** `TraderSkillOOSCacheRefresh` (Sat 10:00) +
forward_ranks catch-up (P0.D) + `TraderSlippageReportWeekly` + nightly backups.

---

## 6. Risk register

| # | Risk | Signal | Branch |
|---|---|---|---|
| R1 | Migration regression — MC nondeterminism across CPU/Python/BLAS (parity gate fails) | P0.B diff outside noise | Pin package versions to old-box lockfile; if irreducible, declare CLEAN BREAK: new-box baselines (P1.A) become reference, cross-box deltas never cited as evidence |
| R2 | Scheduled-task loss — backups/heartbeat/December-reminder silently dead post-migration | Heartbeat RED, or no 08:30 digest | P0.A inventory is the control; heartbeat is the detector; re-register from in-repo scripts (`install_reminder.ps1`, `install_refresh_task.ps1`, `scripts/*`) |
| R3 | Data-buy falls through (user defers Sharadar $40) | 🔒 unresolved by ~week 3 | Deep windows stay survivor-FLOOR screens (doctrine already says so); P1 numbers remain valid as floors; revisit at next budget conversation |
| R4 | December OOS nulls broadly (H1 BLOCK and/or every PL closes) | Dec-15 verdicts | Pre-registered consequences execute mechanically (freeze-ships on a decisive H1 BLOCK; axis closures permanent) — not a crisis, corpus shrinks to what's real |
| R5 | Live Apex ledger keeps underperforming its envelope | H3 segment read; watchdog | Marking-verification → mandatory engine-fidelity investigation (pre-registered, NOT a ship freeze); user's lever is P0.E/P0.3; 2x watchdog bounds the sprint |
| R6 | Real fills falsify the asymmetric-cost canon (P2.B reads materially worse) | Slippage report at N≥30 | Pre-committed: re-validate execution-conditional levers at N≥500 (overflow first), re-open liquidity-cascade under a grounded penalty model, re-run P1.5 with measured slip — the canon was always awaiting real fills |
| R7 | Compute-abundance temptation — E-tier knob searches, re-running closed axes "because it's cheap now" | Any sweep touching §3's NO rows or WHAT-NOT-TO-DO | P-2's "search never above C" + the §3 table are the control; a re-run requires a recorded truncation note or pre-registered re-open condition, never idle capacity |
| R8 | Polygon renewal bleeds ($79/mo past ~Aug 6 with no consumer) | Billing date | 🔒 P2.C decision; recommendation stands: cancel |

---

## 7. What ratification means (Sunday checklist — historical, now executed)

1. Ratify/amend the P0-P4 restack (§2) — especially P0.A as the migration gate.
2. Ratify the N-ladder amendment (§4 P-1..P-6) → landed in process.md.
3. Decide the three 🔒 items: P0.3 sprint switch, Sharadar $40 (P2.A), Polygon cancellation (P2.C).
4. Bless the first-week program (§5) — certificates, calibration, hygiene; no live-money/doctrine change.
5. Standing calendar (§3, P3 table): Oct forward_ranks, Dec-15 pre-registered read, Jan tilt re-read, 2027-06 extensions.

## 8. Post-rebaseline observation schedule (added 2026-08-02)

The §5 program is EXECUTED and the 2026-08-02 data grab is COMPLETE+VERIFIED (64 GB OPRA flat
files on B:, Sharadar round 2). The box now runs a standing observation schedule; active science is
**`experiments/flatfile_exploitation/`** (packages FF-0..FF-6: premium-model fidelity at full N,
execution-cost empirics, the P3.6 liquidity-aware cascade — the one surviving HIGH — minute-level
fidelity, expansion evidence, IV panel; restraint clause honored throughout). Bars, falsification
conditions, dedup keys live in that PROGRAM.md; PM state in its TRACKER.md — not here.

**Standing calendar (merged, authoritative):**

| When | What |
|---|---|
| 2026-08-05 | Polygon final top-up (`TraderPolygonFinalTopup`, registered 2026-08-02) — sub CANCELLED by user 2026-08-02; archive already verified complete, this captures the last fresh sessions + posts a safe-to-cancel marker |
| 2026-08-27 | Sharadar final top-up (`TraderSharadarFinalTopup`, registered 2026-08-02; lapse ~08-29) |
| Sat 10:30 weekly | forward_ranks catch-up (`TraderForwardRanksWeekly`, installed 2026-08-02 per-user; one-off #232 caught the ledger up same day) |
| ~Oct 2026 | OSK forward_ranks read #1 (descriptive, non-gating) |
| 2026-12-15 | Pre-registered OOS evaluation (H1-H6+PL pack); gamma Phase-2 one-shot; H3 is now PIECEWISE (2 segments — P0.3 switch 2026-08-02, OQ-7 executed) |
| ~Jan 2027 | OSK tilt re-read + forward_ranks read #2 |
| 2027-06-15 | Extension date for deferrals |

**Decision-queue status (updated 2026-08-02):** P0.E **APPLIED** (Option B 30-DTE n10, user
green-light — known-issues 2026-08-02 ship block; H3 segment 2 frozen per OQ-7). P2.C **RESOLVED**
(user cancelled; top-up task registered). P0.D **INSTALLED** (no elevation needed). Remaining 🔒:
cloud-copy backup credentials (P0.C residual — D: recovered, weekly full dump ran 2026-08-02, but
the offsite copy still needs an account only the user can create); later, the FF-5B
universe-expansion ship sitting (only after its evidence pack exists).

**Next-program candidates (need a design/prereg before compute):** DH-1 deep-history Stage-1 OOS
— the `legacy_oos` question (does the backbone generalize pre-2016?) is runnable WITHOUT a data
purchase from the Sharadar full pull (1998→2016, full-universe PIT, parquet-only); its 2026-05-09
RESEARCH_PLAN bars are the draft baseline. Engineering: DTE-pass optimization (85% of recalc
runtime, display-only output — bit-identical-output gate) and MySQL 64 GB buffer-pool retune
(needs a maintenance window + before/after benchmark).

---

*Authored by FABLE 2026-07-17 (overnight). Sources: gameplan.md (2026-07-13 state), known-issues.md
WHAT-NOT-TO-DO + CLOSED, data-acquisition.md, process.md, traps.md, monte-carlo/run-monte-carlo
skills, experiments/holdout_oos_2026_12/PREREGISTRATION.md §11-§12, and the six parked-lead
extraction passes. Numbers stamped "expected/estimate" were estimates — P1.D/P1.E replaced them
with measurements (see §5 results).*
