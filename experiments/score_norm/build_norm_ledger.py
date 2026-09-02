"""Point-in-time per-stock score-NORMALIZATION ledger.

For every v70 (symbol, date, overall) over [START..CUTOFF], compute STRICTLY-POINT-IN-TIME
normalization features (no look-ahead — uses only data with date < today for the per-stock
stats; cross-sectional rank is same-day which is PIT-safe):
  ps_z      = (overall - expanding_mean_prior) / expanding_std_prior        (per-stock z)
  ps_pctile = frac of prior-days with overall <= today's overall            (per-stock percentile)
  xs_pctile = frac of stocks THAT DAY with overall <= today's overall        (cross-sectional rank)
  n_prior   = # prior obs used (warmup gate)
Then for CANDIDATE signal rows (absolute>=75 OR ps_z>=Z OR ps_pctile>=P OR xs_pctile>=X) capture
the barrier-agnostic forward path (t_up/t_dn for any TP/SL/W via label.derive). Holdout-locked.

WARMUP: ps_* require >= WARMUP prior obs, else null (a stock's first ~year can't be normalized).
Test mode: set NORM_SAMPLE="AAL,NVDA,ANET" to build+print a few stocks and verify PIT correctness.
"""
import os, sys, json, time, bisect
import numpy as np

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))

from database.trader_database import DB
from database.models.technical import PriceHistory
from experiments._holdout import CUTOFF as HOLDOUT_CUTOFF

CUTOFF = HOLDOUT_CUTOFF.isoformat()
START = os.environ.get("NORM_START", "2016-05-15")
VERSION_ID = int(os.environ.get("NORM_VERSION_ID", "70"))
WARMUP = int(os.environ.get("NORM_WARMUP", "120"))     # prior obs required before normalizing
Z_TH = float(os.environ.get("NORM_Z", "1.0"))
P_TH = float(os.environ.get("NORM_P", "0.90"))
X_TH = float(os.environ.get("NORM_X", "0.90"))
SAMPLE = [s for s in os.environ.get("NORM_SAMPLE", "").split(",") if s]
MAXW = 40
VOL_WIN = 60
UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)


