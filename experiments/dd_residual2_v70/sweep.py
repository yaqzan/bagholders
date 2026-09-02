"""
DQT — Drawdown Quality Tilt: DD-gated contraction of the LOW-conviction call tiers.
Stage-3 sweep (v70 Apex), 2026-06-08 overnight /research.

Mining (experiments/dd_residual2_v70/mine2.py, N=300 full-lever tape, DD-active subset
dd>=0.13) found, on the LIVE RXDD+SVR+MWDD substrate, that the regime/breadth/volume
DD-sizing well is DRY for a 5th orthogonal axis (NH/NL fails the all-levers-off slice =
euphoria re-label; breadth-velocity is McClellan-redundant corr 0.72; VIX-velocity is
anti-aligned/noisy; pe50 captured). The one genuinely ORTHOGONAL, non-regime residual is
the per-signal CASCADE TIER:

  DD-active (dd>=0.13) mean opt pnl + dd-share BY TIER (base mpnl ~+0.05):
    ultra  +0.087  ddshr 0.030     <- high-EV, leave alone
    top    +0.121  ddshr 0.039     <- high-EV, leave alone
    mid    +0.087  ddshr 0.117     <- high-EV (highest conc 2.96 but +EV), leave alone
    low    +0.044  ddshr 0.438     <- LOW-EV + 44% of all DD-dollars  *** target ***
    overflow +0.047 ddshr 0.376    <- low-EV but diffuse (conc 0.55); hydration engine

So the LOW (75-79) tier is the single biggest DD contributor at low EV, while the high-
conviction tiers are high-EV. "During drawdown, be more selective: contract the WEAKEST
tradable signals, keep the conviction tiers full." Orthogonal to all 4 regime levers
(they key on VIX/skew/McClellan/TRIN; DQT keys on conviction tier). Surgical vs the
tier-BLIND DD_SOFT_BAND.

Crash-artifact guard: the low tier is low-EV in stress windows (2020_crash -0.079, 2022
-0.067, 2023 -0.003) but HIGH-EV in bull (2024 +0.241, dip +0.178). The DD-GATE is the
mitigation (same as RXDD/MWDD/TVDD): DQT only acts while running dd >= DQT_DD_MIN, so it
fires often in bear drawdowns (where the low tier is genuinely low-EV) and rarely in bull
tape (where the book is seldom drawn down). Whether the DD-gate fully handles it is the
Phase-B question this sweep answers.

Collapse-safe by construction: DQT only ever REDUCES alloc (smaller positions => less DD
AND less collapse risk), so no VIX-panic exclusion is needed.

Mechanism (monte_carlo.py): linear ramp 1.0 -> per-tier floor over [DQT_DD_MIN, DQT_DD_FULL],
applied to tier in {low, overflow}; high-conviction tiers untouched. Knobs DQT_DD_MIN /
DQT_DD_FULL / DQT_FLOOR (low tier) / DQT_OVF_FLOOR (overflow tier, 1.0 = off). Default OFF
=> baseline byte-identical.

Goal = Pareto: cut 5y WorstDD (and the drawdown-heavy windows) without cutting compound,
collapse=0 on EVERY window incl 2020_crash.

Holdout: CALIBRATION_CUTOFF removed 2026-06-05; standard MC windows end <= 2026-04-24.

Usage (run via task queue, --cpu N):
  python -u experiments/dd_residual2_v70/sweep.py --mode verify --n 200
  python -u experiments/dd_residual2_v70/sweep.py --mode phaseB --n 100 --cand 16
  python -u experiments/dd_residual2_v70/sweep.py --mode phaseC --n 300 --wins FULL --explicit <json>
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Phase B (N=100x6) verdict: DQT is a MILD, mostly-2022 lever. Only the LOW-tier-only
# (OVF_FLOOR=1.0) mild-floor configs stay compound-neutral; deeper floors / overflow
# contraction crater compound (velocity cost). Phase C maps the low-tier-only floor
# frontier 0.60-0.85 at N=300x10 (incl COVID) to see if the small Pareto firms up.
PHASE_C_CANDS = [
    {'DQT_DD_MIN': 0.147, 'DQT_DD_FULL': 0.467, 'DQT_FLOOR': 0.80, 'DQT_OVF_FLOOR': 1.0},  # B-c14 (clean winner, mildest)
    {'DQT_DD_MIN': 0.15,  'DQT_DD_FULL': 0.45,  'DQT_FLOOR': 0.70, 'DQT_OVF_FLOOR': 1.0},  # deeper floor, OVF off
    {'DQT_DD_MIN': 0.13,  'DQT_DD_FULL': 0.55,  'DQT_FLOOR': 0.75, 'DQT_OVF_FLOOR': 1.0},  # wider band
    {'DQT_DD_MIN': 0.15,  'DQT_DD_FULL': 0.50,  'DQT_FLOOR': 0.60, 'DQT_OVF_FLOOR': 1.0},  # moderate (deep end of neutral basin)
]
PHASE_D_CANDS = []   # filled from Phase-C clean leaders

WINS_B    = ['2020_crash', '2022', '2023', '2025', 'dip', '5y']
WINS_FULL = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
# DQT acts during drawdowns broadly; rank by DD reduction on the drawdown-heavy windows.
DD_FOCUS  = ['2022', '2023', '2025', 'dip', '22-now', '5y']


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
    # DD_MIN starts the contraction; DD_FULL reaches the floor; FLOOR = low-tier alloc
    # multiplier at full DD; OVF_FLOOR = overflow-tier floor (1.0 = overflow untouched).
    dims = {'DQT_DD_MIN': (0.10, 0.28), 'DQT_DD_FULL': (0.38, 0.70),
            'DQT_FLOOR': (0.25, 0.85), 'DQT_OVF_FLOOR': (0.55, 1.00)}
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
    print('BASELINE (DQT off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'DQT_ENABLED': 1, **c}
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
        vw = ['2022', '2024']   # 2022 bear+DD-heavy (DQT should fire & cut DD); 2024 strong bull low-DD (DD-gated -> ~no-op)
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'DQT_ENABLED': 1, 'DQT_DD_MIN': 0.13, 'DQT_DD_FULL': 0.45,
                           'DQT_FLOOR': 0.50, 'DQT_OVF_FLOOR': 1.0},
                          a.n, vw, workers=a.workers, tag='verify_on')
        print('\nOFF :', _fmt(b, vw))
        print('ON  :', _fmt(c, vw))
        diff = {w: round(b[w]['worst_dd'] - c[w]['worst_dd'], 3) for w in vw}
        print('ddOFF-ON:', diff, '(2022 should change = mechanism live; 2024 low-DD should barely move)')
        # byte-identical no-op check: DQT off via the explicit flag must equal the bare baseline
        b2 = run_candidate({'DQT_ENABLED': 0}, a.n, vw, workers=a.workers, tag='verify_off_explicit')
        noop = {w: (round(b[w]['worst_dd'] - b2[w]['worst_dd'], 4), round(b[w]['med_ret'] - b2[w]['med_ret'], 4)) for w in vw}
        print('NOOP (bare baseline - DQT_ENABLED=0), should be (0.0, 0.0):', noop)
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
