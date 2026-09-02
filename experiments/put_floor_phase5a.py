"""Phase 5a: ablation + multi-stage + call-side mirror.

Baseline: K0.95_W-17_T50_G28 (Phase 4 winner).

Sweep dimensions:
  Group I — Multi-stage put lift (deep cohort gets stronger lift)
  Group II — Call-side ceiling lift (mirror, mild)
  Group III — Combined put + call
  Group IV — Score-gate extension (lift even mid-range puts 28-32)
  Group V — Pre-regime gating (only lift when pre_regime score also bearish)

Output one row per variant streaming as we go. ~150 variants in ~3 min.
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


def fmt_label(prefix, **kwargs):
    parts = [prefix]
    for k, v in kwargs.items():
        if isinstance(v, float):
            parts.append(f"{k}{v:.2f}")
        else:
            parts.append(f"{k}{v}")
    return '_'.join(parts)


def run(runner, variants, baseline, label):
    print(f"\n{'='*125}", flush=True)
    print(f"{label}: {len(variants)} variants", flush=True)
    print('='*125, flush=True)
    print(f"{'Variant':<55} {'<10':>7} {'<15':>7} {'<25':>7} {'70+':>7} {'85+':>7} {'95+':>7} {'N<25':>7} {'N70+':>7} {'Util':>7}", flush=True)
    print('-' * 125, flush=True)
    results = []
    t0 = time.time()
    for lbl, v in variants:
        s = runner.evaluate_general(**v)
        u = utility(s, baseline)
        results.append((lbl, v, u, s))
        bs = s.get('bucketed_stats', {})
        wr10 = bs.get('<10', {}).get('win_rate_15d') or 0
        wr15 = bs.get('<15', {}).get('win_rate_15d') or 0
        wr25 = bs.get('<25', {}).get('win_rate_15d') or 0
        wr70 = bs.get('70+', {}).get('win_rate_15d') or 0
        wr85 = bs.get('85+', {}).get('win_rate_15d') or 0
        wr95 = bs.get('95+', {}).get('win_rate_15d') or 0
        n25 = bs.get('<25', {}).get('sample_count') or 0
        n70 = bs.get('70+', {}).get('sample_count') or 0
        print(f"{lbl:<55} {wr10:>6.1f}% {wr15:>6.1f}% {wr25:>6.1f}% {wr70:>6.1f}% {wr85:>6.1f}% {wr95:>6.1f}% {n25:>7} {n70:>7} {u:>+7.2f}", flush=True)
    print(f"\n{label}: {time.time()-t0:.1f}s elapsed | top 5:")
    for lbl, v, u, _ in sorted(results, key=lambda x: -x[2])[:5]:
        print(f"  {lbl:<55} util={u:+.2f}")
    return sorted(results, key=lambda x: -x[2])


def main():
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)

    # Baseline = Phase 4 winner K0.95_W-17_T50_G28
    print("\nBaseline: Phase 4 winner K0.95_W-17_T50_G28")
    baseline = runner.evaluate_general(
        k=0.95, wadj_cutoff=-17.0, score_gate=28, lift_target=50, use_tanh_weakness=False
    )
    bs = baseline['bucketed_stats']
    print(f"  baseline: <10={bs.get('<10',{}).get('win_rate_15d',0):.1f}% "
          f"<15={bs.get('<15',{}).get('win_rate_15d',0):.1f}% "
          f"<25={bs.get('<25',{}).get('win_rate_15d',0):.1f}% "
          f"70+={bs.get('70+',{}).get('win_rate_15d',0):.1f}% "
          f"N<25={bs.get('<25',{}).get('sample_count') or 0} "
          f"N70+={bs.get('70+',{}).get('sample_count') or 0}")

    BASE = {'k': 0.95, 'wadj_cutoff': -17.0, 'score_gate': 28, 'lift_target': 50,
            'use_tanh_weakness': False}

    all_res = []

    # ── Group I: Multi-stage put lift ───────────────────────────────
    g1 = []
    for d_k in [1.0, 1.0]:  # full lift on deep
        for d_gate in [5, 8, 10]:
            for d_target in [40, 50, 60, 70]:
                lbl = f"P5a_I_dk{d_k:.1f}_dg{d_gate}_dt{d_target}"
                g1.append((lbl, {**BASE, 'deep_k': d_k, 'deep_score_gate': d_gate, 'deep_lift_target': d_target}))
    all_res += run(runner, g1, baseline, 'Group I — Multi-stage put lift (extra deep boost)')

    # ── Group II: Call-side ceiling lift mirror ─────────────────────
    g2 = []
    for c_k in [0.0, 0.2, 0.5]:
        for c_cut in [13, 16, 20]:
            for c_gate in [70, 75, 80]:
                for c_target in [60, 65, 70]:
                    lbl = f"P5a_II_ck{c_k:.1f}_cw{c_cut}_cg{c_gate}_ct{c_target}"
                    g2.append((lbl, {**BASE, 'call_k': c_k, 'call_wadj_cutoff': c_cut, 'call_gate': c_gate, 'call_lift_target': c_target}))
    # Drop duplicates (when c_k=0, all c_X options are identical)
    seen = set(); g2_d = []
    for lbl, v in g2:
        key = (v.get('call_k', 0.0),) + ((v.get('call_wadj_cutoff'), v.get('call_gate'), v.get('call_lift_target')) if v.get('call_k', 0) > 0 else ())
        if key in seen: continue
        seen.add(key); g2_d.append((lbl, v))
    all_res += run(runner, g2_d, baseline, 'Group II — Call ceiling lift mirror')

    # ── Group III: Score-gate extension (lift mid-range puts 28-32) ──
    g3 = []
    for sg in [25, 28, 30, 32, 35]:
        for lt in [42, 50, 55]:
            lbl = f"P5a_III_sg{sg}_lt{lt}"
            g3.append((lbl, {**BASE, 'score_gate': sg, 'lift_target': lt}))
    all_res += run(runner, g3, baseline, 'Group III — Score-gate extension')

    # ── Group IV: Combined deep boost + call ceiling ─────────────────
    top_g1 = [r for r in all_res[:10] if 'P5a_I_' in r[0]]
    if top_g1:
        best_g1 = top_g1[0][1]
        g4 = []
        for c_k in [0.2, 0.3]:
            for c_cut in [16, 20]:
                lbl = f"P5a_IV_DEEP_CALL_ck{c_k:.1f}_cw{c_cut}"
                g4.append((lbl, {**best_g1, 'call_k': c_k, 'call_wadj_cutoff': c_cut, 'call_gate': 75, 'call_lift_target': 65}))
        all_res += run(runner, g4, baseline, 'Group IV — Deep boost + call ceiling combined')

    # ── Final ranking ───────────────────────────────────────────────
    all_res.sort(key=lambda x: -x[2])
    print(f"\n{'='*125}")
    print(f"FINAL TOP 15 (Phase 5a vs Phase 4 baseline)")
    print('='*125)
    for lbl, v, u, s in all_res[:15]:
        bs = s.get('bucketed_stats', {})
        wr10 = bs.get('<10', {}).get('win_rate_15d') or 0
        wr15 = bs.get('<15', {}).get('win_rate_15d') or 0
        wr25 = bs.get('<25', {}).get('win_rate_15d') or 0
        wr70 = bs.get('70+', {}).get('win_rate_15d') or 0
        wr95 = bs.get('95+', {}).get('win_rate_15d') or 0
        n25 = bs.get('<25', {}).get('sample_count') or 0
        n70 = bs.get('70+', {}).get('sample_count') or 0
        print(f"  {lbl:<55} util={u:+.2f}  "
              f"<10={wr10:.1f}% <15={wr15:.1f}% <25={wr25:.1f}% "
              f"70+={wr70:.1f}% 95+={wr95:.1f}% "
              f"N<25={n25} N70+={n70}")


if __name__ == '__main__':
    main()
