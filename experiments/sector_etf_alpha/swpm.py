"""SWPM (Sector Wave Phase Modulator) — score-stage transform module.

Pure-function variant evaluator. Given the v46 cohort parquet and a SWPM
parameter dict, returns:
  - new_overall column (transformed score)
  - per-tier WR7 baseline vs new (cumulative)
  - per-discrete-bucket WR7 baseline vs new (W4 check)
  - tier N counts (W5 check)

No DB, no scoring code mutation. Fast (~1 sec per variant on 33k rows).
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
import polars as pl


# Cumulative tier thresholds (W1/W2 affected-cohort WR7 reporting)
CALL_TIERS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]
PUT_TIERS = [(5, '<=5'), (10, '<=10'), (15, '<=15'), (20, '<=20'), (25, '<=25')]

# Discrete (W4) buckets
CALL_DISCRETE = [(95, 100, '95-100'), (90, 94, '90-94'), (85, 89, '85-89'),
                 (80, 84, '80-84'), (75, 79, '75-79'), (70, 74, '70-74')]
PUT_DISCRETE = [(0, 5, '0-5'), (6, 10, '6-10'), (11, 15, '11-15'),
                (16, 20, '16-20'), (21, 25, '21-25')]


@dataclass
class SWPMParams:
    """SWPM v2 parameter set.

    Phase coefficient (continuous, ∈ [-1, +1]):
      sec_phase = blend(sec_pct_ema50/EMA_K, (sec_rsi-50)/RSI_K)

    Call (overall ∈ [GATE_LO_C, GATE_HI_C]):
      score_norm = clip((overall-GATE_LO_C)/(GATE_HI_C-GATE_LO_C), 0, 1) ** SCORE_POWER
      if sec_phase > 0: drift overall -= ALPHA_DOWN * sec_phase**P * score_norm * (overall - TARGET_DOWN)
      else:             drift overall += ALPHA_UP   * (-sec_phase)**P * score_norm * (TARGET_UP - overall)

    Put (overall ∈ [GATE_LO_P, GATE_HI_P]):
      score_norm = clip((GATE_HI_P-overall)/(GATE_HI_P-GATE_LO_P), 0, 1) ** SCORE_POWER
      if sec_phase < 0: drift overall += ALPHA_PUT_DOWN * (-sec_phase)**P * score_norm * (TARGET_PUT - overall)
      else:             drift overall -= ALPHA_PUT_UP   * sec_phase**P    * score_norm * (overall - TARGET_PUT_UP)
    """
    EMA_K: float = 5.0
    RSI_K: float = 25.0
    RSI_W: float = 0.5             # weight of RSI vs EMA50 in phase blend
    PHASE_POWER: float = 2.0
    SCORE_POWER: float = 1.5

    # Call dampener
    GATE_LO_C: int = 70
    GATE_HI_C: int = 84
    ALPHA_DOWN: float = 0.80
    TARGET_DOWN: float = 62.0
    ALPHA_UP: float = 0.50
    TARGET_UP: float = 88.0

    # Put modifier (lift OUT of put zone in oversold sector; dampen in overheated)
    GATE_LO_P: int = 16
    GATE_HI_P: int = 25
    ALPHA_PUT_DOWN: float = 0.80   # sector oversold → put fails → lift toward TARGET_PUT (out of zone)
    TARGET_PUT: float = 32.0
    ALPHA_PUT_UP: float = 0.30     # sector overheated → put confirmed → lift INTO put zone (lower score)
    TARGET_PUT_UP: float = 12.0


def add_sec_phase(df: pl.DataFrame, p: SWPMParams) -> pl.DataFrame:
    """Compute sec_phase ∈ [-1, +1] from sec_etf_pct_ema50 and sec_etf_rsi."""
    p1 = (pl.col('sec_etf_pct_ema50') / p.EMA_K).clip(-1.0, 1.0)
    p2 = ((pl.col('sec_etf_rsi') - 50.0) / p.RSI_K).clip(-1.0, 1.0)

    # If sec_etf_rsi is null fall back to p1 only
    sec_phase_expr = (
        pl.when(pl.col('sec_etf_pct_ema50').is_null())
          .then(None)
          .when(pl.col('sec_etf_rsi').is_null())
          .then(p1)
          .otherwise(p1 * (1 - p.RSI_W) + p2 * p.RSI_W)
    ).alias('sec_phase')
    return df.with_columns(sec_phase_expr)


def apply_swpm(df: pl.DataFrame, p: SWPMParams) -> pl.DataFrame:
    """Compute new_overall column from overall + sec_phase per SWPM rules."""
    df = add_sec_phase(df, p)

    overall = pl.col('overall').cast(pl.Float64)
    phase = pl.col('sec_phase')

    # Call score_norm (between gate lo and hi)
    call_lo, call_hi = p.GATE_LO_C, p.GATE_HI_C
    call_sn = ((overall - call_lo) / (call_hi - call_lo)).clip(0.0, 1.0).pow(p.SCORE_POWER)

    # Put score_norm (between put gate lo and hi, mirror direction)
    put_lo, put_hi = p.GATE_LO_P, p.GATE_HI_P
    put_sn = ((put_hi - overall) / (put_hi - put_lo)).clip(0.0, 1.0).pow(p.SCORE_POWER)

    pos_phase = phase.clip(0.0, 1.0).pow(p.PHASE_POWER)
    neg_phase = (-phase).clip(0.0, 1.0).pow(p.PHASE_POWER)

    # Call delta: dampen if pos, lift if neg
    call_in_gate = (overall >= call_lo) & (overall <= call_hi)
    call_delta = (
        pl.when(call_in_gate & (phase > 0))
          .then(- p.ALPHA_DOWN * pos_phase * call_sn * (overall - p.TARGET_DOWN))
        .when(call_in_gate & (phase < 0))
          .then(  p.ALPHA_UP   * neg_phase * call_sn * (p.TARGET_UP - overall))
        .otherwise(0.0)
    )

    # Put delta: lift OUT (toward TARGET_PUT > GATE_HI_P) if oversold sector;
    #            lift INTO (toward TARGET_PUT_UP < GATE_LO_P) if overheated sector.
    put_in_gate = (overall >= put_lo) & (overall <= put_hi)
    put_delta = (
        pl.when(put_in_gate & (phase < 0))
          .then(  p.ALPHA_PUT_DOWN * neg_phase * put_sn * (p.TARGET_PUT - overall))
        .when(put_in_gate & (phase > 0))
          .then(- p.ALPHA_PUT_UP   * pos_phase * put_sn * (overall - p.TARGET_PUT_UP))
        .otherwise(0.0)
    )

    delta = call_delta + put_delta

    # Apply only when phase is not null
    new_overall = (
        pl.when(phase.is_null())
          .then(overall)
          .otherwise(overall + delta)
    )
    new_overall = new_overall.clip(0.0, 100.0).round(0)
    return df.with_columns(new_overall.cast(pl.Int64).alias('new_overall'))


def evaluate(df: pl.DataFrame, p: SWPMParams) -> dict:
    """Apply SWPM and compute W1/W2/W4/W5/W6 metrics.

    Returns dict with:
      - per-tier (cumulative) WR7 base/new/delta/N
      - per-discrete-bucket WR7 base/new/delta/N
      - W1 affected-cohort lift (calls/puts independently)
      - utility scalar
    """
    out = apply_swpm(df, p)

    metrics = {'tiers_call': [], 'tiers_put': [],
               'discrete_call': [], 'discrete_put': [],
               'W4_breaches': 0, 'W5_breaches': 0, 'W6_breaches': 0,
               'sec_phase_coverage': 0.0}

    metrics['sec_phase_coverage'] = out.select(pl.col('sec_phase').is_not_null().mean()).item()
    metrics['n_changed'] = int(out.filter(pl.col('overall') != pl.col('new_overall')).height)

    # Per-cumulative-tier WR7 (calls)
    call_lifts = []
    for thresh, label in CALL_TIERS:
        base = out.filter(pl.col('overall') >= thresh)
        new = out.filter(pl.col('new_overall') >= thresh)
        n_base = base.height
        n_new = new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        wr15_base = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_base else None
        wr15_new = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_new else None
        wr30_base = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_base else None
        wr30_new = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_new else None
        wr1_base = base.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_base else None
        wr1_new = new.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100
        delta15 = ((wr15_new or 0) - (wr15_base or 0)) * 100
        delta30 = ((wr30_new or 0) - (wr30_base or 0)) * 100
        delta1 = ((wr1_new or 0) - (wr1_base or 0)) * 100
        n_delta_pct = (n_new - n_base) / n_base * 100 if n_base else 0
        metrics['tiers_call'].append({
            'tier': label, 'thresh': thresh,
            'n_base': n_base, 'n_new': n_new, 'n_delta_pct': n_delta_pct,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
            'wr1_delta_pp': delta1, 'wr15_delta_pp': delta15, 'wr30_delta_pp': delta30,
        })
        call_lifts.append(delta)

    # Per-cumulative-tier WR7 (puts)
    put_lifts = []
    for thresh, label in PUT_TIERS:
        base = out.filter(pl.col('overall') <= thresh)
        new = out.filter(pl.col('new_overall') <= thresh)
        n_base = base.height
        n_new = new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        wr15_base = base.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_base else None
        wr15_new = new.select(pl.col('wr_15').cast(pl.Float64).mean()).item() if n_new else None
        wr30_base = base.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_base else None
        wr30_new = new.select(pl.col('wr_30').cast(pl.Float64).mean()).item() if n_new else None
        wr1_base = base.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_base else None
        wr1_new = new.select(pl.col('wr_1').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100
        delta15 = ((wr15_new or 0) - (wr15_base or 0)) * 100
        delta30 = ((wr30_new or 0) - (wr30_base or 0)) * 100
        delta1 = ((wr1_new or 0) - (wr1_base or 0)) * 100
        n_delta_pct = (n_new - n_base) / n_base * 100 if n_base else 0
        metrics['tiers_put'].append({
            'tier': label, 'thresh': thresh,
            'n_base': n_base, 'n_new': n_new, 'n_delta_pct': n_delta_pct,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
            'wr1_delta_pp': delta1, 'wr15_delta_pp': delta15, 'wr30_delta_pp': delta30,
        })
        put_lifts.append(delta)

    # W4 — per-discrete-bucket WR7 (must not regress > 0.5pp)
    for lo, hi, label in CALL_DISCRETE:
        base = out.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        new = out.filter((pl.col('new_overall') >= lo) & (pl.col('new_overall') <= hi))
        n_base = base.height
        n_new = new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100 if (wr_base is not None and wr_new is not None) else 0
        metrics['discrete_call'].append({
            'bucket': label, 'n_base': n_base, 'n_new': n_new,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
        })
        # W4 breach: tier loses >0.5pp WR7 AND has N >= 30
        if n_new >= 30 and delta < -0.5:
            metrics['W4_breaches'] += 1

    for lo, hi, label in PUT_DISCRETE:
        base = out.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
        new = out.filter((pl.col('new_overall') >= lo) & (pl.col('new_overall') <= hi))
        n_base = base.height
        n_new = new.height
        wr_base = base.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_base else None
        wr_new = new.select(pl.col('wr_7').cast(pl.Float64).mean()).item() if n_new else None
        delta = ((wr_new or 0) - (wr_base or 0)) * 100 if (wr_base is not None and wr_new is not None) else 0
        metrics['discrete_put'].append({
            'bucket': label, 'n_base': n_base, 'n_new': n_new,
            'wr7_base': wr_base, 'wr7_new': wr_new, 'wr7_delta_pp': delta,
        })
        if n_new >= 30 and delta < -0.5:
            metrics['W4_breaches'] += 1

    # W5 — N capacity floor (per-tier signals/year, calibrated against v46 baseline)
    # Floor table (per known-issues.md H6) approximated as 85% of base count
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

    # W6 — gradient preservation (cumulative WR7 must remain monotone-improving)
    wr7s_call_new = [t['wr7_new'] for t in metrics['tiers_call'] if t['wr7_new'] is not None]
    for i in range(len(wr7s_call_new) - 1):
        if wr7s_call_new[i] < wr7s_call_new[i+1] - 0.005:  # 95+ should >= 90+ (within 0.5pp tolerance)
            metrics['W6_breaches'] += 1

    # Utility — weighted call lift (compounding tiers heavily) + put lift
    # Penalize W4/W5/W6 breaches heavily.
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

    # Affected-cohort summary (the rows whose score actually changed)
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

    # Smoke test with default params
    p = SWPMParams()
    m = evaluate(coh, p)
    print(f'\nDefault SWPM smoke test:')
    print(f'  sec_phase coverage: {m["sec_phase_coverage"]*100:.1f}%')
    print(f'  rows changed: {m["n_changed"]:,}')
    print(f'  affected: {m["affected_n"]:,} (call={m["affected_call_n"]:,}  put={m["affected_put_n"]:,})')
    print(f'  W4 breaches: {m["W4_breaches"]}  W5: {m["W5_breaches"]}  W6: {m["W6_breaches"]}')
    print(f'  utility: {m["util"]:+.3f}')
    print(f'\n  CALL TIERS:')
    print(f'  {"tier":<5} {"n_base":>7} {"n_new":>7} {"n_Δ%":>7} {"wr7_b":>7} {"wr7_n":>7} {"Δpp":>7} {"Δ15":>7} {"Δ30":>7}')
    for t in m['tiers_call']:
        wr7_b = f'{t["wr7_base"]*100:.2f}' if t['wr7_base'] else '   --'
        wr7_n = f'{t["wr7_new"]*100:.2f}' if t['wr7_new'] else '   --'
        print(f'  {t["tier"]:<5} {t["n_base"]:>7,d} {t["n_new"]:>7,d} {t["n_delta_pct"]:>+6.1f}% {wr7_b:>7} {wr7_n:>7} {t["wr7_delta_pp"]:>+6.2f} {t["wr15_delta_pp"]:>+6.2f} {t["wr30_delta_pp"]:>+6.2f}')

    print(f'\n  PUT TIERS:')
    print(f'  {"tier":<5} {"n_base":>7} {"n_new":>7} {"n_Δ%":>7} {"wr7_b":>7} {"wr7_n":>7} {"Δpp":>7} {"Δ15":>7} {"Δ30":>7}')
    for t in m['tiers_put']:
        wr7_b = f'{t["wr7_base"]*100:.2f}' if t['wr7_base'] else '   --'
        wr7_n = f'{t["wr7_new"]*100:.2f}' if t['wr7_new'] else '   --'
        print(f'  {t["tier"]:<5} {t["n_base"]:>7,d} {t["n_new"]:>7,d} {t["n_delta_pct"]:>+6.1f}% {wr7_b:>7} {wr7_n:>7} {t["wr7_delta_pp"]:>+6.2f} {t["wr15_delta_pp"]:>+6.2f} {t["wr30_delta_pp"]:>+6.2f}')
