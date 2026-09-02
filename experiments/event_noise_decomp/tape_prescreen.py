"""Stage-3 pre-screen: is the earnings-spanning (event-risk) cohort a DD lever?

Read-only mine over the fresh v74 dd_ledger tapes. ASCII-only output (cp1252 box).

The G21 recipe, applied to the one ex-ante event-risk feature the tape already
carries (`ern` = position spans an earnings event during the hold):
  1. EV by cohort on the WHOLE tape (expected to be diluted)
  2. EV by cohort on the DD-ACTIVE subset (entry_dd >= DD_MIN) -- where a
     DD-gated lever would actually act
  3. dd-dollar concentration by cohort (a Pareto lever needs low-EV AND
     high dd_conc to COINCIDE)
  4. per-window sign stability (G26: concentration is good in the trend and
     bad in the reversal -- most candidates die here)

No verdict is emitted here; the orchestrator adjudicates.
"""
import sys
import polars as pl

DD_MIN = 0.13          # the house DD-gate where RXDD/MWDD/TVDD act
WINDOWS = ["2021", "2022", "2023", "2024", "2025", "2020_crash", "5y"]


def load(label):
    p = f".cache/dd_ledger/tape_{label}.parquet"
    try:
        df = pl.read_parquet(p)
    except Exception as e:                       # noqa: BLE001
        print(f"  [skip] {label}: {e}")
        return None
    return df.filter(pl.col("side") == "call")


def cohort_stats(df, name):
    """mean option pnl, N, share, and dd-dollar concentration per ern cohort."""
    if df is None or df.height == 0:
        return []
    tot_n = df.height
    # dd-dollars: losses incurred while the book was already in drawdown
    df = df.with_columns(
        (pl.col("option_pnl") * pl.col("premium_cost")).alias("pnl_dollars")
    )
    loss_pool = df.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
    rows = []
    for ern_val in [0, 1]:
        sub = df.filter(pl.col("ern") == ern_val)
        if sub.height == 0:
            continue
        share = sub.height / tot_n
        mpnl = sub["option_pnl"].mean()
        wr = (sub["option_pnl"] > 0).mean()
        sub_loss = sub.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
        # dd_conc = share of loss dollars / share of trades. >1 = over-represented
        dd_conc = (sub_loss / loss_pool) / share if loss_pool and share else float("nan")
        rows.append(dict(scope=name, ern=ern_val, n=sub.height, share=share,
                         mpnl=mpnl, wr=wr, dd_conc=dd_conc))
    return rows


def fmt(rows):
    if not rows:
        return
    print(f"  {'scope':<16}{'ern':>4}{'N':>10}{'share':>8}"
          f"{'mean_pnl':>11}{'WR':>8}{'dd_conc':>9}")
    for r in rows:
        print(f"  {r['scope']:<16}{r['ern']:>4}{r['n']:>10}"
              f"{r['share']:>8.3f}{r['mpnl']:>11.4f}{r['wr']:>8.3f}"
              f"{r['dd_conc']:>9.3f}")


def main():
    print("=" * 78)
    print("EARNINGS-SPANNING (ex-ante event risk) -- Stage-3 pre-screen, v74 tape")
    print("=" * 78)

    for label in WINDOWS:
        df = load(label)
        if df is None:
            continue
        print(f"\n[{label}]  calls={df.height}")
        fmt(cohort_stats(df, "whole-tape"))
        ddact = df.filter(pl.col("entry_dd") >= DD_MIN)
        if ddact.height:
            fmt(cohort_stats(ddact, f"dd>={DD_MIN}"))
        else:
            print(f"  (no rows with entry_dd >= {DD_MIN})")

    # Orthogonality probe: does the cohort survive where a DD-gated lever is off?
    print("\n" + "=" * 78)
    print("ORTHOGONALITY: EV gap (ern=1 minus ern=0) by drawdown state, 5y")
    print("=" * 78)
    df = load("5y")
    if df is not None:
        for lo, hi in [(0.0, 0.05), (0.05, 0.13), (0.13, 0.25), (0.25, 1.01)]:
            sub = df.filter((pl.col("entry_dd") >= lo) & (pl.col("entry_dd") < hi))
            if sub.height < 500:
                continue
            a = sub.filter(pl.col("ern") == 1)["option_pnl"]
            b = sub.filter(pl.col("ern") == 0)["option_pnl"]
            if len(a) < 100 or len(b) < 100:
                continue
            print(f"  dd [{lo:.2f},{hi:.2f})  N_ern={len(a):>7} N_no={len(b):>7}  "
                  f"gap={a.mean() - b.mean():+.4f}  "
                  f"ern_mpnl={a.mean():+.4f}  base={b.mean():+.4f}")

    # Score-tier interaction (is any event risk concentrated in a tier?)
    print("\n" + "=" * 78)
    print("BY SCORE TIER, 5y: mean pnl and share of earnings-spanning entries")
    print("=" * 78)
    if df is not None:
        for tier in sorted(df["tier"].unique().to_list()):
            sub = df.filter(pl.col("tier") == tier)
            if sub.height < 500:
                continue
            a = sub.filter(pl.col("ern") == 1)["option_pnl"]
            b = sub.filter(pl.col("ern") == 0)["option_pnl"]
            if len(a) < 100 or len(b) < 100:
                continue
            print(f"  tier={tier!s:<8} N={sub.height:>7}  ern_share={len(a)/sub.height:.3f}  "
                  f"ern_mpnl={a.mean():+.4f}  base={b.mean():+.4f}  "
                  f"gap={a.mean() - b.mean():+.4f}")


if __name__ == "__main__":
    sys.exit(main())
