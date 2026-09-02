#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Glide-path policy composer (DESIGN.md sections 3/5/6/7/10).

Loads the {core, sentinel} (and {core_noddsb, sentinel_noddsb}) quarterly 2y
panels and composes all 24 pre-registered cells per panel set via pure-Python
resampling. No MC, no MySQL. Renders NO verdict -- the bars_check block is
plain arithmetic against DESIGN.md section 10, same discipline as
experiments/lifecycle_mc/policy.py.

Reuses lifecycle_mc's composer primitives via IMPORT (nearest_window,
core_block_return, DDTracker, pct, _stable_seed, _advance, pooled_starts,
_covers_covid) -- the only new mechanics are: the running-max ratchet + hard/
blend sentinel weight, the ceiling-aware dollar split (section 6.2), the
late-DD-dollars tracker (section 7.2), and the transition diagnostics +
conditional secondary read (sections 7.3/7.4).

Validation arm (mandatory per traps.md): core_only_cap50k must reproduce
experiments/lifecycle_mc/results/screen_n100.json's core_only stats EXACTLY
(same panels, same seed machinery, same block chaining) -- checked and
reported every run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.lifecycle_mc.policy import (  # noqa: E402
    nearest_window, core_block_return, DDTracker, pct, _stable_seed, _advance,
    pooled_starts, _covers_covid, START as PANEL_START, COLLAPSE_FRAC,
    LIFECYCLE_HORIZON_CAL_DAYS,
)
from experiments.lifecycle_mc.panels import build_grid, PANELS_DIR as LIFECYCLE_PANELS_DIR  # noqa: E402
from experiments.glide_path.envs import SENTINEL_CEILING  # noqa: E402

GLIDE_PANELS_DIR = os.path.join(HERE, 'results', 'panels')
LIFECYCLE_SCREEN_JSON = os.path.join(os.path.dirname(LIFECYCLE_PANELS_DIR), 'screen_n100.json')

CAPS = [25_000.0, 50_000.0, 100_000.0]
THRESHOLDS = [500_000.0, 1_000_000.0, 2_000_000.0]
SHAPES = ['hard', 'blend']
BAND_FRAC = 0.20          # DESIGN.md section 3b.1, FABLE ruling 5
SUBSTEP_DAYS = 30         # in-band blend update quantum (DESIGN.md section 6 disclosure)

_TLAB = {500_000.0: '500k', 1_000_000.0: '1M', 2_000_000.0: '2M'}
_CLAB = {25_000.0: '25k', 50_000.0: '50k', 100_000.0: '100k'}


# ---------------------------------------------------------------------------
# Panel loading -- parametrized variant of lifecycle_mc.policy.load_panel
# (that one is hardwired to lifecycle's own PANELS_DIR; body kept identical).
# ---------------------------------------------------------------------------
def load_panel_from(panels_root: str, arm: str) -> list[dict]:
    grid = build_grid()
    arm_dir = os.path.join(panels_root, arm)
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
            'starting_cash': paths.get('starting_cash') or PANEL_START,
        })
    if n_missing:
        print(f"[glide-policy] WARNING: {arm} panel missing/empty {n_missing}/{len(grid)} "
              f"cells under {panels_root} -- proceeding with {len(out)}.", flush=True)
    out.sort(key=lambda w: w['start'])
    return out


# ---------------------------------------------------------------------------
# Glide mechanics (DESIGN.md sections 3b.1 / 6.1 / 6.2)
# ---------------------------------------------------------------------------
def sentinel_weight(run_max: float, threshold: float, shape: str,
                    band_frac: float = BAND_FRAC) -> float:
    """Sentinel weight p from RUNNING-MAX equity (ratchet: monotone non-
    decreasing because run_max is)."""
    if shape == 'hard':
        return 1.0 if run_max >= threshold else 0.0
    lo = (1.0 - band_frac) * threshold
    hi = (1.0 + band_frac) * threshold
    if run_max <= lo:
        return 0.0
    if run_max >= hi:
        return 1.0
    return (run_max - lo) / (hi - lo)


def crossing_day(e0: float, rate_per_day: float, level: float, max_days: int) -> int | None:
    """First integer day d in [1, max_days] with e0*exp(rate*d) >= level, under
    the uniform-per-day log-return seam. 0 if already at/above. None if never
    within max_days."""
    if e0 >= level:
        return 0
    if rate_per_day <= 0.0:
        return None
    d = math.ceil(math.log(level / e0) / rate_per_day - 1e-12)
    if d < 1:
        d = 1
    return d if d <= max_days else None


