# LESSONS — append-only

- 2026-08-10 (recon, before any run): TP/SL cells CANNOT share a `_prepare_window` ctx —
  barriers bake at prepare (monte_carlo.py:2450-2513, 2817-2833). Sizing-only sweeps get
  the load-once discount; TP/SL sweeps do not. Budget cells × windows prepares; recover
  cost via loader memoization + put-precompute skip (puts OFF both profiles).
- 2026-08-10 (recon): patching `mc.TP_BASE` alone is a SILENT NO-OP — the walk reads
  import-time `TP_SIGMA_*`/`SL_SIGMA_*` (:501-504). set_tpsl() must re-derive the whole
  chain; smoke must prove extreme cells diverge.
- 2026-08-10 (recon): MP pool initargs pickle the baked outcomes at pool creation —
  reusing a pool across TP/SL cells feeds workers STALE barriers. Fresh pool per cell.
- 2026-08-10 (recon): same-symbol re-entry IS blocked in current engine (:3274 + filters)
  — the historical tight-SL fake-win artifact is dead; tight-SL cells honestly testable.
- 2026-08-10 (recon): monte_carlo.py's own module docstring prints STALE param claims
  (TP35/SL-35 era). Never read header text as config; read the derived NET_* lines.
- 2026-08-10 (builder): _simulate_window builds+tears down its own fresh Pool per call
  when no pool= is passed (sized min(MC_WORKERS, N_ITER)) -- so "fresh pool per cell" is
  free via the default path + MC_WORKERS env cap. At N=20, sim wall (~5-6s/cell) is
  dominated by pool spawn/teardown, flat across window sizes; N=150 scales sub-linearly.
- 2026-08-10 (builder): tp_rate/sl_rate/hard_rate in phaseA CSVs are the DETERMINISTIC
  baked outcome-classification rates from ctx['call_outcomes'] (N-independent), not
  portfolio-realized fill rates; med/p10/p90/worst_dd/p_coll are portfolio-realized.
- 2026-08-10 (Phase A read): tight-TP corner (TP0.15, any wide SL) dominates BOTH profiles
  by huge margins (Core 22-now DD 41.9% / med +10,418% vs incumbent ~52%/+600-class).
  SUSPICION registered before Phase B: 2026-04 sweeps found TP20 catastrophic under
  entry=-1% slip; current config has SLIP_ENTRY=0.0 + free limit-TP -> per-cycle friction
  ~0, which structurally flatters high-turnover tight-TP cells. Audit dispatched (cash-math
  trace + friction sensitivity + close-only-TP arm) BEFORE refining the corner. Also:
  high-TP cells (>=0.50 Apex, >=0.85 Core) mass-collapse -> real physics, prune stands.
- 2026-08-10 (spec note): PREREG carry-cap truncated the Core incumbent out of the carried
  list; PREREG SS1 already mandates baseline re-run in every phase, so Phase B includes the
  incumbent as its paired baseline arm regardless of the carry list.
