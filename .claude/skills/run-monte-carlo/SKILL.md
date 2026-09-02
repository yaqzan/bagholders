---
name: run-monte-carlo
description: Run and interpret the portfolio Monte Carlo simulator (monte_carlo.py / monte_carlo_15dte.py) — a paired-seed, env-var-driven engine with no --profile flag and no CLI parser at all. Covers the seeded bounded-fill fill model, the canonical WINDOWS list (always screen 2020_crash), N floors (100/300/500), how to set up an A/B sweep by monkey-patching the imported module, and the T1-T7 DD-primary reading rules. Use when the user asks to "run MC", "sweep this portfolio knob", "check the drawdown", "validate at N=500", or wants a Stage-3 A/B between a baseline and a candidate config.
---

# /run-monte-carlo — Portfolio Monte Carlo simulation

`monte_carlo.py` (30 DTE) and `monte_carlo_15dte.py` (15 DTE) are the seeded
Monte Carlo portfolio simulators — the primary evidence engine for **Stage 3
(T1-T7, DD-primary)** ship gates. Companion deterministic tool: `backtest_cascade.py`
(zero randomness, live-parity check). Full gate definitions live in
[assessment-backtest.md](../../docs/assessment-backtest.md) "Three-Stage
Calibration Framework"; this skill is about *running* the engine correctly.

## GUARDS (read before you write a sweep script)

1. **There is no `--profile` flag, no CLI parser, no argparse in `monte_carlo.py`
   at all.** Confirmed by source read: zero `argparse`/`sys.argv` usage in the
   file. It runs bare: `python monte_carlo.py`. Every knob is a **module-level
   global set from an env var at import time**
   (`NAME = float(os.environ.get('NAME', str(_cfg.NAME)))`), so variants are
   expressed by (a) env vars before the process starts, or (b) importing the
   module in-process and reassigning its globals directly (`import monte_carlo
   as mc; mc.TIER_ALLOC = {...}`). Sentinel/Core/Apex portfolio profiles are
   **not** an MC concept — they live in `algorithm_versions/portfolio_profiles.json`
   and are applied by `assess --profile`, `temporal-refresh --profiles`, and
   research-pack `--profiles`, not by MC. A command like `python monte_carlo.py
   --profile apex` is invented syntax; it will silently do nothing (unrecognized
   token, bare script, no parser to reject it) and run production defaults.
