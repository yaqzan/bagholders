"""Joint scoring + portfolio sweep for continuation boost.

Runs autonomously: per-trade screening (instant) → MC validation cycles (slow)
with Bayesian-ish refinement. Decides PROCEED or NO-GO based on results.

Phases:
  A. Per-trade screening — load v4 sweep candidates from sweep_results.json
     (already 250-iter Bayesian run), pick top-K by util.
  B. MC screening at N=150 × 22-now per variant; rank by Δ compound vs baseline.
  C. Top-3 from B → MC at N=200 × {22-now, 5y, 2022, 2025} (4 windows).
  D. Top-1 from C → full N=300 × 8 windows (definitive ship gate).

Decision logic at each phase: continue if at least one variant beats baseline
on the headline metric AND no DD floor breach. Else stop and report null result.

Output: experiments/continuation_boost/joint_results_<timestamp>.json
       + final report stdout.
"""
from __future__ import annotations
import io, sys, os, json, time, hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import namedtuple
from dataclasses import dataclass, asdict

# Don't re-wrap sys.stdout — PYTHONIOENCODING=utf-8 is already set by env

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

CACHE_DIR     = ROOT / '.cache' / 'continuation_boost'
PEAKS_PQ      = CACHE_DIR / 'peaks.parquet'
PRIORS_PQ     = CACHE_DIR / 'priors_long.parquet'
V32_SCORES_PQ = CACHE_DIR / 'v32_scores_full.parquet'

OUT_DIR = Path(__file__).parent
TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')
RESULTS_JSON = OUT_DIR / f'joint_results_{TIMESTAMP}.json'
LOG_PATH     = OUT_DIR / f'joint_sweep_{TIMESTAMP}.log'

# Phase config
PHASE_A_TOP_K       = 8     # number of candidates from per-trade screening
PHASE_B_N_ITER      = 100   # MC iter for screening
PHASE_B_WINDOWS     = [('22-now', date(2022, 1, 1), date(2026, 4, 24))]
PHASE_C_TOP_K       = 3
PHASE_C_N_ITER      = 200
PHASE_C_WINDOWS     = [
    ('22-now', date(2022, 1, 1), date(2026, 4, 24)),
    ('5y',     date(2021, 1, 1), date(2026, 4, 15)),
    ('2022',   date(2022, 1, 1), date(2022, 12, 31)),
    ('2025',   date(2025, 1, 1), date(2025, 12, 31)),
]
PHASE_D_N_ITER      = 300
PHASE_D_WINDOWS     = None  # None = all 8

# Decision thresholds
COLLAPSE_FLOOR    = 0.0   # P(collapse) > 0% => DD breach concern
DD_C_RELATIVE_PP  = 2.5   # max allowed DD-C deterioration vs baseline (pp)
COMPOUND_FLOOR_RATIO = 0.95   # variant compound must be >= 0.95 × baseline (allow ≤5% loss)


# ─────────────────────────────────────────────────────────────────────────────
# Variant model — matches sweep.py
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Variant:
    LIFT_MAX:        float = 4.0
    TAU:             float = 25.0
    LBMIN:           int   = 7
    LBMAX:           int   = 60
    W15_RATIO:       float = 1.0
    MAG_EXP:         float = 1.0
    SAT_K:           float = 4.0
    WIN_NEG_W:       float = 0.0
    MFE_FLOOR:       float = 0.0
    REVERSAL_K_CALL: float = 0.5
    REVERSAL_K_PUT:  float = 1.5

    @classmethod
    def from_dict(cls, d):
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        # legacy fields
        if 'REVERSAL_K' in d and 'REVERSAL_K_CALL' not in d:
            kw['REVERSAL_K_CALL'] = float(d['REVERSAL_K'])
            kw['REVERSAL_K_PUT']  = float(d['REVERSAL_K'])
        return cls(**kw)


# ─────────────────────────────────────────────────────────────────────────────
# Phase A: load per-trade screening candidates
# ─────────────────────────────────────────────────────────────────────────────

