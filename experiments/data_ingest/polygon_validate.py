"""Free-tier Polygon options GATE (data-acquisition.md P2.2 step 1).

Answers the one KILL question before any spend: **can Polygon serve per-date,
2021-depth option chains + daily prices for the traded-signals universe?** If it
can, IV is computable via Black-Scholes (see polygon_client) and the paid ingest
is justified. If per-date 2021 chains are NOT available, KILL Polygon and
evaluate ORATS ($99/mo) instead — do NOT buy the $2k L3 depth on faith.

A free-tier key is enough (5 calls/min, 2yr history). Because the free tier caps
history at ~2yr, this probes a RECENT date by default (still proves the chain +
aggregate + IV-compute pipeline end to end); pass `--date 2022-06-15` on a paid
key to prove the full 2021/2022-bear depth the ingest actually needs.

    set POLYGON_API_KEY=<key>
    python experiments/data_ingest/polygon_validate.py            # recent-date probe
    python experiments/data_ingest/polygon_validate.py --date 2022-06-15 --symbol AAPL

Writes .cache/polygon_iv/validation_report.json and prints a PASS/KILL verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE.parent))

from polygon_client import (  # noqa: E402
    PolygonClient, bars_by_date, implied_vol, rate_sleep_seconds,
)

CACHE = _ROOT / ".cache" / "polygon_iv"


def _default_probe_date() -> str:
    # ~90 days back, snapped off weekends — safely inside the free-tier 2yr window.
    d = date.today() - timedelta(days=90)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--date", default=_default_probe_date(),
                    help="historical trade date to probe (YYYY-MM-DD)")
    ap.add_argument("--rate", type=float, default=0.04, help="risk-free rate for the IV sanity solve")
    a = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    client = PolygonClient(sleep_seconds=rate_sleep_seconds(default=13.0))
    sym, d = a.symbol.upper(), a.date

    report: dict = {"symbol": sym, "date": d, "checks": {}, "samples": {}}

    # 1) underlying close on the probe date (needed to pick ATM / OTM strikes)
    ul = client.get(f"/v1/open-close/{sym}/{d}", adjusted="true")
    close = ul.get("close")
    report["checks"]["underlying_close_available"] = close is not None
    report["samples"]["underlying_close"] = close
    if close is None:
        report["verdict"] = "KILL"
        report["reason"] = f"No underlying close for {sym} {d} (pick a trading day / paid tier for old dates)."
        _write(report)
        return 1
    close = float(close)

    # 2) option chain AS OF the historical date, expirations ~20-45 DTE, expired=true
    exp_gte = (datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=20)).isoformat()
    exp_lte = (datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=45)).isoformat()
    contracts = client.list_option_contracts(sym, as_of=d, exp_gte=exp_gte, exp_lte=exp_lte)
    calls = [c for c in contracts if c.get("contract_type") == "call" and c.get("strike_price")]
    puts = [c for c in contracts if c.get("contract_type") == "put" and c.get("strike_price")]
    report["checks"]["chain_as_of_available"] = len(contracts) > 0
    report["samples"]["contracts_in_band"] = len(contracts)
    report["samples"]["calls_in_band"] = len(calls)
    report["samples"]["puts_in_band"] = len(puts)
    if not calls or not puts:
        report["verdict"] = "KILL"
        report["reason"] = (
            "No per-date option chain in the 20-45 DTE band (as_of/expired listing "
            "returned nothing). If this is a 2021/2022 date on a free key it may just "
            "be the 2yr history cap — retry on a paid key before killing."
        )
        _write(report)
        return 1

    # pick nearest-ATM call + ~10% OTM put/call, fetch their bar on the date
    atm = min(calls, key=lambda c: abs(float(c["strike_price"]) - close))
    otm_put = min(puts, key=lambda c: abs(float(c["strike_price"]) - close * 0.90))
    otm_call = min(calls, key=lambda c: abs(float(c["strike_price"]) - close * 1.10))

    def _price_on(contract) -> float | None:
        bars = client.option_daily_bars(contract["ticker"], d, d)
        return bars_by_date(bars).get(d)

    atm_px = _price_on(atm)
    put_px = _price_on(otm_put)
    call_px = _price_on(otm_call)
    report["checks"]["option_daily_price_available"] = atm_px is not None
    report["samples"]["atm"] = {"ticker": atm["ticker"], "strike": atm["strike_price"],
                                "exp": atm["expiration_date"], "close": atm_px}
    report["samples"]["otm_put"] = {"ticker": otm_put["ticker"], "strike": otm_put["strike_price"],
                                    "close": put_px}
    report["samples"]["otm_call"] = {"ticker": otm_call["ticker"], "strike": otm_call["strike_price"],
                                     "close": call_px}

    # 3) does ANY endpoint hand us IV natively (historically)? probe the snapshot.
    native_iv = None
    try:
        snap = client.get(f"/v3/snapshot/options/{sym}/{atm['ticker']}")
        res = snap.get("results", {}) or {}
        native_iv = res.get("implied_volatility")
    except Exception as exc:  # snapshot is current-only; expected to miss for old dates
        report["samples"]["snapshot_error"] = str(exc)[:200]
    report["checks"]["native_iv_available"] = native_iv is not None
    report["samples"]["native_iv_atm"] = native_iv

    # 4) compute IV from the historical price via BS — the path the ingest relies on
    def _T(contract) -> float:
        exp = datetime.strptime(contract["expiration_date"], "%Y-%m-%d").date()
        return (exp - datetime.strptime(d, "%Y-%m-%d").date()).days / 365.0

    computed = {}
    if atm_px:
        computed["atm_iv"] = implied_vol("call", atm_px, close, float(atm["strike_price"]), _T(atm), r=a.rate)
    if put_px:
        computed["otm_put_iv"] = implied_vol("put", put_px, close, float(otm_put["strike_price"]), _T(otm_put), r=a.rate)
    if call_px:
        computed["otm_call_iv"] = implied_vol("call", call_px, close, float(otm_call["strike_price"]), _T(otm_call), r=a.rate)
    if computed.get("otm_put_iv") is not None and computed.get("otm_call_iv") is not None:
        computed["skew"] = round(computed["otm_put_iv"] - computed["otm_call_iv"], 4)
    report["samples"]["computed_iv"] = {k: (round(v, 4) if isinstance(v, float) else v)
                                        for k, v in computed.items()}
    report["checks"]["computed_iv_sane"] = (
        computed.get("atm_iv") is not None and 0.03 <= computed.get("atm_iv", 0) <= 5.0
    )

    # verdict: the ingest only needs (chain as-of) + (daily option price) + (computable IV).
    core_ok = (report["checks"]["chain_as_of_available"]
               and report["checks"]["option_daily_price_available"]
               and report["checks"]["computed_iv_sane"])
    report["verdict"] = "PASS" if core_ok else "KILL"
    report["reason"] = (
        "Per-date chain + daily option price + BS-computable IV all confirmed. "
        "IV source = computed (native_iv "
        + ("present" if native_iv is not None else "absent, as expected historically") + "). "
        "Paid ingest is justified; run polygon_iv_ingest.py."
    ) if core_ok else (
        "Missing one of: per-date chain, daily option price, or a sane computed IV. "
        "If on a free key at an old date, retry paid before killing; else evaluate ORATS $99/mo."
    )
    report["total_api_calls"] = client.calls
    _write(report)
    return 0 if core_ok else 1


def _write(report: dict) -> None:
    out = CACHE / "validation_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\n{'='*60}\nVERDICT: {report.get('verdict')} — {report.get('reason')}\nwrote {out}\n{'='*60}")


if __name__ == "__main__":
    raise SystemExit(main())
