"""Stage 3 Tertiary Portfolio MC — re-run 3-config matrix with Stage 1 CTSL winner.

Distinct from the prior Phase 3 (which used Phase 2.5 alpha_capture-primary
winner). Stage 1 winner is structurally different: tm=15 (vs 18-20),
target=98.4 (vs 100), alpha=0.56 (vs 1.02), focuses on quality-aligned
selectivity rather than alpha-capture matching.

Per assessment-backtest.md "Stage 3: Tertiary Portfolio Gate":
  Primary metric: 5y WorstDD (T4)
  Hard gates: T1-T7

Configs:
  A (production):     CTSL_ENABLED=0  CT_PROMOTE=1
  B (stack):          CTSL_ENABLED=1  CT_PROMOTE=1
  C (substitute):     CTSL_ENABLED=1  CT_PROMOTE=0

CTSL params come from monte_carlo.py module defaults (now = Stage 1 winner).
"""
from __future__ import annotations
import os, sys, json, subprocess, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / 'experiments' / 'ctsl'
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIGS = {
    'A_baseline':   {'CTSL_ENABLED': '0', 'CT_PROMOTE': '1'},
    'B_stack':      {'CTSL_ENABLED': '1', 'CT_PROMOTE': '1'},
    'C_substitute': {'CTSL_ENABLED': '1', 'CT_PROMOTE': '0'},
}

N_ITER = 500
WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

ENV_BASE = {
    'PYTHONIOENCODING': 'utf-8',
    'N_ITER_OVERRIDE': str(N_ITER),
    'WINDOWS_OVERRIDE': ','.join(WINDOWS),
    'ALGORITHM_VERSION_PIN': 'f274eb6',  # v46
}


def parse_summary(text: str) -> dict[str, dict]:
    results = {}
    in_summary = False
    for line in text.splitlines():
        if 'SUMMARY - Mean Return' in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        m = re.match(
            r'^\s*(\S+)\s+([+-][\d,\.]+)%\s+([+-][\d,\.]+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%',
            line,
        )
        if m:
            try:
                mean_ret = float(m.group(2).replace(',', ''))
            except ValueError:
                mean_ret = None
            results[m.group(1)] = {
                'mean_ret_pct': mean_ret,
                'mean_ret_str': m.group(2) + '%',
                'med_ret_str': m.group(3) + '%',
                'worst_dd': float(m.group(4)),
                'mean_dd': float(m.group(5)),
                'p_collapse': float(m.group(6)),
            }
    return results


def parse_seeded(text: str) -> dict[str, dict]:
    out = {}
    cur = None
    for line in text.splitlines():
        m_win = re.search(r'WINDOW:\s*(\S+)', line)
        if m_win:
            cur = m_win.group(1)
            continue
        if cur:
            m = re.match(
                r'^\s*seeded\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+([\d\.]+)\s+([\d\.]+)',
                line,
            )
            if m:
                out[cur] = {
                    'ctp_pct': float(m.group(1)),
                    'ptp_pct': float(m.group(2)),
                    'ctrd':    float(m.group(3)),
                    'ptrd':    float(m.group(4)),
                }
                cur = None
    return out


def run_config(label: str, overrides: dict) -> dict:
    env = {**os.environ, **ENV_BASE, **overrides}
    log_path = LOG_DIR / f'stage3_{label}.log'
    print(f'\n[stage3] running {label}: {overrides}', flush=True)
    print(f'[stage3] log: {log_path}', flush=True)
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, stdout=f, stderr=subprocess.STDOUT,
            timeout=21600,
        )
    dur = time.time() - t0
    print(f'[stage3] {label} done in {dur/60:.1f} min (rc={proc.returncode})', flush=True)

    text = log_path.read_text(encoding='utf-8', errors='replace')
    summary = parse_summary(text)
    seeded = parse_seeded(text)
    for win in summary:
        if win in seeded:
            summary[win].update(seeded[win])
    return {
        'label': label,
        'overrides': overrides,
        'duration_min': dur / 60,
        'returncode': proc.returncode,
        'summary': summary,
    }


