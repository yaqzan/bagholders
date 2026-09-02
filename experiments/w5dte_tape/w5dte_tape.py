"""w5dte_tape.py -- forward paper-tape driver for the W5DTE rule family
(.horizon/w5dte-paper-tape/TASK.md; experiments/w5dte_ev/OWNER_SPEC.md lock #3 /
FINDINGS.md "Disposition"). Follows the ct15-paper-sleeve pattern for scheduling, state,
and dedup keys (.horizon/ct15-paper-sleeve/driver/ct15_tape.py), but is fully independent
of it (own state.json, own tape file, own predicate).

WHAT THIS OBSERVES: the w5dte_ev study found a real, tech-concentrated, tail-exit lottery
edge in weekly 5-DTE deep-OTM calls (RESULTS_TABLES.md E3 rules R1-R4: moneyness_pct +
hl_range_pct + sector==technology [+cp=='C']), and it PASSED its exposure-matched-control
EV gate (FINDINGS.md: FAMILY TP-5x EV +2.39%, beats 100/100 control draws). The true rule
conjunct (`hl_range_pct`, intraday high-low range) is UNOBSERVABLE in this repo's live
option source (`option_prices` is a daily lastPrice/volume/OI snapshot, no intraday
high/low) -- so this tape is a PROXY-FIDELITY instrument: it substitutes a live-observable
close-to-close move for the true intraday-violence conjunct, calibrated against the archive
in `calibrate_proxy.py` / `FIDELITY.md` (read that first if the numbers below look
arbitrary). Every deviation from the discovered rule is quantified there; this tape is
paper-only and disposable if the owner rejects the proxy.

TWO MODES, both idempotent per (mode, date) via state.json's "done" list (--force
re-runs):

  --entry (Mon/Tue post-close): scans TODAY's option chain for TECHNOLOGY-sector
    underlyings only (w5dte_ev R1-R4 is tech-only -- FINDINGS.md point 3: "the
    family-minus-tech residual has no control-beating EV"), CALLS ONLY (R1-R4's real
    predicates functionally select calls -- moneyness_pct>=+3.958% is OTM-for-calls /
    ITM-for-puts, so an unconditioned moneyness gate already concentrates on calls; R3/R4
    gate cp=='C' explicitly; this is a deliberate PROXY-rule simplification, not a
    replication of all 6 R1..R6 variants separately -- see NOTES below). Applies:
        moneyness_pct = strike/spot - 1 >= 0.03958416633346662   (PREREG.md amendment
            2026-08-18a's full-precision P80 constant, R1/R2/R4's threshold)
        entry premium (option_prices.price) >= 0.20
        proxy-violence |price_today / price_prev_snapshot - 1| >= X   (X = calibrate_proxy.py's
            pooled exceedance-matched threshold; prev snapshot = the most recent PRIOR
            option_prices row for that contract within 5 days)
        expiry == this week's last trading day (Friday, or Thursday on a holiday Friday)
    Logs is_monthly_opex; logs volume/open_interest but never gates on them (FIDELITY.md
    "volume is unreliable live" -- ct15-paper-sleeve/LESSONS.md measured 79% zero-volume
    even on deeply liquid contracts). Appends new (date, option_id) hits to tape.jsonl.

  --outcomes (Friday post-close, or later -- self-heals a missed Friday): for OPEN tape
    entries whose expiry has passed (expiry <= target date), records
    max(option_prices.price) over (entry_date, expiry_day] -- a LOWER BOUND on the true
    week high (FIDELITY.md caveat) -- and the exact expiry-day price if a print exists
    there. growth_mult = max_price_after_entry / entry price. Marks entries closed.
    Rewrites tape.jsonl via tmp+os.replace (whole-file atomic; entries are mutated in
    place, unlike --entry's pure append).

--dry-run (both modes): prints what would be logged/rewritten; writes nothing (no
tape.jsonl change, no state.json change).

PAPER ONLY. Zero production impact: never touches scores/portfolio/strategy_config
write-paths, never queues anything (MySQL reads here are single-day / single-week bounded
by construction -- see NOTES "why this needs no queue submission").

NOTES -- real production data-quality finding (2026-08-18, discovered while building this):
`price_history.close_unadj` (AS-TRADED spot, needed for moneyness) is chronically MISSING
for fresh/live rows: 20-38% coverage over the last 3 weeks vs ~99% in early-mid July 2026,
even though trader.py:280-293's own `_UNADJ_FRESH_DAYS=5` mechanism is *designed* to write
it on every fresh pull (own comment: "the two conventions COINCIDE at the moment a bar is
first printed, because no split or dividend has happened since"). This tape works around
the gap using that SAME repo-established rationale as an explicit, logged fallback: when
close_unadj is null AND the target date is itself fresh (<=5 days old at run time), use
`close` instead (spot_source='close_fallback_fresh' logged on the row) -- justified because
trader.py's own comment establishes `close`==as-traded for a bar this fresh, independent of
whether the close_unadj column happened to get persisted. Older target dates with a missing
close_unadj are SKIPPED (spot_source=None), never silently substituted (a stale adjusted
close would be "a quiet lie" per that same comment). The coverage regression itself is
OUT OF SCOPE for this tape (a production price-pull issue, not a paper-tape concern) and is
flagged separately -- see TAPE_BUILD_REPORT.md.

NOTES -- calls-only / tech-only simplification: R1-R4 is not implemented as 4 separate
rule variants. This is deliberately the single PROXY rule the task specifies (moneyness +
proxy-violence + tech + calls), matching the "PROXY-FIDELITY design" framing in
FINDINGS.md's Disposition, not a literal per-rule replication (that would need the
otm_pct>=0.0576 variants too, and per-rule is_monthly_opex gating that 3 of the 4 rules
don't even apply). is_monthly_opex is logged on every row either way, for future analysis.

NOTES -- why no `trader queue submit`: --entry's MySQL work is one single-day, single-expiry,
single-sector chain join (~200 symbols) plus one small prior-snapshot lookup bounded to a
5-day window over a handful of option_ids; --outcomes is one small lookup per (entry_date,
expiry) group bounded to that week's dates. Both finish in low single-digit seconds --
"single-day chains for tech universe", exactly the CLAUDE.md carve-out for genuinely light
foreground checks, not the sweep/recalc/backfill class of job the queue exists to protect.

Usage:
    python experiments/w5dte_tape/w5dte_tape.py --entry [--date YYYY-MM-DD] [--dry-run] [--force]
    python experiments/w5dte_tape/w5dte_tape.py --outcomes [--date YYYY-MM-DD] [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve().parent            # experiments/w5dte_tape/
REPO_ROOT = _HERE.parents[1]                        # C:\Development\Trader
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))              # pin repo root FIRST -- worktree PYTHONPATH trap guard
assert os.path.isfile(REPO_ROOT / "CLAUDE.md"), \
    f"sys.path pin landed on {REPO_ROOT!r}, expected the Trader repo root"

from database.utils.trading_calendar import (   # noqa: E402
    is_trading_day, last_trading_day, trading_days_between,
)

TASK_ROOT = REPO_ROOT / ".horizon" / "w5dte-paper-tape"
STATE_PATH = TASK_ROOT / "state.json"
TAPE_PATH = TASK_ROOT / "tape.jsonl"
LOG_DIR = TASK_ROOT / "logs"

CALIBRATION_STATS_PATH = _HERE / "calibration_stats.json"

# ---------------------------------------------------------------------------
# rule constants
# ---------------------------------------------------------------------------
SECTOR = "technology"
OPTION_TYPE_DB = "call"        # database.models.options.Option.option_type storage value
CP_LABEL = "C"                 # parent-ledger-style single-letter label logged on the row

# PREREG.md 2026-08-18a amendment: the full-precision P80 constant behind R1/R2/R4's
# rounded "0.03958" display label -- see experiments/w5dte_ev/PREREG.md.
MONEYNESS_THRESHOLD = 0.03958416633346662

ENTRY_PREMIUM_MIN = 0.20
PREV_SNAPSHOT_WINDOW_DAYS = 5

# trader.py:159 _UNADJ_FRESH_DAYS -- close_unadj/close coincide for a bar this fresh, so a
# missing close_unadj on a fresh row can safely fall back to `close` (see module docstring
# NOTES). Mirrors that constant exactly rather than inventing a new one.
SPOT_FALLBACK_FRESH_DAYS = 5


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# calibration load (fails loudly if calibrate_proxy.py has not been run)
# ---------------------------------------------------------------------------
def _load_calibration() -> dict:
    if not CALIBRATION_STATS_PATH.exists():
        raise FileNotFoundError(
            f"{CALIBRATION_STATS_PATH} not found -- run calibrate_proxy.py first; it writes "
            f"the proxy-violence threshold X this script needs (see FIDELITY.md)."
        )
    with open(CALIBRATION_STATS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_CALIBRATION = _load_calibration()
PROXY_X = float(_CALIBRATION["recommended_X"])          # calls-only exceedance-matched threshold
PROXY_X_BASIS = _CALIBRATION.get("recommended_X_basis", "unknown")
PROXY_X_CALIBRATED_AT = _CALIBRATION.get("generated_at", "unknown")


# ---------------------------------------------------------------------------
# pure calendar helpers (self-contained -- deliberately not imported from
# experiments/weekly_5dte_movers/build_ledger.py, which reads the FROZEN archive
# calendar; this tape needs the LIVE NYSE calendar instead. Same logic, different
# calendar source.)
# ---------------------------------------------------------------------------
def week_monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def is_monthly_opex_week(week_monday: date, expiry_day: date) -> bool:
    for month_ref in {week_monday, expiry_day}:
        tf = third_friday(month_ref.year, month_ref.month)
        if week_monday_of(tf) == week_monday:
            return True
    return False


def expiry_day_for_week(week_monday: date) -> date:
    """Last NYSE trading day of the Mon-Fri week starting week_monday -- Friday normally,
    Thursday (or earlier) on a holiday-Friday week."""
    week_friday = week_monday + timedelta(days=4)
    return last_trading_day(week_friday)


def format_occ_ticker(underlying: str, expiry: date, cp: str, strike: float) -> str:
    """O:<ROOT><YYMMDD><C|P><8-digit strike*1000> -- OCC/OPRA convention, matches
    experiments/flatfile_exploitation/ff_common.format_opra_ticker's shape (reimplemented
    here, not imported, to keep this tape independent of the flatfile-exploitation program
    at runtime)."""
    strike_thousandths = int(round(strike * 1000))
    return f"O:{underlying}{expiry.strftime('%y%m%d')}{cp}{strike_thousandths:08d}"


# ---------------------------------------------------------------------------
# state.json I/O -- atomic write (temp file + rename), long-horizon skill contract.
# ---------------------------------------------------------------------------
def default_state() -> dict:
    return {
        "task": "w5dte-paper-tape",
        "updated": "",
        "phase": "idle",
        "cursor": None,
        "done": [],
        "counters": {},
        "config": {
            "sector": SECTOR,
            "option_type": OPTION_TYPE_DB,
            "moneyness_threshold": MONEYNESS_THRESHOLD,
            "entry_premium_min": ENTRY_PREMIUM_MIN,
            "prev_snapshot_window_days": PREV_SNAPSHOT_WINDOW_DAYS,
            "spot_fallback_fresh_days": SPOT_FALLBACK_FRESH_DAYS,
            "proxy_x": PROXY_X,
            "proxy_x_basis": PROXY_X_BASIS,
            "proxy_x_calibrated_at": PROXY_X_CALIBRATED_AT,
            "proxy_x_source": "experiments/w5dte_tape/calibration_stats.json",
        },
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return default_state()
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        st = json.load(f)
    base = default_state()
    for k, v in base.items():
        st.setdefault(k, v)
    return st


def save_state(st: dict) -> None:
    st["updated"] = datetime.now().isoformat(timespec="seconds")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(STATE_PATH.parent), prefix=".state_", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2, default=str)
        os.replace(tmp_path, STATE_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _write_log_line(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{datetime.now():%Y-%m}.log"
    with open(path, "a", encoding="ascii", errors="replace") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")


# ---------------------------------------------------------------------------
# tape.jsonl I/O
# ---------------------------------------------------------------------------
def _read_tape_rows() -> list:
    if not TAPE_PATH.exists():
        return []
    rows = []
    with open(TAPE_PATH, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _load_existing_dedup_keys() -> set:
    return {(r.get("date"), r.get("option_id")) for r in _read_tape_rows()}


def append_tape_rows(rows: list) -> None:
    """Per-run dedup already applied by the caller -- this is a pure open-append, no
    read-modify-write needed (task spec: 'open-append with a per-run dedup check')."""
    if not rows:
        return
    TAPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TAPE_PATH, "a", encoding="ascii", errors="replace") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
        f.flush()


def _atomic_rewrite_tape(rows: list) -> None:
    """--outcomes mutates existing rows in place -- needs a real read-modify-write,
    applied via tmp file + os.replace (whole-file atomic)."""
    TAPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(TAPE_PATH.parent), prefix=".tape_", suffix=".jsonl.tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii", errors="replace") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
        os.replace(tmp_path, TAPE_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# MySQL access -- bounded, single-day / single-week (see module docstring NOTES)
# ---------------------------------------------------------------------------
def _db():
    from database.trader_database import DB
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
    return DB


_CHAIN_SQL = """
    SELECT s.symbol, o.id, o.strike_price, op.price, op.volume, op.open_interest,
           ph.close, ph.close_unadj
    FROM stocks s
    JOIN options o ON o.symbol = s.symbol
    JOIN option_prices op ON op.option_id = o.id AND op.date = %s
    JOIN price_history ph ON ph.symbol = s.symbol AND ph.date = %s
    WHERE s.sector = %s
      AND o.option_type = %s
      AND o.expiration_date = %s
      AND op.price >= %s
