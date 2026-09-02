"""Build per-tier priority tables from assessment data.

Pulls 5y assessment WR15/WR30 + IC from the local DB (ScoreAssessmentMeta)
or the running API. Computes discrete-band WR per cumulative bucket, then
maps to MC tier keys (ultra/top/mid/low/put_top/put_mid/put_low) used by
TIER_ALLOC and PUT_TIER_ALLOC.

Discrete band derivation: cumulative WR_i covers all signals with score >= t_i
(calls) or score <= t_i (puts). For two adjacent thresholds (t_a < t_b for calls):
    discrete WR(t_a..t_b) = (WR_b * N_b - WR_a * N_a) / (N_b - N_a)
where N_a refers to the inner cumulative bucket and N_b to the outer.
"""
from __future__ import annotations

import math
import os
import sys
import json
from typing import Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ----------------------------------------------------------------------------
# Hardcoded fallback table from active v28 5y assessment (pulled 2026-04-28)
# ----------------------------------------------------------------------------
# Cumulative buckets {bucket_label: (WR15, WR30, N)}
# Source: GET /api/assessment, active version 28, 5y (lookback 1825d).
# ----------------------------------------------------------------------------
_V28_5Y_FALLBACK = {
    # Calls (cumulative >=t)
    '95+': dict(wr15=88.9, wr30=91.6, n=154),
    '90+': dict(wr15=79.7, wr30=82.9, n=660),
    '85+': dict(wr15=77.9, wr30=80.6, n=2243),
    '80+': dict(wr15=74.3, wr30=75.8, n=7216),
    '75+': dict(wr15=72.9, wr30=74.3, n=19790),
    '70+': dict(wr15=70.8, wr30=72.3, n=58432),
    # Puts (cumulative <=t)
    '<30': dict(wr15=66.9, wr30=66.6, n=141590),
    '<25': dict(wr15=73.9, wr30=74.0, n=22350),
    '<20': dict(wr15=73.8, wr30=74.1, n=12644),
    '<15': dict(wr15=74.5, wr30=75.1, n=6818),
    '<10': dict(wr15=76.0, wr30=76.2, n=3140),
    '<5':  dict(wr15=79.0, wr30=80.3, n=1186),
}

# Discrete IC15 from band_ics (per discrete band)
_V28_5Y_BAND_IC = {
    '95-100': 0.0414,   # 60d (15d=-0.05 small N — use 60d as more stable proxy)
    '90-94':  0.1431,
    '85-89':  0.0506,
    '80-84':  0.0359,
    '75-79':  -0.0084,
    '70-74':  0.0063,
    # Put bands — would come from band_ics if available; use 0.0 placeholder
    '<5':    0.05,
    '5-9':   0.05,
    '10-14': 0.04,
    '15-19': 0.04,
    '20-24': 0.04,
}


def _discrete_call(cum_outer, cum_inner=None):
    """Discrete WR15 for a call band [t_outer, t_inner). cum_inner=None means open-ended top."""
    if cum_inner is None:
        return cum_outer['wr15'], cum_outer['n']
    n_band = cum_outer['n'] - cum_inner['n']
    if n_band <= 0:
        return cum_inner['wr15'], cum_inner['n']
    wr_band = (cum_outer['wr15'] * cum_outer['n'] - cum_inner['wr15'] * cum_inner['n']) / n_band
    return wr_band, n_band


def _discrete_put(cum_outer, cum_inner=None):
    """Discrete WR15 for a put band. cum_inner=None means open-ended bottom (e.g. <5 alone)."""
    if cum_inner is None:
        return cum_outer['wr15'], cum_outer['n']
    n_band = cum_outer['n'] - cum_inner['n']
    if n_band <= 0:
        return cum_inner['wr15'], cum_inner['n']
    wr_band = (cum_outer['wr15'] * cum_outer['n'] - cum_inner['wr15'] * cum_inner['n']) / n_band
    return wr_band, n_band


