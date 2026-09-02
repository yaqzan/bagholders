# v60 Daily Opportunity / COVID Drawdown Findings

Last updated: 2026-05-21

This is the v60-specific handoff file for the daily opportunity allocation and
COVID drawdown protection research. It is meant for the next agent to pick up
without rereading the full chat or every intermediate artifact.

## Baseline And Scope

- Active baseline verified during this research: `active=v60 db:60`.
- v60 active scoring formula hash / `ALGORITHM_VERSION`: `d4a3e9fec`.
- v60 final ship commit noted by the user: `1c3bb8488`.
- v60 was shipped as "SCW and DD call cap candidate"; the research here is
  portfolio-stage / Stage 3 only unless explicitly stated otherwise.
- This folder is experiment-only. Do not run `trader recalculate`, write score
  rows, change shipped scoring, or bump `ALGORITHM_VERSION` from this research
  folder.
- Current candidate work should preserve `Score.overall`. A portfolio-only
  control does not require an algorithm version bump.

## Entry: v60 Ship / Sector-Concentration Doctrine Clarification

Report added: 2026-05-21

This entry exists because the term `SCW` and the recent Stage 3
sector-concentration doctrine can be easy to conflate. They are not the same
thing, and future agents should keep the commit chain and runtime behavior
separate before extending this experiment.

### Verified State

Truth checks run from `C:\Development\Trader`:

```text
Get-Content ALGORITHM_VERSION
=> d4a3e9fec

python trader.py algorithm active
=> active=v60 db:60 commit=d4a3e9fec

git log --oneline -8
=> 78d8405db Fix stale DB handles in Flask API
=> 171d4f153 Fix score badge neutral thresholds
=> 1c3bb8488 Finalize v60 r054 ship
=> eaf7184d2 Bump ALGORITHM_VERSION for v60 r054 ship
=> d4a3e9fec Ship v60 r054 SCW and DD call cap candidate
=> 65c2644cc Document Stage 3 sector concentration wave doctrine
=> 83bbe6883 Make WR15 the primary scoring utility target
=> eb469b0ef Fix assessment DTE toggle labels
```

Current local checkout note:

- `main` is aligned with `origin/main` at the time of this report entry.
- The `experiments/daily_opportunity_allocation/` folder is local/untracked.
- There are unrelated local modifications in `api.py`, `backtest_cascade.py`,
  and `monte_carlo.py`. Do not scoop those into an experiment handoff commit
  without reviewing scope first.

### What The Docs-Only Doctrine Ship Was

Commit:

```text
65c2644cc Document Stage 3 sector concentration wave doctrine
```

Touched files:

```text
.claude/docs/assessment-backtest.md
.claude/docs/deploy.md
.claude/docs/process.md
AGENTS.md
CLAUDE.md
```

What it changed:

- It documented sector clustering as a Stage 3 portfolio exposure problem.
- It made WR15 plus high-tier N the protected Stage 1 scoring objective.
- It told agents not to encode sector concentration into `Score.overall` unless
  sector exposure independently proves directional WR15 value.
- It added the preferred future shape: a smooth, risk-weighted post-fill
  same-side sector exposure throttle.
- It added exposure attribution as the review surface: per-sector / per-side
  exposure before and after, so DD wins can be traced to reduced clustering
  rather than hidden score or trade-quality drift.

What it did not do:

- It did not edit runtime scoring or portfolio code.
- It did not bump `ALGORITHM_VERSION`.
- It did not create v60.
- It did not run `trader recalculate`.
- It did not write `Score` rows.
- It did not implement a live sector-concentration allocation wave.

The documented candidate shape was intentionally future-facing:

```text
sector_share_after = sector_exposure_after / total_book_exposure
pressure = smoothstep(start, full, sector_share_after)
scale = max(floor, 1 - max_cut * pressure)
```

The intended exposure basis is allocated premium / capital-at-risk, preferably
same-side sector exposure (`sector + call/put side`), not raw symbol count.

### What v60 Actually Shipped

v60 is a later runtime ship. It is separate from the docs-only doctrine commit.

Runtime commit chain:

```text
d4a3e9fec Ship v60 r054 SCW and DD call cap candidate
eaf7184d2 Bump ALGORITHM_VERSION for v60 r054 ship
1c3bb8488 Finalize v60 r054 ship
```

The active version row is:

```text
active=v60 db:60 commit=d4a3e9fec
```

v60 has two real shipped components:

1. Stage 1 scoring: `SCW` is **Stoch Conviction Wave**.
2. Stage 3 portfolio: 30 DTE call cap plus earlier/lower DD call contraction.

Important naming clarification:

```text
SCW = Stoch Conviction Wave
SCW != Sector Concentration Wave
```

The v60 scoring change refines the existing call-side low-stochastic /
weak-weekly timing dampener. The key shipped parameters in `strategy_config.py`
are:

```text
SCW_ENABLED=True
SCW_GATE_CALL=70
SCW_MAX_PENALTY=8.0
SCW_STOCH_POWER=1.5
SCW_DECAY_POWER=6.0
SCW_WEEKLY_HI=14.0
SCW_SCALE=1.3
SCW_BOUNDARY_RELIEF=1.35
SCW_BOUNDARY_WIDTH=0.65
SCW_CONFIRM_RELIEF=0.0
SCW_CONFIRM_MID=0.2673067886722032
SCW_RAW_STOCH_RELIEF=0.05
SCW_RAW_STOCH_MID=71.21723387348588
SCW_EXT_TAPER_STRENGTH=0.25
SCW_EXT_TAPER_MID=1.0
SCW_EXT_TAPER_WIDTH=0.3
```

The implementation anchor is `database/utils/scoring.py`: it computes
`scw_base_dampen`, applies smooth scalar relief/taper terms, subtracts the
result from qualifying CALL-side scores, and records audit fields such as
`scw_dampen`, `scw_base_dampen`, `scw_scalar`, `scw_conf`, `scw_raw_stoch`,
`scw_ext_idx`, and `scw_ext_taper` in `weight_info`.

The v60 Stage 3 portfolio companion is 30 DTE only:

```text
MAX_POSITIONS=14
MAX_POSITIONS_CALL=12
MAX_POSITIONS_PUT=None
DD_SOFT_BAND_LO=0.35
DD_SOFT_BAND_HI=0.55
DD_SOFT_CALL_FLOOR=0.40
```

Interpretation:

- The call cap keeps two residual slots available for puts during crowded call
  runs.
- The DD soft band scales call allocation from 1.0 down to 0.40 as running
  portfolio DD moves through `[0.35, 0.55]`.
- This is Stage 3 exposure shaping, but it is not sector-concentration shaping.

### How v60 Uses The Doctrine

v60 uses the sector-concentration doctrine only indirectly as process alignment.
It demonstrates the same separation of concerns:

- Stage 1 scoring changed `Score.overall` through SCW and therefore required
  `ALGORITHM_VERSION=d4a3e9fec` plus score recalc.
- Stage 3 portfolio changed exposure through call cap / DD band and did not
  need a separate algorithm version by itself.
- The sector-concentration doctrine remains a future Stage 3 design rule.

Do not describe v60 as having shipped a sector-concentration exposure wave. It
did not.

### Sector Overlay / Sector Breadth Caveat

v60 still carries the older sector ETF Market Wave infrastructure from the
v57-era stack, and `algorithm_versions/v60/manifest.json` includes
`database/utils/sector_breadth_wave.py`. That is not the same as a portfolio
sector-concentration wave.

There are two separate concepts:

1. **Sector ETF Market Wave / sector breadth score transform**:
   - Score-stage broad market / sector ETF state.
   - Existing v57 lineage carried forward into v60.
   - Records `weight_info['sector_breadth_wave']` when active.
2. **Sector-concentration portfolio exposure wave**:
   - Not implemented in v60.
   - Would act after scores, while filling or sizing positions.
   - Would measure same-side sector exposure already in the book plus the
     candidate fill.

v60 added sector-overlay metadata for research continuity, but the active docs
and config state say:

```text
overlay_scale=0.0
```

Therefore no active sector overlay shipped as part of v60 r054.

### Recalc / Coverage Boundary

The docs-only doctrine commit did not recalculate anything.

The later v60 ship did run the versioned scoring deployment path. The v60
version-history entry records:

```text
Full score fill: 1,721,162 updated / 32,765 skipped / 0 errors
Final coverage after tail refresh: 1,742,361 non-null v60 rows
Symbols: 772
Score dates: 2,658
Latest full date: 2026-05-18 with 768 rows
Research pack: .cache/algorithm_versions/v60/research_pack/manifest.json
```

Future agents should answer recalc questions with this distinction:

- `65c2644cc` did not recalc and did not create v60.
- `d4a3e9fec` / `eaf7184d2` / `1c3bb8488` are the actual v60 scoring ship and
  finalization chain.

### Implications For This Experiment Folder

This folder should keep daily opportunity allocation work portfolio-stage unless
the user explicitly asks for a scoring ship.

Valid v60-era Stage 3 follow-ups:

- Implement a live reserve ledger for `wave_put_divergence_reserve_0124`.
- Implement / validate `ct_crash_suppress` as a CT-promotion suppressor.
- Explore a true sector-concentration exposure wave as a new portfolio
  allocator/sizer, using the doctrine above.

Invalid shortcuts:

- Do not rename SCW or treat it as sector concentration.
- Do not add sector concentration to `Score.overall` just to reduce DD.
- Do not run `trader recalculate` for portfolio-only allocation research.
- Do not bump `ALGORITHM_VERSION` for a pure allocation/sizing control.
- Do not claim v60 validates a sector-concentration wave; it validates r054 SCW
  plus call-cap/DD-band under the documented ship evidence.

### If A Future Agent Implements The Sector-Concentration Wave

Treat it as a new Stage 3 mechanism with its own evidence package:

1. Freeze the active score version and barrier set.
2. Use post-fill same-side sector exposure:

   ```text
   exposure_key = (sector, side)
   sector_share_after = exposure_after_key / total_book_exposure_after
   ```

3. Use allocated premium / capital-at-risk for exposure, not count of symbols.
4. Apply a smooth throttle to the candidate fill or allocation size.
5. Attribute every DD improvement to sector exposure before/after.
6. Validate through T1-T7 with N=500+ across the canonical windows.
7. Run `tests/test_strategy_config_drift.py` and
   `tests/test_mechanism_registry.py` if `strategy_config.py` or
   `mechanism_registry.py` changes.
8. Refresh dashboard temporal surfaces after a real portfolio ship.
9. Keep the ship out of `ALGORITHM_VERSION` unless scoring changes too.

Potential implementation surfaces to inspect first:

```text
strategy_config.py
portfolio_allocation.py
monte_carlo.py
backtest_cascade.py
trader.py alloc path
mechanism_registry.py
```

The first implementation should be reversible by config and should expose
sector attribution in run artifacts. If it requires persistent state, design
that state explicitly; do not hide it in a stateless allocation printout.

## Executive Read

There are two v60 promotion-quality Stage 3 leads:

1. `ct_crash_suppress`: suppress CT-call promotion for marginal 70-74 calls
   under acute crash tape. This is the clean COVID crash protection lead.
2. `wave_put_divergence_reserve_0124`: smooth put-side exposure throttle that
   reserves unspent throttled cash until the original trade exit date. This is
   the clean Market Wave divergence / replacement-path lead.

Do not combine them in the first implementation pass. They solve different
failure modes. The reserve branch has production-equivalent N=500 validation
but still needs a live reserve-ledger implementation; the CT branch still needs
production-equivalent implementation validation at higher seed coverage before
it is ship-worthy.

The broad v59/v60 overlap guard, broad q90 score demotion, hard skip-put probe,
total-demand controller, and merged Bayesian controller families are no-ship in
their tested forms. Their value is diagnostic, not shippable.

## v59 Carry-Forward Entry: Put-Overhang Allocation Alpha

This entry is explicitly from the v59 daily opportunity allocation research,
not a v60 validation result. The source baseline was `v59` / DB version `59`
at commit `4fd7ffa9`, using the daily-state artifact from:

```text
C:\Development\Trader\.codex\runs\20260518-194146-daily-opportunity-allocation-v59-chunked\daily_state.csv
```

The important conclusion was not that "N alone is the formula." Broad
opportunity-N controllers and the merged Bayesian controller had meaningful
cohort signal but did not become usable portfolio edge after sequencing,
cash-recycling, and MC validation. The usable shape was narrower and
side-specific: put overhang combined with weak recent realized execution.

Mined candidate:

```text
if open_put_n >= 7 and prev5_entry_avg_pnl_pct <= 0.07378:
    put_alloc_scale = 0.70
    max_open_puts = 5
```

Experiment label: `put_overhang_pnl__puts70_cap5`.

v59 evidence:

- Deterministic replay mined 170 rule/action pairs and found 23 passable
  narrow cases.
- The balanced put-overhang case preserved 96.1% of full-window trade
  throughput.
- Deterministic 22-now DD improved by +22.85pp with no deterministic
  2020-crash or 2024 DD regression.
- Stable N=240 fail-window MC improved 2024 worst DD by -1.04pp and was neutral
  in 2020-crash.
- Stable N=240 long-window MC improved 22-now worst DD by -14.83pp and 5y worst
  DD by -2.89pp, with materially better mean DD in both windows.

Blocker:

- It was not a release candidate as-is. The v59 MC runs still showed material
  2024 return drag and a slight 2025 worst-DD regression (+0.15pp). Treat it as
  a drawdown-hedge alpha surface, not a free allocation expansion.

Research read:

- There is still alpha to capitalize on here. The live-safe ingredients are
  shifted/replay-visible: open put count and prior-five-entry realized PnL.
- The next valid step is to port the concept into the v60/v61 research surface
  and solve the return-drag problem, not to ship the v59 rule directly.
- The later smooth put-wave reserve work in this file is the stronger v60-era
  descendant of this finding: it keeps the put-overhang / weak-execution
  insight but fixes the cash-recycling path by reserving throttled buying power.

Key v59 artifacts:

```text
C:\Development\Trader\.codex\runs\20260519-041557-usable-policy-mining-v59\usable_policy_cases_summary.md
C:\Development\Trader\.codex\runs\20260519-042206-usable-policy-failwindow-mc-v59\usable_policy_mc_summary.md
C:\Development\Trader\.codex\runs\20260519-042631-put-overhang-expanded-mc-v59\usable_policy_mc_summary.md
C:\Development\Trader\.codex\runs\20260519-043133-put-overhang-longwindow-mc-v59\usable_policy_mc_summary.md
```

## Lead 1: CT-Call Crash Suppressor

The real COVID crash drawdown alpha is not "all v60 extra calls are bad." The
path-diff pass showed the capital-bearing failure was much narrower:

- 2 CT-promoted marginal calls on 2020-03-05.
- Both had score 70-74 and `trend <= CT_CALL_TREND_MAX`.
- Both were under acute market-structure / sector crash pressure.
- Both lost, with average sampled option PnL about -70.8%.
- Outside COVID, the same selector touched zero signals in 2022, 2024, 22-now,
  and 5y artifacts.

Candidate shape:

```text
ct_call_crash_suppressor =
    score in 70-74
    AND trend <= CT_CALL_TREND_MAX
    AND market_structure_wave >= q90
    AND sector_crash_pressure >= 0.55
    AND breadth_pct_above_ema50 <= 25
    AND McClellan <= -40

action:
    suppress CT-call promotion only;
    leave score and candidate list intact;
    let the original 70-74 call fall back to overflow allocation.
```

Final MC validation:

```text
C:\Development\Trader\.codex\runs\v60_ct_crash_suppressor_mc_20260520_190800
```

N=240 across 2020-crash, covid-peak, 2020-full, 2022, 2024, 2025, 22-now, and
5y:

