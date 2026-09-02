"""Phase 8 — F3f Call Floor/Trigger Sweep.

Current 30DTE production: F3F_CALL_FLOOR=0.50, F3F_CALL_LOW=20, F3F_CALL_THRESH=50.
On 2022-08-30 (brd=13.6), calls already scale at 0.50 (floor). The lever is:
  (a) Lower the floor further [0.40, 0.30] for more cut on extreme days
  (b) Raise F3F_CALL_LOW [25, 30] to trigger the floor on the Sep 2022 cluster
      (breadth 23-28) that currently gets partial scale (0.56-0.62)

2022 breadth distribution for sweep targeting:
  brd <= 20: 2.3% of days  (6 days)   — current floor already fires here
  brd <= 25: 6.6% of days  (17 days)  — Sep 22 (23.6), Sep 26 (24.2) cluster
  brd <= 30: 13.7% of days (35 days)  — Sep 13 (28.1) cluster too

This is CALL-ONLY: puts stay at full scale in stress (they're the hedge, confirmed
by Phases 1-7b). Asymmetric cut: calls down in stress, puts unchanged.

Gate: N=500 × 8 windows (higher N to avoid Phase 7 seed-luck trap).
Primary: DD improvement on 2022, 22-now, 5y. Secondary: compound within ±25%.
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

# Variants: (label, F3F_CALL_FLOOR, F3F_CALL_LOW)
# Current production: floor=0.50, low=20
VARIANTS = [
    ('C0_baseline',         0.50, 20),   # production (no change)
    ('C1_floor40_low20',    0.40, 20),   # lower floor: 0.50→0.40 at same trigger
    ('C2_floor30_low20',    0.30, 20),   # aggressive floor: 0.30 at brd<=20
    ('C3_floor50_low25',    0.50, 25),   # wider trigger: floor at brd<=25
    ('C4_floor50_low30',    0.50, 30),   # widest trigger: floor at brd<=30
    ('C5_floor40_low25',    0.40, 25),   # lower + wider: catches Sep 22/26 cluster
    ('C6_floor40_low30',    0.40, 30),   # lower + widest: catches full Sep cluster
    ('C7_floor30_low25',    0.30, 25),   # aggressive + wider
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER  = 500   # N=500 to avoid Phase 7 N=300 seed-luck trap


def run_variant(label: str, floor: float, low: float) -> str:
    print(f"\n{'='*70}\nRunning {label}: floor={floor} low={low}\n{'='*70}", flush=True)
    env = os.environ.copy()
    env['PYTHONIOENCODING']  = 'utf-8'
    env['N_ITER_OVERRIDE']   = str(N_ITER)
    env['WINDOWS_OVERRIDE']  = ','.join(WINDOWS)
    env['MC_NO_MP']          = '0'
    env['F3F_CALL_FLOOR']    = str(floor)
    env['F3F_CALL_LOW']      = str(low)

    log_file = OUT_DIR / f'phase8_{label}.log'
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


def parse_results(text: str) -> dict:
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
        # put trade count (to check puts aren't being displaced)
        pat3 = rf'^{re.escape(w)}.*\n.*seeded\s+[\d\.]+%\s+[\d\.]+%\s+([\d,]+)\s+([\d,]+)'
        m3 = re.search(pat3, text, re.MULTILINE)
        if m3:
            results[w + '_ctrd'] = m3.group(1).replace(',', '')
            results[w + '_ptrd'] = m3.group(2).replace(',', '')
    return results


def main():
    all_results = {}
    for label, floor, low in VARIANTS:
        text = run_variant(label, floor, low)
        parsed = parse_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len([k for k in parsed if '_ret' not in k and '_trd' not in k])} windows")

    with open(OUT_DIR / 'phase8_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # --- DD summary table ---
    print("\n" + "=" * 130)
    print(f"Phase 8 — F3f Call Floor/Trigger Sweep (N={N_ITER} × 8 windows)  [30DTE, call-side only]")
    print("=" * 130)
    print(f"{'Variant':<26} {'flr':>4} {'low':>4}", end='')
    for w in WINDOWS:
        print(f" {w:>8}", end='')
    print(f"  {'MaxDD':>6}  {'vs_base':>8}")
    print("-" * 130)

    baseline = all_results.get('C0_baseline', {})
    base_vals = [baseline.get(w) for w in WINDOWS]
    base_max = max(v for v in base_vals if v is not None)

    for label, floor, low in VARIANTS:
        r = all_results.get(label, {})
        vals = [r.get(w) for w in WINDOWS]
        max_dd = max((v for v in vals if v is not None), default=0)
        delta = max_dd - base_max

        print(f"{label:<26} {floor:>4.2f} {low:>4.0f}", end='')
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

    # --- Per-period analysis on key windows ---
    print("\n--- Key windows (where mechanism fires) ---")
    print(f"{'Variant':<26} {'flr':>4} {'low':>4}   {'2022 DD':>8} {'22-now DD':>10} {'5y DD':>6}   {'2022 ret':>12} {'22-now ret':>20}")
    print("-" * 110)
    for label, floor, low in VARIANTS:
        r = all_results.get(label, {})
        base = all_results.get('C0_baseline', {})
        v22 = r.get('2022')
        v2n = r.get('22-now')
        v5y = r.get('5y')
        b22 = base.get('2022', 0)
        b2n = base.get('22-now', 0)
        d22 = (f"{v22:.1f}({(v22-b22):+.1f})" if v22 else "?")
        d2n = (f"{v2n:.1f}({(v2n-b2n):+.1f})" if v2n else "?")
        d5y = (f"{v5y:.1f}" if v5y else "?")
        r22 = r.get('2022_ret', '?')[:12]
        r2n = r.get('22-now_ret', '?')[:20]
        print(f"{label:<26} {floor:>4.2f} {low:>4.0f}   {d22:>8}  {d2n:>10} {d5y:>6}   {r22:>12} {r2n:>20}")

    print("\nLegend: + worse >0.5pp, - better. Focus on 2022/22-now/5y DD on key windows.")
    print("Ship gate: max DD <= baseline across all 8w AND no window compound regression >25%.")


if __name__ == '__main__':
    main()
