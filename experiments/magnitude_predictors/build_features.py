"""Build stock-magnitude feature parquet.

Question:
  Given a score signal already fired, can local indicators explain HOW FAR the
  stock tends to move, independently of the existing score bucket?

This builder is intentionally options-agnostic. It uses the generic
barrier_outcomes cache only as a convenient source of forward path metrics:
MFE/MAE/exit_return plus the signal-date realized volatility used to express
those moves in sigma units.

Output:
  .cache/magnitude_predictors/signals_v{active_id}_{lookback}.parquet
"""
from __future__ import annotations

import io
import json
import math
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter

CACHE_DB = ROOT / ".cache" / "barrier_outcomes.db"
CACHE_DUCK = ROOT / ".cache" / "barrier_outcomes.duckdb"
OUT_DIR = ROOT / ".cache" / "magnitude_predictors"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOOKBACK_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
PERIODS = (3, 5, 7, 15, 30)
WEIGHT_INFO_FIELDS = (
    "pre_regime",
    "pre_boost",
    "td",
    "w_comp",
    "w_bias",
    "w_mom",
    "w_adj",
    "mcd_dampen",
    "mcd_mcap_b",
    "ern_boost",
    "days_to_ern",
    "mis_stress",
    "cswc_dampen",
    "scw_dampen",
    "put_regime_mult",
)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _parse_weight_info(raw: str | None) -> dict[str, float | None]:
    out = {f"wi_{k}": None for k in WEIGHT_INFO_FIELDS}
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except Exception:
        return out
    for key in WEIGHT_INFO_FIELDS:
        out[f"wi_{key}"] = _f(data.get(key))
    return out


def _query_signal_rows(av_id: int, cutoff: str) -> pl.DataFrame:
    from database.trader_database import DB

    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=300000")
    rows: list[dict[str, Any]] = []
    start_year = int(cutoff[:4])
    end_year = date.today().year + 1

    print(f"[build] querying score/indicator rows since {cutoff}...", flush=True)
    for yr in range(start_year, end_year):
        y_start = f"{yr}-01-01" if yr != start_year else cutoff
        y_end = f"{yr}-12-31"
        t0 = time.time()
        cur = DB.execute_sql(
            f"""
            SELECT
                s.symbol, s.date, s.overall,
                s.bb, s.trend, s.volume, s.rsi, s.macd, s.stoch,
                s.technical_alignment,
                s.volume_magnitude, s.pct_from_ema50, s.pct_from_ema200,
                s.bb_position, s.score_velocity_7d,
                s.regime_composite, s.regime_multiplier, s.weight_info,
                st.sector, st.industry, st.market_cap,
                i.rsi, i.stoch, i.stoch_signal, i.macd_hist,
                i.ema_50, i.ema_200,
                ph.close
            FROM scores s
            LEFT JOIN stocks st
              ON st.symbol = s.symbol
            LEFT JOIN indicators i
              ON i.symbol = s.symbol AND i.date = s.date
            LEFT JOIN price_history ph
              ON ph.symbol = s.symbol AND ph.date = s.date
            WHERE s.version_id = {int(av_id)}
              AND s.date >= '{y_start}' AND s.date <= '{y_end}'
              AND s.overall IS NOT NULL
              AND (s.overall >= 70 OR s.overall <= 25)
            """
        )
        chunk = cur.fetchall()
        print(f"[build]   {yr}: {len(chunk):,} rows ({time.time() - t0:.1f}s)", flush=True)
        for r in chunk:
            (
                sym, d, overall,
                bb, trend, volume, rsi_score, macd_score, stoch_score,
                ta,
                volume_magnitude, pct_ema50, pct_ema200,
                bb_position, score_velocity_7d,
                regime_composite, regime_multiplier, weight_info,
                sector, industry, market_cap,
                rsi_raw, stoch_raw, stoch_signal, macd_hist,
                ema_50, ema_200,
                close,
            ) = r
            row = {
                "symbol": sym,
                "date": d.isoformat() if hasattr(d, "isoformat") else d,
                "overall": int(overall),
                "side": "call" if int(overall) >= 70 else "put",
                "bb_score": _f(bb),
                "trend_score": _f(trend),
                "volume_score": _f(volume),
                "rsi_score": _f(rsi_score),
                "macd_score": _f(macd_score),
                "stoch_score": _f(stoch_score),
                "ta_score": _f(ta),
                "volume_magnitude": _f(volume_magnitude),
                "pct_from_ema50": _f(pct_ema50),
                "pct_from_ema200": _f(pct_ema200),
                "bb_position": _f(bb_position),
                "score_velocity_7d": _f(score_velocity_7d),
                "regime_composite": _f(regime_composite),
                "regime_multiplier": _f(regime_multiplier),
                "sector": sector or "unknown",
                "industry": industry or "unknown",
                "market_cap": _f(market_cap),
                "mcap_b": _f(market_cap) / 1e9 if _f(market_cap) is not None else None,
                "rsi_raw": _f(rsi_raw),
                "stoch_raw": _f(stoch_raw),
                "stoch_signal": _f(stoch_signal),
                "macd_hist": _f(macd_hist),
                "ema_50": _f(ema_50),
                "ema_200": _f(ema_200),
                "close": _f(close),
            }
            row.update(_parse_weight_info(weight_info))
            rows.append(row)

    df = pl.DataFrame(rows, infer_schema_length=None)
    print(f"[build] signal rows: {len(df):,}", flush=True)
    return df


