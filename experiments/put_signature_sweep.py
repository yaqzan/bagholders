"""
Validate the two hypothesized put sub-bucket signatures uncovered by
put_lowtail_diagnostic.py.

Hypothesis A — "Exhaustion puts" (promotion candidate):
  overall <= k  AND  stoch >= s  [AND vol_sig in {...}]
  Expected: higher WR15/Cap30 than the raw overall<=k bucket.

Hypothesis B — "=5 weak signal" (demotion candidate):
  overall == 5  AND  stoch < s
  Expected: lower WR15 than the raw overall==5 bucket.

Tests each signature across:
  - 5y aggregate (statistical power)
  - Per-year 2021..2025 (regime stability)

Reports N, WR15, WR30, Ret30, MAE30, MFE30, Cap30 per signature per window.

Also prints a baseline anchor: the raw overall<=k bucket numbers from the
signature's implied population, so the gain over baseline is visible.

5y lookback, active version.
"""
from __future__ import annotations
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
    mfe = 0.0
    mae = 0.0
    last = None
    for (d_off, hi, lo, cl) in bars:
        if d_off > W:
            break
        fav = (entry - lo) / entry * 100.0
        adv = (hi - entry) / entry * 100.0
        if fav > mfe: mfe = fav
        if adv > mae: mae = adv
        last = cl
        if lo <= tgt:
            exit_ret = (entry - lo) / entry * 100.0
            return (1, exit_ret, mae, mfe)
        if hi >= stp:
            exit_ret = -(hi - entry) / entry * 100.0
            return (0, exit_ret, mae, mfe)
    if last is None:
        return None
    exit_ret = (entry - last) / entry * 100.0
    return (0, exit_ret, mae, mfe)


# Signature definitions
# Each sig = (label, predicate(row_dict)->bool)
# row_dict keys: overall, trend, bb, rsi, macd, stoch, ta, vsig, vmag, rmult, ext
def _vs_in(vsig, names):
    return vsig in names

SIGNATURES = [
    # ---- Baselines (cumulative) ----
    ("BASE <=4 (all)",            lambda r: r['overall'] <= 4),
    ("BASE <=5 (all)",            lambda r: r['overall'] <= 5),
    ("BASE ==5 (all)",            lambda r: r['overall'] == 5),
    ("BASE 1-4",                  lambda r: 1 <= r['overall'] <= 4),
    ("BASE 6-10",                 lambda r: 6 <= r['overall'] <= 10),

    # ---- Exhaustion puts: stoch ceiling variants ----
    ("EXH <=4 & stoch>=80",       lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 80),
    ("EXH <=4 & stoch>=85",       lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 85),
    ("EXH <=4 & stoch>=90",       lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 90),
    ("EXH <=5 & stoch>=80",       lambda r: r['overall'] <= 5 and r['stoch'] is not None and r['stoch'] >= 80),
    ("EXH <=5 & stoch>=85",       lambda r: r['overall'] <= 5 and r['stoch'] is not None and r['stoch'] >= 85),
    ("EXH <=5 & stoch>=90",       lambda r: r['overall'] <= 5 and r['stoch'] is not None and r['stoch'] >= 90),
    ("EXH <=10 & stoch>=80",      lambda r: r['overall'] <= 10 and r['stoch'] is not None and r['stoch'] >= 80),
    ("EXH <=10 & stoch>=85",      lambda r: r['overall'] <= 10 and r['stoch'] is not None and r['stoch'] >= 85),
    ("EXH <=15 & stoch>=85",      lambda r: r['overall'] <= 15 and r['stoch'] is not None and r['stoch'] >= 85),

    # ---- Exhaustion puts: add volume sig filter ----
    ("EXH <=4 stoch>=80 REJ",     lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 80 and _vs_in(r['vsig'], ('REJECTION',))),
    ("EXH <=4 stoch>=80 REJ/CON", lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 80 and _vs_in(r['vsig'], ('REJECTION', 'CONVICTION'))),
    ("EXH <=5 stoch>=80 REJ/CON", lambda r: r['overall'] <= 5 and r['stoch'] is not None and r['stoch'] >= 80 and _vs_in(r['vsig'], ('REJECTION', 'CONVICTION'))),
    ("EXH <=10 stoch>=80 REJ/CON",lambda r: r['overall'] <= 10 and r['stoch'] is not None and r['stoch'] >= 80 and _vs_in(r['vsig'], ('REJECTION', 'CONVICTION'))),
    ("EXH <=15 stoch>=85 REJ/CON",lambda r: r['overall'] <= 15 and r['stoch'] is not None and r['stoch'] >= 85 and _vs_in(r['vsig'], ('REJECTION', 'CONVICTION'))),

    # ---- Exhaustion puts: add RSI confirm ----
    ("EXH <=4 stoch>=85 rsi>=45", lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 85 and r['rsi'] is not None and r['rsi'] >= 45),
    ("EXH <=10 stoch>=85 rsi>=50",lambda r: r['overall'] <= 10 and r['stoch'] is not None and r['stoch'] >= 85 and r['rsi'] is not None and r['rsi'] >= 50),

    # ---- =5 weak signature (demotion candidate) ----
    ("WEAK ==5 stoch<75",         lambda r: r['overall'] == 5 and r['stoch'] is not None and r['stoch'] < 75),
    ("WEAK ==5 stoch<60",         lambda r: r['overall'] == 5 and r['stoch'] is not None and r['stoch'] < 60),
    ("WEAK ==5 stoch<80 vs not-REJ",
                                   lambda r: r['overall'] == 5 and r['stoch'] is not None and r['stoch'] < 80 and not _vs_in(r['vsig'], ('REJECTION',))),
    ("WEAK 3-7 stoch<60",         lambda r: 3 <= r['overall'] <= 7 and r['stoch'] is not None and r['stoch'] < 60),

    # ---- Strong =5 (the other side of =5 — does it exist?) ----
    ("STRONG ==5 stoch>=85",      lambda r: r['overall'] == 5 and r['stoch'] is not None and r['stoch'] >= 85),

    # ---- ext-above-EMA50 test: is the exhaustion signal on oversold ext stocks? ----
    ("EXH <=4 stoch>=85 ext<=-5%",lambda r: r['overall'] <= 4 and r['stoch'] is not None and r['stoch'] >= 85 and r['ext'] is not None and r['ext'] <= -5),
]


