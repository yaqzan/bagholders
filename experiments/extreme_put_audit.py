"""
Forensic audit: pull actual extreme-put cases (overall <= 9) with full
component context + forward outcome. Goal: see whether the scores match the
indicator data, and whether winners vs losers have a clean indicator-level
separator the current scoring logic is missing.

Output format per case:
  SYM YYYY-MM-DD  score=X  [T BB R M S TA]  ext=%   bbp=   mh=   rsi_raw=
  ret5= ret20=  vol_sig=  wk_comp=  regime=  ->  win30=1/0/None  min30=-X.X%

Organized into:
  (1) 0-4 winners  (continuation-down cases — "real capitulation")
  (2) 0-4 losers   (bounce cases — "exhaustion, not continuation")
  (3) 5-9 winners
  (4) 5-9 losers

Then aggregate stats on each group showing where scores most disagree with data.
"""
from __future__ import annotations
import numpy as np
from datetime import date, timedelta
from collections import defaultdict, Counter
import json

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


def _put_barrier_win(entry, bars, sigma, W):
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

    print(f"Active version: v{av.id} ({av.git_commit[:8]})")

    q = f"""
        SELECT s.symbol, s.date, s.overall, s.trend, s.bb, s.rsi, s.macd,
               s.stoch, s.technical_alignment, s.price,
               s.volume_signal, s.volume_magnitude,
               s.regime_multiplier, s.weight_info,
               i.macd_hist, i.ema_50, i.ema_200,
               i.upper_band, i.lower_band, i.rsi AS rsi_raw,
               i.stoch AS stoch_raw
        FROM scores s
        LEFT JOIN indicators i USING (symbol, date)
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.overall <= 9
          AND s.price IS NOT NULL
          AND i.ema_50 IS NOT NULL
          AND i.upper_band IS NOT NULL
    """
    rows = DB.execute_sql(q).fetchall()
    print(f"Loaded {len(rows)} extreme-low candidates (<=9)")

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)

    records = []
    for sym, sym_rows in by_sym.items():
        min_d = min(r[1] for r in sym_rows)
        max_d = max(r[1] for r in sym_rows)
        ph_start = (min_d - timedelta(days=90)).isoformat()
        ph_end = (max_d + timedelta(days=50)).isoformat()
        bars = DB.execute_sql(f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date BETWEEN '{ph_start}' AND '{ph_end}'
            ORDER BY date
        """).fetchall()
        if len(bars) < 30:
            continue
        d_idx = {b[0]: i for i, b in enumerate(bars)}
        closes = [float(b[1]) for b in bars]
        highs = [float(b[2]) for b in bars]
        lows = [float(b[3]) for b in bars]
        dates = [b[0] for b in bars]

        for r in sym_rows:
            (sym_, d, overall, trend, bb, rsi, macd, stoch, ta,
             price, vol_sig, vol_mag, regime_mult, weight_info,
             macdh, ema50, ema200, upper, lower, rsi_raw, stoch_raw) = r
            if d not in d_idx:
                continue
            try:
                p = float(price); e50 = float(ema50)
                up = float(upper); lo_bb = float(lower)
            except Exception:
                continue
            if e50 <= 0:
                continue
            ext = (p / e50 - 1.0) * 100.0
            if abs(ext) >= 50:
                continue
            bbp = (p - lo_bb) / (up - lo_bb) if up > lo_bb else None
            if bbp is not None and not (-0.5 <= bbp <= 1.5):
                continue
            i = d_idx[d]
            if i < 30:
                continue
            sigma = _realized_vol(closes[:i + 1])
            if sigma is None:
                continue
            entry = closes[i]
            fwd = []
            for j in range(i + 1, len(bars)):
                off = (dates[j] - d).days
                fwd.append((off, highs[j], lows[j]))
                if off > 40:
                    break
            win15 = _put_barrier_win(entry, fwd, sigma, 15)
            win30 = _put_barrier_win(entry, fwd, sigma, 30)
            min30 = 0.0
            for (off, hi, lo) in fwd:
                if off > 30:
                    break
                dp = (lo - entry) / entry * 100.0
                if dp < min30:
                    min30 = dp
            max30 = 0.0  # max BOUNCE magnitude (positive = rally against put)
            for (off, hi, lo) in fwd:
                if off > 30:
                    break
                up_pct = (hi - entry) / entry * 100.0
                if up_pct > max30:
                    max30 = up_pct

            ret5 = (closes[i] / closes[i-5] - 1.0) * 100.0 if i >= 5 else None
            ret20 = (closes[i] / closes[i-20] - 1.0) * 100.0 if i >= 20 else None
            ret60 = (closes[i] / closes[i-60] - 1.0) * 100.0 if i >= 60 else None
            pct_e200 = (entry / float(ema200) - 1.0) * 100.0 if ema200 and float(ema200) > 0 else None

            # weekly composite from weight_info
            wcomp = wbias = wmom = wadj = None
            if weight_info:
                try:
                    wi = json.loads(weight_info) if isinstance(weight_info, str) else weight_info
                    wcomp = wi.get('w_comp'); wbias = wi.get('w_bias')
                    wmom = wi.get('w_mom'); wadj = wi.get('w_adj')
                except Exception:
                    pass

            records.append({
                'symbol': sym, 'date': d, 'overall': overall,
                'trend': trend, 'bb': bb, 'rsi': rsi, 'macd': macd,
                'stoch': stoch, 'ta': ta,
                'rsi_raw': float(rsi_raw) if rsi_raw is not None else None,
                'stoch_raw': float(stoch_raw) if stoch_raw is not None else None,
                'ext': ext, 'bbp': bbp, 'macdh': float(macdh) if macdh is not None else None,
                'ret5': ret5, 'ret20': ret20, 'ret60': ret60,
                'pct_e200': pct_e200, 'sigma': sigma,
                'vol_sig': vol_sig, 'vol_mag': float(vol_mag) if vol_mag else 0,
                'regime_mult': float(regime_mult) if regime_mult else 1.0,
                'wcomp': wcomp, 'wbias': wbias, 'wmom': wmom, 'wadj': wadj,
                'win15': win15, 'win30': win30,
                'min30': min30, 'max30': max30,
            })

    print(f"Records: {len(records)}")

    # Split into buckets
    rs04 = [r for r in records if r['overall'] < 5 and r['win30'] is not None]
    rs59 = [r for r in records if 5 <= r['overall'] < 10 and r['win30'] is not None]
    w04 = [r for r in rs04 if r['win30'] == 1]
    l04 = [r for r in rs04 if r['win30'] == 0]
    w59 = [r for r in rs59 if r['win30'] == 1]
    l59 = [r for r in rs59 if r['win30'] == 0]

    def pp(r):
        def f(v, fmt):
            return format(v, fmt) if v is not None else 'N/A'
        return (f"{r['symbol']:<6} {r['date']} s={r['overall']:<2} "
                f"[T{r['trend']:>3} B{r['bb']:>3} R{r['rsi']:>3} "
                f"M{r['macd']:>3} St{r['stoch']:>3} TA{r['ta']:>3}] "
                f"ext={f(r['ext'],'+6.1f')}% bbp={f(r['bbp'],'+4.2f')} "
                f"mh={f(r['macdh'],'+.3f')} rsi_raw={f(r['rsi_raw'],'.0f')} "
                f"ret5={f(r['ret5'] or 0,'+5.1f')}% ret20={f(r['ret20'] or 0,'+6.1f')}% "
                f"vs={(r['vol_sig'] or 'NONE'):<10} wcomp={r['wcomp']} reg={f(r['regime_mult'],'.2f')} "
                f"-> w30={r['win30']} min30={f(r['min30'],'+.1f')}% max30={f(r['max30'],'+.1f')}%")

    # Sample N from each group
    SAMPLE = 20
    print(f"\n{'='*120}\n0-4 WINNERS (real capitulation -> kept dropping)  N_total={len(w04)}, showing {min(SAMPLE, len(w04))}\n{'='*120}")
    for r in sorted(w04, key=lambda x: x['min30'])[:SAMPLE]:
        print(pp(r))
    print(f"\n{'='*120}\n0-4 LOSERS (hit stop = bounced)  N_total={len(l04)}, showing {min(SAMPLE, len(l04))}\n{'='*120}")
    for r in sorted(l04, key=lambda x: -x['max30'])[:SAMPLE]:
        print(pp(r))
    print(f"\n{'='*120}\n5-9 WINNERS  N_total={len(w59)}, showing {min(SAMPLE, len(w59))}\n{'='*120}")
    for r in sorted(w59, key=lambda x: x['min30'])[:SAMPLE]:
        print(pp(r))
    print(f"\n{'='*120}\n5-9 LOSERS  N_total={len(l59)}, showing {min(SAMPLE, len(l59))}\n{'='*120}")
    for r in sorted(l59, key=lambda x: -x['max30'])[:SAMPLE]:
        print(pp(r))

    # Feature separation
    def stat(rs, k):
        v = [r[k] for r in rs if r[k] is not None]
        return (np.mean(v), np.median(v), len(v)) if v else (None, None, 0)

    print(f"\n{'='*100}\nFEATURE MEANS  (W=winners, L=losers)\n{'='*100}")
    print(f"{'feature':<12} {'0-4 W':>10} {'0-4 L':>10} {'diff':>8} | {'5-9 W':>10} {'5-9 L':>10} {'diff':>8}")
    for k in ['trend','bb','rsi','macd','stoch','ta','rsi_raw','stoch_raw',
             'ext','bbp','macdh','ret5','ret20','ret60','pct_e200',
             'sigma','vol_mag','regime_mult','wcomp','wbias','wmom','wadj']:
        w04m, _, _ = stat(w04, k); l04m, _, _ = stat(l04, k)
        w59m, _, _ = stat(w59, k); l59m, _, _ = stat(l59, k)
        if any(x is None for x in [w04m, l04m, w59m, l59m]):
            continue
        print(f"{k:<12} {w04m:>10.2f} {l04m:>10.2f} {w04m-l04m:>+8.2f} | "
              f"{w59m:>10.2f} {l59m:>10.2f} {w59m-l59m:>+8.2f}")

    # CRITICAL CHECK: at 0-4, do LOSERS look like "already broken down, bouncing"
    # and WINNERS look like "just starting to break"?
    print(f"\n{'='*100}\n0-4 BREAKDOWN BY RET20 (20d prior return)\n{'='*100}")
    print(f"{'ret20_range':<14} {'N_win':>6} {'N_lose':>7} {'WR30':>7}")
    for lo, hi in [(-100, -20), (-20, -10), (-10, -5), (-5, 0), (0, 100)]:
        sub = [r for r in rs04 if r['ret20'] is not None and lo <= r['ret20'] < hi]
        if not sub: continue
        wins = sum(r['win30'] for r in sub)
        print(f"{f'[{lo:+.0f},{hi:+.0f})':<14} {wins:>6} {len(sub)-wins:>7} {wins/len(sub)*100:>6.1f}%")

    print(f"\n{'='*100}\n0-4 BREAKDOWN BY EXT (pct from EMA50)\n{'='*100}")
    print(f"{'ext_range':<14} {'N_win':>6} {'N_lose':>7} {'WR30':>7}")
    for lo, hi in [(-100, -25), (-25, -15), (-15, -10), (-10, -5), (-5, 0), (0, 100)]:
        sub = [r for r in rs04 if r['ext'] is not None and lo <= r['ext'] < hi]
        if not sub: continue
        wins = sum(r['win30'] for r in sub)
        print(f"{f'[{lo:+.0f},{hi:+.0f})':<14} {wins:>6} {len(sub)-wins:>7} {wins/len(sub)*100:>6.1f}%")

    # 2-feature sweep: ret20 x macdh
    print(f"\n{'='*100}\n0-4 CROSS: ret20 × macdh\n{'='*100}")
    print(f"{'ret20':<12} {'mh<0':>14} {'mh>=0':>14}")
    for lo, hi, label in [(-100, -10, 'ret20<-10'), (-10, 0, 'ret20 [-10,0)'), (0, 100, 'ret20>=0')]:
        sub = [r for r in rs04 if r['ret20'] is not None and r['macdh'] is not None and lo <= r['ret20'] < hi]
        if not sub: continue
        mhneg = [r for r in sub if r['macdh'] < 0]
        mhpos = [r for r in sub if r['macdh'] >= 0]
        wn = sum(r['win30'] for r in mhneg); wp = sum(r['win30'] for r in mhpos)
        sn = f"{wn}/{len(mhneg)} {wn/len(mhneg)*100:.1f}%" if mhneg else "—"
        sp = f"{wp}/{len(mhpos)} {wp/len(mhpos)*100:.1f}%" if mhpos else "—"
        print(f"{label:<12} {sn:>14} {sp:>14}")


if __name__ == '__main__':
    main()
