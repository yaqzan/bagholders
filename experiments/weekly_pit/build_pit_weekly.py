"""Point-in-time partial-week weekly reconstruction (Phase 1 + 2).

Reconstructs what the weekly composite / w_adj / w_mom WAS at each (symbol,
signal_date) using ONLY daily PriceHistory bars up to and INCLUDING signal_date
(NO future bars). This removes the recalc look-ahead (which reads the COMPLETE
Mon-Fri WeeklyScore for a mid-week signal) and matches what LIVE `trader update`
computes (current-week PARTIAL, point-in-time).

Faithfulness strategy: we do NOT re-implement the breakout-push / divergence /
phase logic of the weekly RSI/MACD/trend scores. We rebuild the weekly OHLCV
series with the current week truncated to signal_date, run the IDENTICAL talib
calls production uses (RSI 14, MACD 12/26/9, EMA 21/50/200), wrap the result in
indicator-like / price-like objects, and call the REAL production score
functions (`Stock.calculate_{trend,rsi,macd}_score`) through their `_ind_cache` /
`_ph_cache` injection ports. Then the REAL `calculate_weekly_adjustment` gives
pit_w_comp / pit_w_bias / pit_w_mom / pit_w_adj.

Weekly OHLCV aggregation (matches technical.py `_refresh_weekly_aggregates`):
  week_start = date - timedelta(days=date.weekday())   # Monday
  open = first daily open, high = max, low = min, close = last daily close,
  volume = sum. Current (signal) week is truncated to Mon..signal_date.

Phase 2: validate pit_w_comp / pit_w_adj against LIVE `score_intraday_logs`
(production v60, data 2026-05-27..29). Those logs carry w_comp/w_adj (the live
point-in-time partial-week values); a faithful PIT reconstruction matches them.

Run:
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/weekly_pit/build_pit_weekly.py validate
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/weekly_pit/build_pit_weekly.py build [--workers N]
"""
import os
import sys
import json
import time
import math
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np
import talib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CUTOFF = "2026-05-15"               # CALIBRATION_CUTOFF_DATE
LEDGER = os.path.join(ROOT, ".cache", "rqc_v60", "ledger_5y.parquet")
OUT_DIR = os.path.join(ROOT, ".cache", "weekly_pit")
OUT_PARQUET = os.path.join(OUT_DIR, "pit_weekly_5y.parquet")

# Weekly score-function lookback needs (DESC-sorted caches). RSI uses lookback+5,
# MACD uses ~9 months of WEEKLY bars (~39 weeks), trend uses lookback+1=21.
# Give generous tails so talib EMA200 / MACD warmup are fully converged.
MAX_WEEKS_TAIL = 320               # ~6y of weekly bars; covers EMA200 warmup


# ----------------------------------------------------------------------------
# Lightweight stand-ins for WeeklyIndicator / WeeklyPriceHistory rows so we can
# feed the REAL production score functions via _ind_cache / _ph_cache.
# ----------------------------------------------------------------------------
class _IndRow:
    __slots__ = ("date", "rsi", "macd", "macd_signal", "macd_hist",
                 "ema_21", "ema_50", "ema_200", "_ph")

    def __init__(self, d):
        self.date = d
        self.rsi = self.macd = self.macd_signal = self.macd_hist = None
        self.ema_21 = self.ema_50 = self.ema_200 = None
        self._ph = None

    def price_history(self):
        return self._ph


class _PhRow:
    __slots__ = ("date", "open", "high", "low", "close", "volume")

    def __init__(self, d, o, h, l, c, v):
        self.date = d
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def _week_start(d):
    return d - timedelta(days=d.weekday())


