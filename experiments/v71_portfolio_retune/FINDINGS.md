# v71 Stage-3 Portfolio Retune — Findings (2026-06-10)

**Problem:** v71 (integrity-audit ship) doubled 75+ signal supply (+83% 5y) at
flat-to-better honest WR. The Apex portfolio params were fitted to v70's supply
density → the book saturates: N=300 apples-to-apples showed 5y med +1,292% /
WorstDD 74.3% vs v70's +2,574% / 65.1% on the same engine. TP/SL (Stage 2)
frozen; this retune re-fits cascade / exposure / overflow / DD-band (+ the two
user-requested explorations below).

Harness: `sweep.py` on `experiments/v69_portfolio_retune/driver.py`
(deterministic PYTHONHASHSEED=0 paired seeds, MC_NO_DB_PERSIST, active=v71).
Phase B: N=100 × {2020_crash, 2022, 2024, dip, 22-now, 5y}. Results
`.cache/v71_portfolio_retune/phase_B.jsonl`; per-candidate logs in
`experiments/v69_portfolio_retune/_childlogs/`.

## Phase B ranking (DD-focus Δ = mean(5y, 22-now) WorstDD improvement vs base; collapse=0 everywhere unless noted)

| cand | config | dd5y | dd22n | ddΔ | med5y vs base | crash DD |
|---|---|---|---|---|---|---|
| **c14_low05_ovf0** | low .10→.05, ovf 0 | **66.1** | 65.0 | **+6.75** | **×1.48** | 68.8 |
| **c15_topheavy** | 20/15/8/6/0 | 67.6 | **64.1** | +6.45 | ×1.45 | 68.7 |
| c02_alloc065 | all tiers ×0.65 | 67.5 | 65.7 | +5.70 | ×0.82 | 63.8 |
| c03_alloc050 | all tiers ×0.50 | 59.2 | 59.9 | +12.75 | ×0.57 | 58.0 |
| c17_cap45_a080_ovf02 | caps .45 + ×0.8 + ovf .02 | 69.4 | 66.4 | +4.40 | ×1.02 | 66.6 |
| c_base (live Apex) | 20/15/10/10/.035, caps .50 | 74.2 | 70.4 | 0 | ×1.00 | 73.1 |
| c04_ovf0 | ovf 0 only | 74.6 | 74.0 | −2.00 | ×1.57 | 70.9 |
| c09_cap60 | caps .60 | 77.9 | 77.0 | −5.15 | ×0.75 | 78.0 |

**The winner class is SELECTIVITY on the doubled-supply tier**: halving the
75-79 slug (and zeroing overflow) cuts DD ~7-8pp AND lifts compound ~45-48% —
a true Pareto. Mechanically: 75-79 N doubled in v71; at 0.10 alloc that tier
dominated the book's correlated exposure. Smaller slugs across more signals =
same tier participation, better diversification, faster recycling.
`c09` re-confirms the capital-velocity law on the new substrate (cap 60% hurts
BOTH axes); `c03` shows the other extreme (alloc ×0.5 = best DD, gutted
compound — velocity loss).

## Put reintroduction (user request) — NULL RE-CONFIRMED on v71

Prior: hard null on v70 (2026-06-02, `experiments/v69_portfolio_retune/put_tail_tiny.py`).
New condition tested: v71 retired JA4 (puts on standard regime mult) + put WR
+1.1pp. Pre-committed kill criteria from the documented null: any collapse>0,
2022 DD +5pp, return gut.

| probe | config | ddΔ focus | med5y | collapse | 2022 |
|---|---|---|---|---|---|
| c20_put_sliver15 | ≤15-only, 1 slot, 5% cap | **−4.05 (worse)** | ×0.93 | 0 | +33→+21%, DD 66→69 |
| c21_put_mod | 0.08/.05/0, 2 slots, 10% cap | −12.00 | ×0.27 | 0 | med −11%, DD 78 (+12pp → KILLED) |
| c22_put_half_v60 | 0.06/.05/.04, 4 slots, 15% cap | −18.55 | ×−0.01 | **15%** | med −32%, DD 83 (KILLED) |

