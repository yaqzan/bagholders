# Fastest-to-2x exploration — FINDINGS (Tracks A/B/C)

Objective (user, 2026-06): **minimum time-to-2x ($50k→$100k), collapse-TOLERANT.**
A genuinely different objective than the shipped Apex (max compound s.t. collapse=0),
so the optimum differs. Portfolio-stage only — NO scoring change, NO version bump.
All on the live v74 Apex sleeve (calls-only, 5 DD levers + dead-hold ON).

Metric: across random historical start dates (38 quarterly starts × N, pooled),
P(reach 2x within 2y), median calendar days-to-2x among reachers, P(2x before a
50% DD), P(collapse ≤20% of start), worst DD. Harness: `experiments/concentration_2x/`
(load-once-per-window + per-window checkpoint/resume; bit-exact validated vs the
legacy subprocess path). Caveat: SPREAD_TILT-on in the working tree (relative
frontier unbiased; absolute numbers slightly affected).

## Track A — equity/regime WAVE timing aggression → NULL for speed
Three-arm MC (baseline vs market-regime-scaled vs equity-curve-scaled), N=100×8 windows:
- Market-regime aggression scaling **HURTS** every window (lower return, no DD benefit,
  slower 2x) — it mis-times; confirms "the jumps aren't predictable from the wave."
- Equity-curve drawdown scaling is a **DD-shaver** (−13..−17pp DD every window, collapse 0)
  that **halves return and slows time-to-2x 1.5–4×** — wrong trade for the speed objective.
→ Don't time aggression on a wave for speed. (Equity scaler survives only as a Sentinel
DD-minimization lever.)

## Track B — CONCENTRATION → the lever (N=500 confirmed, 19,000 paths/cell)
Grid: flat (N positions × alloc%), top-conviction-first ≥75, gross-capped at 100%, + cascade_ref.

| cell | gross | P(2x) | med days | P(collapse) | worst DD | med compound |
|---|---:|---:|---:|---:|---:|---:|
| **flat_n10_a10** | 100% | **79.7%** | 235 | **0%** | 76% | **+98%** |
| flat_n4_a25 | 100% | 70.5% | **129** | 0% | 83% | +39% |
| flat_n5_a20 | 100% | 70.2% | 148 | 0% | 81% | +36% |
| flat_n3_a33 | 99% | 68.7% | **101** | 0% | 86% | +34% |
| **cascade_ref (production)** | — | 62.5% | 275 | 0% | 71% | +92% |
| flat_n2_a50 | 100% | 58.2% | 71 | **1.9%** | 93% | −9% |
| flat_n1_a50 | 50% | 50.6% | 135 | **2.5%** | 91% | −13% |
| flat_n1_a10 | 10% | 4.5% | 370 | 0% | 51% | +14% |

Findings:
1. Concentration helps — but **full-deployment across 3–10 names**, NOT 1–2 big bets.
2. The production cascade is **beaten** on this objective (`flat_n10_a10` doubles more often,
   faster, same 0% collapse, better compound).
3. **Extreme concentration (1–2 names) is a trap**: breaks collapse=0 (1.9–2.5%), lower reach,
   negative median compound. Single-name books are the bottom of the table.
4. Why this is MORE aggressive than the compound-tuned Apex: time-to-2x is a first-passage —
   you EXIT at 2x before over-deployment's DD penalty compounds. Different objective → more
   aggression optimal, bounded by collapse=0 (≈3-name floor).

## Track C — concentration + equity-DD INSURANCE overlay → NULL for speed
AW equity overlay on the fast cells cuts DD ~14–18pp and restores collapse=0 on n2_a50,
but craters P(2x) by 11–36pp and slows the median (e.g. n10_a10 80%→43%). Trades the speed
away for DD the collapse-tolerant user doesn't need. No tandem; insurance is Sentinel-only.

## RECOMMENDATION
- **Default (best risk-adjusted fast-track): `flat_n10_a10`** — full book evenly across the 10
  top-conviction ≥75 names; doubles ~80% of starts in ~8mo at 0% collapse / 76% recoverable DD;
  strictly dominates the production cascade on the objective.
- **Max speed (accept ~85% recoverable DD): `flat_n3_a33` / `flat_n4_a25`** — double in
  ~3–4 months when they double, 0% collapse.
- **Avoid:** <3-name books (collapse-positive, often lose); the DD-insurance overlay (kills speed).
- Before LIVE wiring as a portfolio profile, confirm the chosen cell passes the standard
  Stage-3 gate (T1–T7, calendar 8-window N=500, DD-primary) — the time-to-2x frontier is a
  different lens than the shipped DD gate.

Still a leveraged-momentum sleeve, not new alpha: "fast-track" = a higher-variance dial on the
same substrate. ~20% of random 2y starts do NOT double (a 2022/23-type grind). Ring-fence +
salary contributions still the real engine.

## SHIPPED 2026-06-17 (portfolio-only, NO version bump; scoring stays v74 `f9fb7b934`)
Restructured the Apex/Core/Sentinel profiles in `algorithm_versions/portfolio_profiles.json` and
flipped the default Apex→Core:
- **Apex = `flat_n4_a25` fast-2x SPRINT** (flat 25% × 4 top-conviction 75+ names, MaxPos 4,
  overflow off; uncapped, puts off). **OPT-IN aggressive, NOT default.** Chosen over `flat_n3_a33`
  (faster but 86% DD + closer to the 3-name collapse floor) for a slightly safer 4-name floor at
  ~same median. **It is a STOP-AT-2x tool, not a held compounder** — held continuously it is
  negative-compound (Stage-3 held gate: ret_5y_med −37.1% / dd_5y 79.6% / collapse 0). The live
  engine has no auto-stop, so it is manual; an auto-rotate-at-2x engine feature was deferred.
- **Core = the former Apex (long-run held compounder), the NEW DEFAULT** (tiers 0.20/0.15/0.08/0.03,
  14 slots, 50% cap, uncapped; 5y +1,247.9% / dd 61.7% / collapse 0).
- **Sentinel** unchanged; the OLD balanced $2M-cap Core was removed.
- Default flip wired across `portfolio_profiles.py` / `portfolio_engine.py` / `api.py` /
  `src/pages/{Allocator,Backtest,Portfolio}.js`; live PortfolioRun migrated apex→core
  (config-neutral). Gates: drift-guard 653, registry 16/206, test_portfolio_profiles, profile-load
  smoke, /health all pass. Docs closed out (known-issues / trading-strategy / version-history / CLAUDE.md).
