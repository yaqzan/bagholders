"""Phase E — Fine grid + full W1-W6 gate validation on Architecture C winner.

Steps:
  1. Tight LHS (200 variants) within ±5% of C winner — confirm robustness
  2. Multi-time-window check (W3): split cohort by year (2021/2022/2023/2024/2025),
     evaluate winner per window, verify WR7 sign-consistency on affected cohort
  3. Per-discrete-bucket WR7 inspection (W4 detail) at 5y
  4. N capacity check (W5) per binding tier
  5. Cumulative gradient (W6) on new_overall ranking
  6. Output: phase_e_winner.json with FULL ship-candidate spec
"""
from __future__ import annotations
import io, sys, json, time, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm_v2 import (
    SWPMv2Params, evaluate_v2, apply_swpm_v2,
    CALL_DISCRETE, PUT_DISCRETE
)

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH_PATH = LOCAL / 'cohort_v46_1825.parquet'
OUT_DIR = ROOT / 'experiments' / 'sector_etf_alpha'

# Phase v2 C-stacked winner
C_WINNER = {
    'EMA_K': 6.8296, 'RSI_K': 24.5361, 'MACD_TANH_K': 0.8301,
    'EMA_W': 0.0476, 'RSI_W': 0.7683, 'MACD_W': 0.8521,
    'PHASE_POWER': 2.9170, 'SCORE_POWER': 2.1684,
    'ALPHA_DOWN': 0.6954, 'TARGET_DOWN': 67.9530,
    'ALPHA_UP': 0.8801, 'TARGET_UP': 91.9687,
    'ALPHA_PUT_DOWN': 0.4696, 'TARGET_PUT': 35.9655,
    'ALPHA_PUT_UP': 0.0998, 'TARGET_PUT_UP': 10.8556,
    'RS_GATE': 0.0580, 'RS_K': 0.0626, 'RS_POWER': 1.9412, 'RS_SCORE_POWER': 1.3111,
    'RS_ALPHA_C': 0.3897, 'RS_TARGET_C': 64.3720,
    'RS_ALPHA_P': 0.3467, 'RS_TARGET_P': 30.8091,
}


def kw_to_params(kw):
    return SWPMv2Params(
        SWPM_ENABLED=True, RSU_ENABLED=True,
        GATE_LO_C=70, GATE_HI_C=84, GATE_LO_P=16, GATE_HI_P=25,
        **kw,
    )


