"""Shared Polygon client + Black-Scholes IV solver for the options-IV ingest.

Used by both `polygon_validate.py` (the free-tier go/no-go gate) and
`polygon_iv_ingest.py` (the real resumable pull). No key is needed to import
this module — the BS/IV helpers run offline and have a self-test at the bottom
(`python polygon_client.py`). The HTTP client reads POLYGON_API_KEY at call time.

Design notes
------------
- Polygon has NO per-date historical IV/greeks endpoint on the retail plans: the
  option *snapshot* (`/v3/snapshot/options/...`) returns `implied_volatility` +
  `greeks`, but only for the CURRENT snapshot. So historical IV is COMPUTED here
  from the option's historical daily close via Black-Scholes (`implied_vol`).
  That is the guaranteed-to-work path and is vendor-agnostic.
- Historical (expired) contracts require `expired=true` on the contracts listing,
  otherwise a 2022 chain returns nothing. `as_of=<date>` returns the chain as it
  existed on that date.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Iterator

import requests

BASE_URL = "https://api.polygon.io"


# --------------------------------------------------------------------------- #
# Black-Scholes price + implied-vol solver (offline, no key needed)
# --------------------------------------------------------------------------- #
_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def bs_price(side: str, S: float, K: float, T: float, sigma: float,
             r: float = 0.04, q: float = 0.0) -> float:
    """Black-Scholes price of a European call/put with continuous div yield q.

    S=spot, K=strike, T=years to expiry, sigma=annualized vol, r=risk-free.
    At T<=0 returns intrinsic. Robust to sigma->0 (returns discounted intrinsic).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        fwd = S * math.exp(-q * max(T, 0.0)) - K * math.exp(-r * max(T, 0.0))
        return max(0.0, fwd) if side == "call" else max(0.0, -fwd)
    vol = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol
    d2 = d1 - vol
    disc_s = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    if side == "call":
        return disc_s * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
    return disc_k * _norm_cdf(-d2) - disc_s * _norm_cdf(-d1)


def implied_vol(side: str, price: float, S: float, K: float, T: float,
                r: float = 0.04, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-5) -> float | None:
    """Invert `bs_price` for sigma via bisection. Returns None when the quoted
    price is below intrinsic / above the no-arb cap (no real solution) or the
    inputs are degenerate. Price is monotonic increasing in sigma, so bisection
    is stable and needs no derivative."""
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    disc_s = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    intrinsic = max(0.0, disc_s - disc_k) if side == "call" else max(0.0, disc_k - disc_s)
    upper_bound = disc_s if side == "call" else disc_k  # BS price asymptote as sigma->inf
    if price <= intrinsic + 1e-9 or price >= upper_bound - 1e-9:
        return None
    p_lo = bs_price(side, S, K, T, lo, r, q)
    p_hi = bs_price(side, S, K, T, hi, r, q)
    if not (p_lo <= price <= p_hi):
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        p_mid = bs_price(side, S, K, T, mid, r, q)
        if abs(p_mid - price) < tol:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
def api_key() -> str:
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "POLYGON_API_KEY is not set. Set it and rerun. "
            "(A free-tier key works for polygon_validate.py; the full ingest "
            "wants the paid Options tier for throughput.)"
        )
    return key


def rate_sleep_seconds(default: float = 0.0) -> float:
    raw = os.environ.get("POLYGON_RATE_SLEEP_SECONDS", "")
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


