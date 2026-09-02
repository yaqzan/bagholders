"""Fast H6 N-floor check.

Counts qualifying scores per tier (calls 70+, puts <=25) at the active
algorithm version, compares to the provisional floor table, prints proximity.

This is a soft-gradient check, not a hard barrier:

  SAFE     : offered/yr  >=  baseline * 0.95   (within 5% of baseline; healthy)
  MARGINAL : offered/yr  in  [floor, baseline*0.95)   (in the buffer zone; flag in ship summary)
  REVIEW   : offered/yr  <   floor             (below floor; compensation curve required)

Floor = baseline * (1 - buffer), where buffer is per-tier-class:
  binding tiers (95+, 85-94, 80-84, 75-79, p<=15) : 15% buffer
  mild surplus (p16-20)                            : 30% buffer
  heavy surplus (p21-25)                           : 60% buffer

Usage (no args; reads active version from DB):
  PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py

Run any time as part of a ship; cheap (~5 sec, just a DB scan).
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from database.models.core import AlgorithmVersion, Score  # noqa: E402
import strategy_config as _scfg  # noqa: E402

_cfg = _scfg.STRATEGY_30DTE
CT_PROMOTE       = _cfg.CT_PROMOTE
CT_CALL_TREND_MAX = _cfg.CT_CALL_TREND_MAX  # call CT: TREND <= this
CT_PUT_TREND_MIN  = _cfg.CT_PUT_TREND_MIN   # put  CT: TREND >= this

# Baselines measured by THIS script at v46 (active 2026-05-08), so future
# runs of this script are method-consistent. Floors are derived as
# baseline * (1 - buffer) per-tier — binding tiers 15%, mild-surplus 30%,
# heavy-surplus 60%. See FINDINGS.md for the binding-vs-surplus rationale.
#
# NOTE: this script counts Score-table rows directly (post-CT-promote) and is
# faster (~5 sec) than the full replay. The replay's `outcomes_by_date` count
# is ~3% higher on most tiers and ~25% higher on 95+ (extra CT-promote
# capture by `compute_outcome` that this Score-only approximation misses).
# Both methods are internally consistent — just compare apples-to-apples.
# Authoritative re-derivation lives in run.py + reaggregate.py.
BASELINE_OFFERED_PER_YEAR = {
    '95+':    31.2,
    '85-94':  98.4,
    '80-84':  193.7,
    '75-79':  513.8,
    'p<=15':  230.7,
    'p16-20': 569.5,
    'p21-25': 1353.1,
}

FLOOR_OFFERED_PER_YEAR = {
    '95+':    int(BASELINE_OFFERED_PER_YEAR['95+']    * 0.85),  # binding 15%
    '85-94':  int(BASELINE_OFFERED_PER_YEAR['85-94']  * 0.85),
    '80-84':  int(BASELINE_OFFERED_PER_YEAR['80-84']  * 0.85),
    '75-79':  int(BASELINE_OFFERED_PER_YEAR['75-79']  * 0.85),
    'p<=15':  int(BASELINE_OFFERED_PER_YEAR['p<=15']  * 0.85),
    'p16-20': int(BASELINE_OFFERED_PER_YEAR['p16-20'] * 0.70),  # mild surplus 30%
    'p21-25': int(BASELINE_OFFERED_PER_YEAR['p21-25'] * 0.40),  # heavy surplus 60%
    # 70-74 disabled (alloc=0); not gated.
}

START_DATE = date(2020, 1, 1)
SAFE_BASELINE_PCT = 95.0  # >= this % of baseline = SAFE
TIERS_ORDER = ['95+', '85-94', '80-84', '75-79', 'p<=15', 'p16-20', 'p21-25']


def _tier_for(score: float, trend: float | None) -> str | None:
    """Map (score, trend) to cascade tier, mirroring backtest_cascade.compute_outcome
    + CT_PROMOTE logic. CT-promoted 70-74 calls (TREND <= 20) display as '95+';
    CT-promoted p21-25 puts (TREND >= 80) display as 'p<=15'.
    """
    if score >= 95: return '95+'
    if score >= 85: return '85-94'
    if score >= 80: return '80-84'
    if score >= 75: return '75-79'
    if score >= 70:
        # 70-74: CT-promoted to '95+' if call AND TREND <= CT_CALL_TREND_MAX,
        # else overflow tier (alloc=0, not gated).
        if CT_PROMOTE and trend is not None and trend <= CT_CALL_TREND_MAX:
            return '95+'
        return None
    if score <= 15: return 'p<=15'
    if score <= 20: return 'p16-20'
    if score <= 25:
        # p21-25: CT-promoted to 'p<=15' if put AND TREND >= CT_PUT_TREND_MIN.
        if CT_PROMOTE and trend is not None and trend >= CT_PUT_TREND_MIN:
            return 'p<=15'
        return 'p21-25'
    return None


def _classify(offered_per_yr: float, floor: float, baseline: float) -> str:
    if offered_per_yr < floor:
        return 'REVIEW'
    if offered_per_yr < baseline * (SAFE_BASELINE_PCT / 100.0):
        return 'MARGINAL'
    return 'SAFE'


def main():
    av = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: id={av.id}")
    print(f"Window:         {START_DATE} -> today (open-ended; per-year normalized)")
    print()

    # Single Score scan — qualifying calls 70+ and qualifying puts <=25,
    # active version only, since 2020-01-01.
    rows = (Score
            .select(Score.date, Score.overall, Score.trend)
            .where((Score.version == av)
                   & (Score.date >= START_DATE)
                   & ((Score.overall >= 70) | (Score.overall <= 25)))
            .tuples())

    counts: dict = defaultdict(int)
    by_year: dict = defaultdict(lambda: defaultdict(int))
    min_date = None
    max_date = None
    for d, ov, tr in rows:
        if ov is None:
            continue
        t = _tier_for(float(ov), float(tr) if tr is not None else None)
        if t is None:
            continue
        counts[t] += 1
        by_year[d.year][t] += 1
        if min_date is None or d < min_date: min_date = d
        if max_date is None or d > max_date: max_date = d

    if not counts:
        print("No qualifying scores found at active version.")
        return

    span_days = (max_date - min_date).days + 1
    span_years = span_days / 365.25
    print(f"Score range: {min_date} -> {max_date}  ({span_years:.2f} years)")
    print()

    # Header
    cols = ['tier', 'offered_total', 'offered/yr', 'floor', 'baseline_v46',
            'vs_floor%', 'vs_baseline%', 'status']
    print(f"{cols[0]:>7}  {cols[1]:>13}  {cols[2]:>10}  {cols[3]:>7}  "
          f"{cols[4]:>12}  {cols[5]:>9}  {cols[6]:>11}  {cols[7]:>9}")

    any_review = False
    any_marginal = False
    for tier in TIERS_ORDER:
        n_total = counts.get(tier, 0)
        n_per_yr = n_total / span_years if span_years > 0 else 0.0
        floor = FLOOR_OFFERED_PER_YEAR[tier]
        baseline = BASELINE_OFFERED_PER_YEAR[tier]
        vs_floor_pct = 100.0 * (n_per_yr - floor) / floor if floor > 0 else 0.0
        vs_baseline_pct = 100.0 * (n_per_yr - baseline) / baseline if baseline > 0 else 0.0
        status = _classify(n_per_yr, floor, baseline)
        if status == 'REVIEW':
            any_review = True
        elif status == 'MARGINAL':
            any_marginal = True

        print(f"{tier:>7}  {n_total:>13,}  {n_per_yr:>10.1f}  "
              f"{floor:>7.0f}  {baseline:>12.0f}  "
              f"{vs_floor_pct:>+8.1f}%  {vs_baseline_pct:>+10.1f}%  {status:>9}")

    print()
    if any_review:
        print("STATUS: REVIEW REQUIRED")
        print("  At least one tier is below its provisional H6 floor.")
        print("  Run the compensation analysis: alloc-bump or WR uplift must")
        print("  show (offered_after * fill_rate * alloc * WR_after) >= baseline")
        print("  cash-deployed-days/year. See assessment-backtest.md H6.")
        print()
        print("  Soft gate, NOT a veto. Document the trade-off in known-issues.md.")
    elif any_marginal:
        print("STATUS: MARGINAL")
        print("  At least one tier is in the buffer zone between floor and baseline.")
        print("  No blocker; flag in the ship summary so cumulative drift across")
        print("  successive ships doesn't silently push tiers below floor.")
    else:
        print("STATUS: SAFE")
        print("  All tiers within 5% of baseline. No throughput concern.")


if __name__ == '__main__':
    main()
