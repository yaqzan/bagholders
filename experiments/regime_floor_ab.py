"""
A/B test for regime-floor overrides targeting the 2023 narrow-bull misclassification.

Hypothesis: When SPY is objectively bullish (above EMA200) AND VIX is calm,
the regime composite often labels the day STRESS because breadth is narrow.
Flooring the regime_multiplier at 1.0 on these days should:
  - PREVENT call-side dampening (more calls qualify at 70+)
  - PREVENT put-side amplification (fewer bogus puts at <=25)

Variants tested (all reuse v21 stored pre_regime; only variant multiplier differs):

  BASE       - stored production multiplier (control)
  FIX_STRICT - floor mult at 1.0 if SPY>EMA200 AND VIX<18 AND SPY_10d>0
  FIX_MED    - floor mult at 1.0 if SPY>EMA200 AND VIX<20
  FIX_LOOSE  - floor mult at 1.0 if SPY>EMA200
  FIX_SYM    - FIX_MED plus cap mult at 1.0 when SPY<EMA200 AND VIX>=25
  NO_REGIME  - fixed mult=1.0 always (reference: what if regime entirely bypassed?)

For each variant, reports:
  - Signal counts per bucket per year (calls 70+, 75+, 80+, puts <=25, <=15)
  - Barrier-touch WR15/WR30 per bucket (5y aggregate + 2022/2023/2024/2025)
  - Delta vs BASE

Uses stored pre_regime directly from weight_info (no re-scoring needed).

Usage:
    python experiments/regime_floor_ab.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.trader_database import DB
from database.models.core import AlgorithmVersion


# ---- barrier-touch helpers ----

def _realized_vol(closes):
    if len(closes) < 30:
        return None
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    if len(rets) < 20:
        return None
    return float(np.std(rets[-60:]) * 100.0)


def _barrier_walk(side, entry, bars, sigma, W):
    """side: 'low' (call) or 'high' (put). Returns win int or None."""
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    if side == 'low':   # call: win = price rises
        tgt = entry * (1.0 + 2.0 * sigma * scale / 100.0)
        stp = entry * (1.0 - 5.0 * sigma * scale / 100.0)
    else:               # put: win = price falls
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


# ---- Variant multiplier functions ----
# Each takes (stored_mult, spy_close, spy_ema200, vix_close, spy_10d_ret) and returns float

def v_base(sm, spy, ema200, vix, spy10):
    return sm


def v_fix_strict(sm, spy, ema200, vix, spy10):
    if spy is None or ema200 is None or vix is None or spy10 is None:
        return sm
    if spy > ema200 and vix < 18 and spy10 > 0:
        return max(sm, 1.0)
    return sm


def v_fix_med(sm, spy, ema200, vix, spy10):
    if spy is None or ema200 is None or vix is None:
        return sm
    if spy > ema200 and vix < 20:
        return max(sm, 1.0)
    return sm


def v_fix_loose(sm, spy, ema200, vix, spy10):
    if spy is None or ema200 is None:
        return sm
    if spy > ema200:
        return max(sm, 1.0)
    return sm


def v_fix_sym(sm, spy, ema200, vix, spy10):
    # FIX_MED + cap on objectively-bear days
    if spy is not None and ema200 is not None and vix is not None:
        if spy > ema200 and vix < 20:
            return max(sm, 1.0)
        if spy < ema200 and vix >= 25:
            return min(sm, 1.0)
    return sm


def v_no_regime(sm, spy, ema200, vix, spy10):
    return 1.0


VARIANTS = [
    ('BASE', v_base),
    ('FIX_STRICT', v_fix_strict),
    ('FIX_MED', v_fix_med),
    ('FIX_LOOSE', v_fix_loose),
    ('FIX_SYM', v_fix_sym),
    ('NO_REGIME', v_no_regime),
]


# ---- Bucket classification ----
def _bucket(score):
    if score >= 95: return '95+'
    if score >= 90: return '90+'
    if score >= 85: return '85+'
    if score >= 80: return '80+'
    if score >= 75: return '75+'
    if score >= 70: return '70+'
    if score <= 5: return '<5'
    if score <= 10: return '<10'
    if score <= 15: return '<15'
    if score <= 20: return '<20'
    if score <= 25: return '<25'
    return None


CALL_BUCKETS = ['70+', '75+', '80+', '85+', '90+', '95+']
PUT_BUCKETS  = ['<25', '<20', '<15', '<10', '<5']
ALL_BUCKETS  = CALL_BUCKETS + PUT_BUCKETS


def _cumulative_bucket_count(score_bucket_counts, bucket_key):
    """Return cumulative count for a bucket (e.g. '70+' = all >=70)."""
    if bucket_key == '70+':
        return (score_bucket_counts.get('70+', 0) + score_bucket_counts.get('75+', 0) +
                score_bucket_counts.get('80+', 0) + score_bucket_counts.get('85+', 0) +
                score_bucket_counts.get('90+', 0) + score_bucket_counts.get('95+', 0))
    if bucket_key == '75+':
        return (score_bucket_counts.get('75+', 0) + score_bucket_counts.get('80+', 0) +
                score_bucket_counts.get('85+', 0) + score_bucket_counts.get('90+', 0) +
                score_bucket_counts.get('95+', 0))
    if bucket_key == '80+':
        return (score_bucket_counts.get('80+', 0) + score_bucket_counts.get('85+', 0) +
                score_bucket_counts.get('90+', 0) + score_bucket_counts.get('95+', 0))
    if bucket_key == '85+':
        return (score_bucket_counts.get('85+', 0) + score_bucket_counts.get('90+', 0) +
                score_bucket_counts.get('95+', 0))
    if bucket_key == '90+':
        return (score_bucket_counts.get('90+', 0) + score_bucket_counts.get('95+', 0))
    if bucket_key == '95+':
        return score_bucket_counts.get('95+', 0)
    if bucket_key == '<25':
        return (score_bucket_counts.get('<25', 0) + score_bucket_counts.get('<20', 0) +
                score_bucket_counts.get('<15', 0) + score_bucket_counts.get('<10', 0) +
                score_bucket_counts.get('<5', 0))
    if bucket_key == '<20':
        return (score_bucket_counts.get('<20', 0) + score_bucket_counts.get('<15', 0) +
                score_bucket_counts.get('<10', 0) + score_bucket_counts.get('<5', 0))
    if bucket_key == '<15':
        return (score_bucket_counts.get('<15', 0) + score_bucket_counts.get('<10', 0) +
                score_bucket_counts.get('<5', 0))
    if bucket_key == '<10':
        return score_bucket_counts.get('<10', 0) + score_bucket_counts.get('<5', 0)
    if bucket_key == '<5':
        return score_bucket_counts.get('<5', 0)
    return 0


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}\n")

    # ---- Load regime/VIX/SPY data per date ----
    print("Loading regime + SPY data...")
    regime_rows = DB.execute_sql(f"""
        SELECT date, vix_close, spy_close, spy_ema50, spy_ema200, regime_multiplier
        FROM market_regime
        WHERE date >= '{cutoff}'
        ORDER BY date
    """).fetchall()

    # Build spy_10d_ret per date
    spy_closes = [(r[0], float(r[2])) for r in regime_rows if r[2] is not None]
    spy_map = {d: c for d, c in spy_closes}
    spy_dates_sorted = [d for d, _ in spy_closes]
    spy_10d_ret_map = {}
    for i, (d, c) in enumerate(spy_closes):
        if i < 10:
            continue
        prev = spy_closes[i - 10][1]
        if prev > 0:
            spy_10d_ret_map[d] = (c / prev - 1.0) * 100

    regime_map = {}
    for r in regime_rows:
        d, vix, spy, ema50, ema200, mult = r
        regime_map[d] = {
            'vix': float(vix) if vix is not None else None,
            'spy': float(spy) if spy is not None else None,
            'ema200': float(ema200) if ema200 is not None else None,
            'mult_stored': float(mult) if mult is not None else 1.0,
            'spy_10d': spy_10d_ret_map.get(d),
        }
    print(f"  Loaded {len(regime_map)} regime rows")

    # ---- Load pre_regime + symbols + dates for qualifying candidates ----
    # To cover all variants, load ANY score where |pre_regime-50| >= 18 (enough to qualify under any floor/cap)
    print("Loading peaks (pre_regime extremes)...")
    q = f"""
        SELECT symbol, date, overall, regime_multiplier, weight_info
        FROM scores
        WHERE version_id = {vid}
          AND date >= '{cutoff}'
          AND weight_info IS NOT NULL
        ORDER BY symbol, date
    """
    rows = DB.execute_sql(q).fetchall()

    # Extract pre_regime per row
    peaks = []
    for sym, d, overall, sm, wi in rows:
        try:
            if isinstance(wi, str):
                wi_d = json.loads(wi)
            else:
                wi_d = wi
            pr = wi_d.get('pre_regime')
            if pr is None:
                continue
            pr = int(pr)
        except Exception:
            continue
        # Pre-filter: only keep rows that could qualify under ANY variant (pre_regime >=60 or <=40)
        # Conservative margin since a floor-at-1.0 shift can push a 68 to 68 and <=25 can shift further
        if not (pr >= 60 or pr <= 40):
            continue
        peaks.append({
            'sym': sym, 'date': d, 'pre_regime': pr,
            'stored_mult': float(sm) if sm is not None else 1.0,
            'overall_stored': int(overall),
        })
    print(f"  Loaded {len(peaks):,} candidate peaks (pre_regime extreme)")

    # ---- For each variant, compute variant_overall per peak ----
    print("\nApplying variants...")
    for pk in peaks:
        reg = regime_map.get(pk['date'], {})
        vc = reg.get('vix')
        spy = reg.get('spy')
        ema200 = reg.get('ema200')
        spy10 = reg.get('spy_10d')
        sm = reg.get('mult_stored', pk['stored_mult'])
        pk['variants'] = {}
        for vname, vfn in VARIANTS:
            m = vfn(sm, spy, ema200, vc, spy10)
            overall = _apply_mult(pk['pre_regime'], m)
            pk['variants'][vname] = {'overall': overall, 'mult': m}

    # ---- Tally signal counts per variant per year per bucket ----
    print("\nTallying signal counts per variant per year...")
    counts = defaultdict(lambda: defaultdict(int))  # (variant, year, bucket_letter) -> count
    for pk in peaks:
        yr = pk['date'].year
        for vname in [v[0] for v in VARIANTS]:
            ov = pk['variants'][vname]['overall']
            b = _bucket(ov)
            if b is None:
                continue
            counts[(vname, yr)][b] += 1
            counts[(vname, '5y')][b] += 1

    print("\n" + "=" * 130)
    print("SIGNAL COUNTS BY VARIANT AND YEAR (cumulative buckets)")
    print("=" * 130)
    years_all = sorted({pk['date'].year for pk in peaks}) + ['5y']
    for yr in years_all:
        print(f"\n--- {yr} ---")
        header = f"{'bucket':<8} | " + " ".join(f"{v[:10]:>10}" for v, _ in VARIANTS)
        print(header)
        print("-" * len(header))
        # Calls
        for b in CALL_BUCKETS:
            cells = []
            for vname, _ in VARIANTS:
                n = _cumulative_bucket_count(counts.get((vname, yr), {}), b)
                cells.append(f"{n:>10}")
            print(f"{b:<8} | " + " ".join(cells))
        print()
        # Puts
        for b in PUT_BUCKETS:
            cells = []
            for vname, _ in VARIANTS:
                n = _cumulative_bucket_count(counts.get((vname, yr), {}), b)
                cells.append(f"{n:>10}")
            print(f"{b:<8} | " + " ".join(cells))

    # ---- Barrier-touch WR per variant per bucket (5y and per-year) ----
    print("\n" + "=" * 130)
    print("BARRIER-TOUCH WR COMPUTATION — loading price data...")
    print("=" * 130)

    # Collect unique (symbol, date) pairs for any variant that qualified the peak
    qual_set = set()  # set of (sym, date) that qualifies under at least one variant
    for pk in peaks:
        for vname, _ in VARIANTS:
            ov = pk['variants'][vname]['overall']
            if ov >= 70 or ov <= 25:
                qual_set.add((pk['sym'], pk['date']))
                break
    print(f"  {len(qual_set):,} (sym, date) pairs qualify under at least one variant")

    symbols_needed = set(sym for sym, _ in qual_set)
    print(f"  {len(symbols_needed):,} unique symbols need price history")

    # Per-symbol price cache for barrier walks
    price_cache = {}
    for i, sym in enumerate(sorted(symbols_needed)):
        if i % 50 == 0:
            print(f"    ...loading prices {i}/{len(symbols_needed)}", flush=True)
        q = f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date >= '{cutoff}'
            ORDER BY date
        """
        rows_p = DB.execute_sql(q).fetchall()
        price_cache[sym] = rows_p
    print(f"  Price cache ready ({len(price_cache)} symbols)")

    # Compute barrier outcome per qualifying peak per variant
    # Outcome per (sym, date, W) is variant-agnostic — compute once per (sym, date)
    print(f"\nComputing barrier outcomes for {len(qual_set):,} pairs...")
    outcomes = {}  # (sym, date) -> {'w15_call': 0/1/None, 'w30_call': ..., 'w15_put': ..., 'w30_put': ...}
    for i, (sym, d) in enumerate(qual_set):
        if i % 2000 == 0:
            print(f"    ...walk {i}/{len(qual_set)}", flush=True)
        bars_list = price_cache.get(sym, [])
        d_idx = {b[0]: idx for idx, b in enumerate(bars_list)}
        idx = d_idx.get(d)
        if idx is None or idx < 30:
            outcomes[(sym, d)] = None
            continue
        closes = [float(b[1]) for b in bars_list]
        highs = [float(b[2]) for b in bars_list]
        lows = [float(b[3]) for b in bars_list]
        sigma = _realized_vol(closes[:idx + 1])
        if sigma is None:
            outcomes[(sym, d)] = None
            continue
        entry = closes[idx]
        fwd = []
        for j in range(idx + 1, len(bars_list)):
            off = (bars_list[j][0] - d).days
            if off > 40:
                break
            fwd.append((off, highs[j], lows[j]))
        w15c = _barrier_walk('low', entry, fwd, sigma, 15)
        w30c = _barrier_walk('low', entry, fwd, sigma, 30)
        w15p = _barrier_walk('high', entry, fwd, sigma, 15)
        w30p = _barrier_walk('high', entry, fwd, sigma, 30)
        outcomes[(sym, d)] = {'w15c': w15c, 'w30c': w30c, 'w15p': w15p, 'w30p': w30p}

    # Aggregate WR per variant per bucket per window
    print("\nAggregating WR...")
    # key: (variant, window, bucket_code, side) -> [wins, total]
    wr = defaultdict(lambda: [0, 0])
    # window: '5y', 2021, 2022, 2023, 2024, 2025
    for pk in peaks:
        sym, d = pk['sym'], pk['date']
        out = outcomes.get((sym, d))
        if out is None:
            continue
        yr = d.year
        for vname, _ in VARIANTS:
            ov = pk['variants'][vname]['overall']
            b = _bucket(ov)
            if b is None:
                continue
            # call-side bucket
            if b in CALL_BUCKETS:
                for window in ('5y', yr):
                    if out['w15c'] is not None:
                        for bk in CALL_BUCKETS:
                            # cumulative - if overall qualifies, add to all weaker cumulative buckets
                            if ov >= int(bk.rstrip('+')):
                                wr[(vname, window, bk, 'w15c')][1] += 1
                                wr[(vname, window, bk, 'w15c')][0] += out['w15c']
                    if out['w30c'] is not None:
                        for bk in CALL_BUCKETS:
                            if ov >= int(bk.rstrip('+')):
                                wr[(vname, window, bk, 'w30c')][1] += 1
                                wr[(vname, window, bk, 'w30c')][0] += out['w30c']
            elif b in PUT_BUCKETS:
                for window in ('5y', yr):
                    if out['w15p'] is not None:
                        for bk in PUT_BUCKETS:
                            # cumulative - if overall qualifies, add to all weaker cumulative buckets
                            if ov <= int(bk.lstrip('<')):
                                wr[(vname, window, bk, 'w15p')][1] += 1
                                wr[(vname, window, bk, 'w15p')][0] += out['w15p']
                    if out['w30p'] is not None:
                        for bk in PUT_BUCKETS:
                            if ov <= int(bk.lstrip('<')):
                                wr[(vname, window, bk, 'w30p')][1] += 1
                                wr[(vname, window, bk, 'w30p')][0] += out['w30p']

    # ---- Print WR tables ----
    def _fmt_wr(w, t, base_w, base_t, dcol_w=10):
        if t == 0:
            return f"{'--':>{dcol_w}}"
        pct = w / t * 100
        cell = f"{pct:.1f}%/{t}"
        # delta vs base
        if base_t == 0 or base_t is None:
            return f"{cell:>{dcol_w}}"
        base_pct = base_w / base_t * 100
        delta = pct - base_pct
        sign = '+' if delta >= 0 else ''
        return f"{cell:>{dcol_w}}"

    def _print_wr_table(window, side_code, side_name, buckets):
        print(f"\n--- {window} {side_name} WR15 ---")
        header = f"{'bucket':<8} | " + " ".join(f"{v[:12]:>12}" for v, _ in VARIANTS)
        print(header)
        print("-" * len(header))
        for b in buckets:
            cells = []
            base_w, base_t = wr.get(('BASE', window, b, side_code), [0, 0])
            for vname, _ in VARIANTS:
                w, t = wr.get((vname, window, b, side_code), [0, 0])
                cells.append(f"{_fmt_wr(w, t, base_w, base_t, 12):>12}")
            print(f"{b:<8} | " + " ".join(cells))

    print("\n" + "=" * 130)
    print("BARRIER-TOUCH WR15 PER VARIANT")
    print("=" * 130)
    for window in ['5y', 2022, 2023, 2024, 2025]:
        _print_wr_table(window, 'w15c', 'CALL', CALL_BUCKETS)
        _print_wr_table(window, 'w15p', 'PUT', PUT_BUCKETS)

    # ---- Summary: single-metric "score" per variant (higher is better) ----
    print("\n" + "=" * 130)
    print("HEADLINE 5y METRICS PER VARIANT")
    print("=" * 130)
    print(f"{'variant':<12} {'calls_70+_N':>12} {'calls_70+_WR15':>15} "
          f"{'calls_80+_N':>12} {'calls_80+_WR15':>15} "
          f"{'puts_<=25_N':>12} {'puts_<=25_WR15':>15} "
          f"{'puts_<=15_N':>12} {'puts_<=15_WR15':>15}")
    for vname, _ in VARIANTS:
        c70 = wr.get((vname, '5y', '70+', 'w15c'), [0, 0])
        c80 = wr.get((vname, '5y', '80+', 'w15c'), [0, 0])
        p25 = wr.get((vname, '5y', '<25', 'w15p'), [0, 0])
        p15 = wr.get((vname, '5y', '<15', 'w15p'), [0, 0])
        def _pct(w, t):
            return f"{w/t*100:.1f}%" if t else '--'
        print(f"{vname:<12} {c70[1]:>12} {_pct(c70[0], c70[1]):>15} "
              f"{c80[1]:>12} {_pct(c80[0], c80[1]):>15} "
              f"{p25[1]:>12} {_pct(p25[0], p25[1]):>15} "
              f"{p15[1]:>12} {_pct(p15[0], p15[1]):>15}")


if __name__ == '__main__':
    main()