def _aggregate_weekly(daily, upto):
    """daily: list of (date, open, high, low, close, volume) ASC, date<=upto.
    Returns weekly _PhRow list ASC. The week containing `upto` is truncated to
    bars with date<=upto (current week is PARTIAL when upto is mid-week)."""
    by_week = {}
    order = []
    for d, o, h, l, c, v in daily:
        if d > upto:
            continue
        wk = _week_start(d)
        if wk not in by_week:
            by_week[wk] = []
            order.append(wk)
        by_week[wk].append((d, o, h, l, c, v))
    out = []
    for wk in order:
        days = by_week[wk]            # already ASC (daily is ASC)
        o = days[0][1]
        h = max(x[2] for x in days)
        l = min(x[3] for x in days)
        c = days[-1][4]
        v = sum(int(x[5]) for x in days)
        out.append(_PhRow(wk, o, h, l, c, v))
    return out


def _build_ind_cache(weekly_ph):
    """Run the SAME talib calls production uses over the weekly close series and
    attach to indicator-like rows. Returns ind rows ASC with talib outputs and
    .price_history() wired. Mirrors core.calculate_indicators(weekly=True)
    column gating exactly."""
    n = len(weekly_ph)
    if n < 14:
        return []
    close = np.array([float(p.close) for p in weekly_ph])
    high = np.array([float(p.high) for p in weekly_ph])
    low = np.array([float(p.low) for p in weekly_ph])

    rsi = talib.RSI(close, timeperiod=14)
    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    ema_21 = talib.EMA(close, timeperiod=21)
    ema_50 = talib.EMA(close, timeperiod=50)
    ema_200 = talib.EMA(close, timeperiod=200)

    rows = []
    for i in range(n):
        r = _IndRow(weekly_ph[i].date)
        r._ph = weekly_ph[i]
        if i >= 13:
            r.rsi = float(rsi[i]) if not math.isnan(rsi[i]) else None
        if i >= 25:
            r.macd = float(macd[i]) if not math.isnan(macd[i]) else None
            r.macd_signal = float(macd_signal[i]) if not math.isnan(macd_signal[i]) else None
            r.macd_hist = float(macd_hist[i]) if not math.isnan(macd_hist[i]) else None
        if i >= 20:
            r.ema_21 = float(ema_21[i]) if not math.isnan(ema_21[i]) else None
        if i >= 49:
            r.ema_50 = float(ema_50[i]) if not math.isnan(ema_50[i]) else None
        if i >= 199:
            r.ema_200 = float(ema_200[i]) if not math.isnan(ema_200[i]) else None
        rows.append(r)
    return rows


# Production score functions + weekly adjustment (imported once per process).
_STOCK_CLS = None
_WADJ = None
_WCOMP = None


def _load_prod():
    global _STOCK_CLS, _WADJ, _WCOMP
    if _STOCK_CLS is not None:
        return
    from database.models.core import Stock
    from database.utils.scoring import calculate_weekly_adjustment, calculate_weekly_composite
    _STOCK_CLS = Stock
    _WADJ = calculate_weekly_adjustment
    _WCOMP = calculate_weekly_composite


def _weekly_scores_for_target(stock, target_week_start, ind_cache_asc):
    """Compute (trend, rsi, macd) weekly component scores AS OF target_week_start
    using the production score functions. ind_cache_asc must contain only bars
    with date <= target_week_start. The functions want DESC-sorted caches and a
    date→ph dict; we slice/relabel here."""
    # Restrict to <= target (defensive; caller already truncates)
    asc = [r for r in ind_cache_asc if r.date <= target_week_start]
    if not asc:
        return None, None, None
    desc = list(reversed(asc))                       # DESC by date
    ph_cache = {r.date: r._ph for r in asc if r._ph is not None}

    # trend (lookback 20 -> needs 21 rows, current ema_21/ema_50)
    trend = stock.calculate_trend_score(
        target_week_start, weekly=True, _ind_cache=desc, _ph_cache=ph_cache)
    # rsi (needs trend bias + macdh_raw/pct_from_ema50 are daily-only None for weekly)
    rsi = stock.calculate_rsi_score(
        target_week_start, trend_score=trend, weekly=True,
        _ind_cache=desc, _ph_cache=ph_cache)
    # macd
    macd = stock.calculate_macd_score(
        target_week_start, weekly=True, _ind_cache=desc, _ph_cache=ph_cache)
    return trend, rsi, macd


