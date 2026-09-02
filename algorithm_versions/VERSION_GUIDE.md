# Algorithm Version Guide

Agent-facing index for shipped algorithm silos. Each `vNN/README.md` explains the intended change, source hints, code delta, structured variable delta, and runtime-loadability.

Coverage: git-history code snapshots are backfilled. Score rows, assessment rows, parquet caches, and experiment datasets are not backfilled here.

| Version | Commit | Category | Runtime | Structured | Intent |
|---|---|---|---:|---:|---|
| [v74](v74/README.md) | `f9fb7b93` | scoring_algorithm | yes | yes | v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST) |
| [v73](v73/README.md) | `07e9722b` | scoring_algorithm | yes | yes | Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation) |
| [v72](v72/README.md) | `fc567120` | scoring_algorithm | yes | yes | Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth) |
| [v71](v71/README.md) | `04044b21` | scoring_algorithm | yes | yes | v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements |
| [v70](v70/README.md) | `c70d16d2` | scoring_algorithm | yes | yes | v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated) |
| [v69](v69/README.md) | `8b59206c` | scoring_algorithm | yes | yes | scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout) |
| [v68](v68/README.md) | `bb6251c1` | scoring_algorithm | no | yes | VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave |
| [v67](v67/README.md) | `e85282f5` | scoring_algorithm | no | yes | VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump) |
| [v66](v66/README.md) | `05d75b4a` | scoring_algorithm | yes | yes | v66 scoring: apply weekly momentum envelope to v60 |
| [v65](v65/README.md) | `14a5981c` | scoring_algorithm | yes | yes | v65 scoring: unify weekly partial context |
| [v64](v64/README.md) | `1bba5f96` | db_linked_snapshot | yes | yes | Fix v64 recalc signal sigma map |
| [v63](v63/README.md) | `7b263922` | scoring_algorithm | yes | yes | v63 scoring: add BB location taper candidate |
| [v62](v62/README.md) | `d4d63798` | scoring_algorithm | yes | yes | Snapshot v62 MACD put wave candidate |
| [v61](v61/README.md) | `e6fbdbde` | scoring_algorithm | yes | yes | v61 scoring: add weekly mature call guard |
| [v60](v60/README.md) | `d4a3e9fe` | db_linked_snapshot | yes | yes | Ship v60 r054 SCW and DD call cap candidate |
| [v59](v59/README.md) | `4fd7ffa9` | scoring_algorithm | yes | yes | v59 scoring: daily volume authority wave |
| [v58](v58/README.md) | `3cfc4dc2` | scoring_algorithm | yes | yes | Retune continuation echo weights for v57 WR7 utility |
| [v57](v57/README.md) | `e568b2f4` | scoring_algorithm | yes | yes | Ship direct Market Wave score transform |
| [v56](v56/README.md) | `c6f384ab` | scoring_algorithm | yes | yes | Ship Market Wave dual-wave score dampener |
| [v55](v55/README.md) | `bfad76a` | scoring_algorithm | yes | yes | Ship sector breadth seed099 score dampener |
| [v54](v54/README.md) | `8af574b` | scoring_algorithm | yes | yes | v54 scoring: Sector ETF breadth crash/recovery dampener |
| [v53](v53/README.md) | `e3ed806` | scoring_context | yes | yes | v53: Fix temporal echo recalc priors |
| [v52](v52/README.md) | `f66bf9b` | scoring_algorithm | yes | yes | v52 scoring: temporal echo wave scoring |
| [v50](v50/README.md) | `b0c1954` | scoring_algorithm | yes | yes | v50 scoring: conservative stoch conviction wave |
| [v48](v48/README.md) | `61561ee` | scoring_algorithm | yes | yes | v48 scoring: add stoch conviction wave |
| [v46](v46/README.md) | `f274eb6` | scoring_algorithm | yes | yes | v46 scoring: WVD-Wave score-stage inverted-U modulator on weekly volume force1 |
| [v45](v45/README.md) | `56eb1f8` | scoring_algorithm | yes | yes | Filter ETFs from production breadth universe |
| [v44](v44/README.md) | `d8024b9` | scoring_algorithm | yes | yes | v44 scoring: ICH Ichimoku Kijun-sen state dampener (Phase H Rank #3) |
| [v43](v43/README.md) | `e083032` | scoring_algorithm | yes | yes | feat: MCD score-stage mcap dampener (mcap-aware call confidence weight) |
| [v42](v42/README.md) | `5e6e3d3` | scoring_algorithm | yes | yes | P5: flip WEEKLY_MODE default to 'rolling' - rolling weekly composite is now active |
| [v41](v41/README.md) | `917659c` | scoring_algorithm | yes | yes | v40 scoring: SVD (Score Velocity Dampener) |
| [v40](v40/README.md) | `917659c` | scoring_algorithm | yes | yes | v40 scoring: SVD (Score Velocity Dampener) |
| [v39](v39/README.md) | `200f33a` | scoring_algorithm | yes | yes | v39 scoring: PESS (Put Earnings Score Suppression) + retire EARN_SUPP_PUT |
| [v38](v38/README.md) | `b093e2d` | scoring_algorithm | yes | yes | v38 scoring: Call Weak-Weekly Dampener (CWWD) extends CWCF below 75 |
| [v37](v37/README.md) | `6f9afda` | scoring_algorithm | yes | yes | v37 scoring: post-crash put dampener (PCD) |
| [v36](v36/README.md) | `d5ef1f5` | scoring_algorithm | yes | yes | v36 scoring: CSWC re-calibration (K 0.30->0.50, wg 12->14) |
| [v35](v35/README.md) | `e77714f` | scoring_algorithm | yes | yes | v35 scoring: EARN_BOOST recalibration on v34 pre-boost scores |
| [v34](v34/README.md) | `232a725` | scoring_algorithm | yes | yes | v34 scoring: call stoch-weekly contradiction dampener (CSWC) |
| [v33](v33/README.md) | `28fa522` | scoring_algorithm | yes | yes | v33 scoring: continuation boost - elevate 70-74 calls with prior-winner support to 75 |
| [v32](v32/README.md) | `43eecea` | scoring_algorithm | yes | yes | v32 scoring: call-side WCF-mirror dampener (Priority #4 close) |
| [v31](v31/README.md) | `f3ec7c1` | scoring_algorithm | no | no | Strict-future earnings semantics + precomputed effective_date column |
| [v30](v30/README.md) | `9a9da33` | scoring_algorithm | no | no | AMC-aware earnings effective_date + [low,high] sampling refactor |
| [v29](v29/README.md) | `8473cba` | scoring_algorithm | no | no | v29: earnings volume suppression - V6 log-gradient (pre-only, W=2, M=1.0) |
| [v28](v28/README.md) | `e3c8678` | scoring_algorithm | no | no | v28: earnings meta-score boost (WR-calibrated, log-smoothed) |
| [v27](v27/README.md) | `ad02704` | scoring_algorithm | no | no | Weekly-confirmation floor lift on extreme puts (Priority #13) |
| [v26](v26/README.md) | `18c3e70` | scoring_algorithm | no | no | EARN_SUPP_PUT: drop puts in [16,20] within 5 trd days of earnings |
| [v25](v25/README.md) | `9463f02` | db_linked_snapshot | no | no | Mis-stress call softener: recover compressed call alpha on narrow-bull misclass days |
| [v24](v24/README.md) | `41fa566` | scoring_algorithm | no | no | Fix volume amplifier earnings suppression: call_time-aware semantics |
| [v23](v23/README.md) | `be057ce` | scoring_algorithm | no | no | Ship v23: floored-regime x_conf gate (Priority #8 counter-trend capture) |
| [v22](v22/README.md) | `41784e0` | scoring_revert | no | no | Add v22 X-confidence gate on trend dominance (REVERTED) |
| [v21](v21/README.md) | `aba4f5d` | scoring_algorithm | no | no | Add ext-focal gradient dampener for puts above EMA50 |
| [v20](v20/README.md) | `66af13d` | scoring_algorithm | no | no | Add capitulation gradient dampener to scoring |
| [v19](v19/README.md) | `6656daa` | scoring_algorithm | no | no | Ship JA4: asymmetric 25% SPY_wk blend for put regime multiplier |
| [v18](v18/README.md) | `17caf99` | scoring_algorithm | no | no | Asymmetric MACD gate for puts |
| [v17](v17/README.md) | `ea8b9fe` | scoring_algorithm | no | no | Remove momentum confirmation gradient; cross-version volume seed fallback |
| [v15](v15/README.md) | `83851db` | scoring_algorithm | no | no | Fix zero-score clustering from asymmetric volume amplification |
| [v14](v14/README.md) | `410a055` | scoring_revert | no | no | Revert directional volume fix (83851db) due to bucket-level regression |
| [v13](v13/README.md) | `8988474` | scoring_algorithm | no | no | Some dashboard touch up, bug fix,new algoirthm |
| [v12](v12/README.md) | `edf6bd1` | scoring_algorithm | no | no | 26.04.09 - Scoring: zero RSI for deeply bearish base signals (put calibration fix) |
| [v11](v11/README.md) | `d93ff2d` | scoring_algorithm | no | no | 26.04.09 - Regime: gradient VIX scoring + dynamic breadth weighting |
| [v10](v10/README.md) | `07bf8c4` | mixed_or_ui | no | no | 26.04.07 - Dashboard update |
| [v9](v9/README.md) | `9c8cb86` | db_linked_snapshot | no | no | 26.04.07 - Fix vol amplification over-firing in three scenarios |
| [v8](v8/README.md) | `a1a32a2` | scoring_algorithm | no | no | 26.04.07 - entry_filter v1: OOS-validated LOW (+20%) and HIGH (+6.5%) gates |
| [v7](v7/README.md) | `f1553a1` | db_linked_snapshot | no | no | recalculate cleanup |
| [v6](v6/README.md) | `061362d` | db_linked_snapshot | no | no | merge conflict |
| [v5](v5/README.md) | `a8cfc75` | scoring_algorithm | no | no | 26.04.06 - Lower max w_trend - Cap trend weight at 28 isntead of 35 |
| [v3](v3/README.md) | `b1cc55c` | db_linked_snapshot | no | no | Systemic backfill |
| [v2](v2/README.md) | `c1f8fcf` | scoring_algorithm | no | no | added algorithm tracker and score assessment logic |
| [v1](v1/README.md) | `baseline` | legacy_metadata_only | no | no | Pre-versioning baseline |

## Fast Access

- Latest shipped snapshot: [v74](v74/README.md)
- Active/main scoring snapshot: [v74](v74/README.md)
- Machine index: [snapshot_index.json](snapshot_index.json)
- Compact snapshot table: [SNAPSHOT_INDEX.md](SNAPSHOT_INDEX.md)
- Runtime scoring loader: no additional cadence versions pending (active `v74` is already the latest shipped snapshot).
