# Daily Opportunity Allocation Alpha Ledger

Status: Stage 3 research ledger, not a ship candidate.

Primary evidence:

- Broad state run:
  `.codex/runs/20260518-194146-daily-opportunity-allocation-v59-chunked`
- Corrected total-demand sweep:
  `.codex/runs/20260518-214020-focused-total-demand-v59-corrected`
- Total-demand MC screen:
  `.codex/runs/20260518-215136-total-demand-mc-v59`
- Conditional alpha mining:
  `.codex/runs/20260519-conditional-alpha-mining-v59`
- Drawdown signature reverse-engineering:
  `.codex/runs/20260519-drawdown-signature-v59`
- Bayesian merged-alpha sweep:
  `.codex/runs/20260519-002738-bayes-alpha-sweep-v59`
- Bayesian alpha MC validation:
  `.codex/runs/20260519-011544-bayes-alpha-mc-v59`
- Bayesian alpha fail-window ablations:
  `.codex/runs/20260519-020132-bayes-alpha-failwindow-mc-v59`
  `.codex/runs/20260519-021204-bayes-alpha-stable-failwindow-mc-v59`
  `.codex/runs/20260519-023206-bayes-alpha-panic-breadth-failwindow-mc-v59`
- v60 active-row smooth conditional controller refresh:
  `.codex/runs/v60_daily_opportunity_smooth_20260519_082415`
- v60 narrow usable-policy mining and MC:
  `.codex/runs/v60_usable_policy_mining_20260519_101853`
  `.codex/runs/v60_usable_policy_failwindow_mc_20260519_102454`
  `.codex/runs/v60_wave_divergence_longwindow_mc_20260519_103107`
- v60 smooth Market Wave put-exposure wave:
  `.codex/runs/v60_wave_put_divergence_sweep_20260519_210628`
  `.codex/runs/v60_wave_put_divergence_failwindow_mc_20260519_214600`
  `.codex/runs/v60_wave_put_divergence_longwindow_mc_20260519_220254`
  `.codex/runs/v60_wave_put_divergence_2025_screen_mc_20260519_224845`
  `.codex/runs/v60_wave_put_strict_sweep_20260520_120809`
  `.codex/runs/v60_wave_put_strict_2024_2025_mc_20260520_124124`
  `.codex/runs/v60_wave_put_strict_2024_wide_mc_20260520_130340`
  `.codex/runs/v60_wave_put_strict_survivor_mc_20260520_133035`
  `.codex/runs/v60_strict0298_pathdiff_20260520_135554`
- v60 smooth put-wave cash-reserve validation:
  `.codex/runs/v60_wave_put_reserve_sweep_20260520_150830`
  `.codex/runs/v60_wave_put_reserve_failwindow_mc_20260520_152929`
  `.codex/runs/v60_wave_put_reserve_broad_mc_20260520_155325`
  `.codex/runs/v60_wave_put_reserve_0069_n500x8_20260520_165723`
  `.codex/runs/v60_wave_put_reserve_0124_n500x8_20260520_175629`
  `.codex/runs/v60_wave_put_reserve_0051_n500x8_20260520_182619`
- production-equivalent reserve MC:
  `.codex/runs/v60_prod_reserve_mc_resume_20260520_224152`
  `.codex/runs/v60_prod_reserve_0124_mc_20260520_225350`
  `.codex/runs/v60_prod_reserve_0124_n500_20260520_230521`

## Banked Alpha

1. Call opportunity supply is quality, not just capacity.
   High `post_call_primary_n` / `post_call_primary_alloc_demand` links to
   better same-day entry TP and PnL. This should inform exposure permission,
   but not a blind global budget expansion.

2. Put overhang plus weak recent execution is a clean throttle state.
   `open_put_n>=7 AND prev5_entry_avg_pnl_pct<=7.38%` produced stable badness:
   train score -1.09, test score -1.22, 2022 score -1.46, 2024 score -0.46,
   2025 score -1.15, 22-now score -1.35. Forward 2025+ edges were
   -18.56pp TP, -11.28pp PnL, -8.17pp 15d worst-return, and +0.31pp 15d DD.

