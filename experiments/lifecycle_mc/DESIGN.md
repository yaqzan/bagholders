# Lifecycle MC — DESIGN (pre-registered BEFORE compute)

**Owner:** LIFECYCLE-MC (gameplan P3.1 Phase 2). **Status:** design + harness + N=100 screen
submitted. **NOT a ship, NOT a verdict.** Portfolio-stage research only — no `ALGORITHM_VERSION`
bump, no scoring change, no `strategy_config.py` / `monte_carlo.py` / `portfolio_profiles.json`
edits. Phase 3 (wiring an auto-rotate mechanism into the live engine) is **USER-LOCKED** — this
document and its harness produce **evidence only**, per gameplan P3.1: "Phase 3 wire auto-rotate
🔒 only if the policy Pareto-wins."

This file is written and committed **before** any Monte Carlo compute for this experiment runs, per
house pre-registration discipline (see `ship-gates` / `run-monte-carlo` skills, and the same pattern
used by `experiments/holdout_oos_2026_12/PREREGISTRATION.md` and `experiments/miss_regime_fakeout/`).
Any deviation discovered while building the harness is called out explicitly below, not silently
absorbed.

---

## 1. Motivation

Gameplan section 1 ("Mission & objective function") states the actual arc this row serves:

> **Capital-lifecycle framing (the user's actual arc):** small account → Apex sprint (stop-at-2x,
> manual today) → Core (held compounder) → Sentinel (preservation at scale). Structure work should
> make this lifecycle *mechanical* rather than manual (see P3).

Two pieces of fresh evidence motivate asking the question **now**, both cited verbatim from files
this row was told to read:

