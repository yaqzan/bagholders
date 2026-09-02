# FINDINGS — Liquidity-floor MC Stage-3 A/B

**STATUS: COMPLETE 2026-08-11. FINAL: A2 passed all locked lanes/guards but the
pre-declared exposure-matched control (`experiments/floor_control_2026_08/`) came back
attributable = −0.57pp (random cuts matched/beat the floor's DD win) → SHIP CANCELLED;
LIQUIDITY_FLOOR stays default-OFF. The lane pass below stands as recorded; it measured a
SIZE effect, not selection. A1 {≥$0.25, ≥5} FAILS both lanes outright.**

Prereg `5a9b4949` + AMENDMENT-1 `e379a4ca` (pre-outcome: population = 75+ primary tier;
windows {2023,2024,2025,dip}; tripwire fired correctly on the original spec — the 70-74
overflow tier is ~70% of raw candidates, carries TIER_ALLOC=0, and would also have injected
an RNG-draw confound if filtered). Amended tripwire 98.1-99.7% PASS ×4; identity arm
bit-exact (14 fields); anchor cell reproduces main battery bit-exact. Queue #456-466.
Evidence: `out/floorMC_{main,survivor,neighborhood,tripwire_amended}.csv`.

## Main battery (N=500 paired, calibrated fills, survivor-measured MISS_P per arm)

WorstDD, A0 (no floor, MISS_P .15) → A2 (floor, MISS_P .11): 2023 45.1→44.4 (+0.7),
2024 25.2→34.3 (**−9.1, the one loss**), 2025 44.4→29.7 (**+14.7**), dip 33.2→21.9
(**+11.3**). Mean +4.40pp ≥ 2.0 bar; 3/4 breadth ✓; compound guard breached only in 2024
(+68.1→+5.1 median — the harvest-year give-back; exactly one window allowed) ✓; collapse 0
everywhere ✓. A1: DD 2/4 only → dead. Supply cuts realized: A2 45.5-53.3%, A1 34.1-43.9%.

## Guards

- **Survivor (delisted-excluded), A2 vs A0:** DD deltas +5.9 / −6.6 / +17.4 / +13.1
  (mean **+7.4pp — the edge GROWS among survivors**; anti-S9 signature). PASS. Noted
  caveat: survivor-universe compound give-back appears in 2023 too (−13.2→−23.8) — recorded,
  feeds the control question below.
- **TP/SL neighborhood under A2:** anchor (0.10,−1.00) is best-median outright on dip;
  within 1.11pp of best (0.075,−0.90) on 2024 with anchor-DD not worse; the tighter-TP cell
  flips to WORST on dip (−13.9 vs −7.4) — direction unstable, anchor robust. STABLE — no
  TP/SL-under-floor re-sweep fires. (With calibP, this closes the TP/SL question under both
  worldviews AND under the floor.)

## Why ship is deferred despite green gates (pre-declared 2026-08-11 before guards ran)

The floor's DD gains live in grind years; its cost concentrates in the up-year (2024
median +68→+5). That pattern is also the signature of *merely trading less*. The measured
MISS_P improvement (0.15→0.11) is real selection value, but the clean discriminator is an
**exposure-matched control**: random cuts to A2's exact per-window supply at UNCHANGED
MISS_P=0.15. Selection-attributable edge = A2's DD improvement minus the random-cut's;
the floor ships as a selection mechanism only if that attributable mean ≥ 2.0pp (the same
LANE-DD bar) at ≥3/4 breadth. Else the honest lever is an explicit exposure knob, not a
floor wearing a selection costume. Control campaign: `experiments/floor_control_2026_08/`.

Additionally, the floor was validated under CALIBRATED fills while the shop's default
doctrine is still canon — shipping it alone would create a measurement-doctrine mismatch.
Package: control result + MC-realism default flip + LIQUIDITY_FLOOR default-ON as ONE
coherent ship decision.

## Limitations that travel

- 4 in-archive windows only (2022-08+ data); crash-window floored behavior unmeasurable
  with current data. Floor removes entries only — crash-mode risk is under-deployment.
- All medians remain negative in 3/4 windows in EVERY arm under calibrated fills — this
  campaign improves the calibrated book's *shape*; it does not make the book fundable.
  Arbiters unchanged (Dec-15 OOS, forward ledger, P2.B).
