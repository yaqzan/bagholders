"""Phase C OSK era-layer -- full-coverage our-recipe skew ledger (evidence substrate).

Applies experiments/iv_skew/build_iv.py's EXACT recipe:
    atm_call   = nearest-ATM ~30DTE (20-45 DTE) CALL: price, iv
    skew       = (10% OTM put IV) - (10% OTM call IV)          [crash-pricing / fear]
    iv_rv      = atm_iv / annualized realized vol (vol_pct)     [variance risk premium]
    pnl15      = (option price ~15 trading bars fwd) / entry_premium - 1
    pnl_max/pnl_min over the same 15-bar window

...to ALL rs_ledger signals with overall >= 70, dates 2025-02-01..2026-06-15
(era_conditioning Phase C window -- NOT build_iv.py's stale 2025-02-10/2026-05-15
constants; window bounds are parameterized below, not hardcoded from that file).
2026-06-15 == strategy_config.CALIBRATION_CUTOFF_DATE (the active holdout lock).

DEPARTURE FROM build_iv.py -- unripe labeling: build_iv.py's fwd_pnl() takes
prices[min(14, len(prices)-1)] -- i.e. if fewer than 15 trading-day rows exist in the
24-calendar-day lookout (contract expired, or signal too close to the data max-date),
it silently uses the LAST available price as a "pnl15" proxy. This script instead
requires >=15 valid trading-day rows to emit pnl15/pnl_max/pnl_min; otherwise it
writes NULL and sets ripe=False + n_fwd_rows_avail, so an unripe window can never
masquerade as realized pnl15 evidence in the downstream Stage-1 mining.

WORKTREE DISCIPLINE:
  - Runs ONLY from C:\\Development\\Trader-exp-osk-era (algo-exp/osk-era-layer).
  - Reads MAIN's rs_ledger.parquet by absolute path (read-only; MAIN is not this
    worktree's concern to mutate).
  - Writes to THIS worktree's .cache/osk_era/ only.
  - PYTHONPATH trap guard: force-inserts this worktree's root at sys.path[0] and
    asserts the resolved `database` package file lives under this root, so a
    global PYTHONPATH (which lists C:\\Development\\Trader, i.e. MAIN, ahead of
    site-packages) cannot silently import MAIN's code instead of the worktree's.

Usage (from worktree root):
    python experiments/osk_era/build_osk_ledger.py --smoke 25
    python experiments/osk_era/build_osk_ledger.py            # full build (queue this -- db=heavy)
    python experiments/osk_era/build_osk_ledger.py --lag-days 1   # LAG-ROBUSTNESS mode: the two
        # OTM skew legs are read from the nearest prior option_prices date (searched back
        # LAG_LOOKBACK_WINDOW_DAYS calendar days); the ATM leg / entry_premium / pnl15 / labels
        # stay D-anchored exactly as in lag_days=0 -- see prev_chain_date() docstring below.
"""
import argparse
import json
import math
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_WORKTREE_MARKER = "Trader-exp-osk-era"
assert _WORKTREE_MARKER in _ROOT, (
    f"build_osk_ledger.py must run from the {_WORKTREE_MARKER} worktree root, "
    f"resolved _ROOT={_ROOT}"
)
# FORCE-insert at index 0 (worktree/PYTHONPATH trap -- see .claude/docs/traps.md G29
# equivalent / feedback_worktree_pythonpath_trap.md).
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl  # noqa: E402

import database.trader_database as _dbmod  # noqa: E402
assert os.path.abspath(_dbmod.__file__).lower().startswith(_ROOT.lower()), (
    f"database.trader_database resolved OUTSIDE the worktree root ({_ROOT}): "
    f"{_dbmod.__file__} -- a global PYTHONPATH is shadowing the worktree checkout."
)
from database.trader_database import DB  # noqa: E402

import experiments._holdout as _holdout_mod  # noqa: E402
assert os.path.abspath(_holdout_mod.__file__).lower().startswith(_ROOT.lower()), (
    f"experiments._holdout resolved OUTSIDE the worktree root ({_ROOT}): "
    f"{_holdout_mod.__file__}"
)
from experiments._holdout import assert_no_holdout_leak, CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

