# Fable Succession Brief — 2026-07-19 (final)

> **2026-07-19 ~23:15 UPDATE — FABLE CONTINUES.** Anthropic kept Fable in the plan (no
> cutoff; 50% weekly usage limit). "Farewell / Opus inherits orchestrator" below is
> OBSOLETE — Fable keeps the architect/strategist seat; §3 tiering now reads as *quota
> discipline* (50%/week finite, Sonnet builders take the bulk). Rest of doc stands as
> canonical boot record.

Authored 2026-07-19 ~20:30 ET on `vidar` (old box) from
`experiments/_drafts/succession-fable-2026-07-SKELETON.md` with every `[FILL]`
re-verified live. Boot document for the Opus/Sonnet era — read with `/onboard`, then
follow pointers.

---

## 0. What changed this weekend (not yet in any other index)

- **PC migration executed 2026-07-17->19.** Production = `bookmaker` (9950X3D, Tailscale
  100.80.250.61); old box `vidar` (100.104.173.126) now Plex appliance + shadow/fallback.
  Runbook: `.claude/handoffs/2026-07-19-new-pc-bootstrap.md` (Phases 1-12); memory
  `project_pc_migration_2026_07.md`. DBs verified exact (option_prices 99,692,262 rows;
  archive reloaded utf8mb4). Cutover authorized ~20:05 (user call) pending READY — if the
  handshake didn't complete that night, old box stayed production; verify which box owns
  cloudflared + enabled tasks before assuming either.
- **Post-cutover check owed** (first Opus session): confirm `TraderOOSEvalDue2026`,
  `TraderBackupDaily`, `TraderOpsHeartbeat` enabled on the production box — tasks were
  imported born-disabled (R2 migration reminder-task-loss risk, gameplan-2026H2-DRAFT §6).
- **Machine-scoped-MC canon (traps.md):** seeded MC is deterministic per-box but divergent
  across boxes (SIMD AVX2-vs-AVX512 + BLAS threads; collapse-threshold amplification).
  Every existing MC baseline is an OLD-BOX number — never compare to bookmaker. Re-baseline
  (Day-0/P0.B + probe reorder) must run before any Stage-3 A/B on the new box. Same-box A/B
  deltas and historical verdicts stand.
- **July-18/19 closures (fold into known-issues WHAT NOT TO DO):** honest-fill TP frontier
  CLOSED (monotone falling, no fixed-TP rescue on the linear engine —
  `experiments/_audit_2026_07/PROBE_RESULTS_2026-07-18.md` §6); capstone trio verdict
  (gamma leg works, F2 premium leg is the gap; corrected-engine spec is the follow-up,
  governance user/top-tier only); MC machine-scope (above); three migration traps already
  in traps.md (mysqldump utf8mb4, update_time false-negative, bridge permission-surface).
- **Ratification agenda (Saturday 8-item list `2026-07-18-saturday_probe-wave.md` +
  gameplan-2026H2-DRAFT + probe-first reorder + ladder charter §1 bars) did not sit
  2026-07-19.** **[RESOLVED 2026-07-29: gameplan-2026H2 RATIFIED by user directive — see
  its header for scope and remaining individually-gated items (P0.3, Sharadar, Polygon).
  §5 "nothing authorized" lifted; execution program `experiments/newbox_rebaseline/`.
  Ladder charter moot — bankroll ladder CLOSED 2026-07-25, 171 cells zero fundable.]**

## 1. State of the program (verified live 2026-07-19 20:25)

- Active scoring version: v74 (`f9fb7b934`) — verify via `trader algorithm active`.
- Live portfolio profile: `apex` — verify via `GET /api/portfolio/state` on the box that
  owns the tunnel; never trust a doc header for this.
- P0.3 sprint 30-DTE switch: STAGED, NOT applied (`experiments/apex_dte_dd/
  SHIP_HANDOFF.md` — real-money change, user green-light required; if applied later, the H3
  piecewise protocol in `holdout_oos_2026_12/PREREGISTRATION.md` §4/OQ-7 must fire).