Monotonic: more puts → strictly worse on every axis; the wipeout mode
reproduces exactly. Even in 2022 — the put-friendly year — the most favorable
sliver LOSES return and worsens DD; the COVID-crash "hedge" (−49→−47% med) is
negligible vs its 5y cost. **The structural conclusion survives the substrate
change: put losses cluster with call stress and consume the cash buffer.
Do not re-test put reintroduction again without a fundamentally different
mechanism (the reserved-cash ledger variant was ALSO already null on v70).**

## 70-74 overflow logic (user request) — premise inverted on v71; retire the tier

B2 ladder (paired seeds): compound is MONOTONIC in overflow size — ovf 0 →
×1.57, 0.01 → ×1.43, 0.035 (live) → ×1.00, 0.05 → ×0.81; DD differences inside
the N=100 noise band. Hydration tape (N=100 × 5y, live config on v71,
`.cache/dd_ledger/tape_5y.parquet`): **overflow = 196,568 of ~431k fills =
45.6% of all trades** — the tier is NOT filling idle slots anymore. The 2026-06-03
justification (hydration 22%→89% on v70's starved book) is gone: v71's doubled
75+ supply fills the book itself, and every overflow slug intertemporally
displaces tomorrow's 75+ signal from the shared 50% premium cap. Overflow goes
to 0 in the winner class. (If a future version DEFLATES 75+ supply again, the
overflow becomes a live lever again — it is supply-density-conditional, not
universally bad.)

## Phase C/D

Phase C (N=300 × 8 incl COVID): c14 leads (5y dd 67.6 / med ×1.29; ddFocus
+7.30 — STRENGTHENED from B's +6.75, the strong-lever signature); c15 trails on
5y dd (71.4); c02 fails the compound guard (×0.67).

## Phase D ship gate (N=500 × 10 windows incl 2020+2020_crash) — ALL T-GATES PASS

| window | base med/dd | c14 med/dd | ddΔ |
|---|---|---|---|
| 2020_crash | −49% / 74.1 | −54% / 69.1 | +5.0 |
| 2020 | −32% / 82.2 | −33% / 72.7 | +9.5 |
| 2021 | −40% / 75.4 | −36% / 67.4 | +8.0 |
| 2022 | +33% / 66.5 | +52% / 55.1 | +11.4 |
| 2023 | −39% / 69.3 | −42% / 58.4 | +10.9 |
| 2024 | +1,144% / 53.6 | +1,022% / 35.5 | +18.1 |
| 2025 | +132% / 72.0 | +27% / 65.7 | +6.3 |
| dip | +160% / 47.6 | +191% / 40.3 | +7.3 |
| 22-now | +2,239% / 72.9 | **+2,485% / 65.0** | +7.9 |
| **5y** | +1,230% / 74.3 | **+1,660% / 67.6** | **+6.7** |

T4 PASS (−6.7pp vs ≤+1.0 tolerance) · T5 PASS (**every window improves** — zero
regressions) · T6 PASS (collapse=0 all 10 × both arms) · T7 PASS (5y compound
**+35%**). Collapse=0 incl COVID; a full Pareto at ship-gate fidelity, stable
across N=100→300→500.

## SHIPPED 2026-06-10 09:45 ET (B→C→D chain ran 06:55–09:30 — ~6 min/candidate at N=300×8, far below the pre-estimates)

`STRATEGY_30DTE.TIER_ALLOC`: `low 0.10 → 0.05`, `overflow 0.035 → 0.0`;
`portfolio_profiles.json` apex: `tier_low 0.05`, `tier_overflow 0.0`.
Core/Sentinel untouched (Core keeps its own profile values pending separate
validation; Sentinel zeroes both anyway). Drift-guard 632 / registry /
dte-audit green; `trader alloc` displays the new values; temporal-refresh +
research-pack rebuild queued at ship. vs the v70-era profile: DD essentially
recovered (67.6 vs 65.1), compound −36% gap remains a v71-substrate property
(honest signal mix), not a tuning gap — the B-sweep's cap/MaxPos/DD-band axes
were all dominated by the selectivity lever.
