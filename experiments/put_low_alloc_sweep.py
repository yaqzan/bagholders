"""
Put-Low Tier Allocation Sweep (21-25 bucket)
=============================================
Hypothesis: v21 cumulative assessment shows the 21-25 put tier drags
    <25 WR30 = 69.2% (Ret30 0.25%)
    <20 WR30 = 70.8% (Ret30 0.41%)
Removing or shrinking the 21-25 allocation should lift per-trade EV.

Baseline put cascade:  <=15=15%  16-20=12%  21-25=12%
Variants tested:
  A_baseline : 21-25 @ 12%  (production v21)
  B_8pct     : 21-25 @  8%
  C_5pct     : 21-25 @  5%
  D_3pct     : 21-25 @  3%
  E_0pct     : 21-25 @  0%  (tier disabled)

All other parameters identical to monte_carlo.py canonical config.
"""

import sys, math, random, statistics, bisect
from collections import defaultdict
from datetime import date, timedelta

# Import all shared logic from monte_carlo (monte_carlo wraps stdout on import)
import monte_carlo as MC
from database.models.core import AlgorithmVersion

N_ITER = 300   # reduced from 500 for sweep speed; still statistically meaningful

WINDOWS = [
    ('2021', date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022', date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023', date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024', date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025', date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',   date(2021, 1, 1),  date(2026, 4, 15)),
]

VARIANTS = [
    ('A_baseline', 0.12),
    ('B_8pct',     0.08),
    ('C_5pct',     0.05),
    ('D_3pct',     0.03),
    ('E_0pct',     0.00),
]


def run_variant(variant_label, put_low_alloc, window_data, mode, n_iter):
    """Run N iterations for one variant on one window x mode. Patches PUT_TIER_ALLOC."""
    original_put_low = MC.PUT_TIER_ALLOC['put_low']
    MC.PUT_TIER_ALLOC['put_low'] = put_low_alloc

    try:
        (trading_days, calls_by_date, call_outcomes,
         puts_by_date, put_outcomes,
         regime_dates, regime_map) = window_data

        finals, dds, ctps, ptps, ctrd, ptrd = [], [], [], [], [], []
        collapses = 0

        for it in range(n_iter):
            rng = random.Random(1000 * hash(variant_label + mode) + it)
            r = MC.run_single_sim(trading_days, calls_by_date, call_outcomes,
                                  puts_by_date, put_outcomes, mode, rng,
                                  regime_dates, regime_map)
            finals.append(r['final']); dds.append(r['max_dd'])
            ctps.append(r['call_tp']); ptps.append(r['put_tp'])
            ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
            if r['final'] <= MC.STARTING_CASH * MC.COLLAPSE_THRESHOLD:
                collapses += 1

        return dict(
            mean_ret=(statistics.mean(finals) / MC.STARTING_CASH - 1) * 100,
            med_ret=(statistics.median(finals) / MC.STARTING_CASH - 1) * 100,
            mean_dd=statistics.mean(dds) * 100,
            worst_dd=max(dds) * 100,
            p_coll=collapses / n_iter * 100,
            call_tp=statistics.mean(ctps),
            put_tp=statistics.mean(ptps),
            call_trades=statistics.mean(ctrd),
            put_trades=statistics.mean(ptrd),
        )
    finally:
        MC.PUT_TIER_ALLOC['put_low'] = original_put_low


def load_window_data(label, d_start, d_end, version):
    """Precompute everything shared across variants for one window."""
    call_sigs = MC.load_signals(version, d_start, d_end)
    put_sigs  = MC.load_put_signals(version, d_start, d_end)
    sym_ids   = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph        = MC.load_price_history(sym_ids, d_start, d_end)
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
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall,
                                         (sig.symbol_id, sig.date)))
    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall,
                                        (sig.symbol_id, sig.date)))

    call_outcomes = MC.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes  = MC.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)

    # Count 21-25 specifically for reporting
    n_put_low = sum(1 for s in put_sigs if 21 <= s.overall <= 25)
    n_put_mid = sum(1 for s in put_sigs if 16 <= s.overall <= 20)
    n_put_top = sum(1 for s in put_sigs if s.overall <= 15)
    print(f"  Put signals: {len(put_sigs)}  (<=15: {n_put_top}, 16-20: {n_put_mid}, 21-25: {n_put_low})")
    print(f"  Call signals: {len(call_sigs)}  |  Outcomes: call={len(call_outcomes)} put={len(put_outcomes)}")

    return (trading_days, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, regime_dates, regime_map)


