"""Intraday-vs-overnight study for the Apex options strategy.

Three questions, three bridges:
 (1) PREMISE: on OUR universe (+SPY/QQQ), does intraday (open->close) beat overnight
     (prevclose->open), and how regime-dependent is it (pre/post-2020)? Confirms the user's stat.
 (2) BRIDGE A (execution layer -- the real one): our backtest enters at the SIGNAL-DATE CLOSE,
     but live you can't buy a close you just used to decide -- earliest real fill is the NEXT OPEN.
     The gap (next_open / signal_close - 1) is (a) a realism check on the backtest and (b) the only
     genuine "buy at open vs close" lever for our strategy. Measure it on the CALL cohort, by year/tier.
 (3) BRIDGE C (directional feature -- expected NULL per rel_strength): a stock's trailing
     intraday-vs-overnight tilt as a scoring discriminator -> cohort-test on the funded apex15 barrier.

Reads: .cache/intraday_overnight/ohlc.parquet (open!) + .cache/rel_strength/rs_ledger.parquet (signals+outcomes).
No DB, no MC. ASCII-only output (cp1252-safe).
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

# apex15 barrier indices on the ledger grids
UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)
I_TP = int(np.argmin(np.abs(UP_GRID - 1.092)))   # +1.092 sigma  (option +30%)
I_SL = int(np.argmin(np.abs(DN_GRID - 2.548)))   # -2.548 sigma  (option -70%)


def welch_t(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if len(a) < 5 or len(b) < 5:
        return float('nan'), float('nan'), len(a), len(b)
    m1, m2 = a.mean(), b.mean()
    s1, s2 = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(s1/len(a) + s2/len(b))
    t = (m1 - m2)/se if se > 0 else float('nan')
    return (m1 - m2), t, len(a), len(b)


def t_vs_zero(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) < 5:
        return float('nan'), float('nan'), len(x)
    m = x.mean(); se = x.std(ddof=1)/math.sqrt(len(x))
    return m, (m/se if se > 0 else float('nan')), len(x)


def load_series(ohlc):
    """symbol -> (dates list, open, close arrays, idx map). intraday/overnight per-bar log rets."""
    out = {}
    for (sym, sub) in ohlc.group_by("symbol"):
        s = sym[0] if isinstance(sym, tuple) else sym
        sub = sub.sort("date")
        d = sub["date"].to_list()
        o = np.asarray(sub["open"].to_list(), float)
        c = np.asarray(sub["close"].to_list(), float)
        with np.errstate(divide='ignore', invalid='ignore'):
            intra = np.log(c / o)                       # open->close
            over = np.full(len(c), np.nan)
            over[1:] = np.log(o[1:] / c[:-1])           # prevclose->open
        intra[~np.isfinite(intra)] = np.nan
        over[~np.isfinite(over)] = np.nan
        out[s] = (d, o, c, intra, over, {dd: i for i, dd in enumerate(d)})
    return out


def part1_premise(series):
    print("\n" + "=" * 78)
    print("PART 1 -- PREMISE: intraday (open->close) vs overnight (prevclose->open)")
    print("  mean per-bar log-return in basis points, by year. (+) = that window gained.")
    print("=" * 78)

    def report(label, syms):
        print("\n  [%s]  year:  intraday(bps)  overnight(bps)   intra-over   N_bars" % label)
        rows = {}
        for s in syms:
            if s not in series:
                continue
            d, o, c, intra, over, _ = series[s]
            for k, di in enumerate(d):
                yr = di[:4]
                rows.setdefault(yr, [[], []])
                if np.isfinite(intra[k]):
                    rows[yr][0].append(intra[k])
                if np.isfinite(over[k]):
                    rows[yr][1].append(over[k])
        alli, allo = [], []
        for yr in sorted(rows):
            iv = np.asarray(rows[yr][0]); ov = np.asarray(rows[yr][1])
            alli.append(iv); allo.append(ov)
            mi = iv.mean()*1e4 if len(iv) else float('nan')
            mo = ov.mean()*1e4 if len(ov) else float('nan')
            print("        %s     %+8.2f      %+8.2f     %+8.2f   %7d" % (yr, mi, mo, mi-mo, len(iv)))
        I = np.concatenate(alli); O = np.concatenate(allo)
        print("        ALL     %+8.2f      %+8.2f     %+8.2f   %7d" % (I.mean()*1e4, O.mean()*1e4, (I.mean()-O.mean())*1e4, len(I)))
        # pre/post 2020 split
        for lab, yrs in [("pre-2020", [y for y in rows if y < "2020"]), ("2020+", [y for y in rows if y >= "2020"])]:
            iv = np.concatenate([np.asarray(rows[y][0]) for y in yrs]) if yrs else np.array([])
            ov = np.concatenate([np.asarray(rows[y][1]) for y in yrs]) if yrs else np.array([])
            if len(iv) and len(ov):
                print("        %-7s %+8.2f      %+8.2f     %+8.2f   %7d" % (lab, iv.mean()*1e4, ov.mean()*1e4, (iv.mean()-ov.mean())*1e4, len(iv)))

    for idx in ("SPY", "QQQ", "IWM", "DIA"):
        if idx in series:
            report(idx, [idx])
    # universe = all ledger symbols (pooled equal-weight per bar)
    report("UNIVERSE(all syms pooled)", list(series.keys()))


def part2_bridge_a(series, led):
    print("\n" + "=" * 78)
    print("PART 2 -- BRIDGE A: entry-timing realism on CALL signals (overall>=75)")
    print("  gap = next_open / signal_close - 1   (realism delta: live-attainable next-open entry")
    print("  vs the backtest's signal-close entry). (+) gap => next-open is HIGHER => backtest")
    print("  entry is CHEAPER than attainable => backtest OPTIMISTIC for calls (real edge thinner).")
    print("  (-) gap => next-open cheaper => a real 'buy at open' edge for calls.")
    print("=" * 78)
    sub = led.filter(pl.col("overall") >= 75)
    syms = sub["symbol"].to_list(); dts = sub["date"].to_list()
    ov = sub["overall"].to_list(); vp = sub["vol_pct"].to_list()
    gaps = []; gaps_sig = []; yrs = []; tiers = []
    intraday_next = []  # next-day open->close (what you'd capture if you bought the open)
    for s, d, o_, v in zip(syms, dts, ov, vp):
        if s not in series:
            continue
        dd, op, cl, intra, over, idx = series[s]
        i = idx.get(d)
        if i is None or i + 1 >= len(dd):
            continue
        sigclose = cl[i]; nopen = op[i+1]
        if not (sigclose > 0 and nopen > 0):
            continue
        g = nopen / sigclose - 1.0
        gaps.append(g)
        gaps_sig.append(g / (v/100.0) if v and v > 0 else np.nan)
        yrs.append(d[:4])
        tiers.append("85+" if o_ >= 85 else ("80-84" if o_ >= 80 else "75-79"))
        intraday_next.append(intra[i+1])
    gaps = np.asarray(gaps); gaps_sig = np.asarray(gaps_sig)
    yrs = np.asarray(yrs); tiers = np.asarray(tiers); intraday_next = np.asarray(intraday_next)
    m, t, n = t_vs_zero(gaps)
    print("\n  OVERALL CALL>=75: mean gap=%+.1f bps  t_vs0=%+.2f  N=%d" % (m*1e4, t, n))
    print("    mean gap in sigma units=%+.4f  (median=%+.4f)" % (np.nanmean(gaps_sig), np.nanmedian(gaps_sig)))
    mi, ti, ni = t_vs_zero(intraday_next)
    print("    next-day intraday (open->close, what you'd then ride) = %+.1f bps  t=%+.2f" % (mi*1e4, ti))
    print("\n  by year:   gap(bps)   t_vs0      N      | next-intraday(bps)")
    for yr in sorted(set(yrs.tolist())):
        gm = gaps[yrs == yr]; im = intraday_next[yrs == yr]
        m, t, n = t_vs_zero(gm); mi2, ti2, _ = t_vs_zero(im)
        print("       %s   %+7.1f   %+6.2f  %6d      | %+7.1f  (t=%+.2f)" % (yr, m*1e4, t, n, mi2*1e4, ti2))
    print("\n  by tier:   gap(bps)   t_vs0      N")
    for tr in ("75-79", "80-84", "85+"):
        gm = gaps[tiers == tr]; m, t, n = t_vs_zero(gm)
        print("       %-5s   %+7.1f   %+6.2f  %6d" % (tr, m*1e4, t, n))


def _apex_win_col(led):
    """reconstruct apex15 win (1/0) + resolved mask from t_up/t_dn lists."""
    tup = led["t_up"].to_list(); tdn = led["t_dn"].to_list(); fwd = led["fwd_caldays"].to_list()
    win = np.zeros(len(tup), dtype=float); res = np.zeros(len(tup), dtype=bool)
    for k in range(len(tup)):
        u = tup[k][I_TP]; dn = tdn[k][I_SL]
        tp_hit = (u <= 15); sl_hit = (dn <= 15)
        if tp_hit and (u < dn):
            win[k] = 1.0; res[k] = True
        elif sl_hit or fwd[k] >= 15:
            win[k] = 0.0; res[k] = True
        else:
            res[k] = False  # too recent / unresolved
    return win, res


def part3_bridge_c(series, led):
    print("\n" + "=" * 78)
    print("PART 3 -- BRIDGE C: trailing intraday/overnight TILT as a directional feature")
    print("  feature (strictly-prior trailing 20 bars, ending at signal date d):")
    print("    io_tilt = mean(intraday_ret_20) - mean(overnight_ret_20)")
    print("  cohort test on the FUNDED apex15 barrier (TP+1.092sigma / SL-2.548sigma / W=15).")
    print("  Expectation per rel_strength FINDINGS: another price partition -> ~flat ~47-50%.")
    print("=" * 78)
    sub = led.filter(pl.col("overall") >= 75)
    syms = sub["symbol"].to_list(); dts = sub["date"].to_list()
    WIN = 20
    io_tilt = []; intra20 = []; over20 = []
    for s, d in zip(syms, dts):
        if s not in series:
            io_tilt.append(np.nan); intra20.append(np.nan); over20.append(np.nan); continue
        dd, op, cl, intra, over, idx = series[s]
        i = idx.get(d)
        if i is None or i < WIN:
            io_tilt.append(np.nan); intra20.append(np.nan); over20.append(np.nan); continue
        wi = intra[i-WIN+1:i+1]; wo = over[i-WIN+1:i+1]
        wi = wi[np.isfinite(wi)]; wo = wo[np.isfinite(wo)]
        if len(wi) < 10 or len(wo) < 10:
            io_tilt.append(np.nan); intra20.append(np.nan); over20.append(np.nan); continue
        mi = wi.mean(); mo = wo.mean()
        intra20.append(mi); over20.append(mo); io_tilt.append(mi - mo)
    sub = sub.with_columns([
        pl.Series("io_tilt", io_tilt), pl.Series("intra20", intra20), pl.Series("over20", over20)])
    win, res = _apex_win_col(sub)
    sub = sub.with_columns([pl.Series("apex_win", win), pl.Series("resolved", res)])
    sub = sub.filter(pl.col("resolved") & pl.col("io_tilt").is_finite())
    base = sub["apex_win"].mean()
    print("\n  resolved CALL>=75 N=%d  baseline apex15 win=%.1f%%" % (sub.height, base*100))

    for feat in ("io_tilt", "intra20", "over20"):
        vals = np.asarray(sub[feat].to_list(), float); w = np.asarray(sub["apex_win"].to_list(), float)
        qs = np.nanquantile(vals, [0.2, 0.4, 0.6, 0.8])
        edges = [np.nanmin(vals)] + list(qs) + [np.nanmax(vals)]
        print("\n  %s -> apex15 win%% by quintile:" % feat)
        bw = []
        for bi in range(5):
            lo, hi = edges[bi], edges[bi+1]
            m = (vals >= lo) & (vals <= hi if bi == 4 else vals < hi)
            wr = w[m]; bw.append(wr)
            print("      Q%d [%+.5f,%+.5f] N=%5d  win=%.1f%%" % (bi+1, lo, hi, m.sum(), 100*wr.mean() if len(wr) else float('nan')))
        # top vs bottom quintile, two-proportion z
        a, b = bw[4], bw[0]
        pa, pb = a.mean(), b.mean(); na, nb = len(a), len(b)
        pp = (a.sum()+b.sum())/(na+nb); se = math.sqrt(pp*(1-pp)*(1/na+1/nb)) if pp not in (0,1) else float('nan')
        z = (pa-pb)/se if se and se > 0 else float('nan')
        print("      Q5-Q1 win%% delta=%+.1fpp  two-prop z=%+.2f%s" % ((pa-pb)*100, z, "  <-- |z|>=3" if abs(z) >= 3 else ""))


def main():
    if not os.path.exists(OHLC):
        print("MISSING %s -- run build_prices.py first (queued task)" % OHLC); return
    ohlc = pl.read_parquet(OHLC)
    led = pl.read_parquet(LED)
    print("ohlc N=%d symbols=%d  |  ledger N=%d range=%s..%s"
          % (ohlc.height, ohlc["symbol"].n_unique(), led.height, led["date"].min(), led["date"].max()))
    print("apex15 grid idx: UP[%d]=%.2f sigma  DN[%d]=%.2f sigma" % (I_TP, UP_GRID[I_TP], I_SL, DN_GRID[I_SL]))
    series = load_series(ohlc)
    part1_premise(series)
    part2_bridge_a(series, led)
    part3_bridge_c(series, led)
    print("\nDONE")


if __name__ == "__main__":
    main()