2. **Always screen `2020_crash` and `2020`, never just the 8 T3 windows.**
   T3 lists 8 canonical windows (2021/2022/2023/2024/2025/dip/22-now/5y) but
   `monte_carlo.py`'s live `WINDOWS` list (verified, line 1227) has **12** entries
   — it also has `2018`, `2020`, `2020_crash`, `10y`. `collapse=0 on EVERY window
   incl 2020-COVID` (standards bar #2) is non-negotiable; a T1-T7 pass on the 8
   canonical windows alone that never touched `2020_crash` is not a ship-grade
   result. See `## FLAGS` in `inventory/gates.md`: treat T3's "8" as a floor, not
   the ceiling.
3. **Paired seeds are automatic ONLY if window labels match exactly.** Seeds are
   `1000 * _stable_label_seed(label) + iteration_index` — a `blake2b` hash of the
   **string label** (`'2022'`, `'22-now'`, …), not of the config. Two variants run
   in the same script with the same `label` string get bit-identical random draws
   except where the swept knob changes fill order — a clean A/B. Rename a label
   between arms (`'2022'` vs `'2022_v2'`) and you silently break pairing (adds MC
   noise the comparison can't distinguish from signal).
4. **N floors are hard, not suggestions.** Smoke N=100 (fill-dynamics sanity
   only, never a gate); screen N=300 (±5-8pp DD noise, ±1.6-1.8× compound noise
   at a single window — memory: "MC noise floor at N=300 single-window"); ship
   N=500+ per (window × mode) for Stage 3 T2. Never cite an N=300 single-window
   result as a DD-primary ship gate (`.claude/docs/deploy.md`: "Never use N=150
   4-window MC as a portfolio ship gate" — same logic applies to any sub-500
   final claim).
5. **DD-primary, compound is sanity only.** 5y compound magnitudes at MC scale
   (1e10-1e36%) are unrealizable and **not** the metric to optimize — read
   `WorstDD` first, `p_collapse` second, compound last (±3 OOM sanity check,
   T7). A candidate that "wins" on compound but worsens 5y WorstDD by >1.0pp is
   the classic Phase-OP1 trap (TS4 in Stage 3 soft constraints) — reject it
   unless the compound win is >~1e10% AND the DD cost is bounded.
6. **15 DTE is NOT calendar-honest yet.** `STRATEGY_15DTE.CALENDAR_HOLD=False`
   (verified `strategy_config.py:1502` — "15 DTE not yet separately optimized on
   the honest engine; keep the legacy path"). `monte_carlo_15dte.py` has **zero**
   references to `CALENDAR_HOLD`/`NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS` anywhere in
   the file — those env knobs literally don't exist on the 15-DTE engine. Only
   `monte_carlo.py` (30 DTE) honors them. Do not port a `NOMINAL_CAL_DTE=15`
   override onto `monte_carlo_15dte.py` expecting an effect — it's a no-op there
   by construction; the honest-calendar 15-DTE port is a documented fast-follow,
   not done.
7. **MC absolutes vs differentials diverge under fill realism (2026-08-10, measured).**
   The engine's default TP fill is measurably generous (~1.5× real overshoot credit;
   ~15% of declared TPs never fill economically — `experiments/tp_fill_fidelity_30dte/`).
   Under the calibrated knobs (`TP_FILL_MISS_P=0.15` + `TP_FILL_GAP_AWARE=1`, both
   default-OFF) every config tested to date — including shipped baselines — reads
   absolutely NEGATIVE while rankings stay stable. Therefore: select and ship on PAIRED
   differentials; never quote MC absolute compound/DD as expected live performance; any
   finalist battery should include a calibrated-reality arm (differential must not
   invert) — standard since the tpsl_refine campaign. Flipping the knobs' defaults is
   its own Stage-3 ship.
8. **Always via the queue, never raw background.** MC is minutes+ compute that
   hammers CPU. `trader queue submit --priority high … -- python monte_carlo.py`
   (off-hours) or `--window off_market` / `--priority normal` during market hours
   — see `/queue-ops`. The harness's own `run_in_background` flag is **not** the
   queue and bypasses CPU/DB admission even if you're actively watching it.

## 1. The two engines and what each answers

| Tool | Randomness | Answers | Companion |
|---|---|---|---|
| `python monte_carlo.py` | Seeded, bounded-fill (30 DTE) | "What's the DD/return distribution under this config?" — the Stage 3 gate engine | `monte_carlo_15dte.py` (15 DTE twin, `CALENDAR_HOLD` inert — GUARD 6) |
| `PYTHONIOENCODING=utf-8 python trader.py backtest --from 2021-01-01 --capital 50000` (bash; PowerShell: `$env:PYTHONIOENCODING="utf-8"; python trader.py backtest --from 2021-01-01 --capital 50000`) (→ `backtest_cascade.py`) | **Zero** — deterministic replay of every real score ≥70 through real OHLCV | "Does this actually happen once, chronologically, with the real signal tape?" — live-parity sanity check, not a distribution | `backtest_cascade_15dte.py` |

Run MC for the distribution (mean/median/worst-case/collapse-rate across many
seeded paths); run `backtest_cascade` afterward as a single deterministic
sanity check that the MC's fill logic isn't diverging from what would actually
have happened. Neither replaces the other — MC has no signal to bias, and
`backtest_cascade` has no dispersion to read a DD floor off.

## 2. Fill model (what "seeded" means)

Single mode since 2026-04-29 (`COLLISION_MODES = ['seeded']` — the historical
3-collision-mode system, including the old "Conservative"/"Realistic" split
cited throughout `.claude/docs/monte-carlo-sweeps.md`'s historical findings, was
removed; the label is kept only as a back-compat iterator key for external
scripts). Per iteration:
- **Intraday resolution is deterministic**: if a bar's high/low crosses both a
  TP and SL trigger, the walk resolves at whichever the underlying model says
  hits first for that specific path — no random tiebreak needed within a bar.
- **Gap resolution is the one random draw**: an overnight gap that jumps past
  the trigger fills at `Uniform(low, open)` (bimodal bounded fill) rather than
  assuming the worst price — this is the "seeded" randomness, one draw per
  gap-crossing event per iteration, seeded by `1000 * _stable_label_seed(label)
  + iteration_index`.
- Reference: memory `project_mc_3mode.md` — "since 2026-04-29 (`3432fb8`) MC =
  single seeded mode + bimodal bounded fill."

## 3. Canonical windows (verified `monte_carlo.py:1227`, as of 2026-07)

```
2018        2018-01-01 .. 2018-12-31   # 2018-Q4 selloff
2020        2020-01-01 .. 2020-12-31   # COVID crash + V-recovery
2020_crash  2020-02-01 .. 2020-04-30   # sharp COVID drawdown — ALWAYS screen this
2021        2021-01-01 .. 2021-12-31
2022        2022-01-01 .. 2022-12-31
2023        2023-01-01 .. 2023-12-31
2024        2024-01-01 .. 2024-12-31
dip         2025-11-01 .. 2026-04-24
22-now      2022-01-01 .. 2026-04-24
2025        2025-01-01 .. 2025-12-31
5y          2021-01-01 .. 2026-04-15
10y         2016-06-01 .. 2026-04-15   # full honest-v70 history incl crashes
```
These end-dates are hardcoded and will look stale as real dates move past
them — that's expected; re-verify against source (`grep -n "^WINDOWS = \["
-A 15 monte_carlo.py`) if a window's boundary matters for your sweep, don't
assume today's date. `experiments/bayes_mc.py`'s `SWEEP_WINDOWS` is a
**6-window subset** (2021/2022/2023/2024/2025/22-now — no dip/5y/10y/2018/2020/
2020_crash) used by most historical Bayesian sweep phases; it is a convenience
subset, not a replacement gate list — a Stage-3 *ship* validation still needs
the full T3 set plus `2020_crash`.

Stage-3 T3 gate windows (the floor, per GUARD 2): **2021, 2022, 2023, 2024,
2025, dip, 22-now, 5y** — 8 of the 12. Crash-screen additions: `2020`,
`2020_crash`, `2018`, `10y`.

## 4. Running it — bare invocation and env overrides

The inline `VAR=value command` forms below are bash syntax (this repo's Bash
tool, or a real bash/WSL shell) — PowerShell has no inline env-var prefix; use
`$env:VAR = "value"; command` instead (see PowerShell equivalents alongside
each command).

```bash
# Bare run: full 12-window sweep, N=500, active version, $50k start.
python monte_carlo.py

# Smoke (fill-dynamics sanity only — NOT a gate):
MC_NO_MP=1 N_ITER_OVERRIDE=100 WINDOWS_OVERRIDE=22-now python -u monte_carlo.py
# PowerShell: $env:MC_NO_MP="1"; $env:N_ITER_OVERRIDE="100"; $env:WINDOWS_OVERRIDE="22-now"; python -u monte_carlo.py

# Screen (N=300, one window):
N_ITER_OVERRIDE=300 WINDOWS_OVERRIDE=22-now python -u monte_carlo.py
# PowerShell: $env:N_ITER_OVERRIDE="300"; $env:WINDOWS_OVERRIDE="22-now"; python -u monte_carlo.py

# Ship validation (N=500+, all 8 T3 windows + 2020_crash):
N_ITER_OVERRIDE=500 WINDOWS_OVERRIDE=2021,2022,2023,2024,2025,dip,22-now,5y,2020_crash python -u monte_carlo.py
# PowerShell: $env:N_ITER_OVERRIDE="500"; $env:WINDOWS_OVERRIDE="2021,2022,2023,2024,2025,dip,22-now,5y,2020_crash"; python -u monte_carlo.py
```

Verified env knobs (`monte_carlo.py` source):

| Env var | Effect |
|---|---|
| `N_ITER_OVERRIDE` | int, replaces `N_ITER` (default 500) when >0 |
| `WINDOWS_OVERRIDE` | comma list of labels from the WINDOWS table above, e.g. `'22-now,5y'` — filters, doesn't add |
| `WIN_START` / `WIN_END` (+ `WIN_LABEL`) | ISO dates; when both set, **fully replaces** the preset WINDOWS list with one arbitrary window (used by rolling-start-date sweeps, e.g. `experiments/concentration_2x/sweep.py`) |
| `MC_NO_MP=1` | disable multiprocessing (needed for in-process monkey-patch sweeps — MP workers re-import the module fresh and won't see your patched globals unless propagated via `_apply_cell_params`) |
| `STARTING_CASH_OV` | overrides `STARTING_CASH` (default $50k) |
| `MAX_POSITIONS_OVERRIDE`, `PUT_THRESHOLD_OVERRIDE`, `TIER_*_OV`, `PUT_TIER_*_OV`, `HARD_SELL_LOSS_OV`, `TP_BASE_OV`/`TP_STRESS_OV`/`SL_BASE_OV`/`SL_STRESS_OV`, `SLIP_ENTRY_OV`/`SLIP_TP_OV`/`SLIP_SL_OV`/`SLIP_HARD_OV`, `REGIME_SLOPE*` family, `RXDD_*`/`MWDD_*`/`TVDD_*`/`BDIV_*`/`SVR_*` (DD-lever knobs) | Per-constant sweep overrides — pattern is always `NAME_OV` or `NAME_OVERRIDE`; grep `os.environ.get` in `monte_carlo.py` for the exact name before trusting a remembered spelling, this list drifts every ship |
| `ALGORITHM_VERSION_PIN` | pin to a specific `AlgorithmVersion` instead of `get_active_scores_version()` — prints `"Algorithm version (PINNED): …"` |
| `MC_RESULTS_JSON` | path — dumps per-window `{mean_ret, worst_dd, mean_dd, p_coll}` (+ per-iteration arrays if `MC_RETURN_PATHS=1`, + weekly equity curves if `MC_EMIT_CURVE=1`) to that path for an external A/B driver |
| `MC_TRADE_TAPE=1` | records the full per-trade tape (feeds DD-ledger / lever attribution mining) |
| `MC_NO_DB_PERSIST=1` | skip writing to the `monte_carlo_run` table (use for sweep iterations; leave unset for the one canonical run you want persisted) |
| 30-DTE-only: `CALENDAR_HOLD`, `NOMINAL_CAL_DTE`, `HOLD_CAL_DAYS` | Honest calendar-day hold/theta (shipped 2026-06-09, default ON for 30 DTE). **Inert on `monte_carlo_15dte.py`** (GUARD 6) |

`15dte`: `monte_carlo_15dte.py` shares the `*_OV` sizing/tier surface but not
the calendar-hold trio. Run it the same bare way: `python monte_carlo_15dte.py`.

Grouped remainder (DD-lever/score-mechanism/experiment-plumbing env vars) is
large and drifts fast — see `inventory/cli.md` §5.3 grouping if you need a name
not listed here, and confirm the exact spelling against source before using it.

## 5. Setting up a Stage-3 A/B (base arm vs candidate arm, same seeds)

Two patterns, both verified against real sweep scripts in `experiments/`:

**Pattern A — simple monkey-patch (single process, sequential variants).**
Exemplar: `experiments/concentration_2x/sweep.py` (2026-06-22, confirmed
post-rewrite — reads the result via `['seeded']`, not a mode dict).
`experiments/bayes_phase13_cutonly_validation.py` (the SHIPPED
`REGIME_SLOPE_UP/DOWN` CUT_ONLY validation) predates the 2026-04-29
seeded-mode rewrite and hardcodes the dead `COLLISION_MODES =
['conservative', 'realistic']` system, reading results via
`.get('realistic', {})` / `.get('conservative', {})` — do not copy that file's
result-reading pattern; `mc.run_window(...)` below returns the bare `'seeded'`
dict directly, same as GUARD/section 6.

```python
import monte_carlo as mc
from database.models.core import AlgorithmVersion

VARIANTS = [
    ('BASELINE', 1.00, 1.00),
    ('CANDIDATE', 0.00, 1.00),
]
N_ITER = 500          # screen; use 500+ for ship
WINDOWS = [('2021', ...), ('2022', ...), ...]   # same 8 T3 labels + 2020_crash

def _run(label, su, sd):
    mc.REGIME_SLOPE_UP = su
    mc.REGIME_SLOPE_DOWN = sd
    mc.N_ITER = N_ITER
    v = AlgorithmVersion.get_active_scores_version()
    results = {}
    for wl, d1, d2 in WINDOWS:
        results[wl] = mc.run_window(wl, d1, d2, v)   # SAME label across variants -> paired seeds
    return results

all_results = {label: _run(label, su, sd) for label, su, sd in VARIANTS}
# then diff WorstDD / p_collapse / mean_ret per window between arms
```

`mc.run_window(label, d_start, d_end, version)` is the stable public entrypoint
(`_prepare_window` + `_simulate_window` in one call) — this is what `main()`
itself calls per window, so a monkey-patched sweep is running the *real* engine
path, not a reimplementation.

**Pattern B — load-once (many cells sharing one expensive PREPARE).** Use when
sweeping a knob that doesn't affect signal loading (tier alloc %, MaxPos, DD
levers) across many cells per window — PREPARE (signal load + barrier-outcome
precompute) is by far the dominant cost and is identical across cells that only
differ in cheap sizing params. Exemplar: `experiments/concentration_2x/sweep.py`.

```python
ctx = mc._prepare_window(label, d_start, d_end, version)   # ONCE per window
for cell_params in grid:                                    # MANY cheap re-sims
    mc._apply_cell_params(cell_params)   # sets TIER_ALLOC/PUT_TIER_ALLOC/MAX_POSITIONS* in-process
    result = mc._simulate_window(ctx)
```
`_apply_cell_params` only accepts `TIER_ALLOC`, `PUT_TIER_ALLOC`,
`MAX_POSITIONS`, `MAX_POSITIONS_CALL`, `MAX_POSITIONS_PUT` (verified — read the
docstring before assuming it covers your knob; anything else still needs direct
`mc.NAME = value` assignment). Bit-exact vs the legacy per-cell-subprocess path
because seeds depend only on `label` + iteration index, never on cell params.
Set `MC_NO_MP=1` (or use `_apply_cell_params`'s MP-propagation path — check the
exemplar) since spawned MP workers re-import fresh and won't see in-process
global patches otherwise.

**Reading the diff:**
1. `WorstDD` per window, primary (T4: 5y within +1.0pp of baseline; T5: no
   annual window regresses DD by >5pp).
2. `p_collapse` per (window × N): must be 0% on every cell including
   `2020_crash` (T6, standards bar #2).
3. Compound `mean_ret`/`med_ret` last, ±3 OOM sanity only (T7) — do not rank
   candidates by this number.
4. Escalate N as confidence grows: N=100 (does the direction hold at all) →
   N=300 (screen, expect ±5-8pp DD noise) → N=500 (ship claim) — per the Stage
   3 sweep cadence (LHS/Optuna at N=100×8 → drill top-5 N=300×8 → final N=500×8).

## 6. Reading a result table

```
Window       MeanRet          MedRet        WorstDD    MeanDD   P(coll)
2022-now     +5,090,000,000%  +4,200,000%    77.4%      52.1%     0.0%
```
- **WorstDD** = the single worst peak-to-trough equity drawdown across all N
  iterations for that window — this is what T4/T5 gate on.
- **P(coll)** = fraction of iterations that hit the collapse floor (<20% of
  starting value) — must be exactly 0.0 for every window on a held-book ship
  (standards bar #2); the opt-in Apex sprint tolerates a small explicit budget
  only with user sign-off.
- Compound `MeanRet`/`MedRet` at MC scale routinely prints values like
  `+5.09B%` or larger — this is expected and not a bug; it is not achievable
  capital, it's a sanity/ranking signal only (T7, ±3 OOM).
- `monte_carlo_run` table persists each non-`MC_NO_DB_PERSIST` run — useful for
  retrieving a past canonical run without re-simulating.

## 7. mc-run — the CLI wrapper (undocumented in CLAUDE.md, real)

`trader.py` has an `mc-run` subcommand that shells out to `monte_carlo.py` /
`monte_carlo_15dte.py` with `PYTHONIOENCODING=utf-8` forced and persists to
`monte_carlo_run` on completion:
```bash
trader mc-run                     # 30 DTE, N=500 default, all windows
trader mc-run --dte 15            # 15 DTE
trader mc-run --n 300             # sets N_ITER_OVERRIDE
trader mc-run --windows 22-now,5y # sets WINDOWS_OVERRIDE
trader mc-run --smoke             # alias for --n 100 --windows 22-now (unless overridden)
```
This is a convenience subprocess wrapper for a *single canonical run*, not a
sweep tool — for an A/B with paired seeds, use Pattern A/B above (in-process,
same Python session) rather than shelling out twice via `mc-run`.

## 8. Submitting through the queue

```bash
# Off-hours or short/light job:
trader queue submit --priority high --cpu 4 --db light \
  --reason "Stage-3 screen: CUT_ONLY N=300x8" \
  -- python experiments/my_sweep.py

# During market hours, protect the scheduled `trader update`:
trader queue submit --priority high --window off_market --cpu 4 --db light \
  -- python experiments/my_sweep.py
# OR drop priority so `scheduled` always wins:
trader queue submit --priority normal --cpu 4 --db light \
  -- python experiments/my_sweep.py

# Wait for it, notified in background (harness run_in_background: true):
trader queue wait <id>
```
See [/queue-ops](../queue-ops/SKILL.md) for the full submit-flag reference,
priority-tier market-hours floor, and exit codes. MC jobs are typically
`--db light` (read-heavy on price/score history, not a heavy write load) unless
`MC_NO_DB_PERSIST` is unset and you're persisting many runs.

## Evidence / see also

- [monte-carlo.md](../../docs/monte-carlo.md), [monte-carlo-sweeps.md](../../docs/monte-carlo-sweeps.md)
  — historical parameter-justification sweeps. **Both are explicitly marked
  "REFERENCE SECTION" in-file** — named scripts like `monte_carlo_options_sim.py`,
  `monte_carlo_regime_tp_sweep.py`, `monte_carlo_optimal_sl_5y.py` are deleted or
  consolidated into `monte_carlo.py`; the historical "3 collision modes" and "80%
  Conservative DD floor" language predates the 2026-04-29 seeded-mode rewrite.
  Read these for *why* a locked value is what it is; never copy a parameter or a
  collision-mode name out of them as if it were live syntax — current values are
  in `strategy_config.py`, current gates are T1-T7.
- [assessment-backtest.md](../../docs/assessment-backtest.md) "Three-Stage
  Calibration Framework" — full T1-T7 gate table, Stage 3 sweep cadence, soft
  constraints (TS1-TS6).
- `/ship-gates` — operationalizes the full W/B/T gate framework across all three
  stages, including the waiver ledger and holdout lock.
- `/queue-ops` — submit flags, priority tiers, wait/exit-codes.
- `/ship-portfolio` — what a Stage-3 ship actually requires end-to-end (drift
  guard, consumer-wiring checklist, temporal-refresh, research-pack rebuild)
  once your MC evidence clears T1-T7.
- Memory: `project_mc_3mode.md` (seeded bounded-fill model), `feedback_mc_noise_floor.md`
  (N=300 noise floor), `feedback_dd_primary_compound_theoretical.md` (DD-primary
  doctrine), `project_apex_time_to_2x_dte_sweep.md` (honest ANY-DTE via
  `NOMINAL_CAL_DTE`, 15DTE needs no separate port for that particular sweep).

## Self-update

If you hit a trap this skill missed, append it to GUARDS above and to
[traps.md](../../docs/traps.md) in the same session.
