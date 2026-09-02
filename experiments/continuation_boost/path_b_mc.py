"""Path B → MC validation.

Take the Q1-passing Path B variant, convert routing decisions to score
modifications (boost sub-threshold peaks to the lowest score in their
routed tier), inject into MC, run baseline vs variant on 22-now and 5y
at N=300.

If both pass (variant ≥ baseline on both compound + DD-relative ≤ +2.5pp),
report PROCEED. Else NO-GO.
"""
from __future__ import annotations
import io, sys, os, json, time
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, asdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CACHE_DIR = ROOT / '.cache' / 'continuation_boost'
PEAKS_PQ_V2     = CACHE_DIR / 'peaks_v2.parquet'
PRIORS_PQ_V2    = CACHE_DIR / 'priors_v2.parquet'
V32_SCORES_PQ   = CACHE_DIR / 'v32_scores_full.parquet'

OUT_DIR = Path(__file__).parent
TIMESTAMP = time.strftime('%Y%m%d_%H%M%S')
RESULTS_JSON = OUT_DIR / f'path_b_mc_{TIMESTAMP}.json'
LOG_PATH = OUT_DIR / f'path_b_mc_{TIMESTAMP}.log'

# Q1-passing variant
QPASS = {
    'TAU': 40.0,
    'MAG_EXP': 0.70,
    'SIG_NORM': 3.0,
    'SIG_MIN': 0.20,
}

CALL_TIERS = [95, 90, 85, 80, 75, 70]
SUB_THRESHOLD_BOUND = 75
WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
]
N_ITER = 300


def aggregate_prior_signal(peaks: pl.DataFrame, priors: pl.DataFrame) -> pl.DataFrame:
    p = priors.with_columns([
        (-pl.col('gap_days').cast(pl.Float64) / QPASS['TAU']).exp().alias('decay'),
        ((pl.col('prior_conviction').cast(pl.Float64) / 50.0).pow(QPASS['MAG_EXP'])).alias('magnitude'),
    ])
    p = p.with_columns(
        (pl.col('decay') * pl.col('magnitude') * pl.col('sig')).alias('contribution')
    )
    agg = p.group_by(['symbol', 'date']).agg(
        pl.col('contribution').sum().alias('prior_signal_raw')
    )
    agg = agg.with_columns(
        (pl.col('prior_signal_raw') / QPASS['SIG_NORM']).tanh().alias('prior_signal')
    )
    return peaks.join(agg, on=['symbol', 'date'], how='left').with_columns(
        pl.col('prior_signal').fill_null(0.0)
    )


def measure_baseline(peaks: pl.DataFrame) -> dict:
    out = {}
    for t in CALL_TIERS:
        sub = peaks.filter(pl.col('overall') >= t)
        wins = sub.filter(pl.col('outcome_tp_15d') == 1).height
        losses = sub.filter(pl.col('outcome_tp_15d') == 0).height
        out[f'{t}+'] = {'tp_pct': 100*wins/(wins+losses) if (wins+losses) else None}
    return out


def measure_strength_cohorts(peaks_with_signal: pl.DataFrame) -> dict:
    sub = peaks_with_signal.filter(
        (pl.col('overall') < SUB_THRESHOLD_BOUND) &
        (pl.col('prior_signal') >= QPASS['SIG_MIN'])
    )
    out = {}
    for lo in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        cohort = sub.filter((pl.col('prior_signal') >= lo) & (pl.col('prior_signal') < lo + 0.1))
        wins = cohort.filter(pl.col('outcome_tp_15d') == 1).height
        losses = cohort.filter(pl.col('outcome_tp_15d') == 0).height
        n = wins + losses
        out[lo] = {'N': cohort.height, 'tp_pct': 100*wins/n if n else None}
    return out


def route_to_tier(strength: float, cohorts: dict, baseline: dict) -> str | None:
    """Match strength to highest tier whose baseline TP% the cohort meets/exceeds."""
    if strength is None or strength < QPASS['SIG_MIN']:
        return None
    for lo in [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]:
        if strength >= lo:
            cohort = cohorts.get(lo, {})
            cohort_tp = cohort.get('tp_pct')
            if cohort_tp is None or cohort['N'] < 30:
                return '75+'  # default route
            best_tier = '75+'
            best_pct = 75
            for t_str, b in baseline.items():
                tier_tp = b.get('tp_pct')
                if tier_tp is None: continue
                if cohort_tp >= tier_tp - 1.0:
                    pct = int(t_str.rstrip('+'))
                    if pct > best_pct:
                        best_pct = pct
                        best_tier = t_str
            return best_tier
    return None


