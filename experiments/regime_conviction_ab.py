"""
A/B test for score-magnitude-conditional regime dampening.

Hypothesis: the regime multiplier's impact should scale INVERSELY with signal
conviction. High-conviction signals (pre_regime 85+/15-) are the alpha-rich
tiers the cascade concentrates capital on; regime compression on those signals
is destructive. Marginal signals (pre_regime 70-80 / 20-30) are where regime
context actually filters noise.

This honors the "concentration philosophy" of Phase 11/13/15 — do not add N at
lower WR. Instead, protect top-tier WR from regime dampening while keeping full
regime influence on marginal signals.

Variants tested:

  BASE          - stored production multiplier (control)
  LIN_A05       - conviction-linear dampening, alpha=0.5
  LIN_A075      - alpha=0.75
  LIN_A10       - alpha=1.0 (high-conviction signals regime-immune)
  STEP          - discrete: |pr-50| >= 35 -> damp=0.3, >=25 -> damp=0.7, else full
  ASYM_CALLS    - LIN_A075 on calls only; full regime on puts
  NO_REGIME     - fixed mult=1.0 (reference)

Conviction formula: c = min(1, |pre_regime - 50| / 30)
  pre_regime = 50 -> c=0  (regime full)
  pre_regime = 65 -> c=0.5 (half regime)
  pre_regime = 80 -> c=1.0 (at threshold for full dampening)
  pre_regime = 95 -> c=1.0 (capped)

Effective multiplier: effective_mult = 1.0 + (stored_mult - 1.0) * (1 - c*alpha)

For each variant, reports:
  - Signal counts per cumulative bucket per year
  - Barrier-touch WR15 per bucket
  - Delta vs BASE
"""
from __future__ import annotations

import json
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
    if sigma is None or sigma <= 0:
        return None
    scale = (W / 30.0) ** 0.5
    if side == 'low':   # call
        tgt = entry * (1.0 + 2.0 * sigma * scale / 100.0)
        stp = entry * (1.0 - 5.0 * sigma * scale / 100.0)
    else:
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


# ---- Conviction-based dampening ----

def _conviction(pre_regime):
    """c = min(1, |pre_regime - 50| / 30).  c=0 at neutral, c=1 at pr>=80 or pr<=20."""
    return min(1.0, abs(pre_regime - 50) / 30.0)


def _effective_mult_linear(stored_mult, pre_regime, alpha):
    c = _conviction(pre_regime)
    damp = 1.0 - c * alpha
    return 1.0 + (stored_mult - 1.0) * damp


def _effective_mult_step(stored_mult, pre_regime):
    d = abs(pre_regime - 50)
    if d >= 35:
        damp = 0.3
    elif d >= 25:
        damp = 0.7
    else:
        damp = 1.0
    return 1.0 + (stored_mult - 1.0) * damp


# ---- Variant multiplier functions ----
# Each takes (stored_mult, pre_regime, side) -> effective_mult
# side is 'call' (pre_regime >= 50) or 'put' (pre_regime < 50)

def v_base(sm, pr, side):
    return sm


def v_lin_a05(sm, pr, side):
    return _effective_mult_linear(sm, pr, 0.50)


def v_lin_a075(sm, pr, side):
    return _effective_mult_linear(sm, pr, 0.75)


def v_lin_a10(sm, pr, side):
    return _effective_mult_linear(sm, pr, 1.00)


def v_step(sm, pr, side):
    return _effective_mult_step(sm, pr)


def v_asym_calls(sm, pr, side):
    if side == 'call':
        return _effective_mult_linear(sm, pr, 0.75)
    return sm


def v_no_regime(sm, pr, side):
    return 1.0


VARIANTS = [
    ('BASE', v_base),
    ('LIN_A05', v_lin_a05),
    ('LIN_A075', v_lin_a075),
    ('LIN_A10', v_lin_a10),
    ('STEP', v_step),
    ('ASYM_CALLS', v_asym_calls),
    ('NO_REGIME', v_no_regime),
]


# ---- Bucket helpers ----

CALL_CUTS = [70, 75, 80, 85, 90, 95]
PUT_CUTS = [25, 20, 15, 10, 5]


