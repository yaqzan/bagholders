import random

import iv_crush_model
import option_pricing
from option_pricing import option_pnl_pct, sample_vega_ratio


def test_sample_vega_ratio_never_boosts_post_earnings_pnl():
    original_cache = option_pricing._VEGA_CACHE
    option_pricing._VEGA_CACHE = {('CALL', '22-35'): [4.35]}
    try:
        ratio = sample_vega_ratio('CALL', 30, random.Random(0))
    finally:
        option_pricing._VEGA_CACHE = original_cache

    assert ratio == 1.0


def test_sample_vega_ratio_preserves_crush_haircut():
    original_cache = option_pricing._VEGA_CACHE
    option_pricing._VEGA_CACHE = {('CALL', '22-35'): [0.72]}
    try:
        ratio = sample_vega_ratio('CALL', 30, random.Random(0))
    finally:
        option_pricing._VEGA_CACHE = original_cache

    assert ratio == 0.72


def test_legacy_iv_crush_price_ratio_never_boosts():
    original_samples = iv_crush_model._SAMPLES
    original_rng = iv_crush_model._SAMPLES_RNG
    iv_crush_model._SAMPLES = {('CALL', '22-35'): [3.0]}
    iv_crush_model._SAMPLES_RNG = random.Random(0)
    try:
        ratio = iv_crush_model.sample_price_ratio('CALL', 30)
    finally:
        iv_crush_model._SAMPLES = original_samples
        iv_crush_model._SAMPLES_RNG = original_rng

    assert ratio == 1.0


def test_legacy_iv_crush_price_ratio_preserves_haircut():
    original_samples = iv_crush_model._SAMPLES
    original_rng = iv_crush_model._SAMPLES_RNG
    iv_crush_model._SAMPLES = {('CALL', '22-35'): [0.67]}
    iv_crush_model._SAMPLES_RNG = random.Random(0)
    try:
        ratio = iv_crush_model.sample_price_ratio('CALL', 30)
    finally:
        iv_crush_model._SAMPLES = original_samples
        iv_crush_model._SAMPLES_RNG = original_rng

    assert ratio == 0.67


def test_adverse_call_move_cannot_become_profit_from_earnings_vega():
    original_cache = option_pricing._VEGA_CACHE
    option_pricing._VEGA_CACHE = {('CALL', '22-35'): [4.35]}
    try:
        vega_ratio = sample_vega_ratio('CALL', 30, random.Random(0))
    finally:
        option_pricing._VEGA_CACHE = original_cache

    pnl = option_pnl_pct(
        'call',
        current_underlying=96.54,
        entry_underlying=102.23,
        bars_held=2,
        premium_pct=1.82 * 5.66 / 100.0,
        total_dte=30,
        vega_ratio=vega_ratio,
        delta=0.5,
    )

    assert pnl < 0


def test_assessment_option_pnl_uses_non_boosting_pricing_path():
    import assess_scores

    assess_scores.set_dte_strategy('30', 'tp')
    pnl = assess_scores._compute_option_pnl(
        side='low',
        fire_type=2,
        exit_bars=2,
        exit_close=96.54,
        entry_close=102.23,
        fire_open=100.0,
        fire_high=101.0,
        fire_low=96.0,
        sigma_pct=5.66,
    )

    assert pnl < 0