def _leg_rate(win: dict, pidx: int) -> tuple[float, float, int]:
    """(per-day log rate, window max-dd fraction, window span) for path pidx
    (mod len -- shared regime-pairing index, DESIGN.md section 6 item 3)."""
    n = len(win['finals'])
    i = pidx % n
    final = win['finals'][i]
    dd = win['dds'][i % len(win['dds'])] if win['dds'] else 0.0
    wspan = max(1, win['span_cal'])
    r = math.log(max(final, 1.0) / PANEL_START) / wspan
    return r, dd, wspan


def _blended_step(eq: float, p: float, r_co: float, r_se: float, step: int,
                  ceiling: float | None) -> float:
    """One step of the p-blended book (DESIGN.md section 3b.1): Core leg on
    full equity; Sentinel leg on min(eq, ceiling) with the excess idle at 0%
    (section 6.2, mirrors portfolio_engine's base_value = min(equity, ceiling))."""
    co_end = eq * math.exp(r_co * step)
    if ceiling and ceiling > 0:
        dep = min(eq, ceiling)
        se_end = dep * math.exp(r_se * step) + max(0.0, eq - ceiling)
    else:
        se_end = eq * math.exp(r_se * step)
    return (1.0 - p) * co_end + p * se_end


class DollarDDTracker:
    """Largest peak-to-trough DOLLAR drawdown over the visited sequence
    (DESIGN.md section 7.2 late-DD-dollars; activated at reference crossing)."""

    def __init__(self):
        self.peak = None
        self.max_dd_dollars = 0.0

    def visit(self, v: float):
        if self.peak is None or v > self.peak:
            self.peak = v
        else:
            self.max_dd_dollars = max(self.max_dd_dollars, self.peak - v)


# ---------------------------------------------------------------------------
# Static comparator (core_only / sentinel_only) -- block loop kept EXACTLY
# rng-equivalent to lifecycle_mc.policy.simulate_core_only (one randrange per
# block, same span rule) so core_only_cap50k bit-matches lifecycle's stored
# screen. Additions (ceiling split, per-ref crossing/late-DD) consume NO rng.
# ---------------------------------------------------------------------------
def simulate_static(windows: list[dict], start_date: date, horizon_cal: int,
                    rng: random.Random, start_cash: float, ceiling: float | None,
                    refs: list[float]) -> tuple[float, float, dict]:
    eq = start_cash
    run_max = eq
    tracker = DDTracker(eq)
    ref_state = {T: {'day': None, 'ldd': DollarDDTracker()} for T in refs}
    for T in refs:
        if eq >= T:
            ref_state[T]['day'] = 0
            ref_state[T]['ldd'].visit(eq)
    cursor = start_date
    consumed = 0
    guard = 0
    while consumed < horizon_cal and guard < 30:
        guard += 1
        win = nearest_window(windows, cursor)
        span = max(1, min(win['span_cal'], horizon_cal - consumed))
        pidx = rng.randrange(len(win['finals']))
        mult, dd = core_block_return(win, pidx, span)
        start_val = eq
        if ceiling and ceiling > 0:
            dep = min(eq, ceiling)
            eq = dep * mult + max(0.0, eq - ceiling)
        else:
            eq = eq * mult
        tracker.visit_segment(start_val, eq, dd)
        end_val = eq
        r_eff = (math.log(end_val / start_val) / span
                 if (end_val > 0 and start_val > 0) else 0.0)
        for T in refs:
            st = ref_state[T]
            if st['day'] is None:
                if end_val >= T and run_max < T:
                    if r_eff > 0:
                        dstar = max(1, min(span, math.ceil(math.log(T / start_val) / r_eff)))
                    else:
                        dstar = span
                    st['day'] = consumed + dstar
                    st['ldd'].visit(T)
                    post_dd = dd * math.sqrt(max(0.0, span - dstar) / span) if span > 0 else 0.0
                    st['ldd'].visit(T * (1.0 - post_dd))
                    st['ldd'].visit(end_val)
            else:
                st['ldd'].visit(start_val)
                st['ldd'].visit(start_val * (1.0 - dd))
                st['ldd'].visit(end_val)
        run_max = max(run_max, end_val)
        consumed += span
        cursor = _advance(cursor, span)
    refs_out = {T: (ref_state[T]['day'],
                    ref_state[T]['ldd'].max_dd_dollars if ref_state[T]['day'] is not None else None)
                for T in refs}
    return eq, tracker.max_dd, refs_out