def compute_tier_stats(cum: Optional[dict] = None) -> Dict[str, dict]:
    """Return {tier_key: {'wr15':..., 'wr30':..., 'n':..., 'ic':...}} for all MC tiers.

    Tier ranges:
      Calls — TIER_ALLOC keys:
          ultra:    score >= 95          (band 95+)
          top:      85 <= score < 95     (bands 85-89, 90-94)
          mid:      80 <= score < 85     (band 80-84)
          low:      75 <= score < 80     (band 75-79)
          overflow: 70 <= score < 75     (band 70-74) — currently disabled (alloc=0)
      Puts — PUT_TIER_ALLOC keys:
          put_top:  score <= 15          (cum <15)
          put_mid:  16 <= score <= 20    (band 15-19, derived <20 minus <15)
          put_low:  21 <= score <= 25    (band 20-24, derived <25 minus <20)
    """
    cum = cum or _V28_5Y_FALLBACK
    out = {}

    # Calls
    # ultra = 95+ alone
    wr, n = _discrete_call(cum['95+'])
    out['ultra'] = {'wr15': wr, 'wr30': cum['95+']['wr30'], 'n': n, 'ic': _V28_5Y_BAND_IC.get('95-100', 0.0)}
    # 90-94 = 90+ minus 95+
    wr_90_94, n_90_94 = _discrete_call(cum['90+'], cum['95+'])
    # 85-89 = 85+ minus 90+
    wr_85_89, n_85_89 = _discrete_call(cum['85+'], cum['90+'])
    # top = 85-94 (combination)
    n_top = n_90_94 + n_85_89
    wr_top = (wr_90_94 * n_90_94 + wr_85_89 * n_85_89) / max(n_top, 1)
    # weighted IC
    ic_top = (_V28_5Y_BAND_IC.get('90-94', 0) * n_90_94 +
              _V28_5Y_BAND_IC.get('85-89', 0) * n_85_89) / max(n_top, 1)
    out['top'] = {'wr15': wr_top, 'wr30': 0.0, 'n': n_top, 'ic': ic_top}
    # mid = 80-84 = 80+ minus 85+
    wr, n = _discrete_call(cum['80+'], cum['85+'])
    out['mid'] = {'wr15': wr, 'wr30': 0.0, 'n': n, 'ic': _V28_5Y_BAND_IC.get('80-84', 0)}
    # low = 75-79 = 75+ minus 80+
    wr, n = _discrete_call(cum['75+'], cum['80+'])
    out['low'] = {'wr15': wr, 'wr30': 0.0, 'n': n, 'ic': _V28_5Y_BAND_IC.get('75-79', 0)}
    # overflow = 70-74 = 70+ minus 75+
    wr, n = _discrete_call(cum['70+'], cum['75+'])
    out['overflow'] = {'wr15': wr, 'wr30': 0.0, 'n': n, 'ic': _V28_5Y_BAND_IC.get('70-74', 0)}

    # Puts
    # put_top = <15 (cumulative)
    wr, n = cum['<15']['wr15'], cum['<15']['n']
    out['put_top'] = {'wr15': wr, 'wr30': cum['<15']['wr30'], 'n': n,
                       'ic': _V28_5Y_BAND_IC.get('<5', 0.05)}
    # put_mid = 16-20 = <20 minus <15
    wr, n = _discrete_put(cum['<20'], cum['<15'])
    out['put_mid'] = {'wr15': wr, 'wr30': 0.0, 'n': n,
                       'ic': _V28_5Y_BAND_IC.get('15-19', 0.04)}
    # put_low = 21-25 = <25 minus <20
    wr, n = _discrete_put(cum['<25'], cum['<20'])
    out['put_low'] = {'wr15': wr, 'wr30': 0.0, 'n': n,
                       'ic': _V28_5Y_BAND_IC.get('20-24', 0.04)}

    return out


