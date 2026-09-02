# Magnitude Predictors - Initial Stock-Only Screen

Date: 2026-05-11  
Substrate: active version v50 (`b0c1954`)  
Window: 5y lookback (`1825` days), holdout-locked through `CALIBRATION_CUTOFF_DATE`  
Inputs: score rows, score components, raw indicators, market breadth/regime, stock metadata, generic stock barrier cache. No options or IV data.

## Question

Can we explain *how far* a stock tends to move after a signal, orthogonally to
the existing score-probability ladder?

Target metric: `mfe_sigma_{W}` where MFE is the favorable stock move within
W calendar days divided by the signal-date 60d realized volatility. This keeps
the target volatility-normalized and IV-agnostic.

Method: residualize MFE by `(side, score_band)`, then split each feature into
low/high terciles inside each side+band cohort. This prevents the screen from
just rediscovering that higher score buckets have higher win rates.

Artifacts:

- `.cache/magnitude_predictors/signals_v50_1825.parquet`
- `.cache/magnitude_predictors/screen_w7_1825.csv`
- `.cache/magnitude_predictors/screen_w15_1825.csv`
- `.cache/magnitude_predictors/screen_w30_1825.csv`
- `.cache/magnitude_predictors/findings_w{7,15,30}_1825.md`

## Dataset

Build command:

```bash
python -u experiments/magnitude_predictors/build_features.py 1825
```

Screen commands:

```bash
python -u experiments/magnitude_predictors/screen_magnitude.py 1825 7
python -u experiments/magnitude_predictors/screen_magnitude.py 1825 15
python -u experiments/magnitude_predictors/screen_magnitude.py 1825 30
```

Build result:

- Total signal rows: 31,909
- Rows with W15 magnitude: 28,762
- Generic barrier rows joined: 150,362

## Baseline Magnitude Anchors

W15 call MFE is already meaningfully above the generic call target in strong
bands:

| side | band | n | W15 WR | MFE p25 sigma | MFE median sigma |
|---|---:|---:|---:|---:|---:|
| call | 70-74 | 14,831 | 72.7% | 1.296 | 1.667 |
| call | 75-79 | 2,054 | 78.8% | 1.457 | 1.769 |
| call | 80-84 | 814 | 84.9% | 1.544 | 2.159 |
| put | 21-25 | 6,709 | 74.3% | 0.677 | 0.960 |
| put | 16-20 | 2,845 | 76.8% | 0.723 | 1.005 |
| put | 11-15 | 811 | 80.6% | 0.760 | 1.077 |

The current assessment table already gives a workable stock-magnitude prior:
calls are roughly 1.3-1.6 sigma at the conservative p25 anchor, puts roughly
0.7-0.8 sigma at p25.

## Findings

1. Existing data is sufficient to measure magnitude. No IV or option chain data
   is required for this first-pass magnitude head because `sigma_pct` already
   normalizes the forward move per stock.

2. Many large separators are not clean magnitude-only features. For calls,
   `volume_magnitude` and `volume_edge` show large negative residual MFE deltas
   across W7/W15/W30, but they also carry -6pp to -8pp WR deltas. That is
   probably probability-quality leakage or a bad-amplification cohort, not a
   pure "move farther after winning" signal.

3. The cleanest first-pass magnitude candidate is put-side stochastic edge.
   `stoch_edge` persists across all three horizons with small WR leakage:

| W | residual MFE delta | WR delta |
|---:|---:|---:|
| 7 | +0.217 sigma | +0.69pp |
| 15 | +0.248 sigma | +0.40pp |
| 30 | +0.272 sigma | +1.21pp |

This is the kind of feature a Stage 2 TP curve can use: it changes expected
distance more than it changes probability.

4. Call-side market backdrop has a smaller but persistent magnitude signal.
   `pct_above_ema200_mkt` improves residual call MFE by +0.16/+0.22/+0.23
   sigma at W7/W15/W30. WR leakage is small at W7/W15 and mildly negative at
   W30. This looks more like environment-dependent travel distance than raw
   score quality.

5. Earnings-boosted calls produce very large W7/W15 MFE separation, but the
   cohort is sparse and W30 fades. It should be treated as a separate
   event-magnitude branch, not the general answer.

6. Weekly put features (`wi_w_mom_side`, `wi_w_bias_side`, `wi_w_comp_edge`)
   show large MFE movement, but WR deltas are also large. They may still be
   useful, but only after a second pass separates barrier probability from
   exit-distance tuning.

## Conclusion

