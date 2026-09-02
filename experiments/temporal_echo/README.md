# Temporal Echo Experiment

Experiment-only harness for testing score "echo" effects across multiple
assessment horizons.

The goal is to measure whether prior same-side signals that won or failed at
short and long horizons improve the utility of current scores. Unlike the
older continuation-boost pass, this ranks candidates by utility: extra top-tier
capacity is allowed to trade off against small win-rate dilution when expected
winners and portfolio usefulness improve.

No production scoring code is modified here.

## Outputs

Run artifacts are written under `.cache/temporal_echo/runs/<timestamp>/`.

- `status.json`: current phase/progress.
- `run.log`: unbuffered pipeline output.
- `done.json` / `failed.json`: terminal state.
- `signals_v*_*.parquet`: current signal rows plus current outcomes.
- `priors_v*_*.parquet`: `(current signal, prior signal)` echo pairs.
- `transition_stats.md`: fine-grain continuation statistics.
- `sweep_results.json`: utility-ranked formula variants.

## Typical Run

```powershell
python -u experiments/temporal_echo/run_pipeline.py --lookback-days 1825 --variants 120
```

For long runs, launch it detached and redirect output to the run directory. The
pipeline itself maintains `status.json` and terminal artifacts.

## Method

1. Build score rows for the active algorithm version, using `weight_info.pre_boost`
   as the raw no-cascade score when available.
2. Join generic WR outcomes for W1/W3/W5/W7/W15/W30/W60/W90 and option-aligned
   W15 TP outcomes.
3. Build same-side prior pairs by symbol over configurable lookback gaps.
4. Summarize transition probabilities such as `P(current W15 win | prior W7 win)`.
5. Sweep smooth echo formulas and rank by top-signal utility, not pure WR lift.

The sweep reads raw/pre-boost prior scores so a shipped version can avoid the
recursive feedback loop where boosted scores create their own future priors.
