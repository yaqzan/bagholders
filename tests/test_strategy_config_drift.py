"""Drift-guard: every shipped strategy constant must match strategy_config.

Runs in <1s. Fails loudly if a future-Claude (or human) edits a constant in
monte_carlo.py / monte_carlo_15dte.py / backtest_cascade.py /
backtest_cascade_15dte.py without updating strategy_config.py — or vice
versa. The whole point of strategy_config is to be the single source of
truth, so any divergence here is a bug.

Also scans production source code for hardcoded numeric/boolean literals assigned
to known strategy parameter names and ticker-specific scoring/config surfaces.
Catches function-local drift and symbol-case leakage that import-based checks
miss.

Run via: `python tests/test_strategy_config_drift.py`
"""
from __future__ import annotations

import math
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _approx(a, b, tol=1e-9):
    return math.isclose(float(a), float(b), abs_tol=tol)


def check_30dte():
    """Verify monte_carlo.py + backtest_cascade.py mirror STRATEGY_30DTE."""
    import strategy_config as cfg
    import monte_carlo as mc
    import backtest_cascade as bc

    s = cfg.STRATEGY_30DTE
    o = s.option

    # ─── monte_carlo.py module-level constants ─────────────────────────
    pairs_mc = [
        # (name, observed, expected)
        ('HOLD_DAYS',                mc.HOLD_DAYS,                s.HOLD_DAYS),
        ('CALENDAR_HOLD',            mc.CALENDAR_HOLD,            s.CALENDAR_HOLD),
        ('HOLD_CAL_DAYS',            mc.HOLD_CAL_DAYS,            s.HOLD_CAL_DAYS),
        ('NOMINAL_CAL_DTE',          mc.NOMINAL_CAL_DTE,          s.NOMINAL_CAL_DTE),
        ('PREMIUM_MULT',             mc.PREMIUM_MULT,             s.PREMIUM_MULT),
        ('DELTA',                    mc.DELTA,                    o.DELTA),
        ('TP_BASE',                  mc.TP_BASE,                  o.TP_BASE),
        ('TP_STRESS',                mc.TP_STRESS,                o.TP_STRESS),
        ('SL_BASE',                  mc.SL_BASE,                  o.SL_BASE),
        ('SL_STRESS',                mc.SL_STRESS,                o.SL_STRESS),
        ('HARD_SELL_LOSS',           mc.HARD_SELL_LOSS,           s.HARD_SELL_LOSS),
        ('BREADTH_THRESHOLD',        mc.BREADTH_THRESHOLD,        o.BREADTH_THRESHOLD),
        ('SLIP_ENTRY',               mc.SLIP_ENTRY,               o.SLIP_ENTRY),
        ('SLIP_TP',                  mc.SLIP_TP,                  o.SLIP_TP),
        ('SLIP_SL',                  mc.SLIP_SL,                  o.SLIP_SL),
        ('SLIP_HARD',                mc.SLIP_HARD,                o.SLIP_HARD),
        # Derived
        ('NET_TP_BASE',              mc.NET_TP_BASE,              o.NET_TP_BASE),
        ('NET_TP_STRESS',            mc.NET_TP_STRESS,            o.NET_TP_STRESS),
        ('NET_SL_BASE',              mc.NET_SL_BASE,              o.NET_SL_BASE),
        ('NET_SL_STRESS',            mc.NET_SL_STRESS,            o.NET_SL_STRESS),
        ('NET_HARD_SELL',            mc.NET_HARD_SELL,            s.NET_HARD_SELL),
        ('TP_SIGMA_BASE',            mc.TP_SIGMA_BASE,            s.TP_SIGMA_BASE),
        ('TP_SIGMA_STRESS',          mc.TP_SIGMA_STRESS,          s.TP_SIGMA_STRESS),
        ('SL_SIGMA_BASE',            mc.SL_SIGMA_BASE,            s.SL_SIGMA_BASE),
        ('SL_SIGMA_STRESS',          mc.SL_SIGMA_STRESS,          s.SL_SIGMA_STRESS),
        # Put-side
        ('PUT_TP',                   mc.PUT_TP,                   o.PUT_TP),
        ('PUT_SL',                   mc.PUT_SL,                   o.PUT_SL),
        ('PUT_NET_TP',               mc.PUT_NET_TP,               o.PUT_NET_TP),
        ('PUT_NET_SL',               mc.PUT_NET_SL,               o.PUT_NET_SL),
        ('PUT_TP_SIGMA',             mc.PUT_TP_SIGMA,             s.PUT_TP_SIGMA),
        ('PUT_SL_SIGMA',             mc.PUT_SL_SIGMA,             s.PUT_SL_SIGMA),
        ('PUT_BREADTH_MODE',         mc.PUT_BREADTH_MODE,         o.PUT_BREADTH_MODE),
        ('PUT_BREADTH_THRESHOLD',    mc.PUT_BREADTH_THRESHOLD,    o.PUT_BREADTH_THRESHOLD),
        ('PUT_TP_STRESS',            mc.PUT_TP_STRESS,            o.PUT_TP_STRESS),
        ('PUT_SL_STRESS',            mc.PUT_SL_STRESS,            o.PUT_SL_STRESS),
        ('PUT_SL_HOLD_BARS_DEFAULT', mc.PUT_SL_HOLD_BARS_DEFAULT, o.PUT_SL_HOLD_BARS_DEFAULT),
        ('PUT_SL_HOLD_BARS_MONDAY',  mc.PUT_SL_HOLD_BARS_MONDAY,  o.PUT_SL_HOLD_BARS_MONDAY),
        # Position sizing
        ('MAX_POSITIONS',            mc.MAX_POSITIONS,            s.MAX_POSITIONS),
        ('MAX_POSITIONS_CALL',       mc.MAX_POSITIONS_CALL,       s.MAX_POSITIONS_CALL),
        ('MAX_POSITIONS_PUT',        mc.MAX_POSITIONS_PUT,        s.MAX_POSITIONS_PUT),
        ('PRACTICAL_EXPOSURE_ENABLED', bool(mc.PRACTICAL_EXPOSURE_ENABLED), s.PRACTICAL_EXPOSURE_ENABLED),
        ('PRACTICAL_CAPITAL_CEILING', mc.PRACTICAL_CAPITAL_CEILING, s.PRACTICAL_CAPITAL_CEILING),
        ('GROSS_PREMIUM_CAP',        mc.GROSS_PREMIUM_CAP,        s.GROSS_PREMIUM_CAP),
        ('CALL_PREMIUM_CAP',         mc.CALL_PREMIUM_CAP,         s.CALL_PREMIUM_CAP),
        ('PUT_PREMIUM_CAP',          mc.PUT_PREMIUM_CAP,          s.PUT_PREMIUM_CAP),
        ('OPP_SAT_CALL_REF',         mc.OPP_SAT_CALL_REF,         s.OPP_SAT_CALL_REF),
        ('OPP_SAT_PUT_REF',          mc.OPP_SAT_PUT_REF,          s.OPP_SAT_PUT_REF),
        ('OPP_SAT_POWER',            mc.OPP_SAT_POWER,            s.OPP_SAT_POWER),
        ('OPP_SAT_FLOOR',            mc.OPP_SAT_FLOOR,            s.OPP_SAT_FLOOR),
        ('PRIMARY_THRESHOLD',        mc.PRIMARY_THRESHOLD,        s.PRIMARY_THRESHOLD),
        ('OVERFLOW_THRESHOLD',       mc.OVERFLOW_THRESHOLD,       s.OVERFLOW_THRESHOLD),
        ('PUT_THRESHOLD',            mc.PUT_THRESHOLD,            s.PUT_THRESHOLD),
        # F3f
        ('F3F_CALL_THRESH',          mc.F3F_CALL_THRESH,          s.F3F_CALL_THRESH),
        ('F3F_CALL_FLOOR',           mc.F3F_CALL_FLOOR,           s.F3F_CALL_FLOOR),
        ('F3F_CALL_LOW',             mc.F3F_CALL_LOW,             s.F3F_CALL_LOW),
        ('F3F_PUT_THRESH',           mc.F3F_PUT_THRESH,           s.F3F_PUT_THRESH),
        ('F3F_PUT_FLOOR',            mc.F3F_PUT_FLOOR,            s.F3F_PUT_FLOOR),
        ('F3F_PUT_HIGH',             mc.F3F_PUT_HIGH,             s.F3F_PUT_HIGH),
        ('ALLOC_SCALE_FLOOR',        mc.ALLOC_SCALE_FLOOR,        s.ALLOC_SCALE_FLOOR),
        ('ALLOC_SCALE_CEIL',         mc.ALLOC_SCALE_CEIL,         s.ALLOC_SCALE_CEIL),
        # Regime
        ('REGIME_SLOPE',             mc.REGIME_SLOPE,             s.REGIME_SLOPE),
        ('REGIME_SLOPE_PUT',         mc.REGIME_SLOPE_PUT,         s.REGIME_SLOPE_PUT),
        ('REGIME_SLOPE_UP',          mc.REGIME_SLOPE_UP,          s.REGIME_SLOPE_UP),
        ('REGIME_SLOPE_DOWN',        mc.REGIME_SLOPE_DOWN,        s.REGIME_SLOPE_DOWN),
        ('REGIME_SLOPE_PUT_UP',      mc.REGIME_SLOPE_PUT_UP,      s.REGIME_SLOPE_PUT_UP),
        ('BREADTH_ALLOC_ENABLED',    mc.BREADTH_ALLOC_ENABLED,    s.BREADTH_ALLOC_ENABLED),
        # Misc
        ('DD_SOFT_BAND_LO',          mc.DD_SOFT_BAND_LO,          s.DD_SOFT_BAND_LO),
        ('DD_SOFT_BAND_HI',          mc.DD_SOFT_BAND_HI,          s.DD_SOFT_BAND_HI),
        ('SAW_PUT_UCURVE_ENABLED',   bool(mc.SAW_PUT_UCURVE_ENABLED), s.SAW_PUT_UCURVE_ENABLED),
        ('SAW_PUT_UCURVE_SHAPE',     mc.SAW_PUT_UCURVE_SHAPE,     s.SAW_PUT_UCURVE_SHAPE),
        ('SAW_PUT_UCURVE_MIDPOINT',  mc.SAW_PUT_UCURVE_MIDPOINT,  s.SAW_PUT_UCURVE_MIDPOINT),
        ('SAW_PUT_UCURVE_HALFWIDTH', mc.SAW_PUT_UCURVE_HALFWIDTH, s.SAW_PUT_UCURVE_HALFWIDTH),
        ('SAW_PUT_UCURVE_FLOOR',     mc.SAW_PUT_UCURVE_FLOOR,     s.SAW_PUT_UCURVE_FLOOR),
        ('SAW_PUT_UCURVE_CEIL',      mc.SAW_PUT_UCURVE_CEIL,      s.SAW_PUT_UCURVE_CEIL),
        ('SAW_PUT_UCURVE_POWER',     mc.SAW_PUT_UCURVE_POWER,     s.SAW_PUT_UCURVE_POWER),
        ('SAW_PUT_UCURVE_K',         mc.SAW_PUT_UCURVE_K,         s.SAW_PUT_UCURVE_K),
        # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08; 30 DTE only)
        ('CTSL_ENABLED',                 bool(mc.CTSL_ENABLED),           s.CTSL_ENABLED),
        ('CTSL_CALL_TREND_MAX',          mc.CTSL_CALL_TREND_MAX,          s.CTSL_CALL_TREND_MAX),
        ('CTSL_CALL_TARGET',             mc.CTSL_CALL_TARGET,             s.CTSL_CALL_TARGET),
        ('CTSL_CALL_ALPHA',              mc.CTSL_CALL_ALPHA,              s.CTSL_CALL_ALPHA),
        ('CTSL_CALL_TREND_POWER',        mc.CTSL_CALL_TREND_POWER,        s.CTSL_CALL_TREND_POWER),
        ('CTSL_CALL_TIER_FLOOR',         mc.CTSL_CALL_TIER_FLOOR,         s.CTSL_CALL_TIER_FLOOR),
        ('CTSL_CALL_SCORE_NORM_WEIGHT',  mc.CTSL_CALL_SCORE_NORM_WEIGHT,  s.CTSL_CALL_SCORE_NORM_WEIGHT),
        ('CTSL_CALL_SCORE_NORM_POWER',   mc.CTSL_CALL_SCORE_NORM_POWER,   s.CTSL_CALL_SCORE_NORM_POWER),
        ('CTSL_PUT_TREND_MIN',           mc.CTSL_PUT_TREND_MIN,           s.CTSL_PUT_TREND_MIN),
        ('CTSL_PUT_TARGET',              mc.CTSL_PUT_TARGET,              s.CTSL_PUT_TARGET),
        ('CTSL_PUT_ALPHA',               mc.CTSL_PUT_ALPHA,               s.CTSL_PUT_ALPHA),
        ('CTSL_PUT_TREND_POWER',         mc.CTSL_PUT_TREND_POWER,         s.CTSL_PUT_TREND_POWER),
        ('CTSL_PUT_TIER_CEILING',        mc.CTSL_PUT_TIER_CEILING,        s.CTSL_PUT_TIER_CEILING),
        ('CTSL_PUT_SCORE_NORM_WEIGHT',   mc.CTSL_PUT_SCORE_NORM_WEIGHT,   s.CTSL_PUT_SCORE_NORM_WEIGHT),
        ('CTSL_PUT_SCORE_NORM_POWER',    mc.CTSL_PUT_SCORE_NORM_POWER,    s.CTSL_PUT_SCORE_NORM_POWER),
        ('DD_SOFT_CALL_FLOOR',       mc.DD_SOFT_CALL_FLOOR,       s.DD_SOFT_CALL_FLOOR),
        # RXDD — VIX-band call dampener (30 DTE, shipped 2026-06-04)
        ('RXDD_ENABLED',             bool(mc.RXDD_ENABLED),       s.RXDD_ENABLED),
        ('RXDD_VIX_C',               mc.RXDD_VIX_C,               s.RXDD_VIX_C),
        ('RXDD_VIX_W',               mc.RXDD_VIX_W,               s.RXDD_VIX_W),
        ('RXDD_DEPTH',               mc.RXDD_DEPTH,               s.RXDD_DEPTH),
        ('RXDD_DD_MIN',              mc.RXDD_DD_MIN,              s.RXDD_DD_MIN),
        # MWDD — McClellan flat-band call dampener (30 DTE, shipped 2026-06-05)
        ('MWDD_ENABLED',             bool(mc.MWDD_ENABLED),       s.MWDD_ENABLED),
        ('MWDD_MCC_C',               mc.MWDD_MCC_C,               s.MWDD_MCC_C),
        ('MWDD_MCC_W',               mc.MWDD_MCC_W,               s.MWDD_MCC_W),
        ('MWDD_DEPTH',               mc.MWDD_DEPTH,               s.MWDD_DEPTH),
        ('MWDD_DD_MIN',              mc.MWDD_DD_MIN,              s.MWDD_DD_MIN),
        ('MWDD_VIX_PANIC',           mc.MWDD_VIX_PANIC,           s.MWDD_VIX_PANIC),
        # TVDD — TRIN volume-flow neutral-band call dampener (30 DTE, shipped 2026-06-07)
        ('TVDD_ENABLED',             bool(mc.TVDD_ENABLED),       s.TVDD_ENABLED),
        ('TVDD_TRIN_C',              mc.TVDD_TRIN_C,              s.TVDD_TRIN_C),
        ('TVDD_TRIN_W',              mc.TVDD_TRIN_W,              s.TVDD_TRIN_W),
        ('TVDD_DEPTH',               mc.TVDD_DEPTH,               s.TVDD_DEPTH),
        ('TVDD_DD_MIN',              mc.TVDD_DD_MIN,              s.TVDD_DD_MIN),
        ('TVDD_VIX_PANIC',           mc.TVDD_VIX_PANIC,           s.TVDD_VIX_PANIC),
        # BDIV — pre-top breadth-divergence-at-highs call dampener (30 DTE, shipped 2026-06-10)
        ('BDIV_ENABLED',             bool(mc.BDIV_ENABLED),       s.BDIV_ENABLED),
        ('BDIV_PROX_CUT',            mc.BDIV_PROX_CUT,            s.BDIV_PROX_CUT),
        ('BDIV_PROX_FULL',           mc.BDIV_PROX_FULL,           s.BDIV_PROX_FULL),
        ('BDIV_GAP_C',               mc.BDIV_GAP_C,               s.BDIV_GAP_C),
        ('BDIV_GAP_W',               mc.BDIV_GAP_W,               s.BDIV_GAP_W),
        ('BDIV_DEPTH',               mc.BDIV_DEPTH,               s.BDIV_DEPTH),
        # SPREAD_TILT — 75-79 component-spread call alloc haircut (30 DTE, shipped 2026-06-15)
        ('SPREAD_TILT_ENABLED',      bool(mc.SPREAD_TILT_ENABLED), s.SPREAD_TILT_ENABLED),
        ('SPREAD_TILT_LO',           mc.SPREAD_TILT_LO,           s.SPREAD_TILT_LO),
        ('SPREAD_TILT_HI',           mc.SPREAD_TILT_HI,           s.SPREAD_TILT_HI),
        ('SPREAD_TILT_DEPTH',        mc.SPREAD_TILT_DEPTH,        s.SPREAD_TILT_DEPTH),
        # SVR — semivol_r skew-bridge entry filter (30 DTE, shipped 2026-06-05)
        ('SVR_ENABLED',              bool(mc.SVR_ENABLED),        s.SVR_ENABLED),
        ('SVR_LO_CUT',               mc.SVR_LO_CUT,               s.SVR_LO_CUT),
        ('SVR_LO_FULL',              mc.SVR_LO_FULL,              s.SVR_LO_FULL),
        ('SVR_HI_FULL',              mc.SVR_HI_FULL,              s.SVR_HI_FULL),
        ('SVR_HI_CUT',               mc.SVR_HI_CUT,               s.SVR_HI_CUT),
        ('SVR_FLOOR',                mc.SVR_FLOOR,                s.SVR_FLOOR),
        # LIQUIDITY_FLOOR — option-volume admission filter (staged ship-candidate 2026-08-07)
        ('LIQUIDITY_FLOOR',          mc.LIQUIDITY_FLOOR,          s.LIQUIDITY_FLOOR),
        ('VOL_LOOKBACK',             mc.VOL_LOOKBACK,             s.VOL_LOOKBACK),
        ('COLLAPSE_THRESHOLD',       mc.COLLAPSE_THRESHOLD,       s.COLLAPSE_THRESHOLD),
        # Earnings put suppression
        ('EARN_SUPP_PUT',            mc.EARN_SUPP_PUT,            s.EARN_SUPP_PUT),
        ('EARN_SUPP_PUT_DAYS',       mc.EARN_SUPP_PUT_DAYS,       s.EARN_SUPP_PUT_DAYS),
        ('EARN_SUPP_PUT_MIN_OV',     mc.EARN_SUPP_PUT_MIN_OV,     s.EARN_SUPP_PUT_MIN_OV),
        ('EARN_SUPP_PUT_MAX_OV',     mc.EARN_SUPP_PUT_MAX_OV,     s.EARN_SUPP_PUT_MAX_OV),
        # Weak-weekly call filter (shipped 2026-05-05)
        ('WEAK_WEEKLY_CALL_DROP',    mc.WEAK_WEEKLY_CALL_DROP,    s.WEAK_WEEKLY_CALL_DROP),
        ('WEAK_WEEKLY_CALL_MIN_OV',  mc.WEAK_WEEKLY_CALL_MIN_OV,  s.WEAK_WEEKLY_CALL_MIN_OV),
        ('WEAK_WEEKLY_CALL_MAX_OV',  mc.WEAK_WEEKLY_CALL_MAX_OV,  s.WEAK_WEEKLY_CALL_MAX_OV),
        ('WEAK_WEEKLY_CALL_WADJ',    mc.WEAK_WEEKLY_CALL_WADJ,    s.WEAK_WEEKLY_CALL_WADJ_LT),
        ('WEAK_WEEKLY_CALL_STOCH_GE',mc.WEAK_WEEKLY_CALL_STOCH_GE,s.WEAK_WEEKLY_CALL_STOCH_GE),
        # Stress-state DTE router (Stage 3 portfolio overlay; 30 DTE only)
        ('DTE_ROUTER_ENABLED',        bool(mc.DTE_ROUTER_ENABLED), s.DTE_ROUTER_ENABLED),
        ('DTE_ROUTER_TARGET_DTE',     mc.DTE_ROUTER_TARGET_DTE,    s.DTE_ROUTER_TARGET_DTE),
        ('DTE_ROUTER_SCORE_MIN',      mc.DTE_ROUTER_SCORE_MIN,     s.DTE_ROUTER_SCORE_MIN),
        ('DTE_ROUTER_TREND_LT',       mc.DTE_ROUTER_TREND_LT,      s.DTE_ROUTER_TREND_LT),
        ('DTE_ROUTER_VIX_MIN',        mc.DTE_ROUTER_VIX_MIN,       s.DTE_ROUTER_VIX_MIN),
        ('DTE_ROUTER_REGIME_MIN',     mc.DTE_ROUTER_REGIME_MIN,    s.DTE_ROUTER_REGIME_MIN),
        ('DTE_ROUTER_REGIME_MAX',     mc.DTE_ROUTER_REGIME_MAX,    s.DTE_ROUTER_REGIME_MAX),
        ('DTE_ROUTER_DAY_CAP',        mc.DTE_ROUTER_DAY_CAP,       s.DTE_ROUTER_DAY_CAP),
        ('DTE_ROUTER_ALLOC_SCORE_CAP', mc.DTE_ROUTER_ALLOC_SCORE_CAP, s.DTE_ROUTER_ALLOC_SCORE_CAP),
        ('DTE_ROUTER_EXCLUDED_SYMBOLS', tuple(mc.DTE_ROUTER_EXCLUDED_SYMBOLS), tuple(s.DTE_ROUTER_EXCLUDED_SYMBOLS)),
    ]

    # REGIME_SLOPE_PUT_DOWN can be None — handle separately
    obs_pd = mc.REGIME_SLOPE_PUT_DOWN
    exp_pd = s.REGIME_SLOPE_PUT_DOWN
    assert (obs_pd is None and exp_pd is None) or _approx(obs_pd, exp_pd), \
        f'REGIME_SLOPE_PUT_DOWN drift: mc={obs_pd!r} cfg={exp_pd!r}'

    # Tier dicts (same key naming in monte_carlo.py)
    for tk in ('ultra', 'top', 'mid', 'low', 'overflow'):
        assert _approx(mc.TIER_ALLOC[tk], s.TIER_ALLOC[tk]), \
            f'TIER_ALLOC[{tk!r}] drift: mc={mc.TIER_ALLOC[tk]} cfg={s.TIER_ALLOC[tk]}'
    for tk in ('put_top', 'put_mid', 'put_low'):
        assert _approx(mc.PUT_TIER_ALLOC[tk], s.PUT_TIER_ALLOC[tk]), \
            f'PUT_TIER_ALLOC[{tk!r}] drift: mc={mc.PUT_TIER_ALLOC[tk]} cfg={s.PUT_TIER_ALLOC[tk]}'

    # ─── backtest_cascade.py mirror (different tier-key naming) ────────
    pairs_bc = [
        ('bc.MAX_POSITIONS',         bc.MAX_POSITIONS,         s.MAX_POSITIONS),
        ('bc.MAX_POSITIONS_CALL',    bc.MAX_POSITIONS_CALL,    s.MAX_POSITIONS_CALL),
        ('bc.MAX_POSITIONS_PUT',     bc.MAX_POSITIONS_PUT,     s.MAX_POSITIONS_PUT),
        ('bc.PRACTICAL_EXPOSURE_ENABLED', bool(bc.PRACTICAL_EXPOSURE_ENABLED), s.PRACTICAL_EXPOSURE_ENABLED),
        ('bc.PRACTICAL_CAPITAL_CEILING', bc.PRACTICAL_CAPITAL_CEILING, s.PRACTICAL_CAPITAL_CEILING),
        ('bc.GROSS_PREMIUM_CAP',     bc.GROSS_PREMIUM_CAP,     s.GROSS_PREMIUM_CAP),
        ('bc.CALL_PREMIUM_CAP',      bc.CALL_PREMIUM_CAP,      s.CALL_PREMIUM_CAP),
        ('bc.PUT_PREMIUM_CAP',       bc.PUT_PREMIUM_CAP,       s.PUT_PREMIUM_CAP),
        ('bc.OPP_SAT_CALL_REF',      bc.OPP_SAT_CALL_REF,      s.OPP_SAT_CALL_REF),
        ('bc.OPP_SAT_PUT_REF',       bc.OPP_SAT_PUT_REF,       s.OPP_SAT_PUT_REF),
        ('bc.OPP_SAT_POWER',         bc.OPP_SAT_POWER,         s.OPP_SAT_POWER),
        ('bc.OPP_SAT_FLOOR',         bc.OPP_SAT_FLOOR,         s.OPP_SAT_FLOOR),
        ('bc.HOLD_CALENDAR_DAYS',    bc.HOLD_CALENDAR_DAYS,    s.HOLD_CAL_DAYS),  # calendar-hold standard (cal days); honest theta (2026-06-09)
        ('bc.CALENDAR_HOLD',         bc.CALENDAR_HOLD,         s.CALENDAR_HOLD),
        ('bc.NOMINAL_CAL_DTE',       bc.NOMINAL_CAL_DTE,       s.NOMINAL_CAL_DTE),
        ('bc.BREADTH_THRESHOLD',     bc.BREADTH_THRESHOLD,     o.BREADTH_THRESHOLD),
        ('bc.TP_SIGMA_BASE',         bc.TP_SIGMA_BASE,         s.TP_SIGMA_BASE),
        ('bc.TP_SIGMA_STRESS',       bc.TP_SIGMA_STRESS,       s.TP_SIGMA_STRESS),
        ('bc.SL_SIGMA_BASE',         bc.SL_SIGMA_BASE,         s.SL_SIGMA_BASE),
        ('bc.SL_SIGMA_STRESS',       bc.SL_SIGMA_STRESS,       s.SL_SIGMA_STRESS),
        ('bc.NET_TP_BASE',           bc.NET_TP_BASE,           o.NET_TP_BASE),
        ('bc.NET_TP_STRESS',         bc.NET_TP_STRESS,         o.NET_TP_STRESS),
        ('bc.NET_SL_BASE',           bc.NET_SL_BASE,           o.NET_SL_BASE),
        ('bc.NET_SL_STRESS',         bc.NET_SL_STRESS,         o.NET_SL_STRESS),
        ('bc.NET_HARD',              bc.NET_HARD,              s.NET_HARD_SELL),
        ('bc.PUT_TP',                bc.PUT_TP,                o.PUT_TP),
        ('bc.PUT_SL',                bc.PUT_SL,                o.PUT_SL),
        ('bc.PUT_NET_TP',            bc.PUT_NET_TP,            o.PUT_NET_TP),
        ('bc.PUT_NET_SL',            bc.PUT_NET_SL,            o.PUT_NET_SL),
        ('bc.PUT_TP_SIGMA',          bc.PUT_TP_SIGMA,          s.PUT_TP_SIGMA),
        ('bc.PUT_SL_SIGMA',          bc.PUT_SL_SIGMA,          s.PUT_SL_SIGMA),
        ('bc.PUT_THRESHOLD',         bc.PUT_THRESHOLD,         s.PUT_THRESHOLD),
        ('bc.PUT_SL_HOLD_BARS_DEFAULT', bc.PUT_SL_HOLD_BARS_DEFAULT, o.PUT_SL_HOLD_BARS_DEFAULT),
        ('bc.PUT_SL_HOLD_BARS_MONDAY',  bc.PUT_SL_HOLD_BARS_MONDAY,  o.PUT_SL_HOLD_BARS_MONDAY),
        ('bc.PREMIUM_MULT',          bc.PREMIUM_MULT,          s.PREMIUM_MULT),
        ('bc.DELTA',                 bc.DELTA,                 o.DELTA),
        ('bc.SLIP_ENTRY',            bc.SLIP_ENTRY,            o.SLIP_ENTRY),
        ('bc.SLIP_TP',               bc.SLIP_TP,               o.SLIP_TP),
        ('bc.SLIP_SL',               bc.SLIP_SL,               o.SLIP_SL),
        ('bc.SLIP_HARD',             bc.SLIP_HARD,             o.SLIP_HARD),
        ('bc.F3F_CALL_FLOOR',        bc.F3F_CALL_FLOOR,        s.F3F_CALL_FLOOR),
        ('bc.F3F_PUT_FLOOR',         bc.F3F_PUT_FLOOR,         s.F3F_PUT_FLOOR),
        ('bc.DD_SOFT_BAND_LO',       bc.DD_SOFT_BAND_LO,       s.DD_SOFT_BAND_LO),
        ('bc.DD_SOFT_BAND_HI',       bc.DD_SOFT_BAND_HI,       s.DD_SOFT_BAND_HI),
        ('bc.SAW_PUT_UCURVE_ENABLED',   bool(bc.SAW_PUT_UCURVE_ENABLED), s.SAW_PUT_UCURVE_ENABLED),
        ('bc.SAW_PUT_UCURVE_SHAPE',     bc.SAW_PUT_UCURVE_SHAPE,         s.SAW_PUT_UCURVE_SHAPE),
        ('bc.SAW_PUT_UCURVE_MIDPOINT',  bc.SAW_PUT_UCURVE_MIDPOINT,      s.SAW_PUT_UCURVE_MIDPOINT),
        ('bc.SAW_PUT_UCURVE_HALFWIDTH', bc.SAW_PUT_UCURVE_HALFWIDTH,     s.SAW_PUT_UCURVE_HALFWIDTH),
        ('bc.SAW_PUT_UCURVE_FLOOR',     bc.SAW_PUT_UCURVE_FLOOR,         s.SAW_PUT_UCURVE_FLOOR),
        ('bc.SAW_PUT_UCURVE_CEIL',      bc.SAW_PUT_UCURVE_CEIL,          s.SAW_PUT_UCURVE_CEIL),
        ('bc.SAW_PUT_UCURVE_POWER',     bc.SAW_PUT_UCURVE_POWER,         s.SAW_PUT_UCURVE_POWER),
        ('bc.SAW_PUT_UCURVE_K',         bc.SAW_PUT_UCURVE_K,             s.SAW_PUT_UCURVE_K),
        ('bc.DD_SOFT_CALL_FLOOR',    bc.DD_SOFT_CALL_FLOOR,    s.DD_SOFT_CALL_FLOOR),
        # RXDD — VIX-band call dampener (30 DTE, shipped 2026-06-04)
        ('bc.RXDD_ENABLED',          bool(bc.RXDD_ENABLED),    s.RXDD_ENABLED),
        ('bc.RXDD_VIX_C',            bc.RXDD_VIX_C,            s.RXDD_VIX_C),
        ('bc.RXDD_VIX_W',            bc.RXDD_VIX_W,            s.RXDD_VIX_W),
        ('bc.RXDD_DEPTH',            bc.RXDD_DEPTH,            s.RXDD_DEPTH),
        ('bc.RXDD_DD_MIN',           bc.RXDD_DD_MIN,           s.RXDD_DD_MIN),
        # MWDD — McClellan flat-band call dampener (30 DTE, shipped 2026-06-05)
        ('bc.MWDD_ENABLED',          bool(bc.MWDD_ENABLED),    s.MWDD_ENABLED),
        ('bc.MWDD_MCC_C',            bc.MWDD_MCC_C,            s.MWDD_MCC_C),
        ('bc.MWDD_MCC_W',            bc.MWDD_MCC_W,            s.MWDD_MCC_W),
        ('bc.MWDD_DEPTH',            bc.MWDD_DEPTH,            s.MWDD_DEPTH),
        ('bc.MWDD_DD_MIN',           bc.MWDD_DD_MIN,           s.MWDD_DD_MIN),
        ('bc.MWDD_VIX_PANIC',        bc.MWDD_VIX_PANIC,        s.MWDD_VIX_PANIC),
        # TVDD — TRIN volume-flow neutral-band call dampener (30 DTE, shipped 2026-06-07)
        ('bc.TVDD_ENABLED',          bool(bc.TVDD_ENABLED),   s.TVDD_ENABLED),
        ('bc.TVDD_TRIN_C',           bc.TVDD_TRIN_C,          s.TVDD_TRIN_C),
        ('bc.TVDD_TRIN_W',           bc.TVDD_TRIN_W,          s.TVDD_TRIN_W),
        ('bc.TVDD_DEPTH',            bc.TVDD_DEPTH,           s.TVDD_DEPTH),
        ('bc.TVDD_DD_MIN',           bc.TVDD_DD_MIN,          s.TVDD_DD_MIN),
        ('bc.TVDD_VIX_PANIC',        bc.TVDD_VIX_PANIC,       s.TVDD_VIX_PANIC),
        # BDIV — pre-top breadth-divergence-at-highs call dampener (30 DTE, shipped 2026-06-10)
        ('bc.BDIV_ENABLED',          bool(bc.BDIV_ENABLED),   s.BDIV_ENABLED),
        ('bc.BDIV_PROX_CUT',         bc.BDIV_PROX_CUT,        s.BDIV_PROX_CUT),
        ('bc.BDIV_PROX_FULL',        bc.BDIV_PROX_FULL,       s.BDIV_PROX_FULL),
        ('bc.BDIV_GAP_C',            bc.BDIV_GAP_C,           s.BDIV_GAP_C),
        ('bc.BDIV_GAP_W',            bc.BDIV_GAP_W,           s.BDIV_GAP_W),
        ('bc.BDIV_DEPTH',            bc.BDIV_DEPTH,           s.BDIV_DEPTH),
        # SPREAD_TILT — 75-79 component-spread call alloc haircut (30 DTE, shipped 2026-06-15)
        ('bc.SPREAD_TILT_ENABLED',   bool(bc.SPREAD_TILT_ENABLED), s.SPREAD_TILT_ENABLED),
        ('bc.SPREAD_TILT_LO',        bc.SPREAD_TILT_LO,       s.SPREAD_TILT_LO),
        ('bc.SPREAD_TILT_HI',        bc.SPREAD_TILT_HI,       s.SPREAD_TILT_HI),
        ('bc.SPREAD_TILT_DEPTH',     bc.SPREAD_TILT_DEPTH,    s.SPREAD_TILT_DEPTH),
        # SVR — semivol_r skew-bridge entry filter (30 DTE, shipped 2026-06-05)
        ('bc.SVR_ENABLED',           bool(bc.SVR_ENABLED),    s.SVR_ENABLED),
        ('bc.SVR_LO_CUT',            bc.SVR_LO_CUT,            s.SVR_LO_CUT),
        ('bc.SVR_LO_FULL',           bc.SVR_LO_FULL,           s.SVR_LO_FULL),
        ('bc.SVR_HI_FULL',           bc.SVR_HI_FULL,           s.SVR_HI_FULL),
        ('bc.SVR_HI_CUT',            bc.SVR_HI_CUT,            s.SVR_HI_CUT),
        ('bc.SVR_FLOOR',             bc.SVR_FLOOR,             s.SVR_FLOOR),
        # LIQUIDITY_FLOOR — option-volume admission filter (staged ship-candidate 2026-08-07)
        ('bc.LIQUIDITY_FLOOR',       bc.LIQUIDITY_FLOOR,       s.LIQUIDITY_FLOOR),
        ('bc.REGIME_SLOPE_DOWN',     bc.REGIME_SLOPE_DOWN,     s.REGIME_SLOPE_DOWN),
        ('bc.REGIME_SLOPE_UP',       bc.REGIME_SLOPE_UP,       s.REGIME_SLOPE_UP),
        ('bc.EARN_SUPP_PUT',         bc.EARN_SUPP_PUT,         s.EARN_SUPP_PUT),
        ('bc.EARN_SUPP_PUT_DAYS',    bc.EARN_SUPP_PUT_DAYS,    s.EARN_SUPP_PUT_DAYS),
        ('bc.WEAK_WEEKLY_CALL_DROP', bc.WEAK_WEEKLY_CALL_DROP, s.WEAK_WEEKLY_CALL_DROP),
        ('bc.WEAK_WEEKLY_CALL_MIN_OV', bc.WEAK_WEEKLY_CALL_MIN_OV, s.WEAK_WEEKLY_CALL_MIN_OV),
        ('bc.WEAK_WEEKLY_CALL_MAX_OV', bc.WEAK_WEEKLY_CALL_MAX_OV, s.WEAK_WEEKLY_CALL_MAX_OV),
        ('bc.WEAK_WEEKLY_CALL_WADJ_LT', bc.WEAK_WEEKLY_CALL_WADJ_LT, s.WEAK_WEEKLY_CALL_WADJ_LT),
        ('bc.WEAK_WEEKLY_CALL_STOCH_GE', bc.WEAK_WEEKLY_CALL_STOCH_GE, s.WEAK_WEEKLY_CALL_STOCH_GE),
        ('bc.CT_PROMOTE',            bc.CT_PROMOTE,            s.CT_PROMOTE),
        ('bc.CT_PUT_TREND_MIN',      bc.CT_PUT_TREND_MIN,      s.CT_PUT_TREND_MIN),
        ('bc.CT_CALL_TREND_MAX',     bc.CT_CALL_TREND_MAX,     s.CT_CALL_TREND_MAX),
        # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08; 30 DTE only)
        ('bc.CTSL_ENABLED',                 bool(bc.CTSL_ENABLED),           s.CTSL_ENABLED),
        ('bc.CTSL_CALL_TREND_MAX',          bc.CTSL_CALL_TREND_MAX,          s.CTSL_CALL_TREND_MAX),
        ('bc.CTSL_CALL_TARGET',             bc.CTSL_CALL_TARGET,             s.CTSL_CALL_TARGET),
        ('bc.CTSL_CALL_ALPHA',              bc.CTSL_CALL_ALPHA,              s.CTSL_CALL_ALPHA),
        ('bc.CTSL_CALL_TREND_POWER',        bc.CTSL_CALL_TREND_POWER,        s.CTSL_CALL_TREND_POWER),
        ('bc.CTSL_CALL_TIER_FLOOR',         bc.CTSL_CALL_TIER_FLOOR,         s.CTSL_CALL_TIER_FLOOR),
        ('bc.CTSL_CALL_SCORE_NORM_WEIGHT',  bc.CTSL_CALL_SCORE_NORM_WEIGHT,  s.CTSL_CALL_SCORE_NORM_WEIGHT),
        ('bc.CTSL_CALL_SCORE_NORM_POWER',   bc.CTSL_CALL_SCORE_NORM_POWER,   s.CTSL_CALL_SCORE_NORM_POWER),
        ('bc.CTSL_PUT_TREND_MIN',           bc.CTSL_PUT_TREND_MIN,           s.CTSL_PUT_TREND_MIN),
        ('bc.CTSL_PUT_TARGET',              bc.CTSL_PUT_TARGET,              s.CTSL_PUT_TARGET),
        ('bc.CTSL_PUT_ALPHA',               bc.CTSL_PUT_ALPHA,               s.CTSL_PUT_ALPHA),
        ('bc.CTSL_PUT_TREND_POWER',         bc.CTSL_PUT_TREND_POWER,         s.CTSL_PUT_TREND_POWER),
        ('bc.CTSL_PUT_TIER_CEILING',        bc.CTSL_PUT_TIER_CEILING,        s.CTSL_PUT_TIER_CEILING),
        ('bc.CTSL_PUT_SCORE_NORM_WEIGHT',   bc.CTSL_PUT_SCORE_NORM_WEIGHT,   s.CTSL_PUT_SCORE_NORM_WEIGHT),
        ('bc.CTSL_PUT_SCORE_NORM_POWER',    bc.CTSL_PUT_SCORE_NORM_POWER,    s.CTSL_PUT_SCORE_NORM_POWER),
        # Stress-state DTE router (Stage 3 portfolio overlay; 30 DTE only)
        ('bc.DTE_ROUTER_ENABLED',        bool(bc.DTE_ROUTER_ENABLED), s.DTE_ROUTER_ENABLED),
        ('bc.DTE_ROUTER_TARGET_DTE',     bc.DTE_ROUTER_TARGET_DTE,    s.DTE_ROUTER_TARGET_DTE),
        ('bc.DTE_ROUTER_SCORE_MIN',      bc.DTE_ROUTER_SCORE_MIN,     s.DTE_ROUTER_SCORE_MIN),
        ('bc.DTE_ROUTER_TREND_LT',       bc.DTE_ROUTER_TREND_LT,      s.DTE_ROUTER_TREND_LT),
        ('bc.DTE_ROUTER_VIX_MIN',        bc.DTE_ROUTER_VIX_MIN,       s.DTE_ROUTER_VIX_MIN),
        ('bc.DTE_ROUTER_REGIME_MIN',     bc.DTE_ROUTER_REGIME_MIN,    s.DTE_ROUTER_REGIME_MIN),
        ('bc.DTE_ROUTER_REGIME_MAX',     bc.DTE_ROUTER_REGIME_MAX,    s.DTE_ROUTER_REGIME_MAX),
        ('bc.DTE_ROUTER_DAY_CAP',        bc.DTE_ROUTER_DAY_CAP,       s.DTE_ROUTER_DAY_CAP),
        ('bc.DTE_ROUTER_ALLOC_SCORE_CAP', bc.DTE_ROUTER_ALLOC_SCORE_CAP, s.DTE_ROUTER_ALLOC_SCORE_CAP),
        ('bc.DTE_ROUTER_EXCLUDED_SYMBOLS', tuple(bc.DTE_ROUTER_EXCLUDED_SYMBOLS), tuple(s.DTE_ROUTER_EXCLUDED_SYMBOLS)),
    ]

    # backtest_cascade.py uses score-range tier keys; map to strategy_config semantic keys
    bc_tier_map = [
        ('95+',    'ultra'),
        ('85-94',  'top'),
        ('80-84',  'mid'),
        ('75-79',  'low'),
        ('70-74',  'overflow'),
    ]
    for bc_k, cfg_k in bc_tier_map:
        assert _approx(bc.TIER_ALLOC[bc_k], s.TIER_ALLOC[cfg_k]), \
            f"bc.TIER_ALLOC[{bc_k!r}] drift: bc={bc.TIER_ALLOC[bc_k]} cfg.{cfg_k}={s.TIER_ALLOC[cfg_k]}"
    bc_put_map = [
        ('p<=15',  'put_top'),
        ('p16-20', 'put_mid'),
        ('p21-25', 'put_low'),
    ]
    for bc_k, cfg_k in bc_put_map:
        assert _approx(bc.TIER_ALLOC[bc_k], s.PUT_TIER_ALLOC[cfg_k]), \
            f"bc.TIER_ALLOC[{bc_k!r}] drift: bc={bc.TIER_ALLOC[bc_k]} cfg.PUT.{cfg_k}={s.PUT_TIER_ALLOC[cfg_k]}"

    return pairs_mc, pairs_bc