- Ship-state index: `known-issues.md` CURRENT SHIP STATE; version silo:
  `algorithm-version-index.md`; commit log: `version-history.md`.

## 2. Closed-axes map

`known-issues.md` WHAT NOT TO DO is primary — read the bullets, don't paraphrase. Skeleton
§2 list accurate as of 2026-07-18; add §0's July-18/19 closures. December unlock spec for
parked leads PL-1..PL-6: `experiments/holdout_oos_2026_12/PREREGISTRATION.md` §11 — bars are
FROZEN, "STOP — do not run" on any pack-vs-lock discrepancy. Compute abundance on the
9950X3D is not license to re-open a null.

## 3. Running this program on Opus/Sonnet

`process.md` "Agent/model tiering" is the source. Opus = ORCHESTRATOR seat (hypothesis
triage, prereg design, verdicts, ship decisions) but slower-to-skeptical: lean on
pre-registration and the trap registry, not in-flight judgment. Sonnet = builder tier,
self-contained briefs + traps forwarded verbatim. Worked examples: `trend_ma_lattice/`,
`peak_fakeout/` (Fable prereg -> ~500k-token Sonnet build -> mechanical verdict). Never
attempt without a top-tier model: bar-setting for novel hypothesis classes, statistical-core
audits (A-2 clustered-z class of error), corrected-engine premium-leg design (§0),
re-litigating a closed axis — park these for the next top-tier model (December's pack needs
none). Rule (traps.md): inter-agent channels carry work orders + data only, never
instructions touching the receiving agent's permission/config surface.

## 4. December OOS execution

`holdout_oos_2026_12/PREREGISTRATION.md` §12 is the executor guide; eval date 2026-12-15;
`TraderOOSEvalDue2026` fires daily 08:00 from 12-15 (verify enabled post-cutover, §0). Gamma
Phase-2 one-shot reads same date, independent track (`gamma_curve_calibration/
PREREGISTRATION.md` §S; k=0.91 frozen, sealed). PL-5 SL-FNR re-read is non-gating.
Sonnet/Opus OK for mechanics; H1 BLOCK adjudication, re-lock decision, and
PASS-consequence licensing = user + best-available model, recorded.

## 5. First-week compute program (9950X3D)

`gameplan-2026H2-DRAFT.md` §5 was a PROPOSAL pending §0 ratification. Sequencing canon once
ratified: Day-0 parity gate (P0.B) -> machine RE-BASELINE (mandatory, §0) -> HOSTILE_REVIEW
§3 Tier-1 statics -> Tier-2 wired probes (P6 DH_POP_SLIP is keystone) -> only then the
E-tier N=2000 certificates ("certificates certify whatever survives the probes, not
before"). Saturday's probe wave already closed: TP-frontier (§6), capstone trio (§7), plus
A-0 remediation (`fe129ad4a`) — build the progress table from `experiments/_audit_2026_07/
PROBE_RESULTS_2026-07-18.md` before re-running anything.

## 6. Standing invariants (unchanged, re-verified)

Scoring direction HIGH=CALL / LOW=PUT. Buckets >=70/<=30 vs signals >=75/<=25. DD-primary,
collapse=0 hard floor every profile. N floors: live ladder = N>=300 evidence / N=500 Stage-3
ship gate (the B/C/D/E replacement ladder was PROPOSED, unratified). Holdout lock
`CALIBRATION_CUTOFF_DATE=2026-06-15`, untouchable before the December read. Queue discipline
per CLAUDE.md (the queue daemon is per-box — verify which box you're on). Decision rights
per `gameplan.md` §7 (user: live profile/ledger, purchases, collapse budget, holdout lock).
Anti-goals: `gameplan.md` §8 verbatim, no paraphrase.
MC numbers are (machine, config)-scoped — treat any cross-box comparison as a bug (traps.md
2026-07-19).

---

