# OSK Era-Layer — Stage-1 Interim Verdict (FABLE, 2026-07-08)

**Status: CONDITIONAL — B4 is the sole surviving candidate, and it survives only if same-day skew
is available to the actionable (close-time) score.** No faithful-path compute is licensed until the
pipeline-ordering question below is answered.

## Evidence state
- 6-variant sweep (stage1_results.json): all 12 computable cells promoted>demoted on pnl15 (sign
  universally right). Aggressive variants (A2/B2) REJECTED — supply growth with blended-quality
  dilution (the anti-selectivity pattern v73/v74 punish). B4 (tanh cap=5 scale=1.5): supply-neutral,
  gap +0.52, t_clust 2.51 — best-of-6, honest post-selection strength ~t1.9. B3 = conservative
  fallback, quality-tilted, weak t.
- Lag-robustness (lag_robustness.json): skew day-over-day r=0.55 (NOT sticky). Under lag-1:
  B4 blended quality −60% (+0.0182→+0.0072), net migration flips +2→−7, promoted cohort falls below
  the N=30 floor (gap/t uncomputable); B3 −72%. Attrition 10% (89.5% skew coverage at D-1).
  Caveat: lag test ran on the thin corroboration panel only (the Polygon panel holds signal-day rows
  only) — low power, but direction consistent across variants and mechanistically explained by the
  autocorrelation.

## The deciding question — RESOLVED 2026-07-08: PROCEED (scout, file:line evidence in transcript)
- Current scheduled pipeline (post_market_daily.ps1:101/105): `trader update` (options OFF) runs
  BEFORE `pull-options` ⇒ today's close scores cannot see today's chains AS-IS.
- BUT the phases are explicitly independent ("the options pull needs nothing from the score update",
  ps1:82-85) ⇒ **reordering = a two-line Invoke-Phase swap**, and legacy `close-update` already
  interleaves per-symbol options-before-score (trader.py:1509-1530), proving data-layer safety.
- Portfolio engine's REAL fills consume the post-close FINAL score row for day D
  (portfolio_engine.py:840, completed sessions only) ⇒ with the swap, the actionable score sees
  same-day chains — the lag regime the evidence rejected never occurs on the surface that matters.
- **Bundled ship prerequisites (Stage-3/ops, no version bump for the swap itself):**
  (1) the two-line phase reorder (costs ~the options-pull duration in evening score latency —
  acceptable; derived tail shifts later);
  (2) modifier no-ops on intraday runs (stale chains) — close-score-only feature by construction;
  (3) documented divergence: the 15:25 provisional BUY-alert score is pre-pull and will not carry
  today's skew — alerts show the unmodified score; only the final close score is skew-aware.

## DECISION: Phase C advances to the faithful-path block (fresh session recommended).
This file + PROPOSAL.md + stage1_results.json + lag_robustness.json are the handoff brief.

## If it proceeds: faithful-path block (next session/handoff)
Wire B4 + rolling regime-guard into worktree scoring (post-weighted_sum, pre-pre_regime clamp;
no-chain ⇒ exactly neutral; intraday ⇒ neutral per above); precompute skew sidecar for the recalc
window; staging-native ScoreSimulator + diff-assess via queue; formal W1-W6 with REAL supply
(42.8%/50.0% coverage fractions); expectations set at "marginal candidate" — the gates decide.

## FINAL — 2026-07-08: **BLOCK** (faithful path, FAITHFUL_PATH_RESULTS.md).
Reconstruction bit-exact; WR15-primary null (t -0.27/-0.32); pnl15-secondary sign-REVERSED vs the
naive sweep (-0.41, t -2.19 at 70+cum) under our-recipe skew — boundary-local signal not
recipe-robust. B3 no rescue. Worktree closed as NULL/BLOCKED; evidence preserved here in MAIN.
OSK stays a per-trade residual only; next legitimate surface = Stage-3 allocation tilt (own gate).
