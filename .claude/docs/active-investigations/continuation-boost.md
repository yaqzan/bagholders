# Continuation Boost — Active Investigation Log

**Status (2026-05-04):** SHIPPED as v33 (commit `28fa522`). V2 variant (97 promotions/5y, promoted-cohort TP% 62.2% vs 75+ baseline 61.02%, Q1 gate +0.01pp) implemented in scoring pipeline. MC gate not applicable — Q1 per-trade gate is the correct ship criterion for scoring changes per assessment-backtest.md.

**Follow-up flag (2026-05-13):** UAMY 2025-07-18 exposed a likely failure mode in the current continuation echo lineage: `cont_lift` can promote an exhaustion-entry candle into the tradable CALL gate. Active v58 (`3cfc4dc2`) promoted that row to `75H` with `cont_lift=+4.0`, while older persisted rows mostly sat around `70-72`. The entry candle had a tiny body, dominant upper wick, weak close location, `REJECTION` volume signal, and a sharp 10-bar runup; the next session stopped before the later rebound. Treat this as a cohort experiment seed, not a single-case ship reason. Candidate audit lineage: v33 legacy boost (`28fa5227`), v52 temporal echo wave (`f66bf9b9`), v53 prior-fix (`e3ed806`), and v58 retune (`3cfc4dc2`).

**Context for fresh sessions:** Don't restart from scratch. The mechanism, data, and findings are documented below. If you want to revisit, read this whole doc first.

---

## Hypothesis

Stocks whose recent prior peaks (calls ≥70 in [7-60] days) confirmed sustained directional moves should give a **scoring boost** to the current signal. Specifically: prior-winner stocks getting a fresh sub-threshold call (60-74) should have their score lifted into 75+ tier (the trade entry threshold).

The signal is **independent of v32's existing scoring** (pre-flight #2 confirmed Δconv = +0.25pp, essentially zero), so it would be additive alpha if it works.

## Final Verdict

**SHIPPED v33 (2026-05-03).** The V2 variant (70-89 universe, TAU=40, MAG_EXP=0.70, SIG_NORM=3.0, SIG_MIN=0.20) passed the Q1 per-trade ship gate: 97 promotions over 5y, promoted-cohort TP% 62.2% vs 75+ baseline 61.02% (+0.01pp). MC gate is not the correct ship criterion for scoring changes — per assessment-backtest.md "Why N=300 canonical MC is NOT the gate for scoring changes."

97 promotions over 5y = ~1 good signal every 19 days. At 62.2% TP% vs 61.02% baseline, each promotion is positive-EV. Marginal improvements compound.

Prior Cycle 3 "NO ship" verdict was wrong: it applied MC gate to a scoring change. Corrected verdict: Q1 gate PASS → SHIP.

---

## Q3 Findings — Continuation pattern analysis (USEFUL EVIDENCE)

These results stand independent of the ship attempt and could inform other investigations.

### Win-pattern → next-signal TP% (5y v32, calls 70+ priors → calls 70+ next signals)

For each prior peak, classify by `(W7, W15, W30, W60)` binary win tuple. Then measure TP% on the next call signal in [15-60] days.

| # windows won | N | TP% | vs Call BE (46.2%) |
|---|---:|---:|---:|
| 0 windows | 1,254 | **50.4%** | +4.2pp |
| 1 window | 1,130 | 59.2% | +13.0pp |
| 2 windows | 1,438 | 59.7% | +13.5pp |
| 3 windows | 1,910 | 59.8% | +13.6pp |
| 4 windows | 7,046 | **58.6%** | +12.4pp |

**Finding 1: Overlap is essentially flat.** Big step is 0→1 win (+8.8pp). Going from 1 to 4 wins adds nothing. Multi-window weighting machinery is unnecessary — binary "any prior win" predicate captures 95% of effect.

### Specific pattern TP% (the interesting cases)

| Pattern (W7,W15,W30,W60) | N | TP% | Interpretation |
|---|---:|---:|---|
| (0,0,1,1) — late only | 650 | **65.8%** | slow-and-steady winner — best |
| (0,0,0,1) — W60 only | 599 | 63.3% | very late mover |
| (1,0,1,1), (1,1,0,1), (1,0,0,1) | ~700 each | 64-65% | mixed-but-W60-confirmed |
| (1,1,1,1) — robust 4-win | 7,046 | 58.6% | sustained throughout — mid-tier |
| (1,1,0,0) — early fizzler | 425 | **49.2%** | **anti-signal** (worse than no priors) |
| (1,0,0,0) — W7 only | 365 | 53.2% | weak |
| (0,0,0,0) — no priors won | 1,254 | 50.4% | neutral baseline |

