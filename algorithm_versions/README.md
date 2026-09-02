# Algorithm Version Silos

This directory stores tracked manifests and source snapshots for shipped or
candidate scoring algorithms. Large datasets and experiment caches belong under
`.cache/algorithm_versions/<key>/`, with checksums and commands recorded in the
tracked `evidence_manifest.json`.

Flat keys are intentional:

- `vNN/` is a shipped or DB-linked algorithm snapshot.
- `candidates/<name>/` can be introduced later for non-DB snapshots if needed.
- `portfolio/*.json` stores portfolio-only parameter snapshots.

Runtime note: current production update/recalc paths still import the checkout
code. The silo loader/injected multi-version scoring path is a later phase after
snapshot parity is proven.

Scoring refinements should happen in isolated algorithm-refinement worktrees by
default. In those sandboxes, the real scoring path is staging code and may be
edited directly for simulations/sweeps; snapshot candidates with `trader
algorithm snapshot-staging` before elevation.

Simulation sweeps should be staging-native by default. Prefer `trader simulate`,
`ScoreSimulator(scoring_fn=None)`, or experiment runners that import checkout
scoring code. External `scoring_fn` variants and monkey patches are quick-probe
tools only; migrate winners into staging scoring before W1-W6 validation and
snapshotting.

Use `trader algorithm snapshot-staging` for dirty or candidate worktrees. Use
`trader algorithm snapshot-git-ref --version vNN` for shipped versions; it copies
the commit recorded on the `AlgorithmVersion` row instead of the current
checkout.

Use `trader algorithm backfill-git-snapshots --force` to reconstruct all
available `vNN/` code snapshots from git history. This only writes source
snapshots, manifests, and diff indexes; score-row recalculation, assessment
backfills, and cache/dataset hydration are separate off-hours work.

`trader update --score-versions v57,v58` runs the normal update once, then
writes additional daily `Score` rows for the requested versions during the same
per-symbol scoring step. `score_calculation_service.py` owns this scoring
boundary. It reuses the fresh price pull, indicators, and current component
scores; each requested version uses its siloed `compute_overall_score`.

`trader algorithm document-snapshots` regenerates `VERSION_GUIDE.md`, the
compact Claude/Codex index, and every `vNN/README.md`.

## Post-Recalc Research Packs

After a version recalc and `trader assess --force --dte 30 --version vNN`, build
the daily evidence pack with:

```bash
python tools/build_research_pack.py --version v58
```

The pack is written to `.cache/algorithm_versions/vNN/research_pack/` and
includes score coverage, assessment coverage, 5y WR15 utility, horizon utility,
temporal backtest stats, named stress-window readiness, a parquet manifest, and
a top-level `manifest.json`. The named stress windows currently include the
March 2020 crash setup (`2020-01-01` to `2020-04-30`), the 2020-2021 crash-plus-
bull cycle, `2020-now`, and the existing `2022-now` gate.

To compute deterministic portfolio metrics for those windows in the pack, use:

```bash
python tools/build_research_pack.py --version v58 --run-portfolio-windows
```

For weekend retro hydration, inspect missing assessment rows without mutating
scores:

```bash
python tools/backfill_research_packs.py --min-version 39 --max-version 58
```

Only add `--run-assess-missing` when the version score recalc is already done
and the goal is to populate missing persisted WR/TP assessment rows before pack
generation. Use `--force-assess` after score rows were backfilled or otherwise
changed, because existing assessment rows can be present but stale. Add
`--run-portfolio-windows` when the weekend pass should compute deterministic
stress-window portfolio metrics after readiness checks pass. For weekend runs,
add `--detach` so the harness writes `pid.txt`, `run.log`, `status.json`, and
terminal `done.json` / `failed.json` artifacts. This script intentionally never
recalculates scores.
