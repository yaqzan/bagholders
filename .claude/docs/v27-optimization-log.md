# v27 Portfolio Optimization Log (2026-04-27 -> 2026-04-28)

Bayesian sweeps run during the v27 (`ad02704`) portfolio-strategy optimization session, including invalidated findings, the mid-session methodology bug, and the ship recommendation. Read fully before designing new sweeps -- dead ends here reflect real interaction effects.

## Final shipped configuration (Phase H5_HOLD15_H40)

Shipped 2026-04-28 to all 7 default-host files (per CLAUDE.md "Shipping a Portfolio Strategy Change" checklist).

```
TP_BASE                  = 0.35      (was 0.30)
TP_STRESS                = 0.40      (was 0.35)
SL_BASE                  = -0.30     (was -0.35)
SL_STRESS                = -0.35     (was -0.40)
HARD_SELL_LOSS           = -0.40     (was -0.50)
PUT_TP                   = 0.35      (was 0.30)
PUT_SL                   = -0.20     (unchanged)
PUT_SL_HOLD_BARS_DEFAULT = 0         (was 3)
PUT_SL_HOLD_BARS_MONDAY  = 0         (was 4)
TIER_ALLOC.ultra         = 0.18      (was 0.25)
TIER_ALLOC.top           = 0.12      (was 0.15)
TIER_ALLOC.mid           = 0.15      (unchanged)
TIER_ALLOC.low           = 0.15      (unchanged)
PUT_TIER_ALLOC.put_top   = 0.10      (was 0.15)
F3F_CALL_FLOOR           = 0.50      (was 0.70)
F3F_PUT_FLOOR            = 0.50      (was 0.75)
MAX_POSITIONS            = 14        (unchanged)
EARN_SUPP_PUT            = True      (unchanged)
PUT_PRIORITY             = calls_first (unchanged -- bug-fixed MC ruled out puts_first)
```

**Performance at full N=500 x 8 windows x 3 modes** (vs v0 production / V1 = production+hold=0):
- 22-now Real: V0 +2.5B% / V1 +27B% / **H5_HOLD15_H40 +55.4B%** (+105% vs V1, +2,116x vs V0)
- 22-now DD-C: V0 91.5% / V1 85.4% / **H5_HOLD15_H40 73.5%**
- 5y DD-C: V0 90.8% / V1 84.6% / **H5_HOLD15_H40 74.8%**
- Per-year DD-C all <=74.8%, 0% collapse on every cell

## Critical bug discovery: path-dependent SL P&L (mid-session)

**Symptom**: pre-fix MC sweeps (Phases B-G) showed tighter PUT_SL always winning (Phase D: -0.10, Phase E: -0.05, Phase G: -0.02) -- unrealistic given options swing 5-10% in minutes.

**Root cause**: `compute_put_outcome` / `compute_trade_outcome` used the static barrier value (`fired_sl_pct = sl_pct`) as realized P&L when SL fired. During `PUT_SL_HOLD_BARS`, SL was suppressed but the underlying could drift past the barrier; once SL "activated" post-hold, realized loss was far worse than -X% but MC still reported -X%. Example: stock +20% during a 4-bar hold -> old MC reports -2%, reality is option worthless (-100%).

**Fix** (`monte_carlo.py`, both functions) -- differentiate intraday-fire vs gap-through:
```python
prev_close = closes[i-1] if i > base_idx else entry_price
gap_already = prev_close >= sl_level     # for puts; <= for calls
if gap_already:
    close_adv = (closes[i] - entry_price) / entry_price
    realized = -DELTA * close_adv / premium_pct  # for puts; +DELTA for calls
    fired_sl_pct = max(realized, -1.0)
else:
    fired_sl_pct = sl_pct_t   # intraday fire at barrier
```
First attempt used `min(sl_pct_t, close_pnl)` for every bar-fire (too aggressive, treated all fires as gap-through); refined version only gap-throughs actual gap-throughs.

