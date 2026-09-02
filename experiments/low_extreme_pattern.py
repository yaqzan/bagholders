"""
Investigate the 0-4 put bucket WR falloff.

5y cumulative WR15 pattern:
  <20:  70.4% (N=9115)
  <15:  72.0% (N=3824)
  <10:  73.2% (N=1516)
  <5:   69.7% (N=537)   <-- falls ~3.5pp

Discrete bucket WR15 (5y):
  0-4:   69.7%  (N=537)
  5-9:   75.1%  (N=979)
  10-14: 71.5%  (N=2308)
  15-19: 69.2%  (N=5291)
  20-24: 67.2%  (N=10984)

The 5-9 bucket is the SWEET SPOT. 0-4 underperforms. Goal: find entry-time
features that separate 0-4 winners from 0-4 losers, and pinpoint a screening
rule that lifts WR at extreme puts.

We enforce realistic "don't have split corrupted data" filters:
    |pct_from_ema50| < 50,  bb_position in [-0.5, 1.5]

Barrier win: put side K=1.0σ target (close-to-entry drop of 1.0σ × √(W/30))
             reached before M=2.0σ stop within W calendar days.
σ = 60d realized daily-return stdev (in %).
"""
from __future__ import annotations
import sys
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _realized_vol(closes: list[float]) -> float | None:
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0) if len(rets) >= 20 else None


