"""
Design D — asymmetric put-only apply flip.

Background:
  v22 (uniform X-conf gate) and v23 (regime-conditional X-conf gate) both passed
  per-trade WR but FAILED canonical 3-mode MC because they touched the call path
  in non-bull regimes, throttling 2024 bull-year compounding. Tier 1 (M_flip,
  N_no_regime, O_top_passthrough) all regressed 90+ WR15 by 4-8pp because
  flipping the apply demoted stress-tape pre_regime ≥ 88 calls (which win at
  77-81%) and recruited calm-tape pre_regime ≥ 88 calls (which win at ~70%)
  into the 90+ bucket.

  Side-finding from Tier 1: M_flip puts WR15 IMPROVED (<5: +5.1pp, <15: +3.1pp,
  <25: +2.3pp). The flip moves narrow-bull-mislabeled-as-stress puts OUT of <25
  (correct - those are profit-taking pullbacks) and moves narrow-stress-mislabeled-
  as-calm puts INTO <25 (correct - those are real breakdowns).

  Design D restricts the flip to the put path (pre_regime < 50) only:
    - Calls: byte-identical to B_current. No regression risk on 90+/85-89.
    - Puts: get the M_flip semantic correction. Expected to capture the
      documented +2-5pp WR15 lift on <25/<15/<5.

Per-trade gate (vs B_current):
  Call buckets (90+, 85-89, 80-84, 75-79, 70-74): IDENTICAL by construction.
  Put buckets <25, <15, <5: WR15 lift > 0pp; total put N within ±15% of B.

Mechanism (mirrors database/utils/scoring.py:201-206):
  Standard apply:
    if overall >= 50: adj = 50 + (overall - 50) * mult           # call path
    else:             adj = 50 + (overall - 50) * (2.0 - mult)   # put path

  Design D:
    if pre_regime >= 50: adj = 50 + (pre_regime - 50) * mult            # unchanged
    else:                adj = 50 + (pre_regime - 50) * mult            # FLIPPED

  i.e. on the put path only, drop the (2.0 - mult) mirror and apply mult
  directly. This matches what the inverted composite (post-2026-04-09) actually
  encodes for puts.
"""
from __future__ import annotations

import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.models.core import Score, MarketRegime, AlgorithmVersion
from database.models.technical import PriceHistory
from assess_scores import _peak_side, _realized_vol_pct, _swing_walk

CALL_THRESHOLD = 70
PUT_THRESHOLD  = 25
MIN_N_REPORT   = 15

# ─── Apply variants ─────────────────────────────────────────────────

def _apply_current(pre_regime, mult):
    """Production: amplify calls when mult > 1, dampen puts when mult > 1.

    With the inverted composite (post-2026-04-09):
      narrow-stress (high VIX low brd) -> high composite -> mult > 1
        -> calls amplified (CORRECT in calm-bear setup) but puts dampened (WRONG).
      narrow-bull   (low VIX high brd) -> low composite -> mult < 1
        -> calls dampened (WRONG in calm-bull setup) but puts amplified (WRONG).
    """
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


def _apply_design_d(pre_regime, mult):
    """Design D: flip the put-path apply ONLY. Calls unchanged.

    Call path: identical to B_current (no risk to 90+/85-89 WR).
    Put path:  drop the (2.0 - mult) mirror; apply mult directly.

    This means a put with pre_regime=15 in a STRESS regime (mult=1.10):
      B_current: 50 + (15 - 50) * (2.0 - 1.10) = 50 + (-35) * 0.90 = 50 - 31.5 = 18.5 -> 19
      D_put:    50 + (15 - 50) * 1.10           = 50 + (-35) * 1.10 = 50 - 38.5 = 11.5 -> 12
      => put PUSHED DEEPER in stress (correct: stress amplifies bearish signal)

    And a put with pre_regime=15 in a BULL regime (mult=0.90):
      B_current: 50 + (-35) * 1.10 = 11.5 -> 12   (deeper - WRONG)
      D_put:    50 + (-35) * 0.90 = 18.5 -> 19   (shallower - CORRECT)
    """
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult                # unchanged
    else:
        adj = 50 + (pre_regime - 50) * mult                # FLIPPED (no mirror)
    return int(max(0, min(100, round(adj))))


VARIANTS = [
    ('B_current', _apply_current),
    ('D_put_flip', _apply_design_d),
]

# ─── Data loading (mirrors tier1_flip_apply.py) ────────────────────

