"""
Tier 1.1 — flip apply_regime_to_score direction.

Diagnosis: the 2026-04-09 composite inversion produced a composite that
genuinely correlates with forward outcomes (commit message: avg Pearson
+0.067 vs legacy -0.037), AND Option B (composite revert) confirmed via
per-trade testing that pure revert catastrophically degrades the 90+
tier WR (81% -> 70-74%, see known-issues #6).

Conclusion: the new composite has signal; the bug is in how
apply_regime_to_score consumes it.  Current code amplifies calls when
mult > 1, but the new composite has mult > 1 in STRESS regimes — so
calls are amplified exactly when they should be damped.

Fix: invert the multiplier at apply time.  effective_mult = 2.0 - mult.
This preserves the composite (no recalc of MarketRegime needed for the
diagnostic — we just re-apply differently from pre_regime).

Variants tested:
  B_current   : reproduce production (inverted composite + current apply
                = stress-amplifies-calls, broken)
  M_flip_apply: same composite, inverted apply
                = stress-dampens-calls, calm-amplifies-calls (intended)
  N_no_regime : mult = 1.0 everywhere
                = pure scoring with no regime adjustment (Tier 1.2 baseline)
  O_top_passthrough : flip apply at 75-89 buckets only; pass-through at
                      90+ and <=15 (Tier 1.3 — protect top buckets)

Per-trade gate (vs B_current):
  90+ bucket WR15 must not regress more than 1pp.
  85-89 WR15 must not regress more than 2pp.
  At least one of: 80-84/75-79/70-74 improves WR15 by >=1pp.
  Total signal N must not collapse (>= 80% of B_current N).

If per-trade gate passes for any variant, queue canonical 3-mode MC.
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
    """Production: amplify calls when mult > 1."""
    if mult is None or mult == 1.0:
        return pre_regime
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * mult
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - mult)
    return int(max(0, min(100, round(adj))))


def _apply_flip(pre_regime, mult):
    """Tier 1.1: invert the multiplier semantically.

    effective_mult = 2.0 - mult, then apply normally.
    Stress (high composite -> high mult) -> low effective_mult -> calls dampened.
    Calm  (low composite  -> low mult)  -> high effective_mult -> calls amplified.
    """
    if mult is None or mult == 1.0:
        return pre_regime
    eff = 2.0 - mult
    if pre_regime >= 50:
        adj = 50 + (pre_regime - 50) * eff
    else:
        adj = 50 + (pre_regime - 50) * (2.0 - eff)   # = mult, restoring symmetry
    return int(max(0, min(100, round(adj))))


def _apply_none(pre_regime, mult):
    """Tier 1.2: no regime adjustment at all."""
    return pre_regime


def _apply_top_passthrough(pre_regime, mult):
    """Tier 1.3: flip apply at 70-89 / 16-30 buckets; pass-through at extremes.

    Mid-conviction tiers benefit from regime gating (filter noise out of
    borderline signals); top tiers (90+ / <=15) keep their high baseline WR.
    """
    if mult is None or mult == 1.0:
        return pre_regime
    # Pass-through extremes
    if pre_regime >= 90 or pre_regime <= 10:
        return pre_regime
    # Apply flipped to mids
    return _apply_flip(pre_regime, mult)


VARIANTS = [
    ('B_current',          _apply_current),
    ('M_flip_apply',       _apply_flip),
    ('N_no_regime',        _apply_none),
    ('O_top_passthrough',  _apply_top_passthrough),
]

# ─── Data loading (mirrors regime_ab_test.py) ──────────────────────

def _load_active_version():
    """Use the production active version (respects ALGORITHM_VERSION file).
    Falls back to highest-id-with-msg if get_active_scores_version isn't
    available for some reason.
    """
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
        print(f"  {vlabel:<22} N_qual={n_total} (skipped {n_skip})")

    print(f"\n{'Bucket':<8}", end='')
    for vlbl, _ in VARIANTS:
        print(f" {vlbl:>20}", end='')
    print()
    print('-' * (8 + 20 * len(VARIANTS)))

    for bucket in ['90+','85-89','80-84','75-79','70-74','<5','<15','<25']:
        print(f"{bucket:<8}", end='')
        for vlbl, _ in VARIANTS:
            entries = results[vlbl].get(bucket, [])
            wr15, n15 = _wr(entries, '15d')
            wr30, n30 = _wr(entries, '30d')
            if wr15 is None and wr30 is None:
                print(f" {'      —      ':>20}", end='')
            else:
                wr15s = f"{wr15:.1f}" if wr15 is not None else '  - '
                wr30s = f"{wr30:.1f}" if wr30 is not None else '  - '
                n = max(n15, n30)
                print(f" {f'{wr15s}/{wr30s}({n})':>20}", end='')
        print()


def main():
    args = sys.argv[1:]
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

    print(f"\n{'='*100}")
    print("Per-trade gate (vs B_current):")
    print("  90+ WR15 must not regress more than 1pp")
    print("  85-89 WR15 must not regress more than 2pp")
    print("  >=1 mid-tier (80-84/75-79/70-74) improves WR15 by >=1pp")
    print("  Total N >= 80% of B_current N")
    print("If gate passes, queue canonical 3-mode MC for portfolio validation.")


if __name__ == '__main__':
    main()
