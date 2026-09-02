"""
SL-fill-bias audit — empirical question: does the bounded-fill [low, sl_level]
uniform model match real-world bar behavior, or is it systematically pessimistic?

Hypothesis: bounded fill samples uniform within [low, sl_level], implying mean
fill at midpoint (0.5 normalized). Real bars where low breached sl_level may
close near sl_level on average (meaning trader stopped near barrier on
continuous price action) rather than near the bar low (gap-through worst case).

Methodology:
1. Take 5y of PriceHistory for the active universe.
2. For each bar where low < open (a "down day"), define hypothetical SL levels
   at -1σ, -2σ, -3σ from open (using 60-day realized vol).
3. For bars where low <= sl_level (the barrier was touched), record:
     pos = (close - low) / max(sl_level - low, 1e-9)
   - pos = 1.0 → close at sl_level (intraday-trigger, bounce-back, favorable)
   - pos = 0.0 → close at low (gap-through, worst-case)
   - uniform model's expected: 0.5
4. Distribution + mean of pos tells us if bounded-fill model is pessimistic.

If mean(pos) is significantly above 0.5 across many bars, the model is
systematically pessimistic by (mean(pos) - 0.5) × range — meaning
real-world realized losses are typically smaller than the uniform model
suggests.

Output: stats + histogram + recommended fill model adjustment.
"""
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.models.core import Stock
from database.models.technical import PriceHistory


VOL_LOOKBACK = 60
SIGMA_LEVELS = [1.0, 1.092, 1.274, 1.5, 2.0, 3.0]   # underlying-σ barrier multiples
LOOKBACK_DAYS = 365 * 5


def realized_vol(closes, idx, lookback=VOL_LOOKBACK):
    """60d realized stdev of daily returns ending at idx (inclusive)."""
    if idx < lookback:
        return None
    rets = []
    for j in range(idx - lookback + 1, idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var)


def main():
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    syms = [s.symbol for s in Stock.select(Stock.symbol).limit(500)]
    print(f"Loading {len(syms)} symbols, last {LOOKBACK_DAYS} days...")
    sys.stdout.flush()

    # Per-σ-level: list of (close_position_in_low_to_sl_range, raw_loss_frac)
    # close_position = (close - low) / (sl_level - low), bounded [0, 1]
    # raw_loss = (sl_level - close) / sl_level   (positive when close worse than barrier)
    samples = defaultdict(list)

    for i, sym in enumerate(syms):
        rows = list(PriceHistory.select(
            PriceHistory.date, PriceHistory.open, PriceHistory.close,
            PriceHistory.high, PriceHistory.low,
        ).where(
            (PriceHistory.symbol == sym) & (PriceHistory.date >= cutoff)
        ).order_by(PriceHistory.date).tuples())

        if len(rows) < VOL_LOOKBACK + 10:
            continue

        dates  = [r[0] for r in rows]
        opens  = [float(r[1]) for r in rows]
        closes = [float(r[2]) for r in rows]
        highs  = [float(r[3]) for r in rows]
        lows   = [float(r[4]) for r in rows]

        for idx in range(VOL_LOOKBACK, len(rows) - 1):
            sigma = realized_vol(closes, idx)
            if not sigma or sigma <= 0:
                continue

            # Look at the NEXT bar (would be exit if we entered today and
            # had a SL set at today_close - K*σ).
            entry = closes[idx]
            next_low  = lows[idx + 1]
            next_high = highs[idx + 1]
            next_close = closes[idx + 1]

            for K in SIGMA_LEVELS:
                sl_level = entry * (1 - K * sigma)
                if next_low > sl_level:
                    continue                  # barrier not touched
                if next_high < sl_level:
                    # Gap-through: even high was below sl. Pure gap case.
                    # close_pos within [low, sl_level] is unaffected by
                    # gap; treat as gap-through.
                    pass

                # close position within [low, sl_level] (only valid if low < sl_level)
                rng = sl_level - next_low
                if rng <= 0:
                    continue
                pos = max(0.0, min(1.0, (next_close - next_low) / rng))
                samples[K].append(pos)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(syms)} syms")
            sys.stdout.flush()

    # Stats per σ level
    print("\n" + "=" * 100)
    print("SL-fill bias by σ barrier")
    print("=" * 100)
    print(f"{'K_σ':>6} {'N':>8} {'mean(pos)':>12} {'median':>10} {'p10':>8} {'p25':>8} {'p75':>8} {'p90':>8}")
    print(f"  uniform expected mean = 0.500   (bounded-fill MC model)")
    print(f"  pos = 1.0 → close at sl_level (favorable)   pos = 0.0 → close at low (worst)")
    print('-' * 100)
    for K in SIGMA_LEVELS:
        pos_list = sorted(samples[K])
        n = len(pos_list)
        if n < 100:
            print(f"{K:>6.2f} {n:>8d}  (insufficient samples)")
            continue
        mean = sum(pos_list) / n
        median = pos_list[n // 2]
        p10 = pos_list[n // 10]
        p25 = pos_list[n // 4]
        p75 = pos_list[3 * n // 4]
        p90 = pos_list[9 * n // 10]
        print(f"{K:>6.2f} {n:>8d}  {mean:>11.3f}  {median:>9.3f}  {p10:>7.3f}  {p25:>7.3f}  {p75:>7.3f}  {p90:>7.3f}")

    # Conclusion
    print("\n" + "=" * 100)
    print("INTERPRETATION")
    print("=" * 100)
    if not samples or sum(len(s) for s in samples.values()) < 1000:
        print("Insufficient samples to draw conclusions.")
        return

    # Use the K=1.092 (production call SL_BASE=-0.30 → 1.092σ underlying) as headline
    headline_K = 1.092
    pos_list = samples.get(headline_K, [])
    if pos_list:
        mean_pos = sum(pos_list) / len(pos_list)
        median_pos = sorted(pos_list)[len(pos_list) // 2]
        bias_vs_uniform = mean_pos - 0.5
        print(f"  Production K=1.092σ (call SL=-0.30, the most relevant case):")
        print(f"    mean close position = {mean_pos:.3f}  ({'ABOVE' if mean_pos > 0.5 else 'below'} uniform 0.5)")
        print(f"    median             = {median_pos:.3f}")
        print(f"    bias vs uniform    = {bias_vs_uniform:+.3f}")
        print(f"    N                  = {len(pos_list):,}")
        if bias_vs_uniform > 0.05:
            print(f"\n  -> Bounded-fill model is PESSIMISTIC by ~{bias_vs_uniform * 100:.1f}pp of [low, sl_level] range.")
            print(f"     Real-world close lands closer to sl_level than to bar low. The MC overstates")
            print(f"     SL-fire realized loss by approximately {bias_vs_uniform:.1%} of (sl_level - low).")
            print(f"     A revised model that samples Beta(2, 2-skew) or biased toward sl_level would")
            print(f"     better match empirical fills.")
        elif bias_vs_uniform < -0.05:
            print(f"\n  -> Bounded-fill model UNDERSTATES SL loss; real fills land NEAR bar low.")
        else:
            print(f"\n  -> Uniform model is well-calibrated; mean close position is near 0.5.")


if __name__ == '__main__':
    main()
