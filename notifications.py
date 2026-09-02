"""Push notification relay for Trader signal events.

This module intentionally sits outside scoring. It observes completed score
writes, compares them to the previous observed score, and sends only threshold
crossing events.
"""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from colorama import Fore
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    FloatField,
    IntegerField,
    TextField,
    fn,
)

from trader_database import BaseModel
from database.trader_database import DB
from database.models.core import AlgorithmVersion, MarketBreadth, Score, Stock
from database.models.technical import PriceHistory


DEFAULT_PUSHOVER_USER_KEY = ""  # set PUSHOVER_USER_KEY
DEFAULT_PUSHOVER_APP_TOKEN = ""  # set PUSHOVER_APP_TOKEN
PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"

CALL_GATE = 80
PUT_GATE = 10
URGENT_CALL_GATE = 85
URGENT_PUT_GATE = 10
TAKE_PROFIT_MARKER = "💰"
STOP_LOSS_MARKER = "🛑"
VOL_BARS = 60


class NotificationScoreState(BaseModel):
    """Last score observed by the notification layer for one score row."""

    id = AutoField(primary_key=True)
    symbol = CharField(max_length=16)
    score_date = DateField()
    version_id = IntegerField()
    last_score = IntegerField(null=True)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "notification_score_state"
        indexes = (
            (("symbol", "score_date", "version_id"), True),
        )


class NotificationEvent(BaseModel):
    """Audit/dedupe ledger for pushed or queued notification events."""

    id = AutoField(primary_key=True)
    event_key = CharField(max_length=160, unique=True)
    event_type = CharField(max_length=32)
    symbol = CharField(max_length=16)
    side = CharField(max_length=8)
    score_date = DateField()
    version_id = IntegerField()
    previous_score = IntegerField(null=True)
    score = IntegerField()
    price = FloatField(null=True)
    allocation_pct = FloatField(null=True)
    threshold = IntegerField()
    immediate = BooleanField(default=False)
    message = TextField(null=True)
    relay_status = CharField(max_length=32, default="pending")
    relay_response = TextField(null=True)
    created_at = DateTimeField(default=datetime.now)
    sent_at = DateTimeField(null=True)

    class Meta:
        table_name = "notification_events"
        indexes = (
            (("score_date", "version_id"), False),
            (("symbol", "score_date", "version_id"), False),
        )


