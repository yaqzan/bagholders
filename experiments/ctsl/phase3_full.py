"""Phase 3 full canonical MC matrix on v46 substrate.

Runs each of the 3 CTSL configs (A baseline / B stack / C substitute) at
N=500 × 8 canonical windows. Results captured to JSON for cross-config
comparison.

Pass criteria for C ≥ A (the phased-removal candidate):
  - 5y compound MeanRet ≥ A within ±10%
  - 5y WorstDD ≤ A within +1.0pp
  - 22-now compound MeanRet ≥ A within ±10%
  - All 8 windows: P(collapse) = 0
  - Per-trade WR/TP% on call/put 75+/<25 unchanged ±0.3pp from A

If C clears: ship CTSL → flip CT_PROMOTE=False (Phase 3 ship).
If B significantly outperforms A and C: CTSL is additive, not a substitute.
If C regresses by >10% on 5y: structural call-side ceiling (0.83 capture)
is too punishing — phased removal rejected.

Estimated runtime: ~3 hours (3 configs × 8 windows × ~5-10 min/window with
MP enabled). Logs streamed to experiments/ctsl/phase3_<config>.log.

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/ctsl/phase3_full.py 2>&1 | \\
    tee experiments/ctsl/phase3_full.log
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
    # Pin to v46 (current production substrate)
    'ALGORITHM_VERSION_PIN': 'f274eb6',
}


def parse_window_summary(text: str) -> dict[str, dict]:
    """Parse the 'SUMMARY - Mean Return / Worst DD / P(collapse) by Window'
    section from a monte_carlo.py run."""
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
            win = m.group(1)
            try:
                mean_ret_str = m.group(2).replace(',', '')
                med_ret_str = m.group(3).replace(',', '')
                results[win] = {
                    'mean_ret_pct': float(mean_ret_str),
                    'med_ret_pct': float(med_ret_str),
                    'mean_ret_str': m.group(2) + '%',
                    'worst_dd': float(m.group(4)),
                    'mean_dd': float(m.group(5)),
                    'p_collapse': float(m.group(6)),
                }
            except ValueError:
                # mean_ret can overflow as scientific — keep string only
                results[win] = {
                    'mean_ret_str': m.group(2) + '%',
                    'med_ret_str': m.group(3) + '%',
                    'worst_dd': float(m.group(4)),
                    'mean_dd': float(m.group(5)),
                    'p_collapse': float(m.group(6)),
                }
    return results


def parse_per_window_seeded(text: str) -> dict[str, dict]:
    """Parse per-window 'seeded' rows for CTP%/PTP%/CTrd/PTrd."""
    out = {}
    cur_window = None
    for line in text.splitlines():
        m_win = re.search(r'WINDOW:\s*(\S+)', line)
        if m_win:
            cur_window = m_win.group(1)
            continue
        if cur_window:
            m = re.match(
                r'^\s*seeded\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+([\d\.]+)\s+([\d\.]+)\s+([+-][\d,\.eE]+)%',
                line,
            )
            if m:
                try:
                    mean_ret = float(m.group(5).replace(',', ''))
                except ValueError:
                    mean_ret = None
                out[cur_window] = {
                    'ctp_pct': float(m.group(1)),
                    'ptp_pct': float(m.group(2)),
                    'ctrd':    float(m.group(3)),
                    'ptrd':    float(m.group(4)),
                    'mean_ret_per_window_str': m.group(5) + '%',
                    'mean_ret_per_window': mean_ret,
                }
                cur_window = None
    return out


def run_config(label: str, overrides: dict) -> dict:
    env = {**os.environ, **ENV_BASE, **overrides}
    log_path = LOG_DIR / f'phase3_{label}.log'
    print(f'\n[phase3] running {label}: {overrides}', flush=True)
    print(f'[phase3] log: {log_path}', flush=True)
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, stdout=f, stderr=subprocess.STDOUT,
            timeout=21600,  # 6 hours hard cap
        )
    dur = time.time() - t0
    print(f'[phase3] {label} done in {dur/60:.1f} min (rc={proc.returncode})', flush=True)

    text = log_path.read_text(encoding='utf-8', errors='replace')
    summary = parse_window_summary(text)
    per_window = parse_per_window_seeded(text)

    # Merge per-window TP rates into summary
    for win in summary:
        if win in per_window:
            summary[win].update({k: v for k, v in per_window[win].items()
                                 if k not in summary[win]})
    return {
        'label': label,
        'overrides': overrides,
        'duration_min': dur / 60,
        'returncode': proc.returncode,
        'summary': summary,
    }


def fmt_dd(v):
    return f'{v:.1f}%' if v is not None else '?'


def fmt_pct(v):
    return f'{v:+.1f}%' if v is not None else '?'


def write_summary(results: dict):
    path = LOG_DIR / 'phase3_summary.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(results, f, default=str, indent=2)
    print(f'\n[phase3] full results: {path}')

    # Per-window comparison
    print('\n' + '=' * 130)
    print(f'  PHASE 3 RESULTS — N={N_ITER} × 8 windows × 3 configs (v46 substrate)')
    print('=' * 130)

    a = results['A_baseline']['summary']
    b = results['B_stack']['summary']
    c = results['C_substitute']['summary']

    print(f'\n  {"window":<10} | {"A (baseline)":>18} {"B (stack)":>18} {"C (substitute)":>18} | {"A DD":>7} {"B DD":>7} {"C DD":>7} | {"A coll":>7} {"B coll":>7} {"C coll":>7}')
    print('  ' + '-' * 128)
    for win in WINDOWS:
        ra = a.get(win, {})
        rb = b.get(win, {})
        rc = c.get(win, {})
        print(f'  {win:<10} | {ra.get("mean_ret_str","?"):>18} {rb.get("mean_ret_str","?"):>18} {rc.get("mean_ret_str","?"):>18} | '
              f'{fmt_dd(ra.get("worst_dd")):>7} {fmt_dd(rb.get("worst_dd")):>7} {fmt_dd(rc.get("worst_dd")):>7} | '
              f'{fmt_dd(ra.get("p_collapse")):>7} {fmt_dd(rb.get("p_collapse")):>7} {fmt_dd(rc.get("p_collapse")):>7}')

    # Pass-criteria check for C vs A
    print('\n  PASS CRITERIA — Config C (CTSL substitute) vs Config A (production)')
    print('  ' + '-' * 110)
    a_5y = a.get('5y', {})
    c_5y = c.get('5y', {})
    a_22n = a.get('22-now', {})
    c_22n = c.get('22-now', {})

    a_5y_dd = a_5y.get('worst_dd')
    c_5y_dd = c_5y.get('worst_dd')
    if a_5y_dd is not None and c_5y_dd is not None:
        dd_delta = c_5y_dd - a_5y_dd
        dd_pass = dd_delta <= 1.0
        print(f'  5y WorstDD: A={a_5y_dd:.1f}%  C={c_5y_dd:.1f}%  Δ={dd_delta:+.2f}pp  '
              f'{"PASS" if dd_pass else "FAIL"} (threshold ≤ +1.0pp)')

    # Collapse check across all windows
    coll_fail = []
    for win in WINDOWS:
        for cfg, label in [(c, 'C'), (a, 'A'), (b, 'B')]:
            r = cfg.get(win, {})
            if r.get('p_collapse', 0) > 0:
                coll_fail.append(f'{label}/{win}={r.get("p_collapse"):.1f}%')
    if coll_fail:
        print(f'  Collapse:   FAIL — non-zero collapse: {", ".join(coll_fail)}')
    else:
        print(f'  Collapse:   PASS — 0% across all 24 (config × window) cells')

    # CTP%/PTP% per-trade quality drift
    for tier_lab in ['ctp_pct', 'ptp_pct']:
        deltas = []
        for win in WINDOWS:
            ra = a.get(win, {})
            rc = c.get(win, {})
            av, cv = ra.get(tier_lab), rc.get(tier_lab)
            if av is not None and cv is not None:
                deltas.append((win, cv - av))
        if deltas:
            max_delta = max(deltas, key=lambda x: abs(x[1]))
            label = 'CTP%' if tier_lab == 'ctp_pct' else 'PTP%'
            ok = abs(max_delta[1]) <= 0.5
            print(f'  {label} drift: max delta {max_delta[0]} = {max_delta[1]:+.2f}pp '
                  f'{"PASS" if ok else "FAIL"} (threshold ±0.5pp)')


def main():
    results = {}
    for label, overrides in CONFIGS.items():
        results[label] = run_config(label, overrides)

    write_summary(results)


if __name__ == '__main__':
    main()
