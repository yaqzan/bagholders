"""
Phase OP1 — Screening sweep under option-pricing-aware MC
==========================================================
First Bayesian retune after the option-pricing model + random-fill MC ship
(2026-04-29). Under the new pricing, theta accumulates on every SL fire and
the σ-barrier no longer maps cleanly to a -20% / -35% premium loss — actual
realized losses are deeper. This invalidates the static-pricing-calibrated
shipped knobs (B68 DD breaker, F3F floors, HARD_SELL, PUT_SL, etc.).

Strategy: 22-now-only screening at N=80 (~5 min/config × 12 configs ≈ 1 hour).
Top-3 advance to full N=300 × 8 windows validation in a follow-up script.

Configs sweep the most likely retune targets (informed by the bug-fix
session log in docs/v27-optimization-log.md):

  C0  baseline         — current shipped (DD=0.68, HARD=-0.40, PUT_SL=-0.20, SL=-0.30)
  C1  tighter_DD       — DD=0.55 (catch losses sooner under deeper theta)
  C2  tighter_DD_alt   — DD=0.60
  C3  wider_PUTSL      — PUT_SL=-0.30 (wider barrier so realized ~= old -20%)
  C4  wider_CALLSL     — SL_BASE=-0.40, SL_STRESS=-0.45
  C5  shorter_HOLD     — HOLD_DAYS=12 (less theta accumulation)
  C6  smaller_ULTRA    — TIER_ALLOC.ultra=0.13 (DD-safer ultra concentration)
  C7  smaller_PUTTIER  — PUT_TIER_ALLOC put_top=0.07
  C8  combo_safety     — DD=0.55 + ULTRA=0.13
  C9  combo_balanced   — DD=0.60 + HOLD=12 + PUT_SL=-0.25
  C10 wider_HARD       — HARD=-0.45 (capture some recoveries)
  C11 deeper_HARD      — HARD=-0.35 (cut losses sooner)

Output: experiments/phase_op1_screening_results.json + console summary
sorted by utility (return + DD penalty).
"""
import os
import sys
import json
import math
import statistics
import multiprocessing
import time
from datetime import date
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Defer stdout encoding to PYTHONIOENCODING env var (set by caller); avoid
# reassigning sys.stdout because monte_carlo.py already did so and colorama
# wrappers don't play nicely with stacked TextIOWrapper.

import monte_carlo as mc

# ── Sweep config ─────────────────────────────────────────────────────────────

WINDOW = ('22-now', date(2022, 1, 1), date(2026, 4, 24))
N_ITER = 80
MC_WORKERS_CT = 4

CONFIGS = {
    'C0_baseline':       {},   # shipped
    'C1_tighter_DD55':   dict(DD_CIRCUIT_BREAKER=0.55),
    'C2_tighter_DD60':   dict(DD_CIRCUIT_BREAKER=0.60),
    'C3_wider_PUTSL':    dict(PUT_SL=-0.30),
    'C4_wider_CALLSL':   dict(SL_BASE=-0.40, SL_STRESS=-0.45),
    'C5_shorter_HOLD':   dict(HOLD_DAYS=12),
    'C6_smaller_ULTRA':  dict(TIER_ALLOC={'ultra':0.13,'top':0.12,'mid':0.15,'low':0.15,'overflow':0.0}),
    'C7_smaller_PUTTOP': dict(PUT_TIER_ALLOC={'put_top':0.07,'put_mid':0.12,'put_low':0.12}),
    'C8_combo_safety':   dict(DD_CIRCUIT_BREAKER=0.55,
                              TIER_ALLOC={'ultra':0.13,'top':0.12,'mid':0.15,'low':0.15,'overflow':0.0}),
    'C9_combo_balanced': dict(DD_CIRCUIT_BREAKER=0.60, HOLD_DAYS=12, PUT_SL=-0.25),
    'C10_wider_HARD':    dict(HARD_SELL_LOSS=-0.45),
    'C11_deeper_HARD':   dict(HARD_SELL_LOSS=-0.35),
}


# ── Apply config helper ─────────────────────────────────────────────────────

def _apply_config(cfg):
    """Mutate monte_carlo module-level constants in-place. Single-process only."""
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


def _restore_baseline_state():
    """Restore from a freshly-imported snapshot. Returns dict of all touched constants."""
    return {
        'DD_CIRCUIT_BREAKER': 0.68,
        'HOLD_DAYS': 15,
        'TIER_ALLOC': {'ultra':0.18,'top':0.12,'mid':0.15,'low':0.15,'overflow':0.0},
        'PUT_TIER_ALLOC': {'put_top':0.10,'put_mid':0.12,'put_low':0.12},
        'PUT_SL': -0.20,
        'SL_BASE': -0.30,
        'SL_STRESS': -0.35,
        'HARD_SELL_LOSS': -0.40,
    }


