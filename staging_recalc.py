"""Read-only diagnostics for the worktree-based staging recalc workflow.

Use BEFORE promoting a staging worktree's algorithm version into production.
Verifies (a) all production stocks have score rows under the target version
for the latest trading date, (b) no date gaps in the lookback window,
(c) ScoreAssessmentRun + BacktestTemporalStats populated.

Does NOT modify any production state. Safe to run while production
`trader update` cron is active and while other recalc/assess jobs are in
flight.

CLI:
    python staging_recalc.py verify              # uses current worktree's ALGORITHM_VERSION
    python staging_recalc.py verify <commit>     # explicit commit
    python staging_recalc.py compare <a> <b>     # side-by-side coverage diff

See .claude/docs/staging-recalc.md for full workflow.
"""
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from peewee import fn

from database.models.core import (
    AlgorithmVersion, Score, Stock, ScoreAssessmentRun,
)
from database.models.technical import PriceHistory


def _resolve_version(commit_or_none):
    """Return AlgorithmVersion row for a commit hash, or for the worktree's
    current ALGORITHM_VERSION file when commit_or_none is None/'HEAD'.
    """
    if not commit_or_none or commit_or_none.upper() == 'HEAD':
        version_file = Path(__file__).parent / 'ALGORITHM_VERSION'
        if not version_file.exists():
            print(f"ERROR: {version_file} not found")
            sys.exit(1)
        commit = version_file.read_text().strip()
    else:
        commit = commit_or_none.strip()

    av = AlgorithmVersion.get_or_none(AlgorithmVersion.git_commit == commit)
    if av is None:
        print(f"ERROR: no AlgorithmVersion row for commit={commit!r}")
        print(f"  (recalc may not have started yet, or commit hash is wrong)")
        sys.exit(1)
    return av


def _coverage_for(av):
    """Return dict of coverage stats for the given AlgorithmVersion."""
    score_count = (
        Score.select(fn.COUNT(Score.symbol))
        .where(Score.version == av)
        .scalar()
    ) or 0

    if score_count == 0:
        return {
            'av': av,
            'score_count': 0,
            'distinct_stocks': 0,
            'min_date': None,
            'max_date': None,
            'stocks_at_max_date': 0,
            'distinct_dates': 0,
        }

    distinct_stocks = (
        Score.select(fn.COUNT(fn.DISTINCT(Score.symbol)))
        .where(Score.version == av)
        .scalar()
    ) or 0

    min_date, max_date = (
        Score.select(fn.MIN(Score.date), fn.MAX(Score.date))
        .where(Score.version == av)
        .tuples()
        .first()
    )

    distinct_dates = (
        Score.select(fn.COUNT(fn.DISTINCT(Score.date)))
        .where(Score.version == av)
        .scalar()
    ) or 0

    stocks_at_max = (
        Score.select(fn.COUNT(Score.symbol))
        .where((Score.version == av) & (Score.date == max_date))
        .scalar()
    ) or 0

    return {
        'av': av,
        'score_count': score_count,
        'distinct_stocks': distinct_stocks,
        'min_date': min_date,
        'max_date': max_date,
        'stocks_at_max_date': stocks_at_max,
        'distinct_dates': distinct_dates,
    }


def _latest_trading_date():
    """Most recent date in PriceHistory across the universe."""
    return PriceHistory.select(fn.MAX(PriceHistory.date)).scalar()


def _stocks_missing_at_date(av, target_date):
    """Stocks that have PriceHistory on target_date but no Score under av."""
    have_price = set(
        row.symbol for row in
        PriceHistory.select(PriceHistory.symbol)
        .where(PriceHistory.date == target_date)
        .distinct()
    )
    have_score = set(
        row.symbol for row in
        Score.select(Score.symbol)
        .where((Score.version == av) & (Score.date == target_date))
        .distinct()
    )
    return sorted(have_price - have_score)


def _date_gaps(av, lookback_days=30):
    """Trading dates in the last `lookback_days` (per PriceHistory) where
    the version has zero scores. Returns list of dates.
    """
    cutoff = (date.today() - timedelta(days=lookback_days * 2))  # buffer for weekends
    ph_dates = set(
        row.date for row in
        PriceHistory.select(PriceHistory.date)
        .where(PriceHistory.date >= cutoff)
        .distinct()
    )
    score_dates = set(
        row.date for row in
        Score.select(Score.date)
        .where((Score.version == av) & (Score.date >= cutoff))
        .distinct()
    )
    return sorted(ph_dates - score_dates)


