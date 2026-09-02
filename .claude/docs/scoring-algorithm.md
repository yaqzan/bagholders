## Scoring Algorithm (database/utils/scoring.py)

### Component Scores (0-100, 50 = neutral)
1. **TREND** — EMA-based price direction
2. **BB** — Bollinger Band position (20-day)
3. **RSI** — 14-period; adjusted downward in strong uptrends (mean-reversion lens)
4. **MACD** — 12-26-9; histogram direction + magnitude
5. **STOCH** — 14-period stochastic momentum
6. **Technical Alignment (X)** — agreement metric across components

### Overall Score Weighting (context-aware)
- `tanh(trend_score)` determines `d` (0=sideways, 1=trending)
- **Sideways weights**: trend=18%, BB=18%, RSI=25%, MACD=25%, Stoch=5%, TA=9%
- **Trending weights** (V6, `strategy_config.py:344-355`): trend=28%, BB=18%, RSI=16%, MACD=19%, Stoch=5%, TA=15%. `w = BASE + SLOPE×d` (TREND 18+10, RSI 25−9, MACD 25−6, TA 9+6; BB/STOCH flat). Pre-V6 `35/12/15` table capped trend at 35; V6 caps at 28, redistributes 7pts to RSI/MACD.
- Dynamic guards reduce trend dominance if BB overextends or RSI/MACD diverge (reversal signal).
- **Momentum confirmation gradient** (`mc` in `weight_info`): when raw MACD histogram contradicts trend direction, `d` is dampened (floor `d×0.60`), restoring RSI/MACD weight toward sideways levels — avoids firing signals against a turned momentum. Inactive when `trend_strength ≤ 0.15`.
- **Asymmetric MACD gate** (`PUT_MACD_GATE=45`, shipped 2026-04-17): when pre-MACD weighted score < 45, MACD's weight zeroes and redistributes to other components (MACD lags and suppresses genuine put signals until breakdown confirms). Validated 5y: put `<25` WR15 +4.3pp, `<15` +6.5pp, calls unchanged. `experiments/asymmetric_macd_ablation.py`. 2026-05-24 v60 follow-up tested smooth call-preserving gradient replacements and rejected them (worsened DD or no-op) — hard gate remains load-bearing. `experiments/macd_gradient_search/FINDINGS.md`.
- **Weekly adjustment**: ± based on current vs prior week RSI/MACD momentum; asymmetric scaling (puts 1.5×, calls 1.0×) inside `calculate_weekly_adjustment`. **ACTIVE INVESTIGATION (2026-04-27):** static scales are magnitude-blind — weak weekly drag (|w_adj|<13) gets the same multiplier as strong drag. Cross-bucket evidence: puts with `w_adj>-13` carry 7-15pp lower WR15 than strong-weekly siblings across every bucket; calls with `w_adj<+13` carry 3-9pp lower WR15. Refactor in flight: smooth tanh-saturating `total *= peak*tanh(|total|/k)` (puts peak=1.5/k=10, calls peak=1.0/k=12, floor 0.5). See known-issues.md Priority #13.
- **Volume multiplier** (CORE, active): `volume_amplifier` classifies daily volume events and amplifies weighted_sum. The Daily Volume Authority Wave (post-`pre_boost` weekly-governed wave) is **RETIRED 2026-06-15 in v74** — CORE amplifier unaffected; see Volume Signal System below.
- **Regime multiplier**: 0.70-1.10× from `market_regime`; applied inside `compute_overall_score` (see Regime integration below).
- **Ext-focal gradient dampener** (shipped 2026-04-20 as v21, `aba4f5d`): puts (overall≤25) with price ABOVE EMA50 are profit-taking pullbacks, not breakdowns. Lift ∝ `(25−overall)/25 × min(1.0, pct_from_ema50/10) × 0.5 × (25−overall)`, after regime adjustment. 5y sweep found WR30 declines monotonically 70.3%(ext=−10..−4)→60.7%(ext>+10) for s≤25. Validated: `<25` WR15+2.7pp, `<15`+2.6pp, `<5`+5.4pp; Ret30 flips positive every put tier; calls untouched.
- **MCD — Mcap Dampener** — **RETIRED 2026-06-10 in v71** (`MCD_ENABLED=False`): founding 8.2pp mcap↔TP ladder was mostly a survivorship artifact of applying TODAY's `Stock.market_cap` to historical dates; under v71's point-in-time mcap proxy the ladder collapses to 2.6pp (z=+2.61, below W1's z≥3 bar) and goes non-monotonic. MCD removals were near-baseline quality (53.0% vs 55.0% optWR15) consuming ~42% of 75+ N. Retirement returned 75+ supply +83% at WR15+1.9pp (5y). `experiments/integrity_audit_2026_06/FINDINGS.md`. Historical (shipped v43 2026-05-07, `e083032`): score-stage calls-only confidence-shifter on `log10(mcap_b)`; cohort signal was large-cap 75+ TP 65.8% vs micro-cap 57.6% (8.2pp, year-stable 2022-2025). Formula: `if 70≤overall≤84 ∧ mcap_b: mcap_factor=clip((1.90−log10(mcap_b))/1.40,0,1)^0.70; score_factor=clip((overall−70)/14,0,1)^1.50; overall -= 0.80×(mcap_factor×score_factor)×(overall−61)`. Applied after PCD, before PESS/EARN_BOOST; 70-72 and ≥$100B untouched by construction. Calibration: `experiments/mcap_dampener/`, 15,195 variants across 3 phases. **2026-05-13 smooth-wave replacement explored** (LUMN 84/85 cliff case) — best wave cut the cliff jump 15→2 but cost -54.72% of 75+ call N (5.12→2.47 avg open calls in the 14-slot book); not shipped without a wave preserving more N or clearing a full-history max-DD gate.
- **SCW — Stoch Conviction Wave** — **RETIRED 2026-06-12 in v73** (`SCW_ENABLED=False`): honest v72 ReSim A/B — ≥75 removals 50.3% optWR15 vs 54.3% shared (z=−1.69, not real) suppressing 14% of potential 75+ N; the real z=−2.47 signal at ≥70 lands mostly in zero-alloc 70-74. Retired with the CWCF/CSWC/SCW trio (dG +19.6% option/+36.3% generic). Historical (v50→v60 r054): call-side timing dampener for `overall≥70` on low daily stochastic + non-confirming weekly. Base wave `8.0×stoch_wave×weekly_gap×conviction_decay` (`stoch_wave=clip((50-stoch)/50,0,1)^1.5`, `weekly_gap=clip((14-wadj)/14,0,1)`, `conviction_decay=clip((100-overall)/30,0,1)^6`); v60 wrapped it in smooth scalar terms (`SCW_SCALE=1.3`, boundary relief 1.35/width 0.65, raw-stoch relief 0.05@71.217, overextension taper 0.25@1.0/width 0.3). `weight_info` exposed `scw_dampen`/`scw_base_dampen`/`scw_scalar`/`scw_conf`/`scw_raw_stoch`/`scw_ext_idx`/`scw_ext_taper`. W5 N-floor was REVIEW REQUIRED on small/high-conviction tiers at ship, accepted as a soft trade-off.
- **Continuation Echo Wave** — **RETIRED 2026-06-15 in v74** (`CONT_BOOST_ENABLED=False`): Phase-3d apex-EV attribution found it NEUTRAL on the funded Apex book (+2.48 restored vs +2.90 common apex-EV, no real alpha) while adding ~+7,030 ≥75 signals the book over-deploys into (DD, not return) — net-dilutive supply. Retired with the whole post-`pre_boost` tail: whole-tail ablation MC (N=300×8) lean 5y +1,143%/51.3%DD vs full +1,097%/62.1%DD = **−10.8pp DD at comparable compound, collapse=0**. **Revisit gate:** re-add only via post-MOS `apply_bias_corrections` MECHANISM_REGISTRY, gated on the apex 0d predictand + forward-holdout (OOS window opened 2026-06-15). Historical (legacy v33→v52 temporal echo→v53 prior-fix; v58 retune reverted 2026-05-15, production restored to pre-v58 params): same-side CALL prior wins echo into current CALL scores via decayed bounded lift. `compute_cont_prior_signal()` looks back 90 days at prior `pre_boost`≥70 scores + resolved outcomes: conviction `(|score-50|/50)^0.8436`, decay `exp(-gap/38.1591)`, `tanh(total/0.9033)`; weights W7=0.1810/W15=0.0229/W30=0.6756/W60=0.7661, loss penalty 0.3845, fizzler penalty 0.4176. Applies to CALL scores [50,84] with `pre_boost≥50`; positive echo must exceed 0.0254; lifts toward 85 (alpha=1.1669, max lift=4.4739). v58 retune improved WR15 utility but regressed the primary 30DTE portfolio gate vs v57 — kept research-only.
- **Sector ETF Market Wave direct transform** — **RETIRED 2026-06-10 in v71** (`SECTOR_BREADTH_WAVE_ENABLED=False`): the source CSV had silently vanished, so the wave was INERT in every stored v60/v69/v70 row set (loader returned empty series with no warning — fixed with loud guards in v71). Rebuilt-source A/B showed the wave removes ABOVE-baseline call winners (−28% N at 75+, removals 56.4% vs shared 54.4% optWR15) — the breadth-crash-artifact trap. `experiments/integrity_audit_2026_06/FINDINGS.md`. Historical (shipped v57 2026-05-13, `e568b2f4`): reads `.cache/market_wave/predictive_market_wave_v57_source.csv`; CALL scores (≥70) pulled toward `call_target=62.9712` when stress<35.2364; PUT scores (≤25) neutralized toward `put_target=28.6435` when repair>67.1963; smooth powers `stress_power=2.2651`/`repair_power=1.6602` (no hard cliff). v57 beat v58 on 30DTE portfolio return/DD, was the active baseline after the 2026-05-15 v58 rollback.
- **CWWD — Call Weak-Weekly Dampener** (shipped 2026-05-06 as v38, `b093e2d`): extends CWCF below 75 (CWCF gates `overall≥75 ∧ wadj<1`). Miss-ledger found `b_wadj=neg` on CALL 70+ at z=+9.2 (52.7% miss, N=1,537/5y) — the highest single-feature miss z in the ledger; 70-74 isolated cohort 1,501 signals at 47.1% TP (10.5pp below baseline). Formula: `if 70≤overall<75 ∧ wadj<0: stoch_grad=clip((stoch-25)/35,0,1); wadj_grad=clip(-wadj/5,0,1); overall -= 0.95×(stoch_grad×wadj_grad)×(overall-55)`. Replaces the `WEAK_WEEKLY_CALL_DROP` cascade-stage filter (1 day live) — score-stage encoding fixes the dashboard-badge divergence. Per-trade: 70+ TP +0.62pp/N-4.6%; 75+/80+/85+/90+/95+ byte-identical (zero spillover); puts byte-identical; multi-window sign-consistent. `weight_info` exposes `cwwd_dampen`. Calibration: `experiments/cwwd_v38/sweep.py` (14 variants).
- **PESS — Put Earnings Score Suppression** (shipped 2026-05-06 as v39, `200f33a`): score-stage replacement for EARN_SUPP_PUT cascade filter (10 days live). Lifts puts in [16,20] with earnings within 7 trading days OUT of the qualifying universe (target=28), applied BEFORE the earnings meta-score boost so target=28 clears EARN_BOOST's `overall≤25` gate. Formula: `if 16≤overall≤20 ∧ 1≤days_to_earnings≤7: score_grad peaks 1.0 at 16-20 width-3 fade; proximity=1.0 if d≤5 else (8-d)/3; overall += 0.95×(score_grad×proximity)×(28-overall)`. Per-trade impact small (+0.06pp `<25` cumulative at 5y, v37 PCD already pre-filtered most); portfolio impact via cascade slot displacement (matches EARN_SUPP_PUT's original +44.7% 5y compound). `weight_info` exposes `pess_lift`. Calibration: `experiments/pess_v39/sweep.py` (11 variants).
- **CWCF — Call-side WCF-mirror dampener** — **RETIRED 2026-06-12 in v73** (`CWCF_DAMPEN_K=0`): honest v72 ReSim A/B — removals 50.4% optWR15 vs 54.3% shared (z=−1.96, not real) at 19% of potential 75+ N; the founding wadj-neg z=+10.1 evidence collapsed to noise on the honest re-mine. Retired with CSWC/SCW. Historical (shipped v32 2026-05-01, `43eecea`): mirror of put WCF on calls — when `overall≥75` but weekly non-confirming (`w_adj<1`), pull score down toward 55. Applied after put WCF lift, before earnings boost. Formula: `weakness=clip((1-wadj)/1,0,1); overall -= 0.95×weakness×(overall-55)`. Calibration (32-variant sweep): K=0.95, wadj_cutoff=+1, score_gate=75, lift_target=55. Per-trade (vs v31): 85+ TP+0.95pp(N-5%), 80+ +1.09pp(N-7%), 75+ +1.14pp(N-10%); puts unchanged. `weight_info` exposed `cwcf_dampen`.
- **WCF — Weekly-confirmation floor lift** — **RETIRED 2026-06-12 in v73** (`WCF_LIFT_K=0`): honest v72 ReSim A/B — the lift deleted ~85% of the put band indiscriminately (removals 41.3% optWR15 vs shared 41.7%, z=−0.43 at ≤25); the v27 founding evidence (+5.9pp `<25`, Q1/Q4 −25pp discriminator) was a look-ahead-era artifact, same pattern as MCD. Retirement restores the put assessment surface (~8× `≤25` N at flat WR); puts are OFF portfolio-wide so the funded book is untouched. `experiments/dampener_ablation_v72/FINDINGS.md`. Historical (shipped v27 2026-04-27, `ad02704`): when put score < 28 but weekly non-confirming (`w_adj > -17`), lift toward 50 — final step of `compute_overall_score`. Formula: `weakness=clip((wadj+17)/17,0,1); overall += 0.95×weakness×(50-overall)`. Calibration: Phase 4 Bayesian, 1300+ variants — K=0.95, wadj_cutoff=−17, score_gate=28, lift_target=50, linear-clip weakness (tanh leaks lift into strong-weekly territory). Per-trade (5y vs prior prod): `<10` WR15+4.0pp, `<15`+3.0pp, `<25`+5.9pp, calls untouched, put N −75% (65k→16k). Discriminator: w_adj Q1 84.1%/Q4 58.8% (Δ=−25.2pp).
  - **v72 score-gate ramp (shipped 2026-06-11, `fc5671200`):** the binary gate fired on the POST-REGIME integer, so a 1-pt wobble at 27/28 toggled the full ~21.85-pt lift (GIS/CBRE/GEHC intraday fakeout family). New `WCF_LIFT_RAMP_TOP=33`: full lift bit-identical at `overall≤27`, fades linearly to zero at 33 (`sgrad=clip((33-overall)/6,0,1)` on wadj weakness; `RAMP_TOP=28` reproduces the old binary gate bit-exactly). Per-trade NEUTRAL — zero tradable-bucket changes (194,526-pair sim A/B); `<30` bucket +0.8pp WR15 on −74% N; fakeout groups −60%. `experiments/wcf_score_ramp/FINDINGS.md`.