def _wadj_from_targets(stock, ind_cache, cur_wk, prev_wk):
    """Compute (w_comp,w_bias,w_mom,w_adj,trend,rsi,macd) using cur_wk as the
    'current' weekly context and prev_wk as the 'previous' (momentum) context.
    Returns None if the current target is uncomputable."""
    asc_cur = [r for r in ind_cache if r.date <= cur_wk]
    if not asc_cur or asc_cur[-1].date != cur_wk:
        return None
    trend, rsi, macd = _weekly_scores_for_target(stock, cur_wk, ind_cache)
    if None in (trend, rsi, macd):
        return None
    p_trend = p_rsi = p_macd = None
    asc_prev = [r for r in ind_cache if r.date <= prev_wk]
    if asc_prev and asc_prev[-1].date == prev_wk:
        p_trend, p_rsi, p_macd = _weekly_scores_for_target(stock, prev_wk, ind_cache)
    total, detail = _WADJ(trend, rsi, macd, p_trend, p_rsi, p_macd)
    if detail is None:
        return None
    return {
        "w_comp": detail.get("w_comp"), "w_bias": detail.get("w_bias"),
        "w_mom": detail.get("w_mom"), "w_adj": detail.get("w_adj"),
        "trend": int(trend), "rsi": int(rsi), "macd": int(macd),
    }


def pit_weekly_features(stock, signal_date, daily_asc):
    """Reconstruct BOTH weekly contexts point-in-time (no future bars):

    PARTIAL (state 2): current week truncated Mon->signal_date is the 'current'
      context; last-completed week is 'previous'. This is the freshest weekly
      reading; it is what a transition-blend would converge toward.
    COMPLETED (state 1): last-completed week is 'current'; the week before it is
      'previous'. This is the convention the LIVE single-row scorer uses
      (`weekly_score` property = weekday()+7) and is the validation anchor.

    Returns dict with pit_* (PARTIAL) and comp_* (COMPLETED) fields, or None.

    daily_asc: list of (date,open,high,low,close,volume) ASC, bars <=signal_date."""
    _load_prod()
    wk = _week_start(signal_date)               # current week's Monday
    prev_wk = wk - timedelta(days=7)            # last-completed week's Monday
    prev2_wk = wk - timedelta(days=14)          # week before that

    weekly_ph = _aggregate_weekly(daily_asc, signal_date)
    if len(weekly_ph) < 30:
        return None
    weekly_ph = weekly_ph[-MAX_WEEKS_TAIL:]
    ind_cache = _build_ind_cache(weekly_ph)
    if not ind_cache:
        return None

    # PARTIAL: current(partial wk) vs previous(last-completed)
    partial = _wadj_from_targets(stock, ind_cache, wk, prev_wk)
    # COMPLETED (live anchor): current(last-completed) vs previous(week before)
    completed = _wadj_from_targets(stock, ind_cache, prev_wk, prev2_wk)
    if partial is None and completed is None:
        return None

    out = {}
    if partial is not None:
        out.update({
            "pit_w_comp": partial["w_comp"], "pit_w_bias": partial["w_bias"],
            "pit_w_mom": partial["w_mom"], "pit_w_adj": partial["w_adj"],
            "pit_trend": partial["trend"], "pit_rsi": partial["rsi"], "pit_macd": partial["macd"],
        })
    else:
        out.update({"pit_w_comp": None, "pit_w_bias": None, "pit_w_mom": None,
                    "pit_w_adj": None, "pit_trend": None, "pit_rsi": None, "pit_macd": None})
    if completed is not None:
        out.update({
            "comp_w_comp": completed["w_comp"], "comp_w_bias": completed["w_bias"],
            "comp_w_mom": completed["w_mom"], "comp_w_adj": completed["w_adj"],
        })
    else:
        out.update({"comp_w_comp": None, "comp_w_bias": None,
                    "comp_w_mom": None, "comp_w_adj": None})
    return out