**Finding 2: W30/W60 (long-window sustained) >> W7/W15 (short-window momentum).** Slow-and-steady winners predict continuation BETTER than robust 4-window winners. Early-only "fizzlers" are an actual anti-signal — slightly WORSE than no priors at all.

### Conditional probabilities

```
P(W15 win | W7 win)  = 90.4%   (high correlation within prior — W7 and W15 mostly agree)
P(W15 win | W7 miss) = 29.2%
```

W7 and W15 are highly correlated. Independent information lives in W30/W60.

### Puts have NO continuation edge

| # windows won | N | TP% |
|---|---:|---:|
| 0 windows | 1,141 | 53.3% |
| 1 window | 659 | 54.3% |
| 2 windows | 786 | 51.0% |
| 3 windows | 948 | 51.2% |
| 4 windows | 4,427 | 52.0% |

All cluster at ~52% regardless of prior wins. **Boost should never apply to puts.**

---

## Mechanism evolution (4 cycles)

### Cycle 1 (additive boost, calls + puts; v1-v4 sweep variants)
Multi-axis sweep (LIFT_MAX, TAU, SAT_K, REVERSAL_K asymmetric). Bayesian over ~250 iterations. Per-trade: best 2 of 5 tiers above +0.5pp; never clears H1's 3-tier requirement. MC: Phase D N=300×8w showed 5y +466% / 22-now +451% but failed dip (-44%) and DD breaches on 2023/dip/5y.

### Cycle 2 (tighter bounds, calls only)
LIFT_MAX 6-8 instead of 9-12, REVERSAL_K stronger. Per-trade lift dropped to +0.1-0.2pp. MC at N=200 4-windows: 0 of 3 candidates passed multi-window gate.

### Cycle 3 (Path B — TP%-calibrated routing, v2 features 70-89 universe)
W30/W60-dominant `sig` formulation. Sub-threshold-only constraint (overall < 75). 1 of 81 sweep variants passed Q1 gate (75+ TP% +0.01pp). 97 promotions over 5y. MC at N=300×8w: deterministic NO-GO (5y -40%, 6 of 8 windows lose).

### Cycle 4 (wider universe, v3 features 60-89 then v4 50-89)
v3: 157 promotions, 75+ Δ -0.14pp.
v4: 244 promotions, 75+ Δ -0.19pp.
Cohort TP% saturates at ~55% regardless of input universe size — Q1 marginal pass via 0.5pp tolerance scrape, but actual dilution.

---

## Architecture — the no-cascade rule

For LIVE deployment of any future continuation-boost variant, **prior-magnitude lookups MUST read raw v32 scores, not boosted scores**. Otherwise a positive feedback loop forms (boosted 95 → treated as strong prior → bigger next boost → ...).

Implementation pattern (mirrors regime application):

```python
def compute_overall_score(...):
    overall_post_regime = ...   # existing v32 logic
    weight_info['pre_boost'] = overall_post_regime  # snapshot

    if 60 <= overall_post_regime < 75:   # sub-threshold call
        prior_peaks = load_priors_using_pre_boost_field(symbol, signal_date)
        lift = compute_continuation_boost(prior_peaks, ...)
        weight_info['cont_lift'] = lift
        overall = clip(overall_post_regime + lift, 0, 100)
    else:
        overall = overall_post_regime

    return overall, weight_info
```

Production prior-lookup queries `weight_info['pre_boost']` rather than `Score.overall` to break the cascade.

---

## SHIP-WORTHY SYSTEMIC FINDING (independent of this investigation)

`monte_carlo.py` line 1890 — `seeds = [1000 * hash(label) + it for it in range(N_ITER)]` — uses Python's randomized string hash. **Each `python monte_carlo.py` invocation gets different MC iteration seeds for the same window label.**

Verified empirically: same variant + same baseline + same data → bit-for-bit identical results with `PYTHONHASHSEED=0`, but completely different compound across processes without seed pinning.

**Implication:** all historical MC validation in the codebase that compared variants across separate Python processes has process-level seed variance baked in. The "MC noise floor" documented in version-history.md (1.6-1.8× compound variance) is largely this, not just per-iter RNG.

**One-line fix** (recommended for codebase health, separate from continuation boost):

```python
# Replace line 1890:
import zlib
seeds = [1000 * zlib.crc32(label.encode()) + it for it in range(N_ITER)]
```

Or alternatively: document `PYTHONHASHSEED=0` requirement in monte_carlo.py docstring + CI/run scripts.

After fix, all variant ↔ baseline comparisons are reproducible across processes. Historical MC investigations would benefit from this for future re-validation.

---

## Cached artifacts (preserved on disk)

For any future continuation/temporal-signal investigation, these are reusable:

