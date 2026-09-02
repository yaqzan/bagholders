"""Run monte_carlo.py with WVD overlay applied at signal-load time.

Monkey-patches `monte_carlo.load_signals` and `load_put_signals` to apply WVD's
smooth-gradient dampener after the standard load and before passing to the
MC pipeline. Production scoring code is NOT modified.

Usage:
  WINDOWS_OVERRIDE=22-now,5y N_ITER_OVERRIDE=200 python -u experiments/weekly_volume/mc_wvd.py
  WVD_ENABLE=0 ... python -u experiments/weekly_volume/mc_wvd.py    # baseline (no WVD)

The WVD constants are hardcoded here matching the v45 sweep top variant. To run
the baseline for comparison, set WVD_ENABLE=0.
"""
from __future__ import annotations
import os, sys, math, time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / 'calls_v45_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / 'puts_v45_1825.parquet'

# WVD ship constants (from v45 sweep top variant)
WVD_GATE_LO = 70
WVD_GATE_HI = 85
WVD_K_CALL = 0.40
WVD_K_POWER = 1.5
WVD_FORCE_SAT = 0.10
WVD_TARGET_CALL = 55.0

WVD_PUT_LO = 10
WVD_PUT_HI = 25
WVD_K_PUT = 0.50
WVD_K_PUT_POWER = 1.0
WVD_CMF_SAT = 0.40
WVD_TARGET_PUT = 33.0

WVD_ENABLE = os.environ.get('WVD_ENABLE', '1') == '1'

print(f'[mc_wvd] WVD_ENABLE={WVD_ENABLE}', flush=True)
print(f'[mc_wvd] Call: gate {WVD_GATE_LO}-{WVD_GATE_HI}, K={WVD_K_CALL}, power={WVD_K_POWER}, fsat={WVD_FORCE_SAT}, target={WVD_TARGET_CALL}', flush=True)
print(f'[mc_wvd] Put:  gate {WVD_PUT_LO}-{WVD_PUT_HI}, K={WVD_K_PUT}, power={WVD_K_PUT_POWER}, csat={WVD_CMF_SAT}, target={WVD_TARGET_PUT}', flush=True)


def build_wv_maps():
    """Load wv_force1 and wv_cmf4 per (symbol, date) from parquets."""
    print('[mc_wvd] loading WVD feature maps...', flush=True)
    t0 = time.time()
    calls = pl.read_parquet(CALLS_PARQ).select(['symbol', 'date', 'wv_force1', 'wv_cmf4'])
    puts  = pl.read_parquet(PUTS_PARQ).select(['symbol', 'date', 'wv_force1', 'wv_cmf4'])
    combined = pl.concat([calls, puts]).unique(subset=['symbol', 'date'])
    force_map = {}
    cmf_map   = {}
    for row in combined.iter_rows(named=True):
        key = (row['symbol'], row['date'])
        force_map[key] = row['wv_force1']
        cmf_map[key]   = row['wv_cmf4']
    print(f'[mc_wvd] feature maps: {len(force_map):,} entries ({time.time()-t0:.1f}s)', flush=True)
    return force_map, cmf_map


def main():
    force_map, cmf_map = build_wv_maps()

    # Import monte_carlo AFTER building maps (so WVD_ENABLE env propagates)
    import monte_carlo as mc

    _orig_load_calls = mc.load_signals
    _orig_load_puts  = mc.load_put_signals

    n_call_dampened_total = 0
    n_call_dropped_total  = 0
    n_put_lifted_total    = 0
    n_put_dropped_total   = 0

    def _wvd_load_calls(version, d_start, d_end):
        nonlocal n_call_dampened_total, n_call_dropped_total
        signals = _orig_load_calls(version, d_start, d_end)
        if not WVD_ENABLE:
            return signals
        out = []
        score_range = max(1, WVD_GATE_HI - WVD_GATE_LO)
        for s in signals:
            d_iso = s.date.isoformat() if hasattr(s.date, 'isoformat') else s.date
            force1 = force_map.get((s.symbol_id, d_iso))
            if force1 is None or force1 <= 0:
                out.append(s)
                continue
            ov = float(s.overall)
            if ov < WVD_GATE_LO:
                out.append(s)
                continue
            score_norm = max(0.0, (ov - WVD_GATE_LO) / score_range)
            K_eff = WVD_K_CALL * (score_norm ** WVD_K_POWER)
            force_grad = math.tanh(force1 / WVD_FORCE_SAT)
            new_ov = round(ov - K_eff * force_grad * (ov - WVD_TARGET_CALL))
            new_ov = int(max(0, min(100, new_ov)))
            if new_ov != int(s.overall):
                n_call_dampened_total += 1
            if new_ov < 70:   # OVERFLOW_THRESHOLD
                n_call_dropped_total += 1
                continue
            s.overall = new_ov
            out.append(s)
        return out

    def _wvd_load_puts(version, d_start, d_end):
        nonlocal n_put_lifted_total, n_put_dropped_total
        signals = _orig_load_puts(version, d_start, d_end)
        if not WVD_ENABLE:
            return signals
        out = []
        score_range = max(1, WVD_PUT_HI - WVD_PUT_LO)
        for s in signals:
            d_iso = s.date.isoformat() if hasattr(s.date, 'isoformat') else s.date
            cmf4 = cmf_map.get((s.symbol_id, d_iso))
            if cmf4 is None or cmf4 >= 0:
                out.append(s)
                continue
            ov = float(s.overall)
            if ov > WVD_PUT_HI:
                out.append(s)
                continue
            score_grad = max(0.0, (WVD_PUT_HI - ov) / score_range)
            score_grad = min(1.0, score_grad)
            K_eff = WVD_K_PUT * (score_grad ** WVD_K_PUT_POWER)
            cmf_grad = math.tanh(-cmf4 / WVD_CMF_SAT)
            new_ov = round(ov + K_eff * cmf_grad * (WVD_TARGET_PUT - ov))
            new_ov = int(max(0, min(100, new_ov)))
            if new_ov != int(s.overall):
                n_put_lifted_total += 1
            if new_ov > 25:
                n_put_dropped_total += 1
                continue
            s.overall = new_ov
            out.append(s)
        return out

    mc.load_signals = _wvd_load_calls
    mc.load_put_signals = _wvd_load_puts
    print('[mc_wvd] runtime patches installed (load_signals, load_put_signals)', flush=True)

    try:
        mc.main()
    finally:
        mc.load_signals = _orig_load_calls
        mc.load_put_signals = _orig_load_puts
        print('\n' + '=' * 80, flush=True)
        print('[mc_wvd] WVD displacement summary across all windows:', flush=True)
        print(f'  Calls dampened: {n_call_dampened_total:,}  dropped (overall<70): {n_call_dropped_total:,}', flush=True)
        print(f'  Puts  lifted:   {n_put_lifted_total:,}    dropped (overall>25): {n_put_dropped_total:,}', flush=True)


if __name__ == '__main__':
    main()
