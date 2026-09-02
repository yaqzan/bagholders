"""2026 pre-earnings 75+ CALL win rate, ACROSS scoring versions.

The user traded LIVE 2026 scores (v57 rollback -> v59 -> v60 -> v69 on 2026-05-31).
v69 (honest) pruned ~27% of 75+ as look-ahead false-positives. This compares how
each version's 2026 75+ near-earnings cohort actually fared, to see whether the
user's "completely opposite the score" perception matches the version they traded.

Win = in-memory forward walk over real prices (version-independent for a given
(symbol,date)); walked ONCE over the union, then attributed per version.
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

from database.trader_database import DB
from database.models.core import AlgorithmVersion, _load_effective_earnings_dates
import assess_scores as A

Peak = namedtuple('Peak', ['symbol_id', 'date', 'overall'])
PH = namedtuple('PH', ['date', 'close', 'high', 'low'])
VERSIONS = [('v69', 69), ('v60', 60), ('v59', 59), ('v57', 57)]
START = '2026-01-01'


def near(sym, d_obj, eff_by_sym):
    eff = eff_by_sym.get(sym)
    if eff is None:
        eff = _load_effective_earnings_dates(sym); eff_by_sym[sym] = eff
    if not eff:
        return False
    idx = bisect_right(eff, d_obj)
    if idx >= len(eff):
        return False
    return 1 <= (eff[idx] - d_obj).days <= 7


def walk(peaks, ph_by_sym, k, m):
    ok, om = A.SWING_K_LOW, A.SWING_M_LOW
    A.SWING_K_LOW, A.SWING_M_LOW = k, m
    try:
        res = A.calculate_forward_returns_from_cache(peaks, ph_by_sym)
    finally:
        A.SWING_K_LOW, A.SWING_M_LOW = ok, om
    out = {}
    for r in res:
        p = (r.get('swing') or {}).get('15d')
        out[(r['symbol'], r['date'])] = None if (not p or p.get('result') is None) else (1 if p['result'] == 'win' else 0)
    return out


def main():
    t0 = time.time()
    eff_by_sym = {}
    per_ver = {}       # label -> list of (sym,date,overall,is_near)
    union = {}         # (sym,date) -> overall (any)
    syms = set()
    for label, vid in VERSIONS:
        cur = DB.execute_sql(
            "SELECT symbol,date,overall FROM scores WHERE version_id=%s AND overall>=75 AND date>=%s",
            [vid, START])
        sigs = []
        for sym, d, ovr in cur.fetchall():
            d_obj = d if hasattr(d, 'year') else date.fromisoformat(d)
            isn = near(sym, d_obj, eff_by_sym)
            sigs.append((sym, d_obj, int(ovr), isn))
            union[(sym, d_obj)] = int(ovr); syms.add(sym)
        per_ver[label] = sigs
        print(f"[{label}] 2026 75+ calls: {len(sigs):,} (near={sum(1 for s in sigs if s[3])})", flush=True)

    # PH only since 2025-09 (enough for 60d vol on Jan-2026 signals) -> fast.
    t1 = time.time()
    ph_by_sym = defaultdict(list)
    ph = ','.join(['%s'] * len(syms))
    cur = DB.execute_sql(
        f"SELECT symbol,date,close,high,low FROM price_history "
        f"WHERE symbol IN ({ph}) AND date>=%s ORDER BY symbol,date", list(syms) + ['2025-09-01'])
    n = 0
    for sym, d, c, h, l in cur.fetchall():
        n += 1; ph_by_sym[sym].append(PH(d, float(c), float(h), float(l)))
    print(f"[price] {n:,} rows / {len(ph_by_sym)} syms in {time.time()-t1:.1f}s", flush=True)

    upeaks = [Peak(s, d, o) for (s, d), o in union.items()]
    gen = walk(upeaks, ph_by_sym, 2.0, 5.0)
    opt = walk(upeaks, ph_by_sym, 1.274, 1.092)
    print(f"[walk] union {len(upeaks):,} -> gen {len(gen):,} / opt {len(opt):,}\n", flush=True)

    def rate(vals):
        vals = [v for v in vals if v is not None]
        return (100.0 * sum(vals) / len(vals)) if vals else None
    def fr(v): return f"{v:5.1f}%" if v is not None else f"{'-':>6}"

    print("=" * 96)
    print("2026 PRE-EARNINGS 75+ CALL WIN RATE BY SCORING VERSION  (WR15: g=generic 2s/5s, o=opt 1.274s/1.092s)")
    print("=" * 96)
    print(f"{'Ver':<5} | {'NEAR-EARNINGS 75+':^28} | {'BASELINE 75+':^28} | {'LIFT near-base':^14}")
    print(f"{'':<5} | {'N(res)':>7} {'WR15g':>6} {'WR15o':>6} | {'N(res)':>7} {'WR15g':>6} {'WR15o':>6} | {'g':>6} {'o':>6}")
    print("-" * 96)
    for label, _vid in VERSIONS:
        ng = [gen.get((s, d)) for (s, d, o, isn) in per_ver[label] if isn]
        no = [opt.get((s, d)) for (s, d, o, isn) in per_ver[label] if isn]
        bg = [gen.get((s, d)) for (s, d, o, isn) in per_ver[label] if not isn]
        bo = [opt.get((s, d)) for (s, d, o, isn) in per_ver[label] if not isn]
        ng_r, no_r = rate(ng), rate(no); bg_r, bo_r = rate(bg), rate(bo)
        nN = len([v for v in ng if v is not None]); bN = len([v for v in bg if v is not None])
        lg = (ng_r - bg_r) if (ng_r is not None and bg_r is not None) else None
        lo = (no_r - bo_r) if (no_r is not None and bo_r is not None) else None
        print(f"{label:<5} | {nN:>7} {fr(ng_r)} {fr(no_r)} | {bN:>7} {fr(bg_r)} {fr(bo_r)} | "
              f"{(f'{lg:+5.1f}' if lg is not None else '   -'):>6} {(f'{lo:+5.1f}' if lo is not None else '   -'):>6}")
    print("-" * 96)
    print("call break-even ~45% on opt barrier. N(res)=resolved (15d window elapsed).")
    print(f"[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
