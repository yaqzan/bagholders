# Daily Opportunity Allocation Research Findings

Status: discovery checkpoint, not a ship candidate.

## Runs Used

- Active-version smoke: `.codex/runs/20260518-193158-daily-opportunity-allocation`
  - Local active v60 had only 339 primary signals, so this run is only a wiring
    smoke test.
- Broad research surface: `.codex/runs/20260518-194146-daily-opportunity-allocation-v59-chunked`
  - v59 `4fd7ffa9`, 2020-01-02 to 2026-05-18, 1,633 daily rows, 40,368 offered
    outcomes, 7,009 entry trades in the daily-state replay.
- Daily policy sweep: `.codex/runs/20260518-200727-daily-opportunity-policy-v59`
  - v59 `4fd7ffa9`, train cutoff 2024-12-31, forward slice from 2025-01-01.
  - Superseded as promotion evidence by the corrected focused sweep below,
    because the first policy-sweep implementation applied an extra
    experiment-only DD guard to every policy, including baseline.
- Corrected focused total-demand sweep:
  `.codex/runs/20260518-214020-focused-total-demand-v59-corrected`
  - v59 `4fd7ffa9`, corrected baseline semantics, train cutoff 2024-12-31,
    forward slice from 2025-01-01.
- MC screen:
  `.codex/runs/20260518-215136-total-demand-mc-v59`
  - v59 `4fd7ffa9`, 80 seeded runs per policy/window across 2020-crash,
    2022, 2024, 2025, 22-now, and 5y.
- Conditional alpha mining:
  `.codex/runs/20260519-conditional-alpha-mining-v59`
  - Mined one- and two-condition opportunity/regime rules using the same v59
    daily state table and the 2024-12-31 train cutoff.
- Drawdown signature reverse-engineering:
  `.codex/runs/20260519-drawdown-signature-v59`
  - Detected replay drawdown episodes directly from the baseline equity curve
    and ranked factors that were abnormal in prelude/onset/deep phases.
- Bayesian merged-alpha sweep:
  `.codex/runs/20260519-002738-bayes-alpha-sweep-v59`
  - Merged throttle, put-overhang, sparse-opportunity, Market Wave divergence,
    MaxPos, put-cap, and re-entry knobs into a 160-eval deterministic Bayesian
    screen over 12,000 candidate points.
- Bayesian alpha MC validation:
  `.codex/runs/20260519-011544-bayes-alpha-mc-v59`
  - N=80 named-window MC screen comparing baseline, the deterministic RC seed,
    and the raw top-utility diagnostic.
- Bayesian alpha fail-window ablations:
  `.codex/runs/20260519-020132-bayes-alpha-failwindow-mc-v59`,
  `.codex/runs/20260519-021204-bayes-alpha-stable-failwindow-mc-v59`,
  `.codex/runs/20260519-023206-bayes-alpha-panic-breadth-failwindow-mc-v59`
  - Isolated the 2020-crash and 2024 MC blockers, added stable label hashing
    for reproducible MC screens, and tested no-slot, DD-only, and panic-breadth
    guards.
- v60 smooth conditional controller refresh:
  `.codex/runs/v60_daily_opportunity_smooth_20260519_082415`
  - Rebuilt `daily_state.csv` from active v60 rows (`d4a3e9fec`) and current
    30DTE portfolio constants, then screened smooth branch-separated
    put-overhang, scarce-opportunity, Market Wave divergence, and re-entry
    controllers.
- v60 narrow usable-policy mining and MC:
  `.codex/runs/v60_usable_policy_mining_20260519_101853`,
  `.codex/runs/v60_usable_policy_failwindow_mc_20260519_102454`,
  `.codex/runs/v60_wave_divergence_longwindow_mc_20260519_103107`
  - Reused the active v60 daily state to mine narrower side/action rules after
    the smooth promotion-style screen found no survivors.
- v60 smooth Market Wave put-exposure wave:
  `.codex/runs/v60_wave_put_divergence_sweep_20260519_210628`,
  `.codex/runs/v60_wave_put_divergence_failwindow_mc_20260519_214600`,
  `.codex/runs/v60_wave_put_divergence_longwindow_mc_20260519_220254`,
  `.codex/runs/v60_wave_put_divergence_2025_screen_mc_20260519_224845`,
  `.codex/runs/v60_wave_put_strict_sweep_20260520_120809`,
  `.codex/runs/v60_wave_put_strict_2024_2025_mc_20260520_124124`,
  `.codex/runs/v60_wave_put_strict_2024_wide_mc_20260520_130340`,
  `.codex/runs/v60_wave_put_strict_survivor_mc_20260520_133035`,
  `.codex/runs/v60_strict0298_pathdiff_20260520_135554`
  - Refit the hard `wave_divergence_tp__skip_puts` probe into a smooth
    put-side exposure wave and tested top candidates under stable N=240 MC,
    including a stricter `0066`-neighborhood follow-up.

## Initial Conclusions

1. Daily N is useful, but not as a simple "more N means allocate more" rule.
   The naive `call_demand_wave` raised risk sharply and failed the forward
   window despite call-demand days having better fixed-barrier TP/PnL.

2. Call opportunity supply is a quality signal.
   `post_call_primary_n` and `post_call_primary_alloc_demand` were among the
   strongest opportunity/outcome links, with Spearman about +0.21 to entry TP
   rate and +0.19 to offered TP rate.

3. Put book pressure is a risk-state signal.
   `filled_put_n` and `open_put_n` correlated negatively with entry/offered TP
   rate. Q5 filled-put days had materially worse entry TP/PnL than Q1 days.