"""


def tech_call_chain(target_date: date, expiry_day: date) -> list:
    """Single bounded query: technology-sector call options expiring this week's expiry
    day, priced on target_date >= ENTRY_PREMIUM_MIN. Returns list of dicts."""
    db = _db()
    rows = db.execute_sql(
        _CHAIN_SQL, (target_date, target_date, SECTOR, OPTION_TYPE_DB, expiry_day, ENTRY_PREMIUM_MIN)
    ).fetchall()
    cols = ["symbol", "option_id", "strike_price", "price", "volume", "open_interest",
            "underlying_close", "underlying_close_unadj"]
    return [dict(zip(cols, r)) for r in rows]


def prev_snapshots(option_ids: list, target_date: date, window_days: int = PREV_SNAPSHOT_WINDOW_DAYS) -> dict:
    """option_id -> (prev_date, prev_price): the most recent OptionPrice row strictly
    before target_date, within window_days. Bounded IN-list + 5-day date range."""
    if not option_ids:
        return {}
    db = _db()
    lo = target_date - timedelta(days=window_days)
    placeholders = ",".join(["%s"] * len(option_ids))
    sql = (f"SELECT option_id, date, price FROM option_prices "
           f"WHERE option_id IN ({placeholders}) AND date < %s AND date >= %s "
           f"ORDER BY option_id, date")
    rows = db.execute_sql(sql, (*option_ids, target_date, lo)).fetchall()
    best = {}
    for option_id, d, price in rows:
        best[option_id] = (d, float(price))   # ORDER BY date ASC per option_id -- last write wins (max date)
    return best


def fetch_week_outcomes(option_ids: list, entry_date: date, expiry_day: date) -> dict:
    """option_id -> {max_price, max_price_date, expiry_price} over (entry_date, expiry_day].
    Bounded IN-list + one week's date range."""
    if not option_ids:
        return {}
    db = _db()
    placeholders = ",".join(["%s"] * len(option_ids))
    sql = (f"SELECT option_id, date, price FROM option_prices "
           f"WHERE option_id IN ({placeholders}) AND date > %s AND date <= %s "
           f"ORDER BY option_id, date")
    rows = db.execute_sql(sql, (*option_ids, entry_date, expiry_day)).fetchall()
    by_option: dict = {}
    for option_id, d, price in rows:
        by_option.setdefault(option_id, []).append((d, float(price)))
    out = {}
    for oid, pts in by_option.items():
        max_date, max_price = max(pts, key=lambda t: t[1])
        expiry_price = next((p for d, p in pts if d == expiry_day), None)
        out[oid] = {"max_price": max_price, "max_price_date": max_date.isoformat(),
                     "expiry_price": expiry_price}
    return out