# ---------------------------------------------------------------------------
# The policy under test (DESIGN.md section 3b)
# ---------------------------------------------------------------------------
def simulate_glide(core_windows: list[dict], sent_windows: list[dict], start_date: date,
                   horizon_cal: int, rng: random.Random, start_cash: float,
                   threshold: float, shape: str, ceiling: float | None = SENTINEL_CEILING,
                   band_frac: float = BAND_FRAC,
                   substep_days: int = SUBSTEP_DAYS) -> tuple[float, float, int | None, float | None]:
    eq = start_cash
    run_max = eq
    tracker = DDTracker(eq)
    ldd = DollarDDTracker()
    day_to_t = None
    if run_max >= threshold:
        day_to_t = 0
        ldd.visit(eq)
    cursor = start_date
    consumed = 0
    guard = 0
    while consumed < horizon_cal and guard < 60:
        guard += 1
        co = nearest_window(core_windows, cursor)
        se = nearest_window(sent_windows, cursor)
        span = max(1, min(co['span_cal'], se['span_cal'], horizon_cal - consumed))
        pidx = rng.randrange(1_000_000)   # one shared regime-pairing index per block
        r_co, dd_co, co_span = _leg_rate(co, pidx)
        r_se, dd_se, se_span = _leg_rate(se, pidx)
        day = 0
        while day < span:
            p = sentinel_weight(run_max, threshold, shape, band_frac)
            rem = span - day
            if p <= 0.0:
                # pure Core; next structural event = band entry (blend) / T (hard)
                level = threshold if shape == 'hard' else (1.0 - band_frac) * threshold
                dstar = crossing_day(eq, r_co, level, rem)
                step = rem if dstar is None else max(1, dstar)
            elif p >= 1.0:
                step = rem                 # fully migrated; ride the block out
            else:
                step = min(substep_days, rem)   # in-band: monthly p updates
            start_val = eq
            eq = _blended_step(eq, p, r_co, r_se, step, ceiling)
            seg_dd = ((1.0 - p) * dd_co * math.sqrt(step / co_span)
                      + p * dd_se * math.sqrt(step / se_span))
            tracker.visit_segment(start_val, eq, seg_dd)
            if day_to_t is not None:
                ldd.visit(start_val)
                ldd.visit(start_val * (1.0 - seg_dd))
                ldd.visit(eq)
            run_max = max(run_max, eq)
            if day_to_t is None and run_max >= threshold:
                day_to_t = consumed + day + step
                ldd.visit(eq)
            day += step
        consumed += span
        cursor = _advance(cursor, span)
    return eq, tracker.max_dd, day_to_t, (ldd.max_dd_dollars if day_to_t is not None else None)


# ---------------------------------------------------------------------------
# Metrics (DESIGN.md section 7; conditional secondary per FABLE ruling 2a)
# ---------------------------------------------------------------------------
def cell_summary(start_cash: float, finals: list[float], dds: list[float],
                 refdata: dict, extra: dict | None = None) -> dict:
    n = len(finals)
    coll = (sum(1 for f in finals if f <= start_cash * COLLAPSE_FRAC) / n * 100.0) if n else None
    med = statistics.median(finals) if n else None
    out = {
        'n': n, 'start_cash': start_cash,
        'median': med, 'p10': pct(finals, 10), 'p25': pct(finals, 25),
        'p75': pct(finals, 75), 'p90': pct(finals, 90),
        'mean': statistics.mean(finals) if n else None,
        'median_multiple': (med / start_cash) if med else None,
        'worst_dd_pct': max(dds) * 100.0 if dds else None,
        'med_dd_pct': statistics.median(dds) * 100.0 if dds else None,
        'p90_dd_pct': pct(dds, 90) * 100.0 if dds else None,
        'p_collapse_pct': coll,
    }
    refs_out = {}
    for T, d in refdata.items():
        crossed_idx = [i for i, x in enumerate(d['days']) if x is not None]
        days = [d['days'][i] for i in crossed_idx]
        n_c = len(crossed_idx)
        ref = {
            'pct_crossed': (n_c / n * 100.0) if n else None,
            'n_crossed': n_c,
            'med_days_to_T': statistics.median(days) if days else None,
            'p90_days_to_T': pct(days, 90) if days else None,
        }
        if n_c >= 30:
            cf = [finals[i] for i in crossed_idx]
            cl = [d['ldd'][i] for i in crossed_idx if d['ldd'][i] is not None]
            ref['conditional'] = {
                'n': n_c,
                'median_terminal': statistics.median(cf),
                'p10_terminal': pct(cf, 10),
                'median_late_dd_dollars': statistics.median(cl) if cl else None,
                'max_late_dd_dollars': max(cl) if cl else None,
            }
        else:
            ref['conditional'] = 'SKIPPED_N_LT_30'
        refs_out[_TLAB[T]] = ref
    out['refs'] = refs_out
    if extra:
        out.update(extra)
    return out


