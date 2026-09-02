"""
Resolve the universe tickers that the Sharadar EQUITY (SEP) file appears not to contain.

Most are not actually missing -- they are reachable through one of four layers, and only
what survives all four genuinely needs a non-Sharadar fix:

  L1 funds        ETFs live in Sharadar's `funds`/SFP table, not `stocks`/SEP
                  (GLD, IWM, HYG, EEM ...).
  L2 punctuation  share classes differ by separator: ours MOG-A / PBR-A vs Sharadar
                  MOG.A / PBR.A. Also strip foreign venue suffixes (.TO, .V) which
                  Sharadar (US-only) will never carry under that form.
  L3 recycled     when a ticker string was reused, Sharadar suffixes the OLDER company
                  (CA -> CA1, EMC -> EMC1). Match via `relatedtickers` and by
                  base-name + digit suffix.
  L4 renamed      follow `actions` tickerchangefrom/tickerchangeto chains.

Read-only.  python map_missing_tickers.py
"""
import csv
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, ".cache", "sharadar")
OUT = os.path.join(DATA, "MISSING_TICKER_MAP.json")


def read_zip(name):
    z = zipfile.ZipFile(os.path.join(DATA, name))
    with z.open(z.namelist()[0]) as f:
        rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace")))
    z.close()
    return rows


def main():
    from database.models.core import Stock

    recon = json.load(open(os.path.join(DATA, "RECONCILIATION.json")))
    missing = sorted(set(recon["not_in_sharadar"]))

    tick = read_zip("tickers.csv.zip")
    sep = {r["ticker"].upper(): r for r in tick if r["table"] == "SEP"}
    sfp = {r["ticker"].upper(): r for r in tick if r["table"] == "SFP"}

    # relatedtickers -> canonical sharadar ticker
    rel = defaultdict(set)
    for r in tick:
        for other in (r.get("relatedtickers") or "").split():
            rel[other.upper()].add((r["ticker"].upper(), r["table"]))

    # base name -> suffixed variants (CA -> CA1, CA2 ...)
    bybase = defaultdict(list)
    for t in sep:
        b = re.sub(r"\d+$", "", t)
        if b != t:
            bybase[b].append(t)

    funds_zip = os.path.join(DATA, "funds.csv.zip")
    have_funds_prices = os.path.exists(funds_zip)

    chains = defaultdict(set)
    for r in read_zip("actions.csv.zip"):
        if r["action"] in ("tickerchangefrom", "tickerchangeto"):
            a, b = (r.get("ticker") or "").upper(), (r.get("contraticker") or "").upper()
            if a and b:
                chains[a].add(b)
                chains[b].add(a)

    res = {}
    for t in missing:
        entry = {"layers": []}
        # L1 fund
        if t in sfp:
            entry["layers"].append("L1_funds")
            entry["sharadar_ticker"] = t
            entry["sharadar_table"] = "SFP"
            entry["name"] = sfp[t].get("name")
        # L2 punctuation / venue
        if "sharadar_ticker" not in entry:
            cands = {t.replace("-", "."), t.replace(".", "-"),
                     re.sub(r"\.(TO|V|L)$", "", t)}
            for c in cands - {t}:
                if c in sep or c in sfp:
                    entry["layers"].append("L2_punctuation")
                    entry["sharadar_ticker"] = c
                    entry["sharadar_table"] = "SEP" if c in sep else "SFP"
                    entry["name"] = (sep.get(c) or sfp.get(c)).get("name")
                    break
        # L3 recycled / relatedtickers
        if "sharadar_ticker" not in entry:
            if t in rel:
                cand = sorted(rel[t])[0]
                entry["layers"].append("L3_relatedtickers")
                entry["sharadar_ticker"] = cand[0]
                entry["sharadar_table"] = cand[1]
                entry["name"] = (sep.get(cand[0]) or sfp.get(cand[0]) or {}).get("name")
            elif bybase.get(t):
                cand = sorted(bybase[t])[0]
                entry["layers"].append("L3_suffix")
                entry["sharadar_ticker"] = cand
                entry["sharadar_table"] = "SEP"
                entry["name"] = sep[cand].get("name")
        # L4 rename chain
        if "sharadar_ticker" not in entry and t in chains:
            for c in sorted(chains[t]):
                if c in sep or c in sfp:
                    entry["layers"].append("L4_rename_chain")
                    entry["sharadar_ticker"] = c
                    entry["sharadar_table"] = "SEP" if c in sep else "SFP"
                    entry["name"] = (sep.get(c) or sfp.get(c)).get("name")
                    break
        if "sharadar_ticker" not in entry:
            entry["layers"].append("UNRESOLVED")
        res[t] = entry

    counts = defaultdict(int)
    for t, e in res.items():
        counts[e["layers"][0]] += 1
    unresolved = sorted([t for t, e in res.items() if e["layers"][0] == "UNRESOLVED"])

    out = {
        "missing_from_SEP": len(missing),
        "funds_file_present": have_funds_prices,
        "resolved_by_layer": dict(counts),
        "unresolved": unresolved,
        "map": res,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "map"}, indent=2))
    print("\nresolved examples:")
    for t, e in list(res.items()):
        if e["layers"][0] != "UNRESOLVED":
            print(f"  {t:>10} -> {e['sharadar_ticker']:>10} [{e['sharadar_table']}] "
                  f"via {e['layers'][0]}  {(e.get('name') or '')[:38]}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
