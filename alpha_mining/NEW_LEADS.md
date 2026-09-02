# New Leads

Last verified: 2026-07-13.

Purpose: this is the ranked backlog of investigation spaces that still look worth traversing. Keep it ordered by expected alpha potential first, where the highest-value work is most likely to improve WR15 while preserving or expanding useful high-tier N. Stage 3 drawdown/capacity work ranks high when it changes practical fill quality, protects the active book, or materially improves cash recycling without contaminating `Score.overall`.

Current baseline to verify before using this file:

- Active scoring in this checkout: **v74 LEAN**, `ALGORITHM_VERSION=f9fb7b934` (shipped 2026-06-15).
- Coded default portfolio profile: **Core** (uncapped, held compounder). The actually-**live**
  `PortfolioRun` can differ from the coded default and from any doc's stated date — always verify via
  `GET /api/portfolio/state` -> `run.profile` before acting on "the live profile" (see
  `.claude/docs/traps.md` "`known-issues.md` CURRENT SHIP STATE header lags real ship history"; as of
  this verify the live run is `apex`, the opt-in 15-DTE risk-budget-elbow sprint — a manual switch, not
  the coded default).
- Portfolio-only profiles are tracked separately in `algorithm_versions/portfolio_profiles.json`; Sentinel/Core/Apex are not scoring `AlgorithmVersion` rows.
- New scoring work must start from the current active scoring baseline or an explicitly named scoring worktree. New portfolio work should keep `Score.overall` frozen unless the lead independently proves score-stage WR15 alpha.

## Agent Update Rule

When a run finds a new investigation area worth traversing, update this file in the same turn that you write the detailed findings artifact.

For each new or changed lead, include:

- ranking position and whether it moved up/down;
- stage: Stage 1 scoring, Stage 2 barrier, Stage 3 portfolio, UX/ops, or null-trap;
- why it may improve WR15, useful N, option capture, DD, or practical capital recycling;
- exact evidence artifacts and enough metrics to orient the next agent;
- next experiment to run;
- ship gates and stop rules.

Do not append duplicate notes. Merge new evidence into the existing lead, rerank the list, and move exhausted ideas into "Low-Priority / Do-Not-Retry Traps" with the blocker.

## Lead Ranking

> **FRONTIER REFRESH 2026-07-14** — the option/IV arc (old N3) is RESOLVED: OSK dead/parked
> (P2.4 cross-era FAIL 2026-07-07 + score-modifier BLOCKED 2026-07-08 + Stage-3 alloc-tilt PARKED
> 2026-07-10); the gamma+IV premium pair is PARKED A/B-only default-OFF (per-trade adoption gate
> failed M1, 2026-07-13); the $2,035 L3 buy is OFF permanently; VEGA_STATE is
> CALIBRATION-BLOCKED (2026-07-13). **Do not cite N3 / "option-IV unblock" as the standing next
> ship anywhere** — see the re-marked N3 and Option-Skew entries below for itemized outcomes +
> verdict-file pointers. Current live frontier: `.claude/docs/gameplan.md` sections 2b/4/6b — see
> the five entries immediately below.

### W5DTE. [UPDATED 2026-08-18 morning — EV read DONE: PASS vs exposure-matched control; tail-exit tech-only lottery edge; proxy paper tape STOOD UP] Convulsing-OTM 5-DTE weekly family

**EV verdict (experiments/w5dte_ev/, prereg-locked gate, 100-draw exposure-matched
control): PASS — FAMILY TP-5x EV +2.39%/premium-$ beats 100/100 draws (control +0.43%).**
Shape: edge is TAIL-EXIT (TP-10x +9.5% best, 100/100; TP-2x/3x NEGATIVE and worse than
control — never scalp this family low), TECH-ONLY (R1 tech +10.9%/+26.9%/+33.9% at
TP5/TP10/expiry all 100/100; non-tech R5/R6 fail controls), hold-to-expiry does NOT beat
exposure (40/100 — the selection premium is the spike, gone by settlement), YEAR-LUMPY
(2024 negative at every policy; 3/5 years positive at TP5), lottery variance (WR 21%,
median −100% → tiny-Kelly). Capacity: median hit $38.9k/day dollar vol, 39.7% below
$25k. Forward instrument: proxy-fidelity paper tape (experiments/w5dte_tape/ +
.horizon/w5dte-paper-tape/) — faithful conjuncts unobservable live (OptionPrice has no
H-L; archive dead 2026-08-05), so violence is proxied via calibrated close-to-close
moves; FIDELITY.md carries the receipts; outcomes are lower-bound proxies.