3. Sparse call opportunity plus weak recent hit rate is a stronger throttle.
   `post_call_primary_alloc_demand<=0.2 AND prev5_entry_tp_rate<=44.89%`
   showed test score -1.73, with -18.68pp TP, -12.92pp PnL, -13.94pp 15d
   worst-return, and +3.05pp 15d DD. It stayed negative in 2020-crash, 2022,
   2024, 2025, and 22-now.

4. Low total opportunity demand plus weak breadth is broad no-risk-off alpha.
   `post_total_primary_alloc_demand<=0.78 AND breadth_pct_above_ema50<=51.27`
   had negative scores across 2020-crash, 2022, 2024, 2025, and 22-now. The
   test edge was not a TP collapse, but a forward-risk signature:
   -7.58pp 15d worst-return and +5.22pp 15d DD.

5. Low participation is itself a broad throttle.
   `filled_call_n<=1` was negative across 2020-crash, 2022, 2024, 2025, and
   22-now, with 2025+ TP edge -13.38pp and PnL edge -4.87pp.

6. Drawdown re-entry may be alpha when opportunity supply remains healthy.
   `post_call_primary_alloc_demand>=0.4 AND dd>=9.56%` showed positive 2020,
   2022, 2024, 2025, and 22-now scores. Test edges: +8.90pp TP, +3.59pp PnL,
   +2.66pp 15d worst-return, and -9.34pp 15d DD. This is a better candidate
   family than the broad total-demand wave because it conditions on book pain.

7. High call demand with elevated VIX is a possible selective boost.
   `post_call_primary_alloc_demand>=0.7 AND regime_vix_close>=19.32` had test
   edges of +11.76pp TP, +7.53pp PnL, +2.02pp 15d worst-return, and -1.98pp
   15d DD. It was positive in 2022, 2024, and 22-now, with no 2020-crash hits
   in this sample. Treat this as a re-entry hypothesis needing more stress
   coverage, not a ship candidate.

8. Total-demand boost remains a benchmark, not the formula.
   High total demand often has strong entry quality, but the MC screen showed
   unstable stress behavior. Broad global scaling on total N is too blunt.

9. Drawdown onsets consistently show recent execution failure.
   Reverse-engineering 16 replay drawdown episodes found `prev5_entry_avg_pnl_pct`
   low at drawdown onset in 94% of episodes and `prev5_entry_tp_rate` low in
   88% of episodes. These are shifted/live-safe features, unlike same-day entry
   outcomes. They should be used as exposure-velocity inputs.

10. Market Wave divergence is a new throttle candidate.
    `breadth_sector_etf_market_wave_signed>=3.72` or
    `breadth_sector_etf_market_wave_score>=51.86` appeared in the prelude of
    60% of measured episodes, but the rule itself had bad forward edges:
    -5.25pp TP, -3.37pp PnL, -1.15pp 15d worst-return, and +2.72pp 15d DD.
    This suggests risk when the market-wave surface looks constructive while
    our strategy execution is not confirming it.

11. Weak execution plus positive Market Wave is the cleanest drawdown-derived
    pair rule.
    `prev5_entry_avg_pnl_pct<=6.74% AND
    breadth_sector_etf_market_wave_signed>=3.72` produced 50 selected days with
    -6.71pp TP, -6.36pp PnL, -5.47pp 15d worst-return, and +0.81pp 15d DD.
    The similar hit-rate pair
    `prev5_entry_tp_rate<=51.33% AND market_wave_signed>=3.72` produced
    -8.33pp TP, -4.97pp PnL, -4.71pp 15d worst-return, and +0.74pp 15d DD.

