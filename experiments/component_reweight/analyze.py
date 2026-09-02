"""Stage A — descriptive: which component-score COMBINATIONS win vs lose on the funded
Apex HOLD sleeve, and do the patterns survive at the 70-74/75-79 reweight boundary?

Primary win label = hold15 (TP-reach 1.274sigma within 15d, ~no early stop) — the Apex
HOLD sleeve outcome. Secondary = opt15 (growth-gate primary), gen15 (dashboard).

Controls baked in (prior-work confounds):
  * bucket WITHIN fixed overall bands (75-79 / 80-84 / 85+) so composition effects aren't
    just "this lead => higher overall".
  * "lead" = weighted contribution w_i*(c_i-50), not raw component (a high-RSI low-weight
    contributes less than a mid-MACD high-weight).
  * MARGINAL check: same buckets on 70-74 (promotion reservoir) + 75-79 (demotion-prone),
    because a reweight moves THOSE signals across 75 — the in-75+ pattern is necessary but
    NOT sufficient; the boundary pattern is what reweighting actually trades on.
  * momentum confound: cross-tab winning combos vs c_trend tercile + vol_pct tercile.
"""
import os, sys, json, math
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from label import standard_labels
LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")

COMPS = ["c_trend", "c_bb", "c_rsi", "c_macd", "c_stoch", "c_ta"]
COMP_SHORT = {"c_trend": "TREND", "c_bb": "BB", "c_rsi": "RSI",
              "c_macd": "MACD", "c_stoch": "STOCH", "c_ta": "TA"}
WCOL = {"c_trend": "w_trend", "c_bb": "w_bb", "c_rsi": "w_rsi",
        "c_macd": "w_macd", "c_stoch": "w_stoch", "c_ta": "w_ta"}


def wr(df, col):
    s = df.filter(pl.col(col).is_not_null())
    if s.height == 0:
        return None, 0
    return float(s[col].mean()), s.height


def zprop(p1, n1, p0, n0):
    """two-proportion z (subset p1/n1 vs reference p0/n0)."""
    if not n1 or not n0 or p1 is None or p0 is None:
        return None
    pp = (p1 * n1 + p0 * n0) / (n1 + n0)
    se = math.sqrt(max(1e-12, pp * (1 - pp) * (1 / n1 + 1 / n0)))
    return (p1 - p0) / se if se > 0 else None


def fmt(p):
    return "  n/a" if p is None else "%5.1f" % (p * 100)


def line(s=""):
    print(s)
    OUT.append(s)


OUT = []


def band_baselines(df, label):
    line("\n=== %s baselines (N, apexTP15 / holdTP15 / optTP15 / genWR15) ===" % label)
    for lo, hi in [(70, 74), (75, 79), (80, 84), (85, 99), (75, 99)]:
        sub = df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        a, n = wr(sub, "apex15"); h, _ = wr(sub, "hold15"); o, _ = wr(sub, "opt15"); g, _ = wr(sub, "gen15")
        tag = "%d-%d" % (lo, hi) if hi < 99 else ("%d+" % lo)
        line("  %-7s N=%6d   apex %s   hold %s   opt %s   gen %s" % (tag, n, fmt(a), fmt(h), fmt(o), fmt(g)))


def lead_class(df):
    """weighted contribution per component; lead = argmax contribution."""
    expr = []
    for c in COMPS:
        expr.append(((pl.col(c) - 50) * pl.col(WCOL[c]) / 100.0).alias("contrib_%s" % c))
    df = df.with_columns(expr)
    contrib_cols = ["contrib_%s" % c for c in COMPS]
    # lead = name of max-contribution component, per row
    lead = (df.select(contrib_cols).to_numpy().argmax(axis=1))
    names = [COMP_SHORT[c] for c in COMPS]
    df = df.with_columns(pl.Series("lead", [names[i] for i in lead]))
    return df


def lead_table(df, label, win="hold15"):
    line("\n=== %s — lead-component (max weighted contribution) vs %s ===" % (label, win))
    base_p, base_n = wr(df, win)
    line("  pool baseline %s = %s  (N=%d)" % (win, fmt(base_p), base_n))
    rows = []
    for nm in ["TREND", "BB", "RSI", "MACD", "STOCH", "TA"]:
        sub = df.filter(pl.col("lead") == nm)
        p, n = wr(sub, win)
        z = zprop(p, n, base_p, base_n) if (p is not None and n >= 20) else None
        rows.append((nm, n, p, z))
    for nm, n, p, z in sorted(rows, key=lambda r: (-(r[2] or 0))):
        zs = "      " if z is None else ("%+5.2f" % z)
        line("  lead=%-6s N=%5d   %s   z=%s   share=%4.1f%%"
             % (nm, n, fmt(p), zs, 100.0 * n / max(1, df.height)))


