"""P1.D -- 10y refresh chain + runtime table.

Sequential subprocess chain, hygiene-only (CLAUDE.md doesn't mark this as a
version bump), each step's wall-clock captured and streamed to its own log
file under results_refresh/:

  1. recalc         : python trader.py recalculate --force --full --rebuild-parquets
  2. assess         : python trader.py assess --force --dte both
  3. research_pack  : python tools/build_research_pack.py --version v74 --run-portfolio-windows

DISCREPANCY RESOLVED (source-verified 2026-07-29, per hard rule 5 "trust
source"): the locked spec text (and the gameplan-2026H2-DRAFT.md section-5
skeleton) phrase step 2 as "assess --force with 10y lookback". Reading
trader.py's `assess` handler directly shows this is ambiguous in an important
way:
  - Passing an EXPLICIT positional lookback token (e.g. "10y", which
    parse_lookback_arg() converts to 3650 days) takes the `days is not None`
    branch: assess_run() for JUST that one 3650-day window, and SKIPS the
    post-assess historic-update call entirely.
  - OMITTING the lookback token takes the `days is None` branch: assess_run_all()
    sweeps ALL FIVE windows (1y/2y/3y/5y/10y -- trader.py's own comment: "All
    windows up to 10y, run automatically when no explicit lookback given"),
    which is a SUPERSET that already includes the 10y window, AND runs
    historic-update once afterward, AND (because `do_force and days is None
    and not do_regime_adjust`) runs backtest-temporal for every DTE x every
    portfolio profile.
  This script defaults to OMITTING the lookback token (`--assess-mode
  auto-sweep`, matching the more complete "full refresh" intent a 10y-recalc
  chain is presumably for) and exposes `--assess-mode explicit-10y` as an
  escape hatch to the literal single-window form if the orchestrator actually
  wants the narrower behavior instead.

Nonzero exit stops the chain (later steps marked 'skipped'); RUNTIME_TABLE
still gets written either way.

Usage (queue-submittable; RUNBOOK.md night 4 -- bare, no flags):
    python experiments/newbox_rebaseline/run_refresh_10y.py
    python experiments/newbox_rebaseline/run_refresh_10y.py --dry-run
    python experiments/newbox_rebaseline/run_refresh_10y.py --skip recalc
    python experiments/newbox_rebaseline/run_refresh_10y.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _common as c  # noqa: E402
import fingerprint  # noqa: E402

RESULTS_ROOT = HERE / 'results_refresh'
TRADER_PY = ROOT / 'trader.py'
RESEARCH_PACK_PY = ROOT / 'algorithm_versions' / 'research_pack.py'

STEP_NAMES = ('recalc', 'assess', 'research_pack')


def build_steps(research_pack_version: str, assess_mode: str) -> list[tuple[str, list[str]]]:
    assess_argv = [sys.executable, str(TRADER_PY), 'assess', '--force', '--dte', 'both']
    if assess_mode == 'explicit-10y':
        assess_argv.append('10y')   # literal single-window form (skips historic-update)
    return [
        ('recalc', [sys.executable, str(TRADER_PY), 'recalculate', '--force', '--full',
                    '--rebuild-parquets']),
        ('assess', assess_argv),
        ('research_pack', [sys.executable, str(RESEARCH_PACK_PY.parent.parent / 'tools' /
                                                'build_research_pack.py'),
                            '--version', research_pack_version, '--run-portfolio-windows']),
    ]


def render_markdown(table: dict) -> str:
    lines = [
        '# RUNTIME_TABLE -- P1.D 10y refresh chain',
        '',
        f"Generated: {table['fingerprint']['timestamp_utc']}  assess_mode: {table['assess_mode']}",
        '',
        '| step | cmd | started_utc | ended_utc | wall_s | exit_code |',
        '|---|---|---|---|---:|---:|',
    ]
    for row in table['steps']:
        cmd = ' '.join(row['cmd'])
        lines.append(f"| {row['name']} | `{cmd}` | {row['started_utc'] or '--'} | "
                      f"{row['ended_utc'] or '--'} | {row['wall_s'] if row['wall_s'] is not None else '--'} | "
                      f"{row['exit_code'] if row['exit_code'] is not None else 'SKIPPED'} |")
    lines += ['', '## Fingerprint', '', '```json', json.dumps(table['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--skip', default='', help='comma list of step names to skip: recalc,assess,research_pack')
    ap.add_argument('--dry-run', action='store_true', help='print the exact commands and exit; runs nothing')
    ap.add_argument('--research-pack-version', default='v74')
    ap.add_argument('--assess-mode', choices=('auto-sweep', 'explicit-10y'), default='auto-sweep',
                    help='auto-sweep (default, recommended): omit the lookback token so trader.py runs '
                         'its full 1y/2y/3y/5y/10y sweep + historic-update. explicit-10y: pass a literal '
                         '"10y" positional token (single window only, skips historic-update).')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    skip = {s.strip() for s in args.skip.split(',') if s.strip()}
    unknown = skip - set(STEP_NAMES)
    if unknown:
        raise SystemExit(f"unknown --skip step name(s): {sorted(unknown)} -- valid: {STEP_NAMES}")

    steps = build_steps(args.research_pack_version, args.assess_mode)

    if args.dry_run:
        print("[run_refresh_10y] DRY RUN -- printing commands only, running nothing.")
        for name, argv_ in steps:
            marker = ' (SKIPPED per --skip)' if name in skip else ''
            print(f"  [{name}]{marker}: {' '.join(argv_)}")
        return 0

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    c.print_plan([
        f"[run_refresh_10y] steps={[n for n, _ in steps]}  skip={sorted(skip)}  "
        f"assess_mode={args.assess_mode}  research_pack_version={args.research_pack_version}",
    ])

    import subprocess
    rows = []
    chain_broken = False
    for name, argv_ in steps:
        if name in skip or chain_broken:
            rows.append({'name': name, 'cmd': argv_, 'started_utc': None, 'ended_utc': None,
                         'wall_s': None, 'exit_code': None,
                         'status': 'skipped (--skip)' if name in skip else 'skipped (prior step failed)'})
            print(f"[run_refresh_10y] {name}: SKIPPED", flush=True)
            continue

        log_path = RESULTS_ROOT / f'{name}.log'
        started = datetime.now(timezone.utc)
        t0 = perf_counter()
        print(f"[run_refresh_10y] {name}: START  cmd={' '.join(argv_)}", flush=True)
        with open(log_path, 'w', encoding='utf-8') as lf:
            proc = subprocess.run(argv_, cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT)
        wall_s = perf_counter() - t0
        ended = datetime.now(timezone.utc)
        print(f"[run_refresh_10y] {name}: DONE exit={proc.returncode} wall_s={wall_s:.1f} "
              f"log={log_path}", flush=True)
        rows.append({'name': name, 'cmd': argv_, 'started_utc': started.isoformat(),
                     'ended_utc': ended.isoformat(), 'wall_s': round(wall_s, 1),
                     'exit_code': proc.returncode, 'status': 'ok' if proc.returncode == 0 else 'FAILED'})
        if proc.returncode != 0:
            chain_broken = True
            print(f"[run_refresh_10y] {name} exited {proc.returncode} -- stopping chain "
                  f"(remaining steps marked skipped); see {log_path}", flush=True)

    fp = fingerprint.capture(ROOT)
    table = {'steps': rows, 'assess_mode': args.assess_mode, 'fingerprint': fp}
    c.write_json(HERE / 'RUNTIME_TABLE.json', table)
    md = render_markdown(table)
    c.assert_ascii(md, 'RUNTIME_TABLE.md')
    c.write_text(HERE / 'RUNTIME_TABLE.md', md)
    print(f"\n[run_refresh_10y] wrote {HERE / 'RUNTIME_TABLE.json'} and RUNTIME_TABLE.md", flush=True)
    return 0 if not chain_broken else 1


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    trader_src = TRADER_PY.read_text(encoding='utf-8')

    # --- recalculate: confirm the tokens this script relies on are recognized ---
    check("trader.py recalculate handler recognizes 'force' token",
          re.search(r"stripped == 'force'", trader_src) is not None)
    check("trader.py recalculate handler recognizes 'full' token",
          re.search(r"stripped == 'full'", trader_src) is not None)
    check("trader.py recalculate handler recognizes 'rebuild-parquets' token",
          re.search(r"stripped == 'rebuild-parquets'", trader_src) is not None)
    check("trader.py dispatches on command in ('recalculate', 'backfill')",
          re.search(r"command in \('recalculate', 'backfill'\)", trader_src) is not None)

    # --- assess: confirm --force / --dte {15,30,both} / positional lookback,
    # and confirm '--days' is NOT a recognized assess flag (CLAUDE.md's own claim).
    check("trader.py assess handler recognizes '--force'",
          re.search(r"arg == '--force'", trader_src) is not None)
    check("trader.py assess handler recognizes '--dte' with 15/30/both values",
          re.search(r"dte_explicit not in \('15', '30', 'both'\)", trader_src) is not None)
    check("trader.py assess handler has NO '--days' flag (positional lookback only)",
          re.search(r"arg == '--days'", trader_src) is None)
    check("trader.py assess handler: omitting lookback runs assess_run_all (all windows to 10y)",
          'from assess_scores import run_all_windows as assess_run_all' in trader_src)
    check("trader.py assess handler: explicit lookback SKIPS historic-update (single-window branch)",
          re.search(r"if days is not None:\s*\n\s*# Explicit lookback: run just that one window",
                     trader_src) is not None)

    # --- research_pack: confirm --version / --run-portfolio-windows exist ---
    if RESEARCH_PACK_PY.exists():
        rp_src = RESEARCH_PACK_PY.read_text(encoding='utf-8')
        check("research_pack.py argparse has --version",
              '"--version"' in rp_src)
        check("research_pack.py argparse has --run-portfolio-windows",
              '"--run-portfolio-windows"' in rp_src)
    else:
        check('algorithm_versions/research_pack.py exists', False, str(RESEARCH_PACK_PY))

    # --- build_steps() produces exactly 3 commands with the right argv shape ---
    steps = build_steps('v74', 'auto-sweep')
    check('build_steps() returns exactly 3 steps', len(steps) == 3, f"got {len(steps)}")
    names = [n for n, _ in steps]
    check('build_steps() step names == (recalc, assess, research_pack)',
          names == list(STEP_NAMES), f"got {names}")

    recalc_argv = dict(steps)['recalc']
    check("recalc argv contains 'recalculate', '--force', '--full', '--rebuild-parquets'",
          all(tok in recalc_argv for tok in ('recalculate', '--force', '--full', '--rebuild-parquets')),
          f"got {recalc_argv}")

    assess_argv = dict(steps)['assess']
    check("assess argv (auto-sweep mode) contains 'assess', '--force', '--dte', 'both' and NO positional lookback",
          assess_argv[-4:] == ['assess', '--force', '--dte', 'both'], f"got {assess_argv}")

    steps_explicit = build_steps('v74', 'explicit-10y')
    assess_argv_explicit = dict(steps_explicit)['assess']
    check("assess argv (explicit-10y mode) ends with a literal '10y' token",
          assess_argv_explicit[-1] == '10y', f"got {assess_argv_explicit}")

    rp_argv = dict(steps)['research_pack']
    check("research_pack argv contains '--version', 'v74', '--run-portfolio-windows'",
          '--version' in rp_argv and 'v74' in rp_argv and '--run-portfolio-windows' in rp_argv,
          f"got {rp_argv}")

    # --- dry-run path prints without executing anything ---
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(['--dry-run'])
        out = buf.getvalue()
        check('--dry-run returns exit 0', rc == 0)
        check('--dry-run output mentions all 3 steps', all(n in out for n in STEP_NAMES))
        check('--dry-run did not create results_refresh/', not RESULTS_ROOT.exists())
    except Exception as e:
        check('--dry-run path runs cleanly', False, str(e))

    # --skip validation
    try:
        main(['--dry-run', '--skip', 'bogus_step'])
        check('--skip with an unknown step name raises SystemExit', False, 'did not raise')
    except SystemExit:
        check('--skip with an unknown step name raises SystemExit', True)

    # Markdown renderer ASCII-safety.
    try:
        synth_table = {
            'assess_mode': 'auto-sweep',
            'steps': [{'name': 'recalc', 'cmd': ['python', 'trader.py', 'recalculate'],
                       'started_utc': '2026-07-29T00:00:00+00:00', 'ended_utc': '2026-07-29T00:10:00+00:00',
                       'wall_s': 600.0, 'exit_code': 0, 'status': 'ok'}],
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth_table)
        c.assert_ascii(md, 'synthetic RUNTIME_TABLE.md')
        check('render_markdown() output is ASCII-safe', True)
    except Exception as e:
        check('render_markdown() output is ASCII-safe', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
