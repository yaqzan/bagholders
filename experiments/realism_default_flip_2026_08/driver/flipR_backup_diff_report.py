"""
Round-8 (resumed) backup-vs-live scores diff -- DIFF COMPUTATION.

Consumes the four extraction CSVs (backup_main/control.csv from
flipR_backup_diff.py; live_main/control_ge70.csv from flipR_live_pull.py
--stage broad) and computes, per window:
  - additions  = live-present, backup-absent   (direct, no further query)
  - removal candidates = backup-present, live-absent-from-ge70-set
                          (needs a targeted live lookup to classify
                          DELETED vs MUTATED -- that's a separate stage)

--mode candidates: writes out/removal_candidates.csv (symbol,date,window,
    backup_overall) for both windows combined, for the targeted queue stage.
    Also writes out/additions_raw.csv (symbol,date,window,live_overall).

--mode final: after out/live_targeted_raw.csv exists (from the targeted
    queued stage), produces the full report: removals classified
    deleted/mutated, zone split, ledger, and (for additions) enriches with
    stocks.created_at/delisted_date for the MNST / Jul-29-family / other
    pattern check. Writes out/round8_removals.csv, out/round8_additions.csv,
    out/round8_report_summary.json.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.dirname(HERE)
OUT_DIR = os.path.join(EXP_DIR, "out")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WINDOWS = {
    "main": ("2021-01-01", "2026-04-24"),
    "control": ("2026-05-01", "2026-08-01"),
}

# Zone cuts within the main window, per the round-8 brief.
ZONE_2021 = ("2021-01-01", "2021-12-31")
ZONE_22NOW = ("2022-01-01", "2026-04-24")
ZONE_TAIL = ("2026-04-16", "2026-04-24")  # subset of ZONE_22NOW, reported separately


def load_csv(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            key = (row["symbol"], row["date"])
            rows[key] = row
    return rows


def in_zone(date_str, zone):
    return zone[0] <= date_str <= zone[1]


def mode_candidates():
    all_removal_candidates = []
    all_additions = []
    counts = {}
    for wname in WINDOWS:
        backup = load_csv(os.path.join(OUT_DIR, f"backup_{wname}.csv"))
        live = load_csv(os.path.join(OUT_DIR, f"live_{wname}_ge70.csv"))
        backup_keys = set(backup.keys())
        live_keys = set(live.keys())
        removal_cands = backup_keys - live_keys
        additions = live_keys - backup_keys
        counts[wname] = {
            "backup_ge70_rows": len(backup_keys),
            "live_ge70_rows": len(live_keys),
            "removal_candidates": len(removal_cands),
            "additions": len(additions),
        }
        for (sym, dt) in removal_cands:
            all_removal_candidates.append({
                "symbol": sym, "date": dt, "window": wname,
                "backup_overall": backup[(sym, dt)]["overall"],
                "backup_updated_at": backup[(sym, dt)]["updated_at"],
            })
        for (sym, dt) in additions:
            all_additions.append({
                "symbol": sym, "date": dt, "window": wname,
                "live_overall": live[(sym, dt)]["overall"],
                "live_updated_at": live[(sym, dt)]["updated_at"],
            })

    cand_path = os.path.join(OUT_DIR, "removal_candidates.csv")
    with open(cand_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "window", "backup_overall", "backup_updated_at"])
        w.writeheader()
        for r in sorted(all_removal_candidates, key=lambda x: (x["window"], x["symbol"], x["date"])):
            w.writerow(r)
    print(f"wrote {cand_path} ({len(all_removal_candidates)} candidate rows)")

    add_path = os.path.join(OUT_DIR, "additions_raw.csv")
    with open(add_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "window", "live_overall", "live_updated_at"])
        w.writeheader()
        for r in sorted(all_additions, key=lambda x: (x["window"], x["symbol"], x["date"])):
            w.writerow(r)
    print(f"wrote {add_path} ({len(all_additions)} addition rows)")

    print(json.dumps(counts, indent=2))
    # Also a de-duped candidate-keys-only file for the targeted live pull stage
    # (dedupe across windows in case a key somehow appears in both, though the
    # windows are disjoint date ranges so this shouldn't happen).
    dedup_path = os.path.join(OUT_DIR, "removal_candidates_keys_only.csv")
    seen = set()
    with open(dedup_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date"])
        for r in all_removal_candidates:
            k = (r["symbol"], r["date"])
            if k not in seen:
                seen.add(k)
                w.writerow([r["symbol"], r["date"]])
    print(f"wrote {dedup_path} ({len(seen)} distinct keys)")


def mode_final():
    targeted_path = os.path.join(OUT_DIR, "live_targeted_raw.csv")
    if not os.path.exists(targeted_path):
        print(f"FATAL: {targeted_path} not found -- run the targeted queue stage first.")
        sys.exit(1)
    targeted = {}
    with open(targeted_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            targeted[(row["symbol"], row["date"])] = row

    cand_path = os.path.join(OUT_DIR, "removal_candidates.csv")
    removals = []
    with open(cand_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            key = (row["symbol"], row["date"])
            live_row = targeted.get(key)
            if live_row is None:
                status = "UNKNOWN_NOT_QUERIED"
                live_overall = ""
                live_updated_at = ""
            elif live_row["found"] == "NO":
                status = "DELETED"
                live_overall = ""
                live_updated_at = ""
            else:
                status = "MUTATED"
                live_overall = live_row["overall"]
                live_updated_at = live_row["updated_at"]
            date_str = row["date"]
            zones = []
            if in_zone(date_str, ZONE_2021):
                zones.append("2021")
            if in_zone(date_str, ZONE_22NOW):
                zones.append("22-now")
            if in_zone(date_str, ZONE_TAIL):
                zones.append("tail(04-16..24)")
            removals.append({
                "symbol": row["symbol"],
                "date": date_str,
                "window": row["window"],
                "backup_overall": row["backup_overall"],
                "backup_updated_at": row["backup_updated_at"],
                "status": status,
                "live_overall": live_overall,
                "live_updated_at": live_updated_at,
                "zones": "|".join(zones),
            })

    out_removals_path = os.path.join(OUT_DIR, "round8_removals.csv")
    with open(out_removals_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["symbol", "date", "window", "backup_overall", "backup_updated_at",
                      "status", "live_overall", "live_updated_at", "zones"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(removals)
    print(f"wrote {out_removals_path} ({len(removals)} rows)")

    # Additions: enrich with stocks.created_at / delisted_date for pattern check.
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    add_path = os.path.join(OUT_DIR, "additions_raw.csv")
    additions = []
    with open(add_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            additions.append(row)
    distinct_syms = sorted({a["symbol"] for a in additions})
    stock_info = {}
    for sym in distinct_syms:
        cur = DB.execute_sql(
            "SELECT created_at, delisted_date FROM stocks WHERE symbol=%s", (sym,)
        )
        r = cur.fetchone()
        if r:
            stock_info[sym] = {"created_at": str(r[0]) if r[0] else "", "delisted_date": str(r[1]) if r[1] else ""}
        else:
            stock_info[sym] = {"created_at": "NOT_FOUND", "delisted_date": ""}

    out_additions_path = os.path.join(OUT_DIR, "round8_additions.csv")
    with open(out_additions_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["symbol", "date", "window", "live_overall", "live_updated_at",
                      "stock_created_at", "stock_delisted_date", "tag"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for a in additions:
            info = stock_info.get(a["symbol"], {"created_at": "", "delisted_date": ""})
            tag_parts = []
            if a["symbol"] == "MNST":
                tag_parts.append("MNST")
            if info["created_at"].startswith("2026-07-29"):
                tag_parts.append("created_at=07-29")
            if not tag_parts:
                tag_parts.append("OTHER")
            w.writerow({
                "symbol": a["symbol"], "date": a["date"], "window": a["window"],
                "live_overall": a["live_overall"], "live_updated_at": a["live_updated_at"],
                "stock_created_at": info["created_at"], "stock_delisted_date": info["delisted_date"],
                "tag": "|".join(tag_parts),
            })
    print(f"wrote {out_additions_path} ({len(additions)} rows)")

    # Zone ledger + summary
    summary = {"windows": {}}
    for wname in WINDOWS:
        w_removals = [r for r in removals if r["window"] == wname]
        w_additions = [a for a in additions if a["window"] == wname]
        deleted = [r for r in w_removals if r["status"] == "DELETED"]
        mutated = [r for r in w_removals if r["status"] == "MUTATED"]
        summary["windows"][wname] = {
            "removals_total": len(w_removals),
            "removals_deleted": len(deleted),
            "removals_mutated": len(mutated),
            "additions_total": len(w_additions),
            "additions_minus_removals": len(w_additions) - len(w_removals),
        }
    # zone breakdown for main window only
    main_removals = [r for r in removals if r["window"] == "main"]
    summary["main_window_zone_breakdown"] = {
        "2021": sum(1 for r in main_removals if "2021" in r["zones"]),
        "22-now": sum(1 for r in main_removals if "22-now" in r["zones"]),
        "tail(04-16..24)_subset_of_22-now": sum(1 for r in main_removals if "tail(04-16..24)" in r["zones"]),
    }
    # additions pattern tags
    add_tags = {}
    for a in additions:
        pass  # tag computed above only in the csv-writing loop; recompute here for summary
    tag_counts = {}
    with open(out_additions_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            t = row["tag"]
            tag_counts[t] = tag_counts.get(t, 0) + 1
    summary["additions_tag_counts"] = tag_counts

    summary_path = os.path.join(OUT_DIR, "round8_report_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {summary_path}")
    print(json.dumps(summary, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["candidates", "final"])
    args = ap.parse_args()
    if args.mode == "candidates":
        mode_candidates()
    else:
        mode_final()


if __name__ == "__main__":
    main()
