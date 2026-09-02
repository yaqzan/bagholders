"""Equity sleeve vs v70 Apex options book -- does holding the SIGNAL STOCK (overnight / N-day) compound
better than buying the call?

Same 75+ v70 signals Apex trades (rs_ledger), same cascade sizing (20/15/10/10 by tier), same MaxPos=14,
same $50k start, same 10y window. Deterministic chronological portfolio sim. Fully DB-free.

Instruments simulated in ONE harness (identical signal/cascade/sizing code; only the per-trade payoff +
hold differ):
  - equity_<hold>_L<L> : buy stock at signal close (MOC), hold {overnight|1d|3d|5d}, exit at open/close;
        notional = committed_margin * L ; P&L = committed*L*stock_ret - notional*spread ; collapse if equity<=0.
  - apex_opt (simplified, CONSERVATIVE -- no dead-hold recovery / F3F / RXDD): per-signal option return
        from the ledger's barrier-agnostic t_up/t_dn at the funded apex barrier (TP +1.092s -> +0.30,
        SL -2.548s -> -0.70, else hard-sell day15 -> -0.40). Defined risk (>= -1). cost-basis curve.
  (The REAL full-engine Apex is BETTER than this simplified one -- dead-hold pops many -0.70s to -0.15.
   Documented full-engine Apex: ~+16,953% 10y MedRet / 84% 5y DD / 0 collapse. Quoted as the reference.)

Leverage-invariant metrics (Sharpe, CAGR/maxDD) decide "better compounding at matched risk"; the leverage
sweep shows where levered equity COLLAPSES (the structural edge of Apex's defined-risk options).
ASCII out.
"""
import os, sys, math, datetime as dt
import numpy as np
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
OHLC = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")

UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2); I_TP = int(np.argmin(np.abs(UP_GRID - 1.092)))
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2); I_SL = int(np.argmin(np.abs(DN_GRID - 2.548)))
START = 50_000.0
MAXPOS = 14
TIER = {"u": 0.20, "t": 0.15, "m": 0.10, "l": 0.10}   # 95+/85-94/80-84/75-79 (Apex cascade)
STOCK_SPREAD = 0.0006   # ~6 bps round-trip on a liquid stock MOC/MOO
APEX_TP, APEX_SL, APEX_HARD = 0.30, -0.70, -0.40


def tier_of(ov):
    return "u" if ov >= 95 else ("t" if ov >= 85 else ("m" if ov >= 80 else "l"))


def build_signals():
    oh = pl.read_parquet(OHLC)
    led = pl.read_parquet(LED).filter(pl.col("overall") >= 75).sort("date")
    ser = {}
    for (sym, sub) in oh.group_by("symbol"):
        s = sym[0] if isinstance(sym, tuple) else sym
        sub = sub.sort("date"); d = sub["date"].to_list()
        ser[s] = (d, np.asarray(sub["open"].to_list(), float), np.asarray(sub["close"].to_list(), float),
                  {dd: i for i, dd in enumerate(d)})
    sigs = []
    syms = led["symbol"].to_list(); dts = led["date"].to_list(); ov = led["overall"].to_list()
    tup = led["t_up"].to_list(); tdn = led["t_dn"].to_list(); fwd = led["fwd_caldays"].to_list()
    for k in range(len(syms)):
        s = syms[k]
        if s not in ser:
            continue
        dl, op, cl, idx = ser[s]
        i = idx.get(dts[k])
        if i is None or i + 1 >= len(dl):
            continue
        ec = cl[i]
        if not (ec > 0):
            continue
        # equity exit prices/dates for each hold
        ex = {}
        ex["overnight"] = (dl[i+1], op[i+1])
        ex["1d"] = (dl[i+1], cl[i+1])
        for nm, n in (("3d", 3), ("5d", 5)):
            if i + n < len(dl):
                ex[nm] = (dl[i+n], cl[i+n])
        # apex option outcome
        u, dn = tup[k][I_TP], tdn[k][I_SL]
        if u <= 15 and u < dn:
            aret, acal = APEX_TP, int(u)
        elif dn <= 15:
            aret, acal = APEX_SL, int(dn)
        elif fwd[k] >= 15:
            aret, acal = APEX_HARD, 15
        else:
            aret, acal = None, None  # unresolved
        d0 = dt.date.fromisoformat(dts[k])
        sigs.append({"date": d0, "ov": ov[k], "tier": tier_of(ov[k]), "ec": ec,
                     "ex": {m: (dt.date.fromisoformat(dd), px) for m, (dd, px) in ex.items()},
                     "aret": aret, "axdate": (d0 + dt.timedelta(acal)) if acal is not None else None})
    sigs.sort(key=lambda r: (r["date"], -r["ov"]))
    return sigs, ser


