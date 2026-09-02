"""experiments/short_premium/portfolio_sim.py -- deterministic capital-
allocation replay of the short_ledger's REAL resolved trades (2022-08+, see
PREREGISTRATION.md "Portfolio phase"). This is the MEASURED half of the
portfolio study; crash_stress.py is the MODELED half (no real crash exists in
this data window).

WHAT THIS BUILDS
----------------
Reads (real mode, --run), all produced by the upstream parallel pull +
short_ledger.py --run:
  .cache/short_premium/contracts.parquet
  .cache/short_premium/paths.parquet
  .cache/short_premium/_unadj_daily.parquet
  .cache/short_premium/trades.parquet   -- primary haircut (0.90) only, per
                                            short_ledger.run()'s own write

For a selected (study_arm, target_moneyness, dte_band, policy) cell this
module:
  1. joins trades.parquet back to contracts.parquet to recover strike/
     contract_type/entry_premium_real (dropped by short_ledger's CANON_COLS
     normalization but needed here to mark the position daily),
  2. builds a per-contract DAILY mark panel (margin_per_share,
     liability_per_share) from paths.parquet + _unadj_daily.parquet, reusing
     short_ledger's _margin_expr/build_calendar/attach_calendar_idx (the same
     primitives short_ledger.compute_peak_margin uses, just keeping the FULL
     daily series instead of collapsing to a max),
  3. hands the resulting positions + marks to _sim_core.replay() /
     _sim_core.jackknife() -- the sequential capital-allocation engine shared
     with crash_stress.py.

Sizing config (locked grid, see PREREGISTRATION.md "Portfolio phase"):
  margin_budget_frac in {0.10, 0.20, 0.33, 0.50}
  per_trade_cap_frac in {0.02, 0.05, 0.10}
  max_concurrent     in {8, 14, 20}
  start_capital = 100_000

One contract = 100 shares (n sized in whole contracts): n = floor(
per_trade_cap_frac * equity / (margin0_per_share * 100)); skip (record) if 0.

Outputs per (cell, sizing-config): final equity, CAGR, worst drawdown,
collapse flag, margin-call count, forced-liquidation P&L impact, mean/peak
margin utilization, skip rate, per-year returns, an equity-curve parquet
(.cache/short_premium/portfolio/{tag}_curve.parquet), and a jackknife
(seed 42, N=200, drop 20% of signals) p05/p50/p95 band on final equity and
worst DD -- this is the signal-level CI, NOT a fill-model bootstrap.

TRAPS HONORED
  G5   ASCII-only stdout.
  G7/G47 same fill_nan/finite-mask discipline as short_ledger.py (reused, not
         re-implemented, for the parquet loads).
  Worktree PYTHONPATH trap: repo root pinned at sys.path[0] first.
  "Long compute must NOT be launched by the builder": --run touches real
  parquet data and can be minutes+ on the full grid -- the orchestrator
  queues it (`trader queue submit`); this script only guarantees --selftest
  passes offline on synthetic data.

USAGE
-----
    python experiments/short_premium/portfolio_sim.py --selftest
    python experiments/short_premium/portfolio_sim.py --run \
        --cells "bull_put:1.00:d30:none_none" --grid
    python experiments/short_premium/portfolio_sim.py --run \
        --cells "bull_put:1.00:d30:tp50_sl2x,bull_put:0.95:d30:tp50_sl2x"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
assert (_ROOT / "strategy_config.py").exists(), (
    f"sys.path[0] pin didn't land on the repo root: {_ROOT}"
)
_HERE_DIR = str(_HERE.parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)          # so `import short_ledger` / `_sim_core` work

import polars as pl                          # noqa: E402

import short_ledger as SL                    # noqa: E402
import _sim_core as SC                       # noqa: E402

CACHE_DIR = SL.CACHE_DIR
TRADES_PATH = SL.TRADES_OUT
PORTFOLIO_DIR = CACHE_DIR / "portfolio"

START_CAPITAL = 100_000.0
MARGIN_BUDGET_FRACS = [0.10, 0.20, 0.33, 0.50]
PER_TRADE_CAP_FRACS = [0.02, 0.05, 0.10]
MAX_CONCURRENT_GRID = [8, 14, 20]
DEFAULT_CONFIG = dict(margin_budget_frac=0.20, per_trade_cap_frac=0.05, max_concurrent=14)

JACKKNIFE_N = 200
JACKKNIFE_SEED = 42
JACKKNIFE_DROP_FRAC = 0.20


def _log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# Cell parsing
# ===========================================================================

def parse_cells(spec: str) -> list[tuple[str, float, str, str]]:
    """'arm:mny:band:policy,arm2:mny2:band2:policy2' -> list of 4-tuples."""
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 4:
            raise ValueError(f"bad --cells entry {chunk!r}; expected arm:mny:band:policy")
        arm, mny, band, policy = parts
        out.append((arm, float(mny), band, policy))
    return out


def sizing_grid(full: bool) -> list[dict]:
    if not full:
        return [dict(DEFAULT_CONFIG)]
    grid = []
    for b in MARGIN_BUDGET_FRACS:
        for c in PER_TRADE_CAP_FRACS:
            for m in MAX_CONCURRENT_GRID:
                grid.append(dict(margin_budget_frac=b, per_trade_cap_frac=c, max_concurrent=m))
    return grid


# ===========================================================================
# Real-mode: build positions + marks panel for one cell from the parquet trio
# ===========================================================================

def load_real_frames():
    for p in (SL.CONTRACTS_PATH, SL.PATHS_PATH, SL.SPOTS_PATH, TRADES_PATH):
        if not p.exists():
            raise FileNotFoundError(
                f"missing input {p} -- run short_ledger.py --run first (or the "
                "upstream parallel pull hasn't landed yet)."
            )
    contracts = SL._fill_nan_choke(pl.read_parquet(SL.CONTRACTS_PATH))
    paths = SL._fill_nan_choke(pl.read_parquet(SL.PATHS_PATH))
    spots = SL._fill_nan_choke(pl.read_parquet(SL.SPOTS_PATH))
    trades = SL._fill_nan_choke(pl.read_parquet(TRADES_PATH))
    contracts = SL._normalize_dates(contracts, ["signal_date", "expiration", "path_end_date"])
    paths = SL._normalize_dates(paths, ["date"])
    spots = SL._normalize_dates(spots, ["date"])
    trades = SL._normalize_dates(trades, ["signal_date"])
    return contracts, paths, spots, trades


def _reverse_calendar(calendar: pl.DataFrame) -> pl.DataFrame:
    """cal_idx -> date lookup (the inverse of build_calendar's date -> cal_idx)."""
    return calendar.select(["cal_idx", "date"])


def build_cell_positions_and_marks(contracts: pl.DataFrame, paths: pl.DataFrame,
                                    spots: pl.DataFrame, trades: pl.DataFrame,
                                    calendar: pl.DataFrame,
                                    arm: str, mny: float, band: str, policy: str,
                                    haircut: float = SL.PRIMARY_HAIRCUT):
    """Returns (positions: list[dict], marks_lookup: dict, day_seq: list[tuple[int,date]])
    for one (arm, mny, band, policy) cell. Empty results if the cell has 0
    kept trades (caller reports and skips, never fabricates)."""
    sel = trades.filter(
        (pl.col("study_arm") == arm) & (pl.col("target_moneyness") == mny)
        & (pl.col("dte_band") == band) & (pl.col("policy") == policy)
    )
    if sel.height == 0:
        return [], {}, []

    is_pmcc = arm == "pmcc"
    if not is_pmcc:
        c = contracts.filter(pl.col("status") == "kept").select(
            ["contract_id", "contract_type", "strike", "entry_premium_real"]
        )
        sel = sel.join(c, on="contract_id", how="left")
    else:
        # Recover the pairing short_ledger.build_pmcc_trades used (symbol,
        # signal_date match) since trades.parquet's CANON_COLS normalization
        # drops long_contract_id / long_entry_premium_real. contract_id in
        # trades.parquet for arm='pmcc' rows is the SHORT leg's contract_id.
        shorts_c = contracts.filter(
            (pl.col("study_arm") == "pmcc_short") & (pl.col("status") == "kept")
        ).select(["contract_id", "contract_type", "strike", "entry_premium_real",
                   "symbol", "signal_date"])
        longs_c = contracts.filter(
            (pl.col("study_arm") == "pmcc_long") & (pl.col("status") == "kept")
        ).select(["symbol", "signal_date", "entry_premium_real"]).rename(
            {"entry_premium_real": "long_entry_premium_real"}
        )
        pair = shorts_c.join(longs_c, on=["symbol", "signal_date"], how="inner")
        sel = sel.join(
            pair.select(["contract_id", "contract_type", "strike", "entry_premium_real",
                         "long_entry_premium_real"]),
            on="contract_id", how="left",
        )
        sel = sel.with_columns(
            (pl.col("long_entry_premium_real") * (2.0 - haircut)).alias("long_entry_cost")
        )

    sel = sel.filter(pl.col("entry_premium_real").is_not_null())
    if sel.height == 0:
        return [], {}, []

    sel = SL.attach_calendar_idx(sel, "signal_date", calendar, "entry_idx")
    sel = sel.with_columns((pl.col("entry_idx") + pl.col("days_held")).alias("exit_idx"))
    rev = _reverse_calendar(calendar).rename({"cal_idx": "exit_idx", "date": "exit_date"})
    sel = sel.sort("exit_idx").join_asof(rev, on="exit_idx", strategy="backward")

    # --- daily mark panel: explode calendar days [signal_date, exit_date],
    # inner-join spots for S_t (drops non-trading days for free, same trick
    # as short_ledger.compute_peak_margin), asof-join this contract's own
    # path for the forward-filled premium mark.
    ef = sel.select(["contract_id", "symbol", "signal_date", "exit_date", "strike",
                      "contract_type", "entry_premium_real"])
    ef = ef.with_columns(
        pl.date_ranges(pl.col("signal_date"), pl.col("exit_date"), interval="1d").alias("date")
    ).explode("date")
    ef = ef.join(spots.select(["symbol", "date", "spot_unadj"]), on=["symbol", "date"], how="inner")
    px = paths.select(["contract_id", "date", "close"]).sort(["contract_id", "date"])
    ef = ef.sort(["contract_id", "date"])
    ef = ef.join_asof(px, on="date", by="contract_id", strategy="backward")
    ef = ef.with_columns(pl.col("close").fill_null(pl.col("entry_premium_real")).alias("prem_mark"))
    ef = ef.with_columns(
        SL._margin_expr("spot_unadj", "strike", "prem_mark", "contract_type").alias("margin_per_share")
    )
    ef = SL.attach_calendar_idx(ef, "date", calendar, "day_idx")

    if is_pmcc:
        long_px = paths.select(["contract_id", "date", "close"]).rename(
            {"close": "long_mark"}
        ).sort(["contract_id", "date"])
        # long leg's own contract_id isn't preserved on `ef` -- rejoin via sel's pairing
        long_map = sel.select(["contract_id", "symbol", "signal_date"]).join(
            contracts.filter(
                (pl.col("study_arm") == "pmcc_long") & (pl.col("status") == "kept")
            ).select(["symbol", "signal_date", "contract_id", "entry_premium_real"]).rename(
                {"contract_id": "long_contract_id", "entry_premium_real": "long_entry_premium_real"}
            ),
            on=["symbol", "signal_date"], how="left",
        )
        ef = ef.join(long_map.select(["contract_id", "long_contract_id", "long_entry_premium_real"]),
                      on="contract_id", how="left")
        ef = ef.sort(["long_contract_id", "date"])
        ef = ef.join_asof(long_px.rename({"contract_id": "long_contract_id"}),
                           on="date", by="long_contract_id", strategy="backward")
        ef = ef.with_columns(pl.col("long_mark").fill_null(pl.col("long_entry_premium_real")))
        ef = ef.with_columns((pl.col("prem_mark") - pl.col("long_mark")).alias("liability_per_share"))
    else:
        ef = ef.with_columns(pl.col("prem_mark").alias("liability_per_share"))

    marks_lookup: dict[str, dict[int, tuple[float, float]]] = {}
    for row in ef.select(["contract_id", "day_idx", "margin_per_share", "liability_per_share"]).iter_rows():
        cid, di, m, l = row
        if di is None or m is None or l is None:
            continue
        marks_lookup.setdefault(cid, {})[int(di)] = (float(m), float(l))

    positions = []
    for row in sel.iter_rows(named=True):
        cid = row["contract_id"]
        if cid not in marks_lookup:
            continue
        if is_pmcc:
            entry_credit = row["entry_premium_real"] * haircut - row["long_entry_cost"]
            margin0 = row["long_entry_cost"]
            kind = "pmcc"
        else:
            entry_credit = row["entry_premium_real"] * haircut
            margin0 = row["margin0"]
            kind = "short"
        positions.append(dict(
            contract_id=cid, symbol=row["symbol"],
            entry_day_idx=int(row["entry_idx"]), exit_day_idx=int(row["exit_idx"]),
            margin0_per_share=float(margin0), entry_credit_per_share=float(entry_credit),
            ledger_pnl_per_share=float(row["pnl_share"]), kind=kind,
        ))

    if not positions:
        return [], {}, []

    lo = min(p["entry_day_idx"] for p in positions)
    hi = max(p["exit_day_idx"] for p in positions)
    day_seq = [(int(r["cal_idx"]), r["date"]) for r in
               calendar.filter((pl.col("cal_idx") >= lo) & (pl.col("cal_idx") <= hi))
               .sort("cal_idx").iter_rows(named=True)]
    return positions, marks_lookup, day_seq


# ===========================================================================
# Run one (cell, sizing-config) -> replay + jackknife + persist curve
# ===========================================================================

def run_one(positions, marks_lookup, day_seq, cell_tag: str, cfg: dict) -> dict:
    config = SC.SimConfig(
        start_capital=START_CAPITAL,
        margin_budget_frac=cfg["margin_budget_frac"],
        per_trade_cap_frac=cfg["per_trade_cap_frac"],
        max_concurrent=cfg["max_concurrent"],
        haircut=SL.PRIMARY_HAIRCUT,
        tag=cell_tag,
    )
    res = SC.replay(positions, marks_lookup, day_seq, config)
    jk = SC.jackknife(positions, marks_lookup, day_seq, config,
                       n=JACKKNIFE_N, seed=JACKKNIFE_SEED, drop_frac=JACKKNIFE_DROP_FRAC)
    res["jackknife"] = jk
    res["config"] = cfg

    tag = (f"{cell_tag}_b{cfg['margin_budget_frac']}_c{cfg['per_trade_cap_frac']}"
           f"_m{cfg['max_concurrent']}").replace(".", "")
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    curve = res["equity_curve"]
    if curve:
        curve_df = pl.DataFrame({
            "date": [d for d, _ in curve],
            "equity": [e for _, e in curve],
        })
        curve_df.write_parquet(PORTFOLIO_DIR / f"{tag}_curve.parquet")
    return res


def print_result_row(res: dict) -> None:
    cfg = res["config"]
    jk = res["jackknife"]
    _log(f"  [{res['tag']:<40}] budget={cfg['margin_budget_frac']:.2f} "
         f"cap={cfg['per_trade_cap_frac']:.2f} maxpos={cfg['max_concurrent']:>2} "
         f"final_eq={res['final_equity']:>12,.0f} CAGR={res['cagr']:+.3f} "
         f"worstDD={res['worst_dd']:.3f} collapse={res['collapse']} "
         f"mcalls={res['margin_call_days']} fliq={res['forced_liq_count']} "
         f"fliq_pnl={res['forced_liq_pnl_impact']:+,.0f} "
         f"util(mean/pk)={res['mean_util']:.2f}/{res['peak_util']:.2f} "
         f"skip_rate={res['skip_rate']:.2f} n_pos={res['n_positions']} | "
         f"JK p05/p50/p95 eq={jk['final_equity_p05']:,.0f}/{jk['final_equity_p50']:,.0f}/"
         f"{jk['final_equity_p95']:,.0f} DD={jk['worst_dd_p05']:.3f}/{jk['worst_dd_p50']:.3f}/"
         f"{jk['worst_dd_p95']:.3f}")


def run(cells_spec: str, full_grid: bool) -> None:
    contracts, paths, spots, trades = load_real_frames()
    calendar = SL.build_calendar(spots)
    cells = parse_cells(cells_spec)
    grid = sizing_grid(full_grid)
    _log(f"[portfolio_sim] {len(cells)} cell(s) x {len(grid)} sizing config(s)")

    all_rows = []
    for arm, mny, band, policy in cells:
        cell_tag = f"{arm}_{mny}_{band}_{policy}"
        positions, marks_lookup, day_seq = build_cell_positions_and_marks(
            contracts, paths, spots, trades, calendar, arm, mny, band, policy,
        )
        if not positions:
            _log(f"[portfolio_sim] cell {cell_tag}: 0 usable trades -- skipping")
            continue
        _log(f"\n-- cell {cell_tag}: {len(positions)} trades --")
        for cfg in grid:
            res = run_one(positions, marks_lookup, day_seq, cell_tag, cfg)
            print_result_row(res)
            row = {k: v for k, v in res.items() if k not in ("equity_curve", "jackknife")}
            row.update({f"cfg_{k}": v for k, v in cfg.items()})
            row.update({f"jk_{k}": v for k, v in res["jackknife"].items() if k not in ("n", "seed", "drop_frac")})
            all_rows.append(row)

    if all_rows:
        for r in all_rows:
            r.pop("per_year", None)   # nested dict -- not parquet-friendly, dropped from the table
            r.pop("collapse_day_idx", None)
        out = pl.DataFrame(all_rows, infer_schema_length=None)
        PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
        out.write_parquet(PORTFOLIO_DIR / "results.parquet")
        _log(f"\n[portfolio_sim] wrote {PORTFOLIO_DIR / 'results.parquet'} ({out.height} rows)")


# ===========================================================================
# Selftest (synthetic in-memory data, no parquet/DB/network)
# ===========================================================================

def _d(y, m, day):
    return _dt.date(y, m, day)


def _weekday_seq(n, start=_dt.date(2022, 1, 3)):
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d = d + _dt.timedelta(days=1)
    return days


def selftest() -> bool:
    results = {}
    cal_days = _weekday_seq(20)
    day_seq = [(i, cal_days[i]) for i in range(6)]

    # -----------------------------------------------------------------
    # (a) two-trade replay, hand-computed equity curve + DD
    # -----------------------------------------------------------------
    positions_a = [
        dict(contract_id="P1", symbol="AAA", entry_day_idx=0, exit_day_idx=3,
             margin0_per_share=1.0, entry_credit_per_share=0.9, ledger_pnl_per_share=0.5, kind="short"),
        dict(contract_id="P2", symbol="AAA", entry_day_idx=2, exit_day_idx=5,
             margin0_per_share=1.0, entry_credit_per_share=0.8, ledger_pnl_per_share=-0.2, kind="short"),
    ]
    marks_a = {
        "P1": {0: (10.0, 0.9), 1: (10.0, 0.5), 2: (10.0, 0.3)},
        "P2": {2: (10.0, 0.8), 3: (10.0, 1.0), 4: (10.0, 1.2)},
    }
    cfg_a = SC.SimConfig(start_capital=10_000.0, margin_budget_frac=1.0,
                          per_trade_cap_frac=0.01, max_concurrent=5, haircut=0.90)
    res_a = SC.replay(positions_a, marks_a, day_seq, cfg_a)
    expect_curve = [10000.0, 10040.0, 10060.0, 10030.0, 10010.0, 10030.0]
    got_curve = [e for _, e in res_a["equity_curve"]]
    ok_a1 = all(abs(g - x) < 1e-6 for g, x in zip(got_curve, expect_curve)) and len(got_curve) == 6
    expect_dd = 50.0 / 10060.0
    ok_a2 = abs(res_a["worst_dd"] - expect_dd) < 1e-9
    ok_a3 = abs(res_a["final_equity"] - 10030.0) < 1e-6
    ok_a = ok_a1 and ok_a2 and ok_a3
    results["a_two_trade_curve_and_dd"] = ok_a
    _log(f"[selftest a] curve={[round(x,2) for x in got_curve]} expect={expect_curve} "
         f"worst_dd={res_a['worst_dd']:.6f} expect={expect_dd:.6f} -> {'PASS' if ok_a else 'FAIL'}")

    # -----------------------------------------------------------------
    # (b) margin-call forced liquidation: largest-margin liquidated first
    # -----------------------------------------------------------------
    positions_b = [
        dict(contract_id="P1", symbol="AAA", entry_day_idx=0, exit_day_idx=5,
             margin0_per_share=1.0, entry_credit_per_share=1.0, ledger_pnl_per_share=0.6, kind="short"),
        dict(contract_id="P2", symbol="AAA", entry_day_idx=0, exit_day_idx=3,
             margin0_per_share=1.0, entry_credit_per_share=0.8, ledger_pnl_per_share=0.3, kind="short"),
    ]
    marks_b = {
        "P1": {0: (1.0, 1.0), 2: (15.0, 15.0)},
        "P2": {0: (0.8, 0.8), 2: (1.0, 1.0)},
    }
    cfg_b = SC.SimConfig(start_capital=2_000.0, margin_budget_frac=1.0,
                          per_trade_cap_frac=0.05, max_concurrent=5, haircut=0.90)
    res_b = SC.replay(positions_b, marks_b, day_seq, cfg_b)
    ok_b1 = res_b["forced_liq_count"] == 1
    ok_b2 = res_b["margin_call_days"] == 1
    ok_b3 = abs(res_b["forced_liq_pnl_impact"] - (-1610.0)) < 1e-6
    ok_b4 = abs(res_b["final_equity"] - 480.0) < 1e-6   # only reachable if P1 (largest margin), not P2, was liquidated
    ok_b = ok_b1 and ok_b2 and ok_b3 and ok_b4
    results["b_margin_call_largest_first"] = ok_b
    _log(f"[selftest b] forced_liq_count={res_b['forced_liq_count']} "
         f"margin_call_days={res_b['margin_call_days']} "
         f"forced_liq_pnl_impact={res_b['forced_liq_pnl_impact']:.2f} "
         f"final_equity={res_b['final_equity']:.2f} -> {'PASS' if ok_b else 'FAIL'}")

    # -----------------------------------------------------------------
    # (c) collapse when equity <= 0 (pmcc kind -- no margin-call machinery,
    #     collapse still triggers on the generic equity<=0 check)
    # -----------------------------------------------------------------
    positions_c = [
        dict(contract_id="X1", symbol="AAA", entry_day_idx=0, exit_day_idx=5,
             margin0_per_share=3.0, entry_credit_per_share=0.0, ledger_pnl_per_share=0.1, kind="pmcc"),
    ]
    marks_c = {"X1": {0: (0.0, 0.0), 1: (0.0, 20.0)}}
    cfg_c = SC.SimConfig(start_capital=1_000.0, margin_budget_frac=1.0,
                          per_trade_cap_frac=0.5, max_concurrent=5, haircut=0.90)
    res_c = SC.replay(positions_c, marks_c, day_seq, cfg_c)
    ok_c1 = res_c["collapse"] is True
    ok_c2 = res_c["collapse_day_idx"] == 1
    ok_c3 = abs(res_c["final_equity"] - (-1000.0)) < 1e-6
    ok_c4 = len(res_c["equity_curve"]) == 2   # day loop stopped at day1, day2+ never simulated
    ok_c = ok_c1 and ok_c2 and ok_c3 and ok_c4
    results["c_collapse_on_equity_leq_zero"] = ok_c
    _log(f"[selftest c] collapse={res_c['collapse']} day_idx={res_c['collapse_day_idx']} "
         f"final_equity={res_c['final_equity']:.2f} n_days_recorded={len(res_c['equity_curve'])} "
         f"-> {'PASS' if ok_c else 'FAIL'}")

    # -----------------------------------------------------------------
    # (d) budget-exhausted skip
    # -----------------------------------------------------------------
    positions_d = [
        dict(contract_id="A", symbol="AAA", entry_day_idx=0, exit_day_idx=3,
             margin0_per_share=0.6, entry_credit_per_share=0.5, ledger_pnl_per_share=0.1, kind="short"),
        dict(contract_id="B", symbol="AAA", entry_day_idx=0, exit_day_idx=3,
             margin0_per_share=0.6, entry_credit_per_share=0.5, ledger_pnl_per_share=0.1, kind="short"),
    ]
    marks_d = {
        "A": {0: (0.6, 0.5)},
        "B": {0: (0.6, 0.5)},
    }
    cfg_d = SC.SimConfig(start_capital=1_000.0, margin_budget_frac=0.10,
                          per_trade_cap_frac=0.10, max_concurrent=5, haircut=0.90)
    res_d = SC.replay(positions_d, marks_d, day_seq, cfg_d)
    ok_d1 = res_d["skip_count"] == 1
    ok_d2 = res_d["attempt_count"] == 2
    ok_d3 = abs(res_d["skip_rate"] - 0.5) < 1e-9
    ok_d = ok_d1 and ok_d2 and ok_d3
    results["d_budget_exhausted_skip"] = ok_d
    _log(f"[selftest d] skip_count={res_d['skip_count']} attempt_count={res_d['attempt_count']} "
         f"skip_rate={res_d['skip_rate']:.2f} -> {'PASS' if ok_d else 'FAIL'}")

    # sub-case: max_concurrent skip (same idea, different limiter)
    cfg_d2 = SC.SimConfig(start_capital=1_000.0, margin_budget_frac=1.0,
                           per_trade_cap_frac=0.10, max_concurrent=1, haircut=0.90)
    res_d2 = SC.replay(positions_d, marks_d, day_seq, cfg_d2)
    ok_d2b = res_d2["skip_count"] == 1 and res_d2["attempt_count"] == 2
    results["d2_max_concurrent_skip"] = ok_d2b
    _log(f"[selftest d2] max_concurrent=1 skip_count={res_d2['skip_count']} "
         f"-> {'PASS' if ok_d2b else 'FAIL'}")

    # -----------------------------------------------------------------
    # (e) jackknife determinism (same seed -> identical result)
    # -----------------------------------------------------------------
    positions_e = positions_a
    marks_e = marks_a
    cfg_e = cfg_a
    jk1 = SC.jackknife(positions_e, marks_e, day_seq, cfg_e, n=50, seed=42, drop_frac=0.2)
    jk2 = SC.jackknife(positions_e, marks_e, day_seq, cfg_e, n=50, seed=42, drop_frac=0.2)
    ok_e = jk1 == jk2
    results["e_jackknife_determinism"] = ok_e
    _log(f"[selftest e] jk1==jk2 -> {'PASS' if ok_e else 'FAIL'} "
         f"(p50 final_equity={jk1['final_equity_p50']:.2f})")
    # different seed should (almost certainly) differ, sanity-checking the
    # seed actually does something rather than always returning a constant
    jk3 = SC.jackknife(positions_e, marks_e, day_seq, cfg_e, n=50, seed=43, drop_frac=0.2)
    ok_e2 = jk3 != jk1
    results["e2_different_seed_differs"] = ok_e2
    _log(f"[selftest e2] jk(seed42)!=jk(seed43) -> {'PASS' if ok_e2 else 'FAIL'}")

    _log("\n" + "=" * 60)
    all_ok = all(results.values())
    for k, v in results.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")
    _log("=" * 60)
    _log(f"SELFTEST {'PASS' if all_ok else 'FAIL'} ({sum(results.values())}/{len(results)})")
    return all_ok


# ===========================================================================
# CLI
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="short_premium portfolio capital-allocation replay")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cells", default=None, help="'arm:mny:band:policy[,arm:mny:band:policy...]'")
    ap.add_argument("--grid", action="store_true", help="run the full sizing grid (36 combos/cell)")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)
    if args.run:
        if not args.cells:
            ap.error("--run requires --cells")
        run(args.cells, args.grid)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
