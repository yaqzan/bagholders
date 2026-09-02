# FINDINGS — Residual mining on honest labels

**STATUS: COMPLETE 2026-08. 24 of 891 pre-enumerated hypotheses survive all six locked
bars → CANDIDATES (each needs its own Stage-1 prereg; nothing ships from here).**
Prereg `df5ccf6d`; driver `driver/mine_residuals.py`; full 891×42 results table
`out/residual_mining_results.csv`; hypothesis enumeration logged pre-outcome
(`out/hypothesis_enumeration.csv`). Population: 26,891 ripe rows, EMBARGO enforced
(971 rows date>2026-06-15 dropped before analysis). Deterministic (hash-verified rerun).

## Funnel

891 → effect≥3pp: 156 → BH-FDR 0.05 (m=891): joint 71 → era-thirds: 71 → survivor: 62
→ **L3 same-sign: 25 (the dominant filter)** → exposure-matched control: **24**.

## The methodological headline (bigger than any single candidate)

**~60% of discriminators that pass effect+FDR+era+survivor bars FLIP SIGN between
simulated L2 EV and realized L3 P&L** (e.g. +4.55pp sim → −6.19pp real). Without the
gold-label bar, this pass would have declared 62 candidates; 37 of them are simulation
artifacts. Promoted to traps.md: never promote a discriminator on simulated EV alone.

## Candidate structure (top rows; full precision in CSV)

- The strongest big-N candidate family is **strong-regime × quality/counter-trend**:
  regime_composite-Q5 × trend-Q1 (+5.61pp, N=1,782, q=1e-13, L3 +3.37) — the CT
  mechanism's signature REDISCOVERED INDEPENDENTLY by the mining, from data alone.
  Siblings: regime-Q5 × {rsi-Q5, bb-Q3, technical_alignment-Q5, macd-Q5} (+3.8-5.4pp,
  L3 up to +23pp); trend-Q1 × wadj_partial-Q2 (+3.97, L3 +28.8).
- One single-feature survivor: regime_composite Q5 alone (+3.72pp, N=5,377, L3 +1.42).
- 7 NEGATIVE candidates (avoid-cohorts), led by regime-Q1 × alignment-Q1 (−3.33, L3
  same-sign) — the mirror image: weak regime + weak alignment is where the book bleeds.
- Two tiny-N survivors (liquidity-t5 × rsi, N=15/17) cleared every bar incl. the L3
  floor but sit at the reliability edge — flagged, weight accordingly.

## Disposition

1. Candidates are LOCKED as a list (this commit) — their first OOS read is the December
  virgin window, added to the scorecard as a Tier-2 belief-update row.
2. Next architect task (`.horizon` board): consolidation review — cluster the 24 (most
  are one regime×quality family), select ≤3 clusters for Stage-1 preregs, park the rest.
3. Delisted-feature hypotheses are structurally unable to pass bar 4 (by construction) —
  2 correctly killed; noted for future enumerations.