def _qualifies(overall, cut, side):
    return overall >= cut if side == 'call' else overall <= cut


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    print(f"Active version: v{av.id} ({av.git_commit[:8]})  lookback from {cutoff}\n")

    # Load pre_regime + stored multiplier
    print("Loading peaks...")
    q = f"""
        SELECT symbol, date, overall, regime_multiplier, weight_info
        FROM scores
        WHERE version_id = {vid}
          AND date >= '{cutoff}'
          AND weight_info IS NOT NULL
        ORDER BY symbol, date
    """
    rows = DB.execute_sql(q).fetchall()

    peaks = []
    for sym, d, overall, sm, wi in rows:
        try:
            wi_d = json.loads(wi) if isinstance(wi, str) else wi
            pr = wi_d.get('pre_regime')
            if pr is None:
                continue
            pr = int(pr)
        except Exception:
            continue
        # Pre-filter: keep candidates that could qualify under any variant.
        # - call: pr>=60 covers all paths to 70+ under any damp
        # - put: pr<=40 covers all paths to <=25 under any damp
        if not (pr >= 60 or pr <= 40):
            continue
        peaks.append({
            'sym': sym, 'date': d, 'pre_regime': pr,
            'stored_mult': float(sm) if sm is not None else 1.0,
            'overall_stored': int(overall),
        })
    print(f"  Loaded {len(peaks):,} candidate peaks")

    # Apply variants
    print("\nApplying variants...")
    for pk in peaks:
        pr = pk['pre_regime']
        sm = pk['stored_mult']
        side = 'call' if pr >= 50 else 'put'
        pk['variants'] = {}
        for vname, vfn in VARIANTS:
            m = vfn(sm, pr, side)
            overall = _apply_mult(pr, m)
            pk['variants'][vname] = {'overall': overall, 'mult': m, 'side': side}

    # Tally signal counts per variant per year per cumulative bucket
    print("Tallying signal counts...")
    # counts[(vname, window, cumulative_bucket_key)] = count
    counts = defaultdict(int)
    for pk in peaks:
        yr = pk['date'].year
        for vname, _ in VARIANTS:
            ov = pk['variants'][vname]['overall']
            side = pk['variants'][vname]['side']
            # cumulative buckets
            if side == 'call':
                for cut in CALL_CUTS:
                    if ov >= cut:
                        counts[(vname, yr, f"{cut}+")] += 1
                        counts[(vname, '5y', f"{cut}+")] += 1
            else:
                for cut in PUT_CUTS:
                    if ov <= cut:
                        counts[(vname, yr, f"<{cut}")] += 1
                        counts[(vname, '5y', f"<{cut}")] += 1

    # Print counts per year
    years = sorted({pk['date'].year for pk in peaks}) + ['5y']
    for yr in years:
        print(f"\n--- {yr} ---")
        print(f"{'bucket':<8} | " + " ".join(f"{v[:10]:>10}" for v, _ in VARIANTS))
        print("-" * (10 + 11 * len(VARIANTS)))
        for cut in CALL_CUTS:
            bk = f"{cut}+"
            cells = [f"{counts.get((v[0], yr, bk), 0):>10}" for v in VARIANTS]
            print(f"{bk:<8} | " + " ".join(cells))
        print()
        for cut in PUT_CUTS:
            bk = f"<{cut}"
            cells = [f"{counts.get((v[0], yr, bk), 0):>10}" for v in VARIANTS]
            print(f"{bk:<8} | " + " ".join(cells))

    # ---- Barrier-touch computation ----
    print("\n" + "=" * 130)
    print("BARRIER-TOUCH WR — loading price data...")
    print("=" * 130)

    qual_set = set()
    for pk in peaks:
        for vname, _ in VARIANTS:
            ov = pk['variants'][vname]['overall']
            if ov >= 70 or ov <= 25:
                qual_set.add((pk['sym'], pk['date']))
                break
    symbols_needed = set(s for s, _ in qual_set)
    print(f"  {len(qual_set):,} qualifying (sym, date) pairs across {len(symbols_needed):,} symbols")

    price_cache = {}
    for i, sym in enumerate(sorted(symbols_needed)):
        if i % 100 == 0:
            print(f"    ...loading {i}/{len(symbols_needed)}", flush=True)
        rows_p = DB.execute_sql(f"""
            SELECT date, close, high, low FROM price_history
            WHERE symbol = '{sym}' AND date >= '{cutoff}' ORDER BY date
        """).fetchall()
        price_cache[sym] = rows_p

    # Compute barrier outcomes once per (sym, date)
    print(f"\n  Computing barrier outcomes for {len(qual_set):,} pairs...")
    outcomes = {}
    for i, (sym, d) in enumerate(qual_set):
        if i % 5000 == 0:
            print(f"    ...walk {i}/{len(qual_set)}", flush=True)
        bars_list = price_cache.get(sym, [])
        d_idx = {b[0]: idx for idx, b in enumerate(bars_list)}
        idx = d_idx.get(d)
        if idx is None or idx < 30:
            continue
        closes = [float(b[1]) for b in bars_list]
        highs = [float(b[2]) for b in bars_list]
        lows = [float(b[3]) for b in bars_list]
        sigma = _realized_vol(closes[:idx + 1])
        if sigma is None:
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

    # Aggregate per variant per cumulative bucket per window
    print("\nAggregating WR...")
    wr = defaultdict(lambda: [0, 0])  # (variant, window, bucket, window_code) -> [wins, total]
    for pk in peaks:
        sym, d = pk['sym'], pk['date']
        out = outcomes.get((sym, d))
        if out is None:
            continue
        yr = d.year
        for vname, _ in VARIANTS:
            ov = pk['variants'][vname]['overall']
            side = pk['variants'][vname]['side']
            if side == 'call' and out['w15c'] is not None:
                for cut in CALL_CUTS:
                    if ov >= cut:
                        for window in ('5y', yr):
                            wr[(vname, window, f"{cut}+", 'w15')][0] += out['w15c']
                            wr[(vname, window, f"{cut}+", 'w15')][1] += 1
            if side == 'put' and out['w15p'] is not None:
                for cut in PUT_CUTS:
                    if ov <= cut:
                        for window in ('5y', yr):
                            wr[(vname, window, f"<{cut}", 'w15')][0] += out['w15p']
                            wr[(vname, window, f"<{cut}", 'w15')][1] += 1

    def _fmt(w, t):
        if t == 0:
            return '     --'
        return f"{w/t*100:5.1f}%/{t}"

    def _print_wr(window, side, cuts, label):
        print(f"\n--- {window} {label} WR15 ---")
        header = f"{'bucket':<8} | " + " ".join(f"{v[:12]:>12}" for v, _ in VARIANTS)
        print(header)
        print("-" * len(header))
        for cut in cuts:
            bk = f"{cut}+" if side == 'call' else f"<{cut}"
            cells = []
            for vname, _ in VARIANTS:
                w, t = wr.get((vname, window, bk, 'w15'), [0, 0])
                cells.append(f"{_fmt(w, t):>12}")
            print(f"{bk:<8} | " + " ".join(cells))

    print("\n" + "=" * 140)
    print("BARRIER-TOUCH WR15 PER VARIANT PER WINDOW")
    print("=" * 140)
    for window in ['5y', 2022, 2023, 2024, 2025]:
        _print_wr(window, 'call', CALL_CUTS, 'CALL')
        _print_wr(window, 'put',  PUT_CUTS,  'PUT')

    # Headline 5y summary
    print("\n" + "=" * 140)
    print("HEADLINE 5y METRICS PER VARIANT — focus on TOP-TIER WR preservation")
    print("=" * 140)
    print(f"{'variant':<12} "
          f"{'c70+N':>8} {'c70+WR':>7} "
          f"{'c80+N':>8} {'c80+WR':>7} "
          f"{'c90+N':>7} {'c90+WR':>7} "
          f"{'c95+N':>7} {'c95+WR':>7} "
          f"{'p25N':>7} {'p25WR':>7} "
          f"{'p15N':>7} {'p15WR':>7}")
    for vname, _ in VARIANTS:
        c70 = wr.get((vname, '5y', '70+', 'w15'), [0, 0])
        c80 = wr.get((vname, '5y', '80+', 'w15'), [0, 0])
        c90 = wr.get((vname, '5y', '90+', 'w15'), [0, 0])
        c95 = wr.get((vname, '5y', '95+', 'w15'), [0, 0])
        p25 = wr.get((vname, '5y', '<25', 'w15'), [0, 0])
        p15 = wr.get((vname, '5y', '<15', 'w15'), [0, 0])
        def p(x):
            return f"{x[0]/x[1]*100:5.1f}" if x[1] else '   --'
        print(f"{vname:<12} "
              f"{c70[1]:>8} {p(c70):>7} "
              f"{c80[1]:>8} {p(c80):>7} "
              f"{c90[1]:>7} {p(c90):>7} "
              f"{c95[1]:>7} {p(c95):>7} "
              f"{p25[1]:>7} {p(p25):>7} "
              f"{p15[1]:>7} {p(p15):>7}")


if __name__ == '__main__':
    main()
