# Sector Wave DD Ledger Findings

Stage A ledger evidence for portfolio-scale sector ETF alpha refinement.

## Inputs

- Ledger: `experiments\sector_etf_alpha\dd_probe\sector_wave_ledger.parquet`
- Windows: 2022, 2024, 22-now, 5y.
- Variants: BASELINE and V4 sector ETF alpha.
- Metric: worst-episode loss share divided by normal premium share inside each side/window.

## Side Loss Share

| variant | window | side | worst_loss_share |
| --- | --- | --- | --- |
| BASELINE | 2022 | call | 21.9% |
| BASELINE | 2022 | put | 78.1% |
| BASELINE | 2024 | call | 51.4% |
| BASELINE | 2024 | put | 48.6% |
| BASELINE | 22-now | call | 51.5% |
| BASELINE | 22-now | put | 48.5% |
| BASELINE | 5y | call | 26.6% |
| BASELINE | 5y | put | 73.4% |
| V4 | 2022 | call | 8.6% |
| V4 | 2022 | put | 91.4% |
| V4 | 2024 | call | 29.0% |
| V4 | 2024 | put | 71.0% |
| V4 | 22-now | call | 28.3% |
| V4 | 22-now | put | 71.7% |
| V4 | 5y | call | 29.1% |
| V4 | 5y | put | 70.9% |

## V4 PUT Sector Exposure Shape

| window | open_side_sector_exp_bin | n_worst_loss_trades | premium_share | loss_share | lift_vs_premium | avg_entry_dd |
| --- | --- | --- | --- | --- | --- | --- |
| 2022 | exp_10_20 | 452 | 21.2% | 26.1% | 1.23 | 6.9% |
| 2022 | exp_20_35 | 151 | 5.7% | 4.6% | 0.80 | 7.5% |
| 2022 | exp_35_50 | 48 | 0.9% | 0.3% | 0.32 | 7.5% |
| 2022 | exp_ge50 | 8 | 0.1% | 0.0% | 0.63 | 16.7% |
| 2022 | exp_lt10 | 1153 | 72.1% | 69.0% | 0.96 | 7.7% |
| 2024 | exp_10_20 | 410 | 21.7% | 22.4% | 1.03 | 5.7% |
| 2024 | exp_20_35 | 154 | 5.4% | 0.4% | 0.06 | 7.8% |
| 2024 | exp_35_50 | 8 | 0.0% | 0.0% | 0.08 | 11.2% |
| 2024 | exp_ge50 | 0 | 0.0% | 0.0% | 0.00 | 11.0% |
| 2024 | exp_lt10 | 1908 | 72.9% | 77.2% | 1.06 | 5.8% |
| 22-now | exp_10_20 | 1299 | 9.6% | 24.4% | 2.55 | 8.5% |
| 22-now | exp_20_35 | 484 | 0.2% | 2.1% | 9.24 | 9.2% |
| 22-now | exp_35_50 | 44 | 0.1% | 0.6% | 8.42 | 12.2% |
| 22-now | exp_ge50 | 1 | 0.0% | 0.0% | 0.00 | 14.7% |
| 22-now | exp_lt10 | 4005 | 90.1% | 72.8% | 0.81 | 8.1% |
| 5y | exp_10_20 | 1340 | 12.2% | 24.8% | 2.02 | 8.6% |
| 5y | exp_20_35 | 498 | 0.2% | 1.8% | 8.81 | 9.2% |
| 5y | exp_35_50 | 66 | 0.1% | 0.8% | 16.13 | 12.5% |
| 5y | exp_ge50 | 8 | 0.0% | 0.0% | 0.00 | 15.0% |
| 5y | exp_lt10 | 4235 | 87.5% | 72.5% | 0.83 | 8.0% |

## Highest V4 Phase Cells

