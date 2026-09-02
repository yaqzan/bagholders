"""Pre-earnings 75+ CALL signal win analysis, BY YEAR.

User question (2026-06-01): do 75+ CALL signals fired just BEFORE an earnings
date still win the way they did in prior years? Hypothesis: 2026's pre-earnings
75+ signals have been "completely opposite the score" (losing), whereas in
prior years earnings amplified gains enough to justify the EARN_BOOST.

EXPLORATORY MEASUREMENT (not a calibration sweep) so it intentionally includes
post-CALIBRATION_CUTOFF_DATE (2026) data — that is the whole point.

Method:
  - Active scoring version (v69 honest, full 10y coverage).
  - All CALL signals with FINAL overall >= 75 (what the dashboard/trader sees).
  - Earnings proximity computed independently from the authoritative
    effective_date table (AMC-shifted), calendar-day cohorts matching the
    shipped lift table:  pre1=1, pre3=2-3, pre7=4-7, none=else.
    near-earnings := next earnings within 7 calendar days of signal.
  - Win via the canonical IN-MEMORY forward walk (assess_scores._peak_to_result,
    version-independent, computed from real price data — NOT the 160-day barrier
    cache). Two barrier definitions:
        generic : SWING_K_LOW=2.0 / SWING_M_LOW=5.0  (dashboard WR15/WR30)
        opt     : SWING_K_LOW=1.274 / SWING_M_LOW=1.092 (30 DTE option-aligned)
  - A not-yet-elapsed window returns None (dropped), so recent-2026 unresolved
    trades are excluded, never counted as losses.
"""
from __future__ import annotations
import io, sys, os, json, time
from bisect import bisect_right
from collections import defaultdict, namedtuple
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.trader_database import DB
from database.models.core import AlgorithmVersion, _load_effective_earnings_dates
import assess_scores as A

Peak = namedtuple('Peak', ['symbol_id', 'date', 'overall'])
PH = namedtuple('PH', ['date', 'close', 'high', 'low'])

CALL_BE = 45.0  # ~30 DTE call break-even win rate (option-aligned)


def cohort_label(d):
    if d is None:   return 'none'
    if d == 1:      return 'pre1'
    if 2 <= d <= 3: return 'pre3'
    if 4 <= d <= 7: return 'pre7'
    return 'none'


def walk_outcomes(peaks, ph_by_sym, k_low, m_low):
    """Run the in-memory swing walk with given call-side barrier; return
    {(symbol,date)-> {w15,w30,ret15,mfe15,mae15}}."""
    ok_l, om_l = A.SWING_K_LOW, A.SWING_M_LOW
    A.SWING_K_LOW, A.SWING_M_LOW = k_low, m_low
    try:
        results = A.calculate_forward_returns_from_cache(peaks, ph_by_sym)
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = ok_l, om_l
    out = {}
    for r in results:
        sw = r.get('swing', {})
        def win(lbl):
            p = sw.get(lbl)
            if not p or p.get('result') is None:
                return None
            return 1 if p['result'] == 'win' else 0
        out[(r['symbol'], r['date'])] = {
            'w15': win('15d'), 'w30': win('30d'),
            'ret15': (r.get('returns') or {}).get('15d'),
            'mfe15': (r.get('mfes') or {}).get('15d'),
            'mae15': (r.get('maes') or {}).get('15d'),
        }
    return out


