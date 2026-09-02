#!/usr/bin/env python3
"""Build the annotated 10y regime/environment dataset for the Apex sleeve.

Produces ONE JSON at experiments/component_reweight/regime_env_dataset.json with:
  (1) Apex + overflow(0.035) DETERMINISTIC backtest over full honest-v70 history:
        daily {date, equity, dd} series + per-trade records.
  (2) Per-date market environment from MySQL: MarketRegime + MarketBreadth, 2016..2026.
  (3) days_held distribution from the ledger at the APEX barrier for W=7/10/15, plus
        the W=15 'reached day 15' cohort outcome split (heading to TP/SL/flat at day 7).

Live ASYMMETRIC cost canon: entry & limit-TP free, only forced exits (SL/hard) pay -0.015.
That is the active strategy_config default — we do NOT override slippage, so the engine
runs the live cost model out of the box. Overflow(70-74)=0.035 via TIER_OVERFLOW_OV env.

Usage:
  PYTHONIOENCODING=utf-8 python experiments/component_reweight/build_regime_env_dataset.py
"""
import json
import os
import sys
from collections import Counter
from datetime import date

import numpy as np
import polars as pl

ROOT = r"C:/Development/Trader"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "component_reweight"))

LEDGER = os.path.join(ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
OUT = os.path.join(ROOT, "experiments", "component_reweight", "regime_env_dataset.json")

# APEX barrier
TP_SIGMA = 1.092
SL_SIGMA = 2.548


def _r(x, n=6):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, n)


# ---------------------------------------------------------------------------
# (1) Deterministic Apex + overflow(0.035) backtest -> daily equity/dd + trades
# ---------------------------------------------------------------------------
def run_apex_backtest():
    # Set overflow BEFORE importing backtest_cascade so module-level constants pick it up.
    os.environ["TIER_OVERFLOW_OV"] = os.environ.get("TIER_OVERFLOW_OV", "0.035")
    import strategy_config as sc
    import backtest_cascade as bc
    from database.models.core import AlgorithmVersion

    # NOTE: backtest_cascade reads TIER_ALLOC from strategy_config at import; the
    # overflow knob is exposed via env in the cfg the engine builds per run. We
    # construct an explicit tier_alloc with overflow=0.035 so it is unambiguous.
    cfg = sc.STRATEGY_30DTE
    overflow = float(os.environ["TIER_OVERFLOW_OV"])
    tier_alloc = dict(bc.TIER_ALLOC)
    tier_alloc["70-74"] = overflow

    av = AlgorithmVersion.get_active_scores_version()
    version_id = av.id

    run_cfg = {
        "tier_alloc": tier_alloc,
        # everything else (caps, breadth alloc, dd-band, regime slopes, practical
        # exposure, hold days) is sourced from strategy_config defaults inside the
        # engine. Apex is uncapped (PRACTICAL_CAPITAL_CEILING=0) and calls-only.
    }

    result = bc.run_cascade_backtest(
        version_id,
        min_score=70.0,          # 70-74 overflow tier participates (alloc 0.035)
        from_date=None,          # full honest-v70 history (engine MIN_DATE 2016-01-01)
        to_date=None,
        initial=50_000.0,
        verbose=True,
        calls_only=True,         # Apex = CALLS ONLY, puts OFF
        cfg=run_cfg,
    )
    if not result:
        raise RuntimeError("backtest returned empty result")

    # Daily equity + running drawdown (equity/peak - 1, i.e. <= 0)
    eq_curve = result["equity_curve"]  # [(date, equity), ...] sorted by date
    daily = []
    peak = float("-inf")
    for d, eq in eq_curve:
        eq = float(eq)
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1.0) if peak > 0 else 0.0
        daily.append({"date": d.isoformat(), "equity": round(eq, 2), "dd": round(dd, 6)})

    # Per-trade records (closed trades only; open holdings reported separately).
    trades = []
    for t in result["trade_log"]:
        trades.append({
            "entry_date": t["entry_date"].isoformat(),
            "exit_date": t["exit_date"].isoformat(),
            "symbol": t["symbol"],
            "score": _r(t["score"], 2),
            "tier": t["tier"],
            "days_held": int(t["hold_bars"]),     # trading bars held
            "outcome": t["outcome"],              # tp | sl | hard | realloc
            "pnl_pct": _r(t["pnl_pct"], 6),
            "stressed": bool(t["stressed"]),
        })

    open_holdings = []
    for h in result.get("open_holdings", []):
        open_holdings.append({
            "entry_date": h["entry_date"].isoformat(),
            "hard_sell_date": h["hard_sell_date"].isoformat() if h.get("hard_sell_date") else None,
            "symbol": h["symbol"],
            "score": _r(h["score"], 2),
            "tier": h["tier"],
        })

    summary = {
        "version_id": version_id,
        "version_commit": (av.git_message or "").splitlines()[0][:80] if av.git_message else None,
        "profile": "apex",
        "overflow_alloc": overflow,
        "calls_only": True,
        "cost_model": "asymmetric_live (entry/TP free, SL/hard -0.015)",
        "tier_alloc": tier_alloc,
        "tp_base": cfg.option.TP_BASE, "sl_base": cfg.option.SL_BASE,
        "hold_days": cfg.HOLD_DAYS,
        "max_positions": cfg.MAX_POSITIONS, "max_positions_call": cfg.MAX_POSITIONS_CALL,
        "gross_premium_cap": getattr(cfg, "GROSS_PREMIUM_CAP", None),
        "practical_capital_ceiling": getattr(cfg, "PRACTICAL_CAPITAL_CEILING", None),
        "initial": result["initial"],
        "final_equity": round(float(result["final_equity"]), 2),
        "total_return_pct": round((float(result["final_equity"]) / result["initial"] - 1.0) * 100.0, 2),
        "max_dd_cost": round(float(result["max_dd"]), 6),
        "max_dd_mtm": round(float(result.get("max_dd_mtm", 0.0)), 6),
        "start_date": result["start_date"].isoformat(),
        "end_date": result["end_date"].isoformat(),
        "n_trading_days": len(daily),
        "n_closed_trades": len(trades),
        "n_open_holdings": len(open_holdings),
        "outcome_counts": dict(Counter(t["outcome"] for t in trades)),
    }

    return {
        "summary": summary,
        "daily_equity": daily,
        "trades": trades,
        "open_holdings": open_holdings,
    }