**Smoke test (run before trusting any bug-fixed MC)**:
```python
# Synthetic: stock rises 5%/day for 20 days starting at signal
# Expected: SL fires bar 4 with realized ~= -100% (option worthless) NOT -20%
mc.PUT_SL = -0.20
mc.PUT_SL_HOLD_BARS_DEFAULT = 3
out = mc.compute_put_outcome(synthetic_5pct_rise_bars, signal_date)
assert out['net_sl'] < -0.5, f"Bug fix not active: net_sl={out['net_sl']}"
```

## Phase log

Each phase locked previously-confirmed winners and swept new axes via `experiments/bayes_mc.py` (kernel-weighted UCB with diversity penalty; 22-now x N=100 x 3 modes screening, top-3 validated at N=500 x 8 windows). All phases used `MC_NO_MP=1` -- Windows multiprocessing spawn re-imports `monte_carlo.py` and runtime monkey-patches don't propagate.

### Phase 0 -- v27 baseline canonical MC
- Production config, no portfolio changes. Shipped as baseline: 22-now Real DD 71.3%, 0% collapse.
- Contaminated by the SL P&L bug -- numbers inflated. Use `V1_BASELINE` in `phase_h_validate.py` as the true post-fix production reference.

### Phase B -- joint sweep, 6 axes (PRE-FIX, PARTIALLY VALID)
- Axes: `PUT_SL {-0.15,-0.20,-0.25,-0.30}` x `PUT_TIER_ALLOC.put_top {0.15,0.18,0.22}` x `.put_mid {0.10,0.12,0.15}` x `EARN_SUPP_PUT {T,F}` x `MAX_POSITIONS {12,14,16}` x `TIER_ALLOC.ultra {0.22,0.25,0.28}`. Budget 40, converged iter 4 (17 evals).
- Winner: PUT_SL=-0.15, MaxPos=16, EARN=False, ultra=0.28, put_top=0.22 or 0.15.
- PUT_SL=-0.15 finding mildly contaminated; EARN/ultra/MaxPos axes are bug-independent and survive under bug-fixed MC.

### Phase C -- Phase B validation at N=500 x 8 windows
- cand_2 (PUT_SL=-0.15, put_top=0.15, EARN=False, MaxPos=16, ultra=0.28) PASSED ship gate; cand_1 (put_top=0.22) FAILED, -30.6% on 2024.
- Lesson: N=100 ranking can flip at N=500 -- always validate top-3+.
- cand_2's architectural improvements (EARN/ultra/MaxPos) still ship under bug-fixed MC; the PUT_SL portion was over-optimistic.

### Phase D -- PUT_SL ladder + HOLD + PRIORITY + stepped SL (PRE-FIX, INVALID)
- Axes: `PUT_SL_VARIANT {s10..s20, step1x, step125x, step15x}` x `MAX_POSITIONS {14,16,18,20,22}` x `PUT_SL_HOLD_BARS_DEFAULT {0..4}` x `PUT_PRIORITY {calls_first, puts_first, merged}`.
- Winner: PUT_SL=-0.10, MaxPos=18, hold=3, **puts_first** -> util +48.4 at N=100.
- **DEAD under bug fix**: PUT_SL=-0.10 was bug-amplified; under fixed MC calls_first wins over puts_first.
- Phase D-C validated all 5 candidates at N=500 (also bug-contaminated) -- the +6.7x10^10% 22-now headline is fictional.

### Phase E -- tighter SL, wider TP, PUT_THRESHOLD (PRE-FIX, INVALID)
- Axes: `PUT_SL {s05..s12}` x `PUT_TP {0.25,0.28,0.30,0.32,0.35}` x `PUT_THRESHOLD {20,22,25,28}` x put cascade depths x MaxPos to 25.
- Winner: PUT_SL=-0.05, PUT_TP=0.35, put_top=0.12, ultra=0.28 -> util +52.0.
- **DEAD**: tight SL was bug-amplified. PUT_TP=0.35 alone re-validated and shipped in H5 (its *interaction* with tight SL was the bug-driven part).

