"""Phase F — Marginal contribution / ablation study.

Test variants in increasing complexity:
  V1. RSU only — relative strength U-curve, calls + puts
  V2. SWPM_SEC only with EMA50+RSI (no MACD)
  V3. SWPM_SEC only with MACD added
  V4. RSU + V2  (stacked, no MACD)
  V5. RSU + V3  (stacked, full — Phase E winner family)

For each, run a focused LHS+drill (240 evals) and report:
  - util at 5y
  - per-tier WR7 deltas
  - per-tier WR7 sign-consistency across years (W3 check)

Goal: identify the SIMPLEST architecture that delivers the bulk of the alpha,
to minimize overfitting risk before recommending a ship candidate.
"""
from __future__ import annotations
import io, sys, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm_v2 import (
    SWPMv2Params, evaluate_v2, apply_swpm_v2
)

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH_PATH = LOCAL / 'cohort_v46_1825.parquet'
OUT = ROOT / 'experiments' / 'sector_etf_alpha'

FULL_SPACE = {
    'EMA_K':           (3.0, 12.0),
    'RSI_K':           (15.0, 35.0),
    'MACD_TANH_K':     (0.2, 1.5),
    'EMA_W':           (0.0, 1.0),
    'RSI_W':           (0.0, 1.0),
    'MACD_W':          (0.0, 1.0),
    'PHASE_POWER':     (1.0, 3.0),
    'SCORE_POWER':     (1.0, 2.5),
    'ALPHA_DOWN':      (0.20, 0.95),
    'TARGET_DOWN':     (55.0, 70.0),
    'ALPHA_UP':        (0.20, 0.95),
    'TARGET_UP':       (80.0, 92.0),
    'ALPHA_PUT_DOWN':  (0.20, 0.95),
    'TARGET_PUT':      (28.0, 38.0),
    'ALPHA_PUT_UP':    (0.0, 0.5),
    'TARGET_PUT_UP':   (8.0, 16.0),
    'RS_GATE':         (0.02, 0.08),
    'RS_K':            (0.04, 0.20),
    'RS_POWER':        (1.0, 2.5),
    'RS_SCORE_POWER':  (1.0, 2.5),
    'RS_ALPHA_C':      (0.20, 0.95),
    'RS_TARGET_C':     (55.0, 70.0),
    'RS_ALPHA_P':      (0.0, 0.7),
    'RS_TARGET_P':     (28.0, 38.0),
}


def lhs(n, d, seed):
    rng = np.random.default_rng(seed)
    out = np.zeros((n, d))
    for j in range(d):
        cuts = (np.arange(n) + rng.uniform(0, 1, n)) / n
        rng.shuffle(cuts)
        out[:, j] = cuts
    return out


def sample_kw(s, dims, sample_set):
    out = {}
    for v, name in zip(s, dims):
        if name in sample_set:
            lo, hi = FULL_SPACE[name]
            out[name] = float(lo + v * (hi - lo))
    return out


def eval_kw(coh, kw, swpm_on, rsu_on, macd_on=True, defaults_for_missing=True):
    """Apply defaults for missing params then eval."""
    full = {}
    for k in FULL_SPACE.keys():
        if k in kw:
            full[k] = kw[k]
        elif defaults_for_missing:
            # Lock to neutral default
            lo, hi = FULL_SPACE[k]
            full[k] = (lo + hi) / 2
    if not macd_on:
        full['MACD_W'] = 0.0
    full['SWPM_ENABLED'] = swpm_on
    full['RSU_ENABLED'] = rsu_on
    full['GATE_LO_C'] = 70
    full['GATE_HI_C'] = 84
    full['GATE_LO_P'] = 16
    full['GATE_HI_P'] = 25
    p = SWPMv2Params(**full)
    return evaluate_v2(coh, p), p


