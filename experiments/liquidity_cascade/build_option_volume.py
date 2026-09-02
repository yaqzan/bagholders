"""Per-signal option_volume_30d feature builder -- P3.6 Liquidity-Aware Cascade
(gameplan.md P3.6 / known-issues.md open-item #10).

For every 75+ CALL signal (active scoring version, full history), resolves:

  option_volume_30d = avg daily CONTRACT volume, trailing 30 calendar days,
                       of the nearest-ATM 20-45 DTE call as of the signal date.

Two regimes, ALWAYS tagged (never silent per the row's own requirement):

  REAL   (signal date >= OPTION_COVERAGE_START, confirmed 2025-02-10 via
          MIN(date) on option_prices, 2026-07-14): resolves an actual contract
          via the options/option_prices join (same DTE band + nearest-strike
          tie-break as experiments/iv_engine_pertrade/build_ledger.py
          select_contract -- reused idiom, not reused code, since that module
          is private to its own experiment) then averages that SAME contract's
          real daily volume over the trailing 30 calendar days.

  FALLBACK (pre-coverage, or the real join found no chainable contract):
          Stock.average_volume(to_date=signal_date, last_n_days=30) * 0.005
          -- the known-issues #10 spec's rough options-fraction-of-equity-volume
          proxy. Computed via a single bulk PriceHistory pull + polars rolling
          mean (NOT the average_volume() ORM method per-signal -- that would be
          one extra DB round trip per fallback signal, and fallback signals are
          the overwhelming majority of the 10y history; see "Efficiency" below).

Scope decision: CALLS ONLY. Every live Stage-3 dampener (RXDD/SVR/MWDD/TVDD/
BDIV/SPREAD_TILT) is calls-only precedent (puts are OFF in the live profile --
known-issues.md / project memory). No put-side resolution is built this pass.

Efficiency: the REAL-era per-signal resolution is 2 indexed queries per signal
(nearest-ATM join + trailing-volume aggregate) against a ~90M-row option_prices
table -- same shape as iv_engine_pertrade's "~7,800 indexed per-signal/per-
contract queries", itself queued db=heavy. The FALLBACK-era path is instead one
bulk PriceHistory pull (chunked_query_by_year, mirrors build_liq.py) + a single
polars rolling-mean pass, then a (symbol,date) join -- NOT per-signal queries,
since fallback signals are ~85%+ of the full 10y set (real coverage is only
~1.4y of ~10y) and per-signal ORM calls at that volume would be the slow path.

LIQUIDITY_SMOKE=<N> env var caps the REAL-era resolution loop to the first N
chainable signals for a fast local correctness/distribution check (mirrors
build_ledger.py's LEDGER_SMOKE). The FALLBACK-era bulk path is unaffected by
smoke mode (it's already cheap/vectorized regardless of N).

Full run is DB-heavy (real-era per-signal joins) -- submit via `trader queue
submit`, do not run the unsmoked full population directly.

Output: .cache/experiment_data/liquidity_option_volume_30d.parquet
        {symbol, date, option_volume_30d, is_fallback}

Run (smoke):  LIQUIDITY_SMOKE=300 PYTHONIOENCODING=utf-8 python -u experiments/liquidity_cascade/build_option_volume.py
Run (full, queued): PYTHONIOENCODING=utf-8 python -u experiments/liquidity_cascade/build_option_volume.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import polars as pl                                                    # noqa: E402
from database.bulk_cache import materialize_polars, chunked_query_by_year  # noqa: E402
from database.trader_database import DB                                # noqa: E402

OPTION_COVERAGE_START = date(2025, 2, 10)   # confirmed MIN(date) option_prices, 2026-07-14
FALLBACK_EQUITY_FRACTION = 0.005            # known-issues.md #10 spec
CACHE_NAME = 'liquidity_option_volume_30d'
SIGNAL_MIN_SCORE = 75                       # the 75+ qualifying universe (cascade tiers)

# Same DTE band as experiments/iv_engine_pertrade/build_ledger.py select_contract
# (20-45 calendar days = "30-DTE-ish"), PLUS open_interest/iv sanity filters it
# also applies (real/active listing, not a garbage print).
_NEAREST_SQL = (
    "SELECT o.id, o.strike_price, o.expiration_date, "
    "DATEDIFF(o.expiration_date, op.date) AS dte, ph.close, op.open_interest "
    "FROM options o "
    "JOIN option_prices op ON op.option_id = o.id "
    "JOIN price_history ph ON ph.symbol = o.symbol AND ph.date = op.date "
    "WHERE o.symbol=%s AND o.option_type='call' AND op.date=%s "
    "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
    "AND op.price>0 AND op.open_interest>0 AND op.iv BETWEEN 0.05 AND 5.0"
)
_TRAILING_VOL_SQL = (
    "SELECT AVG(volume) FROM option_prices "
    "WHERE option_id=%s AND date BETWEEN %s AND %s"
)
TARGET_DTE = 30


def _load_75plus_call_signals() -> pl.DataFrame:
    from database.models.core import AlgorithmVersion
    vid = AlgorithmVersion.get_active_scores_version().id
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id={vid} AND overall >= " + str(SIGNAL_MIN_SCORE) + " "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2016, end_year=2027, vid=vid,
    )
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date":   [r[1] for r in rows],
        "overall": [int(r[2]) for r in rows],
    })


def _resolve_real(symbol: str, sig_date: date) -> float | None:
    """Nearest-ATM 20-45 DTE call on sig_date -> trailing-30-cal-day avg volume
    for THAT contract. Returns None if no chainable contract (caller falls back).

    TWO-STAGE selection: pick the EXPIRY by total open_interest (descending,
    tie-break nearest-to-target(30) DTE), THEN nearest strike within it.

    Two bugs found and fixed en route (both via manual trace during the smoke
    run, both real-money-relevant since they silently biased the feature
    toward near-zero on liquid large-caps):

    1. A single combined strike+DTE distance let a same-strike tie across
       expiries (weeklies + monthlies often share round strikes) resolve on
       an arbitrary lowest-option_id tie-break -- confirmed on SCHW
       2025-02-13: strike=82 existed at DTE=22 (volume 0 every day, won the
       naive tie-break), DTE=29 (volume 23), DTE=43 (volume 10). Fixed by
       resolving the expiry first, strike second (not one combined metric).

    2. Nearest-CALENDAR-DTE-to-30 is ALSO wrong as the expiry criterion: real
       options liquidity concentrates in the standard monthly cycle, not
       whichever expiry is numerically closest to 30 days. Confirmed on RTX
       2025-02-27: the Mar-21 monthly (DTE=22) had open_interest in the
       hundreds/thousands per strike, while the numerically-closer Mar-28
       weekly (DTE=29) and Apr-4 (DTE=36) had single-digit open_interest and
       ZERO volume (freshly-listed, essentially untraded). Fixed by picking
       the expiry with the MOST total open_interest (an empirical liquidity
       signal) instead of assuming calendar-nearness implies liquidity."""
    rows = DB.execute_sql(_NEAREST_SQL, (symbol, sig_date)).fetchall()
    if not rows:
        return None
    by_exp: dict[tuple, list] = {}
    for oid, strike, exp, dte, close, oi in rows:
        by_exp.setdefault((exp, int(dte)), []).append((int(oid), strike, close, int(oi or 0)))
    best_exp = max(
        by_exp.keys(),
        key=lambda k: (sum(c[3] for c in by_exp[k]), -abs(k[1] - TARGET_DTE)),
    )
    best_id, best_dist = None, None
    for oid, strike, close, _oi in by_exp[best_exp]:
        if close is None or float(close) <= 0:
            continue
        dist = abs(float(strike) / float(close) - 1.0)
        if best_id is None or dist < best_dist or (dist == best_dist and oid < best_id):
            best_id, best_dist = oid, dist
    if best_id is None:
        return None
    win_start = sig_date - timedelta(days=30)
    r = DB.execute_sql(_TRAILING_VOL_SQL, (best_id, win_start, sig_date)).fetchone()
    if not r or r[0] is None:
        return None
    return float(r[0])


def _build_fallback_map(symbols: set[str], start_year: int, end_year: int) -> pl.DataFrame:
    """Bulk PriceHistory pull (mirrors build_liq.py) -> 30-trading-day rolling
    mean raw share volume per symbol, in-memory. NOT per-signal ORM calls.

    Scoped to the actual symbols/years needed (pushed into the SQL itself, not
    filtered after a full-table pull) -- internal alnum tickers only, quoted
    defensively rather than trusted as literal-safe."""
    safe_syms = sorted(s for s in symbols if s and all(c.isalnum() or c in '.-' for c in s))
    if not safe_syms:
        return pl.DataFrame({"symbol": [], "date": [], "eq_vol30": []})
    sym_list = ",".join(f"'{s}'" for s in safe_syms)
    rows = chunked_query_by_year(
        "SELECT symbol, date, volume FROM price_history "
        "WHERE date BETWEEN '{y_start}' AND '{y_end}' AND volume IS NOT NULL "
        f"AND symbol IN ({sym_list})",
        start_year=start_year, end_year=end_year + 1,
    )
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date":   [r[1] for r in rows],
        "volume": [float(r[2]) for r in rows],
    })
    df = df.sort(["symbol", "date"])
    df = df.with_columns(
        pl.col("volume").rolling_mean(window_size=30, min_periods=5).over("symbol").alias("eq_vol30")
    )
    return df.select(["symbol", "date", "eq_vol30"])


def _build() -> pl.DataFrame:
    sig = _load_75plus_call_signals()
    print(f"  {sig.height:,} 75+ call signal-days loaded "
          f"({sig['date'].min()}..{sig['date'].max()})", flush=True)

    real_mask = sig["date"] >= OPTION_COVERAGE_START
    real_sig = sig.filter(real_mask)
    fb_sig = sig.filter(~real_mask)
    print(f"  real-era candidates: {real_sig.height:,}  |  fallback-era: {fb_sig.height:,}", flush=True)

    smoke_n = int(os.environ.get('LIQUIDITY_SMOKE', '0') or '0')
    if smoke_n > 0:
        real_sig = real_sig.head(smoke_n)
        fb_sig = fb_sig.head(smoke_n)
        print(f"  [SMOKE] capping real-era AND fallback-era candidates to first {smoke_n} each", flush=True)

    # --- real-era: per-signal indexed resolution -----------------------------
    out_sym, out_date, out_vol, out_fb = [], [], [], []
    n_real_resolved = 0
    fallthrough_rows = []  # (symbol, date, overall) for real-era misses -> fallback proxy
    for sym, d, ov in zip(real_sig["symbol"].to_list(), real_sig["date"].to_list(), real_sig["overall"].to_list()):
        v = _resolve_real(sym, d)
        if v is not None:
            out_sym.append(sym); out_date.append(d); out_vol.append(v); out_fb.append(False)
            n_real_resolved += 1
        else:
            fallthrough_rows.append((sym, d, ov))  # no chainable contract -> fallback proxy below
    print(f"  real-era resolved: {n_real_resolved:,}/{real_sig.height:,}", flush=True)
    if fallthrough_rows:
        fb_sig = pl.concat([fb_sig, pl.DataFrame({
            "symbol": [r[0] for r in fallthrough_rows],
            "date":   [r[1] for r in fallthrough_rows],
            "overall": [r[2] for r in fallthrough_rows],
        })])

    # --- fallback-era: bulk equity-volume proxy ------------------------------
    if fb_sig.height > 0:
        yrs = fb_sig["date"].map_elements(lambda d: d.year, return_dtype=pl.Int64)
        # start_year-1 headroom so the 30-trading-day rolling window has real
        # lookback even for fb_sig's earliest dates (avoids a truncated/noisy
        # window right at the pulled range's boundary).
        fb_map = _build_fallback_map(set(fb_sig["symbol"].to_list()),
                                      start_year=int(yrs.min()) - 1, end_year=int(yrs.max()))
        joined = fb_sig.join(fb_map, on=["symbol", "date"], how="left")
        n_fb_resolved = 0
        for sym, d, eqv in zip(joined["symbol"].to_list(), joined["date"].to_list(), joined["eq_vol30"].to_list()):
            if eqv is None:
                continue
            out_sym.append(sym); out_date.append(d); out_vol.append(float(eqv) * FALLBACK_EQUITY_FRACTION)
            out_fb.append(True)
            n_fb_resolved += 1
        print(f"  fallback-era resolved: {n_fb_resolved:,}/{fb_sig.height:,}", flush=True)

    return pl.DataFrame({
        "symbol": out_sym, "date": out_date,
        "option_volume_30d": out_vol, "is_fallback": out_fb,
    })


def main():
    name = CACHE_NAME
    smoke_n = int(os.environ.get('LIQUIDITY_SMOKE', '0') or '0')
    if smoke_n > 0:
        name = f"{CACHE_NAME}_smoke{smoke_n}"
    path = materialize_polars(name, _build, force=bool(smoke_n))
    df = pl.read_parquet(path)
    print(f"\noption_volume_30d cache: {df.height:,} rows "
          f"({df['date'].min()}..{df['date'].max()})", flush=True)
    n_fb = int(df['is_fallback'].sum())
    print(f"real={df.height - n_fb:,} ({100*(df.height-n_fb)/max(df.height,1):.1f}%)  "
          f"fallback={n_fb:,} ({100*n_fb/max(df.height,1):.1f}%)", flush=True)

    floors = [10, 25, 50, 100, 250, 500]
    print(f"\n{'slice':16s} {'n':>7s} {'p10':>8s} {'p25':>8s} {'med':>9s} "
          + " ".join(f'<{f}' for f in floors), flush=True)
    for label, sub in [
        ("ALL", df),
        ("real-only", df.filter(~pl.col("is_fallback"))),
        ("fallback-only", df.filter(pl.col("is_fallback"))),
    ]:
        v = sub["option_volume_30d"].drop_nulls()
        if v.len() == 0:
            continue
        fr = [f"{100.0*(v < f).sum()/v.len():4.0f}%" for f in floors]
        print(f"{label:16s} {sub.height:>7d} {v.quantile(0.10):>8.1f} {v.quantile(0.25):>8.1f} "
              f"{v.median():>9.1f} " + " ".join(f'{x:>5s}' for x in fr), flush=True)
    print("\n(<N = fraction of that slice's signals with option_volume_30d < N contracts/day)", flush=True)


if __name__ == "__main__":
    main()
