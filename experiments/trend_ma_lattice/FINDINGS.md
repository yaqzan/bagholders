# Trend MA-Lattice Study — FINDINGS

**Date:** 2026-07-14 (overnight /research run, 00:40–~02:30 ET, weeknight budget)
**Verdict:** **COMPREHENSIVE NULL — axis closed.** No cell in any family clears the
pre-registered multiplicity bar. The trend component's internal MA construction
(kernels, horizons, crosses, above/below states, EMA−SMA divergence, sub-term mix)
carries **no unexploited funded-cohort signal** on the v74 substrate.
**Substrate:** v74 (`f9fb7b934`), funded 75+ CALL ledger N=5,854 (697 symbols,
2021-01-01→2026-06-15 holdout cutoff), base apex WR 70.1% / apex EV +0.021.
**Gameplan:** `GAMEPLAN.md` (pre-built 2026-07-13; executed as written).

## The ask (user, verbatim intent)

> Explore the EMA and SMA across different time horizons and gauge their interactions in
> relation to the trend score. Trend is an integral part of our strategy but it's narrow —
> we can benefit from MC Bayesian runs of different time horizons or cross points,
> above/below behaviour.

## Method (Phase A read-only mine; no recalc, no MC — the grid resolves on the ledger)

- **Features** (`features.py`): strictly point-in-time per (symbol, entry_date) from
  PriceHistory closes. EMA+SMA × P={8,13,21,34,50,100,150,200}; above/dist/slope-sign per
  (kernel,period); 7 cross pairs × {state, days-since-cross, fresh golden/death ≤5 bars};
  21/50/200+price stack alignment; kernel divergence (EMA−SMA)/SMA at 21/50/200; stored
  `Score.trend` × lattice disagreement cells; sub-term decomposition (position/alignment/
  momentum/macro recomputed exactly + one-tier-faster/slower horizon variants each).
- **Self-tests, all PASS at full scale:** (1) recomputed EMA/SMA ≡ stored `Indicator`
  columns (1e-3 rel); (2) look-ahead truncation test — features at d identical when the
  series is cut at d (0/6,250 mismatches); (3) trend recomposition ≡ stored `Score.trend`
  ±1 on 750/750 rows (max |diff| = 1); (4) holdout enforced (max date = cutoff 2026-06-15).
- **Judgment surface:** apex option EV mapping {TP +0.30 / SL −0.70 / expiry −0.40},
  same-bar-tie-loses; apex WR primary. Partial-regression controls: every cell's effect
  re-estimated with stored `Score.trend` + pct-from-EMA50/200 in the design (logit for WR,
  robust OLS for EV) — a cell must survive as `t_controlled`, not just raw z. Replication
  surface: full-universe `30dte_apex` DuckDB mirror (scaled-barrier analog, secondary).
- **Bayesian layer (the "MC Bayesian" ask, formalized):** per cohort×window Jeffreys
  beta-binomial (WR) + normal (EV) posteriors, ~10k-draw hierarchical pooling →
  P(pooled edge>0), P(sign consistent ≥4/5 windows). Windows: 2021/2022/2023/2024/2025+.
- **Pre-registered multiplicity bar:** PRIMARY families (kernel-divergence,
  cross-freshness, sub-term decomposition) clear only at |z_raw|≥3 AND |t_controlled|≥2.5
  AND P(sign-consistent)>0.90. All others exploratory: |z|≥4 OR dual-surface replication.
  349 cells enumerated → ~0.9 false z≥3 hits expected; **2 observed** (chance-compatible).

## Results — verdict per family (190 cells treated, 159 screened at |z|<2)

| Family | Cells treated | best \|z_raw\| | best \|t_ctl\| | Replicated | Verdict |
|---|---|---|---|---|---|
| cross-freshness (PRIMARY) | 107 | 3.15 | 3.25 | 6 | **NULL** — best cell fails Psign (0.38) or z (2.91) |
| subterm-decomposition (PRIMARY) | 40 | 2.25 | 1.62 | 3 | **NULL** — decisively flat |
| kernel-divergence (PRIMARY, novel) | 21 | 2.97 | 2.79 | 2 | **NULL** — Psign 0.59, window-unstable |
| dist (above/below distance) | 13 | 2.68 | 1.64 | 1 | NULL (exploratory) |
| above-below | 6 | 2.82 | 1.90 | 2 | NULL (exploratory) |
| stack-alignment | 2 | 3.31 | 2.51 | 0 | NULL — z<4, failed replication |
| trend-interaction (disagreement cells) | 1 | 2.15 | 1.39 | 1 | NULL (exploratory) |
| slope / cross-state | 0 treated | <2 all | — | — | NULL — never left stage-1 screen |

