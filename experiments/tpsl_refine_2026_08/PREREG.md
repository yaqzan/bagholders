# PREREG — TP/SL blast-radius refinement, Core + Apex (2026-08-10)

STATUS: **LOCKED 2026-08-10** (git-committed before any Phase A outcome was viewed).
Recon amendments to §2 artifact guard, §5/§5b feasibility conditionals, and §7 execution
mechanics are folded in below — all from code-contract recon, zero outcome data seen.
Metrics, lanes, and grids may NOT change after Phase A starts.

## 0. Hypothesis + honesty header

- H1: the live joint (TP=+30%, SL=−70%) cell is not the optimum of the CURRENT engine
  (calendar-hold, seeded bounded fill, ~3% spread, repaired substrate, v74) for Core
  (DD-primary) and/or Apex (2x-race) — because TP30 and SL−70 were each locked in
  different, earlier eras and the joint surface was never swept on this stack.
- H2: regime/market-wave-conditional TP/SL (stress-band offsets keyed on ALREADY-SHIPPED
  participation/vol signals) adds DD reduction or compound at bounded DD, via the existing
  TP_STRESS/SL_STRESS machinery (currently inert: base==stress).
- Hypotheses tested (declared up front): Phase A ~160 cells, B ~≤60, C ~≤96, C2 ≤24,
  D ≤8 finalists. Control for multiplicity = escalating N (150→300→500), paired seeds,
  window-family replication, pre-registered acceptance lanes. No cell not surviving
  N=500×12-window with the §3 thresholds may be claimed or shipped.
- Prior evidence honestly stated: three adjacent axes are CLOSED (dynamic count-based
  TP/SL null 2026-05-09; cut-to-redeploy null; "DD-sizing well dry" 2026-06-08). The
  2026-04 regime-TP finding (stress-loosen TP wins) predates the honest engine and was
  flattened at the HOLD-core ship. Prior favors "plateau at incumbent" — hence the
  explicit stop rule in §3.

## 1. Baselines (live, verified 2026-08-10)

Both profiles: TP_BASE=TP_STRESS=+0.30, SL_BASE=SL_STRESS=−0.70, BREADTH_THRESHOLD=40
(inert), hard-sell day 15, calls-only, CALENDAR_HOLD on, 30-DTE engine.
Core: cascade 0.20/0.15/0.08/0.03, gross 0.5, MaxPos 14. Baseline cert: 5y DD 61.7%,
22-now DD 61.5%, 5y med +1,247.9%, collapse 0 (N=500×10).
Apex: flat 0.10, MaxPos 10, gross 1.0, HOLD_CAL_DAYS=27. Cert: P(2x) 72%, med ~191d,
worst DD 76%, collapse 0/12.
Baseline arms are RE-RUN inside every phase with the same seeds/windows as candidates —
never compared against remembered cert numbers.

## 2. Phase A — blast (coarse coverage)

Grid per profile (stress=base, i.e. flat, in this phase):
- TP ∈ {0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65, 0.85, 1.10}  (10)
- SL ∈ {−0.30, −0.40, −0.50, −0.60, −0.70, −0.80, −0.90, −1.00}      (8)
- 80 cells/profile; SL=−1.00 ≡ dead-hold (SL cannot fire; hard-sell/expiry governs) —
  direct test of the dead-hold law at every TP.
- Windows: 2022, 2024, 22-now, 2020_crash. N=150. Paired seeds (identical label strings).
- TP ceiling +1.10 (not +4.00): beyond ~1× premium gain we enter the parked OTM-lottery
  axis (collapsed OOS 2026-07-20) — not reopened here.
- SL floor −0.30: tighter SLs historically manufactured fake wins via same-stock re-entry
  cycling. ARTIFACT GUARD — RESOLVED by recon 2026-08-10: current engine blocks same-symbol
  re-entry while any position is open (`run_single_sim` open_syms filter, monte_carlo.py:3274
  + all candidate comprehensions). Tight-SL cells are honestly testable; no quarantine.

