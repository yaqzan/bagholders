# Algorithm Snapshot Index

Generated from `AlgorithmVersion` rows and git-history snapshots.
No score rows, assessment rows, parquet caches, or experiment datasets are backfilled here.

| Version | Status | Commit | Category | Scoring Snapshot | Diff vs Prior | Message |
|---|---:|---|---|---:|---:|---|
| v74 | shipped | f9fb7b93 | scoring_algorithm | yes | 4 | v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST) |
| v73 | shipped | 07e9722b | scoring_algorithm | yes | 5 | Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation) |
| v72 | shipped | fc567120 | scoring_algorithm | yes | 8 | Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth) |
| v71 | shipped | 04044b21 | scoring_algorithm | yes | 14 | v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements |
| v70 | shipped | c70d16d2 | scoring_algorithm | yes | 4 | v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated) |
| v69 | shipped | 8b59206c | scoring_algorithm | yes | 11 | scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout) |
| v68 | missing | bb6251c1 | scoring_algorithm | yes | 9 | VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave |
| v67 | missing | e85282f5 | scoring_algorithm | yes | 11 | VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump) |
| v66 | ship_candidate | 05d75b4a | scoring_algorithm | yes | 11 | v66 scoring: apply weekly momentum envelope to v60 |
| v65 | shipped | 14a5981c | scoring_algorithm | yes | 10 | v65 scoring: unify weekly partial context |
| v64 | ship_candidate | 1bba5f96 | db_linked_snapshot | yes | 5 | Fix v64 recalc signal sigma map |
| v63 | shipped | 7b263922 | scoring_algorithm | yes | 2 | v63 scoring: add BB location taper candidate |
| v62 | shipped | d4d63798 | scoring_algorithm | yes | 9 | Snapshot v62 MACD put wave candidate |
| v61 | shipped | e6fbdbde | scoring_algorithm | yes | 7 | v61 scoring: add weekly mature call guard |
| v60 | shipped | d4a3e9fe | db_linked_snapshot | yes | 10 | Ship v60 r054 SCW and DD call cap candidate |
| v59 | shipped | 4fd7ffa9 | scoring_algorithm | yes | 8 | v59 scoring: daily volume authority wave |
| v58 | shipped | 3cfc4dc2 | scoring_algorithm | yes | 2 | Retune continuation echo weights for v57 WR7 utility |
| v57 | shipped | e568b2f4 | scoring_algorithm | yes | 4 | Ship direct Market Wave score transform |
| v56 | shipped | c6f384ab | scoring_algorithm | yes | 5 | Ship Market Wave dual-wave score dampener |
| v55 | shipped | bfad76a | scoring_algorithm | yes | 8 | Ship sector breadth seed099 score dampener |
| v54 | shipped | 8af574b | scoring_algorithm | yes | 9 | v54 scoring: Sector ETF breadth crash/recovery dampener |
| v53 | shipped | e3ed806 | scoring_context | yes | 2 | v53: Fix temporal echo recalc priors |
| v52 | shipped | f66bf9b | scoring_algorithm | yes | 11 | v52 scoring: temporal echo wave scoring |
| v50 | shipped | b0c1954 | scoring_algorithm | yes | 5 | v50 scoring: conservative stoch conviction wave |
| v48 | shipped | 61561ee | scoring_algorithm | yes | 11 | v48 scoring: add stoch conviction wave |
| v46 | shipped | f274eb6 | scoring_algorithm | yes | 8 | v46 scoring: WVD-Wave score-stage inverted-U modulator on weekly volume force1 |
| v45 | shipped | 56eb1f8 | scoring_algorithm | yes | 5 | Filter ETFs from production breadth universe |
| v44 | shipped | d8024b9 | scoring_algorithm | yes | 4 | v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3) |
| v43 | shipped | e083032 | scoring_algorithm | yes | 8 | feat: MCD score-stage mcap dampener (mcap-aware call confidence weight) |
| v42 | shipped | 5e6e3d3 | scoring_algorithm | yes | 7 | P5: flip WEEKLY_MODE default to 'rolling' — rolling weekly composite is now active |
| v41 | shipped | 917659c | scoring_algorithm | yes | 0 | v40 scoring: SVD (Score Velocity Dampener) |
| v40 | shipped | 917659c | scoring_algorithm | yes | 4 | v40 scoring: SVD (Score Velocity Dampener) |
| v39 | shipped | 200f33a | scoring_algorithm | yes | 4 | v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT |
| v38 | shipped | b093e2d | scoring_algorithm | yes | 9 | v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75 |
| v37 | shipped | 6f9afda | scoring_algorithm | yes | 4 | v37 scoring: post-crash put dampener (PCD) |
| v36 | shipped | d5ef1f5 | scoring_algorithm | yes | 5 | v36 scoring: CSWC re-calibration (K 0.30→0.50, wg 12→14) |
| v35 | shipped | e77714f | scoring_algorithm | yes | 10 | v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores |
| v34 | shipped | 232a725 | scoring_algorithm | yes | 3 | v34 scoring: call stoch-weekly contradiction dampener (CSWC) |
| v33 | shipped | 28fa522 | scoring_algorithm | yes | 10 | v33 scoring: continuation boost — elevate 70-74 calls with prior-winner support to 75 |
| v32 | shipped | 43eecea | scoring_algorithm | yes | 10 | v32 scoring: call-side WCF-mirror dampener (Priority #4 close) |
| v31 | shipped | f3ec7c1 | scoring_algorithm | yes | 4 | Strict-future earnings semantics + precomputed effective_date column |
| v30 | shipped | 9a9da33 | scoring_algorithm | yes | 8 | AMC-aware earnings effective_date + [low,high] sampling refactor |
| v29 | shipped | 8473cba | scoring_algorithm | yes | 2 | v29: earnings volume suppression — V6 log-gradient (pre-only, W=2, M=1.0) |
| v28 | shipped | e3c8678 | scoring_algorithm | yes | 8 | v28: earnings meta-score boost (WR-calibrated, log-smoothed) |
| v27 | shipped | ad02704 | scoring_algorithm | yes | 9 | Weekly-confirmation floor lift on extreme puts (Priority #13) |
| v26 | shipped | 18c3e70 | scoring_algorithm | yes | 4 | EARN_SUPP_PUT: drop puts in [16,20] within 5 trd days of earnings |
| v25 | shipped | 9463f02 | db_linked_snapshot | yes | 7 | Mis-stress call softener: recover compressed call alpha on narrow-bull misclass days |
| v24 | shipped | 41fa566 | scoring_algorithm | yes | 8 | Fix volume amplifier earnings suppression: call_time-aware semantics |
| v23 | shipped | be057ce | scoring_algorithm | yes | 5 | Ship v23: floored-regime x_conf gate (Priority #8 counter-trend capture) |
| v22 | shipped | 41784e0 | scoring_revert | yes | 2 | Add v22 X-confidence gate on trend dominance (REVERTED) |
| v21 | shipped | aba4f5d | scoring_algorithm | yes | 5 | Add ext-focal gradient dampener for puts above EMA50 |
| v20 | shipped | 66af13d | scoring_algorithm | yes | 7 | Add capitulation gradient dampener to scoring |
| v19 | shipped | 6656daa | scoring_algorithm | yes | 7 | Ship JA4: asymmetric 25% SPY_wk blend for put regime multiplier |
| v18 | shipped | 17caf99 | scoring_algorithm | yes | 8 | Asymmetric MACD gate for puts |
| v17 | shipped | ea8b9fe | scoring_algorithm | yes | 5 | Remove momentum confirmation gradient; cross-version volume seed fallback |
| v15 | shipped | 83851db | scoring_algorithm | yes | 2 | Fix zero-score clustering from asymmetric volume amplification |
| v14 | shipped | 410a055 | scoring_revert | yes | 9 | Revert directional volume fix (83851db) due to bucket-level regression |
| v13 | shipped | 8988474 | scoring_algorithm | yes | 5 | Some dashboard touch up, bug fix,new algoirthm |
| v12 | shipped | edf6bd1 | scoring_algorithm | yes | 7 | 26.04.09 - Scoring: zero RSI for deeply bearish base signals (put calibration fix) |
| v11 | shipped | d93ff2d | scoring_algorithm | yes | 6 | 26.04.09 - Regime: gradient VIX scoring + dynamic breadth weighting |
| v10 | shipped | 07bf8c4 | mixed_or_ui | no | 0 | 26.04.07 - Dashboard update |
| v9 | shipped | 9c8cb86 | db_linked_snapshot | yes | 6 | 26.04.07 - Fix vol amplification over-firing in three scenarios |
| v8 | shipped | a1a32a2 | scoring_algorithm | yes | 0 | 26.04.07 - entry_filter v1: OOS-validated LOW (+20%) and HIGH (+6.5%) gates |
| v7 | shipped | f1553a1 | db_linked_snapshot | yes | 2 | recalculate cleanup |
| v6 | shipped | 061362d | db_linked_snapshot | yes | 5 | merge conflict |
| v5 | shipped | a8cfc75 | scoring_algorithm | yes | 10 | 26.04.06 - Lower max w_trend - Cap trend weight at 28 isntead of 35 |
| v3 | shipped | b1cc55c | db_linked_snapshot | yes | 3 | Systemic backfill |
| v2 | shipped | c1f8fcf | scoring_algorithm | yes | 0 | added algorithm tracker and score assessment logic |
| v1 | legacy_metadata_only | baseline | legacy_metadata_only | no | 0 | Pre-versioning baseline |
