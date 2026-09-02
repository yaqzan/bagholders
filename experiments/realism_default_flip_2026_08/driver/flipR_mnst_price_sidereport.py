"""
Round-8-unfrozen side-report: for 3 mutated MNST score dates (2021-05-12,
2022-12-16, 2023-06-01), compare MNST's price_history CLOSE value between the
Aug-7 daily backup's price_history dump (D:\\Backups\\Trader\\daily\\20260807\\
price_history.sql.gz -- price_history IS in $IrreplaceableTables, unlike
scores) and the live table now. Report only -- no interpretation. A
values-differ result pins "retroactive adjusted-series drift" as the
mechanism; values-identical means the Jul-29-era scoring ran on different
data for some other reason.

Read-only on the backup file (stream-parsed, never restored/modified). No
queue needed (small file, 3 targeted rows) -- run directly.
"""
import csv
import gzip
import json
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

DUMP_PATH = r"D:\Backups\Trader\daily\20260807\price_history.sql.gz"
TARGET_SYMBOL = "MNST"
TARGET_DATES = {"2021-05-12", "2022-12-16", "2023-06-01"}

EXPECTED_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "pulled_at", "close_unadj"]


def _tee(msg, log_path):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_create_table_columns(lines_iter, table_name, log_path):
    cols = []
    in_create = False
    marker = f"CREATE TABLE `{table_name}`"
    for line in lines_iter:
        if not in_create:
            if line.startswith(marker):
                in_create = True
            continue
        stripped = line.strip()
        if stripped.startswith(") ENGINE") or stripped.startswith(")ENGINE"):
            break
        if stripped.startswith("`"):
            end = stripped.index("`", 1)
            colname = stripped[1:end]
            upper = stripped.upper()
            if upper.startswith("PRIMARY KEY") or upper.startswith("KEY ") or \
               upper.startswith("UNIQUE KEY") or upper.startswith("CONSTRAINT"):
                continue
            cols.append(colname)
    _tee(f"parsed CREATE TABLE {table_name} -> {len(cols)} columns: {cols}", log_path)
    return cols


