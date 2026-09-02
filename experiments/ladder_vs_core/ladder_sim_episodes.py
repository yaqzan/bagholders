#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Episode-based ladder simulator (Method 3 — the FAITHFUL 2y-rolling-episode
forward walk). Consumes the rolling-2y episode panels dumped by dump_episodes.py:
  experiments/ladder_vs_core/results/episodes_sprint.json
  experiments/ladder_vs_core/results/episodes_core.json

PORTFOLIO-STAGE research. No ship, no version bump, no scoring change.

Why this is more faithful than the year-block model (ladder_sim.py):
  - The user's scheme is a chain of FRESH sprints that run UNTIL they double (or
    a cap). The 2y rolling episode IS that unit: each window is a fresh-$50k
    sprint over up to 2y, with t2x = first-passage trading-bar to $100k (or None),
    final = continuous-hold terminal, dd = max drawdown.
  - We chain episodes along the REAL calendar: a cursor walks forward; each step
    draws the episode whose rolling-window start is nearest the cursor (REGIME-
    FAITHFUL — preserves the actual 2020/22/23 ordering), advances the cursor by
    the episode's realized duration (t2x in calendar days if it doubled, else the
    full ~2y window), banks $50k on a double + restarts a fresh episode.
  - On a double we bank $50k and restart fresh; the running tranche is the
    POST-double residual is NOT modelled separately (a fresh episode is drawn) —
    this matches "split off $50k, keep $50k running a FRESH sprint".
  - NON-doublers: the episode ends at its 2y final (eroded). The running tranche
    carries that eroded value into the next episode (the episode's % outcome is
    applied multiplicatively to the carried tranche), OR --restart-on-fail funds
    a fresh $50k from cold-store (capital-neutral give-up).

METHODOLOGY: regime correlation preserved by calendar-sequential episode chaining
(NO i.i.d. shuffle). M3a = chain along the real 2016->2026 calendar. M3b =
stationary block bootstrap over episode windows (randomised start, CI/robustness).

DRAWDOWN: total-portfolio (cold-store + running). Per episode we know the
running-tranche max_dd and the cold-store block return/DD over the same calendar
span (cold grows by Core/Sentinel returns drawn from the SAME calendar window).
Conservative simultaneous-trough bound, plus a diluted (non-coincident) bound.
"""
from __future__ import annotations
import argparse
import json
import math
import random
import statistics
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / 'results'
START = 50_000.0
TWO_X = 100_000.0
COLLAPSE_FRAC = 0.20
TD_PER_CAL = 252.0 / 365.25   # trading days per calendar day
HORIZON_5Y_CAL = 5 * 365
HORIZON_10Y_CAL = 10 * 365


def load_episode_panels():
    sp = json.loads((RES / 'episodes_sprint.json').read_text())
    co = json.loads((RES / 'episodes_core.json').read_text())
    # index by window start date
    def index(d):
        out = []
        for w in d['windows']:
            out.append({
                'start': date.fromisoformat(w['start']),
                'end': date.fromisoformat(w['end']),
                'n_td': w['n_trading_days'],
                'finals': w['paths']['finals'],
                't2x': w['paths']['t2x_bars'],
                'dds': w['paths']['dds'],
                'span_cal': (date.fromisoformat(w['end']) -
                             date.fromisoformat(w['start'])).days,
            })
        out.sort(key=lambda x: x['start'])
        return out
    return index(sp), index(co)


def nearest_window(windows, cursor):
    """Window whose start is nearest the cursor calendar date (regime-faithful)."""
    best = None
    best_d = None
    for w in windows:
        dd = abs((w['start'] - cursor).days)
        if best is None or dd < best_d:
            best = w; best_d = dd
    return best


def sprint_episode(sp_win, pidx):
    """One fresh-$50k sprint episode from sprint window sp_win at path pidx.
    Returns (doubled: bool, dur_cal: int, end_mult: float, dd: float).
      doubled  : reached $100k (t2x not None)
      dur_cal  : calendar days the episode consumed (t2x->cal if doubled else
                 full window span)
      end_mult : tranche end value / $50k (only used for non-doublers; doublers
                 bank and restart so end_mult is implicitly 2.0 at the double)
      dd       : episode max drawdown fraction
    """
    t2x = sp_win['t2x'][pidx]
    final = sp_win['finals'][pidx]
    dd = sp_win['dds'][pidx]
    if t2x is not None:
        dur_cal = max(1, int(round(t2x / TD_PER_CAL)))
        return True, dur_cal, 2.0, dd
    return False, sp_win['span_cal'], max(final, 1.0) / START, dd


def core_block_return(co_win, pidx, span_cal):
    """Core block return + dd over span_cal calendar days, scaled from the Core 2y
    window's continuous-hold final (per-cal-day log-return prorated)."""
    final = co_win['finals'][pidx]
    win_span = co_win['span_cal']
    log_per_day = math.log(max(final, 1.0) / START) / win_span
    mult = math.exp(log_per_day * span_cal)
    # dd over a shorter span: scale the window dd by sqrt(span/win) (vol ~ sqrt(t))
    dd = co_win['dds'][pidx] * min(1.0, math.sqrt(span_cal / win_span))
    return mult, dd