1. **`experiments/deep_crash_screen/RESULTS.md` (2026-07-13, N=300, SCREEN tier)** — the Apex sprint
   **held continuously** (i.e., never rotated out) is a documented ruin machine on multi-year grind
   regimes the standard 8/12-window gate never screens:

   | profile | window | med ret | worst DD | P(collapse) |
   |---|---|---:|---:|---:|
   | Apex (held) | dotcom_2000_2002 | −86.3% | 92.2% | **100.0%** |
   | Apex (held) | gfc_2007_2009 | −74.7% | 86.5% | **20.7%** |
   | Apex (held) | 2007_now (19y) | −14.9% | 95.9% | **48.3%** |
   | Apex (held) | ltcm_1998 | +8.8% | 71.4% | 0.0% |
   | Core | dotcom_2000_2002 | −43.0% | 59.5% | **0.0%** |
   | Core | gfc_2007_2009 | −20.6% | 65.5% | **0.0%** |
   | Core | 2007_now | +3,706% | 70.8% | **0.0%** |

   RESULTS.md's own read: *"the mandated investigation RESOLVES TO THE DOCUMENTED MECHANISM
   (capital-velocity law; 'never make the sprint the held default'... the sprint's value exists only
   under stop-at-2x discipline."* This is a SCREEN (survivor-only, never a gate), but it is exactly
   the shape of evidence that makes "sprint forever" clearly wrong and motivates asking whether
   "sprint once, then get out into Core" is right — as opposed to either extreme (all-sprint-forever,
   or never-sprint-at-all).
2. The 2x watchdog (Phase 1, shipped `0b6b778e0`) already halts new sprint entries at 2x and lets
   exits ride — a **manual, per-user-action** stop-at-2x. This Phase 2 asks whether *automatically*
   routing the freed capital into Core (rather than leaving it to a manual decision, or restarting
   another sprint) is a good policy **before** anyone builds the Phase 3 wiring to do it automatically.

## 2. The question

**Does `sprint(30-DTE, stop-at-2x) -> rotate-to-Core` beat `Core-only`, for small starting capital,
pooled across many possible start dates, at bounded drawdown and zero collapse?**

"Small starting capital" = **$50,000** — the canonical sprint starting stake used throughout
`concentration_2x`, `apex_dte_dd`, and the prior `ladder_vs_core` work; this experiment does not
introduce a new capital assumption.

## 3. Arms

All three arms share: v74 pinned scores (active at design time, `f9fb7b934`; each queue run records
its own resolved commit — see `_rolling_runner.py`), calls-only, all 5 shipped DD levers +
SPREAD_TILT + dead-hold **ON** (i.e., whatever `STRATEGY_30DTE` currently ships — this experiment
inherits the shipped mechanism stack rather than re-deriving it), `$50,000` starting cash
(`STARTING_CASH_OV` left **unset** — the engine's own $50k default; verified against
`run_h3_envelope.py`'s own comment: *"FROZEN_ENV deliberately never sets STARTING_CASH_OV — the
module default applies"*), and `MC_RETURN_PATHS=1` / `MC_NO_DB_PERSIST=1` (never write to MySQL).

### 3a. `core_only` — the baseline

**Config:** the shipped, unmodified Core profile. Per `deep_crash_screen/run_screen.py`'s own
verified comment (*"Core: STRATEGY_30DTE bare defaults... IS `algorithm_versions/
portfolio_profiles.json`'s 'core' entry byte-for-byte"*), the harness's `CORE_ENV` (`envs.py`) is
that exact, already-source-verified dict: cascade tiers ultra/top/mid/low = 0.20/0.15/0.08/0.03,
overflow 0, MaxPos 14, gross/call cap 0.50, puts off. No TP/SL/DTE overrides (inherits
`STRATEGY_30DTE`'s TP+30/SL−70/30-DTE/HOLD_CAL_DAYS=27).

**Mechanics:** the full $50k sits in Core for the entire lifecycle horizon. No rotation, ever.

### 3b. `sprint_rotate_core` — the policy under test

**Config — sprint segment:** the **STAGED 30-DTE Option A** config from
`experiments/apex_dte_dd/SHIP_HANDOFF.md` ("minimal, recommended drop-in"): today's live Apex
15-DTE recipe (`FROZEN_ENV`, imported verbatim from
`experiments/holdout_oos_2026_12/run_h3_envelope.py` — never hand-retyped, same anti-drift
discipline `apex_dte_dd/run_p03_evidence.py` already used) with exactly the four fields
SHIP_HANDOFF.md names changed:

```
NOMINAL_CAL_DTE   15 -> 30
HOLD_CAL_DAYS     13 -> 27
SL_BASE_OV     -0.85 -> -0.70
SL_STRESS_OV   -0.85 -> -0.70
```

Tiers stay flat 0.25 × 4 names, `MAX_POSITIONS_OVERRIDE`/`MAX_POSITIONS_CALL`=4, gross/call cap
**0.9** (Apex's own cap, *not* Core's 0.5 — SHIP_HANDOFF.md: *"gross_cap 0.9 — unchanged"*), TP
inherited (+30, unset). This is built programmatically in `envs.py` as `FROZEN_ENV + OPTION_A_DIFF`,
identical in construction to `run_p03_evidence.py`'s `STAGED_N4_ENV` — so there is no way for this
harness's recipe to silently diverge from SHIP_HANDOFF.md's own "How to apply" section.

**Option B (n10, Pareto-best) is NOT run in this screen** — see §11 (out of scope, cost control).

**Mechanics:** start $50k in the sprint. Watch for first-passage to $100k (2×). **On first-passage,
rotate 100% of current equity into Core for the remainder of the lifecycle horizon** (this models
the Phase-1 watchdog's halt-new-entries behavior followed by an *automatic* full rotation, rather
than a manual one). If 2× is never reached within the pre-registered sprint horizon, the **fallback
rule** (§4) fires. There is no second sprint attempt in this arm — that is arm 3.

### 3c. `ladder_sprint_core` — sprint→skim→restart (subsumes `experiments/ladder_vs_core/`)

**Config:** identical sprint and Core configs to 3a/3b.

**Mechanics:** reuses the **bank-on-double, always-fresh-$50k-restake** logic already built in
`experiments/ladder_vs_core/ladder_sim_episodes.py` (`run_ladder_episode_rep`): each time the
at-risk stake doubles, bank the $50k profit into Core (cold-store) and restake a fresh $50k sprint
(topped up from cold-store if the running stake is short); if a sprint episode fails to double
within its horizon, the eroded residual carries into the next episode, re-funded to
min($50k, total). This is a **repeating** version of arm 3b's one-shot rotation. Cold-store
destination is **Core only** in this pre-registration (the old harness also modeled cash/Sentinel
cold-store destinations — out of scope here, see §11, to keep the comparison to the one variable
this gameplan row actually asks about: repeat-forever vs. rotate-once vs. never-sprint).

**Why this is cheap to complete** (per the task's own conditional, "if its prior harness supports it
cheaply"): `dump_episodes.py` + `ladder_sim_episodes.py` already implement exactly this mechanic.
The blocker was never the logic — see §9 "What was wrong with the prior harness" — it was that the
prior harness's **data** (not its code) used a stale, since-superseded sprint recipe. This
experiment's `panels.py` regenerates the underlying panels with the correct (`envs.py`) recipe: the
policy-composition logic in `policy.py::simulate_ladder` is a direct re-implementation of the same
algorithm, fed by the corrected panels, at the same pooled-start grid as arms 3a/3b for apples-to-
apples comparability (the old harness used its own bespoke year-block panels — not reused here).

## 4. Fallback rule (PRE-REGISTERED)

**Rule: "ride to window end."** Sprint horizon **H = 730 calendar days (2 years)**. For any path
that has not reached 2× by day H, the position is force-rotated into Core at day H, using the
sprint MC's own `final` value at H (whatever it settled to — erosion, chop, or partial gain
included) as the rotation equity. No path stays in the sprint state past 2 years.

**Justification (from the sprint's own median-days-to-2x evidence, as instructed):**

1. **H=730d is not a new parameter — it's the existing convention.** `P(2x within 2y)` is the
   headline metric already reported for the STAGED n4 config throughout
   `apex_dte_dd/FINDINGS.md`/`SHIP_HANDOFF.md` (71.8% at N=100, 71.3% at N=300, and the N=500
   ship-tier confirm reproduces it — FINDINGS.md Result 4) and is `concentration_2x/sweep.py`'s own
   `HORIZON_DAYS = 730` module constant, described there as *"long enough for the funded Apex sleeve
   to plausibly 2x in good tape while still leaving many starts that DON'T 2x."* Reusing it means
   this experiment's fallback rate is directly readable off evidence that already exists (≈28-29% of
   paths fall back), rather than inventing a new horizon whose fallback rate would need fresh
   measurement.
2. **It comfortably straddles the median.** The STAGED n4 config's median days-to-2x is **113
   calendar days** (N=500 confirm, `apex_dte_dd/FINDINGS.md` Result 4) — H=730d is ~6.5× the
   median, so the large majority of paths resolve by first-passage long before the fallback would
   ever fire; the fallback only catches the genuine tail (stuck/eroding sprints), which is the
   correct population for a "stop the bleeding, don't wait forever" rule.
3. **It is the cheapest option data-wise.** "Ride to window end" needs nothing beyond what a single
   `MC_RETURN_PATHS=1` pass over a 730-day window already returns per path (`final`, `dd`,
   `t2x_bar`). The rejected alternative — "rotate anyway at some day N < H" for stragglers — would
   require either (a) a **second**, independently-seeded MC pass truncated to day N to read
   "equity as of day N," which breaks the very path identity that makes "this specific path never
   reached 2x" meaningful (the day-N reading and the full-window `final`/`t2x_bar` would come from
   uncorrelated seeds), or (b) new engine plumbing to expose an intra-window equity checkpoint — and
   `monte_carlo.py` is FROZEN for this task (no engine edits). "Ride to window end" is honest,
   reuses exactly the array `dump_episodes.py`/`concentration_2x/sweep.py` already extract, and adds
   zero new assumptions about what happens *between* bars.

**Consequence:** in `sprint_rotate_core`, "time in sprint" is capped at 730 days for every path —
a bounded, time-boxed sprint phase, which also matches the intended spirit of the lifecycle
(the sprint is a *phase*, not an open-ended commitment).

## 5. Pooled starts

**Grid:** reused verbatim (imported, not re-derived) from `experiments/concentration_2x/sweep.py`:
`monthly_windows(hist_start=HIST_START, hist_end=HIST_END, horizon_days=730, step_months=3,
min_horizon_days=180)` — i.e. the exact **quarterly-start grid convention** the task named,
`HIST_START = 2016-06-01`, `HIST_END = 2026-04-15` (matches `monte_carlo.WINDOWS`'s own `10y` row).
This yields ~38 quarterly-rolled 2-year panel windows (matches the ~38-window count
`concentration_2x/README.md` quotes for its own `--step-months 3` coarse stage).

**Panels generated once, reused three ways:** two panel sets are generated over this SAME grid —
`sprint` (STAGED Option A env) and `core` (Core env) — each N iterations per window. All three
arms are built from these two panel sets via post-processing (§6); no additional MC compute is
needed for arm 3c beyond arms 3a/3b.

**Lifecycle horizon (the metric window, distinct from the sprint's own 2y horizon):** **5 years**,
matching the sitewide canonical Core comparison unit (gameplan: *"Core's frontier point is ~62% DD
at +1,248% 5y"*). **Pooled lifecycle starts** = every quarterly tick in the grid that has >= 5y of
runway to `HIST_END`, i.e. `2016-06-01 .. 2021-04-15` stepping quarterly (~19-20 starts). Every
start from 2016-06 through ~2020-04 has 2020-COVID (Feb-Apr 2020) inside its 5y horizon — **~16 of
the ~19-20 pooled starts include COVID**, satisfying "incl COVID" without a special-cased window.
5y horizons that would run past `HIST_END` are not created; the panel grid itself (quarterly through
~2025-10) supplies all the intermediate 2y blocks a 5y chain walking forward from any pooled start
needs.

## 6. Composition seam (DISCLOSED approximations)

This is a **post-processing composer over pre-computed per-window path arrays**, not a change to
`monte_carlo.py` and not a fresh MC run per (start × path × rotation-date) combination — that
combinatorial expansion is infeasible and unnecessary. The seam works exactly like
`experiments/ladder_vs_core/ladder_sim_episodes.py`'s already-built, already-disclosed pattern
(reused, not reinvented):

1. **Segment chaining via calendar-nearest window + per-day log-return proration.** When a segment's
   calendar span doesn't line up exactly with a panel window's own span (e.g., a Core "residual"
   segment after a sprint rotates out partway through a 2y block, or the final partial block of a 5y
   chain), the Core multiplier for that shorter span is computed as
   `exp(log(final/START)/window_span_days * segment_span_days)` — i.e., the window's own realized
   log-return is assumed **uniform per calendar day** across its span, and the segment's slice of it
   is that proportional fraction. Same for drawdown, scaled by `sqrt(segment_span/window_span)`
   (vol ~ sqrt(t)). **This is an approximation, not a re-simulation** — it assumes the path's
   volatility/return profile is stationary within one 2y window, which is weaker for very short
   residual spans (e.g., a rotation 40 days before a window boundary) than for long ones. Identical
   to `ladder_sim_episodes.py::core_block_return`.
2. **First-passage rotation equity = exactly 2× stake, not the bar's actual (possibly
   slightly-overshot) value.** `t2x_bar` marks the first bar where equity ≥ 2×start; the true value
   at that bar could be marginally above 2× (intra-bar/day granularity). Using exactly 2× is a
   small, conservative-in-the-user's-favor-of-Core (i.e. anti-conservative for the rotate policy)
   simplification — it slightly *understates* the rotate policy's rotation equity.
3. **Regime pairing, not path identity.** Sprint and Core panels are generated over the **same**
   calendar windows at the **same** N per window, so at any given calendar tick the harness can draw
   a **shared path index** `pidx` for both the sprint draw and the Core draw at that tick (i.e., "a
   bad quarter for the sprint's cascade is paired with a bad quarter for Core's cascade in the same
   replication"). This is regime-calendar pairing only — **it is not the same underlying price/fill
   path.** The sprint and Core MC cells run as separate engine invocations with independent internal
   per-iteration seeded fills (`random.Random(seed)` per iteration, per `monte_carlo.py`'s own
   per-iteration seeding); sharing a path index only guarantees both draws come from the same
   calendar window, which is the dominant source of cross-sectional regime correlation for this
   book, not a claim of literal shared price ticks.
4. **Common-random-numbers across arms, not lockstep alignment.** For a given (pooled start `s`,
   replication `r`), each arm's own rng stream is seeded deterministically from `(s, r)` (`policy.py`
   uses `random.Random(base_seed + hash((s, r, arm_name)))`-style seeding per arm). This correlates
   the three arms' draws at a given replication better than fully independent randomness (a
   variance-reduction technique used elsewhere in this codebase's paired-seed MC A/Bs) — but because
   `ladder_sprint_core` visits a *different number and length of segments* than `core_only`/
   `sprint_rotate_core` (it restarts on every double), the three arms do **not** consume the same
   rng draws tick-for-tick once their walks diverge in structure. Treat cross-arm comparisons as
   "well-paired at the start, looser thereafter," not perfectly common random numbers throughout.
5. **DD is a conservative running bound, not a tick-by-tick equity curve.** No arm has a continuous
   intra-segment equity curve (the engine returns only `final`/`dd`/`t2x_bar` per path, not a full
   series). Total-lifecycle WorstDD is tracked the same way `ladder_sim.py`/`ladder_sim_episodes.py`
   already do: a running `(peak, max_dd)` pair updated at each segment's own worst-point-within-
   segment (`start_value * (1 - segment_dd)`) and then at its end-value, taking the max drawdown-
   from-running-peak over the whole sequence. For the single-pot arms (3a, 3b) this is a fairly
   tight bound; for the ladder arm's cold-store/running split it is the same "simultaneous-trough"
   conservative bound `ladder_sim_episodes.py` uses (upper bound: assumes the running tranche's worst
   point and the cold-store's worst point coincide, which need not be true).
6. **Holdout lock does not apply**, same reasoning as `concentration_2x/README.md`'s own holdout
   note: this is a portfolio-stage sizing/policy backtest on frozen v74 score rows, not a
   scoring-lift fit. `CALIBRATION_CUTOFF_DATE` governs scoring calibration, not this.

## 7. Metrics

Per arm, pooled across all (pooled start × replication) draws:
- **Terminal wealth distribution**: median, P10/P25/P75/P90, mean (dollars and as a multiple of
  $50k).
- **WorstDD**: max drawdown-from-peak fraction observed over the full 5y lifecycle, across all
  draws (matches sitewide `worst_dd` = a max, not a percentile).
- **Collapse**: `P(terminal <= 0.20 * START)` (the sitewide `COLLAPSE_FRAC = 0.20` convention used
  identically in `concentration_2x/sweep.py` and `ladder_vs_core/ladder_sim.py`).
- **Time-in-sprint**: median/P90 calendar days of the 5y horizon spent in a sprint state (0 for
  `core_only` by construction; capped at 730d per episode for `sprint_rotate_core`; the sum across
  all episodes, potentially most of the 5y, for `ladder_sprint_core`).
- **Median banked** (arm 3c only): dollars banked to Core via doubles, for context.

## 8. BARS (pre-registered verbatim, per the task)

> Phase 3 wiring is licensed for user decision only if the rotate policy Pareto-dominates Core-only
> on terminal-wealth-at-bounded-DD with collapse=0 at N=500 incl 2020-COVID; N=100/300 are screens.

**Operational reading** (this harness's own interpretation, kept separate from the quote above):
"Pareto-dominates on terminal-wealth-at-bounded-DD" = comparing `sprint_rotate_core` against
`core_only` as a 2-objective pair (median terminal wealth, WorstDD): the rotate policy must show
**WorstDD(rotate) <= WorstDD(core_only)** (not worse on the bounded axis) **and** median (and
ideally P25) terminal wealth >= Core-only's, with **collapse=0** for the rotate policy specifically
across every pooled-start/replication cell including every draw whose 5y horizon contains the
2020-COVID crash. `ladder_sprint_core` is evaluated against the same bar as a secondary/exploratory
comparison (it was not named in the row's own BARS sentence, which speaks only of "the rotate
policy" — read as arm 3b). **N=100 and N=300 (per-window engine iteration count) are screens only**
— per `traps.md` "MC noise floor," baseline-to-baseline seed variance at N=300 single-window is
~1.6-1.8x on compound and ±5-8pp on DD; a "win" at N=100/300 is a reason to escalate, not a reason
to license Phase 3. Only an N=500 read, with 2020-COVID represented in the pooled-start grid, licenses
the user decision.

## 9. What was wrong with the prior `ladder_vs_core` harness (why panels are regenerated, not reused)

`experiments/ladder_vs_core/dump_episodes.py`'s `apply_cell_env('sprint')` sets only
`TIER_ULTRA_OV=TIER_TOP_OV=TIER_MID_OV=TIER_LOW_OV=0.25`, `TIER_OVERFLOW_OV=0.0`,
`MAX_POSITIONS_OVERRIDE=MAX_POSITIONS_CALL=4` — it does **not** set `GROSS_PREMIUM_CAP`,
`CALL_PREMIUM_CAP`, `NOMINAL_CAL_DTE`, `HOLD_CAL_DAYS`, `SL_BASE_OV`, or `SL_STRESS_OV`. Since
`monte_carlo.py` is *"the 30 DTE engine"* (`_cfg = STRATEGY_30DTE`) and those fields are all
import-time-frozen env-var-driven globals, leaving them unset means the old "sprint" panel actually
ran at **30-DTE / 50% gross cap / SL−70 / TP+30** — i.e. the *pre-collapse-budget-work* "shipped
sprint" baseline that `concentration_2x/RISK_BUDGET_FINDINGS.md` explicitly labels historical
context (*"Prior context (collapse≈0 work, already done)... Shipped sprint = flat_n4_a25 @ 30DTE /
50% gross / TP30 / SL−70"*), **not** the currently-STAGED Option A (30-DTE / **90%** gross / SL−70),
and **not** the currently-live 15-DTE elbow either. The old panels are stale relative to both.
This is a real, load-bearing discrepancy (50% vs 90% gross materially changes both speed and
collapse risk), not a cosmetic one — hence §3c regenerates panels against the *current* recipe
(`envs.py`, imported from the same source `apex_dte_dd/run_p03_evidence.py` uses) rather than
reading `experiments/ladder_vs_core/results/episodes_sprint.json` as-is. `ladder_sim_episodes.py`'s
own **logic** (Method 3 — bank-on-double, fresh-restake, calendar-nearest-window chaining) is sound
and is the one being reused/subsumed; only the stale *data* is not.

Separately, `ladder_sim_episodes.py` was apparently never run to completion — its own default output
(`results/ladder_vs_core_episodes.json`) does not exist on disk, only the year-block model's outputs
(`ladder_sim.py` → `ladder_vs_core_results.json`, `ladder_yearblock_*.json`, `ladder_giveup*.json`)
do. See the one-line pointer added to `experiments/ladder_vs_core/FINDINGS.md` (new file, this
session) noting the subsumption.

## 10. Staging plan

| Stage | Engine N (per window) | Tier | Who decides to escalate |
|---|---|---|---|
| Screen 1 | 100 | screen | this task — submitted to queue now |
| Screen 2 | 300 | screen | FABLE's call, after reading Screen 1 |
| Gate | 500 | **the only tier that licenses the BARS in §8** | FABLE's call, after reading Screen 2; must include 2020-COVID in the pooled-start grid (already structural per §5, not an extra step) |

This experiment renders **no verdict** at any stage — it reports the arms' numbers side by side, per
the BARS text, for FABLE/the user to read.

## 11. Explicitly out of scope (this pre-registration)

- **Option B (30-DTE n10, "Pareto-best")** as the sprint config — SHIP_HANDOFF.md's own "recommended
  drop-in" is Option A (n4), which is also the config that keeps the user's current 4-name
  concentration, so it is the primary arm here. Option B is a natural follow-up axis (same harness,
  swap `envs.py`'s `SPRINT_ENV` for `STAGED_N10_ENV`, already defined and available) if FABLE wants
  it at the N=300 escalation.
- **Cash / Sentinel cold-store destinations** for the ladder arm (the old harness modeled both) —
  this row's question is specifically Core-only vs. sprint-then-Core, so the ladder arm's cold-store
  is pinned to Core for a clean one-variable comparison (repeat-forever vs. rotate-once vs. never).
- **Phase 3 wiring itself** (auto-rotate in `portfolio_engine.py`) — user-locked, not touched.
- **10y or other horizons** — 5y is the primary, sitewide-comparable horizon; `policy.py` accepts an
  arbitrary horizon parameter so a 10y cut is a cheap re-run of the composer (no new MC) if wanted
  later.
- **Any engine file edit** — `monte_carlo.py`, `strategy_config.py`, `portfolio_profiles.json` are
  untouched; every knob is env-var driven via subprocess, per the task's explicit constraint.

## 12. Harness architecture (what actually runs)

```
envs.py             SPRINT_ENV / CORE_ENV — imported from run_h3_envelope.FROZEN_ENV + the named
                     Option-A diff (sprint) and the source-verified bare-default dict (core); never
                     hand-retyped.
_rolling_runner.py   subprocess-per-(arm,window) runner using WIN_START/WIN_END/WIN_LABEL +
                     MC_RETURN_PATHS=1, resume-safe (skip if the per-cell JSON already exists),
                     modeled on experiments/_mc_pinned_runner.py (which uses WINDOWS_OVERRIDE, the
                     wrong mechanism for arbitrary rolling windows -- this is the WIN_START/WIN_END
                     sibling of that pattern, not a duplicate of it).
panels.py            quarterly-start grid (imported from concentration_2x.sweep.monthly_windows) x
                     {sprint, core} arms -> per-window JSON under results/panels/<arm>/<window>.json.
                     THE QUEUE JOB. Resumable.
policy.py            pure-Python post-processing composer: loads panels, implements the 3 arms
                     (core_only / sprint_rotate_core / ladder_sprint_core), resamples R replications
                     per pooled start (paired seeding per (start, rep, arm)), computes the §7
                     metrics, writes results/screen_n<N>.json. No MC, no MySQL -- cheap, runs at the
                     end of the same queued job.
run_screen.py        CLI entry point: generate panels (resume-safe) -> compose policy -> print +
                     write summary. This is the single command submitted to the queue.
test_policy.py       synthetic, zero-MC unit tests of the composition math (fallback rule, DD
                     bound, collapse counting) on hand-built panels where the answer is known by
                     construction -- mirrors concentration_2x/test_metrics.py's discipline.
```
