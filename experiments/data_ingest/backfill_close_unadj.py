"""
Backfill price_history.close_unadj for the 2026-07-30 -> 2026-08-12 fresh-write hole
from the on-disk Sharadar archive. Bounded UPDATE of close_unadj ONLY -- never touches
close/OHLC/volume, never inserts or deletes rows, never writes outside the window.

WHY THE HOLE EXISTS (root-caused 2026-08-18, traps.md "An INSERT-ONLY column on a
multi-writer table"): the 2026-07-29 rebuild made close_unadj insert-only in
PriceHistory.bulk_build, but on normal trading days today's row is first CREATED by the
premarket 1m pass, which knows no close_unadj -- so the daily pull's value was dropped on
conflict forever (~1-2% coverage), and rows first-inserted by market-hours pulls froze
close_unadj at that moment's PARTIAL INTRADAY PRINT (populated-but-wrong; INTC 08-12
stored 102.57 vs 100.95 true close). bulk_build was fixed 2026-08-18 (COALESCE upsert);
2026-08-13+ is healthy and MUST NOT be touched (hard guard below).

CLASSIFICATION, per (symbol, date) row in the window:
  vendor as-traded close exists   -> FILL (stored NULL) / OVERWRITE (differs -- poison,
                                     vendor truth wins) / EQUAL (skip, already right)
  no vendor value, MAPPED symbol  -> PRESERVE stored non-NULL. Refined 2026-08-21: on
                                     the first run (2026-08-18) every unverifiable
                                     value was poison-era and got scrubbed; after that
                                     purge the only way a mapped symbol holds an
                                     in-window value is a prior vendor backfill, so a
                                     later archive dropping the ticker (EQR vanished
                                     whole from the 08-19 weekly export while trading
                                     normally) must not erase prior vendor truth.
  no vendor value, UNMAPPED       -> SCRUB stored non-NULL to NULL (foreign listings /
                                     identity failures, see repull_non_sharadar.py --
                                     never vendor-confirmable, honest NULL beats a
                                     frozen intraday print).

2026-08-12 is NOT in the current archive (stocks.csv.zip downloaded 2026-08-12 09:15,
vendor data through ~08-11), so its 378 poison rows are scrubbed now and re-filled by
RE-RUNNING THIS SCRIPT UNCHANGED after the next weekly TraderSharadarFinalTopup rewrites
the archive. The script is idempotent: already-correct rows classify EQUAL and skip.

IDENTITY VALIDATION (same approach as the P2.A rebuild's map): SYMBOL_MAP.json is
price-validated, but 3 weeks have passed -- so each symbol's stored `close` returns are
checked against vendor `closeadj` returns over the matched window dates (returns, not
levels: a dividend or split between the two pull dates rescales levels but not returns).
Median abs divergence > 2% quarantines the symbol: no writes at all, surfaced in the
report for a human.

  python backfill_close_unadj.py            # DRY-RUN: classify + report, write nothing
  python backfill_close_unadj.py --apply    # execute, then validate coverage + spot-check
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
REPORT = os.path.join(REPO, "experiments", "data_ingest", "BACKFILL_CLOSE_UNADJ_REPORT.json")

WINDOW_START = date(2026, 7, 30)
WINDOW_END = date(2026, 8, 12)
# 2026-08-13+ is healthy post-fix data written by the live pipeline. Never touch it.
HARD_CEILING = date(2026, 8, 13)

EQUAL_TOL = 0.005          # |stored - vendor| within half a cent = already correct
IDENTITY_MED_RET_TOL = 0.02
IDENTITY_MIN_RETURNS = 3
CSV_CHUNK = 2_000_000
WRITE_BATCH_SYMBOLS = 100  # symbols per transaction; each statement is single-symbol


def read_vendor_window(zip_path, wanted, start_s, end_s, label):
    """Stream one Sharadar bulk zip, keep (ticker, date, closeadj, closeunadj) rows
    inside the window for wanted tickers. Full pass -- the file is date-major but we
    do not rely on its ordering."""
    import pandas as pd
    out = []
    t0 = time.time()
    scanned = 0
    for chunk in pd.read_csv(zip_path, usecols=["ticker", "date", "closeadj", "closeunadj"],
                             dtype={"ticker": str, "date": str}, chunksize=CSV_CHUNK):
        scanned += len(chunk)
        m = (chunk["date"] >= start_s) & (chunk["date"] <= end_s)
        if m.any():
            sub = chunk[m]
            sub = sub[sub["ticker"].isin(wanted)]
            if len(sub):
                out.append(sub)
        print(f"  {label}: scanned {scanned:,} rows, kept "
              f"{sum(len(s) for s in out):,}  ({time.time() - t0:.0f}s)", flush=True)
    if not out:
        return []
    df = pd.concat(out, ignore_index=True)
    return list(df.itertuples(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", "--commit", action="store_true", dest="apply")
    a = ap.parse_args()

    from database.trader_database import DB

    if WINDOW_END >= HARD_CEILING:
        sys.exit("window end reaches healthy post-fix data (>= 2026-08-13) -- refusing")
    for f in ("stocks.csv.zip", "funds.csv.zip", "SYMBOL_MAP.json"):
        if not os.path.exists(os.path.join(DATA, f)):
            sys.exit(f"missing {os.path.join(DATA, f)}")

    smap = json.load(open(os.path.join(DATA, "SYMBOL_MAP.json")))["map"]
    start_s, end_s = str(WINDOW_START), str(WINDOW_END)

    # ---- stored rows in the window (date index serves this; ~10k rows) -------------
    stored = {}  # (symbol, date) -> [close, close_unadj]
    cur = DB.execute_sql(
        "SELECT `symbol`,`date`,`close`,`close_unadj` FROM price_history "
        "WHERE `date` BETWEEN %s AND %s", (start_s, end_s))
    for sym, d, c, cu in cur.fetchall():
        stored[(sym, d)] = [float(c), float(cu) if cu is not None else None]
    window_syms = sorted({s for s, _ in stored})
    dates_all = sorted({d for _, d in stored})
    print(f"window {start_s}..{end_s}: {len(stored):,} stored rows, "
          f"{len(window_syms)} symbols, {len(dates_all)} trading days", flush=True)

    # ---- vendor rows for mapped symbols ---------------------------------------------
    by_table = defaultdict(dict)  # table -> vendor_ticker -> our symbol
    unmapped = []
    for sym in window_syms:
        m = smap.get(sym)
        if m:
            by_table[m["table"]][m["ticker"]] = sym
        else:
            unmapped.append(sym)

    vendor = {}  # (symbol, date) -> (closeadj, closeunadj)
    for table, zname in (("SEP", "stocks.csv.zip"), ("SFP", "funds.csv.zip")):
        tmap = by_table.get(table)
        if not tmap:
            continue
        rows = read_vendor_window(os.path.join(DATA, zname), set(tmap), start_s, end_s, table)
        for r in rows:
            cu = None if r.closeunadj != r.closeunadj else float(r.closeunadj)  # NaN guard
            ca = None if r.closeadj != r.closeadj else float(r.closeadj)
            if cu is not None:
                vendor[(tmap[r.ticker], date.fromisoformat(r.date))] = (ca, cu)
    print(f"vendor: {len(vendor):,} usable as-traded closes for "
          f"{len({s for s, _ in vendor})} symbols", flush=True)

    # ---- identity check: stored-close returns vs vendor-closeadj returns ------------
    quarantined, unchecked = {}, []
    for sym in window_syms:
        if sym in unmapped:
            continue
        pairs = sorted((d, stored[(sym, d)][0], vendor[(sym, d)][0])
                       for d in dates_all
                       if (sym, d) in stored and (sym, d) in vendor
                       and vendor[(sym, d)][0])
        rets = []
        for i in range(1, len(pairs)):
            _, s0, v0 = pairs[i - 1]
            _, s1, v1 = pairs[i]
            if s0 > 0 and v0 > 0:
                rets.append(abs(s1 / s0 - v1 / v0))
        if len(rets) < IDENTITY_MIN_RETURNS:
            unchecked.append(sym)
            continue
        rets.sort()
        med = rets[len(rets) // 2]
        if med > IDENTITY_MED_RET_TOL:
            quarantined[sym] = round(med, 4)

    # ---- classify every stored row ---------------------------------------------------
    per_date = {d: defaultdict(int) for d in dates_all}
    fills = defaultdict(list)    # symbol -> [(date, value)]  (fill + overwrite)
    scrubs = defaultdict(list)   # symbol -> [date]
    preserved = set()            # mapped symbols kept despite vendor absence this run
    overwrite_samples = []
    for (sym, d), (close, cu_stored) in sorted(stored.items()):
        pd_ = per_date[d]
        pd_["rows"] += 1
        if sym in quarantined:
            pd_["quarantined"] += 1
            continue
        v = vendor.get((sym, d))
        if v is not None:
            cu_vendor = v[1]
            if cu_stored is None:
                pd_["fill"] += 1
                fills[sym].append((d, cu_vendor))
            elif abs(cu_stored - cu_vendor) <= EQUAL_TOL:
                pd_["equal"] += 1
            else:
                pd_["overwrite"] += 1
                fills[sym].append((d, cu_vendor))
                overwrite_samples.append(
                    {"symbol": sym, "date": str(d), "stored": cu_stored,
                     "vendor": cu_vendor, "diff": round(cu_stored - cu_vendor, 4)})
        else:
            if cu_stored is None:
                pd_["no_vendor" if sym not in unmapped else "unmapped_null"] += 1
            elif sym in unmapped:
                pd_["scrub"] += 1
                scrubs[sym].append(d)
            else:
                pd_["preserved"] += 1
                preserved.add(sym)

    overwrite_samples.sort(key=lambda r: -abs(r["diff"]))

    hdr = (f"{'date':<12}{'rows':>6}{'fill':>7}{'overwr':>8}{'equal':>7}{'presv':>7}"
           f"{'scrub':>7}{'no_vend':>9}{'unmap':>7}{'quar':>6}{'proj_cov':>10}")
    print("\n" + hdr + "\n" + "-" * len(hdr))
    for d in dates_all:
        c = per_date[d]
        covered = c["fill"] + c["overwrite"] + c["equal"] + c["preserved"]
        print(f"{str(d):<12}{c['rows']:>6}{c['fill']:>7}{c['overwrite']:>8}"
              f"{c['equal']:>7}{c['preserved']:>7}{c['scrub']:>7}{c['no_vendor']:>9}"
              f"{c['unmapped_null']:>7}{c['quarantined']:>6}"
              f"{100 * covered / c['rows']:>9.1f}%")

    n_fill = sum(v["fill"] for v in per_date.values())
    n_over = sum(v["overwrite"] for v in per_date.values())
    n_scrub = sum(v["scrub"] for v in per_date.values())
    n_pres = sum(v["preserved"] for v in per_date.values())
    print(f"\ntotals: fill={n_fill:,} overwrite={n_over:,} scrub={n_scrub:,} "
          f"preserved={n_pres:,} quarantined_syms={len(quarantined)} "
          f"unmapped_syms={len(unmapped)}")
    if preserved:
        print(f"preserved (mapped, vendor-absent this run, prior values kept): "
              f"{sorted(preserved)}")
    if quarantined:
        print(f"QUARANTINED (median return divergence, no writes): {quarantined}")
    if unmapped:
        print(f"unmapped (no Sharadar source, scrub-only): {unmapped}")

    out = {"mode": "apply" if a.apply else "dry-run",
           "window": [start_s, end_s],
           "per_date": {str(d): dict(per_date[d]) for d in dates_all},
           "totals": {"fill": n_fill, "overwrite": n_over, "scrub": n_scrub,
                      "preserved": n_pres},
           "preserved_symbols": sorted(preserved),
           "quarantined": quarantined,
           "identity_unchecked": unchecked,
           "unmapped": unmapped,
           "overwrite_samples_top": overwrite_samples[:25]}
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {REPORT}")
    if not a.apply:
        print("DRY-RUN -- nothing written.")
        return

    # ---- writes: single-symbol statements, batched transactions ---------------------
    t0 = time.time()
    n_written = 0
    syms = sorted(set(fills) | set(scrubs))
    for i in range(0, len(syms), WRITE_BATCH_SYMBOLS):
        with DB.atomic():
            for sym in syms[i:i + WRITE_BATCH_SYMBOLS]:
                if fills.get(sym):
                    pairs = fills[sym]
                    case = " ".join(["WHEN %s THEN %s"] * len(pairs))
                    marks = ",".join(["%s"] * len(pairs))
                    params = [p for d, v in pairs for p in (d, v)]
                    params += [sym] + [d for d, _ in pairs]
                    DB.execute_sql(
                        f"UPDATE price_history SET close_unadj = CASE `date` {case} "
                        f"ELSE close_unadj END WHERE `symbol`=%s AND `date` IN ({marks})",
                        tuple(params))
                    n_written += len(pairs)
                if scrubs.get(sym):
                    ds = scrubs[sym]
                    marks = ",".join(["%s"] * len(ds))
                    DB.execute_sql(
                        f"UPDATE price_history SET close_unadj = NULL "
                        f"WHERE `symbol`=%s AND `date` IN ({marks})",
                        tuple([sym] + ds))
                    n_written += len(ds)
        print(f"  wrote {min(i + WRITE_BATCH_SYMBOLS, len(syms))}/{len(syms)} symbols "
              f"({n_written:,} rows, {time.time() - t0:.0f}s)", flush=True)

    # ---- validation ------------------------------------------------------------------
    print("\npost-apply coverage:")
    cur = DB.execute_sql(
        "SELECT `date`, COUNT(*), SUM(`close_unadj` IS NOT NULL) FROM price_history "
        "WHERE `date` BETWEEN %s AND %s GROUP BY `date` ORDER BY `date`",
        (start_s, end_s))
    coverage = {}
    for d, n, c in cur.fetchall():
        coverage[str(d)] = round(100 * int(c or 0) / n, 2)
        print(f"  {d}  {int(c or 0)}/{n}  ({coverage[str(d)]:.1f}%)")

    spot = sorted(fills)[:: max(1, len(fills) // 9)][:9]
    if "INTC" in fills and "INTC" not in spot:
        spot = ["INTC"] + spot[:9]
    print("\nspot-check (close_unadj vs adjusted close on last vendor-covered date):")
    for sym in spot:
        d = max(d for d, _ in fills[sym])
        r = DB.execute_sql(
            "SELECT `close`, `close_unadj` FROM price_history "
            "WHERE `symbol`=%s AND `date`=%s", (sym, d)).fetchone()
        c, cu = float(r[0]), float(r[1]) if r[1] is not None else None
        note = "MATCH" if cu is not None and abs(c - cu) <= 0.02 else "differs (div/split since?)"
        print(f"  {sym:<8}{d}  close={c:<12.4f}close_unadj={cu:<12.4f}{note}")

    out["post_apply_coverage"] = coverage
    with open(REPORT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nupdated {REPORT}")


if __name__ == "__main__":
    main()