4. Call sector concentration is a separate throttle candidate.
   High `post_call_sector_hhi` days had lower entry TP/PnL and worse forward
   worst-equity returns than low-concentration days. Treat this as a Stage 3
   exposure wave, not a Stage 1 scoring objective.

5. Score post-processing already carries regime information.
   `score_processing_put_delta_n` had strong relationships with breadth and
   regime factors. Any allocation formula must avoid double-counting regime.

## Policy Sweep Read

The first daily policy sweep pointed to `total_demand_wave`, but that result is
not promotion evidence because the sweep accidentally gave every policy an
extra drawdown throttle outside the named policy formula:

- `total_demand_wave`: utility +7.623, log return +11.629, max DD 40.1%, 1,541 trades.
- baseline: utility +7.104, log return +12.589, max DD 54.9%, 1,615 trades.

Keep the directional read: opportunity N/demand is a real surface, and total
demand looked better than sparse-opportunity concentration or net-call tilt
under forward drawdown pressure. Do not use the absolute utility/DD numbers from
this first sweep for ship decisions.

## Focused Total-Demand Sweep

Superseded run: `.codex/runs/20260518-201817-focused-total-demand-v59`

That run also inherited the extra experiment-only DD guard, so its stronger
DD-primary result is invalid as a promotion candidate. It remains useful only as
a pointer to the total-demand/sector-concentration search space.

Corrected run:
`.codex/runs/20260518-214020-focused-total-demand-v59-corrected`

Corrected deterministic baseline:

- train utility 30.394
- 2025+ utility 8.581
- full utility 42.870
- 2025+ max DD 40.6%
- full max DD 96.3%
- 2020-crash max DD 96.4%
- 2022 max DD 49.7%
- 2024 max DD 26.9%

Best corrected deterministic candidate:
`sector_total_f0.84_c1.08_q40_q80`

- 2025+ utility 9.096, delta +0.515
- 2025+ log delta +0.337
- 2025+ max DD delta -1.77 points
- full utility 42.828, delta -0.042
- full max DD delta -2.55 points
- 2020-crash max DD 92.76% vs baseline 96.43%
- 2022 max DD 45.07% vs baseline 49.69%
- 2024 max DD 28.20% vs baseline 26.94%

Read: the corrected deterministic edge is modest but real enough to justify a
cheap MC screen. It is not enough for a direct ship path.

## MC Screen

Run: `.codex/runs/20260518-215136-total-demand-mc-v59`

This was an experiment-local N=80 screen, not final Stage 3 validation. It
patched allocation at the fill point inside the MC runner and compared:

- `baseline`
- `sector_total_f0.84_c1.08_q40_q80`
- `both_total_f0.84_c1.08_q40_q80`
- `sector_total_f0.84_c1.14_q40_q80`

The MC result does not pass promotion criteria.

For `sector_total_f0.84_c1.08_q40_q80`:

- 2022 improved return and drawdown: mean return delta +13,502, median return
  delta +19,497, worst DD delta -5.74 points, mean DD delta -2.02 points.
- 2025 improved mean/median return and mean DD, but worsened worst DD by +5.20
  points.
- 2020-crash lowered return and worsened worst DD by +2.57 points.
- 2024 lowered return materially and worsened mean DD by +2.52 points.
- 22-now and 5y lagged baseline on return; 5y improved worst DD by -2.95
  points but worsened mean DD by +1.09 points.

Other variants did not solve the instability:

- `both_total_f0.84_c1.08_q40_q80` helped 2025 worst/mean DD, but worsened 2022
  worst DD by +8.25 points and lagged long-window returns.
- `sector_total_f0.84_c1.14_q40_q80` improved 2020-crash mean/worst DD and 5y
  worst DD, but gave up median return, weakened 2025, and worsened 2024 DD.

Conclusion: N/demand has alpha, but a broad total-demand budget wave is too
blunt. The next formula should treat N as a conditional exposure signal, not a
global "more opportunities means more allocation" rule.

## Conditional Alpha Mining

Run: `.codex/runs/20260519-conditional-alpha-mining-v59`

The strongest different alpha family is throttle/re-entry, not a larger
total-demand boost.

Banked throttle surfaces:

- `open_put_n>=7 AND prev5_entry_avg_pnl_pct<=7.38%`: train score -1.09,
  test score -1.22, 2022 score -1.46, 2024 score -0.46, 2025 score -1.15,
  22-now score -1.35. Test edges were -18.56pp TP, -11.28pp PnL, -8.17pp
  15d worst-return, and +0.31pp 15d DD.
- `post_call_primary_alloc_demand<=0.2 AND prev5_entry_tp_rate<=44.89%`:
  test score -1.73, with -18.68pp TP, -12.92pp PnL, -13.94pp 15d
  worst-return, and +3.05pp 15d DD. It remained negative in 2020-crash, 2022,
  2024, 2025, and 22-now.
- `post_total_primary_alloc_demand<=0.78 AND breadth_pct_above_ema50<=51.27`:
  negative in every named window checked, with test edges of -7.58pp 15d
  worst-return and +5.22pp 15d DD.

Banked re-entry/boost surfaces:

- `post_call_primary_alloc_demand>=0.4 AND dd>=9.56%`: positive in 2020,
  2022, 2024, 2025, and 22-now. Test edges were +8.90pp TP, +3.59pp PnL,
  +2.66pp 15d worst-return, and -9.34pp 15d DD.
- `post_call_primary_alloc_demand>=0.7 AND regime_vix_close>=19.32`: positive
  in 2022, 2024, and 22-now, with no 2020-crash hits in this sample. Treat as
  a re-entry hypothesis needing more stress coverage.

The durable alpha ledger is now in `ALPHA_LEDGER.md`.