def sentinel_block_return(co_win, pidx, span_cal, k):
    final = co_win['finals'][pidx]
    win_span = co_win['span_cal']
    log_per_day = math.log(max(final, 1.0) / START) / win_span * k
    mult = math.exp(log_per_day * span_cal)
    dd = co_win['dds'][pidx] * k * min(1.0, math.sqrt(span_cal / win_span))
    return mult, dd


def calibrate_sentinel_k(co_windows):
    """Compress factor so Core->Sentinel matches doc +3371%/10y. Core ~5y
    continuous from the most-recent ~2.5 episodes; we instead match per-cal-day:
    Sentinel target per-yr log = ln(34.71)/10 ~ 0.356. Core median per-yr log
    across windows -> k = target/core."""
    logs = []
    for w in co_windows:
        for f in w['finals']:
            logs.append(math.log(max(f, 1.0) / START) / w['span_cal'] * 365.0)
    core_per_yr_log = statistics.median(logs)
    sent_per_yr_log = math.log(34.71) / 10.0
    k = sent_per_yr_log / core_per_yr_log if abs(core_per_yr_log) > 1e-9 else 0.5
    return max(0.0, min(1.0, k))


def run_ladder_episode_rep(sp_windows, co_windows, horizon_cal, cold_dest, k,
                           rng, start_date, blockboot=False,
                           restart_on_fail=False):
    """Two-pot, always-fresh-$50k-stake episode model (panel-faithful).

    running = at-risk sprint STAKE (re-funded to min($50k, total) each episode).
    cold    = safe banked pile (grows by destination).
    Each episode is a FRESH $50k sprint draw from the calendar-nearest window.
      - doubled: stake -> 2x; BANK the $50k profit to cold; re-stake $50k fresh.
      - failed : stake -> end_mult x $50k (a realized loss); the residual stays in
                 the running pot and the NEXT episode re-funds the stake to
                 min($50k, total) (topping up from cold if cold has it; if total <
                 $50k the stake shrinks = the failure tail eating into the book).
    `restart_on_fail` is implied by the always-fresh re-stake; the flag instead
    toggles whether a deep-failed residual (<50% of stake) is swept to cold first
    (lock-in) before re-staking.
    """
    cold = 0.0
    running = START          # at-risk stake
    peak = START
    max_dd_cons = 0.0
    max_dd_dil = 0.0
    banked = 0.0
    cursor = start_date
    consumed = 0
    guard = 0
    while consumed < horizon_cal and guard < 400:
        guard += 1
        total = cold + running
        if total <= START * 0.01:    # wiped out
            break
        # (re)fund the at-risk stake to min($50k, total); rest sits in cold (safe)
        stake = min(START, total)
        cold = total - stake
        running = stake

        if blockboot:
            sp_win = rng.choice(sp_windows)
            co_win = next((c for c in co_windows if c['start'] == sp_win['start']),
                          rng.choice(co_windows))
        else:
            sp_win = nearest_window(sp_windows, cursor)
            co_win = nearest_window(co_windows, cursor)
        npaths = min(len(sp_win['t2x']), len(co_win['finals']))
        pidx = rng.randrange(npaths)

        cold_start = cold
        peak = max(peak, cold_start + stake)

        doubled, dur_cal, end_mult, ep_dd = sprint_episode(sp_win, pidx)
        dur_cal = min(dur_cal, horizon_cal - consumed)
        scale = stake / START      # <1 only when total<$50k (failure tail)

        # cold-store grows over this episode's calendar span
        if cold_dest == 'cash':
            cmult, cdd = 1.0, 0.0
        elif cold_dest == 'core':
            cmult, cdd = core_block_return(co_win, pidx, dur_cal)
        else:
            cmult, cdd = sentinel_block_return(co_win, pidx, dur_cal, k)
        cold_end = cold_start * cmult

        running_trough = stake * (1.0 - ep_dd)
        cold_trough = cold_start * (1.0 - cdd)
        # DD bounds vs running peak
        if peak > 0:
            max_dd_cons = max(max_dd_cons, 1.0 - (cold_trough + running_trough) / peak)
            dd_run_only = 1.0 - (cold_end + running_trough) / peak
            dd_cold_only = 1.0 - (cold_trough + stake) / peak
            max_dd_dil = max(max_dd_dil, dd_run_only, dd_cold_only)

        if doubled:
            profit = stake             # stake -> 2*stake; bank `stake`, keep `stake`
            banked += profit
            cold = cold_end + profit
            running = stake
        else:
            running = stake * end_mult
            cold = cold_end
            if restart_on_fail and running < stake * 0.5:
                cold += running
                running = 0.0          # next loop re-funds stake from total

        consumed += dur_cal
        cursor = date.fromordinal(min(cursor.toordinal() + dur_cal,
                                      date(2026, 4, 15).toordinal()))
        total_end = cold + running
        peak = max(peak, total_end)
        if peak > 0:
            d = 1.0 - total_end / peak
            max_dd_cons = max(max_dd_cons, d)
            max_dd_dil = max(max_dd_dil, d)

    return cold + running, max_dd_cons, max_dd_dil, banked


