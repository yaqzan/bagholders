"""
Put concentration sweep — joint Design D + PUT_THRESHOLD ablation.

Tests the user's hypothesis that "max put score 20" outperforms "max put 25"
together with the per-trade-validated Design D put-only flip.

Variants (6 total):
  B           : production v25 (PUT_THRESHOLD=25, no flip)
  PT20        : production + cap puts at <=20 (drop 21-25 cascade tier)
  PT15        : production + cap puts at <=15 (drop 16-25 tiers, only put_top fires)
  D           : Design D put-flip + PUT_THRESHOLD=25
  D+PT20      : Design D + PT20
  D+PT15      : Design D + PT15

Each variant runs canonical 3-mode MC. By default uses N_ITER=200 and a
focused window set (5y + 22-now + dip). For each variant we record per-window
Realistic mean return, Conservative DD, Conservative collapse rate.

Gates (must pass for ship):
  - 5y Realistic compound > B
  - No per-window Realistic loss > 25% vs B
  - No Conservative DD > 80% on any window
  - 0% collapse on every cell
"""
from __future__ import annotations

import io, sys, os, re, subprocess, json
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = [
    ('B',       {}),
    ('PT20',    {'PUT_THRESHOLD_OVERRIDE': '20'}),
    ('PT15',    {'PUT_THRESHOLD_OVERRIDE': '15'}),
    ('D',       {'DESIGN_D_PUT_FLIP': '1'}),
    ('D+PT20',  {'DESIGN_D_PUT_FLIP': '1', 'PUT_THRESHOLD_OVERRIDE': '20'}),
    ('D+PT15',  {'DESIGN_D_PUT_FLIP': '1', 'PUT_THRESHOLD_OVERRIDE': '15'}),
]

# Default — quick scout
N_ITER = int(os.environ.get('SWEEP_N_ITER', '150'))
WINDOWS = os.environ.get('SWEEP_WINDOWS', '2022,2024,2025,dip,22-now,5y')

# Section headers in monte_carlo.py output we parse:
#   "SUMMARY - Mean Return by Window x Collision Mode"
#   "SUMMARY - Worst Drawdown by Window x Collision Mode"
#   "SUMMARY - P(collapse) by Window x Collision Mode"

NUM_RE = re.compile(r'([+-]?\d[\d,]*\.?\d*)')

def _parse_summary(out: str):
    """Return {window: {'mean': {mode: val}, 'dd': {...}, 'pcoll': {...}}}."""
    sections = {
        'mean':  'SUMMARY - Mean Return',
        'dd':    'SUMMARY - Worst Drawdown',
        'pcoll': 'SUMMARY - P(collapse)',
    }
    lines = out.split('\n')
    res = {}
    for key, header in sections.items():
        i = next((idx for idx, l in enumerate(lines) if header in l), None)
        if i is None:
            continue
        # Header at i, divider at i+1, label row at i+2 (col headers), data starts i+4
        # Actually format is: header line, line of '=', col label line, '-' divider, data rows
        # Look for the row starting with the window label
        for line in lines[i+1:i+30]:
            stripped = line.strip()
            if not stripped or stripped.startswith('=') or stripped.startswith('-'):
                continue
            if stripped.startswith('Window'):
                continue
            # window data row: label  v1  v2  v3 (3 modes)
            parts = stripped.split()
            if len(parts) < 4:
                continue
            label = parts[0]
            # capture last 3 floats
            nums = NUM_RE.findall(line)
            # filter 'numbers' that look like real floats (last 3)
            float_nums = []
            for n in nums:
                try:
                    val = float(n.replace(',', ''))
                    float_nums.append(val)
                except ValueError:
                    pass
            if len(float_nums) < 3:
                continue
            cons, real, opt = float_nums[-3], float_nums[-2], float_nums[-1]
            res.setdefault(label, {}).setdefault(key, {})['cons'] = cons
            res[label][key]['real'] = real
            res[label][key]['opt']  = opt
    return res


