"""
WR15 Stage-1 screen: VIX *weekly-MACD momentum* folded into the regime (post)
multiplier. /research follow-up 2026-06-09 (user clarified: they want a WR15
scoring improvement, not a DD lever).

The production regime multiplier ALREADY uses VIX level + VIX 10d-change (velocity)
via `_vix_gradient` (level*0.55 + tanh(10d_change/15)*0.45). Question: does a
smoother *weekly-MACD* reading of VIX add WR15 signal BEYOND that, when folded into
the post multiplier — and at what "blast radius" (suppression strength)?

Read-only (no DB writes, no recalc). Reuses experiments/regime_ab_test.py internals.
The barrier `swing` outcome per (symbol,date) is multiplier-INVARIANT, so it's walked
ONCE; W1 cohort split + every multiplier variant are pure re-aggregation.

W1 pre-flight (the decisive signal check): among ACTUAL qualifying peaks, do those
fired during rising VIX weekly-MACD have LOWER WR15 — orthogonal to VIX level (which
the multiplier already uses)? If not, no multiplier modulation by weekly-MACD can help.

Usage (queue, db-heavy):
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/vix_weekly_v70/wr15_regime.py [lookback_days]
"""
import sys, math, json
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import experiments.regime_ab_test as ab
from database.models.core import AlgorithmVersion

PERIOD = '15d'
MIN_N = 60
LOOKBACK = int(sys.argv[1]) if len(sys.argv) > 1 else 1825   # 5y default


