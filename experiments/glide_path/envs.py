#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Env-var recipes for the glide-path arms (DESIGN.md section 3/8/13).

CORE_ENV is IMPORTED from experiments.lifecycle_mc.envs (itself source-verified
against strategy_config.py / portfolio_profiles.json's "core" entry) -- never
retyped. SENTINEL_ENV is built PROGRAMMATICALLY from
algorithm_versions/portfolio_profiles.json's "sentinel" entry via the same
param->strategy-attr mapping portfolio_profiles.py itself uses
(PROFILE_STRATEGY_ATTRS / PROFILE_TIER_ATTRS), translated to the monte_carlo.py
env-override names CORE_ENV already uses -- a mechanical transcription with a
hard assertion that the result differs from CORE_ENV on EXACTLY the five
expected fields (DESIGN.md section 3b: exposure x2, selectivity x2, ceiling x1).

The *_NO_DDSB variants force the DD_SOFT_BAND mechanism to its documented
no-op state (LO=0.0, HI=0.0, FLOOR=1.0 -- mechanism_registry.py: "all zero
defaults / floor=1.0 = mechanism disabled") for the section-8 interaction
ablation.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.lifecycle_mc.envs import CORE_ENV  # noqa: E402  (source-verified there)
import portfolio_profiles as _pp  # noqa: E402  (JSON registry read; no MySQL)

# profile-params key  ->  monte_carlo.py env-override name (the exact names
# CORE_ENV uses; MAX_POSITIONS maps to MAX_POSITIONS_OVERRIDE, tiers to *_OV).
PARAM_TO_ENV = {
    'tier_ultra': 'TIER_ULTRA_OV', 'tier_top': 'TIER_TOP_OV',
    'tier_mid': 'TIER_MID_OV', 'tier_low': 'TIER_LOW_OV',
    'tier_overflow': 'TIER_OVERFLOW_OV',
    'put_top': 'PUT_TIER_TOP_OV', 'put_mid': 'PUT_TIER_MID_OV', 'put_low': 'PUT_TIER_LOW_OV',
    'max_positions': 'MAX_POSITIONS_OVERRIDE', 'call_max': 'MAX_POSITIONS_CALL',
    'put_max': 'MAX_POSITIONS_PUT',
    'gross_cap': 'GROSS_PREMIUM_CAP', 'call_cap': 'CALL_PREMIUM_CAP', 'put_cap': 'PUT_PREMIUM_CAP',
    'call_ref': 'OPP_SAT_CALL_REF', 'put_ref': 'OPP_SAT_PUT_REF',
    'sat_power': 'OPP_SAT_POWER', 'sat_floor': 'OPP_SAT_FLOOR',
    'practical_enabled': 'PRACTICAL_EXPOSURE_ENABLED',
    'capital_ceiling': 'PRACTICAL_CAPITAL_CEILING',
    'dd_lo': 'DD_SOFT_BAND_LO', 'dd_hi': 'DD_SOFT_BAND_HI', 'dd_floor': 'DD_SOFT_CALL_FLOOR',
}


def _fmt(v) -> str:
    if isinstance(v, bool):
        return '1' if v else '0'
    return str(v)


def build_sentinel_env() -> dict:
    params = _pp.profile_map()['sentinel']['params']
    unmapped = set(params) - set(PARAM_TO_ENV)
    if unmapped:
        raise AssertionError(f"sentinel params with no env mapping: {sorted(unmapped)} -- "
                             f"extend PARAM_TO_ENV deliberately, never silently drop.")
    env = {PARAM_TO_ENV[k]: _fmt(v) for k, v in params.items()}
    missing = set(CORE_ENV) - set(env)
    if missing:
        raise AssertionError(f"sentinel env missing keys CORE_ENV has: {sorted(missing)}")
    extra = set(env) - set(CORE_ENV)
    if extra:
        raise AssertionError(f"sentinel env has keys CORE_ENV lacks: {sorted(extra)}")
    return env


SENTINEL_ENV = build_sentinel_env()

# DESIGN.md section 3b: Sentinel differs from Core on EXACTLY these five fields.
EXPECTED_SENTINEL_DIFF = {
    'TIER_MID_OV', 'TIER_LOW_OV',                       # selectivity (85+-only)
    'GROSS_PREMIUM_CAP', 'CALL_PREMIUM_CAP',            # exposure 0.50 -> 0.30
    'PRACTICAL_CAPITAL_CEILING',                        # 0 (off) -> 1,000,000
}
_actual_diff = {k for k in CORE_ENV if float(CORE_ENV[k]) != float(SENTINEL_ENV[k])}
if _actual_diff != EXPECTED_SENTINEL_DIFF:
    raise AssertionError(
        f"SENTINEL_ENV vs CORE_ENV diff drifted: expected {sorted(EXPECTED_SENTINEL_DIFF)}, "
        f"got {sorted(_actual_diff)} -- portfolio_profiles.json changed under this design; "
        f"re-verify DESIGN.md section 3b before running anything.")

SENTINEL_CEILING = float(SENTINEL_ENV['PRACTICAL_CAPITAL_CEILING'])   # 1,000,000.0 shipped

# ---------------------------------------------------------------------------
# Section-8 ablation pair: DD_SOFT_BAND forced to its documented no-op state.
# ---------------------------------------------------------------------------
NODDSB_DIFF = {'DD_SOFT_BAND_LO': '0.0', 'DD_SOFT_BAND_HI': '0.0', 'DD_SOFT_CALL_FLOOR': '1.0'}
CORE_ENV_NO_DDSB = dict(CORE_ENV)
CORE_ENV_NO_DDSB.update(NODDSB_DIFF)
SENTINEL_ENV_NO_DDSB = dict(SENTINEL_ENV)
SENTINEL_ENV_NO_DDSB.update(NODDSB_DIFF)

# Arms THIS experiment builds under experiments/glide_path/results/panels/.
BUILD_ARMS = {
    'sentinel': SENTINEL_ENV,
    'core_noddsb': CORE_ENV_NO_DDSB,
    'sentinel_noddsb': SENTINEL_ENV_NO_DDSB,
}
# Arm gap-filled IN PLACE into lifecycle_mc's own panels dir (resume-safe; a
# complete panel costs zero subprocesses). Same env, same grid, same runner.
GAPFILL_ARMS = {'core': CORE_ENV}

PROVENANCE = {
    'core_recipe': 'imported from experiments.lifecycle_mc.envs.CORE_ENV (source-verified there '
                   'against strategy_config.py / portfolio_profiles.json "core").',
    'sentinel_recipe': 'built programmatically from portfolio_profiles.profile_map()["sentinel"]'
                       '["params"] via PARAM_TO_ENV; asserted to differ from CORE_ENV on exactly '
                       '{TIER_MID_OV, TIER_LOW_OV, GROSS_PREMIUM_CAP, CALL_PREMIUM_CAP, '
                       'PRACTICAL_CAPITAL_CEILING} (DESIGN.md section 3b).',
    'noddsb_recipe': 'DD_SOFT_BAND_LO=0.0 / HI=0.0 / FLOOR=1.0 -- the documented disabled state '
                     '(mechanism_registry.py DD_SOFT_BAND notes), DESIGN.md section 8.',
    'starting_cash': 'STARTING_CASH_OV left unset -- engine $50,000 default (panels are '
                     '$50k-nominal multiple generators; DESIGN.md section 6.2).',
}


if __name__ == '__main__':
    import json
    print(json.dumps({'SENTINEL_ENV': SENTINEL_ENV, 'CORE_ENV (imported)': CORE_ENV,
                      'NODDSB_DIFF': NODDSB_DIFF, 'SENTINEL_CEILING': SENTINEL_CEILING,
                      'PROVENANCE': PROVENANCE}, indent=2))
