# equity_wave — Track A: regime/equity aggression-scaling wave (B2 harness)

Portfolio-stage research only. **NO `ALGORITHM_VERSION` bump, NO `trader recalculate`,
NO scoring-code change.** Uses the existing v74 Apex score rows. Active state:
v74 Apex / 30 DTE / calls-only / TP +30 / SL −70 / dead-hold (collapse-PREVENTING,
never disabled) / 5 Stage-3 DD levers (RXDD/SVR/MWDD/TVDD/BDIV) / uncapped / $50k base.

## What this is

A **smooth, bounded aggression-scaling multiplier** on call `alloc_frac`, in two
flavours that get A/B'd against each other:

- **(A) market-context regime-strength index** — combines the R2 per-date market maps
  (VIX, breadth_score, McClellan, TRIN, breadth-divergence, semivol_r) into one
  `strength ∈ [0,1]`, then maps `strength → aggression ∈ [agg_min, agg_max]`. "Does the
  MARKET say the regime is strong → size up; weak → withhold." `wave.regime_strength_index`
  + `wave.strength_to_aggression`.
- **(B) equity-curve-NATIVE scaler** — driven ONLY by the running equity curve: running
  drawdown-from-peak + a weekly-downsampled `{flat, skyrocketing, drawing_down, recovering}`
  state classifier, with explicit flat-curve whipsaw suppression (dead-band + hysteresis,
  no raw daily oscillator). `wave.EquityNativeScaler`.

Both return a **bounded** multiplier (default [0.40, 1.50]) so the engine's premium caps
and collapse floor can never be violated by a runaway wave — withholding ≠ halting, boost
is capped.

