"""
HOSTILE_REVIEW P1 (A-7): barrier-cache drift sample.

Question: are cached barrier outcomes for OLD signal dates (>1y old, before 2025-07-01)
still reproducible by recomputing the SAME walk from CURRENT PriceHistory? The cache
(database/barrier_cache.py) is written once and only refreshed for the trailing 160 days
(refresh_recent); everything older is a frozen snapshot. PriceHistory itself is
retro-adjusted continuously (splits/dividends), so the underlying price series a signal
date "sees" today may differ from what it saw when the cache row was written.

Method: sample ~200 cached rows (result IS NOT NULL, date < 2025-07-01), spread across
years via the DuckDB mirror. For each row, reconstruct the barrier params it implies
(barrier_set + w_days fully determine K/M/reference_days via BARRIER_SETS -- this schema
is UNAMBIGUOUS, no restriction needed), pull CURRENT PriceHistory for that symbol via
MySQL, recompute sigma_pct fresh (same 60d realized-vol window as production) and re-walk
the SAME (database.barrier_cache._walk_outcome) function used to build the cache. Diff
result + exit_return.

Read-only: SELECT-only against MySQL (PriceHistory); read-only against the SQLite/DuckDB
barrier cache (no writes, no rebuild). Reads MySQL per sampled symbol -> run via the task
queue, not foreground.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/_audit_2026_07/p1_barrier_cache_drift.py
"""
import os
import sys
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# FORCE-insert at index 0 -- global PYTHONPATH can put a different checkout ahead
# of this one (worktree trap); guarantee THIS repo's code is what gets imported.
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

from database.barrier_cache import (  # noqa: E402
    CACHE_DB, CACHE_DUCK, BARRIER_SETS, _walk_outcome, _realized_vol, get_conn,
)

N_SAMPLE = 200
CUTOFF = date(2025, 7, 1)
SEED = 20260717
MIN_YEAR = 2010  # sanity floor; cache goes back further but pre-2010 coverage is thin


