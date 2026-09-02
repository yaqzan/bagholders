## Optimal Trading Strategy (MC-Derived)

Current best strategy as validated across all Monte Carlo runs. Update whenever new MC findings supersede a parameter.

**Active scoring version: v73 (`07e9722b5`, shipped 2026-06-12).** v72 minus FIVE retired pre-v69-calibrated score-stage dampeners — WCF, ICH, CWCF, CSWC, SCW (CWWD + WVD kept) — on honest ReSim A/B: **75+ supply +77% at blended −1.6pp optWR15**; put assessment band restored ~8× N at flat WR (puts OFF — surface only). Growth gate dG +19.6% option / +36.3% generic, every window; MC smoke collapse=0. **At unchanged Apex params the book runs DD +5-13pp hotter (v71 signature) — the Stage-3 density retune is the mandatory follow-up.** Evidence: `experiments/dampener_ablation_v72/FINDINGS.md`.
- v72 (2026-06-11): v71 + WCF score-gate ramp (`WCF_LIFT_RAMP_TOP=33`, now inert): smoothed v27 WCF 27/28 post-regime cliff, per-trade NEUTRAL, fakeout groups −60%. `experiments/wcf_score_ramp/FINDINGS.md`.
- v71 base (2026-06-10): integrity-audit fixes on v70 — SPY-weekly look-ahead removed (F2), point-in-time mcap (F4), four mechanisms retired on honest A/B (mis_stress, JA4 put blend, MCD, Sector Market Wave) — 75+ supply +83% at WR15 +1.9pp vs v70 (5y assess).
- v70 base: honest (look-ahead-free) scoring — v69 weekly-transition blend + v70 EARN_BOOST honest recalibration; ~12pp of the old per-trade edge was look-ahead; every tradeable tier still clears break-even honest.

