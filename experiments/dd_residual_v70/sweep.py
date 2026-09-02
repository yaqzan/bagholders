"""
TVDD — TRIN (Arms-index / volume-FLOW) neutral-band CALL-alloc dampener.
Stage-3 sweep (v70 Apex), 2026-06-07 overnight /research.

Mining (experiments/dd_residual_v70/mine.py, N=300 full-lever tape, DD-active subset
dd>=0.13) found, on the LIVE RXDD+SVR+MWDD substrate:
  - The NEUTRAL TRIN band (~1.0-1.3 = balanced/mild-distribution volume flow) is the
    low-EV + DD-concentrated cohort (DD-active mpnl ~+0.025, dd_conc ~1.3-1.7), AND it
    is genuinely ORTHOGONAL: in the all-shipped-levers-off slice (vix<20, |mcc|>30,
    breadth>=40) TRIN 1.0-1.3 still runs mpnl -0.060 (loser-rate 41%, z+57). It's the
    volume-flow analog of MWDD's flat-McClellan / RXDD's VIX-slow-bleed mid-band, and
    distinct from McClellan (count-momentum) -- a flow-vs-momentum divergence.
  - TRIN extremes are mean-reversion / momentum WINNERS -> left alone by a Gaussian bump:
    froth (<0.7, heavy up-volume) mpnl +0.085 AND panic (>1.8, capitulation) mpnl +0.101,
    high-EV in EVERY window.
  - Per-window robustness: TRIN 1.0-1.3 is low-EV in 10y/2020/2021/2025/22-now/5y/dip
    (NOT a crash artifact). Only 2024 (strong bull, low-DD) is above base -- the DD-gate
    skips it. 2020_crash only mildly below the (negative) crash base + VIX-panic excluded.

Mechanism (monte_carlo.py): smooth Gaussian bump on TRIN contracts call alloc in the
neutral-flow band, no-op when disabled / TRIN missing / dd < DD_MIN / VIX >= panic.
Knobs TVDD_TRIN_C / TVDD_TRIN_W / TVDD_DEPTH / TVDD_DD_MIN / TVDD_VIX_PANIC (env-overridable,
default OFF). 4th orthogonal Pareto DD lever stacking on RXDD(VIX)+SVR(skew)+MWDD(McClellan).

Goal = Pareto win: cut WorstDD on the bull/choppy windows (5y / 22-now / 2023 / 2025 / dip)
without cutting compound, collapse=0 on EVERY window incl 2020_crash (the panic-exclusion
must keep COVID untouched).

Holdout: CALIBRATION_CUTOFF removed 2026-06-05; standard MC windows end <= 2026-04-24.

Usage (run via task queue, --cpu N):
  python -u experiments/dd_residual_v70/sweep.py --mode verify --n 200
  python -u experiments/dd_residual_v70/sweep.py --mode phaseB --n 100 --cand 16
  python -u experiments/dd_residual_v70/sweep.py --mode phaseC --n 300 --wins FULL --explicit <json>
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Phase-C candidates = top clean Phase-B leaders (N=100x6, 2026-06-07), collapse=0,
# compound flat-or-up, spanning the Pareto frontier (narrow/+comp, deep/+ddAll, shallow/+dd5y).
PHASE_C_CANDS = [
    {'TVDD_TRIN_C': 1.195, 'TVDD_TRIN_W': 0.23,  'TVDD_DEPTH': 0.347, 'TVDD_DD_MIN': 0.232},  # B-c03 (best ddFoc +2.27, comp +0.054, cleanest)
    {'TVDD_TRIN_C': 1.042, 'TVDD_TRIN_W': 0.268, 'TVDD_DEPTH': 0.426, 'TVDD_DD_MIN': 0.291},  # B-c14 (best ddAll +3.27, comp +0.063)
    {'TVDD_TRIN_C': 1.064, 'TVDD_TRIN_W': 0.332, 'TVDD_DEPTH': 0.182, 'TVDD_DD_MIN': 0.194},  # B-c04 (best dd5y +3.1)
]

# Phase-D ship-gate candidates = top-2 clean Phase-C leaders (N=300x10 incl COVID), both
# collapse=0 every window, Pareto (DD down + compound up). c01 primary, c00 fallback.
PHASE_D_CANDS = [
    {'TVDD_TRIN_C': 1.042, 'TVDD_TRIN_W': 0.268, 'TVDD_DEPTH': 0.426, 'TVDD_DD_MIN': 0.291},  # C-c01 (best ddAll +2.24/dd5y +1.2/crash +8.4/comp +0.064)
    {'TVDD_TRIN_C': 1.195, 'TVDD_TRIN_W': 0.23,  'TVDD_DEPTH': 0.347, 'TVDD_DD_MIN': 0.232},  # C-c00 (ddAll +1.86, ddFoc +0.74, comp +0.060)
]

WINS_B    = ['2020_crash', '2022', '2023', '2025', 'dip', '5y']
WINS_FULL = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
# TVDD targets the neutral-TRIN bull/choppy churn (NOT crashes, VIX-panic-excluded)
# -> rank by DD reduction where the mechanism actually acts.
DD_FOCUS  = ['2023', '2025', 'dip', '5y', '22-now']


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


def lhs(n, seed=13):
    rng = random.Random(seed)
    # center in the low-EV neutral-TRIN trough; width covers it without hitting the
    # froth(<0.7)/panic(>1.8) extremes; depth/dd_min mirror MWDD ranges.
    dims = {'TVDD_TRIN_C': (0.95, 1.30), 'TVDD_TRIN_W': (0.20, 0.45),
            'TVDD_DEPTH': (0.15, 0.45), 'TVDD_DD_MIN': (0.05, 0.30)}
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
    print('BASELINE (TVDD off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'TVDD_ENABLED': 1, 'TVDD_VIX_PANIC': 28.0, **c}
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
    ap.add_argument('--seed', type=int, default=13)
    ap.add_argument('--wins', default='B')
    ap.add_argument('--explicit', default='')
    a = ap.parse_args()
    wins = WINS_B if a.wins == 'B' else WINS_FULL if a.wins == 'FULL' else a.wins.split(',')

    if a.mode == 'verify':
        vw = ['2023', '2024']   # 2023 choppy neutral-TRIN + DD (should fire); 2024 strong bull low-DD (DD-gated -> ~no-op)
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'TVDD_ENABLED': 1, 'TVDD_TRIN_C': 1.15, 'TVDD_TRIN_W': 0.30,
                           'TVDD_DEPTH': 0.35, 'TVDD_DD_MIN': 0.13, 'TVDD_VIX_PANIC': 28.0},
                          a.n, vw, workers=a.workers, tag='verify_on')
        print('\nOFF :', _fmt(b, vw))
        print('ON  :', _fmt(c, vw))
        diff = {w: round(b[w]['worst_dd'] - c[w]['worst_dd'], 3) for w in vw}
        print('ddOFF-ON:', diff, '(2023 should change = mechanism live; 2024 low-DD should barely move)')
        return

    if a.explicit:
        cands = json.loads(a.explicit)
    elif a.mode == 'phaseD':
        cands = PHASE_D_CANDS
    elif a.mode == 'phaseC':
        cands = PHASE_C_CANDS
    else:
        cands = lhs(a.cand, a.seed)
    run_phase(a.mode, a.n, wins, cands, a.workers)


if __name__ == '__main__':
    main()