def check_15dte():
    """Verify monte_carlo_15dte.py + backtest_cascade_15dte.py mirror STRATEGY_15DTE.

    Returns (pairs_mc15, pairs_bc15). Mirrors check_30dte() shape — every field
    that the 15 DTE engine actually wires is asserted engine-vs-config. Fields
    wired only in 30 DTE (SAW Put U-curve currently) are skipped here; their
    intentional non-wiring is enforced by check_dte_field_parity() and the
    "engine should NOT have constant" assertions below.
    """
    import strategy_config as cfg
    import monte_carlo_15dte as mc15
    import backtest_cascade_15dte as bc15

    s = cfg.STRATEGY_15DTE
    o = s.option

    # ─── monte_carlo_15dte.py module-level constants ───────────────────────
    # Mirror pairs_mc layout. Includes EVERY field that mc15 actually wires.
    # Per the audit, mc15 wires 50+ of the 74 strategy_config fields; only
    # SAW_PUT_UCURVE_* and a handful of internal helpers are absent. The
    # dataclass defines DEAD_HOLD_ENABLED on STRATEGY_15DTE so we check that
    # too even though the engine reads it via _cfg directly.
    pairs_mc15 = [
        # ── DTE-specific
        ('mc15.HOLD_DAYS',                mc15.HOLD_DAYS,                s.HOLD_DAYS),
        ('mc15.PREMIUM_MULT',             mc15.PREMIUM_MULT,             s.PREMIUM_MULT),
        ('mc15.HARD_SELL_LOSS',           mc15.HARD_SELL_LOSS,           s.HARD_SELL_LOSS),
        ('mc15.DELTA',                    mc15.DELTA,                    o.DELTA),
        # ── TP / SL on premium (call side)
        ('mc15.TP_BASE',                  mc15.TP_BASE,                  o.TP_BASE),
        ('mc15.TP_STRESS',                mc15.TP_STRESS,                o.TP_STRESS),
        ('mc15.SL_BASE',                  mc15.SL_BASE,                  o.SL_BASE),
        ('mc15.SL_STRESS',                mc15.SL_STRESS,                o.SL_STRESS),
        ('mc15.BREADTH_THRESHOLD',        mc15.BREADTH_THRESHOLD,        o.BREADTH_THRESHOLD),
        ('mc15.SLIP_ENTRY',               mc15.SLIP_ENTRY,               o.SLIP_ENTRY),
        ('mc15.SLIP_TP',                  mc15.SLIP_TP,                  o.SLIP_TP),
        ('mc15.SLIP_SL',                  mc15.SLIP_SL,                  o.SLIP_SL),
        ('mc15.SLIP_HARD',                mc15.SLIP_HARD,                o.SLIP_HARD),
        # ── Derived (call)
        ('mc15.NET_TP_BASE',              mc15.NET_TP_BASE,              o.NET_TP_BASE),
        ('mc15.NET_TP_STRESS',            mc15.NET_TP_STRESS,            o.NET_TP_STRESS),
        ('mc15.NET_SL_BASE',              mc15.NET_SL_BASE,              o.NET_SL_BASE),
        ('mc15.NET_SL_STRESS',            mc15.NET_SL_STRESS,            o.NET_SL_STRESS),
        ('mc15.NET_HARD_SELL',            mc15.NET_HARD_SELL,            s.NET_HARD_SELL),
        ('mc15.TP_SIGMA_BASE',            mc15.TP_SIGMA_BASE,            s.TP_SIGMA_BASE),
        ('mc15.TP_SIGMA_STRESS',          mc15.TP_SIGMA_STRESS,          s.TP_SIGMA_STRESS),
        ('mc15.SL_SIGMA_BASE',            mc15.SL_SIGMA_BASE,            s.SL_SIGMA_BASE),
        ('mc15.SL_SIGMA_STRESS',          mc15.SL_SIGMA_STRESS,          s.SL_SIGMA_STRESS),
        # ── Put-side
        ('mc15.PUT_TP',                   mc15.PUT_TP,                   o.PUT_TP),
        ('mc15.PUT_SL',                   mc15.PUT_SL,                   o.PUT_SL),
        ('mc15.PUT_NET_TP',               mc15.PUT_NET_TP,               o.PUT_NET_TP),
        ('mc15.PUT_NET_SL',               mc15.PUT_NET_SL,               o.PUT_NET_SL),
        ('mc15.PUT_TP_SIGMA',             mc15.PUT_TP_SIGMA,             s.PUT_TP_SIGMA),
        ('mc15.PUT_SL_SIGMA',             mc15.PUT_SL_SIGMA,             s.PUT_SL_SIGMA),
        ('mc15.PUT_BREADTH_MODE',         mc15.PUT_BREADTH_MODE,         o.PUT_BREADTH_MODE),
        ('mc15.PUT_BREADTH_THRESHOLD',    mc15.PUT_BREADTH_THRESHOLD,    o.PUT_BREADTH_THRESHOLD),
        ('mc15.PUT_TP_STRESS',            mc15.PUT_TP_STRESS,            o.PUT_TP_STRESS),
        ('mc15.PUT_SL_STRESS',            mc15.PUT_SL_STRESS,            o.PUT_SL_STRESS),
        ('mc15.PUT_SL_HOLD_BARS_DEFAULT', mc15.PUT_SL_HOLD_BARS_DEFAULT, o.PUT_SL_HOLD_BARS_DEFAULT),
        ('mc15.PUT_SL_HOLD_BARS_MONDAY',  mc15.PUT_SL_HOLD_BARS_MONDAY,  o.PUT_SL_HOLD_BARS_MONDAY),
        # ── Position sizing
        ('mc15.MAX_POSITIONS',            mc15.MAX_POSITIONS,            s.MAX_POSITIONS),
        ('mc15.MAX_POSITIONS_CALL',       mc15.MAX_POSITIONS_CALL,       s.MAX_POSITIONS_CALL),
        ('mc15.MAX_POSITIONS_PUT',        mc15.MAX_POSITIONS_PUT,        s.MAX_POSITIONS_PUT),
        ('mc15.PRIMARY_THRESHOLD',        mc15.PRIMARY_THRESHOLD,        s.PRIMARY_THRESHOLD),
        ('mc15.OVERFLOW_THRESHOLD',       mc15.OVERFLOW_THRESHOLD,       s.OVERFLOW_THRESHOLD),
        ('mc15.PUT_THRESHOLD',            mc15.PUT_THRESHOLD,            s.PUT_THRESHOLD),
        # ── F3f
        ('mc15.F3F_CALL_THRESH',          mc15.F3F_CALL_THRESH,          s.F3F_CALL_THRESH),
        ('mc15.F3F_CALL_FLOOR',           mc15.F3F_CALL_FLOOR,           s.F3F_CALL_FLOOR),
        ('mc15.F3F_CALL_LOW',             mc15.F3F_CALL_LOW,             s.F3F_CALL_LOW),
        ('mc15.F3F_PUT_THRESH',           mc15.F3F_PUT_THRESH,           s.F3F_PUT_THRESH),
        ('mc15.F3F_PUT_FLOOR',            mc15.F3F_PUT_FLOOR,            s.F3F_PUT_FLOOR),
        ('mc15.F3F_PUT_HIGH',             mc15.F3F_PUT_HIGH,             s.F3F_PUT_HIGH),
        ('mc15.ALLOC_SCALE_FLOOR',        mc15.ALLOC_SCALE_FLOOR,        s.ALLOC_SCALE_FLOOR),
        ('mc15.ALLOC_SCALE_CEIL',         mc15.ALLOC_SCALE_CEIL,         s.ALLOC_SCALE_CEIL),
        # ── Regime slopes
        ('mc15.REGIME_SLOPE',             mc15.REGIME_SLOPE,             s.REGIME_SLOPE),
        ('mc15.REGIME_SLOPE_PUT',         mc15.REGIME_SLOPE_PUT,         s.REGIME_SLOPE_PUT),
        ('mc15.REGIME_SLOPE_UP',          mc15.REGIME_SLOPE_UP,          s.REGIME_SLOPE_UP),
        ('mc15.REGIME_SLOPE_DOWN',        mc15.REGIME_SLOPE_DOWN,        s.REGIME_SLOPE_DOWN),
        ('mc15.REGIME_SLOPE_PUT_UP',      mc15.REGIME_SLOPE_PUT_UP,      s.REGIME_SLOPE_PUT_UP),
        ('mc15.BREADTH_ALLOC_ENABLED',    mc15.BREADTH_ALLOC_ENABLED,    s.BREADTH_ALLOC_ENABLED),
        # ── DD soft band
        ('mc15.DD_SOFT_BAND_LO',          mc15.DD_SOFT_BAND_LO,          s.DD_SOFT_BAND_LO),
        ('mc15.DD_SOFT_BAND_HI',          mc15.DD_SOFT_BAND_HI,          s.DD_SOFT_BAND_HI),
        ('mc15.DD_SOFT_CALL_FLOOR',       mc15.DD_SOFT_CALL_FLOOR,       s.DD_SOFT_CALL_FLOOR),
        # ── Dead-hold
        ('mc15.DEAD_HOLD_ENABLED',        mc15.DEAD_HOLD_ENABLED,        s.DEAD_HOLD_ENABLED),
        ('mc15.DEAD_HOLD_TRIGGER_PNL',    mc15.DEAD_HOLD_TRIGGER_PNL,    s.DEAD_HOLD_TRIGGER_PNL),
        ('mc15.DEAD_HOLD_POPOUT_PNL',     mc15.DEAD_HOLD_POPOUT_PNL,     s.DEAD_HOLD_POPOUT_PNL),
        # ── Earnings put suppression
        ('mc15.EARN_SUPP_PUT',            mc15.EARN_SUPP_PUT,            s.EARN_SUPP_PUT),
        ('mc15.EARN_SUPP_PUT_DAYS',       mc15.EARN_SUPP_PUT_DAYS,       s.EARN_SUPP_PUT_DAYS),
        ('mc15.EARN_SUPP_PUT_MIN_OV',     mc15.EARN_SUPP_PUT_MIN_OV,     s.EARN_SUPP_PUT_MIN_OV),
        ('mc15.EARN_SUPP_PUT_MAX_OV',     mc15.EARN_SUPP_PUT_MAX_OV,     s.EARN_SUPP_PUT_MAX_OV),
        # ── Weak-weekly call filter (mc15 uses WADJ short-name; cfg uses WADJ_LT)
        ('mc15.WEAK_WEEKLY_CALL_DROP',    mc15.WEAK_WEEKLY_CALL_DROP,    s.WEAK_WEEKLY_CALL_DROP),
        ('mc15.WEAK_WEEKLY_CALL_MIN_OV',  mc15.WEAK_WEEKLY_CALL_MIN_OV,  s.WEAK_WEEKLY_CALL_MIN_OV),
        ('mc15.WEAK_WEEKLY_CALL_MAX_OV', mc15.WEAK_WEEKLY_CALL_MAX_OV,  s.WEAK_WEEKLY_CALL_MAX_OV),
        ('mc15.WEAK_WEEKLY_CALL_WADJ',    mc15.WEAK_WEEKLY_CALL_WADJ,    s.WEAK_WEEKLY_CALL_WADJ_LT),
        ('mc15.WEAK_WEEKLY_CALL_STOCH_GE',mc15.WEAK_WEEKLY_CALL_STOCH_GE,s.WEAK_WEEKLY_CALL_STOCH_GE),
        # ── CT promotion
        ('mc15.CT_PROMOTE',               mc15.CT_PROMOTE,               s.CT_PROMOTE),
        ('mc15.CT_PUT_TREND_MIN',         mc15.CT_PUT_TREND_MIN,         s.CT_PUT_TREND_MIN),
        ('mc15.CT_CALL_TREND_MAX',        mc15.CT_CALL_TREND_MAX,        s.CT_CALL_TREND_MAX),
        # ── Misc
        ('mc15.VOL_LOOKBACK',             mc15.VOL_LOOKBACK,             s.VOL_LOOKBACK),
        ('mc15.COLLAPSE_THRESHOLD',       mc15.COLLAPSE_THRESHOLD,       s.COLLAPSE_THRESHOLD),
    ]

    # REGIME_SLOPE_PUT_DOWN can be None — handle separately
    obs_pd = mc15.REGIME_SLOPE_PUT_DOWN
    exp_pd = s.REGIME_SLOPE_PUT_DOWN
    assert (obs_pd is None and exp_pd is None) or _approx(obs_pd, exp_pd), \
        f'mc15.REGIME_SLOPE_PUT_DOWN drift: mc15={obs_pd!r} cfg={exp_pd!r}'

    # SAW Put U-curve — SHIPPED 2026-05-09 for 15 DTE (sigmoid mid=70/hw=25/
    # floor=0.65/ceil=1.00/K=12.0). Per Stage 3 N=500x8 ship gate, all T1-T7
    # PASS with 5y DD -1.92pp, 22-now DD -1.76pp.
    pairs_mc15.extend([
        ('mc15.SAW_PUT_UCURVE_ENABLED',   bool(mc15.SAW_PUT_UCURVE_ENABLED), s.SAW_PUT_UCURVE_ENABLED),
        ('mc15.SAW_PUT_UCURVE_SHAPE',     mc15.SAW_PUT_UCURVE_SHAPE,         s.SAW_PUT_UCURVE_SHAPE),
        ('mc15.SAW_PUT_UCURVE_MIDPOINT',  mc15.SAW_PUT_UCURVE_MIDPOINT,      s.SAW_PUT_UCURVE_MIDPOINT),
        ('mc15.SAW_PUT_UCURVE_HALFWIDTH', mc15.SAW_PUT_UCURVE_HALFWIDTH,     s.SAW_PUT_UCURVE_HALFWIDTH),
        ('mc15.SAW_PUT_UCURVE_FLOOR',     mc15.SAW_PUT_UCURVE_FLOOR,         s.SAW_PUT_UCURVE_FLOOR),
        ('mc15.SAW_PUT_UCURVE_CEIL',      mc15.SAW_PUT_UCURVE_CEIL,          s.SAW_PUT_UCURVE_CEIL),
        ('mc15.SAW_PUT_UCURVE_POWER',     mc15.SAW_PUT_UCURVE_POWER,         s.SAW_PUT_UCURVE_POWER),
        ('mc15.SAW_PUT_UCURVE_K',         mc15.SAW_PUT_UCURVE_K,             s.SAW_PUT_UCURVE_K),
    ])

    # Tier dicts (mc15 uses semantic 'ultra'/'top'/...  keys like mc30)
    for tk in ('ultra', 'top', 'mid', 'low', 'overflow'):
        assert _approx(mc15.TIER_ALLOC[tk], s.TIER_ALLOC[tk]), \
            f'mc15.TIER_ALLOC[{tk!r}] drift: mc15={mc15.TIER_ALLOC[tk]} cfg={s.TIER_ALLOC[tk]}'
    for tk in ('put_top', 'put_mid', 'put_low'):
        assert _approx(mc15.PUT_TIER_ALLOC[tk], s.PUT_TIER_ALLOC[tk]), \
            f'mc15.PUT_TIER_ALLOC[{tk!r}] drift: mc15={mc15.PUT_TIER_ALLOC[tk]} cfg={s.PUT_TIER_ALLOC[tk]}'

    # ─── backtest_cascade_15dte.py mirror (uses score-range tier keys) ─────
    pairs_bc15 = [
        ('bc15.MAX_POSITIONS',         bc15.MAX_POSITIONS,         s.MAX_POSITIONS),
        ('bc15.HOLD_CALENDAR_DAYS',    bc15.HOLD_CALENDAR_DAYS,    s.HOLD_DAYS),
        ('bc15.BREADTH_THRESHOLD',     bc15.BREADTH_THRESHOLD,     o.BREADTH_THRESHOLD),
        ('bc15.TP_BASE',               bc15.TP_BASE,               o.TP_BASE),
        ('bc15.TP_STRESS',             bc15.TP_STRESS,             o.TP_STRESS),
        ('bc15.SL_BASE',               bc15.SL_BASE,               o.SL_BASE),
        ('bc15.SL_STRESS',             bc15.SL_STRESS,             o.SL_STRESS),
        ('bc15.TP_SIGMA_BASE',         bc15.TP_SIGMA_BASE,         s.TP_SIGMA_BASE),
        ('bc15.TP_SIGMA_STRESS',       bc15.TP_SIGMA_STRESS,       s.TP_SIGMA_STRESS),
        ('bc15.SL_SIGMA_BASE',         bc15.SL_SIGMA_BASE,         s.SL_SIGMA_BASE),
        ('bc15.SL_SIGMA_STRESS',       bc15.SL_SIGMA_STRESS,       s.SL_SIGMA_STRESS),
        ('bc15.NET_TP_BASE',           bc15.NET_TP_BASE,           o.NET_TP_BASE),
        ('bc15.NET_TP_STRESS',         bc15.NET_TP_STRESS,         o.NET_TP_STRESS),
        ('bc15.NET_SL_BASE',           bc15.NET_SL_BASE,           o.NET_SL_BASE),
        ('bc15.NET_SL_STRESS',         bc15.NET_SL_STRESS,         o.NET_SL_STRESS),
        ('bc15.NET_HARD',              bc15.NET_HARD,              s.NET_HARD_SELL),
        # Put-side
        ('bc15.PUT_TP',                bc15.PUT_TP,                o.PUT_TP),
        ('bc15.PUT_SL',                bc15.PUT_SL,                o.PUT_SL),
        ('bc15.PUT_NET_TP',            bc15.PUT_NET_TP,            o.PUT_NET_TP),
        ('bc15.PUT_NET_SL',            bc15.PUT_NET_SL,            o.PUT_NET_SL),
        ('bc15.PUT_TP_SIGMA',          bc15.PUT_TP_SIGMA,          s.PUT_TP_SIGMA),
        ('bc15.PUT_SL_SIGMA',          bc15.PUT_SL_SIGMA,          s.PUT_SL_SIGMA),
        ('bc15.PUT_THRESHOLD',         bc15.PUT_THRESHOLD,         s.PUT_THRESHOLD),
        ('bc15.PUT_SL_HOLD_BARS_DEFAULT', bc15.PUT_SL_HOLD_BARS_DEFAULT, o.PUT_SL_HOLD_BARS_DEFAULT),
        ('bc15.PUT_SL_HOLD_BARS_MONDAY',  bc15.PUT_SL_HOLD_BARS_MONDAY,  o.PUT_SL_HOLD_BARS_MONDAY),
        # Premium / delta
        ('bc15.PREMIUM_MULT_15',       bc15.PREMIUM_MULT_15,       s.PREMIUM_MULT),
        ('bc15.DELTA',                 bc15.DELTA,                 o.DELTA),
        # Slippage
        ('bc15.SLIP_ENTRY',            bc15.SLIP_ENTRY,            o.SLIP_ENTRY),
        ('bc15.SLIP_TP',               bc15.SLIP_TP,               o.SLIP_TP),
        ('bc15.SLIP_SL',               bc15.SLIP_SL,               o.SLIP_SL),
        ('bc15.SLIP_HARD',             bc15.SLIP_HARD,             o.SLIP_HARD),
        # F3f
        ('bc15.F3F_CALL_THRESH',       bc15.F3F_CALL_THRESH,       s.F3F_CALL_THRESH),
        ('bc15.F3F_CALL_FLOOR',        bc15.F3F_CALL_FLOOR,        s.F3F_CALL_FLOOR),
        ('bc15.F3F_CALL_LOW',          bc15.F3F_CALL_LOW,          s.F3F_CALL_LOW),
        ('bc15.F3F_PUT_THRESH',        bc15.F3F_PUT_THRESH,        s.F3F_PUT_THRESH),
        ('bc15.F3F_PUT_FLOOR',         bc15.F3F_PUT_FLOOR,         s.F3F_PUT_FLOOR),
        ('bc15.F3F_PUT_HIGH',          bc15.F3F_PUT_HIGH,          s.F3F_PUT_HIGH),
        ('bc15.ALLOC_SCALE_FLOOR',     bc15.ALLOC_SCALE_FLOOR,     s.ALLOC_SCALE_FLOOR),
        ('bc15.ALLOC_SCALE_CEIL',      bc15.ALLOC_SCALE_CEIL,      s.ALLOC_SCALE_CEIL),
        # Regime slopes
        ('bc15.REGIME_SLOPE',          bc15.REGIME_SLOPE,          s.REGIME_SLOPE),
        ('bc15.REGIME_SLOPE_PUT',      bc15.REGIME_SLOPE_PUT,      s.REGIME_SLOPE_PUT),
        ('bc15.REGIME_SLOPE_UP',       bc15.REGIME_SLOPE_UP,       s.REGIME_SLOPE_UP),
        ('bc15.REGIME_SLOPE_DOWN',     bc15.REGIME_SLOPE_DOWN,     s.REGIME_SLOPE_DOWN),
        ('bc15.REGIME_SLOPE_PUT_UP',   bc15.REGIME_SLOPE_PUT_UP,   s.REGIME_SLOPE_PUT_UP),
        ('bc15.BREADTH_ALLOC_ENABLED', bc15.BREADTH_ALLOC_ENABLED, s.BREADTH_ALLOC_ENABLED),
        # DD soft band
        ('bc15.DD_SOFT_BAND_LO',       bc15.DD_SOFT_BAND_LO,       s.DD_SOFT_BAND_LO),
        ('bc15.DD_SOFT_BAND_HI',       bc15.DD_SOFT_BAND_HI,       s.DD_SOFT_BAND_HI),
        ('bc15.DD_SOFT_CALL_FLOOR',    bc15.DD_SOFT_CALL_FLOOR,    s.DD_SOFT_CALL_FLOOR),
        # Dead-hold
        ('bc15.DEAD_HOLD_ENABLED',     bc15.DEAD_HOLD_ENABLED,     s.DEAD_HOLD_ENABLED),
        ('bc15.DEAD_HOLD_TRIGGER_PNL', bc15.DEAD_HOLD_TRIGGER_PNL, s.DEAD_HOLD_TRIGGER_PNL),
        ('bc15.DEAD_HOLD_POPOUT_PNL',  bc15.DEAD_HOLD_POPOUT_PNL,  s.DEAD_HOLD_POPOUT_PNL),
        # Earnings put suppression
        ('bc15.EARN_SUPP_PUT',         bc15.EARN_SUPP_PUT,         s.EARN_SUPP_PUT),
        ('bc15.EARN_SUPP_PUT_DAYS',    bc15.EARN_SUPP_PUT_DAYS,    s.EARN_SUPP_PUT_DAYS),
        ('bc15.EARN_SUPP_PUT_MIN_OV',  bc15.EARN_SUPP_PUT_MIN_OV,  s.EARN_SUPP_PUT_MIN_OV),
        ('bc15.EARN_SUPP_PUT_MAX_OV',  bc15.EARN_SUPP_PUT_MAX_OV,  s.EARN_SUPP_PUT_MAX_OV),
        # Weak-weekly call filter
        ('bc15.WEAK_WEEKLY_CALL_DROP',     bc15.WEAK_WEEKLY_CALL_DROP,     s.WEAK_WEEKLY_CALL_DROP),
        ('bc15.WEAK_WEEKLY_CALL_MIN_OV',   bc15.WEAK_WEEKLY_CALL_MIN_OV,   s.WEAK_WEEKLY_CALL_MIN_OV),
        ('bc15.WEAK_WEEKLY_CALL_MAX_OV',   bc15.WEAK_WEEKLY_CALL_MAX_OV,   s.WEAK_WEEKLY_CALL_MAX_OV),
        ('bc15.WEAK_WEEKLY_CALL_WADJ_LT',  bc15.WEAK_WEEKLY_CALL_WADJ_LT,  s.WEAK_WEEKLY_CALL_WADJ_LT),
        ('bc15.WEAK_WEEKLY_CALL_STOCH_GE', bc15.WEAK_WEEKLY_CALL_STOCH_GE, s.WEAK_WEEKLY_CALL_STOCH_GE),
        # CT promotion
        ('bc15.CT_PROMOTE',            bc15.CT_PROMOTE,            s.CT_PROMOTE),
        ('bc15.CT_PUT_TREND_MIN',      bc15.CT_PUT_TREND_MIN,      s.CT_PUT_TREND_MIN),
        ('bc15.CT_CALL_TREND_MAX',     bc15.CT_CALL_TREND_MAX,     s.CT_CALL_TREND_MAX),
        # SAW Put U-curve — SHIPPED 2026-05-09 for 15 DTE
        ('bc15.SAW_PUT_UCURVE_ENABLED',   bool(bc15.SAW_PUT_UCURVE_ENABLED), s.SAW_PUT_UCURVE_ENABLED),
        ('bc15.SAW_PUT_UCURVE_SHAPE',     bc15.SAW_PUT_UCURVE_SHAPE,         s.SAW_PUT_UCURVE_SHAPE),
        ('bc15.SAW_PUT_UCURVE_MIDPOINT',  bc15.SAW_PUT_UCURVE_MIDPOINT,      s.SAW_PUT_UCURVE_MIDPOINT),
        ('bc15.SAW_PUT_UCURVE_HALFWIDTH', bc15.SAW_PUT_UCURVE_HALFWIDTH,     s.SAW_PUT_UCURVE_HALFWIDTH),
        ('bc15.SAW_PUT_UCURVE_FLOOR',     bc15.SAW_PUT_UCURVE_FLOOR,         s.SAW_PUT_UCURVE_FLOOR),
        ('bc15.SAW_PUT_UCURVE_CEIL',      bc15.SAW_PUT_UCURVE_CEIL,          s.SAW_PUT_UCURVE_CEIL),
        ('bc15.SAW_PUT_UCURVE_POWER',     bc15.SAW_PUT_UCURVE_POWER,         s.SAW_PUT_UCURVE_POWER),
        ('bc15.SAW_PUT_UCURVE_K',         bc15.SAW_PUT_UCURVE_K,             s.SAW_PUT_UCURVE_K),
    ]

    # bc15 tier dicts: same score-range key style as bc30
    bc15_tier_map = [
        ('95+',    'ultra'),
        ('85-94',  'top'),
        ('80-84',  'mid'),
        ('75-79',  'low'),
        ('70-74',  'overflow'),
    ]
    for bc_k, cfg_k in bc15_tier_map:
        assert _approx(bc15.TIER_ALLOC[bc_k], s.TIER_ALLOC[cfg_k]), \
            f'bc15.TIER_ALLOC[{bc_k!r}] drift: bc15={bc15.TIER_ALLOC[bc_k]} cfg.{cfg_k}={s.TIER_ALLOC[cfg_k]}'
    bc15_put_map = [
        ('p<=15',  'put_top'),
        ('p16-20', 'put_mid'),
        ('p21-25', 'put_low'),
    ]
    for bc_k, cfg_k in bc15_put_map:
        assert _approx(bc15.TIER_ALLOC[bc_k], s.PUT_TIER_ALLOC[cfg_k]), \
            f'bc15.TIER_ALLOC[{bc_k!r}] drift: bc15={bc15.TIER_ALLOC[bc_k]} cfg.PUT.{cfg_k}={s.PUT_TIER_ALLOC[cfg_k]}'

    return pairs_mc15, pairs_bc15


