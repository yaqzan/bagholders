"""P1.C-2 -- deep crash screens at N=1000 (SCREEN stays SCREEN).

Re-drives experiments/deep_crash_screen (archived at N=300) at N=1000, but
ONLY over the 4 DEEP_4 windows (ltcm_1998, dotcom_crash_2000_2002,
gfc_crash_2007_2009, 2007_now) -- not the full 16-label (12 standard + 4
deep) sweep the original archived run did, since the standard-12 cells at
higher N are already covered by run_ecert.py's certificates. Profiles: core,
apex_live (envs from recipes.py). Pin: f9fb7b934. Fresh results dir:
results_deep_n1000/.

2 profiles x 4 deep windows = 8 cells.

Doctrine (assessment-backtest.md "Deep-window screens (SCREEN, not GATE)"):
these windows are survivor-only (riding the 1995 v74 backfill) and are NEVER
a calibration/tuning/ship target. A deep FAIL is a mandatory mechanism
investigation, never an automatic revert; a deep PASS is weak comfort, never
collapse-proof. The known-expected Apex held-form deep-FAIL on
dotcom_crash_2000_2002 (100% collapse at the archived N=300) is annotated as
an already-documented, already-mitigated mechanism -- see
_common.APEX_DEEP_FAIL_ANNOTATION.

Compares against the archived experiments/deep_crash_screen/results/
summary.json (N=300) when present -- absence handled gracefully.

Usage (queue-submittable; RUNBOOK.md night 6 -- bare except --cpu):
    python experiments/newbox_rebaseline/run_deep_screen_n1000.py --cpu 10
    python experiments/newbox_rebaseline/run_deep_screen_n1000.py --selftest
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

RESULTS_ROOT = HERE / 'results_deep_n1000'
ARCHIVED_SUMMARY = ROOT / 'experiments' / 'deep_crash_screen' / 'results' / 'summary.json'
PIN_COMMIT = 'f9fb7b934'
DEFAULT_PROFILES = ('core', 'apex_live')
# The archived N=300 deep_crash_screen summary keys its Apex-held arm 'apex';
# the fresh runs key the comparable arm 'apex_live' (recipes.APEX_LIVE_ENV).
ARCHIVED_PROFILE_KEYS = {'apex_live': 'apex'}


def _profiles() -> dict:
    return {'core': recipes.CORE_ENV, 'apex_live': recipes.APEX_LIVE_ENV}


def cell_plan(profile_names, deep_windows) -> int:
    return len(profile_names) * len(deep_windows)


def render_markdown(summary: dict) -> str:
    lines = [
        '# DEEP_N1000_SUMMARY -- P1.C-2 deep crash screens at N=1000',
        '',
        f"N={summary['n_iter']}  Pin: `{summary['pin_commit']}`  "
        f"Generated: {summary['fingerprint']['timestamp_utc']}",
        '',
        c.SCREEN_NOT_GATE_BANNER,
        '',
        c.APEX_DEEP_FAIL_ANNOTATION,
        '',
        '## Cells',
        '',
        '| profile | window | mean_ret | med_ret | worst_dd | p_coll | vs N=300 archived p_coll | flag |',
        '|---|---|---:|---:|---:|---:|---:|---|',
    ]
    for profile in summary['profiles']:
        for label in summary['deep_windows']:
            cell = summary['cells'][profile].get(label)
            if cell is None:
                lines.append(f"| {profile} | {label} | MISSING | | | | | |")
                continue
            archived_key = ARCHIVED_PROFILE_KEYS.get(profile, profile)
            archived_cell = summary['archived_cells'].get(archived_key, {}).get(label) if summary['archived_available'] else None
            archived_p_coll = f"{archived_cell['p_coll']:.1f}" if archived_cell else '--'
            flag = ''
            if profile == 'core' and cell['p_coll'] != 0:
                flag = 'investigate per SCREEN doctrine'
            elif profile == 'apex_live' and cell['p_coll'] != 0:
                flag = 'expected (dot-com held-form)' if label == 'dotcom_crash_2000_2002' else 'reported, no hard fail'
            lines.append(f"| {profile} | {label} | {cell['mean_ret']:+.1f} | {cell['med_ret']:+.1f} | "
                          f"{cell['worst_dd']:.1f} | {cell['p_coll']:.1f} | {archived_p_coll} | {flag} |")
    lines += ['', '## Fingerprint', '', '```json', json.dumps(summary['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n-iter', type=int, default=1000)
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--timeout-s', type=int, default=21600,
                    help='per-cell cap; 2007_now (19y) and dotcom_crash_2000_2002 (2.5y) are the '
                         'likely long poles (see the archived N=300 run''s own docstring note)')
    ap.add_argument('--profiles', default=','.join(DEFAULT_PROFILES))
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--pin', default=PIN_COMMIT)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    profile_names = [p.strip() for p in args.profiles.split(',') if p.strip()]
    unknown = set(profile_names) - set(DEFAULT_PROFILES)
    if unknown:
        raise SystemExit(f"unknown profile(s): {sorted(unknown)} -- valid: {DEFAULT_PROFILES}")

    from experiments._mc_pinned_runner import DEEP_4
    profiles = _profiles()
    total = cell_plan(profile_names, DEEP_4)
    for profile in profile_names:
        c.ensure_fresh_or_resume(RESULTS_ROOT / profile, args.resume)

    c.print_plan([
        f"[run_deep_screen_n1000] n_iter={args.n_iter}  profiles={profile_names}  "
        f"deep_windows={DEEP_4}  cpu={args.cpu}  pin={args.pin}  total_cells={total}",
    ])

    from experiments._mc_pinned_runner import run_one_window

    cells = {}
    for profile in profile_names:
        env = profiles[profile]
        cells[profile] = {}
        for label in DEEP_4:
            out_json = RESULTS_ROOT / profile / f'{label}.json'
            res = run_one_window(env, label, args.n_iter, str(out_json), args.pin,
                                  deep=True, timeout_s=args.timeout_s, cpu=args.cpu)
            cells[profile][label] = res.get(label) if res else None

    archived_available = ARCHIVED_SUMMARY.exists()
    archived_cells = {}
    if archived_available:
        archived = c.load_json(ARCHIVED_SUMMARY)
        archived_cells = archived.get('deep_windows_only') or archived.get('cells', {})
    else:
        print(f"[run_deep_screen_n1000] archived {ARCHIVED_SUMMARY} not found -- vs-N=300 column "
              f"will note absence gracefully (not an error).", flush=True)

    fp = fingerprint.capture(ROOT)
    summary = {
        'n_iter': args.n_iter, 'pin_commit': args.pin, 'profiles': profile_names,
        'deep_windows': list(DEEP_4), 'cells': cells,
        'archived_available': archived_available, 'archived_cells': archived_cells,
        'fingerprint': fp,
    }
    missing = [f"{p}/{w}" for p in profile_names for w in DEEP_4 if cells[p].get(w) is None]
    core_flags = [f"core/{w}" for w in DEEP_4
                  if 'core' in profile_names and cells.get('core', {}).get(w) is not None
                  and cells['core'][w]['p_coll'] != 0]
    summary['missing_cells'] = missing
    summary['core_investigate_flags'] = core_flags

    c.write_json(HERE / 'DEEP_N1000_SUMMARY.json', summary)
    md = render_markdown(summary)
    c.assert_ascii(md, 'DEEP_N1000_SUMMARY.md')
    c.write_text(HERE / 'DEEP_N1000_SUMMARY.md', md)

    print(f"\n[run_deep_screen_n1000] {len(missing)} missing cell(s); "
          f"{len(core_flags)} Core investigate-flag(s)", flush=True)
    print(f"[run_deep_screen_n1000] wrote {HERE / 'DEEP_N1000_SUMMARY.json'} and .md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    print("-- recipes.py cross-checks --")
    check('recipes.py cross-checks vs canonical sources', recipes.run_recipe_selftest())

    from experiments._mc_pinned_runner import DEEP_4
    total = cell_plan(list(DEFAULT_PROFILES), DEEP_4)
    check('default enumeration (2 profiles x 4 deep windows) == 8 cells', total == 8, f"got {total}")
    check('DEEP_4 has 4 windows', len(DEEP_4) == 4, f"got {DEEP_4}")

    profs = _profiles()
    check("_profiles() returns keys {'core', 'apex_live'}", set(profs) == {'core', 'apex_live'})
    check("_profiles()['core'] is recipes.CORE_ENV", profs['core'] == recipes.CORE_ENV)
    check("_profiles()['apex_live'] is recipes.APEX_LIVE_ENV", profs['apex_live'] == recipes.APEX_LIVE_ENV)

    # Archived summary.json shape sanity (if present in this checkout).
    if ARCHIVED_SUMMARY.exists():
        try:
            archived = c.load_json(ARCHIVED_SUMMARY)
            check("archived deep_crash_screen summary.json has 'deep_windows_only'",
                  'deep_windows_only' in archived)
            check("archived deep_windows_only covers core+apex",
                  set(archived.get('deep_windows_only', {})) >= {'core', 'apex'})
        except Exception as e:
            check('archived deep_crash_screen summary.json well-formed', False, str(e))
    else:
        print("[WARN] archived deep_crash_screen/results/summary.json not found in this checkout "
              "-- skipping that shape check (not fatal).")

    # Markdown renderer ASCII-safety + SCREEN banner + annotation present.
    try:
        synth_cell = {'mean_ret': -20.0, 'med_ret': -25.0, 'worst_dd': 60.0, 'mean_dd': 50.0, 'p_coll': 0.0}
        synth_fail_cell = dict(synth_cell); synth_fail_cell['p_coll'] = 100.0
        synth = {
            'n_iter': 1000, 'pin_commit': PIN_COMMIT, 'profiles': ['core', 'apex_live'],
            'deep_windows': list(DEEP_4),
            'cells': {'core': {w: synth_cell for w in DEEP_4},
                      'apex_live': {w: (synth_fail_cell if w == 'dotcom_crash_2000_2002' else synth_cell)
                                    for w in DEEP_4}},
            'archived_available': True,
            'archived_cells': {'core': {w: {'p_coll': 0.0} for w in DEEP_4},
                               'apex': {w: {'p_coll': 77.7} for w in DEEP_4}},
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth)
        c.assert_ascii(md, 'synthetic DEEP_N1000_SUMMARY.md')
        check('render_markdown() output is ASCII-safe', True)
        check('render_markdown() includes the SCREEN-not-GATE banner', 'SCREEN, not GATE' in md)
        check('render_markdown() includes the Apex-deep-FAIL annotation', 'KNOWN-EXPECTED' in md)
        check("render_markdown() flags the synthetic dotcom cell as 'expected'",
              'expected (dot-com held-form)' in md)
        check("render_markdown() maps fresh 'apex_live' to archived 'apex' key", '77.7' in md)
    except Exception as e:
        check('render_markdown() ASCII-safety / banner / annotation', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