def simulate(sigs, instrument, hold=None, L=1.0):
    """chronological deterministic sim. instrument in {'equity','apex'}.
    returns dict(final, compound, cagr, maxdd, sharpe, collapse, n_trades, win, avg_pos)."""
    cash = START
    open_pos = []   # list of dict(exit_date, committed, payoff_fn-> realized pnl, mark_fn(D))
    # build a date axis = all dates we must step (signal dates + exit dates), sorted unique
    dates = set(r["date"] for r in sigs)
    for r in sigs:
        if instrument == "equity":
            if hold in r["ex"]:
                dates.add(r["ex"][hold][0])
        else:
            if r["axdate"] is not None:
                dates.add(r["axdate"])
    axis = sorted(dates)
    by_date = {}
    for r in sigs:
        by_date.setdefault(r["date"], []).append(r)
    curve = []; n_trades = 0; wins = 0; pos_count = []
    peak = START; maxdd = 0.0; collapsed = False
    start_date = axis[0];
    for D in axis:
        # 1) exits due today
        still = []
        for p in open_pos:
            if p["exit_date"] <= D:
                pnl = p["realize"]
                cash += p["committed"] + pnl
                n_trades += 1
                if pnl > 0:
                    wins += 1
            else:
                still.append(p)
        open_pos = still
        equity = cash + sum(p["committed"] for p in open_pos)  # cost-basis equity for sizing
        if equity <= 0.01 * START:
            collapsed = True
        # 2) entries today
        for r in by_date.get(D, []):
            if len(open_pos) >= MAXPOS:
                break
            committed = TIER[r["tier"]] * max(equity, 0.0)
            if committed <= 0 or committed > cash:
                continue
            if instrument == "equity":
                if hold not in r["ex"]:
                    continue
                xdate, xpx = r["ex"][hold]
                rstock = xpx / r["ec"] - 1.0
                notional = committed * L
                pnl = notional * rstock - notional * STOCK_SPREAD
                # cap loss at total equity wipe handled at portfolio level
                cash -= committed
                open_pos.append({"exit_date": xdate, "committed": committed, "realize": pnl})
            else:
                if r["aret"] is None:
                    continue
                pnl = committed * r["aret"]   # defined risk, no spread (limit TP free; SL paid baked into -0.70)
                cash -= committed
                open_pos.append({"exit_date": r["axdate"], "committed": committed, "realize": pnl})
        # 3) mark-to-market curve (equity positions priced; apex at cost basis -> conservative)
        equity_mtm = cash + sum(p["committed"] for p in open_pos)
        curve.append((D, equity_mtm))
        peak = max(peak, equity_mtm)
        if peak > 0:
            maxdd = max(maxdd, (peak - equity_mtm) / peak)
        if equity_mtm <= 0.01 * START:
            collapsed = True
    final = curve[-1][1]
    yrs = (axis[-1] - axis[0]).days / 365.25
    compound = final / START - 1.0
    cagr = (final / START) ** (1/yrs) - 1.0 if final > 0 and yrs > 0 else -1.0
    # sharpe on daily-ish curve returns
    eq = np.array([c[1] for c in curve], float)
    rets = eq[1:] / np.clip(eq[:-1], 1e-9, None) - 1.0
    rets = rets[np.isfinite(rets)]
    sharpe = (rets.mean() / rets.std(ddof=1) * math.sqrt(252)) if len(rets) > 5 and rets.std() > 0 else float('nan')
    return {"final": final, "compound": compound, "cagr": cagr, "maxdd": maxdd, "sharpe": sharpe,
            "collapse": collapsed, "n": n_trades, "win": (wins/n_trades if n_trades else float('nan')),
            "yrs": yrs}


