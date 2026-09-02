"""
Option B: composite-revert exploration.

Tests whether reverting the 2026-04-09 composite inversion (Priority #6)
improves per-trade WR after F3f has shipped at the allocation layer.

The 2026-04-09 commit (`d93ff2d`) flipped two signs:
  - VIX gradient: legacy bands had calm = HIGH vix_score; new tanh has calm = LOW.
  - Breadth term: legacy used breadth_score directly; new uses (100 - breadth).

apply_regime_to_score interprets HIGH multiplier as "amplify calls" — so the
correct composite direction is: HEALTHY market → HIGH composite → HIGH mult →
calls amplified. The current formula does the OPPOSITE (post-2026-04-09).

This script applies a corrected composite to pre_regime scores and reports
per-trade barrier WR per bucket. F3f (the allocation knob) is independent of
this — it bypasses the composite at the alloc layer regardless. Option B is
about whether the composite's effect on Score.overall is also worth fixing.

Variants:
  B_current      : reproduce production composite (post-inversion)
  J_legacy_full  : legacy VIX bands + breadth direct + market_trend (pre-2026-04-09)
  K_legacy_no_mt : legacy VIX bands + breadth direct, no market_trend (corrected vs current)
  L_brd_direct   : current VIX gradient (kept) + breadth direct (only flip the breadth sign)

If J/K/L improve per-trade WR meaningfully on the dip window AND don't
materially regress 5y, the next step is:
  1. Revert compute_regime_composite to the chosen variant
  2. AlgorithmVersion bump
  3. trader recalculate --force --full
  4. Re-run canonical 3-mode MC vs F3f baseline (the new production)
  5. If it passes the gate, ship.
"""
from __future__ import annotations

import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json, math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.models.core import Score, MarketRegime, MarketBreadth, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import _peak_side, _realized_vol_pct, _swing_walk

CALL_THRESHOLD = 70
PUT_THRESHOLD  = 25
MIN_N_REPORT   = 15
DISPLAY_PERIODS = ['7d', '15d', '30d', '60d']

_BANDS = [
    (0,  15, 0.70, 0.70), (15, 30, 0.70, 0.78),
    (30, 45, 0.78, 0.88), (45, 60, 0.88, 1.00),
    (60, 75, 1.00, 1.05), (75, 101, 1.05, 1.10),
]


def _apply_bands(c):
    for lo, hi, m_lo, m_hi in _BANDS:
        if lo <= c < hi:
            t = (c - lo) / (hi - lo) if hi > lo else 0
            return round(m_lo + t * (m_hi - m_lo), 4)
    return 1.0


def _apply_to_score(pre_regime, mult):
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


# ─── Composite formulas ─────────────────────────────────────────────

def _vix_tanh(vix_close, vix_chg):
    if vix_close is None or vix_chg is None:
        return None
    level = 50 + 50 * math.tanh((vix_close - 22) / 10)
    trend = 50 + 50 * math.tanh(vix_chg / 15)
    return max(0, min(100, level * 0.55 + trend * 0.45))


def _vix_legacy_bands(vix_close, vix_chg):
    if vix_close is None or vix_chg is None:
        return None
    level_bands = [(0, 15, 90), (15, 20, 75), (20, 25, 55),
                   (25, 30, 35), (30, 40, 15), (40, 999, 0)]
    trend_bands = [(-999, -15, 90), (-15, -5, 70), (-5, 5, 50),
                   (5, 15, 30), (15, 999, 10)]
    def _b(bands, v):
        for lo, hi, s in bands:
            if lo <= v < hi: return s
        return bands[-1][2]
    return _b(level_bands, vix_close) * 0.55 + _b(trend_bands, vix_chg) * 0.45


def _market_trend_legacy(spy_close, spy_ema50, spy_ema200):
    if None in (spy_close, spy_ema50, spy_ema200):
        return None
    p50  = (spy_close - spy_ema50)  / spy_ema50  * 100
    p200 = (spy_close - spy_ema200) / spy_ema200 * 100
    e50_bands  = [(-999, -3, 15), (-3, 0, 35), (0, 3, 65), (3, 999, 85)]
    e200_bands = [(-999, -5, 10), (-5, 0, 35), (0, 5, 65), (5, 999, 85)]
    def _b(bands, v):
        for lo, hi, s in bands:
            if lo <= v < hi: return s
        return bands[-1][2]
    return _b(e50_bands, p50) * 0.5 + _b(e200_bands, p200) * 0.5


def _vix_dyn_weight(vix_close):
    if vix_close is None: return 0.35
    raw = 1 / (1 + math.exp(-0.25 * (vix_close - 22)))
    return max(0.05, min(0.95, raw))


