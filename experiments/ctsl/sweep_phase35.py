"""CTSL Phase 3.5 — DD-objective staged LHS sweep.

Re-tunes CTSL parameters with 5y WorstDD as the objective function (NOT
alpha_capture as in Phase 2.5). Per the new ship gates in
.claude/docs/assessment-backtest.md "Why DD is primary, compound is
secondary", DD is the only metric that translates to real trader outcomes;
compound at 1e30+% scale is theoretical.

Phase 2.5 winner was tuned for portfolio allocation capture — wrong objective.
Phase 3 showed the Phase 2.5 winner stacks marginally on DD (B: 5y=70.6%,
−1.3pp vs A=71.9%). Phase 3.5 asks: can we do better with DD-tuned CTSL?

Tests in B configuration (CTSL+CT_PROMOTE stack — Phase 3 showed this is the
DD-winning direction). Inner loop runs MC for each variant to estimate 5y DD.

Stage 1 — Blast radius:  ~30 LHS variants × N=200 × 8 windows  (~50 min)
Stage 2 — Drill:         ~8 variants × N=500 × 8 windows         (~40 min)

Total: ~90 min compute.

Variant params (call side, mirror for puts):
  - trend_max ∈ [15, 25]
  - target ∈ [85, 105]
  - alpha ∈ [0.30, 1.20]
  - trend_power ∈ [0.5, 3.0]
  - tier_floor ∈ [70, 90]
  - score_norm_weight ∈ [-1.0, 1.0]
  - score_norm_power ∈ [0.5, 3.0]

Objective: minimize 5y WorstDD with secondary penalty for per-window DD
regression > 5pp vs A baseline (P4 stability gate).
"""
from __future__ import annotations
import io, sys, json, os, subprocess, re, time
from pathlib import Path
import numpy as np
from scipy.stats import qmc

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / 'experiments' / 'ctsl'
RESULTS_DIR = LOG_DIR / 'phase35_runs'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

WINDOWS = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

# A baseline (CT_PROMOTE only) DDs for delta calc — from Phase 3 results
A_BASELINE_DD = {
    '2021': 55.3, '2022': 71.7, '2023': 71.2, '2024': 66.6,
    '2025': 57.0, 'dip': 48.9, '22-now': 71.7, '5y': 71.9,
}


# Param specs: (name, lo, hi, is_int)
CALL_SPEC = [
    ('CTSL_CALL_TREND_MAX',         15,    25,   True),
    ('CTSL_CALL_TARGET',            85.0,  105.0, False),
    ('CTSL_CALL_ALPHA',             0.30,  1.20,  False),
    ('CTSL_CALL_TREND_POWER',       0.5,   3.0,   False),
    ('CTSL_CALL_TIER_FLOOR',        70.0,  90.0,  False),
    ('CTSL_CALL_SCORE_NORM_WEIGHT', -1.0,  1.0,   False),
    ('CTSL_CALL_SCORE_NORM_POWER',  0.5,   3.0,   False),
]
PUT_SPEC = [
    ('CTSL_PUT_TREND_MIN',          75,    95,    True),
    ('CTSL_PUT_TARGET',             -5.0,  20.0,  False),
    ('CTSL_PUT_ALPHA',              0.30,  1.20,  False),
    ('CTSL_PUT_TREND_POWER',        0.5,   3.0,   False),
    ('CTSL_PUT_TIER_CEILING',       15.0,  28.0,  False),
    ('CTSL_PUT_SCORE_NORM_WEIGHT', -1.0,  1.0,    False),
    ('CTSL_PUT_SCORE_NORM_POWER',  0.5,    3.0,   False),
]
SPEC = CALL_SPEC + PUT_SPEC


def lhs_samples(n: int, spec: list, seed: int) -> list[dict]:
    sampler = qmc.LatinHypercube(d=len(spec), seed=seed)
    raw = sampler.random(n=n)
    out = []
    for sample in raw:
        cfg = {}
        for i, (name, lo, hi, isint) in enumerate(spec):
            x = lo + sample[i] * (hi - lo)
            if isint:
                x = int(round(x))
            cfg[name] = x
        out.append(cfg)
    return out


def run_mc(label: str, params: dict, n_iter: int) -> dict:
    """Run monte_carlo.py in B config (CTSL+CT_PROMOTE) with these params."""
    env = {**os.environ}
    env.update({
        'PYTHONIOENCODING': 'utf-8',
        'N_ITER_OVERRIDE': str(n_iter),
        'WINDOWS_OVERRIDE': ','.join(WINDOWS),
        'ALGORITHM_VERSION_PIN': 'f274eb6',  # v46
        'CTSL_ENABLED': '1',
        'CT_PROMOTE': '1',  # B config — stack
    })
    for k, v in params.items():
        env[k] = str(v)

    log_path = RESULTS_DIR / f'{label}.log'
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as f:
        proc = subprocess.run(
            [sys.executable, '-u', str(ROOT / 'monte_carlo.py')],
            env=env, stdout=f, stderr=subprocess.STDOUT,
            timeout=1200,  # 20 min hard cap per variant
        )
    dur = time.time() - t0

    text = log_path.read_text(encoding='utf-8', errors='replace')
    summary = parse_summary(text)
    return {
        'label': label,
        'params': dict(params),
        'duration_s': dur,
        'rc': proc.returncode,
        'summary': summary,
    }