def _assess_coverage(av):
    """Count ScoreAssessmentRun rows by (lookback, dte_strategy) for av."""
    rows = list(
        ScoreAssessmentRun.select()
        .where(ScoreAssessmentRun.version == av)
    )
    by_combo = defaultdict(int)
    for r in rows:
        key = (getattr(r, 'lookback_days', None),
               getattr(r, 'dte_strategy', None) or '30')
        by_combo[key] += 1
    return dict(by_combo)


def cmd_verify(commit):
    av = _resolve_version(commit)
    print(f"\n=== verify: AlgorithmVersion id={av.id} commit={av.git_commit} ===")
    print(f"    git_message: {av.git_message or '(none)'}")

    cov = _coverage_for(av)
    print(f"\nScore coverage:")
    print(f"  total rows:        {cov['score_count']:,}")
    print(f"  distinct stocks:   {cov['distinct_stocks']:,}")
    print(f"  distinct dates:    {cov['distinct_dates']:,}")
    print(f"  date range:        {cov['min_date']} -> {cov['max_date']}")

    if cov['score_count'] == 0:
        print("\nNo scores under this version — recalc has not started or has not "
              "completed any stock yet.")
        return 1

    latest_ph = _latest_trading_date()
    print(f"\nLatest PriceHistory date: {latest_ph}")
    print(f"Stocks scored on {cov['max_date']}: {cov['stocks_at_max_date']:,}")

    if cov['max_date'] != latest_ph:
        days_behind = (latest_ph - cov['max_date']).days
        print(f"  WARN: version is {days_behind} day(s) behind latest PriceHistory")

    missing = _stocks_missing_at_date(av, latest_ph)
    if missing:
        print(f"\nWARN: {len(missing)} stocks have PriceHistory on {latest_ph} "
              f"but no Score under this version:")
        for sym in missing[:25]:
            print(f"    {sym}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more")
    else:
        print(f"\nOK: Every stock with PriceHistory on {latest_ph} has a Score row")

    gaps = _date_gaps(av, lookback_days=30)
    if gaps:
        print(f"\nWARN: {len(gaps)} trading date(s) in last 30d have no scores:")
        for d in gaps[:10]:
            print(f"    {d}")
    else:
        print("\nOK: No date gaps in last 30 trading days")

    assess = _assess_coverage(av)
    if assess:
        print(f"\nScoreAssessmentRun rows by (lookback_days, dte_strategy):")
        for (lb, dte), n in sorted(assess.items()):
            print(f"  lookback={lb:>5} dte={dte}: {n} run(s)")
    else:
        print("\nWARN: No ScoreAssessmentRun rows for this version "
              "-- assess --force has not been run")

    ok = (
        not missing
        and not gaps
        and bool(assess)
        and cov['max_date'] == latest_ph
    )
    print()
    print("VERDICT: " + ("READY TO PROMOTE" if ok else "NOT READY -- see warnings above"))
    return 0 if ok else 1


def cmd_compare(commit_a, commit_b):
    av_a = _resolve_version(commit_a)
    av_b = _resolve_version(commit_b)
    cov_a = _coverage_for(av_a)
    cov_b = _coverage_for(av_b)

    print(f"\n=== compare: {av_a.git_commit} (id={av_a.id}) vs "
          f"{av_b.git_commit} (id={av_b.id}) ===\n")
    print(f"{'metric':<24} {'A':>20} {'B':>20} {'delta':>12}")
    print('-' * 80)
    for k in ('score_count', 'distinct_stocks', 'distinct_dates',
              'stocks_at_max_date'):
        a = cov_a[k]
        b = cov_b[k]
        d = b - a
        print(f"{k:<24} {a:>20,} {b:>20,} {d:>+12,}")
    print(f"{'min_date':<24} {str(cov_a['min_date']):>20} "
          f"{str(cov_b['min_date']):>20}")
    print(f"{'max_date':<24} {str(cov_a['max_date']):>20} "
          f"{str(cov_b['max_date']):>20}")
    return 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == 'verify':
        commit = rest[0] if rest else None
        return cmd_verify(commit)
    elif cmd == 'compare':
        if len(rest) != 2:
            print("usage: staging_recalc.py compare <commit_a> <commit_b>")
            return 2
        return cmd_compare(rest[0], rest[1])
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        return 2


if __name__ == '__main__':
    sys.exit(main())
