# Daily Opportunity Allocation Research

Stage 3 research for dynamic per-day allocation based on opportunity supply,
regime state, breadth/sector breadth, open-book pressure, and portfolio outcome
velocity.

This folder is experiment-only. It does not write score rows, recalculate old
versions, change `strategy_config.py`, or bump `ALGORITHM_VERSION`.

Canonical cross-agent alpha ledgers now live at `alpha_mining/` in the repo
root. Use `alpha_mining/NEW_LEADS.md` for broad ranked leads and
`alpha_mining/MISS_CANDIDATES.md` for miss-led score candidates. This folder's
`ALPHA_LEDGER.md` remains the historical output of the daily-opportunity
research pipeline.

## Research Shape

1. Build a daily state table from active-version score rows, deterministic
   cascade replay, MarketBreadth, and MarketRegime.
2. Separate post-score opportunity supply from `weight_info.pre_boost` and
   `weight_info.pre_regime` so score processing and regime/post-processing are
   visible.
3. Correlate daily opportunity N/demand with market regime factors and portfolio
   outcomes.
4. Use the ML probe only for interaction discovery. Any candidate allocation
   wave still needs Stage 3 N=500x8 validation before it can ship.
5. Sweep daily allocation policies at the fill point to test whether opportunity
   N can drive allocation, MaxPos, and call/put spread without changing scores.