## Drawdown Reverse Engineering

Run: `.codex/runs/20260519-drawdown-signature-v59`

This pass detected 16 drawdown episodes using `dd>=15%`, recovery below 5%, and
max DD at least 30%. The largest known clusters were 2020-crash/recovery, 2022
bear drawdowns, early-2023, and the 2025 May/July/August/November drawdowns.

Actionable reads:

- Recent execution failure is the most consistent onset signature:
  `prev5_entry_avg_pnl_pct` low appeared at onset in 94% of episodes, and
  `prev5_entry_tp_rate` low appeared in 88%.
- Market Wave divergence is a new throttle seed. A positive/constructive
  `breadth_sector_etf_market_wave_signed>=3.72` showed bad forward edges by
  itself: -5.25pp TP, -3.37pp PnL, -1.15pp 15d worst-return, and +2.72pp 15d
  DD.
- The clearest pair rule was weak recent execution plus constructive Market
  Wave: `prev5_entry_avg_pnl_pct<=6.74% AND market_wave_signed>=3.72`, with
  -6.71pp TP, -6.36pp PnL, -5.47pp 15d worst-return, and +0.81pp 15d DD.
- Current `dd`, `prev5_dd`, and `dd_5d_delta` ranked highly but are state
  guards, not standalone predictive alpha.

This gives a different branch to test: a Market Wave divergence throttle that
shrinks exposure when broad regime looks constructive but the strategy's recent
entries are not converting.

## Bayesian Merged-Alpha Sweep

Run: `.codex/runs/20260519-002738-bayes-alpha-sweep-v59`

This pass used the existing repo-style Bayesian/phase optimization idea for a
transparent Stage 3 policy screen. It did not write DB rows or mutate shipped
strategy constants.

Result: one deterministic RC seed passed the DD/throughput guardrails.

- Utility +211.90.
- DD deltas: test +0.43pp, full +6.62pp, 22-now +23.14pp, 2020-crash
  +13.10pp, 2022 +1.18pp, 2024 +0.08pp, 2025 +0.47pp.
- Log-return deltas: test +0.14, full +1.38.
- Throughput remained close to baseline: test 1,584 trades vs 1,586 baseline;
  full 6,839 trades vs 6,997 baseline.
- Formula: `throttle_strength=0.26`, `floor_scale=0.5`,
  `put_extra_throttle=0.16`, `slot_cut=2`, `put_cap=3`,
  `recent_tp_floor=0.50`, `recent_pnl_floor=0.0674`,
  `open_put_trigger=9`, `sparse_call_trigger=0.2`,
  `sparse_total_trigger=0.52`, `breadth_floor=51.27`,
  `wave_threshold=10`, weights `(put=1.0, scarce=1.0, wave=1.5)`,
  `reentry_strength=0.12`, `reentry_dd=0.0956`,
  `reentry_call_trigger=0.7`.

The raw top utility row is not a release candidate despite better headline DD:
it improved full DD by +37.44pp and test DD by +3.86pp, but worsened 2024 DD by
1.65pp. That confirms the selection rule should prefer guardrail-pass RC rows
over raw utility.

Conclusion before MC: the merged alpha was viable enough for targeted MC
validation. It was not ready for production promotion without MC stability.

## Bayesian Alpha MC Validation

Run: `.codex/runs/20260519-011544-bayes-alpha-mc-v59`

The MC screen rejected both Bayesian candidates.

For `bayes_rc_seed`:

- Improved worst DD in 5y (-10.60pp), 2025 (-5.20pp), 22-now (-3.41pp), and
  2022 (-0.83pp).
- Failed the release-candidate gate in 2020-crash (+1.22pp worst DD) and 2024
  (+12.29pp worst DD).
- Return deltas were negative in most windows, including 2024 and long windows.

For `bayes_raw_top_utility`:

- Improved 5y worst DD by -12.01pp and 2022 by -4.21pp.
- Failed in 2020-crash (+1.19pp worst DD) and 2024 (+8.78pp worst DD).

Conclusion after MC: no ship candidate. The alpha is still useful, but the next
Bayesian search should explicitly penalize 2024 and crash-window MC DD rather
than relying on deterministic replay guardrails.

## Fail-Window Ablations

Runs:

- `.codex/runs/20260519-020132-bayes-alpha-failwindow-mc-v59`
- `.codex/runs/20260519-021204-bayes-alpha-stable-failwindow-mc-v59`
- `.codex/runs/20260519-023206-bayes-alpha-panic-breadth-failwindow-mc-v59`

Important process fix: MC uses `hash(label)` for per-window seeds. Python string
hashes are randomized per process, so repeated N=80 screens can disagree. The
Bayesian alpha MC validator now installs a stable experiment-local string hash
before calling `monte_carlo.run_window`, with `--seed-offset` available for
controlled repeat screens.

Results:

- Removing the dynamic MaxPos/slot cut did not save the candidate. Under stable
  N=240 fail-window MC, `bayes_seed_no_slot_cut` still worsened 2024 worst DD by
  +6.53pp.
- Severe-DD-only activation preserved more benign behavior but still failed
  2024 by +7.73pp worst DD.
- Panic-breadth-only activation passed 2020-crash and 2024, but only as a near
  no-op: 2024 deltas were exactly zero and 2020-crash worst-DD delta was zero
  with negative return.

Conclusion: the merged daily opportunity allocation controller is no release
candidate. Useful information remains in the daily state features, but this
formula family either hurts 2024 stochastic paths or becomes too inert to
matter.

## Usable Policy Case Mining

Runs:

