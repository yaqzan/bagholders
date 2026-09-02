"""Cross-bucket validation of weekly_adj discriminator on the CALL side.

Mirror of put_wadj_cross_buckets.py for calls. Question: does the same
'weak-weekly underperforms strong-weekly' pattern hold for call buckets
70+ through 95+?

If yes: symmetric confidence-gated weekly amplifier applies to both sides.
If pattern is weaker on calls: asymmetric (puts-only) gate may suffice.

Tests for each call bucket {70-74, 75-79, 80-84, 85-89, 90-94, 95+}:
  - all-bucket WR15
  - WR15 stratified by wadj quartile
  - WR15 stratified by fixed wadj threshold (e.g., >= +13 vs < +13)
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


def _call_barrier(entry, bars, sigma, W):
    """Call barrier: K=2.0sigma target up, M=5.0sigma stop down."""
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    tgt = entry * (1.0 + 2.0 * sigma * scale / 100.0)
    stp = entry * (1.0 - 5.0 * sigma * scale / 100.0)
    last = None
    for (d_off, hi, lo, cl) in bars:
        if d_off > W:
            break
        last = cl
        if hi >= tgt:
            return 1
        if lo <= stp:
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
          AND s.overall >= 70
          AND s.price IS NOT NULL
        ORDER BY s.symbol, s.date
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} call scores (>=70) over 5y")

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

            w15 = _call_barrier(entry, fwd, sigma, 15)
            w30 = _call_barrier(entry, fwd, sigma, 30)
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
        (95, 100, '95+'), (90, 94, '90-94'), (85, 89, '85-89'),
        (80, 84, '80-84'), (75, 79, '75-79'), (70, 74, '70-74'),
    ]

    print("\n" + "=" * 100)
    print("WR15 by CALL BUCKET x WEEKLY_ADJ QUARTILE")
    print("=" * 100)
    print(f"{'bucket':<8} {'all WR15':>10} {'Q1 wadj<= ':>12} {'Q1 N/WR15':>14} {'Q4 wadj>= ':>12} {'Q4 N/WR15':>14} {'Q4-Q1 D':>9}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 20: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        wadjs = sorted(r['wadj'] for r in rs)
        if len(wadjs) < 8: continue
        q1 = wadjs[len(wadjs) // 4]
        q3 = wadjs[3 * len(wadjs) // 4]
        a = [r for r in rs if r['wadj'] <= q1]
        b = [r for r in rs if r['wadj'] >= q3]
        wr_a = sum(r['w15'] for r in a) / len(a) * 100 if a else 0
        wr_b = sum(r['w15'] for r in b) / len(b) * 100 if b else 0
        print(f"{lbl:<8} {wr_all:>9.1f}% {q1:>11.1f}  N={len(a):>5} WR={wr_a:>5.1f}%  "
              f"{q3:>11.1f}  N={len(b):>5} WR={wr_b:>5.1f}%  {wr_b-wr_a:>+8.1f}pp")

    print("\n" + "=" * 100)
    print("WR15 by CALL BUCKET x WADJ THRESHOLD (fixed +13 cutoff — symmetric to put -13)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all N':>7} {'all WR15':>10} | {'wadj>=+13 N':>13} {'WR15':>7} | {'wadj<+13 N':>13} {'WR15':>7}  {'D(weak-strong)':>15}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 20: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        strong = [r for r in rs if r['wadj'] >= 13]
        weak = [r for r in rs if r['wadj'] < 13]
        wr_s = sum(r['w15'] for r in strong) / len(strong) * 100 if strong else 0
        wr_w = sum(r['w15'] for r in weak) / len(weak) * 100 if weak else 0
        d = wr_w - wr_s
        marker = " *" if abs(d) >= 5 and min(len(strong), len(weak)) >= 20 else ""
        print(f"{lbl:<8} {n_all:>7} {wr_all:>9.1f}% | N={len(strong):>9}  {wr_s:>6.1f}% | "
              f"N={len(weak):>9}  {wr_w:>6.1f}%  {d:>+13.1f}pp{marker}")

    print("\n" + "=" * 100)
    print("WR15 by CALL BUCKET x WADJ THRESHOLD (relaxed +6 cutoff — small magnitude)")
    print("=" * 100)
    print(f"{'bucket':<8} {'all N':>7} {'all WR15':>10} | {'wadj>=+6 N':>13} {'WR15':>7} | {'wadj<+6 N':>13} {'WR15':>7}  {'D(weak-strong)':>15}")
    for lo, hi, lbl in BUCKETS:
        rs = [r for r in records if lo <= r['overall'] <= hi]
        if len(rs) < 20: continue
        n_all = len(rs)
        wr_all = sum(r['w15'] for r in rs) / n_all * 100
        strong = [r for r in rs if r['wadj'] >= 6]
        weak = [r for r in rs if r['wadj'] < 6]
        wr_s = sum(r['w15'] for r in strong) / len(strong) * 100 if strong else 0
        wr_w = sum(r['w15'] for r in weak) / len(weak) * 100 if weak else 0
        d = wr_w - wr_s
        marker = " *" if abs(d) >= 5 and min(len(strong), len(weak)) >= 20 else ""
        print(f"{lbl:<8} {n_all:>7} {wr_all:>9.1f}% | N={len(strong):>9}  {wr_s:>6.1f}% | "
              f"N={len(weak):>9}  {wr_w:>6.1f}%  {d:>+13.1f}pp{marker}")


if __name__ == '__main__':
    main()
