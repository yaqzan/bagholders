#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase D deterministic backtest parity check (PREREG.md section 6:
"backtest_cascade deterministic parity: winner vs baseline single replay,
sanity that MC direction reproduces chronologically"). SANITY CHECK ONLY,
NOT A GATE (task spec): flag only if the deterministic replay INVERTS the
MC-measured direction (finalist beats baseline in the MC sweep but loses in
this replay, or vice versa) -- analyze_phaseD.py renders that flag; this
file only produces the numbers.

SCOPE DECISION (read before extending or trusting an 'apex' row's DD/return
as an Apex portfolio number): backtest_cascade.py's own portfolio sizing
(MAX_POSITIONS, TIER_ALLOC, MAX_POSITIONS_CALL/PUT -- bc.py:107-109, 656) is
captured ONCE at import from strategy_config.STRATEGY_30DTE with NO env-var
or cfg-dict override path (`_CFG_KNOB_GLOBALS`, bc.py:3005-3027, covers
breadth/F3F/regime-slope/CT/CTSL/SAW/DTE-router/dead-hold knobs only --
sizing is not in that map). Unlike monte_carlo.py, there is no sanctioned
way to make backtest_cascade.py replay Apex's flat 10x10% sizing without
EITHER a production-file edit (forbidden) OR ALSO monkeypatching
bc.MAX_POSITIONS/bc.TIER_ALLOC post-import (an unverified extra risk: it is
not confirmed here whether the position-sizing code path reads those as
live module globals at call time the same way compute_outcome() does for
TP/SL). This file therefore runs EVERY arm -- Core AND Apex TP/SL finalists
alike -- through backtest_cascade.py's one native CORE cascade sizing. Apex
rows are a TP/SL-BARRIER-DIRECTION proxy only, NOT a reproduction of the
Apex portfolio's DD/compound -- every row carries a `sizing_note` CSV column
saying so explicitly, so this caveat survives even if the row is read
without this docstring. This is a deliberate scope decision, not an
oversight (STOP-rule: report the boundary, do not silently guess past it) --
true Apex-sized parity is a possible future extension.

TP/SL override mechanism (VERIFIED 2026-08-10 recon -- no _OV env exists in
this module, unlike monte_carlo.py): post-import monkeypatch of
backtest_cascade's own module globals (TP_SIGMA_BASE/STRESS, SL_SIGMA_BASE/
STRESS at bc.py:443-446; NET_TP_BASE/STRESS, NET_SL_BASE/STRESS at
bc.py:647-650), mirroring mc_patch.set_tpsl()'s exact formula term-for-term
but sourced from backtest_cascade's own `_cfg`/`_opt` (==
strategy_config.STRATEGY_30DTE(.option) -- the SAME frozen-dataclass config
object monte_carlo.py's Core profile reads; there is only one 30-DTE option
config in this repo). compute_outcome()'s default (non-DTE-routed) path
reads these as bare module-global FALLBACKS (`cfg.get('tp_sigma_base',
TP_SIGMA_BASE)` etc, bc.py:1694-1703, cfg=None here so the fallback always
applies) -- so reassigning them on the imported `bc` module object directly
correctly redirects every subsequent compute_outcome() call in this process.
Confirmed harmless w.r.t. run_cascade_backtest's OWN per-request override
context manager (`_cfg_knob_overrides`, bc.py:3031): its `_CFG_KNOB_GLOBALS`
map does not mention any of the 8 names above, and this file always passes
cfg=None, so that context manager is a documented no-op here and never
restores/clobbers this file's monkeypatch between calls.

Usage:
    python experiments/tpsl_refine_2026_08/driver/phaseD_cascade_parity.py \
        --job NAME --profile core|apex --cells "tp,sl;tp,sl;..." \
        [--from 2022-01-01] [--capital 50000]

