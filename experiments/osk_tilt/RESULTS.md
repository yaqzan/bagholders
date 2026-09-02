# OSK Stage-3 Allocation Tilt — RESULTS

**Date:** 2026-07-10 · **Pre-registration:** `DESIGN.md` (locked before any run) ·
**Run:** queue task #569 (7 engine runs, ~90s total, exit 0)

**HARD CEILING (restated per pre-registration): option data spans ~1.3y
(2025-02-01..2026-06-15). The canonical 8-window N=500 T1-T7 gate (incl.
2020_crash) is UNREACHABLE — the only licensed outcomes here are STAGE or
PARK, never SHIP.**

## VERDICT: **PARK** — 0/5 variants pass the locked bars (need >=2 incl. >=1 lag1).

Decisive numbers: **best uplift t_clust = 1.06** (resid_lag0_g030) vs the
locked **t >= 2.0** bar; the only DD-and-compound passers (both lag1:
+1.25pp/−1.30pp and +0.17pp/−1.45pp) carry **t_clust 0.43 / 0.56** — sizing-
weighted pnl15 uplift is statistically null everywhere.

## Baseline-reproduction check (mandatory): **PASS, bit-exact**

Patched engine at g=0 vs fully unpatched run, restricted to entries <=
2026-06-15: 563/563 trades, 0 field mismatches (symbol/dates/premium/outcome/
pnl to 1e-6/1e-9), 348/348 equity-curve days identical to 1e-6.

## Per-variant table (vs. baseline: compound 32.99%, WorstDD 33.66%, 563 trades)

| variant | g | compound% | d_cmpd_pp | DD% | d_dd_pp | n_elig | n_tilt | uplift | t_clust | bars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| raw_lag0_g015 | 0.15 | 5.37 | −27.62 | 34.29 | +0.63 | 69 | 68 | +0.0097 | 0.79 | FAIL (dd, cmpd, t) |
| raw_lag0_g030 | 0.30 | 7.55 | −25.44 | 34.93 | +1.27 | 69 | 68 | +0.0164 | 0.67 | FAIL (dd, cmpd, t) |
| raw_lag1_g030 | 0.30 | 34.24 | +1.25 | 32.36 | −1.30 | 68 | 56 | +0.0143 | 0.43 | FAIL (t only) |
| resid_lag0_g030 | 0.30 | 5.56 | −27.43 | 34.84 | +1.18 | 69 | 68 | +0.0248 | 1.06 | FAIL (dd, cmpd, t) |
| resid_lag1_g030 | 0.30 | 33.16 | +0.17 | 32.21 | −1.45 | 69 | 56 | +0.0188 | 0.56 | FAIL (t only) |

Bar application (locked, DESIGN.md): STAGE requires >=2 variants (>=1 lag1)
with DD-not-worse AND compound >= baseline AND clustered t >= 2 on the
sizing-weighted uplift. Both lag1 variants clear DD+compound; **no variant
clears t >= 2**. Passing set = {} → **PARK**.

## Honest readings (no bar softening; recorded for the re-read)

1. **The inferential statistic is null across the board** (t 0.43–1.06 on
   n_elig ~69 pnl15-covered 75+ entries, ~44 unique dates). The uplift point
   estimates are all mildly positive (+0.010..+0.025) but the eligible-N is
   tiny — the 75+ tier's chain-hit AND ripe-pnl15 overlap is the binding
   constraint, exactly the coverage wall the score-modifier path already hit.
2. **The lag0-vs-lag1 compound divergence (−27pp vs +1pp) is a single-path
   artifact, not evidence.** A deterministic $50k cascade's terminal compound
   is hypersensitive to a handful of early large-position reroutings
   (repo canon: single-window compound noise 1.6–1.8x at N=300 MC; here N=1
   path). DD moves are small (±1.5pp). Neither direction clears — nor is
   judged by — anything but the pre-registered bars.
3. **This closes the sanctioned Stage-3 tilt surface for now** and the one
   allowed residualized-skew follow-up (osk_validation VERDICT "honest
   nuance") — the resid variants behaved like their raw counterparts, no
   rescue. OSK remains a per-trade residual only.

**Lead recorded:** revisit at the ~2026-10 forward re-read (forward_ranks
ledger cadence, per MEMORY project_osk_regime_conditional) with ~4 more
months of ripe option data — the eligible-N should roughly double by the
Jan re-read; the locked bars stay as-is.

## Artifacts
`DESIGN.md` (pre-registration) · `build_osk_zmaps.py` · `tilt_runner.py` ·
`tilt_results.json` / `tilt_results.txt` · `.cache/osk_tilt/zmap_manifest.json`.
No production file was edited (engine forked in-memory via inspect.getsource
with count==1 anchor asserts + try/finally restore).