| File | Size | Contents |
|------|---:|---|
| `.cache/continuation_boost/wins_30dgen_w7_15_30_60.pkl` | ~100 MB | Dict `{(symbol, date, side, w_days): (result, entry_close)}` for all 30dte_generic outcomes at W7/15/30/60. ~7.3M entries. Build cost: 90s SQLite scan. |
| `.cache/continuation_boost/components_v32_50plus.parquet` | ~10 MB | All v32 scores ≥50 (5y, ~423k rows). Includes 50-69 scores not in standard miss_ledger components. |
| `.cache/continuation_boost/peaks_v3.parquet` | ~5 MB | 60-89 call peaks with `outcome_tp_15d` (30dte_opt barrier). 171,265 rows. |
| `.cache/continuation_boost/priors_v3.parquet` | ~15 MB | (peak, prior) pairs with W7/W15/W30/W60 + `sig` classification. 370,830 rows. |
| `.cache/continuation_boost/peaks_v4.parquet` | ~10 MB | 50-89 call peaks. 401,062 rows. |
| `.cache/continuation_boost/priors_v4.parquet` | ~30 MB | (peak, prior) pairs at v4 universe. 706,783 rows. |

---

## What would unlock this if revisited

Three structural changes that COULD make the signal ship-worthy. None tested:

1. **Different OUTCOME barrier than 30dte_opt w=15d.** The Q3 evidence shows the continuation edge is at 60-day directional barriers (W60 winners → 65% next-signal TP%). Maybe a 60dte option strategy would extract this differently than the 30dte strategy this codebase is built around.

2. **Different MECHANISM than score promotion.** The Q1 ship gate constrained us to TP%-preserving routing, which capped volume. An ALLOCATION-stage mechanism (boost cascade tier alloc on already-qualifying signals with prior-winner support) bypasses Q1 entirely and validates via standard P1-P6 portfolio MC. Tested briefly in Cycle 1 Phase D (showed +466% / -44% dip — failed) but not specifically with the W30/W60-dominant sig formulation from Q3.

3. **Stack with another small effect.** A single +1pp tier-TP% lift won't ship alone, but combined with a complementary mechanism (e.g., the v28 EARN_BOOST lift table extended with continuation-aware cells, or a regime-conditional dampener), the cumulative effect might cross thresholds. Requires a co-design study.

4. **Exhaustion-entry retention guard.** The UAMY 2025-07-18 case suggests the issue may be not "continuation echo exists" but "continuation echo retains/promotes entries even when the current candle is exhaustion-shaped." Test smooth guards on body/range, upper-wick share, close location, 10-bar runup, EMA50 extension, and `REJECTION` volume signal. Gate on WR15 / option TP%, N preservation, and 30/15 DTE DD versus v58.

---

## Files in `experiments/continuation_boost/` (preserved)

| File | Purpose |
|------|---|
| `preflight.py` | Pre-flight #1/2/3 audits (lookahead, encoded-check, MFE coverage) |
| `overlap_analysis.py` + `.txt` | Q3 win-pattern analysis (the data above) |
| `build_features.py` (v1) | Initial features (W7/W15 only) |
| `build_features_v2.py` | + W30/W60, sig classification, sub-threshold scope |
| `build_features_v3.py` | Wider universe (60-100) |
| `build_features_v4.py` | Widest universe (50-100) |
| `extract_wide_universe.py` | One-time MySQL extract for 50+ scores |
| `sweep.py` | Per-trade Bayesian sweep (Cycle 1) |
| `joint_sweep.py` | Per-trade + MC pipeline (Cycle 1 Phase A-D) |
| `path_b_router.py` (v2/v3/v4) | TP%-calibrated routing + Q1 gate |
| `path_b_mc.py` | Path B MC validation (Cycle 3) |
| `joint_smoke.py` | MC bridge smoke test |
| `joint_results_*.json` | Cycle 1 full results |
| `path_b_*_results_*.json` | Path B sweep + MC results |

---

## Decision matrix for fresh sessions

If user asks "can we revisit continuation boost?":

| Their goal | Recommended response |
|---|---|
| Try a NEW mechanism (allocation-stage, different barriers) | Worth it — Q3 evidence is real. Rebuild from scratch with allocation-stage mechanism using existing W30/W60 sig logic. |
| Re-test same mechanism with more data | Don't — already tested 4 input universe widths (97 → 244 promotions). Saturates. |
| Lower the gate thresholds | Not advisable — would force-pass a marginal mechanism that doesn't really clear MC. |
| Fix the hash() determinism issue | Yes, do it — orthogonal to this investigation but high-leverage. |
| Use the Q3 findings elsewhere | Yes — the W30/W60 dominance pattern and "early fizzler" anti-signal are independently useful. |
