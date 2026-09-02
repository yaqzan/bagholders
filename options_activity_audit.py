"""
options_activity_audit.py — Rank all tracked stocks by 30DTE options liquidity.

Activity score = total dollar notional (OI × premium) across ALL expirations
in the [MIN_DTE .. MAX_DTE] window.  This captures the full liquidity pool
feeding into 30DTE entries: a 25DTE chain with thousands of OI counts, and
a cheap chain with zero premium does not inflate scores.

Score = sum(OI × lastPrice × 100)  for every call/put strike in the window.
Reported in $K.  The nearest-to-30DTE chain is also broken out separately.

Uses DB data when available; falls back to live yfinance for missing stocks.
With --save, yfinance-fetched data is persisted to the DB (all expirations,
matching the trader.py pull_options behavior) so future runs use the DB path.

Usage:
    python options_activity_audit.py [options]

Flags:
    --min-score INT     Prune cutoff in $K notional (default: auto = bottom 10%)
    --top INT           Replacement candidates to show (default: 20)
    --no-yf             Skip live yfinance fetch for stocks with no DB options data
    --save              Persist yfinance-fetched option chains to DB (all expirations)
    --no-replacements   Skip new-stock discovery pass
    --output FILE       Write CSV to this file
    --min-dte INT       Minimum DTE to include (default 7 — avoids pin-risk expiries)
    --max-dte INT       Maximum DTE to include (default 42)
"""

import argparse
import io
import statistics
import threading
import time
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf
from colorama import Fore, Style, init

init(autoreset=True, convert=True)