- 2026-08-10 (audit, tasks #328-330): tight-TP suspicion RESOLVED = relative ranking is
  honest. (1) No friction crossover to f=0.03/round-trip (2x worst FF-2 measured real
  spread): TP15/SL-90 > TP30/SL-70 at every f; at f=0.03 the INCUMBENT goes negative
  while TP15 stays +700%. (2) Turnover ratio only 1.22x (683 vs 558 tr/yr; med hold 3d vs
  4d); TP15/SL-30 at 1.64x performs WORSE -> mechanism is fast-winners+never-realize-
  noise-losses, not raw cycling. (3) Close-confirmed-TP arm collapses BOTH cells to -80%/
  100% p_coll nearly identically -> wick-fill dependence is ENGINE-WIDE, differential-
  neutral; per-trade edge is razor-thin (~0.7%/cycle at ~600 tr/yr). Absolute magnitudes
  carry the known intrabar-fill optimism (real-ledger 2026-07-25 quantified ~3.2pp TP-rate
  optimism vs 3,339 real paths); selection stays differential-based.
- 2026-08-10 (cross-experiment, TP-fill fidelity MEASURED): the §6 fill-pessimism
  probe value is now graded by experiments/tp_fill_fidelity_30dte/FINDINGS.md
  (N=2,257/2,688 matched real-contract events, queue #347): same-day real fill on
  engine-declared TP days is only 48.9% (TP30) / 52.3% (TP15) — but economic
  never-fill (resting limit, 27-cal-day window) is 15.8% / 14.1%; late fills median
  4 cal days at the same limit price. The locked 0.10 probe UNDERSTATES the
  measured economic 0.15 (modestly) and the 0.50 mechanical bound (grossly).
  Recommended tightening-only Phase D amendment: add a TP_FILL_MISS_P=0.15 arm
  beside 0.10. Differential comfort unchanged (close-confirm arm was harsher and
  both cells survived it identically); the calibration matters for ABSOLUTE claims.
  Also measured: default Uniform(barrier,high) TP fill credits ~1.5x the real
  limit-mechanics improvement (gamma=46.5% of fills gap/first-print above barrier,
  +7.0pp real vs +10.6pp credited) — GAP_AWARE is the honest middle.
- 2026-08-10 (audit): "~3% spread" in portfolio_profiles.json evidence is a STALE LABEL
  (written a5db6ddc 2026-06-02 14:08 under the 5-hour-lived symmetric slip model;
  superseded same day by db89ea09 asymmetric canon). No live path charges 3%; max is
  -1.5% on forced exits. Fix label at closeout. Also: TSL trail inert (TSL_ENABLED False),
  TP_FILL_MISS_P=0.0 (miss -> fill range widens to [low,high] + forced-exit slip),
  TP_FILL_AT_BARRIER / TP_FILL_GAP_AWARE knobs exist wired-but-inert (mc:102,105) --
  moderate fill-robustness probes for Phase D.
- 2026-08-10 (Phase B read, tasks #331-338): tight-TP region beats incumbent broadly --
  28/30 Core cells enter BOTH lanes; lane thresholds were weak discriminators at B, real
  selection = Pareto/dd_5y. Core finalists (0.10,-0.60) dd_5y 38.7 / (0.10,-1.00) 40.4 /
  (0.10,-0.90) 41.6 vs baseline 52.8 (med_5y +100k-161k% vs +1,201%). Apex sole Pareto =
  (0.10,-0.60) worst-of-9 63.9 vs 70.1. SL nearly flat at TP10 (dead-hold law re-confirmed).
  CAUTION: optimum sits AT the pre-registered TP floor (0.10 = 0.364 sigma barrier),
  gradient unexhausted below -- prereg forbids extending; FINDINGS must state boundary
  status; executability of a 0.364-sigma wick barrier is exactly what the SS6 fill probe +
  the spawned Polygon 30-DTE fidelity task interrogate. Baseline N=300 med_5y +1,201%
  agrees with cert +1,247.9% (N=500) -- engine parity sane; baseline dd_5y 52.8 vs cert
  61.7 is at the edge of the N=300 DD noise band, in-phase pairing makes it moot.
  Apex incumbent med_22now -38% on buy-hold-forever read is EXPECTED (Apex is a stop-at-2x
  sprint, not held; real Apex read = Phase D 2x-race harness).
- 2026-08-10 (survivorship arm semantics, from recon Q7 re-read): DEFAULT MC (no
  MC_UNIVERSE_FILE) = full PIT universe INCLUDING delisted = already survivorship-honest;
  survivor_universe_811.txt creates the SURVIVOR-ONLY counterfactual. Phase D "arm check"
  therefore = survivor-only CONTRAST (edge concentrated in delisted names -> FLAG), not an
  honesty upgrade. D-builder must log delisted-symbol presence in default signal loads to
  confirm.
- 2026-08-10 (Phase C builder): injection = is_stressed monkey-patch, grep-proven scope-
  correct (2 call sites: barrier precompute :2824 + a diagnostic print :4101; alloc path
  reads breadth directly + AW_ENABLED off). Identity battery bit-exact on all 5 sources;
  fired-fractions 22-now/2022 all in [22%,67%] -- none degenerate. Band cutoff DECISION
  (orchestrator-approved): abs(z)<=1.0 for RXDD/MWDD Gaussian bumps = VIX [20,28] /
  McClellan [-22,+22], matching the shipped RXDD slow-bleed comment.
- 2026-08-10 (Phase C builder, caveat inherited by ALL phases): STRATEGY_30DTE ships
  DTE_ROUTER_ENABLED=True (score>=80 AND trend<50, DAY_CAP=1) -- that 0.31-0.47% signal
  slice gets outcomes from monte_carlo_15dte's own precompute with ITS OWN TP/SL globals,
  untouched by every phase's patches. Methodologically consistent across all arms AND
  matches what a real 30-DTE TP/SL ship would do (router slice keeps 15-DTE barriers).
  FINDINGS note, not a defect.
- 2026-08-10 (Phase C builder traps): load_regime_map() is POLYMORPHIC -- returns
  breadth_score when BREADTH_ALLOC_ENABLED=True, NOT regime_multiplier (a naive
  regime-predicate would never fire); use a dedicated MarketRegime.regime_multiplier
  loader. RXDD's VIX source is _load_dte_router_market_maps, NOT load_vix_whist_map.
- 2026-08-10 (Phase D builder, pre-build verification): TP_FILL_MISS_P (monte_carlo.py:422)
  is a plain `os.environ.get(...)` MODULE-LEVEL read (import-time, like TP_BASE_OV) but is
  CONSUMED inside `resolve()`, called once per MC iteration from run_single_sim -- which DOES
  execute inside spawned MP workers. Unlike TP_BASE/TP_SIGMA_BASE (baked once into
  ctx['call_outcomes'] during _prepare_window, in the PARENT process, then pickled into
  workers via initargs -- see the 2026-08-10 recon entries above), TP_FILL_MISS_P has no
  baked equivalent; each worker's OWN fresh top-level import re-reads it. Setting
  os.environ['TP_FILL_MISS_P'] BEFORE `import monte_carlo` in the PARENT is sufficient
  (spawned children inherit parent os.environ at spawn time) -- no MC_NO_MP=1 forcing needed.
  Smoke-verify per the sigma-patch pattern: N=0-probe vs N=0.10-probe on the SAME cell must
  show a measurably different tp_rate, or the propagation silently failed.
- 2026-08-10 (Phase D builder): backtest_cascade.py has NO `_OV` env override for TP/SL at
  all (confirmed: TP_SIGMA_BASE/STRESS, SL_SIGMA_BASE/STRESS at bc.py:443-446 and
  NET_TP_BASE/STRESS, NET_SL_BASE/STRESS at bc.py:647-650 are plain module globals captured
  ONCE from strategy_config's FROZEN dataclass properties -- no env, no cfg-dict path;
  `_CFG_KNOB_GLOBALS`/`_cfg_knob_overrides` at bc.py:3005-3032 covers breadth/F3F/regime-
  slope/CT/CTSL/SAW/DTE-router/dead-hold knobs only, sizing and TP/SL are BOTH absent from
  that map). A deterministic-backtest TP/SL variant needs its OWN post-import monkeypatch of
  those 8 module globals (mirror mc_patch.set_tpsl's formula, sourced from bc.py's own
  `_cfg`/`_opt`) -- safe because compute_outcome()'s default (cfg=None) path always falls
  back to these bare globals, and `_cfg_knob_overrides(None)` is a documented no-op that
  never restores/clobbers them. Sizing (MAX_POSITIONS/TIER_ALLOC, bc.py:107-109,656) has NO
  override path at all -- backtest_cascade.py can only ever replay Core's native cascade
  sizing; there is no way to make it reproduce Apex's flat sizing without either a
  production-file edit or an UNVERIFIED extra monkeypatch (not attempted by Phase D's
  cascade-parity check -- Apex rows there are a TP/SL-direction proxy under Core sizing,
  explicitly labeled, not an Apex portfolio reproduction).
- 2026-08-10 (Phase D builder): the Apex 2x-race harness needs its OWN (arm=TP/SL) x window
  subprocess grid -- concentration_2x/sweep.py's run_window_inproc shares ONE _prepare_window
  ctx across its "cells" via _apply_cell_params, which is sizing-only-safe and TP/SL-unsafe
  (same stale-barrier-at-prepare trap as everywhere else in this campaign). Reuse instead:
  experiments/_mc_pinned_runner.run_one_window (already used by 4 other Block-D evidence
  drivers, e.g. apex_dte_dd/run_p03_evidence.py) launches monte_carlo.py as ITS OWN
  subprocess per (env_overrides, window_label) cell via WIN_START/WIN_END/WIN_LABEL +
  MC_RETURN_PATHS=1, is independently resumable (skip-if-json-exists) and timeout-guarded,
  and its output's `paths` dict (finals/dds/t2x_bars/t_50dd_bars/starting_cash) is a drop-in
  match for concentration_2x/sweep.py's own `_paths_from_result`/`compute_cell_metrics` --
  no new metric code needed, just wire the two together. Adding TP_BASE_OV/TP_STRESS_OV/
  SL_BASE_OV/SL_STRESS_OV to the env_overrides dict is sufficient (no in-process sigma patch
  needed in subprocess mode -- the fresh subprocess's own import reads them natively).
- 2026-08-10 (Phase D builder, provenance): the live Apex cert (PREREG section 1: P(2x)=72%,
  worst DD=76%, med~191d, collapse 0/12) traces to concentration_2x/sweep.py's DEFAULT
  invocation on cell flat_n10_a10 (step_months=1, horizon_days=730, hist_start=2016-06-01 --
  i.e. no flags overridden) at N=500, ~113 monthly-rolled windows (experiments/apex_dte_dd/
  FINDINGS.md "Result 4" + SHIP_HANDOFF.md). Reproducing comparable numbers for a TP/SL
  variant requires the IDENTICAL roll construction, not a re-derived one -- phaseD_apex2x.py
  defaults to exactly this and does not accept a differently-defaulted roll silently.
- 2026-08-10 (Phase D launch, tasks #351/352/363/364 failed-then-fixed): two guard-rail
  false positives, both fixed in driver files after their fleets went terminal (never
  edit a driver an active queue job may restart). (1) phaseC_run's window whitelist
  only held the 5 screen windows -- the PREREG 9-window follow-up labels were rejected;
  whitelist extended to screen+followup sets. (2) phaseD_run's survivor-mode engagement
  check greps each cell's prepare output for '[universe-filter]', but the loader-
  memoization cache suppresses that engine print on every cache-HIT prepare (cell 2+ of
  a window) -> FATAL despite the cached list being genuinely filtered. Fix: fall back to
  the direct property (0 loaded symbols outside the survivor file) before dying.
  GENERAL TRAP: print-based engagement checks and loader caches are mutually hostile --
  prefer property checks on the loaded data itself.
- 2026-08-10 (conditional N=500 confirms, tasks #373-378): BOTH conditionals rejected by
  the locked displacement rule. Core breadth@40 (0.20,-0.50): 5y -3.9pp vs flat-ship BUT
  2024 +3.5pp (>2pp annual regression), crash-family give-backs everywhere (2020 +3.2 /
  crash +2.8 / dip +6.8 / 10y +3.4), probe-inverted on 5y -> overfit-to-2022-mass shape.
  Apex breadth@40 (0.25,-0.75): better 11/12 windows + probe-clean BUT survivor arm
  inverts its edge (+2.9pp 2022, +1.0pp 22-now) -> delisted-dependent, same failure as
  Core flat (0.10,-0.60). AXIS RESULT: regime/market-wave TP/SL conditioning fully closed
  at N=500 on this substrate -- screen-level N=300 gains did not survive the confirm
  battery (add to the cohort-z != portfolio-alpha lesson family). C2 per-tier stays
  PARKED (trigger condition never fired: both profiles share one optimum pair).
- 2026-08-10 (SHIP HALT — human-ruling conflict found at edit time): strategy_config.py's
  own comment (2026-05-11 PUT_TP 0.14->0.35 revert) records a USER-DIRECTED execution-
  realism ruling: "sub-20% TP candidates are too close to option mark/intraday noise."
  Instrument-generic reasoning; both Phase D winners are TP=0.10 -> autonomous ship would
  override a standing human judgment. Per PREREG SS6 escape clause + house rule (human
  edits win, surface as conflicts): PRESENT, don't ship. TP>=0.20 alternates battery
  submitted (tasks #380-387) so the user gets a decision-ready menu. NOTE the ruling
  predates FF-2's measured real spreads (1.0-2.8% round-trip; +10% TP = 3.5-10x spread)
  — quantified context for the user's re-decision, not a license to override.
- 2026-08-10 (grid-design trap for the registry): user execution-realism rulings live in
  strategy_config.py COMMENTS, not only in docs/memory — grep config comments before
  locking a sweep grid. My Phase A TP floor (0.15/0.10) was designed without seeing the
  sub-20% ruling two lines above the constants I was sweeping.
- 2026-08-10 (mechanics correction): SL=-1.00 is NOT "SL off" — it maps to a ~3.64 sigma
  underlying disaster stop (|SL| x PREMIUM_MULT / DELTA); deep-SL fires mostly reroute to
  the dead-hold path (audit Task 1). Economically ~= dead-hold; language in FINDINGS
  corrected. The MEASURED cell is what it is; the gloss was wrong, not the evidence.
- 2026-08-10 (found by the alloc confirm builder): phaseD_apex2x.py writes its summary CSV
  in 'w' (overwrite) mode -- the tpsl campaign's per-finalist jobs all reused
  --job apex_confirm, so each later job clobbered prior rows; out/phaseD_apex2x_
  apex_confirm.csv holds only 2 of 4 arms run. The per-(arm,window) JSONs under
  out/phaseD_apex2x_results/ are COMPLETE and remain the evidence of record (FINDINGS
  metrics came from those + the live [ARM DONE] lines, both correct). Future users of
  this harness: distinct --job per arm, or fix to append mode. allocC_apex2x avoided it
  via distinct job names.
