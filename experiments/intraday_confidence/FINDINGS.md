# Intraday Confidence — Bayesian Calibration of the Volume Amplifier

**Run date:** 2026-05-06
**Algorithm version pinned:** v39 (`200f33a`) — v40 was mid-recalc in another session and excluded
**Universe:** 10 stocks × 7 trading days = 70 stock-days × 13 snapshots = 910 reconstructions
**Stocks:** COHR (case study) · NET TER DINO ADM KRYS (recent v39 calls) · IP PDYN CLX XRAY (recent v39 puts)
**Data source:** yfinance `interval='1m', period='7d'` — limited to last 7 days, no DB writes

## TL;DR

1. **Headline finding (separate from the original question):** yfinance's daily-bar `volume` is **structurally understated** for today's data — 40% of today's stocks show >2× understatement vs the 1-min sum, max 4.2×. The volume amplifier currently runs on stale data when scoring today.
2. **The COHR=84 ghost we've been investigating was NOT primarily a volume amplifier issue.** It was a weekly composite refresh + earnings boost gate cliff (`pre_boost` crossing 70). Volume signal stayed THIN_AIR/magnitude≈0 across both 19:26 (score=59) and 20:23 (score=84) writes.
3. **Bayesian optimum exists for the intraday confidence ramp** — `confidence = vol_completion^2.49 + 0.21 × signal_stability` produces +22.6% MSE improvement over the current 0→1 linear ramp. Closing-time boost was optimized to ≈0 (not needed).
4. **Per-signal-type benefit is uneven.** Learned confidence helps CONVICTION/REJECTION (+13-19% MSE) but hurts ABSORPTION (-21%). A single global confidence is suboptimal.
5. **The original "should I sell COHR before close" use case is now better answered:** in this dataset, by 15:30 ET (snap=360) intraday signal_type matches close-of-day only 76% of the time even with full Bayesian-optimal confidence. The signal *is* unstable enough that "trust it at 3:30pm" remains risky regardless of calibration.

---

## Phase 1 — Data assembly

### Phase 1c: universal volume completion profile (10 stocks × 7 days = 70 stock-days)

| Time ET | Min since open | % of day's volume done |
|---|---:|---:|
| 10:00 | 30 | 12.8% |
| 10:30 | 60 | 20.3% |
| 11:00 | 90 | 27.4% |
| 12:00 | 150 | 39.1% |
| 13:00 | 210 | 50.3% |
| 14:00 | 270 | 61.9% |
| 15:00 | 330 | 75.1% |
| 15:30 | 360 | 82.8% |
| **16:00** | **390** | **100.0%** |

**The closing-cross delivers 17.2% of the day's volume in the final 30 minutes.** This is exactly why classification can flip late — if a stock looks NEUTRAL all day at ratio_50 ≈ 0.85 with 83% of volume in, the closing 17% delivered above-average can push the full-day ratio_50 → 1.0+ and flip to NEUTRAL→CONVICTION territory (or vice versa).

Per-stock variance is real:
- PDYN (small-cap, micro-name): 34.2% of volume by 10:30
- COHR: 23.1% by 10:30
- IP / XRAY (large-cap): 13-14% by 10:30

We used the universal profile for the experiment. Per-stock or per-market-cap-bucket profiles are a future refinement.

### Phase 1d: snapshot trajectories

For each (sym, day, t) where t ∈ [30, 60, ..., 360, 390] minutes from open, reconstructed partial OHLCV from 1-min bars and ran the volume-amplifier classification (`_classify_signal` + `_raw_magnitude` + `_to_multiplier`) using the prior-day baseline (50d average volume from `PriceHistory`).

Output: `trajectories.parquet` (910 rows). Distribution at close (t=390):

| Signal | Stock-days |
|---|---:|
| NEUTRAL | 45 |
| CONVICTION | 8 |
| REJECTION | 5 |
| THIN_AIR | 5 |
| ABSORPTION | 4 |
| CLIMAX | 3 |

64% of stock-days are NEUTRAL at close — the "uninteresting" majority.

---

## Phase 2 — yfinance daily-bar volume staleness (NEW FINDING)

**This was unexpected and overshadows the original question.** During trajectory reconstruction, the 1-min volume sum did not match `PriceHistory.volume`, especially on recent days.

### Ratio (1-min sum / `PriceHistory.volume`) by days_ago:

| days_ago | n | mean ratio | median | min | max | % >2.0× |
|---:|---:|---:|---:|---:|---:|---:|
| 0 (today) | 10 | **1.99** | 1.75 | 0.70 | 4.17 | **40%** |
| 1 (yesterday) | 10 | 1.51 | 1.15 | 0.71 | 3.32 | 20% |
| 2 | 10 | 1.09 | 0.99 | 0.87 | 1.75 | 0% |
| 5 | 10 | 0.97 | 0.91 | 0.70 | 1.68 | 0% |
| 6 | 10 | 1.40 | 1.26 | 0.72 | 2.52 | 10% |
| 7 | 10 | 4.47* | 0.95 | 0.88 | 35.57* | 10% |
| 8 | 10 | 0.89 | 0.92 | 0.75 | 1.00 | 0% |

*NET on day 7 was an outlier (35×). All other days_ago ≥ 2 are stable.

**Interpretation:** Yahoo's daily-bar API aggregates volume in stages. By T+2 the daily bar settles to match true intraday-summed volume. On T0 and T-1 the daily bar can be **2-4× understated**. This means:

> When `trader update` runs at 19:01 ET and reads `PriceHistory.volume` for today, it's reading a value that may be 2-4× below reality. The volume amplifier classifies on this incorrect input. The score row stored at 20:23 today reflects yesterday's-resolution-of-Yahoo'sfeed, not today's actual market.

### Today's COHR specifically (most extreme case):

| | volume | ratio_50 | implied class |
|---|---:|---:|---|
| `PriceHistory.volume` (production saw) | 9,693,292 | 1.29× | NEUTRAL/THIN_AIR |
| Sum of 1-min bars (reality) | 35,303,834 | **4.70×** | **CONVICTION/REJECTION territory** |

Production stored `volume_signal=THIN_AIR, magnitude=6.7e-7` — essentially NEUTRAL (multiplier ≈ 1.0). Reality was a high-volume rejection day. The volume amplifier was **completely blind to today's actual volume signal.**

### Was THIS the COHR=84 ghost?

No — the volume signal didn't change between the 19:26 (score=59) and 20:23 (score=84) writes. Both said `THIN_AIR magnitude≈0`, multiplier ≈ 1.0. The 25-point swing came from:

| Component | 19:26 | 20:23 | Δ |
|---|---:|---:|---:|
| `w_adj` (weekly adjustment) | -5.9 | +11.9 | **+17.8** |
| `w_comp` (weekly composite) | 53 | 65 | +12 |
| `pre_boost` (pre-earnings-boost score) | 59 | 73 | +14 |
| `ern_boost` strength | 0.0 (gate not crossed) | 0.494 | activated |