def load_phase_a_candidates(top_k=PHASE_A_TOP_K) -> list:
    """From the v4 sweep_results.json, pick top-K by utility."""
    sweep_json = OUT_DIR / 'sweep_results.json'
    if not sweep_json.exists():
        raise SystemExit('Run sweep.py first to produce per-trade candidates')
    d = json.load(open(sweep_json))
    history = d['history']
    # Filter to non-trivial variants (avg_lift > 0.1pp)
    nontrivial = [r for r in history if r.get('avg_lift_pp', 0) > 0.1]
    sorted_h = sorted(nontrivial, key=lambda r: r['utility'], reverse=True)

    # Diversity: take top-K but avoid near-duplicates (LIFT_MAX within 0.5)
    seen_keys = set()
    candidates = []
    for r in sorted_h:
        v = r['variant']
        key = (round(v['LIFT_MAX']/2), round(v['TAU']/5),
               round(v['MAG_EXP']*2), round(v.get('REVERSAL_K_PUT', v.get('REVERSAL_K', 1.5))*2))
        if key in seen_keys: continue
        seen_keys.add(key)
        candidates.append({
            'variant': Variant.from_dict(v),
            'src_util': r['utility'],
            'src_avg_lift_pp': r['avg_lift_pp'],
            'src_iter': r['iter'],
        })
        if len(candidates) >= top_k: break
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# DB load: full v32 score set (cached)
# ─────────────────────────────────────────────────────────────────────────────

