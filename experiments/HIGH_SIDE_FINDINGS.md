# HIGH-side band investigation — running findings (session 4)

**Question**: Why do HIGH-side scores ≥90 collapse to negative EV (-18.4%) when 80-84 is the only positive band? What differentiates bands across 70-99?

**Cell baseline** (from prior session): best HIGH cell = K=1.0σ / M=2.0σ / W=63 trading days. Universe-level negative across cells; 80-84 alone is positive.

**Approach**: extend `experiments/probe_maturity_acceleration.py` with HIGH-side band-level analyses. Iterate.

---

## Iteration 1 — band x cell EV table + cross-band features (1095 days, V3 id=3)

### Band counts (1095d, full universe)
| Band | N |
|---|---|
| 70-74 | 5842 |
| 75-79 | 1674 |
| 80-84 | 492 |
| 85-89 | 203 |
| 90+ | 43 |

### EV per band at K=1.0/M=2.0/W=63 (best HIGH cell)
| Band | EV | p_win |
|---|---|---|
| 70-74 | **-1.8%** | 58% |
| 75-79 | -2.6% | 59% |
| 80-84 | **+0.6%** ← only positive band | 62% |
| 85-89 | -2.5% | 58% |
| 90+ | **-19.1%** | 37% |

Confirms the prior session at much larger N.

### Cross-band feature means (entry-time)
| feature | 70-74 | 75-79 | 80-84 | 85-89 | **90+** |
|---|---|---|---|---|---|
| ext | +5.39 | +5.96 | +5.48 | +5.66 | **+16.55** |
| bb_pct | +0.50 | +0.48 | +0.47 | +0.38 | **-1.94** ← impossible |
| vel5 | +0.16 | +1.28 | +1.96 | +2.57 | **-2.04** |
| macdh | -0.65 | -0.66 | **-0.96** | **-1.20** | -0.57 |
| ret_5d_prior | +1.45 | +2.54 | +3.03 | +3.60 | +3.01 |
| ret_20d_prior | +1.50 | +1.43 | +2.64 | +2.91 | -0.51 |
| **vol_ratio** | **+1.07** | **+1.40** | **+2.11** | **+2.41** | +1.48 |
| ema200_dist | +22.64 | +26.73 | +20.91 | +23.85 | +16.62 |

---

## 🚨 FINDING #1 — STOCK SPLIT CORRUPTION OWNS THE 90+ BAND

