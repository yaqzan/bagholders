"""
Short-dated protective-PUT overlay against the call book  (Stage-3 hedge probe).

User hypothesis (2026-06-22): buy a short-dated put (<=3 DTE, probe 1/2/3/5) AGAINST
each call we open, as a tail/whiplash hedge for the "bad 1-day" call outcomes. Test
for both Apex (sprint, flat 25%x4) and Core (held, cascade 0.20/0.15/0.08/0.03).

This is a PORTFOLIO OVERLAY (the reserved-cash protective-put door, the one explicitly
-untested put framing), NOT the directional put signal (already null'd).

Decisive cheap test (G24 gross-theta check, no MC):
  - Co-buy an ATM protective put with matched underlying notional at each call entry.
  - premium_pct = 1.82*sqrt(DTE/30)*sigma_daily  (the engine's premium model).
  - Walk days 1..DTE with HONEST theta (option_pricing.option_pnl_pct).
  - Give the hedge its BEST CASE: best-intraday exit = perfect timing of the dip
    (sell the put at the most favorable intraday LOW over its life). If the hedge is
    net-negative even with perfect timing, it's dead before spread.
  - Also report the realistic insurance exit (hold to expiry / exercise if ITM).

Outputs per (window, DTE): mean/median best & expiry put pnl (fraction of put premium),
% of positions the hedge "worked", the breakeven adverse move vs the actual overnight
gap, the alloc-weighted BOOK drag for Apex + Core sizing, and the call-loser-subset
payoff (does the put pay off exactly on the bad call days?).

Run:  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/short_put_hedge/overlay_ledger.py
"""
import sys, math, json, os
from pathlib import Path
import numpy as np
import polars as pl

# Short-DTE premium is OVERSTATED by the 1.82*sqrt(DTE/30) formula (~29% per the
# option_price_assessment note). A cheaper real put bleeds less -> robustness knob.
PREM_HAIRCUT = float(os.environ.get("PUT_PREM_HAIRCUT", "1.0"))

ROOT = Path(__file__).resolve().parents[2]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
from option_pricing import option_pnl_pct  # honest delta+theta model

TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OHLC = ROOT / ".cache" / "intraday_overnight" / "ohlc.parquet"
OUT = ROOT / "experiments" / "short_put_hedge"
OUT.mkdir(parents=True, exist_ok=True)

PREMIUM_MULT_30 = 1.82          # ATM 30DTE premium = 1.82 * sigma_daily (engine)
DTES = [1, 2, 3, 5]
DELTA = 0.5

# tier -> alloc fraction of book, per profile (post 2026-06-17 restructure)
CORE_ALLOC = {"ultra": 0.20, "top": 0.15, "mid": 0.08, "low": 0.03, "overflow": 0.0}
APEX_ALLOC = {"ultra": 0.25, "top": 0.25, "mid": 0.25, "low": 0.25, "overflow": 0.0}  # flat 25%, top-4


def premium_mult(dte):
    return PREMIUM_MULT_30 * math.sqrt(dte / 30.0)


def load_call_signals():
    """Unique 75+ call signals per window from the MC tape (the actually-traded
    population), with mean realized call option_pnl across seeds."""
    files = sorted(TAPE_DIR.glob("tape_*.parquet"))
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        df = pl.read_parquet(f)
        if "side" in df.columns:
            df = df.filter(pl.col("side") == "call")
        # keep entry_date as 'YYYY-MM-DD' string (matches OHLC date string keys)
        df = df.with_columns(pl.col("entry_date").cast(pl.Utf8))
        g = df.group_by(["sym_id", "entry_date"]).agg(
            pl.col("tier").first().alias("tier"),
            pl.col("score").first().alias("score"),
            pl.col("option_pnl").mean().alias("call_pnl_mean"),
            pl.len().alias("n_fills"),
        ).with_columns(pl.lit(label).alias("window"))
        frames.append(g)
        print(f"   {f.name:24s} unique call signals={g.height}")
    return pl.concat(frames, how="diagonal_relaxed")


def build_symbol_arrays():
    o = pl.read_parquet(OHLC).sort(["symbol", "date"])
    o = o.with_columns(pl.col("date").cast(pl.Utf8))  # string keys, match tape
    arrs = {}
    for sym, sub in o.group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        dates = sub["date"].to_list()
        close = sub["close"].to_numpy().astype(float)
        high = sub["high"].to_numpy().astype(float)
        low = sub["low"].to_numpy().astype(float)
        opn = sub["open"].to_numpy().astype(float)
        # 60-day realized daily vol (pct returns), shifted so it's known at entry
        ret = np.diff(close) / close[:-1]
        sig = np.full(len(close), np.nan)
        for i in range(60, len(close)):
            sig[i] = np.std(ret[i - 60:i])  # uses returns up to and incl day i-1 -> i
        idx = {d: k for k, d in enumerate(dates)}
        arrs[sym] = (idx, close, high, low, opn, sig)
    return arrs