12. Current drawdown state is a guard, not the alpha.
    `dd`, `prev5_dd`, and `dd_5d_delta` rank highly because they identify
    already-forming drawdowns. Use them to modulate severity, pause re-entry,
    or cap gross exposure, but do not treat them as independent predictive
    alpha.

13. The first merged-alpha Bayesian sweep found one deterministic RC seed.
    The guardrail-pass formula uses moderate throttle strength, a lower
    allocation floor, put-specific extra throttle, a small MaxPos cut, put cap,
    weak-execution thresholds, sparse total demand, Market Wave divergence, and
    capped re-entry. It improved DD in test (+0.43pp), full (+6.62pp), 22-now
    (+23.14pp), 2020-crash (+13.10pp), 2022 (+1.18pp), 2024 (+0.08pp), and
    2025 (+0.47pp), while preserving throughput. It was only an MC-validation
    seed, not production promotion.

14. Raw utility still overfits stress-window tradeoffs.
    The top utility row improved full DD by +37.44pp and test DD by +3.86pp,
    but worsened 2024 DD by 1.65pp. Another high utility row worsened 2022 DD.
    Rank the RC-pass subset above raw utility for release-candidate selection.

15. The first Bayesian RC seed failed MC validation.
    N=80 MC rejected both `bayes_rc_seed` and the raw top-utility diagnostic.
    `bayes_rc_seed` improved 5y, 22-now, 2025, and 2022 worst-DD surfaces, but
    worsened 2020-crash worst DD by +1.22pp and 2024 worst DD by +12.29pp,
    with negative return deltas in most windows. The raw top-utility diagnostic
    had the same blocker: 2020-crash +1.19pp and 2024 +8.78pp worst-DD. This is
    no-ship, but it gives a precise next search target: preserve the long-window
    DD reduction without breaking 2024 and crash-window stochastic paths.

16. MC seed determinism matters for this research path.
    The MC engine derives per-window seeds from `hash(label)`, and Python
    randomizes string hashes per process. The Bayesian alpha validator now
    installs a stable experiment-local label hash before calling MC so repeated
    screens are reproducible. Treat older unseeded N=80 MC reads as directional,
    not final.

17. Slot-cut removal and benign guards did not rescue the release path.
    N=240 stable-seed fail-window MC killed the no-slot version: 2024 worst DD
    still worsened by +6.53pp. Severe-DD-only also failed 2024 by +7.73pp. A
    panic-breadth-only variant passed 2020-crash and 2024, but only because it
    was effectively no-op: 2024 deltas were exactly zero and crash worst-DD
    delta was zero with negative return. That is not alpha.

18. A narrow put-overhang hedge is the first usable mined case.
    Deterministic replay of 170 rule/action pairs found 23 passable narrow
    cases. The best release-shaped family was not the broad Bayesian controller;
    it was a side-specific rule: when `open_put_n>=7` and
    `prev5_entry_avg_pnl_pct<=0.07378`, reduce put exposure only. The balanced
    action `put_overhang_pnl__puts70_cap5` scales put allocation to 70% and
    caps open puts at 5. Deterministic replay preserved 96.1% full-window trade
    throughput, improved 22-now DD by +22.85pp, and did not worsen 2020-crash
    or 2024 deterministic DD.

19. MC says the narrow case is a DD hedge, not yet a free alpha.
    Stable N=240 fail-window MC showed `put_overhang_pnl__puts70_cap5`
    improved 2024 worst DD by -1.04pp and was neutral in 2020-crash, but it
    reduced 2024 mean return materially. Long-window MC improved 22-now worst
    DD by -14.83pp and 5y worst DD by -2.89pp, with better mean DD in both
    windows. It slightly worsened 2025 worst DD by +0.15pp and has mixed
    compound-return behavior. This is usable as a research candidate for
    drawdown control, but not a release candidate without a return-drag guard.

