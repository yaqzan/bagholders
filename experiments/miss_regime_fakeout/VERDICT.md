# Miss × Regime × Fakeout Ledger — VERDICT MEMO

**Date:** 2026-07-10 · **Owner:** FABLE (architect) · **Status: CLOSED — NULL (Layer A, per the
pre-registered whole-pass kill criterion); Layer B PARKED to the Dec-2026 OOS unlock.**
**Pre-registration:** DESIGN.md, approved-as-amended (A1–A8) before any mining compute; bars verbatim:
z ≥ 3 clustered, N ≥ 500, replication on BOTH interleaved-year halves; fakeout-reduction measurable on
intraday-swing metrics with per-trade WR neutrality acceptable (WCF precedent).

## Chain of results
1. **The motivating priorities were already closed.** known-issues #7/#9 (whiplash / stability gate)
   CLOSED 2026-06-16; this pass CONFIRMED that closure independently, out-of-band: pre-cutoff fakeout
   events by version = 61/16/60/11 (v60/v69/v70/v71) vs **0/1/2 (v72/v73/v74)**. v74 residual fakeout
   rate 0.26–0.35% of (symbol,date) groups — far below the 5% actionable floor.
2. **Power wall (holdout lock × log start):** v74 pre-cutoff intraday substrate = ONE trading day
   (2026-06-15; 2 events). The real v74 sample (46 events post-cutoff, ~2.5/day growth) is
   holdout-reserved — hence the two-layer design.
3. **Phase 0 attribution (holdout-clean, v69+, N=90 events):** the RETIRED `wcf_lift` stage owns
   **72/90** events — the v72 ramp + v73 retirements removed the bulk of the fakeout mechanism.
   Live-mechanism residual: volume=10 (reversal-class ABSORPTION/CLIMAX only 3/10), components=7, boost=1.
4. **Layer A mining — all three licensed candidates FAIL decisively.** Ledger 140,102 rows × 43 cols,
   2016-04-29→2026-06-15 (holdout guard passed in-build + independently reconfirmed); date-clustered
   SEs byte-identical to the gex_test.py prior art; SPREAD_TILT carve-out applied to c2 (73.3% overlap).

   | Candidate | Pooled N (treated) | t_clust | Even/Odd half | Era | Verdict |
   |---|---|---|---|---|---|
   | c1 reversal-volume guard (calls) | 51,655 (4,109) | +0.25 | −0.11 / +0.49 | era-local noise | **FAILS** |
   | c2 admission-boundary spread (carved) | 46,499 | −0.28 | −0.13 / −0.29 | era-local noise | **FAILS** |
   | c3 regime-boundary crossing (calls) | 51,655 (43,121) | +0.29 | −0.13 / +0.52 | era-local noise | **FAILS** |

   12-cell regime cross: max |t_clust| = 2.46. **Global max across ~30 tests = 2.55**, on a
   NON-licensed secondary (c1 put-side; puts are OFF portfolio-wide — recorded, not actionable).
   No candidate reached Phase 3 orthogonality (per design gating). Era-half sign flips (c2 +1.80→−2.01,
   c3 +2.04→−1.49, both sides < 3) are the OSK-class instability the dual split exists to expose — at
   noise magnitude here.
5. **Kill criterion applies mechanically → axis CLOSED NULL.**

## What this means
- **EOD fakeout-proneness proxies carry no outcome signal** on honest v74 history: boundary distance
  to the 70/75 gates, admission-zone component disagreement, volume-classification proximity, and
  regime-reapply gate-crossing are all |t_clust| < 0.3 at N = 46–52k. Do not re-mine these axes.
- The residual intraday fakeout class is (a) tiny (≪ the 5% floor), (b) mostly attributable to
  retired mechanisms in the pre-cutoff record, (c) not predictable from EOD features at the gate.
- **Layer B stays PARKED, pre-registered in DESIGN.md:** after the Dec-2026 OOS evaluation completes
  and the cutoff re-locks forward, the v74 post-cutoff intraday window rolls in-sample; re-run the
  Phase-0 attribution + the WCF-precedent swing metric at the SAME locked bars. The ABSORPTION/CLIMAX
  same-day transition guard (c1's intraday form — the one genuinely new, live-confirmed family, ±38–41pt
  swings, ungated by INTRADAY_TYPE_CONF_GATE) is the named follow-up hypothesis there. Do not spend it early.
- No adversarial verify was run: nulls don't receive one by charter. Harness sanity instead: clustering
  implementation byte-identical to the GEX prior art, lever/tilt constants verified against
  strategy_config.py, holdout guard enforced in-build, and non-degenerate secondary structure (sensible
  near-2 t's in plausible places) rules out a dead-join false null.

## Artifacts
DESIGN.md (pre-registration + A1–A8 amendments + Layer-B park) · results/{phase0_attribution,
phase1_build, candidates_report, regime_cell_cross, mining_results}.{json,txt} ·
results/ledger_v74.parquet (large, gitignored) · build_ledger.py / mine_candidates.py /
regime_cell_mining.py / write_mining_results.py / phase0_attribution.py · queue task #582 (ledger build).