def _ref_late_max(cell: dict, tlab: str):
    ref = cell['refs'].get(tlab)
    if not ref or ref['conditional'] == 'SKIPPED_N_LT_30':
        return None
    return ref['conditional']['max_late_dd_dollars']


def bars_check(cells: dict, n_iter: int) -> dict:
    """Plain arithmetic against DESIGN.md section 10 -- NOT a verdict."""
    rows = {}
    for T in THRESHOLDS:
        tlab = _TLAB[T]
        for shape in SHAPES:
            for cap in CAPS:
                clab = _CLAB[cap]
                gname = f'glide_T{tlab}_{shape}_cap{clab}'
                g = cells[gname]
                c = cells[f'core_only_cap{clab}']
                s = cells[f'sentinel_only_cap{clab}']
                g_late = _ref_late_max(g, tlab)
                c_late = _ref_late_max(c, tlab)
                s_late = _ref_late_max(s, tlab)
                rows[gname] = {
                    'glide_median': g['median'], 'core_median': c['median'],
                    'sentinel_median': s['median'],
                    'd_median_vs_core_pct': ((g['median'] / c['median'] - 1.0) * 100.0
                                             if g['median'] and c['median'] else None),
                    'd_median_vs_sentinel_pct': ((g['median'] / s['median'] - 1.0) * 100.0
                                                 if g['median'] and s['median'] else None),
                    'median_ge_core': (g['median'] >= c['median']
                                       if g['median'] is not None and c['median'] is not None else None),
                    'glide_worst_dd_pct': g['worst_dd_pct'], 'core_worst_dd_pct': c['worst_dd_pct'],
                    'glide_late_dd_max': g_late, 'core_late_dd_max': c_late,
                    'sentinel_late_dd_max': s_late,
                    'late_dd_le_core': (g_late <= c_late
                                        if g_late is not None and c_late is not None else None),
                    'glide_collapse_pct': g['p_collapse_pct'],
                    'pct_crossed_T': g['refs'][tlab]['pct_crossed'],
                }
    return {
        'note': (f'Plain arithmetic only -- NOT a verdict. N={n_iter} SCREEN tier; under the '
                 f'section-10 spend cap (FABLE ruling 2c) escalation requires a glide cell '
                 f'beating its static comparator outside screen noise on the primary bar.'),
        'rows': rows,
    }


# ---------------------------------------------------------------------------
# Composition driver
# ---------------------------------------------------------------------------
PANELSETS = {
    'primary': {'core': (LIFECYCLE_PANELS_DIR, 'core'), 'sentinel': (GLIDE_PANELS_DIR, 'sentinel')},
    'noddsb': {'core': (GLIDE_PANELS_DIR, 'core_noddsb'),
               'sentinel': (GLIDE_PANELS_DIR, 'sentinel_noddsb')},
}


