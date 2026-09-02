"""
Phase A — per-trade sweep across all 5 CT gradient-gating designs.

Builds one ScoreSimulator (full universe, 22-now lookback), then for each
variant:
  1. swap scoring_mod.compute_overall_score with make_ct_score(design, **params)
  2. run sim.simulate()
  3. build non-overlapping peaks (score >= 70 or <= 25), recompute TREND per peak
  4. classify into CT-PUT / AL-PUT / CT-CALL / AL-CALL via (overall, trend) thresholds
  5. walk WR15 via vol-adjusted barrier-touch (mirrors x_conf_counter_trend.py)
  6. emit per-window N + resolved + WR15 per bucket

Output: long-form table printed to stdout (captured to .out file via stdout redirect).

Validation gate (per CLAUDE.md Priority #8):
  - CT-PUT WR15 >= 81% on 5y, AL-PUT/AL-CALL preserved within +-0.5pp vs baseline
  - Portfolio validation (Phase B) comes next for survivors only.

Parameter grid (designed for breadth first, depth later):

  baseline (α=0)              — reference
  uniform  (A)                — α ∈ {0.10, 0.25, 0.50}
  asym_put (D)  ★ prior-1     — α ∈ {0.50, 0.75, 1.00}, put_gate ∈ {30, 35, 40, 45}
  regime_cond (C) ★ prior-2   — α ∈ {0.50, 1.00},        regime_cutoff ∈ {0.90, 0.95, 1.00}
  floored  (B)                — α=0.50, floor ∈ {0.50, 0.60, 0.75, 0.85}
  redistribute (E)            — cutoff ∈ {0.20, 0.30, 0.40}, redistrib_max ∈ {0.30, 0.50}

~34 variants total. Each simulate() is ~90s on full universe + 5y — the sweep
is several hours. Background run.
"""
from __future__ import annotations

import math
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import database.utils.scoring as scoring_mod
from experiments.ct_designs import make_ct_score, _ORIGINAL


def _trend_for_peak(ctx, d):
    return ctx.stock.calculate_trend_score(
        d, _ind_cache=ctx.ind_cache_for(d), _ph_cache=ctx.ph_map
    )


def _walk_wr15(peaks, ph_by_sym):
    """Mirror of experiments/x_conf_counter_trend.py::_walk_wr15."""
    import numpy as np
    from datetime import timedelta as td

    def vol_pct(rows, idx, lookback=60):
        start = max(0, idx - lookback)
        closes = [float(r.close) for r in rows[start:idx + 1] if r.close]
        if len(closes) < 20:
            return None
        rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        if not rets:
            return None
        return float(np.std(rets, ddof=0)) * 100.0

    wins = total = 0
    scale = math.sqrt(15.0 / 30.0)
    for p in peaks:
        rows = ph_by_sym.get(p.symbol_id, [])
        if not rows:
            continue
        idx = next((i for i, r in enumerate(rows) if r.date == p.date), None)
        if idx is None or idx == 0:
            continue
        v = vol_pct(rows, idx)
        if v is None or v <= 0:
            continue
        entry = float(rows[idx].close)
        side_high = (p.overall < 50)  # put side
        if side_high:
            K, M = 1.0, 2.0
        else:
            K, M = 2.0, 5.0
        target = entry * (1 - K * v / 100 * scale) if side_high else entry * (1 + K * v / 100 * scale)
        stop   = entry * (1 + M * v / 100 * scale) if side_high else entry * (1 - M * v / 100 * scale)
        cutoff = p.date + td(days=15)
        verdict = None
        for r in rows[idx + 1:]:
            if r.date > cutoff:
                break
            hi, lo = float(r.high), float(r.low)
            if side_high:
                if lo <= target:
                    verdict = 1; break
                if hi >= stop:
                    verdict = 0; break
            else:
                if hi >= target:
                    verdict = 1; break
                if lo <= stop:
                    verdict = 0; break
        if verdict is None:
            continue
        wins += verdict
        total += 1
    return wins, total


def _classify(peaks, trend_by_key, ct_put_trend_min=70, ct_call_trend_max=30):
    ct_put, al_put, ct_call, al_call = [], [], [], []
    for p in peaks:
        tr = trend_by_key.get((p.symbol_id, p.date))
        if tr is None:
            continue
        if p.overall <= 25 and tr >= ct_put_trend_min:
            ct_put.append(p)
        elif p.overall <= 25 and tr <= 30:
            al_put.append(p)
        elif p.overall >= 70 and tr <= ct_call_trend_max:
            ct_call.append(p)
        elif p.overall >= 70 and tr >= 70:
            al_call.append(p)
    return {
        'CT-PUT':  ct_put,
        'AL-PUT':  al_put,
        'CT-CALL': ct_call,
        'AL-CALL': al_call,
    }


def _slice_peaks(peaks, start, end):
    return [p for p in peaks if start <= p.date <= end]