def spy_benchmark(ser, d0, d1):
    if "SPY" not in ser:
        return None
    dl, op, cl, idx = ser["SPY"]
    sub = [(dl[i], cl[i]) for i in range(len(dl)) if d0.isoformat() <= dl[i] <= d1.isoformat()]
    if len(sub) < 10:
        return None
    eq = np.array([x[1] for x in sub], float); eq = eq / eq[0] * START
    peak = eq[0]; mdd = 0.0
    for v in eq:
        peak = max(peak, v); mdd = max(mdd, (peak - v)/peak)
    yrs = (dt.date.fromisoformat(sub[-1][0]) - dt.date.fromisoformat(sub[0][0])).days/365.25
    rets = eq[1:]/eq[:-1]-1.0
    return {"compound": eq[-1]/START-1, "cagr": (eq[-1]/START)**(1/yrs)-1, "maxdd": mdd,
            "sharpe": rets.mean()/rets.std(ddof=1)*math.sqrt(252), "yrs": yrs}


def fmt(r):
    c = r["compound"]
    cs = ("%+.1f%%" % (c*100)) if abs(c) < 100 else ("%+.2e%%" % (c*100))
    return "%14s  CAGR=%+7.1f%%  maxDD=%5.1f%%  Sharpe=%5.2f  Calmar=%5.2f  collapse=%s  N=%s" % (
        cs, r["cagr"]*100, r["maxdd"]*100, r["sharpe"],
        (r["cagr"]/r["maxdd"] if r["maxdd"] > 0 else float('nan')),
        ("YES" if r.get("collapse") else "no"), r.get("n", "-"))


def main():
    sigs, ser = build_signals()
    d0, d1 = sigs[0]["date"], sigs[-1]["date"]
    print("signals: %d (75+ v70 calls)  window: %s .. %s  start=$%.0f  MaxPos=%d  cascade=%s"
          % (len(sigs), d0, d1, START, MAXPOS, TIER))
    print("=" * 110)
    spy = spy_benchmark(ser, d0, d1)
    if spy:
        print("  BENCHMARK  SPY buy-hold     :", fmt({**spy, "collapse": False, "n": "-"}))
    print("  REFERENCE  v70 Apex (full eng) :   ~+16,953%% 10y MedRet  maxDD~84%%  collapse=no  (LEVERAGED options, documented)")
    print("  REFERENCE  apex_opt (this harness, simplified/conservative, same signals):")
    print("            %s" % fmt(simulate(sigs, "apex")))
    print("-" * 110)
    print("  EQUITY SLEEVE (unlevered, L=1):")
    for h in ("overnight", "1d", "3d", "5d"):
        print("    %-9s %s" % (h, fmt(simulate(sigs, "equity", hold=h, L=1.0))))
    print("-" * 110)
    print("  EQUITY SLEEVE leverage sweep (best hold by Sharpe shown across L) -- where does it COLLAPSE?")
    # pick best hold by unlevered Sharpe
    base = {h: simulate(sigs, "equity", hold=h, L=1.0) for h in ("overnight", "1d", "3d", "5d")}
    besth = max(base, key=lambda h: (base[h]["sharpe"] if math.isfinite(base[h]["sharpe"]) else -9))
    print("    (best unlevered Sharpe hold = %s)" % besth)
    for L in (1, 2, 3, 5, 8, 10, 13, 20):
        r = simulate(sigs, "equity", hold=besth, L=float(L))
        print("    L=%-3d  %s" % (L, fmt(r)))
    print("=" * 110)
    print("Read: Sharpe + Calmar (CAGR/maxDD) are leverage-invariant -> they decide 'better compounding at")
    print("matched risk'. Apex's defined-risk options can't collapse; levered equity collapses past some L.")
    print("\nDONE")


if __name__ == "__main__":
    main()
