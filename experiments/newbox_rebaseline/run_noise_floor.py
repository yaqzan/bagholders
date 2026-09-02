"""P1.E -- measured seed-noise dispersion table.

Question: for a FIXED config (Core, the production-default-equivalent profile)
and a FIXED window, how much does the MC's own per-iteration RNG dispersion
move worst_dd / mean_ret / med_ret / p_coll as a function of N (the "N-ladder"
tiers)? This supersedes the Phase-v32 inherited noise figures in
known-issues.md once adopted.

METHOD: for each window, run B independent "batches" -- each batch is ONE
monte_carlo.py subprocess at N=n-max with MC_RETURN_PATHS=1, using a custom
WIN_START/WIN_END/WIN_LABEL window (NOT WINDOWS_OVERRIDE) so we can control
the per-batch seed stream directly:
    batch 0            : WIN_LABEL = <label>            (the CANONICAL label
                          -- same seed stream a normal canonical-window run
                          of this same config would use; the "paired-
                          reference" batch).
    batch i (i=1..B-1)  : WIN_LABEL = '<label>#b<i>'      (a different string
                          -> a different blake2b hash -> an INDEPENDENT seed
                          stream, per monte_carlo.py:3663 _stable_label_seed
                          + :4407 seeds = [1000*_stable_label_seed(label)+it
                          for it in range(N_ITER)]).
This does NOT call experiments._mc_pinned_runner.run_one_window (that helper
hardcodes WINDOWS_OVERRIDE=<label> mode); this file's run_one_batch() is its
own small driver built on _common.run_mc_subprocess, exactly as the locked
spec requires.

Then, per batch, we compute TIER-PREFIX statistics at each N in --tiers by
slicing the SAME per-iteration finals/dds arrays to the first N entries and
recomputing mean_ret/med_ret/mean_dd/worst_dd/p_coll via
_common.tier_prefix_stats -- i.e. one $2000-iteration subprocess run buys
every smaller tier's statistics for free (N=300 is an exact prefix of N=2000
on the same label; MP vs serial is bit-identical).

VALIDATION built in: for batch 0 (b0) at the full N=n-max, the prefix-computed
stats MUST equal monte_carlo.py's own emitted aggregate fields bit-exactly.
This validates that OUR replication of the aggregate math (mean/median/max/
count-over-N) is correct -- if it does not hold, this script aborts with a
clear message rather than silently reporting wrong numbers for every tier.

Usage (queue-submittable; RUNBOOK.md night 3):
    python experiments/newbox_rebaseline/run_noise_floor.py --cpu 10
    python experiments/newbox_rebaseline/run_noise_floor.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
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

RESULTS_ROOT = HERE / 'results_noise_floor'
PIN_COMMIT = 'f9fb7b934'
DEFAULT_WINDOWS = ['5y', '22-now', '2020_crash']
DEFAULT_TIERS = [300, 500, 1000, 2000]


def batch_label(canonical_label: str, batch_idx: int) -> str:
    return canonical_label if batch_idx == 0 else f'{canonical_label}#b{batch_idx}'


def run_one_batch(env_overrides: dict, win_start: str, win_end: str, win_label: str,
                   n_iter: int, out_json_path, pin_commit: str, timeout_s: int, cpu: int,
                   resume: bool = True) -> dict | None:
    """Own small subprocess driver for the custom WIN_START/WIN_END/WIN_LABEL
    mode (see module docstring for why this cannot reuse
    _mc_pinned_runner.run_one_window). Returns the FULL raw MC_RESULTS_JSON
    dict (keyed by win_label, plus '_meta'), or None on failure/timeout."""
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['MC_NO_DB_PERSIST'] = '1'
    env['N_ITER_OVERRIDE'] = str(n_iter)
    env['WIN_START'] = win_start
    env['WIN_END'] = win_end
    env['WIN_LABEL'] = win_label
    env['MC_RETURN_PATHS'] = '1'
    env['MC_RESULTS_JSON'] = str(out_json_path)
    env['ALGORITHM_VERSION_PIN'] = pin_commit
    env['MC_WORKERS'] = str(max(1, cpu))
    for k, v in env_overrides.items():
        env[k] = str(v)
    return c.run_mc_subprocess(env, out_json_path, timeout_s, resume=resume)


def validate_b0_against_own_aggregate(win_label: str, raw: dict, n_max: int) -> None:
    """Abort with a clear message if our tier_prefix_stats replication does
    not bit-exactly match monte_carlo.py's own aggregate for the full-N batch
    0 run (see module docstring 'VALIDATION built in')."""
    cell = raw.get(win_label)
    if cell is None:
        raise SystemExit(f"batch 0 for {win_label!r}: raw JSON has no key {win_label!r} "
                         f"(keys present: {list(raw.keys())})")
    paths = cell.get('paths')
    if not paths or not paths.get('finals') or not paths.get('dds'):
        raise SystemExit(f"batch 0 for {win_label!r}: 'paths.finals'/'paths.dds' missing or empty -- "
                         f"MC_RETURN_PATHS may not have been honored by this monte_carlo.py.")
    starting_cash = paths.get('starting_cash') or 50_000.0
    collapse_threshold = c.parse_collapse_threshold()
    finals, dds = paths['finals'], paths['dds']
    if len(finals) != n_max:
        print(f"  [WARN] {win_label}: batch-0 realized n_iter={len(finals)} != requested {n_max} "
              f"-- validating against the realized count instead.", flush=True)
    recomputed = c.tier_prefix_stats(finals, dds, starting_cash, len(finals), collapse_threshold)
    own_agg = {k: cell[k] for k in ('mean_ret', 'med_ret', 'mean_dd', 'worst_dd', 'p_coll') if k in cell}
    mismatches = {k: (recomputed[k], own_agg.get(k)) for k in own_agg if recomputed[k] != own_agg[k]}
    if mismatches:
        raise SystemExit(
            f"VALIDATION FAILED for {win_label!r}: our tier_prefix_stats replication of "
            f"monte_carlo.py's own aggregate math does NOT match bit-exactly at N={len(finals)}. "
            f"Mismatches (recomputed, engine-reported): {mismatches}. This means the aggregate-math "
            f"replication in _common.tier_prefix_stats is wrong -- fix it before trusting any tier "
            f"below full-N in NOISE_FLOOR_TABLE.")
    print(f"  [OK] {win_label}: batch-0 full-N self-consistency check passed "
          f"(our replication == monte_carlo.py's own aggregate, bit-exact)", flush=True)


def render_markdown(table: dict, fp: dict) -> str:
    lines = [
        '# NOISE_FLOOR_TABLE -- P1.E measured seed-noise dispersion',
        '',
        'Supersedes Phase-v32 inherited noise figures (known-issues.md) once adopted; '
        f"measured on this fingerprint (see bottom). Generated: {fp['timestamp_utc']}",
        '',
        f"Windows: {table['windows']}  Batches: {table['batches']}  Tiers: {table['tiers']}  "
        f"Pin: `{table['pin_commit']}`",
        '',
        '| window | tier(N) | worst_dd mean | worst_dd std | worst_dd min | worst_dd max | '
        'worst_dd range | mean_ret max/min ratio | med_ret max/min ratio | p_coll (per batch) |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|',
    ]
    for window in table['windows']:
        for tier in table['tiers']:
            cell = table['cells'][window][str(tier)]
            dd = cell['worst_dd']
            lines.append(
                f"| {window} | {tier} | {dd['mean']:.3f} | {dd['std']:.3f} | {dd['min']:.3f} | "
                f"{dd['max']:.3f} | {dd['range']:.3f} | {cell['mean_ret_ratio']} | "
                f"{cell['med_ret_ratio']} | {cell['p_coll_list']} |")
    lines += ['', '## Fingerprint', '', '```json', json.dumps(fp, indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--windows', default=','.join(DEFAULT_WINDOWS))
    ap.add_argument('--batches', type=int, default=8)
    ap.add_argument('--n-max', type=int, default=2000)
    ap.add_argument('--tiers', default=','.join(str(t) for t in DEFAULT_TIERS))
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--timeout-s', type=int, default=21600)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--pin', default=PIN_COMMIT)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    windows = [w.strip() for w in args.windows.split(',') if w.strip()]
    tiers = sorted(int(t.strip()) for t in args.tiers.split(',') if t.strip())
    if tiers[-1] > args.n_max:
        raise SystemExit(f"largest tier {tiers[-1]} exceeds --n-max {args.n_max} -- "
                         f"every tier must be a prefix of the full n-max run.")

    wmap = c.window_date_map(include_deep=True)
    missing_labels = [w for w in windows if w not in wmap]
    if missing_labels:
        raise SystemExit(f"unknown window label(s): {missing_labels} -- not found in monte_carlo.py's "
                         f"WINDOWS/DEEP_WINDOWS tables (source-parsed fresh; known labels: "
                         f"{sorted(wmap)})")

    c.ensure_fresh_or_resume(RESULTS_ROOT, args.resume)

    c.print_plan([
        f"[run_noise_floor] windows={windows}  batches={args.batches}  n_max={args.n_max}  "
        f"tiers={tiers}  cpu={args.cpu}  pin={args.pin}  "
        f"total_subprocesses={len(windows) * args.batches}",
    ])

    collapse_threshold = c.parse_collapse_threshold()
    cells = {}
    for window in windows:
        win_start, win_end = wmap[window]
        cells[window] = {}
        batch_paths = []   # list of (finals, dds, starting_cash) per batch
        for b in range(args.batches):
            label = batch_label(window, b)
            out_json = RESULTS_ROOT / window / f'batch_{b}.json'
            raw = run_one_batch(recipes.CORE_ENV, win_start, win_end, label, args.n_max,
                                 out_json, args.pin, args.timeout_s, args.cpu, resume=args.resume)
            if raw is None:
                print(f"  [MISSING] {window} batch {b} ({label}) -- subprocess failed/timed out", flush=True)
                batch_paths.append(None)
                continue
            if b == 0:
                validate_b0_against_own_aggregate(label, raw, args.n_max)
            cell = raw.get(label)
            paths = (cell or {}).get('paths') or {}
            finals, dds = paths.get('finals'), paths.get('dds')
            starting_cash = paths.get('starting_cash') or 50_000.0
            if not finals or not dds:
                print(f"  [MISSING] {window} batch {b}: paths.finals/dds empty", flush=True)
                batch_paths.append(None)
                continue
            batch_paths.append((finals, dds, starting_cash))

        for tier in tiers:
            per_batch_stats = []
            for bp in batch_paths:
                if bp is None:
                    continue
                finals, dds, starting_cash = bp
                if len(finals) < tier:
                    continue
                per_batch_stats.append(c.tier_prefix_stats(finals, dds, starting_cash, tier, collapse_threshold))
            if not per_batch_stats:
                cells[window][str(tier)] = {'error': 'no complete batches at this tier'}
                continue
            worst_dds = [s['worst_dd'] for s in per_batch_stats]
            mean_rets = [s['mean_ret'] for s in per_batch_stats]
            med_rets = [s['med_ret'] for s in per_batch_stats]
            p_colls = [s['p_coll'] for s in per_batch_stats]

            def _ratio(vals):
                if not vals or min(vals) == 0:
                    return None
                return max(vals) / min(vals)

            cells[window][str(tier)] = {
                'n_batches': len(per_batch_stats),
                'worst_dd': {
                    'mean': statistics.mean(worst_dds), 'std': statistics.pstdev(worst_dds) if len(worst_dds) > 1 else 0.0,
                    'min': min(worst_dds), 'max': max(worst_dds), 'range': max(worst_dds) - min(worst_dds),
                },
                'mean_ret_ratio': _ratio(mean_rets), 'mean_ret_values': mean_rets,
                'med_ret_ratio': _ratio(med_rets), 'med_ret_values': med_rets,
                'p_coll_list': p_colls,
            }

    fp = fingerprint.capture(ROOT)
    table = {
        'windows': windows, 'batches': args.batches, 'n_max': args.n_max, 'tiers': tiers,
        'pin_commit': args.pin, 'cells': cells, 'fingerprint': fp,
        'note': 'Supersedes Phase-v32 inherited noise figures (known-issues.md) once adopted.',
    }
    c.write_json(HERE / 'NOISE_FLOOR_TABLE.json', table)
    md = render_markdown(table, fp)
    c.assert_ascii(md, 'NOISE_FLOOR_TABLE.md')
    c.write_text(HERE / 'NOISE_FLOOR_TABLE.md', md)
    print(f"\n[run_noise_floor] wrote {HERE / 'NOISE_FLOOR_TABLE.json'} and NOISE_FLOOR_TABLE.md", flush=True)
    return 0


def _selftest() -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        status = 'PASS' if cond else 'FAIL'
        print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    # Source-regex window parser returns 12+4 windows with exact expected labels.
    from experiments._mc_pinned_runner import STANDARD_12, DEEP_4
    std = c.parse_mc_windows()
    deep = c.parse_mc_deep_windows()
    check('parse_mc_windows() returns 12 windows', len(std) == 12, f"got {len(std)}")
    check('parse_mc_windows() labels == STANDARD_12', [w[0] for w in std] == STANDARD_12,
          f"got {[w[0] for w in std]}")
    check('parse_mc_deep_windows() returns 4 windows', len(deep) == 4, f"got {len(deep)}")
    check('parse_mc_deep_windows() labels == DEEP_4', [w[0] for w in deep] == DEEP_4,
          f"got {[w[0] for w in deep]}")
    wmap = c.window_date_map(include_deep=True)
    check('window_date_map() has 16 entries (12 std + 4 deep)', len(wmap) == 16, f"got {len(wmap)}")
    for label in DEFAULT_WINDOWS:
        check(f"default window {label!r} resolvable via window_date_map()", label in wmap)

    # Batch-label scheme produces distinct labels and b0 == canonical.
    labels = [batch_label('5y', i) for i in range(8)]
    check('batch_label(...,0) == canonical label', labels[0] == '5y')
    check('batch labels are all distinct', len(set(labels)) == len(labels), f"got {labels}")
    check('batch labels 1..7 all contain the canonical label as a prefix',
          all(lbl.startswith('5y#b') for lbl in labels[1:]))

    # Prefix-stat math on synthetic arrays (hand-computable).
    finals = [100.0, 200.0, 300.0, 50.0]
    dds = [0.10, 0.30, 0.05, 0.90]
    starting_cash = 100.0
    collapse_threshold = 0.20
    s3 = c.tier_prefix_stats(finals, dds, starting_cash, 3, collapse_threshold)
    # first 3: finals=[100,200,300] mean=200 -> mean_ret=100%; median=200 -> med_ret=100%
    # dds=[0.10,0.30,0.05] mean=0.15 -> mean_dd=15%; max=0.30 -> worst_dd=30%
    # collapse threshold_usd=100*0.2=20; none of [100,200,300] <=20 -> p_coll=0
    check('tier_prefix_stats n=3: mean_ret', abs(s3['mean_ret'] - 100.0) < 1e-9, f"got {s3['mean_ret']}")
    check('tier_prefix_stats n=3: med_ret', abs(s3['med_ret'] - 100.0) < 1e-9, f"got {s3['med_ret']}")
    check('tier_prefix_stats n=3: mean_dd', abs(s3['mean_dd'] - 15.0) < 1e-9, f"got {s3['mean_dd']}")
    check('tier_prefix_stats n=3: worst_dd', abs(s3['worst_dd'] - 30.0) < 1e-9, f"got {s3['worst_dd']}")
    check('tier_prefix_stats n=3: p_coll', abs(s3['p_coll'] - 0.0) < 1e-9, f"got {s3['p_coll']}")

    s4 = c.tier_prefix_stats(finals, dds, starting_cash, 4, collapse_threshold)
    # all 4: finals=[100,200,300,50] mean=162.5 -> mean_ret=62.5%; median=(100+200)/2=150 -> med_ret=50%
    # dds=[.10,.30,.05,.90] mean=.3375 -> mean_dd=33.75%; max=.90 -> worst_dd=90%
    # 50<=20? no (20 is threshold, 50>20) -> p_coll=0 still (50 is NOT <= 20)
    check('tier_prefix_stats n=4: mean_ret', abs(s4['mean_ret'] - 62.5) < 1e-9, f"got {s4['mean_ret']}")
    check('tier_prefix_stats n=4: med_ret', abs(s4['med_ret'] - 50.0) < 1e-9, f"got {s4['med_ret']}")
    check('tier_prefix_stats n=4: worst_dd', abs(s4['worst_dd'] - 90.0) < 1e-9, f"got {s4['worst_dd']}")
    check('tier_prefix_stats n=4: p_coll (50 > threshold_usd=20, not collapsed)',
          abs(s4['p_coll'] - 0.0) < 1e-9, f"got {s4['p_coll']}")

    # Collapse-predicate replication tested against a synthetic paths dict.
    synth_finals = [25.0, 20.0, 19.99, 100.0]   # threshold_usd = 100*0.2 = 20
    pc = c.compute_collapse_pct(synth_finals, 100.0, 0.20)
    check('compute_collapse_pct: exactly-at-threshold (20<=20) and below both count',
          abs(pc - 50.0) < 1e-9, f"got {pc} (expected 50.0 -- 2 of 4 <= 20.0)")

    # validate_b0_against_own_aggregate: a self-consistent synthetic raw dict passes.
    try:
        label = '5y'
        agg = c.tier_prefix_stats(finals, dds, starting_cash, len(finals), collapse_threshold)
        synth_raw = {
            label: {
                'mean_ret': agg['mean_ret'], 'med_ret': agg['med_ret'],
                'mean_dd': agg['mean_dd'], 'worst_dd': agg['worst_dd'], 'p_coll': agg['p_coll'],
                'paths': {'finals': finals, 'dds': dds, 'starting_cash': starting_cash},
            }
        }
        # Monkeypatch parse_collapse_threshold isn't needed -- pass matching value directly
        # by calling the same code path with the real strategy_config.py value; since our
        # synthetic aggregate was built with 0.20 and the real repo's STRATEGY_30DTE
        # COLLAPSE_THRESHOLD is also 0.20, this should pass with the real parser too.
        real_threshold = c.parse_collapse_threshold()
        check('sandbox check: strategy_config.py COLLAPSE_THRESHOLD == 0.20 (assumed by this synthetic fixture)',
              abs(real_threshold - 0.20) < 1e-9, f"got {real_threshold}")
        validate_b0_against_own_aggregate(label, synth_raw, len(finals))
        check('validate_b0_against_own_aggregate: self-consistent synthetic fixture passes', True)
    except SystemExit as e:
        check('validate_b0_against_own_aggregate: self-consistent synthetic fixture passes', False, str(e))

    # And a DELIBERATELY inconsistent one must raise SystemExit.
    try:
        bad_raw = {
            label: {
                'mean_ret': 999.0, 'med_ret': agg['med_ret'], 'mean_dd': agg['mean_dd'],
                'worst_dd': agg['worst_dd'], 'p_coll': agg['p_coll'],
                'paths': {'finals': finals, 'dds': dds, 'starting_cash': starting_cash},
            }
        }
        try:
            validate_b0_against_own_aggregate(label, bad_raw, len(finals))
            check('validate_b0_against_own_aggregate: inconsistent fixture raises SystemExit', False,
                  'did not raise')
        except SystemExit:
            check('validate_b0_against_own_aggregate: inconsistent fixture raises SystemExit', True)
    except Exception as e:
        check('validate_b0_against_own_aggregate: inconsistent fixture raises SystemExit', False, str(e))

    # Markdown renderer ASCII-safety.
    try:
        synth_table = {
            'windows': ['5y'], 'batches': 2, 'n_max': 300, 'tiers': [300],
            'pin_commit': PIN_COMMIT,
            'cells': {'5y': {'300': {
                'n_batches': 2, 'worst_dd': {'mean': 30.0, 'std': 1.0, 'min': 29.0, 'max': 31.0, 'range': 2.0},
                'mean_ret_ratio': 1.05, 'mean_ret_values': [10.0, 10.5],
                'med_ret_ratio': 1.02, 'med_ret_values': [9.0, 9.2], 'p_coll_list': [0.0, 0.0],
            }}},
        }
        md = render_markdown(synth_table, fingerprint.capture(ROOT))
        c.assert_ascii(md, 'synthetic NOISE_FLOOR_TABLE.md')
        check('render_markdown() output is ASCII-safe', True)
    except Exception as e:
        check('render_markdown() output is ASCII-safe', False, str(e))

    return ok


if __name__ == '__main__':
    raise SystemExit(main())