class BuyingOpportunity(BaseModel):
    """Running ledger of alertable entries for later TP/SL tracking."""

    id = AutoField(primary_key=True)
    opportunity_key = CharField(max_length=220, unique=True)
    source_event_key = CharField(max_length=220, null=True, unique=True)
    symbol = CharField(max_length=16)
    side = CharField(max_length=8)
    status = CharField(max_length=16, default="open")
    opening_reason = CharField(max_length=32, default="signal_alert")
    opened_date = DateField()
    opened_at = DateTimeField(default=datetime.now)
    version_id = IntegerField()
    entry_score = IntegerField()
    entry_price = FloatField(null=True)
    sigma_daily = FloatField(null=True)
    strategy = CharField(max_length=16, default="30dte")
    allocation_pct = FloatField(null=True)
    threshold = IntegerField(null=True)
    previous_score = IntegerField(null=True)
    last_score = IntegerField(null=True)
    last_price = FloatField(null=True)
    last_seen_date = DateField(null=True)
    last_seen_at = DateTimeField(null=True)
    take_profit_price = FloatField(null=True)
    stop_loss_price = FloatField(null=True)
    take_profit_pct = FloatField(null=True)
    stop_loss_pct = FloatField(null=True)
    close_score = IntegerField(null=True)
    close_price = FloatField(null=True)
    closed_date = DateField(null=True)
    closed_at = DateTimeField(null=True)
    closed_reason = CharField(max_length=32, null=True)
    replaced_by_key = CharField(max_length=220, null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "buying_opportunities"
        indexes = (
            (("symbol", "status"), False),
            (("opened_date", "version_id"), False),
            (("status", "side"), False),
        )


@dataclass(frozen=True)
class ScoreNotification:
    symbol: str
    side: str
    score: int
    price: Optional[float]
    allocation_pct: Optional[float]
    previous_score: Optional[int]
    threshold: int
    immediate: bool
    event_type: str

    @property
    def event_key(self) -> str:
        return f"{self.event_type}:{self.symbol}:{self.side}:{self.threshold}"


@dataclass(frozen=True)
class ExitNotification:
    symbol: str
    side: str
    reason: str
    current_price: Optional[float]
    trigger_price: Optional[float]
    current_score: Optional[int]
    allocation_pct: Optional[float]
    reopened: bool = False


def ensure_schema() -> None:
    DB.create_tables([NotificationScoreState, NotificationEvent, BuyingOpportunity], safe=True)

    def add_column(table_name: str, column_name: str, definition: str) -> None:
        try:
            DB.execute_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        except Exception:
            pass

    add_column("notification_events", "price", "FLOAT NULL")
    add_column("notification_events", "allocation_pct", "FLOAT NULL")
    add_column("buying_opportunities", "source_event_key", "VARCHAR(220) NULL UNIQUE")
    add_column("buying_opportunities", "opening_reason", "VARCHAR(32) DEFAULT 'signal_alert'")
    add_column("buying_opportunities", "sigma_daily", "FLOAT NULL")
    add_column("buying_opportunities", "strategy", "VARCHAR(16) DEFAULT '30dte'")
    add_column("buying_opportunities", "allocation_pct", "FLOAT NULL")
    add_column("buying_opportunities", "threshold", "INT NULL")
    add_column("buying_opportunities", "previous_score", "INT NULL")
    add_column("buying_opportunities", "last_score", "INT NULL")
    add_column("buying_opportunities", "last_price", "FLOAT NULL")
    add_column("buying_opportunities", "last_seen_date", "DATE NULL")
    add_column("buying_opportunities", "last_seen_at", "DATETIME NULL")
    add_column("buying_opportunities", "take_profit_price", "FLOAT NULL")
    add_column("buying_opportunities", "stop_loss_price", "FLOAT NULL")
    add_column("buying_opportunities", "take_profit_pct", "FLOAT NULL")
    add_column("buying_opportunities", "stop_loss_pct", "FLOAT NULL")
    add_column("buying_opportunities", "close_score", "INT NULL")
    add_column("buying_opportunities", "close_price", "FLOAT NULL")
    add_column("buying_opportunities", "closed_date", "DATE NULL")
    add_column("buying_opportunities", "closed_at", "DATETIME NULL")
    add_column("buying_opportunities", "closed_reason", "VARCHAR(32) NULL")
    add_column("buying_opportunities", "replaced_by_key", "VARCHAR(220) NULL")
    add_column("buying_opportunities", "created_at", "DATETIME NULL")
    add_column("buying_opportunities", "updated_at", "DATETIME NULL")


def _pushover_user_key() -> str:
    return (
        os.environ.get("TRADER_PUSHOVER_USER_KEY")
        or os.environ.get("PUSHOVER_USER_KEY")
        or DEFAULT_PUSHOVER_USER_KEY
    )


def _pushover_app_token() -> Optional[str]:
    return (
        os.environ.get("TRADER_PUSHOVER_APP_TOKEN")
        or os.environ.get("PUSHOVER_APP_TOKEN")
        or os.environ.get("PUSHOVER_API_TOKEN")
        or DEFAULT_PUSHOVER_APP_TOKEN
    )


def classify_score_transition(
    previous_score: Optional[int],
    new_score: Optional[int],
) -> Optional[Tuple[str, int, bool, str]]:
    """Return side, threshold, immediate, event_type for a new signal crossing."""

    if new_score is None:
        return None

    if new_score >= URGENT_CALL_GATE and (
        previous_score is None or previous_score < URGENT_CALL_GATE
    ):
        return "call", URGENT_CALL_GATE, True, "score_call_85"
    if new_score <= URGENT_PUT_GATE and (
        previous_score is None or previous_score > URGENT_PUT_GATE
    ):
        return "put", URGENT_PUT_GATE, True, "score_put_10"
    if new_score >= CALL_GATE and (previous_score is None or previous_score < CALL_GATE):
        return "call", CALL_GATE, False, "score_call_80"
    if new_score <= PUT_GATE and (previous_score is None or previous_score > PUT_GATE):
        return "put", PUT_GATE, False, "score_put_10"
    return None


def side_for_score(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    if score >= CALL_GATE:
        return "call"
    if score <= PUT_GATE:
        return "put"
    return None


def is_score_still_alertable(side: str, score: Optional[int]) -> bool:
    if score is None:
        return False
    if side == "call":
        return score >= CALL_GATE
    if side == "put":
        return score <= PUT_GATE
    return False


def exit_marker(reason: str) -> str:
    reason_lc = reason.lower()
    if reason_lc in ("tp", "take_profit", "take-profit"):
        return TAKE_PROFIT_MARKER
    if reason_lc in ("sl", "stop_loss", "stop-loss"):
        return STOP_LOSS_MARKER
    return ""


def _realized_vol_pct(closes: List[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    returns = [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((ret - mean) ** 2 for ret in returns) / len(returns)
    sigma = math.sqrt(variance) * 100.0
    return sigma if sigma > 0 else None


def _target_prices(
    *,
    entry_price: float,
    sigma_daily: float,
    side: str,
    strategy: str = "30dte",
    stressed: bool = False,
) -> Tuple[float, float, float, float]:
    import strategy_config

    cfg = strategy_config.STRATEGY_15DTE if strategy == "15dte" else strategy_config.STRATEGY_30DTE
    if side == "put":
        tp_move_pct = cfg.PUT_TP_SIGMA * sigma_daily
        sl_move_pct = cfg.PUT_SL_SIGMA * sigma_daily
        return (
            entry_price * (1.0 - tp_move_pct / 100.0),
            entry_price * (1.0 + sl_move_pct / 100.0),
            -tp_move_pct,
            sl_move_pct,
        )

    tp_sigma = cfg.TP_SIGMA_STRESS if stressed else cfg.TP_SIGMA_BASE
    sl_sigma = cfg.SL_SIGMA_STRESS if stressed else cfg.SL_SIGMA_BASE
    tp_move_pct = tp_sigma * sigma_daily
    sl_move_pct = sl_sigma * sigma_daily
    return (
        entry_price * (1.0 + tp_move_pct / 100.0),
        entry_price * (1.0 - sl_move_pct / 100.0),
        tp_move_pct,
        -sl_move_pct,
    )


def _exit_reason_for_bar(
    side: str,
    high_price: float,
    low_price: float,
    take_profit_price: Optional[float],
    stop_loss_price: Optional[float],
    *,
    put_sl_hold_active: bool = False,
) -> Optional[str]:
    if take_profit_price is None or stop_loss_price is None:
        return None
    if side == "put":
        if low_price <= take_profit_price:
            return "tp"
        if not put_sl_hold_active and high_price >= stop_loss_price:
            return "sl"
        return None
    if high_price >= take_profit_price:
        return "tp"
    if low_price <= stop_loss_price:
        return "sl"
    return None


def allocation_pct_for_score(
    score: int,
    side: str,
    *,
    strategy: str = "30dte",
    signal_date: Optional[date] = None,
) -> Optional[float]:
    """Return current strategy allocation percentage for a score alert."""

    try:
        from portfolio_allocation import allocation_breakdown_for_score

        breakdown = allocation_breakdown_for_score(
            score,
            side,
            strategy=strategy,
            signal_date=signal_date,
        )
        if breakdown is None or breakdown.effective_pct <= 0:
            return None
        return round(breakdown.effective_pct, 1)
    except Exception:
        return None


def capture_current_scores(target_date: Optional[date] = None) -> Tuple[Optional[date], Optional[int], Dict[str, int]]:
    """Snapshot active-version scores before an intraday update overwrites them."""

    version = AlgorithmVersion.get_active_scores_version()
    if not version:
        return None, None, {}

    if target_date is None:
        target_date = (
            Score.select(fn.MAX(Score.date))
            .where(Score.version == version)
            .scalar()
        )
    if target_date is None:
        return None, version.id, {}

    rows = (
        Score.select(Score.symbol, Score.overall, Score.price)
        .join(Stock, on=(Score.symbol == Stock.symbol))
        .where(
            (Score.version == version)
            & (Score.date == target_date)
            & Score.overall.is_null(False)
            & Stock.delisted_date.is_null()
        )
    )
    return target_date, version.id, {row.symbol_id: int(row.overall) for row in rows}


def _load_state_scores(target_date: date, version_id: int) -> Dict[str, int]:
    return {
        row.symbol: row.last_score
        for row in NotificationScoreState.select()
        .where(
            (NotificationScoreState.score_date == target_date)
            & (NotificationScoreState.version_id == version_id)
        )
    }


def _score_rows(target_date: date, version: AlgorithmVersion) -> Iterable[Score]:
    return (
        Score.select(Score.symbol, Score.overall, Score.price)
        .join(Stock, on=(Score.symbol == Stock.symbol))
        .where(
            (Score.version == version)
            & (Score.date == target_date)
            & Score.overall.is_null(False)
            & Stock.delisted_date.is_null()
        )
        .order_by(Score.overall.desc())
    )


def _record_score_state(symbol: str, target_date: date, version_id: int, score: int) -> None:
    row, _ = NotificationScoreState.get_or_create(
        symbol=symbol,
        score_date=target_date,
        version_id=version_id,
        defaults={"last_score": score},
    )
    row.last_score = score
    row.updated_at = datetime.now()
    row.save()


def _opportunity_key_from_event(event_key: str) -> str:
    return f"alert:{event_key}"


def _latest_breadth_on_or_before(target_date: date) -> Optional[float]:
    row = (
        MarketBreadth.select(MarketBreadth.breadth_score)
        .where((MarketBreadth.date <= target_date) & MarketBreadth.breadth_score.is_null(False))
        .order_by(MarketBreadth.date.desc())
        .first()
    )
    return float(row.breadth_score) if row else None


def _is_call_stress_date(target_date: date, strategy: str = "30dte") -> bool:
    import strategy_config

    cfg = strategy_config.STRATEGY_15DTE if strategy == "15dte" else strategy_config.STRATEGY_30DTE
    breadth = _latest_breadth_on_or_before(target_date)
    return breadth is not None and breadth <= cfg.option.BREADTH_THRESHOLD


def _entry_sigma(symbol: str, entry_date: date) -> Optional[float]:
    rows = list(
        PriceHistory.select(PriceHistory.close)
        .where((PriceHistory.symbol == symbol) & (PriceHistory.date <= entry_date))
        .order_by(PriceHistory.date.desc())
        .limit(VOL_BARS + 1)
    )
    closes = [float(row.close) for row in reversed(rows)]
    return _realized_vol_pct(closes)


def _apply_opportunity_targets(opportunity: BuyingOpportunity) -> bool:
    # ALWAYS recompute TP/SL from the CURRENT strategy config (do NOT freeze at log-time).
    # Targets were previously frozen on first computation, so an opportunity logged under an
    # older stop (e.g. -27%) kept that stale stop forever and the tracker fired SL-breach
    # alerts at the wrong (tighter) level after the strategy moved to the wide -70% SL.
    # Recomputing per pass makes the stop track the live config and self-heals config changes.
    # The entry-date sigma is stable, so we reuse the stored sigma_daily (no re-query) when
    # present; the TP/SL *prices* are re-derived from the current SL_SIGMA/TP_SIGMA each call.
    if opportunity.entry_price is None:
        return False
    sigma = opportunity.sigma_daily
    if sigma is None:
        sigma = _entry_sigma(opportunity.symbol, opportunity.opened_date)
        if sigma is None:
            return False
    stressed = opportunity.side == "call" and _is_call_stress_date(
        opportunity.opened_date,
        opportunity.strategy or "30dte",
    )
    tp_price, sl_price, tp_pct, sl_pct = _target_prices(
        entry_price=float(opportunity.entry_price),
        sigma_daily=sigma,
        side=opportunity.side,
        strategy=opportunity.strategy or "30dte",
        stressed=stressed,
    )
    new_tp, new_sl = round(tp_price, 4), round(sl_price, 4)
    if (
        opportunity.sigma_daily != sigma
        or opportunity.take_profit_price != new_tp
        or opportunity.stop_loss_price != new_sl
    ):
        opportunity.sigma_daily = sigma
        opportunity.take_profit_price = new_tp
        opportunity.stop_loss_price = new_sl
        opportunity.take_profit_pct = round(tp_pct, 4)
        opportunity.stop_loss_pct = round(sl_pct, 4)
        opportunity.updated_at = datetime.now()
        opportunity.save()
    return True


def _record_buying_opportunity(
    event_row: NotificationEvent,
    event: ScoreNotification,
    target_date: date,
    version_id: int,
) -> BuyingOpportunity:
    now = datetime.now()
    existing = (
        BuyingOpportunity.select()
        .where(
            (BuyingOpportunity.symbol == event.symbol)
            & (BuyingOpportunity.side == event.side)
            & (BuyingOpportunity.status == "open")
        )
        .order_by(BuyingOpportunity.opened_at.desc(), BuyingOpportunity.id.desc())
        .first()
    )
    if existing is not None:
        existing.last_score = event.score
        existing.last_price = event.price
        existing.last_seen_date = target_date
        existing.last_seen_at = now
        existing.updated_at = now
        existing.save()
        _apply_opportunity_targets(existing)
        return existing

    opportunity_key = _opportunity_key_from_event(event_row.event_key)
    row, created = BuyingOpportunity.get_or_create(
        opportunity_key=opportunity_key,
        defaults={
            "source_event_key": event_row.event_key,
            "symbol": event.symbol,
            "side": event.side,
            "status": "open",
            "opening_reason": "signal_alert",
            "opened_date": target_date,
            "opened_at": now,
            "version_id": version_id,
            "entry_score": event.score,
            "entry_price": event.price,
            "strategy": "30dte",
            "allocation_pct": event.allocation_pct,
            "threshold": event.threshold,
            "previous_score": event.previous_score,
            "last_score": event.score,
            "last_price": event.price,
            "last_seen_date": target_date,
            "last_seen_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )
    if not created and row.status == "open":
        row.last_score = event.score
        row.last_price = event.price
        row.last_seen_date = target_date
        row.last_seen_at = now
        row.updated_at = now
        row.save()
    _apply_opportunity_targets(row)
    return row


def close_opportunity(
    opportunity: BuyingOpportunity,
    *,
    reason: str,
    current_score: Optional[int],
    current_price: Optional[float],
    target_date: date,
    version_id: int,
    allocation_pct: Optional[float] = None,
) -> Tuple[BuyingOpportunity, Optional[BuyingOpportunity]]:
    """Close an opportunity and reopen it if the current score still qualifies."""

    now = datetime.now()
    close_reason = reason[:32]
    opportunity.status = "closed"
    opportunity.closed_reason = close_reason
    opportunity.closed_date = target_date
    opportunity.closed_at = now
    opportunity.close_score = current_score
    opportunity.close_price = current_price
    opportunity.last_score = current_score
    opportunity.last_price = current_price
    opportunity.last_seen_date = target_date
    opportunity.last_seen_at = now
    opportunity.updated_at = now

    reopened = None
    if is_score_still_alertable(opportunity.side, current_score):
        reopen_key = f"reopen:{opportunity.id}:{close_reason}:{target_date.isoformat()}"
        reopened, _ = BuyingOpportunity.get_or_create(
            opportunity_key=reopen_key,
            defaults={
                "source_event_key": reopen_key,
                "symbol": opportunity.symbol,
                "side": opportunity.side,
                "status": "open",
                "opening_reason": f"reopen_after_{close_reason}"[:32],
                "opened_date": target_date,
                "opened_at": now,
                "version_id": version_id,
                "entry_score": int(current_score) if current_score is not None else opportunity.entry_score,
                "entry_price": current_price,
                "strategy": opportunity.strategy or "30dte",
                "allocation_pct": allocation_pct,
                "threshold": CALL_GATE if opportunity.side == "call" else PUT_GATE,
                "previous_score": opportunity.entry_score,
                "last_score": current_score,
                "last_price": current_price,
                "last_seen_date": target_date,
                "last_seen_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        opportunity.replaced_by_key = reopened.opportunity_key
        _apply_opportunity_targets(reopened)
    opportunity.save()
    return opportunity, reopened


def _create_event(
    event: ScoreNotification,
    target_date: date,
    version_id: int,
) -> Optional[NotificationEvent]:
    event_key = f"{target_date.isoformat()}:{version_id}:{event.event_key}"
    row, created = NotificationEvent.get_or_create(
        event_key=event_key,
        defaults={
            "event_type": event.event_type,
            "symbol": event.symbol,
            "side": event.side,
            "score_date": target_date,
            "version_id": version_id,
            "previous_score": event.previous_score,
            "score": event.score,
            "price": event.price,
            "allocation_pct": event.allocation_pct,
            "threshold": event.threshold,
            "immediate": event.immediate,
        },
    )
    if not created:
        return None
    _record_buying_opportunity(row, event, target_date, version_id)
    return row


def _send_event(event_row: NotificationEvent, event: ScoreNotification, *, dry_run: bool) -> None:
    title = _title(event)
    message = _price_line(event)
    status, response = send_pushover(
        title,
        message,
        priority=1 if event.immediate else 0,
        html=True,
        dry_run=dry_run,
    )
    event_row.message = _storage_line(event)
    event_row.relay_status = status
    event_row.relay_response = response[:1000]
    event_row.sent_at = datetime.now() if status == "sent" else None
    event_row.save()


def send_pushover(
    title: str,
    message: str,
    *,
    priority: int = 0,
    url: Optional[str] = None,
    html: bool = False,
    dry_run: bool = False,
) -> Tuple[str, str]:
    """Send one Pushover message. Returns status, response text."""

    token = _pushover_app_token()
    user = _pushover_user_key()
    payload = {
        "token": token or "",
        "user": user,
        "title": title,
        "message": message,
        "priority": str(priority),
    }
    if url:
        payload["url"] = url
        payload["url_title"] = "Open Trader"
    if html:
        payload["html"] = "1"

    if dry_run:
        return "dry_run", json.dumps({k: v for k, v in payload.items() if k != "token"})
    if not token:
        return "disabled", "Missing TRADER_PUSHOVER_APP_TOKEN/PUSHOVER_APP_TOKEN"

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(PUSHOVER_ENDPOINT, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return "sent", body
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


def _score_marker(event: ScoreNotification) -> str:
    marker = "🟢" if event.side == "call" else "🔻"
    return f"{marker} {event.score}"


def _title(event: ScoreNotification) -> str:
    return f"{event.symbol} {_score_marker(event)}"


def _exit_title(event: ExitNotification) -> str:
    return f"{event.symbol} {exit_marker(event.reason)} {event.reason.upper()}"


def _price_line(event: ScoreNotification) -> str:
    price = f"${event.price:.2f}" if event.price is not None else "$--"
    if event.allocation_pct is None:
        return price
    return f"{price} ({_fmt_pct(event.allocation_pct)})"


def _line(event: ScoreNotification) -> str:
    price = f"${event.price:.2f}" if event.price is not None else "$--"
    pct = f"  {_fmt_pct(event.allocation_pct)}" if event.allocation_pct is not None else ""
    return f"{event.symbol} {_score_marker(event)}  {price}{pct}"


def _storage_line(event: ScoreNotification) -> str:
    suffix = "H" if event.side == "call" else "L"
    price = f"${event.price:.2f}" if event.price is not None else "$--"
    pct = f" {_fmt_pct(event.allocation_pct)}" if event.allocation_pct is not None else ""
    return f"{event.symbol} {event.score}{suffix} {price}{pct}"


def _exit_line(event: ExitNotification) -> str:
    current = f"${event.current_price:.2f}" if event.current_price is not None else "$--"
    trigger_label = "target" if event.reason == "tp" else "stop"
    trigger = f"${event.trigger_price:.2f}" if event.trigger_price is not None else "$--"
    score = f"  {_score_marker(ScoreNotification(event.symbol, event.side, int(event.current_score), event.current_price, event.allocation_pct, None, 0, True, 'exit'))}" if event.current_score is not None else ""
    reopened = "  reopened" if event.reopened else ""
    return f"{current} ({trigger_label} {trigger}){score}{reopened}"


def _exit_storage_line(event: ExitNotification) -> str:
    current = f"${event.current_price:.2f}" if event.current_price is not None else "$--"
    trigger_label = "target" if event.reason == "tp" else "stop"
    trigger = f"${event.trigger_price:.2f}" if event.trigger_price is not None else "$--"
    score = f" score {event.current_score}" if event.current_score is not None else ""
    reopened = " reopened" if event.reopened else ""
    return f"{event.symbol} {event.reason.upper()} {current} {trigger_label} {trigger}{score}{reopened}"


def _fmt_pct(value: float) -> str:
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return f"{int(rounded)}%"
    return f"{rounded:.1f}%"


def _html_line(event: ScoreNotification) -> str:
    color = "#10b981" if event.side == "call" else "#ef4444"
    return f'<font color="{color}">{_line(event)}</font>'


def _dashboard_sort_value(event: ScoreNotification) -> int:
    if event.score >= CALL_GATE:
        return 200 + event.score
    if event.score <= PUT_GATE:
        return 100 + (100 - event.score)
    return event.score if event.score >= 50 else 100 - event.score


def format_score_digest(events: List[ScoreNotification]) -> str:
    sorted_events = sorted(events, key=_dashboard_sort_value, reverse=True)
    return "\n".join(_html_line(e) for e in sorted_events[:20])


def _current_score_map(target_date: date, version: AlgorithmVersion) -> Dict[str, Score]:
    return {
        row.symbol_id: row
        for row in Score.select(Score.symbol, Score.overall, Score.price)
        .join(Stock, on=(Score.symbol == Stock.symbol))
        .where(
            (Score.version == version)
            & (Score.date == target_date)
            & Score.overall.is_null(False)
            & Stock.delisted_date.is_null()
        )
    }


def _latest_price_bars(symbols: List[str], target_date: date) -> Dict[str, PriceHistory]:
    bars: Dict[str, PriceHistory] = {}
    if not symbols:
        return bars
    rows = (
        PriceHistory.select(
            PriceHistory.symbol,
            PriceHistory.date,
            PriceHistory.open,
            PriceHistory.high,
            PriceHistory.low,
            PriceHistory.close,
        )
        .where((PriceHistory.symbol.in_(symbols)) & (PriceHistory.date <= target_date))
        .order_by(PriceHistory.symbol, PriceHistory.date.desc())
    )
    for row in rows:
        symbol = row.symbol_id
        if symbol not in bars:
            bars[symbol] = row
    return bars


def _trading_bars_held(symbol: str, opened_date: date, current_date: date) -> int:
    return (
        PriceHistory.select()
        .where(
            (PriceHistory.symbol == symbol)
            & (PriceHistory.date > opened_date)
            & (PriceHistory.date <= current_date)
        )
        .count()
    )


def _put_sl_hold_active(opportunity: BuyingOpportunity, current_date: date) -> bool:
    if opportunity.side != "put":
        return False
    try:
        import strategy_config

        cfg = strategy_config.STRATEGY_15DTE if opportunity.strategy == "15dte" else strategy_config.STRATEGY_30DTE
        hold_bars = (
            cfg.option.PUT_SL_HOLD_BARS_MONDAY
            if opportunity.opened_date.weekday() == 0
            else cfg.option.PUT_SL_HOLD_BARS_DEFAULT
        )
        return _trading_bars_held(opportunity.symbol, opportunity.opened_date, current_date) <= hold_bars
    except Exception:
        return False


def _create_exit_event(
    opportunity: BuyingOpportunity,
    alert: ExitNotification,
    target_date: date,
    version_id: int,
) -> Optional[NotificationEvent]:
    event_type = f"exit_{alert.reason}"
    event_key = f"{target_date.isoformat()}:{version_id}:exit:{opportunity.id}:{alert.reason}"
    row, created = NotificationEvent.get_or_create(
        event_key=event_key,
        defaults={
            "event_type": event_type,
            "symbol": opportunity.symbol,
            "side": opportunity.side,
            "score_date": target_date,
            "version_id": version_id,
            "previous_score": opportunity.entry_score,
            "score": alert.current_score if alert.current_score is not None else opportunity.entry_score,
            "price": alert.current_price,
            "allocation_pct": alert.allocation_pct,
            "threshold": 0,
            "immediate": True,
            "message": _exit_storage_line(alert),
        },
    )
    return row if created else None


def _send_exit_event(event_row: NotificationEvent, alert: ExitNotification, *, dry_run: bool) -> None:
    status, response = send_pushover(
        _exit_title(alert),
        _exit_line(alert),
        priority=1,
        html=True,
        dry_run=dry_run,
    )
    event_row.message = _exit_storage_line(alert)
    event_row.relay_status = status
    event_row.relay_response = response[:1000]
    event_row.sent_at = datetime.now() if status == "sent" else None
    event_row.save()


def process_opportunity_exits(
    *,
    target_date: Optional[date] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> List[NotificationEvent]:
    """Check open buying opportunities for TP/SL hits and push exit alerts."""

    if not dry_run and not _pushover_app_token():
        if verbose:
            print(Fore.YELLOW + "Exit notifications skipped: missing TRADER_PUSHOVER_APP_TOKEN/PUSHOVER_APP_TOKEN")
        return []

    ensure_schema()
    version = AlgorithmVersion.get_active_scores_version()
    if not version:
        if verbose:
            print(Fore.YELLOW + "Exit notifications skipped: no active algorithm version")
        return []

    if target_date is None:
        target_date = (
            Score.select(fn.MAX(Score.date))
            .where(Score.version == version)
            .scalar()
        )
    if target_date is None:
        if verbose:
            print(Fore.YELLOW + "Exit notifications skipped: no active score date")
        return []

    opportunities = list(
        BuyingOpportunity.select()
        .where(BuyingOpportunity.status == "open")
        .order_by(BuyingOpportunity.opened_at.asc(), BuyingOpportunity.id.asc())
    )
    if not opportunities:
        if verbose:
            print(Fore.CYAN + "Exit notifications: no open opportunities")
        return []

    score_map = _current_score_map(target_date, version)
    price_bars = _latest_price_bars([opp.symbol for opp in opportunities], target_date)
    created_rows: List[NotificationEvent] = []

    for opportunity in opportunities:
        bar = price_bars.get(opportunity.symbol)
        if bar is None or bar.date <= opportunity.opened_date:
            continue
        if not _apply_opportunity_targets(opportunity):
            continue

        score_row = score_map.get(opportunity.symbol)
        current_score = int(score_row.overall) if score_row is not None and score_row.overall is not None else None
        current_price = (
            float(score_row.price)
            if score_row is not None and score_row.price is not None
            else float(bar.close)
        )
        reason = _exit_reason_for_bar(
            opportunity.side,
            float(bar.high),
            float(bar.low),
            opportunity.take_profit_price,
            opportunity.stop_loss_price,
            put_sl_hold_active=_put_sl_hold_active(opportunity, bar.date),
        )
        opportunity.last_score = current_score
        opportunity.last_price = current_price
        opportunity.last_seen_date = bar.date
        opportunity.last_seen_at = datetime.now()
        opportunity.updated_at = datetime.now()
        opportunity.save()
        if reason is None:
            continue

        allocation_pct = (
            allocation_pct_for_score(
                current_score,
                opportunity.side,
                strategy=opportunity.strategy or "30dte",
                signal_date=bar.date,
            )
            if current_score is not None and is_score_still_alertable(opportunity.side, current_score)
            else opportunity.allocation_pct
        )
        alert = ExitNotification(
            symbol=opportunity.symbol,
            side=opportunity.side,
            reason=reason,
            current_price=current_price,
            trigger_price=opportunity.take_profit_price if reason == "tp" else opportunity.stop_loss_price,
            current_score=current_score,
            allocation_pct=allocation_pct,
            reopened=False,
        )
        if dry_run:
            created_rows.append(
                NotificationEvent(
                    event_key=f"dry-run:{target_date.isoformat()}:{opportunity.id}:{reason}",
                    event_type=f"exit_{reason}",
                    symbol=opportunity.symbol,
                    side=opportunity.side,
                    score_date=bar.date,
                    version_id=version.id,
                    previous_score=opportunity.entry_score,
                    score=current_score if current_score is not None else opportunity.entry_score,
                    price=current_price,
                    allocation_pct=allocation_pct,
                    threshold=0,
                    immediate=True,
                    message=_exit_storage_line(alert),
                    relay_status="dry_run",
                )
            )
            continue

        _, reopened = close_opportunity(
            opportunity,
            reason=reason,
            current_score=current_score,
            current_price=current_price,
            target_date=bar.date,
            version_id=version.id,
            allocation_pct=allocation_pct,
        )
        alert = ExitNotification(
            symbol=opportunity.symbol,
            side=opportunity.side,
            reason=reason,
            current_price=current_price,
            trigger_price=opportunity.take_profit_price if reason == "tp" else opportunity.stop_loss_price,
            current_score=current_score,
            allocation_pct=allocation_pct,
            reopened=reopened is not None,
        )
        event_row = _create_exit_event(opportunity, alert, bar.date, version.id)
        if event_row is None:
            continue
        _send_exit_event(event_row, alert, dry_run=dry_run)
        created_rows.append(event_row)

    if verbose:
        if created_rows:
            print(Fore.CYAN + f"Exit notifications: {len(created_rows)} TP/SL alert(s)")
        else:
            print(Fore.CYAN + "Exit notifications: no TP/SL hits")
    return created_rows


def process_score_notifications(
    *,
    previous_scores: Optional[Dict[str, int]] = None,
    target_date: Optional[date] = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> List[NotificationEvent]:
    """Compare active scores to previous scores and push new signal alerts."""

    if not dry_run and not _pushover_app_token():
        if verbose:
            print(Fore.YELLOW + "Push notifications skipped: missing TRADER_PUSHOVER_APP_TOKEN/PUSHOVER_APP_TOKEN")
        return []

    ensure_schema()
    version = AlgorithmVersion.get_active_scores_version()
    if not version:
        if verbose:
            print(Fore.YELLOW + "Push notifications skipped: no active algorithm version")
        return []

    if target_date is None:
        target_date = (
            Score.select(fn.MAX(Score.date))
            .where(Score.version == version)
            .scalar()
        )
    if target_date is None:
        if verbose:
            print(Fore.YELLOW + "Push notifications skipped: no active score date")
        return []

    prior = previous_scores if previous_scores is not None else _load_state_scores(target_date, version.id)
    immediate: List[ScoreNotification] = []
    digest: List[ScoreNotification] = []
    created_rows: List[NotificationEvent] = []

    for row in _score_rows(target_date, version):
        symbol = row.symbol_id
        score = int(row.overall)
        price = float(row.price) if row.price is not None else None
        prev = prior.get(symbol)
        transition = classify_score_transition(prev, score)
        _record_score_state(symbol, target_date, version.id, score)
        if transition is None:
            continue
        side, threshold, is_immediate, event_type = transition
        allocation_pct = allocation_pct_for_score(score, side, signal_date=target_date)
        event = ScoreNotification(
            symbol=symbol,
            side=side,
            score=score,
            price=price,
            allocation_pct=allocation_pct,
            previous_score=prev,
            threshold=threshold,
            immediate=is_immediate,
            event_type=event_type,
        )
        event_row = _create_event(event, target_date, version.id)
        if event_row is None:
            continue
        created_rows.append(event_row)
        if is_immediate:
            immediate.append(event)
        else:
            digest.append(event)

    for event in immediate:
        event_row = next((r for r in created_rows if r.symbol == event.symbol and r.event_type == event.event_type), None)
        if event_row:
            _send_event(event_row, event, dry_run=dry_run)

    if digest:
        message = format_score_digest(digest)
        storage_message = "\n".join(_storage_line(e) for e in digest)
        status, response = send_pushover(
            f"Trader signals {len(digest)}",
            message,
            priority=0,
            html=True,
            dry_run=dry_run,
        )
        for event_row in created_rows:
            if not event_row.immediate:
                event_row.message = storage_message
                event_row.relay_status = status
                event_row.relay_response = response[:1000]
                event_row.sent_at = datetime.now() if status == "sent" else None
                event_row.save()

    if verbose:
        if created_rows:
            print(Fore.CYAN + f"Push notifications: {len(immediate)} immediate, {len(digest)} digested")
        else:
            print(Fore.CYAN + "Push notifications: no new signal crossings")
    return created_rows


def process_single_score_notification(
    symbol: str,
    *,
    previous_score: Optional[int],
    target_date: date,
    dry_run: bool = False,
    verbose: bool = False,
) -> Optional[NotificationEvent]:
    """Send an urgent per-symbol alert immediately after a score is written."""

    if not dry_run and not _pushover_app_token():
        return None

    ensure_schema()
    version = AlgorithmVersion.get_active_scores_version()
    if not version:
        return None

    row = (
        Score.select(Score.symbol, Score.overall, Score.price)
        .where(
            (Score.symbol == symbol)
            & (Score.version == version)
            & (Score.date == target_date)
            & Score.overall.is_null(False)
        )
        .first()
    )
    if row is None:
        return None

    score = int(row.overall)
    price = float(row.price) if row.price is not None else None
    transition = classify_score_transition(previous_score, score)
    _record_score_state(symbol, target_date, version.id, score)
    if transition is None:
        return None

    side, threshold, is_immediate, event_type = transition
    if not is_immediate:
        return None

    allocation_pct = allocation_pct_for_score(score, side, signal_date=target_date)
    event = ScoreNotification(
        symbol=symbol,
        side=side,
        score=score,
        price=price,
        allocation_pct=allocation_pct,
        previous_score=previous_score,
        threshold=threshold,
        immediate=True,
        event_type=event_type,
    )
    event_row = _create_event(event, target_date, version.id)
    if event_row is None:
        return None
    _send_event(event_row, event, dry_run=dry_run)
    if verbose:
        print(Fore.CYAN + f"Push notification sent: {_storage_line(event)}")
    return event_row