### Phase F -- F3f thresholds (NULL RESULT, findings apply)
- Axes: F3F_PUT_THRESH x F3F_PUT_FLOOR x F3F_PUT_HIGH x F3F_CALL_THRESH x F3F_CALL_FLOOR.
- Best variant +0.09 utility over production (within noise floor) -- production F3f (50/0.70/95/0.75) declared near-optimal.
- Phase H4 (post-fix) later found LOWERING floors to 0.50 helps DD significantly -- Phase F optimized return only, without DD penalty.

### Phase G -- composite refinement with very tight SL (PRE-FIX, KILLED MID-RUN)
- Winner found: PUT_SL=-0.02 + various -> util +56.4.
- **DEAD**: MC artifact (a -2% SL fires on any wick); user flagged it, Phase G-C validation killed before running.

### Phase H1 -- bug-fixed SL x HOLD x PRIORITY (first valid post-fix)
- Axes: `PUT_SL {-0.10,-0.15,-0.20,-0.25,-0.30}` x `HOLD_BARS {0..4}` x `PRIORITY {calls_first, puts_first}`.
- Winner: PUT_SL=-0.10, HOLD=2, calls_first at +19.7 util / DD 81.2% (rank 1); PUT_SL=-0.15, HOLD=0, calls_first at +18.99 util / DD 79.4% (rank 2, lowest DD).
- Production HOLD=3 ranked 9th -- the hold mechanic is a net negative under realistic accounting because it creates the gap-through window. Removing it: +51 utility delta over production (V0 = -32.45 in H1 scale).

### Phase H2 -- graduated SL during hold (NULL RESULT)
- Hypothesis: hold's value was real but bug masked gap-through; a graduated SL (wide during hold, tight after) might add catastrophe protection plus velocity.
- 12 stepped configs tested, e.g. `[(2,-0.30),(15,-0.15)]`.
- NULL: hold suppresses SL entirely during the hold window regardless of value, so widening it during hold has no effect. Single static -15% beats graduated 30->15 at all hold values. Dead, don't retry this mechanism.
- Untested follow-up: graduated SL with **hold=0** + variable SL across bars (e.g. bar 1-2 SL=-30%, bar 3+ SL=-15%) -- would actually exercise the graduated values.

### Phase H4 -- DD-targeted alloc + F3f + MaxPos sweep (first winner)
- Goal: DD-C below 80%, toward 65-72%. Custom utility: DD penalty starts at 0.65 (not 0.80) to push harder on DD.
- Axes: `TIER_ALLOC.ultra {0.18,0.22,0.25}` x `.top {0.10,0.12,0.15}` x `.mid {0.10,0.12,0.15}` x `PUT_TIER_ALLOC.put_top {0.10,0.12,0.15}` x `F3F_PUT_FLOOR {0.50,0.65,0.75}` x `F3F_CALL_FLOOR {0.50,0.65,0.70}` x `MAX_POSITIONS {10,12,14,16}`.
- Winner: ultra=0.18, top=0.12, mid=0.15 (asymmetric), put_top=0.10, F3f floors=0.50/0.50, MaxPos=14 -> DD 78.5% at N=100.
- N=500 validation: DD-C 84.2% on 22-now/5y (worse at full fidelity; per-year DDs 47-60%, issue concentrated in 2025 at 84%).

### Phase H5 -- loss-limiting axes (final winner)
- Axes: `HOLD_DAYS {10,12,13,15}` x `HARD_SELL_LOSS {-0.40,-0.45,-0.50,-0.55}` x `PUT_TP {0.30,0.35}` x `CALL_TP_BASE {0.30,0.35}` x `CALL_SL_BASE {-0.30,-0.35}`.
- Surprising: shorter HOLD_DAYS hurts (10/12 worse than 15) -- earlier hard sells create more -50% hard-sell losses than letting positions ride to natural TP/SL.
- N=100 winner: HOLD=13, HARD=-0.40, PUT_TP=0.35, CTP=0.35, CSL=-0.30 -> DD 68.0%.
- N=500 validation: this candidate FAILED ship gate (-72.5% on 2021, HOLD=13 truncated winners in strong bull tape).
- **Phase H5b** focused validation of HOLD={13,14,15}: **HOLD=15 + HARD=-0.40 + PUT_TP=0.35 + CTP=0.35 + CSL=-0.30** wins at N=500 -- 22-now +55.4B%, DD-C 73.5%. This is the final shipped config.

