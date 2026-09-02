# Put Gradient Fix v2 — Null Result (2026-04-23)

## Thesis
From `put_pipeline_sensitivity.py`: 100% of =0 signals had `base_ws > 25` (not
bearish from components alone); weekly pushed 99.4% across the 25 line; volume
contributed 12.7pp of deviation at =0 vs 0.5pp at 20-25. The =0 pile is an
amplifier-stacking artifact, not real capitulation.

## Fix attempted (Option E — amplifier-room dampener)
Between the amplifier stages (weekly + volume) and the clamp, compute
component-bearishness room = `max(0, 50 - base_ws)`. When components are not
meaningfully bearish (`room/20 < 1`), cap the amplifier contribution
proportionally:

```python
if weighted_sum < base_ws:
    component_deviation = max(0.0, 50.0 - base_ws)
    room = min(1.0, component_deviation / 20.0)
    if room < 1.0:
        amp_dampen = 0.5 + 0.5 * room        # 0.5..1.0
        amp_contribution = weighted_sum - base_ws  # negative
        weighted_sum = base_ws + amp_contribution * amp_dampen
```

At `base_ws=50` (neutral): dampen=0.5 → amplifiers 50% effective.
At `base_ws≤30` (clearly bearish): dampen=1.0 → unchanged.

## Result (5y full universe, v21 DB → v2 sim)

| Bucket | DB N / WR15 | Sim N / WR15 | ΔN | ΔWR15 |
|---|---|---|---:|---:|
| 70+ | 10350 / 71.6% | 10910 / 71.1% | +5% | −0.5pp |
| <25 | 20022 / 69.1% | 19622 / 67.9% | −2% | −1.2pp |
| <20 | 9101 / 70.1% | 8236 / 68.5% | −10% | −1.6pp |
| <15 | 3718 / 72.1% | 3336 / 70.9% | −10% | −1.2pp |
| <10 | 1430 / 73.3% | 1307 / 71.3% | −9% | −2.0pp |
| <5 | 465 / 70.6% | 441 / 71.1% | −5% | **+0.5pp** |

Sim peaks: 31,268 vs DB 50,579. The amplifier-only =0 pile IS being compressed
(population behavior correct), but per-trade WR regresses on every bucket except
<5.

## Why v2 fails per-trade gates

1. **Over-broad gate.** `weighted_sum < base_ws` fires on any stage where
   amplifiers pushed the score down, including calls with negative weekly
   adjustments. Call side 70+ N grew +5% (10350 → 10910) with WR slightly
   lower (71.6% → 71.1%) — the dampener unintentionally lifts calls whose
   bullish components got knocked down by a bearish weekly.

2. **Over-reaching room threshold.** The linear ramp from `base_ws=50` down to
   `base_ws=30` dampens signals in the 30-40 base_ws band that had genuine put
   edge. Those signals are the core of the <20/<15/<10 population — all three
   regress ~1.2-2.0pp.

3. **Tail is only marginal.** Only <5 (the deepest tail — where the profiler's
   modal =0 pile actually lived) shows improvement, and only +0.5pp WR15 /
   +1.2pp WR30. The dampener nearly gets the right answer at the extreme and
   is wrong everywhere else.

## Ship decision
Not shipped. Per-trade gate failed. No `ALGORITHM_VERSION` bump, no commit,
no production change. `put_gradient_fix_v2.py` retained as reference.

## Follow-up (v3 design sketch)

v3 must narrow the gate to fire only on the =0/<5 pile specifically:

- **Put-isolated:** `weighted_sum < 50 AND weighted_sum < base_ws` — exclude
  any call-direction amplifier movement.
- **Tighter room:** `component_deviation < 10` (only `base_ws > 40`), so the
  30-40 base_ws band that v2 over-suppressed is left alone.
- **Target the clamp:** only dampen when the un-dampened score would clamp
  at ≤5 (the profiler's evidence-base) — i.e. condition on the projected
  pre-clamp value, not just the amplifier direction.

If v3 also fails per-trade: pivot to direction-aware volume amplifier
(REJECTION = 68% of =0 signals per profiler; treating REJECT as absorption
not continuation at the extreme tail is the alternative thesis).
