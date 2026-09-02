"""Honest EARN_BOOST PUT ledger (mirror of build_ledger.py, side='high').

pre_boost <= 25 put signals, v69, holdout-locked (<=2026-05-15). Walks generic
(K_high=1.0/M_high=2.0) and option-aligned (K_high=1.274/M_high=0.728) barriers.
Puts boost only when already <=25 (EARN_BOOST_PUT_ADMIT=False), so universe is
pre_boost<=25. Output: .cache/earnboost_honest/put_ledger_v69_holdout.parquet
"""
from __future__ import annotations
import io, sys, os, json, time
from bisect import bisect_right
from collections import defaultdict, namedtuple
from datetime import date
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import polars as pl
from database.trader_database import DB
from database.models.core import AlgorithmVersion, _load_effective_earnings_dates
from experiments._holdout import assert_no_holdout_leak, cutoff_iso
import assess_scores as A

Peak = namedtuple('Peak', ['symbol_id', 'date', 'overall'])
PH = namedtuple('PH', ['date', 'close', 'high', 'low'])
CUTOFF = cutoff_iso()


def cohort_label(d):
    if d is None:   return 'none'
    if d == 1:      return 'pre1'
    if 2 <= d <= 3: return 'pre3'
    if 4 <= d <= 7: return 'pre7'
    return 'none'


def walk(peaks, ph_by_sym, k_high, m_high):
    ok, om = A.SWING_K_HIGH, A.SWING_M_HIGH
    A.SWING_K_HIGH, A.SWING_M_HIGH = k_high, m_high
    try:
        res = A.calculate_forward_returns_from_cache(peaks, ph_by_sym)
    finally:
        A.SWING_K_HIGH, A.SWING_M_HIGH = ok, om
    out = {}
    for r in res:
        sw = r.get('swing', {})
        def win(lbl):
            p = sw.get(lbl)
            if not p or p.get('result') is None: return None
            return 1 if p['result'] == 'win' else 0
        out[(r['symbol'], r['date'])] = {
            'w7': win('7d'), 'w15': win('15d'), 'w30': win('30d'),
            'mfe15': (r.get('mfes') or {}).get('15d'), 'mae15': (r.get('maes') or {}).get('15d'),
        }
    return out


def main():
    t0 = time.time()
    A.PERIODS = [(l, w) for (l, w) in A.PERIODS if w <= 30]
    av = AlgorithmVersion.get_active_scores_version()
    assert av.id == 69
    print(f"[active] v{av.id} {av.git_commit[:10]} cutoff={CUTOFF} periods={A.PERIODS}", flush=True)

    cur = DB.execute_sql(
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id=%s AND date<=%s AND overall<=28 ORDER BY symbol, date", [av.id, CUTOFF])
    rows = cur.fetchall()
    print(f"[query] {len(rows):,} prefilter rows (overall<=28)", flush=True)

    eff_by_sym = {}; sigs = []; syms = set(); last = None; eff = []
    for sym, d, ovr, wi_raw in rows:
        d_obj = d if hasattr(d, 'year') else date.fromisoformat(d)
        pre_b = ovr; ern_boost = None
        if wi_raw:
            try:
                w = json.loads(wi_raw); pb = w.get('pre_boost')
                if pb is not None: pre_b = int(pb)
                ern_boost = w.get('ern_boost')
            except (TypeError, json.JSONDecodeError): pass
        if pre_b > 25:
            continue
        if sym != last:
            eff = eff_by_sym.get(sym)
            if eff is None: eff = _load_effective_earnings_dates(sym); eff_by_sym[sym] = eff
            last = sym
        my_days = None
        if eff:
            idx = bisect_right(eff, d_obj)
            if idx < len(eff):
                delta = (eff[idx] - d_obj).days
                if 1 <= delta <= 7: my_days = delta
        sigs.append({'symbol': sym, 'date': d_obj, 'pre_boost': int(pre_b), 'final_overall': int(ovr),
                     'days_to_ern': my_days, 'cohort': cohort_label(my_days),
                     'ern_boost': float(ern_boost) if ern_boost is not None else 0.0})
        syms.add(sym)
    assert_no_holdout_leak([s['date'] for s in sigs], context='earnboost_honest_put_ledger')
    print(f"[universe] pre_boost<=25 PUT signals: {len(sigs):,} ({len(syms)} syms) "
          f"near={sum(1 for s in sigs if s['cohort']!='none')} in {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    ph_by_sym = defaultdict(list)
    ph = ','.join(['%s'] * len(syms))
    cur = DB.execute_sql(f"SELECT symbol,date,close,high,low FROM price_history WHERE symbol IN ({ph}) "
                         f"ORDER BY symbol,date", list(syms))
    n = 0
    for sym, d, c, h, l in cur.fetchall():
        n += 1; ph_by_sym[sym].append(PH(d, float(c), float(h), float(l)))
    print(f"[price] {n:,} rows / {len(ph_by_sym)} syms in {time.time()-t1:.1f}s", flush=True)

    t2 = time.time()
    peaks = [Peak(s['symbol'], s['date'], s['pre_boost']) for s in sigs]
    gen = walk(peaks, ph_by_sym, 1.0, 2.0)
    opt = walk(peaks, ph_by_sym, 1.274, 0.728)
    print(f"[walk] gen {len(gen):,} / opt {len(opt):,} in {time.time()-t2:.1f}s", flush=True)

    recs = []
    for s in sigs:
        k = (s['symbol'], s['date']); g = gen.get(k); o = opt.get(k)
        if not g: continue
        recs.append({**s, 'date': s['date'].isoformat(), 'year': s['date'].year,
                     'w7g': g['w7'], 'w15g': g['w15'], 'w30g': g['w30'], 'mfe15': g['mfe15'], 'mae15': g['mae15'],
                     'w7o': (o or {}).get('w7'), 'w15o': (o or {}).get('w15'), 'w30o': (o or {}).get('w30')})
    df = pl.DataFrame(recs)
    out = ROOT / '.cache' / 'earnboost_honest'; out.mkdir(parents=True, exist_ok=True)
    pq = out / 'put_ledger_v69_holdout.parquet'
    df.write_parquet(pq)
    print(f"[saved] {pq} rows={len(df):,} w15g-resolved={len(df.filter(pl.col('w15g').is_not_null())):,}", flush=True)
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
