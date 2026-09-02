# Deep Crash SCREEN — read (FABLE, 2026-07-13)

**Tier: SCREEN, not GATE** (doctrine: assessment-backtest.md "Deep-window screens"; a deep FAIL is a
mandatory mechanism investigation, never an automatic revert; a deep PASS is weak comfort). N=300 per
window, v74 pinned (`f9fb7b934`), survivor-only substrate — every number below reads OPTIMISTICALLY.
Queue task 606; full per-window artifacts in results/.

| profile | window | med ret | worst DD | P(collapse) |
|---|---|---:|---:|---:|
| Core | ltcm_1998 | −25.8% | 53.3% | **0.0%** |
| Core | dotcom_2000_2002 | −43.0% | 59.5% | **0.0%** |
| Core | gfc_2007_2009 | −20.6% | 65.5% | **0.0%** |
| Core | 2007_now | +3,706% | 70.8% | **0.0%** |
| Apex (held) | ltcm_1998 | +8.8% | 71.4% | 0.0% |
| Apex (held) | dotcom_2000_2002 | −86.3% | 92.2% | **100.0%** |
| Apex (held) | gfc_2007_2009 | −74.7% | 86.5% | **20.7%** |
| Apex (held) | 2007_now | −14.9% | 95.9% | **48.3%** |

## Read
1. **Core: deep PASS.** collapse=0 on every deep window including two multi-year grind regimes
   (dot-com 2.5y, GFC 1.5y) the DD stack was never calibrated on. Consistent with the deterministic
   pack numbers (P1.1) and the Sentinel≫Core≫Apex hierarchy. Weak comfort by doctrine: survivor bias
   means true DD/collapse would be worse; the delisted-equity buy (P2.1, user-locked) is what would
   harden these numbers.
2. **Apex held-form: deep FAIL — quantified ruin.** 100% collapse held through dot-com at N=300;
   20.7% GFC; 48.3% held 19y. The mandated investigation RESOLVES TO THE DOCUMENTED MECHANISM
   (capital-velocity law; "never make the sprint the held default", concentration_2x FINDINGS): the
   sprint's value exists only under stop-at-2x discipline. No new mechanism work required. Mitigation
   already shipped 2026-07-13: the 2x watchdog (halt-new-entries latch, `0b6b778e0`).
   **Live-money relevance:** the live ledger has been HOLDING Apex (15-DTE elbow) since ~2026-06-22 —
   this screen sharpens the standing user decisions (P0.3 DTE switch, evidence task 610; and the
   stop-at-2x/rotation discipline generally).
3. No gating, tuning, or calibration action follows from any of this (SCREEN tier). The numbers are
   context for ship decisions and the December read.