20. v60 active-row validation did not rescue the put-overhang hedge.
    The v60 rebuild used active v60 score rows (`d4a3e9fec`) and current 30DTE
    portfolio constants, including the 14 total / 12 call cap. The smooth
    put-overhang branch tested soft exposure floors, open-put count/share, and
    weak recent execution. Best fail-window MC screen row
    `put_overhang_0076` improved 2020-crash worst DD by -0.36pp, but worsened
    2024 worst DD by +1.78pp. It also did not solve the release blocker because
    2024 stability failed before broader validation.

21. Re-entry boost is the strongest deterministic v60 lead, but not stable MC
    alpha yet. Deterministic replay ranked re-entry candidates highest, led by
    `reentry_0104` utility +91.65. Stable N=240 fail-window MC rejected that
    shape: 2020-crash worst DD worsened by +2.35pp and 2024 worst DD worsened
    by +6.64pp. Re-entry remains a plausible branch, but it needs an explicit
    crash/stress guard before another promotion-style screen.

22. Scarce-opportunity and Market Wave divergence branches are not release
    candidates from the v60 smooth screen. `scarce_opps_0006` failed 2024 worst
    DD by +6.64pp. `wave_divergence_0095` was effectively a no-op on worst DD
    in both fail windows and reduced 2024 mean return, so it is not alpha.
    The v60 run produced no fail-window survivors and skipped broader MC by
    design.

23. There is still alpha in the narrower v60 mined cases.
    The promotion-style smooth sweep was too broad for Market Wave divergence:
    gross throttles were inert. Re-running narrow side/action probes on active
    v60 found 51 deterministic usable rows. The strongest branch that survived
    fail-window MC was `wave_divergence_tp__skip_puts`: when recent entry TP
    rate is weak (`prev5_entry_tp_rate<=0.5133`) while Market Wave is
    constructive (`market_wave_signed>=3.72`), blocking new put entries was
    neutral in 2020-crash, neutral on 2024 worst DD, improved 2024 mean return,
    and improved 2025 worst DD by -1.53pp in broader MC.

24. The new Market Wave divergence branch is a mined lead, not a ship formula.
    Broad N=240 MC showed `wave_divergence_tp__skip_puts` did not worsen worst
    DD in 2020-crash, 2022, 2024, 22-now, or 5y, and improved 2025 worst DD.
    But it is a hard side skip, it was no-op in several windows, 5y mean return
    fell despite median return rising, and 2025 mean DD worsened by +0.41pp.
    Next search should soften it into a put exposure wave with scale/cap knobs
    and explicit mean-DD/return guards.

25. `put_overhang_pnl__puts_50` is a cleaner DD hedge than the old cap5 form,
    but still not free alpha. It preserved fail-window worst DD except for a
    small +0.32pp 2024 worst-DD drift, improved 22-now worst DD by -3.36pp and
    5y worst DD by -2.47pp, and improved long-window mean DD. The blocker is
    persistent return drag in 2024, 22-now, and 5y. Keep it as a hedge branch,
    not a standalone ship candidate.

26. The first `v59_extra_calls_guard` pass was diagnostic, not shippable. The
    all-window rebuild showed the prior overlap artifact had only populated the
    v59/v60 divergence through 2020, so later-window stability was partly a
    no-op. Do not ship a live dependency on v59 shadow comparison unless the
    system explicitly supports shadow scoring as a first-class production input.

27. v60-only calls identify marginal 70-74 boundary mechanics. Across 5y,
    v60-only calls had weaker quality than common v59/v60 calls: 53.4% win /
    44.4% bad / +0.044 mean sampled pnl vs 60.1% win / 37.0% bad / +0.120 mean
    sampled pnl. Membership is driven mostly by `wi_pre_boost`, `v60_stoch`,
    `v60_margin_over_70`, `v60_overall`, `wi_pre_regime`, SCW fields, and
    `wi_w_adj`. The v60-only cohort is useful as a label for marginal-call
    over-admission, not as the production feature.