def compute_score_modifications(peaks_v2: pl.DataFrame, priors_v2: pl.DataFrame) -> dict:
    """Returns dict {(symbol, date): new_overall} for promoted peaks."""
    pk = aggregate_prior_signal(peaks_v2, priors_v2)
    baseline = measure_baseline(pk)
    cohorts = measure_strength_cohorts(pk)

    mods = {}
    for r in pk.iter_rows(named=True):
        if r['overall'] >= SUB_THRESHOLD_BOUND:
            continue   # not a promotion candidate
        target_tier = route_to_tier(r['prior_signal'], cohorts, baseline)
        if target_tier is None:
            continue
        target_min_score = int(target_tier.rstrip('+'))
        if target_min_score > r['overall']:
            mods[(r['symbol'], r['date'])] = target_min_score
    return mods


def load_v32_scores_full() -> pl.DataFrame:
    df = pl.read_parquet(V32_SCORES_PQ)
    return df


def apply_modifications(scores_full: pl.DataFrame, mods: dict) -> pl.DataFrame:
    """Apply score modifications to the full v32 scores DataFrame."""
    if not mods:
        return scores_full
    mod_df = pl.DataFrame({
        'symbol': [k[0] for k in mods.keys()],
        'date':   [k[1] for k in mods.keys()],
        'new_overall': list(mods.values()),
    })
    out = scores_full.join(mod_df, on=['symbol', 'date'], how='left')
    out = out.with_columns(
        pl.coalesce(['new_overall', 'overall']).cast(pl.Int64).alias('overall_eff')
    ).drop(['overall', 'new_overall']).rename({'overall_eff': 'overall'})
    return out


def build_sim_rows(scores: pl.DataFrame):
    from experiments.sim_mc_bridge import SimScoreRow
    rows = []
    for r in scores.iter_rows(named=True):
        rows.append(SimScoreRow(
            symbol=r['symbol'],
            symbol_id=r['symbol_id'],
            date=r['date'],
            overall=int(r['overall']),
            trend=int(r['trend']) if r['trend'] is not None else 50,
            weight_info=r['weight_info'] or '{}',
            volume_signal=r['volume_signal'] or 'NEUTRAL',
            regime_multiplier=float(r['regime_multiplier']) if r['regime_multiplier'] is not None else 1.0,
        ))
    rows.sort(key=lambda r: (r.date, -r.overall))
    return rows


def run_mc(scores: pl.DataFrame, label: str, n_iter: int = N_ITER) -> dict:
    import monte_carlo as mc
    from experiments.sim_mc_bridge import inject_into_mc
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()

    mc.N_ITER = n_iter
    os.environ['MC_NO_MP'] = '1'

    rows = build_sim_rows(scores)
    print(f'  [{label}] built {len(rows):,} sim rows', flush=True)

    results = {}
    with inject_into_mc(rows):
        for w_label, d_start, d_end in WINDOWS:
            t0 = time.time()
            res = mc.run_window(w_label, d_start, d_end, av)
            r = res['seeded']
            results[w_label] = {
                'mean_ret': r['mean_ret'],
                'med_ret':  r['med_ret'],
                'worst_dd': r['worst_dd'],
                'p_coll':   r['p_coll'],
                'call_tp':  r['call_tp'],
                'put_tp':   r['put_tp'],
                'call_trades': r['call_trades'],
                'put_trades':  r['put_trades'],
                'wall_s':   time.time() - t0,
            }
    return results


