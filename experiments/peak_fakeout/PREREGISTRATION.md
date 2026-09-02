# Peak-Fakeout Discriminator — PRE-REGISTRATION

**Date locked:** 2026-07-15 ~02:30 ET (before any feature was computed or any outcome read).
**Run type:** /research weeknight (~7h to 09:30 open). Read-only mine — no scoring code touched.
**User hypothesis (verbatim intent):** "lots of buy signals at the top of rallies where the exact
opposite happened and the stock plunged … there is some metric (mcap? volatility? ema? ma?) that can
differentiate the buy signals at peaks that are fakeouts from the breakouts."

## Triage — what is already closed and may NOT be re-tested here

| Axis | Verdict | Where |
|---|---|---|
| EMA/SMA lattice (horizons, crosses, above/below, kernel div, sub-terms) | COMPREHENSIVE NULL 2026-07-14 | `experiments/trend_ma_lattice/` |
| Late/extended-at-top entries underperform | FALSE — WR flat by run-position; delayed entry anti-selective | `experiments/retrace_entry_v70/` |
| Overbought/divergence dampening | NULL — extended calls CONTINUE; dampening deletes winners | `experiments/divergence_dampener/` |
| Score gradient / components / calibration above the gate | INERT — "nothing predicts apex above the gate" | `experiments/verify_value/` |
| EOD fakeout-proneness proxies (boundary dist, component disagreement, volume-class proximity, regime-reapply) | NULL at N=46-52k; intraday ABSORPTION/CLIMAX family PARKED to Dec-2026 OOS (do not touch) | `experiments/miss_regime_fakeout/` |
| Mcap dampener (MCD) | Retired v71 — survivorship (8.2pp → 2.6pp on PIT) | `experiments/integrity_audit_2026_06/` |
| Market-level %-at-ATH / narrow breadth | INVERTED for our buy-weakness sleeve + MWDD-redundant | `experiments/breadth_ath_dd/` |
| Regime-conditioned component reweighting at the gate | NULL across classifiers | `experiments/regime_reweight/` |

**The genuinely untested residual this study targets:** the **two-way interaction** — *conditional
on* a peak-state entry (price at/near its own multi-month high after a strong run), does any
feature separate the plunge cohort from the continuation cohort? All priors above tested features
*marginally* (unconditional), and none used per-name **price-high structure** (as opposed to MA
distance), **call-side earnings proximity** (PESS is put-side [16,20]; EARN_BOOST retired — no live
call mechanism), or **path-shape of the run-up** (parabolic acceleration, climax day, base age).
MISS_CANDIDATES #7 ("participation can mark blowoff/exhaustion") is an open lead this overlaps.

**Honest prior:** heavily null (~85-90%). The value of the null is real: this is the 3rd+ session
a form of this user intuition has surfaced (retrace_entry, divergence, now this); a pre-registered
interaction-level close ends it with numbers.

## Substrate

- **Primary:** `.cache/trend_ma_lattice/ledger_v74_full.parquet` — funded v74 75+ CALL ledger,
  N=5,854, 697 symbols, 2021-01-01→2026-06-15 (holdout cutoff enforced), apex outcome + stored
  score/component controls, self-tested no-look-ahead (2026-07-14).
- **Replication (secondary):** full-universe `30dte_apex` barrier DuckDB mirror, 75+ rows 2016+
  (bigger N, scaled-barrier analog) — a PRIMARY-bar cell must replicate in sign there.
