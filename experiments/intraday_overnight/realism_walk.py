"""Bridge A, quantified: re-walk the FUNDED apex15 barrier under two entry anchors and report the
realism haircut.

  anchor=close : enter at signal_close (what monte_carlo.py / backtest_cascade.py / the ledger assume)
  anchor=open  : enter at NEXT session open (earliest live-attainable fill -- you can't buy a close you
                 just used to generate the signal)

Funded Apex: TP at +1.092 sigma above entry (option +30%), SL at -2.548 sigma (option -70%), hard-sell
at 15 calendar days. sigma = ledger vol_pct (entry-date trailing 60d), same for both anchors.
win = favorable +1.092s reached on or before SL -2.548s, within 15 cal days (same-bar tie => loss).
Pure in-memory over the OHLC parquet + ledger 75+ cohort. No DB, no MC. ASCII out.
"""
import os, sys, math
import numpy as np
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
OHLC = os.path.join(_ROOT, ".cache", "intraday_overnight", "ohlc.parquet")
LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")

TP_SIG, SL_SIG, W = 1.092, 2.548, 15


def walk(entry, hi, lo, days, sig):
    """first-touch apex outcome. hi/lo arrays are the forward bars (post-entry), days = cal-days from
    entry date, sig = entry sigma (fraction). returns 1 win / 0 loss / None unresolved."""
    tp_lvl = entry * (1 + TP_SIG * sig)
    sl_lvl = entry * (1 - SL_SIG * sig)
    t_tp, t_sl = None, None
    for k in range(len(days)):
        if days[k] > W:
            break
        if t_tp is None and hi[k] >= tp_lvl:
            t_tp = days[k]
        if t_sl is None and lo[k] <= sl_lvl:
            t_sl = days[k]
        if t_tp is not None or t_sl is not None:
            break  # first-touch day decides (conservative: if both same day -> tie below)
    # resolve on the first-touch day (need to know if BOTH touched that day)
    if t_tp is not None and t_sl is not None:
        return 0  # same-bar tie => loss (conservative)
    if t_tp is not None:
        return 1
    if t_sl is not None:
        return 0
    # neither touched within window
    if days[-1] >= W:
        return 0  # held to expiry, no TP => loss (apex win == TP-before-SL)
    return None  # too recent


def main():
    oh = pl.read_parquet(OHLC)
    led = pl.read_parquet(LED).filter(pl.col("overall") >= 75)
    # per-symbol arrays
    ser = {}
    for (sym, sub) in oh.group_by("symbol"):
        s = sym[0] if isinstance(sym, tuple) else sym
        sub = sub.sort("date")
        d = sub["date"].to_list()
        ser[s] = (d, np.asarray(sub["open"].to_list(), float), np.asarray(sub["high"].to_list(), float),
                  np.asarray(sub["low"].to_list(), float), np.asarray(sub["close"].to_list(), float),
                  {dd: i for i, dd in enumerate(d)})
    import datetime as dt
    syms = led["symbol"].to_list(); dts = led["date"].to_list()
    ov = led["overall"].to_list(); vp = led["vol_pct"].to_list()
    rec = []  # (year, tier, win_close, win_open)
    for s, dd, o_, v in zip(syms, dts, ov, vp):
        if s not in ser or not v or v <= 0:
            continue
        dlist, op, hi, lo, cl, idx = ser[s]
        i = idx.get(dd)
        if i is None or i + 1 >= len(dlist):
            continue
        sig = v / 100.0
        d0 = dt.date.fromisoformat(dd)
        # close anchor: enter close[i], forward bars i+1.. ; cal-days from d0
        j = i + 1; days_c, hc, lc = [], [], []
        while j < len(dlist):
            cd = (dt.date.fromisoformat(dlist[j]) - d0).days
            if cd > W + 1:
                break
            if cd >= 1:
                days_c.append(cd); hc.append(hi[j]); lc.append(lo[j])
            j += 1
        # open anchor: enter open[i+1], entry date = dlist[i+1]; forward bars from i+1 (rest of that day
        # counts -- you hold from the open) ; cal-days from entry date d1
        d1 = dt.date.fromisoformat(dlist[i + 1])
        j = i + 1; days_o, ho, lo_ = [], [], []
        while j < len(dlist):
            cd = (dt.date.fromisoformat(dlist[j]) - d1).days
            if cd > W + 1:
                break
            days_o.append(cd); ho.append(hi[j]); lo_.append(lo[j])
            j += 1
        if not days_c or not days_o:
            continue
        wc = walk(cl[i], np.array(hc), np.array(lc), np.array(days_c), sig)
        wo = walk(op[i + 1], np.array(ho), np.array(lo_), np.array(days_o), sig)
        if wc is None or wo is None:
            continue
        tier = "85+" if o_ >= 85 else ("80-84" if o_ >= 80 else "75-79")
        rec.append((dd[:4], tier, wc, wo))
    arr = np.array([(r[2], r[3]) for r in rec], float)
    yrs = np.array([r[0] for r in rec]); tiers = np.array([r[1] for r in rec])
    wc = arr[:, 0]; wo = arr[:, 1]
    print("FUNDED apex15 re-walk: TP +%.3fs / SL -%.3fs / W=%dd  (N=%d resolved 75+ signals)"
          % (TP_SIG, SL_SIG, W, len(rec)))
    print("  anchor=close (BACKTEST assumption)  vs  anchor=open (live-attainable next-open fill)\n")
    print("  cohort     win@close  win@open   delta(pp)   N")
    bc, bo = wc.mean(), wo.mean()
    print("  ALL 75+    %7.2f%%  %7.2f%%   %+7.2f   %5d" % (bc*100, bo*100, (bo-bc)*100, len(rec)))
    for tr in ("75-79", "80-84", "85+"):
        m = tiers == tr
        if m.sum():
            print("  %-8s   %7.2f%%  %7.2f%%   %+7.2f   %5d" % (tr, wc[m].mean()*100, wo[m].mean()*100, (wo[m].mean()-wc[m].mean())*100, m.sum()))
    print("\n  by year:   win@close  win@open   delta(pp)   N")
    for yr in sorted(set(yrs.tolist())):
        m = yrs == yr
        print("    %s    %7.2f%%  %7.2f%%   %+7.2f   %5d" % (yr, wc[m].mean()*100, wo[m].mean()*100, (wo[m].mean()-wc[m].mean())*100, m.sum()))
    # paired McNemar-ish: how often do they disagree?
    flip_lose = int(((wc == 1) & (wo == 0)).sum()); flip_win = int(((wc == 0) & (wo == 1)).sum())
    print("\n  paired flips: close-WIN->open-LOSE = %d   close-LOSE->open-WIN = %d   (net %+d trades worse under live entry)"
          % (flip_lose, flip_win, flip_lose - flip_win))
    print("  => realism haircut on the funded win-rate = %+.2f pp (live next-open entry vs backtest signal-close)" % ((bo-bc)*100))
    print("\nDONE")


if __name__ == "__main__":
    main()
