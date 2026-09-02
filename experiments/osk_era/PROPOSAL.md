# OSK Score Modifier — Candidate Shapes (Phase C, design doc only)

**Owner:** Sonnet (Phase C worktree agent) · **Date:** 2026-07-08 · **Status: PROPOSAL, not
shipped.** No changes to `scoring.py` / `core.py` accompany this document. The orchestrator
reviews this before any sweep is authorized.

## 0. What this modifier is trying to capture

era_conditioning/DESIGN.md's Phase A confirmed OSK (`opt_skew = 10%-OTM-put-IV − 10%-OTM-call-IV`)
as the one RAMPING in-era cell (12/12 rolling windows, |beta| 0.032→0.083 front-to-back-third) out
of three crowding candidates tested; osk_validation/VERDICT.md separately confirmed the sign/effect
is measurement-robust (Polygon D2 replication: spearman +0.090, 75+ +0.114, clustered t +3.38) but
**regime-conditional** — absent pre-2025 (backward-OOS spearman −0.002, 2022-bear-tail −0.107).
Direction: **higher skew (relatively more OTM-put fear-pricing) → more positive forward call pnl15**
on the confirmed 2025-26 in-era cohort. All three shapes below apply that sign, on the CALL side
only (`overall >= 50`; OSK's confirmed evidence never covered puts and this book is calls-only).

## 1. Evidence substrate now in hand (this session)

- `experiments/osk_era/build_osk_ledger.py` (queued task #536, MAIN's shared daemon, 52s) built the
  our-recipe ledger over 2025-02-01..2026-06-15, `overall>=70`: **N=4,667 requested, 1,998 chain-hit
  (42.8%), only 730 RIPE (15.6% of total; 20.8% of the 75+ subset)** — see `.cache/osk_era/
  osk_era_ledger.parquet` + `build_manifest.json`.
- **Load-bearing finding: build_iv.py's historical "pnl15" coverage (~99%) was never a true ripeness
  check.** It takes `prices[min(14, len(prices)-1)]` — with fewer than 15 forward trading-day price
  rows for a contract, it silently substitutes the LAST available price as if it were the 15th-bar
  price. Cross-validated directly: MAS/2025-02-11 (a stored row in `.cache/iv_skew/iv_ledger.
  parquet`) reproduces atm_iv=0.2583 and skew=0.0396 EXACTLY under this session's recipe, but that
  contract only had 10 forward price rows in the 24-day lookout — build_iv.py reported a "pnl15" of
  −0.4951 for what is actually a ~10-trading-day return, not 15. **This means the historical
  +0.110/+0.145/+0.114 OSK statistics were computed on a ledger where a majority of "pnl15" values
  are truncated-horizon proxies, not verified 15-bar returns** (this session's stratified 81-row
  scan across the whole window found the same pattern everywhere, not just near the cutoff: ripe
  rate ~35% of chain-hit rows, consistently, in 2025-Q1 through 2026-Q2 alike). This doesn't reverse
  the sign finding — VERDICT.md's own bars were rank/spearman-based and reasonably robust to this —
  but it means the TRUE usable evidence N for any Stage-1 gate is the ripe count (730 / 179 / 39 at
  70+/75+/80+), not the larger chain-hit or requested counts, and any future re-mine of OSK evidence
  should use `build_osk_ledger.py`'s ripe flag (or an equivalent >=15-row check), not build_iv.py's.
- `experiments/osk_era/supply_check.py` — real ground-truth cross-check (task #536's output joined
  back onto the full window) vs. the two coverage_map-only estimates:

  | Cohort | N | span-based (naive) | density-adjusted | **REAL (MySQL-verified)** |
  |---|---|---|---|---|
  | ALL 70+ | 4,667 | 62.5% | 34.3% | **42.8%** chain-hit / **15.6%** ripe |
  | ALL 75+ | 860 | 70.1% | 38.8% | **50.0%** chain-hit / **20.8%** ripe |
  | 70-74 | 3,807 | 60.8% | 33.3% | **41.2%** chain-hit / **14.5%** ripe |
  | 75-79 | 672 | 72.9% | 40.3% | **51.5%** chain-hit / **20.8%** ripe |
  | 80+ | 188 | 60.1% | 33.5% | **44.7%** chain-hit / **20.7%** ripe |

  The naive span-based read (the literal "date within symbol's tracked chain span" definition)
  overstates real coverage by ~20pp at every cohort — exactly the growth-gate supply-fallback trap
  this deliverable was named for. **Any W-gate run on this modifier must use the REAL ripe counts
  (730 / 179 / 39), not a coverage-map approximation.** The 80+ tier's N=39 ripe rows is almost
  certainly below every Stage-1 N-floor (100/300/500) for a confident per-bucket W4 read — expect
  80+ evidence to be report-only for a long time, not a hard gate input.

## 2. Where any shape plugs in (post-component, pre-regime)

`database/utils/scoring.py`'s v74 pipeline computes `weighted_sum` from the technical components,
then applies the volume-amplifier CONVICTION/ABSORPTION/REJECTION blend (~line 1234-1257) to that
same `weighted_sum`, and *only then* snapshots `pre_regime_overall = clamp(weighted_sum)` (line
1259) before the regime multiplier (`_eff_mult`, mis_stress softener, ~line 1270+) and the long tail
of downstream gradient dampeners (CAP/EXH/ext-focal, WCF/CWCF/CWWD/CSWC, PCD/MCD/ICH, SCW, etc.)
that follow. **All three candidates below plug in at the exact same pipeline position as the volume
amplifier — an additive (or blend-style) adjustment to `weighted_sum`, applied immediately before
the `pre_regime_overall` clamp** — not appended after `_pre_boost_overall` (line 1472) where the v74
lean stack already retired one dampener tail, and not mixed into the regime-multiplier math itself.
This means the OSK adjustment is regime-eligible for free: it gets compressed/expanded by
`_eff_mult` exactly like every other component-driven point of `overall`, inherits the mis_stress
softener, and is subject to the same CAP/EXH gradient dampeners downstream — no special-casing
needed elsewhere in the function. It is orthogonal to (applies before, and is unaware of) the
WCF/CWCF/CWWD/CSWC/PCD/MCD/ICH/SCW dampener chain.

**Universal supply-degradation rule (applies to all three shapes, non-negotiable):** if the daily
feature build finds no valid ATM-call + both OTM legs for `(symbol, date)` (the majority case — 57%
of 70+ signals per the real ground truth above), the modifier contributes **exactly 0.0** — the
`weighted_sum` adjustment block is skipped entirely, not defaulted to a pooled-average skew or any
other imputed value. This is the modifier-level mirror of "no fallback coverage" from the
growth-gate trap.

## 3. Rolling regime guard — shared mechanics (proposed, all three shapes)

- **Data source:** the era_conditioning Phase B ledger (`build_forward_ranks.py` →
  `.cache/era_conditioning/forward_ranks.parquet`), NOT this session's static
  `osk_era_ledger.parquet`. This session's ledger is the one-time Stage-1 SHIP-DECISION evidence;
  Phase B's ledger is the idempotent, continuously-growing feed the LIVE guard reads post-ship.
  Guard computation must filter to `ripe==True` (or an equivalent >=15-trading-row check) — the
  same ripeness discipline this session's build applied, since Phase B's builder should inherit it
  rather than build_iv.py's truncation behavior.
- **Metric:** trailing-6-calendar-month `spearman(skew, pnl15)` over ripe rows only, recomputed as
  of each cadence tick (below). Pre-registered floor from the task brief: 0.03.
- **Cadence + mechanism:** monthly, on the first trading day post-month-end (piggyback on the
  existing `trader temporal-refresh` scheduling slot rather than inventing a new cron path). A
  small queued job (`--priority low`, cheap — a few thousand rows of spearman on a cached parquet)
  writes ONE small state file, e.g. `.cache/osk_era/guard_state.json` = `{as_of_date, spearman_6m,
  n_ripe_6m, guard_multiplier}`. The scoring hot path reads that one file per run (cached in
  memory for the day) — no live DB query and no recompute inside `Score.build()`, mirroring how
  `MarketRegime` is computed once/day and reused across all symbols.
- **Guard-to-multiplier shape (gradient, not a cliff):** this repo's own house style prefers ramps
  over hard cutoffs (the WCF 27/28 binary→ramp fix is the canonical precedent). Propose:
  `guard_multiplier = clamp((spearman_6m - OSK_GUARD_ZERO) / (OSK_GUARD_FULL - OSK_GUARD_ZERO), 0, 1)`
  with `OSK_GUARD_ZERO = 0.00` and `OSK_GUARD_FULL = 0.06` (twice the pre-registered 0.03 floor) —
  so the modifier is at full designed strength once trailing spearman clears 0.06, linearly decays
  to zero at spearman<=0.00, and 0.03 (the breach line) sits at exactly half strength rather than
  being a step function. Every shape's final adjustment is `raw_shape_value * guard_multiplier`.
- **Fail-safe:** if `guard_state.json` is missing or stale (`as_of_date` older than ~45 days — i.e.
  the monthly job didn't run), `guard_multiplier` defaults to **0** (decay-to-neutral), not to 1
  (fail-open). A dead cron job must never silently leave a stale-but-"hot" modifier running.
- **N caveat on the guard itself:** at ship time, the trailing-6m window will contain roughly the
  same ~15-20% ripe fraction found in section 1 — a 6-month window at the current signal cadence is
  likely a few hundred ripe rows pooled (not per-bucket), which is adequate for a pooled spearman
  read but too thin to recompute the guard separately per score bucket. The guard is intentionally
  ONE pooled number across all option-covered calls, not a per-bucket guard.

## 4. Candidate shapes

### Candidate A — Rank-based additive lift, capped ±X points (simplest, most auditable)

- **Mechanism:** maintain a pooled trailing reference distribution of `skew` (e.g. trailing 18mo of
  ripe + unripe chain-hit rows pooled across symbols — unripe rows still have valid skew, only
  pnl15 is unusable, so the reference distribution can use ALL chain-hit rows, not just ripe ones).
  Convert today's raw skew to a percentile rank `p` (0-100) against that distribution. Adjustment =
  `OSK_LIFT_CAP * (2*p/100 - 1) * guard_multiplier`, added to `weighted_sum`. `OSK_LIFT_CAP`
  proposed starting point: 3 points (conservative given a confirmed spearman of ~0.09-0.11 — this repo's
  existing single-mechanism dampeners/lifts are typically low-single-digit points; the sweep tunes
  the exact cap, this is a design placeholder not a calibrated value).
  A fixed pooled reference distribution (not per-day cross-sectional ranking) avoids rank noise on
  days when only a handful of symbols carry coverage.
- **Stage-1 evidence to license:** W1 cohort z-score on the ripe-only era ledger, top-rank-quintile
  vs bottom-rank-quintile WR15 delta (mirrors VERDICT.md's own quintile methodology, so it is
  directly comparable to the already-existing validation evidence). W4 per-discrete-bucket
  non-regression at 70-74/75-79 (N=551/140 ripe — 75-79 sits near typical N-floors, treat cautiously);
  80+ (N=39 ripe) is almost certainly report-only, not gateable, until Phase B accrues more forward
  N. W5 growth verdict computed with `lambda_eff` using the REAL 15.6%/20.8% supply numbers, not a
  coverage-map estimate — a naive supply assumption would materially overstate `ebar` here.

### Candidate B — Sigmoid (tanh) on skew z-score (smoothest, most sample-efficient)

- **Mechanism:** `z = (skew - pooled_trailing_mean) / pooled_trailing_std` (same pooled reference
  population as Candidate A). Adjustment = `OSK_LIFT_CAP * tanh(OSK_Z_K * z) * guard_multiplier`,
  added to `weighted_sum`. `OSK_Z_K` proposed starting point ~1.0 (so a 1-sigma skew reading produces
  `tanh(1) ≈ 0.76` of the cap; saturates by z≈2.5-3, which caps the influence of a single
  thinly-traded/noisy skew reading — matches the recipe's own known fragility, per osk_validation/
  VERDICT.md's D1 finding that OTM-leg strike selection is the dominant source of cross-vendor skew
  disagreement).
- **Stage-1 evidence to license:** this shape is the most direct match to VERDICT.md's own Test 3
  (orthogonalized OLS: `pnl15 ~ skew + semivol_r + overall + stock_r20`, plain + date-clustered t) —
  license via replicating that exact regression on THIS session's era ledger (ripe rows only,
  restricted to the call-side / 70+ universe used elsewhere in this doc), requiring clustered
  t >= 2.0-2.5 (VERDICT.md's own D2 replication bar), consistent sign across at least the 3
  best-populated quarters (2025-Q4/2026-Q1/2026-Q2 per the ripe-count table), plus the standard
  W1-W6 pass. Because it's continuous (no binning), this shape needs the least N per evidence cell
  and is the LEAST exposed to the thin-80+-N problem — recommend this as the lead candidate for the
  first sweep given the real supply picture in section 1.

### Candidate C — Tiered boost at skew quintiles (discrete, closest to existing dampener style)

- **Mechanism:** bucket pooled-reference skew into quintiles Q1(lowest)…Q5(highest). Fixed
  per-tier point adjustment, asymmetric toward the call-favorable side (matching this book's
  calls-only structure): e.g. `Q5:+4, Q4:+2, Q3:0, Q2:-1, Q1:-2`, each `* guard_multiplier`. This is
  the closest in spirit to existing calibrated dampeners (WCF/RXDD-style named constants,
  `OSK_Q5_LIFT` … `OSK_Q1_LIFT`), and directly mirrors VERDICT.md's own already-reported
  quintile-WR framing (34.5%→47.4% WR spread top-vs-bottom quintile on the Polygon D2 replication).
- **Stage-1 evidence to license:** per-quintile WR15 (and option-TP) delta vs. baseline on the era
  ledger's ripe rows — directly reusing VERDICT.md's quintile-table methodology. **N caveat:** 730
  ripe rows at 70+ split five ways is ~146/quintile before even reaching the 75+ overlay — likely
  under or right at typical N-floors for a confident per-tier W4 read at 70+, and thin-to-unusable
  at 75+ (179 ripe / 5 ≈ 36/quintile) or 80+ (39 ripe, unusable as 5 tiers). **Recommend deferring
  Candidate C until Phase B's forward-ranks ledger has banked its first pre-registered re-read
  (N>=60 forward trading days, ~2026-10)** so the quintile cells have enough pooled N (backward
  ledger + forward accrual) to clear W4 without collapsing to single-digit per-cell counts.

## 5. Recommendation (for the orchestrator's review, not a final call)

Given the real supply/ripeness picture (section 1), lead with **Candidate B (sigmoid-on-z)** for
the first Stage-1 mining pass — it needs the least N per evidence cell, maps most directly onto the
regression form VERDICT.md already validated, and degrades gracefully (via `tanh` saturation) against
the known OTM-strike-selection noise in the recipe itself. Treat **Candidate A** as the fallback if
B's regression evidence is ambiguous (rank-based is more robust to a badly-shaped z if the pooled
reference distribution turns out to be fat-tailed). **Defer Candidate C** until forward N accrues —
attempting a 5-way tiered split on today's ripe counts risks exactly the kind of small-N overfit this
repo's ship-gate reform (W4 noise-aware bucket rule) already exists to catch. All three inherit the
same plug-in point (section 2), the same rolling guard (section 3), and the same hard
supply-degradation rule (neutral when uncovered) — none of this requires touching `scoring.py` or
`core.py` until a shape has cleared Stage 1.
