# FINDINGS — TP/SL calibrated-PRIMARY verification (Core boundary sweep)

**STATUS: COMPLETE 2026-08-11. VERDICT: CONFIRMED — the shipped Core pair (TP +0.10 /
SL −1.00) stands. No cell passed the locked displacement rule; no ship motion.**

Prereg: [PREREG.md](PREREG.md), locked pre-outcome at `7e788f45`. Runner:
`driver/calibP_run.py` (imports tpsl_refine phaseD machinery read-only; version pin id=74
`f9fb7b934` reused). Queue #446 smoke / #447 full, N=500 paired, windows 22-now/5y,
TP_FILL_MISS_P=0.15 + TP_FILL_GAP_AWARE=1 (self-logged in `logs/*.log`). Identity-check
arms reproduced `phaseD_core_calib.csv` **bit-identically** (8/8 values, delta 0.000000pp)
— engine/env drift excluded. Evidence: `out/calibP_core16.csv` (+ per-path parquet).

## Result grid (calibrated median compound %, by cell)

| TP \ SL | −0.80 | −0.90 | −0.95 | −1.00 |
|---|---|---|---|---|
| **5y:** 0.050 | −35.7 | −34.0 | −34.3 | −38.2 |
| 0.075 | −27.7 | −26.0 | −27.7 | −32.7 |
| **0.100** | **−23.5** | **−24.1** | −25.2 | **−28.7 (shipped)** |
| 0.150 | −34.6 | −35.0 | −37.3 | −40.4 |
| **22-now:** 0.050 | −45.9 | −44.3 | −45.2 | −46.3 |
| 0.075 | −44.1 | −42.9 | −44.1 | −44.8 |
| **0.100** | −42.8 | **−41.9** | −43.5 | **−43.7 (shipped)** |
| 0.150 | −47.0 | −46.5 | −48.8 | −49.0 |

WorstDD: 56.4–60.4 across all 32 cells (shipped 57.2/57.2; no cell better by >0.7pp
anywhere). **p_coll = 0.00 in all 32 cells** — the entire shipped neighborhood is
collapse-free under calibrated fills.

## Findings

1. **TP 0.10 is the INTERIOR argmax in TP — the boundary question is closed.** The
   tighter-is-better gradient (TP30→TP20→TP10) REVERSES below 0.10: TP 0.075 loses
   1.1–4.0pp and TP 0.05 loses 7.2–10.2pp of median vs TP 0.10 at every SL, both windows.
   Mechanism: below 0.10 the touch-rate gain is tiny (tp_rate 90.3→93.1 at 5y) while the
   per-win take halves — the at-barrier/never-fill haircut does not reward infinite
   scalping. The scalp-artifact friction guard (§4.6) never fired — no TP<0.10 candidate.
2. **SL: dead-hold −1.00 stands; the −0.80/−0.90 median preference is real but below the
   locked bar.** (0.10,−0.90): Δmed +4.6pp (5y) / **+1.8pp (22-now → fails the ≥3.0pp
   both-windows bar)**, DD +0.5pp worse. (0.10,−0.80): +5.2pp / +1.0pp — same failure
   shape. Context recorded, not adjudicated: SL-fires Core variants were survivor-FLAGGED
   in the parent campaign (delisted-cohort-dependent; sl_rate rises 8.4%→9.7–10.9% as SL
   loosens, and those extra fires concentrate in the death-spiral cohort per the dead-hold
   law) — the honest prior is that part of the −0.80/−0.90 median edge is delisted-cohort
   artifact. Guards §4.3–4.6 not run (no candidate cleared rule 1).
3. Prereg honesty check: §2 declared the as-seen runner-up would fail the bar unless new
   cells beat it — no new cell did. No surprises; the rule was not bent.

## Disposition

- Shipped pair (0.10, −1.00) is now verified argmax-or-statistically-indistinguishable
  under BOTH worldviews (canon-primary per parent campaign; calibrated-primary here).
- Re-visit triggers (either): (a) the MC-realism default flip lands (its Stage-3 A/B
  should re-read this grid's −0.90/−0.80 column with a survivor arm at N=500×12), or
  (b) P2.B live-fill reconciliation re-derives the fill canon.
- Apex TP/SL: closed from parent-campaign evidence (all alternates collapse 34–100% under
  calibration; shipped 0.10/−0.60 uniquely robust). Sentinel: still has NO calibrated read
  (tracked by capital_plan_refresh flag).
- Ops note: submitted `--db heavy` (orchestrator instruction); SUBMIT_PLAN precedent for
  this read-pattern is `--db light` — future runs should use light.
