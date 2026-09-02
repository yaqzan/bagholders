#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Env-var recipes for the lifecycle-MC arms. Every value is either IMPORTED from
an already-authoritative source or copied from a source file that itself states
it verified the values line-by-line against strategy_config.py -- nothing here
is hand-retyped from memory. See DESIGN.md section 3 for the full justification
of each recipe and section 9 for why the OLD experiments/ladder_vs_core panels
cannot be reused as-is (wrong gross cap).

SPRINT_ENV = STAGED 30-DTE Option A (experiments/apex_dte_dd/SHIP_HANDOFF.md
    "minimal, recommended drop-in"): FROZEN_ENV (today's live Apex 15-DTE
    recipe) with the 4 named fields changed. Built the same way
    experiments/apex_dte_dd/run_p03_evidence.py builds its own
    STAGED_N4_ENV -- FROZEN_ENV + OPTION_A_DIFF, never hand-retyped.
STAGED_N10_ENV = Option B (Pareto-best, n10) -- defined for completeness /
    a cheap future escalation (DESIGN.md section 11), NOT used in this
    screen's primary arm.
CORE_ENV = the shipped, unmodified Core profile. Copied verbatim from
    experiments/deep_crash_screen/run_screen.py's CORE_ENV, whose own
    docstring states it was "verified line-by-line against
    strategy_config.py" and matches algorithm_versions/portfolio_profiles.json
    's "core" entry byte-for-byte. Spelled out explicitly (not left unset)
    for self-documentation and robustness against stray env leakage from the
    queue worker's shell -- byte-identical to a bare invocation either way.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.holdout_oos_2026_12.run_h3_envelope import FROZEN_ENV as APEX_LIVE_15DTE_ENV  # noqa: E402

# ---------------------------------------------------------------------------
# Sprint arm -- STAGED 30-DTE Option A (drop-in, n4)
# ---------------------------------------------------------------------------
OPTION_A_DIFF = {
    'NOMINAL_CAL_DTE': '30', 'HOLD_CAL_DAYS': '27',
    'SL_BASE_OV': '-0.70', 'SL_STRESS_OV': '-0.70',
}
SPRINT_ENV = dict(APEX_LIVE_15DTE_ENV)
SPRINT_ENV.update(OPTION_A_DIFF)

# Option B (n10, Pareto-best) -- defined, NOT used as the primary sprint arm
# in this screen. See DESIGN.md section 11.
OPTION_B_EXTRA_DIFF = {
    'TIER_ULTRA_OV': '0.10', 'TIER_TOP_OV': '0.10', 'TIER_MID_OV': '0.10', 'TIER_LOW_OV': '0.10',
    'MAX_POSITIONS_OVERRIDE': '10', 'MAX_POSITIONS_CALL': '10',
    'GROSS_PREMIUM_CAP': '1.0', 'CALL_PREMIUM_CAP': '1.0',
}
STAGED_N10_ENV = dict(SPRINT_ENV)
STAGED_N10_ENV.update(OPTION_B_EXTRA_DIFF)

# ---------------------------------------------------------------------------
# Core arm -- shipped default, spelled out explicitly (byte-identical to a
# bare invocation). Copied verbatim from experiments/deep_crash_screen/
# run_screen.py's CORE_ENV.
# ---------------------------------------------------------------------------
CORE_ENV = {
    'TIER_ULTRA_OV': '0.20', 'TIER_TOP_OV': '0.15', 'TIER_MID_OV': '0.08',
    'TIER_LOW_OV': '0.03', 'TIER_OVERFLOW_OV': '0.0',
    'PUT_TIER_TOP_OV': '0.0', 'PUT_TIER_MID_OV': '0.0', 'PUT_TIER_LOW_OV': '0.0',
    'MAX_POSITIONS_OVERRIDE': '14', 'MAX_POSITIONS_CALL': '14', 'MAX_POSITIONS_PUT': '0',
    'GROSS_PREMIUM_CAP': '0.50', 'CALL_PREMIUM_CAP': '0.50', 'PUT_PREMIUM_CAP': '0.0',
    'OPP_SAT_CALL_REF': '16.0', 'OPP_SAT_PUT_REF': '4.0',
    'OPP_SAT_POWER': '0.50', 'OPP_SAT_FLOOR': '0.55',
    'PRACTICAL_EXPOSURE_ENABLED': '1', 'PRACTICAL_CAPITAL_CEILING': '0.0',
    'DD_SOFT_BAND_LO': '0.35', 'DD_SOFT_BAND_HI': '0.55', 'DD_SOFT_CALL_FLOOR': '0.40',
}

ARMS = {
    'sprint': SPRINT_ENV,
    'core': CORE_ENV,
}

# Informational: what changed vs the live baseline, for provenance in output JSON.
PROVENANCE = {
    'sprint_recipe': 'experiments/apex_dte_dd/SHIP_HANDOFF.md Option A (drop-in n4): '
                      'FROZEN_ENV + OPTION_A_DIFF (NOMINAL_CAL_DTE 15->30, HOLD_CAL_DAYS 13->27, '
                      'SL_BASE/STRESS_OV -0.85->-0.70; tiers/gross/call caps unchanged from live Apex).',
    'core_recipe': 'shipped Core profile, bare STRATEGY_30DTE defaults (byte-identical to unset), '
                   'source-verified in experiments/deep_crash_screen/run_screen.py.',
    'starting_cash': 'STARTING_CASH_OV left unset -- engine $50,000 default (matches every sibling '
                      'experiment: apex_dte_dd, concentration_2x, ladder_vs_core).',
}


if __name__ == '__main__':
    import json
    print(json.dumps({'SPRINT_ENV': SPRINT_ENV, 'CORE_ENV': CORE_ENV,
                       'STAGED_N10_ENV (not used in screen)': STAGED_N10_ENV,
                       'PROVENANCE': PROVENANCE}, indent=2))
