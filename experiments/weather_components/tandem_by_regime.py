"""Pairwise component tandem/anti-synergy BY MARKET ENVIRONMENT (the regime-conditioned grid).

Report 4 gave pooled pairwise tandem; Report A gave per-year univariate + only trend+macd.
This is the missing grid: for all 10 pairs of the 5 base components, the tandem lift
  lift = EV(both>=70) - max(EV(A-only>=70), EV(B-only>=70))   [>0 synergy, <0 anti-synergy]
by VIX band (calm/normal/elevated/panic) AND by year, on the 30dte_apex CALL payoff.
Plus the ALPHA lens: within the FUNDED >=75 book, apex-EV by each top pair's agreement state x VIX.

Read-only. In-sample. apex EV +0.30/-0.70/-0.40, 30d.
"""
import os
import sys
import math
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion, MarketRegime

VID = AlgorithmVersion.get_active_scores_version().id
EVMAP = {"win": +0.30, "stop": -0.70, "expire": -0.40}
BASE5 = ["trend", "bb", "rsi", "macd", "stoch"]
SAMPLE_N = int(os.environ.get("SAMPLE_N", "300000"))


def mean_diff_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


def vix_map():
    m = {}
    for r in MarketRegime.select(MarketRegime.date, MarketRegime.vix_close).where(
        MarketRegime.vix_close.is_null(False)
    ):
        d = r.date
        m[d.isoformat() if hasattr(d, "isoformat") else str(d)] = float(r.vix_close)
    return m


def vix_band(v):
    if v != v:
        return None
    if v < 16:
        return "calm<16"
    if v < 22:
        return "norm16-22"
    if v < 28:
        return "elev22-28"
    return "panic>=28"


def attach(df):
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    res, _ = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in res:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get("30d")
        if sw and sw.get("result") in EVMAP:
            m[(r["symbol"], dk)] = EVMAP[sw["result"]]
    vm = vix_map()
    ev = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
    vb = [vix_band(vm.get(d, float("nan"))) for d in df["date"]]
    return df.with_columns(
        apex_ev=pl.Series(ev, dtype=pl.Float64),
        vixb=pl.Series(vb, dtype=pl.Utf8),
        yr=pl.col("date").str.slice(0, 4),
    ).filter(pl.col("apex_ev").is_not_null())


def ev(df, mask):
    s = df.filter(mask)
    return (s.height, s["apex_ev"].to_numpy() * 100 if s.height else np.array([]))


def tandem_lift(d, A, B):
    nb, eb = ev(d, (pl.col(A) >= 70) & (pl.col(B) >= 70))
    na, ea = ev(d, (pl.col(A) >= 70) & (pl.col(B) < 70))
    nbo, ebo = ev(d, (pl.col(B) >= 70) & (pl.col(A) < 70))
    if nb < 40 or na < 20 or nbo < 20:
        return None, nb
    best = ea.mean() if ea.mean() >= ebo.mean() else ebo.mean()
    return (eb.mean() - best), nb


def main():
    parquet = materialize_polars(f"weather_comp_v{VID}_2016", None)
    full = pl.read_parquet(parquet)
    uni = attach(full.sample(n=min(SAMPLE_N, full.height), seed=17))
    f75 = attach(full.filter(pl.col("overall") >= 75))
    pairs = [(BASE5[i], BASE5[j]) for i in range(5) for j in range(i + 1, 5)]

    print(f"\n{'='*94}\nPAIRWISE TANDEM LIFT by MARKET ENVIRONMENT (v{VID}, apex payoff)\n{'='*94}")
    print("cell = tandem lift pp (N both>=70). >0 = work together (synergy); <0 = anti-synergy. |lift|>=1pp & N>=150 = bold.")

    vbands = ["calm<16", "norm16-22", "elev22-28", "panic>=28"]
    print(f"\n--- by VIX band ---  (universe rows/band: " +
          ", ".join(f"{b}={uni.filter(pl.col('vixb')==b).height:,}" for b in vbands) + ")")
    print(f"{'pair':>12} | " + " | ".join(f"{b:>16}" for b in vbands))
    for A, B in pairs:
        cells = []
        for b in vbands:
            lift, nb = tandem_lift(uni.filter(pl.col("vixb") == b), A, B)
            if lift is None:
                cells.append(f"{'. (N'+str(nb)+')':>16}")
            else:
                mark = "*" if (abs(lift) >= 1.0 and nb >= 150) else " "
                cells.append(f"{lift:+6.2f}{mark}(N{nb:>5,})")
        print(f"{A+'+'+B:>12} | " + " | ".join(cells))

    years = ["2021", "2022", "2023", "2024", "2025"]
    print(f"\n--- by year ---")
    print(f"{'pair':>12} | " + " | ".join(f"{y:>14}" for y in years))
    for A, B in pairs:
        cells = []
        for y in years:
            lift, nb = tandem_lift(uni.filter(pl.col("yr") == y), A, B)
            cells.append(f"{lift:+6.2f}(N{nb:>5,})" if lift is not None else f"{'.(N'+str(nb)+')':>14}")
        print(f"{A+'+'+B:>12} | " + " | ".join(cells))

    # ALPHA LENS: funded >=75 book — does a pair's agreement STATE predict apex-EV per VIX band?
    print(f"\n{'='*94}\nALPHA LENS — FUNDED >=75 book: apex-EV by pair agreement-state x VIX band\n{'='*94}")
    print("For a tradeable tilt we need a state x regime cell that is materially worse/better AND has N>=80.")
    for A, B in [("trend", "macd"), ("bb", "rsi"), ("trend", "bb"), ("rsi", "macd")]:
        print(f"\n  pair {A}+{B}:")
        for b in vbands:
            d = f75.filter(pl.col("vixb") == b)
            if d.height < 60:
                print(f"    {b:>10}: N={d.height} (thin)"); continue
            nbb, ebb = ev(d, (pl.col(A) >= 70) & (pl.col(B) >= 70))     # both confirm
            nsp, esp = ev(d, ((pl.col(A) >= 70) != (pl.col(B) >= 70)))   # split (one confirms)
            nnn, enn = ev(d, (pl.col(A) < 70) & (pl.col(B) < 70))        # neither (still 75+ via others)
            t = mean_diff_t(ebb, esp) if nbb and nsp else float("nan")
            print(f"    {b:>10}: both {ebb.mean() if nbb else float('nan'):+5.2f}%(N{nbb}) | "
                  f"split {esp.mean() if nsp else float('nan'):+5.2f}%(N{nsp}) | "
                  f"neither {enn.mean() if nnn else float('nan'):+5.2f}%(N{nnn}) | both-split t={t:+.2f}")
    print("\nNOTE: in-sample; per-cell N thin -> read signs + magnitude + N, not precision. apex EV +0.30/-0.70/-0.40.")


if __name__ == "__main__":
    main()
