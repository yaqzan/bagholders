# Per-Stock Score Normalization ("bell curve") — NULL (2026-06-03)

**Status: DILUTIVE on the option barrier → NULL. No scoring change, no version bump.** Point-in-time,
holdout-locked (≤2026-05-15), universe-scale (N=340,405 candidate signals, v70). Workflow throttled
4× this session → ran the 10-angle evaluation inline. Artifact: `out/analyze_results.txt`.

## Hypothesis (user's "bell curve")
Absolute `overall>=75` is a per-stock-mean+std lottery: **32% of stocks (247/774) make ZERO 75+ signals
in 5y** (AAL: mean 42, max-ever 68 — can't cross 75). Normalize each stock to its own distribution
(per-stock z / percentile / cross-sectional rank), point-in-time, so every stock contributes its
relative-best days. Does bridging the quiet and loud stocks yield a better result?

## Verdict: NO — every scheme, every threshold, is DILUTIVE; ps_z adds NO info beyond `overall`.

### A. All three schemes, all thresholds < the 75+ base on the OPTION barrier
NEW signals (scheme fires, absolute<75) vs 75+ base (opt15 47.5% / apex15 70.0%):

| scheme | N | opt15 (z) | apex15 (z) | verdict |
|---|---:|---:|---:|---|
| ps_z≥1.5 & abs<75 | 111,373 | 46.0% (−7.1) | 67.1% (−14.9) | DILUTIVE |
| ps_z≥3.0 & abs<75 (strictest) | 1,895 | 44.1% (−2.1) | 62.6% (−4.8) | DILUTIVE |
| ps_pctile≥0.95 & abs<75 | 106,904 | 46.5% (−4.8) | 67.7% (−11.6) | DILUTIVE |
| xs_pctile≥0.95 & abs<75 | 96,526 | 46.7% (−3.4) | 68.0% (−9.7) | DILUTIVE |

Cross-sectional rank ≈ the 70-74 absolute base (vs-70 z≈0) — because rank is mechanically correlated
with `overall`; it's literally "trade more 70-74-equivalents."

### B. The alpha-vs-dilution boundary: only the 73-74 band (≈ the existing threshold) is non-dilutive
ps_z≥1.5 & abs<75 by signal `overall` band: 50-59 = 44.4% (z−5.2) → 60-64 = 45.6% → 65-69 = 46.3% →
70-72 = 46.3% → **73-74 = 48.8% (z+1.1, the ONLY ~flat band).** Dilution worsens monotonically the
deeper below 75 you go. **The pure-BRIDGE mute stocks (max overall<75 — the user's exact case) are the
WORST cohort: opt15 43.7% (z−7.4) / apex15 63.2% (z−14.1).** The quiet stocks' relative-best days are
the most dilutive signals in the whole universe. Dilutive in every year (2018-2024), every vol bucket
(high-vol/NVDA-like worst), every warmup level.

### C. THE KILLER QUESTION — does ps_z add info BEYOND `overall`? NO.
Within a fixed `overall` band, WR is FLAT-to-DECLINING across ps_z:
- overall 60-69: opt15 by ps_z bin = 47.2% / 47.2% / 46.3% / 45.2% (higher z → *worse*).
- overall 70-74: 47.9% / 46.7% / 46.5%.
**ps_z is entirely redundant with the absolute score it normalizes.** "Grade on the curve" = "lower the
threshold" *exactly* — and lower bands are worse. This collapses the hypothesis to threshold-lowering,
a known-null, with per-band WR proving the lower bands lose.

### D. Skeptic checks all hold
- Concentration: drop top-5 new-signal symbols (F, XOM, EQT, URA, CVE — cyclical/energy) → still z−7.0.
- PIT: n_prior≥250 (strict warmup) → still z−6.0. Not a look-ahead/warmup artifact.
- Option-vs-generic: gen15 (62-64%) > opt15 (44-46%) — the usual gap; only the option barrier is
  decision-relevant, and it's dilutive. (gen15-only would have been the SVD/v42 trap.)

### E. Supply expands but it's worthless
abs≥75 = 4,700 signals / 613 stocks → ps_z≥1.5 = 111,374 signals / 766 stocks (154 mute stocks now
contribute). The bridge mechanically solves the 32%-mute problem — but with dilutive signals (the v42
volume-dump trap at 25× scale): adds bodies, not alpha.

## Why (the economic read)
The absolute score is already (weakly) calibrated to option outcomes. A stock has a low score-mean
**because** it's a poorer momentum/option vehicle — the score is correctly declining to signal on stocks
that don't set up. Forcing its relative-best day to trade buys "the best of a bad lot." The 32%-mute
fact is correct risk-pricing, not unfairness. The user's intuition (the lottery is real) is right; the
fix is wrong because the "lottery" is the score doing its job.

## What NOT to retry
- Per-stock score normalization / z / percentile / cross-sectional re-thresholding (any window/warmup):
  DILUTIVE on the option barrier, redundant with `overall`. The only non-dilutive band (73-74) is the
  already-known 70-74 overflow tier (marginally enabled in Apex @0.035) — normalization rediscovers it,
  nothing new.
- Bridging the mute (0-signal) stocks specifically: their relative-best days are the WORST cohort (z−7.4).
- Retry needs a genuinely new per-stock SIGNAL (not a re-scaling of `overall`) that adds info beyond the
  score — e.g. a per-stock feature the score doesn't already encode. Re-scaling `overall` is closed.

## Artifacts
`recon.py` (supply distribution) · `build_norm_ledger.py` (PIT ledger, sample-verified no look-ahead) ·
`eval_norm.py` (option-barrier helper) · `analyze_norm.py` + `out/analyze_results.txt` · `workflow.js`
(authored; server-throttled — ran inline). Ledger: `.cache/score_norm/norm_ledger_v70.parquet`.
