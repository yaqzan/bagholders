# FINDINGS — Entry liquidity-floor cost/benefit triage (report-only)

**STATUS: COMPLETE 2026-08-11.** Measurement study on real-print data (ledger_v2 N=4,403
kept signals 2022-08→2026-07; fill-fidelity matched events N=3,199; FF-3' liquidity map).
No ship motion; feeds the floor-MC Stage-3 A/B and the G3(b) small-account model.
Evidence: `out/*.csv`; script `build_floor_sweep.py`. Integrity: premium/tier joins
reproduce source values exactly; skip-reason census matches FF1_RESULTS.

## Verdict-level readings

1. **VOLUME, not premium, is the fill-fidelity lever.** Volume floor ≥5/≥10/≥25/≥50
   contracts/day cuts no-print 17.1% → 9.5/7.1/3.9/2.5% (supply cost 28.6/40.0/57.3/68.7%).
   Premium floor to $2.00 costs 29.4% of supply for NO fill improvement (never-fill flat
   15.5→16.2%) — premium level is a friction/cost variable, not a fill variable.
2. **~10% economic never-fill is FLOOR-RESISTANT.** Even at volume ≥50, never-fill only
   reaches 10.0% (from 15.5%). Selection removes the liquidity-absence channel; a ~10%
   model-vs-market path-divergence channel remains. Consequence: under any floor, the
   calibrated MISS_P for survivors is ~0.10-0.12, never →0. The fill gap is only ~2/3
   selection-fixable.
3. **The premium floor's case is friction-side only:** sub-$1/share = 11.0% of entries but
   **51.0% of equal-dollar contract weight** on this ledger (COST_AUDIT convention
   reproduced; older ledger said 10.2%/48.2% — same shape). A $0.50 premium floor sheds a
   third of the weight the fixed ~$10.8/contract friction feeds on, at 3.7% supply cost.
4. **Capacity headline (floor-independent):** with NO floor, **53.5% of real entries could
   not absorb even a 5-contract clip** without exceeding 25% of that contract-day's total
   volume; **75.2% fail at 20 contracts**. The signal population's tradable capacity is
   intrinsically tiny; any engine-faithful small-account model (capital plan G3b) must
   carry per-name clip caps, not just a universe filter.
5. **EV is flat across floors** (mean −7.9 to −8.9% band, median pinned +30% on the
   1.30/0.30 close-only real-print walk; payoff-shape artifact, not a floor effect).
   Excluded-mass attribution at {p≥0.50, v≥10}: 37% of excluded TP-declared events did
   fill same-day, 42% late, 21% never — the floor sheds UNRELIABLE and UN-SIZABLE
   exposure at ~EV-neutral per-trade terms; it does not sacrifice measurable edge.
6. **Candidate cells for the Stage-3 A/B:** gentle {premium ≥0.25, volume ≥5} (supply
   −29.8%, no-print ~9.5%, weight 61.4%) and working {premium ≥0.50, volume ≥10}
   (supply −42.7%, no-print ~7%, never-fill 11.3%, weight 44.3%).

## Caveats that must travel

- **Naming:** this study used `B:\polygon_derived\ledger_v2\` (4,403 kept), NOT the older
  3,371-path REST ledger the COST_AUDIT used. Cross-checked, consistent, different N.
- **PIT for the follow-on MC:** realized same-day `entry_volume` is only quasi-ex-ante
  (near-close entry sees most of the day's volume); the clean ex-ante variable is FF-3'
  `opt_vol_30d_atm` (trailing), but it correlates with realized entry volume at only
  ρ≈0.38 — the MC floor arm must pick its variable explicitly and re-derive supply cost
  under it, not import this study's numbers.
- The EV walk here (fixed 1.30/0.30 close-only on real premiums) is a per-trade shape
  check, NOT portfolio EV — no sizing, cascade, dampeners, or costs.

## Disposition

Next: floor-MC Stage-3 A/B (calibrated fills, survivor-measured MISS_P per arm, candidate
cells above vs no-floor baseline, N=300 screen → N=500 confirm), which also answers
whether the TP/SL optimum moves under a floor (prior: it doesn't — shrinking the miss
channel reinforces TP10; the A/B checks the argmax neighborhood cheaply). Capacity fact
(reading 4) feeds capital-plan G3(b) directly.