def run_variant(coh, name, swpm_on, rsu_on, sample_set, n_lhs=240, n_drill=160, macd_on=True):
    print(f'\n{"="*100}\n[{name}]  swpm={swpm_on} rsu={rsu_on} macd_on={macd_on}\n{"="*100}', flush=True)

    samples = lhs(n_lhs, len(sample_set), seed=hash(name) % 100000)
    rows = []
    t0 = time.time()
    for i, s in enumerate(samples):
        kw = sample_kw(s, sample_set, sample_set)
        m, _ = eval_kw(coh, kw, swpm_on, rsu_on, macd_on=macd_on)
        rows.append({**kw, 'util': m['util'], 'W4': m['W4_breaches'],
                     'W5': m['W5_breaches'], 'W6': m['W6_breaches']})
        for t in m['tiers_call']:
            rows[-1][f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            rows[-1][f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
            rows[-1][f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            rows[-1][f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        for t in m['tiers_put']:
            rows[-1][f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            rows[-1][f'put_{t["tier"]}_dN%'] = t['n_delta_pct']
            rows[-1][f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            rows[-1][f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        if (i+1) % 60 == 0:
            print(f'  [LHS] {i+1}/{n_lhs} elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'  done LHS in {time.time()-t0:.0f}s')
    df = pl.DataFrame(rows).sort('util', descending=True)

    # Drill
    top5 = df.head(5)
    anchor = {k: top5[k].mean() for k in sample_set if k in df.columns}
    rng = np.random.default_rng(hash(name+'drill') % 100000)
    drill_rows = []
    t0 = time.time()
    for i in range(n_drill):
        kw = {}
        for k in sample_set:
            v = anchor[k]
            lo_h, hi_h = FULL_SPACE[k]
            span = max((hi_h - lo_h) * 0.15, abs(v) * 0.20)
            lo = max(lo_h, v - span)
            hi = min(hi_h, v + span)
            kw[k] = float(rng.uniform(lo, hi))
        m, _ = eval_kw(coh, kw, swpm_on, rsu_on, macd_on=macd_on)
        drill_rows.append({**kw, 'util': m['util'], 'W4': m['W4_breaches'],
                           'W5': m['W5_breaches'], 'W6': m['W6_breaches']})
        for t in m['tiers_call']:
            drill_rows[-1][f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            drill_rows[-1][f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
            drill_rows[-1][f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            drill_rows[-1][f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        for t in m['tiers_put']:
            drill_rows[-1][f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            drill_rows[-1][f'put_{t["tier"]}_dN%'] = t['n_delta_pct']
            drill_rows[-1][f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            drill_rows[-1][f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
    print(f'  done drill in {time.time()-t0:.0f}s')
    df_drill = pl.DataFrame(drill_rows).sort('util', descending=True)

    df_all = pl.concat([df, df_drill], how='diagonal_relaxed').sort('util', descending=True)
    df_all.write_csv(OUT / f'phase_f_{name}.csv')

    top = df_all.head(1).to_dicts()[0]
    print(f'  WINNER util={top["util"]:+.3f} W4={top["W4"]} W5={top["W5"]} W6={top["W6"]}')
    print(f'  per-tier:')
    for tier in ['95+', '90+', '85+', '80+', '75+']:
        wr7 = top.get(f'call_{tier}_dWR7', 0)
        wr15 = top.get(f'call_{tier}_dWR15', 0)
        wr30 = top.get(f'call_{tier}_dWR30', 0)
        npct = top.get(f'call_{tier}_dN%', 0)
        print(f'    call {tier:<5} N{npct:+5.1f}%  Δ7={wr7:+5.2f}  Δ15={wr15:+5.2f}  Δ30={wr30:+5.2f}')
    for tier in ['<=15', '<=20', '<=25']:
        wr7 = top.get(f'put_{tier}_dWR7', 0)
        wr15 = top.get(f'put_{tier}_dWR15', 0)
        wr30 = top.get(f'put_{tier}_dWR30', 0)
        npct = top.get(f'put_{tier}_dN%', 0)
        print(f'    put  {tier:<5} N{npct:+5.1f}%  Δ7={wr7:+5.2f}  Δ15={wr15:+5.2f}  Δ30={wr30:+5.2f}')

    return df_all, top


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] {len(coh):,} rows')

    swpm_keys = ['EMA_K', 'RSI_K', 'MACD_TANH_K', 'EMA_W', 'RSI_W', 'MACD_W',
                 'PHASE_POWER', 'SCORE_POWER',
                 'ALPHA_DOWN', 'TARGET_DOWN', 'ALPHA_UP', 'TARGET_UP',
                 'ALPHA_PUT_DOWN', 'TARGET_PUT', 'ALPHA_PUT_UP', 'TARGET_PUT_UP']
    swpm_no_macd = [k for k in swpm_keys if k not in ('MACD_W', 'MACD_TANH_K')]
    rsu_keys = ['RS_GATE', 'RS_K', 'RS_POWER', 'RS_SCORE_POWER',
                'RS_ALPHA_C', 'RS_TARGET_C', 'RS_ALPHA_P', 'RS_TARGET_P']

    results = {}

    # V1 — RSU only
    df1, w1 = run_variant(coh, 'V1_RSU_only', False, True, rsu_keys, n_lhs=200, n_drill=200)
    results['V1_RSU_only'] = w1

    # V2 — SWPM_SEC EMA+RSI only (no MACD)
    df2, w2 = run_variant(coh, 'V2_SEC_emarsi', True, False, swpm_no_macd, n_lhs=200, n_drill=200, macd_on=False)
    results['V2_SEC_emarsi'] = w2

    # V3 — SWPM_SEC + MACD (full SEC)
    df3, w3 = run_variant(coh, 'V3_SEC_full', True, False, swpm_keys, n_lhs=200, n_drill=200)
    results['V3_SEC_full'] = w3

    # V4 — RSU + SEC (no MACD)
    df4, w4 = run_variant(coh, 'V4_RSU_SEC_emarsi', True, True, list(set(swpm_no_macd) | set(rsu_keys)), n_lhs=200, n_drill=200, macd_on=False)
    results['V4_RSU_SEC_emarsi'] = w4

    # V5 — RSU + SEC full (Phase E architecture)
    df5, w5 = run_variant(coh, 'V5_RSU_SEC_full', True, True, list(set(swpm_keys) | set(rsu_keys)), n_lhs=200, n_drill=200)
    results['V5_RSU_SEC_full'] = w5

    print('\n\n========== ABLATION SUMMARY ==========')
    print(f'{"Variant":<24} {"util":>7} {"W4":>3} {"W5":>3} {"W6":>3} {"call75+":>8} {"call80+":>8} {"call85+":>8} {"call90+":>8} {"put<=20":>8}')
    for name, w in results.items():
        c75 = w.get('call_75+_dWR7', 0); c80 = w.get('call_80+_dWR7', 0)
        c85 = w.get('call_85+_dWR7', 0); c90 = w.get('call_90+_dWR7', 0)
        p20 = w.get('put_<=20_dWR7', 0)
        print(f'{name:<24} {w["util"]:+7.2f} {w["W4"]:>3d} {w["W5"]:>3d} {w["W6"]:>3d} {c75:+7.2f}  {c80:+7.2f}  {c85:+7.2f}  {c90:+7.2f}  {p20:+7.2f}')

    with open(OUT / 'phase_f_ablation.json', 'w') as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if not isinstance(vv, (np.floating, np.integer)) or True}
                   for k, v in results.items()}, f, indent=2, default=str)
    print('\n[save] phase_f_ablation.json')


if __name__ == '__main__':
    main()