Proceed with a Stage 2 stock-barrier experiment, not a score-stage ship.

The next experiment should fit a small number of smooth TP multipliers against
the clean residual candidates:

- put `stoch_edge`
- call `pct_above_ema200_mkt`
- possibly event-only call `wi_ern_boost`

Gate it as barrier/portfolio work: compare per-trade MFE/MAE, then run
portfolio validation. Do not modify `Score.overall` unless a later pass proves
the feature improves probability independently of the existing score bands.

## Stage 2 TP-Multiplier Screen

Script:

```bash
python -u experiments/magnitude_predictors/sweep_tp_multipliers.py 1825 7
python -u experiments/magnitude_predictors/sweep_tp_multipliers.py 1825 15
python -u experiments/magnitude_predictors/sweep_tp_multipliers.py 1825 30
```

Method: direct OHLCV forward walk over generic stock barriers. Stop distances
stay fixed. TP distance is adjusted by smooth feature multipliers:

- put `stoch_edge`: `TP_mult = 1 + amp * normalized(stoch_edge)`
- call `pct_above_ema200_mkt`: `TP_mult = 1 + amp * normalized(pct_above_ema200_mkt)`

Primary output:

- `.cache/magnitude_predictors/tp_multiplier_summary_w7_1825.csv`
- `.cache/magnitude_predictors/tp_multiplier_summary_w15_1825.csv`
- `.cache/magnitude_predictors/tp_multiplier_summary_w30_1825.csv`

All-side summary:

| W | variant | side | delta WR | delta avg exit sigma | median bars delta |
|---:|---|---|---:|---:|---:|
| 7 | call_breadth_amp10 | call | -0.68pp | +0.002 | 0 |
| 7 | call_breadth_amp20 | call | -1.30pp | +0.005 | 0 |
| 15 | call_breadth_amp10 | call | -0.59pp | +0.006 | 0 |
| 15 | call_breadth_amp20 | call | -1.34pp | +0.009 | 0 |
| 30 | call_breadth_amp10 | call | -0.64pp | +0.005 | 0 |
| 30 | call_breadth_amp20 | call | -1.15pp | +0.010 | 0 |
| 7 | put_stoch_amp20 | put | +1.14pp | -0.006 | 0 |
| 15 | put_stoch_amp20 | put | +0.94pp | -0.015 | 0 |
| 30 | put_stoch_amp20 | put | +1.41pp | -0.005 | 0 |

### Stage 2 Verdict

No ship yet.

The put-side stochastic idea is not useful as a TP-widening magnitude rule.
In practice the fitted smooth multiplier tightens average put TP, raising hit
rate but reducing average stock EV. That is probability/capture-rate behavior,
not a better magnitude harvest.

The call-side breadth rule is directionally real and stable across W7/W15/W30:
healthier long-term market breadth supports slightly wider call TP. But the
aggregate lift is small (+0.002 to +0.010 sigma average exit improvement) and
costs -0.6pp to -1.3pp WR. This is worth a narrower follow-up, not a ship.

Follow-up refinement: restrict call breadth widening to higher-quality call
bands where W15 per-band lift is larger. Added variants:

- `call_breadth_75_89_amp10`
- `call_breadth_75_89_amp20`
- `call_breadth_80_89_amp20`
- `call_breadth_80_89_amp30`

Gated all-side results reduce the WR cost, but the aggregate EV lift also
shrinks:

| W | best gated variant | delta WR | delta avg exit sigma |
|---:|---|---:|---:|
| 7 | call_breadth_80_89_amp30 | -0.03pp | +0.001 |
| 15 | call_breadth_75_89_amp20 | -0.09pp | +0.005 |
| 30 | call_breadth_80_89_amp30 | -0.06pp | +0.004 |

The actual affected bands still show a real travel-distance signal:

| band | W15 delta avg exit sigma | W15 delta WR | variant |
|---|---:|---:|---|
| 80-84 | +0.060 | -0.37pp | call_breadth_amp20 |
| 85-89 | +0.043 | +0.60pp | call_breadth_amp20 |
| 75-79 | +0.019 | +0.15pp | call_breadth_amp10 |

Refined verdict: this is still below ship threshold. The signal exists, but
once it is gated tightly enough to avoid broad WR tax, the all-population EV
lift is tiny. Do not spend more time on broad put-stoch TP tuning unless the
objective is hit-rate rather than EV. Only revisit call breadth TP widening if
we wire it directly into portfolio simulation and the affected 80-89 call
cohort produces a drawdown or capital-efficiency benefit that this stock-only
EV screen cannot see.

