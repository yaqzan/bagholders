# Weekly Put Wave Findings

Date: 2026-05-15

## Formula

The staged replacement for the legacy `WEEKLY_PUT_SCALE` cliff is:

```text
scale = floor + (peak - floor) * tanh((abs(raw_wadj) / width) ** power)
weekly_adj = raw_wadj * scale
```

It applies only when the raw weekly adjustment is negative. `raw_wadj` is the
pre-amplification `base_bias + momentum_bias` total from
`calculate_weekly_adjustment()`.

## Run

Primary WR15 optimizer run:

```text
C:\Development\Trader_wadj_put_wave_20260515\.codex\runs\wadj_put_wave_bayes_wr15_20260515_191453
```

Surface:

- 5-year lookback
- 891 stock contexts
- 931,057 cached scoring cases
- 15,711 non-overlapping legacy baseline peaks
- 32 Bayesian/seed candidates
- WR15 `30dte_generic` outcomes from the DuckDB barrier mirror

Earlier WR7 run:

```text
C:\Development\Trader_wadj_put_wave_20260515\.codex\runs\wadj_put_wave_bayes_cached_20260515_174750
```

That run is superseded for decision-making because the current emphasis is
WR15. It was useful only as a plateau check.

## Result

Every evaluated WR15 candidate had the same N/WR15 surface as the legacy static
`WEEKLY_PUT_SCALE` path.

Best/tied seed:

```text
floor=1.0
peak=1.5
width=10.0
power=1.0
objective=0.0
penalties=0
```

Baseline 5-year WR15 PUT buckets:

```text
<25 N=4,924 WR15=78.11%
<20 N=2,044 WR15=78.82%
<15 N=798   WR15=80.45%
```

All candidates preserved those same bucket counts and win rates within the
tracked objective.

## Conclusion

This smooth wave is safe as a threshold-removal implementation in the tested
range, but the Bayesian objective found no measurable alpha or utility lift.
The tested constants sit on a flat peak-assessment plateau, so the current
evidence does not justify a scoring ship on performance grounds alone.