28. The COVID crash failure is more day-state than exact-symbol toxicity. In
    2020-crash, v60-only calls were 31 signals with 51.6% win / 45.2% bad,
    while common calls were 45.7% win / 48.4% bad. The overlap guard worked
    because divergence days lined up with a dangerous market/book state, not
    because those exact v60-only symbols were uniquely bad.

29. The strongest new lead is a Market Structure Overflow Dampener for 70-74
    calls. The daily wave model separated top-20% risk days at 64.9% call-bad /
    32.4% call-win from non-top-20 days at 34.1% bad / 63.1% win. Top features
    were breadth above EMA200/EMA50, call/put primary share, recent call TP
    velocity, call demand, SPY distance from EMA200, market trend, McClellan,
    prior DD, and VIX. Signal-level screens showed `wave_q90__overflow_70_74`
    removed 2,226 calls that were 65.7% bad / 32.0% win, including 75
    crash-window calls with 61 bad saved vs 8 wins lost. The narrower
    `wave_q90__sector_crash_overflow` removed 102 calls at 79.4% bad / 14.7%
    win, including 38 crash-window calls with 33 bad saved vs 1 win lost.
    Stage this as a smooth score-boundary wave, not a hard threshold.

30. The hard Market Wave put skip can be made wave-shaped, but the first
    promotion-shaped pass is still no-ship. The smooth branch scales new put
    exposure with:

    ```text
    pressure = recent_weak * constructive_market_wave
               * (0.5 + 0.5 * put_overhang)
    put_scale = 1.0 - strength * pressure
    ```

    Top deterministic row `wave_put_divergence_0051` used `strength=0.55`,
    `floor_scale=0.45`, `weak_tp_floor=0.58`, `wave_threshold=0`, and
    `wave_width=24`. It was neutral on 2020-crash and 2024 deterministic DD
    and improved 2024 deterministic log return, which confirmed the hard
    `skip_puts` probe can be expressed as a continuous exposure wave.

31. MC found a real but unstable smooth Market Wave put-throttle surface.
    Stable N=240 fail-window MC showed `wave_put_divergence_0051` preserved
    2020-crash and 2024 worst DD, slightly improved 2024 mean DD (-0.02pp),
    and improved 2024 median return, but mean return was slightly lower. The
    old hard `put_overhang_pnl__puts70_cap5` benchmark again failed 2024 badly
    (+16.53pp worst DD), so the smooth wave fixed the cliff-specific DD
    blocker.

    Broader MC rejected `wave_put_divergence_0051`: it worsened 2025 worst DD
    by +3.11pp and worsened 22-now / 5y mean DD. A targeted 2025 screen found
    a better shape, `wave_put_divergence_0066` (`strength=0.65`,
    `floor_scale=0.35`, stricter `weak_tp_floor=0.50`, wider put-overhang
    trigger), which improved 2025 worst DD by -1.82pp with positive mean and
    median return. But that same candidate carried material 2024 return drag in
    the fail-window MC. Current judgment: alpha exists, but no single smooth
    put-wave shape has passed both 2024 return stability and 2025 / long-window
    DD stability.

32. The strict `0066`-neighborhood mined 2024-stable smooth survivors, but
    still did not produce a ship candidate. The strict deterministic sweep
    (`v60_wave_put_strict_sweep_20260520_120809`) ranked
    `wave_put_divergence_strict_0318` first, but top-ranked rows still carried
    2024 return drag under MC. A wider 2024-only screen found two return-stable
    survivors: `wave_put_divergence_strict_0298` and
    `wave_put_divergence_strict_0127`.

    Survivor N=240 MC rejected both. `strict_0298` fixed the original 2024
    blocker (+2.06Mpp mean return delta, +2.46Mpp median return delta, neutral
    worst DD), and stayed neutral in 2020-crash, but dragged 2025 return
    (-92.7kpp mean, -136.9kpp median) and worsened 2025 mean DD by +0.87pp.
    `strict_0127` also held 2020-crash and had positive 2024 mean return, but
    worsened 2025 worst DD by +3.11pp. Stop broad validation here: the smooth
    wave can fix the hard-cliff 2024 failure, but this family has not preserved
    2025 return/DD at the same time.