## Conditional Magnitude Split

Script:

```bash
python -u experiments/magnitude_predictors/conditional_magnitude.py 1825 7
python -u experiments/magnitude_predictors/conditional_magnitude.py 1825 15
python -u experiments/magnitude_predictors/conditional_magnitude.py 1825 30
```

Method: split magnitude into two orthogonal questions inside side+score-band
cohorts:

- winner headroom: among trades that already hit generic stock TP, which
  features predict excess MFE beyond the target?
- loser near-misses: among trades that failed generic stock TP, which features
  travelled most of the way to TP before failing?

Primary output:

- `.cache/magnitude_predictors/conditional_baseline_w{7,15,30}_1825.csv`
- `.cache/magnitude_predictors/conditional_screen_w{7,15,30}_1825.csv`
- `.cache/magnitude_predictors/conditional_findings_w{7,15,30}_1825.md`

W15 baseline anchors:

| side | band | n | WR | winner excess MFE | winner reach 120% TP | loser MFE ratio |
|---|---:|---:|---:|---:|---:|---:|
| call | 70-74 | 14,831 | 72.7% | +0.717 sigma | 65.5% | 0.489 |
| call | 75-79 | 2,054 | 78.8% | +1.044 sigma | 69.1% | 0.504 |
| call | 80-84 | 814 | 84.9% | +2.273 sigma | 78.9% | 0.446 |
| put | 21-25 | 6,709 | 74.3% | +0.714 sigma | 80.0% | 0.353 |
| put | 16-20 | 2,845 | 76.8% | +0.744 sigma | 81.2% | 0.357 |
| put | 11-15 | 811 | 80.6% | +1.151 sigma | 82.7% | 0.340 |

The conditional split is more revealing than the first residual screen:

1. Call market breadth is a true winner-headroom signal. `pct_above_ema200_mkt`
   adds winner excess MFE across W7/W15/W30 by about +0.209/+0.291/+0.363
   sigma with strong z-scores. It also improves reach-120% odds, especially
   by W30.

2. Put stochastic edge is also a winner-headroom signal. `stoch_edge` adds
   about +0.280/+0.326/+0.351 sigma of winner excess MFE across W7/W15/W30.
   This survives the winner-only filter, so the earlier put signal was not
   just a probability artifact.

3. Near-miss features are a different family. Call `wi_w_adj_side` and
   `wi_w_mom_side` identify failed trades that got meaningfully closer to TP,
   but those are candidates for TP tightening or capital velocity, not for
   harvesting bigger winners.

4. Earnings remains event-specific. `wi_ern_boost` produces large winner
   headroom, but the cohort is sparse and should stay separate from the
   general magnitude answer.

## One-Sided TP Sweep

Script:

```bash
python -u experiments/magnitude_predictors/sweep_one_sided_tp.py 1825 7
python -u experiments/magnitude_predictors/sweep_one_sided_tp.py 1825 15
python -u experiments/magnitude_predictors/sweep_one_sided_tp.py 1825 30
```

Method: convert the conditional findings into one-sided smooth TP multipliers:

- widen TP only on the high-headroom side for call breadth and put stochastic
  edge;
- tighten TP only on near-miss features.

Primary output:

- `.cache/magnitude_predictors/one_sided_tp_summary_w7_1825.csv`
- `.cache/magnitude_predictors/one_sided_tp_summary_w15_1825.csv`
- `.cache/magnitude_predictors/one_sided_tp_summary_w30_1825.csv`

All-side W15 results:

| variant | side | delta WR | delta avg exit sigma |
|---|---|---:|---:|
| call_breadth_widen_pos10 | call | -1.11pp | +0.015 |
| call_breadth_widen_pos20 | call | -2.34pp | +0.025 |
| call_breadth_widen_pos30 | call | -3.43pp | +0.038 |
| call_breadth_80_89_widen_pos30 | call | -0.12pp | +0.007 |
| put_stoch_widen_pos10 | put | -0.32pp | +0.005 |
| put_stoch_widen_pos20 | put | -0.71pp | +0.008 |
| put_stoch_widen_pos30 | put | -1.03pp | +0.012 |
| call_wadj_tighten_pos20 | call | +1.54pp | -0.018 |
| call_wmom_tighten_pos20 | call | +1.88pp | -0.032 |
| put_wadj_tighten_pos20 | put | +0.66pp | -0.010 |

The same shape holds across W7/W30: one-sided widening creates positive
average stock-exit lift, but it pays for that lift with lower win rate.
One-sided tightening raises win rate while reducing average exit sigma.

