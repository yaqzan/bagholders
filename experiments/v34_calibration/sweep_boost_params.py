"""v34 EARN_BOOST calibration sweep.

For each candidate (lift_table_path, MAX, NORM_CALL, NORM_PUT, WINDOW, MIN_N,
PUT_ADMIT), simulates the full boost transform on `pre_boost` scores and
measures per-bucket WR15/N/lift. Compares against baseline (PRODUCTION =
v27 table + current production params).

Inputs (cached in .cache/v34_cal/):
  - rows.parquet: (symbol, date, pre_boost, days_to_ern) — built once
  - barriers_w15.parquet: from .cache/runner/barriers_1825.parquet, w_days=15

Output:
  - results.csv with per-(variant × bucket) WR15, N, vs baseline delta
  - winner.json with the best calibration

Per H1-H5 gate: candidate ships if
  H1: TP%/WR15 +0.5pp on ≥3 of 5 call tiers (95+, 90+, 85+, 80+, 75+) at 5y;
      no tier regresses >-1.0pp
  H3: N stability ±15% per bucket at 5y; no primary tier <50 peaks
  H4: <25 / <15 puts WR15 unchanged or better
  H5: TP% sign consistent across 1y, 3y, 5y
"""
from __future__ import annotations
import io, sys, os, json, math, time, copy, itertools
from dataclasses import dataclass, asdict, field
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
from database.trader_database import DB
from database.models.core import (
    AlgorithmVersion,
    _load_effective_earnings_dates,
)

CACHE_DIR = ROOT / '.cache' / 'v34_cal'
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Boost transform (mirror of database/utils/scoring.py) ───────────────────

def _ern_cohort(d_to_ern, window):
    if d_to_ern is None or d_to_ern < 0 or d_to_ern > window:
        return None
    if d_to_ern <= 1: return 'pre1'
    if d_to_ern <= 3: return 'pre3'
    if d_to_ern <= 7: return 'pre7'
    return None


def _ern_bucket(overall):
    if overall >= 95: return '95+'
    if overall >= 90: return '90-94'
    if overall >= 85: return '85-89'
    if overall >= 80: return '80-84'
    if overall >= 75: return '75-79'
    if overall >= 70: return '70-74'
    if overall <= 5:  return '0-5'
    if overall <= 10: return '6-10'
    if overall <= 15: return '11-15'
    if overall <= 20: return '16-20'
    if overall <= 25: return '21-25'
    return None


def _is_call_qual(overall): return overall >= 70
def _is_put_qual(overall):  return overall <= 25


def load_strength_map(lift_table_path, norm_call, norm_put, min_n):
    with open(lift_table_path) as f:
        raw = json.load(f)
    log_norm_call = math.log(1 + norm_call)
    log_norm_put = math.log(1 + norm_put)
    smap = {}
    for k, v in raw.items():
        side, cohort, bucket = k.split('|')
        lift, n = v
        if n < min_n or lift <= 0:
            continue
        log_norm = log_norm_call if side == 'low' else log_norm_put
        strength = min(1.0, math.log(1 + lift) / log_norm)
        smap[(side, cohort, bucket)] = strength
    return smap


def boost(pre_b, d_to_ern, smap, window, max_boost, put_admit):
    """Return post-boost overall (int) given pre_boost score and earnings proximity."""
    if d_to_ern is None:
        return pre_b
    cohort = _ern_cohort(d_to_ern, window)
    if cohort is None:
        return pre_b
    bucket = _ern_bucket(pre_b)
    if bucket is None:
        # Boundary case for puts/calls outside qual range
        if put_admit and 25 < pre_b <= 30:
            bucket = '21-25'
        elif put_admit and 65 <= pre_b < 70:
            bucket = '70-74'
        else:
            return pre_b
    side = 'low' if pre_b >= 50 else 'high'
    # Production rule: calls always boost when ≥70; puts only boost when ≤25
    # (admission gated by put_admit)
    if side == 'low' and pre_b < 70:
        return pre_b
    if side == 'high' and pre_b > 25 and not put_admit:
        return pre_b
    strength = smap.get((side, cohort, bucket), 0.0)
    if strength <= 0:
        return pre_b
    log_w = math.log(window + 1)
    proximity = math.log(window + 1 - d_to_ern) / log_w
    boost_strength = max_boost * proximity * strength
    boosted = 50 + (pre_b - 50) * (1.0 + boost_strength)
    return int(round(max(0, min(100, boosted))))


# ── Data loading ────────────────────────────────────────────────────────────

