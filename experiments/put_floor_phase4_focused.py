"""Phase 4 (focused): tighter Bayesian iteration around K0.85_W-18_T42_G28.

Designed to complete in <3 minutes. Tests 80-100 variants spanning:
  - 4D core sweep around current best
  - score_floor variants (preserve deepest puts)
  - non-linear weakness curves
  - tight final grid around overall best

Sequential evaluation (Polars internal threading already saturates CPU).
Output flushes after each variant for live progress.
"""
from __future__ import annotations
import io, sys, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))

from fast_variant_runner import FastVariantRunner

PUT_BUCKET_WEIGHTS = {'<25': 0.10, '<20': 0.10, '<15': 0.20, '<10': 0.30, '<5': 0.30}
CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']


def utility(stats, baseline):
    if not baseline: return 0.0
    bs = stats.get('bucketed_stats', {}); bb = baseline.get('bucketed_stats', {})
    put_lift = sum(w * ((bs.get(bk, {}).get('win_rate_15d') or 0)
                        - (bb.get(bk, {}).get('win_rate_15d') or 0))
                   for bk, w in PUT_BUCKET_WEIGHTS.items())
    call_reg = 0.0
    for bk in CALL_BUCKETS:
        v = bs.get(bk, {}).get('win_rate_15d')
        bv = bb.get(bk, {}).get('win_rate_15d')
        if v is None or bv is None: continue
        if (v - bv) < call_reg: call_reg = v - bv
    n_put = bs.get('<25', {}).get('sample_count') or 0
    n_call = bs.get('70+', {}).get('sample_count') or 0
    n_par = -max(0, (n_put / max(1, n_call)) - 1.0) * 0.05
    return put_lift + 5.0 * min(0, call_reg) + n_par


def fmt(v):
    suf = '_tanh' if v.get('use_tanh_weakness') else ''
    pwr = f"_p{v.get('weakness_power', 1.0):.1f}" if v.get('weakness_power', 1.0) != 1.0 else ''
    fl  = f"_F{v.get('score_floor', 0)}" if v.get('score_floor', 0) > 0 else ''
    return f"K{v['k']:.2f}_W{int(v['wadj_cutoff'])}_T{v['lift_target']}_G{v['score_gate']}{suf}{pwr}{fl}"


def run_round(runner, variants, baseline, label):
    print(f"\n{'='*120}")
    print(f"{label}: {len(variants)} variants")
    print('='*120, flush=True)
    print(f"{'Variant':<38} {'<10':>7} {'<15':>7} {'<25':>7} {'70+':>7} {'N<25':>8} {'Util':>7}", flush=True)
    print('-' * 120, flush=True)
    results = []
    t0 = time.time()
    for v in variants:
        s = runner.evaluate_floor(**v)
        u = utility(s, baseline)
        results.append((v, u, s))
        bs = s.get('bucketed_stats', {})
        wr10 = bs.get('<10', {}).get('win_rate_15d') or 0
        wr15 = bs.get('<15', {}).get('win_rate_15d') or 0
        wr25 = bs.get('<25', {}).get('win_rate_15d') or 0
        wr70 = bs.get('70+', {}).get('win_rate_15d') or 0
        n25 = bs.get('<25', {}).get('sample_count') or 0
        print(f"{fmt(v):<38} {wr10:>6.1f}% {wr15:>6.1f}% {wr25:>6.1f}% {wr70:>6.1f}% {n25:>8} {u:>+7.2f}", flush=True)
    print(f"\n{label}: {time.time()-t0:.1f}s elapsed | top 3:", flush=True)
    for v, u, _ in sorted(results, key=lambda x: -x[1])[:3]:
        print(f"  {fmt(v):<38}  util={u:+.2f}")
    return sorted(results, key=lambda x: -x[1])


def main():
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)

    print("\nBaseline = Phase 3 winner K0.85_W-18_T42_G28")
    baseline = runner.evaluate_floor(k=0.85, wadj_cutoff=-18.0, score_gate=28,
                                      lift_target=42, use_tanh_weakness=False)
    bs = baseline['bucketed_stats']
    print(f"  baseline: <10={bs.get('<10',{}).get('win_rate_15d',0):.1f}% "
          f"<15={bs.get('<15',{}).get('win_rate_15d',0):.1f}% "
          f"<25={bs.get('<25',{}).get('win_rate_15d',0):.1f}% "
          f"70+={bs.get('70+',{}).get('win_rate_15d',0):.1f}% "
          f"N<25={bs.get('<25',{}).get('sample_count') or 0}")

    all_res = []

    # Round A: tight 4D around current best
    rA = []
    for k in [0.80, 0.85, 0.90, 0.95]:
        for wc in [-16, -17, -18, -19, -20]:
            for lt in [38, 40, 42, 44, 46]:
                for sg in [25, 28, 30]:
                    rA.append({'k': k, 'wadj_cutoff': float(wc), 'score_gate': sg,
                               'lift_target': lt, 'use_tanh_weakness': False})
    all_res += run_round(runner, rA, baseline, 'Round A — 4D refined sweep (300 variants)')

    # Round B: score_floor — preserve deepest puts
    top_a = all_res[0][0] if all_res else {'k': 0.85, 'wadj_cutoff': -18, 'score_gate': 28, 'lift_target': 42}
    rB = []
    for fl in [3, 5, 8, 10, 15]:
        for k in [top_a['k'], top_a['k'] + 0.05]:
            k = min(1.0, max(0.6, k))
            rB.append({'k': k, 'wadj_cutoff': top_a['wadj_cutoff'],
                       'score_gate': top_a['score_gate'], 'lift_target': top_a['lift_target'],
                       'use_tanh_weakness': False, 'score_floor': fl})
    all_res += run_round(runner, rB, baseline, 'Round B — score_floor preserves deepest puts')

    # Round C: non-linear weakness
    rC = []
    for pwr in [0.5, 0.7, 1.5, 2.0]:
        for k in [top_a['k']]:
            for lt in [top_a['lift_target'] - 4, top_a['lift_target'], top_a['lift_target'] + 4]:
                rC.append({'k': k, 'wadj_cutoff': top_a['wadj_cutoff'],
                           'score_gate': top_a['score_gate'], 'lift_target': lt,
                           'use_tanh_weakness': False, 'weakness_power': pwr})
    all_res += run_round(runner, rC, baseline, 'Round C — non-linear weakness')

    # Round D: tanh weakness
    rD = []
    for k in [0.85, 0.90, 1.0]:
        for lt in [40, 42, 45, 48]:
            rD.append({'k': k, 'wadj_cutoff': -18.0, 'score_gate': top_a['score_gate'],
                       'lift_target': lt, 'use_tanh_weakness': True})
    all_res += run_round(runner, rD, baseline, 'Round D — tanh weakness')

    # Final ranking
    all_res.sort(key=lambda x: -x[1])
    print(f"\n{'='*120}")
    print(f"FINAL TOP 10 (vs K0.85_W-18_T42_G28 baseline)")
    print('='*120)
    for v, u, s in all_res[:10]:
        bs = s['bucketed_stats']
        print(f"  {fmt(v):<38} util={u:+.2f}  "
              f"<10={bs.get('<10',{}).get('win_rate_15d',0):.1f}% "
              f"<15={bs.get('<15',{}).get('win_rate_15d',0):.1f}% "
              f"<25={bs.get('<25',{}).get('win_rate_15d',0):.1f}% "
              f"70+={bs.get('70+',{}).get('win_rate_15d',0):.1f}% "
              f"N<25={bs.get('<25',{}).get('sample_count') or 0}")


if __name__ == '__main__':
    main()
