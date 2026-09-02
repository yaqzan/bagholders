#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dump rolling-start SPRINT episode panels + CORE block panels for the
ladder-vs-core resampling simulator.

PORTFOLIO-STAGE research. No ship, no version bump, no scoring change.

Two products, both via the MC's WIN_START/WIN_END + MC_RETURN_PATHS contract
(monte_carlo.py reads these env vars; calls-only flat_n4_a25 sprint config and
the v74-Apex / Core cascade come from env knobs identical to
experiments/concentration_2x/sweep.py cell_env()).

Output: experiments/ladder_vs_core/results/episodes_<cell>.json
  { meta, windows: [ {label, start, end, n_td, paths:{finals,dds,t2x_bars,
                       t_50dd_bars, n_trading_days, starting_cash}} ] }

The SPRINT panel (flat_n4_a25) over rolling 2y-start windows gives the
fresh-start episode distribution (t2x first-passage, continuous-hold terminal,
maxdd). The CORE panel (v74 Apex cascade = the held compounder) over the SAME
windows gives the cold-store / pure-Core block returns.

Driven in-process (load-once-per-window) for speed; calls monte_carlo.run_window
directly per window with the cell env applied at module import via os.environ.

USAGE (run by the ladder_vs_core driver or directly):
  python experiments/ladder_vs_core/dump_episodes.py --cell sprint --n-iter 300 \
      --step-months 2 --horizon-days 730 --out results/episodes_sprint.json
  python experiments/ladder_vs_core/dump_episodes.py --cell core --n-iter 300 \
      --step-months 2 --horizon-days 730 --out results/episodes_core.json

This is intended to run as ONE small queue job per cell when DB is free.
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
HIST_START = date(2016, 6, 1)
HIST_END = date(2026, 4, 15)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day
    import calendar
    dd = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, dd)


def rolling_windows(step_months: int, horizon_days: int,
                    min_horizon_days: int = 180,
                    hist_start: date = HIST_START,
                    hist_end: date = HIST_END):
    wins = []
    cur = hist_start
    while cur < hist_end:
        w_end = min(cur + timedelta(days=horizon_days), hist_end)
        runway = (w_end - cur).days
        if runway >= min_horizon_days:
            wins.append((f"{cur.isoformat()}", cur, w_end))
        cur = _add_months(cur, step_months)
    return wins


def apply_cell_env(cell: str):
    """Set the sizing env knobs BEFORE importing monte_carlo (import-time reads)."""
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    os.environ['MC_RETURN_PATHS'] = '1'
    os.environ['MC_NO_DB_PERSIST'] = '1'
    os.environ['STARTING_CASH_OV'] = '50000'
    # calls-only (v74 Apex is puts-off already; pin it)
    os.environ['MAX_POSITIONS_PUT'] = '0'
    os.environ['PUT_TIER_TOP_OV'] = '0.0'
    os.environ['PUT_TIER_MID_OV'] = '0.0'
    os.environ['PUT_TIER_LOW_OV'] = '0.0'
    if cell == 'sprint':
        # flat_n4_a25: 4 positions x 25% each, all tiers equal, overflow 0
        a = '0.250000'
        os.environ['TIER_ULTRA_OV'] = a
        os.environ['TIER_TOP_OV'] = a
        os.environ['TIER_MID_OV'] = a
        os.environ['TIER_LOW_OV'] = a
        os.environ['TIER_OVERFLOW_OV'] = '0.0'
        os.environ['MAX_POSITIONS_OVERRIDE'] = '4'
        os.environ['MAX_POSITIONS_CALL'] = '4'
    elif cell == 'core':
        # v74 Apex production cascade (the held compounder = "Core")
        os.environ['TIER_ULTRA_OV'] = '0.20'
        os.environ['TIER_TOP_OV'] = '0.15'
        os.environ['TIER_MID_OV'] = '0.08'
        os.environ['TIER_LOW_OV'] = '0.03'
        os.environ['TIER_OVERFLOW_OV'] = '0.0'
        os.environ['MAX_POSITIONS_OVERRIDE'] = '14'
        os.environ['MAX_POSITIONS_CALL'] = '14'
    else:
        raise SystemExit(f"unknown cell {cell}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cell', required=True, choices=['sprint', 'core'])
    ap.add_argument('--n-iter', type=int, default=300)
    ap.add_argument('--step-months', type=int, default=2)
    ap.add_argument('--horizon-days', type=int, default=730)
    ap.add_argument('--min-horizon-days', type=int, default=180)
    ap.add_argument('--workers', type=int, default=0)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    apply_cell_env(args.cell)
    if args.workers and args.workers > 1:
        os.environ['MC_WORKERS'] = str(args.workers)
    elif args.workers == 1:
        os.environ['MC_NO_MP'] = '1'
    os.environ['N_ITER_OVERRIDE'] = str(args.n_iter)

    import monte_carlo as mc  # import AFTER env set
    version = mc.AlgorithmVersion.get_active_scores_version() \
        if hasattr(mc, 'AlgorithmVersion') else None

    wins = rolling_windows(args.step_months, args.horizon_days,
                           args.min_horizon_days)
    print(f"[dump_episodes] cell={args.cell} n_iter={args.n_iter} "
          f"windows={len(wins)} step={args.step_months}mo horizon={args.horizon_days}d",
          flush=True)

    out = {
        '_meta': {
            'cell': args.cell, 'n_iter': args.n_iter,
            'step_months': args.step_months, 'horizon_days': args.horizon_days,
            'starting_cash': 50000.0,
            'calendar_hold': getattr(mc, 'CALENDAR_HOLD', None),
            'hold_cal_days': getattr(mc, 'HOLD_CAL_DAYS', None),
        },
        'windows': [],
    }
    for i, (lbl, ws, we) in enumerate(wins):
        print(f"  [{i+1}/{len(wins)}] {lbl}  {ws} -> {we}", flush=True)
        res = mc.run_window(lbl, ws, we, version)['seeded']
        out['windows'].append({
            'label': lbl, 'start': ws.isoformat(), 'end': we.isoformat(),
            'n_trading_days': res.get('n_trading_days'),
            'paths': {
                'finals': [float(x) for x in res['finals']],
                'dds': [float(x) for x in res['dds']],
                't2x_bars': res.get('t2x_bars'),
                't_50dd_bars': res.get('t_50dd_bars'),
                'starting_cash': res.get('starting_cash'),
            },
        })

    outp = HERE / args.out if not os.path.isabs(args.out) else Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out))
    print(f"[dump_episodes] wrote {len(out['windows'])} windows -> {outp}",
          flush=True)


if __name__ == '__main__':
    main()