# ── Config ─────────────────────────────────────────────────────────────────────
TARGET_DTE         = 30      # reference DTE — used to pick the "nearest 30DTE" chain
MIN_DTE_DEFAULT    = 7       # ignore very near-expiry chains
MAX_DTE_DEFAULT    = 42      # upper bound of window
OI_FETCH_TIMEOUT   = 25
RATE_LIMIT_SLEEP   = 0.25
REPLACEMENT_SOURCES = [
    ("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
    ("S&P 400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"),
    ("S&P 600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"),
]
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def load_db_options_activity(symbols: list[str], min_dte: int, max_dte: int) -> dict[str, dict]:
    """
    Three-pass bulk load — no correlated subqueries.

    Pass 1: all Options in [min_dte .. max_dte] for the given symbols.
    Pass 2: latest OptionPrice rows (last 7 days) for those option IDs.
    Pass 3: latest close price per symbol (for ATM detection).

    Returns dict[symbol] → activity dict, or {'source': 'db_missing'} if absent.

    Activity dict keys:
        total_dollar_notional_k  — sum(OI × premium × 100) across window, in $K
        total_oi                 — sum of OI across window
        nearest30_dollar_k       — dollar notional on the nearest-to-30DTE chain only
        nearest30_oi             — total OI on the nearest-to-30DTE chain
        nearest30_atm_oi         — ATM strike OI on nearest-to-30DTE chain
        nearest30_atm_premium    — ATM option premium on nearest-to-30DTE chain
        nearest30_exp            — date of the nearest-to-30DTE expiration
        nearest30_dte            — actual DTE of that expiration
        current_price            — latest close
        source                   — 'db'
    """
    from database.models.options import Option, OptionPrice
    from database.models.technical import PriceHistory

    today  = date.today()
    lo     = today + timedelta(days=min_dte)
    hi     = today + timedelta(days=max_dte)
    pr_lo  = today - timedelta(days=7)

    # Pass 1: Options in window
    print("  Loading Options table...", end="", flush=True)
    # opt_map: option_id → (symbol, exp_date, strike_float, option_type_upper)
    opt_map: dict[int, tuple] = {}
    for o in Option.select().where(
        Option.symbol.in_(symbols),
        Option.expiration_date >= lo,
        Option.expiration_date <= hi,
    ):
        opt_map[o.id] = (
            o.symbol_id,
            o.expiration_date,
            float(o.strike_price),
            (o.option_type or '').upper(),
        )
    print(f" {len(opt_map):,} options found")

    if not opt_map:
        return {sym: {'source': 'db_missing'} for sym in symbols}

    # Pass 2: latest OptionPrice per option in the last 7 days
    # Structure: sym → exp → strike → {call_oi, put_oi, call_prem, put_prem,
    #                                    call_vol, put_vol}
    print("  Loading OptionPrice data...", end="", flush=True)
    by_sym: dict[str, dict] = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: {
                    'call_oi': 0, 'put_oi': 0,
                    'call_prem': 0.0, 'put_prem': 0.0,
                    'call_vol': 0, 'put_vol': 0,
                }
            )
        )
    )
    seen_opt: set[int] = set()
    n_price = 0
    for pr in (
        OptionPrice
        .select()
        .where(
            OptionPrice.option.in_(list(opt_map.keys())),
            OptionPrice.date >= pr_lo,
        )
        .order_by(OptionPrice.date.desc())
    ):
        oid = pr.option_id
        if oid in seen_opt:
            continue
        seen_opt.add(oid)
        sym, exp, strike, otype = opt_map[oid]
        bucket = by_sym[sym][exp][strike]
        prem = float(pr.price or 0)
        oi   = int(pr.open_interest or 0)
        vol  = int(pr.volume or 0)
        if otype.startswith('C'):
            bucket['call_oi']   += oi
            bucket['call_prem']  = prem   # last seen = most recent date (ordered desc)
            bucket['call_vol']  += vol
        else:
            bucket['put_oi']    += oi
            bucket['put_prem']   = prem
            bucket['put_vol']   += vol
        n_price += 1
    print(f" {n_price:,} price rows")

    # Pass 3: latest close price per symbol
    print("  Loading close prices...", end="", flush=True)
    latest_close: dict[str, float] = {}
    seen_close: set[str] = set()
    for r in (
        PriceHistory
        .select(PriceHistory.symbol, PriceHistory.close, PriceHistory.date)
        .where(
            PriceHistory.symbol.in_(symbols),
            PriceHistory.date >= today - timedelta(days=10),
        )
        .order_by(PriceHistory.date.desc())
    ):
        s = r.symbol_id
        if s not in seen_close:
            latest_close[s] = float(r.close)
            seen_close.add(s)
    print(f" {len(latest_close):,} prices")

    # Build per-symbol activity dicts
    results: dict[str, dict] = {}
    for sym in symbols:
        exps = by_sym.get(sym)
        if not exps:
            results[sym] = {'source': 'db_missing'}
            continue

        cur_price = latest_close.get(sym)

        # Aggregate across ALL expirations in window
        total_dollar = 0.0   # $
        total_oi     = 0

        # Per-expiration totals for finding nearest-30DTE
        exp_totals: dict[date, dict] = {}
        for exp, strikes in exps.items():
            exp_dollar = 0.0
            exp_oi     = 0
            for strk, bkt in strikes.items():
                combined_oi   = bkt['call_oi'] + bkt['put_oi']
                # Use mean premium of available side(s); zero premium → OI still counted
                c_notional = bkt['call_oi'] * bkt['call_prem'] * 100
                p_notional = bkt['put_oi']  * bkt['put_prem']  * 100
                exp_dollar += c_notional + p_notional
                exp_oi     += combined_oi
            total_dollar += exp_dollar
            total_oi     += exp_oi
            exp_totals[exp] = {'dollar': exp_dollar, 'oi': exp_oi}

        # Nearest expiration to 30DTE
        nearest_exp = min(exps.keys(), key=lambda e: abs((e - today).days - TARGET_DTE))
        n30_dte     = (nearest_exp - today).days
        n30_strikes = exps[nearest_exp]

        n30_oi     = exp_totals[nearest_exp]['oi']
        n30_dollar = exp_totals[nearest_exp]['dollar']

        # ATM on nearest-30DTE chain
        if cur_price and n30_strikes:
            atm_strk = min(n30_strikes.keys(), key=lambda s: abs(s - cur_price))
        elif n30_strikes:
            # Fallback: highest OI strike
            atm_strk = max(
                n30_strikes.keys(),
                key=lambda s: n30_strikes[s]['call_oi'] + n30_strikes[s]['put_oi'],
            )
        else:
            atm_strk = None

        atm_bkt = n30_strikes.get(atm_strk, {}) if atm_strk else {}
        atm_oi   = atm_bkt.get('call_oi', 0) + atm_bkt.get('put_oi', 0)
        atm_prem = (atm_bkt.get('call_prem', 0) + atm_bkt.get('put_prem', 0)) / 2

        results[sym] = {
            'total_dollar_notional_k': round(total_dollar / 1000, 1),
            'total_oi':                total_oi,
            'nearest30_dollar_k':      round(n30_dollar / 1000, 1),
            'nearest30_oi':            n30_oi,
            'nearest30_atm_oi':        atm_oi,
            'nearest30_atm_premium':   round(atm_prem, 2),
            'nearest30_exp':           nearest_exp,
            'nearest30_dte':           n30_dte,
            'current_price':           cur_price,
            'source':                  'db',
        }

    return results


