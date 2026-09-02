# FINDINGS — allocation re-sweep on the TP10 substrate (2026-08-10)

Prereg: [PREREG.md](PREREG.md) (locked 5ea15a6b pre-outcome; inherits tpsl_refine lanes/
floors/mechanics). Confirm rules locked in [TASK.md](TASK.md) before any N=500 number.
All evidence paired seeds, v74 pinned, TP/SL frozen at the same-day tpsl ship values
(Core TP+10/SL−100; Apex TP+10/SL−60). ~26 queue tasks #401-#438.

## Verdicts

**Apex: SHIPPED — n=12 × flat 6% (was n=10 × 10%), profile v6.** Paired 113-window
2x-race N=500 vs the same-barrier baseline (reused tpsl #379 arm, bit-identical on disk):
P(2x ever) 99.4→**99.6%**, worst DD 65.4→**56.6%** (−8.8pp), med days 128→197 (slower,
inside the ≤200 floor), collapse 0. Robust in every battery — survivor-only universe DD
edge GROWS (34.2 vs 50.9 on 22-now), calibrated-reality differential-positive (5y DD
70.1 vs 82.5, collapse 0 vs 1.2%), fill-probe −14pp. Runner-up n10×8% (99.0/59.6/158d)
passed the floors but was dominated on both primary metrics. n10×6% NO-SHIP (P2x 98.4%).
Under TP10's fast recycling the sprint wants MORE names and LESS per-name — deployment
~72% beats 100%.

**Core: both finalists FLAG — NO ship; the shipped S0 cascade stands.**
- S9(0.20/0.10/0.05/0.03)/MaxPos20/gross0.50: 5y DD −10.0pp (30.8 vs 40.8), 22-now
  −9.0pp, med 0.80×, no annual/crash regression, probes clean — but the **survivor-only
  arm kills it**: the DD edge evaporates without delisted names (same failure family as
  tpsl's (0.10,−0.60) cell). Real in the full universe, delisted-dependent at core sizing.
- S9/MaxPos20/gross0.65: crash-family +6.1-6.5pp regressions (2020/2020_crash/10y) AND
  survivor-flagged. The N=300 crash warning confirmed at N=500.
- Interpretation: the steep-shape/more-slots direction is genuinely promising in the full
  universe but its Core-sized edge concentrates in the delisted cohort — the honest next
  step (if pursued) is understanding WHY sizing changes interact with delisted names
  (likely: more slots reach deeper into the signal queue on high-supply days, and the
  delisted cohort's signal mix differs), not re-running grids.

## Coverage

Blast 249 cells (Core 240 = 10 shapes × 6 MaxPos × 4 gross; Apex 9) at N=150×4win →
refine 52 at N=300×9 → confirm 5 finalists + baselines at N=500×12 + batteries + 3
apex2x arms. Gross 0.80 mass-collapsed at blast (40 cells — the capital-velocity law
survives TP10). Lanes bit hard at refine (2 Core entrants of 40). Cross-campaign
integrity check: the alloc baseline reproduced the tpsl shipped-cell numbers bit-exactly
under paired seeds, twice.

## Ship record

`portfolio_profiles.json` Apex v6: tier_* 0.10→0.06 ×4, max_positions/call_max 10→12;
TP/SL pins untouched; selection_metrics = the paired 2x cert + battery notes. Drift
guard green (655 constants); derived overrides verified (mp12, tiers 0.06, net_tp 0.1 /
net_sl −0.615, gross 1.0). Research pack #441 + temporal-refresh #442 queued; API
restarted. Live ledger (Apex): re-qualification sweep fires on next update —
threshold-neutral, forward sizing moves to 12×6%.

## Traps banked (see LESSONS.md)

Gross-cap worker-liveness (one gross per process); research-env profile-inheritance leak
(Apex would have silently simulated Core's barriers); phaseD_apex2x CSV overwrite mode
(per-window JSONs are the evidence of record); locked-grid mechanical yields (Apex ~16
estimated → 9 actual under the band filter).