# ---------------------------------------------------------------------------
# (2) Per-date market environment from MySQL
# ---------------------------------------------------------------------------
def load_market_environment():
    from database.models.core import MarketRegime, MarketBreadth
    lo = date(2016, 1, 1)
    hi = date(2026, 12, 31)

    env = {}
    reg_rows = (MarketRegime
                .select(MarketRegime.date, MarketRegime.regime_composite,
                        MarketRegime.regime_multiplier, MarketRegime.vix_close)
                .where((MarketRegime.date >= lo) & (MarketRegime.date <= hi))
                .order_by(MarketRegime.date)
                .tuples())
    for d, comp, mult, vix in reg_rows:
        env.setdefault(d.isoformat(), {})
        env[d.isoformat()].update({
            "regime_composite": _r(comp, 4),
            "regime_multiplier": _r(mult, 4),
            "vix_close": _r(vix, 4),
        })

    brd_rows = (MarketBreadth
                .select(MarketBreadth.date, MarketBreadth.breadth_score)
                .where((MarketBreadth.date >= lo) & (MarketBreadth.date <= hi))
                .order_by(MarketBreadth.date)
                .tuples())
    for d, bs in brd_rows:
        env.setdefault(d.isoformat(), {})
        env[d.isoformat()]["breadth_score"] = _r(bs, 4)

    meta = {
        "n_dates": len(env),
        "n_regime": sum(1 for v in env.values() if "regime_composite" in v),
        "n_breadth": sum(1 for v in env.values() if "breadth_score" in v),
        "date_min": min(env) if env else None,
        "date_max": max(env) if env else None,
    }
    return env, meta