Prune A→B (mechanical, applied by driver): drop any cell with collapse>0 on ANY of the 4
windows (both profiles — Apex's cert standard is 0/12). Carry to Phase B: the Pareto-
nondominated set on (22-now WorstDD, 22-now median compound) ∪ top-12 by 22-now WorstDD
∪ incumbent cell + its 4 grid neighbors. Cap 15 cells/profile (truncate by 22-now DD).
Apex ranks on (worst-of-4-windows WorstDD, 22-now median compound) Pareto instead.

## 3. Metrics, acceptance lanes, stop rule

DD-primary doctrine. Compound is T7 sanity (±3 OOM), never the ranking key alone.

Core acceptance lanes (Phase B → candidate; confirmed only at Phase D N=500×12):
- LANE-DD: 5y WorstDD ≤ baseline −1.5pp AND 22-now WorstDD ≤ baseline +0.5pp AND 5y
  median compound ≥ 0.75× baseline AND no annual window WorstDD regression >5pp (T5).
- LANE-GROWTH: 5y median compound ≥ 1.5× baseline AND 5y WorstDD ≤ baseline +0.5pp AND
  22-now WorstDD ≤ baseline +1.0pp AND T5.
- Always: collapse=0 on every window incl 2020_crash (hard, both lanes).

Apex acceptance (final read on the 2x-race harness, Phase D): P(2x) ≥ 72% AND worst DD
≤ 76% AND median cal-days-to-2x ≤ 200 AND collapse 0/12, with ≥1 of the first three
strictly better than the live cert (else it's a tie → no ship).

Noise floors (paired-seed): DD claims need ≥1.5pp at N=500; compound claims need ≥30%.
Anything smaller is "noise-indistinguishable" by definition here.

STOP RULE: if after Phase B no cell enters a lane, skip C2, run Phase C on the incumbent
(TP30/SL−70) as the base pair only; if Phase C also produces no lane entrant, Phase D
runs nothing but a baseline re-cert and the verdict is CONFIRMED-OPTIMAL. No grid
extensions, no new lanes, no metric swaps after outcomes are seen.

## 4. Phase B — refine

Fill ±0.05 TP / ±0.05 SL gaps around carried cells (dedup; ≤30 new cells/profile).
N=300. Windows: 2021, 2022, 2023, 2024, 2025, dip, 22-now, 5y, 2020_crash (9).
Baseline re-run in-phase. Rank per §3 lanes.

## 5. Phase C — regime / market-wave conditional TP/SL (pre-registered signal set)

Applied on the Phase-B winner pair per profile (or incumbent per §3 stop rule).
Signals (ONLY these — all already-shipped, participation/vol-anchored series):
1. Breadth stress band — existing engine machinery: BREADTH_THRESHOLD ∈ {40, 50};
   TP_STRESS = TP_B + {0, +0.05, +0.10, +0.15}; SL_STRESS = SL_B + {0, −0.15, +0.10}
   (clamped ≥ −1.00). 2×4×3 = 24 cells/profile.
2. MWDD band active (McClellan flat/topping) — same offset menu on (TP,SL): 12 cells.
3. RXDD VIX band active — same menu: 12 cells.
4. Regime multiplier < 1.0 band — same menu: 12 cells.
   (2-4 CONFIRMED feasible by recon: all per-day maps — mcclellan/vix/trin/bdiv — are
   already loaded once per window in `_prepare_window`; conditioning TP/SL on them means
   threading the existing maps into `precompute_outcomes`/`compute_trade_outcome`,
   mirroring the shipped breadth_map/is_stressed pattern. In-process patch only — no
   production file edits. Stress classification is fixed at ENTRY date, matching the
   shipped breadth mechanism.)
CLOSED-AXIS GUARD: no signal-count/density/queue-depth classifiers (2026-05-09 null —
quiet-tape days are not high-vol days). No new composite signals invented mid-phase.
CLARIFICATIONS locked 2026-08-10 before any Phase B/C outcome was seen:
- Predicates are MARKET-STATE-ONLY at entry date. The shipped MWDD/RXDD/TVDD levers are
  DD-gated (active only when running portfolio DD ≥ *_DD_MIN) — a path-dependent state
  that cannot classify a signal at precompute time. Phase C uses the band conditions
  sans DD-gate: MWDD = McClellan flat/topping band INCLUDING its VIX-panic guard;
  RXDD = the VIX Gaussian band condition; regime = regime_multiplier(on-or-before) < 1.0.
- TVDD/BDIV are NOT run (their cells were never enumerated in this section).
- Identity-offset cells (stress≡base) are injection-validation only (must reproduce the
  unpatched engine bit-exactly under paired seeds), never evidence.
- The flat Phase-B winner pair is re-run in-phase as the paired baseline row.
N=300 × 5 windows (2022, 2024, 22-now, 5y, 2020_crash). Lane entry per §3 (evaluated on
the 9-window set for any cell that passes the 5-window screen).

## 5b. Phase C2 — per-tier TP/SL (conditional)

Condition (a) is CONFIRMED by recon: `precompute_outcomes` already reads `sig.overall` at
the exact call site where barriers get fixed (monte_carlo.py:2825), so per-tier TP/SL is a
surgical in-process patch. C2 therefore runs ONLY on condition (b): Phase A/B shows the
Core (cascade-weighted) and Apex (flat) optima diverging in a direction consistent with
tier composition. Cells: TP for ultra/top vs
mid/low at winner ± 0.10, SL shared. ≤12 cells/profile, N=300 × 5 windows. Else PARKED.

## 6. Phase D — confirm + ship decision

Finalists (≤3/profile) + baseline. N=500, all 12 canonical windows. Formal T1-T7 read.
Additional required evidence on each finalist:
- Survivorship arm: re-run 2022 + 22-now with the honest delisted-inclusive universe
  (MC_UNIVERSE_FILE, path per recon). Edge that evaporates on the honest arm = FLAG →
  escalate to orchestrator, no autonomous ship.
- backtest_cascade deterministic parity: winner vs baseline single replay, sanity that
  MC direction reproduces chronologically.
- Apex finalist: full 2x-race harness (P(2x), median cal-days, collapse) vs live cert.
- AMENDMENT 2026-08-10 (tightening-only, added post-Phase-A, selection-neutral): each
  finalist AND the baseline also re-run under a moderate fill-pessimism probe
  (TP_FILL_MISS_P=0.10) on 22-now + 5y; report deltas. A finalist whose edge over
  baseline inverts under the probe = FLAG (escalate, no autonomous ship). Rationale:
  audit showed engine EV is concentrated in intrabar barrier fills (close-confirm
  collapses baseline and candidates alike); the probe checks the DIFFERENTIAL stays
  fill-robust. Lanes/metrics/grids unchanged.
Ship rule: clean lane pass + all extra evidence green → ship autonomously via
/ship-portfolio (user authorized autonomous proceed). Any FLAG, any tradeoff requiring
judgment (e.g. DD win at >25% compound giveback), or any gate waiver → present, don't ship.
Evaluation date: when Phase D completes (expected within days of 2026-08-10).

## 7. Execution mechanics (recon-amended 2026-08-10, before Phase A)

Cost model (recon): TP/SL barriers are baked per-signal inside `_prepare_window`
(`compute_trade_outcome` → `precompute_outcomes`, monte_carlo.py:2400-2571/2817-2853/4371)
— each (TP,SL) cell needs its OWN prepare; `_apply_cell_params` (sizing-only) does not
cover TP/SL. Consequences, all mandatory for the driver:

- **Loader memoization:** wrap `mc.load_signals` / `load_price_history` / breadth/regime/
  mcclellan/trin/vix/bdiv map loaders with an in-process cache keyed on (window, version)
  so MySQL is hit once per window, not once per cell. In-process wrapping only.
- **Put-side skip:** puts are OFF in both profiles (put tiers 0 / put_max 0). Skip
  `precompute_put_outcomes` (in-process stub returning the empty structure) — put signals
  are ~2.5× the call count on long windows and pure waste. Smoke must verify zero put
  entries occur and nothing downstream crashes on the empty structure.
- **Sigma re-derivation:** `compute_trade_outcome` reads import-time floats
  `TP_SIGMA_BASE/STRESS`, `SL_SIGMA_BASE/STRESS` (derived at monte_carlo.py:501-504), NOT
  `TP_BASE`/`SL_BASE`. The driver's `set_tpsl()` must re-derive EVERY import-time
  derivation of TP/SL (sigma + any NET_* aliases — grep the 495-510 region and mirror the
  chain exactly). Smoke acceptance: TP=0.15 vs TP=1.10 cells MUST produce different
  tp-rates/outcomes, else the patch is a silent no-op and the run is invalid.
- **Fresh MP pool per cell:** `_make_window_pool` ships baked outcomes to workers via
  pickled initargs at pool creation — a pool created before a cell's re-prepare holds
  STALE barriers. Never reuse a pool across TP/SL cells; create after each prepare (or
  run MC_NO_MP in-process). SLIP_* are read live in workers but are never varied here.
- **Frozen recipe pins on every run:** `MC_NO_DB_PERSIST=1`, `LIQUIDITY_FLOOR=0.0`
  (explicit, so a future default-enable can't shift results), `ALGORITHM_VERSION_PIN`
  = the active version id recorded at Phase A start (v74), `PYTHONIOENCODING=utf-8`
  (queue stdout blindness), stress=base in Phase A.
- **Apex arm env** (from experiments/apex_dte_dd/run_p03_evidence.py): NOMINAL_CAL_DTE=30,
  HOLD_CAL_DAYS=27, TIER_ULTRA/TOP/MID/LOW_OV=0.10, TIER_OVERFLOW=0, MAX_POSITIONS_OVERRIDE=10,
  MAX_POSITIONS_CALL=10, GROSS_PREMIUM_CAP=1.0, CALL_PREMIUM_CAP=1.0 — the premium caps
  MUST be explicit (concentration_2x's cell_env omits them and silently inherits 0.50).
  Core arm = strategy defaults (gross 0.5, cascade 0.20/0.15/0.08/0.03, MaxPos 14).
- **Paired seeds** via identical window LABEL strings across all arms/cells.
- **No production file edits, ever** — all variant behavior via in-process monkey-patching
  inside the driver process. monte_carlo.py / strategy_config.py stay byte-identical.
- All sweep compute via `trader queue submit --priority high --db light --restartable`
  (+ `--window off_market` if submitted during market hours), jobs partitioned
  (profile × window subsets) to target ≤ ~12h wall for Phase A; driver resumable via
  per-job state JSON (atomic writes) under driver/state/; results to out/*.csv +
  per-iteration return summaries (median/p10/p90) — raw per-iter arrays to a compact
  parquet in out/ for later analysis; bulk logs to logs/.
- **Survivorship arm (Phase D):** MC_UNIVERSE_FILE mechanism verified inert-by-default;
  arm files under experiments/survivorship_decomposition/ (e.g. survivor_universe_811.txt);
  require the `[universe-filter]` engagement line in the log when set.
- Token economy: orchestrator (Fable) designs/reads phase summaries only; builder =
  Sonnet subagent from this spec; raw logs never enter context. Builder submits queue
  jobs and reports IDs; the ORCHESTRATOR owns all cross-turn queue watches (subagent
  queue-wait orphan trap).
