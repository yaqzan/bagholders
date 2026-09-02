"""
expand_options_active.py — Add high options-activity tickers from Yahoo Finance.

Pulls Yahoo's options screener pages (most-active, highest-OI, highest-IV),
filters out symbols already in the DB, runs the same OI check + pull pipeline
as expand_universe.py.

Usage:
    python expand_options_active.py [--min-oi MIN_OI] [--count N] [--dry-run]
"""

import argparse
import re
import time

import requests
from colorama import init, Fore, Style

from expand_universe import (
    check_option_oi,
    pull_symbol,
    get_existing_symbols,
    has_price_history,
    NEAR_EXPIRATIONS,
    RATE_LIMIT_SLEEP,
    DEFAULT_MIN_OI,
)

init(autoreset=True, convert=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

YAHOO_PAGES = [
    ("most-active",  "https://finance.yahoo.com/markets/options/most-active/"),
    ("highest-oi",   "https://finance.yahoo.com/markets/options/highest-open-interest/"),
    ("highest-iv",   "https://finance.yahoo.com/markets/options/highest-implied-volatility/"),
]

QUOTE_RE = re.compile(r'/quote/([A-Z][A-Z0-9.\-]{0,9})(?=[/"?])')

# Yahoo's quote pages render symbols whose suffix indicates non-equity asset
# classes we don't want (crypto, FX, futures, indices).
EXCLUDE_SUFFIXES = ("-USD", "=F", "=X")
EXCLUDE_PREFIXES = ("^",)


def fetch_yahoo_page(url: str, count: int) -> list[str]:
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    resp = requests.get(url, params={"count": count}, headers=headers, timeout=30)
    resp.raise_for_status()
    raw = set(QUOTE_RE.findall(resp.text))
    out = []
    for s in raw:
        if any(s.endswith(suf) for suf in EXCLUDE_SUFFIXES):
            continue
        if any(s.startswith(pre) for pre in EXCLUDE_PREFIXES):
            continue
        out.append(s.replace(".", "-"))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description="Expand universe with high options-activity tickers from Yahoo Finance")
    ap.add_argument("--min-oi", type=int, default=DEFAULT_MIN_OI,
                    help=f"Min total OI across nearest {NEAR_EXPIRATIONS} expirations (default {DEFAULT_MIN_OI})")
    ap.add_argument("--count", type=int, default=250,
                    help="Yahoo per-page row count (max ~250 useful, default 250)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print qualifying symbols without pulling them")
    args = ap.parse_args()

    from database.project_root import chdir_trader_project
    chdir_trader_project()
    from trader import Trader

    print(Style.BRIGHT + "\n=== Expand Universe: High Options Activity (Yahoo Finance) ===\n")
    print(f"  Min OI threshold : {args.min_oi:,}")
    print(f"  Per-page count   : {args.count}")
    print(f"  Dry run          : {args.dry_run}\n")

    print(Fore.WHITE + Style.BRIGHT + "Fetching Yahoo options screener pages ...")
    candidates: list[str] = []
    seen: set[str] = set()
    for name, url in YAHOO_PAGES:
        try:
            syms = fetch_yahoo_page(url, args.count)
        except Exception as e:
            print(Fore.YELLOW + f"  {name:20s} ERROR: {e}")
            continue
        new_in_page = [s for s in syms if s not in seen]
        seen.update(syms)
        candidates.extend(new_in_page)
        print(Fore.CYAN + f"  {name:20s} {len(syms):>4} symbols  (+{len(new_in_page)} new to union)")
        time.sleep(0.5)

    print(f"  Combined unique  : {len(candidates)}\n")

    if not candidates:
        print(Fore.RED + "No symbols returned from Yahoo. Check network or page structure.")
        return

    print(Fore.WHITE + Style.BRIGHT + "Filtering against existing DB ...")
    existing = get_existing_symbols()
    new_symbols = [s for s in candidates if s not in existing and not has_price_history(s)]
    print(f"  Already in DB    : {len(candidates) - len(new_symbols)}")
    print(f"  New to evaluate  : {len(new_symbols)}\n")

    if not new_symbols:
        print(Fore.GREEN + "Nothing new to add. Done.")
        return

    print(Fore.WHITE + Style.BRIGHT + f"OI check + pull ({len(new_symbols)} symbols) ...\n")
    client = Trader()
    passed, skipped_oi, timed_out, errors = [], [], [], []

    for i, symbol in enumerate(new_symbols, 1):
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
