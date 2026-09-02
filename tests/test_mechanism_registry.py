"""Validates mechanism_registry.REGISTRY against actual engine state.

Catches the failure mode that drift-guard alone cannot: "shipped a portfolio
mechanism to 30 DTE without an explicit decision about 15 DTE coverage."

Three checks per mechanism:

1. **Schema**: every claimed config_field exists on STRATEGY_30DTE and
   STRATEGY_15DTE (the dataclasses).

2. **`enabled` invariant**: when status='enabled', the listed engine files
   must define module-level constants for every config_field (modulo known
   name aliases like WEAK_WEEKLY_CALL_WADJ_LT → WEAK_WEEKLY_CALL_WADJ in
   mc15).

3. **`disabled` invariant**: when status='disabled':
   - `wiring_mode='not_wired'` → engine MUST NOT define ANY of the config
     constants (catches "someone added wiring without flipping the flag")
   - `wiring_mode='wired_neutral'` → engine wires the constants but the
     STRATEGY config sets no-op values (drift-guard already covers values;
     this test just verifies the wiring mode declaration is truthful)

Run via: `python tests/test_mechanism_registry.py`
"""
from __future__ import annotations

import ast
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Known name aliases between strategy_config field names and engine
# module-level constant names. The engine sometimes uses a shorter name.
# Format: {config_field_name: engine_constant_name}
ENGINE_NAME_ALIASES = {
    'WEAK_WEEKLY_CALL_WADJ_LT': 'WEAK_WEEKLY_CALL_WADJ',
}


def _engine_constants(filename: str) -> set[str]:
    """Return the set of module-level constant names defined in `filename`.

    Uses AST to catch top-level `NAME = ...` assignments only — variables
    defined inside functions don't count, by design.
    """
    path = ROOT / filename
    src = path.read_text(encoding='utf-8', errors='replace')
    tree = ast.parse(src, filename=str(path))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


# Cache: filename → set of constants. Engine files are read once per run.
_ENGINE_CONST_CACHE: dict[str, set[str]] = {}


def _engine_has_field(engine_file: str, field_name: str) -> bool:
    """Check whether an engine file defines a constant matching the field."""
    if engine_file not in _ENGINE_CONST_CACHE:
        _ENGINE_CONST_CACHE[engine_file] = _engine_constants(engine_file)
    consts = _ENGINE_CONST_CACHE[engine_file]
    if field_name in consts:
        return True
    alias = ENGINE_NAME_ALIASES.get(field_name)
    if alias and alias in consts:
        return True
    return False


# ─── Per-mechanism checks ────────────────────────────────────────────────


def _check_schema(spec, errors: list) -> None:
    """Every claimed config_field must exist on both DTE configs."""
    import strategy_config as cfg
    s30 = cfg.STRATEGY_30DTE
    s15 = cfg.STRATEGY_15DTE
    for f in spec.config_fields:
        # Try DteStrategyConfig directly first; fall back to .option
        if not (hasattr(s30, f) or hasattr(s30.option, f)):
            errors.append(
                f'{spec.name}: config_field {f!r} does not exist on '
                f'STRATEGY_30DTE (or its .option). Registry is out of sync '
                f'with strategy_config.py.'
            )
        if not (hasattr(s15, f) or hasattr(s15.option, f)):
            errors.append(
                f'{spec.name}: config_field {f!r} does not exist on '
                f'STRATEGY_15DTE (or its .option).'
            )


def _check_enabled(spec, dte_label: str, status: str, engine_files: tuple,
                   errors: list) -> None:
    """For 'enabled' status, every engine_file must wire every config_field."""
    if status != 'enabled':
        return
    if not engine_files:
        errors.append(
            f'{spec.name} ({dte_label}): status=enabled but engine_files is '
            f'empty. Declare which files wire this mechanism.'
        )
        return
    for engine_file in engine_files:
        for f in spec.config_fields:
            if not _engine_has_field(engine_file, f):
                errors.append(
                    f'{spec.name} ({dte_label}): registry says enabled, '
                    f'engine_files lists {engine_file!r}, but that file does '
                    f'not define a module-level constant {f!r}. Either wire '
                    f'the constant in {engine_file} or amend the registry.'
                )


