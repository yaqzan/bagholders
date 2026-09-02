# Wave/Cycle Structure Mine — PRE-REGISTRATION

**Date locked:** 2026-07-16 ~21:45 ET (before any feature was computed or any outcome read).
**Run type:** /research weeknight (~12h to Fri 2026-07-17 09:30 open). Read-only mine — no scoring
code touched, no MC.
**User hypothesis (verbatim intent):** "I would like to find wave-like patterns in the market we can
algorithmically statistically benefit from" — following the Voynich entropy-rate discussion
("the drop as you add context is the signal"), i.e. structure-vs-noise / periodicity conditioning.

## Triage — what is already closed and may NOT be re-tested here

| Axis | Verdict | Where |
|---|---|---|
| Market-Wave / SPY-breadth collapse contraction | Shipped as MWDD+BDIV; literal core = crash-artifact trap | `experiments/spy_breadth_corr_dd/` |
| Sector Market Wave score transform | RETIRED v71 (crash-artifact) | `experiments/integrity_audit_2026_06/` |
| Folded Participation Wave | NULL | `experiments/mcd_cwwd_wvd_recal/` |
| VIX weekly-MACD / velocity / acceleration | NULL (only LEVEL works = RXDD) | `experiments/vix_weekly_v70/` |
| Equity-curve wave timing of aggression | NULL for speed; Sentinel DD-shaver only | `experiments/concentration_2x/` Track A |
| Breadth-velocity | McClellan-redundant (corr +0.72) | `experiments/dd_residual2_v70/` |
| MA-lattice price geometry (349 cells) | COMPREHENSIVE NULL 2026-07-14 | `experiments/trend_ma_lattice/` |
| Day-of-week **wadj dampener** (scoring-stability mechanism) | NULL — per-trade DOW WR overfit, 5/9 sign-fail | `experiments/weekly_proximity/` |
| Peak/fakeout texture interaction | CLOSED-NULL 2026-07-15; TEXTURE parked Dec-2026 OOS (do not touch) | `experiments/peak_fakeout/` |
| Entry timing (close vs next-open, retracement delay) | CLOSED — realism haircut, anti-selective | `experiments/{intraday_overnight,retrace_entry_v70}/` |

**The genuinely untested residuals this study targets:**
1. **W-family (market calendar / cycle phase):** OPEX-cycle position, turn-of-month, quarter-end,
   month-half, day-of-week — NO registry entry has ever mined outcome seasonality on the funded
   ledger (grep confirmed 2026-07-16: no hits for opex/expiration-cycle/turn-of-month/seasonal/
   month-end in known-issues, archive, NEW_LEADS, traps). Escape story from the 47% wall: expiration
   cycles act on the **vol path** (pinning, vol-crush, hedging flows), which dominates the option
   barrier outcome — not on direction.
2. **S-family (per-name path-structure / "waviness"):** variance ratio, permutation entropy, LZ
   complexity, Kaufman efficiency — the parked NEW_LEADS "return path-quality" sliver (logged
   2026-07-16), executing its written retry protocol exactly (CR1 clustered-z harness, PIT-mcap +
   vol controls, cross-sectional rank/tercile form).

**Honest prior:** null ~85% (per-family). Known dilution: the outcome window is 15 trading days
(~3 calendar weeks), spanning a full OPEX transition for most entries — any daily-phase effect from
the literature is smeared. DOW prior additionally lowered by the weekly_proximity overfit result.
The value of a clean null is real: it closes the *entire* wave-shaped axis family at EOD resolution
(all literal-wave forms already closed; this covers the calendar-periodic and path-rhythm forms).

## Substrate (identical to peak_fakeout, locked)

- **Primary:** `.cache/trend_ma_lattice/ledger_v74_full.parquet` — funded v74 75+ CALL ledger,
  N=5,854, 697 symbols, 2021-01-01→2026-06-15 (holdout cutoff enforced via
  `experiments/_holdout.py`); rows with entry date > 2026-06-08 dropped (OHLC coverage, locked by
  the peak_fakeout precedent) → **N=5,810**.
