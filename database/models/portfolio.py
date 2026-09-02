"""Live Portfolio tracking models.

A single active PortfolioRun is the transparent, persisted realization of the
v70 Apex strategy: it starts from a fixed date + capital and, every
``trader update``, the deterministic cascade engine (backtest_cascade) is
re-evaluated over [start_date .. last completed session] to reproduce exactly
what the strategy would be holding and for how much.

These tables are a *materialization* of that deterministic run — the source of
truth is the engine; the rows are kept so the API can serve the page cheaply
and so position open/close events can be de-duplicated for notifications.
"""
from datetime import datetime, date

from peewee import (
    AutoField, CharField, IntegerField, FloatField, DateField, DateTimeField,
    DecimalField, BooleanField, TextField,
)

from trader_database import BaseModel


class PortfolioRun(BaseModel):
    """One tracked portfolio (singleton in practice — the active run)."""
    id = AutoField(primary_key=True)
    name = CharField(max_length=64, default='v70 Apex Live')
    profile = CharField(max_length=16, default='apex')        # portfolio_profiles key
    version_id = IntegerField(null=True)                      # scoring AlgorithmVersion.id at creation

    start_date = DateField()                                  # inception (e.g. 2026-06-01)
    go_live_date = DateField()                                # first day notifications fire (e.g. 2026-06-05)

    starting_capital_cad = DecimalField(max_digits=16, decimal_places=2)
    starting_capital_usd = DecimalField(max_digits=16, decimal_places=2)
    cad_per_usd = DecimalField(max_digits=12, decimal_places=6)   # fixed FX pinning the CAD start

    strategy_fingerprint = CharField(max_length=64, null=True)  # hash of the active Apex config
    last_processed_date = DateField(null=True)               # D of the last sync
    last_synced_at = DateTimeField(null=True)

    # Sprint watchdog (P3.1 Phase 1, 2026-07-13) — pure user-protection
    # instrumentation for the Apex sprint's stop-at-2x discipline. NO
    # auto-rotate/profile-switch wiring lives here (that is P3.1 Phase 3,
    # user-locked) — this only ever sets flags the engine reads to skip
    # NEW entries; exits/sweeps/alerts stay fully live regardless. See
    # portfolio_engine._sprint_watchdog_check for the exact semantics of
    # each field (why halt_new_entries and sprint_2x_hit_date are separate
    # latches, and why sprint_2x_notified is decoupled from both).
    sprint_start_equity = DecimalField(max_digits=18, decimal_places=2, null=True)
    sprint_start_date = DateField(null=True)
    halt_new_entries = BooleanField(default=False)
    sprint_2x_hit_date = DateField(null=True)
    sprint_2x_notified = BooleanField(default=False)

    # Incident kill-switch (P3.8, 2026-07-13) — `trader portfolio
    # pause|resume`. Deliberately a SEPARATE flag from halt_new_entries
    # above: this is the manual, universal, any-profile incident switch
    # (suppresses BUY pushes + halts new entries; exits/sweeps/sell alerts
    # stay live; pure sync-behavior flag, no data mutation), while
    # halt_new_entries is the automatic, Apex-sprint-only 2x watchdog latch.
    # Both independently gate new entries (OR'd together in
    # portfolio_engine); clearing one never touches the other. See
    # .claude/docs/incident-runbook.md.
    sync_paused = BooleanField(default=False)

    equity_usd = DecimalField(max_digits=18, decimal_places=2, null=True)   # latest MTM total
    cash_usd = DecimalField(max_digits=18, decimal_places=2, null=True)
    peak_equity_usd = DecimalField(max_digits=18, decimal_places=2, null=True)
    max_dd_pct = FloatField(null=True)                       # honest MTM max drawdown

    active = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'portfolio_runs'
        indexes = ((('active',), False),)

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)
        for ddl in (
            "ALTER TABLE portfolio_runs ADD COLUMN strategy_fingerprint VARCHAR(64) NULL",
            "ALTER TABLE portfolio_runs ADD COLUMN sprint_start_equity DECIMAL(18,2) NULL",
            "ALTER TABLE portfolio_runs ADD COLUMN sprint_start_date DATE NULL",
            "ALTER TABLE portfolio_runs ADD COLUMN halt_new_entries TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE portfolio_runs ADD COLUMN sprint_2x_hit_date DATE NULL",
            "ALTER TABLE portfolio_runs ADD COLUMN sprint_2x_notified TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE portfolio_runs ADD COLUMN sync_paused TINYINT(1) NOT NULL DEFAULT 0",
        ):
            try:
                DB.execute_sql(ddl)
            except Exception:
                pass

    @classmethod
    def get_active(cls):
        cls.ensure_schema()
        return (cls.select()
                .where(cls.active == True)
                .order_by(cls.id.desc())
                .first())


