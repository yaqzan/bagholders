"""Rank the NOT-yet-honest scoring versions by their INFLATED per-trade WR15 — a
heuristic 'window into potential' for prioritizing the honest-recalc grind under a
time budget. Higher inflated WR on high-conviction tiers => stronger mechanism worth
honest-verifying first (could be real alpha, or the biggest look-ahead artifact —
either is a 'significant finding'). Inflated numbers are UNRELIABLE for ranking the
honest truth; this is ONLY a prioritization heuristic.

Usage: python experiments/version_alpha_mining/rank_potential.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from database.trader_database import DB

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    reg = json.load(open(os.path.join(HERE, 'honest_versions.json')))
    honest = set(reg['honest_recalc_versions'])
    redundant = {int(k) for k in reg.get('redundant_skip', {})}  # honest form == an existing honest version
    exclude = honest | redundant
    # all scoring versions with a 5y/30dte assess run, newest-id first
    rows = DB.execute_sql(
        'SELECT DISTINCT version_id FROM score_assessment_runs '
        'WHERE lookback_days=1825 AND dte_strategy=%s', ['30']).fetchall()
    vids = sorted({r[0] for r in rows if r[0] not in exclude}, reverse=True)
    if redundant:
        print(f"[excluded as redundant] {sorted(redundant)} — see honest_versions.json redundant_skip "
              f"(e.g. v60 == v69 honest).\n")

    out = []
    for vid in vids:
        msg = (DB.execute_sql('SELECT git_message FROM algorithm_versions WHERE id=%s', [vid]).fetchone() or [''])[0] or ''
        run = DB.execute_sql(
            'SELECT id FROM score_assessment_runs WHERE version_id=%s AND lookback_days=1825 '
            'AND dte_strategy=%s ORDER BY id DESC LIMIT 1', [vid, '30']).fetchone()
        wr = {}
        if run:
            for b, w in DB.execute_sql(
                'SELECT bucket, win_rate_15d FROM score_assessment_results WHERE run_id=%s', [run[0]]).fetchall():
                wr[b] = w
        out.append((vid, wr.get('95+'), wr.get('85+'), wr.get('75+'), wr.get('<15'), msg[:54]))

    # rank by inflated 75+ WR15 desc (primary potential signal), then 85+
    out.sort(key=lambda r: (r[3] or 0, r[2] or 0), reverse=True)
    print("NOT-yet-honest scoring versions, ranked by INFLATED WR15 (heuristic only — see flags!)")
    print("=" * 112)
    print(f"{'rank':<5}{'ver':<6}{'95+':>6}{'85+':>6}{'75+':>6}{'<15':>6}  {'flag':<10}mechanism (git message)")
    print("-" * 112)
    WEEKLY = {17, 27, 32, 34, 36, 42, 55, 56, 57, 61, 65, 66}  # lean on the weekly signal == most look-ahead-distorted
    for i, (vid, w95, w85, w75, p15, msg) in enumerate(out, 1):
        def f(x): return f'{x:.1f}' if x is not None else '  -'
        flag = ''
        if vid in WEEKLY:           flag = 'WEEKLY*'    # highest honest-surprise (look-ahead bit hardest here)
        elif 'REVERT' in msg.upper(): flag = 're-eval'  # DROPPED under inflated eval -> maybe an honest gem
        print(f"{i:<5}v{vid:<5}{f(w95):>6}{f(w85):>6}{f(w75):>6}{f(p15):>6}  {flag:<8}{msg}")
    print("-" * 112)
    print(f"{len(out)} versions remain (v60 excluded = v69). Honest anchor: 75+ ~{reg['honest_75plus_wr15_anchor']}%.")
    print("PRIORITIZE BY MINING-VALUE, *NOT* by age or inflated WR (we cannot tell signal from inflation-noise")
    print("in ANY version until we honest-recalc it). The look-ahead was a WEEKLY, MECHANISM-DEPENDENT inflation")
    print("(EARN_BOOST pre1 hugely inflated, pre7 = the honest gem). So highest expected information:")
    print("  (1) WEEKLY* mechanisms above (honest differs MOST there);")
    print("  (2) mechanisms DROPPED on inflated evidence ('re-eval' / known reverts: v40 SVD, v33/v34, v24) —")
    print("      candidate honest gems, UNLESS the revert was structural (v42 rolling-weekly bypassed push-bands).")
    print("Only deprioritize on HONEST evidence (v68/VCBW recalc'd -> ~0 honest, so skip its v67 twin).")


if __name__ == '__main__':
    main()