- `.codex/runs/20260519-041557-usable-policy-mining-v59`
- `.codex/runs/20260519-042206-usable-policy-failwindow-mc-v59`
- `.codex/runs/20260519-042631-put-overhang-expanded-mc-v59`
- `.codex/runs/20260519-043133-put-overhang-longwindow-mc-v59`

Deterministic replay of 170 narrow rule/action pairs found 23 usable-pass
cases. The broad merged controller was not the usable shape; the signal became
usable only when reduced to a side-specific put hedge:

```text
if open_put_n >= 7 and prev5_entry_avg_pnl_pct <= 0.07378:
    put_alloc_scale = 0.70
    max_open_puts = 5
```

The best-balanced deterministic candidate was
`put_overhang_pnl__puts70_cap5`. It preserved 96.1% of full-window trades,
improved 22-now deterministic DD by +22.85pp, and had no deterministic 2020 or
2024 DD regression.

Stable N=240 MC changed the read from "release candidate" to "usable DD hedge":

- Fail windows: 2024 worst DD improved by -1.04pp and 2020-crash was neutral.
- Long windows: 22-now worst DD improved by -14.83pp and 5y by -2.89pp; mean
  DD also improved materially in both.
- Blocker: 2024 mean return fell materially, and 2025 worst DD worsened slightly
  by +0.15pp in the long-window MC pass.

Conclusion: we did mine a usable case, but not a ship. The candidate should be
carried forward as a drawdown hedge and refined with a return-drag guard, not
merged as-is.

## v60 Smooth Conditional Controller Refresh

Run: `.codex/runs/v60_daily_opportunity_smooth_20260519_082415`

This run used active shipped v60 rows and current v60 30DTE constants. It did
not write score rows, did not run recalc, and did not change
`ALGORITHM_VERSION`.

Dataset:

- version v60 `d4a3e9fec`
- 1,634 daily rows from 2020-01-01 through 2026-05-19
- 7,241 replay trades
- 14 total positions, 12 call cap, no put side cap
- replay source: `daily_opportunity_allocation_v60_caps`

Deterministic branch screen:

- Put-overhang smooth variants did not cleanly solve the v59 blocker. The best
  branch candidate selected for MC, `put_overhang_0076`, had tiny crash DD
  improvement but still showed a +3.14pp 2024 deterministic DD regression.
- Scarce-opportunity gross throttles were mostly unstable and often damaged
  2024 DD or returns.
- Market Wave divergence variants were mostly inert: little or no DD movement
  and only small return deltas.
- Re-entry boost was the best deterministic family. The raw top row
  `reentry_0104` had utility +91.65, but it already had a +3.19pp 2024
  deterministic DD regression. Cleaner re-entry rows existed, but no branch
  was allowed to skip fail-window MC.

Stable N=240 fail-window MC:

- Legacy `put_overhang_pnl__puts70_cap5`: 2024 return drag persisted and 2024
  worst DD worsened by +16.53pp under the v60 active-row MC screen.
- `put_overhang_0076`: 2020-crash worst DD improved by -0.36pp, but 2024 worst
  DD worsened by +1.78pp.
- `scarce_opps_0006`: 2024 worst DD worsened by +6.64pp.
- `wave_divergence_0095`: worst DD was unchanged in both fail windows and 2024
  mean return fell; this is a no-op/drag, not alpha.
- `reentry_0104`: 2020-crash worst DD worsened by +2.35pp and 2024 worst DD
  worsened by +6.64pp.

Conclusion: no ship candidate and no broader MC. The v60 active-row screen
produced zero 2020-crash/2024 survivors, so it correctly skipped 2022/2025/
22-now/5y validation. The put-overhang hedge remains a useful DD-control idea,
but the smooth v60 attempt did not fix the fail-window blocker. Re-entry
remains a better future branch than broad total-demand throttling, but only if
the next formula explicitly guards crash/stress paths before any boost is
allowed.

## v60 Narrow Usable-Policy Mining

Runs:

- `.codex/runs/v60_usable_policy_mining_20260519_101853`
- `.codex/runs/v60_usable_policy_failwindow_mc_20260519_102454`
- `.codex/runs/v60_wave_divergence_longwindow_mc_20260519_103107`

This was a discovery pivot after the smooth promotion-style screen produced no
fail-window survivors. The mining pass intentionally used hard/narrow actions
as probes, not ship formulas.

Deterministic mining found 51 usable rows. The best raw row
`sparse_call_tp__skip_calls` was rejected despite a huge full-window DD
improvement because it worsened 2020-crash and 22-now DD. The useful read was
not that row; it was the branch evidence from smaller, cleaner side actions.

Stable N=240 fail-window MC on top usable rows:

- `wave_divergence_tp__skip_puts` passed the 2020-crash / 2024 screen. It was
  neutral in 2020-crash, neutral on 2024 worst DD, improved 2024 mean return by
  about +12.3M percentage points, and improved 2024 mean DD by -0.03pp.
- `sparse_call_tp__calls_50` and `sparse_call_tp__calls_70` lifted 2024 mean
  return but worsened 2024 worst DD by +3.44pp and +8.63pp respectively.
- Sparse put-cap variants failed 2024 worst-DD stability.
- `put_overhang_pnl__puts_50` had only a small 2024 worst-DD drift (+0.32pp)
  and better 2024 mean DD, but retained material 2024 return drag.

Broader N=240 MC:

- `wave_divergence_tp__skip_puts` did not worsen worst DD in 2022, 22-now, or
  5y, and improved 2025 worst DD by -1.53pp. It improved 22-now mean return,
  was no-op in 2022, and was mixed in 5y: mean return fell while median return
  rose. Mean DD worsened by +0.41pp in 2025 and +0.11pp in 5y.