| window | side | sector_phase_bin | open_side_sector_exp_bin | n_worst_loss_trades | premium_share | loss_share | lift_vs_premium |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5y | call | phase_deep_overheated | exp_lt10 | 74 | 1.4% | 22.0% | 16.22 |
| 22-now | put | phase_mid | exp_20_35 | 347 | 0.0% | 0.5% | 13.78 |
| 22-now | put | phase_mid | exp_10_20 | 806 | 1.4% | 16.6% | 11.70 |
| 5y | put | phase_overheated | exp_20_35 | 105 | 0.2% | 1.7% | 10.84 |
| 5y | put | phase_mid | exp_10_20 | 803 | 1.4% | 14.2% | 10.11 |
| 2022 | call | phase_deep_oversold | exp_20_35 | 191 | 0.0% | 0.0% | 8.82 |
| 22-now | put | phase_overheated | exp_20_35 | 111 | 0.2% | 1.6% | 8.55 |
| 22-now | call | phase_deep_overheated | exp_lt10 | 81 | 3.0% | 18.4% | 6.20 |
| 22-now | call | phase_deep_overheated | exp_10_20 | 50 | 1.7% | 10.2% | 5.83 |
| 2024 | put | phase_deep_overheated | exp_lt10 | 266 | 10.2% | 32.4% | 3.19 |
| 22-now | call | phase_overheated | exp_lt10 | 122 | 6.9% | 21.5% | 3.12 |
| 5y | put | phase_deep_overheated | exp_10_20 | 89 | 0.3% | 0.8% | 3.09 |
| 2022 | put | phase_oversold | exp_10_20 | 108 | 4.9% | 12.2% | 2.49 |
| 5y | put | phase_overheated | exp_10_20 | 308 | 4.2% | 9.6% | 2.27 |
| 5y | put | phase_mid | exp_20_35 | 378 | 0.0% | 0.1% | 2.19 |
| 5y | call | phase_overheated | exp_lt10 | 129 | 8.9% | 17.7% | 1.98 |

## Highest V4 RSI Cells

| window | side | sector_rsi_bin | open_side_sector_exp_bin | n_worst_loss_trades | premium_share | loss_share | lift_vs_premium |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 22-now | put | rsi_45_55 | exp_10_20 | 565 | 1.3% | 16.2% | 12.32 |
| 5y | put | rsi_55_70 | exp_20_35 | 117 | 0.2% | 1.8% | 11.13 |
| 5y | put | rsi_45_55 | exp_10_20 | 561 | 1.3% | 14.1% | 10.58 |
| 22-now | put | rsi_55_70 | exp_20_35 | 126 | 0.2% | 1.7% | 9.38 |
| 5y | put | rsi_gt70 | exp_lt10 | 583 | 2.2% | 16.0% | 7.33 |
| 22-now | put | rsi_45_55 | exp_20_35 | 253 | 0.0% | 0.2% | 6.73 |
| 2022 | call | rsi_lt30 | exp_20_35 | 109 | 0.0% | 0.0% | 6.37 |
| 2024 | call | rsi_55_70 | exp_10_20 | 52 | 3.2% | 17.5% | 5.38 |
| 5y | call | rsi_55_70 | exp_10_20 | 70 | 2.3% | 12.3% | 5.28 |
| 22-now | call | rsi_55_70 | exp_10_20 | 65 | 3.9% | 13.0% | 3.35 |
| 5y | call | rsi_45_55 | exp_lt10 | 322 | 14.5% | 47.7% | 3.30 |
| 2024 | call | rsi_gt70 | exp_lt10 | 82 | 9.4% | 28.9% | 3.07 |
| 22-now | put | rsi_55_70 | exp_10_20 | 435 | 2.9% | 7.2% | 2.50 |
| 2024 | put | rsi_55_70 | exp_lt10 | 782 | 21.9% | 51.3% | 2.34 |
| 5y | put | rsi_55_70 | exp_10_20 | 439 | 4.5% | 10.4% | 2.33 |
| 2022 | put | rsi_30_45 | exp_10_20 | 134 | 7.0% | 14.7% | 2.12 |

## Highest V4 Stock-vs-Sector Divergence Cells

