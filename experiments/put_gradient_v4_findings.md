# Put Gradient v4 — Findings (2026-04-23)

## Hypothesis

REJECTION volume signals at exhaustion extremes are statistically absorption/bottom-buying,
not breakdown continuation. Gate: `vol_sig == 'REJECTION' AND pre_vol_ws < 35 AND
raw_stoch <= 15 AND raw_rsi <= 35 AND pct_from_ema50 <= -10%`. Expected: footprint
cohort WR15 >= 5pp BELOW non-footprint REJECTION cohort.

## Result: FALSIFIED

Script: `experiments/exhaustion_footprint_profile.py`  
Lookback: 730d (2y), algorithm v21 (aba4f5d)  
Universe: 653 stocks, 324,421 scored rows, 22,009 put signals (overall <= 25)

| Cohort | N | WR15 | WR30 | Ret30 | Delta vs (b) |
|---|---|---|---|---|---|
| (c) All puts <= 25 [baseline] | 21,933 | 66.4% | 66.9% | -0.0% | — |
| (b) REJECTION, ws<35 [no footprint] | 3,907 | 65.6% | 66.4% | -0.2% | — |
| (a) Exhaustion footprint REJECTION | 367 | 65.6% | 64.6% | -0.2% | **-0.0pp** |

Threshold sensitivity sweep — no combination achieves the -5pp gate:

| Dimension | Best variant | N_foot | WR15_foot | WR15_nofoot | Delta |
|---|---|---|---|---|---|
| stoch | <= 10 | 240 | 66.4% | 65.5% | +0.8pp (wrong direction) |
| rsi | <= 25 | 92 | 64.1% | 65.6% | -1.5pp (best, but N too small) |
| ext | <= -5% | 460 | 64.0% | 65.8% | -1.7pp (best at adequate N) |
| ext | <= -15% | 250 | 68.8% | 65.4% | +3.4pp (wrong direction, deep ext performs better) |
| ws | <= 30 | 264 | 66.2% | 65.6% | +0.6pp |

## Interpretation

The PSKY case study (2026-03-20: REJECTION at extreme extension → +29% rally in 3 weeks)
was a BAD_LUCK event, not a population pattern. At population scale:

1. Exhaustion footprint REJECTION signals have identical WR15 to non-footprint REJECTION.
2. At deeper extension (ext <= -15%), footprint signals actually OUTPERFORM (+3.4pp) —
   extremely extended stocks + REJECTION candle is a legitimate breakdown signal.
3. Tighter oscillator gates (stoch<=10) show footprint slightly BETTER than non-footprint.
4. The volume amplifier at exhaustion extremes is adding real signal, not noise.

## Conclusion

Do NOT suppress or neutralize the REJECTION amplifier for any exhaustion footprint sub-population.
The zero-score clustering problem (known issue) is a real distribution artifact but cannot be
fixed at the amplifier stage without degrading genuine signals.

v1 (weekly dampen), v2 (amplifier-room dampener v1), v3 (amplifier-room dampener v2), v4 (volume
amplifier stage) have all been searched. The correct approach is NOT in the scoring formula
for the =0 put pile.
