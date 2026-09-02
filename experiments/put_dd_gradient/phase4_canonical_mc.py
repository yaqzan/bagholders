"""Phase 4 — Canonical MC validation of top depth-dampener candidates.

Runs 5 variants spanning the N-reduction spectrum + baseline at N=200 × 8 windows
under bounded+bimodal+dead-hold MC (the canonical config).

DD-primary gate (per Phase v32 noise finding: compound at N=200/300 has 4-order
swings; DD signal is reliable). Compound reported as median, must stay positive.

Mechanism: lift = K × depth_factor(overall) × (target − overall)
  applied to puts (overall <= 25) only, in load_put_signals via monkey-patch.
  depth_shape ∈ {linear, log}.
"""
from __future__ import annotations
import io, sys, os, json, time, math, subprocess
from pathlib import Path
import multiprocessing as mp

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / 'experiments' / 'put_dd_gradient'
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Candidates revised after Phase 1C V3 finding: hard-dropping deep puts INCREASES DD
# (deep puts are partial hedges). Phase 4 tests the spectrum of "redistribute within
# cascade" (target=22 keeps lifted puts in 21-25 tier) vs "lift out of cascade"
# (target=50 removes them entirely).
CANDIDATES = [
    # (label, K, score_gate, target, depth_shape)
    ('C0_baseline',          0.0,  25, 0,   'linear'),  # K=0 → no lift
    ('C1_redist_G15_T22',    1.0,  15, 22,  'log'),     # redistribute <15 within cascade
    ('C2_redist_G20_T22',    1.0,  20, 22,  'log'),     # redistribute <20 within cascade
    ('C3_partial_G15_T35',   1.0,  15, 35,  'log'),     # Phase3 -8.9% N (partial lift)
    ('C4_partial_G20_T50',   0.5,  20, 50,  'log'),     # Phase3 -17% N (moderate)
    ('C5_aggressive_G25_T50',0.7,  25, 50,  'log'),     # Phase3 -88% N (control: aggressive)
]

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
N_ITER = 200


def run_variant(label, K, score_gate, target, depth_shape):
    """Run monte_carlo.py with score-stage put-lift monkey-patch applied via subprocess."""
    print(f"\n{'='*70}\nRunning {label}: K={K} gate={score_gate} target={target} shape={depth_shape}\n{'='*70}", flush=True)

    # Build a wrapper script that imports monte_carlo, patches load_put_signals, and runs.
    wrapper_path = OUT_DIR / f'_wrapper_{label}.py'
    wrapper_src = f'''
import os, sys, math
sys.path.insert(0, r"{ROOT}")

K = {K}
SCORE_GATE = {score_gate}
TARGET = {target}
DEPTH_SHAPE = "{depth_shape}"

def depth_factor(overall):
    """Returns 0..1 depth multiplier. Larger for deeper (lower) overall."""
    if overall >= SCORE_GATE: return 0.0
    if SCORE_GATE <= 0: return 0.0
    if DEPTH_SHAPE == "linear":
        return max(0.0, min(1.0, (SCORE_GATE - overall) / SCORE_GATE))
    elif DEPTH_SHAPE == "log":
        depth = max(0, SCORE_GATE - overall)
        return max(0.0, min(1.0, math.log(depth + 1.0) / math.log(SCORE_GATE + 1.0)))
    else:
        return 0.0

def apply_lift(overall):
    if K <= 0: return overall
    if overall > 25: return overall  # puts only
    f = depth_factor(overall)
    if f <= 0: return overall
    new_ov = overall + K * f * (TARGET - overall)
    new_ov = int(round(max(0, min(100, new_ov))))
    return new_ov

# Patch load_put_signals BEFORE monte_carlo loads it
import monte_carlo as mc

_orig_load_put = mc.load_put_signals

def _patched_load_put(version, d_start, d_end):
    # Load broader range so lifted-up puts that exit <=25 still re-qualify
    sigs = _orig_load_put(version, d_start, d_end)
    out = []
    for s in sigs:
        new_ov = apply_lift(int(s.overall))
        if new_ov > mc.PUT_THRESHOLD: continue  # lifted out of put cascade
        s.overall = new_ov
        out.append(s)
    out.sort(key=lambda s: (s.date, s.overall))
    return out

mc.load_put_signals = _patched_load_put

# Force baseline by setting K=0 (no patch needed but applied identity)

if __name__ == "__main__":
    mc.main()
'''
    wrapper_path.write_text(wrapper_src, encoding='utf-8')

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['N_ITER_OVERRIDE'] = str(N_ITER)
    env['WINDOWS_OVERRIDE'] = ','.join(WINDOWS)
    env['MC_NO_MP'] = '0'

    log_file = OUT_DIR / f'phase4_{label}.log'
    t0 = time.time()
    with open(log_file, 'w', encoding='utf-8') as logf:
        proc = subprocess.run(
            [sys.executable, '-u', str(wrapper_path)],
            env=env, cwd=str(ROOT),
            stdout=logf, stderr=subprocess.STDOUT,
            timeout=7200,
        )
    elapsed = time.time() - t0
    print(f"  finished in {elapsed:.1f}s, rc={proc.returncode}")
    return log_file.read_text(encoding='utf-8', errors='replace')


