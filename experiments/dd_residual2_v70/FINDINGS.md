# DQT — Drawdown Quality Tilt (v70 Apex, 2026-06-08 overnight /research)

Goal: find a **5th** Stage-3 call-alloc DD lever on the live v70 Apex sleeve, AFTER RXDD (VIX),
SVR (semivol/skew), MWDD (McClellan), TVDD (TRIN) all shipped (the last three in the prior 3 days).

## Part 1 — the regime/breadth/volume DD-sizing well is DRY (confirmed)

The TVDD FINDINGS (shipped 2026-06-07) predicted "the regime-axis DD-lever well is largely dry after
3 levers." This run **confirms it for the next layer of candidate axes** the prior agent never
explicitly tested. Screen: `mine2.py` on the live full-lever tape (RXDD+SVR+MWDD), DD-active subset
(dd>=0.13), single-axis EV + dd_conc scan + the decisive **all-shipped-levers-off orthogonal slice**
(vix<20 AND |mcc|>30 AND breadth>=40).

| fresh axis | full-tape signal | orthogonal-slice verdict | conclusion |
|---|---|---|---|
| **NH/NL** (participation quality) | broad-highs (nhnl≥55) low-EV (+0.014..+0.029), conc 1.4-1.5 | **FAILS** — nhnl5 mpnl +0.073 (NOT low-EV), conc ~1.0; the "broad highs = low-EV" was a re-label of the euphoria regime (low-VIX/+McClellan/high-breadth) | **dead** (captured) |
| **breadth-velocity** (Δbreadth/5d) | flat band sharp low-EV (−0.035; slice −0.189) | survives slice BUT **corr 0.724 with McClellan-velocity, 0.574 with level** — same family as MWDD; flat-within-\|mcc\|>30 is only 5.2% of days (tiny cohort) | **dead** (McClellan-redundant, like A-D-slope per G22) |
| **pe50** (%above EMA50) | high-pe50 low-EV but conc 0.48 (diffuse) | mid band low-EV/non-monotonic; breadth-correlated | **dead** (diffuse/captured) |
| **VIX-velocity** | falling=low-EV/low-conc, rising=high-EV/high-conc | **anti-aligned + noisy** in slice (vv5 rising mpnl +0.042 conc 3.56) | **dead** (G22 confirmed: poor standalone, anti-aligned) |
| concurrency / entry_dd / A-D-slope | captured/diffuse | captured | dead (per TVDD FINDINGS) |

So no genuinely-orthogonal **regime/breadth/volume** axis remains. The 5 shipped levers
(RXDD/SVR/MWDD/TVDD/F3F) have captured that structure.

## Part 2 — the one orthogonal residual: per-signal CONVICTION TIER

The single axis the screen surfaced that is **fully orthogonal** to all 4 regime levers (they key on
market context: VIX/skew/McClellan/TRIN; this keys on the per-signal cascade tier) and has a real
low-EV + DD-concentrated cohort is the **tier**:

DD-active (dd>=0.13) mean option pnl + dd-share BY TIER (base mpnl ~+0.05):

| tier | mean opt pnl | dd_conc | dd_share | verdict |
|---|---:|---:|---:|---|
| ultra (95+) | +0.087 | 1.07 | 0.030 | high-EV, leave alone |
| top (85-94) | +0.121 | 1.15 | 0.039 | high-EV, leave alone |
| mid (80-84) | +0.087 | 2.96 | 0.117 | high-EV (highest conc but +EV), leave alone |
| **low (75-79)** | **+0.044** | **2.06** | **0.438** | **LOW-EV + 44% of all DD-dollars → target** |
| overflow (70-74) | +0.047 | 0.55 | 0.376 | low-EV but diffuse (low conc); hydration engine |

So the **LOW (75-79) tier is the single biggest DD contributor (44% of drawdown-dollars) at low EV**,
while the high-conviction tiers are high-EV but only 19% of DD-dollars combined. Mechanism: "during
drawdown, be more selective — contract the WEAKEST tradable signals, keep the conviction tiers full."
Surgical vs the tier-BLIND DD_SOFT_BAND (which contracts all tiers uniformly).

**Crash-artifact guard:** the low tier is low-EV in stress (2020_crash −0.079, 2022 −0.067, 2023
−0.003) but HIGH-EV in bull (2024 +0.241, dip +0.178) — the classic crash-artifact shape. The
**DD-gate is the mitigation** (same as RXDD/MWDD/TVDD): DQT only acts while running dd >= DQT_DD_MIN,
so it fires often in bear drawdowns (low tier genuinely low-EV) and rarely in bull tape (book seldom
drawn down). **Collapse-safe by construction** — DQT only ever REDUCES alloc, so smaller positions =>
less DD AND less collapse risk; no VIX-panic exclusion needed.

## Mechanism (DQT)