def run_pure_core_episode_rep(co_windows, horizon_cal, rng, start_date,
                              blockboot=False):
    eq = START
    peak = START
    max_dd = 0.0
    cursor = start_date
    consumed = 0
    guard = 0
    while consumed < horizon_cal and guard < 200:
        guard += 1
        co_win = rng.choice(co_windows) if blockboot else nearest_window(co_windows, cursor)
        pidx = rng.randrange(len(co_win['finals']))
        span = min(co_win['span_cal'], horizon_cal - consumed)
        cmult, cdd = core_block_return(co_win, pidx, span)
        peak = max(peak, eq)
        max_dd = max(max_dd, 1.0 - eq * (1.0 - cdd) / peak if peak > 0 else 0.0)
        eq *= cmult
        peak = max(peak, eq)
        max_dd = max(max_dd, 1.0 - eq / peak if peak > 0 else 0.0)
        consumed += span
        cursor = date.fromordinal(min(cursor.toordinal() + span,
                                      date(2026, 4, 15).toordinal()))
    return eq, max_dd


def pct(v, q):
    s = sorted(v)
    i = q / 100.0 * (len(s) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def summ(fin, dd_cons, dd_dil=None, banked=None):
    coll = sum(1 for f in fin if f <= START * COLLAPSE_FRAC) / len(fin) * 100.0
    o = {'median': statistics.median(fin), 'p10': pct(fin, 10), 'p25': pct(fin, 25),
         'p75': pct(fin, 75), 'p90': pct(fin, 90), 'mean': statistics.mean(fin),
         'med_dd': statistics.median(dd_cons) * 100, 'p90_dd': pct(dd_cons, 90) * 100,
         'max_dd': max(dd_cons) * 100, 'p_collapse': coll}
    if dd_dil is not None:
        o['med_dd_diluted'] = statistics.median(dd_dil) * 100
    if banked is not None:
        o['median_banked'] = statistics.median(banked)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-reps', type=int, default=20000)
    ap.add_argument('--seed', type=int, default=999)
    ap.add_argument('--restart-on-fail', action='store_true')
    ap.add_argument('--out', default='results/ladder_vs_core_episodes.json')
    args = ap.parse_args()

    sp_windows, co_windows = load_episode_panels()
    k = calibrate_sentinel_k(co_windows)
    # start the M3a calendar walk at the EARLIEST window (full 10y runway) and at
    # a recent-5y start for the 5y horizon.
    earliest = sp_windows[0]['start']
    # 5y start ~ 5y before the last window start
    last = sp_windows[-1]['start']
    start_5y = date.fromordinal(last.toordinal() - HORIZON_5Y_CAL)
    start_5y = max(start_5y, earliest)

    horizons = {'5y': (HORIZON_5Y_CAL, start_5y),
                '10y': (HORIZON_10Y_CAL, earliest)}
    dests = ['cash', 'sentinel', 'core']
    results = {'_meta': {
        'n_reps': args.n_reps, 'seed': args.seed, 'sentinel_k': k,
        'restart_on_fail': args.restart_on_fail,
        'episode_windows_sprint': len(sp_windows),
        'episode_windows_core': len(co_windows),
        'window_span': [earliest.isoformat(), sp_windows[-1]['end'].isoformat()],
        'methodology': ('M3a episode forward-walk along real calendar (regime-'
                        'ordered); M3b stationary episode-block bootstrap. 2y '
                        'rolling sprint episodes, bank-on-double + fresh restart. '
                        'NO i.i.d. shuffle.'),
    }}

    for method, bb in [('M3a_calendar', False), ('M3b_blockboot', True)]:
        results[method] = {}
        for hz, (hcal, sd) in horizons.items():
            results[method][hz] = {}
            for dest in dests:
                rng = random.Random(args.seed + hash((method, hz, dest)) % 100000)
                fin, ddc, ddd, bk = [], [], [], []
                for _ in range(args.n_reps):
                    t, dc, dl, b = run_ladder_episode_rep(
                        sp_windows, co_windows, hcal, dest, k, rng, sd,
                        blockboot=bb, restart_on_fail=args.restart_on_fail)
                    fin.append(t); ddc.append(dc); ddd.append(dl); bk.append(b)
                results[method][hz][f'ladder_{dest}'] = summ(fin, ddc, ddd, bk)
            rng = random.Random(args.seed + hash((method, hz, 'pc')) % 100000)
            fin, ddc = [], []
            for _ in range(args.n_reps):
                t, dc = run_pure_core_episode_rep(co_windows, hcal, rng, sd, bb)
                fin.append(t); ddc.append(dc)
            results[method][hz]['pure_core'] = summ(fin, ddc)

    outp = HERE / args.out
    outp.write_text(json.dumps(results, indent=2))

    def f(v):
        if v != v:
            return '   n/a'
        if abs(v) >= 1e6:
            return f'{v/1e6:8.2f}M'
        return f'{v:9,.0f}'

    print('=' * 116)
    print('LADDER vs PURE-CORE  |  EPISODE model (2y rolling sprint episodes, '
          f'bank-on-double)  N={args.n_reps:,}  Sentinel k={k:.3f}')
    print(f"  episode windows: {len(sp_windows)} sprint / {len(co_windows)} core   "
          f"span {results['_meta']['window_span'][0]}..{results['_meta']['window_span'][1]}"
          f"   restart_on_fail={args.restart_on_fail}")
    print('=' * 116)
    for method in ['M3a_calendar', 'M3b_blockboot']:
        tag = ('M3a episode forward-walk, REAL calendar order (PREFERRED)'
               if method == 'M3a_calendar'
               else 'M3b episode-block bootstrap (randomised order)')
        print(f'\n### {tag}')
        for hz in ['5y', '10y']:
            print(f'\n  [{hz}]')
            hdr = (f"  {'scheme':<18}{'median':>11}{'P10':>11}{'P25':>11}{'P75':>11}"
                   f"{'P90':>11}{'medDD%':>8}{'p90DD%':>8}{'ruin%':>7}{'bank(med)':>11}")
            print(hdr); print('  ' + '-' * (len(hdr) - 2))
            for sch in ['ladder_cash', 'ladder_sentinel', 'ladder_core', 'pure_core']:
                s = results[method][hz][sch]; bk = s.get('median_banked')
                print(f"  {sch:<18}{f(s['median']):>11}{f(s['p10']):>11}"
                      f"{f(s['p25']):>11}{f(s['p75']):>11}{f(s['p90']):>11}"
                      f"{s['med_dd']:>7.1f}%{s['p90_dd']:>7.1f}%{s['p_collapse']:>6.1f}%"
                      f"{(f(bk) if bk is not None else '     -'):>11}")
    print(f'\nwrote {outp}')


if __name__ == '__main__':
    main()
