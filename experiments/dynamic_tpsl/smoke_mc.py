"""Smoke MC: STRESS_MODE='breadth' (baseline) vs 'count' (proposed).

Quick directional read at N=80 × 22-now (only). If count-based mode shows DD
or compound improvement, escalate to Phase B Bayesian threshold sweep.
"""
from __future__ import annotations
import os, sys, subprocess, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / 'experiments' / 'dynamic_tpsl' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


def run_one(label: str, env_overrides: dict[str, str]) -> Path:
    """Spawn monte_carlo.py with N=80, 22-now only, given env overrides."""
    log_path = LOG_DIR / f'smoke_{label}.log'
    env = os.environ.copy()
    env.update({
        'PYTHONIOENCODING': 'utf-8',
        'MC_NO_MP': '0',
        'N_ITER_OVERRIDE': '80',
        'WINDOWS_OVERRIDE': '22-now',
        'PYTHONHASHSEED': '0',
        **env_overrides,
    })
    print(f"[{label}] launching with overrides: {env_overrides}")
    t0 = time.perf_counter()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            ['python', '-u', 'monte_carlo.py'],
            cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT, text=True,
        )
    elapsed = time.perf_counter() - t0
    print(f"[{label}] rc={proc.returncode} elapsed={elapsed:.0f}s log={log_path.name}")
    return log_path


def grep_summary(log_path: Path) -> dict:
    """Pull headline metrics from the log tail."""
    text = log_path.read_text(encoding='utf-8', errors='ignore')
    out = {'log': log_path.name, 'tail': []}
    # Last 80 lines for headline tables
    for line in text.splitlines()[-120:]:
        s = line.strip()
        if not s: continue
        if 'Real ' in s or 'Cons ' in s or 'WorstDD' in s or 'CallTP' in s or '22-now' in s or 'collapse' in s.lower():
            out['tail'].append(s)
    return out


def main():
    runs = [
        ('baseline_breadth', {'STRESS_MODE': 'breadth'}),
        ('count_thr1',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '1'}),
        ('count_thr3',       {'STRESS_MODE': 'count', 'STRESS_COUNT_THRESHOLD_CALL': '3'}),
    ]
    for label, env in runs:
        run_one(label, env)
    print("\n" + "="*72)
    print("SMOKE SUMMARY")
    print("="*72)
    for label, _ in runs:
        log = LOG_DIR / f'smoke_{label}.log'
        if not log.exists():
            print(f"\n[{label}] log missing")
            continue
        s = grep_summary(log)
        print(f"\n[{label}] tail ({len(s['tail'])} lines):")
        for line in s['tail'][-30:]:
            print(f"  {line}")


if __name__ == '__main__':
    main()
