"""
Re-source ONE symbol's price_history.close_unadj from the Sharadar archive after a
split-wipe casualty. Bounded UPDATE of close_unadj only, for existing rows, never
inside the live fresh window (today - _UNADJ_FRESH_DAYS - 3 onward stays untouched --
that lifecycle belongs to the daily pipeline).

WHY: check_and_apply_splits used to wipe a symbol's entire price_history on an
unreflected split and re-pull yfinance-only, destroying the vendor as-traded series
(yfinance has no as-traded feed). Fixed 2026-08-18 (snapshot + verbatim restore +
split floor in the fresh-bar heuristic), but wipes that happened between the 07-29
rebuild and the fix lost their history: MNST (2:1 split 2026-08-11, wiped 10:00:04,
10,096 pre-window rows NULLed) is the measured casualty. This tool re-fills from
`.cache/sharadar/stocks.csv.zip` / `funds.csv.zip` -- the same source of truth the
rebuild used.

Identity is sanity-checked before writing (median |stored-close return - vendor
closeadj return| over the most recent 60 matched days must be < 2%); both series are
fully adjusted on their own basis, so returns match for the same company regardless
of the split.

--insert-missing additionally INSERTS full bars for vendor dates absent from
price_history (before the fresh-window cutoff): OHLC scaled by closeadj/close into
the adjusted convention (the P2.A rebuild formula), close=closeadj,
close_unadj=closeunadj, volume as-traded. Needed for BYND: Yahoo dropped 08-12/08-13
outright around its reverse split, so those bars exist only in the Sharadar archive.
Weekly aggregates for inserted weeks are refreshed via the standard helper.

  python repair_split_wiped_unadj.py --symbol MNST            # dry-run
  python repair_split_wiped_unadj.py --symbol MNST --apply
  python repair_split_wiped_unadj.py --symbol BYND --insert-missing --apply
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "REPAIR_SPLIT_WIPED_REPORT.json")

EQUAL_TOL = 0.005
IDENTITY_MED_RET_TOL = 0.02
CSV_CHUNK = 2_000_000
# Never write into the live fresh lifecycle. A bar is live-supplied by the daily
# pipeline iff (today - d) <= _UNADJ_FRESH_DAYS (= 5, trader.py); the exclusive
# cutoff today-5 therefore admits exactly the strictly-stale bars (d <= today-6).
# Was 8 (+3 pad) -- the pad cost three days of repair lag for zero extra safety.
FRESH_GUARD_DAYS = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--insert-missing", action="store_true", dest="insert_missing")
    ap.add_argument("--apply", "--commit", action="store_true", dest="apply")
    a = ap.parse_args()
    sym = a.symbol.upper()

    import pandas as pd
    from database.trader_database import DB

    smap = json.load(open(os.path.join(DATA, "SYMBOL_MAP.json")))["map"]
    m = smap.get(sym)
    if not m:
        sys.exit(f"{sym} has no Sharadar mapping (SYMBOL_MAP.json) -- nothing to re-source")
    ticker, table = m["ticker"], m["table"]
    zname = "stocks.csv.zip" if table == "SEP" else "funds.csv.zip"
    cutoff = date.today() - timedelta(days=FRESH_GUARD_DAYS)

    stored = {}
    cur = DB.execute_sql(
        "SELECT `date`,`close`,`close_unadj` FROM price_history "
        "WHERE `symbol`=%s AND `date` < %s", (sym, cutoff))
    for d, c, cu in cur.fetchall():
        stored[d] = (float(c), float(cu) if cu is not None else None)
    print(f"{sym} ({table}:{ticker}): {len(stored):,} stored rows before {cutoff}")

    vendor = {}
    vendor_full = {}
    cols = ["ticker", "date", "closeadj", "closeunadj"]
    if a.insert_missing:
        cols += ["open", "high", "low", "close", "volume"]
    t0 = time.time()
    for chunk in pd.read_csv(os.path.join(DATA, zname), usecols=cols,
                             dtype={"ticker": str, "date": str}, chunksize=CSV_CHUNK):
        sub = chunk[chunk["ticker"] == ticker]
        for r in sub.itertuples(index=False):
            cu = None if r.closeunadj != r.closeunadj else float(r.closeunadj)
            ca = None if r.closeadj != r.closeadj else float(r.closeadj)
            if cu is not None:
                vendor[date.fromisoformat(r.date)] = (ca, cu)
                if a.insert_missing:
                    vendor_full[date.fromisoformat(r.date)] = r
    print(f"vendor: {len(vendor):,} as-traded closes ({time.time() - t0:.0f}s scan)")

    common = sorted(d for d in stored if d in vendor and vendor[d][0])
    recent = common[-60:]
    rets = []
    for i in range(1, len(recent)):
        s0, v0 = stored[recent[i - 1]][0], vendor[recent[i - 1]][0]
        s1, v1 = stored[recent[i]][0], vendor[recent[i]][0]
        if s0 > 0 and v0 > 0:
            rets.append(abs(s1 / s0 - v1 / v0))
    rets.sort()
    med = rets[len(rets) // 2] if rets else None
    print(f"identity: median return divergence over last {len(rets)} matched days = {med}")
    if med is None or med > IDENTITY_MED_RET_TOL:
        sys.exit("IDENTITY CHECK FAILED -- refusing to write")

    fills, overwrites, equal = [], [], 0
    for d in common:
        cu_vendor = vendor[d][1]
        cu_stored = stored[d][1]
        if cu_stored is None:
            fills.append((d, cu_vendor))
        elif abs(cu_stored - cu_vendor) <= EQUAL_TOL:
            equal += 1
        else:
            overwrites.append((d, cu_vendor))
    no_vendor = len(stored) - len(common)
    print(f"classified: fill={len(fills):,} overwrite={len(overwrites):,} "
          f"equal={equal:,} no_vendor_rows={no_vendor:,}")
    if overwrites[:5]:
        for d, v in overwrites[:5]:
            print(f"  overwrite sample {d}: stored={stored[d][1]} -> vendor={v}")

    # bars the vendor has but price_history lacks entirely (e.g. Yahoo dropped
    # 08-12/08-13 around BYND's reverse split) -- full-row inserts, P2.A scaling
    inserts = []
    if a.insert_missing:
        for d, r in sorted(vendor_full.items()):
            if d >= cutoff or d in stored:
                continue
            c = None if r.close != r.close else float(r.close)
            ca, cu = vendor[d]
            if not c or not ca or c <= 0:
                continue
            f = ca / c
            o = None if r.open != r.open else float(r.open) * f
            h = None if r.high != r.high else float(r.high) * f
            lo = None if r.low != r.low else float(r.low) * f
            v = 0 if r.volume != r.volume else int(r.volume)
            inserts.append((d, o, h, lo, ca, cu, v))
        print(f"missing bars to insert: {len(inserts)}"
              + (f" ({inserts[0][0]}..{inserts[-1][0]})" if inserts else ""))

    rep = {}
    if os.path.exists(REPORT):
        try:
            rep = json.load(open(REPORT))
        except Exception:
            rep = {}
    rep[sym] = {"mode": "apply" if a.apply else "dry-run", "run_date": str(date.today()),
                "cutoff": str(cutoff), "fill": len(fills), "overwrite": len(overwrites),
                "equal": equal, "no_vendor_rows": no_vendor,
                "inserted": len(inserts) if a.insert_missing else None,
                "identity_median_ret_divergence": med}

    if a.apply:
        todo = fills + overwrites
        n = 0
        CH = 500
        for i in range(0, len(todo), CH):
            chunk = todo[i:i + CH]
            case = " ".join(["WHEN %s THEN %s"] * len(chunk))
            marks = ",".join(["%s"] * len(chunk))
            params = [p for d, v in chunk for p in (d, v)]
            params += [sym] + [d for d, _ in chunk]
            with DB.atomic():
                DB.execute_sql(
                    f"UPDATE price_history SET close_unadj = CASE `date` {case} "
                    f"ELSE close_unadj END WHERE `symbol`=%s AND `date` IN ({marks})",
                    tuple(params))
            n += len(chunk)
            print(f"  wrote {n:,}/{len(todo):,}", flush=True)
        if inserts:
            from datetime import datetime as _dt
            from database.models.technical import PriceHistory
            now = _dt.now()
            colspec = ("`symbol`,`date`,`open`,`high`,`low`,`close`,`close_unadj`,"
                       "`volume`,`pulled_at`")
            CHI = 500
            for i in range(0, len(inserts), CHI):
                chunk = inserts[i:i + CHI]
                marks = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
                params = []
                for d, o, h, lo, ca, cu, v in chunk:
                    params.extend((sym, d, o, h, lo, ca, cu, v, now))
                with DB.atomic():
                    DB.execute_sql(
                        f"INSERT INTO price_history ({colspec}) VALUES {marks}",
                        tuple(params))
            nw = PriceHistory._refresh_weekly_aggregates(
                sym, inserts[0][0], inserts[-1][0])
            print(f"inserted {len(inserts)} missing bars; weekly aggregates "
                  f"refreshed for their range ({nw})")
        cov = DB.execute_sql(
            "SELECT COUNT(*), SUM(close_unadj IS NOT NULL) FROM price_history "
            "WHERE `symbol`=%s AND `date` < %s", (sym, cutoff)).fetchone()
        pct = 100 * int(cov[1] or 0) / cov[0] if cov[0] else 0
        rep[sym]["post_coverage_pct"] = round(pct, 2)
        print(f"post-apply coverage before {cutoff}: {int(cov[1] or 0)}/{cov[0]} ({pct:.1f}%)")
    else:
        print("DRY-RUN -- nothing written.")

    with open(REPORT, "w") as f:
        json.dump(rep, f, indent=2)
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