# ---------------------------------------------------------------------------
# spot resolution (with the documented close_unadj fallback -- see module docstring NOTES)
# ---------------------------------------------------------------------------
def compute_spot(row: dict, target_date: date) -> tuple:
    unadj = row.get("underlying_close_unadj")
    if unadj is not None and float(unadj) > 0:
        return float(unadj), "unadj"
    is_fresh = (date.today() - target_date).days <= SPOT_FALLBACK_FRESH_DAYS
    if is_fresh:
        close = row.get("underlying_close")
        if close is not None and float(close) > 0:
            return float(close), "close_fallback_fresh"
    return None, None


# ---------------------------------------------------------------------------
# --entry
# ---------------------------------------------------------------------------
def find_entry_candidates(target_date: date) -> tuple:
    week_monday = week_monday_of(target_date)
    expiry_day = expiry_day_for_week(week_monday)
    opex = is_monthly_opex_week(week_monday, expiry_day)

    raw = tech_call_chain(target_date, expiry_day)

    staged = []
    n_no_spot = 0
    n_below_moneyness = 0
    for row in raw:
        spot, spot_source = compute_spot(row, target_date)
        if spot is None:
            n_no_spot += 1
            continue
        strike = float(row["strike_price"])
        moneyness_pct = strike / spot - 1.0
        if moneyness_pct < MONEYNESS_THRESHOLD:
            n_below_moneyness += 1
            continue
        staged.append({
            "symbol": row["symbol"], "option_id": int(row["option_id"]), "strike": strike,
            "price": float(row["price"]), "volume": int(row["volume"]) if row["volume"] is not None else 0,
            "oi": int(row["open_interest"]) if row["open_interest"] is not None else 0,
            "spot": spot, "spot_source": spot_source, "moneyness_pct": moneyness_pct,
        })

    meta = {
        "week_monday": week_monday, "expiry_day": expiry_day, "is_monthly_opex": opex,
        "raw_chain_rows": len(raw), "no_spot": n_no_spot, "below_moneyness": n_below_moneyness,
        "after_moneyness_filter": len(staged),
    }

    if not staged:
        return [], meta

    option_ids = [r["option_id"] for r in staged]
    prevmap = prev_snapshots(option_ids, target_date)

    candidates = []
    n_no_prev = 0
    n_below_proxy = 0
    for r in staged:
        prev = prevmap.get(r["option_id"])
        if prev is None:
            n_no_prev += 1
            continue
        prev_date, prev_price = prev
        if prev_price <= 0:
            n_no_prev += 1
            continue
        proxy_move = abs(r["price"] / prev_price - 1.0)
        if proxy_move < PROXY_X:
            n_below_proxy += 1
            continue
        c = dict(r)
        c.update({"prev_date": prev_date, "prev_price": prev_price, "proxy_move": proxy_move})
        candidates.append(c)

    meta["no_prev_snapshot"] = n_no_prev
    meta["below_proxy_threshold"] = n_below_proxy
    meta["candidates"] = len(candidates)
    return candidates, meta


