"""P0.B -- the migration MC-determinism gate.

Every MC baseline in the corpus is an OLD-box number, and monte_carlo.py's
seeded MC is machine-scoped (per-iteration seeds are a pure function of
(window_label, iteration_index) via blake2b -- monte_carlo.py:3663
_stable_label_seed / :4407 seeds -- but floating-point summation order and
libm transcendental implementations can differ across CPU microarchitectures
and BLAS/compiler versions, so "same seeds" does not automatically guarantee
"same box" reproduces bit-for-bit on a DIFFERENT box). Nothing new-box may be
compared cross-box against the corpus until this gate renders a verdict.

WHAT THIS DOES: re-runs ONE archived arm (default: staged_30dte_n10, the
richest/most sizing-divergent of the three P0.3 evidence arms) at the
archived N and pin, across the SAME 12 windows, and diffs every window's
5-metric result against experiments/apex_dte_dd/results_p03_evidence/
summary.json -- the frozen task-610 evidence, loaded VERBATIM (recipe env,
pin, n_iter, window list all come from that JSON, never from code, so this
gate can never silently drift from what task-610 actually measured).

Per-window classification:
  BIT_EQUAL      : all 5 metrics (mean_ret, med_ret, worst_dd, mean_dd,
                   p_coll) exactly equal.
  NEAR_EQUAL     : |d worst_dd| and |d mean_dd| <= 0.05 (both already stored
                   on the 0-100 percentage-point scale -- see
                   monte_carlo.py's worst_dd=max(dds)*100 -- so 0.05 here
                   means 0.05 percentage points, i.e. 0.0005 as a 0-1
                   fraction); relative |d mean_ret| and |d med_ret| <= 1e-3;
                   p_coll exactly equal.
  DIVERGENT      : anything else.
  MISSING        : either side's cell is absent (subprocess timeout/failure,
                   or --allow-n-mismatch not needed here).

Overall verdict (written to PARITY_VERDICT.json / .md):
  PARITY_BIT_EQUAL        : every window BIT_EQUAL. Historical old-box
                             numbers remain directly comparable on this box.
  PARITY_FP_DRIFT          : every window >= NEAR_EQUAL (mix of BIT_EQUAL/
                             NEAR_EQUAL). "same-box paired A/Bs remain valid;
                             cross-box deltas are never citable (machine-
                             scoped MC canon)".
  DIVERGENT_CLEAN_BREAK    : any window DIVERGENT. "R1 protocol: new-box
                             baselines become the reference; do not cite
                             cross-box deltas as evidence; proceed to P1.A
                             E-tier baselines."
  INCOMPLETE               : one or more cells missing -- resubmit with
                             --resume.
  NOT_COMPARABLE_N_MISMATCH: --n-iter != archived n_iter and
                             --allow-n-mismatch was passed -- order-stat
                             comparisons (worst_dd/p_coll) are not
                             apples-to-apples across different N even given
                             identical per-iteration draws (N=300 IS an exact
                             prefix of N=500 on the same label, but max()/
                             count-over-N genuinely differ by construction).

Usage (queue-submittable bare; RUNBOOK.md Day-0 invocation):
    python experiments/newbox_rebaseline/run_parity_gate.py --cpu 8
    python experiments/newbox_rebaseline/run_parity_gate.py --selftest
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

ARCHIVED_SUMMARY = ROOT / 'experiments' / 'apex_dte_dd' / 'results_p03_evidence' / 'summary.json'
RESULTS_ROOT = HERE / 'results_parity'

VERDICT_TEXT = {
    'PARITY_BIT_EQUAL': (
        'Every requested window reproduced bit-for-bit against the archived task-610 '
        'evidence. Historical old-box numbers for this arm remain directly comparable '
        'on this box.'),
    'PARITY_FP_DRIFT': (
        'same-box paired A/Bs remain valid; cross-box deltas are never citable '
        '(machine-scoped MC canon)'),
    'DIVERGENT_CLEAN_BREAK': (
        'R1 protocol: new-box baselines become the reference; do not cite cross-box '
        'deltas as evidence; proceed to P1.A E-tier baselines.'),
    'INCOMPLETE': (
        'One or more cells are still missing (subprocess timeout/failure) -- resubmit '
        'with --resume to fill them in before trusting any verdict here.'),
    'NOT_COMPARABLE_N_MISMATCH': (
        '--n-iter differs from the archived n_iter and --allow-n-mismatch was passed -- '
        'every window is marked NOT_COMPARABLE for order-statistics (worst_dd/p_coll are '
        'aggregates over N and differ mechanically with N even given identical '
        'per-iteration draws). Re-run with --n-iter matching the archived value for a '
        'real parity verdict.'),
}


def load_archived() -> dict:
    if not ARCHIVED_SUMMARY.exists():
        raise SystemExit(f"archived summary not found: {ARCHIVED_SUMMARY} -- this repo checkout is "
                         f"missing the task-610 P0.3 evidence the parity gate diffs against.")
    return c.load_json(ARCHIVED_SUMMARY)


def render_markdown(summary: dict) -> str:
    lines = [
        '# PARITY_VERDICT',
        '',
        f"**Verdict: {summary['verdict']}**",
        '',
        summary['verdict_text'],
        '',
        f"Arm: `{summary['arm']}`  N_iter: {summary['n_iter']} (archived: {summary['archived_n_iter']})  "
        f"Pin: `{summary['pin_commit']}`  Generated: {summary['fingerprint']['timestamp_utc']}",
        '',
        '| window | class | d_mean_ret | d_med_ret | d_worst_dd(pp) | d_mean_dd(pp) | archived_p_coll | rerun_p_coll |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
    ]
    for row in summary['diff_rows']:
        arch, rer = row['archived'], row['rerun']
        if arch is None or rer is None:
            lines.append(f"| {row['window']} | {row['class']} | -- | -- | -- | -- | -- | -- |")
            continue
        d_mean_ret = rer['mean_ret'] - arch['mean_ret']
        d_med_ret = rer['med_ret'] - arch['med_ret']
        d_worst_dd = rer['worst_dd'] - arch['worst_dd']
        d_mean_dd = rer['mean_dd'] - arch['mean_dd']
        lines.append(
            f"| {row['window']} | {row['class']} | {d_mean_ret:+.6f} | {d_med_ret:+.6f} | "
            f"{d_worst_dd:+.6f} | {d_mean_dd:+.6f} | {arch['p_coll']} | {rer['p_coll']} |")
    lines += ['', '## Fingerprint', '', '```json', json.dumps(summary['fingerprint'], indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--arm', default='staged_30dte_n10')
    ap.add_argument('--n-iter', type=int, default=500)
    ap.add_argument('--cpu', type=int, default=8)
    ap.add_argument('--timeout-s', type=int, default=5400)
    ap.add_argument('--windows', default=None,
                    help='comma-separated window labels; default: all windows from the archived summary')
    ap.add_argument('--allow-n-mismatch', action='store_true',
                    help='proceed even if --n-iter != archived n_iter (marks every window NOT_COMPARABLE)')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    archived = load_archived()
    if args.arm not in archived['recipes']:
        raise SystemExit(f"--arm {args.arm!r} not in archived recipes: {list(archived['recipes'])}")
    recipe = dict(archived['recipes'][args.arm])
    pin_commit = archived['pinned_version_commit']
    archived_n_iter = archived['n_iter']
    windows = args.windows.split(',') if args.windows else list(archived['windows'])

    n_mismatch = args.n_iter != archived_n_iter
    if n_mismatch and not args.allow_n_mismatch:
        raise SystemExit(
            f"--n-iter {args.n_iter} != archived n_iter {archived_n_iter}. This check keeps the "
            f"parity gate an apples-to-apples determinism test. Pass --allow-n-mismatch to proceed "
            f"anyway (every window will be marked NOT_COMPARABLE for order-stats).")

    out_dir = RESULTS_ROOT / args.arm
    c.ensure_fresh_or_resume(out_dir, args.resume)

    c.print_plan([
        f"[run_parity_gate] arm={args.arm}  n_iter={args.n_iter} (archived={archived_n_iter})  "
        f"cpu={args.cpu}  cells={len(windows)}  windows={windows}",
        f"  pin={pin_commit}  out_dir={out_dir}  n_mismatch={n_mismatch} "
        f"(allowed={args.allow_n_mismatch})",
    ])

    from experiments._mc_pinned_runner import run_one_window  # lazy; module itself is DB-free at import

    rerun_cells = {}
    for label in windows:
        out_json = out_dir / f'{label}.json'
        res = run_one_window(recipe, label, args.n_iter, str(out_json), pin_commit,
                              deep=False, timeout_s=args.timeout_s, cpu=args.cpu)
        rerun_cells[label] = res.get(label) if res else None

    archived_cells = archived['cells'].get(args.arm, {})
    diff_rows = []
    for label in windows:
        arch = archived_cells.get(label)
        rer = rerun_cells.get(label)
        if arch is None or rer is None:
            cls = 'MISSING'
        elif n_mismatch:
            cls = 'NOT_COMPARABLE'
        else:
            cls = c.classify_metric_delta(arch, rer)
        diff_rows.append({'window': label, 'class': cls, 'archived': arch, 'rerun': rer})

    classes_present = {r['class'] for r in diff_rows}
    if 'MISSING' in classes_present:
        verdict = 'INCOMPLETE'
    elif n_mismatch:
        verdict = 'NOT_COMPARABLE_N_MISMATCH'
    elif 'DIVERGENT' in classes_present:
        verdict = 'DIVERGENT_CLEAN_BREAK'
    elif classes_present == {'BIT_EQUAL'}:
        verdict = 'PARITY_BIT_EQUAL'
    else:
        verdict = 'PARITY_FP_DRIFT'

    fp = fingerprint.capture(ROOT)
    summary = {
        'verdict': verdict,
        'verdict_text': VERDICT_TEXT[verdict],
        'arm': args.arm,
        'n_iter': args.n_iter,
        'archived_n_iter': archived_n_iter,
        'n_mismatch': n_mismatch,
        'pin_commit': pin_commit,
        'windows': windows,
        'diff_rows': diff_rows,
        'fingerprint': fp,
    }
    c.write_json(HERE / 'PARITY_VERDICT.json', summary)
    md = render_markdown(summary)
    c.assert_ascii(md, 'PARITY_VERDICT.md')
    c.write_text(HERE / 'PARITY_VERDICT.md', md)

    print(f"\n[run_parity_gate] verdict = {verdict}", flush=True)
    print(f"[run_parity_gate] wrote {HERE / 'PARITY_VERDICT.json'} and PARITY_VERDICT.md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    # 1. Verdict classifier on synthetic fixtures (all three classes + MISSING).
    bit_equal_a = {'mean_ret': 1.0, 'med_ret': 2.0, 'worst_dd': 3.0, 'mean_dd': 4.0, 'p_coll': 5.0}
    bit_equal_b = dict(bit_equal_a)
    check('classify_metric_delta: BIT_EQUAL fixture',
          c.classify_metric_delta(bit_equal_a, bit_equal_b) == 'BIT_EQUAL')

    near_a = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0, 'mean_dd': 40.0, 'p_coll': 0.0}
    near_b = {'mean_ret': 10.005, 'med_ret': 10.005, 'worst_dd': 50.05, 'mean_dd': 40.05, 'p_coll': 0.0}
    check('classify_metric_delta: NEAR_EQUAL fixture (at the 0.05pp / 1e-3-rel boundary)',
          c.classify_metric_delta(near_a, near_b) == 'NEAR_EQUAL')

    near_edge_fail_a = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0, 'mean_dd': 40.0, 'p_coll': 0.0}
    near_edge_fail_b = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0501, 'mean_dd': 40.0, 'p_coll': 0.0}
    check('classify_metric_delta: just-over-threshold worst_dd (0.0501pp) is DIVERGENT, not NEAR_EQUAL',
          c.classify_metric_delta(near_edge_fail_a, near_edge_fail_b) == 'DIVERGENT')

    div_a = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0, 'mean_dd': 40.0, 'p_coll': 0.0}
    div_b = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 56.0, 'mean_dd': 40.0, 'p_coll': 0.0}
    check('classify_metric_delta: DIVERGENT fixture (worst_dd off by 6pp)',
          c.classify_metric_delta(div_a, div_b) == 'DIVERGENT')

    div_coll_a = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0, 'mean_dd': 40.0, 'p_coll': 0.0}
    div_coll_b = {'mean_ret': 10.0, 'med_ret': 10.0, 'worst_dd': 50.0, 'mean_dd': 40.0, 'p_coll': 0.33}
    check('classify_metric_delta: DIVERGENT fixture (p_coll not exactly equal)',
          c.classify_metric_delta(div_coll_a, div_coll_b) == 'DIVERGENT')

    check('classify_metric_delta: MISSING fixture (archived side absent)',
          c.classify_metric_delta(None, bit_equal_b) == 'MISSING')
    check('classify_metric_delta: MISSING fixture (rerun side absent)',
          c.classify_metric_delta(bit_equal_a, None) == 'MISSING')

    # 2 + 3. Archived summary.json parse + recipe key sanity + windows list.
    if ARCHIVED_SUMMARY.exists():
        try:
            archived = c.load_json(ARCHIVED_SUMMARY)
            recipe = archived['recipes'].get('staged_30dte_n10', {})
            check('archived summary.json: staged_30dte_n10 recipe has >=20 env keys',
                  len(recipe) >= 20, f"got {len(recipe)}")
            check('archived summary.json: NOMINAL_CAL_DTE present in recipe',
                  'NOMINAL_CAL_DTE' in recipe)
            check('archived summary.json: n_iter == 500', archived.get('n_iter') == 500,
                  f"got {archived.get('n_iter')}")

            from experiments._mc_pinned_runner import STANDARD_12
            check('archived summary.json windows == the live-source STANDARD_12 list',
                  archived['windows'] == STANDARD_12,
                  f"archived={archived['windows']} vs STANDARD_12={STANDARD_12}")
            check('archived summary.json windows == live-parsed monte_carlo.py WINDOWS labels',
                  archived['windows'] == [w[0] for w in c.parse_mc_windows()])
        except Exception as e:
            check('archived summary.json well-formed', False, str(e))
    else:
        print("[WARN] archived summary.json not found -- skipping checks 2/3 (this repo checkout is "
              "missing task-610 evidence; run_parity_gate.py cannot run for real without it, but this "
              "is not a code defect)")

    # Verdict-text table completeness (every verdict this module can emit has text).
    for v in ('PARITY_BIT_EQUAL', 'PARITY_FP_DRIFT', 'DIVERGENT_CLEAN_BREAK', 'INCOMPLETE',
              'NOT_COMPARABLE_N_MISMATCH'):
        check(f"VERDICT_TEXT has an entry for {v}", v in VERDICT_TEXT)

    # Markdown renderer produces ASCII-safe output on a synthetic summary.
    try:
        synth = {
            'verdict': 'PARITY_BIT_EQUAL', 'verdict_text': VERDICT_TEXT['PARITY_BIT_EQUAL'],
            'arm': 'staged_30dte_n10', 'n_iter': 500, 'archived_n_iter': 500,
            'pin_commit': 'f9fb7b934',
            'diff_rows': [{'window': '5y', 'class': 'BIT_EQUAL', 'archived': bit_equal_a, 'rerun': bit_equal_b}],
            'fingerprint': fingerprint.capture(ROOT),
        }
        md = render_markdown(synth)
        c.assert_ascii(md, 'synthetic PARITY_VERDICT.md')
        check('render_markdown() output is ASCII-safe', True)
    except Exception as e:
        check('render_markdown() output is ASCII-safe', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
