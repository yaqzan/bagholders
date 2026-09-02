"""
Phase B — canonical 3-mode MC sweep over Phase A survivor variants.

Pattern:
  1. Build ONE ScoreSimulator (shared across variants)
  2. For each variant (design + params):
       a. Swap scoring_mod.compute_overall_score = make_ct_score(design, **params)
       b. sim.simulate() -> scores
       c. Build call/put signal objects with TREND recomputed per peak
       d. For each window (subset of monte_carlo.WINDOWS):
            - Feed to monte_carlo.precompute_outcomes / precompute_put_outcomes
            - Run N_ITER sims per collision mode (via monte_carlo.run_single_sim)
            - Record mean/worst DD, CTP/PTP rates, final portfolio value

Gate:
  - No Conservative-mode WorstDD > 80% on any window
  - No Realistic-mode mean return loss > 25% vs v19 baseline

Outputs: stdout table of every variant x window x mode, plus summary ranking.

Usage:
    # Edit VARIANTS at bottom of file, then:
    python experiments/ct_phase_b_mc.py  > experiments/ct_phase_b.out
"""
from __future__ import annotations

import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import database.utils.scoring as scoring_mod
import monte_carlo as MC
from experiments.ct_designs import make_ct_score, _ORIGINAL


# ---- Signal adapter ---------------------------------------------------------

@dataclass
class _Sig:
    symbol_id: int
    date: date
    overall: float
    trend: float


def _trend_for_peak(ctx, d):
    return ctx.stock.calculate_trend_score(
        d, _ind_cache=ctx.ind_cache_for(d), _ph_cache=ctx.ph_map
    )


def _build_peaks(scores, cutoff_date):
    """Non-overlap peak pruning: emit one _Sig per event per symbol."""
    from simulator import _FakePeak
    by_symbol = defaultdict(list)
    for (sym, d), score in scores.items():
        if d < cutoff_date:
            continue
        if score >= 70 or score <= 25:
            by_symbol[sym].append((d, score))

    peaks = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            peaks.append((sym, d, score))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)
    return peaks


def _split_sigs(peaks, sim):
    """Split peaks into call_sigs / put_sigs, recomputing TREND per peak."""
    call_sigs, put_sigs = [], []
    for sym, d, score in peaks:
        ctx = sim._contexts.get(sym)
        if ctx is None:
            continue
        tr = _trend_for_peak(ctx, d)
        if tr is None:
            tr = 50.0
        if score >= MC.OVERFLOW_THRESHOLD:
            call_sigs.append(_Sig(sym, d, float(score), float(tr)))
        elif score <= MC.PUT_THRESHOLD:
            put_sigs.append(_Sig(sym, d, float(score), float(tr)))
    # Sort matches monte_carlo.load_signals / load_put_signals order.
    call_sigs.sort(key=lambda s: (s.date, -s.overall))
    put_sigs.sort(key=lambda s: (s.date, s.overall))
    return call_sigs, put_sigs


# ---- Window runner (in-memory sig version of MC.run_window) -----------------

def _run_window(label, d_start, d_end, call_sigs_all, put_sigs_all, n_iter):
    call_sigs = [s for s in call_sigs_all if d_start <= s.date <= d_end]
    put_sigs  = [s for s in put_sigs_all  if d_start <= s.date <= d_end]

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph      = MC.load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = MC.load_breadth_map(d_start, d_end)
    regime_dates, regime_map   = MC.load_regime_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = MC.ct_tag(sig.overall, sig.trend, 'call')
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))

    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = MC.ct_tag(sig.overall, sig.trend, 'put')
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))

    call_outcomes = MC.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes  = MC.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)

    results = {}
    for mode in MC.COLLISION_MODES:
        finals, dds, ctps, ptps = [], [], [], []
        collapses = 0
        for it in range(n_iter):
            rng = random.Random(1000 * hash(label) + it)
            r = MC.run_single_sim(trading_days, calls_by_date, call_outcomes,
                                  puts_by_date, put_outcomes, mode, rng,
                                  regime_dates, regime_map)
            finals.append(r['final']); dds.append(r['max_dd'])
            ctps.append(r['call_tp']); ptps.append(r['put_tp'])
            if r['final'] <= MC.STARTING_CASH * MC.COLLAPSE_THRESHOLD:
                collapses += 1

        mean_ret = (statistics.mean(finals) / MC.STARTING_CASH - 1) * 100
        worst_dd = max(dds) * 100
        results[mode] = dict(
            mean_ret = mean_ret,
            worst_dd = worst_dd,
            p_coll   = collapses / n_iter * 100,
            call_tp  = statistics.mean(ctps),
            put_tp   = statistics.mean(ptps),
            call_n   = len(call_sigs),
            put_n    = len(put_sigs),
        )
    return results


# ---- Variant runner ---------------------------------------------------------

