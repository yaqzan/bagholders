"""
Put pipeline sensitivity decomposition.

For each put signal (final overall <= 25), record the score at every pipeline
stage and attribute "distance from 50" to each stage's contribution:

  Stage 1 (components)  : base_ws             (post component weights, post MACD gate, post RSI zero)
  Stage 2 (weekly)      : post_weekly         (= base_ws + weekly_adj)
  Stage 3 (volume)      : post_volume         (multiplicative/blend applied)
  Stage 4 (clamp)       : pre_regime          (= max(0, min(100, post_volume)))
  Stage 5 (regime)      : post_regime         (put-mirror applied)
  Stage 6 (dampeners)   : overall             (capitulation, exhaustion, ext-focal)

Reports per bucket (<25, <15, <5, =0):
  - Mean deviation contributed by each stage
  - Share of signals that would already be <=25 at each stage
  - Where the score "crosses" each threshold (distribution)

Run from repo root:
  python -m experiments.put_pipeline_sensitivity --days 1825
"""
from __future__ import annotations
import argparse
import numpy as np
from collections import defaultdict

from database.utils import scoring as scoring_mod
from database.models.core import AlgorithmVersion
from simulator import ScoreSimulator


_orig_compute = scoring_mod.compute_overall_score
_log = []  # list of per-signal stage dicts


def compute_overall_score_profiled(
    trend, bb, rsi, macd, stoch, technical_alignment,
    *,
    bb_pct=None, pct_from_ema50=None,
    ws_trend=None, ws_rsi=None, ws_macd=None,
    prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
    vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
    blend_w=0.0, vol_target=50.0,
    macdh_raw=None,
    regime_multiplier=None,
    put_regime_multiplier=None,
):
    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None, {}, None

    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
    trend_dominance = trend_strength ** 0.7
    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        trend_dominance *= (1.0 - 0.5 * (bull_ext + bear_ext))
    osc_avg = (rsi + macd) / 2.0
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= (1.0 - 0.7 * osc_divergence)
    d = trend_dominance

    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 - 9 * d
    w_macd  = 25 - 6 * d
    w_stoch = 5
    w_ta    = 9 + 6 * d

    PUT_MACD_GATE = 45.0
    _scale_no_macd = 100.0 / (100.0 - w_macd)
    _pre_no_macd = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb +
        (rsi - 50) * w_rsi + (stoch - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) * _scale_no_macd / 100
    macd_gated = _pre_no_macd < PUT_MACD_GATE
    if macd_gated:
        w_trend *= _scale_no_macd
        w_bb    *= _scale_no_macd
        w_rsi   *= _scale_no_macd
        w_stoch *= _scale_no_macd
        w_ta    *= _scale_no_macd
        w_macd   = 0.0

    _pre_rsi = 50 + (
        (trend - 50) * w_trend + (bb - 50) * w_bb +
        (macd - 50) * w_macd + (stoch - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    rsi_zeroed = _pre_rsi < 40
    if rsi_zeroed:
        rsi = 50

    base_ws = 50 + (
        ((trend - 50) * w_trend) + ((bb - 50) * w_bb) +
        ((rsi - 50) * w_rsi) + ((stoch - 50) * w_stoch) +
        ((macd - 50) * w_macd) + ((technical_alignment - 50) * w_ta)
    ) / 100

    weighted_sum = base_ws
    w_adj = 0.0
    if None not in (ws_rsi, ws_macd):
        adj, _ = scoring_mod.calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        w_adj = adj
        weighted_sum += adj

    post_weekly = weighted_sum

    vol_boost = 1.0 + 0.6 * trend_strength
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > 15.0:
            ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w_eff = min(blend_w * vol_boost, 0.60)
        if abs(weighted_sum - 50) < 25:
            if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                blend_w_eff *= 0.50
        weighted_sum = weighted_sum * (1 - blend_w_eff) + vol_target * blend_w_eff
    elif vol_mult != 1.0:
        if vol_sig == 'CONVICTION':
            if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    post_volume = weighted_sum
    pre_regime = int(max(0, min(100, weighted_sum)))

    overall = pre_regime
    _eff_mult = (put_regime_multiplier
                 if (overall < 50 and put_regime_multiplier is not None)
                 else regime_multiplier)
    if _eff_mult is not None and _eff_mult != 1.0:
        if overall >= 50:
            adjusted = 50 + (overall - 50) * _eff_mult
        else:
            adjusted = 50 + (overall - 50) * (2.0 - _eff_mult)
        overall = int(max(0, min(100, round(adjusted))))
    post_regime = overall

    cap_dampened = False
    if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
        ext = abs(pct_from_ema50)
        overall = min(20, int(round(5.0 + (ext - 10.0))))
        cap_dampened = True

    exh_damp = 0.0
    if overall <= 9 and macdh_raw is not None:
        score_weight = (9 - overall) / 9.0
        mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))
        exh_damp = score_weight * mh_pos * 0.5
        if exh_damp > 0.0:
            overall = int(round(overall * (1 - exh_damp) + 10.0 * exh_damp))

    ext_damp = 0.0
    if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
        ext_ramp = min(1.0, pct_from_ema50 / 10.0)
        score_weight = (25 - overall) / 25.0
        ext_damp = 0.5 * ext_ramp * score_weight
        if ext_damp > 0.0:
            lift = ext_damp * (25 - overall)
            overall = int(min(100, round(overall + lift)))

    if overall <= 30:
        _log.append({
            'base_ws':      float(base_ws),
            'post_weekly':  float(post_weekly),
            'post_volume':  float(post_volume),
            'pre_regime':   int(pre_regime),
            'post_regime':  int(post_regime),
            'overall':      int(overall),
            'w_adj':        float(w_adj),
            'vol_sig':      str(vol_sig),
            'vol_mult':     float(vol_mult),
            'blend_w':      float(blend_w),
            'eff_mult':     float(_eff_mult) if _eff_mult is not None else 1.0,
            'macd_gated':   bool(macd_gated),
            'rsi_zeroed':   bool(rsi_zeroed),
            'ext':          float(pct_from_ema50) if pct_from_ema50 is not None else None,
            'cap_dampened': bool(cap_dampened),
            'exh_damp':     float(exh_damp),
            'ext_damp':     float(ext_damp),
        })

    # Return stage values through normal contract (simulator only reads overall)
    weight_info = {'pre_regime': pre_regime}
    return overall, weight_info, None


