#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Policy composer: loads {sprint, core} quarterly-rolled 2y panels and
composes the 3 pre-registered lifecycle arms (DESIGN.md section 3) via
pure-Python resampling. No MC, no MySQL -- cheap, runs after panels.py.

Arms:
  core_only            : $50k held in Core for the full lifecycle horizon.
  sprint_rotate_core    : $50k in the sprint until first-passage 2x (or the
                          "ride to window end" fallback at 730d, DESIGN.md
                          section 4), then 100% rotates to Core for the rest
                          of the horizon.
  ladder_sprint_core    : bank-on-double + fresh-$50k-restake repeating
                          sprint, cold-store grows in Core (subsumes
                          experiments/ladder_vs_core/ladder_sim_episodes.py's
                          Method-3 mechanic -- DESIGN.md section 3c/9).

See DESIGN.md section 6 for the composition-seam disclosures this module
implements (calendar-nearest-window chaining, per-day log-return proration,
shared-index regime pairing, common-random-numbers-per-(start,rep,arm)
seeding, conservative running-peak DD bound). Renders NO verdict -- the
'bars_check' block in the output is plain arithmetic against DESIGN.md
section 8's pre-registered bar, same discipline as
experiments/apex_dte_dd/run_p03_evidence.py's own T1-T7 evidence block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.lifecycle_mc.panels import build_grid, PANELS_DIR  # noqa: E402
from experiments.concentration_2x.sweep import HIST_END  # noqa: E402

START = 50_000.0
TWO_X = 100_000.0
COLLAPSE_FRAC = 0.20               # sitewide convention (concentration_2x, ladder_vs_core)
SPRINT_HORIZON_CAL_DAYS = 730       # DESIGN.md section 4 fallback rule ("ride to window end")
LIFECYCLE_HORIZON_CAL_DAYS = 5 * 365   # 5y, sitewide Core comparison unit
TD_PER_CAL = 252.0 / 365.25         # trading days per calendar day (concentration_2x convention)
COVID_START, COVID_END = date(2020, 2, 1), date(2020, 4, 30)   # monte_carlo.WINDOWS '2020_crash'