def compose_panelset(setname: str, n_iter: int, reps: int, horizon_cal: int, seed: int) -> dict | None:
    spec = PANELSETS[setname]
    co = load_panel_from(*spec['core'])
    se = load_panel_from(*spec['sentinel'])
    if not co or not se:
        print(f"[glide-policy] panelset '{setname}': panels unavailable "
              f"(core={len(co)}, sentinel={len(se)}) -- skipped.", flush=True)
        return None
    starts = pooled_starts(co, se, horizon_cal)
    if not starts:
        print(f"[glide-policy] panelset '{setname}': no pooled start has {horizon_cal}d runway.",
              flush=True)
        return None
    n_covid = sum(1 for s in starts if _covers_covid(s, horizon_cal))
    print(f"[glide-policy] panelset={setname}  pooled starts={len(starts)} ({n_covid} incl COVID)  "
          f"reps/start={reps}  draws/cell={len(starts) * reps}", flush=True)

    cells = {}
    # -- statics ------------------------------------------------------------
    for prof, windows, ceil in (('core_only', co, None), ('sentinel_only', se, SENTINEL_CEILING)):
        for cap in CAPS:
            finals, dds = [], []
            refdata = {T: {'days': [], 'ldd': []} for T in THRESHOLDS}
            for s in starts:
                sl = s.isoformat()
                for r_i in range(reps):
                    rng = random.Random(_stable_seed(seed, sl, r_i, prof))
                    f, dd, refs_out = simulate_static(windows, s, horizon_cal, rng, cap, ceil,
                                                      THRESHOLDS)
                    finals.append(f)
                    dds.append(dd)
                    for T in THRESHOLDS:
                        dday, lval = refs_out[T]
                        refdata[T]['days'].append(dday)
                        refdata[T]['ldd'].append(lval)
            cells[f'{prof}_cap{_CLAB[cap]}'] = cell_summary(cap, finals, dds, refdata)
    # -- glide cells ----------------------------------------------------------
    for T in THRESHOLDS:
        for shape in SHAPES:
            for cap in CAPS:
                cname = f'glide_T{_TLAB[T]}_{shape}_cap{_CLAB[cap]}'
                finals, dds, days_l, ldd_l = [], [], [], []
                for s in starts:
                    sl = s.isoformat()
                    for r_i in range(reps):
                        rng = random.Random(_stable_seed(seed, sl, r_i, cname))
                        f, dd, dday, lval = simulate_glide(co, se, s, horizon_cal, rng, cap,
                                                           T, shape)
                        finals.append(f)
                        dds.append(dd)
                        days_l.append(dday)
                        ldd_l.append(lval)
                cells[cname] = cell_summary(cap, finals, dds,
                                            {T: {'days': days_l, 'ldd': ldd_l}})
    return {
        'starts': [s.isoformat() for s in starts], 'n_covid_starts': n_covid,
        'cells': cells, 'bars_check': bars_check(cells, n_iter),
    }


def validate_core_only(cells: dict, tol: float = 1e-9) -> dict:
    """core_only_cap50k must reproduce lifecycle_mc's stored screen exactly."""
    out = {'reference': LIFECYCLE_SCREEN_JSON, 'checked': False}
    if not os.path.exists(LIFECYCLE_SCREEN_JSON):
        out['note'] = 'lifecycle screen JSON not found -- validation skipped.'
        return out
    with open(LIFECYCLE_SCREEN_JSON, 'r', encoding='utf-8') as f:
        ref = json.load(f)['arms']['core_only']
    mine = cells.get('core_only_cap50k')
    if not mine:
        out['note'] = 'core_only_cap50k cell missing.'
        return out
    keys = ['median', 'p10', 'p25', 'p75', 'p90', 'mean', 'worst_dd_pct', 'p_collapse_pct']
    diffs = {}
    ok = True
    for k in keys:
        a, b = mine.get(k), ref.get(k)
        if a is None or b is None:
            diffs[k] = 'missing'
            ok = False
            continue
        rel = abs(a - b) / max(1e-12, abs(b))
        diffs[k] = rel
        if rel > tol:
            ok = False
    out.update({'checked': True, 'exact_match': ok, 'rel_diffs': diffs})
    return out