def main():
    t0 = time.time()
    av = AlgorithmVersion.get_active_scores_version()
    print(f"[active] v{av.id} commit={av.git_commit[:10]}", flush=True)

    # 1) Final-overall >= 75 CALL signals (full history).
    cur = DB.execute_sql(
        "SELECT symbol, date, overall, weight_info FROM scores "
        "WHERE version_id=%s AND overall>=75 ORDER BY symbol, date", [av.id])
    raw = cur.fetchall()
    print(f"[query] {len(raw):,} final-overall>=75 CALL signals", flush=True)

    # 2) Earnings proximity + boost overlay.
    eff_by_sym = {}
    sigs = []  # (sym, date_obj, overall, cohort, ern_boost)
    syms_needed = set()
    last_sym = None; eff = []
    for sym, d, ovr, wi_raw in raw:
        d_obj = d if hasattr(d, 'year') else date.fromisoformat(d)
        syms_needed.add(sym)
        if sym != last_sym:
            eff = eff_by_sym.get(sym)
            if eff is None:
                eff = _load_effective_earnings_dates(sym); eff_by_sym[sym] = eff
            last_sym = sym
        my_days = None
        if eff:
            idx = bisect_right(eff, d_obj)
            if idx < len(eff):
                delta = (eff[idx] - d_obj).days
                if 1 <= delta <= 7:
                    my_days = delta
        ern_boost = None
        if wi_raw:
            try: ern_boost = json.loads(wi_raw).get('ern_boost')
            except (TypeError, json.JSONDecodeError): pass
        sigs.append((sym, d_obj, int(ovr), cohort_label(my_days), ern_boost))
    print(f"[earnings] proximity attached ({len(eff_by_sym):,} symbols) in {time.time()-t0:.1f}s", flush=True)

    # 3) Bulk-load PriceHistory for needed symbols (full history, ascending).
    t1 = time.time()
    ph_by_sym = defaultdict(list)
    placeholders = ','.join(['%s'] * len(syms_needed))
    cur = DB.execute_sql(
        f"SELECT symbol, date, close, high, low FROM price_history "
        f"WHERE symbol IN ({placeholders}) ORDER BY symbol, date", list(syms_needed))
    n = 0
    for sym, d, c, h, l in cur.fetchall():
        n += 1
        ph_by_sym[sym].append(PH(d, float(c), float(h), float(l)))
    print(f"[price] loaded {n:,} OHLC rows for {len(ph_by_sym):,} symbols in {time.time()-t1:.1f}s", flush=True)

    # 4) Forward walks (generic + option-aligned).
    peaks = [Peak(s, d, o) for (s, d, o, _c, _b) in sigs]
    gen = walk_outcomes(peaks, ph_by_sym, 2.0, 5.0)
    opt = walk_outcomes(peaks, ph_by_sym, 1.274, 1.092)
    print(f"[walk] generic resolved {len(gen):,} / opt resolved {len(opt):,} (of {len(peaks):,})", flush=True)

    # 5) Aggregate.
    def blank():
        return {'n': 0, 'w15g': [], 'w15o': [], 'w30g': [], 'ret': [], 'mfe': [], 'mae': [], 'boosted': 0}
    by = defaultdict(blank)         # (year|'PRIOR', group) ; group in near/base
    by_cohort = defaultdict(blank)  # (year|'PRIOR', cohort)
    pending = defaultdict(int)

    for (sym, d_obj, ovr, cohort, ern_boost) in sigs:
        key = (sym, d_obj)
        g = gen.get(key); o = opt.get(key)
        if g is None or g.get('w15') is None:
            pending[d_obj.year] += 1
            continue
        group = 'near' if cohort != 'none' else 'base'
        buckets = [(d_obj.year, group)]
        if d_obj.year < 2026:
            buckets.append(('PRIOR', group))
        for yk, gp in buckets:
            t = by[(yk, gp)]
            t['n'] += 1
            t['w15g'].append(g['w15'])
            if o and o.get('w15') is not None: t['w15o'].append(o['w15'])
            if g.get('w30') is not None: t['w30g'].append(g['w30'])
            if g.get('ret15') is not None: t['ret'].append(g['ret15'])
            if g.get('mfe15') is not None: t['mfe'].append(g['mfe15'])
            if g.get('mae15') is not None: t['mae'].append(g['mae15'])
            if ern_boost not in (None, 0, 0.0): t['boosted'] += 1
        if group == 'near':
            for yk in ([d_obj.year, 'PRIOR'] if d_obj.year < 2026 else [d_obj.year]):
                c = by_cohort[(yk, cohort)]
                c['n'] += 1; c['w15g'].append(g['w15'])
                if o and o.get('w15') is not None: c['w15o'].append(o['w15'])

    def pct(l): return (100.0 * sum(l) / len(l)) if l else None
    def avg(l): return (sum(l) / len(l)) if l else None
    def f(v, suf='%'): return f"{v:5.1f}{suf}" if v is not None else f"{'-':>6}"
    def lift(a, b): return (a - b) if (a is not None and b is not None) else None
    def fl(v): return (f"{v:+5.1f}" if v is not None else "   -")

    years = sorted([y for y in {k[0] for k in by} if y != 'PRIOR'])
    order = years + ['PRIOR']

    print("\n" + "=" * 108)
    print(f"PRE-EARNINGS 75+ CALL SIGNALS - WIN RATE BY YEAR   (active v{av.id} honest; in-memory walk over real prices)")
    print("near-earnings = next earnings effective_date within 1-7 calendar days of the signal")
    print("WR = barrier-touch win within 15 cal days.  g=dashboard 2s/5s.  o=30DTE option-aligned 1.274s/1.092s.")
    print("=" * 108)
    print(f"{'Year':<6} | {'NEAR-EARNINGS 75+':^41} | {'BASELINE 75+ (>7d to ern)':^33} | {'LIFT near-base':^15}")
    print(f"{'':<6} | {'N':>4} {'WR15g':>6} {'WR15o':>6} {'WR30g':>6} {'%boost':>6} | {'N':>4} {'WR15g':>6} {'WR15o':>6} {'WR30g':>6} | {'WR15g':>7} {'WR15o':>6}")
    print("-" * 108)
    for yk in order:
        if yk == 'PRIOR': print("-" * 108)
        ne = by.get((yk, 'near'), blank()); ba = by.get((yk, 'base'), blank())
        ne_g, ba_g = pct(ne['w15g']), pct(ba['w15g'])
        ne_o, ba_o = pct(ne['w15o']), pct(ba['w15o'])
        bo = (100.0 * ne['boosted'] / ne['n']) if ne['n'] else None
        label = '16-25' if yk == 'PRIOR' else str(yk)
        print(f"{label:<6} | {ne['n']:>4} {f(ne_g)} {f(ne_o)} {f(pct(ne['w30g']))} {f(bo)} | "
              f"{ba['n']:>4} {f(ba_g)} {f(ba_o)} {f(pct(ba['w30g']))} | "
              f"{fl(lift(ne_g,ba_g)):>7} {fl(lift(ne_o,ba_o)):>6}")
    print("-" * 108)
    print(f"call break-even ~{CALL_BE:.0f}% on the option-aligned (o) barrier. PRIOR='16-25' pools 2016-2025.")
    if pending:
        print("unresolved (window not elapsed / insufficient data), excluded: " +
              ", ".join(f"{y}:{n}" for y, n in sorted(pending.items()) if n))

    print("\n" + "=" * 92)
    print("NEAR-EARNINGS 75+ MAGNITUDE (15d fwd, generic barrier) - does earnings 'amplify gains'?")
    print("=" * 92)
    print(f"{'Year':<6} | {'N':>4} | {'avg exit_ret':>12} | {'avg MFE15':>10} | {'avg MAE15':>10}")
    print("-" * 60)
    for yk in order:
        ne = by.get((yk, 'near'), blank())
        label = '16-25' if yk == 'PRIOR' else str(yk)
        print(f"{label:<6} | {ne['n']:>4} | {f(avg(ne['ret']))} | {f(avg(ne['mfe']))} | {f(avg(ne['mae']))}")

    print("\n" + "=" * 92)
    print("NEAR-EARNINGS COHORT DETAIL  (WR15  g=generic / o=opt , N)")
    print("=" * 92)
    cohorts = ['pre1', 'pre3', 'pre7']
    print(f"{'Year':<6} | " + " | ".join(f"{c:^24}" for c in cohorts))
    for yk in order:
        label = '16-25' if yk == 'PRIOR' else str(yk)
        cells = []
        for c in cohorts:
            st = by_cohort.get((yk, c))
            if not st or not st['w15g']:
                cells.append(f"{'-':^24}")
            else:
                cells.append(f"g{f(pct(st['w15g']))} o{f(pct(st['w15o']))} n={st['n']:>3}")
        print(f"{label:<6} | " + " | ".join(cells))

    print(f"\n[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