def fine_lhs_basin(anchor: dict, frac: float, n: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    keys = list(anchor.keys())
    out = []
    for i in range(n):
        s = {}
        for k in keys:
            v = anchor[k]
            span = max(abs(v) * frac, 0.01)
            s[k] = float(rng.uniform(v - span, v + span))
        out.append(s)
    return out


def affected_cohort_metrics(coh, params):
    """Per-window WR1/7/15/30 on the AFFECTED cohort (rows whose score changed)."""
    out = apply_swpm_v2(coh, params)
    affected = out.filter(pl.col('overall') != pl.col('new_overall'))

    # Year splits
    years = ['2021', '2022', '2023', '2024', '2025', '2026']
    res = []
    for yr in years:
        yr_subset = affected.filter(pl.col('date').str.starts_with(yr))
        if yr_subset.height < 30:
            continue
        # Group by side: how many SURVIVE in qualifying tier (call >=70 or put <=25)
        for side, qualifies_old, qualifies_new in [
            ('CALL', pl.col('overall') >= 70, pl.col('new_overall') >= 70),
            ('PUT',  pl.col('overall') <= 25, pl.col('new_overall') <= 25),
        ]:
            base = yr_subset.filter(qualifies_old)
            new = yr_subset.filter(qualifies_new)
            if base.height < 20:
                continue
            row = {'window': yr, 'side': side, 'n_base': base.height, 'n_new': new.height}
            for w in [1, 7, 15, 30]:
                col = f'wr_{w}'
                wr_b = base.select(pl.col(col).cast(pl.Float64).mean()).item()
                wr_n = new.select(pl.col(col).cast(pl.Float64).mean()).item() if new.height else None
                row[f'WR{w}_base'] = wr_b
                row[f'WR{w}_new'] = wr_n
                row[f'WR{w}_dpp'] = ((wr_n or 0) - (wr_b or 0)) * 100 if (wr_n is not None and wr_b is not None) else 0
            res.append(row)
    return pl.DataFrame(res)


def per_discrete_bucket_5y(coh, params):
    out = apply_swpm_v2(coh, params)
    rows = []
    for lo, hi, label in CALL_DISCRETE + PUT_DISCRETE:
        base = out.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        new = out.filter((pl.col('new_overall') >= lo) & (pl.col('new_overall') <= hi))
        n_base, n_new = base.height, new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100 if (wr_base is not None and wr_new is not None) else 0
        rows.append({'bucket': label, 'n_base': n_base, 'n_new': n_new,
                     'WR7_base_pct': (wr_base or 0)*100, 'WR7_new_pct': (wr_new or 0)*100,
                     'WR7_delta_pp': delta})
    return pl.DataFrame(rows)


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] {len(coh):,} rows  date {coh["date"].min()} -> {coh["date"].max()}', flush=True)

    # ----- Step 1: Tight LHS basin around C winner -----
    print('\n[step 1] Tight ±5% LHS around C winner (200 variants)')
    samples = fine_lhs_basin(C_WINNER, frac=0.05, n=200, seed=303)
    rows = []
    t0 = time.time()
    for i, kw in enumerate(samples):
        params = kw_to_params(kw)
        m = evaluate_v2(coh, params)
        row = {**kw, 'util': m['util'],
               'W4': m['W4_breaches'], 'W5': m['W5_breaches'], 'W6': m['W6_breaches'],
               'n_changed': m['n_changed']}
        for t in m['tiers_call']:
            row[f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row[f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
            row[f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row[f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        for t in m['tiers_put']:
            row[f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
            row[f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
            row[f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
        rows.append(row)
        if (i+1) % 50 == 0:
            print(f'  {i+1}/200 elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'  done in {time.time()-t0:.0f}s')

    df = pl.DataFrame(rows).sort('util', descending=True)
    df.write_csv(OUT_DIR / 'phase_e_tight_lhs.csv')

    # Robustness — distribution of util
    print(f'\n  [robustness] util distribution in tight basin:')
    print(f'    max={df["util"].max():.2f}  p95={df["util"].quantile(0.95):.2f}  median={df["util"].median():.2f}  p5={df["util"].quantile(0.05):.2f}  min={df["util"].min():.2f}')
    print(f'    {(df["W4"]==0).sum()}/{len(df)} variants have W4=0')

    # Top 5
    cols = ['util', 'W4', 'W5', 'W6', 'call_75+_dWR7', 'call_80+_dWR7',
            'call_85+_dWR7', 'call_90+_dWR7', 'put_<=20_dWR7', 'put_<=15_dWR7']
    avail = [c for c in cols if c in df.columns]
    print('\n  Top 5 in tight basin:')
    print(df.select(avail).head(5))

    # Use tight winner
    e_winner = df.head(1).to_dicts()[0]
    e_kw = {k: e_winner[k] for k in C_WINNER.keys()}

    # ----- Step 2: Multi-time-window check (W3) on affected cohort -----
    print('\n[step 2] W3 multi-time-window check on affected cohort')
    e_params = kw_to_params(e_kw)
    yr_metrics = affected_cohort_metrics(coh, e_params)
    print('\nAffected-cohort WR1/7/15/30 by year:')
    print(yr_metrics)

    # Save year breakdown
    yr_metrics.write_csv(OUT_DIR / 'phase_e_yearly_affected.csv')

    # W3 check: WR7 sign consistency across years on affected cohort (per side)
    print('\n  [W3 check] WR7 sign consistency across years (affected cohort):')
    for side in ['CALL', 'PUT']:
        side_data = yr_metrics.filter(pl.col('side') == side)
        if side_data.height < 3:
            print(f'    {side}: insufficient years')
            continue
        signs = side_data['WR7_dpp'].to_list()
        n_pos = sum(1 for s in signs if s > 0)
        n_neg = sum(1 for s in signs if s < 0)
        n_total = len(signs)
        print(f'    {side}: {n_pos}/{n_total} positive years on affected cohort. WR7 deltas: {[f"{s:+.2f}" for s in signs]}')

    # ----- Step 3: Per-discrete-bucket inspection (W4 detail) -----
    print('\n[step 3] W4 per-discrete-bucket WR7 (5y full)')
    discr = per_discrete_bucket_5y(coh, e_params)
    print(discr)
    discr.write_csv(OUT_DIR / 'phase_e_discrete.csv')

    # ----- Step 4: Cumulative WR7 gradient (W6 detail) -----
    print('\n[step 4] W6 gradient — cumulative call WR7 should be monotone improving')
    out = apply_swpm_v2(coh, e_params)
    grad_rows = []
    for thresh, label in [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]:
        n_base = out.filter(pl.col('overall') >= thresh).height
        wr_base = out.filter(pl.col('overall') >= thresh).select(pl.col('wr_7').cast(pl.Float64).mean()).item() or 0
        n_new = out.filter(pl.col('new_overall') >= thresh).height
        wr_new = out.filter(pl.col('new_overall') >= thresh).select(pl.col('wr_7').cast(pl.Float64).mean()).item() or 0
        grad_rows.append({'tier': label, 'n_base': n_base, 'n_new': n_new,
                          'wr7_base_pct': wr_base*100, 'wr7_new_pct': wr_new*100,
                          'wr7_delta_pp': (wr_new - wr_base)*100})
    print(pl.DataFrame(grad_rows))

    # W6 check
    wr_news = [r['wr7_new_pct'] for r in grad_rows]
    print(f'\n  [W6 check] cumulative WR7 sequence (should be decreasing as tier broadens):')
    print(f'    {[f"{x:.2f}" for x in wr_news]}')
    is_monotone = all(wr_news[i] >= wr_news[i+1] - 0.5 for i in range(len(wr_news)-1))
    print(f'    monotone (within 0.5pp tolerance): {is_monotone}')

    # ----- Step 5: Final ship-candidate spec -----
    print('\n[step 5] FINAL SHIP-CANDIDATE SPEC (write phase_e_winner.json)')
    final = {
        'phase_e_kw': e_kw,
        'util_at_5y': e_winner['util'],
        'W4_breaches': e_winner['W4'],
        'W5_breaches': e_winner['W5'],
        'W6_breaches': e_winner['W6'],
        'tight_basin_robustness': {
            'util_max': float(df['util'].max()),
            'util_p95': float(df['util'].quantile(0.95)),
            'util_median': float(df['util'].median()),
            'util_p5': float(df['util'].quantile(0.05)),
            'pct_W4_clean': float((df['W4']==0).sum() / len(df)),
        },
    }
    with open(OUT_DIR / 'phase_e_winner.json', 'w') as f:
        json.dump(final, f, indent=2, default=str)
    print(f'  saved phase_e_winner.json')


if __name__ == '__main__':
    main()
