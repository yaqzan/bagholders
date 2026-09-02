# Version History & Shipped Phase Summaries

## 2026-08-10 — TP/SL joint retune SHIPPED (Stage-2/3 portfolio, no version bump)

Prereg-locked campaign `experiments/tpsl_refine_2026_08/` (Phase A 160 cells N=150x4win -> B 60 cells N=300x9win -> C 112 conditional cells -> D N=500x12win; tasks #324-394).

**Core: TP 0.30->0.10, SL -0.70->-1.00 ("scalp-and-dead-hold").** N=500x12 vs baseline: 5y WorstDD 53.2->40.8 (-12.4pp), 22-now 53.2->39.0 (-14.1pp), DD better on all 12 windows, collapse 0, survivor-robust. SL-fires variant (0.10,-0.60) FLAGGED (delisted-dependent) and rejected; dead-hold variants clean.
**Apex: TP 0.30->0.10 (pinned), SL -0.70->-0.60.** Paired 113-window 2x-race N=500: P(2x ever) 61.3->99.4%, before-50dd 54.2->94.4%, med 136->128d, worst DD 69.7->65.4%, collapse 0.
Regime/market-wave-conditional TP/SL REJECTED at N=500 (crash give-backs, probe inversion, delisted-dependence); per-tier TP/SL never triggered. May-2026 sub-20%-TP veto re-adjudicated on `experiments/tp_fill_fidelity_30dte/`: no tight-TP fill cliff (economic never-fill 14.1% @TP15 vs 15.8% @TP30). Winner survived fill-pessimism probes (miss 0.10 / calibrated 0.15+GAP_AWARE / 0.20 buffer) and beat TP>=0.20 alternates under canon and calibrated fills.

**Program-level escalation (open, in known-issues.md):** under calibrated fill knobs every config including the prior baseline reads negative (Core baseline 5y med -53%; old Apex p_coll 98% vs new 0.8%); pessimistic model tracks the live ledger's -52%-since-June better than canon. Differentials are fill-model-robust, absolutes are not — MC-realism default flip (GAP_AWARE+0.15) needs its own Stage-3 A/B + P2.B reconciliation.

## 2026-08-07 — LIQUIDITY_FLOOR wired default-OFF (Stage-3 portfolio, no version bump)

Corrected penalized-engine A/B with matched random-drop control, prereg-locked, run #305-307 (N=100/300/500, paired seeds, v74 pinned). **Core floor-150: control-adjusted DD nets positive 7/8 covered windows (2023 +10.2pp, dip +13.1pp, 2025 +7.4pp), converged across N, collapse 0 every cell, supply cut ~36%; 2024 -5.6pp is the named persistent-trend counter-window. Apex failed T4 (5y +1.47pp vs +1.0 allowance).** Incidence-knob sensitivity: bit-identical at 0.5x/2x (#308). Wired through all 13 consumers at 0.0 = OFF (registry `disabled/wired_neutral` 30-DTE, `not_wired` 15-DTE; profile-overridable; `portfolio_engine` mirrored via `load_signals(cfg=)`; live-map tool `tools/build_liquidity_map_live.py`). Enable gate: P2.B live fills N>=30 -> Core `liquidity_floor: 150`, Apex stays 0. Evidence: `experiments/flatfile_exploitation/{FF3_STAGEB_PREREG,FF3_STAGEB_RESULTS}.md`. Penalty instruments remain env-only research tooling.

## Active Version: v74 (`f9fb7b934`) — 2026-06-15 (Lean — post-pre_boost tail retired)

Scoring ship (ALGORITHM_VERSION bump `99cd2f0b1`, DB version 74, silo `algorithm_versions/v74`, pushed `5a393f871`). Outcome of `experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md`.

**What.** Disabled 4 post-`pre_boost` score-stage tail members in `strategy_config.SCORING`: `CONT_BOOST_ENABLED`, `WVD_WAVE_ENABLED`, `DAILY_VOLUME_AUTHORITY_WAVE_ENABLED`, `EARN_BOOST_ENABLED` -> False. Lean core kept. v74 == v73 `pre_boost` bit-exact (100% on 2024 sample).

**Why (per-member apex-EV attribution, 30dte_apex predictand).** cont-echo NEUTRAL (+2.48 vs +2.90; over-deployment DD), EARN_BOOST NEUTRAL (+7% apex-EV was N=77 noise -> +0.2pp at N=179), daily-volume DILUTIVE (-1.66% vs +2.57%), WVD HARMFUL (removes +8.44%-EV signals). No member carries real per-trade alpha.

**Validation.** Whole-tail ablation MC (N=300x8): lean 5y +1,143% / 51.3% DD vs full +1,097% / 62.1% DD (-10.8pp DD at comparable/better compound, collapse=0). 0d substrate gate FLAG (beats momentum t=+2.95, marginal vs climatology). Reversible `/revert v73`. Weatherization re-integration design (MOS-isolated, predictand-gated, forward-holdout-validated) is the standing gate for any future mechanism re-add.

## Portfolio Release: Apex/Core restructure (fast-2x sprint) - 2026-06-17

Portfolio-only, NO `ALGORITHM_VERSION` bump (scoring stays v74 `f9fb7b934`). Renames/re-roles the three profiles in `algorithm_versions/portfolio_profiles.json` and flips the default Apex->Core. Record: `experiments/concentration_2x/FINDINGS.md`.

**What changed.**
- **Apex** = fast-2x SPRINT = `flat_n4_a25` (flat 25% across 4 top-conviction 75+ names; `tier_ultra/top/mid/low=0.25`, `max_positions=4`, `call_max=4`, overflow 0; caps gross/call 0.5, uncapped ceiling, DD band 0.35/0.55/0.40, puts off). OPT-IN aggressive, NOT default. color red, risk `sprint`.
- **Core** = former Apex = long-run held compounder, NEW DEFAULT: tiers `0.20/0.15/0.08/0.03`, `max_positions=14`, gross/call cap 0.5, UNCAPPED ceiling. color yellow. (Same params as old Apex/`STRATEGY_30DTE` -> live ledger unchanged, renamed only.)
- **Sentinel** unchanged (85+-only, 30% cap, $1M ceiling).
- Old balanced Core ($2M cap / 40%) REMOVED.
- Default flipped apex->core across `portfolio_profiles.py` (`DEFAULT_PROFILE_KEY`), `portfolio_engine.py`, `api.py` (2 sites), `src/pages/{Allocator,Backtest,Portfolio}.js`. Live `PortfolioRun` DB row migrated apex->core (config-neutral, bit-unchanged).

**Why.** Time-to-2x is a different objective than max-compound: user objective for sprint = minimum time-to-2x ($50k->$100k), collapse-tolerant (exit at 2x before over-deployment's DD penalty compounds, bounded only by collapse=0, ~3-name floor). Frontier (`experiments/concentration_2x`, 19,000 paths/cell): `flat_n4_a25` P(2x within 2y)=70.5% / median ~129 cal-days / collapse=0 / worst DD ~83%.

**Key finding: a fast-2x config run CONTINUOUSLY is negative-compound.** Held-continuously Stage-3 gate (SPREAD_TILT OFF, N=500, COVID-incl): sprint `ret_5y_med -37.1%`, `dd_5y 79.6%`, `dd_22now 79.1%`, `dd_2024 60.3%`, `p_collapse 0.0` every window. Sprint only realizes value with stop-at-2x discipline; live engine has no auto-stop -> sprint is manual/opt-in, Core is held default. Auto-rotate-at-2x engine feature considered and deferred.

**Core (held default) Stage-3 gate row (baseline_apex):** 5y compound +1247.9% / `dd_5y 61.7%` / 22-now +600.2% / 2024 +476.7% / collapse 0 (v73 c04_low03_mid08 retune, N=500x10 incl COVID). Prior Apex 10y was +16,953% at ~86% DD.

**Exploration nulls (CLOSED):**
- Track A (regime/equity-curve WAVE timing) NULL for speed — market-regime scaling hurts (mis-times); equity-curve scaling is a DD-shaver costing ~half return/speed. Survives only as a Sentinel DD-minimization lever.
- Track C (equity-DD insurance overlay) NULL for speed — cuts DD ~14-18pp but craters P(2x) 11-36pp and slows median.
- Extreme concentration (1-2 names at 50%) is a COLLAPSE-TRAP: breaks collapse=0 (1.9-2.5%), negative median compound; `flat_n3_a33` hit 2.8% collapse on 5y held gate. ~3-name floor is the collapse boundary.

**Validation:** drift-guard 653 pass, mechanism registry 16/206 pass, `test_portfolio_profiles` pass, profile-load smoke (Apex=0.25/4, Core=0.20.../14, default=core), `/health` green, live run shows `(core)`. Revert: restore `apex`/`core` defs in `portfolio_profiles.json` + flip defaults back.

## Portfolio Release: SPREAD_TILT 75-79 high-spread call-alloc haircut - 2026-06-15

Portfolio-stage ship (NO `ALGORITHM_VERSION` bump; scoring stays v74). Committed `9acf7465d`, pushed. Full arc: [weatherization.md](weatherization.md).

**Origin.** Verification Substrate (`30dte_apex` predictand + skill-vs-baseline 0d gate) localized the score's weakness to bear/chop regimes, where it loses to a 12-1 momentum baseline. Kill-test for an orthogonal risk signal landed on component disagreement — when the five base components (trend/bb/rsi/macd/stoch) disagree, the 75-79 signal is low-conviction noise, distinct from the price-direction signal.

**Mechanism (SPREAD_TILT).** Down-weight-only call-alloc haircut, 75-79-band-only. `spread = sqrt(population variance of the five component scores / 5)`; `scale = 1.0` below `SPREAD_TILT_LO`, ramping linearly to floor `1 - SPREAD_TILT_DEPTH` (0.60x) above `SPREAD_TILT_HI`. `STRATEGY_30DTE`: `SPREAD_TILT_ENABLED=True, LO=26.9` (75-79 spread p50), `HI=31.3` (p75), `DEPTH=0.40`. 15 DTE disabled (`not_wired`). Ultra/top/mid tiers and puts untouched.

**Stage-3 validation — T1-T7 ALL PASS** (N=500x9 incl 2020-COVID; #195 vs #196): 5y WorstDD 53.5->49.4 (-4.1pp), DD down 8/9 windows, return held-or-up, collapse=0 every window.

**Wiring (13-consumer).** strategy_config + mechanism_registry (16 mechs, 206 checks) + monte_carlo + backtest_cascade + live portfolio_engine (entry sizing + `_strategy_fingerprint`) + trader (`_cmd_backtest`/`_cmd_alloc`) + api (`/api/backtest/run` + `/api/trader/simulate`) + Backtest.js + portfolio_param_manifest + drift-guard (653). API serves `SPREAD_TILT_ENABLED` 30dte=True / 15dte=False post-restart.

**Live portfolio-engine parity.** Re-validated vs `run_cascade_backtest`: sizing bit-exact (positions 14/14, closed 18/18, premiums <=$0.005, pnl_pct exact, final cost-equity to the cent; `experiments/portfolio_engine_parity/validate.py`). KNOWN separate item (not SPREAD_TILT, chip spawned): MTM equity-curve diverges ~$295 (~0.7%) from 2026-06-08 — display-only pre-existing engine-vs-backtest marking nuance; likely the 2026-06-11 graceful-sweep `pending_requal` mark.

Live Portfolio runs qualification-neutral `strategy_sweep` on next `trader update`. Revert: `SPREAD_TILT_ENABLED=False`.

## v73 (`07e9722b5`) — 2026-06-12 (Honest Dampener Retirements)

Scoring ship (bump `e32fb4ec6`, DB version 73, silo+lock `a51311fa7`). Executes N1 dampener-stack ablation (`experiments/dampener_ablation_v72/{FINDINGS,SHIP_HANDOFF}.md`) — 3rd honest-era retirement campaign (after v69 weekly, v71 four retirements). Worktree `algo-exp/v73-dampener-retire`; retirement-by-config (code paths stay).

**Retired (5), honest evidence (5y full-universe ReSim A/B, option barrier w=15d):**

| Mech | Flip | Evidence |
|---|---|---|
| WCF (v27) | `WCF_LIFT_K=0` | removals 41.3% vs shared 41.7% (z=-0.43) — deletes ~85% of put band with zero discrimination; founding +5.9pp evidence was look-ahead artifact (MCD pattern). Puts OFF; v72 ramp now inert (kept for replay) |
| ICH (v44) | `ICH_ENABLED=False` | call leg INERT (>=75 removes N=1/5y); put leg wrong-way (deletes 45.0%-WR puts vs 41.7% shared, z=+1.65) |
| CWCF (v32) | `CWCF_DAMPEN_K=0` | removals 50.4% vs 54.3% (z=-1.96) at 19% of potential 75+ N |
| CSWC | `CSWC_DAMPEN_K=0` | removals 51.2% vs 54.2% (z=-1.63) at 22% of N |
| SCW (v50/v60) | `SCW_ENABLED=False` | >=75 removals 50.3% (z=-1.69) at 14% of N; real >=70 signal (z=-2.47) lands in zero-alloc 70-74 |

**Kept (2):** CWWD (z=-2.28, real, lands in dead 70-74, free) and WVD (deleted >=70 cohort 41.6% vs 51.9%, z=-3.32 — below call BE; the one clear earner).

**Adjudication.** Bundle-confirm arms (#152): bundle_b restores 2,507 distinct 75+ signals (+77%) at 50.5% vs 54.3% shared (z=-2.84) — every one above call BE 45; puts identical across bundles. Stage-1 growth gate on real calls-only supply (`gate_inputs.py`): dG +19.56% option / +36.27% generic, positive every window (binding=dip); W4/W6 clean; calls-only coverage 0.323->0.483. FLAG on bootstrap-CI width only -> shipped per FLAG-teeth, watch metric = post-ship 5y assess 85+/80+ option-TP15 + 75+ supply/binding-window coverage.

**MC smoke (N=300 x 7 windows, #154): collapse 0.0% every cell.** At unchanged Apex params DD runs +5-13pp hotter (max 62.7%) with lower long-window compound — the v71 signature. Mandatory follow-up: Stage-3 sizing retune on v73 density (N=500x10 incl COVID, seeded from PRF `portfolio_response.py --derive v73`). Interim rollback: `/revert v72`.

**Ops notes.** (1) MC smoke harness traps fixed in `ea15d3390`: peewee DeferredForeignKey lazy resolution (`s.symbol` = one SELECT/row — use `s.symbol_id`) and queue-launcher cp1252 stdout making monte_carlo block-buffer-rewrap (pass `--env PYTHONIOENCODING=utf-8` at submit). (2) Worktree candidate verified bit-identical to bundle_b arm (6,323/6,325; 2 diffs = live intraday drift).

## Portfolio Release: v73 Apex Retune (mid+low selectivity on doubled supply) - 2026-06-12

Stage-3 portfolio-only ship (no version bump; scoring stays v73). Mandatory FLAG-teeth follow-up to v73 dampener retirements, which raised 75+ supply +77% on top of v71's already-doubled density (ReSim 5y 3,262 -> 5,769 75+ signals; 85-89 N 132->220). Pre-ship MC smoke showed the v71 signature — collapse=0 but DD +5..+13pp at unchanged Apex params (fitted to v71's density).

PRF-seeded (`portfolio_response.py --derive v73`, real supply) proposed `low 0.03`; structured B->C->D sweep (`experiments/v73_portfolio_retune/`, v69 subprocess driver, paired seeds) added `mid 0.08` (trio-removal densified 80-84 too). **Winner c04_low03_mid08 = `TIER_MID 0.10->0.08` + `TIER_LOW 0.05->0.03`.**

Phase D ship gate (N=500 x 10 windows incl 2020+2020_crash): every window's DD improves (worst +0.7pp; 5y 66.3->61.7, 22-now 66.9->61.5, 2024 -7.4pp, dip -6.4pp, 2025 -8.7pp), 5y compound +1% (highest 5y median of three: 1297 > 1280 base > 1241 c08), collapse=0 every window x both arms; T1-T7 all PASS; ranking stable N=100/300/500.

**Decision: c04 over bigger-DD-cut `c08_alloc065`** (all tiers x0.65, 5y DD -6.8pp but uniform-shrink velocity-loss: 2024 compound x0.53, 22-now x0.69). c04 = targeted-selectivity Pareto — squeeze only over-supplied 75-84 tiers, leave ultra/top full. Per Apex objective (max cost-adjusted return s.t. collapse=0, DD a budget) the higher-return-lower-DD option wins; c08's extra shave is a Core/Sentinel preservation trade. 61.7% 5y DD inside Apex's 80-90% recoverable budget. Direct lineage of v71 c14 selectivity winner.

Core/Sentinel untouched. Overflow stays 0; puts stay off. drift-guard 645 / registry / dte-audit green; `trader alloc` shows new values (80-84 $1,138, 75-79 $427 at $50k). Records: `experiments/v73_portfolio_retune/FINDINGS.md` + `.cache/v73_portfolio_retune/phase_{B,C,D}.jsonl`. Revert: restore low 0.05, mid 0.10. Watch metric: post-ship 5y assess 85+/80+ option-TP15 (ship baseline 85-89 51.2% / 80-84 49.5%).

## Infra: Pre-v69 score-row purge + honest-era cadence floor - 2026-06-12

No scoring/portfolio change, no `ALGORITHM_VERSION` bump. Commits `52a002409` (cadence floor + purge tool), `5e071c0db` (5.7 DELETE-plan fix).

**What.** `scores` table had grown to ~152M rows (84GB data + 35GB index); 95.3% (145M rows) belonged to pre-v69 versions (inflated look-ahead-era output, or hybrid honest re-recalcs from the 2026-06-01 campaign already distilled into research packs). Backed nothing; an indexed GROUP BY over `scores` exceeded the 30s read_timeout. All pre-v69 rows in `scores` and `dte_recommendations` (100% pre-v69) purged via `tools/purge_score_versions.py --below 69 --swap` (#147, off-market, 36m31s).

**Method lesson — at 95% deletion ratio, COPY-SWAP not DELETE.** Two delete attempts failed: (1) `DELETE ... LIMIT` dies on read_timeout — MySQL 5.7 rejects index hints on single-table DELETE, optimizer picks a clustered-index crawl; (2) properly-planned date-windowed deletes (EXPLAIN-verified range plan on `scores_version_date_IDX`) ran ~1,500 rows/s (~22h projected) because the clustered PK `(symbol, date, version_id)` interleaves every version on every page. `--swap` path copies survivors into a `CREATE TABLE LIKE` clone, parity-checks counts before atomic `RENAME` (aborts if a writer races), drops old ~120GB tablespace (2s), restores 2 FK constraints (`FOREIGN_KEY_CHECKS=0` -> instant INPLACE).

**Result:** `scores` 152M/84+35GB -> 7.08M rows/3.8+1.5GB (~114GB reclaimed); `dte_recommendations` -> empty; prior-timing-out GROUP BY now 2.1s; FKs verified; API healthy.

**KEPT:** `algorithm_versions` ledger, assessment rows, temporal stats, research packs (v27-v68 coverage verified; v67 undocumented inflated-era candidate purged without one), silos. Version ids NOT renumbered — ledger is the audit trail, honest era starts at v69.

**Cadence policy = v69+.** `DEFAULT_SELECTOR_VERSIONS` trimmed to {69..72}; new `AlgorithmVersion.CADENCE_MIN_VERSION_ID = 69` floor in `seed_selector_defaults` auto-enrolls every future shipped version. Daily `trader update` drops ~13 sidecar versions to 3 (+ active writer). Version dropdown self-heals (`api._score_version_catalog` skips rowless versions); verified serving [72,71,70,69]. Guard test: `tests/test_algorithm_version_cadence.py`.

## Portfolio Release: BDIV pre-top breadth-divergence-at-highs call dampener - 2026-06-11

Portfolio-only ship (no version bump; scoring stays v71 `04044b21b`). Commit `3505c8770`, pushed. Overnight run answering: "isolate periods of major drawdowns, look for a Hindenburg omen or similar market shape that raises drawdown likelihood."

**Two-part answer (first DD mine on the fresh v71 tape — all prior tapes were v70):**
- **Literal Hindenburg/omen family is NULL and INVERTED.** Episode-onset event study vs 5 canonical major-DD onsets (2020-02-10, 2021-01-25, 2022-04-12/20, 2025-02-04; 300 seeds): 0 of 24 omen days (0 of 222 trailing-window days) preceded a major onset within 10-20d; entries on omen days are mean-reversion WINNERS (loser-rate 0.226 vs 0.310, mpnl +0.079 vs +0.022). Zweig/churn/NL-spike/McClellan-summation-divergence all per-year sign-flip; trailing-confirmed cohort inverts to winner in all-levers-off slice. Pre-onset lookbacks show strength/complacency (A-D rising, VIX falling) — drawdowns start at tops, not after warnings (highest forward-onset lift: bullish Zweig thrust, 2.5x).
- **Shippable signal: classic pre-top breadth divergence.** SPY within ~1-2% of 60d high while breadth_score rolled over ~5-10pts in 10d — low-EV (mpnl -0.018 vs +0.029 off, z=+25, dd_conc 1.82, 12% of trades), sign-stable every year incl both momentum-persistent ones (2024 -0.028, 2025 -0.053), orthogonal (survives vix<20 & |mcc|>30 & breadth>=40 & TRIN-outside-neutral, gap -0.068). Proximity-to-highs monotonic; breadth-gap is inverted-U (gap>12 = sharp shakeout = mean-reversion winners, left alone).

**Mechanism (BDIV).** `alloc *= 1 - DEPTH . prox_ramp(spy_from60h; PROX_CUT->PROX_FULL) . exp(-0.5.((brd_det10 - GAP_C)/GAP_W)^2)`, calls only. First LEADING DD lever: no DD-gate (fires pre-onset at the top, running dd~0), no VIX-panic knob (SPY-near-highs is the structural crash guard; cannot fire mid-crash — 2022 fires 4% of days, MC delta 0.0; COVID flat). Map = SPY rolling-60d-high distance (PriceHistory) + breadth_score 10d change (MarketBreadth). `STRATEGY_30DTE` winner: `PROX_CUT=0.0198, PROX_FULL=0.0075, GAP_C=7.716, GAP_W=3.4571, DEPTH=0.53`; 15 DTE `not_wired`.

**Validation — Stage-3 T1-T7 ALL PASS** (verify NOOP byte-identical -> B LHS-16 N=100x6 16/16 -> C N=300x10 -> D N=500x10 ship gate incl COVID, paired seeds). Headline deltas (base->BDIV WorstDD): 5y 67.6->64.6 (+3.0pp, compound +1,660%->+2,010%/+21%); dip 40.3->26.3 (+14.0pp, +191%->+284%/+49%, the 2025-02-04 divergence-led drawdown that motivated the ask); 22-now 65.0->65.0 (flat DD, compound +2,485%->+2,796%/+12.5%); 2022 0.0 by construction (calls-only, no fire mid-crash); 2024 -0.1pp (-8.4% compound, persistent-year price). collapse=0 every window; worst annual DD regression 0.1pp (<<T5 5pp).

**Wiring.** Full 13-consumer mirror of TVDD: strategy_config (30 on/15 off) + mechanism_registry (15 mechs) + monte_carlo (`load_bdiv_map`, MP-threaded) + backtest_cascade (cfg-overridable; vacuum call BDIV-inert per TVDD/G9) + trader + api (`/api/trader/simulate`+`/api/backtest/run`) + Backtest.js (4 blocks) + drift-guard (644) + param manifest/ENGINE_CFG_KEYS + driver ENV_MAP. Adversarial diff review: 0 blockers. Revert: `BDIV_ENABLED=False`. Record: `experiments/dd_onset_omens/{mine_onset,mine2_refine,sweep}.py` + `FINDINGS.md`.

**Review WARN, RESOLVED 2026-06-11:** live Portfolio engine (`portfolio_engine._cascade_entries`) had applied only RXDD — SVR/MWDD/TVDD/BDIV missing from real-money entry sizing and `_strategy_fingerprint` (divergent since 2026-06-05). Fixed: all four wired into `_open_entries_for_day` (mirrors `run_backtest`: SVR per-signal, MWDD/TVDD/BDIV per-date maps once per sync, same multiply order) + fingerprint extended + CT-sort <95 parity fix; bit-exact parity re-validated vs `run_cascade_backtest` (06-01->06-10: 8 closed + 14 open identical, pnl_pct exact, premiums <=$0.005). Harness: `experiments/portfolio_engine_parity/validate.py`. Triggered `strategy_sweep` qualification-neutral except ADUR (79->38 close-update fakeout; fill-timing race chip spawned).

**Ops lesson.** Phase D attempt 1 hung 5h empty log — engine files edited mid-run for ship wiring; Windows MP workers re-import modules from disk per window, a worker died on mid-edit file state, pool.map blocked forever. Also: once a mechanism ships enabled in strategy_config, sweep baselines must set its `*_ENABLED=0` explicitly.

## Portfolio Release: v71 Apex Retune (selectivity on doubled supply) - 2026-06-10 (09:45 ET)

Stage-3 portfolio-only ship (no version bump; scoring stays v71). v71's +83% 75+ supply saturated the Apex book under v70-fitted params (5y med +1,230% / WorstDD 74.3%). Structured B->C->D sweep (`experiments/v71_portfolio_retune/`, paired seeds, driver subprocess harness): **winner c14 = `TIER_LOW 0.10->0.05` + `TIER_OVERFLOW 0.035->0`.**

Phase D ship gate (N=500 x 10 windows incl 2020+2020_crash): every window's DD improves (+5.0..+18.1pp; 5y 74.3->67.6, 22-now 72.9->65.0, 2024 53.6->35.5), 5y compound +35% (+1,230->+1,660%), collapse=0 every window x both arms; T1-T7 all PASS; ranking stable N=100/300/500. Mechanism: selectivity on the doubled 75-79 tier (smaller slugs = same participation, better diversification, faster recycling); cap/MaxPos/DD-band axes all dominated by it (cap60 re-confirmed capital-velocity law: worse on both).

Also closed same sweep: **put reintroduction re-NULL'd on v71** (monotonic: best sliver DD +4pp worse / -7% compound / loses 2022; half-v60 = 15% collapse) — cash-buffer-drain structure survives the substrate change; and **70-74 overflow retired as supply-density-conditional** (45.6% of all fills on v71, strictly compound-dilutive: ovf 0 -> x1.57; was a hydration patch for v70's starved book). Core/Sentinel untouched. drift-guard 632 / registry / dte-audit green.

## v72 (`fc5671200`) — 2026-06-11 (WCF Score-Gate Ramp)

Scoring ship (bump `97f5118e0`, DB version 72, silo `71ee9d527`). Stability-motivated, per-trade NEUTRAL — smooths the v27 WCF put-floor lift's binary score gate, source of the largest recurring intraday-fakeout family.

**Root cause.** WCF lift (`overall < 28 AND w_adj > -17 -> +0.95.(50-overall)`) fires on the POST-REGIME integer: regime applied and int-rounded before WCF, so sub-point component wobble AND regime-multiplier reapplication (1.02<->1.04) flip the 27/28 boundary — toggling the full ~21.85-pt lift. Evidence: GIS 2026-06-10 toggled 49<->28 three times intraday (`wcf_lift: 21.85->None` in `score_intraday_logs`); ~15 symbols/day in lo=28/hi=45-49 FAKEOUT family (CBRE, GEHC, TEM, CRM, STLA).

**Mechanism.** New constant `WCF_LIFT_RAMP_TOP=33`: full lift bit-identical at `overall <= 27`; fades linearly to zero at 33 (`sgrad = clip((33-overall)/6)` multiplying existing wadj weakness). `RAMP_TOP=28` reproduces the legacy binary gate bit-exactly. wadj axis untouched. Hysteresis rejected by design (stateless scoring).

**Validation** (`experiments/wcf_score_ramp/FINDINGS.md`, worktree `algo-exp/wcf-score-ramp`):
- 11,492/11,492 stored v71 fired rows reconstruct bit-exact through the formula model.
- Full-faithful ScoreSimulator A/B (894 stocks, 194,526 pairs, 1y): top33 = 13,305 diffs, 0 tradable-bucket violations — every diff starts at 28-32 and lands in (base, 45], between put gates (<=27) and call gates (>=69).
- Only compositional change: non-tradable `<30` bucket (~74% weak-weekly 28/29 stragglers) sheds ~31k rows and improves +0.8pp WR15 (migrators 64.5% vs remainder 65.6%). All cascade tiers/peaks/N-floor tiers byte-identical. W4/W6 PASS; W5 scoring-neutral FLAG.
- Stability replay (2 weeks of `score_intraday_logs`): WCF-affected fakeout groups (swing>=10) 43->17 (-60%); all-symbol >=20-pt swings 18->7. top33 chosen over top31/top30 (-60% vs -23%/-5% at identical bucket safety).

**Ops notes.** (1) Worktree PYTHONPATH trap: global PYTHONPATH includes main checkout, so worktree scripts silently import MAIN's modules — first sim A/B false-NULLed. Fixed with `sys.path[0]` pin + module-origin asserts (`feedback_worktree_pythonpath_trap.md`). (2) `intraday_diagnostics.attribute_swing` decomposes stages in the wrong order (assumes pre_boost->pre_regime; actual pipeline is reverse), mislabeling this family as "Earnings / continuation boost" — chip spawned. (3) `ScoreSimulator.compare()` vs DB broken (`'Row' object has no attribute 'symbol_id'`) — pre-existing, minor.

## v71 (`04044b21b`) — 2026-06-10 (Integrity-Audit Honest Fixes + 4 Retirements)

Scoring ship (bump `de7fa330e`, DB version 71, full 10y recalc). Executes the 2026-06-09 scoring-integrity audit via worktree `algo-exp/integrity-audit` (`experiments/integrity_audit_2026_06/{AUDIT_FIX_HANDOFF,FINDINGS}.md`).

**Leak fixes (shipped unconditionally):**
- **F2 — SPY-weekly look-ahead (v69 class, missed consumers).** `core.py`'s `_spy_wk_on_or_before(score_date)` selected the CURRENT week's Monday-keyed complete-week `WeeklyScore` row for Mon-Thu signals — future bars leaked into `mis_stress` (call softener) and JA4 (`put_regime_multiplier`). New `_spy_wk_last_completed()` (lookup at target-7d, the kijun/wv_force convention) at all 5 per-date call sites + the simulator's own map.
- **F4 — static-mcap survivorship.** `Stock.market_cap` (current yfinance snapshot) was applied to ALL historical dates. New PIT proxy `mcap_t ~= mcap_latest x close_t / anchor_close` (`compute_pit_mcap_b`/`build_pit_mcap_map` in scoring.py).
- **F1 — wave inertness guards.** v57 Market Wave source CSV silently vanished; `_load_rows` returned `[]` with no warning -> wave was INERT in every v60/v69/v70 row set. Loud once-per-process stderr guards + self-contained rebuilder (`build_market_wave_source.py`, SPDR DB history 1999->present).
- **F3 — barrier cache truncation.** `.cache/barrier_outcomes.db` had been wiped to 117 days by nightly `refresh_recent` — would have silently zeroed `cont_lift` before 2025-12-19. Rebuilt to 2016-04-26 -> 2026-06-09 (83.3M rows, 801 symbols) + coverage guard in `_load_cont_barrier_wins`.
- Regression tests: `tests/test_integrity_guards.py`.

**Retirements (honest 5y full-universe sharded ReSim A/B, option-aligned barrier primary; validation arm reproduced stored v70 at 98.43% exact / 936,580 rows; doctrine: bias-to-retire when marginal):**

| Mechanism | Verdict evidence |
|---|---|
| mis_stress (v25) | lag-fixed admits at >=75 BELOW baseline (50.7% vs 55.2%, z=-1.03); original +0.2pp evidence was leak-measured |
| JA4 put blend | wash at <=25; z=-2.48 at <=30; puts OFF in all profiles; also removes a sim/prod divergence |
| MCD (v43) | PIT ladder collapses 8.2pp->2.6pp, z=+2.61 (<W1 3), non-monotonic — gradient was survivorship; removals near-baseline (53.0 vs 55.0) at ~42% of 75+ N |
| Sector Market Wave (v57) | inert all era; rebuilt-source A/B removes ABOVE-baseline winners (-28% 75+ N, removals 56.4 vs shared 54.4) — crash-artifact trap |

**Result (production assess, 5y/30DTE, v70->v71):** 75+ N 1,868->3,415 (+83%) at WR15 51.2->53.1 (+1.9pp); 80+ N +106%/+1.3pp; 85+ +4.7pp; puts <=25 +1.1pp; gradient preserved. Honest era's -27% supply deflation substantially recovered without re-admitting look-ahead.

**Cleared suspicions:** ICH/WVD residual weekly look-ahead = FALSE (both already lag -7d); cont-echo timing PIT-safe. Adversarial 3-agent verification: 0 blockers.

## Portfolio Release: Calendar-day hold + HONEST THETA standardization - 2026-06-09

Engine-semantics standardization (no `ALGORITHM_VERSION` bump; scoring stays v70 `c70d16d22`). Unifies MC + deterministic engines onto honest, real-life calendar-day basis.

**Root bug.** `strategy_config.HOLD_DAYS` was declared "trading bars" but engines disagreed: `monte_carlo` held 15 trading bars (~21 cal days) while `backtest_cascade` (live Portfolio + `/api/backtest/run`) read the same field as 15 calendar days (~11 bars). Both also decayed option theta over `30` trading bars (~42 cal days) while pricing entry premium for a 30-calendar-day option, under-charging theta ~16pp on slow/hard/dead-hold trades.

**Standard (`CALENDAR_HOLD=True`, 30 DTE):** hold to `HOLD_CAL_DAYS=27` calendar days; theta over `NOMINAL_CAL_DTE=30` calendar days (`cal_held = (fire_bar.date - signal_date).days`). Env-overridable (`CALENDAR_HOLD/HOLD_CAL_DAYS/NOMINAL_CAL_DTE/DH_POP_SLIP`). 15 DTE `CALENDAR_HOLD=False` (deferred, fast-follow).

**HOLD=27** from #93 (per-trade option-EV plateaus flat W21-25 on D30) + #94 fine-bracket {24-29} x {free-fill, realistic-popout}: flat noise plateau (5y within ~2% across 25-29, well inside the 1.6-1.8x N=300 noise band), 27 is the 5y-pop03 max, collapse=0 everywhere.

**Honest result (/~200 fee):** Apex 30-DTE @ 27 cal honest theta -> 5y ~= +1,802% (realistic popout) to +2,574% (free-fill), 22-now +2,762-3,548%, ~66% DD, collapse=0 incl 2020-COVID (#92/#94, N=300). Old bar-engine +557,692% 5y was theta fantasy — the strategy is unchanged, only the measurement is now honest.

**Wired + validated:** `monte_carlo.py` + `backtest_cascade.py` read the standard from config (env-overridable); honest theta + calendar deadline in both; drift-guard 632 + registry green; bc honest-theta proven (synthetic flat->hard-sell realizes -0.757 at 28 cal = theta(28,30)+slip, vs old static -0.415). Reassess: `temporal-refresh --profiles all` + research-pack v70 rebuild re-run on honest engine; verified `/api/strategy/config` 30dte CALENDAR_HOLD=true/27/30.

**Concurrent fix:** task-queue scheduler equal-priority kill+requeue livelock — `task_queue/daemon.py` preemption now gates on BASE priority, not aged `effective_priority` (aging governs admission order only). 58-test-validated.

**Fast-follows:** (1) `trader.py _cmd_backtest` + `api.py ~2940` cruder STATIC engines (net_tp/sl, no theta, still 15-cal; NOT decision surfaces) -> redirect to `run_cascade_backtest`. (2) 15 DTE honest-calendar port + own hold optimization. Records: `experiments/calendar_hold/`, `experiments/option_curve/`. Revert: `CALENDAR_HOLD=False` in `strategy_config`.

## Portfolio Release: TVDD TRIN volume-flow neutral-band call dampener - 2026-06-07

Portfolio-only ship (no version bump; scoring stays v70). Overnight run. 4th orthogonal Stage-3 DD lever on the live Apex sleeve, stacking on RXDD(VIX) + SVR(skew) + MWDD(McClellan count-momentum) + F3F(breadth level).

**Method.** Regenerated full-lever MC trade-tape (RXDD+SVR+MWDD all LIVE; 6.9M call trades, 12 windows), mined residual DD-concentration. Key refinement (MWDD design insight): a lever only ACTS when running dd >= DD_MIN, so mine the DD-active subset (dd>=0.13) — the whole-tape mine diluted signal (TRIN trough mpnl +0.041); the DD-active mine sharpened it (mpnl +0.025, then -0.060 in the clean slice).

**Finding.** After 3 levers, a Pareto DD lever needs low-EV AND high-dd_conc COINCIDING on an orthogonal, crash-robust cohort. **TRIN (Arms index = volume flow):** neutral band (TRIN ~1.0-1.3, balanced/mild-distribution flow) is low-EV + DD-concentrated, with the RXDD/MWDD inverted-U (extremes high-EV). Orthogonality proven: in all-shipped-levers-off slice (VIX<20, |McClellan|>30, breadth>=40) TRIN 1.0-1.3 still runs mpnl -0.060, loser-rate 41%, z+57 — a volume-flow-vs-breadth-momentum divergence. The MWDD note's "TRIN likely McClellan-correlated -> redundant" warning is refuted for this band. Crash-robust: low-EV across 10y/2021/2025/22-now/5y/dip; extremes (froth<0.7 +0.085, panic>1.8 +0.101) high-EV every window; VIX-panic-excluded.

**Mechanism.** Mirror of MWDD/RXDD: `alloc_frac *= 1 - DEPTH.exp(-0.5.((trin-TRIN_C)/TRIN_W)^2)`, no-op when disabled / TRIN missing / running dd < DD_MIN / VIX >= VIX_PANIC. TRIN map = `MarketBreadth.trin` (on-or-before lookup, full history; live `trader update` computes TRIN daily). `STRATEGY_30DTE` c00: `TVDD_ENABLED=True, TVDD_TRIN_C=1.042, TVDD_TRIN_W=0.268, TVDD_DEPTH=0.426, TVDD_DD_MIN=0.291, TVDD_VIX_PANIC=28.0`. 15 DTE `not_wired`.

**Validation — Stage-3 T1-T7 ALL PASS** (B N=100x6 -> C N=300x10 -> D N=500x10 ship-gate, all COVID-inclusive). Headline deltas (base->TVDD WorstDD): 5y 64.8->61.7 (+3.1pp, compound +478k%->+558k%/+17%); 2020_crash 77.5->69.2 (+8.3pp); 2022 61.3->57.3 (+4.0pp, +125%->+165%); 22-now 61.8->64.0 (-2.2pp, the compound-coupling window, compound +176k%->+226k%/+28%). Pareto: -3.1pp 5y WorstDD AND +17% 5y compound; collapse=0 every window incl 2020+2020_crash. Comparable to RXDD (-5.6pp/+9.4%), SVR (-5.8pp/+28.6%), MWDD (-2.6pp/flat).

**Wiring (mirrors MWDD).** strategy_config (30DTE on/15DTE off) + mechanism_registry (TVDD spec) + monte_carlo + backtest_cascade (`load_trin_map`; vacuum call stays TVDD-inert per maps-default-None pattern) + api (`/api/trader/simulate`+`/api/backtest/run`) + trader + Backtest.js (DEFAULT_ADVANCED + buildAdvancedFromConfig + FIELD_TIPS + send-keys) + drift-guard (627) + driver ENV_MAP. Revert: `STRATEGY_30DTE.TVDD_ENABLED=False`. Two clean nulls banked: concurrency-DD now CAPTURED (old dd_ledger concur=hi 5.95x -> most-crowded band now lowest dd_conc 0.68); VIX-velocity is a clean monotonic EV signal but anti-aligned for contraction (poor DD lever; a velocity refinement of RXDD's level-band is a possible micro-lead). Record: `experiments/dd_residual_v70/{mine.py,sweep.py,FINDINGS.md}`.

## Portfolio Release: MWDD McClellan breadth-momentum flat-band call dampener - 2026-06-05

Portfolio-only ship (no version bump; scoring stays v70). Commit `d79f8a144`, pushed. Answers: "the portfolio drew down on a market pullback; can we use the Market Wave to protect ourselves?"

**Two-part answer:**
- **Crash-state contraction is FALSIFIED.** Mining v70 Apex MC tape (#63, 6.86M call trades, live RXDD+SVR substrate): Market-Wave/breadth crash cohorts (low Market Wave, negative McClellan, high crash_echo) are mean-reversion WINNERS (+0.05 to +0.18 mean option pnl in 11/12 windows) — contracting calls there is the documented "DD-mitigation is structural" crash-artifact trap, now confirmed for the explicit Market Wave. The Apex edge is buying weakness, so calls into breadth-weakness mean-revert and win, hardest during crashes. 2020/2020_crash/10y drawdowns are irreducible by call contraction.
- **Shippable signal: flat/topping breadth-momentum band.** Low-EV (+0.046) AND DD-concentrated (conc 2.04) cohort = flat McClellan band (~0), the breadth-momentum analog of RXDD's VIX 20-28 slow-bleed. Stays low-EV when VIX<20 (orthogonal to RXDD) and breadth>=40 (orthogonal to F3F). Contracting it helps bull/choppy years but HURTS crash windows (2020_crash flat-band cohort +0.234) — rescued by VIX-panic exclusion (RXDD's leave-panic-alone trick: >=28 = capitulation = winners, untouched -> COVID collapse-safe).

**Track B (regime/market-wave weights):** live `compute_regime_composite` is a dynamic VIX + inverted-breadth blend (trend=0); `SIGNAL_WEIGHTS` dict is vestigial. Regime inverts breadth on purpose (weak breadth -> amplify, for signal reliability/WR) -> structurally cannot protect drawdown (2026-06-05 breadth-27 pullback multiplier was 1.03, amplifying). Prior regime-weight A/Bs (F/G/H/I) all lost -> recalc-free place to tune market wave is portfolio sizing (MWDD).

**Mechanism (MWDD).** Gaussian bump on McClellan oscillator contracts CALL alloc in flat band: `alloc_frac *= 1 - DEPTH.exp(-0.5.((mcc-MCC_C)/MCC_W)^2)`, no-op when disabled / McClellan missing / running DD < `MWDD_DD_MIN` / VIX >= `MWDD_VIX_PANIC`. McClellan threaded as date->value map (`MarketBreadth.mcclellan_oscillator`) like RXDD's VIX. `STRATEGY_30DTE` c00: `MWDD_ENABLED=True, MWDD_MCC_C=-0.336, MWDD_MCC_W=22.185, MWDD_DEPTH=0.337, MWDD_DD_MIN=0.128, MWDD_VIX_PANIC=28.0`. 15 DTE `not_wired`.

**Validation — Stage-3 T1-T7 ALL PASS** (B N=100x6 -> C N=300x10 -> D N=500x10 ship-gate, all COVID-inclusive). Every window's DD improves (base->MWDD WorstDD): 5y 67.4->64.8 (-2.6pp), 22-now 67.3->61.8 (-5.5pp), 2024 51.7->43.4 (-8.3pp, biggest cut), 2020_crash 79.3->77.5 (-1.8pp, smallest). collapse=0 everywhere incl COVID; compound flat (5y med 532,466%->478,047%, same OOM, MC noise). dd5y stable +2.6/+3.5/+2.6 across N=100/300/500. 2nd orthogonal DD lever stacking on RXDD(VIX)+SVR(skew)+F3F(breadth level).

**Wiring (mirrors RXDD).** strategy_config (30DTE on/15DTE off) + mechanism_registry (MWDD spec) + monte_carlo + backtest_cascade (`load_mcclellan_map`; vacuum call left MWDD-off per G9) + api + trader + Backtest.js (DEFAULT_ADVANCED + buildAdvancedFromConfig + FIELD_TIPS + field-list) + drift-guard (615) + driver ENV_MAP. Reversible: `MWDD_ENABLED=False`. Record: `experiments/market_wave_dd_v70/{mine.py,sweep.py,FINDINGS.md}`.

## Portfolio Release: SVR semivol_r skew-bridge entry filter - 2026-06-05

Portfolio-only ship (no version bump; scoring stays v70). Commit `7e6f8fe19`, pushed. The ONE shippable win from the 2026-06-04/05 apex-speed overnight run (user's "50k->200k / sizing-allocation-trend / current-regime" ask) — every other sizing/regime lever that session was NULL (static 100% exposure collapses COVID; EXR size+VIX exposure ramp null; DD-band retune within MC noise; trailing stop re-confirmed null under honest v70; regime-adaptive TP/SL + regime-mult + signal-pool displacement null). The real lever was option-pricing, not sizing.

**Mechanism.** SVR operationalizes confirmed-but-options-data-locked option put-skew alpha (`experiments/iv_skew/`) via the live, 10y-MC-computable cousin `semivol_r = std(downside 60d returns)/std(upside 60d returns)` (high = downside-heavy = put-skew-like = cheap call). Smooth band-pass on CALL allocation (calls only): full alloc in `[SVR_LO_FULL, SVR_HI_FULL]` sweet spot, contracting linearly toward `SVR_FLOOR` down to `SVR_LO_CUT` (euphoric/expensive-call low-svr cohort, worst per-trade) and up to `SVR_HI_CUT` (crash-mode high-svr). Cohort (10y, 75+ option-P&L by svr quintile): inverted-U 0.35->0.55->0.51->0.39; underlying barrier apex15 +1.9pp z=+11.57 (N=394k), orthogonal to price score.

**Ship config (STRATEGY_30DTE, gentleband c00):** `SVR_ENABLED=True, SVR_LO_CUT=0.50, SVR_LO_FULL=0.70, SVR_HI_FULL=1.25, SVR_HI_CUT=1.65, SVR_FLOOR=0.50`. STRATEGY_15DTE disabled (schema parity, `not_wired`).

**Validation (Stage-3 MC, B->C->D, vs live Apex RXDD-on baseline):** B N=100x6 gentleband +0.106 compound/+0.122 bear/dd5y -2.7pp/collapse 0. C N=300x8 incl COVID Pareto, collapse=0. **D ship-gate N=500x8 incl 2020-COVID:** 5y WorstDD 73.2->67.4 (-5.8pp) AND 5y compound +28.6% (med 413,972->532,466); 22-now DD -5.6pp/compound +40%; dip DD -6.4pp; 2022 -0.8pp; COVID DD 83.3->79.3 (-4.0pp); max annual DD regression +1.4pp (2024) <<T5 +5pp; collapse=0 every window incl COVID -> T1-T7 all PASS. Lone soft spot: 2024 median compound -18% (far inside T7 +-3 OOM). Live-engine smoke (`run_cascade_backtest`, 2024-06->now, $50k, 75+): OFF $4.84M/68.3% -> ON $4.42M/63.9% (-4.4pp).

**Wiring (13-consumer, RXDD-mirror).** New live feature `database/utils/semivol.py:compute_semivol_r` (verified 10/10 match to `experiments/iv_skew/build_proxy.py` cache build). Live engine (`backtest_cascade.run_cascade_backtest`) computes `semivol_r` inline per signal and stamps onto `TradeOutcome.semivol_r` in the build loop, so the `compute_and_store_temporal` per-month vacuum call applies SVR consistently with no separate map. MC uses static cache `.cache/apex_speed_v70/semivol_map.parquet` (built by `build_semivol_cache.py`). Plus: `monte_carlo.py`, `strategy_config.py`, `mechanism_registry.py`, `tests/test_strategy_config_drift.py` (6 SVR pairs -> 603 constants), `trader.py`, `api.py`, `src/pages/Backtest.js`.

**Reversal:** `STRATEGY_30DTE.SVR_ENABLED=False` (or env `SVR_ENABLED=0`) = byte-identical baseline. Record: `experiments/apex_speed_v70/{FINDINGS,SVR_SHIP_HANDOFF,sweep_speed}` + `svr{B,C,D}_results.json`.

## Portfolio Release: RXDD VIX-band call-alloc dampener - 2026-06-04

Portfolio-only ship (no version bump; scoring stays v70). Commit `9b0ab6604`, pushed. Regime-aware, drawdown-shaping CALL-allocation dampener discovered by mining v70 Apex MC trade-tape (`MC_TRADE_TAPE=1`): VIX 20-28 entries are the worst call cohort (loser-rate z+52, ~break-even EV, safe to contract) while VIX>=28 panic entries are the best (z-90, +15% mean, left alone). RXDD smoothly contracts call alloc in that low-EV "slow-bleed" band via a Gaussian bump, gated to running DD>=DD_MIN: `alloc_frac *= 1 - DEPTH.exp(-0.5.((vix-VIX_C)/VIX_W)^2)`, no-op outside the band/when disabled/vix unavailable.

Winner c00 (`VIX_C=22.701, W=3.14, DEPTH=0.447, DD_MIN=0.077`) on `STRATEGY_30DTE` (15 DTE disabled, `not_wired`). Validated N=100 (B LHS-16) -> N=300 (C, COVID-incl) -> N=500x8 (D ship-gate), Stage-3 T1-T7 all PASS: 5y WorstDD 78.8->73.2 (-5.6pp) AND compound +9.4%, 22-now DD -6.4pp/+35%, 2022 DD -9.6pp/+89%, collapse=0 every window incl 2020-COVID (VIX in >=28 panic band sits outside the bump -> COVID untouched -> collapse-safe). Cuts drawdown AND raises compound — Pareto win, robust across N=100/300/500.

Wired across strategy_config, monte_carlo, backtest_cascade (vix threaded into `run_backtest` + `cfg.get` for `/api/backtest/run` override), trader.py, api.py, Backtest.js, mechanism_registry, drift-guard pairs_mc/pairs_bc. Also fixes a latent polars schema crash in `_dump_trade_tape` (`infer_schema_length=None`). Reversible: `STRATEGY_30DTE.RXDD_ENABLED=False`. Two valuable nulls: regime x DD `entry_dd x breadth` contraction is a crash artifact (positive-EV in bull years, the "DD-mitigation is structural" trap); explosion-winner analysis is null (top-decile runs ~= rest; sequencing variance, no learnable entry signature). Record: `experiments/regime_dd_v70/{FINDINGS,SHIP_HANDOFF,mine,sweep}`.

## Portfolio Release: Apex overflow + dead-hold popout-15 + notification fix - 2026-06-03

Portfolio-only ship (no version bump; scoring stays v70). Commits `df9e8adb1` (overflow + allocator + router gate + regime analysis), `a4cbfee82` (dead-hold popout-15 + verdicts), `f164a8b88` (notification SL-staleness fix). Harness: `experiments/component_reweight/` (`REGIME_KNOB_PLAN.md`, `FINDINGS.md`, `OVERFLOW_HANDOFF.md`, `run_*_sweep.py`).

**SHIPPED (Apex):**
1. **70-74 overflow @0.035** (`STRATEGY_30DTE.TIER_ALLOC['overflow']` 0->0.035 + `portfolio_profiles.json` apex.tier_overflow; Core/Sentinel left 0). Apex 75+ book ran mostly idle (occupancy ~3204 of ~34k eligible); overflow fills it inside the 50% gross cap (never displaces higher-conviction 75+). N=500x9 windows: collapse-safe on every config x window (max 0.2%=baseline; 0.040 hits 0.0% COVID), 10y +2.38M%->+35.9M% (15x) at 0.035, DD in ~86% budget, hydration 22%->89%. EXECUTION-CONDITIONAL: holds under LIVE asymmetric cost canon (limit-TP avoids spread); under a conservative symmetric ~3% round-trip it evaporates (5y hurt) and baseline shows ~3% worst-case-crash-entry collapse — durable wins are hydration/DD/collapse, not compound scale.
2. **Dead-hold POPOUT -0.25->-0.15 + TRIGGER -0.50->-0.40 (30 DTE only; 15 DTE left -0.50/-0.25).** N=500 near-Pareto: 10y ret x2.07 AND -2.7pp DD, more return on every window, collapse=0. Letting dead-hold recoverers run to a -15% exit captures strong rebounds (dh_pop reaches +357%). Dead-hold is collapse-PREVENTING — `dh_off` (clean -70% SL) AND premium-stops = 100% collapse (cutting deep losses realizes them simultaneously in a crash; dead-hold defers/spreads them). HOLD>>CUT at the portfolio-survival level.
3. **15DTE router VIX_MAX crash-gate wired** (`dte_router.py`+`monte_carlo.py`+`backtest_cascade.py`+`strategy_config`+`mechanism_registry`, default 0=off). Lets router be broadened with a low-VIX-only gate — broadening itself is a NULL, live tiny 1/day router unchanged.
4. **Notification SL-staleness fix** (`notifications.py`, `f164a8b88`). `_apply_opportunity_targets` early-returned when targets were set, freezing the stop at log-time; opportunities logged under the old -27% SL kept that stale (~2.6x tighter) stop and fired SL-breach alerts on daily noise. Now recomputes TP/SL from live config each pass (reusing stored entry sigma), tracks the live -70% SL, self-heals config changes. 2 open rows (NET, ATI) corrected.

**Allocator reworked** (`src/pages/Allocator.js`): pie chart + clean buy-list, puts hidden when off, default profile Apex.

**The 7-SL audit (user-flagged):** model is HONEST, no inflation-style bug. `sl=7` of 6458 is a labeling artifact — every -70% SL touch intercepted by the dead-hold (relabeled `dh_*`). Real deep losses present: 14.2% of trades lose >=70% (`dh_expiry` mean -90%, min -101%=total+spread). Theta priced via `option_pnl_pct(...,bars_held)` (closed-form sqrt(T) decay + delta + sampled empirical vega); day-15 hard-sell is option-model-priced (realized mean -37%, range +7% to -94%), not the flat -40% constant (theta-derived nominal/legacy fallback). The 77% DD is real.

**Regime analysis:** Apex's forward-20d return turns negative in breadth<30 (-0.086) and VIX 25-30 (-0.017); F3F breadth knob floors at 0.50 = half-size into the bleed zone. F3F floor 0.30 cuts 10y(COVID) DD -3.4pp but costs -19% tail return (Core/Sentinel tilt, not wired to Apex — dead-hold popout was the cleaner DD lever).

**NULL/FALSIFIED this ship (also in known-issues WHAT NOT TO DO + CLOSED — NULL RESULTS):**
- **Hard-sell-7** (days-held U-shape): CATASTROPHIC — HOLD_DAYS 15->7 craters 10y ret ~1000x + collapse breach. Day-15 hold is the return engine; 89% hydration doesn't save cutting TPs early.
- **15DTE router broadening:** breaches collapse 1-4.7% + loses return vs the tiny router (the "10x prize" was a pure-15DTE COVID-ruin mirage). Keep DAY_CAP=1.
- **Puts reserved-cash-ledger (<15 hedge):** net-negative — profit only in sharp COVID crashes (+12%), loses every other window incl 2022-inflation.
- **Component/weekly/ICH scoring reweight:** null (honest v70 well-calibrated).

## Portfolio Release: v70 Apex / Core / Sentinel Profiles - 2026-06-02

Stage 3 portfolio-only ship (no version bump; scoring stays v70). Full options-strategy rebuild on the honest v70 substrate (calls-only, realistic ~3% spread, HOLD core, full 10y incl 2020-COVID), replacing the v69-Honest "hygiene / not-fundable" retune below. Three selectable profiles on a return-vs-DD frontier, all puts-off, collapse=0 on every window. Default = Apex. Canonical handoff: `experiments/v69_portfolio_retune/PROFILES_SHIP_HANDOFF.md`.

| Profile | Threshold | Exposure | Ceiling | 10y MedRet | 5y DD | Role |
|---|---|---|---|---:|---:|---|
| Apex (default) | 75+ | 50% | uncapped | +16,953% | 84% | explosive (early/small book) |
| Core | 75+ | 40% | $2M | +7,422% | 79% | balanced |
| Sentinel | 85+ only | 30% | $1M | +3,371% | 37% | preservation (mature/large book) |

**Shared HOLD call engine** (`strategy_config.STRATEGY_30DTE`/`OPT_30DTE`, = Apex/default): TP +30%, SL -70% (base==stress, breadth-adaptive off), hard-sell day 15, SLIP -0.015/leg (~3% round-trip), cascade 20/15/10/10, MaxPos 14/14/0, puts OFF. Profiles differ only on exposure cap + threshold (Sentinel zeroes 80-84 & 75-79 tiers -> 85+-only) + base ceiling, all in `algorithm_versions/portfolio_profiles.json`. Apex validated N=300 (+17,026% / 0 collapse / 86% DD).

**Load-bearing findings:** (1) HOLD >> CUT for calls (wide SL, sell day 15 — don't cut the ~68% of losers that recover; at real spread, high turnover is the bleed; CUT-at-70+ collapsed 100%). (2) Exposure peaks ~50%, over-deployment HURTS (capital-velocity law: bigger sizing -> deeper DD -> less survives to compound; 50% +16,953% > 65% > 100%/off +6,365%). (3) Capacity ceiling is a pure growth dial — DD/collapse scale-invariant ($250k->uncapped flat ~86%/0%; only MedRet moves +3,623%->+16,953%) -> Apex uncapped, Core/Sentinel capped for realism at larger books. (4) Selectivity (85+) is the DD lever, NOT exposure (Sentinel 85+ cut 5y DD 84%->37%; Core's exposure cut only reached 78.6%). (5) Puts closed — tested to a <15-only/5%-cap/1-slot sliver (CUT and HOLD, putTP up to 63.6%): every sliver introduces collapse (0.5-8%, baseline 0%), worsens 2022 DD +5 to +8pp, guts 10y return 33-52%. Net-harmful (put losses cluster with call stress + consume the cash buffer). Do not re-open without a fundamentally different mechanism (e.g. a reserved-cash put ledger that can't touch the call buffer — untested).

**Risk framing:** collapse=0 is the hard floor for every profile incl Apex (ruin is unrecoverable at any book size); 86% Apex DD is recoverable, the price of compounding off a small early-stage book. Leveraged-momentum sleeve (honest v70 removed ~12pp of look-ahead edge; residual is thin momentum-beta + option convexity), NOT proven risk-adjusted alpha vs buy-and-hold.

**Wired across** allocator, Backtest (profile toggle snaps all advanced params + Calls Only + min call score; default Apex), assessment calendar, MC, deterministic backtest, CLI. drift-guard 579 + registry 138 green. Evidence: `experiments/v69_portfolio_retune/` (`profile_frontier.py`, `n300_confirm.py`, `ceiling_curve.py`, `put_tail_tiny.py`, `MASTER_FINDINGS.md`).

## Portfolio Release: v69-Honest Retune (HYGIENE, NOT FUNDABLE) - 2026-05-31

Stage 2+3 portfolio-only ship (`b85c514bb`, no version bump). After v69 removed the weekly look-ahead, v60-era params were found to COLLAPSE on honest scores (MC 5y/22-now 100% collapse, 92% DD). Retuned: 85+ calls only (TIER mid/low=0; 80-84/75-79 TP < BE), puts OFF (PUT_TIER=0), TP_BASE 0.33->0.28 (capital velocity; wider SL hurt), MaxPos 14/12/8->8/7/2. N=500 confirm: 5y +130%/35% DD, 0 collapse, 7/8 windows positive, puts truly off. Also added `monte_carlo.py` `SLIP_*_OV` env knobs (cost-diagnostic infra).

**Shipped only as hygiene** so the live dashboard reflects the honest best config instead of a collapsing one — NOT a capital-allocation recommendation. Three diagnostics (`experiments/v69_portfolio_retune/FINDINGS.md`) show the strategy does not beat buy-and-hold SPY (5y +101% / ~14%/yr / ~25% DD): D1 stock-selection alpha statistically insignificant (market-adj +1.03%/15d, t=1.25, mostly momentum beta); D2 at realistic ~6% options spread the 5y goes -45% (break-even ~3%; even liquid-only 2.5%-spread ~= 7%/yr at 59% DD); D3 the 2022 "crisis alpha" was option convexity luck on ~3 names (underlying signal lost money). Big edge was look-ahead (removed in v69); residual is thin momentum-beta + convexity. Recommendation: hold SPY/MTUM; do not fund; do not open-endedly mine.

## Portfolio Release: Sentinel Practical Exposure Profile - 2026-05-21

Stage 3 portfolio-only release; preserves `Score.overall`, keeps scoring `ALGORITHM_VERSION` unchanged, no score row recalc. Wires practical exposure saturation into production-equivalent 30DTE allocation: practical base `min(portfolio_value, $25M)`, gross premium cap 80%, call premium cap 65%, put premium cap 25%, opportunity saturation refs 16 calls/4 puts with power 0.5 and floor 0.55, global 14 slots, call cap 12, put cap 8.

Version identity correction: Sentinel/Core/Apex are portfolio-profile versions tracked separately from scoring `AlgorithmVersion` rows.

Promotion candidate `g80_c65_p25_ref16_4_pow05_floor55_25m` — best production-clean controller after rejecting the active-v60-only g75 optimum and the original g75 discovery shape (cross-version DD regressions / weaker practical-floor behavior). N=1000 cross-version confirm (`.codex/runs/g80_cross_version_confirm_20260520_220338`): +10.49pp avg worst-DD improvement, +9.86pp avg mean-DD improvement, +0.00pp max DD regression, 75% floor pass, avg open premium/base 62.6%.

Post-wiring v60 validation (`sentinel_g80_postwire_20260521_044902`, N=160 across 2020_crash, covid_peak, 2020_full, 2022, 2024, 2025, 2025_dip, 2022_now, 5y): +10.33pp avg worst-DD improvement, +11.51pp avg mean-DD improvement, +0.00pp max worst-DD regression, +13.32pp 2020_crash DD improvement, +17.59pp covid_peak DD improvement, no 2025_dip DD regression. Mean final wealth stays above the practical $2M floor on 7/9 windows (2020_full $12.47M, 2022 $6.93M, 2025_dip $2.19M, 2022_now $693.76M, 5y $886.00M from $50k).

CT crash suppression and wave-put reserve overlays remain future work (CT still depends on a clean production feature source; reserve needs live reserved-cash ledger semantics). Sentinel is the strongest clean ship surface, not the largest research-only overlay stack.

Follow-up risk-profile canonization completed 2026-05-24 without changing production strategy params or `ALGORITHM_VERSION`. Registry `algorithm_versions/portfolio_profiles.json` tracks Sentinel/Core/Apex portfolio overlays. Sentinel = green safe default. Core v1 = `core_g85_c70_p25_ref18_4_floor60_dd405565` (85/70/25 caps, 18/4 refs, 14/12/8 slots), current balanced candidate. Apex v2 = `apex_fullportfolio_g90_c74_p24_ref20_dd456575` (no practical capital ceiling, 90/74/24 caps, 20/4 refs, 16/14/8 slots), explosive full-portfolio profile. Evidence: `.codex/runs/apex_fullportfolio_compare_20260524_031304/findings.md`, N=500 compare across nine v60 windows: Apex v2 added +0.05698 avg log-final lift with +7.93pp avg worst-DD, +14.71pp max worse, 74.02% max candidate worst-DD, +5.48pp 2025_dip DD worsening, 0% collapse. Apex temporal stats refreshed at `.codex/runs/temporal_apex_fullportfolio_20260524_034934`. Dashboard comparison surface: `/portfolio-profiles`.

---

## Older Versions (v60 and earlier) — Archived

Pre-honest-era ship logs (v60 -> v18, plus 2026-05-and-earlier Monte Carlo, Session-Commit, and phase history) live in **[version-history-archive.md](version-history-archive.md)** to keep this file lean. Open the archive when you need a specific older version's mechanism, WR table, or commit log.