def parse_summary(text: str) -> dict[str, dict]:
    """Parse SUMMARY block — return {window: {worst_dd, mean_dd, p_collapse}}."""
    results = {}
    in_summary = False
    for line in text.splitlines():
        if 'SUMMARY - Mean Return' in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        m = re.match(
            r'^\s*(\S+)\s+([+-][\d,\.]+)%\s+([+-][\d,\.]+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%\s+(\d+\.\d+)%',
            line,
        )
        if m:
            results[m.group(1)] = {
                'worst_dd': float(m.group(4)),
                'mean_dd': float(m.group(5)),
                'p_collapse': float(m.group(6)),
            }
    return results


def score_variant(result: dict) -> float:
    """Composite DD-objective score. Lower is better.

    Primary: 5y WorstDD (the locked decision metric).
    Secondary: penalty for per-window DD regression >5pp vs A baseline (P4).
    Hard fail: any window with non-zero collapse → return huge penalty.
    """
    s = result.get('summary', {})
    if '5y' not in s:
        return 1e6  # parse failure
    if any(s.get(w, {}).get('p_collapse', 0) > 0 for w in WINDOWS):
        return 1e5  # collapse anywhere = hard fail

    primary = s['5y']['worst_dd']
    penalty = 0.0
    for w in WINDOWS:
        if w not in s:
            continue
        delta = s[w]['worst_dd'] - A_BASELINE_DD.get(w, s[w]['worst_dd'])
        if delta > 5.0:  # >5pp regression
            penalty += (delta - 5.0) * 2.0
    return primary + penalty


def short_label(params: dict, prefix: str = 's1') -> str:
    """Compact human label for a variant."""
    return (f"{prefix}_tm{params['CTSL_CALL_TREND_MAX']}"
            f"_tg{params['CTSL_CALL_TARGET']:.0f}"
            f"_a{params['CTSL_CALL_ALPHA']:.2f}"
            f"_p{params['CTSL_CALL_TREND_POWER']:.2f}"
            f"_pmin{params['CTSL_PUT_TREND_MIN']}")


def derive_drill_ranges(top_results: list, spec: list, slack_pct: float = 0.20) -> list:
    """Tighten ranges to bbox of top results + slack_pct on each side."""
    new_spec = []
    for name, lo, hi, isint in spec:
        vals = [r['params'][name] for r in top_results]
        if not vals:
            new_spec.append((name, lo, hi, isint))
            continue
        vmin, vmax = min(vals), max(vals)
        span = max(1e-6, vmax - vmin)
        slack = span * slack_pct
        new_lo = max(lo, vmin - slack)
        new_hi = min(hi, vmax + slack)
        if new_hi - new_lo < (hi - lo) * 0.05:
            cen = (new_lo + new_hi) / 2
            new_lo = max(lo, cen - (hi - lo) * 0.025)
            new_hi = min(hi, cen + (hi - lo) * 0.025)
        new_spec.append((name, new_lo, new_hi, isint))
    return new_spec


def stage1_broad(n: int = 30, n_iter: int = 200) -> list[dict]:
    print('\n' + '=' * 78, flush=True)
    print(f' STAGE 1 — broad LHS, {n} variants × N={n_iter} × 8 windows', flush=True)
    print('=' * 78, flush=True)
    samples = lhs_samples(n, SPEC, seed=42)
    results = []
    for i, params in enumerate(samples):
        label = short_label(params, prefix=f's1_{i:02d}')
        print(f'\n[s1 {i+1}/{n}] {label}', flush=True)
        r = run_mc(label, params, n_iter)
        score = score_variant(r)
        r['score'] = score
        results.append(r)
        s5y = r['summary'].get('5y', {})
        print(f'  duration={r["duration_s"]:.0f}s  5y_DD={s5y.get("worst_dd","?"):.1f}%  score={score:.2f}',
              flush=True)
    return results


