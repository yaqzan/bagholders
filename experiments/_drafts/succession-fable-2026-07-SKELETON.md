# FABLE Succession Skeleton — 2026-07

> **DRAFT — pending FABLE adjudication, do not act on this file.**
> This is a SKELETON: headers, bullet stubs, and file pointers only. No section below is final
> prose — a finale session fills each `[FILL]` in with a live re-check before anyone treats a
> bullet as a current fact. Everything dated "as of 2026-07-18" is this drafter's authoring-time
> snapshot, not a guarantee it still holds at finale time. Produced by a documentation subagent,
> read-only: no DB, no queue, no commits, no edits to any other file.

**How to use this file:** boot the finale session with `/onboard` first (per the program's own
convention), then walk sections 1→6 in order, re-verifying every pointer live before writing final
prose over a stub. Do not skip straight to section 5 (the compute program) without section 2 (the
closed-axes map) — the whole point of the closed-axes discipline is that compute abundance is not
a license to re-open a null.

---

## 1. State of the program

- **Active scoring version:** `[FILL — re-verify, do not trust a cached header]`. As of
  2026-07-18: **v74** (`f9fb7b934`, shipped 2026-06-15). Pointer: `CLAUDE.md` "Algorithm
  Versioning"; `.claude/docs/known-issues.md` CURRENT SHIP STATE header (top of file).
- **Live portfolio profile:** `[FILL — MUST re-verify via GET /api/portfolio/state -> run.profile;
  never trust a doc header or a commit date]`. As of 2026-07-13 (per
  `experiments/holdout_oos_2026_12/PREREGISTRATION.md` §1 table, itself dated): **`apex`** (the
  15-DTE risk-budget elbow), even though the coded default is Core. Pointer:
  `.claude/skills/portfolio-ops/SKILL.md`; `.claude/docs/traps.md` "`known-issues.md` CURRENT SHIP
  STATE header lags real ship history."
- **Ship-state pointer (canonical index):** `.claude/docs/known-issues.md` CURRENT SHIP STATE
  (top of file, refreshed per-ship). Full commit log: `.claude/docs/version-history.md`. Silo
  entrypoints: `.claude/docs/algorithm-version-index.md` → `algorithm_versions/VERSION_GUIDE.md`.
- **Staged-but-unapplied real-money decision:** the P0.3 sprint 30-DTE switch
  (`experiments/apex_dte_dd/SHIP_HANDOFF.md`) — evidenced, awaiting 🔒 user green-light as of
  2026-07-18. `[FILL: check whether applied by finale time. If applied, verify the H3 piecewise
  protocol fired — holdout_oos_2026_12/PREREGISTRATION.md §4 H3 "piecewise protocol" + §6 ruling
  OQ-7 — a new segment recipe/envelope must exist, and segment 1 must be truncated at the switch
  date. Do not adjudicate H3 as single-segment if a switch happened.]`
