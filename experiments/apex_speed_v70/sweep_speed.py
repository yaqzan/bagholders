"""
Apex 50k->200k SPEED re-tune — Stage-3 sweep (v70 Apex, RXDD-on baseline).

Objective (user reframe): the early-stage book ($50k) wants the FASTEST RELIABLE
path to 4x ($200k). DD is a *budget* (reported, not constrained); collapse=0 is
the hard floor (you cannot compound from zero, even at $50k).

Mining (experiments/regime_dd_v70/mine.py on the 2026-06-04 tape, 6.4M call
trades) found the governing law:
  - EV by entry running-DD: dd[10,35) EV +0.071/+0.073  >>  dd[35,55) EV +0.041
    (loser z+19.6). Deep-DD entries are the WORST cohort.
  - Explosion analysis (top-decile vs rest, every window): the explosive runs are
    NOT distinguished by entry VIX/breadth/tier — they enter at LOWER mean
    entry_dd (e.g. 2021 0.12 vs 0.21; 2023 0.315 vs 0.449; dip 0.066 vs 0.102).
  => For this leveraged-momentum sleeve, DRAWDOWN-AVOIDANCE *is* return-max
     (capital-velocity law). The fastest 50k->200k path minimizes hole depth,
     it does NOT crank sizing. We test BOTH directions empirically here.

Two sweep families (all knobs are ALREADY wired => a winner ships as a pure
strategy_config value change, no new mechanism):
  (A) EXPOSURE-CAP 1-D scan: GROSS=CALL premium cap in {0.35..1.00}. Directly
      tests the user's "be more aggressive early" hypothesis vs the velocity law.
  (B) DD-SOFT-BAND re-shape: (LO, HI, FLOOR) LHS — sharper/earlier velocity
      protection of the worst dd[35,55) pocket.
  (+ combined brackets.)

Rank: collapse=0 on EVERY window (incl COVID at C/D) is the hard floor; then
maximize mean log-compound across the window mix (speed), don't tank any single
window. DD reported.

Holdout: all windows end <= 2026-04-24 < CALIBRATION_CUTOFF_DATE (2026-05-15).

Usage (run via task queue, --cpu N -> each MC subprocess uses MC_WORKERS=N):
  python -u experiments/apex_speed_v70/sweep_speed.py --mode verify  --n 80
  python -u experiments/apex_speed_v70/sweep_speed.py --mode phaseB --n 100 --wins B
  python -u experiments/apex_speed_v70/sweep_speed.py --mode phaseC --n 300 --wins FULL --explicit '<json>'
  python -u experiments/apex_speed_v70/sweep_speed.py --mode phaseD --n 500 --wins FULL --explicit '<json>'
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# Window roles ---------------------------------------------------------------
SPEED_WINS = ['2021', '2024', '2025', 'dip']     # compounding-speed reps (bull/choppy/drawdown)
BEAR_WINS  = ['2022', '5y']                       # reliability under stress + collapse guard
WINS_B     = SPEED_WINS + BEAR_WINS               # 6 windows for Phase B ranking
WINS_FULL  = ['2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']

BASE_DDLO, BASE_DDHI, BASE_DDFL = 0.35, 0.55, 0.40
BASE_CAP = 0.50


def lr(pct):
    v = 1.0 + pct / 100.0
    return math.log(v) if v > 1e-9 else -50.0


def score(base, cand, wins):
    sp = [w for w in SPEED_WINS if w in wins]
    be = [w for w in BEAR_WINS if w in wins]
    d = {w: lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins}
    return dict(
        coll=max(cand[w]['p_collapse'] for w in wins),
        comp=round(sum(d.values()) / len(d), 4),                          # mean log-compound delta (all wins)
        speed=round(sum(d[w] for w in sp) / max(1, len(sp)), 4),          # speed-window compound delta
        bear=round(sum(d[w] for w in be) / max(1, len(be)), 4),           # bear-window compound delta (reliability)
        worst=round(min(d.values()), 4),                                  # worst single-window compound delta
        dd_chg=round(sum(cand[w]['worst_dd'] - base[w]['worst_dd'] for w in wins) / len(wins), 2),
        dd_5y=round(cand['5y']['worst_dd'], 1) if '5y' in wins else None,
    )


def lhs(n, dims, seed=11):
    rng = random.Random(seed)
    cols = {}
    for k, (lo, hi) in dims.items():
        cuts = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(cuts)
        cols[k] = [round(lo + c * (hi - lo), 3) for c in cuts]
    return [{k: cols[k][i] for k in dims} for i in range(n)]


def phaseB_candidates(seed=11):
    cands = []
    # (A) exposure-cap 1-D scan — both gross & call cap to X (Apex puts-off => gross==call)
    for cap in [0.35, 0.40, 0.50, 0.65, 0.80, 1.00]:
        cands.append({'_fam': 'cap', 'GROSS_PREMIUM_CAP': cap, 'CALL_PREMIUM_CAP': cap})
    # (B) DD-soft-band re-shape LHS (cap held at baseline 0.50), enforce LO<HI
    raw = lhs(12, {'DD_SOFT_BAND_LO': (0.18, 0.40),
                   'DD_SOFT_BAND_HI': (0.46, 0.66),
                   'DD_SOFT_CALL_FLOOR': (0.20, 0.55)}, seed=seed)
    nb = 0
    for r in raw:
        if r['DD_SOFT_BAND_LO'] + 0.06 >= r['DD_SOFT_BAND_HI']:
            continue
        cands.append({'_fam': 'ddband', **r})
        nb += 1
        if nb >= 8:
            break
    # (+) combined brackets: velocity-protective vs aggressive philosophies
    cands.append({'_fam': 'velo', 'CALL_PREMIUM_CAP': 0.50, 'GROSS_PREMIUM_CAP': 0.50,
                  'DD_SOFT_BAND_LO': 0.25, 'DD_SOFT_BAND_HI': 0.50, 'DD_SOFT_CALL_FLOOR': 0.28})
    cands.append({'_fam': 'aggr', 'CALL_PREMIUM_CAP': 0.70, 'GROSS_PREMIUM_CAP': 0.70,
                  'DD_SOFT_BAND_LO': 0.45, 'DD_SOFT_BAND_HI': 0.65, 'DD_SOFT_CALL_FLOOR': 0.55})
    return cands


# Phase C drill set (N=300x8 incl COVID). Drills the Phase-B winners + the
# aggressive bracket + EXR variants (size+VIX-gated exposure = user's "100% early").
PHASE_C_CANDS = [
    # DD-band re-tunes (Phase B top-2, pure value change if they win)
    {'_fam': 'ddband12', 'DD_SOFT_BAND_LO': 0.282, 'DD_SOFT_BAND_HI': 0.556, 'DD_SOFT_CALL_FLOOR': 0.49},
    {'_fam': 'ddband07', 'DD_SOFT_BAND_LO': 0.36, 'DD_SOFT_BAND_HI': 0.525, 'DD_SOFT_CALL_FLOOR': 0.44},
    # aggressive bracket (Phase B biggest compound; test robustness + COVID collapse)
    {'_fam': 'aggr70', 'CALL_PREMIUM_CAP': 0.70, 'GROSS_PREMIUM_CAP': 0.70,
     'DD_SOFT_BAND_LO': 0.45, 'DD_SOFT_BAND_HI': 0.65, 'DD_SOFT_CALL_FLOOR': 0.55},
    # EXR size+VIX+DD-gated hot exposure (the headline test: keep aggression's
    # compound, the gates remove the collapse the static cap-scan showed at high cap)
    {'_fam': 'exr70', 'EXR_ENABLED': 1, 'EXR_HOT_CAP': 0.70, 'EXR_SIZE_LO': 4,
     'EXR_SIZE_HI': 40, 'EXR_VIX_FADE': 22, 'EXR_VIX_W': 8, 'EXR_DD_FADE': 0.30},
    # HOT 1.0 but tight DD-gate (0.22) + earlier VIX fade — push aggression to the limit
    {'_fam': 'exr100dd', 'EXR_ENABLED': 1, 'EXR_HOT_CAP': 1.0, 'EXR_SIZE_LO': 4,
     'EXR_SIZE_HI': 40, 'EXR_VIX_FADE': 20, 'EXR_VIX_W': 8, 'EXR_DD_FADE': 0.22},
    # EXR pure VIX/DD-gated cap (no size throttle) = regime-conditional exposure
    {'_fam': 'exrVix', 'EXR_ENABLED': 1, 'EXR_HOT_CAP': 0.65, 'EXR_SIZE_LO': 1e6,
     'EXR_SIZE_HI': 2e6, 'EXR_VIX_FADE': 22, 'EXR_VIX_W': 8, 'EXR_DD_FADE': 0.30},
    # combo: EXR hot exposure + the c12 DD-band re-tune
    {'_fam': 'exr70+dd', 'EXR_ENABLED': 1, 'EXR_HOT_CAP': 0.70, 'EXR_SIZE_LO': 4,
     'EXR_SIZE_HI': 40, 'EXR_VIX_FADE': 22, 'EXR_VIX_W': 8, 'EXR_DD_FADE': 0.30,
     'DD_SOFT_BAND_LO': 0.282, 'DD_SOFT_BAND_HI': 0.556, 'DD_SOFT_CALL_FLOOR': 0.49},
]


# Phase D ship-gate (N=500 x full incl 2020+2023+COVID). Confirm the only Phase-C
# clean winner (ddband07) + close neighbors, so the ship sits on a robust local
# optimum and is collapse-safe at higher N (Phase B hid ddband12's COVID collapse).
PHASE_D_CANDS = [
    {'_fam': 'dd07',  'DD_SOFT_BAND_LO': 0.36, 'DD_SOFT_BAND_HI': 0.525, 'DD_SOFT_CALL_FLOOR': 0.44},  # Phase-C winner
    {'_fam': 'dd07b', 'DD_SOFT_BAND_LO': 0.35, 'DD_SOFT_BAND_HI': 0.525, 'DD_SOFT_CALL_FLOOR': 0.42},  # more DD-protective floor
    {'_fam': 'dd07c', 'DD_SOFT_BAND_LO': 0.36, 'DD_SOFT_BAND_HI': 0.51,  'DD_SOFT_CALL_FLOOR': 0.43},  # narrower band
]


# TSL — trailing stop on winners. Sweep giveback width (TSL_SIGMA) x conviction
# gate (TSL_MIN_SCORE = trail only signals >= this). s06_all (gate 70) = trail
# everything = the OLD-null contrast; the user's hypothesis is high-conviction-only.
TSL_B_CANDS = [
    {'_fam': 's04_75',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.4, 'TSL_MIN_SCORE': 75},
    {'_fam': 's06_75',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.6, 'TSL_MIN_SCORE': 75},
    {'_fam': 's09_75',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.9, 'TSL_MIN_SCORE': 75},
    {'_fam': 's06_80',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.6, 'TSL_MIN_SCORE': 80},
    {'_fam': 's06_85',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.6, 'TSL_MIN_SCORE': 85},
    {'_fam': 's09_85',  'TSL_ENABLED': 1, 'TSL_SIGMA': 0.9, 'TSL_MIN_SCORE': 85},
    {'_fam': 's12_85',  'TSL_ENABLED': 1, 'TSL_SIGMA': 1.2, 'TSL_MIN_SCORE': 85},
    {'_fam': 's06_all', 'TSL_ENABLED': 1, 'TSL_SIGMA': 0.6, 'TSL_MIN_SCORE': 70},
]


# Regime-adaptive TP/SL on the honest-v70 HOLD core (currently flat: TP 0.30/0.30,
# SL -0.70/-0.70, breadth-adaptive OFF). Pre-v70 had breadth-adaptive TP/SL
# (brd_TP30/35 = +44-49%) but the HOLD rebuild dropped it. Test: stress-wider TP
# (sell winners wider in low-breadth tape) + calm-tighter SL (cut losers faster in
# calm bull; HOLD/ride in stress). Stress fires when breadth_score <= BREADTH_THRESHOLD.
RTPSL_B_CANDS = [
    {'_fam': 'tpS35',    'TP_BASE': 0.30, 'TP_STRESS': 0.35, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'tpS42',    'TP_BASE': 0.30, 'TP_STRESS': 0.42, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'tpS50',    'TP_BASE': 0.30, 'TP_STRESS': 0.50, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'tpS42t50', 'TP_BASE': 0.30, 'TP_STRESS': 0.42, 'BREADTH_THRESHOLD': 50},
    {'_fam': 'slB55',    'SL_BASE': -0.55, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'slB60',    'SL_BASE': -0.60, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'both',     'TP_BASE': 0.30, 'TP_STRESS': 0.42, 'SL_BASE': -0.55, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'tpB28slB60', 'TP_BASE': 0.28, 'TP_STRESS': 0.40, 'SL_BASE': -0.60, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 45},
]


# Tighter-calm-barriers Phase C (N=300x8 incl COVID). The night's ONLY +speed signal
# (calm-tighter TP/SL = faster bull recycling). KEY test: does the tighter calm SL
# (cut losers faster in calm) stay collapse=0 on COVID, or does it conflict with the
# collapse-preventing dead-hold? Brackets SL-tightness + isolates TP vs SL.
CALM_C_CANDS = [
    {'_fam': 'cb_sl65',    'TP_BASE': 0.28, 'TP_STRESS': 0.40, 'SL_BASE': -0.65, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 45},
    {'_fam': 'cb_sl60',    'TP_BASE': 0.28, 'TP_STRESS': 0.40, 'SL_BASE': -0.60, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 45},
    {'_fam': 'cb_sl55',    'TP_BASE': 0.26, 'TP_STRESS': 0.40, 'SL_BASE': -0.55, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 45},
    {'_fam': 'cb_slonly60', 'SL_BASE': -0.60, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 40},
    {'_fam': 'cb_tponly',  'TP_BASE': 0.28, 'TP_STRESS': 0.40, 'SL_BASE': -0.70, 'SL_STRESS': -0.70, 'BREADTH_THRESHOLD': 45},
]


# SVR entry-filter (semivol_r skew-bridge — the ONLY orthogonal-alpha lead). Downweight
# call alloc on low-semivol_r (euphoric/expensive-call, worst cohort) + optional high-taper
# (crash-mode). Inverted-U: sweet spot ~0.9-1.1. Knob: contract toward FLOOR below LO_FULL
# and above HI_FULL; full in [LO_FULL, HI_FULL]. (HI_FULL=9 => low-cut-only; LO_FULL=0 => hi-only.)
SVR_B_CANDS = [
    # low-cut only (downweight the euphoric/expensive-call cohort — the strong signal)
    {'_fam': 'lo07f50', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.50},
    {'_fam': 'lo08f50', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.60, 'SVR_LO_FULL': 0.80, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.50},
    {'_fam': 'lo08f30', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.60, 'SVR_LO_FULL': 0.80, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.30},
    {'_fam': 'lo07f15', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.55, 'SVR_LO_FULL': 0.68, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.15},
    # band-pass (avoid both extremes — the inverted-U)
    {'_fam': 'band50',  'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.60, 'SVR_LO_FULL': 0.90, 'SVR_HI_FULL': 1.20, 'SVR_HI_CUT': 1.55, 'SVR_FLOOR': 0.50},
    {'_fam': 'band30',  'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.65, 'SVR_LO_FULL': 0.95, 'SVR_HI_FULL': 1.15, 'SVR_HI_CUT': 1.40, 'SVR_FLOOR': 0.30},
    # high-taper only (isolate whether the crash-mode extreme matters)
    {'_fam': 'hi_only', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0, 'SVR_LO_FULL': 0, 'SVR_HI_FULL': 1.20, 'SVR_HI_CUT': 1.55, 'SVR_FLOOR': 0.50},
]


# SVR Phase C (N=300x8 incl COVID) — drill the Phase-B Pareto winner (gentle low-cut of
# the euphoric/expensive-call cohort: +compound AND -4.1pp DD) + the gentle stacked band.
SVR_C_CANDS = [
    {'_fam': 'lo07f50',    'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.50},  # B winner
    {'_fam': 'lo07f60',    'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.60},  # gentler floor
    {'_fam': 'gentleband', 'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 1.25, 'SVR_HI_CUT': 1.65, 'SVR_FLOOR': 0.50},  # low-cut + gentle high-taper (stack)
    {'_fam': 'hi_only',    'SVR_ENABLED': 1, 'SVR_LO_CUT': 0, 'SVR_LO_FULL': 0, 'SVR_HI_FULL': 1.20, 'SVR_HI_CUT': 1.55, 'SVR_FLOOR': 0.50},  # high-taper only
]


# SVR Phase D ship-gate (N=500x8 incl COVID) — confirm the gentle band-pass Pareto winner.
SVR_D_CANDS = [
    {'_fam': 'gentleband',  'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 1.25, 'SVR_HI_CUT': 1.65, 'SVR_FLOOR': 0.50},  # C winner
    {'_fam': 'band_f40',    'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 1.25, 'SVR_HI_CUT': 1.65, 'SVR_FLOOR': 0.40},  # slightly more aggressive
    {'_fam': 'lo07f50',     'SVR_ENABLED': 1, 'SVR_LO_CUT': 0.50, 'SVR_LO_FULL': 0.70, 'SVR_HI_FULL': 9, 'SVR_HI_CUT': 99, 'SVR_FLOOR': 0.50},  # low-cut-only fallback
]


def _fmt(res, wins):
    return {w: {'dd': res[w]['worst_dd'], 'med': res[w]['med_ret'], 'coll': res[w]['p_collapse']} for w in wins}


def run_phase(mode, n_iter, wins, cands, workers):
    base = run_candidate({}, n_iter, wins, workers=workers, tag=f'{mode}_base')
    print('BASELINE (live Apex, RXDD-on):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        fam = c.pop('_fam', '?') if isinstance(c, dict) else '?'
        try:
            r = run_candidate(c, n_iter, wins, workers=workers, tag=f'{mode}_c{i:02d}')
        except Exception as e:
            print(f'  cand {i:02d} FAILED: {e}')
            continue
        s = score(base, r, wins)
        results.append({'i': i, 'fam': fam, 'params': c, **s, 'per_win': _fmt(r, wins)})
        print(f"c{i:02d} [{fam:6s}] {c}  comp={s['comp']:+.3f} speed={s['speed']:+.3f} "
              f"bear={s['bear']:+.3f} worst={s['worst']:+.3f} ddChg={s['dd_chg']:+.1f} "
              f"dd5y={s['dd_5y']} coll={s['coll']}")
    # clean = collapse-free AND no single window tanked beyond -0.25 log (~ -22%)
    clean = [x for x in results if x['coll'] == 0 and x['worst'] >= -0.25]
    clean.sort(key=lambda x: -x['comp'])
    out = {'mode': mode, 'n_iter': n_iter, 'wins': wins,
           'baseline': _fmt(base, wins), 'results': results, 'ranked_clean': clean[:10]}
    with open(os.path.join(HERE, f'{mode}_results.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('\n=== TOP CLEAN (collapse=0, worst>=-0.25) by mean log-compound ===')
    for x in clean[:10]:
        print(f"  c{x['i']:02d} [{x['fam']:6s}] comp={x['comp']:+.3f} speed={x['speed']:+.3f} "
              f"bear={x['bear']:+.3f} ddChg={x['dd_chg']:+.1f} dd5y={x['dd_5y']} {x['params']}")
    if not clean:
        print("  (NONE clean — best by comp regardless of worst/coll):")
        for x in sorted(results, key=lambda x: -x['comp'])[:8]:
            print(f"  c{x['i']:02d} [{x['fam']:6s}] comp={x['comp']:+.3f} worst={x['worst']:+.3f} coll={x['coll']} {x['params']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='phaseB')
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--wins', default='B')
    ap.add_argument('--explicit', default='')   # JSON list of param dicts for phaseC/D
    a = ap.parse_args()
    wins = WINS_B if a.wins == 'B' else WINS_FULL if a.wins == 'FULL' else a.wins.split(',')

    if a.mode == 'verify':
        # confirm the cap override moves the result vs baseline on 1 win
        vw = ['2024']
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        c = run_candidate({'GROSS_PREMIUM_CAP': 0.30, 'CALL_PREMIUM_CAP': 0.30}, a.n, vw,
                          workers=a.workers, tag='verify_cap30')
        d = run_candidate({'DD_SOFT_BAND_LO': 0.20, 'DD_SOFT_BAND_HI': 0.45, 'DD_SOFT_CALL_FLOOR': 0.25},
                          a.n, vw, workers=a.workers, tag='verify_ddband')
        print('\nbase   2024:', _fmt(b, vw))
        print('cap.30 2024:', _fmt(c, vw), '(cap=0.30 should LOWER med vs 0.50 base if cap binds)')
        print('ddband 2024:', _fmt(d, vw), '(sharper DD-band should move med/dd)')
        return

    if a.mode == 'verify_exr':
        # EXR off must == baseline; EXR on must LIFT 2024 (hot small book in calm
        # bull) AND keep 2020_crash collapse=0 (VIX-gate fades hot at COVID VIX).
        vw = ['2024', '2020_crash']
        b   = run_candidate({}, a.n, vw, workers=a.workers, tag='vexr_base')
        off = run_candidate({'EXR_ENABLED': 0}, a.n, vw, workers=a.workers, tag='vexr_off')
        on  = run_candidate({'EXR_ENABLED': 1, 'EXR_HOT_CAP': 1.0, 'EXR_SIZE_LO': 4,
                             'EXR_SIZE_HI': 40, 'EXR_VIX_FADE': 22, 'EXR_VIX_W': 8},
                            a.n, vw, workers=a.workers, tag='vexr_on')
        print('\nbase     :', _fmt(b, vw))
        print('EXR off  :', _fmt(off, vw), '(must == base; EXR default OFF is byte-identical)')
        print('EXR on   :', _fmt(on, vw), '(2024 med should RISE = hot small book; 2020_crash coll must stay 0)')
        return

    if a.mode == 'verify_tsl':
        # TSL off must == baseline; TSL on must CHANGE 2024 (winners trail) and
        # NOT increase collapse on 2020_crash (winner mechanism; losers untouched).
        vw = ['2024', '2020_crash']
        b   = run_candidate({}, a.n, vw, workers=a.workers, tag='vtsl_base')
        off = run_candidate({'TSL_ENABLED': 0}, a.n, vw, workers=a.workers, tag='vtsl_off')
        on  = run_candidate({'TSL_ENABLED': 1, 'TSL_SIGMA': 0.6, 'TSL_MIN_SCORE': 75},
                            a.n, vw, workers=a.workers, tag='vtsl_on')
        print('\nbase     :', _fmt(b, vw))
        print('TSL off  :', _fmt(off, vw), '(must == base; TSL default OFF is byte-identical)')
        print('TSL on   :', _fmt(on, vw), '(2024 med should CHANGE = winners trailing; 2020_crash coll must NOT rise)')
        return

    if a.explicit:
        cands = json.loads(a.explicit)
        for c in cands:
            c.setdefault('_fam', 'expl')
    elif a.mode == 'phaseC':
        cands = [dict(c) for c in PHASE_C_CANDS]
    elif a.mode == 'phaseD':
        cands = [dict(c) for c in PHASE_D_CANDS]
    elif a.mode == 'tslB':
        cands = [dict(c) for c in TSL_B_CANDS]
    elif a.mode == 'rtpslB':
        cands = [dict(c) for c in RTPSL_B_CANDS]
    elif a.mode == 'calmC':
        cands = [dict(c) for c in CALM_C_CANDS]
    elif a.mode == 'svrB':
        cands = [dict(c) for c in SVR_B_CANDS]
    elif a.mode == 'svrC':
        cands = [dict(c) for c in SVR_C_CANDS]
    elif a.mode == 'svrD':
        cands = [dict(c) for c in SVR_D_CANDS]
    else:
        cands = phaseB_candidates(a.seed)
    run_phase(a.mode, a.n, wins, cands, a.workers)


if __name__ == '__main__':
    main()
