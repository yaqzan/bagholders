"""
Quantify the price_history adjustment SPLICE surfaced by the Sharadar reconciliation.

Hypothesis: our stored daily closes are dividend-adjusted only from some cut date
onward; before it they are un-dividend-adjusted. That injects a phantom one-day gap on
the cut date, sized by each name's cumulative dividend yield since -- invisible without
an external reference, and contaminating every indicator/score/backtest whose window
spans it.

Test, per universe ticker, against Sharadar (which supplies BOTH conventions):
  r_adj(t)   = our_close(t) / closeadj(t)      -> flat 1.0 if we are div-adjusted
  r_unadj(t) = our_close(t) / closeunadj(t)    -> flat 1.0 if we are NOT
Compare the medians of each on both sides of the candidate cut date. A splice shows as
r_unadj ~ 1 BEFORE and r_adj ~ 1 AFTER.

Read-only.  python diagnose_splice.py
"""
import json
import os
import sys
from collections import defaultdict
from datetime import date

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
PARQUET = os.path.join(DATA, "stocks_by_ticker.parquet")
OUT = os.path.join(DATA, "SPLICE_DIAGNOSIS.json")

CUT = date(2015, 12, 28)
PAD = 250   # trading days sampled either side


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def main():
    import polars as pl
    from database.models.core import Stock
    from database.trader_database import DB

    universe = sorted({s.symbol.upper() for s in Stock.select(Stock.symbol)})
    lf = pl.scan_parquet(PARQUET).select(
        ["ticker", "date", "close", "closeadj", "closeunadj"])

    per = {}
    CH = 100
    for i in range(0, len(universe), CH):
        chunk = universe[i:i + CH]
        sh = lf.filter(pl.col("ticker").is_in(chunk)).collect()
        if sh.height == 0:
            continue
        s_by = defaultdict(dict)
        for t, d, c, ca, cu in sh.iter_rows():
            s_by[t][d] = (c, ca, cu)

        ph = ",".join(["%s"] * len(chunk))
        cur = DB.execute_sql(
            f"SELECT `symbol`, `date`, `close` FROM price_history WHERE `symbol` IN ({ph})",
            tuple(chunk))
        ours = defaultdict(dict)
        for sym, d, c in cur.fetchall():
            if c is not None:
                ours[sym][d] = float(c)

        for t in chunk:
            if t not in s_by or t not in ours:
                continue
            before_adj, before_un, after_adj, after_un = [], [], [], []
            for d, oc in ours[t].items():
                row = s_by[t].get(d)
                if not row or oc <= 0:
                    continue
                _c, ca, cu = row
                delta = (d - CUT).days
                if -PAD * 2 <= delta < 0:
                    if ca:
                        before_adj.append(oc / ca)
                    if _c:
                        before_un.append(oc / _c)
                elif 0 <= delta <= PAD * 2:
                    if ca:
                        after_adj.append(oc / ca)
                    if _c:
                        after_un.append(oc / _c)
            if len(before_adj) < 30 or len(after_adj) < 30:
                continue
            ba, bu = med(before_adj), med(before_un)
            aa, au = med(after_adj), med(after_un)
            spliced = (bu is not None and aa is not None
                       and abs(bu - 1) < 0.02 and abs(aa - 1) < 0.02
                       and abs(ba - 1) >= 0.02)
            per[t] = {
                "median_ratio_vs_ADJ_before": round(ba, 4) if ba else None,
                "median_ratio_vs_SPLITADJ_before": round(bu, 4) if bu else None,
                "median_ratio_vs_ADJ_after": round(aa, 4) if aa else None,
                "median_ratio_vs_SPLITADJ_after": round(au, 4) if au else None,
                "spliced": spliced,
                "implied_phantom_gap_pct": (round((1.0 / ba - 1) * 100, 2)
                                            if (spliced and ba) else None),
            }
        print(f"  {min(i + CH, len(universe))}/{len(universe)}", flush=True)

    spliced = {t: r for t, r in per.items() if r["spliced"]}
    gaps = [r["implied_phantom_gap_pct"] for r in spliced.values()
            if r["implied_phantom_gap_pct"] is not None]
    out = {
        "cut_date_tested": str(CUT),
        "judged": len(per),
        "spliced": len(spliced),
        "spliced_pct": round(100 * len(spliced) / len(per), 2) if per else 0,
        "phantom_gap_pct": {
            "min": min(gaps) if gaps else None,
            "median": med(gaps),
            "max": max(gaps) if gaps else None,
        },
        "worst_20": dict(sorted(spliced.items(),
                                key=lambda kv: -(kv[1]["implied_phantom_gap_pct"] or 0))[:20]),
        "per_ticker": per,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\njudged {len(per)}  spliced {len(spliced)} ({out['spliced_pct']}%)")
    print(f"phantom one-day gap: min {out['phantom_gap_pct']['min']}%  "
          f"median {out['phantom_gap_pct']['median']}%  max {out['phantom_gap_pct']['max']}%")
    print("\nworst:")
    for t, r in list(out["worst_20"].items())[:12]:
        print(f"  {t:>6} pre: vs_splitadj={r['median_ratio_vs_SPLITADJ_before']} "
              f"vs_adj={r['median_ratio_vs_ADJ_before']} | post: vs_adj={r['median_ratio_vs_ADJ_after']}"
              f"  gap={r['implied_phantom_gap_pct']}%")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
