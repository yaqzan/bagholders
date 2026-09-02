"""
VXMD — VIX Weekly-MACD momentum call-alloc dampener — Stage-3 confirmatory screen (v70 Apex).

User hypothesis (2026-06-09): contract calls when VIX *momentum* (weekly MACD building)
is rising, not just when VIX *level* is high (RXDD, already shipped).

Mining verdict (experiments/vix_weekly_v70/mine.py, full-lever tape): the rising-weekly-MACD
cohort IS DD-concentrated (63-70% of DD-dollars) BUT, point-in-time (no look-ahead), is
HIGH-EV (mpnl +0.08..+0.10 -- the dead-hold mean-reversion runners cluster there) and a
CRASH ARTIFACT (low-EV bear 2022 -0.17, high-EV bull 2024 +0.09). Expectation: VXMD cuts
2022/5y DD but ALSO cuts bull-2024 compound -> not Pareto -> NULL (the DQT/G23 failure mode).

This screen makes that airtight with MC evidence on the live Apex stack (RXDD+SVR+MWDD+TVDD
on; VXMD added on top). Baseline {} == live Apex (VXMD off-by-default).

Usage (queue, --cpu N -> MC_WORKERS):
  python -u experiments/vix_weekly_v70/sweep.py --mode verify --n 100
  python -u experiments/vix_weekly_v70/sweep.py --mode screen --n 100
"""
import os, sys, json, math, random, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active

from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

# hand-picked configs spanning depth x dd_min x whist_hi (the crash-artifact probe)
SCREEN_CANDS = [
    {'VXMD_DEPTH': 0.30, 'VXMD_DD_MIN': 0.10, 'VXMD_WHIST_HI': 0.8, 'VXMD_WHIST_LO': 0.0},  # moderate
    {'VXMD_DEPTH': 0.50, 'VXMD_DD_MIN': 0.10, 'VXMD_WHIST_HI': 0.5, 'VXMD_WHIST_LO': 0.0},  # aggressive
    {'VXMD_DEPTH': 0.30, 'VXMD_DD_MIN': 0.05, 'VXMD_WHIST_HI': 0.5, 'VXMD_WHIST_LO': 0.0},  # fires early/often
    {'VXMD_DEPTH': 0.40, 'VXMD_DD_MIN': 0.20, 'VXMD_WHIST_HI': 0.8, 'VXMD_WHIST_LO': 0.0},  # deep-DD-only gate
    {'VXMD_DEPTH': 0.30, 'VXMD_DD_MIN': 0.10, 'VXMD_WHIST_HI': 0.4, 'VXMD_WHIST_LO': 0.0},  # low threshold
    {'VXMD_DEPTH': 0.50, 'VXMD_DD_MIN': 0.15, 'VXMD_WHIST_HI': 0.8, 'VXMD_WHIST_LO': 0.0},  # deep + mod gate
]

WINS_SCREEN = ['2020_crash', '2022', '2023', '2024', '2025', 'dip', '5y', '22-now']
DD_FOCUS    = ['2022', '2023', '5y', '22-now']   # where the user's vol-spike DD pain lives


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
        dd_2022=round(base['2022']['worst_dd'] - cand['2022']['worst_dd'], 3) if '2022' in wins else None,
        comp=round(sum(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins) / len(wins), 4),
        worst_comp=round(min(lr(cand[w]['med_ret']) - lr(base[w]['med_ret']) for w in wins), 4),
        # the crash-artifact tell: bull-2024 compound delta (should go NEGATIVE if VXMD cuts winners)
        comp_2024=round(lr(cand['2024']['med_ret']) - lr(base['2024']['med_ret']), 4) if '2024' in wins else None,
    )


def _fmt(base, wins):
    return {w: {'dd': base[w]['worst_dd'], 'med': base[w]['med_ret'], 'coll': base[w]['p_collapse']} for w in wins}