def check_dte_field_parity():
    """Schema parity: every field on STRATEGY_30DTE must exist on STRATEGY_15DTE
    and vice-versa. Catches the case where a new mechanism's config field is
    added to one DTE only — currently latent (dataclass enforces parity by
    construction) but the test makes the invariant explicit and survives
    refactors that might split the dataclass.
    """
    import strategy_config as cfg
    from dataclasses import fields

    f30 = {f.name for f in fields(cfg.STRATEGY_30DTE)}
    f15 = {f.name for f in fields(cfg.STRATEGY_15DTE)}
    only_30 = f30 - f15
    only_15 = f15 - f30
    assert not only_30, (
        f'Fields on STRATEGY_30DTE missing from STRATEGY_15DTE: {sorted(only_30)}. '
        f'Add the field to the 15 DTE config (use the disabled-default value if not '
        f'validated for 15 DTE) and document the asymmetry.')
    assert not only_15, (
        f'Fields on STRATEGY_15DTE missing from STRATEGY_30DTE: {sorted(only_15)}. '
        f'Add the field to the 30 DTE config.')

    # OptionStrategyConfig parity
    o30 = {f.name for f in fields(cfg.STRATEGY_30DTE.option)}
    o15 = {f.name for f in fields(cfg.STRATEGY_15DTE.option)}
    assert o30 == o15, (
        f'OptionStrategyConfig field divergence: 30={sorted(o30)} 15={sorted(o15)}')

    return len(f30) + len(o30)  # checks count for the runner


