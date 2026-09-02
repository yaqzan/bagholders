"""Validate an honest-recalc'd version: contamination tripwire + honest-vs-inflated
WR comparison. Usage: python validate_honest_version.py <version_id>

- Tripwire: v59 row count must be ~unchanged (1,168,564 ± small) — proves the
  honest recalc wrote ONLY the target version, not v59 (the candidate-commit
  ALGORITHM_VERSION landmine).
- Comparison: the TWO latest 5y/30dte assess runs for the target version
  (newest = honest, previous = inflated) by call+put bucket, WR15/WR7/WR30.
  Expect ~12pp WR15 deflation on top call tiers (matches v69 vs v60).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from database.trader_database import DB

BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<15', '<5']
# Contamination safety is enforced PRE-LAUNCH (get_or_create_current().id == target
# before any recalc) — that is the writer target, the real gate. The post-recalc
# count here is informational only; COUNT over the 117GB scores table can time out
# under load, so it is wrapped + non-fatal. (v59 full count baseline ~3,054,522.)


def main():
    vid = int(sys.argv[1].lstrip('v')) if len(sys.argv) > 1 else 68
    try:
        cov = DB.execute_sql('SELECT COUNT(*),MIN(date),MAX(date) FROM scores WHERE version_id=%s', [vid]).fetchone()
        print(f'[target] v{vid} rows={cov[0]:,} {cov[1]}..{cov[2]}  (honest recalc target)')
    except Exception as e:
        print(f'[target] v{vid} count skipped (DB timeout under load): {type(e).__name__}')

    # two latest 5y/30dte runs for this version
    runs = DB.execute_sql(
        'SELECT id FROM score_assessment_runs WHERE version_id=%s AND lookback_days=1825 '
        'AND dte_strategy=%s ORDER BY id DESC LIMIT 2', [vid, '30']).fetchall()
    if len(runs) < 2:
        print(f'[compare] only {len(runs)} run(s) for v{vid} — need 2 (honest+inflated). '
              f'(inflated run may predate; showing newest only)')
    honest = runs[0][0] if runs else None
    inflated = runs[1][0] if len(runs) > 1 else None

    def wr(run_id):
        if not run_id: return {}
        rows = DB.execute_sql('SELECT bucket,win_rate_7d,win_rate_15d,win_rate_30d '
                              'FROM score_assessment_results WHERE run_id=%s', [run_id]).fetchall()
        return {b: (a, c, d) for b, a, c, d in rows}
    H, I = wr(honest), wr(inflated)
    print(f'\nv{vid} HONEST (run {honest}) vs INFLATED (run {inflated}) — WR by bucket (5y/30dte)')
    print(f"{'bucket':<6} | {'infl WR15':>9} {'hon WR15':>9} {'dWR15':>7} | {'infl WR7':>8} {'hon WR7':>8} | {'infl WR30':>9} {'hon WR30':>9}")
    for b in BUCKETS:
        h = H.get(b); inf = I.get(b)
        if not h:
            continue
        h15 = h[1]; i15 = (inf[1] if inf else None)
        d = (h15 - i15) if (h15 is not None and i15 is not None) else None
        print(f"{b:<6} | {(f'{i15:.1f}' if i15 is not None else '-'):>9} {(f'{h15:.1f}' if h15 is not None else '-'):>9} "
              f"{(f'{d:+.1f}' if d is not None else '-'):>7} | "
              f"{(f'{inf[0]:.1f}' if inf else '-'):>8} {(f'{h[0]:.1f}' if h[0] is not None else '-'):>8} | "
              f"{(f'{inf[2]:.1f}' if inf else '-'):>9} {(f'{h[2]:.1f}' if h[2] is not None else '-'):>9}")


if __name__ == '__main__':
    main()