33. Path-diff says the `strict_0298` 2025 blocker is replacement/path churn,
    not direct removal of profitable puts. The N=240 tape run
    (`v60_strict0298_pathdiff_20260520_135554`) used identical stable labels
    for baseline and candidate. It reproduced the split: 2024 mean return
    +2.07Mpp with neutral worst DD, while 2025 mean return fell -56.3kpp and
    worst DD worsened +4.47pp under this path-diff label.

    The same-key shared put leg was positive in both windows: +$374.5B in 2024
    and +$1.17B in 2025. The 2025 loss came from secondary path effects:
    candidate-only added trades, removed/changed trades, and shared-call path
    drift. Inactive days after prior path perturbation accounted for about
    -$5.79B net trade PnL delta vs about -$0.97B on directly active scale
    days. The worst-DD episode shifted into late October / November 2025; the
    top 80 episode comparisons were all worse, with average top-episode delta
    +8.25pp and max +19.14pp. Month attribution was concentrated in 2025-12
    (-$4.76B net trade delta) and 2025-11 (-$1.69B).

    Next smooth-put-wave test should treat reduced put exposure as a gross
    exposure/cash-reserve cut, not free buying power to recycle into later
    fills. Do not keep sweeping only the side-scale surface; add a no-redeploy
    or post-throttle gross-budget guard and require 2025 episode stability.

34. The first MC validation of hard score-dampen proxies is no-ship. N=240 MC
    rejected the broad q90 overflow demotions because they fixed some crash
    exposure while creating 2024/2025 or long-window DD drift. The best
    precision lead, `wave_q90_sector_crash_overflow`, improved 2020-crash by
    -3.04pp worst DD, improved 5y by -2.47pp, preserved 2024/2025, and only
    drifted 22-now by +0.66pp, but failed 2022 by +3.39pp. The q85 sector
    variant also failed 2022 (+2.39pp) and 22-now (+1.10pp).

35. Future scoring work should split acute crash overflow from slow bear-market
    overflow. The q90 sector-crash filter fired on 2020-03-05/2020-03-06 and
    correctly removed very bad calls, but its 2022 firings worsened portfolio
    pathing despite poor signal-level outcomes. Add a 2022 guard such as fast
    breadth-collapse acceleration, VIX shock, crash-echo slope, or a smoother
    partial score penalty rather than hard-removing every matching 70-74 call.

36. Path-diff showed the broad score-dampen filters were over-attributing
    alpha. Outside COVID, q90-sector filters removed zero-allocation 70-74
    overflow rows rather than baseline-filled trades, so much of the 2022 /
    22-now / 5y movement is MC path/tie-break perturbation. Do not treat broad
    blocked-signal counts as portfolio alpha unless the blocked rows are filled
    trades or otherwise change real allocation.

37. The actual COVID crash drawdown lead is CT-call promotion under acute crash
    tape. Only 2 q90-sector source signals were CT-like (`trend <= 20`), both
    on 2020-03-05, and both were catastrophic with average sampled option PnL
    -70.8%. CT-only N=240 replay (`acute_q90_sector_ct_overflow`) blocked those
    2 signals, improved 2020-crash mean DD by -3.84pp and worst DD by -0.78pp,
    and produced 225 better seeds vs 5 worse seeds.

38. Next candidate should be a Stage 3 CT-call crash suppressor, not a scoring
    version bump: suppress or fade CT-call promotion when score is 70-74,
    trend <= CT_CALL_TREND_MAX, daily market-structure risk is q90, sector
    crash pressure >=0.55, broad EMA50 breadth <=25, and McClellan <=-40.
    This selector fires on zero 2022/2024/22-now/5y signals in the current
    artifact set, avoiding the slow-bear churn that killed broader filters, but
    source N is only 2, so validate on more crash/stress windows before ship.