def main():
    print('=' * 110)
    print("PUT-LOW (21-25) ALLOCATION SWEEP")
    print('=' * 110)
    print(f"Baseline put cascade: <=15=15%  16-20=12%  21-25=12%")
    print(f"Variants:  " + ', '.join(f"{v[0]}={v[1]:.0%}" for v in VARIANTS))
    print(f"Iterations per (variant x window x mode): {N_ITER}")
    print(f"Windows: {', '.join(w[0] for w in WINDOWS)}")
    print(f"Modes:   {', '.join(MC.COLLISION_MODES)}")

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: v{version.id} ({version.git_commit})\n")

    # Load each window's shared data once
    window_cache = {}
    for label, d_start, d_end in WINDOWS:
        print(f"Loading {label} ({d_start} -> {d_end})...")
        window_cache[label] = load_window_data(label, d_start, d_end, version)

    # Run: {window: {variant: {mode: result}}}
    results = {}
    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*110}")
        print(f"WINDOW: {label}")
        print('='*110)
        results[label] = {}
        header = f"{'Variant':<14} {'Mode':<14} {'CTP%':>5} {'PTP%':>5} {'CTrd':>6} {'PTrd':>6} {'MeanRet':>14} {'MedRet':>14} {'WorstDD':>8} {'P(col)':>7}"
        print(header)
        print('-' * len(header))

        for variant_label, put_low_alloc in VARIANTS:
            results[label][variant_label] = {}
            for mode in MC.COLLISION_MODES:
                r = run_variant(variant_label, put_low_alloc,
                                window_cache[label], mode, N_ITER)
                results[label][variant_label][mode] = r
                print(f"{variant_label:<14} {mode:<14} {r['call_tp']:>4.1f}% {r['put_tp']:>4.1f}% "
                      f"{r['call_trades']:>5.1f} {r['put_trades']:>5.1f} "
                      f"{r['mean_ret']:>+13.1f}% {r['med_ret']:>+13.1f}% "
                      f"{r['worst_dd']:>7.1f}% {r['p_coll']:>6.1f}%")
            print()  # blank between variants

    # ---- Cross-window Realistic summary ----
    print('\n' + '=' * 110)
    print("REALISTIC MODE - Mean Return by Window x Variant")
    print('=' * 110)
    header = f"{'Window':<8}  " + '  '.join(f"{v[0]:>14}" for v in VARIANTS)
    print(header)
    print('-' * len(header))
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for variant_label, _ in VARIANTS:
            r = results[label][variant_label]['realistic']
            row += f"{r['mean_ret']:>+13.1f}%  "
        print(row)

    print('\n' + '=' * 110)
    print("REALISTIC MODE - Worst Drawdown by Window x Variant")
    print('=' * 110)
    print(header)
    print('-' * len(header))
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for variant_label, _ in VARIANTS:
            r = results[label][variant_label]['realistic']
            row += f"{r['worst_dd']:>13.1f}%  "
        print(row)

    print('\n' + '=' * 110)
    print("CONSERVATIVE MODE - Worst Drawdown (80% floor check)")
    print('=' * 110)
    print(header)
    print('-' * len(header))
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        for variant_label, _ in VARIANTS:
            r = results[label][variant_label]['conservative']
            marker = ' ' if r['worst_dd'] < 80.0 else '!'
            row += f"{r['worst_dd']:>12.1f}%{marker} "
        print(row)

    # ---- Delta vs baseline (Realistic) ----
    print('\n' + '=' * 110)
    print("DELTA vs A_baseline - Realistic mean return (pp of baseline return)")
    print('=' * 110)
    header2 = f"{'Window':<8}  " + '  '.join(f"{v[0]:>14}" for v in VARIANTS)
    print(header2)
    print('-' * len(header2))
    for label, _, _ in WINDOWS:
        row = f"{label:<8}  "
        base = results[label]['A_baseline']['realistic']['mean_ret']
        for variant_label, _ in VARIANTS:
            r = results[label][variant_label]['realistic']
            if variant_label == 'A_baseline':
                row += f"{'--':>14}  "
            else:
                if abs(base) > 1e-6:
                    ratio = (r['mean_ret'] - base) / abs(base) * 100
                    row += f"{ratio:>+13.1f}%  "
                else:
                    row += f"{r['mean_ret'] - base:>+13.1f}pp  "
        print(row)


if __name__ == '__main__':
    main()
