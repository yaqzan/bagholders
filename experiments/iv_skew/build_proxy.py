"""Price-based skew PROXY (10y, point-in-time) — to test whether real option skew can be approximated
from price data, enabling full-history validation + a shippable 10y feature.

For each call signal in the 60-99 ledger (10y), compute STRICTLY-PRIOR trailing-60d:
  ret_skew  = skewness of daily returns (negative = crash-heavy = put-skew-like)
  semivol_r = downside_vol / upside_vol (>1 = downside-heavy = put-skew-like)
Join real option skew (iv_ledger) on the 1.3y overlap + the barrier outcomes (already in the ledger).
Reconciliation (separate script) then asks: corr(proxy, real_skew)? does proxy -> option P&L track?
db=light read.
"""
import os, sys, time
import numpy as np
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.models.technical import PriceHistory

RS = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
IV = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")
OUT = os.path.join(_ROOT, ".cache", "iv_skew")
WIN = 60


def main():
    t0 = time.time()
    led = pl.read_parquet(RS)
    syms = led["symbol"].unique().to_list()
    rows_by_sym = {}
    for s, d in zip(led["symbol"].to_list(), led["date"].to_list()):
        rows_by_sym.setdefault(s, []).append(d)

    rskew = {}; svr = {}
    done = 0
    for sym in syms:
        rs = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
                  .where(PriceHistory.symbol == sym).order_by(PriceHistory.date))
        dts = [r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date) for r in rs]
        cl = np.array([float(r.close) if r.close else np.nan for r in rs])
        ret = np.full(len(cl), np.nan); ret[1:] = cl[1:] / cl[:-1] - 1.0
        idx = {d: i for i, d in enumerate(dts)}
        for d in rows_by_sym.get(sym, []):
            i = idx.get(d)
            if i is None or i < WIN:
                continue
            w = ret[i - WIN + 1:i + 1]  # 60 returns ending at signal date (strictly known EOD)
            w = w[~np.isnan(w)]
            if len(w) < 30:
                continue
            mu, sd = w.mean(), w.std(ddof=1)
            if sd < 1e-9:
                continue
            sk = float(np.mean(((w - mu) / sd) ** 3))
            up = w[w > 0]; dn = w[w < 0]
            sv = float(np.std(dn) / np.std(up)) if (len(up) >= 3 and len(dn) >= 3 and np.std(up) > 1e-9) else None
            rskew[(sym, d)] = round(sk, 4)
            svr[(sym, d)] = (round(sv, 4) if sv is not None else None)
        done += 1
        if done % 150 == 0:
            print(f"  {done}/{len(syms)} ({time.time()-t0:.0f}s)", flush=True)

    def col(dct):
        return [dct.get((s, d)) for s, d in zip(led["symbol"].to_list(), led["date"].to_list())]
    led = led.with_columns([pl.Series("ret_skew", col(rskew)), pl.Series("semivol_r", col(svr))])
    # attach real option skew + actual option pnl on the overlap
    iv = pl.read_parquet(IV).select(["symbol", "date", "skew", "pnl15", "pnl_max", "pnl_min"]).rename({"skew": "opt_skew"})
    led = led.join(iv, on=["symbol", "date"], how="left")
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "proxy_ledger.parquet")
    led.write_parquet(out)
    cov = led["ret_skew"].drop_nulls().len()
    ov = led.filter(pl.col("opt_skew").is_not_null()).height
    print(f"DONE {out} N={led.height} ret_skew_cov={100*cov/led.height:.0f}% overlap_with_opt_skew={ov} time={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