| Policy | 2020-crash | covid-peak | 2020-full | 2022 / 2024 / 2025 / 22-now / 5y |
|---|---:|---:|---:|---|
| `ct_crash_suppress` | worst DD -2.68pp, mean DD -3.70pp | worst DD -8.91pp, mean DD -7.98pp | worst DD -2.32pp, mean DD -3.92pp | flat deltas |
| `ct_crash_demote_low` | worst DD -0.68pp | worst DD -4.80pp | worst DD -1.04pp | flat deltas |

Read: `ct_crash_suppress` is preferred. The partial low-tier fade
`ct_crash_demote_low` also passes but gives up too much protection.

Implementation guidance:

- Implement as a Stage 3 CT-promotion suppressor in the portfolio/cascade path.
- Do not change `Score.overall`, score buckets, or v60 scoring rows.
- Do not model this as `v59_extra_calls_guard`; that metric was only useful as
  a diagnostic overlap probe.
- After implementation, run production-equivalent MC at higher seed coverage
  before calling it shippable.

Key artifacts:

```text
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_combined_20260520_130700\combined_summary.md
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_ct_20260520_130200
C:\Development\Trader\.codex\runs\v60_ct_crash_suppressor_mc_20260520_190800\ct_crash_suppressor_summary.md
C:\Development\Trader\.codex\runs\v60_ct_crash_suppressor_mc_20260520_190800\ct_crash_suppressor_ranked.csv
```

## Lead 2: Smooth Put-Wave Reserve Controller

The Market Wave divergence branch found a real put-side exposure problem:
constructive-looking market state can coincide with weak realized entries and
put overhang. A hard `skip_puts` probe showed alpha but was not shippable
because it violated the smooth controller ethos and created replacement-path
risk.

The fix was not just "scale puts down." The blocker was freed buying power.
When throttled premium was immediately recycled, later fills changed and moved
drawdowns into late-path churn, especially in 2025. The reserve mechanic keeps
the unspent premium idle until the throttled trade's original exit date.

Candidate shape:

```text
recent_weak =
    prev5_entry_tp_rate low OR prev5_entry_avg_pnl_pct low

put_overhang =
    open_put_n high OR filled_put_n high OR open_put_share high

pressure =
    recent_weak
    * constructive_market_wave
    * (0.5 + 0.5 * put_overhang)

put_scale =
    max(floor_scale, 1.0 - strength * pressure)

reserve:
    if a put is throttled, reserve the unspent premium until that trade's
    original exit date instead of recycling it into later fills.
```

Best known parameters for `wave_put_divergence_reserve_0124`:

```text
strength=0.55
floor_scale=0.45
weak_tp_floor=0.539
weak_tp_width=0.10
open_put_trigger=9
open_put_width=8
filled_put_trigger=1
filled_put_width=3
open_put_share_trigger=0.65
open_put_share_width=0.25
wave_threshold=0
wave_width=6
```

Validation lineage:

```text
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0051_n500x8_20260520_182619\wave_put_reserve_0051_n500x8_summary.md
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0124_n500x8_20260520_175629\wave_put_reserve_0124_n500x8_summary.md
C:\Development\Trader\.codex\runs\v60_prod_reserve_mc_resume_20260520_224152\production_reserve_mc_summary.md
C:\Development\Trader\.codex\runs\v60_prod_reserve_0124_mc_20260520_225350\production_reserve_mc_summary.md
C:\Development\Trader\.codex\runs\v60_prod_reserve_0124_n500_20260520_230521\production_reserve_mc_summary.md
C:\Development\Trader\experiments\daily_opportunity_allocation\top_candidates_production_reserve.csv
```

Read:

- `0051` passed the experiment-local N=500 x 8 Stage 3 screen and proved the
  reserve mechanic worked, but production-equivalent MC still showed
  return-drag fingerprints: 2025 and 22-now log-return drag, and a slight 5y
  median-log drag with flat 5y DD.
- `0124` was weaker in the experiment-local final screen but cleaner in the
  canonical production-equivalent engines. It is now the promotion candidate.
- Production-equivalent N=240 for `0124`:
  - `2020-crash`: unchanged.
  - `2024`: worst DD unchanged, mean log +0.0529, median log +0.0925.
  - `2025`: worst DD -2.49pp, mean log +0.0378, median log -0.0051.
  - `22-now`: worst DD -5.27pp, mean log -0.0372, median log +0.0607.
  - `5y`: worst DD unchanged, mean log +0.3657, median log +0.0144.
- Production-equivalent N=500 for `0124`:
  - `2020-crash`: unchanged.
  - `2024`: worst DD unchanged, mean log +0.0661, median log +0.0711.
  - `2025`: worst DD -4.93pp, mean log +0.0545, median log +0.0652.
  - `22-now`: worst DD -5.27pp, mean log +0.0399, median log +0.0953.
  - `5y`: worst DD unchanged, mean log +0.2063, median log +0.1104.
  - Collapse delta was 0.00pp everywhere.
- The original return-drag blocker on the put-overhang hedge is fixed for
  `0124` in production-equivalent MC.

Implementation guidance:

- This requires a live reserved-idle-cash implementation in the portfolio path.
- Keep it portfolio-only; no score change and no `ALGORITHM_VERSION` bump.
- Validate after implementation because the current live `trader alloc` flow is
  stateless and cannot enforce multi-day reserved cash by itself.
- The validation plumbing is default-off in `monte_carlo.py` and
  `backtest_cascade.py` via `PUT_WAVE_RESERVE_ENABLED=1` plus
  `PUT_WAVE_RESERVE_DAILY_STATE_CSV=<daily_state.csv>`. Treat that as research
  plumbing, not a shipped portfolio mechanism.
- Live ship design needs a reserve ledger or equivalent auditable state:
  reserve the unspent premium at fill time, subtract it from deployable cash
  until the expected exit/release date, but keep it in equity.
- Do not ship `wave_put_divergence_0069`; it looked good deterministically but
  failed N=500 x 8 with 5y worst DD +7.90pp.
- Do not ship `0051` now that `0124` has better production-equivalent evidence.

Key artifacts:

```text
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_sweep_20260520_150830\smooth_controller_top_candidates.csv
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_failwindow_mc_20260520_152929\wave_put_reserve_failwindow_mc_summary.md
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_broad_mc_20260520_155325\wave_put_reserve_broad_mc_summary.md
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0124_n500x8_20260520_175629\wave_put_reserve_0124_n500x8_summary.md
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0051_n500x8_20260520_182619\wave_put_reserve_0051_n500x8_summary.md
C:\Development\Trader\.codex\runs\v60_prod_reserve_0124_n500_20260520_230521\production_reserve_mc_summary.md
C:\Development\Trader\experiments\daily_opportunity_allocation\top_candidates_production_reserve.csv
```

## Rejected Or Diagnostic-Only Findings

### `v59_extra_calls_guard`

This was diagnostic only. The useful sentence is:

```text
when v60 is taking calls that v59 would not have taken, some of those extra
calls expose a crash-window failure mode.
```

The shippable lesson is not to compare v60 to v59 at runtime. Mine the common
structure behind the bad extra calls. That structure narrowed to acute crash
tape plus CT promotion of 70-74 calls.

### Broad Score-Dampen / Q90 Overflow Filters

Discovery:

```text
C:\Development\Trader\.codex\runs\v60_extra_call_wave_20260519_215034
```

Proxy MC:

```text
C:\Development\Trader\.codex\runs\v60_score_dampen_proxy_mc_20260519_222128
```

Read:

- Market-structure wave signal is real at the signal level.
- Broad hard-removal of marginal 70-74 calls is no-ship.
- It improved some 2020-crash surfaces but failed 2022, 2024, 2025, 22-now, or
  5y depending on variant.
- Path-diff showed much of the non-COVID movement came from removing
  zero-allocation overflow rows and changing MC path/tie-break behavior, not
  clean capital-bearing alpha.

### Total-Demand / Merged Allocation Controllers

Read:

- Opportunity N and total demand contain information, but direct budget
  expansion/throttle controllers were unstable.
- Early merged Bayesian controller improved some long-window DD but worsened
  2020-crash and 2024 worst DD.
- Panic-breadth-only variants passed only by becoming near no-op.
- Do not restart this family without a stricter crash-window and 2024 penalty.

### Hard `skip_puts` And Strict Put-Wave Rows

Read:

- Hard `wave_divergence_tp__skip_puts` proved there is put-side alpha, but it is
  not a production shape.
- Smooth rows `wave_put_divergence_0051`, `0066`, `strict_0298`, and
  `strict_0127` each failed at least one broader MC surface before reserve
  logic was added.
- The important learning is replacement-path churn, not that the put wave is
  dead.

### Stochastic Rollover / Raw-High Call Pocket

Report added: 2026-05-20

Verdict:

- This is not a dead end as diagnostic alpha.
- It is a dead end for a Stage 1 scoring ship in its current form.
- High raw stochastic `%K` inside call territory is a real lower-quality pocket,
  especially around marginal `CALL >=70` rows.
- Smooth stochastic rollover waves lift WR15 by pruning rows, but they reduce
  expected winner throughput. The lift is mostly purity-through-deletion, not
  additive opportunity quality.
- Best future use is Stage 3 allocation/admission tie-breaking under slot
  scarcity, crowding, or DD stress. Do not put this into `Score.overall` without
  new evidence that it improves N-aware utility.

Scope:

```text
Research worktree: C:\Development\Trader_stoch_alpha_20260519
Research branch: codex/stoch-alpha-research-20260519
Research head: 78d8405db
Active DB version during research: v60 id=60, ALGORITHM_VERSION=d4a3e9fec
```

Implementation / documentation cleanup performed in the research worktree:

- Clarified the stochastic inversion in comments and docs. Raw `%K` near `0`
  maps to a high call-friendly component; raw `%K` near `100` maps to a low
  put-friendly component.
- Files touched for clarification:
  - `database/models/core.py`: `calculate_stoch_score` docstring.
  - `database/utils/scoring.py`: CWWD / CSWC / SCW comments.
  - `.claude/docs/scoring-algorithm.md`: STOCH / SCW wording.
- This cleanup is not a scoring version update: no `Score.overall` behavior
  changed, so no version bump, recalc, or score-row write is warranted.

Research-only staging candidate added in the sandbox:

- `strategy_config.py`: `STOCH_ROLLOVER_WAVE_*` knobs.
- `database/utils/scoring.py`: `_apply_stoch_rollover_wave()`.
- `database/models/core.py` and `simulator.py`: pass `raw_stoch_signal`.
- `tests/test_scw_sector_candidate.py`: helper coverage.
- Experiment scripts:
  - `experiments/stoch_alpha/stoch_alpha_sweep.py`
  - `experiments/stoch_alpha/staging_validate.py`
  - `experiments/stoch_alpha/diagnose_staging_wave.py`
  - `experiments/stoch_alpha/staging_replay_sweep.py`
  - `experiments/stoch_alpha/monitor_run.py`

Research default after the final alignment:

```text
STOCH_ROLLOVER_WAVE_ENABLED = True
STOCH_ROLLOVER_WAVE_GATE = 70
STOCH_ROLLOVER_WAVE_AMP = 1.5
STOCH_ROLLOVER_WAVE_RAW_MID = 78.0
STOCH_ROLLOVER_WAVE_RAW_WIDTH = 4.0
STOCH_ROLLOVER_WAVE_CROSS_STRENGTH = 1.0
STOCH_ROLLOVER_WAVE_CROSS_WIDTH = 5.0
STOCH_ROLLOVER_WAVE_DECAY_DENOM = 30.0
STOCH_ROLLOVER_WAVE_DECAY_POWER = 0.5
```

Important tooling bug found:

- Early staging-native results that showed `0 changed_rows` were invalid.
- Cause: when `experiments/stoch_alpha/*.py` ran as scripts, the checkout root
  was not forced to the front of `sys.path`; Python could import a stale scoring
  silo before the edited checkout.
- Symptom: `diagnose_staging_wave.py` failed with:

```text
module 'database.utils.scoring' has no attribute '_apply_stoch_rollover_wave'
```

- Fix every staging experiment script with this pattern before importing repo
  modules:

```python
ROOT_STR = str(ROOT)
if ROOT_STR in sys.path:
    sys.path.remove(ROOT_STR)
sys.path.insert(0, ROOT_STR)
```

- After the path fix, the diagnostic run confirmed the helper fired on the
  production-equivalent scorer.

Persisted-row active v60 evidence:

| Cohort | N | WR15 | Raw `%K 80-100` N | Raw `%K 80-100` WR15 |
| --- | ---: | ---: | ---: | ---: |
| 5y `CALL >=70` | 19,706 | 73.72% | 5,811 | 69.45% |
| 5y `CALL >=75` | 7,152 | 77.07% | 2,035 | 72.43% |

This is the core discovery: raw-high stochastic inside call territory is
directionally weaker than the surrounding call book.

Quick / focused persisted-row sweeps:

- `20260519_212500_quick_strict` and `20260519_220500_focused_5y` both selected
  `call_raw_hi_a1.5_m78_w6_wk0_x0.75_d1_g70`.
- `CALL >=70`: N `19,706 -> 17,897`, WR15 `73.72% -> 74.62%`, `+0.90pp`.
- `CALL >=75`: N `7,152 -> 6,910`, WR15 `77.07% -> 77.37%`, `+0.30pp`.
- `PUT <=25`: unchanged.
- 1y smoke winner `call_raw_hi_a2.5_m82_w6_wk0.75_x0.75_d1_g70`:
  - `CALL >=70`: N `4,772 -> 4,490`, WR15 `74.539% -> 74.967%`, `+0.428pp`.
  - `CALL >=75`: N `1,695 -> 1,686`, WR15 `76.047% -> 76.038%`, flat/slightly
    negative.
  - `CALL >=80`: N `380 -> 378`, WR15 `81.58% -> 82.01%`.
  - Puts unchanged.

Full 5y persisted-row sweep:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260519_233659_persisted_full_5y_retry
```

Best combined candidate: `combo_0026_base1`.

Effective call-side formula:

```text
call_amp=1.5
call_mid=78.0
call_width=4.0
call_cross_strength=1.0
call_decay_power=0.5
call_gate=70.0
call_weekly_strength=0.0
```

Put-side add-on was noise:

```text
put_alpha=0.1
put_mid=25
put_width=4
put_cross_strength=1
put_target=27
put_gate=30
```

`PUT <=25` was unchanged. `PUT <=20` moved only two rows and improved WR15 by
`+0.0217pp`, so do not treat put-side stochastic as found alpha.

Full 5y persisted-row metrics:

| Cohort | Baseline N | Candidate N | Baseline WR15 | Candidate WR15 | WR15 Delta | Wins Delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `CALL >=70` | 19,706 | 18,466 | 73.7237% | 74.3095% | +0.5858pp | -806 |
| `CALL >=75` | 7,152 | 6,966 | 77.0694% | 77.3758% | +0.3065pp | -122 |
| `CALL >=80` | 1,536 | 1,496 | 84.2448% | 84.8930% | +0.6483pp | -24 |
| `PUT <=25` | unchanged | unchanged | unchanged | unchanged | 0 | 0 |
| `PUT <=20` | 2,653 | 2,651 | 78.7410% | 78.7627% | +0.0217pp | -1 |

Raw-high call pocket cleanup:

| Cohort | Baseline Raw `%K 80-100` | Candidate Raw `%K 80-100` | Removed Raw-High WR15 |
| --- | ---: | ---: | ---: |
| `CALL >=70` | N=5,811, WR15=69.4545% | N=4,596, WR15=70.7354% | ~64.61% |
| `CALL >=75` | N=2,035, WR15=72.4324% | N=1,855, WR15=73.0997% | ~65.56% |

The alpha is real, but it is achieved by deleting many rows that still win more
often than they lose.

1y persisted profile:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260519_233100_profile_persisted_1y
```

Profiled candidate: `call_raw_hi_a2.5_m82_w6_wk0.75_x0.75_d1_g70`.

Removed `CALL >=70` rows:

