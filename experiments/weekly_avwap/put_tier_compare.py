"""Put-side tier comparison: does the Ichimoku inversion mirror at the 30 boundary?

Hypothesis: bullish weekly Ichimoku state contradicts the put thesis. So for puts
(overall ≤ 25), bullish-Ichimoku peaks should have LOWER put WR than bearish-
Ichimoku peaks — the mirror of what we found at 75+ calls (where bullish-Ichimoku
peaks have HIGHER call WR).

If confirmed, the design is symmetric: a single mechanism with sign-flipped
indicator can dampen both sides (call dampener for bullish Ichimoku at low
scores; put dampener for bullish Ichimoku at low overall... wait that's not
symmetric, let me restate):

  Calls: bullish Ichimoku confirms strong call → BOOST call WR (do nothing, signal already firing)
  Calls: bearish Ichimoku contradicts strong call → DAMPEN strong call (the proposed mechanism)
  Puts:  bullish Ichimoku contradicts strong put → DAMPEN strong put (mirror)
  Puts:  bearish Ichimoku confirms strong put → BOOST put WR (do nothing)

So we expect:
  Puts (≤25), bullish Ichimoku cohort → LOWER put WR (drift toward 50 = mirror dampener)
  Puts (≤25), bearish Ichimoku cohort → HIGHER put WR (already where we want them)

Output: per-tier put-WR spreads on tk_dir / cloud_position / kijun_pct.

Loads BOTH put-side (side='high') and call-side (side='low') barriers so we can
report each metric on every cohort regardless of "what side the signal is for".
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

PARQUET = sys.argv[1] if len(sys.argv) > 1 else 'calls_v39_1825d_min0.parquet'

# Tier slices spanning the full distribution.
# Puts side: 0-25 (qualifying), 26-30 (borderline), 31-49 (no signal)
# Neutral:   50-59, 60-69
# Calls:     70-74, 75-79, 80-84, 85+
TIERS = [
    ('0-10',   0, 11),
    ('11-15', 11, 16),
    ('16-20', 16, 21),
    ('21-25', 21, 26),
    ('26-30', 26, 31),
    ('31-49', 31, 50),
    ('50-59', 50, 60),
    ('60-69', 60, 70),
    ('70-74', 70, 75),
    ('75-79', 75, 80),
    ('80-84', 80, 85),
    ('85+',   85, 101),
]


def cohort_wr(df: pl.DataFrame, col: str) -> tuple[float, int]:
    sub = df.filter(pl.col(col).is_not_null())
    n = len(sub)
    if n == 0:
        return float('nan'), 0
    return float(sub[col].mean()) * 100, n


def main():
    pq = CACHE / PARQUET
    if not pq.exists():
        print(f'[ERROR] parquet not found: {pq}', flush=True)
        return

    df = pl.read_parquet(pq)
    print(f'[load] parquet: {pq.name} → {len(df):,} rows', flush=True)

    # Drop inf rows
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )

    # ------------------------------------------------------------------
    # Attach BOTH put-side (high) and call-side (low) barriers
    # ------------------------------------------------------------------
    print('[load] put-side WR15 (side=high)...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')
    print(f'[joined] put_result_15: {df["put_result_15"].drop_nulls().len():,} resolved '
          f'({time.time()-t0:.1f}s)', flush=True)

    # The existing parquet already has opt_result_15 (call-side). Rename for clarity.
    df = df.rename({'opt_result_15': 'call_result_15'})

    # ============================================================================
    # Per-tier table
    # ============================================================================
    print('\n' + '=' * 120)
    print('Per-tier Ichimoku spreads — does the put-side INVERT at the 30 boundary?')
    print('=' * 120)
    print(
        '  call_WR15  = WR15 if treated as a CALL (side=low, price rises to TP)\n'
        '  put_WR15   = WR15 if treated as a PUT  (side=high, price falls to TP)\n'
        '  Spread direction signs use cloud_position (v=+1 cohort − v=−1 cohort).\n'
        '\n'
        '  Predicted patterns:\n'
        '    For PUTS (≤25): bullish Ichimoku → put_WR LOWER (negative cloud spread)\n'
        '    For CALLS (≥75): bullish Ichimoku → call_WR HIGHER (positive cloud spread, confirmed)\n'
        '    For NEUTRAL (50-69): bullish Ichimoku → call_WR LOWER (inversion, already confirmed)\n'
    )

    print(
        f'{"Tier":<8s} {"N":>8s} '
        f'{"call_baseWR":>11s} {"put_baseWR":>11s}  '
        f'{"call_cl_sp":>10s} {"put_cl_sp":>10s}  '
        f'{"call_tk_sp":>10s} {"put_tk_sp":>10s}  '
        f'{"call_kj_sp":>10s} {"put_kj_sp":>10s}'
    )
    print('-' * 120)

    summary = []

    for label, lo, hi in TIERS:
        tier = df.filter((pl.col('overall') >= lo) & (pl.col('overall') < hi))
        if len(tier) < 50:
            print(f'{label:<8s} N={len(tier):>5,}  (insufficient, skip)')
            continue

        call_base, _ = cohort_wr(tier, 'call_result_15')
        put_base,  _ = cohort_wr(tier, 'put_result_15')

        # cloud_position spread on each metric
        cl_pos = tier.filter(pl.col('cloud_position') == 1)
        cl_neg = tier.filter(pl.col('cloud_position') == -1)
        if len(cl_pos) >= 30 and len(cl_neg) >= 30:
            call_cl_pos, _ = cohort_wr(cl_pos, 'call_result_15')
            call_cl_neg, _ = cohort_wr(cl_neg, 'call_result_15')
            put_cl_pos,  _ = cohort_wr(cl_pos, 'put_result_15')
            put_cl_neg,  _ = cohort_wr(cl_neg, 'put_result_15')
            call_cl_sp = call_cl_pos - call_cl_neg
            put_cl_sp  = put_cl_pos  - put_cl_neg
        else:
            call_cl_sp = put_cl_sp = float('nan')

        # tenkan_kijun_dir spread
        tk_pos = tier.filter(pl.col('tenkan_kijun_dir') == 1)
        tk_neg = tier.filter(pl.col('tenkan_kijun_dir') == -1)
        if len(tk_pos) >= 30 and len(tk_neg) >= 30:
            call_tk_pos, _ = cohort_wr(tk_pos, 'call_result_15')
            call_tk_neg, _ = cohort_wr(tk_neg, 'call_result_15')
            put_tk_pos,  _ = cohort_wr(tk_pos, 'put_result_15')
            put_tk_neg,  _ = cohort_wr(tk_neg, 'put_result_15')
            call_tk_sp = call_tk_pos - call_tk_neg
            put_tk_sp  = put_tk_pos  - put_tk_neg
        else:
            call_tk_sp = put_tk_sp = float('nan')

        # kijun continuous (high tercile - low tercile)
        kij = tier.filter(
            pl.col('price_vs_kijun_pct').is_not_null()
            & pl.col('price_vs_kijun_pct').is_finite()
        )
        if len(kij) >= 90:
            q33 = kij['price_vs_kijun_pct'].quantile(0.333)
            q67 = kij['price_vs_kijun_pct'].quantile(0.667)
            kij_lo = kij.filter(pl.col('price_vs_kijun_pct') <= q33)
            kij_hi = kij.filter(pl.col('price_vs_kijun_pct') > q67)
            call_kj_lo, _ = cohort_wr(kij_lo, 'call_result_15')
            call_kj_hi, _ = cohort_wr(kij_hi, 'call_result_15')
            put_kj_lo,  _ = cohort_wr(kij_lo, 'put_result_15')
            put_kj_hi,  _ = cohort_wr(kij_hi, 'put_result_15')
            call_kj_sp = call_kj_hi - call_kj_lo
            put_kj_sp  = put_kj_hi  - put_kj_lo
        else:
            call_kj_sp = put_kj_sp = float('nan')

        print(
            f'{label:<8s} {len(tier):>8,} '
            f'{call_base:>10.2f}% {put_base:>10.2f}%  '
            f'{call_cl_sp:>+9.2f} {put_cl_sp:>+9.2f}  '
            f'{call_tk_sp:>+9.2f} {put_tk_sp:>+9.2f}  '
            f'{call_kj_sp:>+9.2f} {put_kj_sp:>+9.2f}'
        )

        summary.append({
            'tier': label, 'n': len(tier),
            'call_base': call_base, 'put_base': put_base,
            'call_cl': call_cl_sp, 'put_cl': put_cl_sp,
            'call_tk': call_tk_sp, 'put_tk': put_tk_sp,
            'call_kj': call_kj_sp, 'put_kj': put_kj_sp,
        })

    # ============================================================================
    # Summary: identify inversion points on each side
    # ============================================================================
    print('\n' + '=' * 120)
    print('PUT-SIDE inversion check (cloud_position spread on put_WR15):')
    print('=' * 120)
    for r in summary:
        sign = '+' if (r['put_cl'] > 0.5) else ('-' if r['put_cl'] < -0.5 else '0')
        marker = '  ← BULLISH-ICHIMOKU CONTRADICTS PUT (mirror confirmed)' if r['put_cl'] < -2 \
            else ('  ← bullish-Ichimoku helps put (unexpected)' if r['put_cl'] > 2 else '')
        print(f'  {r["tier"]:<8s}  put_cl_sp = {r["put_cl"]:+6.2f}pp  ({sign}){marker}')

    print('\n' + '=' * 120)
    print('CALL-SIDE inversion check (cloud_position spread on call_WR15):')
    print('=' * 120)
    for r in summary:
        sign = '+' if (r['call_cl'] > 0.5) else ('-' if r['call_cl'] < -0.5 else '0')
        marker = '  ← INVERSION (bullish-Ichimoku contradicts call)' if r['call_cl'] < -2 \
            else ('  ← bullish-Ichimoku helps call (expected)' if r['call_cl'] > 2 else '')
        print(f'  {r["tier"]:<8s}  call_cl_sp = {r["call_cl"]:+6.2f}pp  ({sign}){marker}')


if __name__ == '__main__':
    main()