# ── Source-code scanner for trader.py and api.py ────────────────────────────
#
# Catches hardcoded literal assignments to known strategy params in files
# that aren't directly importable for value comparison (function-local vars).
#
# A line matching `^\s*PARAM_NAME\s*=\s*<numeric_or_bool_literal>` is
# considered a drift candidate. The literal must match either the 30DTE
# or 15DTE strategy_config value (whichever DTE the file/block targets);
# if it matches NEITHER, fail.

# Params with potentially-different 30DTE vs 15DTE values
_DTE_AWARE_PARAMS = {
    # name → (30dte_path, 15dte_path) — paths into STRATEGY_*DTE
    'F3F_CALL_THRESH':         ('F3F_CALL_THRESH',         'F3F_CALL_THRESH'),
    'F3F_CALL_FLOOR':          ('F3F_CALL_FLOOR',          'F3F_CALL_FLOOR'),
    'F3F_CALL_LOW':            ('F3F_CALL_LOW',            'F3F_CALL_LOW'),
    'F3F_PUT_THRESH':          ('F3F_PUT_THRESH',          'F3F_PUT_THRESH'),
    'F3F_PUT_FLOOR':           ('F3F_PUT_FLOOR',           'F3F_PUT_FLOOR'),
    'F3F_PUT_HIGH':            ('F3F_PUT_HIGH',            'F3F_PUT_HIGH'),
    'MAX_POSITIONS':           ('MAX_POSITIONS',           'MAX_POSITIONS'),
    'MAX_POSITIONS_CALL':      ('MAX_POSITIONS_CALL',      'MAX_POSITIONS_CALL'),
    'MAX_POSITIONS_PUT':       ('MAX_POSITIONS_PUT',       'MAX_POSITIONS_PUT'),
    'PRACTICAL_EXPOSURE_ENABLED': ('PRACTICAL_EXPOSURE_ENABLED', 'PRACTICAL_EXPOSURE_ENABLED'),
    'PRACTICAL_CAPITAL_CEILING': ('PRACTICAL_CAPITAL_CEILING', 'PRACTICAL_CAPITAL_CEILING'),
    'GROSS_PREMIUM_CAP':       ('GROSS_PREMIUM_CAP',       'GROSS_PREMIUM_CAP'),
    'CALL_PREMIUM_CAP':        ('CALL_PREMIUM_CAP',        'CALL_PREMIUM_CAP'),
    'PUT_PREMIUM_CAP':         ('PUT_PREMIUM_CAP',         'PUT_PREMIUM_CAP'),
    'OPP_SAT_CALL_REF':        ('OPP_SAT_CALL_REF',        'OPP_SAT_CALL_REF'),
    'OPP_SAT_PUT_REF':         ('OPP_SAT_PUT_REF',         'OPP_SAT_PUT_REF'),
    'OPP_SAT_POWER':           ('OPP_SAT_POWER',           'OPP_SAT_POWER'),
    'OPP_SAT_FLOOR':           ('OPP_SAT_FLOOR',           'OPP_SAT_FLOOR'),
    'HOLD_DAYS':               ('HOLD_DAYS',               'HOLD_DAYS'),
    'PREMIUM_MULT':            ('PREMIUM_MULT',            'PREMIUM_MULT'),
    'HARD_SELL_LOSS':          ('HARD_SELL_LOSS',          'HARD_SELL_LOSS'),
    'PUT_THRESHOLD':           ('PUT_THRESHOLD',           'PUT_THRESHOLD'),
    'PUT_TP_SIGMA':            ('PUT_TP_SIGMA',            'PUT_TP_SIGMA'),
    'PUT_SL_SIGMA':            ('PUT_SL_SIGMA',            'PUT_SL_SIGMA'),
    'BREADTH_ALLOC_ENABLED':   ('BREADTH_ALLOC_ENABLED',   'BREADTH_ALLOC_ENABLED'),
    'CT_PROMOTE':              ('CT_PROMOTE',              'CT_PROMOTE'),
    'CT_PUT_TREND_MIN':        ('CT_PUT_TREND_MIN',        'CT_PUT_TREND_MIN'),
    'CT_CALL_TREND_MAX':       ('CT_CALL_TREND_MAX',       'CT_CALL_TREND_MAX'),
    'EARN_SUPP_PUT':           ('EARN_SUPP_PUT',           'EARN_SUPP_PUT'),
    'EARN_SUPP_PUT_DAYS':      ('EARN_SUPP_PUT_DAYS',      'EARN_SUPP_PUT_DAYS'),
    'EARN_SUPP_PUT_MIN_OV':    ('EARN_SUPP_PUT_MIN_OV',    'EARN_SUPP_PUT_MIN_OV'),
    'EARN_SUPP_PUT_MAX_OV':    ('EARN_SUPP_PUT_MAX_OV',    'EARN_SUPP_PUT_MAX_OV'),
    'DEAD_HOLD_ENABLED':       ('DEAD_HOLD_ENABLED',       'DEAD_HOLD_ENABLED'),
    'DEAD_HOLD_TRIGGER_PNL':   ('DEAD_HOLD_TRIGGER_PNL',   'DEAD_HOLD_TRIGGER_PNL'),
    'DEAD_HOLD_POPOUT_PNL':    ('DEAD_HOLD_POPOUT_PNL',    'DEAD_HOLD_POPOUT_PNL'),
    'REGIME_SLOPE':            ('REGIME_SLOPE',            'REGIME_SLOPE'),
    'REGIME_SLOPE_PUT':        ('REGIME_SLOPE_PUT',        'REGIME_SLOPE_PUT'),
    'ALLOC_SCALE_FLOOR':       ('ALLOC_SCALE_FLOOR',       'ALLOC_SCALE_FLOOR'),
    'ALLOC_SCALE_CEIL':        ('ALLOC_SCALE_CEIL',        'ALLOC_SCALE_CEIL'),
    'REGIME_SLOPE_UP':         ('REGIME_SLOPE_UP',         'REGIME_SLOPE_UP'),
    'REGIME_SLOPE_DOWN':       ('REGIME_SLOPE_DOWN',       'REGIME_SLOPE_DOWN'),
    'REGIME_SLOPE_PUT_UP':     ('REGIME_SLOPE_PUT_UP',     'REGIME_SLOPE_PUT_UP'),
    'REGIME_SLOPE_PUT_DOWN':   ('REGIME_SLOPE_PUT_DOWN',   'REGIME_SLOPE_PUT_DOWN'),
}