- Outcome mapping (identical to lattice): apex option EV {TP +0.30 / SL −0.70 / expiry −0.40},
  same-bar-tie-loses. **Primary outcome:** apex WR. **Co-primary (the user's actual pain): plunge
  rate** = P(SL-touch / worst-leg outcome), and MAE_sigma as the continuous tail read.

## Peak-state (conditioning) definitions — locked

- **P_high:** entry close within **3%** of the rolling **126d** high.
- **P_run:** 20d run-up ≥ **+1.5σ** (σ = 60d daily close-to-close vol, annualization-free σ·√20 scaling).
- **P_both:** P_high ∧ P_run (the user's archetype: "top of a rally").
- Tuning rule (locked, discretion-free ladder, applied ONCE on **prevalence only**, evaluated
  BEFORE any outcome column is joined — never on WR/EV):
  P_high 3% → if prevalence >40%: 1.5% → if still >40%: 252d-high @1.5% → if <15%: 5%.
  P_run 1.5σ → if >40%: 2.0σ → if <15%: 1.25σ → if still <15%: 1.0σ.
  P_both: if <8% after the above, use the wider parent rungs (P_high 5% and/or P_run 1.25σ)
  for P_both only.
- Clustering (locked): the gate's z is **date-cluster-robust** (CR1 sandwich, clusters = entry
  date). The lattice's naive binomial z_raw is reported for comparison but does NOT gate.
- Coverage drop (locked before compute): the 44/5,854 ledger rows with entry date after the OHLC
  parquet's max (2026-06-08) are dropped (0.75%, coverage-driven, not outcome-driven) → N=5,810.
- Outcome labels confirmed 3-valued before feature compute: apex_ev ∈ {+0.30 TP (N=4,103),
  −0.70 SL (N=1,362), −0.40 expiry (N=389)} → **plunge := apex_ev = −0.70** (base 23.3%).

## Feature families (computed strictly point-in-time from data ≤ entry date)

Within each peak-state, bucket each feature (terciles unless noted) and test WR/EV/plunge vs the
peak-state base rate:

| # | Feature | Definition (PIT) | Prior status |
|---|---|---|---|
| F1 | PIT mcap tier | current mcap × (price_t / price_now) proxy, terciles | MCD retired; tier-as-discriminator-at-peak untested |
| F2 | Realized vol 20d | σ20 of daily returns, terciles | partitions flat marginally; interaction untested |
| F3 | Parabolic ratio | runup_5d(σ) / runup_20d(σ) — acceleration into the peak | NEW |
| F4 | Climax day | max single-day close-to-close gain in last 10d, in σ units, terciles | NEW (MISS #7 adjacent) |
| F5 | Up-day streak | consecutive up-closes at entry: ≤2 / 3-5 / ≥6 | NEW |
| F6 | High-zone age | days price has been within 3% of the 126d high in the current visit: ≤3 (fresh breakout) / 4-10 / >10 (camped) | NEW — fresh-cross INVERSION prior says expect fresh=worse |
| F7 | Base age | days since price last traded at/above today's close before the current run (capped 252) — "how old is the level being broken" | NEW |
| F8 | Volume z | entry-day volume vs 60d mean (log-z), terciles | amplifier funded-neutral marginally; blowoff-at-peak untested (MISS #7) |
| F9 | Earnings proximity | days to next earnings: ≤7 / 8-21 / >21-or-none | call-side OPEN (PESS is puts-only; EARN_BOOST retired) |
| F10 | Medium-term extension | 60d run-up in σ units (σ60·√60 scaling), terciles — the "late-stage extension" bell-curve idea without weekly-resample look-ahead risk (G28) | "harmful when extended" seen in old 70-74 lead; funded-75+ untested |
| F11 | Gate-driver mix | trend-component share of the gate crossing: trend-dominant vs oscillator-dominant (binary, stored components) | adjacent-null (regime_reweight) — exploratory only |
| F12 | VIX band at entry | <20 / 20-28 / >28 | closed as sizing; peak-interaction exploratory only |

**Excluded by triage (will not be computed):** any MA-lattice cell, raw stoch/rsi divergence,
run-position ordinal (flat), market-breadth %-at-ATH, boundary-distance-to-gate, score velocity.

## Multiplicity bar — locked

Cell budget: 12 features × ≤3 buckets × (3 peak-states + 1 unconditional) ≈ **≤144 treated cells**
→ expected false |z|≥3 ≈ 0.4 under the global null.

A cell is a **FINDING** only if ALL of:
1. **|z_clustered| ≥ 3** on apex WR or plunge rate (z clustered by entry date — market-sync trap);
2. **|t_controlled| ≥ 2.5** — logit (WR/plunge) or robust OLS (EV/MAE) with controls: `overall`,
   stored `trend` component, runup_20σ, pct-from-EMA50, and the peak-state main effect. Inputs
   masked finite (the G47 false-NULL trap);
3. **P(sign-consistent ≥ 4/5 windows) > 0.90** (Jeffreys per-window posteriors, hierarchical pooling;
   windows 2021/2022/2023/2024/2025+);
4. **Interaction delta:** |effect inside peak-state| exceeds |same feature unconditional| by
   ≥ **2pp WR** (or 0.02 EV) — otherwise it is a marginal feature and belongs to the closed axes;
5. **N ≥ 150** in-peak rows in the cell;
6. **Replicates in sign** on the full-universe 30dte_apex mirror.

Marginal (unconditional) cells are report-only, |z|≥4 to even be discussed.

## Decision rule (locked)

- **0 findings →** close the axis: FINDINGS.md + known-issues WHAT-NOT-TO-DO entry + NEW_LEADS/memory;
  the recurring "peak fakeout" intuition is answered at the interaction level.
- **≥1 finding →** design a Stage-1 gate-acting dampener (or Stage-3 sizing tilt if the signal is
  tail-only) on the surviving cell; escalate per ship gates (sharded ReSim A/B → growth gate for
  Stage-1; tape+MC B→C→D for Stage-3). Ship only if fully wired + gate-validated with ≥30-45min
  margin before 09:30 ET; otherwise STAGE with SHIP_HANDOFF.md (G13).
- The near-miss ledger: any cell passing ≥4 of 6 legs is recorded for the Dec-2026 OOS re-read,
  as a candidate only.
