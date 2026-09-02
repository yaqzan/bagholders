"""
P2.A STEP 2 GATE -- split/adjustment reconciliation between Sharadar and our stored
price_history. Read-only. **<1% mismatch or KILL.**

Method. Both series are split+dividend adjusted (ours = yfinance auto_adjust=True,
Sharadar = closeadj), so on shared dates ratio_t = our_close_t / sharadar_closeadj_t
should be ~flat. What we are hunting is a MISSED OR EXTRA SPLIT, which shows up as a
single-day step change in that ratio (x2, x3, x1.5 ...). Vendor differences in dividend
methodology instead produce slow drift, which is harmless for our purposes and must not
be allowed to trip the gate -- so the statistic is the largest ONE-DAY jump in the ratio,
not the ratio's overall spread.

  max_daily_jump(ticker) = max_t | ratio_t / ratio_{t-1} - 1 |

  >= 0.20  -> split/adjustment mismatch (a 2:1 split is 0.50; noise is <0.02)

Gate: mismatched tickers / overlapping tickers < 1%.

  python reconcile_sharadar.py
"""
import csv
import io
import json
import os
import sys
import zipfile
from collections import defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
PARQUET = os.path.join(DATA, "stocks_by_ticker.parquet")
OUT = os.path.join(DATA, "RECONCILIATION.json")

JUMP_THRESHOLD = 0.20      # one-day ratio step that means a split disagreement
GATE_MAX_MISMATCH = 0.01   # <1% or KILL
MIN_SHARED_DAYS = 60       # too few shared bars to judge


def sp500_ever():
    z = zipfile.ZipFile(os.path.join(DATA, "sp500.csv.zip"))
    with z.open(z.namelist()[0]) as f:
        s = {r["ticker"].upper()
             for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
             if r.get("ticker")}
    z.close()
    return s


def main():
    import polars as pl
    from database.models.core import Stock
    from database.trader_database import DB

    universe = {s.symbol.upper() for s in Stock.select(Stock.symbol)}
    scope = sp500_ever() | universe

    # Only tickers we ALREADY have can be reconciled.
    overlap = sorted(universe)
    print(f"scope={len(scope)}  live universe={len(universe)}  reconcilable={len(overlap)}",
          flush=True)

    lf = pl.scan_parquet(PARQUET).select(["ticker", "date", "close", "closeadj"])

    results = {}
    no_sharadar = []
    too_short = []
    CH = 100
    for i in range(0, len(overlap), CH):
        chunk = overlap[i:i + CH]
        sh = (lf.filter(pl.col("ticker").is_in(chunk))
                .collect())
        if sh.height == 0:
            no_sharadar.extend(chunk)
            continue
        sh_by = defaultdict(dict)
        for t, d, _c, ca in sh.iter_rows():
            if ca and ca > 0:
                sh_by[t][d] = ca

        ph = ",".join(["%s"] * len(chunk))
        cur = DB.execute_sql(
            f"SELECT `symbol`, `date`, `close` FROM price_history "
            f"WHERE `symbol` IN ({ph}) ORDER BY `symbol`, `date`", tuple(chunk))
        ours = defaultdict(list)
        for sym, d, c in cur.fetchall():
            if c is not None and float(c) > 0:
                ours[sym].append((d, float(c)))

        for t in chunk:
            if t not in sh_by:
                no_sharadar.append(t)
                continue
            shared = [(d, c, sh_by[t][d]) for d, c in ours.get(t, []) if d in sh_by[t]]
            if len(shared) < MIN_SHARED_DAYS:
                too_short.append(t)
                continue
            shared.sort()
            ratios = [c / a for _d, c, a in shared]
            jumps = [abs(ratios[k] / ratios[k - 1] - 1.0) for k in range(1, len(ratios))
                     if ratios[k - 1] > 0]
            mx = max(jumps) if jumps else 0.0
            mx_at = None
            if jumps:
                mx_at = str(shared[jumps.index(mx) + 1][0])
            med = sorted(ratios)[len(ratios) // 2]
            results[t] = {
                "shared_days": len(shared),
                "median_ratio": round(med, 5),
                "max_daily_jump": round(mx, 5),
                "max_jump_date": mx_at,
                "mismatch": mx >= JUMP_THRESHOLD,
            }
        print(f"  {min(i + CH, len(overlap))}/{len(overlap)}", flush=True)

    judged = len(results)
    bad = sorted([t for t, r in results.items() if r["mismatch"]],
                 key=lambda t: -results[t]["max_daily_jump"])
    rate = (len(bad) / judged) if judged else 0.0
    verdict = "PASS" if rate < GATE_MAX_MISMATCH else "KILL"

    out = {
        "gate": {
            "jump_threshold": JUMP_THRESHOLD,
            "max_mismatch_rate": GATE_MAX_MISMATCH,
            "min_shared_days": MIN_SHARED_DAYS,
        },
        "judged_tickers": judged,
        "mismatched": len(bad),
        "mismatch_rate": round(rate, 5),
        "verdict": verdict,
        "not_in_sharadar": sorted(set(no_sharadar)),
        "too_few_shared_days": sorted(set(too_short)),
        "worst": {t: results[t] for t in bad[:40]},
        "per_ticker": results,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)

    print(f"\njudged {judged} tickers")
    print(f"mismatched (max 1-day ratio jump >= {JUMP_THRESHOLD}): {len(bad)} "
          f"({rate * 100:.3f}%)")
    print(f"not in Sharadar: {len(set(no_sharadar))}   too few shared days: {len(set(too_short))}")
    print(f"\nVERDICT: {verdict}  (gate: <{GATE_MAX_MISMATCH * 100:.0f}%)")
    if bad:
        print("\nworst offenders:")
        for t in bad[:15]:
            r = results[t]
            print(f"  {t:>8} jump={r['max_daily_jump']:.3f} at {r['max_jump_date']} "
                  f"median_ratio={r['median_ratio']:.4f} n={r['shared_days']}")
    print(f"\nwrote {OUT}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