# Option-level params (path goes through .option)
_OPT_AWARE_PARAMS = {
    'TP_BASE':                ('option.TP_BASE',          'option.TP_BASE'),
    'TP_STRESS':              ('option.TP_STRESS',        'option.TP_STRESS'),
    'SL_BASE':                ('option.SL_BASE',          'option.SL_BASE'),
    'SL_STRESS':              ('option.SL_STRESS',        'option.SL_STRESS'),
    'PUT_TP':                 ('option.PUT_TP',           'option.PUT_TP'),
    'PUT_SL':                 ('option.PUT_SL',           'option.PUT_SL'),
    'PUT_NET_TP':             ('option.PUT_NET_TP',       'option.PUT_NET_TP'),
    'PUT_NET_SL':             ('option.PUT_NET_SL',       'option.PUT_NET_SL'),
    'NET_HARD_SELL':          ('NET_HARD_SELL',           'NET_HARD_SELL'),
    'PUT_SL_HOLD_BARS_DEFAULT': ('option.PUT_SL_HOLD_BARS_DEFAULT', 'option.PUT_SL_HOLD_BARS_DEFAULT'),
    'PUT_SL_HOLD_BARS_MONDAY':  ('option.PUT_SL_HOLD_BARS_MONDAY',  'option.PUT_SL_HOLD_BARS_MONDAY'),
}