# ── yfinance helpers ───────────────────────────────────────────────────────────

def _run_with_timeout(fn, timeout, *args):
    result_box = [None]
    exc_box    = [None]
    def _target():
        try:
            result_box[0] = fn(*args)
        except Exception as e:
            exc_box[0] = e
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, True
    if exc_box[0]:
        raise exc_box[0]
    return result_box[0], False


def _save_chain_to_db(symbol: str, expiration: str, chain, today: date) -> None:
    """Persist one option chain to DB — mirrors trader.py pull_options logic exactly."""
    from database.models.options import Option, OptionPrice
    for _, row in chain.calls.iterrows():
        opt = Option.build(symbol, row['strike'], expiration, 'call')
        OptionPrice.build(opt, today, row['lastPrice'], row['volume'],
                          row['openInterest'], row['impliedVolatility'])
    for _, row in chain.puts.iterrows():
        opt = Option.build(symbol, row['strike'], expiration, 'put')
        OptionPrice.build(opt, today, row['lastPrice'], row['volume'],
                          row['openInterest'], row['impliedVolatility'])


def _fetch_yf_activity(symbol: str, min_dte: int, max_dte: int,
                        save_to_db: bool = False) -> dict:
    today  = date.today()
    lo     = today + timedelta(days=min_dte)
    hi     = today + timedelta(days=max_dte)

    ticker      = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        return {'source': 'yf_no_options'}

    # Parse all expiration dates
    exp_dates: list[date] = []
    for s in expirations:
        try:
            exp_dates.append(date.fromisoformat(s))
        except ValueError:
            pass

    window_exps = [e for e in exp_dates if lo <= e <= hi]

    # If nothing in window, use the single closest to 30DTE as fallback
    if not window_exps:
        if exp_dates:
            window_exps = [min(exp_dates, key=lambda e: abs((e - today).days - TARGET_DTE))]
        else:
            return {'source': 'yf_no_options'}

    # Current price for ATM detection
    try:
        cur_price = float(ticker.fast_info.last_price or 0) or None
    except Exception:
        cur_price = None

    def _mid(row) -> float:
        """Mid price when bid/ask present, else lastPrice."""
        bid = row.get('bid', 0) or 0
        ask = row.get('ask', 0) or 0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return float(row.get('lastPrice', 0) or 0)

    total_dollar = 0.0
    total_oi     = 0
    nearest_exp  = min(window_exps, key=lambda e: abs((e - today).days - TARGET_DTE))
    n30_dollar   = 0.0
    n30_oi       = 0
    n30_atm_oi   = 0
    n30_atm_prem = 0.0

    # Fetch all expirations so we can save ALL of them when --save is set,
    # mirroring trader.py pull_options which saves every available expiry.
    # Metric calculation still uses only the window exps.
    all_chains: dict[date, object] = {}  # exp_date → chain (only fetched once per exp)

    for exp_str in expirations:
        try:
            exp = date.fromisoformat(exp_str)
        except ValueError:
            continue
        chain = ticker.option_chain(exp_str)
        all_chains[exp] = chain

        # Save to DB for ALL expirations when requested
        if save_to_db:
            _save_chain_to_db(symbol, exp_str, chain, today)

        # Metric accumulation — window exps only
        if exp not in window_exps:
            continue

        exp_dollar = 0.0
        exp_oi     = 0
        for df in (chain.calls, chain.puts):
            for _, row in df.iterrows():
                oi   = int(row.get('openInterest', 0) or 0)
                prem = _mid(row)
                exp_dollar += oi * prem * 100
                exp_oi     += oi

        total_dollar += exp_dollar
        total_oi     += exp_oi

        if exp == nearest_exp:
            n30_dollar = exp_dollar
            n30_oi     = exp_oi

            # ATM on nearest-30DTE chain
            if cur_price:
                def _atm_row(df):
                    if df.empty:
                        return None
                    idx = (df['strike'] - cur_price).abs().idxmin()
                    return df.loc[idx]
                cr = _atm_row(chain.calls)
                pr = _atm_row(chain.puts)
            else:
                def _max_oi_row(df):
                    if df.empty:
                        return None
                    return df.loc[df['openInterest'].fillna(0).idxmax()]
                cr = _max_oi_row(chain.calls)
                pr = _max_oi_row(chain.puts)

            c_oi   = int(cr['openInterest'] if cr is not None and pd.notna(cr.get('openInterest')) else 0)
            p_oi   = int(pr['openInterest'] if pr is not None and pd.notna(pr.get('openInterest')) else 0)
            c_prem = _mid(cr) if cr is not None else 0.0
            p_prem = _mid(pr) if pr is not None else 0.0
            n30_atm_oi   = c_oi + p_oi
            n30_atm_prem = (c_prem + p_prem) / 2 if (c_prem + p_prem) > 0 else 0.0

    return {
        'total_dollar_notional_k': round(total_dollar / 1000, 1),
        'total_oi':                total_oi,
        'nearest30_dollar_k':      round(n30_dollar / 1000, 1),
        'nearest30_oi':            n30_oi,
        'nearest30_atm_oi':        n30_atm_oi,
        'nearest30_atm_premium':   round(n30_atm_prem, 2),
        'nearest30_exp':           nearest_exp,
        'nearest30_dte':           (nearest_exp - today).days,
        'current_price':           cur_price,
        'source':                  'yf',
    }


