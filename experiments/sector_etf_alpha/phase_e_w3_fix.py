"""Phase E — proper W3 multi-time-window check.

For each year (2021..2025), evaluate the SWPM v2 winner separately and report
per-cumulative-tier WR7 deltas. W3 PASSES if WR7 sign on the affected-tier (the
tier most concentrated by the change) is consistent across years.

Also reports:
  - per-tier WR7 deltas split by year on full cohort (1y/3y/5y simulation)
  - the SHIP CANDIDATE summary table

Also tests a SIMPLIFIED variant — locks SCORE_POWER=1.5, PHASE_POWER=2.0, EMA_W/RSI_W/MACD_W=1/3 each
to see if a cleaner architecture preserves alpha (would reduce overfitting risk).
"""
from __future__ import annotations
import io, sys, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from datetime import date
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.sector_etf_alpha.swpm_v2 import (
    SWPMv2Params, evaluate_v2, apply_swpm_v2, CALL_DISCRETE, PUT_DISCRETE
)

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH_PATH = LOCAL / 'cohort_v46_1825.parquet'
OUT = ROOT / 'experiments' / 'sector_etf_alpha'

# Phase E winner (loaded from json)
with open(OUT / 'phase_e_winner.json') as f:
    E_DATA = json.load(f)
E_WINNER = E_DATA['phase_e_kw']


def kw_to_params(kw, **overrides):
    full = dict(kw, **overrides)
    return SWPMv2Params(
        SWPM_ENABLED=True, RSU_ENABLED=True,
        GATE_LO_C=70, GATE_HI_C=84, GATE_LO_P=16, GATE_HI_P=25,
        **full,
    )


def per_tier_yearly(coh, params, label):
    """For each year, compute per-cumulative-tier WR7 base/new/delta."""
    out = apply_swpm_v2(coh, params)

    rows = []
    for yr in ['2021', '2022', '2023', '2024', '2025', '2026']:
        sub = out.filter(pl.col('date').str.starts_with(yr))
        if sub.height < 100:
            continue
        for thresh, tier_label in [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]:
            base = sub.filter(pl.col('overall') >= thresh)
            new = sub.filter(pl.col('new_overall') >= thresh)
            n_b, n_n = base.height, new.height
            if n_b < 20 or n_n < 20:
                continue
            wr_b = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item()
            wr_n = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item()
            wr15_b = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item()
            wr15_n = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item()
            wr30_b = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item()
            wr30_n = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item()
            rows.append({'window': yr, 'tier': tier_label, 'side': 'CALL',
                         'n_base': n_b, 'n_new': n_n,
                         'WR7_b_pct': (wr_b or 0)*100, 'WR7_n_pct': (wr_n or 0)*100, 'WR7_dpp': ((wr_n or 0)-(wr_b or 0))*100,
                         'WR15_dpp': ((wr15_n or 0)-(wr15_b or 0))*100,
                         'WR30_dpp': ((wr30_n or 0)-(wr30_b or 0))*100})
        for thresh, tier_label in [(5, '<=5'), (10, '<=10'), (15, '<=15'), (20, '<=20'), (25, '<=25')]:
            base = sub.filter(pl.col('overall') <= thresh)
            new = sub.filter(pl.col('new_overall') <= thresh)
            n_b, n_n = base.height, new.height
            if n_b < 20 or n_n < 20:
                continue
            wr_b = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item()
            wr_n = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item()
            wr15_b = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item()
            wr15_n = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item()
            wr30_b = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item()
            wr30_n = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item()
            rows.append({'window': yr, 'tier': tier_label, 'side': 'PUT',
                         'n_base': n_b, 'n_new': n_n,
                         'WR7_b_pct': (wr_b or 0)*100, 'WR7_n_pct': (wr_n or 0)*100, 'WR7_dpp': ((wr_n or 0)-(wr_b or 0))*100,
                         'WR15_dpp': ((wr15_n or 0)-(wr15_b or 0))*100,
                         'WR30_dpp': ((wr30_n or 0)-(wr30_b or 0))*100})

    df = pl.DataFrame(rows)
    df.write_csv(OUT / f'phase_e_yearly_{label}.csv')
    return df


