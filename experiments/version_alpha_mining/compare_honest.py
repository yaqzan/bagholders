"""HONEST-ONLY version compare. Reads honest_versions.json and tabulates WR15 by
bucket (5y/30dte) for ONLY the honest-recalc'd versions — inflated versions are
WITHHELD so the comparison is meaningful. Grows as the overnight grind appends
versions. Flags the best version per bucket ('best at what').

Usage: python experiments/version_alpha_mining/compare_honest.py [--metric 15d|7d|30d]
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from database.trader_database import DB

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<15', '<5']


def main():
    metric = '15d'
    if '--metric' in sys.argv:
        metric = sys.argv[sys.argv.index('--metric') + 1]
    col = f'win_rate_{metric}'
    reg = json.load(open(os.path.join(HERE, 'honest_versions.json')))
    versions = sorted(set(reg['honest_recalc_versions']))

    data = {}   # vid -> {bucket: wr}
    for vid in versions:
        run = DB.execute_sql(
            'SELECT id FROM score_assessment_runs WHERE version_id=%s AND lookback_days=1825 '
            'AND dte_strategy=%s ORDER BY id DESC LIMIT 1', [vid, '30']).fetchone()
        d = {}
        if run:
            for b, w in DB.execute_sql(
                f'SELECT bucket, {col} FROM score_assessment_results WHERE run_id=%s', [run[0]]).fetchall():
                d[b] = w
        data[vid] = d

    print(f"HONEST version compare — WR{metric} by bucket (5y/30dte). Inflated versions WITHHELD.")
    print(f"Versions (honest): {versions}")
    print("=" * (8 + 8 * len(versions)))
    hdr = f"{'bucket':<7}" + "".join(f"v{v:>6}" for v in versions) + "  | best"
    print(hdr); print("-" * len(hdr))
    for b in BUCKETS:
        cells = []
        best_v, best_w = None, None
        for v in versions:
            w = data[v].get(b)
            cells.append(f"{w:>6.1f}" if w is not None else f"{'-':>6}")
            if w is not None and (best_w is None or w > best_w):
                best_w, best_v = w, v
        best = f"v{best_v} ({best_w:.1f})" if best_v is not None else "-"
        print(f"{b:<7}" + "".join(cells) + f"  | {best}")
    print("-" * len(hdr))
    print("Append a version id to honest_versions.json after its honest recalc+assess to include it here.")
    print("Anchor: a correctly-honest version sits ~50-51% on 75+. 'best at what' = highest honest WR per bucket.")


if __name__ == '__main__':
    main()