def run_variant(label, env_extra):
    env = os.environ.copy()
    env.update(env_extra)
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = WINDOWS
    env['PYTHONIOENCODING'] = 'utf-8'
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> running {label}: env={env_extra} N={N_ITER} windows={WINDOWS}")
    sys.stdout.flush()
    proc = subprocess.run(
        ['python', 'monte_carlo.py'],
        cwd=str(ROOT), env=env,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"  FAILED rc={proc.returncode}")
        print(proc.stderr[-3000:])
        return None
    parsed = _parse_summary(proc.stdout)
    # Persist raw output for forensics
    out_path = ROOT / 'experiments' / f'put_concsweep_{label.replace("+", "p")}.out'
    out_path.write_text(proc.stdout, encoding='utf-8', errors='replace')
    return parsed


def main():
    results = {}
    for label, env in VARIANTS:
        r = run_variant(label, env)
        if r is not None:
            results[label] = r

    # Save to JSON
    out_json = ROOT / 'experiments' / 'put_concentration_sweep_results.json'
    out_json.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults JSON: {out_json}")

    # Summary table
    win_list = WINDOWS.split(',')
    print('\n' + '='*120)
    print(f"PUT CONCENTRATION SWEEP — N={N_ITER}  Windows={WINDOWS}")
    print('='*120)
    print(f"{'Window':<8} | " + ' | '.join(f"{v:>22}" for v, _ in VARIANTS))
    print('-' * 120)
    for w in win_list:
        # Realistic mean
        row = f"{w:<8} | "
        cells = []
        for vlabel, _ in VARIANTS:
            v = results.get(vlabel, {}).get(w, {})
            real = v.get('mean', {}).get('real')
            cells.append(f"{real:>+18,.1f}%   " if real is not None else f"{'—':>22}")
        print(f"{w:<8} R | " + ' | '.join(cells))
        cells = []
        for vlabel, _ in VARIANTS:
            v = results.get(vlabel, {}).get(w, {})
            ddc = v.get('dd', {}).get('cons')
            cells.append(f"{ddc:>20.1f}%   " if ddc is not None else f"{'—':>22}")
        print(f"{w:<8} DDC | " + ' | '.join(cells))
        cells = []
        for vlabel, _ in VARIANTS:
            v = results.get(vlabel, {}).get(w, {})
            pcoll = v.get('pcoll', {}).get('cons')
            cells.append(f"{pcoll:>20.1f}%   " if pcoll is not None else f"{'—':>22}")
        print(f"{w:<8} P% | " + ' | '.join(cells))
        print()

    # Gate evaluation
    print('\n' + '='*120)
    print("GATE EVALUATION (vs B baseline) — Realistic mode, F3f production parameters")
    print('='*120)
    base = results.get('B', {})
    for vlabel, _ in VARIANTS:
        if vlabel == 'B':
            continue
        v = results.get(vlabel, {})
        if not v:
            continue
        any_breach = False
        breaches = []
        for w in win_list:
            b_real = base.get(w, {}).get('mean', {}).get('real')
            v_real = v.get(w, {}).get('mean', {}).get('real')
            v_ddc  = v.get(w, {}).get('dd', {}).get('cons')
            v_pcl  = v.get(w, {}).get('pcoll', {}).get('cons')
            if b_real is not None and v_real is not None and abs(b_real) > 1e-6:
                # Don't compute pct delta on tiny baselines; use abs threshold instead
                pct = (v_real - b_real) / abs(b_real) * 100
                if pct < -25:
                    breaches.append(f"{w} R{pct:+.1f}%")
                    any_breach = True
            if v_ddc is not None and v_ddc > 80:
                breaches.append(f"{w} DDC={v_ddc:.1f}%")
                any_breach = True
            if v_pcl is not None and v_pcl > 0:
                breaches.append(f"{w} P%={v_pcl:.1f}")
                any_breach = True
        verdict = 'FAIL' if any_breach else 'PASS'
        print(f"  {vlabel:<10} {verdict}  {'; '.join(breaches) if breaches else 'all gates clear'}")


if __name__ == '__main__':
    main()