def _run_variant(label, fn, sim, cutoff_date, windows, n_iter):
    print("\n" + "=" * 110)
    print(f"  PHASE-B VARIANT: {label}")
    if fn is not None:
        print(f"    design={fn._design}  params={fn._params}")
    print("=" * 110)
    sys.stdout.flush()

    t0 = time.time()
    scoring_mod.compute_overall_score = _ORIGINAL if fn is None else fn
    scores = sim.simulate()
    t_sim = time.time() - t0

    peaks = _build_peaks(scores, cutoff_date)
    call_sigs, put_sigs = _split_sigs(peaks, sim)
    print(f"  [sim {t_sim:.1f}s  peaks={len(peaks):,}  calls={len(call_sigs):,}  puts={len(put_sigs):,}]")
    sys.stdout.flush()

    variant_results = {}
    for win_label, d_start, d_end in windows:
        t1 = time.time()
        r = _run_window(win_label, d_start, d_end, call_sigs, put_sigs, n_iter)
        t_win = time.time() - t1
        variant_results[win_label] = r

        print(f"\n  -- [{win_label}] {d_start} -> {d_end}  "
              f"(win {t_win:.1f}s  callN={r['conservative']['call_n']}  putN={r['conservative']['put_n']})")
        print(f"     {'Mode':<13}  {'CTP%':>5}  {'PTP%':>5}  {'MeanRet':>14}  {'WorstDD':>8}  {'P(col)':>7}")
        for mode in MC.COLLISION_MODES:
            m = r[mode]
            print(f"     {mode:<13}  {m['call_tp']:>4.1f}%  {m['put_tp']:>4.1f}%  "
                  f"{m['mean_ret']:>+13.1f}%  {m['worst_dd']:>7.1f}%  {m['p_coll']:>6.1f}%")
        sys.stdout.flush()

    return variant_results


def main(variants, windows=None, n_iter=250):
    from simulator import ScoreSimulator

    if windows is None:
        # Default Phase B: 2022 (bear), 2024 (bull), 22-now (continuous)
        today = date.today()
        windows = [
            ('2022',   date(2022, 1, 1), date(2022, 12, 31)),
            ('2024',   date(2024, 1, 1), date(2024, 12, 31)),
            ('22-now', date(2022, 1, 1), today),
        ]

    days = (date.today() - date(2022, 1, 1)).days
    sim  = ScoreSimulator(symbols=None, lookback_days=days, scoring_fn=None)
    cutoff_date = date.today() - timedelta(days=days)

    print("=" * 110)
    print("PHASE B - Canonical MC sweep (CT-design survivors)")
    print("=" * 110)
    print(f"Starting cash: ${MC.STARTING_CASH:,}  |  iterations per (var,win,mode): {n_iter}")
    print(f"Variants: {len(variants)}")
    print(f"Windows: {[w[0] for w in windows]}")
    print(f"Modes  : {MC.COLLISION_MODES}")
    print(f"Sim lookback: {days}d  cutoff={cutoff_date}")
    sys.stdout.flush()

    all_results = {}
    overall_t0 = time.time()
    for i, (label, fn) in enumerate(variants, 1):
        print(f"\n### [{i}/{len(variants)}] {label}  (elapsed {time.time()-overall_t0:.0f}s)")
        sys.stdout.flush()
        all_results[label] = _run_variant(label, fn, sim, cutoff_date, windows, n_iter)

    scoring_mod.compute_overall_score = _ORIGINAL

    # ---- Summary tables ------------------------------------------------------
    print("\n\n" + "=" * 110)
    print("PHASE B SUMMARY - Mean Return (Realistic)  |  22-now = primary metric")
    print("=" * 110)
    hdr = f"{'Variant':<32}  " + '  '.join(f"{w[0]:>14}" for w in windows)
    print(hdr)
    print('-' * len(hdr))
    for label, _ in variants:
        row = f"{label:<32}  "
        for win_label, _, _ in windows:
            r = all_results[label][win_label]['realistic']
            row += f"{r['mean_ret']:>+13.1f}%  "
        print(row)

    print("\n" + "=" * 110)
    print("PHASE B SUMMARY - Worst DD Conservative (must stay < 80%)")
    print("=" * 110)
    print(hdr)
    print('-' * len(hdr))
    for label, _ in variants:
        row = f"{label:<32}  "
        for win_label, _, _ in windows:
            r = all_results[label][win_label]['conservative']
            breach = '*' if r['worst_dd'] > 80.0 else ' '
            row += f"{r['worst_dd']:>13.1f}%{breach} "
        print(row)

    print(f"\n=== Phase B complete in {time.time()-overall_t0:.0f}s ===")
    return all_results


# ---- Default variant list ---------------------------------------------------
# These are the "day-1 candidates" grounded in Priority #8 brainstorm + Phase A
# expected leaders. Survivors from Phase A can be substituted in later.

DEFAULT_VARIANTS = [
    ('baseline_v19',                 None),

    # Design C — regime_cond (top Phase A performers; all clear full gate Wpc)
    ('C_regime_a100_rc100',          make_ct_score('regime_cond', alpha=1.00, regime_cutoff=1.00)),   # N=1059 WR15=81.3%
    ('C_regime_a050_rc100',          make_ct_score('regime_cond', alpha=0.50, regime_cutoff=1.00)),   # N=909 WR15=81.9%
    ('C_regime_a050_rc095',          make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.95)),   # N=900 WR15=81.6%
    ('C_regime_a050_rc090',          make_ct_score('regime_cond', alpha=0.50, regime_cutoff=0.90)),   # N=877 WR15=81.0%

    # Design B — floored (different family, cleanest gate pass)
    ('B_floored_fl050',              make_ct_score('floored', alpha=0.50, floor=0.50)),                # N=764 WR15=81.9%
    ('B_floored_fl060',              make_ct_score('floored', alpha=0.50, floor=0.60)),                # N=702 WR15=80.8%

    # Design A — uniform (borderline AL-CALL, kept as cross-family reference)
    ('A_uniform_a050',               make_ct_score('uniform', alpha=0.50)),                            # N=934 WR15=81.9% (AL-CALL +0.9)
    ('A_uniform_a025',               make_ct_score('uniform', alpha=0.25)),                            # N=755 WR15=81.5%

    # Design E — redistribute (top raw CT-PUT but AL failures; included as ceiling test)
    ('E_redist_cu020_rm030',         make_ct_score('redistribute', cutoff=0.20, redistrib_max=0.30)),  # N=1011 WR15=82.0% (AL-PUT -1.5)
]


if __name__ == '__main__':
    main(DEFAULT_VARIANTS)