def variant_current(reg):
    """Current production: VIX tanh (high VIX → high score) + (100 − breadth)."""
    vs  = _vix_tanh(reg.get('vix_close'), reg.get('vix_chg'))
    bi  = (100 - reg['breadth']) if reg.get('breadth') is not None else None
    if vs is None or bi is None: return None
    w_v = _vix_dyn_weight(reg.get('vix_close'))
    return vs * w_v + bi * (1 - w_v)


def variant_J_legacy_full(reg):
    """Legacy: bands VIX + breadth direct + market_trend (pre-2026-04-09)."""
    vs = _vix_legacy_bands(reg.get('vix_close'), reg.get('vix_chg'))
    br = reg.get('breadth')
    mt = _market_trend_legacy(reg.get('spy_close'), reg.get('spy_ema50'), reg.get('spy_ema200'))
    if vs is None or br is None or mt is None: return None
    return vs * 0.35 + br * 0.40 + mt * 0.25


def variant_K_legacy_no_mt(reg):
    """Legacy bands VIX + breadth direct, no market_trend (corrected from current)."""
    vs = _vix_legacy_bands(reg.get('vix_close'), reg.get('vix_chg'))
    br = reg.get('breadth')
    if vs is None or br is None: return None
    w_v = _vix_dyn_weight(reg.get('vix_close'))
    return vs * w_v + br * (1 - w_v)


def variant_L_brd_direct(reg):
    """Keep current VIX tanh direction; only flip breadth: use breadth_score directly."""
    vs = _vix_tanh(reg.get('vix_close'), reg.get('vix_chg'))
    br = reg.get('breadth')
    if vs is None or br is None: return None
    w_v = _vix_dyn_weight(reg.get('vix_close'))
    return vs * w_v + br * (1 - w_v)


VARIANTS = [
    ('B_current',      variant_current),
    ('J_legacy_full',  variant_J_legacy_full),
    ('K_legacy_no_mt', variant_K_legacy_no_mt),
    ('L_brd_direct',   variant_L_brd_direct),
]

# ─── Data loading ───────────────────────────────────────────────────

def _load_active_version():
    return (AlgorithmVersion.select()
            .where(AlgorithmVersion.git_message != '')
            .order_by(AlgorithmVersion.id.desc())
            .first())


def _load_peaks(start, end, version):
    rows = (Score.select(Score.symbol, Score.date, Score.overall,
                         Score.regime_multiplier, Score.weight_info)
            .where(Score.date >= start, Score.date <= end,
                   Score.version == version,
                   Score.overall.is_null(False))
            .order_by(Score.date))
    out = []
    for s in rows:
        ov = int(s.overall)
        if not (ov >= CALL_THRESHOLD or ov <= PUT_THRESHOLD):
            continue
        pre = None
        if s.weight_info:
            try:
                d = json.loads(s.weight_info)
                if 'pre_regime' in d:
                    pre = int(d['pre_regime'])
            except Exception:
                pass
        if pre is None and s.regime_multiplier is not None:
            m = float(s.regime_multiplier)
            if m != 1.0:
                if ov >= 50:
                    pre = round(50 + (ov - 50) / m)
                else:
                    pre = round(50 + (ov - 50) / (2.0 - m))
            else:
                pre = ov
        if pre is None:
            pre = ov
        out.append({
            'symbol': s.symbol_id, 'date': s.date,
            'overall': ov, 'pre_regime': max(0, min(100, pre)),
        })
    return out


def _load_regime_cache(start, end):
    regs = list(MarketRegime.select(
        MarketRegime.date, MarketRegime.vix_close, MarketRegime.vix_10d_change,
        MarketRegime.spy_close, MarketRegime.spy_ema50, MarketRegime.spy_ema200,
    ).where(MarketRegime.date >= start, MarketRegime.date <= end))
    brds = {b.date: float(b.breadth_score)
            for b in MarketBreadth.select()
            .where(MarketBreadth.date >= start, MarketBreadth.date <= end)
            if b.breadth_score is not None}

    cache = {}
    for r in regs:
        cache[r.date] = {
            'vix_close':  float(r.vix_close)        if r.vix_close       is not None else None,
            'vix_chg':    float(r.vix_10d_change)   if r.vix_10d_change  is not None else None,
            'spy_close':  float(r.spy_close)        if r.spy_close       is not None else None,
            'spy_ema50':  float(r.spy_ema50)        if r.spy_ema50       is not None else None,
            'spy_ema200': float(r.spy_ema200)       if r.spy_ema200      is not None else None,
            'breadth':    brds.get(r.date),
        }
    return cache


