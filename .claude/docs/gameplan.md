# Trader — Forward Gameplan (authored 2026-07-06)

> **2026-07-29:** The 2026-H2 restack ([gameplan-2026H2-DRAFT.md](gameplan-2026H2-DRAFT.md))
> was **RATIFIED** — its P0-P4 stack supersedes this file's §5 where they overlap, and its
> first-week compute program is in execution (`experiments/newbox_rebaseline/`). This file
> stays the strategic layer: mission, frontier verdicts, decision rights (§7), anti-goals (§8).
> §5 below is now a historical record of the pre-2026-07-29 priority stack (mostly DONE/CLOSED).

**Audience:** every engineer/agent carrying this project forward. Read [known-issues.md](known-issues.md)
(WHAT NOT TO DO) and `.claude/skills/README.md` first if you haven't. When this doc conflicts with
machine truth (`strategy_config.py`, `portfolio_profiles.json`, `trader algorithm active`,
`GET /api/portfolio/state`), machine truth wins — update this doc.

---

## 1. Mission & objective function

**Maximize capital multiplication over time, subject to:**
- **Collapse ≈ 0 (hard constraint)** for any *held* book, verified on every MC window including
  2020-COVID. "Collapse" = account ruin in the seeded bounded-fill MC.
- **The opt-in Apex sprint is the sanctioned exception:** a first-passage stop-at-2x tool may carry a
  small, explicit, user-approved collapse budget (the live elbow accepts ~2.4%). Never the held
  default (held continuously it is negative-compound: −37.1% 5y / 79.6% DD).
- **Drawdown is a managed budget, not a target.** 5y WorstDD is the primary ship metric; Core's
  frontier point is ~62% DD at +1,248% 5y. DD reduction that costs compounding must be a Pareto or
  targeted-selectivity win (the c04-over-c08 doctrine), never uniform shrink.
- **Honesty is a constraint, not a virtue-signal:** look-ahead-free features, survivorship-corrected
  windows where possible, asymmetric execution costs, N=500 gates. An edge that only exists in a
  contaminated measurement is a liability with extra steps.

**Capital-lifecycle framing:** small account → Apex sprint (stop-at-2x, manual today) → Core (held
compounder) → Sentinel (preservation at scale). Structure work should make this lifecycle
*mechanical* rather than manual (see P3).

## 2. Where we are (2026-07 snapshot, refreshed 2026-07-13 in §2b)

- **Scoring: v74 LEAN** (`f9fb7b934`), endpoint of the June-2026 verification arc: honest
  (look-ahead-free since v69), integrity-audited (v71), fakeout-tamed (v72), dampener-pruned on
  per-member EV attribution (v73/v74). The score's funded value is the **70/75-gate membership**;
  the gradient above the gate is per-trade-inert on every tested axis. **Score.overall re-shaping is
  a closed frontier** (§4).
- **Portfolio: 6-lever DD stack live** (F3F, RXDD, SVR, MWDD, TVDD, BDIV + SPREAD_TILT sizing tilt),
  calls-only, 30-DTE HOLD core, dead-hold as free collapse insurance, asymmetric-cost canon. The
  5th-sizing-lever well is **dry** (documented).
- **Live money:** live PortfolioRun ran **Apex** (verified 2026-07-06, manual switch ~2026-06-22),
  the 15-DTE elbow (TP+30/SL−85/90% gross/4 names/hold 13, ~2.4% collapse budget). 2026-06-30
  `experiments/apex_dte_dd/` N=500 showed 30-DTE strictly dominates that elbow (compound +4%→+50%,
  DD 88%→82%, collapse 1.3%→0%, P(2x) 57%→72%) — staged awaiting green-light.
  **[SUPERSEDED twice: applied 2026-08-02 as P0.3 (30-DTE n10 flat-10%); then 2026-08-10
  `experiments/tpsl_refine_2026_08/` retuned the barriers — Apex TP+10/SL−60 pinned (P(2x)
  61.3%→99.4%), Core TP+10/SL−100 scalp-and-dead-hold (5y WorstDD −12.4pp). Current canon:
  known-issues.md CURRENT SHIP STATE + strategy_config.py — treat the elbow numbers as history.]**
- **Deep history:** v74 scores+breadth+regime backfilled to **1995-01-03** (dot-com+GFC eras),
  survivor-only.
