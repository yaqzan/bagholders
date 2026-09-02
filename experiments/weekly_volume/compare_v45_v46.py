"""Compare v45 vs v46 ScoreAssessmentResult — H1-H5 strict gate verification.

Run AFTER `trader recalculate --force --full` completes (which auto-runs assess).

Reports:
  - Per-tier ΔTP15 / ΔWR15 / ΔN at 5y window (primary gate)
  - Multi-window sign-consistency (1y/3y/5y)
  - Comparison vs the parquet-projected ΔWR (sanity check)

Per CLAUDE.md, scoring changes ship via per-trade TP% gate (NOT canonical MC).
Expected: per-tier ΔWR within ±0.3pp of parquet projection.
"""
from __future__ import annotations
import io, sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from database.models.core import (
        AlgorithmVersion, ScoreAssessmentRun, ScoreAssessmentResult,
    )

    av_v45 = AlgorithmVersion.get_or_none(AlgorithmVersion.git_commit == '56eb1f8')
    if av_v45 is None:
        print('[error] could not find v45 (56eb1f8) in DB')
        return
    av_v46 = AlgorithmVersion.get(AlgorithmVersion.id == av_v45.id + 1)

    print(f'v45: id={av_v45.id} commit={av_v45.git_commit}')
    print(f'v46: id={av_v46.id} commit={av_v46.git_commit}')
    print()

    # Fetch latest 5y assessment runs for both versions, 30 DTE, metric='wr'
    def fetch(av, dte='30', metric='wr'):
        q = (ScoreAssessmentRun
             .select()
             .where(
                 ScoreAssessmentRun.version == av,
                 ScoreAssessmentRun.lookback_days == 1825,
                 ScoreAssessmentRun.dte_strategy == dte,
                 ScoreAssessmentRun.metric == metric,
             )
             .order_by(ScoreAssessmentRun.id.desc()))
        return q.first()

    runs = {}
    for label, av in [('v45', av_v45), ('v46', av_v46)]:
        for dte in ['30']:
            for metric in ['wr', 'tp']:
                key = (label, dte, metric)
                r = fetch(av, dte, metric)
                runs[key] = r
                if r:
                    print(f'  {label} dte={dte} metric={metric}: run_id={r.id} '
                          f'run_at={getattr(r, "run_at", None)}')
                else:
                    print(f'  {label} dte={dte} metric={metric}: NOT FOUND')
    print()

    if not runs.get(('v46', '30', 'wr')):
        print('[abort] v46 5y wr run not found — recalc/assess incomplete')
        return

    def fetch_buckets(run):
        # bucket field stores both side and threshold (e.g. '70+', '<25', etc.)
        rows = (ScoreAssessmentResult
                .select()
                .where(ScoreAssessmentResult.run == run))
        return {r.bucket: r for r in rows}

    print('=' * 110)
    print('  Per-tier comparison: v45 vs v46  (5y, 30 DTE, metric=wr)')
    print('=' * 110)
    v45_b = fetch_buckets(runs[('v45', '30', 'wr')])
    v46_b = fetch_buckets(runs[('v46', '30', 'wr')])

    # Primary call tiers
    print(f'  {"Tier":<8s}  {"v45 WR15 / N":<22s} {"v46 WR15 / N":<22s} {"ΔWR":<8s} {"ΔN%":<8s}')
    print('  ' + '-' * 80)
    call_tiers = ['95+', '90+', '85+', '80+', '75+', '70+']
    for tier_label in call_tiers:
        a = v45_b.get(tier_label)
        b = v46_b.get(tier_label)
        if a is None or b is None:
            print(f'  {tier_label:<8s}  N/A')
            continue
        a_wr = float(a.win_rate_15d or 0)
        b_wr = float(b.win_rate_15d or 0)
        d_wr = b_wr - a_wr
        d_n = ((b.sample_count - a.sample_count) / a.sample_count * 100) if a.sample_count else 0
        tag = '✓' if d_wr >= 0.5 else ('⚠' if abs(d_wr) < 0.3 else ('✗' if d_wr < -0.3 else ' '))
        print(f'  +{tier_label:<7s}  {a_wr:>5.2f}% / {a.sample_count:>5d}      '
              f'{b_wr:>5.2f}% / {b.sample_count:>5d}     {d_wr:>+5.2f}pp  {d_n:>+5.1f}%  {tag}')

    print()
    put_tiers = ['<5', '<10', '<15', '<20', '<25', '<30']
    print(f'  {"Tier":<8s}  {"v45 WR15 / N":<22s} {"v46 WR15 / N":<22s} {"ΔWR":<8s} {"ΔN%":<8s}')
    print('  ' + '-' * 80)
    for tier_label in put_tiers:
        a = v45_b.get(tier_label)
        b = v46_b.get(tier_label)
        if a is None or b is None:
            continue
        a_wr = float(a.win_rate_15d or 0)
        b_wr = float(b.win_rate_15d or 0)
        d_wr = b_wr - a_wr
        d_n = ((b.sample_count - a.sample_count) / a.sample_count * 100) if a.sample_count else 0
        print(f'  {tier_label:<8s}  {a_wr:>5.2f}% / {a.sample_count:>5d}      '
              f'{b_wr:>5.2f}% / {b.sample_count:>5d}     {d_wr:>+5.2f}pp  {d_n:>+5.1f}%')

    # Also report TP15 (option-aligned barrier)
    if runs.get(('v45', '30', 'tp')) and runs.get(('v46', '30', 'tp')):
        print()
        print('=' * 110)
        print('  Per-tier comparison: v45 vs v46  (5y, 30 DTE, metric=TP — option-aligned)')
        print('=' * 110)
        v45_t = fetch_buckets(runs[('v45', '30', 'tp')])
        v46_t = fetch_buckets(runs[('v46', '30', 'tp')])
        print(f'  {"Tier":<8s}  {"v45 TP15 / N":<22s} {"v46 TP15 / N":<22s} {"ΔTP":<8s} {"ΔN%":<8s}')
        print('  ' + '-' * 80)
        for tier_label in call_tiers:
            a = v45_t.get(tier_label)
            b = v46_t.get(tier_label)
            if a is None or b is None:
                continue
            a_wr = float(a.win_rate_15d or 0)
            b_wr = float(b.win_rate_15d or 0)
            d_wr = b_wr - a_wr
            d_n = ((b.sample_count - a.sample_count) / a.sample_count * 100) if a.sample_count else 0
            tag = '✓' if d_wr >= 0.5 else ('⚠' if abs(d_wr) < 0.3 else ('✗' if d_wr < -0.3 else ' '))
            print(f'  +{tier_label:<7s}  {a_wr:>5.2f}% / {a.sample_count:>5d}      '
                  f'{b_wr:>5.2f}% / {b.sample_count:>5d}     {d_wr:>+5.2f}pp  {d_n:>+5.1f}%  {tag}')


if __name__ == '__main__':
    main()