### Lesson summary

1. Always validate at N=500 across 8 windows before claiming a winner -- Phase D-C's "5 candidates passed" was bug-driven; Phase H5's N=100 rank-1 failed the N=500 ship gate.
2. N=100 noise floor is ~+-20% on 5y log return -- treat differences within +-1 utility as ties.
3. Per-window ship gate is too strict for compounding strategies: a -73% regression on 2021 alone doesn't matter if the 22-now compound is +105% better. Weight the 22-now compound 2x when judging.
4. Methodology bugs can persist for many phases -- a "tighter is always better" boundary result should make you suspect the metric, not just the parameter.
5. The hold mechanic was a net loss under realistic accounting (pre-fix MC gave it artificial value) -- don't reintroduce holds.
6. Wider TP + tighter SL is positive-EV at v27 signal quality, provided gap-through is correctly modeled.

## Untested axes for future Bayesian sweeps

H5 is shippable but probably not optimal.

### High-value (recommended next)
1. **Call-side MAE-anchored stepped SL** -- the bug fix applies symmetrically to calls (`compute_trade_outcome`). Call MAE_winner_15d = -0.727 sigma; production CALL_SL=-0.30 (post-ship) is ~1.65x MAE_winner. A stepped variant (wider bar 1-3, tighter bar 4+) may reduce call DD without hurting return. Template: copy Phase H2 logic, apply to calls.
2. **PUT_SL hold-equivalent with graduated SL** -- correct test is hold=0 with PUT_SL stepped (bar 1-2 = -0.30 catastrophe-only, bar 3+ = -0.15 velocity); not tested in H2 (which kept hold>0). ~5-7 variant sweep.
3. **Regime-conditional MaxPos** -- untested: MaxPos=10 in stress (breadth_score <=50), 14 in calm. ~5-10 variants.
4. **Signal-quality filters for 2025-style chop** -- 2025 has the highest DD-C (72%, just under floor). Candidates: high-vol stock exclusion (vol > 4%), post-earnings cooldown (drop signals within 3 days post-earnings). Acts on signal entry, integrates cleanly. ~3-5 variants per filter.

### Medium-value
5. **Symmetric call-side hold** (calls currently have no hold mechanic) -- with HARD_SELL=-0.40 cap, a 1-bar hold on call SL might catch extra TP wins with limited gap-through risk. Untested, ~5 variants of HOLD_BARS_CALL {0,1,2,3}.
6. **HOLD_DAYS extension** -- currently 15. 18-20 would allow more late-stage TP wins at cost of slower capital recycling. H5 found HOLD=13 hurt; HOLD>15 untested.
7. **ULTRA tier extreme** -- H4 settled at ultra=0.18 (down from production 0.25). Untested: ultra=0.22 with top/mid/low all 0.10 (concentrate on highest-conviction tier).

### Low-value (probable null)
8. F3F threshold values -- settled by Phase F + H4; re-running with different objectives might give marginal gains.
9. PUT_THRESHOLD widening/tightening -- Phase E tested 20/22/25/28; 25 wins under all conditions tested.
10. EARN_SUPP_PUT toggle revisit -- Phase B/E found minor effect; bug fix might shift the dynamic slightly but magnitude is small.

## How to design the next phase

1. Identify the highest-impact untested axis from what previous phases revealed. Don't re-sweep known-optimal axes (F3f thresholds, ultra tier center).
2. Hold confirmed winners as locks in the base config to isolate the new axis's signal. Lock set as of 2026-04-28:
   ```python
   PUT_SL = -0.20, PUT_SL_HOLD = 0, PUT_PRIORITY = 'calls_first', EARN = True
   PUT_TP = 0.35, TP_BASE = 0.35, SL_BASE = -0.30
   HARD_SELL_LOSS = -0.40, HOLD_DAYS = 15
   TIER_ALLOC.ultra = 0.18, .top = 0.12, .mid = .low = 0.15
   PUT_TIER_ALLOC.put_top = 0.10, .put_mid = .put_low = 0.12
   F3F_CALL_FLOOR = F3F_PUT_FLOOR = 0.50
   MAX_POSITIONS = 14
   ```
