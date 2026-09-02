"""
DD-EPISODE-ONSET omen mine (2026-06-10 /research, v71 substrate).

User hypothesis: "isolate the periods of major drawdowns and look back at what the market
was doing leading up to them — e.g. a Hindenburg-omen-style market shape that raises the
likelihood of a drawdown."

Prior nulls constrain this hard (G23: NH/NL level failed the orthogonal slice; G26: off-year
DD is a diffuse momentum reversal; G19: crash cohorts are mean-reversion winners). The
genuinely UNTESTED objects here:
  1. DISCRETE composite events (Hindenburg omen/confirmed, Zweig thrust, breadth DIVERGENCE
     = index near highs while internals deteriorate) — prior mines used continuous levels.
  2. EPISODE-ONSET framing: profile the 5-20d LOOKBACK before major portfolio-DD episode
     onsets (episodes parquet start_date), i.e. LEADING indicators, vs entry-date context.
  3. Fresh v71 substrate (retune shipped 2026-06-10; all prior tapes were v70).

Parts:
  A. canonical onsets — cluster episode start_dates across seeds.
  B. pre-onset lookback profile — feature percentiles in bands [-20,-11], [-10,-6], [-5,-1]
     vs baseline distribution.
  C. forward event study — E[onset density over next K days | event] vs base, per event flag.
  D. per-trade shippability — cohort EV (loser-rate lift/z, dd_conc, mean_pnl) for entries
     during event-active windows; per-year sign-stability (G26); all-levers-off orthogonal
     slice (G21/G23: vix<20 & |mcc|>30 & breadth>=40 & TRIN outside 1.0-1.3); DD-active subset.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/dd_onset_omens/mine_onset.py
"""
import os, sys, math, json
from datetime import timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "dd_onset_omens"
OUT.mkdir(parents=True, exist_ok=True)

# Only the FRESH v71 tapes (older tape_2021..tape_2025/tape_10y are v70-scored).
TAPE_LABELS = ["2020", "5y"]
MIN_N = 200
LOWEV = 0.045
DD_FILTER = float(os.environ.get("DD_FILTER", "0"))
ONSET_CLUSTER_D = 2      # +/- days for seed-onset clustering
CANON_FRAC = 0.25        # >= this fraction of seeds onset within window => canonical onset
SOFT_FRAC = 0.10


def _norm_date(df, col):
    if col not in df.columns:
        return df
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def fnum(x):
    return float(x) if x is not None else None


