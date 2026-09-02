"""
P2.A step 4 -- assemble the A/B/C survivorship decomposition tables.

Reads:
  A: experiments/data_ingest/survivor_baseline_pre_sharadar/deep_crash_screen/summary.json
     (frozen 2026-07-29; run 2026-07-19 on this box, contaminated conventions,
     survivor-only universe)
  B: results/B/summary.json  (clean conventions, survivor-only 811 universe)
  C: results/C/summary.json  (clean conventions, full PIT universe)

Falls back to per-window JSONs when an arm's summary.json is missing (e.g.
report built mid-run). Emits:
  results/decomposition.json  -- every metric for every (profile, window) with
                                 A, B, C, d_AB (=B-A), d_BC (=C-B)
  stdout                      -- the per-profile markdown tables FINDINGS.md embeds

Deltas are signed so the reader never re-derives direction:
  d_AB = B - A  (substrate-repair effect, on the survivor universe)
  d_BC = C - B  (survivorship effect, on the clean substrate)
For worst_dd/mean_dd a POSITIVE d_BC means the honest universe draws DEEPER
drawdown (the expected direction of the survivorship discount).
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
A_SUMMARY = os.path.join(ROOT, 'experiments', 'data_ingest',
                         'survivor_baseline_pre_sharadar', 'deep_crash_screen',
                         'summary.json')
RESULTS = os.path.join(HERE, 'results')

PROFILES = ['core', 'apex']
DEEP_4 = ['ltcm_1998', 'dotcom_crash_2000_2002', 'gfc_crash_2007_2009', '2007_now']
STANDARD_12 = ['2018', '2020', '2020_crash', '2021', '2022', '2023', '2024',
               'dip', '22-now', '2025', '5y', '10y']
WINDOWS = DEEP_4 + STANDARD_12
METRICS = ['worst_dd', 'mean_dd', 'p_coll', 'mean_ret', 'med_ret']


def _load_arm_cells(arm: str) -> dict:
    """-> {profile: {window: metrics-dict-or-None}}"""
    if arm == 'A':
        with open(A_SUMMARY, 'r', encoding='utf-8') as f:
            return json.load(f)['cells']
    summary = os.path.join(RESULTS, arm, 'summary.json')
    if os.path.exists(summary):
        with open(summary, 'r', encoding='utf-8') as f:
            return json.load(f)['cells']
    cells = {}
    for p in PROFILES:
        cells[p] = {}
        for w in WINDOWS:
            path = os.path.join(RESULTS, arm, p, f'{w}.json')
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    cells[p][w] = json.load(f).get(w)
            else:
                cells[p][w] = None
    return cells


def _fmt(v, nd=1):
    return '--' if v is None else f'{v:.{nd}f}'


def _delta(b, a, nd=1):
    if a is None or b is None:
        return '--'
    d = b - a
    return f'{d:+.{nd}f}'


def main():
    arms = {arm: _load_arm_cells(arm) for arm in ['A', 'B', 'C']}

    decomp = {}
    for p in PROFILES:
        decomp[p] = {}
        for w in WINDOWS:
            row = {}
            for m in METRICS:
                a = (arms['A'][p].get(w) or {}).get(m)
                b = (arms['B'][p].get(w) or {}).get(m)
                c = (arms['C'][p].get(w) or {}).get(m)
                row[m] = {
                    'A': a, 'B': b, 'C': c,
                    'd_AB': None if (a is None or b is None) else b - a,
                    'd_BC': None if (b is None or c is None) else c - b,
                }
            decomp[p][w] = row

    out = os.path.join(RESULTS, 'decomposition.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'design': 'A=contaminated+survivor-only (frozen 2026-07-29, run 2026-07-19); '
                      'B=clean+survivor-only-811; C=clean+full-PIT. '
                      'd_AB=B-A=substrate repair; d_BC=C-B=survivorship.',
            'n_iter': 300,
            'pinned_version': 'f9fb7b934 (v74, id 74)',
            'decomposition': decomp,
        }, f, indent=2)
    print(f'wrote {out}\n')

    for p in PROFILES:
        print(f'### {p.upper()} -- worst_dd (pp) decomposition')
        print('| window | A | B | C | A->B (repair) | B->C (survivorship) | p_coll A/B/C |')
        print('|---|---|---|---|---|---|---|')
        for w in WINDOWS:
            r = decomp[p][w]
            dd = r['worst_dd']
            pc = r['p_coll']
            print(f"| {w} | {_fmt(dd['A'])} | {_fmt(dd['B'])} | {_fmt(dd['C'])} "
                  f"| {_delta(dd['B'], dd['A'])} | {_delta(dd['C'], dd['B'])} "
                  f"| {_fmt(pc['A'], 3)}/{_fmt(pc['B'], 3)}/{_fmt(pc['C'], 3)} |")
        print()
        print(f'### {p.upper()} -- med_ret (%) decomposition')
        print('| window | A | B | C | A->B (repair) | B->C (survivorship) |')
        print('|---|---|---|---|---|---|')
        for w in WINDOWS:
            r = decomp[p][w]
            mr = r['med_ret']
            print(f"| {w} | {_fmt(mr['A'])} | {_fmt(mr['B'])} | {_fmt(mr['C'])} "
                  f"| {_delta(mr['B'], mr['A'])} | {_delta(mr['C'], mr['B'])} |")
        print()


if __name__ == '__main__':
    main()