# ----------------------------------------------------------------------------
# Daily-bar loader (raw SQL, mirrors build_ledger.py DB usage).
# ----------------------------------------------------------------------------
def _load_daily_map(symbols, upto):
    """Returns {symbol: [(date,o,h,l,c,v) ASC]} for date<=upto, in batches."""
    from database.trader_database import DB
    out = {s: [] for s in symbols}
    syms = list(symbols)
    CH = 200
    for i in range(0, len(syms), CH):
        chunk = syms[i:i + CH]
        in_clause = ",".join("'%s'" % s.replace("'", "''") for s in chunk)
        sql = (
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM price_history WHERE symbol IN (%s) AND date <= '%s' "
            "ORDER BY symbol, date ASC" % (in_clause, upto)
        )
        for sym, d, o, h, l, c, v in DB.execute_sql(sql).fetchall():
            out[sym].append((d, float(o), float(h), float(l), float(c), int(v or 0)))
    return out


def _get_stock(symbol, _cache={}):
    from database.models.core import Stock
    st = _cache.get(symbol)
    if st is None:
        st = Stock.get_or_none(Stock.symbol == symbol)
        _cache[symbol] = st
    return st


# ----------------------------------------------------------------------------
# PHASE 2 — validate against live score_intraday_logs
# ----------------------------------------------------------------------------
def validate(limit_syms=None):
    _load_prod()
    from database.trader_database import DB
    print("=== PHASE 2: validate PIT reconstruction vs live score_intraday_logs ===", flush=True)
    # Pull one representative log row per (symbol,date): take the LAST logged_at
    # (final score for the day, which is the point-in-time partial-week state).
    sql = (
        "SELECT t.symbol, t.date, t.weight_info_json FROM score_intraday_logs t "
        "JOIN (SELECT symbol, date, MAX(logged_at) AS mx FROM score_intraday_logs "
        "GROUP BY symbol, date) g "
        "ON t.symbol=g.symbol AND t.date=g.date AND t.logged_at=g.mx"
    )
    rows = DB.execute_sql(sql).fetchall()
    by_date = {}
    for sym, d, wij in rows:
        try:
            j = json.loads(wij)
        except Exception:
            continue
        if j.get("w_comp") is None and j.get("w_adj") is None:
            continue
        by_date.setdefault(d, []).append((sym, j))
    upto = max(by_date)
    syms_all = sorted({sym for lst in by_date.values() for sym, _ in lst})
    if limit_syms:
        syms_all = syms_all[:limit_syms]
        keep = set(syms_all)
        by_date = {d: [(s, j) for s, j in lst if s in keep] for d, lst in by_date.items()}
    print("validation dates:", {str(d): len(lst) for d, lst in sorted(by_date.items())},
          "| symbols:", len(syms_all), flush=True)

    daily_map = _load_daily_map(syms_all, upto)

    rows_out = []
    for d in sorted(by_date):
        bars_into = d.weekday() + 1
        for sym, j in by_date[d]:
            daily = daily_map.get(sym)
            if not daily:
                continue
            st = _get_stock(sym)
            if st is None:
                continue
            try:
                pit = pit_weekly_features(st, d, daily)
            except Exception as e:
                pit = None
            if pit is None:
                continue
            rows_out.append({
                "symbol": sym, "date": str(d), "dow": d.weekday(), "bars_into": bars_into,
                "live_w_comp": j.get("w_comp"), "live_w_adj": j.get("w_adj"), "live_w_mom": j.get("w_mom"),
                # COMPLETED PIT = live convention (last-completed week) -> faithfulness anchor
                "comp_w_comp": pit["comp_w_comp"], "comp_w_adj": pit["comp_w_adj"], "comp_w_mom": pit["comp_w_mom"],
                # PARTIAL PIT = freshest current-week reading
                "pit_w_comp": pit["pit_w_comp"], "pit_w_adj": pit["pit_w_adj"], "pit_w_mom": pit["pit_w_mom"],
            })

    import polars as pl
    vdf = pl.DataFrame(rows_out)
    vpath = os.path.join(OUT_DIR, "validation_vs_live.parquet")
    os.makedirs(OUT_DIR, exist_ok=True)
    vdf.write_parquet(vpath)

    def _report(df, col_live, col_pit, label):
        s = df.filter(pl.col(col_live).is_not_null() & pl.col(col_pit).is_not_null())
        if s.height == 0:
            print("  %s: no rows" % label); return None
        diff = (s[col_pit] - s[col_live])
        ad = diff.abs()
        within2 = (ad <= 2.0).sum() / s.height * 100
        within5 = (ad <= 5.0).sum() / s.height * 100
        print("  %-10s N=%d  mean|d|=%.3f  med|d|=%.3f  p90|d|=%.3f  %%<=2=%.1f  %%<=5=%.1f  bias(mean d)=%.3f"
              % (label, s.height, ad.mean(), ad.median(), ad.quantile(0.90), within2, within5, diff.mean()))
        return float(ad.mean()), float(within2)

    print("\n=== ANCHOR: COMPLETED-week PIT vs live (live uses last-completed `weekly_score` property; should MATCH) ===", flush=True)
    anchor = {}
    anchor["w_comp"] = _report(vdf, "live_w_comp", "comp_w_comp", "w_comp")
    anchor["w_adj"] = _report(vdf, "live_w_adj", "comp_w_adj", "w_adj")
    anchor["w_mom"] = _report(vdf, "live_w_mom", "comp_w_mom", "w_mom")

    print("\n=== PARTIAL-week PIT vs live (freshest current-week reading; EXPECTED to differ from live's lagged week) ===", flush=True)
    partial = {}
    partial["w_comp"] = _report(vdf, "live_w_comp", "pit_w_comp", "w_comp")
    partial["w_adj"] = _report(vdf, "live_w_adj", "pit_w_adj", "w_adj")
    partial["w_mom"] = _report(vdf, "live_w_mom", "pit_w_mom", "w_mom")

    print("\n--- ANCHOR (completed PIT vs live) by bars-into-week ---", flush=True)
    for bw in sorted(vdf["bars_into"].unique().to_list()):
        sub = vdf.filter(pl.col("bars_into") == bw)
        tag = "current-week-partial-elapsed" if bw < 5 else "current-week-complete"
        print("  bars_into=%d (%s) N=%d:" % (bw, tag, sub.height), flush=True)
        _report(sub, "live_w_comp", "comp_w_comp", "  comp.w_comp")
        _report(sub, "live_w_adj", "comp_w_adj", "  comp.w_adj")

    summ = {"validation_parquet": vpath, "n_rows": vdf.height,
            "anchor_completed_vs_live": anchor, "partial_vs_live": partial}
    with open(os.path.join(OUT_DIR, "validation_summary.json"), "w") as f:
        json.dump(summ, f, indent=2)
    print("\nwrote", vpath, "and validation_summary.json", flush=True)
    return summ


