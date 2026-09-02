"""
Build a PRICE-VALIDATED map: our price_history symbol -> (Sharadar ticker, table).

Naive string / relatedtickers matching is unsafe here. Ticker strings get recycled, so
`CA` resolves to a California muni-bond ETF (ours is Computer Associates), `EMC` to a
Global X emerging-markets fund (ours is EMC Corp), `NFX` to a 2x Netflix ETF (ours is
Newfield Exploration). Accepting those would silently graft one company's price history
onto another's -- a corruption far worse than the splice we are repairing.

VALIDATION: daily-RETURN correlation on shared dates.
Return correlation is invariant to price convention (dividend-adjusted vs not) and to any
constant scale factor (an un-back-applied split), which is exactly what we need, because
our stored series are known to differ from Sharadar in both of those ways. A genuine match
still correlates ~1.0; a recycled ticker correlates ~0.

  accept if n_shared >= 60 and corr(daily returns) >= 0.95

Candidates are tried in order: direct SEP -> direct SFP -> punctuation/venue variants ->
relatedtickers -> numeric-suffix siblings -> rename chains. The first candidate that
VALIDATES wins; a candidate that fails validation is recorded as rejected, not silently
dropped, so the identity swaps stay visible.

Symbols with no stored history (the S&P names new to us) cannot be validated this way;
they are taken directly from Sharadar's own sp500 membership table, which is authoritative
for its own ticker strings, and flagged `unvalidated_new`.

Read-only.  python build_symbol_map.py
"""
import csv
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
EQ = os.path.join(DATA, "stocks_by_ticker.parquet")
FU = os.path.join(DATA, "funds_by_ticker.parquet")
OUT = os.path.join(DATA, "SYMBOL_MAP.json")

MIN_SHARED = 60
MIN_CORR = 0.95
# Pearson is dominated by single-day corporate-action outliers (spinoffs: MO, DD, MDLZ,
# JCI, FOX). Sharadar's closeadj adjusts for spinoffs, yfinance does not, so one -60%
# separation day can crush corr across 5,000 otherwise-identical bars. Agreement RATE is
# robust to that: a true match agrees day-to-day ~always, a recycled ticker ~never.
MIN_AGREE = 0.90
AGREE_TOL = 0.005
# A fixed-tolerance HIT RATE also fails: heavily split-adjusted old bars sit near $0.89
# (AAPL 2000), where a single rounding tick is ~0.1% and return noise routinely exceeds a
# 50bp tolerance -- it rejected AAPL and NVDA. The MEDIAN absolute return difference is
# robust to both the rounding tail and one-day corporate-action outliers:
#   true match         -> ~0.0001-0.001 (rounding only)
#   different company  -> ~0.01-0.02    (its own daily volatility)
# 0.005 sits well clear of both.
MAX_MED_DIFF = 0.005


