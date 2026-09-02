"""Phase 7b — N=500 validation of S1_mild_T25_F80 vs baseline.

S1 (F3F_PUT_STRESS_THRESH=25, FLOOR=0.80, LOW=10) showed ALL 8 windows improving
on DD at N=300. Statistical P(all 8 improve by chance) = 0.4%.

This run noise-reduces the result at N=500 × 8 windows.

Ship gate (portfolio change P1-P6):
- P3: 5y compound >= baseline AND 22-now compound >= baseline
- P4: No annual window regresses >25% on compound
- P5: 0% collapse on every cell
- P6: Per-window DD improvement vs baseline (primary signal)
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
    ('V0_baseline',     '0',    '0.80', '10'),  # no stress cut
    ('V1_S1_mild',     '25',   '0.80', '10'),  # S1 winner
]
WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER  = 500


def run_variant(label, thresh, floor, low):
    print(f"\n{'='*70}\nRunning {label}: thresh={thresh} floor={floor}\n{'='*70}", flush=True)
    env = os.environ.copy()
    env['PYTHONIOENCODING']       = 'utf-8'
    env['N_ITER_OVERRIDE']        = str(N_ITER)
    env['WINDOWS_OVERRIDE']       = ','.join(WINDOWS)
    env['MC_NO_MP']               = '0'
    env['F3F_PUT_STRESS_THRESH']  = thresh
    env['F3F_PUT_STRESS_FLOOR']   = floor
    env['F3F_PUT_STRESS_LOW']     = low

    log_file = OUT_DIR / f'phase7b_{label}.log'
    t0 = time.time()
    with open(log_file, 'w', encoding='utf-8') as logf:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, cwd=str(ROOT),
            stdout=logf, stderr=subprocess.STDOUT,
            timeout=7200,
        )
    elapsed = time.time() - t0
    print(f"  finished in {elapsed:.1f}s, rc={proc.returncode}")
    return log_file.read_text(encoding='utf-8', errors='replace')


def parse_window_results(text):
    import re
    results = {}
    for w in WINDOWS:
        pat = rf'^{re.escape(w)}\s+[\+\-\d\.,e]+%\s+[\+\-\d\.,e]+%\s+([\d\.]+)%'
        m = re.search(pat, text, re.MULTILINE)
        if m:
            results[w] = float(m.group(1))
        pat2 = rf'^{re.escape(w)}\s+([\+\-\d\.,e]+)%'
        m2 = re.search(pat2, text, re.MULTILINE)
        if m2:
            results[w + '_ret'] = m2.group(1)
        pat3 = rf'seeded\s+([\d\.]+)%\s+([\d\.]+)%'
        m3 = re.search(pat3, text)
        if m3:
            results['ctp'] = float(m3.group(1))
            results['ptp'] = float(m3.group(2))
    return results


def main():
    all_results = {}
    for label, thresh, floor, low in VARIANTS:
        text = run_variant(label, thresh, floor, low)
        parsed = parse_window_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len([k for k in parsed if not any(s in k for s in ['_ret','ctp','ptp'])])} windows")

    with open(OUT_DIR / 'phase7b_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # Summary
    print("\n" + "=" * 120)
    print(f"Phase 7b — N={N_ITER} validation: S1_mild (T25,F80) vs baseline")
    print("=" * 120)
    baseline = all_results.get('V0_baseline', {})
    base_max = max((baseline.get(w, 0) for w in WINDOWS), default=0)

    print(f"{'Variant':<22} {'thr':>4} {'flr':>4}", end='')
    for w in WINDOWS:
        print(f" {w:>8}", end='')
    print(f"  {'MaxDD':>6}  {'vs_base':>8}")
    print("-" * 120)

    for label, thresh, floor, low in VARIANTS:
        r = all_results.get(label, {})
        vals = [r.get(w) for w in WINDOWS]
        max_dd = max((v for v in vals if v is not None), default=0)
        delta = max_dd - base_max

        print(f"{label:<22} {thresh:>4} {floor:>4}", end='')
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

    print("\nCompound returns (noise-dominated at N=500, use directionally):")
    print(f"{'Variant':<22}", end='')
    for w in ['2022', '22-now', '5y']:
        print(f" {w:>16}", end='')
    print()
    for label, thresh, floor, low in VARIANTS:
        r = all_results.get(label, {})
        print(f"{label:<22}", end='')
        for w in ['2022', '22-now', '5y']:
            print(f" {r.get(w+'_ret','?'):>16}", end='')
        print()


if __name__ == '__main__':
    main()