def walk_put(U0, sigma, fwd_low, fwd_close, dte):
    """Return (best_intraday, best_close, expiry, min_low_frac) for an ATM protective
    put of `dte` days bought at U0. Three exit policies:
      best_intraday -> sell at the most favorable intraday LOW (FANTASY: perfect dip
                       timing, unachievable; the MFE-gap/trailing-stop trap)
      best_close    -> sell at the close of the best day (ACHIEVABLE end-of-day decision)
      expiry        -> hold to expiry / pure insurance
    pnl_pct = fraction of the put premium (>= -1)."""
    prem = premium_mult(dte) * sigma * PREM_HAIRCUT   # fraction of underlying
    if prem <= 0:
        return None
    best = -1.0; best_close = -1.0; min_low_frac = 0.0
    closes = []  # causal daily-close put value series
    for d in range(1, dte + 1):
        if d - 1 >= len(fwd_low):
            break
        low = fwd_low[d - 1]
        min_low_frac = min(min_low_frac, low / U0 - 1.0)
        # generous: dip happens mid-day -> bars_held = d - 0.5
        v = option_pnl_pct("put", low, U0, max(0.0, d - 0.5), prem, total_dte=dte, delta=DELTA)
        if v > best:
            best = v
        if d - 1 < len(fwd_close):
            vc = option_pnl_pct("put", fwd_close[d - 1], U0, d, prem, total_dte=dte, delta=DELTA)
            closes.append(vc)
            if vc > best_close:
                best_close = vc
    last = min(dte, len(fwd_close))
    expiry = option_pnl_pct("put", fwd_close[last - 1], U0, dte, prem, total_dte=dte, delta=DELTA) if last >= 1 else -1.0
    # CAUSAL target rules (the steelman achievable exit): each day at close, if the put
    # is up >= T, sell; else hold; settle at expiry. T in {0.25,0.50,1.00}.
    causal = {}
    for T in (0.25, 0.50, 1.00):
        r = expiry
        for vc in closes:
            if vc >= T:
                r = vc
                break
        causal[T] = r
    return best, best_close, expiry, min_low_frac, causal