# ---------------------------------------------------------------- context grid
def load_context():
    from database.models.core import MarketBreadth, MarketRegime
    from database.models.technical import PriceHistory

    rows = list(MarketBreadth.select(
        MarketBreadth.date, MarketBreadth.breadth_score, MarketBreadth.mcclellan_oscillator,
        MarketBreadth.mcclellan_summation, MarketBreadth.pct_above_ema50,
        MarketBreadth.pct_above_ema200, MarketBreadth.trin, MarketBreadth.ad_line,
        MarketBreadth.new_highs_52w, MarketBreadth.new_lows_52w, MarketBreadth.total_issues,
        MarketBreadth.zweig_thrust_active, MarketBreadth.zweig_boost,
        MarketBreadth.hindenburg_omen, MarketBreadth.hindenburg_confirmed,
        MarketBreadth.hindenburg_penalty,
    ).order_by(MarketBreadth.date))
    B = pl.DataFrame({
        "date":    [r.date for r in rows],
        "breadth": [fnum(r.breadth_score) for r in rows],
        "mcc":     [fnum(r.mcclellan_oscillator) for r in rows],
        "mcc_sum": [fnum(r.mcclellan_summation) for r in rows],
        "pe50":    [fnum(r.pct_above_ema50) for r in rows],
        "pe200":   [fnum(r.pct_above_ema200) for r in rows],
        "trin":    [fnum(r.trin) for r in rows],
        "adline":  [fnum(r.ad_line) for r in rows],
        "nh":      [r.new_highs_52w for r in rows],
        "nl":      [r.new_lows_52w for r in rows],
        "issues":  [r.total_issues for r in rows],
        "zweig":   [bool(r.zweig_thrust_active) for r in rows],
        "h_omen":  [bool(r.hindenburg_omen) for r in rows],
        "h_conf":  [bool(r.hindenburg_confirmed) for r in rows],
    })
    B = _norm_date(B, "date").sort("date")

    reg = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close).order_by(MarketRegime.date))
    R = pl.DataFrame({"date": [r.date for r in reg], "vix": [fnum(r.vix_close) for r in reg]})
    R = _norm_date(R, "date").sort("date")
    R = R.with_columns((pl.col("vix") - pl.col("vix").shift(5)).alias("vixvel5"))

    spy = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
               .where(PriceHistory.symbol == "SPY").order_by(PriceHistory.date))
    S = pl.DataFrame({"date": [r.date for r in spy], "spy": [fnum(r.close) for r in spy]})
    S = _norm_date(S, "date").sort("date")
    S = S.with_columns([
        (pl.col("spy") / pl.col("spy").rolling_max(window_size=60) - 1.0).alias("spy_from60h"),
        (pl.col("spy") / pl.col("spy").shift(10) - 1.0).alias("spy_ret10"),
    ])

    ctx = B.join(R, on="date", how="full", coalesce=True).join(S, on="date", how="full", coalesce=True).sort("date")
    ctx = ctx.filter(pl.col("breadth").is_not_null())  # breadth grid = the trading-day grid we mine on
    # derived features
    ctx = ctx.with_columns([
        (100.0 * pl.col("nh") / pl.col("issues")).alias("nh_pct"),
        (100.0 * pl.col("nl") / pl.col("issues")).alias("nl_pct"),
        (100.0 * pl.min_horizontal("nh", "nl") / pl.col("issues")).alias("churn_pct"),  # split-market core
        (pl.col("breadth") - pl.col("breadth").shift(10)).alias("brd_chg10"),
        (pl.col("mcc_sum") - pl.col("mcc_sum").shift(10)).alias("msum_chg10"),
        (pl.col("adline") - pl.col("adline").shift(10)).alias("ad_chg10"),
    ])
    # event flags (trailing-window where appropriate)
    ctx = ctx.with_columns([
        pl.col("h_omen").cast(pl.Int8).rolling_max(window_size=20).fill_null(0).cast(pl.Boolean).alias("omen20"),
        pl.col("h_conf").cast(pl.Int8).rolling_max(window_size=30).fill_null(0).cast(pl.Boolean).alias("conf30"),
        ((pl.col("spy_from60h") > -0.025) & (pl.col("brd_chg10") < -5)).fill_null(False).alias("div_brd"),
        ((pl.col("spy_from60h") > -0.025) & (pl.col("nl_pct") > 3.0)).fill_null(False).alias("div_nl"),
        ((pl.col("spy_from60h") > -0.025) & (pl.col("msum_chg10") < 0)).fill_null(False).alias("div_msum"),
        ((pl.col("churn_pct") >= 2.5)).fill_null(False).alias("churn_hi"),     # NH and NL BOTH elevated
        ((pl.col("nl_pct") >= 4.0) & (pl.col("nl_pct").shift(5) < pl.col("nl_pct"))).fill_null(False).alias("nl_spike"),
    ])
    # divergence flags persisted 10d (an omen "stays in effect")
    for c in ["div_brd", "div_nl", "div_msum", "churn_hi", "nl_spike"]:
        ctx = ctx.with_columns(pl.col(c).cast(pl.Int8).rolling_max(window_size=10).fill_null(0).cast(pl.Boolean).alias(c + "10"))
    print(f"   context grid: {ctx.height} days  ({ctx['date'].min()} .. {ctx['date'].max()})")
    return ctx


# ---------------------------------------------------------------- episodes -> onsets
def load_onsets():
    frames = []
    for lab in TAPE_LABELS:
        f = TAPE_DIR / f"episodes_{lab}.parquet"
        if not f.exists():
            print(f"   !! missing {f.name}")
            continue
        df = pl.read_parquet(f).with_columns(pl.lit(lab).alias("window"))
        frames.append(df)
        print(f"   loaded {f.name}: {df.height} episodes, {df['seed'].n_unique()} seeds")
    E = pl.concat(frames, how="diagonal_relaxed")
    E = _norm_date(E, "start_date")
    E = _norm_date(E, "trough_date")
    return E


