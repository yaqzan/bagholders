"""Polygon options-IV ingest — the resumable pull (data-acquisition.md P2.2 step 2).

!!! KNOWN DEFECT — DO NOT TRUST THIS SCRIPT'S OUTPUT (found 2026-07-25) !!!
This script uses MySQL `price_history.close` as the underlying spot (L128-129), and
feeds it BOTH to the ATM strike selection (L199 `priced(calls, close, ...)`) and to the
Black-Scholes solve (L203 `implied_vol(..., close, strike, ...)`). That column is
SPLIT+DIVIDEND back-adjusted (`trader.py` pulls yfinance with auto_adjust=True) while
option strikes are AS-TRADED. The two are different price spaces.

Measured contamination of `.cache/polygon_iv/iv_ledger_polygon.parquet`:
31.4% of rows drift; 8.6% selected an "ATM" contract actually >5% ITM; WORST in 2022
(44.5% drift >2%, 30.3% >5% ITM) and in CALM/dividend names (symbol-level
spearman(vol, adj_factor) = -0.402). Full audit:
`experiments/polygon_real_premium/PANEL_AUDIT.md`. Trap registry entry:
`.claude/docs/traps.md` "price_history.close is SPLIT+DIVIDEND ADJUSTED".

Corrected replacement: `experiments/polygon_real_premium/rebuild_iv_panel.py`, which uses
`spot_unadj` (yfinance auto_adjust=False x forward split ratios) throughout. Any conclusion
drawn from the old panel -- notably the OSK 2022 backward-OOS read and the gamma+IV M1
"disagrees on calm names" finding -- needs re-measurement on the corrected panel before it
can be relied on.


Extends the ATM-IV + 10%-OTM-skew + forward-option-P&L panel that `experiments/
iv_skew/build_iv.py` builds from the shallow (~1.3y) MySQL `option_prices` back
across ~4 years (2021-2025, incl the 2022 bear) using Polygon, for the SAME
traded-signals universe (`rs_ledger.parquet`, 70+ calls). Output is a parquet
**sidecar** with the SAME schema as `iv_ledger.parquet`, so the downstream OSK /
gamma re-validation (`experiments/iv_skew/build_proxy.py`,
`experiments/year_2024_factor/*`) consumes it by swapping the source path.

HARD INVARIANT (data-acquisition.md + traps.md): vendor data is NEVER written
into MySQL `option_prices`. This only reads price_history (underlying closes) and
writes .cache/polygon_iv/*.parquet.

IV is COMPUTED from each option's historical daily close via Black-Scholes
(polygon_client.implied_vol) — Polygon has no per-date historical IV endpoint.
Run polygon_validate.py FIRST (the free-tier gate) to confirm the pipeline.

Usage
-----
    set POLYGON_API_KEY=<paid Options-tier key>
    python experiments/data_ingest/polygon_iv_ingest.py --smoke 25    # tiny check
    python experiments/data_ingest/polygon_iv_ingest.py               # full 2021-25 70+

Resumable: every (symbol,date) result is appended to
.cache/polygon_iv/_ingest_progress.jsonl; a re-run skips pairs already logged and
periodically rewrites iv_ledger_polygon.parquet. Kill and restart freely.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE.parent))

from polygon_client import (  # noqa: E402
    PolygonClient, bars_by_date, implied_vol, rate_sleep_seconds,
)

LEDGER = _ROOT / ".cache" / "rel_strength" / "rs_ledger.parquet"
CACHE = _ROOT / ".cache" / "polygon_iv"
PROGRESS = CACHE / "_ingest_progress.jsonl"
OUT = CACHE / "iv_ledger_polygon.parquet"

# Final columns mirror experiments/iv_skew/build_iv.py's iv_ledger exactly, so the
# downstream proxy/OSK scripts read the sidecar unchanged. Extra diagnostic cols
# (otm_put_iv, otm_call_iv, dte, atm_strike) ride along harmlessly.
CORE_COLS = ["symbol", "date", "overall", "vol_pct", "atm_iv", "entry_premium",
             "iv_rv", "skew", "pnl15", "pnl_max", "pnl_min"]


def _dt(s: str):
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def load_universe(min_overall: int, start: str, end: str,
                  symbols: set[str] | None) -> list[tuple[str, str, int, float | None]]:
    led = pl.read_parquet(LEDGER)
    win = led.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end) & (pl.col("overall") >= min_overall)
    ).select(["symbol", "date", "overall", "vol_pct"]).unique(subset=["symbol", "date"])
    win = win.sort(["date", "symbol"])
    rows = list(zip(win["symbol"].to_list(), win["date"].to_list(),
                    win["overall"].to_list(), win["vol_pct"].to_list()))
    if symbols:
        rows = [r for r in rows if r[0] in symbols]
    return rows


def load_done() -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if PROGRESS.exists():
        for line in PROGRESS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["symbol"], r["date"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


class Closes:
    """Underlying daily closes, sourced from MySQL price_history (fast, local,
    free), with a Polygon open-close fallback for any missing (symbol,date).
    Per-symbol closes are loaded once and cached."""

    def __init__(self, client: PolygonClient):
        self.client = client
        self._by_symbol: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()
        try:
            from database.trader_database import DB  # noqa
            self._DB = DB
        except Exception as exc:  # DB optional — falls back entirely to Polygon
            print(f"[warn] price_history DB unavailable ({exc!r}); using Polygon for underlying closes", flush=True)
            self._DB = None

    def preload(self, symbols: list[str]) -> None:
        """Load every symbol's closes single-threaded UP FRONT. Required before a
        threaded run: PyMySQL isn't thread-safe, so no DB access may happen inside
        worker threads — after preload, get() only reads the cache + Polygon HTTP."""
        for i, sym in enumerate(symbols):
            if sym not in self._by_symbol:
                self._by_symbol[sym] = self._load_symbol(sym)
            if (i + 1) % 150 == 0:
                print(f"  preloaded closes {i+1}/{len(symbols)} symbols", flush=True)

    def _load_symbol(self, sym: str) -> dict[str, float]:
        m: dict[str, float] = {}
        if self._DB is not None:
            try:
                cur = self._DB.execute_sql(
                    "SELECT date, close FROM price_history WHERE symbol=%s", (sym,))
                for d, c in cur.fetchall():
                    if c is None:
                        continue
                    ds = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
                    m[ds] = float(c)
            except Exception as exc:
                print(f"[warn] price_history query failed for {sym}: {exc!r}", flush=True)
        return m

    def get(self, sym: str, d: str) -> float | None:
        if sym not in self._by_symbol:
            self._by_symbol[sym] = self._load_symbol(sym)
        c = self._by_symbol[sym].get(d)
        if c is not None:
            return c
        try:  # Polygon fallback for a hole (HTTP is thread-safe; guard the cache write)
            oc = self.client.get(f"/v1/open-close/{sym}/{d}", adjusted="true")
            close = oc.get("close")
            if close is not None:
                close = float(close)
                with self._lock:
                    self._by_symbol.setdefault(sym, {})[d] = close
                return close
        except Exception:
            pass
        return None


def nearest(contracts, target: float):
    return min(contracts, key=lambda c: abs(float(c["strike_price"]) - target)) if contracts else None


def process_one(client: PolygonClient, closes: Closes, sym: str, d: str,
                overall: int, vol_pct: float | None, args) -> dict:
    """Return a progress record: {symbol,date,status, ...rec fields}. status is
    'kept' or 'miss:<reason>'. Never raises for expected data holes."""
    base = {"symbol": sym, "date": d, "overall": int(overall),
            "vol_pct": (float(vol_pct) if vol_pct is not None else None)}
    close = closes.get(sym, d)
    if close is None or close <= 0:
        return {**base, "status": "miss:no_underlying"}

    exp_gte = (_dt(d) + timedelta(days=args.dte_lo)).isoformat()
    exp_lte = (_dt(d) + timedelta(days=args.dte_hi)).isoformat()
    contracts = client.list_option_contracts(sym, as_of=d, exp_gte=exp_gte, exp_lte=exp_lte)
    calls = [c for c in contracts if c.get("contract_type") == "call" and c.get("strike_price")]
    puts = [c for c in contracts if c.get("contract_type") == "put" and c.get("strike_price")]
    if not calls or not puts:
        return {**base, "status": "miss:no_chain"}

    def T(contract) -> float:
        exp = _dt(contract["expiration_date"])
        return max((exp - _dt(d)).days / 365.0, 1e-6)

    fwd_end = (_dt(d) + timedelta(days=24)).isoformat()

    def priced(candidates, target: float, end: str):
        """Walk out from `target` strike; return (contract, bars_map) for the first
        whose contract actually TRADED on `d`. Illiquid OTM strikes frequently have
        no daily bar on a given date, so the single-nearest pick silently drops skew
        — trying the nearest few (unlimited calls on Developer) restores coverage."""
        ordered = sorted(candidates, key=lambda x: abs(float(x["strike_price"]) - target))
        for c in ordered[:args.strike_tries]:
            bars = bars_by_date(client.option_daily_bars(c["ticker"], d, end))
            if bars.get(d, 0) > 0:
                return c, bars
        return None, None

    # ATM call: nearest priced call to spot, carrying the forward path
    atm, atm_bars = priced(calls, close, fwd_end)
    if atm is None:
        return {**base, "status": "miss:no_atm_price"}
    entry = atm_bars[d]
    atm_iv = implied_vol("call", entry, close, float(atm["strike_price"]), T(atm),
                         r=args.rate, q=args.div)
    if atm_iv is None:
        return {**base, "status": "miss:atm_iv_unsolved"}

    # ±10% OTM legs (nearest priced strike to each target) -> skew = put_iv - call_iv
    otm_put, put_bars = priced(puts, close * (1.0 + args.otm_frac_put), d)
    otm_call, call_bars = priced(calls, close * (1.0 + args.otm_frac_call), d)
    put_iv = (implied_vol("put", put_bars[d], close, float(otm_put["strike_price"]), T(otm_put),
                          r=args.rate, q=args.div) if otm_put else None)
    call_iv = (implied_vol("call", call_bars[d], close, float(otm_call["strike_price"]), T(otm_call),
                           r=args.rate, q=args.div) if otm_call else None)
    skew = round(put_iv - call_iv, 4) if (put_iv is not None and call_iv is not None) else None

    # forward option P&L (mirror build_iv.fwd_pnl: strictly-forward bars, 15th bar).
    # Require >=15 forward bars; otherwise leave pnl15/pmax/pmin as None -- an
    # unresolved 15-bar horizon is never truncate-substituted with the last available
    # price (matches build_iv.fwd_pnl / osk_era build_osk_ledger.fwd_pnl).
    fwd = [px for dd, px in sorted(atm_bars.items()) if d < dd <= fwd_end and px > 0]
    pnl15 = pmax = pmin = None
    if len(fwd) >= 15:
        p15 = fwd[14]  # 15th trading bar fwd (0-indexed 14)
        pnl15 = round(p15 / entry - 1.0, 4)
        pmax = round(max(fwd[:15]) / entry - 1.0, 4)
        pmin = round(min(fwd[:15]) / entry - 1.0, 4)

    rv_ann = (float(vol_pct) / 100.0) * math.sqrt(252) if vol_pct else None
    iv_rv = round(atm_iv / rv_ann, 4) if (rv_ann and rv_ann > 1e-6) else None

    return {
        **base, "status": "kept",
        "atm_iv": round(atm_iv, 4), "entry_premium": round(entry, 4), "iv_rv": iv_rv,
        "skew": skew, "pnl15": pnl15, "pnl_max": pmax, "pnl_min": pmin,
        "otm_put_iv": (round(put_iv, 4) if put_iv is not None else None),
        "otm_call_iv": (round(call_iv, 4) if call_iv is not None else None),
        "atm_strike": float(atm["strike_price"]),
        "dte": (_dt(atm["expiration_date"]) - _dt(d)).days,
    }


def consolidate() -> int:
    """Rebuild iv_ledger_polygon.parquet from the kept rows in the progress log."""
    if not PROGRESS.exists():
        return 0
    recs = []
    for line in PROGRESS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("status") == "kept":
            recs.append({k: v for k, v in r.items() if k != "status"})
    if not recs:
        return 0
    df = pl.DataFrame(recs, infer_schema_length=None)
    front = [c for c in CORE_COLS if c in df.columns]
    df = df.select(front + [c for c in df.columns if c not in front])
    df.write_parquet(OUT)
    return df.height


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-overall", type=int, default=70,
                    help="score floor (70 matches build_iv; OSK ship cohort is 75)")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--symbols", default="", help="comma-separated subset filter")
    ap.add_argument("--rate", type=float, default=0.04, help="risk-free rate for BS IV")
    ap.add_argument("--div", type=float, default=0.0, help="continuous dividend yield for BS IV")
    ap.add_argument("--dte-lo", type=int, default=20)
    ap.add_argument("--dte-hi", type=int, default=45)
    ap.add_argument("--otm-frac-put", type=float, default=-0.10)
    ap.add_argument("--otm-frac-call", type=float, default=0.10)
    ap.add_argument("--strike-tries", type=int, default=6,
                    help="how many nearest strikes to try per leg until one traded that day")
    ap.add_argument("--smoke", type=int, default=0, help="process only the first N pairs")
    ap.add_argument("--limit", type=int, default=0, help="cap pairs processed this run (0=all)")
    ap.add_argument("--flush-every", type=int, default=200)
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent Polygon fetchers (Developer tier is unlimited-calls; 8 is safe)")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="just rebuild the parquet from the progress log and exit")
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)

    if args.consolidate_only:
        n = consolidate()
        print(f"consolidated {n} kept rows -> {OUT}")
        return 0

    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    universe = load_universe(args.min_overall, args.start, args.end, symbols)
    done = load_done()
    todo = [r for r in universe if (r[0], r[1]) not in done]
    if args.smoke:
        todo = todo[:args.smoke]
    if args.limit:
        todo = todo[:args.limit]

    print(f"universe={len(universe)} done={len(done)} todo={len(todo)} "
          f"(min_overall={args.min_overall}, {args.start}..{args.end})", flush=True)
    if not todo:
        n = consolidate()
        print(f"nothing to do; parquet has {n} rows -> {OUT}")
        return 0

    client = PolygonClient(sleep_seconds=rate_sleep_seconds(default=0.0))
    closes = Closes(client)
    t0 = time.time()
    ctr = {"kept": 0, "miss": 0, "done": 0}
    log = PROGRESS.open("a", encoding="utf-8")
    log_lock = threading.Lock()

    def record(rec: dict) -> None:
        with log_lock:
            log.write(json.dumps(rec) + "\n")
            log.flush()
            ctr["kept" if rec.get("status") == "kept" else "miss"] += 1
            ctr["done"] += 1
            if ctr["done"] % args.flush_every == 0:
                n = consolidate()
                rate = ctr["done"] / max(time.time() - t0, 1e-6)
                print(f"  {ctr['done']}/{len(todo)} kept={ctr['kept']} miss={ctr['miss']} "
                      f"parquet={n} calls={client.calls} {rate:.1f}/s", flush=True)

    def run_one(sig) -> None:
        sym, d, overall, vol_pct = sig
        try:
            rec = process_one(client, closes, sym, d, overall, vol_pct, args)
        except Exception as exc:  # unexpected — record as miss, keep going
            rec = {"symbol": sym, "date": d, "status": f"miss:err:{repr(exc)[:80]}"}
        record(rec)

    if args.workers > 1:
        # PyMySQL isn't thread-safe -> load all closes single-threaded, THEN fan out
        # (workers do only Polygon HTTP + cache reads + locked jsonl writes).
        syms = sorted({r[0] for r in todo})
        print(f"preloading closes for {len(syms)} symbols (single-threaded)...", flush=True)
        closes.preload(syms)
        print(f"fanning out {len(todo)} signals across {args.workers} workers", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_one, todo))
    else:
        for sig in todo:
            run_one(sig)
    log.close()

    n = consolidate()
    print(f"DONE this run: kept={ctr['kept']} miss={ctr['miss']} api_calls={client.calls} "
          f"in {time.time()-t0:.0f}s -> {OUT} (total kept rows={n})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