def fetch_yf_activity_safe(symbol: str, min_dte: int, max_dte: int,
                            save_to_db: bool = False) -> dict:
    try:
        result, timed_out = _run_with_timeout(
            _fetch_yf_activity, OI_FETCH_TIMEOUT, symbol, min_dte, max_dte, save_to_db
        )
        if timed_out:
            return {'source': 'yf_timeout'}
        return result
    except Exception as e:
        return {'source': f'yf_error: {str(e)[:60]}'}


# ── Scoring ────────────────────────────────────────────────────────────────────

def activity_score(info: dict) -> float:
    """Primary sort key = total dollar notional in $K across the DTE window."""
    src = info.get('source', '')
    if not src.startswith('db') and not src.startswith('yf') or \
       'missing' in src or 'no_options' in src or 'timeout' in src or 'error' in src:
        return 0.0
    return float(info.get('total_dollar_notional_k', 0))


# ── Replacement candidates ─────────────────────────────────────────────────────

def fetch_index_symbols(url: str, name: str) -> list[str]:
    try:
        resp   = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
    except Exception:
        try:
            tables = pd.read_html(io.StringIO(resp.text))
        except Exception:
            print(Fore.YELLOW + f"  Could not parse {name}")
            return []
    for tbl in tables:
        cols    = [c.strip() for c in tbl.columns]
        sym_col = next((c for c in cols if c.lower() in ("symbol", "ticker")), None)
        if sym_col:
            syms = tbl[sym_col].dropna().str.strip().str.upper().tolist()
            syms = [s.replace(".", "-") for s in syms]
            print(Fore.CYAN + f"  {name}: {len(syms)} constituents")
            return syms
    return []


def find_replacement_candidates(
    existing: set[str], min_dte: int, max_dte: int, top_n: int
) -> list[tuple[str, dict]]:
    all_candidates: set[str] = set()
    for name, url in REPLACEMENT_SOURCES:
        print(f"\n  Fetching {name}...")
        syms = fetch_index_symbols(url, name)
        all_candidates.update(s for s in syms if s not in existing)

    print(f"\n  {len(all_candidates)} candidates not in DB — scanning options activity...")
    scored: list[tuple[str, float, dict]] = []
    for i, sym in enumerate(sorted(all_candidates)):
        info  = fetch_yf_activity_safe(sym, min_dte, max_dte)
        score = activity_score(info)
        scored.append((sym, score, info))
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(all_candidates)} scanned...")
        time.sleep(RATE_LIMIT_SLEEP)

    scored.sort(key=lambda x: x[1], reverse=True)
    return [(sym, info) for sym, _, info in scored[:top_n]]


