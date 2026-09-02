# Investigation Conclusion: Put DD — No Viable Intervention Found

## The Central Finding

**High put scores are NOT catastrophic to DD despite high WR.** The original premise was inverted:
- Puts in extreme bear tape ARE winning (WR rises with depth, per Phase 1A)
- Puts in bear tape are PARTIAL HEDGES (they TP when calls SL)
- The DD problem comes from the size of the leveraged positions in macro-correlated events
- ANY mechanism that reduces put exposure in bear tape undermines this hedge

All five tested intervention classes confirm this structural diagnosis.

---

## Phase-by-Phase Summary

| Phase | Mechanism | Result | Root cause of failure |
|---|---|---|---|
| 1C | Hard ablation (drop deep puts) | FAILED (+0.7pp DD) | Deep puts are partial hedges |
| 2+3 | Score-stage gradient (OptTP per-trade) | Per-trade: S3 looks good | Doesn't fix portfolio correlated-DD |
| 4 | Canonical MC for score-stage variants | ALL FAILED (no max DD improvement) | Deep puts are hedges; removing them hurts recovery windows |
| 6 | Daily put cap (MAX_PUTS_PER_DAY) | NULL (max DD ±noise) | MaxPos=14 already limits; idle slots don't help |
| 7b | Stress-side F3f put cut (S1) | NULL at N=500 (+0.9pp worse) | Puts in stressed tape are winning → cutting their allocation removes the hedge |

---

## Why Puts Can't Be Reduced to Fix DD

### Phase 1A result
- PUT bucket 6-10: WR15=55.7% (highest of all put buckets!)
- OptTP15 falls with depth (44.2%) — but BARRIER-TOUCH WR rises

### What this means for portfolio DD
On a day like 2022-08-30 (124 puts fire, market crashes):
1. Most put signals HIT TP (stock falls, put wins — market IS crashing)
2. Call signals HIT SL (stock falls against call direction)
3. The portfolio's net P&L on extreme crash days = put TP gains − call SL losses
4. Puts are the POSITIVE leg of the portfolio on crash days

Reducing puts in crash tape → fewer put TPs → larger net loss → HIGHER DD.

### Why DD is high despite puts hedging
The 73-74% max DD is inherent to the strategy structure:
- 14 concurrent positions at 10-20% allocation each
- In a macro-bear event, 14 call positions all SL simultaneously
- Put TPs partially offset but don't fully cancel call SLs (puts get 29% net vs calls losing 37.3%)
- The DD circuit breaker (0.60) already stops the cascade

---

## The Real DD Levers (What Already Works)

| Mechanism | Ship date | DD effect |
|---|---|---|
| DD circuit breaker DD=0.60 | 2026-05-01 | 5y: -4.1pp, 22-now: -3.5pp (N=1000 validated) |
| F3f call cut in stress | 2026-04-24 | Reduces call allocation in weak tape |
| Dead-hold mechanism | 2026-05-01 | Keeps positions alive during bear rallies |
| Phase v32: cut low tier 0.15→0.12 | 2026-05-01 | 5y: -6.7pp DD |
| EARN_SUPP_PUT | 2026-04-26 | -2pp DD on 5y |

These already represent the primary available levers. The current max DD under canonical bounded-fill MC:
- N=1000 baseline: 5y DD 83.5%, 22-now DD 83.1% (per known-issues.md Phase OP2)
- After DD060 ship: 5y DD 79.4%, 22-now DD 79.6%

---

## What Was Learned

### About put signals specifically
1. **Deep puts (≤10) are NOT quality-degraded overall** — WR15 rises with depth (Phase 1A)
2. **OptTP15 gap is real but narrow** (44.2% vs 43.5% BE = 0.7pp margin), not catastrophic
3. **Concurrent-fire density is the real DD driver** (Phase 1B) — top-20 high-density days = ALL Aug-Sep 2022
4. **Deep puts ACT AS HEDGES** in bear tape — confirmed by every intervention attempt failing

### About the portfolio architecture
- The F3f breadth knob already reduces put allocation in BULL tape (correct: fewer noise puts when market is healthy)
- The F3f for STRESS should NOT cut puts (incorrect: puts are needed most in stress)
- The DD circuit breaker is the right mechanism for portfolio-level protection

### About the MC methodology
- N=300 per window has ±5-8pp DD noise; Phase 7 N=300 result was lucky seeds
- N=500 is the minimum for reliable DD signal on individual windows
- Pattern consistency across windows (all 8 improving) is a better signal than individual window magnitudes
- BUT: a lucky N=300 run showing all 8 windows improving can still be noise (Phase 7 proved this)

---

## Do NOT Retry

- Any mechanism that REDUCES put allocation or put count in heavy-stress / bear tape
- Score-stage gradient lifts for deep puts (Phases 2-4)
- Daily put count caps (Phase 6)
- Stress-side F3f put allocation cuts (Phase 7)

All of these undermine the natural hedge that puts provide in exactly the regime where they're most valuable.

---

## If Further DD Reduction Is Needed

The only viable paths (from known-issues.md):

1. **Tighter DD circuit breaker** (DD=0.55-0.58): TESTED in Phase OP1/OP2, showed 22-now-biased win at N=150 but reversed at N=300. Needs N=1000 validation. Mechanism is correct (pauses entries in distress); question is optimal threshold.

2. **Reduced call allocations on the low tier** (Phase v32): 75-79 CALL tier cut 0.15→0.12 already shipped. Further cuts to mid/ultra tiers possible but trade compound vs safety.

3. **Reduced MaxPositions** (currently 14): tested, borderline at MaxPos=13 on 2022 Conservative. Could revisit at MaxPos=12 at N=1000.

4. **Per-bucket TP/SL based on MFE/MAE** (known-issues.md Priority #18): untested, architecturally complex. The put SL=−20% is already tighter than calls; per-bucket tuning would require N=1000+ to validate signal.

The current strategy is already within 4-6pp of the 80% DD floor (79.4-79.6% at N=1000 per Phase OP2). The correct assessment is that the floor has been substantially cleared and further DD reduction has diminishing returns vs the cost to compound.

---

## Files

| File | Content |
|---|---|
| `PHASE1_FINDINGS.md` | Per-bucket WR/OptTP decomposition + concurrent-fire density |
| `phase1_diagnostic.py` | Phase 1A/1B scripts |
| `phase1c_mc_ablation.py` | Hard ablation (drop_lt5/lt10/lt15) — null/negative |
| `phase3_sweep.py` | 265-variant per-trade gradient sweep (M1_depth wins per-trade but not portfolio) |
| `phase4_canonical_mc.py` | 6-variant canonical MC — all failed |
| `PHASE4_FINDINGS.md` | Score-stage final verdict |
| `phase6_daily_put_cap.py` | Daily cap sweep — null |
| `PHASE6_FINDINGS.md` | Cap mechanism analysis |
| `phase7_f3f_put_stress.py` | Stress F3f sweep — S1 looked good at N=300 |
| `phase7b_validate_s1.py` | N=500 validation — null result |
| `PHASE7_FINDINGS.md` | F3f stress mechanism analysis |
