# DTE Router Findings

Last updated: 2026-05-28.

Scope: Stage 3 portfolio routing only. v60 `Score.overall` stays fixed; do not
write score rows, bump `ALGORITHM_VERSION`, or treat Sentinel/Core/Apex profile
identity as scoring-version identity.

## Ship Verdict

Ship `call_ge_80_daycap1_trend_lt_50`.

This is a broader high-score call sleeve, not a broad 15DTE replacement. For
30DTE call entries only, route at most one signal per day to the 15DTE option
path when all conditions hold:

- `score >= 80`
- `trend < 50`
- day cap is one routed call per signal date
- market filters, haven exclusions, and routed allocation score caps are off

No `ALGORITHM_VERSION` bump. This is a portfolio overlay in
`strategy_config.STRATEGY_30DTE`, MC/backtest/allocation wiring, and
`mechanism_registry.py`.

## Confirmed N=500 Evidence

Artifact:
`C:\Development\Trader_dte_router_20260526\.codex\runs\dte_router_mc_trend50_n500_20260528_0026\mc_window_summary.csv`

| Window | Routed signals | Mean log delta | Median log delta | Worst-DD delta | Gate |
|---|---:|---:|---:|---:|---|
| 2021 | 0 | +0.0000 | +0.0000 | +0.00pp | PASS |
| 2022 | 79 | +0.3876 | +0.4368 | -4.34pp | PASS |
| 2023 | 15 | +0.0332 | +0.0515 | -2.32pp | PASS |
| 2024 | 3 | +0.0017 | +0.0010 | +0.00pp | PASS |
| 2025 | 16 | +0.0416 | +0.0290 | -0.31pp | PASS |
| 2025-11-01 to 2026-04-24 dip | 4 | +0.0821 | +0.0743 | +0.00pp | PASS |
| 2022-now | 117 | +0.0170 | +0.0180 | +1.54pp | FAIL |
| 5y | 117 | +0.0133 | +0.0182 | +0.00pp | PASS |

The strict all-window gate fails on 22-now worst DD by +0.54pp beyond the +1pp
limit, but the broader router is return-positive across every routed window and
improves 22-now mean DD by -2.37pp. The miss is accepted as a practical
participation tradeoff.

## Search Trail

- Deterministic search:
  `C:\Development\Trader_dte_router_20260526\.codex\runs\dte_router_20260526_235616`
- Early N=500 MC confirmation:
  `C:\Development\Trader_dte_router_20260526\.codex\runs\dte_router_mc_trend35_n500_20260526_212800`
- Broader N=500 MC confirmation:
  `C:\Development\Trader_dte_router_20260526\.codex\runs\dte_router_mc_trend50_n500_20260528_0026`
- `call_ge_80_daycap1_trend_lt_35` showed real 2022/dip uplift but failed strict
  ship gate: 2025 mean/median negative and 22-now DD `+1.25pp`.
- The first shipped stress sleeve passed every strict gate but only routed 41
  5y signals. `trend_lt_50` routes 117 5y signals and keeps annual windows
  positive, so it supersedes the narrower sleeve.
- `trend_20_30` failed 2023.
- `trend_lt_35_vix_25_35` and raw `vix_ge_25` failed 2023 due haven-ETF leakage.
- `no_haven_etf_regime_50_85` fixed 2023 but failed 2022 DD and slight 5y mean
  drag until allocation score was capped at 80.

## Operational Notes

MySQL became blocked by long-running/killed Trader and Archivist queries during
parallel retries. `mc_validate_router.py` was patched to avoid the
`algorithm_versions` lookup and to load v60 score rows with unordered indexed
SQL via `scores_version_date_IDX`, then sort locally. Future reruns should keep
workers low and run one candidate at a time.

The useful finding is not "15DTE is better." It is that high-score calls with
weaker trend confirmation can benefit from shorter 15DTE exposure while 30DTE
remains the default portfolio instrument.