def fmt(v, suffix='', fmt='.1f'):
    if v is None:
        return '?'
    return f'{v:{fmt}}{suffix}'


def write_summary(results: dict):
    path = LOG_DIR / 'stage3_summary.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(results, f, default=str, indent=2)
    print(f'\n[stage3] full results: {path}')

    a = results['A_baseline']['summary']
    b = results['B_stack']['summary']
    c = results['C_substitute']['summary']

    print('\n' + '=' * 130)
    print(f'  STAGE 3 RESULTS — N={N_ITER} × 8 windows × 3 configs (Stage 1 CTSL winner + current barriers)')
    print('=' * 130)
    print(f'\n  {"window":<10} | {"A baseline":>16} {"B stack":>16} {"C substitute":>16} | {"A DD":>6} {"B DD":>6} {"C DD":>6} | {"coll A":>7} {"coll B":>7} {"coll C":>7}')
    print('  ' + '-' * 128)
    for win in WINDOWS:
        ra, rb, rc = a.get(win, {}), b.get(win, {}), c.get(win, {})
        print(f'  {win:<10} | {ra.get("mean_ret_str", "?"):>16} {rb.get("mean_ret_str", "?"):>16} {rc.get("mean_ret_str", "?"):>16} | '
              f'{fmt(ra.get("worst_dd"), "%"):>6} {fmt(rb.get("worst_dd"), "%"):>6} {fmt(rc.get("worst_dd"), "%"):>6} | '
              f'{fmt(ra.get("p_collapse"), "%"):>7} {fmt(rb.get("p_collapse"), "%"):>7} {fmt(rc.get("p_collapse"), "%"):>7}')

    # T4 verdict
    print('\n  T4 — 5y WorstDD primary (≤ baseline +1.0pp):')
    a_dd = a.get('5y', {}).get('worst_dd')
    for cfg_label, summary_ in [('B_stack', b), ('C_substitute', c)]:
        cfg_dd = summary_.get('5y', {}).get('worst_dd')
        if a_dd and cfg_dd:
            delta = cfg_dd - a_dd
            verdict = 'PASS' if delta <= 1.0 else 'FAIL'
            print(f'  {cfg_label:<14}  A={a_dd:.1f}%  cfg={cfg_dd:.1f}%  Δ={delta:+.2f}pp  {verdict}')

    # T5 per-window stability
    print('\n  T5 — per-window DD stability (no annual >5pp regression vs baseline):')
    for cfg_label, summary_ in [('B_stack', b), ('C_substitute', c)]:
        breaches = []
        for win in WINDOWS:
            a_w = a.get(win, {}).get('worst_dd')
            cfg_w = summary_.get(win, {}).get('worst_dd')
            if a_w and cfg_w:
                d = cfg_w - a_w
                if d > 5.0:
                    breaches.append((win, d))
        verdict = 'PASS' if not breaches else f'FAIL ({len(breaches)} breaches)'
        breach_str = ', '.join(f'{w}={d:+.1f}pp' for w, d in breaches) or 'none'
        print(f'  {cfg_label:<14}  {verdict}  breaches: {breach_str}')

    # T6 collapse check
    print('\n  T6 — collapse rate (must be 0% on every cell):')
    for cfg_label, summary_ in [('A_baseline', a), ('B_stack', b), ('C_substitute', c)]:
        bad = [w for w in WINDOWS if summary_.get(w, {}).get('p_collapse', 0) > 0]
        verdict = 'PASS' if not bad else 'FAIL'
        print(f'  {cfg_label:<14}  {verdict}  ' + (f'(non-zero: {bad})' if bad else ''))


def main():
    print('\n' + '#' * 78)
    print(' STAGE 3 — Tertiary Portfolio MC with Stage 1 CTSL winner')
    print(' Locked stack: Stage 1 CTSL winner + current shipped barriers')
    print('#' * 78)
    results = {}
    for label, overrides in CONFIGS.items():
        results[label] = run_config(label, overrides)
    write_summary(results)


if __name__ == '__main__':
    main()