def _load_price_cache(symbols, start):
    rows = list(PriceHistory.select(
        PriceHistory.symbol, PriceHistory.date,
        PriceHistory.close, PriceHistory.high, PriceHistory.low,
    ).where(PriceHistory.symbol.in_(list(symbols)))
     .order_by(PriceHistory.symbol, PriceHistory.date)
     .tuples())
    cache = defaultdict(list)
    for sym, d, c, h, l in rows:
        if d >= start - timedelta(days=70):
            cache[sym].append((d, float(c), float(h), float(l)))
    return dict(cache)


def _run_barrier(symbol, peak_date, score, price_rows):
    if not price_rows:
        return None
    di = {r[0]: i for i, r in enumerate(price_rows)}
    base = di.get(peak_date)
    if base is None:
        return None
    dates  = [r[0] for r in price_rows]
    closes = [r[1] for r in price_rows]
    highs  = [r[2] for r in price_rows]
    lows   = [r[3] for r in price_rows]
    side   = _peak_side(score)
    vol = _realized_vol_pct(closes, base)
    swing = _swing_walk(closes, base, side, vol, highs=highs, lows=lows, dates=dates)
    if swing is None:
        return None
    return {'side': side, 'swing': swing}


def _bucket(score):
    if score >= 90: return '90+'
    if score >= 85: return '85-89'
    if score >= 80: return '80-84'
    if score >= 75: return '75-79'
    if score >= 70: return '70-74'
    if score <= 5:  return '<5'
    if score <= 15: return '<15'
    if score <= 25: return '<25'
    return None


def _wr(entries, period):
    per = [e['swing'][period] for e in entries if period in e.get('swing', {})]
    if len(per) < MIN_N_REPORT:
        return None, len(per)
    wins = sum(1 for s in per if s['result'] == 'win')
    return round(wins / len(per) * 100, 1), len(per)


def run_window(label, start, end):
    print(f"\n{'='*100}")
    print(f"WINDOW: {label}  ({start} → {end})")
    print(f"{'='*100}")

    version = _load_active_version()
    peaks = _load_peaks(start, end, version)
    print(f"  pre-regime peaks loaded: {len(peaks)}")
    if not peaks: return

    cache = _load_regime_cache(start, end)
    syms = {p['symbol'] for p in peaks}
    print(f"  loading prices for {len(syms)} symbols...")
    pcache = _load_price_cache(syms, start)

    # Run all variants
    results = {}
    for label_v, fn in VARIANTS:
        bucket_results = defaultdict(list)
        n_skip = 0
        for pk in peaks:
            reg = cache.get(pk['date'])
            if reg is None:
                n_skip += 1; continue
            comp = fn(reg)
            mult = _apply_bands(comp) if comp is not None else 1.0
            new_score = _apply_to_score(pk['pre_regime'], mult)
            b = _bucket(new_score)
            if b is None:
                n_skip += 1; continue
            r = _run_barrier(pk['symbol'], pk['date'], new_score, pcache.get(pk['symbol'], []))
            if r is None:
                n_skip += 1; continue
            bucket_results[b].append(r)
        results[label_v] = bucket_results
        n_total = sum(len(v) for v in bucket_results.values())
        print(f"  {label_v:<18} N_qual={n_total} (skipped {n_skip})")

    # WR15 + WR30 table
    print(f"\n{'Bucket':<8}", end='')
    for vlbl, _ in VARIANTS:
        print(f" {vlbl:>16}", end='')
    print()
    print('-' * 100)

    for bucket in ['90+','85-89','80-84','75-79','70-74','<5','<15','<25']:
        print(f"{bucket:<8}", end='')
        for vlbl, _ in VARIANTS:
            entries = results[vlbl].get(bucket, [])
            wr15, n15 = _wr(entries, '15d')
            wr30, n30 = _wr(entries, '30d')
            if wr15 is None and wr30 is None:
                print(f" {'      —      ':>16}", end='')
            else:
                wr15s = f"{wr15:.1f}" if wr15 is not None else '  - '
                wr30s = f"{wr30:.1f}" if wr30 is not None else '  - '
                # Use larger N as the "N" for display
                n = max(n15, n30)
                print(f" {f'{wr15s}/{wr30s}({n})':>16}", end='')
        print()


def main():
    args = sys.argv[1:]
    # Default: dip + 3y
    windows = [
        ('DIP-5m', date(2025, 11, 1), date(2026, 4, 24)),
        ('1y',     date(2025, 4, 24), date(2026, 4, 24)),
        ('3y',     date(2023, 4, 24), date(2026, 4, 24)),
    ]
    for w in args:
        if w == '5y':
            windows = [('5y', date(2021, 4, 24), date(2026, 4, 24))]
        elif w == 'dip':
            windows = [('DIP-5m', date(2025, 11, 1), date(2026, 4, 24))]

    for label, s, e in windows:
        run_window(label, s, e)


if __name__ == '__main__':
    main()