- **Masterplan ratification status:** `.claude/docs/gameplan-2026H2-DRAFT.md` is a **PROPOSAL**
  as of 2026-07-17, "user ratifies Sunday" (expected 2026-07-19), and explicitly "does NOT replace
  gameplan.md... Nothing here authorizes spending money, changing the live ledger, changing
  doctrine, or re-opening closed axes." `[FILL: has it been ratified? If yes, treat it as
  superseding gameplan.md per its own header and re-point §5/§6 below at the ratified doc's final
  section numbers if they moved. If the finale session is after 2026-07-19 and ratification status
  is still unclear, ask the user before executing anything in its §2/§5.]`
- Plan-of-record pointer (unaffected by the above until ratification): `.claude/docs/gameplan.md`
  — mission/objective function §1, current alpha-frontier verdicts §4, priority stack §5, decision
  rights §7 (see §6 below), standing anti-goals §8.

---

## 2. Closed-axes map + reopen conditions

- **Primary index:** `.claude/docs/known-issues.md` "WHAT NOT TO DO" (bottom of file). One bullet
  per closed axis; each bullet states its own reopen condition inline — read the actual bullet,
  don't paraphrase from memory. `[FILL: confirm no entries were added after 2026-07-18 before
  treating the list below as exhaustive.]`
- **July-2026 closure memos** (each a null/park at a pre-registered bar; source-of-truth file
  first, Dec-2026 OOS re-read slot second):
  - Calendar/periodic wave features — `experiments/wave_cycle_mine/FINDINGS.md` §Park
    (2026-07-16). 17/17 W-cells null; S1_vr5 path-persistence PARKED (below actionability floor
    by 0.0002 d_ev, sign-stable). Re-read = **PL-1**.
  - Peak/extension/blowoff "fakeout at the top" — `experiments/peak_fakeout/FINDINGS.md` §3
    (2026-07-15). 0/131 cells; TEXTURE near-miss PARKED. Re-read = **PL-2**.
  - Trend MA-lattice, 349 cells — `experiments/trend_ma_lattice/FINDINGS.md` (2026-07-14).
    Comprehensive null; `cdays_SMA_8_21` 16-30 near-miss logged with no numeric bar at park time —
    bar retroactively SET in `PREREGISTRATION.md` §11 PL-3 (mark `[BAR SET 2026-07-17]`).
    Re-read = **PL-3**.
  - EOD fakeout-proneness proxies — `experiments/miss_regime_fakeout/VERDICT.md` (2026-07-10).
    4/4 axes null at |t_clust|<0.3, N=46-52k. ABSORPTION/CLIMAX intraday family PARKED
    (Layer-B). Re-read = **PL-4**.
  - Dealer-GEX, all forms — `experiments/gex/VERDICT.md` (2026-07-07). CLOSED PERMANENTLY, no
    Dec action beyond a moot-note (re-open only with crash-spanning index option data).
  - OSK / option-skew — `experiments/osk_validation/VERDICT.md` (cross-regime KILL, 2026-07-07)
    + `experiments/osk_tilt/DESIGN.md` / `RESULTS.md` (Stage-3 tilt PARKED). Modifier BLOCKED at
    faithful path. Forward reads ~Oct (N≥60) + ~Jan-2027 (N≥120, tilt) = **PL-6**.
  - Gamma/IV pair — `experiments/gamma_iv_phaseb/VERDICT.md` (COVERAGE-BLOCKED, 2026-07-10) →
    `experiments/iv_premium_model/VERDICT.md` (F2 PASS, adversarially verified, 2026-07-12) →
    `experiments/iv_engine_pertrade/VERDICT.md` (M1 FAIL, PARKED, 2026-07-13). Re-open = NEW DATA
    CLASS ONLY (real fills / mid-quotes / P1.4 vega-state). December M3 SL-FNR watch = **PL-5**
    (non-gating). See also this drafter's companion memo:
    `experiments/_drafts/data-buy-memo-2026-07-DRAFT.md` §1b/§2 for the dollar-cost framing of
    this axis's reopen path.
  - Component-ensemble / reweight / score-cliff-smoothing / P(win) calibration — multiple
    2026-06-24/25 closures: `experiments/regime_reweight/`, `experiments/score_fidelity/`,
    `experiments/verify_value/`. See `known-issues.md` WHAT NOT TO DO entries dated 2026-06-24/25.
  - DD-sizing-lever well — `experiments/dd_residual2_v70/FINDINGS.md` (2026-06-08). DRY after 5
    shipped levers (RXDD/SVR/MWDD/TVDD/BDIV). Remaining seams = option-pricing + model-fidelity,
    not another sizing lever.
  - `[FILL: any closures dated after 2026-07-17 not yet captured above — re-grep known-issues.md
    WHAT NOT TO DO for new entries before treating this list as current.]`
- **§11 Pre-Registration Pack (the mechanical Dec-2026 unlock spec for PL-1..PL-6):**
  `experiments/holdout_oos_2026_12/PREREGISTRATION.md` §11 (lines ~625-1255 as of authoring).
  Each PL entry cites its own source-of-truth lock file (the bullets above name them); §11 adds
  only executor scaffolding — cache mechanics, N-sufficiency handling (INSUFFICIENT_N /
  INCONCLUSIVE / BLOCKED deferral clauses), version pinning (v74 rows even if a newer version
  ships), and ordering (§11 common rules 1-8; §12 master run order). §11 never moves a threshold —
  "STOP — do not run" is the rule on any discrepancy between the pack and a source lock.
- **Reopen-condition status table:** `[FILL — build a one-row-per-axis table at finale time: axis
  | closed-at | reopen trigger | trigger status as of finale date. Do not presume any trigger has
  already fired — check each pointer live, especially PL-6's ~Oct forward_ranks read, which may
  have already happened by a Q4 finale session.]`

---

## 3. Running the program on Opus/Sonnet-class models

- **Source (full section):** `.claude/docs/process.md` "Agent/model tiering — token economy"
  (~lines 303-340). User directive dated 2026-07-16. Memory pointer: `feedback_agent_tiering.md`.
- **Role split (stub, quote the doc at finale time rather than paraphrasing further):** Fable
  (top-tier orchestrator) = architect/strategist — hypothesis selection, pre-registration design,
  statistical-core audit, verdict rendering, ship/stage decisions, user-facing conclusions — never
  delegated. Implementation — harness builds from a locked spec, broad searches, doc hydration —
  goes to Sonnet-class builders / Explore searchers.
- **Delegatable-to-cheaper-model precedents:** `experiments/trend_ma_lattice/` (~440k subagent
  tokens, ~39 min build, first-full-run green after smoke) and `experiments/peak_fakeout/`
  (~580k tokens, ~35 min), both from a Fable-authored `PREREGISTRATION.md`/`DESIGN.md`.
- **What NOT to attempt without a top-tier model — stub list, `[FILL: expand with a concrete
  near-miss example per bullet at finale time]`:**
  - Hypothesis selection / null-check triage against the §2 closed-axes map — a weaker model can
    re-mine a closed axis without recognizing it as closed.
  - Pre-registration design (bar-setting) — c.f. `holdout_oos_2026_12/PREREGISTRATION.md` §8
    (FABLE rulings OQ-1..OQ-7) and `gamma_curve_calibration/PREREGISTRATION.md` §G (adversarial
    closures) as worked examples of what this looks like done right.
  - Auditing the load-bearing statistical core (PIT loops, clustering/gating machinery) — c.f.
    `experiments/_audit_2026_07/HOSTILE_REVIEW.md` A-2 (clustered-z re-screen) as the canonical
    example of the error class a mechanical implementer misses.
  - Verdict rendering against a prereg, and any ship/stage decision.
  - Final review of `strategy_config.py` / engine wiring — a builder may DRAFT from a locked
    spec (e.g. the 13-consumer checklist), Fable audits every consumer and runs the gates.
- **Offload rules (4 headers only — quote `process.md` in full at finale time, don't restate
  here):** (1) forward the trap registry verbatim, (2) briefs must be self-contained, (3) audit
  the core not the bulk, (4) don't offload small judgment edits.
- `[FILL: check process.md's tiering section for edits made between 2026-07-16 and finale date.]`

---

## 4. December OOS execution pointer

- **Primary spec:** `experiments/holdout_oos_2026_12/PREREGISTRATION.md`. Boot order per the
  doc's own §12: `/onboard` → this file top-to-bottom (§0 first) → §11/§12 → `references.json`.
  Do not load prior experiment SUMMARY files beyond what a PL entry cites (unblinding risk).
- **§12 "December Executor Guide"** (~lines 1256-1321): master run order — (1) pre-steps
  (skill-cache rebuild, marking verification via `experiments/portfolio_engine_parity/validate.py`
  → `--marking-verified`, H3 envelope extension, piecewise-segment check) → (2) run
  `run_oos_eval.py --marking-verified` → (3) FABLE/user checkpoint on H1-H6 + the re-lock decision
  (must be a *recorded* ask-and-answer, not silent deferral) → (4) parked-lead path A (re-locked:
  PL-1/2/3 then PL-4/5) or path B (deferred: PL-1/2/3 via `HOLDOUT_DISABLE=1`, PL-4/5 stay
  PARKED) → (5) PL-6 is NOT a December item, freshness-check only → (6) write-up, one file per PL
  lead under `results/`, append-only. Who-runs-what: Sonnet/Opus OK for mechanical steps; FABLE/
  user required for H1 BLOCK adjudication, the cutoff re-lock decision, PASS-consequence
  licensing, and any amendment.
- **Eval date:** 2026-12-15 (frozen, §1). Single-window-miss extension date: 2027-06-15.
- **Hypotheses H1-H6 — pointer only, do not re-derive:** §4 (operationalized per-hypothesis), §5
  (noise-aware thresholds summary table), §8 (FABLE rulings OQ-1..OQ-7 that finalized every
  ambiguous call in the original draft). Only H1 carries freeze-ships; a marking-verified H3 DD
  breach → mandatory investigation, not a freeze; H2/H4/H5/H6 are uniformly softer
  (FLAG/extend-to-2027-06-15/review-trigger).
- **Reminder mechanism already installed:** `TraderOOSEvalDue2026` scheduled task (§10), fires
  daily 08:00 local, 2026-12-15 through ~2026-12-29, `-StartWhenAvailable`. `[FILL: verify still
  registered post any box migration — gameplan-2026H2-DRAFT.md P0.A names scheduled-task loss as
  the single highest-leverage migration-checklist risk, and R2 in its risk register names this
  exact task by name.]`
- **Companion track — gamma-curve calibration:** `experiments/gamma_curve_calibration/
  PREREGISTRATION.md` §S "Schedule, executor guide, integrity rails" (~lines 98-125). Phase 1
  (build+fit, post-migration, Sonnet-OK) → Phase 2 (2026-12-15+, one-shot OOS read, Sonnet-OK,
  FABLE/user reads the verdict) → Phase 3 (event-gated, FABLE/user only). Bars: §B (Bar A —
  daily-grain BS convexity real?; Bar B — does one fitted parameter earn its seat?). Integrity
  rails: §S numbered list (append-only attempt log, frozen-k seal, no-bypass date guard, executor
  conduct). This file never touches `holdout_oos_2026_12`'s frozen objects — the two tracks are
  independently pre-registered and run in parallel, not sequentially.
- **iv_engine_pertrade December watch item:** `experiments/iv_engine_pertrade/VERDICT.md`
  ruling 4 — re-read M3 SL-FNR when the real-SL event count ~doubles (~Dec-2026; currently N=33,
  underpowered). Cross-tracked as **PL-5** in `holdout_oos_2026_12/PREREGISTRATION.md` §11
  (non-gating, does not feed H1-H6).
- `[FILL: confirm at finale time whether 2026-12-15 has already passed / is imminent — check
  today's date before treating this whole section as "future work" vs "in progress" vs "done, go
  read the results files."]`

---

## 5. First-week compute program pointer

- **Primary spec:** `.claude/docs/gameplan-2026H2-DRAFT.md` §5 "First-week compute program"
  (Day 0 + Nights 1-7, every job as a `trader queue submit` skeleton). **STATUS: this whole
  document is a PROPOSAL pending user ratification** (§7 Sunday checklist). `[FILL: verify
  ratification status before executing ANY §5 item — see §1 above.]`
- **Sequencing override from the hostile review:** `experiments/_audit_2026_07/
  HOSTILE_REVIEW.md` §3 "Commissioned probes — ordered by information-value per compute-hour."
  Explicit recommendation (§0 executive synthesis, quoted): "run these probes BEFORE the N=2000
  E-tier certificates... certifying possibly-artifact numbers at 4× precision inverts the value
  order." `[FILL: has the user's Sunday re-ordering decision been recorded? §0's own words: "User
  re-orders Sunday; decision rights unchanged." Find that record before assuming either ordering
  is authorized.]`
- **Probes to sequence ahead of the E-tier certificates (pointer list; see `HOSTILE_REVIEW.md`
  §1 for each attack's full mechanism before running any of them):**
  - P0.B parity gate (masterplan Day 0) — unchanged, still runs first.
  - Tier 1 statics (§3): P1 barrier-cache drift sample (A-7), P2 per-bucket simulator parity
    (A-12), P3 clustered-z re-screen of the 5 DD levers (A-2), P4 parquet mixed-version audit
    (A-13), P5 bare-assess ship forensics (A-15).
  - Tier 2 wired MC pairs, 1-3h each (§3): P6 `DH_POP_SLIP` probe (A-1 — the review's own words:
    "THE keystone probe"), P7 TP-fill-pinned-to-barrier (A-9), P8 entry-realism bundle (A-11a),
    P9 same-symbol cooldown (A-11b).
  - THEN `gameplan-2026H2-DRAFT.md` §5's Night 1-7 E-tier certificate program (P1.A/P1.C/P1.D/
    P1.E) — re-sequenced behind the above per `HOSTILE_REVIEW.md` §3's closing line: "certificates
    certify whatever survives the probes, not before."
- **Already done, no fill needed:** A-0 (v74 scoring-lock gap) remediated same-night 2026-07-17,
  commit `fe129ad4a` — context only.
- `[FILL: at finale time, which Tier 1/2/3 probes have actually completed? Check
  experiments/_audit_2026_07/ and gameplan-2026H2-DRAFT.md §5's night-by-night for result
  files/completion markers — none existed as of this skeleton's authoring (2026-07-18). This
  section likely needs a from-scratch progress table by finale time, not just a status update.]`

---

## 6. Standing invariants

- **Scoring direction:** HIGH score = CALL, LOW score = PUT. Never invert. (`CLAUDE.md` top.)
- **Assessment buckets (≥70/≤30) vs signal thresholds (≥75/≤25):** distinct, don't conflate.
  (`CLAUDE.md` top.)
- **DD-primary, compound-secondary for Stage-3** — the mature/large-portfolio default view, NOT
  universal. Portfolio maturity sets the DD budget per profile (Apex = high recoverable DD
  accepted / Core = moderate / Sentinel = low). Pointer: `.claude/docs/process.md` "Risk-budget
  ethos — drawdown tolerance scales with portfolio maturity."
- **Collapse=0 is a hard floor regardless of profile/risk appetite** — `process.md`, verbatim:
  "collapse-rate = 0 is a hard floor for every profile including Apex."
- **N floors:** `[FILL — pull the CURRENT live ladder, don't assume]`. As of 2026-07-18, the
  shipped/standing floor per `known-issues.md` WHAT NOT TO DO is N≥300 minimum evidence, N=500 the
  Stage-3 ship gate, N<500 compound untrustworthy, N=150/4-window explicitly insufficient.
  `gameplan-2026H2-DRAFT.md` §4 **PROPOSES** (not yet ratified) a replacement ladder
  B=300/C=500/D=500-1000(ship floor unchanged)/E=2000(certificate-only, never a search surface) —
  verify ratification (§1 above) before citing this as the live floor.
- **Holdout lock:** `CALIBRATION_CUTOFF_DATE` in `strategy_config.py`, **2026-06-15** as of
  authoring (re-locked per memory `project_holdout_lock_disabled.md`), user-gated to move, OOS
  re-eval ≈2026-12-15 (§4 above). Never touch before the December read —
  `.claude/docs/gameplan.md` §8 standing anti-goals: "touch `CALIBRATION_CUTOFF_DATE` before the
  December read" is on the condensed null wall.
- **Queue discipline:** `CLAUDE.md` "Long-Running Compute — enqueue it, don't run it raw" (full
  section) — every heavy/long compute job via `trader queue submit`; market-hours HIGH-floor
  caveat; the harness's own `run_in_background` is NOT the queue; `trader queue wait <id>` with
  the harness background flag for cross-turn completion notification.
- **Decision rights:** `.claude/docs/gameplan.md` §7 — 🔒 user required for: live profile/ledger
  change, any purchase, collapse-budget change, holdout-lock changes. Ship gates (Stage 1 W1-W6,
  Stage 2 B1-B5, Stage 3 T1-T7) unchanged. Engine-fidelity adoptions (IV premium, gamma, vega
  state) = no version bump; acceptance question is always "does any gate DECISION flip?" December
  protocol = pre-registered, amendments documented not improvised.
- **Standing anti-goals (condensed null wall):** `.claude/docs/gameplan.md` §8 — one dense
  paragraph, pointer only, do not restate or paraphrase (paraphrase drift is exactly how a closed
  axis gets re-opened by accident).
- `[FILL: cross-check this entire section against CLAUDE.md + process.md + gameplan.md at finale
  time — invariants rarely move, but a ratified gameplan-2026H2 could formally amend the N-ladder
  (§4 of that doc) between this skeleton's authoring and the finale session.]`
