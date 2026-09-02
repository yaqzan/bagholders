"""
Fine-grain diagnostic for the put low-tail (scores 0 through 15).

Extends put_zero_profile.py with:

  1) Per-score (not just bucketed) WR15/WR30/Ret30/MAE30/MFE30.
     Isolates whether score=5 or score=1-4 drives the observed 1-5 dip.

  2) Symbol concentration per sub-bucket (=0, 1-4, 5, 6-10, 11-15).
     Gini-style top-N share + unique symbol count. Tests "chronically-weak
     ticker serial re-entry" hypothesis.

  3) Component mean distributions per sub-bucket (trend/bb/rsi/macd/stoch/ta +
     volume_signal mix). Tests "score=5 is the collision point of multiple
     suppression paths" hypothesis.

  4) weight_info decomposition per sub-bucket (pre_weekly, weekly_adj, pre_regime,
     regime_mult). Tests "weekly-crushed weak puts dominate low tail" hypothesis.

  5) Regime distribution per sub-bucket (regime_mult banding).

  6) Serial re-entry check: days_since_last_signal on same symbol.

Runs against the active version over 5y by default.
"""
from __future__ import annotations
import json
import numpy as np
from datetime import date, timedelta
from collections import Counter, defaultdict

from database.trader_database import DB
from database.models.core import AlgorithmVersion


SUB_BUCKETS = [
    (0, 0, "=0"),
    (1, 4, "1-4"),
    (5, 5, "=5"),
    (6, 10, "6-10"),
    (11, 15, "11-15"),
    (16, 20, "16-20"),
    (21, 25, "21-25"),
]


def _realized_vol(closes):
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _put_barrier(entry, bars, sigma, W):
    """Barrier-touch: returns (win, exit_ret, mae_pct, mfe_pct) or None.

    win: 1 if lo <= K·sigma·sqrt(W/30) before hi >= M·sigma·sqrt(W/30), 0 if stop or
         expire, None if insufficient forward history.
    exit_ret: side-adjusted (puts positive on drop) close-to-exit return %.
    mae_pct / mfe_pct: worst-case adverse / best-case favorable excursion in % of entry,
         side-adjusted (positive = move against / with the thesis).
    """
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    tgt = entry * (1.0 - 1.0 * sigma * scale / 100.0)  # puts win if price drops K·σ
    stp = entry * (1.0 + 2.0 * sigma * scale / 100.0)  # puts stop if price rises M·σ

    mfe = 0.0  # favorable = drop (positive number stored)
    mae = 0.0  # adverse = rise (positive number stored)
    last = None
    for (d_off, hi, lo, cl) in bars:
        if d_off > W:
            break
        # excursions
        fav = (entry - lo) / entry * 100.0
        adv = (hi - entry) / entry * 100.0
        if fav > mfe:
            mfe = fav
        if adv > mae:
            mae = adv
        last = cl
        if lo <= tgt:
            exit_ret = (entry - lo) / entry * 100.0
            return (1, exit_ret, mae, mfe)
        if hi >= stp:
            exit_ret = -(hi - entry) / entry * 100.0
            return (0, exit_ret, mae, mfe)
    if last is None:
        return None
    # expired
    exit_ret = (entry - last) / entry * 100.0
    return (0, exit_ret, mae, mfe)


def _gini(values):
    """Gini coefficient on a list of counts (symbol frequencies)."""
    a = np.asarray(sorted(values), dtype=float)
    n = len(a)
    if n == 0 or a.sum() == 0:
        return 0.0
    cum = np.cumsum(a)
    return float((2 * np.sum((np.arange(1, n + 1)) * a) - (n + 1) * cum[-1]) / (n * cum[-1]))