- **Data gaps:** historical options/IV depth (~1.4y in `option_prices`, Feb-2025→now) and
  delisted-equity history. Phased de-risk plan: [data-acquisition.md](data-acquisition.md).
- **Verification substrate:** predictand barrier + skill-vs-baseline 0d gate built; score is a
  FLAG-grade "risk-shaper" vs momentum (accepted profile — never auto-revert on FLAG). Holdout
  re-locked at `CALIBRATION_CUTOFF_DATE=2026-06-15`; OOS re-eval ≈ 2026-12-15.

## 2b. Where we are — 2026-07-13 refresh (supersedes stale lines in §2)

- Ops layer HARDENED: backups+restore drill+DR doc, heartbeat alerting, daemon on fixed code, fill
  race closed, kill-switch+runbook, real-fill loop, 2x watchdog. §2's "no backups/pre-fix daemon/
  uncommitted edits/fill race" lines are OBSOLETE.
- Options/IV instrument arc RESOLVED at $79 total: the explosion was an error-cancellation artifact
  (proven, adversarially verified); gamma+IV strictly coupled; pair PARKED A/B-only after the
  per-trade adoption gate failed M1 (calm-name premium level; panel-IV vs real-contract gap). Re-open
  = new data class only (P3.7 real fills / mid-quotes / P1.4). P2.5 L3 buy OFF permanently at current
  evidence. §3's "only unshipped edge is OSK" is OBSOLETE (OSK modifier BLOCKED, tilt PARKED to ~Oct).
- 2026-12-15 OOS evaluation is PRE-REGISTERED and mechanically enforced
  (`experiments/holdout_oos_2026_12/`); carries the accumulated watch items (M3 SL-FNR, Layer-B
  fakeout unlock, OSK re-reads, H1-H6).
- Live-money interim flag: the live Apex-15DTE ledger printed −39.7% (2026-06-01..07-10) vs its own
  frozen MC envelope p05 of −19.4% (NON-ADJUDICATED per prereg; marking-integrity check required
  first). Staged 30-DTE switch (P0.3, user-locked) had its N=500 evidence run in flight (task 610).

## 3. The strategic read

1. **The cheapest large wins are operational/infrastructural** — protecting irreplaceable data,
   correcting the live config to the already-proven 30-DTE sprint, fixing the instrument
   (IV-aware premium + gamma) every future gate reads through.
2. **The only confirmed unshipped per-trade edge was option-implied (OSK skew)** — everything
   price/breadth/regime-shaped is a documented null. (Since resolved FAIL — see §4.)
3. **The December OOS read + the live forward ledger are worth more than anything mineable in-sample
   before then** — spend the window on data unblocks, instrument fidelity, structure; pre-register
   December now; don't churn the system into unreadability before it.

## 4. The alpha frontier (honest class verdicts)

