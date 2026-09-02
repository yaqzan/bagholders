"""W1 pre-flight for the Oscillator-Alignment Lift (OAL) booster.

The linear reweight collapsed supply because TREND is load-bearing for 75+ membership.
An ADDITIVE booster instead PROMOTES a validated high-WR cohort from 70-74 into 75+ (growing
supply with higher-WR signals) without trimming TREND's weight. This profiles candidate
promotion-cohort definitions on the funded apex barrier to confirm a strong, sizable, promotable
target exists — and (critically) that the SAME cohort definition wins at BOTH 70-74 (promotion)
and 75-79 (the band it joins), i.e. the promoted signals are >= the band marginal they land in.

A promotion is accretive iff cohort apex WR >= the 75-79 band marginal (~baseline). We want a
cohort that (a) is materially above the 70-74 baseline (68.1%), (b) holds that edge vs 75-79,
(c) is large enough to move supply, (d) z>=3.
"""
import os, sys, math
import polars as pl
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
from label import add_label

df = pl.read_parquet(LEDGER)
df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))
df = add_label(df, 1.092, 2.548, 15, "apex15")
COMPS = ["c_trend", "c_bb", "c_rsi", "c_macd", "c_stoch", "c_ta"]


def wr(sub):
    s = sub.filter(pl.col("apex15").is_not_null())
    return (float(s["apex15"].mean()), s.height) if s.height else (None, 0)


def z(p1, n1, p0, n0):
    if not n1 or not n0 or p1 is None or p0 is None:
        return None
    pp = (p1 * n1 + p0 * n0) / (n1 + n0)
    se = math.sqrt(max(1e-12, pp * (1 - pp) * (1 / n1 + 1 / n0)))
    return (p1 - p0) / se if se else None


def band(lo, hi):
    return df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))


# tercile thresholds computed on the 70-74 promotion reservoir (where promotion happens)
p7074 = band(70, 74)
q = {c: (float(p7074[c].quantile(0.5)), float(p7074[c].quantile(0.67)), float(p7074[c].quantile(0.33)))
     for c in COMPS}

# candidate cohort definitions (signal-time, point-in-time safe)
def cohorts(d):
    ta_hi = pl.col("c_ta") >= q["c_ta"][1]
    ta_med = pl.col("c_ta") >= q["c_ta"][0]
    tr_lo = pl.col("c_trend") <= q["c_trend"][0]
    tr_vlo = pl.col("c_trend") <= q["c_trend"][2]
    osc = (pl.col("c_rsi") >= 52) & (pl.col("c_stoch") >= 52) & (pl.col("c_macd") >= 52)
    osc_strong = (pl.col("c_rsi") >= 58) & (pl.col("c_stoch") >= 58) & (pl.col("c_macd") >= 58)
    return [
        ("TA_hi", ta_hi),
        ("TREND_lo", tr_lo),
        ("TA_hi & TREND<=med", ta_hi & tr_lo),
        ("TA_hi & TREND_vlo", ta_hi & tr_vlo),
        ("osc_agree(>=52)", osc),
        ("osc_strong(>=58)", osc_strong),
        ("TA_med & osc_agree", ta_med & osc),
        ("TREND_lo & osc_agree", tr_lo & osc),
        ("TA_hi & osc_agree", ta_hi & osc),
    ]


for lo, hi, nm in [(70, 74, "PROMOTION 70-74"), (75, 79, "BAND 75-79"),
                   (80, 84, "BAND 80-84"), (85, 99, "BAND 85+"), (75, 99, "POOL 75+")]:
    d = band(lo, hi)
    bp, bn = wr(d)
    print("\n=== %s  baseline apex=%.1f%% N=%d ===" % (nm, bp * 100, bn))
    print("  %-22s  apexWR    N     z_vs_base   share" % "cohort")
    for cname, expr in cohorts(d):
        sub = d.filter(expr)
        p, n = wr(sub)
        if n < 20:
            continue
        zz = z(p, n, bp, bn)
        print("  %-22s  %5.1f%%  %5d   %s   %4.1f%%" % (
            cname, (p * 100 if p else 0), n,
            ("%+5.2f" % zz if zz is not None else "  n/a"), 100.0 * n / bn))