def onset_density(E, ctx, min_mag=0.30):
    """Per calendar day: fraction of seeds (per window) with a major episode starting within +/-ONSET_CLUSTER_D."""
    grid = ctx.select("date").sort("date")
    out = {}
    for lab in TAPE_LABELS:
        sub = E.filter((pl.col("window") == lab) & (pl.col("magnitude") >= min_mag))
        if sub.height == 0:
            continue
        n_seeds = E.filter(pl.col("window") == lab)["seed"].n_unique()
        starts = sub.select("seed", "start_date").unique()
        dens = []
        sd = starts.to_dicts()
        for d in grid["date"].to_list():
            lo, hi = d - timedelta(days=ONSET_CLUSTER_D), d + timedelta(days=ONSET_CLUSTER_D)
            seeds = {r["seed"] for r in sd if r["start_date"] is not None and lo <= r["start_date"] <= hi}
            dens.append(len(seeds) / n_seeds)
        out[lab] = pl.DataFrame({"date": grid["date"], f"onset_frac_{lab}": dens})
    D = grid
    for lab, df in out.items():
        D = D.join(df, on="date", how="left")
    cols = [c for c in D.columns if c.startswith("onset_frac")]
    D = D.with_columns(pl.max_horizontal(cols).fill_null(0.0).alias("onset_frac"))
    return D


def canonical_onsets(D, frac):
    """Local-max onset dates with onset_frac >= frac, deduped within 7 days."""
    rows = D.filter(pl.col("onset_frac") >= frac).sort("date").to_dicts()
    canon = []
    for r in rows:
        if canon and (r["date"] - canon[-1]["date"]).days <= 7:
            if r["onset_frac"] > canon[-1]["onset_frac"]:
                canon[-1] = r
        else:
            canon.append(r)
    return canon


# ---------------------------------------------------------------- Part B: lookback profile
PROFILE_FEATS = ["breadth", "mcc", "mcc_sum", "msum_chg10", "pe50", "trin", "vix", "vixvel5",
                 "nh_pct", "nl_pct", "churn_pct", "brd_chg10", "ad_chg10", "spy_from60h", "spy_ret10"]
BANDS = [(-20, -11), (-10, -6), (-5, -1)]


def pct_rank(series, val):
    s = [x for x in series if x is not None]
    if not s or val is None:
        return None
    return round(100.0 * sum(1 for x in s if x <= val) / len(s), 1)


