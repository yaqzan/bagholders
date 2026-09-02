"""
Profile the put score distribution on the active (v21) algorithm.

Three questions:
  1) How many scores at exactly 0 vs 1-5 vs 6-10 vs 11-15 vs 16-25?
  2) WR15/WR30 gradient per bucket — is <5 really worse than 6-10?
  3) For the 0-pile-up, what are the weight_info components (pre-regime, pre-weekly,
     pre-volume, etc.) telling us about WHY they land at 0?

5y lookback, active version only.
"""
from __future__ import annotations
import json
import numpy as np
from datetime import date, timedelta
from collections import Counter, defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


def _realized_vol(closes: list[float]) -> float | None:
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _put_win(entry, bars, sigma, W):
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    tgt = entry * (1.0 - 1.0 * sigma * scale / 100.0)
    stp = entry * (1.0 + 2.0 * sigma * scale / 100.0)
    for (d_off, hi, lo) in bars:
        if d_off > W:
            return 0
        if lo <= tgt:
            return 1
        if hi >= stp:
            return 0
    return None


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}")

    # --- Question 1: distribution ---
    print("\n" + "=" * 90)
    print("PUT SCORE DISTRIBUTION (5y, all qualifying scores <=25)")
    print("=" * 90)
    q_dist = f"""
        SELECT overall, COUNT(*) AS n
        FROM scores
        WHERE version_id = {vid}
          AND date >= '{cutoff}'
          AND overall <= 25
        GROUP BY overall
        ORDER BY overall
    """
    dist = DB.execute_sql(q_dist).fetchall()
    total = sum(r[1] for r in dist)
    print(f"Total put scores (<=25) over 5y: {total}")
    print(f"{'score':>6} {'N':>8} {'% of <=25':>10} {'cum%':>8}")
    cum = 0
    for score, n in dist:
        cum += n
        print(f"{score:>6} {n:>8} {n/total*100:>9.2f}% {cum/total*100:>7.2f}%")

    # Bucketed summary
    buckets = [
        (0, 0, "=0"),
        (1, 5, "1-5"),
        (6, 10, "6-10"),
        (11, 15, "11-15"),
        (16, 20, "16-20"),
        (21, 25, "21-25"),
    ]
    print(f"\n{'bucket':<10} {'N':>8} {'%':>8}")
    for lo, hi, lbl in buckets:
        n = sum(r[1] for r in dist if lo <= r[0] <= hi)
        print(f"{lbl:<10} {n:>8} {n/total*100:>7.2f}%")

    # --- Question 2: WR gradient per bucket ---
    print("\n" + "=" * 90)
    print("LOADING PUT SCORES FOR BARRIER-TOUCH WR")
    print("=" * 90)
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.price
        FROM scores s
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 25
          AND s.price IS NOT NULL
        ORDER BY s.symbol, s.date
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} put scores")

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    win_by_bucket = defaultdict(lambda: {"w15": [0, 0], "w30": [0, 0]})  # [wins, total]

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
        dates = [b[0] for b in bars_q]

        for r in sym_rows:
            sym_, d, overall, price = r
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
                off = (dates[j] - d).days
                fwd.append((off, highs[j], lows[j]))
                if off > 40:
                    break
            w15 = _put_win(entry, fwd, sigma, 15)
            w30 = _put_win(entry, fwd, sigma, 30)

            # bucket
            bkey = None
            for lo, hi, lbl in buckets:
                if lo <= overall <= hi:
                    bkey = lbl
                    break
            if bkey is None:
                continue
            if w15 is not None:
                win_by_bucket[bkey]["w15"][0] += w15
                win_by_bucket[bkey]["w15"][1] += 1
            if w30 is not None:
                win_by_bucket[bkey]["w30"][0] += w30
                win_by_bucket[bkey]["w30"][1] += 1

    print("\n" + "=" * 90)
    print("PUT WR GRADIENT BY BUCKET (barrier-touch, K=1.0 sigma target / M=2.0 sigma stop)")
    print("=" * 90)
    print(f"{'bucket':<10} {'N_w15':>8} {'WR15':>7} {'N_w30':>8} {'WR30':>7}")
    for lo, hi, lbl in buckets:
        d = win_by_bucket[lbl]
        n15 = d["w15"][1]
        n30 = d["w30"][1]
        w15 = d["w15"][0] / n15 * 100 if n15 else 0.0
        w30 = d["w30"][0] / n30 * 100 if n30 else 0.0
        print(f"{lbl:<10} {n15:>8} {w15:>6.1f}% {n30:>8} {w30:>6.1f}%")

    # --- Question 3: what pushes scores to exactly 0? Inspect weight_info ---
    print("\n" + "=" * 90)
    print("ZERO PILE-UP FORENSICS (weight_info components for overall=0)")
    print("=" * 90)
    q_zero = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd,
               s.stoch, s.technical_alignment, s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.weight_info, s.price,
               i.ema_50
        FROM scores s
        LEFT JOIN indicators i USING (symbol, date)
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall = 0
        LIMIT 500
    """
    zrows = DB.execute_sql(q_zero).fetchall()
    print(f"Sampled {len(zrows)} rows at overall=0 (out of total count above)")

    pre_regimes = []
    pre_weeklies = []  # pre_regime minus weekly_adjustment
    weekly_adjs = []
    components_sum_weighted = []
    regime_mults = []
    vol_mags = []
    vol_sigs = Counter()
    ext_list = []

    for r in zrows:
        (sym, d, overall, trend, bb, rsi, macd, stoch, ta, vsig, vmag, rmult,
         winfo_str, price, ema50) = r
        vol_sigs[vsig or 'NONE'] += 1
        if vmag is not None:
            vol_mags.append(float(vmag))
        if rmult is not None:
            regime_mults.append(float(rmult))
        if price and ema50 and float(ema50) > 0:
            ext_list.append((float(price) / float(ema50) - 1.0) * 100.0)
        if winfo_str:
            try:
                wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                pr = wi.get('pre_regime')
                wadj = wi.get('w_adj') or wi.get('weekly_adj') or 0
                if pr is not None:
                    pre_regimes.append(float(pr))
                    pre_weeklies.append(float(pr) - float(wadj))
                    weekly_adjs.append(float(wadj))
            except Exception:
                pass

    def summarize(name, arr):
        if not arr:
            print(f"  {name}: N=0")
            return
        a = np.array(arr)
        print(f"  {name}: N={len(a)} mean={a.mean():+.2f} median={np.median(a):+.2f} "
              f"p10={np.percentile(a,10):+.2f} p90={np.percentile(a,90):+.2f} "
              f"min={a.min():+.2f} max={a.max():+.2f}")

    print("\n[Distribution of components at overall=0]")
    summarize("pre_regime (post-weekly, post-volume)", pre_regimes)
    summarize("pre_weekly (pre_regime - weekly_adj)", pre_weeklies)
    summarize("weekly_adj", weekly_adjs)
    summarize("regime_multiplier", regime_mults)
    summarize("volume_magnitude", vol_mags)
    summarize("ext (pct from EMA50)", ext_list)
    print(f"  volume_signal distribution: {dict(vol_sigs)}")


if __name__ == '__main__':
    main()