def main():
    log_f = open(LOG_PATH, 'w', encoding='utf-8')
    class Tee:
        def __init__(self, *streams):
            self.streams = streams
            self.buffer = streams[0].buffer if hasattr(streams[0], 'buffer') else streams[0]
            self.encoding = 'utf-8'
            self.errors = 'replace'
        def write(self, s):
            for x in self.streams:
                try: x.write(s)
                except Exception: pass
        def flush(self):
            for x in self.streams:
                try: x.flush()
                except Exception: pass
        def isatty(self): return False
        def fileno(self):
            return self.streams[0].fileno() if hasattr(self.streams[0], 'fileno') else -1
    sys.stdout = Tee(sys.stdout, log_f)

    print(f'Path B → MC validation  ({date.today().isoformat()})')
    print(f'  Variant: {QPASS}')
    print(f'  N_ITER: {N_ITER}')
    print(f'  Windows: {[w[0] for w in WINDOWS]}')

    # Load features and compute modifications
    print(f'\n[load] features ...')
    peaks_v2 = pl.read_parquet(PEAKS_PQ_V2)
    priors_v2 = pl.read_parquet(PRIORS_PQ_V2)
    print(f'  peaks: {len(peaks_v2):,}  priors: {len(priors_v2):,}')

    print(f'\n[compute] score modifications ...')
    mods = compute_score_modifications(peaks_v2, priors_v2)
    print(f'  {len(mods):,} peaks modified')
    if mods:
        # Show distribution of modifications
        deltas = defaultdict(int)
        for (sym, d), new_ov in mods.items():
            old_ov = peaks_v2.filter((pl.col('symbol')==sym) & (pl.col('date')==d))['overall'][0]
            delta = new_ov - old_ov
            deltas[delta] += 1
        print(f'  modification distribution (lift):')
        for d in sorted(deltas):
            print(f'    +{d} pts: {deltas[d]:,}')

    # Load full scores
    print(f'\n[load] v32 full scores ...')
    scores_full = load_v32_scores_full()
    print(f'  {len(scores_full):,} score rows')

    # Apply modifications
    print(f'\n[apply] score modifications to full DataFrame ...')
    scores_modified = apply_modifications(scores_full, mods)
    print(f'  {len(scores_modified):,} rows after apply')

    # Verify modifications applied
    n_actually_modified = 0
    if mods:
        # Sample check
        for (sym, d), new_ov in list(mods.items())[:5]:
            row = scores_modified.filter((pl.col('symbol')==sym) & (pl.col('date')==d))
            if row.height > 0 and row['overall'][0] == new_ov:
                n_actually_modified += 1
        print(f'  spot-check: {n_actually_modified}/5 sample mods verified in modified scores')

    # Baseline MC
    print(f'\n=== BASELINE MC (no modifications) ===')
    baseline_mc = run_mc(scores_full, 'baseline', N_ITER)
    for w_label, r in baseline_mc.items():
        print(f'  {w_label:<8}  meanRet={r["mean_ret"]:>+18,.1f}%  '
              f'WorstDD={r["worst_dd"]:>5.1f}%  P(coll)={r["p_coll"]:>4.1f}%  '
              f'C/P TP=({r["call_tp"]:.1f}/{r["put_tp"]:.1f})  '
              f'C/P trades=({r["call_trades"]:.0f}/{r["put_trades"]:.0f})')

    # Variant MC
    print(f'\n=== VARIANT MC (Path B routed) ===')
    variant_mc = run_mc(scores_modified, 'variant', N_ITER)
    for w_label, r in variant_mc.items():
        b = baseline_mc[w_label]
        d_ret = (r['mean_ret'] - b['mean_ret']) / abs(b['mean_ret'] or 1) * 100 if b['mean_ret'] else 0
        d_dd = r['worst_dd'] - b['worst_dd']
        marker = '✓' if (r['mean_ret'] >= b['mean_ret'] and d_dd <= 2.5 and r['p_coll'] == 0) else '✗'
        print(f'  {w_label:<8} {marker} meanRet={r["mean_ret"]:>+18,.1f}% (Δ={d_ret:+6.1f}%)  '
              f'WorstDD={r["worst_dd"]:>5.1f}% (Δ{d_dd:+5.1f}pp)  '
              f'P(coll)={r["p_coll"]:>4.1f}%  '
              f'C/P TP=({r["call_tp"]:.1f}/{r["put_tp"]:.1f})')

    # Decision
    pass_22now = (variant_mc['22-now']['mean_ret'] >= baseline_mc['22-now']['mean_ret'] and
                  variant_mc['22-now']['worst_dd'] - baseline_mc['22-now']['worst_dd'] <= 2.5 and
                  variant_mc['22-now']['p_coll'] == 0)
    pass_5y = (variant_mc['5y']['mean_ret'] >= baseline_mc['5y']['mean_ret'] and
               variant_mc['5y']['worst_dd'] - baseline_mc['5y']['worst_dd'] <= 2.5 and
               variant_mc['5y']['p_coll'] == 0)

    print(f'\n{"="*70}')
    if pass_22now and pass_5y:
        decision = 'PROCEED — Path B variant passes 22-now AND 5y MC gates'
    elif pass_22now or pass_5y:
        decision = f'BORDERLINE — passes only {"22-now" if pass_22now else "5y"}'
    else:
        decision = 'NO-GO — Path B variant fails MC validation'
    print(f'FINAL DECISION: {decision}')
    print(f'{"="*70}')

    out = {
        'timestamp': TIMESTAMP,
        'variant': QPASS,
        'n_modified': len(mods),
        'baseline_mc': baseline_mc,
        'variant_mc': variant_mc,
        'decision': decision,
    }
    with open(RESULTS_JSON, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'[done] wrote {RESULTS_JSON}')


if __name__ == '__main__':
    main()
