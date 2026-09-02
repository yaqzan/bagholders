"""Phase 1C — MC ablation: drop deepest puts vs baseline.

Tests four variants on dip + 22-now + 5y windows at N=200:
  - V0_baseline: no put filter (production)
  - V1_drop_lt5:  drop overall <= 5
  - V2_drop_lt10: drop overall <= 10
  - V3_drop_lt15: drop overall <= 15

Each runs as a subprocess with PUT_MIN_OVERALL env var applied to monte_carlo.

Validates whether removing deepest puts at the cascade-input layer reduces
correlated DD without significant compound loss — direct test of "are deep
puts catastrophic to DD despite high WR".
"""
from __future__ import annotations
import os, sys, io, json, time, subprocess, tempfile, re
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / 'experiments' / 'put_dd_gradient'
OUT_DIR.mkdir(parents=True, exist_ok=True)

VARIANTS = [
    ('V0_baseline',  ''),
    ('V1_drop_lt5',  '5'),
    ('V2_drop_lt10', '10'),
    ('V3_drop_lt15', '15'),
]

WINDOWS = ['dip', '22-now', '5y']
N_ITER = 200


def parse_mc_output(text):
    """Parse mean_ret, dd_c, collapse from monte_carlo.py output."""
    results = {}
    # Match window header lines like:  | 5y         | +1.85e+27% | 78.1% | 0.0% |
    # Or our specific format: "Window=5y   real_mean=+1.85e+27%  worstdd_c=78.1%"
    for window in WINDOWS:
        # Look for window summary line
        pat = rf"Window\s*=\s*{re.escape(window)}\s+real_mean\s*=\s*([+\-]?\d+\.?\d*(?:e[+\-]?\d+)?%?)\s+worstdd_c\s*=\s*([\d\.]+)%"
        m = re.search(pat, text)
        if m:
            results[window] = {
                'mean_ret_str': m.group(1),
                'dd_c_pct': float(m.group(2)),
            }
    return results


def run_variant(name, put_min_str):
    print(f"\n{'='*60}")
    print(f"Running {name} (PUT_MIN_OVERALL='{put_min_str}')")
    print('='*60, flush=True)
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = ','.join(WINDOWS)
    env['MC_NO_MP'] = '0'  # multiprocessing on
    if put_min_str:
        env['PUT_MIN_OVERALL'] = put_min_str

    log_file = OUT_DIR / f'phase1c_{name}.log'
    t0 = time.time()
    with open(log_file, 'w', encoding='utf-8') as logf:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env,
            cwd=str(ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            timeout=3600,
        )
    elapsed = time.time() - t0
    print(f"  finished in {elapsed:.1f}s, rc={proc.returncode}")
    text = log_file.read_text(encoding='utf-8', errors='replace')
    return text


def main():
    # Patch monte_carlo.py to accept PUT_MIN_OVERALL (transient — we restore after)
    mc_path = ROOT / 'monte_carlo.py'
    src_orig = mc_path.read_text(encoding='utf-8')

    # Insert PUT_MIN_OVERALL env hook right after PUT_THRESHOLD line
    if 'PUT_MIN_OVERALL' not in src_orig:
        anchor = "PUT_THRESHOLD      = int(os.environ.get('PUT_THRESHOLD_OVERRIDE', str(_cfg.PUT_THRESHOLD)))"
        new_lines = (
            anchor + "\n"
            + "PUT_MIN_OVERALL    = int(os.environ.get('PUT_MIN_OVERALL', '-1'))  # ablation: drop puts with overall <= this\n"
        )
        src_patched = src_orig.replace(anchor, new_lines, 1)
        # Add filter inside load_put_signals after the SQL fetch
        # Find: where Score.overall <= PUT_THRESHOLD,
        anchor2 = "Score.overall <= PUT_THRESHOLD,"
        new_lines2 = (
            "Score.overall <= PUT_THRESHOLD,\n"
            "                Score.overall > PUT_MIN_OVERALL,"
        )
        src_patched = src_patched.replace(anchor2, new_lines2, 1)
        mc_path.write_text(src_patched, encoding='utf-8')
        print(f"[patch] applied PUT_MIN_OVERALL filter to monte_carlo.py")
    else:
        print(f"[patch] PUT_MIN_OVERALL already present (skip)")

    try:
        all_results = {}
        for name, pmo in VARIANTS:
            text = run_variant(name, pmo)
            parsed = parse_mc_output(text)
            all_results[name] = parsed
            print(f"  parsed: {parsed}")

        # Save
        with open(OUT_DIR / 'phase1c_results.json', 'w') as f:
            json.dump(all_results, f, indent=2)

        # Summary table
        print("\n" + "=" * 70)
        print("Phase 1C summary — DD and compound by variant")
        print("=" * 70)
        print(f"{'Variant':<16} ", end='')
        for w in WINDOWS:
            print(f"{w+' DD%':>10} {w+' ret':>16} ", end='')
        print()
        print("-" * (18 + 28 * len(WINDOWS)))
        for name, _ in VARIANTS:
            r = all_results.get(name, {})
            print(f"{name:<16} ", end='')
            for w in WINDOWS:
                wr = r.get(w, {})
                dd = wr.get('dd_c_pct', '?')
                ret = wr.get('mean_ret_str', '?')
                dd_s = f"{dd:.1f}" if isinstance(dd, (int, float)) else str(dd)
                print(f"{dd_s:>10} {ret:>16} ", end='')
            print()

    finally:
        # Restore monte_carlo.py
        mc_path.write_text(src_orig, encoding='utf-8')
        print(f"\n[patch] restored monte_carlo.py to original")


if __name__ == '__main__':
    main()
