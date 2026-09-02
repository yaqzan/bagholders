# P3.5 — Sentinel v74 Health Check (read-only)

Gameplan row: "Sentinel v74 health check (read-only from the pack + temporal API; only if
drift >5pp or collapse>0 -> one N=300 confirm; else stamp verified). Cheap staleness closure
on a profile nobody currently runs."

**Scope:** read-only. No commits, no queue submissions, no engine edits. One supplementary
read-only deterministic backtest call was run (see Method note) to get window-matched figures;
no Monte Carlo, no sweep, no writes to any table.

## Method

Two data sources were compared:

1. **"Expected" (documented) Sentinel numbers** — `algorithm_versions/portfolio_profiles.json`
   `sentinel_v70_85plus_exp30_1m.selection_metrics`, generated **2026-06-02** by
   `experiments/v69_portfolio_retune/profile_frontier.py`: an **N=250 seeded bounded-fill Monte
   Carlo** on the **v70** scoring substrate. Its window list (`WINDOWS = ['2020','2022','5y','10y']`)
   is defined in that script as `2020`=calendar-year 2020, `2022`=calendar-year 2022,
   `5y`=2021-01-01..2026-04-15, `10y`=2016-06-01..2026-04-15.

2. **"Observed" v74 numbers** — `.cache/algorithm_versions/v74/research_pack/stress_windows_sentinel.json`
   (generated 2026-07-06, active v74 `f9fb7b934`), produced by `algorithm_versions/research_pack.py`
   calling `backtest_cascade.run_cascade_backtest` — a **single deterministic path**, not a Monte
   Carlo. Its window catalog (`dotcom_crash_2000_2002`, `gfc_crash_2007_2009`, `deep_1995_now`,
   `covid_crash_2020`, `covid_cycle_2020_2021`, `year_2020..2026_ytd`, `2020_now`, `22_now`) does
   **not** include a "5y"/"10y" rolling window — that label is MC-script-specific, not part of the
   research-pack taxonomy. To get a window-matched comparison for those two metrics, one
   supplementary **read-only** `run_cascade_backtest` call was made replicating the exact
   `profile_frontier.py` "5y" (2021-01-01..2026-04-15) and "10y" (2016-06-01..2026-04-15) date
   bounds on the Sentinel profile / active v74 version (script:
   `experiments/sentinel_v74_check/sentinel_5y10y_check.py`, output alongside it as
   `experiments/sentinel_v74_check/sentinel_5y10y_result.json`). This is still a single
   deterministic path — it removes the *window-definition* mismatch but not the *engine*
   mismatch (see below).

**Methodology gap that matters more than the raw deltas:** the documented Sentinel baseline is
an MC point estimate from **2026-06-02**, which predates **all six** of the portfolio DD levers
now live and unconditionally inherited by every 30-DTE profile including Sentinel — none of
`RXDD_ENABLED` / `SVR_ENABLED` / `MWDD_ENABLED` / `TVDD_ENABLED` / `BDIV_ENABLED` /
`SPREAD_TILT_ENABLED` appear in `portfolio_profiles.PROFILE_STRATEGY_ATTRS` (the whitelist of
fields a profile can override), so Sentinel always runs with whatever `STRATEGY_30DTE` sets —
currently all six `=True` (`strategy_config.py:1315,1325,1340,1359,1371,1384`). Ship dates:
RXDD 2026-06-04, SVR/MWDD 06-05, TVDD 06-07, BDIV 06-11, SPREAD_TILT 06-15 — every one of them
postdates the 2026-06-02 Sentinel MC baseline. Each lever was independently validated as
DD-reducing (and mostly compound-neutral-or-positive, a Pareto) on Core/Apex at ship time. A
large favorable (lower) DD delta below is the **expected, already-priced-in consequence** of
this, not a surprise. In addition, a single deterministic path vs an N=250 stochastic
bounded-fill MC point estimate is not apples-to-apples on its own — per-window compound-return
noise between a single path and an N=300 MC sample is documented elsewhere in this repo as
1.6-1.8x (DD noise band ~+-5-8pp) — so some of the gap is expected sampling difference, layered
on top of the lever effect.

