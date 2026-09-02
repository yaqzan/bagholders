"""Phase F — build exact active-stack feature cache at ICH entry.

This is the fast-sweep substrate for ICH put-side recalibration. It runs the
normal simulator path once, but instruments compute_overall_score in memory to
capture the score immediately before the ICH block plus the downstream context
needed to replay candidate ICH variants through PESS and EARN_BOOST.

No production files are modified.
"""
from __future__ import annotations

import inspect
import io
import json
import sqlite3
import sys
import textwrap
import time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / ".cache" / "ich_put_recal"
OUT_PATH = CACHE_DIR / "phase_f_active_ich_entry_v46_1825d.parquet"
RAW_PATH = CACHE_DIR / "phase_f_active_ich_entry_raw_v46_1825d.parquet"
LOOKBACK_DAYS = 1825
W_DAYS_LIST = (7, 15, 30)


def _s(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)[:10]


def _window():
    from experiments._holdout import CUTOFF

    end = min(date.today(), CUTOFF)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start, end


def _build_instrumented_compute(record_list):
    """Return compute_overall_score variant that records ICH-entry state."""
    import database.utils.scoring as scoring

    src = inspect.getsource(scoring.compute_overall_score)
    src = textwrap.dedent(src)

    marker = "    _ich_call_d = 0.0\n    _ich_put_l  = 0.0\n"
    inject = (
        "    _ich_entry_overall = overall\n"
        "    _ich_entry_kijun_pct = kijun_pct\n"
        "    _ich_entry_days_to_earnings = days_to_earnings\n"
        + marker
    )
    if src.count(marker) != 1:
        raise RuntimeError("could not locate ICH entry marker in compute_overall_score")
    src = src.replace(marker, inject)

    ret_marker = "    return overall, weight_info, vol_update\n"
    ret_inject = (
        "    _rec = globals().get('_ICH_ACTIVE_RECORDS')\n"
        "    _key = globals().get('_ICH_ACTIVE_CURRENT_KEY')\n"
        "    if _rec is not None and _key is not None:\n"
        "        _rec.append({\n"
        "            'symbol': _key[0],\n"
        "            'date': _key[1].isoformat() if hasattr(_key[1], 'isoformat') else str(_key[1])[:10],\n"
        "            'pre_ich': int(_ich_entry_overall) if _ich_entry_overall is not None else None,\n"
        "            'kijun_pct': float(_ich_entry_kijun_pct) if _ich_entry_kijun_pct is not None else None,\n"
        "            'days_to_earnings': int(_ich_entry_days_to_earnings) if _ich_entry_days_to_earnings is not None else None,\n"
        "            'old_ich_put_lift': float(_ich_put_l),\n"
        "            'old_pess_lift': float(_pess_lift),\n"
        "            'old_ern_boost': float(_ern_boost_strength),\n"
        "            'old_final': int(overall) if overall is not None else None,\n"
        "        })\n"
        + ret_marker
    )
    if src.count(ret_marker) != 1:
        raise RuntimeError("could not locate return marker in compute_overall_score")
    src = src.replace(ret_marker, ret_inject)

    ns = dict(scoring.__dict__)
    ns["_ICH_ACTIVE_RECORDS"] = record_list
    ns["_ICH_ACTIVE_CURRENT_KEY"] = None
    exec(src, ns)
    return ns["compute_overall_score"], ns


