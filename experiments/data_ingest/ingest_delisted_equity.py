"""
SCAFFOLD — delisted-equity ingest (the survivorship fix for the dot-com/GFC test).
Complete the vendor column-map + verify the price-adjustment convention against a
KNOWN ticker before a real (--commit) run.

Loads a survivorship-bias-free equity export (default: Sharadar SEP schema; Tiingo
works with the alt column-map) into `price_history`, and creates `Stock` rows for
tickers NOT in today's universe with `delisted_date` set to their last bar — so
they are present for the 1995-2026 backtest but excluded from LIVE scoring
(core.py caps each stock's effective end date at delisted_date). Run
breadth-backfill / regime-backfill / recalculate afterward to fold them in.

Usage:
  python ingest_delisted_equity.py <export.csv> [--vendor sharadar|tiingo]   # DRY-RUN
  python ingest_delisted_equity.py <export.csv> --vendor sharadar --commit   # writes DB
"""
import sys, csv, argparse
from datetime import datetime
sys.path.insert(0, r"C:\Development\Trader")
from database.models.core import Stock
from database.models.technical import PriceHistory

# --- Vendor column maps — VERIFY against your actual export header ------------
VENDORS = {
    # Sharadar SEP: ticker,date,open,high,low,close,volume,closeadj,closeunadj,lastupdated
    "sharadar": dict(ticker="ticker", date="date", open="open", high="high",
                     low="low", close="close", volume="volume", closeadj="closeadj"),
    # Tiingo EOD: ...,adjOpen,adjHigh,adjLow,adjClose,adjVolume (already adjusted -> no factor)
    "tiingo":   dict(ticker="ticker", date="date", open="adjOpen", high="adjHigh",
                     low="adjLow", close="adjClose", volume="adjVolume", closeadj=None),
}
DELISTED_GRACE_DAYS = 10  # a ticker whose last bar is >10d before the file's max date => delisted

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--vendor", default="sharadar", choices=list(VENDORS))
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    cm = VENDORS[a.vendor]

    rows_by_tkr = {}
    with open(a.export, newline="") as f:
        for r in csv.DictReader(f):
            rows_by_tkr.setdefault(r[cm["ticker"]].upper(), []).append(r)
    def _d(s): return datetime.strptime(s[:10], "%Y-%m-%d").date()
    file_max = max(_d(r[cm["date"]]) for rs in rows_by_tkr.values() for r in rs)
    universe = {s.symbol for s in Stock.select(Stock.symbol)}

    n_new = n_delisted = n_bars = 0
    for tkr, rs in rows_by_tkr.items():
        bars = []
        for r in sorted(rs, key=lambda x: x[cm["date"]]):
            d = _d(r[cm["date"]])
            o, h, l, c = (float(r[cm[k]]) for k in ("open", "high", "low", "close"))
            v = int(float(r[cm["volume"]]))
            # Adjust as-reported OHLC to split+div-adjusted (yfinance auto_adjust convention)
            # via closeadj/close. Tiingo adj* cols are pre-adjusted -> factor 1.0.
            # >>> VERIFY on a known ticker (e.g. AAPL 2000 split) that this matches your stored data.
            if cm.get("closeadj"):
                ca = float(r[cm["closeadj"]]); fac = (ca / c) if c else 1.0
                o, h, l, c = o * fac, h * fac, l * fac, ca
            bars.append((d, o, h, l, c, v))
        last = bars[-1][0]
        is_delisted = (file_max - last).days > DELISTED_GRACE_DAYS
        if tkr not in universe:
            n_new += 1
            if is_delisted:
                n_delisted += 1
            if a.commit:
                # NOTE: Stock may have other required/NOT NULL columns — fill via defaults{} as needed.
                st, _ = Stock.get_or_create(symbol=tkr, defaults={"name": tkr})
                if is_delisted:
                    st.delisted_date = last
                    st.save()
        n_bars += len(bars)
        if a.commit:
            # NOTE: confirm PriceHistory.bulk_build's expected row format (dict vs tuple).
            PriceHistory.bulk_build(
                tkr,
                [dict(date=d, open=o, high=h, low=l, close=c, volume=v) for d, o, h, l, c, v in bars],
                refresh_weekly=False,
            )

    print(f"{'COMMIT' if a.commit else 'DRY-RUN'}: {len(rows_by_tkr)} tickers, {n_bars:,} bars, "
          f"{n_new} not-in-universe ({n_delisted} delisted -> flagged out of live scoring)")
    print("Next: trader breadth-backfill / regime-backfill / recalculate  (then re-run the deep research pack).")

if __name__ == "__main__":
    main()