## Drift table

| Metric | Expected (v70, N=250 MC, 2026-06-02) | Observed (v74, single deterministic path) | Delta | Window match |
|---|---:|---:|---:|---|
| DD 2020 | 65.9% | 36.83% (`year_2020`, pack) | **-29.1pp** | Exact bounds (calendar 2020) |
| DD 2022 | 25.5% | 20.74% (`year_2022`, pack) | -4.8pp | Exact bounds (calendar 2022) |
| DD ~5y | 37.0% | 25.94% (2021-01-01..2026-04-15, supplementary run) | **-11.1pp** | Exact bounds match |
| DD ~10y | 66.2% | 36.83% (2016-06-01..2026-04-15, supplementary run) | **-29.4pp** | Exact bounds match |
| Return ~10y | +3,370.9% | +871.35% (same supplementary run) | **-2,499.5pp** | Exact bounds match |
| Collapse | 0.0% (of N=250 seeds) | 0 events (of 1 deterministic path; terminal equity > initial in all 16 windows checked) | n/a | Not a like-for-like test (see below) |

All four DD deltas move in the **safe** direction (lower observed DD than the stale baseline);
none regress. The 2022 delta (-4.8pp) is under the 5pp bar; the other three are well over it —
triggering the gameplan's literal ">5pp -> recommend the N=300 confirm" rule regardless of
direction. The return delta is large and negative (lower absolute compounding) — expected,
since every one of the six DD levers trades some upside for downside protection by
construction, and Sentinel's own documented purpose is preservation, not return-maximization
(the gameplan's DD-primary ship canon applies here too).

**Collapse check:** the observed side has no distributional collapse concept (one path either
survives or doesn't; it always did — terminal equity exceeded initial capital in every one of
the 14 pack windows plus the 2 supplementary windows, including the dot-com (2000-2002, +74.4%),
GFC (2007-2009, +60.7%), and full `deep_1995_now` (+3,039%, max DD 49.9%) windows). This is
reassuring but is **not** a check of the MC's `p_collapse_pct` metric, which is a property of
the random-seed distribution that a single path cannot speak to.

## Verdict

**Recommend the N=300 MC confirm on Sentinel/v74 — but flagged as a stale-baseline refresh, not
a regression alarm.** Rationale:
- Per the pre-registered rule, drift exceeds 5pp on 3 of 4 comparable DD metrics (and the return
  metric), so the mechanical trigger fires.
- However, every DD delta moves in the favorable direction, and the magnitude is fully
  consistent with (and most likely explained by) six already-shipped, already-validated DD
  levers that Sentinel silently started inheriting after the reference number was computed — not
  evidence that anything is broken.
- No collapse, no adverse signal, in any of the 16 windows read.
- The stale baseline itself (four scoring versions and six portfolio mechanisms out of date) is
  the real finding here: Sentinel has not had a dedicated Stage-3 N>=300 confirm since
  2026-06-02, even though nothing in the historical record suggests it needs one operationally
  (nobody currently runs it — live profile is Apex per `GET /api/portfolio/state`, default is
  Core). **Do not run the N=300 confirm as part of this task** — this is a recommendation only,
  sized at effort **S + 1 queued night**, matching P3.5's own effort budget, whenever Sentinel
  actually needs a fresh reference (e.g. before recommending it to a user, or alongside the
  already-planned P3.2 Core re-sweep for consistency).

## Artifacts

- `.cache/algorithm_versions/v74/research_pack/stress_windows_sentinel.json` (pack, read)
- `.cache/algorithm_versions/v74/research_pack/manifest.json` (pack, read)
- `algorithm_versions/portfolio_profiles.json` (documented baseline, read)
- `experiments/sentinel_v74_check/sentinel_5y10y_check.py` + `sentinel_5y10y_result.json`
  (supplementary read-only window-matched run, this session)
