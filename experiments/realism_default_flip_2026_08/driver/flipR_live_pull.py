"""
Round-8 (resumed) backup-vs-live scores diff -- LIVE-SIDE EXTRACTION.

Two stages, run as separate queued tasks (this file is the --db light payload
submitted via `trader queue submit`, per the round-8 brief: the backup-side
extraction is pure file work (flipR_backup_diff.py, no queue), but any MySQL
touch goes through the queue since backup #482's mysqldump holds the heavy slot).

--stage broad:
    SELECT symbol,date,overall,updated_at FROM scores WHERE version_id=74
    AND overall>=70 AND date in {main window OR control window}.
    Writes out/live_main_ge70.csv, out/live_control_ge70.csv.
    This is the full live >=70 population for both windows -- used directly
    for the ADDITIONS direction (live-present/backup-absent) and to identify
    REMOVAL CANDIDATES (backup>=70 keys not in this set).

--stage targeted --candidates <csv path with symbol,date columns>:
    For an exact candidate key list (e.g. removal candidates from the diff),
    pulls the RAW current value (any overall, or true absence) for each
    (symbol,date) at version_id=74, so removals can be classified
    DELETED vs MUTATED. Writes out/live_targeted_raw.csv.

Read-only SELECTs only. No writes to any table.
"""
import argparse
import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.dirname(HERE)
OUT_DIR = os.path.join(EXP_DIR, "out")
LOG_DIR = os.path.join(EXP_DIR, "logs")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TARGET_VERSION_ID = 74
MIN_OVERALL = 70
WINDOWS = {
    "main": ("2021-01-01", "2026-04-24"),
    "control": ("2026-05-01", "2026-08-01"),
}


def _tee(msg, log_path):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def stage_broad(log_path):
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    t0 = time.time()
    for wname, (lo, hi) in WINDOWS.items():
        _tee(f"broad pull window={wname} [{lo}..{hi}] version_id={TARGET_VERSION_ID} overall>={MIN_OVERALL}", log_path)
        sql = (
            "SELECT symbol, date, overall, updated_at FROM scores "
            "WHERE version_id=%s AND overall>=%s AND date BETWEEN %s AND %s "
            "ORDER BY symbol, date"
        )
        cur = DB.execute_sql(sql, (TARGET_VERSION_ID, MIN_OVERALL, lo, hi))
        rows = cur.fetchall()
        out_path = os.path.join(OUT_DIR, f"live_{wname}_ge70.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "overall", "updated_at"])
            for r in rows:
                w.writerow([r[0], str(r[1]), r[2], str(r[3]) if r[3] is not None else ""])
        _tee(f"wrote {out_path} ({len(rows)} rows)", log_path)
    _tee(f"stage=broad DONE elapsed={time.time()-t0:.1f}s", log_path)


def stage_targeted(candidates_path, log_path):
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    t0 = time.time()
    keys = []
    with open(candidates_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            keys.append((row["symbol"], row["date"]))
    _tee(f"targeted lookup: {len(keys)} candidate keys from {candidates_path}", log_path)
    out_path = os.path.join(OUT_DIR, "live_targeted_raw.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        w.writerow(["symbol", "date", "overall", "updated_at", "found"])
        for (sym, dt) in keys:
            sql = "SELECT overall, updated_at FROM scores WHERE version_id=%s AND symbol=%s AND date=%s"
            cur = DB.execute_sql(sql, (TARGET_VERSION_ID, sym, dt))
            row = cur.fetchone()
            if row is None:
                w.writerow([sym, dt, "", "", "NO"])
            else:
                w.writerow([sym, dt, row[0], str(row[1]) if row[1] is not None else "", "YES"])
    _tee(f"wrote {out_path}", log_path)
    _tee(f"stage=targeted DONE elapsed={time.time()-t0:.1f}s", log_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["broad", "targeted"])
    ap.add_argument("--candidates", default=None)
    args = ap.parse_args()
    log_path = os.path.join(LOG_DIR, "flipR_live_pull.log")
    open(log_path, "a", encoding="utf-8").close()
    _tee(f"START stage={args.stage}", log_path)
    if args.stage == "broad":
        stage_broad(log_path)
    else:
        if not args.candidates:
            _tee("FATAL: --candidates required for --stage targeted", log_path)
            sys.exit(1)
        stage_targeted(args.candidates, log_path)
    _tee("SUCCESS", log_path)


if __name__ == "__main__":
    main()
