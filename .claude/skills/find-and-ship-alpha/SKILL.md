---
name: find-and-ship-alpha
description: End-to-end "find and ship a new scoring version" loop — recon the version history + ship gate, derive a gate-aligned Stage-1 hypothesis, build an option-augmented holdout-locked ledger, mine discriminators on OPTION-TP, sweep via the real growth gate with REAL supply, and ship if it's a clean SHIP. Use when the user wants to discover scoring alpha and elevate a new active ALGORITHM_VERSION autonomously.
---

# /find-and-ship-alpha — discover and ship a new scoring version

A workflow-oriented loop to find Stage-1 scoring alpha and (if it clears the
honest ship gate) elevate it to the active `ALGORITHM_VERSION`. It bakes in the
hard-won traps below — **read the GUARDS first; they are why most prior attempts
(v42, v58, v61-v68, the weekly-transition-resolver) failed.**

Active baseline to confirm first: `python trader.py algorithm active`. Work in an
isolated `algo-exp/<name>` worktree per CLAUDE.md if you will edit scoring code;
the ledger/sweep below are staging-native (no DB writes) and can run on the
serving checkout.

## GUARDS (the traps that killed prior attempts)

1. **Optimize on OPTION-TP, not cumulative WR15.** The growth gate's PRIMARY
   barrier is `tp` (30dte_opt). VCBW (v67/v68) optimized cumulative 75+ WR15 and
   nulled. A promote into 75-79 is accretive iff its optTP beats the **75-79 band
   marginal (~0.61)**, NOT the cumulative 75+ pool (~0.81). The 75-79 band is ~42%
   of the filled book — improving it directly moves ebar.
2. **ALWAYS compute REAL supply before trusting the gate.** A missing candidate
   supply row makes the gate use `FALLBACK_COVERAGE=0.92` (rosy) → a FALSE SHIP
   for N-cutting candidates (the false v63/BBLT SHIP). The gate now neutralizes +
   refuses to auto-SHIP on approximated supply, but still run:
   `python experiments/version_scorecard/signal_supply.py --versions v60,<cand>`.
   See [[reference-growth-gate-supply-fallback]].
3. **Weekly features in the RECALC are LOOK-AHEAD.** `WeeklyScore` is keyed on the
   CURRENT week (`date - weekday()`) and stored as the COMPLETE Mon-Fri bar, so a
   recalc of a mid-week historical signal sees future bars. Any cohort mined on
   `w_mom`/`w_comp`/`w_bias`/`w_adj` (or kijun/wv_force1) from recalc'd rows is
   contaminated. **Test:** split the cohort's optTP by day-of-week — if Monday ≫
   Friday (Friday ≈ baseline), it's look-ahead, not alpha. See
   [[reference-weekly-recalc-lookahead]].
4. **The gate CANNOT certify a look-ahead FIX.** It measures on contaminated
   recalc rows, so an honest (look-ahead-free) candidate looks WORSE and BLOCKs.
   Validate look-ahead fixes / stability ships / leak removals on the formal
   **Stage 1-N neutrality track** (assessment-backtest.md, added 2026-06-11):
   N1 zero-tradable-diff sim A/B + N2 bit-exactness + N3 the value metric —
   the v69/v71/v72 class is first-class now, not ad-hoc.
5. **Hard <80 ceiling** on any boundary controller — leaking above 80 degrades
   80+ WR15 (the reverted v61 `eval13333` trap).
6. **Holdout lock.** Gate every ledger/sweep on `CALIBRATION_CUTOFF_DATE` via
   `experiments/_holdout.assert_no_holdout_leak`. RE-LOCKED 2026-06-11 at
   `2026-06-15` (was None 06-04→06-11) — the lock is LIVE again.
7. **A clean SHIP is rare and strong.** Real ships MCD/ICH only earned FLAG
   (prune-only → growth-neutral). A two-sided prune+promote can reach SHIP. FLAG
   ≠ ship-blocker, but it requires the downstream portfolio-DD check (guard 8).
8. **Residual blind spot = Stage-3 portfolio DD.** The gate is MC-free and cannot
   see correlated-fill drawdown (how reverted v58 slipped). After any Stage-1
   SHIP, sanity-check 22-now / 2020-now WorstDD on a deterministic backtest before
   finalizing the bump.