- N=282, WR15=67.73%.
- Average overall score=70; average candidate score=69.
- Average raw stochastic=90.62.
- Average `signal_minus_raw=-0.65`.
- Average weekly adjustment=7.13.
- `has_cont_pct=0%`.
- `has_dvaw_pct=0%`.
- `has_sector_pct=0%`.
- `has_scw_pct=26.6%`.
- Non-SCW subset: N=207, WR15=68.60%.
- SCW subset: N=75, WR15=65.33%.

Interpretation: the 1y signal is not a broad continuation, DVAW, sector, or SCW
story. It is mostly a specific raw-high, borderline `overall=70` call pocket.

Staging-native diagnostics after the import-path fix:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260520_122044_diagnose_staging_wave_1y_pathfix
```

- rows / scores: 193,952
- helper calls: 193,952
- score-eligible rows: 5,362
- raw80-eligible rows: 1,489
- raw80 rows with signal >= raw: 376
- changed / meta rows: 705
- Example movements were one-point dampens such as `70 -> 69`, `73 -> 72`,
  and `87 -> 86`.

This confirmed the production-equivalent scorer can express the wave once the
script imports the checkout scoring code.

Staging-native WR validation after the path fix:

1y run:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260520_123549_staging_1y_pathfix_full_best
```

| Cohort | Baseline N | Candidate N | Baseline WR15 | Candidate WR15 | WR15 Delta | Wins Delta | Removed WR15 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CALL >=70` | 5,363 | 5,027 | 74.7383% | 75.1159% | +0.3776pp | -232.14 | 69.09% |
| `CALL >=75` | 1,104 | 1,073 | 78.5647% | 79.1829% | +0.6182pp | -17.72 | 57.17% |
| `CALL >=80` | 441 | 437 | 80.6147% | 80.4296% | -0.1851pp | -4.03 | winner-heavy removed rows |
| Puts | unchanged | unchanged | unchanged | unchanged | 0 | 0 | n/a |

5y run:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260520_125847_staging_5y_pathfix_full_best
```

- rows: 930,643
- changed_rows: 2,876

