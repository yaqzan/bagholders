# wave_cycle_mine — calendar/cycle-phase (W) + path-structure/waviness (S) on the funded v74 ledger

**Date:** 2026-07-16 (weeknight run, ~11h budget, used ~3.5h incl. two builder rounds)
**Verdict:** **W-family (all calendar-periodic "waves") = COMPREHENSIVELY NULL, axis closed. S-family
= ONE real locus (S1_vr5 path-persistence), the only 5/5-leg survivor of the July-2026 mines — but
BELOW the locked actionability floor → PARKED to the Dec-2026 OOS re-read. NO SHIP (honest park, G38).**
**Provenance:** user-steered ("find wave-like patterns we can statistically benefit from"), following
the Voynich-entropy discussion; both families null-checked against the registry before build
(NEW_LEADS triage entry 2026-07-16). Pre-registration locked before any outcome read
([PREREGISTRATION.md](PREREGISTRATION.md), incl. Amendment 1 — calendar source, locked pre-run).

## Substrate & bar

- N=5,810 funded v74 75+ call entries, 2021-01-04..2026-06-08, holdout-locked (max entry 2026-06-03
  < cutoff 2026-06-15). Base apex_wr 70.07% (expect ~70.1), plunge 23.25% (expect ~23.3) — matches
  the peak_fakeout substrate; 6/6 self-tests PASS (look-ahead, base-rate, holdout, finX, young-listing,
  calendar anchors ×10).
- **5-leg bar per cell** (29 cells × 2 outcomes; expected false |z|≥3 hits ≈ 0.16): (1) |z_clust|≥3
  (CR1 date-clustered) on WR or plunge; (2) |t_controlled|≥2.5 (logit; 7 controls incl. overall,
  trend, vol20, runup20_sig, pct_ema50, ln PIT-mcap, VIX); (3) P(sign-consistent ≥4/5 windows)>0.90;
  (4) N≥150 ∧ prevalence 5-95%; (5) replication — S: barrier-mirror sign; W: 2016-2020 era-slice sign
  (3,138-row fresh mirror). **Actionability REPORTED, not gated:** cohort WR < call BE 45%, or
  |d_ev| ≥ 0.03 vs rest.
- Observed |z_clust|≥3 cells: **2** — both terciles of ONE feature (one independent locus, per the
  G49 loci rule).

## W-family: null (17/17 cells)

OPEX-cycle position (0-3/4-8/9-13/≥14 days), OPEX week, turn-of-month, day-of-week, month-half
(may_oct/nov_apr), quarter-end week: **nothing near the bar**. Best cells: month-half |z_clust| 1.56-1.98
(t 2.05-2.29, the "sell in May" axis — null); Wednesday −3.23pp WR but z_clust −1.40 (date clustering
correctly deflates naive binomial z for date-level features — ~1,363 effective date clusters, not 5,810
trades); quarter-end week +2.35pp WR at N=446, z 0.68. The market's literal periodic waves carry no
funded-ledger edge.

**Supply diagnostic (report-only):** entries are heavily Friday-concentrated — 31.0% of entries vs
20.2% trading-day baseline (weekly-cadence features maturing into Friday scores). A supply artifact,
not alpha: Friday outcomes are base-rate (+0.41pp WR, z 0.27).

## S-family: one real locus, sub-floor

| cell | N | d_wr | d_plunge | z_clust(pl) | t_ctl(pl) | apex_ev | d_ev | legs |
|---|---|---|---|---|---|---|---|---|
| **S1_vr5 T1_low** (choppy/anti-persistent path) | 1,934 | **+2.79pp** | **−3.45pp** | −3.96 | −3.93 | +0.0506 | **+0.0298** | 5/5 |
| S1_vr5 T2_mid | 1,933 | −0.13 | +0.54 | 0.65 | 0.48 | +0.0182 | −0.0025 | 3/5 |
| **S1_vr5 T3_high** (trending/persistent path) | 1,933 | **−2.61pp** | **+2.87pp** | +3.53 | +3.71 | −0.0062 | −0.0269 | 5/5 |

