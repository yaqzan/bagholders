"""P1.A -- E-tier certificates: N=2000 x 12 standard windows, plus N=1000 deep
screens (MC_WINDOW_SET=deep, 4 windows), for up to 4 arms: core, apex_live,
apex_n10, sentinel. Envs come from recipes.py (the single place every runner
in this package gets profile envs from) -- never re-declared here. Pin:
f9fb7b934 (v74, the active scoring version this whole first-week program
certifies against; CLAUDE.md "Active scoring version: v74 (f9fb7b934)").

CERTIFICATE READING RULES (encoded below and in ECERT_SUMMARY.md):
  - Core and Sentinel MUST show p_coll==0 on every one of the 12 standard
    cells. Any nonzero collapse on either is a CERT FAIL flag (prominent --
    these two profiles are supposed to be the collapse-safe reference
    points of the whole portfolio-profile stack).
  - Apex arms (apex_live, apex_n10) report collapse WITH rule-of-three
    bounds, but never hard-fail on it -- Apex is a user-owned sprint budget,
    not a collapse-safe default (CLAUDE.md: "Apex... held continuously is
    negative-compound... a stop-at-2x tool").
  - Deep cells (MC_WINDOW_SET=deep, N=1000) are SCREEN-only, never a gate
    (assessment-backtest.md "Deep-window screens (SCREEN, not GATE)"). The
    known-expected Apex held-form deep-FAIL on dotcom_crash_2000_2002 (and
    partial collapse on gfc_crash_2007_2009 / 2007_now) is annotated as an
    already-documented mechanism, not a new finding (see
    _common.APEX_DEEP_FAIL_ANNOTATION). Core's (and, by the same
    preservation-profile logic, Sentinel's) deep cells are expected to stay
    at p_coll==0; a nonzero reading there is flagged "investigate per SCREEN
    doctrine" (mandatory investigation, never an automatic revert).
  - Every p_coll==0 cell also gets a rule-of-three 95% upper collapse bound
    (~3/N) printed alongside it, e.g. 0/2000 -> <=0.15%.

Cell plan (default, all 4 arms, deep screens included):
    4 arms x 12 standard windows  = 48
    4 arms x  4 deep windows      = 16
    total                          64 cells

Usage (queue-submittable; RUNBOOK.md nights 1-3 run one arm-subset per night):
    python experiments/newbox_rebaseline/run_ecert.py --arms core --cpu 10
    python experiments/newbox_rebaseline/run_ecert.py --arms apex_live,apex_n10 --cpu 10
    python experiments/newbox_rebaseline/run_ecert.py --arms sentinel --cpu 10
    python experiments/newbox_rebaseline/run_ecert.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _common as c  # noqa: E402
import fingerprint  # noqa: E402
import recipes  # noqa: E402

RESULTS_ROOT = HERE / 'results_ecert'
PIN_COMMIT = 'f9fb7b934'   # v74, CLAUDE.md "Active scoring version"
ALL_ARM_NAMES = ('core', 'apex_live', 'apex_n10', 'sentinel')
HARD_FAIL_ARMS = ('core', 'sentinel')   # must be p_coll==0 on every standard cell


def build_cell_plan(arm_names, skip_deep: bool) -> dict:
    """arm -> {'standard': [windows], 'deep': [windows]}."""
    from experiments._mc_pinned_runner import STANDARD_12, DEEP_4
    plan = {}
    for arm in arm_names:
        plan[arm] = {'standard': list(STANDARD_12), 'deep': [] if skip_deep else list(DEEP_4)}
    return plan


def count_cells(plan: dict) -> int:
    return sum(len(v['standard']) + len(v['deep']) for v in plan.values())


def render_markdown(summary: dict) -> str:
    lines = [
        '# ECERT_SUMMARY -- P1.A E-tier certificates',
        '',
        f"Pin: `{summary['pin_commit']}`  N_std: {summary['n_std']}  N_deep: {summary['n_deep']}  "
        f"Generated: {summary['fingerprint']['timestamp_utc']}",
        '',
        c.SCREEN_NOT_GATE_BANNER,
        '',
        '## Certificate rules',
        '',
        '- Core and Sentinel: p_coll==0 required on every standard cell (CERT FAIL flag if not).',
        '- Apex arms (apex_live, apex_n10): collapse reported with rule-of-three bounds, no hard fail.',
        '- Deep cells: SCREEN only. ' + c.APEX_DEEP_FAIL_ANNOTATION,
        '',
        '## Standard cells (N={})'.format(summary['n_std']),
        '',
        '| arm | window | mean_ret | med_ret | worst_dd | mean_dd | p_coll | ro3_bound(95%) | flag |',
        '|---|---|---:|---:|---:|---:|---:|---:|---|',
    ]
    for arm in summary['arms']:
        for label in summary['cell_plan'][arm]['standard']:
            cell = summary['cells'][arm].get(label)
            flag = ''
            if cell is None:
                lines.append(f"| {arm} | {label} | MISSING | | | | | | |")
                continue
            ro3 = c.rule_of_three_bound_pct(summary['n_std']) if cell['p_coll'] == 0 else None
            if arm in HARD_FAIL_ARMS and cell['p_coll'] != 0:
                flag = 'CERT FAIL (p_coll!=0)'
            lines.append(
                f"| {arm} | {label} | {cell['mean_ret']:+.1f} | {cell['med_ret']:+.1f} | "
                f"{cell['worst_dd']:.1f} | {cell['mean_dd']:.1f} | {cell['p_coll']:.2f} | "
                f"{'<=%.2f%%' % ro3 if ro3 is not None else '--'} | {flag} |")
    lines += ['', '## Deep cells (SCREEN only, N={})'.format(summary['n_deep']), '',
              '| arm | window | mean_ret | med_ret | worst_dd | p_coll | flag |',
              '|---|---|---:|---:|---:|---:|---|']
    for arm in summary['arms']:
        for label in summary['cell_plan'][arm]['deep']:
            cell = summary['cells'][arm].get(label)
            if cell is None:
                lines.append(f"| {arm} | {label} | MISSING | | | | |")
                continue
            flag = ''
            if arm == 'core' and cell['p_coll'] != 0:
                flag = 'investigate per SCREEN doctrine'
            elif arm == 'sentinel' and cell['p_coll'] != 0:
                flag = 'investigate per SCREEN doctrine (Sentinel is preservation-tier like Core)'
            elif arm in ('apex_live', 'apex_n10') and cell['p_coll'] != 0:
                flag = 'expected (see annotation above)' if label == 'dotcom_crash_2000_2002' else 'reported, no hard fail'
            lines.append(f"| {arm} | {label} | {cell['mean_ret']:+.1f} | {cell['med_ret']:+.1f} | "
                          f"{cell['worst_dd']:.1f} | {cell['p_coll']:.2f} | {flag} |")
    lines += ['', '## Recipes echoed', '', '```json', json.dumps(summary['recipes'], indent=2), '```',
              '', '## Fingerprint', '', '```json', json.dumps(summary['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--arms', default=','.join(ALL_ARM_NAMES),
                    help='comma subset of core,apex_live,apex_n10,sentinel')
    ap.add_argument('--n-std', type=int, default=2000)
    ap.add_argument('--n-deep', type=int, default=1000)
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--timeout-s', type=int, default=21600)
    ap.add_argument('--skip-deep', action='store_true')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--pin', default=PIN_COMMIT)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    arm_names = [a.strip() for a in args.arms.split(',') if a.strip()]
    unknown = set(arm_names) - set(ALL_ARM_NAMES)
    if unknown:
        raise SystemExit(f"unknown arm(s): {sorted(unknown)} -- valid: {ALL_ARM_NAMES}")

    plan = build_cell_plan(arm_names, args.skip_deep)
    total = count_cells(plan)
    out_dir = RESULTS_ROOT
    for arm in arm_names:
        c.ensure_fresh_or_resume(out_dir / arm, args.resume)

    c.print_plan([
        f"[run_ecert] arms={arm_names}  n_std={args.n_std}  n_deep={args.n_deep} "
        f"(skip_deep={args.skip_deep})  cpu={args.cpu}  pin={args.pin}  total_cells={total}",
    ])

    from experiments._mc_pinned_runner import run_one_window

    cells = {}
    for arm in arm_names:
        env = recipes.ARMS[arm]
        cells[arm] = {}
        for label in plan[arm]['standard']:
            out_json = out_dir / arm / f'{label}.json'
            res = run_one_window(env, label, args.n_std, str(out_json), args.pin,
                                  deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
            cells[arm][label] = res.get(label) if res else None
        for label in plan[arm]['deep']:
            out_json = out_dir / arm / f'{label}.json'
            res = run_one_window(env, label, args.n_deep, str(out_json), args.pin,
                                  deep=True, timeout_s=args.timeout_s, cpu=args.cpu)
            cells[arm][label] = res.get(label) if res else None

    fp = fingerprint.capture(ROOT)
    summary = {
        'pin_commit': args.pin, 'n_std': args.n_std, 'n_deep': args.n_deep,
        'arms': arm_names, 'cell_plan': plan, 'cells': cells,
        'recipes': {a: recipes.ARMS[a] for a in arm_names},
        'fingerprint': fp,
    }

    missing = [f"{a}/{w}" for a in arm_names for w in (plan[a]['standard'] + plan[a]['deep'])
               if cells[a].get(w) is None]
    hard_fails = [f"{a}/{w}" for a in arm_names if a in HARD_FAIL_ARMS
                  for w in plan[a]['standard']
                  if cells[a].get(w) is not None and cells[a][w]['p_coll'] != 0]

    summary['missing_cells'] = missing
    summary['hard_fail_cells'] = hard_fails

    c.write_json(HERE / 'ECERT_SUMMARY.json', summary)
    md = render_markdown(summary)
    c.assert_ascii(md, 'ECERT_SUMMARY.md')
    c.write_text(HERE / 'ECERT_SUMMARY.md', md)

    print(f"\n[run_ecert] {len(missing)} cell(s) missing; {len(hard_fails)} CERT FAIL cell(s) "
          f"(Core/Sentinel p_coll!=0)", flush=True)
    if hard_fails:
        print(f"[run_ecert] CERT FAIL: {hard_fails}", flush=True)
    print(f"[run_ecert] wrote {HERE / 'ECERT_SUMMARY.json'} and ECERT_SUMMARY.md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    # Recipe-equality checks vs canonical sources (delegated to recipes.py).
    print("-- recipes.py cross-checks --")
    recipe_ok = recipes.run_recipe_selftest()
    check('recipes.py cross-checks vs canonical sources', recipe_ok)

    # Rule-of-three arithmetic.
    check('rule_of_three_bound_pct(1000) == 0.3', abs(c.rule_of_three_bound_pct(1000) - 0.3) < 1e-9)
    check('rule_of_three_bound_pct(2000) == 0.15', abs(c.rule_of_three_bound_pct(2000) - 0.15) < 1e-9)
    check('rule_of_three_bound_pct(3) == 100.0', abs(c.rule_of_three_bound_pct(3) - 100.0) < 1e-9)
    try:
        c.rule_of_three_bound_pct(0)
        check('rule_of_three_bound_pct(0) raises', False, 'did not raise')
    except ValueError:
        check('rule_of_three_bound_pct(0) raises', True)

    # Cell plan enumeration counts (4 arms x 12 + 4 arms x 4 = 64 cells default).
    plan_all = build_cell_plan(list(ALL_ARM_NAMES), skip_deep=False)
    check('default cell plan (4 arms, deep included) == 64 cells', count_cells(plan_all) == 64,
          f"got {count_cells(plan_all)}")
    plan_no_deep = build_cell_plan(list(ALL_ARM_NAMES), skip_deep=True)
    check('--skip-deep cell plan == 48 cells', count_cells(plan_no_deep) == 48,
          f"got {count_cells(plan_no_deep)}")
    plan_one_arm = build_cell_plan(['core'], skip_deep=False)
    check('single-arm cell plan == 16 cells (12 std + 4 deep)', count_cells(plan_one_arm) == 16,
          f"got {count_cells(plan_one_arm)}")
    plan_two_arm = build_cell_plan(['apex_live', 'apex_n10'], skip_deep=False)
    check('two-arm cell plan == 32 cells', count_cells(plan_two_arm) == 32,
          f"got {count_cells(plan_two_arm)}")

    check('every ARMS key in recipes.py is a valid --arms token',
          set(recipes.ARMS) == set(ALL_ARM_NAMES), f"recipes.ARMS={sorted(recipes.ARMS)}")

    # Markdown renderer ASCII-safety on a synthetic summary (incl a CERT FAIL row).
    try:
        synth_cell = {'mean_ret': 10.0, 'med_ret': 9.0, 'worst_dd': 40.0, 'mean_dd': 30.0, 'p_coll': 0.0}
        synth_fail_cell = dict(synth_cell); synth_fail_cell['p_coll'] = 5.0
        synth = {
            'pin_commit': PIN_COMMIT, 'n_std': 2000, 'n_deep': 1000, 'arms': ['core'],
            'cell_plan': {'core': {'standard': ['5y'], 'deep': ['dotcom_crash_2000_2002']}},
            'cells': {'core': {'5y': synth_fail_cell, 'dotcom_crash_2000_2002': synth_cell}},
            'recipes': {'core': recipes.CORE_ENV},
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth)
        c.assert_ascii(md, 'synthetic ECERT_SUMMARY.md')
        check('render_markdown() output is ASCII-safe (incl a CERT FAIL row)', True)
        check('render_markdown() flags CERT FAIL for a Core p_coll!=0 cell', 'CERT FAIL' in md)
    except Exception as e:
        check('render_markdown() ASCII-safety / CERT FAIL flag', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
