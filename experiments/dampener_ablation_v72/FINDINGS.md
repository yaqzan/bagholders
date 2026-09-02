# N1 Dampener-Stack Ablation on the Honest v72 Substrate — 2026-06-12

**Status: COMPLETE — all 7 mechanisms analyzed. Outcome: 2 clear RETIRE candidates (WCF, ICH),
3 marginal retire-leaning (CWCF, CSWC, SCW — growth gate decides), 2 KEEP (CWWD, WVD).
v73 candidate STAGED (see SHIP_HANDOFF.md), not shipped (3h morning budget — Stage > rush).**

## Question

Every remaining score-stage dampener — WCF, CWCF, CSWC, CWWD, SCW, ICH, WVD — was calibrated
**pre-v69 on look-ahead-contaminated scores** and pre-honest outcome labels (NEW_LEADS lead N1).
The v71 integrity campaign already retired 4 of the 10 stack members on honest A/B evidence
(mis_stress, JA4, MCD, Sector Market Wave) and returned +83% 75+ supply at +1.9pp WR15.
This run asks the remaining seven to re-earn their keep on the honest v72 substrate.

## Method

Sharded ReSim A/B (the proven `experiments/integrity_audit_2026_06/ab_eval.py` pattern, G30):
build `ScoreSimulator` once per shard (6 shards, full universe, EVAL_START=2021-06-01 ≈ 5y),
run all 8 arms in-process via module-constant patches with try/finally restore, persist
`(symbol, date, overall)` per arm, join to the full-10y `barrier_outcomes` DuckDB mirror
(rebuilt v71 F3), judge by **delta-cohort WR on the option-aligned barrier** (`30dte_opt`,
w=15d): a dampener earns its keep iff its REMOVALS run clearly BELOW the shared-cohort WR.

- Validation: baseline arm vs stored v72 rows — smoke 98.53% exact (cont-echo gap, same
  profile as the v71 campaign's 98.43%).
- Doctrine: bias toward RETIREMENT when marginal (v71 precedent).
- Puts are OFF portfolio-wide — put-side verdicts (WCF, ICH-put leg) affect the assessment
  surface, not the funded book.

| Arm | Patch | Mechanism (ship-time evidence, INFLATED era) |
|---|---|---|
| baseline | — | exact v72 replica |
| wcf_off | `WCF_LIFT_K=0` | v27 put-floor lift (+5.9pp <25 WR15 at −75% put N) + v72 ramp |
| cwcf_off | `CWCF_DAMPEN_K=0` | v32 call WCF-mirror (+1.14pp 75+ at −10% N) |
| cwwd_off | `CWWD_DAMPEN_K=0` | v38 call weak-weekly 70-74 (+0.62pp 70+ at −4.6% N) |
| cswc_off | `CSWC_DAMPEN_K=0` | v36-era call stoch/weekly conviction dampener |
| scw_off | `SCW_ENABLED=False` | v50/v60-r054 stoch conviction wave (70+ WR7 +1.1pp at −15% N) |
| ich_off | `ICH_ENABLED=False` | v44 Ichimoku kijun dampener (95+ +5.77pp at −7.7% N; put<25 +0.72pp at −14.9% N) |
| wvd_off | `WVD_WAVE_ENABLED=False` | v46/v59 weekly-volume wave (90+ +1.54pp / <15 +2.67pp at 5y) |

Retirement flip points (a v73 ship edits `strategy_config.SCORING`):
`WCF_LIFT_K` (L409), `CWCF_DAMPEN_K` (L416), `CWWD_DAMPEN_K` (L424), `CSWC_DAMPEN_K` (L432),
`SCW_ENABLED` (L435), `ICH_ENABLED` (L524), `WVD_WAVE_ENABLED` (L539).

## Run facts

- Task #150 (6 shards, 8 arms): killed at the 2h timeout with 6 arms × 6/6 shards complete
  (ich_off 4/6, wvd_off 1/6); per-arm simulate ≈ 18 min/shard, so 8 arms ≈ 2h20m — the 2h
  timeout was mis-sized. Resume = task #151 (`EVAL_ARMS=ich_off,wvd_off` + a skip-if-parquet-
  exists resume guard added to the harness).
