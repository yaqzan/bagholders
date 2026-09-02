"""Free-tier Polygon schema probe for legacy OOS research.

This script validates the data shapes needed before paying for all-history
access. It intentionally pulls only a tiny recent sample and writes parquet/JSON
artifacts under `.cache/legacy_oos/probe/`.

Required:
    set POLYGON_API_KEY=<key>
    python experiments/legacy_oos/polygon_probe.py

Optional:
    set POLYGON_RATE_SLEEP_SECONDS=13
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import requests


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache" / "legacy_oos" / "probe"
BASE_URL = "https://api.polygon.io"


def _api_key() -> str:
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "POLYGON_API_KEY is not set. Set it and rerun "
            "`python experiments/legacy_oos/polygon_probe.py`."
        )
    return key


def _rate_sleep() -> float:
    raw = os.environ.get("POLYGON_RATE_SLEEP_SECONDS", "13")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 13.0


class PolygonClient:
    def __init__(self, api_key: str, sleep_seconds: float) -> None:
        self.api_key = api_key
        self.sleep_seconds = sleep_seconds
        self.calls = 0

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        if self.calls:
            time.sleep(self.sleep_seconds)
        params["apiKey"] = self.api_key
        url = f"{BASE_URL}{path}"
        resp = requests.get(url, params=params, timeout=30)
        self.calls += 1
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Polygon request failed {resp.status_code}: {url} "
                f"params={{{', '.join(k for k in params if k != 'apiKey')}}} "
                f"body={resp.text[:500]}"
            )
        return resp.json()


def _ticker_overview(client: PolygonClient, ticker: str) -> dict[str, Any]:
    return client.get(f"/v3/reference/tickers/{ticker}")


def _recent_bars(client: PolygonClient, ticker: str) -> list[dict[str, Any]]:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=45)
    data = client.get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        adjusted="true",
        sort="asc",
        limit=5000,
    )
    return data.get("results", []) or []


def _inactive_ticker(client: PolygonClient) -> dict[str, Any] | None:
    data = client.get(
        "/v3/reference/tickers",
        market="stocks",
        active="false",
        order="asc",
        sort="ticker",
        limit=10,
    )
    results = data.get("results", []) or []
    return results[0] if results else None


def _bars_frame(rows: list[dict[str, Any]], ticker: str) -> pl.DataFrame:
    out = []
    for row in rows:
        ts = row.get("t")
        out.append(
            {
                "ticker": ticker,
                "date": (
                    date.fromtimestamp(ts / 1000).isoformat()
                    if isinstance(ts, (int, float))
                    else None
                ),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v"),
                "vwap": row.get("vw"),
                "transactions": row.get("n"),
            }
        )
    return pl.DataFrame(out)


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    client = PolygonClient(_api_key(), _rate_sleep())

    tickers = ["AAPL", "MSFT", "SPY"]
    overviews = []
    bars_frames = []

    print("[probe] fetching inactive ticker sample", flush=True)
    inactive = _inactive_ticker(client)
    if inactive and inactive.get("ticker"):
        tickers.append(str(inactive["ticker"]))

    for ticker in tickers:
        print(f"[probe] fetching overview {ticker}", flush=True)
        overview = _ticker_overview(client, ticker)
        result = overview.get("results", {}) or {}
        result["probe_ticker"] = ticker
        overviews.append(result)

        print(f"[probe] fetching recent bars {ticker}", flush=True)
        bars_frames.append(_bars_frame(_recent_bars(client, ticker), ticker))

    overview_df = pl.DataFrame(overviews)
    bars_df = pl.concat(bars_frames, how="diagonal") if bars_frames else pl.DataFrame()

    overview_path = CACHE / "ticker_overviews.parquet"
    bars_path = CACHE / "recent_day_bars.parquet"
    report_path = CACHE / "probe_report.json"
    overview_df.write_parquet(overview_path)
    bars_df.write_parquet(bars_path)

    report = {
        "calls": client.calls,
        "tickers": tickers,
        "overview_rows": len(overview_df),
        "overview_columns": overview_df.columns,
        "bar_rows": len(bars_df),
        "bar_columns": bars_df.columns,
        "outputs": {
            "ticker_overviews": str(overview_path.relative_to(ROOT)),
            "recent_day_bars": str(bars_path.relative_to(ROOT)),
        },
        "checks": {
            "has_inactive_sample": inactive is not None,
            "has_delisted_field": "delisted_utc" in overview_df.columns,
            "has_market_cap_field": "market_cap" in overview_df.columns,
            "has_share_count_field": (
                "share_class_shares_outstanding" in overview_df.columns
            ),
            "has_daily_ohlcv": all(
                col in bars_df.columns
                for col in ["open", "high", "low", "close", "volume"]
            ),
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
