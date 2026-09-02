"""Run monte_carlo.py with WVD-Wave (score-stage Gaussian lift + tanh dampen) overlay.

Score-stage modulator captures the FULL inverted-U cohort signal:
  - LIFTS scores when wv_force1 is in moderate-positive zone (anti-climax)
  - DAMPENS scores when wv_force1 is extreme-positive (climax)
  - Both smooth gradients, no labels, no tier overrides.

Architecturally consistent with v37+v38+v39+v43+v44 (score-stage continuous).

Usage:
  WINDOWS_OVERRIDE=22-now,5y N_ITER_OVERRIDE=300 python -u experiments/weekly_volume/mc_wave.py
  WAVE_ENABLE=0 ... python -u experiments/weekly_volume/mc_wave.py    # baseline
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

# Wave constants — populated from wave_sweep top variant via env overrides
WAVE_ENABLE       = os.environ.get('WAVE_ENABLE', '1') == '1'
WAVE_GATE_LO      = int(os.environ.get('WAVE_GATE_LO', '70'))
WAVE_GATE_HI      = int(os.environ.get('WAVE_GATE_HI', '85'))
WAVE_SCORE_POWER  = float(os.environ.get('WAVE_SCORE_POWER', '1.0'))
WAVE_PEAK         = float(os.environ.get('WAVE_PEAK', '0.0'))
WAVE_WIDTH        = float(os.environ.get('WAVE_WIDTH', '0.06'))
WAVE_K_LIFT       = float(os.environ.get('WAVE_K_LIFT', '0.20'))
WAVE_TARGET_LIFT  = float(os.environ.get('WAVE_TARGET_LIFT', '85.0'))
WAVE_CLIMAX_THRESH = float(os.environ.get('WAVE_CLIMAX_THRESH', '0.08'))
WAVE_CLIMAX_SAT   = float(os.environ.get('WAVE_CLIMAX_SAT', '0.15'))
WAVE_K_DAMPEN     = float(os.environ.get('WAVE_K_DAMPEN', '0.30'))
WAVE_TARGET_DAMPEN = float(os.environ.get('WAVE_TARGET_DAMPEN', '60.0'))

print(f'[mc_wave] WAVE_ENABLE={WAVE_ENABLE}', flush=True)
print(f'[mc_wave] gate {WAVE_GATE_LO}-{WAVE_GATE_HI}, score_power={WAVE_SCORE_POWER}', flush=True)
print(f'[mc_wave] LIFT: peak={WAVE_PEAK}, width={WAVE_WIDTH}, K={WAVE_K_LIFT}, target={WAVE_TARGET_LIFT}', flush=True)
print(f'[mc_wave] DAMPEN: thresh={WAVE_CLIMAX_THRESH}, sat={WAVE_CLIMAX_SAT}, K={WAVE_K_DAMPEN}, target={WAVE_TARGET_DAMPEN}', flush=True)


def build_wv_force_map():
    """Pre-load wv_force1 per (symbol, date) from parquets."""
    print('[mc_wave] loading WVD feature maps...', flush=True)
    t0 = time.time()
    calls = pl.read_parquet(CALLS_PARQ).select(['symbol', 'date', 'wv_force1'])
    puts  = pl.read_parquet(PUTS_PARQ).select(['symbol', 'date', 'wv_force1'])
    combined = pl.concat([calls, puts]).unique(subset=['symbol', 'date'])
    force_map = {}
    for row in combined.iter_rows(named=True):
        force_map[(row['symbol'], row['date'])] = row['wv_force1']
    print(f'[mc_wave] feature maps: {len(force_map):,} entries ({time.time()-t0:.1f}s)', flush=True)
    return force_map


def main():
    force_map = build_wv_force_map()

    import monte_carlo as mc

    _orig_load_calls = mc.load_signals

    n_lifted = 0
    n_dampened = 0
    n_promoted = 0   # below 70 → above 70
    n_demoted = 0    # above 70 → below 70

    def _wave_load_calls(version, d_start, d_end):
        nonlocal n_lifted, n_dampened, n_promoted, n_demoted
        signals = _orig_load_calls(version, d_start, d_end)
        if not WAVE_ENABLE:
            return signals
        out = []
        score_range = max(1, WAVE_GATE_HI - WAVE_GATE_LO)
        for s in signals:
            d_iso = s.date.isoformat() if hasattr(s.date, 'isoformat') else s.date
            force1 = force_map.get((s.symbol_id, d_iso))
            ov = float(s.overall)
            if force1 is None or ov < WAVE_GATE_LO:
                out.append(s)
                continue
            score_norm = max(0.0, min(1.0, (ov - WAVE_GATE_LO) / score_range))
            score_w = score_norm ** WAVE_SCORE_POWER

            # Bell lift
            delta = force1 - WAVE_PEAK
            bell = math.exp(-(delta / WAVE_WIDTH)**2)

            # Climax dampen
            excess = max(0.0, force1 - WAVE_CLIMAX_THRESH)
            dampen_grad = math.tanh(excess / WAVE_CLIMAX_SAT)

            lift_amt   = WAVE_K_LIFT   * score_w * bell        * (WAVE_TARGET_LIFT - ov)
            dampen_amt = WAVE_K_DAMPEN * score_w * dampen_grad * (ov - WAVE_TARGET_DAMPEN)

            new_ov = round(ov + lift_amt - dampen_amt)
            new_ov = int(max(0, min(100, new_ov)))

            if new_ov > int(s.overall):
                n_lifted += 1
            elif new_ov < int(s.overall):
                n_dampened += 1

            if int(s.overall) >= 70 and new_ov < 70:
                n_demoted += 1
                continue   # dropped from 70+ universe (rare given gate_lo=70)
            if int(s.overall) < 70 and new_ov >= 70:
                n_promoted += 1
            s.overall = new_ov
            out.append(s)
        return out

    mc.load_signals = _wave_load_calls
    print('[mc_wave] runtime patches installed (load_signals only — calls only)', flush=True)

    try:
        mc.main()
    finally:
        mc.load_signals = _orig_load_calls
        print('\n' + '=' * 80, flush=True)
        print('[mc_wave] WVD-Wave displacement summary across all windows:', flush=True)
        print(f'  Calls lifted:    {n_lifted:,}', flush=True)
        print(f'  Calls dampened:  {n_dampened:,}', flush=True)
        print(f'  Calls promoted (<70 → >=70): {n_promoted:,}', flush=True)
        print(f'  Calls demoted (>=70 → <70): {n_demoted:,}', flush=True)


if __name__ == '__main__':
    main()
