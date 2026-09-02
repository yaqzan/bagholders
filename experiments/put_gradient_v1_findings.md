# Put Gradient Fix v1 — Null Result (2026-04-23)

## Thesis
The 0-5 put bucket has worse WR15 (70.6% DB) than 6-10 (73.3%) — non-monotonic gradient at
the extreme. Hypothesis: asymmetric weekly (1.5× on puts) piles already-deep-bearish
component signals into clamped-0 territory, diluting resolution.

## Fix attempted
In `compute_overall_score`, after `calculate_weekly_adjustment`, dampen negative `adj` when
`weighted_sum < 30`:

```python
if adj < 0 and weighted_sum < 30:
    room = max(0.0, weighted_sum) / 30.0
    weekly_dampen = 0.5 + 0.5 * room   # 0.5 at ws=0, 1.0 at ws=30
    adj *= weekly_dampen
weighted_sum += adj
```

## Result (5y full universe, v21 DB → v1 sim)

| Bucket | DB N/WR15 | Sim N/WR15 | ΔN | ΔWR15 |
|---|---|---|---:|---:|
| <25 | 20022 / 69.1% | 23458 / 68.9% | **+17%** | −0.2pp |
| <20 | 9101 / 70.1% | 10210 / 69.3% | +12% | −0.8pp |
| <15 | 3718 / 72.1% | 4114 / 71.0% | +11% | −1.1pp |
| <10 | 1430 / 73.3% | 1605 / 72.4% | +12% | −0.9pp |
| <5  |  465 / 70.6% |  530 / 71.6% | +14% | +1.0pp |

Call side (70+/75+/80+/85+/90+/95+) unchanged within ±0.1pp — fix is correctly put-isolated.

## Why v1 fails per-trade gates
1. **N grew in every extreme bucket** — opposite of mechanism prediction. Dampening weekly
   magnitude should compress scores toward the middle, reducing extreme put counts. Observed
   +11-17% growth instead.
2. **WR15 regressed −0.8 to −1.1pp in <20/<15/<10** while only <5 improved +1.0pp.
   Monotonicity slightly better at the tail (<10→<5 drop from 2.7pp to 0.8pp) but the
   middle of the gradient deteriorated.
3. **Asymmetry not tracked** — the dampener adjusts the weekly ADDITIVE stage, but the
   subsequent multiplicative volume blend `50 + (ws-50) * (1+deviation)` and the
   put-mirror regime `(2.0 - mult)` both compound deviation-from-50. Suppressing the weekly
   push by 50-100% at ws=0 changes the input to those stages in ways the thesis didn't model.

## Ship decision
Not shipped. Gates fail. Nothing committed to `database/utils/scoring.py`;
`ALGORITHM_VERSION` untouched. `put_gradient_fix_v1.py` retained as experimental reference.

## Follow-up hypotheses (not tested this cycle)
- **Option C** — diminishing-returns on put-mirror regime: when `pre_regime ≤ 10`, cap the
  effective multiplier at `min(2.0 - mult, 1.3)` so already-extreme puts don't get
  further amplified by STRESS regime. Targets the interaction v1 missed.
- **Option D** — cap weekly_adj magnitude at `0.3 × |ws − 50|` regardless of direction.
  Bounds the weekly's ability to push scores past a fraction of their component-driven
  deviation.
- **Option B** — post-regime soft floor at overall=3 (not overall=0) to guarantee any
  score that reached the floor actually had ext < −10% capitulation signal. Current
  `_cap_dampened` lifts overall=0 to 5-20 only when ext < -10%, but scores reach 0
  from many paths and the cap is not a hard mechanism.

## Anomaly worth investigating independently
+11-17% N expansion across every put bucket when the fix was designed to compress them
suggests the put pipeline has a non-obvious amplification at the volume/regime stages
that magnifies small weighted_sum changes. Before any further gradient fix attempts,
profile **how much variation in `overall` comes from ws input variation vs the
multiplicative stages** — a sensitivity decomposition would clarify which stage actually
controls the population shape.