| window | side | stock_rs_abs_bin | open_side_sector_exp_bin | n_worst_loss_trades | premium_share | loss_share | lift_vs_premium |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5y | put | rs_moderate | exp_20_35 | 164 | 0.1% | 1.7% | 14.07 |
| 22-now | put | rs_moderate | exp_20_35 | 138 | 0.1% | 1.5% | 11.56 |
| 22-now | put | rs_lockstep | exp_20_35 | 123 | 0.1% | 0.4% | 6.87 |
| 2024 | call | rs_moderate | exp_10_20 | 72 | 2.8% | 17.5% | 6.18 |
| 22-now | put | rs_lockstep | exp_10_20 | 527 | 3.8% | 19.4% | 5.13 |
| 22-now | put | rs_extreme | exp_20_35 | 223 | 0.0% | 0.2% | 5.08 |
| 5y | put | rs_lockstep | exp_10_20 | 490 | 3.8% | 18.7% | 4.88 |
| 2024 | call | rs_moderate | exp_lt10 | 156 | 12.8% | 43.0% | 3.37 |
| 5y | put | rs_extreme | exp_20_35 | 206 | 0.0% | 0.0% | 2.81 |
| 22-now | call | rs_moderate | exp_10_20 | 77 | 4.9% | 13.0% | 2.64 |
| 5y | call | rs_lockstep | exp_lt10 | 359 | 19.4% | 49.5% | 2.56 |
| 2022 | call | rs_moderate | exp_lt10 | 425 | 22.0% | 55.2% | 2.50 |
| 5y | call | rs_moderate | exp_10_20 | 79 | 5.5% | 12.3% | 2.24 |
| 5y | put | rs_extreme | exp_10_20 | 597 | 2.7% | 5.8% | 2.13 |
| 22-now | put | rs_extreme | exp_10_20 | 510 | 1.9% | 3.9% | 2.09 |
| 22-now | call | rs_lockstep | exp_lt10 | 411 | 21.5% | 44.1% | 2.05 |

## Sector Diagnostic Cells

| window | side | sector | open_side_sector_exp_bin | n_worst_loss_trades | premium_share | loss_share | lift_vs_premium |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2022 | call | healthcare | exp_lt10 | 202 | 10.1% | 51.8% | 5.15 |
| 2022 | call | technology | exp_lt10 | 233 | 17.5% | 30.4% | 1.74 |
| 2024 | call | energy | exp_lt10 | 55 | 14.9% | 26.8% | 1.80 |
| 2024 | put | consumer-cyclical | exp_lt10 | 272 | 4.2% | 26.1% | 6.18 |
| 2024 | put | technology | exp_lt10 | 257 | 16.6% | 26.0% | 1.57 |
| 22-now | call | consumer-cyclical | exp_lt10 | 106 | 7.3% | 24.3% | 3.31 |
| 5y | call | technology | exp_lt10 | 159 | 17.2% | 20.7% | 1.20 |
| 5y | call | consumer-cyclical | exp_lt10 | 108 | 6.5% | 20.6% | 3.18 |
| 22-now | call | technology | exp_lt10 | 154 | 13.9% | 18.3% | 1.32 |
| 2024 | put | technology | exp_10_20 | 138 | 14.3% | 18.1% | 1.27 |
| 5y | put | healthcare | exp_lt10 | 580 | 13.8% | 17.5% | 1.27 |
| 2024 | call | technology | exp_10_20 | 60 | 4.8% | 17.0% | 3.53 |
| 22-now | call | consumer-defensive | exp_lt10 | 92 | 2.3% | 17.0% | 7.48 |
| 5y | call | basic-materials | exp_lt10 | 64 | 3.3% | 16.5% | 5.05 |
| 22-now | put | healthcare | exp_lt10 | 571 | 12.0% | 16.4% | 1.37 |
| 5y | call | consumer-defensive | exp_lt10 | 91 | 3.0% | 15.4% | 5.05 |

## Bayesian Replay Screen

Added a first-order tape replay estimator:

```bash
python experiments/sector_etf_alpha/estimate_sector_wave_alloc.py
```

Output:

- `experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_estimates.csv`

This is not full MC. It keeps the same fills and exits from the original V4 tape,
rescales premium per trade, then recomputes portfolio path and DD. It is a fast
Bayesian filter for deciding which allocation curves deserve a true MC runner.