**Active portfolio strategy: Apex / Core / Sentinel profiles (portfolio-only, no `ALGORITHM_VERSION` bump). RESTRUCTURED 2026-06-17 — default flipped Apex→Core.**
- **Core = default held long-run compounder** (former Apex: 75+ monotonic cascade `0.20/0.15/0.08/0.03`, 50% gross/call cap, uncapped ceiling, MaxPos 14 — retuned twice: v71 2026-06-10 `low 0.10→0.05, overflow 0.035→0`; v73 2026-06-12 `mid 0.10→0.08, low 0.05→0.03`; v73 Phase D N=500×10 incl COVID: every window's DD improves, 5y 66.3→61.7, 22-now 66.9→61.5, 5y compound +1%, collapse=0; `experiments/v73_portfolio_retune/FINDINGS.md`).
- **Apex = opt-in fast-2x SPRINT — P0.3 switch 2026-08-02: 30-DTE n10** (flat 10% × 10 top-conviction 75+ names, MaxPos 10, 100% gross/call cap, SL−70, overflow off, profile v4) — **stop-at-2x** first-passage tool (vs retired 15-DTE n4 elbow: median compound +4%→+108%, worst DD 88%→76%, collapse→0, P(2x) 57%→72%, median ~191 cal-days to 2x; `experiments/apex_dte_dd/SHIP_HANDOFF.md` Option B, T-gate #610 DD-better 12/12, apex_n10 collapse 0/12), NOT a held compounder (clean-cert held-form DD 47-69%; `experiments/concentration_2x/FINDINGS.md`).
- Old balanced $2M-cap/40% Core is REMOVED. All on the honest substrate (calls-only, ~3% spread, HOLD core, full 10y incl 2020-COVID); puts-off (re-null'd on v71, monotonic, collapse=0 every window). Canonical: `algorithm_versions/portfolio_profiles.json`. Earlier handoff: [`PROFILES_SHIP_HANDOFF.md`](../../experiments/v69_portfolio_retune/PROFILES_SHIP_HANDOFF.md).

| Profile | Threshold | Exposure (gross/call cap) | Sizing | Base ceiling | Held metric | Role |
|---|---|---|---|---|---:|---|
| **Core** (default) | 75+ | 50% | cascade 0.20/0.15/0.08/0.03, 14 slots | uncapped | 5y +1,247.9% / dd 61.7% | long-run held compounder |
| **Apex** (opt-in; 30-DTE since 2026-08-02) | 75+ | 100% | flat 10% × 10 names, 10 slots | uncapped | P(2x)=72% / ~191d (clean-cert held DD 47-69%) | fast-2x stop-at-2x sprint, NOT held |
| **Sentinel** | 85+ only | 30% | cascade 0.20/0.15 (mid/low 0) | $1M | +3,371% 10y / 37% 5y DD | preservation (mature/large book) |

> **Apex is a STOP-AT-2x sprint, not buy-and-hold.** Time-to-2x is a first-passage objective, MORE aggressive than the compound-optimum, bounded by collapse=0 (≈ a 3-name floor). Run continuously it is negative-compound — realizes value only via manual stop-at-2x (no auto-stop in the live engine; an auto-rotate-at-2x feature was considered and deferred). Core is default for held capital.

Shared HOLD call engine (`strategy_config.STRATEGY_30DTE` / `OPT_30DTE` hold Core/default values): 30 DTE ATM calls, **TP +10% / SL −100%** ("scalp-and-dead-hold", 2026-08-10 tpsl_refine retune; SL−100 ≈ 3.64σ disaster stop, deep fires reroute to dead-hold; base==stress, breadth-adaptive re-tested + REJECTED at N=500 same campaign; **Apex overrides TP+10/SL−60 pinned in its profile params**; prior TP+30/SL−70 era 2026-06-02→2026-08-10), **hard-sell day 15**, **SLIP asymmetric: entry/limit-TP free, forced exits −0.015** (canon 2026-06-02), cascade **20/15/08/03** (95+/85-94/80-84/75-79; v71 low 0.10→0.05 + v73 mid 0.10→0.08/low 0.05→0.03; Sentinel zeroes 80-84 & 75-79 → 85+-only), **MaxPos 14 all calls**, **puts OFF**. Profiles differ ONLY on exposure cap + threshold + base ceiling.

Load-bearing findings: HOLD ≫ CUT for calls; exposure peaks ~50% (over-deployment hurts — capital-velocity law); capacity ceiling is a pure growth dial (DD/collapse scale-invariant) so Apex runs uncapped; **selectivity (85+) is the DD lever, not exposure**; puts are net-harmful even as a 1-slot tail-fill (rigorously closed). collapse=0 is the hard floor for every profile incl. Apex — the 86% Apex DD is recoverable, the accepted price of early-stage compounding.

> **SUPERSEDED:** the v60-era config (r054 SCW 12-call-cap, Sentinel practical-exposure $25M base/80% gross/puts-on, TP 0.33/SL −0.27, cascade with puts) in the param tables and "Authoritative ship state" block further down is **REPLACED** by the v70 Apex/Core/Sentinel profiles above. Where tables disagree, profiles + `strategy_config.py` + `portfolio_profiles.json` win.

> **30 DTE is the definitive primary instrument (confirmed 2026-05-01).** Under bounded-fill option-pricing-aware MC (N=500×8 windows, v31 f3ec7c1), 30 DTE beats 15 DTE on every window on both median return and worst DD (15 DTE median negative on dip −57% and 2023 −44%; 30 DTE WorstDD 57-74% vs 15 DTE 73-80%). Root cause: 15 DTE's smaller premium (1.29× vs 1.82×σ) means gap-downs wipe more premium under bounded-fill; the retired DD circuit breaker fired too late. 15 DTE remains shipped as parallel strategy but is not the optimization focus.

### Instrument & Entry
- **Instrument**: ATM options, **30 DTE** — **calls** on scores ≥ 70, **puts** on scores ≤ 25
- **Universe (calls)**: ≥ 70; 70-74 is overflow tier, filled after all 75+ call slots
- **Universe (puts)**: ≤ 25; current scores include asymmetric weekly 1.5× put scale + asymmetric MACD gate lineage
- **Entry trigger**: any day a qualifying score is produced
- **Side priority**: sort signals by conviction (distance from 50), walk top-to-bottom; calls/puts compete for the same 14-slot pool, 12-call/8-put side caps; same-symbol block across sides

### Position Sizing — Cascade Allocation
Sort eligible signals conviction-descending each day; walk top-to-bottom.

**Call side (score ≥ 75 — 70-74 overflow disabled):**

| Score bucket | Allocation per trade | At $50k | At $100k | At $250k |
|---|---|---|---|---|
| **95+ (ultra)** | **20%** | $10,000 | $20,000 | $50,000 |
| **85-94** | **15%** | $7,500 | $15,000 | $37,500 |
| **80-84** | **10%** | $5,000 | $10,000 | $25,000 |
| **75-79** | **10%** | $5,000 | $10,000 | $25,000 |
| **70-74** | **0% (disabled)** | — | — | — |

**Put side (score ≤ 25):**

| Score bucket | Allocation | At $50k | At $100k | At $250k |
|---|---|---|---|---|
| **≤15** | **12%** | $6,000 | $12,000 | $30,000 |
| **16-20** | **10%** | $5,000 | $10,000 | $25,000 |
| **21-25** | **8%** | $4,000 | $8,000 | $20,000 |

**Rules:**
- Skip a signal if remaining cash < tier cost
- Stop filling at 14 concurrent positions, 12 concurrent calls, 8 concurrent puts, or signal list exhausted
- Cost basis = tier % × allocation base × F3f scale. Sentinel practical-exposure profile: allocation base = `min(current portfolio value, $25M)`.
- Sentinel also caps total open premium at 80% of practical base, side caps 65% calls / 25% puts.
- Opportunity saturation: `scale=max(0.55, (ref / pressure)^0.5)`, `ref=16` calls, `ref=4` puts.
- Put cascade 12/10/8 (put_top/mid/low) — monotonic by put conviction (v32_optim 30DTE retune). Canonical in `strategy_config.py`.
- **Call ultra split (95+ @ 20%)** replaces merged 85+ tier — 95+ has best WR15 but tiny-N. Current 30DTE cascade 20/15/10/10: Phase 7 trimmed prior 25% ultra + fixed non-monotonic H4 pattern; v32_optim flattened 80-84/75-79 at 10%.
- **70-74 overflow disabled.** Any non-zero allocation breaches the 80% Conservative DD floor on 2025/22-now (Phase 6c decomposition + Phase 8 sweep, 2026-04-17). Correlated-DD cost of overflow exceeds signal-volume contribution.
- **Breadth-driven allocation knob (F3f) — shipped 2026-04-24, replaces composite-driven scaling.** Call/put allocations scale off `MarketBreadth.breadth_score` per signal date, bypassing `regime_multiplier` (inverted 2026-04-09).
  - **Calls**: `scale_call = 1.0` if `breadth ≥ 50`, linear to `0.70` at `breadth = 20`, clamp `[ALLOC_SCALE_FLOOR=0.25, ALLOC_SCALE_CEIL=1.75]`.
  - **Puts**: `scale_put = 1.0` if `breadth ≤ 75`, linear to `0.75` at `breadth = 95`, same clamp.
  - Applied as `tier_alloc × scale_{call|put}` per signal at signal date.
  - Validated 2026-04-24 canonical 3-mode MC (N=150) vs prior production: 5y Realistic compound +121%, 22-now +90%, DIP-5m +18%. Max Conservative DD-C 64.5% (under 80% floor), no Realistic-mean window losing >25% vs B, 0% collapse.
  - Portfolio-stage only — no scoring change, no recalculate, no version bump. Legacy `regime_multiplier` path preserved behind `BREADTH_ALLOC_ENABLED` flag in `monte_carlo.py`, `backtest_cascade.py`, `api.py`, `trader.py`.
  - **Why it fixes the dip**: 2026-04-09 composite inversion mislabeled narrow-bull days (low VIX + healthy breadth) as STRESS (mult≈0.79, calls over-contracted) and narrow-stress days as near-NEUTRAL (mult≈1.0, no contraction in weak markets). Nov 2025–Mar 2026 dip: calls 55% TP rate / −0.5% avg PnL across 202 trades despite normal ~70% per-trade barrier-touch WR — loss came from oversizing during weak-breadth tape via inflated composite. F3f re-anchors on `breadth_score` directly.
  - ~~Regime-aware scaling (2026-04-17, CUT_ONLY asymmetric — Phase 13+15)~~ superseded 2026-04-24 by F3f (same asymmetric shape, re-anchored on `breadth_score` instead of the misaligned composite).
  - ~~Earnings-window put suppression (EARN_SUPP_PUT), shipped 2026-04-26~~ **RETIRED 2026-05-06**, replaced by score-stage **PESS** (v39, `scoring.py`): softens `overall ∈ [PESS_GATE_LO=16, PESS_GATE_HI=20]` puts toward `PESS_TARGET=28` over earnings-proximity window (`PESS_DAYS_MIN=1..PESS_DAYS_MAX=7`, full effect `d_to_ern ≤ 5`). `EARN_SUPP_PUT=False` in `strategy_config.py` both DTEs. Historical mechanics: dropped puts `overall∈[16,20]` within 5 trading days of an EarningsDate; removed ~982 put-slot occupancies/year. N=1000 canonical MC validation: 5y compound +44.7% Realistic vs baseline, all annual windows ±7%, 5y DD-C improved 77.1%→75.0%, 0% collapse. Constants `EARN_SUPP_PUT*` in `monte_carlo.py`/`backtest_cascade.py`/`trader.py`.

### Exit Logic (whichever triggers first)

**Call side — breadth-adaptive TP/SL (H5 lineage, shipped 2026-04-28; 30 DTE retuned by v32_optim 2026-05-04; slippage zeroed 2026-04-30). 30 DTE (`OPT_30DTE`) values below; 15 DTE (`SHARED_OPTION`) differs — see 15 DTE table at end:**

| Trigger | Condition (30 DTE) | Option P&L |
|---|---|---|
| **TP (base)** | Underlying hits +TP σ-barrier | **+33%** on premium (`TP_BASE`) |
| **TP (stressed)** | Wider barrier when breadth_score ≤ 40 | **+42%** (`TP_STRESS`) |
| **SL (base)** | Underlying drops to −SL σ-barrier | **−27%** (`SL_BASE`) |
| **SL (stressed)** | Wider barrier when breadth_score ≤ 40 | **−40%** (`SL_STRESS`) |
| **Hard sell** | Day 15, no trigger yet | **−40%** (`HARD_SELL_LOSS`) |
| **Dead-hold** | SL fires AND pnl ≤ −50%: hold to −25% recovery (intraday) or Day 15 | see below |

Stress band switches at `breadth_score ≤ BREADTH_THRESHOLD` = 40 (30 DTE) / 50 (15 DTE). σ-barriers derived as `pnl_pct × PREMIUM_MULT / DELTA`, moving with per-DTE `PREMIUM_MULT`.

**Put side — fixed TP/SL (no breadth adaptation):**

| Trigger | Condition | Option P&L |
|---|---|---|
| **TP** | −1.274σ below entry | **+35%** |
| **SL** | +0.728σ above entry | **−20%** |
| **Hard sell** | Day 15 | **−40%** |
| **Dead-hold** | SL fires AND pnl ≤ −50%: hold to −25% recovery or Day 15 | see below |

σ = 60-day realized daily volatility at signal date. Premium multiplier = 1.82×σ_daily (30 DTE).

**Why put SL is tighter (−20%) than call SL (−27% base 30 DTE):** TP=35/SL=20 drops put BE to 36.4% (=20/(35+20)), all annual windows clear. Mirror-SL puts (SL=−35%) underperform calls-only on 22-now by −81M% delta. Do not widen put SL beyond −20%, do not tighten further (`PUT_SL=−10%` is a same-stock re-entry cycling artifact).

**Put SL hard-hold — day-normalized (2026-04-23 investigation; current config `hold=0`, superseded by dead-hold):** SL check suppressed for first N trading bars after entry (TP stays active); SL anchored to entry price, no reset. Hold window: 4 bars Monday entries, 3 bars other days (normalizes to ~6 calendar-day shakeout protection regardless of entry day — 3-bar Mon hold would fire Thu with no weekend gap; Tue-Fri 3-bar always crosses a weekend). `experiments/put_sl_dynamic_hold.py` (N=500, 3 variants, 6 windows×3 modes): raw 5y put TP% 42.5%(hold=0)→60.3%(hold=B); portfolio PutTP% 65.8% (5y); no DD floor breaches; wins/ties A (fixed hold=3) 2022 +7%, 2023 +66%, 5y compound +35%; regressions 2024 −6%, 2025 −13% (within 25% gate). Implemented `PUT_SL_HOLD_BARS_DEFAULT=3` / `PUT_SL_HOLD_BARS_MONDAY=4`. **Variant C (regime-gated: breadth≤threshold→hold=5) is a NULL RESULT** — `experiments/put_sl_hold_c40.py` (N=500, thresholds 40/35/30): every variant loses to B on 5y compound (C40 −402T%, C35 −437T%, C30 −328T%); no DD breaches at any threshold; dead end, ship B as-is.

**Breadth-adaptive SL (30 DTE)**: widens −27%→−40% when `breadth_score ≤ 40` (weak participation, shakeout protection). Evaluated at signal entry date on latest `MarketBreadth.breadth_score`. (15 DTE: −30%→−35% at ≤50.)

**Breadth-adaptive TP (30 DTE)**: mirrors SL — widens +33%→+42% at `breadth_score ≤ 40` (elevated-fear regimes price higher realized vol; wider σ target captures more of the move distribution). One threshold drives both SL and TP. Original TP=30%→35% switch validated +49% compound; mechanism extends to current 30 DTE band. (15 DTE: +35%→+40% at ≤50.)

**Dead-hold post-SL (shipped 2026-05-01):** When SL fires and realized pnl ≤ −50%, hold forward bar-by-bar; exit when intraday high(calls)/low(puts) implies pnl ≥ −25% (popout target), or at Day 15 close regardless. Slot stays occupied throughout (matches real capital lock-up). N=300×8-window (P1 gate): 30 DTE 5y compound +1.0×10²⁹% vs OFF (+8.5×10²⁵%), avg DD 75.4%, max DD 79.3%. Initial N=150 ship used POPOUT=−0.30; N=300 showed −0.30 breaches 80% floor on 5y (30 DTE) / 5y+2022 (15 DTE) — POPOUT=−0.25 clears all windows. **RETUNED 2026-06-03 (30 DTE): `DEAD_HOLD_TRIGGER_PNL=−0.40`, `DEAD_HOLD_POPOUT_PNL=−0.15`** — N=500 near-Pareto win (10y ret ×2.07 AND −2.7pp DD, collapse=0). **Dead-hold is collapse-PREVENTING** — `dh_off` (clean −70% SL) and premium-stops both = 100% collapse (deferral avoids simultaneous crash realization); do not disable or add a premium-stop. 15 DTE left at −0.50/−0.25 (not re-validated).

**H3/v60 DD-soft band call alloc contraction (shipped 2026-05-04, recalibrated 2026-05-19, 30 DTE):** running DD in `[0.35, 0.55]` scales call allocation linearly 1.0×→0.40×; above 0.55 = full floor; below 0.35 = no effect. Calls only. Constants `DD_SOFT_BAND_LO=0.35, HI=0.55, DD_SOFT_CALL_FLOOR=0.40`. Binary `DD_CIRCUIT_BREAKER` retired 2026-05-11.
- N=500×8 (P1 gate, vs PYTHONHASHSEED=0 baseline): 5y DD-C 75.8%→71.4% (−4.4pp), 22-now −3.1pp, 2025 −2.8pp, 2023 −3.5pp. Per-trade quality unchanged (Call TP 58.3%→58.4%, CTrd 3566→3578). Compound within MC noise floor every window. 0% collapse. Disabled 15 DTE (not validated under bounded-fill MC).
- Surfaced via `experiments/dd_ledger/` cohort lift/z mining over 4.83M trade rows (N=300×8 MC): `entry_dd=mid × regime=HEALTHY` = highest DD-PnL concentration cohort (19.7×) on CALL 75-79. v60 r054 sweep selected coupled `callcap12_dd035055f040` form: avg focus DD +0.90pp, no material max-DD worsening, log-return guardrail +0.054, call TP drift −0.02pp, ~7.4 fewer call trades. See `.codex/runs/v60_r054_portfolio_dd_short_retry_20260518_055226`.

**Practical exposure saturation (Sentinel, 30 DTE):** limits deployable premium against a practical capital base rather than theoretical-equity compounding. `PRACTICAL_CAPITAL_CEILING=25_000_000`, `GROSS_PREMIUM_CAP=0.80`, `CALL_PREMIUM_CAP=0.65`, `PUT_PREMIUM_CAP=0.25`, `OPP_SAT_CALL_REF=16`, `OPP_SAT_PUT_REF=4`, `OPP_SAT_POWER=0.50`, `OPP_SAT_FLOOR=0.55`, `MAX_POSITIONS_CALL=12`, `MAX_POSITIONS_PUT=8`. Post-wire v60 N=160 across 9 windows: avg worst-DD +10.33pp, avg mean-DD +11.51pp, max worst-DD regression 0.00pp, 2020_crash +13.32pp, covid_peak +17.59pp, no 2025_dip regression. Mean final wealth above $2M floor on 7/9 windows. Confirmation: `sentinel_g80_postwire_20260521_044902`.

**Broad 15DTE router sleeve (shipped 2026-05-28, 30 DTE overlay):** at most one high-score call/day routes to 15DTE when `score>=80` and `trend<50`; VIX/regime filters, havens, routed-alloc caps off. N=500 all-window: 117 routed 5y signals (0.361%). 2022 mean log delta +0.3876/DD −4.34pp, 2023 +0.0332/DD −2.32pp, 2025 +0.0416/DD −0.31pp, dip +0.0821/DD flat, 5y +0.0133/DD flat. 22-now +0.0170 mean, DD −2.37pp but worst DD +1.54pp (accepted tradeoff). `.codex/runs/dte_router_mc_trend50_n500_20260528_0026`. Portfolio-stage only.

**Portfolio risk profiles (canonized 2026-05-24, v60 rows):** `algorithm_versions/portfolio_profiles.json` — Sentinel = safe default; Core v1 = balanced (`core_g85_c70_p25_ref18_4_floor60_dd405565`: caps 85/70/25%, refs 18/4, floor 0.60, 14/12/8 slots, DD band 0.40/0.55/0.65); Apex v2 = explosive full-portfolio (`apex_fullportfolio_g90_c74_p24_ref20_dd456575`: no capital ceiling, caps 90/74/24%, refs 20/4, floor 0.65, 16/14/8 slots, DD band 0.45/0.65/0.75; evidence `.codex/runs/apex_fullportfolio_compare_20260524_031304/findings.md`: +0.05698 avg log-final lift, +7.93pp avg worst-DD, +14.71pp max worse, 74.02% max candidate worst-DD, +5.48pp 2025_dip DD worsening, 0% collapse). No change to `Score.overall`/`ALGORITHM_VERSION`/score rows.

**Execution Cost Model — canonical, ASYMMETRIC (shipped 2026-06-02).** Supersedes both the all-zero (2026-04-30) and the symmetric "−1.5%/leg ≈3% round-trip" stress. Cost depends on liquidity provide vs take:
- Commissions = $0 (Wealthsimple).
- Entry (buy at mid): `SLIP_ENTRY = 0` — resting mid limit, no spread crossed.
- TP exit (limit sell at target): `SLIP_TP = 0` — post the offer, fill at price or better. Confirmed 2024 backtest: 199 TP winners net +0.32, not +0.30−3%.
- Dead-hold popout: 0 — same as TP.
- Forced exits (SL stop, day-15 hard sell, dead-hold expiry/open): `SLIP_SL = SLIP_HARD = −0.015` — takes liquidity into a (often widened) book, pays the half-spread; raise toward −0.02..−0.03 to model adverse-move widening — the single calibration knob.
- Why asymmetric: old flat round-trip over-charged the ~85% of trades that win (mid-entry, limit-TP legs); real cost lives only on forced exits.
- **Not-yet-modeled (conservative-direction TODOs):** entry mid-limit fill probability <100% (adverse selection on immediate runners), TP-touch-but-bid-didn't-reach non-fills, illiquid-name spread/fill failure (→ Liquidity-Aware Cascade priority).
- Constants in `strategy_config.py` (`OPT_30DTE`/`SHARED_OPTION` `SLIP_*`), read by `monte_carlo.py`, `monte_carlo_15dte.py`, `backtest_cascade.py`, `backtest_cascade_15dte.py`, research-pack windows; drift-guard covers all four engines' `SLIP_HARD`. All existing packs/MC/compound numbers predate this — see `experiments/version_alpha_mining/EXECUTION_COST_CANON_CATCHUP.md`.

**Execution Timing Canon (2026-06-11, `experiments/entry_timing_v71/FINDINGS.md`).** Scores finalize at close; engines anchor entries at signal-day close. Live execution = buy in the last ~30 min (15:25–16:00 ET) on the then-current score: ~15:45 read is 92% close-faithful (~0.4% median price drift, Phase 0 live `score_intraday_logs`); morning/midday reads are partial-day — ~26% of open-qualified signals fade by close AND open-qualification misses 39% of the true close-signal universe. **Next-open entry costs −1.2 to −1.4pp WR** (75+ signals gap up +21bps overnight, t=+5.2) — apply that haircut for at-open buyers. No open-entry mix exists: overnight gap is a strong outcome signal (gap-up cohort 81.4% vs gap-down 61.8% close-anchor WR) yet every gap-filtered next-open policy converges to ~71.5% — selection gain and entry-price drag cancel because the σ-anchored TP moves with entry; gap is priced in by 9:30. A gap-down on a held position is NOT a cut signal (61.8% still clears BE; HOLD doctrine applies). Wired surfaces (2026-06-12, no version bump): provisional BUY pushes gated to 15:25–16:00 (`portfolio_engine.BUY_ALERT_FROM_ET`; SELL pushes stay live during market hours — price-barrier touches, not score reads), `GET /api/portfolio/pending` + Allocator execution-window banner (`/allocator` is the primary execution surface), `trader alloc` GUIDELINE timing line. Ledger was already canonical.

**Alert cadence (2026-06-12).** The ~20-min "Stock Daily Update" finishes at unpredictable times (15:00 run→~15:20 before buy window; 15:45 run→~16:05 after close), so time-sensitive pushes come from two scheduled lightweight `trader portfolio notify` passes (~1 min; Task Scheduler `TraderPortfolioNotifyMorning` 08:45 ET + `TraderPortfolioNotifyClose` 15:30 ET; installer `scripts/install_portfolio_notify.ps1`): **08:45** = carry-over buy digest + pre-open sells, 45 min before open; **15:30** = buy-window alerts on 15:00-run scores + live sells, ~28 min before close. Update-hook pushes gated in `_pending_actions`: nothing overnight/post-close (`MORNING_ALERT_FROM_ET=08:30`); intraday barrier-touch sells push any time during market hours; buys only ≥15:25.

> **SL evolution**: Original SL=−60% (2.184σ). 2026-04-16 3-mode sweep found SL=−35% (1.274σ) wins every window. H5 ship (2026-04-28) inverted TP/SL to **TP=+35%/SL=−30%** (tighter SL, wider TP, lowers BE). Do not go tighter than SL=−25% on calls (bar-1 noise dominates; `put_sl_shakeout_sweep.py`).
>
> **Break-even TP rates (zero slippage, current params):**
> - 30 DTE call base (TP=+33%, SL=−27%): BE=27/(33+27)=**45.0%**
> - 30 DTE call stressed (TP=+42%, SL=−40%): BE=40/(42+40)=**48.8%**
> - 15 DTE call base (TP=+35%, SL=−30%, H5): BE=30/(35+30)=**46.2%**
> - 15 DTE call stressed (TP=+40%, SL=−35%): BE=35/(40+35)=**46.7%**
> - Put (TP=+35%, SL=−20%, both DTEs): BE=20/(35+20)=**36.4%**
>
> v31 call TP rates (5y): 2022=55.3%, 2023=57.9%, 2024=63.6%, 2025=56.8% — clear BE by 9-17pp. Put TP rates: 2022=47.8%, 2024=51.3% — clear BE by 11-15pp.

### Why These Parameters (MC justification)

| Parameter | Value | Why |
|---|---|---|
| 30 DTE | over 15 DTE | ~2× return at same drawdown; extra 8 hold days = alpha |
| Call TP +33%/+42% (30 DTE) | over H5 +35%/+40% (now 15 DTE), over +30%/+35% (pre-H5) | H5 inverted TP/SL asymmetry; v32_optim (2026-05-04) retuned 30 DTE to TP_BASE=0.33/TP_STRESS=0.42 (v34+ scoring has stronger per-trade WR than 0.35 anchor was tuned for). Switch fires at `breadth_score ≤ 40` (30 DTE) / 50 (15 DTE), same breadth signal drives TP+SL. |
| Call SL −27% base (30 DTE) | over H5 −30% (now 15 DTE), −35% (original), −60% (early) | H5 tightened toward −30%; v32_optim set 30 DTE SL_BASE=−0.27 for faster capital recycling. Tighter SL+wider TP lowers BE; all annual v31 TP rates (55-64%) clear BE. Dead-hold handles deep fires (≤−50%). |
| Call SL −40% at breadth≤40 (30 DTE stress) | over H5 −35%@≤50 (now 15 DTE), fixed −30%, or composite-based | Breadth decomposition (2026-04-16): breadth alone beats composite by +20% on 2022→now Realistic. v32_optim widened to −40% but moved trigger to deeper `breadth_score≤40` (fires only at genuine deep stress). |
| Hard sell day 15 / −40% | over day 7 or −50% (pre-H5) | Day 15 > day 7 return; hard sell fires on <2% of trades. H5 improved HARD_SELL_LOSS −50%→−40% (theta scaling √(15/30)×premium). Dead-hold handles trades reaching −50% before day 15. |
| 95+ (ultra) at 20% | over 25% (Phase 7 reduced) or merged 85+ | Phase 7 established 20% cap; v32_optim later cut 80-84 while preserving 95+/85-94. 95+ has best WR15 but tiny-N. |
| 85-94=15%, 80-84=10%, 75-79=10% | over prior 20/15/12/10 | v32_optim cut 80-84 12%→10%, flattening mid/low without weakening 95+/85-94. |
| Max 14 positions, call cap 12 / put cap 8 | over uncapped one-sided saturation | Shared 14-slot pool for headroom; v60 caps concurrent calls at 12 so puts occupy residual slots; Sentinel caps puts at 8. r054 sweep selected `callcap12_dd035055f040`. Sentinel post-wire: +10.33pp avg worst-DD, no max-DD regression. |
| Practical exposure saturation: $25M base, 80% gross, 65% call, 25% put, 16/4 refs | over unconstrained theoretical compounding | Active v60 can compound into absurd simulated equity and over-concentrate on dense signal days. Sentinel preserves practical-stage compounding while reducing crowded-market drawdown. Post-wire N=160: 2020_crash DD 72.6%→59.3%, covid_peak 74.5%→56.9%, 2020_full 77.4%→60.3%, 5y 66.3%→58.5%; no window worsened worst DD. |
| 70-74 overflow DISABLED | not 5% | Phase 6c+8 (2026-04-17): removing overflow lifts 22-now Realistic +13% and fixes two 80% DD floor breaches (2025 83.0%→74.1%, 22-now 83.3%→75.2%). Any overflow ≥2% re-breaches. |
| Asymmetric weekly put_scale=1.5× | over symmetric 1.0× | `experiments/asymmetric_weekly_sweep.py` (2026-04-17, 5y): puts <25 WR30 63.9%→65.5%, <15 WR30 64.1%→69.3%, <15 Ret30 −0.28%→+0.25%; calls unchanged. Shipped v18 (`17caf99`). |
| Asymmetric MACD gate `PUT_MACD_GATE=45` | over zero-MACD or symmetric | `experiments/asymmetric_macd_ablation.py` (2026-04-17, 5y): puts <25 WR15 +4.3pp, <15 +6.5pp; calls unchanged/within noise. Shipped v18. |
| Puts alongside calls (v18) | over calls-only | `monte_carlo_puts_asym_validation.py` (2026-04-17, 3-mode): Variant C (TP=30/SL=20, cascade 15/12/12) beats calls-only on 22-now Realistic +273M pp (2.1× uplift), wins 4/5 annual windows. Mirror-SL (TP=30/SL=35) LOSES 22-now (−81M pp) — put-specific tighter SL required. |
| Put cascade 12/10/8 | over inverted 10/12/12 | v32_optim made cascade monotonic by conviction. |
| Put SL hard-hold (historical — current config `hold=0`, superseded by dead-hold 2026-05-01) | Mon=4/else=3 is the 2026-04-23 sweep result, not the live setting | `experiments/put_sl_dynamic_hold.py`: raw 5y put TP% 42.5%(hold=0)→60.3%(hold=B); portfolio PutTP 5y 65.8% vs ~49% baseline; wins 2022 +7%/2023 +66% vs hold=3; regressions 2024 −6%/2025 −13% (within gate). Variant C (regime-gated, 30-50 threshold) is null — `put_sl_hold_c40.py` shows tighter thresholds cut utilization but still lose on 5y compound; implemented `PUT_SL_HOLD_BARS_DEFAULT=3`/`MONDAY=4`. |
| Put TP=+35%/SL=−20% | NOT SL=−10% or −35% | Bayesian sweep (2026-04-17, `bayes_phase1_put_tpsl.py`): SL=−10% top-utility on paper but raw TP rate collapsed to 28-31% (vs 46-50% at −20%) via same-stock re-entry cycling artifact; Phase 5b confirmed reverting to −20% restores wins. `put_sl_shakeout_sweep.py` (2026-04-23): every widened variant (−30/−35/−40/−45%) FAILS 80% DD floor on 2023/2025/5y; 5y Realistic collapses +21.6T%(−20%)→+4.3T%(−35%)→+0.96T%(−40%); raw TP% rises but so does BE threshold; capital velocity dominates shakeout-recovery EV. Exception: 2022 bear SL=−35% gives +79% uplift (sparse call competition). Breadth-gated put SL untested. Do not widen as standing policy. |
| Asymmetric regime slope `slope_up=0.0, slope_down=1.0, slope_put_up=-0.5, slope_put_down=0.0` | over symmetric SC100 | Phase 12/13 (N=1000) shipped asymmetric call slopes; Phase 14/15 added mild put BULL cut. Combined +58%→+18% additional on 22-now compound (final +9.11B% vs +5.25B% SC100, +74% total); DD-C 74.5% vs 77.4%. Boosting puts in STRESS hurts (crowds shared 14-slot pool). |
| Counter-trend cascade promotion `CT_PROMOTE=True`, `PUT_TREND_MIN=80`, `CALL_TREND_MAX=20`, ct_call→ultra(25%), ct_put→put_top(15%) | over CT_PROMOTE=False | Path B/V2 ship (2026-04-21), 8-variant 5y sweep. CT-PUT (overall≤25∧TREND≥80) / CT-CALL (overall≥70∧TREND≤20) promoted ahead of score-sorted queue. Replaces reverted v22 weight-stage X-confidence gate. No recalculate, no version bump. |
| ~~Earnings-window put suppression `EARN_SUPP_PUT=True`~~ **RETIRED 2026-05-06**, replaced by score-stage PESS (v39) | `DAYS=5, MIN_OV=16, MAX_OV=20` over no suppression | N=1000 canonical MC: 5y compound +44.7% Realistic; all windows ±7%; 5y DD-C improved 77.1%→75.0%; 0% collapse. Per-trade A/B: dropped 16-20 cohort regresses −3.1pp WR15 at d=3, −0.7pp at d=5. Mechanism: ~982 puts/year filtered, freeing 14-pool capacity. `<15` and `21-25` puts left untouched (they outperform in earnings windows). |
| Earnings meta-score boost `EARN_BOOST_ENABLED=True, WINDOW=5, MAX_BOOST=0.50, LIFT_NORM_CALL=22.3, LIFT_NORM_PUT=16.3` | over no boost | **Shipped 2026-04-28 as v28 (`e3c8678`).** Score-stage WR-calibrated log-smoothed multiplier on `(overall−50)` within 5 trading days of earnings. Lift table `experiments/v27_optimization/phase_tp3b_lift_table.json`. Calls always boost ≥70; puts only ≤25. Canonical N=200 MC: 22-now Realistic compound +10,876% vs baseline; 2022 +124%, 2023 +167%, 2024 +685%, 2025 +92%, 5y +10,798%. Call TP +0.5-2.6pp every window. DD-C improved 5/8 windows, all under 80%, 0% collapse. Only regression: 2021 −7.9% (within gate). Score-stage → recalculate required, version bumped. Formula: `scoring-algorithm.md` "Earnings meta-score boost". |

### Year-by-Year Yield — Canonical MC (`monte_carlo.py`, 3 collision modes)

Realistic per-exit slippage (entry −1.0%, TP 0% limit sell, SL −1.3%, hard −0.5%), three collision modes for same-bar TP+SL ambiguity: **Conservative** (SL, lower bound), **Realistic** (50/50 coin flip, honest mid estimate), **Optimistic** (TP, upper bound). Each year independently from $50k, 500 iterations. **Algorithm version 9463f02 (v25, mis-stress call softener)** extends v21 (`aba4f5d`) with a score-stage call-side regime softener pulling `regime_multiplier` toward 1.0 by `(mis_stress × 0.25)` on bull-mislabeled-stress days; v22/v23/v24 all reverted (see known-issues "What NOT to do"). Re-entry blocked while symbol has an open position; same-symbol block across sides.

> **2026-04-29 — option-pricing-aware MC + bounded fill shipped (canonical).** Replaced the static-pricing 3-mode model: (1) `option_pricing.py` closed-form delta+theta+vega + random-fill resolve (`f2329a1`); (2) `[low,high]` sampling range eliminating the 50/50 coin flip (`9a9da33`); (3) AMC-aware earnings `effective_date` for correct vega sampling on AMC-spanning trades (`9a9da33`); (4) bounded fill by trigger barrier — TP can't resolve below TP, SL can't resolve above SL (`3432fb8`). Phase OP1 attempted DD=0.60 ship but **REVERTED** under OP1b N=300×8-window validation: C2 (DD=0.60) wins the N=150 4-window screen (22-now +15.6% return/−1.0pp DD) but **regresses on the full 8-window 5y check (−5.4% return, +2.5pp DD)** — per-year losses in 2022/2023/2025 compound past 2021/2024 wins. Reverted to `DD_CIRCUIT_BREAKER=0.68` in `monte_carlo.py`, `backtest_cascade.py`, `api.py`, `src/pages/Backtest.js`. **Lesson: N=150 4-window screening is insufficient for ship decisions — require N=300+×8-window validation before ship.** The option-pricing-aware MC architecture itself (random fill within bar, theta+vega in resolve()) remains shipped; only the DD breaker tightening was reverted. Phase OP2 retune happened under this now-canonical bounded-fill model; the binary DD breaker from that era was retired 2026-05-11, replaced by score-visible breadth/risk controls (led by v56 sector ETF Market Wave dual-wave dampener).
>
- **CTSL — Counter-Trend Score Lift (Stage 1 winner, additive on CT_PROMOTE) — shipped 2026-05-08.** First production ship under the three-stage calibration framework (`process.md` + `assessment-backtest.md`). Score-stage continuous lift at signal-load time in `monte_carlo.py`/`backtest_cascade.py`/`api.py /api/trader/simulate`/`trader.py _cmd_backtest`. Stacks ADDITIVELY on `CT_PROMOTE` (Config B), does not replace it — the "phase out CT_PROMOTE" goal was REJECTED at Stage 3 (Config C substitute failed T4, 5y DD +2.20pp; CT_PROMOTE earns its keep via accidental ULTRA-slot capping in bear-tape realizations CTSL alone can't replicate). Call-side: tighter gate (tm=15 vs CT_PROMOTE's 20) lifts deepest 5% of CT-call cohort toward target=98.4 with `score_norm^2.27` concave power. Put-side: wider gate (tm=76 vs 80) dampens deep CT-puts toward target=−0.13 with near-linear trend gradient. Stage 3 N=500×8 vs A baseline: 5y WorstDD 71.0%→70.6% (−0.40pp, T4 PASS); 22-now DD 73.1%→69.7% (−3.40pp); 2023 DD −3.20pp; 2025 DD −5.00pp; per-trade CTP%/PTP% within ±0.5pp; 0% collapse; T1-T7 all PASS. Per-trade affected-cohort WR7 at K=2σ/M=5σ: call=78.26% (N=69 5y), put=88.72% (N=266 5y). Constants `CTSL_*` (15 fields) in `strategy_config.py`; `CTSL_ENABLED=False` (or env=0) to revert. Portfolio-stage only. 15 DTE registry `not_wired`. Trail: `experiments/ctsl/FINDINGS.md`.

- **SAW Put U-curve (Sector-breadth-driven put alloc gradient) — 30 DTE shipped 2026-05-08; 15 DTE shipped 2026-05-09.** Looks up cross-sector ETF breadth (% of 11 SPDRs above EMA50) per put signal date, applies scale to put alloc. **30 DTE and 15 DTE optima are architecturally distinct.**
  - **30 DTE (quadratic, mid=72/hw=18/floor=0.55/ceil=1.35/power=3.0):** `scale = floor + (|brd-midpoint|/halfwidth)^power × (ceil-floor)`, clipped `[floor,ceil]`. Full ceil (1.35×) at brd≤54 or ≥90 (sector tails); deepest contraction (0.55×) at brd≈72 (empirical bad-zone trough) — mean-reversion alpha is highest at breadth extremes, lowest 60-90 mid-band. Stage C N=300×8: 7/8 windows improve compound (2021+103%, 2022+57%, 2023+54%, 2024+98%, 2025+11%, 22-now+182%; only dip −33%); 5y DD 71.7%→70.6%; 22-now DD −2.4pp; 2022 DD −4.6pp; 0% collapse. `experiments/saw_put_ucurve/`.
  - **15 DTE (sigmoid, mid=70/hw=25/floor=0.65/ceil=1.00/K=12.0):** NO breadth-extreme amplification (ceil=1.00) — pure mid-zone contraction; 30 DTE's tail-amplification Region B optimum does NOT transfer (smoke test 5y_dd +2.9pp worse). 15 DTE's faster theta + smaller premium-mult (1.29 vs 1.82) means larger position fraction at breadth extremes can't recover before bar-7 hard-sell. Phase D N=500×8: 5y DD 80.8%→78.9%; 22-now DD −1.76pp; all 8 windows DD reduces; 7/8 compound improves (only dip −24%, DD also reduces); 0% collapse. Calibration: staged Bayesian Phase B(40 evals N=200×4)→C(top-5 N=300×8)→D(winner N=500×8); all top-10 Phase B candidates by 5y_dd had `ceil=1.00`. `experiments/saw_put_ucurve_15dte/`.
  - Constants `SAW_PUT_UCURVE_*` per-DTE in `strategy_config.py`; `SAW_PUT_UCURVE_ENABLED=False` to revert per-DTE. Portfolio-stage only.

- **PUT_TP Stage 2 SL-tax refinement — reverted for both DTEs.** 30 DTE tightened put TP 35%→14% (Phase D N=500×8: 5y DD 73.0%→65.23%, 5y PutTP 45.6%→60.4%); 15 DTE separately selected +6%. Both reverted — execution-realism pass rejected sub-20% premium exits as too close to option mark/intraday noise. Put TP restored to +35% both DTEs. Trails: `experiments/sl_tax_stage2/FINDINGS.md`, `experiments/sl_tax_stage2_15dte/FINDINGS.md`, `experiments/dynamic_tpsl/FINDINGS.md`.

> **HOLD/THETA STANDARDIZED TO CALENDAR + HONEST THETA (2026-06-09).** Active 30 DTE engines (`monte_carlo` + `backtest_cascade` = live Portfolio + Backtest page) now hold **27 CALENDAR days** and price theta honestly over **30 CALENDAR days** (`CALENDAR_HOLD=True, HOLD_CAL_DAYS=27, NOMINAL_CAL_DTE=30`). Re-bases reported returns ~÷200 off the old theta-optimistic trading-bar engine (honest Apex 5y ≈ +1,800–2,574% / ~66% DD / collapse=0). **Every "day 15/15 trading bar" reference above is SUPERSEDED** — see known-issues CURRENT SHIP STATE entry "2026-06-09 Calendar-day hold + HONEST THETA". (15 DTE left on legacy basis pending its own honest port.)
>
> **AUTHORITATIVE STATE IS THE v70 APEX/CORE/SENTINEL PROFILES (2026-06-02, refined 2026-06-03).** Canonical: `strategy_config.STRATEGY_30DTE` (Apex/default) + `algorithm_versions/portfolio_profiles.json` + [`PROFILES_SHIP_HANDOFF.md`](../../experiments/v69_portfolio_retune/PROFILES_SHIP_HANDOFF.md) + [`REGIME_KNOB_PLAN.md`](../../experiments/component_reweight/REGIME_KNOB_PLAN.md). Current Apex (default): `ALGORITHM_VERSION=c70d16d22` (v70 honest); calls-only; `TP_BASE=TP_STRESS=0.30`, `SL_BASE=SL_STRESS=−0.70`, `SLIP_*=−0.015` (~3% round-trip); cascade `ULTRA=0.20/TOP=0.15/MID=0.08/LOW=0.03/OVERFLOW=0.0` (retuned for supply density: v71 low 0.10→0.05+overflow 0.035→0; v73 mid 0.10→0.08+low 0.05→0.03 — dampener retirements doubled-then-redoubled 75-84 supply); dead-hold POPOUT=−0.15/TRIGGER=−0.40 (30 DTE; 15 DTE −0.25/−0.50; 2026-06-03 near-Pareto win, collapse-preventing); puts OFF; `MAX_POSITIONS=14/CALL=14/PUT=0` (`PUT_TIER_ALLOC` all 0); `GROSS_PREMIUM_CAP=CALL_PREMIUM_CAP=0.50`; `PRACTICAL_CAPITAL_CEILING=0` (uncapped); `DD_SOFT_BAND 0.35/0.55/0.40`; hard-sell day 15;
> - **SVR semivol_r skew-bridge entry filter (2026-06-05, `7e6f8fe19`)** — calls-only band-pass downweighting euphoric (low-svr) and crash-mode (high-svr) call cohorts toward 0.5x outside ~0.7-1.25 semivol_r sweet spot (`SVR_ENABLED=True`, gentleband c00 LO_CUT0.5/LO_FULL0.7/HI_FULL1.25/HI_CUT1.65/FLOOR0.5; feature `database/utils/semivol.py`); N=500×8 incl COVID: **−5.8pp 5y WorstDD AND +28.6% compound, collapse=0**.
> - **MWDD McClellan breadth-momentum flat-band call dampener (2026-06-05, `d79f8a144`)** — 2nd orthogonal DD lever: Gaussian contraction of CALL alloc in low-EV flat/topping McClellan band (~0), DD-gated + VIX-panic-excluded (capitulation/COVID left alone as mean-reversion winners) (`MWDD_ENABLED=True`, c00 MCC_C−0.336/W22.185/DEPTH0.337/DD_MIN0.128/VIX_PANIC28; map `MarketBreadth.mcclellan_oscillator`); N=500×10 incl COVID: −2.6pp 5y WorstDD / −5.5pp 22-now, every window DD down, compound flat, collapse=0. Market-Wave *crash* state is NOT a DD sizing signal (mean-reversion winner); flat/topping breadth-momentum band is.
> - **LIQUIDITY_FLOOR thin-name admission filter (2026-08-07) — WIRED, DEFAULT-OFF (0.0), enable gated on P2.B live fills**: drops 75+ CALL signals whose trailing-30d ATM-band option volume (`option_volume_30d`, FF-3' map + live `option_prices` tail via `tools/build_liquidity_map_live.py`) is below the floor (profile-overridable `liquidity_floor`). Evidence Core-only: floor 150 c/d beat matched random-drop control 7/8 windows N=500×12 in spread+fill-penalized engine, collapse 0 every cell (`experiments/flatfile_exploitation/FF3_STAGEB_RESULTS.md`); **Apex failed T4 — never enable there**. At 0.0 engines byte-identical to pre-ship. Enable = build live map, set Core `liquidity_floor: 150`.
>
> **SPREAD_TILT 75-79 high-spread call-alloc haircut (2026-06-15, `9acf7465d`)** — down-weights low-conviction, high-component-disagreement 75-79 calls only (`spread = sqrt(pop-var of trend/bb/rsi/macd/stoch / 5)`; `SPREAD_TILT_ENABLED=True`, LO26.9/HI31.3/DEPTH0.40 → floor 0.60×); N=500×9 incl COVID: −4.1pp 5y WorstDD, DD down 8/9 windows, return held-or-up, collapse=0. (TVDD TRIN volume-flow 2026-06-07 + BDIV pre-top-breadth-divergence 2026-06-11 are the 4th/5th orthogonal Stage-3 call-alloc levers, also shipped — full list in known-issues CURRENT SHIP STATE.) **Core** = same cascade at 40% caps/$2M ceiling; **Sentinel** = MID=LOW=0 (85+-only) at 30% caps/$1M ceiling. SCW/MCD/ICH/continuation echo/sector Market Wave score-stage mechanisms are still in the v70 scoring stack; the v60 portfolio bullets below are HISTORICAL (OP1/OP2 MC lineage) — where they disagree with Apex canon, Apex canon wins.
>
> **Historical v60 portfolio ship state (as of 2026-05-28 broad DTE router — superseded by Apex above):**
> - `ALGORITHM_VERSION=d4a3e9fec` (v60 r054 SCW; v58 continuation retune remains reverted)
> - Call cascade: `ULTRA=0.20, TOP=0.15, MID=0.10, LOW=0.10, OVERFLOW=0.00`
> - Put cascade: `PUT_TOP=0.12, PUT_MID=0.10, PUT_LOW=0.08`
> - `DD_CIRCUIT_BREAKER` removed 2026-05-11 (both DTEs)
> - `MAX_POSITIONS=14`, `MAX_POSITIONS_CALL=12`, `MAX_POSITIONS_PUT=8` (30 DTE only)
> - Practical exposure saturation Sentinel: `PRACTICAL_CAPITAL_CEILING=25_000_000`, `GROSS_PREMIUM_CAP=0.80`, `CALL_PREMIUM_CAP=0.65`, `PUT_PREMIUM_CAP=0.25`, `OPP_SAT_CALL_REF=16`, `OPP_SAT_PUT_REF=4`, `OPP_SAT_POWER=0.50`, `OPP_SAT_FLOOR=0.55`
> - `DD_SOFT_BAND_LO=0.35, HI=0.55, CALL_FLOOR=0.40` (30 DTE only, recalibrated 2026-05-19)
> - Broad 15DTE router (30 DTE overlay, 2026-05-28): ≤1 call/day when `score>=80`/`trend<50`; filters off; no version bump
> - `CTSL_ENABLED=True` (30 DTE only): call tm=15/target=98.4/α=0.56/p=2.82/floor=74.7/snw=+0.75/snp=2.27; put tm=76/target=-0.13/α=0.83/p=0.99/ceiling=27.9/snw=-0.22/snp=1.68
> - `SCW_ENABLED=True` (score-stage v60 r054 — call-side low-stoch/weak-weekly timing dampener with boundary relief, raw-stoch relief, overextension taper): gate=70, max_penalty=8.0, stoch_power=1.5, decay_power=6.0, weekly_hi=14.0, scale=1.3
> - Sector ETF Market Wave direct transform enabled (`SECTOR_BREADTH_WAVE_ENABLED=True`): source=`market_wave`, call_min=70, put_max=25, v57 bayes_185 params
> - `DEAD_HOLD_ENABLED=True`, `DEAD_HOLD_TRIGGER_PNL=−0.50`, `DEAD_HOLD_POPOUT_PNL=−0.25`
> - 30 DTE: `TP_BASE=0.33, TP_STRESS=0.42, SL_BASE=−0.27, SL_STRESS=−0.40, PUT_TP=0.35, PUT_SL=−0.20`
> - 15 DTE: `TP_BASE=0.35, TP_STRESS=0.40, SL_BASE=−0.30, SL_STRESS=−0.35, PUT_TP=0.35, PUT_SL=−0.20`
> - `HARD_SELL_LOSS=−0.40` (30 DTE) / `−0.45` (15 DTE); all slippage=0.0 (Wealthsimple $0 commissions)
> - MC architecture: bounded bimodal SL fill + theta + sampled vega; dead-hold resolve
> - Phase OP2 CLOSED: DD=0.60 shipped 2026-05-01 after N=1000 confirmation
> - v60 Stage 3 overlay shipped 2026-05-19: callcap12 + DD band 0.35/0.55/0.40, avg focus DD +0.90pp, no material max-DD worsening, call TP drift −0.02pp

**Historical MC baselines (superseded by Apex canon; retained for lineage).** OP1 (N=200 partial, 2026-04-29) vs legacy v25: 2021 +73%, 2022 (bear) −94% ✗, 2023 −59% — new pricing punishes choppy/bear regimes harder (SL fires accumulate theta drag). OP1 screening (22-now×N=80, 12 configs) top-3: `C10_wider_HARD`(-0.45), `C1_tighter_DD55`, `C2_tighter_DD60`; `C4_wider_CALLSL`(-0.40/-0.45) breached the 80% DD floor, killed; `C3_wider_PUTSL`(-0.30) lifted put TP to 57% but lost 88% on 22-now — capital-velocity loss. OP1 4-window N=150 validation initially favored C2 (DD=0.60): +15% 5y compound, −1.5pp 5y DD-C — shipped 2026-04-29 (`f2329a1`). **OP1b full N=300×8-window reversed it**: C2 wins 22-now (+15.6% return, −1.0pp DD) but regresses 5y (−5.4% return, +2.5pp DD) — per-year losses compound past the 2021/2024 wins. Reverted to `DD_CIRCUIT_BREAKER=0.68`.

**v25 MC summary** (`monte_carlo.py`, legacy static pricing, 500 iters×3 modes, 2026-04-26):

| Window | Cons Real | Real Mean | Opt Real | Real DD | Cons DD | Call TP% | Put TP% |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2021 | +50,796% | +62,725% | +81,203% | 59.4% | 59.4% | 67.0% | 59.1% |
| 2022 | +27,063% | +42,430% | +61,737% | 57.2% | 58.6% | 61.3% | 68.4% |
| 2023 | +54,332% | +74,379% | +94,681% | 51.1% | 45.3% | 65.5% | 62.1% |
| 2024 | +378,609% | +451,006% | +593,225% | 46.8% | 47.0% | 69.0% | 68.7% |
| 2025 | +60,628% | +80,853% | +106,267% | 57.0% | 57.2% | 66.1% | 61.6% |
| 5y | +1.69×10¹⁶% | +6.80×10¹⁶% | +3.04×10¹⁷% | 78.6% | 74.6% | 65.2% | 64.2% |

Zero collapses across all 18 cells. v25 vs v21 baseline: 2021 −64% regress (2021 accepted loss — 2021 was luxuriously-bull at +175k% baseline; 5y compound +101% better; DD-C stays under 60%), 2022 +19%, **2023 +124% headline win**, 2024 +147%, 2025 +101%, 5y compound +101% strong win. Mis-stress softener tuned for the 2023 narrow-bull regression (composite mislabels low-VIX bullish days as STRESS). Treat 5y figures as directional only — projection assumes unlimited broker capacity and free liquidity on rare 95+ signals; real-world compounding caps far below. Signal counts (v25): 2021 N=8505 calls/3341 puts, 2022 N=3781/15984, 2023 N=2909/17509, 2024 N=5528/12503, 2025 N=5129/12628, 5y N=27957/64980. Same-bar collision rates 0.5-1.1% calls, 0.4-0.6% puts. Realistic per-exit slippage: call BE=56.3%, put BE=43.5%; v25 call TP by year 67.0/61.3/65.5/69.0/66.1%, put TP 59.1/68.4/62.1/68.7/61.6% — all clear BE.

### What Is Not Yet Validated
- **Same-bar TP/SL resolution uncertainty** — bounded by Conservative/Optimistic modes; no window crosses zero at v25. Resolving precisely needs intraday (1-min) data; quote Realistic as point estimate, Conservative as floor.
- ~~Regime-aware sizing~~ TESTED 2026-04-16 (`monte_carlo_regime_sl_hybrid.py`+`monte_carlo_vix_breadth_decomp.py`): breadth-adaptive SL beats fixed and composite-based; breadth alone beats composite +20% on 2022→now; VIX adds noise not signal.
- ~~Trailing stops~~ TESTED & KILLED 2026-04-15 (`monte_carlo_trail.py`): destroys capital velocity, positions locked 8-13 extra bars; +8,188%(2024 baseline)→+8.4%(best trail); the 88% MFE gap can't be captured — extra hold time destroys more value than marginal P&L gains. Strategy's alpha IS capital velocity; close fast and redeploy.
- **Correlated drawdown** — MC assumes independent positions; a market selloff hits all 14 open positions simultaneously.
- ~~SL re-validation under 3-mode MC~~ COMPLETED 2026-04-16 (`monte_carlo_sl_sweep.py`): SL −25%→−35%, wins every window both modes.
- ~~Remaining parameter re-validation~~ COMPLETED 2026-04-16 (`monte_carlo_maxpos_sweep.py`): MaxPos 10→14; TP=+30%, cascade 15/12/12/5 locked.
- ~~85-89 allocation re-sweep~~ COMPLETED 2026-04-15 (`monte_carlo_alloc_sweep_85plus.py`), superseded by later cascade work (merged 85-89/90+ into 85+ at 15%; v32_optim later cut current mid/low to 10%/10%, kept 95+ at 20%/85-94 at 15%).

---

## 15 DTE Variant (C1, Phase 15B — SHIPPED 2026-04-28)

Parallel strategy alongside 30 DTE production. Same scoring engine, cascade philosophy, F3F/regime mechanisms — only DTE-specific params change. Legacy DD circuit breaker below retired 2026-05-11.

### Why a 15 DTE variant exists
Per-trade A/B (`experiments/fifteen_dte_per_trade_ab.py`, 2026-04-28): at 15 DTE the option needs only 0.774σ for TP=30% (vs 1.092σ at 30 DTE) and 0.516σ for put SL (vs 0.728σ). AvgTPBar 1.3-1.5 bars (vs 1.7-2.2 at 30 DTE) — capital recycles 30-50% faster.

First MC (Phase 15A, 30 DTE H5 applied to 15 DTE) showed massive compound advantage but 87% Conservative DD-C breached the 80% floor on 3 windows (2025, 22-now, 5y); best fixable was 86.6%. Phase 15B introduced a (since-retired) binary DD circuit breaker + MaxPos contraction (14→8) + stronger F3f floors (0.50→0.40). MaxPos/F3F floors remain the shipped protection; v56 sector ETF Market Wave dual-wave dampener is now the score-visible breadth protection shared by both readers.

### C1 shipped config
Anchored at H5 per-trade EV winners — 15 DTE retains original H5 call band (TP=0.35/SL=-0.30, stress 0.40/-0.35 at `BREADTH_THRESHOLD=50`) and PUT_TP=0.35/PUT_SL=-0.20. Note: 30 DTE call band later retuned by v32_optim to TP=0.33/SL=-0.27 (stress 0.42/-0.40 at threshold 40) — only put TP/SL (0.35/-0.20) is shared across DTEs now.

| Param | 30 DTE | 15 DTE C1 | Why |
|---|---|---|---|
| `HOLD_DAYS` | 15 | **7** | Half-DTE hard sell |
| `PREMIUM_MULT` | 1.82 | **1.29** | 15 DTE ATM ≈ 1.29×σ_daily |
| `HARD_SELL_LOSS` | -0.40 | **-0.45** | Theta scaling: day-7 ~-46% empirical |
| `MAX_POSITIONS` | 14 | **8** | Reduce concurrent SL clustering |
| `F3F_CALL_FLOOR` / `F3F_PUT_FLOOR` | 0.50 | **0.40** | Stronger weak-tape contraction |
| `DD_CIRCUIT_BREAKER` | retired | retired | Removed 2026-05-11; v56 Market Wave dampener replaces it |
| `PUT_TP`/`PUT_SL` | 0.35/-0.20 | 0.35/-0.20 | Stage 2 micro-TP refinements reverted (sub-20% sits inside execution noise) |
| Cascade alloc | 20/15/10/10 | **18/17/12/08** (v15_optim) | 15 DTE wants top-heavy shape, not 30DTE flat mid-low |
| Put cascade | 12/10/8 | **8/8/8** | 30DTE uses monotonic conviction sizing; 15DTE keeps all puts at floor |
| `EARN_SUPP_PUT` | False (retired) | False (retired) | Retired 2026-05-06 both DTEs, replaced by PESS |

### Validation (N=500, all 8 windows, vs 30 DTE H5 baseline N=200, v28 e3c8678)

| Window | 30DTE Real | 15DTE C1 Real | Δ% | C1 DD-C | Floor ≤80% |
|---|---:|---:|---:|---:|---:|
| 2021 | +13.3k% | +78.4k% | +488% | 73.7% | ✓ |
| 2022 | +9.3k% | +80.0k% | +763% | 69.3% | ✓ |
| 2023 | +18.6k% | +44.5k% | +139% | 59.0% | ✓ |
| 2024 | +585k% | +1.08M% | +85% | 68.1% | ✓ |
| 2025 | +13.6k% | +6.24k% | −54% ⚠ | 71.0% | ✓ |
| dip | +380% | +422% | +11% | 72.4% | ✓ |
| 22-now | +4.07T% | +219T% | +54× | 69.3% | ✓ |
| 5y | +409T% | +142 quadrillion% | +346× | 74.3% | ✓ |

(N=150 screen gave directionally identical results — 22-now +50×, 5y +342×, max DD-C 73.7%.) Gates passed: all Conservative DD-C ≤73.7% (clears floor by 6.3pp); 22-now/5y massively beat 30 DTE; 0% collapse every cell. One accepted regression: 2025 underperformed 30 DTE by −65% absolute — the now-retired DD breaker fired more in choppy 2025, reducing trade volume.

### DD Circuit Breaker Retirement
Removed from config, MC, deterministic backtests, API, UI, mechanism registry 2026-05-11 — it was path-dependent, invisible on the score/dashboard surface, and fired after concurrent crash exposure was already open. v56 sector ETF Market Wave dual-wave dampener now moves breadth response into persisted scores so dashboard and portfolio see the same eligibility signal. (The older folded-wave breadth-confirmation rescue in `experiments/mcd_cwwd_wvd_recal/FINDINGS.md` is a separate null result — do not confuse with the shipped Market Wave dampener.)

### Why H5 wider-TP/tighter-SL is the right anchor at 15 DTE
Per-trade A/B initially suggested narrower TP(0.30)+wider SL(-0.35); Phase 15A MC proved otherwise:

| Config | TP_σ | SL_σ | BE% | Net TP | Net SL |
|---|---:|---:|---:|---:|---:|
| H5 @ 15 DTE (TP=0.35/SL=-0.30) | 0.903 | 0.774 | **48.7%** | +0.340 | -0.323 |
| Per-trade A/B (TP=0.30/SL=-0.35) | 0.774 | 0.903 | 56.3% | +0.290 | -0.373 |

H5's tighter SL (0.774σ<0.903σ TP) drops BE to 48.7% (vs 56.3%); per-trade WR ~58-60% clears with +9-12pp margin. Wider A/B SL admits bigger gap-through losses without compensating WR.

### Frontend toggle
Added 2026-04-28: `Assessment.js` top-level "Strategy" toggle, 30 DTE/15 DTE pills, persists in `localStorage`. Controls which periods highlight in the WR table (30 DTE: WR15+WR30; 15 DTE: WR7+WR15) and which drives "Best bucket" (30 DTE: WR30; 15 DTE: WR15).

### Phase 15A null result (what NOT to retry)
Swept TP/SL/cascade/MaxPos at 15 DTE before the old breaker and F3F changes. Best result still breached DD-C floor at 86.6% on 22-now alone. Per-trade A/B params (TP=0.30/SL=-0.35) underperformed H5 at portfolio scale — wider SL admits bigger gap-through losses. Do not resurrect the binary DD breaker; use the breadth-wave family for crash protection.
