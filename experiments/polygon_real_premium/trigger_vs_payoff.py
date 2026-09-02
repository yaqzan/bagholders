"""Decompose the barrier error: is it the TRIGGER LEVEL or the PAYOFF MAP?

Two links, two different consumers:
  link 1  TRIGGER : exit when the UNDERLYING touches +1.092 sigma / -2.548 sigma.
                    Used by BOTH the apex15 assessment AND the Stage-3 MC.
  link 2  PAYOFF  : the assessment applies a FIXED +0.30/-0.70/-0.40 EV map.
                    The MC does NOT -- it reprices via option_pricing.option_pnl_pct.

`barrier_fidelity.py` tested the two jointly ("did the real CONTRACT reach +30%?").
This isolates them by asking the question the MC actually depends on:

    WHEN THE UNDERLYING TOUCHES +1.092 SIGMA, WHAT IS THE REAL CONTRACT WORTH?

If the answer is ~+30%, the sigma trigger is faithful and the MC is untouched --
the whole error lives in the assessment's fixed EV map. If it is materially below
+30%, the trigger level itself is mis-calibrated and the MC inherits it.

Read-only. ASCII output only.
"""
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / ".cache/polygon_real_premium/real_premium_ledger.parquet"
OHLC = ROOT / ".cache/experiment_data/event_noise_ohlc_2019.parquet"

TP_SIGMA, SL_SIGMA = 1.092, 2.548     # = TP/SL x PREMIUM_MULT / DELTA
VOL_WINDOW, VOL_MIN = 60, 20
HOLD_MKT = 15
DTE_LO, DTE_HI = 25, 38


