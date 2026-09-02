"""FORENSIC AUDIT of the Polygon BS-IV panel's underlying-spot price space.

WHAT THIS MEASURES (read-only; no verdict, no recommendation)
------------------------------------------------------------
`experiments/data_ingest/polygon_iv_ingest.py` anchors its "spot" on MySQL
`price_history.close`. That column is written by trader.py's yfinance pull with
auto_adjust=True, i.e. it is BACK-ADJUSTED for splits AND dividends/spinoffs.
Historical option STRIKES are in AS-TRADED dollars. This script quantifies the
gap between the two price spaces for every row of
`.cache/polygon_iv/iv_ledger_polygon.parquet`, and what that gap did to the
"ATM" strike the panel selected.

METHOD
  spot_unadj(t) = yf_Close(t, auto_adjust=False) * PROD(split_ratio(s), s > t)
  -- the exact function used by the corrected real-premium harness
  (`experiments/polygon_real_premium/pull.py: load_unadj_spots /
  apply_forward_splits`), REUSED here rather than reimplemented. It was
  validated against NVDA (17.18 x 10 = 171.81), MMM (118.52 x 1.196 = 141.75,
  which matches the option chain that day) and AZN (ADR ratio change).

  adj_factor        = spot_unadj / price_history.close      (>1 => adjusted low)
  true_moneyness    = atm_strike / spot_unadj               (what it ACTUALLY was)
  intended_moneyness= atm_strike / price_history.close      (~1.0 by construction)

HARD INVARIANTS
  * READ-ONLY on MySQL and on every existing .cache artifact. The only file this
    script writes is its own spot cache (.cache/polygon_real_premium/
    _panel_audit_spot.parquet) and experiments/polygon_real_premium/PANEL_AUDIT.md.
  * ASCII-only stdout. No API keys printed (none are used -- yfinance only).
  * polars: infer_schema_length=None on row-built frames; fill_nan(None) at one
    choke point before null checks.

Usage
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/polygon_real_premium/panel_audit.py
    ... --refresh-spot     # force a yfinance re-fetch
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))          # pin repo root FIRST (worktree PYTHONPATH trap)
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import polars as pl                          # noqa: E402

# REUSE the validated as-traded spot machinery. Do not reimplement.
from pull import load_unadj_spots, realized_vol   # noqa: E402

PANEL = _ROOT / ".cache" / "polygon_iv" / "iv_ledger_polygon.parquet"
REAL = _ROOT / ".cache" / "polygon_real_premium" / "real_premium_ledger.parquet"
SEED_SPOT = _ROOT / ".cache" / "polygon_real_premium" / "_unadj_spot.parquet"
AUDIT_SPOT = _ROOT / ".cache" / "polygon_real_premium" / "_panel_audit_spot.parquet"
OUT_MD = _HERE.parent / "PANEL_AUDIT.md"

VOL_LOOKBACK = 60           # trading days, matches strategy_config.STRATEGY_30DTE.VOL_LOOKBACK
TRADING_DAYS = 252

_LINES: list[str] = []


def emit(s: str = "") -> None:
    """Print to stdout AND buffer for the markdown report. ASCII only."""
    s = str(s)
    print(s, flush=True)
    _LINES.append(s)


# ---------------------------------------------------------------------------
# Pure stats helpers (no scipy dependency).
# ---------------------------------------------------------------------------
def quant(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def dist_row(name: str, xs: list[float]) -> str:
    if not xs:
        return "| %s | 0 | - | - | - | - | - | - | - | - |" % name
    mean = sum(xs) / len(xs)
    return "| %s | %d | %.4f | %.4f | %.4f | %.4f | %.4f | %.4f | %.4f | %.4f |" % (
        name, len(xs), min(xs), quant(xs, 0.10), quant(xs, 0.25), quant(xs, 0.50),
        quant(xs, 0.75), quant(xs, 0.90), max(xs), mean)


DIST_HEADER = ("| group | n | min | p10 | p25 | median | p75 | p90 | max | mean |\n"
               "|---|---|---|---|---|---|---|---|---|---|")


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return pearson(_ranks(xs), _ranks(ys))


def frac(xs: list[float], pred) -> str:
    if not xs:
        return "0 / 0 (n/a)"
    k = sum(1 for x in xs if pred(x))
    return "%d / %d (%.1f%%)" % (k, len(xs), 100.0 * k / len(xs))


# ---------------------------------------------------------------------------
# MySQL (READ-ONLY): the adjusted closes the panel actually used, plus enough
# history before the window to compute a causal 60-day realized vol.
# ---------------------------------------------------------------------------
def load_adjusted_closes(symbols: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    from database.trader_database import DB
    out: dict[str, dict[str, float]] = {}
    CH = 200
    for i in range(0, len(symbols), CH):
        chunk = symbols[i:i + CH]
        ph = ",".join(["%s"] * len(chunk))
        sql = ("SELECT symbol, date, close FROM price_history "
               "WHERE symbol IN (%s) AND date >= %%s AND date <= %%s" % ph)
        cur = DB.execute_sql(sql, tuple(chunk) + (start, end))
        for sym, d, c in cur.fetchall():
            if c is None:
                continue
            ds = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
            out.setdefault(sym, {})[ds] = float(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-spot", action="store_true")
    args = ap.parse_args()

    t_all = time.time()

    if not PANEL.exists():
        emit("ERROR: missing %s" % PANEL)
        return 2

    panel = pl.read_parquet(PANEL)
    panel = panel.with_columns([
        pl.col(c).fill_nan(None) for c, t in panel.schema.items() if t == pl.Float64
    ])
    syms = sorted(panel["symbol"].unique().to_list())
    d_min = panel["date"].min()
    d_max = panel["date"].max()

    emit("=" * 78)
    emit("POLYGON BS-IV PANEL AUDIT -- adjusted-vs-as-traded spot contamination")
    emit("=" * 78)
    emit("")
    emit("panel        : %s" % PANEL.as_posix())
    emit("rows         : %d   symbols: %d   dates: %s .. %s" % (panel.height, len(syms), d_min, d_max))
    emit("")

    # ---- as-traded spot (REUSED machinery, own cache file) ----
    if not AUDIT_SPOT.exists() and SEED_SPOT.exists() and not args.refresh_spot:
        # Warm-start from the real-premium harness's cache (READ-ONLY copy out of
        # it) so only the ~100 symbols unique to this panel hit the network.
        shutil.copyfile(SEED_SPOT, AUDIT_SPOT)
        emit("[info] seeded audit spot cache from %s (copy; original untouched)" % SEED_SPOT.name)

    t0 = time.time()
    unadj = load_unadj_spots(syms, d_min, d_max, cache=AUDIT_SPOT,
                             refresh=args.refresh_spot, batch=50)
    t_spot = time.time() - t0
    n_res = sum(1 for s in syms if unadj.get(s))
    emit("[timing] as-traded spot resolution: %.1fs for %d panel symbols (%d resolved; "
         "cache holds %d symbols incl. the seeded real-premium universe)"
         % (t_spot, len(syms), n_res, len(unadj)))

    # ---- adjusted closes from MySQL (read-only) ----
    hist_start = (date.fromisoformat(d_min) - timedelta(days=200)).isoformat()
    t0 = time.time()
    adj = load_adjusted_closes(syms, hist_start, d_max)
    emit("[timing] price_history read: %.1fs (%d symbols)" % (time.time() - t0, len(adj)))
    emit("")

    # ---- per-symbol causal realized vol series (adjusted closes; returns are
    #      adjustment-invariant except across a split/dividend date, which is the
    #      point of using the adjusted series here) ----
    rv_at: dict[str, dict[str, float]] = {}
    for sym, m in adj.items():
        ds = sorted(m)
        cs = [m[d] for d in ds]
        idx = {d: i for i, d in enumerate(ds)}
        rv_at[sym] = {"__ds": ds, "__cs": cs, "__idx": idx}  # type: ignore

    def rv_for(sym: str, d: str):
        s = rv_at.get(sym)
        if not s:
            return None
        i = s["__idx"].get(d)                                # type: ignore
        if i is None:
            return None
        v = realized_vol(s["__cs"], i, lookback=VOL_LOOKBACK)  # type: ignore
        if v is None:
            return None
        return v / 100.0 * math.sqrt(TRADING_DAYS) * 100.0   # annualized vol, percent

    # ---- assemble the audit frame ----
    recs = []
    for sym, d, ov, strike, dte, prem, iv, volpct in zip(
            panel["symbol"].to_list(), panel["date"].to_list(), panel["overall"].to_list(),
            panel["atm_strike"].to_list(), panel["dte"].to_list(),
            panel["entry_premium"].to_list(), panel["atm_iv"].to_list(),
            panel["vol_pct"].to_list()):
        a = (adj.get(sym) or {}).get(d)
        u = (unadj.get(sym) or {}).get(d)
        try:
            p_exp = (date.fromisoformat(d) + timedelta(days=int(dte))).isoformat() if dte else None
        except Exception:
            p_exp = None
        recs.append({
            "symbol": sym, "date": d, "year": d[:4], "overall": ov, "panel_exp": p_exp,
            "atm_strike": strike, "dte": dte, "entry_premium": prem, "atm_iv": iv,
            "vol_pct": volpct,
            "adj_close": a, "spot_unadj": u,
            "adj_factor": (u / a) if (a and u and a > 0) else None,
            "true_moneyness": (strike / u) if (u and strike and u > 0) else None,
            "intended_moneyness": (strike / a) if (a and strike and a > 0) else None,
            "rv_ann": rv_for(sym, d),
        })
    aud = pl.DataFrame(recs, infer_schema_length=None)
    aud = aud.with_columns([pl.col(c).fill_nan(None)
                            for c, t in aud.schema.items() if t == pl.Float64])

    n_adj = int(aud["adj_close"].is_not_null().sum())
    n_un = int(aud["spot_unadj"].is_not_null().sum())
    n_both = int(aud["adj_factor"].is_not_null().sum())
    emit("## 0. Coverage")
    emit("")
    emit("| quantity | rows | pct of panel |")
    emit("|---|---|---|")
    emit("| panel rows | %d | 100.0%% |" % panel.height)
    emit("| price_history adjusted close resolved | %d | %.1f%% |" % (n_adj, 100.0 * n_adj / panel.height))
    emit("| yfinance as-traded spot resolved | %d | %.1f%% |" % (n_un, 100.0 * n_un / panel.height))
    emit("| BOTH resolved (audit sample) | %d | %.1f%% |" % (n_both, 100.0 * n_both / panel.height))
    emit("")
    miss_syms = sorted(set(syms) - set(unadj.keys()))
    emit("Symbols with no yfinance as-traded series (delisted / renamed): %d%s"
         % (len(miss_syms), (" -- e.g. " + ", ".join(miss_syms[:12])) if miss_syms else ""))
    emit("")

    ok = aud.filter(pl.col("adj_factor").is_not_null())
    af = ok["adj_factor"].to_list()

    # ---------------------------------------------------------------- 1. drift
    emit("## 1. Adjustment drift: adj_factor = as_traded_close / price_history.close")
    emit("")
    emit("A no-dividend, no-split name has adj_factor exactly 1.0, so the MEDIAN row")
    emit("is uncontaminated by construction; the damage lives in the upper tail. The")
    emit("'DRIFTED ONLY' row below conditions on adj_factor > 1.001.")
    emit("")
    emit(DIST_HEADER)
    emit(dist_row("ALL", af))
    emit(dist_row("DRIFTED ONLY (>1.001)", [x for x in af if x > 1.001]))
    for y in sorted(ok["year"].unique().to_list()):
        emit(dist_row(y, ok.filter(pl.col("year") == y)["adj_factor"].to_list()))
    emit("")
    n_drift = sum(1 for x in af if x > 1.001)
    emit("Rows with ANY upward drift (adj_factor > 1.001): %s" % frac(af, lambda x: x > 1.001))
    emit("Distinct symbols with median adj_factor > 1.001: %d of %d"
         % (int(ok.group_by("symbol").agg(pl.col("adj_factor").median().alias("m"))
                  .filter(pl.col("m") > 1.001).height),
            ok["symbol"].n_unique()))
    emit("")
    emit("| threshold | rows above |")
    emit("|---|---|")
    for th in (1.02, 1.05, 1.10, 1.25):
        emit("| adj_factor > %.2f | %s |" % (th, frac(af, lambda x, t=th: x > t)))
    emit("| adj_factor < 0.98 (adjusted ABOVE as-traded) | %s |" % frac(af, lambda x: x < 0.98))
    emit("")
    emit("Rows by year and threshold (blast radius):")
    emit("")
    emit("| year | n | >1.02 | >1.05 | >1.10 | >1.25 | median |")
    emit("|---|---|---|---|---|---|---|")
    for y in sorted(ok["year"].unique().to_list()):
        v = ok.filter(pl.col("year") == y)["adj_factor"].to_list()
        emit("| %s | %d | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.4f |" % (
            y, len(v),
            100.0 * sum(1 for x in v if x > 1.02) / len(v),
            100.0 * sum(1 for x in v if x > 1.05) / len(v),
            100.0 * sum(1 for x in v if x > 1.10) / len(v),
            100.0 * sum(1 for x in v if x > 1.25) / len(v),
            quant(v, 0.50)))
    emit("")

    # ------------------------------------------------------------ 2. strike err
    tm = ok.filter(pl.col("true_moneyness").is_not_null())
    tml = tm["true_moneyness"].to_list()
    iml = tm["intended_moneyness"].to_list()
    emit("## 2. Strike error: what the 'ATM' contract actually was")
    emit("")
    emit(DIST_HEADER)
    emit(dist_row("true_moneyness (strike / as_traded_spot)", tml))
    emit(dist_row("intended_moneyness (strike / adjusted_close)", iml))
    emit("")
    emit("For a CALL, moneyness < 1 means IN the money.")
    emit("")
    emit("| condition | rows |")
    emit("|---|---|")
    emit("| ITM at all (true_moneyness < 1.000) | %s |" % frac(tml, lambda x: x < 1.0))
    for th, lbl in ((0.95, "5%"), (0.90, "10%"), (0.75, "25%")):
        emit("| more than %s ITM (true_moneyness < %.2f) | %s |"
             % (lbl, th, frac(tml, lambda x, t=th: x < t)))
    emit("| more than 5%% OTM (true_moneyness > 1.05) | %s |" % frac(tml, lambda x: x > 1.05))
    emit("")
    emit("| year | n | median true_moneyness | >5% ITM | >10% ITM | >25% ITM |")
    emit("|---|---|---|---|---|---|")
    for y in sorted(tm["year"].unique().to_list()):
        v = tm.filter(pl.col("year") == y)["true_moneyness"].to_list()
        emit("| %s | %d | %.4f | %.1f%% | %.1f%% | %.1f%% |" % (
            y, len(v), quant(v, 0.50),
            100.0 * sum(1 for x in v if x < 0.95) / len(v),
            100.0 * sum(1 for x in v if x < 0.90) / len(v),
            100.0 * sum(1 for x in v if x < 0.75) / len(v)))
    emit("")

    # ------------------------------------------------------- 3. calm-name test
    emit("## 3. Calm-name hypothesis: is drift concentrated in low-volatility names?")
    emit("")
    emit("Volatility measure: causal %d-trading-day realized vol of the price_history"
         % VOL_LOOKBACK)
    emit("close series ending on the signal date, annualized (x sqrt(%d)), in percent."
         % TRADING_DAYS)
    emit("Cross-checked against the panel's own stored `vol_pct` (daily sigma, percent).")
    emit("")
    rv = ok.filter(pl.col("rv_ann").is_not_null())
    xs = rv["adj_factor"].to_list()
    ys = rv["rv_ann"].to_list()
    emit("Row level (n=%d):  pearson(adj_factor, realized_vol) = %s   spearman = %s"
         % (len(xs),
            ("%.4f" % pearson(xs, ys)) if pearson(xs, ys) is not None else "n/a",
            ("%.4f" % spearman(xs, ys)) if spearman(xs, ys) is not None else "n/a"))
    vp = ok.filter(pl.col("vol_pct").is_not_null())
    xs2 = vp["adj_factor"].to_list()
    ys2 = vp["vol_pct"].to_list()
    emit("Row level (n=%d):  pearson(adj_factor, panel vol_pct) = %s   spearman = %s"
         % (len(xs2),
            ("%.4f" % pearson(xs2, ys2)) if pearson(xs2, ys2) is not None else "n/a",
            ("%.4f" % spearman(xs2, ys2)) if spearman(xs2, ys2) is not None else "n/a"))

    sym_agg = (rv.group_by("symbol")
                 .agg([pl.col("adj_factor").median().alias("af"),
                       pl.col("rv_ann").median().alias("rv"),
                       pl.len().alias("n")])
                 .filter(pl.col("af").is_not_null() & pl.col("rv").is_not_null()))
    sx = sym_agg["af"].to_list()
    sy = sym_agg["rv"].to_list()
    emit("Symbol level (n=%d symbols, per-symbol medians):  pearson = %s   spearman = %s"
         % (len(sx),
            ("%.4f" % pearson(sx, sy)) if pearson(sx, sy) is not None else "n/a",
            ("%.4f" % spearman(sx, sy)) if spearman(sx, sy) is not None else "n/a"))
    emit("")

    # terciles by symbol-level realized vol
    if len(sy) >= 6:
        cut1 = quant(sy, 1 / 3.0)
        cut2 = quant(sy, 2 / 3.0)
        buckets = {"T1 calmest": [], "T2 mid": [], "T3 most volatile": []}
        sym_bucket: dict[str, str] = {}
        for s, a, v in zip(sym_agg["symbol"].to_list(), sx, sy):
            b = "T1 calmest" if v <= cut1 else ("T2 mid" if v <= cut2 else "T3 most volatile")
            buckets[b].append((s, a, v))
            sym_bucket[s] = b
        emit("Symbol-level realized-vol terciles (cuts at %.1f%% / %.1f%% annualized):"
             % (cut1, cut2))
        emit("")
        emit("| tercile | symbols | median ann.vol | median adj_factor | mean adj_factor | "
             "pct symbols adj_factor>1.05 |")
        emit("|---|---|---|---|---|---|")
        for b in ("T1 calmest", "T2 mid", "T3 most volatile"):
            rowsb = buckets[b]
            avs = [a for _, a, _ in rowsb]
            vvs = [v for _, _, v in rowsb]
            emit("| %s | %d | %.1f%% | %.4f | %.4f | %.1f%% |" % (
                b, len(rowsb), quant(vvs, 0.5), quant(avs, 0.5),
                sum(avs) / len(avs),
                100.0 * sum(1 for a in avs if a > 1.05) / len(avs)))
        emit("")
        # row-level view of the same terciles
        emit("Row-level distribution of adj_factor within each symbol tercile:")
        emit("")
        emit(DIST_HEADER)
        for b in ("T1 calmest", "T2 mid", "T3 most volatile"):
            members = {s for s, _, _ in buckets[b]}
            v = rv.filter(pl.col("symbol").is_in(list(members)))["adj_factor"].to_list()
            emit(dist_row(b, v))
        emit("")
        # the 15 worst-drift symbols with their vol
        worst = sorted(zip(sym_agg["symbol"].to_list(), sx, sy, sym_agg["n"].to_list()),
                       key=lambda r: -r[1])[:15]
        emit("15 highest-drift symbols (median adj_factor), with their realized vol:")
        emit("")
        emit("| symbol | rows | median adj_factor | median ann.vol | vol tercile |")
        emit("|---|---|---|---|---|")
        for s, a, v, n in worst:
            emit("| %s | %d | %.4f | %.1f%% | %s |" % (s, n, a, v, sym_bucket.get(s, "-")))
        emit("")

    # --------------------------------------------------- 4. real-ledger cross-check
    emit("## 4. Cross-check against the corrected real-premium ledger")
    emit("")
    if not REAL.exists():
        emit("(%s missing -- skipped)" % REAL.as_posix())
    else:
        real = pl.read_parquet(REAL)
        real = real.with_columns([pl.col(c).fill_nan(None)
                                  for c, t in real.schema.items() if t == pl.Float64])
        rk = (real.filter(pl.col("status") == "kept")
                  .select([pl.col("symbol"), pl.col("signal_date").alias("date"),
                           pl.col("atm_strike").alias("real_strike"),
                           pl.col("entry_premium_real"),
                           pl.col("dte_cal"), pl.col("expiration_date"),
                           pl.col("spot_unadj").alias("real_spot_unadj"),
                           pl.col("entry_price").alias("real_adj_close"),
                           pl.col("moneyness").alias("real_moneyness"),
                           pl.col("liquid_entry")]))
        j = aud.join(rk, on=["symbol", "date"], how="inner")
        emit("Overlap: %d (symbol, date) rows appear in BOTH the panel and the kept rows"
             % j.height)
        emit("of the corrected real-premium ledger.")
        emit("")
        emit("NOTE ON COMPARABILITY: the two harnesses are not selection-identical.")
        emit("The panel queries expiries in [d+20, d+45] and takes the nearest priced")
        emit("strike at whatever expiry the chain returns first; the real ledger picks")
        emit("the expiry whose calendar DTE is closest to 30 within [18,50] and then the")
        emit("nearest strike. Rows are therefore split below into ALL overlap and the")
        emit("SAME-EXPIRATION subset, which isolates the strike-selection effect.")
        emit("")
        j = j.with_columns([
            (pl.col("panel_exp") == pl.col("expiration_date")).alias("same_exp"),
            (pl.col("atm_strike") == pl.col("real_strike")).alias("same_strike"),
            (pl.col("entry_premium") / pl.col("entry_premium_real")).alias("prem_ratio"),
        ])
        j = j.with_columns([pl.col("prem_ratio").fill_nan(None)])

        for label, sub in (("ALL overlap", j),
                           ("SAME-EXPIRATION subset", j.filter(pl.col("same_exp")))):
            if sub.height == 0:
                emit("### %s -- n=0" % label)
                continue
            n = sub.height
            n_same = int(sub["same_strike"].sum())
            emit("### %s (n=%d)" % (label, n))
            emit("")
            emit("| quantity | value |")
            emit("|---|---|")
            emit("| same expiration | %d / %d (%.1f%%) |"
                 % (int(sub["same_exp"].sum()), n, 100.0 * int(sub["same_exp"].sum()) / n))
            emit("| SAME strike | %d / %d (%.1f%%) |" % (n_same, n, 100.0 * n_same / n))
            emit("| DIFFERENT strike | %d / %d (%.1f%%) |"
                 % (n - n_same, n, 100.0 * (n - n_same) / n))
            sr = [a / b for a, b in zip(sub["atm_strike"].to_list(), sub["real_strike"].to_list())
                  if a and b and b > 0]
            pr = [x for x in sub["prem_ratio"].to_list() if x is not None and x > 0]
            emit("")
            emit(DIST_HEADER)
            emit(dist_row("panel_strike / real_strike", sr))
            emit(dist_row("panel_entry_premium / real_entry_premium", pr))
            emit("")
            emit("| premium-ratio band | rows |")
            emit("|---|---|")
            emit("| panel premium > 1.25x real | %s |" % frac(pr, lambda x: x > 1.25))
            emit("| panel premium > 1.50x real | %s |" % frac(pr, lambda x: x > 1.50))
            emit("| panel premium > 2.00x real | %s |" % frac(pr, lambda x: x > 2.00))
            emit("| panel premium < 0.80x real | %s |" % frac(pr, lambda x: x < 0.80))
            emit("")

        # sanity: the two independent as-traded spot resolutions must agree
        sp = [(a, b) for a, b in zip(j["spot_unadj"].to_list(), j["real_spot_unadj"].to_list())
              if a and b and b > 0]
        if sp:
            diffs = [abs(a / b - 1.0) for a, b in sp]
            emit("Spot sanity: this audit's as-traded spot vs the real ledger's, on the")
            emit("%d overlapping rows -- max |ratio-1| = %.2e, p99 = %.2e (they use the same"
                 % (len(diffs), max(diffs), quant(diffs, 0.99)))
            emit("function, so this only checks the cache seeding).")
            emit("")

        # premium ratio vs drift
        pj = j.filter(pl.col("prem_ratio").is_not_null() & pl.col("adj_factor").is_not_null())
        if pj.height > 10:
            c = pearson(pj["adj_factor"].to_list(), pj["prem_ratio"].to_list())
            s = spearman(pj["adj_factor"].to_list(), pj["prem_ratio"].to_list())
            emit("Correlation between adj_factor and the panel/real premium ratio (n=%d): "
                 "pearson %s, spearman %s"
                 % (pj.height,
                    ("%.4f" % c) if c is not None else "n/a",
                    ("%.4f" % s) if s is not None else "n/a"))
            emit("")
            emit("Strike disagreement CONDITIONED on drift (the headline cross-check):")
            emit("")
            emit("| adj_factor filter | n | different strike | median premium ratio |")
            emit("|---|---|---|---|")
            for lo, lbl in ((None, "<= 1.001 (undrifted)"), (1.001, "> 1.001"),
                            (1.02, "> 1.02"), (1.05, "> 1.05"), (1.10, "> 1.10")):
                sub = (pj.filter(pl.col("adj_factor") <= 1.001) if lo is None
                       else pj.filter(pl.col("adj_factor") > lo))
                if sub.height == 0:
                    continue
                nd = int((~sub["same_strike"]).sum())
                emit("| %s | %d | %d (%.1f%%) | %.4f |" % (
                    lbl, sub.height, nd, 100.0 * nd / sub.height,
                    quant(sub["prem_ratio"].to_list(), 0.5)))
            emit("")
            emit("| adj_factor band | n | median premium ratio | median panel_strike/real_strike |")
            emit("|---|---|---|---|")
            bands = [(0.0, 1.02), (1.02, 1.05), (1.05, 1.10), (1.10, 1.25), (1.25, 99.0)]
            for lo, hi in bands:
                sub = pj.filter((pl.col("adj_factor") > lo) & (pl.col("adj_factor") <= hi))
                if sub.height == 0:
                    continue
                srr = [a / b for a, b in zip(sub["atm_strike"].to_list(),
                                             sub["real_strike"].to_list()) if a and b and b > 0]
                emit("| (%.2f, %.2f] | %d | %.4f | %s |" % (
                    lo, hi, sub.height, quant(sub["prem_ratio"].to_list(), 0.5),
                    ("%.4f" % quant(srr, 0.5)) if srr else "-"))
            emit("")

    # --------------------------------------------------------- 5. blast radius
    emit("## 5. Blast radius by year")
    emit("")
    emit("| year | panel rows | audited | >2% drift | >5% drift | >10% drift | "
         "ATM actually >5% ITM | ATM actually >10% ITM |")
    emit("|---|---|---|---|---|---|---|---|")
    for y in sorted(aud["year"].unique().to_list()):
        allr = aud.filter(pl.col("year") == y)
        sub = ok.filter(pl.col("year") == y)
        v = sub["adj_factor"].to_list()
        t = [x for x in sub["true_moneyness"].to_list() if x is not None]
        if not v:
            emit("| %s | %d | 0 | - | - | - | - | - |" % (y, allr.height))
            continue
        emit("| %s | %d | %d | %d (%.1f%%) | %d (%.1f%%) | %d (%.1f%%) | %d (%.1f%%) | %d (%.1f%%) |" % (
            y, allr.height, len(v),
            sum(1 for x in v if x > 1.02), 100.0 * sum(1 for x in v if x > 1.02) / len(v),
            sum(1 for x in v if x > 1.05), 100.0 * sum(1 for x in v if x > 1.05) / len(v),
            sum(1 for x in v if x > 1.10), 100.0 * sum(1 for x in v if x > 1.10) / len(v),
            sum(1 for x in t if x < 0.95), 100.0 * sum(1 for x in t if x < 0.95) / max(len(t), 1),
            sum(1 for x in t if x < 0.90), 100.0 * sum(1 for x in t if x < 0.90) / max(len(t), 1)))
    emit("")

    # score-band split (the OSK cohort is 75+, the panel floor is 70)
    emit("| overall band | rows | audited | median adj_factor | >5% drift | median true_moneyness |")
    emit("|---|---|---|---|---|---|")
    for lo, hi, lbl in ((70, 74, "70-74"), (75, 79, "75-79"), (80, 84, "80-84"),
                        (85, 200, "85+")):
        sub = ok.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        allr = aud.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        if sub.height == 0:
            emit("| %s | %d | 0 | - | - | - |" % (lbl, allr.height))
            continue
        v = sub["adj_factor"].to_list()
        t = [x for x in sub["true_moneyness"].to_list() if x is not None]
        emit("| %s | %d | %d | %.4f | %.1f%% | %s |" % (
            lbl, allr.height, len(v), quant(v, 0.5),
            100.0 * sum(1 for x in v if x > 1.05) / len(v),
            ("%.4f" % quant(t, 0.5)) if t else "-"))
    emit("")

    emit("Total wall clock: %.1fs" % (time.time() - t_all))
    emit("Generated: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    header = [
        "# Polygon BS-IV Panel Audit -- adjusted-vs-as-traded spot contamination",
        "",
        "Measurement only. No verdict on prior findings is offered here.",
        "",
        "Source panel : `.cache/polygon_iv/iv_ledger_polygon.parquet`",
        "Built by     : `experiments/data_ingest/polygon_iv_ingest.py`",
        "Audit script : `experiments/polygon_real_premium/panel_audit.py`",
        "Spot method  : `experiments/polygon_real_premium/pull.py: load_unadj_spots`",
        "(yfinance `auto_adjust=False` Close x cumulative product of FORWARD split",
        "ratios), reused verbatim -- the same function the corrected real-premium",
        "ledger uses.",
        "",
    ]
    body = header + _LINES + [""]
    OUT_MD.write_text("\n".join(body), encoding="ascii", errors="backslashreplace")
    print("\nwrote %s" % OUT_MD, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
