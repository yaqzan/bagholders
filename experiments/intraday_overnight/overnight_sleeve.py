"""Decisive test of the user's refined idea: capture the +17.5bps overnight gap on call signals by
buying a SHORT-DTE call at the close (MOC) and selling at the next open / after a few days, day-of-week
aware. Does shorter DTE 'lever' the overnight pop into compounding alpha, or does theta+spread eat it?

Models the real option P&L with option_pricing.option_pnl_pct (delta + theta + vega), generalized to
short DTE via premium_mult(DTE) = 1.82 * sqrt(DTE/30) and theta = sqrt((DTE-tau)/DTE) - 1 where tau =
CALENDAR days held (so a Fri->Mon overnight pays 3 days of theta for the weekend gap). Entry = signal
close (MOC, so you HOLD the overnight -- no realism gap). Spread modeled as a round-trip % of premium.

In-memory over the OHLC parquet + 75+ call ledger. No DB, no MC. ASCII out.
"""
import os, sys, math, datetime as dt
import numpy as np
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
from option_pricing import option_pnl_pct

OHLC = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
DTES = [3, 5, 7, 15, 30, 45, 60, 90, 180]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def pmult(dte):
    return 1.82 * math.sqrt(dte / 30.0)


def main():
    oh = pl.read_parquet(OHLC)
    led = pl.read_parquet(LED).filter(pl.col("overall") >= 75)
    ser = {}
    for (sym, sub) in oh.group_by("symbol"):
        s = sym[0] if isinstance(sym, tuple) else sym
        sub = sub.sort("date")
        d = sub["date"].to_list()
        ser[s] = (d, np.asarray(sub["open"].to_list(), float), np.asarray(sub["close"].to_list(), float),
                  {dd: i for i, dd in enumerate(d)})
    syms = led["symbol"].to_list(); dts = led["date"].to_list(); vp = led["vol_pct"].to_list()

    # collect, per signal: entry close, sigma, weekday, and exit underlyings + cal-days for each horizon
    # horizons: overnight (next open), +1d (next close), +3d (3rd fwd close)
    recs = []  # dict per signal
    for s, dd, v in zip(syms, dts, vp):
        if s not in ser or not v or v <= 0:
            continue
        dlist, op, cl, idx = ser[s]
        i = idx.get(dd)
        if i is None or i + 1 >= len(dlist):
            continue
        d0 = dt.date.fromisoformat(dd)
        sig = v / 100.0
        u0 = cl[i]
        rec = {"wd": d0.weekday(), "sig": sig, "u0": u0}
        # overnight: next open
        rec["ov_u"] = op[i + 1]; rec["ov_tau"] = (dt.date.fromisoformat(dlist[i + 1]) - d0).days
        # +1d: next close
        rec["d1_u"] = cl[i + 1]; rec["d1_tau"] = rec["ov_tau"]
        # +3d: 3rd forward bar close (if exists)
        if i + 3 < len(dlist):
            rec["d3_u"] = cl[i + 3]; rec["d3_tau"] = (dt.date.fromisoformat(dlist[i + 3]) - d0).days
        else:
            rec["d3_u"] = None
        recs.append(rec)
    n = len(recs)
    print("75+ call signals modeled: %d  (entry=signal close / MOC, so overnight gap is HELD)" % n)
    print("premium_mult(DTE): " + "  ".join("%dDTE=%.2f" % (d, pmult(d)) for d in DTES))
    print("(a 30DTE ATM premium ~= 1.82*sigma of underlying; theta/night = 1-sqrt((DTE-tau)/DTE))\n")

    def pnl_for(rec, dte, mode, spread):
        pm = pmult(dte) * rec["sig"]
        if mode == "ov":
            u, tau = rec["ov_u"], rec["ov_tau"]
        elif mode == "d1":
            u, tau = rec["d1_u"], rec["d1_tau"]
        else:
            if rec["d3_u"] is None:
                return None
            u, tau = rec["d3_u"], rec["d3_tau"]
        if not (u and u > 0):
            return None
        p = option_pnl_pct("low", u, rec["u0"], tau, pm, total_dte=dte, vega_ratio=1.0, delta=0.5)
        return p - spread  # round-trip spread as fraction of premium (both legs cross the market)

    print("=" * 86)
    print("TABLE 1 -- mean option P&L (%% of premium) by DTE x exit, at spread = {0%%, 6%%, 12%%} round-trip")
    print("  exit:  ov=buy close,sell next open (pure overnight)   d1=sell next close   d3=sell 3rd close")
    print("=" * 86)
    print("  DTE  exit |  gross mean   win%% |  net@6%%   net@12%% |   N")
    for dte in DTES:
        for mode in ("ov", "d1", "d3"):
            g = np.array([pnl_for(r, dte, mode, 0.0) for r in recs], dtype=object)
            g = np.array([x for x in g if x is not None], float)
            if not len(g):
                continue
            n6 = g - 0.06; n12 = g - 0.12
            print("  %3d  %-4s | %+8.1f%%  %5.1f%% | %+7.1f%% %+7.1f%% | %5d"
                  % (dte, mode, g.mean()*100, 100*(g > 0).mean(), n6.mean()*100, n12.mean()*100, len(g)))
    # STOCK benchmark = the DTE->inf limit (delta=1, ZERO theta): return-on-CAPITAL is just the
    # overnight underlying return. This is what every option row is competing against per dollar deployed.
    ov_stock = np.array([(r["ov_u"]/r["u0"] - 1.0) for r in recs], float)            # buy close -> sell open
    d1_stock = np.array([(r["d1_u"]/r["u0"] - 1.0) for r in recs], float)            # buy close -> sell next close
    d3_stock = np.array([(r["d3_u"]/r["u0"] - 1.0) for r in recs if r["d3_u"] is not None], float)
    sp = 0.0006  # ~3bps half-spread each way on a liquid stock (round-trip ~6bps)
    print("  --   ----  | (STOCK benchmark = DTE->inf limit, return on CAPITAL, unlevered)")
    print("  STK  ov   | %+8.3f%%  %5.1f%% | net@6bps %+.3f%%          | %5d"
          % (ov_stock.mean()*100, 100*(ov_stock > 0).mean(), (ov_stock.mean()-sp)*100, len(ov_stock)))
    print("  STK  d1   | %+8.3f%%  %5.1f%% | net@6bps %+.3f%%          | %5d"
          % (d1_stock.mean()*100, 100*(d1_stock > 0).mean(), (d1_stock.mean()-sp)*100, len(d1_stock)))
    print("  STK  d3   | %+8.3f%%  %5.1f%% | net@6bps %+.3f%%          | %5d"
          % (d3_stock.mean()*100, 100*(d3_stock > 0).mean(), (d3_stock.mean()-sp)*100, len(d3_stock)))
    print("  NOTE: option rows are return-on-PREMIUM (already leveraged); STK is return-on-capital unlevered.")
    print("        Per dollar of capital, the option's leverage multiplies BOTH the gap-capture AND the")
    print("        theta -- and theta wins until very long DTE, where the option just CONVERGES to STK")
    print("        (minus its theta + wider spread). No DTE strictly beats holding the stock for this.")

    print("\n" + "=" * 86)
    print("TABLE 2 -- DAY-OF-WEEK: pure overnight (buy close, sell next open), by entry weekday")
    print("  Fri = captures the WEEKEND gap (but pays ~3 calendar-days of theta). 5DTE shown.")
    print("=" * 86)
    print("  wd  | overnight gap(bps) | 5DTE ov gross |  net@6%% |  net@12%% |   N")
    for w in range(5):
        rs = [r for r in recs if r["wd"] == w]
        if not rs:
            continue
        gap = np.array([(r["ov_u"]/r["u0"] - 1.0) for r in rs], float)
        g5 = np.array([pnl_for(r, 5, "ov", 0.0) for r in rs], dtype=object)
        g5 = np.array([x for x in g5 if x is not None], float)
        print("  %-3s | %+10.1f       | %+10.1f%%  | %+6.1f%% | %+7.1f%% | %5d"
              % (WD[w], gap.mean()*1e4, g5.mean()*100, (g5.mean()-0.06)*100, (g5.mean()-0.12)*100, len(rs)))

    print("\n" + "=" * 86)
    print("TABLE 3 -- BEST-CASE SCAN: any (DTE x exit x weekday) cell net-positive after 6%% round-trip?")
    print("=" * 86)
    best = []
    for dte in DTES:
        for mode in ("ov", "d1", "d3"):
            for w in range(5):
                rs = [r for r in recs if r["wd"] == w]
                vals = np.array([pnl_for(r, dte, mode, 0.06) for r in rs], dtype=object)
                vals = np.array([x for x in vals if x is not None], float)
                if len(vals) >= 100:
                    best.append((vals.mean(), dte, mode, WD[w], len(vals), 100*(vals > 0).mean()))
    best.sort(reverse=True)
    print("  top 8 cells by net@6%% mean P&L:")
    for m, dte, mode, w, nn, wr in best[:8]:
        flag = "  <== NET POSITIVE" if m > 0 else ""
        print("    %3dDTE %-3s %-3s  net@6%%=%+6.1f%%  win%%=%4.1f  N=%4d%s" % (dte, mode, w, m*100, wr, nn, flag))
    pos = [b for b in best if b[0] > 0]
    print("\n  cells net-positive @6%% spread: %d / %d" % (len(pos), len(best)))
    print("\nDONE")


if __name__ == "__main__":
    main()