HARD RULE: backtest_cascade.py is imported read-only and only ever
monkeypatched post-import (module-global reassignment) -- the file on disk
is never edited. This script never git-commits anything and never persists
to MySQL (run_cascade_backtest's own pipeline has no write path for a plain
CLI-style call like this one -- verified: only compute + in-memory result).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                       # .../driver
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))    # repo root
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} has no monte_carlo.py "
    f"(computed from __file__={__file__!r}) -- fix the dirname() chain above"
)

EXP_DIR = os.path.dirname(_THIS_DIR)
OUT_DIR = os.path.join(EXP_DIR, 'out')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
META_PATH = os.path.join(STATE_DIR, 'meta.json')   # SAME pinned-version file Phase A wrote

from phaseD_run import parse_cells_arg, BASELINE_CELL   # noqa: E402 -- pure parsing fn, no side effects

CSV_FIELDS = [
    'job', 'profile', 'sizing_note', 'arm_tag', 'tp', 'sl', 'is_baseline',
    'from_date', 'to_date', 'capital', 'final_equity', 'total_return_pct',
    'max_dd_pct', 'n_trades', 'n_tp', 'n_sl', 'n_hard', 'elapsed_s',
]


def set_tpsl_bc(bc, tp, sl, tp_stress=None, sl_stress=None):
    """Mirror of mc_patch.set_tpsl(), targeting backtest_cascade.py's module
    globals instead of monte_carlo's (see file header for the full
    verification trail). Flat by default (stress=base) -- Phase D confirms
    flat cells only."""
    if tp_stress is None:
        tp_stress = tp
    if sl_stress is None:
        sl_stress = sl
    premium_mult = bc._cfg.PREMIUM_MULT
    delta = bc._opt.DELTA
    slip_entry = bc._opt.SLIP_ENTRY
    slip_tp = bc._opt.SLIP_TP
    slip_sl = bc._opt.SLIP_SL
    bc.TP_SIGMA_BASE = tp * premium_mult / delta
    bc.TP_SIGMA_STRESS = tp_stress * premium_mult / delta
    bc.SL_SIGMA_BASE = abs(sl) * premium_mult / delta
    bc.SL_SIGMA_STRESS = abs(sl_stress) * premium_mult / delta
    bc.NET_TP_BASE = tp + slip_entry + slip_tp
    bc.NET_TP_STRESS = tp_stress + slip_entry + slip_tp
    bc.NET_SL_BASE = sl + slip_entry + slip_sl
    bc.NET_SL_STRESS = sl_stress + slip_entry + slip_sl


def trade_kind_counts(trade_log):
    n_tp = sum(1 for t in trade_log if t.get('outcome') == 'tp')
    n_sl = sum(1 for t in trade_log if t.get('outcome') == 'sl')
    n_hard = sum(1 for t in trade_log if t.get('outcome') == 'hard')
    return n_tp, n_sl, n_hard


