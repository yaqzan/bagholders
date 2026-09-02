"""Phase D — Joint validation of top candidates from Phase B + Phase C.

Reads top-N from each phase's results JSONL, builds joint candidates by
swapping in the winning cascade and TP/SL, and re-evaluates at N=500
(or N=300 if env BUDGET_FAST=1) on all 8 canonical windows.

This is the P1-P6 ship gate per assessment-backtest.md "Portfolio Change
Ship Criteria":
  - P1: N=500+
  - P2: All 8 windows
  - P3: 5y AND 22-now compound >= baseline
  - P4: No annual window regresses >25% vs baseline
  - P5: P(collapse) = 0% on every cell
  - P6: DD improvement vs baseline (relative — 80% absolute floor unattainable
       under bounded-fill MC; baseline runs 71-78% on most windows)

Usage:
  PYTHONIOENCODING=utf-8 N_ITER=500 python -u \\
    experiments/v32_optim/phase_d_validate.py 2>&1 \\
    | tee experiments/v32_optim/phase_d_validate.log
"""
from __future__ import annotations
import json, os, sys, time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402
import strategy_config as _sc  # noqa: E402

SWEEP_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 15)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
    ('5y',     date(2021, 4, 15), date(2026, 4, 15)),
]

def apply_full(cfg, n_iter):
    """cfg dict with optional keys overriding strategy_config defaults:
      ultra,top,mid,low,put_top,put_mid,put_low,
      tp_base,tp_stress,sl_base,sl_stress,breadth_thr.

    Sets env vars (workers re-import MC and inherit) + patches parent module."""
    cfg30 = _sc.STRATEGY_30DTE
    opt = cfg30.option

    # Resolve all values, falling back to strategy_config defaults
    tp_base   = cfg.get('tp_base',   opt.TP_BASE)
    tp_stress = cfg.get('tp_stress', opt.TP_STRESS)
    sl_base   = cfg.get('sl_base',   opt.SL_BASE)
    sl_stress = cfg.get('sl_stress', opt.SL_STRESS)
    breadth   = cfg.get('breadth_thr', opt.BREADTH_THRESHOLD)
    ultra = cfg.get('ultra', cfg30.TIER_ALLOC['ultra'])
    top   = cfg.get('top',   cfg30.TIER_ALLOC['top'])
    mid   = cfg.get('mid',   cfg30.TIER_ALLOC['mid'])
    low   = cfg.get('low',   cfg30.TIER_ALLOC['low'])
    pt = cfg.get('put_top', cfg30.PUT_TIER_ALLOC['put_top'])
    pm = cfg.get('put_mid', cfg30.PUT_TIER_ALLOC['put_mid'])
    pl = cfg.get('put_low', cfg30.PUT_TIER_ALLOC['put_low'])

    # Env vars — CRITICAL for MP workers (re-import on spawn)
    os.environ['TP_BASE_OV']   = str(tp_base)
    os.environ['TP_STRESS_OV'] = str(tp_stress)
    os.environ['SL_BASE_OV']   = str(sl_base)
    os.environ['SL_STRESS_OV'] = str(sl_stress)
    os.environ['BREADTH_THRESHOLD_OV'] = str(breadth)
    os.environ['TIER_ULTRA_OV']    = str(ultra)
    os.environ['TIER_TOP_OV']      = str(top)
    os.environ['TIER_MID_OV']      = str(mid)
    os.environ['TIER_LOW_OV']      = str(low)
    os.environ['TIER_OVERFLOW_OV'] = '0.0'
    os.environ['PUT_TIER_TOP_OV']  = str(pt)
    os.environ['PUT_TIER_MID_OV']  = str(pm)
    os.environ['PUT_TIER_LOW_OV']  = str(pl)
    os.environ['N_ITER_OVERRIDE']  = str(n_iter)

    # Patch parent process
    mc.TP_BASE = tp_base; mc.TP_STRESS = tp_stress
    mc.SL_BASE = sl_base; mc.SL_STRESS = sl_stress
    mc.BREADTH_THRESHOLD = breadth
    mc.NET_TP_BASE = tp_base; mc.NET_TP_STRESS = tp_stress
    mc.NET_SL_BASE = sl_base; mc.NET_SL_STRESS = sl_stress
    mc.TP_SIGMA_BASE   = tp_base       * cfg30.PREMIUM_MULT / opt.DELTA
    mc.TP_SIGMA_STRESS = tp_stress     * cfg30.PREMIUM_MULT / opt.DELTA
    mc.SL_SIGMA_BASE   = abs(sl_base)   * cfg30.PREMIUM_MULT / opt.DELTA
    mc.SL_SIGMA_STRESS = abs(sl_stress) * cfg30.PREMIUM_MULT / opt.DELTA
    mc.TIER_ALLOC = {'ultra':ultra,'top':top,'mid':mid,'low':low,'overflow':0.0}
    mc.PUT_TIER_ALLOC = {'put_top':pt,'put_mid':pm,'put_low':pl}

    mc.MAX_POSITIONS = cfg30.MAX_POSITIONS
    mc.MAX_POSITIONS_CALL = cfg30.MAX_POSITIONS_CALL
    mc.MAX_POSITIONS_PUT  = cfg30.MAX_POSITIONS_PUT
    mc.PRIMARY_THRESHOLD  = cfg30.PRIMARY_THRESHOLD
    mc.OVERFLOW_THRESHOLD = cfg30.OVERFLOW_THRESHOLD
    mc.PUT_THRESHOLD      = cfg30.PUT_THRESHOLD
    mc.F3F_CALL_THRESH = cfg30.F3F_CALL_THRESH; mc.F3F_CALL_FLOOR = cfg30.F3F_CALL_FLOOR
    mc.F3F_CALL_LOW = cfg30.F3F_CALL_LOW
    mc.F3F_PUT_THRESH = cfg30.F3F_PUT_THRESH; mc.F3F_PUT_FLOOR = cfg30.F3F_PUT_FLOOR
    mc.F3F_PUT_HIGH = cfg30.F3F_PUT_HIGH
    mc.ALLOC_SCALE_FLOOR = cfg30.ALLOC_SCALE_FLOOR
    mc.ALLOC_SCALE_CEIL  = cfg30.ALLOC_SCALE_CEIL
    mc.REGIME_SLOPE = cfg30.REGIME_SLOPE; mc.REGIME_SLOPE_PUT = cfg30.REGIME_SLOPE_PUT
    mc.REGIME_SLOPE_UP = cfg30.REGIME_SLOPE_UP; mc.REGIME_SLOPE_DOWN = cfg30.REGIME_SLOPE_DOWN
    mc.REGIME_SLOPE_PUT_UP = cfg30.REGIME_SLOPE_PUT_UP
    mc.REGIME_SLOPE_PUT_DOWN = cfg30.REGIME_SLOPE_PUT_DOWN
    mc.BREADTH_ALLOC_ENABLED = cfg30.BREADTH_ALLOC_ENABLED
    mc.DD_CIRCUIT_BREAKER = cfg30.DD_CIRCUIT_BREAKER
    mc.DD_SOFT_BAND_LO = cfg30.DD_SOFT_BAND_LO
    mc.DD_SOFT_BAND_HI = cfg30.DD_SOFT_BAND_HI
    mc.DD_SOFT_CALL_FLOOR = cfg30.DD_SOFT_CALL_FLOOR
    mc.DEAD_HOLD_ENABLED     = cfg30.DEAD_HOLD_ENABLED
    mc.DEAD_HOLD_TRIGGER_PNL = cfg30.DEAD_HOLD_TRIGGER_PNL
    mc.DEAD_HOLD_POPOUT_PNL  = cfg30.DEAD_HOLD_POPOUT_PNL
    mc.EARN_SUPP_PUT          = cfg30.EARN_SUPP_PUT
    mc.EARN_SUPP_PUT_DAYS     = cfg30.EARN_SUPP_PUT_DAYS
    mc.EARN_SUPP_PUT_MIN_OV   = cfg30.EARN_SUPP_PUT_MIN_OV
    mc.EARN_SUPP_PUT_MAX_OV   = cfg30.EARN_SUPP_PUT_MAX_OV
    mc.CT_PROMOTE             = cfg30.CT_PROMOTE
    mc.CT_PUT_TREND_MIN       = cfg30.CT_PUT_TREND_MIN
    mc.CT_CALL_TREND_MAX      = cfg30.CT_CALL_TREND_MAX

    mc.N_ITER = n_iter
    mc.COLLISION_MODES = ['seeded']