class PortfolioPosition(BaseModel):
    """A single option position (open or closed) in the active run."""
    id = AutoField(primary_key=True)
    run_id = IntegerField(index=True)
    pos_key = CharField(max_length=96, unique=True)          # f"{run}:{symbol}:{entry_date}:{side}"

    symbol = CharField(max_length=16)
    side = CharField(max_length=8)                           # 'call' | 'put'
    tier = CharField(max_length=8, null=True)                # '95+' etc.
    entry_score = IntegerField(null=True)
    entry_version_id = IntegerField(null=True)               # scoring AlgorithmVersion at entry (for re-qualify sweep)
    dte = IntegerField(default=30)                           # nominal option DTE (30 / 15)
    routed_15dte = BooleanField(default=False)
    stressed = BooleanField(default=False)                   # breadth regime at entry (barrier base/stress)
    premium_mult = FloatField(null=True)                     # 1.82 (30d) / 1.29 (15d)
    delta = FloatField(null=True)
    dead_hold_active = BooleanField(default=False)           # SL fired deep; holding for popout/expiry

    strike = DecimalField(max_digits=12, decimal_places=2, null=True)
    expiration_date = DateField(null=True)

    entry_date = DateField()
    entry_underlying = DecimalField(max_digits=12, decimal_places=4)  # stock close at entry
    sigma_daily = FloatField(null=True)
    premium_pct = FloatField(null=True)                     # premium as fraction of underlying
    premium_usd = DecimalField(max_digits=16, decimal_places=2)       # dollars allocated (cost)
    entry_equity_usd = DecimalField(max_digits=18, decimal_places=2, null=True)
    alloc_pct = FloatField(null=True)                       # premium_usd / equity_at_entry × 100

    status = CharField(max_length=8, default='open')        # 'open' | 'closed'
    mark_date = DateField(null=True)                        # session the mark/exit is as of
    current_underlying = DecimalField(max_digits=12, decimal_places=4, null=True)
    current_value_usd = DecimalField(max_digits=16, decimal_places=2, null=True)
    pnl_usd = FloatField(null=True)
    pnl_pct = FloatField(null=True)                         # option P&L (fraction of premium)
    hold_bars = IntegerField(null=True)

    tp_price = DecimalField(max_digits=12, decimal_places=4, null=True)
    sl_price = DecimalField(max_digits=12, decimal_places=4, null=True)
    deadline = DateField(null=True)                         # hard-sell day

    last_open_date = DateField(null=True)                  # most recent session this was still open (exit clamp)
    exit_date = DateField(null=True)
    exit_reason = CharField(max_length=16, null=True)       # tp|sl|hard|dh_pop|dh_expiry|version_sweep|strategy_sweep

    # Re-qualification sweep state: a version/strategy change marks disqualified
    # holdings sweep_pending; the ledger exits them at the close of
    # sweep_action_date (the first session that completes AFTER the change was
    # detected) instead of backdating the exit to an already-closed session.
    sweep_pending = BooleanField(default=False)
    sweep_reason = CharField(max_length=16, null=True)      # version_sweep | strategy_sweep
    sweep_action_date = DateField(null=True)

    notified_open = BooleanField(default=False)
    notified_close = BooleanField(default=False)

    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'portfolio_positions'
        indexes = (
            (('run_id', 'status'), False),
            (('run_id', 'entry_date'), False),
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)
        for ddl in (
            "ALTER TABLE portfolio_positions ADD COLUMN entry_version_id INT NULL",
            "ALTER TABLE portfolio_positions ADD COLUMN stressed TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE portfolio_positions ADD COLUMN premium_mult DOUBLE NULL",
            "ALTER TABLE portfolio_positions ADD COLUMN delta DOUBLE NULL",
            "ALTER TABLE portfolio_positions ADD COLUMN dead_hold_active TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE portfolio_positions ADD COLUMN last_open_date DATE NULL",
            "ALTER TABLE portfolio_positions ADD COLUMN sweep_pending TINYINT(1) NOT NULL DEFAULT 0",
            "ALTER TABLE portfolio_positions ADD COLUMN sweep_reason VARCHAR(16) NULL",
            "ALTER TABLE portfolio_positions ADD COLUMN sweep_action_date DATE NULL",
        ):
            try:
                DB.execute_sql(ddl)
            except Exception:
                pass


