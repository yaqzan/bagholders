"""Phase 9 — N=1000 validation of C4_floor50_low30 vs baseline.

C4 (F3F_CALL_FLOOR=0.50, F3F_CALL_LOW=30): raise the existing call F3f floor
trigger from brd<=20 to brd<=30 — catching the sustained Sep 2022 stress cluster
(brd 23-28) that currently only gets partial scale (0.57-0.63 vs floor 0.50).

Phase 8 N=500 result:
  Max DD:   73.3% vs 75.0% baseline → -1.7pp (7/8 windows improve)
  22-now compound: +75% MORE than baseline (lower DD → more 2023-25 compounding)
  2022 compound:   -36% (bear-year call income slightly reduced, noise at N=500)

Ship gate (P1-P6):
  P3: 5y compound >= baseline AND 22-now compound >= baseline
  P4: No annual window compound regression >25% — need N=1000 to assess 2022
  P5: 0% collapse
  P6: DD improvement vs baseline (primary gate)
"""
from __future__ import annotations
import io, sys, os, json, time, subprocess
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / 'experiments' / 'put_dd_gradient'
OUT_DIR.mkdir(parents=True, exist_ok=True)

VARIANTS = [
    ('V0_baseline',   '0.50', '20'),   # current production
    ('V1_C4_low30',   '0.50', '30'),   # C4 winner: floor=0.50, low=30
]
WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER  = 1000


def run_variant(label, floor, low):
    print(f"\n{'='*70}\nRunning {label}: floor={floor} low={low}\n{'='*70}", flush=True)
    env = os.environ.copy()
    env['PYTHONIOENCODING']  = 'utf-8'
    env['N_ITER_OVERRIDE']   = str(N_ITER)
    env['WINDOWS_OVERRIDE']  = ','.join(WINDOWS)
    env['MC_NO_MP']          = '0'
    env['F3F_CALL_FLOOR']    = floor
    env['F3F_CALL_LOW']      = low

    log_file = OUT_DIR / f'phase9_{label}.log'
    t0 = time.time()
    with open(log_file, 'w', encoding='utf-8') as logf:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, cwd=str(ROOT),
            stdout=logf, stderr=subprocess.STDOUT,
            timeout=14400,
        )
    elapsed = time.time() - t0
    print(f"  finished in {elapsed:.1f}s, rc={proc.returncode}")
    return log_file.read_text(encoding='utf-8', errors='replace')


def parse_results(text):
    import re
    results = {}
    for w in WINDOWS:
        pat = rf'^{re.escape(w)}\s+[\+\-\d\.,e]+%\s+[\+\-\d\.,e]+%\s+([\d\.]+)%'
        m = re.search(pat, text, re.MULTILINE)
        if m:
            results[w] = float(m.group(1))
        # Compound (mean return)
        pat2 = rf'^{re.escape(w)}\s+([\+\-\d\.,e]+)%'
        m2 = re.search(pat2, text, re.MULTILINE)
        if m2:
            results[w + '_ret'] = m2.group(1)
        # Collapse rate
        pat3 = rf'^{re.escape(w)}.*\n.*seeded.*P\(col\).*\n.*seeded.*?([\d\.]+)%\s*$'
        m3 = re.search(pat3, text, re.MULTILINE)
        if m3:
            results[w + '_pcol'] = float(m3.group(1))
    return results


def main():
    all_results = {}
    for label, floor, low in VARIANTS:
        text = run_variant(label, floor, low)
        parsed = parse_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len([k for k in parsed if not any(s in k for s in ['_ret','_pcol'])])} windows")

    with open(OUT_DIR / 'phase9_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    baseline = all_results.get('V0_baseline', {})
    base_max = max((baseline.get(w, 0) for w in WINDOWS), default=0)

    print("\n" + "=" * 120)
    print(f"Phase 9 — N={N_ITER} validation: C4_low30 (floor=0.50, low=30) vs baseline")
    print("=" * 120)
    print(f"{'Variant':<22} {'flr':>4} {'low':>4}", end='')
    for w in WINDOWS:
        print(f" {w:>8}", end='')
    print(f"  {'MaxDD':>6}  {'vs_base':>8}")
    print("-" * 120)

    for label, floor, low in VARIANTS:
        r = all_results.get(label, {})
        vals = [r.get(w) for w in WINDOWS]
        max_dd = max((v for v in vals if v is not None), default=0)
        delta = max_dd - base_max

        print(f"{label:<22} {floor:>4} {low:>4}", end='')
        for w, v in zip(WINDOWS, vals):
            if v is None:
                print(f" {'?':>8}", end='')
            else:
                base = baseline.get(w)
                diff = v - base if base is not None else 0
                marker = '+' if diff > 0.5 else ('-' if diff < -0.5 else ' ')
                print(f" {v:>7.1f}{marker}", end='')

        ds = (f"+{delta:.1f}pp" if delta > 0 else f"{delta:.1f}pp")
        print(f"  {max_dd:.1f}%  {ds}")

    # Compound table
    print(f"\nCompound returns (N={N_ITER}):")
    print(f"{'Variant':<22}  {'2022':>15} {'2025':>15} {'22-now':>20} {'5y':>25}")
    for label, floor, low in VARIANTS:
        r = all_results.get(label, {})
        print(f"{label:<22}  "
              f"{r.get('2022_ret', '?'):>15} "
              f"{r.get('2025_ret', '?'):>15} "
              f"{r.get('22-now_ret', '?'):>20} "
              f"{r.get('5y_ret', '?'):>25}")

    # Ship decision helper
    print("\nShip gate assessment:")
    c4 = all_results.get('V1_C4_low30', {})
    base = all_results.get('V0_baseline', {})
    for w in WINDOWS:
        v = c4.get(w); b = base.get(w)
        if v is not None and b is not None:
            d = v - b
            status = 'IMPROVE' if d < -0.5 else ('REGRESS' if d > 0.5 else 'NEUTRAL')
            print(f"  {w:<10}: {v:.1f}% ({d:+.1f}pp) [{status}]")


if __name__ == '__main__':
    main()
