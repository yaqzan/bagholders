"""
Phase OP2 ship action — applies winning candidate to strategy_config.py,
runs drift test, and recalculates.

Usage:
    python experiments/op2/ship_action.py '{"DD":0.55,"PREM":0.50}'

Caveat: this script makes real changes (file edit + DB recompute). Review
the printed git diff before letting it proceed in unattended mode.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def _log(msg):
    print(f"[ship] {msg}", flush=True)


def axis_values_to_strategy_config_edits(axis_values: dict) -> tuple[list, dict, list]:
    """Returns (simple_edits, tier_alloc_overrides, manual_required).
      simple_edits: list of (field_name, new_repr) for single-field replacements
      tier_alloc_overrides: dict of {key: new_value} keys to override in STRATEGY_30DTE.TIER_ALLOC
      manual_required: list of strings describing axes that need manual editing
    """
    edits = []
    tier_alloc = {}
    manual = []
    for axis, val in axis_values.items():
        if axis == 'DD':
            edits.append(('DD_CIRCUIT_BREAKER', f'{float(val)}'))
        elif axis == 'PREM':
            manual.append(f"PREM_STOP_LOSS={-abs(float(val))} (needs new OptionStrategyConfig field)")
        elif axis == 'SL':
            # SHARED_OPTION.SL_BASE / SL_STRESS — single negative floats
            edits.append(('SL_BASE', f'{-abs(float(val))}'))
            edits.append(('SL_STRESS', f'{-abs(float(val) + 0.05)}'))
        elif axis == 'HOLD':
            edits.append(('HOLD_DAYS', f'{int(val)}'))
        elif axis == 'MAXPOS':
            edits.append(('MAX_POSITIONS', f'{int(val)}'))
        elif axis == 'ULTRA':
            ultra = float(val)
            tier_alloc.update({
                'ultra': ultra,
                'top':   max(0.10, ultra - 0.06),
                'mid':   0.15, 'low': 0.15, 'overflow': 0.00,
            })
        elif axis == 'CASCADE':
            mid_low = float(val)
            tier_alloc.update({
                'ultra': 0.18, 'top': 0.12,
                'mid':   mid_low, 'low': mid_low, 'overflow': 0.00,
            })
        elif axis.startswith('RA_'):
            manual.append(f"REALLOC: {axis} (needs new MC port + DteStrategyConfig fields)")
    return edits, tier_alloc, manual


def _replace_tier_alloc_block(src: str, new_alloc: dict) -> tuple[str, bool]:
    """Replace the TIER_ALLOC dict in the STRATEGY_30DTE block. Returns (new_src, ok)."""
    # Find the STRATEGY_30DTE block first to scope the replacement
    m = re.search(r'(STRATEGY_30DTE\s*=\s*DteStrategyConfig\([^)]*?TIER_ALLOC\s*=\s*\{)([^}]*?)(\s*\},)',
                  src, re.DOTALL)
    if not m:
        return src, False
    new_block = (
        f"\n        'ultra':    {new_alloc.get('ultra', 0.18):.4f},   # 95+\n"
        f"        'top':      {new_alloc.get('top',   0.12):.4f},   # 85-94\n"
        f"        'mid':      {new_alloc.get('mid',   0.15):.4f},   # 80-84\n"
        f"        'low':      {new_alloc.get('low',   0.15):.4f},   # 75-79\n"
        f"        'overflow': {new_alloc.get('overflow', 0.00):.4f},   # 70-74 disabled\n    "
    )
    new_src = src[:m.start(2)] + new_block + src[m.end(2):]
    return new_src, True


def main():
    if len(sys.argv) < 2:
        print("Usage: ship_action.py '{\"DD\":0.55,...}'")
        sys.exit(1)
    axis_values = json.loads(sys.argv[1])
    _log(f"Winning candidate: {axis_values}")

    simple_edits, tier_alloc, manual_required = axis_values_to_strategy_config_edits(axis_values)
    _log(f"Required edits — simple: {len(simple_edits)}, TIER_ALLOC: {bool(tier_alloc)}, manual: {len(manual_required)}")
    for field, new in simple_edits:
        _log(f"  {field}: -> {new}")
    if tier_alloc:
        _log(f"  TIER_ALLOC: -> {tier_alloc}")
    for m in manual_required:
        _log(f"  MANUAL: {m}")

    if manual_required:
        _log("!!! Manual edits required — orchestrator pauses for review.")
        sys.exit(2)

    # Apply edits to strategy_config.py
    sc_path = os.path.join(ROOT, 'strategy_config.py')
    with open(sc_path) as fh:
        src = fh.read()

    # Simple field replacements (looks for "FIELD=value" or "FIELD = value" pattern)
    for field, new_val in simple_edits:
        pat = rf'(\b{re.escape(field)}\s*=\s*)(-?\d+\.?\d*)'
        new_src = re.sub(pat, lambda m: m.group(1) + new_val, src, count=1)
        if new_src == src:
            _log(f"WARN: failed to find {field} in strategy_config.py — skipping")
        else:
            src = new_src
            _log(f"  applied {field} = {new_val}")

    # TIER_ALLOC dict replacement
    if tier_alloc:
        src, ok = _replace_tier_alloc_block(src, tier_alloc)
        if not ok:
            _log("WARN: failed to replace TIER_ALLOC block — manual edit needed")
            sys.exit(2)
        _log(f"  applied TIER_ALLOC = {tier_alloc}")

    with open(sc_path, 'w') as fh:
        fh.write(src)

    # Run drift test
    _log("Running drift test...")
    proc = subprocess.run([sys.executable, 'tests/test_strategy_config_drift.py'],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        _log(f"DRIFT TEST FAILED: {proc.stdout}\n{proc.stderr}")
        sys.exit(3)
    _log("Drift test OK")

    # Recalculate (this is the big one — ~20 min)
    _log("Running trader recalculate --force --full ...")
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run([sys.executable, 'trader.py', 'recalculate', '--force', '--full'],
                          cwd=ROOT, env=env, timeout=3600)
    if proc.returncode != 0:
        _log(f"RECALCULATE FAILED (exit={proc.returncode})")
        sys.exit(4)
    _log("Recalculate complete")

    _log("Ship action complete.")


if __name__ == '__main__':
    main()