def _build_variants():
    """Return list of (label, scoring_fn_or_None).  None = baseline (use _ORIGINAL)."""
    out = [('baseline', None)]

    # Design A — uniform
    for a in (0.10, 0.25, 0.50):
        out.append((f'A_uniform_a{int(a*100):03d}',
                    make_ct_score('uniform', alpha=a)))

    # Design D — asym_put (★ highest priority)
    for a in (0.50, 0.75, 1.00):
        for pg in (30, 35, 40, 45):
            out.append((f'D_asym_put_a{int(a*100):03d}_pg{pg}',
                        make_ct_score('asym_put', alpha=a, put_gate=float(pg))))

    # Design C — regime_cond
    for a in (0.50, 1.00):
        for rc in (0.90, 0.95, 1.00):
            out.append((f'C_regime_a{int(a*100):03d}_rc{int(rc*100):03d}',
                        make_ct_score('regime_cond', alpha=a, regime_cutoff=rc)))

    # Design B — floored
    for fl in (0.50, 0.60, 0.75, 0.85):
        out.append((f'B_floored_fl{int(fl*100):03d}',
                    make_ct_score('floored', alpha=0.50, floor=fl)))

    # Design E — redistribute
    for cu in (0.20, 0.30, 0.40):
        for rm in (0.30, 0.50):
            out.append((f'E_redist_cu{int(cu*100):03d}_rm{int(rm*100):03d}',
                        make_ct_score('redistribute', cutoff=cu, redistrib_max=rm)))

    return out


def _run_variant(label, fn, sim, cutoff_date, windows):
    from simulator import _FakePeak
    from database.models.technical import PriceHistory

    print("\n" + "=" * 100)
    print(f"  VARIANT: {label}")
    if fn is not None:
        print(f"    design={fn._design}  params={fn._params}")
    print("=" * 100)
    sys.stdout.flush()

    t0 = time.time()
    scoring_mod.compute_overall_score = _ORIGINAL if fn is None else fn
    scores = sim.simulate()
    sim_elapsed = time.time() - t0

    by_symbol = defaultdict(list)
    for (sym, d), score in scores.items():
        if d < cutoff_date:
            continue
        if score >= 70 or score <= 25:
            by_symbol[sym].append((d, score))

    sim_peaks = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            sim_peaks.append(_FakePeak(sym, d, score))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    trend_by_key = {}
    for p in sim_peaks:
        ctx = sim._contexts.get(p.symbol_id)
        if ctx is None:
            continue
        tr = _trend_for_peak(ctx, p.date)
        if tr is not None:
            trend_by_key[(p.symbol_id, p.date)] = tr

    all_syms = list({p.symbol_id for p in sim_peaks})
    rows = list(
        PriceHistory.select()
        .where(PriceHistory.symbol.in_(all_syms), PriceHistory.date >= cutoff_date)
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .namedtuples()
    )
    ph_by_sym = defaultdict(list)
    for r in rows:
        ph_by_sym[r.symbol].append(r)

    print(f"  [sim {sim_elapsed:.1f}s  peaks={len(sim_peaks):,}  trend_keys={len(trend_by_key):,}]")
    sys.stdout.flush()

    for win_label, start, end in windows:
        slice_peaks = _slice_peaks(sim_peaks, start, end)
        buckets = _classify(slice_peaks, trend_by_key)
        print(f"\n-- Window [{win_label}]  ({start} -> {end})   peaks={len(slice_peaks)}")
        for bname, plist in buckets.items():
            wins, total = _walk_wr15(plist, ph_by_sym)
            wr = (100.0 * wins / total) if total else 0.0
            print(f"  {bname:10s}  N={len(plist):6d}  resolved={total:6d}  WR15={wr:5.1f}%")
        sys.stdout.flush()


def main():
    from simulator import ScoreSimulator

    days = (date.today() - date(2022, 1, 1)).days
    sim = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)
    cutoff_date = date.today() - timedelta(days=days)
    today = date.today()

    def clamp(d): return min(d, today)
    windows = [
        ('22-now', date(2022, 1, 1), today),
        ('2022',   date(2022, 1, 1), clamp(date(2022, 12, 31))),
        ('2023',   date(2023, 1, 1), clamp(date(2023, 12, 31))),
        ('2024',   date(2024, 1, 1), clamp(date(2024, 12, 31))),
        ('2025',   date(2025, 1, 1), clamp(date(2025, 12, 31))),
    ]

    variants = _build_variants()

    print(f"Phase A sweep — {len(variants)} variants  lookback={days}d  cutoff={cutoff_date}")
    print(f"Windows: {[w[0] for w in windows]}")
    sys.stdout.flush()

    overall_t0 = time.time()
    for i, (label, fn) in enumerate(variants, 1):
        print(f"\n### [{i}/{len(variants)}] {label}  (elapsed {time.time()-overall_t0:.0f}s)")
        sys.stdout.flush()
        _run_variant(label, fn, sim, cutoff_date, windows)

    scoring_mod.compute_overall_score = _ORIGINAL
    print(f"\n\n=== Phase A complete in {time.time()-overall_t0:.0f}s ===")


if __name__ == '__main__':
    main()