- Full-universe validation (940,088 common rows): baseline vs stored v72 **98.54% exact**,
  ge3-diff 0.60%, 31% of sampled big diffs carry `cont_lift` (the known simulator gap).
  Same profile as the v71 campaign's 98.43% — harness trusted.
- Arms: 941,328 (sym,date) rows each; baseline signal rows (≥70 or ≤30): 34,399.
  Baseline 75+ N=3,262 / 5y; shared-cohort 75+ optWR15 54.3% (call BE 45.0%).

## Results (option-aligned barrier `30dte_opt` w=15d; delta-cohort vs shared)

| Mech | side/gate | ON-removes N | removes WR | shared WR | z | % of potential N suppressed |
|---|---|---:|---:|---:|---:|---:|
| **WCF** | put ≤25 | 37,802 | 41.3 | 41.7 | **−0.43** | ~88% of put band |
| WCF | put ≤30 | 111,169 | 40.9 | 41.4 | −1.24 | ~85% |
| **CWCF** | call ≥75 | 784 | 50.4 | 54.3 | **−1.96** | 19.4% |
| **CWWD** | call ≥70 | 1,476 | 48.8 | 52.0 | **−2.28** | 9.0% (lands in dead 70-74) |
| CWWD | call ≥75 | 89 | 50.6 | 54.3 | −0.69 | 2.7% |
| **CSWC** | call ≥75 | 914 | 51.2 | 54.2 | **−1.63** | 21.9% |
| **SCW** | call ≥70 | 2,643 | 49.3 | 51.9 | **−2.47** | 15.1% (mostly 70-74) |
| SCW | call ≥75 | 519 | 50.3 | 54.3 | −1.69 | 13.7% |
| **ICH** | call ≥75 | **1** | — | 54.3 | — | ~0% (INERT on calls) |
| ICH | put ≤25 | 686 | **45.0** | 41.7 | **+1.65 (wrong-way)** | 11.8% |
| **WVD** | call ≥70 | 262 | **41.6** | 51.9 | **−3.32 (real, below BE)** | 1.7% |
| WVD | call ≥75 | 346 | 48.8 | 54.3 | −1.92 | 9.6% |

ON-admits ≈ 0 everywhere (pure dampeners — they only remove). Full bucket tables:
`.cache/dampener_ablation_v72/analysis_report.txt`.

**Trio union (CWCF+CSWC+SCW, from the single-ablation parquets):** 2,025 distinct 75+
signals suppressed = **+61% supply recovery LOWER BOUND** (signals dampened by ≥2 mechanisms
only restore in a bundle, so the true bundle union is larger) at pooled optWR15 **50.7%** —
every restored signal clears call BE (45.0%) by ~6pp; blended 75+ mix ≈ 54.3 → 52.9 (−1.4pp)
for +61% N. The W5 hydration logic (v72 coverage 0.781 — droughts bind) is what adjudicates
this WR-for-velocity trade; v71's strictly-better version of the same trade (+83% N at
+1.9pp) shipped and validated.

## Verdicts (bias-to-retire doctrine, v71 precedent)

1. **WCF — RETIRE candidate (assessment-surface only).** Zero quality discrimination on the
   honest substrate (z=−0.43 at ≤25): it deletes ~85% of the put band INDISCRIMINATELY. The
   v27 founding evidence (+5.9pp <25, Q1/Q4 −25pp discriminator) was look-ahead-era artifact —
   the exact MCD pattern. BUT puts are OFF portfolio-wide → retiring is a diagnostics/honesty
   win only, NOT worth a version bump alone. Fold into a bundle ship if one happens. (The v72
   ramp itself remains correct as long as WCF exists — it smooths the gate, orthogonal issue.)