9. **Gate semantics post-reform (2026-06-11) + comparison-data rot.** W2/W3 are
   CI-based (contradictions need z≥2 ∧ N≥100); W6 gradient is FLAG-only, pooled
   (95-100→90-94), and only candidate-INTRODUCED z≤−2 inversions escalate; the
   N-floor table is REPORT-ONLY; any waived gate goes in the waiver ledger
   (3 same-class strikes → fix the gate). Regression-test the gate with
   `--selftest`, NEVER `--replay` — the 2026-06 honest recalcs overwrote pre-v69
   packs, so the documented replay anchors (v40→v42 BLOCK) no longer exist on
   disk, and pre-v69 packs must not be mined as "documented history" either.

## Phase 1 — Recon (parallel, optional Workflow)

Map the landscape before hypothesizing. Read: `.claude/docs/version-history.md`,
`algorithm_versions/vNN/README.md` for recent versions, `alpha_mining/NEW_LEADS.md`
+ `MISS_CANDIDATES.md`, `experiments/version_scorecard/STAGE1_GROWTH_GATE.md`.
Retro-run the gate on prior failed candidates to confirm trustworthiness:
`for c in v61 v63 v65 v66; do python experiments/version_scorecard/stage1_growth_gate.py --baseline v60 --candidate $c; done`
(after `signal_supply.py` populates their real supply).

## Phase 2 — Hypothesis

Pick the highest-edge, point-in-time-SAFE Stage-1 cohort from MISS_CANDIDATES /
NEW_LEADS. Prefer DAILY features (current-day: stoch, trend, macd, bb, rsi,
vol_pct, scw_*, mcd_*, ich_*) — they have no look-ahead. Weekly cohorts require
the look-ahead test (guard 3) and usually a point-in-time reconstruction first.

## Phase 3 — Option-augmented ledger (the foundation)

`experiments/rqc_v60/build_ledger.py` is the template: pull active-version CALL
peaks [70,89] + `weight_info` features, run TWO forward walks
(`assess_scores._forward_walk_subset`) — generic (K=2/M=5) **and** option
(monkeypatch `A.SWING_K_LOW/M_LOW = 1.274/1.092` = `30dte_opt`) — and capture
full-universe per-date supply (75+ call / <=25 put counts) for candidate-supply
synthesis. Holdout-locked. ~13 min for 5y (two walks). The barrier_outcomes
cache only holds ~160d, so the forward walk (not the cache) is required for 5y.

## Phase 4 — Mine on OPTION-TP

`experiments/rqc_v60/mine.py`: cohort-z on `opt15` (not generic) for the promote
(70-74) and demote (75-79) cohorts vs the 75-79 marginal. **Run the dow look-ahead
test (guard 3) on any weekly discriminator.** Abandon if the only signal is
look-ahead or daily-features are weak (then the boundary is genuinely null,
consistent with VCBW).

## Phase 5 — Gate-aligned sweep (real gate, real supply)

`experiments/rqc_v60/eval.py` reuses the REAL gate functions
(`growth_windows`/`w4_check`/`verdict`) on a re-tiered ledger, building an EXACT
synthetic candidate band (barrier outcomes are score-invariant) + REAL computed
recycle_coverage (anchor-checked vs the gate's v60 0.859). `sweep.py` ranks
SHIP > FLAG > BLOCK by binding option dG. No recalc needed for triage.

## Phase 6 — Ship (only on a clean SHIP, or FLAG + portfolio-DD pass)

Migrate the winner into staging `scoring.py`, then the deploy sequence
([deploy.md](../../docs/deploy.md)): commit scoring → bump `ALGORITHM_VERSION`
(separate commit, respect the scoring-version guard) → `trader recalculate 1d` →
`trader recalculate --force` (10y `--force --full` off-hours) → `trader assess
--force` → **the three-part comparability unit**: `python
tools/build_research_pack.py --version vNN --run-portfolio-windows` +
`signal_supply.py --versions <base>,vNN` + `python
experiments/version_scorecard/portfolio_response.py --materialize vNN` (PRF
matched sizing — also the SEEDED first candidate if the new substrate's
supply/hydration shifted enough to warrant a Stage-3 retune) → real
`stage1_growth_gate.py` on the REAL pack → guard-8 portfolio-DD check →
`trader algorithm promote-candidate vNN --status shipped`. Update NEW_LEADS /
MISS_CANDIDATES / known-issues / version-history. If FLAG/BLOCK: refine (more promotion to lift
ebar; re-mine) — do not ship a FLAG without the portfolio-DD pass.

## Reusable artifacts

`experiments/rqc_v60/` (build_ledger, mine, eval, sweep, validate_fix) is the
canonical harness — copy + adapt the discriminator/mechanism per hypothesis.
