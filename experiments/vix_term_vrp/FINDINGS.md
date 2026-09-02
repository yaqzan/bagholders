# N5 forward-vol-expectation probe (VIX term structure / VVIX / VRP) — NULL, corner closed (2026-06-18, /research)

**Lead (NEW_LEADS N5):** the last untested regime-signal family. VIX-momentum
(weekly-MACD/velocity/accel) is freshly NULL (G28), and the regime multiplier
already encodes VIX LEVEL + velocity. This probes **forward vol expectation** —
VIX9D/VIX3M term-structure slope (backwardation), VVIX (vol-of-vol), VRP
(VIX² − realized var). $0-cost W1 cohort mine on the 75+ apex CALL book.

**Decisive test (G28):** a forward-vol signal must add WITHIN VIX-level bands
(orthogonal to level), not just track level. Every cohort mined pooled AND within
calm/norm/elev VIX bands. Read-only; yfinance fetch cached to `vix_term_cache.parquet`.

## Result: NULL (the documented "expectation low" outcome)

- **Pooled terciles** (ts_ratio, vrp, vvix): all |z| < 1.2 vs rest. No signal.
- **Within VIX-level bands** (the control): ts_ratio/vrp/vvix terciles all |z| < 2
  (strongest: norm-VIX vrp-MID z−2.70, a non-monotonic mid-dip, not directional).
  Every pooled flicker DISSOLVES once VIX level is controlled → it was just level
  (already encoded). Confirms the family is null, like all prior regime A/Bs.

## The one flicker (noted, not actionable): norm-VIX backwardation
The single cell that survives the level control: **norm-VIX (16-22) +
backwardation (VIX9D > VIX3M) → 75+ calls +8.94% apex-EV vs contango +1.22%
(z+3.47, N=479)**, echoed in calm-VIX (z+2.74 but N=39 noise). Mechanism: a
*contained* near-term fear spike (backwardation while VIX still moderate) →
bullish-technical names mean-revert / bounce (the G19 buy-weakness pattern),
conditional on a forward-vol state distinct from VIX level.

**But it is NOT a clean lead — it fails W3 (multi-window consistency):**

| 2016 | 2018 | 2019 | 2023 | 2024 | 2025 | 2021/2022 |
|---|---|---|---|---|---|---|
| **−8.17** (N43) | +5.81 | +17.49 | +6.77 | +5.78 | −2.26 | N=0 (state absent) |

Positive in the N≥80 windows (2018/2019/2024) but **sign-flips negative in 2016
(−8.17, N=43)** and 2025, and the state doesn't even occur in 2021/2022. The
pooled z+3.47 leans on 3-4 windows. Same G26/persist-crash pattern (real in some
regimes, flips in others). Plus: narrow (3.7% of the 75+ book) and a *size-up /
admit-more* direction — which the concurrent v74 cascade retune just showed is
**G16-DD-costly** to exploit (sizing up calls at coverage 0.97 + the 50% cap
concentrates). And it's 1 cell of ~30 tested (multiple-comparison-marginal).

## Status
NULL / corner closed. The forward-vol-expectation family adds nothing beyond the
VIX level the multiplier already encodes. The norm-VIX-backwardation flicker is
recorded for the record (a future agent with deeper option/IV data or a different
mechanism could revisit "contained fear spike → bounce"), but it is not W3-stable,
not portfolio-exploitable in the size-up direction, and not worth a build.
**Do-not-retry:** VIX term structure / VVIX / VRP as a regime cohort — null within
the level control. Artifacts: `mine.py`, `robust.py`, `vix_term_cache.parquet`.
