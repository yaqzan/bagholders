"""
Find the ext% (pct from EMA50) value where put WR30 bottoms out.
Sweeps finely across the ext range for puts (score <= 25), computes WR30 per bin,
and fits a smooth curve to locate the trough.
"""
from __future__ import annotations
import datetime as dt
import numpy as np
import pandas as pd
from database.trader_database import DB
from database.models.core import AlgorithmVersion


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = dt.date.today() - dt.timedelta(days=1825)

    # Pull all put scores (<=25) with ext and forward-win features
    # Need: overall, price on signal date, next 30d OHLCV for barrier walk, 60d sigma
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.price,
               i.ema_50
        FROM scores s
        JOIN indicators i USING (symbol, date)
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 25
          AND s.price IS NOT NULL
          AND i.ema_50 IS NOT NULL
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} put signals (<=25) over 5y")

    # Group by symbol for efficient forward walk
    by_sym = {}
    for sym, date, overall, price, ema50 in rows:
        by_sym.setdefault(sym, []).append({
            'date': date, 'overall': int(overall),
            'price': float(price),
            'ext': (float(price) - float(ema50)) / float(ema50) * 100,
        })

    # Load price history per symbol for forward walk + sigma
    syms = list(by_sym.keys())
    print(f"Loading price history for {len(syms)} symbols...")

    placeholders = ','.join(['%s'] * len(syms))
    ph_cutoff = cutoff - dt.timedelta(days=90)
    q2 = f"""
        SELECT symbol, date, high, low, close
        FROM price_history
        WHERE symbol IN ({placeholders})
          AND date >= %s
        ORDER BY symbol, date
    """
    cur = DB.execute_sql(q2, syms + [ph_cutoff])
    ph = {}
    for sym, d, h, l, c in cur.fetchall():
        ph.setdefault(sym, []).append((d, float(h), float(l), float(c)))
    for sym in ph:
        ph[sym].sort(key=lambda x: x[0])

    # Barrier walk: put win if low touches entry*(1 - K*sigma*sqrt(W/30))
    # K=1.0, M=2.0 for puts. W=30 calendar days.
    K_PUT, M_PUT = 1.0, 2.0
    W = 30
    scale = 1.0  # sqrt(30/30)

    records = []
    for sym, sigs in by_sym.items():
        bars = ph.get(sym, [])
        if not bars:
            continue
        dates = [b[0] for b in bars]
        closes = [b[3] for b in bars]

        for sig in sigs:
            # Find bar index for signal date
            try:
                i0 = dates.index(sig['date'])
            except ValueError:
                continue

            # Sigma: 60-day realized vol of daily returns
            if i0 < 60:
                continue
            rets = []
            for k in range(i0 - 60, i0):
                if k > 0 and closes[k - 1] > 0:
                    rets.append((closes[k] - closes[k - 1]) / closes[k - 1])
            if len(rets) < 30:
                continue
            sigma = float(np.std(rets)) * 100  # in pct
            if sigma <= 0:
                continue

            entry = sig['price']
            tp = entry * (1 - K_PUT * sigma * scale / 100)
            sl = entry * (1 + M_PUT * sigma * scale / 100)

            # Walk forward 30 calendar days (≈ 21 trading bars)
            cutoff_date = sig['date'] + dt.timedelta(days=W)
            win = None
            for k in range(i0 + 1, len(bars)):
                d, h, l, c = bars[k]
                if d > cutoff_date:
                    break
                hit_tp = l <= tp
                hit_sl = h >= sl
                if hit_tp and hit_sl:
                    win = 1  # optimistic; random for now use 1
                    break
                if hit_tp:
                    win = 1
                    break
                if hit_sl:
                    win = 0
                    break
            if win is None:
                # Check if we have enough forward bars to call it expire-loss
                if bars[-1][0] > cutoff_date:
                    win = 0
                else:
                    continue

            records.append({
                'symbol': sym, 'date': sig['date'],
                'overall': sig['overall'], 'ext': sig['ext'], 'win30': win,
            })

    df = pd.DataFrame(records)
    print(f"Usable records: {len(df)}")
    print(f"  by bucket: <=5: {len(df[df.overall<=5])}, 6-10: {len(df[(df.overall>=6)&(df.overall<=10)])}, "
          f"11-15: {len(df[(df.overall>=11)&(df.overall<=15)])}, "
          f"16-20: {len(df[(df.overall>=16)&(df.overall<=20)])}, "
          f"21-25: {len(df[(df.overall>=21)&(df.overall<=25)])}")

    # Fine-grained ext sweep for different score bands
    print("\n" + "="*100)
    print("WR30 vs EXT (fine-grained, 2pp bins)")
    print("="*100)

    # Focus on scores <=15 (where the trough matters most per earlier audit)
    for score_cap, label in [(5, 's<=5'), (10, 's<=10'), (15, 's<=15'), (20, 's<=20'), (25, 's<=25')]:
        sub = df[df.overall <= score_cap]
        if len(sub) < 100:
            print(f"\n{label}: N={len(sub)} (skip, too small)")
            continue
        print(f"\n{label}: N={len(sub)}")
        print(f"{'ext_lo':>8} {'ext_hi':>8} {'N':>6} {'WR30':>7}")
        # Bins every 2 pp from -40 to +10
        edges = list(range(-40, 12, 2))
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (sub.ext >= lo) & (sub.ext < hi)
            n = m.sum()
            if n < 10:
                continue
            wr = sub.loc[m, 'win30'].mean() * 100
            print(f"{lo:>8} {hi:>8} {n:>6} {wr:>6.1f}%")

    # Now find the empirical trough for s<=15 (primary target band)
    print("\n" + "="*100)
    print("TROUGH FIT (s<=15)")
    print("="*100)
    sub = df[(df.overall <= 15) & (df.ext > -40) & (df.ext < 15)]

    # Smoothed WR by ext using moving window
    ext_values = np.linspace(-30, 10, 81)  # 0.5pp steps
    wr_smooth = []
    n_smooth = []
    HALF = 3.0  # ±3pp window = 6pp bin
    for e in ext_values:
        m = (sub.ext >= e - HALF) & (sub.ext <= e + HALF)
        n = int(m.sum())
        if n < 20:
            wr_smooth.append(np.nan)
            n_smooth.append(n)
            continue
        wr_smooth.append(sub.loc[m, 'win30'].mean() * 100)
        n_smooth.append(n)
    wr_smooth = np.array(wr_smooth)

    # Print smoothed curve
    print(f"\n{'ext':>6} {'N(±3pp)':>8} {'WR30':>7}")
    for e, wr, n in zip(ext_values, wr_smooth, n_smooth):
        if np.isnan(wr):
            continue
        print(f"{e:>+6.1f} {n:>8} {wr:>6.1f}%")

    # Find minimum
    valid = ~np.isnan(wr_smooth)
    if valid.any():
        min_idx = np.nanargmin(wr_smooth)
        print(f"\n  TROUGH @ ext = {ext_values[min_idx]:+.1f}%  WR30 = {wr_smooth[min_idx]:.1f}%  N(±3pp) = {n_smooth[min_idx]}")

        # Quadratic fit for sub-bin focal point
        ev = ext_values[valid]
        wv = wr_smooth[valid]
        coeffs = np.polyfit(ev, wv, 2)  # ax^2 + bx + c
        a, b, c = coeffs
        if a > 0:
            focal_ext = -b / (2 * a)
            focal_wr = a * focal_ext ** 2 + b * focal_ext + c
            print(f"  QUADRATIC FOCAL: ext = {focal_ext:+.2f}%  WR30_fit = {focal_wr:.1f}%")
        else:
            print(f"  Quadratic a={a:.3f} (not convex), using empirical trough")


if __name__ == '__main__':
    main()
