"""Format the side-by-side MC comparison once both runs complete."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_LOG = ROOT / 'experiments' / 'weekly_volume' / 'mc_baseline.log'
WVD_LOG      = ROOT / 'experiments' / 'weekly_volume' / 'mc_wvd.log'


def parse_summary(log_path: Path):
    """Find the SUMMARY block and parse per-window metrics."""
    if not log_path.exists():
        return {}
    text = log_path.read_text(encoding='utf-8', errors='replace')
    out = {}
    # Find lines like "22-now    +12345.6%  ... 78.2%  ..."
    # Format: "Window     MeanRet  MedRet  WorstDD  MeanDD  P(coll)"
    in_summary = False
    for line in text.split('\n'):
        if 'SUMMARY - Mean Return' in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        # Example: "22-now    +1,234,567.8%   +123,456.7%   78.2%    65.5%      0.0%"
        m = re.match(r'^(\S+)\s+([+-][\d,]+(?:\.\d+)?)%\s+([+-][\d,]+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%', line.strip())
        if m:
            out[m.group(1)] = {
                'mean_ret': float(m.group(2).replace(',', '')),
                'med_ret':  float(m.group(3).replace(',', '')),
                'worst_dd': float(m.group(4)),
                'mean_dd':  float(m.group(5)),
                'p_coll':   float(m.group(6)),
            }
    return out


def parse_displacement(log_path: Path):
    """Find the WVD displacement summary."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding='utf-8', errors='replace')
    out = {}
    for line in text.split('\n'):
        m = re.match(r'\s*Calls dampened:\s*(\d[\d,]*)\s+dropped \(overall<70\):\s*(\d[\d,]*)', line)
        if m:
            out['calls_dampened'] = int(m.group(1).replace(',', ''))
            out['calls_dropped']  = int(m.group(2).replace(',', ''))
        m = re.match(r'\s*Puts\s+lifted:\s*(\d[\d,]*)\s+dropped \(overall>25\):\s*(\d[\d,]*)', line)
        if m:
            out['puts_lifted']   = int(m.group(1).replace(',', ''))
            out['puts_dropped']  = int(m.group(2).replace(',', ''))
    return out


def format_pct(v):
    if abs(v) >= 1e9:
        return f'{v:+.2e}%'
    if abs(v) >= 1e6:
        return f'{v/1e6:+.1f}M%'
    if abs(v) >= 1e3:
        return f'{v/1e3:+.1f}k%'
    return f'{v:+.1f}%'


def main():
    base = parse_summary(BASELINE_LOG)
    wvd  = parse_summary(WVD_LOG)

    if not base or not wvd:
        print('[report] One or both runs not yet complete.')
        print(f'  baseline windows: {list(base.keys()) or "none"}')
        print(f'  wvd      windows: {list(wvd.keys()) or "none"}')
        return

    disp = parse_displacement(WVD_LOG) or {}

    print('=' * 110)
    print('  Portfolio MC Comparison: v45 baseline vs WVD overlay  (N=200, 30 DTE)')
    print('=' * 110)

    if disp:
        print(f'\nWVD displacement (across all windows × iters):')
        print(f'  Calls dampened: {disp.get("calls_dampened",0):,}  dropped to <70: {disp.get("calls_dropped",0):,}')
        print(f'  Puts  lifted:   {disp.get("puts_lifted",0):,}    dropped to >25: {disp.get("puts_dropped",0):,}')

    print()
    print(f'  {"Window":<10} {"BASELINE":<46} {"WVD":<46} Δ vs base')
    print(f'  {"":<10} {"MeanRet":>14} {"MedRet":>14} {"WrstDD":>8} {"MeanRet":>14} {"MedRet":>14} {"WrstDD":>8}')
    print('  ' + '-' * 110)

    for win in ['22-now', '5y']:
        if win not in base or win not in wvd:
            continue
        b = base[win]
        w = wvd[win]
        # Δ% on compound: (1+w/100)/(1+b/100) - 1
        if abs(b['mean_ret']) < 1e15:  # don't divide nonsense numbers
            try:
                ratio_mean = (1 + w['mean_ret']/100) / (1 + b['mean_ret']/100) - 1
                ratio_med  = (1 + w['med_ret']/100)  / (1 + b['med_ret']/100)  - 1
            except ZeroDivisionError:
                ratio_mean = float('nan')
                ratio_med  = float('nan')
        else:
            ratio_mean = ratio_med = float('nan')

        print(f'  {win:<10} '
              f'{format_pct(b["mean_ret"]):>14} {format_pct(b["med_ret"]):>14} {b["worst_dd"]:>7.1f}% '
              f'{format_pct(w["mean_ret"]):>14} {format_pct(w["med_ret"]):>14} {w["worst_dd"]:>7.1f}% '
              f'  Mean×{1+ratio_mean:.2f}  DDΔ={w["worst_dd"]-b["worst_dd"]:+.1f}pp')


if __name__ == '__main__':
    main()
