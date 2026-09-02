"""Subprocess-based MC candidate driver for the v69 honest-substrate portfolio retune.

WHY subprocess (not in-process patching like v32 phase_b):
  monte_carlo.py computes TP_SIGMA_BASE / SL_SIGMA / PUT_*_SIGMA / NET_* and the
  PUT_SL_HOLD globals at MODULE IMPORT from env vars. The per-candidate precompute
  (precompute_call_outcomes / precompute_put_outcomes) runs in the PARENT process,
  using the parent's frozen globals. Setting env vars mid-run does NOT re-derive the
  parent's frozen TP/SL sigma globals, so an in-process TP/SL sweep would silently
  run the parent precompute at the baseline barriers. A fresh subprocess re-imports
  monte_carlo with the candidate env vars set, so BOTH the parent precompute AND the
  MP workers see the correct barriers. This is the only correct harness for varying
  TP/SL. (Cascade-only sweeps could patch in-process, but we use one path for all.)

Reads the SUMMARY table printed by monte_carlo.main() and the per-window 'seeded'
line (CTP/PTP/CTrd/PTrd). Returns a per-window dict.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MC = os.path.join(ROOT, 'monte_carlo.py')

# Env var name for each tunable param (matches monte_carlo.py overrides exactly).
ENV_MAP = {
    'TP_BASE': 'TP_BASE_OV',
    'TP_STRESS': 'TP_STRESS_OV',
    'SL_BASE': 'SL_BASE_OV',
    'SL_STRESS': 'SL_STRESS_OV',
    'BREADTH_THRESHOLD': 'BREADTH_THRESHOLD_OV',
    'PUT_TP': 'PUT_TP_OV',
    'PUT_SL': 'PUT_SL_OV',
    'TIER_ULTRA': 'TIER_ULTRA_OV',
    'TIER_TOP': 'TIER_TOP_OV',
    'TIER_MID': 'TIER_MID_OV',
    'TIER_LOW': 'TIER_LOW_OV',
    'TIER_OVERFLOW': 'TIER_OVERFLOW_OV',
    'PUT_TIER_TOP': 'PUT_TIER_TOP_OV',
    'PUT_TIER_MID': 'PUT_TIER_MID_OV',
    'PUT_TIER_LOW': 'PUT_TIER_LOW_OV',
    'MAX_POSITIONS': 'MAX_POSITIONS_OVERRIDE',
    'HOLD_DAYS': 'HOLD_DAYS_OV',
    'MAX_POSITIONS_CALL': 'MAX_POSITIONS_CALL',
    'MAX_POSITIONS_PUT': 'MAX_POSITIONS_PUT',
    'DD_SOFT_BAND_LO': 'DD_SOFT_BAND_LO',
    'DD_SOFT_BAND_HI': 'DD_SOFT_BAND_HI',
    'DD_SOFT_CALL_FLOOR': 'DD_SOFT_CALL_FLOOR',
    'SLIP_ENTRY': 'SLIP_ENTRY_OV',
    'SLIP_TP': 'SLIP_TP_OV',
    'SLIP_SL': 'SLIP_SL_OV',
    'SLIP_HARD': 'SLIP_HARD_OV',
    'PRACTICAL_CAPITAL_CEILING': 'PRACTICAL_CAPITAL_CEILING',
    'PRACTICAL_EXPOSURE_ENABLED': 'PRACTICAL_EXPOSURE_ENABLED',
    'GROSS_PREMIUM_CAP': 'GROSS_PREMIUM_CAP',
    'CALL_PREMIUM_CAP': 'CALL_PREMIUM_CAP',
    'PUT_PREMIUM_CAP': 'PUT_PREMIUM_CAP',
    # RXDD — VIX-regime-aware call alloc dampener (Stage-3 sweep, env-only)
    'RXDD_ENABLED': 'RXDD_ENABLED',
    'RXDD_VIX_C': 'RXDD_VIX_C',
    'RXDD_VIX_W': 'RXDD_VIX_W',
    'RXDD_DEPTH': 'RXDD_DEPTH',
    'RXDD_DD_MIN': 'RXDD_DD_MIN',
    # EXR — Early eXposure Ramp: size+VIX-conditional call exposure cap (env-only)
    'EXR_ENABLED': 'EXR_ENABLED',
    'EXR_HOT_CAP': 'EXR_HOT_CAP',
    'EXR_SIZE_LO': 'EXR_SIZE_LO',
    'EXR_SIZE_HI': 'EXR_SIZE_HI',
    'EXR_VIX_FADE': 'EXR_VIX_FADE',
    'EXR_VIX_W': 'EXR_VIX_W',
    'EXR_DD_FADE': 'EXR_DD_FADE',
    # TSL — trailing stop on high-conviction winners (env-only)
    'TSL_ENABLED': 'TSL_ENABLED',
    'TSL_SIGMA': 'TSL_SIGMA',
    'TSL_MIN_SCORE': 'TSL_MIN_SCORE',
    # SVR — semivol_r (skew-bridge) entry filter: downweight euphoric/crash-mode calls (env-only)
    'SVR_ENABLED': 'SVR_ENABLED',
    'SVR_LO_CUT': 'SVR_LO_CUT',
    'SVR_LO_FULL': 'SVR_LO_FULL',
    'SVR_HI_FULL': 'SVR_HI_FULL',
    'SVR_HI_CUT': 'SVR_HI_CUT',
    'SVR_FLOOR': 'SVR_FLOOR',
    # MWDD — McClellan (breadth-momentum) flat-band CALL alloc dampener, DD-gated,
    # VIX-panic-excluded (env-only).
    'MWDD_ENABLED': 'MWDD_ENABLED',
    'MWDD_MCC_C': 'MWDD_MCC_C',
    'MWDD_MCC_W': 'MWDD_MCC_W',
    'MWDD_DEPTH': 'MWDD_DEPTH',
    'MWDD_DD_MIN': 'MWDD_DD_MIN',
    'MWDD_VIX_PANIC': 'MWDD_VIX_PANIC',
    # TVDD — TRIN (volume-flow) neutral-band CALL alloc dampener, DD-gated,
    # VIX-panic-excluded (env-only).
    'TVDD_ENABLED': 'TVDD_ENABLED',
    'TVDD_TRIN_C': 'TVDD_TRIN_C',
    'TVDD_TRIN_W': 'TVDD_TRIN_W',
    'TVDD_DEPTH': 'TVDD_DEPTH',
    'TVDD_DD_MIN': 'TVDD_DD_MIN',
    'TVDD_VIX_PANIC': 'TVDD_VIX_PANIC',
    # DQT — Drawdown Quality Tilt: DD-gated contraction of the LOW-conviction call tiers
    # (low=75-79, overflow=70-74), orthogonal to the regime levers (env-only).
    'DQT_ENABLED': 'DQT_ENABLED',
    'DQT_DD_MIN': 'DQT_DD_MIN',
    'DQT_DD_FULL': 'DQT_DD_FULL',
    'DQT_FLOOR': 'DQT_FLOOR',
    'DQT_OVF_FLOOR': 'DQT_OVF_FLOOR',
    'VXMD_ENABLED': 'VXMD_ENABLED',
    'VXMD_WHIST_LO': 'VXMD_WHIST_LO',
    'VXMD_WHIST_HI': 'VXMD_WHIST_HI',
    'VXMD_DEPTH': 'VXMD_DEPTH',
    'VXMD_DD_MIN': 'VXMD_DD_MIN',
    'VXMD_VIX_PANIC': 'VXMD_VIX_PANIC',
    # BDIV — pre-top breadth-divergence-at-highs CALL alloc dampener (env-only).
    # No DD-gate/VIX-panic by design (SPY-near-highs requirement self-excludes crashes).
    'BDIV_ENABLED': 'BDIV_ENABLED',
    'BDIV_PROX_CUT': 'BDIV_PROX_CUT',
    'BDIV_PROX_FULL': 'BDIV_PROX_FULL',
    'BDIV_GAP_C': 'BDIV_GAP_C',
    'BDIV_GAP_W': 'BDIV_GAP_W',
    'BDIV_DEPTH': 'BDIV_DEPTH',
}

# Per-window 'seeded' line:
#  seeded         55.8%  35.6%  339.2  292.9    +218.2%   +202.0%   69.5%   52.1%   0.0%
SEEDED_RE = re.compile(
    r'^\s*seeded\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s+'
    r'([+\-][\d,.]+)%\s+([+\-][\d,.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%'
)
# WINDOW header line:  WINDOW: 2024  (2024-01-01 -> 2024-12-31)
WINDOW_RE = re.compile(r'^WINDOW:\s+(\S+)\s')
# SUMMARY table rows (final, authoritative MedRet/WorstDD):
#  2024                   +218.2%          +202.0%       69.5%      52.1%       0.0%
SUMMARY_RE = re.compile(
    r'^(\S+)\s+([+\-][\d,.]+)%\s+([+\-][\d,.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s*$'
)
VALID_LABELS = {'2018', '2020', '2020_crash', '2021', '2022', '2023', '2024', '2025',
                'dip', '22-now', '5y', '10y'}


def _f(s: str) -> float:
    return float(s.replace(',', ''))


def run_candidate(params: dict, n_iter: int, windows, workers: int = 0,
                  timeout: int = 7200, tag: str = '') -> dict:
    """Run one MC candidate via subprocess. Returns {label: {metrics}}.

    params: dict of tunable -> value (keys from ENV_MAP). Anything omitted stays
            at strategy_config default.
    windows: list of window labels (subset of VALID_LABELS).
    """
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['PYTHONHASHSEED'] = '0'           # deterministic baseline/candidate compare
    env['MC_NO_DB_PERSIST'] = '1'         # never pollute the MC results table
    env['N_ITER_OVERRIDE'] = str(n_iter)
    env['WINDOWS_OVERRIDE'] = ','.join(windows)
    if workers:
        env['MC_WORKERS'] = str(workers)
    for k, v in params.items():
        if k not in ENV_MAP:
            raise KeyError(f'unknown param {k!r} (no env mapping)')
        env[ENV_MAP[k]] = str(v)

    # Stream child stdout to a per-candidate log file so long sweeps are
    # observable (subprocess.run buffers everything otherwise).
    logdir = os.path.join(os.path.dirname(__file__), '_childlogs')
    os.makedirs(logdir, exist_ok=True)
    safe_tag = re.sub(r'[^A-Za-z0-9._-]', '_', tag or 'cand')
    child_log = os.path.join(logdir, f'{safe_tag}.log')
    with open(child_log, 'w', encoding='utf-8') as lf:
        proc = subprocess.Popen(
            [sys.executable, '-u', MC],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        chunks = []
        for line in proc.stdout:
            chunks.append(line)
            lf.write(line)
            lf.flush()
        proc.wait(timeout=timeout)
    out = ''.join(chunks)
    if proc.returncode != 0:
        sys.stderr.write(f'[driver] candidate {tag} rc={proc.returncode}\n')
        sys.stderr.write(out[-3000:])
        sys.stderr.write(proc.stderr[-3000:])
        raise RuntimeError(f'MC subprocess failed rc={proc.returncode} tag={tag}')

    # First pass: per-window seeded line (CTP/PTP/CTrd/PTrd) keyed by the
    # preceding WINDOW header.
    seeded = {}
    cur = None
    for line in out.splitlines():
        wm = WINDOW_RE.match(line)
        if wm:
            cur = wm.group(1)
            continue
        sm = SEEDED_RE.match(line)
        if sm and cur:
            seeded[cur] = dict(
                call_tp=_f(sm.group(1)), put_tp=_f(sm.group(2)),
                call_trades=_f(sm.group(3)), put_trades=_f(sm.group(4)),
            )
            cur = None
    # Second pass: authoritative SUMMARY table (after 'SUMMARY - Mean Return').
    res = {}
    in_summary = False
    for line in out.splitlines():
        if line.startswith('SUMMARY - Mean Return'):
            in_summary = True
            continue
        if not in_summary:
            continue
        m = SUMMARY_RE.match(line)
        if not m:
            continue
        label = m.group(1)
        if label not in VALID_LABELS:
            continue
        rec = dict(
            mean_ret=_f(m.group(2)), med_ret=_f(m.group(3)),
            worst_dd=_f(m.group(4)), mean_dd=_f(m.group(5)),
            p_collapse=_f(m.group(6)),
        )
        rec.update(seeded.get(label, {}))
        res[label] = rec
    missing = [w for w in windows if w not in res]
    if missing:
        sys.stderr.write(out[-2000:])
        raise RuntimeError(f'parse failed; missing windows {missing} tag={tag}')
    return res


if __name__ == '__main__':
    # smoke
    import json
    r = run_candidate({}, n_iter=40, windows=['2024'], tag='smoke')
    print(json.dumps(r, indent=2))
