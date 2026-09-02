"""Unit tests for tools/slippage_report.py's pure logic (P3.7).

Covers the sign convention (`_cost_impact` must always report POSITIVE =
worse-than-modeled regardless of buy/sell direction -- the easiest thing to
get backwards in this tool) and the category/verdict helpers. No DB access
-- fills/positions are plain namespaces.

Run: python -m pytest tests/test_slippage_report.py -v
"""
from __future__ import annotations

import types

from tools.slippage_report import _category, _cost_impact, _verdict, N_FLOOR


def _fill(side='buy', fill_value_usd=100.0):
    return types.SimpleNamespace(side=side, fill_value_usd=fill_value_usd)


def _pos(status='closed', premium_usd=100.0, current_value_usd=100.0, exit_reason=None):
    return types.SimpleNamespace(status=status, premium_usd=premium_usd,
                                 current_value_usd=current_value_usd, exit_reason=exit_reason)


# ---------------------------------------------------------------------------
# _category
# ---------------------------------------------------------------------------
def test_category_buy_is_entry():
    assert _category(_fill(side='buy'), _pos()) == 'entry'


def test_category_buy_is_entry_even_with_no_position():
    assert _category(_fill(side='buy'), None) == 'entry'


def test_category_sell_tp():
    assert _category(_fill(side='sell'), _pos(exit_reason='tp')) == 'tp-exit'


def test_category_sell_forced_variants():
    for reason in ('sl', 'hard', 'dh_pop', 'dh_expiry', 'dh_open', 'prem',
                   'realloc', 'version_sweep', 'strategy_sweep'):
        assert _category(_fill(side='sell'), _pos(exit_reason=reason)) == 'forced-exit', reason


def test_category_sell_unknown_reason_is_other():
    assert _category(_fill(side='sell'), _pos(exit_reason=None)) == 'other-exit'
    assert _category(_fill(side='sell'), None) == 'other-exit'


# ---------------------------------------------------------------------------
# _cost_impact -- sign convention: POSITIVE always means worse-than-modeled
# ---------------------------------------------------------------------------
def test_cost_impact_none_when_unlinked():
    assert _cost_impact(_fill(), None) is None


def test_cost_impact_buy_paid_more_than_modeled_is_positive():
    # paid $110 real vs $100 modeled premium -- worse for the buyer -> positive
    fill = _fill(side='buy', fill_value_usd=110.0)
    pos = _pos(premium_usd=100.0)
    assert abs(_cost_impact(fill, pos) - 0.10) < 1e-9


def test_cost_impact_buy_paid_less_than_modeled_is_negative():
    fill = _fill(side='buy', fill_value_usd=90.0)
    pos = _pos(premium_usd=100.0)
    assert abs(_cost_impact(fill, pos) - (-0.10)) < 1e-9


def test_cost_impact_buy_zero_model_is_none():
    fill = _fill(side='buy', fill_value_usd=110.0)
    pos = _pos(premium_usd=0.0)
    assert _cost_impact(fill, pos) is None


def test_cost_impact_sell_received_less_than_modeled_is_positive():
    # received $90 real vs $100 modeled exit value -- worse for the seller -> positive
    fill = _fill(side='sell', fill_value_usd=90.0)
    pos = _pos(status='closed', current_value_usd=100.0)
    assert abs(_cost_impact(fill, pos) - 0.10) < 1e-9


def test_cost_impact_sell_received_more_than_modeled_is_negative():
    fill = _fill(side='sell', fill_value_usd=110.0)
    pos = _pos(status='closed', current_value_usd=100.0)
    assert abs(_cost_impact(fill, pos) - (-0.10)) < 1e-9


def test_cost_impact_sell_on_still_open_position_is_none():
    """A live mark on an open position is not a frozen exit valuation --
    must be excluded, not silently compared as if it were final."""
    fill = _fill(side='sell', fill_value_usd=90.0)
    pos = _pos(status='open', current_value_usd=100.0)
    assert _cost_impact(fill, pos) is None


def test_cost_impact_sell_zero_model_is_none():
    fill = _fill(side='sell', fill_value_usd=90.0)
    pos = _pos(status='closed', current_value_usd=0.0)
    assert _cost_impact(fill, pos) is None


# ---------------------------------------------------------------------------
# _verdict
# ---------------------------------------------------------------------------
def test_verdict_forced_worse_supports_canon():
    gap, verdict = _verdict(free_side_mean=0.0, forced_mean=0.02)
    assert gap > 0
    assert 'CONSISTENT' in verdict


def test_verdict_forced_not_worse_contradicts_canon():
    gap, verdict = _verdict(free_side_mean=0.02, forced_mean=0.0)
    assert gap < 0
    assert 'CONTRADICTS' in verdict


def test_verdict_within_noise_band_is_ambiguous():
    gap, verdict = _verdict(free_side_mean=0.001, forced_mean=0.003)
    assert 'AMBIGUOUS' in verdict


def test_n_floor_is_30_per_gameplan():
    assert N_FLOOR == 30
