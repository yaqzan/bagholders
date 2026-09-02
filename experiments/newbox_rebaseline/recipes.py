"""Single source of truth for arm/profile Monte Carlo env recipes used by this
package's runners (run_ecert.py, run_noise_floor.py's Core config,
run_pessimism_n1000.py, run_deep_screen_n1000.py). run_parity_gate.py and
run_tail_ablation_n1000.py deliberately do NOT use this file -- the former
sources its arm env VERBATIM from the archived results_p03_evidence/summary.json
(the frozen parity reference), the latter builds its own MOM_SCORE_PARQUET/
MOM_SCORE_COL pair directly (no portfolio-profile sizing knobs involved).

Every dict below is either:
  (a) a literal copy of a dict that already exists verbatim in a canonical
      experiment file (CORE_ENV, FROZEN_ENV/apex_live, STAGED_N10_ENV/apex_n10),
      cross-checked in run_recipe_selftest() by ast.literal_eval-extracting the
      SAME dict from source and asserting key-by-key equality; or
  (b) SENTINEL_ENV, built the same way experiments/glide_path/envs.py builds it
      (PARAM_TO_ENV mapping over algorithm_versions/portfolio_profiles.json's
      "sentinel" params), cross-checked by independently recomputing it from
      the raw JSON registry + an ast-extracted copy of PARAM_TO_ENV, WITHOUT
      importing experiments.glide_path.envs itself (that module's imports were
      empirically confirmed DB-free in this sandbox on 2026-07-29, but this
      file re-derives from source text anyway, per the locked spec's
      "source-parsing is the offline verification tool" discipline --
      insulates every runner here from a future edit to a sibling experiment
      module, DB-touching or not).

NEVER import monte_carlo / database.* / strategy_config / peewee here. The one
non-stdlib-adjacent read this file does is `algorithm_versions/portfolio_profiles.json`
(via plain `json.load` -- a data file, not a module import).
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from _common import HERE, ROOT, extract_dict_literal  # bare same-dir import
except ImportError:  # pragma: no cover - defensive, see bootstrap_syspath() note
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import HERE, ROOT, extract_dict_literal  # noqa: E402

PORTFOLIO_PROFILES_JSON = ROOT / 'algorithm_versions' / 'portfolio_profiles.json'

# Canonical source files each recipe below is re-declared from (for the
# selftest's ast-equality cross-checks).
_SRC_DEEP_CRASH_SCREEN = ROOT / 'experiments' / 'deep_crash_screen' / 'run_screen.py'
_SRC_PESSIMISM_CERT = ROOT / 'experiments' / 'pessimism_cert' / 'run_cert.py'
_SRC_LIFECYCLE_ENVS = ROOT / 'experiments' / 'lifecycle_mc' / 'envs.py'
_SRC_H3_ENVELOPE = ROOT / 'experiments' / 'holdout_oos_2026_12' / 'run_h3_envelope.py'
_SRC_GLIDE_PATH_ENVS = ROOT / 'experiments' / 'glide_path' / 'envs.py'
_SRC_P03_EVIDENCE_SUMMARY = (ROOT / 'experiments' / 'apex_dte_dd' /
                              'results_p03_evidence' / 'summary.json')

# ---------------------------------------------------------------------------
# core -- shipped Core profile, bare STRATEGY_30DTE defaults. Byte-identical
# across experiments/deep_crash_screen/run_screen.py, experiments/pessimism_cert/
# run_cert.py, and experiments/lifecycle_mc/envs.py (all three re-declare the
# same dict; see run_recipe_selftest()'s cross-file check).
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

# ---------------------------------------------------------------------------
# apex_live -- FROZEN live Apex 15-DTE recipe, imported verbatim (by re-
# declaration) from experiments/holdout_oos_2026_12/run_h3_envelope.py's
# FROZEN_ENV. Cross-checked against results_p03_evidence/summary.json's own
# recipes['baseline_live_apex_15dte'] too (a second independent source).
# ---------------------------------------------------------------------------
APEX_LIVE_ENV = {
    "NOMINAL_CAL_DTE": "15",
    "HOLD_CAL_DAYS": "13",
    "CALENDAR_HOLD": "1",
    "SL_BASE_OV": "-0.85",
    "SL_STRESS_OV": "-0.85",
    "TIER_ULTRA_OV": "0.25",
    "TIER_TOP_OV": "0.25",
    "TIER_MID_OV": "0.25",
    "TIER_LOW_OV": "0.25",
    "TIER_OVERFLOW_OV": "0.0",
    "PUT_TIER_TOP_OV": "0.0",
    "PUT_TIER_MID_OV": "0.0",
    "PUT_TIER_LOW_OV": "0.0",
    "MAX_POSITIONS_OVERRIDE": "4",
    "MAX_POSITIONS_CALL": "4",
    "MAX_POSITIONS_PUT": "0",
    "GROSS_PREMIUM_CAP": "0.9",
    "CALL_PREMIUM_CAP": "0.9",
    "PUT_PREMIUM_CAP": "0.0",
    "OPP_SAT_CALL_REF": "16.0",
    "OPP_SAT_PUT_REF": "4.0",
    "OPP_SAT_POWER": "0.5",
    "OPP_SAT_FLOOR": "0.55",
    "PRACTICAL_EXPOSURE_ENABLED": "1",
    "PRACTICAL_CAPITAL_CEILING": "0.0",
    "DD_SOFT_BAND_LO": "0.35",
    "DD_SOFT_BAND_HI": "0.55",
    "DD_SOFT_CALL_FLOOR": "0.4",
}

# ---------------------------------------------------------------------------
# apex_n10 -- Option B (Pareto-best, n10): APEX_LIVE_ENV + OPTION_A_DIFF (the
# staged-30DTE drop-in) + OPTION_B_EXTRA_DIFF (the n10 sizing escalation).
# Re-declared from experiments/lifecycle_mc/envs.py's SPRINT_ENV/STAGED_N10_ENV
# construction (itself built the same way experiments/apex_dte_dd/
# run_p03_evidence.py builds STAGED_N4_ENV/STAGED_N10_ENV -- never hand-retyped
# there either). Cross-checked against results_p03_evidence/summary.json's
# recipes['staged_30dte_n10'] as a second independent source.
# ---------------------------------------------------------------------------
OPTION_A_DIFF = {
    'NOMINAL_CAL_DTE': '30', 'HOLD_CAL_DAYS': '27',
    'SL_BASE_OV': '-0.70', 'SL_STRESS_OV': '-0.70',
}
OPTION_B_EXTRA_DIFF = {
    'TIER_ULTRA_OV': '0.10', 'TIER_TOP_OV': '0.10', 'TIER_MID_OV': '0.10', 'TIER_LOW_OV': '0.10',
    'MAX_POSITIONS_OVERRIDE': '10', 'MAX_POSITIONS_CALL': '10',
    'GROSS_PREMIUM_CAP': '1.0', 'CALL_PREMIUM_CAP': '1.0',
}
APEX_N10_ENV = dict(APEX_LIVE_ENV)
APEX_N10_ENV.update(OPTION_A_DIFF)
APEX_N10_ENV.update(OPTION_B_EXTRA_DIFF)

# ---------------------------------------------------------------------------
# sentinel -- built programmatically from portfolio_profiles.json's "sentinel"
# entry via PARAM_TO_ENV (mirrors experiments/glide_path/envs.py's
# build_sentinel_env(), re-derived here from the raw JSON so this file never
# depends on importing that -- or any -- experiments/* module). Hardcoded
# below (matches what glide_path.envs.SENTINEL_ENV produces today, empirically
# confirmed 2026-07-29); the selftest independently recomputes it from source
# + JSON and asserts equality, so drift in either the mapping or the registry
# is caught rather than silently baked in.
# ---------------------------------------------------------------------------
SENTINEL_ENV = {
    'TIER_ULTRA_OV': '0.2', 'TIER_TOP_OV': '0.15', 'TIER_MID_OV': '0.0', 'TIER_LOW_OV': '0.0',
    'TIER_OVERFLOW_OV': '0.0',
    'PUT_TIER_TOP_OV': '0.0', 'PUT_TIER_MID_OV': '0.0', 'PUT_TIER_LOW_OV': '0.0',
    'MAX_POSITIONS_OVERRIDE': '14', 'MAX_POSITIONS_CALL': '14', 'MAX_POSITIONS_PUT': '0',
    'GROSS_PREMIUM_CAP': '0.3', 'CALL_PREMIUM_CAP': '0.3', 'PUT_PREMIUM_CAP': '0.0',
    'OPP_SAT_CALL_REF': '16.0', 'OPP_SAT_PUT_REF': '4.0',
    'OPP_SAT_POWER': '0.5', 'OPP_SAT_FLOOR': '0.55',
    'PRACTICAL_EXPOSURE_ENABLED': '1', 'PRACTICAL_CAPITAL_CEILING': '1000000.0',
    'DD_SOFT_BAND_LO': '0.35', 'DD_SOFT_BAND_HI': '0.55', 'DD_SOFT_CALL_FLOOR': '0.4',
}

# DESIGN.md section 3b: Sentinel differs from Core on EXACTLY these five
# fields (selectivity x2, exposure x2, ceiling x1). Re-declared from
# experiments/glide_path/envs.py's EXPECTED_SENTINEL_DIFF.
EXPECTED_SENTINEL_DIFF = {
    'TIER_MID_OV', 'TIER_LOW_OV', 'GROSS_PREMIUM_CAP', 'CALL_PREMIUM_CAP',
    'PRACTICAL_CAPITAL_CEILING',
}

# PARAM_TO_ENV -- re-declared verbatim from experiments/glide_path/envs.py.
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

# The 4 arms run_ecert.py / run_pessimism_n1000.py / run_deep_screen_n1000.py
# select from via --arms / --profiles.
ARMS = {
    'core': CORE_ENV,
    'apex_live': APEX_LIVE_ENV,
    'apex_n10': APEX_N10_ENV,
    'sentinel': SENTINEL_ENV,
}


def _fmt(v) -> str:
    """Replicates experiments/glide_path/envs.py's `_fmt`: bool -> '1'/'0',
    else str(v)."""
    if isinstance(v, bool):
        return '1' if v else '0'
    return str(v)


def _load_sentinel_params_from_json(path: Path | None = None) -> dict:
    path = path or PORTFOLIO_PROFILES_JSON
    with open(path, 'r', encoding='utf-8') as f:
        registry = json.load(f)
    row = next((r for r in registry.get('profiles', []) if r.get('key') == 'sentinel'), None)
    if row is None:
        raise ValueError(f"no profile with key='sentinel' in {path}")
    return row['params']


def build_sentinel_env_from_json(param_to_env: dict, path: Path | None = None) -> dict:
    """Recompute the sentinel env straight from portfolio_profiles.json,
    independent of the hardcoded SENTINEL_ENV above (used by the selftest)."""
    params = _load_sentinel_params_from_json(path)
    unmapped = set(params) - set(param_to_env)
    if unmapped:
        raise AssertionError(f"sentinel params with no env mapping: {sorted(unmapped)}")
    return {param_to_env[k]: _fmt(v) for k, v in params.items()}


# ---------------------------------------------------------------------------
# Selftest: verify every recipe above against its canonical source(s).
# ---------------------------------------------------------------------------

def run_recipe_selftest() -> bool:
    """Print PASS/FAIL per check; return True iff every check passed. Called
    by each runner's own --selftest (recipes.py has no CLI of its own)."""
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    # --- CORE_ENV vs its three canonical sibling copies ---
    try:
        src = _SRC_DEEP_CRASH_SCREEN.read_text(encoding='utf-8')
        canonical_core = extract_dict_literal(src, 'CORE_ENV')
        check('CORE_ENV == deep_crash_screen/run_screen.py:CORE_ENV', canonical_core == CORE_ENV,
              f"diff keys: {_dict_diff(canonical_core, CORE_ENV)}")
    except Exception as e:
        check('CORE_ENV == deep_crash_screen/run_screen.py:CORE_ENV', False, str(e))

    try:
        src2 = _SRC_PESSIMISM_CERT.read_text(encoding='utf-8')
        core2 = extract_dict_literal(src2, 'CORE_ENV')
        check('CORE_ENV == pessimism_cert/run_cert.py:CORE_ENV', core2 == CORE_ENV,
              f"diff keys: {_dict_diff(core2, CORE_ENV)}")
    except Exception as e:
        check('CORE_ENV == pessimism_cert/run_cert.py:CORE_ENV', False, str(e))

    try:
        src3 = _SRC_LIFECYCLE_ENVS.read_text(encoding='utf-8')
        core3 = extract_dict_literal(src3, 'CORE_ENV')
        check('CORE_ENV == lifecycle_mc/envs.py:CORE_ENV', core3 == CORE_ENV,
              f"diff keys: {_dict_diff(core3, CORE_ENV)}")
    except Exception as e:
        check('CORE_ENV == lifecycle_mc/envs.py:CORE_ENV', False, str(e))

    # --- APEX_LIVE_ENV vs run_h3_envelope.py's FROZEN_ENV, and vs the
    # archived p03 evidence summary's baseline recipe ---
    try:
        src4 = _SRC_H3_ENVELOPE.read_text(encoding='utf-8')
        frozen = extract_dict_literal(src4, 'FROZEN_ENV')
        check('APEX_LIVE_ENV == run_h3_envelope.py:FROZEN_ENV', frozen == APEX_LIVE_ENV,
              f"diff keys: {_dict_diff(frozen, APEX_LIVE_ENV)}")
    except Exception as e:
        check('APEX_LIVE_ENV == run_h3_envelope.py:FROZEN_ENV', False, str(e))

    try:
        if _SRC_P03_EVIDENCE_SUMMARY.exists():
            with open(_SRC_P03_EVIDENCE_SUMMARY, 'r', encoding='utf-8') as f:
                archived = json.load(f)
            archived_baseline = archived['recipes']['baseline_live_apex_15dte']
            check('APEX_LIVE_ENV == results_p03_evidence/summary.json recipes.baseline_live_apex_15dte',
                  archived_baseline == APEX_LIVE_ENV, f"diff keys: {_dict_diff(archived_baseline, APEX_LIVE_ENV)}")
            archived_n10 = archived['recipes']['staged_30dte_n10']
            check('APEX_N10_ENV == results_p03_evidence/summary.json recipes.staged_30dte_n10',
                  archived_n10 == APEX_N10_ENV, f"diff keys: {_dict_diff(archived_n10, APEX_N10_ENV)}")
        else:
            print("  [WARN] results_p03_evidence/summary.json not found -- skipping that cross-check "
                  "(not fatal; the ast-extracted source checks above already cover this recipe).")
    except Exception as e:
        check('APEX_LIVE_ENV / APEX_N10_ENV vs archived p03 summary.json', False, str(e))

    # --- APEX_N10_ENV vs lifecycle_mc/envs.py's OPTION_A_DIFF/OPTION_B_EXTRA_DIFF ---
    try:
        src5 = _SRC_LIFECYCLE_ENVS.read_text(encoding='utf-8')
        opt_a = extract_dict_literal(src5, 'OPTION_A_DIFF')
        opt_b = extract_dict_literal(src5, 'OPTION_B_EXTRA_DIFF')
        check('OPTION_A_DIFF == lifecycle_mc/envs.py:OPTION_A_DIFF', opt_a == OPTION_A_DIFF)
        check('OPTION_B_EXTRA_DIFF == lifecycle_mc/envs.py:OPTION_B_EXTRA_DIFF', opt_b == OPTION_B_EXTRA_DIFF)
        recomputed_n10 = dict(APEX_LIVE_ENV)
        recomputed_n10.update(opt_a)
        recomputed_n10.update(opt_b)
        check('APEX_N10_ENV recomputed from FROZEN_ENV+OPTION_A_DIFF+OPTION_B_EXTRA_DIFF matches hardcoded copy',
              recomputed_n10 == APEX_N10_ENV, f"diff keys: {_dict_diff(recomputed_n10, APEX_N10_ENV)}")
    except Exception as e:
        check('APEX_N10_ENV diff-reconstruction vs lifecycle_mc/envs.py', False, str(e))

    # --- SENTINEL_ENV: PARAM_TO_ENV equality + JSON-recomputation ---
    try:
        src6 = _SRC_GLIDE_PATH_ENVS.read_text(encoding='utf-8')
        canonical_param_to_env = extract_dict_literal(src6, 'PARAM_TO_ENV')
        check('PARAM_TO_ENV == glide_path/envs.py:PARAM_TO_ENV', canonical_param_to_env == PARAM_TO_ENV,
              f"diff keys: {_dict_diff(canonical_param_to_env, PARAM_TO_ENV)}")
        canonical_expected_diff = extract_dict_literal(src6, 'EXPECTED_SENTINEL_DIFF')
        check('EXPECTED_SENTINEL_DIFF == glide_path/envs.py:EXPECTED_SENTINEL_DIFF',
              canonical_expected_diff == EXPECTED_SENTINEL_DIFF)
    except Exception as e:
        check('PARAM_TO_ENV / EXPECTED_SENTINEL_DIFF vs glide_path/envs.py', False, str(e))

    try:
        recomputed_sentinel = build_sentinel_env_from_json(PARAM_TO_ENV)
        check('SENTINEL_ENV recomputed from portfolio_profiles.json matches hardcoded copy',
              recomputed_sentinel == SENTINEL_ENV, f"diff keys: {_dict_diff(recomputed_sentinel, SENTINEL_ENV)}")
        actual_diff = {k for k in CORE_ENV if float(CORE_ENV[k]) != float(SENTINEL_ENV[k])}
        check('SENTINEL_ENV differs from CORE_ENV on exactly the 5 expected fields',
              actual_diff == EXPECTED_SENTINEL_DIFF, f"got diff={sorted(actual_diff)}")
    except Exception as e:
        check('SENTINEL_ENV recompute from portfolio_profiles.json', False, str(e))

    return ok


def _dict_diff(a: dict, b: dict) -> dict:
    keys = set(a) | set(b)
    return {k: (a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)}


if __name__ == '__main__':
    import sys as _sys
    if '--selftest' in _sys.argv:
        _sys.exit(0 if run_recipe_selftest() else 1)
    print(json.dumps({'core': CORE_ENV, 'apex_live': APEX_LIVE_ENV,
                       'apex_n10': APEX_N10_ENV, 'sentinel': SENTINEL_ENV}, indent=2))