# ---------------------------------------------------------------------------
# (3) days_held distribution at APEX barrier for W in {7,10,15} + outcome split
# ---------------------------------------------------------------------------
def days_held_analysis():
    from label import UP_GRID, DN_GRID, _idx
    df = pl.read_parquet(LEDGER)
    iu = _idx(UP_GRID, TP_SIGMA)
    idn = _idx(DN_GRID, SL_SIGMA)

    # Extract first-touch calendar days for the chosen TP/SL grid levels.
    # 999 = untouched within MAXW.
    sub = df.select([
        pl.col("t_up").list.get(iu).alias("ttp"),
        pl.col("t_dn").list.get(idn).alias("tsl"),
        pl.col("fwd_caldays").alias("fc"),
    ])
    ttp = sub["ttp"].to_numpy()
    tsl = sub["tsl"].to_numpy()
    fc = sub["fc"].to_numpy()
    n = len(ttp)

    out = {
        "barrier": {"tp_sigma_target": TP_SIGMA, "sl_sigma_target": SL_SIGMA,
                    "tp_grid_used": float(UP_GRID[iu]), "sl_grid_used": float(DN_GRID[idn]),
                    "iu": iu, "idn": idn},
        "n_signals_total": int(n),
        "windows": {},
    }

    for W in (7, 10, 15):
        # Resolved cohort: signal had enough forward history to evaluate at horizon W.
        # If neither barrier touched but fwd_caldays < W -> unresolved (too recent).
        touched = (ttp <= W) | (tsl <= W)
        resolved = touched | (fc >= W)
        n_resolved = int(resolved.sum())
        n_unresolved = int((~resolved).sum())

        # days_held = min(first TP touch, first SL touch, W). 999 collapses to W via min.
        dh = np.minimum(np.minimum(ttp, tsl), W).astype(np.int64)
        dh_res = dh[resolved]

        # histogram over 0..W (calendar days)
        hist = Counter(int(x) for x in dh_res)
        histogram = {str(k): int(hist.get(k, 0)) for k in range(0, W + 1)}

        # outcome label at horizon W for resolved cohort (TP-first / SL-first / flat-expire)
        tp_first = (ttp <= W) & (ttp < tsl)
        sl_first = (tsl <= W) & (tsl <= ttp)
        flat_expire = resolved & (~tp_first) & (~sl_first)  # reached W, neither barrier
        out_window = {
            "n_resolved": n_resolved,
            "n_unresolved": n_unresolved,
            "days_held_histogram": histogram,
            "days_held_mean": _r(float(dh_res.mean()) if n_resolved else None, 3),
            "days_held_median": _r(float(np.median(dh_res)) if n_resolved else None, 1),
            "frac_reached_W": _r(float((dh_res >= W).sum() / n_resolved) if n_resolved else None, 4),
            "outcome_split_at_W": {
                "tp_first": int((tp_first & resolved).sum()),
                "sl_first": int((sl_first & resolved).sum()),
                "flat_expire": int(flat_expire.sum()),
            },
        }
        out["windows"][f"W{W}"] = out_window

    # --- W=15 'reached day 15' cohort: eventual outcome + status at day 7 ---
    W = 15
    touched15 = (ttp <= W) | (tsl <= W)
    resolved15 = touched15 | (fc >= W)
    dh15 = np.minimum(np.minimum(ttp, tsl), W).astype(np.int64)
    # 'reached day 15' = held to the hard-sell deadline (neither barrier fired by day 15)
    reached15 = resolved15 & (dh15 >= W)
    n_reached = int(reached15.sum())

    # For that cohort, what was the EVENTUAL outcome (using full MAXW horizon, no W cap)?
    # ttp/tsl are first-touch within the ledger MAXW window (999 = never within MAXW).
    ev_tp = (ttp < tsl) & (ttp < 999)
    ev_sl = (tsl <= ttp) & (tsl < 999)
    ev_flat = (ttp >= 999) & (tsl >= 999)
    eventual = {
        "tp": int((reached15 & ev_tp).sum()),
        "sl": int((reached15 & ev_sl).sum()),
        "flat_never": int((reached15 & ev_flat).sum()),
    }

    # Status at day 7 for the reached-day-15 cohort: where was the path 'heading'
    # (which barrier does it eventually hit), still flat at day 7 by construction
    # (since it did not fire by day 15, it certainly had not fired by day 7).
    # We classify by the eventual direction (TP-bound vs SL-bound vs flat) — this
    # is the 'heading to TP vs SL vs flat at day 7' decomposition requested.
    heading_at_day7 = {
        "heading_tp": int((reached15 & ev_tp).sum()),
        "heading_sl": int((reached15 & ev_sl).sum()),
        "flat": int((reached15 & ev_flat).sum()),
        "note": ("Cohort = signals that held to the day-15 hard sell (neither the "
                 "1.092-sigma TP nor the 2.548-sigma SL fired by day 15). By "
                 "construction none had fired by day 7 either, so 'heading' = the "
                 "EVENTUAL barrier each path reaches within the ledger MAXW horizon; "
                 "'flat'/'flat_never' = neither barrier ever touched within MAXW."),
    }

    out["reached_day15_cohort"] = {
        "n_reached_day15": n_reached,
        "frac_of_resolved15": _r(n_reached / int(resolved15.sum()) if int(resolved15.sum()) else None, 4),
        "eventual_outcome": eventual,
        "eventual_outcome_pct": {
            k: _r(v / n_reached * 100.0 if n_reached else None, 2) for k, v in eventual.items()
        },
        "status_at_day7": heading_at_day7,
    }
    return out


