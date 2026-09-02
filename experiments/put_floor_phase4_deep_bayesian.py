"""Phase 4: Deep Bayesian iteration around K0.85_W-18_T42_G28.

Phase 3 found K0.85_W-18_T42_G28 as optimum (util +2.25 over K10_T35).
With fast infrastructure, run deeper exploration:

  Round A: 4D refined sweep around current best (~150 variants)
  Round B: explore score_floor (preserve deepest puts) (~40 variants)
  Round C: non-linear weakness curves (~30 variants)
  Round D: tanh weakness combined with the best linear settings (~15 variants)
  Round E: final tight grid around overall best (~30 variants)

Each variant ~0.8s -> total ~5 minutes for ~265 variants.

Utility weights (encoding user preferences):
  - WR15 lift on deep put buckets (<10, <15, <25): primary
  - Call WR regression: heavy penalty
  - Put N reduction toward call N parity: bonus
"""
from __future__ import annotations
import io, sys, time, itertools
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))

from fast_variant_runner import FastVariantRunner

BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]

PUT_BUCKET_WEIGHTS = {'<25': 0.10, '<20': 0.10, '<15': 0.20, '<10': 0.30, '<5': 0.30}
CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']
TARGET_PUT_CALL_RATIO = 1.0


def utility(stats, baseline):
    if not baseline: return 0.0
    bs = stats.get('bucketed_stats', {}); bb = baseline.get('bucketed_stats', {})
    put_wr_lift = 0.0
    for bk, w in PUT_BUCKET_WEIGHTS.items():
        v = (bs.get(bk, {}).get('win_rate_15d') or 0)
        bv = (bb.get(bk, {}).get('win_rate_15d') or 0)
        put_wr_lift += w * (v - bv)
    call_regression = 0.0
    for bk in CALL_BUCKETS:
        v = bs.get(bk, {}).get('win_rate_15d')
        bv = bb.get(bk, {}).get('win_rate_15d')
        if v is None or bv is None: continue
        delta = v - bv
        if delta < call_regression: call_regression = delta
    n_put = bs.get('<25', {}).get('sample_count') or 0
    n_call = bs.get('70+', {}).get('sample_count') or 0
    n_parity = -max(0, (n_put / max(1, n_call)) - TARGET_PUT_CALL_RATIO) * 0.05
    return put_wr_lift + 5.0 * min(0, call_regression) + n_parity


def fmt(v):
    suf = '_tanh' if v.get('use_tanh_weakness') else ''
    pwr = f"_p{v.get('weakness_power', 1.0):.1f}" if v.get('weakness_power', 1.0) != 1.0 else ''
    fl = f"_F{v.get('score_floor', 0)}" if v.get('score_floor', 0) > 0 else ''
    return (f"K{v['k']:.2f}_W{int(v['wadj_cutoff'])}_T{v['lift_target']}_G{v['score_gate']}"
            + suf + pwr + fl)


def run_round(runner, variants, baseline, label):
    print(f"\n{'='*125}")
    print(f"{label}: {len(variants)} variants (threaded)")
    print('='*125)
    t_start = time.time()
    batch = runner.evaluate_batch(variants)
    elapsed = time.time() - t_start
    print(f"  threaded eval: {elapsed:.1f}s ({elapsed/max(1,len(variants))*1000:.0f}ms/variant amortized)")

    print(f"{'Variant':<40}  {'<10 WR':>8}  {'<15 WR':>8}  {'<25 WR':>8}  {'70+ WR':>8}  {'N <25':>8}  {'Util':>7}")
    print('-' * 125)
    results = []
    for v, stats in batch:
        u = utility(stats, baseline)
        results.append((v, u, stats))
    results_for_print = sorted(results, key=lambda x: -x[1])
    for v, u, stats in results_for_print[:25]:
        bs = stats.get('bucketed_stats', {})
        wr10 = bs.get('<10', {}).get('win_rate_15d') or 0
        wr15 = bs.get('<15', {}).get('win_rate_15d') or 0
        wr25 = bs.get('<25', {}).get('win_rate_15d') or 0
        wr70 = bs.get('70+', {}).get('win_rate_15d') or 0
        n25 = bs.get('<25', {}).get('sample_count') or 0
        print(f"{fmt(v):<40}  {wr10:>7.1f}%  {wr15:>7.1f}%  {wr25:>7.1f}%  {wr70:>7.1f}%  {n25:>8}  {u:>+7.2f}",
              flush=True)
    print(f"\n{label} top 5:")
    for v, u, _ in results_for_print[:5]:
        print(f"  {fmt(v):<40}  util={u:+.2f}")
    return results_for_print