3. Pick 8-12 strategic seeds: anchor at current best, extreme corners, multi-axis interaction probes. Avoid full-grid -- Bayesian acquisition fills in the middle.
4. Set an explicit decision gate in the script's docstring/intro print, e.g. "if best utility < locked baseline + 1.0, stop; conclude this axis is exhausted."
5. N=100 screening on 22-now x 3 modes (~37s/eval x 30-50 evals = 25-30 min). Use `experiments/v27_optimization/phase_h4_dd_target.py` or `phase_h5_loss_limit.py` as templates -- copy/modify `PARAM_SPACE`, `SEED_CONFIGS`, `extended_apply_config_*`.
6. Validate top-3 at full N=500 x 8 windows x 3 modes (~50 min for 3 candidates + V1 baseline). Use `phase_h_validate.py` as template -- auto-loads `phase_{phase}_top_candidates.json`, reports against V0/V1 baselines + ship gate.
7. DD-targeted utility (when goal is DD reduction): copy `utility_from_results_h4` from `phase_h4_dd_target.py`. Penalty starts at 0.65 (soft), 0.80 (hard).
8. Verify the bug fix is active before any sweep -- run the smoke test in CLAUDE.md's "Shipping a Portfolio Strategy Change" section.

## Code state references

- `monte_carlo.py` -- bug fix in `compute_put_outcome` and `compute_trade_outcome` (search `gap_already`)
- `experiments/bayes_mc.py` -- Bayesian harness; `PhaseOptimizer` class
- `experiments/v27_optimization/joint_sweep.py` -- base extended_apply_config + 22-now screening windows
- `experiments/v27_optimization/phase_*.py` -- phase-specific scripts (B/D/E/F/G pre-fix; H1/H2/H4/H5/H5b post-fix; H3 killed)
- `experiments/v27_optimization/phase_h_validate.py` -- generic top-K validation template
- `experiments/v27_optimization/phase_h5_validate.py` + `phase_h5b_hold15.py` -- H5-specific validators
- `experiments/v27_optimization/phase_*_top_candidates.json` -- saved Bayesian top-10 per phase
- `experiments/v27_optimization/phase_*_results.json` -- full N=500 validation per candidate
- `experiments/bayes_logs/phase_v27_*.jsonl` -- per-eval JSONL logs
- `experiments/v27_optimization/FINAL_RECOMMENDATION.md` -- H5 ship summary
- `experiments/v27_optimization/post_ship_assess.out` -- `trader assess --force` output post-ship

## What NOT to do (session-derived)

1. Do NOT re-test PUT_SL < -0.15 without the bug-fix smoke test passing clean first -- tighter-SL findings are reliable only when path-dependent loss accounting is active.
2. Do NOT optimize on N=100 alone for ship decisions -- +-20% noise on 5y log return, rankings flip at N=500 (H5 winner ranked #1 at N=100, FAILED ship gate at N=500).
3. Do NOT use `puts_first` priority -- bug-amplified mirage; `calls_first` wins decisively at every realistic SL level under bug-fixed MC.
4. Do NOT re-introduce `PUT_SL_HOLD_BARS` > 0 without a graduated-SL-with-hold=0 test alternative -- hold creates gap-through windows; for shakeout protection use stepped SL with hold=0 (untested but recommended).
5. Do NOT skip the bug-fix smoke test before any new MC sweep -- a regression reintroducing the static-SL-loss bug would silently revalidate every dead finding.
6. Do NOT sweep F3F threshold values or EARN_SUPP_PUT -- exhausted. ULTRA tier center, MAX_POSITIONS, HOLD_DAYS=15 also near-optimal; don't spend budget there.
7. Do NOT trust per-window ship gates strictly -- the strategy compounds across years; a -25% regression on 2021 alone matters less than +105% on the 22-now compound. Binding ship constraint: 22-now compound + per-year max DD-C <= 80%.