`monte_carlo.py` / `backtest_cascade.py`, calls-only, applied at the alloc-frac product:
```
dqt_scale = 1 − ramp · (1 − floor)           # ramp = clip((dd − DD_MIN)/(DD_FULL − DD_MIN), 0, 1)
```
for tier in {low (floor=DQT_FLOOR), overflow (floor=DQT_OVF_FLOOR=1.0 → off by default)}; high
tiers untouched. Linear gradient 1.0 → floor over [DQT_DD_MIN, DQT_DD_FULL]. Knobs DQT_ENABLED /
DQT_DD_MIN / DQT_DD_FULL / DQT_FLOOR / DQT_OVF_FLOOR, env-overridable, default OFF
(=> scale 1.0 => baseline byte-identical).

## Verify (N=200, task #82) — PASS

- **NOOP** (bare baseline − DQT_ENABLED=0) = (0.0, 0.0) on 2022 & 2024 → true byte-identical no-op when off.
- **Fires correctly**: 2022 (bear, DD-heavy) WorstDD 57.0 → 51.6 (**−5.4pp**) AND compound 158→182
  (**+15%**) — Pareto direction; 2024 (bull, low-DD) DD 43.0 → 42.6 (−0.4pp), compound −0.9% (~no-op,
  DD-gate working).

## Sweep B → C → D — DQT is a NULL (velocity-coupled, no clean Pareto)

**Phase B (LHS-16, N=100 × 6 windows incl 2020_crash, task #83):** markedly weaker than the prior 4
levers (TVDD Phase B had ~14/16 clean). Only **2 of 16** passed the clean filter (collapse=0,
worstComp ≥ −0.15). The standout, c14 (FLOOR=0.80, OVF off, DD_MIN 0.147, DD_FULL 0.467), gave 5y
WorstDD −1.4pp at flat compound (−0.002) — but the DD reduction was concentrated in **2022 (−2.4pp)**
and **5y (−1.4pp, 5y med 626k→597k = −5%, well inside N=100 noise)**; 2023/2025/dip flat-to-negative.
The deeper configs (c01/c05/c10: ddFoc +1.9, dd5y up to +3.3) all **cost real compound** (worstComp
−0.3 to −0.6; c05 craters 5y −35%). Every config with `OVF_FLOOR < ~0.8` (contracting the overflow
hydration engine, low dd_conc) had worse compound → **never contract overflow** (confirmed pre-build).

**Phase C (N=300 × 10 windows incl COVID, task #84) — DECISIVE NULL.** The Phase B "Pareto" was MC noise:

| cand (low-tier-only, OVF off) | ddFoc | dd5y | comp | worstComp | verdict |
|---|---:|---:|---:|---:|---|
| c00 FLOOR=0.80 (B-c14 "winner") | +0.07 | **−1.1** (5y DD WORSE) | −0.024 | −0.091 | no-op→worse |
| c01 FLOOR=0.70 | +0.83 | +1.5 | −0.053 | **−0.242** (fails clean) | DD-helps / compound-costs |
| c02 FLOOR=0.75 wide | +0.32 | −0.9 | −0.039 | −0.179 | worse |
| c03 FLOOR=0.60 | +0.42 | −0.5 | −0.052 | −0.170 | worse |

The N=100 "−1.4pp 5y DD" flipped to **−1.1pp WORSE** at N=300. **No config delivers a confident 5y
WorstDD reduction with compound flat-or-up.** The 22-now and 5y *primary* gate windows do not improve.

**Why DQT is fundamentally null (not a tuning failure):** the LOW (75-79) tier is the capital-velocity
**volume engine** (G16), and its low-EV-during-drawdown is **crash-artifact-coupled** — low-EV in
sustained-bear drawdowns (2022 −0.067) but HIGH-EV in bull drawdowns (2024 +0.241, dip +0.178). The
DD-gate cannot separate these: contract early (low DD_MIN) → hits bull drawdowns (compound cost);
contract late (high DD_MIN) → fires after the drawdown already happened (no DD benefit). So the
2022-concentrated DD benefit can't be extracted without paying compound elsewhere. The tier-DD seam,
while orthogonal and DD-concentrated, is not Pareto-extractable. (Contrast the regime levers: their
cohorts are low-EV *across* windows — a genuine regime/flow/skew inefficiency, not a velocity-engine.)

## VERDICT — NULL. Staged env-gated OFF (reversible null-infra).

The DD-sizing-lever well is **rigorously dry** after RXDD/SVR/MWDD/TVDD + F3F: the regime/breadth/volume
axes are captured/redundant/anti-aligned, and the one orthogonal residual (conviction-tier) is
velocity-coupled and not Pareto-extractable. **No 5th DD-sizing lever ships.** DQT is left env-gated
OFF in `monte_carlo.py` (default OFF → baseline byte-identical, verify NOOP=(0,0)) + `driver.py`
ENV_MAP, as reversible null-infra (the EXR/CDR/REALLOC pattern) for a future revisit if the substrate
changes. NOT wired into strategy_config / backtest_cascade / registry (it is not shipped).

## Harness

- `mine2.py` — extended residual-DD screen (adds NH/NL, %above-EMA, breadth-velocity bins +
  orthogonal slice); `mine_report.json`.
- `sweep.py` — DQT B/C/D driver (LHS over DQT_DD_MIN/DD_FULL/FLOOR/OVF_FLOOR; collapse-0 +
  worstComp>=−0.15 clean filter; ranks by DD-focus reduction).