def _put_barrier_win(entry_close: float, bars_after: list, sigma_pct: float,
                     W_cal: int) -> int | None:
    """bars_after: list of (date_iso, high, low) sorted ascending, excludes entry.
    Returns 1 if K·σ target hit before M·σ stop within W calendar days,
            0 if stop hit or expired,
            None if insufficient history.
    """
    if sigma_pct is None or sigma_pct <= 0:
        return None
    # Scaled barriers per CLAUDE.md put side:  K=1.0σ target, M=2.0σ stop
    scale = (W_cal / 30.0) ** 0.5
    tgt = entry_close * (1.0 - 1.0 * sigma_pct * scale / 100.0)
    stp = entry_close * (1.0 + 2.0 * sigma_pct * scale / 100.0)
    cutoff_day = W_cal  # calendar days forward
    for (days_offset, hi, lo) in bars_after:
        if days_offset > cutoff_day:
            # expired; need strictly past cutoff loaded to be sure
            return 0
        if lo <= tgt:
            return 1
        if hi >= stp:
            return 0
    # ran out of bars before cutoff
    return None


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    lookback_days = 1825
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()

    print(f"Active version: v{av.id} ({av.git_commit[:8]})")
    print(f"Lookback: {lookback_days}d (since {cutoff})")

    # Pull candidates: all LOW scores in 5y window with overall < 25
    # Compute pct_from_ema50 / bb_pos from indicators since v20 scores rarely populate them.
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd,
               s.stoch, s.technical_alignment, s.price,
               NULL AS pct_from_ema50,
               NULL AS bb_position,
               s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.weight_info,
               i.macd_hist, i.ema_50, i.ema_200,
               i.upper_band, i.lower_band
        FROM scores s
        LEFT JOIN indicators i USING (symbol, date)
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 25
          AND s.price IS NOT NULL
          AND i.ema_50 IS NOT NULL
          AND i.upper_band IS NOT NULL
          AND i.lower_band IS NOT NULL
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} LOW score candidates")

    # Group by symbol for efficient PH loading
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    # Load price history per symbol once
    # We need history FROM (signal_date - 75 days) TO (signal_date + 100 calendar days)
    W_vals = [15, 30]
    max_fwd_cal = max(W_vals) + 15  # padding

    records = []
    for sym, sym_rows in by_sym.items():
        # load full price history for this symbol in the needed window
        min_date = min(r[1] for r in sym_rows)
        max_date = max(r[1] for r in sym_rows)
        ph_start = (min_date - timedelta(days=90)).isoformat()
        ph_end = (max_date + timedelta(days=max_fwd_cal + 10)).isoformat()
        q2 = f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date BETWEEN '{ph_start}' AND '{ph_end}'
            ORDER BY date
        """
        bars = DB.execute_sql(q2).fetchall()
        if len(bars) < 30:
            continue
        date_idx = {b[0]: i for i, b in enumerate(bars)}
        closes = [float(b[1]) for b in bars]
        highs = [float(b[2]) for b in bars]
        lows = [float(b[3]) for b in bars]
        dates = [b[0] for b in bars]

        for r in sym_rows:
            (sym_, d, overall, trend, bb, rsi, macd, stoch, ta,
             price, pct_ema50, bb_pos, vol_sig, vol_mag,
             regime_mult, weight_info, macdh, ema50, ema200,
             upper, lower) = r
            if d not in date_idx:
                continue
            # Compute pct_from_ema50 and bb_pos from indicators
            try:
                p = float(price) if price is not None else None
                e50 = float(ema50) if ema50 is not None else None
                up = float(upper) if upper is not None else None
                lo = float(lower) if lower is not None else None
            except Exception:
                continue
            if p is None or e50 is None or e50 <= 0:
                continue
            pct_ema50 = (p / e50 - 1.0) * 100.0
            if abs(pct_ema50) >= 50:
                continue  # split-corrupted
            if up is not None and lo is not None and up > lo:
                bb_pos = (p - lo) / (up - lo)
                if not (-0.5 <= bb_pos <= 1.5):
                    continue  # split-corrupted
            i = date_idx[d]
            if i < 30:
                continue
            # realized vol from closes up to entry
            sigma = _realized_vol(closes[:i + 1])
            if sigma is None:
                continue
            entry = closes[i]
            # forward bars
            fwd_bars = []
            for j in range(i + 1, len(bars)):
                days_off = (dates[j] - d).days
                fwd_bars.append((days_off, highs[j], lows[j]))
                if days_off > max(W_vals) + 5:
                    break
            win15 = _put_barrier_win(entry, fwd_bars, sigma, 15)
            win30 = _put_barrier_win(entry, fwd_bars, sigma, 30)
            # forward returns (lowest low within W cal days — measures run)
            def _max_drop(W):
                best = 0.0
                for (days_off, hi, lo) in fwd_bars:
                    if days_off > W:
                        break
                    drop_pct = (lo - entry) / entry * 100.0
                    if drop_pct < best:
                        best = drop_pct
                return best  # negative number
            min_price_30 = _max_drop(30)

            # features
            ext = float(pct_ema50) if pct_ema50 is not None else None
            bbp = float(bb_pos) if bb_pos is not None else None
            mh = float(macdh) if macdh is not None else None
            # percent from EMA200
            pct_ema200 = None
            if ema200 and float(ema200) > 0:
                pct_ema200 = (entry / float(ema200) - 1.0) * 100.0
            # prior returns
            ret_5d = (closes[i] / closes[i - 5] - 1.0) * 100.0 if i >= 5 else None
            ret_20d = (closes[i] / closes[i - 20] - 1.0) * 100.0 if i >= 20 else None
            # vel5 = 5-day return relative to σ (how violent)
            vel5_sigma = ret_5d / sigma if (ret_5d is not None and sigma) else None
            # regime
            rm = float(regime_mult) if regime_mult is not None else 1.0

            records.append({
                'symbol': sym, 'date': d, 'overall': overall,
                'trend': trend, 'bb': bb, 'rsi': rsi, 'macd': macd,
                'stoch': stoch, 'ta': ta,
                'ext': ext, 'bbp': bbp, 'macdh': mh,
                'ret_5d': ret_5d, 'ret_20d': ret_20d,
                'vel5_sigma': vel5_sigma,
                'pct_ema200': pct_ema200,
                'sigma': sigma,
                'vol_sig': vol_sig, 'vol_mag': vol_mag,
                'regime_mult': rm,
                'win15': win15, 'win30': win30,
                'min30_pct': min_price_30,
            })
    print(f"Records with forward bars: {len(records)}")

    # Bucket by discrete score
    def bucket(score: int) -> str:
        if score < 5:
            return '0-4'
        if score < 10:
            return '5-9'
        if score < 15:
            return '10-14'
        if score < 20:
            return '15-19'
        return '20-24'

    buckets = defaultdict(list)
    for r in records:
        buckets[bucket(r['overall'])].append(r)

    # Overall WR per bucket (re-sanity vs stored)
    print("\n=== Re-measured barrier-touch WR (5y, sanitized) ===")
    print(f"{'Bucket':<8} {'N':>6} {'WR15':>7} {'WR30':>7} {'Ret30%':>8}")
    for b in ['0-4', '5-9', '10-14', '15-19', '20-24']:
        rs = buckets[b]
        w15 = [r['win15'] for r in rs if r['win15'] is not None]
        w30 = [r['win30'] for r in rs if r['win30'] is not None]
        min30 = [-r['min30_pct'] for r in rs if r['min30_pct'] is not None]  # positive = drop magnitude
        wr15 = np.mean(w15) * 100 if w15 else 0
        wr30 = np.mean(w30) * 100 if w30 else 0
        avg_min = np.mean(min30) if min30 else 0
        print(f"{b:<8} {len(rs):>6} {wr15:>6.1f}% {wr30:>6.1f}% {avg_min:>7.2f}%")

    # Deep dive on 0-4: what separates winners (win30=1) from losers (win30=0)?
    print("\n=== 0-4 bucket: winners vs losers (win30) feature means ===")
    rs04 = [r for r in buckets['0-4'] if r['win30'] is not None]
    winners = [r for r in rs04 if r['win30'] == 1]
    losers = [r for r in rs04 if r['win30'] == 0]
    print(f"N winners={len(winners)}, N losers={len(losers)}, WR30={len(winners)/len(rs04)*100:.1f}%")

    def stats(rs, key):
        vals = [r[key] for r in rs if r[key] is not None]
        return (np.mean(vals), np.median(vals), len(vals)) if vals else (None, None, 0)

    feat_keys = ['trend', 'bb', 'rsi', 'macd', 'stoch', 'ta',
                 'ext', 'bbp', 'macdh', 'ret_5d', 'ret_20d',
                 'vel5_sigma', 'pct_ema200', 'sigma', 'vol_mag', 'regime_mult']
    print(f"\n{'feature':<14} {'winners_mean':>14} {'losers_mean':>14} {'diff':>10}")
    for k in feat_keys:
        wm, _, wn = stats(winners, k)
        lm, _, ln = stats(losers, k)
        if wm is None or lm is None:
            continue
        print(f"{k:<14} {wm:>14.3f} {lm:>14.3f} {wm-lm:>+10.3f}")

    # Volume signal breakdown for 0-4
    print("\n=== 0-4 vol_sig breakdown ===")
    from collections import Counter
    wvs = Counter(r['vol_sig'] for r in winners)
    lvs = Counter(r['vol_sig'] for r in losers)
    all_sigs = set(wvs) | set(lvs)
    for s in all_sigs:
        print(f"  {s}: winners={wvs.get(s,0)} losers={lvs.get(s,0)}")

    # Threshold sweep on key features that show separation
    print("\n=== Threshold sweeps on 0-4 bucket ===")

    def sweep(key, thresholds, operator='<'):
        print(f"\nFeature: {key}  ({operator})")
        print(f"  {'threshold':>10} {'N_pass':>6} {'WR30_pass':>10} {'N_fail':>6} {'WR30_fail':>10}")
        for t in thresholds:
            if operator == '<':
                passed = [r for r in rs04 if r[key] is not None and r[key] < t]
                failed = [r for r in rs04 if r[key] is not None and r[key] >= t]
            elif operator == '>':
                passed = [r for r in rs04 if r[key] is not None and r[key] > t]
                failed = [r for r in rs04 if r[key] is not None and r[key] <= t]
            w_p = [r['win30'] for r in passed if r['win30'] is not None]
            w_f = [r['win30'] for r in failed if r['win30'] is not None]
            wrp = np.mean(w_p) * 100 if w_p else 0
            wrf = np.mean(w_f) * 100 if w_f else 0
            print(f"  {t:>10.2f} {len(w_p):>6} {wrp:>9.1f}% {len(w_f):>6} {wrf:>9.1f}%")

    sweep('macdh', [-0.1, -0.05, 0.0, 0.05, 0.1])
    sweep('ret_5d', [-10, -5, -3, 0, 3], operator='<')
    sweep('ret_20d', [-15, -10, -5, 0, 5], operator='<')
    sweep('vel5_sigma', [-3, -2, -1, 0, 1], operator='<')
    sweep('ext', [-20, -15, -10, -5, 0], operator='<')
    sweep('pct_ema200', [-25, -15, -10, -5, 0], operator='<')
    sweep('regime_mult', [0.8, 0.9, 0.95, 1.0, 1.05], operator='<')
    sweep('sigma', [2, 3, 4, 5, 6], operator='>')
    sweep('rsi', [20, 30, 40, 50, 60], operator='>')

    # Also check the same sweep on 5-9 for contrast
    print("\n\n=== Same sweeps on 5-9 bucket (for contrast) ===")
    rs59 = [r for r in buckets['5-9'] if r['win30'] is not None]

    def sweep59(key, thresholds, operator='<'):
        print(f"\nFeature: {key}  ({operator}) [5-9]")
        print(f"  {'threshold':>10} {'N_pass':>6} {'WR30_pass':>10} {'N_fail':>6} {'WR30_fail':>10}")
        for t in thresholds:
            if operator == '<':
                passed = [r for r in rs59 if r[key] is not None and r[key] < t]
                failed = [r for r in rs59 if r[key] is not None and r[key] >= t]
            else:
                passed = [r for r in rs59 if r[key] is not None and r[key] > t]
                failed = [r for r in rs59 if r[key] is not None and r[key] <= t]
            w_p = [r['win30'] for r in passed if r['win30'] is not None]
            w_f = [r['win30'] for r in failed if r['win30'] is not None]
            wrp = np.mean(w_p) * 100 if w_p else 0
            wrf = np.mean(w_f) * 100 if w_f else 0
            print(f"  {t:>10.2f} {len(w_p):>6} {wrp:>9.1f}% {len(w_f):>6} {wrf:>9.1f}%")

    sweep59('macdh', [-0.1, 0.0, 0.1])
    sweep59('ret_5d', [-5, 0], operator='<')
    sweep59('ext', [-15, -5, 0], operator='<')
    sweep59('pct_ema200', [-15, -5, 0], operator='<')

if __name__ == '__main__':
    main()