def load_v32_scores_full(refresh=False) -> pl.DataFrame:
    """Load all Score rows for active version that MC will see (overall>=70 OR <=40)."""
    if V32_SCORES_PQ.exists() and not refresh:
        df = pl.read_parquet(V32_SCORES_PQ)
        print(f'[load] v32 scores cache: {len(df):,} rows')
        return df

    print('[load] querying Score from MySQL (5y, calls 70+ + puts ≤40) ...')
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB
    av = AlgorithmVersion.get_active_scores_version()
    cutoff = (date.today() - timedelta(days=1825)).isoformat()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.trend,
               s.weight_info, s.volume_signal, s.regime_multiplier
        FROM scores s
        WHERE s.version_id = {av.id}
          AND s.date >= '{cutoff}'
          AND (s.overall >= 70 OR s.overall <= 40)
    """)
    rows = cur.fetchall()
    print(f'[load]   fetched {len(rows):,} rows')
    # MySQL returns datetime.date objects directly — no string parsing needed
    df = pl.DataFrame(
        rows,
        schema=['symbol', 'date', 'overall', 'trend',
                'weight_info', 'volume_signal', 'regime_multiplier'],
        orient='row',
    ).with_columns([
        pl.col('overall').cast(pl.Int64),
        pl.col('trend').cast(pl.Int64),
        pl.col('regime_multiplier').cast(pl.Float64, strict=False),
        # symbol_id == symbol string (Stock PK is symbol, FK column = symbol value)
        pl.col('symbol').alias('symbol_id'),
    ])
    CACHE_DIR.mkdir(exist_ok=True)
    df.write_parquet(V32_SCORES_PQ)
    print(f'[load]   cached to {V32_SCORES_PQ.name} ({len(df):,} rows)')
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Apply boost to scores DataFrame
# ─────────────────────────────────────────────────────────────────────────────

def compute_boost_for_peaks(peaks: pl.DataFrame, priors: pl.DataFrame, v: Variant) -> pl.DataFrame:
    """Returns peaks DataFrame with `new_overall` column = boosted score."""
    # Filter priors
    p = priors.filter(
        (pl.col('gap_days') >= v.LBMIN) & (pl.col('gap_days') <= v.LBMAX)
    )
    W7_WT  = 0.7
    W15_WT = v.W15_RATIO
    p = p.with_columns([
        (-pl.col('gap_days').cast(pl.Float64) / v.TAU).exp().alias('decay'),
        ((pl.col('prior_conviction').cast(pl.Float64) / 50.0).pow(v.MAG_EXP)).alias('magnitude'),
        pl.when((pl.col('gap_days') >= 15) & (pl.col('win_15d_30dgen') == 1))
          .then(W15_WT)
          .when((pl.col('gap_days') >= 15) & (pl.col('win_15d_30dgen') == 0))
          .then(-v.WIN_NEG_W * W15_WT)
          .when((pl.col('gap_days') >= 7)  & (pl.col('win_7d_30dgen')  == 1))
          .then(W7_WT)
          .when((pl.col('gap_days') >= 7)  & (pl.col('win_7d_30dgen')  == 0))
          .then(-v.WIN_NEG_W * W7_WT)
          .otherwise(0.0)
          .alias('win_quality'),
    ])
    p = p.with_columns(
        (pl.col('decay') * pl.col('magnitude') * pl.col('win_quality')).alias('contribution')
    )
    peak_signals = p.group_by(['symbol', 'date']).agg([
        pl.col('contribution').sum().alias('prior_signal'),
        pl.col('prior_close').sort_by(pl.col('gap_days'), descending=True).first()
          .alias('oldest_prior_close'),
        pl.col('prior_mfe_anchor').sort_by(pl.col('gap_days'), descending=True).first()
          .alias('oldest_prior_mfe'),
    ])
    pk = peaks.join(peak_signals, on=['symbol', 'date'], how='left').with_columns([
        pl.col('prior_signal').fill_null(0.0),
    ])
    pk = pk.with_columns([
        pl.when(pl.col('oldest_prior_close').is_not_null() & (pl.col('oldest_prior_close') > 0))
          .then(
              pl.when(pl.col('side') == 'call')
                .then((pl.col('current_close') - pl.col('oldest_prior_close'))
                      / pl.col('oldest_prior_close') * 100)
                .otherwise((pl.col('oldest_prior_close') - pl.col('current_close'))
                           / pl.col('oldest_prior_close') * 100)
          )
          .otherwise(None)
          .alias('realized_pct'),
    ])
    pk = pk.with_columns([
        pl.when(pl.col('oldest_prior_mfe').is_not_null() & (pl.col('oldest_prior_mfe') > 0)
                & pl.col('realized_pct').is_not_null())
          .then((pl.col('realized_pct') / pl.col('oldest_prior_mfe')).clip(-2.0, 2.0))
          .otherwise(0.0)
          .alias('realized_ratio'),
    ])
    pk = pk.with_columns([
        pl.when(pl.col('side') == 'put').then(v.REVERSAL_K_PUT).otherwise(v.REVERSAL_K_CALL)
          .alias('reversal_k'),
    ])
    pk = pk.with_columns([
        pl.when(pl.col('realized_ratio') >= 0)
          .then(1.0 - (1.0 - v.MFE_FLOOR) * pl.col('realized_ratio').clip(0.0, 1.0))
          .otherwise(1.0 - pl.col('reversal_k') * (-pl.col('realized_ratio')).clip(0.0, 1.0))
          .clip(0.0, 1.0)
          .alias('mfe_room'),
    ])
    pk = pk.with_columns([
        (pl.col('prior_signal') / v.SAT_K).tanh().alias('sat_signal'),
        pl.when(pl.col('side') == 'call').then(1.0).otherwise(-1.0).alias('side_sign'),
    ])
    pk = pk.with_columns([
        (v.LIFT_MAX * pl.col('sat_signal') * pl.col('mfe_room')).alias('lift_mag'),
    ])
    pk = pk.with_columns([
        (pl.col('overall').cast(pl.Float64) + pl.col('side_sign') * pl.col('lift_mag'))
        .round().clip(0, 100).cast(pl.Int64)
        .alias('new_overall'),
    ])
    return pk.select(['symbol', 'date', 'new_overall'])


def apply_boost_to_scores(scores_full: pl.DataFrame, peaks: pl.DataFrame,
                           priors: pl.DataFrame, v: Variant) -> pl.DataFrame:
    """Return scores_full with `overall` column updated where the boost applies."""
    if v.LIFT_MAX < 1e-6:
        # Baseline — no modification
        return scores_full

    boost = compute_boost_for_peaks(peaks, priors, v)
    out = scores_full.join(boost, on=['symbol', 'date'], how='left').with_columns([
        pl.coalesce(['new_overall', 'overall']).cast(pl.Int64).alias('overall_new')
    ]).drop('overall').rename({'overall_new': 'overall', 'new_overall': '_unused'}).drop('_unused')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# MC runner: build SimScoreRow list and inject
# ─────────────────────────────────────────────────────────────────────────────

def build_sim_rows(scores_df: pl.DataFrame):
    """Convert scores DataFrame → list of SimScoreRow."""
    from experiments.sim_mc_bridge import SimScoreRow
    rows = []
    for r in scores_df.iter_rows(named=True):
        rows.append(SimScoreRow(
            symbol=r['symbol'],
            symbol_id=r['symbol_id'],   # str (Stock PK = symbol)
            date=r['date'],
            overall=int(r['overall']),
            trend=int(r['trend']) if r['trend'] is not None else 50,
            weight_info=r['weight_info'] or '{}',
            volume_signal=r['volume_signal'] or 'NEUTRAL',
            regime_multiplier=float(r['regime_multiplier']) if r['regime_multiplier'] is not None else 1.0,
        ))
    rows.sort(key=lambda r: (r.date, -r.overall))
    return rows


def run_mc_for_variant(scores_full: pl.DataFrame, peaks, priors,
                        v: Variant, windows: list, n_iter: int) -> dict:
    """Apply boost and run MC across the given windows. Returns per-window dict."""
    import monte_carlo as mc
    from experiments.sim_mc_bridge import inject_into_mc

    # Apply boost
    t0 = time.time()
    modified = apply_boost_to_scores(scores_full, peaks, priors, v)
    rows = build_sim_rows(modified)
    print(f'  [boost+rows]: {time.time()-t0:.1f}s ({len(rows):,} signals)')

    # MC
    mc.N_ITER = n_iter
    os.environ['MC_NO_MP'] = '1'
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()

    results = {}
    with inject_into_mc(rows):
        for label, d_start, d_end in windows:
            t0 = time.time()
            res = mc.run_window(label, d_start, d_end, av)
            r = res['seeded']
            results[label] = {
                'mean_ret': r['mean_ret'],
                'med_ret':  r['med_ret'],
                'mean_dd':  r['mean_dd'],
                'worst_dd': r['worst_dd'],
                'p_coll':   r['p_coll'],
                'call_tp':  r['call_tp'],
                'put_tp':   r['put_tp'],
                'call_trades': r['call_trades'],
                'put_trades':  r['put_trades'],
                'wall_s': time.time() - t0,
            }
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Decision logic
# ─────────────────────────────────────────────────────────────────────────────

def beats_baseline(variant_res: dict, baseline_res: dict, label: str) -> bool:
    """Heuristic: variant beats baseline on this window if compound is higher
    AND DD-C is not >2.5pp worse AND no collapse."""
    v = variant_res[label]
    b = baseline_res[label]
    if v['p_coll'] > 0:
        return False
    if v['worst_dd'] > b['worst_dd'] + DD_C_RELATIVE_PP:
        return False
    return v['mean_ret'] > b['mean_ret']


def report_window(label: str, v: dict, b: dict | None = None, prefix: str = '  '):
    if b is None:
        print(f'{prefix}{label:<8}  meanRet={v["mean_ret"]:>+18,.1f}%  '
              f'WorstDD={v["worst_dd"]:>5.1f}%  P(coll)={v["p_coll"]:>4.1f}%  '
              f'C/P TP=({v["call_tp"]:.1f}/{v["put_tp"]:.1f})  '
              f'C/P trades=({v["call_trades"]}/{v["put_trades"]})')
    else:
        d_ret = (v['mean_ret'] - b['mean_ret']) / abs(b['mean_ret'] or 1) * 100 if b['mean_ret'] else 0
        d_dd = v['worst_dd'] - b['worst_dd']
        marker = '✓' if beats_baseline({label: v}, {label: b}, label) else '✗'
        print(f'{prefix}{label:<8} {marker} meanRet={v["mean_ret"]:>+18,.1f}% (Δ={d_ret:+6.1f}%)  '
              f'WorstDD={v["worst_dd"]:>5.1f}% (Δ{d_dd:+5.1f}pp)  '
              f'P(coll)={v["p_coll"]:>4.1f}%  '
              f'C/P TP=({v["call_tp"]:.1f}/{v["put_tp"]:.1f})')


# ─────────────────────────────────────────────────────────────────────────────
# Phase orchestration
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log_f = open(LOG_PATH, 'w', encoding='utf-8')
    class Tee:
        def __init__(self, *streams):
            self.streams = streams
            # Mimic stdout.buffer so monte_carlo's TextIOWrapper rewrap doesn't crash
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

    overall_t0 = time.time()
    print(f'╔══════════════════════════════════════════════════════════════════╗')
    print(f'║ JOINT scoring+portfolio sweep   ({TIMESTAMP})                     ║')
    print(f'╚══════════════════════════════════════════════════════════════════╝')

    # Load shared data
    print('\n=== LOADING DATA ===')
    peaks = pl.read_parquet(PEAKS_PQ)
    priors = pl.read_parquet(PRIORS_PQ)
    print(f'  peaks: {len(peaks):,}  priors: {len(priors):,}')
    scores_full = load_v32_scores_full()
    print(f'  scores: {len(scores_full):,}')

    # Phase A: per-trade candidates
    print('\n=== PHASE A: per-trade screening candidates ===')
    candidates = load_phase_a_candidates(top_k=PHASE_A_TOP_K)
    for i, c in enumerate(candidates):
        v = c['variant']
        print(f'  #{i:02d}  src_util={c["src_util"]:+.2f}  '
              f'L={v.LIFT_MAX:.2f} T={v.TAU:.1f} K={v.SAT_K:.2f} '
              f'W15={v.W15_RATIO:.2f} M={v.MAG_EXP:.2f} '
              f'WN={v.WIN_NEG_W:.2f} F={v.MFE_FLOOR:.2f} '
              f'RKc={v.REVERSAL_K_CALL:.2f} RKp={v.REVERSAL_K_PUT:.2f}')

    # MC baseline
    print(f'\n=== MC BASELINE (no boost) — N={PHASE_B_N_ITER}, {len(PHASE_B_WINDOWS)} window(s) ===')
    baseline_res_b = run_mc_for_variant(
        scores_full, peaks, priors, Variant(LIFT_MAX=0.0),
        windows=PHASE_B_WINDOWS, n_iter=PHASE_B_N_ITER)
    for w_lbl, _, _ in PHASE_B_WINDOWS:
        report_window(w_lbl, baseline_res_b[w_lbl], None, prefix='  baseline ')

    # Phase B: MC each candidate
    print(f'\n=== PHASE B: MC screening (N={PHASE_B_N_ITER} × {len(PHASE_B_WINDOWS)} windows × {len(candidates)} variants) ===')
    phase_b_results = []
    for i, c in enumerate(candidates):
        v = c['variant']
        print(f'\n  ── Variant B-#{i:02d} ──')
        try:
            res = run_mc_for_variant(scores_full, peaks, priors, v,
                                       windows=PHASE_B_WINDOWS, n_iter=PHASE_B_N_ITER)
            for w_lbl, _, _ in PHASE_B_WINDOWS:
                report_window(w_lbl, res[w_lbl], baseline_res_b[w_lbl], prefix='  ')
            phase_b_results.append({
                'idx': i, 'variant': asdict(v),
                'src_util': c['src_util'],
                'mc': res,
            })
        except Exception as e:
            print(f'  ERROR: {e}')
            import traceback; traceback.print_exc()

    # Decision: any variant beats baseline on 22-now?
    primary_label = '22-now'
    survivors_b = [r for r in phase_b_results
                   if beats_baseline(r['mc'], baseline_res_b, primary_label)]
    print(f'\n  Phase B survivors: {len(survivors_b)}/{len(phase_b_results)}')
    if not survivors_b:
        print('  ╳ NO VARIANTS BEAT BASELINE on 22-now N=100. Stopping.')
        decision = 'NO-GO @ Phase B'
        _save_results(decision, baseline_res_b, phase_b_results, [], [], None)
        return

    # Phase C: top-K from B → multi-window MC
    survivors_b.sort(key=lambda r: r['mc'][primary_label]['mean_ret'], reverse=True)
    phase_c_candidates = survivors_b[:PHASE_C_TOP_K]
    print(f'\n=== PHASE C: top-{len(phase_c_candidates)} → MC (N={PHASE_C_N_ITER} × {len(PHASE_C_WINDOWS)} windows) ===')

    # MC baseline at C iter level (different N → different RNG noise)
    print(f'\n  MC BASELINE (no boost) at N={PHASE_C_N_ITER}')
    baseline_res_c = run_mc_for_variant(
        scores_full, peaks, priors, Variant(LIFT_MAX=0.0),
        windows=PHASE_C_WINDOWS, n_iter=PHASE_C_N_ITER)
    for w_lbl, _, _ in PHASE_C_WINDOWS:
        report_window(w_lbl, baseline_res_c[w_lbl], None, prefix='  baseline ')

    phase_c_results = []
    for i, c in enumerate(phase_c_candidates):
        v = Variant(**c['variant'])
        print(f'\n  ── Variant C-#{i} (was B-#{c["idx"]}) ──')
        try:
            res = run_mc_for_variant(scores_full, peaks, priors, v,
                                       windows=PHASE_C_WINDOWS, n_iter=PHASE_C_N_ITER)
            for w_lbl, _, _ in PHASE_C_WINDOWS:
                report_window(w_lbl, res[w_lbl], baseline_res_c[w_lbl], prefix='  ')
            phase_c_results.append({
                'idx': c['idx'], 'variant': c['variant'],
                'mc': res,
            })
        except Exception as e:
            print(f'  ERROR: {e}')

    # Decision: variant must beat baseline on BOTH 22-now AND 5y, no annual regression > 25%
    survivors_c = []
    for r in phase_c_results:
        ok = True
        for w_lbl in ['22-now', '5y']:
            if not beats_baseline(r['mc'], baseline_res_c, w_lbl):
                ok = False; break
        # Annual regression check
        for w_lbl in ['2022', '2025']:
            v_r = r['mc'][w_lbl]['mean_ret']
            b_r = baseline_res_c[w_lbl]['mean_ret']
            if b_r != 0 and (v_r - b_r) / abs(b_r) < -0.25:
                ok = False; break
        if ok: survivors_c.append(r)

    print(f'\n  Phase C survivors: {len(survivors_c)}/{len(phase_c_results)}')
    if not survivors_c:
        print('  ╳ NO VARIANTS PASS multi-window gate. Stopping.')
        decision = 'NO-GO @ Phase C'
        _save_results(decision, baseline_res_c, phase_b_results, phase_c_results, [], None)
        return

    # Phase D: top-1 → full validation
    survivors_c.sort(key=lambda r: r['mc']['5y']['mean_ret'], reverse=True)
    final_candidate = survivors_c[0]
    print(f'\n=== PHASE D: final candidate → full N={PHASE_D_N_ITER} × 8 windows ===')
    v = Variant(**final_candidate['variant'])
    print(f'  Variant: {asdict(v)}')

    import monte_carlo as mc
    full_windows = mc.WINDOWS

    print(f'\n  MC BASELINE at N={PHASE_D_N_ITER}, 8 windows')
    baseline_res_d = run_mc_for_variant(
        scores_full, peaks, priors, Variant(LIFT_MAX=0.0),
        windows=full_windows, n_iter=PHASE_D_N_ITER)
    for w_lbl, _, _ in full_windows:
        report_window(w_lbl, baseline_res_d[w_lbl], None, prefix='  baseline ')

    print(f'\n  Variant MC')
    variant_res_d = run_mc_for_variant(
        scores_full, peaks, priors, v,
        windows=full_windows, n_iter=PHASE_D_N_ITER)
    for w_lbl, _, _ in full_windows:
        report_window(w_lbl, variant_res_d[w_lbl], baseline_res_d[w_lbl], prefix='  ')

    # Final P-gate evaluation
    p_pass = {'P3_5y': False, 'P3_22now': False, 'P4_annual': True, 'P5_collapse': True, 'P6_dd': True}
    for w_lbl, _, _ in full_windows:
        v_r = variant_res_d[w_lbl]
        b_r = baseline_res_d[w_lbl]
        if w_lbl == '5y':  p_pass['P3_5y']    = v_r['mean_ret'] >= b_r['mean_ret']
        if w_lbl == '22-now': p_pass['P3_22now'] = v_r['mean_ret'] >= b_r['mean_ret']
        if b_r['mean_ret'] != 0:
            d = (v_r['mean_ret'] - b_r['mean_ret']) / abs(b_r['mean_ret'])
            if d < -0.25: p_pass['P4_annual'] = False
        if v_r['p_coll'] > 0: p_pass['P5_collapse'] = False
        if v_r['worst_dd'] > b_r['worst_dd'] + DD_C_RELATIVE_PP:
            p_pass['P6_dd'] = False

    print(f'\n=== PORTFOLIO GATE RESULTS ===')
    for k, p in p_pass.items():
        print(f'  {"✓" if p else "✗"} {k}')

    if all(p_pass.values()):
        decision = 'PROCEED — variant passes all P-gates at N=300 × 8 windows'
    else:
        failed = [k for k, p in p_pass.items() if not p]
        decision = f'NO-GO @ Phase D — failed gates: {failed}'

    print(f'\n{"="*70}\nFINAL DECISION: {decision}\n{"="*70}')
    print(f'Total wall time: {(time.time()-overall_t0)/60:.1f} min')

    _save_results(decision, baseline_res_d, phase_b_results, phase_c_results,
                  variant_res_d, asdict(v), p_pass=p_pass, baseline_b=baseline_res_b,
                  baseline_c=baseline_res_c)


def _save_results(decision, baseline_d, phase_b, phase_c, variant_d, variant_dict,
                   p_pass=None, baseline_b=None, baseline_c=None):
    out = {
        'timestamp': TIMESTAMP,
        'decision': decision,
        'baseline_b': baseline_b,
        'phase_b_results': phase_b,
        'baseline_c': baseline_c,
        'phase_c_results': phase_c,
        'baseline_d': baseline_d,
        'final_variant': variant_dict,
        'final_variant_mc': variant_d,
        'p_gate': p_pass,
    }
    with open(RESULTS_JSON, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'\n[done] wrote {RESULTS_JSON}')


if __name__ == '__main__':
    main()
