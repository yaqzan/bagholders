"""Bayesian-style sweep of (DEAD_HOLD_TRIGGER_PNL, DEAD_HOLD_POPOUT_PNL) for
30 DTE and 15 DTE strategies, finding the optimal DD vs compound trade-off.

DD targets (per ship criteria):
    65% = great
    72% = acceptable
    80% = max ceiling

Strategy: subprocess each variant with env-var overrides, capture stdout, parse
the WINDOW summary table. Two stages:

    Stage 1 (screening): 8 variants × 3 windows (22-now, 5y, 2025) × N=100
    Stage 2 (validation): top 3 + baseline × 8 windows × N=300

Usage:
    python experiments/dead_hold_sweep.py --dte 30 --stage 1
    python experiments/dead_hold_sweep.py --dte 15 --stage 1
    python experiments/dead_hold_sweep.py --dte 30 --stage 2 --pick T-0.50_P-0.30 T-0.50_P-0.20
"""
from __future__ import annotations
import sys, os, subprocess, time, re, json, argparse
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Stage 1 variants (8 of them — covers the matrix corners + middle)
STAGE1_VARIANTS = [
    ('OFF',         False, 0,      0     ),   # baseline (DH off)
    ('T-0.40_P-0.30', True,  -0.40, -0.30),
    ('T-0.40_P-0.10', True,  -0.40, -0.10),
    ('T-0.50_P-0.30', True,  -0.50, -0.30),
    ('T-0.50_P-0.20', True,  -0.50, -0.20),
    ('T-0.50_P-0.00', True,  -0.50,  0.00),
    ('T-0.60_P-0.30', True,  -0.60, -0.30),
    ('T-0.70_P-0.20', True,  -0.70, -0.20),
]


WINDOW_RE = re.compile(
    r'^([\w\-,]+(?:[\s]*\(\d{4}-\d{2}-\d{2}[^\)]+\))?)\s+'
    r'\+?([\d,\.\-eE\+]+)%\s+\+?([\d,\.\-eE\+]+)%\s+'
    r'([\d\.]+)%\s+([\d\.]+)%\s+([\d\.]+)%'
)


def parse_summary(text: str, expected_windows: list[str]) -> dict[str, dict]:
    """Parse window-level summary stats from monte_carlo[_15dte].py output.

    Two output formats supported:
      30 DTE: single combined "SUMMARY - Mean Return / Worst DD / P(collapse)" table
      15 DTE: three separate tables (MeanRet, WorstDD, P(collapse))

    Strategy: parse the per-window per-mode lines emitted INSIDE each
    "WINDOW: <name>" block (right above the SUMMARY blocks). Format:

      seeded   <CTP>%  <PTP>%  <CTrd>  <PTrd>  <MeanRet>%  <MedRet>%  <WorstDD>%  <MeanDD>%  <P(col)>%

    This gives all 5 metrics per window in one line, present in BOTH DTE
    output formats, indexed by the window header.
    """
    results: dict[str, dict] = {}
    cur_window = None
    for line in text.splitlines():
        s = line.strip()
        # Window header
        if s.startswith('WINDOW:'):
            # 'WINDOW: 22-now  (2022-01-01 -> 2026-04-24)'
            head = s.split('WINDOW:', 1)[1].strip()
            cur_window = head.split()[0]   # take first token only
            continue
        if cur_window is None:
            continue
        # Per-mode metric line — starts with mode name 'seeded'
        if not s.startswith('seeded'):
            continue
        # Tokens: mode CTP% PTP% CTrd PTrd MeanRet% MedRet% WorstDD% MeanDD% P(col)%
        parts = s.split()
        if len(parts) < 10:
            continue
        try:
            p_coll = float(parts[-1].rstrip('%'))
            mean_dd = float(parts[-2].rstrip('%'))
            worst_dd = float(parts[-3].rstrip('%'))
            med_ret = float(parts[-4].rstrip('%').replace(',', ''))
            mean_ret = float(parts[-5].rstrip('%').replace(',', ''))
            results[cur_window] = dict(
                mean_ret=mean_ret, med_ret=med_ret,
                worst_dd=worst_dd, mean_dd=mean_dd, p_coll=p_coll,
            )
        except ValueError:
            continue
    return results


def run_variant(dte: str, name: str, enabled: bool, trigger: float, popout: float,
                windows: str, n_iter: int, timeout_sec: int = 1200) -> dict:
    """Subprocess one MC variant; return summary dict."""
    script = 'monte_carlo_15dte.py' if dte == '15' else 'monte_carlo.py'
    env = os.environ.copy()
    env.update({
        'PYTHONIOENCODING': 'utf-8',
        'N_ITER_OVERRIDE': str(n_iter),
        'WINDOWS_OVERRIDE': windows,
        'DEAD_HOLD_ENABLED': '1' if enabled else '0',
        'DEAD_HOLD_TRIGGER_PNL': str(trigger),
        'DEAD_HOLD_POPOUT_PNL': str(popout),
    })
    env.pop('MC_NO_MP', None)   # use multiprocessing if available
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, '-u', str(REPO_ROOT / script)],
        env=env, cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=timeout_sec,
    )
    elapsed = time.time() - t0

    summary = parse_summary(result.stdout, windows.split(','))
    return dict(
        name=name, enabled=enabled, trigger=trigger, popout=popout,
        elapsed_sec=round(elapsed, 1),
        windows=summary,
        stderr_tail=result.stderr[-500:] if result.returncode != 0 else None,
        returncode=result.returncode,
    )