def main():
    print("[1/3] Running Apex + overflow(0.035) deterministic backtest (full honest-v70 history)...")
    bt = run_apex_backtest()
    print(f"      final ${bt['summary']['final_equity']:,.0f}  ({bt['summary']['total_return_pct']:,.1f}%)  "
          f"maxDD(cost) {bt['summary']['max_dd_cost']*100:.1f}%  maxDD(mtm) {bt['summary']['max_dd_mtm']*100:.1f}%  "
          f"trades {bt['summary']['n_closed_trades']:,}  days {bt['summary']['n_trading_days']:,}")

    print("[2/3] Loading per-date market environment (MarketRegime + MarketBreadth)...")
    env, env_meta = load_market_environment()
    print(f"      {env_meta['n_dates']:,} dates  regime={env_meta['n_regime']:,}  breadth={env_meta['n_breadth']:,}")

    print("[3/3] days_held distribution at APEX barrier (W=7/10/15) + outcome split...")
    dh = days_held_analysis()
    for w in ("W7", "W10", "W15"):
        ww = dh["windows"][w]
        print(f"      {w}: resolved={ww['n_resolved']:,}  mean_held={ww['days_held_mean']}  "
              f"reachedW={ww['frac_reached_W']}  TP/SL/flat="
              f"{ww['outcome_split_at_W']['tp_first']}/{ww['outcome_split_at_W']['sl_first']}/{ww['outcome_split_at_W']['flat_expire']}")
    rc = dh["reached_day15_cohort"]
    print(f"      reached-day15 cohort N={rc['n_reached_day15']:,}  eventual TP/SL/flat%="
          f"{rc['eventual_outcome_pct']['tp']}/{rc['eventual_outcome_pct']['sl']}/{rc['eventual_outcome_pct']['flat_never']}")

    dataset = {
        "meta": {
            "generated": date.today().isoformat(),
            "description": ("Annotated 10y regime/environment dataset for the Apex sleeve "
                            "(honest-v70, 30 DTE ATM calls, TP+30/SL-70, HOLD-to-day-15, "
                            "cascade 20/15/10/10 + overflow 0.035, MaxPos14, gross cap 0.50, "
                            "uncapped, asymmetric live cost)."),
            "ledger": "ledger_v70_5y.parquet (despite name, spans 2016-05..2026-05)",
        },
        "backtest": bt,
        "market_environment_meta": env_meta,
        "market_environment": env,
        "days_held": dh,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dataset, f, separators=(",", ":"))
    sz = os.path.getsize(OUT) / 1e6
    print(f"\nWrote {OUT}  ({sz:.1f} MB)")


if __name__ == "__main__":
    main()