def lookback_profile(ctx, onsets):
    dates = ctx["date"].to_list()
    idx = {d: i for i, d in enumerate(dates)}
    data = {f: ctx[f].to_list() for f in PROFILE_FEATS}
    # baseline distribution of band-means (rolling means with same widths)
    base = {}
    for (a, b) in BANDS:
        w = b - a + 1
        for f in PROFILE_FEATS:
            vals = data[f]
            roll = []
            for i in range(len(vals)):
                seg = [v for v in vals[max(0, i - w + 1):i + 1] if v is not None]
                roll.append(sum(seg) / len(seg) if seg else None)
            base[(f, (a, b))] = roll

    print("\n### PART B -- pre-onset lookback profile (percentile of band-mean vs all-days baseline)")
    table = {}
    for (a, b) in BANDS:
        w = b - a + 1
        print(f"\n  band [{a},{b}]d before onset:")
        hdr = f"  {'feature':12s}" + "".join(f" {('o'+str(k+1)):>6s}" for k in range(len(onsets))) + "   med"
        print(hdr)
        for f in PROFILE_FEATS:
            pcts = []
            for o in onsets:
                i = idx.get(o["date"])
                if i is None:
                    pcts.append(None); continue
                lo, hi = i + a, i + b
                if lo < 0:
                    pcts.append(None); continue
                seg = [v for v in data[f][lo:hi + 1] if v is not None]
                if not seg:
                    pcts.append(None); continue
                m = sum(seg) / len(seg)
                pcts.append(pct_rank(base[(f, (a, b))], m))
            ok = [p for p in pcts if p is not None]
            med = round(sorted(ok)[len(ok) // 2], 1) if ok else None
            table[f"{f}|{a}..{b}"] = {"pcts": pcts, "median": med}
            cells = "".join(f" {('--' if p is None else f'{p:.0f}'):>6s}" for p in pcts)
            print(f"  {f:12s}{cells}   {med}")
    return table


# ---------------------------------------------------------------- Part C: forward event study
EVENT_FLAGS = ["h_omen", "omen20", "h_conf", "conf30", "zweig",
               "div_brd10", "div_nl10", "div_msum10", "churn_hi10", "nl_spike10"]


def event_study(ctx, D, ks=(10, 20)):
    M = ctx.join(D.select("date", "onset_frac"), on="date", how="left").sort("date")
    of = M["onset_frac"].fill_null(0.0).to_list()
    n = len(of)
    fwd = {}
    for K in ks:
        fwd[K] = [max(of[i + 1:min(n, i + 1 + K)]) if i + 1 < n else None for i in range(n)]
    print("\n### PART C -- forward event study: E[max onset_frac in next K days | event] vs base")
    res = {}
    for K in ks:
        col = fwd[K]
        valid = [(i, v) for i, v in enumerate(col) if v is not None]
        base = sum(v for _, v in valid) / len(valid)
        base_hit = sum(1 for _, v in valid if v >= CANON_FRAC) / len(valid)
        print(f"\n  K={K}d   base E[fwd_onset]={base:.4f}   base P(onset>={CANON_FRAC})={base_hit:.4f}")
        print(f"  {'event':12s} {'n_days':>7s} {'E[fwd]':>8s} {'lift':>6s} {'P(maj)':>7s} {'plift':>6s}")
        res[K] = {}
        for ev in EVENT_FLAGS:
            flags = M[ev].fill_null(False).to_list()
            sel = [v for (i, v) in valid if flags[i]]
            if len(sel) < 5:
                print(f"  {ev:12s} {len(sel):>7d}   (too few)")
                res[K][ev] = {"n": len(sel)}
                continue
            m = sum(sel) / len(sel)
            hit = sum(1 for v in sel if v >= CANON_FRAC) / len(sel)
            res[K][ev] = {"n": len(sel), "e_fwd": round(m, 4), "lift": round(m / base, 2) if base else None,
                          "p_major": round(hit, 4), "p_lift": round(hit / base_hit, 2) if base_hit else None}
            print(f"  {ev:12s} {len(sel):>7d} {m:>8.4f} {m/base:>6.2f} {hit:>7.4f} {(hit/base_hit if base_hit else 0):>6.2f}")
    return res


# ---------------------------------------------------------------- Part D: per-trade shippability
def load_tapes():
    frames = []
    for lab in TAPE_LABELS:
        f = TAPE_DIR / f"tape_{lab}.parquet"
        if not f.exists():
            print(f"   !! missing tape_{lab}.parquet"); continue
        df = pl.read_parquet(f).with_columns(pl.lit(lab).alias("window"))
        frames.append(df)
        print(f"   loaded tape_{lab}.parquet rows={df.height}")
    T = pl.concat(frames, how="diagonal_relaxed")
    T = _norm_date(T, "entry_date")
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    T = T.with_columns(
        (pl.col("premium_cost").cast(pl.Float64) * pl.col("option_pnl").cast(pl.Float64)).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
        pl.col("entry_date").dt.year().alias("yr"),
    )
    return T


def cohort(T, axes, base, title, topn=20):
    g = T.group_by(axes).agg(
        pl.len().alias("n"), pl.col("loser").mean().alias("rate"),
        pl.col("pnl_dollars").filter(pl.col("loser")).abs().sum().alias("loss_usd"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
    ).filter(pl.col("n") >= MIN_N)
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = T.height
    rows = []
    for r in g.iter_rows(named=True):
        n, rate = r["n"], r["rate"]
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if n else 0.0
        conc = ((r["loss_usd"] or 0.0) / total_loss) / (n / total_n) if n else 0.0
        rows.append({"cohort": " & ".join(str(r[a]) for a in axes), "n": n, "rate": round(rate, 4),
                     "z": round(z, 1), "dd_conc": round(conc, 2),
                     "dd_share": round((r["loss_usd"] or 0.0) / total_loss, 4),
                     "mean_pnl": round(r["mean_pnl"], 4)})
    rows.sort(key=lambda x: x["cohort"])
    print(f"\n### {title}  (base loser-rate={base:.4f}, N={total_n})")
    print(f"  {'cohort':22s} {'n':>9s} {'rate':>6s} {'z':>7s} {'conc':>6s} {'ddshr':>6s} {'mpnl':>7s}")
    for r in rows[:topn]:
        flag = "  <-LOW-EV" if r["mean_pnl"] < LOWEV else ""
        print(f"  {r['cohort']:22s} {r['n']:>9d} {r['rate']:>6.3f} {r['z']:>7.1f} "
              f"{r['dd_conc']:>6.2f} {r['dd_share']:>6.3f} {r['mean_pnl']:>7.3f}{flag}")
    return rows


def per_year(T, flagcol, title):
    print(f"\n### per-year sign-stability: {flagcol}  ({title})")
    out = {}
    for yr in sorted(T["yr"].unique().to_list()):
        sub = T.filter(pl.col("yr") == yr)
        on = sub.filter(pl.col(flagcol))
        off = sub.filter(~pl.col(flagcol))
        if on.height < 50 or off.height < 50:
            print(f"  {yr}: n_on={on.height} (skip)"); continue
        d = on["option_pnl"].mean() - off["option_pnl"].mean()
        out[int(yr)] = {"n_on": on.height, "mpnl_on": round(on["option_pnl"].mean(), 4),
                        "mpnl_off": round(off["option_pnl"].mean(), 4), "delta": round(d, 4)}
        print(f"  {yr}: n_on={on.height:6d}  mpnl_on={on['option_pnl'].mean():+.4f}  "
              f"mpnl_off={off['option_pnl'].mean():+.4f}  delta={d:+.4f}")
    return out


def main():
    print("== PART A: episodes -> canonical onsets ==")
    ctx = load_context()
    E = load_onsets()
    D = onset_density(E, ctx)
    canon = canonical_onsets(D, CANON_FRAC)
    soft = canonical_onsets(D, SOFT_FRAC)
    print(f"\n  canonical onsets (>= {CANON_FRAC:.0%} of seeds):")
    for o in canon:
        print(f"    {o['date']}  onset_frac={o['onset_frac']:.2f}")
    print(f"  soft onsets (>= {SOFT_FRAC:.0%}): {len(soft)} dates")

    report = {"canonical_onsets": [{"date": str(o["date"]), "frac": o["onset_frac"]} for o in canon],
              "n_soft_onsets": len(soft)}

    report["lookback"] = lookback_profile(ctx, canon)
    report["event_study"] = event_study(ctx, D)

    print("\n== PART D: per-trade shippability on the v71 tape ==")
    T = load_tapes()
    ctxr = ctx.rename({"date": "entry_date"})
    T = T.sort("entry_date").join_asof(ctxr, on="entry_date", strategy="backward")
    if DD_FILTER > 0:
        n0 = T.height
        T = T.filter(pl.col("entry_dd") >= DD_FILTER)
        print(f"   DD_FILTER={DD_FILTER}: kept {T.height}/{n0}")
    base = T["loser"].mean()
    report["base_loser_rate"] = round(base, 4)
    report["base_mpnl"] = round(T["option_pnl"].mean(), 4)
    print(f"   base mean_pnl={T['option_pnl'].mean():+.4f}")

    report["cohorts"] = {}
    for ev in EVENT_FLAGS:
        T = T.with_columns(pl.col(ev).fill_null(False))
        report["cohorts"][ev] = cohort(T, [ev], base, f"event-state: {ev}")

    # per-year sign stability on the flags that matter most a-priori
    report["per_year"] = {}
    for ev in ["omen20", "conf30", "div_brd10", "div_nl10", "churn_hi10", "nl_spike10", "div_msum10"]:
        report["per_year"][ev] = per_year(T, ev, "mpnl delta on vs off")

    # orthogonal slice: all shipped levers ~off (RXDD vix<20; MWDD |mcc|>30; F3F breadth>=40;
    # TVDD trin outside 1.0-1.3). VIX null rows excluded (pre-2021-04).
    print("\n== ORTHOGONAL SLICE (vix<20 & |mcc|>30 & breadth>=40 & trin outside [1.0,1.3]) ==")
    O = T.filter((pl.col("vix") < 20) & (pl.col("mcc").abs() > 30) & (pl.col("breadth") >= 40)
                 & ((pl.col("trin") < 1.0) | (pl.col("trin") > 1.3)))
    report["ortho"] = {}
    if O.height >= MIN_N:
        ob = O["loser"].mean()
        print(f"   slice n={O.height} ({100*O.height/T.height:.1f}%)  base rate={ob:.4f}  mpnl={O['option_pnl'].mean():+.4f}")
        for ev in ["omen20", "conf30", "div_brd10", "div_nl10", "churn_hi10", "nl_spike10", "div_msum10"]:
            report["ortho"][ev] = cohort(O, [ev], ob, f"ORTHO {ev}")
    else:
        print("   slice too small")

    # DD-active subset (G21)
    print("\n== DD-ACTIVE SUBSET (entry_dd >= 0.13) ==")
    A = T.filter(pl.col("entry_dd") >= 0.13)
    report["dd_active"] = {}
    if A.height >= MIN_N:
        ab = A["loser"].mean()
        print(f"   subset n={A.height} ({100*A.height/T.height:.1f}%)  base rate={ab:.4f}  mpnl={A['option_pnl'].mean():+.4f}")
        for ev in ["omen20", "conf30", "div_brd10", "div_nl10", "churn_hi10", "nl_spike10", "div_msum10"]:
            report["dd_active"][ev] = cohort(A, [ev], ab, f"DD-ACTIVE {ev}")

    (OUT / "mine_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
