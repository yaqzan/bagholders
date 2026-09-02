"""True MC sweep for sector-wave allocation curves.

Experiment-local only. It patches V4 sector ETF scores at signal-load time and
patches monte_carlo.run_single_sim in-process so allocation scaling happens at
the actual fill point. That lets cash, caps, ordering, exits, and DD be
recomputed instead of replayed from an existing tape.

The shipped static realloc family is intentionally forced off here; it is
env-gated OFF in production and documented as a Stage-3 null.

Outputs:
  experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_mc.csv
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.sector_etf_alpha.sector_dd_probe import build_lookup, patch_signal_loaders

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
META = ROOT / ".cache" / "sector_etf_screen" / "stock_meta.parquet"
OUT = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe" / "sector_wave_alloc_mc.csv"

NON_OVERSOLD_RSI = {"rsi_45_55", "rsi_55_70", "rsi_gt70"}
NON_OVERSOLD_PHASE = {"phase_mid", "phase_overheated", "phase_deep_overheated"}


def _lerp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 <= x0:
        return y1
    t = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
    return y0 + t * (y1 - y0)


def exposure_scale(exp: float, strength: str = "soft") -> float:
    """Smooth exposure ramp matching the replay families."""
    if strength == "balanced":
        pts = [(0.10, 1.00), (0.20, 0.90), (0.35, 0.75), (0.50, 0.60), (0.70, 0.45)]
    elif strength == "strong":
        pts = [(0.10, 1.00), (0.20, 0.75), (0.35, 0.45), (0.50, 0.30), (0.70, 0.20)]
    else:
        pts = [(0.10, 1.00), (0.20, 0.85), (0.35, 0.65), (0.50, 0.50), (0.70, 0.35)]
    if exp <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if exp <= x1:
            return _lerp(exp, x0, y0, x1, y1)
    return pts[-1][1]


def load_sector_context() -> tuple[dict[str, str], dict[tuple[str, str], tuple[str, str]]]:
    meta = pl.read_parquet(META).select(["symbol", "sector"]).unique("symbol")
    sector_by_symbol = {
        str(sym): str(sec or "unknown")
        for sym, sec in meta.iter_rows()
    }

    cohort = pl.read_parquet(
        LOCAL / "cohort_v46_1825.parquet",
        columns=["symbol", "date", "sec_etf_rsi", "sec_etf_pct_ema50"],
    )
    cohort = cohort.with_columns([
        pl.when(pl.col("sec_etf_rsi") < 30).then(pl.lit("rsi_lt30"))
          .when(pl.col("sec_etf_rsi") < 45).then(pl.lit("rsi_30_45"))
          .when(pl.col("sec_etf_rsi") <= 55).then(pl.lit("rsi_45_55"))
          .when(pl.col("sec_etf_rsi") <= 70).then(pl.lit("rsi_55_70"))
          .otherwise(pl.lit("rsi_gt70"))
          .alias("sector_rsi_bin"),
        ((((pl.col("sec_etf_rsi") - 50.0) / 25.0).clip(-1.0, 1.0).fill_null(0.0)
          + (pl.col("sec_etf_pct_ema50") / 8.0).clip(-1.0, 1.0).fill_null(0.0)) / 2.0)
          .alias("sector_phase"),
    ])
    cohort = cohort.with_columns(
        pl.when(pl.col("sector_phase") < -0.60).then(pl.lit("phase_deep_oversold"))
          .when(pl.col("sector_phase") < -0.25).then(pl.lit("phase_oversold"))
          .when(pl.col("sector_phase") <= 0.25).then(pl.lit("phase_mid"))
          .when(pl.col("sector_phase") <= 0.60).then(pl.lit("phase_overheated"))
          .otherwise(pl.lit("phase_deep_overheated"))
          .alias("sector_phase_bin")
    )
    wave_by_key = {
        (str(sym), str(dt)): (str(rsi), str(phase))
        for sym, dt, rsi, phase in cohort.select(
            ["symbol", "date", "sector_rsi_bin", "sector_phase_bin"]
        ).iter_rows()
    }
    return sector_by_symbol, wave_by_key


def curve_scale(
    curve: str,
    side: str,
    side_exp: float,
    gross_exp: float,
    rsi_bin: str,
    phase_bin: str,
) -> float:
    if curve in {"V4_BASE", "V4"}:
        return 1.0
    if curve == "SWA_BOTH_SOFT":
        return exposure_scale(side_exp, "soft")
    if curve == "SWA_GROSS_SOFT":
        return exposure_scale(gross_exp, "soft")
    if curve == "SWA_GROSS_BALANCED":
        return exposure_scale(gross_exp, "balanced")
    if curve == "SWA_GROSS_STRONG":
        return exposure_scale(gross_exp, "strong")
    if curve == "SWA_GROSS_PHASEMID_SOFT":
        if phase_bin in NON_OVERSOLD_PHASE:
            return exposure_scale(gross_exp, "soft")
        return 1.0
    if curve == "SWA_GROSS_RSI45_SOFT":
        if rsi_bin in NON_OVERSOLD_RSI:
            return exposure_scale(gross_exp, "soft")
        return 1.0
    if curve == "SWA_PUT_PHASEMID_SOFT":
        if side == "put" and phase_bin in NON_OVERSOLD_PHASE:
            return exposure_scale(side_exp, "soft")
        return 1.0
    if curve == "SWA_PUT_RSI45_SOFT":
        if side == "put" and rsi_bin in NON_OVERSOLD_RSI:
            return exposure_scale(side_exp, "soft")
        return 1.0
    raise ValueError(f"unknown curve {curve}")


def make_run_single_sim(curve: str, sector_by_symbol: dict[str, str], wave_by_key: dict[tuple[str, str], tuple[str, str]]):
    import monte_carlo as mc

    def _sector(sym_id: str) -> str:
        return sector_by_symbol.get(str(sym_id), "unknown")

    def _wave(sym_id: str, today) -> tuple[str, str]:
        d = today.isoformat() if hasattr(today, "isoformat") else str(today)
        return wave_by_key.get((str(sym_id), d), ("rsi_unknown", "phase_unknown"))

    def run_single_sim(trading_days, calls_by_date, call_outcomes,
                       puts_by_date, put_outcomes, rng,
                       regime_dates=None, regime_map=None,
                       collect_tape=False, close_by_sym_idx=None):
        cash = mc.STARTING_CASH
        positions = []
        pos_sector: dict[int, str] = {}
        peak_value = mc.STARTING_CASH
        max_dd = 0.0
        day_to_idx = {d: i for i, d in enumerate(trading_days)}

        tp_c = sl_c = hard_c = 0
        tp_p = sl_p = hard_p = 0
        trade_rows = [] if collect_tape else None
        cur_episode_start_idx = 0 if collect_tape else None
        cur_episode_trough_idx = 0 if collect_tape else None
        cur_episode_trough_val = mc.STARTING_CASH if collect_tape else None
        episodes = [] if collect_tape else None

        cap_call = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
        cap_put = mc.MAX_POSITIONS_PUT if mc.MAX_POSITIONS_PUT is not None else mc.MAX_POSITIONS
        side_capped = (mc.MAX_POSITIONS_CALL is not None) or (mc.MAX_POSITIONS_PUT is not None)

        def current_exposure(sector: str, side: str, portfolio_value: float) -> tuple[float, float]:
            if portfolio_value <= 0:
                return 0.0, 0.0
            side_prem = 0.0
            gross_prem = 0.0
            for p in positions:
                if pos_sector.get(id(p), "unknown") != sector:
                    continue
                gross_prem += p.premium_cost
                if p.side == side:
                    side_prem += p.premium_cost
            return side_prem / portfolio_value, gross_prem / portfolio_value

        for day_idx, today in enumerate(trading_days):
            keep = []
            for p in positions:
                base = day_to_idx.get(p.entry_date, -999)
                if base + p.exit_bar <= day_idx:
                    cash += p.premium_cost * (1 + p.option_pnl)
                    pos_sector.pop(id(p), None)
                    if p.side == "call":
                        if p.outcome == "tp":
                            tp_c += 1
                        elif p.outcome == "sl":
                            sl_c += 1
                        else:
                            hard_c += 1
                    else:
                        if p.outcome == "tp":
                            tp_p += 1
                        elif p.outcome == "sl":
                            sl_p += 1
                        else:
                            hard_p += 1
                    if collect_tape:
                        trade_rows.append((
                            p.sym_id, p.entry_date, today, p.side, p.score, p.tier,
                            p.ct, p.ern, p.premium_cost, p.option_pnl, p.outcome,
                            p.entry_value, p.entry_peak, p.entry_dd,
                            p.entry_open_calls, p.entry_open_puts,
                        ))
                else:
                    keep.append(p)
            positions = keep

            portfolio_value = cash + sum(p.premium_cost for p in positions)
            if portfolio_value > peak_value:
                if collect_tape and cur_episode_trough_val < peak_value:
                    episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                                     peak_value, cur_episode_trough_val))
                peak_value = portfolio_value
                if collect_tape:
                    cur_episode_start_idx = day_idx
                    cur_episode_trough_idx = day_idx
                    cur_episode_trough_val = portfolio_value
            elif collect_tape and portfolio_value < cur_episode_trough_val:
                cur_episode_trough_idx = day_idx
                cur_episode_trough_val = portfolio_value

            dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
            max_dd = max(max_dd, dd)
            if portfolio_value <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD:
                break

            open_syms = {p.sym_id for p in positions}
            call_open = sum(1 for p in positions if p.side == "call")
            put_open = sum(1 for p in positions if p.side == "put")

            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)

            def _try_fill_call(sym_id, score, key, ct, ern):
                nonlocal cash, call_open
                if len(positions) >= mc.MAX_POSITIONS or (side_capped and call_open >= cap_call):
                    return False
                if ct == "ct_call":
                    tier = mc.CT_CALL_TIER
                elif ern and mc.EARN_BOOST_CALL:
                    tier = mc.EARN_BOOST_CALL_TIER
                else:
                    tier = mc.score_to_tier(score)
                dd_scale = 1.0
                if mc.DD_SOFT_BAND_HI > mc.DD_SOFT_BAND_LO and dd > mc.DD_SOFT_BAND_LO:
                    if dd >= mc.DD_SOFT_BAND_HI:
                        dd_scale = mc.DD_SOFT_CALL_FLOOR
                    else:
                        t = (dd - mc.DD_SOFT_BAND_LO) / (mc.DD_SOFT_BAND_HI - mc.DD_SOFT_BAND_LO)
                        dd_scale = 1.0 - t * (1.0 - mc.DD_SOFT_CALL_FLOOR)
                sector = _sector(sym_id)
                rsi_bin, phase_bin = _wave(sym_id, today)
                side_exp, gross_exp = current_exposure(sector, "call", portfolio_value)
                swa_scale = curve_scale(curve, "call", side_exp, gross_exp, rsi_bin, phase_bin)
                premium_cost = portfolio_value * mc.TIER_ALLOC[tier] * reg_scale_c * dd_scale * swa_scale
                if premium_cost > cash or premium_cost <= 0:
                    return False
                o = call_outcomes[key]
                outcome, pnl = mc.resolve(o, rng)
                cash -= premium_cost
                p = mc.Position(sym_id, today, o["exit_bar"], premium_cost, pnl, outcome, "call",
                                score=score, tier=tier, ct=ct, ern=ern,
                                entry_value=portfolio_value, entry_peak=peak_value,
                                entry_dd=dd, entry_open_calls=call_open, entry_open_puts=put_open,
                                entry_idx=day_idx, entry_underlying=o.get("entry"),
                                premium_pct=o.get("premium_pct"))
                positions.append(p)
                pos_sector[id(p)] = sector
                open_syms.add(sym_id)
                call_open += 1
                return True

            def _try_fill_put(sym_id, score, key, ct):
                nonlocal cash, put_open
                if len(positions) >= mc.MAX_POSITIONS or (side_capped and put_open >= cap_put):
                    return False
                tier = mc.CT_PUT_TIER if ct == "ct_put" else mc.put_score_to_tier(score)
                sector = _sector(sym_id)
                rsi_bin, phase_bin = _wave(sym_id, today)
                side_exp, gross_exp = current_exposure(sector, "put", portfolio_value)
                swa_scale = curve_scale(curve, "put", side_exp, gross_exp, rsi_bin, phase_bin)
                alloc_frac = mc.PUT_TIER_ALLOC[tier] * reg_scale_p * mc.saw_put_ucurve_scale(today) * swa_scale
                premium_cost = portfolio_value * alloc_frac
                if premium_cost > cash or premium_cost <= 0:
                    return False
                o = put_outcomes[key]
                outcome, pnl = mc.resolve(o, rng)
                cash -= premium_cost
                p = mc.Position(sym_id, today, o["exit_bar"], premium_cost, pnl, outcome, "put",
                                score=score, tier=tier, ct=ct, ern=False,
                                entry_value=portfolio_value, entry_peak=peak_value,
                                entry_dd=dd, entry_open_calls=call_open, entry_open_puts=put_open,
                                entry_idx=day_idx, entry_underlying=o.get("entry"),
                                premium_pct=o.get("premium_pct"))
                positions.append(p)
                pos_sector[id(p)] = sector
                open_syms.add(sym_id)
                put_open += 1
                return True

            def _do_calls():
                eligible = [(sid, sc, k, ct, ern) for sid, sc, k, ct, ern in calls_by_date.get(today, [])
                            if k in call_outcomes and sid not in open_syms]
                primary = [e for e in eligible if e[1] >= mc.PRIMARY_THRESHOLD or e[3] == "ct_call"]
                overflow = [e for e in eligible if e[1] < mc.PRIMARY_THRESHOLD and e[3] != "ct_call"]
                primary.sort(key=lambda x: (0 if x[3] == "ct_call" else 1, -x[1], rng.random()))
                overflow.sort(key=lambda x: (-x[1], rng.random()))
                for sym_id, score, key, ct, ern in primary + overflow:
                    if len(positions) >= mc.MAX_POSITIONS:
                        break
                    if side_capped and call_open >= cap_call:
                        break
                    _try_fill_call(sym_id, score, key, ct, ern)

            def _do_puts():
                eligible = [(sid, sc, k, ct) for sid, sc, k, ct in puts_by_date.get(today, [])
                            if k in put_outcomes and sid not in open_syms]
                eligible.sort(key=lambda x: (0 if x[3] == "ct_put" else 1, x[1], rng.random()))
                for sym_id, score, key, ct in eligible:
                    if len(positions) >= mc.MAX_POSITIONS:
                        break
                    if side_capped and put_open >= cap_put:
                        break
                    _try_fill_put(sym_id, score, key, ct)

            if mc.DD_CIRCUIT_BREAKER > 0.0 and dd > mc.DD_CIRCUIT_BREAKER:
                if not positions:
                    peak_value = portfolio_value
                    dd = 0.0
                else:
                    continue

            if mc.PUT_PRIORITY == "puts_first":
                _do_puts()
                _do_calls()
            else:
                # Current production default. This experiment intentionally keeps
                # the sweep narrow; merged queues can be added if they become active.
                _do_calls()
                _do_puts()

        last_idx = len(trading_days) - 1
        last_day = trading_days[-1] if trading_days else None
        for p in positions:
            cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
            if p.side == "call":
                hard_c += 1
            else:
                hard_p += 1
            if collect_tape:
                trade_rows.append((
                    p.sym_id, p.entry_date, last_day, p.side, p.score, p.tier,
                    p.ct, p.ern, p.premium_cost, mc.NET_HARD_SELL, "hard",
                    p.entry_value, p.entry_peak, p.entry_dd,
                    p.entry_open_calls, p.entry_open_puts,
                ))
        portfolio_value = cash
        final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
        max_dd = max(max_dd, final_dd)
        if collect_tape:
            if cur_episode_trough_val < peak_value:
                episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                                 peak_value, cur_episode_trough_val))
            if portfolio_value < peak_value and last_idx >= 0:
                episodes.append((cur_episode_start_idx, last_idx, peak_value, portfolio_value))
        ct = tp_c + sl_c + hard_c or 1
        pt = tp_p + sl_p + hard_p or 1
        result = dict(
            final=portfolio_value,
            max_dd=max_dd,
            call_tp=tp_c / ct * 100,
            call_sl=sl_c / ct * 100,
            call_hard=hard_c / ct * 100,
            put_tp=tp_p / pt * 100,
            put_sl=sl_p / pt * 100,
            put_hard=hard_p / pt * 100,
            call_trades=tp_c + sl_c + hard_c,
            put_trades=tp_p + sl_p + hard_p,
        )
        if collect_tape:
            ranked = sorted(episodes, key=lambda e: (e[2] - e[3]) / e[2] if e[2] > 0 else 0, reverse=True)
            result["_tape"] = trade_rows
            result["_episodes"] = ranked[:3]
        return result

    return run_single_sim


def summarize(label: str, curve: str, results: list[dict]) -> dict:
    finals = [r["final"] for r in results]
    dds = [r["max_dd"] for r in results]
    ctps = [r["call_tp"] for r in results]
    ptps = [r["put_tp"] for r in results]
    ctrd = [r["call_trades"] for r in results]
    ptrd = [r["put_trades"] for r in results]

    def _pct(seq, q):
        seq = sorted(seq)
        if not seq:
            return None
        i = max(0, min(len(seq) - 1, int(round(q * (len(seq) - 1)))))
        return seq[i]

    return {
        "window": label,
        "curve": curve,
        "mean_ret": (statistics.mean(finals) / 10_000.0 - 1) * 100,
        "med_ret": (statistics.median(finals) / 10_000.0 - 1) * 100,
        "p25_ret": (_pct(finals, 0.25) / 10_000.0 - 1) * 100,
        "p75_ret": (_pct(finals, 0.75) / 10_000.0 - 1) * 100,
        "worst_dd": max(dds) * 100,
        "mean_dd": statistics.mean(dds) * 100,
        "med_dd": statistics.median(dds) * 100,
        "p_coll": sum(1 for f in finals if f <= 10_000.0 * 0.20) / len(finals) * 100,
        "call_tp": statistics.mean(ctps),
        "put_tp": statistics.mean(ptps),
        "call_trades": statistics.mean(ctrd),
        "put_trades": statistics.mean(ptrd),
        "n": len(results),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="V4")
    ap.add_argument("--windows", default="2022,2024,22-now,5y")
    ap.add_argument("--curves", default="V4_BASE,SWA_BOTH_SOFT,SWA_GROSS_SOFT,SWA_GROSS_BALANCED,SWA_PUT_PHASEMID_SOFT,SWA_PUT_RSI45_SOFT")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["MC_TRADE_TAPE"] = "0"
    os.environ["REALLOC_STRATEGY"] = ""

    import monte_carlo as mc

    if args.variant.upper() not in {"V4"}:
        raise SystemExit("this sweep currently supports --variant V4")

    lookup = build_lookup(args.variant)
    orig_call, orig_put = patch_signal_loaders(mc, lookup)
    orig_run_single = mc.run_single_sim

    sector_by_symbol, wave_by_key = load_sector_context()
    windows = [x.strip() for x in args.windows.split(",") if x.strip()]
    curves = [x.strip() for x in args.curves.split(",") if x.strip()]
    version = AlgorithmVersion.get_active_scores_version()
    wanted = {w for w in windows}
    mc_windows = [w for w in mc.WINDOWS if w[0] in wanted]
    rows = []
    out = Path(args.out).resolve()

    def flush_rows() -> None:
        if rows:
            out.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(rows).write_csv(out)

    try:
        for curve in curves:
            mc.run_single_sim = make_run_single_sim(curve, sector_by_symbol, wave_by_key)
            print(f"\n[curve] {curve}", flush=True)
            for label, d_start, d_end in mc_windows:
                t0 = time.time()
                row = dict(mc.run_window(label, d_start, d_end, version)["seeded"])
                row["window"] = label
                row["curve"] = curve
                row["n"] = args.n
                rows.append(row)
                flush_rows()
                print(
                    f"    mean={row['mean_ret']:+.1f}% med={row['med_ret']:+.1f}% "
                    f"worstDD={row['worst_dd']:.1f}% meanDD={row['mean_dd']:.1f}% "
                    f"calls={row['call_trades']:.1f} puts={row['put_trades']:.1f} "
                    f"elapsed={time.time() - t0:.0f}s",
                    flush=True,
                )
    finally:
        mc.load_signals = orig_call
        mc.load_put_signals = orig_put
        mc.run_single_sim = orig_run_single

    flush_rows()
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"\n[save] {shown} rows={len(rows):,}", flush=True)


if __name__ == "__main__":
    main()
