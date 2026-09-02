"""P1.C-3 -- v74 whole-tail re-ablation at N=1000, IN-SAMPLE windows only.

Re-drives the whole-tail dampener ablation (experiments/skill_vs_baseline/
OVERNIGHT_FINDINGS.md) at N=1000 on the v73 substrate (the tail is already
retired ON v74, so score_real == score_notail there -- ablating it against
itself would be a no-op comparison; the substrate must be v73, where the
tail was still live). Arms: MOM_SCORE_PARQUET/MOM_SCORE_COL override the
signal monte_carlo.py reads (inert unless set -- monte_carlo.py:1973-1993):
  tail_on  : MOM_SCORE_COL=score_real    (v73's live overall, tail ON)
  tail_off : MOM_SCORE_COL=score_notail  (v73's pre_boost, tail OFF)
Both point at the SAME parquet: .cache/experiment_data/notail_v{vid}.parquet
(built by experiments/skill_vs_baseline/build_notail_parquet.py).

HARD CONSTRAINT (pre-arms December H5): every window's END date must be <=
CALIBRATION_CUTOFF_DATE (strategy_config.py, source-parsed at runtime -- the
OOS holdout line). Refused otherwise; this is the "in-sample rows only"
guarantee.

DISCREPANCY (source-verified 2026-07-29) -- RESOLVED same day by the
orchestrator: build_notail_parquet.py originally had NO version-selection
surface (`VID = AlgorithmVersion.get_active_scores_version().id` at import
time), so a bare run under active v74 would have built a meaningless
v74-vs-v74 self-comparison. Resolution: an env override
`NOTAIL_VID_OVERRIDE` was added to build_notail_parquet.py (unset =>
active-version behavior unchanged), and RUNBOOK.md Night-7 passes
`--env NOTAIL_VID_OVERRIDE=73` on the pre-step. The alternative flow of
temporarily /revert-ing the ACTIVE version to v73 was REJECTED: never flip
the live active-version pointer (dashboard, live scoring, concurrent queue
jobs all read it) for a research artifact. This runner's precondition check
still refuses (exit 2) if the v73 parquet is absent.

Usage (queue-submittable; RUNBOOK.md night 7, AFTER the notail-parquet
pre-step actually lands notail_v73.parquet -- bare except --cpu):
    python experiments/newbox_rebaseline/run_tail_ablation_n1000.py --cpu 10
    python experiments/newbox_rebaseline/run_tail_ablation_n1000.py --selftest

Exit codes: 0 selftest/run ok; 1 selftest fail; 2 missing parquet;
3 window(s) past the calibration cutoff; 4 vid/pin inconsistency.
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

RESULTS_ROOT = HERE / 'results_tail_n1000'
BUILD_NOTAIL_PY = ROOT / 'experiments' / 'skill_vs_baseline' / 'build_notail_parquet.py'

DEFAULT_VID = 73
DEFAULT_PIN = '07e9722b5'
KNOWN_VID_PIN_PAIRS = {73: '07e9722b5'}   # the only vid<->pin mapping this repo has ever used here

# Literal fallback matching SHIP_VALIDATION_9[:-1] (the spec's stated 8
# in-sample windows), used only if the lazy import below ever fails.
_FALLBACK_TAIL_WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']


def default_tail_windows() -> list[str]:
    """The 8 in-sample windows (locked spec default) -- computed lazily
    (import happens inside this function, never at module level, per hard
    rule 1) as SHIP_VALIDATION_9[:-1] (both lists sourced from the same
    _mc_pinned_runner constant, never hand-retyped twice). Falls back to the
    literal list above if _mc_pinned_runner is for some reason unimportable
    (defensive; that module is pure-stdlib and always importable in
    practice -- see run_*.py's other lazy imports of it)."""
    try:
        from experiments._mc_pinned_runner import SHIP_VALIDATION_9
        return list(SHIP_VALIDATION_9[:-1])
    except Exception:
        return list(_FALLBACK_TAIL_WINDOWS)


def parquet_path_for_vid(vid: int) -> Path:
    return ROOT / '.cache' / 'experiment_data' / f'notail_v{vid}.parquet'


def check_preconditions(vid: int, pin: str, windows: list[str], force_vid_pin: bool) -> tuple[int, str]:
    """Returns (exit_code, message). exit_code == 0 means all preconditions
    passed (message is empty in that case)."""
    parquet_path = parquet_path_for_vid(vid)
    if not parquet_path.exists():
        msg = (
            f"MISSING PRECONDITION: {parquet_path} does not exist.\n"
            f"Build it WITHOUT touching the live active-version pointer, via the env override "
            f"added 2026-07-29 (NOTAIL_VID_OVERRIDE; unset preserves the old active-version "
            f"behavior):\n"
            f"  trader queue submit --priority high --db heavy --cpu 4 --restartable "
            f"--window off_market --dedup notail-parquet-v{vid} "
            f"--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 --env NOTAIL_VID_OVERRIDE={vid} "
            f"--reason \"P1.C-3 pre-step: notail parquet (v{vid} substrate)\" "
            f"-- python experiments/skill_vs_baseline/build_notail_parquet.py\n"
            f"Confirm its printed parquet path ends in notail_v{vid}.parquet. Do NOT flip the "
            f"active scores version (/revert) for this -- the override exists precisely so the "
            f"live pointer is never moved for a research artifact."
        )
        return 2, msg

    cutoff = c.parse_calibration_cutoff()
    wmap = c.window_date_map(include_deep=True)
    late_windows = []
    for label in windows:
        if label not in wmap:
            late_windows.append((label, 'UNKNOWN_WINDOW_LABEL'))
            continue
        _, end = wmap[label]
        if end > cutoff:
            late_windows.append((label, end))
    if late_windows:
        msg = (f"HARD CONSTRAINT VIOLATED: the following window(s) end AFTER "
               f"CALIBRATION_CUTOFF_DATE={cutoff} (or are unknown labels), which would leak "
               f"out-of-sample rows into a pre-December-H5 ablation: {late_windows}. Remove them "
               f"from --windows or wait for the December OOS read to use them.")
        return 3, msg

    known_pin = KNOWN_VID_PIN_PAIRS.get(vid)
    if not force_vid_pin:
        if known_pin is None:
            msg = (f"vid={vid} has no known-good pin mapping in KNOWN_VID_PIN_PAIRS "
                   f"({KNOWN_VID_PIN_PAIRS}) -- pass --force-vid-pin to proceed anyway with "
                   f"--pin={pin!r} at your own risk (this repo has only ever run this ablation "
                   f"on vid=73/pin=07e9722b5).")
            return 4, msg
        if not (pin == known_pin or known_pin.startswith(pin) or pin.startswith(known_pin)):
            msg = (f"vid/pin MISMATCH: vid={vid} is known to pair with pin={known_pin!r}, but "
                   f"--pin={pin!r} was given. Pass --force-vid-pin to override if this is "
                   f"intentional (e.g. a genuinely new vid/pin combination).")
            return 4, msg

    return 0, ''


def render_markdown(summary: dict) -> str:
    lines = [
        '# TAIL_N1000_SUMMARY -- P1.C-3 whole-tail re-ablation at N=1000',
        '',
        f"vid={summary['vid']}  pin=`{summary['pin']}`  N={summary['n_iter']}  "
        f"Generated: {summary['fingerprint']['timestamp_utc']}",
        '',
        'The v74 ship decision stands either way -- this artifact is a power-hardening '
        're-read that pre-arms the December 2026 H5 stage-2 methodology '
        '(experiments/holdout_oos_2026_12/PREREGISTRATION.md). DD is the primary read.',
        '',
        '| window | tail_on worst_dd | tail_off worst_dd | delta(pp) | tail_on mean_ret | '
        'tail_off mean_ret | tail_on p_coll | tail_off p_coll |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for label in summary['windows']:
        on = summary['cells']['tail_on'].get(label)
        off = summary['cells']['tail_off'].get(label)
        if on is None or off is None:
            lines.append(f"| {label} | MISSING | | | | | | |")
            continue
        d_dd = off['worst_dd'] - on['worst_dd']
        lines.append(f"| {label} | {on['worst_dd']:.1f} | {off['worst_dd']:.1f} | {d_dd:+.1f} | "
                      f"{on['mean_ret']:+.1f} | {off['mean_ret']:+.1f} | {on['p_coll']:.1f} | "
                      f"{off['p_coll']:.1f} |")
    lines += ['', '## Fingerprint', '', '```json', json.dumps(summary['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vid', type=int, default=DEFAULT_VID)
    ap.add_argument('--pin', default=DEFAULT_PIN)
    ap.add_argument('--n-iter', type=int, default=1000)
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--timeout-s', type=int, default=5400)
    ap.add_argument('--windows', default=','.join(default_tail_windows()))
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--force-vid-pin', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    windows = [w.strip() for w in args.windows.split(',') if w.strip()]
    rc, msg = check_preconditions(args.vid, args.pin, windows, args.force_vid_pin)
    if rc != 0:
        print(msg, file=sys.stderr)
        return rc

    parquet_path = parquet_path_for_vid(args.vid)
    tail_on_env = {'MOM_SCORE_PARQUET': str(parquet_path), 'MOM_SCORE_COL': 'score_real'}
    tail_off_env = {'MOM_SCORE_PARQUET': str(parquet_path), 'MOM_SCORE_COL': 'score_notail'}
    arms = {'tail_on': tail_on_env, 'tail_off': tail_off_env}

    for arm in arms:
        c.ensure_fresh_or_resume(RESULTS_ROOT / arm, args.resume)

    c.print_plan([
        f"[run_tail_ablation_n1000] vid={args.vid}  pin={args.pin}  n_iter={args.n_iter}  "
        f"windows={windows}  cpu={args.cpu}  parquet={parquet_path}  "
        f"total_cells={len(arms) * len(windows)}",
    ])

    from experiments._mc_pinned_runner import run_one_window

    cells = {}
    for arm, env in arms.items():
        cells[arm] = {}
        for label in windows:
            out_json = RESULTS_ROOT / arm / f'{label}.json'
            res = run_one_window(env, label, args.n_iter, str(out_json), args.pin,
                                  deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
            cells[arm][label] = res.get(label) if res else None

    fp = fingerprint.capture(ROOT)
    summary = {
        'vid': args.vid, 'pin': args.pin, 'n_iter': args.n_iter, 'windows': windows,
        'parquet_path': str(parquet_path), 'cells': cells, 'fingerprint': fp,
    }
    c.write_json(HERE / 'TAIL_N1000_SUMMARY.json', summary)
    md = render_markdown(summary)
    c.assert_ascii(md, 'TAIL_N1000_SUMMARY.md')
    c.write_text(HERE / 'TAIL_N1000_SUMMARY.md', md)
    print(f"\n[run_tail_ablation_n1000] wrote {HERE / 'TAIL_N1000_SUMMARY.json'} and .md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    tail_windows = default_tail_windows()
    check('default_tail_windows() == _FALLBACK_TAIL_WINDOWS (lazy import matches the literal fallback)',
          tail_windows == _FALLBACK_TAIL_WINDOWS, f"got {tail_windows}")
    check('default_tail_windows() has 8 entries', len(tail_windows) == 8, f"got {tail_windows}")
    check("default_tail_windows() == the spec's literal 8-window list",
          tail_windows == ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y'],
          f"got {tail_windows}")

    # Enumeration: 2 arms x 8 windows = 16 cells.
    total = 2 * len(default_tail_windows())
    check('default enumeration (2 arms x 8 windows) == 16 cells', total == 16, f"got {total}")

    # build_notail_parquet.py: still no argparse surface, but it MUST now carry
    # the NOTAIL_VID_OVERRIDE env mechanism (orchestrator resolution 2026-07-29)
    # plus the active-version fallback.
    if BUILD_NOTAIL_PY.exists():
        src = BUILD_NOTAIL_PY.read_text(encoding='utf-8')
        check("build_notail_parquet.py has NO argparse/ArgumentParser (env override, not CLI)",
              'argparse' not in src and 'ArgumentParser' not in src)
        check("build_notail_parquet.py carries the NOTAIL_VID_OVERRIDE env mechanism",
              'NOTAIL_VID_OVERRIDE' in src)
        check("build_notail_parquet.py keeps get_active_scores_version() as the unset fallback",
              'get_active_scores_version()' in src)
    else:
        check('experiments/skill_vs_baseline/build_notail_parquet.py exists', False, str(BUILD_NOTAIL_PY))

    # Precondition (a): missing parquet -> exit 2, using a synthetic vid that
    # definitely has no parquet on disk (avoids any dependency on this
    # sandbox's real .cache/ state, which does not exist here).
    rc, msg = check_preconditions(999999, DEFAULT_PIN, default_tail_windows(), force_vid_pin=True)
    check('missing-parquet precondition returns exit code 2', rc == 2, f"got {rc}")
    check('missing-parquet message prescribes the NOTAIL_VID_OVERRIDE flow',
          'NOTAIL_VID_OVERRIDE=999999' in msg)
    check('missing-parquet message forbids flipping the active pointer (/revert rejected)',
          'Do NOT flip' in msg)

    # Precondition (b): a fake window past the cutoff -> refusal (exit 3).
    # Every REAL window (standard-12 and deep-4) currently ends before
    # CALIBRATION_CUTOFF_DATE (2026-06-15) -- that is the documented,
    # correct state of this repo (CLAUDE.md: "All 12 canonical windows END
    # before the cutoff"), so there is no real label to (ab)use for this
    # test. Monkeypatch _common.window_date_map to inject one synthetic
    # out-of-cutoff label instead, isolating the cutoff-comparison logic
    # itself from monte_carlo.py's actual (currently all-compliant) window
    # dates.
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_parquet_dir = Path(tmpdir) / '.cache' / 'experiment_data'
        fake_parquet_dir.mkdir(parents=True)
        fake_vid = 424242
        fake_parquet = fake_parquet_dir / f'notail_v{fake_vid}.parquet'
        fake_parquet.write_bytes(b'not a real parquet, just a stand-in for the existence check')

        orig_fn = parquet_path_for_vid
        orig_wmap = c.window_date_map
        try:
            globals()['parquet_path_for_vid'] = lambda vid, _p=fake_parquet: _p
            c.window_date_map = lambda include_deep=True: {
                'fake_future_window': ('2030-01-01', '2030-12-31'),
                **{w: d for w, d in orig_wmap(include_deep=True).items()},
            }
            rc2, msg2 = check_preconditions(fake_vid, DEFAULT_PIN, ['fake_future_window'], force_vid_pin=True)
            check('window ending after CALIBRATION_CUTOFF_DATE is refused with exit code 3',
                  rc2 == 3, f"got {rc2}, msg={msg2}")
            check("cutoff-violation message names the offending window ('fake_future_window')",
                  'fake_future_window' in msg2)

            rc3, msg3 = check_preconditions(fake_vid, DEFAULT_PIN, default_tail_windows(), force_vid_pin=True)
            check('all 8 default (real) windows pass the cutoff check (all end <= CALIBRATION_CUTOFF_DATE)',
                  rc3 == 0, f"got {rc3}, msg={msg3}")

            rc4, msg4 = check_preconditions(fake_vid, DEFAULT_PIN, ['not_a_real_label'], force_vid_pin=True)
            check('an unknown window label is also refused with exit code 3',
                  rc4 == 3, f"got {rc4}, msg={msg4}")
        finally:
            globals()['parquet_path_for_vid'] = orig_fn
            c.window_date_map = orig_wmap

    # Precondition (c): vid/pin consistency.
    with tempfile.TemporaryDirectory() as tmpdir2:
        fake_parquet2 = Path(tmpdir2) / 'notail_v73.parquet'
        fake_parquet2.write_bytes(b'stand-in')
        orig_fn2 = parquet_path_for_vid
        try:
            globals()['parquet_path_for_vid'] = lambda vid, _p=fake_parquet2: _p
            rc_ok, _ = check_preconditions(73, '07e9722b5', default_tail_windows(), force_vid_pin=False)
            check('vid=73/pin=07e9722b5 (the known-good pair) passes without --force-vid-pin',
                  rc_ok == 0, f"got {rc_ok}")
            rc_bad, msg_bad = check_preconditions(73, 'deadbeef000', default_tail_windows(), force_vid_pin=False)
            check('vid=73 with a mismatched pin is refused (exit 4) without --force-vid-pin',
                  rc_bad == 4, f"got {rc_bad}")
            rc_forced, _ = check_preconditions(73, 'deadbeef000', default_tail_windows(), force_vid_pin=True)
            check('vid=73 with a mismatched pin proceeds when --force-vid-pin is given',
                  rc_forced == 0, f"got {rc_forced}")
            rc_unknown_vid, msg_uv = check_preconditions(999, DEFAULT_PIN, default_tail_windows(), force_vid_pin=False)
            check('an unrecognized vid (no known pin mapping) is refused without --force-vid-pin',
                  rc_unknown_vid == 4, f"got {rc_unknown_vid}")
        finally:
            globals()['parquet_path_for_vid'] = orig_fn2

    check('parquet_path_for_vid restored to original after monkeypatch tests',
          parquet_path_for_vid is orig_fn2)

    # Markdown renderer ASCII-safety.
    try:
        synth_cell = {'mean_ret': 15.0, 'med_ret': 12.0, 'worst_dd': 45.0, 'mean_dd': 30.0, 'p_coll': 0.0}
        synth = {
            'vid': 73, 'pin': DEFAULT_PIN, 'n_iter': 1000, 'windows': ['5y'],
            'parquet_path': str(parquet_path_for_vid(73)),
            'cells': {'tail_on': {'5y': synth_cell}, 'tail_off': {'5y': synth_cell}},
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth)
        c.assert_ascii(md, 'synthetic TAIL_N1000_SUMMARY.md')
        check('render_markdown() output is ASCII-safe', True)
        check('render_markdown() mentions December H5 pre-arming', 'December' in md and 'H5' in md)
    except Exception as e:
        check('render_markdown() ASCII-safety', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
