"""SWPM v2 — extended sector wave phase modulator + stock relative-strength U-curve.

Two stacked mechanisms:
  (1) SWPM_SEC — sector phase from {sec_pct_ema50, sec_rsi, sec_macd_sign}.
      Dampens calls in overheated sectors, lifts calls in oversold sectors,
      bidirectional on puts.
  (2) RSU — Relative-Strength U-curve dampener. Fires when |stock_rs_5d| is
      large in EITHER direction (z=-9.58 / -8.52 cohort signature). Pulls
      affected calls toward TARGET. Symmetric (both extremes hurt calls).

Each mechanism can be enabled/disabled independently via gate fields.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
import polars as pl

CALL_TIERS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]
PUT_TIERS = [(5, '<=5'), (10, '<=10'), (15, '<=15'), (20, '<=20'), (25, '<=25')]

CALL_DISCRETE = [(95, 100, '95-100'), (90, 94, '90-94'), (85, 89, '85-89'),
                 (80, 84, '80-84'), (75, 79, '75-79'), (70, 74, '70-74')]
PUT_DISCRETE = [(0, 5, '0-5'), (6, 10, '6-10'), (11, 15, '11-15'),
                (16, 20, '16-20'), (21, 25, '21-25')]


@dataclass
class SWPMv2Params:
    # SWPM_SEC — sector phase mechanism
    SWPM_ENABLED: bool = True
    EMA_K: float = 5.0
    RSI_K: float = 25.0
    MACD_TANH_K: float = 0.5     # tanh saturation of sec_macd line
    EMA_W: float = 0.4           # weight of sec_pct_ema50 in phase blend
    RSI_W: float = 0.3           # weight of sec_rsi
    MACD_W: float = 0.3          # weight of sec_macd
    PHASE_POWER: float = 2.0
    SCORE_POWER: float = 1.5

    GATE_LO_C: int = 70
    GATE_HI_C: int = 84
    ALPHA_DOWN: float = 0.50
    TARGET_DOWN: float = 65.0
    ALPHA_UP: float = 0.50
    TARGET_UP: float = 88.0

    GATE_LO_P: int = 16
    GATE_HI_P: int = 25
    ALPHA_PUT_DOWN: float = 0.50
    TARGET_PUT: float = 32.0
    ALPHA_PUT_UP: float = 0.20
    TARGET_PUT_UP: float = 12.0

    # RSU — Relative-Strength U-curve mechanism (NEW for v2)
    RSU_ENABLED: bool = True
    RS_GATE: float = 0.05        # |rs_5d| threshold to start firing (5%)
    RS_K: float = 0.10           # |rs_5d| at which weakness saturates (10%)
    RS_POWER: float = 1.5        # power of (|rs_5d|-gate) saturation
    RS_SCORE_POWER: float = 1.5  # score-norm power
    RS_ALPHA_C: float = 0.60     # call dampen alpha
    RS_TARGET_C: float = 64.0    # call drift target
    RS_ALPHA_P: float = 0.40     # put dampen alpha (puts in extreme rs)
    RS_TARGET_P: float = 32.0    # put drift target (lift OUT)


def add_sec_phase(df: pl.DataFrame, p: SWPMv2Params) -> pl.DataFrame:
    """sec_phase ∈ [-1, +1] from sec_pct_ema50, sec_rsi, sec_macd."""
    p1 = (pl.col('sec_etf_pct_ema50') / p.EMA_K).clip(-1.0, 1.0)
    p2 = ((pl.col('sec_etf_rsi') - 50.0) / p.RSI_K).clip(-1.0, 1.0)
    # tanh-bound macd
    p3 = (pl.col('sec_etf_macd') / p.MACD_TANH_K).tanh().clip(-1.0, 1.0)

    total_w = p.EMA_W + p.RSI_W + p.MACD_W
    if total_w <= 0:
        total_w = 1.0
    ew = p.EMA_W / total_w
    rw = p.RSI_W / total_w
    mw = p.MACD_W / total_w

    # Component-wise null handling: if any is null, drop its weight and renormalize
    has_ema = pl.col('sec_etf_pct_ema50').is_not_null().cast(pl.Float64)
    has_rsi = pl.col('sec_etf_rsi').is_not_null().cast(pl.Float64)
    has_macd = pl.col('sec_etf_macd').is_not_null().cast(pl.Float64)

    weighted = (p1.fill_null(0.0) * ew * has_ema +
                p2.fill_null(0.0) * rw * has_rsi +
                p3.fill_null(0.0) * mw * has_macd)
    norm = (ew * has_ema + rw * has_rsi + mw * has_macd)

    sec_phase_expr = pl.when(norm > 0).then(weighted / norm).otherwise(None).alias('sec_phase')
    return df.with_columns(sec_phase_expr)


def apply_swpm_v2(df: pl.DataFrame, p: SWPMv2Params) -> pl.DataFrame:
    """Apply both SWPM_SEC and RSU sequentially. Each mechanism transforms 'overall'.

    Order: SWPM_SEC first (sector phase based), RSU second (relative strength).
    Both target the same overall column; second mechanism reads the result of the first.
    """
    df = add_sec_phase(df, p)

    overall = pl.col('overall').cast(pl.Float64)
    phase = pl.col('sec_phase')

    # ---------------- SWPM_SEC ----------------
    if p.SWPM_ENABLED:
        call_lo, call_hi = p.GATE_LO_C, p.GATE_HI_C
        call_sn = ((overall - call_lo) / (call_hi - call_lo)).clip(0.0, 1.0).pow(p.SCORE_POWER)
        put_lo, put_hi = p.GATE_LO_P, p.GATE_HI_P
        put_sn = ((put_hi - overall) / (put_hi - put_lo)).clip(0.0, 1.0).pow(p.SCORE_POWER)

        pos_phase = phase.clip(0.0, 1.0).pow(p.PHASE_POWER)
        neg_phase = (-phase).clip(0.0, 1.0).pow(p.PHASE_POWER)

        call_in = (overall >= call_lo) & (overall <= call_hi)
        call_delta = (
            pl.when(call_in & (phase > 0))
              .then(- p.ALPHA_DOWN * pos_phase * call_sn * (overall - p.TARGET_DOWN))
            .when(call_in & (phase < 0))
              .then(  p.ALPHA_UP   * neg_phase * call_sn * (p.TARGET_UP - overall))
            .otherwise(0.0)
        )

        put_in = (overall >= put_lo) & (overall <= put_hi)
        put_delta = (
            pl.when(put_in & (phase < 0))
              .then(  p.ALPHA_PUT_DOWN * neg_phase * put_sn * (p.TARGET_PUT - overall))
            .when(put_in & (phase > 0))
              .then(- p.ALPHA_PUT_UP   * pos_phase * put_sn * (overall - p.TARGET_PUT_UP))
            .otherwise(0.0)
        )

        delta = call_delta + put_delta
        new_overall = (
            pl.when(phase.is_null()).then(overall).otherwise(overall + delta)
        )
        df = df.with_columns(new_overall.alias('_swpm_overall'))
    else:
        df = df.with_columns(pl.col('overall').cast(pl.Float64).alias('_swpm_overall'))

    # ---------------- RSU — Relative-Strength U-curve ----------------
    if p.RSU_ENABLED:
        prev = pl.col('_swpm_overall')
        rs5 = pl.col('stock_ret_5d') - pl.col('sec_etf_ret_5d')
        # weakness: 0 if |rs5| <= GATE, ramps to 1 at |rs5| = GATE + K
        excess = (rs5.abs() - p.RS_GATE).clip(0.0, None)
        weakness = (excess / p.RS_K).clip(0.0, 1.0).pow(p.RS_POWER)

        call_lo, call_hi = p.GATE_LO_C, p.GATE_HI_C
        call_sn = ((prev - call_lo) / (call_hi - call_lo)).clip(0.0, 1.0).pow(p.RS_SCORE_POWER)
        put_lo, put_hi = p.GATE_LO_P, p.GATE_HI_P
        put_sn = ((put_hi - prev) / (put_hi - put_lo)).clip(0.0, 1.0).pow(p.RS_SCORE_POWER)

        call_in = (prev >= call_lo) & (prev <= call_hi)
        put_in = (prev >= put_lo) & (prev <= put_hi)

        call_delta = (
            pl.when(call_in & rs5.is_not_null())
              .then(- p.RS_ALPHA_C * weakness * call_sn * (prev - p.RS_TARGET_C))
            .otherwise(0.0)
        )
        put_delta = (
            pl.when(put_in & rs5.is_not_null())
              .then(  p.RS_ALPHA_P * weakness * put_sn * (p.RS_TARGET_P - prev))
            .otherwise(0.0)
        )

        delta = call_delta + put_delta
        df = df.with_columns((pl.col('_swpm_overall') + delta).alias('_rsu_overall'))
    else:
        df = df.with_columns(pl.col('_swpm_overall').alias('_rsu_overall'))

    new_overall = pl.col('_rsu_overall').clip(0.0, 100.0).round(0).cast(pl.Int64)
    return df.with_columns(new_overall.alias('new_overall'))


def evaluate_v2(df: pl.DataFrame, p: SWPMv2Params) -> dict:
    out = apply_swpm_v2(df, p)

    metrics = {'tiers_call': [], 'tiers_put': [],
               'discrete_call': [], 'discrete_put': [],
               'W4_breaches': 0, 'W5_breaches': 0, 'W6_breaches': 0,
               'sec_phase_coverage': 0.0}
    metrics['sec_phase_coverage'] = out.select(pl.col('sec_phase').is_not_null().mean()).item() if 'sec_phase' in out.columns else 0
    metrics['n_changed'] = int(out.filter(pl.col('overall') != pl.col('new_overall')).height)

    for thresh, label in CALL_TIERS:
        base = out.filter(pl.col('overall') >= thresh)
        new = out.filter(pl.col('new_overall') >= thresh)
        n_base, n_new = base.height, new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        wr15_base = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_base else None
        wr15_new = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_new else None
        wr30_base = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_base else None
        wr30_new = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_new else None
        wr1_base = base.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_base else None
        wr1_new = new.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100
        delta1 = ((wr1_new or 0) - (wr1_base or 0)) * 100
        delta15 = ((wr15_new or 0) - (wr15_base or 0)) * 100
        delta30 = ((wr30_new or 0) - (wr30_base or 0)) * 100
        n_delta_pct = (n_new - n_base) / n_base * 100 if n_base else 0
        metrics['tiers_call'].append({
            'tier': label, 'thresh': thresh,
            'n_base': n_base, 'n_new': n_new, 'n_delta_pct': n_delta_pct,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
            'wr1_delta_pp': delta1, 'wr15_delta_pp': delta15, 'wr30_delta_pp': delta30,
        })

    for thresh, label in PUT_TIERS:
        base = out.filter(pl.col('overall') <= thresh)
        new = out.filter(pl.col('new_overall') <= thresh)
        n_base, n_new = base.height, new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        wr15_base = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_base else None
        wr15_new = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_new else None
        wr30_base = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_base else None
        wr30_new = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_new else None
        wr1_base = base.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_base else None
        wr1_new = new.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100
        delta1 = ((wr1_new or 0) - (wr1_base or 0)) * 100
        delta15 = ((wr15_new or 0) - (wr15_base or 0)) * 100
        delta30 = ((wr30_new or 0) - (wr30_base or 0)) * 100
        n_delta_pct = (n_new - n_base) / n_base * 100 if n_base else 0
        metrics['tiers_put'].append({
            'tier': label, 'thresh': thresh,
            'n_base': n_base, 'n_new': n_new, 'n_delta_pct': n_delta_pct,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
            'wr1_delta_pp': delta1, 'wr15_delta_pp': delta15, 'wr30_delta_pp': delta30,
        })

    for lo, hi, label in CALL_DISCRETE:
        base = out.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        new = out.filter((pl.col('new_overall') >= lo) & (pl.col('new_overall') <= hi))
        n_base, n_new = base.height, new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100 if (wr_base is not None and wr_new is not None) else 0
        metrics['discrete_call'].append({
            'bucket': label, 'n_base': n_base, 'n_new': n_new,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
        })
        if n_new >= 30 and delta < -0.5:
            metrics['W4_breaches'] += 1

    for lo, hi, label in PUT_DISCRETE:
        base = out.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        new = out.filter((pl.col('new_overall') >= lo) & (pl.col('new_overall') <= hi))
        n_base, n_new = base.height, new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100 if (wr_base is not None and wr_new is not None) else 0
        metrics['discrete_put'].append({
            'bucket': label, 'n_base': n_base, 'n_new': n_new,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
        })
        if n_new >= 30 and delta < -0.5:
            metrics['W4_breaches'] += 1

    # W5
    for t in metrics['tiers_call']:
        if t['thresh'] in [70, 75, 80, 85, 90]:
            floor = max(20, int(t['n_base'] * 0.85))
            if t['n_new'] < floor:
                metrics['W5_breaches'] += 1
    for t in metrics['tiers_put']:
        if t['thresh'] in [15, 20, 25]:
            floor = max(20, int(t['n_base'] * 0.85))
            if t['n_new'] < floor:
                metrics['W5_breaches'] += 1

    # W6 gradient (cumulative WR7 monotone-improving)
    wr7s_call_new = [t['wr7_new'] for t in metrics['tiers_call'] if t['wr7_new'] is not None]
    for i in range(len(wr7s_call_new) - 1):
        if wr7s_call_new[i] < wr7s_call_new[i+1] - 0.005:
            metrics['W6_breaches'] += 1

    # Utility — same weighting as v1
    weights_call = {95: 1.0, 90: 1.5, 85: 1.5, 80: 2.0, 75: 2.0, 70: 1.0}
    weights_put = {15: 1.5, 20: 1.0, 25: 1.5}
    util = 0.0
    for t in metrics['tiers_call']:
        w = weights_call.get(t['thresh'], 0.5)
        util += w * (t['wr7_delta_pp'] or 0)
    for t in metrics['tiers_put']:
        w = weights_put.get(t['thresh'], 0.5)
        util += w * (t['wr7_delta_pp'] or 0)
    util -= 5.0 * metrics['W4_breaches']
    util -= 5.0 * metrics['W5_breaches']
    util -= 8.0 * metrics['W6_breaches']

    metrics['util'] = util
    affected = out.filter(pl.col('overall') != pl.col('new_overall'))
    metrics['affected_n'] = affected.height
    metrics['affected_call_n'] = affected.filter(pl.col('signal_type') == 'CALL').height
    metrics['affected_put_n'] = affected.filter(pl.col('signal_type') == 'PUT').height

    return metrics


if __name__ == '__main__':
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT))
    coh = pl.read_parquet(ROOT / '.cache' / 'sector_etf_alpha' / 'cohort_v46_1825.parquet')
    print(f'Loaded {len(coh):,} rows')

    print('\n--- Test 1: SWPM_SEC only (RSU off) ---')
    p = SWPMv2Params(SWPM_ENABLED=True, RSU_ENABLED=False)
    m = evaluate_v2(coh, p)
    print(f'util={m["util"]:+.3f}  W4={m["W4_breaches"]}  affected={m["affected_n"]:,} (call={m["affected_call_n"]}, put={m["affected_put_n"]})')
    print(f'  call: 75+ {m["tiers_call"][4]["wr7_delta_pp"]:+.2f}pp / 80+ {m["tiers_call"][3]["wr7_delta_pp"]:+.2f}pp / 85+ {m["tiers_call"][2]["wr7_delta_pp"]:+.2f}pp / 90+ {m["tiers_call"][1]["wr7_delta_pp"]:+.2f}pp')

    print('\n--- Test 2: RSU only (SWPM off) ---')
    p = SWPMv2Params(SWPM_ENABLED=False, RSU_ENABLED=True)
    m = evaluate_v2(coh, p)
    print(f'util={m["util"]:+.3f}  W4={m["W4_breaches"]}  affected={m["affected_n"]:,} (call={m["affected_call_n"]}, put={m["affected_put_n"]})')
    print(f'  call: 75+ {m["tiers_call"][4]["wr7_delta_pp"]:+.2f}pp / 80+ {m["tiers_call"][3]["wr7_delta_pp"]:+.2f}pp / 85+ {m["tiers_call"][2]["wr7_delta_pp"]:+.2f}pp / 90+ {m["tiers_call"][1]["wr7_delta_pp"]:+.2f}pp')
    for t in m['tiers_call']:
        print(f'    {t["tier"]:<5} N: {t["n_base"]:>5,d} -> {t["n_new"]:>5,d} ({t["n_delta_pct"]:+5.1f}%)  WR7 {(t["wr7_base"] or 0)*100:.2f} -> {(t["wr7_new"] or 0)*100:.2f}  Δ{t["wr7_delta_pp"]:+5.2f}pp')

    print('\n--- Test 3: Both stacked ---')
    p = SWPMv2Params(SWPM_ENABLED=True, RSU_ENABLED=True)
    m = evaluate_v2(coh, p)
    print(f'util={m["util"]:+.3f}  W4={m["W4_breaches"]}  affected={m["affected_n"]:,}')
    for t in m['tiers_call']:
        print(f'    {t["tier"]:<5} N: {t["n_base"]:>5,d} -> {t["n_new"]:>5,d} ({t["n_delta_pct"]:+5.1f}%)  WR7 {(t["wr7_base"] or 0)*100:.2f} -> {(t["wr7_new"] or 0)*100:.2f}  Δ{t["wr7_delta_pp"]:+5.2f}pp')