def w3_summary(yr_df, label):
    """W3 sign-consistency: for each tier, count + vs - years."""
    print(f'\n=== W3 yearly tier consistency [{label}] ===')
    for side in ['CALL', 'PUT']:
        side_df = yr_df.filter(pl.col('side') == side)
        if side_df.height == 0:
            continue
        for tier in ['95+', '90+', '85+', '80+', '75+', '<=15', '<=20', '<=25']:
            t = side_df.filter(pl.col('tier') == tier)
            if t.height == 0:
                continue
            wr7s = t['WR7_dpp'].to_list()
            wr15s = t['WR15_dpp'].to_list()
            wr30s = t['WR30_dpp'].to_list()
            ns = t['n_new'].to_list()
            yrs = t['window'].to_list()
            n_pos7 = sum(1 for x in wr7s if x > 0.05)
            n_neg7 = sum(1 for x in wr7s if x < -0.05)
            consistent = (n_pos7 >= max(1, len(wr7s) - 1) and n_neg7 == 0) or (n_neg7 >= max(1, len(wr7s) - 1) and n_pos7 == 0)
            flag = '✓' if consistent else ('?' if max(n_pos7, n_neg7) >= len(wr7s) - 1 else '✗')
            wr7s_str = ' '.join(f'{x:+5.2f}' for x in wr7s)
            wr15s_str = ' '.join(f'{x:+5.2f}' for x in wr15s)
            wr30s_str = ' '.join(f'{x:+5.2f}' for x in wr30s)
            ns_str = ' '.join(f'{x:>4d}' for x in ns)
            print(f'  {flag} {side} {tier:<5}  yrs={yrs}  N_new={ns_str}')
            print(f'      ΔWR7  {wr7s_str}')
            print(f'      ΔWR15 {wr15s_str}')
            print(f'      ΔWR30 {wr30s_str}')


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] {len(coh):,} rows')

    # ----- Original Phase E winner -----
    print('\n[original E winner] full param set')
    for k, v in E_WINNER.items():
        print(f'  {k}={v:.4f}')

    params_e = kw_to_params(E_WINNER)
    yr_e = per_tier_yearly(coh, params_e, 'E_winner')
    w3_summary(yr_e, 'E_winner')

    # ----- Locked-architecture variant — simpler, less overfitting risk -----
    print('\n[locked architecture] PHASE_POWER=2.0, SCORE_POWER=1.5, EMA_W=0.33, RSI_W=0.33, MACD_W=0.33')
    locked = dict(E_WINNER)
    locked['PHASE_POWER'] = 2.0
    locked['SCORE_POWER'] = 1.5
    locked['EMA_W'] = 0.33
    locked['RSI_W'] = 0.33
    locked['MACD_W'] = 0.33

    params_locked = kw_to_params(locked)
    m_locked = evaluate_v2(coh, params_locked)
    print(f'\n  [locked] util={m_locked["util"]:+.3f}  W4={m_locked["W4_breaches"]} W5={m_locked["W5_breaches"]} W6={m_locked["W6_breaches"]}')
    for t in m_locked['tiers_call']:
        print(f'    call {t["tier"]:<5} N={t["n_new"]:>5,d} ({t["n_delta_pct"]:+5.1f}%)  Δ7={t["wr7_delta_pp"]:+5.2f} Δ15={t["wr15_delta_pp"]:+5.2f} Δ30={t["wr30_delta_pp"]:+5.2f}')
    for t in m_locked['tiers_put']:
        print(f'    put  {t["tier"]:<5} N={t["n_new"]:>5,d} ({t["n_delta_pct"]:+5.1f}%)  Δ7={t["wr7_delta_pp"]:+5.2f} Δ15={t["wr15_delta_pp"]:+5.2f} Δ30={t["wr30_delta_pp"]:+5.2f}')

    yr_locked = per_tier_yearly(coh, params_locked, 'locked')
    w3_summary(yr_locked, 'locked')

    # ----- Save final ship candidate -----
    final_kw = dict(locked)  # prefer locked architecture for ship candidate
    final_summary = {
        'description': 'SWPM v2 Stage 1 ship candidate',
        'algorithm_target': 'v47 (post-v46 SWPM ship)',
        'cohort_version': 46,
        'cohort_n_rows': 33450,
        'date_range_max': '2026-05-05',
        'holdout_filtered': True,
        'params': final_kw,
        'metrics_5y': {
            'util': m_locked['util'],
            'W4_breaches': m_locked['W4_breaches'],
            'W5_breaches': m_locked['W5_breaches'],
            'W6_breaches': m_locked['W6_breaches'],
            'tiers_call': m_locked['tiers_call'],
            'tiers_put': m_locked['tiers_put'],
        },
    }
    with open(OUT / 'phase_e_ship_candidate.json', 'w') as f:
        json.dump(final_summary, f, indent=2, default=str)
    print(f'\n[save] phase_e_ship_candidate.json')


if __name__ == '__main__':
    main()