**REALISM HAIRCUT (2026-08-18 afternoon, experiments/w5dte_minute_real/, prereg FAIL):**
minute-tape re-pricing shows the touches are REAL (97.9% 1-lot-realizable; 5.5% lone
prints; family touches MORE tradeable than controls') but the thin EV cannot lose fills
— under small-clip gating (>=5min AND >=10 contracts at the level) TP-5x EV flips to
−7.9% and falls BELOW gated controls. **Surviving corner: 1-lot × TP-10x only (+7.9%
R1-gated, above all gated controls).** Exit-print capacity is the binding constraint
(entry dollar-volume was misleading); ~45% of touches are opening-30-min events.
**Practical ceiling: a single-contract lottery sleeve — knowledge value >> P&L value.
Rank accordingly (below every capacity-viable lead).** Paper tape carries an
owner-review flag: its forward evidence is meaningful only at 1-lot × TP-10x semantics.

**Next-experiment ladder (revised):** (1) clip absorption was ANSWERED by the
realizability grid (R2/R3 columns) — no separate study needed; (2) the only upgrade
path is a mechanism that relaxes exit-print capacity (e.g. scaling out across the
touch window / selling into the opening auction), which is execution research, not
signal research — park unless the December-era tape read revives interest. Stop rules
unchanged; December discipline unchanged.

**Stage:** option-surface (neither Stage 1 scoring nor a portfolio knob yet; feeds the paused
`option-surface-features` program task as a prior). **Scoring untouched; v74 orthogonal by
measurement** (leave-score-family-out dAUC -0.0005/+0.002).

**What was found** (owner-directed census, `experiments/weekly_5dte_movers/`, N=1,341,534
covered tradeable Mon/Tue entries on same-week-Friday expiries, 2022-08..2026-06-12,
prereg-locked, holdout intact): P(touch >=5x entry premium at some print before expiry) =
8.58% base; the rule family **OTM >= ~5.8% AND entry-day contract H-L range >= ~127% of close
AND (tech | call) AND non-OPEX week** hits 19.4-20.7% (lift 2.26-2.42, N 25.6k-44.3k/rule),
**HOLD** on the full mechanical grid: 5/5 years, 3x AND 10x thresholds, ex-earnings,
ex-index-ETF. Year-blocked CV AUC 0.727 (C) / 0.709 (P) vs shuffled ~0.50. Reusable negative
knowledge from the same run: the 104-col SMA/EMA ladder collapses to ONE factor and is
predictively redundant (whole-family ablation dAUC +0.007/-0.001); F7 regime/breadth features
ANTI-generalize across years on this label (removing them IMPROVES CV: calls +0.033 AUC,
top-decile lift 1.62->2.05) — treat regime conditioning as overfit-suspect in any short-DTE
model. Evidence: `experiments/weekly_5dte_movers/FINDINGS.md` + RESULTS_TABLES.md +
`B:\polygon_derived\weekly_5dte_movers\`.

**Why it may matter:** first prereg-robust discriminator found on the option surface itself
(contract geometry + entry-day tape, not underlying trend) — orthogonal to everything the ~40
closed price/volume/breadth axes mined.

**Why it is NOT ranked higher:** growth is the week's highest PRINT (owner's theoretical
frame) — touch != capture; misses are deep-OTM weeklies (loss-given-miss ~ -100%), so 20%@5x
is not obviously +EV before spreads; L3-flip law applies to any modeled EV.

**Next experiment:** real-premium EV read on rule-hits — walk actual day-aggs paths (entry
close -> best realizable exit under limit conventions vs expiry), terminal AND path P&L,
against an **exposure-matched random control** (mandatory per the option-surface-features
preconditions and the liquidity-floor lesson). Capacity screen alongside (these are ~$0.20-2
contracts; clip-size reality check before any sizing talk).

**Stop rules:** control-arm parity kills it; EV negative after asymmetric costs kills it;
anything touching post-2026-06-15 data waits for the December gate discipline.

### A-PANEL. [2026-07-25 — OSK LEG RESOLVED (kill STANDS); gamma+IV leg still OPEN] The Polygon BS-IV panel was contaminated; two KILL verdicts rested on the contaminated slices

**What was found.** `price_history.close` is split+dividend back-adjusted; option strikes are
as-traded. `experiments/data_ingest/polygon_iv_ingest.py` used the adjusted close BOTH to select the
"ATM" strike and as the spot in the Black-Scholes solve. Audit
(`experiments/polygon_real_premium/PANEL_AUDIT.md`, n=8,529): 31.4% of rows drift; 8.6% picked a
contract actually >5% ITM; on drifted rows a correctly-built ledger disagrees on strike **61.4%** of
the time with median premium ratio 1.207 (2.45x at >5% drift).

**Why it is the top lead.** The contamination is worst exactly where the kill decisions were made:
- **2022 rows: 44.5% drift >2%, 30.3% >5% ITM.** OSK (P2.4) was closed on its 2022 backward-OOS read
  (`-0.107`, N=569) — the most contaminated year in the panel.
- **Calm/dividend names are the drifted names** (symbol-level `spearman(vol, adj_factor) = -0.402`;
  9 of the top-15 drift symbols sit in the calmest vol tercile). The gamma+IV per-trade gate failed
  M1 with the diagnosis "panel BS-IV and real contract premiums systematically disagree on CALM
  names" — which is precisely what this bug produces.

**This does NOT mean OSK works or gamma+IV should ship.** It means the evidence both were killed on
is unreliable, so the verdicts are **un-adjudicated**, not reversed.

**OUTCOME — OSK leg CLOSED 2026-07-25, same night.** The panel was rebuilt clean (9,287 rows,
59,929 API calls, 416s) and the identical battery re-run via
`experiments/polygon_real_premium/osk_reread.py`, which first reproduces every published
`osk_validation` number **bit-exactly (deltas 0.000000)**. **The KILL STANDS on both panels.**
era_E1 spearman −0.1072 -> **−0.0726** (still below the −0.05 clause); backward-OOS pooled spearman
−0.0017 -> +0.0396 but its clustered t goes **+4.598 -> −0.692**, nowhere near the t>=3 bar.
Correctness check: like-for-like undrifted rows agree to d_rho <= 0.0025 / d_t <= 0.029, while
drifted rows diverge sharply (backward-OOS d_t **−2.988**) — the correction was material but did not
manufacture the kill. It also closes the dangling "unverified observation" (backward orthogonalized
skew, clustered t +4.6): on clean data that cell is **−0.692**, i.e. an artifact.
**Net: the 2026-07-07 kill and the decision not to spend ~$2,035 on L3 were correct, and are now
correct on clean data rather than by luck.** Record: `OSK_REREAD_VERDICT.md`.

**GAMMA+IV LEG RE-READ 2026-07-25 — M1 NOW MET; the pair is RE-OPENED, not adopted.**
`build_fit_model.py:64` fit the premium model with the contaminated panel's `atm_iv` as its
regression TARGET (line ~322), with clamps derived from the same corrupted quantiles (~line 415) —
and the corruption is worst on CALM/dividend names, exactly the cohort whose pathology killed M1.
Re-read (`IV_M1_REREAD.md` / adjudication `IV_M1_VERDICT.md`) reproduces every published number
first (F2 coeffs to 1e-6, R1i to 1e-7, M1=1/3, calm bias +0.32826), then on the corrected panel:
**M1 goes 1/3 -> 2/3 terciles + beats RV overall, for BOTH frozen forms.** The calm-tercile penalty
t **+3.10 -> +0.88**; calm signed bias **+0.328 -> +0.009**; R2 0.368 -> 0.649; the reverse-fold
degradation inverts (+11.8% -> −15.9%). **Coverage control is decisive:** refit on the 8,620 common
rows only, same forms/ledger, still 1/3 -> 2/3 — the flip is the LABEL, not the row set.
**Not adopted.** M2 remains split and its earnings-window cell REGRESSED (+0.571 t 2.15 -> **+0.957
t 2.61**, N=51, P1.4 vega-state scope = calibration-blocked); the d15-overall and d15 high-RV legs
stay unmet by every model; **M3 is unmeasured** (needs a `build_ledger.py` re-run, db=heavy).
Next step = complete the gate, then the standard engine-fidelity re-baseline with a
"does any gate DECISION flip?" audit. Never flip GAMMA_AWARE alone.

**Original framing (superseded above):** The M1 per-trade gate failed on the diagnosis "panel BS-IV and
real contract premiums systematically disagree on CALM names", and calm/dividend names are precisely
the highest-drift names (symbol-level rho −0.40). That re-read runs against
`experiments/iv_engine_pertrade/` (its own harness, `build_ledger.py` + bars verbatim per its VERDICT
ruling 3) and is now the remaining contaminated verdict worth re-measuring. Note the corrected panel
also fixes a SECOND, orthogonal defect that leg depends on: `pnl15` was truncate-substituted (0.7%
nulls where ~50% is honest) because the panel predates the `ebca1e1b` fix by one day.

**Action, and it is time-critical:** the Polygon subscription is slated for cancellation ~Aug 6 and
a corrected pull costs ~1 minute of API time (measured: 3,904 signals, 8,885 calls, 65.5s). Rebuild
the panel with `spot_unadj` throughout (`experiments/polygon_real_premium/rebuild_iv_panel.py`), then
re-run the specific test that killed OSK — the per-year sign table on 2022 — on clean data. Bars
unchanged from P2.4. Note Developer depth reaches only ~2022-08, so 2021 was never available and the
original verdict rested on 2022 alone.

### A-BARRIER. [NEW 2026-07-25 — MEDIUM, measurement layer] The apex15 assessment barrier is ~3.2pp EV-optimistic because it is theta-blind

First check of the assessment predictand against real traded contracts (3,339 real Polygon paths,
2022-08..2026-07). Matched, liquid, DTE 25-38, N=1,714: modeled EV **+0.0154** vs real **-0.0163**,
delta **-0.0317**, negative in all 5 covered years; label agreement 85.6%.

**Decomposed** (`trigger_vs_payoff.py`): the sigma-to-option mapping is FAITHFUL — at the +1.092σ
underlying touch the real contract's HIGH median is **1.359x** vs an assumed 1.300x (and 0.341x vs
0.300x on the down leg). The gap is **theta**: the real contract reaches -70% 27.8% of the time vs
23.2% modeled, because an option can shed 70% via decay plus a modest adverse move without the
underlying ever reaching -2.548σ. A sigma barrier on the underlying is structurally blind to that.

**Scope:** indicts Stage-1 absolute per-trade EV claims. The MC is largely insulated (it reprices
exits with theta via `option_pnl_pct`, and the live book dead-holds rather than stopping at -70%).
Bias is common-mode, so cross-version comparisons stand. Open question worth an hour: does any Stage-1
gate DECISION flip if the predictand is theta-aware?

### ❌ A-EVENT. [2026-07-25 user ask → CLOSED-NULL same night, Stage 1 + Stage 3] Event/news noise decomposition, binary checklist, ex-ante earnings sizing

Four legs, all null; full record `experiments/event_noise_decomp/FINDINGS.md`, bars pre-registered.
- **Noise constant:** jumps carry **32%** of return variance but flip only **3.4%** of win/stop/expire
  labels (`nu_flip` 0.0339). Effective-N correction widens the headline WR CI by 0.02pp. The idea is
  conceptually right and empirically negligible. EVENT vs CYCLICAL win rates are identical
  (70.08 / 70.11); only loss *severity* differs (EV +0.0055 vs +0.0241).
- **Gradient on the clean subset:** FALSIFIED. Spearman 0.0055, CR1 t **+0.40**; 2-band spread +0.91pp
  t +0.66; 3/6 year sign stability. The inertness above the gate is NOT a signal-to-noise artifact.
  Independently re-confirmed on REAL option returns (corr(overall, real_pnl_d15) = **+0.003**).
- **Binary checklist ("more checks = more confidence"):** premise INVERTED. 6-of-6 unanimity EV
  **-0.0055** vs 4-of-6 **+0.0361**. Effective ensemble size is 3.5/6, so unanimity selects a crowded
  state, not a better signal. A checklist re-expresses the gate; it does not improve it.
- **Ex-ante earnings sizing lever:** null (gap +0.0118, dd_conc 1.19). The apparent -0.3794 gap was a
  hold-duration tautology (realized exit is an outcome) — promoted to `traps.md`.
- **PARKED to Dec-2026 OOS:** `prior_earn_jump_pct` T3_high (dEV -4.38pp, CR1 t -3.33/-4.41, PIT-clean,
  5/6 years) — real, but failed 3 of 5 locked kill-tests (mechanism falsified: expiries +5.85pp, stops
  flat; effect vanishes above score 80; survives only 1 of 3 vol terciles; costs 33% of supply).

### P3.2. [LIVE 2026-07-14 — Stage 3, IN-FLIGHT] Core tier/overflow re-sweep on v74's deflated supply — the nearest actual Stage-3 ship candidate

Stage-B screen running now (task 612, `experiments/v69_portfolio_retune/retune_stageB_v74_p32.py`,
81 cells, N=100). Axes: TIER_MID {.08,.10,.12} × TIER_LOW {.03,.05,.08} × OVERFLOW {0,.02,.035} ×
MaxPos {12,14,16}; staged B(100)→C(300)→D(500×10 incl COVID), paired seeds. Licensed by the v71
"supply-density-conditional" precedent (v74 cut ≥75 supply ~38% vs v71/v73) — the retune class has
paid twice before (v71 c14: every-window DD −5..−18pp AND +35% compound; v73 c04). Ship only
Pareto / targeted-selectivity (c04-class), never uniform-shrink (c08-class); overflow hard-capped
at 0.040. Survivors advance to Stage C then D. Pointers: `.claude/docs/gameplan.md` §5 P3.2 / §6b
task 612.

### P3.1. [LIVE 2026-07-14 — Stage 3, QUEUED] Lifecycle policy MC — sprint(30-DTE)→rotate-to-Core vs Core-only vs ladder

Task 623 queued (submitted 2026-07-13, not yet run: N=100 screen comparing `core_only` vs
`sprint_rotate_core` vs `ladder_sprint_core`, pooled quarterly starts incl COVID;
`experiments/lifecycle_mc/{DESIGN.md,run_screen.py}`). Phase 2 of the auto-rotate-at-2x work
(Phase 1 — the 2x watchdog, single-fire latch + halt-new-entries — already shipped `0b6b778e0`).
Also subsumes/completes the stalled `experiments/ladder_vs_core/` analysis. The sprint's entire
value is conditional on a stop-at-2x discipline the live engine doesn't enforce, and the user is
LIVE on it — this decides where small capital should sit. Phase 3 (wire auto-rotate) is 🔒
user-gated and only follows if the policy Pareto-wins. Pointers: `.claude/docs/gameplan.md` §5
P3.1 / §6b task 623.

### P3.6. [ENGINE-BLOCKED 2026-07-14; corrected path in flight 2026-08-06] Liquidity-aware cascade (`option_volume_30d` floor)

The floor A/B RAN 2026-07-14 (the same day this entry went live) and closed **ENGINE-BLOCKED**:
the N=500 "win" was adversarially verified as a CONCENTRATION ARTIFACT — the engine prices no
illiquidity cost, so a filter can only remove names; bare floor sweeps on this engine are
banned (`experiments/liquidity_cascade/VERDICT.md`, `7ffce085`; traps.md). The fill-realism
question (95+ signals on 5-15%-spread names vs modeled ~3%) remains REAL and is being answered
the corrected way — `experiments/flatfile_exploitation/FF3_AMENDMENT.md` (2026-08-06): FF-3′
Stage A real 4y liquidity map (READ — coverage 100%, rank-validated vs the FF-1 ledger) + FF-2
trades_v1 effective-spread proxy curves (RUNNING) → a penalized-engine floor A/B with a
random-equal-count-drop control arm; prereg after the FF-2 read; any ship-candidate needs
P2.B live-fill confirmation. Do NOT re-run a bare floor sweep off this entry.

### DEC-OOS. [LIVE 2026-07-14 — pre-registered, mechanically enforced] The 2026-12-15 OOS evaluation and its watch-item bundle

Pre-registration LANDED 2026-07-13 (`experiments/holdout_oos_2026_12/{PREREGISTRATION.md,
references.json,run_oos_eval.py}`, `--selftest` green, Task Scheduler reminder installed).
Hypotheses H1 (0d-gate OOS: BLOCK→freeze ships + escalate) through H6 (per-lever drift, the only
sanctioned lever-consolidation trigger). Carries the accumulated watch items: **Layer-B fakeout
family** (ABSORPTION/CLIMAX same-day volume-blend transition, `experiments/miss_regime_fakeout/VERDICT.md`,
PARKED to this unlock), **M3 SL-FNR re-read** (`experiments/iv_engine_pertrade/VERDICT.md`,
underpowered at N=33, re-read when real-SL events ~double), and H1-H6 generally. OSK carries its
OWN separate forward_ranks re-read at ~2026-10 (`experiments/osk_tilt/RESULTS.md`) — not gated on
Dec 15, don't conflate the two dates. No amendments except pre-registered ones; a FLAG is not an
emergency, a t-significant BLOCK triggers the pre-registered escalation. Pointers:
`.claude/docs/gameplan.md` §2b / §5 P1.6 / §6b / §7.

### P2.1. [LIVE 2026-07-14 — ops/data, user-locked 🔒, still open] Sharadar SEP survivorship buy (~$30-40)

Independent of the (now-resolved) options/IV chain — do not conflate the two. 🔒 Freeze the
survivor-only baseline pack FIRST (P1.1 output, already done 2026-07-13), pre-spend-check the
export schema, dry-run ingest + adjustment reconciliation (<1% on known split tickers — KILL if
irreconcilable, never `--commit` corrupt factors), then a queued recompute chain (breadth →
regime → scoped recalc → barrier cache → pack) and a paired survivorship-discount report
(Δbreadth, Δ75+ N, ΔWR by band, ΔDD per crash window). No `ALGORITHM_VERSION` bump (input
completion, #457 precedent). Awaiting user green-light. Pointers: `.claude/docs/gameplan.md` §5
P2.1 / §6b "User-decisions pending".

> **NOTE (2026-06-03):** active scoring is now **v70 (`c70d16d22`)**; active portfolio = the **Apex/Core/Sentinel profiles refined 2026-06-03** (70-74 overflow @0.035 + dead-hold popout-15 shipped — see `known-issues.md` CURRENT SHIP STATE + `experiments/component_reweight/REGIME_KNOB_PLAN.md`). The "v60 baseline" line above is stale. Several 2026-06-03 candidates were rigorously CLOSED — do NOT re-open without a new mechanism: 15DTE-router-broadening (collapse breach), puts/<15-reserved-ledger (net-negative), hard-sell-7 (catastrophic), component/weekly/ICH reweight (null). See `known-issues.md` WHAT NOT TO DO.

### ❌ A-TML. [2026-07-13 user ask → CLOSED-NULL 2026-07-14, Stage 1] Trend MA-lattice (EMA/SMA horizons × crosses × above/below vs `Score.trend`)

User ask: "explore EMA/SMA across time horizons, cross points, above/below behaviour vs the trend score — trend is integral but narrow." Executed as the pre-built overnight study (`experiments/trend_ma_lattice/GAMEPLAN.md`): 349-cell point-in-time lattice on the funded v74 75+ ledger (N=5,854, holdout-locked, look-ahead self-tested, trend recomposition exact ±1, controls = stored trend + pct-from-EMA50/200, Jeffreys+hierarchical-pooling Bayesian layer). **COMPREHENSIVE NULL — zero cells clear the pre-registered bar; observed z≥3 count = chance (2 vs 0.9 expected).** All three PRIMARY families null: kernel-divergence (novel, Psign 0.59), cross-freshness (best cell inverted vs hypothesis: fresh crosses NEGATIVE, G27/G19 again), sub-term decomposition decisively flat (best |z| 2.3 → no dead-weight sub-term, no LEAN retune). Kernel choice funded-irrelevant. One near-miss logged for the Dec-2026 OOS re-read only: `cdays_SMA_8_21=16-30` (z 2.9 / t_ctl 3.3 / Psign 0.85 / replicates). Do NOT re-open on price-only features; see known-issues WHAT NOT TO DO + `experiments/trend_ma_lattice/FINDINGS.md`.

### ❌ A-PKF. [2026-07-15 user ask → CLOSED-NULL same night, Stage 1] Peak-fakeout discriminator ("some metric — mcap? volatility? ema? ma? — separates the buy signals at rally tops that plunge from the breakouts")

User ask: differentiate 75+ signals at the top of rallies that plunge ("fakeouts") from the breakouts. Executed as `experiments/peak_fakeout/` — the first **interaction-level** test (all priors were marginal): pre-registered 6-leg bar (date-cluster-robust z≥3 ∧ controlled t≥2.5 ∧ Psign>0.90 ∧ interaction-delta ≥2pp ∧ N≥150 ∧ mirror sign-replication), 131 cells = 12 features × 3 peak-states on the funded v74 ledger (N=5,810, reused the trend_ma_lattice substrate + its self-test discipline). **COMPREHENSIVE NULL — 0 findings.** Three structural reads: (1) the peak STATE itself ≈ base (P_high 68.8% / P_run 67.5% / P_both 67.7% vs 70.1% WR; plunge +≤1.1pp) — 3rd independent confirmation after retrace_entry; (2) **no below-BE cohort exists** — worst texture cell still wins ~65% (BE ~45%), so there is no "fakeout cohort" to filter, only the visible left tail of a profitable population (salience bias); (3) the user's spitballs individually: mcap non-monotonic 2024-flipper, realized-vol null (Psign 0.53), EMA/MA excluded (closed by A-TML), **call-side earnings proximity decisively null** (best z 1.84 — the one genuinely-open pre-run question). Near-miss cluster parked for the **Dec-2026 OOS unlock only** (locked def in FINDINGS §3): `TEXTURE := P_run ∧ (climax_day∈T3 ∨ parabolic∈T3)` — all-5-window-consistent observed signs (−4.9pp N=474 / −9.3pp N=194) but Psign 0.76/0.87, and excess losses arrive via expiry not SL. Do NOT re-open peak/extension/blowoff EOD features before the unlock; see known-issues WHAT NOT TO DO + `experiments/peak_fakeout/FINDINGS.md`.

### A-SPRINT-DTE. [2026-06-30, Stage 3] Apex fast-2x sprint = 30-DTE (STAGED); 45/60-DTE is the open arm

**RESOLVED + STAGED — do not re-test 15-vs-30.** Overnight run `experiments/apex_dte_dd/` settled the
sprint's DTE: **30-DTE strictly dominates the live 15-DTE** (4×25%: median compound +4%→+50%, worst DD
88→82%, collapse 1.3→0%, P(2x) 57→72%; only ~1 month slower; the pure-DTE control isolates it to the
premium cushion, not the stop). Stable across N=100/300/500. Staged as a 4-field
`portfolio_profiles.json` apex edit in `SHIP_HANDOFF.md` (user green-lights — it's the live real-money sprint).
- The **DD↔compound dial is the number of names**, NOT a lever: n4 (fast, 82% DD) vs **n10 (+108%
  compound AND 76% DD, Pareto-best, slower)**. Arm-2 lever re-tune NULL — stronger RXDD is a no-op (the
  VIX-20-28 band is too small a slice when fully-deployed → the level lever is saturated for the
  sprint), DD-soft-band cuts DD only ~1:1 for compound (capital-velocity, G16). Collapse-≤10%
  relaxation was a non-event (n2/n3 stay traps: −6%/+36% compound, 90%+ DD, even when *allowed*).
- **Vehicle note:** the `concentration_2x` harness runs ANY DTE via `NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS`
  env (premium+sigma+theta auto-scale by √(DTE/30)); metric = pooled monthly-roll P(2x)/days/DD/
  collapse/compound incl COVID+2022 starts — the sprint-aligned metric, no new harness needed.

**OPEN ARM (cheap, worth traversing):** the 30>>15 premium-cushion trend suggests **45/60-DTE** might
cushion gap-downs even more — but theta drag + slower velocity likely peak it near 30 (the documented
"30-DTE is the definitive primary instrument"). Next experiment: add a 45/60-DTE arm to the same harness
(`NOMINAL_CAL_DTE=45/60`), cells n4+n10, N=300, gate on DD/compound/collapse/P2x.
- **Stop rule:** if 45-DTE doesn't Pareto-beat 30-DTE (compound AND DD), stop — 30-DTE is the instrument.
- Evidence: `experiments/apex_dte_dd/{FINDINGS,SHIP_HANDOFF}.md`.

### A-MKT. [2026-06-22 → ❌ TESTED-NULL 2026-06-25, Stage 3] Market-trend (SPY) FLAT/CHOP call-alloc contraction — REDUNDANT with the shipped MWDD lever

**❌ CLOSED-NULL — the orthogonality gate (stop-rule #1) FAILED on MWDD** (`experiments/regime_call_alpha/orthogonality.py`,
2026-06-25). FLAT_chop's negative-EV is NOT a 6th orthogonal axis: its weakness lives ENTIRELY in the
McClellan-flat band where the shipped **MWDD** already fires — where MWDD is OFF (`|mcc|>22`) FLAT_chop is
ABOVE base (WR 84.2% vs 63.9% inside MWDD's band; base 75.1%). SPY-flat ≡ directionless-breadth ≡
McClellan-flat = MWDD's state; the system ALREADY does the user's "regime-aware chop contraction" (it's MWDD).
The reweight sibling (de-weight trend in bear/chop) is also tested-null (A0, `experiments/regime_reweight/`) —
so the whole regime-aware-chop idea is closed at both layers. **Residual (LOW priority, NOT a parallel lever):**
a thin SPY-flat × McClellan-flat INTERACTION — within MWDD's band SPY-flat marks a worse sub-cohort
(FLAT∩flat 63.9% < NOT-flat∩flat 74.0% that MWDD is calibrated on) → a "deepen MWDD when SPY-trend is also
flat" 2nd-order MWDD *parameter refinement* on a thin cell (N=223), G22 regression-risk, low expected DD;
not worth an overnight MC. Original lead text preserved below.

**Stage 3 portfolio.** From the user's "calls have a drift tailwind → the edge is in choppy/drawdown
windows" thesis. The beta-stripped decomposition (`experiments/regime_call_alpha/FINDINGS.md`, read-only,
N=4,699 75+ calls × SPY fwd returns) shows the 75+ call edge is strongly **market-trend-regime-dependent
and it is NOT beta** (excess-over-SPY is +1.69% in hard drawdowns vs +0.37% in flat-chop): **DRAWDOWNS are
the BEST regime** (DOWN_hard apexEV +8.98%, SPY-dd>6% +8.56%; the buy-weakness/dead-hold bounce, partly
already captured), **FLAT/CHOP is the WORST and NEGATIVE** (apexEV **−1.57%**, apexWR 0.681 vs 0.743 base).
The strategy is leveraged-momentum → it needs DIRECTION (up *or* down); flat/chop is the poison. **Within
chop NO signal-time feature discriminates the winners** (all |z|<1.5 incl the RSI-bear-robust hint) → it's
a **regime-level SIZING** effect, not a selectable one (consistent with the reweight-null).

- **Mechanism:** smooth contraction of CALL alloc when SPY trailing ~20d return ≈ 0 (flat/chop). A
  *contraction* (documented-safe direction, like RXDD/MWDD/TVDD) on the **SPY-market-trend axis** — distinct
  from RXDD(VIX level)/MWDD(McClellan)/TVDD(TRIN)/F3F(breadth level)/BDIV(SPY-near-high+breadth-rollover).
- **Why it may help DD/EV:** cuts a genuinely negative-EV regime; the user's "integrate with the market-trend
  indicator" operationalized.
- **Gates / stop rules:** (1) **orthogonality vs the 5 shipped levers** (all-levers-off slice — is FLAT_chop
  already contracted by VIX/McClellan/TRIN? G21/G23); (2) Stage-3 MC B→C→D N=500×8 incl COVID — collapse=0,
  5y WorstDD ≤ baseline +1pp, compound flat-or-up. **Do NOT** add the DOWN_hard "size-UP" arm (G16 over-
  deployment + already-captured buy-weakness). STOP if FLAT_chop is redundant with an existing lever, or the
  MC shows no incremental DD reduction.
- **Next experiment:** join VIX/McClellan/TRIN per signal → confirm FLAT_chop low-EV survives the all-levers-off
  slice; if orthogonal, clone the `regime_dd_v70` sweep harness with a SPY-trend Gaussian/ramp contraction.
- Evidence: `experiments/regime_call_alpha/{probe.py,FINDINGS.md}` + `regime_call_report.json`.
- **⚠ Scope (2026-06-23):** this lead is the SPY-trend FLAT/chop *magnitude* axis ONLY. The adjacent
  **breadth-collapse / SPY↔breadth-correlation** axis (Market-Wave decoupling, SPY-up/breadth-down
  divergence → cut calls) is **CLOSED** — already shipped as MWDD (breadth-momentum flat-band) + BDIV
  (SPY-near-high × breadth-rollover), and its literal "collapse → cut calls" core is the crash-artifact
  trap (every collapse cohort is a mean-reversion WINNER in the all-5-levers-off slice; +0.274 mpnl on
  the literal collapse_flag). Do NOT fold a breadth-divergence term into A-MKT. See
  `experiments/spy_breadth_corr_dd/FINDINGS.md`.

### A0. [NEW 2026-06-17, Stage 1] Component-ensemble verification leads — TA-suppressor v75-lean + regime-conditioned trend/rsi bear-chop defense

Forecast-verification of the 6 components on the apex payoff (`experiments/weather_components/FINDINGS.md`,
diagnostic, read-only). The ensemble is structurally back-to-front: TREND drives `overall` most (corr
+0.72) but has ZERO per-trade resolution and is regime-HARMFUL in bear/chop (2022 −2.42 / 2023 −1.81 ΔEV);
MACD+RSI are the skillful members (RSI bear-robust, +5.10/+4.35 in 2021/2022); TA is a suppressor
(multivar β −0.63, t −3.59); eff ensemble size 3.5/6; trend+macd is anti-synergistic (−4.38 in 2023).

- **TA-suppressor → v75-lean — ❌ PROBED 2026-06-17 → TESTED-NULL (dilutive reweight-trap).** The
  universe-multivariate suppressor (β −0.63) is NOT a removable component. Re-scored a 298-sym stride-3
  sample with `W_TA_BASE=0 W_TA_SLOPE=0` (queue #240/#241, `experiments/weather_components/probe_analyze.py`):
  75+ supply −11.2% AND the retired signals were HIGHER-EV than the kept book in 4/5 windows (2025 removed
  +9.74% vs kept −1.29%; pooled "accretive" is a 2024-Simpson artifact). The regression's "hold-others-fixed"
  suppressor ≠ the score's weight-removal op (which sheds genuine confirmation + cuts supply). RE-CONFIRMS
  the **reweight-null** (`project_component_reweight_null`) on the apex predictand. Residual untested (low
  priority, expect neutral): *redistribute* TA's weight to macd/rsi/bb (renorm) — squarely reweight-null
  domain. No clean Stage-3 TA-tilt either (funded TA non-monotonic). **Lead closed; do not re-run zeroing.**

The remaining Stage-1 lead is now CLOSED-NULL:
- **Regime-conditioned trend-down / rsi-up bear-chop defense — ❌ PROBED 2026-06-25 → TESTED-NULL
  (`experiments/regime_reweight/`).** Cheap read-only pre-test (2 runs, no rescore): within the 75+ gate,
  the hi-tercile-vs-regime-base apex EV across all 6 components × 5 regime classifiers shows NO component
  with a large spread CONSISTENT across the classifiers a mechanism can use (composite AND VIX). TREND (the
  motivating axis) is the FLATTEST (±0.8pp; the weather_components 2022/2023 component-EV harm does NOT
  survive to the gate via a usable classifier — only the 2023 year-label shows it, single-window). The
  noisiest cell (bb) FLIPS SIGN across composite-vs-VIX within the same regime (±5pp = the noise floor).
  Confirms the reweight-null + G26 at the gate, per-component-per-regime. **The chop weakness is a Stage-3
  SIZING problem (the OPEN A-MKT lever — SPY-flat-chop call-alloc contraction), NOT a Stage-1 reweight**
  (consistent with A-MKT's own "regime-level sizing, not selectable" note). Lead closed; do not re-run
  regime-conditioned component reweighting.
- **[Stage-3, 2026-06-17 → ❌ PROBED 2026-06-18 → tested-NULL] trend+macd-confirm × high-VIX call-alloc tilt.**
  The cheap per-trade precondition (`experiments/bearchop_trend_dd/cohort_mine.py`, no MC) killed it
  before the B→C→D. The pooled bear/chop-band signal IS real (trend≥70 in `elev|weakbrd ex-panic`:
  cohort +0.95% apex-EV vs band-rest +7.18%, z−2.17) but the wrong kind: (a) **panic≥28 INVERTS**
  (trend≥80 +3.56% vs +0.48%, z+2.45 — mean-reversion winner, G19, must not trim, and it's most of the
  high-VIX population); (b) **the per-window G26 check FAILS** — trend≥80 in the bear/chop band is worse
  in 2022 (−4.46) but BETTER in 2023-chop (+2.63) and worse in 2024-BULL (−4.41); trend&macd ≈flat in the
  actual bear 2022 (−0.13); sign-flips across windows; (c) the pooled "−5pp" is the elevated-VIX BAND
  effect (**RXDD's territory**) + a thin recent-2026 window, NOT a regime-stable, RXDD-orthogonal,
  trend-confirm-specific DD-driver. The persist-vs-crash wall (G14/G20) + reversal-trap (G26) wearing a
  trend hat; confirms the lead's own "likely a refinement, overlaps RXDD" pessimism. **Closed NULL** —
  `experiments/bearchop_trend_dd/FINDINGS.md`. Do-not-retry: a regime/trend-confirm CALL-alloc trim keyed
  on VIX/breadth bands (RXDD already owns the band; the trend-confirm sub-cohort isn't regime-separable).
  NB the per-trade evidence here also further-weakens the sibling "regime trend-down/rsi-up bear-chop
  defense" Stage-1 lead above: the trend-extended-in-bear/chop cohort is not cleanly regime-separable
  (same persist-crash wall), so a Stage-1 regime reweight faces the same obstacle + the reconfirmed
  reweight-null — pursue only with a genuinely new (non-trend-extension) discriminator.

Stop rules: abandon TA-lean if the probe shows >~10% 75+ supply loss OR apex-EV non-accretive; abandon the
regime-defense if it can't beat the bear/chop deficit without cutting bull-window supply/return. Watch:
SPREAD_TILT's per-trade premise is THIN on v74-lean (funded 75-79 spread-skill +0.14pp vs +1.76pp on v73).

### ✅ A2. [RESOLVED 2026-06-24 — NO SHIP, v74 already at the gate-vs-gradient optimum] Gate-vs-gradient score-stage parsimony audit

> **AUDIT DONE (`experiments/verify_value/GATE_AUDIT.md`, /research, <1h, config + one 25-sym rescore A/B, no recalc):** the principle is **already satisfied by v74** — there is NO funded (75+) score-stage gradient-shaper left to cut. Every one (MCD/ICH/SCW/CWCF/CSWC/continuation/WVD/daily-volume/EARN_BOOST) was retired by the v71/v73/v74 lean campaign, which retroactively pre-satisfied the principle. The funded-relevance rule that settled it: **a score-stage mechanism is funded-load-bearing only if its gate reaches ≥75 (the Apex traded threshold)** — the only ACTIVE call-side dampener, **CWWD**, is gated `[70,75)` and was empirically confirmed funded-irrelevant (rescore A/B: 0/569 75+ rows change, 75+ set byte-identical; its 186 affected rows all land in 70-74). The surviving 75+-relevant machinery is the gate-determining CORE (components/weekly/regime), which the principle says to KEEP. **Residual surface = the put-side + 70-74 hygiene cut (CWWD/CAP/EXH/EXT_FOCAL/PCD/PESS/WEEKLY_PUT) — funded-byte-identical but it changes dashboard behavior (untraded-band de-qual + put surfacing) + needs a recalc → a PRODUCT-decision STAGE, not a funded ship.** Do-not-retry as a funded cut. The lead's value was realized as confirmation that the lean ships were directionally right; the next genuine ship is the option/IV data-unblock (N3). **[CORRECTED 2026-07-14: N3 has since RESOLVED — OSK dead/parked, gamma+IV pair PARKED A/B-only, L3 buy OFF; see the re-marked N3 entry below and `.claude/docs/gameplan.md` §2b/6b for the current live frontier.]**

(original lead text, historical:)

Stage: Stage 1 scoring (subtractive — like N1). From the value/calibration verification
(`experiments/verify_value/FINDINGS.md`, read-only, in-sample): on the apex predictand the score's value is
ENTIRELY the 70-gate selection (+~1pp EV, the only t≈2 effect); the **continuous gradient above 70 is
per-trade-inert on EVERY axis — direction (within-70+ potential-BSS 0.0002), 3-outcome EV (flat), and
run-magnitude (MFE-σ ~4.0σ all bands, Spearman −0.011)**. No cheap model (components/vol/momentum) predicts
apex outcome above the gate either; calibration is a no-op (out-of-fold reliability ≈0); the lineage
ensemble (v70-v74, corr 0.68-0.89) adds stability not skill.

- **The falsifiable rule:** a score-stage mechanism earns its keep only if it is **(a) gate-acting** —
  pushes scores ACROSS 70/75 (changes *membership*, where all the value is, e.g. CWWD drifting a 73 below
  70) — or **(b) DD-validated** via tier-sizing (e.g. SPREAD_TILT, −4.1pp MC DD). A **pure-gradient-shaper**
  (re-ranks/re-magnitudes *within* 70+ without crossing a gate and with no MC effect) is, by this evidence,
  inert on the funded book → a lean candidate. Generalizes the v74-lean philosophy from "net-dilutive tail"
  to a principle.
- **Next experiment (cheap, read-only):** classify each shipped score-stage mechanism (CWWD, ext-focal/CSWC
  survivors, any residual dampener) as gate-acting vs pure-gradient via the fraction of its score-deltas that
  cross a 70/75 boundary (ScoreSimulator on/off A/B). For each pure-gradient one, confirm funded inertness
  with an N=300×8 MC smoke (DD/return unchanged) → retire if inert.
- **Gates / stop rules:** subtractive, so the bar is "no funded-book regression" (collapse=0, 5y DD/compound
  within noise). A gate-acting OR DD-validated mechanism is OUT of scope (keep). **Do NOT touch the cascade
  tiers or SPREAD_TILT.** Stop if a mechanism's gate-crossing fraction is non-trivial (not a pure-gradient
  shaper). Caveat: all in-sample / σ-barrier — the OOS read (~Dec 2026) is the real arbiter; lone watch is
  90-100 (N≈180) ticking mildly up.
- Evidence: `experiments/verify_value/{FINDINGS.md, phase1_brier_rev.py, phase2_ensemble.py, phase3_magnitude.py}`.

### ✅ A1. [RESOLVED 2026-06-18 — VALIDATE-c04, NO SHIP] v74-lean cascade retune (the "re-tune on the new substrate" follow-up)

v74's LEAN ship cut ≥75 supply ~38% (→ PRF 2.8 sig/day), and the PRF
(`derived_portfolio.json`) flagged a divergence (F: mid .10/low .051 vs live c04
mid .08/low .03). Full Stage-3 B(N=100×6)→C(N=300×8)→D(N=500×8 gate) sweep
(`experiments/v74_cascade_retune/`): **every size-up direction FAILS T4** (c04_mid10
+1.7pp 5y DD converged across N=100/300/500 = real not noise; +2.2% compound
doesn't justify it). **c04 confirmed DD-optimal on v74-lean.** Why: despite −38%
supply, recycle_coverage stays 0.975 (book saturated) + the 50% gross cap → sizing
up CONCENTRATES (G16), no compound recapture. **Lesson: the PRF "size-up"
extrapolation is falsified by the MC** — its v70/v71-fit coefficients don't hold on
a saturated/cap-bound substrate; a PRF size-up divergence at coverage≈0.97 + a
binding gross cap IS the G16 over-deployment trap. Do-not-retry the v74 cascade
retune. (PRF still useful as a seed for IDLE/denser substrates, not saturated ones.)
Record: `experiments/v74_cascade_retune/FINDINGS.md`.

### ✅ J. [RESOLVED 2026-06-11 — SHIPPED `3505c8770`, Stage 3] DD-episode-onset omens → Hindenburg NULL; BDIV pre-top breadth-divergence dampener shipped

User ask ("isolate major drawdowns, look back for Hindenburg-omen-style precursors") answered on the
fresh v71 tape (`experiments/dd_onset_omens/FINDINGS.md`): the **literal Hindenburg/omen family is
NULL/INVERTED** — 0/24 omen days preceded a major episode onset within 20d; omen-day entries are
mean-reversion WINNERS (+0.079 mpnl); Zweig/churn/NL-spike/summation-divergence all sign-flip (G26);
pre-onset markets look strong/complacent (drawdowns start at TOPS, not after warnings). The survivor:
**pre-top breadth divergence** (SPY within ~1-2% of 60d high while breadth_score drops ~5-10pts/10d)
— low-EV (−0.018 vs +0.029, z+25, conc 1.82), sign-stable incl 2024/2025, orthogonal (survives the
all-levers-off slice). Shipped as **BDIV** (5th DD lever, the FIRST leading one — no DD-gate; the
SPY-near-highs requirement is the structural crash guard). Phase D N=500×10 T1-T7 PASS: **5y WorstDD
−3.0pp AND compound +21%; dip DD 40.3→26.3 (−14.0pp) at +49%**; collapse=0 incl COVID.
**Do-not-retry trap:** Hindenburg/NH-NL/Zweig omen-state entry contraction (winner cohorts /
ortho-slice failures). **Open follow-up (chip spawned):** `portfolio_engine._cascade_entries` applies
only RXDD — SVR/MWDD/TVDD/BDIV missing from LIVE entry sizing + invisible to the strategy
fingerprint (pre-existing since 06-05); wire + re-validate bit-exact.

### H0. [HIGH — Stage 3, RETEST, harness staged 2026-06-10] HOLD-vs-CUT re-test under honest theta on the v71 substrate

Stage: Stage-3 portfolio (T1–T7), no `ALGORITHM_VERSION` bump. **Status: harness staged, NOT run** (needs compute box: MySQL + queue).

**Why it ranks high.** `HOLD ≫ CUT for calls` (wide SL −0.70 + ride to the day-N hard-sell + dead-hold) is the **foundational axiom of the entire Apex architecture**, and it was decided in `experiments/v69_portfolio_retune/` on the **theta-OPTIMISTIC, trading-bar** MC engine. The 2026-06-09 honest calendar-theta standardization (`experiments/calendar_hold/FINDINGS.md`, point 3) proved that engine **under-charged theta ~16pp on slow trades** and flagged this exact re-test: *"the HOLD edge is partly a theta-accounting artifact… re-test HOLD-vs-CUT under honest theta (CUT exits faster = less theta exposure → maybe flips the conclusion)."* It has **never been run** on the honest engine, and **never on v71** — which makes it *more* live: v71 ~doubled 75+ supply, so a CUT/fast-recycle strategy now has ~2× as many signals to redeploy into (the dimension most likely to tip HOLD→CUT, on top of the honest-theta penalty). This re-test also **gates** the downstream exit-discipline retests (trailing stops, CDR/REALLOC, per-tier hard-sell) — they only make sense once the exit regime is re-settled.

**Substrate (must be v71-honest, NOT v70):** scoring `04044b21b`; `CALENDAR_HOLD=True / HOLD_CAL_DAYS=27 / NOMINAL_CAL_DTE=30`; cascade `TIER_LOW 0.05 / overflow 0`; SL −0.70; dead-hold −0.40/−0.15; puts off; caps 0.50. `c_base` = these live defaults (zero overrides).

**Axis (existing knobs only, no engine change):** stop width `SL_BASE` (−0.70 hold ↔ −0.30..−0.50 cut), hold length `HOLD_CAL_DAYS` (27 ↔ 14..21), dead-hold ON for every shippable cell (`dh_off` is documented 100% collapse — one `probe_dh_off` cell only re-confirms that under honest theta). The genuinely new idea — a **theta-aware mid-life time-stop on the slow-bleed cohort** (exit >X% underwater & not-in-dead-hold by cal-day {14,18,21}, keeping the dead-hold for crash deferral) — is **Phase 2**, needs a small `monte_carlo.py` mechanism, gated on Phase-1 showing CUT has any life.

**Gate:** collapse=0 mandatory (hard veto, incl 2020_crash) → DD-primary mean(5y,22-now) WorstDD vs base → honest-compound guard 5y MedRet ≥ 0.8× base → Pareto preferred. B N=100×6 → C N=300×8 → D N=500×10.

**Decision/stop:** if no CUT cell beats `c_base` on DD without breaking the compound guard, **HOLD≫CUT is re-confirmed on honest v71** — close the follow-up, keep config. If a CUT cell Pareto-wins, escalate to C/D + the Phase-2 time-stop.

**Artifacts:** `experiments/holdcut_honest_v71/{README.md,sweep.py}` (reuses `experiments/v69_portfolio_retune/driver.py`, deterministic paired seeds). Run: `trader queue submit --priority high --window off_market --db light --cpu 6 --restartable --dedup holdcut-honest-v71-B -- python experiments/holdcut_honest_v71/sweep.py --phase B --workers 6`; then `--report B`. Raw: `.cache/holdcut_honest_v71/phase_B.jsonl`.

### ✅ I. [RESOLVED 2026-06-10 — shipped as v71 `04044b21b`] 2026-06-09 scoring-integrity audit — all four defects fixed, four mechanisms retired

**Campaign executed overnight 2026-06-09/10** (handoff `experiments/integrity_audit_2026_06/AUDIT_FIX_HANDOFF.md`; full evidence `experiments/integrity_audit_2026_06/FINDINGS.md`). Verdicts via honest 5y full-universe ReSim A/B (6 arms + bundle, option-aligned barrier, validation arm reproduced stored v70 at 98.43% exact over 936,580 rows):
- **I1 wave → fixed + formally RETIRED** (`SECTOR_BREADTH_WAVE_ENABLED=False`): rebuilt-source A/B showed the wave deletes ABOVE-baseline call winners (−28% N at 75+, removals 56.4% vs shared 54.4%) — the breadth-crash-artifact trap. Loud inert/stale guards added; self-contained source rebuilder at `experiments/integrity_audit_2026_06/build_market_wave_source.py`.
- **I2 spy_wk look-ahead → FIXED** (`_spy_wk_last_completed`, −7d at all 5 call sites + simulator); on the lagged substrate **mis_stress RETIRED** (75+ admits 50.7% vs shared 55.2%, z=−1.03) and **JA4 RETIRED** (`_JA4_SPY_WK_WEIGHT=0`; wash at ≤25, z=−2.48 at ≤30; puts off portfolio-wide).
- **I3 barrier cache → REBUILT** 2016-04-26 → 2026-06-09 (83.3M rows, queue #101) + coverage guard in `_load_cont_barrier_wins`.
- **I4 static mcap → FIXED** (PIT proxy in all 4 scoring paths); on PIT mcap the MCD ladder **collapsed 8.2pp → 2.6pp (z=+2.61 < W1 bar), non-monotonic** → **MCD RETIRED** (`MCD_ENABLED=False`) — the single biggest N-recovery (75-79 +67%, 80-84 +135%).
- Net v71 bundle vs stored v70: **75+ tradable supply +49% at FLAT honest WR**, puts unchanged, gradient intact. Regression tests: `tests/test_integrity_guards.py`.

The original I-block analysis follows (historical record):

**I1. [CRITICAL — silent no-op] The v57 Sector Market Wave score transform is INERT in ALL current row sets (v60/v69/v70).**
Stage: correctness / Stage 1 re-validation. `SECTOR_BREADTH_WAVE_ENABLED=True` and every doc lists it in the active stack, but its source file `.cache/market_wave/predictive_market_wave_v57_source.csv` (and the fallback `.cache/sector_etf_screen/sector_breadth_daily_2020plus.csv`) is MISSING from the production checkout. `sector_breadth_wave.py:_load_rows` returns `[]` on a missing path → `wave_on_or_before` → 50.0 → stress=repair=0 → no adjustment, no weight_info key, no warning. DB verification: **zero** rows carry `sector_breadth_wave` in weight_info across v60/v69/v70 on deep-stress dates (2025-04-08 tariff selloff, 2022-06-13 bear, 2020-03-16 COVID) AND bull-repair dates (2026-02-10) — the mechanism never fired in the entire honest-era row set (all three versions were recalced June 2026, after the CSV vanished). Consequences: (a) the honest v70 backtests + Apex/RXDD/SVR/MWDD/TVDD sweeps are internally consistent but ran WAVE-LESS — the docs' mechanism inventory is wrong; (b) a believed-active crash-protection score mechanism is dead; (c) re-adding the CSV now would silently CHANGE live scores vs stored history (de-facto unversioned scoring change). Next experiment: rebuild the full-history wave source self-contained from SPDR prices via `market_breadth._load_sector_etf_breadth_rows()` (the MWDD rebuild already proved this path), add a LOUD missing/stale-source guard to the loader, then run an honest Stage-1 A/B (wave ON vs OFF on v70 substrate) before deciding **re-ship honestly vs retire formally**. Prior: the v57 calibration was on pre-v69 look-ahead scores, and MWDD's mining showed breadth-crash cohorts are mean-reversion WINNERS — a real chance the honest verdict is retire. Either outcome ends the doc/code drift. Gates: W1-W6 if re-shipped (it's a `Score.overall` change → version bump + recalc).

**I2. [HIGH — live look-ahead leak, v69 class] `mis_stress` (CALL-side) + JA4 `put_regime_multiplier` read the SPY weekly composite with current-week look-ahead.**
Stage: Stage 1 leak fix. `core.py:_load_spy_wk_composite_map` + `_spy_wk_on_or_before(target=score_date)` forward-fill SPY `WeeklyScore` rows, which are Monday-keyed and (in recalc/backfill) hold the COMPLETE Mon-Fri week — so a Mon-Thu historical signal reads a spy_wk containing future bars (the exact v69 weekly look-ahead class, missed by the v69 fix because it lives in core.py regime helpers, not the per-stock weekly path). Leak direction: the mis-stress softener (`_load_mis_stress_map`, gap = spy_wk − composite ≥ 0, full at 30) relaxes call regime-compression preferentially in weeks that END strong → call admission tilted toward future-strong weeks → WR inflation on the affected cohort (~162 full-mis-stress days + partials; at mult 0.78 a full softener moves an 80-call ~+1.7pts — enough to cross the 75 gate). JA4 mirrors on puts (25% spy_wk blend → puts suppressed in future-strong weeks). Live reads the PARTIAL current week → live-vs-recalc divergence (a fakeout channel too). Fix: look up at `date − 7d` (the same last-completed-week convention `build_kijun_pct_map` uses) or a weekly_pit-style blend; then honestly re-derive `MIS_STRESS_FULL`/`MIS_STRESS_CALL_DAMPEN` and the JA4 weight — both were calibrated pre-honest on the 2026-04-09 composite-inversion thesis, so the honest re-test may simply retire both (a simplification win). Score-stage change → version bump + recalc; cheap to pre-flight via ScoreSimulator A/B (lagged vs current spy_wk).

**I3. [HIGH — ops landmine] `barrier_outcomes` cache truncated to 117 dates (2025-12-19 → 2026-06-08); next full recalc will write cont-echo-inconsistent history.**
Stage: ops / data integrity. The cache was wiped and re-seeded by the nightly `refresh_recent(days=160)` (likely during the 2026-06-08/09 calendar-hold engine work) — full multi-year coverage is gone (was ~4.2M rows/set; now 1.44M/set). The continuation echo (`core.py:_load_cont_barrier_wins`) returns `{}` outside coverage → `cont_lift` silently zero for any recalc date before 2025-12-19 → a future `trader recalculate --force --full` writes rows whose continuation behavior differs by era with no error. Also slows assess (cache-miss → full forward walks). Fix BEFORE the next recalc/scoring ship: re-run the full `database.barrier_cache` backfill (`backfill_sets`) off-hours via the queue, and add a coverage guard to `_load_cont_barrier_wins` (warn when `lo_date < min(cache.date)`).

**I4. [MEDIUM — leak/survivorship] MCD uses TODAY's `Stock.market_cap` (yfinance `info['marketCap']`) for ALL historical dates.**
Stage: Stage 1 re-validation. Static current mcap applied to 5-10y of history tilts the calibration: stocks that GREW into large caps (whose historical small-cap signals disproportionately WON) escape historical dampening, while shrinkers get dampened — so part of the 8.2pp mcap↔TP gradient and the +2.73pp ship evidence is survivorship artifact, and MCD's −44.9% N cost on 75+ may be partly unjustified. Fix: point-in-time mcap proxy `mcap_t ≈ mcap_today × close_t / close_today` (split-consistent with adjusted closes; ignores share-count drift — still far better than static), re-run the mcap cohort z + recalibrate-or-retire MCD. Pairs with the N-recovery lead below (Lead N1).

**Cleared (retire these suspicions):** (a) the known-issues note "ICH `kijun_pct` + WVD `wv_force1` likely share a residual weekly look-ahead" is FALSE — both builders use a deliberate last-completed-week lookup (`date − 7d` bisect) and are wired identically in all three scoring paths (single-row / batched / recalc); verified clean. (b) Continuation-echo outcome timing is point-in-time-safe: `compute_cont_prior_signal` gates each prior window's outcome on elapsed gap (`wins.get(W) if gap >= W`), so no unresolved-window outcomes leak in.

### ✅ N1. [RESOLVED — SHIPPED as v73 (`07e9722b5`) 2026-06-12] Honest-substrate dampener-stack ablation matrix (the N-recovery lead)

> **2026-06-12 SHIP (same day):** Option B shipped — WCF + ICH + CWCF + CSWC + SCW retired
> (CWWD + WVD keep their seats). Bundle-confirm arms: 2,507 distinct 75+ signals restored
> (+77%) at 50.5% vs 54.3% shared, all above call BE. Growth gate (REAL calls-only supply):
> dG +19.6% option / +36.3% generic, every window; W4/W6 clean. MC smoke N=300×7w paired:
> collapse=0 every cell; DD +5-13pp at unchanged Apex params = the v71 signature.
> **Successor lead: the Stage-3 sizing retune on the v73 density** (seed from
> `portfolio_response.py --derive v73`; expect the c14 direction). Ship record:
> `experiments/dampener_ablation_v72/FINDINGS.md` "v73 SHIP ADJUDICATION".

> **2026-06-12 resolution (/research, `experiments/dampener_ablation_v72/`):** all 7 remaining
> ablations executed on the v72 substrate (8-arm sharded ReSim, full universe 5y, baseline
> 98.54% exact vs stored over 940k rows; option-barrier delta-cohort verdicts). **WCF + ICH =
> clear RETIRE** (WCF deletes ~85% of the put band with ZERO discrimination, z=−0.43 — the v27
> evidence was look-ahead artifact; ICH is INERT on calls — ≥75 removes N=1/5y, its kijun<0
> high-call founding cohort vanished post-honest-weekly — and wrong-way on puts, deleting
> 45.0%-WR puts vs 41.7% shared). **CWCF/CSWC/SCW = marginal retire-leaning** (removals
> 50.3-51.2% ≫ BE 45 but ~3-4pp below shared 54.3, |z| 1.6-2.0; trio union = +61% lower-bound
> 75+ supply at 50.7% pooled — a hydration-vs-mix trade for the W5 growth gate). **CWWD + WVD
> = KEEP** (CWWD z=−2.28, lands in dead 70-74; WVD's deleted ≥70 cohort 41.6% vs 51.9%,
> z=−3.32 — below BE, the audit's one clear earner). Ship options + exact procedure:
> `experiments/dampener_ablation_v72/SHIP_HANDOFF.md` (Option A honesty ship = WCF+ICH,
> ~call-neutral; Option B growth ship = A + trio, growth-gate-adjudicated, N=300×8 MC smoke
> mandatory, expect a Stage-3 density retune after — the v71→retune precedent). The remaining
> open work here is the SHIP, not analysis.

> **2026-06-10 update:** four of the ten stack members are now resolved by the v71 integrity ship — **mis_stress, JA4, MCD, wave all RETIRED** on honest A/B evidence (see resolved block I above; MCD alone returned +49% 75+ supply at flat WR). **REMAINING open ablations: WCF, CWCF, CSWC, CWWD, SCW, ICH, WVD** — same method (the `experiments/integrity_audit_2026_06/ab_eval.py` sharded ReSim harness is reusable directly: one `sc_patch` per mechanism). The harness + barrier-cache join is proven; each additional mechanism is ~20 min of compute + one delta-cohort read.

Stage: Stage 1 scoring (subtractive — removes/weakens existing mechanisms; not a new directional discriminator, so it does NOT collide with the missretest_apex15 closure, which tested ADDING discriminators to the current row set).

Why: every score-stage dampener in the active stack — WCF, CWCF, CSWC, CWWD, SCW, MCD, ICH, WVD, mis-stress, JA4 — was calibrated pre-v69 on look-ahead-contaminated scores AND pre-honest outcome labels. Three specific reasons to expect dead weight: (1) the founding evidence for CWCF/CWWD (wadj-neg miss cohort, z=+10.1) collapsed to N≈21 noise on the honest re-mine (`experiments/version_alpha_mining/honest_miss/`); (2) v69 changed the wadj feature itself (completed/partial blend) so every wadj-gated cutoff (+1, 0, [1,14), −17) is mis-anchored against a different distribution; (3) I2/I4 above show two of the inputs were leaky. The dampeners collectively cost large N (ship-time: MCD −44.9% of 75+, CWCF −10% 75+, ICH −7.7%/−14% on 95+/90+, WCF −75% of put N) — and they fire MOST in weak-weekly/bear-ish tape, which is exactly the binding low-`lambda_eff` drought window where the W5 growth gate says supply is worth the most. Honest 75+ supply is already −27% vs the look-ahead era; this is the highest-leverage place to win WR-neutral N back.

Next experiment: one-at-a-time ablation (and a few pairwise) of each dampener on the honest v70 substrate via `ScoreSimulator`/`capture_inputs` fast re-score (`experiments/component_reweight/` harness); evaluate per-discrete-bucket WR15/N on BOTH the option-aligned (apex15/opt15) and generic barriers + the W5 growth verdict (`experiments/version_scorecard/stage1_growth_gate.py` with REAL candidate supply — never the fallback). Keep mechanisms that re-earn their keep; weaken/retire the rest; recalibrate survivors' cutoffs against the blended-wadj distribution. Gates: W1-W6 with W5 SHIP; N=300×8 MC smoke if any binding tier's density shifts >30% (likely — that's the point). Outcome either way is valuable: re-validated mechanisms get honest evidence; failed ones return N.

### N2. [NEW 2026-06-09 — MEDIUM, Stage 3] Equity-milestone profile glide path (Apex→Core→Sentinel automation)

Stage: Stage 3 portfolio (profile overlay; no score change). The documented lifecycle principle (process.md risk-budget ethos: migrate Apex→Core→Sentinel as the book grows) exists only as a manual decision today. Build it INTO the MC/engine as an equity-indexed interpolation: below $X stay Apex (uncapped, 75+), between $X..$Y glide exposure cap 50→40→30% and zero out the 75-79/80-84 tiers progressively (selectivity is THE proven DD lever — Sentinel 85+ cut 5y DD 84%→37%), above $Y run Sentinel. Keyed on OWN equity, not market context — so it does NOT re-open the dry 5th-sizing-lever well (RXDD/SVR/MWDD/TVDD + F3F are market-context levers; this is a lifecycle dial). Hypothesis: near-Apex early compounding with Sentinel-class drawdowns at scale, i.e. max terminal wealth s.t. late-stage dollar-DD bounded — the actual user objective. Validate N=500×10 incl COVID with glide thresholds swept; compare against static Apex/Core/Sentinel frontiers; collapse=0 floor. Gates: T1-T7; profile-registry + temporal-refresh on ship.

### ✅ N3. [RESOLVED 2026-07-13 — the option/IV data-unblock chain ran end-to-end; OSK dead, gamma+IV parked, L3 buy OFF] Acquire historical options/IV data to unblock the #1 staged lead

> **CHAIN CLOSED 2026-07-05→2026-07-13 (all gates run; do not cite this lead as "the next ship" —
> see the FRONTIER REFRESH banner at the top of this section).** Polygon $79/mo bought (P2.2).
> **OSK direct-skew (P2.4): FAIL 2026-07-07** — in-era replicates cross-vendor (rho +0.090, t3.4)
> but backward-OOS 2022-24 is ABSENT (2022 bear −0.107) → regime-conditional, not universal
> (`experiments/osk_validation/VERDICT.md`). **OSK score-modifier: BLOCKED 2026-07-08** at the
> faithful path (WR15 null; pnl15 sign-reversed t−2.19; `experiments/osk_era/STAGE1_VERDICT.md`).
> **OSK Stage-3 alloc tilt: PARKED 2026-07-10** (0/5 variants clear t≥2, best 1.06; re-read
> ~2026-10; `experiments/osk_tilt/RESULTS.md`) — OSK stays a per-trade residual only. **Gamma×IV
> raw panel: COVERAGE-BLOCKED 2026-07-10** (19.3% dose, 10.7% on the decisive 2022-08 window;
> `experiments/gamma_iv_phaseb/VERDICT.md`); the **calibrated model (F2) PASSED the MC A/B
> 2026-07-12, adversarially verified** (explosion collapses, strictly coupled;
> `experiments/iv_premium_model/VERDICT.md`) but the **per-trade adoption gate FAILED M1
> 2026-07-13 after one refinement round** (panel-IV vs real-contract data gap on calm names) →
> **(GAMMA_AWARE+IV_MODEL) PARKED A/B-only default-OFF** (`experiments/iv_engine_pertrade/VERDICT.md`);
> re-open only on a NEW data class (real fills / mid-quotes / P1.4), never another refit on this
> panel. **VEGA_STATE: CALIBRATION-BLOCKED 2026-07-13** — no VIX-stress episode in the 1.4y
> panel, r²≈0.001 wrong-signed (`experiments/vega_state/RESULTS.md`). **L3 buy (~$2,035, P2.5):
> RESOLVED OFF permanently** — both its gates (P2.3 OR P2.4 PASS) are dead. Current live
> frontier: `.claude/docs/gameplan.md` §2b/4/6b (Core tier re-sweep task 612, lifecycle-policy MC
> task 623, P3.6, Dec-2026-12-15 OOS unlock). **P2.1 Sharadar survivorship buy is a SEPARATE,
> still-open, user-locked ($40) thread — do not conflate it with this options chain.**

(original lead text, historical:)

> **PRICED + PLAN 2026-07-05 → [.claude/docs/data-acquisition.md](../.claude/docs/data-acquisition.md).** Both data gaps are retail (~$2k first yr, not the "18-month accumulation" framing). De-risk plan: **trial Polygon options ~$79/mo (4yr IV, 2021-25 incl 2022 bear)** → feed real ATM IV into the MC premium (`0.4·IV·√(DTE/365)`) → re-run the gamma A/B → does it kill the +1754% explosion? Only if yes, buy **historicaloptiondata.com L3 ~$2,035 one-time** for deep depth (2008/2020). The delisted-equity survivorship fix is separate + cheap (Sharadar ~$40 one-month). Scaffolds: `experiments/data_ingest/{polygon_iv_gamma_prototype,ingest_delisted_equity}.py`.

The top staged lead (direct option-skew OSK) and Lead A (historical-IV reprice) are both blocked on `option_prices` depth (~1.3y, no 2020-2022). Accumulating live takes ~18 more months; buying/backfilling historical end-of-day IV/chains (ORATS, CBOE DataShop, Polygon options, OptionMetrics — even ATM-IV + 10%-OTM-skew summaries suffice) unblocks: OSK validation across a bull stretch + 2022, an IV-aware MC, honest forced-exit repricing, and the persist-vs-crash discriminator multiple closed leads keep dying for (G14/G20 wall, sector-cap retry condition). Probably the single highest-EV non-code action available. Next: price the data options, ingest into a parquet sidecar keyed (symbol, date), re-run `experiments/iv_skew/` + `experiments/year_2024_factor/OSK_SHIP_HANDOFF.md` on full depth.

### N4. [NEW 2026-06-09 — LOW, Stage 1] Weekly transition-blend schedule sweep (v69 follow-on)

v69 shipped a LINEAR bars/5 blend (`weekly_transition_t`) between completed-week and partial-week adjustment. Untested cheap variants: cosine ramp, evidence-weighted (bar-count/holiday-aware), and gap-aware trust (lean on partial when `weekly_adj_gap` is small, completed when large). Targets residual Monday/Friday WR asymmetry + fakeout smoothness at zero mechanism risk. Sweep via ScoreSimulator on the honest ledger; gate on dow-flatness + stability + W5 non-regression. Low expected magnitude; very cheap.

### N6. [NEW 2026-08-06 — LOW as tunable lever / MEDIUM as ops screen, Stage 3] Value/growth factor-rotation spread as grind-class regime screen

Trailing-1y Russell 1000 Value−Growth relative spread (IWD/IWF via yfinance, daily 2000-05→now;
~5-line build, series cached `.cache/vg_spread_iwd_iwf.parquet`, probe 2026-08-06). Genuinely new
axis — null-check clean (no factor/style-rotation entry in known-issues/NEW_LEADS/traps; the
dry-well `dd_residual2_v70` screen covered breadth/VIX/EMA axes only). **Episode-level read (this
is the whole signal):** three deep value-rotation episodes in 26y — 2001-02 (trough −48.5pp),
2022-10→2023-01 (−24.0), and NOW (2026-07→, trough −24.2; 2026-08-06 reading −20.3pp = 3.0th
percentile) — and the two priors are exactly our two worst modeled regimes (dot-com grind: Apex
−84% / held-form 100% collapse; 2022: apex_live 50-63% modeled collapse, live book −51%), while
the crash classes our DD stack survives were growth-led or factor-neutral (COVID window mean
+16.5pp, GFC +6.8pp). Mechanism-consistent: the book is a leveraged momentum sleeve
(version-history.md "thin momentum-beta + option convexity") and deep value rotations ARE
momentum-crash grinds — the grind-vs-V distinction the deep-crash screen already flags as the DD
stack's unscreened class. **Monthly-level conditioning is NOISE:** corr(month-start spread level,
call TP rate) sign-flips across profiles/DTEs (−0.45..+0.30, v74 `temporal_summary` join); the one
suggestive cell (30-DTE Apex value-leading months medRet −26% vs −2..−6 elsewhere, n=9 months) is
~2 episodes restated, not independent evidence. **Ceiling: N≈3 episodes → SCREEN, not a tunable
7th lever** (SCREEN-not-GATE doctrine). As a lever it would face the dry-well bar (orthogonality
in the all-levers-off slice — breadth already weakens during rotations, monte-carlo-sweeps.md:156;
sign-stability; panic-exclusion) plus the holdout lock (in-sample until Dec-2026). Next if
pursued: (a) zero-compute ops screen — surface spread state on the ops heartbeat / dashboard as
"Apex sprint is in its documented worst regime class" context; (b) only with extraordinary cause:
MC trade-tape DD-active-subset join vs rotation state (per-trade factor-beta regression), N=300
screen, pre-registered.

**PREDICTIVENESS PROBE 2026-08-07 — timing rules DO NOT clear any bar; screen framing confirmed.**
History extended to 1993 via VIGRX/VIVAX NAVs (r=+0.92 vs IWD−IWF in 6,335-day overlap), so the
dot-com front edge is measured. (1) **Onset flag (zero-cross after growth-led regime): n=6 in
33y — 1 immediate save (2000: −40% growth in 6m), 2 clean false alarms (2010, 2019), 2 whipsaws
that rallied first (2021 +17%, 2025 +27% in the 6m after the flag).** Unusable as a withhold
trigger. (2) **The ≤−15 deep flag is HINDSIGHT in 2 of 3 episodes** (2022 and 2026: 100% of the
growth-index DD already done at flag date); only the dot-com grind had 62% of its −63.6% DD still
ahead — the flag leads only in the grind class, and class is unknowable at flag time. (3) **AFTER
is bimodal and was hostile for our book in both observed cases:** post-recovery 2002-03 → growth
−26%/−27% (+6m/+12m — everything fell together; the spread "recovery" was a trap); post-recovery
2023-02 → growth +14%/+38% (AI V) — but on OUR v74 30-DTE Apex ledger the 2023 REBOUND phase was
the WORST stretch in 127 months (mean −40.3%/mo, med −56.4%, TP 58.1%, supply 10.3 trades/mo vs
15.4 baseline; low-breadth megacap rally starves the 75+ cohort). (4) **Ex-ante phase table
(month-start label, thresholds single-shot):** Apex UNWIND months are its BEST (+18.7%/mo mean,
n=23) and EXTREME_GROWTH fine (+10.6%, n=13) — any front-edge withhold rule cuts the best months
(the panic-exclusion trap, measured on our own ledger); only DEEP (−19.1%, n=3) and REBOUND
(−40.3%, n=6) are red, both post-flag, both from the single 2022-23 episode. **Net: monitoring
screen only — "we are in the historically hostile DEEP→REBOUND window (extends ~6mo past episode
end in both precedents)"; risk response routes through sanctioned controls (stop-at-2x, N2 glide
path, shipped DD levers), never a mined spread-keyed gate.** Repro: scratchpad
`vg_predictive_probe.py` logic described here; series `.cache/vg_spread_iwd_iwf.parquet`.

### N5. [2026-06-09 → ❌ MINED 2026-06-18 → tested-NULL] VIX term structure / VVIX / VRP cohort mine (the regime-intuition corner — now closed)

**Closed NULL (`experiments/vix_term_vrp/{mine,robust}.py` + FINDINGS).** Forward-vol family
(VIX9D/VIX3M term-structure slope, VVIX, VRP) cohort-mined on the 75+ apex CALL book. Pooled
terciles all |z|<1.2; **within VIX-level bands (the decisive G28 control) every flicker DISSOLVES**
— it was just VIX level, already encoded in the multiplier. One cell survived the control —
**norm-VIX (16-22) + backwardation (VIX9D>VIX3M) → +8.94% vs contango +1.22% (z+3.47, N=479)**,
a "contained near-term fear spike → bullish-name bounce" (G19) — but it **fails W3** (sign-flips
−8.17 in 2016, −2.26 in 2025; absent 2021/2022; thin in most windows), is narrow (3.7% of book),
and is a *size-up/admit-more* direction (G16-DD-costly to exploit) + 1-of-~30 cells (mult-comparison).
NOT actionable. Do-not-retry the VIX term-structure/VVIX/VRP family as a regime cohort. The
backwardation flicker is recorded only in case a future option/IV data-unblock revisits "contained
fear spike → bounce" with a different mechanism.

(historical lead text below — superseded by the result above)

The VIX-momentum family (weekly-MACD/velocity/accel, as DD lever AND as regime-multiplier input) is freshly NULL (2026-06-09, `experiments/vix_weekly_v70/`) — do not re-test. The genuinely untested corner: **implied-vol term structure** (^VIX9D/^VIX/^VIX3M slope, backwardation flag), **VVIX**, and **variance risk premium** (VIX² − realized var) — forward-expectation signals, a different family from spot level (RXDD) and momentum (nulled). All fetchable free via yfinance. Scope STRICTLY to a $0-cost W1 cohort z-mine on the option barrier (calls 75+ by term-structure state) — NOT a sizing lever (dry well), NOT a mechanism build unless z≥3 appears. Expectation low (regime A/Bs incl SKEW/credit/haven all null; the multiplier already encodes VIX level+velocity and re-validated optimal on honest v70) — this exists to settle the user-intuition cheaply and finally.

### ◐ [RESOLVED 2026-07-13 — direct option-skew (OSK) validated + FAILED cross-era / Stage-3 tilt PARKED; semivol_r bridge (SVR) remains SHIPPED+live] Option-Skew "Buy-the-Cheap-Side" Entry Filter

**UPDATE 2026-07-13 (RESOLVED) — the DATA-LOCKED blocker was removed (Polygon bought 2026-07-05) and the direct option-skew (OSK) piece was FULLY TESTED, not just unblocked.** Cross-era validation on the Polygon 4yr panel: in-era (2025-26) replicates cross-vendor (rho +0.090, t3.4) but the backward-OOS 2022-24 univariate edge is ABSENT (2022 bear −0.107) — OSK is **regime-conditional, not the universal per-trade residual this entry originally staged** (`experiments/osk_validation/VERDICT.md`, 2026-07-07). The score-modifier ship path is **BLOCKED** at the faithful path (WR15 null, pnl15 sign-reversed; `experiments/osk_era/STAGE1_VERDICT.md`, 2026-07-08) and the Stage-3 allocation-tilt path is **PARKED** (0/5 variants clear the bar; `experiments/osk_tilt/RESULTS.md`, 2026-07-10; next re-read ~2026-10). **OSK stays a per-trade residual only — do not re-open on "needs more data," it now HAS the data and failed cross-era.** The `semivol_r` proxy (SVR) below is UNAFFECTED and remains shipped+live. Full chain: `alpha_mining/NEW_LEADS.md` N3 (RESOLVED) / `.claude/docs/gameplan.md` §2b/4.

**UPDATE 2026-06-09 (/research, 2024-IT-factor follow-on) — direct option-skew is a CONFIRMED RESIDUAL to the shipped SVR on the TRADABLE 75+ cohort, and SVR's "skew bridge" branding is a WEAK PROXY (not the real edge).** Partial regression on the 75+ covered cohort (N=430, actual fwd option P&L, win-rate): **opt_skew\|semivol_r t=+3.16 while semivol_r\|opt_skew collapses to t=+0.15** — `semivol_r` correlates only +0.14 with `opt_skew` and works for its OWN directional reason, NOT as the skew bridge it was branded. opt_skew is orthogonal to the price score (t=+3.21) AND recent return (t=+3.52, strongest in down-r20 tercile t=+3.07 = the persist-vs-crash sweet spot), sign-stable every quarter (never flips), 23pp win-rate quintile spread (36%→59%). **But it CANNOT pass the standard gate, structurally:** premium-dominated (realized-vol MC blind, opt_skew→underlying barrier only z≈+1.8), option-data-locked (~1.3y, no COVID/2022/2021 → Stage-3 collapse=0 floor unreachable), and the one covered window (2025-02→2026-04) is a net-negative selloff book (OSK-on-top-of-SVR = +0.6-1.5pp per-trade but flat-to-−1.2pp portfolio there). **STAGED, not shipped.** Mechanism design (OSK = SVR-clone keyed on opt_skew, one-sided cheap-side cut) + the exact ship-path (data depth ≥~2.5-3y spanning a bull stretch + a premium-aware covered-window cascade validator + a framework decision on gating option-implied signals, shared with lead A) in **`experiments/year_2024_factor/OSK_SHIP_HANDOFF.md`** (scripts `skew_residual/skew_winrate/skew_robust/isolate_osk_vs_svr.py`). The strong piece waits on `option_prices` coverage, not ideas.

**UPDATE 2026-06-05 — the `semivol_r` half SHIPPED as SVR** (`7e6f8fe19`, portfolio-only, v70 Apex). Stage-3 MC of the `semivol_r` band-pass entry filter PASSED the full gate: Phase D N=500×8 incl COVID **5y WorstDD −5.8pp AND compound +28.6%**, 22-now −5.6pp/+40%, collapse=0 every window, T1-T7 PASS — the modest "+1.9pp apex15 may not survive portfolio gates" caution was beaten (the band-pass shape, contracting BOTH the euphoric-low and crash-high tails, did more than the raw directional tilt). Live feature `database/utils/semivol.py`, gentleband c00 (LO_CUT0.5/LO_FULL0.7/HI_FULL1.25/HI_CUT1.65/FLOOR0.5). Record: `experiments/apex_speed_v70/SVR_SHIP_HANDOFF.md` + `project_svr_skew_bridge_ship` memory. **STILL OPEN:** the DIRECT option-chain skew (the stronger PREMIUM-aware edge, t=+4.04 vs semivol's underlying-barrier z=+4.25) — needs an IV-aware MC or deeper `option_prices` coverage (>1.3y) to validate at portfolio scale; that's the bigger fish SVR only partially captures.

Stage: Stage 3 portfolio (entry filter / sizing — it's about OPTION PRICING, not stock direction; score frozen, no `ALGORITHM_VERSION` bump). The ONLY positive result from the 2026-06-03 new-signal hunt after 5 clean nulls (divergence ×2, per-stock knobs, per-stock normalization, relative-strength).

Goal: skip/downweight call signals where the CALL is expensive relative to the put (low/negative skew = call-euphoria/squeeze pricing); keep/upweight where the call is cheap (normal/high put-skew).

Why it ranks top: it's the first signal ORTHOGONAL to the price-technical score (options-implied) — and the realized-vol barrier is structurally blind to it, which is why the directional-signal hunt kept landing at ~47% while this hit. `skew = put_iv(10%-OTM) − call_iv(10%-OTM)` from `option_prices`, point-in-time (same-day chain), live-computable.

Evidence (holdout-respecting, N=2,018 calls / 5,820 puts, ACTUAL fwd option P&L, not barrier touch):
- Calls: HIGH put-skew → better Apex P&L, win-rate 39%→53%, **t=+4.04**; sign-STABLE every quarter (the test that killed the iv_rv variant, which sign-flipped).
- Orthogonal: adds within recent-return terciles (corr −0.16) AND within score bands; survives drop-top-5-syms (t=+4.24).
- NOT meme-unwind: strongest in mid/large caps (≥10B t=+3.32), 6/8 sectors same-sign; robust to measure spec (ATM-normalized t=+3.88).
- **Mechanism CONFIRMED symmetric** ("buy the cheap side"): puts flip sign — LOW skew → better puts, **t=−4.62**, both periods. Relative-value cheapness, not directional sentiment.
- Artifacts: `experiments/iv_skew/FINDINGS.md` (+ recon/build/w1/verify/deepen/put scripts), `.cache/iv_skew/*.parquet`.

Validation (2026-06-04): **(1) the edge is mostly PREMIUM** — skew→actual option P&L t=+4.04 but skew→underlying barrier only z=+1.8 (weak), so the realized-vol-premium MC is structurally blind to most of it. **(2) Premium-aware sim (1.3y actual option P&L): per-trade edge real (win 47→52%, perTrade −19.9→−15.1% at 70+/drop-50%) but PORTFOLIO benefit MODEST (+1-4pp) and the book is net-negative either way** on the selloff-heavy window — makes a losing book less-losing, not a winner; 1.3y too short for a confident portfolio claim. **(3) Proxy reconcile: literal price-skew proxy barely tracks option skew (corr 0.12), BUT `semivol_r` (downside/upside realized-vol ratio) independently captures the SAME call-P&L edge (overlap t=+5.0) AND a small directional signal on full 10y (→opt15 z=+4.25, →apex15 z=+11.57, +0.8/+1.9pp)** — a 10y-computable cousin that the standard MC CAN see.

Next experiment: **Stage-3 MC of a `semivol_r` entry-filter/sizing knob on FULL 10y** (it predicts the underlying barrier, so the standard MC isn't blind to it) — tempered, +1.9pp apex15 is modest and may not survive portfolio gates. Separately, re-validate REAL option skew (the stronger, premium-aware edge) via an IV-aware MC on the covered window OR as `option_prices` coverage deepens past the current 1.3y. Artifacts: `experiments/iv_skew/{skew_decompose,premium_sim,build_proxy,reconcile_proxy}.py` + results.

Ship gates: Stage-3 T1-T7 (DD-primary) on whatever window is validated; portfolio-only, no score change. Honest about the 1.25y data depth; do not over-claim a ship until the portfolio-level benefit is shown net of capacity (the cheap-side calls must be numerous enough to matter, not just better per-trade).

### A. [OPEN — 2026-06-03, MEDIUM] Historical-IV reprice for the day-15 hard-sell / forced exits

The day-15 hard-sell and SL fills are priced by a calibrated **closed-form** option model (√T theta + delta@0.5 + sampled empirical vega). It's empirically anchored (premium ±3% vs ~194k real ATM quotes; IV-crush from ~20,871 obs) but not exact: theta is √T (real theta accelerates near expiry), delta is held at entry-ATM 0.5, and there's no historical IV surface. The one real upgrade: where `option_prices` chains exist (Feb-2025→now), reprice forced exits against actual historical IV — would mostly refine earnings-spanning trades. Low-to-medium EV; the current model's realized hard-sell mean (−37%) sits at the theta-nominal (−40%), so no systematic bias, but exactness would harden the DD tail.

### B. [OPEN — 2026-06-03, LOW] F3F breadth floor 0.30 as a Core/Sentinel DD-tilt

F3F_CALL_FLOOR 0.50→0.30 (+ LOW 30→40) cut 10y(COVID) DD −3.4pp but cost −19% tail return (and 5y ×0.78, 2022 ×0.49) — return-costly, so NOT wired to Apex (which wants compounding). It's a legitimate **DD-tilt for Core/Sentinel** (which trade return for safety). Validate at N=500 on the Core/Sentinel caps before wiring.

### C. [OPEN — 2026-06-03, LOW] Reconcile SL-breach notification semantics with the dead-hold

Notifications now fire at the live −70% SL (staleness fixed, `f164a8b88`), but under the dead-hold a −70% touch is HELD, not sold — so the alert is an FYI, not an exit. Consider rewording to "deep underwater / dead-hold engaged," or alerting only at the dead-hold popout (−15%) / day-15 exit.

### 0. [ROOT CAUSE — 2026-05-31] Weekly recalc look-ahead contaminates ALL weekly/boundary calibration

**This is the single most important finding for the weekly leads below.** All
scoring paths key the weekly composite on the CURRENT week (`date - weekday()`),
and `WeeklyScore` is stored as the COMPLETE Mon-Fri bar. So a **recalc** of a
mid-week historical signal reads future bars = **look-ahead**. Empirically: the
70-74 `w_mom∈[5,8)` "promote" cohort shows optTP15 Mon 68.3 / Fri 49.75 (vs 70-74
baseline Fri 49.70) — on Friday (current week ~complete) the edge VANISHES. A
daily-only point-in-time re-mine of the 75-79 boundary found NO clean edge. So the
apparent alpha in leads #1, #5, #6, #7 (all weekly/boundary residual) is largely
**look-ahead, not tradeable** — this is why v61/v65/v66 and the
weekly-transition-resolver all failed. Artifacts: `experiments/rqc_v60/`
(option-augmented ledger + dow tests), memories [[reference-weekly-recalc-lookahead]]
and [[reference-growth-gate-supply-fallback]].

**Live vs recalc:** live `trader update` recomputes the current-week `WeeklyScore`
from current bars (Mon→today **partial**, point-in-time but fakeout-prone as it
evolves daily). Recalc reads the stored COMPLETE week (look-ahead). The naive fix
(use last-completed-week) is TOO stale — it collapses 75+ signals by ~64% and
moves scores ~7pts (validated via simulator). **The proper fix = point-in-time
partial-week reconstruction (rebuild weekly RSI/MACD from daily bars
week-start→signal-date only) + a transition blend** to also smooth the
intraday/mid-week fakeout. This is the recommended next Stage-1 build; it CANNOT
be certified by the growth gate (the gate measures on contaminated recalc rows
and will prefer the look-ahead version) — validate on dow-flatness + stability +
honest-WR-clears-BE. NOTE the prior weekly-transition-resolver was invalidated by
a look-ahead bug in ITS feature builder ([[feedback_lookahead_feature_build]]) —
reconstruct from raw PriceHistory only, validate against `score_intraday_logs`.

Until that build exists, treat leads #1/#5/#6/#7 as BLOCKED on weekly
look-ahead, and prefer DAILY (point-in-time-safe) discriminators for any
near-term Stage-1 ship.

### 1. Active-Baseline Residual WR15 Controller — ◐ LARGELY CLOSED 2026-06-09 (apex15 re-mine)

Stage: Stage 1 scoring.

**STATUS 2026-06-09 (`experiments/missretest_apex15/`):** both sides closed on the live apex15 HOLD
barrier. DAMP side — a fresh open-ended mine of the v70 75+ universe found **0/42 cohorts harmful at
z≤−3**; no identifiably-bad 75+ incumbent to damp (misses are vol-path, not feature: 82% of losers never
approached TP, median MFE 0.62σ). ADMISSION side — **no selective 70-74 lift target**: the band sits
68.1% (only 1.7pp under the 69.8% 75-79 tier, which is why whole-band overflow@0.035 already works), but
70-74 cohorts that reach the bar have no discriminator and cohorts with a discriminator sit just under
it. Whole-band overflow (shipped) is the right 70-74 tool. Revisit ONLY with a non-directional feature
(option skew / vol-path), not a re-grade of the technical score.

Moved: was the top lead; downgraded — directional-score residual control is closed for the live strategy.

Goal: mine active-score mistakes directly: missed 70-74 CALL winners that deserve promotion, weak 75-84 incumbents that should be softly damped, and rows where the current active scorer moved in the wrong direction.

Why it ranks highest: it targets the user's primary objective directly, WR15 plus useful high-tier N. The alpha-fisher searches proved the 70-74 admission surface has real signal, but active-DB validations showed the current profile shapes damage high-tier quality. The next shape must be active-baseline residual, not alpha-disabled replay.

Evidence:

- v60 broad WR15 profiles selected huge cohorts but diluted 75+ WR15 by more than 2pp.
- v60 focused `admission70` same-runtime profiles were clean, then failed active-DB because they lost active 75+ N and degraded 80+ WR15.
- v61 worktree / active-panel search found strong 70-74 candidates, for example `eval13333` with N=1,975, WR15=79.14%, residual +7.24pp, excess +143.0, but active-DB validation still degraded 80+ WR15 by about 2.8pp and created broad score deltas around 0.86% with `abs(delta)>=5`. Treat v61 here as evidence from that isolated research line, not as the active score version in this checkout.
- Artifacts:
  - `C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_16000calls.md`
  - `C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\stage1_validation\admission70_eval13333_active_db_2329d.md`
  - `C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\stage1_validation\admission70_eval6920_active_db_2329d.md`

Next experiment:

1. Build an active-baseline residual ledger from current active score rows.
2. Label missed 70-74 winners, failed 75-84 incumbents, and active-score wrong-way moves.
3. Fit a two-sided smooth modifier: promote high-confidence 70-74 misses and damp weak 75-84 incumbents only when the active baseline evidence says the row is wrong.
4. Validate same-runtime, then active-DB. Do not rank by selected-row WR alone.

Ship gates:

- 75+ WR15 preserved or improved versus active DB.
- 75+ N preserved or increased; no hidden loss of useful high-tier N.
- 80+ WR15 no worse by more than 1.0pp.
- 85+/90+/95+ unchanged or improved.
- Score deltas narrow; admission-only profiles should not reach base scores in the low 50s.
- No scoring ship without an `ALGORITHM_VERSION` bump and normal scoring deploy path.

### 2. Wave-Put Reserve Ledger

Stage: Stage 3 portfolio.

Moved: up from lower backlog. This is the highest-upside remaining practical DD reducer from the post-Sentinel exploration, but it is infrastructure-heavy.

Goal: throttle weak put exposure while reserving the unspent premium until the original exit date, preventing freed buying power from causing harmful replacement-path churn.

Why it ranks here: the reserve branch has the cleanest direct evidence that the earlier put-wave alpha was not just a score/filter artifact. Its blocker is not the signal; the blocker is live portfolio state. If implemented correctly, it can reduce DD without changing score rows.

Evidence:

- The original smooth put-throttle failed because freed buying power changed later fills.
- Reserve candidate `wave_put_divergence_reserve_0124` passed production-equivalent N=500 MC in the research harness: 2020-crash unchanged, 2024 unchanged with positive return, 2025 worst DD improved -4.93pp, 22-now worst DD improved -5.27pp, 5y worst DD unchanged with positive mean/median return.
- The reserve design supersedes the older `0051` path for promotion work.
- Artifacts:
  - `.codex/runs/v60_wave_put_reserve_0124_n500x8_20260520_175629/wave_put_reserve_0124_n500x8_summary.md`
  - `.codex/runs/v60_prod_reserve_0124_mc_20260520_225350/production_reserve_mc_summary.md`
  - `.codex/runs/v60_prod_reserve_0124_n500_20260520_230521/production_reserve_mc_summary.md`
  - `experiments/daily_opportunity_allocation/top_candidates_production_reserve.csv`

Next experiment:

Implement a prototype reserve ledger in maintained portfolio code: reserve unspent premium from throttled puts until the baseline trade's simulated exit date, subtract that reserve from deployable cash, release it deterministically on the modeled exit date, and rerun production-equivalent Stage 3 validation.

Ship gates:

- Portfolio-only; no `ALGORITHM_VERSION` bump.
- Ledger must be auditable, deterministic, and visible in allocation/backtest/MC artifacts.
- No 2024/2025 replacement-path DD or return drag.
- Validate with N=500 x 8 or stronger after live-style reserve plumbing exists.

### 3. CT Crash Suppression With Production Feature Source

Stage: Stage 3 portfolio.

Moved: up from lower backlog. This is the cleanest acute-crash suppressor, but it cannot ship from research-only wave files.

Goal: suppress or fade CT-call promotion during acute crash tape for marginal CALLs.

Why it ranks here: it directly targets COVID-style crash DD without changing scoring, and broad validation showed little non-crash disturbance. The practical blocker is a production-grade feature source and sufficient crash-window confirmation.

Evidence:

- `ct_crash_suppress` touched exactly two COVID CT-promoted source calls in the validation artifact and was neutral elsewhere tested.
- N=240 MC improved covid-peak worst DD by -8.91pp and mean DD by -7.98pp; 2020-crash worst DD by -2.68pp; 2020-full worst DD by -2.32pp; flat in 2022, 2024, 2025, 22-now, and 5y.
- `ct_crash_demote_low` also passed but gave up too much protection relative to full suppression.
- Artifact: `.codex/runs/v60_ct_crash_suppressor_mc_20260520_190800`.

Next experiment:

Replace research-only wave prediction files with production-grade features: score 70-74, `trend <= CT_CALL_TREND_MAX`, daily market-structure risk q90, sector crash pressure, broad EMA50 breadth <=25, McClellan <=-40, and VIX/breadth acceleration. Then test CT-only suppression under canonical stress windows.

Stop rules:

- Source N is tiny; validate on more crash/stress windows before treating it as ship-ready.
- Keep it CT-promotion suppression, not a scoring version bump, unless independent WR15 evidence appears.
- Do not turn this into broad marginal-call deletion.

### 4. Conditional 12/4 Portfolio Capacity Controller

Stage: Stage 3 portfolio.

Moved: stayed high, now behind the two higher-upside DD leads. This is the lowest-friction post-Sentinel tuning path.

Goal: improve DD by behaving like a lower offered-N profile only when state says the book is crowded or fragile, while retaining Sentinel's 16 calls / 4 puts center in the long-window states that broke static 12/4.

Why it ranks high: this is the cleanest incremental portfolio-side extension after Sentinel. It can improve practical drawdown without changing scores, and it directly answers how much N can be traded for WR15 without relying on hard cliffs.

Evidence:

- Sentinel center: 16 calls / 4 puts.
- Static `n12_4_quality_g80_c65_p25_floor58` improved average worst DD by +3.79pp versus Sentinel and improved practical log by +0.011, but worsened `2022_now` worst DD by +3.89pp.
- Filled-capacity floor is roughly 5-6 total entries/day; practical side floors are about 4 calls/day and 3 puts/day.
- The strongest next mining region is conditional 12/4, not a static 12/4 replacement.
- Artifact: `C:\Development\Trader\.codex\runs\n_capacity_profiles_20260521_152650\n_capacity_profile_comparison.md`.

Next experiment:

Search a smooth state-conditioned `call_ref`, `put_ref`, `sat_floor`, and gross/call cap controller. Candidate state features: same-day call pressure, put pressure, recent realized entry TP/PnL, breadth, VIX, drawdown, Market Wave divergence, and cash-bound share.

Ship gates:

- No tested stress-window DD regression versus Sentinel greater than 1.0pp unless explicitly accepted as a risk profile.
- Preserve practical log/final-return floor.
- Keep fill-equivalent daily N above about 5 unless WR15 evidence is extreme.
- Refresh temporal profile stats after any portfolio profile change.

### 5. N-Preserving Weekly Maturity Guard Retune

Stage: Stage 1 scoring, possibly followed by Stage 3 confirmation.

Moved: down behind the stronger portfolio DD leads because v61 shipped and was reverted, but this remains the best weekly scoring residue.

Goal: preserve the drawdown protection found by the weekly mature call guard while recovering more CALL 75+ N.

Why it ranks high: the weekly guard family found real DD/WR structure, but the useful N compression was expensive. A softer curve may keep the 75-79 WR15 lift while improving practical signal supply.

Evidence:

- The weekly mature call guard work found a strong CALL 75-79 WR15 lift, but CALL 75+ N compressed heavily and total 5y WR15 utility fell.
- Future-weekly section in `experiments/daily_opportunity_allocation/V60_FINDINGS.md` identifies this as the top weekly follow-up.
- v61 was shipped and reverted, so this must restart as a fresh scoring-version research stream, not a portfolio overlay.

Next experiment:

1. Rebuild the weekly event ledger from active score rows.
2. Split guard events into DD-saving true positives, harmless N removals, and lost winners.
3. Search softer target/risk curves, lower max penalty, and narrower overextension triggers.
4. Validate against active baseline with per-discrete-bucket WR15 and N.

Ship gates:

- 75+ WR15 preserved or improved.
- Material 75+ N recovery versus the guard candidate.
- 80+/85+/90+/95+ not degraded.
- Portfolio stress windows no worse than baseline if the scoring change materially shifts offered N.

### 6. Fresh Weekly Momentum Admission Lift

Stage: Stage 1 scoring.

Moved: down one tier under the retune umbrella, but still a high-quality weekly lead.

Goal: promote 70-74 CALL rows when weekly momentum is fresh and constructive, while avoiding late overextension.

Why it matters: `w_mom` 5.0-7.9 was one of the strongest positive weekly signal pockets. This may be additive to the maturity guard because it is an admission lift, not just a dampener.

Next experiment:

- Mine CALL 70-74 rows where `w_mom` is constructive but `wk_ret4` / `w_comp` is not extended.
- Fit a smooth lift wave with a ceiling that cannot disturb 80+ rows.
- Validate active baseline, not old v60 discovery panels.

Ship gates:

- Promotions land mostly in 75-79, not top-tier churn.
- 75+ WR15 and N improve together.
- 80+ and above remain stable.

### 7. Weekly Phase Inverted-U: `w_comp`, `w_bias`, and `wk_ret4`

Stage: Stage 1 scoring.

Moved: unchanged as a secondary weekly shape.

Goal: model weekly phase as a bell/saturation curve: moderate weekly strength is constructive, but late-stage extension should fade.

Evidence:

- Moderate `w_comp` / `w_bias` bins were positive while high bins were negative.
- `wk_ret4` was constructive in moderate ranges and harmful when extended.

Next experiment:

Fit an inverted-U weekly phase controller. Do not use a monotonic boost. Test standalone and in interaction with the maturity guard retune.

Stop rules:

- Any broad weekly-composite boost that expands N while degrading 80+ WR15 is a no-ship.
- If the curve only works by deleting 75+ N, move it to Stage 3 tie-breaking instead.

### 8. Continuation / Markov Recovery Exit Model

Stage: Stage 3 portfolio exit management, possibly Stage 1 only if score-stage state proves directional WR15.

Moved: new lead from the v59 recovery / Markov cohort append.

Goal: replace blind adverse-PnL exits with a smoothed continuation model that estimates recovery odds from side, DTE/day, current PnL, score state, prior fulfillment, and fallback cohorts.

Why it ranks here: this is not a quick ship, but it could improve DD and capital velocity without throwing away recoverable trades. The key finding was asymmetric: early adverse CALLs can still recover often enough that broad hard cuts look dangerous, while early adverse PUTs looked materially weaker.

Evidence:

- CALLs down about 20-30% early still had meaningful recovery odds; blind hard cuts are not obviously superior.
- PUTs down about 20-25% by day 3 looked materially worse and are closer to a portfolio-stage cut/throttle candidate.
- Exact 25-30 PUT buckets were thin; deeper adverse buckets were dominated by dead-hold and should not be interpreted as normal recovery odds.
- Prior fulfillment and current score state are useful dimensions, but not enough as standalone gates.
- Artifact directory: `C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543`.
- Key artifacts:
  - `state_visits.csv`
  - `state_summary.csv`
  - `transitions.csv`
  - `summary.json`

Next experiment:

Fit a smoothed/backoff continuation model: full state -> no-prior state -> core state -> side/DTE/day/PnL base rate. Validate as a portfolio exit overlay before considering any score-stage integration.

Ship gates:

- Improve DD or capital velocity without reducing final-return floor.
- Separate CALL recovery from PUT failure states; do not use one universal adverse-PnL rule.
- Prove the model survives sparse-state shrinkage and dead-hold contamination.

### 9. Continuation Echo Exhaustion-Entry Guard — ◐ premise weakened 2026-06-09

Stage: Stage 1 scoring.

**STATUS 2026-06-09:** on the live apex15 barrier, continuation-lifted 75+ calls are z+0.9 (cohort
slightly *better*, not harmful), and miss-family #2 (continuation over-admission) REVERSED sign vs the
v60 generic barrier. The premise (cont_lift promotes bad exhaustion entries) does not hold on the funded
HOLD barrier; the UAMY seed was a generic-barrier single-stock case. Pursue only if a fresh
exhaustion-geometry cohort shows harm specifically on apex15. Record: `experiments/missretest_apex15/FINDINGS.md`.

Moved: down behind the broader Markov recovery path; premise weakened on the live barrier.

Goal: prevent continuation lift from promoting exhaustion-entry candles into 75+ CALL territory.

Evidence:

- UAMY 2025-07-18 exposed a continuation lineage failure mode: tiny real body, dominant upper wick, weak close location, `REJECTION` volume signal, and sharp 10-bar runup were promoted by `cont_lift`.
- Active-investigation doc: `.claude/docs/active-investigations/continuation-boost.md`.

Next experiment:

Build a cohort from continuation-lift promotions with exhaustion geometry. Candidate smooth inputs: body/range, upper-wick share, close location, 10-bar runup, distance from EMA50, and rejection volume. Apply as a retention dampener on continuation lift, not a broad call score penalty.

Ship gates:

- Affected-cohort WR15 improves.
- 75+/80+ N not materially reduced.
- Do not ship from the UAMY seed case alone.

### 10. Core Profile Midpoint Search

Stage: Stage 3 portfolio profile.

Moved: up slightly as a practical profile productization lead.

Goal: find a better middle profile between Sentinel's DD discipline and Apex v2's full-base growth.

Evidence:

- Core v1 is provisional: `core_g85_c70_p25_ref18_4_floor60_dd405565`, +0.026 average log-final lift, +4.34pp average worst-DD, +10.15pp max worse, 0% collapse.
- Apex v2 is deliberately DD-up: +0.05698 average log-final lift, +7.93pp average worst-DD, +14.71pp max worse, +5.48pp 2025_dip DD worsening, 0% collapse.
- Registry: `algorithm_versions/portfolio_profiles.json`.

Next experiment:

Search Core v2 around caps 82-88/68-72/23-25, call refs 16-19, sat floors 0.56-0.62, and DD bands between Sentinel and Apex. Optimize for useful compounding lift with less 2025_dip and max-DD drift than Core v1.

Ship gates:

- Profile-only registry update; no score recalc.
- Temporal stats refreshed for the profile.
- UI/API labels remain Sentinel/Core/Apex, not score-version labels.

### 11. Market Structure Overflow Dampener for Marginal Calls

Stage: likely Stage 3 allocation tie-breaker; Stage 1 only if directional WR15 independently passes.

Moved: down because CT crash suppression is the sharper acute-crash version of this idea.

Goal: reduce or deprioritize risky 70-74 CALL overflow during bad market-structure days without harming useful high-tier calls.

Evidence:

- Daily wave model separated top-20% risk days at 64.9% call-bad / 32.4% call-win versus non-top-20 days at 34.1% bad / 63.1% win.
- `wave_q90__sector_crash_overflow` was precise in COVID-style crash tape but failed 2022 when applied too broadly.
- Hard score-dampen proxies failed MC; path-diff showed many blocked rows were zero-allocation overflow, so filled-trade attribution matters.

Next experiment:

Split acute crash overflow from slow bear-market overflow. Use fast breadth-collapse acceleration, VIX shock, crash-echo slope, and McClellan to distinguish COVID-like tape from 2022-style churn. Prefer allocation/admission tie-breaking under slot scarcity.

Stop rules:

- Do not hard-remove every matching 70-74 call.
- Do not count unfilled overflow rows as real portfolio alpha.

### 12. Sector-Concentration Exposure Wave — TESTED NULL 2026-06-09 (do not retry without a regime gate)

Stage: Stage 3 portfolio. **Status: NULL** (cheap tape-mine, `experiments/offyear_dd_catalog/`).

**Result (2026-06-09 /research, off-year DD miss-catalog):** the off-year (2021/2023/2025) drawdowns are NOT sector-correlated crashes, so a same-sector exposure cap has nothing consistent to bite on. Three kills: (1) the −90% `dh_expiry` bags are ~sector-distributed like the book (bag-Herfindahl ≈ book-Herfindahl, top-sector Δ only +3-6pp); (2) bags are temporally diffuse — enter AND exit across 39-47 distinct weeks (worst week 6-15%), a steady drip not a synchronized crash; (3) same-sector concurrency does NOT consistently predict bags — the partial t (bag ~ same-sector-concur | total-concur) **FLIPS sign by year**: +3.0 (2021), +4.7 (2023), but **−5.6 (2025), −5.1 (2024)** — crowded-sector is the WORST cohort in reversal years and the BEST in momentum-persistent years (2024 ssc 6+ bagrate 5.6% / mpnl +0.169). Mechanism: off-year DD is a **cross-sector momentum-FACTOR reversal**, not a sector crash — the book's only common factor is the 75+ momentum score (definitional exposure, not diversifiable), and whether a crowded sector persists vs reverses is the unpredictable persist-vs-crash distinction (G14/G20). A static sector cap would HURT the 2024-type years. Total-concurrency DD is already captured (G22/MAX_POS); same-sector adds nothing stable beyond it.

Retry condition: ONLY with a regime gate that can distinguish persist vs reverse ex-ante (the same wall the option-skew lead targets) — a static or DD-gated sector cap is closed. Do not re-mine sector/temporal/crowding clustering of the off-year bags. Full catalog: `experiments/offyear_dd_catalog/FINDINGS.md`.

Stop rules:

- Do not add sector concentration to `Score.overall` merely to reduce DD.
- Do not optimize Stage 1 scoring on correlated fill outcomes.
- Do not re-test a static/DD-gated same-sector exposure cap — the signal sign-flips by regime (2026-06-09 null).

### 13. Put-Side Weekly Phase and Mild `w_adj` Residual

Stage: Stage 1 scoring, put side.

Moved: down. It remains a real open surface, but narrower than the call-side weekly and portfolio-DD leads.

Goal: mine put-side residual signal in weekly volume/composite/stochastic interactions and the `wadj` band not already captured by existing WCF lineage.

Evidence:

- Some put-side weekly bins had positive signal (`wv_force1`, `w_comp`, stochastic interactions).
- Historical docs preserve put-side `wadj in (-13, 0)` as still open after the call-side weekly dampener shipped.

Next experiment:

Run a put-side miss ledger on active scores, specifically for mild bearish weekly confirmation that fails to become a strong put signal. Test tanh or bell-shaped weekly amplification, not a cliff.

Ship gates:

- Improve put WR15 in affected cohorts without hurting tighter put tiers.
- Preserve put N floors, especially because Sentinel already keeps practical put floors tight.

### 14. 2023 Signal-Density Anomaly

Stage: Stage 1 scoring diagnostics / Stage 3 fill diagnostics.

Moved: unchanged.

Goal: understand why 2023 produced unusually few qualifying setups despite a bullish tape.

Evidence:

- Monte Carlo sweep docs note 2023 signal density around 2.32/day versus 4.55 in 2022 and 4.95 in 2024.

Next experiment:

Compare score component distributions and rejected near-threshold rows in 2023 against 2021/2024. Check regime filters, breadth filters, Market Wave state, SCW/CWWD/PESS/MCD/ICH contributions, and continuation echo retention.

Ship angle:

- If the anomaly is true over-suppression, it may produce N-preserving Stage 1 admissions.
- If it is correct risk avoidance, leave it alone.

### 15. PESS / EARN_BOOST / CWCF Interaction Audit

Stage: scoring audit.

Moved: unchanged.

Goal: verify that score-stage replacements and earnings boost do not unintentionally re-admit or re-inflate rows that were supposed to leave a boundary.

Evidence:

- Known-issues retains three interaction questions: PESS-lifted puts near earnings, CWCF-dampened calls near earnings, and PCD-lifted puts with EARN_BOOST.

Next experiment:

Use `explain-scores` and weight_info queries on real affected rows, then build small cohort checks if any interaction looks wrong.

Ship angle:

- Mostly correctness/guardrail work. Promote only if it reveals a real WR15/N defect.

### 16. Dashboard Tradability Indicator

Stage: UX / user-protection.

Moved: up in practical relevance, but below alpha/DD leads because it does not improve the strategy by itself.

Goal: surface portfolio-state filters that cannot be encoded in score: F3F, DD soft-band, dead-hold, MaxPos/side caps, Sentinel/Core/Apex constraints, and profile-specific allocation throttles.

Why it matters: after Sentinel, more of the truth lives at portfolio state. Users should not see a high score and assume it is fully tradable when caps or path-dependent portfolio mechanisms would throttle or skip it.

Evidence:

- Score-stage replacements fixed CWWD/PESS dashboard divergence, but remaining cascade-stage mechanics are path-dependent.
- Known-issues describes a `would_trade` field with reason codes.
- The older `cascade_skip` pattern is dormant but useful precedent for surfacing runtime-vs-dashboard divergence.

Next experiment:

Add API/UI indicator showing whether a current signal would be tradable under a canonical portfolio/profile state, with clear reason text. This is not an alpha improvement but reduces user execution mismatch.

Ship gates:

- Keep it explanatory; do not invent hidden score penalties.
- Include reason codes for side caps, max positions, DD soft-band, F3F-style breadth filters, and profile caps.

### 17. Market-Cap-Dependent TP/SL Calibration — TESTED NULL 2026-06-09 (σ-normalization pre-flight)

Stage: Stage 2 barrier / portfolio-barrier. **Status: NULL.**

**Result (2026-06-09, `experiments/missretest_apex15/sigma_norm_mcap.py`):** the σ-normalization caveat
the framework demanded killed it. v70 75+ CALL JOIN market_cap — in σ units the forward-path shape is
identical across mcap (micro vs large+: mfe15σ ratio **0.99**, mae15σ ratio **1.05**). The barriers are
already σ-scaled, so per-mcap TP/SL just re-derives the existing scaling and adds nothing. The residual
WR gap (xl/mega 75.8% vs micro 68.5%) is non-monotonic (large 50-200B lowest at 67.1%), score-stage/MCD
turf (shipped v43), and NOT barrier-addressable (σ-path identical). Retry only if a future σ-normalized
move distribution genuinely differs by mcap (it does not on v70). Closed.

Moved: was open Stage-2; now NULL.

Goal: test whether POET-style small/high-movement names deserve wider take-profit and stop-loss settings because their normal move distribution is materially larger.

Evidence:

- Prior participation-wave work preserved this as orthogonal future work.
- Current Stage 2 framework says barrier changes must bound the WR15-to-option-capture gap and rebuild barrier caches.

Next experiment:

Bucket by market cap and realized move distribution, then test per-cap TP/SL or premium-mult curves. Keep scoring frozen.

Stop rules:

- Do not mix into Stage 1 scoring.
- No ship without Stage 2 barrier rebuild and downstream Stage 3 sanity.

### 18. Put SL Breadth-Gated Widening

Stage: Stage 2 barrier / Stage 3 portfolio interaction.

Moved: unchanged and intentionally low because standing put SL widening is already a no.

Goal: test whether puts should get wider SL only in truly bearish breadth regimes, avoiding the standing-policy velocity drag that killed wider put SL.

Evidence:

- Standing put SL widening is a null: wider put SL raises raw TP but loses capital velocity and hurts high-density years.
- 2022 bear year was the exception where wider SL helped.
- Trading-strategy notes preserve breadth-gated put SL, for example widen to -35% when `breadth_score <= 30`, as untested.

Next experiment:

Small coarse sweep over breadth thresholds 20/25/30/35 and put SL -25/-30/-35, with option TP%, average option PnL, and Stage 3 smoke.

Stop rules:

- Do not widen put SL as a standing policy.
- Reject if high-density years give back too much velocity.

### 19. EVR-1: Volume Gradient OFF Full Confirmation

Stage: scoring / code-health if confirmed.

Moved: down. This is useful cleanup/confirmation, but its evidence is contradictory and less directly tied to the post-Sentinel path.

Goal: resolve contradictory evidence on whether the earnings volume gradient should be disabled.

Evidence:

- Per-trade gate showed OFF admits extra high-tier peaks above call break-even.
- Some lower-N MC contradicted due likely seed noise.
- Known-issues asks for N=1000 x 8-window validation before action.

Next experiment:

Run N=1000 across all canonical windows for current gradient versus OFF under pinned/stable seeds. If OFF clears, remove the dead gradient code path.

Stop rules:

- If any major DD/stress window regresses, classify OFF as per-trade alpha that fails portfolio sequencing.

### 20. Re-Entry Boost With Crash/Stress Guard

Stage: Stage 3 portfolio.

Moved: unchanged, low until a crash-safe shape appears.

Goal: allow small call-side allocation recovery when drawdown is elevated but call demand remains healthy.

Evidence:

- Deterministic replay found re-entry candidates with high utility.
- Stable MC rejected broad re-entry because it worsened 2020-crash and 2024 DD.

Next experiment:

Rebuild with explicit crash/stress guard: only permit re-entry when call demand is healthy, breadth/VIX are not crash-like, and recent execution is improving.

Stop rules:

- Any crash-window DD worsening is a no-ship.

### 21. SAW-Adjacent Put Admission Priority

Stage: Stage 3 portfolio.

Moved: unchanged.

Goal: if the desired behavior is more PUT admissions during corrections, build a mechanism separate from current SAW.

Evidence:

- Current SAW only rescales premium after a PUT already clears the allocator. It does not admit more puts.

Next experiment:

Mine correction states where missed puts had strong WR15 / option capture, then test admission priority or side-slot reservation. Keep current SAW unchanged until this proves independent value.

## Recently Resolved / Shipped

### TVDD — TRIN volume-flow neutral-band CALL DD dampener (SHIPPED 2026-06-07, portfolio-only)

Stage 3 portfolio, v70 Apex. The **4th orthogonal Stage-3 DD lever** (stacks on RXDD(VIX) +
SVR(skew) + MWDD(McClellan count-momentum) + F3F(breadth level)). Mining the full-lever MC tape's
**DD-active subset (dd≥0.13, where levers fire)**: the neutral TRIN band (~1.0-1.3 = balanced/mild-
distribution volume FLOW) is low-EV + DD-concentrated AND orthogonal — it survives the all-shipped-
levers-off slice at mpnl −0.060/z+57 (a **volume-flow-vs-breadth-momentum divergence**; the
"TRIN≈McClellan-redundant" warning is refuted for this band). TRIN extremes (froth/panic) are
mean-reversion winners, left alone by the Gaussian bump. c00: TRIN_C=1.042/W=0.268/DEPTH=0.426/
DD_MIN=0.291/VIX_PANIC=28. Phase D N=500×10 incl COVID T1-T7 PASS: **5y WorstDD −3.1pp AND compound
+17%, 2020_crash DD −8.3pp, 22-now compound +28%, collapse=0 every window** (Pareto). Wired across
all consumers; drift-guard 627 / registry 14. Record: `experiments/dd_residual_v70/`.

**Residual-DD-lever status after 4 levers (RXDD+SVR+MWDD+TVDD):** the regime-axis well is now
substantially harvested. The mining principle: a Pareto DD lever needs **low-EV AND high-dd_conc to
COINCIDE on an orthogonal, crash-robust cohort**, mined on the **DD-active subset (dd≥DD_MIN)**, not
the whole tape. After TVDD, the remaining residual low-EV cohorts are the diffuse complacent-bull bulk
(dd_conc ~1.0-1.2, not sharply concentrated) — contracting them = blunt global de-risking (capital-
velocity null) or crash-artifact. **Open micro-lead (LOW):** *VIX-velocity refinement of RXDD* — the
mine showed a clean monotonic EV split within the VIX band (rising=high-EV buy-the-dip, falling=low-EV
post-spike drift) that RXDD (level-only) doesn't exploit; gating RXDD's contraction by VIX velocity
(contract less when VIX is rising = the high-EV recovery) could be a Pareto refinement. Not a clean
parallel lever (the low-EV falling side has dd_conc ~1.0); validate as an RXDD modification, carefully
(could regress RXDD). **Closed nulls:** concurrency-DD is CAPTURED (old dd_ledger concur=hi 5.95× →
most-crowded band now LOWEST dd_conc 0.68); A-D-line-slope is McClellan-correlated (redundant).

### Broad 15DTE Router Sleeve

Stage: Stage 3 portfolio. Shipped 2026-05-28 as a portfolio-only DTE overlay; no `ALGORITHM_VERSION` bump.

Resolution: the first strict-gate ship was too narrow, so the broader low-knob router was promoted after N=500 confirmation. The shipped rule routes at most one 30DTE call signal per day to the 15DTE option path when `score >= 80` and `trend < 50`. VIX/regime filters, haven exclusions, and routed allocation score caps are disabled. The rule routes 117 5y signals (0.361% of trades) versus 41 for the prior stress sleeve.

Evidence:

- N=500 confirmation: `.codex/runs/dte_router_mc_trend50_n500_20260528_0026/mc_window_summary.csv`.
- 2022 mean log delta `+0.3876`, median `+0.4368`, worst DD `-4.34pp`.
- 2023 mean `+0.0332`, median `+0.0515`, worst DD `-2.32pp`.
- 2025 mean `+0.0416`, median `+0.0290`, worst DD `-0.31pp`.
- Dip mean `+0.0821`, median `+0.0743`, worst DD flat.
- 22-now mean `+0.0170`, median `+0.0180`, mean DD `-2.37pp`, worst DD `+1.54pp`.
- 5y mean `+0.0133`, median `+0.0182`, worst DD flat.

Do-not-retry lessons:

- Do not broaden the router into all high-score calls; raw `call_ge_80_daycap1` failed 2021, 2024, and 5y return/DD.
- `call_ge_80_daycap1_trend_lt_35` without VIX/regime/haven filters failed N=500 on 2025 return drag and 22-now DD `+1.25pp`.
- `trend_20_30`, `trend_lt_35_vix_25_35`, and raw `vix_ge_25` variants failed because the pocket either lost 2023 or leaked haven ETF behavior.
- `no_haven_etf_regime_50_85` failed 2022 DD and 5y mean until allocation score was capped at 80, but that fix reduced participation too far.

## Low-Priority / Do-Not-Retry Traps

These are not top leads unless a future run introduces genuinely new evidence.

- Broad hard removal of marginal 70-74 calls: failed portfolio path stability and often removed unfilled overflow, not real filled trades.
- Broad CMR / composite additive call admission: found selected-row signal but repeatedly failed stress-window DD.
- Symmetric/directional MACD widening above the put MACD 45 cliff: already exhausted across 24 variants; current cliff is the empirical optimum for that family.
- Rolling weekly indicator infrastructure: reverted and removed after catastrophic WR15 regression; revisit only if breakout/divergence push-band behavior is redesigned.
- Broad weekly composite / weekly volume / resonance boosts: useful weekly signal is phase-aware and inverted-U, not monotonic.
- Hard weekly maturity cliffs or high-WR candidates that win by collapsing 75+ N: not compatible with current N-aware scoring gates.
- Standing wider put SL: raw TP improves but capital velocity collapses outside bear years.
- No-op panic breadth guards: preserving DD by doing nothing is not alpha.
- **5th call-alloc DD-sizing lever (regime/breadth/volume/tier axes): the well is DRY after RXDD/SVR/MWDD/TVDD + F3F (2026-06-08).** Screen of the DD-active MC tape ruled out every remaining axis: NH/NL participation-quality FAILS the all-levers-off orthogonal slice (euphoria re-label), breadth-velocity is McClellan-redundant (corr +0.72), %above-EMA diffuse, VIX-velocity anti-aligned. The one orthogonal residual — the conviction **tier** (LOW 75-79 = 44% of DD-dollars at low EV) — built as DQT and **nulled at N=300×10** (the low tier is the velocity volume-engine + its low-EV-in-drawdown is crash-artifact-coupled; no Pareto). Do not re-mine these axes for a sizing lever; the EV/dd_conc anti-align = the dry-well signature (SKILL G21). The orthogonal seams left are **option-pricing** (direct option-chain skew — top lead above, data-blocked at 1.3y) and **model-fidelity** (lead A, historical-IV reprice). Artifacts: `experiments/dd_residual2_v70/{FINDINGS.md,mine2.py,sweep.py}`.
- **VIX weekly-MACD / momentum / acceleration as a STANDALONE DD lever (VXMD): TESTED NULL 2026-06-09.** The user's "VIX as a moving velocity/accel / weekly-MACD-crossover reading, not static level." The naive weekly-MACD resample is **within-week look-ahead** (fakes a 73%-of-DD-dollars low-EV cohort); point-in-time (daily-equiv EMA60/130/45 OR last-completed-week) the rising-momentum cohort is **HIGH-EV (+0.08..+0.10)** (the dead-hold bounce runners) and a **crash-artifact** (bear-2022 −0.17 / bull-2024 +0.09). MC screen N=100×8: no Pareto (cut 5y DD costs −17% compound, comp_2024 neg for ALL; DQT/G23 mode). Whole VIX-momentum axis dead as a portfolio DD lever — only the LEVEL band (RXDD) works. VXMD env-gated OFF in `monte_carlo.py`. `experiments/vix_weekly_v70/`. **NOTE:** the *separate* open micro-lead above — "VIX-velocity *refinement of RXDD*" (gate RXDD's level contraction by velocity, contract LESS when VIX rising = the high-EV recovery) — is a DIFFERENT mechanism (a modifier of the shipped level lever, not a standalone momentum contraction) and is NOT closed by this null; VXMD's finding (rising-VIX = high-EV) is actually consistent with that refinement's thesis.
- Static lower-N profiles below 16/4: can look good on average DD but break critical windows. Conditional controllers only.
- Direct daily-N / total-demand hard budget controllers: early tests caused replacement-path churn. Use smooth saturation, reserve ledgers, or state-conditioned capacity instead.
- F3F score-stage encoding: closed-null on per-trade scoring evidence; keep it portfolio-stage and surface via tradability indicators.
- **Re-grading/re-tuning the directional score or its exit barriers for the LIVE strategy** (miss-cohort dampers, global stop-loss tuning, mcap-conditional barriers, 70-74 selective promotion): swept 5 ways on the v70 apex15 HOLD barrier 2026-06-09 and ALL closed — the technical score has ~no edge on the 15-day option outcome (vol-path dominated; misses 0/42 concentrated; SL EV flat, tightening cuts eventual-winners; σ-scaling neutralizes mcap; no selective 70-74 lift target). **Never re-test these on the generic/`gen15` barrier** — the generic-vs-option trap inflates phantom cohorts (the exact reason the v60 MISS_CANDIDATES table looked actionable). Remaining scoring-side leverage is NON-directional only (option skew / realized-vol path). Record: `experiments/missretest_apex15/FINDINGS.md`.
- Trend/oscillator divergence dampening or lifting on raw stoch/rsi/trend: confirmed NULL on the OPTION barrier universe-wide (2026-06-03, holdout-locked). Overbought-in-uptrend calls (`trend≥90 ∧ stoch≤15`) CONTINUE — opt15 z=+0.16, and *better* (z=+1.80) when tighter; dampening deletes momentum winners. Capitulation dips (`trend≤45 ∧ stoch≥80`, N=310k) are BELOW the 75+ call base on every option barrier (opt15 z=−2.08) because the generic "price-up @15d" bounce trips the option SL before recovery. gen15-only signals here are the SVD/v42 generic-vs-option trap; the NVDA seed was a single-stock artifact. A 10-feature "reverts-vs-continues" rescue probe (overextension `pct_from_ema50/200`, regime, vol-climax, weekly-confirm, macd, bb, vol_signal) COMPLETED NULL (2026-06-03) — nothing splits the overbought-in-uptrend cohort at z≥3 on the option barrier; overextension is flat (momentum dominates). Thread fully closed; retry needs a genuinely new mechanism family, not these features. See `experiments/divergence_dampener/FINDINGS.md`, `experiments/nvda_analysis/FINAL_REPORT.md`.
- Per-stock score normalization / "bell curve" (z / percentile / cross-sectional re-threshold of `overall`): confirmed DILUTIVE on the option barrier, universe PIT (2026-06-03). Every scheme below the 75+ base (ps_z≥1.5 opt15 z−7.1 / apex15 z−14.9), worsening below 75; the mute-stock (max overall<75) bridge signals — the exact target — are the WORST cohort (z−7.4); and ps_z adds NO info beyond `overall` (WR flat across ps_z within a fixed band) so it reduces to threshold-lowering. The 32%-mute fact is correct risk-pricing, not a bug. **Lesson: re-shaping/re-grading `overall` is exhausted (divergence, per-stock knobs, normalization all null) — the open frontier is a NEW per-stock signal the score does NOT encode (relative strength vs SPY/sector, IV/skew, return path-quality).** See `experiments/score_norm/FINDINGS.md`.
- Relative strength vs SPY (stock_ret − spy_ret, 20d/60d) as a scoring/entry signal: NULL (2026-06-03, holdout, N=394k calls). Within the 75+ pool, RS quintiles are flat on the option barrier (top-vs-bottom opt15 z=−0.90); controlled for the stock's OWN 20d momentum, RS adds nothing (it's just price-momentum the score's `trend` already encodes); and what little signal exists is REGIME-FLIPPING (2022 leaders crushed z=−2.69, 2024 leaders continued z=+2.32). Don't re-test RS as a directional price signal. See `experiments/rel_strength/FINDINGS.md`.
- **Intraday/overnight return decomposition (the "SPY gains in market hours" arb) — NULL for the options strategy (2026-06-08, N=4,699 75+ calls + 1.93M-bar OHLC, holdout-era).** Three findings: (1) the famous anomaly is **index- and era-specific** — SPY is ~flat (slight overnight edge, intra−over −0.72 bps/bar); on OUR stock universe **overnight DOMINATES** (intra−over −7.04, −9.84 post-2020), the classic Lou-Polk-Skouras overnight premium, *opposite* of the user's intraday stat. (2) Trailing intraday/overnight *tilt as a directional scoring feature* is flat on apex15 (io_tilt z=+0.05, intra20 z=−1.36, over20 z=−1.83) — another price partition at ~47%/70%, exactly the rel_strength meta-lesson. (3) The only execution touchpoint — entry timing — is **unfavorable**: 75+ call signals gap UP **+17.5 bps overnight (t=+6.19)**, so the live next-open entry is **−1.22 pp of funded apex15 win-rate worse** than the backtest's signal-close entry (a realism HAIRCUT, not an arb). We hold 30-DTE options for weeks (span all intraday+overnight segments), so a buy-open/sell-close overlay is a *separate equity sleeve*, not a strategy tweak. Don't re-test intraday/overnight as scoring alpha or as a call entry-timing edge. See `experiments/intraday_overnight/FINDINGS.md`. **Short-DTE reframe also NULL (2026-06-08):** "capture the overnight via 3/5/7DTE/WR7 calls, buy-before-close, day-of-week aware" (`overnight_sleeve.py`, real theta+spread) is net-negative at every DTE even GROSS (30DTE-ov −4.3%, 5DTE −22.1%, 3DTE −41.8%), MONOTONICALLY worse for shorter DTE (theta/night ∝1/√DTE > gap-leverage), Friday/weekend the worst cell (3× theta), 0/75 cells net-positive @6% spread — buying any tradeable-DTE option across a +0.056σ overnight rents gamma at a theta loss. The structural edge is on the SELL side (theta harvest = short-gamma, out of mandate). **Two spin-offs parked:** (a) **equity-hold sleeve on signal stocks — TESTED 2026-06-08 (`equity_sleeve.py`): beats SPY risk-adjusted but LOSES to Apex.** Pure overnight is weak (+4.5%/yr unlevered); the edge is the 3-5d hold (short-horizon MOMENTUM): 5d unlevered Sharpe 1.25 / Calmar 0.51 / CAGR 19.6% vs SPY 0.81/0.40/13.7%, 0 collapse. But at Apex-matched ~84% DD (lever 3×) it does +7,039% 10y vs Apex's +16,953% (~2.4× less, Calmar ~0.64 vs ~0.80), and COLLAPSES at L≥8 (gap-down ruin) while Apex's defined-risk options never collapse — options are the better survivable-leverage vehicle. Possible separate lower-DD diversifier ONLY, but it's momentum-beta (benchmark vs MTUM, not SPY — untested) and correlated to Apex's momentum exposure. NOT an Apex replacement. See `experiments/intraday_overnight/FINDINGS.md`; (b) the **−1.22pp entry-realism haircut** is a backtest-honesty item (our MC/Portfolio anchor entry at the unattainable signal close → ~1.2pp optimistic on call WR; cushioned by the dead-hold) — a user-decision methodology question, adjacent to Lead A (forced-exit reprice).
- **Fama-French factor tilts (MKT beta / SMB size / HML value / RMW profitability / CMA investment) as scoring or portfolio inputs — TRIAGED CLOSED-BY-EXISTING-EVIDENCE (2026-07-10, video-inspired user lead; null-check only, no new compute).** FF explains long-horizon cross-sectional expected returns of DIVERSIFIED portfolios (the famous R² is co-movement explanation, not timing); the premia are ~3-4%/yr ≈ tens of bps per 15d — invisible against the vol-path-dominated option barrier where every price partition lands ~45-50% (strategic framing below). Per-factor mapping to existing closures: **MKT** — the market/regime axis is saturated (regime multiplier + RXDD/MWDD/BDIV/TVDD; SPY-trend weight 0 in the composite; A-MKT flat/chop contraction NULL 2026-06-25 = MWDD-redundant) and a beta-stripped decomposition already exists (`experiments/regime_call_alpha/FINDINGS.md` — the call edge is NOT beta); **SMB** — size-in-scoring is the MCD axis, RETIRED v71 as survivorship on PIT mcap (8.2pp ladder → 2.6pp non-monotonic; `experiments/integrity_audit_2026_06/`), and per-mcap TP/SL is closed by σ-normalization (mfe15σ ratio 0.99, lead #17 above); **HML/RMW/CMA** — untested here but land in the gameplan §4 "new non-option inputs" HOLD class (same vol-path wall), and are DATA-GATED: yfinance fundamentals are current-snapshot-only = the exact look-ahead/survivorship class the F4 PIT-mcap fix killed — any retry needs a filing-dated point-in-time fundamentals substrate (e.g. Sharadar SF1, sibling of the P2.1 SEP buy) + a cheap cohort-z on the apex barrier, post-N3 only [N3 RESOLVED 2026-07-14 — gate lifted, see `.claude/docs/gameplan.md` §2b/6b], expectation low; **FF-residualized (beta-weighted) momentum** — a rephrasing of the closed rel_strength axis (NULL, N=394k, entry above), not a new mechanism class; no escape story from the vol-path wall. The genuinely transferable FF content — "check whether an apparent edge is just factor exposure" — is ALREADY APPLIED: the v69-honest D1 diagnostic (market-adj stock-selection alpha +1.03%/15d, t=1.25, mostly momentum-beta; `known-issues.md`) is this system's FF verdict on itself. Residual open sliver (diagnostic, not alpha): benchmark the strategy/equity-sleeve against an investable momentum factor (MTUM) rather than SPY at the Dec-2026 OOS read (P1.6) — flagged untested in the equity-sleeve note above.
- **Entropy-rate / conditional-entropy / series-complexity ("Voynich structure-vs-noise" diagnostic) of price or score series as a noise filter or signal-strength conditioner — TRIAGED CLOSED-BY-EXISTING-EVIDENCE except one parked sliver (2026-07-16, user lead from Voynich-manuscript entropy reading; null-check only, no new compute).** The diagnostic (how much next-symbol/outcome uncertainty is consumed by conditioning) is mathematically the mutual-information audit this system already runs, and the measured magnitudes are why it cannot work as a language-style filter here: a strong tradable edge (50%→65% band WR) carries ~0.066 bits/trade vs the 0.5–1.5-bit gaps character-entropy methods discriminate, and the measured within-gate score resolution is potential-BSS ~0.0002 (`experiments/verify_value/FINDINGS.md` — the score→outcome channel above the 70-gate carries ~10⁻⁴ of the outcome's uncertainty; the information is GATE MEMBERSHIP, not the gradient). Per-form mapping: **(a)** "filter noisy names by price-series entropy" — a price-only per-name conditioner: the 47% wall (strategic framing below) + the 349-cell MA-lattice comprehensive null (2026-07-14, don't re-mine price-only) + per-name estimation noise (~1.25k daily samples vs alphabet^k contexts) + return-entropy content is mostly vol clustering ≡ the shipped-and-DRY vol/regime lever axis; **(b)** "filter noisy score moves" — the score series is smooth by construction (its conditional entropy measures the RSI/MACD/EMA smoothers, not meaning); the actionable form IS the fakeout program: WCF ramp shipped v72 (fakeout groups −60%), INTRADAY_TYPE_CONF_GATE, residual CLOSED NULL 2026-07-10 at 0.26–0.35% ≪ 5% floor (`experiments/miss_regime_fakeout/VERDICT.md`; Layer-B intraday family parked to Dec-2026 OOS, bars locked); **(c)** "strengthen signals by information content of score→outcome" — already operational as the Verification Substrate skill-vs-baseline 0d gate (weatherization.md), and anything that ACTS on it (entropy-weighted recalibration/reweighting) is the DEAD `Score.overall` re-shaping class (gameplan §4); **(d)** component-level noise attribution — exists as the per-member apex-EV attribution that pruned v73/v74 plus the pack-time `components_scorecard.json` panel (P1.8), READ-ONLY by charter. **Parked sliver (the only retry): per-name return-PATH-complexity (permutation entropy / Lempel-Ziv / Kaufman-efficiency family) as a funded-ledger cohort conditioner** — literally the "return path-quality" item in the score_norm open-frontier list, the one of its three never directly mined (RS nulled 2026-06-03; IV/skew ran as OSK). Coherent escape story from the 47% wall (it targets vol-STRUCTURE, which dominates the barrier outcome, not direction) but LOW prior: must beat (i) per-trade redundancy with shipped vol/regime state (SVR/RXDD), (ii) the mcap/liquidity confound (complexity correlates with size — the retired-MCD survivorship trap; PIT-mcap control mandatory), (iii) per-name estimation noise (use cross-sectional rank form). Retry = ONE pre-registered feature column through the `experiments/peak_fakeout/mine.py` CR1 clustered-z harness on the holdout-locked funded ledger (z≥3 bar, mcap + vol controls), ideally post-Dec-2026-OOS per gameplan §3. **RESOLVED 2026-07-16 — the user green-lit the immediate retry and it RAN as `experiments/wave_cycle_mine/`** (joint W calendar-periodic + S path-structure families, 29 cells, pre-registered 5-leg bar incl. a fresh 2016-2020 era mirror for W). **W = comprehensively null** (17 cells: OPEX-cycle/TOM/DOW/month-half/quarter-end all |z_clust|<2.0; the 31%-Friday entry concentration is a weekly-cadence supply artifact, not alpha) — calendar axis CLOSED. **S = the sliver is REAL: S1_vr5 (5-day variance ratio) is the only 5/5-leg survivor of the July-2026 mines** (choppy/anti-persistent entry paths +2.79pp WR / −3.45pp plunge; trending paths the mirror; plunge z_clust −3.96/+3.53 sign-stable 5/5 windows; orthogonal to vol20/runup/PIT-mcap; corroborates the parked peak_fakeout TEXTURE mechanistically) — **but BELOW the locked actionability floor** (T1 d_ev +0.0298 vs the 0.03 bar, a 0.0002 miss recorded as such; no cohort near BE45; WR leg 2022-inverts = G26 caution; perm_entropy/lz76/kaufman standalone null). **PARKED to the Dec-2026 OOS re-read, bars locked** in `experiments/wave_cycle_mine/FINDINGS.md` §Park (OOS plunge |z_clust|≥2 same-sign AND pooled d_ev≥0.03 → license an SVR-class per-signal Stage-3 probe; flip/attenuate → close permanently). Do not build early; do not re-mine W-calendar or the other S statistics.

### Strategic framing (2026-06-04, the alpha-hunt meta-lesson)

The 2026-06-03/04 hunt closed 5 directional/price-feature families (divergence ×2, per-stock knobs, per-stock normalization, relative strength) and surfaced ONE real edge (option skew). The unifying finding: **opt15 WR15 is ~45-50% for essentially EVERY price-technical partition** (absolute tiers, divergence cohorts, normalized signals, RS quintiles) — the directional signal explains almost none of the 15d option-TP-before-SL outcome, which is dominated by the realized VOL PATH, not direction (consistent with the v69-honest "stock-selection alpha is statistically thin" finding). **Implication: stop hunting directional PRICE signals for scoring alpha — they all land at ~47%. The leverage is (a) OPTION-PRICING signals (skew/semivol — the one thing that hit, because the realized-vol barrier is blind to premium) and (b) the PORTFOLIO/execution layer (where Apex's HOLD/exposure/dead-hold work gave real MC gains). Re-grading `overall` is closed.** Full hunt record: `experiments/{nvda_analysis,divergence_dampener,score_norm,rel_strength,iv_skew}/`.
- Research-only CT or reserve features: do not ship them without production feature sourcing or live ledger semantics.
- Portfolio-only mechanisms described as scoring versions: Sentinel/Core/Apex are profile overlays, not `ALGORITHM_VERSION` rows.

### Apex 50k→200k sizing/exit/regime levers — COMPREHENSIVE NULL (2026-06-04/05 overnight /research)

A full overnight sweep of "can we go faster / be more aggressive for the early-stage book" closed every lever. **v70 Apex is at a robust, well-tuned local optimum; aggression is falsified.** Harness `experiments/apex_speed_v70/` (FINDINGS.md). The governing law: drawdown-avoidance = return-max (capital-velocity); every lever that adds exposure or holds positions longer nulls or collapses. Do NOT re-open without a fundamentally new mechanism:

- **Static / dynamic 100% exposure**: cap scan {0.35..1.00} peaks at the current 0.50; **static 100% collapses 10% AND compounds slower** (marginal calls above 50% are low-EV crowding). Falsified.
- **EXR (size+VIX+DD-gated hot exposure = "run 100% early")**: built env-gated in monte_carlo (off=byte-identical); null at N=300×8 — the bull lift doesn't survive non-bull tape (velocity penalty + gating); un-gated collapses. Env-OFF, preserved.
- **DD-band re-tune**: Phase-C "winner" (FLOOR 0.44) collapses 0.2% at the N=500 ship-gate; collapse-safe neighbors within MC noise → no confirmed ship. (dd07b 0.35/0.525/0.42 = OPTIONAL marginal Pareto-non-neg candidate.)
- **Trailing stop (TSL = "let high-conviction winners run", honest-v70 low-hydration reframe)**: built env-gated; **RE-CONFIRMED NULL, decisive** — trail 75+/all = comp −6 to −7 + **100% collapse** (held winners cluster+crash); even trail-85+-only ≈ −70% compound. The freed slot's overflow refill compounds more than holding the winner. Capital-velocity is ironclad; the low-hydration reframe is falsified. Env-OFF, preserved.
- **Regime-adaptive TP/SL on the HOLD core**: null — stress-WIDER TP (the proven pre-v70 `brd_TP30/35` +44% shape) **BACKFIRES** (HOLD + dead-hold subsume it); calm-tighter barriers give bull speed but worse DD/bear/worst-window. Flat TP0.30/SL−0.70 near-optimal.
- **Regime multiplier "tighten"** (Stage-1): read-only 5y A/B shows current (B) is optimal-or-best on 90+ CALL (WR15 77.9/EV30 2.92); no-regime −15.5pp, wider worse → no headroom for a tighter re-tune. Well-calibrated on honest v70.
- **Signal-pool displacement / realloc** (cut a loser for a golden signal; cross-side): documented null (REALLOC_STRATEGY v32 N=300×8) + **conflicts with the collapse-preventing dead-hold** (cutting deep losers in drawdowns = the collapse mode) + **cross-side moot** (puts off in all v70 profiles). Cheap gating diagnostic before any retry: measure how often a 90+/95+ signal is skipped due to a full book.
- **Sector-ETF regime lever**: source CSV (`.cache/sector_etf_screen/sector_breadth_daily_2020plus.csv`) MISSING from cache (SAW silently falls back to neutral) + Priority-#13 null. Needs data rebuild before testable.

**Methodology gotcha reinforced:** a Phase-B screen WITHOUT a COVID window hid a collapse that only showed at Phase C/D — never rank a screening phase without `2020_crash`.

**DD-reduction lever that DID ship (2026-06-05 MWDD, `d79f8a144`):** the breadth-momentum FLAT/topping band (McClellan≈0) is a genuine low-EV + DD-concentrated cohort orthogonal to RXDD(VIX)+SVR(skew)+F3F(breadth-level) — shipped as MWDD (N=500×10 incl COVID: 5y −2.6pp / 22-now −5.5pp WorstDD, every window DD down, collapse=0, compound flat; `experiments/market_wave_dd_v70/`). Generalizing the RXDD/MWDD pair: a regime axis's MID/topping band is the shippable DD-driver, its CRASH extreme is a mean-reversion WINNER (contracting = crash-artifact trap) → pair any mid-band contraction with a panic-exclusion. This also **resolves the sector-ETF-data blocker** above: rebuild the full-history Market Wave via `market_breadth._load_sector_etf_breadth_rows()` (self-contained from SPDR prices), or use `MarketBreadth.mcclellan_oscillator` (full history) directly. Remaining adjacent axes to probe (likely McClellan-correlated → expect redundancy): breadth-velocity, TRIN, A-D-line slope.

**Open sliver-lead (LOW, needs validation):** tighter-calm-barriers (`TP_BASE 0.28 / SL_BASE −0.60 / breadth_thresh 45`) was the night's ONLY positive-speed signal (+0.177 on the speed windows = faster bull recycling, the velocity instinct) — but +6.7pp DD, −0.25 bear, −0.48 worst-window, and COVID-collapse untested. A DD-relaxed early-stage book MIGHT accept it; needs an N=300×8 incl-COVID validation before it's a real lead. Driver knobs already exposed (no engine change).

**Also removed (user-directed 2026-06-05):** `CALIBRATION_CUTOFF_DATE` → None (holdout lock disabled; `experiments/_holdout` no-ops on None) — sweeps may now use current-regime data.
