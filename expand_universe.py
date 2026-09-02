"""
expand_universe.py — Add S&P 400 + S&P 600 stocks to the Trader database.

Steps:
  1. Fetch S&P 400 and S&P 600 constituent lists from Wikipedia.
  2. Skip symbols already in the database (safe to re-run / resume).
  3. For each new symbol, check the option chain for sufficient Open Interest.
  4. If OI passes the threshold, run the full pull_stock pipeline.

Usage:
    python expand_universe.py [--min-oi MIN_OI] [--dry-run]

Options:
    --min-oi    Minimum total Open Interest across the nearest 2 expirations
                (calls + puts combined). Default: 500.
    --dry-run   Print qualifying symbols without pulling them.
"""

import sys
import argparse
import time
import traceback
import io

import requests
import pandas as pd
import yfinance as yf
from colorama import init, Fore, Style
import threading

init(autoreset=True, convert=True)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Wikipedia URLs for each index ────────────────────────────────────────────
SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
SP600_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_MIN_OI  = 500
NEAR_EXPIRATIONS = 2    # how many near-term expirations to sum OI over
RATE_LIMIT_SLEEP = 0.3  # seconds between yfinance calls
OI_CHECK_TIMEOUT = 20   # hard timeout (s) for the OI fetch thread
PULL_TIMEOUT     = 180  # hard timeout (s) for the full pull_stock thread


def fetch_index_symbols(url: str, index_name: str) -> list[str]:
    """Read constituent symbols from a Wikipedia index table."""
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    html = io.StringIO(resp.text)

    try:
        tables = pd.read_html(html, attrs={"id": "constituents"})
    except Exception:
        html.seek(0)
        tables = pd.read_html(html)

    for tbl in tables:
        cols = [c.strip() for c in tbl.columns]
        sym_col = next((c for c in cols if c.lower() in ("symbol", "ticker")), None)
        if sym_col:
            symbols = tbl[sym_col].dropna().str.strip().str.upper().tolist()
            symbols = [s.replace(".", "-") for s in symbols]
            print(Fore.CYAN + f"  {index_name}: {len(symbols)} constituents found")
            return symbols

    print(Fore.RED + f"  {index_name}: could not parse constituent table from {url}")
    return []


# ── OI check ─────────────────────────────────────────────────────────────────

