"""Phase 7 — Stress-side F3f put allocation cut.

Tests scaling put allocation DOWN in extreme bear tape (brd <= THRESH),
mirroring the existing call F3f but in the stress direction.

Motivation:
- Phase 1B: 2.1% of days have >=50 concurrent puts → correlated-SL DD spikes
- Current F3f put: 1.0 if brd<=75, else linear to 0.50 at brd=95 (bull-side only)
- Stress zone (brd<=25) is completely unaddressed at full 1.0 scale
- This fires only on extreme bear days, neutral on 97.9% of days

Shape (THRESH=30, FLOOR=0.70, LOW=10):
  brd >= 30:  scale = 1.0 (98% of days — no change)
  brd = 20:   scale = 0.85 (15% cut on stressed days)
  brd = 10:   scale = 0.70 (30% cut at extreme, floor)
  brd <= 10:  scale = 0.70 (floor)

Implementation: F3F_PUT_STRESS_THRESH / F3F_PUT_STRESS_FLOOR / F3F_PUT_STRESS_LOW
env vars in monte_carlo.py (0 = disabled, production default).

Gate: N=300 × 8 windows (portfolio change, no recalculate needed).
Primary: max DD reduction. Secondary: compound preservation (±25% gate).
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

# Variants: (label, thresh, floor, low)
# thresh=0 → disabled (baseline)
VARIANTS = [
    # label                   thresh  floor  low
    ('S0_baseline',           0.0,    0.70,  10.0),   # no stress cut (production)
    ('S1_mild_T25_F80',      25.0,    0.80,  10.0),   # mild: 20% cut at brd<=10
    ('S2_mod_T30_F70',       30.0,    0.70,  10.0),   # moderate: 30% cut at brd<=10
    ('S3_strong_T30_F60',    30.0,    0.60,  10.0),   # strong: 40% cut at brd<=10
    ('S4_wide_T40_F70',      40.0,    0.70,  20.0),   # wider trigger, moderate floor
    ('S5_narrow_T20_F70',    20.0,    0.70,   5.0),   # narrow trigger, strong floor
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER  = 300


def run_variant(label: str, thresh: float, floor: float, low: float) -> str:
    print(f"\n{'='*70}\nRunning {label}: thresh={thresh} floor={floor} low={low}\n{'='*70}", flush=True)

    env = os.environ.copy()
    env['PYTHONIOENCODING']       = 'utf-8'
    env['N_ITER_OVERRIDE']        = str(N_ITER)
    env['WINDOWS_OVERRIDE']       = ','.join(WINDOWS)
    env['MC_NO_MP']               = '0'
    env['F3F_PUT_STRESS_THRESH']  = str(thresh)
    env['F3F_PUT_STRESS_FLOOR']   = str(floor)
    env['F3F_PUT_STRESS_LOW']     = str(low)

    log_file = OUT_DIR / f'phase7_{label}.log'
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
        # compound return
        pat2 = rf'^{re.escape(w)}\s+([\+\-\d\.,e]+)%'
        m2 = re.search(pat2, text, re.MULTILINE)
        if m2:
            results[w + '_ret'] = m2.group(1)
        # put trades
        pat3 = rf'^{re.escape(w)}\s+[\+\-\d\.,e]+%\s+[\+\-\d\.,e]+%\s+[\d\.]+%\s+[\d\.]+%\s+[\d\.]+%.*\n[^\n]*seeded\s+[\d\.]+%\s+[\d\.]+%\s+([\d,]+)\s+([\d,]+)'
        m3 = re.search(pat3, text, re.MULTILINE)
        if m3:
            results[w + '_ctrd'] = m3.group(1)
            results[w + '_ptrd'] = m3.group(2)
    return results


def main():
    all_results = {}
    for label, thresh, floor, low in VARIANTS:
        text = run_variant(label, thresh, floor, low)
        parsed = parse_window_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len([k for k in parsed if '_ret' not in k and '_trd' not in k])} windows")

    with open(OUT_DIR / 'phase7_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # Summary table
    print("\n" + "=" * 120)
    print(f"Phase 7 — Stress-side F3f put cut DD sweep (N={N_ITER} × 8 windows)")
    print("=" * 120)
    print(f"{'Variant':<28} {'thr':>4} {'flr':>4}", end='')
    for w in WINDOWS:
        print(f" {w:>8}", end='')
    print(f"  {'MaxDD':>6}  {'vs_base':>8}")
    print("-" * 120)

    baseline_dds = all_results.get('S0_baseline', {})
    base_vals = [baseline_dds.get(w) for w in WINDOWS]
    base_max = max(v for v in base_vals if v is not None)

    for label, thresh, floor, low in VARIANTS:
        r = all_results.get(label, {})
        vals = [r.get(w) for w in WINDOWS]
        max_dd = max((v for v in vals if v is not None), default=None)
        delta = (max_dd - base_max) if max_dd is not None else None

        print(f"{label:<28} {thresh:>4.0f} {floor:>4.2f}", end='')
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

    print("\nLegend: + worse >0.5pp, - better, space = neutral")


if __name__ == '__main__':
    main()
