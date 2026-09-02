"""Parse EVR-1 sweep output and emit a clean comparison table.

Reads a phase_evr1_per_trade.py log file, extracts the diff-assess WR15/WR30/N
for each variant on the call buckets (95+/85+/70+) and put buckets (<25/<15/<5),
and prints a side-by-side comparison vs B (current production).

Usage:
    python -m experiments.evr_optimization.analyze_evr1 path/to/run.out
"""
from __future__ import annotations

import io
import os
import re
import sys

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']
PUT_BUCKETS  = ['<5', '<10', '<15', '<20', '<25', '<30']
ALL_BUCKETS = CALL_BUCKETS + PUT_BUCKETS

# Strip ANSI color codes that color the diff_assess output.
# Some output also contains bare ESC chars (Windows colorama edge case);
# strip those too.
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m|\x1b')

# Bucket row in diff-assess looks like:
#   95+ |   10/13   | 90.0%->92.3%   100.0%->100.0%  6.65%->6.32%   ...
# Format: bucket | db_n/sim_n | db_w15%->sim_w15%   db_w30%->sim_w30%   ...
ROW_RE = re.compile(
    r'^\s*(?P<bucket>(?:[0-9]+\+|<[0-9]+))\s*\|\s*(?P<db_n>[0-9]+)/(?P<sim_n>[0-9]+)\s*\|\s*'
    r'(?P<db_w15>[0-9.]+)%->(?P<sim_w15>[0-9.]+)%\s+'
    r'(?P<db_w30>[0-9.]+)%->(?P<sim_w30>[0-9.]+)%'
)


def parse_log(path: str):
    """Returns dict[variant_label] -> dict[bucket] -> {sim_n, sim_wr15, sim_wr30, ...}."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read()
    raw = ANSI_RE.sub('', raw)
    lines = raw.splitlines()

    results = {}
    current_variant = None

    for line in lines:
        m = re.match(r'\s*VARIANT:\s+(\S+)\s', line)
        if m:
            current_variant = m.group(1)
            results[current_variant] = {}
            continue

        if current_variant is None:
            continue

        m = ROW_RE.match(line)
        if not m:
            continue
        bucket = m.group('bucket')
        if bucket not in ALL_BUCKETS:
            continue
        results[current_variant][bucket] = {
            'db_n':   int(m.group('db_n')),
            'sim_n':  int(m.group('sim_n')),
            'db_w15': float(m.group('db_w15')),
            'sim_w15': float(m.group('sim_w15')),
            'db_w30': float(m.group('db_w30')),
            'sim_w30': float(m.group('sim_w30')),
        }

    return results


def emit_comparison(results, baseline='B'):
    if baseline not in results:
        print(f"[evr1-analyze] baseline {baseline!r} not found; available: {list(results.keys())}")
        return

    base = results[baseline]
    variants = [v for v in results.keys() if v != baseline]

    for bucket_set, label in [(CALL_BUCKETS, 'CALLS'), (PUT_BUCKETS, 'PUTS')]:
        print(f"\n{'=' * 96}")
        print(f"  {label} -- WR15 / WR30 / N    delta vs {baseline}")
        print(f"{'=' * 96}")
        header = f"  Bucket  | {baseline} (sim_w15/w30/n)        |"
        for v in variants:
            header += f" {v:6} (dW15  dW30  dN%)         |"
        print(header)
        print('-' * len(header))

        for bucket in bucket_set:
            if bucket not in base:
                continue
            b = base[bucket]
            row = f"  {bucket:7} | {b['sim_w15']:5.1f}% / {b['sim_w30']:5.1f}% / N={b['sim_n']:6d} |"
            for v in variants:
                if bucket not in results[v]:
                    row += f" {'(missing)':35} |"
                    continue
                vb = results[v][bucket]
                d_w15 = vb['sim_w15'] - b['sim_w15']
                d_w30 = vb['sim_w30'] - b['sim_w30']
                d_n   = (vb['sim_n'] - b['sim_n']) / max(1, b['sim_n']) * 100
                row += f" {d_w15:+5.1f}pp/{d_w30:+5.1f}pp/{d_n:+5.1f}%       |"
            print(row)


def main():
    if len(sys.argv) < 2:
        print("usage: analyze_evr1.py <log_path> [baseline_label]")
        sys.exit(1)
    path = sys.argv[1]
    baseline = sys.argv[2] if len(sys.argv) > 2 else 'B'
    if not os.path.exists(path):
        print(f"[evr1-analyze] file not found: {path}")
        sys.exit(2)

    results = parse_log(path)
    print(f"[evr1-analyze] parsed {len(results)} variants from {path}")
    for v, buckets in results.items():
        print(f"  {v}: {len(buckets)} buckets parsed")

    emit_comparison(results, baseline=baseline)


if __name__ == '__main__':
    main()