| Window | V4 replay WorstDD | Best replay curve | Best WorstDD | Readout |
|---|---:|---|---:|---|
| 2022 | 89.9% | SWA_BOTH_SOFT | 84.9% | PUT-only haircuts worsened DD |
| 2024 | 64.2% | SWA_BOTH_SOFT | 58.0% | PUT-only haircuts worsened DD |
| 22-now | 83.7% | V4_REPLAY | 83.7% | all tested sector cuts worsened DD |
| 5y | 81.7% | SWA_PUT_RSI45_SOFT / SWA_PUT_PHASEMID_SOFT | 75.9% | PUT wave-conditioned cuts helped |

Key result: the worst-episode losses are put-heavy, but a naive PUT-only sector
allocation haircut is not robust. It worsened 2022, 2024, and 22-now replay DD.
That implies the PUT exposure is not simply "bad risk"; it is also part of the
convex recovery/hedge structure. Cutting only the side that appears in the loss
ledger can steal the recovery wave.

The more promising law is **sector gross crowding**, not a fixed sector cap and
not a PUT-only cap. `SWA_BOTH_SOFT` improved 2022, 2024, and 5y replay DD, but
still failed 22-now, so it is not a ship candidate. It is the first true-MC
candidate family.

## True MC Experiment

Build `SECTOR_WAVE_ALLOC` as a portfolio-stage experiment-local MC variant:

- First family after replay: `SWA_BOTH_SOFT`, scaling new allocation by gross sector crowding.
- Use a smooth exposure ramp, not a sector blacklist: start near 1.0 below 10% open side-sector premium, fade progressively above 20-35%, and saturate above 50%.
- Modulate the fade by sector wave state only if Phase/RSI cells show consistent lift across 2022, 2024, 22-now, and 5y.
- Keep sector names diagnostic only; the sector top cells rotate by window.

Refined candidates after replay:

1. `SWA_BOTH_SOFT`: both sides, same-sector exposure curve. This is the leading candidate.
2. `SWA_GROSS_SOFT`: gross sector exposure across calls+puts, applied symmetrically to new allocation.
3. `SWA_PUT_PHASEMID_SOFT`: put-side only, but only when sector phase is mid/overheated. This is 5y-positive but not robust.
4. Kill/avoid `SWA_PUT_STRONG`: worsened replay DD in every window except marginal 5y.

## True MC N=120 Screen

Added experiment-local true MC runner:

```bash
python experiments/sector_etf_alpha/sweep_sector_wave_alloc_mc.py \
  --windows 2022,2024,22-now,5y \
  --curves V4_BASE,SWA_BOTH_SOFT,SWA_GROSS_SOFT \
  --n 120

python experiments/sector_etf_alpha/sweep_sector_wave_alloc_mc.py \
  --windows 2022,2024,22-now,5y \
  --curves SWA_GROSS_BALANCED \
  --n 120 \
  --out experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_mc_balanced.csv

python experiments/sector_etf_alpha/sweep_sector_wave_alloc_mc.py \
  --windows 2022,2024,22-now,5y \
  --curves SWA_GROSS_PHASEMID_SOFT,SWA_GROSS_RSI45_SOFT \
  --n 120 \
  --out experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_mc_wavegated.csv
```

Combined outputs:

- `experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_mc_all.csv`
- `experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_mc_all_deltas.csv`

The runner patches only the experiment process:

- V4 score patch at signal load.
- `monte_carlo.run_single_sim` patched in-process so scaling happens at actual fill time.
- `MC_NO_MP=1`, `MC_NO_DB_PERSIST=1`, `REALLOC_STRATEGY=''`.
- No production scoring or portfolio code changed.

WorstDD deltas vs V4 baseline:

| Curve | 2022 | 2024 | 22-now | 5y | Readout |
|---|---:|---:|---:|---:|---|
| `SWA_GROSS_SOFT` | -1.04pp | -1.09pp | +1.30pp | -1.44pp | best, but fails 22-now |
| `SWA_GROSS_RSI45_SOFT` | -0.41pp | +0.70pp | +1.22pp | -0.47pp | wave gate weakens edge |
| `SWA_GROSS_PHASEMID_SOFT` | +0.89pp | +0.70pp | +0.75pp | -0.97pp | not robust |
| `SWA_BOTH_SOFT` | +0.36pp | +3.78pp | +1.25pp | -0.03pp | kill |
| `SWA_GROSS_BALANCED` | +1.44pp | +11.50pp | +1.09pp | -0.22pp | kill |