39. The put-wave replacement-path blocker can be fixed by reserving throttled
    cash instead of recycling it. The reserve branch keeps the same smooth
    pressure shape:

    ```text
    pressure = recent_weak * constructive_market_wave
               * (0.5 + 0.5 * put_overhang)
    put_scale = max(floor_scale, 1.0 - strength * pressure)
    ```

    but when a put is throttled, the unspent premium is held as reserved cash
    until that trade's original exit date. This directly targets the
    `strict_0298` path-diff finding: the bad 2025 path came from freed buying
    power changing later fills, not from the directly scaled put leg.

40. `wave_put_divergence_reserve_0051` was the first experiment-local
    promotion-quality reserve candidate, but production-equivalent validation
    demoted it behind `0124`. Parameters: `strength=0.75`,
    `floor_scale=0.40`, `weak_tp_floor=0.539`, `weak_tp_width=0.08`,
    `open_put_trigger=13`, `open_put_width=10`,
    `open_put_share_trigger=0.75`, `open_put_share_width=0.15`,
    `wave_threshold=0`, `wave_width=16`.

    Evidence path:

    - Deterministic reserve sweep top candidates:
      `.codex/runs/v60_wave_put_reserve_sweep_20260520_150830/smooth_controller_top_candidates.csv`
    - N=240 fail-window MC:
      `.codex/runs/v60_wave_put_reserve_failwindow_mc_20260520_152929/wave_put_reserve_failwindow_mc_summary.md`
    - N=240 broad MC:
      `.codex/runs/v60_wave_put_reserve_broad_mc_20260520_155325/wave_put_reserve_broad_mc_summary.md`
    - N=500 x 8 final screen:
      `.codex/runs/v60_wave_put_reserve_0051_n500x8_20260520_182619/wave_put_reserve_0051_n500x8_summary.md`

    The N=500 x 8 final screen passed the Stage 3 hard gates in the
    experiment-local validator: worst-DD delta was 0.00pp on every window
    except 2025, where it improved by -2.03pp; collapse delta was 0.00
    everywhere; 5y mean and median return both improved. 2024, dip, 22-now,
    and 5y mean-return deltas were positive. The only soft flag is 22-now
    median-return drag despite positive mean return and neutral DD.

    Production-equivalent N=240 MC then exposed remaining return-drag
    fingerprints: 2025 worst DD improved by -2.49pp and 22-now by -5.27pp,
    but 2025 and 22-now log returns dragged, and 5y median log return was
    slightly negative with flat DD. That made `0051` useful evidence for the
    reserve mechanic, but no longer the best release shape.

41. `wave_put_divergence_reserve_0069` is not the ship shape despite being the
    best deterministic row and the cleanest explicit Market Wave divergence
    form. N=500 x 8 rejected it because 5y worst DD worsened by +7.90pp. This
    proves the reserve mechanic is necessary but not sufficient; parameter
    shape still matters.

42. `wave_put_divergence_reserve_0124` is now the promotion candidate after
    production-equivalent MC. Parameters: `strength=0.55`,
    `floor_scale=0.45`, `weak_tp_floor=0.539`, `weak_tp_width=0.10`,
    `open_put_trigger=9`, `open_put_width=8`,
    `open_put_share_trigger=0.65`, `open_put_share_width=0.25`,
    `wave_threshold=0`, `wave_width=6`.

    Evidence:

    - Experiment-local N=500 x 8:
      `.codex/runs/v60_wave_put_reserve_0124_n500x8_20260520_175629/wave_put_reserve_0124_n500x8_summary.md`
    - Production-equivalent N=240:
      `.codex/runs/v60_prod_reserve_0124_mc_20260520_225350/production_reserve_mc_summary.md`
    - Production-equivalent N=500:
      `.codex/runs/v60_prod_reserve_0124_n500_20260520_230521/production_reserve_mc_summary.md`

    N=500 production-equivalent MC passed the critical gates: `2020-crash`
    unchanged, `2024` worst DD unchanged with positive mean/median log return,
    `2025` worst DD improved by -4.93pp with positive mean/median log return,
    `22-now` worst DD improved by -5.27pp with positive mean/median log
    return, and 5y worst DD was unchanged with positive mean/median log return.
    Collapse stayed 0.00pp everywhere. This solves the prior 2024/2025
    return-drag blocker for the put-overhang hedge.

