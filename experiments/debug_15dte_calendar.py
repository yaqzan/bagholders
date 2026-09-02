"""Debug — why does 15 DTE backtest only produce 2016 trades?"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_cascade_15dte import run_cascade_backtest
from database.models.core import AlgorithmVersion

v = AlgorithmVersion.get_active_scores_version()
print(f'Active version: id={v.id} commit={v.git_commit[:7]}', flush=True)
r = run_cascade_backtest(v.id, min_score=70.0, verbose=False)
eq = r.get('equity_curve', [])
trades = r.get('trade_log', [])

print(f'equity_curve: {len(eq)} points')
print(f'trade_log:    {len(trades)} trades')
print()
print(f'Initial equity: ${eq[0][1]:,.2f}' if eq else 'No equity')
print(f'Final equity:   ${eq[-1][1]:,.2f}' if eq else 'No equity')
print(f'Max DD: {r.get("max_dd", 0)*100:.1f}%')
print()
print('Equity samples:')
for i in [0, 50, 100, 200, 300, 500, 1000, 1500, 2000, 2594]:
    if i < len(eq):
        d, e = eq[i]
        print(f'  bar {i:>5} {d}: ${e:>15,.2f}')

# Find lowest equity in curve
if len(eq) > 1:
    print()
    print('Equity range:')
    min_eq = min(eq, key=lambda x: x[1])
    max_eq = max(eq, key=lambda x: x[1])
    print(f'  Min: {min_eq[0]}: ${min_eq[1]:,.2f}')
    print(f'  Max: {max_eq[0]}: ${max_eq[1]:,.2f}')
    print(f'  DD-from-max: {(1.0 - eq[-1][1] / max_eq[1])*100:.1f}%')
    print(f'  Breaker (60%) -> DD must be >60% to block; current DD={int((1.0 - eq[-1][1] / max_eq[1])*100)}%')

# Now check outcomes_by_date for post-2016 dates
print()
print('Checking outcomes_by_date dates...')
from backtest_cascade_15dte import run_cascade_backtest as _rcb
# Re-import to access outcomes_by_date
import backtest_cascade_15dte as bc15
# Patch to expose outcomes_by_date
_outcomes = r.get('outcomes_by_date')
if _outcomes:
    dates_w_outcomes = sorted(_outcomes.keys())
    print(f'  outcomes_by_date: {len(dates_w_outcomes)} signal dates')
    print(f'  First: {dates_w_outcomes[0]}')
    print(f'  Last:  {dates_w_outcomes[-1]}')
    after_q1_2016 = [d for d in dates_w_outcomes if d.year > 2016 or (d.year == 2016 and d.month > 3)]
    print(f'  Dates after 2016-Q1: {len(after_q1_2016)}')
    if after_q1_2016:
        print(f'  E.g. {after_q1_2016[0]}: {len(_outcomes[after_q1_2016[0]])} outcomes')
else:
    print('  No outcomes_by_date in result')