def _run_with_timeout(fn, timeout: int, *args) -> tuple[bool, any, bool]:
    """
    Run fn(*args) in a daemon thread.  Returns (completed, result, timed_out).
    Daemon threads are abandoned on timeout — they don't block the main thread.
    """
    result_box = [None]
    exc_box    = [None]

    def target():
        try:
            result_box[0] = fn(*args)
        except Exception as e:
            exc_box[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        return False, None, True   # timed out — thread is abandoned (daemon)
    if exc_box[0] is not None:
        raise exc_box[0]
    return True, result_box[0], False


def _fetch_oi(symbol: str, near_exp: int) -> int:
    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        return 0
    total_oi = 0
    for exp in expirations[:near_exp]:
        chain = ticker.option_chain(exp)
        calls_oi = chain.calls["openInterest"].fillna(0).sum()
        puts_oi  = chain.puts["openInterest"].fillna(0).sum()
        total_oi += int(calls_oi + puts_oi)
    return total_oi


def check_option_oi(symbol: str, min_oi: int, near_exp: int) -> tuple[bool, int]:
    try:
        completed, total_oi, timed_out = _run_with_timeout(_fetch_oi, OI_CHECK_TIMEOUT, symbol, near_exp)
        if timed_out:
            print(Fore.YELLOW + f"    [OI check timed out for {symbol}]")
            return False, 0
        return total_oi >= min_oi, total_oi
    except Exception as e:
        print(Fore.YELLOW + f"    [OI check failed for {symbol}]: {e}")
        return False, 0


# ── Pull stock ────────────────────────────────────────────────────────────────

def _do_pull(client, symbol: str):
    client.pull_stock(symbol)


def pull_symbol(client, symbol: str) -> str:
    """Run pull_stock in a daemon thread with a hard timeout. Returns 'ok'/'timeout'/'error'."""
    try:
        completed, _, timed_out = _run_with_timeout(_do_pull, PULL_TIMEOUT, client, symbol)
        if timed_out:
            print(Fore.RED + f"  TIMEOUT pulling {symbol} (>{PULL_TIMEOUT}s) — skipping")
            return "timeout"
        return "ok"
    except Exception:
        print(Fore.RED + f"  ERROR pulling {symbol}:")
        traceback.print_exc()
        return "error"


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_existing_symbols() -> set[str]:
    """Symbols already in the stocks table."""
    from database import Stock
    return {s.symbol for s in Stock.select(Stock.symbol)}


def has_price_history(symbol: str) -> bool:
    """True if this symbol already has at least one price history row."""
    from database.models.technical import PriceHistory
    return PriceHistory.select().where(PriceHistory.symbol == symbol).exists()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Expand universe with S&P 400 + S&P 600 stocks")
    parser.add_argument("--min-oi", type=int, default=DEFAULT_MIN_OI,
                        help=f"Min total OI across nearest {NEAR_EXPIRATIONS} expirations (default {DEFAULT_MIN_OI})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print qualifying symbols without pulling them into the DB")
    args = parser.parse_args()

    from database.project_root import chdir_trader_project
    chdir_trader_project()

    from trader import Trader

    print(Style.BRIGHT + "\n=== Expand Universe: S&P 400 + S&P 600 ===\n")
    print(f"  Min OI threshold : {args.min_oi:,}")
    print(f"  Pull timeout     : {PULL_TIMEOUT}s per symbol")
    print(f"  Dry run          : {args.dry_run}")
    print()

    # 1. Fetch constituents
    print(Fore.WHITE + Style.BRIGHT + "Fetching index constituents ...")
    sp400 = fetch_index_symbols(SP400_URL, "S&P 400")
    sp600 = fetch_index_symbols(SP600_URL, "S&P 600")
    candidates = list(dict.fromkeys(sp400 + sp600))
    print(f"  Combined unique  : {len(candidates)} symbols\n")

    # 2. Filter: skip if already has price history (supports resume)
    print(Fore.WHITE + Style.BRIGHT + "Checking existing database ...")
    existing_stocks = get_existing_symbols()
    # A symbol is "done" if it has price history (may have been added in a prior run)
    new_symbols = [s for s in candidates if not has_price_history(s)]
    already_done = len(candidates) - len(new_symbols)
    print(f"  Already have price history : {already_done} symbols (skipping)")
    print(f"  New to evaluate            : {len(new_symbols)} symbols\n")

    if not new_symbols:
        print(Fore.GREEN + "Nothing new to add. Done.")
        return

    # 3 & 4. OI check → pull
    print(Fore.WHITE + Style.BRIGHT + f"Checking options OI + pulling ({len(new_symbols)} symbols) ...\n")

    client = Trader()
    passed, skipped_oi, timed_out, errors = [], [], [], []

    for i, symbol in enumerate(new_symbols, 1):
        # Re-check in case a prior pull in THIS run completed (e.g. after a restart)
        if has_price_history(symbol):
            print(Fore.CYAN + f"  [{i:>4}/{len(new_symbols)}] {symbol:<8}  already pulled, skipping")
            passed.append(symbol)
            continue

        prefix = f"[{i:>4}/{len(new_symbols)}] {symbol:<8}"

        ok, total_oi = check_option_oi(symbol, args.min_oi, NEAR_EXPIRATIONS)
        time.sleep(RATE_LIMIT_SLEEP)

        if not ok:
            oi_str = f"{total_oi:,}" if total_oi else "no chain"
            print(Fore.YELLOW + f"  {prefix}  SKIP  OI={oi_str}  (threshold {args.min_oi:,})")
            skipped_oi.append(symbol)
            continue

        print(Fore.GREEN + f"  {prefix}  OI={total_oi:,}  OK")

        if args.dry_run:
            passed.append(symbol)
            continue

        result = pull_symbol(client, symbol)
        if result == "ok":
            passed.append(symbol)
        elif result == "timeout":
            timed_out.append(symbol)
        else:
            errors.append(symbol)

        time.sleep(RATE_LIMIT_SLEEP)

    # Summary
    print()
    print(Style.BRIGHT + "=== Summary ===")
    action = "Qualified" if args.dry_run else "Pulled"
    print(Fore.GREEN  + f"  {action}           : {len(passed)}")
    print(Fore.YELLOW + f"  Skipped (low OI) : {len(skipped_oi)}")
    if timed_out:
        print(Fore.YELLOW + f"  Timed out        : {len(timed_out)}  ({', '.join(timed_out)})")
    if errors:
        print(Fore.RED   + f"  Errors           : {len(errors)}  ({', '.join(errors)})")
    print()

    if args.dry_run and passed:
        print(Fore.CYAN + "Qualifying symbols (would be pulled without --dry-run):")
        print("  " + ", ".join(passed))


if __name__ == "__main__":
    main()