- **Outcomes:** apex option EV {TP +0.30 / SL −0.70 / expiry −0.40}. **Primary:** apex WR.
  **Co-primary:** plunge rate = P(apex_ev = −0.70). Secondary (continuous): apex EV. Known base
  rates (from peak_fakeout, same substrate): WR 70.1%, plunge 23.3% — self-test must reconcile.
- **Controls source:** `.cache/peak_fakeout/features_v74_full.parquet` (vol20, runup20_sig,
  pit_mcap_b, vix_close, days_to_earnings) + `.cache/trend_ma_lattice/features_v74_full.parquet`
  (control_pct_ema50, repl_win, repl_ev) + ledger stored components (overall, trend). Fallback:
  recompute per peak_fakeout/features.py definitions.
- **Windows:** 2021 / 2022 / 2023 / 2024 / 2025+ (add_window_column convention).
- **Statistical machinery:** copied from peak_fakeout/mine.py (CR1 date-clustered sandwich gates;
  naive binomial z reported but never gates; Jeffreys per-window + hierarchical pooling;
  finX-masked controlled fits; MIN_N_POOLED=30, MIN_N_WINDOW=10, MIN_CELL_CONTROL_SIDE=20).

## W-family features (pure calendar functions of entry date — PIT-trivial)

OPEX day := 3rd Friday of the calendar month, moved to the preceding trading day when that Friday
is a market holiday (via `database.utils.trading_calendar.is_trading_day`; unit test locked: April
2025 OPEX = **2025-04-17**, since Good Friday 2025-04-18 was the 3rd Friday). `days_to_opex` :=
count of trading days strictly after entry up to and including the next OPEX day (entry on OPEX
day → 0).

| # | Feature | Buckets | Notes |
|---|---|---|---|
| W1 | days_to_opex | {0-3, 4-8, 9-13, ≥14} | the monthly options-expiration cycle phase |
| W2 | opex_week | binary: entry in the Mon-Fri week containing OPEX day | classic formulation; **shares one locus with W1** |
| W3 | turn_of_month | binary: last 3 + first 2 trading days of month (trading-calendar ranks) | the documented TOM anomaly window |
| W4 | day_of_week | Mon/Tue/Wed/Thu/Fri | exploratory (prior lowered — weekly_proximity) |
| W5 | month_half | binary: May-Oct vs Nov-Apr | sell-in-May |
| W6 | quarter_end_week | binary: last 5 trading days of Mar/Jun/Sep/Dec | window dressing |

17 cells, ~5 independent loci.

## S-family features (PIT from trailing closes ≤ entry date; ≥126 prior bars else null)

Daily log returns r_t = ln(C_t/C_{t−1}), window ending at the entry-date close (signal is computed
from that close — consistent with lattice/peak convention).

| # | Feature | Definition | Buckets |
|---|---|---|---|
| S1 | vr5 | Lo-MacKinlay variance ratio: Var(overlapping 5-day sums)/(5·Var(1-day)), trailing 120 returns (≥100 valid) | terciles |
| S2 | perm_entropy | permutation entropy, order m=3, τ=1, trailing 60 returns, stable-argsort tie-break, normalized by ln 6 | terciles |
| S3 | lz76 | LZ76 complexity of sign sequence (r>0→1 else 0), trailing 60 returns, normalized c = C·log2(n)/n | terciles |
| S4 | kaufman_er20 | \|P_t − P_{t−20}\| / Σ\|P_i − P_{i−1}\| over 20d (prices) | terciles |

12 cells, 4 loci. Terciles computed once on the pooled substrate column (rank-based convention).
Report (diagnostic, not gated): S×S and S×{vol20, runup20_sig, pit_mcap} correlation matrix —
the expected failure mode is "S ≈ vol/trendiness re-label," which the controlled fit must expose.

**Total treated cells ≈ 29 × 2 gated outcomes → expected false |z|≥3 ≈ 0.16 under the global null.**