| Class | Verdict | Note |
|---|---|---|
| Option-IV/skew selection+sizing (**OSK**) | **RESOLVED 2026-07-08 — FAILED, modifier form BLOCKED** | Polygon 4yr panel: in-era edge replicates cross-vendor (rho+0.090,t3.4) but backward-OOS 2022-24 univariate ABSENT (2022 bear −0.107) ⇒ regime-conditional; L3 buy killed per own FAIL clause. v75 score-modifier BLOCKED (WR15 null; pnl15 sign-reversed t−2.19; not recipe-robust). Stage-3 alloc tilt PARKED (`experiments/osk_tilt/`: lag1 variants DD/compound-clean but best t_clust 1.06 vs bar 2, eligible-N wall ~69; re-read ~2026-10 when N≈2x). |
| Gamma-aware engine + IV premium | **ACCEPT — #2 (coupled)** | Not alpha — the instrument. Built + real-price-validated, ships only jointly with real-IV premium (gamma alone = +1754% explosion, error-cancellation unmasking). 2026-07-10: raw-panel premium COVERAGE-BLOCKED (19.3% dose). 2026-07-12: calibrated-model form (F2) PASSED MC A/B at 100% dose, adversarially verified — explosion collapses; IV-alone as catastrophic as gamma-alone; pair strictly coupled. 2026-07-13: per-trade adoption gate FAILED M1 → pair PARKED default-OFF, A/B-only. Root cause is a DATA gap (panel BS-IV vs real contract premiums disagree on calm names), not the form — model remains the better PATH (d10 t−2.4, d15 ex-earnings t−3.8). Re-open on new data class only: P3.7 real fills, a mid-quote source, or P1.4 vega-state. |
| Survivorship fix + deep crash windows | **ACCEPT — #3** | Hardens collapse≈0 where it's soft. ~$40 + queue time. Deep windows are SCREEN, not gate. |
| Sprint DTE 45/60 arm | ACCEPT — cheap, since CLOSED (see P3.3) | 45-DTE beat 30-DTE on zero axes — axis closed permanently. |
| Equity-milestone glide path (N2) | ACCEPT — since CLOSED (see P3.4) | Every glide cell was a tie vs static comparator — closed at screen-1. |
| Overnight equity sleeve | REFINE — pre-test PASSED 2026-07-13 | Co-clustering test cleared: sleeve loss-rate LOWER on acute book-DD days (36.8% vs 45.1%, z−2.78, clustered CI excludes 0, holdout-clean). No correlated-loss objection; unbuilt pending a deliberate product decision. |
| Put-side revival | DEAD standalone | Only sanctioned path: skew-selected sliver inside OSK, after call-side validates, with stress-clustering test. Low expectation. |
| MWDD both-flat residual | BACKLOG | Thin cell (N=223); piggyback only on a future MWDD retune. |
| Earnings-cycle scoring | DEAD | Retired v74 (N=77 noise). Residual lives only in IV-crush exit repricing. |
| Any `Score.overall` re-shaping/reweighting/normalization/calibration/cliff-smoothing | **DEAD** | Multi-layer closure 2026-06-24/25. Proposing this is the canonical failure mode. |
| New non-option inputs (short interest, sentiment, …) | HOLD | Same vol-path wall. N3 RESOLVED 2026-07-14 — sequencing gate lifted; revisit only via cheap cohort-z on the apex barrier if a new non-option source surfaces. |
| v74 residual-fakeout supply-quality guards | **CLOSED NULL 2026-07-10** | Residual 0.26-0.35% ≪5% floor; EOD proxy mining all \|t_clust\|<0.3 at N=46-52k. Layer-B intraday re-read PARKED to the Dec-2026 OOS unlock. |

## 5. Priority stack (historical — superseded by gameplan-2026H2-DRAFT.md §5 where overlapping)

Effort keys: [S] hours, [M] a day-ish, [L] multi-day. Compute always via `trader queue submit`. Anything
touching the live ledger/profile or spending money requires explicit user green-light (🔒).

### P0 — Protect what exists — ALL DONE 2026-07-13
- **P0.1** backups+restore drill (`3ba9faab5`): nightly TraderBackupDaily 03:00, first backup 1.3GB/68min, restore verified. `config.py` (MySQL creds, outside repo) still user-owned, not backed up.
- **P0.2** fill-race fix (`1a5655b21`): fill gate now data-driven (waits for finalized scores), not wall-clock 16:00 — fixes the ADUR 2026-06-10 incident (16:11 fill on a 79 that finalized at 38).
- **P0.3** 🔒 30-DTE sprint switch decision — evidence landed (task 610): Option B (n10) DD-better 12/12 windows vs the live elbow, 10y +1,403% vs +327%, collapse 0/12. Applied 2026-08-02, later retuned 2026-08-10 (see §2).
- **P0.4** queue-daemon preemption fix (`2b6ea24d5`): market-hours HIGH→NORMAL guard live, 58/59 tests pass.
- **P0.5** ops heartbeat (`6c6d3baff`): TraderOpsHeartbeat every 30min, push on RED, 08:30 digest.
- **P0.6** uncommitted work committed, scoped by workstream (`5c619db2c`, `2b6ea24d5`, `5b831b1df`). Never `git add -A` (sweeps ~190 sweep artifacts).