- **Mis-stress call softener** — **RETIRED 2026-06-10 in v71** (`MIS_STRESS_CALL_DAMPEN=0.0`): the detector read the SPY weekly composite with current-week look-ahead (v69 leak class, fixed v71 via `_spy_wk_last_completed`); on the leak-fixed substrate its 75+ admits ran below the shared baseline (50.7% vs 55.2% optWR15, z=−1.03, 5y full universe). The related JA4 put-regime blend (`_JA4_SPY_WK_WEIGHT`) retired same ship (wash at ≤25; puts OFF). `experiments/integrity_audit_2026_06/FINDINGS.md`. Historical (`MIS_STRESS_CALL_DAMPEN=0.25`, shipped v25 2026-04-26, `9463f02`): on bull-mislabeled-stress days, soften call-side regime compression toward 1.0 by `mis_stress×0.25` (calls only, `overall≥50`). Detector required gap `(spy_wk_composite−regime_composite)≥30` AND objective bull (`SPY>EMA200 ∧ VIX<20 ∧ SPY_10d>0`). Rationale: the 2026-04-09 composite inversion mislabeled narrow-bull tape as STRESS (2023: ~140/255 BULL-objective days labeled STRESS/CAUT-), compressing ~2,689 alpha-rich calls while amplifying ~5,774 zero-EV puts. Validated: 22-now CALL75+ N+5.6%, WR15+0.2pp; 2024 +0.1pp WR; 2022 clean no-op.
- **Post-Crash put Dampener (PCD)** (shipped 2026-05-05 as v37, `6f9afda`): vol-fair dampener lifting put scores out of any ≤25 bucket when the underlying fell >1.0 stock-σ over the last 10 bars. Applied after continuation boost, before earnings boost. Formula: `if overall≤25 ∧ ret_10d_sigma≤-1.0: overall=max(overall,30)` where `ret_10d_sigma = ret_10d/(sigma_60×√10)` (same 60d vol def as strategy TP/SL barriers) — sigma-normalizes so a -10%/10d move means different things for low-vol KO vs high-vol PLTR (regression concentrated in low/mid-vol, z=-5.03 at σ_d 1.5-2.5%, insignificant at σ_d>6% z=-1.63). Calibration: 18-variant fine grid — GATE=25, RET10D_SIGMA=-1.0, TARGET=30 (cutoffs ≤-1.25σ fail H5 at 1y; ≥-1.5σ reduce lift without N gain). Per-trade (30dte_opt W=15d, 5y vs v36): `<5` WR15+2.95pp, `<15`+3.22pp, `<25`+1.56pp, sign-consistent 1y/3y/5y; ~30% of ≤25 put peaks displaced (by design); calls untouched. `weight_info` exposes `pcd_active`/`pcd_r10sigma`.
- **Earnings meta-score boost** — **RETIRED 2026-06-15 in v74** (`EARN_BOOST_ENABLED=False`): Phase-3d apex-EV attribution found it NEUTRAL — the apparent +7% apex-EV was N=77 noise (→+0.2pp at N=179), confirming the v70 honest-recalibration verdict that the earnings premium is thin/hygiene-not-alpha once look-ahead is removed. Retired with the post-`pre_boost` tail (−10.8pp whole-tail DD, collapse=0). **The look-ahead-leak class this mechanism founded (a fitted lift table inside the scorer) is now structurally guarded by the post-MOS `LiftTableMechanism` holdout-stamp contract** — a future re-add is forced through a `<table>.meta.json` holdout-cutoff stamp (WARNS today on the unstamped `lift_table_v70.json`). **Revisit gate:** apex 0d predictand + forward-holdout (opened 2026-06-15) + a stamped lift table.

  Historical (shipped v28 2026-04-28, `e3c8678`, final v35 recalibration 2026-05-04 `e77714f`): WR-calibrated log-smoothed multiplier on `(overall−50)` within `EARN_BOOST_WINDOW=5` trading days of earnings — final transform in `compute_overall_score`, symmetric around 50. `proximity=log(W+1-d)/log(W+1)`; `strength=log(1+lift)/log(1+lift_norm)` from a calibrated lift table per `(side,cohort,bucket)`; `boost_mult=1+EARN_BOOST_MAX×proximity×strength`; `overall=clip(round(50+(overall-50)×boost_mult),0,100)`. Defaults: `EARN_BOOST_MAX=0.55`, `LIFT_NORM_CALL=14.0`, `LIFT_NORM_PUT=16.3`, `MIN_N=10`; calls always boost if ≥70, puts only if already ≤25 (`EARN_BOOST_PUT_ADMIT=False`). Lift table `experiments/v34_calibration/lift_table_v34.json` (rebuilt from `pre_boost` to remove v28-boost contamination; v27 table at `experiments/v27_optimization/phase_tp3b_lift_table.json` kept for reference). Cohorts pre1/pre3/pre7; N<10 or lift≤0 → strength=0. Headline v34 cells (5y): pre1 75-79 +29.2pp(N=183), pre1 80-84 +24.5pp(N=54), pre1 70-74 +21.4pp(N=879); puts pre1 11-15 +37.3pp(N=93), pre1 16-20 +20.6pp(N=279). Per-trade v35 vs v34: 95+ +4.20pp/N+4.9%, 90+ +4.18pp/N-1.1%, 85+ +0.86pp/N+8.1%, 80+ +0.98pp/N+8.9%, `<5` puts +2.72pp/N+18.8% — H1-H5 PASS all windows. Original v28 ship (vs v27): 80+ N+137/WR15+1.26pp, canonical N=200 MC 22-now Realistic compound +10,876%. `weight_info` exposes `ern_boost`/`days_to_ern`/`pre_boost`.
