# Put Gradient Fix v3 — Null Result (2026-04-23)

## Thesis (refined from v2 failure)

v2 failed because (1) the gate `weighted_sum < base_ws` caught call-direction
amplifier movement, (2) room threshold `component_deviation < 20` over-reached
into the 30-40 base_ws band that holds genuine put edge, (3) only the <5 bucket
showed the intended lift.

v3 narrowed all three:
1. **Put-isolated gate**: `weighted_sum < 50 AND weighted_sum < base_ws`
2. **Tighter room**: `component_deviation < 10` (base_ws > 40 only)
3. **Tail-targeted**: only fire when un-dampened `weighted_sum <= 5`

Stronger compression ramp: `dampen = 0.3 + 0.7 * room` (vs v2's 0.5 + 0.5).

## Result (5y full universe, v21 DB → v3 sim)

| Bucket | DB N / WR15 | Sim N / WR15 | ΔN | ΔWR15 |
|---|---|---|---:|---:|
| 70+ | 10,350 / 71.6% | 10,342 / 71.6% | ~0 | 0.0pp |
| <25 | 20,023 / 69.1% | 23,468 / 68.9% | +17% | **−0.2pp** |
| <20 | 9,101 / 70.1% | 10,252 / 69.4% | +13% | **−0.7pp** |
| <15 | 3,718 / 72.1% | 4,172 / 71.0% | +12% | **−1.1pp** |
| <10 | 1,430 / 73.3% | 1,655 / 72.8% | +16% | −0.5pp |
| <5 | 465 / 70.6% | 543 / 71.9% | +17% | **+1.3pp** |

Call side untouched (all 6 buckets within noise). Sim peaks 34,584 vs DB
50,583 — the dampener IS compressing the amplifier-only =0 pile as intended.

## Why v3 fails per-trade gates

1. **The compressed signals land in <15/<20, diluting those buckets.** The
   dampener lifts amplifier-only scores out of clamp-0 territory, but they
   don't rise all the way to neutral — they land in the 5-15 range where
   they mix with genuine quality puts and drag WR15 down 0.7-1.1pp.

2. **The gate STILL isn't narrow enough.** Even with `ws <= 5` tail-targeting,
   17% of the <25 population is being repopulated from former-=0 signals,
   and those signals carry worse WR than the native <25 population. The
   "amplifier-only =0" cohort is lower-quality than the bucket it's being
   pushed into.

3. **Only <5 shows improvement (+1.3pp WR15).** Same pattern as v2 — the
   deepest tail benefits because the dampener's target lift lands IN that
   bucket, not above it. Every bucket above <5 sees dilution from new
   arrivals.

## Structural conclusion

Both v2 and v3 confirm the same population dynamic: compressing the
amplifier-only =0 pile REDISTRIBUTES mass upward into lower-quality cohorts
in adjacent buckets. The dampener cannot simultaneously:
- Lift amplifier-only =0 signals out of the tail (intended effect)
- Preserve bucket quality in <15/<20 (required for per-trade gate)

Any dampener that operates on the score-distribution stage hits this
constraint. **A different lever is needed.**

## Ship decision

Not shipped. Per-trade gate failed on 4 of 5 put buckets. No
`ALGORITHM_VERSION` bump, no commit, no production change.
`put_gradient_fix_v3.py` retained as reference.

## Pivot — direction-aware volume amplifier (v4 thesis)

Per `put_pipeline_sensitivity.out`: **68% of =0 signals fire on REJECTION
volume**, vs 21% on <25 aggregate. REJECTION at the deepest-put extreme
is statistically capitulation absorption (large buyers at exhausted selling)
NOT breakdown continuation.

v4 design: at the volume amplifier stage, when `vol_sig == 'REJECTION'` AND
`weighted_sum < 35` (already bearish) AND exhaustion footprint confirmed
(Stoch <= 15, RSI <= 35, pct_from_ema50 <= -10%), either:
- **Design A**: flip amplifier sign toward neutral (softer)
- **Design B**: cap multiplier at 1.0 (no amplification — safer first-ship)

This operates on the amplifier stage itself, not the post-amplifier score,
so it does NOT redistribute mass between buckets — it prevents the bad
amplification from happening in the first place. Population sizes should
remain stable in <15/<20 with the =0 pile thinning directly.

See `Open Optimization Priority #9` in CLAUDE.md for the full design sketch
and test plan.