def _to_date(v):
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def sample_rows_duck(n, cutoff, seed):
    import duckdb
    con = duckdb.connect(str(CACHE_DUCK), read_only=True)
    try:
        # DuckDB's own progress bar emits unicode block-drawing characters on
        # long-running queries -- fatal on a cp1252 console. Disable it.
        con.execute("SET enable_progress_bar=false")
        yrs = con.execute(
            "SELECT DISTINCT EXTRACT(year FROM date)::INT AS y FROM barrier_outcomes "
            "WHERE date < ? AND date >= ? AND result IS NOT NULL ORDER BY y",
            [cutoff, date(MIN_YEAR, 1, 1)],
        ).fetchall()
        years = [int(r[0]) for r in yrs]
        if not years:
            return None, "no rows with result IS NOT NULL in [%d, %s)" % (MIN_YEAR, cutoff)
        per_year = max(1, (n // len(years)) + 1)
        rows = []
        for y in years:
            q = (
                "SELECT symbol, date, side, w_days, barrier_set, result, exit_return, "
                "entry_close, sigma_pct FROM barrier_outcomes "
                f"WHERE date >= DATE '{y}-01-01' AND date < DATE '{y + 1}-01-01' "
                "AND date < ? AND result IS NOT NULL ORDER BY random() LIMIT ?"
            )
            rs = con.execute(q, [cutoff, per_year]).fetchall()
            rows.extend(rs)
        random.Random(seed).shuffle(rows)
        return rows[:n], None
    finally:
        con.close()


def sample_rows_sqlite(n, cutoff, seed):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT substr(date,1,4) AS y FROM barrier_outcomes "
            "WHERE date < ? AND date >= ? AND result IS NOT NULL ORDER BY y",
            (cutoff.isoformat(), f"{MIN_YEAR}-01-01"),
        )
        years = [int(r[0]) for r in cur.fetchall()]
        if not years:
            return None, "no rows with result IS NOT NULL in [%d, %s)" % (MIN_YEAR, cutoff)
        per_year = max(1, (n // len(years)) + 1)
        rows = []
        for y in years:
            cur.execute(
                "SELECT symbol, date, side, w_days, barrier_set, result, exit_return, "
                "entry_close, sigma_pct FROM barrier_outcomes "
                "WHERE date >= ? AND date < ? AND date < ? AND result IS NOT NULL "
                "ORDER BY RANDOM() LIMIT ?",
                (f"{y}-01-01", f"{y + 1}-01-01", cutoff.isoformat(), per_year),
            )
            rows.extend(cur.fetchall())
        random.Random(seed).shuffle(rows)
        return rows[:n], None
    finally:
        conn.close()


def sample_rows(n=N_SAMPLE, cutoff=CUTOFF, seed=SEED):
    if CACHE_DUCK.exists():
        print(f"  sampling via DuckDB mirror: {CACHE_DUCK}", flush=True)
        try:
            return sample_rows_duck(n, cutoff, seed)
        except Exception as e:
            print(f"  DuckDB sampling failed ({e}); falling back to SQLite", flush=True)
    if not CACHE_DB.exists():
        return None, f"neither DuckDB mirror ({CACHE_DUCK}) nor SQLite cache ({CACHE_DB}) exists"
    print(f"  sampling via SQLite: {CACHE_DB}", flush=True)
    return sample_rows_sqlite(n, cutoff, seed)


def recompute_row(row):
    """Recompute one cached row's barrier walk from CURRENT PriceHistory (MySQL)."""
    from database.models.technical import PriceHistory

    symbol, d_raw, side, w_days, barrier_set, cached_result, cached_exit_return, \
        cached_entry_close, cached_sigma = row
    sig_date = _to_date(d_raw)
    w_days = int(w_days)

    if barrier_set not in BARRIER_SETS:
        return dict(status="unknown_barrier_set", symbol=symbol, date=sig_date,
                    side=side, w_days=w_days, barrier_set=barrier_set)

    lo = sig_date - timedelta(days=100)   # 70d sigma window + margin
    hi = sig_date + timedelta(days=w_days + 30)
    bars = list(
        PriceHistory.select(PriceHistory.date, PriceHistory.close,
                             PriceHistory.high, PriceHistory.low, PriceHistory.open)
        .where(PriceHistory.symbol == symbol,
               PriceHistory.date >= lo, PriceHistory.date <= hi)
        .order_by(PriceHistory.date)
        .tuples()
    )
    if len(bars) < 31:
        return dict(status="insufficient_current_data", symbol=symbol, date=sig_date,
                    side=side, w_days=w_days, barrier_set=barrier_set, n_bars=len(bars))

    dates_ = [b[0] for b in bars]
    closes = [float(b[1]) for b in bars]
    highs = [float(b[2]) for b in bars]
    lows = [float(b[3]) for b in bars]
    opens = [float(b[4]) if b[4] is not None else float(b[1]) for b in bars]

    try:
        base_idx = dates_.index(sig_date)
    except ValueError:
        return dict(status="signal_date_missing_in_current_pricehistory", symbol=symbol,
                    date=sig_date, side=side, w_days=w_days, barrier_set=barrier_set)
    if base_idx < 30:
        return dict(status="insufficient_trailing_data", symbol=symbol, date=sig_date,
                    side=side, w_days=w_days, barrier_set=barrier_set)

    new_sigma = _realized_vol(closes[max(0, base_idx - 70):base_idx + 1])
    if new_sigma is None or new_sigma <= 0:
        return dict(status="sigma_unavailable", symbol=symbol, date=sig_date, side=side,
                    w_days=w_days, barrier_set=barrier_set)

    res = _walk_outcome(closes, highs, lows, dates_, base_idx, side, new_sigma, w_days,
                         scale=True, barrier_set=barrier_set, opens=opens)
    new_result, new_exit_offset, new_exit_close, new_exit_return = res[0], res[1], res[2], res[3]

    return dict(
        status="ok", symbol=symbol, date=sig_date, side=side, w_days=w_days,
        barrier_set=barrier_set,
        cached_result=cached_result, new_result=new_result,
        cached_exit_return=cached_exit_return, new_exit_return=new_exit_return,
        cached_sigma=cached_sigma, new_sigma=new_sigma,
        cached_entry_close=cached_entry_close,
    )


def pctl(vals, p):
    if not vals:
        return float("nan")
    vals = sorted(vals)
    k = (len(vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def main():
    print("=" * 78)
    print("P1 -- barrier-cache drift sample (HOSTILE_REVIEW A-7)")
    print("=" * 78)
    print(f"SQLite cache : {CACHE_DB}  exists={CACHE_DB.exists()}")
    print(f"DuckDB mirror: {CACHE_DUCK}  exists={CACHE_DUCK.exists()}")
    print(f"cutoff (signal date must be older than): {CUTOFF.isoformat()}")
    print("cache schema note: (symbol,date,side,w_days,barrier_set) is the PK and fully "
          "determines K/M/reference_days via BARRIER_SETS -- schema is UNAMBIGUOUS for "
          "every row, no restriction to a subset of rows was needed.")
    print()

    rows, err = sample_rows(N_SAMPLE, CUTOFF, SEED)
    if rows is None:
        print(f"MISSING: could not sample cache rows -- {err}")
        return 0
    print(f"sampled {len(rows)} cached rows", flush=True)
    print()

    results = []
    status_counts = defaultdict(int)
    for i, row in enumerate(rows):
        try:
            r = recompute_row(row)
        except Exception as e:
            r = dict(status="exception", symbol=row[0], date=_to_date(row[1]),
                      side=row[2], w_days=row[3], barrier_set=row[4], error=str(e))
        results.append(r)
        status_counts[r["status"]] += 1
        if (i + 1) % 25 == 0:
            print(f"  recomputed {i + 1}/{len(rows)}...", flush=True)

    print()
    print("recompute status breakdown:")
    for st, n in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {st:42s} {n:4d}")
    print()

    ok = [r for r in results if r["status"] == "ok"]
    n_ok = len(ok)
    if n_ok == 0:
        print("MISSING: no rows successfully recomputed -- cannot report drift stats.")
        return 0

    exact = [r for r in ok if r["cached_result"] is not None and r["new_result"] is not None
             and int(r["cached_result"]) == int(r["new_result"])]
    comparable = [r for r in ok if r["cached_result"] is not None and r["new_result"] is not None]
    n_comparable = len(comparable)
    n_exact = len(exact)
    win_to_loss = [r for r in comparable
                   if int(r["cached_result"]) == 1 and int(r["new_result"]) == 0]
    loss_to_win = [r for r in comparable
                   if int(r["cached_result"]) == 0 and int(r["new_result"]) == 1]

    abs_deltas = []
    for r in ok:
        ce, ne = r.get("cached_exit_return"), r.get("new_exit_return")
        if ce is not None and ne is not None:
            abs_deltas.append(abs(float(ce) - float(ne)))

    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"N sampled                    : {len(rows)}")
    print(f"N successfully recomputed    : {n_ok}  ({100.0 * n_ok / len(rows):.1f}%)")
    print(f"N comparable (both results)  : {n_comparable}")
    if n_comparable:
        print(f"exact result-match           : {n_exact}/{n_comparable} "
              f"({100.0 * n_exact / n_comparable:.1f}%)")
        print(f"result flips win->loss       : {len(win_to_loss)}")
        print(f"result flips loss->win       : {len(loss_to_win)}")
    if abs_deltas:
        mean_d = statistics.mean(abs_deltas)
        p90_d = pctl(abs_deltas, 90)
        print(f"mean  |delta exit_return|    : {mean_d:.4f} pct-pts  (N={len(abs_deltas)})")
        print(f"p90   |delta exit_return|    : {p90_d:.4f} pct-pts")
    print()

    print("breakdown by signal-date year (comparable rows only):")
    print(f"  {'year':6} {'N':>6} {'exact%':>8} {'win->loss':>10} {'loss->win':>10} "
          f"{'mean|dExit|':>12}")
    by_year = defaultdict(list)
    for r in comparable:
        by_year[r["date"].year].append(r)
    for yr in sorted(by_year):
        yrows = by_year[yr]
        n = len(yrows)
        ex = sum(1 for r in yrows if int(r["cached_result"]) == int(r["new_result"]))
        wl = sum(1 for r in yrows if int(r["cached_result"]) == 1 and int(r["new_result"]) == 0)
        lw = sum(1 for r in yrows if int(r["cached_result"]) == 0 and int(r["new_result"]) == 1)
        yd = [abs(float(r["cached_exit_return"]) - float(r["new_exit_return"]))
              for r in yrows if r.get("cached_exit_return") is not None
              and r.get("new_exit_return") is not None]
        mean_yd = statistics.mean(yd) if yd else float("nan")
        print(f"  {yr:<6} {n:>6} {100.0 * ex / n:>7.1f}% {wl:>10} {lw:>10} {mean_yd:>12.4f}")
    print()

    flipped = win_to_loss + loss_to_win
    print(f"example flipped rows (up to 5 of {len(flipped)}):")
    print(f"  {'symbol':8} {'date':12} {'side':6} {'w_days':7} {'barrier_set':14} "
          f"{'cached_res':10} {'new_res':8} {'cached_exit':12} {'new_exit':10}")
    for r in flipped[:5]:
        print(f"  {r['symbol']:8} {r['date'].isoformat():12} {r['side']:6} "
              f"{r['w_days']:7} {r['barrier_set']:14} "
              f"{r['cached_result']:>10} {r['new_result']:>8} "
              f"{float(r['cached_exit_return']):>12.3f} {float(r['new_exit_return']):>10.3f}")
    if not flipped:
        print("  (none)")
    print()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