- `put_overhang_pnl__puts_50` improved 22-now worst DD by -3.36pp and 5y worst
  DD by -2.47pp, with better long-window mean DD. It still carried large
  return drag in 22-now and 5y, so it remains a hedge lead rather than a ship
  candidate.

Conclusion: yes, there is alpha to mine. The current best lead is not the old
put-overhang hard cap; it is a Market Wave divergence put-side throttle:

```text
if prev5_entry_tp_rate <= 0.5133
   and breadth_sector_etf_market_wave_signed >= 3.72:
    reduce or block new put exposure
```

Do not ship this hard `skip_puts` form. The next search should turn it into a
smooth put-side wave, test put scale/cap variants, and explicitly score both
worst-DD preservation and mean-DD/return drag.

## v60 Smooth Market Wave Put Exposure

Runs:

- `.codex/runs/v60_wave_put_divergence_sweep_20260519_210628`
- `.codex/runs/v60_wave_put_divergence_failwindow_mc_20260519_214600`
- `.codex/runs/v60_wave_put_divergence_longwindow_mc_20260519_220254`
- `.codex/runs/v60_wave_put_divergence_2025_screen_mc_20260519_224845`
- `.codex/runs/v60_wave_put_strict_sweep_20260520_120809`
- `.codex/runs/v60_wave_put_strict_2024_2025_mc_20260520_124124`
- `.codex/runs/v60_wave_put_strict_2024_wide_mc_20260520_130340`
- `.codex/runs/v60_wave_put_strict_survivor_mc_20260520_133035`
- `.codex/runs/v60_strict0298_pathdiff_20260520_135554`

The hard `skip_puts` probe was converted into a continuous put exposure wave:

```text
pressure = recent_weak * constructive_market_wave
           * (0.5 + 0.5 * put_overhang)
put_scale = max(floor_scale, 1.0 - strength * pressure)
```

This matches the Market Wave ethos better than a side skip: it fades new put
exposure when recent execution is weak, Market Wave is constructive, and the
put book is already pressured. It does not change scores or version state.

Deterministic sweep:

- `wave_put_divergence_0051` was the top row: utility +1.96, neutral
  2020-crash / 2024 DD, and +0.14 deterministic 2024 log-return delta.
- It used `strength=0.55`, `floor_scale=0.45`, `weak_tp_floor=0.58`,
  `wave_threshold=0`, and `wave_width=24`.
- The top-candidate artifact was corrected to include the new
  `wave_put_divergence` branch; prior output had only the benchmark row.

Stable N=240 fail-window MC:

- The old hard `put_overhang_pnl__puts70_cap5` benchmark again failed 2024:
  +16.53pp worst DD and material return drag.
- `wave_put_divergence_0051` preserved 2020-crash and 2024 worst DD, improved
  2024 mean DD by -0.02pp, and improved 2024 median return, but had a small
  negative mean-return delta.
- Other top smooth rows also preserved 2020-crash and 2024 worst DD, but most
  still showed 2024 return drag.

Broader MC:

- `wave_put_divergence_0051` failed 2025 with +3.11pp worst DD and also
  worsened 22-now / 5y mean DD. No ship.
- A targeted 2025 screen found the failure was parameter-specific:
  `wave_put_divergence_0066` improved 2025 worst DD by -1.82pp and improved
  2025 mean and median return.
- `wave_put_divergence_0066` is not a ship candidate because fail-window MC
  already showed material 2024 return drag.

Strict `0066`-neighborhood follow-up:

- The strict deterministic sweep top row was `wave_put_divergence_strict_0318`
  (utility +2.25), but top-ranked rows still failed MC because of 2024 return
  drag.
- A wider 2024-only MC screen found two return-stable survivors:
  `wave_put_divergence_strict_0298` and `wave_put_divergence_strict_0127`.
- Survivor N=240 MC rejected both. `strict_0298` held 2020-crash and fixed the
  2024 blocker (+2.06Mpp mean return delta, neutral worst DD), but dragged 2025
  return and worsened 2025 mean DD by +0.87pp. `strict_0127` also held
  2020-crash and had positive 2024 mean return, but worsened 2025 worst DD by
  +3.11pp.

`strict_0298` path-diff:

- The N=240 tape run reproduced the split under an identical path-diff label:
  2024 mean return +2.07Mpp with neutral worst DD, while 2025 mean return
  -56.3kpp and worst DD +4.47pp.
- The 2025 drag was not the same-key put leg. Shared put trade delta was
  positive in both windows: +$374.5B in 2024 and +$1.17B in 2025.
- The blocker is replacement/path churn from freed buying power. In 2025,
  inactive days after prior path perturbation accounted for about -$5.79B net
  trade PnL delta versus about -$0.97B on directly active scale days.
- The worst-DD episode shifted into late October / November 2025. The top 80
  episode comparisons were all worse; average top-episode delta was +8.25pp,
  with max +19.14pp.
- Month attribution concentrated in 2025-12 (-$4.76B net trade delta) and
  2025-11 (-$1.69B). This points to cash/slot recycling after the throttle,
  not a bad same-day signal from the put-wave pressure itself.

Conclusion: there is alpha in the wave-shaped put throttle, and the hard
`skip_puts` concept can behave like a smooth exposure wave. It is still no-ship:
no single candidate has passed 2020-crash, 2024 return/DD stability, and 2025
return/DD stability together. Do not broaden this branch into Stage 3 combined
validation unless the next formula treats reduced put exposure as a gross
exposure/cash-reserve cut rather than free capital to recycle into later fills,
and explicitly guards the late-2025 episode path.

## v60 Smooth Put-Wave Cash Reserve

Runs:

- `.codex/runs/v60_wave_put_reserve_sweep_20260520_150830`
- `.codex/runs/v60_wave_put_reserve_failwindow_mc_20260520_152929`
- `.codex/runs/v60_wave_put_reserve_broad_mc_20260520_155325`
- `.codex/runs/v60_wave_put_reserve_0069_n500x8_20260520_165723`
- `.codex/runs/v60_wave_put_reserve_0124_n500x8_20260520_175629`
- `.codex/runs/v60_wave_put_reserve_0051_n500x8_20260520_182619`
- `.codex/runs/v60_prod_reserve_mc_resume_20260520_224152`
- `.codex/runs/v60_prod_reserve_0124_mc_20260520_225350`
- `.codex/runs/v60_prod_reserve_0124_n500_20260520_230521`

This pass tested the path-diff fix directly: when a put is throttled, reserve
the unspent premium until the trade's original exit date instead of letting the
cash immediately fund replacement trades. It is still a smooth put exposure
wave, not a hard skip:

```text
pressure = recent_weak * constructive_market_wave
           * (0.5 + 0.5 * put_overhang)
put_scale = max(floor_scale, 1.0 - strength * pressure)
```

Deterministic reserve sweep:

- Best row: `wave_put_divergence_reserve_0069`, utility +1.84.
- Top reserve rows held 2020-crash and 2024 deterministic DD flat and generally
  removed the 2024 return-drag blocker.
- The old hard benchmark `put_overhang_pnl__puts70_cap5` remained no-ship in
  MC, failing 2024 with +16.53pp worst-DD delta and return drag.

N=240 fail-window MC:

- Passes: `0069`, `0051`, `0124`, `0144`.
- Reject: `0130` due to +1.49pp 2025 worst-DD drift.
- `0124` had the strongest 2025 worst-DD improvement at N=240 (-3.18pp), while
  `0069` best preserved the explicit `wave_threshold=3.72` divergence shape.

N=240 broad MC:

- `0069`, `0051`, `0124`, and `0144` all had zero worst-DD regression across
  2022, 22-now, and 5y.
- `0051` had the strongest 5y mean-return lift in that screen.

N=500 x 8 final screens:

- `0069`: no-ship. It held 2024/2025/dip/22-now, but failed 5y worst DD by
  +7.90pp.
- `0124`: passes hard gates, but is weaker as a release shape. It had no
  5y worst-DD regression and improved 22-now / 2024 / dip, but 5y mean return
  fell while median rose; 2025 worst-DD was effectively flat (+0.017pp).
- `0051`: current promotion candidate. It passed N=500 x 8 with no worst-DD
  regression in any window and -2.03pp worst-DD improvement in 2025. 5y mean
  and median return both improved; 2024 and dip improved return with neutral
  worst DD; 22-now mean return improved with neutral worst DD, although median
  return was lower.

Production-equivalent MC changed the ranking. `0051` remained useful evidence
for the reserve mechanic, but still carried return-drag fingerprints in the
canonical engine. `0124` was cleaner and is now the promotion candidate.

`0124` parameters:

```text
strength=0.55
floor_scale=0.45
weak_tp_floor=0.539
weak_tp_width=0.10
open_put_trigger=9
open_put_width=8
open_put_share_trigger=0.65
open_put_share_width=0.25
wave_threshold=0
wave_width=6
```

Production-equivalent N=500 MC for `0124`:

- `2020-crash`: unchanged worst DD, unchanged log return.
- `2024`: unchanged worst DD, mean log +0.0661, median log +0.0711.
- `2025`: worst DD -4.93pp, mean log +0.0545, median log +0.0652.
- `22-now`: worst DD -5.27pp, mean log +0.0399, median log +0.0953.
- `5y`: unchanged worst DD, mean log +0.2063, median log +0.1104.
- Collapse delta: 0.00pp everywhere.

Current judgment: alpha was mined. `wave_put_divergence_reserve_0124` is the
portfolio-stage promotion candidate, not a scoring change and not yet a
production ship. Shipping requires isolating the reserve-cash mechanic in the
live portfolio path so unused throttled premium remains intentionally idle
rather than silently available to later fills. Do not bump `ALGORITHM_VERSION`
and do not run score recalc.

## Candidate Formula Family

Do not ship the first total-demand wave. The next candidate family should be a
conditional Stage 3 exposure wave:

```text
total_wave = clip((post_total_primary_alloc_demand - train_q40)
                  / (train_q80 - train_q40), 0, 1)

call_scale = baseline_call_scale
put_scale = baseline_put_scale

if total_wave is high and call_sector_concentration is high:
    call_scale *= concentration_guard

if put_pressure is high and regime/breadth is not put-friendly:
    put_scale *= put_pressure_guard

if open_put_n >= 7 and prev5_entry_avg_pnl_pct is weak:
    put_scale *= 0.70
    cap open puts at 5

if opportunities are sparse but recent TP velocity is high:
    allow a small concentration boost, capped by DD state
```

Refined from conditional mining:

```text
if recent_weak and put_overhang:
    reduce total exposure and cap puts

if recent_weak and scarce_call_opps:
    reduce total exposure and max positions

if scarce_opps and weak breadth:
    reduce gross allocation even if cash is available

if current_dd elevated and call demand remains healthy:
    permit a small call-side re-entry boost, capped by stress state

if market_wave constructive and recent execution weak:
    throttle exposure; do not assume constructive wave means budget expansion
```

Then test guarded extensions independently:

- Keep `sector_total_f0.84_c1.08_q40_q80` as a benchmark, not a candidate.
- Mine 2024 and 2020-crash failure days before adding complexity.
- Separate call-demand and put-pressure effects; the combined total wave is too
  coarse.
