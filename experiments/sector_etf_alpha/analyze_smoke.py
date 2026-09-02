"""Parse smoke MC baseline/treatment logs and produce a side-by-side comparison."""
from __future__ import annotations
import sys, re
from pathlib import Path

OUT = Path(__file__).resolve().parent


def parse_summary(log_path: Path):
    """Extract per-window summary lines from MC stdout.

    Format from monte_carlo.py:
      WINDOW    MeanRet         MedRet      WorstDD  MeanDD  P(coll)
      22-now    +1234.5%        +890.2%     72.4%    65.1%   0.0%
    """
    if not log_path.exists():
        return []
    text = log_path.read_text(errors='replace')
    # Find SUMMARY block
    m = re.search(r'SUMMARY[\s\S]+?({[^}]+}|\Z)', text)
    rows = []
    in_summary = False
    for line in text.splitlines():
        if 'SUMMARY' in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        # Match: window mean med dd meanDD p_coll
        m = re.match(r'^\s*(\S+)\s+([+-][0-9,.]+%)\s+([+-][0-9,.]+%)\s+([0-9.]+)%\s+([0-9.]+)%\s+([0-9.]+)%\s*$', line)
        if m:
            rows.append({
                'window': m.group(1),
                'mean_ret': m.group(2),
                'med_ret':  m.group(3),
                'worst_dd': float(m.group(4)),
                'mean_dd':  float(m.group(5)),
                'p_coll':   float(m.group(6)),
            })
    return rows


def parse_per_trade(log_path: Path):
    """Extract per-trade/per-tier metrics from MC log if present."""
    if not log_path.exists():
        return {}
    text = log_path.read_text(errors='replace')
    # Look for "Call signals: NNNN" and "Put signals: NNNN" etc.
    out = {}
    m = re.search(r'Call signals:\s*(\d+)\s*\((75\+=\d+, 70-74=\d+)\).*?Put signals.*?:\s*(\d+)', text)
    if m:
        out['call_total'] = int(m.group(1))
        out['call_breakdown'] = m.group(2)
        out['put_total'] = int(m.group(3))
    # Treatment patch counts
    m = re.search(r'\[patch_call\]\s+orig N=([\d,]+)\s+kept=([\d,]+)\s+modified=(\d+)\s+dropped=(\d+)', text)
    if m:
        out['call_modified'] = int(m.group(3))
        out['call_dropped'] = int(m.group(4))
    m = re.search(r'\[patch_put\]\s+orig N=([\d,]+)\s+kept=([\d,]+)\s+modified=(\d+)\s+dropped=(\d+)', text)
    if m:
        out['put_modified'] = int(m.group(3))
        out['put_dropped'] = int(m.group(4))
    # Per-trade rates if present
    m = re.search(r'Call TP%\s*=\s*([\d.]+)%.*?Put TP%\s*=\s*([\d.]+)%', text)
    if m:
        out['call_tp_pct'] = float(m.group(1))
        out['put_tp_pct'] = float(m.group(2))
    return out


def main():
    # Handle both single-pair (smoke_mc_baseline.log) and multi-window (smoke_mc_<window>_baseline.log)
    pairs = []
    if (OUT / 'smoke_mc_baseline.log').exists():
        pairs.append(('22-now', OUT / 'smoke_mc_baseline.log', OUT / 'smoke_mc_treatment.log'))
    for w in ['5y', '2024', '2022', '2025', '2023', '2021', 'dip']:
        b = OUT / f'smoke_mc_{w}_baseline.log'
        t = OUT / f'smoke_mc_{w}_treatment.log'
        if b.exists() and t.exists():
            pairs.append((w, b, t))

    print('=' * 110)
    print('SMOKE MC COMPARISON — V4 SWPM (treatment) vs v46 production (baseline)  [N=200 per window]')
    print('=' * 110)

    all_results = []
    for win_label, blog, tlog in pairs:
        b_rows = parse_summary(blog)
        t_rows = parse_summary(tlog)
        # Find row matching the window in each log
        b_row = next((r for r in b_rows), None)
        t_row = next((r for r in t_rows), None)
        if b_row is None or t_row is None:
            print(f'\n[{win_label}] missing summary in logs (b={blog.exists()} t={tlog.exists()})')
            continue
        all_results.append({'window': win_label, 'baseline': b_row, 'treatment': t_row,
                            'b_pt': parse_per_trade(blog), 't_pt': parse_per_trade(tlog)})

    if not all_results:
        print('\nNo completed pairs yet')
        return

    print(f'\n{"Window":<8} {"BaseWDD":>9} {"TrtWDD":>9} {"ΔWDD":>8} | {"BaseMDD":>9} {"TrtMDD":>9} {"ΔMDD":>8} | {"P(coll)":>8}>{"P(coll)":>8}')
    print('-' * 110)
    for r in all_results:
        w, b, t = r['window'], r['baseline'], r['treatment']
        print(f'{w:<8} {b["worst_dd"]:>8.1f}%  {t["worst_dd"]:>8.1f}%  '
              f'{t["worst_dd"]-b["worst_dd"]:>+7.1f}pp | '
              f'{b["mean_dd"]:>8.1f}%  {t["mean_dd"]:>8.1f}%  '
              f'{t["mean_dd"]-b["mean_dd"]:>+7.1f}pp | '
              f'{b["p_coll"]:>7.1f}% {t["p_coll"]:>7.1f}%')

    print(f'\n{"Window":<8} {"BaseRet":>30}     {"TreatRet":>30}')
    print('-' * 80)
    for r in all_results:
        w, b, t = r['window'], r['baseline'], r['treatment']
        print(f'{w:<8} {b["mean_ret"]:>30}     {t["mean_ret"]:>30}')

    # Aggregated DD shifts
    print('\n=== Verdict per window ===')
    n_imp_w = n_dgd_w = n_neu_w = 0
    n_imp_m = n_dgd_m = n_neu_m = 0
    for r in all_results:
        ddw = r['treatment']['worst_dd'] - r['baseline']['worst_dd']
        ddm = r['treatment']['mean_dd'] - r['baseline']['mean_dd']
        v_w = 'IMPROVE' if ddw <= -1.0 else ('DEGRADE' if ddw >= +1.0 else 'NEUTRAL')
        v_m = 'IMPROVE' if ddm <= -1.0 else ('DEGRADE' if ddm >= +1.0 else 'NEUTRAL')
        print(f'  {r["window"]:<8}  ΔWorstDD={ddw:+.1f}pp [{v_w}]  ΔMeanDD={ddm:+.1f}pp [{v_m}]')
        if v_w == 'IMPROVE': n_imp_w += 1
        elif v_w == 'DEGRADE': n_dgd_w += 1
        else: n_neu_w += 1
        if v_m == 'IMPROVE': n_imp_m += 1
        elif v_m == 'DEGRADE': n_dgd_m += 1
        else: n_neu_m += 1

    print(f'\nWorstDD shifts: {n_imp_w} IMPROVE / {n_neu_w} NEUTRAL / {n_dgd_w} DEGRADE  out of {len(all_results)}')
    print(f'MeanDD  shifts: {n_imp_m} IMPROVE / {n_neu_m} NEUTRAL / {n_dgd_m} DEGRADE  out of {len(all_results)}')

    if all_results:
        b_pt = all_results[0]['b_pt']
        t_pt = all_results[0]['t_pt']
        print(f'\nFirst window — patch counts:')
        print(f'  Baseline: {b_pt}')
        print(f'  Treatment: {t_pt}')


if __name__ == '__main__':
    main()