def build_priority_table(formula: str = 'WR15',
                         call_bias: float = 1.0,
                         fallback: float = 65.0,
                         tier_stats: Optional[Dict] = None,
                         ct_call_pri: Optional[float] = None,
                         ct_put_pri:  Optional[float] = None) -> Dict[str, float]:
    """Build {tier_key: priority_value} from tier_stats.

    formula:
      'WR15'          - raw WR15
      'WR30'          - raw WR30 (less data on most tiers; falls back to WR15 if 0)
      'WR15xIC'       - WR15 * (1 + 5*max(IC, 0))  [IC capped at 0 for negative]
      'WR15xLogN'     - WR15 + 2*log10(N)/log10(60000) * 10  [tiny-N dampened]
      'EV'            - WR15*0.30 - (1-WR15/100)*0.30  (rough EV at TP=30/SL=30)

    call_bias:  multiplier applied to call-side tiers ('ultra','top','mid','low','overflow').
                <1.0 deprioritizes calls vs puts; >1.0 prioritizes calls.
    fallback:   priority for unknown tiers (used by overflow/CT defaults).
    ct_call_pri/ct_put_pri:  priority for CT-tagged signals; defaults to highest in table + small bonus.
    """
    ts = tier_stats if tier_stats is not None else compute_tier_stats()

    def _val(stats: dict) -> float:
        wr15 = stats.get('wr15', 0.0) or 0.0
        wr30 = stats.get('wr30', 0.0) or 0.0
        ic   = stats.get('ic',   0.0) or 0.0
        n    = max(int(stats.get('n', 1) or 1), 1)
        if formula == 'WR15':
            return wr15
        if formula == 'WR30':
            return wr30 if wr30 > 0 else wr15
        if formula == 'WR15xIC':
            return wr15 * (1.0 + 5.0 * max(ic, 0.0))
        if formula == 'WR15xLogN':
            return wr15 + 10.0 * math.log10(n) / math.log10(60000)
        if formula == 'EV':
            # crude — uses TP=30, SL=30 placeholder; all signs symmetric so it just rescales WR
            p = wr15 / 100.0
            return (p * 30.0 - (1 - p) * 30.0)  # = (2p-1)*30
        raise ValueError(f'unknown formula: {formula}')

    table: Dict[str, float] = {}
    call_keys = ('ultra', 'top', 'mid', 'low', 'overflow')
    for k in call_keys:
        if k in ts:
            table[k] = _val(ts[k]) * call_bias
    for k in ('put_top', 'put_mid', 'put_low'):
        if k in ts:
            table[k] = _val(ts[k])

    # CT priorities — by default rank above the highest in-table tier
    if table:
        max_pri = max(table.values())
    else:
        max_pri = fallback
    if ct_call_pri is None:
        ct_call_pri = max_pri + 5.0
    if ct_put_pri is None:
        ct_put_pri = max_pri + 5.0
    table['ct_call'] = ct_call_pri
    table['ct_put']  = ct_put_pri

    return table


def print_table(table: Dict[str, float], header: str = '') -> None:
    if header:
        print(f'  --- {header} ---')
    for k, v in sorted(table.items(), key=lambda kv: -kv[1]):
        print(f'    {k:>10s}: {v:7.2f}')


if __name__ == '__main__':
    ts = compute_tier_stats()
    print('Per-tier discrete WR / N / IC (v28 5y):')
    for k in ('ultra', 'top', 'mid', 'low', 'overflow', 'put_top', 'put_mid', 'put_low'):
        s = ts[k]
        print(f'  {k:>10s}: WR15={s["wr15"]:5.1f}  WR30={s["wr30"]:5.1f}  N={s["n"]:>6d}  IC={s["ic"]:+.4f}')

    print()
    for formula in ('WR15', 'WR30', 'WR15xIC', 'WR15xLogN', 'EV'):
        for cb in (1.00, 1.03, 0.97):
            tbl = build_priority_table(formula=formula, call_bias=cb)
            print_table(tbl, header=f'formula={formula}  call_bias={cb}')
            print()
