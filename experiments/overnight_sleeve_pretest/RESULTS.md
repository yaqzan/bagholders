# Overnight-Equity-Sleeve Pre-Test (read-only)

Gameplan (Sec 4, alpha-frontier table): "Overnight equity sleeve | REFINE — one read-only
pre-test | Never tested in equity form. **Decisive test: do sleeve loss-nights co-cluster with
book DD?** Any kill -> WHAT-NOT-TO-DO entry." Also listed under P4 as a half-day read-only item.

**Scope:** read-only. No commits, no queue submissions, no engine edits. One read-only
deterministic `backtest_cascade` call was made to obtain the book's daily equity curve (a normal
read of MySQL price/score/option data, no writes) — same class of call already used throughout
`algorithm_versions/research_pack.py`.

**Prior art** (`experiments/intraday_overnight/FINDINGS.md`): the +17.5bps/night overnight
premium on our 75+ call-signal universe is real (t=+6.2) but every *option* capture form
(short-DTE overnight, WR7, day-of-week tilt) is decisively theta-null; the only harvestable form
is holding the **underlying stock** overnight — parked as an unbuilt "separate equity sleeve"
lead. That FINDINGS.md never tested whether the sleeve's own losses cluster with the main
book's drawdowns — the question this pre-test answers.

## Method

- **Sleeve cohort:** every `Score.overall >= 75` row, active version **v74**, `2020-01-01 ..
  2026-06-15` (`CALIBRATION_CUTOFF_DATE`, hard cap applied at the SQL layer). N=7,051 raw
  signals / 717 distinct symbols.
- **Sleeve-night return:** `next_trading_day.open / signal_date.close - 1` per (symbol, date)
  from `PriceHistory` (0 rows dropped for missing outcome). Aggregated to one **equal-weighted
  day-level sleeve return** per calendar date across that day's firing signals (mean; "hold
  signal names overnight," matching the gameplan's framing and the `equity_sleeve.py` prior-art
  construction) — N=1,354 distinct signal-fire dates. Mean +0.129%/night (std 1.56%), consistent
  with the prior art's independently-measured overnight drift.
- **Book DD proxy — Core profile chosen** (documented as `known-issues.md` CURRENT SHIP STATE's
  default/live-representative call book; Apex/Sentinel not tested — see Limitations). Daily
  equity curve from one read-only `backtest_cascade.run_cascade_backtest(74, cfg=core_profile,
  from_date=2020-01-01, to_date=2026-06-15)` call (1,652 daily points). Daily book P&L =
  `equity.pct_change()`; book drawdown = `(running_peak - equity) / running_peak`.
- **Panel:** inner-joined sleeve day-panel x book day-panel on date -> **N=1,354** matched
  trading days (2020-03-11 through 2026-06-15 for the active DD subsets; full panel spans
  2020-01-03..2026-06-15).