def build_data_cache(lookback_days=1825, force=False):
    cache_pq = CACHE_DIR / f'rows_{lookback_days}.parquet'
    if cache_pq.exists() and not force:
        return pl.read_parquet(cache_pq)
    av = AlgorithmVersion.get_active_scores_version()
    print(f"[active] id={av.id} commit={av.git_commit[:8]}", flush=True)
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    print(f"[query] pre_boost since {cutoff}", flush=True)
    sql = """
        SELECT symbol, date, overall, weight_info
        FROM scores
        WHERE version_id = %s AND date >= %s AND weight_info IS NOT NULL
    """
    cur = DB.execute_sql(sql, [av.id, cutoff])
    rows = []
    n = 0
    for sym, d, ovr, wi_raw in cur.fetchall():
        n += 1
        if n % 200000 == 0:
            print(f"  fetched {n:,}", flush=True)
        try:
            wi = json.loads(wi_raw)
        except (TypeError, json.JSONDecodeError):
            continue
        pre_b = wi.get('pre_boost')
        if pre_b is None:
            pre_b = ovr
        rows.append((sym, d.isoformat() if hasattr(d, 'isoformat') else d, int(pre_b)))
    print(f"[query] {len(rows):,} rows", flush=True)
    df = pl.DataFrame(rows, schema=['symbol', 'date', 'pre_boost'], orient='row')

    # Earnings days (window=10 to allow pre7 cohort comparisons + slack)
    syms = df['symbol'].to_list()
    dates = df['date'].to_list()
    eff_by_sym = {}
    days = []
    last_sym = None
    eff = []
    from bisect import bisect_right
    for sym, d_str in zip(syms, dates):
        if sym != last_sym:
            if sym not in eff_by_sym:
                eff_by_sym[sym] = _load_effective_earnings_dates(sym)
            eff = eff_by_sym[sym]
            last_sym = sym
        if not eff:
            days.append(None); continue
        di = date.fromisoformat(d_str)
        idx = bisect_right(eff, di)
        if idx >= len(eff):
            days.append(None); continue
        delta = (eff[idx] - di).days
        days.append(delta if 1 <= delta <= 10 else None)
    df = df.with_columns(pl.Series('days_to_ern', days))
    df.write_parquet(cache_pq)
    print(f"[cache] saved {cache_pq}", flush=True)
    return df


def load_barriers():
    barrier_pq = ROOT / '.cache' / 'runner' / 'barriers_1825.parquet'
    return pl.read_parquet(barrier_pq).filter(pl.col('w_days') == 15)


# ── Variant evaluation ──────────────────────────────────────────────────────

@dataclass
class Variant:
    name: str
    lift_table_path: str
    max_boost: float = 0.50
    norm_call: float = 22.3
    norm_put: float = 16.3
    window: int = 5
    min_n: int = 10
    put_admit: bool = False


CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']
PUT_BUCKETS = ['<5', '<10', '<15', '<20', '<25']


def cumulative_bucket_overall(overall, side):
    if side == 'low':
        for t in [95, 90, 85, 80, 75, 70]:
            if overall >= t:
                return f'{t}+'
        return None
    else:
        for t in [5, 10, 15, 20, 25]:
            if overall <= t:
                return f'<{t}'
        return None


def evaluate(variant: Variant, df, barriers, since=None):
    """Return per-bucket {bucket: (n, wr15, n_full, wr_full)} where:
      - n: count after boost (qualifying only)
      - wr15: WR15 % (with barrier resolution)
    """
    smap = load_strength_map(variant.lift_table_path, variant.norm_call, variant.norm_put, variant.min_n)

    # Apply boost row-by-row (vectorize later if too slow)
    pre_b = df['pre_boost'].to_list()
    d_e = df['days_to_ern'].to_list()
    new_o = []
    n_boosted = 0
    for p, d in zip(pre_b, d_e):
        nb = boost(p, d, smap, variant.window, variant.max_boost, variant.put_admit)
        new_o.append(nb)
        if nb != p:
            n_boosted += 1
    df2 = df.with_columns(pl.Series('new_overall', new_o))
    df2 = df2.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    if since:
        df2 = df2.filter(pl.col('date') >= since)
    df2 = df2.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )

    joined = df2.join(barriers, on=['symbol', 'date', 'side'], how='inner')

    results = {}
    # Cumulative call buckets
    for t in [95, 90, 85, 80, 75, 70]:
        sub = joined.filter(pl.col('new_overall') >= t)
        sub = sub.filter(pl.col('result').is_not_null())
        n = len(sub)
        wr = sub['result'].mean() * 100 if n else None
        results[f'{t}+'] = (n, wr, n_boosted)
    for t in [5, 10, 15, 20, 25]:
        sub = joined.filter(pl.col('new_overall') <= t)
        sub = sub.filter(pl.col('result').is_not_null())
        n = len(sub)
        wr = sub['result'].mean() * 100 if n else None
        results[f'<{t}'] = (n, wr, n_boosted)
    return results


