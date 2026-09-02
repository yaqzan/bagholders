"""Phase B: confirm smoke signal at N=300 x 22-now across multiple thresholds.

Smoke at N=80 showed count_thr3 with WorstDD -3.4pp vs baseline at neutral
per-trade quality. N=80 single window is at the edge of MC noise. Re-run at
N=300 to bring DD signal into reliable territory (per known-issues.md "DD
reliable at N>=300, +/-3pp inter-seed").

Configs: baseline (breadth) + count_thr in {1,2,3,4,5}.
Time est: ~3-4 min per run x 6 configs = ~25 min.
"""
from __future__ import annotations
import os, sys, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / 'experiments' / 'dynamic_tpsl' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


def run_one(label: str, env_overrides: dict[str, str], n_iter='300', windows='22-now') -> Path:
    log_path = LOG_DIR / f'phase_b_{label}.log'
    env = os.environ.copy()
    env.update({
        'PYTHONIOENCODING': 'utf-8',
        'N_ITER_OVERRIDE': n_iter,
        'WINDOWS_OVERRIDE': windows,
        'PYTHONHASHSEED': '0',
        **env_overrides,
    })
    print(f"[{label}] launching N={n_iter} {windows} {env_overrides}", flush=True)
    t0 = time.perf_counter()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            ['python', '-u', 'monte_carlo.py'],
            cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT, text=True,
        )
    elapsed = time.perf_counter() - t0
    print(f"[{label}] rc={proc.returncode} elapsed={elapsed:.0f}s", flush=True)
    return log_path


def parse_log(log_path: Path) -> dict:
    """Extract headline metrics from log."""
    text = log_path.read_text(encoding='utf-8', errors='ignore')
    out = {'log': log_path.name}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('seeded'):
            # CTP%, PTP%, CTrd, PTrd, MeanRet, MedRet, WorstDD, MeanDD, P(col)
            parts = s.split()
            try:
                out['ctp'] = float(parts[1].rstrip('%'))
                out['ptp'] = float(parts[2].rstrip('%'))
                out['ctrd'] = float(parts[3])
                out['ptrd'] = float(parts[4])
                out['meanret'] = parts[5]
                out['medret'] = parts[6]
                out['worstdd'] = float(parts[7].rstrip('%'))
                out['meandd'] = float(parts[8].rstrip('%'))
                out['pcol'] = float(parts[9].rstrip('%'))
            except (IndexError, ValueError):
                pass
        if 'Precomputing call outcomes' in s and 'N=' in s:
            # extract overall TP/SL/Both/Hard
            for tok in s.split():
                if '=' in tok:
                    k, v = tok.split('=', 1)
                    out[f'call_{k.lower()}'] = v.rstrip('%')
    return out


def main():
    configs = [
        ('baseline_breadth', {'STRESS_MODE': 'breadth'}),
        ('count_thr1',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '1'}),
        ('count_thr2',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '2'}),
        ('count_thr3',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '3'}),
        ('count_thr4',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '4'}),
        ('count_thr5',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '5'}),
    ]
    for label, env in configs:
        run_one(label, env)
    print("\n" + "="*100)
    print("PHASE B SUMMARY: N=300 x 22-now")
    print("="*100)
    print(f"{'config':<22} {'CTP%':>6} {'PTP%':>6} {'CTrd':>7} {'WorstDD%':>9} {'MeanDD%':>8} {'P(col)':>7}  MeanRet")
    print('-' * 100)
    rows = []
    for label, _ in configs:
        log = LOG_DIR / f'phase_b_{label}.log'
        if not log.exists(): continue
        m = parse_log(log)
        rows.append((label, m))
        if 'worstdd' in m:
            print(f"{label:<22} {m.get('ctp', float('nan')):>6.2f} {m.get('ptp', float('nan')):>6.2f} "
                  f"{m.get('ctrd', 0):>7.0f} {m['worstdd']:>9.2f} {m['meandd']:>8.2f} "
                  f"{m['pcol']:>6.1f}%  {m.get('meanret', '?')}")
        else:
            print(f"{label:<22} (parse failed — see {log.name})")
    # Headline diff vs baseline
    base = next((m for lbl, m in rows if 'baseline' in lbl), None)
    if base and 'worstdd' in base:
        print(f"\nDelta vs baseline_breadth (WorstDD: lower=better):")
        for label, m in rows:
            if 'baseline' in label or 'worstdd' not in m: continue
            dd = m['worstdd'] - base['worstdd']
            ctp = m.get('ctp', 0) - base.get('ctp', 0)
            print(f"  {label:<22}: dWorstDD={dd:+.2f}pp  dCTP={ctp:+.2f}pp")


if __name__ == '__main__':
    main()
