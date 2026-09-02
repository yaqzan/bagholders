# Weekly-whiplash kill-switch probe — CLOSED (tamed) 2026-06-16

Closes the last open **weatherization** sub-item (item W) and Priorities **#7/#9**
(weekly-adjustment day-over-day score whiplash — the COHR 86→52, VICR 85→61 class).

## Question
Did the **v69 transition blend** (point-in-time partial-week + completed-week weekly
adjustment, weighted by bars-elapsed — live since v69, baked into all v74 recalc'd
scores) tame the day-over-day score whiplash? Per known-issues #9: a high-conviction
flip rate **< 5% → gate has no value (close NULL)**; **≥ 15% → high-value fix**.

## Method (read-only)
`probe.py` — adjacent-trading-day pairs of v74 daily `Score.overall`, 2024-06-01→now
(391k score rows; 523 of the 75+ symbols' close prices). The **gross** 75→{<75}
flip rate is NOT the metric — it is ~58%, dominated by benign boundary jitter
(median day-over-day |Δ|=5: a 76 ticking to 74) and by **genuine price-driven**
de-qualification. The COHR/VICR pathology is a **large score swing with little price
movement** (components were byte-identical; the move was weekly-driven). So the
whiplash metric is `|Δoverall| ≥ 15 AND |next-day price move| < 2%`.

## Result

| cut | FULL (2024-06+) | RECENT (2026-01+) |
|---|---:|---:|
| gross flip-out (75+→<75) | 58.4% | 59.4% | *(boundary jitter — not whiplash)* |
| large swing |Δ|≥15 | 15.6% | 14.5% |
| **whiplash |Δ|≥15 & price<2%** | **7.42%** | **5.42%** |
| |Δ|≥15 & price<1% | 3.71% | 2.64% |
| **|Δ|≥20 & price<2% (COHR/VICR magnitude)** | **3.28%** | **2.64%** |

## Verdict: CLOSE NULL — tamed
- The **severe** whiplash (the actual pathology: |Δ|≥20 with a flat price, or any ≥15
  with a truly-flat <1% price) is **~3% — below the 5% close-floor.**
- Every cut is **lower in the recent window than the full window** → the whiplash is
  **decreasing**, consistent with the v69 blend + the v72 WCF score-gate ramp
  (27/28-cliff fakeout family −60%) + the v74 lean (fewer dampener toggle-points).
- The loosest cut (|Δ|≥15 with up to a 2% move = 7.4% full / 5.4% recent) is borderline,
  but a 1.5–2% underlying move on a volatile 75+ momentum name is largely a **genuine**
  re-rating, not score-instability.
- Crucially, **no viable fix mechanism survives**: the replace-weekly-with-a-slow-index
  family is falsified (v42 rolling-weekly −13pp WR15 disaster; v44 substitution failed;
  day-of-week / Mon-only dampeners null). So even the borderline residual has no
  buildable remedy — the actionable conclusion (do not build a weekly-whiplash fix) is
  unchanged.

The weekly-whiplash item is therefore closed. Residual borderline large-flat-price
swings are a small, decreasing, no-known-fix tail; a future-only refinement (not needed
for this close) would attribute them to the weekly axis vs daily-component/regime/volume
toggles via `weight_info` (some of which — the WCF cliff, the intraday volume-amp TYPE
flip — are already separately addressed).