def diff_against(baseline, candidate, label):
    print(f"\n=== {label} ===")
    print(f"{'Bucket':<8} | {'Base WR15':>10} {'Base N':>8} | {'Cand WR15':>10} {'Cand N':>8} | {'ΔWR':>7} {'ΔN%':>7}")
    print('-' * 80)
    for b in CALL_BUCKETS + PUT_BUCKETS:
        bn, bwr, _ = baseline.get(b, (0, None, 0))
        cn, cwr, _ = candidate.get(b, (0, None, 0))
        dwr = (cwr - bwr) if (bwr is not None and cwr is not None) else None
        dn_pct = (100*(cn - bn)/bn) if bn else None
        bwr_s = f'{bwr:5.2f}%' if bwr is not None else '   -  '
        cwr_s = f'{cwr:5.2f}%' if cwr is not None else '   -  '
        dwr_s = f'{dwr:+.2f}pp' if dwr is not None else '   -  '
        dn_s  = f'{dn_pct:+.1f}%' if dn_pct is not None else '   -  '
        print(f"{b:<8} | {bwr_s:>10} {bn:>8,} | {cwr_s:>10} {cn:>8,} | {dwr_s:>7} {dn_s:>7}")


def h1_h5_gate(baseline, candidate):
    """Check H1-H5 per-trade gate. Returns (pass, reason)."""
    fails = []
    # H1: TP%/WR15 +0.5pp on ≥3 of {95+, 90+, 85+, 80+, 75+}; no >-1.0pp regression
    call_tiers = ['95+', '90+', '85+', '80+', '75+']
    improvements = 0
    for t in call_tiers:
        bwr = baseline.get(t, (0, None))[1]
        cwr = candidate.get(t, (0, None))[1]
        if bwr is None or cwr is None: continue
        diff = cwr - bwr
        if diff >= 0.5: improvements += 1
        if diff < -1.0:
            fails.append(f'H1 regression: {t} {diff:+.2f}pp')
    if improvements < 3:
        fails.append(f'H1 only {improvements}/5 tiers improve')
    # H3: N stability ±15% per call bucket
    for t in call_tiers:
        bn = baseline.get(t, (0, None))[0]
        cn = candidate.get(t, (0, None))[0]
        if bn == 0: continue
        d = abs(cn - bn) / bn
        if d > 0.15:
            fails.append(f'H3 N drift {t}: {d*100:.1f}% (>15%)')
        if cn < 50:
            fails.append(f'H3 N too low: {t}={cn}')
    # H4: puts (<25, <15) unchanged or better
    for t in ['<25', '<15']:
        bwr = baseline.get(t, (0, None))[1]
        cwr = candidate.get(t, (0, None))[1]
        if bwr is None or cwr is None: continue
        if cwr < bwr - 0.5:
            fails.append(f'H4 put regression {t}: {cwr-bwr:+.2f}pp')
    return (len(fails) == 0, fails)


