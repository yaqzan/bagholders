# Entry-Timing Phase A — close-anchor vs next-open vs gap-filtered mixes

**Date:** 2026-06-11 · **Substrate:** active v72 (`fc5671200`, = v71 calls + WCF put-ramp; call universe ≡ v71) ·
**Universe:** 4,852 call signals ≥75, 5y (2021-06 → 2026-06), all symbols incl ETFs ·
**Runner:** `run_phase_a.py` (queue task #134, 52s) · **Outputs:** `out/phase_a_results_full.json`, `out/phase_a_signals_full.parquet`

## Question (user, 2026-06-11)

Options trade market-hours only. Assess "only buying at open vs only buying at/near close, or some
mix" — close entry in practice = last ~30 min. Follow-up sanctioned by the `intraday_overnight`
(2026-06-08) spin-off note: the −1.22pp next-open haircut is a methodology/realism question.

## Phase 0 (live intraday-log read, ~11 days, `phase0_intraday_flip_rates.py`)

- Open-score entry (first 09:30-11:00 run ≥75): **26% of open-qualified signals die by close**
  (35% on volatile days) AND **misses 39% of the true close-signal universe** (day-builders:
  AMSC 60→91, ARKX 65→82 on its actual signal day).
- Last-30-min entry: **83% coverage of close signals, 8.3% false-positive fade, 0.36% median
  price drift** to close. The close anchor is executable.

## Phase A results (paired walks, barriers anchored at each policy's own entry)

Gap: signals gap UP **+20.8 bps** overnight (t=+5.15), 42.4% gap down — reproduces the
intraday_overnight +17.5 bps on the honest substrate.

| barrier (TP/SL σ, window) | n | C (close) | N (next open) | Δ N−C |
|---|---:|---:|---:|---:|
| apexW15 (1.092/2.548, 15 bars) | 4,743 | **73.03%** | 71.68% | **−1.35pp** |
| apexW19 (~27 cal days, live CALENDAR_HOLD) | 4,711 | 73.51% | 72.34% | −1.17pp |
| holdTP15 (TP-only) | 4,743 | 80.58% | 79.19% | −1.39pp |

Per-year: C wins 2021/2022/2024/2025/2026 (−0.3 to −2.4pp); only 2023 flips (+2.4pp, smallest
n=327). Per-tier: 75-79 −1.73, 85+ −1.47, 80-84 +0.22 (noise). **Independently reproduces the
−1.22pp finding** (different ledger build, v72 scores, same conclusion).

## The mix policies — selection and entry price CANCEL (the key finding)

Gap-filtered next-open entry, apexW15 ("entryFx" = N−C on the same entered subset):

| policy | enter% | N-WR | C-WR(same subset) | entryFx | C-WR(skipped) |
|---|---:|---:|---:|---:|---:|
| N_all | 100.0% | 71.68% | 73.03% | −1.35pp | — |
| N_nogapdn00 (gap ≥ 0) | 57.5% | 71.51% | **81.37%** | **−9.86pp** | **61.76%** |
| N_nogapdn025 | 77.8% | 71.31% | 77.87% | −6.56pp | 56.08% |
| N_nogapdn050 | 88.4% | 71.32% | 76.12% | −4.80pp | 49.64% |
| N_nochase05 (gap ≤ +0.5σ) | 85.0% | 71.60% | 69.64% | +1.96pp | **92.26%** |
| N_nochase10 | 96.2% | 71.64% | 72.06% | −0.42pp | 97.78% |
| N_band (−0.25..+1.0σ) | 74.0% | 71.23% | 76.84% | −5.61pp | 62.18% |

Read it as efficient pricing of the gap:

1. **The overnight gap is a STRONG outcome signal** — gap-up cohort C-WR 81.4% vs gap-down 61.8%
   (19.6pp spread); the biggest gap-ups (>+1σ) run 97.8%. "Winners run" extends overnight.
2. **But you cannot harvest it at the open.** Every single next-open policy — any filter, any
   direction — converges to **71.2-71.7% N-WR**, below the unconditional close anchor (73.0%).
   The σ-anchored TP moves up with the entry price by exactly the informative amount; selection
   gain and entry-price drag cancel almost one-for-one (N_nogapdn00: +8.3pp selection,
   −9.9pp entry effect). Skipping gap-ups ("no chase") gets a favorable entry (+1.96pp) but
   excludes the 92%-WR best signals.
3. The gap-down cohort (61.8% C-WR) is still well above call break-even — not a cut/skip signal
   for an already-entered close-anchor position (consistent with the CDR/cut-to-redeploy null).

## Conclusions

- **Buy at/near the close (last ~30 min). Closed question.** It is faithful to the model entry
  (Phase 0), and it preserves the −1.2/−1.35pp that ANY open-entry variant loses.
- **No open-entry mix exists** in price space: the gap is informative but already in the price.
- **Open-SCORE entry is dominated twice over** (Phase 0): buys the 26%-fakeout morning cohort and
  misses 39% of real signals.
- **Phase A2 (full open-score reconstruction + confirmation gate) is NOT recommended.** The score
  at open moves with the gap (its dominant input); the cancellation result plus Phase 0's
  miss/fakeout rates leave A2 with little room. Only revisit with a signal that is NOT price
  (e.g., overnight news/volume context) and a mechanism for why it isn't priced into the open.
- Realism note: anyone trading at next open should mentally subtract ~1.2-1.4pp WR from
  backtest/assessment numbers (the engines anchor at signal close; live last-30-min entry ≈ no
  haircut).

## Do not retry

- Gap-direction-filtered next-open entry (any threshold, either direction, or bands) as a WR
  improvement — measured cancellation, sign-consistent 5 of 6 years.
- Open-score qualification as an entry policy (Phase 0 fakeout/miss rates; same family as the
  retrace-entry anti-selection null, overnight edition).
