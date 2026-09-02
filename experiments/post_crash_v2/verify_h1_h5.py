"""H1-H5 ship gate verification: v37 (PCD) vs v36 baseline at 5y / 30 DTE."""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from database.models.core import AlgorithmVersion, ScoreAssessmentRun, ScoreAssessmentResult


def main():
    av_v37 = AlgorithmVersion.get_active_scores_version()
    av_v36 = AlgorithmVersion.get_or_none(AlgorithmVersion.git_commit == 'd5ef1f5')
    print(f'Active: id={av_v37.id} commit={av_v37.git_commit}')
    print(f'Prior:  id={av_v36.id} commit={av_v36.git_commit}')

    def get_run(av_id, lookback, dte):
        runs = list(ScoreAssessmentRun.select().where(
            (ScoreAssessmentRun.version == av_id) &
            (ScoreAssessmentRun.lookback_days == lookback) &
            (ScoreAssessmentRun.dte_strategy == dte)
        ))
        return sorted(runs, key=lambda r: r.id, reverse=True)[0] if runs else None

    print('\n' + '=' * 110)
    print('H1-H5 PER-TRADE GATE VERIFICATION — 30 DTE / barrier-touch (PCD v37 vs v36 baseline)')
    print('=' * 110)

    call_buckets = ['95+', '90+', '85+', '80+', '75+', '70+']
    put_buckets = ['<25', '<20', '<15', '<10', '<5']
    all_buckets = call_buckets + put_buckets

    for lookback, label in [(365, '1y'), (1095, '3y'), (1825, '5y')]:
        r37 = get_run(av_v37.id, lookback, '30')
        r36 = get_run(av_v36.id, lookback, '30')
        if not r37 or not r36:
            print(f'\n[{label}] MISSING RUN — v37={r37}, v36={r36}')
            continue

        print(f'\n--- WINDOW: {label} ({lookback}d) ---')
        hdr = f'{"bucket":<7} {"v36 N":>7} {"v36 WR15":>10} {"v37 N":>7} {"v37 WR15":>10} {"dWR15":>9} {"dN%":>8}'
        print(hdr)
        print('-' * 110)

        for bucket in all_buckets:
            res36 = ScoreAssessmentResult.get_or_none(
                (ScoreAssessmentResult.run == r36.id) & (ScoreAssessmentResult.bucket == bucket))
            res37 = ScoreAssessmentResult.get_or_none(
                (ScoreAssessmentResult.run == r37.id) & (ScoreAssessmentResult.bucket == bucket))
            if not res36 or not res37:
                print(f'{bucket:<7} (missing)')
                continue
            n36 = res36.sample_count or 0
            n37 = res37.sample_count or 0
            wr36 = float(res36.win_rate_15d) if res36.win_rate_15d is not None else None
            wr37 = float(res37.win_rate_15d) if res37.win_rate_15d is not None else None
            dwr = (wr37 - wr36) if (wr36 is not None and wr37 is not None) else None
            dn = (n37 - n36) / n36 * 100 if n36 else 0
            wr36_s = f'{wr36:6.2f}%' if wr36 is not None else '   --'
            wr37_s = f'{wr37:6.2f}%' if wr37 is not None else '   --'
            dwr_s = f'{dwr:+6.2f}pp' if dwr is not None else '    --'
            mark = ''
            if dwr is not None:
                if bucket in put_buckets and dwr > 0: mark = ' put-up'
                if bucket in call_buckets and abs(dwr) < 0.05: mark = ' (call=)'
            print(f'{bucket:<7} {n36:>7} {wr36_s:>10} {n37:>7} {wr37_s:>10} {dwr_s:>9} {dn:+7.1f}%{mark}')


if __name__ == '__main__':
    main()