def split_top_level_tuples(s):
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n or s[i] != "(":
            break
        start = i
        i += 1
        depth = 1
        in_str = None
        while i < n and depth > 0:
            c = s[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
                i += 1
            else:
                if c == "'" or c == '"':
                    in_str = c
                    i += 1
                elif c == "(":
                    depth += 1
                    i += 1
                elif c == ")":
                    depth -= 1
                    i += 1
                else:
                    i += 1
        yield (start, i)


def split_fields(tup):
    inner = tup[1:-1]
    fields = []
    i = 0
    n = len(inner)
    start = 0
    in_str = None
    while i < n:
        c = inner[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
        else:
            if c == "'" or c == '"':
                in_str = c
                i += 1
            elif c == ",":
                fields.append(inner[start:i])
                i += 1
                start = i
            else:
                i += 1
    fields.append(inner[start:i])
    return fields


def unquote(raw):
    raw = raw.strip()
    if raw == "NULL":
        return None
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    return raw


def cheap_symbol_date_ok(tup):
    """tup = "('SYM','YYYY-MM-DD', ...". Cheap check on fields 0,1 only."""
    if len(tup) < 4 or tup[1] != "'":
        return None
    j = 2
    while j < len(tup):
        if tup[j] == "\\":
            j += 2
            continue
        if tup[j] == "'":
            break
        j += 1
    sym = tup[2:j]
    if sym != TARGET_SYMBOL:
        return None
    k = j + 1
    if k >= len(tup) or tup[k] != ",":
        return None
    k += 1
    if k >= len(tup) or tup[k] != "'":
        return None
    m = k + 1
    while m < len(tup):
        if tup[m] == "\\":
            m += 2
            continue
        if tup[m] == "'":
            break
        m += 1
    date_val = tup[k + 1:m]
    if date_val not in TARGET_DATES:
        return None
    return date_val


def main():
    log_path = os.path.join(LOG_DIR, "flipR_mnst_price_sidereport.log")
    open(log_path, "a", encoding="utf-8").close()
    t0 = time.time()
    _tee(f"START MNST price_history side-report. backup={DUMP_PATH}", log_path)
    if not os.path.exists(DUMP_PATH):
        _tee(f"FATAL: backup path does not exist: {DUMP_PATH}", log_path)
        sys.exit(1)
    sz = os.path.getsize(DUMP_PATH)
    _tee(f"backup compressed size = {sz} bytes ({sz/1e6:.1f} MB)", log_path)

    backup_rows = {}
    cols = None
    col_index = {}
    saw_create = False
    saw_lock = False
    line_no = 0
    tuples_scanned = 0
    insert_lines = 0

    with gzip.open(DUMP_PATH, mode="rt", encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            line_no += 1
            if line.startswith("CREATE TABLE `price_history`"):
                saw_create = True
                def _gen(first_line):
                    yield first_line
                    for l in f:
                        yield l
                cols = parse_create_table_columns(_gen(line), "price_history", log_path)
                break
        if not saw_create:
            _tee("FATAL: never found CREATE TABLE `price_history`", log_path)
            sys.exit(2)
        for i, name in enumerate(cols):
            col_index[name] = i
        if cols != EXPECTED_COLS:
            _tee(f"NOTE: dump column order differs from expected: dump={cols} expected={EXPECTED_COLS}", log_path)
        idx_symbol = col_index.get("symbol")
        idx_date = col_index.get("date")
        idx_close = col_index.get("close")
        idx_close_unadj = col_index.get("close_unadj")
        idx_pulled_at = col_index.get("pulled_at")
        idx_open = col_index.get("open")
        idx_volume = col_index.get("volume")

        for line in f:
            line_no += 1
            if not saw_lock:
                if line.startswith("LOCK TABLES `price_history` WRITE"):
                    saw_lock = True
                    _tee(f"found LOCK TABLES price_history WRITE at dump line {line_no}", log_path)
                continue
            if line.startswith("UNLOCK TABLES"):
                _tee(f"found UNLOCK TABLES (end of price_history block) at dump line {line_no}", log_path)
                break
            if not line.startswith("INSERT INTO `price_history`"):
                continue
            insert_lines += 1
            vpos = line.find(" VALUES ")
            if vpos == -1:
                continue
            payload = line[vpos + len(" VALUES "):]
            if payload.endswith(";\n"):
                payload = payload[:-2]
            elif payload.endswith(";"):
                payload = payload[:-1]
            for (s0, s1) in split_top_level_tuples(payload):
                tuples_scanned += 1
                tup = payload[s0:s1]
                date_val = cheap_symbol_date_ok(tup)
                if date_val is None:
                    continue
                fields = split_fields(tup)
                if len(fields) != len(cols):
                    _tee(f"WARN: field count mismatch at line {line_no}: {tup[:80]!r}", log_path)
                    continue
                backup_rows[date_val] = {
                    "close": unquote(fields[idx_close]),
                    "close_unadj": unquote(fields[idx_close_unadj]) if idx_close_unadj is not None else None,
                    "open": unquote(fields[idx_open]) if idx_open is not None else None,
                    "volume": unquote(fields[idx_volume]) if idx_volume is not None else None,
                    "pulled_at": unquote(fields[idx_pulled_at]) if idx_pulled_at is not None else None,
                }
                if len(backup_rows) == len(TARGET_DATES):
                    _tee(f"all {len(TARGET_DATES)} target dates found -- stopping scan early "
                        f"at dump line {line_no}", log_path)
                    break
            if len(backup_rows) == len(TARGET_DATES):
                break

    elapsed = time.time() - t0
    _tee(f"backup scan done: insert_lines={insert_lines} tuples_scanned={tuples_scanned} "
        f"found={len(backup_rows)}/{len(TARGET_DATES)} elapsed={elapsed:.1f}s", log_path)
    for d in sorted(TARGET_DATES):
        if d not in backup_rows:
            _tee(f"WARNING: target date {d} NOT FOUND in backup dump for {TARGET_SYMBOL}", log_path)

    # Live side
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    live_rows = {}
    for d in sorted(TARGET_DATES):
        cur = DB.execute_sql(
            "SELECT close, close_unadj, open, volume, pulled_at FROM price_history "
            "WHERE symbol=%s AND date=%s", (TARGET_SYMBOL, d))
        row = cur.fetchone()
        if row is None:
            live_rows[d] = None
            _tee(f"[LIVE] {TARGET_SYMBOL} {d}: NO ROW FOUND", log_path)
        else:
            live_rows[d] = {
                "close": str(row[0]), "close_unadj": str(row[1]) if row[1] is not None else None,
                "open": str(row[2]), "volume": row[3], "pulled_at": str(row[4]) if row[4] is not None else None,
            }
            _tee(f"[LIVE] {TARGET_SYMBOL} {d}: close={row[0]} close_unadj={row[1]} open={row[2]} "
                f"volume={row[3]} pulled_at={row[4]}", log_path)

    # Compare
    report_rows = []
    for d in sorted(TARGET_DATES):
        b = backup_rows.get(d)
        l = live_rows.get(d)
        b_close = b["close"] if b else None
        l_close = l["close"] if l else None
        try:
            differ = (b_close is None) != (l_close is None) or (
                b_close is not None and l_close is not None and float(b_close) != float(l_close)
            )
        except (TypeError, ValueError):
            differ = (b_close != l_close)
        _tee(f"[COMPARE] {TARGET_SYMBOL} {d}: backup_close={b_close} live_close={l_close} "
            f"DIFFER={differ} | backup_pulled_at={b['pulled_at'] if b else None} "
            f"live_pulled_at={l['pulled_at'] if l else None}", log_path)
        report_rows.append({
            "symbol": TARGET_SYMBOL, "date": d,
            "backup_close": b_close, "live_close": l_close, "close_differs": differ,
            "backup_close_unadj": b["close_unadj"] if b else None,
            "live_close_unadj": l["close_unadj"] if l else None,
            "backup_open": b["open"] if b else None, "live_open": l["open"] if l else None,
            "backup_volume": b["volume"] if b else None, "live_volume": l["volume"] if l else None,
            "backup_pulled_at": b["pulled_at"] if b else None, "live_pulled_at": l["pulled_at"] if l else None,
        })

    out_csv = os.path.join(OUT_DIR, "mnst_price_sidereport.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(report_rows[0].keys()))
        w.writeheader()
        w.writerows(report_rows)
    _tee(f"wrote {out_csv} ({len(report_rows)} rows)", log_path)

    any_differ = any(r["close_differs"] for r in report_rows)
    _tee(f"SUMMARY: any_close_differs={any_differ}", log_path)
    _tee("SUCCESS", log_path)


if __name__ == "__main__":
    main()