def utility(window_data: dict, dd_target: float = 72.0, dd_ceil: float = 80.0) -> float:
    """Combined utility scalar:
      log(1 + mean_ret) clipped + linear penalty when DD breaches targets.

    Higher is better. Used to rank variants.
    """
    if not window_data:
        return float('-inf')

    import math
    log_returns = []
    dd_penalty = 0.0
    coll_penalty = 0.0

    for win, m in window_data.items():
        ret = m['mean_ret'] / 100.0   # convert from % to fraction
        # log return — clip negative for sanity
        if ret > -0.999:
            log_returns.append(math.log(1.0 + ret))
        else:
            log_returns.append(-7.0)
        # DD penalty: linear above target, steeper above ceiling
        dd = m['worst_dd']
        if dd > dd_target:
            dd_penalty += (dd - dd_target) * 0.05
        if dd > dd_ceil:
            dd_penalty += (dd - dd_ceil) * 0.5
        # Collapse penalty
        coll_penalty += m['p_coll'] * 0.5

    if not log_returns:
        return float('-inf')
    avg_log = sum(log_returns) / len(log_returns)
    return avg_log - dd_penalty - coll_penalty


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dte', choices=('30', '15'), required=True)
    parser.add_argument('--stage', choices=('1', '2'), default='1')
    parser.add_argument('--n', type=int, default=None)
    parser.add_argument('--windows', type=str, default=None,
                        help='Comma-separated list of windows (overrides stage default)')
    parser.add_argument('--pick', nargs='+', default=None,
                        help='For stage 2: variant names to validate (e.g. T-0.50_P-0.30)')
    parser.add_argument('--out', type=str, default=None)
    args = parser.parse_args()

    if args.stage == '1':
        n_iter = args.n or 100
        windows = args.windows or '22-now,5y,2025'
        variants = STAGE1_VARIANTS
    else:
        n_iter = args.n or 300
        windows = args.windows or '2021,2022,2023,2024,2025,dip,22-now,5y'
        if not args.pick:
            print('Stage 2 requires --pick <variant_name> ...', file=sys.stderr)
            sys.exit(1)
        # Always include OFF baseline for diff
        picked_names = ['OFF'] + [n for n in args.pick if n != 'OFF']
        variants = [v for v in STAGE1_VARIANTS if v[0] in picked_names]
        # Add any custom variants from --pick that aren't in STAGE1_VARIANTS
        for name in args.pick:
            if name not in [v[0] for v in variants]:
                # Parse 'T-0.50_P-0.30' format
                m = re.match(r'T(-?\d+\.\d+)_P(-?\d+\.\d+)', name)
                if m:
                    trig = float(m.group(1))
                    pop = float(m.group(2))
                    variants.append((name, True, trig, pop))

    out_file = args.out or f'experiments/dead_hold_sweep_{args.dte}dte_stage{args.stage}.json'
    out_path = REPO_ROOT / out_file

    print(f'\n=== Dead-hold sweep: {args.dte} DTE, Stage {args.stage} ===')
    print(f'    N_iter={n_iter}  windows={windows}')
    print(f'    {len(variants)} variants')
    print(f'    Output: {out_path}\n')

    all_results = []
    t_start = time.time()

    for i, (name, enabled, trigger, popout) in enumerate(variants, 1):
        print(f'[{i}/{len(variants)}] {name}: enabled={enabled} '
              f'trigger={trigger} popout={popout}', flush=True)
        try:
            res = run_variant(args.dte, name, enabled, trigger, popout,
                              windows, n_iter, timeout_sec=2400)
        except subprocess.TimeoutExpired:
            print(f'    TIMEOUT', flush=True)
            res = dict(name=name, enabled=enabled, trigger=trigger, popout=popout,
                       windows={}, elapsed_sec=2400, returncode=-1, stderr_tail='timeout')

        if res['returncode'] != 0:
            print(f'    FAILED rc={res["returncode"]}: {res.get("stderr_tail","")[:200]}', flush=True)
        else:
            util = utility(res['windows'])
            res['utility'] = round(util, 3)
            print(f'    Done in {res["elapsed_sec"]}s, utility={util:+.3f}', flush=True)
            for win, m in res['windows'].items():
                print(f'      {win:12s} ret={m["mean_ret"]:+12.1e}%  DD={m["worst_dd"]:5.1f}%  coll={m["p_coll"]:.1f}%')
        all_results.append(res)
        # Save incrementally
        out_path.write_text(json.dumps(all_results, indent=2))

    # Summary ranking
    print(f'\n=== Sweep complete in {time.time()-t_start:.1f}s ===')
    print(f'\n{"Variant":18s}  {"Util":>7s}  {"AvgDD":>7s}  {"MaxDD":>7s}  {"AvgCol":>7s}  {"5y MeanRet":>14s}')
    for r in sorted(all_results, key=lambda x: -x.get('utility', -1e9)):
        if r['returncode'] != 0 or not r['windows']:
            print(f'{r["name"]:18s}  FAILED')
            continue
        avg_dd = sum(m['worst_dd'] for m in r['windows'].values()) / max(1, len(r['windows']))
        max_dd = max(m['worst_dd'] for m in r['windows'].values())
        avg_col = sum(m['p_coll'] for m in r['windows'].values()) / max(1, len(r['windows']))
        ret_5y = r['windows'].get('5y', {}).get('mean_ret', float('nan'))
        print(f'{r["name"]:18s}  {r["utility"]:>+7.3f}  {avg_dd:>6.1f}%  {max_dd:>6.1f}%  {avg_col:>6.2f}%  {ret_5y:>+13.2e}%')

    print(f'\nResults saved to {out_path}')


if __name__ == '__main__':
    main()