2. **CWCF + CSWC + SCW — MARGINAL, retire-leaning, growth-gate decides.** All three select
   below-average-but-PROFITABLE calls (removals 50.3-51.2% ≫ BE 45%), none statistically real
   at the tradable gate (|z| 1.6-2.0). Each suppresses 14-22% of potential 75+ N; naive union
   ≈ +2,200 signals on a 3,262 base (~+68%, true union less — overlapping cohorts). On the
   supply-starved honest substrate (v72 coverage 0.781, droughts bind W5), returning +50-68%
   above-BE supply at a ~−1.5pp blended WR mix shift is plausibly a growth-gate SHIP — the v71
   trade (+83% N at +1.9pp) was strictly better and shipped; this one trades a small WR mix
   decline for hydration, so it MUST be adjudicated by `stage1_growth_gate.py` with REAL
   supply, not eyeballed.
3. **CWWD — KEEP.** The one weak-weekly call dampener that is statistically real (z=−2.28) —
   and its removals land in 70-74, which has zero allocation (overflow=0 since the v71
   retune), so keeping it is free and keeps the diagnostic tier honest.
4. **ICH — RETIRE candidate (the "wave pattern": inert + wrong-way).** The call leg no longer
   fires on the honest substrate: ON-removes at ≥75 is N=1 over 5y full universe; the only
   effect is intra-band down-shifting of ~5-9 top-tier rows (90-94 ON 68.2% N=44 vs OFF
   61.2% N=49 — noise-level N). The v44 founding cohort (kijun<0 high-conviction calls,
   95+ +5.77pp) has essentially vanished post-honest-weekly — the mechanism is dead weight
   on calls. The put leg is WRONG-WAY: it deletes 686 ≤25 puts at 45.0% vs shared 41.7%
   (z=+1.65) — removing better-than-average puts — and its 40 admits run 32.5% (z=−1.18).
   Nothing earned on either side; retire for honesty/simplicity (puts-off makes it
   portfolio-inert either way).
5. **WVD — KEEP (the clear winner of the audit).** Its fully-deleted cohort (≥70) runs
   **41.6% optWR15 vs shared 51.9% (z=−3.32)** — statistically real AND below call BE
   (45.0%): WVD deletes genuinely money-losing calls. At the ≥75 gate its removals (346 at
   48.8%, z=−1.92) include down-shifts into 70-74. Zero put-side effect. The only mechanism
   of the seven whose removals are below break-even — it earns its keep exactly as designed.

## Bottom line

The honest-era audit pattern repeats a third time (v69 weekly, v71 retirements, now this):
**most of the pre-v69 dampener stack does not survive honest re-measurement.** Of 7 mechanisms,
only WVD (and CWWD, on the dead tier) demonstrably select bad signals; WCF and ICH are
artifact/inert; the CWCF/CSWC/SCW trio suppresses +61% (lower bound) of potential 75+ supply
whose quality (50.7%) is well above break-even — a hydration-vs-mix trade only the W5 growth
gate can adjudicate.

## Artifacts

- Harness: `experiments/dampener_ablation_v72/ab_eval.py` (8 arms × 6 shards)
- Analyzer: `experiments/dampener_ablation_v72/analyze.py`
- Arms + verdicts: `.cache/dampener_ablation_v72/`

---

# v73 SHIP ADJUDICATION — 2026-06-12 (ship session)

**DECISION: Option B** — retire WCF + ICH + CWCF + CSWC + SCW; keep CWWD + WVD.

## Bundle confirm (handoff step 1; task #152, 56m45s)

Arms `bundle_a` (WCF+ICH off) and `bundle_b` (+trio) on the same 6-shard ReSim
(941,366 rows each; merged validation 98.54% exact unchanged). Analyzer pairs in
`analysis_report_bundles.txt` / `verdict_data_bundles.json`:

- **bundle_a**: call side neutral by construction (≥75 ON-removes N=1 — the
  single ICH row); put band restores 45,406 ≤25 signals at 41.1% vs shared
  41.8% (z=−0.96) — indiscriminate-deletion read confirmed at bundle level.
  Bundle > sum-of-singles on puts (45.4k vs 38.5k) is mechanically expected:
  overlapping lifts only restore jointly. 63 put "admits" (0.13%) = downstream
  interaction noise.
- **bundle_b**: ≥75 ON-removes **2,507 at 50.5% vs shared 54.3% (z=−2.84)** =
  **+77% 75+ supply** (above the 2,025 single-union lower bound, as predicted),
  every restored signal above call BE 45.0. Put side identical to bundle_a ✓
  (trio is call-only — clean separability).

