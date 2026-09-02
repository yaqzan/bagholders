"""
Cross-version score-quality comparison
=======================================
Pulls 5y / 30 DTE ScoreAssessmentResult rows per algorithm version and
tabulates per-bucket WR15 / WR30 / Ret30 / Cap30 / N. Used to track
how each shipped scoring change has moved per-trade signal quality
relative to predecessors.

Output: console table + experiments/version_comparison.json.
"""
import os
import sys
import json
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assess_scores import ScoreAssessmentRun, ScoreAssessmentResult
from database.models.core import AlgorithmVersion


# Versions to compare (id, label, short_commit, why_it_matters)
VERSIONS = [
    (21, 'v21', 'aba4f5d', 'ext-focal put dampener (last pre-OP-aware portfolio shipping)'),
    (27, 'v27', 'ad02704', 'weekly-confirmation floor lift on extreme puts'),
    (28, 'v28', 'e3c8678', 'earnings meta-score boost (WR-calibrated)'),
    (29, 'v29', '8473cba', 'V6 earnings volume gradient (re-shipped)'),
    (30, 'v30', '9a9da33', 'AMC-aware earnings effective_date (this commit)'),
]

# Buckets to display (cumulative call buckets descending; cumulative put buckets ascending)
CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']
PUT_BUCKETS  = ['<5', '<10', '<15', '<20', '<25', '<30']


def load_5y_run(version_id, metric='wr'):
    """Return latest 5y / 30 DTE ScoreAssessmentRun for the given version + metric.

    metric='wr' = canonical barrier-touch win rate (K·σ target before M·σ stop).
    metric='tp' = option-TP-rate (whether option premium hit +30%) — different
                  semantic, much harder to hit because of theta/IV. Only present
                  on v29+ runs.
    """
    return (
        ScoreAssessmentRun
        .select()
        .where(
            ScoreAssessmentRun.version == version_id,
            ScoreAssessmentRun.lookback_days == 1825,
            ScoreAssessmentRun.dte_strategy == '30',
            ScoreAssessmentRun.metric == metric,
        )
        .order_by(ScoreAssessmentRun.id.desc())
        .first()
    )


def load_buckets(run_id):
    """Return {bucket_label: dict_of_metrics} for a given run."""
    out = {}
    for r in ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == run_id):
        out[r.bucket] = {
            'N': r.sample_count or 0,
            'WR15': r.win_rate_15d,
            'WR30': r.win_rate_30d,
            'Ret15': r.avg_return_15d,
            'Ret30': r.avg_return_30d,
            'Cap30': r.capture_ratio_30d,
        }
    return out


def fmt(v, w=6, p=1, suffix=''):
    if v is None:
        return f"{'—':>{w}}"
    return f"{v:>{w-len(suffix)}.{p}f}{suffix}"


def print_section(label, buckets, data_by_version):
    print(f"\n{label}")
    header = f"{'bucket':<7} " + " | ".join(
        f"{vname:>22}" for _, vname, _, _ in VERSIONS
    )
    print(header)
    print('-' * len(header))
    for b in buckets:
        row = f"{b:<7} "
        cells = []
        for vid, vname, _, _ in VERSIONS:
            d = data_by_version.get(vid, {}).get(b)
            if d:
                cell = f"WR15={fmt(d['WR15'],5,1,'%')} WR30={fmt(d['WR30'],5,1,'%')} N={d['N']:>5}"
            else:
                cell = f"{'(no data)':>22}"
            cells.append(cell)
        row += " | ".join(cells)
        print(row)


def main():
    data_by_version = {}
    print(f"{'='*120}")
    print(f"CROSS-VERSION 5y / 30 DTE assessment comparison")
    print(f"{'='*120}")
    for vid, vname, cmt, why in VERSIONS:
        run = load_5y_run(vid)
        if run is None:
            print(f"  {vname} ({cmt}): NO 5y/30dte run")
            data_by_version[vid] = {}
            continue
        bk = load_buckets(run.id)
        data_by_version[vid] = bk
        print(f"  {vname} ({cmt}) run id={run.id} run_at={run.run_at} N_peaks={run.total_peaks}  — {why}")

    print_section("CALL TIERS — WR15 / WR30 / N", CALL_BUCKETS, data_by_version)
    print_section("PUT TIERS — WR15 / WR30 / N", PUT_BUCKETS, data_by_version)

    # Diff vs latest baseline (v29) on key metrics
    print(f"\n{'='*120}")
    print(f"DELTA vs v29 (8473cba) — pp on WR15 / WR30, raw on N change pct")
    print(f"{'='*120}")
    base = data_by_version.get(29, {})
    for vid, vname, cmt, _ in VERSIONS:
        if vid == 29:
            continue
        cur = data_by_version.get(vid, {})
        if not cur or not base:
            continue
        print(f"\n{vname} vs v29:")
        print(f"  {'bucket':<7}  {'ΔWR15':>8}  {'ΔWR30':>8}  {'ΔN%':>8}")
        for b in CALL_BUCKETS + PUT_BUCKETS:
            d_cur = cur.get(b)
            d_base = base.get(b)
            if not d_cur or not d_base or d_base['WR15'] is None or d_cur['WR15'] is None:
                continue
            d_wr15 = d_cur['WR15'] - d_base['WR15']
            d_wr30 = (d_cur['WR30'] or 0) - (d_base['WR30'] or 0)
            d_n = ((d_cur['N'] - d_base['N']) / d_base['N'] * 100) if d_base['N'] else 0
            print(f"  {b:<7}  {d_wr15:>+7.1f}  {d_wr30:>+7.1f}  {d_n:>+7.1f}%")

    # Save JSON
    with open('experiments/version_comparison.json', 'w') as f:
        json.dump({str(vid): bk for vid, bk in data_by_version.items()},
                  f, indent=2, default=str)
    print(f"\nSaved -> experiments/version_comparison.json")


if __name__ == '__main__':
    main()
