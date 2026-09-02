# Sweep cadences — staged-Bayesian methodology per stage

Overflow reference from [../SKILL.md](../SKILL.md). Load this when you're
actually planning a sweep's phase structure (variant counts, compute budget);
the main SKILL.md covers routing + gate thresholds + verdict math without it.

Default methodology across all three stages: **staged Bayesian** — LHS/Sobol
blast radius over the full space, drill into the top-quartile basin with
Bayesian search, then a dense fine-grid near the winner. A uniform grid wastes
~95% of compute in already-known-bad regions and can't scale past a handful of
parameters (5 params × 5 levels = 3,125 variants; a 6th param at 5 levels =
15,625). Total staged-Bayesian spend (~200–600 variants typically) beats a
2,000-variant uniform grid on both coverage and optimum-finding.

## Stage 1 — Scoring calibration cadence

| Phase | Approach | Variants | Compute |
|---|---|---:|---|
| A. Cohort validation | z-score of targeted feature vs neighbors; abandon if z<+3 | 1 | <1 min (parquet cache) |
| B. Blast radius | LHS/Sobol over full param space, WR15 on affected cohort | 50–150 | 5–20 min |
| C. Drill | Bayesian (Optuna) restricted to top-quartile basin from B | 100–400 | 10–30 min |
| D. Final tune | Dense fine-grid ±10% of C winner, full multi-window check | 30–80 | 5–10 min |
| E. Validate | Run W1-W6 on the D winner | 1 | <1 min |

Total compute: roughly 20 min – 1 hour for a full Stage 1 sweep. This is the
cheapest stage by far — no MC, no cache rebuild, everything reads from
existing parquet/pack data.

## Stage 2 — Barrier optimization cadence

| Phase | Approach | Variants | Compute |
|---|---|---:|---|
| A. Baseline pin | Current barrier set's option TP%/avg_option_pnl/WorstDD | 1 | ~3 min (cache-served) |
| B. Coarse Bayesian | LHS over (TP_BASE, TP_STRESS, SL_BASE, SL_STRESS, BREADTH_THRESHOLD, HOLD_DAYS) | 5–10 | 1–3 hours total (full `barrier_outcomes` rebuild + 22-now smoke MC per candidate) |
| C. Fine refinement | Top-3 candidates, ±10% Bayesian | 5–8 | 1–2 hours |
| D. Validate | Final candidate's full 5y assessment + N=300 22-now MC | 1 | 30 min |

**Why so few variants compared to Stage 1:** each candidate needs a
`barrier_outcomes` cache rebuild (~30–60 min on 30 DTE, longer on 15 DTE)
because TP/SL/HOLD/PREMIUM_MULT changes invalidate the cache's key. A full
Bayesian search here (hundreds of variants) is computationally impractical —
the coarse-then-fine cadence above is the accepted compromise. Total compute:
roughly 2.5–5.5 hours for a full Stage 2 sweep.

## Stage 3 — Tertiary portfolio cadence

| Phase | Approach | Variants | Compute |
|---|---|---:|---|
| A. Baseline pin | N=500×8 with `PYTHONHASHSEED=0..2` reruns to quantify MC noise | 1 | 3–6 hours |
| B. Bayesian sweep | LHS + Optuna over portfolio knobs at N=100×8 | 50–150 | 4–8 hours |
| C. Drill top-5 | Top-5 candidates at N=300×8 | 5 | 4–6 hours |
| D. Ship gate | Final candidate at N=500×8 | 1 | 1–2 hours |

Total compute: roughly 12–22 hours for a full Stage 3 sweep — always submit
via `trader queue submit --priority high` (see `/queue-ops`), never run raw.
Never rank a screening phase without a 2020/2020_crash window even though
it's outside the 8 T3 canonical windows.

## Preflight checklist (before launching ANY stage's sweep)

1. **List ALL knobs**, including ones you're tempted to "fix at sensible
   defaults." Ask: "if a reviewer asked why this knob is at this value, would
   I have an empirical answer or just a hunch?" A hunch belongs in the search
   space.
2. **Identify empirically-locked vs empirically-open knobs** (e.g. cascade
   tier alloc structure is usually locked; a lift function's shape or a gate
   boundary is usually open). Sweep the open ones; document why the locked
   ones are locked.
3. **Define the objective function explicitly** before launching — a
   composite with weighted sub-objectives beats a single-metric optimum.
4. **Pre-commit to the stage gate** (the exact W/B/T thresholds) before
   seeing any results — otherwise you'll rationalize the winner after the
   fact.
5. **Run on holdout-locked data.** `experiments/_holdout.py` enforces this;
   never calibrate on data past `CALIBRATION_CUTOFF_DATE` (see main
   SKILL.md's "Holdout lock" section).

## Anti-patterns

- **"I'll just grid 5 levels per param to start, then drill if needed"** — the
  5-level grid wastes compute on regions Stage-1-style LHS would have ruled
  out in 1/10 the variants.
- **"I dropped this knob from the search to keep variant count low"** — that
  knob just became a hidden assumption. Either include it (Bayesian search
  makes 7 knobs nearly as cheap as 4) or document why it's empirically
  locked.
- **"I expanded the grid by 2× and re-ran"** — switch to Bayesian at this
  point. Iterative grid expansion is a sign the wrong methodology is in use.
- **Picking the winner by visual inspection of a top-15 list** — encode the
  objective function explicitly and let it rank; eyeballing sneaks in
  confirmation bias and ignores Pareto trade-offs.

## When a uniform grid IS appropriate

- Stage 3 fine-tuning around a known optimum — once the basin is located,
  ~50 dense-grid variants cleanly nail the local optimum without Bayesian
  overhead.
- 2–3 parameter sweeps where every cell genuinely has signal (e.g.
  `metric × period × bucket` enumeration is exhaustive by intent).
- Sanity baselines — running 4–5 hand-picked variants alongside the Bayesian
  winner to prove non-Bayesian configs lose.

## Canonical sweeps to read before writing a new one

Most of the boilerplate (LHS sampler, Optuna integration, JSONL logging,
composite-objective ranking) is already solved:

| Sweep | What it shipped | Pattern |
|---|---|---|
| `experiments/v32_optim/phase_b_cascade.py` | v32_optim cascade retune | 16 Bayesian evals × N=100×8 windows for Stage 2; Stage 3 = N=300/N=500 confirmation |
| `experiments/mcap_dampener/sweep_v3_bayes.py` | v43 MCD score-stage dampener | 3-stage: v1 linear → v2 power-law → v3 dense Bayesian |
| `experiments/weekly_avwap/` (Phase H) | v44 ICH score-stage dampener | 1,170+ variants across 7 sequenced sweeps |
| `experiments/v34_calibration/` (Phase 1–3) | v35 EARN_BOOST recalibration | gradient → combined-stack → 2D fine grid |
| `experiments/concentration_2x/sweep.py` | Apex fast-2x sprint (Stage 3, portfolio-stage) | smoke(N=10) → coarse(N=100, `--step-months 3`) → drill(N=500 frontier cells, `--step-months 1`) |

Read the target stage's exemplar before writing a new sweep script — don't
reinvent the sampler or the ranking logic.