**PRIMARY VERDICT [kernel-divergence]: NULL** — best cell `kdivmag_21=extreme`
(N=770, EVΔ −0.040, z −3.0, t −2.8, Psign 0.59, signs `-+---`).
**PRIMARY VERDICT [cross-freshness]: NULL** — best-|z| cell `fresh_golden SMA21/100`
(N=228, EVΔ −0.092, z −3.2, t −2.9, Psign 0.38, signs `--.--`).
**PRIMARY VERDICT [subterm-decomposition]: NULL** — best |z| 2.25 over 40 cells; no
sub-term is dead weight with funded consequence; the LEAN-retune arm (ARM-L) is dead too.

### The one near-miss, recorded honestly

`cdays_SMA_8_21 = 16-30` (mid-aged short-kernel cross): N=754, EVΔ +0.047, WRΔ +4.8pp,
z +2.91, t_ctl +3.25, P_pooled 1.00, Psign 0.85 (2021 negative), **replicates=True** on
the full-universe mirror. It fails the pre-registered bar on two legs (z<3, Psign<0.90)
and is one story out of 349 cells — **not** a finding, and not shippable evidence. Logged
because it is the only cell that would merit a look at the Dec-2026 OOS re-read.

### Observations (evidence-grade context, not verdicts)

1. **The fresh-cross hypothesis is INVERTED in the data.** Fresh golden crosses / 0-5d
   cells lean *negative* EV (ranks 3,6,10,11,12,15,17 all negative); mid-aged (16-30d)
   lean positive. Consistent with G27 (winners are direct continuation — a fresh cross is
   often a reversal-chase) and the sleeve's buy-weakness character (G19/G45). The whole
   family is still window-unstable → NULL, but any future re-mine should expect this sign.
2. **Below-SMA21 is (weak) positive EV** (+2.8z raw, t 1.9 controlled) — the substrate-
   robust buy-weakness inversion again (G19/G45), already exploited; nothing new.
3. **Sub-term flatness closes the "trend is narrow" attack**: no horizon variant of any
   sub-term (one lattice-tier faster/slower each) separates funded EV. The trend component
   is at its funded optimum given price-history inputs — matches verify_value/G41-G42
   ("nothing predicts apex above the gate") and the A0/weather_components diagnosis that
   trend's weakness is *regime-conditional dominance*, not construction.
4. **Kernel choice (EMA vs SMA) does not matter at the funded gate.** No kdiv sign/mag
   cell survives controls with window stability; the SMA columns in `Indicator` can stay
   unconsumed.

## What would change this verdict

- The Dec-2026 holdout unlock (OOS ≥2026-06-15): if `cdays_SMA_8_21=16-30` (and the
  fresh-cross negative pattern) hold sign and magnitude OOS, cross-freshness earns ONE
  re-read on the then-active substrate — as a candidate feature, not a presumption.
- A NEW discriminator class (e.g. real option-IV/gamma inputs, N3 data-unblock) changing
  what "funded EV" is conditioned on. MA lattices on price alone are closed.

## Engineering traps hit (promoted to /research LIVING GOTCHAS G47)

Three full-scale-only failures after a green 20-symbol smoke; all share one root —
**young listings with <200 bars** appear only in the full universe:
1. `pl.DataFrame(list-of-dicts)` schema inference (G7 recurrence — the class recurs
   because subagent briefs didn't forward it; fixed `infer_schema_length=None` ×4).
2. NaN-as-sentinel ≠ null in polars: NaN passes `is_not_null()` → strict Int cast crash.
   Fixed with a single post-selftest `fill_nan(None)` normalization choke point.
3. **Silent false-NULL (the dangerous one):** NaN regressors (null dist_EMA_200) poisoned
   the controlled logit/OLS inside try/except → t_controlled=NaN → PRIMARY gate auto-fail
   with no error. Fixed by masking non-finite X rows (`finX`) in `controlled_t`. Any gate
   whose failure mode is "NaN counts as null-verdict" must prove its inputs finite.

## Artifacts

- `experiments/trend_ma_lattice/{GAMEPLAN.md, features.py, mine.py}` (self-testing harness;
  `--smoke` / `--symbols N` modes)
- `.cache/trend_ma_lattice/{ledger,features,cohort_stats}_v74_full.parquet`
- Queue tasks #624-627 (fail→fail→fail→pass); run log with full SUMMARY:
  `.codex/runs/task_627_*/run.log`