def bucket(overall):
    if overall == 0: return '= 0'
    if overall <= 4: return '1-4'
    if overall <= 9: return '5-9'
    if overall <= 14: return '10-14'
    if overall <= 19: return '15-19'
    if overall <= 25: return '20-25'
    return '26-30'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=1825)
    parser.add_argument('--symbols', nargs='*', default=None)
    args = parser.parse_args()

    active = AlgorithmVersion.get_active_scores_version()
    print(f"Active DB version: v{active.id} ({active.git_commit[:8]})")
    print(f"Lookback: {args.days}d | Symbols: {'ALL' if not args.symbols else len(args.symbols)}")
    print("=" * 100)

    scoring_mod.compute_overall_score = compute_overall_score_profiled
    sim = ScoreSimulator(symbols=args.symbols, lookback_days=args.days)
    sim.simulate()
    scoring_mod.compute_overall_score = _orig_compute
    print(f"Profiled {len(_log)} signals with overall <= 30")

    # Bucket assignment by FINAL overall
    by_bucket = defaultdict(list)
    for rec in _log:
        by_bucket[bucket(rec['overall'])].append(rec)

    buckets = ['= 0', '1-4', '5-9', '10-14', '15-19', '20-25', '26-30']

    # --- Stage contribution analysis ---
    # Each stage's contribution to "distance below 50" on the final overall:
    #   d_components = 50 - base_ws             (positive = bearish contribution)
    #   d_weekly     = base_ws - post_weekly
    #   d_volume     = post_weekly - post_volume
    #   d_clamp      = post_volume - pre_regime (positive only when post_volume<0)
    #   d_regime     = pre_regime - post_regime
    #   d_dampeners  = post_regime - overall    (negative = dampener LIFTED the score)
    # Sum = 50 - overall

    print("\n" + "=" * 100)
    print("STAGE CONTRIBUTIONS TO (50 - overall), mean per bucket")
    print("=" * 100)
    hdr = f"{'bucket':>8} {'N':>6}  {'compon':>7} {'weekly':>7} {'volume':>7} {'clamp':>6} {'regime':>7} {'damp':>6}  {'TOTAL':>7}"
    print(hdr)
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        d_comp   = np.mean([50.0 - r['base_ws']               for r in rs])
        d_weekly = np.mean([r['base_ws'] - r['post_weekly']   for r in rs])
        d_volume = np.mean([r['post_weekly'] - r['post_volume'] for r in rs])
        d_clamp  = np.mean([r['post_volume'] - r['pre_regime'] for r in rs])
        d_regime = np.mean([r['pre_regime'] - r['post_regime'] for r in rs])
        d_damp   = np.mean([r['post_regime'] - r['overall']    for r in rs])
        total    = d_comp + d_weekly + d_volume + d_clamp + d_regime + d_damp
        print(f"{b:>8} {n:>6}  {d_comp:>7.1f} {d_weekly:>7.1f} {d_volume:>7.1f} {d_clamp:>6.1f} {d_regime:>7.1f} {d_damp:>6.1f}  {total:>7.1f}")

    # --- "Would this signal already be <=25 at stage X?" ---
    print("\n" + "=" * 100)
    print("CROSS-THRESHOLD: share of final-bucket rows that were already <=25 at each stage")
    print("=" * 100)
    hdr = f"{'bucket':>8} {'N':>6}  {'base_ws':>9} {'post_wk':>9} {'post_vol':>9} {'pre_reg':>9} {'post_reg':>9}"
    print(hdr)
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        p_bws = sum(1 for r in rs if r['base_ws'] <= 25) / n * 100
        p_pwk = sum(1 for r in rs if r['post_weekly'] <= 25) / n * 100
        p_pvl = sum(1 for r in rs if r['post_volume'] <= 25) / n * 100
        p_prg = sum(1 for r in rs if r['pre_regime'] <= 25) / n * 100
        p_pst = sum(1 for r in rs if r['post_regime'] <= 25) / n * 100
        print(f"{b:>8} {n:>6}  {p_bws:>8.1f}% {p_pwk:>8.1f}% {p_pvl:>8.1f}% {p_prg:>8.1f}% {p_pst:>8.1f}%")

    # --- Volume signal distribution per bucket ---
    print("\n" + "=" * 100)
    print("VOLUME SIGNAL DISTRIBUTION per bucket")
    print("=" * 100)
    print(f"{'bucket':>8} {'N':>6}   NEUT  CONVIC  ABSORP  REJECT  CLIMAX  THINAIR")
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        def pct(sig): return sum(1 for r in rs if r['vol_sig'] == sig) / n * 100
        print(f"{b:>8} {n:>6}  "
              f"{pct('NEUTRAL'):>5.1f}%  {pct('CONVICTION'):>5.1f}%  "
              f"{pct('ABSORPTION'):>5.1f}%  {pct('REJECTION'):>5.1f}%  "
              f"{pct('CLIMAX'):>5.1f}%  {pct('THIN_AIR'):>5.1f}%")

    # --- Regime distribution per bucket ---
    print("\n" + "=" * 100)
    print("EFFECTIVE REGIME MULTIPLIER per bucket (put-mirror = (2 - mult))")
    print("=" * 100)
    print(f"{'bucket':>8} {'N':>6}   mean_mult  mean_mirror   share_stressed(mirror>1.10)")
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        mults = [r['eff_mult'] for r in rs]
        mirrors = [2.0 - m for m in mults]
        mean_mult = np.mean(mults)
        mean_mirror = np.mean(mirrors)
        p_stressed = sum(1 for m in mirrors if m > 1.10) / n * 100
        print(f"{b:>8} {n:>6}    {mean_mult:>7.3f}     {mean_mirror:>7.3f}       {p_stressed:>5.1f}%")

    # --- Dampener coverage per bucket ---
    print("\n" + "=" * 100)
    print("DAMPENER FIRE RATES per bucket")
    print("=" * 100)
    print(f"{'bucket':>8} {'N':>6}   cap_damp%   exh_damp>0%   ext_damp>0%")
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        p_cap = sum(1 for r in rs if r['cap_dampened']) / n * 100
        p_exh = sum(1 for r in rs if r['exh_damp'] > 0) / n * 100
        p_ext = sum(1 for r in rs if r['ext_damp'] > 0) / n * 100
        print(f"{b:>8} {n:>6}    {p_cap:>5.1f}%        {p_exh:>5.1f}%        {p_ext:>5.1f}%")

    # --- Gate rates per bucket ---
    print("\n" + "=" * 100)
    print("UPSTREAM GATE FIRE RATES per bucket")
    print("=" * 100)
    print(f"{'bucket':>8} {'N':>6}   macd_gated%   rsi_zeroed%")
    for b in buckets:
        rs = by_bucket[b]
        if not rs:
            continue
        n = len(rs)
        p_m = sum(1 for r in rs if r['macd_gated']) / n * 100
        p_r = sum(1 for r in rs if r['rsi_zeroed']) / n * 100
        print(f"{b:>8} {n:>6}    {p_m:>5.1f}%         {p_r:>5.1f}%")


if __name__ == '__main__':
    main()