### P1 — Fix the instrument & windows — ALL DONE/CLOSED 2026-07-13/14
- **P1.1** coverage-floor fix + crash test landed: `stress_windows_{core,apex,sentinel}.json` populated — dotcom worst regime (Apex −84.1%/Core −41.8%/Sentinel +74.4%), GFC Core +7.6% (survivorship flag stands), 31y Sentinel≫Core≫Apex.
- **P1.2** `MC_WINDOW_SET=deep` opt-in screen tier wired, doctrine codified: Core deep-PASS (collapse=0 all 4 deep windows); Apex HELD-form deep-FAIL (dot-com 100% collapse, GFC 20.7%, 19y-held 48.3% — resolves to the documented capital-velocity/stop-at-2x mechanism, watchdog=mitigation). SCREEN not GATE — never calibrate/tune/gate on survivor-only deep windows.
- **P1.3** gamma+IV Phase A — subsumed into P2.3 (see §4 gamma row).
- **P1.4** CLOSED → CALIBRATION-BLOCKED: own-panel ATM-IV~VIX fit is r²≈0.001 with a wrong-signed VIX coefficient (no stress era in the 1.4y panel) — A/B tested a near-null transform, |ΔDD|>3pp escalation never exercised. Re-open needs crash-spanning IV data or an organic VIX>40 episode.
- **P1.5** execution-pessimism certification (`ab2726a3c`, task 607): Core CERTIFIED — collapse=0 under every arm incl combined-pessimist, no keep-decision flips (combined pessimism costs ~+10pp DD on 5y/22-now, next-open-dominated, arbitrated by P3.7). Apex: collapse budget is execution-conditional and quantified (TP-miss-7% alone → 11% collapse 22-now; combined → 26-39% long-window).
- **P1.6** Dec-2026 OOS pre-registration landed (`experiments/holdout_oos_2026_12/`): v74 hash + cutoff 2026-06-15 frozen, H1-H6 operationalized, H3 envelope frozen, marking-verification enforced, Task Scheduler reminder installed.
- **P1.7** N-floor table recalibrated vs v74 (task 611, `summary_v2_v74.json`, N=2,496 closed trades, 2020-01→2026-07) — floors remain REPORT-ONLY per ship-gate reform.
- **P1.8** component-skill drift panel (`c6d6fcf9b`): `components_scorecard.json` emits at pack build, read-only diagnostic.

### P2 — The data unblock chain — resolved (see §4); worst-case spend was ≈$120, actual spend $79
- **P2.1** 🔒 $30-40 Sharadar SEP delisted-equity ingest — not yet spent (see 6b pending list).
- **P2.2** 🔒 $79 Polygon options month — spent; enabled P2.3/P2.4.
- **P2.3** Gamma×IV Phase B — DONE 2026-07-10 → COVERAGE-BLOCKED (gamma leg does not reopen; IV dose only 19.3%/10.7% on the decisive 2022-08 window vs the 60% bar). Model-form follow-up 2026-07-12 → PASS, adversarially verified (F2 collapses the explosion at 100% dose) — adoption NOT licensed, gated on per-trade validation vs realized P&L.
- **P2.4** OSK 4yr re-validation — DONE 2026-07-07 → FAIL (Polygon depth reaches 2022-08 only, no 2021; 2022 read negative −0.107 N=569; backward-OOS pooled rho −0.002). Lead closed; L3 buy OFF for OSK.
- **P2.5** 🔒 ~$2,035 L3 buy — RESOLVED OFF 2026-07-10 (both gates dead: P2.4 FAIL, P2.3 COVERAGE-BLOCKED). No purchase at current evidence.

### P3 — Structure: make the lifecycle mechanical
- **P3.1** auto-rotate-at-2x, staged: Phase 1 DONE (`0b6b778e0`, watchdog: persist sprint_start_equity, halt-new-entries at ≥2×, exits stay live). **Phase 2 lifecycle MC READ 2026-07-14**: rotate policy NOT Pareto vs Core-only (p10 3.2x worse, DD 91.7 vs 71.9); ladder collapse-breach 1.95% → Phase 3 wiring NOT licensed.
- **P3.2** Core tier/overflow re-sweep on v74's deflated supply — Stage-B (task 612) survivors {mp12, mp16} → **Stage-C NULL 2026-07-14**: neither clears the shipped control outside N=300 noise → CLOSED, shipped Core = frontier.
- **P3.3** 45-DTE sprint probe — **CLOSED 2026-07-14**: beat 30-DTE on zero axes; DTE axis closed permanently (re-open only if the IV-premium engine ships).
- **P3.4** N2 equity-milestone glide path — **CLOSED 2026-07-14 at screen-1**: every glide cell tied its static comparator (medians ±0.1-1.1%); transition rates 0-19% at realistic capital. Re-open trigger: live equity approaching ~$500k.
- **P3.5** Sentinel v74 health check — **DONE 2026-07-13**: drift trigger fired favorably (DD deltas −5..−29pp vs stale pre-DD-lever baseline; Sentinel inherits all 6 levers). N=300 confirm deferred until actually needed.
- **P3.6** Liquidity-aware cascade — **CLOSED 2026-07-14 → ENGINE-BLOCKED**: N=500 evidence showed the apparent compound win was a CONCENTRATION ARTIFACT (random-drop control reproduces it, gain scales with MaxPos headroom not trade quality, selection t−0.74) — the fill-realism fee is unobservable without a real spread/illiquidity penalty. Re-open = P3.7 real fills (N≥30) → grounded penalty model → re-run.
- **P3.7** Real-fill logging — **DONE 2026-07-13** (`4c8d462ef`): `portfolio_fills` table + `trader portfolio record-fill` + weekly slippage report, report-only until N≥30.
- **P3.8** Kill-switch + incident runbook — **DONE 2026-07-13** (`2ca1b4de6`): `trader portfolio pause|resume` + `.claude/docs/incident-runbook.md`, verified live.