def main():
    t0 = time.time()
    where_sym = (" AND symbol IN (%s)" % ",".join("'%s'" % s for s in SAMPLE)) if SAMPLE else ""
    sql = ("SELECT symbol, date, overall FROM scores WHERE version_id=%d "
           "AND overall IS NOT NULL AND date BETWEEN '%s' AND '%s'%s ORDER BY symbol, date"
           % (VERSION_ID, START, CUTOFF, where_sym))
    rows = DB.execute_sql(sql).fetchall()
    print("pulled scores:", len(rows), "in %.1fs" % (time.time() - t0), flush=True)

    by_sym = {}
    xs = {}  # date -> list of overall (for cross-sectional rank)
    for sym, d, ov in rows:
        if ov is None:
            continue
        by_sym.setdefault(sym, []).append((d, int(ov)))
        xs.setdefault(d, []).append(int(ov))
    for d in xs:
        xs[d].sort()
    print("symbols:", len(by_sym), " dates:", len(xs), flush=True)

    # ---- point-in-time per-stock normalization ----
    feats = {}  # (sym,date) -> dict
    for sym, series in by_sym.items():
        # series sorted by date already (ORDER BY)
        prior_sorted = []      # running sorted list of PRIOR overalls (strictly before today)
        csum = 0.0; csum2 = 0.0; npr = 0
        for (d, ov) in series:
            # stats from STRICTLY-prior obs
            if npr >= WARMUP:
                mean = csum / npr
                var = max(0.0, csum2 / npr - mean * mean)
                std = var ** 0.5
                z = (ov - mean) / std if std > 1e-9 else 0.0
                # percentile: frac of prior <= ov
                k = bisect.bisect_right(prior_sorted, ov)
                pct = k / npr
            else:
                z = None; pct = None
            # cross-sectional percentile THAT DAY (same-day, PIT-safe)
            day = xs[d]; kk = bisect.bisect_right(day, ov); xpct = kk / len(day)
            feats[(sym, d)] = {"overall": ov, "ps_z": (round(z, 4) if z is not None else None),
                               "ps_pctile": (round(pct, 4) if pct is not None else None),
                               "xs_pctile": round(xpct, 4), "n_prior": npr}
            # now add today to the running prior for FUTURE days
            bisect.insort(prior_sorted, ov)
            csum += ov; csum2 += ov * ov; npr += 1

    if SAMPLE:
        # PIT correctness print
        for sym in SAMPLE:
            ser = by_sym.get(sym, [])
            print(f"\n--- {sym} (n={len(ser)}) PIT check: first norm at obs#{WARMUP}, uses only prior ---")
            shown = 0
            for (d, ov) in ser:
                f = feats[(sym, d)]
                if f["ps_z"] is not None and shown < 6:
                    print(f"   {d} ov={ov:3} n_prior={f['n_prior']:4} ps_z={f['ps_z']:+.2f} ps_pctile={f['ps_pctile']:.2f} xs_pctile={f['xs_pctile']:.2f}")
                    shown += 1
            # signal counts under each scheme
            ns_abs = sum(1 for (d, ov) in ser if ov >= 75)
            ns_z = sum(1 for (d, ov) in ser if (feats[(sym, d)]["ps_z"] or -9) >= Z_TH)
            ns_p = sum(1 for (d, ov) in ser if (feats[(sym, d)]["ps_pctile"] or -9) >= P_TH)
            print(f"   signals: absolute>=75: {ns_abs} | ps_z>={Z_TH}: {ns_z} | ps_pctile>={P_TH}: {ns_p}  (max ov={max(o for _,o in ser)})")
        print("\nSAMPLE mode — no forward walk / no write. Verify: n_prior strictly increases, first")
        print("normalized row is at obs#%d, and a 0-signal-absolute stock now gets ps_z/ps_pctile signals." % WARMUP)
        return

    # ---- candidate signal rows -> forward path ----
    def is_cand(f):
        return (f["overall"] >= 75 or (f["ps_z"] is not None and f["ps_z"] >= Z_TH)
                or (f["ps_pctile"] is not None and f["ps_pctile"] >= P_TH)
                or f["xs_pctile"] >= X_TH)
    cand_syms = set(s for (s, d) in feats if is_cand(feats[(s, d)]))
    print("candidate signals across", len(cand_syms), "symbols; loading price...", flush=True)

    t1 = time.time()
    price = {}
    for sym in cand_syms:
        rs = list(PriceHistory.select(PriceHistory.date, PriceHistory.close, PriceHistory.high, PriceHistory.low)
                  .where(PriceHistory.symbol == sym).order_by(PriceHistory.date))
        if not rs:
            continue
        dts = np.array([r.date for r in rs]); cl = np.array([float(r.close) for r in rs])
        hi = np.array([float(r.high) for r in rs]); lo = np.array([float(r.low) for r in rs])
        ret = np.empty_like(cl); ret[0] = 0.0; ret[1:] = cl[1:] / cl[:-1] - 1.0
        sig = np.full(len(cl), np.nan)
        for i in range(len(cl)):
            lob = max(0, i - VOL_WIN + 1)
            if i - lob >= 20:
                sig[i] = np.std(ret[lob:i + 1], ddof=1)
        price[sym] = (dts, cl, hi, lo, sig, {d: i for i, d in enumerate(dts)})
    print("price load: %d syms in %.1fs" % (len(price), time.time() - t1), flush=True)

    t2 = time.time(); recs = []
    for (sym, d), f in feats.items():
        if not is_cand(f) or sym not in price:
            continue
        dts, cl, hi, lo, sig, idx = price[sym]
        i0 = idx.get(d)
        if i0 is None:
            continue
        s = sig[i0]; entry = cl[i0]
        if not (s and s > 1e-6 and entry > 0):
            continue
        j = i0 + 1; caldays = []; up_ex = []; dn_ex = []
        while j < len(dts):
            cd = (dts[j] - d).days
            if cd > MAXW:
                break
            if cd >= 1:
                caldays.append(cd); up_ex.append((hi[j] - entry) / entry / s); dn_ex.append((entry - lo[j]) / entry / s)
            j += 1
        if not caldays:
            continue
        caldays = np.array(caldays); cum_up = np.maximum.accumulate(np.array(up_ex)); cum_dn = np.maximum.accumulate(np.array(dn_ex))
        iu = np.searchsorted(cum_up, UP_GRID, side="left"); idn = np.searchsorted(cum_dn, DN_GRID, side="left")
        t_up = np.where(iu < len(caldays), caldays[np.clip(iu, 0, len(caldays) - 1)], 999).astype(np.int16)
        t_dn = np.where(idn < len(caldays), caldays[np.clip(idn, 0, len(caldays) - 1)], 999).astype(np.int16)
        recs.append({"symbol": sym, "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                     "overall": f["overall"], "ps_z": f["ps_z"], "ps_pctile": f["ps_pctile"],
                     "xs_pctile": f["xs_pctile"], "n_prior": f["n_prior"], "vol_pct": round(float(s) * 100, 4),
                     "fwd_caldays": int(caldays[-1]), "t_up": t_up.tolist(), "t_dn": t_dn.tolist()})
    print("forward walk: %d signals in %.1fs" % (len(recs), time.time() - t2), flush=True)

    import polars as pl
    outdir = os.path.join(_ROOT, ".cache", "score_norm"); os.makedirs(outdir, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    out = os.path.join(outdir, "norm_ledger_v%d.parquet" % VERSION_ID)
    df.write_parquet(out)
    assert df["date"].max() <= CUTOFF, "HOLDOUT LEAK"
    meta = {"version_id": VERSION_ID, "warmup": WARMUP, "z_th": Z_TH, "p_th": P_TH, "x_th": X_TH,
            "N": df.height, "window": [df["date"].min(), df["date"].max()],
            "n_abs75": int((df["overall"] >= 75).sum())}
    json.dump(meta, open(os.path.join(outdir, "norm_ledger_meta.json"), "w"), indent=2)
    print("DONE", out, "N=%d" % df.height, "abs75=%d" % meta["n_abs75"], flush=True)


if __name__ == "__main__":
    main()
