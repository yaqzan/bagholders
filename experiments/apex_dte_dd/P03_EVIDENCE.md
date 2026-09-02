# P0.3 Evidence Package — staged 30-DTE options vs live 15-DTE elbow (FABLE, 2026-07-13)

**EVIDENCE ONLY — the apply decision is the user's (🔒 live ledger). Nothing applied.**
Queue task 610: N=500 × 12 windows (full default set ⊇ T3-canonical 8), v74 pinned, paired recipes
recorded in results_p03_evidence/summary.json (baseline = live Apex 15-DTE elbow; A = staged 30-DTE
n4; B = staged 30-DTE n10 Pareto).

## Held-window (standard Stage-3 T1-T7 lens), key rows

| window | live 15-DTE: ret/DD | A n4: ret/DD | B n10: ret/DD |
|---|---|---|---|
| 2020_crash | +8.3 / 71.4 | −54.4 / **89.6** | +4.5 / **57.1** |
| 2021 | +143.4 / 63.1 | −38.2 / 75.5 | +37.3 / 61.7 |
| 2022 | −57.6 / 86.2 | +29.8 / 65.1 | −26.5 / 58.1 |
| 22-now | −48.1 / 86.8 | +3.3 / 84.8 | +38.2 / 66.1 |
| 5y | +20.7 / 86.7 | −25.7 / 85.0 (coll 0.2%) | +202.9 / 66.5 |
| 10y | +326.7 / 87.3 | +345.6 / 85.3 | **+1,403.3 / 65.8** |

- **Option B (n10): T-gate clean sweep vs the live baseline.** Worst-DD improves in 12/12 windows
  (T5: zero breaches — every delta favorable, most −15 to −25pp); T4 5y DD −20.2pp; T6 collapse
  0.00 in all 12. Also dominates Option A on every held-window metric.
- **Option A (n4): lens-dependent.** Held-window lens: T5 breaches (+14.2pp 2020, +18.3pp
  2020_crash, +12.3pp 2021), 5y collapse 0.2% (inside the sprint's ~2.4% budget, outside the strict
  T6 floor), and severe held-return losses in melt-up windows (2021 −38 vs +143). Its case lives
  entirely on the FIRST-PASSAGE lens (stop-at-2x: P2x 72%, median ~113d, per
  FINDINGS.md/SHIP_HANDOFF.md N=500 monthly-roll) — the lens matching the sprint's designed use.

## Context accumulated this week (all committed, cited)
1. Deep crash screen: HELD-Apex(n4-style) through dot-com = 100% collapse (screen tier, survivor-
   optimistic). More names = the DD dial (concentration_2x lineage).
2. Pessimism certification: the sprint collapse budget is execution-conditional (TP-fill-miss 7%
   alone → 11% collapse on 22-now at the LIVE config; combined pessimism 26-39%).
3. Live ledger interim: −39.7% (06-01..07-10) vs its own frozen envelope p05 −19.4% (recorded
   non-adjudicated; marking-verification required before adjudication).
4. The 2x watchdog + pause/resume + fill-race fix shipped 2026-07-13 (discipline infrastructure).
5. Lifecycle MC (sprint→rotate-to-Core policy vs Core-only) N=100 screen queued (task 623).

## Architect's read (advisory only — decision is the user's)
The standard gate endorses **Option B (n10)**: it dominates the live elbow on every window at
N=500 with collapse=0 and does not depend on stop-at-2x discipline or friendly execution to be
safe — the two conditions this week's evidence showed are fragile. Option A remains a coherent
pure-sprint tool ONLY under strict watchdog-enforced stop-at-2x + the asymmetric-cost fill
assumptions. Whichever is chosen, application follows the ship-portfolio procedure (profile edit,
registry, temporal-refresh, parity, backend restart) — one green-light away; and the December H3
envelope re-freezes piecewise at the switch date per the pre-registration (no eval reason to delay).