- Add dynamic MaxPos only after the allocation wave survives MC validation.
- The merged Bayesian allocation-controller path is no-ship. Mine a different
  alpha family rather than adding more knobs to this one.
- The current best mined family is put-overhang plus weak recent execution;
  solve the 2024 return drag before any Stage 3 release-candidate sweep.

## ML Architecture Fit

Use the repo's existing `PhaseOptimizer` style for the next search stage after
the formula family is constrained. The ML probe in this folder is intentionally
only for feature discovery:

- Random forest generalized modestly for future drawdown increase.
- It did not generalize as a direct forward-return predictor.

So ML should optimize transparent wave parameters and detect interactions; it
should not become an opaque daily return forecast for allocation.

## Guardrails

- No `ALGORITHM_VERSION` bump.
- No score recalc.
- No production portfolio constants changed.
- Treat all outputs here as Stage 3 discovery until deterministic stress
  windows and MC-style validation pass.

## 2026-05-19 v60 Extra-Call / Market-Wave Proxy Pass

Run:

```text
C:\Development\Trader\.codex\runs\v60_extra_call_wave_20260519_215034
```

This pass corrected the earlier `v59_extra_calls_guard` read. The first hard
controller MC used an overlap feature that was only populated through 2020, so
its later-window "pass" was partly a no-op artifact. The all-window rebuild
shows the v59/v60 divergence exists in later years too, but the raw overlap
metric is still diagnostic-only and should not be shipped.

Key empirical reads:

- v60-only calls are almost entirely 70-74 boundary calls. The v60-only
  membership model was dominated by `wi_pre_boost`, `v60_stoch`,
  `v60_margin_over_70`, `v60_overall`, `wi_pre_regime`, SCW dampening fields,
  and `wi_w_adj`. This points to marginal score-boundary mechanics, not a
  sector-specific or ticker-specific exception.
- In the 2020-crash slice, the extra symbols themselves were not worse than the
  common v59/v60 call set: extra calls had 51.6% win / 45.2% bad vs common
  calls at 45.7% win / 48.4% bad. The hard controller worked because the
  divergence days identified a dangerous market/book state, not because those
  exact extra symbols were uniquely toxic.
- Across 5y, v60-only calls are lower quality: 53.4% win / 44.4% bad / +0.044
  mean sampled pnl vs common calls at 60.1% win / 37.0% bad / +0.120 mean pnl.
  This is real marginal-call quality drag, but it is not a standalone COVID DD
  explanation.
- The stronger alpha is a daily market-structure wave over call accuracy. The
  ML daily wave split produced top-20% risk days with 64.9% call-bad rate and
  32.4% call-win rate, vs non-top-20 days at 34.1% bad and 63.1% win. Top-10%
  risk days were more extreme: 73.4% bad and 24.3% win.
- Daily wave feature importance was broad-market and opportunity-structure
  heavy: breadth above EMA200/EMA50, call/put primary share, recent call TP
  rate, call primary N and alloc demand, SPY distance from EMA200, market trend,
  McClellan, prior DD, and VIX.

Best next score-stage candidate family:

```text
market_structure_call_risk = smooth ML-derived or hand-fit wave over:
    breadth_pct_above_ema200 / ema50
    sector and market breadth deterioration
    call-primary share and call alloc demand
    weak recent call TP velocity
    SPY distance from EMA200 / regime trend / VIX

call_vulnerability = smooth boundary wave centered on v60 overall 70-74,
                     with optional SCW / weak weekly / sector-crash pressure
                     amplifiers

call_score_delta = -alpha * market_structure_call_risk
                          * call_vulnerability
                          * distance_to_neutral
```

Signal-level demotion screens worth staging:

- `wave_q80__overflow_70_74`: removes 4,259 calls; removed set is 60.4% bad /
  37.2% win, and removes 95 crash-window calls with 77 bad saved vs 12 wins
  lost.
- `wave_q90__overflow_70_74`: removes 2,226 calls; removed set is 65.7% bad /
  32.0% win, and removes 75 crash-window calls with 61 bad saved vs 8 wins
  lost.
- `wave_q90__sector_crash_overflow`: much narrower, removes 102 calls; removed
  set is 79.4% bad / 14.7% win, and removes 38 crash-window calls with 33 bad
  saved vs 1 win lost. This is the cleanest precision lead but may be too sparse
  for enough portfolio effect.

No ship yet. The next step is staging-native scoring validation of a smooth
Market Structure Overflow Dampener on 70-74 calls. It must prove WR15/high-tier
N utility first, then pass portfolio DD windows. Do not ship the raw v59 overlap
metric, and do not repeat broad portfolio throttles as a substitute for the
score-boundary wave.

## 2026-05-19 Score-Dampen Proxy MC Validation

Run:

```text
C:\Development\Trader\.codex\runs\v60_score_dampen_proxy_mc_20260519_222128
```

This pass emulated score-stage demotion by removing marginal 70-74 calls under
the mined market-structure risk wave. It used active v60 rows and N=240 MC
across 2020-crash, 2024, 2025, 2022, 22-now, and 5y.

Result: no shippable proxy. The signal-level wave is real, but hard-removing
calls at the MC loader is too blunt and changes portfolio pathing in ways that
create new drawdown elsewhere.

Key MC reads:

- Broad `wave_q90_overflow_70_74` helped 2020-crash (-1.67pp worst DD) but
  failed badly in 2024 (+13.90pp worst DD) and worsened 22-now / 5y.
- `wave_q90_overflow_plus_wadj` improved 2020-crash (-2.32pp) and 2022
  (-1.07pp), but failed 2024 (+1.89pp), 2025 (+1.80pp), and 22-now (+2.81pp).