43. `ct_crash_suppress` is the clean CT-call crash suppressor candidate. The
    N=240 MC validation at
    `.codex/runs/v60_ct_crash_suppressor_mc_20260520_190800` touched exactly
    the two COVID CT-promoted source calls and was neutral elsewhere tested.
    It improved covid-peak worst DD by -8.91pp and mean DD by -7.98pp,
    improved 2020-crash worst DD by -2.68pp and mean DD by -3.70pp, improved
    2020-full worst DD by -2.32pp and mean DD by -3.92pp, and had flat deltas
    in 2022, 2024, 2025, 22-now, and 5y. `ct_crash_demote_low` also passed but
    provided weaker protection, so the next implementation-stage candidate is
    full CT-promotion suppression under acute crash tape, not a low-tier fade.

## Formula Direction

The next candidate should be a conditional exposure controller:

```text
recent_weak = prev5_entry_tp_rate low OR prev5_entry_avg_pnl_pct low
scarce_opps = call_demand low OR total_demand low
put_overhang = open_put_n high OR filled_put_n high OR open_put_share high
reentry_ok = call_demand healthy AND current_dd elevated
wave_divergence = market_wave constructive AND recent_weak

if recent_weak and put_overhang:
    reduce put exposure and cap puts

if recent_weak and scarce_opps:
    reduce total exposure and reduce max positions

if scarce_opps and weak breadth:
    reduce gross allocation even if cash is available

if wave_divergence:
    throttle gross exposure despite constructive market-wave state

if reentry_ok:
    permit a small call-side allocation recovery, capped by crash/stress state
```

## Validation Gates

- Build deterministic replay policies for throttle-only first.
- Validate re-entry boosts separately from throttles.
- Validate Market Wave divergence as its own throttle before combining it with
  put-overhang or sparse-opportunity rules.
- Require 2020-crash, 2022, 2024, 2025, 22-now, and 5y MC stability before
  promotion.
- Any future Bayesian seed must pass stable-seed MC in 2020-crash and 2024
  before broader Stage 3 validation.
- Active-row v60 evidence supersedes v59 discovery for promotion decisions.
  v59 artifacts remain discovery evidence only.
- Do not promote no-op panic-breadth guards as alpha; require positive DD or
  return utility after preserving 2024.
- Treat `put_overhang_pnl__puts70_cap5` as the current mined candidate: useful
  DD hedge, no ship until 2024 return drag is solved.
- Treat the v60 smooth controller run as no-ship: there were no 2020-crash /
  2024 fail-window survivors.
- Treat `wave_divergence_tp__skip_puts` as the current best alpha lead, but
  convert it from a hard skip into a smooth put-side exposure wave before any
  ship-style validation.
- Treat `wave_put_divergence_0066` as the next research seed only, not a ship
  candidate. It fixed the 2025 DD failure that killed `0051`, but it has not
  solved the 2024 return-drag blocker.
- Treat `wave_put_divergence_reserve_0124` as the current promotion candidate,
  not yet a production ship. It passed production-equivalent N=500 MC with the
  throttled-cash reserve mechanic, but shipping still requires a live
  portfolio-only implementation of reserved idle cash rather than an
  experiment CSV dependency.
- Do not combine this with scoring changes or an `ALGORITHM_VERSION` bump.
