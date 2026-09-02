"""
A/B simulation: test two v11->v14 changes in isolation and combined.
Window: 2022 (365d lookback). Primary metric: WR15 (matches 30DTE hard-sell day 15).

Variants:
  baseline : current v14 (momentum gradient ON, MAX_AMP=0.50)
  A        : remove momentum confirmation gradient (back to v11 behavior)
  B        : revert MAX_AMPLIFICATION 0.55->0.50 (back to v11 value)
  AB       : both A and B together
"""

import numpy as np
import volume_amplifier as va
from database.utils.scoring import compute_overall_score
from simulator import ScoreSimulator

LOOKBACK = 1825


def _pct_ema50(raw_price, raw_ema50):
    if raw_price and raw_ema50 and float(raw_ema50) != 0:
        return (float(raw_price) - float(raw_ema50)) / float(raw_ema50) * 100
    return None


def _compute_no_mc(trend, bb, rsi, macd, stoch, technical_alignment, *,
                   bb_pct=None, pct_from_ema50=None,
                   ws_trend=None, ws_rsi=None, ws_macd=None,
                   prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
                   vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
                   blend_w=0.0, vol_target=50.0,
                   macdh_raw=None, regime_multiplier=None):
    """compute_overall_score with momentum confirmation gradient removed."""
    from database.utils.scoring import calculate_weekly_adjustment

    if None in (trend, bb, rsi, stoch, macd, technical_alignment):
        return None

    trend_bias      = float(np.tanh((trend - 50) * 0.06))
    trend_strength  = abs(trend_bias)
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

    # MC gradient intentionally omitted here

    d = trend_dominance
    w_trend = 18 + 10 * d
    w_bb    = 18
    w_rsi   = 25 -  9 * d
    w_macd  = 25 -  6 * d
    w_stoch =  5
    w_ta    =  9 +  6 * d

    _pre_rsi = 50 + (
        (trend               - 50) * w_trend +
        (bb                  - 50) * w_bb +
        (macd                - 50) * w_macd +
        (stoch               - 50) * w_stoch +
        (technical_alignment - 50) * w_ta
    ) / 100
    if _pre_rsi < 40:
        rsi = 50

    weighted_sum = 50 + (
        ((trend  - 50) * w_trend) +
        ((bb     - 50) * w_bb) +
        ((rsi    - 50) * w_rsi) +
        ((stoch  - 50) * w_stoch) +
        ((macd   - 50) * w_macd) +
        ((technical_alignment - 50) * w_ta)
    ) / 100

    if None not in (ws_rsi, ws_macd):
        adj, _ = calculate_weekly_adjustment(
            ws_trend, ws_rsi, ws_macd,
            prev_ws_trend, prev_ws_rsi, prev_ws_macd,
        )
        weighted_sum += adj

    vol_boost = 1.0 + 0.6 * trend_strength
    if pct_from_ema50 is not None:
        ext = abs(pct_from_ema50)
        if ext > 15.0:
            ema_damp = 1.0 - 0.5 * min(1.0, (ext - 15.0) / 25.0)
            vol_boost *= ema_damp

    if blend_w > 0:
        blend_w = min(blend_w * vol_boost, 0.60)
        if abs(weighted_sum - 50) < 25:
            if (vol_target < 35 and macd > 60) or (vol_target > 65 and macd < 40):
                blend_w *= 0.50
        weighted_sum = weighted_sum * (1 - blend_w) + vol_target * blend_w
    elif vol_mult != 1.0:
        if vol_sig == 'CONVICTION':
            if (weighted_sum < 35 and macd > 60) or (weighted_sum > 65 and macd < 40):
                vol_mult = 1.0 + (vol_mult - 1.0) * 0.25
        deviation = (vol_mult - 1.0) * vol_boost
        weighted_sum = 50 + (weighted_sum - 50) * (1.0 + deviation)

    pre_regime = int(max(0, min(100, weighted_sum)))
    overall = pre_regime
    if regime_multiplier is not None and regime_multiplier != 1.0:
        if overall >= 50:
            adjusted = 50 + (overall - 50) * regime_multiplier
        else:
            adjusted = 50 + (overall - 50) * (2.0 - regime_multiplier)
        overall = int(max(0, min(100, round(adjusted))))
    return overall


def make_scoring_fn(remove_mc_gradient=False, max_amplification=0.50):
    _orig_max_amp = va.MAX_AMPLIFICATION

    def fn(trend, bb, rsi, macd, stoch, ta, *,
           bb_pct=None,
           ws_trend=None, ws_rsi=None, ws_macd=None,
           prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
           vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
           blend_w=0.0, vol_target=50.0,
           raw_macd_hist=None, raw_price=None, raw_ema50=None,
           regime_multiplier=None,
           **_ignored):

        va.MAX_AMPLIFICATION = max_amplification
        macdh = float(raw_macd_hist) if raw_macd_hist is not None else None
        ema_pct = _pct_ema50(raw_price, raw_ema50)

        if remove_mc_gradient:
            overall = _compute_no_mc(
                trend, bb, rsi, macd, stoch, ta,
                bb_pct=bb_pct, pct_from_ema50=ema_pct,
                ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
                prev_ws_trend=prev_ws_trend, prev_ws_rsi=prev_ws_rsi, prev_ws_macd=prev_ws_macd,
                vol_mult=vol_mult, vol_raw=vol_raw, vol_sig=vol_sig, vol_mag=vol_mag,
                blend_w=blend_w, vol_target=vol_target,
                macdh_raw=macdh, regime_multiplier=regime_multiplier,
            )
        else:
            overall, _, _ = compute_overall_score(
                trend, bb, rsi, macd, stoch, ta,
                bb_pct=bb_pct, pct_from_ema50=ema_pct,
                ws_trend=ws_trend, ws_rsi=ws_rsi, ws_macd=ws_macd,
                prev_ws_trend=prev_ws_trend, prev_ws_rsi=prev_ws_rsi, prev_ws_macd=prev_ws_macd,
                vol_mult=vol_mult, vol_raw=vol_raw, vol_sig=vol_sig, vol_mag=vol_mag,
                blend_w=blend_w, vol_target=vol_target,
                macdh_raw=macdh, regime_multiplier=regime_multiplier,
            )

        va.MAX_AMPLIFICATION = _orig_max_amp
        return overall

    return fn