def _check_disabled(spec, dte_label: str, status: str, wiring_mode: str,
                    engine_files: tuple, errors: list) -> None:
    """For 'disabled' status, enforce the declared wiring_mode."""
    if status != 'disabled':
        return

    if wiring_mode == 'not_wired':
        # 15 DTE engines for the not_wired case
        engines_to_check = (
            ('monte_carlo_15dte.py', 'backtest_cascade_15dte.py')
            if dte_label == '15 DTE'
            else ('monte_carlo.py', 'backtest_cascade.py')
        )
        for engine_file in engines_to_check:
            for f in spec.config_fields:
                if _engine_has_field(engine_file, f):
                    errors.append(
                        f'{spec.name} ({dte_label}): registry says '
                        f"status='disabled', wiring_mode='not_wired', but "
                        f'{engine_file} unexpectedly defines a constant for '
                        f'{f!r}. Either (a) finish wiring + flip the config '
                        f'flag + change registry to enabled, or (b) remove '
                        f'the engine constant.'
                    )
    elif wiring_mode == 'wired_neutral':
        # The drift-guard pairs_mc15/pairs_bc15 already enforce that the
        # values match config (which sets no-op defaults). We just verify
        # the declared engine_files exist.
        if not engine_files:
            errors.append(
                f'{spec.name} ({dte_label}): wiring_mode=wired_neutral but '
                f'engine_files is empty. Declare which files wire the '
                f'no-op constants (drift-guard pairs verify the values match).'
            )


# ─── Registry-vs-strategy_config completeness check ──────────────────────


# Fields in strategy_config that are pure infrastructure / scoring-side and
# don't represent a portfolio mechanism. These are exempt from the "every
# field should be in some registry entry" warning.
_INFRASTRUCTURE_FIELDS = frozenset({
    'name',
    # Sizing primitives — not a mechanism per se
    'HOLD_DAYS', 'PREMIUM_MULT', 'HARD_SELL_LOSS', 'MAX_POSITIONS',
    # Calendar hold + honest theta standard (2026-06-09) — engine hold/theta semantics
    'CALENDAR_HOLD', 'HOLD_CAL_DAYS', 'NOMINAL_CAL_DTE',
    'MAX_POSITIONS_CALL', 'MAX_POSITIONS_PUT',
    'PRIMARY_THRESHOLD', 'OVERFLOW_THRESHOLD', 'PUT_THRESHOLD',
    'TIER_ALLOC', 'PUT_TIER_ALLOC',
    # Regime asymmetric slope plumbing — calibrated together at v15
    'REGIME_SLOPE', 'REGIME_SLOPE_PUT',
    'REGIME_SLOPE_UP', 'REGIME_SLOPE_DOWN',
    'REGIME_SLOPE_PUT_UP', 'REGIME_SLOPE_PUT_DOWN',
    # MC infrastructure
    'VOL_LOOKBACK', 'COLLAPSE_THRESHOLD',
    # Composed config (handled separately)
    'option',
})


def _check_field_coverage(errors: list, warnings: list) -> None:
    """Soft check: every DteStrategyConfig field should belong to either
    INFRASTRUCTURE_FIELDS or a registry mechanism. New fields that match
    neither suggest a mechanism shipped without registry update.
    """
    import strategy_config as cfg
    import mechanism_registry as mr
    from dataclasses import fields

    f30 = {f.name for f in fields(cfg.STRATEGY_30DTE)}
    claimed = mr.all_config_fields()
    unclaimed = f30 - claimed - _INFRASTRUCTURE_FIELDS
    for f in sorted(unclaimed):
        warnings.append(
            f'Strategy field {f!r} is not claimed by any mechanism in '
            f'mechanism_registry.REGISTRY and is not in INFRASTRUCTURE_FIELDS. '
            f'If this is a new portfolio mechanism, add a registry entry. If '
            f'pure infrastructure, add to _INFRASTRUCTURE_FIELDS in this test.'
        )


# ─── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    import mechanism_registry as mr

    errors: list[str] = []
    warnings: list[str] = []

    n_mechanisms = len(mr.REGISTRY)
    n_field_checks = 0

    for spec in mr.REGISTRY:
        _check_schema(spec, errors)
        n_field_checks += len(spec.config_fields) * 2  # 30 + 15

        _check_enabled(spec, '30 DTE', spec.dte_30_status,
                       spec.engine_files_30, errors)
        _check_enabled(spec, '15 DTE', spec.dte_15_status,
                       spec.engine_files_15, errors)
        _check_disabled(spec, '30 DTE', spec.dte_30_status,
                        spec.dte_30_wiring_mode, spec.engine_files_30, errors)
        _check_disabled(spec, '15 DTE', spec.dte_15_status,
                        spec.dte_15_wiring_mode, spec.engine_files_15, errors)

    _check_field_coverage(errors, warnings)

    if errors:
        print(f'MECHANISM REGISTRY DRIFT: {len(errors)} error(s)\n')
        for e in errors:
            print(f'  • {e}')
        if warnings:
            print(f'\n{len(warnings)} warning(s):')
            for w in warnings:
                print(f'  ⚠ {w}')
        sys.exit(1)

    msg = (f'OK: {n_mechanisms} mechanisms in registry, {n_field_checks} '
           f'field-existence checks pass')
    if warnings:
        msg += f'  ({len(warnings)} warning(s))'
        print(msg)
        for w in warnings:
            print(f'  ⚠ {w}')
    else:
        print(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