def _classify(score):
    for lo, hi, lbl in SUB_BUCKETS:
        if lo <= score <= hi:
            return lbl
    return None


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}")
    print(f"Today = {date.today().isoformat()}\n")

    # Pull ALL put scores and indicator context in one query
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd, s.stoch,
               s.technical_alignment, s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.weight_info, s.price,
               i.ema_50
        FROM scores s
        LEFT JOIN indicators i
          ON i.symbol = s.symbol AND i.date = s.date
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 25
          AND s.price IS NOT NULL
        ORDER BY s.symbol, s.date
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} put scores (<=25) over 5y")

    # Pre-group by symbol for barrier walk
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    # Per-signal results keyed by bucket label
    per_bucket = defaultdict(lambda: {
        'n_w15_total': 0, 'w15_wins': 0,
        'n_w30_total': 0, 'w30_wins': 0,
        'ret30': [], 'mae30': [], 'mfe30': [],
        'trend': [], 'bb': [], 'rsi': [], 'macd': [], 'stoch': [], 'ta': [],
        'vsig': Counter(), 'vmag': [],
        'pre_weekly': [], 'weekly_adj': [], 'pre_regime': [], 'regime_mult': [],
        'ext': [],
        'symbols': Counter(),
        'days_since_last': [],  # days since PRIOR put signal (<=25) on same sym
    })

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

        sym_rows_sorted = sorted(sym_rows, key=lambda r: r[1])
        prior_date = None

        for r in sym_rows_sorted:
            (sym_, d, overall, trend, bb, rsi, macd, stoch, ta, vsig, vmag, rmult,
             winfo_str, price, ema50) = r
            bkey = _classify(overall)
            if bkey is None:
                continue
            B = per_bucket[bkey]

            if prior_date is not None:
                B['days_since_last'].append((d - prior_date).days)
            prior_date = d

            if d not in d_idx:
                continue
            i = d_idx[d]
            if i < 30:
                continue
            sigma = _realized_vol(closes[:i + 1])
            if sigma is None:
                continue
            entry = closes[i]

            # build forward bars up to 35 calendar days
            fwd = []
            for j in range(i + 1, len(bars_q)):
                off = (dates_bar[j] - d).days
                if off > 40:
                    break
                fwd.append((off, highs[j], lows[j], closes[j]))

            r15 = _put_barrier(entry, fwd, sigma, 15)
            r30 = _put_barrier(entry, fwd, sigma, 30)

            if r15 is not None:
                w, _, _, _ = r15
                B['n_w15_total'] += 1
                B['w15_wins'] += w
            if r30 is not None:
                w, exret, maev, mfev = r30
                B['n_w30_total'] += 1
                B['w30_wins'] += w
                B['ret30'].append(exret)
                B['mae30'].append(maev)
                B['mfe30'].append(mfev)

            # components
            if trend is not None: B['trend'].append(float(trend))
            if bb is not None: B['bb'].append(float(bb))
            if rsi is not None: B['rsi'].append(float(rsi))
            if macd is not None: B['macd'].append(float(macd))
            if stoch is not None: B['stoch'].append(float(stoch))
            if ta is not None: B['ta'].append(float(ta))
            B['vsig'][vsig or 'NONE'] += 1
            if vmag is not None: B['vmag'].append(float(vmag))
            if rmult is not None: B['regime_mult'].append(float(rmult))
            if price and ema50 and float(ema50) > 0:
                B['ext'].append((float(price) / float(ema50) - 1.0) * 100.0)

            # weight_info forensics
            if winfo_str:
                try:
                    wi = json.loads(winfo_str) if isinstance(winfo_str, str) else winfo_str
                    pr = wi.get('pre_regime')
                    wadj = wi.get('w_adj') or wi.get('weekly_adj') or 0
                    if pr is not None:
                        B['pre_regime'].append(float(pr))
                        B['pre_weekly'].append(float(pr) - float(wadj))
                        B['weekly_adj'].append(float(wadj))
                except Exception:
                    pass

            B['symbols'][sym] += 1

    # --- Reports ---
    def _stat(arr, fmt=".2f"):
        if not arr:
            return " --   --   --"
        a = np.asarray(arr, dtype=float)
        return f"{a.mean():{fmt}} {np.median(a):{fmt}} {np.std(a):{fmt}}"

    print("\n" + "=" * 110)
    print("PER-SUBBUCKET WR / RET / EXCURSION")
    print("=" * 110)
    print(f"{'bucket':<8} {'N_w15':>7} {'WR15':>6} {'N_w30':>7} {'WR30':>6} "
          f"{'Ret30_mean':>11} {'Ret30_med':>10} {'MAE30':>7} {'MFE30':>7} {'Cap30':>7}")
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        n15 = B['n_w15_total']
        n30 = B['n_w30_total']
        wr15 = B['w15_wins'] / n15 * 100 if n15 else 0.0
        wr30 = B['w30_wins'] / n30 * 100 if n30 else 0.0
        ret30 = np.mean(B['ret30']) if B['ret30'] else 0.0
        ret30_med = np.median(B['ret30']) if B['ret30'] else 0.0
        mae30 = np.mean(B['mae30']) if B['mae30'] else 0.0
        mfe30 = np.mean(B['mfe30']) if B['mfe30'] else 0.0
        cap30 = ret30 / mfe30 if mfe30 else 0.0
        print(f"{lbl:<8} {n15:>7} {wr15:>5.1f}% {n30:>7} {wr30:>5.1f}% "
              f"{ret30:>+10.2f}% {ret30_med:>+9.2f}% {mae30:>6.2f}% {mfe30:>6.2f}% {cap30:>7.3f}")

    print("\n" + "=" * 110)
    print("COMPONENT DISTRIBUTION (mean / median / std per bucket)")
    print("=" * 110)
    print(f"{'bucket':<8} {'trend':>18} {'bb':>18} {'rsi':>18} {'macd':>18} {'stoch':>18} {'ta':>18}")
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        print(f"{lbl:<8} {_stat(B['trend']):>18} {_stat(B['bb']):>18} {_stat(B['rsi']):>18} "
              f"{_stat(B['macd']):>18} {_stat(B['stoch']):>18} {_stat(B['ta']):>18}")

    print("\n" + "=" * 110)
    print("WEIGHT_INFO DECOMPOSITION (mean / median / std per bucket)")
    print("=" * 110)
    print(f"{'bucket':<8} {'pre_weekly':>18} {'weekly_adj':>18} {'pre_regime':>18} {'regime_mult':>18} {'ext_%':>18}")
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        print(f"{lbl:<8} {_stat(B['pre_weekly']):>18} {_stat(B['weekly_adj']):>18} "
              f"{_stat(B['pre_regime']):>18} {_stat(B['regime_mult'], '.3f'):>18} "
              f"{_stat(B['ext']):>18}")

    print("\n" + "=" * 110)
    print("VOLUME SIGNAL MIX + VOLUME MAG (per bucket)")
    print("=" * 110)
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        total = sum(B['vsig'].values())
        if total == 0:
            print(f"{lbl:<8} n=0")
            continue
        parts = []
        for sig in ['CONVICTION', 'REJECTION', 'ABSORPTION', 'CLIMAX', 'THIN_AIR', 'NONE']:
            cnt = B['vsig'].get(sig, 0)
            if cnt:
                parts.append(f"{sig}={cnt / total * 100:.0f}%")
        vmag_mean = np.mean(B['vmag']) if B['vmag'] else 0.0
        print(f"{lbl:<8}  vmag_mean={vmag_mean:+.2f}  {' '.join(parts)}")

    print("\n" + "=" * 110)
    print("SYMBOL CONCENTRATION (is the bucket driven by a few chronically-weak tickers?)")
    print("=" * 110)
    print(f"{'bucket':<8} {'total_sigs':>10} {'uniq_sym':>10} {'top1_%':>8} {'top5_%':>8} "
          f"{'top10_%':>8} {'gini':>8}   {'top-5 symbols (sym:n)':<60}")
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        syms = B['symbols']
        total = sum(syms.values())
        if total == 0:
            print(f"{lbl:<8} n=0")
            continue
        uniq = len(syms)
        sorted_counts = syms.most_common()
        top1 = sorted_counts[0][1] / total * 100
        top5 = sum(c for _, c in sorted_counts[:5]) / total * 100
        top10 = sum(c for _, c in sorted_counts[:10]) / total * 100
        gini = _gini(list(syms.values()))
        top5_str = ' '.join(f"{s}:{n}" for s, n in sorted_counts[:5])
        print(f"{lbl:<8} {total:>10} {uniq:>10} {top1:>7.1f}% {top5:>7.1f}% "
              f"{top10:>7.1f}% {gini:>7.3f}   {top5_str:<60}")

    print("\n" + "=" * 110)
    print("SERIAL RE-ENTRY (days since PRIOR put signal <=25 on same symbol)")
    print("=" * 110)
    print(f"{'bucket':<8} {'N':>7} {'mean':>8} {'median':>8} {'p25':>8} {'p75':>8} "
          f"{'<=3d%':>8} {'<=7d%':>8} {'<=15d%':>8}")
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        arr = B['days_since_last']
        if not arr:
            print(f"{lbl:<8} n=0")
            continue
        a = np.asarray(arr, dtype=float)
        n = len(a)
        pct3 = (a <= 3).sum() / n * 100
        pct7 = (a <= 7).sum() / n * 100
        pct15 = (a <= 15).sum() / n * 100
        print(f"{lbl:<8} {n:>7} {a.mean():>8.1f} {np.median(a):>7.1f} "
              f"{np.percentile(a, 25):>7.1f} {np.percentile(a, 75):>7.1f} "
              f"{pct3:>7.1f}% {pct7:>7.1f}% {pct15:>7.1f}%")

    print("\n" + "=" * 110)
    print("REGIME DISTRIBUTION (signal date regime_mult banding)")
    print("=" * 110)
    bands = [
        (0.70, 0.80, 'STRESS'),
        (0.80, 0.88, 'CAUTION-'),
        (0.88, 1.00, 'CAUTION'),
        (1.00, 1.05, 'HEALTHY'),
        (1.05, 1.12, 'BULL'),
    ]
    print(f"{'bucket':<8}  " + "  ".join(f"{lbl:>10}" for _, _, lbl in bands))
    for lo, hi, lbl in SUB_BUCKETS:
        B = per_bucket[lbl]
        arr = B['regime_mult']
        if not arr:
            print(f"{lbl:<8}  n=0")
            continue
        total = len(arr)
        cells = []
        for blo, bhi, bname in bands:
            cnt = sum(1 for v in arr if blo <= v < bhi)
            cells.append(f"{cnt / total * 100:>9.1f}%")
        print(f"{lbl:<8}  " + "  ".join(cells))


if __name__ == '__main__':
    main()
