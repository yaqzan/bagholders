"""Derive win/loss labels from the barrier-agnostic forward-path ledger for ANY
(TP_sigma, SL_sigma, max_hold_W) — so changing the apex/sentinel/core knobs needs NO rebuild.

The ledger stores, per signal, t_up[g] / t_dn[g] = first calendar-day the cumulative-max
favorable / adverse excursion reached g sigma (999 = untouched within MAXW), plus fwd_caldays.

win  = 1  if t_up[TP] <= W and t_up[TP] < t_dn[SL]                 (TP touched first, in time)
     = 0  if t_dn[SL] <= W and t_dn[SL] <= t_up[TP]               (SL touched first or same bar)
     = 0  if neither touched but fwd_caldays >= W                  (day-W expire = no-TP = loss)
     = None if neither touched and fwd_caldays < W                 (unresolved; too recent)

Same-bar tie => loss (conservative). FIXED sigma thresholds (matches monte_carlo.py exits).

Reproduce established barriers by choosing sigma:
  funded APEX TP30/SL70/15d   -> derive(df, 1.092, 2.548, 15)
  growth-gate opt (30dte_opt scaled to 15d: 1.274/1.092 * sqrt(15/30)) -> derive(df, 0.901, 0.772, 15)
  dashboard generic WR15 (2/5 scaled to 15d)                           -> derive(df, 1.414, 3.536, 15)
  prior holdTP15 (1.274/10 scaled to 15d)                              -> derive(df, 0.901, 7.07, 15)
"""
import numpy as np
import polars as pl

UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)


def _idx(grid, target):
    return int(np.clip(int(np.argmin(np.abs(grid - float(target)))), 0, len(grid) - 1))


def win_expr(tp_sigma, sl_sigma, W):
    iu = _idx(UP_GRID, tp_sigma)
    idn = _idx(DN_GRID, sl_sigma)
    ttp = pl.col("t_up").list.get(iu)
    tsl = pl.col("t_dn").list.get(idn)
    fc = pl.col("fwd_caldays")
    return (
        pl.when((ttp <= W) & (ttp < tsl)).then(pl.lit(1))
        .when((tsl <= W) & (tsl <= ttp)).then(pl.lit(0))
        .when(fc >= W).then(pl.lit(0))
        .otherwise(pl.lit(None))
        .cast(pl.Int8)
    )


def add_label(df, tp_sigma, sl_sigma, W, name):
    return df.with_columns(win_expr(tp_sigma, sl_sigma, W).alias(name))


def derive(df, tp_sigma, sl_sigma, W):
    return df.select(win_expr(tp_sigma, sl_sigma, W).alias("win")).to_series()


# canonical label set used across analyze/resim (current best-guess until knobs land)
def standard_labels(df):
    df = add_label(df, 1.092, 2.548, 15, "apex15")     # funded APEX TP30/SL70/15d (PRIMARY)
    df = add_label(df, 1.092, 2.548, 30, "apex30")     # if max-hold extends to ~30
    df = add_label(df, 0.901, 0.772, 15, "opt15")      # growth-gate barrier (scaled opt @15d)
    df = add_label(df, 1.414, 3.536, 15, "gen15")      # dashboard generic WR15 (scaled @15d)
    df = add_label(df, 0.901, 7.07, 15, "hold15")      # prior funded proxy (1.274/10 scaled @15d)
    return df
