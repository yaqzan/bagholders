"""Build the honest EARN_BOOST calibration ledger (CALLS), holdout-locked.

For every v69 CALL signal with pre_boost >= 70 and date <= CALIBRATION_CUTOFF
(2026-05-15), record:
  symbol, date, year, pre_boost, final_overall, days_to_ern (cal, strictly-future,
  <=7), cohort (pre1/pre3/pre7/none), ern_boost (stored), and barrier-touch wins
  for W in {7,15,30} on BOTH barrier sets:
    gen = 30dte_generic  (SWING 2.0s / 5.0s)  -> dashboard WR
    opt = 30dte_opt      (SWING 1.274s / 1.092s) -> 30 DTE option-aligned
plus mfe15/mae15 (generic).

Walk = assess_scores in-memory swing walk (calendar-day cutoffs, sqrt(W/30)
scaling) — identical methodology to barrier_outcomes, version-independent.
Periods capped at <=30d for speed.

Output parquet: .cache/earnboost_honest/call_ledger_v69_holdout.parquet
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
CUTOFF = cutoff_iso()   # 2026-05-15


def cohort_label(d):
    if d is None:   return 'none'
    if d == 1:      return 'pre1'
    if 2 <= d <= 3: return 'pre3'
    if 4 <= d <= 7: return 'pre7'
    return 'none'


def walk(peaks, ph_by_sym, k_low, m_low):
    """One scan per peak; returns {(sym,date)-> {w7,w15,w30, mfe15,mae15}}."""
    ok, om = A.SWING_K_LOW, A.SWING_M_LOW
    A.SWING_K_LOW, A.SWING_M_LOW = k_low, m_low
    try:
        res = A.calculate_forward_returns_from_cache(peaks, ph_by_sym)
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = ok, om
    out = {}
    for r in res:
        sw = r.get('swing', {})
        def win(lbl):
            p = sw.get(lbl)
            if not p or p.get('result') is None: return None
            return 1 if p['result'] == 'win' else 0
        out[(r['symbol'], r['date'])] = {
            'w7': win('7d'), 'w15': win('15d'), 'w30': win('30d'),
            'mfe15': (r.get('mfes') or {}).get('15d'),
            'mae15': (r.get('maes') or {}).get('15d'),
        }
    return out


def main():
    t0 = time.time()
    # cap periods <=30d for speed (early-exit limits forward iteration)
    A.PERIODS = [(l, w) for (l, w) in A.PERIODS if w <= 30]
    print(f"[periods] {A.PERIODS}", flush=True)

    av = AlgorithmVersion.get_active_scores_version()
    assert av.id == 69, f"expected v69 active, got {av.id}"
    print(f"[active] v{av.id} {av.git_commit[:10]}  cutoff={CUTOFF}", flush=True)

    # 1) pull call universe (prefilter overall>=60 to capture pre_boost>=70 even
    #    under max SCW dampening; parse pre_boost; keep pre_boost>=70).
    cur = DB.execute_sql(
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id=%s AND date<=%s AND overall>=60 ORDER BY symbol, date",
        [av.id, CUTOFF])
    rows = cur.fetchall()
    print(f"[query] {len(rows):,} prefilter rows (overall>=60, <=cutoff)", flush=True)

    eff_by_sym = {}
    sigs = []   # dicts
    syms = set()
    last = None; eff = []
    for sym, d, ovr, wi_raw in rows:
        d_obj = d if hasattr(d, 'year') else date.fromisoformat(d)
        pre_b = ovr; ern_boost = None
        if wi_raw:
            try:
                w = json.loads(wi_raw)
                pb = w.get('pre_boost')
                if pb is not None: pre_b = int(pb)
                ern_boost = w.get('ern_boost')
            except (TypeError, json.JSONDecodeError):
                pass
        if pre_b < 70:
            continue
        if sym != last:
            eff = eff_by_sym.get(sym)
            if eff is None:
                eff = _load_effective_earnings_dates(sym); eff_by_sym[sym] = eff
            last = sym
        my_days = None
        if eff:
            idx = bisect_right(eff, d_obj)
            if idx < len(eff):
                delta = (eff[idx] - d_obj).days
                if 1 <= delta <= 7:
                    my_days = delta
        sigs.append({'symbol': sym, 'date': d_obj, 'pre_boost': int(pre_b),
                     'final_overall': int(ovr), 'days_to_ern': my_days,
                     'cohort': cohort_label(my_days),
                     'ern_boost': float(ern_boost) if ern_boost not in (None,) else 0.0})
        syms.add(sym)
    assert_no_holdout_leak([s['date'] for s in sigs], context='earnboost_honest_ledger')
    print(f"[universe] pre_boost>=70 CALL signals: {len(sigs):,}  ({len(syms)} symbols)  "
          f"near={sum(1 for s in sigs if s['cohort']!='none')}  in {time.time()-t0:.1f}s", flush=True)

    # 2) PriceHistory (full history for these symbols; need 60d prior vol + 30d fwd).
    t1 = time.time()
    ph_by_sym = defaultdict(list)
    ph = ','.join(['%s'] * len(syms))
    cur = DB.execute_sql(
        f"SELECT symbol, date, close, high, low FROM price_history "
        f"WHERE symbol IN ({ph}) ORDER BY symbol, date", list(syms))
    n = 0
    for sym, d, c, h, l in cur.fetchall():
        n += 1; ph_by_sym[sym].append(PH(d, float(c), float(h), float(l)))
    print(f"[price] {n:,} OHLC rows / {len(ph_by_sym)} syms in {time.time()-t1:.1f}s", flush=True)

    # 3) walks
    t2 = time.time()
    peaks = [Peak(s['symbol'], s['date'], s['pre_boost']) for s in sigs]
    gen = walk(peaks, ph_by_sym, 2.0, 5.0)
    opt = walk(peaks, ph_by_sym, 1.274, 1.092)
    print(f"[walk] gen {len(gen):,} / opt {len(opt):,} in {time.time()-t2:.1f}s", flush=True)

    # 4) assemble + write parquet
    recs = []
    for s in sigs:
        k = (s['symbol'], s['date'])
        g = gen.get(k); o = opt.get(k)
        if not g:  # unanchored / no history
            continue
        recs.append({
            **s,
            'date': s['date'].isoformat(),
            'year': s['date'].year,
            'w7g': g['w7'], 'w15g': g['w15'], 'w30g': g['w30'],
            'mfe15': g['mfe15'], 'mae15': g['mae15'],
            'w7o': (o or {}).get('w7'), 'w15o': (o or {}).get('w15'), 'w30o': (o or {}).get('w30'),
        })
    df = pl.DataFrame(recs)
    out = ROOT / '.cache' / 'earnboost_honest'
    out.mkdir(parents=True, exist_ok=True)
    pq = out / 'call_ledger_v69_holdout.parquet'
    df.write_parquet(pq)
    res15 = df.filter(pl.col('w15g').is_not_null())
    print(f"[saved] {pq}  rows={len(df):,}  w15g-resolved={len(res15):,}", flush=True)
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