def run_phase(mode, n_iter, wins, cands, workers):
    base = run_candidate({}, n_iter, wins, workers=workers, tag=f'{mode}_base')
    print('BASELINE (VXMD off):', json.dumps(_fmt(base, wins)))
    results = []
    for i, c in enumerate(cands):
        params = {'VXMD_ENABLED': 1, **c}
        try:
            r = run_candidate(params, n_iter, wins, workers=workers, tag=f'{mode}_c{i:02d}')
        except Exception as e:
            print(f'  cand {i:02d} FAILED: {e}')
            continue
        s = score(base, r, wins)
        results.append({'i': i, 'params': c, **s, 'per_win': _fmt(r, wins)})
        print(f"c{i:02d} {c}\n     dd5y={s['dd_5y']} dd2022={s['dd_2022']} ddFoc={s['dd_red_focus']:+.2f} "
              f"comp={s['comp']:+.3f} comp2024={s['comp_2024']:+.3f} worstComp={s['worst_comp']:+.3f} coll={s['coll']}")
    # A clean PARETO ship would need: dd_5y > +1.0 AND comp >= ~0 AND comp_2024 >= ~0 AND coll==0
    clean = [x for x in results if x['coll'] == 0 and x['worst_comp'] >= -0.15
             and (x['dd_5y'] or 0) > 1.0 and x['comp'] >= -0.02]
    clean.sort(key=lambda x: -(x['dd_5y'] or 0))
    out = {'mode': mode, 'n_iter': n_iter, 'wins': wins,
           'baseline': _fmt(base, wins), 'results': results, 'ranked_clean': clean[:8]}
    with open(os.path.join(HERE, f'{mode}_results.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('\n=== CLEAN PARETO (coll=0, dd5y>+1.0, comp>=-0.02) ===')
    if clean:
        for x in clean[:8]:
            print(f"  c{x['i']:02d} dd5y={x['dd_5y']:+.2f} dd2022={x['dd_2022']:+.2f} comp={x['comp']:+.3f} "
                  f"comp2024={x['comp_2024']:+.3f} {x['params']}")
    else:
        print("  (NONE — VXMD is NULL: no config cuts 5y DD with compound flat-or-up)")
        print("  best by dd5y regardless of compound cost:")
        for x in sorted(results, key=lambda x: -(x['dd_5y'] or 0))[:6]:
            print(f"  c{x['i']:02d} dd5y={x['dd_5y']} dd2022={x['dd_2022']} comp={x['comp']:+.3f} "
                  f"comp2024={x['comp_2024']:+.3f} coll={x['coll']} {x['params']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', default='screen')
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()

    if a.mode == 'verify':
        vw = ['2022', '2024']
        b = run_candidate({}, a.n, vw, workers=a.workers, tag='verify_base')
        off = run_candidate({'VXMD_ENABLED': 0, 'VXMD_DEPTH': 0.5, 'VXMD_DD_MIN': 0.0},
                            a.n, vw, workers=a.workers, tag='verify_off')
        on = run_candidate({'VXMD_ENABLED': 1, 'VXMD_DEPTH': 0.4, 'VXMD_DD_MIN': 0.0, 'VXMD_WHIST_HI': 0.5},
                           a.n, vw, workers=a.workers, tag='verify_on')
        print('\nBASE   :', _fmt(b, vw))
        print('OFF    :', _fmt(off, vw), '(must EQUAL base -> true no-op when flag off)')
        print('ON     :', _fmt(on, vw))
        noop = all(abs(b[w]['worst_dd'] - off[w]['worst_dd']) < 1e-6
                   and abs(b[w]['med_ret'] - off[w]['med_ret']) < 1e-6 for w in vw)
        fires = any(abs(b[w]['worst_dd'] - on[w]['worst_dd']) > 1e-6 for w in vw)
        print(f"NO-OP when off: {noop}   |   FIRES when on: {fires}")
        return

    run_phase(a.mode, a.n, WINS_SCREEN, SCREEN_CANDS, a.workers)


if __name__ == '__main__':
    main()