# Compile literal-assignment regex once
# Matches: optional whitespace, NAME, =, optional sign, number-or-bool-or-None
_LITERAL_PATTERN = re.compile(
    r'^\s*([A-Z_][A-Z0-9_]+)\s*=\s*([+\-]?\d+(?:\.\d+)?|True|False|None)\s*(?:#.*)?$'
)


def _resolve_path(strategy, path):
    """Walk a dotted path like 'option.TP_BASE' on a strategy config."""
    obj = strategy
    for part in path.split('.'):
        obj = getattr(obj, part)
    return obj


def _parse_literal(s):
    s = s.strip()
    if s == 'True':  return True
    if s == 'False': return False
    if s == 'None':  return None
    return float(s)


def _values_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if a is None or b is None:
        return a == b
    return _approx(a, b, tol=1e-6)


def scan_source_for_drift(filename, all_params):
    """Scan a Python source file for hardcoded strategy param literals.

    Returns list of drift records: (line_no, name, observed, expected_30, expected_15).
    A drift is reported only when the literal matches NEITHER 30DTE nor 15DTE.
    """
    import strategy_config as cfg
    s30 = cfg.STRATEGY_30DTE
    s15 = cfg.STRATEGY_15DTE

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)
    drifts = []
    checks = 0

    with open(path, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, start=1):
            m = _LITERAL_PATTERN.match(line)
            if not m:
                continue
            name, raw = m.group(1), m.group(2)
            if name not in all_params:
                continue
            try:
                observed = _parse_literal(raw)
            except (ValueError, TypeError):
                continue

            checks += 1
            p30, p15 = all_params[name]
            try:
                exp_30 = _resolve_path(s30, p30)
                exp_15 = _resolve_path(s15, p15)
            except AttributeError:
                continue

            if not _values_equal(observed, exp_30) and not _values_equal(observed, exp_15):
                drifts.append((lineno, name, observed, exp_30, exp_15))

    return drifts, checks


