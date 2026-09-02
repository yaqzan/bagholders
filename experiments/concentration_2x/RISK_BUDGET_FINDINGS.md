# Apex sprint — collapse-budget × time-to-2x frontier (TP/SL optimization)

**Objective (user, 2026-06-19; refined):** the Apex sprint is collapse-TOLERANT. Build the
**PARETO FRONTIER** in (P_collapse, median-days-to-2x) over the config space, and report the
*natural* regime breakpoints — "strategy A optimal up to X% collapse, strategy B from X–Y%, …"
— letting the DATA set the breakpoints (NOT pre-picked budgets; 1%/15% were just examples).
**Monotonicity is required & enforced by Pareto-dominance:** report only non-dominated configs;
sorted by collapse ascending, median MUST strictly decrease — any config that costs more collapse
without buying speed is DOMINATED and dropped. **SL is the primary fine-optimization axis (spend
compute here).** Each frontier point carries its fully-optimized TP/SL.

Metric: random monthly-start sampling (2y horizon), pooled across ~113 starts (drill) / ~55 (map).
median cal-days-to-2x (primary), P(2x within 2y), **P(collapse)** = final ≤ 20% of start, worst DD,
median compound. Harness: `experiments/concentration_2x/sweep.py` (`--tag`, env-driven TP/SL via
`TP_BASE_OV/TP_STRESS_OV/SL_BASE_OV/SL_STRESS_OV`, gross via `GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP`,
honest DTE via `CALENDAR_HOLD/NOMINAL_CAL_DTE/HOLD_CAL_DAYS`). Substrate: v74, calls-only, 5 DD
levers + dead-hold ON. Cell = concentration ladder `{n2_a50,n3_a33,n4_a25,n5_a20,n10_a10}`.