class PolygonClient:
    """Thin Polygon REST client: paced `get`, retry/backoff on 429+5xx, and a
    `paginate` generator that follows `next_url`.

    `sleep_seconds` paces successive calls (set POLYGON_RATE_SLEEP_SECONDS=13 for
    the 5-calls/min free tier; leave ~0 for the paid tier). Retries honor a
    `Retry-After` header when present."""

    def __init__(self, key: str | None = None, sleep_seconds: float = 0.0,
                 max_retries: int = 5, timeout: int = 30) -> None:
        self.api_key = key or api_key()
        self.sleep_seconds = sleep_seconds
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.calls = 0
        self._last_call = 0.0

    def _pace(self) -> None:
        if self.sleep_seconds <= 0:
            return
        wait = self.sleep_seconds - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def get(self, path_or_url: str, **params: Any) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
        params["apiKey"] = self.api_key
        attempt = 0
        while True:
            self._pace()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Polygon request error after retries: {url} :: {exc}")
                time.sleep(min(2 ** attempt, 30))
                attempt += 1
                continue
            finally:
                self.calls += 1
                self._last_call = time.time()
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Polygon {resp.status_code} after {attempt} retries: {url} "
                        f"body={resp.text[:300]}"
                    )
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after and retry_after.isdigit()) else min(2 ** attempt, 30)
                time.sleep(delay)
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Polygon {resp.status_code}: {url} "
                    f"params={{{', '.join(k for k in params if k != 'apiKey')}}} "
                    f"body={resp.text[:300]}"
                )
            return resp.json()

    def paginate(self, path: str, **params: Any) -> Iterator[dict[str, Any]]:
        """Yield every `results` row across all pages, following `next_url`."""
        data = self.get(path, **params)
        for row in data.get("results", []) or []:
            yield row
        next_url = data.get("next_url")
        while next_url:
            data = self.get(next_url)  # next_url is absolute; apiKey re-appended in get()
            for row in data.get("results", []) or []:
                yield row
            next_url = data.get("next_url")

    # --- domain helpers ---------------------------------------------------- #
    def list_option_contracts(self, underlying: str, as_of: str,
                              exp_gte: str, exp_lte: str,
                              contract_type: str | None = None,
                              limit: int = 1000) -> list[dict[str, Any]]:
        """Option chain for `underlying` as it existed on `as_of`, restricted to
        expirations in [exp_gte, exp_lte].

        IMPORTANT (verified live 2026-07-06): `as_of` is Polygon's point-in-time
        mechanism and ALREADY returns contracts that were active on that date even
        if they have since expired — so it must be used ALONE. Combining `as_of`
        with `expired=true` returns ZERO rows (the two params conflict). Only send
        `expired=true` for the no-`as_of` case (a raw expired listing)."""
        params: dict[str, Any] = {
            "underlying_ticker": underlying,
            "expiration_date.gte": exp_gte,
            "expiration_date.lte": exp_lte,
            "limit": limit,
            "order": "asc",
            "sort": "expiration_date",
        }
        if as_of:
            params["as_of"] = as_of          # point-in-time; do NOT also pass expired
        else:
            params["expired"] = "true"       # raw historical listing needs this
        if contract_type:
            params["contract_type"] = contract_type
        return list(self.paginate("/v3/reference/options/contracts", **params))

    def option_daily_bars(self, option_ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        """Daily OHLC bars for one option contract over [start, end] (inclusive).
        `option_ticker` is Polygon's O:AAPL220617C00150000 form."""
        data = self.get(
            f"/v2/aggs/ticker/{option_ticker}/range/1/day/{start}/{end}",
            adjusted="true", sort="asc", limit=50000,
        )
        return data.get("results", []) or []


def bars_by_date(bars: list[dict[str, Any]]) -> dict[str, float]:
    """Map a Polygon aggregate list -> {YYYY-MM-DD: close}. `t` is ms epoch UTC;
    Polygon stamps a US daily bar at 00:00 ET, so UTC-date == trading date."""
    from datetime import datetime, timezone
    out: dict[str, float] = {}
    for b in bars:
        t = b.get("t")
        c = b.get("c")
        if t is None or c is None:
            continue
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date().isoformat()
        out[d] = float(c)
    return out


# --------------------------------------------------------------------------- #
# offline self-test
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    # Round-trip: price at a known sigma, then recover it.
    for side in ("call", "put"):
        for S, K, T, sig in [(100, 100, 30 / 365, 0.30), (100, 110, 45 / 365, 0.55),
                             (100, 90, 20 / 365, 0.80), (250, 250, 30 / 365, 0.22)]:
            px = bs_price(side, S, K, T, sig, r=0.04, q=0.0)
            iv = implied_vol(side, px, S, K, T, r=0.04, q=0.0)
            assert iv is not None and abs(iv - sig) < 1e-3, (side, S, K, T, sig, px, iv)
    # Below-intrinsic quote has no solution.
    assert implied_vol("call", 0.01, 100, 50, 30 / 365) is None
    # Put-call skew sign: OTM put pricier (higher IV) than OTM call at same |moneyness|.
    S = 100.0
    put_iv = implied_vol("put", bs_price("put", S, 90, 30 / 365, 0.45), S, 90, 30 / 365)
    call_iv = implied_vol("call", bs_price("call", S, 110, 30 / 365, 0.35), S, 110, 30 / 365)
    assert put_iv is not None and call_iv is not None
    assert round(put_iv - call_iv, 3) == 0.100, (put_iv, call_iv)
    print("polygon_client self-test OK (BS price/IV round-trip + skew sign)")


if __name__ == "__main__":
    _self_test()