## Growth-gate adjudication (REAL supply, calls-only)

`gate_inputs.py` synthesized pack-format bands + real per-day supply rows from
the arm parquets (tokens v72sim / v73a / v73b; same shrinkage/refs as the
research packs). **Calls-only by design**: the live Apex book is calls-only
(PUT_TIER_ALLOC=0, MAX_POSITIONS_PUT=0) but the gate's greedy fill still models
puts at stale v60-era allocs — the WCF put flood (~8×, identical in both
candidates) would saturate the modeled fill and mask the call-side trio signal.
Baseline = ReSim baseline arm (98.54% exact) — same measurement as candidates.

| pair | dG option (5y) | dG generic (5y) | binding window | W4 | W6 | verdict |
|---|---:|---:|---|---|---|---|
| v72sim → v73a | +0.74% | +0.24% | 2022 (+0.70%) | clean | clean | FLAG (scoring-neutral by construction — as the handoff predicted) |
| **v72sim → v73b** | **+19.56%** | **+36.27%** | dip (+11.82% / +27.45%) | **clean** (worst: 80-84 wr −3.87pp z=−1.62 = noise) | clean | **FLAG (wide CI only: option p05 −8.7%; generic confidently positive p05 +18.9%)** |

Calls-only coverage: v72 0.323 (2023 binds at 0.14!) → v73b 0.483 — every
window improves. ebar dips (mix −1.4pp blended) but velocity dominates on the
starved substrate — exactly the WR-for-velocity trade W5 exists to adjudicate.

**FLAG-teeth compliance** (the gate FLAGs on bootstrap CI width from thin top
tiers, NOT a point regression): (a) justification = hydration-dominant growth
win, positive in every window on both barriers; (b) named watch metric =
post-ship 5y assess **85+/80+ option-TP15** (the dilution tiers; 85-89 ReSim
read 54.5→49.5 on N 132→220, z=−0.88) plus 75+ supply/day and binding-window
coverage in the next supply row; (c) downstream confirmation = pre-ship MC
smoke below + the post-ship Stage-3 sizing retune (v71 precedent, seeded via
`portfolio_response.py --derive`).

## W1-W6 record

- **W1** (retirement track, v71 doctrine): a dampener keeps its seat only if its
  removals run clearly BELOW shared on the option barrier. WCF z=−0.43, ICH
  inert/wrong-way, trio |z| 1.6-2.0 — none earn their keep. CWWD (z=−2.28) and
  WVD (z=−3.32, below-BE deletions) DO → kept.
- **W2** (multi-barrier-window): restored ≥75 cohort vs shared at w=3/5/7/15/30,
  option barrier: 63.0/58.9/55.5/50.5/49.2% — above call BE 45.0 at EVERY
  window; uniformly below-shared (no directional flip; the dilution is real and
  consistent, owned by the ship rationale). PASS.
- **W3** (multi-time-window): per-window dG positive in every window on both
  barriers (2022/2023/2024/2025/dip/5y). PASS.
- **W4**: clean (gate, noise-aware z). PASS.
- **W5**: FLAG → shipped per FLAG-teeth rule (above).
- **W6**: clean (no candidate-introduced real inversion). PASS.
- **Holdout**: ReSim window 2021-06-01→2026-06-12 ends before
  CALIBRATION_CUTOFF_DATE=2026-06-15. PASS.

## Worktree verification

`algo-exp/v73-dampener-retire` flips (`strategy_config.SCORING`): WCF_LIFT_K=0,
CWCF_DAMPEN_K=0, CSWC_DAMPEN_K=0, SCW_ENABLED=False, ICH_ENABLED=False
(CWWD/WVD untouched). Worktree smoke ReSim vs main's `arm_bundle_b.parquet`:
**6,323/6,325 bit-identical** — the 2 diffs are today's date (live intraday
drift between run times, not code). Drift-guard 645 + registry 198 green.

## Pre-ship MC smoke (N=300 × 7 windows, paired)