The 90+ winners and stopouts are **dominated by ORLY** (O'Reilly Auto, 15-for-1 split mid-2024). The sample shows:
```
ORLY 2024-05-14  score=100  ext=-93.66  bb_pct=-6.40   ← post-split closes
ORLY 2024-05-07  score=97   ext=-93.62  bb_pct=-6.60     vs pre-split EMA/BB
ORLY 2024-05-03  score=96   ext=-93.69  bb_pct=-6.82
... (8 more ORLY rows in worst-10 stopouts and best-10 winners)
```
The negative `bb_pct=-1.94` cross-band mean for 90+ is **arithmetic from the corrupted ORLY rows**, not a real signal. CLAUDE.md flagged stock splits as a known issue (`auto_adjust=False` in yfinance, `stock_split_implementations.py` not integrated).

**Implication**: the "90+ collapses to -19.1% EV" finding from the prior session is **largely an artifact of one corrupted symbol**. The real 90+ band is ~30 signals (43 minus ~13 ORLY ghosts), too small to draw conclusions from.

**Action item**: re-run with sanity filter `|ext| < 50` AND `-1 ≤ bb_pct ≤ 2` to strip split-discontinuity rows. Done in iteration 2.

---

## FINDING #2 — vol_ratio scales monotonically with band; the score may be a vol-conviction tag

| Band | vol_ratio mean |
|---|---|
| 70-74 | 1.07 |
| 75-79 | 1.40 |
| 80-84 | 2.11 |
| 85-89 | 2.41 |
| 90+ | 1.48 (split-corrupted) |

The score doesn't actually differentiate setups by mean-reversion quality — it differentiates them by **how loud** the volume signal is (the score is multiplied by `volume_amplifier`, see CLAUDE.md). Higher band = higher chance the entry coincided with a CONVICTION/CLIMAX volume event.

**Hypothesis to test (iter 2)**: filter 70-74 to `vol_ratio >= 2.0` and see if EV climbs to ~80-84 levels. If yes, the score is just a vol-flag and we can replicate the 80-84 "edge" with a 70-74 + vol filter (10x more sample size).

---

## FINDING #3 — 80-84 and 85-89 winners share a fingerprint: prior run-up + cracking macdh

**80-84 winners vs stopouts** (z-score on macdh = -0.33, only material discriminator):
- Winners: macdh = -1.08
- Stopouts: macdh = -0.72

**85-89 winners vs stopouts** (two material signals):
- macdh: winners -1.51 vs stopouts -0.76 (z=-0.54 ★★)
- ret_20d_prior: winners +4.43% vs stopouts +0.80% (z=+0.42 ★★)

**Pattern**: the stocks that work as shorts had a **real recent rally that is NOW cracking** (negative macdh after a positive 20d). Stopouts are stocks at the same score that DON'T have a rally to fade (ret_20d_prior near zero, macdh barely negative — momentum is fine).

This is the textbook fade setup. The score on its own doesn't capture it.

---

## FINDING #4 — 70-74 has zero feature discrimination; pure noise

Every z-score in the 70-74 winner-vs-stopout comparison is < 0.2. The band is huge (5838 signals) but it's a coin flip decorated with naked-put theta decay. Even "winners" only achieve -0.6% to -0.9% spot moves — well inside the typical noise floor — so the puts often lose money even on technical wins.

This is NOT salvageable with any single feature filter on the 70-74 band alone.

---

## FINDING #5 — HIGH filter test: every classical "overbought" filter HURTS

| Filter | base EV | filtered EV | Δ |
|---|---|---|---|
| NONE (baseline) | -1.9% | — | — |
| `ret_20d_prior > +10%` (rallied hard) | | -6.3% | **-4.4pp** |
| `ret_20d_prior > +20%` (parabolic) | | -6.2% | **-4.3pp** |
| `bb_pct >= 0.95` (upper band pierced) | | -5.9% | **-4.0pp** |
| `rsi >= 70` | | -4.9% | -3.0pp |
| `stoch >= 80` | | -4.6% | -2.7pp |
| `ext > +15%` (far from EMA50) | | -4.6% | -2.7pp |
| **`macdh < 0`** | | **-0.9%** | **+1.0pp** |
| **`ret_5d_prior < 0`** | | **-0.7%** | **+1.2pp** |
| **`COMBO: ext>+15 + macdh<0`** | | **-0.9%** (N=357, p_win=65%) | +1.0pp |

**The HIGH-side score's surviving edge is "momentum already cracking" — NOT classical overbought.** Filtering for high RSI/Stoch/BB-pierced makes EV worse. This is the **inverse** of what a normal mean-reversion strategy would do.

**Why?** The score formula has the well-known sign-inversion bug (CLAUDE.md "Open investigation: component-vs-overall sign convention"). Components score HIGH when raw oscillators are oversold. So when you score 80-84 HIGH, the raw oscillators are actually neutral-to-low. When you filter for "raw oscillators high," you filter out the signals the score is structurally biased toward.

The 80-84 sweet spot is the band where the inverted formula happens to coincide with **vol_ratio>2 + macdh just turned negative**. That coincidence pattern is the actual edge.

---

## Hypotheses to test in iteration 2

1. **Strip ORLY/split corruption** and rerun band table. Does 90+ improve from -19% to merely "noisy"?
2. **vol_ratio >= 2.0 filter on 70-74** alone — does it reproduce 80-84 EV?
3. **The 85-89 winning fingerprint as a UNIVERSAL filter on all HIGH bands**:
   `ret_20d_prior > +3%` AND `macdh < -0.5` AND `vol_ratio >= 1.5`
   Apply to the full 70+ universe and measure EV / N.
4. Per-band: rerun the filter test ON each band individually to find band-specific optima.

---

## Iteration 2 — sanitize + hypothesis tests

### Sanitize
Dropped 73 rows from 40 symbols. **ORLY alone = 19 rows.** 90+ band: 43 → 29.

### Band table after sanitize (K=1.0/M=2.0/W=63)
| Band | N | EV | p_win |
|---|---|---|---|
| 70-74 | 5805 | -1.7% | 58% |
| 75-79 | 1658 | -2.6% | 59% |
| 80-84 | 484 | **+0.8%** | 62% |
| 85-89 | 200 | -2.6% | 58% |
| 90+ | **29** | **-18.5%** | **41%** |

**🚨 90+ collapse is REAL (not just ORLY).** Sanitizing barely moved EV (-19.1% → -18.5%). N=29 is small but the p_win=41% is materially below all other bands. The 90+ band genuinely underperforms.

### Cross-band features AFTER sanitize — the 90+ fingerprint changes character
| feature | 70-74 | 75-79 | 80-84 | 85-89 | **90+** |
|---|---|---|---|---|---|
| ext | +5.08 | +5.80 | +4.85 | +6.36 | **+8.24** |
| bb_pct | +0.50 | +0.50 | +0.51 | +0.51 | **+0.63** |
| vel5 | +0.11 | +1.14 | +1.48 | +2.50 | **+4.06** |
| ret_5d_prior | +1.32 | +2.35 | +2.43 | +3.50 | **+5.38** |
| macdh | -0.65 | -0.67 | -0.99 | **-1.22** | **-0.15** ← back near zero |
| vol_ratio | +1.06 | +1.38 | **+2.10** | **+2.42** | +1.66 |

## 🚨 FINDING #6 — The 90+ band is firing on MID-RALLY momentum, not exhausted tops

The 80-84 / 85-89 bands have **macdh deeply negative** (-0.99 / -1.22) — momentum already cracking. At 90+, **macdh comes back to -0.15** (near zero, momentum still pushing) AND **vel5 jumps to +4.06** AND **ret_5d_prior is +5.38%** (just had a big push). These are stocks **mid-rally, accelerating**, not exhausted.

The score formula's component math reaches 90+ when many components co-spike, and that co-spike happens during fast directional moves — exactly the wrong moment to fade. So the score's right tail is structurally biased toward continuation patterns, not reversal patterns.

This is the **score-formula exhaustion paradox**: the more components agree, the less likely it's a reversal — because all components agreeing means the trend is fully developed, not exhausted.

## FINDING #7 — vol_ratio threshold does NOT replicate higher-band EV (Hyp 1 REJECTED)

| Band | base EV | vol≥1.0 EV | vol≥2.0 EV |
|---|---|---|---|
| 70-74 | -1.7% | -2.6% | **-5.3%** |
| 75-79 | -2.6% | -4.2% | -7.5% |
| 80-84 | +0.8% | **+2.7%** ← best | -2.1% |
| 85-89 | -2.6% | -3.5% | -5.7% |

vol_ratio doesn't transfer. The score isn't simply a vol-flag; it's a **vol-flag combined with the inverted oscillator math**. Disentangled, neither piece works.

But — **80-84 + vol_ratio>=1.0 = +2.7% EV at N=358** is the cleanest marginal improvement found this session. Modest but real, ~120 trades/yr.

## FINDING #8 — Rally+crack+vol fingerprint does NOT generalize (Hyp 2 REJECTED)

| Strategy | N | EV |
|---|---|---|
| Baseline (8176) | 8176 | -1.8% |
| LOOSE: 20d>0 + macdh<0 + vol≥1 | 1110 | -1.5% |
| FINGERPRINT: 20d>3 + macdh<-0.5 + vol≥1.5 | 121 | **-6.5%** |
| TIGHT: 20d>5 + macdh<-1 + vol≥2 | 15 | -11.8% |

Tightening hurts. The 80-84/85-89 winner-vs-stopout signal does NOT survive as an additive filter on the broader universe — same trap as the LOW-side ema200_dist filter from session 3.

Per-band: only the 75-79 band gets a boost (-2.6% → +5.1% with N=31), but N=31/3yr is unusably thin. Same for 80-84 (+0.8 → +1.1%, no real lift).

---

## Findings summary so far

1. **The user's premise needs correction**: there is NO "high return band in the 70's range." The 70-74 / 75-79 / 85-89 bands are all negative-EV. **80-84 is the only positive HIGH band**, and only marginally (+0.8% EV).
2. **90+ collapse is REAL** (not just ORLY corruption). Sanitized 90+ still shows -18.5% EV at p_win=41% with N=29.
3. **The 90+ collapse mechanism is the score-formula exhaustion paradox**: at 90+ the score is firing on **mid-rally accelerating moves** (vel5=+4.06, ret_5d=+5.4%, macdh=-0.15 ≈ zero), not exhausted reversals. All components co-spike during fast continuation, not at tops. The right tail of the score is structurally biased toward continuation patterns.
4. **80-84 is the sweet spot** because it has the most-negative macdh (-0.99) AND elevated vol_ratio (+2.10) — momentum cracking on climactic volume. By 90+ macdh re-rises and the cracking pattern is gone.
5. **vol_ratio does not transfer** — applying vol_ratio≥2 to lower bands HURTS them. Score and vol are entangled in the formula's amplification step; disentangling fails.
6. **Rally+crack fingerprint does not generalize** — same overfitting trap as LOW-side ema200_dist (session 3).
7. **One marginal positive**: **80-84 + vol_ratio≥1.0 → +2.7% EV, N=358 (≈120/yr), p_win=64.5%**. Cleanest finding. Still small enough that compounding makes this maybe-tradeable.
8. **The HIGH side's structural problem**: classical overbought filters all HURT EV; the formula is sign-inverted on oscillators (CLAUDE.md "open investigation"). The right tail rewards co-spike patterns which are momentum continuations, not exhaustion. There is no clean fix without changing the formula.

---

## Iteration 3 — 80-84 sub-drill + exhaustion-paradox validation

### 80-84 sub-fingerprint drill (best EV at W=42 trading days)
| Filter | N | keep% | p_win | EV |
|---|---|---|---|---|
| NONE (80-84 baseline) | 484 | 100% | 62.0% | +0.6% |
| vol_ratio ≥ 1.0 | 358 | 74% | 64.2% | +2.9% |
| macdh < 0 | 364 | 75% | 63.2% | +1.8% |
| macdh < -0.5 | 204 | 42% | 66.7% | +2.9% |
| macdh < -1.0 | 127 | 26% | 67.7% | +3.6% |
| **vol≥1 + macdh<0** | **270** | **56%** | **65.9%** | **+4.4%** ★ |
| vol≥1 + macdh<-0.5 | 148 | 31% | 67.6% | +4.2% |
| vol>=1.5 + macdh<-0.5 + vel5<1 | 88 | 18% | 62.5% | -0.3% |

**Best: 80-84 + vol_ratio≥1.0 + macdh<0 → +4.4% EV, N=270 (~90/yr), p_win=65.9%, W=42 trading days**

(adding more filters quickly destroys the edge — same overfitting pattern as session 3)

### Exhaustion-paradox test — macdh<-0.5 splits per band

| Band | all EV | **macdh<-0.5** | macdh∈[-0.5,0] | macdh≥0 |
|---|---|---|---|---|
| 70-74 | -1.7% | -0.8% | -0.8% | -4.9% |
| 75-79 | -2.6% | **+0.6%** ← turned + | -3.2% | -7.4% |
| 80-84 | +0.8% | **+2.6%** | +0.6% | -2.2% |
| 85-89 | -2.6% | -2.6% | -3.4% | -1.0% |
| **90+** | **-18.5%** | **-16.9% (N=13)** ← no recovery | -20.3% | -19.1% |

## 🎯 FINDING #9 — macdh<-0.5 is the universal HIGH-side improvement... except at 90+

`macdh < -0.5` strictly improves EV on 70-74, 75-79, 80-84. **It does not recover 90+ even when applied as a filter**. Even the 90+ subset that satisfies `macdh<-0.5` (N=13) stays at -16.9% EV.

**This is the strongest validation of the exhaustion-paradox hypothesis** (FINDING #6). At 90+ the score itself selects for a *different population* — not just "score 80 with stronger oscillators." The high score is a structurally different setup that the macdh filter cannot rescue. Likely the very high `vel5` (+4.06 vs +1.5 at 80-84) is a "this is mid-rally acceleration" tell that no single feature can override.

## 🎯 FINDING #10 — The score's edge LIVES in 75-84, not the right tail

If the score were a clean "more conviction = better signal" tag, EV would scale monotonically up to 90+. It does the opposite: the **belly bands (75-84) hold whatever edge exists**, and the **right tail (85+) turns toxic**. The exhaustion paradox at the right tail is structural, not noise.

The closest thing to a tradeable HIGH-side signal:

```
HIGH SHORT entry (buy ATM put, 42 trading day expiry):
   1. score in [80, 84]
   2. vol_ratio >= 1.0 (active volume)
   3. macdh < 0 (momentum already cracking)

Exit:  +1σ realized vol of underlying  (target)
       -2σ realized vol of underlying  (stop)
       42 trading days max hold

Empirical (1095d, V3 id=3, sanitized):
   N=270  ≈ 90/yr  p_win=65.9%  EV=+4.4% per trade
```

For comparison this is **less than ¼ the LOW side's edge** (LOW: +21.6% EV, ~210/yr). The HIGH side is a marginal opportunity at best. The session-3 LOW-side strategy remains the cornerstone.

---

---

## (A) RESOLVED — LOW strategy validated out-of-sample

**Command**: `python -m experiments.probe_maturity_acceleration --validate-low --days 1095 --test-days 365`

**Window**: 2023-04-10 → 2026-03-06 (1061 days)
**Train**: 2023-04-10 → 2025-03-05 (696d, 2745 LOW peaks)
**Test**: 2025-03-06 → 2026-03-06 (365d, 1087 LOW peaks)

| Split | strat N | keep% | **p_win** | win_pnl | stop_pnl | **EV** | trades/yr |
|---|---|---|---|---|---|---|---|
| train | 432 | 15.7% | 68.3% | +75.4% | -94.6% | **+21.7%** | ~227 |
| **test** | **178** | **16.4%** | **71.9%** | **+67.1%** | **-96.4%** | **+21.6%** | **~178** |
| full | 610 | 15.9% | 69.3% | +72.9% | -95.0% | +21.6% | ~210 |

**Decision gates (CLAUDE.md session-3 pickup recipe)**:
- Required: test EV > +15%, p_win > 63%, N ≥ 50
- Achieved: **EV +21.6%, p_win 71.9%, N=178**
- Verdict: **[VALIDATED] — ship as entry-filter layer**

**Per-band test breakdown** confirms in-sample structure:
| Band | Test N | p_win | EV |
|---|---|---|---|
| 16-19 | 50 | **76.0%** | **+22.1%** |
| 23-25 | 128 | 70.3% | +21.4% |
| 20-22 | 89 | 62.9% | +10.0% ← still diluted (matches train) |
| 6-15 | 32 | 50.0% | **-11.9%** ← actively bad (correctly excluded) |

The 20-22 dilution holds out-of-sample at exactly the same +10% EV — the band split is structural, not an artifact. The 6-15 band flipped sign on test (+14.6% → -11.9%) but it was correctly excluded from the strategy. This is the most reassuring possible result: every decision baked into the strategy survives OOS, and the only band that misbehaves is the one we already discard.

**What this means for shipping**: the 3-gate + sweet-bands strategy is the cornerstone signal. Building an `entry_filter.py` layer that takes `(symbol, date, score)` and returns `(tradeable: bool, reason: str)` is now justified. Wire into [dte_recommendation.py](dte_recommendation.py) so it gates LONG-side thesis classification.

---

## (C) Bear put spread test on 80-84+vol+macdh filter — CLOSED

**Question**: Does capping the stop loss with a vertical spread improve the 80-84 + vol≥1 + macdh<0 EV (+4.4% naked baseline)?

**Approach**: `_payout_cell_spread()` prices a 1σ-OTM short leg against the 1σ-ITM long; runs the same path-dependent simulation; compares naked vs spread head-to-head across all (K, M, W) cells.

**Result**: **Spreads HURT EV by 0.1–2.1pp on every cell tested.** At the best cell (W=42), naked=+4.4% / spread=+2.6%. The cap on losses (~-39% naked → ~-15% spread) is more than offset by the cap on winners — at 66% WR, every winner clipped from +60% to +30% drags EV down faster than every loser saved from -39% to -15%.

**Verdict**: **Naked puts are correct for this filter.** Bear put spreads only help if WR < 50% and asymmetric loss control matters more than upside. Closed.

---

## (B) 90+ HIGH cohort investigation — CLOSED

**Question**: Is the 90+ collapse driven by a fixed cohort of names (utilities/staples) where mean reversion never works?

**Approach**: `_inspect_90plus_cohort()` lists all 29 sanitized 90+ rows with symbol, date, full feature vector, and outcome. Then frequency-counts symbols.

**Result**: **27 unique symbols across 29 rows.** No structural cohort (no utilities, no dividend names cluster). The 90+ band is **bimodal**:
- **Rocket continuations** (most rows): AAOI +159%, RKLB +57%, ALAB +50% — small/mid-cap momentum names where score peaked while the trend was still accelerating. These match the "exhaustion paradox" finding from iteration 3.
- **Sign-bug ghosts**: e.g. FTAI 2024-12-19 score=90 ext=-10.6 ret_20d_prior=-23.4 (already deeply broken!) → ret30=-36.1%. The score fired HIGH on a stock that had already cracked, and the cracking continued. Pure model failure, not exhaustion.

**Verdict**: There is no fixable "cohort" — the 90+ band is two distinct failure modes glued together by the score being too generous on its right tail. The cohort question is closed; the residual question (the sign-bug ghosts) is what motivated (D).

---

## (D) Sign-bug investigation re-opened — RESULT: bug is real but inverted from session 3 framing

**Question (from session 3 CLAUDE.md)**: Does the inverted oscillator-component convention (oversold raw RSI/Stoch → high component → high overall) create false HIGH signals that need to be flipped?

**New framing**: Instead of trying yet another reformulation, test the hypothesis directly with `_sign_bug_ghost_test()`:
- **HIGH-side ghost** = `ret_20d_prior < -10 AND ext < 0` (stock already deeply broken — should NOT receive a fresh "expect drop" signal under any sane convention)
- **LOW-side ghost** = `ret_20d_prior > +10 AND ext > +10` (stock already deeply rallied — should NOT receive a fresh "expect bounce" signal)

If the bug is benign, ghost rows should perform similarly to clean rows. If ghost rows are **systematically worse**, the convention is producing genuine misfires on a recognizable fingerprint.

**Result** (1095d, V3, full universe, sanitized 11,937 rows):

### HIGH side @ K=1.0/M=2.0/W=63 (best naked-put cell)

| Band | all N | all EV | ghost N | ghost EV | clean N | clean EV | clean − all |
|---|---|---|---|---|---|---|---|
| 70-74 | 5805 | -1.7% | 373 | -2.7% | 5432 | -1.6% | +0.1pp |
| 75-79 | 1658 | -2.6% | 139 | **-8.3%** | 1519 | -2.1% | **+0.5pp** |
| 80-84 | 484 | +0.8% | 49 | **-8.6%** | 435 | **+1.8%** | **+1.0pp** |
| 85-89 | 200 | -2.6% | 18 | -0.1% | 182 | -2.8% | -0.2pp |
| 90+ | 29 | -18.5% | 1 | (1 row) | 28 | -19.6% | -1.1pp |

### HIGH side @ K=1.0/M=5.0/W=21 (alternate cell)

| Band | all EV | ghost EV | clean EV | clean − all |
|---|---|---|---|---|
| 75-79 | -6.5% | **-19.9%** | -5.3% | **+1.2pp** |
| 80-84 | -2.1% | **-14.3%** | -0.7% | **+1.4pp** |

### LOW side @ K=1.0/M=5.0/W=21 (best call cell)

| Band | all EV | ghost EV | clean EV | clean − all |
|---|---|---|---|---|
| 16-19 | +12.6% | **+17.0%** | +12.3% | -0.3pp |
| 20-22 | +3.4% | **+12.7%** | +2.6% | -0.8pp |
| 23-25 | +10.3% | -1.5% | +10.7% | +0.5pp |

**Interpretation — the bug is real, but it's the opposite of session 3's framing**:

1. **HIGH-side ghosts are NOT accidental wins — they are systematic LOSSES.** A HIGH score fired on a stock that's already broken (ret_20d ≪ 0, ext < 0) is the WORST possible HIGH signal, losing 6–13 percentage points more than the band average. The convention isn't "accidentally correct" on this fingerprint; it's emitting strong sell signals into stocks that have already capitulated and continue lower.

2. **Removing HIGH ghosts improves EV by +1.0 to +1.4pp on the 75-84 sweet bands** — the same bands that house the only positive HIGH-side strategy (80-84 + vol + macdh, +4.4% baseline). Stacking the ghost filter on top should push this toward +5.5–6.0% EV. **Worth implementing as an additional gate.**

3. **LOW-side "ghosts" are not actually ghost-like.** Stocks already up >10% with ext>+10 receiving a LOW score still bounce (ghost EV +12.7% to +17.0% vs band averages of +3.4% to +12.6%). On the LOW side, the inverted convention is genuinely picking up consolidating-rally pullbacks, not misfiring. **The bug is one-sided — it only damages the HIGH side.**

4. **The session-3 fix proposal (flip oscillator sign) is still risky.** Flipping the convention would help HIGH 75-84 by removing the systematically-bad ghost rows, but it would also redefine LOW signals — and LOW-side ghosts are slightly *positive*, meaning a flip might disturb the working LOW edge. The safer move is the **filter approach**: keep the score formula, add a "no HIGH if ret_20d < -10 AND ext < 0" gate.

**Verdict**: **Sign convention is broken on HIGH only. Fix it via additional gate, not formula reformulation.** Add `not (ret_20d_prior < -10 and ext < 0)` to the 80-84 + vol + macdh filter. Re-test out-of-sample before shipping. Investigation closed pending OOS validation.

---

## Hypotheses for next iteration / open questions

1. **Out-of-sample test the 80-84 filter** (train/test split, same as the LOW-side to-do). +4.4% in-sample over 1095d may shrink out-of-sample.
2. **Investigate what makes 90+ structurally different** — is it a fixed set of names (e.g. utility / staples / dividend payers) where mean reversion never works? Or is it a market-regime issue (90+ peaks cluster in low-VIX bull periods)?
3. **Test bear-put-spreads** instead of naked puts on the 80-84 + vol+macdh filter. Capping the stop loss at the spread width should turn +4.4% into substantially more EV given the current asymmetry (mean stop -39%).
4. **Sign-bug retrofit**: the exhaustion paradox is *exactly* what the inverted-oscillator-convention bug predicts. Reframing the score so high-RSI/Stoch raw → high overall might eliminate the right-tail toxicity and restore monotonic EV scaling. This is the path-B option from session 3 that we explicitly chose not to pursue. Maybe revisit?

---

## Summary table — what we now know about the HIGH side

| Question | Answer |
|---|---|
| Is there a "high return band in the 70's"? | **No.** All of 70-74, 75-79 are negative. |
| Why does 90+ collapse? | Score's right tail selects for **mid-rally acceleration** (high vel5, high ret_5d, macdh near zero), not exhaustion. macdh-filtering doesn't rescue it. |
| What does 80-84 have that 90+ doesn't? | macdh deeply negative (-0.99 vs -0.15), elevated vol_ratio (2.10 vs 1.66 corrupted), more days_since_same_side (less clustered). Cracking-with-conviction vs still-pushing-with-acceleration. |
| Is there ANY tradeable HIGH-side filter? | Yes, marginal: **80-84 + vol≥1 + macdh<0 → +4.4% EV @ W=42**. ~90 trades/yr. ~¼ the LOW side's edge. |
| Was 90+ collapse just split corruption? | No. ORLY accounted for ~14 of 43 rows. After sanitize, 29 rows still show -18.5% EV / 41% p_win. The collapse is real. |
| Is the universal "macdh cracking" signal real? | Yes for 75-84. **No for 70-74 (noise floor) and no for 85+ (exhaustion paradox)**. |