# ---------------------------------------------------------------------------
# Deterministic (cross-process-stable) seeding -- Python's built-in hash() on
# str is salted per-process by PYTHONHASHSEED unless fixed, so it is NOT
# reproducible run-to-run; hashlib is. Used instead of the hash()-on-tuple
# pattern in the older ladder_vs_core/ladder_sim.py.
# ---------------------------------------------------------------------------
def _stable_seed(*parts) -> int:
    s = '|'.join(str(p) for p in parts)
    return int(hashlib.md5(s.encode('utf-8')).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Panel loading
# ---------------------------------------------------------------------------
def load_panel(arm: str, n_iter: int) -> list[dict]:
    """Returns a list of window dicts sorted by start date:
      {label, start (date), end (date), span_cal (int days), n_td,
       finals: [...], dds: [...], t2x_bars: [None or int, ...]}
    Missing/failed cells are skipped (not zero-filled) -- callers tolerate a
    thinner grid than the ideal (a resubmit fills gaps in later)."""
    grid = build_grid()
    arm_dir = os.path.join(PANELS_DIR, arm)
    out = []
    n_missing = 0
    for (label, ws, we) in grid:
        p = os.path.join(arm_dir, f'{label}.json')
        if not os.path.exists(p):
            n_missing += 1
            continue
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cell = data.get(label)
        if not cell or 'paths' not in cell:
            n_missing += 1
            continue
        paths = cell['paths']
        finals = paths.get('finals') or []
        if not finals:
            n_missing += 1
            continue
        out.append({
            'label': label, 'start': ws, 'end': we,
            'span_cal': max(1, (we - ws).days),
            'n_td': paths.get('n_trading_days'),
            'finals': [float(x) for x in finals],
            'dds': [float(x) for x in (paths.get('dds') or [])],
            't2x_bars': paths.get('t2x_bars') or [None] * len(finals),
            'starting_cash': paths.get('starting_cash') or START,
        })
    if n_missing:
        print(f"[policy] WARNING: {arm} panel missing/empty {n_missing}/{len(grid)} "
              f"window cells -- proceeding with the {len(out)} available.", flush=True)
    out.sort(key=lambda w: w['start'])
    return out


def nearest_window(windows: list[dict], cursor: date) -> dict:
    """Calendar-nearest-start window to `cursor` (DESIGN.md section 6.1)."""
    best, best_d = None, None
    for w in windows:
        d = abs((w['start'] - cursor).days)
        if best is None or d < best_d:
            best, best_d = w, d
    return best


def bars_to_caldays(t2x_bar) -> int | None:
    if t2x_bar is None:
        return None
    return max(1, int(round(t2x_bar / TD_PER_CAL)))


def core_block_return(co_win: dict, pidx: int, span_cal: int) -> tuple[float, float]:
    """Core multiplier + dd over span_cal calendar days, prorated from the
    (possibly longer) panel window's own realized log-return (DESIGN.md
    section 6.1; identical technique to ladder_sim_episodes.py's own
    core_block_return, re-implemented here so lifecycle_mc has no runtime
    dependency on the ladder_vs_core directory)."""
    n = len(co_win['finals'])
    final = co_win['finals'][pidx % n]
    dd = co_win['dds'][pidx % len(co_win['dds'])] if co_win['dds'] else 0.0
    win_span = max(1, co_win['span_cal'])
    span_cal = max(0, min(span_cal, win_span))
    log_per_day = math.log(max(final, 1.0) / START) / win_span
    mult = math.exp(log_per_day * span_cal)
    dd_scaled = dd * min(1.0, math.sqrt(span_cal / win_span)) if win_span > 0 else dd
    return mult, dd_scaled


def sprint_episode_outcome(sp_win: dict, pidx: int, cal_budget: int) -> tuple[bool, int, float, float]:
    """One fresh-stake sprint episode, capped at cal_budget calendar days (the
    lesser of SPRINT_HORIZON_CAL_DAYS and whatever lifecycle horizon remains
    -- this second cap only binds near the tail of a 5y ladder chain, DESIGN.md
    section 6.1). Returns (doubled, dur_cal, end_mult, dd):
      doubled  : True if 2x first-passage occurs at/before the cap.
      dur_cal  : calendar days actually consumed (<= cal_budget).
      end_mult : end-of-episode value / stake. Exactly 2.0 if doubled
                 (DESIGN.md section 6.2, first-passage-at-2x convention);
                 else the window's own eventual per-cal-day log-return
                 prorated to `dur_cal` days (same technique as
                 core_block_return -- covers both the full-window "ride to
                 window end" fallback and a truncated-by-remaining-horizon
                 partial episode).
      dd       : the window path's own max-dd fraction, used as the
                 episode's worst-point floor (DESIGN.md section 6.2).
    """
    win_span = sp_win['span_cal']
    cap = max(1, min(SPRINT_HORIZON_CAL_DAYS, cal_budget, win_span))
    t2x_bar = sp_win['t2x_bars'][pidx % len(sp_win['t2x_bars'])]
    t2x_cal = bars_to_caldays(t2x_bar)
    final = sp_win['finals'][pidx % len(sp_win['finals'])]
    dd = sp_win['dds'][pidx % len(sp_win['dds'])] if sp_win['dds'] else 0.0

    if t2x_cal is not None and t2x_cal <= cap:
        return True, t2x_cal, 2.0, dd
    log_per_day = math.log(max(final, 1.0) / START) / max(1, win_span)
    end_mult = math.exp(log_per_day * cap)
    return False, cap, end_mult, dd


def _advance(cursor: date, days: int) -> date:
    return date.fromordinal(min(cursor.toordinal() + days, HIST_END.toordinal()))


# ---------------------------------------------------------------------------
# Running (peak, max_dd) bound -- shared by all 3 arms (DESIGN.md section 6.5)
# ---------------------------------------------------------------------------
class DDTracker:
    def __init__(self, start_value: float = START):
        self.peak = start_value
        self.max_dd = 0.0

    def visit(self, value: float):
        self.peak = max(self.peak, value)
        if self.peak > 0:
            self.max_dd = max(self.max_dd, 1.0 - value / self.peak)

    def visit_segment(self, start_value: float, end_value: float, seg_dd_frac: float):
        """A segment whose worst point is approximated as
        start_value * (1 - seg_dd_frac) (DESIGN.md section 6.5)."""
        self.peak = max(self.peak, start_value)
        self.visit(start_value * (1.0 - seg_dd_frac))
        self.visit(end_value)


# ---------------------------------------------------------------------------
# Arm 3a -- core_only
# ---------------------------------------------------------------------------
def simulate_core_only(co_windows: list[dict], start_date: date, horizon_cal: int,
                       rng: random.Random) -> tuple[float, float]:
    eq = START
    tracker = DDTracker(START)
    cursor = start_date
    consumed = 0
    guard = 0
    while consumed < horizon_cal and guard < 30:
        guard += 1
        win = nearest_window(co_windows, cursor)
        span = max(1, min(win['span_cal'], horizon_cal - consumed))
        pidx = rng.randrange(len(win['finals']))
        mult, dd = core_block_return(win, pidx, span)
        start_val = eq
        eq = eq * mult
        tracker.visit_segment(start_val, eq, dd)
        consumed += span
        cursor = _advance(cursor, span)
    return eq, tracker.max_dd


# ---------------------------------------------------------------------------
# Arm 3b -- sprint_rotate_core (the policy under test)
# ---------------------------------------------------------------------------
def simulate_sprint_rotate_core(sp_windows: list[dict], co_windows: list[dict],
                                start_date: date, horizon_cal: int,
                                rng: random.Random) -> tuple[float, float, int]:
    tracker = DDTracker(START)
    sp_win = nearest_window(sp_windows, start_date)
    n = min(len(sp_win['finals']), len(sp_win['t2x_bars']))
    pidx = rng.randrange(n)

    doubled, days_in_sprint, end_mult, sprint_dd = sprint_episode_outcome(sp_win, pidx, horizon_cal)
    rotation_equity = START * end_mult
    tracker.visit_segment(START, rotation_equity, sprint_dd)

    remaining = max(0, horizon_cal - days_in_sprint)
    cursor = _advance(start_date, days_in_sprint)
    eq = rotation_equity
    consumed = 0
    guard = 0
    while consumed < remaining and guard < 30:
        guard += 1
        co_win = nearest_window(co_windows, cursor)
        span = max(1, min(co_win['span_cal'], remaining - consumed))
        pidx2 = rng.randrange(len(co_win['finals']))
        mult, dd = core_block_return(co_win, pidx2, span)
        start_val = eq
        eq = eq * mult
        tracker.visit_segment(start_val, eq, dd)
        consumed += span
        cursor = _advance(cursor, span)

    return eq, tracker.max_dd, days_in_sprint


# ---------------------------------------------------------------------------
# Arm 3c -- ladder_sprint_core (bank-on-double, fresh restake; subsumes
# ladder_vs_core/ladder_sim_episodes.py's Method-3 mechanic)
# ---------------------------------------------------------------------------
def simulate_ladder(sp_windows: list[dict], co_windows: list[dict], start_date: date,
                    horizon_cal: int, rng: random.Random) -> tuple[float, float, int, float]:
    cold = 0.0
    running = START
    tracker = DDTracker(START)
    cursor = start_date
    consumed = 0
    days_in_sprint = 0
    banked = 0.0
    guard = 0
    while consumed < horizon_cal and guard < 200:
        guard += 1
        total = cold + running
        if total <= START * 0.01:
            break   # wiped out
        stake = min(START, total)
        cold_before = total - stake
        running = stake

        sp_win = nearest_window(sp_windows, cursor)
        n = min(len(sp_win['finals']), len(sp_win['t2x_bars']))
        pidx = rng.randrange(n)
        budget = horizon_cal - consumed
        doubled, ep_dur, end_mult, sprint_dd = sprint_episode_outcome(sp_win, pidx, budget)

        co_win = nearest_window(co_windows, cursor)
        pidx2 = rng.randrange(len(co_win['finals']))
        cmult, cdd = core_block_return(co_win, pidx2, ep_dur)
        cold_end = cold_before * cmult

        tracker.visit(cold_before + stake)
        running_trough = stake * (1.0 - sprint_dd)
        cold_trough = cold_before * (1.0 - cdd)
        tracker.visit(cold_trough + running_trough)   # conservative simultaneous-trough bound

        if doubled:
            banked += stake
            cold = cold_end + stake
            running = stake
        else:
            running = stake * end_mult
            cold = cold_end

        days_in_sprint += ep_dur
        consumed += ep_dur
        cursor = _advance(cursor, ep_dur)
        tracker.visit(cold + running)

    return cold + running, tracker.max_dd, days_in_sprint, banked


# ---------------------------------------------------------------------------
# Pooled starts
# ---------------------------------------------------------------------------
def pooled_starts(sp_windows: list[dict], co_windows: list[dict], horizon_cal: int) -> list[date]:
    if not sp_windows or not co_windows:
        return []
    eff_start = max(sp_windows[0]['start'], co_windows[0]['start'])
    eff_end = min(sp_windows[-1]['end'], co_windows[-1]['end'])
    starts = []
    for w in sp_windows:
        s = w['start']
        if s < eff_start:
            continue
        if s + timedelta(days=horizon_cal) <= eff_end:
            starts.append(s)
    return starts


def _covers_covid(start: date, horizon_cal: int) -> bool:
    end = start + timedelta(days=horizon_cal)
    return start <= COVID_END and end >= COVID_START


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        return float('nan')
    i = q / 100.0 * (len(s) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def summarize(finals: list[float], dds: list[float], extra: dict | None = None) -> dict:
    n = len(finals)
    coll = (sum(1 for f in finals if f <= START * COLLAPSE_FRAC) / n * 100.0) if n else float('nan')
    out = {
        'n': n,
        'median': statistics.median(finals) if n else None,
        'p10': pct(finals, 10), 'p25': pct(finals, 25),
        'p75': pct(finals, 75), 'p90': pct(finals, 90),
        'mean': statistics.mean(finals) if n else None,
        'median_return_pct': (statistics.median(finals) / START - 1.0) * 100.0 if n else None,
        'worst_dd_pct': max(dds) * 100.0 if dds else None,
        'med_dd_pct': statistics.median(dds) * 100.0 if dds else None,
        'p90_dd_pct': pct(dds, 90) * 100.0 if dds else None,
        'p_collapse_pct': coll,
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_all_arms(n_iter: int, reps_per_start: int, horizon_cal: int = LIFECYCLE_HORIZON_CAL_DAYS,
                 seed: int = 20260713) -> dict:
    sp_windows = load_panel('sprint', n_iter)
    co_windows = load_panel('core', n_iter)
    if not sp_windows or not co_windows:
        raise SystemExit(f"[policy] no panels found for n_iter={n_iter} -- run panels.py first "
                          f"(sprint={len(sp_windows)} windows, core={len(co_windows)} windows).")

    starts = pooled_starts(sp_windows, co_windows, horizon_cal)
    if not starts:
        raise SystemExit(f"[policy] no pooled start has {horizon_cal}d of runway within the "
                          f"loaded panel coverage -- panels may be incomplete.")
    n_covid = sum(1 for s in starts if _covers_covid(s, horizon_cal))
    print(f"[policy] pooled starts: {len(starts)}  ({n_covid} include 2020-COVID in their "
          f"{horizon_cal}d horizon)  reps/start={reps_per_start}  "
          f"total draws/arm={len(starts) * reps_per_start}", flush=True)

    buckets = {
        'core_only': {'finals': [], 'dds': []},
        'sprint_rotate_core': {'finals': [], 'dds': [], 'days_in_sprint': []},
        'ladder_sprint_core': {'finals': [], 'dds': [], 'days_in_sprint': [], 'banked': []},
    }

    for s in starts:
        slabel = s.isoformat()
        for r in range(reps_per_start):
            rng_co = random.Random(_stable_seed(seed, slabel, r, 'core_only'))
            f_co, dd_co = simulate_core_only(co_windows, s, horizon_cal, rng_co)
            buckets['core_only']['finals'].append(f_co)
            buckets['core_only']['dds'].append(dd_co)

            rng_sr = random.Random(_stable_seed(seed, slabel, r, 'sprint_rotate_core'))
            f_sr, dd_sr, dis_sr = simulate_sprint_rotate_core(sp_windows, co_windows, s,
                                                              horizon_cal, rng_sr)
            buckets['sprint_rotate_core']['finals'].append(f_sr)
            buckets['sprint_rotate_core']['dds'].append(dd_sr)
            buckets['sprint_rotate_core']['days_in_sprint'].append(dis_sr)

            rng_ld = random.Random(_stable_seed(seed, slabel, r, 'ladder_sprint_core'))
            f_ld, dd_ld, dis_ld, bk_ld = simulate_ladder(sp_windows, co_windows, s,
                                                         horizon_cal, rng_ld)
            buckets['ladder_sprint_core']['finals'].append(f_ld)
            buckets['ladder_sprint_core']['dds'].append(dd_ld)
            buckets['ladder_sprint_core']['days_in_sprint'].append(dis_ld)
            buckets['ladder_sprint_core']['banked'].append(bk_ld)

    arms_out = {}
    for arm, b in buckets.items():
        extra = {}
        if 'days_in_sprint' in b:
            dis = b['days_in_sprint']
            extra['median_days_in_sprint'] = statistics.median(dis)
            extra['p90_days_in_sprint'] = pct(dis, 90)
        if 'banked' in b:
            extra['median_banked'] = statistics.median(b['banked'])
            extra['mean_banked'] = statistics.mean(b['banked'])
        arms_out[arm] = summarize(b['finals'], b['dds'], extra)

    core = arms_out['core_only']
    rot = arms_out['sprint_rotate_core']
    lad = arms_out['ladder_sprint_core']
    bars_check = {
        'note': ('Plain arithmetic only -- NOT a verdict. DESIGN.md section 8 bar requires N=500 '
                 'incl 2020-COVID before licensing any Phase-3 decision; this is an N=%d screen.' % n_iter),
        'worst_dd_rotate_le_core': (rot['worst_dd_pct'] is not None and core['worst_dd_pct'] is not None
                                    and rot['worst_dd_pct'] <= core['worst_dd_pct']),
        'median_wealth_rotate_ge_core': (rot['median'] is not None and core['median'] is not None
                                         and rot['median'] >= core['median']),
        'rotate_collapse_pct': rot['p_collapse_pct'],
        'core_collapse_pct': core['p_collapse_pct'],
        'ladder_collapse_pct': lad['p_collapse_pct'],
        'd_worst_dd_pp_rotate_minus_core': (rot['worst_dd_pct'] - core['worst_dd_pct']
                                            if rot['worst_dd_pct'] is not None and core['worst_dd_pct'] is not None
                                            else None),
        'd_median_wealth_pct_rotate_vs_core': ((rot['median'] / core['median'] - 1.0) * 100.0
                                               if rot['median'] and core['median'] else None),
    }

    result = {
        '_meta': {
            'n_iter': n_iter, 'reps_per_start': reps_per_start,
            'horizon_cal_days': horizon_cal, 'sprint_horizon_cal_days': SPRINT_HORIZON_CAL_DAYS,
            'seed': seed, 'n_pooled_starts': len(starts), 'n_pooled_starts_covid': n_covid,
            'pooled_starts': [s.isoformat() for s in starts],
            'n_sprint_windows_loaded': len(sp_windows), 'n_core_windows_loaded': len(co_windows),
            'collapse_frac': COLLAPSE_FRAC, 'start_cash': START,
            'design_doc': 'experiments/lifecycle_mc/DESIGN.md',
        },
        'arms': arms_out,
        'bars_check': bars_check,
    }
    return result


def print_summary(result: dict):
    m = result['_meta']
    print('\n' + '=' * 100)
    print(f"LIFECYCLE MC -- N={m['n_iter']} screen  |  {m['n_pooled_starts']} pooled starts "
          f"({m['n_pooled_starts_covid']} incl COVID)  |  {m['reps_per_start']} reps/start  |  "
          f"horizon={m['horizon_cal_days']}d")
    print('=' * 100)
    hdr = f"  {'arm':<22}{'median $':>14}{'medRet%':>10}{'worstDD%':>10}{'medDD%':>9}{'collapse%':>11}{'medDaysSprint':>15}"
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for arm, s in result['arms'].items():
        dis = s.get('median_days_in_sprint')
        dis_s = f"{dis:>14.0f}" if dis is not None else f"{'n/a':>14}"
        print(f"  {arm:<22}{s['median']:>14,.0f}{s['median_return_pct']:>+9.1f}%"
              f"{s['worst_dd_pct']:>9.1f}%{s['med_dd_pct']:>8.1f}%{s['p_collapse_pct']:>10.1f}%"
              f"{dis_s}")
    print()
    bc = result['bars_check']
    print(f"  BARS CHECK (N={m['n_iter']} screen -- NOT the N=500 gate, see DESIGN.md section 8):")
    print(f"    worst_dd(rotate) <= worst_dd(core_only): {bc['worst_dd_rotate_le_core']}  "
          f"(delta {bc['d_worst_dd_pp_rotate_minus_core']:+.1f}pp)")
    print(f"    median_wealth(rotate) >= median_wealth(core_only): {bc['median_wealth_rotate_ge_core']}  "
          f"(delta {bc['d_median_wealth_pct_rotate_vs_core']:+.1f}%)")
    print(f"    collapse%  core_only={bc['core_collapse_pct']:.2f}  rotate={bc['rotate_collapse_pct']:.2f}  "
          f"ladder={bc['ladder_collapse_pct']:.2f}")
    print(f"  {bc['note']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100, help='engine iterations/window the panels were built at')
    ap.add_argument('--reps-per-start', type=int, default=500,
                    help='resampling replications per pooled start (cheap -- pure python)')
    ap.add_argument('--horizon-days', type=int, default=LIFECYCLE_HORIZON_CAL_DAYS)
    ap.add_argument('--seed', type=int, default=20260713)
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    result = run_all_arms(args.n_iter, args.reps_per_start, args.horizon_days, args.seed)
    print_summary(result)

    out_path = args.out or os.path.join(HERE, 'results', f'screen_n{args.n_iter}.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print(f"\n[policy] wrote {out_path}", flush=True)


if __name__ == '__main__':
    main()