- `wave_q90_extra_like_overflow` improved 2020-crash (-2.99pp), 2022
  (-1.07pp), 22-now (-3.76pp), and 5y (-0.68pp), but failed 2024 (+5.95pp)
  and 2025 (+2.27pp).
- Precision `wave_q90_sector_crash_overflow` was the cleanest lead: it improved
  2020-crash (-3.04pp), improved 5y (-2.47pp), preserved 2024/2025, and had
  only minor 22-now drift (+0.66pp), but failed 2022 (+3.39pp).
- `wave_q85_sector_crash_overflow` also preserved 2024/2025 and improved
  2020-crash (-2.59pp), but failed 2022 (+2.39pp) and 22-now (+1.10pp).

Interpretation:

- The Market Structure Overflow Dampener is not dead, but the first hard
  demotion forms are no-ship.
- The remaining research value is the split between acute crash overflow and
  ordinary bear-market overflow. The q90 sector-crash filter blocked only two
  COVID days (2020-03-05 and 2020-03-06), and those blocked calls were highly
  negative. It also blocked six 2022 bear-market days; despite poor signal-level
  outcomes there, portfolio pathing worsened 2022 DD. The next formula must add
  a 2022 guard, not merely increase precision.
- Candidate direction: soften the score effect instead of hard-removing calls,
  and require acute crash confirmation such as fast breadth collapse / VIX shock
  / crash-echo acceleration. Avoid filters that fire on slow 2022-style
  bear-market churn.

Do not promote this as v61. Preserve it as future scoring evidence: a smooth,
acute-crash-only overflow dampener may be worth staging, but hard q-threshold
removal is rejected.

## 2026-05-20 Score-Dampen Path-Diff / CT Crash Lead

Combined read:

```text
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_combined_20260520_130700\combined_summary.md
```

Supporting runs:

```text
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_20260520_123050
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_5y_20260520_125410
C:\Development\Trader\.codex\runs\v60_score_dampen_path_diff_ct_20260520_130200
```

The path-diff pass corrected the hard-removal read. Outside COVID, the
q90-sector filters mostly removed zero-allocation 70-74 overflow rows, not
filled trades. The apparent 2022 / 22-now / 5y movement is therefore mostly MC
path/tie-break perturbation from changing the candidate list, not a clean
capital-bearing signal.

The real capital-bearing COVID alpha is narrower:

- `wave_q90_sector_crash_overflow` blocked 38 COVID signals, but only 2 were
  CT-like (`trend <= 20`) and actually carried allocation through CT promotion.
- Those 2 signals were both on 2020-03-05, both lost, and had average sampled
  option PnL of -70.8%.
- CT-only validation `acute_q90_sector_ct_overflow` blocked only those 2
  source signals. At N=240 on 2020-crash it improved mean DD by -3.84pp, worst
  DD by -0.78pp, and had 225 better seeds vs 5 worse seeds.
- The CT-only selector fires on zero signals in 2022, 2024, 22-now, and the
  5y window, so it avoids the slow-bear churn that killed broader filters.

Best candidate direction:

```text
ct_call_crash_suppressor =
    score in 70-74
    AND trend <= CT_CALL_TREND_MAX
    AND market_structure_wave >= q90
    AND sector_crash_pressure >= 0.55
    AND breadth_pct_above_ema50 <= 25
    AND McClellan <= -40

action: suppress CT-call promotion or fade CT-call allocation toward zero
        under acute crash tape.
```

Ship read: no scoring-version ship. This belongs in Stage 3 as a guarded CT
promotion crash suppressor, not as a v61 score formula and not as a v59 overlap
metric. The evidence is strong for COVID protection but sparse (`N=2` source
signals), so the next validation should test a smooth CT-call allocation fade
against additional crash/stress windows before production.

## 2026-05-20 CT Crash Suppressor MC Validation

Run:

```text
C:\Development\Trader\.codex\runs\v60_ct_crash_suppressor_mc_20260520_190800
```

This pass directly tested the CT-call crash lead from the path-diff artifacts
with N=240 MC across 2020-crash, covid-peak, 2020-full, 2022, 2024, 2025,
22-now, and 5y. It compared two Stage 3 controls:

- `ct_crash_suppress`: leave the signal in the candidate list but suppress CT
  promotion, so 70-74 calls fall back to overflow allocation.
- `ct_crash_demote_low`: suppress CT promotion but lift the signal to the
  normal 75 low tier, approximating a partial allocation fade.

Result: `ct_crash_suppress` is the promotion candidate. It touched exactly the
two COVID CT-promoted source calls and was neutral everywhere else tested.

Key MC reads:

- `ct_crash_suppress`: covid-peak mean return +57.70pp, worst DD -8.91pp,
  mean DD -7.98pp; 2020-crash mean return +41.95pp, worst DD -2.68pp, mean DD
  -3.70pp; 2020-full mean return +34598.70pp, worst DD -2.32pp, mean DD
  -3.92pp.
- `ct_crash_demote_low`: also passed, but with weaker protection: covid-peak
  worst DD -4.80pp, 2020-crash worst DD -0.68pp, and 2020-full worst DD
  -1.04pp.
- Both policies touched zero signals and had exactly flat deltas in 2022, 2024,
  2025, 22-now, and 5y.

Ship read: proceed to implementation-stage validation for `ct_crash_suppress`,
not `ct_crash_demote_low`. This remains a Stage 3 portfolio/cascade control,
not a scoring version bump. The production shape should suppress CT promotion
under acute crash tape while preserving the original score and candidate list,
then rerun production-equivalent MC and temporal refresh validation.