# ── Display helpers ────────────────────────────────────────────────────────────

def _fmt_k(val: float) -> str:
    if val >= 1_000_000:
        return f"${val/1_000_000:.1f}B"
    if val >= 1_000:
        return f"${val/1_000:.1f}M"
    return f"${val:.0f}K"


def _color_score(score: float, median: float, p10: float) -> str:
    s = _fmt_k(score)
    if score == 0:
        return Fore.RED + s
    if score < p10:
        return Fore.RED + s
    if score < median:
        return Fore.YELLOW + s
    return Fore.GREEN + s


def print_ranked_table(
    ranked: list[tuple[str, float, dict]],
    cutoff: float,
    median: float,
    p10: float,
):
    hdr = (
        f"{'#':>4}  {'Sym':<7}  {'TotalNotional':>14}  {'TotalOI':>9}"
        f"  {'N30$':>10}  {'N30 OI':>8}  {'ATM OI':>7}  {'ATM$':>6}"
        f"  {'DTE':>4}  {'Src':<4}  Exp"
    )
    print(Fore.CYAN + hdr)
    print("─" * len(hdr))
    for i, (sym, score, info) in enumerate(ranked, 1):
        flag     = " ← PRUNE?" if score < cutoff else ""
        src      = info.get('source', '?')[:4]
        exp      = str(info.get('nearest30_exp', ''))[:10] if info.get('nearest30_exp') else '—'
        dte      = str(info.get('nearest30_dte', '')) or '—'
        tot_oi   = info.get('total_oi', 0)
        n30_k    = info.get('nearest30_dollar_k', 0)
        n30_oi   = info.get('nearest30_oi', 0)
        atm_oi   = info.get('nearest30_atm_oi', 0)
        atm_prem = info.get('nearest30_atm_premium', 0)
        score_str = _color_score(score, median, p10)
        print(
            f"{i:>4}  {sym:<7}  {score_str:>14}  {tot_oi:>9,}"
            f"  {_fmt_k(n30_k):>10}  {n30_oi:>8,}  {atm_oi:>7,}  ${atm_prem:>5.2f}"
            f"  {dte:>4}  {src:<4}  {exp}{flag}"
        )


# ── Prune ─────────────────────────────────────────────────────────────────────