def build_tape_row(c: dict, meta: dict, target_date: date) -> dict:
    expiry_day = meta["expiry_day"]
    occ_ticker = format_occ_ticker(c["symbol"], expiry_day, CP_LABEL, c["strike"])
    dte_calendar = (expiry_day - target_date).days
    dte_trading = trading_days_between(target_date, expiry_day)
    return {
        "status": "open",
        "date": target_date.isoformat(),
        "entry_dow": "Mon" if target_date.weekday() == 0 else "Tue",
        "week_monday": meta["week_monday"].isoformat(),
        "occ_ticker": occ_ticker,
        "option_id": c["option_id"],
        "underlying": c["symbol"],
        "sector": SECTOR,
        "strike": c["strike"],
        "expiry": expiry_day.isoformat(),
        "dte_calendar": dte_calendar,
        "dte_trading": dte_trading,
        "cp": CP_LABEL,
        "price": c["price"],
        "prev_price": c["prev_price"],
        "prev_date": c["prev_date"].isoformat(),
        "proxy_move": c["proxy_move"],
        "proxy_x_used": PROXY_X,
        "moneyness_pct": c["moneyness_pct"],
        "spot": c["spot"],
        "spot_source": c["spot_source"],
        "volume": c["volume"],
        "oi": c["oi"],
        "is_monthly_opex": meta["is_monthly_opex"],
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        # outcome fields -- filled in by --outcomes:
        "outcome_recorded_date": None,
        "max_price_after_entry": None,
        "max_price_date": None,
        "price_at_expiry": None,
        "no_later_print": None,
        "growth_mult": None,
    }