def stage2_drill(top_results: list, n: int = 8, n_iter: int = 500) -> list[dict]:
    print('\n' + '=' * 78, flush=True)
    print(f' STAGE 2 — drill, {n} variants × N={n_iter} × 8 windows', flush=True)
    print('=' * 78, flush=True)
    drill_spec = derive_drill_ranges(top_results, SPEC, slack_pct=0.25)
    print('  drill ranges:', flush=True)
    for name, lo, hi, isint in drill_spec:
        print(f'    {name:<28}: [{lo:.3f}, {hi:.3f}]', flush=True)

    samples = lhs_samples(n, drill_spec, seed=137)
    results = []
    for i, params in enumerate(samples):
        label = short_label(params, prefix=f's2_{i:02d}')
        print(f'\n[s2 {i+1}/{n}] {label}', flush=True)
        r = run_mc(label, params, n_iter)
        score = score_variant(r)
        r['score'] = score
        results.append(r)
        s5y = r['summary'].get('5y', {})
        s22 = r['summary'].get('22-now', {})
        print(f'  duration={r["duration_s"]:.0f}s  5y_DD={s5y.get("worst_dd","?"):.1f}%  '
              f'22-now_DD={s22.get("worst_dd","?"):.1f}%  score={score:.2f}',
              flush=True)
    return results


def report(stage_results: list[dict], stage_name: str):
    sorted_r = sorted(stage_results, key=lambda r: r['score'])
    print(f'\n[{stage_name}] top 8 by 5y WorstDD (score = 5y_DD + per-window-regression-penalty):',
          flush=True)
    print(f'  {"label":<32} {"5y DD":>7} {"22-n DD":>9} {"22  DD":>7} {"23  DD":>7} {"24  DD":>7} {"score":>7}', flush=True)
    for r in sorted_r[:8]:
        s = r['summary']
        print(f'  {r["label"]:<32} {s.get("5y", {}).get("worst_dd", -1):>6.1f}% '
              f'{s.get("22-now", {}).get("worst_dd", -1):>8.1f}% '
              f'{s.get("2022", {}).get("worst_dd", -1):>6.1f}% '
              f'{s.get("2023", {}).get("worst_dd", -1):>6.1f}% '
              f'{s.get("2024", {}).get("worst_dd", -1):>6.1f}% '
              f'{r["score"]:>7.2f}', flush=True)


def main():
    t0 = time.time()
    print(f'\nA baseline reference (Phase 3 v46): 5y DD = {A_BASELINE_DD["5y"]:.1f}%, 22-now DD = {A_BASELINE_DD["22-now"]:.1f}%', flush=True)

    s1_results = stage1_broad(n=30, n_iter=200)
    report(s1_results, 's1')

    # Persist
    with open(LOG_DIR / 'phase35_stage1.json', 'w', encoding='utf-8') as f:
        json.dump(s1_results, f, default=str, indent=1)

    # Pick top quartile (top 8) for drill
    s1_sorted = sorted(s1_results, key=lambda r: r['score'])
    top_quartile = s1_sorted[: max(8, len(s1_sorted) // 4)]
    print(f'\n[stage 1->2 transition] {len(top_quartile)} top variants forwarded to drill', flush=True)

    s2_results = stage2_drill(top_quartile, n=8, n_iter=500)
    report(s2_results, 's2')

    with open(LOG_DIR / 'phase35_stage2.json', 'w', encoding='utf-8') as f:
        json.dump(s2_results, f, default=str, indent=1)

    # Pick winner
    s2_sorted = sorted(s2_results, key=lambda r: r['score'])
    winner = s2_sorted[0]

    print('\n' + '#' * 78, flush=True)
    print(' PHASE 3.5 WINNER (B config: CTSL_ENABLED=1, CT_PROMOTE=1)', flush=True)
    print('#' * 78, flush=True)
    print(f'\nlabel:  {winner["label"]}', flush=True)
    print(f'score:  {winner["score"]:.2f}  (5y_DD + per-window penalty)', flush=True)
    print(f'5y_DD:  {winner["summary"]["5y"]["worst_dd"]:.1f}%  '
          f'(A baseline: {A_BASELINE_DD["5y"]:.1f}%, '
          f'Δ={winner["summary"]["5y"]["worst_dd"]-A_BASELINE_DD["5y"]:+.2f}pp)', flush=True)
    print('\nper-window WorstDD vs A baseline:', flush=True)
    for w in WINDOWS:
        ws = winner['summary'].get(w, {})
        wd = ws.get('worst_dd', -1)
        ad = A_BASELINE_DD.get(w, -1)
        delta = wd - ad if wd > 0 and ad > 0 else None
        delta_str = f'{delta:+.2f}pp' if delta is not None else '?'
        print(f'  {w:<10}  variant={wd:>5.1f}%  A_baseline={ad:>5.1f}%  Δ={delta_str}', flush=True)

    print('\nparams:', flush=True)
    for k, v in winner['params'].items():
        if isinstance(v, float):
            print(f'  {k:<30} = {v:+.4f}', flush=True)
        else:
            print(f'  {k:<30} = {v}', flush=True)

    print(f'\n[done] total runtime: {(time.time()-t0)/60:.1f} min', flush=True)


if __name__ == '__main__':
    main()