def _run_instrumented_simulation(start: date) -> pl.DataFrame:
    import database.utils.scoring as scoring
    from simulator import ScoreSimulator

    records: list[dict] = []
    instrumented, ns = _build_instrumented_compute(records)

    original = scoring.compute_overall_score
    scoring.compute_overall_score = instrumented
    t0 = time.time()
    try:
        sim = ScoreSimulator(symbols=None, lookback_days=LOOKBACK_DAYS)

        # Copy of ScoreSimulator.simulate with one addition: set current key
        # before each compute_overall_score call so the instrumented scorer can
        # attach (symbol, date) to the ICH-entry record.
        from simulator import _week_start
        from volume_amplifier import get_volume_multiplier_from_cache

        cutoff = start
        results = {}
        for sym, ctx in sim._contexts.items():
            stock = ctx.stock
            prior_vol_signals = {}
            for ind_row in ctx.ind_rows:
                d = ind_row.date
                if d < cutoff:
                    continue

                ind_cache = ctx.ind_cache_for(d)
                trend = stock.calculate_trend_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                if trend is None:
                    continue

                ph_sim = ctx.ph_map.get(d)
                pct_ema_sim = (
                    (float(ph_sim.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100
                    if ph_sim and ind_row.ema_50 is not None else None
                )
                rsi = stock.calculate_rsi_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map,
                    macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                    pct_from_ema50=pct_ema_sim)
                bb = stock.calculate_bollinger_bands_score(
                    d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                macd = stock.calculate_macd_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                stoch = stock.calculate_stoch_score(
                    d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
                if None in (rsi, bb, macd, stoch):
                    continue

                scores_list = [bb, rsi, macd]
                total = len(scores_list)
                bullish = sum(s >= 50 for s in scores_list)
                bearish = total - bullish
                agreement = max(bullish, bearish) / total
                avg_dist = sum(s - 50 for s in scores_list) / total
                sig_str = min(abs(avg_dist) / 50, 1.0)
                turbo = 1 + 0.5 * sig_str * agreement
                ta = round(max(0, min(100, 50 + avg_dist * sig_str * (agreement ** 1.5) * turbo)))

                ws = ctx.ws_map.get(_week_start(d))
                pws = ctx.ws_map.get(_week_start(d) - timedelta(weeks=1))
                ws_rsi = ws.rsi if ws and ws.rsi and ws.macd else None
                ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

                ph_slice = ctx.ph_slice_desc_for(d)
                vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = (
                    get_volume_multiplier_from_cache(
                        d, ph_slice, prior_vol_signals, ctx.earnings_set)
                )

                bb_pct = None
                if None not in (ind_row.upper_band, ind_row.lower_band):
                    bw = float(ind_row.upper_band) - float(ind_row.lower_band)
                    ph = ctx.ph_map.get(d)
                    if bw > 0 and ph:
                        bb_pct = (float(ph.close) - float(ind_row.lower_band)) / bw

                r_mult = sim._regime_map.get(d)
                mis_stress = sim._mis_stress_map.get(d, 0.0)

                pct_ema50 = None
                ph = ctx.ph_map.get(d)
                if ind_row.ema_50 and ph and ph.close:
                    pct_ema50 = (float(ph.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100

                d_to_ern = None
                if ctx.earnings_set:
                    for delta in range(6):
                        if (d + timedelta(days=delta)) in ctx.earnings_set:
                            d_to_ern = delta
                            break

                ns["_ICH_ACTIVE_CURRENT_KEY"] = (sym, d)
                overall, _, _ = instrumented(
                    trend, bb, rsi, macd, stoch, ta,
                    bb_pct=bb_pct,
                    pct_from_ema50=pct_ema50,
                    ws_trend=ws.trend if ws else None,
                    ws_rsi=ws_rsi, ws_macd=ws_macd,
                    prev_ws_trend=pws.trend if pws else None,
                    prev_ws_rsi=pws.rsi if pws else None,
                    prev_ws_macd=pws.macd if pws else None,
                    vol_mult=vol_mult, vol_raw=vol_raw,
                    vol_sig=vol_sig, vol_mag=vol_mag,
                    blend_w=blend_w, vol_target=vol_target,
                    macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                    regime_multiplier=r_mult,
                    mis_stress=mis_stress,
                    days_to_earnings=d_to_ern,
                    ret_10d_sigma=ctx.ret10d_sigma_map.get(d),
                    mcap_b=ctx.mcap_b,
                    kijun_pct=ctx.kijun_pct_map.get(d),
                    wv_force1=ctx.wv_force1_map.get(d),
                )
                ns["_ICH_ACTIVE_CURRENT_KEY"] = None
                if overall is None:
                    continue
                results[(sym, d)] = overall
                prior_vol_signals[d] = (vol_sig, vol_mag, vol_raw)

        print(f"[instrumented] scored {len(results):,} rows; records={len(records):,} ({time.time() - t0:.1f}s)")
    finally:
        scoring.compute_overall_score = original

    df = pl.DataFrame(records)
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_f_active_cache records")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(RAW_PATH)
    print(f"[cache] wrote raw checkpoint {RAW_PATH}: {len(df):,} rows")
    return df


def _join_baseline_db(df: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    cur = DB.execute_sql(
        """
        SELECT symbol, date, overall
        FROM scores
        WHERE version_id = %s
          AND date >= %s
          AND date <= %s
        """,
        (av.id, start.isoformat(), end.isoformat()),
    )
    rows = cur.fetchall()
    base = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [_s(r[1]) for r in rows],
            "db_final": [int(r[2]) if r[2] is not None else None for r in rows],
        }
    ).filter(pl.col("db_final").is_not_null())
    out = df.join(base, on=["symbol", "date"], how="left")
    mismatch = out.filter(
        pl.col("db_final").is_not_null() & (pl.col("old_final") != pl.col("db_final"))
    )
    print(f"[validate] simulator old_final vs DB mismatches: {len(mismatch):,} / {len(out):,}")
    if len(mismatch) > 0:
        print(mismatch.select(["symbol", "date", "old_final", "db_final"]).head(5))
    return out


def _join_barriers(df: pl.DataFrame) -> pl.DataFrame:
    symbols = sorted(df["symbol"].unique().to_list())
    placeholders = ",".join("?" for _ in symbols)
    conn = sqlite3.connect(str(ROOT / ".cache" / "barrier_outcomes.db"))
    try:
        for w in W_DAYS_LIST:
            cur = conn.execute(
                f"""
                SELECT symbol, date, result
                FROM barrier_outcomes
                WHERE side = 'high'
                  AND w_days = {w}
                  AND barrier_set = '30dte_generic'
                  AND symbol IN ({placeholders})
                """,
                symbols,
            )
            rows = cur.fetchall()
            bo = pl.DataFrame(
                {
                    "symbol": [r[0] for r in rows],
                    "date": [_s(r[1]) for r in rows],
                    f"wr{w}": [int(r[2]) if r[2] is not None else None for r in rows],
                }
            ).filter(pl.col(f"wr{w}").is_not_null())
            df = df.join(bo, on=["symbol", "date"], how="left")
            print(f"[barrier] W={w}: {df[f'wr{w}'].is_not_null().sum():,} non-null")
    finally:
        conn.close()
    return df


def main():
    if OUT_PATH.exists():
        df = pl.read_parquet(OUT_PATH)
        print(f"[cache] hit {OUT_PATH}: {len(df):,} rows")
        return

    start, end = _window()
    print(f"[window] {start.isoformat()} .. {end.isoformat()} (holdout enforced)")
    if RAW_PATH.exists():
        df = pl.read_parquet(RAW_PATH)
        print(f"[cache] hit raw checkpoint {RAW_PATH}: {len(df):,} rows")
    else:
        df = _run_instrumented_simulation(start)
    df = _join_baseline_db(df, start, end)
    df = _join_barriers(df)

    # Keep enough context for candidate admission/removal. A lifted put cannot
    # move down into the book, so pre_ich <= 30 plus current DB puts <= 30 covers
    # the active put region with buffer.
    df = df.filter((pl.col("pre_ich") <= 35) | (pl.col("db_final") <= 35))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    meta = {
        "rows": len(df),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "created_at": date.today().isoformat(),
        "path": str(OUT_PATH),
    }
    (CACHE_DIR / "phase_f_active_ich_entry_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[cache] wrote {OUT_PATH}: {len(df):,} rows")


if __name__ == "__main__":
    main()
