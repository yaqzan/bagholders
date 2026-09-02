"""Regime-conditioned call-edge decomposition: is the 75+ call edge DRIFT-BETA or
regime-conditional SELECTION, and is there a discriminable WINNER pocket in chop/down?

User thesis (2026-06-22): the market drifts up, so calls have a tailwind; the real
edge should live in CHOPPY / DRAWDOWN windows (integrate with the market-trend/weekly
indicator). This measures it directly, beta-stripped, the way nobody has yet:

  For each 75+ call signal (proxy_ledger, 2016-2026), classify the MARKET-TREND regime
  by SPY (trailing 20d return + drawdown-from-60d-high; the "market trend" axis), then:
    (1) RAW stock forward 15d/30d return  [includes the drift beta]
    (2) EXCESS = stock_fwd - SPY_fwd       [beta-STRIPPED selection skill]
    (3) apex option EV (TP +1.092sigma / SL -2.548sigma / 30d; +0.30/-0.70/-0.40)
  by regime. If the EXCESS is flat across regimes -> the edge is beta (no hidden chop
  alpha). If EXCESS concentrates in chop/down -> the thesis is real (a lever).
  Then a DISCRIMINATOR HUNT within the worst (chop/down) regime: does RSI/trend/rs/w_adj
  separate the chop-WINNERS from chop-losers (cohort-z >= +3 = a regime-conditioned
  selection lever)?

Read-only, no MySQL, no MC. SPY/stock fwd returns from the cached OHLC parquet.
Run: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/regime_call_alpha/probe.py
"""
import sys, math, json
from pathlib import Path
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

PROXY = ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet"
OHLC = ROOT / ".cache" / "intraday_overnight" / "ohlc.parquet"
OUT = ROOT / "experiments" / "regime_call_alpha"
OUT.mkdir(parents=True, exist_ok=True)

APEX_TP_SIG = 1.092   # 30dte_apex up barrier (TP +30%)
APEX_SL_SIG = 2.548   # 30dte_apex down barrier (SL -70%)
HOLD_CAL = 30
EV_WIN, EV_STOP, EV_EXP = 0.30, -0.70, -0.40


def sym_arrays(o):
    arrs = {}
    for sym, sub in o.group_by("symbol", maintain_order=True):
        sym = sym[0] if isinstance(sym, tuple) else sym
        dates = sub["date"].to_list()
        close = sub["close"].to_numpy().astype(float)
        high = sub["high"].to_numpy().astype(float)
        low = sub["low"].to_numpy().astype(float)
        idx = {d: k for k, d in enumerate(dates)}
        arrs[sym] = (idx, dates, close, high, low)
    return arrs


def fwd_ret(arr, k, n):
    """simple close-to-close forward return over n trading bars."""
    idx, dates, close, high, low = arr
    if k + n < len(close) and close[k] > 0:
        return close[k + n] / close[k] - 1.0
    return None


def apex_outcome(arr, k, sigma):
    """30dte_apex barrier walk from bar k over ~HOLD_CAL trading bars. returns +1 win /
    -1 stop / 0 expire (None if insufficient fwd)."""
    idx, dates, close, high, low = arr
    if not (sigma and sigma > 0) or k + 1 >= len(close):
        return None
    U0 = close[k]
    tp = U0 * (1 + APEX_TP_SIG * sigma)
    sl = U0 * (1 - APEX_SL_SIG * sigma)
    end = min(k + HOLD_CAL, len(close) - 1)
    if end <= k:
        return None
    for j in range(k + 1, end + 1):
        hit_tp = high[j] >= tp
        hit_sl = low[j] <= sl
        if hit_tp and hit_sl:
            return 1  # ambiguous same-bar -> count win (generous; symmetric noise)
        if hit_tp:
            return 1
        if hit_sl:
            return -1
    return 0


