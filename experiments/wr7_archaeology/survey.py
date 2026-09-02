"""WR7 alpha archaeology — trace WR7 across all shipped versions to find buckets
where alpha may have been removed.

Run: python experiments/wr7_archaeology/survey.py
"""
from database.models.core import (
    AlgorithmVersion, ScoreAssessmentResult, ScoreAssessmentRun,
)
import collections


def main():
    q = (ScoreAssessmentResult
         .select(
             ScoreAssessmentRun.version.alias('vid'),
             ScoreAssessmentRun.git_commit.alias('commit'),
             ScoreAssessmentRun.run_at.alias('run_at'),
             ScoreAssessmentRun.metric.alias('metric'),
             ScoreAssessmentResult.bucket,
             ScoreAssessmentResult.win_rate_7d,
             ScoreAssessmentResult.win_rate_15d,
             ScoreAssessmentResult.win_rate_30d,
             ScoreAssessmentResult.sample_count,
         )
         .join(ScoreAssessmentRun, on=(ScoreAssessmentResult.run == ScoreAssessmentRun.id))
         .where(ScoreAssessmentRun.lookback_days == 1825)
         .where(ScoreAssessmentRun.dte_strategy == '30')
         .where(ScoreAssessmentRun.metric == 'wr')
         .order_by(ScoreAssessmentRun.version.asc(), ScoreAssessmentRun.run_at.desc())
         .dicts())

    seen = {}
    for r in q:
        key = (r['vid'], r['bucket'])
        if key not in seen:
            seen[key] = r

    by_ver = collections.defaultdict(dict)
    ver_commit = {}
    for (vid, bucket), r in seen.items():
        ver_commit[vid] = r['commit'][:7] if r['commit'] else '?'
        by_ver[vid][bucket] = (r['win_rate_7d'], r['win_rate_15d'],
                               r['win_rate_30d'], r['sample_count'])

    call_buckets = ['95+', '90+', '85+', '80+', '75+', '70+']
    put_buckets = ['<5', '<10', '<15', '<20', '<25', '<30']

    versions_sorted = sorted(by_ver.keys())

    def fmt_wr(v):
        return '{:5.1f}'.format(v) if v is not None else '  -- '

    def fmt_n(n):
        return '{:>6}'.format(n if n is not None else '--')

    print('=' * 130)
    print('CALL-SIDE WR7 (5y, 30DTE, generic K=2sig/M=5sig barriers, W=7d -> K_eff=0.97sig)')
    print('=' * 130)
    hdr = '{:>4} {:>8}'.format('vid', 'commit')
    for b in call_buckets:
        hdr += '  {:>10}'.format(b + ' WR7/N')
    print(hdr)
    print('-' * 130)
    for vid in versions_sorted:
        if vid < 18:
            continue
        row = '{:>4} {:>8}'.format(vid, ver_commit[vid])
        for b in call_buckets:
            if b in by_ver[vid]:
                wr7, _, _, n = by_ver[vid][b]
                row += '  {} {}'.format(fmt_wr(wr7), fmt_n(n))
            else:
                row += '  {:>10}'.format('--')
        print(row)

    print()
    print('=' * 130)
    print('PUT-SIDE WR7 (5y, 30DTE, generic K=1sig/M=2sig barriers, W=7d -> K_eff=0.48sig)')
    print('=' * 130)
    hdr = '{:>4} {:>8}'.format('vid', 'commit')
    for b in put_buckets:
        hdr += '  {:>10}'.format(b + ' WR7/N')
    print(hdr)
    print('-' * 130)
    for vid in versions_sorted:
        if vid < 18:
            continue
        row = '{:>4} {:>8}'.format(vid, ver_commit[vid])
        for b in put_buckets:
            if b in by_ver[vid]:
                wr7, _, _, n = by_ver[vid][b]
                row += '  {} {}'.format(fmt_wr(wr7), fmt_n(n))
            else:
                row += '  {:>10}'.format('--')
        print(row)

    # Peak / current comparison
    print()
    print('=' * 110)
    print('ALPHA-ARCHAEOLOGY: per-bucket peak WR7 vs current (v46), with N gate >= 50')
    print('=' * 110)
    current_vid = max(versions_sorted)
    print('Current version: vid={} commit={}'.format(current_vid, ver_commit[current_vid]))
    print()

    findings = []
    for b in call_buckets + put_buckets:
        # Find peak WR7 across versions with sufficient N
        candidates = []
        for vid in versions_sorted:
            if vid < 18:
                continue
            if b not in by_ver[vid]:
                continue
            wr7, _, _, n = by_ver[vid][b]
            if wr7 is None or n is None or n < 50:
                continue
            candidates.append((wr7, vid, n))
        if not candidates:
            continue
        peak_wr7, peak_vid, peak_n = max(candidates, key=lambda x: x[0])
        cur = by_ver.get(current_vid, {}).get(b)
        if cur is None:
            continue
        cur_wr7, _, _, cur_n = cur
        if cur_wr7 is None:
            continue
        delta = cur_wr7 - peak_wr7
        findings.append({
            'bucket': b,
            'peak_wr7': peak_wr7,
            'peak_vid': peak_vid,
            'peak_commit': ver_commit[peak_vid],
            'peak_n': peak_n,
            'cur_wr7': cur_wr7,
            'cur_n': cur_n if cur_n is not None else 0,
            'delta': delta,
        })

    findings.sort(key=lambda x: x['delta'])

    print('{:>6} {:>9} {:>7} {:>8} {:>9} {:>7} {:>9} {:>6}'.format(
        'bucket', 'peak_WR7', 'peak_v', 'commit', 'cur_WR7', 'cur_v',
        'delta', 'cur_N'))
    print('-' * 110)
    for f in findings:
        print('{:>6} {:>9.1f} {:>7} {:>8} {:>9.1f} {:>7} {:>+9.2f} {:>6}'.format(
            f['bucket'], f['peak_wr7'], f['peak_vid'], f['peak_commit'],
            f['cur_wr7'], current_vid, f['delta'], f['cur_n']))


if __name__ == '__main__':
    main()
