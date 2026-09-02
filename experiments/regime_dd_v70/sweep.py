"""
RXDD VIX-regime-aware call-alloc dampener — Stage-3 sweep (v70 Apex).

Mining (experiments/regime_dd_v70/mine.py, N=300 tape) found:
  - VIX 20-28 call entries are the worst cohort (loser-rate lift 1.07, z+52,
    mpnl +0.027 ~ break-even) -> low-EV, safe to contract.
  - VIX>=28 panic entries are the BEST (lift 0.80, mpnl +0.15) -> leave alone.
  - entry_dd / breadth contraction was a CRASH ARTIFACT (positive-EV in bull
    years) -> rejected. VIX band is the one robust, Pareto-shaped signal.

Mechanism (monte_carlo.py): a smooth Gaussian bump contracts call alloc in the
mid-VIX band, no-op outside it. Knobs RXDD_VIX_C / RXDD_VIX_W / RXDD_DEPTH /
RXDD_DD_MIN (env-overridable, default OFF).

Goal = Pareto win: cut WorstDD (esp 5y / 22-now / 2022 / 2023 / 2020_crash)
without cutting compound, collapse=0 on every window incl COVID.

Holdout: all windows end <= 2026-04-24, before CALIBRATION_CUTOFF_DATE
(2026-05-15) -> no post-cutoff leak.

Usage (run via task queue, --cpu N, each MC subprocess uses MC_WORKERS=workers):
  python -u experiments/regime_dd_v70/sweep.py --mode verify --n 300
  python -u experiments/regime_dd_v70/sweep.py --mode phaseB --n 100 --cand 14
  python -u experiments/regime_dd_v70/sweep.py --mode phaseC --n 300 --wins FULL --explicit <json>
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Top clean candidates from Phase B (N=100x6win, 2026-06-04) — DD-focus leaders.
PHASE_C_CANDS = [
    {'RXDD_VIX_C': 22.701, 'RXDD_VIX_W': 3.14,  'RXDD_DEPTH': 0.447, 'RXDD_DD_MIN': 0.077},  # B-c01 (best compound)
    {'RXDD_VIX_C': 21.994, 'RXDD_VIX_W': 4.891, 'RXDD_DEPTH': 0.324, 'RXDD_DD_MIN': 0.24},   # B-c07 (best 5y DD)
    {'RXDD_VIX_C': 24.014, 'RXDD_VIX_W': 5.155, 'RXDD_DEPTH': 0.418, 'RXDD_DD_MIN': 0.177},  # B-c08 (mid)
]

WINS_B    = ['2020_crash', '2022', '2023', '2024', 'dip', '5y']
WINS_FULL = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
DD_FOCUS  = ['2020_crash', '2022', '2023', '5y', '22-now']   # where we most want DD down


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
        comp=round(sum(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins) / len(wins), 4),
        worst_comp=round(min(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins), 4),
    )


def lhs(n, seed=7):
    rng = random.Random(seed)
    dims = {'RXDD_VIX_C': (21.0, 27.0), 'RXDD_VIX_W': (3.0, 7.0),
            'RXDD_DEPTH': (0.12, 0.45), 'RXDD_DD_MIN': (0.0, 0.30)}
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
    print('BASELINE (RXDD off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'RXDD_ENABLED': 1, **c}
        try:
            r = run_candidate(params, n_iter, wins, workers=workers, tag=f'{mode}_c{i:02d}')
        except Exception as e:
            print(f'  cand {i:02d} FAILED: {e}')
            continue
        s = score(base, r, wins)
        results.append({'i': i, 'params': c, **s, 'per_win': _fmt(r, wins)})
        print(f"c{i:02d} {c}  ddRed={s['dd_red']:+.2f} ddFoc={s['dd_red_focus']:+.2f} "
              f"dd5y={s['dd_5y']} comp={s['comp']:+.3f} worstComp={s['worst_comp']:+.3f} coll={s['coll']}")
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
    ap.add_argument('--cand', type=int, default=14)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--wins', default='B')
    ap.add_argument('--explicit', default='')   # JSON list of param dicts for phaseC/D
    a = ap.parse_args()
    wins = WINS_B if a.wins == 'B' else WINS_FULL if a.wins == 'FULL' else a.wins.split(',')

    if a.mode == 'verify':
        vw = ['2022', 'dip']
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'RXDD_ENABLED': 1, 'RXDD_VIX_C': 24, 'RXDD_VIX_W': 5,
                           'RXDD_DEPTH': 0.35, 'RXDD_DD_MIN': 0.0}, a.n, vw, workers=a.workers, tag='verify_on')
        print('\nOFF :', _fmt(b, vw))
        print('ON  :', _fmt(c, vw))
        diff = {w: round(b[w]['worst_dd'] - c[w]['worst_dd'], 3) for w in vw}
        print('ddOFF-ON:', diff, '(2022 should change = mechanism live; dip low-VIX should barely move)')
        return

    if a.explicit:
        cands = json.loads(a.explicit)
    elif a.mode == 'phaseC':
        cands = PHASE_C_CANDS
    elif a.mode == 'phaseD':
        cands = PHASE_C_CANDS[:1]   # winner c00 only (ship-gate confirm)
    else:
        cands = lhs(a.cand, a.seed)
    run_phase(a.mode, a.n, wins, cands, a.workers)


if __name__ == '__main__':
    main()