def two_prop_z(p1, n1, p0, n0):
    if n1 == 0 or n0 == 0:
        return 0.0
    p = (p1 * n1 + p0 * n0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return (p1 - p0) / se if se > 0 else 0.0


def ema(xs, span):
    a = 2.0 / (span + 1.0)
    out, e = [], None
    for x in xs:
        e = x if e is None else a * x + (1.0 - a) * e
        out.append(e)
    return out


def main():
    ver = AlgorithmVersion.get_active_scores_version()
    print(f"active version: {ver.id if ver else None} ({getattr(ver,'git_hash',None) or getattr(ver,'commit_hash','')})  lookback={LOOKBACK}d")
    cutoff = date.today() - timedelta(days=LOOKBACK)

    print("loading peaks + regime + price caches ...")
    peaks = ab._load_peaks_with_pre_regime(LOOKBACK, ver)
    regc = ab._load_regime_cache(cutoff - timedelta(days=300))   # warmup for EMA
    symbols = {p['symbol'] for p in peaks}
    pricec = ab._load_price_cache(symbols, cutoff - timedelta(days=160))
    print(f"  peaks={len(peaks)}  symbols={len(symbols)}  regime_dates={len(regc)}")

    # --- VIX daily-equivalent weekly MACD (EMA60-EMA130, signal EMA45) + slope ---
    ds = sorted(d for d in regc if regc[d].get('vix_close') is not None)
    vs = [regc[d]['vix_close'] for d in ds]
    macd = [a - b for a, b in zip(ema(vs, 60), ema(vs, 130))]
    sig = ema(macd, 45)
    hist = [m - s for m, s in zip(macd, sig)]
    whist = {d: hist[i] for i, d in enumerate(ds)}
    wslope = {d: (hist[i] - hist[i - 5]) if i >= 5 else None for i, d in enumerate(ds)}

    # --- walk each peak ONCE; cache (win15, side, overall, whist, vix, slope) ---
    print("walking barriers (once per peak) ...")
    rec = []
    nbad = 0
    for pk in peaks:
        rows = pricec.get(pk['symbol'], [])
        r = ab._run_barrier(pk['symbol'], pk['date'], pk['overall'], rows)
        if r is None or PERIOD not in r['swing']:
            nbad += 1
            continue
        d = pk['date']
        rec.append({
            'sym': pk['symbol'], 'date': d, 'overall': pk['overall'],
            'pre': pk['pre_regime'], 'side': r['side'],
            'win': 1 if r['swing'][PERIOD]['result'] == 'win' else 0,
            'whist': whist.get(d), 'wslope': wslope.get(d),
            'vix': regc.get(d, {}).get('vix_close'),
        })
    print(f"  usable peaks={len(rec)}  (no-data {nbad})")

    out = {'version': ver.id if ver else None, 'lookback': LOOKBACK, 'n': len(rec)}

    # ============ W1: cohort split by VIX weekly-MACD (the signal check) ============
    def wr(sub):
        n = len(sub)
        return (round(100 * sum(x['win'] for x in sub) / n, 2), n) if n else (None, 0)

    print("\n" + "=" * 78)
    print("W1 — qualifying-peak WR15 split by VIX weekly-MACD (the signal check)")
    print("=" * 78)
    for side, lbl in [('low', 'CALL (overall>=70)'), ('high', 'PUT (overall<=25)')]:
        sd = [x for x in rec if x['side'] == side and x['whist'] is not None]
        base_wr, base_n = wr(sd)
        print(f"\n  {lbl}   base WR15={base_wr}%  N={base_n}")
        # whist sign x slope quadrant (the 'building' cohort = pos & rising)
        groups = {
            'whist_pos_rising (building)': [x for x in sd if x['whist'] >= 0 and (x['wslope'] or 0) >= 0],
            'whist_pos_falling':           [x for x in sd if x['whist'] >= 0 and (x['wslope'] or 0) < 0],
            'whist_neg':                   [x for x in sd if x['whist'] < 0],
        }
        for gl, g in groups.items():
            w, n = wr(g)
            if n >= MIN_N:
                rest = [x for x in sd if x not in g]
                rw, rn = wr(rest)
                z = two_prop_z(w / 100, n, rw / 100, rn)
                print(f"    {gl:30s} WR15={w:5.2f}% N={n:6d}  (rest {rw:5.2f}%/{rn})  z={z:+.2f}")
        # CONTROL for VIX level: within each VIX band, split by whist sign
        print(f"    -- controlling for VIX level --")
        for lo, hi, bl in [(0, 20, 'vix<20'), (20, 28, 'vix20-28'), (28, 99, 'vix>=28')]:
            band = [x for x in sd if x['vix'] is not None and lo <= x['vix'] < hi]
            pos = [x for x in band if x['whist'] >= 0]
            neg = [x for x in band if x['whist'] < 0]
            pw, pn = wr(pos)
            nw, nn = wr(neg)
            if pn >= MIN_N and nn >= MIN_N:
                z = two_prop_z(pw / 100, pn, nw / 100, nn)
                print(f"      {bl:9s}: whist>=0 WR15={pw:5.2f}%/{pn:5d}  vs whist<0 {nw:5.2f}%/{nn:5d}  z(pos-neg)={z:+.2f}")
        out[f'w1_{side}'] = {gl: wr(g) for gl, g in groups.items()}

    # ============ Variant sweep: fold weekly-MACD into the multiplier ============
    # blast radius = SHIFT (composite points subtracted at full rising-momentum).
    # Suppress hypothesis: rising VIX weekly-MACD -> lower composite -> lower mult ->
    # pull extremes toward 50 -> disqualify the (hopefully lower-WR15) signals.
    WSCALE = 0.7   # whist at which suppression is full (p90 ~ 0.7)

    def whist_pos(d):
        h = whist.get(d)
        if h is None or h <= 0:
            return 0.0
        return min(1.0, h / WSCALE)

    def mk_variant(shift, rising_only=False):
        def fn(reg, fg=None):
            if reg is None:
                return 1.0
            comp = ab._composite_current(reg, fg)
            if comp is None:
                return 1.0
            d = reg.get('date')
            wp = whist_pos(d)
            if rising_only and (wslope.get(d) or 0) < 0:
                wp = 0.0
            comp2 = max(0.0, min(100.0, comp - shift * wp))
            return ab._apply_bands(comp2, ab._BANDS_CURRENT)
        return fn

    variants = {
        'B_current': lambda reg, fg=None: (ab._apply_bands(ab._composite_current(reg, fg), ab._BANDS_CURRENT)
                                           if reg and ab._composite_current(reg, fg) is not None else 1.0),
        'J_suppress_shift10': mk_variant(10),
        'K_suppress_shift20': mk_variant(20),
        'L_suppress_shift30': mk_variant(30),
        'M_shift20_risingOnly': mk_variant(20, rising_only=True),
    }

    BUCKETS = ['90+', '85-89', '80-84', '75-79', '70-74', '<25', '<15', '<5']
    print("\n" + "=" * 78)
    print("Variant sweep — re-apply multiplier to pre_regime, re-bucket cached WR15")
    print("(blast radius = SHIFT composite-points suppressed at full rising momentum)")
    print("=" * 78)
    var_tables = {}
    for vl, vfn in variants.items():
        agg = defaultdict(lambda: [0, 0])  # bucket -> [wins, n]
        for x in rec:
            reg = regc.get(x['date'])
            mult = vfn(reg)
            ns = ab._apply_to_score(x['pre'], mult)
            b = ab._score_bucket(ns)
            if b is None:
                continue
            agg[b][0] += x['win']
            agg[b][1] += 1
        var_tables[vl] = {b: (round(100 * agg[b][0] / agg[b][1], 2) if agg[b][1] else None, agg[b][1]) for b in BUCKETS}

    # print side-by-side
    hdr = f"{'bucket':8s}"
    for vl in variants:
        hdr += f" | {vl[:16]:>16s}"
    print("\n" + hdr)
    for b in BUCKETS:
        row = f"{b:8s}"
        base = var_tables['B_current'][b]
        for vl in variants:
            w, n = var_tables[vl][b]
            d = (f"{w - base[0]:+.2f}" if (w is not None and base[0] is not None) else "  --")
            row += f" | {str(w):>6} N{n:<5} {d:>6}"
        print(row)
    out['variants'] = var_tables

    (Path(__file__).parent / 'wr15_regime_report.json').write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {Path(__file__).parent / 'wr15_regime_report.json'}")


if __name__ == '__main__':
    main()