VARIANTS = [
    ('baseline (v14)',           make_scoring_fn(remove_mc_gradient=False, max_amplification=0.50)),
    ('A: no MC gradient',        make_scoring_fn(remove_mc_gradient=True,  max_amplification=0.50)),
    ('B: MAX_AMP=0.55',          make_scoring_fn(remove_mc_gradient=False, max_amplification=0.55)),
    ('AB: no MC + MAX_AMP=0.55', make_scoring_fn(remove_mc_gradient=True,  max_amplification=0.55)),
]

SHOW_BUCKETS = ['70+', '75+', '80+', '85+', '90+', '95+', '<25', '<15']


def run_variant(label, sfn):
    from collections import defaultdict
    from datetime import date, timedelta
    from assess_scores import extract_peaks, assess_peaks_in_memory
    from database.models.core import AlgorithmVersion
    from database.models.technical import PriceHistory
    import simulator as _sim_mod

    print('\n' + '-'*90)
    print('VARIANT: ' + label)
    print('-'*90)

    sim = ScoreSimulator(lookback_days=LOOKBACK, scoring_fn=sfn)
    scores = sim.simulate()

    cutoff = date.today() - timedelta(days=LOOKBACK)
    active_ver = AlgorithmVersion.get_active_scores_version()

    # DB peaks
    db_peaks = extract_peaks(lookback_days=LOOKBACK, version=active_ver)
    loaded_syms = set(sim._contexts.keys())
    db_peaks = [p for p in db_peaks if p.symbol_id in loaded_syms]

    # Sim peaks
    by_sym = defaultdict(list)
    for (sym, d), score in scores.items():
        if d >= cutoff and (score >= 70 or score <= 25):
            by_sym[sym].append((d, score))

    _FakePeak = _sim_mod._FakePeak
    sim_peaks = []
    for sym, entries in by_sym.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1]-50), reverse=True):
            if (sym, d) in pruned:
                continue
            sim_peaks.append(_FakePeak(sym, d, score))
            for direction in (-1, 1):
                w = d + timedelta(days=direction)
                while w in date_set and (sym, w) not in pruned:
                    pruned.add((sym, w))
                    w += timedelta(days=direction)

    print('DB peaks: %d  Sim peaks: %d' % (len(db_peaks), len(sim_peaks)))

    all_syms = list({p.symbol_id for p in db_peaks} | {p.symbol_id for p in sim_peaks})
    all_ph = list(
        PriceHistory.select()
        .where(PriceHistory.symbol.in_(all_syms), PriceHistory.date >= cutoff)
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .namedtuples()
    )
    ph_by_sym = defaultdict(list)
    for r in all_ph:
        ph_by_sym[r.symbol].append(r)

    db_data  = assess_peaks_in_memory(db_peaks,  ph_by_sym, LOOKBACK)
    sim_data = assess_peaks_in_memory(sim_peaks, ph_by_sym, LOOKBACK)

    db_b  = db_data['bucketed_stats']
    sim_b = sim_data['bucketed_stats']

    def g(d, k):
        v = d.get(k)
        return v if v is not None else float('nan')

    def fmt(v):
        return '%7.1f' % v if v == v else '    nan'

    def delta(old, new):
        if old != old or new != new:
            return '  nan'
        d = new - old
        return ('%+.1f' % d)

    print('%6s | %5s %5s | %7s %7s %6s | %7s %7s %6s | %7s %7s' % (
        'Bucket', 'DB_N', 'SIM_N',
        'WR15_DB', 'WR15_SIM', 'dWR15',
        'WR30_DB', 'WR30_SIM', 'dWR30',
        'Ret15_DB', 'Ret15_SIM'))
    print('-'*88)

    for bkt in SHOW_BUCKETS:
        db = db_b.get(bkt, {})
        s  = sim_b.get(bkt, {})
        db_n  = db.get('sample_count') or 0
        sim_n = s.get('sample_count') or 0
        if db_n == 0 and sim_n == 0:
            continue

        wr15_db  = g(db, 'win_rate_15d')
        wr15_sim = g(s,  'win_rate_15d')
        wr30_db  = g(db, 'win_rate_30d')
        wr30_sim = g(s,  'win_rate_30d')
        r15_db   = g(db, 'avg_return_15d')
        r15_sim  = g(s,  'avg_return_15d')

        print('%6s | %5d %5d | %s %s %6s | %s %s %6s | %s %s' % (
            bkt, db_n, sim_n,
            fmt(wr15_db), fmt(wr15_sim), delta(wr15_db, wr15_sim),
            fmt(wr30_db), fmt(wr30_sim), delta(wr30_db, wr30_sim),
            fmt(r15_db), fmt(r15_sim)))


print('='*90)
print('A/B Simulation - 2022 window (%dd lookback)' % LOOKBACK)
print('Primary metric: WR15 (30DTE options, hard sell day 15)')
print('='*90)

for label, sfn in VARIANTS:
    run_variant(label, sfn)
