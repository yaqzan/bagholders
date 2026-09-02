#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Emit the LOCKED S3 screen grid for ladder_mc.py.

The grid is exactly what the coordinator specified on 2026-07-25 -- NO
additions, no extra probe arms, no spread-floor sweep (CHARTER 4.3 says the
floor is swept on the WINNER, not in the screen).

  barrier groups (IMPORT-TIME axes -- one process each):
      dte      in {15, 30}
      tp       in {+0.30, +0.50, +1.00}
      sl       in {-0.50, -0.70, dead_hold}
    = 2 x 3 x 3 = 18 groups

  within-group (FREE axes -- all share one 23 s prepare):
      n_slots    in {1, 2, 3}
      slot_frac  in {0.33, 0.50, 1.00}
    = 9 option cells per group  -> 18 x 9 = 162 option cells

  spread_floor fixed at 0.05 for the whole screen
  instrument   'option'

  PLUS the equity arm: n_slots {1,2,3} x slot_frac {0.33,0.50,1.00} = 9 cells,
  carried inside ONE barrier group (the baseline dte=30 / tp=+0.30 / sl=-0.70).
  The equity cells cost no extra prepare -- `instrument` is a free axis and the
  equity arm reads the SAME sigma barriers off the SAME precomputed outcomes.

Writes one JSON per barrier group plus a manifest, and prints the exact
commands. Does NOT run anything and does NOT touch the task queue.

  python experiments/bankroll_ladder/make_s3_grid.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / 'results' / 's3_grid'

DTES = [15, 30]
TPS = [0.30, 0.50, 1.00]
SLS = [('sl50', -0.50, False), ('sl70', -0.70, False), ('dh', -0.70, True)]
SLOTS = [1, 2, 3]
FRACS = [0.33, 0.50, 1.00]
SPREAD_FLOOR = 0.05           # FIXED for the screen
EQUITY_SLIPPAGE_BPS = 5.0

# The one barrier group that also carries the equity arm.
EQUITY_GROUP = (30, 0.30, 'sl70')


def tp_tag(tp: float) -> str:
    return f"tp{int(round(tp * 100))}"


def frac_tag(f: float) -> str:
    return f"f{int(round(f * 100))}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    n_option = n_equity = 0
    for dte in DTES:
        for tp in TPS:
            for sl_tag, sl, dead in SLS:
                gname = f"d{dte}_{tp_tag(tp)}_{sl_tag}"
                cells = []
                for ns in SLOTS:
                    for fr in FRACS:
                        cells.append(dict(
                            name=f"opt_{gname}_s{ns}_{frac_tag(fr)}",
                            instrument='option',
                            dte=dte, tp=tp, sl=sl, sl_dead_hold=dead,
                            n_slots=ns, slot_frac=fr,
                            spread_floor=SPREAD_FLOOR,
                        ))
                        n_option += 1
                if (dte, tp, sl_tag) == EQUITY_GROUP:
                    for ns in SLOTS:
                        for fr in FRACS:
                            cells.append(dict(
                                name=f"eq_{gname}_s{ns}_{frac_tag(fr)}",
                                instrument='equity',
                                dte=dte, tp=tp, sl=sl, sl_dead_hold=dead,
                                n_slots=ns, slot_frac=fr,
                                equity_slippage_bps=EQUITY_SLIPPAGE_BPS,
                            ))
                            n_equity += 1
                path = OUT_DIR / f"{gname}.json"
                path.write_text(json.dumps(cells, indent=2))
                manifest.append(dict(group=gname, config=str(
                    path.relative_to(HERE.parent.parent)).replace('\\', '/'),
                    n_cells=len(cells),
                    out=f"experiments/bankroll_ladder/results/s3_grid/out_{gname}.json"))
    (OUT_DIR / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2))

    print(f"wrote {len(manifest)} barrier-group configs to {OUT_DIR}")
    print(f"  option cells : {n_option}")
    print(f"  equity cells : {n_equity}")
    print(f"  TOTAL cells  : {n_option + n_equity}")
    print()
    print("--- run one group (N=300, full-runway starts, standard savings null) ---")
    for m in manifest:
        print(f"PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "
              f"python experiments/bankroll_ladder/ladder_mc.py --mode screen "
              f"--config {m['config']} --n-iter 300 --workers 8 "
              f"--out {m['out']}")
    print()
    print("--- merge every finished group into one ranked table ---")
    print("python experiments/bankroll_ladder/ladder_mc.py "
          "--merge 'experiments/bankroll_ladder/results/s3_grid/out_*.json'")


if __name__ == '__main__':
    main()