# ----------------------------------------------------------------------------
# PHASE 1 — build the holdout-locked PIT parquet keyed (symbol,date)
# ----------------------------------------------------------------------------
def _ledger_keys():
    import polars as pl
    df = pl.read_parquet(LEDGER).select(["symbol", "date"]).unique()
    # holdout lock at materialization (<= cutoff)
    df = df.filter(pl.col("date") <= CUTOFF)
    keys = [(r["symbol"], r["date"]) for r in df.iter_rows(named=True)]
    return keys


def _build_worker(args):
    """Worker: given a symbol and its list of signal-date strings, return rows."""
    symbol, dates_iso = args
    # Each worker re-imports DB lazily via _load_daily_map / _get_stock.
    from datetime import date as _date
    upto = max(dates_iso)
    daily_map = _load_daily_map([symbol], upto)
    daily = daily_map.get(symbol) or []
    st = _get_stock(symbol)
    out = []
    if st is None or not daily:
        return out
    for diso in dates_iso:
        y, m, dd = (int(x) for x in diso.split("-"))
        d = _date(y, m, dd)
        try:
            pit = pit_weekly_features(st, d, daily)
        except Exception:
            pit = None
        if pit is None:
            out.append({"symbol": symbol, "date": diso,
                        "pit_w_comp": None, "pit_w_mom": None, "pit_w_bias": None, "pit_w_adj": None,
                        "comp_w_comp": None, "comp_w_mom": None, "comp_w_bias": None, "comp_w_adj": None})
        else:
            out.append({"symbol": symbol, "date": diso,
                        "pit_w_comp": pit["pit_w_comp"], "pit_w_mom": pit["pit_w_mom"],
                        "pit_w_bias": pit["pit_w_bias"], "pit_w_adj": pit["pit_w_adj"],
                        "comp_w_comp": pit["comp_w_comp"], "comp_w_mom": pit["comp_w_mom"],
                        "comp_w_bias": pit["comp_w_bias"], "comp_w_adj": pit["comp_w_adj"]})
    return out