- ~~**X-confidence gate on `d`**~~ (briefly shipped v22 2026-04-21, `41784e0`, **reverted same day** — canonical MC showed −91% on 2024 Realistic and a DD-C floor breach on 5y+2023; see Priority #8). Would have repurposed technical_alignment (X) as a structural confidence coefficient gating trend dominance: `agreement=max(bull,bear)/5`, `signal_strength=|Σ(s-50)|/(5×50)`, `x_conf=signal_strength×agreement²`, `d_eff=d×x_conf^0.50`. Validated well on the counter-trend put bucket alone (PSKY case: N 541→934 +73% on 22-now, WR15 79.5%→81.9%) but broke the broader portfolio — reverted.

### Weekly Scoring
- Weekly RSI+MACD from weekly OHLCV aggregates; `calculate_weekly_composite(rsi,macd,trend)` stored every Monday; feeds daily adjustment, filters false signals in choppy markets.

### Weekly Adjustment Mechanics (`calculate_weekly_adjustment`)

Applied additively to `weighted_sum` before volume amplification.

| Component | Formula | Max magnitude |
|-----------|---------|---------------|
| **Base bias** | `15 × tanh(deviation × 1.5) × agreement` | ±15 pts (before agreement scaling) |
| **Momentum bias** | `8 × tanh(delta / 15)` (delta = current − prior week composite) | ±8 pts |
| **Agreement amplifier** | `0.8 + 0.6 × consistency` (directional alignment of trend/rsi/macd) | 0.8-1.4× on base_bias |

### Weekly Magnitude Ablation (2026-04-15, `experiments/weekly_magnitude_sweep.py`)

Scaled the full weekly adjustment 0.0×-1.5× against v17 DB scores, 5y, full universe (~669 stocks, 803k pairs). **1.0× is the call-side optimum** — WR30 peaks at 1.0× for 95+/85+/80+/75+ (the MC-driving buckets); removing weekly (0.0×) loses 28% of qualifying call peaks and degrades WR30 5-23pp across buckets. 90+ at 0.75× looks better (87.8% vs 81.7% WR30) but N drops 75→43 — a quality illusion from shrinking N, not smarter selection. At 1.5×, 90+ N balloons to 320 but WR30 drops to 78.7% (dilution). **Puts improve monotonically with scale**: `<25` WR30 60.7%(0.0×)→65.7%(1.5×); `<15` 60.3%→69.3%. Untested follow-ups: asymmetric weekly scale (1.0× calls / 1.25-1.5× puts); split base-bias vs momentum scaling; fine-grain sweep 0.85-1.15.

---

## Volume Signal System (volume_amplifier.py)

**Daily Volume Authority Wave (v59, `4fd7ffa9`)** — **RETIRED 2026-06-15 in v74** (`DAILY_VOLUME_AUTHORITY_WAVE_ENABLED=False`): Phase-3d apex-EV attribution found the post-`pre_boost` DVAW negative-EV (adds ran −1.66% apex-EV vs +2.57% common — buying low-EV signals). The CORE daily volume classifier/amplifier (weighted_sum stage) is UNCHANGED and active; only the v59 weekly-governed authority wave applied after `pre_boost` is retired. Retired with the post-`pre_boost` tail (−10.8pp DD, collapse=0). **Revisit gate:** apex 0d predictand + forward-holdout (opened 2026-06-15). Historical: weighted daily-candle conviction by weekly volume force so daily conviction is strongest when the weekly tape confirms and fades when weekly participation is weak — wave-like (continuous authority, soft high-tier taper, no cliff).

| Signal | Condition | Impact |
|--------|-----------|--------|
| CONVICTION | Large candle body + volume >1.5x 50/200-day ratio | Up to +50% amplification (`MAX_AMPLIFICATION=0.50`; suppression cap asymmetric at −45%, `MAX_SUPPRESSION=0.45`) |
| ABSORPTION | Doji + high volume at price extreme | Reversal setup |
| REJECTION | Wick spike reversal + volume | Mean-reversion confirmation |
| CLIMAX | Volume >4x 50-day average | Dampened −40% if contra-signal |
| THIN_AIR | Large move on <0.7x avg volume | Suppressed −30% |
| NEUTRAL | No signal | 1.0x multiplier |

**Decay model**: half-life 1.5-3 days; extreme signals (magnitude≥0.5) decay slower.

**Intraday TYPE-stability gate (`INTRADAY_TYPE_CONF_GATE=0.5`, shipped 2026-06-03, no version bump).** `_classify_signal` takes an intraday `confidence` and returns `NEUTRAL` when `confidence < gate`. Early-session projected full-day volume (`ratio_50`) is noisy and oscillates across the CONVICTION(1.5×)/THIN_AIR(0.7×) cliffs, flipping signal TYPE and swinging the score ~18pts intraday (the ATI/AMSC "fakeout" — `confidence` previously scaled only magnitude, not TYPE). Gate holds NEUTRAL until ~10:15 (confidence≥0.5 over the 90-min `INTRADAY_CONFIDENCE_WINDOW`) **unless a banked-volume lock fires**: `_effective_type_confidence` overrides the clock when observed (already-traded) volume alone crosses the elevated cliff (`observed/avg_50 ≥ ELEVATED_RATIO_THRESHOLD`) — observed volume is a lower bound on final `ratio_50`, so a genuine early spike locks its tier immediately rather than being missed; only the low-banked/projection-driven case falls back to the time gate. **EOD-invariant by construction:** `_analyze`/`_analyze_from_cache` set `confidence=1.0` for `is_historical` bars — every stored score/assessment/backtest is bit-identical, only live partial-day evolution is smoothed. Not an alpha change (funded-WR-neutral, verified by an 8-variant 5y flatten sweep, `experiments/volume_rework/`). `INTRADAY_TYPE_CONF_GATE=0` reverts. Contract test: `tests/test_volume_intraday_stability.py`.

---

## Market Breadth System (market_breadth.py)

Computed daily from raw PriceHistory + Indicator data for the tracked universe (~727 stocks post-2026-05-08 ETF filter; was ~772 with ETFs). Stored in `market_breadth`.

**ETF EXCLUSION (v45 ship 2026-05-08, `56eb1f8`):** `_get_daily_breadth` restricts to `Stock.sector IS NOT NULL` (ETFs have NULL sector from yfinance), removing ~45 ETFs: 11 sector SPDRs, 4 broad indices (SPY/QQQ/IWM/DIA), **6 leveraged 3x ETFs (TQQQ/SOXL/LABD/BOIL/SVIX/TNA)** — most distorting, 3× amplitude contribution to advancing/declining and TRIN — 6 international, 8 commodity/bond, ~10 sub-industry/thematic. Pre-filter Pearson with sector-breadth-aggregate ETF basket=0.7075; post-filter separates the two breadth concepts (production=equal-weighted internal participation; sector basket=mcap-weighted directional flow). See version-history.md v45.

**Breadth does NOT depend on scores** — reads `PriceHistory`(close/volume/high/low) + `Indicator`(EMA50/200) directly, computable before or in parallel with scoring.

### Indicators

| Indicator | How Computed |
|-----------|-------------|
| Advancing/Declining | Close vs prior close per stock |
| TRIN (Arms Index) | (A/D ratio) ÷ (advancing vol/declining vol); <1 bullish, >1 bearish |
| Cumulative A-D Line | Running sum of daily (advancing−declining) |
| McClellan Oscillator | EMA19(A-D diff) − EMA39(A-D diff), short-term breadth momentum |
| McClellan Summation Index | Cumulative sum of Oscillator, slow-moving regime health |
| New 52w Highs/Lows | Within 3% of 252-day high/low |
| % above EMA50/EMA200 | From Indicator table |
| Zweig Breadth Thrust | EMA10 of A/(A+D) crosses <40%→>61.5% within 10 trading days |
| Hindenburg Omen | NH & NL both ≥ max(3%,10 stocks); McClellan negative; >50% above EMA50; NH ≤ 2×NL |

### Breadth Score (0-100) — fed into regime composite

| Component | Weight | Notes |
|-----------|--------|-------|
| McClellan Oscillator | 28% | Best leading indicator |
| McClellan Summation 10d trend | 20% | Confirms sustained shifts |
| New 52w Highs vs Lows | 20% | Participation quality |
| TRIN | 15% | Real-time volume flow |
| % above EMA50 | 12% | Lagging trend backdrop |
| % above EMA200 | 5% | De-emphasized, very slow |

**Event decay** (additive, after weighting): Zweig Thrust +15pts trigger day, half-life 7d, clears 20d. Hindenburg Confirmed −18pts, half-life 10d, clears 35d. Both stored as live float values (`zweig_boost`, `hindenburg_penalty`).

**Hindenburg threshold**: with ~470 stocks, `max(3.0%×total, 10 absolute)` ≈ 14 stocks minimum per side.

**CLI**: `trader breadth-backfill [days]` (default 365d) — process oldest-to-newest (EMA chain correctness); `TRUNCATE TABLE market_breadth` first for a clean slate.

---

## Market Regime System (market_regime.py)

Computes a composite from market-wide signals → a multiplier applied to every stock's `Score.overall` symmetrically around 50.

The LIVE composite (`compute_regime_composite`, `market_regime.py:217`) is a **dynamic two-signal blend of VIX and inverted breadth, market trend always 0** (`_vix_dynamic_weight`, `market_regime.py:207`):
```
composite = w_vix × vix_score + (1 − w_vix) × inverted_breadth_score
w_vix = _vix_dynamic_weight(vix_close)   # sigmoid centred at VIX 22, scale 0.25
```
`w_vix` shifts breadth-dominated (~9% VIX weight at VIX 13) → VIX-dominated (~96% at VIX 35), clipped [0.05, 0.95]. Breadth is inverted (weak breadth = more reliable signal).

> **Historical/vestigial:** the static `SIGNAL_WEIGHTS` dict (`market_regime.py:31`, Breadth 35%/VIX 35%/market_trend 30%) is the pre-2026-04-09 Gen-1 static composite and is NOT used by `compute_regime_composite` — don't treat it as live.

### Regime Composite → Multiplier Bands

| Composite | Multiplier | Label |
|-----------|------------|-------|
| 0-15 | 0.70 (floor) | STRESS |
| 15-30 | 0.70-0.78 | CAUTION |
| 30-45 | 0.78-0.88 | CAUTION |
| 45-60 | 0.88-1.00 | NEUTRAL |
| 60-75 | 1.00-1.05 | HEALTHY |
| 75-100 | 1.05-1.10 | BULL |

**Score application** (inside `compute_overall_score`): scores≥50 → `50+(score-50)×multiplier`; scores<50 → `50+(score-50)×(2.0-multiplier)` — both extremes suppressed equally in stressed regimes. Final step before clamping.

### Data dependencies — breadth does NOT need scores
VIX/SPY from yfinance; breadth from `PriceHistory`+`Indicator` (`_get_daily_breadth()`). Breadth and regime compute before scoring starts — no chicken-and-egg dependency.

**CLI**: `trader update` auto-runs breadth+regime+scoring. `trader regime-backfill [days]` backfills `MarketRegime` (default 365d), does NOT mutate historical `Score.overall`. `trader assess --regime-adjust` is a no-op now (scores already include regime; kept for compatibility).

---

## Regime integration into scoring (COMPLETED 2026-04-13)

Regime multiplier is baked into `compute_overall_score()` — no destructive "Pass 2" overwrite.

```
PriceHistory + Indicators → MarketBreadth → MarketRegime → Score.overall
                                                            (regime_multiplier applied INSIDE scoring)
```

`compute_overall_score()` accepts `regime_multiplier`, applies symmetrically around 50 as the final step, stores `pre_regime` in `weight_info`. `Score.calculate_overall_score()` looks up `MarketRegime` for the date if no multiplier passed. `calculate_scores_batched()`/`recalculate_scores_batched()` bulk-load and pass per-date multipliers. `simulator.py` loads the `MarketRegime` map too.

**`trader update` flow**: (1) score all stocks using whatever `MarketRegime` row exists today via `_regime_map` — falls back to the most recent prior regime row if none yet (never implicit 1.0). (2) After scoring: `compute_regime()` computes fresh regime from today's data, stores the row, returns `regime._created=True` on first compute for today. (3) If `_created`: `reapply_regime_today()` immediately patches today's `Score` rows with the fresh multiplier (reads `weight_info['pre_regime']`, recomputes `overall`, one atomic transaction). Subsequent runs are idempotent.

**`trader recalculate` flow**: regime auto-included — `recalculate_scores_batched()` bulk-loads and applies per-date, no separate re-application step.

**Key invariants**: `Score.overall` is ALWAYS regime-adjusted; `Score.regime_multiplier` always in sync; `weight_info.pre_regime` holds the raw pre-regime score; NULL `regime_multiplier` → treated as 1.0; premarket scores fall back to yesterday's regime, never implicit 1.0.

| Function | Location | Purpose |
|----------|----------|---------|
| `MarketRegime.latest_on_or_before(d)` | `core.py` | Most recent regime row with non-null multiplier on/before `d` |
| `reapply_regime_today(mult, composite, target_date)` | `core.py` | Reads `pre_regime` for today's Score rows, recomputes `overall`, bulk-saves in one transaction. Called by `trader update` after the day's first `compute_regime()`. |

**Coverage gate**: 80% of tracked stocks must have PriceHistory for the date before `compute_regime()` runs.

**Deleted functions**: `_apply_regime_to_scores()` (bulk post-hoc overwrite, replaced by inline scoring), `reapply_stored_regime()` (interim fix, no longer needed), `compute_and_apply_regime()` (renamed `compute_regime()`, no longer mutates scores).

### A/B Test Framework — `experiments/regime_ab_test.py`

Tests alternate regime composites/bands against stored scores without DB writes: reads `weight_info['pre_regime']` per Score row, re-applies each variant's multiplier, re-qualifies peaks against ≥70/≤25, runs barrier-touch walk, reports WR15/WR30+N per bucket per variant.

```bash
python experiments/regime_ab_test.py 1825                      # 5y lookback from today
python experiments/regime_ab_test.py --start=2022-01-01 --end=2022-12-31
```

**Variants (keep all 9 for future sweeps):** A no regime · B current production · C VIX-only · D breadth-only · E static 50/50 · F Fear&Greed proxy (VIX 40%+breadth 30%+credit 15%+haven 10%+SKEW 5%) · G wider bands (floor 0.55/ceil 1.15) · H asymmetric (floor 0.60/ceil 1.05) · I wider capped (floor 0.60/ceil 1.06). F&G data (`hyg_close`,`lqd_close`,`tlt_close`,`skew_close`,`hyg_lqd_ret_diff`,`tlt_spy_ret_diff`,`credit_spread_score`,`haven_score`,`skew_score`,`fg_composite`,`fg_multiplier`) stored via `backfill_regime()`, 5y backfill = 1,281 dates, no live network dependency at assessment time.

### Findings from A/B testing (v14+v17, 5y + 2022 bear)

**F (Fear&Greed) does not beat B** — credit spread/haven/SKEW correlate too heavily with VIX+breadth for marginal value; same result on both v14 and v17.

**G (wider bands) small win on v14, marginal on v17** — trade-off: wins +0.6-1.7pp WR15 at 80-84/75-79 but loses −2.2-2.5pp at 90+ (wider ceiling lets borderline 88-89 into 90+, diluting quality) and drops N ~16% at 75-84 (wider floor suppresses more). On v17 the 90+ baseline is 74.0% WR15 (5y) — **keep B**; re-test G if a future change materially shifts the 90+ baseline.

**I (wider_capped, floor 0.60/ceil 1.06) doesn't work** — hurts 80-84 WR15 (−3.3pp in 2022 vs B) without recovering the 90+ loss; suppression and boost are coupled, can't tighten one without losing the other.

**Structural insight:** band widening trades mid-conviction retention against top-bucket discrimination. Evaluate any future regime-tuning against the actual signal density×allocation×WR structure (90+ fires ~0.06/day, 75-79 ~1.8/day on v17).
