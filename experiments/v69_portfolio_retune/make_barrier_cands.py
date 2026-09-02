"""Build the Stage-2 barrier candidate JSON, layered on a survivable sizing base.

Given the winning sizing base params (from the sizing sweep), generate barrier
variants probing the thin-edge break-even axes:
  - call SL tighter (faster recycle, lower base BE) vs current
  - call TP modest down (faster fills)
  - breadth_threshold (stress band trigger frequency)
  - PUT_SL tighter (lower put BE; reverted-stepped territory but honest substrate)
  - PUT_TP modest changes

Each is the base dict UNION the barrier overrides. Writes barrier_cands.json.

Usage:
  python experiments/v69_portfolio_retune/make_barrier_cands.py '<BASE_JSON>'
"""
from __future__ import annotations
import json
import os
import sys


def main():
    base = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    cands = {}

    def add(name, **ov):
        d = dict(base)
        d.update(ov)
        cands[name] = d

    # reference: base sizing with current barriers
    add('base_curbarrier')
    # Call SL tighter — faster recycle, lowers base BE (27->22/20). Watch collapse.
    add('csl_-0.22', SL_BASE=-0.22)
    add('csl_-0.24', SL_BASE=-0.24)
    add('csl_-0.30', SL_BASE=-0.30)             # wider SL (fewer false stops, higher BE)
    # Call TP modest down — faster fills on a thin edge
    add('ctp_0.30', TP_BASE=0.30)
    add('ctp_0.30_csl_-0.24', TP_BASE=0.30, SL_BASE=-0.24)
    add('ctp_0.36_csl_-0.30', TP_BASE=0.36, SL_BASE=-0.30)   # wider both (let winners run)
    # Breadth threshold — how often the wider stress band engages
    add('brd_50', BREADTH_THRESHOLD=50)
    add('brd_30', BREADTH_THRESHOLD=30)
    # Put SL tighter — lowers put BE toward the honest ~25-40% put TP
    add('psl_-0.16', PUT_SL=-0.16)
    add('psl_-0.14', PUT_SL=-0.14)
    # Put TP wider — also lowers put BE (BE = |SL|/(TP+|SL|))
    add('ptp_0.40', PUT_TP=0.40)
    add('ptp_0.45_psl_-0.16', PUT_TP=0.45, PUT_SL=-0.16)
    # Combined thin-edge: tighter call SL + tighter put SL
    add('csl_-0.24_psl_-0.16', SL_BASE=-0.24, PUT_SL=-0.16)

    out = os.path.join(os.path.dirname(__file__), 'barrier_cands.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(cands, f, indent=2)
    print(f'wrote {len(cands)} barrier candidates -> {out}')
    print(json.dumps(cands, indent=2)[:1500])


if __name__ == '__main__':
    main()
