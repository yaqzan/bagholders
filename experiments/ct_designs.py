"""
Priority #8 follow-up — five gradient-gating designs for counter-trend capture.

Each design is a drop-in replacement for compute_overall_score. They all start
from the v21 baseline and add a different flavor of x_conf gating on `d`
(trend_dominance). v22 shipped `design='uniform'` with α=0.50 and regressed at
the portfolio level (see "v22 Portfolio-Level MC Regression" in CLAUDE.md):
uniform throttling killed bull-year TREND compounding (2024: −91% vs v19).

The five designs, recommended test order:

  D: 'asym_put'   — x_conf gate active only when pre-MACD weighted_sum < put_gate.
                    Calls keep TREND dominance at high `d`; puts get CT lift.
                    Closest precedent: asymmetric MACD gate (shipped v18).
  C: 'regime_cond' — gate active only when regime_multiplier < regime_cutoff.
                    Bull regimes pass through; caution/stress regimes apply gate.
  A: 'uniform'    — d_eff = d * x_conf**α.  Matches v22.  Softer α sweep.
  B: 'floored'    — d_eff = d * max(x_conf**α, floor).  Hard TREND floor.
  E: 'redistribute' — no d change.  At low x_conf, redistribute TREND weight to
                    RSI+MACD+Stoch proportional to their disagreement w/ TREND.

Public API:
    make_ct_score(design, **params) -> callable

Params per design (with defaults):
    D: alpha=0.50, put_gate=35.0, x_inputs='5comp'
    C: alpha=0.50, regime_cutoff=1.0, x_inputs='5comp'
    A: alpha=0.50, x_inputs='5comp'
    B: alpha=0.50, floor=0.60, x_inputs='5comp'
    E: alpha=0.50, cutoff=0.30, x_inputs='5comp'

x_conf formula (shared across designs):
    bull/bear = count components on each side of 50 (of x_inputs)
    agreement = max(bull, bear) / n
    sig_str   = |mean(s-50)/50|
    x_conf    = sig_str * agreement**2        ∈ [0, 1]

All other v19 mechanics (asymmetric MACD gate, asymmetric RSI zero, asymmetric
weekly, volume, regime, ext-focal, capitulation dampener) are preserved exactly
as in production. Only the trend-dominance weighting step is modified.
"""
from __future__ import annotations

import numpy as np

import database.utils.scoring as scoring_mod

_ORIGINAL = scoring_mod.compute_overall_score

PUT_MACD_GATE = 45.0   # production constant (v18)


def _x_conf(trend, bb, rsi, macd, stoch, x_inputs):
    if x_inputs == '5comp':
        comps = [trend, bb, rsi, macd, stoch]
    elif x_inputs == '3comp':
        comps = [bb, rsi, macd]
    elif x_inputs == '4comp_no_trend':
        comps = [bb, rsi, macd, stoch]
    else:
        raise ValueError(f"bad x_inputs: {x_inputs}")
    n = len(comps)
    bull = sum(1 for s in comps if s >= 50)
    bear = n - bull
    agreement = max(bull, bear) / n
    avg_dist = sum(s - 50 for s in comps) / n
    sig_str = min(abs(avg_dist) / 50.0, 1.0)
    return sig_str * (agreement ** 2)