def _prune_stocks(symbols: list[str]) -> None:
    """Delete all DB rows for the given symbols, then remove the Stock rows."""
    from database.models.options import Option, OptionPrice
    from database.models.technical import PriceHistory, Indicator, WeeklyPriceHistory, WeeklyIndicator
    from database.models.core import (
        Score, WeeklyScore, EarningsDate, HistoricPeak, DteRecommendation,
    )
    from database import Stock

    # Try importing optional models gracefully
    try:
        from database.models.core import SplitEvent
    except ImportError:
        SplitEvent = None
    try:
        from database.models.position import Position
    except ImportError:
        Position = None

    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{total}] Deleting {sym}...", end=" ", flush=True)
        counts = []

        # OptionPrice → Option (must go first — child of Option)
        opt_ids = [o.id for o in Option.select(Option.id).where(Option.symbol == sym)]
        if opt_ids:
            n = OptionPrice.delete().where(OptionPrice.option.in_(opt_ids)).execute()
            counts.append(f"{n} option_prices")
            n = Option.delete().where(Option.symbol == sym).execute()
            counts.append(f"{n} options")

        # All direct Stock children
        for Model, label in [
            (Score,              "scores"),
            (WeeklyScore,        "weekly_scores"),
            (Indicator,          "indicators"),
            (WeeklyIndicator,    "weekly_indicators"),
            (PriceHistory,       "price_history"),
            (WeeklyPriceHistory, "weekly_price_history"),
            (EarningsDate,       "earnings_dates"),
            (HistoricPeak,       "historic_peaks"),
            (DteRecommendation,  "dte_recs"),
        ]:
            n = Model.delete().where(Model.symbol == sym).execute()
            if n:
                counts.append(f"{n} {label}")

        if SplitEvent:
            n = SplitEvent.delete().where(SplitEvent.symbol == sym).execute()
            if n:
                counts.append(f"{n} split_events")
        if Position:
            n = Position.delete().where(Position.symbol == sym).execute()
            if n:
                counts.append(f"{n} positions")

        Stock.delete().where(Stock.symbol == sym).execute()
        print(Fore.GREEN + "done" + Style.RESET_ALL + f"  ({', '.join(counts) or 'no child rows'})")

    print(Fore.GREEN + f"\n  ✓ Pruned {total} stocks from DB.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score",       type=float, default=None,
                        help="Prune cutoff in $K notional (default: auto = bottom 10%)")
    parser.add_argument("--top",             type=int,   default=20)
    parser.add_argument("--no-yf",           action="store_true")
    parser.add_argument("--save",            action="store_true",
                        help="Persist yfinance-fetched chains to DB (all expirations)")
    parser.add_argument("--prune",           action="store_true",
                        help="Delete all stocks below the cutoff from the DB (irreversible)")
    parser.add_argument("--no-replacements", action="store_true")
    parser.add_argument("--output",          type=str,   default=None)
    parser.add_argument("--min-dte",         type=int,   default=MIN_DTE_DEFAULT,
                        help=f"Min DTE to include (default {MIN_DTE_DEFAULT})")
    parser.add_argument("--max-dte",         type=int,   default=MAX_DTE_DEFAULT,
                        help=f"Max DTE to include (default {MAX_DTE_DEFAULT})")
    args = parser.parse_args()

    from database.project_root import chdir_trader_project
    chdir_trader_project()
    from database import Stock

    # ── 1. Load tracked symbols ───────────────────────────────────────────────
    print(Fore.CYAN + "\n── Loading tracked stocks...")
    all_symbols = [s.symbol for s in Stock.select(Stock.symbol).order_by(Stock.symbol)]
    print(f"  {len(all_symbols)} stocks in DB")

    # ── 2. DB query ───────────────────────────────────────────────────────────
    print(Fore.CYAN + f"\n── Querying DB for {args.min_dte}–{args.max_dte} DTE options activity...")
    db_results = load_db_options_activity(all_symbols, args.min_dte, args.max_dte)

    missing  = [s for s in all_symbols if db_results.get(s, {}).get('source') == 'db_missing']
    has_data = [s for s in all_symbols if db_results.get(s, {}).get('source') == 'db']
    print(f"  {len(has_data)} stocks have DB options data")
    print(f"  {len(missing)} stocks have no DB options data")

    # ── 3. Live yfinance fetch for missing stocks ──────────────────────────────
    yf_results: dict[str, dict] = {}
    if missing and not args.no_yf:
        print(Fore.CYAN + f"\n── Fetching from yfinance for {len(missing)} missing stocks...")
        if args.save:
            print(Fore.YELLOW + "  --save active: fetched chains will be persisted to DB")
        for i, sym in enumerate(missing):
            info  = fetch_yf_activity_safe(sym, args.min_dte, args.max_dte,
                                            save_to_db=args.save)
            yf_results[sym] = info
            score = activity_score(info)
            color = Fore.GREEN if score > 500 else (Fore.YELLOW if score > 0 else Fore.RED)
            src   = info.get('source', '?')
            saved = " [saved]" if args.save and 'yf' in src else ""
            print(
                f"  [{i+1:>3}/{len(missing)}] {sym:<7} "
                f"score={color}{_fmt_k(score):>10}{Style.RESET_ALL}  ({src}){saved}"
            )
            time.sleep(RATE_LIMIT_SLEEP)
    elif missing and args.no_yf:
        print(Fore.YELLOW + f"  --no-yf: {len(missing)} stocks will score 0")

    # ── 4. Merge and rank ──────────────────────────────────────────────────────
    combined = {
        sym: yf_results.get(sym) or db_results.get(sym, {'source': 'db_missing'})
        for sym in all_symbols
    }
    ranked = sorted(
        [(sym, activity_score(combined[sym]), combined[sym]) for sym in all_symbols],
        key=lambda x: x[1], reverse=True,
    )

    scores = [s for _, s, _ in ranked]
    median = statistics.median(scores) if scores else 0
    p10    = sorted(scores)[max(0, len(scores) // 10 - 1)] if scores else 0
    cutoff = args.min_score if args.min_score is not None else p10

    # ── 5. Print ranked table ─────────────────────────────────────────────────
    print(Fore.CYAN + f"\n── Stocks ranked by options dollar notional ({args.min_dte}–{args.max_dte} DTE window)")
    print(
        f"   Median: {_fmt_k(median)}  |  Bottom 10%: {_fmt_k(p10)}  |  Prune cutoff: {_fmt_k(cutoff)}\n"
    )
    print_ranked_table(ranked, cutoff, median, p10)

    # ── 6. Prune summary (and optional deletion) ──────────────────────────────
    prune_list = [(sym, score, info) for sym, score, info in ranked if score < cutoff]
    print(f"\n{Fore.RED}── Prune candidates (below {_fmt_k(cutoff)}): {len(prune_list)}")
    for sym, score, info in prune_list:
        print(f"  {sym:<7}  {_fmt_k(score):>10}  source={info.get('source','?')}")

    if args.prune and prune_list:
        prune_symbols = [sym for sym, _, _ in prune_list]
        print(f"\n{Fore.RED}── --prune flag set.  About to DELETE {len(prune_symbols)} stocks from DB.")
        print(f"   Symbols: {', '.join(prune_symbols)}")
        print(f"\n   Type YES to confirm: ", end="", flush=True)
        answer = input().strip()
        if answer != "YES":
            print(Fore.YELLOW + "  Aborted — nothing deleted.")
        else:
            _prune_stocks(prune_symbols)
    elif args.prune and not prune_list:
        print(Fore.GREEN + "\n  Nothing to prune at this cutoff.")

    # ── 7. CSV export ─────────────────────────────────────────────────────────
    if args.output:
        rows = []
        for sym, score, info in ranked:
            rows.append({
                'symbol':               sym,
                'total_dollar_notional_k': score,
                'total_oi':             info.get('total_oi', 0),
                'nearest30_dollar_k':   info.get('nearest30_dollar_k', 0),
                'nearest30_oi':         info.get('nearest30_oi', 0),
                'nearest30_atm_oi':     info.get('nearest30_atm_oi', 0),
                'nearest30_atm_premium':info.get('nearest30_atm_premium', 0),
                'nearest30_exp':        str(info.get('nearest30_exp', '')),
                'nearest30_dte':        info.get('nearest30_dte', ''),
                'current_price':        info.get('current_price', ''),
                'source':               info.get('source', ''),
                'prune':                score < cutoff,
            })
        pd.DataFrame(rows).to_csv(args.output, index=False)
        print(f"\n{Fore.GREEN}CSV saved → {args.output}")

    # ── 8. Replacement candidates ─────────────────────────────────────────────
    if not args.no_replacements:
        existing = set(all_symbols)
        print(Fore.CYAN + f"\n── Finding top {args.top} replacement candidates not in DB...")
        replacements = find_replacement_candidates(existing, args.min_dte, args.max_dte, args.top)
        if replacements:
            print(Fore.CYAN + f"\n── Top {len(replacements)} replacements:\n")
            hdr = f"{'#':>4}  {'Sym':<7}  {'TotalNotional':>14}  {'TotalOI':>9}  {'N30$':>10}  Exp"
            print(Fore.CYAN + hdr)
            print("─" * len(hdr))
            for i, (sym, info) in enumerate(replacements, 1):
                score  = activity_score(info)
                tot_oi = info.get('total_oi', 0)
                n30_k  = info.get('nearest30_dollar_k', 0)
                exp    = str(info.get('nearest30_exp', ''))[:10] if info.get('nearest30_exp') else '—'
                score_str = _color_score(score, median, p10)
                print(
                    f"{i:>4}  {sym:<7}  {score_str:>14}  {tot_oi:>9,}  {_fmt_k(n30_k):>10}  {exp}"
                )


if __name__ == '__main__':
    main()
