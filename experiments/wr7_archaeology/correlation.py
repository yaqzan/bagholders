"""WR7 vs option-TP% cross-version correlation.

Does WR7 (current 30d-anchor) track option TP% (option-aligned barriers
at W=15d for 30 DTE) across the shipped algorithm versions?

If yes (rho > 0.7) -> current WR7 anchor is a good proxy for option
outcomes; recalibration to 15d-anchor gains nothing.

If no (rho < 0.4) -> structural mismatch; consider 15d-anchor or
custom W=7 option-aligned barrier walk.

Run: python experiments/wr7_archaeology/correlation.py
"""
import collections
from database.models.core import (
    ScoreAssessmentResult, ScoreAssessmentRun,
)


def main():
    q = (ScoreAssessmentResult
         .select(
             ScoreAssessmentRun.version.alias('vid'),
             ScoreAssessmentRun.git_commit.alias('commit'),
             ScoreAssessmentRun.metric.alias('metric'),
             ScoreAssessmentResult.bucket,
             ScoreAssessmentResult.win_rate_7d,
             ScoreAssessmentResult.win_rate_15d,
             ScoreAssessmentResult.win_rate_30d,
             ScoreAssessmentResult.sample_count,
             ScoreAssessmentRun.run_at.alias('run_at'),
         )
         .join(ScoreAssessmentRun, on=(ScoreAssessmentResult.run == ScoreAssessmentRun.id))
         .where(ScoreAssessmentRun.lookback_days == 1825)
         .where(ScoreAssessmentRun.dte_strategy == '30')
         .order_by(ScoreAssessmentRun.version.asc(), ScoreAssessmentRun.run_at.desc())
         .dicts())

    # Per (vid, metric, bucket) keep most recent
    seen = {}
    for r in q:
        key = (r['vid'], r['metric'], r['bucket'])
        if key not in seen:
            seen[key] = r

    # Pivot to {bucket: [(vid, wr7, tp15)]}
    by_bucket = collections.defaultdict(list)
    versions = set()
    for (vid, metric, bucket), r in seen.items():
        versions.add(vid)

    versions_sorted = sorted(versions)

    # Build (wr7, tp15) per (bucket, vid) where both exist
    for vid in versions_sorted:
        for bucket in ['95+', '90+', '85+', '80+', '75+', '70+',
                       '<5', '<10', '<15', '<20', '<25', '<30']:
            wr_row = seen.get((vid, 'wr', bucket))
            tp_row = seen.get((vid, 'tp', bucket))
            if wr_row is None or tp_row is None:
                continue
            wr7 = wr_row['win_rate_7d']
            tp15 = tp_row['win_rate_15d']
            wr_n = wr_row['sample_count']
            tp_n = tp_row['sample_count']
            if wr7 is None or tp15 is None:
                continue
            if (wr_n or 0) < 50 or (tp_n or 0) < 50:
                continue
            by_bucket[bucket].append((vid, wr7, tp15, wr_n))

    # Correlations
    def pearson(xs, ys):
        n = len(xs)
        if n < 3:
            return None, n
        mx = sum(xs) / n
        my = sum(ys) / n
        sx = sum((x - mx) ** 2 for x in xs)
        sy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sx == 0 or sy == 0:
            return None, n
        return sxy / (sx ** 0.5 * sy ** 0.5), n

    print('=' * 100)
    print('LEVEL CORRELATION: WR7 (30d-anchor) vs option TP% (W=15d, 30 DTE) across versions')
    print('=' * 100)
    print('Pearson rho, by bucket. n = number of versions where both metrics exist with N>=50')
    print()
    print('{:>6} {:>6} {:>10} {:>10} {:>10} {:>10}'.format(
        'bucket', 'n', 'rho', 'mean_WR7', 'mean_TP15', 'WR7-TP15'))
    print('-' * 100)

    all_buckets = ['95+', '90+', '85+', '80+', '75+', '70+',
                   '<5', '<10', '<15', '<20', '<25', '<30']
    for bucket in all_buckets:
        rows = by_bucket.get(bucket, [])
        if len(rows) < 3:
            print('{:>6} {:>6} {:>10}'.format(bucket, len(rows), 'too few'))
            continue
        wr7s = [r[1] for r in rows]
        tp15s = [r[2] for r in rows]
        rho, n = pearson(wr7s, tp15s)
        mw = sum(wr7s) / n
        mt = sum(tp15s) / n
        rho_str = '{:>10.3f}'.format(rho) if rho is not None else '{:>10}'.format('--')
        print('{:>6} {:>6} {} {:>10.2f} {:>10.2f} {:>+10.2f}'.format(
            bucket, n, rho_str, mw, mt, mw - mt))

    # Delta correlation: cohort lift consistency
    print()
    print('=' * 100)
    print('DELTA CORRELATION: dWR7 vs dTP15 between consecutive versions, per bucket')
    print('=' * 100)
    print('A high rho here means: when one version improves WR7 on a bucket, it also')
    print('improves option TP% on the same bucket. This is the signal that matters for')
    print('Stage 1 calibration -- does WR7-lift translate to TP%-lift?')
    print()
    print('{:>6} {:>6} {:>10} {:>10}'.format(
        'bucket', 'n_pairs', 'delta_rho', 'note'))
    print('-' * 100)
    for bucket in all_buckets:
        rows = by_bucket.get(bucket, [])
        if len(rows) < 4:
            print('{:>6} {:>6} {:>10}'.format(bucket, max(0, len(rows) - 1), 'too few'))
            continue
        rows = sorted(rows, key=lambda r: r[0])
        d_wr7 = [rows[i+1][1] - rows[i][1] for i in range(len(rows) - 1)]
        d_tp15 = [rows[i+1][2] - rows[i][2] for i in range(len(rows) - 1)]
        rho, n = pearson(d_wr7, d_tp15)
        rho_str = '{:>10.3f}'.format(rho) if rho is not None else '{:>10}'.format('--')
        note = ''
        if rho is not None:
            if rho > 0.7:
                note = 'STRONG TRACKING'
            elif rho > 0.4:
                note = 'moderate'
            elif rho > 0.0:
                note = 'weak'
            else:
                note = 'NEGATIVE - structural mismatch'
        print('{:>6} {:>6} {} {:>30}'.format(bucket, n, rho_str, note))

    # Per-version side-by-side for the most-active middle tiers
    print()
    print('=' * 100)
    print('SIDE-BY-SIDE: WR7 vs TP15 trajectory on call tiers 75+ / 80+ / 85+')
    print('=' * 100)
    print('Versions with both metrics, sorted by vid')
    print()
    rows_75 = sorted(by_bucket.get('75+', []))
    rows_80 = sorted(by_bucket.get('80+', []))
    rows_85 = sorted(by_bucket.get('85+', []))
    by_vid = collections.defaultdict(dict)
    for r in rows_75:
        by_vid[r[0]]['75'] = (r[1], r[2])
    for r in rows_80:
        by_vid[r[0]]['80'] = (r[1], r[2])
    for r in rows_85:
        by_vid[r[0]]['85'] = (r[1], r[2])

    print('{:>4} | {:>15} | {:>15} | {:>15}'.format('vid', '75+ WR7/TP15', '80+ WR7/TP15', '85+ WR7/TP15'))
    print('-' * 100)
    for vid in sorted(by_vid.keys()):
        cells = []
        for k in ['75', '80', '85']:
            t = by_vid[vid].get(k)
            if t:
                cells.append('{:>5.1f} / {:>5.1f}'.format(t[0], t[1]))
            else:
                cells.append('{:>13}'.format('--'))
        print('{:>4} | {:>15} | {:>15} | {:>15}'.format(vid, *cells))


if __name__ == '__main__':
    main()