# ── Run one config ──────────────────────────────────────────────────────────

def run_config(cfg_name, cfg, version, n_iter=N_ITER, mc_workers=MC_WORKERS_CT):
    """Subprocess one config so each gets a fresh multiprocessing pool.
    Windows leaks pools when reused across configs; subprocess isolation
    is the cleanest fix.
    """
    import subprocess
    label, d_start, d_end = WINDOW
    out_path = f'experiments/_op1_tmp_{cfg_name}.json'
    print(f"\n{'='*100}")
    print(f"CONFIG: {cfg_name}   {cfg}")
    print(f"{'='*100}", flush=True)
    t0 = time.time()
    cmd = [
        sys.executable, '-u',
        'experiments/bayes_phase_op1_runner.py',
        json.dumps(cfg),
        label, d_start.isoformat(), d_end.isoformat(),
        str(n_iter), str(mc_workers), out_path,
    ]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    # Discard subprocess stdout/stderr to avoid OS pipe buffer fill on Windows
    log_path = f'experiments/_op1_log_{cfg_name}.out'
    with open(log_path, 'wb') as logf:
        proc = subprocess.run(cmd, env=env, stdout=logf, stderr=logf, timeout=1800)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        with open(log_path) as f:
            tail = f.read()[-2000:]
        print(f"  FAILED (rc={proc.returncode})\n{tail}", flush=True)
        return None
    print(f"  elapsed: {elapsed:.1f}s", flush=True)
    with open(out_path) as f:
        out = json.load(f)
    out['cfg_name'] = cfg_name
    out['cfg'] = cfg
    out['elapsed_s'] = elapsed
    return out


def utility(r, dd_floor=0.80, dd_target=0.65):
    """Geometric-style utility:
        log(1 + mean_ret/100) — penalty for DD over target, hard-cap above floor.

    DD penalty: 0 if worst_dd <= 65%; quadratic ramp 0→4 between 65-80%; +∞ above 80%.
    Mean-ret guarded against negative (log(1+x) for x>-0.99) and capped at log(1e16) to
    keep numerics sane with the multi-billion-percent compounding.
    """
    mr = r['mean_ret'] / 100.0
    mr_clip = max(-0.99, min(mr, 1e16))
    ret_term = math.log(1.0 + mr_clip)
    dd = r['worst_dd'] / 100.0
    if dd >= dd_floor:
        dd_pen = 1e6   # hard reject
    elif dd > dd_target:
        f = (dd - dd_target) / (dd_floor - dd_target)
        dd_pen = 4.0 * f * f
    else:
        dd_pen = 0.0
    coll_pen = 100.0 * r['p_coll'] / 100.0   # any collapse rate is bad
    return ret_term - dd_pen - coll_pen


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"Window: {WINDOW[0]} ({WINDOW[1]} -> {WINDOW[2]})", flush=True)
    print(f"N_ITER per config: {N_ITER}", flush=True)
    print(f"Configs: {len(CONFIGS)}", flush=True)

    results = []
    t_start = time.time()
    for name, cfg in CONFIGS.items():
        try:
            r = run_config(name, cfg, version=None)
            if r is None:
                continue
            r['utility'] = utility(r)
            results.append(r)
            # incremental save
            with open('experiments/phase_op1_screening_results.json', 'w') as f:
                json.dump(results, f, indent=2, default=str)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            import traceback; traceback.print_exc()

    print(f"\nTotal elapsed: {time.time()-t_start:.0f}s", flush=True)

    # Sort by utility desc
    results.sort(key=lambda x: -x['utility'])
    print(f"\n{'='*120}")
    print(f"PHASE OP1 — RANKED RESULTS (22-now × N={N_ITER} × seeded mode)")
    print(f"{'='*120}")
    print(f"{'Rank':>4}  {'Config':<22} {'Util':>8} {'MeanRet':>15} {'WorstDD':>9} {'CTP%':>6} {'PTP%':>6} {'CTrd':>6} {'PTrd':>6}")
    print('-'*120)
    for i, r in enumerate(results, 1):
        print(f"{i:>4}  {r['cfg_name']:<22} {r['utility']:>+8.2f} "
              f"{r['mean_ret']:>+13,.1f}%  "
              f"{r['worst_dd']:>8.1f}% "
              f"{r['call_tp']:>5.1f}% {r['put_tp']:>5.1f}% "
              f"{r['call_trades']:>6.1f} {r['put_trades']:>6.1f}")
    print(f"{'='*120}")
    print(f"\nResults saved to experiments/phase_op1_screening_results.json")


if __name__ == '__main__':
    main()