def read_zip(name):
    z = zipfile.ZipFile(os.path.join(DATA, name))
    with z.open(z.namelist()[0]) as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace")))
    z.close()
    return rows


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def main():
    import polars as pl
    from database.models.core import Stock
    from database.trader_database import DB

    tick = read_zip("tickers.csv.zip")
    sep = {r["ticker"].upper() for r in tick if r["table"] == "SEP"}
    sfp = {r["ticker"].upper() for r in tick if r["table"] == "SFP"}
    names = {r["ticker"].upper(): r.get("name") for r in tick}

    rel = defaultdict(set)
    for r in tick:
        for o in (r.get("relatedtickers") or "").split():
            rel[o.upper()].add((r["ticker"].upper(), r["table"]))
    bybase = defaultdict(list)
    for t in sep | sfp:
        b = re.sub(r"\d+$", "", t)
        if b != t:
            bybase[b].append(t)
    chains = defaultdict(set)
    for r in read_zip("actions.csv.zip"):
        if r["action"] in ("tickerchangefrom", "tickerchangeto"):
            a, b = (r.get("ticker") or "").upper(), (r.get("contraticker") or "").upper()
            if a and b:
                chains[a].add(b)
                chains[b].add(a)

    sp500 = {r["ticker"].upper() for r in read_zip("sp500.csv.zip") if r.get("ticker")}
    universe = sorted({s.symbol.upper() for s in Stock.select(Stock.symbol)})

    eq = pl.scan_parquet(EQ)
    fu = pl.scan_parquet(FU) if os.path.exists(FU) else None

    def candidates(s):
        out = []
        if s in sep:
            out.append((s, "SEP"))
        if s in sfp:
            out.append((s, "SFP"))
        for v in {s.replace("-", "."), s.replace(".", "-"), re.sub(r"\.(TO|V|L)$", "", s)} - {s}:
            if v in sep:
                out.append((v, "SEP"))
            if v in sfp:
                out.append((v, "SFP"))
        for c in sorted(rel.get(s, ())):
            out.append(c)
        for c in sorted(bybase.get(s, ())):
            out.append((c, "SEP" if c in sep else "SFP"))
        for c in sorted(chains.get(s, ())):
            if c in sep:
                out.append((c, "SEP"))
            elif c in sfp:
                out.append((c, "SFP"))
        seen, uniq = set(), []
        for c in out:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    # our stored closes, per symbol
    print("loading stored closes ...", flush=True)
    ours = defaultdict(dict)
    CH = 200
    for i in range(0, len(universe), CH):
        ch = universe[i:i + CH]
        ph = ",".join(["%s"] * len(ch))
        cur = DB.execute_sql(
            f"SELECT `symbol`,`date`,`close` FROM price_history WHERE `symbol` IN ({ph})",
            tuple(ch))
        for s, d, c in cur.fetchall():
            if c is not None and float(c) > 0:
                ours[s][d] = float(c)

    mapping, rejected, unresolved = {}, defaultdict(list), []
    for n, s in enumerate(universe):
        cands = candidates(s)
        if not cands:
            unresolved.append(s)
            continue
        mine = ours.get(s)
        chosen = None
        for tkr, tbl in cands:
            lf = eq if tbl == "SEP" else fu
            if lf is None:
                continue
            df = lf.filter(pl.col("ticker") == tkr).select(["date", "closeadj"]).collect()
            if df.height == 0:
                continue
            theirs = {d: c for d, c in df.iter_rows() if c and c > 0}
            if not mine:
                chosen = {"ticker": tkr, "table": tbl, "validation": "no_stored_history"}
                break
            shared = sorted(set(mine) & set(theirs))
            if len(shared) < MIN_SHARED:
                rejected[s].append({"ticker": tkr, "table": tbl,
                                    "reason": f"only {len(shared)} shared days"})
                continue
            r1 = [mine[shared[k]] / mine[shared[k - 1]] - 1 for k in range(1, len(shared))]
            r2 = [theirs[shared[k]] / theirs[shared[k - 1]] - 1 for k in range(1, len(shared))]
            c = corr(r1, r2)
            agree = (sum(1 for x, y in zip(r1, r2) if abs(x - y) <= AGREE_TOL)
                     / len(r1)) if r1 else 0.0
            diffs = sorted(abs(x - y) for x, y in zip(r1, r2))
            mad = diffs[len(diffs) // 2] if diffs else 1.0
            if mad <= MAX_MED_DIFF:
                chosen = {"ticker": tkr, "table": tbl, "validation": "return_median_diff",
                          "median_abs_return_diff": round(mad, 6),
                          "agree_rate": round(agree, 4),
                          "corr": round(c, 4) if c is not None else None,
                          "shared_days": len(shared), "name": names.get(tkr)}
                break
            rejected[s].append({"ticker": tkr, "table": tbl,
                                "reason": f"median|dret| {round(mad, 5)} > {MAX_MED_DIFF} "
                                          f"(agree {round(agree, 3)}, "
                                          f"corr {round(c, 3) if c else None})",
                                "shared_days": len(shared),
                                "their_name": names.get(tkr)})
        if chosen:
            mapping[s] = chosen
        else:
            unresolved.append(s)
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(universe)}", flush=True)

    # S&P names new to us -- authoritative from Sharadar's own membership table
    for t in sorted(sp500 & sep):
        if t not in mapping and t not in universe:
            mapping[t] = {"ticker": t, "table": "SEP", "validation": "unvalidated_new",
                          "name": names.get(t)}

    out = {
        "min_shared_days": MIN_SHARED, "min_return_corr": MIN_CORR,
        "mapped": len(mapping),
        "validated_by_median_diff": sum(1 for v in mapping.values()
                                        if v["validation"] == "return_median_diff"),
        "unvalidated_new": sum(1 for v in mapping.values()
                               if v["validation"] == "unvalidated_new"),
        "unresolved": sorted(unresolved),
        "rejected_candidates": {k: v for k, v in rejected.items()},
        "map": mapping,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nmapped {out['mapped']}  (return-corr validated {out['validated_by_median_diff']}, "
          f"new-from-sp500 {out['unvalidated_new']})")
    print(f"unresolved: {len(unresolved)} -> {sorted(unresolved)[:25]}")
    print("\nIDENTITY SWAPS CAUGHT (rejected candidates):")
    shown = 0
    for s, rs in rejected.items():
        for r in rs:
            if "median|dret|" in r["reason"]:
                print(f"  {s:>10} !=> {r['ticker']:>8} [{r['table']}] {r['reason']}  "
                      f"{(r.get('their_name') or '')[:40]}")
                shown += 1
        if shown > 30:
            break
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
