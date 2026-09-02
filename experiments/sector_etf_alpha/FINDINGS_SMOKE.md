# V4 Smoke MC — Stage 3 directional check

**Date:** 2026-05-09
**Method:** Runtime monkey-patch of `monte_carlo.load_signals` / `load_put_signals` to apply V4 SWPM transform on v46 production scores at MC load time. NO production code modified.
**Iterations:** N=200 per window (single-process, MC_NO_MP=1)
**Windows:** 4 (5y, 22-now, 2022, 2024)
**Lookup:** 35,305 (sym, date) entries; 4,663 modified (13.2%) — 2,298 lifted, 2,365 dampened.

## Per-window result

| Window | Base WorstDD | Treatment WorstDD | ΔWorstDD | Base MeanDD | Treatment MeanDD | ΔMeanDD | Base Compound | Treatment Compound |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **5y** | 62.6% | 63.2% | **+0.6pp** | 48.7% | 48.1% | -0.6pp | 1.56e37% | 4.14e36% (0.27×) |
| 22-now | 62.0% | 63.4% | +1.4pp | 48.6% | 46.9% | **-1.7pp** | 3.24e31% | 2.22e31% (0.68×) |
| 2022 | 59.4% | 64.1% | **+4.7pp** | 47.9% | 46.7% | -1.2pp | +33.4M% | +58.6M% (1.75×) |
| 2024 | 49.1% | **56.3%** | **+7.2pp** | 30.7% | 29.9% | -0.8pp | +82.5B% | +52.2B% (0.63×) |

P(collapse) = 0% on every window for both. Patch confirmed working: 3,188 calls modified, 1 dropped; 921 puts modified, 0 dropped on 22-now.

## Stage 3 gate evaluation

Per [assessment-backtest.md](.claude/docs/assessment-backtest.md) Stage 3 gates:

| Gate | Threshold | V4 result | Status |
|---|---|---|---|
| **T1** Stage 1 frozen | scoring stack must not change | V4 *is* the scoring change | n/a smoke |
| **T2** N=500+ | iterations per (window × mode) | N=200 (smoke only) | smoke insufficient |
| **T3** All 8 windows | 2021/2022/2023/2024/2025/dip/22-now/5y | 4 of 8 covered | smoke partial |
| **T4** 5y WorstDD primary | ≤ baseline +1.0pp | +0.6pp | **PASS** |
| **T5** Per-window DD stability | ≤ baseline +5pp on every window | 2024: **+7.2pp** | **FAIL** |
| **T6** Collapse rate | 0% | 0% on all | PASS |
| **T7** Compound OOM | within ±3 OOMs of baseline | 5y: -1 OOM | PASS |

**V4 fails T5 at 2024.** The +7.2pp WorstDD increase on the calmest annual window (baseline=49.1%) is well outside MC noise.

## Diagnosis

V4 reshapes the score distribution:
- **Dampens 80-84 tier** (-14% N at 80+) — pushes overheated-sector and rs-extreme calls to 70-79
- **Lifts 70-74 → 85+/90+** (+14%/+4% N) — promotes oversold-sector calls into ULTRA-tier alloc

The lifted signals are **correlated** — they all fire when sector ETF RSI is low.
The cascade fills 14 concurrent slots with tier alloc 20%/15%/10%/10%; lifting 4-5
calls into ULTRA simultaneously consumes ~80% of capital on a **single-factor bet
(low sector RSI)**. Per-trade WR7 of these signals is real (+10.1pp on the cohort
in Phase B) but at the portfolio level the correlated DD on adverse paths
overwhelms the per-trade alpha advantage.

The MeanDD-improves / WorstDD-degrades pattern is the signature of a mechanism
that lowers volatility on average paths but exposes new tail-risk paths.

This is exactly the kind of result the three-stage framework's Stage 3 gate is
designed to catch — Stage 1 W1-W6 (per-trade WR7) approves V4, Stage 3 T5 catches
the correlated-DD cost.