def tercile_table(df, label, win="hold15"):
    line("\n=== %s — per-RAW-component tercile vs %s (within band) ===" % (label, win))
    base_p, base_n = wr(df, win)
    line("  baseline %s = %s (N=%d). Spread = HIGH-tercile minus LOW-tercile WR." % (win, fmt(base_p), base_n))
    for c in COMPS:
        vals = df[c].to_numpy()
        if len(vals) < 30:
            continue
        q1 = float(pl.Series(vals).quantile(0.3333))
        q2 = float(pl.Series(vals).quantile(0.6667))
        lo = df.filter(pl.col(c) <= q1)
        mid = df.filter((pl.col(c) > q1) & (pl.col(c) <= q2))
        hi = df.filter(pl.col(c) > q2)
        plo, nlo = wr(lo, win); pmid, nmid = wr(mid, win); phi, nhi = wr(hi, win)
        spread = (phi - plo) if (phi is not None and plo is not None) else None
        z = zprop(phi, nhi, plo, nlo) if (phi is not None and plo is not None and nhi >= 20 and nlo >= 20) else None
        ss = "   n/a" if spread is None else "%+5.1f" % (spread * 100)
        zs = "      " if z is None else ("%+5.2f" % z)
        line("  %-6s [<=%2.0f|%2.0f<] lo %s (N%5d)  mid %s  hi %s (N%5d)   spread %s  z=%s"
             % (COMP_SHORT[c], q1, q2, fmt(plo), nlo, fmt(pmid), fmt(phi), nhi, ss, zs))


def pair_matrix(df, label, win="hold15", thr=65):
    """User's example: high MACD+RSI vs high BB(+volume). 2x2 cells on (X high?, Y high?)."""
    line("\n=== %s — pairwise HIGH-component cells (>=%d) vs %s ===" % (label, thr, win))
    pairs = [("c_macd", "c_rsi"), ("c_bb", "c_macd"), ("c_trend", "c_macd"),
             ("c_bb", "c_rsi"), ("c_stoch", "c_macd"), ("c_trend", "c_rsi")]
    for x, y in pairs:
        line("  %s x %s:" % (COMP_SHORT[x], COMP_SHORT[y]))
        for xh in (True, False):
            for yh in (True, False):
                sub = df.filter(
                    ((pl.col(x) >= thr) == xh) & ((pl.col(y) >= thr) == yh))
                p, n = wr(sub, win)
                if n >= 20:
                    line("    %s%s / %s%s   N=%5d   %s"
                         % (COMP_SHORT[x], "+" if xh else "-",
                            COMP_SHORT[y], "+" if yh else "-", n, fmt(p)))


def volume_table(df, label, win="hold15"):
    line("\n=== %s — volume signal vs %s ===" % (label, win))
    base_p, base_n = wr(df, win)
    sigs = df["vol_signal"].unique().to_list()
    for sg in sorted([s for s in sigs if s is not None]):
        sub = df.filter(pl.col("vol_signal") == sg)
        p, n = wr(sub, win)
        if n >= 20:
            z = zprop(p, n, base_p, base_n)
            line("  %-12s N=%5d   %s   z=%s" % (sg, n, fmt(p), "n/a" if z is None else "%+5.2f" % z))


def robustness_table(df, label):
    """Lead-class win% across a RANGE of (TP,SL) — the apex/sentinel/core knobs are being
    retuned, so show the ORDERING is barrier-stable, not an artifact of one best-guess."""
    from label import add_label
    bars = [("TP30/SL70", 1.092, 2.548, 15), ("TP25/SL70", 0.910, 2.548, 15),
            ("TP35/SL70", 1.274, 2.548, 15), ("TP30/SL50", 1.092, 1.820, 15),
            ("TP30/SL100", 1.092, 3.640, 15), ("opt(gate)", 0.901, 0.772, 15)]
    line("\n=== %s — lead-class win%% ROBUSTNESS across barriers (the ordering is what matters) ===" % label)
    cols = []
    d2 = df
    for nm, tp, sl, w in bars:
        d2 = add_label(d2, tp, sl, w, "_b_" + nm)
        cols.append(nm)
    hdr = "  lead     " + "".join("%11s" % c for c in cols)
    line(hdr)
    for nm in ["TREND", "BB", "RSI", "MACD", "STOCH", "TA"]:
        sub = d2.filter(pl.col("lead") == nm)
        if sub.height < 20:
            continue
        cells = []
        for c in cols:
            p, n = wr(sub, "_b_" + c)
            cells.append("%11s" % (fmt(p).strip() if p is not None else "n/a"))
        line("  %-6s(N%4d)%s" % (nm, sub.height, "".join(cells)))