def check_source_drift():
    """Scan trader.py and api.py for hardcoded literals that drift from strategy_config."""
    all_params = {**_DTE_AWARE_PARAMS, **_OPT_AWARE_PARAMS}
    total_checks = 0
    all_drifts = []
    for fname in ('trader.py', 'api.py'):
        drifts, checks = scan_source_for_drift(fname, all_params)
        total_checks += checks
        for d in drifts:
            all_drifts.append((fname, *d))
    return all_drifts, total_checks


_SYMBOL_SURFACE_ALLOW = 'symbol-specific-surface-ok:'
_SYMBOL_SURFACE_FILES = (
    'strategy_config.py',
    'database/utils/scoring.py',
    'database/models/core.py',
    'simulator.py',
    'market_breadth.py',
)
_SYMBOL_SURFACE_PATTERNS = (
    re.compile(r'\bPOET_[A-Z0-9_]+\b'),
    re.compile(r"""['"]poet_[a-z0-9_]+['"]"""),
)


def _has_symbol_surface_allow(lines, idx):
    """Allow a ticker-specific production surface only with nearby rationale."""
    start = max(0, idx - 2)
    return any(_SYMBOL_SURFACE_ALLOW in lines[i] for i in range(start, idx + 1))


def check_symbol_specific_surfaces():
    """Soft-review ticker-named production scoring/config surfaces.

    Motivating symbols belong in experiments and docs. If a production mechanism
    truly needs a ticker-specific name, keep it explicit with:
    # symbol-specific-surface-ok: <reason>
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    findings = []
    checks = 0
    for fname in _SYMBOL_SURFACE_FILES:
        path = os.path.join(root, fname)
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for idx, line in enumerate(lines):
            for pattern in _SYMBOL_SURFACE_PATTERNS:
                checks += 1
                m = pattern.search(line)
                if m and not _has_symbol_surface_allow(lines, idx):
                    findings.append((fname, idx + 1, m.group(0)))
    return findings, checks


def check_scoring():
    """Verify database/utils/scoring.py module-level constants mirror SCORING.

    Score-stage constants are DTE-agnostic: a single SCORING singleton in
    strategy_config.py is the source of truth, and scoring.py reads each
    field via os.environ.get(NAME, str(SCORING.NAME)) so env-overrides
    persist for sweeps. Every field on ScoringConfig must have a matching
    module-level binding in scoring.py — the drift-guard catches any
    divergence (e.g. someone edits SCORING but forgets the scoring.py
    re-export, or vice versa).
    """
    import strategy_config as cfg
    import database.utils.scoring as sc
    from dataclasses import fields

    pairs = []
    skip_for_module_check = {
        'SECTOR_BREADTH_WAVE_PARAMS',
        'DAILY_VOLUME_AUTHORITY_WAVE_PARAMS',
    }

    for f in fields(cfg.ScoringConfig):
        if f.name in skip_for_module_check:
            continue
        expected = getattr(cfg.SCORING, f.name)
        observed = getattr(sc, f.name, '__MISSING__')
        pairs.append((f.name, observed, expected))
    for key, expected in cfg.SCORING.SECTOR_BREADTH_WAVE_PARAMS.items():
        observed = sc.SECTOR_BREADTH_WAVE_PARAMS.get(key, '__MISSING__')
        pairs.append((f'SECTOR_BREADTH_WAVE_PARAMS[{key}]', observed, expected))
    for key, expected in cfg.SCORING.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.items():
        observed = sc.DAILY_VOLUME_AUTHORITY_WAVE_PARAMS.get(key, '__MISSING__')
        pairs.append((f'DAILY_VOLUME_AUTHORITY_WAVE_PARAMS[{key}]', observed, expected))
    return pairs


def main():
    failures = []
    pairs_30_mc, pairs_30_bc = check_30dte()
    pairs_mc15, pairs_bc15 = check_15dte()
    parity_checks = check_dte_field_parity()
    pairs_scoring = check_scoring()

    all_pairs = pairs_30_mc + pairs_30_bc + pairs_mc15 + pairs_bc15 + pairs_scoring
    for name, observed, expected in all_pairs:
        if isinstance(observed, bool) or isinstance(expected, bool):
            ok = observed == expected
        elif isinstance(observed, (tuple, list, set)) or isinstance(expected, (tuple, list, set)):
            ok = tuple(observed) == tuple(expected)
        elif observed is None or expected is None:
            ok = observed == expected
        elif isinstance(observed, str) or isinstance(expected, str):
            ok = observed == expected
        else:
            ok = _approx(observed, expected)
        if not ok:
            failures.append((name, observed, expected))

    # Source-code drift scan for trader.py + api.py
    src_drifts, src_checks = check_source_drift()
    symbol_findings, symbol_checks = check_symbol_specific_surfaces()

    n_total = len(all_pairs)

    if failures or src_drifts or symbol_findings:
        if failures:
            print(f'DRIFT DETECTED in {len(failures)}/{n_total} import-based checks:')
            for name, obs, exp in failures:
                print(f'  {name}: observed={obs!r}  expected={exp!r}')
        if src_drifts:
            print(f'DRIFT DETECTED in {len(src_drifts)}/{src_checks} source-code scans:')
            for fname, lineno, name, obs, exp30, exp15 in src_drifts:
                print(f'  {fname}:{lineno}  {name} = {obs!r}  expected 30DTE={exp30!r} or 15DTE={exp15!r}')
        if symbol_findings:
            print(f'REVIEW REQUIRED in {len(symbol_findings)}/{symbol_checks} symbol-surface scans:')
            print('  Ticker-named scoring/config surfaces need a mechanism name, or a nearby')
            print(f'  "# {_SYMBOL_SURFACE_ALLOW} <reason>" comment when genuinely intentional.')
            for fname, lineno, token in symbol_findings:
                print(f'  {fname}:{lineno}  {token}')
        sys.exit(1)
    print(f'OK: all {n_total} strategy constants '
          f'({len(pairs_30_mc)} mc + {len(pairs_30_bc)} bc + '
          f'{len(pairs_mc15)} mc15 + {len(pairs_bc15)} bc15 + '
          f'{len(pairs_scoring)} scoring) '
          f'+ {parity_checks} schema-parity fields '
          f'+ {src_checks} source-code scans '
          f'+ {symbol_checks} symbol-surface scans match strategy_config.py')


if __name__ == '__main__':
    main()
