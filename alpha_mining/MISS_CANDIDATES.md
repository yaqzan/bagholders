# Miss Candidates

Last verified: 2026-06-09 (entire table re-tested on the live v70 apex15 barrier — see STATUS banner).

Purpose: compact backlog of high-count harmful miss groupings from the v60 miss-analysis sweep, updated for the v63 BB Location Taper sidecar package. Use this file when choosing the next Stage 1 miss-led research path. Keep `NEW_LEADS.md` for the broader ranked research backlog; keep this file focused on score-miss candidates and the grouping evidence behind them.

**This file is history-only (retired as a work queue — see STATUS banner below).** It is not maintained as a live lead surface. For the current ranked research backlog see `alpha_mining/NEW_LEADS.md`; for the current live strategic frontier see `.claude/docs/gameplan.md` (sections 2b/4/6b).

## ⚠ STATUS 2026-06-09 — ENTIRE TABLE INVALIDATED ON THE LIVE BARRIER (retired as an action queue)

This table was mined by the v60 sweep on a **generic** barrier. The live strategy is the **v70 Apex
30-DTE HOLD** (apex15 = TP+30% / SL−70% / day-15). Re-tested every priority family on apex15 over the
v70 10y ledger (`experiments/missretest_apex15/FINDINGS.md`): **none clears z≤−3 in the harmful
direction.** Families that read z+3 to +4.7 on the generic barrier flatten to |z|<3 on the funded HOLD
barrier, and several REVERSE sign (continuation #2, trend-mid/macd-mid #5 become cohort-*better*). A
fresh open-ended mine on apex15 (42 cohorts) found **0 real miss concentrations** — misses are vol-PATH
(82% of losers never approached TP; loser median MFE 0.62σ vs the 1.092σ target), not a score-feature
cohort. The directional technical score has ~no edge on the 15-day option outcome. **The miss-led
Stage-1 damp queue below is closed for the live strategy** (generic-vs-option-trap artifacts); kept for
history/reproducibility. Retry only with a NON-directional feature (option skew / realized-vol path),
never a re-grade of the technical score.

## Current State

- Active scoring is now **v70 (`c70d16d22`)**; this table was mined on v60 (`d4a3e9fec`) and is
  superseded for action purposes per the STATUS banner above.
- v63 BBLT sidecar package exists at commit `7b263922f`; it was assessed without flipping `ALGORITHM_VERSION`.
- v63 BBLT changed `12,009` sidecar rows: `5,763` CALL-side and `6,246` PUT-side.
- 10y combined `75+ / <25` WR15 moved from v60 `75.21%` at `N=14,806` to v63 `76.23%` at `N=12,606`.
- 5y combined `75+ / <25` WR15 moved from v60 `77.43%` at `N=7,651` to v63 `77.89%` at `N=6,580`.
- Treat BB-location miss groups as addressed by BBLT for research priority purposes, but refresh after the full-lookback market-data repair completes before any version toggle.

## Source Artifacts

- Miss analysis report: `C:\Development\Trader\.codex\runs\miss_analysis_20200101_20260515_20260523_084320\REPORT.md`
- Miss grouping CSV: `C:\Development\Trader\.codex\runs\miss_analysis_20200101_20260515_20260523_084320\miss_groupings_by_count.csv`
- v63 sidecar report: `C:\Development\Trader_v63_score_research_20260524\.codex\runs\v63_sidecar_ship_package_20260525_012626\REPORT.md`
- v63 research pack: `C:\Development\Trader_v63_score_research_20260524\.cache\algorithm_versions\v63\research_pack\manifest.json`

## Highest Count Harmful Miss Groupings To Prioritize

Sorted by practical priority after deduping overlapping rows and marking BBLT-covered cohorts. Counts are from the original v60 2020-01-01 to 2026-05-15 miss sweep, filtered to lift `>= 1.08`.

| priority | candidate family | source groupings | misses | N | miss% | lift | z | status | next probe |
|---:|---|---|---:|---:|---:|---:|---:|---|---|
| 1 | Marginal CALL boundary weakness | `score_bin=75-79` on CALL 75+ / TRADEABLE | 1,242 | 4,677 | 26.6% | 1.11 | +4.38 | Open. Highest count open miss surface. | Build active-baseline residual controller: damp weak 75-79 incumbents and separately test 70-74 admission, with 80+ preservation as a hard gate. |
| 2 | Continuation echo over-admission | `continuation_echo_lifted`, `b_cont_lift=on` | 1,137 | 4,345 | 26.2% | 1.10 | +3.62 | Open. Duplicate rows, one mechanism. | Split continuation lift into true continuation, late exhaustion, and crowded trend states; test a smooth lift fade near the tradeable boundary. |
| 3 | Unknown/stress regime residual | `b_regime=unk`, `b_regime=STRESS` across TRADEABLE, CALL, PUT | 698 | 2,572 | 27.1% | 1.14 | +3.94 | Open. High-count state-quality issue. | Fit side-specific regime/breadth residual wave; validate WR15 first, then portfolio DD separately. |
| 4 | Stoch/SCW phase leakage | `call_stoch_low_score_proxy`, `call_scw_dampened_but_tradeable`, `b_scw_dampen=on` | 649 | 2,497 | 26.0% | 1.09 | +2.54 | Open. SCW already helps, but leaves bad calls tradeable. | Mine SCW scalar, raw stoch, extension index, and final-score distance; test a smooth phase-width taper, not a hard cliff. |
| 5 | Component agreement gap | `b_trend=mid`, `b_macd=mid`, side-specific mid-trend rows | 466 | 1,801 | 25.9% | 1.09 | +2.04 | Open. Likely final score is too confident when components disagree. | Build an agreement/conflict score from trend, MACD, RSI, BB, stoch, and weekly context; use a small taper around 75-84 and 16-25. |
| 6 | Wide-sigma fragility | `b_sigma=vwide`, `b_sigma=wide` across TRADEABLE/CALL/PUT | 423 | 1,501 | 28.2% | 1.18 | +3.96 | Open. High miss rate, lower count, likely volatility-shape issue. | Test volatility-normalized score confidence, with side-specific caps so high-vol crash puts are not over-dampened. |
| 7 | High volume authority false confidence | `b_vmag=hi`, `daily_volume_authority_wave_adjusted`, `b_daily_volume_authority_wave_delta=on` | 389 | 1,390 | 28.0% | 1.17 | +3.64 | Open. Participation can mark blowoff/exhaustion, not just conviction. | Bucket authority by side, breadth, score distance, and next-day gap behavior; test authority as a convex quality term. |
| 8 | Ichimoku put residual | `PUT <=25 b_ich_put_lift=off`, `PUT <=20 b_ich_put_lift=off` | 346 | 1,370 | 25.3% | 1.11 | +2.16 | Open. PUT side still has Kijun/context residual. | Audit PUT misses by Kijun percent, BB location, trend mid, and final score band before changing the ICH lift curve. |
| 9 | Top-tier earnings pocket | `CALL 80+ b_earnings=off` | 175 | 865 | 20.2% | 1.11 | +1.53 | Lower priority. Smaller N but top-tier surface. | Separate no-earnings continuation from hidden event/proximity artifacts; only ship if 80+ WR15 improves without losing useful N. |
| 10 | Weekly momentum drag residue | `call_weekly_momentum_drag` | 156 | 605 | 25.8% | 1.08 | +1.13 | Lower priority. Already partly represented in weekly guard work. | Revisit only after active-boundary and SCW/continuation probes; require N-preserving weekly shape. |

## BBLT-Covered Rows

These were high-count harmful rows in the original table, but should no longer rank as open miss candidates unless a post-BBLT refresh shows residual alpha.

| original rank | grouping | misses | N | miss% | lift | z | current treatment |
|---:|---|---:|---:|---:|---:|---:|---|
| 7 | `CALL 75+ b_bb=mid` | 587 | 2,048 | 28.7% | 1.17 | +4.31 | Covered by BBLT CALL taper: high score but mid BB location was damped toward neutral. |
| 8 | `PUT <=25 b_bb=lo` | 543 | 2,117 | 25.6% | 1.12 | +3.11 | Covered by BBLT PUT taper: low score already at low BB location was damped toward neutral. |
| 26 | `PUT <=20 b_bb=lo` | 216 | 900 | 24.0% | 1.14 | +2.16 | Covered by the same PUT-side BBLT mechanism. |

## Raw Harmful Rows

Reference rows from the original harmful table, kept for reproducibility. Duplicates are intentional; the priority table above is the deduped action queue.

| raw rank | type | cohort | pattern | misses | N | miss% | lift | z |
|---:|---|---|---|---:|---:|---:|---:|---:|
| 1 | feature | CALL 75+ | `score_bin=75-79` | 1,242 | 4,677 | 26.6% | 1.08 | +3.17 |
| 2 | feature | TRADEABLE | `score_bin=75-79` | 1,242 | 4,677 | 26.6% | 1.11 | +4.38 |
| 3 | root_label | TRADEABLE | `continuation_echo_lifted` | 1,137 | 4,345 | 26.2% | 1.10 | +3.62 |
| 4 | feature | TRADEABLE | `b_cont_lift=on` | 1,137 | 4,345 | 26.2% | 1.10 | +3.62 |
| 5 | feature | TRADEABLE | `b_regime=unk` | 698 | 2,572 | 27.1% | 1.14 | +3.94 |
| 6 | root_label | TRADEABLE | `call_stoch_low_score_proxy` | 649 | 2,497 | 26.0% | 1.09 | +2.54 |
| 7 | feature | CALL 75+ | `b_bb=mid` | 587 | 2,048 | 28.7% | 1.17 | +4.31 |
| 8 | feature | PUT <=25 | `b_bb=lo` | 543 | 2,117 | 25.6% | 1.12 | +3.11 |
| 9 | feature | CALL 75+ | `b_regime=unk` | 480 | 1,772 | 27.1% | 1.10 | +2.47 |
| 10 | feature | TRADEABLE | `b_trend=mid` | 466 | 1,801 | 25.9% | 1.09 | +2.04 |
| 11 | feature | TRADEABLE | `b_sigma=vwide` | 423 | 1,501 | 28.2% | 1.18 | +3.96 |
| 12 | feature | TRADEABLE | `b_vmag=hi` | 389 | 1,390 | 28.0% | 1.17 | +3.64 |
| 13 | root_label | TRADEABLE | `daily_volume_authority_wave_adjusted` | 388 | 1,497 | 25.9% | 1.09 | +1.90 |
| 14 | feature | TRADEABLE | `b_daily_volume_authority_wave_delta=on` | 388 | 1,497 | 25.9% | 1.09 | +1.90 |
| 15 | feature | CALL 75+ | `b_macd=mid` | 367 | 1,377 | 26.7% | 1.09 | +1.80 |
| 16 | feature | CALL 75+ | `b_sigma=wide` | 362 | 1,306 | 27.7% | 1.13 | +2.65 |
| 17 | feature | PUT <=25 | `b_ich_put_lift=off` | 346 | 1,370 | 25.3% | 1.11 | +2.16 |
| 18 | feature | PUT <=25 | `b_trend=mid` | 307 | 1,154 | 26.6% | 1.17 | +3.07 |
| 19 | feature | CALL 75+ | `b_vmag=hi` | 300 | 1,028 | 29.2% | 1.19 | +3.44 |
| 20 | root_label | CALL 75+ | `call_scw_dampened_but_tradeable` | 297 | 1,023 | 29.0% | 1.18 | +3.32 |
| 21 | feature | CALL 75+ | `b_scw_dampen=on` | 297 | 1,023 | 29.0% | 1.18 | +3.32 |
| 22 | root_label | TRADEABLE | `call_scw_dampened_but_tradeable` | 297 | 1,023 | 29.0% | 1.22 | +3.91 |
| 23 | feature | TRADEABLE | `b_scw_dampen=on` | 297 | 1,023 | 29.0% | 1.22 | +3.91 |
| 24 | feature | PUT <=25 | `b_sigma=vwide` | 235 | 933 | 25.2% | 1.10 | +1.73 |
| 25 | feature | PUT <=25 | `b_regime=unk` | 218 | 800 | 27.3% | 1.19 | +2.99 |
| 26 | feature | PUT <=20 | `b_bb=lo` | 216 | 900 | 24.0% | 1.14 | +2.16 |
| 27 | feature | CALL 75+ | `b_sigma=vwide` | 188 | 568 | 33.1% | 1.35 | +4.73 |
| 28 | feature | CALL 80+ | `b_earnings=off` | 175 | 865 | 20.2% | 1.11 | +1.53 |
| 29 | feature | TRADEABLE | `b_regime=STRESS` | 171 | 630 | 27.1% | 1.14 | +1.95 |
| 30 | feature | CALL 80+ | `b_vsig=CONVICTION` | 159 | 788 | 20.2% | 1.11 | +1.42 |

## Update Rule

When a new miss-led scoring candidate ships, is promoted, or is killed:

1. Re-run or refresh the relevant miss grouping surface against the current active score version or explicit candidate version.
2. Move directly addressed rows into `BBLT-Covered Rows` or a new resolved section with the ship artifact.
3. Keep the priority table deduped by mechanism, not raw row count.
4. Preserve the raw historical rows unless a full refreshed table supersedes this source sweep.