# --- MAIN's parquet (read-only; absolute path per worktree discipline) ---
MAIN_ROOT = r"C:\Development\Trader"
LED = os.path.join(MAIN_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")

# --- Window / recipe constants (Phase C spec; NOT build_iv.py's stale constants) ---
WIN_START = "2025-02-01"
WIN_END = "2026-06-15"            # == CALIBRATION_CUTOFF_DATE (holdout lock)
SCORE_MIN = 70                     # overall >= 70 (matches build_iv.py's own filter)
DTE_LO, DTE_HI = 20, 45
OTM_FRAC = 0.10
FWD_TRADING_BARS = 15
FWD_CAL_BUFFER_DAYS = 24            # calendar lookout window (build_iv.py's convention)
MIN_RIPE_ROWS = FWD_TRADING_BARS    # need >=15 trading-day rows to call it "ripe"
LAG_LOOKBACK_WINDOW_DAYS = 5         # lag-robustness mode: width of the calendar-day search
                                     # window (ending at D - lag_days) for the nearest chain date

OUT_DIR = os.path.join(_ROOT, ".cache", "osk_era")


def one(sql, params):
    cur = DB.execute_sql(sql, params)
    return cur.fetchone()


def atm_call(sym, d):
    # nearest-ATM ~30DTE call: id, price, iv, and the stock close
    row = one(
        "SELECT o.id, op.price, op.iv, ph.close, o.expiration_date "
        "FROM options o JOIN option_prices op ON op.option_id=o.id "
        "JOIN price_history ph ON ph.symbol=o.symbol AND ph.date=op.date "
        "WHERE o.symbol=%s AND o.option_type='call' AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s "
        "AND op.iv BETWEEN 0.05 AND 5.0 AND op.price>0 AND op.open_interest>0 "
        "ORDER BY ABS(o.strike_price - ph.close)/ph.close LIMIT 1",
        (sym, d, DTE_LO, DTE_HI),
    )
    return row


def otm_iv(sym, d, otype, close, frac):
    target = close * (1 + frac)  # frac<0 for OTM put, >0 for OTM call
    row = one(
        "SELECT op.iv FROM options o JOIN option_prices op ON op.option_id=o.id "
        "WHERE o.symbol=%s AND o.option_type=%s AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s "
        "AND op.iv BETWEEN 0.05 AND 5.0 AND op.open_interest>0 "
        "ORDER BY ABS(o.strike_price - %s) LIMIT 1",
        (sym, otype, d, DTE_LO, DTE_HI, target),
    )
    return float(row[0]) if row and row[0] is not None else None


def prev_chain_date(sym, d, lag_days):
    """Lag-robustness helper (task: "live scoring may only have PRIOR-DAY chains
    available at score time" -- options are pulled post-close). Returns the most
    recent option_prices date for `sym` that is <= (d - lag_days) calendar days,
    searched back LAG_LOOKBACK_WINDOW_DAYS calendar days total -- i.e. for
    lag_days=1: the most recent chain date in [d-5, d-1], else None.

    None means mechanical skew attrition: the row's ATM leg / entry_premium /
    pnl15 / labels stay D-anchored regardless (computed by atm_call(sym, d) /
    fwd_pnl exactly as in lag_days=0) -- only the two OTM skew legs are
    redirected to this lagged date. otm_iv()'s signature is unchanged; it is
    simply called with this date instead of `d`, and with `close` STILL the
    D-anchored close (the underlying spot price is known live at scoring
    time -- only the OPTION CHAIN snapshot used to price the OTM legs is
    stale), so "10% OTM" is still relative to today's spot even though the
    IV quoted for that strike is yesterday's.
    """
    row = one(
        "SELECT MAX(op.date) FROM option_prices op JOIN options o ON o.id=op.option_id "
        "WHERE o.symbol=%s AND op.date <= DATE_SUB(%s, INTERVAL %s DAY) "
        "AND op.date >= DATE_SUB(%s, INTERVAL %s DAY)",
        (sym, d, lag_days, d, lag_days + LAG_LOOKBACK_WINDOW_DAYS - 1),
    )
    return row[0] if row and row[0] is not None else None


def fwd_pnl(option_id, entry_price, d):
    """15-trading-bar-forward option P&L. Returns (pnl15, pmax, pmin, ripe, n_avail).

    Unlike build_iv.py, NEVER truncate-substitutes the last available price when
    fewer than MIN_RIPE_ROWS rows exist -- that case returns (None, None, None,
    False, n_avail) so unripe rows are labeled, not silently approximated.
    """
    cur = DB.execute_sql(
        "SELECT date, price FROM option_prices WHERE option_id=%s AND date > %s "
        "AND date <= DATE_ADD(%s, INTERVAL %s DAY) ORDER BY date",
        (option_id, d, d, FWD_CAL_BUFFER_DAYS),
    )
    rows = cur.fetchall()
    prices = [float(p) for (_, p) in rows if p is not None and float(p) > 0]
    n_avail = len(prices)
    if n_avail < MIN_RIPE_ROWS:
        return None, None, None, False, n_avail
    p15 = prices[FWD_TRADING_BARS - 1]
    pnl15 = p15 / entry_price - 1.0
    pmax = max(prices[:FWD_TRADING_BARS]) / entry_price - 1.0
    pmin = min(prices[:FWD_TRADING_BARS]) / entry_price - 1.0
    return round(pnl15, 4), round(pmax, 4), round(pmin, 4), True, n_avail


def build(smoke: int = 0, lag_days: int = 0, score_min: int = SCORE_MIN) -> None:
    t0 = time.time()
    led = pl.read_parquet(LED)

    win_all = led.filter(
        (pl.col("date") >= WIN_START) & (pl.col("date") <= WIN_END) & (pl.col("overall") >= score_min)
    )
    # Holdout guard -- the window end IS the cutoff; this must never trip, but assert anyway
    # (defensive per experiments/_holdout.py callsite convention).
    assert_no_holdout_leak(win_all, context="osk_era ledger build")

    win = win_all
    if smoke:
        win = win.sort(["date"]).head(smoke)
        print(f"SMOKE mode: {win.height} signals", flush=True)

    print(
        f"in-window overall>={score_min} signals: {win.height} ({WIN_START}..{WIN_END}), "
        f"full window would be {win_all.height}",
        flush=True,
    )

    recs = []
    miss_no_chain = 0
    miss_unripe = 0
    miss_lag_chain_notfound = 0  # kept rows (ATM leg found) where lag_days>0 and no chain
                                 # date exists anywhere in the lag lookback window
    rows = list(
        zip(
            win["symbol"].to_list(),
            win["date"].to_list(),
            win["overall"].to_list(),
            win["vol_pct"].to_list(),
        )
    )
    for i, (sym, d, ov, volp) in enumerate(rows):
        try:
            ac = atm_call(sym, d)
            if not ac or ac[1] is None or ac[2] is None or float(ac[1]) <= 0:
                miss_no_chain += 1
                continue
            oid, eprice, eiv, close = int(ac[0]), float(ac[1]), float(ac[2]), float(ac[3])
            if lag_days > 0:
                d_skew = prev_chain_date(sym, d, lag_days)
                if d_skew is None:
                    put_iv, call_iv = None, None
                    miss_lag_chain_notfound += 1
                else:
                    put_iv = otm_iv(sym, d_skew, "put", close, -OTM_FRAC)
                    call_iv = otm_iv(sym, d_skew, "call", close, OTM_FRAC)
            else:
                d_skew = d
                put_iv = otm_iv(sym, d, "put", close, -OTM_FRAC)
                call_iv = otm_iv(sym, d, "call", close, OTM_FRAC)
            skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None
            rv_ann = (float(volp) / 100.0) * math.sqrt(252) if volp else None
            iv_rv = (eiv / rv_ann) if (rv_ann and rv_ann > 1e-6) else None
            pnl15, pmax, pmin, ripe, n_avail = fwd_pnl(oid, eprice, d)
            if not ripe:
                miss_unripe += 1
            recs.append(
                {
                    "symbol": sym,
                    "date": d,
                    "overall": int(ov),
                    "vol_pct": float(volp) if volp else None,
                    "atm_iv": round(eiv, 4),
                    "entry_premium": eprice,
                    "iv_rv": (round(iv_rv, 4) if iv_rv else None),
                    "skew": (round(skew, 4) if skew is not None else None),
                    "skew_chain_date": (str(d_skew) if d_skew is not None else None),
                    "lag_days": lag_days,
                    "pnl15": pnl15,
                    "pnl_max": pmax,
                    "pnl_min": pmin,
                    "ripe": ripe,
                    "n_fwd_rows_avail": n_avail,
                }
            )
        except Exception as e:
            miss_no_chain += 1
            if miss_no_chain <= 3:
                print("  err", sym, d, repr(e)[:120], flush=True)
        if (i + 1) % 500 == 0:
            print(
                f"  {i + 1}/{len(rows)} ({time.time() - t0:.0f}s) kept={len(recs)} "
                f"no_chain={miss_no_chain} unripe_so_far={miss_unripe}"
                + (f" lag_chain_missing_so_far={miss_lag_chain_notfound}" if lag_days else ""),
                flush=True,
            )

    print(
        f"kept={len(recs)} no_chain={miss_no_chain} unripe_of_kept={miss_unripe} "
        f"in {time.time() - t0:.0f}s"
        + (f" lag_days={lag_days} lag_chain_missing={miss_lag_chain_notfound}" if lag_days else ""),
        flush=True,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None) if recs else pl.DataFrame()
    suffix_parts = []
    if score_min != SCORE_MIN:
        suffix_parts.append(str(score_min))
    if lag_days:
        suffix_parts.append(f"lag{lag_days}")
    if smoke:
        suffix_parts.append("smoke")
    suffix = ("_" + "_".join(suffix_parts)) if suffix_parts else ""
    out_name = f"osk_era_ledger{suffix}.parquet"
    out = os.path.join(OUT_DIR, out_name)
    df.write_parquet(out)

    n = df.height

    def cov(col):
        return round(100.0 * df[col].drop_nulls().len() / max(n, 1), 1) if n else 0.0

    manifest = {
        "window_start": WIN_START,
        "window_end": WIN_END,
        "score_min": score_min,
        "smoke": smoke,
        "lag_days": lag_days,
        "lag_lookback_window_days": (LAG_LOOKBACK_WINDOW_DAYS if lag_days else None),
        "n_requested": len(rows),
        "n_kept_rows": n,
        "n_miss_no_chain": miss_no_chain,
        "n_unripe": miss_unripe,
        "n_skew_null_due_to_lag_chain_missing": miss_lag_chain_notfound,
        "skew_cov_pct": cov("skew"),
        "pnl15_cov_pct": cov("pnl15"),
        "atm_iv_cov_pct": cov("atm_iv"),
        "ripe_pct_of_kept": round(100.0 * (n - miss_unripe) / max(n, 1), 1) if n else 0.0,
        "elapsed_sec": round(time.time() - t0, 1),
        "recipe_source": "experiments/iv_skew/build_iv.py (DTE 20-45, OTM 10%, fwd 15 trading bars)",
        "holdout_cutoff": str(HOLDOUT_CUTOFF),
        "output_parquet": out,
    }
    manifest_name = f"build_manifest{suffix}.json"
    with open(os.path.join(OUT_DIR, manifest_name), "w") as f:
        json.dump(manifest, f, indent=2)
    print("DONE", out, json.dumps(manifest), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", type=int, default=0, help="limit to first N (sorted by date) signals")
    ap.add_argument(
        "--lag-days", type=int, default=0, dest="lag_days",
        help=(
            "lag-robustness mode: compute skew (OTM put/call IV legs) from the nearest prior "
            "option_prices date at least this many days back (search window is "
            f"{LAG_LOOKBACK_WINDOW_DAYS} calendar days wide); ATM leg/entry_premium/pnl15/labels "
            "always stay D-anchored. 0 = original same-day skew (default)."
        ),
    )
    ap.add_argument(
        "--score-min", type=int, default=SCORE_MIN, dest="score_min",
        help=(
            f"minimum overall score for the in-window population (default {SCORE_MIN}, matching "
            "build_iv.py's own filter). Use a lower value (e.g. 65) to widen the base population "
            "so bucket-migration analysis downstream can see rows promoted into 70+ by an "
            "additive lift. Output filename gets a _<score_min> suffix when this differs from "
            "the default, e.g. osk_era_ledger_65.parquet."
        ),
    )
    args = ap.parse_args()
    build(smoke=args.smoke, lag_days=args.lag_days, score_min=args.score_min)