- **Statistics:** (a) conditional loss probabilities + lift + two-proportion z-test, sleeve-loss
  given book-DD-active vs not; (b) Pearson correlation of sleeve-night return vs book daily P&L,
  overall and DD-active-subset, with a **date-clustered SE** (calendar-month block bootstrap,
  4,000 resamples, cluster = year-month — resamples whole months with replacement so
  within-month serial correlation doesn't masquerade as precision). SKIP<30 enforced throughout
  (not triggered anywhere in this run — smallest subset N=185).
- **DD-active threshold — methodological note:** `book_dd_pct` on this book has **median 30.2%**
  over the window (Core is a levered, compounding book whose rolling peak resets often; being
  "10-15% off peak" turns out to be the *normal* state, not a stress signal — it captures
  78-86% of all days). The originally-planned 0.10/0.15 cuts are reported for completeness but
  **do not isolate genuine stress episodes**; a strict **0.40** tail cut was added
  (`pretest_extra_threshold.py`) once this was discovered — 383 days, concentrated
  2020-04-02..2024-04-19 (COVID trough + 2022 bear + the choppy 2023 stretch), which is the more
  decisive read.

## Results

| Comparison | N (active / inactive) | P(sleeve loss \| active) | P(sleeve loss \| not) | lift | two-prop z | r (clustered SE, 95% CI) |
|---|---:|---:|---:|---:|---:|---|
| All days (unconditional) | N=1,354 | sleeve loss rate 42.76% | — | — | — | **r=0.0161** (SE 0.0223, CI [-0.030, 0.057]) |
| DD >= 0.10 | 1,169 / 185 | 42.17% | 46.49% | 0.986 | -1.10 (n.s.) | r=0.0386 (SE 0.0276, CI [-0.019, 0.088]) |
| DD >= 0.15 | 1,056 / 298 | 41.67% | 46.64% | 0.974 | -1.53 (n.s.) | r=0.0066 (SE 0.0265, CI [-0.042, 0.061]) |
| **DD >= 0.40 (acute stress)** | **383 / 971** | **36.81%** | **45.11%** | **0.861** | **-2.78 (p~=0.005)** | **r=0.0682 (SE 0.0330, CI [0.001, 0.132])** |

**The decisive statistic** (acute-stress cut, the one that actually isolates real drawdown
episodes): sleeve loss rate is **significantly lower** during the book's worst drawdown days
(36.8% vs 45.1%, two-proportion z=-2.78) — the opposite of harmful co-clustering. The
within-regime day-to-day correlation is small and only barely excludes zero (r=0.068, 95% CI
[0.001, 0.132], N=383, 29 month-clusters) — a marginal, second-order co-movement, dwarfed by the
larger and more directly decisive loss-rate finding, and of a magnitude (<1% variance explained)
that does not read as a risk-concentration signal on its own.

At every threshold tested (0.10, 0.15, 0.40) and overall, confidence intervals for the
correlation straddle or barely clear zero, lift ratios sit at or below 1.0 (independence or
mild negative association), and two-proportion z-tests are non-significant-to-mildly-favorable.
**No threshold, including the strict acute-stress cut, shows sleeve losses co-clustering with
book drawdowns.** If anything, the point estimates trend toward a (weak, not risk-loaded)
protective pattern.

## Decisive-test verdict: NOT A KILL

The pre-registered decisive question — "do sleeve loss-nights co-cluster with book DD?" — is
answered **no** across all four cuts tested, most decisively at the threshold that actually
isolates real stress (DD>=0.40, z=-2.78 in the *favorable* direction). Per the gameplan's own
framing, a kill triggers a WHAT-NOT-TO-DO entry; **since no kill was triggered here, no such
entry is drafted.**

This is **not** a ship decision or a fundability finding — it clears exactly one pre-registered
gate (no correlated-loss risk with the main book) out of what a real build would need (its own
sizing/capacity/cost model, cross-checked against the prior-art `equity_sleeve.py` result that
an unlevered 5-day-hold variant of this same 75+ cohort already shows Sharpe 1.25 / Calmar 0.51
/ 0 collapse, standalone-SPY-beating, but still a smaller and separately-correlated-to-Apex
book). The sleeve remains correctly classified as **REFINE** — worth a real build-out
feasibility pass if the user wants a diversifier sleeve — not escalated by this result to
higher priority, and not killed.

## Limitations (read honestly)

- **Single book tested (Core).** Apex/Sentinel book-DD proxies were not checked; Core was
  chosen as the documented default/most-representative call book. A different book's DD
  calendar could read differently, though Core, Apex, and Sentinel share the same six DD-lever
  gates and broadly overlapping drawdown episodes (2020, 2022), so a materially different
  co-clustering verdict on another profile would be surprising but is not ruled out here.
- **`book_pnl_pct` is exactly 0.0 on 22.5% of matched days** (idle-book periods with no open
  Core positions) — a real data characteristic, not a bug, but it mechanically reduces the
  correlation test's power (a true small correlation would be harder to detect). The finding
  here is "no evidence of co-clustering," not "conclusively proven absent."
- **Equal-weighted, no-cost sleeve construction** (matches the gameplan's framing of this as a
  co-clustering pre-test, not a P&L feasibility study — the prior-art `equity_sleeve.py` already
  covers gross/net P&L and leverage feasibility separately).
- **Single correlation lens.** The clustered SE uses a month-block bootstrap as a pragmatic
  stand-in for a formal HAC/Newey-West estimator (no extra dependency required); directionally
  consistent with a naive i.i.d. SE check (not shown) at every threshold.

## Small infra note (not fixed here — read-only task)

`experiments/_holdout.py`'s `pre_cutoff_filter()` compares a polars `Date` column to
`CUTOFF.isoformat()` (a string), which raises `InvalidOperationError` under this box's polars
1.40 ("cannot compare date to a string value"). Worked around locally (native `pl.col("date") >
CUTOFF` assertion) rather than editing shared infra under a read-only task; worth a small fix
since every future holdout-respecting experiment with a native `Date`-typed column will hit the
same error.

## Artifacts

- `experiments/overnight_sleeve_pretest/pretest.py` — main analysis (signal pull, overnight-return
  join, Core equity curve, all-days + 0.10/0.15 threshold stats).
- `experiments/overnight_sleeve_pretest/pretest_extra_threshold.py` — supplementary 0.40
  acute-stress threshold (reuses `panel.parquet`, no DB re-pull).
- `experiments/overnight_sleeve_pretest/panel.parquet` — the merged day-level sleeve/book panel
  (date, sleeve_return, n_signals, book_pnl_pct, book_dd_pct), N=1,354.
- `experiments/overnight_sleeve_pretest/results.json` — full numeric results (meta, overall,
  by_dd_threshold for 0.10/0.15/0.40).