def _query_breadth_regime(cutoff: str) -> pl.DataFrame:
    from database.trader_database import DB

    cur = DB.execute_sql(
        f"""
        SELECT
            mb.date,
            mb.breadth_score, mb.mcclellan_oscillator,
            mb.pct_above_ema50, mb.pct_above_ema200,
            mb.trin, mb.ad_ratio,
            mr.vix_close, mr.vix_10d_change, mr.vix_score,
            mr.market_trend_score, mr.credit_spread_score,
            mr.haven_score, mr.skew_score, mr.fg_composite
        FROM market_breadth mb
        LEFT JOIN market_regime mr
          ON mr.date = mb.date
        WHERE mb.date >= '{cutoff}'
        """
    )
    rows = cur.fetchall()
    return pl.DataFrame(
        {
            "date": [r[0].isoformat() if hasattr(r[0], "isoformat") else r[0] for r in rows],
            "breadth_score": [_f(r[1]) for r in rows],
            "mcclellan_osc": [_f(r[2]) for r in rows],
            "pct_above_ema50_mkt": [_f(r[3]) for r in rows],
            "pct_above_ema200_mkt": [_f(r[4]) for r in rows],
            "trin": [_f(r[5]) for r in rows],
            "ad_ratio": [_f(r[6]) for r in rows],
            "vix_close": [_f(r[7]) for r in rows],
            "vix_10d_change": [_f(r[8]) for r in rows],
            "vix_score": [_f(r[9]) for r in rows],
            "market_trend_score": [_f(r[10]) for r in rows],
            "credit_spread_score": [_f(r[11]) for r in rows],
            "haven_score": [_f(r[12]) for r in rows],
            "skew_score": [_f(r[13]) for r in rows],
            "fg_composite": [_f(r[14]) for r in rows],
        }
    )


def _query_barriers(signals: pl.DataFrame, cutoff: str) -> pl.DataFrame:
    w_csv = ",".join(str(w) for w in PERIODS)
    keys_path = OUT_DIR / "_signal_keys.parquet"
    (
        signals.select(["symbol", "date", "barrier_side"])
        .unique()
        .write_parquet(keys_path)
    )

    if CACHE_DUCK.exists():
        import duckdb

        con = duckdb.connect(str(CACHE_DUCK), read_only=True)
        barriers = con.execute(
            f"""
            SELECT b.symbol, b.date, b.side, b.w_days, b.result, b.exit_return,
                   b.mae_pct, b.mfe_pct, b.sigma_pct
            FROM read_parquet('{keys_path.as_posix()}') k
            JOIN barrier_outcomes b
              ON b.symbol = k.symbol
             AND b.date = k.date
             AND b.side = k.barrier_side
            WHERE b.barrier_set = '30dte_generic'
              AND b.date >= '{cutoff}'
              AND b.w_days IN ({w_csv})
            """
        ).pl()
        con.close()
    else:
        conn = sqlite3.connect(str(CACHE_DB))
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_signal_keys")
        cur.execute("CREATE TEMP TABLE temp_signal_keys (symbol TEXT, date TEXT, side TEXT)")
        cur.executemany(
            "INSERT INTO temp_signal_keys VALUES (?, ?, ?)",
            signals.select(["symbol", "date", "barrier_side"]).unique().iter_rows(),
        )
        cur.execute("CREATE INDEX temp_signal_keys_idx ON temp_signal_keys(symbol, date, side)")
        conn.commit()
        barriers = pl.read_database(
            f"""
            SELECT b.symbol, b.date, b.side, b.w_days, b.result, b.exit_return,
                   b.mae_pct, b.mfe_pct, b.sigma_pct
            FROM temp_signal_keys k
            JOIN barrier_outcomes b
              ON b.symbol = k.symbol
             AND b.date = k.date
             AND b.side = k.side
            WHERE b.barrier_set = '30dte_generic'
              AND b.date >= '{cutoff}'
              AND b.w_days IN ({w_csv})
            """,
            connection=conn,
        )
        conn.close()

    barriers = barriers.with_columns(pl.col("date").cast(pl.Utf8))
    print(f"[build] generic barrier rows: {len(barriers):,}", flush=True)

    wide = barriers.pivot(
        index=["symbol", "date", "side"],
        on="w_days",
        values=["result", "exit_return", "mae_pct", "mfe_pct", "sigma_pct"],
        aggregate_function="first",
    )
    return wide


def main() -> None:
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    print(f"[build] active version: v{av.id} ({av.git_commit[:8]})", flush=True)
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    t0 = time.time()
    signals = _query_signal_rows(av.id, cutoff)
    signals = signals.with_columns(
        pl.when(pl.col("side") == "call").then(pl.lit("low")).otherwise(pl.lit("high")).alias("barrier_side")
    )
    breadth = _query_breadth_regime(cutoff)
    barriers = _query_barriers(signals, cutoff)
    df = signals.join(breadth, on="date", how="left")
    df = df.join(
        barriers,
        left_on=["symbol", "date", "barrier_side"],
        right_on=["symbol", "date", "side"],
        how="left",
    )

    for w in PERIODS:
        df = df.with_columns(
            [
                (pl.col(f"mfe_pct_{w}") / pl.col(f"sigma_pct_{w}")).alias(f"mfe_sigma_{w}"),
                (pl.col(f"mae_pct_{w}") / pl.col(f"sigma_pct_{w}")).alias(f"mae_sigma_{w}"),
                (pl.col(f"exit_return_{w}") / pl.col(f"sigma_pct_{w}")).alias(f"exit_return_sigma_{w}"),
            ]
        )

    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, "magnitude_predictors/build_features")

    out = OUT_DIR / f"signals_v{av.id}_{LOOKBACK_DAYS}.parquet"
    df.write_parquet(out)

    n_resolved = df.filter(pl.col("mfe_sigma_15").is_not_null()).height
    print(f"[build] wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)
    print(f"[build] rows={len(df):,} | rows with W15 magnitude={n_resolved:,}", flush=True)
    print(f"[build] elapsed {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