Baseline V4 WorstDD at this seed set:

| Window | V4 WorstDD | V4 MeanDD |
|---|---:|---:|
| 2022 | 65.12% | 47.25% |
| 2024 | 53.79% | 34.53% |
| 22-now | 67.19% | 61.71% |
| 5y | 69.41% | 61.65% |

### True MC Readout

Still **NO SHIP**.

The law is real but incomplete:

- Gross sector crowding is a better axis than side-specific PUT crowding.
- `SWA_GROSS_SOFT` improved WorstDD in 3 of 4 windows and improved average DD
  in 3 of 4 windows, but the 22-now tail got worse by +1.30pp.
- Wave-gating by sector phase/RSI did not fix the 22-now failure; it mostly
  diluted the good 2022/2024/5y effect.
- Balanced/milder curve was worse than soft, so the problem is not simply
  "over-cutting."

Bayesian posterior after this screen:

1. Sector ETF alpha is real per-trade.
2. DD failure is portfolio-scale and sector-crowding related.
3. The useful state variable is gross sector crowding, not fixed sector name and
   not PUT-only allocation.
4. A standalone sector allocation wave is not enough yet; it must interact with
   the broader portfolio drawdown/SAW state or with entry order/slot pressure.

Next experiment should isolate the 22-now failure specifically:

- Build a true-MC trade tape for `V4_BASE` vs `SWA_GROSS_SOFT` on `22-now`.
- Compare worst-episode side/sector/exposure attribution after the allocation
  change.
- Test a combined law: `SWA_GROSS_SOFT` active only when portfolio DD is below a
  shallow/mid band, or with a recovery override that disables sector cuts after
  portfolio DD is already elevated.

## Production vs Raw V4 Check

The key ship question is not whether V4 breaches 70% DD against itself. It is
whether raw V4 improves the currently active production portfolio enough to
justify a scoring ship.

Added:

```bash
python experiments/sector_etf_alpha/compare_prod_v4_mc.py \
  --windows 2022,2024,22-now,5y \
  --variants PROD,V4 \
  --n 120
```

Corrected outputs:

- `experiments/sector_etf_alpha/dd_probe/prod_vs_v4_mc.csv`
- `experiments/sector_etf_alpha/dd_probe/prod_vs_v4_mc_deltas.csv`

Note: the first run of this helper had a patch-assignment bug where V4 was
identical to PROD. The helper was fixed, V4 was rerun, and the primary CSVs were
overwritten with the corrected production+V4 comparison.

V4 deltas vs production:

| Window | dMeanRet | dWorstDD | dMeanDD | dCalls | dPuts |
|---|---:|---:|---:|---:|---:|
| 2022 | +1.15Mpp | -1.70pp | -2.27pp | +4.4 | +6.6 |
| 2024 | -20.11Bpp | -6.38pp | +1.30pp | -65.6 | +67.3 |
| 22-now | -5.01e25pp | -6.44pp | -2.22pp | -96.5 | +142.1 |
| 5y | -2.26e32pp | +0.48pp | -1.15pp | -137.1 | +149.3 |

Raw V4 readout:

- It **does** reduce production worst DD in 2022, 2024, and 22-now.
- It brings production 22-now WorstDD from 73.63% to 67.19%.
- It does **not** solve 5y tail DD; 5y worsens from 68.93% to 69.41%.
- It loses too much compound/velocity in 2024 and 5y.
- Mechanically, it rotates the book: fewer calls and more puts in the long
  windows.

This answers the "why not ship if it did not breach 70%" challenge:

**Raw V4 is closer than the earlier no-ship framing implied, but still not a
ship candidate.** It is a DD reducer, not a tradeoff winner. The issue is not
catastrophic DD breach; the issue is the cost of the DD reduction and the 5y
tail regression.

Next refinement should preserve the V4 DD reduction while restoring call-side
velocity. The highest-probability direction is not sector allocation alone; it
is a score-stage variant that keeps V4's put/DD benefit but avoids demoting or
displacing too many productive calls.