def vol_and_path(ohlc):
    """{symbol: (dates[list], close[np], high[np], low[np], sigma[np])} causal 60-bar."""
    out = {}
    for sym, g in ohlc.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
        s = sym[0] if isinstance(sym, tuple) else sym
        c = g["close"].to_numpy().astype(float)
        n = len(c)
        if n < VOL_MIN + 2:
            continue
        ret = np.zeros(n)
        ret[1:] = c[1:] / c[:-1] - 1.0
        cs = np.concatenate([[0.0], np.cumsum(ret)])
        cs2 = np.concatenate([[0.0], np.cumsum(ret ** 2)])
        idx = np.arange(n)
        lo = np.maximum(1, idx - VOL_WINDOW + 1)
        cnt = idx - lo + 1
        a = cs[idx + 1] - cs[lo]
        b = cs2[idx + 1] - cs2[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = a / cnt
            var = (b - cnt * mean * mean) / np.maximum(cnt - 1, 1)
        sig = np.where(cnt >= VOL_MIN, np.sqrt(np.maximum(var, 0.0)), np.nan)
        out[s] = (
            {d: i for i, d in enumerate(g["date"].to_list())},
            c, g["high"].to_numpy().astype(float), g["low"].to_numpy().astype(float), sig,
        )
    return out


def main():
    real = pl.read_parquet(REAL).filter(pl.col("status") == "kept")
    ohlc = pl.read_parquet(OHLC)
    print("building causal vol/price index ...", flush=True)
    idx = vol_and_path(ohlc)
    print(f"  symbols indexed: {len(idx)}")

    import datetime as dt
    rows = []
    for r in real.iter_rows(named=True):
        sym, sd = r["symbol"], r["signal_date"]
        ent = dt.date.fromisoformat(sd) if isinstance(sd, str) else sd
        rec = idx.get(sym)
        if rec is None:
            continue
        dmap, close, high, low, sig = rec
        i0 = dmap.get(ent)
        if i0 is None or i0 + 1 >= len(close):
            continue
        s0 = sig[i0]
        if not np.isfinite(s0) or s0 <= 0:
            continue
        e = close[i0]
        tp_lvl = e * (1 + TP_SIGMA * s0)
        sl_lvl = e * (1 - SL_SIGMA * s0)

        # first underlying touch within the apex15 horizon
        end = min(i0 + HOLD_MKT, len(close) - 1)
        tp_bar = sl_bar = None
        for k in range(i0 + 1, end + 1):
            if tp_bar is None and high[k] >= tp_lvl:
                tp_bar = k - i0
            if sl_bar is None and low[k] <= sl_lvl:
                sl_bar = k - i0
            if tp_bar is not None and sl_bar is not None:
                break

        # real contract value at that offset
        try:
            path = json.loads(r["path_json"]) if r.get("path_json") else []
        except Exception:                                          # noqa: BLE001
            path = []
        by_off = {}
        for p in path:
            o = p.get("off")
            if o is not None:
                by_off[int(o)] = p
        ep = r.get("entry_premium_real")
        if not ep or ep <= 0:
            continue

        def mult_at(off):
            p = by_off.get(off)
            if p is None:
                return None, None
            c = p.get("c")
            h = p.get("h")
            return (float(c) / ep if c else None), (float(h) / ep if h else None)

        tp_c = tp_h = sl_c = None
        if tp_bar is not None:
            tp_c, tp_h = mult_at(tp_bar)
        if sl_bar is not None:
            sl_c, _ = mult_at(sl_bar)

        rows.append(dict(symbol=sym, signal_date=sd, dte_cal=r.get("dte_cal"),
                         liquid=bool(r.get("liquid_entry")),
                         sigma=float(s0), tp_bar=tp_bar, sl_bar=sl_bar,
                         tp_close_mult=tp_c, tp_high_mult=tp_h, sl_close_mult=sl_c))

    df = pl.DataFrame(rows, infer_schema_length=None)
    print(f"  rows with usable vol+path: {df.height}")

    prim = df.filter(pl.col("liquid") & (pl.col("dte_cal") >= DTE_LO)
                     & (pl.col("dte_cal") <= DTE_HI))

    print("\n" + "=" * 90)
    print("LINK 1 -- when the UNDERLYING touches +1.092 sigma, what is the real contract worth?")
    print("   (the assessment AND the MC both assume this moment is worth +30%, i.e. 1.30x)")
    print("=" * 90)
    for lab, d in [("all kept", df), (f"liquid + DTE {DTE_LO}-{DTE_HI}", prim)]:
        t = d.filter(pl.col("tp_bar").is_not_null() & pl.col("tp_close_mult").is_not_null())
        if t.height < 50:
            continue
        m = t["tp_close_mult"]
        h = t.filter(pl.col("tp_high_mult").is_not_null())["tp_high_mult"]
        print(f"\n  [{lab}]  underlying-TP touches with a priced contract bar: N={t.height}")
        print(f"    contract CLOSE multiple at the touch bar : "
              f"p25={float(m.quantile(.25)):.3f} MED={float(m.quantile(.5)):.3f} "
              f"p75={float(m.quantile(.75)):.3f}  mean={float(m.mean()):.3f}")
        if h.len() > 50:
            print(f"    contract HIGH  multiple at the touch bar : "
                  f"p25={float(h.quantile(.25)):.3f} MED={float(h.quantile(.5)):.3f} "
                  f"p75={float(h.quantile(.75)):.3f}  mean={float(h.mean()):.3f}")
        print(f"    share with CLOSE >= 1.30x  : {float((m >= 1.30).mean()):.4f}")
        if h.len() > 50:
            print(f"    share with HIGH  >= 1.30x  : {float((h >= 1.30).mean()):.4f}")
        print(f"    ==> assumed 1.300x, realized median {float(m.quantile(.5)):.3f}x "
              f"(CLOSE) / {float(h.quantile(.5)) if h.len() > 50 else float('nan'):.3f}x (HIGH)")

    print("\n" + "=" * 90)
    print("LINK 1 (down) -- when the UNDERLYING touches -2.548 sigma, is the contract at -70%?")
    print("   (assumed 0.30x)")
    print("=" * 90)
    for lab, d in [("all kept", df), (f"liquid + DTE {DTE_LO}-{DTE_HI}", prim)]:
        t = d.filter(pl.col("sl_bar").is_not_null() & pl.col("sl_close_mult").is_not_null())
        if t.height < 50:
            continue
        m = t["sl_close_mult"]
        print(f"  [{lab}] N={t.height}  p25={float(m.quantile(.25)):.3f} "
              f"MED={float(m.quantile(.5)):.3f} p75={float(m.quantile(.75)):.3f}  "
              f"share <= 0.30x = {float((m <= 0.30).mean()):.4f}")

    print("\n" + "=" * 90)
    print("TRIGGER RATES -- underlying barrier vs real contract barrier (same rows)")
    print("=" * 90)
    d = prim
    print(f"  N={d.height}")
    print(f"    underlying touched +1.092 sigma within {HOLD_MKT}d : "
          f"{float(d['tp_bar'].is_not_null().mean()):.4f}")
    print(f"    underlying touched -2.548 sigma within {HOLD_MKT}d : "
          f"{float(d['sl_bar'].is_not_null().mean()):.4f}")

    print("\n  time-to-touch (underlying TP), market days:")
    tb = d.filter(pl.col("tp_bar").is_not_null())["tp_bar"]
    if tb.len() > 50:
        print(f"    p25={float(tb.quantile(.25)):.0f} MED={float(tb.quantile(.5)):.0f} "
              f"p75={float(tb.quantile(.75)):.0f}")

    out = ROOT / ".cache/polygon_real_premium/trigger_vs_payoff.parquet"
    df.write_parquet(out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    sys.exit(main())
