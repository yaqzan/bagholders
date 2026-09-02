"""Parse mc_15dte_n200.out and mc_30dte_n200.out and produce a delta table."""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_mc_out(path):
    """Returns dict {window: {mode: {ret, dd, ctp, ptp, ctrd, ptrd, p_coll}}}."""
    out = {}
    cur_window = None
    with open(path) as f:
        for line in f:
            m = re.match(r'WINDOW:\s+(\S+)\s+\(', line)
            if m:
                cur_window = m.group(1)
                out[cur_window] = {}
                continue
            # Mode lines: "conservative   55.1%  41.5%  963.0  235.4   +3021.9%  +2851.2%  68.8%  60.4%  0.0%"
            m = re.match(r'^(conservative|realistic|optimistic)\s+'
                         r'([+-]?\d+\.\d+)%\s+([+-]?\d+\.\d+)%\s+'
                         r'([\d\.]+)\s+([\d\.]+)\s+'
                         r'([+-]?\d+\.\d+)%\s+([+-]?\d+\.\d+)%\s+'
                         r'([\d\.]+)%\s+([\d\.]+)%\s+([\d\.]+)%', line)
            if m and cur_window:
                mode = m.group(1)
                out[cur_window][mode] = {
                    'ctp':   float(m.group(2)),
                    'ptp':   float(m.group(3)),
                    'ctrd':  float(m.group(4)),
                    'ptrd':  float(m.group(5)),
                    'ret':   float(m.group(6)),
                    'medret':float(m.group(7)),
                    'dd':    float(m.group(8)),
                    'meandd':float(m.group(9)),
                    'p_coll':float(m.group(10)),
                }
    return out


def fmt_pct(x):
    if x is None: return '—'
    if abs(x) >= 1e6:  return f"{x/1e6:.2f}M%"
    if abs(x) >= 1e3:  return f"{x/1e3:.2f}k%"
    return f"{x:+.1f}%"


def fmt_delta(d):
    if d is None: return '—'
    if abs(d) >= 1e6: return f"{d/1e6:+.2f}M"
    if abs(d) >= 1e3: return f"{d/1e3:+.2f}k"
    return f"{d:+.1f}"


def main():
    p15 = parse_mc_out('experiments/mc_15dte_n200.out')
    p30 = parse_mc_out('experiments/mc_30dte_n200.out')
    windows = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

    print('=' * 132)
    print('  15 DTE vs 30 DTE — N=200, v28, all 8 windows')
    print('=' * 132)

    # Realistic table
    print('\n  REALISTIC mode — Mean Compound Return')
    print(f"  {'Window':<10}  {'30DTE Ret':>14}  {'15DTE Ret':>14}  {'Δ% (15/30)':>12}  "
          f"{'30DTE DD-C':>10}  {'15DTE DD-C':>10}  {'ΔDD':>8}  "
          f"{'30 CTP%':>8}  {'15 CTP%':>8}  {'30 PTP%':>8}  {'15 PTP%':>8}")
    print('  ' + '-' * 130)

    for w in windows:
        r30 = p30.get(w, {}).get('realistic')
        r15 = p15.get(w, {}).get('realistic')
        c30 = p30.get(w, {}).get('conservative')
        c15 = p15.get(w, {}).get('conservative')
        if not r30 or not r15:
            print(f"  {w:<10}  (incomplete)")
            continue
        # Compound delta — use (1+r15) / (1+r30) ratio
        ratio_15_30 = ((1.0 + r15['ret']/100) / (1.0 + r30['ret']/100) - 1.0) * 100.0
        dd_delta = c15['dd'] - c30['dd']
        print(f"  {w:<10}  {fmt_pct(r30['ret']):>14}  {fmt_pct(r15['ret']):>14}  "
              f"{ratio_15_30:>+11.1f}%  "
              f"{c30['dd']:>9.1f}%  {c15['dd']:>9.1f}%  {dd_delta:>+7.1f}  "
              f"{r30['ctp']:>7.1f}%  {r15['ctp']:>7.1f}%  "
              f"{r30['ptp']:>7.1f}%  {r15['ptp']:>7.1f}%")

    # Conservative DD-C floor check
    print('\n  CONSERVATIVE mode DD-C — must stay <= 80%')
    print(f"  {'Window':<10}  {'30DTE DD-C':>10}  {'15DTE DD-C':>10}  {'Pass 80% floor?':>16}")
    print('  ' + '-' * 56)
    for w in windows:
        c30 = p30.get(w, {}).get('conservative')
        c15 = p15.get(w, {}).get('conservative')
        if not c30 or not c15:
            print(f"  {w:<10}  (incomplete)")
            continue
        floor_30 = '✓' if c30['dd'] <= 80.0 else '✗ BREACH'
        floor_15 = '✓' if c15['dd'] <= 80.0 else '✗ BREACH'
        print(f"  {w:<10}  {c30['dd']:>9.1f}%{floor_30:>5}  {c15['dd']:>9.1f}%{floor_15:>5}")

    # Collapse rates
    print('\n  P(collapse) — must be 0% in both')
    print(f"  {'Window':<10}  {'30DTE Real':>10}  {'15DTE Real':>10}  {'30DTE Cons':>10}  {'15DTE Cons':>10}")
    print('  ' + '-' * 56)
    for w in windows:
        r30 = p30.get(w, {}).get('realistic')
        r15 = p15.get(w, {}).get('realistic')
        c30 = p30.get(w, {}).get('conservative')
        c15 = p15.get(w, {}).get('conservative')
        if not r30 or not r15:
            print(f"  {w:<10}  (incomplete)")
            continue
        print(f"  {w:<10}  {r30['p_coll']:>9.1f}%  {r15['p_coll']:>9.1f}%  "
              f"{c30['p_coll']:>9.1f}%  {c15['p_coll']:>9.1f}%")

    # Per-trade volume
    print('\n  Trade volume (Realistic) — capital velocity check')
    print(f"  {'Window':<10}  {'30 CTrd':>8}  {'15 CTrd':>8}  {'15/30':>6}  "
          f"{'30 PTrd':>8}  {'15 PTrd':>8}  {'15/30':>6}")
    print('  ' + '-' * 70)
    for w in windows:
        r30 = p30.get(w, {}).get('realistic')
        r15 = p15.get(w, {}).get('realistic')
        if not r30 or not r15:
            print(f"  {w:<10}  (incomplete)")
            continue
        c_ratio = r15['ctrd'] / r30['ctrd'] if r30['ctrd'] else 0
        p_ratio = r15['ptrd'] / r30['ptrd'] if r30['ptrd'] else 0
        print(f"  {w:<10}  {r30['ctrd']:>8.0f}  {r15['ctrd']:>8.0f}  {c_ratio:>5.2f}x  "
              f"{r30['ptrd']:>8.0f}  {r15['ptrd']:>8.0f}  {p_ratio:>5.2f}x")

    print()


if __name__ == '__main__':
    main()