- **The signal:** 5-day variance ratio of the trailing price path. Low-VR (mean-reverting/choppy)
  entries win more and plunge less; high-VR (persistent/trending) entries the mirror. Coherent with
  the whole canon — the edge is buy-weakness mean-reversion (G19); fresh-trend entries lean bad
  (MA-lattice fresh-cross inversion, G27); and it mechanistically corroborates the PARKED
  peak_fakeout TEXTURE family (climax/parabolic-in-strong-runs = plunge-prone).
- **Orthogonality:** vr5 is not a proxy — corr vol20 −0.085, runup20_sig −0.118, PIT-mcap +0.008,
  perm_entropy 0.040, lz76 −0.020 (kaufman_er20 0.222). The controlled-t barely moves vs raw z.
- **Honest cautions:** (a) **WR sign flips in 2022** (T1 signs `+−+++`) — the G26 reversal-trap
  signature on the WR outcome; only PLUNGE is sign-stable 5/5. (b) Leg-5 is the barrier-convention
  mirror replication (same entries, mirror barrier), NOT era-independent — the 2016-2020 era leg was
  locked W-only. (c) Other S features null: perm_entropy & lz76 flat (the literal entropy statistics
  carry less than the variance ratio); kaufman_er20 T2 z 2.40 (3 legs, null).
- **Actionability (as locked, report-only): NOT met.** No cohort below BE45 (worst = T3 67.5% ≫ 45);
  |d_ev| vs rest: T1 **+0.0298 vs the 0.03 floor — a 0.0002 miss, recorded as exactly that**; T3
  −0.0269. (The T1↔T3 tercile spread is 0.0568, but the locked text reads per-cell vs rest; no
  post-hoc redefinition.) Per the pre-registration, actionability was never a mechanism trigger —
  and a tonight-build Stage-3 tilt on a plunge-robust / WR-2022-flipping / ~zero-EV-cohort profile
  is exactly what G23/G26 prohibit.

## PARK — locked Dec-2026 OOS re-read (the only sanctioned next step)

On post-2026-06-15 OOS rows at the December unlock (`experiments/holdout_oos_2026_12/` window):
1. **Confirm:** T1-vs-T3 plunge separation same sign with |z_clust| ≥ 2 on OOS rows alone; AND
2. **Actionability:** pooled (IS+OOS) T1 d_ev ≥ 0.03 (the locked per-cell form).
3. **Both pass → license an SVR-class per-signal Stage-3 probe** (vr5-keyed sizing tilt, its own
   B→C→D N=500 incl COVID, collapse=0) as a separately gated step. **OOS sign-flip or |z_clust|<1 →
   close the axis permanently.** Bars fixed here; do not re-derive.

## Engineering notes (reusable)

- **Empirical trading-day index** (`calendar_features.py`): trading day iff ≥50 distinct symbols have
  bars (`.cache/intraday_overnight/ohlc.parquet`), static `is_trading_day` only for the tail after
  2026-06-08, overlap-agreement assert between sources. This caught THREE static-table gaps: no
  pre-2023 holidays (April-2019 OPEX was Thursday 4/18 — Good Friday on the 3rd Friday), the
  2018-12-05 closure, and **2025-01-09 (Carter mourning) missing even in-window** (pinned directional
  exemption). Production fix chipped separately. Amendment 1 in the prereg records the source change,
  locked pre-run.
- Harness: `mine.py` (+ `stats.py` CR1/Bayesian copied from peak_fakeout per its copy-not-import
  rationale, `calendar_features.py`, `prep_mirror.py`, `diagnostics.py`). Outputs in
  `.cache/wave_cycle_mine/` (cohort_stats/features/mirror parquets; gate columns persisted per-cell).
  Builder-tiered: Sonnet implementer, 2 rounds, ~630k subagent tokens, ~40 min build + 7 min patch;
  orchestrator audited the PIT loop, calendar core, actionability adjudication, and this verdict.

## Do-not-retry scope

- **W-calendar axis is closed**: OPEX-cycle/TOM/DOW/month/quarter cells on funded option outcomes.
  (DOW was already twice-burned: weekly_proximity dampener null; overnight-sleeve Friday-theta.)
- **S-family re-mines**: perm_entropy/lz76/kaufman as standalone conditioners are null here — don't
  re-run them; vr5 is parked with its bars locked above. A NEW path-structure feature class would
  need its own prereg and must cite this file.
