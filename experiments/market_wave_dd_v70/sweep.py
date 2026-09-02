"""
MWDD — McClellan (breadth-momentum / "market wave") flat-band CALL-alloc dampener.
Stage-3 sweep (v70 Apex), 2026-06-05 overnight.

Mining (experiments/market_wave_dd_v70/mine.py, N=300 tape) found, on the live
RXDD+SVR substrate:
  - The FLAT McClellan band (~0, the breadth-momentum analog of RXDD's VIX 20-28
    'slow-bleed') is the low-EV + DD-concentrated cohort (mpnl +0.046, conc 2.04),
    AND stays low-EV when VIX<20 (orthogonal to RXDD) and breadth>=40 (orthogonal to F3F).
  - Deep-NEGATIVE McClellan (breadth capitulation) and STRONG-positive (healthy momentum)
    are mean-reversion / velocity WINNERS -> must be left alone (Gaussian bump on McClellan).
  - Per-window robustness: contracting the flat band HELPS 2021/2023/2024/2025/22-now/5y/dip
    but HURTS the crash windows (2020_crash flat-band cohort +0.234). The crash harm is
    VIX-panic territory -> excluded via MWDD_VIX_PANIC (RXDD's panic-leave-alone trick).

Mechanism (monte_carlo.py): smooth Gaussian bump on the McClellan oscillator contracts
call alloc in the flat/topping band, no-op when disabled / McClellan missing / dd < DD_MIN
/ VIX >= panic. Knobs MWDD_MCC_C / MWDD_MCC_W / MWDD_DEPTH / MWDD_DD_MIN / MWDD_VIX_PANIC
(env-overridable, default OFF).

Goal = Pareto win: cut WorstDD on the bull/choppy windows (5y / 22-now / 2023 / 2024 / dip)
without cutting compound, collapse=0 on EVERY window incl 2020_crash (the panic-exclusion
must keep COVID untouched).

Holdout: standard MC windows end <= 2026-04-24 (CALIBRATION_CUTOFF removed 2026-06-05; n/a).

Usage (run via task queue, --cpu N):
  python -u experiments/market_wave_dd_v70/sweep.py --mode verify --n 200
  python -u experiments/market_wave_dd_v70/sweep.py --mode phaseB --n 100 --cand 16
  python -u experiments/market_wave_dd_v70/sweep.py --mode phaseC --n 300 --wins FULL --explicit <json>
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Top clean Phase-B candidates (N=100x6, 2026-06-05) — DD-focus leaders, collapse=0,
# compound flat-or-up. Span center 0 -> +13.5 and depth 0.19 -> 0.40.
PHASE_C_CANDS = [
    {'MWDD_MCC_C': -0.336, 'MWDD_MCC_W': 22.185, 'MWDD_DEPTH': 0.337, 'MWDD_DD_MIN': 0.128},  # B-c05 (best ddFoc, comp +0.041)
    {'MWDD_MCC_C': 11.578, 'MWDD_MCC_W': 20.632, 'MWDD_DEPTH': 0.396, 'MWDD_DD_MIN': 0.200},  # B-c10 (best dd5y, comp +0.041)
    {'MWDD_MCC_C': 13.503, 'MWDD_MCC_W': 24.441, 'MWDD_DEPTH': 0.187, 'MWDD_DD_MIN': 0.147},  # B-c02 (low depth, conservative)
]

WINS_B    = ['2020_crash', '2022', '2023', '2024', 'dip', '5y']
WINS_FULL = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
# MWDD targets the flat-McClellan bull/choppy topping churn (NOT crashes, which are
# VIX-panic-excluded) -> rank by DD reduction where the mechanism actually acts.
DD_FOCUS  = ['2023', '2024', 'dip', '5y', '22-now']


def lr(pct):
    v = 1.0 + pct / 100.0
    return math.log(v) if v > 1e-9 else -50.0


def score(base, cand, wins):
    foc = [w for w in DD_FOCUS if w in wins]
    return dict(
        coll=max(cand[w]['p_collapse'] for w in wins),
        dd_red=round(sum(base[w]['worst_dd'] - cand[w]['worst_dd'] for w in wins) / len(wins), 3),
        dd_red_focus=round(sum(base[w]['worst_dd'] - cand[w]['worst_dd'] for w in foc) / max(1, len(foc)), 3),
        dd_5y=round(base['5y']['worst_dd'] - cand['5y']['worst_dd'], 3) if '5y' in wins else None,
        dd_crash=round(base['2020_crash']['worst_dd'] - cand['2020_crash']['worst_dd'], 3) if '2020_crash' in wins else None,
        comp=round(sum(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins) / len(wins), 4),
        worst_comp=round(min(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins), 4),
    )


def lhs(n, seed=11):
    rng = random.Random(seed)
    dims = {'MWDD_MCC_C': (-10.0, 15.0), 'MWDD_MCC_W': (12.0, 35.0),
            'MWDD_DEPTH': (0.15, 0.45), 'MWDD_DD_MIN': (0.05, 0.30)}
    cols = {}
    for k, (lo, hi) in dims.items():
        cuts = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(cuts)
        cols[k] = [round(lo + c * (hi - lo), 3) for c in cuts]
    return [{k: cols[k][i] for k in dims} for i in range(n)]


def _fmt(base, wins):
    return {w: {'dd': base[w]['worst_dd'], 'med': base[w]['med_ret'], 'coll': base[w]['p_collapse']} for w in wins}


def run_phase(mode, n_iter, wins, cands, workers):
    base = run_candidate({}, n_iter, wins, workers=workers, tag=f'{mode}_base')
    print('BASELINE (MWDD off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'MWDD_ENABLED': 1, 'MWDD_VIX_PANIC': 28.0, **c}
        try:
            r = run_candidate(params, n_iter, wins, workers=workers, tag=f'{mode}_c{i:02d}')
        except Exception as e:
            print(f'  cand {i:02d} FAILED: {e}')
            continue
        s = score(base, r, wins)
        results.append({'i': i, 'params': c, **s, 'per_win': _fmt(r, wins)})
        print(f"c{i:02d} {c}  ddRed={s['dd_red']:+.2f} ddFoc={s['dd_red_focus']:+.2f} "
              f"dd5y={s['dd_5y']} ddCrash={s['dd_crash']} comp={s['comp']:+.3f} worstComp={s['worst_comp']:+.3f} coll={s['coll']}")
    # clean = collapse-0 everywhere AND no catastrophic per-window compound loss
    clean = [x for x in results if x['coll'] == 0 and x['worst_comp'] >= -0.15]
    clean.sort(key=lambda x: -x['dd_red_focus'])
    out = {'mode': mode, 'n_iter': n_iter, 'wins': wins,
           'baseline': _fmt(base, wins), 'results': results, 'ranked_clean': clean[:8]}
    with open(os.path.join(HERE, f'{mode}_results.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('\n=== TOP CLEAN (collapse=0, worstComp>=-0.15) by DD-focus reduction ===')
    for x in clean[:8]:
        print(f"  c{x['i']:02d} ddFoc={x['dd_red_focus']:+.2f} ddAll={x['dd_red']:+.2f} dd5y={x['dd_5y']} "
              f"comp={x['comp']:+.3f} {x['params']}")
    if not clean:
        print("  (none clean — best by ddFoc regardless of comp):")
        for x in sorted(results, key=lambda x: -x['dd_red_focus'])[:6]:
            print(f"  c{x['i']:02d} ddFoc={x['dd_red_focus']:+.2f} comp={x['comp']:+.3f} worstComp={x['worst_comp']:+.3f} coll={x['coll']} {x['params']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='phaseB')
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--cand', type=int, default=16)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--wins', default='B')
    ap.add_argument('--explicit', default='')
    a = ap.parse_args()
    wins = WINS_B if a.wins == 'B' else WINS_FULL if a.wins == 'FULL' else a.wins.split(',')

    if a.mode == 'verify':
        vw = ['2023', '2024']   # 2023 choppy flat-McClellan + DD (should fire); 2024 strong bull low-DD (DD-gated -> ~no-op)
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'MWDD_ENABLED': 1, 'MWDD_MCC_C': 0.0, 'MWDD_MCC_W': 22.0,
                           'MWDD_DEPTH': 0.35, 'MWDD_DD_MIN': 0.10, 'MWDD_VIX_PANIC': 28.0},
                          a.n, vw, workers=a.workers, tag='verify_on')
        print('\nOFF :', _fmt(b, vw))
        print('ON  :', _fmt(c, vw))
        diff = {w: round(b[w]['worst_dd'] - c[w]['worst_dd'], 3) for w in vw}
        print('ddOFF-ON:', diff, '(2023 should change = mechanism live; 2024 low-DD should barely move)')
        return

    if a.explicit:
        cands = json.loads(a.explicit)
    elif a.mode == 'phaseC':
        cands = PHASE_C_CANDS
    elif a.mode == 'phaseD':
        cands = PHASE_C_CANDS[:1]   # winner only (ship-gate confirm; override via --explicit)
    else:
        cands = lhs(a.cand, a.seed)
    run_phase(a.mode, a.n, wins, cands, a.workers)


if __name__ == '__main__':
    main()
