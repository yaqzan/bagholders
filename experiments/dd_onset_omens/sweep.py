"""
BDIV — pre-top Breadth-DIVergence-at-highs CALL alloc dampener: Stage-3 sweep (v71 Apex).
2026-06-10 overnight /research (user ask: "isolate major drawdowns, look back for
Hindenburg-style omens that raise drawdown likelihood").

Mining (experiments/dd_onset_omens/mine_onset.py + mine2_refine.py, FRESH v71 tape
2020+5y N=300): the literal Hindenburg/omen hypothesis is NULL/INVERTED (0 of 24 omen days
preceded a major episode onset; omen-day entries are mean-reversion WINNERS mpnl +0.079;
the trailing-confirmed cohort fails the orthogonal slice = G23's NH/NL death). The ONE
survivor is the classic PRE-TOP BREADTH DIVERGENCE:

  SPY within ~1% of its 60d high WHILE breadth_score has dropped ~5pts in 10d
    -> mpnl -0.018 vs +0.029 off (z=+25, dd_conc 1.82, 12% of trades)
    -> sign-stable 5/6 years INCL both momentum-persistent years (2024 -0.028, 2025 -0.053)
    -> survives the all-levers-off orthogonal slice (gap -0.068)
    -> proximity axis MONOTONIC; gap axis INVERTED-U (gap>12 = sharp shakeout = winners)

Mechanism (monte_carlo.py): scale = 1 - DEPTH * prox_ramp(spy_from60h) * gauss(det10; C, W).
NO DD-gate (value is PRE-onset contraction, book dd ~ 0 at the top) and NO VIX-panic knob:
the SPY-near-highs requirement is the structural crash guard (the flag CANNOT fire mid-crash
-- SPY is never near its 60d high in a bear leg; 2022 has ~no firings by construction).

Goal = Pareto: cut WorstDD on the divergence-led windows (2021/2025/dip/5y/22-now) without
cutting compound; collapse=0 on EVERY window incl 2020_crash (G16: always screen with COVID).

Usage (queue, --cpu N):
  python -u experiments/dd_onset_omens/sweep.py --mode verify --n 200
  python -u experiments/dd_onset_omens/sweep.py --mode phaseB --n 100 --cand 16
  python -u experiments/dd_onset_omens/sweep.py --mode phaseC --n 300 --wins FULL
  python -u experiments/dd_onset_omens/sweep.py --mode phaseD --n 500 --wins FULL
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', '04044b21b')  # v71 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Phase B (LHS-16, N=100 x 6 incl 2020_crash): 16/16 clean (collapse=0, worstComp>=-0.19),
# 16/16 positive ddRed — the strong-lever signature (vs DQT's 2/16). Basin: GAP_C ~7.4-7.9,
# wide GAP_W ~3.0-3.5, DEPTH 0.44-0.53. Leaders ddFoc +5.0pp with compound UP (Pareto).
PHASE_C_CANDS = [
    {'BDIV_PROX_CUT': 0.0285, 'BDIV_PROX_FULL': 0.0130, 'BDIV_GAP_C': 7.4008, 'BDIV_GAP_W': 3.0206, 'BDIV_DEPTH': 0.4401},  # B-c02 ddFoc+5.00 comp+0.053
    {'BDIV_PROX_CUT': 0.0198, 'BDIV_PROX_FULL': 0.0075, 'BDIV_GAP_C': 7.7160, 'BDIV_GAP_W': 3.4571, 'BDIV_DEPTH': 0.5300},  # B-c14 ddFoc+5.00 comp+0.085
    {'BDIV_PROX_CUT': 0.0222, 'BDIV_PROX_FULL': 0.0076, 'BDIV_GAP_C': 7.8489, 'BDIV_GAP_W': 3.3452, 'BDIV_DEPTH': 0.4494},  # B-c04 ddFoc+4.85 comp+0.069
    {'BDIV_PROX_CUT': 0.0164, 'BDIV_PROX_FULL': 0.0048, 'BDIV_GAP_C': 6.9568, 'BDIV_GAP_W': 2.8875, 'BDIV_DEPTH': 0.3957},  # B-c11 mild form ddFoc+3.58
]
# Phase C (N=300 x 10 incl COVID): signal FIRMED (4/4 clean; winner c01 = B-c14:
# ddFoc +4.26, dd5y +3.0, comp +0.061, dip DD 40.3->26.3 (+14.0!), 2022 exactly 0.0).
# Phase D gates the winner + two close neighbors (G16: winners can sit on a boundary).
PHASE_D_CANDS = [
    {'BDIV_PROX_CUT': 0.0198, 'BDIV_PROX_FULL': 0.0075, 'BDIV_GAP_C': 7.7160, 'BDIV_GAP_W': 3.4571, 'BDIV_DEPTH': 0.5300},  # C-c01 winner
    {'BDIV_PROX_CUT': 0.0222, 'BDIV_PROX_FULL': 0.0076, 'BDIV_GAP_C': 7.8489, 'BDIV_GAP_W': 3.3452, 'BDIV_DEPTH': 0.4494},  # C-c02 neighbor (shallower depth)
    {'BDIV_PROX_CUT': 0.0285, 'BDIV_PROX_FULL': 0.0130, 'BDIV_GAP_C': 7.4008, 'BDIV_GAP_W': 3.0206, 'BDIV_DEPTH': 0.4401},  # C-c00 neighbor (wider prox)
]

WINS_B    = ['2020_crash', '2021', '2024', '2025', 'dip', '5y']
WINS_FULL = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
# BDIV fires at-highs; the divergence-led drawdowns live here (2021-01/2025-02 onsets).
DD_FOCUS  = ['2021', '2025', 'dip', '22-now', '5y']


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


def lhs(n, seed=23):
    rng = random.Random(seed)
    # PROX_CUT: contraction starts this far below the 60d high; PROX_FRAC: PROX_FULL as a
    # fraction of PROX_CUT (keeps FULL < CUT by construction); GAP_C/W: Gaussian band on the
    # 10d breadth deterioration; DEPTH: max contraction.
    dims = {'BDIV_PROX_CUT': (0.012, 0.035), 'PROX_FRAC': (0.15, 0.55),
            'BDIV_GAP_C': (5.0, 8.5), 'BDIV_GAP_W': (1.5, 3.5),
            'BDIV_DEPTH': (0.25, 0.60)}
    cols = {}
    for k, (lo, hi) in dims.items():
        cuts = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(cuts)
        cols[k] = [round(lo + c * (hi - lo), 4) for c in cuts]
    out = []
    for i in range(n):
        c = {k: cols[k][i] for k in dims}
        c['BDIV_PROX_FULL'] = round(c['BDIV_PROX_CUT'] * c.pop('PROX_FRAC'), 4)
        out.append(c)
    return out


def _fmt(base, wins):
    return {w: {'dd': base[w]['worst_dd'], 'med': base[w]['med_ret'], 'coll': base[w]['p_collapse']} for w in wins}


def run_phase(mode, n_iter, wins, cands, workers):
    # Explicit BDIV_ENABLED=0 baseline: strategy_config ships BDIV ON as of 2026-06-10,
    # so a bare {} baseline would silently run BDIV-on (wrong marginal compare).
    base = run_candidate({'BDIV_ENABLED': 0}, n_iter, wins, workers=workers, tag=f'{mode}_base')
    print('BASELINE (BDIV off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'BDIV_ENABLED': 1, **c}
        try:
            r = run_candidate(params, n_iter, wins, workers=workers, tag=f'{mode}_c{i:02d}')
        except Exception as e:
            print(f'  cand {i:02d} FAILED: {e}')
            continue
        s = score(base, r, wins)
        results.append({'i': i, 'params': c, **s, 'per_win': _fmt(r, wins)})
        print(f"c{i:02d} {c}  ddRed={s['dd_red']:+.2f} ddFoc={s['dd_red_focus']:+.2f} "
              f"dd5y={s['dd_5y']} ddCrash={s['dd_crash']} comp={s['comp']:+.3f} worstComp={s['worst_comp']:+.3f} coll={s['coll']}")
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
        print("  (none clean -- best by ddFoc regardless of comp):")
        for x in sorted(results, key=lambda x: -x['dd_red_focus'])[:6]:
            print(f"  c{x['i']:02d} ddFoc={x['dd_red_focus']:+.2f} comp={x['comp']:+.3f} worstComp={x['worst_comp']:+.3f} coll={x['coll']} {x['params']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='phaseB')
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--cand', type=int, default=16)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--seed', type=int, default=23)
    ap.add_argument('--wins', default='B')
    ap.add_argument('--explicit', default='')
    a = ap.parse_args()
    wins = WINS_B if a.wins == 'B' else WINS_FULL if a.wins == 'FULL' else a.wins.split(',')

    if a.mode == 'verify':
        # 2021 = at-highs divergence year (the 2021-01-25 onset, biggest 5y episode) -> BDIV
        # should FIRE and move DD; 2022 = bear (SPY never near 60d highs) -> ~no-op BY
        # CONSTRUCTION (this IS the crash-guard claim).
        vw = ['2021', '2022']
        b = run_candidate({'BDIV_ENABLED': 0}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'BDIV_ENABLED': 1, 'BDIV_PROX_CUT': 0.020, 'BDIV_PROX_FULL': 0.005,
                           'BDIV_GAP_C': 6.5, 'BDIV_GAP_W': 2.5, 'BDIV_DEPTH': 0.45},
                          a.n, vw, workers=a.workers, tag='verify_on')
        print('\nOFF :', _fmt(b, vw))
        print('ON  :', _fmt(c, vw))
        diff = {w: round(b[w]['worst_dd'] - c[w]['worst_dd'], 3) for w in vw}
        print('ddOFF-ON:', diff, '(2021 should change = mechanism live; 2022 should be ~no-op by construction)')
        b2 = run_candidate({'BDIV_ENABLED': 0}, a.n, vw, workers=a.workers, tag='verify_off_explicit')
        noop = {w: (round(b[w]['worst_dd'] - b2[w]['worst_dd'], 4), round(b[w]['med_ret'] - b2[w]['med_ret'], 4)) for w in vw}
        print('NOOP (bare baseline - BDIV_ENABLED=0), should be (0.0, 0.0):', noop)
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
