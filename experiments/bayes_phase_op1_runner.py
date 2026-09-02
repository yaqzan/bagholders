"""Inner runner — one config at a time, exits cleanly. Called by
`bayes_phase_op1_screening.py` via subprocess so each config gets a fresh
multiprocessing pool (Windows leaks pools when reused across configs).
"""
import os
import sys
import json
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monte_carlo as mc


def _apply_config(cfg):
    for k, v in cfg.items():
        if k == 'TIER_ALLOC':
            mc.TIER_ALLOC = dict(v)
        elif k == 'PUT_TIER_ALLOC':
            mc.PUT_TIER_ALLOC = dict(v)
        elif k == 'PUT_SL':
            mc.PUT_SL = v
            mc.PUT_NET_SL = v + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.PUT_SL_SIGMA = abs(v) * mc.PREMIUM_MULT / mc.DELTA
        elif k == 'SL_BASE':
            mc.SL_BASE = v
            mc.NET_SL_BASE = v + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.SL_SIGMA_BASE = abs(v) * mc.PREMIUM_MULT / mc.DELTA
        elif k == 'SL_STRESS':
            mc.SL_STRESS = v
            mc.NET_SL_STRESS = v + mc.SLIP_ENTRY + mc.SLIP_SL
            mc.SL_SIGMA_STRESS = abs(v) * mc.PREMIUM_MULT / mc.DELTA
        elif k == 'HARD_SELL_LOSS':
            mc.HARD_SELL_LOSS = v
            mc.NET_HARD_SELL = v + mc.SLIP_ENTRY + mc.SLIP_HARD
        else:
            setattr(mc, k, v)


def main():
    """argv: cfg_json window_label window_start window_end n_iter mc_workers out_path"""
    cfg = json.loads(sys.argv[1])
    label = sys.argv[2]
    d_start = date.fromisoformat(sys.argv[3])
    d_end = date.fromisoformat(sys.argv[4])
    n_iter = int(sys.argv[5])
    mc_workers = int(sys.argv[6])
    out_path = sys.argv[7]

    mc.N_ITER = n_iter
    # Force single-process: spawned workers re-import monte_carlo and don't
    # see the in-process mutations to module-level constants. Single-process
    # ensures all run_single_sim calls use the same mutated state.
    os.environ['MC_NO_MP'] = '1'
    _apply_config(cfg)

    pin = os.environ.get('ALGORITHM_VERSION_PIN', '').strip()
    if pin:
        version = mc.AlgorithmVersion.get(mc.AlgorithmVersion.git_commit == pin)
    else:
        version = mc.AlgorithmVersion.get_active_scores_version()

    res = mc.run_window(label, d_start, d_end, version)
    r = res['seeded']
    out = {
        'cfg': cfg,
        'label': label,
        'mean_ret': r['mean_ret'],
        'med_ret':  r['med_ret'],
        'mean_dd':  r['mean_dd'],
        'worst_dd': r['worst_dd'],
        'p_coll':   r['p_coll'],
        'call_tp':  r['call_tp'],
        'put_tp':   r['put_tp'],
        'call_trades': r['call_trades'],
        'put_trades':  r['put_trades'],
    }
    with open(out_path, 'w') as f:
        json.dump(out, f, default=str)


if __name__ == '__main__':
    main()
