# Staging-Native Scoring Experiments

This folder is the agent-facing home for the new scoring refinement loop.
Individual experiment folders can still live under `experiments/<candidate>/`,
but they should follow this contract when the candidate might ship.

## Default Flow

1. Start from an isolated algorithm-refinement worktree or branch.
2. Edit the real staging scoring path in that checkout, usually
   `database/utils/scoring.py`, `strategy_config.SCORING`, or scoring helpers.
3. Run no-write simulations or sweeps against the checkout path:

   ```bash
   python trader.py simulate --compare --assess
   python experiments/<candidate>/run_sweep.py
   ```

4. Store bulky artifacts under `.cache/algorithm_versions/<candidate>/` or a
   candidate-specific `.cache/<experiment>/` folder. Keep tracked summaries near
   the experiment or in the eventual algorithm silo.
5. If W1-W6 evidence passes, snapshot the candidate:

   ```bash
   python trader.py algorithm snapshot-staging
   ```

6. Elevation, `ALGORITHM_VERSION` bump, `trader recalculate`, and `trader update`
   happen only from the designated ship checkout.

## Runner Expectations

- Prefer `ScoreSimulator(scoring_fn=None)` so the simulator imports the current
  checkout scoring path.
- Experiment runners may build feature caches, enumerate candidate parameters,
  run Bayesian/LHS loops, and rank outputs.
- Do not keep the candidate formula only inside the experiment runner once it is
  a serious ship candidate. Move it into staging scoring first, then rerun the
  evidence.
- Legacy `ScoreSimulator(scoring_fn=variant_fn)` and runtime monkey patches are
  acceptable for quick probes. Treat them as draft tools, not final validation.
- Do not call `trader update` or `trader recalculate` from ordinary experiment
  worktrees because they write shared production score rows.