def _load_active_version():
    try:
        return AlgorithmVersion.get_active_scores_version()
    except Exception:
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
            # Need to also load borderline pre_regime peaks that might cross
            # the threshold under D variant. Lower the gate to 65 / 30 here.
            if not (ov >= 65 or ov <= 30):
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
        mult = float(s.regime_multiplier) if s.regime_multiplier else 1.0
        out.append({
            'symbol':  s.symbol_id, 'date': s.date,
            'overall': ov, 'pre_regime': max(0, min(100, pre)),
            'mult':    mult,
        })
    return out


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
    print(f"  active version: {version.git_commit[:8] if version else 'unknown'} "
          f"({version.git_message[:60] if version else ''})")

    peaks = _load_peaks(start, end, version)
    print(f"  pre-regime peaks loaded: {len(peaks)}")
    if not peaks: return

    syms = {p['symbol'] for p in peaks}
    print(f"  loading prices for {len(syms)} symbols...")
    pcache = _load_price_cache(syms, start)

    results = {}
    for vlabel, fn in VARIANTS:
        bucket_results = defaultdict(list)
        n_skip = 0
        for pk in peaks:
            new_score = fn(pk['pre_regime'], pk['mult'])
            b = _bucket(new_score)
            if b is None:
                n_skip += 1; continue
            r = _run_barrier(pk['symbol'], pk['date'], new_score, pcache.get(pk['symbol'], []))
            if r is None:
                n_skip += 1; continue
            bucket_results[b].append(r)
        results[vlabel] = bucket_results
        n_total = sum(len(v) for v in bucket_results.values())
        print(f"  {vlabel:<14} N_qual={n_total} (skipped {n_skip})")

    print(f"\n{'Bucket':<8}", end='')
    for vlbl, _ in VARIANTS:
        print(f" {vlbl:>22}", end='')
    print(f" {'ΔWR15':>8} {'ΔWR30':>8} {'ΔN':>8}")
    print('-' * (8 + 22 * len(VARIANTS) + 30))

    for bucket in ['90+','85-89','80-84','75-79','70-74','<5','<15','<25']:
        print(f"{bucket:<8}", end='')
        cells = []
        for vlbl, _ in VARIANTS:
            entries = results[vlbl].get(bucket, [])
            wr15, n15 = _wr(entries, '15d')
            wr30, n30 = _wr(entries, '30d')
            cells.append((wr15, wr30, max(n15, n30)))
            if wr15 is None and wr30 is None:
                print(f" {'      —      ':>22}", end='')
            else:
                wr15s = f"{wr15:.1f}" if wr15 is not None else '  - '
                wr30s = f"{wr30:.1f}" if wr30 is not None else '  - '
                n = max(n15, n30)
                print(f" {f'{wr15s}/{wr30s}(N={n})':>22}", end='')
        # Delta column (D vs B)
        b_wr15, b_wr30, b_n = cells[0]
        d_wr15, d_wr30, d_n = cells[1]
        if b_wr15 is not None and d_wr15 is not None:
            print(f" {d_wr15-b_wr15:+8.1f}", end='')
        else:
            print(f" {'—':>8}", end='')
        if b_wr30 is not None and d_wr30 is not None:
            print(f" {d_wr30-b_wr30:+8.1f}", end='')
        else:
            print(f" {'—':>8}", end='')
        if b_n > 0:
            print(f" {(d_n-b_n)/b_n*100:+7.1f}%")
        else:
            print(f" {'—':>8}")


def main():
    args = sys.argv[1:]
    windows = [
        ('DIP-5m', date(2025, 11, 1), date(2026, 4, 24)),
        ('1y',     date(2025, 4, 24), date(2026, 4, 24)),
        ('3y',     date(2023, 4, 24), date(2026, 4, 24)),
    ]
    if '5y' in args:
        windows = [('5y', date(2021, 4, 24), date(2026, 4, 24))]
    elif 'dip' in args:
        windows = [('DIP-5m', date(2025, 11, 1), date(2026, 4, 24))]
    elif 'all' in args:
        windows = [
            ('DIP-5m', date(2025, 11, 1), date(2026, 4, 24)),
            ('1y',     date(2025, 4, 24), date(2026, 4, 24)),
            ('3y',     date(2023, 4, 24), date(2026, 4, 24)),
            ('5y',     date(2021, 4, 24), date(2026, 4, 24)),
        ]
    elif 'years' in args:
        windows = [
            ('2021', date(2021, 1, 1), date(2021, 12, 31)),
            ('2022', date(2022, 1, 1), date(2022, 12, 31)),
            ('2023', date(2023, 1, 1), date(2023, 12, 31)),
            ('2024', date(2024, 1, 1), date(2024, 12, 31)),
            ('2025', date(2025, 1, 1), date(2025, 12, 31)),
        ]

    for label, s, e in windows:
        run_window(label, s, e)

    print(f"\n{'='*100}")
    print("Per-trade gate (vs B_current):")
    print("  Call buckets 90+/85-89/80-84/75-79/70-74: IDENTICAL by construction (sanity check)")
    print("  Put bucket <25 WR15 lift > 0pp")
    print("  Put bucket <15 WR15 lift > 0pp")
    print("  Total put N within +/- 15% of B_current N")
    print("If gate passes, queue canonical 3-mode MC for portfolio validation.")


if __name__ == '__main__':
    main()
