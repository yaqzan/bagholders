"""Cross-bucket validation of the weekly_adj discriminator found in =5.

The =5 split analysis showed weekly_adj > -13 puts have WR15=60% vs <=-16
at WR15=81.6%. Question: does this signal extend across other put buckets,
or is it score-5-specific?

If broader: the filter has more statistical power and better portfolio impact.
If narrow: only score-5 dip is fixable.

Test: for each bucket {=0, 1-4, =5, 6-10, 11-15, 16-20, 21-25}, compute
WR15 stratified by weekly_adj bands.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _realized_vol(closes):
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _put_barrier(entry, bars, sigma, W):
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    tgt = entry * (1.0 - 1.0 * sigma * scale / 100.0)
    stp = entry * (1.0 + 2.0 * sigma * scale / 100.0)
    last = None
    for (d_off, hi, lo, cl) in bars:
        if d_off > W:
            break
        last = cl
        if lo <= tgt:
            return 1
        if hi >= stp:
            return 0
    if last is None:
        return None
    return 0


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}")

    q = f"""
        SELECT s.symbol, s.date, s.overall, s.weight_info, s.price, s.volume_signal
        FROM scores s
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 25
          AND s.price IS NOT NULL
        ORDER BY s.symbol, s.date
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} put scores (<=25) over 5y")

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    records = []
    for sym, sym_rows in by_sym.items():
        min_d = min(r[1] for r in sym_rows)
        max_d = max(r[1] for r in sym_rows)
        ph_start = (min_d - timedelta(days=90)).isoformat()
        ph_end = (max_d + timedelta(days=50)).isoformat()
        bars_q = DB.execute_sql(f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date BETWEEN '{ph_start}' AND '{ph_end}'
            ORDER BY date
        """).fetchall()
        if len(bars_q) < 30:
            continue
        d_idx = {b[0]: i for i, b in enumerate(bars_q)}
        closes = [float(b[1]) for b in bars_q]
        highs = [float(b[2]) for b in bars_q]
        lows = [float(b[3]) for b in bars_q]
        dates_bar = [b[0] for b in bars_q]

        for r in sym_rows:
            sym_, d, overall, winfo_str, price, vsig = r
            ov = int(overall)
            if d not in d_idx: continue
            i = d_idx[d]
            if i < 30: continue
            sigma = _realized_vol(closes[:i + 1])
            if sigma is None: continue
            entry = closes[i]
            fwd = []
            for j in range(i + 1, len(bars_q)):
                off = (dates_bar[j] - d).days
                if off > 35: break
                fwd.append((off, highs[j], lows[j], closes[j]))

            w15 = _put_barrier(entry, fwd, sigma, 15)
            w30 = _put_barrier(entry, fwd, sigma, 30)
            if w15 is None or w30 is None: continue

            wadj = None
            if winfo_str:
                try:
                    wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                    wa = wi.get('w_adj') or wi.get('weekly_adj') or 0
                    wadj = float(wa)
                except Exception:
                    pass
            if wadj is None: continue
            records.append({'overall': ov, 'w15': w15, 'w30': w30,
                            'wadj': wadj, 'vsig': vsig or 'NONE'})

    print(f"Records: {len(records)}")

    BUCKETS = [
        (0, 0, '=0'), (1, 4, '1-4'), (5, 5, '=5'), (6, 10, '6-10'),
        (11, 15, '11-15'), (16, 20, '16-20'), (21, 25, '21-25'),
    ]

    # 1. WR by (bucket, wadj quartile)
    print("\n" + "=" * 100)
    print("WR15 by BUCKET x WEEKLY_ADJ QUARTILE (data-driven quartiles per bucket)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all WR15':>10} {'Q1 wadj<= ':>12} {'Q1 N/WR15':>14} {'Q4 wadj>= ':>12} {'Q4 N/WR15':>14} {'Q4-Q1 D':>9}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 40: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        wadjs = sorted(r['wadj'] for r in rs)
        q1 = wadjs[len(wadjs) // 4]
        q3 = wadjs[3 * len(wadjs) // 4]
        a = [r for r in rs if r['wadj'] <= q1]
        b = [r for r in rs if r['wadj'] >= q3]
        wr_a = sum(r['w15'] for r in a) / len(a) * 100 if a else 0
        wr_b = sum(r['w15'] for r in b) / len(b) * 100 if b else 0
        print(f"{lbl:<8} {wr_all:>9.1f}% {q1:>11.1f}  N={len(a):>4} WR={wr_a:>5.1f}%  "
              f"{q3:>11.1f}  N={len(b):>4} WR={wr_b:>5.1f}%  {wr_b-wr_a:>+8.1f}pp")

    # 2. WR by (bucket, fixed wadj threshold)
    print("\n" + "=" * 100)
    print("WR15 by BUCKET x WADJ THRESHOLD (fixed -13 cutoff — does pattern extend?)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all N':>7} {'all WR15':>10} | {'wadj<=-13 N':>14} {'WR15':>7} | {'wadj>-13 N':>14} {'WR15':>7}  {'D(weak-strong)':>15}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 40: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        strong = [r for r in rs if r['wadj'] <= -13]
        weak = [r for r in rs if r['wadj'] > -13]
        wr_s = sum(r['w15'] for r in strong) / len(strong) * 100 if strong else 0
        wr_w = sum(r['w15'] for r in weak) / len(weak) * 100 if weak else 0
        d = wr_w - wr_s
        marker = " *" if abs(d) >= 8 and min(len(strong), len(weak)) >= 25 else ""
        print(f"{lbl:<8} {n_all:>7} {wr_all:>9.1f}% | N={len(strong):>10}  {wr_s:>6.1f}% | "
              f"N={len(weak):>11}  {wr_w:>6.1f}%  {d:>+13.1f}pp{marker}")

    # 3. WR by (bucket, REJECTION vs other vsig)
    print("\n" + "=" * 100)
    print("WR15 by BUCKET x VSIG (REJECTION vs others)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all N':>7} {'all WR15':>10} | {'REJECTION N':>14} {'WR15':>7} | {'other N':>9} {'WR15':>7}  {'D(rej-other)':>13}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 40: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        rej = [r for r in rs if r['vsig'] == 'REJECTION']
        other = [r for r in rs if r['vsig'] != 'REJECTION']
        wr_r = sum(r['w15'] for r in rej) / len(rej) * 100 if rej else 0
        wr_o = sum(r['w15'] for r in other) / len(other) * 100 if other else 0
        d = wr_r - wr_o
        marker = " *" if abs(d) >= 8 and min(len(rej), len(other)) >= 25 else ""
        print(f"{lbl:<8} {n_all:>7} {wr_all:>9.1f}% | N={len(rej):>10}  {wr_r:>6.1f}% | "
              f"N={len(other):>6}  {wr_o:>6.1f}%  {d:>+11.1f}pp{marker}")

    # 4. Combined predicate: REJECTION & weak weekly  per bucket
    print("\n" + "=" * 100)
    print("WR15 by BUCKET x COMBINED (REJECTION AND wadj > -13)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all N':>7} {'all WR15':>10} | {'match N':>9} {'WR15':>7} | {'other N':>9} {'WR15':>7}  {'D':>9}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 40: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        match = [r for r in rs if r['vsig'] == 'REJECTION' and r['wadj'] > -13]
        other = [r for r in rs if not (r['vsig'] == 'REJECTION' and r['wadj'] > -13)]
        wr_m = sum(r['w15'] for r in match) / len(match) * 100 if match else 0
        wr_o = sum(r['w15'] for r in other) / len(other) * 100 if other else 0
        d = wr_m - wr_o
        marker = " *" if abs(d) >= 8 and min(len(match), len(other)) >= 25 else ""
        print(f"{lbl:<8} {n_all:>7} {wr_all:>9.1f}% | N={len(match):>6}  {wr_m:>6.1f}% | "
              f"N={len(other):>6}  {wr_o:>6.1f}%  {d:>+8.1f}pp{marker}")


if __name__ == '__main__':
    main()
