# v73 Candidate Handoff — Honest Dampener Retirements (STAGED)

**Status: STAGED (not shipped).** Produced by the 2026-06-12 morning /research run
(~3h budget to the 09:30 open — Stage > rush). All evidence in `FINDINGS.md` +
`.cache/dampener_ablation_v72/`.

## What this is

One-at-a-time honest ablation of the 7 remaining pre-v69-calibrated score-stage
dampeners (WCF, CWCF, CSWC, CWWD, SCW, ICH, WVD) on the v72 substrate — the direct
continuation of the v71 integrity campaign (which retired mis_stress/JA4/MCD/wave
the same way). Verdict per mechanism below.

## Verdicts (full evidence in FINDINGS.md)

| Mech | Verdict | Decisive number |
|---|---|---|
| WCF | **RETIRE** (clear) | removals 41.3% vs shared 41.7% (z=−0.43) — deletes ~85% of put band with ZERO discrimination; v27 evidence was look-ahead artifact |
| ICH | **RETIRE** (clear) | call leg INERT (≥75 removes N=1/5y); put leg wrong-way (deletes 45.0% puts vs 41.7% shared, z=+1.65) |
| CWCF | retire-leaning, **growth gate decides** | removals 50.4% vs 54.3% (z=−1.96), 19% of potential 75+ N |
| CSWC | retire-leaning, **growth gate decides** | removals 51.2% vs 54.2% (z=−1.63), 22% of potential 75+ N |
| SCW | retire-leaning, **growth gate decides** | ≥75 removals 50.3% (z=−1.69), 14% of N; real at ≥70 (z=−2.47) but that mostly lands in dead 70-74 |
| CWWD | **KEEP** | real discriminator (z=−2.28), effect lands in zero-alloc 70-74 — free |
| WVD | **KEEP** | deleted cohort 41.6% vs 51.9% (z=−3.32) — BELOW call BE; the audit's one clear earner |

## Two ship options

**Option A — conservative honesty ship (retire WCF + ICH only).** Call-tier impact ≈ nil
(ICH = intra-band shuffle of single-digit rows; WCF is put-band only) → near-neutral on the
funded book; restores an honest put assessment surface (put-band N ×~7-8 at unchanged WR) and
removes two dead mechanisms. Gates: standard W-path; W5 will FLAG (≈neutral by construction —
document per the FLAG-teeth rule); cheap MC smoke optional (call density ~unchanged).

**Option B — growth ship (A + retire the CWCF/CSWC/SCW trio).** +61% (lower bound; bundle
union is larger) 75+ supply at pooled 50.7% optWR15 (≫ BE 45.0), blended mix −1.4pp. The v71
trade (+83% at +1.9pp) was strictly better and validated; this one is hydration-positive but
mix-negative → MUST be adjudicated by `stage1_growth_gate.py` with REAL candidate supply.
Density shift ≫30% on binding tiers → **N=300×8 MC smoke mandatory**, and expect a follow-up
Stage-3 sizing retune on the new density (the v71→retune precedent; seed it with
`portfolio_response.py --derive`).

## Exact ship procedure (for the ship agent)

1. **Bundle confirm first** (interactions matter — CWCF/CWWD/CSWC/SCW overlap on
   weak-weekly/low-stoch call cohorts): add a `bundle` entry to
   `ab_eval.py:ARM_PATCHES` with ONLY the selected retirements, run
   `EVAL_ARMS=bundle python -u experiments/dampener_ablation_v72/ab_eval.py --parallel 6`
   (queued), re-run `analyze.py` with a bundle pair. Expect the bundle delta ≈ the
   sum of singles; investigate if not.
2. **Worktree** per the algorithm-experiment trigger: `git worktree add
   ../Trader-exp-v73-retire -b algo-exp/v73-dampener-retire`.
3. Edit `strategy_config.SCORING` flip points (values confirmed 2026-06-12):
   `WCF_LIFT_K` (L409), `CWCF_DAMPEN_K` (L416), `CWWD_DAMPEN_K` (L424),
   `CSWC_DAMPEN_K` (L432), `SCW_ENABLED` (L435), `ICH_ENABLED` (L524),
   `WVD_WAVE_ENABLED` (L539) — set ONLY the retired ones to 0/False. Prefer
   retirement-by-config (v71 precedent: `MCD_ENABLED=False`, code path stays).
4. Gates: W1-W6 + growth gate with **REAL supply** (run
   `experiments/version_scorecard/signal_supply.py` for the candidate — never the
   fallback), holdout lock (CALIBRATION_CUTOFF_DATE=2026-06-15 — the ReSim window
   ends before it but verify any new sweep gates), OPTION-TP primary. If any
   binding tier's density shifts >30% (likely if SCW/ICH retire): N=300×8 MC smoke.
5. Ship per deploy.md: commit scoring → bump ALGORITHM_VERSION → recalc
   (market-hours order: `1d` then `--force`, `--force --full` off-hours) → assess →
   **three-part comparability unit** (research pack + supply row + PRF materialize)
   → silo snapshot → docs.
6. Post-ship: the live Portfolio fires a version_sweep re-qualification on the next
   `trader update` (expected); fakeout/N-floor report-only checks.

## Caveats

- ReSim lacks the continuation-echo path (all arms share the lack; arm-vs-arm
  deltas valid; baseline-vs-stored 98.5% exact).
- One-at-a-time ablation: pairwise overlap between the weak-weekly/stoch call
  dampeners is NOT measured here — that's what the bundle arm is for.
- Puts are OFF portfolio-wide: WCF / ICH-put verdicts change the assessment
  surface only, not the funded book. A put-side-only retirement is NOT worth a
  version bump on its own.