## Commands

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/run_pipeline.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/sweep_daily_policies.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir>
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/sweep_total_demand_params.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir>
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/validate_total_demand_mc.py --out-dir <run-dir> --iterations 80
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/mine_conditional_alpha.py --daily <run-dir>/daily_state.csv --out-dir <run-dir>
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/reverse_engineer_drawdowns.py --daily <run-dir>/daily_state.csv --out-dir <run-dir>
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/bayes_alpha_sweep.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir> --budget 160 --candidate-pool 12000
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/validate_bayes_alpha_mc.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir> --n 80 --seed-offset 0
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/mine_usable_policy_cases.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir>
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/validate_usable_policy_mc.py --daily <run-dir>/daily_state.csv --version-id <id> --out-dir <run-dir> --n 240 --windows 2020-crash,2024 --policies baseline,<policy> --seed-offset 0
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/daily_opportunity_allocation/analyze_smooth_put_wave_path_diff.py --daily <daily-state.csv> --candidate-csv <smooth-controller-ranked.csv> --out-dir <run-dir> --version-id <id> --n 240 --windows 2024,2025 --policies <smooth-policy>
```

Outputs:

- `daily_state.csv`
- `daily_state_meta.json`
- `opportunity_regime_correlations.csv`
- `opportunity_outcome_correlations.csv`
- `opportunity_cohorts.csv`
- `ml_probe.json`
- `FINDINGS.md`
- `daily_policy_sweep.csv`
- `daily_policy_summary.md`
- `focused_total_demand_sweep.csv`
- `focused_total_demand_ranked.csv`
- `focused_total_demand_summary.md`
- `total_demand_mc.csv`
- `total_demand_mc_ranked.csv`
- `total_demand_mc_summary.md`
- `conditional_alpha_ranked.csv`
- `conditional_alpha_ledger.json`
- `conditional_alpha_summary.md`
- `drawdown_episodes.csv`
- `drawdown_signature_ranked.csv`
- `drawdown_pair_rules.csv`
- `drawdown_signature_summary.md`
- `bayes_alpha_evals.jsonl`
- `bayes_alpha_ranked.csv`
- `bayes_alpha_summary.md`
- `bayes_alpha_best.json`
- `bayes_alpha_mc.csv`
- `bayes_alpha_mc_ranked.csv`
- `bayes_alpha_mc_summary.md`
- `usable_policy_cases_ranked.csv`
- `usable_policy_cases_summary.md`
- `usable_policy_mc.csv`
- `usable_policy_mc_ranked.csv`
- `usable_policy_mc_summary.md`
- `smooth_put_wave_path_diff_summary.md`
- `smooth_put_wave_trade_delta_summary.csv`
- `smooth_put_wave_trade_delta_by_date.csv`
- `smooth_put_wave_trade_delta_by_month.csv`
- `ALPHA_LEDGER.md`

## Current Read

The v59 discovery run found useful alpha surfaces in daily opportunity N,
especially call-demand quality, put-book pressure, and call sector
concentration. The corrected deterministic sweep and N=80 MC screen did not
promote the first total-demand allocation wave: it helped 2022 drawdown but was
unstable across 2020-crash, 2024, 2025, and long-window return surfaces.

Next research should use N as a conditional exposure input, not as a direct
global budget expander. The current alpha ledger favors throttle/re-entry
families: shrink exposure when recent execution is weak and opportunity supply
or put overhang is hostile; allow selective call-side re-entry when the book is
in drawdown but call demand remains healthy.

The drawdown reverse-engineering pass adds a separate Market Wave divergence
branch: throttle when market-wave state looks constructive but recent realized
entries are failing.

The Bayesian merged-alpha sweep found one deterministic RC seed out of 160
evaluations. The seed combines weak-execution throttles, sparse-demand and
Market Wave divergence risk, put caps, a small MaxPos cut, and capped call-side
re-entry.

The N=80 MC validation rejected that seed. It improved several long-window DD
surfaces, but worsened 2020-crash and 2024 worst DD. There is no ship candidate
yet; the next search should keep the long-window DD reduction while directly
penalizing stochastic 2024 and crash-window DD regressions.

Follow-up ablations added stable label hashing for reproducible MC screens.
No-slot, severe-DD-only, and panic-breadth-only guards did not produce a real
release candidate. Panic-breadth-only preserved 2024 only by becoming effectively
no-op, so this merged allocation-controller family should not be promoted.

The first usable mined case is narrower: when `open_put_n>=7` and
`prev5_entry_avg_pnl_pct<=0.07378`, scale puts to 70% and cap open puts at 5.
This `put_overhang_pnl__puts70_cap5` case is a DD hedge candidate, not a ship.
Stable N=240 MC improved 2024, 22-now, and 5y worst-DD surfaces, but showed
material 2024 return drag and a slight 2025 worst-DD regression.

The v60 active-row refresh supersedes v59 artifacts for promotion evidence.
The current best portfolio-stage lead is the Market Wave divergence put-side
throttle. A hard `skip_puts` probe showed alpha, but it violates the preferred
wave-shaped design. The smooth refit scales put exposure continuously from
recent weak execution, constructive Market Wave, and put-overhang pressure.
It fixed the hard-cliff 2024 DD failure, but no smooth row is ship-worthy yet:
`wave_put_divergence_0051` failed 2025 worst DD, while the stricter
`wave_put_divergence_0066` fixed 2025 but retained 2024 return drag.

The stricter `0066`-neighborhood follow-up found two 2024-stable smooth
survivors, `wave_put_divergence_strict_0298` and
`wave_put_divergence_strict_0127`, but survivor N=240 MC still rejected both.
`strict_0298` fixed 2024 return/DD but dragged 2025 return and mean DD;
`strict_0127` worsened 2025 worst DD by +3.11pp.

The `strict_0298` path-diff shows the 2025 blocker is not the same-key put leg.
Shared put trade delta was positive in both 2024 and 2025. The
failure is replacement/path churn: freed buying power changed later fills,
especially late October through December 2025, and moved the worst-DD episode
into late October / November.

Next research should not broaden this put-wave branch without a gross
exposure/cash-reserve guard. The smooth form is viable, but the current mined
surface is no-ship because the side throttle recycles too much exposure into
later path churn.

The cash-reserve follow-up fixed that blocker. The reserve branch holds the
premium not spent on throttled puts until that trade's original exit date,
preventing freed buying power from creating late-path churn. Experiment-local
MC first pointed at `0051`, but production-equivalent MC promoted the smoother
`wave_put_divergence_reserve_0124` shape instead.

`0124` passed N=500 production-equivalent MC on active v60 rows: `2020-crash`
and `2024` worst DD were unchanged, `2025` worst DD improved by -4.93pp,
`22-now` worst DD improved by -5.27pp, 5y worst DD was unchanged, mean/median
log-return deltas were positive on every non-no-op window, and collapse stayed
0%. This is the current portfolio-stage promotion candidate.

Do not treat this as shipped. The next step is a live portfolio-only
implementation of reserved idle cash, followed by normal Stage 3 ship
validation. No scoring change, score recalc, or `ALGORITHM_VERSION` bump is
allowed for this family.