def build(workers=None, force=False):
    import polars as pl
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(OUT_PARQUET) and not force:
        df = pl.read_parquet(OUT_PARQUET)
        print("[cache hit] %s (%d rows)" % (OUT_PARQUET, df.height), flush=True)
        return OUT_PARQUET

    t0 = time.time()
    keys = _ledger_keys()
    # holdout guard
    from experiments._holdout import assert_no_holdout_leak
    import polars as _pl
    assert_no_holdout_leak(_pl.DataFrame({"date": [k[1] for k in keys]}), context="weekly_pit.build")

    by_sym = {}
    for sym, diso in keys:
        by_sym.setdefault(sym, []).append(diso)
    work = [(sym, sorted(dates)) for sym, dates in by_sym.items()]
    print("PIT build: %d symbols, %d (sym,date) keys" % (len(work), len(keys)), flush=True)

    rows = []
    if workers and workers > 1:
        import multiprocessing as mp
        with mp.Pool(workers) as pool:
            done = 0
            for res in pool.imap_unordered(_build_worker, work, chunksize=2):
                rows.extend(res)
                done += 1
                if done % 50 == 0:
                    print("  %d/%d symbols done (%.0fs)" % (done, len(work), time.time() - t0), flush=True)
    else:
        for i, w in enumerate(work):
            rows.extend(_build_worker(w))
            if (i + 1) % 50 == 0:
                print("  %d/%d symbols done (%.0fs)" % (i + 1, len(work), time.time() - t0), flush=True)

    df = pl.DataFrame(rows)
    # final holdout assert
    from experiments._holdout import assert_no_holdout_leak
    assert_no_holdout_leak(df, context="weekly_pit.build.final")
    df.write_parquet(OUT_PARQUET)
    n_ok = df.filter(pl.col("pit_w_comp").is_not_null()).height
    print("wrote %s: %d rows (%d with pit_w_comp), %.0fs"
          % (OUT_PARQUET, df.height, n_ok, time.time() - t0), flush=True)
    return OUT_PARQUET


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if cmd == "validate":
        lim = None
        if "--syms" in sys.argv:
            lim = int(sys.argv[sys.argv.index("--syms") + 1])
        validate(limit_syms=lim)
    elif cmd == "build":
        w = None
        if "--workers" in sys.argv:
            w = int(sys.argv[sys.argv.index("--workers") + 1])
        force = "--force" in sys.argv
        build(workers=w, force=force)
    else:
        print("usage: build_pit_weekly.py [validate|build] [--workers N] [--syms N] [--force]")