## Prior context (collapse≈0 work, already done)
- Shipped sprint = `flat_n4_a25` @ 30DTE / 50% gross / TP30 / SL−70 → **113d median, ~0 collapse**.
- TP/SL cross (#278-287) on that cell: **TP+30 / SL−85 beats TP+30/SL−70** at collapse-0
  (107d vs 113d, 0/56.5k collapse incl COVID, lower DD, higher compound). v70 "−85 COVID-collapse
  trap" did NOT reproduce on v74/n4/50%-gross. TP+35 bad; TP+20/25 not median-faster at collapse-0.
- 4-arm DTE×gross grid (TP30/SL70): full-deploy + shorter-DTE both speed the median ONLY by buying
  collapse, and they stack (15DTE/100%/n3 = fastest 51d @ **13.6% collapse**, DD>100%). At collapse=0
  the shipped 30DTE/n4/50% (~113d) was unbeaten. ⇒ the budget-relaxed region is exactly where the
  aggressive corners become *admissible* — that's what this frontier maps.

## Stage 1 — frontier map (16 jobs #290-305, queued 2026-06-19, off_market/high)
`DTE{30,15} × gross{50,100} × SL{50,70,85,100}` at TP=30, cells = ladder, N=200 / step-2 (~11k
paths/cell). TP fixed at 30 here (strongly favored; +35 proven bad) — refined in the drill.
Tags `rb_d{DTE}_g{G}_sl{SL}`. Result files `sweep_drill_rb_*.json`. Extractor: `frontier.py`.

STATUS: **DONE (80 configs).** Pareto frontier (monotonic, collapse asc → median strictly drops):

| collapse% | med d→2x | config | P2x% | DD% |
|---|---|---|---|---|
| 0.000 | 82.6 | 30d/50%/n3/SL−85 | 68 | 82 |
| 0.40 | 81.2 | 30d/100%/n4/SL−50 | 80 | 93 |
| 0.56 | 78.3 | 30d/100%/n4/SL−70 | 71 | 94 |
| 0.87 | 71.0 | 30d/50%/n2/SL−85 | 61 | 93 |
| 1.45 | 56.5 | 30d/50%/n2/SL−70 | 59 | 91 |
| 1.88 | 53.6 | 30d/100%/n3/SL−100 | 68 | 95 |
| 3.69 | 44.9 | 15d/100%/n4/SL−85 | 53 | 97 |
| 4.30 | 43.5 | 15d/100%/n4/SL−100 | 56 | 96 |
| 14.4 | 39.1 | 15d/100%/n3/SL−85 | 50 | 101 |
| 15.6 | 34.8 | 15d/100%/n3/SL−100 | 53 | 101 |
| 25.6 | 33.3 | 15d/100%/n2/SL−100 | 58 | 101 |

Regimes: **30 DTE owns 0–2%, the 30→15 DTE switch is ~2–4%, 15 DTE owns >4%.** SL is coarse here
(needs Stage 2). **KNEE: 4.3%→14.4% (43.5→39.1d)** — big collapse jump for ~5d; needs knee-fill
(intermediate gross/DTE at n3/n4). Low-end 0% points (esp 30d/50%/n3/SL−85, faster than the n4
anchor) need N=500 confirm (n3 collapsed 0.09% at SL−70/N500 earlier; SL−85 may fix it via HOLD).

## Stage 2 — FINE SL (21 jobs rb2_*, DONE) + Stage 2b knee-fill (10 jobs rb3_*, DONE)
Fine SL (every 5% 50-100 at the 3 frontier combos) + knee-fill (15d × gross{60,70,80,90} on n3/n4 +
21d/g100 probe). Fine SL reshaped the curve (tight SL−55 best at 0% end; wide SL−85/90/100 aggressive);
knee-fill cleanly populated 3-9% collapse. 21d probe dominated (off frontier).

### RESOLVED FRONTIER (N=200/step-2, 36 configs / 3 waves, 15 non-dominated, monotonic)
| collapse% | med d→2x | config |
|---|---|---|
| 0.00 | 78 | 30d/100%/n5/SL−55 |
| 0.03 | 72 | 30d/50%/n2/SL−90 |
| 0.9 | 61 | 30d/50%/n2/SL−60 |
| 1.5 | 51 | 30d/100%/n3/SL−95 |
| 3.2 | 45 | 15d/90%/n4/SL−85 |
| 4.2 | 42 | 15d/60%/n3/SL−85 |
| 5.7 | 36 | 15d/70%/n3/SL−100 (elbow) |
| 9.5 | 35 | 15d/80%/n3/SL−100 |
| 25.6 | 33 | 15d/100%/n2/SL−100 |
| 27.2 | 30 | 15d/100%/n2/SL−80 |

**SHAPE = headline: returns DIMINISH HARD.** 0%→~5% collapse nearly HALVES median (78→36d); past ~9%
it's FLAT (35→30d for 9%→27% collapse). **Economic elbow ≈ 4-6% collapse / 36-42d** — max speed per
unit collapse; beyond it = ruin for ~nothing. 30 DTE owns 0-1.5%; 15 DTE (gross-throttled) owns 3-9%.

## Stage 3 — N=500/step-1 confirm (6 regime points #337-342, RUNNING)
Full N incl COVID (reliable collapse): 30d/100%/n5/SL−55 (0% anchor), 30d/50%/n2/SL−90,
30d/100%/n3/SL−95, 15d/90%/n4/SL−85, 15d/70%/n3/SL−100 (elbow), 15d/100%/n2/SL−100 (fastest). TP=30.
⚠ N=200 0%-points MUST confirm at N=500.

### CONFIRMED FRONTIER (N=500/step-1, incl COVID, 56.5k paths) — DONE, monotonic
| collapse% | med d→2x | config | TP/SL |
|---|---|---|---|
| 0.014 | 97.1 | 30d/100%/n5 | +30/−55 |
| 0.018 | 72.5 | 30d/50%/n2 | +30/−90 |
| 1.08 | 58.0 | 30d/100%/n3 | +30/−95 |
| 2.41 | 44.9 | 15d/90%/n4 | +30/−85 |
| 5.98 | 42.0 | 15d/70%/n3 | +30/−100 |
| 25.11 | 34.8 | 15d/100%/n2 | +30/−100 |

N=200 was OPTIMISTIC at the safe/mid end (n5/SL55 78→97d, n3/SL95 51→58, elbow 36→42); shape held.
**DIMINISHING RETURNS confirmed + sharper: 0.02%→2.4% collapse = 72.5→45d (huge); 6%→25% = 42→35d
(ruin for a week). ELBOW ≈ 2.4% collapse / 45d = 15d/90%/n4/SL−85.** Safe pick 30d/50%/n2/SL−90 =
72.5d @ 0.018% is ~36% faster than shipped (113d) at negligible collapse — and 2-name concentration
is NOT a collapse trap when gross is throttled (50%) + SL wide (−90); the old "n2 trap" was
full-deploy/tight-SL. TP=30 throughout.

## Stage 4 — TP refine (6 jobs tpref_*, #343-348) — DONE. **TP+30 CONFIRMED optimal.**
TP∈{20,25} N=500 on 3 aggressive picks. Tighter TP is UNIFORMLY SLOWER to 2x: 15d/90/n4/SL85
TP30=45d vs TP25=55d vs TP20=75d; 15d/70/n3/SL100 TP30=42 vs 61 vs 57; 30d/100/n3/SL95 TP30=58 vs
67 vs 68. First-passage logic: tighter TP wins smaller → needs more doublings. (TP20 DOES win
P(2x)-ever 81% & compound +150% — better buy-and-hold knob, but loses the median-speed objective.)

## ★ FINAL FRONTIER (N=500 confirmed, TP+30, monotonic) — RESEARCH COMPLETE
| collapse | med d→2x | config |
|---|---|---|
| ~0.01% | 97 | 30d/100%/n5/SL−55 (abs-min collapse) |
| ~0.02% | 72.5 | 30d/50%/n2/SL−90 (SAFE; ~36% faster than shipped 113d) |
| ~1% | 58 | 30d/100%/n3/SL−95 |
| ~2.4% | 45 | 15d/90%/n4/SL−85 (★ ELBOW — best risk/reward) |
| ~6% | 42 | 15d/70%/n3/SL−100 |
| ~25% | 35 | 15d/100%/n2/SL−100 (max speed) |

Returns diminish HARD past the elbow (6→25% collapse = 42→35d).

## ADOPTED 2026-06-22 — Apex profile = the ELBOW (allocator-faithful)
Apex profile (`portfolio_profiles.json`) set to the elbow: gross/call cap **0.90**, **sl_base/sl_stress
−0.85**, **nominal_cal_dte 15 / hold_cal_days 13** (honest-calendar 15-DTE on the shared 30d engine),
tier 0.25 × 4 names (TP+30 inherited). Wired NEW profile-overridable keys into `portfolio_profiles.py`
(PROFILE_OPTION_ATTRS for TP/SL via `dataclasses.replace`; nominal_cal_dte/hold_cal_days top-level) +
`allocation_plan.py` (DTE label + hold from profile NOMINAL_CAL_DTE/HOLD_CAL_DAYS). Allocator now
renders Apex = **15 DTE / TP+30 / SL−85 / 90% gross / 4 names / hold 13** (verified); Core/Sentinel
unchanged. Gates: test_portfolio_profiles rc=0, drift-guard 653 rc=0. API restarted.
## 2026-06-22 — ENGINE REFACTOR + LIVE PORTFOLIO SWITCHED TO APEX
Made the honest-calendar tenor **per-call (profile-derived)** so Apex 15-DTE runs faithfully in the
SIM engines (was a process-global before):
- `backtest_cascade.py`: the 2 theta spots (`compute_outcome._theta_args`, `run_backtest` MTM) now read
  `calendar_hold`/`nominal_cal_dte` from cfg (default = module globals → Core bit-identical).
- `portfolio_profiles._derived_engine_pricing`: a DTE/SL-overriding profile emits the engine cfg
  (premium_mult = base·√(DTE/30), tp/sl sigma, net tp/sl, calendar_hold, nominal_cal_dte,
  hold_calendar_days). Apex → premium 1.287, sl_sigma 2.19, net_sl −0.865, 15-DTE. Core/Sentinel emit
  nothing (defaults preserved).
- `portfolio_engine.py`: entry path threaded (`dte`=nominal_cal_dte, `pm` from cfg premium_mult,
  tp/sl_price from cfg sigma) + `_mark_pnl` uses `p['dte']` for the MTM theta.
- `portfolio_param_manifest`: classified CALENDAR_HOLD/NOMINAL_CAL_DTE/HOLD_CAL_DAYS (EXCLUDED) +
  added the 2 new cfg keys to `_NON_KNOB_CFG_KEYS`.
Gates: drift 653 / profiles / manifest all rc=0. **Core parity PASS** (engine vs backtest bit-identical
on the live run: equity $41,696.79 both, MTM max delta $0.004) — the refactor is safe for Core.

**LIVE PORTFOLIO SWITCHED Core→Apex** (`PortfolioRun.profile='apex'` + sync, notifications off):
forward-transition (engine processes forward from last_processed_date; 57 closed trades FROZEN /
Core history preserved; 14 open ride to their Core barriers; NEW entries = Apex 15-DTE). Live verified:
`/api/portfolio/state` profile=apex, 14 open / 57 closed, equity ~$29.7k MTM (−17.3% — the open book is
currently underwater; that's the pre-existing MTM, not the switch). `/api/allocation/live?profile=apex`
= 15 DTE / SL−85 / 90% gross. Portfolio.js shows the "Apex · Sprint" profile badge (data-driven).
~2.4% collapse is the accepted risk for this opt-in sprint. Reversible: set profile back to 'core' + sync.
NOTE: the portfolio_engine-vs-backtest parity harness will intentionally NOT match post-transition
(engine preserves Core history; a fresh backtest re-runs all-Apex) — by design, not a regression.
Working-tree edits on `main` are UNCOMMITTED.

NOTHING ELSE SHIPPED — profile-design
study. Adopting any point needs profile-scoped wiring (TP/SL/DTE/gross are NOT currently
profile-overridable; only gross/positions are) OR shared-engine re-validation, since the points
move DTE+gross+positions+SL together. Records: this file + frontier.py + sweep_drill_{rb,rb2,rb3,rb4,tpref}_*.json.

## Stage 2 — FINE SL optimization at the frontier (after Stage 1)
From Stage 1, take the (DTE,gross,positions) combos that appear on the (collapse,median) Pareto
front and **finely sweep SL** there (≈ every 5%: {45,50,…,95,100}) at N=300/step-2, TP=30. This is
the compute spend on SL. Output: the SL-optimized median + collapse at each frontier aggression
level. (Add intermediate DTE via NOMINAL_CAL_DTE — e.g. 21 — or intermediate gross only if Stage 1
shows a large unfilled gap between the 30/15 or 50/100 frontier points.)

## Stage 3 — confirm + TP refine
Drill the non-dominated Stage-2 winners at N=500/step-1 (incl COVID, 56.5k paths) with TP∈{20,25,30}.
Extract the final Pareto set; enforce monotonicity (drop dominated). ITERATE Stage 2→3 if the
frontier has an unresolved knee (a big median jump between adjacent collapse levels) — keep drilling
SL/positions/DTE around the knee until the curve is smooth.

STATUS: pending Stage 1.

## FRONTIER RESULT (monotonic Pareto set — to be filled; breakpoints data-driven)
| regime | optimal config (DTE/gross/positions/TP/SL) | median days→2x | collapse% | worst DD | optimal over budget |
|---|---|---|---|---|---|
| safest | (prior anchor: 30DTE/50%/n4/TP30/SL85) | 107 | ~0% | 81% | [0, next) |
| … | TBD (data-driven breakpoints) | | | | |
| fastest | (likely 15DTE/100%/n2-3) | ~50? | ~13%? | >90% | [last, ∞) |

Nothing ships from this — it's a profile-design study (which Pareto point each risk appetite picks).
SL lives in the shared STRATEGY_30DTE engine, so any adopted point is either profile-scoped (wire
`sl`/`tp` into portfolio_profiles) or globally re-validated (Stage-3 Core check).