## Finding bar — locked (a cell is a FINDING only if ALL)

1. **|z_clustered| ≥ 3** (CR1 sandwich, clusters = entry date) on apex WR or plunge rate. Calendar
   features are date-level → effective N ≈ #distinct entry dates (~1,300); the naive z is reported
   for reference and NEVER gates.
2. **|t_controlled| ≥ 2.5** — clustered logit (WR/plunge) or clustered OLS (EV) with controls
   [overall, trend, vol20, runup20_sig, control_pct_ema50, ln(pit_mcap_b), vix_close], inputs
   finite-masked (G47).
3. **P(sign-consistent ≥ 4/5 windows) > 0.90** (Jeffreys + hierarchical pooling).
4. **N ≥ 150** in-cell AND bucket prevalence ∈ **[5%, 95%]** of the substrate (G49 degenerate-bucket
   guard).
5. **Replication:**
   - S-family: sign-match on the full-universe 30dte_apex mirror (repl_win breadth replication).
   - W-family: sign-match on a **2016-2020 pre-substrate era slice** (fresh mirror pull: v74 75+
     rows 2016-01→2020-12 joined to the 30dte_apex barrier cache) — a genuinely different market
     era, the honest test for calendar flukes. **Fallback (locked):** if the era slice is not
     materializable by ~05:00 ET, the repl_win breadth check substitutes but caps the cell at
     NEAR-MISS (calendar effects need era-independence; breadth replication shares the same dates).
6. Actionability is REPORTED, not gated: cohort WR below call BE 45%, or EV spread ≥ 0.03 vs rest.

Cells passing ≥4 of 5 legs → near-miss ledger for the Dec-2026 OOS re-read (candidate only).

## Decision rule (locked)

- **0 findings →** close BOTH families: FINDINGS.md + known-issues WHAT-NOT-TO-DO entries
  (calendar-phase outcome conditioning; path-complexity conditioning — the NEW_LEADS sliver's
  registered retry, executed) + NEW_LEADS merge + memory. Combined with the already-closed literal
  wave mechanisms, this closes the wave-shaped axis at EOD/daily resolution.
- **≥1 finding →** route by family: W → Stage-3 calendar-phase sizing tilt (or Stage-1 admission
  conditioner if score-side); S → Stage-1 conditioner via /find-and-ship-alpha. Given the ~12h
  budget: **STAGE with SHIP_HANDOFF.md** (G13 — no rushed ship); a Stage-3 B-screen may be queued
  overnight if the finding is strong AND wiring is trivial, but the ship decision waits for the
  standard gates.

## AMENDMENT 1 — calendar source (2026-07-16 ~22:25 ET, locked BEFORE the full run / any outcome read)

The W-family trading-day source is amended from the static `database.utils.trading_calendar.is_trading_day`
(named above) to an **empirical index** derived from observed market bars (`.cache/intraday_overnight/
ohlc.parquet`; a date is a trading day iff >=50 distinct symbols have a bar), with the static function as
fallback only for the index tail after 2026-06-08. Reason (builder-surfaced, orchestrator-audited): the
static NYSE_HOLIDAYS table covers only 2023-2026, so the 2016-2020 era-replication leg AND the 2021-2022
portion of the main substrate would mislabel days_to_opex / TOM / quarter-end near ~9 holidays/yr; the
table is additionally missing the 2025-01-09 closure even in-window (caught by the new overlap-agreement
validation; pinned as the single directional exemption — any other disagreement hard-fails). All locked
unit anchors unchanged and passing; two era anchors added (April-2019 OPEX = 2019-04-18; 2018-12-05
closed). No bucket definitions, bars, controls, or outcome mappings changed; bucket populations shifted
marginally (e.g. W1 4-8: N 98->100) purely via corrected holiday labels.

## Diagnostics (report-only, never gated)

- Entry-supply by OPEX phase / TOM / DOW — does the scoring system itself have calendar rhythm?
- Per-window base rates (drift check vs peak_fakeout's 70.1%/23.3%).
- S-family coverage (young-listing null counts).