def z2(p1, n1, p0, n0):
    """two-proportion z (p1 cohort vs p0 baseline)."""
    if n1 < 1 or n0 < 1:
        return 0.0
    p = (p1 * n1 + p0 * n0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return (p1 - p0) / se if se > 0 else 0.0


def main():
    print("== load ==")
    px = pl.read_parquet(PROXY).with_columns(pl.col("date").cast(pl.Utf8))
    px = px.filter(pl.col("overall") >= 75)
    print(f"   75+ call signals in proxy_ledger: {px.height}  ({px['date'].min()}..{px['date'].max()})")
    o = pl.read_parquet(OHLC).with_columns(pl.col("date").cast(pl.Utf8)).sort(["symbol", "date"])
    arrs = sym_arrays(o)
    spy = arrs.get("SPY")
    if spy is None:
        print("!! no SPY in ohlc"); sys.exit(2)
    spy_idx, spy_dates, spy_close, _, _ = spy
    # SPY market-trend context per date: trailing 20d return + drawdown from 60d high
    spy_ctx = {}
    for k, d in enumerate(spy_dates):
        r20 = spy_close[k] / spy_close[k - 20] - 1.0 if k >= 20 else None
        dd = spy_close[k] / max(spy_close[max(0, k - 60):k + 1]) - 1.0 if k >= 1 else None
        spy_ctx[d] = (r20, dd)

    rows = []
    miss = 0
    for r in px.iter_rows(named=True):
        sym, d = r["symbol"], r["date"]
        a = arrs.get(sym)
        if a is None or d not in a[0]:
            miss += 1; continue
        k = a[0][d]
        sc = spy_ctx.get(d)
        if sc is None or sc[0] is None:
            miss += 1; continue
        spy_r20, spy_dd = sc
        sk = spy_idx.get(d)
        sigma = (r.get("vol_pct") or 0) / 100.0
        st15 = fwd_ret(a, k, 15); st30 = fwd_ret(a, k, 30)
        sp15 = fwd_ret(spy, sk, 15) if sk is not None else None
        sp30 = fwd_ret(spy, sk, 30) if sk is not None else None
        apex = apex_outcome(a, k, sigma)
        rows.append({
            "symbol": sym, "date": d, "overall": r["overall"],
            "spy_r20": spy_r20, "spy_dd": spy_dd, "sigma": sigma,
            "st15": st15, "st30": st30, "sp15": sp15, "sp30": sp30,
            "exc15": (st15 - sp15) if (st15 is not None and sp15 is not None) else None,
            "exc30": (st30 - sp30) if (st30 is not None and sp30 is not None) else None,
            "apex": apex,
            "c_trend": r.get("c_trend"), "c_rsi": r.get("c_rsi"), "c_macd": r.get("c_macd"),
            "c_bb": r.get("c_bb"), "c_stoch": r.get("c_stoch"), "rs20": r.get("rs20"),
            "w_adj": r.get("w_adj"),
        })
    L = pl.DataFrame(rows)
    L.write_parquet(OUT / "regime_call_ledger.parquet")
    print(f"   ledger={L.height} (miss {miss})")

    # ---- market-trend regime bins (SPY) ----
    L = L.with_columns([
        pl.when(pl.col("spy_r20") >= 0.03).then(pl.lit("UP_strong"))
          .when(pl.col("spy_r20") >= 0.005).then(pl.lit("UP_mild"))
          .when(pl.col("spy_r20") >= -0.005).then(pl.lit("FLAT_chop"))
          .when(pl.col("spy_r20") >= -0.03).then(pl.lit("DOWN_mild"))
          .otherwise(pl.lit("DOWN_hard")).alias("mkt"),
        pl.when(pl.col("spy_dd") >= -0.02).then(pl.lit("near_high"))
          .when(pl.col("spy_dd") >= -0.06).then(pl.lit("dd_2_6"))
          .otherwise(pl.lit("dd_6p")).alias("mkt_dd"),
    ])

    print("\n=== (1)+(2)+(3) CALL OUTCOME by MARKET-TREND regime (SPY trailing 20d) ===")
    print("  raw st15/st30 = stock fwd return (incl drift beta); exc = stock - SPY (beta-STRIPPED);")
    print("  apexEV = funded option EV (+0.30 win/-0.70 stop/-0.40 expire)")
    print(f"{'regime':12s} {'N':>7s} {'st15%':>7s} {'sp15%':>7s} {'exc15%':>7s} {'exc30%':>7s} "
          f"{'apexWR':>7s} {'apexEV%':>8s}")
    order = ["UP_strong", "UP_mild", "FLAT_chop", "DOWN_mild", "DOWN_hard"]
    report = {"n": L.height, "by_mkt": {}, "by_dd": {}}
    base_apex = L.filter(pl.col("apex").is_not_null())
    base_wr = (base_apex["apex"] == 1).mean()
    for g in order:
        s = L.filter(pl.col("mkt") == g)
        if s.height < 50:
            continue
        ap = s.filter(pl.col("apex").is_not_null())
        wr = (ap["apex"] == 1).mean() if ap.height else None
        ev = (ap.select((pl.when(pl.col("apex") == 1).then(EV_WIN)
                         .when(pl.col("apex") == -1).then(EV_STOP).otherwise(EV_EXP)).mean()).item()
              if ap.height else None)
        st15 = s["st15"].drop_nulls().mean(); sp15 = s["sp15"].drop_nulls().mean()
        e15 = s["exc15"].drop_nulls().mean(); e30 = s["exc30"].drop_nulls().mean()
        print(f"{g:12s} {s.height:>7d} {st15*100:>7.2f} {sp15*100:>7.2f} {e15*100:>7.3f} "
              f"{e30*100:>7.3f} {(wr if wr else 0):>7.3f} {(ev*100 if ev else 0):>8.2f}")
        report["by_mkt"][g] = {"n": s.height, "st15": round(st15, 5), "sp15": round(sp15, 5),
                               "exc15": round(e15, 5), "exc30": round(e30, 5),
                               "apex_wr": round(wr, 4) if wr else None,
                               "apex_ev": round(ev, 4) if ev else None}

    print("\n=== by SPY DRAWDOWN-from-60d-high (the 'drawdown window' axis) ===")
    print(f"{'dd_regime':12s} {'N':>7s} {'exc15%':>7s} {'exc30%':>7s} {'apexWR':>7s} {'apexEV%':>8s}")
    for g in ["near_high", "dd_2_6", "dd_6p"]:
        s = L.filter(pl.col("mkt_dd") == g)
        if s.height < 50:
            continue
        ap = s.filter(pl.col("apex").is_not_null())
        wr = (ap["apex"] == 1).mean() if ap.height else None
        ev = (ap.select((pl.when(pl.col("apex") == 1).then(EV_WIN)
                         .when(pl.col("apex") == -1).then(EV_STOP).otherwise(EV_EXP)).mean()).item()
              if ap.height else None)
        e15 = s["exc15"].drop_nulls().mean(); e30 = s["exc30"].drop_nulls().mean()
        print(f"{g:12s} {s.height:>7d} {e15*100:>7.3f} {e30*100:>7.3f} {(wr if wr else 0):>7.3f} "
              f"{(ev*100 if ev else 0):>8.2f}")
        report["by_dd"][g] = {"n": s.height, "exc15": round(e15, 5), "exc30": round(e30, 5),
                              "apex_wr": round(wr, 4) if wr else None, "apex_ev": round(ev, 4) if ev else None}

    # ---- (3) DISCRIMINATOR HUNT within the worst (chop/down) regime ----
    chop = L.filter(pl.col("mkt").is_in(["FLAT_chop", "DOWN_mild", "DOWN_hard"]) & pl.col("apex").is_not_null())
    cwr = (chop["apex"] == 1).mean()
    print(f"\n=== DISCRIMINATOR HUNT within CHOP/DOWN (N={chop.height}, apexWR={cwr:.3f}, baseWR={base_wr:.3f}) ===")
    print("  does any signal-time feature separate the chop/down WINNERS? cohort-z vs chop baseline")
    print(f"{'feature split':32s} {'N_hi':>6s} {'WR_hi':>6s} {'N_lo':>6s} {'WR_lo':>6s} {'z(hi-lo)':>9s}")
    report["chop_discriminators"] = []
    feats = [("c_rsi", 55), ("c_trend", 70), ("c_macd", 55), ("c_bb", 55),
             ("c_stoch", 50), ("rs20", 0.0), ("w_adj", 0.0)]
    for f, thr in feats:
        if f not in chop.columns:
            continue
        hi = chop.filter(pl.col(f) >= thr); lo = chop.filter(pl.col(f) < thr)
        if hi.height < 80 or lo.height < 80:
            continue
        whi = (hi["apex"] == 1).mean(); wlo = (lo["apex"] == 1).mean()
        z = z2(whi, hi.height, wlo, lo.height)
        print(f"{f+' >= '+str(thr):32s} {hi.height:>6d} {whi:>6.3f} {lo.height:>6d} {wlo:>6.3f} {z:>9.2f}")
        report["chop_discriminators"].append({"feature": f, "thr": thr, "n_hi": hi.height,
                                              "wr_hi": round(whi, 4), "n_lo": lo.height,
                                              "wr_lo": round(wlo, 4), "z": round(z, 2)})
    # the documented hint: RSI-high & TREND-low (the bear-robust mean-reversion pocket)
    pk = chop.filter((pl.col("c_rsi") >= 55) & (pl.col("c_trend") < 60))
    rest = chop.filter(~((pl.col("c_rsi") >= 55) & (pl.col("c_trend") < 60)))
    if pk.height >= 80 and rest.height >= 80:
        wpk = (pk["apex"] == 1).mean(); wr = (rest["apex"] == 1).mean()
        z = z2(wpk, pk.height, wr, rest.height)
        print(f"\n  HINT pocket [rsi>=55 & trend<60] in chop/down: N={pk.height} WR={wpk:.3f} "
              f"vs rest WR={wr:.3f}  z={z:.2f}")
        report["chop_rsi_trend_pocket"] = {"n": pk.height, "wr": round(wpk, 4),
                                           "rest_wr": round(wr, 4), "z": round(z, 2)}

    (OUT / "regime_call_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'regime_call_report.json'} ==")


if __name__ == "__main__":
    main()