def print_cells(cells: dict, title: str):
    print('\n' + '=' * 110)
    print(title)
    print('=' * 110)
    hdr = (f"  {'cell':<28}{'median $':>12}{'p10 $':>11}{'worstDD%':>10}"
           f"{'coll%':>7}{'crossed%':>10}{'lateDDmax $':>13}")
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for name, c in cells.items():
        if name.startswith('glide_'):
            tlab = name.split('_')[1][1:]
            ref = c['refs'][tlab]
        else:
            ref = None
        crossed = f"{ref['pct_crossed']:>9.1f}%" if ref else f"{'n/a':>10}"
        late = _ref_late_max(c, tlab) if ref else None
        late_s = f"{late:>12,.0f}" if late is not None else f"{'n/a':>12}"
        print(f"  {name:<28}{c['median']:>12,.0f}{c['p10']:>11,.0f}{c['worst_dd_pct']:>9.1f}%"
              f"{c['p_collapse_pct']:>6.1f}%{crossed}{late_s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-iter', type=int, default=100)
    ap.add_argument('--reps-per-start', type=int, default=500)
    ap.add_argument('--horizon-days', type=int, default=LIFECYCLE_HORIZON_CAL_DAYS)
    ap.add_argument('--seed', type=int, default=20260713)
    ap.add_argument('--validate-only', action='store_true',
                    help='compose core_only cells only (needs just the lifecycle core panel) '
                         'and check the bit-match against lifecycle screen_n100.json')
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    if args.validate_only:
        co = load_panel_from(LIFECYCLE_PANELS_DIR, 'core')
        if not co:
            raise SystemExit('[glide-policy] no lifecycle core panels found.')
        starts = pooled_starts(co, co, args.horizon_days)
        cells = {}
        for cap in [50_000.0]:
            finals, dds = [], []
            refdata = {T: {'days': [], 'ldd': []} for T in THRESHOLDS}
            for s in starts:
                sl = s.isoformat()
                for r_i in range(args.reps_per_start):
                    rng = random.Random(_stable_seed(args.seed, sl, r_i, 'core_only'))
                    f, dd, refs_out = simulate_static(co, s, args.horizon_days, rng, cap, None,
                                                      THRESHOLDS)
                    finals.append(f)
                    dds.append(dd)
                    for T in THRESHOLDS:
                        dday, lval = refs_out[T]
                        refdata[T]['days'].append(dday)
                        refdata[T]['ldd'].append(lval)
            cells[f'core_only_cap{_CLAB[cap]}'] = cell_summary(cap, finals, dds, refdata)
        v = validate_core_only(cells)
        print(json.dumps(v, indent=2))
        if not v.get('exact_match'):
            raise SystemExit(1)
        print('[glide-policy] VALIDATION PASS -- core_only_cap50k reproduces lifecycle exactly.')
        return

    result = {'_meta': {
        'n_iter': args.n_iter, 'reps_per_start': args.reps_per_start,
        'horizon_cal_days': args.horizon_days, 'seed': args.seed,
        'band_frac': BAND_FRAC, 'substep_days': SUBSTEP_DAYS,
        'sentinel_ceiling': SENTINEL_CEILING, 'collapse_frac': COLLAPSE_FRAC,
        'caps': CAPS, 'thresholds': THRESHOLDS, 'shapes': SHAPES,
        'design_doc': 'experiments/glide_path/DESIGN.md',
    }}
    primary = compose_panelset('primary', args.n_iter, args.reps_per_start,
                               args.horizon_days, args.seed)
    if primary is None:
        raise SystemExit('[glide-policy] primary panelset unavailable -- build panels first.')
    result['primary'] = primary
    result['validation'] = validate_core_only(primary['cells'])

    noddsb = compose_panelset('noddsb', args.n_iter, args.reps_per_start,
                              args.horizon_days, args.seed)
    if noddsb is not None:
        result['noddsb'] = noddsb
        # Ablation flag comparison (DESIGN.md section 8): does any section-10
        # arithmetic flag differ between the DDSB-on and DDSB-off panelsets?
        comp = {}
        for gname, prow in primary['bars_check']['rows'].items():
            nrow = noddsb['bars_check']['rows'].get(gname)
            if not nrow:
                continue
            comp[gname] = {
                'primary_flags': {'median_ge_core': prow['median_ge_core'],
                                  'late_dd_le_core': prow['late_dd_le_core']},
                'noddsb_flags': {'median_ge_core': nrow['median_ge_core'],
                                 'late_dd_le_core': nrow['late_dd_le_core']},
                'flags_match': (prow['median_ge_core'] == nrow['median_ge_core']
                                and prow['late_dd_le_core'] == nrow['late_dd_le_core']),
                'd_median_vs_core_pct_primary': prow['d_median_vs_core_pct'],
                'd_median_vs_core_pct_noddsb': nrow['d_median_vs_core_pct'],
            }
        result['ablation_compare'] = comp

    print_cells(primary['cells'], f"GLIDE PATH -- screen N={args.n_iter} -- PRIMARY panelset "
                                  f"(shipped DD_SOFT_BAND on)")
    if noddsb is not None:
        print_cells(noddsb['cells'], f"GLIDE PATH -- screen N={args.n_iter} -- NODDSB ablation "
                                     f"panelset (DD_SOFT_BAND forced off)")
        n_mismatch = sum(1 for r in result['ablation_compare'].values() if not r['flags_match'])
        print(f"\n  ABLATION: {n_mismatch}/{len(result['ablation_compare'])} glide cells change a "
              f"section-10 arithmetic flag when DD_SOFT_BAND is forced off.")
    v = result['validation']
    print(f"\n  VALIDATION vs lifecycle screen: "
          f"{'EXACT MATCH' if v.get('exact_match') else 'MISMATCH -- investigate before reading'}")

    out_path = args.out or os.path.join(HERE, 'results', f'screen_n{args.n_iter}.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print(f"\n[glide-policy] wrote {out_path}", flush=True)


if __name__ == '__main__':
    main()