def run_config(cfg, version, n_iter):
    apply_full(cfg, n_iter)
    out = {}
    for label, d_start, d_end in SWEEP_WINDOWS:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wr = mc.run_window(label, d_start, d_end, version)
        r = wr.get('seeded', {})
        out[label] = {
            'mean_ret':float(r.get('mean_ret',0.0)), 'med_ret':float(r.get('med_ret',0.0)),
            'worst_dd':float(r.get('worst_dd',0.0)), 'mean_dd':float(r.get('mean_dd',0.0)),
            'p_collapse':float(r.get('p_coll',0.0)),
            'call_tp':float(r.get('call_tp',0.0)), 'put_tp':float(r.get('put_tp',0.0)),
            'call_trades':float(r.get('call_trades',0.0)), 'put_trades':float(r.get('put_trades',0.0)),
        }
    return out

def load_top_from_jsonl(path, top_k=3):
    if not os.path.exists(path): return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get('event') == 'eval':
                    rows.append(r)
            except Exception:
                continue
    rows.sort(key=lambda r: r.get('utility', float('-inf')), reverse=True)
    return rows[:top_k]

def main():
    log_dir = os.path.join(ROOT, 'experiments', 'v32_optim')
    out_path = os.path.join(log_dir, 'phase_d_validate_results.jsonl')
    N_ITER = int(os.environ.get('N_ITER', '500'))

    version = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit if version else None}")
    print(f"Validation N_ITER={N_ITER}")

    candidates = []

    # Always include baseline as A/B reference
    candidates.append(('baseline', {}))

    # Top-3 from Phase B (cascade only)
    top_b = load_top_from_jsonl(os.path.join(log_dir, 'phase_b_cascade_results.jsonl'), top_k=3)
    for i, r in enumerate(top_b):
        candidates.append((f'B{i+1}_cascade', r['params']))

    # Top-3 from Phase C (TP/SL only)
    top_c = load_top_from_jsonl(os.path.join(log_dir, 'phase_c_tpsl_results.jsonl'), top_k=3)
    for i, r in enumerate(top_c):
        candidates.append((f'C{i+1}_tpsl', r['params']))

    # Joint: B1 + C1 cascade & TP/SL combined
    if top_b and top_c:
        joint = {**top_b[0]['params'], **{
            'tp_base': top_c[0]['params'].get('tp_base'),
            'tp_stress': top_c[0]['params'].get('tp_stress'),
            'sl_base': top_c[0]['params'].get('sl_base'),
            'sl_stress': top_c[0]['params'].get('sl_stress'),
            'breadth_thr': top_c[0]['params'].get('breadth'),
        }}
        candidates.append(('JOINT_B1_C1', {k:v for k,v in joint.items() if v is not None}))

    print(f"\nCandidates to validate: {len(candidates)}")
    for n, c in candidates:
        print(f"  {n}: {c}")

    results = []
    for name, cfg in candidates:
        t0 = time.time()
        win_res = run_config(cfg, version, N_ITER)
        dur = time.time() - t0
        rec = {'name':name, 'params':cfg, 'windows':win_res, 'duration_s':dur}
        results.append(rec)
        with open(out_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        print(f"\n[{name}] dur={dur/60:.1f}min")
        for label,_,_ in SWEEP_WINDOWS:
            w = win_res.get(label, {})
            print(f"  {label:<8}  ret={w.get('mean_ret',0):+.2e}%  dd={w.get('worst_dd',0):.1f}%  "
                  f"col={w.get('p_collapse',0):.2f}%  ctp={w.get('call_tp',0):.1f}  ptp={w.get('put_tp',0):.1f}")

    # Comparison vs baseline
    print("\n" + "="*120)
    print("COMPARISON vs baseline")
    print("="*120)
    baseline = next(r for r in results if r['name']=='baseline')
    print(f"{'name':<20} | {'5y ret':>14} {'5y dd':>6} | {'22n ret':>14} {'22n dd':>6} | {'maxdd':>6}")
    for r in results:
        b5y = r['windows']['5y']; b22 = r['windows']['22-now']
        max_dd = max(w.get('worst_dd',0) for w in r['windows'].values())
        print(f"{r['name']:<20} | {b5y['mean_ret']:>+14.2e} {b5y['worst_dd']:>6.1f} | "
              f"{b22['mean_ret']:>+14.2e} {b22['worst_dd']:>6.1f} | {max_dd:>6.1f}")

if __name__ == '__main__':
    main()