def run_entry(target_date: date, dry_run: bool, force: bool) -> int:
    if not is_trading_day(target_date):
        log(f"{target_date.isoformat()} is not an NYSE trading day -- nothing to do")
        return 0
    if target_date.weekday() not in (0, 1):
        log(f"{target_date.isoformat()} is not a Monday/Tuesday (weekday={target_date.weekday()}) -- "
            f"--entry only runs Mon/Tue; nothing to do")
        return 0

    state = load_state()
    run_key = f"entry:{target_date.isoformat()}"
    if run_key in state["done"] and not force:
        log(f"{run_key} already recorded in state.json -- skipping (--force to re-run)")
        return 0

    candidates, meta = find_entry_candidates(target_date)
    log(f"ENTRY {target_date.isoformat()} (week_monday={meta['week_monday']} "
        f"expiry_day={meta['expiry_day']} is_monthly_opex={meta['is_monthly_opex']}): "
        f"chain_rows={meta['raw_chain_rows']} no_spot={meta['no_spot']} "
        f"below_moneyness={meta['below_moneyness']} after_moneyness={meta['after_moneyness_filter']} "
        f"no_prev_snapshot={meta.get('no_prev_snapshot', 0)} below_proxy={meta.get('below_proxy_threshold', 0)} "
        f"-- {len(candidates)} candidate(s) (proxy X={PROXY_X:.4f})")

    existing_keys = _load_existing_dedup_keys()
    new_rows = []
    n_dupe = 0
    for c in candidates:
        key = (target_date.isoformat(), c["option_id"])
        if key in existing_keys:
            n_dupe += 1
            continue
        new_rows.append(build_tape_row(c, meta, target_date))

    log(f"  {len(new_rows)} new row(s) to log ({n_dupe} already present this run -- dedup skip)")
    for r in new_rows[:10]:
        log(f"    {r['underlying']:<6s} {r['occ_ticker']:<24s} strike={r['strike']:.2f} "
            f"price={r['price']:.2f} prev={r['prev_price']:.2f} proxy_move={r['proxy_move']:.4f} "
            f"moneyness={r['moneyness_pct']:.4f} vol={r['volume']} oi={r['oi']} "
            f"spot_source={r['spot_source']}")

    if dry_run:
        log(f"DRY RUN -- would append {len(new_rows)} row(s) to {TAPE_PATH}; nothing written")
        return 0

    append_tape_rows(new_rows)
    state["done"].append(run_key)
    state["cursor"] = target_date.isoformat()
    state["counters"][run_key] = {
        "chain_rows": meta["raw_chain_rows"], "no_spot": meta["no_spot"],
        "below_moneyness": meta["below_moneyness"], "after_moneyness": meta["after_moneyness_filter"],
        "no_prev_snapshot": meta.get("no_prev_snapshot", 0),
        "below_proxy_threshold": meta.get("below_proxy_threshold", 0),
        "candidates": len(candidates), "appended": len(new_rows), "dedup_skipped": n_dupe,
    }
    save_state(state)
    _write_log_line(f"ENTRY {target_date.isoformat()}: appended={len(new_rows)} dedup_skip={n_dupe} "
                     f"candidates={len(candidates)} chain_rows={meta['raw_chain_rows']}")
    return 0


