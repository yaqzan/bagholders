"""Phase 6 — Portfolio-stage daily put cap sweep.

Tests MAX_PUTS_PER_DAY ∈ {10, 15, 20, 25, 30, baseline} at N=300 × 8 windows.

Motivation (Phase 1B finding):
  - Median put fires/day: 11
  - p95: 35/day
  - Max: 124/day (2022-08-30)
  - 2.1% of days have >=50 concurrent puts → correlated SL clusters → DD spikes
  - Top 20 highest put-density days: ALL in 2022 (Aug-Sep) and Feb 2023

A cap of 15-20/day fires only on the extreme tail (>p90), leaving 90% of days
completely unaffected while cutting the correlated-DD mechanism on outlier days.

This is portfolio-stage only:
  - No Score.overall change
  - No recalculate needed
  - No ALGORITHM_VERSION bump
  - Gate: P3/P6 canonical MC (N=300 × 8 windows, DD-primary)
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

# Variants: (label, MAX_PUTS_PER_DAY)
# 0 = disabled (baseline)
VARIANTS = [
    ('P0_baseline',    0),
    ('P1_cap10',      10),
    ('P2_cap15',      15),
    ('P3_cap20',      20),
    ('P4_cap25',      25),
    ('P5_cap30',      30),
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER  = 300


def run_variant(label: str, cap: int) -> str:
    print(f"\n{'='*70}\nRunning {label}: MAX_PUTS_PER_DAY={cap}\n{'='*70}", flush=True)

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['N_ITER_OVERRIDE']  = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = ','.join(WINDOWS)
    env['MC_NO_MP']         = '0'
    if cap > 0:
        env['MAX_PUTS_PER_DAY'] = str(cap)
    else:
        env.pop('MAX_PUTS_PER_DAY', None)

    log_file = OUT_DIR / f'phase6_{label}.log'
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


def parse_window_results(text: str) -> dict:
    import re
    results = {}
    for w in WINDOWS:
        pat = rf'^{re.escape(w)}\s+[\+\-\d\.,e]+%\s+[\+\-\d\.,e]+%\s+([\d\.]+)%'
        m = re.search(pat, text, re.MULTILINE)
        if m:
            results[w] = float(m.group(1))
        # also grab compound
        pat2 = rf'^{re.escape(w)}\s+([\+\-\d\.,e]+)%'
        m2 = re.search(pat2, text, re.MULTILINE)
        if m2:
            results[w + '_ret'] = m2.group(1)
    return results


def main():
    all_results = {}
    for label, cap in VARIANTS:
        text = run_variant(label, cap)
        parsed = parse_window_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len(parsed)//2} windows")

    with open(OUT_DIR / 'phase6_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # Summary table — DD primary
    print("\n" + "=" * 120)
    print(f"Phase 6 — Daily put cap DD sweep (N={N_ITER} × 8 windows)")
    print("=" * 120)
    print(f"{'Variant':<22} {'cap':>5}", end='')
    for w in WINDOWS:
        print(f" {w:>8}", end='')
    print(f"  {'MaxDD':>6}  {'vs_base':>8}")
    print("-" * 120)

    baseline_dds = all_results.get('P0_baseline', {})
    base_vals = [baseline_dds.get(w) for w in WINDOWS]
    base_max = max(v for v in base_vals if v is not None)

    for label, cap in VARIANTS:
        r = all_results.get(label, {})
        vals = [r.get(w) for w in WINDOWS]
        max_dd = max((v for v in vals if v is not None), default=None)
        delta = (max_dd - base_max) if max_dd is not None else None

        cap_str = str(cap) if cap > 0 else 'off'
        print(f"{label:<22} {cap_str:>5}", end='')
        for w, v in zip(WINDOWS, vals):
            if v is None:
                print(f" {'?':>8}", end='')
            else:
                base = baseline_dds.get(w)
                diff = v - base if base is not None else 0
                marker = '+' if diff > 0.5 else ('-' if diff < -0.5 else ' ')
                print(f" {v:>7.1f}{marker}", end='')

        max_str = f"{max_dd:.1f}%" if max_dd is not None else "?"
        delta_str = (f"+{delta:.1f}pp" if delta > 0 else f"{delta:.1f}pp") if delta is not None else "?"
        print(f"  {max_str:>6}  {delta_str:>8}")

    print("\nLegend: + worse than baseline >0.5pp, - better, space = within noise")
    print("Gate: max DD reduction with compound within -25% on any window")


if __name__ == '__main__':
    main()