### P4 — Docs & hygiene (rolling)
- Re-triage known-issues OPEN WORK (evidence-cited closures): CLOSE #0b, #1 EVR-1, #3, #4, #5, #6, #16 (dormant). KEEP #10 (→P3.6), #0, #11 (→P1.6). ADD explicit entries: fill race (→P0.2), daemon restart (→P0.4), MTM divergence, N-floor recal.
- Close the 15-DTE honest-calendar port as OBE (per-call tenor refactor delivered it; sole consumer post-P0.3 is the frozen 1/day router).
- MTM $295 curve divergence: diagnose (suspect `pending_requal` marking), display-layer fix or documented semantics — don't refactor marking for $295.
- Tradability indicator (reduced scope): `would_trade:{eligible,reason}` on `/api/stocks/all` reusing the engine's pending dry-run; StockTable pill (8 invisible sizing levers now shape alloc below what the score badge implies).
- Ledger freshness: NEW_LEADS header/baseline refresh, `trader algorithm document-snapshots`, `ladder_vs_core` FINDINGS.md completion (folds into P3.1), known-issues ship-state header refresh.
- Overnight-equity-sleeve pre-test (§4): half-day read-only; any kill → WHAT-NOT-TO-DO entry.

## 6. Sequencing at a glance (as originally planned — see 6b for actual landings)

```
Week 0   P0.1 backups → P0.4 daemon → P0.2 fill race → P0.5 heartbeat → P0.6 commits
         🔒 P0.3 sprint 30-DTE decision (user)
Weeks 1-2  P1.1 coverage fix + crash test → P1.2 deep MC screen + doctrine
           P1.3 gamma+IV Phase A (free)  → P1.4 vega state → P1.5 pessimism cert
           P1.6 OOS pre-registration (BEFORE further churn)   P1.7 N-floor   P1.8 skill panel
Weeks 2-6  🔒 P2.1 Sharadar $40 (weekend queue)   🔒 P2.2 Polygon $79 → P2.3 gamma B / P2.4 OSK
           → 🔒 P2.5 L3 $2k iff a gate passes → OSK Stage-3 + gamma ship candidacy
Weeks 3-6  P3.1 watchdog + lifecycle MC → P3.2 Core re-sweep → P3.3/P3.4/P3.5
           P3.6 liquidity cascade   P3.7 fill loop   P3.8 kill-switch      P4 rolling
2026-12-15 Run the pre-registered OOS evaluation exactly as written.
```

Parallelism: P2.1 recompute is queue-heavy (weekend); P2.2 is network-bound (concurrent); P3 MCs
are queued nights interleaved with P1/P2. The queue's priority tiers + off_market windows arbitrate
— never raw compute.

## 6b. In-flight + dispositions (2026-07-13, all now landed)