class PortfolioPendingAlert(BaseModel):
    """De-dup ledger for live (intraday / pre-open) action pushes.

    The deterministic ledger only actions completed sessions, but the
    notification layer fires DURING market hours (buy/sell alerts ahead of the
    close-of-session actioning). One row is recorded per push actually sent so
    repeated `trader update` runs never re-send the same alert.

    kind: 'entry' (provisional intraday buy, ref=symbol),
          'entry_cancel' (alerted buy that faded by the close, ref=symbol),
          'exit' (sell — TP/SL touch, popout, deadline, sweep; ref=pos_key),
          'exit_cancel' (sweep rescinded after scores backfilled, ref=pos_key),
          'digest' (morning carry-over buy summary, ref='open').
    """
    id = AutoField(primary_key=True)
    run_id = IntegerField(index=True)
    kind = CharField(max_length=16)
    ref = CharField(max_length=96)                          # symbol or pos_key
    date = DateField()                                      # session the alert is for
    payload = TextField(null=True)
    sent_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'portfolio_pending_alerts'
        indexes = ((('run_id', 'kind', 'ref', 'date'), True),)

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)


class PortfolioEquitySnapshot(BaseModel):
    """Daily portfolio equity (mark-to-market) — drives the growth chart."""
    id = AutoField(primary_key=True)
    run_id = IntegerField(index=True)
    date = DateField()
    equity_usd = DecimalField(max_digits=18, decimal_places=2)          # MTM total
    equity_cost_usd = DecimalField(max_digits=18, decimal_places=2, null=True)  # cost-basis
    return_pct = FloatField(null=True)                     # vs starting_capital_usd
    open_count = IntegerField(null=True)
    updated_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'portfolio_equity_snapshots'
        indexes = ((('run_id', 'date'), True),)            # unique per run per date

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)


class PortfolioFill(BaseModel):
    """A REAL, manually-recorded fill (P3.7, 2026-07-13) — this system is
    alerts + manual execution with no broker feed, so a real fill's price/
    qty only ever exists if the user records it via `trader portfolio
    record-fill`. Used ONLY by tools/slippage_report.py to validate the
    modeled execution-cost assumptions (the asymmetric-cost canon: mid-entry
    + limit-TP FREE, forced exits pay ~half-spread) against reality — never
    read by the live engine/sizing/scoring path, and never mutates a
    PortfolioPosition row.

    price is the premium PER CONTRACT (per-share quote, e.g. 2.35 for a
    $2.35/share = $235/contract fill), matching how a broker fill
    confirmation is normally read; fill_value_usd = price * qty * 100 is
    derived at record time so the report never has to re-derive it.
    pos_key is a best-effort auto-link to the corresponding
    PortfolioPosition (see portfolio_engine._resolve_fill_position) — NULL
    when no single clear candidate was found; the raw fill is still recorded
    either way (never lose real data) and can be reconciled later via
    --pos-key.
    """
    id = AutoField(primary_key=True)
    run_id = IntegerField(index=True, null=True)
    pos_key = CharField(max_length=96, null=True)
    symbol = CharField(max_length=16)
    side = CharField(max_length=8, default='buy')          # 'buy' (open) | 'sell' (close)
    price = DecimalField(max_digits=12, decimal_places=4)   # premium per contract
    qty = IntegerField()                                    # number of contracts
    fill_value_usd = DecimalField(max_digits=16, decimal_places=2)   # price * qty * 100
    filled_at = DateTimeField()                             # --ts, or now() if omitted
    notes = CharField(max_length=255, null=True)
    recorded_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'portfolio_fills'
        indexes = (
            (('symbol', 'filled_at'), False),
            (('pos_key',), False),
        )

    @classmethod
    def ensure_schema(cls):
        from database.trader_database import DB
        DB.create_tables([cls], safe=True)


def ensure_all_schema():
    PortfolioRun.ensure_schema()
    PortfolioPosition.ensure_schema()
    PortfolioEquitySnapshot.ensure_schema()
    PortfolioPendingAlert.ensure_schema()
    PortfolioFill.ensure_schema()
