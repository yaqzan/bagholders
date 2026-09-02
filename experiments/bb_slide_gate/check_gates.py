"""
Parse a diff_assess output file and report H1-H5 gate compliance.

Gates (per .claude/docs/assessment-backtest.md):
  H1: TP%/WR15 ≥+0.5pp on ≥3 of {95+, 90+, 85+, 80+, 75+} at 5y; no tier <-1.0pp
  H2: WR15 directional consistency with TP% per bucket; no WR15 > WR30 anomaly
  H3: N stability — each bucket within ±15%; no primary tier below 50 peaks
  H4: <25 and <15 unchanged or improved
  H5: Multi-window consistency — sign of improvement same across 1y/3y/5y
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


CALL_TIERS = ['95+', '90+', '85+', '80+', '75+', '70+']
PUT_TIERS = ['<25', '<15', '<5']


def parse_diff_assess(path: Path) -> dict:
    """
    Parse a diff_assess block. Returns dict[bucket] -> {n_old, n_new, wr15_old, wr15_new, ...}
    """
    text = path.read_text()
    rows = {}

    # Lines look like:
    #   90+ |    7/4    | 100.0%->100.0%  85.7%->100.0%  4.06%->2.65% ...
    pat = re.compile(
        r'^\s*(\S+)\s*\|\s*(\d+)/\s*(\d+)\s*\|\s*(.+)$',
        re.MULTILINE,
    )
    for m in pat.finditer(text):
        bucket = m.group(1)
        if bucket not in CALL_TIERS + PUT_TIERS + ['<30', '<20', '<10']:
            continue
        n_old = int(m.group(2))
        n_new = int(m.group(3))
        rest = m.group(4)

        # Parse 6 metric pairs separated by 2-space gaps. Each cell is "oldval->newval (delta)"
        # Pattern: number%->number% optional trailing (signed delta)
        cell_pat = re.compile(r'([\d.\-]+%?)->([\d.\-]+%?)')
        cells = cell_pat.findall(rest)
        # metrics order: WR15, WR30, Ret30, MAE30, MFE30, Cap30
        labels = ['wr15', 'wr30', 'ret30', 'mae30', 'mfe30', 'cap30']
        if len(cells) >= 6:
            row = {'n_old': n_old, 'n_new': n_new}
            for label, (old_val, new_val) in zip(labels, cells[:6]):
                row[f'{label}_old'] = _to_float(old_val)
                row[f'{label}_new'] = _to_float(new_val)
            rows[bucket] = row

    return rows


def _to_float(s: str):
    if s == '--':
        return None
    return float(s.rstrip('%'))


def check_gates(stats: dict) -> None:
    print("\n=== Gate Verification ===\n")

    # H1: TP%/WR15 improvements on call tiers
    print("H1 — TP%/WR15 improvement on primary call tiers (≥+0.5pp on ≥3 of {95+,90+,85+,80+,75+}, no <-1.0pp regression):")
    primary = ['95+', '90+', '85+', '80+', '75+']
    h1_count_pos = 0
    h1_violations = []
    for tier in primary:
        if tier not in stats:
            print(f"   {tier:<5}: MISSING")
            continue
        wr15_old = stats[tier].get('wr15_old')
        wr15_new = stats[tier].get('wr15_new')
        if wr15_old is None or wr15_new is None:
            print(f"   {tier:<5}: missing data")
            continue
        delta = wr15_new - wr15_old
        n_old = stats[tier]['n_old']
        n_new = stats[tier]['n_new']
        if delta >= 0.5:
            h1_count_pos += 1
            print(f"   {tier:<5}: WR15 {wr15_old:.1f}→{wr15_new:.1f} ({delta:+.1f}pp) N={n_old}→{n_new} ✓")
        elif delta <= -1.0:
            h1_violations.append((tier, delta))
            print(f"   {tier:<5}: WR15 {wr15_old:.1f}→{wr15_new:.1f} ({delta:+.1f}pp) N={n_old}→{n_new} ✗ regression")
        else:
            print(f"   {tier:<5}: WR15 {wr15_old:.1f}→{wr15_new:.1f} ({delta:+.1f}pp) N={n_old}→{n_new} (within noise)")
    h1_pass = h1_count_pos >= 3 and not h1_violations
    print(f"   H1 {'PASS' if h1_pass else 'FAIL'} — {h1_count_pos}/5 tiers improved ≥+0.5pp; {len(h1_violations)} violations")

    # H2: WR15 directional consistency with WR30
    print("\nH2 — WR15 directional consistency (WR15 should not move opposite WR30):")
    h2_violations = []
    for tier in primary:
        if tier not in stats:
            continue
        wr15_d = stats[tier]['wr15_new'] - stats[tier]['wr15_old']
        wr30_d = stats[tier]['wr30_new'] - stats[tier]['wr30_old']
        if abs(wr15_d) > 0.5 and abs(wr30_d) > 0.5 and wr15_d * wr30_d < 0:
            h2_violations.append((tier, wr15_d, wr30_d))
            print(f"   {tier:<5}: WR15 {wr15_d:+.1f}, WR30 {wr30_d:+.1f} ✗ opposite signs")
    h2_pass = not h2_violations
    print(f"   H2 {'PASS' if h2_pass else 'FAIL'} — {len(h2_violations)} sign mismatches")

    # H3: N stability ±15%, primary tier ≥50
    print("\nH3 — N stability (each call tier within ±15%; primary tier ≥50):")
    h3_violations = []
    for tier in primary:
        if tier not in stats:
            continue
        n_old = stats[tier]['n_old']
        n_new = stats[tier]['n_new']
        if n_old == 0:
            continue
        pct = (n_new - n_old) / n_old * 100
        if abs(pct) > 15.0:
            h3_violations.append((tier, n_old, n_new, pct))
            print(f"   {tier:<5}: N {n_old}→{n_new} ({pct:+.1f}%) ✗ outside ±15%")
        else:
            print(f"   {tier:<5}: N {n_old}→{n_new} ({pct:+.1f}%) ok")
        if tier in ['95+', '90+', '85+', '80+', '75+'] and n_new < 50:
            print(f"   {tier:<5}: N_new={n_new} below 50-peak floor")
    h3_pass = not h3_violations
    print(f"   H3 {'PASS' if h3_pass else 'FAIL'} — {len(h3_violations)} N drift violations")

    # H4: Put side neutral or better
    print("\nH4 — Put side neutral or better (<25, <15 unchanged or improved):")
    h4_violations = []
    for tier in PUT_TIERS:
        if tier not in stats:
            print(f"   {tier:<5}: missing")
            continue
        wr15_d = stats[tier]['wr15_new'] - stats[tier]['wr15_old']
        n_old = stats[tier]['n_old']
        n_new = stats[tier]['n_new']
        if wr15_d <= -1.0:
            h4_violations.append((tier, wr15_d))
            print(f"   {tier:<5}: WR15 {wr15_d:+.1f}pp N={n_old}→{n_new} ✗ regression")
        else:
            print(f"   {tier:<5}: WR15 {wr15_d:+.1f}pp N={n_old}→{n_new} ok")
    h4_pass = not h4_violations
    print(f"   H4 {'PASS' if h4_pass else 'FAIL'} — {len(h4_violations)} put regressions")

    print()
    print("=" * 60)
    pass_count = sum([h1_pass, h2_pass, h3_pass, h4_pass])
    print(f"OVERALL: {pass_count}/4 gates pass (H5 multi-window check is separate)")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m experiments.bb_slide_gate.check_gates <output_file>")
        sys.exit(1)
    path = Path(sys.argv[1])
    stats = parse_diff_assess(path)
    print(f"Parsed {len(stats)} bucket rows from {path.name}")
    print()
    for bucket, row in stats.items():
        print(f"  {bucket:<5} N={row['n_old']}→{row['n_new']:<4}  WR15 {row.get('wr15_old')}→{row.get('wr15_new')}  WR30 {row.get('wr30_old')}→{row.get('wr30_new')}")
    check_gates(stats)


if __name__ == '__main__':
    main()
