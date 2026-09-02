"""P1.C-1 -- pessimism-certification robustness matrix, re-driven at N=1000.

Re-runs experiments/pessimism_cert (archived at N=300) at N=1000 into a FRESH
results_pessimism_n1000/ directory -- the original results/ dir at N=300 is
NEVER touched or read-written, only read-referenced for the vs-archived
delta section below.

7 arms (baseline + 6 pessimism knobs, see ARMS below -- re-declared verbatim
from experiments/pessimism_cert/run_cert.py, selftest-checked) x 2 profiles
(core, apex -- envs from recipes.py) x SHIP_VALIDATION_9 windows (the house
"8 canonical + 2020_crash" ship-validation set) = 126 cells.

This is one of the THREE compute-truncated decisions licensed for a power
re-run (gameplan-2026H2-DRAFT.md section 3/RUNBOOK.md "Nights 5-7" -- a
re-run requires a recorded truncation note; nothing else qualifies). Per the
locked spec's own framing: "keep-decision flip adjudication is the
orchestrator's job -- this artifact measures." This script never issues a
keep/revert verdict; it computes deltas and mechanical flags only.

Flags computed (mechanical, not verdicts):
  - any Core-profile cell with p_coll>0                       (hard flag)
  - any (arm != baseline) cell whose worst_dd regresses >5pp   vs the
    arm-local 'baseline' arm at the SAME N=1000, on the 5y/22-now windows
    (T5-style; mirrors experiments/pessimism_cert/collect_matrix.py's own
    COLLAPSE+/DD+Xpp mechanical-flag logic, re-implemented here rather than
    imported since this script's summary JSON has a different shape than
    collect_matrix.py's expected --summary format).
  - deltas vs the ARCHIVED N=300 run (experiments/pessimism_cert/results/
    summary.json), when present -- absence is handled gracefully with a note,
    never an error.

Usage (queue-submittable; RUNBOOK.md night 5 -- bare except --cpu):
    python experiments/newbox_rebaseline/run_pessimism_n1000.py --cpu 10
    python experiments/newbox_rebaseline/run_pessimism_n1000.py --selftest
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

RESULTS_ROOT = HERE / 'results_pessimism_n1000'
ARCHIVED_SUMMARY = ROOT / 'experiments' / 'pessimism_cert' / 'results' / 'summary.json'
PIN_COMMIT = 'f9fb7b934'

# Re-declared verbatim from experiments/pessimism_cert/run_cert.py's ARMS
# (selftest ast-extracts and compares against source).
ARMS = {
    'baseline':           {},
    'slip_sl_03':         {'SLIP_SL_OV': '-0.03'},
    'tp_miss_03':         {'TP_FILL_MISS_P': '0.03'},
    'tp_miss_07':         {'TP_FILL_MISS_P': '0.07'},
    'entry_miss_05':      {'ENTRY_FILL_MISS_P': '0.05'},
    'next_open_anchor':   {'NEXT_OPEN_ANCHOR': '1'},
    'combined_pessimist': {'SLIP_SL_OV': '-0.03', 'TP_FILL_MISS_P': '0.07',
                           'ENTRY_FILL_MISS_P': '0.05', 'NEXT_OPEN_ANCHOR': '1'},
}
PROFILES = {'core': None, 'apex': None}   # filled from recipes.py in main() / _selftest()
DD_FLAG_THRESHOLD_PP = 5.0
FOCUS_WINDOWS = ('5y', '22-now')

_SRC_PESSIMISM_CERT = ROOT / 'experiments' / 'pessimism_cert' / 'run_cert.py'


def _profiles() -> dict:
    return {'core': recipes.CORE_ENV, 'apex': recipes.APEX_LIVE_ENV}


def cell_plan(arm_names, profile_names, windows) -> int:
    return len(arm_names) * len(profile_names) * len(windows)


def render_markdown(summary: dict) -> str:
    lines = [
        '# PESSIMISM_N1000_SUMMARY -- P1.C-1 robustness matrix re-cert',
        '',
        f"N={summary['n_iter']}  Pin: `{summary['pin_commit']}`  "
        f"Generated: {summary['fingerprint']['timestamp_utc']}",
        '',
        'Certification sweep -- these knobs must never become a new shipped default. '
        '"keep-decision flip adjudication is the orchestrator\'s job -- this artifact measures."',
        '',
        '## Hard flags (Core p_coll>0)',
        '',
    ]
    if summary['core_hard_flags']:
        for f in summary['core_hard_flags']:
            lines.append(f"- {f}")
    else:
        lines.append('- none')
    lines += ['', '## DD-regression flags (vs arm-local baseline @ N={}, {} windows, >{}pp)'.format(
        summary['n_iter'], FOCUS_WINDOWS, DD_FLAG_THRESHOLD_PP), '']
    if summary['dd_regress_flags']:
        for f in summary['dd_regress_flags']:
            lines.append(f"- {f}")
    else:
        lines.append('- none')

    lines += ['', '## vs archived N=300 (experiments/pessimism_cert/results/summary.json)', '']
    if summary['archived_available']:
        lines.append('| arm | profile | window | worst_dd (N=1000) | worst_dd (N=300 archived) | delta(pp) |')
        lines.append('|---|---|---|---:|---:|---:|')
        for row in summary['vs_archived_rows']:
            lines.append(f"| {row['arm']} | {row['profile']} | {row['window']} | "
                          f"{row['n1000_worst_dd']:.1f} | {row['n300_worst_dd']:.1f} | {row['delta_pp']:+.1f} |")
    else:
        lines.append('archived N=300 summary.json not found -- section skipped (not an error).')

    lines += ['', '## Fingerprint', '', '```json', json.dumps(summary['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n-iter', type=int, default=1000)
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--timeout-s', type=int, default=10800)
    ap.add_argument('--arms', default=','.join(ARMS), help='comma subset of the 7 arm names')
    ap.add_argument('--profiles', default='core,apex', help='comma subset of core,apex')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--pin', default=PIN_COMMIT)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    arm_names = [a.strip() for a in args.arms.split(',') if a.strip()]
    unknown = set(arm_names) - set(ARMS)
    if unknown:
        raise SystemExit(f"unknown arm(s): {sorted(unknown)} -- valid: {list(ARMS)}")
    profile_names = [p.strip() for p in args.profiles.split(',') if p.strip()]
    unknown_p = set(profile_names) - {'core', 'apex'}
    if unknown_p:
        raise SystemExit(f"unknown profile(s): {sorted(unknown_p)} -- valid: core, apex")

    from experiments._mc_pinned_runner import SHIP_VALIDATION_9
    windows = list(SHIP_VALIDATION_9)
    profiles = _profiles()

    total = cell_plan(arm_names, profile_names, windows)
    for arm in arm_names:
        for profile in profile_names:
            c.ensure_fresh_or_resume(RESULTS_ROOT / arm / profile, args.resume)

    c.print_plan([
        f"[run_pessimism_n1000] n_iter={args.n_iter}  arms={arm_names}  profiles={profile_names}  "
        f"windows={windows}  cpu={args.cpu}  pin={args.pin}  total_cells={total}",
    ])

    from experiments._mc_pinned_runner import run_one_window

    cells = {}
    for arm in arm_names:
        cells[arm] = {}
        for profile in profile_names:
            env = dict(profiles[profile])
            env.update(ARMS[arm])
            cells[arm][profile] = {}
            for label in windows:
                out_json = RESULTS_ROOT / arm / profile / f'{label}.json'
                res = run_one_window(env, label, args.n_iter, str(out_json), args.pin,
                                      deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
                cells[arm][profile][label] = res.get(label) if res else None

    # --- Hard flag: any Core cell with p_coll>0 ---
    core_hard_flags = []
    if 'core' in profile_names:
        for arm in arm_names:
            for label in windows:
                cell = cells[arm]['core'].get(label)
                if cell is not None and cell['p_coll'] != 0:
                    core_hard_flags.append(f"{arm}/core/{label}: p_coll={cell['p_coll']}")

    # --- DD-regression flags: vs arm-local baseline @ N=1000, focus windows only ---
    dd_regress_flags = []
    if 'baseline' in arm_names:
        for arm in arm_names:
            if arm == 'baseline':
                continue
            for profile in profile_names:
                base_cells = cells['baseline'][profile]
                for label in FOCUS_WINDOWS:
                    if label not in windows:
                        continue
                    b = base_cells.get(label)
                    a = cells[arm][profile].get(label)
                    if b is None or a is None:
                        continue
                    d_dd = a['worst_dd'] - b['worst_dd']
                    if d_dd > DD_FLAG_THRESHOLD_PP:
                        dd_regress_flags.append(
                            f"{arm}/{profile}/{label}: worst_dd {b['worst_dd']:.1f} -> {a['worst_dd']:.1f} "
                            f"({d_dd:+.1f}pp vs baseline)")
                    if b['p_coll'] == 0 and a['p_coll'] > 0:
                        dd_regress_flags.append(
                            f"{arm}/{profile}/{label}: COLLAPSE+ (baseline p_coll=0, this arm p_coll={a['p_coll']})")
    else:
        print("[run_pessimism_n1000] 'baseline' arm not in this run -- skipping arm-local DD-regression "
              "flags (need a zero-pessimism reference row to diff against).", flush=True)

    # --- vs archived N=300 ---
    archived_available = ARCHIVED_SUMMARY.exists()
    vs_archived_rows = []
    if archived_available:
        archived = c.load_json(ARCHIVED_SUMMARY)
        archived_cells = archived.get('cells', {})
        for arm in arm_names:
            for profile in profile_names:
                for label in windows:
                    n1000_cell = cells[arm][profile].get(label)
                    n300_cell = archived_cells.get(arm, {}).get(profile, {}).get(label)
                    if n1000_cell is None or n300_cell is None:
                        continue
                    vs_archived_rows.append({
                        'arm': arm, 'profile': profile, 'window': label,
                        'n1000_worst_dd': n1000_cell['worst_dd'], 'n300_worst_dd': n300_cell['worst_dd'],
                        'delta_pp': n1000_cell['worst_dd'] - n300_cell['worst_dd'],
                    })
    else:
        print(f"[run_pessimism_n1000] archived {ARCHIVED_SUMMARY} not found -- vs-N=300 section "
              f"will note absence gracefully (not an error).", flush=True)

    fp = fingerprint.capture(ROOT)
    summary = {
        'n_iter': args.n_iter, 'pin_commit': args.pin, 'arms': arm_names, 'profiles': profile_names,
        'windows': windows, 'cells': cells, 'core_hard_flags': core_hard_flags,
        'dd_regress_flags': dd_regress_flags, 'archived_available': archived_available,
        'vs_archived_rows': vs_archived_rows, 'fingerprint': fp,
    }
    c.write_json(HERE / 'PESSIMISM_N1000_SUMMARY.json', summary)
    md = render_markdown(summary)
    c.assert_ascii(md, 'PESSIMISM_N1000_SUMMARY.md')
    c.write_text(HERE / 'PESSIMISM_N1000_SUMMARY.md', md)

    print(f"\n[run_pessimism_n1000] {len(core_hard_flags)} Core hard-flag(s), "
          f"{len(dd_regress_flags)} DD-regression flag(s)", flush=True)
    print(f"[run_pessimism_n1000] wrote {HERE / 'PESSIMISM_N1000_SUMMARY.json'} and .md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    # Recipe equality via recipes.py
    print("-- recipes.py cross-checks --")
    check('recipes.py cross-checks vs canonical sources', recipes.run_recipe_selftest())

    # Source-equality of the re-declared ARMS dict vs pessimism_cert/run_cert.py.
    try:
        src = _SRC_PESSIMISM_CERT.read_text(encoding='utf-8')
        canonical_arms = c.extract_dict_literal(src, 'ARMS')
        check('ARMS == pessimism_cert/run_cert.py:ARMS', canonical_arms == ARMS,
              f"diff: {_dict_diff(canonical_arms, ARMS)}")
    except Exception as e:
        check('ARMS == pessimism_cert/run_cert.py:ARMS', False, str(e))

    # Arm/profile/window enumeration = 126 cells.
    from experiments._mc_pinned_runner import SHIP_VALIDATION_9
    total = cell_plan(list(ARMS), ['core', 'apex'], SHIP_VALIDATION_9)
    check('default enumeration (7 arms x 2 profiles x 9 windows) == 126 cells', total == 126,
          f"got {total}")
    check('SHIP_VALIDATION_9 has 9 windows', len(SHIP_VALIDATION_9) == 9, f"got {len(SHIP_VALIDATION_9)}")

    # _profiles() returns exactly core/apex, sourced from recipes.py.
    profs = _profiles()
    check("_profiles() returns keys {'core', 'apex'}", set(profs) == {'core', 'apex'})
    check("_profiles()['core'] is recipes.CORE_ENV", profs['core'] == recipes.CORE_ENV)
    check("_profiles()['apex'] is recipes.APEX_LIVE_ENV", profs['apex'] == recipes.APEX_LIVE_ENV)

    # Markdown renderer ASCII-safety + flag rendering.
    try:
        synth = {
            'n_iter': 1000, 'pin_commit': PIN_COMMIT, 'arms': ['baseline', 'combined_pessimist'],
            'profiles': ['core'], 'windows': ['5y'],
            'cells': {}, 'core_hard_flags': ['combined_pessimist/core/5y: p_coll=0.5'],
            'dd_regress_flags': ['combined_pessimist/core/5y: worst_dd 40.0 -> 47.0 (+7.0pp vs baseline)'],
            'archived_available': False, 'vs_archived_rows': [],
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth)
        c.assert_ascii(md, 'synthetic PESSIMISM_N1000_SUMMARY.md')
        check('render_markdown() output is ASCII-safe', True)
        check('render_markdown() surfaces the hard-flag line', 'p_coll=0.5' in md)
        check('render_markdown() notes archived absence gracefully',
              'not found' in md or 'not an error' in md or 'skipped' in md)
    except Exception as e:
        check('render_markdown() ASCII-safety / flag rendering', False, str(e))

    return ok


def _dict_diff(a: dict, b: dict) -> dict:
    keys = set(a) | set(b)
    return {k: (a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)}


if __name__ == '__main__':
    raise SystemExit(main())