| Cohort | Baseline N | Candidate N | Baseline WR15 | Candidate WR15 | WR15 Delta | Wins Delta | Removed WR15 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CALL >=70` | 21,330 | 20,032 | 73.6411% | 74.1833% | +0.5422pp | -847.25 | 65.27% |
| `CALL >=75` | 4,470 | 4,350 | 80.2001% | 80.5217% | +0.3216pp | -82.25 | 68.54% |
| `CALL >=80` | 1,804 | 1,783 | 83.4495% | 83.4215% | -0.0280pp | -18.02 | 85.83% |
| Puts | unchanged | unchanged | unchanged | unchanged | 0 | 0 | n/a |

Production-equivalent validation confirms the WR15 lift in the 70 and 75 call
buckets. It also confirms the throughput loss and slight high-tier flatness /
harm. That is why this should not ship as Stage 1 scoring.

Staging replay sweep:

```text
C:\Development\Trader_stoch_alpha_20260519\experiments\stoch_alpha\runs\20260520_153227_staging_replay_1y_full
```

Purpose:

- Run the checkout scorer once with the wave disabled.
- Attach raw stochastic values.
- Vector-sweep smooth formulas against barrier outcomes.
- Avoid rerunning the full scorer for every candidate.

Replay facts:

- Baseline staging scores: 193,952.
- Replay frame: 193,952.
- Barrier-joined assessed rows: 185,259.
- Baseline `CALL >=75` WR15: 78.56%, N=1,059.

Best headline WR candidate:

```text
call_raw_hi_a7_m78_w6_wk0_x1_d0.5_g75
```

| Cohort | N | WR15 | WR15 Delta | Wins Delta |
| --- | ---: | ---: | ---: | ---: |
| `CALL >=70` | 5,064 | 74.7433% | unchanged / negligible | n/a |
| `CALL >=75` | 972 | 80.2469% | +1.6822pp | -52 |
| `CALL >=80` | 381 | 81.6273% | +1.0126pp | -30 |

This is a purity candidate, not a production scoring candidate.

Constraint-search outcome:

```text
positive call75 wins candidates: 0
nonnegative call75 and call80 wins: 1234
nonnegative call70/call75/call80 wins: 484
```

The nonnegative-wins rows were mostly identity/no-op or too weak to matter. The
best practical N-preserving-ish rows still had negative `CALL >=75` wins_delta,
for example:

- `call_raw_hi_a2.5_m74_w4_wk0_x1_d4_g75`: `CALL >=75` N=1,035,
  WR15=79.1304%, wins_delta=-13.
- `call_raw_hi_a3.5_m78_w12_wk0_x1_d4_g75`: `CALL >=75` N=1,029,
  WR15=79.2031%, wins_delta=-17.
- `call_raw_hi_a1.5_m74_w4_wk0_x1_d2_g75`: `CALL >=75` N=1,042,
  WR15=78.9827%, wins_delta=-9.

Bottom line: no smooth stochastic formula in this grid produced positive
`CALL >=75` expected-winner throughput. The best WR candidates prune more rows.

Verification in the stochastic sandbox:

```text
python -m py_compile simulator.py database\utils\scoring.py experiments\stoch_alpha\stoch_alpha_sweep.py experiments\stoch_alpha\staging_validate.py experiments\stoch_alpha\diagnose_staging_wave.py experiments\stoch_alpha\staging_replay_sweep.py experiments\stoch_alpha\monitor_run.py
python -m pytest tests/test_scw_sector_candidate.py tests/test_continuation_echo.py
python tests/test_strategy_config_drift.py
```

Results:

- Py compile passed.
- Pytest: 9 passed.
- Strategy config drift: OK, all 546 strategy constants plus schema/source/symbol
  scans matched.

Future-agent pickup plan:

1. Do not resume Stage 1 scoring mining from the same objective unless the goal
   explicitly changes to WR purity at the expense of opportunity count.
2. If mined further, move it to Stage 3 allocation/admission:
   - treat high raw `%K` call rows as a tie-breaker when call demand exceeds
     available slots;
   - inspect crash/DD windows, same-sector crowding, CT-promotion crowding, and
     daily opportunity scarcity;
   - evaluate downstream portfolio DD and realized allocation replacement, not
     just score-bucket WR.
3. Reuse `staging_replay_sweep.py` for cheap formula exploration, but preserve
   the checkout-root `sys.path` fix.
4. Promotion gate for any future stochastic use:
   - nonnegative expected-winner throughput in `CALL >=75`;
   - no harm to `CALL >=80`;
   - portfolio-window DD improvement after replacement-path accounting;
   - no version bump unless `Score.overall` actually changes.

## Feature Lessons For Future ML Mining

Useful variables / interactions:

- Acute market-structure wave percentile, especially q90-style states.
- Sector crash pressure / sector ETF breadth under broad market stress.
- Broad EMA50 breadth and McClellan confirmation.
- CT trend state, specifically marginal calls that qualify for CT promotion.
- Score boundary behavior at 70-74 calls.
- Recent realized entry quality: `prev5_entry_tp_rate` and
  `prev5_entry_avg_pnl_pct`.
- Open-book put pressure: `open_put_n`, `filled_put_n`, and `open_put_share`.
- Replacement-path effects after throttles or skips; always inspect actual
  filled trades, not just source candidates.

Bad directions:

- Runtime v59/v60 overlap guard.
- Hard score cliffs as final shapes.
- Broad "remove all marginal calls under stress" filters.
- Controllers that free buying power without reserving it.
- No-op panic guards dressed up as alpha.

## Recommended Next Agent Path

1. Implement `ct_crash_suppress` in a clean portfolio/cascade staging branch.
   Keep the action limited to CT-call promotion suppression under the acute
   crash selector.
2. Validate with production-equivalent MC across 2020-crash, covid-peak,
   2020-full, 2022, 2024, 2025, 22-now, and 5y. Use stable labels and at least
   the same or stronger seed coverage than the experiment run.
3. Separately implement reserved idle cash for
   `wave_put_divergence_reserve_0124` in the live portfolio path. Do not rely
   on the experiment CSV dependency as the final live surface.
4. Validate the reserve implementation with N=500 x 8 or stronger. Confirm the
   production path reproduces the production-equivalent `0124` result:
   2020-crash and 2024 DD stable, 2025 and 22-now DD improved, 5y DD stable,
   and no mean/median log-return drag on non-no-op windows.
5. Only after both mechanisms pass independently should a combined controller be
   tested. Combination may create path interactions and must not be assumed
   additive.

Ship stop rules:

- Any 2020-crash, covid-peak, 2020-full, 2022, 2024, 2025, 22-now, or 5y
  worst-DD regression is a stop.
- Any collapse-rate regression is a stop.
- Any 2024 return drag must be explicitly justified by DD improvement; otherwise
  stop.
- Do not bump `ALGORITHM_VERSION` unless `Score.overall` changes.
- If a portfolio mechanism ships, update `strategy_config.py`,
  `mechanism_registry.py`, docs, and run the portfolio drift/registry tests per
  normal ship procedure.

## Current Artifact Index

Primary docs:

```text
experiments\daily_opportunity_allocation\README.md
experiments\daily_opportunity_allocation\FINDINGS.md
experiments\daily_opportunity_allocation\ALPHA_LEDGER.md
experiments\daily_opportunity_allocation\V60_FINDINGS.md
experiments\daily_opportunity_allocation\top_candidates_production_reserve.csv
```

Primary scripts:

```text
experiments\daily_opportunity_allocation\mine_v60_extra_call_wave.py
experiments\daily_opportunity_allocation\validate_score_dampen_proxy_mc.py
experiments\daily_opportunity_allocation\analyze_score_dampen_path_diff.py
experiments\daily_opportunity_allocation\validate_ct_crash_suppressor_mc.py
experiments\daily_opportunity_allocation\analyze_smooth_put_wave_path_diff.py
experiments\daily_opportunity_allocation\validate_smooth_controller_mc.py
experiments\daily_opportunity_allocation\validate_production_reserve_mc.py
```

Primary run directories:

```text
C:\Development\Trader\.codex\runs\v60_extra_call_wave_20260519_215034
C:\Development\Trader\.codex\runs\v60_score_dampen_proxy_mc_20260519_222128
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_combined_20260520_130700
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_ct_20260520_130200
C:\Development\Trader\.codex\runs\v60_ct_crash_suppressor_mc_20260520_190800
C:\Development\Trader\.codex\runs\v60_daily_opportunity_smooth_20260519_082415
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_sweep_20260520_150830
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0124_n500x8_20260520_175629
C:\Development\Trader\.codex\runs\v60_wave_put_reserve_0051_n500x8_20260520_182619
C:\Development\Trader\.codex\runs\v60_prod_reserve_mc_resume_20260520_224152
C:\Development\Trader\.codex\runs\v60_prod_reserve_0124_mc_20260520_225350
C:\Development\Trader\.codex\runs\v60_prod_reserve_0124_n500_20260520_230521
```

## Current Implementation / Validation Notes

The latest reserve validation added default-off production-equivalent research
plumbing in the shared 30DTE engines:

- `monte_carlo.py`: `PUT_WAVE_RESERVE_ENABLED`,
  `PUT_WAVE_RESERVE_DAILY_STATE_CSV`, reserve-wave parameters, stable
  label hashing for deterministic MC seeds, reserved-cash accounting in
  `run_single_sim`.
- `backtest_cascade.py`: same default-off reserve-wave loader and reserved-cash
  accounting in deterministic cascade replay.
- `experiments/daily_opportunity_allocation/validate_production_reserve_mc.py`:
  runs baseline and candidate policies in separate Python child processes,
  reconnects to MySQL per window, retries transient read timeouts, and can reuse
  `--baseline-json` for resume.

These edits are validation plumbing, not a production ship. A live ship still
needs explicit allocator state so a real `trader alloc` workflow can carry
reserved cash across days. Without that ledger, live allocation would still
recycle the freed premium that the candidate is designed to hold idle.

Latest verification after the validation plumbing:

```text
python -m py_compile monte_carlo.py backtest_cascade.py experiments\daily_opportunity_allocation\validate_production_reserve_mc.py experiments\daily_opportunity_allocation\smooth_controller_sweep.py experiments\daily_opportunity_allocation\validate_smooth_controller_mc.py
python tests\test_strategy_config_drift.py
python tests\test_algorithm_version_sync.py
Get-Content ALGORITHM_VERSION  # d4a3e9fec
```

Git/workspace notes for the next workflow:

- `experiments/daily_opportunity_allocation/` is still local experiment
  research and may be untracked in this checkout. Preserve it.
- `api.py` had unrelated pre-existing local changes during this workflow; do
  not revert them while working on reserve allocation.
- GitNexus status was stale during the latest pass and duplicate symbol names
  resolved to archived `algorithm_versions/v48/portfolio_sources/*` in some
  impact calls. Use GitNexus as advisory, but verify root-file call paths
  directly before editing production symbols.

## WR15 Alpha-Fisher / Admission70 Findings

This section is a Stage 1 scoring-alpha handoff added here because the v60 daily
opportunity work and the WR15 alpha-fisher work were run in the same research
window. Keep the separation clear:

- Daily opportunity allocation / COVID drawdown work above is Stage 3 portfolio
  expression and should not bump `ALGORITHM_VERSION` unless `Score.overall`
  changes.
- WR15 alpha-fisher / admission70 work below is Stage 1 scoring research. If it
  ships, it changes `Score.overall`, needs a new algorithm version, and must go
  through the normal scoring ship procedure.
- Do not chain Stage 1 scoring, Stage 2 barrier, and Stage 3 portfolio changes
  into one ship. Mine and validate them independently.

### Baselines And Worktrees

v60 active baseline:

- Active DB version during the v60 mining pass: `v60`, version id `60`.
- Current shipped v60 commit during the active-DB comparison:
  `d4a3e9fec`.
- Relevant v60 change context: SCW plus DD call cap were already present in the
  active DB baseline, so same-runtime alpha-disabled wins were not enough. The
  final question was incremental alpha versus active shipped v60.

v60 alpha worktree:

```text
C:\Development\Trader_ml_alpha_fisher_v60
```

v61 continuation baseline:

- Active DB version at the time v61 mining started: `v61`, version id `61`.
- Active v61 commit: `e6fbdbde1`.
- Commit message: `v61 scoring: add weekly mature call guard`.
- v61 worktree:

```text
C:\Development\Trader_ml_alpha_fisher_v61
```

The v61 worktree inherited the v60 alpha-radar tooling but was recalibrated
against persisted active v61 score rows. Some scripts and status phases still
say `v60` in their names because the tooling was built during the v60 cycle; the
version id and output paths are the authoritative signal.

## v60 WR15 Search Results

The first broad v60 WR15/N search used a DB-backed no-alpha v60 panel from
persisted score rows and searched three scopes:

- `all`: any selected CALL row.
- `below75`: rows below the 75+ call threshold.
- `tradeable`: rows already near or above the tradeable range.

Primary run:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_wr15_db_recalibration_20260519_081515
```

Primary panel:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\staging_wr15_panel\v60_db_no_alpha_panel_20200101_20260516.parquet
```

Primary search outputs:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\wr15_algorithm
```

Top broad candidates:

| Candidate | Scope | Selected N | WR15 | Residual / excess signal | Read |
| --- | --- | ---: | ---: | ---: | --- |
| `all_eval4692` | all | 53,137 | 73.09% | +4.84pp residual | Too broad; mostly low-score rows, high distortion risk. |
| `below75_eval3464` | below75 | 43,345 | 72.64% | +4.65pp residual | Large N, but not specifically promotion-quality. |
| `tradeable_eval2663` | tradeable | 12,658 | 76.61% | +2.29pp residual | Cleaner WR, but still mixed existing 75+ and 70-74 rows. |

### Broad Stage 1 Validation

Same-runtime validation run:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_stage1_profiles_20260519_134007
```

Outputs:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\stage1_validation
```

Results:

| Candidate | Added CALL rows | 75+ N delta | 75+ WR15 delta | 70+ WR15 delta | Score delta quality | Verdict |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `tradeable_eval2663_same_runtime_2329d` | 9,473 | 3,699 -> 8,557 (+4,858) | 78.4% -> 75.9% (-2.5pp) | +0.2pp | max delta 4, `abs>=5` 0 | No-ship. It bought N by diluting high-tier WR15. |
| `below75_eval3464_same_runtime_2329d` | 12,827 | 3,699 -> 10,276 (+6,577) | 78.4% -> 76.3% (-2.1pp) | +0.6pp | max delta 5, `abs>=5` 5,285 (0.46%) | No-ship. Too broad and too much 75+ dilution. |
| `all_eval4692_same_runtime_2329d` | 9,444 | 3,699 -> 8,864 (+5,165) | 78.4% -> 75.9% (-2.5pp) | +0.4pp | max delta 5, `abs>=5` 3,671 (0.32%) | No-ship. Broad low-score distortion. |

Read: broad WR15/N utility can look good on selected rows while still being a
bad scoring change. The ship-relevant question is not just "does the selected
cohort win?" It is "does promoting these rows preserve or improve the actual
75+ and high-tier score buckets?"

## Focused Admission70 Retune

The broad search failure led to a narrower `admission70` scope. This scope
targets promotable `70-74` CALL rows rather than all selected rows.

Search run:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_admission70_search_20260519_181352
```

Search output:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_12000calls.json
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_12000calls.md
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_12000calls.csv
```

Search constraints:

- `score_scope=admission70`.
- `n_calls=12000`.
- `wr_floor=0.78`.
- `min_n=300`.
- `year_wr_floor=0.72`.
- `min_year_n=25`.
- `min_stability=0.70`.

Best focused candidates:

| Candidate | Rank | N | WR15 | Residual / excess signal | Stability | Read |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `admission70_eval3613` | 1 | 1,194 | 79.15% | +7.80pp | 7/7 years | Best N profile. |
| `admission70_eval1758` | 2 | 1,040 | 79.52% | +8.18pp | 7/7 years | Good WR/N, but later had a score-delta anomaly. |
| `admission70_eval981` | 15 | 566 | 80.74% | +9.40pp | 7/7 years | Cleanest preservation profile. |

### Focused Same-Runtime Validation

Same-runtime admission70 validation run:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_admission70_stage1_20260519_202337
```

Outputs:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\stage1_validation\admission70_eval3613_same_runtime_2329d.json
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\stage1_validation\admission70_eval1758_same_runtime_2329d.json
C:\Development\Trader_ml_alpha_fisher_v60\.cache\ml_alpha_radar\stage1_validation\admission70_eval981_same_runtime_2329d.json
```

Results versus the same-runtime alpha-disabled baseline:

| Candidate | Added CALL rows | Base score range admitted | 75+ N delta | 75+ WR15 delta | 75+ WR30 delta | 70+ WR15 delta | Score deltas | Read |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| `eval3613` | 2,083 | 71-73 | +1,042 | flat at 78.4% | +0.3pp | +0.1pp | mean +0.00568, max 4, `abs>=5` 0 | Highest-N clean same-runtime candidate. |
| `eval1758` | 1,817 | 71-73 | +844 | +0.1pp | +0.1pp | +0.1pp | mean +0.00501, max 19, `abs>=5` 9 | Promising, but max-delta anomaly needs diagnosis before reuse. |
| `eval981` | 928 | 71-73 | +502 | +0.2pp | +0.2pp | +0.1pp | mean +0.00256, max 4, `abs>=5` 0 | Cleanest preservation profile. |

Read: the admission70 signal is real against a no-alpha / same-runtime baseline.
The useful shape is narrow: 70-74 CALL rows, mostly base 71-73, small positive
score deltas, and no high-tier bucket churn. This is where future mining should
start, not the broad all/below75/tradeable surfaces.

## Active-DB v60 Comparison

The same-runtime wins did not clear the final incremental test against the
already-shipped active v60 DB baseline.

First active-DB run:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_admission70_active_db_20260520_121015
```

This run completed `eval981` and failed `eval3613` due to a MySQL read timeout
while reading active v60 `Score` rows. The validator was then repaired so
`_stored_scores` fetches active scores in sorted 75-symbol chunks.

Retry run for `eval3613`:

```text
C:\Development\Trader_ml_alpha_fisher_v60\.codex\runs\v60_admission70_eval3613_active_db_retry_20260520_173501
```

Active-DB results:

| Candidate | Added CALL rows | Base score range admitted | 75+ N delta vs active DB | 75+ WR15 delta | 80+ WR15 delta | 70+ WR15 | Score delta quality | Verdict |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| `eval981_active_db_2329d` | 1,256 | 52-74 | 6,023 -> 4,199 (-1,824) | 76.1% -> 78.6% (+2.5pp) | 82.3% -> 79.9% (-2.4pp) | flat at 72.8%, N +399 | `abs_delta_gte_5` 11,105 (0.9633%), max +32, min -30 | No-ship. Improved headline 75+ WR by throwing away too much active 75+ N and hurting 80+. |
| `eval3613_active_db_2329d` | 2,046 | 52-74 | 6,023 -> 4,739 (-1,284) | 76.1% -> 78.4% (+2.3pp) | 82.3% -> 79.9% (-2.4pp) | flat at 72.8%, N +399 | `abs_delta_gte_5` 11,165 (0.9685%), max +32, min -30 | No-ship. Same failure mode with less N loss but still too much high-tier damage. |

Important interpretation:

- These profiles did not prove "no alpha exists." They proved this specific v60
  admission70 mapping was not incremental to active v60.
- Same-runtime validation showed real signal against the weaker/no-alpha
  baseline.
- Active v60 already contained some of the useful scoring behavior. The proposed
  profile partly replaced or removed existing active-v60 75+ winners rather than
  adding clean new winners.
- The active-DB base score range expanding down to 52-74 is a red flag. An
  admission-only promoter should not create broad low-score rewrites during
  active comparison.
- The active-DB failure was not a DD or portfolio issue. It failed before Stage
  3 because high-tier scoring utility was not preserved.

## v60 No-Ship Decision

Do not ship any of the v60 WR15 alpha-fisher profiles as-is.

Explicit blockers:

- Broad profiles diluted 75+ WR15 by more than 2pp.
- Focused same-runtime profiles were promising, but active-DB comparison lost
  1,284 to 1,824 active 75+ rows.
- Active-DB comparison degraded 80+ WR15 by 2.4pp.
- Active-DB comparison produced broad score deltas: about 0.96% of compared
  rows had `|delta| >= 5`, with max deltas around +32/-30.
- The active-DB admitted base score range reached 52-74, which is incoherent for
  a clean `70-74` admission-only scoring alpha.

What remains valuable:

- The admission70 surface is a useful residual-alpha search region.
- `eval981` is the clean preservation reference from same-runtime validation.
- `eval3613` is the high-N reference from same-runtime validation.
- Future work should mine against the active baseline directly, not just against
  alpha-disabled panels.

## Tooling Map For Future Agents

Alpha-radar tool directory:

```text
experiments\ml_alpha_radar
```

Core scripts and roles:

| Script | Role | Notes |
| --- | --- | --- |
| `build_v60_db_wr15_panel.py` | Builds a DB-backed WR15 panel from persisted `Score` rows and extracted `weight_info` fields. | Legacy `v60` name; accepts other version ids in the runner path. |
| `v60_wr15_db_recalibration_runner.py` | Durable background-style runner for panel build plus WR15 selector search. | Legacy `v60` name; for v61 it was launched with `version_id=61`. |
| `search_wr15_algorithm.py` | Bayesian / randomized alpha selector search. | Use `--score-scope admission70` for focused 70-74 promotion mining. |
| `validate_stage1_candidate.py` | Same-runtime and active-DB Stage 1 validator. | Supports `--profile-json`, `--profile-eval`, and `--profile-name`. |
| `stage1_profile_validation_runner.py` | Runs a small set of candidate profiles through Stage 1 validation with durable artifacts. | Use this for top 2-3 candidates, not every search hit. |

Important fixes already made:

- `simulator.py` tuple unpack bug was fixed in the alpha worktree path:
  `compute_overall_score` returns `(overall, weight_info, vol_update)`, so the
  simulator must unpack `overall, weight_info, vol_update`.
- Active DB score reads in `validate_stage1_candidate._stored_scores` were
  changed to symbol chunks after a MySQL read timeout. Preserve this pattern.
- The v61 worktree has a staging alpha hook:
  `WR15_SCORING_ALPHA_ENABLED` and `WR15_SCORING_ALPHA_PARAMS`.
- The v61 simulator path now plumbs market/regime/breadth context into
  `compute_overall_score` for validation parity:
  `regime_composite`, `breadth_score`, `vix_close`, `market_trend_score`,
  `sector_etf_market_wave_signed`, and `sector_etf_crash_echo`.

Validation checks that passed after the v61 hook:

```text
python -m compileall strategy_config.py database\utils\scoring.py simulator.py tests\test_strategy_config_drift.py experiments\ml_alpha_radar\validate_stage1_candidate.py experiments\ml_alpha_radar\stage1_profile_validation_runner.py
python tests\test_strategy_config_drift.py
python experiments\ml_alpha_radar\validate_stage1_candidate.py --symbols AAPL,MSFT --lookback 365 --name smoke_v61_alpha_hook --same-runtime-baseline
```

Smoke output:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\stage1_validation\smoke_v61_alpha_hook.md
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\stage1_validation\smoke_v61_alpha_hook.json
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\stage1_validation\smoke_v61_alpha_hook.csv
```

Smoke read: placeholder v61 alpha params were inert. The smoke compared 498
score pairs, kept peak counts unchanged, and produced zero score deltas.

## Active v61 Continuation

Active v61 residual alpha run:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.codex\runs\v61_wr15_residual_20260520_222341
```

Run log:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.codex\runs\v61_wr15_residual_20260520_222341\run.log
```

Recent log:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.codex\runs\v61_wr15_residual_20260520_222341\run.recent.log
```

Panel output:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\staging_wr15_panel\v61_db_active_panel_20200101_20260516.parquet
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\staging_wr15_panel\v61_db_active_panel_20200101_20260516.json
```

Search outputs:

```text
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_16000calls.json
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_16000calls.md
C:\Development\Trader_ml_alpha_fisher_v61\.cache\ml_alpha_radar\wr15_algorithm\wr15_algorithm_admission70_16000calls.csv
```

Search setup:

- Version id: `61`.
- Source score rows: persisted active v61 DB rows.
- Source long panel:
  `C:\Development\Trader_ml_alpha_fisher\.cache\ml_alpha_radar\version_panel_v44_v50_v53_v55_v57_v59_20200101_20260516.parquet`.
- Scope: `admission70`.
- Calls: `16000`.
- `min_n=300`.
- `wr_floor=0.78`.
- `year_wr_floor=0.72`.
- `min_year_n=25`.
- `min_stability=0.70`.

2026-05-25 status update:

- The v61 residual search and the same-runtime / active-DB validations completed.
- Search quality was real: `eval13333` selected N=1,975 at WR15=79.14%
  with residual +7.24pp and excess +143.0; `eval6920` selected N=2,123
  at WR15=78.10% with residual +6.20pp and excess +131.6.
- Active-DB validation did not clear ship gates. `eval13333` and `eval6920`
  both increased 75+ N but degraded active-v61 80+ WR15 by about 2.8pp and
  produced broad score deltas (`abs(delta)>=5` around 0.86%).
- Current next region is not another alpha-disabled admission panel. Mine an
  active-baseline residual ledger that promotes missed 70-74 winners and softly
  dampens weak 75-84 incumbents with narrow score deltas.
- The consolidated current backlog now lives in `alpha_mining/NEW_LEADS.md`.

## Future Mining Protocol

Use this sequence for v61 and later. Do not restart from the broad v60 surfaces.

1. Wait for the active-v61 admission70 search to complete.
2. Summarize top candidates by N, WR15, residual/excess wins, yearly stability,
   and exact score-band composition.
3. Only take candidates forward if they are primarily `70-74` rows and do not
   need broad low-score movement to work.
4. Run same-runtime Stage 1 validation for the top 2-3 candidates. This proves
   the runtime hook and the candidate mapping work in the scoring code.
5. Run active-v61 DB comparison for any same-runtime pass. This is the real
   incremental test.
6. Stop before ship if active-v61 75+ N is lost, 80+ WR15 degrades materially,
   or score deltas become broad.
7. If a candidate clears active-v61 comparison, then plan the algorithm-version
   ship. Do not stealth-update a `Score.overall` change.
8. After scoring ship evidence clears, run normal recalc / assessment /
   research-pack / docs workflow from the designated ship checkout.

Recommended next search region if the 16k v61 admission70 run fails:

- Mine an active-baseline residual ledger, not just an alpha-disabled panel:
  missed active-v61 winners in `70-74`, failed active-v61 incumbents in `75-84`,
  and rows where active v61 moved the score in the wrong direction.
- Treat the task as a two-sided residual controller:
  softly promote missed winners and softly damp weak incumbents.
- Keep the final shape smooth and narrow. Hard thresholds are acceptable as
  diagnostic probes but should be refit into a smooth score modifier before
  ship consideration.
- Keep sector clustering and drawdown defense in Stage 3 portfolio exposure
  waves. Do not contaminate the Stage 1 WR15 + high-tier-N objective unless the
  sector signal independently proves directional WR15 value.

## Future Ship Gates

Minimum Stage 1 gates for this family:

- 75+ WR15 must be preserved or improved versus active DB baseline.
- 75+ N must be preserved or increased versus active DB baseline. A loss larger
  than normal row noise is a stop.
- 80+ WR15 must not degrade materially. Treat more than 1.0pp degradation as a
  stop unless there is an explicit user-approved tradeoff.
- 85+/90+/95+ buckets must remain unchanged or improve. Any high-tier churn must
  be explained by row-level evidence.
- `70+` utility must remain coherent; do not improve 75+ by wrecking the broader
  call surface.
- Admission-only profiles should mostly admit rows with base score `70-74`.
  Seeing base scores down near the low 50s in active-DB comparison is a runtime
  mismatch or broad rewrite red flag.
- For admission-only profiles, `abs_delta_gte_5` should be near zero. Anything
  around 0.1% needs review; around 1% is a no-ship signal.
- Yearly stability must persist after runtime validation, not just in the search
  panel.
- No production score rows should be written from an alpha-fisher worktree until
  the candidate is deliberately elevated through the normal scoring ship path.

## Current Lead Index

The copy-paste v61 continuation prompt that used to live here is superseded by
the ranked backlog in:

```text
alpha_mining\NEW_LEADS.md
```

Use that file for future mining order, active-baseline residual instructions,
portfolio-capacity follow-ups, and null-trap stop rules.

## Weekly Metrics Research / v61 Weekly Mature Call Guard

This section records the weekly-metric research pass run from the isolated
algorithm-refinement worktree:

```text
C:\Development\Trader_weekly_alpha_v60_20260520
```

Branch pushed:

```text
codex/weekly-alpha-research-v60-20260520
```

The branch contains the v61 shipped scoring change and documentation commits:

```text
e6fbdbde1 v61 scoring: add weekly mature call guard
4ddeabd50 Bump ALGORITHM_VERSION for v61 weekly mature call guard
813450b53 Add v61 weekly mature call guard silo
6910f76b0 Document v61 weekly mature call guard ship
```

Keep the scope separation clear:

- The daily opportunity allocation work earlier in this file is Stage 3
  portfolio research.
- The WR15 alpha-fisher work earlier in this file is Stage 1 admission70 scoring
  research.
- The weekly-metrics work in this section became a Stage 1 scoring ship in v61
  because it changes `Score.overall`.
- Do not combine this with Stage 2 barrier or Stage 3 allocation changes in the
  same ship.

### Executive Read

There was alpha in weekly and weekly-adjacent metrics, but it was not broad
"more weekly bullish = more score" alpha. The useful signal was a specific
CALL-side maturity/climax failure:

```text
CALL score already near the tradable band
AND weekly composite / weekly base bias are extended
AND weekly volume force is elevated
AND 4-week weekly return is extended
AND fresh weekly momentum is weak enough not to relieve the risk
```

The shipped v61 mechanism is the Weekly Mature Call Guard. It is a smooth
score-stage guard applied after the Daily Volume Authority Wave. It drifts
mature/extended CALL setups toward `target=60.1806` with small persisted score
moves, mostly -1 or -2 points in the final staging run.

Interpretation: v61 is **DD/quality compression**, not a broad N/utility
expansion. It improves lower CALL bucket WR15 and major drawdown surfaces by
cutting weak mature CALL exposure. It should not be cited as a free alpha/N
expansion because total 5y WR15 utility and CALL 75+ N both fall.

### Discovery Dataset And Search Surface

Primary discovery run:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639
```

Key artifacts:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639\summary.json
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639\cohort_profile.csv
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639\cohort_profile_top_abs.csv
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639\variants.csv
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639\targeted_weekly_midband_guard.csv
```

Run setup:

- Active baseline: v60, DB version id `60`, commit `d4a3e9fec`.
- Cutoff: `2026-05-15`.
- Lookback: `1825` days.
- Dataset rows: `467,544`.
- Baseline CALL 70+ WR15 in this panel: `60.60%` on `19,898` rows.
- Baseline CALL 75+ WR15: `63.79%` on `7,216` rows.
- Baseline CALL 80+ WR15: `70.01%` on `1,547` rows.

Strong cohort signals found:

| Feature / bucket | Side | N | WR15 delta vs side | z | Read |
| --- | --- | ---: | ---: | ---: | --- |
| `w_mom` 5.0-7.9 | CALL | 4,454 | +5.72pp | +8.87 | Fresh weekly momentum is genuinely bullish. |
| `w_mom` 0.5-3.0 | CALL | 4,406 | -5.20pp | -8.01 | Weak fresh momentum is dangerous. |
| `wk_ret4` moderate positive | CALL | 3,980 | +4.37pp | +6.31 | Moderate weekly advance can be constructive. |
| `wk_ret4` extended high bucket | CALL | 3,981 | -5.19pp | -7.49 | Extended 4-week weekly move becomes maturity risk. |
| `wv_force1` high bucket | CALL | 3,980 | -4.47pp | -6.46 | Weekly volume force is not monotonic; high force can mark climax. |
| `w_comp` 64-68 | CALL | 3,325 | +3.07pp | +3.96 | Moderate weekly composite is constructive. |
| `w_comp` 68-84 | CALL | 4,986 | -3.30pp | -5.51 | Very high weekly composite can be late. |
| `w_bias` 3.1-6.3 | CALL | 4,114 | +2.59pp | +3.82 | Moderate bias helps. |
| `w_bias` 8.2-14.2 | CALL | 4,006 | -3.94pp | -5.71 | Extended bias becomes risk. |

Mechanism-overlap lesson:

- Existing `scw_dampen` fired on `4,363` CALL rows and that cohort had WR15
  `57.46%` versus `61.49%` for the rest, z `-4.81`.
- Existing `wvd_dampen`, `cwwd_dampen`, and `cwcf_dampen` also showed negative
  CALL cohorts, but each covered only part of the maturity/climax pattern.
- The new weekly maturity signal was residual to v60 because it uses the
  combination of weekly extension, weekly-volume force, 4-week return, and weak
  fresh weekly momentum after Daily Volume Authority Wave has already run.

Main lesson: weekly metrics are inverted-U / phase signals. Moderate weekly
strength helps; extended weekly strength with weak new momentum hurts. Future
work should model this as phase/maturity, not as a stronger linear boost.

### Rejected Broad Candidate Families

The first broad winner was not shippable:

```text
family=call_resonance_lift
id=220
affected_n=10067
affected_wr15=61.76%
avg_score_delta=+1.08
```

It looked attractive on objective utility but failed the bucket-protection
reading:

- CALL 75+ N increased by `398`, but CALL 75+ WR15 fell `-0.43pp`.
- CALL 80+ N increased by `544`, but CALL 80+ WR15 fell `-1.81pp`.
- CALL 85+/90+/95+ were unchanged, so the apparent utility came from pushing
  more mediocre rows into the lower high buckets.

Verdict: no-ship. It confirmed that weekly metrics contain signal, but broad
CALL resonance lifts dilute the high-tier surface.

Other no-ship implications:

- Do not implement a broad weekly-composite boost.
- Do not implement a broad weekly-volume-force boost.
- Do not treat high `wk_ret4` as bullish by itself.
- Do not retry rolling weekly as the obvious weekly fix; v42 rolling weekly was
  already shipped and reverted after catastrophic WR regression.

### Refined Guard Search

Refined guard run:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_refine_gate_20260519_230000
```

Key artifacts:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_refine_gate_20260519_230000\summary.json
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_refine_gate_20260519_230000\passing_candidates.csv
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_refine_gate_20260519_230000\refined_candidates.csv
```

Search scale:

- `2,500` samples.
- `29` passing candidates.
- Seed data came from `targeted_weekly_midband_guard.csv`.

The highest utility candidate was not used:

```text
id=2235
hard_pass=false
affected_n=10877
call75_dn=-2076
call75_dwr_pp=+1.35
call80_dn=-558
call80_dwr_pp=+3.20
```

Read: too much N compression. It improved WR by removing many signals and did
not meet the intended ship shape.

The production candidate was the best passing constrained row:

```text
id=1642
hard_pass=true
affected_n=4530
call75_dn=-644
call75_dwr_pp=+0.32
call80_dn=-162
call80_dwr_pp=+1.76
call85_dn=-13
call85_dwr_pp=+0.37
call90_dn=0
call90_dwr_pp=0.00
```

Important caveat: candidate `1642` still flagged a soft W6 / N-floor style
review surface (`w6_candidate_violations=1`). The reason it was allowed forward
was not "free bucket alpha"; it was that the later production-equivalent stress
windows showed major DD compression.

### What Shipped In v61

Production mechanism:

```text
Weekly Mature Call Guard
```

Source anchors in the v61 branch:

```text
strategy_config.py
database\utils\scoring.py
database\models\core.py
simulator.py
api.py
mechanism_registry.py
ALGORITHM_VERSION
algorithm_versions\v61\
```

Structured config:

```text
WEEKLY_MATURE_CALL_GUARD_ENABLED=True
score_lo=72.49981169711549
score_lo_width=4.318306546556302
score_hi=81.42326751772556
score_hi_width=3.1545639155873073
target=60.18061302183849
k=0.21365891783568708
bias_mid=6.358089714737812
bias_width=2.3620738069477167
bias_weight=0.25938373337360876
comp_mid=68.73904300497583
comp_width=5.201703912769646
comp_weight=0.4363515673549808
wv_mid=0.08491400832688323
wv_width=0.022388072253294658
wv_weight=0.06294045942335882
wk4_mid=0.11181277557817569
wk4_width=0.037548436669286936
wk4_weight=0.42930945850864727
mom_mid=3.5606659252920165
mom_width=3.661404150277635
mom_relief=0.45138359953402063
```

Formula shape:

```text
band =
  sigmoid(score, score_lo, score_lo_width)
  * (1 - sigmoid(score, score_hi, score_hi_width))

mature =
  bias_weight * sigmoid(w_bias, bias_mid, bias_width)
  + comp_weight * sigmoid(w_comp, comp_mid, comp_width)
  + wv_weight * sigmoid(wv_force1, wv_mid, wv_width)
  + wk4_weight * sigmoid(wk_ret4, wk4_mid, wk4_width)

relief =
  1 - mom_relief * sigmoid(w_mom, mom_mid, mom_width)

risk = clamp(band * mature * relief, 0, 1)
dampen = k * risk * (score - target)
new_score = round(score - dampen)
```

Guard only applies to CALL-side scores (`overall >= 50`) and only when
`score > target`. It records audit metadata in:

```text
weight_info['weekly_mature_call_guard']
```

Metadata includes `before`, `after`, `delta`, `dampen`, `risk`, `mature`,
`band`, `w_bias`, `w_comp`, `w_mom`, `wv_force1`, and `wk_ret4`.

Final staging-native gate:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_staging_gate_final_20260520_141009
```

Final staging summary:

- Active baseline: v60, DB id `60`, commit `d4a3e9fec`.
- Symbols: `891`.
- Scored pairs: `930,643`.
- Simulated peaks: `16,005`.
- DB comparison peaks: `49,426`.
- Guard events: `3,713`.
- Average score delta on guard events: `-1.012`.
- Max delta: `-1`.
- Min delta: `-2`.

Guard events by pre-guard bucket:

| Pre-guard bucket | Events |
| --- | ---: |
| `<70` | 3 |
| `70-74` | 2,198 |
| `75-79` | 1,099 |
| `80-84` | 385 |
| `85-89` | 28 |

Read: the shipped guard is narrow in score movement but large enough in row
count to materially change tradeable CALL exposure. It mostly moves boundary
and lower-high CALLs, with very little direct top-tier churn.

### v60 to v61 Production-Equivalent Evidence

v61 research pack:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.cache\algorithm_versions\v61\research_pack\manifest.json
```

Clean same-worktree v60 baseline pack:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.cache\algorithm_versions\v60\research_pack\manifest.json
```

Operational proof:

- DB AlgorithmVersion row: v61, id `61`.
- v61 commit: `e6fbdbde1`.
- v61 score coverage: `1,755,800` rows.
- Coverage date span: `2015-12-31` through `2026-05-20`.
- Score dates: `2,659`.
- v61 research pack complete for assessment, temporal, WR15 utility, WR7
  utility, and portfolio stress windows.

5y WR15 utility comparison:

| Surface | v60 | v61 | Delta |
| --- | ---: | ---: | ---: |
| CALL 75-79 WR15 | 74.57% | 79.22% | +4.65pp |
| CALL 80-84 WR15 | 84.08% | 84.66% | +0.58pp |
| CALL 85-89 WR15 | 83.86% | 83.85% | flat |
| CALL 90-94 WR15 | 78.45% | 75.62% | -2.83pp on small N |
| CALL 95-100 WR15 | 100.00% | 95.50% | -4.50pp on tiny N |
| CALL 75+ N | 4,226 | 2,245 | -1,981 |
| Total 5y WR15 utility | 7,397.6 | 5,505.6 | -1,892.0 |

Portfolio stress windows:

| Window | v60 max DD | v61 max DD | DD delta | Return / compounding read |
| --- | ---: | ---: | ---: | --- |
| `covid_crash_2020` | 99.61% | 76.63% | -22.98pp | v60 lost -95.69%; v61 finished +306.71%. |
| `covid_cycle_2020_2021` | 99.66% | 82.03% | -17.64pp | v61 still lower DD but lower long compounding than unconstrained v60. |
| `2020_now` | 99.66% | 82.03% | -17.64pp | v61 lower DD, slightly lower log10 equity multiple. |
| `22_now` | 65.11% | 60.50% | -4.61pp | v61 lower DD; log10 equity multiple falls 18.634 -> 16.689. |

Ship read:

- The DD win is real and large in the crash windows.
- The quality win is concentrated in lower CALL high buckets.
- The cost is real: CALL 75+ N compresses by about 47%, and total 5y WR15
  utility falls.
- Therefore v61 should be described as DD-first / quality-compression alpha, not
  as a broad scoring utility improvement.

### Ship Gate Caveats

W5 / throughput remained a soft review, not a clean pass:

- Put-side p<=15 and p16-20 remained `REVIEW REQUIRED`.
- 80-84 and p21-25 were marginal.
- The ship was accepted because the portfolio stress-window DD gains were large
  and directly addressed production risk.

Future agents should not use this as precedent for ignoring W5. The correct
interpretation is narrower:

```text
W5 can be a review gate when a score-stage change is explicitly DD-first and
downstream production-equivalent stress windows improve enough to justify N
compression.
```

### What Not To Re-Test As-Is

Do not re-test these without a materially different shape:

- Broad weekly-composite boosts.
- Broad weekly-volume boosts.
- Broad CALL resonance lifts.
- Hard weekly maturity cliffs.
- Rolling weekly replacement as a fix for calendar weekly instability.
- A top-tier N collapse dressed up as high WR.
- A candidate evaluated only against same-runtime simulated rows without
  active-DB / production-equivalent comparison.

The already-proven shape is:

```text
smooth, CALL-side, maturity/climax guard;
phase-aware weekly inputs;
small score deltas;
late in score stack, after Daily Volume Authority Wave;
auditable in weight_info;
validated against v60 persisted rows and v61 research pack.
```

### Future Weekly Alpha Hypotheses

These are the best next research directions.

1. N-preserving maturity guard retune.

   v61 proved the DD alpha, but it paid with heavy CALL 75+ N compression. Search
   for a softer target or risk curve that keeps most of the 75-79 WR15 lift while
   preserving more 75+ N. This should be judged against active v61, not v60.

2. Fresh weekly momentum admission lift.

   The `w_mom` 5.0-7.9 CALL bucket had the strongest positive weekly signal in
   the discovery panel. Mine 70-74 CALL rows where fresh weekly momentum is high
   but the setup is not extended by `wk_ret4` / `w_comp`. This is a separate
   promoter from v61's guard and must preserve 75+ and 80+ bucket quality.

3. Moderate weekly composite / bias phase lift.

   Moderate `w_comp` and `w_bias` bins were positive, while high bins were
   negative. Fit an inverted-U phase controller rather than a monotonic boost.
   The controller should prefer mid-phase weekly strength and fade late-phase
   extension.

4. Guard orthogonality ledger.

   Build a row-level ledger of v61 guard events versus SCW, CWWD, WVD, DVAW,
   ICH, and MCD metadata. Goal: determine whether any overlap can be narrowed
   to reduce N loss without giving back DD protection.

5. Put-side weekly volume phase.

   Some put-side bins had positive signal (`wv_force1`, `w_comp`, and stoch
   interactions), but no put-side weekly candidate shipped in this pass. Keep it
   as a separate Stage 1 search. Do not let a put-side score change ride along
   with v61-style CALL maturity work.

6. Weekly ret4 saturation curve.

   `wk_ret4` was constructive in moderate ranges and harmful when extended.
   This is a clean candidate for a smooth bell / saturation wave. It should be
   fitted as a phase term, not a threshold.

### Recommended Protocol For The Next Weekly Agent

Start from active v61, not v60.

1. Rebuild the weekly event ledger from active v61 score rows.
2. Split v61 guard events into:
   - DD-saving true positives.
   - harmless N removals.
   - lost winners / over-suppressed rows.
3. Compare those rows to `weekly_mature_call_guard`, `daily_volume_authority_wave`,
   `scw_dampen`, `cwcf_dampen`, `cwwd_dampen`, `wvd_*`, `ich_*`, and `mcd_*`
   metadata.
4. Search N-preserving v61 retunes first. Gate on:
   - 75+ WR15 preserved or improved.
   - 75+ N materially recovered versus v61.
   - 80+/85+/90+/95+ not degraded.
   - 2020 crash, 2020-2021, 2020-now, and 22-now DD not worse than v61.
5. Only then search additive weekly promoters such as fresh weekly momentum
   admission. Keep promoters separate from maturity dampeners.
6. Any candidate that changes `Score.overall` needs a new algorithm version.
   Do not stealth-update scoring logic.

Stop rules:

- If 2020-crash DD regresses materially versus v61, stop.
- If 22-now DD regresses versus v61 without an explicit user-approved return
  tradeoff, stop.
- If CALL 80+ WR15 drops more than 1pp, stop.
- If 85+/90+/95+ churn is unexplained, stop.
- If the candidate only wins by throwing away 75+ N, classify it as DD
  compression, not scoring alpha expansion.
- If the candidate only improves WR on selected rows but damages active bucket
  utility, no-ship.

### Artifact Index

Discovery / search:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_20260519_210639
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_refine_gate_20260519_230000
```

Staging-native gates:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_staging_gate_20260520_115721
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_staging_gate_fast_20260520_124247
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_staging_gate_fast2_20260520_125325
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\weekly_alpha_v60_staging_gate_final_20260520_141009
```

Recalc / repair / full coverage:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\v61_recalc_market_20260520_151812
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\v61_recalc_symbolcopy_repair_20260520_160900
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\v61_recalc_10y_extend_20260520_190701
```

Research packs:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.cache\algorithm_versions\v61\research_pack\manifest.json
C:\Development\Trader_weekly_alpha_v60_20260520\.cache\algorithm_versions\v60\research_pack\manifest.json
```

Algorithm silo:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\algorithm_versions\v61\README.md
C:\Development\Trader_weekly_alpha_v60_20260520\algorithm_versions\v61\manifest.json
C:\Development\Trader_weekly_alpha_v60_20260520\algorithm_versions\v61\diff_from_previous.json
C:\Development\Trader_weekly_alpha_v60_20260520\algorithm_versions\v61\scoring_config.json
```

Docs updated in the v61 branch:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.claude\docs\version-history.md
C:\Development\Trader_weekly_alpha_v60_20260520\.claude\docs\scoring-algorithm.md
C:\Development\Trader_weekly_alpha_v60_20260520\.claude\docs\known-issues.md
```

GitNexus:

```text
C:\Development\Trader_weekly_alpha_v60_20260520\.codex\runs\gitnexus_analyze_20260520_224232\run.log
```

GitNexus analyze succeeded after push and indexed commit
`6910f76b0f358857333cfe63770826132cf3c952` with `51,816` symbols,
`137,583` relationships, and `300` flows. Analyzer-only doc/skill churn was
left uncommitted per repo policy.

## Historical v59 Recovery / Markov Cohort Findings

Report added: 2026-05-21

This entry is a historical v59 carry-forward, not a v60 or v61 validation
result. It belongs in this file because the findings are directly relevant to
future daily opportunity allocation, stop/hold, dead-hold, and recovery-odds
work. Use it as a candidate source for future scoring research or portfolio
profile iteration, not as a shipped rule.

### Executive Read

The original question was whether an adverse early option move should be held,
cut, or converted into a state-dependent decision. Example: a high CALL signal
is down about 25% after 3 trading days. What is the historical likelihood that
it still wins, and can Markov-style state transitions use prior fulfillment and
current score to forecast recovery?

The short answer from the final v59 live-open cohort build:

- CALLs down around 20-30% by day 3 still recover often enough that a blind
  hard cut is not obviously superior.
- PUTs down around 20-25% by day 3 are materially worse and look closer to a
  portfolio-stage cut / throttle candidate.
- The exact 25-30% PUT buckets are thin and should not be used as standalone
  policy evidence.
- Deeper adverse buckets are dominated by dead-hold states. Do not interpret
  those as normal recovery odds.
- Current score and prior fulfillment are useful state dimensions, but neither
  is strong enough as a standalone rule in the first pass.
- The right next implementation shape is a smoothed/backoff continuation model:
  full state -> no-prior state -> core state -> side/DTE/day/PnL base rate.

This is Stage 3 / portfolio-path research unless a future agent explicitly
changes `Score.overall`. A continuation/cut probability, allocation throttle,
or dead-hold-aware exit policy should not bump `ALGORITHM_VERSION` by itself.

### Source Baseline And Artifacts

Baseline:

```text
AlgorithmVersion active/current at run time: v59
DB version id: 59
Commit: 4fd7ffa9
```

Successful run:

```text
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543
```

Outputs:

```text
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\state_visits.csv
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\state_summary.csv
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\transitions.csv
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\summary.json
```

Dependency-gated inputs:

```text
C:\Development\Trader\.codex\runs\market_data_backfill_2016_afterhours_20260515_160557
C:\Development\Trader\.codex\runs\v59_market_data_gap_recalc_20260515_224640
```

Important execution note:

- The first naive recovery pass was rejected because it included states that
  were counterfactual after production would already have exited.
- The final artifacts use live-open states only: `outcome == open` or
  `outcome.hold_bars > snapshot_day`.
- The final job waited for both the market-data backfill and the v59 gap recalc
  to reach done before reading scores.
- The broad `scores` read had to be replaced with chunked reads through
  `scores_version_symbol_date_IDX`; future agents should keep this pattern when
  building state maps from live score rows.

Run scale:

```text
30DTE signals: call=8,515 put=12,607 symbols=760
15DTE signals: call=8,520 put=12,607 symbols=760
state rows: 39,905
summary rows: 9,192
transition rows: 16,769
unique live-open trades represented in state rows: 18,286
```

### State Model

Each row in `state_visits.csv` is one live-open trade snapshot at a trading-day
offset after signal entry. The state key used for the Markov/cohort summaries
tracks:

```text
DTE
side
day bucket
current option PnL bucket
entry score bucket
open phase: normal vs dead_hold
current score bucket
entry-to-current score delta bucket
prior same-symbol fulfillment: none / tp / sl / hard / dead_hold / open
```

The transition table stores next-state transitions plus absorbing outcomes:

```text
S:<next live state>
A:tp
A:sl
A:hard
A:dead_hold
A:open
```

The summary table stores, per state:

```text
n
tp_rate_pct
positive_rate_pct
dead_hold_rate_pct
avg_current_pnl_pct
avg_final_pnl_pct
ev_continue_minus_cut_pp
```

`ev_continue_minus_cut_pp` is simply final average option PnL minus current
marked option PnL. Positive values mean "continuing improved the mark on
average"; negative values mean "cutting at the snapshot mark was better on
average." It is not yet portfolio-equivalent MC.

### Mechanics Verified Before Interpreting Results

The user explicitly raised two confounders: re-entry and dead-hold.

The relevant v59 mechanics were:

- Same-symbol re-entry is blocked while a position is open in `run_backtest()`.
- Dead-hold keeps the slot occupied after a deep stop-loss event.
- Therefore low stop-loss behavior should not be interpreted as simple
  immediate same-symbol churn in this artifact.
- However, dead-hold materially affects adverse buckets. Many deep adverse
  states are already in `open_phase=dead_hold`, so their final averages are not
  "normal recovery from a live thesis"; they are the dead-hold mechanic playing
  out.

State rows by DTE / side / final family:

| DTE | Side | TP | SL | Hard | Dead-hold | Open |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | 5,634 | 2,743 | 693 | 2,172 | 12 |
| 30DTE | put | 7,082 | 5,898 | 1,170 | 1,776 | 5 |
| 15DTE | call | 2,872 | 1,125 | 547 | 1,262 | 2 |
| 15DTE | put | 2,878 | 1,910 | 870 | 1,251 | 3 |

Unique trades represented:

| DTE | Side | TP | SL | Hard | Dead-hold | Open |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | 2,774 | 1,278 | 74 | 338 | 8 |
| 30DTE | put | 3,453 | 2,647 | 124 | 278 | 3 |
| 15DTE | call | 1,939 | 773 | 147 | 423 | 2 |
| 15DTE | put | 2,004 | 1,335 | 239 | 445 | 2 |

### Day-3 Adverse PnL Read

Detailed day-3 live-open rows by option PnL bucket:

| DTE | Side | Day 3 PnL bucket | N | TP rate | Positive final | Dead-hold rate | Continue vs cut |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | -10 to -20 | 174 | 35.1% | 35.1% | 5.7% | +0.4pp |
| 30DTE | call | -20 to -25 | 66 | 22.7% | 24.2% | 12.1% | +1.9pp |
| 30DTE | call | -25 to -30 | 32 | 15.6% | 15.6% | 12.5% | -0.1pp |
| 30DTE | call | -30 to -40 | 30 | 13.3% | 13.3% | 43.3% | +8.4pp |
| 30DTE | call | -40 to -50 | 24 | 4.2% | 4.2% | 87.5% | +7.0pp |
| 30DTE | call | <= -50 | 162 | 3.7% | 0.6% | 94.4% | +8.7pp |
| 15DTE | call | -10 to -20 | 85 | 28.2% | 27.1% | 1.2% | -5.2pp |
| 15DTE | call | -20 to -25 | 39 | 20.5% | 17.9% | 12.8% | -1.4pp |
| 15DTE | call | -25 to -30 | 33 | 18.2% | 12.1% | 24.2% | -0.5pp |
| 15DTE | call | -30 to -40 | 34 | 8.8% | 5.9% | 55.9% | -4.9pp |
| 15DTE | call | -40 to -50 | 17 | 5.9% | 0.0% | 88.2% | -4.0pp |
| 15DTE | call | <= -50 | 214 | 1.4% | 0.5% | 96.7% | +6.2pp |
| 30DTE | put | -10 to -20 | 267 | 21.3% | 22.1% | 3.0% | -3.2pp |
| 30DTE | put | -20 to -25 | 32 | 6.2% | 6.2% | 6.2% | -7.7pp |
| 30DTE | put | -25 to -30 | 3 | 0.0% | 0.0% | 66.7% | +2.5pp |
| 30DTE | put | -30 to -40 | 8 | 0.0% | 0.0% | 100.0% | +9.1pp |
| 30DTE | put | -40 to -50 | 8 | 0.0% | 0.0% | 100.0% | +10.2pp |
| 30DTE | put | <= -50 | 131 | 0.0% | 0.0% | 100.0% | +14.3pp |
| 15DTE | put | -10 to -20 | 181 | 17.7% | 16.6% | 8.8% | -5.6pp |
| 15DTE | put | -20 to -25 | 36 | 11.1% | 13.9% | 13.9% | -4.0pp |
| 15DTE | put | -25 to -30 | 14 | 14.3% | 14.3% | 42.9% | +2.5pp |
| 15DTE | put | -30 to -40 | 9 | 0.0% | 0.0% | 100.0% | -8.2pp |
| 15DTE | put | -40 to -50 | 19 | 0.0% | 5.3% | 100.0% | +3.4pp |
| 15DTE | put | <= -50 | 202 | 0.0% | 0.5% | 99.5% | +2.0pp |

Interpretation:

- A 30DTE CALL down 20-25% on day 3 still had about 23-24% recovery/positive
  final odds and a slightly positive continue-vs-cut mark.
- A 30DTE CALL down 25-30% on day 3 had only about 16% TP odds and was roughly
  neutral on continue-vs-cut.
- 15DTE CALLs in the same bands had similar TP odds but worse average
  continue-vs-cut behavior.
- PUTs were weaker. 30DTE PUTs down 20-25% on day 3 had only 6.2% TP / positive
  final odds and -7.7pp continue-vs-cut.
- Do not treat the deep adverse PUT rows as proof that holding is good. They
  are tiny or dead-hold dominated.

### First-Five-Day Shape Around -20% To -30%

Aggregating only the -20 to -25 and -25 to -30 buckets:

| DTE | Side | Day | N | TP rate | Positive final | Dead-hold rate | Continue vs cut |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | 1 | 180 | 33.9% | 31.1% | 12.2% | +10.9pp |
| 30DTE | call | 2 | 148 | 28.4% | 26.4% | 14.9% | +5.5pp |
| 30DTE | call | 3 | 98 | 20.4% | 21.4% | 12.2% | +1.2pp |
| 30DTE | call | 4 | 78 | 17.9% | 16.7% | 14.1% | -2.5pp |
| 30DTE | call | 5 | 50 | 20.0% | 20.0% | 22.0% | -0.5pp |
| 15DTE | call | 1 | 234 | 36.8% | 32.1% | 13.2% | +9.7pp |
| 15DTE | call | 2 | 113 | 32.7% | 24.8% | 24.8% | -0.3pp |
| 15DTE | call | 3 | 72 | 19.4% | 15.3% | 18.1% | -1.0pp |
| 15DTE | call | 4 | 41 | 2.4% | 2.4% | 2.4% | -3.5pp |
| 30DTE | put | 1 | 24 | 16.7% | 16.7% | 20.8% | +7.0pp |
| 30DTE | put | 2 | 33 | 15.2% | 15.2% | 9.1% | +2.4pp |
| 30DTE | put | 3 | 35 | 5.7% | 5.7% | 11.4% | -6.8pp |
| 30DTE | put | 4 | 37 | 8.1% | 8.1% | 10.8% | -7.1pp |
| 30DTE | put | 5 | 36 | 8.3% | 8.3% | 11.1% | -8.8pp |
| 15DTE | put | 1 | 48 | 6.2% | 10.4% | 60.4% | +1.9pp |
| 15DTE | put | 2 | 56 | 17.9% | 16.1% | 10.7% | -0.7pp |
| 15DTE | put | 3 | 50 | 12.0% | 14.0% | 22.0% | -2.2pp |
| 15DTE | put | 4 | 27 | 3.7% | 11.1% | 25.9% | -4.4pp |

Read:

- The call side has a time-decay pattern: holding adverse 20-30% marks is much
  more defensible on day 1 than on day 3-4.
- Day 3 is the inflection zone for calls: odds are no longer strong, but still
  nonzero enough that a hard universal stop could create death-by-a-thousand
  cuts.
- PUTs deteriorate faster. By day 3, both 30DTE and 15DTE PUTs in the 20-30%
  adverse band have negative continue-vs-cut averages.
- This supports a side/DTE/day-specific policy search, not a single stop-loss.

### Current Score And Prior Fulfillment Slices

Day 3, adverse 20-30% rows by score delta bucket, keeping only N >= 8:

| DTE | Side | Score delta | N | TP rate | Positive final | Avg final | Continue vs cut |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | worse 10+ | 66 | 19.7% | 21.2% | -22.6% | +1.7pp |
| 30DTE | call | worse 3-9 | 26 | 19.2% | 19.2% | -25.7% | -2.4pp |
| 15DTE | call | worse 10+ | 41 | 7.3% | 7.3% | -33.2% | -8.1pp |
| 15DTE | call | worse 3-9 | 24 | 33.3% | 20.8% | -23.6% | +1.1pp |
| 30DTE | put | flat | 10 | 0.0% | 0.0% | -36.0% | -14.2pp |
| 30DTE | put | worse 10+ | 9 | 0.0% | 0.0% | -29.0% | -6.7pp |
| 30DTE | put | worse 3-9 | 14 | 14.3% | 14.3% | -24.1% | -2.0pp |
| 15DTE | put | flat | 8 | 37.5% | 37.5% | -15.0% | +10.1pp |
| 15DTE | put | worse 10+ | 26 | 3.8% | 11.5% | -23.8% | +0.1pp |
| 15DTE | put | worse 3-9 | 13 | 7.7% | 0.0% | -39.1% | -16.3pp |

Day 3, adverse 20-30% rows by prior same-symbol fulfillment, keeping only
N >= 8:

| DTE | Side | Prior fulfillment | N | TP rate | Positive final | Avg final | Continue vs cut |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 30DTE | call | prior SL | 35 | 22.9% | 20.0% | -23.2% | +0.3pp |
| 30DTE | call | prior TP | 56 | 19.6% | 23.2% | -22.2% | +2.1pp |
| 15DTE | call | prior SL | 17 | 0.0% | 5.9% | -29.2% | -5.1pp |
| 15DTE | call | prior TP | 45 | 26.7% | 17.8% | -24.3% | +0.9pp |
| 30DTE | put | prior SL | 16 | 6.2% | 6.2% | -26.6% | -4.4pp |
| 30DTE | put | prior TP | 17 | 5.9% | 5.9% | -30.9% | -8.8pp |
| 15DTE | put | prior SL | 21 | 4.8% | 4.8% | -31.1% | -7.4pp |
| 15DTE | put | prior TP | 18 | 22.2% | 22.2% | -26.8% | -2.8pp |

Read:

- Current score deterioration is directionally useful in places but not robust
  enough as a hard rule.
- Prior fulfillment is not a standalone edge. It may help as a Markov/backoff
  feature, but the N is too small to let prior TP/SL drive policy directly.
- The most promising side-specific observation is still the broad shape:
  adverse CALLs decay from "maybe hold" to "review/cut" over days 1-4; adverse
  PUTs become poor continuation candidates by day 3.

### Candidate Implications

Candidate 1: portfolio-stage adverse continuation model.

Use `state_summary.csv` and `transitions.csv` to build deterministic
continuation probabilities for open positions:

```text
state_full = dte | side | day_bucket | pnl_bucket | entry_score_bucket |
             open_phase | current_score_bucket | score_delta_bucket |
             prior_fulfillment

backoff order:
  1. full state if N >= threshold
  2. no_prior state
  3. core state
  4. side + DTE + day + PnL bucket base rate
```

Suggested outputs per open position:

```text
P_TP
P_positive_final
EV_continue_minus_cut
dead_hold_probability
state_n
confidence / shrinkage weight
```

Candidate 2: side/DTE/day-specific soft cut or allocation throttle.

Initial policy shape to test, not ship:

```text
IF side=put AND day>=3 AND current_option_pnl in [-30%, -20%]:
    consider exit / scale-down / no-add state

IF side=call AND day<=2 AND current_option_pnl in [-30%, -20%]:
    avoid hard forced cut unless other state features are poor

IF side=call AND day>=4 AND current_option_pnl in [-30%, -20%]:
    move from hold-default to review/cut candidate
```

This should be a smooth probability/EV controller, not a cliff. Use a
continuation score such as:

```text
continue_score =
    shrunk_EV_continue_minus_cut
    + lambda_tp * shrunk(P_TP - side_dte_day_base_P_TP)
    - lambda_dead_hold * P_dead_hold
```

Candidate 3: dead-hold-aware exit review.

Deep adverse buckets have high dead-hold rates. A future policy should not ask
"did this recover?" in isolation; it should ask whether dead-hold is tying up a
slot with poor marginal EV versus replacement opportunities. This belongs in
portfolio MC, because cutting changes cash recycling and replacement path.

Candidate 4: re-entry / replacement-path audit.

Same-symbol re-entry is blocked while a position is open, but cutting losses
still frees capital for other symbols. Any lower stop-loss policy must be
validated with portfolio MC, not only state rows, because the real risk is
death-by-a-thousand-cuts through replacement churn.

### No-Ship / Caution Rules From This Pass

Do not ship a universal 25% stop from this evidence.

Reasons:

- CALLs down 20-30% early still have meaningful recovery odds.
- The day-3 CALL result is mixed, not decisively negative.
- PUT evidence is more negative but thin in exact 25-30% buckets.
- Deeper buckets are dead-hold dominated and can invert simple EV readings.
- The artifacts are cohort/transition evidence, not production-equivalent MC.

Do not treat current score or prior fulfillment as standalone policy.

Reasons:

- Both dimensions are useful state features.
- Neither is robust enough in the day-3 adverse cohorts to become a hard rule.
- They should enter the smoothed Markov/backoff model with shrinkage.

Do not evaluate a new lower SL without portfolio replacement-path validation.

Reasons:

- Cohort EV and portfolio EV can diverge when freed buying power is recycled.
- The v60 smooth put-wave reserve work elsewhere in this file already showed
  that cash recycling can turn a seemingly good exposure throttle into return
  drag unless reserve mechanics are explicit.

### Recommended Next Agent Protocol

1. Load the final v59 artifacts listed above.
2. Build a small reusable probability table from `state_summary.csv` with
   min-N backoff and shrinkage.
3. Produce a dashboard-style probe over current open positions:
   `P_TP`, `P_positive`, `EV_continue_minus_cut`, `P_dead_hold`, and state N.
4. Validate candidate exit/throttle policies in deterministic replay first.
5. Promote only the best candidates into production-equivalent portfolio MC.
6. Gate on drawdown and return, not just per-trade win rate.
7. Keep CALL and PUT policies separate at first.
8. Keep 15DTE and 30DTE policies separate at first.
9. Treat exact buckets with N < 30 as qualitative only unless pooled through
   the backoff model.
10. If the policy only improves apparent trade-level EV by cutting often but
    harms portfolio DD or log return through churn, classify it as no-ship.

### Artifact Index

Primary recovery/Markov run:

```text
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\summary.json
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\state_visits.csv
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\state_summary.csv
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\transitions.csv
```

Dependency runs:

```text
C:\Development\Trader\.codex\runs\market_data_backfill_2016_afterhours_20260515_160557\backfill\done.json
C:\Development\Trader\.codex\runs\v59_market_data_gap_recalc_20260515_224640\summary.json
```

Run-local scripts:

```text
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\build_markov_recovery.py
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\wait_then_run.py
C:\Development\Trader\.codex\runs\v59_markov_recovery_after_backfill_20260515_185543\monitor_recent.py
```

These scripts are artifacts, not production code. If the next agent wants this
as a maintained research tool, move the logic into
`experiments/daily_opportunity_allocation/` and preserve the dependency-gated,
chunked-read behavior.

## Practical Exposure Controller Findings

Report added: 2026-05-21

This entry records the practical exposure-controller work that followed the
daily-opportunity allocation research. It is Stage 3 portfolio-only work. It
does not change `Score.overall`, does not write score rows, does not require an
`ALGORITHM_VERSION` bump, and did not run `trader recalculate`.

### Executive Read

The user's working hypothesis was correct: much of the drawdown problem is not
the utility ranking itself, but the portfolio's allocation behavior when high-N
opportunity days cause cash to be broadly dispensed. More opportunities mean
more positions and more open premium. Because the WR/N utility ranking is
separate from portfolio simulation, the right control is a ubiquitous Stage 3
exposure behavior, not a scoring rewrite.

The confirmed shape is:

- cap deployable exposure relative to a practical capital base;
- preserve enough call exposure to keep practical compounding alive;
- cut put-side overfill harder than call-side exposure;
- scale allocation down when same-day opportunity supply is dense;
- avoid drawdown-reactive behavior in this controller, so it remains a
  theoretical allocation behavior rather than a hindsight DD breaker.

The ship-shaped candidate from the final confirmation is:

```text
g80_c65_p25_ref16_4_pow05_floor55_25m
```

Parameterization:

```text
PRACTICAL_CAPITAL_CEILING = 25_000_000
GROSS_PREMIUM_CAP = 0.80
CALL_PREMIUM_CAP = 0.65
PUT_PREMIUM_CAP = 0.25
OPP_SAT_CALL_REF = 16
OPP_SAT_PUT_REF = 4
OPP_SAT_POWER = 0.50
OPP_SAT_FLOOR = 0.55
MAX_POSITIONS_OVERRIDE = 14
MAX_POSITIONS_CALL = 12
MAX_POSITIONS_PUT = 8
```

The practical interpretation is simple: once the account is above the practical
base, size as if only the first $25M is deployable; keep gross open premium near
80% of that base, let calls use up to 65%, and let puts use only 25%. When
same-day signal supply is dense, allocation is smoothly scaled by side-specific
opportunity pressure, with calls allowed more breadth than puts.

### Why This Is Not A Score Finding

Do not reinterpret this as proof that the score surface improved.

The controller improves DD by reducing exposure density:

- baseline cross-version average open premium/base was about `85.4%`;
- confirmed `g80_c65` average open premium/base was `62.6%`;
- baseline call/base was `54.0%`;
- confirmed `g80_c65` call/base was `44.2%`;
- baseline put/base was `31.5%`;
- confirmed `g80_c65` put/base was `18.4%`.

The key change is the put-side reduction. The winning controller is not merely
"cap everything"; it keeps more call exposure than the earlier `g75` candidate
while materially reducing put overfill.

### Mechanism Shape Used In MC

The MC research added default-inert, env-gated controls to `monte_carlo.py`:

```text
PRACTICAL_CAPITAL_CEILING
GROSS_PREMIUM_CAP
CALL_PREMIUM_CAP
PUT_PREMIUM_CAP
OPP_SAT_CALL_REF
OPP_SAT_PUT_REF
OPP_SAT_POWER
OPP_SAT_FLOOR
MAX_POSITIONS_OVERRIDE
```

The controller computes a practical allocation base:

```text
allocation_base = min(portfolio_value, PRACTICAL_CAPITAL_CEILING)
```

when the ceiling is enabled. It then applies gross and side-specific premium
caps against that base. Opportunity saturation is side-specific:

```text
if pressure <= ref:
    saturation_scale = 1.0
else:
    saturation_scale = max(floor, (ref / pressure) ** power)
```

where `pressure` is the count of same-day eligible signals on the relevant
side, excluding symbols already open. This is a smooth opportunity-density
controller, not a hard threshold and not a drawdown band.

The runs also logged exposure attribution metrics:

```text
avg_open_premium_base_pct
avg_call_open_premium_base_pct
avg_put_open_premium_base_pct
avg_saturation_scale
```

Those metrics are essential. Future agents should not evaluate this controller
only by final wealth or DD; they should confirm the DD reduction came from the
intended exposure behavior.

### Search And Validation Lineage

Initial broad sweep artifact:

```text
C:\Development\Trader\.codex\runs\practical_exposure_sweep_20260519_220000
```

That sweep found the first strong candidate:

```text
g75_c55_p40_ref12_6_pow05_floor50_25m
```

Early cross-version phase-2 read across v42/v54/v58/v60 showed:

```text
avg worst-DD improve: +10.67pp
avg mean-DD improve:  +13.57pp
max DD worse:         +1.85pp
practical log delta:  +0.054
floor pass:           65%
avg open prem/base:   63.7%
avg saturation:       0.79
```

At that point it was not ship-shaped because earlier low-N evidence showed a
v60 `2025_dip` DD regression and weak practical-floor behavior on some short
windows. It was useful as a mechanism discovery, not as the final candidate.

Focused validation artifact:

```text
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450
```

This run separated two ideas:

1. **Active-v60 optimum**:

   ```text
   g75_c62_p25_ref16_4_pow075_floor55_25m
   ```

   v60 N=500 result:

   ```text
   avg worst-DD improve: +9.03pp
   avg mean-DD improve:  +11.34pp
   max DD worse:         +0.00pp
   v60 2025_dip worse:   +0.00pp
   floor pass:           100%
   ```

   But cross-version N=250 rejected it as a ubiquitous controller:

   ```text
   max DD worse: +4.31pp
   main regression: v58 2022
   floor pass: 70%
   ```

2. **Cross-version compromise**:

   ```text
   g80_c65_p25_ref16_4_pow05_floor55_25m
   ```

   Cross-version N=250 result:

   ```text
   avg worst-DD improve: +8.86pp
   avg mean-DD improve:  +9.82pp
   max DD worse:         +0.31pp
   v60 2025_dip worse:   +0.00pp
   practical log delta:  +0.107
   floor pass:           75%
   avg open prem/base:   62.6%
   call/base:            44.2%
   put/base:             18.4%
   avg saturation:       0.81
   ```

Final confirmation artifact:

```text
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338
```

This was the confirm-only N=1000 run across:

```text
versions = v42, v54, v58, v60
windows = 2022, 2024, 2025_dip, 2022_now, 2020_now
candidates = baseline, original g75, g80_c65
```

Final N=1000 ranking:

| Candidate | Avg Worst-DD Improve | Avg Mean-DD Improve | Max DD Worse | Floor Pass | Avg Open Prem/Base | Call/Base | Put/Base |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `g80_c65_p25_ref16_4_pow05_floor55_25m` | +10.49pp | +9.86pp | +0.00pp | 75% | 62.6% | 44.2% | 18.4% |
| `g75_c55_p40_ref12_6_pow05_floor50_25m` | +8.60pp | +13.54pp | +1.81pp | 60% | 63.7% | 39.0% | 24.7% |
| `baseline` | +0.00pp | +0.00pp | +0.00pp | 80% | 85.4% | 54.0% | 31.5% |

Final conclusion from N=1000:

```text
g80_c65_p25_ref16_4_pow05_floor55_25m
```

is the ship-shaped Stage 3 candidate. It had no cross-version DD regression in
the confirm run and beat original `g75` on both average worst DD and practical
floor behavior.

### Active v60 2025_Dip Detail

The active-v60 2025 dip window matters because it was the original concern for
`g75`. The N=1000 confirm result:

| Candidate | Worst DD | Mean DD | Mean Final | Open Prem/Base | Call/Base | Put/Base |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 56.70% | 31.22% | $6.35M | 79.9% | 63.5% | 16.4% |
| `g75_c55_p40_ref12_6_pow05_floor50_25m` | 52.52% | 24.32% | $1.96M | 62.4% | 43.6% | 18.9% |
| `g80_c65_p25_ref16_4_pow05_floor55_25m` | 48.09% | 27.83% | $2.16M | 65.5% | 50.4% | 15.0% |

Read:

- `g75` improved DD but failed the practical $2M floor by a small amount.
- `g80_c65` improved worst DD more than `g75`, stayed above the $2M floor, and
  cut put exposure while preserving more call exposure.

### Why G80 Beats G75

The original `g75` was a bluntly useful exposure reducer, but it was not the
best practical controller:

- it cut calls too deeply (`39.0%` call/base in the N=1000 confirm);
- it left too much put exposure (`24.7%` put/base);
- it repeated a v58/2022 DD regression in the N=1000 confirm:

  ```text
  v58 2022 baseline worst DD: 61.4%
  v58 2022 g75 worst DD:      63.2%
  regression:                 +1.81pp
  ```

`g80_c65` fixed the shape:

- more call capacity (`44.2%` call/base);
- less put capacity (`18.4%` put/base);
- no DD regression across the 20 version/window cells in the N=1000 confirm;
- better average worst-DD improvement;
- better practical-floor pass rate.

The relevant lesson is not "higher gross cap is safer." It is "higher gross cap
can be safer when side caps and opportunity saturation shift exposure away from
overfilled puts while preserving enough calls."

### Ship Read

`g80_c65_p25_ref16_4_pow05_floor55_25m` is promotion-worthy as a Stage 3
portfolio candidate, but it is not yet shipped.

Promotion requirements:

1. Implement the controller in the production-equivalent allocation surfaces,
   not only as run-local env vars.
2. Keep it config-gated and reversible.
3. Preserve `Score.overall` and do not bump `ALGORITHM_VERSION` unless a
   separate scoring change is made.
4. Validate parity across Monte Carlo, deterministic backtest, and any live
   allocation display path.
5. Include exposure attribution in the validation artifacts:
   gross/base, call/base, put/base, saturation scale, and position counts.
6. Run the normal portfolio ship sentinels if production config or registry
   files change:

   ```text
   python tests/test_strategy_config_drift.py
   python tests/test_mechanism_registry.py
   ```

7. If the mechanism is added to `mechanism_registry.py` or DTE-paired config
   surfaces, also run the DTE audit:

   ```text
   python experiments/_dte_audit/audit.py
   ```

Likely implementation surfaces to inspect first:

```text
strategy_config.py
portfolio_allocation.py
monte_carlo.py
backtest_cascade.py
trader.py alloc path
mechanism_registry.py
```

Do not ship by copying the run-local script. The run script is evidence. The
ship needs a small, named Stage 3 mechanism with explicit config and parity
tests.

### No-Ship / Caution Rules From This Pass

Do not ship the active-v60 optimum `g75_c62_p25_ref16_4_pow075_floor55_25m`
as a ubiquitous controller. It was best on v60 N=500 but failed the broader
cross-version test with a +4.31pp max DD regression.

Do not ship original `g75_c55_p40_ref12_6_pow05_floor50_25m`. It was a strong
discovery candidate but repeated a v58/2022 DD regression and had weaker floor
behavior than `g80_c65`.

Do not claim this validates the utility score itself. Utility remains WR/N
ranking. The DD improvement is from portfolio-stage exposure control.

Do not convert this into a hard cliff. The winning form is already smooth:
opportunity pressure scales allocation gradually with a floor.

Do not use DD state as the trigger for this controller. v60 already has a
separate DD soft-band call contraction. This controller is about opportunity
density and practical capital deployment.

### Recommended Next Agent Protocol

1. Read this entry and the two final artifacts first:

   ```text
   C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\findings.md
   C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\findings.md
   ```

2. Confirm whether the env-gated `monte_carlo.py` research knobs are still
   present in the checkout. They were default-inert at research time.
3. Design a named Stage 3 mechanism for practical exposure saturation.
4. Add config with defaults matching current shipped behavior until explicitly
   enabled.
5. Wire the same math into MC, deterministic backtest, and live allocation
   explanation surfaces.
6. Re-run baseline vs `g80_c65` in production-equivalent MC after wiring, not
   only in the env-gated research runner.
7. Treat the N=1000 confirm as the evidence target to reproduce:

   ```text
   avg worst-DD improve around +10pp
   no cross-version DD regression
   v60 2025_dip worst DD materially lower
   avg open premium/base around 62-63%
   put/base around 18%
   ```

8. If reproduction materially deviates, stop and debug parity before changing
   docs or claiming a ship.

### Artifact Index

Broad discovery:

```text
C:\Development\Trader\.codex\runs\practical_exposure_sweep_20260519_220000\findings.md
C:\Development\Trader\.codex\runs\practical_exposure_sweep_20260519_220000\phase2_summary.csv
C:\Development\Trader\.codex\runs\practical_exposure_sweep_20260519_220000\phase2_results.csv
```

Focused validation:

```text
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\findings.md
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\phase1_summary.csv
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\phase2_summary.csv
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\phase3_summary.csv
```

Final confirmation:

```text
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\findings.md
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\confirm_summary.csv
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\confirm_results.csv
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\done.json
```

Run-local scripts:

```text
C:\Development\Trader\.codex\runs\g75_focused_validation_20260520_120450\run_g75_focused_validation.py
C:\Development\Trader\.codex\runs\g80_cross_version_confirm_20260520_220338\run_g80_cross_version_confirm.py
```

These scripts are artifacts, not production code. If this becomes a real
portfolio ship, move only the mechanism math into maintained allocation modules
and leave the sweep scripts as evidence.

### 2026-05-21 Update: Sentinel Practical Exposure Candidate Wired And Revalidated

The promotion requirements above have now been executed in the working tree for
the clean `g80_c65_p25_ref16_4_pow05_floor55_25m` candidate.

Implemented surfaces:

```text
strategy_config.py
portfolio_allocation.py
monte_carlo.py
backtest_cascade.py
trader.py
mechanism_registry.py
tests/test_strategy_config_drift.py
experiments/daily_opportunity_allocation/ legacy combined-validation runner
```

Production candidate parameters:

```text
PRACTICAL_CAPITAL_CEILING = 25_000_000
GROSS_PREMIUM_CAP = 0.80
CALL_PREMIUM_CAP = 0.65
PUT_PREMIUM_CAP = 0.25
OPP_SAT_CALL_REF = 16
OPP_SAT_PUT_REF = 4
OPP_SAT_POWER = 0.50
OPP_SAT_FLOOR = 0.55
MAX_POSITIONS = 14
MAX_POSITIONS_CALL = 12
MAX_POSITIONS_PUT = 8
```

The shipped Sentinel practical exposure profile remains Stage 3 portfolio-only:

- active scoring remains v60, `ALGORITHM_VERSION=d4a3e9fec`;
- `Score.overall` is unchanged;
- no score rows were written;
- no recalc was run;
- v61 remains historical/reverted and is not the active baseline.

Post-wire validation artifact:

```text
C:\Development\Trader\.codex\runs\sentinel_g80_postwire_20260521_044902\findings.md
```

Post-wire v60 N=160 result across 2020_crash, covid_peak, 2020_full, 2022,
2024, 2025, 2025_dip, 2022_now, and 5y:

| Metric | Result |
|---|---:|
| Average worst-DD improvement | +10.33pp |
| Average mean-DD improvement | +11.51pp |
| Max worst-DD regression | +0.00pp |
| 2020_crash DD improvement | +13.32pp |
| covid_peak DD improvement | +17.59pp |
| 2025_dip DD regression | +0.00pp |
| Avg open premium/base | 59.2% |
| Avg call/base | 42.4% |
| Avg put/base | 16.8% |
| Avg saturation scale | 0.849 |
| $2M practical floor pass | 7/9 windows |

Key practical-wealth read from $50k:

| Window | v60 mean final | Sentinel mean final | DD read |
|---|---:|---:|---|
| 2020_crash | $238,671 | $129,119 | DD 72.6% -> 59.3% |
| covid_peak | $164,773 | $102,230 | DD 74.5% -> 56.9% |
| 2020_full | $154,977,905 | $12,473,485 | DD 77.4% -> 60.3% |
| 2022 | $67,901,622 | $6,926,185 | DD 60.8% -> 57.4% |
| 2024 | $61,301,305,867 | $135,021,362 | DD 55.4% -> 44.4% |
| 2025 | $664,368,903 | $38,397,778 | DD 64.6% -> 56.7% |
| 2025_dip | $6,837,767 | $2,190,962 | DD 49.8% -> 46.5% |
| 2022_now | $3.195e23 | $693,764,074 | DD 69.4% -> 57.9% |
| 5y | $9.459e26 | $885,998,142 | DD 66.3% -> 58.5% |

Combined-screen context:

- `g80 + CT + reserve` and `g80 + CT` scored higher in the broad research
  screen, but they are not the clean Sentinel production surface yet.
- CT crash suppression still needs a production-grade feature source rather
  than research-only wave prediction files.
- Wave-put reserve still needs live reserved-cash ledger semantics.
- Therefore the clean candidate is practical exposure saturation alone; CT and
  reserve are future portfolio-profile overlays after proper production
  plumbing.

Validation run after wiring:

```text
python tests/test_strategy_config_drift.py
python tests/test_mechanism_registry.py
python experiments/_dte_audit/audit.py
python trader.py alloc 50
```

Ship verdict: shipped as the Sentinel portfolio-only profile on 2026-05-21. It
confidently beats active v60 on the DD objective while preserving enough
practical compounding to reach the millionaire stage in the relevant full-year
and multi-year windows.

## 2026-05-21/22 Update: Daily N Capacity And Sentinel-Adjacent Risk-Profile Sweep

This checkpoint answers the follow-up question: how much daily signal N is
actually needed for portfolio recycling, and when can N be sacrificed for
higher WR15?

Primary capacity artifact:

```text
C:\Development\Trader\.codex\runs\n_capacity_analysis_20260521\n_capacity_summary.md
```

Risk-profile MC artifact:

```text
C:\Development\Trader\.codex\runs\n_capacity_profiles_20260521_152650\n_capacity_profile_comparison.md
```

Script surfaces added for future reuse:

```text
experiments\daily_opportunity_allocation\analyze_n_capacity.py
experiments\daily_opportunity_allocation\validate_n_capacity_profiles.py
```

### Capacity Read

The shipped Sentinel profile changes the N question. The binding constraint is not
the 15-day hard hold; realized portfolio hold time is only about 2.3 trading
bars. The practical capacity stack is:

| Surface | v60 smooth 2022+ read |
|---|---:|
| Total avg fills/day | 4.36 |
| Total q75 fills/day | 6.00 |
| Total slot service capacity/day | 6.05 |
| Call avg fills/day | 2.63 |
| Call q75 fills/day | 4.00 |
| Call slot service capacity/day | 5.03 |
| Put avg fills/day | 1.73 |
| Put q75 fills/day | 3.00 |
| Put slot service capacity/day | 3.63 |
| Cash-bound day share | 73.9% |

Interpretation:

- Filled-capacity floor is roughly 5-6 total entries/day.
- Practical side floors are roughly 4 calls/day and 3 puts/day.
- The offered-N robust center is still wider: Sentinel starts smooth opportunity
  saturation at 16 calls and 4 puts. That is the allocator's queue/diversity
  center, not the fill count.
- N above 16/4 can be traded for WR15 more freely because allocation is already
  being scaled down on crowded days.
- N below 16/4 requires Stage 3 proof because queue/diversity and long-window
  DD can break even if fill count remains above the mathematical floor.

The compensation formula used in the artifact:

```text
required_wr_new =
    wr_old * min(capacity, old_daily_n) / min(capacity, new_daily_n)
```

Using q75 total fills/day as capacity, dropping total fill-equivalent daily N to
5 requires roughly 90-96% WR to preserve expected wins from a 75-80% baseline;
dropping to 4 is not viable. Do not treat a high-WR15 filter as acceptable if it
starves the book below that fill-equivalent floor.

### Sentinel-Adjacent Risk Profiles

The custom N-capacity MC screen completed all 90 cells at N=160. The original
wrapper failed only during findings write because the custom runner passed
`versions/windows/n` metadata while the reused practical-exposure writer expected
`phase1_versions/phase1_windows/phase1_n`. The CSV evidence is complete and the
terminal artifacts were repaired to `done`.

Candidate comparison against Sentinel:

| Candidate | Refs | Avg worst DD vs shipped | Max worse vs shipped | Worst window | Practical log vs shipped | Read |
|---|---:|---:|---:|---|---:|---|
| `n12_4_quality_g80_c65_p25_floor58` | 12/4 | +3.79pp | +3.89pp | 2022_now | +0.011 | strongest next mining region, not ship-safe |
| `n12_3_defensive_g70_c58_p18_floor50` | 12/3 | +2.17pp | +1.91pp | 2022 | -0.075 | too much return/floor drag |
| `n14_3_balanced_g75_c62_p20_floor52` | 14/3 | +1.63pp | +1.23pp | 5y | -0.039 | interesting but weak floor pass |
| `n16_3_putthin_g80_c68_p20_floor58` | 16/3 | +0.67pp | +2.09pp | covid_peak | -0.026 | put-thin crash regression |
| Sentinel `n16_4_g80_c65_p25_floor55` | 16/4 | baseline | 0.00pp | - | 0.000 | robust production center |
| `n18_4_growth_g85_c70_p22_floor58` | 18/4 | -1.97pp | +5.65pp | 2022_now | -0.006 | gives back DD |
| `n20_4_growth_g90_c72_p20_floor60` | 20/4 | -2.68pp | +7.72pp | covid_peak | -0.012 | growth profile, not DD optimum |
| `n24_5_highqueue_g90_c72_p22_floor62` | 24/5 | -3.67pp | +9.51pp | covid_peak | -0.012 | too much crash/covid exposure |

### Research Verdict

Keep Sentinel `16 calls / 4 puts` as the production-safe offered-N center.
It is not the absolute highest DD-improvement point, but it is the cleanest
no-regression balance across crash, recent, and long-window surfaces.

The best next research region is **conditional 12/4**, not a static replacement:
`12/4` improves average worst DD and does not increase practical-log drag versus
shipped, but it worsens `2022_now` worst DD by about 3.9pp. A future candidate
should search for a smooth conditional ref/floor controller that behaves like
`12/4` in crowded/recent-risk windows while retaining `16/4` in the long-window
state that broke.

Stage 1 WR15 filter guidance:

- If the filter keeps daily offered pressure above roughly 16 calls / 4 puts,
  N loss is acceptable when WR15 improves.
- If the filter drops below 16/4 but stays above about 12/4, treat it as a
  research candidate and require Stage 3 portfolio proof.
- If fill-equivalent N approaches 5 total/day, require extreme WR15 lift; below
  5/day the book is likely starved regardless of headline WR15.
- Below 4 total fill-equivalent entries/day is not a viable production target
  under the current max-position/hold/recycling behavior.

## 2026-05-23 Update: Sentinel/Core/Apex Portfolio Profiles

This follow-up mined named portfolio-risk profiles on active v60 score rows.
It is Stage 3 portfolio-only work: no score rows were written, no
`ALGORITHM_VERSION` bump was needed, and the active production overlay remains
the Sentinel practical exposure profile.

Durable sweep artifact:

```text
C:\Development\Trader\.codex\runs\portfolio_profiles_20260522_230928\findings.md
```

Implementation surfaces:

```text
experiments\portfolio_profiles\sweep_profiles.py
tools\canonize_portfolio_profiles.py
algorithm_versions\portfolio_profiles.json
portfolio_profiles.py
src\pages\PortfolioProfiles.js
```

### Sweep Contract

The sweep used a three-phase Codex background run:

| Phase | N | Candidates | Windows |
|---|---:|---:|---|
| phase1 | 120 | 13 | 2020_crash, covid_peak, 2020_full, 2022, 2024, 2025, 2025_dip, 2022_now, 5y |
| phase2 | 300 | 6 | same nine windows |
| phase3 | 500 | 3 | same nine windows |

The objective deliberately split profile semantics:

- Sentinel: fixed baseline, current safe production profile.
- Core: balanced compounding; penalizes collapse, large max-DD worsening,
  average DD worsening, and 2025_dip DD worsening.
- Apex: maximum compounding with limited DD regard, but still rejects collapse
  probability and catastrophic absolute DD.

### Canonized Profiles

| Profile | Color | Candidate | Main params | Phase3 read |
|---|---|---|---|---|
| Sentinel v1 | green | `sentinel_v1_current` | current Sentinel profile: $25M ceiling, 80/65/25 caps, 16/4 refs, 14/12/8 slots | safe baseline |
| Core v1 | yellow | `core_g85_c70_p25_ref18_4_floor60_dd405565` | $25M ceiling, 85/70/25 caps, 18/4 refs, floor 0.60, 14/12/8 slots, DD 0.40/0.55/0.65 | +0.026 avg log-final lift, +4.34pp avg worst-DD, +10.15pp max worse, 0% collapse |
| Apex v1 | red | `core_noceiling_g90_c74_p24_ref20_dd456575` | $100M ceiling, 90/74/24 caps, 20/4 refs, floor 0.65, 16/14/8 slots, DD 0.45/0.65/0.75 | +0.056 avg log-final lift, +6.79pp avg worst-DD, +14.71pp max worse, 0% collapse |

### Interpretation

- The safe production center is Sentinel.
- Core is not a new DD optimum. It is a controlled risk-up profile: modest
  compounding lift, mild DD acceptance, and no collapse.
- Apex is not "turn the caps off." Fully uncapped or near-uncapped candidates
  showed huge tail wealth but 90%+ crash DD and material collapse probability.
  The mined Apex optimum keeps practical exposure controls but raises the
  ceiling, caps, refs, floor, and slot counts.
- Core and Apex are tracked in `algorithm_versions/portfolio_profiles.json`
  with version numbers so future improvements can bump profile versions without
  contaminating score-version history.

Dashboard/API surface:

```text
/api/portfolio/profiles/compare
/portfolio-profiles
```

## 2026-05-24 Update: Apex v2 Full-Portfolio Profile

User-directed Apex risk semantics now intentionally use the full portfolio
allocation base. The new Apex profile keeps the high-risk 90/74/24 caps,
20/4 refs, 16/14/8 slots, and DD 0.45/0.65/0.75 band, but removes the practical
capital ceiling (`capital_ceiling=0.0`). This is still portfolio-only Stage 3
work: no score rows were written and no `ALGORITHM_VERSION` bump was needed.

Durable artifacts:

```text
C:\Development\Trader\.codex\runs\apex_fullportfolio_compare_20260524_031304\findings.md
C:\Development\Trader\.codex\runs\temporal_apex_fullportfolio_20260524_034934\done.json
```

Targeted phase1 N=500 across the same nine v60 stress windows selected
`apex_fullportfolio_g90_c74_p24_ref20_dd456575` over the prior capped Apex:

| Profile | Candidate | Main params | N=500 read |
|---|---|---|---|
| Apex v2 | `apex_fullportfolio_g90_c74_p24_ref20_dd456575` | full portfolio base, 90/74/24 caps, 20/4 refs, floor 0.65, 16/14/8 slots, DD 0.45/0.65/0.75 | +0.05698 avg log-final lift, +7.93pp avg worst-DD, +14.71pp max worse, 74.02% max candidate worst-DD, +5.48pp 2025_dip DD worsening, 0% collapse |

Interpretation: Apex v2 is a deliberate DD-up, compounding-up profile. It is
appropriate for the red Explosive/Apex identity and not a safe replacement for
Sentinel. Core remains provisional; the next Core search should target the
middle ground between Sentinel's DD discipline and Apex v2's full-base growth.
