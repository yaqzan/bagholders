"""
Round-8 (resumed) backup-vs-live scores diff -- BACKUP-SIDE EXTRACTION ONLY.

Stream-parses D:\\Backups\\Trader\\weekly\\20260802\\trader_full.sql.gz (a plain
mysqldump full-DB text dump, gzip-compressed) WITHOUT ever writing to it, restoring
it, or loading it into any database. Pure text streaming + a hand-rolled,
quote/paren-aware SQL-tuple scanner (needed because `scores.weight_info` is a TEXT
column that can contain commas/parens/quotes inside JSON, sitting BEFORE
`version_id` in column order -- a naive comma-split would misalign every field
after it).

Extracts scores rows for version_id=74 with overall>=70, in TWO date windows:
  main    = [2021-01-01, 2026-04-24]   (the round-8 investigation window)
  control = [2026-05-01, 2026-08-01]   (out-of-window recency control)

Output: out/backup_main.csv, out/backup_control.csv (symbol,date,overall,updated_at)
plus a small JSON summary (out/backup_diff_parse_summary.json) with row-scan counts.

Read-only on the backup file. No queue, no MySQL. Run directly:
    py -3.11 flipR_backup_diff.py
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

DUMP_PATH = r"D:\Backups\Trader\weekly\20260802\trader_full.sql.gz"

TARGET_VERSION_ID = 74
MIN_OVERALL = 70

WINDOWS = {
    "main": ("2021-01-01", "2026-04-24"),
    "control": ("2026-05-01", "2026-08-01"),
}

# Expected live column order (SHOW COLUMNS FROM scores, live DB, 2026-08-11) --
# used only as a cross-check; the authoritative order is parsed from the dump's
# own CREATE TABLE statement below.
EXPECTED_COLS = [
    "symbol", "date", "overall", "bb", "ma20", "trend", "volume", "rsi", "macd",
    "stoch", "technical_alignment", "high_30", "high_60", "high_90", "high_180",
    "high_360", "high_30_days", "high_60_days", "high_90_days", "high_180_days",
    "high_360_days", "high_30_biggest_drop", "high_60_biggest_drop",
    "high_90_biggest_drop", "updated_at", "daily_change", "price", "market_cap",
    "pe", "forward_pe", "price_target_growth", "next_earnings", "price_target",
    "name", "flagged", "growth_score", "volume_signal", "volume_magnitude",
    "weight_info", "version_id", "pct_from_ema50", "pct_from_ema200",
    "bb_position", "score_velocity_7d", "regime_composite", "regime_multiplier",
]


def _tee(msg, log_path):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_create_table_columns(lines_iter, log_path):
    """Consume lines from lines_iter until the CREATE TABLE `scores` block is
    fully read; return the column names in declared order."""
    cols = []
    in_scores_create = False
    for line in lines_iter:
        if not in_scores_create:
            if line.startswith("CREATE TABLE `scores`"):
                in_scores_create = True
            continue
        stripped = line.strip()
        if stripped.startswith(") ENGINE") or stripped.startswith(")ENGINE"):
            break
        # column def lines look like:  `symbol` varchar(10) NOT NULL,
        if stripped.startswith("`"):
            end = stripped.index("`", 1)
            colname = stripped[1:end]
            # Skip constraint/key lines that also start with a backtick-quoted
            # name but aren't column defs (PRIMARY KEY, KEY `...`, etc.) --
            # those are preceded by a keyword, not immediately after CREATE's
            # opening paren, but easiest robust check: skip lines containing
            # "KEY " before the backtick or lines that are literally KEY/PRIMARY.
            continue_ok = True
            upper = stripped.upper()
            if upper.startswith("PRIMARY KEY") or upper.startswith("KEY ") or \
               upper.startswith("UNIQUE KEY") or upper.startswith("CONSTRAINT"):
                continue_ok = False
            if continue_ok:
                cols.append(colname)
    _tee(f"parsed CREATE TABLE scores -> {len(cols)} columns", log_path)
    return cols


def split_top_level_tuples(s):
    """Yield (start,end) spans of each top-level (...) tuple in s, respecting
    quoted strings and backslash escapes (mysqldump default escaping)."""
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
    """tup includes surrounding parens. Return list of raw field strings
    (still quoted for string types), respecting quotes/escapes."""
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


def cheap_prefix_ok(tup, date_lo, date_hi_list):
    """Fast reject using only the first 3 fields (symbol, date, overall) --
    both always short/unquoted-safe -- without doing the full field split
    (which must walk through the weight_info TEXT blob). Returns the parsed
    (date_str, overall_int) if the row's date falls in ANY configured window
    and overall>=MIN_OVERALL, else None. date_hi_list is a list of (lo,hi)
    pairs to test against."""
    # tup = "('SYM','YYYY-MM-DD',NN, ..."  or with NULL date/overall
    # Find end of field 0 (symbol)
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
    sym_end = j  # index of closing quote of symbol
    k = sym_end + 1
    if k >= len(tup) or tup[k] != ",":
        return None
    k += 1
    if k >= len(tup):
        return None
    if tup[k] == "'":
        m = k + 1
        while m < len(tup):
            if tup[m] == "\\":
                m += 2
                continue
            if tup[m] == "'":
                break
            m += 1
        date_val = tup[k + 1:m]
        k = m + 1
    elif tup[k:k + 4] == "NULL":
        return None
    else:
        return None
    if k >= len(tup) or tup[k] != ",":
        return None
    k += 1
    # overall: signed int or NULL
    m2 = k
    while m2 < len(tup) and (tup[m2].isdigit() or tup[m2] == "-"):
        m2 += 1
    if m2 == k:
        return None  # NULL or malformed
    overall_str = tup[k:m2]
    try:
        overall_val = int(overall_str)
    except ValueError:
        return None
    if overall_val < MIN_OVERALL:
        return None
    for (lo, hi) in date_hi_list:
        if lo <= date_val <= hi:
            return (date_val, overall_val, sym_end)
    return None


def main():
    log_path = os.path.join(LOG_DIR, "flipR_backup_diff.log")
    open(log_path, "a", encoding="utf-8").close()
    t0 = time.time()
    _tee(f"START backup-side extraction. dump={DUMP_PATH}", log_path)
    if not os.path.exists(DUMP_PATH):
        _tee(f"FATAL: dump path does not exist: {DUMP_PATH}", log_path)
        sys.exit(1)
    sz = os.path.getsize(DUMP_PATH)
    _tee(f"dump compressed size = {sz} bytes ({sz/1e9:.3f} GB)", log_path)

    window_bounds = list(WINDOWS.values())

    rows_out = {name: [] for name in WINDOWS}

    scanned_tuples_total = 0
    scanned_tuples_in_scores = 0
    matched_total = 0
    cols = None
    col_index = {}
    in_scores_data = False
    saw_scores_create = False
    saw_scores_lock = False
    line_no = 0
    scores_insert_lines = 0
    last_progress = time.time()

    with gzip.open(DUMP_PATH, mode="rt", encoding="utf-8", errors="replace", newline="") as f:
        # Phase 1: find CREATE TABLE `scores` and parse its column order.
        for line in f:
            line_no += 1
            if line.startswith("CREATE TABLE `scores`"):
                saw_scores_create = True
                # re-consume the rest of the CREATE block via a small local
                # generator that starts with this line's remainder handled
                # by parse_create_table_columns's own loop (it expects the
                # CREATE line itself to have already been seen, so replay).
                def _gen(first_line):
                    yield first_line
                    for l in f:
                        yield l
                cols = parse_create_table_columns(_gen(line), log_path)
                break
        if not saw_scores_create:
            _tee("FATAL: never found 'CREATE TABLE `scores`' in dump -- aborting.", log_path)
            sys.exit(2)

        for i, name in enumerate(cols):
            col_index[name] = i
        if cols != EXPECTED_COLS:
            _tee("NOTE: dump's scores column order DIFFERS from live schema order.", log_path)
            _tee(f"  dump   : {cols}", log_path)
            _tee(f"  live   : {EXPECTED_COLS}", log_path)
        else:
            _tee("dump's scores column order MATCHES live schema exactly.", log_path)

        idx_symbol = col_index.get("symbol")
        idx_date = col_index.get("date")
        idx_overall = col_index.get("overall")
        idx_updated_at = col_index.get("updated_at")
        idx_version_id = col_index.get("version_id")
        _tee(f"column indices: symbol={idx_symbol} date={idx_date} overall={idx_overall} "
             f"updated_at={idx_updated_at} version_id={idx_version_id}", log_path)
        if None in (idx_symbol, idx_date, idx_overall, idx_updated_at, idx_version_id):
            _tee("FATAL: one or more required columns not found in parsed CREATE TABLE.", log_path)
            sys.exit(3)

        # Phase 2: scan forward for LOCK TABLES `scores` WRITE; then INSERT lines,
        # until UNLOCK TABLES; closes the scores block.
        for line in f:
            line_no += 1
            if not saw_scores_lock:
                if line.startswith("LOCK TABLES `scores` WRITE"):
                    saw_scores_lock = True
                    _tee(f"found LOCK TABLES `scores` WRITE at dump line {line_no}", log_path)
                continue
            # we're inside the scores data block
            if line.startswith("UNLOCK TABLES"):
                _tee(f"found UNLOCK TABLES (end of scores block) at dump line {line_no}", log_path)
                break
            if not line.startswith("INSERT INTO `scores`"):
                continue
            scores_insert_lines += 1
            # locate the VALUES payload
            vpos = line.find(" VALUES ")
            if vpos == -1:
                continue
            payload = line[vpos + len(" VALUES "):]
            if payload.endswith(";\n"):
                payload = payload[:-2]
            elif payload.endswith(";"):
                payload = payload[:-1]
            for (s0, s1) in split_top_level_tuples(payload):
                scanned_tuples_total += 1
                scanned_tuples_in_scores += 1
                tup = payload[s0:s1]
                pref = cheap_prefix_ok(tup, None, window_bounds)
                if pref is None:
                    continue
                date_val, overall_val, _sym_end = pref
                # full field split only for candidates that passed the cheap gate
                fields = split_fields(tup)
                if len(fields) != len(cols):
                    _tee(f"WARN: field count mismatch ({len(fields)} vs {len(cols)}) "
                         f"at dump line {line_no}, tuple starting {tup[:60]!r}", log_path)
                    continue
                try:
                    version_val = int(fields[idx_version_id])
                except (ValueError, TypeError):
                    continue
                if version_val != TARGET_VERSION_ID:
                    continue
                symbol_val = unquote(fields[idx_symbol])
                updated_at_val = unquote(fields[idx_updated_at])
                matched_total += 1
                for wname, (lo, hi) in WINDOWS.items():
                    if lo <= date_val <= hi:
                        rows_out[wname].append((symbol_val, date_val, overall_val, updated_at_val))
            if time.time() - last_progress > 15:
                elapsed = time.time() - t0
                _tee(f"...progress: dump_line={line_no} insert_lines={scores_insert_lines} "
                     f"tuples_scanned={scanned_tuples_in_scores} matched_v74_ge70={matched_total} "
                     f"elapsed={elapsed:.1f}s", log_path)
                last_progress = time.time()

    elapsed = time.time() - t0
    _tee(f"DONE scanning scores block. insert_lines={scores_insert_lines} "
         f"tuples_scanned={scanned_tuples_in_scores} matched_v74_ge70_any_window={matched_total} "
         f"elapsed={elapsed:.1f}s", log_path)

    summary = {
        "dump_path": DUMP_PATH,
        "dump_size_bytes": sz,
        "target_version_id": TARGET_VERSION_ID,
        "min_overall": MIN_OVERALL,
        "windows": WINDOWS,
        "dump_scores_column_order": cols,
        "matches_live_schema_order": (cols == EXPECTED_COLS),
        "scores_insert_lines": scores_insert_lines,
        "tuples_scanned_in_scores_block": scanned_tuples_in_scores,
        "matched_v74_ge70_any_window_total": matched_total,
        "rows_per_window": {k: len(v) for k, v in rows_out.items()},
        "elapsed_seconds": elapsed,
    }

    for wname, rows in rows_out.items():
        out_path = os.path.join(OUT_DIR, f"backup_{wname}.csv")
        rows_sorted = sorted(rows, key=lambda r: (r[0], r[1]))
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "overall", "updated_at"])
            w.writerows(rows_sorted)
        _tee(f"wrote {out_path} ({len(rows_sorted)} rows)", log_path)

    summary_path = os.path.join(OUT_DIR, "backup_diff_parse_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    _tee(f"wrote summary {summary_path}", log_path)
    _tee("SUCCESS", log_path)


if __name__ == "__main__":
    main()