def main():
    print("== loading call signals ==")
    sig = load_call_signals()
    print(f"   total unique (window,sym,date) call signals: {sig.height}")
    print("== building per-symbol OHLC arrays ==")
    arrs = build_symbol_arrays()

    # walk every signal x DTE
    rows = []
    miss = 0
    for r in sig.iter_rows(named=True):
        sym = r["sym_id"]; d0 = r["entry_date"]
        a = arrs.get(sym)
        if a is None:
            miss += 1; continue
        idx, close, high, low, opn, sigv = a
        k = idx.get(d0)
        if k is None or k + 1 >= len(close):
            miss += 1; continue
        U0 = close[k]
        sigma = sigv[k]
        if not (sigma and sigma > 0) or U0 <= 0:
            miss += 1; continue
        # overnight gap (entry close -> next open) for the gross-theta context
        gap = opn[k + 1] / U0 - 1.0 if k + 1 < len(opn) else None
        fwd_low = low[k + 1:k + 1 + 5]
        fwd_close = close[k + 1:k + 1 + 5]
        base = {"window": r["window"], "sym": sym, "date": str(d0), "tier": r["tier"],
                "score": r["score"], "call_pnl_mean": r["call_pnl_mean"],
                "sigma": sigma, "gap": gap}
        for dte in DTES:
            w = walk_put(U0, sigma, fwd_low, fwd_close, dte)
            if w is None:
                continue
            best, best_close, expiry, mlf, causal = w
            base[f"best_{dte}"] = best
            base[f"bclose_{dte}"] = best_close
            base[f"exp_{dte}"] = expiry
            base[f"mlf_{dte}"] = mlf  # min low frac over the dte window
            for T, rv in causal.items():
                base[f"causal_{dte}_{int(T*100)}"] = rv  # fixed-T causal exit
        rows.append(base)
    L = pl.DataFrame(rows)
    print(f"   ledger rows={L.height}  (missed {miss} signals lacking price/vol)")
    L.write_parquet(OUT / "overlay_ledger.parquet")

    # ---- analytical gross-theta floor (G24) ----
    print("\n=== GROSS-THETA FLOOR (analytical; per-night, ATM put) ===")
    print(f"{'DTE':>4s} {'prem/30DTE':>11s} {'theta_n1':>9s} {'theta_n2':>9s} {'BE_move_n1':>11s}")
    for dte in DTES:
        prem_frac_of_call = math.sqrt(dte / 30.0)
        th1 = math.sqrt(max(0, dte - 1) / dte) - 1.0
        th2 = math.sqrt(max(0, dte - 2) / dte) - 1.0 if dte >= 2 else float("nan")
        # breakeven down-move (in sigma) to offset night-1 theta:
        # need delta*|move|/prem >= |th1|; prem_pct = premium_mult(dte)*sigma
        # 0.5*|m*sigma|/(premium_mult*sigma) >= |th1| -> |m| >= |th1|*premium_mult/0.5
        be_sigma = abs(th1) * premium_mult(dte) / DELTA
        print(f"{dte:>4d} {prem_frac_of_call:>11.3f} {th1:>9.3f} {th2:>9.3f} {be_sigma:>9.3f}sd")

    # actual overnight gap on the call population
    gaps = L["gap"].drop_nulls()
    print(f"\novernight gap (entry close->next open) on 75+ call signals: "
          f"mean={gaps.mean()*1e4:+.1f}bps  median={gaps.median()*1e4:+.1f}bps  "
          f"P(gap<0)={(gaps<0).mean():.3f}  N={gaps.len()}")

    # ---- per-DTE per-window hedge economics ----
    report = {"n_signals": L.height, "windows": {}, "gross_theta": {}}
    wins = sorted(L["window"].unique().to_list())
    for dte in DTES:
        bcol, ccol, ecol = f"best_{dte}", f"bclose_{dte}", f"exp_{dte}"
        if ccol not in L.columns:
            continue
        # REALISTIC verdict uses 'causal' (achievable fixed-T target exit) and 'insure'
        # (hold-to-expiry). 'fantasy'(intraday low) and 'EODclose'(best daily close over
        # window) are BOTH look-ahead upper bounds (the MFE-gap/trailing-stop trap) -- shown
        # for context only. net% = hedge NET $ contribution as % of deployed call premium
        # (hedge cost frac of call premium = sqrt(dte/30)); NEGATIVE = the hedge bleeds.
        causal_cols = [f"causal_{dte}_{t}" for t in (25, 50, 100)]
        hc = math.sqrt(dte / 30.0) * PREM_HAIRCUT   # put cost as frac of call premium
        print(f"\n=== PROTECTIVE PUT  DTE={dte}  (returns = fraction of PUT premium) ===")
        print(f"  look-ahead upper bounds (NOT achievable): fantasy=intraday-low, EOD=best-daily-close")
        print(f"{'window':12s} {'N':>6s} {'fantasy':>8s} {'EOD':>7s} | "
              f"{'causal*':>8s} {'insure':>8s} | {'core_net%':>9s} {'apex_net%':>9s} {'loser_caus':>10s}")
        for w in wins:
            sub = L.filter(pl.col("window") == w)
            if sub.height == 0 or bcol not in sub.columns:
                continue
            b = sub[bcol].drop_nulls(); c = sub[ccol].drop_nulls(); e = sub[ecol].drop_nulls()
            # best achievable fixed-T causal rule (choose the single best T at aggregate)
            best_caus_mean = None; best_caus_col = causal_cols[0]
            for cc in causal_cols:
                m = sub[cc].drop_nulls().mean()
                if best_caus_mean is None or m > best_caus_mean:
                    best_caus_mean = m; best_caus_col = cc
            sub2 = sub.with_columns([
                pl.col("tier").replace_strict(CORE_ALLOC, default=0.0).alias("core_alloc"),
                pl.col("tier").replace_strict(APEX_ALLOC, default=0.0).alias("apex_alloc"),
            ])
            core_net = (sub2["core_alloc"] * sub2[best_caus_col].fill_null(-1.0) * hc).sum() / (sub2["core_alloc"].sum() or 1.0) * 100
            apex_net = (sub2["apex_alloc"] * sub2[best_caus_col].fill_null(-1.0) * hc).sum() / (sub2["apex_alloc"].sum() or 1.0) * 100
            losers = sub.filter(pl.col("call_pnl_mean") < -0.30)
            lcaus = losers[best_caus_col].drop_nulls().mean() if losers.height else None
            print(f"{w:12s} {b.len():>6d} {b.mean():>8.3f} {c.mean():>7.3f} | "
                  f"{best_caus_mean:>8.3f} {e.mean():>8.3f} | {core_net:>9.2f} {apex_net:>9.2f} "
                  f"{(lcaus if lcaus is not None else float('nan')):>10.3f}")
            report["windows"].setdefault(w, {})[f"dte_{dte}"] = {
                "n": b.len(), "fantasy_intraday_mean": round(b.mean(), 4),
                "eod_close_lookahead_mean": round(c.mean(), 4),
                "causal_best_mean": round(best_caus_mean, 4), "causal_best_T": best_caus_col,
                "insurance_expiry_mean": round(e.mean(), 4),
                "core_book_net_pct": round(core_net, 3), "apex_book_net_pct": round(apex_net, 3),
                "loser_subset_causal_mean": round(lcaus, 4) if lcaus is not None else None,
            }
    (OUT / "overlay_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'overlay_report.json'} and overlay_ledger.parquet ==")


if __name__ == "__main__":
    main()