def arm_tag(tp, sl):
    return f"tp{tp:+.2f}_sl{sl:+.2f}"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--job', required=True, help='job name -- tags output rows + default csv name')
    p.add_argument('--profile', required=True, choices=['core', 'apex'],
                   help='LABEL only -- both profiles run under backtest_cascade\'s native Core '
                        'cascade sizing; see file header SCOPE DECISION')
    p.add_argument('--cells', default=None,
                   help="'tp,sl;tp,sl;...' finalist(s); baseline (0.30,-0.70) always included")
    p.add_argument('--from', dest='from_date', default='2022-01-01')
    p.add_argument('--to', dest='to_date', default=None)
    p.add_argument('--capital', type=float, default=50_000.0)
    p.add_argument('--min-score', type=float, default=70.0,
                   help='matches backtest_cascade.py main()\'s own CLI default -- consistent with '
                        'how this module is normally operated')
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    import mc_patch
    version_meta = mc_patch.resolve_and_pin_version(META_PATH)   # meta.json already exists -- no DB touch

    # Explicit pin (matches PREREG section 7 "frozen recipe pins" philosophy)
    # even though strategy_config's own default is already 0.0 -- belt and
    # suspenders against a future default-enable silently shifting this run.
    os.environ.setdefault('LIQUIDITY_FLOOR', '0.0')
    import backtest_cascade as bc

    cells = parse_cells_arg(args.cells)
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date) if args.to_date else None
    sizing_note = 'core-cascade-native' if args.profile == 'core' else 'core-cascade-PROXY(NOT apex-sized; see file header)'

    print(f"[phaseD_cascade_parity] job={args.job} profile={args.profile} "
         f"sizing_note={sizing_note!r} cells={cells} from={from_date} to={to_date} "
         f"capital={args.capital} version_id={version_meta['id']}", flush=True)

    rows = []
    for tp, sl in cells:
        tag = arm_tag(tp, sl)
        set_tpsl_bc(bc, tp, sl)
        t0 = time.time()
        result = bc.run_cascade_backtest(version_meta['id'], min_score=args.min_score,
                                         from_date=from_date, to_date=to_date,
                                         initial=args.capital, verbose=False,
                                         calls_only=True, cfg=None)
        elapsed = time.time() - t0
        if not result:
            print(f"  [warn] arm={tag}: no qualifying signals in range -- skipped", flush=True)
            continue
        trade_log = result['trade_log']
        n_tp, n_sl, n_hard = trade_kind_counts(trade_log)
        final_equity = result['final_equity']
        total_return_pct = (final_equity / args.capital - 1.0) * 100.0
        max_dd_pct = result['max_dd'] * 100.0
        row = {
            'job': args.job, 'profile': args.profile, 'sizing_note': sizing_note,
            'arm_tag': tag, 'tp': tp, 'sl': sl, 'is_baseline': (tp, sl) == BASELINE_CELL,
            'from_date': str(result.get('start_date', from_date)),
            'to_date': str(result.get('end_date', to_date)),
            'capital': args.capital, 'final_equity': round(final_equity, 2),
            'total_return_pct': round(total_return_pct, 1), 'max_dd_pct': round(max_dd_pct, 1),
            'n_trades': len(trade_log), 'n_tp': n_tp, 'n_sl': n_sl, 'n_hard': n_hard,
            'elapsed_s': round(elapsed, 1),
        }
        rows.append(row)
        print(f"  [ARM DONE] {tag} n_trades={len(trade_log)} (tp={n_tp} sl={n_sl} hard={n_hard}) "
             f"final_equity=${final_equity:,.0f} total_return={total_return_pct:+.1f}% "
             f"max_dd={max_dd_pct:.1f}% ({elapsed:.1f}s)", flush=True)

    csv_path = os.path.join(OUT_DIR, f'phaseD_cascade_parity_{args.job}.csv')
    csv_is_new = not os.path.exists(csv_path)
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if csv_is_new:
            w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"\n[WROTE] {csv_path} ({len(rows)} row(s) appended)", flush=True)

    # Direction sanity echo (informational only -- analyze_phaseD.py renders
    # the formal invert-vs-MC flag by joining this CSV against the MC CSVs).
    base_row = next((r for r in rows if r['is_baseline']), None)
    if base_row:
        for r in rows:
            if r['is_baseline']:
                continue
            d_dd = r['max_dd_pct'] - base_row['max_dd_pct']
            d_ret = r['total_return_pct'] - base_row['total_return_pct']
            print(f"  [DIRECTION] {r['arm_tag']} vs baseline: d_max_dd={d_dd:+.1f}pp "
                 f"d_total_return={d_ret:+.1f}pp  (lower DD better, higher return better; "
                 f"analyze_phaseD.py flags only if this SIGN disagrees with the MC read)", flush=True)
    else:
        print("  [warn] no baseline row in this run -- direction echo skipped", flush=True)


if __name__ == '__main__':
    main()
