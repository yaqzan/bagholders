"""Sector ETF staging scoring sidecar.

This module is intentionally separate from production score calculation. It
lets `trader update` persist today's experimental sector-ETF adjusted scores
under an inactive staging version, while default dashboard/API readers keep
using the active production version.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


STAGING_VARIANT = "sector-etf"


SECTOR_ETF_MAP = {
    "technology": "XLK",
    "healthcare": "XLV",
    "consumer-cyclical": "XLY",
    "industrials": "XLI",
    "basic-materials": "XLB",
    "real-estate": "XLRE",
    "financial-services": "XLF",
    "energy": "XLE",
    "consumer-defensive": "XLP",
    "utilities": "XLU",
    "communication-services": "XLC",
}

# Tracked ETFs often do not have stock-sector metadata. For staging scoring, use
# the ETF itself as the sector reference and only fall back if its own features
# are unavailable.
ETF_SELF_REFERENCE_SYMBOLS = {
    "ARKQ", "ARKX", "ASHR", "BOIL", "DRAM", "EEM", "EWY", "EWZ", "FBTC", "FXI",
    "GLD", "HYG", "IAU", "IBIT", "IEF", "IGV", "IWM", "KWEB",
    "LABD", "QQQ", "SLV", "SMH", "SOXL", "SOXX", "SPY", "SVIX", "TLT",
    "TNA", "TQQQ", "UFO", "URA", "XLB", "XLC", "XLE", "XLF", "XLI",
    "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
}


@dataclass(frozen=True)
class SectorEtfParams:
    gate_lo_c: int = 70
    gate_hi_c: int = 84
    gate_lo_p: int = 16
    gate_hi_p: int = 25
    ema_k: float = 8.1919
    rsi_k: float = 22.7129
    macd_tanh_k: float = 0.83
    ema_w: float = 0.4028
    rsi_w: float = 0.5655
    macd_w: float = 0.0
    phase_power: float = 1.7660
    score_power: float = 1.3609
    alpha_down: float = 0.5606
    target_down: float = 57.3281
    alpha_up: float = 0.8971
    target_up: float = 90.7926
    alpha_put_down: float = 0.6088
    target_put: float = 30.3925
    alpha_put_up: float = 0.1508
    target_put_up: float = 12.7735
    rs_gate: float = 0.0647
    rs_k: float = 0.1252
    rs_power: float = 1.6429
    rs_score_power: float = 2.2108
    rs_alpha_c: float = 0.8667
    rs_target_c: float = 69.8427
    rs_alpha_p: float = 0.4730
    rs_target_p: float = 31.0075


PARAMS = SectorEtfParams()


def get_sector_etf_staging_version(active_version=None, create: bool = False):
    from database.models.core import AlgorithmVersion, StagingVersion

    if active_version is None:
        active_version = AlgorithmVersion.get_active_scores_version()
    StagingVersion.ensure_schema()
    if create:
        return StagingVersion.get_or_create_active(
            variant=STAGING_VARIANT,
            base_version=active_version,
            label="Sector ETF staging",
            notes=f"Inactive staging score version for {STAGING_VARIANT}",
        )
    return StagingVersion.get_active(variant=STAGING_VARIANT, base_version=active_version)


def _finite(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _phase_component(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return max(-1.0, min(1.0, value / scale))


def _sector_phase(sec_pct_ema50, sec_rsi, sec_macd, p: SectorEtfParams = PARAMS) -> float | None:
    p1 = _phase_component(_finite(sec_pct_ema50), p.ema_k)
    rsi = _finite(sec_rsi)
    p2 = max(-1.0, min(1.0, (rsi - 50.0) / p.rsi_k)) if rsi is not None else None
    macd = _finite(sec_macd)
    p3 = max(-1.0, min(1.0, math.tanh(macd / p.macd_tanh_k))) if macd is not None else None

    weighted = 0.0
    total = 0.0
    for component, weight in ((p1, p.ema_w), (p2, p.rsi_w), (p3, p.macd_w)):
        if component is not None and weight > 0:
            weighted += component * weight
            total += weight
    if total <= 0:
        return None
    return weighted / total


def apply_sector_etf_overlay(
    overall: int | float,
    *,
    sec_pct_ema50=None,
    sec_rsi=None,
    sec_macd=None,
    stock_ret_5d=None,
    sector_ret_5d=None,
    p: SectorEtfParams = PARAMS,
) -> tuple[int, dict[str, Any]]:
    """Return V4 sector ETF staging score plus compact diagnostic metadata."""
    base = _finite(overall)
    if base is None:
        return overall, {"reason": "missing_overall"}

    score = float(base)
    phase = _sector_phase(sec_pct_ema50, sec_rsi, sec_macd, p)
    if phase is not None:
        call_sn = max(0.0, min(1.0, (score - p.gate_lo_c) / (p.gate_hi_c - p.gate_lo_c))) ** p.score_power
        put_sn = max(0.0, min(1.0, (p.gate_hi_p - score) / (p.gate_hi_p - p.gate_lo_p))) ** p.score_power
        pos_phase = max(0.0, min(1.0, phase)) ** p.phase_power
        neg_phase = max(0.0, min(1.0, -phase)) ** p.phase_power

        delta = 0.0
        if p.gate_lo_c <= score <= p.gate_hi_c:
            if phase > 0:
                delta = -p.alpha_down * pos_phase * call_sn * (score - p.target_down)
            elif phase < 0:
                delta = p.alpha_up * neg_phase * call_sn * (p.target_up - score)
        elif p.gate_lo_p <= score <= p.gate_hi_p:
            if phase < 0:
                delta = p.alpha_put_down * neg_phase * put_sn * (p.target_put - score)
            elif phase > 0:
                delta = -p.alpha_put_up * pos_phase * put_sn * (score - p.target_put_up)
        score += delta

    stock_ret = _finite(stock_ret_5d)
    sector_ret = _finite(sector_ret_5d)
    rs5 = None
    if stock_ret is not None and sector_ret is not None:
        rs5 = stock_ret - sector_ret
        excess = max(0.0, abs(rs5) - p.rs_gate)
        weakness = max(0.0, min(1.0, excess / p.rs_k)) ** p.rs_power

        call_sn = max(0.0, min(1.0, (score - p.gate_lo_c) / (p.gate_hi_c - p.gate_lo_c))) ** p.rs_score_power
        put_sn = max(0.0, min(1.0, (p.gate_hi_p - score) / (p.gate_hi_p - p.gate_lo_p))) ** p.rs_score_power
        if p.gate_lo_c <= score <= p.gate_hi_c:
            score += -p.rs_alpha_c * weakness * call_sn * (score - p.rs_target_c)
        elif p.gate_lo_p <= score <= p.gate_hi_p:
            score += p.rs_alpha_p * weakness * put_sn * (p.rs_target_p - score)

    new_score = int(max(0, min(100, round(score))))
    meta = {
        "variant": STAGING_VARIANT,
        "base_overall": int(round(base)),
        "staging_overall": new_score,
        "sec_phase": round(phase, 4) if phase is not None else None,
        "stock_rs_5d": round(rs5, 6) if rs5 is not None else None,
    }
    return new_score, meta


def _ret_5d_map(symbols: set[str], target_date: date) -> dict[str, float | None]:
    from database.models.technical import PriceHistory

    if not symbols:
        return {}
    cutoff = target_date - timedelta(days=30)
    rows = (
        PriceHistory
        .select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close)
        .where(
            (PriceHistory.symbol.in_(symbols))
            & (PriceHistory.date <= target_date)
            & (PriceHistory.date >= cutoff)
        )
        .order_by(PriceHistory.symbol.asc(), PriceHistory.date.desc())
    )
    grouped: dict[str, list[float]] = {}
    for r in rows:
        grouped.setdefault(r.symbol_id, []).append(float(r.close))
    out = {}
    for sym in symbols:
        closes = grouped.get(sym) or []
        out[sym] = (closes[0] / closes[5] - 1.0) if len(closes) >= 6 and closes[5] else None
    return out


def _latest_indicator_map(symbols: set[str], target_date: date) -> dict[str, Any]:
    from database.models.technical import Indicator

    if not symbols:
        return {}
    rows = (
        Indicator
        .select(Indicator.symbol, Indicator.date, Indicator.rsi, Indicator.macd, Indicator.ema_50)
        .where((Indicator.symbol.in_(symbols)) & (Indicator.date <= target_date))
        .order_by(Indicator.symbol.asc(), Indicator.date.desc())
    )
    out = {}
    for r in rows:
        out.setdefault(r.symbol_id, r)
    return out


def _latest_close_map(symbols: set[str], target_date: date) -> dict[str, float | None]:
    from database.models.technical import PriceHistory

    if not symbols:
        return {}
    rows = (
        PriceHistory
        .select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close)
        .where((PriceHistory.symbol.in_(symbols)) & (PriceHistory.date <= target_date))
        .order_by(PriceHistory.symbol.asc(), PriceHistory.date.desc())
    )
    out = {}
    for r in rows:
        out.setdefault(r.symbol_id, float(r.close))
    return out


def _merge_weight_info(existing: str | None, staging_meta: dict[str, Any]) -> str:
    try:
        data = json.loads(existing) if existing else {}
        if not isinstance(data, dict):
            data = {"_raw": existing}
    except Exception:
        data = {"_raw": existing}
    data["_staging_sector_etf"] = staging_meta
    return json.dumps(data, separators=(",", ":"))


def _score_symbol(score) -> str:
    sym = getattr(score, "symbol_id", None)
    if sym:
        return str(sym)
    stock = getattr(score, "symbol", None)
    stock_sym = getattr(stock, "symbol", None)
    if stock_sym:
        return str(stock_sym)
    return str(stock)


def write_today_sector_etf_staging_scores(target_date: date | None = None, verbose: bool = True) -> dict[str, Any]:
    """Upsert today's sector ETF staging scores.

    Only active scores for `target_date` are copied. Missing sector features do
    not block coverage; the active score is copied unchanged and marked in
    weight_info.
    """
    from database.models.core import AlgorithmVersion, Score, Stock, StagingScore
    from database.trader_database import DB

    if target_date is None:
        target_date = date.today()

    active_version = AlgorithmVersion.get_active_scores_version()
    staging_version = get_sector_etf_staging_version(active_version, create=True)
    active_scores = list(
        Score
        .select(Score, Stock.symbol, Stock.sector)
        .join(Stock, on=(Score.symbol == Stock.symbol))
        .where(
            (Score.version == active_version)
            & (Score.date == target_date)
            & Score.overall.is_null(False)
        )
    )
    if not active_scores:
        if verbose:
            print(f"Sector ETF staging: no active scores for {target_date}; skipped")
        return {
            "date": target_date,
            "active_version_id": active_version.id,
            "staging_version_id": staging_version.id,
            "written": 0,
            "changed": 0,
            "fallback": 0,
            "missing": len(active_scores),
        }

    sectors_by_symbol = {_score_symbol(s): (getattr(s.symbol, "sector", None) or "unknown") for s in active_scores}
    etfs = {SECTOR_ETF_MAP[sec] for sec in sectors_by_symbol.values() if sec in SECTOR_ETF_MAP}
    etfs |= {sym for sym in sectors_by_symbol if sym in ETF_SELF_REFERENCE_SYMBOLS}
    symbols = set(sectors_by_symbol) | etfs
    ret5 = _ret_5d_map(symbols, target_date)
    ind = _latest_indicator_map(etfs, target_date)
    close = _latest_close_map(etfs, target_date)

    rows = []
    changed = 0
    fallback = 0
    etf_self_reference = 0
    now = datetime.now()

    for s in active_scores:
        sym = _score_symbol(s)
        sector = sectors_by_symbol.get(sym, "unknown")
        etf = SECTOR_ETF_MAP.get(sector)
        if etf is None and sym in ETF_SELF_REFERENCE_SYMBOLS:
            etf = sym
            sector = f"etf:{sym}"
            etf_self_reference += 1
        etf_ind = ind.get(etf) if etf else None
        etf_close = close.get(etf) if etf else None
        etf_ema50 = _finite(etf_ind.ema_50) if etf_ind else None
        sec_pct_ema50 = ((etf_close - etf_ema50) / etf_ema50 * 100.0) if etf_close and etf_ema50 else None
        if (
            etf is None
            or etf_ind is None
            or sec_pct_ema50 is None
            or _finite(etf_ind.rsi if etf_ind else None) is None
            or ret5.get(sym) is None
            or ret5.get(etf) is None
        ):
            new_overall = int(s.overall)
            meta = {
                "variant": STAGING_VARIANT,
                "base_overall": int(s.overall),
                "staging_overall": int(s.overall),
                "sector": sector,
                "sector_etf": etf,
                "reason": "missing_features",
            }
            fallback += 1
        else:
            new_overall, meta = apply_sector_etf_overlay(
                int(s.overall),
                sec_pct_ema50=sec_pct_ema50,
                sec_rsi=etf_ind.rsi,
                sec_macd=etf_ind.macd,
                stock_ret_5d=ret5.get(sym),
                sector_ret_5d=ret5.get(etf),
            )
            meta["sector"] = sector
            meta["sector_etf"] = etf
            if new_overall != int(s.overall):
                changed += 1

        rows.append((
            sym, s.date, staging_version.id,
            s.bb, s.trend, s.volume, s.rsi, s.macd, s.stoch,
            s.ma20, s.high_30, s.high_60, s.high_90,
            s.high_30_days, s.high_60_days, s.high_90_days,
            s.high_30_biggest_drop, s.high_60_biggest_drop, s.high_90_biggest_drop,
            s.technical_alignment, new_overall, now,
            s.daily_change, s.price, s.price_target_growth, s.next_earnings,
            s.price_target, s.growth_score, s.volume_signal, s.volume_magnitude,
            s.pct_from_ema50, s.pct_from_ema200, s.bb_position,
            s.score_velocity_7d, s.regime_composite, s.regime_multiplier,
            _merge_weight_info(s.weight_info, meta),
        ))

    cols = ["symbol", "date", "staging_version_id", *StagingScore.score_column_names()]
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in {"symbol", "date", "staging_version_id"})
    sql = (
        f"INSERT INTO `staging_scores` ({', '.join(f'`{c}`' for c in cols)}) "
        f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
    )
    with DB.atomic():
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            sql_b = sql.replace(
                f"VALUES ({placeholders})",
                "VALUES " + ", ".join([f"({placeholders})"] * len(batch)),
            )
            DB.execute_sql(sql_b, [v for row in batch for v in row])

    result = {
        "date": target_date,
        "active_version_id": active_version.id,
        "staging_version_id": staging_version.id,
        "written": len(rows),
        "changed": changed,
        "fallback": fallback,
        "etf_self_reference": etf_self_reference,
    }
    if verbose:
        print(
            f"Sector ETF staging scores: {len(rows)} written for {target_date} "
            f"(changed={changed}, fallback={fallback}, "
            f"etf_self_reference={etf_self_reference}, staging=s{staging_version.id})"
        )
    return result