**The core A/B question (`analyze.py`):** given per-path MC daily equity curves, does ANY
equity-native signal (dd / weekly state / curve momentum) predict the NEXT-window
return/DD *beyond* the market-context strength index? The cautionary case
(Jan 2025 +51% → Mar −28%) is the null we expect to confirm — curve momentum does not
predict the next jump. The analyzer answers it quantitatively (nested-model OOS R²,
folds split by path so curve noise can't leak train→test).

## Files

| File | Role |
|------|------|
| `wave.py` | PURE primitives: strength index, aggression map, drawdown transform, weekly state classifier, equity-native scaler, weekly downsample. No MySQL/MC/engine import. |
| `test_wave.py` | 72 synthetic unit tests (range/monotonicity/NaN-safety/flat-whipsaw/hysteresis/floor-clamp). **RUN — 72/72 GREEN.** |
| `analyze.py` | The equity-vs-market A/B analyzer + a synthetic `--selftest`. **RUN — selftest GREEN** (correctly flags a constructed curve-predictive case and a constructed null). |
| `README.md` | this file |

## Run the tests now (no MySQL, no MC)

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/equity_wave/test_wave.py     # -> ALL GREEN (72/72)
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/equity_wave/analyze.py --selftest   # -> SELFTEST GREEN
```

`analyze.py --selftest` builds two synthetic curve populations and asserts the analyzer
reaches the right verdict on each:
- **null flavour** (market-strength drives returns, jumps independent of the curve):
  reports `NULL: equity-native adds nothing beyond market-context` (OOS gain inside the
  noise band), and finds strength does correlate with forward return.
- **curve-predictive flavour** (constructed deep-dd mean-reversion, no market strength):
  reports `EQUITY-NATIVE ADDS POWER` (positive OOS R² gain vs constant), dd correlates.

## The regime-strength formula (documented)

`strength = ( Σ_i w_i · s_iᵖ / Σ_i w_i )^(1/p)`, a weighted **power-mean** with
`p = blend_p = 0.4`. p=1 is the arithmetic mean; p→0 → weighted geometric mean (one
near-zero input crushes the result). p=0.4 gives the asymmetry the strategy wants: a
**strong** regime requires broad agreement across inputs, but a **single alarm** (crash
VIX, capitulation breadth) can withhold aggression on its own. Each `s_i` is a smooth
bounded sub-score (HIGHER = stronger regime), defaults in `StrengthParams`:

| input | sub-score orientation | default shape |
|---|---|---|
| VIX | low = strong | ramp 1→0 over [14, 30] |
| breadth_score | high = strong | ramp 0→1 over [35, 70] |
| McClellan | positive momentum = strong; flat≈mid; deep-neg capitulation = low | ramp 0→1 over [−40, +40] |
| TRIN | froth/healthy-up (<0.85) = strong; neutral trough (≈1.0-1.3) = weak; panic = weak | piecewise |
| breadth-divergence (spy_from60h, brd_det10) | pre-top divergence (near high + breadth rolling over) = penalty | `1 − maxpen·prox·gauss(det; 7.7, 3.5)` |
| semivol_r | sweet spot [0.70, 1.25] = strong; euphoric-low / crash-high = weak | symmetric band-pass |

Missing/None/NaN inputs contribute a **neutral 0.5** (or skip if weight 0). All-unknown →
strength ≈ 0.5 → aggression ≈ 1.0 (baseline). Every output is provably in [0,1] / bounded
(NaN-safe `_clip`).

`strength_to_aggression`: `0.5 → ~1.0`, `0 → agg_min`, `1 → agg_max`, monotone, optional
`gamma` (convex = only the very-strong get the boost). Defaults `agg_min=0.50, agg_max=1.50`.

## The equity-native flat-whipsaw guard (documented)

`WeeklyStateClassifier` operates on the **weekly-downsampled** curve (Fri-close) and:
- **dead-band** (`flat_band=0.015`): a week-over-week |log-return| below the band → FLAT,
  regardless of prior state. A long flat stretch CANNOT be relabeled sky/down by noise.
- **hysteresis** (`enter=0.04`, `exit_=0.015`): entering SKY/DOWN needs to clear `enter`;
  you stay until you fall back inside `exit_`. No single-week flip-flop.
- **sky vs recovering**: a strong rise near new highs (dd ≤ `recover_dd`) = SKY; a strong
  rise while still below a recent peak = RECOVERING.

The classifier is *descriptive*, not predictive — `analyze.py` is what tests whether the
state carries forward signal. Defaults encode a defensive prior: `agg_sky=1.00` (do NOT
chase the last jump — the cautionary case), `agg_down=0.60` (protect during a drawdown),
plus a continuous `drawdown_space(dd)` overlay so a deepening DD damps aggression smoothly.

## Wiring point (for a later MC arm — NOT done this pass)

R2 pinpointed the call alloc multiplier at **`monte_carlo.py:2668`**:

```python
alloc_frac = TIER_ALLOC[tier] * reg_scale_c * dd_scale * sat_scale * rxdd_scale \
           * svr_scale * mwdd_scale * tvdd_scale * dqt_scale * vxmd_scale \
           * bdiv_scale * spread_tilt_scale
```

A regime/equity wave arm adds one more env-gated factor `* aw_scale` exactly like the
existing levers (default OFF ⇒ `aw_scale==1.0` ⇒ baseline byte-identical). For arm (A)
the per-date market maps are already loaded each day at `monte_carlo.py:2495-2502`
(`rxdd_vix_today`, `mwdd_mcc_today`, `tvdd_trin_today`, `bdiv_today`) plus `breadth` via
the `reg_scale_c` path and `semivol_r` via `_svr_load()` — `regime_strength_index(...)`
consumes those directly. For arm (B) the running `dd` is already in scope at that line;
the weekly state needs the in-flight `daily_path`/`peak_value` (both already tracked).

## DAILY-PATH EXPORT — the one gap for the A/B analyzer's primary data source

The time-to-2x **objective** is already wired (B1): `run_single_sim` computes `t2x_bar`
(first bar equity ≥ 2×start, line 2467) and `t_50dd_bar` (first bar dd ≥ 50%, line ~2483),
and `MC_RETURN_PATHS=1` exports per-path `finals`, `dds`, `t1m_bars`, `t2x_bars`,
`t_50dd_bars`, `n_trading_days`, `starting_cash` (lines 3587-3594), carried into
`MC_RESULTS_JSON` (lines 3749-3758). So **median days-to-2x, P(2x before 50% DD), P(2x ever),
P(collapse)** are computable from existing output (see "Time-to-2x metrics" below).

`analyze.py`'s A/B, however, needs the **full daily equity curve** per path (to compute
forward-window return/DD and the equity-native predictors). That is NOT currently exported
— `daily_path` (`(date, value, peak, dd)`) is collected only when `collect_tape=True`
(heavy) and only the top-3 worst `_episodes` reach `result`. The documented additive
extension (~6 lines, default-OFF ⇒ byte-identical):

1. In `run_single_sim`, always build a lightweight equity series (downsample to weekly to
   keep it small): append `portfolio_value` each bar to a local `eq_series` (cheap; no
   `collect_tape` gate), and `result['eq_weekly'] = eq_series[4::5] + [eq_series[-1]]`.
   Gate it on `os.environ.get('MC_EMIT_CURVE')=='1'` so default runs are untouched.
2. In the `MC_RETURN_PATHS` block, add `result['eq_weeklies'] = [r.get('eq_weekly') for r in rs]`.
3. In the `MC_RESULTS_JSON` paths block, add `'eq_weeklies': r.get('eq_weeklies')`.
4. `analyze.py load_paths_from_json` already accepts `{"paths":[{"equity":[...],"strength":...}]}`
   and `{"daily_paths":[[...]]}` — a tiny adapter maps `eq_weeklies` → `paths`.

Weekly curves keep the payload small (N=100 × 5y ≈ 100×260 floats per window) and are
exactly what the weekly state classifier + forward-window math consume. The market
`strength` series per path can be appended the same way (one `regime_strength_index` call
per weekly bar from the same maps) so the A/B compares (A) vs (B) on identical anchors.

Until that lands, run `analyze.py --selftest` (validates the A/B logic on synthetic curves).

## Time-to-2x metrics from existing MC output (no extension needed)

Given a `MC_RESULTS_JSON` with `paths.t2x_bars` / `t_50dd_bars` / `finals` / `dds` /
`n_trading_days`, per config:
- **median days-to-2x (+P25/P75)** = percentiles of `[b for b in t2x_bars if b is not None]`
  (bars ≈ calendar/ (5/7) for days; report in trading bars or convert).
- **P(2x ever within horizon)** = `mean(b is not None for b in t2x_bars)`.
- **P(2x before 50% DD)** = `mean(t2x is not None and (t_50dd is None or t2x <= t_50dd))`
  over paths.
- **P(collapse)** = already in `p_coll`, or `mean(f <= 0.20*start for f in finals)`.

**Roll the start date** (so the metric means "if I start at a random point, how fast"):
the engine already supports an arbitrary single window via `WIN_START`/`WIN_END`/`WIN_LABEL`
(line 3706). A driver loops month-start dates across the 10y history, sets `WIN_START=<that
month>`, `WIN_END=<+horizon, e.g. +3y>`, runs N=100 with `MC_RETURN_PATHS=1
MC_RESULTS_JSON=<...>`, and POOLS `t2x_bars` across all start-months (each month × N seeds).
That randomizes both fill (seed) and start (month) — the objective the task asks for.

## Smoke / coarse A/B commands (off-market, via the task queue)

All heavy MC goes through the queue (CPU/MySQL admission); never raw background. Today is
2026-06-15 (Mon) — submit with `--window off_market` so a heavy sweep can't outrank the
scheduled `trader update` during market hours.

**Smoke (tiny real-data, single window, baseline vs regime-scaled, N=100):** the regime/equity
wave arm (`aw_scale`) is not yet wired into `monte_carlo.py` (this pass is the harness +
analyzer). The runnable smoke for THIS deliverable is the synthetic analyzer self-test
(above). Once the `aw_scale` arm + curve export land, the real-data A/B baseline-vs-scaled
smoke is:

```bash
# smoke: 1 window, N=100, emit weekly curves + per-path t2x, no DB persist
trader queue submit --priority high --db light --cpu 4 --restartable \
  --window off_market --dedup eqwave-smoke-baseline \
  --reason "Track A equity-wave smoke: baseline N=100 5y, curve+t2x export" \
  --env PYTHONIOENCODING=utf-8 --env MC_NO_DB_PERSIST=1 --env MC_RETURN_PATHS=1 \
  --env MC_EMIT_CURVE=1 --env N_ITER_OVERRIDE=100 --env WINDOWS_OVERRIDE=5y \
  --env MC_RESULTS_JSON=experiments/equity_wave/out/baseline_5y_n100.json \
  -- python monte_carlo.py
```

**Coarse A/B (N=100): baseline vs regime-strength-scaled vs equity-native-scaled, 8 windows.**
Three arms (the `AW_*` env knobs are the documented future wiring; OFF arm == baseline):

```bash
# arm 1 — baseline (wave OFF)
trader queue submit --priority high --db light --cpu 4 --restartable --window off_market \
  --dedup eqwave-coarse-baseline --reason "Track A coarse: baseline N=100 8w" \
  --env PYTHONIOENCODING=utf-8 --env MC_NO_DB_PERSIST=1 --env MC_RETURN_PATHS=1 \
  --env MC_EMIT_CURVE=1 --env N_ITER_OVERRIDE=100 \
  --env WINDOWS_OVERRIDE=2021,2022,2023,2024,2025,dip,22-now,5y \
  --env MC_RESULTS_JSON=experiments/equity_wave/out/coarse_baseline.json \
  -- python monte_carlo.py

# arm 2 — market-context regime-strength scaling (AW_MODE=market)
trader queue submit --priority high --db light --cpu 4 --restartable --window off_market \
  --dedup eqwave-coarse-market --reason "Track A coarse: regime-strength-scaled N=100 8w" \
  --env PYTHONIOENCODING=utf-8 --env MC_NO_DB_PERSIST=1 --env MC_RETURN_PATHS=1 \
  --env MC_EMIT_CURVE=1 --env N_ITER_OVERRIDE=100 \
  --env WINDOWS_OVERRIDE=2021,2022,2023,2024,2025,dip,22-now,5y \
  --env AW_ENABLED=1 --env AW_MODE=market \
  --env MC_RESULTS_JSON=experiments/equity_wave/out/coarse_market.json \
  -- python monte_carlo.py

# arm 3 — equity-curve-native scaling (AW_MODE=equity)
trader queue submit --priority high --db light --cpu 4 --restartable --window off_market \
  --dedup eqwave-coarse-equity --reason "Track A coarse: equity-native-scaled N=100 8w" \
  --env PYTHONIOENCODING=utf-8 --env MC_NO_DB_PERSIST=1 --env MC_RETURN_PATHS=1 \
  --env MC_EMIT_CURVE=1 --env N_ITER_OVERRIDE=100 \
  --env WINDOWS_OVERRIDE=2021,2022,2023,2024,2025,dip,22-now,5y \
  --env AW_ENABLED=1 --env AW_MODE=equity \
  --env MC_RESULTS_JSON=experiments/equity_wave/out/coarse_equity.json \
  -- python monte_carlo.py
```

Then the A/B read (no MC): point `analyze.py` at the baseline curve dump (it tests whether
the equity-native signal would have helped, independent of which arm ran):

```bash
PYTHONIOENCODING=utf-8 python experiments/equity_wave/analyze.py \
  --paths-json experiments/equity_wave/out/coarse_baseline.json --H 21 \
  --out experiments/equity_wave/out/ab_verdict.json
```

Bridge each queued job back: `trader queue wait <id> --timeout 3h` run with the harness
`run_in_background` flag.

**Drill (later, N=500) only on the frontier arm.** Do not run N=500 × all arms blindly —
the DD-signal noise floor is N=300; compound swings 1.6-1.8× at N<500. Pick the winning
arm from the coarse pass, re-run that arm + baseline at `N_ITER_OVERRIDE=500` on the same 8
windows, and (for the time-to-2x objective) add the monthly-rolling-start driver.

## Runtime estimate

- `test_wave.py` + `analyze.py --selftest`: **< 2 s each** (pure Python/numpy, no IO).
- One MC window at N=100, `--db light`, 4 cores: ~**2-5 min** (data bulk-loads once, then
  CPU-bound). 8 windows ≈ **15-40 min** per arm. Three coarse arms ≈ **45 min - 2 h** wall,
  run concurrently under the queue's admission (light DB, 4 cpu each).
- N=500 drill (winning arm + baseline, 8 windows): ~**1.5-3 h** per arm.
- Monthly-rolling-start driver (≈120 month-starts × N=100 × one window each): heaviest;
  scope to a horizon (e.g. +3y) and a coarser month step (quarterly = ~40 starts) first.

## Status

- **(1) regime-strength index + aggression map** — built (`wave.regime_strength_index`,
  `strength_to_aggression`), documented, unit-tested.
- **(2) equity-curve-native scaler** (drawdown-from-peak + weekly state classifier, flat-whipsaw
  handled) — built (`wave.EquityNativeScaler`, `WeeklyStateClassifier`, `drawdown_space`),
  unit-tested.
- **(3) A/B analyzer** (equity-native predictive power beyond market-context) — built
  (`analyze.py`), validated on synthetic curve-predictive vs null constructions.
- **Not done this pass (by design):** wiring `aw_scale` into `monte_carlo.py:2668`, the
  daily-curve export extension, and any real MC run. Those are the next step; the harness
  + the exact commands above are ready.