Affected call bands show larger local W15 movement than the all-side rollup:

| variant | band | delta WR | delta avg exit sigma |
|---|---:|---:|---:|
| call_breadth_widen_pos10 | 75-79 | -0.44pp | +0.030 |
| call_breadth_widen_pos10 | 80-84 | -0.74pp | +0.040 |
| call_breadth_widen_pos20 | 80-84 | -1.47pp | +0.079 |
| call_breadth_widen_pos30 | 80-84 | -1.96pp | +0.107 |
| call_breadth_80_89_widen_pos30 | 85-89 | -1.49pp | +0.115 |

### Current Decision

This is now a portfolio candidate, not a stock-barrier ship. The information is
real enough to use in experiments, but the tradeoff is not free: wider TPs
increase average stock capture while reducing hit rate. The next useful test is
a Stage 3 portfolio simulation of a small candidate set:

- `call_breadth_widen_pos10`
- `call_breadth_widen_pos20`
- `call_breadth_80_89_widen_pos30`
- `put_stoch_widen_pos10`
- `put_stoch_widen_pos20`

Ship only if portfolio MC says the bigger winners improve drawdown-adjusted
outcomes. If MC shows higher compound with worse drawdown, keep it as a
diagnostic "expected headroom" surface rather than an execution rule.

## Portfolio MC Validation - v53

Run:

```bash
python -u experiments/magnitude_predictors/portfolio_tp_mc.py --lookback 1825 --variants baseline,call_breadth_widen_pos10,call_breadth_widen_pos20,call_breadth_80_89_widen_pos30,put_stoch_widen_pos10,put_stoch_widen_pos20,call_breadth_widen_pos10__put_stoch_widen_pos10,call_breadth_widen_pos20__put_stoch_widen_pos20 --windows 2021,2022,2023,2024,2025,dip,22-now,5y --n-iter 500
```

Final artifacts:

- `.cache/magnitude_predictors/runs/portfolio_tp_after_v53_overall2_20260512_041348/portfolio/summary.csv`
- `.cache/magnitude_predictors/runs/portfolio_tp_after_v53_overall2_20260512_041348/portfolio/results.jsonl`

Version: v53 (`e3ed806`, "Fix temporal echo recalc priors"). The run rebuilt
`signals_v53_1825.parquet` after the corrected overall-only recalc completed.

5y comparison versus baseline:

| variant | 5y mean return delta | 5y worst DD delta | call TP delta | put TP delta |
|---|---:|---:|---:|---:|
| put_stoch_widen_pos10 | +9.8% | -1.99pp | +0.02pp | -0.32pp |
| put_stoch_widen_pos20 | -3.2% | -2.47pp | -0.00pp | -0.79pp |
| call_breadth_80_89_widen_pos30 | -37.7% | -1.96pp | -0.43pp | -0.13pp |
| call_breadth_widen_pos10 | -45.5% | -0.84pp | -0.76pp | -0.07pp |
| call_breadth_widen_pos20 | -44.8% | +4.93pp | -1.15pp | -0.08pp |
| call_breadth_widen_pos10__put_stoch_widen_pos10 | -46.7% | -0.96pp | -0.75pp | -0.39pp |
| call_breadth_widen_pos20__put_stoch_widen_pos20 | -63.4% | +8.08pp | -1.13pp | -0.85pp |

22-now stress comparison:

| variant | mean return delta | worst DD delta |
|---|---:|---:|
| put_stoch_widen_pos20 | -36.6% | -0.32pp |
| call_breadth_80_89_widen_pos30 | -45.2% | -0.04pp |
| put_stoch_widen_pos10 | -4.9% | +1.74pp |
| call_breadth_widen_pos20__put_stoch_widen_pos20 | -68.0% | +3.59pp |

### Portfolio Verdict

No ship.

The only 5y candidate with positive compound and lower worst drawdown is
`put_stoch_widen_pos10`, but the effect is not robust enough: the same variant
worsens 22-now worst drawdown by +1.74pp and trims return by -4.9%. The stronger
put widening lowers 5y DD but gives back compound and put TP rate, while
call-breadth widening consistently taxes call TP and usually destroys compound.

Conclusion: keep the magnitude features as diagnostics for expected headroom.
Do not wire them into production TP/SL. If revisited later, restrict the search
to put-side stochastic widening with a narrower portfolio/DD objective; broad
call-breadth TP widening is now a killed branch unless a materially different
mechanism family is proposed.
