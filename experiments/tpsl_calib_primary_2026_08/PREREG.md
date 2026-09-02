# PREREG — TP/SL calibrated-PRIMARY verification (Core boundary sweep)

STATUS: LOCKED 2026-08-11 before any new-cell outcome exists (git commit = the lock).
Owner directive 2026-08-11: "find the optimal TP/SL" honestly, post fill-realism escalation.

## 1. Question + why this exists

`tpsl_refine_2026_08` optimized TP/SL **canon-primary** (default fill model) with the
calibrated-reality knobs (TP_FILL_MISS_P=0.15 + TP_FILL_GAP_AWARE=1) as a differential
ship gate. Its winners are therefore canon-argmax cells that *survive* calibration — not
proven calibrated-argmax cells. This campaign asks: **is the shipped Core pair
(TP +0.10 / SL −1.00) the argmax under the calibrated-PRIMARY reading, including the
TP < 0.10 boundary the original grid never covered?** (Phase A TP floor was 0.15; 0.10
entered only via Phase B ±0.05 gap-fill around a carried cell — the boundary was reached,
never crossed.)

SCOPE: **Core only.**
- Apex: adjudicated CLOSED from existing evidence — every tested TP/SL alternate collapses
  under calibration (p_coll 34–100%) while shipped (0.10, −0.60) reads 0.8% (old n10×10
  sizing) / 0.0% (shipped v6 n12×6%). No new cells can plausibly flip this; no sweep.
- Sentinel: out of scope (no calibrated substrate exists at all — flagged separately).

## 2. Already-seen evidence (declared for honesty; the LOCK covers the decision rule + new grid)

From `tpsl_refine_2026_08/out/phaseD_core_calib.csv` (N=500, windows 22-now / 5y,
MISS_P=0.15 + GAP_AWARE) and the ship record:

| cell | 22-now med / DD | 5y med / DD | canon 5y DD |
|---|---|---|---|
| 0.30/−0.70 base | −55.5 / 65.4 | −53.2 / 64.9 | 53.2 |
| **0.10/−1.00 SHIPPED** | −43.7 / 57.17 | −28.7 / 57.20 | **40.8** |
| 0.10/−0.90 runner-up | −41.9 / 57.62 | −24.1 / 57.75 | 41.6 |
| 0.20/−1.00 | −49.5 / 59.9 | −44.7 / 61.8 | — |
| 0.20/−0.70 | −46.1 / 59.2 | −43.4 / 60.0 | — |

Also seen: `alloc_retune_2026_08` calibrated Core S9_mp20 cells read −25.7 / **+2.4** but
were FLAGGED no-ship (survivor-only edge evaporates — delisted-dependent); Apex calib table
as summarized above. Declared consequence: the (0.10,−0.90) runner-up's as-seen deltas
(+4.6pp 5y / +1.8pp 22-now median, DD +0.5pp worse) already FAIL the both-windows bar in
§4 — the rule below is stated knowing this, and the justification for the bar is given
there, not reverse-engineered afterward.

## 3. New grid (the locked hypothesis set)

Calibrated mode (TP_FILL_MISS_P=0.15, TP_FILL_GAP_AWARE=1), N=500, windows **22-now, 5y**,
paired seeds via identical window-label strings, ONE SUBPROCESS PER CELL (barriers bake at
`_prepare_window`; sigma globals at import — never reuse a pool across TP/SL cells).

Core grid: TP ∈ {0.05, 0.075, 0.10, 0.15} × SL ∈ {−0.80, −0.90, −0.95, −1.00}
= 16 cells, of which (0.10,−0.90) and (0.10,−1.00) are **identity-check arms** (must
reproduce §2 within seed-pairing tolerance; a mismatch invalidates the run — engine or env
drift) and 14 are new calibrated reads.

Smoke acceptance before the full job: 2 cells at --smoke-n, distinct outputs required
(TP=0.05 vs TP=0.15 must differ — stale-barrier trap check).

## 4. Decision rule (LOCKED)

A cell C displaces the shipped (0.10, −1.00) for ship-consideration ONLY if ALL hold:
1. Calibrated median: C beats shipped on **both** windows by ≥ **+3.0pp** each.
   (Bar justification: seed-SE of the median at N=500 ≈ 0.4–0.5pp — 3pp ≈ 6–7× seed
   noise, sized to absorb model-family spread between fillprobe/calib/buf20 variants, and
   matches the campaign's materiality scale. Both-windows requirement guards single-regime
   luck.)
2. Calibrated WorstDD: C ≤ shipped + 1.0pp on both windows (DD-primary retained; median
   ranks only because calibrated DD is near-tied in the tight-TP family).
3. Canon re-read (full 12-window battery, default fills): C's 5y WorstDD ≤ shipped
   (40.8) + 2.0pp and collapse 0 everywhere — fill-model robustness must hold BOTH ways.
4. Survivor arm: C's edge survives with delisted excluded (no S9-style cohort artifact).
5. buf20 (MISS_P=0.20 + GAP_AWARE): ordering C > shipped preserved.
6. If TP(C) < 0.10 — SCALP-ARTIFACT GUARD: C's turnover (round-trips) vs shipped may not
   exceed +25% unless a friction-tape resim (audit_tp15_* machinery pattern) shows the
   ordering survives per-contract costs; canon's free-limit-TP cost model makes extra
   round-trips artificially free (COST_AUDIT split ruling) — a tight-TP "win" that exists
   only under free exits does not count.

If no cell passes all six: **verdict = CONFIRMED — the shipped pair stands as the
cross-worldview optimum**; runner-up structure recorded; no ship motion. Any pass →
separate ship-portfolio process with its own Stage-3 evidence (this prereg licenses a
verdict, not a ship).

## 5. Stop rule

One grid. No extensions, no metric swaps, no new lanes after any new-cell outcome is seen.
Follow-ups limited to the declared guards (§4.3–4.6) on candidate winners. Campaign closes
with a FINDINGS.md verdict either way.

## 6. Compute

~16 cells × ~22s/cell (N=500×2w, measured from phaseD calib logs) ≈ 6–10 min + guards.
Queue: high tier, off-market submission, mirroring the campaign's submission pattern.