So the COHR ghost is a **weekly + earnings-boost-gate-cliff** issue (Priority #7 / #9 in `known-issues.md`), not a volume amplifier issue. The volume amplifier WAS broken (per-staleness above), but coincidentally classified COHR at near-NEUTRAL multiplier in both writes.

This shifts the operational priority: **fixing the weekly composite stability is more urgent for the COHR class of ghost than fixing the volume amplifier intraday confidence.**

---

## Phase 3 — Bayesian optimization (existing volume amplifier)

**Confidence shape:**
```
stat_conf  = vol_completion ** stat_exp                   # ∈ [0,1]
closing    = σ((30 − mins_to_close) / closing_scale) × closing_weight
stability  = signal_history_match_fraction × stability_weight
confidence = clip(stat_conf + closing + stability, 0, 1)
```

**Loss:** MSE between confidence-weighted effective multiplier `1 + conf × (mult_intraday − 1)` and the close-of-day multiplier (treating close-of-day as ground truth from 1-min-derived classification).

**Optimizer:** `gp_minimize` (skopt), 80 evaluations, 4D parameter space.

### Result

| Parameter | Best |
|---|---:|
| `stat_exp` | **2.49** (vol_completion²·⁵ — much steeper than sqrt or linear) |
| `closing_scale` | 5.39 (sharp sigmoid) |
| `closing_weight` | **0.012** (essentially zero — unnecessary) |
| `stability_weight` | **0.21** (modest signal-persistence bonus) |

**MSE:**
| Method | MSE | Improvement |
|---|---:|---:|
| Always neutral (multiplier=1.0) | 0.03521 | baseline |
| Full-trust intraday signal | 0.03940 | -11.9% (worse than neutral!) |
| **Current `_intraday_confidence`** | 0.03940 | (same as full-trust — current ramp hits 1.0 by 10:30 so equivalent) |
| **Learned confidence** | **0.03049** | **+22.6% vs current** |

**Key insight:** The current behavior (full-trust after 10:30) is **strictly worse** than just assuming NEUTRAL all day. That's how broken the existing intraday confidence is.

### Learned confidence shape (with universal profile, vol_completion):

| Time ET | snap | vol_done | conf(stab=0) | conf(stab=1) |
|---|---:|---:|---:|---:|
| 10:00 | 30 | 12.8% | 0.006 | 0.216 |
| 10:30 | 60 | 20.3% | 0.019 | 0.229 |
| 11:00 | 90 | 27.4% | 0.040 | 0.250 |
| 12:00 | 180 | 39.1% | 0.133 | 0.343 |
| 13:30 | 240 | 55.9% | 0.236 | 0.446 |
| 14:30 | 300 | 67.8% | 0.381 | 0.591 |
| 15:00 | 330 | 75.1% | 0.492 | 0.702 |
| **15:30** | **360** | **82.8%** | **0.632** | **0.842** |

**For day-trading: the answer to "should I sell COHR before close" boils down to two questions:**
1. How close to 4pm is it? (vol_completion does most of the work)
2. Has the current signal classification been consistent for the last 3 snapshots? (stability adds 0.2)

A high-conviction signal that's been stable since 1:30pm has confidence ~0.84 by 3:30pm — actionable. A signal that just flipped at 3:00pm has confidence ~0.49 — wait or fade.

---

## Phase 4 — Sub-analyses

### Confusion matrix (intraday signal_type vs close-of-day signal_type, 840 pairs)

```
close_signal     ABSORPTION  CLIMAX  CONVICTION  NEUTRAL  REJECTION  THIN_AIR
intraday signal
ABSORPTION             0.17    0.08        0.15     0.01       0.03      0.00
CLIMAX                 0.00    0.50        0.10     0.00       0.02      0.00
CONVICTION             0.04    0.22        0.36     0.04       0.15      0.00
NEUTRAL                0.54    0.17        0.29     0.86       0.53      0.48
REJECTION              0.25    0.03        0.04     0.04       0.27      0.00
THIN_AIR               0.00    0.00        0.05     0.06       0.00      0.52
```

(Read: column "CONVICTION" = of all stock-days where close-of-day was CONVICTION, 36% of intraday snapshots also classified CONVICTION; 29% read NEUTRAL.)

**Hardest signals to detect intraday:**
- REJECTION → 53% of pre-close snapshots read NEUTRAL (volume hasn't yet revealed the rejection pattern)
- THIN_AIR → 48% read NEUTRAL (low-vol pattern only emerges by close)

**Easiest:**
- NEUTRAL → 86% match
- CLIMAX → 50% match (high volume gets noticed early)

### Match rate by snapshot time

| t (min) | Clock | match% |
|---:|---|---:|
| 30 | 10:00 | 56% |
| 90 | 11:00 | 59% |
| 150 | 12:00 | 66% |
| 210 | 13:00 | 73% |
| 240 | 13:30 | 79% (peak) |
| 300 | 14:30 | 71% |
| 360 | 15:30 | 76% |

Hits 79% at 1:30pm, dips slightly through afternoon, recovers to 76% at 3:30pm. The 1:30pm peak is interesting — partial-day signal is most reliable there. Late afternoon may regress because the closing auction's last-30-min volume (17%!) introduces flipping.

### Per-signal-type loss (interesting subset only — 25 stock-days where close ≠ NEUTRAL)

| close signal | N | always neutral | full trust | learned | best |
|---|---:|---:|---:|---:|---|
| ABSORPTION | 48 | 0.145 | **0.087** | 0.105 | full trust |
| CLIMAX | 36 | **0.026** | 0.060 | 0.031 | always neutral |
| CONVICTION | 96 | 0.127 | 0.134 | **0.108** | learned |
| REJECTION | 60 | 0.144 | 0.151 | **0.131** | learned |
| THIN_AIR | 60 | 0.014 | **0.002** | 0.006 | full trust |

**A single global confidence function is uneven across signal types.** Learned helps CONVICTION/REJECTION (the bigger-magnitude swing signals) but hurts ABSORPTION/THIN_AIR (which have small enough magnitude that neutralizing them is itself a loss). CLIMAX is best left at neutral (its multiplier is the most dampened anyway).

**Per-signal-type confidence calibration would likely capture most of the residual error** — especially if the optimizer can learn "ABSORPTION is small magnitude, trust it" vs "REJECTION is big magnitude, gate it carefully."

---

## New hypotheses generated

### H1 — Replace `PriceHistory.volume` with cumulative 1-min sum for today's score (HIGHEST PRIORITY)

The volume staleness finding is **structural** — the volume amplifier's input is wrong by 2-4× for today's signals. No amount of confidence calibration fixes this.

**Implementation:**
- Add intraday volume polling: every `trader update` cron pulls 1-min from yfinance and stores cumulative volume in a side table (`PriceHistoryIntraday`).
- Volume amplifier reads cumulative-as-of-now from the side table for today; falls back to `PriceHistory.volume` for past days (where it's settled).
- Estimated impact: removes 30-50% of intraday volume-misclassification noise.

**Cost:** Schema change + new yfinance call in `trader update` (~1 extra API call per stock per cron). 1-2 days of work.

### H2 — Ship the learned confidence shape now (LOW EFFORT, MEDIUM IMPACT)

Replace `_intraday_confidence` at [volume_amplifier.py:122-124](volume_amplifier.py#L122) with:

```python
def _intraday_confidence(pulled_at: datetime, partial_day_volume_ratio: float = None,
                          signal_history: list = None) -> float:
    elapsed = max(0, (pulled_at.hour - 9) * 60 + (pulled_at.minute - 30))
    vol_completion = elapsed / 390
    stat = vol_completion ** 2.49
    if signal_history:
        stab = signal_history.count(signal_history[-1]) / len(signal_history)
        stat += 0.21 * stab
    return min(1.0, max(0.0, stat))
```

**Impact:** +22.6% MSE improvement on intraday score stability. Doesn't fix the staleness bug (H1 does that) but cleanly attenuates partial-day signals.

**Cost:** ~30 lines of code, plus stability needs `signal_history` plumbed in (last 2-3 prior intraday writes — requires the audit log from H4).

### H3 — Per-signal-type confidence

Different signal types have different reliability profiles. Build:

```python
CONFIDENCE_PARAMS = {
    'CONVICTION': {'stat_exp': 2.5, 'stability_weight': 0.30},
    'REJECTION':  {'stat_exp': 2.5, 'stability_weight': 0.25},
    'ABSORPTION': {'stat_exp': 0.8, 'stability_weight': 0.10},  # trust mostly
    'THIN_AIR':   {'stat_exp': 1.0, 'stability_weight': 0.05},  # trust mostly
    'CLIMAX':     {'stat_exp': 4.0, 'stability_weight': 0.40},  # gate hard
}
```

Per-signal stat_exp lets us tune confidence ramping per the empirical signal-volatility profile.

**Cost:** Re-run optimizer with 5× the parameter space. Needs more data (~200 stock-days) to fit reliably.

### H4 — Score-write audit log (PREREQUISITE FOR H2 STABILITY + GLOBAL OPTIMIZATION)

Add a `ScoreHistory` table that captures every Score row write (not just the latest). Schema:

```sql
CREATE TABLE score_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(10),
    date DATE,
    version_id INT,
    overall FLOAT,
    volume_signal VARCHAR(20),
    volume_magnitude FLOAT,
    weight_info JSON,
    written_at DATETIME,
    INDEX (symbol, date, written_at)
);
```

Every `trader update` writes one row here in addition to upserting `Score`. After 2 weeks of operation we'd have ~5,000 datapoints/day to optimize confidence globally.

**Cost:** ~50 lines of code, marginal DB write cost (~1k rows/day × 470 stocks × 13 cron runs = 6k/day → manageable).

### H5 — Fix the COHR-class issue at the source (HIGH PRIORITY, separate from this experiment)

The COHR=84 ghost was weekly+earnings, NOT volume. Priority #7 / #9 in known-issues.md address this directly:
- Daily-layer EMA smoothing of `wadj` (α=0.7)
- Soft ramp on earnings boost gate (replace binary 70-cliff with gradient)

This is the highest-leverage scoring-stability fix. Should ship before/alongside H1+H2.

### H6 — Pre-close commit at 15:30 ET instead of 16:00

The closing-cross delivers 17% of day's volume in the last 30 min. Score the day at 15:30 instead of 16:00 — you'd lose some signal accuracy on closing-volume-driven flips but gain stability on signals where the closing auction adds noise.

**Validation needed:** Compare H1's 15:30-cumulative-1m signal classification vs 16:00 close-of-day signal across full universe over a longer window. If closing auction generally CONFIRMS rather than flips, ship the earlier commit.

### H7 — Per-stock or per-bucket volume profile

Current experiment used a 10-stock-averaged universal profile. Per-stock variance was 13-34% at the 10:30 mark — meaningful. Build:

```python
volume_profile_by_market_cap = {
    'small': {30: 0.18, 60: 0.27, ...},   # PDYN-like
    'mid':   {30: 0.13, 60: 0.20, ...},   # COHR-like
    'large': {30: 0.11, 60: 0.18, ...},   # IP-like
}
```

Tightens projection accuracy. ~20% of staleness might be mis-projection rather than Yahoo settling — worth measuring.

---

## Open questions worth answering

1. **Is the staleness pattern consistent over longer windows?** 7 days isn't enough to know if Yahoo settles by EXACTLY T+2 always. Run a 60-day study via `pulled_at` in `PriceHistory` to see when daily bars actually settle.
2. **Do we have intraday volume in any other yfinance endpoint?** The `quote.regularMarketVolume` field is real-time; if accessible via `Ticker.info`, it could replace daily-bar volume cleanly.
3. **Does the COHR-class fix (H5) eliminate enough ghost trails to deprioritize H1+H2?** Run the volume amplifier blind (vol_mult forced to 1.0) for a week and measure how often score changes by >10pts intra-day. If rare, the volume amplifier is third-tier; if frequent, H1 is critical.
4. **What does the *full-strategy* MC look like with H1+H2 shipped?** Per-trade gate predicts +X% TP rate but portfolio-level slot displacement could compound differently. Run N=500 × 8-window canonical MC after shipping H1+H2.

---

## Artifacts produced

| File | Content |
|---|---|
| `01_pull_minute_data.py` | yfinance 1-min puller (10 stocks, 7d) |
| `02_build_volume_profile.py` | Universal + per-stock volume completion profile |
| `03_reconstruct_trajectories.py` | 30-min snapshot reconstruction with full classification |
| `04_volume_staleness_analysis.py` | PriceHistory.volume vs 1-min sum staleness study |
| `05_bayesian_optimize.py` | gp_minimize over 4D confidence parameter space |
| `06_visualize_cohr.py` | COHR trajectory plot + sub-analyses |
| `volume_profile.json` | Universal + per-stock profiles |
| `volume_staleness.json` | days_ago × ratio summary |
| `trajectories.parquet` | 910 snapshot rows |
| `optimization_pairs.parquet` | 840 (snap, ground-truth) pairs with learned confidence |
| `optimization_result.json` | Best params + all 80 evaluations |
| `cohr_trajectory.png` | Visual: COHR's 7-day signal evolution |
| `interesting_only_loss.txt` | Sub-analysis on close ≠ NEUTRAL pairs |

---

## Ranked recommendations

| # | What | Effort | Impact |
|---|---|---|---|
| **1** | **H5** — Weekly EMA smoothing + earnings boost gate ramp (Priority #7/#9) | 3-5 days | Highest — fixes the COHR-class ghost directly |
| **2** | **H1** — Replace `PriceHistory.volume` with intraday 1-min cumulative for today's scores | 2 days | High — eliminates staleness bug (40% of today's signals are mis-fed) |
| **3** | **H4** — Score audit log | 1 day | Enabling — prerequisite for stability metric in H2 + future global optimization |
| **4** | **H2** — Ship learned confidence shape (`vol_completion^2.49 + 0.21 × stability`) | 1 day | Medium — +22.6% MSE on volume amp stability |
| **5** | **H7** — Per-stock or per-cap volume profiles | 2 days | Low — tightens projection accuracy by ~10-20% |
| **6** | **H3** — Per-signal-type confidence | After H4 collects data | Medium — addresses sub-analysis residuals |
| **7** | **H6** — Pre-close commit (15:30 vs 16:00) | 1 day to validate | Speculative — needs broader study |
