#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fast RESAMPLING simulator: "fast-2x ladder" capital-management scheme vs
pure-Core hold, over 5y and 10y horizons.

PORTFOLIO-STAGE research. No ship, no version bump, no scoring change.

THE SCHEME (user's idea)
------------------------
Start $50k on the Apex SPRINT (flat_n4_a25: 4x25% calls-only, v74 base, barriers
TP+30/SL-70/HOLD-27/dead-hold). When a sprint DOUBLES ($50k -> $100k), split off
$50k into COLD STORAGE and keep $50k running a FRESH sprint; repeat every time
the running tranche doubles. Cold-store grows per its destination:
  (a) CASH      = 0% growth (frozen)
  (b) SENTINEL  = safe compounder (~3,371%/10y, 37%/5y DD)   [MODELLED]
  (c) CORE      = the long-run compounder (5y +1247.9% median, 53-62% DD)

Compare the laddered scheme's terminal-wealth + drawdown distribution to PURE
CORE (the whole $50k held in Core for the period).

METHODOLOGY — regime correlation preserved (NO i.i.d. shuffle)
-------------------------------------------------------------
Data = per-calendar-window MC (N=500 paths/window) from
experiments/concentration_2x/results/stage3_gate/. Episodes are NOT i.i.d.-
shuffled (a 2022/23 grind = a run of consecutive sprint failures; i.i.d.
resampling understates that clustering). We sequence by CALENDAR BLOCKS:

  M1  SEQUENTIAL-CALENDAR forward walk (PREFERRED, gold standard for ordering):
      walk the REAL calendar order. 5y = 2021->2022->2023->2024->2025; 10y =
      two observed cycles (2020-2024 x2; 2016-19 tape absent from stage3). Each
      year-block draws ONE path index, SHARED across sprint + cold-store assets
      so they experience the SAME regime that year. Sprint within-year doubling +
      restart is modelled from that year's t2x first-passage.

  M2  STATIONARY year-block bootstrap (robustness / CI): draw calendar-year
      blocks WITH REPLACEMENT to fill the horizon — preserves WITHIN-year regime,
      randomises year ORDER. Quantifies how path-specific M1 is to 2020-2025.

  PURE-CORE 5y HEADLINE also reported from the DIRECT continuous-hold 5y window
  (baseline_apex '5y', N=500) — the most faithful Core number, with NO block
  chaining. 10y has no continuous data on disk (flagged).

CAVEAT — block chaining vs continuous hold: chaining independent yearly draws
breaks WITHIN-replication serial correlation (a single continuous 5y path has
the same path running through all years). We therefore anchor pure-Core 5y to the
DIRECT continuous window and treat the chained pure-Core as the apples-to-apples
control for the chained ladder (both use the same chaining). Differences between
chained-core and direct-core bound the chaining artifact.

FAILURE TAIL — modelled honestly
--------------------------------
~30% of fresh 2y sprints never double; a stuck sprint has NEGATIVE drift
(held-sprint 5y median ~ -37%). The running tranche follows its drawn path and
ERODES; a double can still happen in a later block. NO "give up" by default;
--giveup-frac parks an eroded tranche to cold-store for sensitivity.

DRAWDOWN — total-portfolio
--------------------------
DD is on the TOTAL portfolio (cold-store + running sprint). As cold-store grows,
the running sprint's deep (~75%) DD is diluted to its share of total wealth — the
de-risking the ladder is designed to deliver. We bound DD CONSERVATIVELY by
combining each year's cold-store trough with the running-tranche trough at the
SAME (worst) instant (an upper bound; real troughs need not coincide).
"""
from __future__ import annotations
import argparse
import json
import math
import random
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE3 = HERE.parent / 'concentration_2x' / 'results' / 'stage3_gate'
START = 50_000.0
TWO_X = 100_000.0
COLLAPSE_FRAC = 0.20  # total portfolio <= 20% of $50k start = ruin

YEAR_BLOCKS = ['2020', '2021', '2022', '2023', '2024', '2025']
CYCLE_5Y = ['2021', '2022', '2023', '2024', '2025']
CYCLE_10Y = ['2020', '2021', '2022', '2023', '2024',
             '2020', '2021', '2022', '2023', '2024']


# ---------------------------------------------------------------------------
def load_year_panels():
    sprint = json.loads((STAGE3 / 'flat_n4_a25.json').read_text())
    core = json.loads((STAGE3 / 'baseline_apex.json').read_text())
    panels = {}
    for y in YEAR_BLOCKS:
        sp = sprint[y]['paths']
        co = core[y]['paths']
        panels[y] = {
            'n_td': sp['n_trading_days'],
            'sprint_finals': sp['finals'],
            'sprint_t2x': sp['t2x_bars'],
            'sprint_dds': sp['dds'],
            'core_finals': co['finals'],
            'core_dds': co['dds'],
        }
    # direct continuous-hold windows for pure-core headline
    panels['_direct'] = {
        'core_5y_finals': core['5y']['paths']['finals'],
        'core_5y_dds': core['5y']['paths']['dds'],
        'core_22now_finals': core['22-now']['paths']['finals'],
        'core_22now_dds': core['22-now']['paths']['dds'],
        'sprint_5y_finals': sprint['5y']['paths']['finals'],
        'sprint_5y_dds': sprint['5y']['paths']['dds'],
    }
    return panels


# ---------------------------------------------------------------------------
def sentinel_block_returns(panels):
    """Per-year Sentinel block return multipliers (MODELLED — no Sentinel dump).

    Sentinel = 85+-only / 30%-cap preservation (doc: 10y +3,371%, 5y 37% DD).
    We DAMPEN Core's per-year path log-returns by factor k so the 5y (2021-2025)
    geometric compound matches Sentinel's documented ~+3,371%/10y profile, and we
    DAMPEN Core's per-year DDs by the same k toward Sentinel's ~37%/5y DD. Aligned
    to Core path index so a shared draw shares the regime. APPROXIMATION (flagged).
    """
    core_year_geo = {}
    for y in YEAR_BLOCKS:
        finals = panels[y]['core_finals']
        logs = [math.log(max(f, 1.0) / START) for f in finals]
        core_year_geo[y] = sum(logs) / len(logs)
    core_5y_log = sum(core_year_geo[y] for y in CYCLE_5Y)
    sent_per_yr_log = math.log(34.71) / 10.0   # +3371%/10y
    sent_5y_log = sent_per_yr_log * 5.0
    k = sent_5y_log / core_5y_log if abs(core_5y_log) > 1e-9 else 0.5
    k = max(0.0, min(1.0, k))
    ret = {}
    dd = {}
    for y in YEAR_BLOCKS:
        finals = panels[y]['core_finals']
        dds = panels[y]['core_dds']
        ret[y] = [math.exp(math.log(max(f, 1.0) / START) * k) for f in finals]
        # compress DD: Sentinel DD ~ k * core DD (preservation profile)
        dd[y] = [d * k for d in dds]
    return ret, dd, k


# ---------------------------------------------------------------------------
def sprint_year_outcome(panels, year, path_idx, rng):
    """Run a $50k-normalised sprint tranche through one calendar-year block with
    bank-on-double + immediate fresh restart WITHIN the year.

    Returns:
      banked_norm       : multiples of $50k banked this year (0,1,2,...)
      running_end_norm  : end-of-year value of the running tranche / $50k base...
                          NO — value in DOLLARS on a $50k base (i.e. final tranche
                          $ for a tranche that started the year at $50k)
      trough_mult       : worst running-tranche level this year as a fraction of
                          the tranche's START-OF-YEAR value (for DD; <=1)
    """
    sp_t2x = panels[year]['sprint_t2x']
    sp_finals = panels[year]['sprint_finals']
    sp_dds = panels[year]['sprint_dds']
    n_td = panels[year]['n_td']
    npaths = len(sp_t2x)

    banked = 0.0
    bar = 0
    running = START
    cur_idx = path_idx
    # worst running-tranche level relative to its OWN start-of-year value.
    # Before the first double, start-of-year value = $50k. After a double the
    # tranche resets to $50k and continues; the deepest DD any single tranche
    # episode reaches is ~its path max_dd. We track the min level reached on the
    # CURRENT tranche relative to $50k (tranche base), which is what the caller
    # scales by the actual tranche $ to combine with cold-store.
    trough_level = 1.0
    guard = 0
    while bar < n_td and guard < 40:
        guard += 1
        t2x = sp_t2x[cur_idx]
        final = sp_finals[cur_idx]
        dd = sp_dds[cur_idx]
        remaining = n_td - bar
        if t2x is None or t2x > remaining:
            frac = remaining / n_td
            end_log = math.log(max(final, 1.0) / START) * frac
            running = START * math.exp(end_log)
            # this (final, non-doubling) tranche's worst level
            trough_level = min(trough_level, 1.0 - dd)
            bar = n_td
            break
        # double fits
        # this tranche reached 2x; its pre-double trough is bounded by its dd
        # (the dd field is the path's MAX dd over the whole year; for a doubler the
        # pre-2x portion may have a shallower dd, so 1-dd is a conservative floor)
        trough_level = min(trough_level, 1.0 - dd)
        bar += t2x
        banked += START
        running = START
        cur_idx = rng.randrange(npaths)
    return banked, running, trough_level


# ---------------------------------------------------------------------------
def run_ladder_rep(panels, year_seq, cold_dest, sent_ret, sent_dd, rng,
                   giveup_frac=None):
    """ONE ladder replication along year_seq. Returns (terminal, max_dd, banked).

    Total-portfolio DD: at each year we know cold-store start/end and its DD, and
    the running tranche start/end and its trough. We bound the worst total level
    that year by (cold_trough + running_trough) and compare to the running peak.
    """
    cold = 0.0
    running = START
    peak_total = START
    max_dd = 0.0
    total_banked = 0.0

    for year in year_seq:
        npaths_sp = len(panels[year]['sprint_t2x'])
        npaths_co = len(panels[year]['core_finals'])
        # shared regime draw: same index into the (equal-length N=500) panels
        pidx = rng.randrange(min(npaths_sp, npaths_co))

        # ALWAYS-FRESH-$50k re-stake (default, artifact-free): the at-risk sprint
        # stake is topped up to min($50k, total) from cold each year, so the
        # fresh-$50k within-year t2x logic always applies correctly. (The legacy
        # multiplicative carry is dropped — applying a fresh-$50k t2x to an eroded
        # tranche over-banked late doubles.)
        total_now = cold + running
        tranche_start = min(START, total_now)
        cold = total_now - tranche_start
        cold_start = cold                            # $ at year open (post re-stake)
        peak_total = max(peak_total, cold_start + tranche_start)

        banked_norm, running_end_norm, trough_level = \
            sprint_year_outcome(panels, year, pidx, rng)
        scale = tranche_start / START
        banked_year = banked_norm * scale
        running = running_end_norm * scale
        running_trough = tranche_start * trough_level   # worst running $ this yr
        total_banked += banked_year

        # cold-store growth on the balance present at YEAR START; freshly banked
        # $ is added at year end (no growth same-year — conservative).
        if cold_dest == 'cash':
            cold_end = cold_start
            cold_dd = 0.0
        elif cold_dest == 'core':
            cmult = max(panels[year]['core_finals'][pidx], 1.0) / START
            cold_end = cold_start * cmult
            cold_dd = panels[year]['core_dds'][pidx]
        elif cold_dest == 'sentinel':
            cold_end = cold_start * sent_ret[year][pidx]
            cold_dd = sent_dd[year][pidx]
        cold_trough = cold_start * (1.0 - cold_dd)

        # --- DD bound: worst simultaneous total level this year ---
        # (conservative: assumes cold + running troughs coincide)
        worst_total = cold_trough + running_trough
        if peak_total > 0:
            max_dd = max(max_dd, 1.0 - worst_total / peak_total)

        # commit year-end balances; freshly banked added now
        cold = cold_end + banked_year

        # optional give-up (capital-NEUTRAL): if the running tranche has eroded
        # below giveup_frac of $50k, MOVE its remaining value to cold-store
        # (lock in the survival), then RE-FUND a fresh $50k sprint by pulling
        # $50k BACK OUT of cold-store (only if cold can cover it). No capital is
        # fabricated. If cold < $50k, fund whatever is available (the running
        # tranche shrinks). This models "stop bleeding a dead sprint, restart a
        # fresh one from your safe pile".
        if giveup_frac is not None and running < START * giveup_frac:
            cold += running          # park the eroded tranche to safety
            refund = min(START, cold)
            cold -= refund
            running = refund

        total_end = cold + running
        peak_total = max(peak_total, total_end)
        if peak_total > 0:
            max_dd = max(max_dd, 1.0 - total_end / peak_total)

    return cold + running, max_dd, total_banked


# ---------------------------------------------------------------------------
def run_core_rep_chained(panels, year_seq, rng):
    eq = START
    peak = START
    max_dd = 0.0
    for year in year_seq:
        npaths = len(panels[year]['core_finals'])
        pidx = rng.randrange(npaths)
        cmult = max(panels[year]['core_finals'][pidx], 1.0) / START
        dd = panels[year]['core_dds'][pidx]
        peak = max(peak, eq)
        max_dd = max(max_dd, 1.0 - eq * (1.0 - dd) / peak if peak > 0 else 0.0)
        eq *= cmult
        peak = max(peak, eq)
        max_dd = max(max_dd, 1.0 - eq / peak if peak > 0 else 0.0)
    return eq, max_dd


# ---------------------------------------------------------------------------
def boot_year_seq(n_years, rng):
    return [rng.choice(YEAR_BLOCKS) for _ in range(n_years)]


# ---------------------------------------------------------------------------
def pct(vals, q):
    s = sorted(vals)
    if not s:
        return float('nan')
    i = q / 100.0 * (len(s) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def summarize(finals, dds, banked=None):
    coll = sum(1 for f in finals if f <= START * COLLAPSE_FRAC) / len(finals) * 100.0
    out = {
        'median': statistics.median(finals),
        'p10': pct(finals, 10), 'p25': pct(finals, 25),
        'p75': pct(finals, 75), 'p90': pct(finals, 90),
        'mean': statistics.mean(finals),
        'max_dd': max(dds) * 100.0, 'med_dd': statistics.median(dds) * 100.0,
        'p90_dd': pct(dds, 90) * 100.0, 'p10_dd': pct(dds, 10) * 100.0,
        'p_collapse': coll,
    }
    if banked is not None:
        out['median_banked'] = statistics.median(banked)
        out['mean_banked'] = statistics.mean(banked)
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-reps', type=int, default=40000)
    ap.add_argument('--seed', type=int, default=12345)
    ap.add_argument('--giveup-frac', type=float, default=None)
    ap.add_argument('--out', default='results/ladder_vs_core_results.json')
    args = ap.parse_args()

    panels = load_year_panels()
    sent_ret, sent_dd, sent_k = sentinel_block_returns(panels)

    horizons = {'5y': CYCLE_5Y, '10y': CYCLE_10Y}
    dests = ['cash', 'sentinel', 'core']

    results = {'_meta': {
        'n_reps': args.n_reps, 'seed': args.seed, 'start_cash': START,
        'two_x': TWO_X, 'collapse_frac': COLLAPSE_FRAC,
        'sentinel_compression_k': sent_k, 'giveup_frac': args.giveup_frac,
        'methodology': ('M1 sequential-calendar forward walk (regime-ordered, '
                        'PREFERRED); M2 stationary year-block bootstrap '
                        '(within-yr regime, randomised order). NO i.i.d. shuffle. '
                        'pure-core 5y also reported DIRECT from continuous-hold '
                        '5y window (no chaining).'),
        'year_blocks': YEAR_BLOCKS, 'cycle_5y': CYCLE_5Y, 'cycle_10y_M1': CYCLE_10Y,
        'sentinel_note': 'Sentinel block returns + DDs MODELLED by compressing '
                         'Core per-year log-returns/DDs by k to match doc '
                         '+3371%/10y & ~37%/5y DD. APPROXIMATION.',
        'dd_note': 'Total-portfolio DD, conservative simultaneous-trough bound '
                   '(cold trough + running trough at same instant).',
    }}

    # ---- direct continuous-hold pure-core (5y) + held-sprint (5y) anchors ----
    d = panels['_direct']
    results['_direct_anchors'] = {
        'pure_core_5y_continuous': summarize(d['core_5y_finals'], d['core_5y_dds']),
        'pure_core_22now_continuous': summarize(d['core_22now_finals'],
                                                d['core_22now_dds']),
        'held_sprint_5y_continuous': summarize(d['sprint_5y_finals'],
                                               d['sprint_5y_dds']),
    }

    for method in ['M1_sequential', 'M2_blockboot']:
        results[method] = {}
        for hz, seq in horizons.items():
            n_years = len(seq)
            results[method][hz] = {}
            for dest in dests:
                rng = random.Random(args.seed + hash((method, hz, dest)) % 100000)
                fin, dd, bk = [], [], []
                for _ in range(args.n_reps):
                    ys = seq if method == 'M1_sequential' else boot_year_seq(n_years, rng)
                    t, x, b = run_ladder_rep(panels, ys, dest, sent_ret, sent_dd,
                                             rng, args.giveup_frac)
                    fin.append(t); dd.append(x); bk.append(b)
                results[method][hz][f'ladder_{dest}'] = summarize(fin, dd, bk)
            rng = random.Random(args.seed + hash((method, hz, 'core')) % 100000)
            fin, dd = [], []
            for _ in range(args.n_reps):
                ys = seq if method == 'M1_sequential' else boot_year_seq(n_years, rng)
                t, x = run_core_rep_chained(panels, ys, rng)
                fin.append(t); dd.append(x)
            results[method][hz]['pure_core_chained'] = summarize(fin, dd)

    outp = HERE / args.out
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(results, indent=2))

    # ---------------- print ----------------
    def f(v):
        if v != v:
            return '   n/a'
        if abs(v) >= 1e6:
            return f'{v/1e6:8.2f}M'
        return f'{v:9,.0f}'

    print('=' * 120)
    print('LADDER (fast-2x) vs PURE-CORE  |  terminal wealth from $50,000  '
          f'(N={args.n_reps:,} reps; Sentinel k={sent_k:.3f})')
    print('PORTFOLIO-STAGE | v74 Apex sleeve | calls-only | SPREAD_TILT OFF | '
          'asymmetric exec cost (baked into source data)')
    print('=' * 120)

    a = results['_direct_anchors']
    print('\n### DIRECT continuous-hold anchors (N=500, no chaining — ground truth)')
    print(f"  pure-core 5y      median ${a['pure_core_5y_continuous']['median']:>12,.0f}  "
          f"maxDD {a['pure_core_5y_continuous']['max_dd']:.0f}%  "
          f"medDD {a['pure_core_5y_continuous']['med_dd']:.0f}%")
    print(f"  pure-core 22-now  median ${a['pure_core_22now_continuous']['median']:>12,.0f}  "
          f"maxDD {a['pure_core_22now_continuous']['max_dd']:.0f}%")
    print(f"  held-sprint 5y    median ${a['held_sprint_5y_continuous']['median']:>12,.0f}  "
          f"maxDD {a['held_sprint_5y_continuous']['max_dd']:.0f}%  "
          f"(the negative-drift held sprint = why you MUST ladder it)")

    for method in ['M1_sequential', 'M2_blockboot']:
        tag = ('M1 sequential-calendar (regime-ordered; PREFERRED)'
               if method == 'M1_sequential'
               else 'M2 year-block bootstrap (within-yr regime, randomised order)')
        print(f'\n### {tag}')
        for hz in ['5y', '10y']:
            print(f'\n  [{hz}]')
            hdr = (f"  {'scheme':<20}{'median':>11}{'P10':>11}{'P25':>11}"
                   f"{'P75':>11}{'P90':>11}{'medDD%':>8}{'p90DD%':>8}{'maxDD%':>8}"
                   f"{'ruin%':>7}{'bank(med)':>11}")
            print(hdr); print('  ' + '-' * (len(hdr) - 2))
            row = results[method][hz]
            for scheme in ['ladder_cash', 'ladder_sentinel', 'ladder_core',
                           'pure_core_chained']:
                s = row[scheme]; bk = s.get('median_banked')
                print(f"  {scheme:<20}{f(s['median']):>11}{f(s['p10']):>11}"
                      f"{f(s['p25']):>11}{f(s['p75']):>11}{f(s['p90']):>11}"
                      f"{s['med_dd']:>7.1f}%{s['p90_dd']:>7.1f}%{s['max_dd']:>7.1f}%"
                      f"{s['p_collapse']:>6.1f}%"
                      f"{(f(bk) if bk is not None else '     -'):>11}")

    print('\n' + '=' * 120)
    print('HEADLINE VERDICT (M1 sequential-calendar; pure-core 5y vs DIRECT anchor)')
    m1 = results['M1_sequential']
    pc5_direct = a['pure_core_5y_continuous']
    for hz in ['5y', '10y']:
        pcc = m1[hz]['pure_core_chained']
        lc = m1[hz]['ladder_core']; lcash = m1[hz]['ladder_cash']
        lsent = m1[hz]['ladder_sentinel']
        anchor = pc5_direct if hz == '5y' else pcc
        anc_label = 'direct' if hz == '5y' else 'chained'
        print(f'\n[{hz}] median wealth (vs pure-core {anc_label} '
              f'${anchor["median"]:,.0f}, medDD {anchor["med_dd"]:.0f}%):')
        for nm, s in [('ladder->cash', lcash), ('ladder->sentinel', lsent),
                      ('ladder->core', lc)]:
            print(f'   {nm:<16} ${s["median"]:>12,.0f} '
                  f'({s["median"]/anchor["median"]:.2f}x median)  '
                  f'medDD {s["med_dd"]:.0f}% (p90 {s["p90_dd"]:.0f}%)  '
                  f'ruin {s["p_collapse"]:.1f}%')
    print(f'\nwrote {outp}')


if __name__ == '__main__':
    main()
