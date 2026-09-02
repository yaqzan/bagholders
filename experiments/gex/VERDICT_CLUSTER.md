# GEX Phase 2 — Cluster-Escape Dynamics: VERDICT MEMO

**Date:** 2026-07-07 · **Owner:** FABLE · **Pre-registration:** DESIGN_CLUSTER.md (+ amendment), locked
before any cluster feature existed. Parent: VERDICT.md (phase 1, per-name linear GEX = NULL).

## Verdicts (registered claims)
| Claim | Result | VERDICT |
|---|---|---|
| **Track B — per-name escape → fast-track (speed)** | Primary label (reached +1σ within 15cd): esc_above tc **+0.64**, gap_room_up tc −0.78, local_density tc −1.55; sub-period signs flip on the primary label (pooled and constant-coverage); within-escape gap regression (purest fast-track test, N=3,242) all dead (tc ≤ 1.82) | **NULL — one-shot spent. Per-name GEX closed PERMANENTLY, conditional forms included.** |
| **Track A1 — SPY-chain GEX as DD lever** | 138 quality-gated days, no crash regime in window; fwd_vol tc −1.5/−1.9 (right sign, under bar), fwd_dd tc +0.70 + halves flip; quintile materiality unmeasurable at N | **FAIL (coverage-limited)** |
| **Track A2 — cross-sectional GEX as DD lever** | fwd_dd_10d tc +1.76 + halves FLIP; fwd_vol dead (tc ≤0.85); mkt_neg_share cohort cell halves-flip | **FAIL** |
| **Market-level GEX DD-lever lead** (A1 AND A2 failed) | — | **CLOSED at current data** |

## Mechanism texture worth keeping (secondary labels, Track B — diagnostic, NOT stageable)
- **Overhead-gamma pinning is real:** local_density → mfe15 tc **−4.70** (β −0.052σ): dense gamma at
  spot caps upside excursion. gap_room_up INVERTED vs fast-track (more "runway" above → LESS upside,
  tc −3.92; MORE downside, tc +3.58): far-below-the-wall names are laggards, not coiled springs —
  the anti-magnetism/momentum-echo reading again.
- **Escape ≠ acceleration; escape ≈ support:** esc_above → mae15 tc −2.92 (less adverse excursion),
  mfe15 tc +2.57, speed dead. Escaped names don't run faster; they draw down less (cluster below = support).
- All under the ≥3+stability bar and/or on secondary labels; recorded as physics texture only.

## The one robust off-registration observation (recorded, NOT staged, NOT verified by panel)
`mkt_gex_ratio` (daily mean gex_ratio of the 70+ signal cohort, ≥30 signals/day) →
`cohort_net_path` (date-mean 15cd underlying path of that day's signals):
**tc +3.13 week-clustered / +2.66 under 3-week-block clustering** (verify_overlap_clustering.py —
blocks ≥ the 15cd label-overlap horizon; 21 effective clusters), halves +/+, β ≈ +0.19σ, controlling
VIX, VIXΔ5, McClellan, TRIN, breadth-EMA.
**Why it is NOT actionable:** (1) money-dead — same series on cohort_pnl15: tc **+0.81** (the effect
does not survive option-P&L transmission; same lesson as pure momentum losing through the engine);
(2) off-registration (found while testing a different claim); (3) single-regime 1.3y window,
composition-conditioned on the momentum cohort; (4) 21 effective clusters is thin.
It becomes interesting ONLY if a future, independently-motivated mechanism converts cohort-level
underlying-path timing into option P&L — and entry-timing is itself a closed axis. Treat as context,
not as a lead.

## Closures now in force
1. **Per-name / cross-sectional dealer-GEX scoring features: closed permanently** (phase 1 linear +
   phase 2 conditional/one-shot both spent).
2. **Market-level GEX DD-lever: closed at current data.** Honest re-open condition: deep index-option
   history spanning ≥1 crash regime (the Polygon options-tier buy in data-acquisition.md unblocks
   exactly this — SPY's own chain history here starts 2025-06-25).
3. OOS re-read (~2026-07-29, phase-1 gex_ratio cohort): moot unless (1) is somehow overturned; do not spend it.

## Artifacts
DESIGN_CLUSTER.md (pre-reg + amendment) · MATH_SPEC_CLUSTERS.md (locked segmentation, 12 refs) ·
dealer_gex.py (36-col, A+C selftests green, old columns bit-identical) · gex_cluster_test.py /
spy_gex_test.py / verify_overlap_clustering.py · gex_cluster_results.{txt,json} ·
spy_gex_results.{txt,json} · caches: gex_features.parquet (36-col), spy_gex_chain/features, control parquets.