def main():
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load()

    # Establish K0.85_W-18_T42_G28 baseline (Phase 3 winner)
    print("\nEstablishing Phase 3 winner baseline (K0.85_W-18_T42_G28)...")
    baseline = runner.evaluate_floor(k=0.85, wadj_cutoff=-18.0, score_gate=28,
                                      lift_target=42, use_tanh_weakness=False)
    bs = baseline['bucketed_stats']
    print(f"  baseline: <10 {bs.get('<10', {}).get('win_rate_15d', 0):.1f}%  "
          f"<15 {bs.get('<15', {}).get('win_rate_15d', 0):.1f}%  "
          f"<25 {bs.get('<25', {}).get('win_rate_15d', 0):.1f}%  "
          f"70+ {bs.get('70+', {}).get('win_rate_15d', 0):.1f}%  "
          f"N<25 {bs.get('<25', {}).get('sample_count') or 0}")

    all_results = []

    # ── Round A: refined 4D sweep around current best ─────────────────
    rA = []
    for k in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00]:
        for wc in [-15, -16, -17, -18, -19, -20, -22]:
            for lt in [38, 40, 42, 45, 48, 50]:
                for sg in [25, 28, 30, 32]:
                    if abs(k - 0.85) > 0.10 and abs(wc + 18) > 2 and abs(lt - 42) > 5 and abs(sg - 28) > 2:
                        continue  # skip far corners, focus near current best
                    rA.append({'k': k, 'wadj_cutoff': float(wc), 'score_gate': sg,
                               'lift_target': lt, 'use_tanh_weakness': False})
    # Dedupe
    seen = set(); rA_d = []
    for v in rA:
        key = (v['k'], v['wadj_cutoff'], v['score_gate'], v['lift_target'])
        if key in seen: continue
        seen.add(key); rA_d.append(v)
    print(f"\nRound A grid: {len(rA_d)} variants")
    res_A = run_round(runner, rA_d, baseline, 'Round A — 4D refined sweep')
    all_results += res_A

    # ── Round B: score_floor — preserve deepest puts ──────────────────
    top_a = res_A[0][0]
    rB = []
    for fl in [3, 5, 8, 10, 12, 15]:
        for k in [top_a['k'] - 0.05, top_a['k'], top_a['k'] + 0.05]:
            for lt in [top_a['lift_target'] - 4, top_a['lift_target'], top_a['lift_target'] + 4]:
                k = max(0.5, min(1.0, k))
                rB.append({'k': k, 'wadj_cutoff': top_a['wadj_cutoff'],
                           'score_gate': top_a['score_gate'], 'lift_target': lt,
                           'use_tanh_weakness': False, 'score_floor': fl})
    res_B = run_round(runner, rB, baseline, 'Round B — score_floor preserves deepest puts')
    all_results += res_B

    # ── Round C: non-linear weakness curves ───────────────────────────
    rC = []
    for pwr in [0.5, 0.7, 1.5, 2.0, 3.0]:
        for k in [top_a['k'], 1.0]:
            for lt in [42, 45, 48]:
                rC.append({'k': k, 'wadj_cutoff': top_a['wadj_cutoff'],
                           'score_gate': top_a['score_gate'], 'lift_target': lt,
                           'use_tanh_weakness': False, 'weakness_power': pwr})
    res_C = run_round(runner, rC, baseline, 'Round C — non-linear weakness shaping')
    all_results += res_C

    # ── Round D: tanh weakness around best ────────────────────────────
    rD = []
    for k in [0.80, 0.85, 0.90, 0.95, 1.0]:
        for lt in [38, 42, 45, 48, 52]:
            rD.append({'k': k, 'wadj_cutoff': -18.0, 'score_gate': top_a['score_gate'],
                       'lift_target': lt, 'use_tanh_weakness': True})
    res_D = run_round(runner, rD, baseline, 'Round D — tanh weakness curve (smooth)')
    all_results += res_D

    # ── Round E: final tight grid around overall best ────────────────
    all_results.sort(key=lambda x: -x[1])
    best = all_results[0][0]
    rE = []
    base_k = best['k']
    base_wc = best['wadj_cutoff']
    base_lt = best['lift_target']
    base_sg = best['score_gate']
    for k_off in [-0.03, 0, 0.03]:
        for wc_off in [-1, 0, 1]:
            for lt_off in [-2, 0, 2]:
                for sg_off in [-1, 0, 1]:
                    v = {
                        'k': max(0.5, min(1.0, base_k + k_off)),
                        'wadj_cutoff': base_wc + wc_off,
                        'score_gate': max(20, base_sg + sg_off),
                        'lift_target': base_lt + lt_off,
                        'use_tanh_weakness': best.get('use_tanh_weakness', False),
                        'weakness_power': best.get('weakness_power', 1.0),
                        'score_floor': best.get('score_floor', 0),
                    }
                    rE.append(v)
    res_E = run_round(runner, rE, baseline, 'Round E — tight grid around overall best')
    all_results += res_E

    all_results.sort(key=lambda x: -x[1])
    print(f"\n{'='*125}")
    print("FINAL TOP 10 (vs K0.85_W-18_T42_G28 baseline)")
    print('='*125)
    for v, u, stats in all_results[:10]:
        bs = stats['bucketed_stats']
        wr10 = bs.get('<10', {}).get('win_rate_15d', 0)
        wr15 = bs.get('<15', {}).get('win_rate_15d', 0)
        wr25 = bs.get('<25', {}).get('win_rate_15d', 0)
        wr70 = bs.get('70+', {}).get('win_rate_15d', 0)
        n25 = bs.get('<25', {}).get('sample_count') or 0
        print(f"  {fmt(v):<40}  util={u:+.2f}  "
              f"<10={wr10:.1f}%  <15={wr15:.1f}%  <25={wr25:.1f}%  70+={wr70:.1f}%  N<25={n25}")


if __name__ == '__main__':
    main()