## Recommendation update

**DO NOT SHIP V4 standalone.** Three viable paths forward:

### Path A — Stage 3 retune to defuse correlated DD (most thorough)
Run a Stage 3 retune of portfolio constants AFTER applying V4 transform.
Candidates to lower correlated-DD exposure:
- Per-day correlated-call cap (e.g. max 2 ULTRA-tier fills per signal date)
- Tighter ULTRA alloc when V4-lifted (e.g. 0.15 instead of 0.20 for sec_phase < -0.5 lifts)
- Tighter DD soft band (LO=0.30 instead of 0.40, or stronger floor)

Cost: ~1 day of N=300 × 8-window MC sweeps.

### Path B — Constrain V4 mechanism (mild)
Re-calibrate V4 with portfolio-aware objective. Two modifications:
- Cap TARGET_UP at 85 instead of 90.79 (don't push lifts into ULTRA tier)
- Add a per-day correlated-trades-firing constraint as part of the LHS objective

This is essentially redoing Phase C-D-E with a multi-objective: per-tier WR7 lift
+ correlated-DD penalty. Would likely sacrifice ~30% of WR7 alpha for clean
portfolio dynamics.

Cost: ~3 hours of re-calibration.

### Path C — Pursue stock_rs_5d standalone (alternative)
The cohort z analysis showed `stock_ret_5d − sec_ret_5d` U-curve at z=−9.58 / −8.52
(strongest single feature in codebase). The current RSU sub-mechanism in V4 only
captures part of this signal. A standalone score-stage dampener targeting JUST
extreme rs_5d (no sector phase mechanism, no correlated-cohort lift) would have:
- Smaller affected cohort (just |rs_5d| > 0.07 calls)
- No upward score lift (only dampens)
- No tier promotion → no concentrated-bet risk
- Per-trade alpha of ~+1pp on 80+ tier (similar magnitude to V4's per-trade gain
  on that tier without the correlated-DD downside)

Cost: ~1 day to design, calibrate, smoke MC.

### Path D — Accept tradeoff and ship anyway
- 5y headline DD essentially flat (+0.6pp within noise)
- 5y compound -73% (but unrealizable at MC scale)
- Per-tier WR7 on 85+/90+ tiers strong (+1.24pp / +0.93pp)
- Real-world live trading: trader experiences MeanDD improvement on most paths;
  bad-tape paths see worse DD
- Ship and monitor; revert at first WorstDD breach

**Recommended path: B (constrained re-calibration) or C (rs_5d standalone).**

Path B preserves the work invested in V4. Path C is a cleaner mechanism that
captures the strongest single cohort signal without the correlated-cohort risk.

## Important caveat — N=200 smoke noise

Per [known-issues.md](.claude/docs/known-issues.md) MC noise floor:
- N=300 single-window: compound swings 1.6-1.8× baseline-to-baseline
- DD signal reliable at N≥300 (±3pp inter-seed)
- N=200 has even higher noise — DD ±4pp is plausible

The 5y WorstDD +0.6pp and 22-now +1.4pp results are within noise.
The 2022 +4.7pp and 2024 +7.2pp deltas are larger than typical N=200 noise but
still need N=500 confirmation before treating as definitive.

The MeanDD improvements (averaged over 200 iters) are ~±0.5pp noise — most likely real.

## Artifacts

- `smoke_mc_v4.py` — runtime patch + MC runner
- `analyze_smoke.py` — multi-window comparison parser
- `smoke_mc_5y_baseline.log`, `smoke_mc_5y_treatment.log` — 5y N=200 logs
- `smoke_mc_baseline.log`, `smoke_mc_treatment.log` — 22-now N=200 logs
- `smoke_mc_2022_*.log`, `smoke_mc_2024_*.log` — annual window logs
- `.cache/sector_etf_alpha/v4_lookup.pkl` — 35,305-entry transform lookup