def main():
    parser_args = sys.argv[1:]
    t0 = time.time()
    df = build_data_cache(1825)
    print(f"[data] {len(df):,} rows in {time.time()-t0:.1f}s", flush=True)
    barriers = load_barriers()
    print(f"[barriers] {len(barriers):,} rows W=15", flush=True)

    v27_path = str(ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json')
    v34_path = str(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34_preboost.json')

    # Variants: focus gradient over saturation thresholds (NORM_CALL, NORM_PUT, MAX)
    # Also test PUT_ADMIT, WINDOW.
    variants = [
        Variant('PROD_v27',     v27_path, max_boost=0.50, norm_call=22.3, norm_put=16.3),  # baseline
        Variant('V34_BASIC',    v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3),
        # Gradient sweep on NORM_CALL (saturation threshold for call lifts)
        Variant('V34_NC15',     v34_path, max_boost=0.50, norm_call=15.0, norm_put=16.3),
        Variant('V34_NC20',     v34_path, max_boost=0.50, norm_call=20.0, norm_put=16.3),
        Variant('V34_NC30',     v34_path, max_boost=0.50, norm_call=30.0, norm_put=16.3),
        Variant('V34_NC40',     v34_path, max_boost=0.50, norm_call=40.0, norm_put=16.3),
        # Gradient sweep on NORM_PUT
        Variant('V34_NP10',     v34_path, max_boost=0.50, norm_call=22.3, norm_put=10.0),
        Variant('V34_NP25',     v34_path, max_boost=0.50, norm_call=22.3, norm_put=25.0),
        Variant('V34_NP35',     v34_path, max_boost=0.50, norm_call=22.3, norm_put=35.0),
        # MAX_BOOST sweep
        Variant('V34_MAX35',    v34_path, max_boost=0.35, norm_call=22.3, norm_put=16.3),
        Variant('V34_MAX65',    v34_path, max_boost=0.65, norm_call=22.3, norm_put=16.3),
        Variant('V34_MAX80',    v34_path, max_boost=0.80, norm_call=22.3, norm_put=16.3),
        # WINDOW sweep
        Variant('V34_W3',       v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=3),
        Variant('V34_W7',       v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=7),
        # PUT_ADMIT
        Variant('V34_PUT_ADMIT', v34_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, put_admit=True),
    ]

    # 5y window
    results_5y = {}
    for v in variants:
        t1 = time.time()
        r = evaluate(v, df, barriers)
        print(f"[5y] {v.name}: evaluated in {time.time()-t1:.1f}s, n_boosted={r['70+'][2]:,}", flush=True)
        results_5y[v.name] = r

    baseline = results_5y['PROD_v27']
    print("\n" + "="*120)
    print("5y RESULTS (vs PROD_v27 baseline)")
    print("="*120)
    for v in variants:
        if v.name == 'PROD_v27': continue
        diff_against(baseline, results_5y[v.name], v.name)
        passed, fails = h1_h5_gate(baseline, results_5y[v.name])
        print(f"  H1-H5 5y gate: {'PASS' if passed else 'FAIL'}")
        for f in fails: print(f"    - {f}")

    # Multi-window check (1y, 3y) — only for top candidates after 5y screening
    print("\n" + "="*120)
    print("Multi-window check (1y, 3y)")
    print("="*120)
    multi_windows = {
        '1y': (date.today() - timedelta(days=365)).isoformat(),
        '3y': (date.today() - timedelta(days=3*365)).isoformat(),
    }
    candidates_to_multi = [v for v in variants if v.name != 'PROD_v27']
    multi_results = defaultdict(dict)
    for v in candidates_to_multi:
        for label, since in multi_windows.items():
            r = evaluate(v, df, barriers, since=since)
            multi_results[v.name][label] = r
        # baseline
    base_multi = {}
    for label, since in multi_windows.items():
        base_multi[label] = evaluate(variants[0], df, barriers, since=since)

    print("\nH5 multi-window check (sign consistency on call tiers):")
    print(f"{'Variant':<18} | {'1y 75+':>9} {'3y 75+':>9} {'5y 75+':>9} | {'1y 80+':>9} {'3y 80+':>9} {'5y 80+':>9} | sign?")
    for v in candidates_to_multi:
        cells = [v.name]
        ok_75 = True; ok_80 = True
        for tier in ['75+', '80+']:
            b1 = base_multi['1y'][tier][1]; c1 = multi_results[v.name]['1y'][tier][1]
            b3 = base_multi['3y'][tier][1]; c3 = multi_results[v.name]['3y'][tier][1]
            b5 = baseline[tier][1]; c5 = results_5y[v.name][tier][1]
            d1 = c1 - b1 if (b1 and c1) else None
            d3 = c3 - b3 if (b3 and c3) else None
            d5 = c5 - b5 if (b5 and c5) else None
            cells.append(f'{d1:+.2f}pp' if d1 is not None else '-')
            cells.append(f'{d3:+.2f}pp' if d3 is not None else '-')
            cells.append(f'{d5:+.2f}pp' if d5 is not None else '-')
            # H5: signs consistent
            signs = [d for d in [d1, d3, d5] if d is not None]
            if len(signs) >= 2 and not all(s >= 0 for s in signs) and not all(s <= 0 for s in signs):
                if tier == '75+': ok_75 = False
                if tier == '80+': ok_80 = False
        sign_status = ('75+' if not ok_75 else '') + ('|80+' if not ok_80 else '')
        cells.append('OK' if not sign_status else f'INCONSIST({sign_status})')
        print(f"{cells[0]:<18} | " + " ".join(f"{c:>9}" for c in cells[1:7]) + f" | {cells[7]}")

    # Save full results
    out = {
        'baseline': 'PROD_v27',
        'results_5y': {k: {b: list(v) for b, v in r.items()} for k, r in results_5y.items()},
    }
    with open(CACHE_DIR / 'sweep_results.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n[saved] {CACHE_DIR / 'sweep_results.json'}")
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