| Task | Name | Disposition |
|---|---|---|
| 605 | p11_pack_rebuild | P1.1 acceptance met: dotcom/gfc/deep_1995 ready+complete. |
| 606 | deep_crash_screen | SCREEN read per doctrine: Core deep-PASS; Apex HELD-form deep-FAIL, quantified — resolves to capital-velocity/stop-at-2x mechanism. |
| 607 | pessimism_cert | Robustness matrix run; no shipped lever's keep-decision flipped under combined-pessimist. |
| 608 | vega_state_ab | \|ΔDD\|>3pp escalation never triggered — see P1.4. |
| 609 | dte45_probe | LANDED 2026-07-14: zero-axes win → DTE axis CLOSED permanently. |
| 610 | p03_evidence_t17 | LANDED 2026-07-13: Option B (n10) DD-better 12/12 vs the live elbow, 10y +1,403% vs +327%, collapse 0/12; Option A (n4) held-lens breaches T5 +12..+18pp in 2020/2020c/2021. Decision was the user's (🔒). |
| 611 | n_floor_v74 | LANDED 2026-07-14: `summary_v2_v74.json`, report-only per ship-gate reform. |
| 612 | core_resweep_b | Stage-B LANDED: shipped Core 3rd of 81, all overflow>0 families dominated → survivors {mp12,mp16} to Stage-C. **Stage-C LANDED → NULL**: neither clears the control outside N=300 noise → P3.2 CLOSED, shipped Core = frontier. |
| 623 | lifecycle_mc_screen | LANDED 2026-07-14: rotate policy NOT Pareto vs Core-only; Phase-3 wiring NOT licensed. |

**Wave-3 (deferred, not queued):** P3.1 Phase 2 lifecycle MC; P3.4 glide-path; P3.6 liquidity cascade;
P4 hygiene (known-issues re-triage, MTM $295 diagnosis, tradability pill, ledger freshness,
`experiments/_holdout.py` polars-Date dtype fix, daemon-test isolation); Sentinel N=300 baseline
refresh (trigger fired favorably, deferred).

**User-decisions pending:** P0.3 switch (evidence = task 610); Sharadar $40 (P2.1); cloud-copy
credentials; config.py backup gap; Polygon cancellation before ~Aug 6 renewal.

## 7. Decision rights & standing gates

- **User green-light required (🔒):** any live profile/ledger change (P0.3, P3.1 Phase 3, profile
  switches), any purchase (P2.1/P2.2/P2.5), any change to a collapse budget, holdout-lock changes.
- **Ship gates unchanged:** Stage-1 W1-W6 + growth gate (real supply first), Stage-2 B1-B5,
  Stage-3 T1-T7 (N=500 incl COVID, paired seeds, DD-primary, collapse=0). Deep windows are screens.
  FLAG verdicts ship with justification + named watch metric. Waiver ledger, 3 strikes.
- **Engine-fidelity adoptions** (IV premium, gamma, vega state) are portfolio-stage measurement
  changes: NO version bump, calendar-hold-style re-baseline, and the acceptance question is always
  *"does any gate DECISION flip?"* — decisions stable → re-baseline and move on; a decision flip is
  the finding.
- **December protocol:** run `experiments/holdout_oos_2026_12/run_oos_eval.py` as pre-registered.
  Amendments are documented, not improvised. A FLAG is not an emergency; a t-significant BLOCK
  triggers the pre-registered escalation (freeze ships, user decision on de-risking).

## 8. Standing anti-goals (the condensed null wall — full list in known-issues.md)

Never: re-shape/reweight/normalize/calibrate `Score.overall` or smooth component cliffs without the
dropped-rows test · mine another market-context DD-sizing lever (the well is DRY) · fund puts in any
standalone form (incl. protective/insurance puts) · re-test entry timing in any form · make the
sprint the held default or run 1-2-name concentration · disable the dead-hold / premium-stop /
hard-sell before day 15 · broaden the 15-DTE router · exceed overflow 0.040 · trust N<500 compound
or 22-now-only wins · calibrate on survivor-only deep windows · flip GAMMA_AWARE alone in production ·
buy L3 before a Polygon gate passes · mix vendor history into live `option_prices` · run heavy
compute outside the queue · touch `CALIBRATION_CUTOFF_DATE` before the December read · broker/trade
automation (alerts + manual execution is the model).

## 9. Success criteria for this plan (12-month lens)

1. **Survivability:** a box death costs ≤1 day of recovery and zero irreplaceable data (P0).
2. **Instrument trust:** gate numbers ride an IV-aware, pessimism-certified, survivorship-quantified
   engine; deep crash screens exist for every ship (P1/P2).
3. **The OSK verdict is in** — shipped through real collapse windows, or killed cleanly with the
   evidence documented (P2).
4. **The lifecycle is mechanical:** sprint → 2x watchdog → validated rotation policy; Core re-swept
   on v74; small-account canon documented (P3).
5. **December OOS read executed as pre-registered**, and the system was stable enough for it to be
   readable (P1.6 + restraint).
6. **The team operates at standard without the founding context** — via the skill library
   (`.claude/skills/README.md`), traps registry, and this gameplan staying current.