def parse_window_results(text):
    """Parse per-window MeanRet/WorstDD/PCol from monte_carlo.py output."""
    import re
    results = {}
    for w in WINDOWS:
        # Match "WINDOW: <label>" then look for the seeded mode summary line
        # Format from monte_carlo.py:
        # 5y         +X.XXe+XX%  +Y.YYe+YY%       73.1%      58.3%       0.0%
        # Each line: window CTP% PTP% CTrd PTrd MeanRet MedRet WorstDD MeanDD P(col)
        # But that's the row; we need to find it after "WINDOW: <w>"
        pat = rf"WINDOW:\s*{re.escape(w)}\b.*?\n.*?seeded.*?\n[^\n]*?\b(?P<mode>seeded)\s+(?P<ctp>[\d\.]+)%?\s+(?P<ptp>[\d\.]+)%?\s+(?P<ctrd>[\d,]+)\s+(?P<ptrd>[\d,]+)\s+(?P<mean>[+\-\d\.,e]+)%\s+(?P<med>[+\-\d\.,e]+)%\s+(?P<wdd>[\d\.]+)%\s+(?P<mdd>[\d\.]+)%\s+(?P<pcol>[\d\.]+)%"
        m = re.search(pat, text, re.DOTALL)
        if m:
            results[w] = {
                'ctp': float(m.group('ctp')),
                'ptp': float(m.group('ptp')),
                'mean_ret_str': m.group('mean'),
                'med_ret_str': m.group('med'),
                'worst_dd': float(m.group('wdd')),
                'mean_dd': float(m.group('mdd')),
                'p_collapse': float(m.group('pcol')),
            }
        else:
            # Fallback: simpler match
            simple = rf"^{re.escape(w):<10}\s+([+\-\d\.,e]+)%\s+([+\-\d\.,e]+)%\s+([\d\.]+)%\s+([\d\.]+)%\s+([\d\.]+)%"
            sm = re.search(simple, text, re.MULTILINE)
            if sm:
                results[w] = {
                    'mean_ret_str': sm.group(1),
                    'med_ret_str': sm.group(2),
                    'worst_dd': float(sm.group(3)),
                    'mean_dd': float(sm.group(4)),
                    'p_collapse': float(sm.group(5)),
                }
    return results


def main():
    all_results = {}
    for entry in CANDIDATES:
        label = entry[0]
        text = run_variant(*entry)
        parsed = parse_window_results(text)
        all_results[label] = parsed
        print(f"  parsed: {len(parsed)} windows")

    with open(OUT_DIR / 'phase4_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)

    # Summary table
    print("\n" + "=" * 130)
    print(f"Phase 4 — Per-window DD and compound by variant (N={N_ITER}, all 8 windows)")
    print("=" * 130)
    print(f"{'Variant':<28} ", end='')
    for w in WINDOWS:
        print(f"{w+'DD%':>9} {w+'cmp':>14} ", end='')
    print()
    print("-" * (30 + 24 * len(WINDOWS)))
    for label, _, _, _, _, _ in CANDIDATES:
        r = all_results.get(label, {})
        print(f"{label:<28} ", end='')
        for w in WINDOWS:
            wr = r.get(w, {})
            dd = wr.get('worst_dd', '?')
            ret = wr.get('mean_ret_str', '?')
            dd_s = f"{dd:.1f}" if isinstance(dd, (int, float)) else str(dd)
            print(f"{dd_s:>9} {ret:>14} ", end='')
        print()


if __name__ == '__main__':
    main()