# ---------------------------------------------------------------------------
# --outcomes
# ---------------------------------------------------------------------------
def run_outcomes(target_date: date, dry_run: bool, force: bool) -> int:
    state = load_state()
    run_key = f"outcomes:{target_date.isoformat()}"
    if run_key in state["done"] and not force:
        log(f"{run_key} already recorded in state.json -- skipping (--force to re-run)")
        return 0

    rows = _read_tape_rows()
    open_rows = [r for r in rows
                 if r.get("status") == "open" and date.fromisoformat(r["expiry"]) <= target_date]
    log(f"OUTCOMES {target_date.isoformat()}: {len(rows)} total tape row(s), "
        f"{len(open_rows)} open+expired-by-today")

    if not open_rows:
        log("  nothing to close")
        if not dry_run:
            state["done"].append(run_key)
            state["cursor"] = target_date.isoformat()
            state["counters"][run_key] = {"open_before": 0, "closed": 0}
            save_state(state)
        return 0

    groups: dict = {}
    for r in open_rows:
        key = (r["date"], r["expiry"])
        groups.setdefault(key, []).append(r["option_id"])

    outcome_map: dict = {}
    for (entry_date_str, expiry_str), oids in groups.items():
        part = fetch_week_outcomes(sorted(set(oids)), date.fromisoformat(entry_date_str),
                                    date.fromisoformat(expiry_str))
        for oid, oc in part.items():
            outcome_map[(entry_date_str, oid)] = oc

    updated_rows = []
    for r in rows:
        if r.get("status") != "open" or date.fromisoformat(r["expiry"]) > target_date:
            continue
        oc = outcome_map.get((r["date"], r["option_id"]))
        entry_price = r["price"]
        if oc and oc["max_price"] is not None:
            r["max_price_after_entry"] = oc["max_price"]
            r["max_price_date"] = oc["max_price_date"]
            r["no_later_print"] = 0
            r["growth_mult"] = (oc["max_price"] / entry_price) if entry_price else None
        else:
            r["no_later_print"] = 1
            r["growth_mult"] = None
        r["price_at_expiry"] = oc["expiry_price"] if oc else None
        r["status"] = "closed"
        r["outcome_recorded_date"] = target_date.isoformat()
        updated_rows.append(r)

    log(f"  would close {len(updated_rows)} row(s)")
    for r in updated_rows[:10]:
        gm = f"{r['growth_mult']:.3f}" if r["growth_mult"] is not None else "None"
        log(f"    {r['underlying']:<6s} entry_date={r['date']} entry_px={r['price']:.2f} "
            f"max_after={r['max_price_after_entry']} expiry_px={r['price_at_expiry']} "
            f"growth_mult={gm} no_later_print={r['no_later_print']}")

    if dry_run:
        log(f"DRY RUN -- would rewrite {TAPE_PATH} with {len(updated_rows)} closed row(s); nothing written")
        return 0

    _atomic_rewrite_tape(rows)
    state["done"].append(run_key)
    state["cursor"] = target_date.isoformat()
    state["counters"][run_key] = {"open_before": len(open_rows), "closed": len(updated_rows)}
    save_state(state)
    _write_log_line(f"OUTCOMES {target_date.isoformat()}: closed={len(updated_rows)} "
                     f"open_before={len(open_rows)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--entry", action="store_true", help="Mon/Tue post-close entry pass")
    mode.add_argument("--outcomes", action="store_true", help="Friday+ post-close outcomes pass")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print would-log rows, write nothing")
    ap.add_argument("--force", action="store_true",
                     help="reprocess even if state.json already recorded this (mode, date)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    target_date = date.fromisoformat(args.date) if args.date else date.today()
    log(f"w5dte_tape.py mode={'entry' if args.entry else 'outcomes'} date={target_date.isoformat()} "
        f"dry_run={args.dry_run} force={args.force} (proxy X={PROXY_X:.4f}, "
        f"calibrated {PROXY_X_CALIBRATED_AT})")
    if args.entry:
        return run_entry(target_date, args.dry_run, args.force)
    return run_outcomes(target_date, args.dry_run, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
