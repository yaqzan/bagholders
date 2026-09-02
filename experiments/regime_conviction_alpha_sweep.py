"""
Fine-grain alpha sweep on conviction-conditional regime dampening.

LIN_A05 (alpha=0.5) emerged as the Pareto winner in regime_conviction_ab.py
because 95+ WR INCREASED to 96.4% (vs BASE 91.7%) while N expanded modestly
(+17%). This sweep tunes alpha in tighter increments to find the optimum.

Variants (all conviction-linear):
  BASE   - stored
  A03    - alpha=0.30
  A035   - alpha=0.35
  A04    - alpha=0.40
  A045   - alpha=0.45
  A05    - alpha=0.50  (baseline winner)
  A055   - alpha=0.55
  A060   - alpha=0.60

Metric: headline 5y + per-year 95+ / 90+ / 85+ WR and N + put tail.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def _barrier_walk(side, entry, bars, sigma, W):
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    if side == 'low':
        tgt = entry * (1.0 + 2.0 * sigma * scale / 100.0)
        stp = entry * (1.0 - 5.0 * sigma * scale / 100.0)
    else:
        tgt = entry * (1.0 - 1.0 * sigma * scale / 100.0)
        stp = entry * (1.0 + 2.0 * sigma * scale / 100.0)
    for (off, hi, lo) in bars:
        if off > W:
            return 0
        if side == 'low':
            if hi >= tgt: return 1
            if lo <= stp: return 0
        else:
            if lo <= tgt: return 1
            if hi >= stp: return 0
    return None


def _apply_mult(pre_regime, mult):
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


def _conviction(pre_regime):
    return min(1.0, abs(pre_regime - 50) / 30.0)


def _eff_mult(stored_mult, pre_regime, alpha):
    if alpha == 0.0:
        return stored_mult
    c = _conviction(pre_regime)
    damp = 1.0 - c * alpha
    return 1.0 + (stored_mult - 1.0) * damp


ALPHAS = [
    ('BASE', 0.00),
    ('A030', 0.30),
    ('A035', 0.35),
    ('A040', 0.40),
    ('A045', 0.45),
    ('A050', 0.50),
    ('A055', 0.55),
    ('A060', 0.60),
]

CALL_CUTS = [70, 75, 80, 85, 90, 95]
PUT_CUTS = [25, 20, 15, 10, 5]


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}\n")

    print("Loading peaks...")
    rows = DB.execute_sql(f"""
        SELECT symbol, date, overall, regime_multiplier, weight_info
        FROM scores
        WHERE version_id = {vid}
          AND date >= '{cutoff}'
          AND weight_info IS NOT NULL
        ORDER BY symbol, date
    """).fetchall()

    peaks = []
    for sym, d, overall, sm, wi in rows:
        try:
            wi_d = json.loads(wi) if isinstance(wi, str) else wi
            pr = wi_d.get('pre_regime')
            if pr is None: continue
            pr = int(pr)
        except Exception:
            continue
        if not (pr >= 60 or pr <= 40):
            continue
        peaks.append({
            'sym': sym, 'date': d, 'pre_regime': pr,
            'stored_mult': float(sm) if sm is not None else 1.0,
        })
    print(f"  Loaded {len(peaks):,} candidate peaks")

    # Apply variants
    print("Applying variants...")
    for pk in peaks:
        pr = pk['pre_regime']
        sm = pk['stored_mult']
        side = 'call' if pr >= 50 else 'put'
        pk['side'] = side
        pk['variants'] = {}
        for vname, alpha in ALPHAS:
            m = _eff_mult(sm, pr, alpha)
            overall = _apply_mult(pr, m)
            pk['variants'][vname] = overall

    # Tally counts
    print("Tallying counts...")
    counts = defaultdict(int)
    for pk in peaks:
        yr = pk['date'].year
        for vname, _ in ALPHAS:
            ov = pk['variants'][vname]
            side = pk['side']
            if side == 'call':
                for cut in CALL_CUTS:
                    if ov >= cut:
                        counts[(vname, yr, f"{cut}+")] += 1
                        counts[(vname, '5y', f"{cut}+")] += 1
            else:
                for cut in PUT_CUTS:
                    if ov <= cut:
                        counts[(vname, yr, f"<{cut}")] += 1
                        counts[(vname, '5y', f"<{cut}")] += 1

    # Barrier walk
    print("\nBarrier walk setup...")
    qual_set = set()
    for pk in peaks:
        for vname, _ in ALPHAS:
            ov = pk['variants'][vname]
            if ov >= 70 or ov <= 25:
                qual_set.add((pk['sym'], pk['date']))
                break
    symbols_needed = set(s for s, _ in qual_set)
    print(f"  {len(qual_set):,} qualifying pairs across {len(symbols_needed):,} symbols")

    price_cache = {}
    for i, sym in enumerate(sorted(symbols_needed)):
        if i % 100 == 0:
            print(f"    ...loading {i}/{len(symbols_needed)}", flush=True)
        price_cache[sym] = DB.execute_sql(f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date >= '{cutoff}' ORDER BY date
        """).fetchall()

    print(f"\n  Computing {len(qual_set):,} barrier outcomes...")
    outcomes = {}
    for i, (sym, d) in enumerate(qual_set):
        if i % 10000 == 0:
            print(f"    ...walk {i}/{len(qual_set)}", flush=True)
        bars_list = price_cache.get(sym, [])
        d_idx = {b[0]: idx for idx, b in enumerate(bars_list)}
        idx = d_idx.get(d)
        if idx is None or idx < 30:
            continue
        closes = [float(b[1]) for b in bars_list]
        highs = [float(b[2]) for b in bars_list]
        lows = [float(b[3]) for b in bars_list]
        sigma = _realized_vol(closes[:idx + 1])
        if sigma is None:
            continue
        entry = closes[idx]
        fwd = []
        for j in range(idx + 1, len(bars_list)):
            off = (bars_list[j][0] - d).days
            if off > 40: break
            fwd.append((off, highs[j], lows[j]))
        outcomes[(sym, d)] = {
            'w15c': _barrier_walk('low',  entry, fwd, sigma, 15),
            'w15p': _barrier_walk('high', entry, fwd, sigma, 15),
        }

    print("\nAggregating WR...")
    wr = defaultdict(lambda: [0, 0])
    for pk in peaks:
        out = outcomes.get((pk['sym'], pk['date']))
        if out is None: continue
        yr = pk['date'].year
        for vname, _ in ALPHAS:
            ov = pk['variants'][vname]
            side = pk['side']
            if side == 'call' and out['w15c'] is not None:
                for cut in CALL_CUTS:
                    if ov >= cut:
                        for w in ('5y', yr):
                            wr[(vname, w, f"{cut}+")][0] += out['w15c']
                            wr[(vname, w, f"{cut}+")][1] += 1
            elif side == 'put' and out['w15p'] is not None:
                for cut in PUT_CUTS:
                    if ov <= cut:
                        for w in ('5y', yr):
                            wr[(vname, w, f"<{cut}")][0] += out['w15p']
                            wr[(vname, w, f"<{cut}")][1] += 1

    # ---- Per-alpha 5y summary ----
    print("\n" + "=" * 150)
    print("5Y HEADLINE PER ALPHA (WR% / N) — focus on 95+ preservation + 85+/80+ stability")
    print("=" * 150)
    print(f"{'variant':<8}  "
          f"{'c95+':>14} {'c90+':>14} {'c85+':>14} {'c80+':>14} {'c75+':>14} {'c70+':>14}"
          f"  {'<5':>12} {'<15':>14} {'<25':>14}")
    for vname, _ in ALPHAS:
        def _fmt(k):
            w, t = wr.get((vname, '5y', k), [0, 0])
            if t == 0: return f"{'--':>14}"
            return f"{w/t*100:5.1f}%/{t:>6}"
        def _fmtp(k):
            w, t = wr.get((vname, '5y', k), [0, 0])
            if t == 0: return f"{'--':>12}"
            return f"{w/t*100:5.1f}%/{t:>4}"
        print(f"{vname:<8}  "
              f"{_fmt('95+')} {_fmt('90+')} {_fmt('85+')} {_fmt('80+')} {_fmt('75+')} {_fmt('70+')}"
              f"  {_fmtp('<5')} {_fmt('<15')} {_fmt('<25')}")

    # ---- Per-year headline ----
    print("\n" + "=" * 150)
    print("PER-YEAR 95+ WR/N and 70+ WR/N and <25 WR/N")
    print("=" * 150)
    for yr in [2022, 2023, 2024, 2025]:
        print(f"\n--- {yr} ---")
        print(f"{'variant':<8}  "
              f"{'c95+':>12} {'c90+':>12} {'c85+':>14} {'c80+':>14} {'c70+':>14}"
              f"   {'<15':>14} {'<25':>14}")
        for vname, _ in ALPHAS:
            def _fmt(k):
                w, t = wr.get((vname, yr, k), [0, 0])
                if t == 0: return f"{'--':>14}"
                return f"{w/t*100:5.1f}%/{t:>6}"
            def _fmts(k):
                w, t = wr.get((vname, yr, k), [0, 0])
                if t == 0: return f"{'--':>12}"
                return f"{w/t*100:5.1f}%/{t:>4}"
            print(f"{vname:<8}  "
                  f"{_fmts('95+')} {_fmts('90+')} {_fmt('85+')} {_fmt('80+')} {_fmt('70+')}"
                  f"   {_fmt('<15')} {_fmt('<25')}")

    # ---- Per-trade EV analysis ----
    # Using TP=+29% net / SL=-37.3% net (calls); TP=+29% / SL=-22.3% (puts)
    print("\n" + "=" * 150)
    print("PER-TRADE EV (net, post-slippage) 5y AGGREGATE")
    print("  Calls: TP=+29%, SL=-37.3% (BE=56.3%)")
    print("  Puts:  TP=+29%, SL=-22.3% (BE=43.5%)")
    print("=" * 150)
    print(f"{'variant':<8}  "
          f"{'c95+_EV':>10} {'c90+_EV':>10} {'c85+_EV':>10} {'c80+_EV':>10} {'c75+_EV':>10} {'c70+_EV':>10}"
          f"  {'p<5_EV':>10} {'p<15_EV':>10} {'p<25_EV':>10}")
    for vname, _ in ALPHAS:
        def _ev(k, is_put=False):
            w, t = wr.get((vname, '5y', k), [0, 0])
            if t == 0: return f"{'--':>10}"
            p = w / t
            tp = 0.29
            sl = 0.223 if is_put else 0.373
            ev = p * tp - (1 - p) * sl
            total_ev = ev * t
            return f"{total_ev:+7.0f} ({ev*100:+.1f}%)"
        def _evc(k): return _ev(k, False)
        def _evp(k): return _ev(k, True)
        print(f"{vname:<8}  "
              f"{_evc('95+'):>14} {_evc('90+'):>14} {_evc('85+'):>14} {_evc('80+'):>14} "
              f"{_evc('75+'):>14} {_evc('70+'):>14}  "
              f"{_evp('<5'):>14} {_evp('<15'):>14} {_evp('<25'):>14}")


if __name__ == '__main__':
    main()