def momentum_confound(df, label, win="hold15"):
    """Is the lead-class win-rate ordering just momentum (c_trend) or vol_pct?"""
    line("\n=== %s — momentum/vol confound (lead-class within c_trend tercile) ===" % label)
    tv = df["c_trend"].to_numpy()
    tq1 = float(pl.Series(tv).quantile(0.5))
    for trend_hi in (False, True):
        seg = df.filter((pl.col("c_trend") > tq1) == trend_hi)
        p, n = wr(seg, win)
        line("  c_trend %s (>%2.0f): N=%5d  %s" % ("HIGH" if trend_hi else "low", tq1, n, fmt(p)))
        for nm in ["MACD", "RSI", "BB", "TREND", "STOCH", "TA"]:
            sub = seg.filter(pl.col("lead") == nm)
            pp, nn = wr(sub, win)
            if nn >= 20:
                line("      lead=%-6s N=%5d  %s" % (nm, nn, fmt(pp)))


def main():
    if not os.path.exists(LEDGER):
        print("ledger missing:", LEDGER); sys.exit(1)
    df = pl.read_parquet(LEDGER)
    n0 = df.height
    df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))
    line("# Stage A — component-combination win/loss on v70 75+ CALL signals (funded APEX barrier)")
    line("(filtered %d -> %d rows on finite/sane vol_pct)" % (n0, df.height))
    line("ledger: %s   rows=%d   dates %s..%s" %
         (LEDGER, df.height, df["date"].min(), df["date"].max()))
    line("PRIMARY win label = apex15 (funded APEX TP30/SL70/15d, fixed thresholds; provisional —")
    line("  knobs being retuned, but lead-class ORDERING is barrier-robust, see robustness table).")
    line("Secondary: opt15 (growth-gate scaled barrier), gen15 (dashboard), hold15 (prior proxy).")
    line("NOTE win% is the apex TP-before-SL touch rate; funded EV also depends on day-15 expire")
    line("  exits (not -70%), so true EV > what the raw win% vs 70% break-even implies.")

    df = standard_labels(df)   # apex15 (funded), opt15, gen15, hold15, apex30
    df = lead_class(df)
    band_baselines(df, "ALL")

    p75 = df.filter(pl.col("overall") >= 75)
    p7074 = df.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))
    p7579 = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
    p8084 = df.filter((pl.col("overall") >= 80) & (pl.col("overall") <= 84))
    p85 = df.filter(pl.col("overall") >= 85)

    WIN = "apex15"   # funded Apex TP30/SL70 label (primary)

    # ---- The funded pool ----
    lead_table(p75, "75+ POOL", WIN)
    lead_table(p75, "75+ POOL", "opt15")   # growth-gate barrier for contrast
    tercile_table(p75, "75+ POOL", WIN)
    robustness_table(p75, "75+ POOL")
    pair_matrix(p75, "75+ POOL", WIN)
    volume_table(p75, "75+ POOL", WIN)
    momentum_confound(p75, "75+ POOL", WIN)

    # ---- Within-band (control for score level) ----
    for nm, seg in [("75-79", p7579), ("80-84", p8084), ("85+", p85)]:
        lead_table(seg, "BAND %s" % nm, WIN)
        tercile_table(seg, "BAND %s" % nm, WIN)

    # ---- THE MARGINAL CHECK: promotion reservoir (70-74) + demotion band (75-79) ----
    line("\n\n##### MARGINAL / REWEIGHT-BOUNDARY INTEGRITY #####")
    line("Reweighting moves 70-74 winners UP into 75+ and 75-79 losers DOWN out of it.")
    line("The reweight thesis holds ONLY if winning lead-classes win at BOTH bands.")
    lead_table(p7074, "PROMOTION RESERVOIR 70-74", WIN)
    tercile_table(p7074, "PROMOTION RESERVOIR 70-74", WIN)
    lead_table(p7579, "DEMOTION-PRONE 75-79", WIN)
    tercile_table(p7579, "DEMOTION-PRONE 75-79", WIN)

    out_md = os.path.join(_HERE, "FINDINGS_STAGE_A.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\nwrote", out_md)


if __name__ == "__main__":
    main()