def _trend_dominance_base(trend, bb_pct, rsi, macd):
    """Reproduces production td computation (before x_conf gate)."""
    trend_bias = float(np.tanh((trend - 50) * 0.06))
    trend_strength = abs(trend_bias)
    td = trend_strength ** 0.7

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0, -trend_bias)
        td *= (1.0 - 0.5 * (bull_ext + bear_ext))

    osc_avg = (rsi + macd) / 2.0
    bull_div = max(0.0, (40 - osc_avg) / 40.0) * max(0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60) / 40.0) * max(0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    td *= (1.0 - 0.7 * osc_divergence)
    return td, trend_strength


def make_ct_score(design: str, **params):
    """Return a compute_overall_score replacement for the given CT design.

    design: one of {'uniform', 'asym_put', 'regime_cond', 'floored', 'redistribute'}.
    """
    alpha = float(params.get('alpha', 0.50))
    x_inputs = params.get('x_inputs', '5comp')
    put_gate = float(params.get('put_gate', 35.0))           # design D
    regime_cutoff = float(params.get('regime_cutoff', 1.0))  # design C
    floor = float(params.get('floor', 0.60))                 # design B
    cutoff = float(params.get('cutoff', 0.30))               # design E: x_conf threshold
    redistrib_max = float(params.get('redistrib_max', 0.50)) # design E: max fraction of w_trend to redirect

    assert design in ('uniform', 'asym_put', 'regime_cond', 'floored', 'redistribute',
                      'floored_regime', 'mis_stress_call_softener')

    # Design 'mis_stress_call_softener' params:
    call_dampen = float(params.get('call_dampen', 0.25))   # 0..1; how strongly to soften
    # mis_stress is passed in per-call from simulator (precomputed per date).

    def fn(
        trend, bb, rsi, macd, stoch, technical_alignment,
        *,
        bb_pct=None,
        pct_from_ema50=None,
        ws_trend=None, ws_rsi=None, ws_macd=None,
        prev_ws_trend=None, prev_ws_rsi=None, prev_ws_macd=None,
        vol_mult=1.0, vol_raw=50, vol_sig='NEUTRAL', vol_mag=0.0,
        blend_w=0.0, vol_target=50.0,
        macdh_raw=None,
        regime_multiplier=None,
        put_regime_multiplier=None,
        mis_stress=0.0,
        **_extra,  # absorb future simulator kwargs without breaking
    ):
        if None in (trend, bb, rsi, stoch, macd, technical_alignment):
            return None, {}, None

        td, trend_strength = _trend_dominance_base(trend, bb_pct, rsi, macd)
        d = td

        x_conf = _x_conf(trend, bb, rsi, macd, stoch, x_inputs)

        # ── Compute naive weighted_sum WITHOUT gating (for design D's gate decision)
        # This mirrors production at α=0: baseline weights from unmodified `d`.
        def _apply_weights(d_eff_local, trend_, bb_, rsi_, macd_, stoch_, ta_):
            w_tr = 18 + 10 * d_eff_local
            w_bb_ = 18
            w_rs = 25 - 9 * d_eff_local
            w_md = 25 - 6 * d_eff_local
            w_st = 5
            w_ta = 9 + 6 * d_eff_local
            return w_tr, w_bb_, w_rs, w_md, w_st, w_ta

        # Baseline weights (no gating yet)
        w_tr0, w_bb0, w_rs0, w_md0, w_st0, w_ta0 = _apply_weights(
            d, trend, bb, rsi, macd, stoch, technical_alignment
        )
        base_sum = 50 + (
            (trend - 50) * w_tr0 +
            (bb - 50) * w_bb0 +
            (rsi - 50) * w_rs0 +
            (stoch - 50) * w_st0 +
            (macd - 50) * w_md0 +
            (technical_alignment - 50) * w_ta0
        ) / 100

        # ── Select d_eff per design ──────────────────────────────────────
        d_eff = d
        w_redistrib = None  # design E: (extra_rsi, extra_macd, extra_stoch) redirected from TREND

        if design == 'uniform':
            if alpha > 0:
                d_eff = d * (x_conf ** alpha)
        elif design == 'asym_put':
            # Only gate when pre-gate weighted sum is bearish enough.
            if base_sum < put_gate and alpha > 0:
                d_eff = d * (x_conf ** alpha)
        elif design == 'regime_cond':
            # Only gate when regime is stressed (multiplier < cutoff).
            # Use put_regime_multiplier if bearish (base_sum < 50), else regime_multiplier.
            eff_mult = put_regime_multiplier if (base_sum < 50 and put_regime_multiplier is not None) else regime_multiplier
            if eff_mult is not None and eff_mult < regime_cutoff and alpha > 0:
                d_eff = d * (x_conf ** alpha)
        elif design == 'floored':
            if alpha > 0:
                gate_mult = max(x_conf ** alpha, floor)
                d_eff = d * gate_mult
        elif design == 'floored_regime':
            # Hybrid: apply floored gate only when regime is stressed.
            # Bull/neutral regimes pass through (preserves bull-year TREND compounding).
            eff_mult = put_regime_multiplier if (base_sum < 50 and put_regime_multiplier is not None) else regime_multiplier
            if eff_mult is not None and eff_mult < regime_cutoff and alpha > 0:
                gate_mult = max(x_conf ** alpha, floor)
                d_eff = d * gate_mult
        elif design == 'redistribute':
            # No d change. At low x_conf, redirect a fraction of w_trend to
            # contradicting components. The fraction scales linearly from 0
            # (at x_conf >= cutoff) up to redistrib_max (at x_conf = 0).
            if x_conf < cutoff:
                frac = (cutoff - x_conf) / cutoff * redistrib_max
                # Determine contradicting components (those on opposite side of TREND).
                trend_bias_sign = 1 if trend >= 50 else -1
                # Each of RSI, MACD, Stoch: sign vs 50 opposed to trend_bias_sign → "contradicting"
                contra_rsi   = (rsi - 50) * -trend_bias_sign
                contra_macd  = (macd - 50) * -trend_bias_sign
                contra_stoch = (stoch - 50) * -trend_bias_sign
                positives = [max(0.0, x) for x in (contra_rsi, contra_macd, contra_stoch)]
                total_pos = sum(positives)
                if total_pos > 0:
                    w_redistrib = (
                        frac * positives[0] / total_pos,   # extra to RSI
                        frac * positives[1] / total_pos,
                        frac * positives[2] / total_pos,
                    )

        # ── Final weights ─────────────────────────────────────────────────
        w_trend = 18 + 10 * d_eff
        w_bb    = 18
        w_rsi   = 25 - 9 * d_eff
        w_macd  = 25 - 6 * d_eff
        w_stoch = 5
        w_ta    = 9 + 6 * d_eff

        if w_redistrib is not None:
            extra_rsi, extra_macd, extra_stoch = w_redistrib
            total_frac = extra_rsi + extra_macd + extra_stoch
            if total_frac > 0:
                transfer = total_frac * w_trend
                w_trend -= transfer
                w_rsi   += transfer * (extra_rsi   / total_frac)
                w_macd  += transfer * (extra_macd  / total_frac)
                w_stoch += transfer * (extra_stoch / total_frac)

        # ── Asymmetric MACD gate (unchanged) ──────────────────────────────
        _scale_no_macd = 100.0 / (100.0 - w_macd)
        _pre_no_macd = 50 + (
            (trend               - 50) * w_trend +
            (bb                  - 50) * w_bb +
            (rsi                 - 50) * w_rsi +
            (stoch               - 50) * w_stoch +
            (technical_alignment - 50) * w_ta
        ) * _scale_no_macd / 100
        if _pre_no_macd < PUT_MACD_GATE:
            w_trend *= _scale_no_macd
            w_bb    *= _scale_no_macd
            w_rsi   *= _scale_no_macd
            w_stoch *= _scale_no_macd
            w_ta    *= _scale_no_macd
            w_macd = 0.0

        # ── Asymmetric RSI zero (unchanged) ───────────────────────────────
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
            (trend  - 50) * w_trend +
            (bb     - 50) * w_bb +
            (rsi    - 50) * w_rsi +
            (stoch  - 50) * w_stoch +
            (macd   - 50) * w_macd +
            (technical_alignment - 50) * w_ta
        ) / 100

        # ── Weekly adjustment (via module — asymmetric scaling lives there) ──
        weekly_detail = None
        if None not in (ws_rsi, ws_macd):
            adj, weekly_detail = scoring_mod.calculate_weekly_adjustment(
                ws_trend, ws_rsi, ws_macd,
                prev_ws_trend, prev_ws_rsi, prev_ws_macd,
            )
            weighted_sum += adj

        # ── Volume amplification (unchanged) ──────────────────────────────
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

        pre_regime_overall = int(max(0, min(100, weighted_sum)))

        # ── Regime adjustment ─────────────────────────────────────────────
        overall = pre_regime_overall
        _eff_mult = (put_regime_multiplier
                     if (overall < 50 and put_regime_multiplier is not None)
                     else regime_multiplier)

        # Design 'mis_stress_call_softener': on detected mis-stress days
        # (narrow-bull mislabeled as STRESS), pull the call-side multiplier
        # toward 1.0 by (mis_stress * call_dampen). Preserves regime mechanics
        # on real-stress days (mis_stress=0) and on puts.
        if (design == 'mis_stress_call_softener'
                and overall >= 50
                and mis_stress > 0
                and _eff_mult is not None):
            _eff_mult = 1.0 + (_eff_mult - 1.0) * (1.0 - mis_stress * call_dampen)

        if _eff_mult is not None and _eff_mult != 1.0:
            if overall >= 50:
                adjusted = 50 + (overall - 50) * _eff_mult
            else:
                adjusted = 50 + (overall - 50) * (2.0 - _eff_mult)
            overall = int(max(0, min(100, round(adjusted))))

        # ── Post-regime dampeners (unchanged) ─────────────────────────────
        if overall == 0 and pct_from_ema50 is not None and pct_from_ema50 < -10.0:
            ext = abs(pct_from_ema50)
            overall = min(20, int(round(5.0 + (ext - 10.0))))

        if overall <= 9 and macdh_raw is not None:
            score_weight = (9 - overall) / 9.0
            mh_pos = max(0.0, float(np.tanh(macdh_raw / 0.05)))
            _exh = score_weight * mh_pos * 0.5
            if _exh > 0.0:
                overall = int(round(overall * (1 - _exh) + 10.0 * _exh))

        if overall <= 25 and pct_from_ema50 is not None and pct_from_ema50 > 0.0:
            ext_ramp = min(1.0, pct_from_ema50 / 10.0)
            score_weight = (25 - overall) / 25.0
            _ext = 0.5 * ext_ramp * score_weight
            if _ext > 0.0:
                lift = _ext * (25 - overall)
                overall = int(min(100, round(overall + lift)))

        weight_info = {
            'trend': round(w_trend, 1), 'bb': round(w_bb, 1),
            'rsi': round(w_rsi, 1), 'macd': round(w_macd, 1),
            'stoch': round(w_stoch, 1), 'ta': round(w_ta, 1),
            'td': round(td, 3),
            'd_eff': round(d_eff, 3),
            'x_conf': round(x_conf, 3),
            'design': design,
            'pre_regime': pre_regime_overall,
        }
        if weekly_detail:
            weight_info.update(weekly_detail)

        vol_update = {'volume': vol_raw, 'volume_signal': vol_sig, 'volume_magnitude': vol_mag} \
            if vol_sig != 'NEUTRAL' else None
        return overall, weight_info, vol_update

    fn._design = design
    fn._params = dict(params)
    return fn
