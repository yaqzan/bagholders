# Apex sprint DD/DTE optimization — STAGE HANDOFF

**Status: STAGED, not applied.** Portfolio-stage only (NO version bump, NO recalc). The recommended
change touches the user's LIVE sprint (real-money tracker), so it is **not auto-applied** — apply at
the user's green-light. Full evidence: `experiments/apex_dte_dd/FINDINGS.md`.

## TL;DR verdict
1. **Switch the Apex sprint from 15-DTE to 30-DTE** — a strict upgrade (at 4×25%: median compound
   **+4% → +50%**, worst DD **88% → 82%**, collapse **1.3% → 0%**, P(2x) **57% → 72%**; cost: ~1
   month slower to 2x). Confirmed N=100 → N=300 (full monthly roll incl. COVID/2022); N=500 finalist
   confirm (task #442) DONE — see Result 4 in FINDINGS.md. The standard 12-window Stage-3 T1-T7 gate
   (a different instrument — the fixed-window gate every other portfolio ship is measured against,
   vs #442's monthly-rolling-start harness) is now queued as task #610 (2026-07-13).
2. **The DD-vs-compound dial is the number of names, not a lever** — no RXDD/DD-soft re-tune is a
   Pareto win (arm 2 NULL). DD is reduced by diversifying.
3. **Two configs to choose between** (both crush the live 15-DTE sprint):

| option | config | median compound | worst DD | collapse | median days-to-2x |
|---|---|---:|---:|---:|---:|
| **A — drop-in** (keep 4 names) | 30-DTE **n4** (flat 25%×4) | +50% | 82% | 0% | ~113d |
| **B — Pareto-best** (compound+DD) | 30-DTE **n10** (flat 10%×10) | **+108%** | **76%** | 0% | ~191d |

Option B beats A on BOTH compound and DD (and beats the 14-name Core's +98% compound) — it only
costs ~10 weeks more patience. **A** = the minimal, same-name-count upgrade; **B** = the best for the
user's stated "max compounding + min DD" goal.

> Absolute numbers are SPREAD_TILT-on (current live config); the apex profile's stored
> `selection_metrics` (44.9d/95.7%DD/2.4%coll) predate SPREAD_TILT, so don't compare them directly —
> the consistent within-this-run 30-vs-15 comparison is the basis.

---

## How to apply (one of the two)

Edit `algorithm_versions/portfolio_profiles.json`, the **apex** profile `params` block (lines ~24-52).

### Option A — 30-DTE, keep 4×25% (minimal, recommended drop-in)
Change **4 fields** (premium_mult & sigma barriers auto-derive from `nominal_cal_dte`+`sl_base`):
```
"nominal_cal_dte": 15  ->  30
"hold_cal_days":   13  ->  27
"sl_base":      -0.85  ->  -0.70
"sl_stress":    -0.85  ->  -0.70
```
(tier_* stay 0.25, max_positions 4, call_max 4, gross_cap 0.9 — unchanged.)

### Option B — 30-DTE, n10 flat 10% (Pareto-best compound+DD)
The 4 fields above, PLUS the sizing:
```
"tier_ultra": 0.25 -> 0.10 ,  "tier_top": 0.25 -> 0.10
"tier_mid":   0.25 -> 0.10 ,  "tier_low": 0.25 -> 0.10
"max_positions": 4 -> 10   ,  "call_max": 4 -> 10
"gross_cap":   0.9 -> 1.0  ,  "call_cap": 0.9 -> 1.0
```

### After editing (either option)
```
python tests/test_strategy_config_drift.py          # should stay green (profile-only)
python tests/test_mechanism_registry.py             # green
# refresh the dashboard/assessment surfaces for the changed profile:
trader queue submit --priority normal --db light --cpu 4 --dedup apex-temporal \
  --reason "apex 30DTE profile" -- python trader.py temporal-refresh --profiles all
# rebuild the VersionCompare/research-pack windows if the profile feeds them (optional)
```
Then **restart the trader-api backend** (it caches the profile at import; G10). The live Portfolio
engine re-fingerprints the profile on the next `trader update` and adopts the new DTE for **new**
entries (existing 15-DTE positions ride out — no disruption).

**Revert:** restore the 4 (or 8) fields. Fully reversible.

---

## What was tested / not
- **Tested + confirmed:** 15-vs-30 DTE × concentration {n2,n3,n4,n5,n10,cascade} × 3 DTE arms (N=100
  step-2, ~57 windows); N=300 full-roll confirm of n4/n5/n10; N=500 finalist confirm (n4,n10) DONE
  (#442, see Result 4). Arm-2 lever re-tune (RXDD depth, DD-soft-band) on 30-DTE n4 = NULL.
- **QUEUED 2026-07-13 (task #610, evidence-only — applies nothing):** the standard 12-window Stage-3
  T1-T7 gate (the concentration_2x metric pools COVID/2022 *starts* across the monthly roll, which is
  a broader DD read, but a literal T1-T7 run on the 30-DTE-n4/n10 profile formalizes the ship-gate
  before a permanent change) — `experiments/apex_dte_dd/run_p03_evidence.py`, N=500, baseline live
  15-DTE Apex vs staged 30-DTE n4/n10, no verdict rendered (P0.3 stays a user-locked 🔒 decision).
  **NOT done (still out of scope / future):** a 45/60-DTE arm (the 30>>15 premium-cushion trend may
  continue — cheap follow-up; see the separate P3.3 probe, `run_dte45_probe.py`, task #609).
- **VIX-trend / BB-middle hypothesis:** tested NULL pre-run (reproduces VXMD); the validated VIX→DD
  signal (level band / RXDD) is already shipped and, per arm 2, already saturated for the sprint.