`mc_smoke.py` (task #153): signals from arm parquets (parent-side loader patch;
hydration attrs from stored v72 rows, which the retirements don't touch).
Windows 2022/2023/2024/2025/dip/22-now/5y_sim (parquets start 2021-06-01 —
2021/COVID windows get their full read at the post-ship Stage-3 retune on real
recalc'd rows). Results: `.cache/dampener_ablation_v72/mc_smoke_*.json`
(task #154, 12m58s; #153 was killed — peewee FK-per-row trap in the patched
loader + stdout-rewrap-blinded log, both fixed in `ea15d3390`).

| window | base MedRet | cand MedRet | base DD | cand DD | dDD | collapse |
|---|---:|---:|---:|---:|---:|---:|
| 2022 | +64% | +2% | 50.7 | 56.0 | +5.3 | 0 / 0 |
| 2023 | −27% | −43% | 50.0 | 61.7 | +11.6 | 0 / 0 |
| 2024 | +771% | +980% | 27.6 | 32.5 | +5.0 | 0 / 0 |
| 2025 | +137% | +60% | 41.5 | 54.5 | +13.0 | 0 / 0 |
| dip | +125% | +131% | 33.2 | 39.3 | +6.1 | 0 / 0 |
| 22-now | +5,184% | +1,171% | 54.1 | 62.1 | +8.0 | 0 / 0 |
| 5y_sim | +10,573% | +2,811% | 54.5 | 62.7 | +8.2 | 0 / 0 |

**Smoke gate: PASS — collapse 0.0% on every cell, both arms.** The hotter
DD/compound profile at UNCHANGED Apex params is the v71 signature reproduced
(v71: +83% supply at v70-fitted params → DD +9.2pp / compound −50%; the c14
retune — TIER_LOW 0.10→0.05, overflow→0 — then recovered EVERY window's DD and
+35% compound). Max candidate DD 62.7% is inside the documented Apex
recoverable-DD budget (80-90%). ⇒ ship + **immediate Stage-3 sizing retune on
the v73 density** (seeded from PRF `portfolio_response.py --derive v73`),
exactly per the handoff. Interim rollback: `/revert v72`. Note the baseline
here is the ReSim-sourced paired arm (no cont-echo, 5y_sim window) — not
directly comparable to official stored-row v72 MC numbers; pairing is internal.

## SHIPPED 2026-06-12 — post-recalc validation read (real rows)

Merge `07e9722b5` → bump `e32fb4ec6` → lock+silo `a51311fa7`. Recalc: 1d
(1,543 rows, 0 err) + 5y `--force` (44 min, full assess/temporal tail, 0 err);
10y full queued off-market (#157).

**Watch metric (FLAG-teeth), production 5y assess v72→v73 (30dte, metric=tp):**

| bucket | v72 WR15 / N | v73 WR15 / N | d | z |
|---|---|---|---|---|
| 75+ | 53.2% / 3,419 | 51.4% / 5,276 (+54%) | −1.8pp | — (predicted −1.6) |
| 80+ | 53.0% / 880 | 50.2% / 1,415 (+61%) | −2.8pp | −1.3 (noise) |
| **85+** | 59.9% / 167 | 53.0% / 269 (+61%) | **−6.9pp** | **−1.41 (not real; WATCH)** |
| 95+ | 83.3% / 6 | 66.7% / 9 | micro-N noise | — |
| ≤25 put | 43.1% / 3,195 | 43.9% / 22,313 (×7) | **+0.8pp** | restored band ≥ WCF survivors |
| <15 put | 49.1% / 232 | 43.1% / 3,061 | −6.0pp | old band was a 232-row boutique; all > put BE 36.4 |

All call tiers above call BE 45; the 85+ dilution moved in the predicted
direction but stays statistically noise — keep watching on the next assess
cycles + the OOS holdout read (≈2026-12-15). N-floor report (report-only):
75-79 +172%/80-84 +84%/puts +300% SAFE; 95+/85-94 REVIEW vs the STALE v46-era
floor table = inherited (v73 ADDS supply at every tier vs v72), the same
stale-calibration class as the v71/v72 ships. Supply row: v73 total 45.7/day
cov 0.978 (put-band dominated). Stage-3 retune chip spawned (task_0bca24c2).
