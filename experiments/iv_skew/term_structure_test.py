"""Term-structure slope + OI-imbalance test — do option-STRUCTURE signals add
predictive power on forward 15d ATM-call option P&L, ORTHOGONALLY to the confirmed
opt_skew (OSK) edge, on the 1.3y in-sample panel?

Reads only local parquets (no MySQL): the option slice + the labels/controls ledger.
Mirrors how OSK was confirmed: univariate quintile WR + spearman, then the decisive
orthogonalized OLS controlling for opt_skew/semivol_r/score/recent-return, then
sub-period sign-stability, then a persist-vs-crash interaction probe.

    set PYTHONUTF8=1 && python experiments/iv_skew/term_structure_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import spearmanr

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from experiments._holdout import assert_no_holdout_leak  # noqa: E402

SLICE = _ROOT / ".cache" / "experiment_data" / "option_slice_ivledger.parquet"
PROXY = _ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet"
OUT = _ROOT / ".cache" / "iv_skew" / "term_structure_results.txt"

_LINES: list[str] = []
def log(s=""):
    print(s, flush=True)
    _LINES.append(str(s))


# ---- feature extraction from the raw option slice -------------------------- #
def atm_call_iv_at(slice_df: pl.DataFrame, target_dte: int) -> pl.DataFrame:
    c = slice_df.filter(
        (pl.col("option_type") == "call") & pl.col("iv").is_not_null()
        & (pl.col("iv") > 0.03) & (pl.col("iv") < 5.0)
    ).with_columns([
        (pl.col("moneyness") - 1.0).abs().alias("_m"),
        (pl.col("dte") - target_dte).abs().alias("_dd"),
    ]).sort(["symbol", "date", "_dd", "_m"])
    return c.group_by(["symbol", "date"]).first().select(["symbol", "date", "iv", "dte"])


def build_ts_slope(slice_df: pl.DataFrame) -> pl.DataFrame:
    near = atm_call_iv_at(slice_df, 30).rename({"iv": "near_iv", "dte": "near_dte"})
    far = atm_call_iv_at(slice_df, 65).rename({"iv": "far_iv", "dte": "far_dte"})
    ts = near.join(far, on=["symbol", "date"]).filter(pl.col("near_dte") != pl.col("far_dte"))
    return ts.with_columns((pl.col("near_iv") - pl.col("far_iv")).alias("ts_slope")).select(
        ["symbol", "date", "ts_slope"])


def build_oi_imb(slice_df: pl.DataFrame) -> pl.DataFrame:
    band = slice_df.filter(
        (pl.col("dte") >= 20) & (pl.col("dte") <= 45)
        & (pl.col("moneyness") >= 0.85) & (pl.col("moneyness") <= 1.15)
        & pl.col("open_interest").is_not_null()
    )
    agg = band.group_by(["symbol", "date", "option_type"]).agg(pl.col("open_interest").sum().alias("oi"))
    wide = agg.pivot(values="oi", index=["symbol", "date"], on="option_type")
    cols = wide.columns
    if "put" not in cols or "call" not in cols:
        return pl.DataFrame({"symbol": [], "date": [], "oi_imb": []})
    return wide.filter((pl.col("put") > 0) & (pl.col("call") > 0)).with_columns(
        (pl.col("put").log() - pl.col("call").log()).alias("oi_imb")
    ).select(["symbol", "date", "oi_imb"])


# ---- stats helpers --------------------------------------------------------- #
def _z(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / s if s > 1e-12 else x - x.mean()


def ols_t(y: np.ndarray, feat: np.ndarray, controls: dict[str, np.ndarray]):
    """Return {name:(beta,t)} for OLS y ~ const + feat + controls (regressors z-scored)."""
    names = ["const", "feat"] + list(controls)
    cols = [np.ones_like(y), _z(feat)] + [_z(v) for v in controls.values()]
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = len(y) - X.shape[1]
    s2 = (resid @ resid) / dof
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return {n: (float(b), float(bt)) for n, b, bt in zip(names, beta, beta / se)}


def quintiles(feat: np.ndarray, pnl: np.ndarray):
    order = np.argsort(feat)
    bins = np.array_split(order, 5)
    return [(float(feat[b].mean()), float((pnl[b] > 0).mean()), float(pnl[b].mean()), len(b)) for b in bins]


def evaluate(name: str, df: pl.DataFrame):
    """df has columns: feat, pnl15, opt_skew, semivol_r, overall, stock_r20, date."""
    d = df.drop_nulls(["feat", "pnl15", "opt_skew", "semivol_r", "overall", "stock_r20"])
    n = d.height
    feat = d["feat"].to_numpy(); pnl = d["pnl15"].to_numpy()
    log(f"\n{'='*70}\n{name}   N={n}\n{'='*70}")
    log(f"  dist: mean={feat.mean():+.4f} std={feat.std():.4f}")

    # 1) univariate
    rho, p = spearmanr(feat, pnl)
    log(f"  univariate spearman(feat,pnl15) = {rho:+.3f} (p={p:.3f})")
    log("  quintiles (feat_mean | win% | mean_pnl | n):")
    q = quintiles(feat, pnl)
    for i, (fm, wr, mp, nb) in enumerate(q):
        log(f"    Q{i+1}: {fm:+.4f} | {wr*100:4.1f}% | {mp:+.3f} | {nb}")
    log(f"  WR spread Q5-Q1 = {(q[-1][1]-q[0][1])*100:+.1f}pp")
    d75 = d.filter(pl.col("overall") >= 75)
    if d75.height >= 40:
        r75, _ = spearmanr(d75["feat"].to_numpy(), d75["pnl15"].to_numpy())
        log(f"  75+ cohort N={d75.height}  spearman={r75:+.3f}")

    # 2) orthogonalized (decisive)
    controls = {k: d[k].to_numpy() for k in ["opt_skew", "semivol_r", "overall", "stock_r20"]}
    res = ols_t(pnl, feat, controls)
    fb, ft = res["feat"]
    log(f"  ORTHOGONALIZED  pnl15 ~ feat + opt_skew + semivol_r + overall + stock_r20:")
    log(f"    feat: beta={fb:+.4f}  t={ft:+.2f}   (opt_skew t={res['opt_skew'][1]:+.2f}, "
        f"semivol_r t={res['semivol_r'][1]:+.2f})")

    # 3) sub-period sign-stability (3 date-thirds)
    d_sorted = d.sort("date")
    thirds = np.array_split(np.arange(d_sorted.height), 3)
    signs = []
    for j, idx in enumerate(thirds):
        sub = d_sorted[idx]
        rr, _ = spearmanr(sub["feat"].to_numpy(), sub["pnl15"].to_numpy())
        signs.append(np.sign(rr))
        log(f"  sub-period {j+1} (N={sub.height}): spearman={rr:+.3f}")
    stable = len(set(signs)) == 1
    log(f"  sign-stable across thirds: {stable}")

    # 4) persist-vs-crash interaction (feat effect in the down-r20 cohort)
    down = (d["stock_r20"].to_numpy() < 0).astype(float)
    inter = _z(feat) * down
    Xn = ["const", "feat", "feat_x_down", "opt_skew", "semivol_r", "overall", "stock_r20"]
    Xc = [np.ones_like(pnl), _z(feat), inter] + [_z(controls[k]) for k in ["opt_skew", "semivol_r", "overall", "stock_r20"]]
    X = np.column_stack(Xc)
    beta, *_ = np.linalg.lstsq(X, pnl, rcond=None)
    resid = pnl - X @ beta; dof = len(pnl) - X.shape[1]
    se = np.sqrt(np.diag((resid @ resid) / dof * np.linalg.inv(X.T @ X)))
    tmap = dict(zip(Xn, beta / se))
    log(f"  INTERACTION feat×down_r20: t={tmap['feat_x_down']:+.2f} (persist-vs-crash cohort)")

    # verdict
    at = abs(ft)
    verdict = "PASS" if (at >= 3 and stable) else ("WEAK" if (at >= 2 or abs(tmap['feat_x_down']) >= 3) else "NULL")
    log(f"  >>> VERDICT[{name}] = {verdict}  (orth t={ft:+.2f}, stable={stable}, "
        f"interaction t={tmap['feat_x_down']:+.2f})")
    return {"name": name, "N": n, "orth_t": ft, "stable": stable,
            "inter_t": tmap["feat_x_down"], "verdict": verdict}


def main() -> int:
    sl = pl.read_parquet(SLICE)
    lab = pl.read_parquet(PROXY).filter(pl.col("opt_skew").is_not_null()).select(
        ["symbol", "date", "overall", "opt_skew", "semivol_r", "stock_r20", "pnl15"])
    assert_no_holdout_leak(lab.select("date"), "term_structure_test")
    log(f"labels N={lab.height}  (opt_skew baseline: spearman(opt_skew,pnl15)="
        f"{spearmanr(lab['opt_skew'].to_numpy(), lab['pnl15'].to_numpy())[0]:+.3f})")

    summaries = []
    for name, feat_df in [("ts_slope (IV term-structure)", build_ts_slope(sl)),
                          ("oi_imb (put/call OI imbalance)", build_oi_imb(sl))]:
        joined = feat_df.rename({feat_df.columns[-1]: "feat"}).join(lab, on=["symbol", "date"], how="inner")
        summaries.append(evaluate(name, joined))

    log(f"\n{'#'*70}\nSUMMARY (per-trade only — single selloff regime, no ship claim)\n{'#'*70}")
    for s in summaries:
        log(f"  {s['name']:34s} N={s['N']:4d}  orth_t={s['orth_t']:+.2f}  "
            f"stable={str(s['stable']):5s}  inter_t={s['inter_t']:+.2f}  -> {s['verdict']}")
    OUT.write_text("\n".join(_LINES), encoding="utf-8")
    log(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