def _year_of(d):
    return d.year


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}\n")

    # Pull put scores + indicator context
    q = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd, s.stoch,
               s.technical_alignment, s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.price,
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
    print(f"Loaded {len(rows)} put scores")

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    # Per-signature, per-window accumulators:
    # key = (sig_label, window_label) -> {'wins15': 0, 'tot15': 0, 'wins30': 0, 'tot30': 0,
    #                                     'ret30': [], 'mae30': [], 'mfe30': []}
    acc = defaultdict(lambda: {'wins15': 0, 'tot15': 0, 'wins30': 0, 'tot30': 0,
                                'ret30': [], 'mae30': [], 'mfe30': []})

    windows = ['5y', '2021', '2022', '2023', '2024', '2025', '2026']

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
             price, ema50) = r
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
                if off > 40:
                    break
                fwd.append((off, highs[j], lows[j], closes[j]))

            r15 = _put_barrier(entry, fwd, sigma, 15)
            r30 = _put_barrier(entry, fwd, sigma, 30)
            if r15 is None and r30 is None:
                continue

            ext = None
            if price and ema50 and float(ema50) > 0:
                ext = (float(price) / float(ema50) - 1.0) * 100.0

            row_dict = {
                'overall': overall,
                'trend': float(trend) if trend is not None else None,
                'bb': float(bb) if bb is not None else None,
                'rsi': float(rsi) if rsi is not None else None,
                'macd': float(macd) if macd is not None else None,
                'stoch': float(stoch) if stoch is not None else None,
                'ta': float(ta) if ta is not None else None,
                'vsig': vsig,
                'vmag': float(vmag) if vmag is not None else None,
                'rmult': float(rmult) if rmult is not None else None,
                'ext': ext,
            }

            year = d.year
            yr_lbl = str(year)
            win_set = ['5y']
            if yr_lbl in windows:
                win_set.append(yr_lbl)

            for label, pred in SIGNATURES:
                try:
                    if not pred(row_dict):
                        continue
                except Exception:
                    continue
                for w in win_set:
                    A = acc[(label, w)]
                    if r15 is not None:
                        A['tot15'] += 1
                        A['wins15'] += r15[0]
                    if r30 is not None:
                        A['tot30'] += 1
                        A['wins30'] += r30[0]
                        A['ret30'].append(r30[1])
                        A['mae30'].append(r30[2])
                        A['mfe30'].append(r30[3])

    def _row(lbl, w):
        A = acc.get((lbl, w))
        if not A or A['tot15'] == 0:
            return f"{'--':>6} {'--':>6} {'--':>6} {'--':>7} {'--':>7} {'--':>7} {'--':>6}"
        n = A['tot15']
        wr15 = A['wins15'] / n * 100
        wr30 = A['wins30'] / A['tot30'] * 100 if A['tot30'] else 0
        ret30 = np.mean(A['ret30']) if A['ret30'] else 0
        mae30 = np.mean(A['mae30']) if A['mae30'] else 0
        mfe30 = np.mean(A['mfe30']) if A['mfe30'] else 0
        cap30 = ret30 / mfe30 if mfe30 else 0
        return f"{n:>6} {wr15:>5.1f}% {wr30:>5.1f}% {ret30:>+6.2f}% {mae30:>6.2f}% {mfe30:>6.2f}% {cap30:>6.3f}"

    # --- 5y aggregate table ---
    print("\n" + "=" * 110)
    print("5Y AGGREGATE PER SIGNATURE")
    print("=" * 110)
    print(f"{'signature':<40} {'N':>6} {'WR15':>6} {'WR30':>6} {'Ret30':>7} {'MAE30':>7} {'MFE30':>7} {'Cap30':>6}")
    print("-" * 110)
    for lbl, _ in SIGNATURES:
        print(f"{lbl:<40} {_row(lbl, '5y')}")

    # --- Per-year WR15 stability table (focus on signatures with enough N) ---
    print("\n" + "=" * 110)
    print("WR15 (% / N) BY YEAR — stability check for promotion/demotion candidates")
    print("=" * 110)
    print(f"{'signature':<40} " + " ".join(f"{w:>14}" for w in ['2021', '2022', '2023', '2024', '2025']))
    print("-" * 110)
    for lbl, _ in SIGNATURES:
        cells = []
        for w in ['2021', '2022', '2023', '2024', '2025']:
            A = acc.get((lbl, w))
            if not A or A['tot15'] == 0:
                cells.append(f"{'--':>14}")
                continue
            wr = A['wins15'] / A['tot15'] * 100
            cells.append(f"{wr:>5.1f}%/N={A['tot15']:<5}")
        print(f"{lbl:<40} " + " ".join(cells))


if __name__ == '__main__':
    main()
