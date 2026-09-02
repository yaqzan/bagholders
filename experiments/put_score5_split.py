"""Focused split analysis on the score=5 put dip.

The =5 bucket sits at WR15=66.9% (5y v26, N=269) vs 1-4 at 74.5% and 6-10 at
73.7% — a persistent 7-8pp dip surrounded by strong neighbors. Prior signature
sweeps (put_signature_sweep.py) tested stoch/RSI/ext/volume — all failed.

This script tests new discriminators on the =5 bucket only.
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

    # Load =5 + reference buckets (1-4, 6-10) — also include indicator data
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd, s.stoch,
               s.technical_alignment, s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.weight_info, s.price,
               i.ema_50, i.macd_hist, i.upper_band, i.lower_band, i.middle_band, i.stoch as raw_stoch,
               i.rsi as raw_rsi
        FROM scores s
        LEFT JOIN indicators i
          ON i.symbol = s.symbol AND i.date = s.date
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 10
          AND s.price IS NOT NULL
        ORDER BY s.symbol, s.date
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} put scores (<=10) over 5y")

    # Group by symbol for forward-walk
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    # Build per-row record with bucket label, forward win, and discriminator features
    records = []  # list of dicts
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
            (sym_, d, overall, trend, bb, rsi, macd, stoch, ta, vsig, vmag, rmult,
             winfo_str, price, ema50, macd_hist, upper, lower, middle, raw_stoch, raw_rsi) = r
            ov = int(overall)
            if d not in d_idx:
                continue
            i = d_idx[d]
            if i < 30:
                continue
            sigma = _realized_vol(closes[:i + 1])
            if sigma is None:
                continue
            entry = closes[i]
            fwd = []
            for j in range(i + 1, len(bars_q)):
                off = (dates_bar[j] - d).days
                if off > 35:
                    break
                fwd.append((off, highs[j], lows[j], closes[j]))

            w15 = _put_barrier(entry, fwd, sigma, 15)
            w30 = _put_barrier(entry, fwd, sigma, 30)
            if w15 is None or w30 is None:
                continue

            wadj = None
            pre_weekly = None
            pre_regime = None
            if winfo_str:
                try:
                    wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                    pr = wi.get('pre_regime')
                    wa = wi.get('w_adj') or wi.get('weekly_adj') or 0
                    if pr is not None:
                        pre_regime = float(pr)
                        wadj = float(wa)
                        pre_weekly = pre_regime - wadj
                except Exception:
                    pass

            ext = None
            if price and ema50 and float(ema50) > 0:
                ext = (float(price) / float(ema50) - 1.0) * 100.0

            bb_pct = None
            if upper is not None and lower is not None and price is not None:
                bw = float(upper) - float(lower)
                if bw > 0:
                    bb_pct = (float(price) - float(lower)) / bw

            records.append({
                'sym': sym, 'date': d, 'overall': ov, 'w15': w15, 'w30': w30,
                'trend': float(trend) if trend is not None else None,
                'bb': float(bb) if bb is not None else None,
                'rsi': float(rsi) if rsi is not None else None,
                'macd': float(macd) if macd is not None else None,
                'stoch': float(stoch) if stoch is not None else None,
                'ta': float(ta) if ta is not None else None,
                'vsig': vsig or 'NONE',
                'vmag': float(vmag) if vmag is not None else 0.0,
                'rmult': float(rmult) if rmult is not None else 1.0,
                'wadj': wadj, 'pre_weekly': pre_weekly, 'pre_regime': pre_regime,
                'ext': ext,
                'macd_hist': float(macd_hist) if macd_hist is not None else None,
                'raw_stoch': float(raw_stoch) if raw_stoch is not None else None,
                'raw_rsi': float(raw_rsi) if raw_rsi is not None else None,
                'bb_pct': bb_pct,
                'year': d.year,
                'dow': d.weekday(),  # 0=Mon
                'month': d.month,
            })

    print(f"Processed records: {len(records)}")

    def bucket(r):
        ov = r['overall']
        if ov == 0: return '=0'
        if 1 <= ov <= 4: return '1-4'
        if ov == 5: return '=5'
        if 6 <= ov <= 10: return '6-10'
        return None

    by_bucket = defaultdict(list)
    for r in records:
        b = bucket(r)
        if b: by_bucket[b].append(r)

    print("\n--- Reference: WR per bucket ---")
    for b in ['=0', '1-4', '=5', '6-10']:
        rs = by_bucket[b]
        if rs:
            wr15 = sum(r['w15'] for r in rs) / len(rs) * 100
            wr30 = sum(r['w30'] for r in rs) / len(rs) * 100
            print(f"  {b:<5} N={len(rs):>5}  WR15={wr15:.1f}%  WR30={wr30:.1f}%")

    # ==== =5 SPLITS ====
    five = by_bucket['=5']
    print(f"\n=== ALL SPLITS ON =5 BUCKET (N={len(five)}) ===\n")

    def split_report(label, rs, predicate_fn, side_a_label, side_b_label):
        a = []
        b = []
        for r in rs:
            v = predicate_fn(r)
            if v is None:
                continue
            if bool(v):
                a.append(r)
            else:
                b.append(r)
        if not a or not b:
            print(f"  {label}: degenerate split (a={len(a)}, b={len(b)})")
            return None
        wr15_a = sum(r['w15'] for r in a) / len(a) * 100
        wr30_a = sum(r['w30'] for r in a) / len(a) * 100
        wr15_b = sum(r['w15'] for r in b) / len(b) * 100
        wr30_b = sum(r['w30'] for r in b) / len(b) * 100
        delta = wr15_a - wr15_b
        marker = " *" if abs(delta) >= 8 and min(len(a), len(b)) >= 25 else ""
        print(f"  {label:<40}  {side_a_label} N={len(a):>3} WR15={wr15_a:5.1f}%/WR30={wr30_a:5.1f}%   "
              f"vs   {side_b_label} N={len(b):>3} WR15={wr15_b:5.1f}%/WR30={wr30_b:5.1f}%   "
              f"D={delta:+5.1f}pp{marker}")

    print("CONTINUOUS SPLITS (median split):\n")
    for field, threshold_label in [
        ('wadj',       'weekly_adj'),
        ('pre_weekly', 'pre_weekly'),
        ('pre_regime', 'pre_regime'),
        ('rmult',      'regime_mult'),
        ('stoch',      'stoch_comp'),
        ('raw_stoch',  'raw_stoch (oscillator 0-100)'),
        ('macd',       'macd_comp'),
        ('macd_hist',  'macd_hist (raw)'),
        ('rsi',        'rsi_comp'),
        ('raw_rsi',    'raw_rsi'),
        ('bb',         'bb_comp'),
        ('bb_pct',     'bb_pct (price loc 0-1)'),
        ('ext',        'pct_from_ema50'),
        ('trend',      'trend_comp'),
        ('vmag',       'vol_magnitude'),
    ]:
        vals = [r[field] for r in five if r[field] is not None]
        if not vals:
            continue
        med = np.median(vals)
        pred = lambda r, f=field, m=float(med): None if r[f] is None else (r[f] <= m)
        split_report(f"{threshold_label} <= {med:.2f}", five, pred, "lo", "hi")

    print("\nQUARTILE SPLITS (top vs bottom quartile):\n")
    for field, label in [
        ('wadj', 'weekly_adj'), ('rmult', 'regime_mult'), ('stoch', 'stoch_comp'),
        ('ext', 'pct_from_ema50'), ('pre_weekly', 'pre_weekly')
    ]:
        vals = sorted([r[field] for r in five if r[field] is not None])
        if len(vals) < 40:
            continue
        q1 = vals[len(vals) // 4]
        q3 = vals[3 * len(vals) // 4]
        a = [r for r in five if r[field] is not None and r[field] <= q1]
        b = [r for r in five if r[field] is not None and r[field] >= q3]
        if not a or not b:
            continue
        wr15_a = sum(r['w15'] for r in a) / len(a) * 100
        wr30_a = sum(r['w30'] for r in a) / len(a) * 100
        wr15_b = sum(r['w15'] for r in b) / len(b) * 100
        wr30_b = sum(r['w30'] for r in b) / len(b) * 100
        delta = wr15_a - wr15_b
        marker = " *" if abs(delta) >= 10 and min(len(a), len(b)) >= 25 else ""
        print(f"  {label:<25}  Q1(<={q1:.2f}) N={len(a):>3} WR15={wr15_a:5.1f}%/WR30={wr30_a:5.1f}%   "
              f"vs   Q4(>={q3:.2f}) N={len(b):>3} WR15={wr15_b:5.1f}%/WR30={wr30_b:5.1f}%   "
              f"D={delta:+5.1f}pp{marker}")

    print("\nCATEGORICAL SPLITS:\n")
    # Per-year
    print("  Per-year WR15:")
    yrs = sorted(set(r['year'] for r in five))
    for y in yrs:
        rs = [r for r in five if r['year'] == y]
        if not rs: continue
        wr15 = sum(r['w15'] for r in rs) / len(rs) * 100
        wr30 = sum(r['w30'] for r in rs) / len(rs) * 100
        print(f"    {y}: N={len(rs):>3}  WR15={wr15:.1f}%  WR30={wr30:.1f}%")

    print("\n  Per-volume-signal:")
    vsigs = sorted(set(r['vsig'] for r in five))
    for v in vsigs:
        rs = [r for r in five if r['vsig'] == v]
        if not rs: continue
        wr15 = sum(r['w15'] for r in rs) / len(rs) * 100
        wr30 = sum(r['w30'] for r in rs) / len(rs) * 100
        print(f"    {v:<12} N={len(rs):>3}  WR15={wr15:.1f}%  WR30={wr30:.1f}%")

    print("\n  Per-DOW (0=Mon):")
    for dow in range(5):
        rs = [r for r in five if r['dow'] == dow]
        if not rs: continue
        wr15 = sum(r['w15'] for r in rs) / len(rs) * 100
        wr30 = sum(r['w30'] for r in rs) / len(rs) * 100
        dn = ['Mon','Tue','Wed','Thu','Fri'][dow]
        print(f"    {dn} N={len(rs):>3}  WR15={wr15:.1f}%  WR30={wr30:.1f}%")

    # Combined predicate hunt: =5 with bottom-quartile pre_weekly
    print("\n=== COMBINED PREDICATES ===")
    combos = [
        ('weak weekly (wadj > -13.7) & low stoch (<78)',
         lambda r: r['wadj'] is not None and r['stoch'] is not None and r['wadj'] > -13.7 and r['stoch'] < 78),
        ('strong weekly (wadj <= -15) & high stoch (>=85)',
         lambda r: r['wadj'] is not None and r['stoch'] is not None and r['wadj'] <= -15 and r['stoch'] >= 85),
        ('high regime_mult (>=0.90) — non-stress',
         lambda r: r['rmult'] >= 0.90),
        ('low regime_mult (<=0.83) — deep stress',
         lambda r: r['rmult'] <= 0.83),
        ('vsig=THIN_AIR or NONE',
         lambda r: r['vsig'] in ('THIN_AIR', 'NONE')),
        ('vsig=REJECTION',
         lambda r: r['vsig'] == 'REJECTION'),
        ('weak weekly (wadj > -13)',
         lambda r: r['wadj'] is not None and r['wadj'] > -13),
        ('strong weekly (wadj <= -16)',
         lambda r: r['wadj'] is not None and r['wadj'] <= -16),
    ]
    for label, pred in combos:
        rs_match = [r for r in five if pred(r)]
        rs_other = [r for r in five if not pred(r)]
        if not rs_match or not rs_other:
            print(f"  {label}: degenerate")
            continue
        wr15_m = sum(r['w15'] for r in rs_match) / len(rs_match) * 100
        wr30_m = sum(r['w30'] for r in rs_match) / len(rs_match) * 100
        wr15_o = sum(r['w15'] for r in rs_other) / len(rs_other) * 100
        wr30_o = sum(r['w30'] for r in rs_other) / len(rs_other) * 100
        delta = wr15_m - wr15_o
        marker = " *" if abs(delta) >= 8 and min(len(rs_match), len(rs_other)) >= 25 else ""
        print(f"  {label:<55}  match N={len(rs_match):>3} WR15={wr15_m:5.1f}%/WR30={wr30_m:5.1f}%  "
              f"vs other N={len(rs_other):>3} WR15={wr15_o:5.1f}%/WR30={wr30_o:5.1f}%  "
              f"D={delta:+5.1f}pp{marker}")


if __name__ == '__main__':
    main()
