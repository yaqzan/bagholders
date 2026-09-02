# Candidate consolidation — architect pass (2026-08)

Input: the 24 locked survivors (`out/residual_mining_results.csv`, FINDINGS). Decision:
cluster into 3, select 2 for Stage-1 preregs, park 1. Rationale recorded per cluster.
NOTE valid for all: candidate lifts are measured on the honest ledger, whose labels come
from the engine WITH all shipped mechanisms active — so every lift is INCREMENTAL to
current production behavior by construction.

## Cluster A — SELECTED: "strong-regime counter-trend/quality" (16 of 24)

regime_composite-Q5 × {trend-Q1, rsi-Q5, bb-Q3, technical_alignment-Q5, macd-Q5, ...}
+ the regime-Q5 single + trend-Q1 × wadj_partial-Q2. Big-N (744-5,377), all L3-confirmed,
q down to 1e-13. This is the CT signature found blind — convergent with the mechanism
ablation. Stage-1 hypothesis to prereg: a regime-Q5-gated entry/funding tilt toward the
deep-CT/quality cohort, defined explicitly as incremental over the shipped regime
multiplier and CT_PROMOTE (both already in the labels' baseline). FIRST EVIDENCE GATE =
the December OOS read (scorecard Tier-2 row) — the prereg is written to WAIT; no
in-sample re-testing before it.

## Cluster B — SELECTED: "weak-regime avoid-list" (7 of 24, negative lifts)

regime-Q1 × weak-alignment family (−3.3 to −5.5pp, L3 same-sign). Hypothesis: an entry
SKIP gate for the identified avoid-cohorts. Supply-cutting → the mining's bar-6
exposure-matched control already passed at cohort level, but the Stage-1 prereg must
re-run it at portfolio level (floor_control method) per the standing trap. Same December
first-gate.

## Cluster C — PARKED: liquidity-t5 × rsi cells (2 of 24)

N=15/17 — cleared every bar but sit below any honest Stage-1 N floor. Parked with a
pointer into the option-surface-features campaign (their natural home when it runs);
revisit only with more data, never alone.

## Sequencing

Both selected preregs get drafted at or after scorecard ratification (their gate
definitions cite it), evidence arrives 2026-12-15, and NOTHING pre-tests on the embargoed
window before then. If December's reads come back same-sign for a cluster, its Stage-1
proceeds through the normal W-gates toward a possible v75; if sign flips, the cluster
dies with one clean OOS bullet. Either way the answer costs nothing and cannot be
rationalized after the fact.
