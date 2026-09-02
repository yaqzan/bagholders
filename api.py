#!/usr/bin/env python3
"""
Flask REST API for the Trader Program
Provides RESTful endpoints for stock data, technical indicators, scores, options, and portfolio management.
"""

from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal
import json
import csv
import traceback
from pathlib import Path
from peewee import fn

# Add the Trader directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Trader'))

# Remove the parent dev directory from sys.path so Flask's reloader doesn't
# watch sibling projects (e.g. C:\Development\Archivist\...).
_api_root = os.path.dirname(os.path.abspath(__file__))
_dev_parent = os.path.normcase(os.path.dirname(_api_root))
sys.path = [p for p in sys.path if os.path.normcase(os.path.abspath(p or '.')) != _dev_parent]

from database.project_root import chdir_trader_project

chdir_trader_project()

from trader import Trader
from database import Stock, PriceHistory, Indicator, Score, Option, OptionPrice, Position, Trend, WeeklyPriceHistory, WeeklyIndicator, WeeklyScore, EarningsDate, DteRecommendation, HistoricPeak
from database.models.core import MarketRegime, MarketBreadth
from database.trader_database import DB
from dte_recommendation import get_signal_context, recommend_dte
from market_breadth import (
    SECTOR_ETF_MARKET_WAVE_PARAMS,
    compute_sector_etf_breadth,
    _load_sector_etf_breadth_rows,
)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes


def _ensure_db_connection():
    """Open or revive the request DB handle.

    PyMySQL can leave Peewee holding a socket object that is not marked closed
    after a long in-request compute phase. A ping is cheap and prevents the
    opaque ``InterfaceError(0, '')`` from leaking to API clients.
    """
    if DB.is_closed():
        DB.connect(reuse_if_open=True)
        return
    try:
        connection = DB.connection()
        ping = getattr(connection, 'ping', None)
        if callable(ping):
            ping(reconnect=True)
        else:
            DB.execute_sql('SELECT 1')
    except Exception:
        try:
            DB.close()
        except Exception:
            pass
        DB.connect(reuse_if_open=True)


@app.before_request
def _open_db_for_request():
    """Use a fresh DB handle per Flask request to avoid stale MySQL sockets."""
    _ensure_db_connection()


@app.teardown_request
def _close_db_after_request(_exc):
    if not DB.is_closed():
        DB.close()


# Score data changes intra-poll (every backend update overwrites today's row);
# any cached response served to the dashboard or earnings calendar is a stale-
# read risk. The 30s URL cache-bust bucket on the frontend (`buildApiUrl` in
# StockContext.js) only dedupes burst requests — between buckets nothing
# prevents browser/CDN/proxy caches from holding the response.
#
# Set `Cache-Control: no-store` on every score-bearing endpoint so the
# response is never persisted at any layer. Static endpoints (assessment
# results, MC sweeps) are cacheable for longer; we scope the no-store to
# paths that read live Score / HistoricPeak / earnings data.
_NO_STORE_PREFIXES = (
    '/api/stocks/',          # /api/stocks/all, /api/stocks/<sym>, /scores, /indicators, etc.
    '/api/market/',          # /api/market/breadth, /regime, /historic-peaks, /trends
    '/api/earnings/',        # /api/earnings/weekly (calendar)
    '/api/strategy/',        # shipped strategy params (config can change between polls)
    '/api/score/',           # curated algorithm selector metadata
    '/api/allocation/',      # live score rows and today's allocation plan
    '/api/portfolio/',       # live tracked portfolio (holdings + equity curve)
)


@app.after_request
def _no_store_for_live_data(response):
    """Force no-store on live-data paths so dashboards never read a cached score."""
    try:
        path = request.path or ''
        if path.startswith(_NO_STORE_PREFIXES):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    except Exception:
        pass
    return response


# Initialize trader client
trader = Trader()


def get_api_score_version():
    """Same persisted-score version as recalculate / dashboard (see AlgorithmVersion.get_active_scores_version)."""
    _ensure_db_connection()
    from database.models.core import AlgorithmVersion
    return AlgorithmVersion.get_active_scores_version()


def _sector_etf_breadth_payload(row, allow_live_fallback=False):
    """Serialize sector ETF breadth persisted on MarketBreadth."""
    def f(v, dp=2):
        return round(float(v), dp) if v is not None else None

    asof = getattr(row, 'sector_etf_asof_date', None)
    effective = getattr(row, 'sector_etf_effective_crash_echo', None)
    if asof is not None and effective is not None:
        return {
            'date': asof.isoformat(),
            'days_stale': (row.date - asof).days,
            'pct_above_ema50': f(row.sector_etf_pct_above_ema50),
            'pct_above_ema200': f(row.sector_etf_pct_above_ema200),
            'avg_rsi': f(row.sector_etf_avg_rsi),
            'breadth_1d_change': f(row.sector_etf_breadth_1d_change),
            'breadth_5d_change': f(row.sector_etf_breadth_5d_change),
            'breadth_10d_change': f(row.sector_etf_breadth_10d_change),
            'breadth_15d_change': f(row.sector_etf_breadth_15d_change),
            'breadth_avg5': f(row.sector_etf_breadth_avg5),
            'breadth_10d_position': f(row.sector_etf_breadth_10d_position),
            'breadth_30d_position': f(row.sector_etf_breadth_30d_position),
            'market_wave_score': f(row.sector_etf_market_wave_score),
            'market_wave_signed': f(row.sector_etf_market_wave_signed),
            'market_wave_state': row.sector_etf_market_wave_state,
            'crash_echo': f(row.sector_etf_crash_echo, 4),
            'bull_wave': f(row.sector_etf_bull_wave, 4),
            'effective_crash_echo': f(row.sector_etf_effective_crash_echo, 4),
            'source': row.sector_etf_source,
            'issues': row.sector_etf_issues,
        }

    if not allow_live_fallback:
        return None

    fallback = compute_sector_etf_breadth(row.date)
    if not fallback:
        return None
    return {
        'date': fallback['date'].isoformat(),
        'days_stale': (row.date - fallback['date']).days,
        'pct_above_ema50': fallback['pct_above_ema50'],
        'pct_above_ema200': fallback['pct_above_ema200'],
        'avg_rsi': fallback['avg_rsi'],
        'breadth_1d_change': fallback['breadth_1d_change'],
        'breadth_5d_change': fallback['breadth_5d_change'],
        'breadth_10d_change': fallback['breadth_10d_change'],
        'breadth_15d_change': fallback['breadth_15d_change'],
        'breadth_avg5': fallback['breadth_avg5'],
        'breadth_10d_position': fallback['breadth_10d_position'],
        'breadth_30d_position': fallback['breadth_30d_position'],
        'market_wave_score': fallback['market_wave_score'],
        'market_wave_signed': fallback['market_wave_signed'],
        'market_wave_state': fallback['market_wave_state'],
        'crash_echo': fallback['crash_echo'],
        'bull_wave': fallback['bull_wave'],
        'effective_crash_echo': fallback['effective_crash_echo'],
        'source': fallback.get('source'),
        'issues': fallback.get('issues'),
    }


# Process-level cache for realized-60d vol by (version_id, target_date).
# First /api/stocks/all request per day pays the ~10s PriceHistory scan;
# all subsequent polls (dashboard refreshes every 5-10min) hit the cache.
_VOL_CACHE = {}
_MARKET_TRENDS_CACHE = {
    'built_at': None,
    'end_date': None,
    'rows': None,
}


def _sector_etf_wave_raw(row):
    """Return the raw pre-tanh Market Wave terms for one sector ETF breadth row."""
    wave = SECTOR_ETF_MARKET_WAVE_PARAMS
    pct50 = row.get('pct_above_ema50')
    pct200 = row.get('pct_above_ema200')
    level50 = ((float(pct50) if pct50 is not None else 50.0) - 50.0) / 50.0
    level200 = ((float(pct200) if pct200 is not None else 50.0) - 50.0) / 50.0
    terms = {
        'level50': wave['level50_weight'] * level50,
        'level200': wave['level200_weight'] * level200,
        'd1': wave['d1_weight'] * (float(row.get('breadth_1d_change') or 0.0) / 100.0),
        'd5': wave['d5_weight'] * (float(row.get('breadth_5d_change') or 0.0) / 100.0),
        'd15': wave['d15_weight'] * (float(row.get('breadth_15d_change') or 0.0) / 100.0),
        'range10': wave['range10_weight'] * (float(row.get('breadth_10d_position') or 0.0) / 100.0),
        'range30': wave['range30_weight'] * (float(row.get('breadth_30d_position') or 0.0) / 100.0),
    }
    raw = sum(terms.values())
    return raw, terms


def _cached_sector_etf_wave_rows(end_date):
    """Build sector ETF wave history with a short process cache for UI requests."""
    now = datetime.now()
    cached_at = _MARKET_TRENDS_CACHE.get('built_at')
    cached_rows = _MARKET_TRENDS_CACHE.get('rows')
    cached_end = _MARKET_TRENDS_CACHE.get('end_date')
    if (
        cached_at is not None
        and cached_rows is not None
        and cached_end is not None
        and cached_end >= end_date
        and (now - cached_at).total_seconds() < 600
    ):
        return cached_rows

    rows = _load_sector_etf_breadth_rows(end_date=end_date)
    _MARKET_TRENDS_CACHE.update({
        'built_at': now,
        'end_date': end_date,
        'rows': rows,
    })
    return rows


def _stock_detail_cascade_skip(latest_score):
    """Per-stock cascade-skip computation for /api/stocks/<symbol>.
    Wraps _compute_cascade_skip with the EarningsDate-window lookup that
    /api/stocks/all does in bulk."""
    try:
        import strategy_config as _sc
        from database.utils.trading_calendar import is_trading_day as _is_td
        cfg = _sc.STRATEGY_30DTE
        signal_date = latest_score.date
        cursor = signal_date
        n = 0
        while n < cfg.EARN_SUPP_PUT_DAYS:
            cursor += timedelta(days=1)
            if _is_td(cursor): n += 1
        has_ern = (EarningsDate
                   .select()
                   .where((EarningsDate.symbol == latest_score.symbol)
                          & (EarningsDate.date > signal_date)
                          & (EarningsDate.date <= cursor))
                   .exists())
        syms_in_window = {latest_score.symbol_id} if has_ern else set()
        return _compute_cascade_skip(
            latest_score.overall,
            latest_score.stoch,
            latest_score.weight_info,
            latest_score.symbol_id,
            syms_in_window,
            cfg.EARN_SUPP_PUT_MIN_OV, cfg.EARN_SUPP_PUT_MAX_OV, cfg.EARN_SUPP_PUT_DAYS,
            cfg.WEAK_WEEKLY_CALL_DROP, cfg.WEAK_WEEKLY_CALL_MIN_OV, cfg.WEAK_WEEKLY_CALL_MAX_OV,
            cfg.WEAK_WEEKLY_CALL_WADJ_LT, cfg.WEAK_WEEKLY_CALL_STOCH_GE,
        )
    except Exception:
        return None


def _compute_cascade_skip(overall, stoch, weight_info_raw, sym,
                          syms_in_earn_window,
                          earn_supp_min, earn_supp_max, earn_supp_days,
                          wwc_active, wwc_min, wwc_max, wwc_wadj, wwc_stoch):
    """Return a {'reason', 'detail'} dict if the signal would be skipped by
    a default-on cascade-stage filter, or None if not skipped.

    Mirrors EARN_SUPP_PUT and WEAK_WEEKLY_CALL_DROP logic from the MC/backtest
    engines so the dashboard reflects exactly what the strategy will trade.
    """
    if overall is None:
        return None
    # EARN_SUPP_PUT — puts in [16,20] within N trd days of upcoming earnings
    if earn_supp_min <= overall <= earn_supp_max:
        if sym in syms_in_earn_window:
            return {
                'reason': 'EARN_SUPP_PUT',
                'detail': f'put with earnings within {earn_supp_days} trading days',
            }
    # WEAK_WEEKLY_CALL_DROP — calls in [MIN,MAX] with wadj<LT and stoch>=GE
    if wwc_active and wwc_min <= overall <= wwc_max:
        if stoch is not None and (wwc_stoch <= 0 or int(stoch) >= wwc_stoch):
            wadj = None
            if weight_info_raw:
                try:
                    wi = json.loads(weight_info_raw) if isinstance(weight_info_raw, str) else weight_info_raw
                    wa = wi.get('w_adj') if isinstance(wi, dict) else None
                    if wa is None and isinstance(wi, dict): wa = wi.get('weekly_adj')
                    if wa is not None: wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is not None and wadj < wwc_wadj:
                stoch_str = f' & stoch≥{wwc_stoch}' if wwc_stoch > 0 else ''
                return {
                    'reason': 'WEAK_WEEKLY_CALL_DROP',
                    'detail': f'weak weekly (w_adj<{wwc_wadj:.0f}{stoch_str})',
                }
    return None


def _load_dte(symbol: str, target_date=None):
    """Return a recommendation dict for (symbol, date). Prefers the persisted
    DteRecommendation row for the active version; falls back to a live compute
    so callers always get a result if scoring data is available."""
    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()
    q = (DteRecommendation
         .select()
         .where((DteRecommendation.symbol == symbol)
                & (DteRecommendation.version == version)))
    if target_date is not None:
        q = q.where(DteRecommendation.date == target_date)
    else:
        q = q.order_by(DteRecommendation.date.desc())
    row = q.first()
    if row is not None:
        return row.to_api_dict()

    # Fallback: live compute
    ctx = get_signal_context(symbol, target_date=target_date)
    if not ctx:
        return None
    rec = recommend_dte(**ctx)
    rec.pop('symbol', None)
    return rec


def _get_prev_version(current_version):
    """Return the AlgorithmVersion immediately before current_version, or None."""
    from database.models.core import AlgorithmVersion
    return (AlgorithmVersion.select()
            .where(
                (AlgorithmVersion.id < current_version.id)
                & (~(AlgorithmVersion.git_commit.startswith('shadow/')))
            )
            .order_by(AlgorithmVersion.id.desc())
            .first())


_HONEST_VERSIONS_CACHE = {'mtime': None, 'ids': set()}


def _honest_version_ids():
    """Set of AlgorithmVersion ids whose persisted scores are look-ahead-free.

    Sourced from algorithm_versions/honest_versions.json (v69/v70 shipped honest;
    the rest were honest-recalc'd 2026-06-02). Every other version still carries the
    pre-v69 weekly look-ahead ('inflated'). Cached by file mtime.

    NOTE: the JSON registry only matters for RETIRED (pre-v69) versions now —
    everything at/after the honest-era floor (v69+) is honest STRUCTURALLY and
    is flagged so in _version_payload without registry maintenance per ship
    (the stale registry hid v71/v72 from VersionCompare until 2026-06-12).
    """
    try:
        path = Path(os.getcwd()) / 'algorithm_versions' / 'honest_versions.json'
        if not path.exists():
            return set()
        mtime = path.stat().st_mtime
        if _HONEST_VERSIONS_CACHE['mtime'] != mtime:
            with path.open('r', encoding='utf-8') as fh:
                data = json.load(fh)
            _HONEST_VERSIONS_CACHE['ids'] = {int(v) for v in (data.get('honest_versions') or [])}
            _HONEST_VERSIONS_CACHE['mtime'] = mtime
        return _HONEST_VERSIONS_CACHE['ids']
    except Exception:
        return _HONEST_VERSIONS_CACHE.get('ids') or set()


def _version_payload(version, active_version=None):
    _ensure_db_connection()
    created_at = version.created_at
    display_description = getattr(version, 'display_description', None)
    is_active = bool(active_version and version.id == active_version.id)
    if is_active and not display_description:
        display_description = 'Current production baseline'
    return {
        'id': version.id,
        'label': version.production_label,
        'git_commit': version.git_commit,
        'git_message': version.git_message,
        'display_description': display_description,
        'selector_description': display_description,
        'cadence_enabled': bool(getattr(version, 'cadence_enabled', False)),
        'selector_rank': getattr(version, 'selector_rank', None),
        'created_at': (
            created_at.isoformat()
            if hasattr(created_at, 'isoformat')
            else str(created_at) if created_at else None
        ),
        'is_active': is_active,
        'honest': version.id >= _honest_era_floor() or version.id in _honest_version_ids(),
        'retired': version.id < _honest_era_floor(),
    }


def _honest_era_floor():
    """First structurally-honest version id (the v69+ honest era). Pre-floor
    versions are RETIRED: their score rows were purged 2026-06-12 and only
    research packs remain."""
    try:
        from database.models.core import AlgorithmVersion
        return int(AlgorithmVersion.CADENCE_MIN_VERSION_ID)
    except Exception:
        return 69


def _resolve_production_version(req_version):
    from database.models.core import AlgorithmVersion
    token = (req_version or '').strip()
    if not token:
        return None
    resolved = None
    if token.lower().startswith('v') and token[1:].isdigit():
        resolved = AlgorithmVersion.get_by_production_label(int(token[1:]))
    elif token.lower().startswith('db:') and token[3:].isdigit():
        resolved = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(token[3:]))
    elif token.isdigit():
        resolved = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(token))
    if resolved is None:
        resolved = (AlgorithmVersion
                    .select()
                    .where(
                        AlgorithmVersion.git_commit.startswith(token)
                        & (~(AlgorithmVersion.git_commit.startswith('shadow/')))
                    )
                    .first())
    if resolved and AlgorithmVersion.is_legacy_staging_commit(resolved.git_commit):
        return None
    return resolved


_SCORE_VERSION_CATALOG_CACHE = {}


def _score_version_catalog(active_version):
    from database.models.core import AlgorithmVersion

    bucket = int(datetime.now().timestamp() // 30)
    cache_key = (active_version.id if active_version else None, bucket)
    cached = _SCORE_VERSION_CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        AlgorithmVersion.ensure_schema()
        AlgorithmVersion.seed_selector_defaults()
    except Exception:
        pass

    all_versions = []
    versions = (
        AlgorithmVersion
        .select()
        .where(~(AlgorithmVersion.git_commit.startswith('shadow/')))
        .order_by(AlgorithmVersion.id.desc())
    )
    for version in versions:
        row = DB.execute_sql(
            "SELECT `date` FROM `scores` FORCE INDEX (`scores_version_date_IDX`) "
            "WHERE `version_id`=%s ORDER BY `date` DESC LIMIT 1",
            (version.id,),
        ).fetchone()
        latest_score_date = row[0] if row else None
        if not latest_score_date:
            continue
        item = _version_payload(version, active_version)
        item['latest_score_date'] = (
            latest_score_date.isoformat()
            if hasattr(latest_score_date, 'isoformat')
            else str(latest_score_date)
        )
        all_versions.append(item)

    active_id = active_version.id if active_version else None

    def _rank(item):
        rank = item.get('selector_rank')
        if rank is None:
            rank = 9999
        return (rank, -int(item.get('id') or 0))

    primary = []
    if active_id is not None:
        primary.extend([item for item in all_versions if item['id'] == active_id])
    cadence = [
        item for item in all_versions
        if item['id'] != active_id and item.get('cadence_enabled')
    ]
    primary.extend(sorted(cadence, key=_rank))
    if not primary and all_versions:
        primary.append(all_versions[0])

    primary_ids = {item['id'] for item in primary}
    legacy = [item for item in all_versions if item['id'] not in primary_ids]

    payload = {
        'available_versions': primary,
        'legacy_versions': legacy,
        'all_versions': primary + legacy,
    }

    _SCORE_VERSION_CATALOG_CACHE.clear()
    _SCORE_VERSION_CATALOG_CACHE[cache_key] = payload
    return payload


def _score_versions_with_rows(active_version):
    return _score_version_catalog(active_version)['available_versions']


def _score_version_response_fields(catalog):
    return {
        'available_versions': catalog.get('available_versions', []),
        'legacy_versions': catalog.get('legacy_versions', []),
    }


@app.route('/api/score/versions', methods=['GET'])
def get_score_version_options():
    try:
        active_version = get_api_score_version()
        if not active_version:
            return jsonify({'error': 'No active algorithm version found'}), 404
        catalog = _score_version_catalog(active_version)
        return jsonify({
            'version': _version_payload(active_version, active_version),
            'active_version_id': active_version.id,
            **_score_version_response_fields(catalog),
        })
    except Exception as e:
        print(f"ERROR in /api/score/versions: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


def _resolve_dashboard_score_version():
    active_version = get_api_score_version()
    req_version = request.args.get('version')
    if req_version:
        version = _resolve_production_version(req_version)
        if not version:
            return None, active_version, req_version
        return version, active_version, req_version
    return active_version, active_version, None


# Custom JSON encoder for Decimal and datetime objects
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

app.json_encoder = CustomJSONEncoder


# Add this to catch all errors and print them
@app.errorhandler(500)
def internal_error(error):
    print("=== 500 ERROR DETAILS ===")
    print(f"Error: {error}")
    print(traceback.format_exc())
    print("========================")
    return jsonify({"error": "Internal server error"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'trader-api'
    })

@app.route('/api/ping', methods=['GET'])
def ping():
    """Lightweight liveness probe used by the frontend status indicator."""
    return jsonify({'ok': True})


@app.route('/api/strategy/param-manifest', methods=['GET'])
def get_param_manifest():
    """Schema-driven portfolio-knob manifest for the Backtest advanced-params UI.

    Returns every portfolio (Stage-2/3) knob — grouped, with UI metadata, an
    'editable' flag (true = overridable per-run via /api/backtest/run; false =
    applies from config, reproducible but not yet UI-editable), and live default
    values for each profile so the UI can snap on a profile toggle. Driven
    entirely by portfolio_param_manifest.PARAMS, so a new strategy_config knob
    auto-appears here (CI guard: tests/test_portfolio_param_manifest.py).
    """
    import portfolio_param_manifest as _ppm
    import strategy_config as _sc
    import portfolio_profiles as _pp

    dte = (request.args.get('dte') or '30').strip()
    if dte not in ('30', '15'):
        dte = '30'
    base = _sc.STRATEGY_15DTE if dte == '15' else _sc.STRATEGY_30DTE

    manifest = _ppm.manifest_payload(base, dte)

    profile_defaults = {}
    for key in getattr(_pp, 'PROFILE_ORDER', ('sentinel', 'core', 'apex')):
        try:
            cobj, _prof = _pp.profiled_strategy_config(base, key)
            pl = _ppm.manifest_payload(cobj, dte)
            profile_defaults[key] = {p['key']: p['default'] for grp in pl for p in grp['params']}
        except Exception:
            continue

    n_editable = sum(1 for grp in manifest for p in grp['params'] if p['editable'])
    return jsonify({
        'dte_strategy': dte,
        'manifest': manifest,
        'profile_defaults': profile_defaults,
        'n_params': sum(len(grp['params']) for grp in manifest),
        'n_editable': n_editable,
        'editable_note': ('editable=true knobs are overridable per-run; editable=false '
                          'apply from the strategy config (reproducible; per-run edit coming).'),
    })


@app.route('/api/strategy/config', methods=['GET'])
def get_strategy_config():
    """Return shipped strategy parameters for both DTE strategies as JSON.

    Single source of truth = strategy_config.py. JS consumers (Backtest.js
    DEFAULT_ADVANCED + FIELD_TIPS, Dashboard.js BREADTH_TIPS + ternaries)
    fetch this on mount instead of hard-coding values that drift from the
    Python defaults.

    Includes all derived @property values (NET_TP_BASE, TP_SIGMA_BASE,
    NET_HARD_SELL etc.) so frontend can display correct net P&L and σ
    thresholds without re-deriving.

    Use ?strategy=30dte or ?strategy=15dte to fetch a single strategy;
    no param returns both keyed by '30dte' / '15dte'.
    """
    import strategy_config
    which = request.args.get('strategy', '').strip().lower()
    if which in ('30', '30dte'):
        return jsonify({'30dte': strategy_config.to_json_dict(strategy_config.STRATEGY_30DTE)})
    if which in ('15', '15dte'):
        return jsonify({'15dte': strategy_config.to_json_dict(strategy_config.STRATEGY_15DTE)})
    return jsonify({
        '30dte': strategy_config.to_json_dict(strategy_config.STRATEGY_30DTE),
        '15dte': strategy_config.to_json_dict(strategy_config.STRATEGY_15DTE),
    })


@app.route('/api/allocation/live', methods=['GET'])
def live_allocation_endpoint():
    """Return today's structured allocation plan, matching the trader alloc path."""
    try:
        raw_amount = (
            request.args.get('amount')
            or request.args.get('portfolio')
            or request.args.get('capital')
            or '50000'
        )
        try:
            amount = float(str(raw_amount).replace(',', ''))
        except (TypeError, ValueError):
            return jsonify({'error': 'amount must be a positive number'}), 400

        from allocation_plan import AllocationPlanError, build_live_allocation_plan

        payload = build_live_allocation_plan(
            amount,
            currency=request.args.get('currency', 'CAD'),
            strategy=request.args.get('strategy', '30dte'),
            portfolio_profile=request.args.get('profile') or request.args.get('portfolio_profile') or 'core',
            staging_variant=request.args.get('staging'),
            version=request.args.get('version'),
        )
        return jsonify(payload)
    except AllocationPlanError as exc:
        return jsonify({'error': str(exc), **exc.payload}), exc.status_code
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/portfolio/state', methods=['GET'])
def portfolio_state_endpoint():
    """Live tracked portfolio (v70 Apex): holdings, equity curve, summary.

    Serves the materialized state written by the portfolio engine during
    `trader update`. Pass ?sync=1 to force a fresh deterministic re-evaluation
    before serving (also auto-syncs when the stored state is stale or absent).
    """
    try:
        import portfolio_engine
        force = request.args.get('sync') in ('1', 'true', 'yes')
        if force:
            portfolio_engine.sync(send_notifications=False)
            payload = portfolio_engine.build_state_payload(ensure_fresh=False)
        else:
            # serve from the materialized state; lazily re-sync only if stale
            payload = portfolio_engine.build_state_payload(ensure_fresh=True)
        if payload is None:
            return jsonify({'error': 'portfolio not initialized'}), 404
        return jsonify(payload)
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


# /api/portfolio/pending is a heavy dry-run sync (~5-60s cold) polled by the
# Allocator landing page — TTL cache + single-flight lock so concurrent tabs /
# polls can never stack syncs (the API stays responsive during update runs).
_PENDING_CACHE = {'ts': 0.0, 'payload': None}
_PENDING_LOCK = __import__('threading').Lock()
_PENDING_TTL_S = 75


@app.route('/api/portfolio/pending', methods=['GET'])
def portfolio_pending_endpoint():
    """Live execution view for the Allocator (entry-timing canon, 2026-06-11).

    Returns the execution-window state (closed / pre_open / provisional /
    window) plus the engine's dry-run pending picture: would_open (entries the
    ledger will fill at TODAY's close if the signals hold), would_exit (sell-now
    barrier touches / sweeps), and carryover (positions filled at the last
    completed close — the open is the user's first executable moment, ~-1.3pp
    haircut). Read-only; writes and pushes nothing. Served from a 75s TTL cache;
    the execution_window block is recomputed fresh on every response."""
    import time as _time
    try:
        import portfolio_engine

        def _respond(payload, stale=False):
            # window state is cheap — always current even when pending is cached
            payload = dict(payload)
            payload['execution_window'] = portfolio_engine.execution_window_status()
            payload['stale'] = stale
            resp = jsonify(payload)
            resp.headers['Cache-Control'] = 'no-store'
            return resp

        now = _time.time()
        if _PENDING_CACHE['payload'] is not None and now - _PENDING_CACHE['ts'] < _PENDING_TTL_S:
            return _respond(_PENDING_CACHE['payload'])
        if _PENDING_LOCK.acquire(blocking=False):
            try:
                payload = portfolio_engine.build_pending_payload()
                _PENDING_CACHE['payload'] = payload
                _PENDING_CACHE['ts'] = _time.time()
                return _respond(payload)
            finally:
                _PENDING_LOCK.release()
        # another request is already computing — serve stale rather than stack
        if _PENDING_CACHE['payload'] is not None:
            return _respond(_PENDING_CACHE['payload'], stale=True)
        return jsonify({'error': 'pending view warming up', 'retry_in_s': 15}), 503
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/portfolio/sync', methods=['POST', 'GET'])
def portfolio_sync_endpoint():
    """Force a deterministic re-materialization (no notifications from the API)."""
    try:
        import portfolio_engine
        portfolio_engine.sync(send_notifications=False)
        payload = portfolio_engine.build_state_payload()
        return jsonify(payload or {'error': 'portfolio not initialized'})
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/stock/<symbol>/flag', methods=['GET', 'POST'])
def _flag_stock(symbol):
    stock = Stock.get_or_none(symbol=symbol.upper())
    if not stock:
        return jsonify({'error': 'not found'}), 404
    if stock.flagged:
        stock.unflag()
    else:
        stock.flag()
    return redirect(request.referrer or 'https://www.bagholders.ai/', code=303)


@app.route('/api/stock/<symbol>/unflag', methods=['GET', 'POST'])
def _unflag_stock(symbol):
    stock = Stock.get_or_none(symbol=symbol.upper())
    if not stock:
        return jsonify({'error': 'not found'}), 404
    if stock.flagged:
        stock.unflag()
    return redirect(request.referrer or 'https://www.bagholders.ai/', code=303)


@app.route('/api/stocks/<symbol>', methods=['GET'])
def get_stock(symbol):
    """Get detailed information for a specific stock"""
    try:
        stock = Stock.get_or_none(symbol=symbol.upper())
        if not stock:
            return jsonify({'error': f'Stock {symbol} not found'}), 404
        
        # Get latest price history
        latest_price = PriceHistory.select().where(PriceHistory.symbol == stock).order_by(PriceHistory.date.desc()).first()
        
        version = get_api_score_version()
        latest_score = (Score.select()
                        .where((Score.symbol == stock.symbol) & (Score.version == version) &
                               Score.overall.is_null(False))
                        .order_by(Score.date.desc())
                        .first())
        if latest_score is None:
            prev_v = _get_prev_version(version)
            if prev_v:
                latest_score = (Score.select()
                                .where((Score.symbol == stock.symbol) & (Score.version == prev_v) &
                                       Score.overall.is_null(False))
                                .order_by(Score.date.desc())
                                .first())
        
        stock_data = {
            'symbol': stock.symbol,
            'name': stock.name,
            'industry': stock.industry,
            'sector': stock.sector,
            'next_earnings': stock.next_earnings.isoformat() if stock.next_earnings else None,
            'eps': float(stock.eps) if stock.eps else None,
            'forward_eps': float(stock.forward_eps) if stock.forward_eps else None,
            'pe': float(stock.pe) if stock.pe else None,
            'forward_pe': float(stock.forward_pe) if stock.forward_pe else None,
            'peg': float(stock.peg) if stock.peg else None,
            'price_to_book': float(stock.price_to_book) if stock.price_to_book else None,
            'price_to_sales': float(stock.price_to_sales) if stock.price_to_sales else None,
            'profit_margin': float(stock.profit_margin) if stock.profit_margin else None,
            'roe': float(stock.roe) if stock.roe else None,
            'roa': float(stock.roa) if stock.roa else None,
            'revenue': float(stock.revenue) if stock.revenue else None,
            'revenue_growth': float(stock.revenue_growth) if stock.revenue_growth else None,
            'debt_to_equity': float(stock.debt_to_equity) if stock.debt_to_equity else None,
            'free_cash_flow': float(stock.free_cash_flow) if stock.free_cash_flow else None,
            'dividend_yield': float(stock.dividend_yield) if stock.dividend_yield else None,
            'market_cap': float(stock.market_cap) if stock.market_cap else None,
            'beta': float(stock.beta) if stock.beta else None,
            'target_mean_price': float(stock.target_mean_price) if stock.target_mean_price else None,
            'flagged': stock.flagged,
            'delisted_date': stock.delisted_date.isoformat() if stock.delisted_date else None,
            'current_price': float(stock.current_price()) if stock.current_price() else None,
            'current_volume': stock.current_volume(),
            'latest_price_data': {
                'date': latest_price.date.isoformat() if latest_price else None,
                'open': float(latest_price.open) if latest_price else None,
                'high': float(latest_price.high) if latest_price else None,
                'low': float(latest_price.low) if latest_price else None,
                'close': float(latest_price.close) if latest_price else None,
                'volume': latest_price.volume if latest_price else None
            } if latest_price else None,
            'latest_score': latest_score.output_hash() if latest_score else None,
            'ct_tag': (
                ('ct_call' if (latest_score.overall is not None and latest_score.overall >= 70
                               and latest_score.trend is not None and latest_score.trend <= 20)
                 else 'ct_put' if (latest_score.overall is not None and latest_score.overall <= 25
                                   and latest_score.trend is not None and latest_score.trend >= 80)
                 else None)
                if latest_score else None
            ),
            'cascade_skip': (
                _stock_detail_cascade_skip(latest_score)
                if latest_score else None
            ),
        }

        stock_data['dte_recommendation'] = _load_dte(symbol.upper())

        # Realized 60-day vol (daily %) — used to de-normalize sigma-based MAE stops
        try:
            from assess_scores import _realized_vol_pct, SWING_VOL_LOOKBACK
            closes_rows = list(PriceHistory.select(PriceHistory.close)
                               .where(PriceHistory.symbol == stock)
                               .order_by(PriceHistory.date.desc())
                               .limit(SWING_VOL_LOOKBACK + 5))
            closes = [float(r.close) for r in reversed(closes_rows)]
            if len(closes) >= SWING_VOL_LOOKBACK + 1:
                rv = _realized_vol_pct(closes, len(closes) - 1)
                stock_data['realized_vol_60d'] = round(rv, 3) if rv is not None else None
            else:
                stock_data['realized_vol_60d'] = None
        except Exception:
            stock_data['realized_vol_60d'] = None

        return jsonify(stock_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Scores endpoints
@app.route('/api/stocks/<symbol>/scores', methods=['GET'])
def get_scores(symbol):
    """Get scores for a stock"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int, default=100)
        
        version = get_api_score_version()
        query = Score.select().where(Score.symbol == symbol.upper(), Score.version == version,
                                     Score.overall.is_null(False))
        if start_date:
            query = query.where(Score.date >= start_date)
        if end_date:
            query = query.where(Score.date <= end_date)

        current_scores = list(query.order_by(Score.date.desc()).limit(limit))

        # If the current version doesn't fill the requested window, pad with the
        # previous version's scores for dates not already covered.
        if len(current_scores) < limit:
            prev_v = _get_prev_version(version)
            if prev_v:
                current_dates = {s.date for s in current_scores}
                fb_q = Score.select().where(Score.symbol == symbol.upper(),
                                            Score.version == prev_v,
                                            Score.overall.is_null(False))
                if start_date:
                    fb_q = fb_q.where(Score.date >= start_date)
                if end_date:
                    fb_q = fb_q.where(Score.date <= end_date)
                if current_dates:
                    fb_q = fb_q.where(Score.date.not_in(list(current_dates)))
                fallback = list(fb_q.order_by(Score.date.desc()).limit(limit - len(current_scores)))
                current_scores = sorted(current_scores + fallback,
                                        key=lambda s: s.date, reverse=True)[:limit]

        scores = current_scores
        
        result = []
        for score in scores:
            row = {
                'date': score.date.isoformat(),
                'overall': score.overall,
                'rsi': score.rsi,
                'macd': score.macd,
                'trend': score.trend,
                'volume': score.volume,
                'volume_signal': score.volume_signal,
                'volume_magnitude': float(score.volume_magnitude) if score.volume_magnitude else None,
                'bb': score.bb,
                'stoch': score.stoch,
                'ma20': score.ma20,
                'technical_alignment': score.technical_alignment,
                'high_30': float(score.high_30) if score.high_30 else None,
                'high_60': float(score.high_60) if score.high_60 else None,
                'high_90': float(score.high_90) if score.high_90 else None,
                'weights': json.loads(score.weight_info) if score.weight_info else None
            }
            # Match PriceChart "opportunity zones" (green/red markers): score ≥75 or ≤25
            if score.overall >= 75 or score.overall <= 25:
                rec = _load_dte(symbol.upper(), target_date=score.date)
                if rec:
                    row['dte_recommendation'] = rec
            result.append(row)
        
        return jsonify({
            'symbol': symbol.upper(),
            'scores': result,
            'count': len(result)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Per-bucket win rates over the last year for a single stock.
# Clusters consecutive qualifying signals (within MIN_EVENT_GAP days) into one
# event so the same move isn't counted multiple times. For each clustered peak,
# runs the standard barrier-touch walk against forward price data and returns
# WR + N per discrete score bucket per period (7d / 15d / 30d).
@app.route('/api/stocks/<symbol>/bucket-winrates', methods=['GET'])
def get_bucket_winrates(symbol):
    try:
        from historic_peaks import (
            _barrier_wins, _realized_vol,
            _SWING_VOL_LOOKBACK,
        )

        sym = symbol.upper()
        days = request.args.get('days', type=int, default=365)
        version = get_api_score_version()

        today = date.today()
        cutoff = today - timedelta(days=days)

        call_min = 75
        put_max = 25

        rows = list(
            Score.select(Score.date, Score.overall, Score.price)
            .where(
                Score.symbol == sym,
                Score.version == version,
                Score.date >= cutoff,
                Score.date <= today,
                Score.overall.is_null(False),
                ((Score.overall >= call_min) | (Score.overall <= put_max)),
            )
            .order_by(Score.date)
        )

        if not rows:
            return jsonify({'symbol': sym, 'days': days, 'buckets': []})

        # Use every qualifying day as its own signal — no event clustering.
        # The clustered peak count is shown on historic_peaks; here we want
        # the full N so the WR and N reflect all actual signal occurrences.
        peaks = rows

        # Load price history (with vol buffer) for the forward walk.
        vol_buffer = timedelta(days=120)
        ph_rows = list(
            PriceHistory.select(
                PriceHistory.date, PriceHistory.high, PriceHistory.low, PriceHistory.close,
            )
            .where(
                PriceHistory.symbol == sym,
                PriceHistory.date >= cutoff - vol_buffer,
                PriceHistory.date <= today,
            )
            .order_by(PriceHistory.date)
        )
        ph = [(r.date, float(r.high), float(r.low), float(r.close)) for r in ph_rows]

        def _bucket(score):
            if score >= 95: return '95+'
            if score >= 90: return '90-94'
            if score >= 85: return '85-89'
            if score >= 80: return '80-84'
            if score >= 75: return '75-79'
            if score <= 5:  return '<5'
            if score <= 10: return '6-10'
            if score <= 15: return '11-15'
            if score <= 20: return '16-20'
            if score <= 25: return '21-25'
            return None

        BUCKET_ORDER = ['95+', '90-94', '85-89', '80-84', '75-79',
                        '21-25', '16-20', '11-15', '6-10', '<5']
        PERIODS = ('7d', '15d', '30d')

        agg = {b: {p: {'wins': 0, 'n': 0} for p in PERIODS} for b in BUCKET_ORDER}

        for peak in peaks:
            b = _bucket(peak.overall)
            if b is None:
                continue
            entry = float(peak.price) if peak.price else None
            if not entry or entry <= 0:
                continue
            # Locate peak index in price history.
            peak_idx = None
            for i, (d, _, _, _) in enumerate(ph):
                if d == peak.date:
                    peak_idx = i
                    break
            if peak_idx is None:
                continue
            vol_closes = [c for _, _, _, c in ph[max(0, peak_idx - _SWING_VOL_LOOKBACK):peak_idx + 1]]
            vol_pct = _realized_vol(vol_closes)
            if vol_pct is None or vol_pct <= 0:
                continue
            fwd_bars = [(d, h, l, c) for d, h, l, c in ph[peak_idx + 1:]]
            peak_type = 'HIGH' if peak.overall >= 50 else 'LOW'
            wins, _reach = _barrier_wins(peak_type, entry, vol_pct, fwd_bars, peak.date)
            for p in PERIODS:
                w = wins.get(p)
                if w is None:
                    continue
                agg[b][p]['n'] += 1
                if w == 1:
                    agg[b][p]['wins'] += 1

        out = []
        for b in BUCKET_ORDER:
            cells = agg[b]
            total_n = sum(cells[p]['n'] for p in PERIODS)
            if total_n == 0:
                continue
            row = {
                'bucket': b,
                'side': 'call' if b in ('95+', '90-94', '85-89', '80-84', '75-79') else 'put',
            }
            for p in PERIODS:
                n = cells[p]['n']
                row[f'wr_{p}'] = round(cells[p]['wins'] / n * 100, 1) if n > 0 else None
                row[f'n_{p}'] = n
            out.append(row)

        return jsonify({
            'symbol': sym,
            'days': days,
            'signal_count': len(peaks),
            'buckets': out,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# Indicators endpoints
@app.route('/api/stocks/<symbol>/indicators', methods=['GET'])
def get_indicators(symbol):
    """Get technical indicators for a stock"""
    try:
        # Query parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int, default=100)
        
        query = Indicator.select().where(Indicator.symbol == symbol.upper())
        
        if start_date:
            query = query.where(Indicator.date >= start_date)
        if end_date:
            query = query.where(Indicator.date <= end_date)
        
        indicators = query.order_by(Indicator.date.desc()).limit(limit)
        
        result = []
        for ind in indicators:
            result.append({
                'date': ind.date.isoformat(),
                'macd': float(ind.macd) if ind.macd else None,
                'macd_signal': float(ind.macd_signal) if ind.macd_signal else None,
                'macd_hist': float(ind.macd_hist) if ind.macd_hist else None,
                'rsi': float(ind.rsi) if ind.rsi else None,
                'stoch': float(ind.stoch) if ind.stoch else None,
                'stoch_signal': float(ind.stoch_signal) if ind.stoch_signal else None,
            })
        
        return jsonify({
            'symbol': symbol.upper(),
            'indicators': result,
            'count': len(result)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Price History endpoints
@app.route('/api/stocks/<symbol>/price-history', methods=['GET'])
def get_price_history(symbol):
    """Get price history for a stock"""
    try:
        # Query parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int, default=100)
        
        query = PriceHistory.select().where(PriceHistory.symbol == symbol.upper())
        
        if start_date:
            query = query.where(PriceHistory.date >= start_date)
        if end_date:
            query = query.where(PriceHistory.date <= end_date)
        
        price_history = query.order_by(PriceHistory.date.desc()).limit(limit)
        
        result = []
        for ph in price_history:
            result.append({
                'date': ph.date.isoformat(),
                'open': float(ph.open),
                'high': float(ph.high),
                'low': float(ph.low),
                'close': float(ph.close),
                'volume': ph.volume
            })
        
        return jsonify({
            'symbol': symbol.upper(),
            'price_history': result,
            'count': len(result)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/earnings-dates', methods=['GET'])
def get_earnings_dates(symbol):
    try:
        rows = (EarningsDate
            .select(EarningsDate.date)
            .where(EarningsDate.symbol == symbol.upper())
            .order_by(EarningsDate.date))
        return jsonify({'symbol': symbol.upper(), 'dates': [r.date.isoformat() for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/weekly-price-history', methods=['GET'])
def get_weekly_price_history(symbol):
    """Get weekly price history for a stock"""
    try:
        limit = request.args.get('limit', type=int, default=52)
        query = WeeklyPriceHistory.select().where(WeeklyPriceHistory.symbol == symbol.upper())
        weekly_history = query.order_by(WeeklyPriceHistory.date.desc()).limit(limit)
        
        result = []
        for wh in weekly_history:
            result.append({
                'date': wh.date.isoformat(),
                'open': float(wh.open),
                'high': float(wh.high),
                'low': float(wh.low),
                'close': float(wh.close),
                'volume': wh.volume
            })
        
        return jsonify({
            'symbol': symbol.upper(),
            'weekly_price_history': result,
            'count': len(result)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/weekly-indicators', methods=['GET'])
def get_weekly_indicators(symbol):
    """Get weekly technical indicators for a stock.

    Includes kijun_sen (Ichimoku 26-week base line midpoint) computed on-demand
    from WeeklyPriceHistory — used to visualize where the v44 ICH dampener
    fires (price below kijun_sen = bearish weekly state = ICH active).
    """
    try:
        sym = symbol.upper()
        limit = request.args.get('limit', type=int, default=250)
        query = WeeklyIndicator.select().where(WeeklyIndicator.symbol == sym)
        indicators = list(query.order_by(WeeklyIndicator.date.desc()).limit(limit))

        # ── Kijun-sen overlay (Ichimoku 26-week midpoint of high+low) ────────
        # Pull weekly price history covering all returned dates + 26-week lookback
        # so we can compute kijun-sen at the earliest returned date.
        kijun_map = {}
        if indicators:
            from database.models.technical import WeeklyPriceHistory
            from datetime import timedelta as _timedelta
            min_ind_date = min(ind.date for ind in indicators)
            wph_lo = min_ind_date - _timedelta(days=26 * 7 + 14)   # 26 weeks + small buffer
            wph_rows = list(
                WeeklyPriceHistory.select(
                    WeeklyPriceHistory.date,
                    WeeklyPriceHistory.high,
                    WeeklyPriceHistory.low,
                ).where(
                    (WeeklyPriceHistory.symbol == sym)
                    & (WeeklyPriceHistory.date >= wph_lo)
                ).order_by(WeeklyPriceHistory.date.asc())
            )
            # For each weekly date with >= 26 prior bars, compute kijun_sen
            for i in range(25, len(wph_rows)):
                window = wph_rows[i - 25:i + 1]   # 26 bars inclusive
                try:
                    highs = [float(w.high) for w in window if w.high is not None]
                    lows  = [float(w.low)  for w in window if w.low  is not None]
                except (TypeError, ValueError):
                    continue
                if len(highs) < 26 or len(lows) < 26:
                    continue
                kijun_map[wph_rows[i].date] = (max(highs) + min(lows)) / 2.0

        result = []
        for ind in indicators:
            result.append({
                'date': ind.date.isoformat(),
                'macd': float(ind.macd) if ind.macd else None,
                'macd_signal': float(ind.macd_signal) if ind.macd_signal else None,
                'macd_hist': float(ind.macd_hist) if ind.macd_hist else None,
                'rsi': float(ind.rsi) if ind.rsi else None,
                'ema_50': float(ind.ema_50) if ind.ema_50 else None,
                'ema_200': float(ind.ema_200) if ind.ema_200 else None,
                'upper_band': float(ind.upper_band) if ind.upper_band else None,
                'middle_band': float(ind.middle_band) if ind.middle_band else None,
                'lower_band': float(ind.lower_band) if ind.lower_band else None,
                'kijun_sen':  round(kijun_map[ind.date], 4) if ind.date in kijun_map else None,
            })

        return jsonify({
            'symbol': sym,
            'indicators': result,
            'count': len(result)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/weekly-scores', methods=['GET'])
def get_weekly_scores(symbol):
    """Get weekly scores for a stock"""
    try:
        limit = request.args.get('limit', type=int, default=250)
        query = WeeklyScore.select().where(WeeklyScore.symbol == symbol.upper())
        scores = query.order_by(WeeklyScore.date.desc()).limit(limit)
        
        result = []
        for s in scores:
            result.append({
                'date': s.date.isoformat(),
                'composite': s.composite,
                'rsi': s.rsi,
                'macd': s.macd,
                'trend': s.trend
            })
        
        return jsonify({
            'symbol': symbol.upper(),
            'scores': result,
            'count': len(result)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Options clusters endpoint
@app.route('/api/stocks/<symbol>/options-clusters', methods=['GET'])
def get_options_clusters(symbol):
    """Aggregate options volume and open interest into strike/expiration clusters for future dates.

    Returns top clusters and summary totals for scaling and sidebar bars.
    """
    try:
        symbol = symbol.upper()
        days_ahead = request.args.get('days_ahead', type=int, default=365)
        max_clusters = request.args.get('max', type=int, default=150)

        today = date.today()
        future_cutoff = today + timedelta(days=days_ahead)

        # For each option, take the latest OptionPrice (by date), then aggregate by expiration and strike
        # Use a correlated subquery to get latest price row per option (MySQL-friendly)
        latest_prices = (OptionPrice
            .select(OptionPrice, Option)
            .join(Option, on=(OptionPrice.option == Option.id))
            .where(
                (Option.symbol == symbol) &
                (Option.expiration_date >= today) &
                (Option.expiration_date <= future_cutoff) &
                (
                    OptionPrice.date == (
                        OptionPrice
                            .select(fn.MAX(OptionPrice.date))
                            .where(OptionPrice.option == Option.id)
                    )
                )
            ))

        # Aggregate in Python for clarity
        clusters_map = {}
        totals = {
            'call_volume': 0,
            'put_volume': 0,
            'call_oi': 0,
            'put_oi': 0
        }

        max_values = {
            'volume': 0,
            'oi': 0,
            'combined': 0
        }

        for op in latest_prices:
            option = op.option
            key = (option.expiration_date, float(option.strike_price))
            if key not in clusters_map:
                clusters_map[key] = {
                    'expiration_date': option.expiration_date,
                    'strike': float(option.strike_price),
                    'call_volume': 0,
                    'put_volume': 0,
                    'call_oi': 0,
                    'put_oi': 0
                }
            bucket = clusters_map[key]
            if (option.option_type or '').upper().startswith('C'):
                bucket['call_volume'] += int(op.volume or 0)
                bucket['call_oi'] += int(op.open_interest or 0)
                totals['call_volume'] += int(op.volume or 0)
                totals['call_oi'] += int(op.open_interest or 0)
            else:
                bucket['put_volume'] += int(op.volume or 0)
                bucket['put_oi'] += int(op.open_interest or 0)
                totals['put_volume'] += int(op.volume or 0)
                totals['put_oi'] += int(op.open_interest or 0)

        clusters = []
        for bucket in clusters_map.values():
            total_vol = bucket['call_volume'] + bucket['put_volume']
            total_oi = bucket['call_oi'] + bucket['put_oi']
            combined = total_vol + total_oi
            max_values['volume'] = max(max_values['volume'], total_vol)
            max_values['oi'] = max(max_values['oi'], total_oi)
            max_values['combined'] = max(max_values['combined'], combined)
            bucket['total_volume'] = total_vol
            bucket['total_oi'] = total_oi
            bucket['combined'] = combined
            clusters.append(bucket)

        # Sort clusters by combined size descending and limit
        clusters.sort(key=lambda x: x['combined'], reverse=True)
        clusters = clusters[:max_clusters]

        return jsonify({
            'symbol': symbol,
            'as_of': today.isoformat(),
            'clusters': [
                {
                    'expiration_date': c['expiration_date'].isoformat() if hasattr(c['expiration_date'], 'isoformat') else str(c['expiration_date']),
                    'strike': c['strike'],
                    'call_volume': c['call_volume'],
                    'put_volume': c['put_volume'],
                    'call_oi': c['call_oi'],
                    'put_oi': c['put_oi'],
                    'total_volume': c['total_volume'],
                    'total_oi': c['total_oi'],
                    'combined': c['combined']
                }
                for c in clusters
            ],
            'totals': totals,
            'max_values': max_values,
            'params': {
                'days_ahead': days_ahead,
                'max_clusters': max_clusters
            }
        })
    except Exception as e:
        print('ERROR in /api/stocks/<symbol>/options-clusters:', str(e))
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/all', methods=['GET'])
def get_all_stocks_with_scores():
    """Get all stocks with scores (no filtering applied)"""
    try:
        limit = request.args.get('limit', type=int, default=1000)
        version, active_version, req_version = _resolve_dashboard_score_version()
        version_catalog = _score_version_catalog(active_version)
        if req_version and not version:
            return jsonify({
                'error': f'Algorithm version not found: {req_version}',
                'active_version_id': active_version.id if active_version else None,
                **_score_version_response_fields(version_catalog),
            }), 404
        target_date = Score.select(fn.MAX(Score.date)).where(Score.version == version).scalar()

        # If the active/default version has no scores yet, fall back entirely
        # to the previous version. Explicit historical selections never fall
        # through to a different version.
        if not target_date:
            prev_v = _get_prev_version(version) if not req_version else None
            if prev_v:
                version = prev_v
                target_date = Score.select(fn.MAX(Score.date)).where(Score.version == version).scalar()
            if not target_date:
                return jsonify({
                    'error': f'No scores available for {version.production_label}',
                    'active_version_id': active_version.id if active_version else None,
                    **_score_version_response_fields(version_catalog),
                }), 404

        active_positions = set(Position.active_positions())

        # would_trade: {eligible, reason, detail} — gameplan P4 tradability pill
        # (known-issues.md OPEN WORK #0, reduced scope). Reuses the SAME dry-run
        # pending payload /api/portfolio/pending already computes and caches
        # (_PENDING_CACHE below) — PASSIVE read only. This endpoint never
        # acquires _PENDING_LOCK or triggers a fresh sync itself: that ~5-60s
        # cold-cache cost stays exclusively on /api/portfolio/pending's own
        # TTL+lock, so a cold pending cache just means would_trade is null
        # (unknown) for every row here, never a slow /api/stocks/all response.
        # "already held" is checked against the LIVE PortfolioRun ledger
        # (database.models.portfolio.PortfolioPosition) — NOT the unrelated
        # legacy `Position`/`active_positions` above, which is a separate,
        # older manual-holdings table.
        _would_open_syms = None       # None = no trustworthy would-open list right now
        _has_live_day = False          # would_open is ONLY meaningful during a live session
        _new_entries_halted = False    # standing state (sprint 2x watchdog / pause) -- true regardless of _has_live_day
        _sprint_halt = False
        _live_held_syms = set()
        _calls_only_live = True
        _min_score_for_trade = 70.0
        try:
            import portfolio_engine as _pe_wt
            _min_score_for_trade = float(_pe_wt.MIN_SCORE)
            _cached_pending = _PENDING_CACHE.get('payload')
            if _cached_pending:
                _pending_meta = _cached_pending.get('pending') or {}
                _has_live_day = bool(_pending_meta.get('live_day'))
                _new_entries_halted = bool(_pending_meta.get('new_entries_halted'))
                _sprint_halt = bool(_pending_meta.get('sprint_halt_new_entries'))
                if _has_live_day:
                    _would_open_syms = {w['symbol'] for w in _pending_meta.get('would_open', [])}
                # else leave _would_open_syms=None -- outside a live session would_open is
                # structurally empty (nothing to do with per-signal filtering), so treating
                # it as "unknown" avoids a false 'filtered' verdict for most of the day.
            from database.models.portfolio import PortfolioRun, PortfolioPosition
            _live_run = PortfolioRun.get_active()
            if _live_run is not None:
                _live_held_syms = {
                    p.symbol for p in PortfolioPosition.select(PortfolioPosition.symbol).where(
                        (PortfolioPosition.run_id == _live_run.id) & (PortfolioPosition.status == 'open'))
                }
                import portfolio_profiles as _pp_wt
                _cfg_wt, _ = _pp_wt.apply_profile_to_config({}, _live_run.profile)
                _calls_only_live = all(
                    float((_cfg_wt.get('tier_alloc') or {}).get(k, 0) or 0) == 0
                    for k in ('p<=15', 'p16-20', 'p21-25'))
        except Exception:
            pass

        def _would_trade(sym, overall_score):
            """Best-effort tradability verdict for one row. None = not applicable
            (score isn't in a qualifying zone, no live session to open against
            right now, or the pending cache hasn't warmed yet) — deliberately
            quiet rather than a guess."""
            if overall_score is None:
                return None
            if sym in _live_held_syms:
                return {'eligible': True, 'reason': 'held', 'detail': 'already an open position'}
            if overall_score <= 25:
                if _calls_only_live:
                    return {'eligible': False, 'reason': 'puts_off',
                            'detail': 'puts are OFF in the live portfolio profile'}
                return None  # puts are live somewhere -- no cheap generic verdict without the engine walk
            if overall_score < _min_score_for_trade:
                return None  # below the cascade admission threshold -- score itself already says "not a signal"
            if _new_entries_halted:
                return {'eligible': False, 'reason': 'halted',
                        'detail': ('new entries halted: sprint 2x watchdog' if _sprint_halt
                                  else 'new entries halted: portfolio paused')}
            if _would_open_syms is None:
                return None  # no live session to open against right now (or cache cold) -- unknown, not "filtered"
            if sym in _would_open_syms:
                return {'eligible': True, 'reason': 'would_open', 'detail': 'would open at today\'s close'}
            return {'eligible': False, 'reason': 'filtered',
                    'detail': 'qualifies by score but filtered by current portfolio state '
                              '(DD band / breadth / slot cap / dampener) -- see the Allocator'}

        # Intraday score-swing map for the latest date (one GROUP BY over
        # score_intraday_logs). Powers the dashboard SwingBadge / fakeout flag.
        # Cheap aggregate only — per-symbol cause attribution is on-demand via
        # /api/stocks/<sym>/intraday-swing. Never let it break the endpoint.
        try:
            from intraday_diagnostics import today_swing_map as _today_swing_map
            _swing_map, _ = _today_swing_map(version, target_date)
        except Exception:
            _swing_map = {}

        _score_join_fields = (Score, Stock.name, Stock.symbol, Stock.pe, Stock.forward_pe,
                              Stock.eps, Stock.forward_eps, Stock.market_cap, Stock.flagged)

        scores_list = list(Score
                           .select(*_score_join_fields)
                           .join(Stock, on=(Score.symbol == Stock.symbol))
                           .where((Score.date == target_date) & (Score.version == version) &
                                  (Score.overall.is_null(False)) &
                                  Stock.delisted_date.is_null())
                           .limit(limit))

        # Only run the prev-version fallback when the primary query returned
        # nothing — partial-gap coverage is rare and not worth a second 500-row
        # Score+Stock join on every dashboard poll.
        if not scores_list:
            prev_v = _get_prev_version(version) if not req_version else None
            if prev_v:
                prev_target = Score.select(fn.MAX(Score.date)).where(Score.version == prev_v).scalar()
                if prev_target:
                    version = prev_v
                    target_date = prev_target
                    scores_list = list(Score
                                       .select(*_score_join_fields)
                                       .join(Stock, on=(Score.symbol == Stock.symbol))
                                       .where((Score.date == prev_target) & (Score.version == prev_v) &
                                              Score.overall.is_null(False) &
                                              Stock.delisted_date.is_null())
                                       .limit(limit))

        # Bulk-load persisted DTE rows for the active version on target_date
        # so the per-stock loop is one dict lookup, not 500 recomputes.
        dte_by_sym = {
            r.symbol_id: r for r in DteRecommendation.select().where(
                (DteRecommendation.version == version)
                & (DteRecommendation.date == target_date)
            )
        }

        # Bulk-load next earnings date per symbol from EarningsDate (source of
        # truth). Stock.next_earnings / Score.next_earnings are cached snapshots
        # that go stale until pull_stock_data runs for that specific stock.
        today = date.today()
        next_earnings_by_sym = {}
        next_earnings_call_time_by_sym = {}
        try:
            for row in (EarningsDate
                        .select(EarningsDate.symbol, fn.MIN(EarningsDate.date).alias('next_date'))
                        .where(EarningsDate.date >= today)
                        .group_by(EarningsDate.symbol)):
                next_earnings_by_sym[row.symbol_id] = row.next_date
            if next_earnings_by_sym:
                pairs = list(next_earnings_by_sym.items())
                for row in (EarningsDate
                            .select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                            .where(EarningsDate.symbol.in_([s for s, _ in pairs]))):
                    if next_earnings_by_sym.get(row.symbol_id) == row.date:
                        next_earnings_call_time_by_sym[row.symbol_id] = row.call_time
        except Exception:
            pass

        # Bulk-load weekly composite ('W') for the week containing target_date.
        # Score.output_hash() triggers a per-row WeeklyScore.get_or_none — that
        # N+1 is 600+ extra queries on every dashboard hit.
        week_start = target_date - timedelta(days=target_date.weekday())
        weekly_by_sym = {
            w.symbol_id: w.composite
            for w in WeeklyScore.select(WeeklyScore.symbol, WeeklyScore.composite)
                                .where(WeeklyScore.date == week_start)
        }

        # Bulk-load forward earnings windows for cascade-skip computation.
        # EARN_SUPP_PUT drops puts in [16,20] when an EarningsDate falls in
        # (target_date, target_date + N trading days].  We pre-compute the set
        # of symbols whose next earnings is within that window.
        try:
            import strategy_config as _sc_skip
            from database.utils.trading_calendar import is_trading_day as _is_td
            _earn_supp_days = _sc_skip.STRATEGY_30DTE.EARN_SUPP_PUT_DAYS
            _earn_supp_min  = _sc_skip.STRATEGY_30DTE.EARN_SUPP_PUT_MIN_OV
            _earn_supp_max  = _sc_skip.STRATEGY_30DTE.EARN_SUPP_PUT_MAX_OV
            _wwc_active     = _sc_skip.STRATEGY_30DTE.WEAK_WEEKLY_CALL_DROP
            _wwc_min        = _sc_skip.STRATEGY_30DTE.WEAK_WEEKLY_CALL_MIN_OV
            _wwc_max        = _sc_skip.STRATEGY_30DTE.WEAK_WEEKLY_CALL_MAX_OV
            _wwc_wadj       = _sc_skip.STRATEGY_30DTE.WEAK_WEEKLY_CALL_WADJ_LT
            _wwc_stoch      = _sc_skip.STRATEGY_30DTE.WEAK_WEEKLY_CALL_STOCH_GE
        except Exception:
            _earn_supp_days, _earn_supp_min, _earn_supp_max = 5, 16, 20
            _wwc_active, _wwc_min, _wwc_max, _wwc_wadj, _wwc_stoch = True, 70, 84, 0.0, 35
            _is_td = lambda d: d.weekday() < 5
        # Map signal_date → trading-day-ahead boundary (target_date + EARN_SUPP_PUT_DAYS trading days)
        _td_cursor = target_date
        _td_count = 0
        while _td_count < _earn_supp_days:
            _td_cursor += timedelta(days=1)
            if _is_td(_td_cursor):
                _td_count += 1
        _earn_window_end = _td_cursor
        # Set of symbols with EarningsDate strictly in (target_date, _earn_window_end]
        _syms_in_earn_window = set()
        try:
            for row in (EarningsDate
                        .select(EarningsDate.symbol)
                        .where((EarningsDate.date > target_date)
                               & (EarningsDate.date <= _earn_window_end))
                        .distinct()):
                _syms_in_earn_window.add(row.symbol_id)
        except Exception:
            pass

        # Realized 60d vol keyed by symbol — cached per (version, target_date)
        # at module level since this is a ~10s PriceHistory scan on production
        # and target_date only changes once per day.
        cache_key = (version.id if version else None, target_date)
        realized_vol_by_sym = _VOL_CACHE.get(cache_key)
        if realized_vol_by_sym is None:
            realized_vol_by_sym = {}
            try:
                from assess_scores import _realized_vol_pct, SWING_VOL_LOOKBACK
                from collections import defaultdict
                symbols_for_vol = [s.symbol.symbol for s in scores_list]
                vol_cutoff = target_date - timedelta(days=SWING_VOL_LOOKBACK * 2 + 10)
                closes_by_sym = defaultdict(list)
                for row in (PriceHistory
                            .select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close)
                            .where((PriceHistory.symbol.in_(symbols_for_vol))
                                   & (PriceHistory.date >= vol_cutoff)
                                   & (PriceHistory.date <= target_date))
                            .order_by(PriceHistory.symbol, PriceHistory.date)):
                    closes_by_sym[row.symbol_id].append(float(row.close))
                for sym, closes in closes_by_sym.items():
                    if len(closes) >= SWING_VOL_LOOKBACK + 1:
                        rv = _realized_vol_pct(closes, len(closes) - 1)
                        if rv is not None:
                            realized_vol_by_sym[sym] = round(rv, 3)
                # Keep only the most recent date's entries to cap memory.
                _VOL_CACHE.clear()
                _VOL_CACHE[cache_key] = realized_vol_by_sym
            except Exception:
                pass

        result = []
        for score in scores_list:
            stock = score.symbol
            sym = stock.symbol
            overall_score = score.overall
            default_sort_value = overall_score if overall_score is not None else -1

            dte_thesis = dte_target = dte_min = dte_max = dte_confidence = None
            dte_tradeable = False
            dte_filter_side = None
            dte_row = dte_by_sym.get(sym)
            if dte_row is not None:
                dte_thesis = dte_row.thesis
                dte_target = dte_row.dte_target
                dte_min = dte_row.dte_min
                dte_max = dte_row.dte_max
                dte_confidence = dte_row.confidence
                dte_tradeable = bool(dte_row.tradeable)
                dte_filter_side = dte_row.filter_side
            elif score.pct_from_ema50 is not None and score.pct_from_ema200 is not None:
                # Fallback: live compute when no persisted row exists yet
                try:
                    rec = recommend_dte(
                        symbol=sym,
                        score=overall_score,
                        volume_signal=score.volume_signal,
                        volume_magnitude=score.volume_magnitude,
                        pct_from_ema50=float(score.pct_from_ema50),
                        pct_from_ema200=float(score.pct_from_ema200),
                        bb_position=float(score.bb_position) if score.bb_position is not None else 0.5,
                        score_velocity_7d=score.score_velocity_7d,
                        target_date=target_date,
                    )
                    dte_thesis = rec['thesis']
                    dte_target = rec['dte_target']
                    dte_min = rec['dte_min']
                    dte_max = rec['dte_max']
                    dte_confidence = rec['confidence']
                    dte_tradeable = bool(rec.get('tradeable'))
                    dte_filter_side = rec.get('filter_side')
                except Exception:
                    pass

            result.append({
                'symbol': sym,
                'name': stock.name,
                'overall_score': overall_score,
                'default_sort_value': default_sort_value,
                'weekly_composite': weekly_by_sym.get(sym),
                'rsi_score': score.rsi,
                'macd_score': score.macd,
                'trend_score': score.trend,
                'bb_score': score.bb,
                'stoch_score': score.stoch,
                'ma20_score': score.ma20,
                'technical_alignment_score': score.technical_alignment,
                'current_price': float(score.price) if score.price else None,
                'daily_percentage_change': round(score.daily_change, 2),
                'market_cap': float(stock.market_cap) if stock.market_cap else None,
                'pe': float(stock.pe) if stock.pe else None,
                'forward_pe': float(stock.forward_pe) if stock.forward_pe else None,
                'eps': float(stock.eps) if stock.eps else None,
                'forward_eps': float(stock.forward_eps) if stock.forward_eps else None,
                'volume_score': score.volume,
                'volume_signal': score.volume_signal,
                'growth_score': float(score.growth_score) if score.growth_score else None,
                'price_target_growth': float(score.price_target_growth) if score.price_target_growth else None,
                'next_earnings': (
                    (next_earnings_by_sym[sym] - today).days
                    if sym in next_earnings_by_sym
                    else score.next_earnings
                ),
                'next_earnings_call_time': next_earnings_call_time_by_sym.get(sym) or getattr(stock, 'earnings_call_time', None),
                'updated_at': score.updated_at.isoformat() if score.updated_at else None,
                'flagged': stock.flagged,
                'in_portfolio': sym in active_positions,
                'dte_thesis': dte_thesis,
                'dte_target': dte_target,
                'dte_min': dte_min,
                'dte_max': dte_max,
                'dte_confidence': dte_confidence,
                'dte_tradeable': dte_tradeable,
                'dte_filter_side': dte_filter_side,
                'realized_vol_60d': realized_vol_by_sym.get(sym),
                'ct_tag': (
                    'ct_call' if (overall_score >= 70 and score.trend is not None and score.trend <= 20)
                    else 'ct_put' if (overall_score <= 25 and score.trend is not None and score.trend >= 80)
                    else None
                ),
                'cascade_skip': _compute_cascade_skip(
                    overall_score, score.stoch, score.weight_info, sym,
                    _syms_in_earn_window,
                    _earn_supp_min, _earn_supp_max, _earn_supp_days,
                    _wwc_active, _wwc_min, _wwc_max, _wwc_wadj, _wwc_stoch,
                ),
                'intraday_swing': _swing_map.get(sym),
                'would_trade': _would_trade(sym, overall_score),
            })
        
        result.sort(key=lambda x: x['default_sort_value'], reverse=True)
        
        return jsonify({
            'stocks': result,
            'count': len(result),
            'date': target_date.isoformat(),
            'version': _version_payload(version, active_version),
            'active_version_id': active_version.id if active_version else None,
            **_score_version_response_fields(version_catalog),
        })
    
    except Exception as e:
        print(f"ERROR in /api/stocks/all: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/intraday-swing', methods=['GET'])
def get_intraday_swing(symbol):
    """Per-snapshot intraday timeline + swing attribution for one (symbol, date).

    Reads score_intraday_logs (the append-only per-update snapshot table — Score
    itself overwrites today's row in place). Returns the ordered snapshot timeline
    plus the dominant cause of the day's score swing (weekly / components / volume /
    regime / dampener / boost) and, when recognized, a `fakeout_family` tag (e.g.
    'wcf_boundary'). `?date=YYYY-MM-DD` overrides the default (most recent
    multi-snapshot date for the symbol).
    """
    try:
        from intraday_diagnostics import symbol_day_diagnostic
        version, active_version, req_version = _resolve_dashboard_score_version()
        diag = symbol_day_diagnostic(
            symbol.upper(),
            target_date=request.args.get('date'),
            version=version,
        )
        if not diag:
            return jsonify({'error': f'No intraday snapshots for {symbol.upper()}'}), 404
        return jsonify(diag)
    except Exception as e:
        print(f"ERROR in /api/stocks/{symbol}/intraday-swing: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/count', methods=['GET'])
def get_stocks_count():
    """Get total count of stocks in database and comprehensive statistics"""
    try:
        # Dashboard coverage is for the active tradable universe only. Delisted
        # rows are historical-only and should not inflate the pull-progress gap.
        active_stock_counts = (Stock
            .select(
            fn.COUNT(Stock.symbol).alias('total_stocks'),
            fn.COUNT(Stock.revenue).alias('stocks_with_revenue')
            )
            .where(Stock.delisted_date.is_null())
            .tuples()
            .get())
        
        # Get the latest trend data efficiently
        latest_trend = Trend.select().order_by(Trend.date.desc()).first()
        if not latest_trend:
            return jsonify({'error': 'No trend data available'}), 404
            
        # Get previous day's trend data for comparison
        previous_trend = Trend.select().where(Trend.date < latest_trend.date).order_by(Trend.date.desc()).first()
        
        version, active_version, req_version = _resolve_dashboard_score_version()
        version_catalog = _score_version_catalog(active_version)
        if req_version and not version:
            return jsonify({
                'error': f'Algorithm version not found: {req_version}',
                'active_version_id': active_version.id if active_version else None,
                **_score_version_response_fields(version_catalog),
            }), 404
        score_coverage_version = version
        score_coverage_date = Score.select(fn.MAX(Score.date)).where(Score.version == score_coverage_version).scalar()
        if not score_coverage_date and not req_version:
            prev_v = _get_prev_version(version)
            if prev_v:
                score_coverage_version = prev_v
                score_coverage_date = Score.select(fn.MAX(Score.date)).where(Score.version == score_coverage_version).scalar()

        stocks_with_scores = 0
        if score_coverage_date:
            stocks_with_scores = (Score
                .select(Score.symbol)
                .join(Stock, on=(Score.symbol == Stock.symbol))
                .where((Score.date == score_coverage_date)
                       & (Score.version == score_coverage_version)
                       & Score.overall.is_null(False)
                       & Stock.delisted_date.is_null())
                .count())
        
        # Calculate percentages from counts
        total_stocks = latest_trend.total_stocks
        stocks_up_pct = (latest_trend.stocks_up / total_stocks * 100) if total_stocks > 0 else 0
        
        # Calculate high/low ratio
        high_score_pct = (latest_trend.over_75_count / total_stocks * 100) if total_stocks > 0 else 0
        low_score_pct = (latest_trend.under_25_count / total_stocks * 100) if total_stocks > 0 else 0
        high_low_ratio = round(high_score_pct / low_score_pct, 2) if low_score_pct > 0 else 0
        
        # Calculate day-over-day changes
        def calculate_change(current, previous):
            if previous is None or previous == 0:
                return 0
            return current - previous
        
        # Calculate changes for each metric
        high_score_change = calculate_change(latest_trend.over_75_count, previous_trend.over_75_count if previous_trend else None)
        low_score_change = calculate_change(latest_trend.under_25_count, previous_trend.under_25_count if previous_trend else None)
        avg_score_change = calculate_change(
            float(latest_trend.avg_score) if latest_trend.avg_score is not None else 0,
            float(previous_trend.avg_score) if previous_trend and previous_trend.avg_score is not None else 0
        )
        total_stocks_change = calculate_change(latest_trend.total_stocks, previous_trend.total_stocks if previous_trend else None)
        
        comprehensive_stats = {
            'total_stocks': active_stock_counts[0],
            'stocks_with_revenue': active_stock_counts[1],
            'stocks_with_scores': stocks_with_scores,
            'score_coverage_total_stocks': active_stock_counts[0],
            'score_coverage_stocks_with_scores': stocks_with_scores,
            'score_coverage_date': score_coverage_date.isoformat() if score_coverage_date else None,
            'score_coverage_version': score_coverage_version.id if score_coverage_version else None,
            'version': _version_payload(score_coverage_version, active_version) if score_coverage_version else None,
            'active_version_id': active_version.id if active_version else None,
            **_score_version_response_fields(version_catalog),
            'trend_total_stocks': latest_trend.total_stocks,
            'high_score_stocks': latest_trend.over_75_count,
            'low_score_stocks': latest_trend.under_25_count,
            'avg_score': float(latest_trend.avg_score) if latest_trend.avg_score is not None else 0,
            'positive_change_stocks': latest_trend.stocks_up,
            'negative_change_stocks': latest_trend.stocks_down,
            'neutral_change_stocks': latest_trend.stocks_neutral,
            'percentage_up': round(stocks_up_pct, 2),
            'high_low_ratio': high_low_ratio,
            'index_funds_avg_change': 0.0,
            'major_indices': {},
            'date': latest_trend.date.isoformat(),
            'last_updated': latest_trend.updated_at.isoformat() if latest_trend.updated_at else None,
            # Day-over-day changes
            'high_score_change': high_score_change,
            'low_score_change': low_score_change,
            'avg_score_change': round(avg_score_change, 2),
            'total_stocks_change': total_stocks_change
        }
        
        # Optimize: Get market indices data in a single query
        market_stocks = [
            'SPY', 'QQQ', 'ARKQ', 'ARKX', 'SOXX', 'IWM', 'TQQQ', 'SMH', 'SOXL', 'TNA',
            'XLC', 'XLE', 'XLF', 'XLP', 'XLU', 'IGV',
            'GLD', 'SLV', 'IBIT', 'FBTC',
            'TLT', 'IEF', 'HYG',
            'EEM', 'FXI', 'EWZ', 'EWY', 'KWEB', 'ASHR',
            'SVIX', 'BOIL', 'LABD', 'IAU', 'DRAM', 'UFO', 'URA',
        ]
        lookback_date = latest_trend.date - timedelta(days=7)

        # Get the two most recent trading days per index (handles weekends/holidays)
        # Note: PriceHistory.symbol_id is the string column, not the foreign key object
        market_data = (PriceHistory
            .select(
                PriceHistory.symbol,
                PriceHistory.close,
                PriceHistory.date
            )
            .where(
                (PriceHistory.symbol_id.in_(market_stocks)) &
                (PriceHistory.date >= lookback_date) &
                (PriceHistory.date <= latest_trend.date)
            )
            .order_by(PriceHistory.symbol, PriceHistory.date.desc())
        )

        # Group by symbol; keep the two most recent dates per symbol
        index_changes = []
        market_prices = {}

        for ph in market_data:
            symbol = ph.symbol_id  # Direct access to the symbol string
            if symbol not in market_prices:
                market_prices[symbol] = {}
            if len(market_prices[symbol]) < 2:
                market_prices[symbol][ph.date] = float(ph.close)

        for stock_symbol in market_stocks:
            if stock_symbol in market_prices:
                dates = sorted(market_prices[stock_symbol].keys(), reverse=True)
                current_price = market_prices[stock_symbol].get(dates[0]) if len(dates) >= 1 else None
                prev_price = market_prices[stock_symbol].get(dates[1]) if len(dates) >= 2 else None
                
                if current_price and prev_price and prev_price > 0:
                    daily_change = ((current_price - prev_price) / prev_price) * 100
                    comprehensive_stats['major_indices'][stock_symbol] = round(daily_change, 2)
                    index_changes.append(daily_change)
                else:
                    comprehensive_stats['major_indices'][stock_symbol] = 0.0
                    index_changes.append(0.0)
            else:
                comprehensive_stats['major_indices'][stock_symbol] = 0.0
                index_changes.append(0.0)
        
        # Calculate average index fund change
        if index_changes:
            comprehensive_stats['index_funds_avg_change'] = round(sum(index_changes) / len(index_changes), 2)
        
        return jsonify(comprehensive_stats)
    
    except Exception as e:
        print(f"ERROR in /api/stocks/count: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/<symbol>/update', methods=['POST'])
def update_single_stock(symbol):
    """Update a single stock's data or pull a new stock if it doesn't exist"""
    try:
        # Validate symbol
        if not symbol or len(symbol) > 6:
            return jsonify({'error': 'Invalid symbol format'}), 400
        
        # Only allow alphanumeric characters
        if not symbol.isalnum():
            return jsonify({'error': 'Symbol can only contain letters and numbers'}), 400

        stock = Stock.get_or_none(symbol=symbol.upper())
        is_new_stock = False if stock else True
        trader.pull_stock(symbol.upper())
        
        # Fetch the updated stock data
        updated_stock = Stock.select().where(Stock.symbol == symbol.upper()).first()
        
        if not updated_stock:
            return jsonify({'error': 'Failed to update stock'}), 500
        
        version = get_api_score_version()
        latest_score = Score.select().where(Score.symbol == updated_stock.symbol, Score.version == version).order_by(Score.date.desc()).first()
        
        # Format the stock data similar to the dashboard function
        stock_data = {
            'symbol': updated_stock.symbol,
            'name': updated_stock.name,
            'current_price': float(updated_stock.current_price()) if updated_stock.current_price() else None,
            'daily_percentage_change': float(updated_stock.daily_percentage_change(latest_score.date)) if latest_score and updated_stock.daily_percentage_change(latest_score.date) else None,
            'pe': float(updated_stock.pe) if updated_stock.pe else None,
            'forward_pe': float(updated_stock.forward_pe) if updated_stock.forward_pe else None,
            'revenue': float(updated_stock.revenue) if updated_stock.revenue else None,
            'earnings': updated_stock.next_earnings.isoformat() if updated_stock.next_earnings else None,
            'market_cap': float(updated_stock.market_cap) if updated_stock.market_cap else None,
            'flagged': updated_stock.flagged,
            'updated_at': latest_score.updated_at.isoformat() if latest_score.updated_at else None,
            'latest_score': {
                'OVR': float(latest_score.overall) if latest_score.overall else None,
                'W': latest_score.output_hash().get('W'),
                'TREND': float(latest_score.trend) if latest_score.trend else None,
                'BB': float(latest_score.bb) if latest_score.bb else None,
                'RSI': float(latest_score.rsi) if latest_score.rsi else None,
                'MACD': float(latest_score.macd) if latest_score.macd else None,
                'X': float(latest_score.technical_alignment) if latest_score.technical_alignment else None
            } if latest_score else None
        }
        
        # Add growth interpretation and target percentage if available
        if updated_stock.pe and updated_stock.forward_pe and updated_stock.forward_pe > 0:
            growth_ratio = updated_stock.pe / updated_stock.forward_pe
            if growth_ratio < 0.8:
                stock_data['growth_interpretation'] = 'Strong'
            elif growth_ratio < 1.0:
                stock_data['growth_interpretation'] = 'Good'
            elif growth_ratio < 1.2:
                stock_data['growth_interpretation'] = 'Moderate'
            elif growth_ratio < 1.5:
                stock_data['growth_interpretation'] = 'Weak'
            else:
                stock_data['growth_interpretation'] = 'Stagnant'
        
        # Add earnings days if available
        if updated_stock.next_earnings:
            days_until_earnings = (updated_stock.next_earnings - date.today()).days
            if days_until_earnings == 0:
                stock_data['earnings_days'] = 'Today'
            elif days_until_earnings == 1:
                stock_data['earnings_days'] = 'Tomorrow'
            elif days_until_earnings < 0:
                stock_data['earnings_days'] = f'{abs(days_until_earnings)} days ago'
            else:
                stock_data['earnings_days'] = f'{days_until_earnings} days'
        
        action = 'pulled' if is_new_stock else 'updated'
        return jsonify({
            'success': True,
            'stock': stock_data,
            'message': f'Stock {symbol} {action} successfully',
            'is_new_stock': is_new_stock
        })
        
    except Exception as e:
        print(f"Error updating/pulling stock {symbol}: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/market/regime', methods=['GET'])
def get_market_regime():
    """Return the latest MarketRegime row for dashboard display."""
    try:
        row = MarketRegime.select().order_by(MarketRegime.date.desc()).first()
        if not row:
            return jsonify({'error': 'No regime data available'}), 404

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        mb_row = MarketBreadth.get_or_none(MarketBreadth.date == row.date)
        mbs = f(mb_row.breadth_score) if mb_row and mb_row.breadth_score is not None else None

        return jsonify({
            'date': row.date.isoformat(),
            'vix_close': f(row.vix_close),
            'vix_10d_change': f(row.vix_10d_change),
            'spy_close': f(row.spy_close),
            'spy_ema50': f(row.spy_ema50),
            'spy_ema200': f(row.spy_ema200),
            'internal_breadth_score': f(row.internal_breadth_score),
            'market_breadth_score': mbs,
            'vix_score': f(row.vix_score),
            'market_trend_score': f(row.market_trend_score),
            'regime_composite': f(row.regime_composite),
            'regime_multiplier': f(row.regime_multiplier, 4),
            'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        })
    except Exception as e:
        print(f"ERROR in /api/market/regime: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/market/breadth', methods=['GET'])
def get_market_breadth():
    """Return latest MarketBreadth row plus optional history."""
    try:
        MarketBreadth.ensure_schema()
        days = request.args.get('days', type=int, default=1)
        rows = list(
            MarketBreadth.select()
            .order_by(MarketBreadth.date.desc())
            .limit(max(1, days))
        )
        if not rows:
            return jsonify({'error': 'No breadth data available'}), 404

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        def serialize(row, allow_live_sector_fallback=False):
            return {
                'date': row.date.isoformat(),
                'advancing': row.advancing,
                'declining': row.declining,
                'unchanged': row.unchanged,
                'total_issues': row.total_issues,
                'advancing_volume': f(row.advancing_volume, 0),
                'declining_volume': f(row.declining_volume, 0),
                'trin': f(row.trin, 4),
                'new_highs_52w': row.new_highs_52w,
                'new_lows_52w': row.new_lows_52w,
                'ad_diff': row.ad_diff,
                'ad_line': f(row.ad_line),
                'ema19_ad_diff': f(row.ema19_ad_diff, 2),
                'ema39_ad_diff': f(row.ema39_ad_diff, 2),
                'mcclellan_oscillator': f(row.mcclellan_oscillator, 1),
                'mcclellan_summation': f(row.mcclellan_summation, 0),
                'ad_ratio': f(row.ad_ratio, 4),
                'ema10_ad_ratio': f(row.ema10_ad_ratio, 4),
                'zweig_thrust_active': bool(row.zweig_thrust_active),
                'zweig_thrust_date': row.zweig_thrust_date.isoformat() if row.zweig_thrust_date else None,
                'pct_above_ema50': f(row.pct_above_ema50),
                'pct_above_ema200': f(row.pct_above_ema200),
                'hindenburg_omen': bool(row.hindenburg_omen),
                'hindenburg_confirmed': bool(row.hindenburg_confirmed),
                'breadth_score': f(row.breadth_score),
                'sector_etf_breadth': _sector_etf_breadth_payload(row, allow_live_sector_fallback),
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            }

        if days == 1:
            return jsonify(serialize(rows[0], allow_live_sector_fallback=True))
        return jsonify({'history': [serialize(r) for r in reversed(rows)]})
    except Exception as e:
        print(f"ERROR in /api/market/breadth: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/market/trends', methods=['GET'])
def get_market_trends():
    """Return Market Wave history with SPY aligned for the Market Trends page."""
    try:
        def parse_ymd(name):
            value = request.args.get(name)
            if not value:
                return None
            return datetime.strptime(value, '%Y-%m-%d').date()

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        today = date.today()
        end_arg = parse_ymd('end_date')
        start_arg = parse_ymd('start_date')
        end_date = min(end_arg or today, today)
        year = request.args.get('year', type=int)
        days = request.args.get('days', type=int)

        if year is not None:
            start_date = date(year, 1, 1)
            if end_arg is None:
                end_date = min(date(year, 12, 31), today)
        elif start_arg is not None:
            start_date = start_arg
        elif days is not None:
            start_date = end_date - timedelta(days=max(1, days) - 1)
        else:
            start_date = end_date - timedelta(days=365)

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        all_rows = _cached_sector_etf_wave_rows(end_date)
        available_years = sorted({r['date'].year for r in all_rows})
        filtered = [r for r in all_rows if start_date <= r['date'] <= end_date]
        if not filtered:
            return jsonify({
                'history': [],
                'count': 0,
                'available_years': available_years,
                'params': SECTOR_ETF_MARKET_WAVE_PARAMS,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            })

        min_year = min(r['date'].year for r in filtered)
        spy_query_start = date(min_year, 1, 1) - timedelta(days=10)
        spy_rows = list(
            PriceHistory
            .select(PriceHistory.date, PriceHistory.close)
            .where(
                (PriceHistory.symbol == 'SPY')
                & (PriceHistory.date >= spy_query_start)
                & (PriceHistory.date <= end_date)
            )
            .order_by(PriceHistory.date.asc())
        )
        spy_series = [(r.date, float(r.close)) for r in spy_rows]

        spy_first_by_year = {}
        for spy_date, close in spy_series:
            if spy_date.year not in spy_first_by_year:
                spy_first_by_year[spy_date.year] = close

        history = []
        spy_idx = 0
        last_spy_close = None
        prev_spy_close = None
        for row in filtered:
            row_date = row['date']
            exact_spy_close = None
            exact_prev_close = None
            while spy_idx < len(spy_series) and spy_series[spy_idx][0] <= row_date:
                prev_spy_close = last_spy_close
                spy_date, spy_close = spy_series[spy_idx]
                last_spy_close = spy_close
                if spy_date == row_date:
                    exact_spy_close = spy_close
                    exact_prev_close = prev_spy_close
                spy_idx += 1

            spy_close = exact_spy_close if exact_spy_close is not None else last_spy_close
            ytd_base = spy_first_by_year.get(row_date.year)
            raw_wave, terms = _sector_etf_wave_raw(row)
            spy_1d_pct = (
                ((spy_close - exact_prev_close) / exact_prev_close) * 100.0
                if spy_close is not None and exact_prev_close not in (None, 0)
                else None
            )
            spy_ytd_pct = (
                ((spy_close - ytd_base) / ytd_base) * 100.0
                if spy_close is not None and ytd_base not in (None, 0)
                else None
            )

            history.append({
                'date': row_date.isoformat(),
                'year': row_date.year,
                'pct_above_ema50': f(row.get('pct_above_ema50')),
                'pct_above_ema200': f(row.get('pct_above_ema200')),
                'avg_rsi': f(row.get('avg_rsi')),
                'breadth_1d_change': f(row.get('breadth_1d_change')),
                'breadth_5d_change': f(row.get('breadth_5d_change')),
                'breadth_10d_change': f(row.get('breadth_10d_change')),
                'breadth_15d_change': f(row.get('breadth_15d_change')),
                'breadth_avg5': f(row.get('breadth_avg5')),
                'breadth_10d_position': f(row.get('breadth_10d_position')),
                'breadth_30d_position': f(row.get('breadth_30d_position')),
                'market_wave_score': f(row.get('market_wave_score')),
                'market_wave_signed': f(row.get('market_wave_signed')),
                'market_wave_state': row.get('market_wave_state'),
                'market_wave_raw': f(raw_wave, 4),
                'market_wave_centered': f(raw_wave - SECTOR_ETF_MARKET_WAVE_PARAMS['center'], 4),
                'market_wave_terms': {k: f(v, 4) for k, v in terms.items()},
                'crash_echo': f(row.get('crash_echo'), 4),
                'bull_wave': f(row.get('bull_wave'), 4),
                'effective_crash_echo': f(row.get('effective_crash_echo'), 4),
                'source': row.get('source'),
                'issues': row.get('issues'),
                'spy_close': f(spy_close),
                'spy_1d_pct': f(spy_1d_pct),
                'spy_ytd_pct': f(spy_ytd_pct),
            })

        return jsonify({
            'history': history,
            'latest': history[-1] if history else None,
            'count': len(history),
            'available_years': available_years,
            'params': SECTOR_ETF_MARKET_WAVE_PARAMS,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'source': 'sector_etf_live_history',
        })
    except Exception as e:
        print(f"ERROR in /api/market/trends: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/market/historic-peaks', methods=['GET'])
def get_historic_peaks():
    """Return cached historic peak scores (≥75 or ≤25) per stock.

    Each stock appears once — the single most extreme qualifying score in the window.
    Includes sub-scores at peak, current price, price change since peak, and any
    mixed-signal (opposite direction threshold crossed after the peak).
    """
    try:
        HistoricPeak.ensure_schema()
        today = date.today()
        version, active_version, req_version = _resolve_dashboard_score_version()
        version_catalog = _score_version_catalog(active_version)

        if not version:
            msg = f'Algorithm version not found: {req_version}' if req_version else 'No active algorithm version found'
            return jsonify({
                'error': msg,
                'active_version_id': active_version.id if active_version else None,
                **_score_version_response_fields(version_catalog),
            }), 404

        # Filter on the selected algorithm_version so a stale row left over from
        # a prior version (rebuild skipped after a version bump) cannot serve
        # the wrong score. Rows with NULL algorithm_version_id are pre-migration
        # legacy entries that should be discarded by the next rebuild — exclude
        # them here to fail closed.
        rows = list(
            HistoricPeak.select(HistoricPeak, Stock)
            .join(Stock, on=(HistoricPeak.symbol == Stock.symbol))
            .where(HistoricPeak.algorithm_version == version)
            .order_by(HistoricPeak.peak_score.desc())
        )

        score_coverage_date = (
            Score.select(fn.MAX(Score.date))
            .where(Score.version == version)
            .scalar()
        )

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        result = []
        for row in rows:
            peak_date = row.peak_date
            days_ago = (today - peak_date).days
            price_at_peak = f(row.price_at_peak)
            current_price = f(row.current_price)
            price_change_pct = None
            if price_at_peak and current_price and price_at_peak != 0:
                price_change_pct = round((current_price - price_at_peak) / price_at_peak * 100, 2)

            result.append({
                'symbol': row.symbol.symbol,
                'name': row.symbol.name,
                'flagged': row.symbol.flagged,
                'peak_date': peak_date.isoformat(),
                'days_ago': days_ago,
                'peak_score': row.peak_score,
                'peak_type': row.peak_type,
                'price_at_peak': price_at_peak,
                'current_price': current_price,
                'price_change_pct': price_change_pct,
                'peak_trend': row.peak_trend,
                'peak_bb': row.peak_bb,
                'peak_rsi': row.peak_rsi,
                'peak_macd': row.peak_macd,
                'peak_vol': row.peak_vol,
                'peak_weekly': row.peak_weekly,
                'peak_volume_signal': row.peak_volume_signal,
                'peak_dte_thesis': row.peak_dte_thesis,
                'peak_dte_min': row.peak_dte_min,
                'peak_dte_max': row.peak_dte_max,
                'peak_dte_target': row.peak_dte_target,
                'price_peak_pct': f(row.price_peak_pct, 2) if row.price_peak_pct is not None else None,
                'price_peak_days_after': row.price_peak_days_after,
                'fwd_1d':  f(row.fwd_1d,  2) if row.fwd_1d  is not None else None,
                'fwd_7d':  f(row.fwd_7d,  2) if row.fwd_7d  is not None else None,
                'fwd_15d': f(row.fwd_15d, 2) if row.fwd_15d is not None else None,
                'fwd_30d': f(row.fwd_30d, 2) if row.fwd_30d is not None else None,
                'fwd_60d': f(row.fwd_60d, 2) if row.fwd_60d is not None else None,
                'fwd_90d': f(row.fwd_90d, 2) if row.fwd_90d is not None else None,
                'fwd_peak_1d':       f(row.fwd_peak_1d,  2) if row.fwd_peak_1d  is not None else None,
                'fwd_peak_days_1d':  row.fwd_peak_days_1d,
                'fwd_peak_7d':       f(row.fwd_peak_7d,  2) if row.fwd_peak_7d  is not None else None,
                'fwd_peak_days_7d':  row.fwd_peak_days_7d,
                'fwd_peak_15d':      f(row.fwd_peak_15d, 2) if row.fwd_peak_15d is not None else None,
                'fwd_peak_days_15d': row.fwd_peak_days_15d,
                'fwd_peak_30d':      f(row.fwd_peak_30d, 2) if row.fwd_peak_30d is not None else None,
                'fwd_peak_days_30d': row.fwd_peak_days_30d,
                'fwd_peak_60d':      f(row.fwd_peak_60d, 2) if row.fwd_peak_60d is not None else None,
                'fwd_peak_days_60d': row.fwd_peak_days_60d,
                'fwd_peak_90d':        f(row.fwd_peak_90d, 2) if row.fwd_peak_90d is not None else None,
                'fwd_peak_days_90d':   row.fwd_peak_days_90d,
                'fwd_peak_delta_7d':   f(row.fwd_peak_delta_7d,  2) if row.fwd_peak_delta_7d  is not None else None,
                'fwd_peak_delta_15d':  f(row.fwd_peak_delta_15d, 2) if row.fwd_peak_delta_15d is not None else None,
                'fwd_peak_delta_30d':  f(row.fwd_peak_delta_30d, 2) if row.fwd_peak_delta_30d is not None else None,
                'fwd_peak_delta_60d':  f(row.fwd_peak_delta_60d, 2) if row.fwd_peak_delta_60d is not None else None,
                'fwd_peak_delta_90d':  f(row.fwd_peak_delta_90d, 2) if row.fwd_peak_delta_90d is not None else None,
                'vol_pct': f(row.vol_pct, 4) if row.vol_pct is not None else None,
                'win_1d':  row.win_1d,
                'win_7d':  row.win_7d,
                'win_15d': row.win_15d,
                'win_30d': row.win_30d,
                'win_60d': row.win_60d,
                'win_90d': row.win_90d,
                'reach_1d':  row.reach_1d,
                'reach_7d':  row.reach_7d,
                'reach_15d': row.reach_15d,
                'reach_30d': row.reach_30d,
                'reach_60d': row.reach_60d,
                'reach_90d': row.reach_90d,
                'mixed_signal_date': row.mixed_signal_date.isoformat() if row.mixed_signal_date else None,
                'mixed_signal_score': row.mixed_signal_score,
                'mixed_signal_days_after': row.mixed_signal_days_after,
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            })

        return jsonify({
            'peaks': result,
            'count': len(result),
            'version': _version_payload(version, active_version),
            'active_version_id': active_version.id if active_version else None,
            **_score_version_response_fields(version_catalog),
            'score_coverage_date': score_coverage_date.isoformat() if score_coverage_date else None,
        })

    except Exception as e:
        print(f"ERROR in /api/market/historic-peaks: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/earnings/weekly', methods=['GET'])
def get_weekly_earnings():
    """Get stocks with earnings for Mon-Fri, using the EarningsDate table"""
    try:
        today = date.today()
        weekday = today.weekday()  # 0=Mon, 4=Fri

        this_monday = today - timedelta(days=weekday)
        next_monday = this_monday + timedelta(days=7)

        calendar_days = []
        for i in range(5):
            if i < weekday:
                calendar_days.append({'date': next_monday + timedelta(days=i), 'is_next_week': True})
            else:
                calendar_days.append({'date': this_monday + timedelta(days=i), 'is_next_week': False})

        all_dates = [d['date'] for d in calendar_days]
        date_to_idx = {d['date']: i for i, d in enumerate(calendar_days)}

        earnings_by_day = {i: {
            'before': [], 'during': [], 'after': [],
            'date': calendar_days[i]['date'].isoformat(),
            'is_next_week': calendar_days[i]['is_next_week']
        } for i in range(5)}

        def get_timing_key(time_str):
            if not time_str:
                return 'during'
            if time_str in ['BMO', 'bmo']:
                return 'before'
            if time_str in ['AMC', 'amc']:
                return 'after'
            try:
                parts = time_str.split(':')
                hour, minute = int(parts[0]), int(parts[1])
                time_minutes = hour * 60 + minute
                if time_minutes < 9 * 60 + 30:
                    return 'before'
                elif time_minutes >= 16 * 60:
                    return 'after'
                return 'during'
            except:
                return 'during'

        rows = (EarningsDate
            .select(EarningsDate.date, EarningsDate.call_time,
                    Stock.symbol, Stock.name, Stock.flagged)
            .join(Stock, on=(EarningsDate.symbol == Stock.symbol))
            .where(EarningsDate.date.in_(all_dates)))

        # Collect all symbols first so we can batch-fetch latest scores
        earnings_rows = list(rows)
        all_symbols = list({row.symbol.symbol for row in earnings_rows})

        version = get_api_score_version()
        # Get the most recent score date for this version
        latest_score_date = (Score.select(fn.MAX(Score.date))
            .where(Score.version == version)
            .scalar())

        score_map = {}
        if latest_score_date and all_symbols:
            score_rows = (Score.select(Score.symbol, Score.overall)
                .where(
                    Score.symbol.in_(all_symbols),
                    Score.version == version,
                    Score.date == latest_score_date,
                    Score.overall.is_null(False)
                ))
            for sr in score_rows:
                score_map[sr.symbol_id] = round(sr.overall)

        for row in earnings_rows:
            idx = date_to_idx.get(row.date)
            if idx is None:
                continue
            timing_key = get_timing_key(row.call_time)
            sym = row.symbol.symbol
            earnings_by_day[idx][timing_key].append({
                'symbol': sym,
                'name': row.symbol.name,
                'flagged': row.symbol.flagged,
                'score': score_map.get(sym)
            })

        return jsonify({
            'today_index': weekday if weekday < 5 else None,
            'earnings': earnings_by_day
        })
    except Exception as e:
        print(f"ERROR in /api/earnings/weekly: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# Error handlers
@app.route('/api/assessment', methods=['GET'])
def get_assessment():
    """Return aggregated assessment data for the active algorithm version.

    Query params:
      version: id (e.g. "14"), "v14", or commit hash/prefix. Default: active version.
      dte:     '30' (default) or '15'.
      metric:  'wr' (default — directional Win Rate, generic K=2.0σ barriers, DTE-agnostic)
               or 'tp' (Take Profit % — option-aligned barriers per DTE: Phase H5 for 30, Phase 15B C1 for 15).
               Phase 17 (2026-04-29).
               or 'tp26' (Take Profit % at the 2026-08-10 TP/SL retune canon — 30 DTE only,
               additive dual anchor; 'tp' stays frozen for W1-W6 comparability). Populated
               for the active version only. experiments/assess_reanchor_2026_08/.

    Routing:
      (dte=30, metric=wr) → served from ScoreAssessmentMeta cache (existing path)
      All other (dte, metric) combos → aggregated fresh from ScoreAssessmentRun rows
        filtered by (dte_strategy, metric).

    Response shape:
      {
        version: { id, git_commit, git_message, run_count, total_peaks, updated_at, correlations: {1d..90d} },
        buckets: [ { bucket, side, sample_count, avg_score,
                     win_rate: {1d..90d}, avg_return: {..}, avg_peak: {..},
                     avg_mae: {..}, avg_mfe: {..}, capture_ratio: {..},
                     avg_mae_winner_30d, avg_mfe_30d, shakeout_depth, shakeout_recovery } ],
        band_ics: [ { band, sample_count, ic: {1d..90d} } ]
      }
    """
    try:
        from database.models.core import (
            AlgorithmVersion, ScoreAssessmentMeta, ScoreAssessmentRun, ScoreAssessmentBandIC,
            ScoreAssessmentResult,
        )
        active_version = AlgorithmVersion.get_active_scores_version()
        if not active_version:
            return jsonify({'error': 'No active algorithm version found'}), 404

        # DTE strategy (Phase 16, 2026-04-28)
        dte_strategy = (request.args.get('dte') or '30').strip()
        if dte_strategy not in ('30', '15'):
            dte_strategy = '30'
        # Metric variant (Phase 17, 2026-04-29) — 'wr' = directional WR (DTE-agnostic),
        # 'tp' = option Take Profit % (DTE-specific option-aligned barriers, frozen at the
        # Phase H5/15B anchors for W1-W6 ship-gate comparability). 'tp26' (2026-08-10,
        # experiments/assess_reanchor_2026_08/) = a SECOND option-TP% anchor at the live
        # 2026-08-10 TP/SL retune canon, 30 DTE only, additive — 'tp' is untouched.
        metric = (request.args.get('metric') or 'wr').strip()
        if metric not in ('wr', 'tp', 'tp26'):
            metric = 'wr'

        # Resolve version selector: accepts production label ("v48"), db id
        # ("db:50" or bare "50"), or commit hash/prefix.
        version = active_version
        req_version = request.args.get('version')
        if req_version:
            resolved = _resolve_production_version(req_version)
            if resolved is None:
                return jsonify({'error': f'Algorithm version not found: {req_version}'}), 404
            version = resolved

        # Build list of versions that actually have assessment data, for the selector.
        version_ids_with_data = {
            m.version_id for m in ScoreAssessmentMeta.select(ScoreAssessmentMeta.version)
        }
        versions_with_data = list(
            AlgorithmVersion
            .select()
            .where(AlgorithmVersion.id.in_(list(version_ids_with_data)) if version_ids_with_data else False)
            .order_by(AlgorithmVersion.id.desc())
        ) if version_ids_with_data else []
        available_versions = [
            _version_payload(v, active_version)
            for v in versions_with_data
            if v.git_message and v.git_message.strip()
            and not AlgorithmVersion.is_legacy_staging_commit(v.git_commit)
        ]

        # Routing rule (Phase 17): WR is DTE-canonical (always stored at dte_strategy='30')
        # so the (15, wr) request is served from the (30, wr) data — they would be
        # byte-identical anyway since WR uses generic barriers regardless of DTE.
        # The (30, wr) cache path uses ScoreAssessmentMeta; everything else aggregates
        # fresh from ScoreAssessmentRun rows.
        _query_dte = '30' if metric == 'wr' else dte_strategy
        if _query_dte == '30' and metric == 'wr':
            meta_rows = list(
                ScoreAssessmentMeta.select()
                .where(ScoreAssessmentMeta.version == version)
            )
            if not meta_rows:
                return jsonify({
                    'error': f'No assessment data for version {version.production_label}',
                    'available_versions': available_versions,
                }), 404
        else:
            # Aggregate fresh from runs+results filtered by (dte_strategy, metric).
            from types import SimpleNamespace
            from datetime import datetime as _dt
            dte_runs = list(
                ScoreAssessmentRun.select()
                .where(ScoreAssessmentRun.version == version,
                       ScoreAssessmentRun.dte_strategy == _query_dte,
                       ScoreAssessmentRun.metric == metric)
            )
            if not dte_runs:
                _flag = f' --dte {_query_dte}' if _query_dte != '30' else ''
                _mflag = f' --metric {metric}' if metric != 'wr' else ''
                return jsonify({
                    'error': f'No assessment data for version {version.production_label} (dte={_query_dte}, metric={metric}). '
                             f'Run: trader assess --force{_flag}{_mflag} --version {version.git_commit}',
                    'available_versions': available_versions,
                }), 404
            # Pull all results for these runs and aggregate by bucket (weighted by sample_count).
            run_id_to_run = {r.id: r for r in dte_runs}
            all_results = list(
                ScoreAssessmentResult.select()
                .where(ScoreAssessmentResult.run.in_(list(run_id_to_run.keys())))
            )
            # Aggregate fields with sample_count-weighted means across the 1y/2y/3y/5y/10y runs.
            # We pick the LARGEST window's result per (bucket) since per-window data is independent.
            # Largest = max lookback_days among rows for that bucket.
            by_bucket = {}
            for r in all_results:
                run = run_id_to_run.get(r.run_id)
                if not run:
                    continue
                key = r.bucket
                if key not in by_bucket or run.lookback_days > by_bucket[key][1].lookback_days:
                    by_bucket[key] = (r, run)
            # Construct meta-like rows
            meta_rows = []
            run_counts_by_bucket = {}
            total_peaks_by_bucket = {}
            for bucket, (res, run) in by_bucket.items():
                # Count runs that include this bucket and sum peaks
                run_counts_by_bucket[bucket] = sum(1 for rr in all_results if rr.bucket == bucket)
                total_peaks_by_bucket[bucket] = sum((rr.sample_count or 0) for rr in all_results if rr.bucket == bucket)
                ns = SimpleNamespace()
                # Copy all attributes from the result row
                for col in res._meta.fields.keys():
                    setattr(ns, col, getattr(res, col, None))
                # Add fields meta_rows would have
                ns.run_count = run_counts_by_bucket[bucket]
                ns.total_peaks = res.sample_count or 0
                ns.updated_at = run.run_at
                # Pull correlations from run
                for p_label in ['1d', '3d', '5d', '7d', '15d', '30d', '60d', '90d']:
                    setattr(ns, f'correlation_{p_label}', getattr(run, f'correlation_{p_label}', None))
                meta_rows.append(ns)
            # Sort to match the BUCKET_ORDER iteration in the consumer code
            meta_rows.sort(key=lambda r: r.bucket)

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        def fg(obj, attr, dp=2):
            """Safe getattr — returns None if column doesn't exist on model."""
            v = getattr(obj, attr, None)
            return round(float(v), dp) if v is not None else None

        # Random-walk floors per side — gambler's ruin P(hit target) = M/(K+M).
        # CALL: K=2σ/M=5σ → 5/7 ≈ 71.43%.  PUT: K=1σ/M=2σ → 2/3 ≈ 66.67%.
        _CALL_FLOOR = 5 / 7 * 100   # 71.4286…
        _PUT_FLOOR  = 2 / 3 * 100   # 66.6667…

        def _rtr(wr, floor):
            """Normalise win_rate relative to the side-appropriate random-walk floor."""
            if wr is None:
                return None
            return round((wr - floor) / (100.0 - floor) * 100.0, 1)

        PERIODS = ['1d', '3d', '5d', '7d', '15d', '30d', '60d', '90d']
        META_PERIODS = PERIODS
        SELL_BUCKETS = {'<30', '<25', '<20', '<15', '<10', '<5'}

        # Version info from any meta row (they share run-level fields)
        m0 = meta_rows[0]
        correlations = {p: fg(m0, f'correlation_{p}', 4) for p in PERIODS}
        version_info = {
            'id': version.id,
            'label': version.production_label,
            'git_commit': version.git_commit,
            'git_message': version.git_message if version.git_message else None,
            'created_at': version.created_at.isoformat() if version.created_at else None,
            'run_count': m0.run_count,
            'total_peaks': m0.total_peaks,
            'updated_at': m0.updated_at.isoformat() if m0.updated_at else None,
            'correlations': correlations,
        }

        # Order buckets: calls first (descending threshold), then puts (ascending)
        BUCKET_ORDER = ['95+', '90+', '85+', '80+', '75+', '70+', '<30', '<25', '<20', '<15', '<10', '<5']
        meta_by_bucket = {r.bucket: r for r in meta_rows}

        buckets = []
        for bucket_key in BUCKET_ORDER:
            r = meta_by_bucket.get(bucket_key)
            side = 'put' if bucket_key in SELL_BUCKETS else 'call'
            if not r or (r.sample_count or 0) == 0:
                # Emit a placeholder so every bucket is represented in the UI.
                buckets.append({
                    'bucket': bucket_key,
                    'side': side,
                    'sample_count': 0,
                    'avg_score': None,
                    'win_rate': {p: None for p in META_PERIODS},
                    'win_rate_unscaled': {p: None for p in META_PERIODS},
                    'rtr_win_rate': {p: None for p in META_PERIODS},
                    'avg_return': {p: None for p in META_PERIODS},
                    'avg_peak': {p: None for p in META_PERIODS},
                    'avg_mae': {p: None for p in META_PERIODS},
                    'avg_mfe': {p: None for p in META_PERIODS},
                    'mfe_sigma_p25': {p: None for p in META_PERIODS},
                    'median_mfe_sigma': {p: None for p in META_PERIODS},
                    'mfe_sigma_p75': {p: None for p in META_PERIODS},
                    'capture_ratio': {p: None for p in META_PERIODS},
                    'avg_mae_winner_30d': None,
                    'avg_mae_loser_30d': None,
                    'avg_mae_winner': {p: None for p in PERIODS},
                    'avg_mae_loser': {p: None for p in PERIODS},
                    'avg_mae_winner_sigma': {p: None for p in PERIODS},
                    'avg_mae_loser_sigma': {p: None for p in PERIODS},
                    'shakeout_depth': None,
                    'shakeout_recovery': None,
                })
                continue
            floor = _PUT_FLOOR if side == 'put' else _CALL_FLOOR
            win_rates = {p: fg(r, f'win_rate_{p}', 1) for p in META_PERIODS}
            win_rates_u = {p: fg(r, f'win_rate_unscaled_{p}', 1) for p in META_PERIODS}
            buckets.append({
                'bucket': bucket_key,
                'side': side,
                'sample_count': r.sample_count or 0,
                'avg_score': f(r.avg_score),
                'win_rate': win_rates,
                'win_rate_unscaled': win_rates_u,
                'rtr_win_rate': {p: _rtr(win_rates[p], floor) for p in META_PERIODS},
                'avg_return': {p: fg(r, f'avg_return_{p}') for p in META_PERIODS},
                'avg_peak': {p: fg(r, f'avg_peak_{p}') for p in META_PERIODS},
                'avg_mae': {p: fg(r, f'avg_mae_{p}') for p in META_PERIODS},
                'avg_mfe': {p: fg(r, f'avg_mfe_{p}') for p in META_PERIODS},
                'mfe_sigma_p25':    {p: fg(r, f'mfe_sigma_p25_{p}', 3) for p in META_PERIODS},
                'median_mfe_sigma': {p: fg(r, f'median_mfe_sigma_{p}', 3) for p in META_PERIODS},
                'mfe_sigma_p75':    {p: fg(r, f'mfe_sigma_p75_{p}', 3) for p in META_PERIODS},
                'capture_ratio': {p: fg(r, f'capture_ratio_{p}', 3) for p in META_PERIODS},
                'avg_mae_winner_30d': fg(r, 'avg_mae_winner_30d'),
                'avg_mae_loser_30d': fg(r, 'avg_mae_loser_30d'),
                'avg_mae_winner': {p: fg(r, f'avg_mae_winner_{p}') for p in PERIODS},
                'avg_mae_loser': {p: fg(r, f'avg_mae_loser_{p}') for p in PERIODS},
                'avg_mae_winner_sigma': {p: fg(r, f'avg_mae_winner_sigma_{p}', 3) for p in PERIODS},
                'avg_mae_loser_sigma': {p: fg(r, f'avg_mae_loser_sigma_{p}', 3) for p in PERIODS},
                'shakeout_depth': fg(r, 'shakeout_depth'),
                'shakeout_recovery': getattr(r, 'shakeout_recovery', None),
            })

        # Aggregate band ICs across all runs for this version + (dte, metric).
        # `_query_dte` (computed earlier) maps WR requests to the canonical dte=30
        # row store, so 15-DTE WR requests pull from the same canonical rows.
        runs = list(ScoreAssessmentRun.select().where(
            ScoreAssessmentRun.version == version,
            ScoreAssessmentRun.dte_strategy == _query_dte,
            ScoreAssessmentRun.metric == metric,
        ))
        run_ids = [r.id for r in runs]
        band_ics = []
        if run_ids:
            all_bic = list(
                ScoreAssessmentBandIC.select()
                .where(ScoreAssessmentBandIC.run.in_(run_ids))
            )
            IC_PERIODS = PERIODS
            by_band = {}
            for row in all_bic:
                if row.band not in by_band:
                    by_band[row.band] = []
                by_band[row.band].append(row)

            # Sort bands: high first (75-79, 80-84...), then low (<25 area)
            def band_sort_key(band_label):
                parts = band_label.split('-')
                try:
                    return int(parts[0])
                except Exception:
                    return 0

            for band_label in sorted(by_band.keys(), key=band_sort_key, reverse=True):
                rows_b = by_band[band_label]
                total_n = sum(r.sample_count for r in rows_b)
                ic_vals = {}
                for p in IC_PERIODS:
                    vals = [(getattr(r, f'ic_{p}'), r.sample_count)
                            for r in rows_b if getattr(r, f'ic_{p}', None) is not None and r.sample_count > 0]
                    if vals:
                        wavg = sum(v * w for v, w in vals) / sum(w for _, w in vals)
                        ic_vals[p] = round(wavg, 4)
                    else:
                        ic_vals[p] = None
                band_ics.append({
                    'band': band_label,
                    'sample_count': total_n,
                    'ic': ic_vals,
                })

        # Cross-bucket RTR Pearson: Pearson(threshold, RTR_win%) across the 7 HIGH
        # buckets and 3 LOW buckets separately.  Computed fresh from stored win rates —
        # no reassessment needed.
        import numpy as _np
        BUY_THRESHOLDS  = [95, 90, 85, 80, 75, 70]
        SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]

        def _pearson(xy):
            if len(xy) < 3:
                return None
            xs, ys = zip(*xy)
            c = _np.corrcoef(xs, ys)[0, 1]
            return round(float(c), 3) if not _np.isnan(c) else None

        rtr_correlations = {}
        for p in META_PERIODS:
            high_xy, low_xy = [], []
            for t in BUY_THRESHOLDS:
                r = meta_by_bucket.get(f'{t}+')
                if r:
                    rv = _rtr(fg(r, f'win_rate_{p}', 1), _CALL_FLOOR)
                    if rv is not None:
                        high_xy.append((t, rv))
            for t in SELL_THRESHOLDS:
                r = meta_by_bucket.get(f'<{t}')
                if r:
                    rv = _rtr(fg(r, f'win_rate_{p}', 1), _PUT_FLOOR)
                    if rv is not None:
                        low_xy.append((50 - t, rv))
            rtr_correlations[p] = {
                'high': _pearson(high_xy),
                'low':  _pearson(low_xy),
            }
        version_info['rtr_correlations'] = rtr_correlations

        # ── Window breakdown: group runs by lookback_days ─────────────────────
        _WINDOW_LABELS = {365: '1y', 730: '2y', 1095: '3y', 1825: '5y',
                          3650: '10y', 9125: '25y'}

        runs_by_lb = {}
        for run in runs:
            lb = run.lookback_days
            runs_by_lb.setdefault(lb, []).append(run)

        all_result_rows = []
        if run_ids:
            all_result_rows = list(
                ScoreAssessmentResult.select()
                .where(ScoreAssessmentResult.run.in_(run_ids))
            )
        results_by_run = {}
        for row in all_result_rows:
            results_by_run.setdefault(row.run_id, []).append(row)

        def _wavg(rows, field, dp=1):
            pairs = [(getattr(r, field, None), r.sample_count)
                     for r in rows
                     if getattr(r, field, None) is not None and (r.sample_count or 0) > 0]
            if not pairs:
                return None
            return round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), dp)

        windows = []
        for lb in sorted(runs_by_lb.keys()):
            lb_runs  = runs_by_lb[lb]
            lb_rows  = [r for run in lb_runs for r in results_by_run.get(run.id, [])]
            label    = _WINDOW_LABELS.get(lb, f'{lb}d')
            total_pk = sum(run.total_peaks for run in lb_runs)

            win_bkts = []
            for bkt_key in BUCKET_ORDER:
                bkt_rows = [r for r in lb_rows
                            if r.bucket == bkt_key and (r.sample_count or 0) > 0]
                side = 'put' if bkt_key in SELL_BUCKETS else 'call'
                if not bkt_rows:
                    # Placeholder so every bucket is present in the windows tab.
                    win_bkts.append({
                        'bucket':            bkt_key,
                        'side':              side,
                        'sample_count':      0,
                        'win_rate':          {p: None for p in META_PERIODS},
                        'win_rate_unscaled': {p: None for p in META_PERIODS},
                        'rtr_win_rate':      {p: None for p in META_PERIODS},
                        'avg_return':        {p: None for p in META_PERIODS},
                        'avg_peak':          {p: None for p in META_PERIODS},
                        'avg_mae':           {p: None for p in META_PERIODS},
                        'avg_mfe':           {p: None for p in META_PERIODS},
                        'mfe_sigma_p25':     {p: None for p in META_PERIODS},
                        'median_mfe_sigma':  {p: None for p in META_PERIODS},
                        'mfe_sigma_p75':     {p: None for p in META_PERIODS},
                        'capture_ratio':     {p: None for p in META_PERIODS},
                        'avg_mae_winner_30d':  None,
                        'avg_mae_loser_30d':   None,
                        'avg_mae_winner':      {p: None for p in PERIODS},
                        'avg_mae_loser':       {p: None for p in PERIODS},
                        'avg_mae_winner_sigma':{p: None for p in PERIODS},
                        'avg_mae_loser_sigma': {p: None for p in PERIODS},
                        'shakeout_depth':      None,
                        'shakeout_recovery':   None,
                    })
                    continue
                n    = sum(r.sample_count for r in bkt_rows)
                floor_w = _PUT_FLOOR if side == 'put' else _CALL_FLOOR
                wr   = {p: _wavg(bkt_rows, f'win_rate_{p}') for p in META_PERIODS}
                wr_u = {p: _wavg(bkt_rows, f'win_rate_unscaled_{p}') for p in META_PERIODS}
                # Pick the row with the largest sample_count as the canonical
                # source for non-period fields (shakeout_depth, shakeout_recovery)
                # — these are summary stats that don't aggregate cleanly.
                anchor = max(bkt_rows, key=lambda r: r.sample_count or 0)
                win_bkts.append({
                    'bucket':            bkt_key,
                    'side':              side,
                    'sample_count':      n,
                    'win_rate':          wr,
                    'win_rate_unscaled': wr_u,
                    'rtr_win_rate':      {p: _rtr(wr[p], floor_w) for p in META_PERIODS},
                    'avg_return':        {p: _wavg(bkt_rows, f'avg_return_{p}') for p in META_PERIODS},
                    'avg_peak':          {p: _wavg(bkt_rows, f'avg_peak_{p}') for p in META_PERIODS},
                    'avg_mae':           {p: _wavg(bkt_rows, f'avg_mae_{p}') for p in META_PERIODS},
                    'avg_mfe':           {p: _wavg(bkt_rows, f'avg_mfe_{p}') for p in META_PERIODS},
                    'mfe_sigma_p25':     {p: _wavg(bkt_rows, f'mfe_sigma_p25_{p}', 3) for p in META_PERIODS},
                    'median_mfe_sigma':  {p: _wavg(bkt_rows, f'median_mfe_sigma_{p}', 3) for p in META_PERIODS},
                    'mfe_sigma_p75':     {p: _wavg(bkt_rows, f'mfe_sigma_p75_{p}', 3) for p in META_PERIODS},
                    'capture_ratio':     {p: _wavg(bkt_rows, f'capture_ratio_{p}', 3) for p in META_PERIODS},
                    'avg_mae_winner_30d':   _wavg(bkt_rows, 'avg_mae_winner_30d'),
                    'avg_mae_loser_30d':    _wavg(bkt_rows, 'avg_mae_loser_30d'),
                    'avg_mae_winner':       {p: _wavg(bkt_rows, f'avg_mae_winner_{p}') for p in PERIODS},
                    'avg_mae_loser':        {p: _wavg(bkt_rows, f'avg_mae_loser_{p}') for p in PERIODS},
                    'avg_mae_winner_sigma': {p: _wavg(bkt_rows, f'avg_mae_winner_sigma_{p}', 3) for p in PERIODS},
                    'avg_mae_loser_sigma':  {p: _wavg(bkt_rows, f'avg_mae_loser_sigma_{p}', 3) for p in PERIODS},
                    'shakeout_depth':       fg(anchor, 'shakeout_depth'),
                    'shakeout_recovery':    getattr(anchor, 'shakeout_recovery', None),
                })

            windows.append({
                'label':        label,
                'lookback_days': lb,
                'total_peaks':  total_pk,
                'run_count':    len(lb_runs),
                'buckets':      win_bkts,
            })

        # ── Previous version comparison ───────────────────────────────────────
        prev_version_info = None
        prev_buckets = []
        try:
            prev_v = _get_prev_version(version)
            if prev_v:
                prev_meta_rows = list(
                    ScoreAssessmentMeta.select()
                    .where(ScoreAssessmentMeta.version == prev_v)
                )
                if prev_meta_rows:
                    prev_m0 = prev_meta_rows[0]
                    prev_version_info = {
                        'id': prev_v.id,
                        'git_commit': prev_v.git_commit,
                        'git_message': prev_v.git_message if prev_v.git_message else None,
                        'total_peaks': prev_m0.total_peaks,
                        'updated_at': prev_m0.updated_at.isoformat() if prev_m0.updated_at else None,
                    }
                    prev_meta_by_bucket = {r.bucket: r for r in prev_meta_rows}
                    for bucket_key in BUCKET_ORDER:
                        r = prev_meta_by_bucket.get(bucket_key)
                        if not r or (r.sample_count or 0) == 0:
                            continue
                        side = 'put' if bucket_key in SELL_BUCKETS else 'call'
                        prev_buckets.append({
                            'bucket': bucket_key,
                            'side': side,
                            'sample_count': r.sample_count or 0,
                            'win_rate': {p: fg(r, f'win_rate_{p}', 1) for p in META_PERIODS},
                            'win_rate_unscaled': {p: fg(r, f'win_rate_unscaled_{p}', 1) for p in META_PERIODS},
                            'avg_return': {p: fg(r, f'avg_return_{p}') for p in META_PERIODS},
                        })
        except Exception:
            pass  # Comparison is best-effort; don't fail the whole endpoint

        return jsonify({
            'version': version_info,
            'buckets': buckets,
            'band_ics': band_ics,
            'windows': windows,
            'prev_version': prev_version_info,
            'prev_buckets': prev_buckets,
            'available_versions': available_versions,
            'active_version_id': active_version.id,
        })
    except Exception as e:
        print(f"ERROR in /api/assessment: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/trader/simulate', methods=['GET'])
def simulate_trader():
    """
    Simulate the optimal cascade allocation strategy from a start date.

    Query params:
      start      - start date YYYY-MM-DD (default 2026-04-01)
      capital    - starting capital USD (default 25000)
      min_score  - call-side score threshold (default 70)

    Returns:
      params, summary, trades (closed), open_positions, equity_curve
    """
    from collections import defaultdict
    import numpy as np

    try:
        # --- Parameters ---
        start_str = request.args.get('start', '2026-04-01')
        capital   = float(request.args.get('capital', 25_000))
        if capital < 500:   # 25 → 25000, 100 → 100000
            capital *= 1_000
        min_score = float(request.args.get('min_score', 70))
        # Strategy defaults from strategy_config.STRATEGY_30DTE; query params
        # override per-request for sweeps. CLI/dashboard tooltips fetch the
        # same values via /api/strategy/config so display stays in sync.
        import strategy_config as _sc
        _cfg = _sc.STRATEGY_30DTE
        _opt = _cfg.option
        tp_pct    = float(request.args.get('tp',   _opt.TP_BASE * 100))    # gross TP % on premium
        sl_pct    = float(request.args.get('sl',   abs(_opt.SL_BASE) * 100))   # gross SL % (positive)
        hard_day  = int(  request.args.get('hard', _cfg.HOLD_DAYS))
        max_pos   = int(  request.args.get('open', request.args.get('max_pos', _cfg.MAX_POSITIONS)))
        today     = date.today()

        try:
            start_date = date.fromisoformat(start_str)
        except ValueError:
            return jsonify({'error': f'Invalid start date: {start_str}'}), 400

        # ---------------------------------------------------------------
        # Strategy constants — sourced from strategy_config.STRATEGY_30DTE.
        # σ-multiplier = PREMIUM_MULT / DELTA. For per-request tp/sl
        # override, σ scales linearly with the override.
        # ---------------------------------------------------------------
        _SIGMA_MULT        = _cfg.PREMIUM_MULT / _opt.DELTA
        TP_SIGMA           = tp_pct / 100 * _SIGMA_MULT
        SL_SIGMA           = sl_pct / 100 * _SIGMA_MULT
        # Net P&L: gross − entry slippage − exit slippage (TP=0 limit, SL=-1.3%, hard=-0.5%)
        NET_TP             = +(tp_pct / 100 + _opt.SLIP_ENTRY + _opt.SLIP_TP)
        NET_SL             = -(sl_pct / 100 - _opt.SLIP_ENTRY - _opt.SLIP_SL)
        NET_HARD           = abs(_cfg.HARD_SELL_LOSS) * -1 + _opt.SLIP_ENTRY + _opt.SLIP_HARD
        MAX_POSITIONS      = max_pos
        HOLD_CALENDAR_DAYS = hard_day
        VOL_BARS           = _cfg.VOL_LOOKBACK
        MIN_VOL_BARS       = 20

        # Cascade — display-key-mapped from strategy_config semantic keys.
        TIER_ALLOC = {
            '95+':   _cfg.TIER_ALLOC['ultra'],
            '85-94': _cfg.TIER_ALLOC['top'],
            '80-84': _cfg.TIER_ALLOC['mid'],
            '75-79': _cfg.TIER_ALLOC['low'],
            '70-74': _cfg.TIER_ALLOC['overflow'],
        }

        # H3 — DD-soft band call alloc contraction (shipped 2026-05-04).
        DD_SOFT_BAND_LO    = float(request.args.get('dd_soft_lo',    _cfg.DD_SOFT_BAND_LO))
        DD_SOFT_BAND_HI    = float(request.args.get('dd_soft_hi',    _cfg.DD_SOFT_BAND_HI))
        DD_SOFT_CALL_FLOOR = float(request.args.get('dd_soft_floor', _cfg.DD_SOFT_CALL_FLOOR))

        # RXDD — VIX-band CALL-alloc dampener (shipped 2026-06-04, 30 DTE). Smooth
        # Gaussian contraction of CALL alloc in the low-EV VIX slow-bleed band,
        # gated to running DD >= DD_MIN. Default OFF / no-op for 15 DTE.
        RXDD_ENABLED  = (request.args.get('rxdd_enabled', None).lower() in ('1', 'true', 'yes')
                         if request.args.get('rxdd_enabled', None) is not None
                         else getattr(_cfg, 'RXDD_ENABLED', False))
        RXDD_VIX_C    = float(request.args.get('rxdd_vix_c',  getattr(_cfg, 'RXDD_VIX_C', 0.0)))
        RXDD_VIX_W    = float(request.args.get('rxdd_vix_w',  getattr(_cfg, 'RXDD_VIX_W', 0.0)))
        RXDD_DEPTH    = float(request.args.get('rxdd_depth',  getattr(_cfg, 'RXDD_DEPTH', 0.0)))
        RXDD_DD_MIN   = float(request.args.get('rxdd_dd_min', getattr(_cfg, 'RXDD_DD_MIN', 0.0)))

        def _rxdd_call_scale(dd, vix):
            """Smooth VIX-band CALL alloc multiplier in [1-depth, 1.0]; no-op (1.0)
            when disabled, vix unavailable, running dd < dd_min, or vix_w<=0."""
            if (not RXDD_ENABLED) or vix is None or dd < RXDD_DD_MIN or RXDD_VIX_W <= 0:
                return 1.0
            z = (float(vix) - RXDD_VIX_C) / RXDD_VIX_W
            return 1.0 - RXDD_DEPTH * float(np.exp(-0.5 * z * z))

        # MWDD — McClellan flat-band CALL-alloc dampener (shipped 2026-06-05, 30 DTE).
        MWDD_ENABLED  = (request.args.get('mwdd_enabled', None).lower() in ('1', 'true', 'yes')
                         if request.args.get('mwdd_enabled', None) is not None
                         else getattr(_cfg, 'MWDD_ENABLED', False))
        MWDD_MCC_C    = float(request.args.get('mwdd_mcc_c',     getattr(_cfg, 'MWDD_MCC_C', 0.0)))
        MWDD_MCC_W    = float(request.args.get('mwdd_mcc_w',     getattr(_cfg, 'MWDD_MCC_W', 22.0)))
        MWDD_DEPTH    = float(request.args.get('mwdd_depth',     getattr(_cfg, 'MWDD_DEPTH', 0.0)))
        MWDD_DD_MIN   = float(request.args.get('mwdd_dd_min',    getattr(_cfg, 'MWDD_DD_MIN', 0.10)))
        MWDD_VIX_PANIC= float(request.args.get('mwdd_vix_panic', getattr(_cfg, 'MWDD_VIX_PANIC', 28.0)))

        def _mwdd_call_scale(dd, mcc, vix):
            """Smooth McClellan-flat-band CALL alloc multiplier; no-op (1.0) when disabled,
            mcc unavailable, dd < dd_min, mcc_w<=0, or VIX in the panic band."""
            if (not MWDD_ENABLED) or mcc is None or dd < MWDD_DD_MIN or MWDD_MCC_W <= 0:
                return 1.0
            if vix is not None and MWDD_VIX_PANIC > 0 and float(vix) >= MWDD_VIX_PANIC:
                return 1.0
            z = (float(mcc) - MWDD_MCC_C) / MWDD_MCC_W
            return 1.0 - MWDD_DEPTH * float(np.exp(-0.5 * z * z))

        # TVDD — TRIN neutral volume-flow-band CALL-alloc dampener (Stage-3, 30 DTE).
        TVDD_ENABLED  = (request.args.get('tvdd_enabled', None).lower() in ('1', 'true', 'yes')
                         if request.args.get('tvdd_enabled', None) is not None
                         else getattr(_cfg, 'TVDD_ENABLED', False))
        TVDD_TRIN_C   = float(request.args.get('tvdd_trin_c',    getattr(_cfg, 'TVDD_TRIN_C', 1.15)))
        TVDD_TRIN_W   = float(request.args.get('tvdd_trin_w',    getattr(_cfg, 'TVDD_TRIN_W', 0.30)))
        TVDD_DEPTH    = float(request.args.get('tvdd_depth',     getattr(_cfg, 'TVDD_DEPTH', 0.0)))
        TVDD_DD_MIN   = float(request.args.get('tvdd_dd_min',    getattr(_cfg, 'TVDD_DD_MIN', 0.13)))
        TVDD_VIX_PANIC= float(request.args.get('tvdd_vix_panic', getattr(_cfg, 'TVDD_VIX_PANIC', 28.0)))

        def _tvdd_call_scale(dd, trin, vix):
            """Smooth TRIN-neutral-band CALL alloc multiplier; no-op (1.0) when disabled,
            trin unavailable, dd < dd_min, trin_w<=0, or VIX in the panic band."""
            if (not TVDD_ENABLED) or trin is None or dd < TVDD_DD_MIN or TVDD_TRIN_W <= 0:
                return 1.0
            if vix is not None and TVDD_VIX_PANIC > 0 and float(vix) >= TVDD_VIX_PANIC:
                return 1.0
            z = (float(trin) - TVDD_TRIN_C) / TVDD_TRIN_W
            return 1.0 - TVDD_DEPTH * float(np.exp(-0.5 * z * z))

        # BDIV — pre-top breadth-divergence-at-highs CALL-alloc dampener (Stage-3, 30 DTE).
        # No DD-gate / VIX-panic by design (SPY-near-highs requirement = the crash guard).
        BDIV_ENABLED   = (request.args.get('bdiv_enabled', None).lower() in ('1', 'true', 'yes')
                          if request.args.get('bdiv_enabled', None) is not None
                          else getattr(_cfg, 'BDIV_ENABLED', False))
        BDIV_PROX_CUT  = float(request.args.get('bdiv_prox_cut',  getattr(_cfg, 'BDIV_PROX_CUT', 0.020)))
        BDIV_PROX_FULL = float(request.args.get('bdiv_prox_full', getattr(_cfg, 'BDIV_PROX_FULL', 0.005)))
        BDIV_GAP_C     = float(request.args.get('bdiv_gap_c',     getattr(_cfg, 'BDIV_GAP_C', 6.5)))
        BDIV_GAP_W     = float(request.args.get('bdiv_gap_w',     getattr(_cfg, 'BDIV_GAP_W', 2.5)))
        BDIV_DEPTH     = float(request.args.get('bdiv_depth',     getattr(_cfg, 'BDIV_DEPTH', 0.0)))

        def _bdiv_call_scale(bdiv):
            """Pre-top breadth-divergence CALL alloc multiplier; no-op (1.0) when disabled
            or the (spy_from60h, brd_det10) map value is unavailable."""
            if (not BDIV_ENABLED) or bdiv is None:
                return 1.0
            spyh, det = bdiv
            if spyh is None or det is None or BDIV_GAP_W <= 0 or BDIV_PROX_CUT <= BDIV_PROX_FULL:
                return 1.0
            prox = (float(spyh) + BDIV_PROX_CUT) / (BDIV_PROX_CUT - BDIV_PROX_FULL)
            prox = 0.0 if prox < 0.0 else 1.0 if prox > 1.0 else prox
            if prox <= 0.0:
                return 1.0
            z = (float(det) - BDIV_GAP_C) / BDIV_GAP_W
            return 1.0 - BDIV_DEPTH * prox * float(np.exp(-0.5 * z * z))

        # SVR — semivol_r skew-bridge entry filter (shipped 2026-06-05, 30 DTE).
        # Band-pass CALL-alloc scale by per-signal semivol_r (60d downside/upside
        # vol ratio = live cousin of put-skew). Default OFF / no-op for 15 DTE.
        try:
            from database.utils.semivol import compute_semivol_r as _svr_compute
        except Exception:
            def _svr_compute(closes, idx, win=60):
                return None
        SVR_ENABLED  = (request.args.get('svr_enabled', None).lower() in ('1', 'true', 'yes')
                        if request.args.get('svr_enabled', None) is not None
                        else getattr(_cfg, 'SVR_ENABLED', False))
        SVR_LO_CUT   = float(request.args.get('svr_lo_cut',  getattr(_cfg, 'SVR_LO_CUT', 0.6)))
        SVR_LO_FULL  = float(request.args.get('svr_lo_full', getattr(_cfg, 'SVR_LO_FULL', 0.8)))
        SVR_HI_FULL  = float(request.args.get('svr_hi_full', getattr(_cfg, 'SVR_HI_FULL', 9.0)))
        SVR_HI_CUT   = float(request.args.get('svr_hi_cut',  getattr(_cfg, 'SVR_HI_CUT', 99.0)))
        SVR_FLOOR    = float(request.args.get('svr_floor',   getattr(_cfg, 'SVR_FLOOR', 0.5)))
        _svr_sym_cache = {}   # symbol -> (closes_list, {date: idx})

        def _svr_call_scale(svr):
            """Band-pass CALL-alloc multiplier in [SVR_FLOOR, 1.0]; no-op (1.0) when
            disabled or svr missing. Mirror of backtest_cascade._svr_call_scale."""
            if (not SVR_ENABLED) or svr is None:
                return 1.0
            if svr < SVR_LO_FULL:
                t = 0.0 if SVR_LO_FULL <= SVR_LO_CUT else (svr - SVR_LO_CUT) / (SVR_LO_FULL - SVR_LO_CUT)
                return SVR_FLOOR + (1.0 - SVR_FLOOR) * min(1.0, max(0.0, t))
            if svr > SVR_HI_FULL:
                t = 0.0 if SVR_HI_CUT <= SVR_HI_FULL else (SVR_HI_CUT - svr) / (SVR_HI_CUT - SVR_HI_FULL)
                return SVR_FLOOR + (1.0 - SVR_FLOOR) * min(1.0, max(0.0, t))
            return 1.0

        # SPREAD_TILT — 75-79-band-only high-disagreement CALL-alloc haircut (shipped
        # 2026-06-15, 30 DTE). Mirror of monte_carlo.py / backtest_cascade.py: spread =
        # 5-member component pop-stdev; down-weight only, strictly gated to 75<=score<=79.
        SPREAD_TILT_ENABLED = (request.args.get('spread_tilt_enabled', None).lower() in ('1', 'true', 'yes')
                               if request.args.get('spread_tilt_enabled', None) is not None
                               else getattr(_cfg, 'SPREAD_TILT_ENABLED', False))
        SPREAD_TILT_LO    = float(request.args.get('spread_tilt_lo',    getattr(_cfg, 'SPREAD_TILT_LO', 26.9)))
        SPREAD_TILT_HI    = float(request.args.get('spread_tilt_hi',    getattr(_cfg, 'SPREAD_TILT_HI', 31.3)))
        SPREAD_TILT_DEPTH = float(request.args.get('spread_tilt_depth', getattr(_cfg, 'SPREAD_TILT_DEPTH', 0.40)))
        _SPREAD_TILT_BAND_LO, _SPREAD_TILT_BAND_HI = 75, 79

        def _spread_tilt_call_scale(score, spread):
            """75-79-band CALL-alloc multiplier in [1-DEPTH, 1.0]; no-op (1.0) when
            disabled, spread missing, or score outside 75-79. Mirror of
            backtest_cascade._spread_tilt_call_scale."""
            if (not SPREAD_TILT_ENABLED) or spread is None:
                return 1.0
            if score < _SPREAD_TILT_BAND_LO or score > _SPREAD_TILT_BAND_HI:
                return 1.0
            if SPREAD_TILT_HI <= SPREAD_TILT_LO or SPREAD_TILT_DEPTH <= 0.0:
                return 1.0
            if spread <= SPREAD_TILT_LO:
                return 1.0
            if spread >= SPREAD_TILT_HI:
                return 1.0 - SPREAD_TILT_DEPTH
            t = (spread - SPREAD_TILT_LO) / (SPREAD_TILT_HI - SPREAD_TILT_LO)
            return 1.0 - SPREAD_TILT_DEPTH * t

        # Regime-aware allocation (asymmetric CUT_ONLY shipped 2026-04-17).
        REGIME_SLOPE          = _cfg.REGIME_SLOPE
        REGIME_SLOPE_PUT      = _cfg.REGIME_SLOPE_PUT
        ALLOC_SCALE_FLOOR     = _cfg.ALLOC_SCALE_FLOOR
        ALLOC_SCALE_CEIL      = _cfg.ALLOC_SCALE_CEIL
        REGIME_SLOPE_UP       = _cfg.REGIME_SLOPE_UP
        REGIME_SLOPE_DOWN     = _cfg.REGIME_SLOPE_DOWN
        REGIME_SLOPE_PUT_UP   = _cfg.REGIME_SLOPE_PUT_UP
        REGIME_SLOPE_PUT_DOWN = _cfg.REGIME_SLOPE_PUT_DOWN

        # Breadth-driven allocation knob (F3f) — shipped 2026-04-24.
        BREADTH_ALLOC_ENABLED = _cfg.BREADTH_ALLOC_ENABLED
        F3F_CALL_THRESH       = _cfg.F3F_CALL_THRESH
        F3F_CALL_FLOOR        = _cfg.F3F_CALL_FLOOR
        F3F_CALL_LOW          = _cfg.F3F_CALL_LOW
        F3F_PUT_THRESH        = _cfg.F3F_PUT_THRESH
        F3F_PUT_FLOOR         = _cfg.F3F_PUT_FLOOR
        F3F_PUT_HIGH          = _cfg.F3F_PUT_HIGH

        def _tier(score):
            if score >= 95: return '95+'
            if score >= 85: return '85-94'
            if score >= 80: return '80-84'
            if score >= 75: return '75-79'
            return '70-74'

        # Counter-trend cascade promotion (Path B / V2, shipped 2026-04-21).
        # Defaults from strategy_config; calls with overall>=70 AND TREND<=20
        # are tagged ct_call and promoted to the '95+' (ultra) override tier.
        CT_PROMOTE        = _cfg.CT_PROMOTE
        CT_CALL_TREND_MAX = _cfg.CT_CALL_TREND_MAX
        CT_CALL_TIER      = '95+'

        def _ct_tag(overall, trend, side):
            if not CT_PROMOTE or trend is None:
                return None
            if side == 'call' and overall >= 70 and trend <= CT_CALL_TREND_MAX:
                return 'ct_call'
            return None

        # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
        # Mirror backtest_cascade.py wiring on the simulate path.
        from backtest_cascade import _apply_ctsl_to_signals as _ctsl_apply

        def _realized_vol(closes):
            if len(closes) < MIN_VOL_BARS:
                return None
            arr  = np.array(closes, dtype=float)
            rets = np.diff(arr) / arr[:-1]
            return float(np.std(rets)) * 100.0

        # ---------------------------------------------------------------
        # Load qualifying call-side signals
        # ---------------------------------------------------------------
        version = get_api_score_version()
        raw = list(
            Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
                 .where(
                     (Score.version  == version)
                     & (Score.overall >= min_score)
                     & (Score.date   >= start_date)
                     & (Score.date   <= today)
                 )
                 .order_by(Score.date, Score.overall.desc(), Score.symbol)
                 .namedtuples()
        )
        # Apply CTSL — score-stage continuous lift on CT-eligible call signals.
        # No-op when _cfg.CTSL_ENABLED=False. Re-sort after lift (overall changes).
        raw = _ctsl_apply(raw, 'call')
        raw.sort(key=lambda s: (s.date, -int(s.overall), s.symbol))

        if not raw:
            return jsonify({
                'params':         {'start_date': start_date.isoformat(),
                                   'end_date': today.isoformat(),
                                   'capital': capital, 'min_score': min_score},
                'summary':        {'message': 'No qualifying signals in date range'},
                'trades':         [],
                'open_positions': [],
                'equity_curve':   [],
            })

        symbols = {s.symbol for s in raw}

        # ---------------------------------------------------------------
        # Load price history (vol lookback buffer before start_date)
        # ---------------------------------------------------------------
        buffer_date = start_date - timedelta(days=VOL_BARS * 2)
        settle_end  = today + timedelta(days=HOLD_CALENDAR_DAYS + 5)

        ph = defaultdict(list)
        sym_list = list(symbols)
        for i in range(0, len(sym_list), 100):
            batch = sym_list[i:i + 100]
            rows = (PriceHistory
                    .select(PriceHistory.date,
                            PriceHistory.open,
                            PriceHistory.high,
                            PriceHistory.low,
                            PriceHistory.close,
                            Stock.symbol)
                    .join(Stock)
                    .where(
                        (Stock.symbol.in_(batch))
                        & (PriceHistory.date >= buffer_date)
                        & (PriceHistory.date <= settle_end)
                    )
                    .order_by(Stock.symbol, PriceHistory.date)
                    .namedtuples())
            for r in rows:
                ph[r.symbol].append(r)

        # ---------------------------------------------------------------
        # Compute trade outcome for each signal
        # ---------------------------------------------------------------
        def _outcome(symbol, signal_date, score, ph_rows, trend=None):
            date_idx    = {r.date: i for i, r in enumerate(ph_rows)}
            sig_i       = date_idx.get(signal_date)
            if sig_i is None:
                return None

            entry_price = float(ph_rows[sig_i].close)
            if entry_price <= 0:
                return None

            vol_start   = max(0, sig_i - VOL_BARS)
            closes      = [float(ph_rows[j].close) for j in range(vol_start, sig_i + 1)]
            sigma       = _realized_vol(closes)
            if not sigma or sigma <= 0:
                return None

            tp_price = entry_price * (1.0 + TP_SIGMA * sigma / 100.0)
            sl_price = entry_price * (1.0 - SL_SIGMA * sigma / 100.0)
            deadline = signal_date + timedelta(days=HOLD_CALENDAR_DAYS)
            tier     = CT_CALL_TIER if _ct_tag(score, trend, 'call') else _tier(score)

            for j in range(sig_i + 1, len(ph_rows)):
                bar       = ph_rows[j]
                bar_date  = bar.date
                bars_held = j - sig_i

                # TP checked before SL (intraday high = call win convention)
                if float(bar.high) >= tp_price:
                    return dict(symbol=symbol, signal_date=signal_date, score=score,
                                tier=tier, entry_price=entry_price, sigma_daily=sigma,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                                outcome='tp', exit_date=bar_date, net_return=NET_TP,
                                hold_bars=bars_held, current_price=None)
                if float(bar.low) <= sl_price:
                    return dict(symbol=symbol, signal_date=signal_date, score=score,
                                tier=tier, entry_price=entry_price, sigma_daily=sigma,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                                outcome='sl', exit_date=bar_date, net_return=NET_SL,
                                hold_bars=bars_held, current_price=None)
                if bar_date >= deadline:
                    return dict(symbol=symbol, signal_date=signal_date, score=score,
                                tier=tier, entry_price=entry_price, sigma_daily=sigma,
                                tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                                outcome='hard', exit_date=bar_date, net_return=NET_HARD,
                                hold_bars=bars_held, current_price=None)

            # No resolution yet — position still open
            last_bar = ph_rows[-1]
            return dict(symbol=symbol, signal_date=signal_date, score=score,
                        tier=tier, entry_price=entry_price, sigma_daily=sigma,
                        tp_price=tp_price, sl_price=sl_price, deadline=deadline,
                        outcome='open', exit_date=None, net_return=None,
                        hold_bars=len(ph_rows) - 1 - sig_i,
                        current_price=float(last_bar.close))

        # Load alloc scalar map: breadth_score (F3f) or regime_multiplier (legacy)
        regime_map = {}
        try:
            if BREADTH_ALLOC_ENABLED:
                from database.models.core import MarketBreadth
                brrows = list(
                    MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
                        .where(
                            (MarketBreadth.date >= start_date - timedelta(days=7))
                            & (MarketBreadth.breadth_score.is_null(False))
                        )
                        .order_by(MarketBreadth.date)
                        .namedtuples()
                )
                for br in brrows:
                    regime_map[br.date] = float(br.breadth_score)
            else:
                rrows = list(
                    MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
                        .where(
                            (MarketRegime.date >= start_date - timedelta(days=7))
                            & (MarketRegime.regime_multiplier.is_null(False))
                        )
                        .order_by(MarketRegime.date)
                        .namedtuples()
                )
                for rr in rrows:
                    regime_map[rr.date] = float(rr.regime_multiplier)
        except Exception:
            pass

        _NEUTRAL_VALUE = 50.0 if BREADTH_ALLOC_ENABLED else 1.0

        def _regime_on_or_before(d):
            if d in regime_map:
                return regime_map[d]
            for offset in range(1, 8):
                prev = d - timedelta(days=offset)
                if prev in regime_map:
                    return regime_map[prev]
            return _NEUTRAL_VALUE

        def _alloc_scale(d, is_put=False):
            value = _regime_on_or_before(d)

            if BREADTH_ALLOC_ENABLED:
                # F3f breadth knob
                if value is None:
                    s = 1.0
                elif is_put:
                    if value <= F3F_PUT_THRESH:
                        s = 1.0
                    elif value >= F3F_PUT_HIGH:
                        s = F3F_PUT_FLOOR
                    else:
                        s = 1.0 - (value - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
                else:
                    if value >= F3F_CALL_THRESH:
                        s = 1.0
                    elif value <= F3F_CALL_LOW:
                        s = F3F_CALL_FLOOR
                    else:
                        s = F3F_CALL_FLOOR + (value - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)
                return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, s))

            # Legacy regime_multiplier path
            delta = value - 1.0
            if is_put:
                if delta >= 0 and REGIME_SLOPE_PUT_UP is not None:
                    slope = REGIME_SLOPE_PUT_UP
                elif delta < 0 and REGIME_SLOPE_PUT_DOWN is not None:
                    slope = REGIME_SLOPE_PUT_DOWN
                else:
                    slope = REGIME_SLOPE_PUT if REGIME_SLOPE_PUT is not None else REGIME_SLOPE
            else:
                if delta >= 0 and REGIME_SLOPE_UP is not None:
                    slope = REGIME_SLOPE_UP
                elif delta < 0 and REGIME_SLOPE_DOWN is not None:
                    slope = REGIME_SLOPE_DOWN
                else:
                    slope = REGIME_SLOPE
            if slope == 0.0:
                return 1.0
            scale = 1.0 + slope * delta
            return max(ALLOC_SCALE_FLOOR, min(ALLOC_SCALE_CEIL, scale))

        # SPREAD_TILT component-spread map — load once when enabled (mirror of backtest_cascade).
        _spread_tilt_map = {}
        if SPREAD_TILT_ENABLED:
            try:
                from backtest_cascade import _spread_tilt_load as _st_load
                _spread_tilt_map = _st_load()
            except Exception:
                _spread_tilt_map = {}

        outcomes_by_date = defaultdict(list)
        n_ct_call = 0
        for sig in raw:
            trend = float(sig.trend) if sig.trend is not None else None
            o = _outcome(sig.symbol, sig.date, float(sig.overall),
                         ph.get(sig.symbol, []), trend=trend)
            if o is None:
                continue
            # SVR: stamp per-signal semivol_r (calls only; skew-bridge entry filter)
            if SVR_ENABLED and o.get('side') != 'put':
                _r = ph.get(sig.symbol, [])
                _sv = _svr_sym_cache.get(sig.symbol)
                if _sv is None:
                    _sv = ([float(x.close) for x in _r], {x.date: i for i, x in enumerate(_r)})
                    _svr_sym_cache[sig.symbol] = _sv
                _si = _sv[1].get(sig.date)
                if _si is not None:
                    o['semivol_r'] = _svr_compute(_sv[0], _si)
            # SPREAD_TILT: stamp per-signal component disagreement (75-79-band haircut, calls only)
            if SPREAD_TILT_ENABLED and o.get('side') != 'put':
                o['spread'] = _spread_tilt_map.get((sig.symbol, sig.date))
            if _ct_tag(float(sig.overall), trend, 'call'):
                n_ct_call += 1
            outcomes_by_date[sig.date].append(o)

        # CT-aware tiebreak: CT-promoted (tier='95+' but score<95) fills ahead
        # of natural lower-tier signals, then score desc, then symbol asc.
        def _sort_key(o):
            score       = o['score']
            ct_priority = 0 if (o['tier'] == CT_CALL_TIER and score < 95) else 1
            return (ct_priority, -score, o['symbol'])
        for d in outcomes_by_date:
            outcomes_by_date[d].sort(key=_sort_key)

        # ---------------------------------------------------------------
        # Portfolio simulation (deterministic, no randomness)
        # ---------------------------------------------------------------
        all_dates = sorted({
            r.date
            for rows in ph.values()
            for r in rows
            if start_date <= r.date <= today
        })

        cash        = capital
        open_pos    = []   # [{'outcome': dict, 'premium': float}]
        equity_curve = []
        trade_log   = []
        peak_equity = capital
        max_dd      = 0.0

        # RXDD VIX map — load once when enabled (also when MWDD/TVDD on, for their panic gate).
        rxdd_vix_dates, rxdd_vix_map = [], {}
        if RXDD_ENABLED or MWDD_ENABLED or TVDD_ENABLED:
            try:
                from dte_router import load_router_market_maps as _rxdd_load_maps
                rxdd_vix_dates, rxdd_vix_map, _ = _rxdd_load_maps(start_date, today)
            except Exception:
                rxdd_vix_dates, rxdd_vix_map = [], {}
        # MWDD McClellan map — load once when enabled.
        mwdd_mcc_dates, mwdd_mcc_map = [], {}
        if MWDD_ENABLED:
            try:
                from backtest_cascade import load_mcclellan_map as _mwdd_load_mcc
                mwdd_mcc_dates, mwdd_mcc_map = _mwdd_load_mcc(start_date)
            except Exception:
                mwdd_mcc_dates, mwdd_mcc_map = [], {}
        # TVDD TRIN map — load once when enabled.
        tvdd_trin_dates, tvdd_trin_map = [], {}
        if TVDD_ENABLED:
            try:
                from backtest_cascade import load_trin_map as _tvdd_load_trin
                tvdd_trin_dates, tvdd_trin_map = _tvdd_load_trin(start_date)
            except Exception:
                tvdd_trin_dates, tvdd_trin_map = [], {}
        # BDIV divergence map — load once when enabled.
        bdiv_dates, bdiv_map = [], {}
        if BDIV_ENABLED:
            try:
                from backtest_cascade import load_bdiv_map as _bdiv_load
                bdiv_dates, bdiv_map = _bdiv_load(start_date)
            except Exception:
                bdiv_dates, bdiv_map = [], {}

        for today_d in all_dates:
            # 1. Close positions whose exit date has arrived
            remaining = []
            for pos in open_pos:
                o = pos['outcome']
                if o['outcome'] != 'open' and o['exit_date'] <= today_d:
                    proceeds = pos['premium'] * (1.0 + o['net_return'])
                    cash    += proceeds
                    trade_log.append({
                        'type':           'closed',
                        'symbol':         o['symbol'],
                        'tier':           o['tier'],
                        'score':          round(o['score'], 1),
                        'entry_date':     o['signal_date'].isoformat(),
                        'exit_date':      o['exit_date'].isoformat(),
                        'hold_bars':      o['hold_bars'],
                        'entry_price':    round(o['entry_price'], 2),
                        'sigma_daily':    round(o['sigma_daily'], 3),
                        'tp_price':       round(o['tp_price'], 2),
                        'sl_price':       round(o['sl_price'], 2),
                        'outcome':        o['outcome'],
                        'allocation':     round(pos['premium'], 2),
                        'allocation_pct': TIER_ALLOC[o['tier']] * 100,
                        'pnl':            round(proceeds - pos['premium'], 2),
                        'pnl_pct':        round(o['net_return'] * 100, 2),
                    })
                else:
                    remaining.append(pos)
            open_pos = remaining

            # 2. Mark-to-market (open positions carried at cost basis)
            equity    = cash + sum(p['premium'] for p in open_pos)
            open_syms = {p['outcome']['symbol'] for p in open_pos}

            # 2.5 Running DD snapshot for allocation modifiers.
            running_dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0

            # 3. Enter new trades for today's signals (score desc, re-entry blocked)
            for o in outcomes_by_date.get(today_d, []):
                if len(open_pos) >= MAX_POSITIONS:
                    break
                if o['symbol'] in open_syms:
                    continue

                is_put  = o.get('side') == 'put'
                scale   = _alloc_scale(today_d, is_put=is_put)
                # H3: DD-soft-band call alloc contraction (calls only)
                dd_scale = 1.0
                if (not is_put) and DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and running_dd > DD_SOFT_BAND_LO:
                    if running_dd >= DD_SOFT_BAND_HI:
                        dd_scale = DD_SOFT_CALL_FLOOR
                    else:
                        t = (running_dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
                        dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
                # RXDD: VIX-band call-alloc contraction (calls only)
                rxdd_scale = 1.0
                if (not is_put) and RXDD_ENABLED:
                    from dte_router import value_on_or_before as _rxdd_vob
                    rxdd_vix_today = (_rxdd_vob(rxdd_vix_dates, rxdd_vix_map, today_d)
                                      if rxdd_vix_dates else None)
                    rxdd_scale = _rxdd_call_scale(running_dd, rxdd_vix_today)
                # SVR: semivol_r skew-bridge call-alloc band-pass (calls only)
                svr_scale = 1.0
                if (not is_put) and SVR_ENABLED:
                    svr_scale = _svr_call_scale(o.get('semivol_r'))
                # MWDD: McClellan flat-band call-alloc contraction (calls only), DD-gated, VIX-panic-excluded
                mwdd_scale = 1.0
                if (not is_put) and MWDD_ENABLED:
                    from dte_router import value_on_or_before as _mwdd_vob
                    mwdd_mcc_today = (_mwdd_vob(mwdd_mcc_dates, mwdd_mcc_map, today_d) if mwdd_mcc_dates else None)
                    mwdd_vix_today = (_mwdd_vob(rxdd_vix_dates, rxdd_vix_map, today_d) if rxdd_vix_dates else None)
                    mwdd_scale = _mwdd_call_scale(running_dd, mwdd_mcc_today, mwdd_vix_today)
                # TVDD: TRIN neutral volume-flow-band call-alloc contraction (calls only), DD-gated, VIX-panic-excluded
                tvdd_scale = 1.0
                if (not is_put) and TVDD_ENABLED:
                    from dte_router import value_on_or_before as _tvdd_vob
                    tvdd_trin_today = (_tvdd_vob(tvdd_trin_dates, tvdd_trin_map, today_d) if tvdd_trin_dates else None)
                    tvdd_vix_today = (_tvdd_vob(rxdd_vix_dates, rxdd_vix_map, today_d) if rxdd_vix_dates else None)
                    tvdd_scale = _tvdd_call_scale(running_dd, tvdd_trin_today, tvdd_vix_today)
                # BDIV: pre-top breadth-divergence-at-highs call-alloc contraction (calls only; leading lever, no DD-gate)
                bdiv_scale = 1.0
                if (not is_put) and BDIV_ENABLED:
                    from dte_router import value_on_or_before as _bdiv_vob
                    bdiv_today = (_bdiv_vob(bdiv_dates, bdiv_map, today_d) if bdiv_dates else None)
                    bdiv_scale = _bdiv_call_scale(bdiv_today)
                # SPREAD_TILT: 75-79-band-only high-disagreement call-alloc haircut (calls only)
                spread_tilt_scale = 1.0
                if (not is_put) and SPREAD_TILT_ENABLED:
                    spread_tilt_scale = _spread_tilt_call_scale(o['score'], o.get('spread'))
                premium = TIER_ALLOC[o['tier']] * scale * dd_scale * rxdd_scale * svr_scale * mwdd_scale * tvdd_scale * bdiv_scale * spread_tilt_scale * equity
                if cash < premium or premium < 1.0:
                    continue

                cash    -= premium
                open_pos.append({'outcome': o, 'premium': premium})
                open_syms.add(o['symbol'])
                equity = cash + sum(p['premium'] for p in open_pos)

            # 4. Drawdown tracking
            if equity > peak_equity:
                peak_equity = equity
            dd = (1.0 - equity / peak_equity) if peak_equity > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

            equity_curve.append({'date': today_d.isoformat(), 'equity': round(equity, 2)})

        # ---------------------------------------------------------------
        # Collect still-open positions (not yet resolved by TP/SL/hard)
        # ---------------------------------------------------------------
        open_positions = []
        for pos in open_pos:
            o        = pos['outcome']
            ph_rows  = ph.get(o['symbol'], [])
            cur_price = float(ph_rows[-1].close) if ph_rows else None
            open_positions.append({
                'symbol':         o['symbol'],
                'tier':           o['tier'],
                'score':          round(o['score'], 1),
                'entry_date':     o['signal_date'].isoformat(),
                'entry_price':    round(o['entry_price'], 2),
                'current_price':  round(cur_price, 2) if cur_price else None,
                'sigma_daily':    round(o['sigma_daily'], 3),
                'tp_price':       round(o['tp_price'], 2),
                'sl_price':       round(o['sl_price'], 2),
                'hard_sell_date': o['deadline'].isoformat(),
                'allocation':     round(pos['premium'], 2),
                'allocation_pct': TIER_ALLOC[o['tier']] * 100,
                'days_open':      (today - o['signal_date']).days,
            })

        # ---------------------------------------------------------------
        # Summary stats
        # ---------------------------------------------------------------
        final_equity = cash + sum(p['premium'] for p in open_pos)
        n_tp         = sum(1 for t in trade_log if t['outcome'] == 'tp')
        n_sl         = sum(1 for t in trade_log if t['outcome'] == 'sl')
        n_hard       = sum(1 for t in trade_log if t['outcome'] == 'hard')
        n_closed     = len(trade_log)

        return jsonify({
            'params': {
                'start_date':   start_date.isoformat(),
                'end_date':     today.isoformat(),
                'capital':      capital,
                'min_score':    min_score,
                'version':      version.git_commit[:8] if version else None,
                'tp_pct':       tp_pct,
                'sl_pct':       sl_pct,
                'hard_day':     hard_day,
                'max_pos':      max_pos,
                'dd_soft_lo':   DD_SOFT_BAND_LO,
                'dd_soft_hi':   DD_SOFT_BAND_HI,
                'dd_soft_floor': DD_SOFT_CALL_FLOOR,
                'tp_sigma':     round(TP_SIGMA, 4),
                'sl_sigma':     round(SL_SIGMA, 4),
                'net_tp_pct':   round(NET_TP  * 100, 2),
                'net_sl_pct':   round(NET_SL  * 100, 2),
                'net_hard_pct': round(NET_HARD * 100, 2),
            },
            'summary': {
                'initial_capital':  round(capital, 2),
                'current_equity':   round(final_equity, 2),
                'total_return_pct': round((final_equity / capital - 1) * 100, 2),
                'n_closed':         n_closed,
                'n_open':           len(open_positions),
                'tp_count':         n_tp,
                'sl_count':         n_sl,
                'hard_count':       n_hard,
                'tp_rate_pct':      round(n_tp / n_closed * 100, 1) if n_closed else 0,
                'max_drawdown_pct': round(max_dd * 100, 2),
                'ct_call_count':    n_ct_call,
            },
            'trades':         sorted(trade_log,      key=lambda t: (t['entry_date'], t['symbol'])),
            'open_positions': sorted(open_positions, key=lambda p: (p['entry_date'], p['symbol'])),
            'equity_curve':   equity_curve,
        })

    except Exception as e:
        print(f'ERROR in /api/trader/simulate: {e}')
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404


# ---------------------------------------------------------------------------
# Temporal backtest — year/month breakdown (read from DB, computed at assess time)
# ---------------------------------------------------------------------------
# Monte Carlo results — surfaced to the dashboard so MC headline metrics can
# be displayed alongside per-trade quality without re-running monte_carlo.py.
# Populated by `monte_carlo.py` / `monte_carlo_15dte.py` upserting after each
# window completes. See database/utils/mc_persist.py.
# ---------------------------------------------------------------------------
@app.route('/api/mc/results', methods=['GET'])
def get_mc_results():
    """Return persisted Monte Carlo runs for the active (or specified) algorithm version.

    Query params:
      version: id ("14"), "v14", or commit prefix. Default: active version.
      dte:     '30' (default) or '15'.
      n_iter:  optional minimum iteration count to filter on (e.g. ?n_iter=300
               drops smoke runs with N<300).
      engine:  optional engine label filter (default: all). Example: 'seeded'.
      latest_per_window: '1' (default) — for each window_label keep only the
                          most recently-run row matching the other filters.
                          Set to '0' to return all stored runs (useful when
                          comparing param-hash variants on the same window).

    Response shape:
      {
        version: { id, git_commit, git_message, created_at, is_active },
        active_version_id: int,
        runs: [ { ...MonteCarloRun.to_dict() } ],
        windows: ['2021', '2022', ..., '5y'],   // canonical ordering
        summary: { window: {...best_run}, ... } // headline view per window
      }
    """
    try:
        from database.models.core import AlgorithmVersion, MonteCarloRun
        MonteCarloRun.ensure_schema()

        active_version = AlgorithmVersion.get_active_scores_version()
        if not active_version:
            return jsonify({'error': 'No active algorithm version'}), 404

        # Resolve version selector
        version = active_version
        req_version = request.args.get('version')
        if req_version:
            resolved = _resolve_production_version(req_version)
            if resolved is None:
                return jsonify({'error': f'Algorithm version not found: {req_version}'}), 404
            version = resolved

        dte_strategy = (request.args.get('dte') or '30').strip()
        if dte_strategy not in ('30', '15'):
            dte_strategy = '30'

        engine_filter = request.args.get('engine')
        n_iter_min    = request.args.get('n_iter')
        try:
            n_iter_min = int(n_iter_min) if n_iter_min else None
        except (TypeError, ValueError):
            n_iter_min = None
        latest_per_window = (request.args.get('latest_per_window', '1').strip() == '1')

        # Pull all rows for this version + dte. Filter in Python — the table
        # is small (≤ a few hundred rows per version) so DB-side optimization
        # isn't worth the complexity.
        q = (MonteCarloRun
             .select()
             .where((MonteCarloRun.version == version)
                    & (MonteCarloRun.dte_strategy == dte_strategy))
             .order_by(MonteCarloRun.window_label, MonteCarloRun.run_at.desc()))
        rows = list(q)

        if engine_filter:
            rows = [r for r in rows if r.engine == engine_filter]
        if n_iter_min:
            rows = [r for r in rows if (r.n_iter or 0) >= n_iter_min]

        # Per-window: keep latest run unless latest_per_window=0
        if latest_per_window:
            seen = set()
            kept = []
            for r in rows:        # already sorted run_at desc within label
                key = r.window_label
                if key in seen:
                    continue
                seen.add(key)
                kept.append(r)
            rows = kept

        # Canonical window ordering for the dashboard
        WINDOW_ORDER = ['2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y']
        def _wkey(label):
            try:
                return WINDOW_ORDER.index(label)
            except ValueError:
                return len(WINDOW_ORDER)
        rows.sort(key=lambda r: (_wkey(r.window_label), -(r.n_iter or 0), -(r.run_at.timestamp() if r.run_at else 0)))

        # "Best" per window for the summary view: latest run with the largest
        # n_iter (most authoritative). When latest_per_window is set this
        # collapses to one row per window.
        summary = {}
        for r in rows:
            cur = summary.get(r.window_label)
            if cur is None:
                summary[r.window_label] = r.to_dict()
            else:
                # Prefer larger N, then more recent run_at.
                if (r.n_iter or 0) > (cur.get('n_iter') or 0):
                    summary[r.window_label] = r.to_dict()

        # Build version metadata
        version_info = _version_payload(version, active_version)

        # Available versions (any version that has at least one MC run)
        version_ids_with_data = {
            r.version_id for r in MonteCarloRun.select(MonteCarloRun.version)
                                           .where(MonteCarloRun.dte_strategy == dte_strategy)
        }
        avail = []
        if version_ids_with_data:
            avail_rows = list(
                AlgorithmVersion.select()
                .where(AlgorithmVersion.id.in_(list(version_ids_with_data)))
                .order_by(AlgorithmVersion.id.desc())
            )
            avail = [{
                **_version_payload(v, active_version),
            } for v in avail_rows if v.git_message and not AlgorithmVersion.is_legacy_staging_commit(v.git_commit)]

        return jsonify({
            'version':           version_info,
            'active_version_id': active_version.id,
            'dte_strategy':      dte_strategy,
            'window_order':      WINDOW_ORDER,
            'runs':              [r.to_dict() for r in rows],
            'summary':           summary,
            'available_versions': avail,
        })

    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
def _portfolio_window_label(name):
    labels = {
        'covid_crash_2020': 'Mar 2020 crash',
        'covid_cycle_2020_2021': '2020-2021',
        '2020_now': '2020-now',
        '22_now': '22-now',
    }
    if name in labels:
        return labels[name]
    if name.startswith('year_') and name.endswith('_ytd'):
        return f"{name[5:9]} YTD"
    if name.startswith('year_'):
        return name[5:]
    return name


def _research_pack_portfolio_windows():
    from algorithm_versions.research_pack import DEFAULT_PORTFOLIO_WINDOWS

    return [
        {
            'name': window.name,
            'label': _portfolio_window_label(window.name),
            'start': window.start,
            'end': window.end,
            'primary_metric': window.primary_metric,
            'description': window.description,
        }
        for window in DEFAULT_PORTFOLIO_WINDOWS
    ]


TEMPORAL_PORTFOLIO_WINDOWS = _research_pack_portfolio_windows()


def _month_start(row):
    try:
        return date(int(row.get('year')), int(row.get('month')), 1)
    except Exception:
        return None


def _weighted_rate(rows, n_key, rate_key):
    n_total = 0
    wins = 0.0
    for row in rows:
        n = row.get(n_key)
        rate = row.get(rate_key)
        if n is None or rate is None:
            continue
        n_total += int(n)
        wins += int(n) * float(rate) / 100.0
    if n_total <= 0:
        return None
    return round(wins / n_total * 100.0, 1)


def _load_stress_window_pack(version, dte_strategy, portfolio_profile=None):
    """Return optional post-recalc pack stress-window rows keyed by name."""
    try:
        root = Path(os.getcwd())
        pack_dir = root / '.cache' / 'algorithm_versions' / f'v{int(version.id)}' / 'research_pack'
        paths = []
        if portfolio_profile:
            paths.append(pack_dir / f'stress_windows_{portfolio_profile}.json')
        paths.append(pack_dir / 'stress_windows.json')
        for path in paths:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding='utf-8'))
            if str(payload.get('dte_strategy') or '30') != str(dte_strategy):
                continue
            pack_profile = payload.get('portfolio_profile') or payload.get('profile')
            if portfolio_profile:
                if pack_profile and str(pack_profile) != str(portfolio_profile):
                    continue
                if not pack_profile and str(portfolio_profile) != 'apex':
                    continue
            rows = payload.get('windows') or []
            return {row.get('window', {}).get('name'): row for row in rows if row.get('window')}, str(path)
        return {}, None
    except Exception:
        return {}, None


def _pack_window_metrics(pack_row):
    metrics = (pack_row or {}).get('metrics')
    if not metrics or not metrics.get('complete'):
        return None
    max_dd_event = metrics.get('max_dd_event') or {}
    return {
        'source': 'research_pack',
        'start_date': metrics.get('actual_start_date'),
        'end_date': metrics.get('actual_end_date'),
        'initial_capital': metrics.get('initial_capital'),
        'terminal_equity': metrics.get('terminal_equity'),
        'total_return_pct': metrics.get('total_return_pct'),
        'log10_equity_multiple': metrics.get('log10_equity_multiple'),
        'max_dd_pct': metrics.get('max_dd_pct'),
        'max_dd_peak_date': max_dd_event.get('peak_date'),
        'max_dd_trough_date': max_dd_event.get('trough_date'),
        'n_trades': metrics.get('n_trades'),
        'call_trades': metrics.get('call_trades'),
        'put_trades': metrics.get('put_trades'),
        'tp_rate': metrics.get('tp_rate'),
        'call_tp_rate': metrics.get('call_tp_rate'),
        'put_tp_rate': metrics.get('put_tp_rate'),
        'avg_monthly_return_pct': metrics.get('avg_monthly_return_pct'),
        'median_monthly_return_pct': metrics.get('median_monthly_return_pct'),
        'monthly_return_count': metrics.get('monthly_return_count'),
        'avg_hold_bars': metrics.get('avg_hold_bars'),
        'call_avg_hold_bars': metrics.get('call_avg_hold_bars'),
        'put_avg_hold_bars': metrics.get('put_avg_hold_bars'),
        'avg_open_positions': metrics.get('avg_open_positions'),
        'avg_call_open_positions': metrics.get('avg_call_open_positions'),
        'avg_put_open_positions': metrics.get('avg_put_open_positions'),
        'raw_signals_per_day': metrics.get('raw_signals_per_day'),
        'call_raw_signals_per_day': metrics.get('call_raw_signals_per_day'),
        'put_raw_signals_per_day': metrics.get('put_raw_signals_per_day'),
        'avg_slot_utilization_pct': metrics.get('avg_slot_utilization_pct'),
        'call_slot_utilization_pct': metrics.get('call_slot_utilization_pct'),
        'put_slot_utilization_pct': metrics.get('put_slot_utilization_pct'),
        'pool_full_rate': metrics.get('pool_full_rate'),
        'call_pool_full_rate': metrics.get('call_pool_full_rate'),
        'put_pool_full_rate': metrics.get('put_pool_full_rate'),
        'hydration_gap_per_day': metrics.get('hydration_gap_per_day'),
        'target_entries_per_day': metrics.get('target_entries_per_day'),
        'accepted_entries_per_day': metrics.get('accepted_entries_per_day'),
        'time_to_1m': metrics.get('time_to_1m'),
        'time_to_10m': metrics.get('time_to_10m'),
    }


# Cumulative tier -> non-overlapping utility bands (see algorithm_versions/research_pack.py).
_CALL_TIER_BANDS = {
    '75': ('75-79', '80-84', '85-89', '90-94', '95-100'),
    '85': ('85-89', '90-94', '95-100'),
    '95': ('95-100',),
}
_PUT_TIER_BANDS = {
    'lt25': ('21-25', '16-20', '11-15', '6-10', '0-5'),
    'lt15': ('11-15', '6-10', '0-5'),
}


def _cumulative_band_rate(bands_by_label, labels, rate_key):
    """N-weighted cumulative rate (as a percent) over independent utility bands."""
    n_total = 0
    acc = 0.0
    for label in labels:
        band = bands_by_label.get(label)
        if not band:
            continue
        n = band.get('n') or 0
        rate = band.get(rate_key)
        if not n or rate is None:
            continue
        n_total += int(n)
        acc += int(n) * float(rate)
    if n_total <= 0:
        return None, 0
    return round(acc / n_total * 100.0, 1), n_total


def _scoring_quality_from_utility(utility_doc):
    """Version-level scoring-quality block (WR15 utility + cumulative tier win rates).

    Algorithm versions differ in *scoring*, so the most decision-relevant
    comparison metric is the WR15 utility (Stage-1 primary scoring target) and
    the per-tier 5y win rates, independent of the portfolio profile layer.
    """
    doc = utility_doc or {}
    sides = doc.get('sides') or {}
    call = sides.get('call') or {}
    put = sides.get('put') or {}
    total = doc.get('total') or {}
    call_bands = {b.get('band'): b for b in (call.get('bands') or [])}
    put_bands = {b.get('band'): b for b in (put.get('bands') or [])}

    scoring = {
        'period': doc.get('period'),
        'utility_wr': total.get('utility_wr'),
        'utility_wrtp': total.get('utility_wrtp'),
        'n': total.get('n'),
        'call_utility_wr': call.get('utility_wr'),
        'put_utility_wr': put.get('utility_wr'),
        'call_n': call.get('n'),
        'put_n': put.get('n'),
    }
    for tier, labels in _CALL_TIER_BANDS.items():
        wr, n = _cumulative_band_rate(call_bands, labels, 'wr')
        tp, _ = _cumulative_band_rate(call_bands, labels, 'tp')
        scoring[f'call_wr{tier}'] = wr
        scoring[f'call_tp{tier}'] = tp
        scoring[f'call_n{tier}'] = n
    for tier, labels in _PUT_TIER_BANDS.items():
        wr, n = _cumulative_band_rate(put_bands, labels, 'wr')
        tp, _ = _cumulative_band_rate(put_bands, labels, 'tp')
        scoring[f'put_wr_{tier}'] = wr
        scoring[f'put_tp_{tier}'] = tp
        scoring[f'put_n_{tier}'] = n
    return scoring


def _read_research_pack_json(path):
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _research_pack_compare_row(version, active_version, dte_strategy='30', portfolio_profile=None):
    """Compact version-comparison row sourced from research-pack artifacts."""
    root = Path(os.getcwd())
    pack_dir = root / '.cache' / 'algorithm_versions' / f'v{int(version.id)}' / 'research_pack'
    manifest = _read_research_pack_json(pack_dir / 'manifest.json') or {}
    stress_rows, stress_path = _load_stress_window_pack(version, dte_strategy, (portfolio_profile or {}).get('key'))
    utility_wr15 = _read_research_pack_json(pack_dir / 'utility_5y_wr15.json') or {}
    utility_wr7 = _read_research_pack_json(pack_dir / 'utility_5y_wr7.json') or {}
    horizon_rows = _read_research_pack_json(pack_dir / 'utility_by_horizon.json') or []

    windows = {}
    for name, row in stress_rows.items():
        window = row.get('window') or {}
        windows[name] = {
            'window': window,
            'ready': bool(row.get('ready')),
            'readiness': row.get('readiness') or {},
            'metrics': _pack_window_metrics(row) or {},
        }

    def _horizon_row(period):
        for row in horizon_rows if isinstance(horizon_rows, list) else []:
            if int(row.get('lookback_days') or 0) == 1825 and row.get('period') == period:
                return row
        return None

    def _first_not_none(*values):
        for value in values:
            if value is not None:
                return value
        return None

    def _utility_payload(utility_doc, horizon_row, period):
        utility_total = utility_doc.get('total') or {}
        return {
            'period': utility_doc.get('period') or (horizon_row or {}).get('period') or period,
            'n': _first_not_none(utility_total.get('n'), (horizon_row or {}).get('total_n')),
            'utility_wr': _first_not_none(utility_total.get('utility_wr'), (horizon_row or {}).get('total_utility_wr')),
            'utility_wrtp': _first_not_none(utility_total.get('utility_wrtp'), (horizon_row or {}).get('total_utility_wrtp')),
            'call_n': (horizon_row or {}).get('call_n'),
            'put_n': (horizon_row or {}).get('put_n'),
        }

    horizon_5y_wr15 = _horizon_row('15d')
    horizon_5y_wr7 = _horizon_row('7d')
    primary_utility = _utility_payload(utility_wr15, horizon_5y_wr15, '15d')
    legacy_utility = _utility_payload(utility_wr7, horizon_5y_wr7, '7d')
    if primary_utility.get('utility_wr') is None and legacy_utility.get('utility_wr') is not None:
        primary_utility = {**legacy_utility, 'fallback_period': '7d'}

    health = manifest.get('health') or {}
    version_payload = _version_payload(version, active_version)
    profile_key = (portfolio_profile or {}).get('key')
    profile_name = (portfolio_profile or {}).get('name')
    return {
        'version': version_payload,
        'portfolio_profile': portfolio_profile,
        'portfolio_profile_key': profile_key,
        'portfolio_profile_name': profile_name,
        'version_profile_label': f"{version_payload.get('label')} {profile_name}" if profile_name else version_payload.get('label'),
        'row_key': f"{version_payload.get('id')}:{profile_key or 'profile'}",
        'pack_dir': str(pack_dir),
        'stress_windows_path': stress_path,
        'generated_at': manifest.get('generated_at'),
        'dte_strategy': manifest.get('dte_strategy') or dte_strategy,
        'score_coverage': {
            'min_date': health.get('score_date_min'),
            'max_date': health.get('score_date_max'),
            'rows': health.get('score_rows'),
        },
        'health': {
            'assessment_complete': bool(health.get('assessment_complete')),
            'temporal_complete': bool(health.get('temporal_complete')),
            'portfolio_windows_run': bool(health.get('portfolio_windows_run')),
            'utility_5y_wr15_complete': bool(health.get('utility_5y_wr15_complete')),
            'utility_5y_wr7_complete': bool(health.get('utility_5y_wr7_complete')),
        },
        'windows': windows,
        'utility_5y_wr15': primary_utility,
        'utility_5y_wr7': legacy_utility,
        'scoring': _scoring_quality_from_utility(utility_wr15),
        # 0b Verification Substrate: the standing apex-predictand scorecard
        # (experiments/skill_vs_baseline/verify_scorecard.py run_scorecard -> JSON).
        # Carries the 0d verdict (SHIP/FLAG/BLOCK) + per-trade option-EV vs the
        # climatology + momentum baselines on the live 30dte_apex payoff. Null-tolerant:
        # absent until the artifact is built; a FLAG ('risk-shaper') is the accepted
        # profile, NOT a regression.
        'skill': _read_research_pack_json(pack_dir / 'verify_scorecard.json'),
        # PRF instrument output (experiments/version_scorecard/portfolio_response.py
        # --materialize): the supply-matched portfolio sizing F(phi) derived from
        # this version's own WR15/hydration observables. Instrument-only — never
        # a live config; F proposes, MC disposes.
        'derived_portfolio': _read_research_pack_json(pack_dir / 'derived_portfolio.json'),
    }


def _float_payload(value):
    try:
        if value in (None, ''):
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except (TypeError, ValueError):
        return None


def _active_portfolio_overlay_payload(active_version, dte_strategy='30'):
    """Active portfolio-only overlay surfaced beside score-version rankings."""
    if str(dte_strategy) != '30':
        return None
    try:
        import strategy_config as _sc
        cfg = _sc.STRATEGY_30DTE
        if not getattr(cfg, 'PRACTICAL_EXPOSURE_ENABLED', False):
            return None

        run_dir = Path(os.getcwd()) / '.codex' / 'runs' / 'sentinel_g80_postwire_20260521_044902'
        summary_path = run_dir / 'phase1_summary.csv'
        results_path = run_dir / 'phase1_results.csv'
        findings_path = run_dir / 'findings.md'

        evidence = {}
        candidate = 'g80_c65_p25_ref16_4_pow05_floor55_25m'
        if summary_path.exists():
            with summary_path.open('r', encoding='utf-8', newline='') as fh:
                for row in csv.DictReader(fh):
                    if row.get('candidate') == candidate:
                        evidence = {
                            'objective': _float_payload(row.get('objective')),
                            'avg_worst_dd_improve_pp': _float_payload(row.get('avg_worst_dd_improve_pp')),
                            'avg_mean_dd_improve_pp': _float_payload(row.get('avg_mean_dd_improve_pp')),
                            'max_worst_dd_worse_pp': _float_payload(row.get('max_worst_dd_worse_pp')),
                            'v60_2020_crash_dd_improve_pp': _float_payload(row.get('v60_2020_crash_dd_improve_pp')),
                            'v60_covid_peak_dd_improve_pp': _float_payload(row.get('v60_covid_peak_dd_improve_pp')),
                            'v60_2025_dip_worse_pp': _float_payload(row.get('v60_2025_dip_worse_pp')),
                            'avg_practical_log_final_delta': _float_payload(row.get('avg_practical_log_final_delta')),
                            'wealth_floor_pass_rate': _float_payload(row.get('wealth_floor_pass_rate')),
                            'million_floor_pass_rate': _float_payload(row.get('million_floor_pass_rate')),
                            'avg_open_premium_base_pct': _float_payload(row.get('avg_open_premium_base_pct')),
                            'avg_call_open_premium_base_pct': _float_payload(row.get('avg_call_open_premium_base_pct')),
                            'avg_put_open_premium_base_pct': _float_payload(row.get('avg_put_open_premium_base_pct')),
                            'avg_saturation_scale': _float_payload(row.get('avg_saturation_scale')),
                            'windows': _float_payload(row.get('windows')),
                        }
                        break

        window_map = {}
        if results_path.exists():
            with results_path.open('r', encoding='utf-8', newline='') as fh:
                rows = list(csv.DictReader(fh))
            by_window = {}
            for row in rows:
                by_window[(row.get('candidate'), row.get('window'))] = row
            for source_name, display_name in (
                ('2020_crash', '2020 crash'),
                ('covid_peak', 'COVID peak'),
                ('2020_full', '2020 full'),
                ('2022', '2022'),
                ('2025_dip', '2025 dip'),
                ('2022_now', '2022-now'),
                ('5y', '5y'),
            ):
                base = by_window.get(('baseline', source_name)) or {}
                cand = by_window.get((candidate, source_name)) or {}
                if not cand:
                    continue
                base_dd = _float_payload(base.get('worst_dd'))
                cand_dd = _float_payload(cand.get('worst_dd'))
                window_map[source_name] = {
                    'label': display_name,
                    'baseline_worst_dd_pct': base_dd,
                    'candidate_worst_dd_pct': cand_dd,
                    'dd_improve_pp': (base_dd - cand_dd) if base_dd is not None and cand_dd is not None else None,
                    'baseline_mean_final': _float_payload(base.get('mean_final')),
                    'candidate_mean_final': _float_payload(cand.get('mean_final')),
                    'candidate_mean_dd_pct': _float_payload(cand.get('mean_dd')),
                    'candidate_avg_open_premium_base_pct': _float_payload(cand.get('avg_open_premium_base_pct')),
                }

        return {
            'label': 'Sentinel portfolio profile',
            'candidate': candidate,
            'state': 'active',
            'score_version_changed': False,
            'base_version_id': active_version.id if active_version else None,
            'base_version_label': active_version.production_label if active_version else None,
            'run_dir': str(run_dir),
            'summary_path': str(summary_path),
            'results_path': str(results_path),
            'findings_path': str(findings_path),
            'evidence': evidence,
            'windows': window_map,
            'strategy': {
                'max_positions': cfg.MAX_POSITIONS,
                'max_positions_call': cfg.MAX_POSITIONS_CALL,
                'max_positions_put': cfg.MAX_POSITIONS_PUT,
                'practical_capital_ceiling': cfg.PRACTICAL_CAPITAL_CEILING,
                'gross_premium_cap': cfg.GROSS_PREMIUM_CAP,
                'call_premium_cap': cfg.CALL_PREMIUM_CAP,
                'put_premium_cap': cfg.PUT_PREMIUM_CAP,
                'opp_sat_call_ref': cfg.OPP_SAT_CALL_REF,
                'opp_sat_put_ref': cfg.OPP_SAT_PUT_REF,
                'opp_sat_power': cfg.OPP_SAT_POWER,
                'opp_sat_floor': cfg.OPP_SAT_FLOOR,
            },
        }
    except Exception:
        return None


def _derive_window_metrics(spec, summary, monthly):
    if not monthly:
        return False, ['no monthly temporal rows'], None

    monthly_rows = []
    for row in monthly:
        mo_date = _month_start(row)
        if mo_date is not None:
            monthly_rows.append((mo_date, row))
    monthly_rows.sort(key=lambda pair: pair[0])
    if not monthly_rows:
        return False, ['no valid monthly temporal rows'], None

    start_dt = date.fromisoformat(spec['start'])
    start_month = date(start_dt.year, start_dt.month, 1)
    end_dt = date.fromisoformat(spec['end']) if spec.get('end') else None
    end_month = date(end_dt.year, end_dt.month, 1) if end_dt else monthly_rows[-1][0]
    first_month = monthly_rows[0][0]
    last_month = monthly_rows[-1][0]

    missing = []
    if first_month > start_month:
        missing.append(f"calendar coverage starts {first_month.isoformat()} after {start_month.isoformat()}")
    if last_month < end_month:
        missing.append(f"calendar coverage ends {last_month.isoformat()} before {end_month.isoformat()}")

    selected = [(mo, row) for mo, row in monthly_rows if start_month <= mo <= end_month]
    if not selected:
        missing.append('no monthly rows inside window')
        return False, missing, None
    if missing:
        return False, missing, None

    prior = [(mo, row) for mo, row in monthly_rows if mo < start_month and row.get('equity_end') is not None]
    if prior:
        start_equity = float(prior[-1][1]['equity_end'])
        start_equity_source = f"prior_month:{prior[-1][0].isoformat()}"
    else:
        start_equity = float((summary or {}).get('initial_capital') or 50000.0)
        start_equity_source = 'initial_capital'

    end_equity = None
    for _, row in reversed(selected):
        if row.get('equity_end') is not None:
            end_equity = float(row['equity_end'])
            break
    if end_equity is None:
        missing.append('missing ending equity for window')
        return False, missing, None

    rows = [row for _, row in selected]
    n_trades = sum(int(row.get('n_trades') or 0) for row in rows)
    call_trades = sum(int(row.get('call_n') or 0) for row in rows)
    put_trades = sum(int(row.get('put_n') or 0) for row in rows)
    total_return_pct = (end_equity / start_equity - 1.0) * 100.0 if start_equity else None
    metrics = {
        'source': 'temporal_monthly',
        'start_date': selected[0][0].isoformat(),
        'end_date': selected[-1][0].isoformat(),
        'start_equity': start_equity,
        'start_equity_source': start_equity_source,
        'terminal_equity': end_equity,
        'total_return_pct': total_return_pct,
        'max_dd_pct': None,
        'max_dd_source': 'requires research pack --run-portfolio-windows',
        'n_trades': n_trades,
        'call_trades': call_trades,
        'put_trades': put_trades,
        'tp_rate': _weighted_rate(rows, 'n_trades', 'tp_rate'),
        'call_tp_rate': _weighted_rate(rows, 'call_n', 'call_tp_rate'),
        'put_tp_rate': _weighted_rate(rows, 'put_n', 'put_tp_rate'),
    }
    return not missing, missing, metrics


def _temporal_portfolio_windows(version, summary, yearly, monthly, dte_strategy, portfolio_profile=None):
    pack_rows, pack_path = _load_stress_window_pack(version, dte_strategy, portfolio_profile)
    windows = []
    for spec in TEMPORAL_PORTFOLIO_WINDOWS:
        ready, missing, derived_metrics = _derive_window_metrics(spec, summary, monthly)
        pack_row = pack_rows.get(spec['name'])
        pack_metrics = _pack_window_metrics(pack_row)
        metrics = pack_metrics or derived_metrics
        pack_readiness = (pack_row or {}).get('readiness') or {}
        # A window is genuinely ready if EITHER source can back it. Pack metrics come
        # from `run_portfolio_window`, which passes its own explicit from/to dates and
        # so can cover eras (pre-2016) the derived continuous-backtest fallback can't
        # reach — without this OR, real pack-backed windows (e.g. year_1998) were
        # marked 'waiting' in the UI despite having complete, displayed metrics.
        combined_ready = bool(ready) or bool(pack_metrics)
        windows.append({
            **spec,
            'ready': combined_ready,
            'missing': [] if pack_metrics else missing,
            'metrics': metrics,
            'metric_source': (metrics or {}).get('source') if metrics else None,
            'pack_ready': pack_readiness.get('ready'),
            'pack_missing': pack_readiness.get('missing') or [],
            'has_pack_metrics': bool(pack_metrics),
        })
    return {
        'source': 'temporal_monthly_with_optional_profile_research_pack',
        'portfolio_profile': portfolio_profile,
        'pack_path': pack_path,
        'windows': windows,
    }


def _portfolio_profiles_payload(root: Path | None = None) -> dict:
    import portfolio_profiles

    return portfolio_profiles.payload(root or Path(os.getcwd()))


def _resolve_portfolio_profile_arg(default: str = 'core') -> tuple[str, dict, dict]:
    import portfolio_profiles

    root = Path(os.getcwd())
    raw = request.args.get('profile') or request.args.get('portfolio_profile') or default
    key = portfolio_profiles.normalize_profile_key(raw, root)
    return key, portfolio_profiles.get_profile(key, root), portfolio_profiles.payload(root)


def _load_scorecard_overlays():
    """Load the version-scorecard artifacts (corrected per-trade Q + recency +
    supply burstiness per version; MC speed/safety dominance per version x profile)
    so the compare table can surface the ranking layers the raw packs lack.
    Best-effort: missing files just leave overlays empty.
    """
    base = Path(os.getcwd()) / '.cache' / 'algorithm_versions' / '_scorecard'
    by_version = {}
    mc_by_vp = {}
    try:
        sc = json.loads((base / 'scorecard.json').read_text(encoding='utf-8'))
        for tok, sq in (sc.get('signal_quality') or {}).items():
            supply = sq.get('supply_burstiness') if isinstance(sq.get('supply_burstiness'), dict) else {}
            tot = supply.get('total') or {}
            by_version[tok] = {
                'q_pertrade': (sq.get('Q_raw') * 100.0) if sq.get('Q_raw') is not None else None,
                'recency': sq.get('horizon_consistency'),
                'supply_per_day': tot.get('mean_per_day'),
                'supply_cv': tot.get('cv'),
                'supply_gini': tot.get('gini'),
                'recycle_coverage': supply.get('recycle_coverage'),
                'call_dry_day_rate': (supply.get('call') or {}).get('dry_day_rate'),
                'put_dry_day_rate': (supply.get('put') or {}).get('dry_day_rate'),
            }
    except Exception:
        pass
    # Fallback: versions absent from scorecard.json (it regenerates rarely) get
    # their hydration fields straight from supply_burstiness.json, which
    # signal_supply.py keeps current per version. Added 2026-06-12 after v70-v72
    # surfaced with null hydration in VersionCompare because the scorecard was
    # stale even though the supply rows existed.
    try:
        sb = json.loads((base / 'supply_burstiness.json').read_text(encoding='utf-8'))
        for tok, row in (sb.get('versions') or {}).items():
            if tok in by_version or not (isinstance(row, dict) and row.get('complete')):
                continue
            tot = row.get('total') or {}
            by_version[tok] = {
                'q_pertrade': None,
                'recency': None,
                'supply_per_day': tot.get('mean_per_day'),
                'supply_cv': tot.get('cv'),
                'supply_gini': tot.get('gini'),
                'recycle_coverage': row.get('recycle_coverage'),
                'call_dry_day_rate': (row.get('call') or {}).get('dry_day_rate'),
                'put_dry_day_rate': (row.get('put') or {}).get('dry_day_rate'),
            }
    except Exception:
        pass
    try:
        mc = json.loads((base / 'mc' / 'run1' / 'mc_dominance.json').read_text(encoding='utf-8'))
        n_iter = (mc.get('method') or {}).get('n_iter')
        for pkey, ranked in (mc.get('ranking') or {}).items():
            for r in ranked or []:
                tok = r.get('version')
                if not tok:
                    continue
                mc_by_vp[(tok, pkey)] = {
                    'speed_dominance': r.get('speed_dominance'),
                    'safety_dominance': r.get('safety_dominance'),
                    'chosen_dominance': r.get('chosen_dominance'),
                    'rank': r.get('rank'),
                    'reach_fraction': r.get('reach_fraction'),
                    'median_cal_days_to_1m': r.get('median_cal_days_to_1m'),
                    'dd_median_pct': r.get('dd_median_pct'),
                    'dd_worst_pct': r.get('dd_worst_pct'),
                    'collapse_fraction': r.get('collapse_fraction'),
                    'n_iter': n_iter,
                }
    except Exception:
        pass
    return by_version, mc_by_vp


@app.route('/api/algorithm/versions/compare', methods=['GET'])
def compare_algorithm_versions():
    """Compare shipped algorithm versions using post-recalc research packs."""
    try:
        from database.models.core import AlgorithmVersion

        active_version = get_api_score_version()
        dte_strategy = (request.args.get('dte') or '30').strip()
        if dte_strategy not in ('30', '15'):
            dte_strategy = '30'

        requested = (request.args.get('versions') or '').strip()
        versions = []
        if requested:
            for token in requested.replace(';', ',').split(','):
                token = token.strip()
                if not token:
                    continue
                resolved = _resolve_production_version(token)
                if resolved is None:
                    return jsonify({'error': f'Algorithm version not found: {token}'}), 404
                versions.append(resolved)
        else:
            pack_root = Path(os.getcwd()) / '.cache' / 'algorithm_versions'
            ids_with_packs = []
            if pack_root.exists():
                for path in pack_root.glob('v*/research_pack/manifest.json'):
                    try:
                        ids_with_packs.append(int(path.parents[1].name.lstrip('vV')))
                    except Exception:
                        pass
            # Default scope = honest era only (v69+). Pre-floor versions are
            # RETIRED — score rows purged 2026-06-12, research packs kept —
            # and are only worth rendering on explicit request.
            include_retired = (request.args.get('include_retired') or '').strip() in ('1', 'true', 'yes')
            if not include_retired:
                floor = _honest_era_floor()
                ids_with_packs = [vid for vid in ids_with_packs if vid >= floor]
            if ids_with_packs:
                versions = list(
                    AlgorithmVersion
                    .select()
                    .where(AlgorithmVersion.id.in_(sorted(set(ids_with_packs))))
                    .order_by(AlgorithmVersion.id.desc())
                )

        portfolio_profiles_payload = _portfolio_profiles_payload(Path(os.getcwd()))
        all_profiles = portfolio_profiles_payload.get('profiles') or []
        profile_map = {row.get('key'): row for row in all_profiles if row.get('key')}
        raw_profiles = (request.args.get('profiles') or request.args.get('profile') or 'all').strip()
        if raw_profiles.lower() == 'all':
            profiles_to_run = all_profiles
        else:
            try:
                import portfolio_profiles as _portfolio_profiles
                profiles_to_run = [
                    profile_map[_portfolio_profiles.normalize_profile_key(token, Path(os.getcwd()))]
                    for token in raw_profiles.replace(';', ',').split(',')
                    if token.strip()
                ]
            except (KeyError, TypeError) as exc:
                return jsonify({'error': str(exc), 'portfolio_profiles': portfolio_profiles_payload}), 400

        rows = [
            _research_pack_compare_row(
                version,
                active_version,
                dte_strategy=dte_strategy,
                portfolio_profile=profile,
            )
            for version in versions
            for profile in profiles_to_run
        ]
        rows = [
            row for row in rows
            if row['windows'] or (
                row['portfolio_profile_key'] == portfolio_profiles_payload.get('default_profile', 'sentinel')
                and (row['generated_at'] or row['utility_5y_wr15'].get('utility_wr') is not None)
            )
        ]

        # Merge version-scorecard overlays (corrected per-trade Q + recency +
        # supply burstiness per version; MC speed/safety dominance per version x
        # profile). Only the scoped contenders have these; others stay null.
        sc_by_version, mc_by_vp = _load_scorecard_overlays()
        for row in rows:
            label = (row.get('version') or {}).get('label')
            pkey = row.get('portfolio_profile_key')
            ov = sc_by_version.get(label)
            if ov and isinstance(row.get('scoring'), dict):
                row['scoring'].update({
                    'q_pertrade': ov.get('q_pertrade'),
                    'recency': ov.get('recency'),
                    'supply_per_day': ov.get('supply_per_day'),
                    'supply_cv': ov.get('supply_cv'),
                    'supply_gini': ov.get('supply_gini'),
                    'recycle_coverage': ov.get('recycle_coverage'),
                    'call_dry_day_rate': ov.get('call_dry_day_rate'),
                    'put_dry_day_rate': ov.get('put_dry_day_rate'),
                })
            row['mc'] = mc_by_vp.get((label, pkey))

        return jsonify({
            'dte_strategy': dte_strategy,
            'active_version_id': active_version.id if active_version else None,
            'version_semantics': {
                'rows': 'scoring_algorithm_versions',
                'score_rows_keyed_by': ['symbol', 'date', 'algorithm_version'],
                'portfolio_profiles_keyed_by': ['profile_key', 'profile_version'],
                'portfolio_profile_used_for_scoring': None,
                'portfolio_profiles_apply_after_scoring': True,
                'default_portfolio_profile': portfolio_profiles_payload.get('default_profile', 'sentinel'),
            },
            'portfolio_profiles': portfolio_profiles_payload,
            'portfolio_overlay': _active_portfolio_overlay_payload(active_version, dte_strategy),
            'windows': TEMPORAL_PORTFOLIO_WINDOWS,
            'rows': rows,
            'generated_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        })
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/portfolio/profiles/compare', methods=['GET'])
def compare_portfolio_profiles():
    """Compare Sentinel/Core/Apex portfolio-only profiles on active scores."""
    try:
        import portfolio_profiles

        return jsonify(portfolio_profiles.compare_payload(Path(os.getcwd())))
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


# ---------------------------------------------------------------------------
@app.route('/api/backtest/temporal', methods=['GET'])
def backtest_temporal():
    """Return pre-computed cascade-backtest temporal stats.
    Defaults to the active algorithm version; accepts ?version=ID|vID|commit-prefix
    to surface a historical version's stats. Accepts ?dte=15 to return Phase 15B
    C1 (15 DTE) stats. Accepts ?profile=sentinel|core|apex for the
    portfolio-only profile dimension."""
    try:
        from database.models.core import AlgorithmVersion, BacktestTemporalStats
        BacktestTemporalStats.ensure_schema()
        try:
            portfolio_profile_key, portfolio_profile, portfolio_profiles_payload = _resolve_portfolio_profile_arg()
        except KeyError as exc:
            return jsonify({'error': str(exc), 'portfolio_profiles': _portfolio_profiles_payload(Path(os.getcwd()))}), 400

        ver = get_api_score_version()
        req_version = request.args.get('version')
        if req_version:
            resolved = _resolve_production_version(req_version)
            if resolved is None:
                return jsonify({'error': f'Algorithm version not found: {req_version}'}), 404
            ver = resolved

        # DTE strategy (Phase 16)
        dte_strategy = (request.args.get('dte') or '30').strip()
        if dte_strategy not in ('30', '15'):
            dte_strategy = '30'

        try:
            row = BacktestTemporalStats.get(
                BacktestTemporalStats.version == ver,
                BacktestTemporalStats.dte_strategy == dte_strategy,
                BacktestTemporalStats.portfolio_profile == portfolio_profile_key,
            )
        except BacktestTemporalStats.DoesNotExist:
            cmd = (
                f'trader temporal-refresh --dte {dte_strategy} --profile {portfolio_profile_key}'
                if dte_strategy in ('30', '15')
                else f'trader temporal-refresh --profile {portfolio_profile_key}'
            )
            return jsonify({
                'yearly': [], 'monthly': [], 'monthly_avg': [], 'summary': {},
                'portfolio_profile': portfolio_profile,
                'portfolio_profiles': portfolio_profiles_payload,
                'error': f'No temporal stats for version {ver.production_label} (dte={dte_strategy}, profile={portfolio_profile_key}). Run `{cmd}` to generate.',
            }), 404

        yearly = row.yearly()
        monthly = row.monthly()
        monthly_avg = row.monthly_avg()
        summary = row.summary()

        active_version = get_api_score_version()

        return jsonify({
            'yearly':      yearly,
            'monthly':     monthly,
            'monthly_avg': monthly_avg,
            'summary':     summary,
            'portfolio_windows': _temporal_portfolio_windows(
                ver, summary, yearly, monthly, dte_strategy, portfolio_profile_key
            ),
            'version':     _version_payload(ver, active_version),
            'active_version_id': active_version.id if active_version else None,
            'computed_at': row.computed_at.isoformat() if row.computed_at else None,
            'dte_strategy': row.dte_strategy,
            'portfolio_profile': portfolio_profile,
            'portfolio_profiles': portfolio_profiles_payload,
        })

    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500

# ---------------------------------------------------------------------------
# On-demand backtest — runs the cascade backtest pipeline against the active
# algorithm version for the requested date range and returns structured JSON.
# ---------------------------------------------------------------------------
@app.route('/api/backtest/run', methods=['GET'])
def run_backtest_endpoint():
    """Run the deterministic cascade backtest and return structured results.

    Query params (basic):
      from           YYYY-MM-DD  start date (default: 1 year ago)
      to             YYYY-MM-DD  end date   (default: today)
      capital        float       starting capital USD (default 50000)
      min_score      float       call score floor (default 70)
      max_put_score  float       put score ceiling (default 25)
      calls_only     bool        skip put signals (default false)
      version        str         algorithm version id, vNN label, or commit prefix (default active)
      profile        str         portfolio profile key: sentinel, core, or apex (default sentinel)

    Query params (advanced — all optional, defaults = optimal strategy):
      tp_pct         float  call TP % on premium, base regime (default 30)
      tp_stress_pct  float  call TP % stressed regime (default 35)
      sl_pct         float  call SL % base regime (default 35)
      sl_stress_pct  float  call SL % stressed regime (default 40)
      put_tp_pct     float  put TP % (default 30)
      put_sl_pct     float  put SL % (default 20)
      breadth_adaptive bool  use breadth-adaptive TP/SL (default true)
      hard_sell_day  int    calendar days until hard sell (default 15)
      max_positions  int    max concurrent positions (default 14)
      max_positions_call int max concurrent calls inside the shared pool (default 12)
      max_positions_put int  max concurrent puts inside the shared pool (default 8)
      practical_exposure_enabled bool use Sentinel practical exposure profile (default true for 30 DTE)
      practical_capital_ceiling float allocation-base ceiling in dollars (default 25,000,000)
      gross_premium_cap float max open premium as % of practical base (default 80)
      call_premium_cap  float max open call premium as % of practical base (default 65)
      put_premium_cap   float max open put premium as % of practical base (default 25)
      opp_sat_call_ref  float call opportunity count where saturation begins (default 16)
      opp_sat_put_ref   float put opportunity count where saturation begins (default 4)
      opp_sat_power     float saturation curve power (default 0.5)
      opp_sat_floor     float minimum saturation scale (default 0.55)
      alloc_95plus   float  allocation % for 95+ tier (default 25)
      alloc_85_94    float  allocation % for 85-94 tier (default 15)
      alloc_80_84    float  allocation % for 80-84 tier (default 15)
      alloc_75_79    float  allocation % for 75-79 tier (default 15)
      alloc_70_74    float  allocation % for 70-74 tier (default 0)
      alloc_p15      float  allocation % for puts <=15 tier (default 15)
      alloc_p16_20   float  allocation % for puts 16-20 tier (default 12)
      alloc_p21_25   float  allocation % for puts 21-25 tier (default 12)
      breadth_alloc_enabled bool  use F3f breadth-driven knob (default true).
                                  When false, falls back to legacy regime_multiplier slope.
      f3f_call_thresh float breadth at/above which call alloc is unscaled (default 50)
      f3f_call_floor  float minimum call alloc scale at deepest breadth (default 0.70)
      f3f_call_low    float breadth at which call floor is reached (default 20)
      f3f_put_thresh  float breadth at/below which put alloc is unscaled (default 75)
      f3f_put_floor   float minimum put alloc scale at highest breadth (default 0.75)
      f3f_put_high    float breadth at which put floor is reached (default 95)
    """
    try:
        # Phase 16: route to backtest_cascade_15dte for ?dte=15, otherwise standard 30 DTE.
        _dte_strategy = (request.args.get('dte') or '30').strip()
        if _dte_strategy not in ('30', '15'):
            _dte_strategy = '30'
        try:
            _portfolio_profile_key, _portfolio_profile, _portfolio_profile_catalog = _resolve_portfolio_profile_arg()
        except KeyError as exc:
            return jsonify({'error': str(exc), 'portfolio_profiles': _portfolio_profiles_payload(Path(os.getcwd()))}), 400

        if _dte_strategy == '15':
            from backtest_cascade_15dte import (
                run_cascade_backtest, TIER_ALLOC,
                NET_TP_BASE, NET_SL_BASE, NET_TP_STRESS, NET_SL_STRESS,
                PUT_NET_TP, PUT_NET_SL, NET_HARD,
                TP_SIGMA_BASE, TP_SIGMA_STRESS, SL_SIGMA_BASE, SL_SIGMA_STRESS,
                PUT_TP_SIGMA, PUT_SL_SIGMA, HOLD_CALENDAR_DAYS, MAX_POSITIONS,
                PUT_SL_HOLD_BARS_DEFAULT, PUT_SL_HOLD_BARS_MONDAY,
            )
            from backtest_cascade_15dte import compute_temporal_stats
        else:
            from backtest_cascade import (
                run_cascade_backtest, TIER_ALLOC,
                NET_TP_BASE, NET_SL_BASE, NET_TP_STRESS, NET_SL_STRESS,
                PUT_NET_TP, PUT_NET_SL, NET_HARD,
                TP_SIGMA_BASE, TP_SIGMA_STRESS, SL_SIGMA_BASE, SL_SIGMA_STRESS,
                PUT_TP_SIGMA, PUT_SL_SIGMA, HOLD_CALENDAR_DAYS, MAX_POSITIONS,
                PUT_SL_HOLD_BARS_DEFAULT, PUT_SL_HOLD_BARS_MONDAY,
            )
            from backtest_cascade import compute_temporal_stats

        # ── Parse basic query params ────────────────────────────────────────
        today = date.today()

        from_str = request.args.get('from')
        to_str   = request.args.get('to')
        try:
            from_date = date.fromisoformat(from_str) if from_str else (today - timedelta(days=365))
        except ValueError:
            return jsonify({'error': f'Invalid from date: {from_str!r}'}), 400
        try:
            to_date = date.fromisoformat(to_str) if to_str else today
        except ValueError:
            return jsonify({'error': f'Invalid to date: {to_str!r}'}), 400

        try:
            capital = float(request.args.get('capital', 50_000))
            if capital <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'error': 'capital must be a positive number'}), 400

        try:
            min_score = float(request.args.get('min_score', 70))
        except (ValueError, TypeError):
            return jsonify({'error': 'min_score must be a number'}), 400

        calls_only   = request.args.get('calls_only',   'false').lower() in ('1', 'true', 'yes')
        flagged_only = request.args.get('flagged_only', 'false').lower() in ('1', 'true', 'yes')

        max_put_score_raw = request.args.get('max_put_score')
        max_put_score = float(max_put_score_raw) if max_put_score_raw is not None else None

        # ── Parse advanced params ────────────────────────────────────────────
        def _float(key, default):
            v = request.args.get(key)
            try:
                return float(v) if v is not None else default
            except (ValueError, TypeError):
                return default

        def _int(key, default):
            v = request.args.get(key)
            try:
                return int(v) if v is not None else default
            except (ValueError, TypeError):
                return default

        def _bool(key, default):
            v = request.args.get(key)
            if v is None:
                return default
            return v.lower() in ('1', 'true', 'yes')

        # Defaults from strategy_config (single source of truth). Choose 30 vs
        # 15 DTE strategy based on dte query param. Sweeps override per-request.
        import strategy_config as _sc
        _bcfg = _sc.STRATEGY_15DTE if _dte_strategy == '15' else _sc.STRATEGY_30DTE
        _bopt = _bcfg.option

        tp_pct        = _float('tp_pct',          _bopt.TP_BASE * 100)
        tp_stress_pct = _float('tp_stress_pct',   _bopt.TP_STRESS * 100)
        sl_pct        = _float('sl_pct',          abs(_bopt.SL_BASE) * 100)
        sl_stress_pct = _float('sl_stress_pct',   abs(_bopt.SL_STRESS) * 100)
        put_tp_pct    = _float('put_tp_pct',      _bopt.PUT_TP * 100)
        put_sl_pct    = _float('put_sl_pct',      abs(_bopt.PUT_SL) * 100)
        put_sl_hold_default = _int('put_sl_hold_default', _bopt.PUT_SL_HOLD_BARS_DEFAULT)
        put_sl_hold_monday  = _int('put_sl_hold_monday',  _bopt.PUT_SL_HOLD_BARS_MONDAY)
        breadth_adap  = _bool('breadth_adaptive', True)
        hard_sell_day = _int('hard_sell_day',   _bcfg.HOLD_DAYS)
        hard_sell_loss_pct = _float('hard_sell_loss', None)  # None = keep NET_HARD default
        max_positions = _int('max_positions',   _bcfg.MAX_POSITIONS)
        max_positions_call = _int('max_positions_call', _bcfg.MAX_POSITIONS_CALL)
        max_positions_put  = _int('max_positions_put',  _bcfg.MAX_POSITIONS_PUT)
        practical_exposure_enabled = _bool('practical_exposure_enabled', _bcfg.PRACTICAL_EXPOSURE_ENABLED)
        practical_capital_ceiling = _float('practical_capital_ceiling', _bcfg.PRACTICAL_CAPITAL_CEILING)
        gross_premium_cap = _float('gross_premium_cap', _bcfg.GROSS_PREMIUM_CAP * 100.0) / 100.0
        call_premium_cap = _float('call_premium_cap', _bcfg.CALL_PREMIUM_CAP * 100.0) / 100.0
        put_premium_cap = _float('put_premium_cap', _bcfg.PUT_PREMIUM_CAP * 100.0) / 100.0
        opp_sat_call_ref = _float('opp_sat_call_ref', _bcfg.OPP_SAT_CALL_REF)
        opp_sat_put_ref = _float('opp_sat_put_ref', _bcfg.OPP_SAT_PUT_REF)
        opp_sat_power = _float('opp_sat_power', _bcfg.OPP_SAT_POWER)
        opp_sat_floor = _float('opp_sat_floor', _bcfg.OPP_SAT_FLOOR)

        # Cascade allocation defaults from strategy_config semantic keys,
        # exposed to clients with display-keyed names.
        alloc_95plus  = _float('alloc_95plus',  _bcfg.TIER_ALLOC['ultra']    * 100) / 100.0
        alloc_85_94   = _float('alloc_85_94',   _bcfg.TIER_ALLOC['top']      * 100) / 100.0
        alloc_80_84   = _float('alloc_80_84',   _bcfg.TIER_ALLOC['mid']      * 100) / 100.0
        alloc_75_79   = _float('alloc_75_79',   _bcfg.TIER_ALLOC['low']      * 100) / 100.0
        alloc_70_74   = _float('alloc_70_74',   _bcfg.TIER_ALLOC['overflow'] * 100) / 100.0
        alloc_p15     = _float('alloc_p15',     _bcfg.PUT_TIER_ALLOC['put_top'] * 100) / 100.0
        alloc_p16_20  = _float('alloc_p16_20',  _bcfg.PUT_TIER_ALLOC['put_mid'] * 100) / 100.0
        alloc_p21_25  = _float('alloc_p21_25',  _bcfg.PUT_TIER_ALLOC['put_low'] * 100) / 100.0

        # F3f breadth-driven allocation knob — defaults from strategy_config.
        breadth_alloc_enabled = _bool('breadth_alloc_enabled', _bcfg.BREADTH_ALLOC_ENABLED)
        f3f_call_thresh = _float('f3f_call_thresh', _bcfg.F3F_CALL_THRESH)
        f3f_call_floor  = _float('f3f_call_floor',  _bcfg.F3F_CALL_FLOOR)
        f3f_call_low    = _float('f3f_call_low',    _bcfg.F3F_CALL_LOW)
        f3f_put_thresh  = _float('f3f_put_thresh',  _bcfg.F3F_PUT_THRESH)
        f3f_put_floor   = _float('f3f_put_floor',   _bcfg.F3F_PUT_FLOOR)
        f3f_put_high    = _float('f3f_put_high',    _bcfg.F3F_PUT_HIGH)
        dd_soft_lo      = _float('dd_soft_lo',       _bcfg.DD_SOFT_BAND_LO)
        dd_soft_hi      = _float('dd_soft_hi',       _bcfg.DD_SOFT_BAND_HI)
        dd_soft_floor   = _float('dd_soft_floor',    _bcfg.DD_SOFT_CALL_FLOOR)
        rxdd_enabled    = _bool('rxdd_enabled',  getattr(_bcfg, 'RXDD_ENABLED', False))
        rxdd_vix_c      = _float('rxdd_vix_c',   getattr(_bcfg, 'RXDD_VIX_C', 0.0))
        rxdd_vix_w      = _float('rxdd_vix_w',   getattr(_bcfg, 'RXDD_VIX_W', 0.0))
        rxdd_depth      = _float('rxdd_depth',   getattr(_bcfg, 'RXDD_DEPTH', 0.0))
        rxdd_dd_min     = _float('rxdd_dd_min',  getattr(_bcfg, 'RXDD_DD_MIN', 0.0))
        mwdd_enabled    = _bool('mwdd_enabled', getattr(_bcfg, 'MWDD_ENABLED', False))
        mwdd_mcc_c      = _float('mwdd_mcc_c',   getattr(_bcfg, 'MWDD_MCC_C', 0.0))
        mwdd_mcc_w      = _float('mwdd_mcc_w',   getattr(_bcfg, 'MWDD_MCC_W', 22.0))
        mwdd_depth      = _float('mwdd_depth',   getattr(_bcfg, 'MWDD_DEPTH', 0.0))
        mwdd_dd_min     = _float('mwdd_dd_min',  getattr(_bcfg, 'MWDD_DD_MIN', 0.10))
        mwdd_vix_panic  = _float('mwdd_vix_panic', getattr(_bcfg, 'MWDD_VIX_PANIC', 28.0))
        tvdd_enabled    = _bool('tvdd_enabled', getattr(_bcfg, 'TVDD_ENABLED', False))
        tvdd_trin_c     = _float('tvdd_trin_c',  getattr(_bcfg, 'TVDD_TRIN_C', 1.15))
        tvdd_trin_w     = _float('tvdd_trin_w',  getattr(_bcfg, 'TVDD_TRIN_W', 0.30))
        tvdd_depth      = _float('tvdd_depth',   getattr(_bcfg, 'TVDD_DEPTH', 0.0))
        tvdd_dd_min     = _float('tvdd_dd_min',  getattr(_bcfg, 'TVDD_DD_MIN', 0.13))
        tvdd_vix_panic  = _float('tvdd_vix_panic', getattr(_bcfg, 'TVDD_VIX_PANIC', 28.0))
        bdiv_enabled    = _bool('bdiv_enabled', getattr(_bcfg, 'BDIV_ENABLED', False))
        bdiv_prox_cut   = _float('bdiv_prox_cut',  getattr(_bcfg, 'BDIV_PROX_CUT', 0.020))
        bdiv_prox_full  = _float('bdiv_prox_full', getattr(_bcfg, 'BDIV_PROX_FULL', 0.005))
        bdiv_gap_c      = _float('bdiv_gap_c',   getattr(_bcfg, 'BDIV_GAP_C', 6.5))
        bdiv_gap_w      = _float('bdiv_gap_w',   getattr(_bcfg, 'BDIV_GAP_W', 2.5))
        bdiv_depth      = _float('bdiv_depth',   getattr(_bcfg, 'BDIV_DEPTH', 0.0))
        svr_enabled     = _bool('svr_enabled',  getattr(_bcfg, 'SVR_ENABLED', False))
        svr_lo_cut      = _float('svr_lo_cut',  getattr(_bcfg, 'SVR_LO_CUT', 0.6))
        svr_lo_full     = _float('svr_lo_full', getattr(_bcfg, 'SVR_LO_FULL', 0.8))
        svr_hi_full     = _float('svr_hi_full', getattr(_bcfg, 'SVR_HI_FULL', 9.0))
        svr_hi_cut      = _float('svr_hi_cut',  getattr(_bcfg, 'SVR_HI_CUT', 99.0))
        svr_floor       = _float('svr_floor',   getattr(_bcfg, 'SVR_FLOOR', 0.5))
        spread_tilt_enabled = _bool('spread_tilt_enabled', getattr(_bcfg, 'SPREAD_TILT_ENABLED', False))
        spread_tilt_lo      = _float('spread_tilt_lo',    getattr(_bcfg, 'SPREAD_TILT_LO', 26.9))
        spread_tilt_hi      = _float('spread_tilt_hi',    getattr(_bcfg, 'SPREAD_TILT_HI', 31.3))
        spread_tilt_depth   = _float('spread_tilt_depth', getattr(_bcfg, 'SPREAD_TILT_DEPTH', 0.40))
        # LIQUIDITY_FLOOR — option-volume admission filter (staged ship-candidate
        # 2026-08-07, Core-evidence-only). 0.0 = off (contracts/day; the value
        # itself is the switch, no separate enabled flag).
        liquidity_floor     = _float('liquidity_floor',   getattr(_bcfg, 'LIQUIDITY_FLOOR', 0.0))

        # σ ↔ % conversion uses the active strategy's premium/delta.
        # Per-request tp/sl % overrides scale linearly through this mult.
        _premium_mult = _bcfg.PREMIUM_MULT
        _delta        = _bopt.DELTA
        _slip_entry   = _float('slip_entry', _bopt.SLIP_ENTRY)
        _slip_tp      = _float('slip_tp',    _bopt.SLIP_TP)
        _slip_sl      = _float('slip_sl',    _bopt.SLIP_SL)
        def _to_sigma(pct): return (pct / 100.0) * _premium_mult / _delta
        def _net_tp(pct):   return  pct / 100.0 + _slip_entry + _slip_tp
        def _net_sl(pct):   return -(pct / 100.0) + _slip_entry + _slip_sl

        cfg = {
            'breadth_adaptive':   breadth_adap,
            'hold_calendar_days': hard_sell_day,
            'max_positions':      max_positions,
            'max_positions_call': max_positions_call,
            'max_positions_put':  max_positions_put,
            'practical_exposure_enabled': practical_exposure_enabled,
            'practical_capital_ceiling': practical_capital_ceiling,
            'gross_premium_cap':  gross_premium_cap,
            'call_premium_cap':   call_premium_cap,
            'put_premium_cap':    put_premium_cap,
            'opp_sat_call_ref':   opp_sat_call_ref,
            'opp_sat_put_ref':    opp_sat_put_ref,
            'opp_sat_power':      opp_sat_power,
            'opp_sat_floor':      opp_sat_floor,
            'net_hard':           (hard_sell_loss_pct / 100.0) if hard_sell_loss_pct is not None else NET_HARD,
            # Call barriers
            'tp_sigma_base':   _to_sigma(tp_pct),
            'tp_sigma_stress': _to_sigma(tp_stress_pct),
            'sl_sigma_base':   _to_sigma(sl_pct),
            'sl_sigma_stress': _to_sigma(sl_stress_pct),
            'net_tp_base':     _net_tp(tp_pct),
            'net_tp_stress':   _net_tp(tp_stress_pct),
            'net_sl_base':     _net_sl(sl_pct),
            'net_sl_stress':   _net_sl(sl_stress_pct),
            # Put barriers
            'put_tp_sigma':        _to_sigma(put_tp_pct),
            'put_sl_sigma':        _to_sigma(put_sl_pct),
            'put_net_tp':          _net_tp(put_tp_pct),
            'put_net_sl':          _net_sl(put_sl_pct),
            'put_sl_hold_default': put_sl_hold_default,
            'put_sl_hold_monday':  put_sl_hold_monday,
            # Tier allocation
            'tier_alloc': {
                '95+':    alloc_95plus,
                '85-94':  alloc_85_94,
                '80-84':  alloc_80_84,
                '75-79':  alloc_75_79,
                '70-74':  alloc_70_74,
                'p<=15':  alloc_p15,
                'p16-20': alloc_p16_20,
                'p21-25': alloc_p21_25,
            },
            # F3f breadth-driven allocation knob
            'breadth_alloc_enabled': breadth_alloc_enabled,
            'f3f_call_thresh':       f3f_call_thresh,
            'f3f_call_floor':        f3f_call_floor,
            'f3f_call_low':          f3f_call_low,
            'f3f_put_thresh':        f3f_put_thresh,
            'f3f_put_floor':         f3f_put_floor,
            'f3f_put_high':          f3f_put_high,
            # DD-soft-band allocation contraction
            'dd_soft_band_lo':        dd_soft_lo,
            'dd_soft_band_hi':        dd_soft_hi,
            'dd_soft_call_floor':     dd_soft_floor,
            # RXDD VIX-band call-alloc dampener (run_cascade_backtest reads these)
            'rxdd_enabled':           rxdd_enabled,
            'rxdd_vix_c':             rxdd_vix_c,
            'rxdd_vix_w':             rxdd_vix_w,
            'rxdd_depth':             rxdd_depth,
            'rxdd_dd_min':            rxdd_dd_min,
            # MWDD McClellan flat-band call-alloc dampener (run_cascade_backtest reads these)
            'mwdd_enabled':           mwdd_enabled,
            'mwdd_mcc_c':             mwdd_mcc_c,
            'mwdd_mcc_w':             mwdd_mcc_w,
            'mwdd_depth':             mwdd_depth,
            'mwdd_dd_min':            mwdd_dd_min,
            'mwdd_vix_panic':         mwdd_vix_panic,
            # TVDD TRIN neutral volume-flow-band call-alloc dampener (run_cascade_backtest reads these)
            'tvdd_enabled':           tvdd_enabled,
            'tvdd_trin_c':            tvdd_trin_c,
            'tvdd_trin_w':            tvdd_trin_w,
            'tvdd_depth':             tvdd_depth,
            'tvdd_dd_min':            tvdd_dd_min,
            'tvdd_vix_panic':         tvdd_vix_panic,
            # BDIV pre-top breadth-divergence call-alloc dampener (run_cascade_backtest reads these)
            'bdiv_enabled':           bdiv_enabled,
            'bdiv_prox_cut':          bdiv_prox_cut,
            'bdiv_prox_full':         bdiv_prox_full,
            'bdiv_gap_c':             bdiv_gap_c,
            'bdiv_gap_w':             bdiv_gap_w,
            'bdiv_depth':             bdiv_depth,
            # SVR semivol_r skew-bridge entry filter (run_cascade_backtest reads these)
            'svr_enabled':            svr_enabled,
            'svr_lo_cut':             svr_lo_cut,
            'svr_lo_full':            svr_lo_full,
            'svr_hi_full':            svr_hi_full,
            'svr_hi_cut':             svr_hi_cut,
            'svr_floor':              svr_floor,
            # SPREAD_TILT 75-79 component-spread call-alloc haircut (run_cascade_backtest reads these)
            'spread_tilt_enabled':    spread_tilt_enabled,
            'spread_tilt_lo':         spread_tilt_lo,
            'spread_tilt_hi':         spread_tilt_hi,
            'spread_tilt_depth':      spread_tilt_depth,
            # LIQUIDITY_FLOOR option-volume admission filter (load_signals reads this via cfg)
            'liquidity_floor':        liquidity_floor,
        }

        import portfolio_profiles as _portfolio_profiles
        cfg.update(_portfolio_profiles.profile_config_overrides(_portfolio_profile_key, Path(os.getcwd())))
        # Per-request user overrides — applied AFTER the profile so a user edit WINS
        # over the profile default (otherwise the profile clobbers gross_premium_cap,
        # the cascade allocations, opp-sat, max-positions, dd-soft, and the module-
        # global knobs). Explicit-only, so a default run is byte-identical. The
        # backtest_cascade override wrapper applies the module-global knobs.
        import portfolio_param_manifest as _ppm_ovr
        _user_ovr = _ppm_ovr.request_cfg_overrides(request.args.get, _dte_strategy)
        _tier_ovr = _user_ovr.pop('tier_alloc', None)
        cfg.update(_user_ovr)
        if _tier_ovr:
            cfg.setdefault('tier_alloc', {}).update(_tier_ovr)

        max_positions = cfg['max_positions']
        max_positions_call = cfg.get('max_positions_call')
        max_positions_put = cfg.get('max_positions_put')
        practical_exposure_enabled = cfg['practical_exposure_enabled']
        practical_capital_ceiling = cfg['practical_capital_ceiling']
        gross_premium_cap = cfg['gross_premium_cap']
        call_premium_cap = cfg['call_premium_cap']
        put_premium_cap = cfg['put_premium_cap']
        opp_sat_call_ref = cfg['opp_sat_call_ref']
        opp_sat_put_ref = cfg['opp_sat_put_ref']
        opp_sat_power = cfg['opp_sat_power']
        opp_sat_floor = cfg['opp_sat_floor']
        tier_alloc_effective = cfg['tier_alloc']
        alloc_95plus = tier_alloc_effective.get('95+', alloc_95plus)
        alloc_85_94 = tier_alloc_effective.get('85-94', alloc_85_94)
        alloc_80_84 = tier_alloc_effective.get('80-84', alloc_80_84)
        alloc_75_79 = tier_alloc_effective.get('75-79', alloc_75_79)
        alloc_70_74 = tier_alloc_effective.get('70-74', alloc_70_74)
        alloc_p15 = tier_alloc_effective.get('p<=15', alloc_p15)
        alloc_p16_20 = tier_alloc_effective.get('p16-20', alloc_p16_20)
        alloc_p21_25 = tier_alloc_effective.get('p21-25', alloc_p21_25)
        dd_soft_lo = cfg['dd_soft_band_lo']
        dd_soft_hi = cfg['dd_soft_band_hi']
        dd_soft_floor = cfg['dd_soft_call_floor']
        rxdd_enabled = cfg['rxdd_enabled']
        rxdd_vix_c = cfg['rxdd_vix_c']
        rxdd_vix_w = cfg['rxdd_vix_w']
        rxdd_depth = cfg['rxdd_depth']
        rxdd_dd_min = cfg['rxdd_dd_min']
        mwdd_enabled = cfg['mwdd_enabled']
        mwdd_mcc_c = cfg['mwdd_mcc_c']
        mwdd_mcc_w = cfg['mwdd_mcc_w']
        mwdd_depth = cfg['mwdd_depth']
        mwdd_dd_min = cfg['mwdd_dd_min']
        mwdd_vix_panic = cfg['mwdd_vix_panic']
        svr_enabled = cfg['svr_enabled']
        svr_lo_cut = cfg['svr_lo_cut']
        svr_lo_full = cfg['svr_lo_full']
        svr_hi_full = cfg['svr_hi_full']
        svr_hi_cut = cfg['svr_hi_cut']
        svr_floor = cfg['svr_floor']
        liquidity_floor = cfg['liquidity_floor']

        net_tp_base   = cfg['net_tp_base']
        net_tp_stress = cfg['net_tp_stress']
        net_sl_base   = cfg['net_sl_base']
        net_sl_stress = cfg['net_sl_stress']
        put_net_tp    = cfg['put_net_tp']
        put_net_sl    = cfg['put_net_sl']

        # ── Resolve algorithm version ────────────────────────────────────────
        active_version = get_api_score_version()
        if active_version is None:
            return jsonify({'error': 'No algorithm version found'}), 404
        version_catalog = _score_version_catalog(active_version)
        ver = active_version
        req_version = request.args.get('version')
        if req_version:
            resolved = _resolve_production_version(req_version)
            if resolved is None:
                return jsonify({
                    'error': f'Algorithm version not found: {req_version}',
                    'active_version_id': active_version.id,
                    **_score_version_response_fields(version_catalog),
                }), 404
            ver = resolved

        # Overflow-band loader fix: a profile (e.g. Apex) can deploy into the
        # 70-74 overflow tier, but the primary call gate (min_score) defaults to
        # 75 and would exclude that band entirely — leaving the overflow alloc
        # inert (those signals never load). When overflow alloc is on and the user
        # is at/below the primary gate, drop the LOAD floor to OVERFLOW_THRESHOLD
        # so the 70-74 band is available. The cascade still treats 75+ as primary
        # and gives 70-74 the small overflow alloc; an explicit min_score above the
        # primary gate (e.g. 80) is respected as 80-only.
        _load_min_score = min_score
        _overflow_alloc = (cfg.get('tier_alloc') or {}).get('70-74', 0.0) or 0.0
        if _overflow_alloc > 0 and min_score <= _bcfg.PRIMARY_THRESHOLD:
            _load_min_score = min(float(min_score), float(_bcfg.OVERFLOW_THRESHOLD))

        # ── Run the backtest ─────────────────────────────────────────────────
        result = run_cascade_backtest(
            version_id=ver.id,
            min_score=_load_min_score,
            max_put_score=max_put_score,
            from_date=from_date,
            to_date=to_date,
            initial=capital,
            verbose=False,
            calls_only=calls_only,
            flagged_only=flagged_only,
            cfg=cfg,
        )

        if not result:
            return jsonify({'error': 'No qualifying signals in this date range'}), 404

        trade_log     = result['trade_log']
        equity_curve  = result['equity_curve']
        initial       = result['initial']
        final         = result['final_equity']
        max_dd        = result['max_dd']
        open_holdings = result.get('open_holdings', [])

        # ── Current scores for open holdings ────────────────────────────────
        open_syms = {h['symbol'] for h in open_holdings}
        current_scores = {}
        if open_syms:
            try:
                cur_rows = (Score
                            .select(Score.symbol, Score.overall, Score.date)
                            .join(Stock)
                            .where(
                                (Score.version == ver)
                                & (Stock.symbol.in_(list(open_syms)))
                            )
                            .order_by(Score.date.desc())
                            .namedtuples())
                seen = set()
                for row in cur_rows:
                    if row.symbol not in seen:
                        current_scores[row.symbol] = round(float(row.overall), 1)
                        seen.add(row.symbol)
            except Exception:
                pass

        # ── Summary stats ────────────────────────────────────────────────────
        calls  = [t for t in trade_log if t.get('side', 'call') == 'call']
        puts   = [t for t in trade_log if t.get('side') == 'put']
        n_tot  = len(trade_log)
        n_tp   = sum(1 for t in trade_log if t['outcome'] == 'tp')
        n_sl   = sum(1 for t in trade_log if t['outcome'] == 'sl')
        n_hard = sum(1 for t in trade_log if t['outcome'] == 'hard')
        c_tp   = sum(1 for t in calls if t['outcome'] == 'tp')
        p_tp   = sum(1 for t in puts  if t['outcome'] == 'tp')
        n_stressed = sum(1 for t in trade_log if t.get('stressed'))

        be_calm     = abs(net_sl_base)   / (net_tp_base   + abs(net_sl_base))
        be_stressed = abs(net_sl_stress) / (net_tp_stress + abs(net_sl_stress))
        be_put      = abs(put_net_sl)    / (put_net_tp    + abs(put_net_sl))

        # ── Tier breakdown ───────────────────────────────────────────────────
        active_alloc = cfg['tier_alloc']
        tier_stats = []
        for tier, alloc in active_alloc.items():
            tt = [t for t in trade_log if t['tier'] == tier]
            n  = len(tt)
            tier_stats.append({
                'tier':      tier,
                'n':         n,
                'alloc_pct': round(alloc * 100, 0),
                'tp_rate':   round(sum(1 for t in tt if t['outcome'] == 'tp') / n * 100, 1) if n else None,
                'avg_hold':  round(sum(t['hold_bars'] for t in tt) / n, 1) if n else None,
            })

        # ── Temporal breakdown (yearly + monthly) ────────────────────────────
        temporal = compute_temporal_stats(trade_log, equity_curve, initial)

        # ── Equity curve (downsample to ≤500 points for charting) ────────────
        ec_full = [{'date': str(d), 'equity': round(eq, 2)} for d, eq in equity_curve]
        if len(ec_full) > 500:
            step  = len(ec_full) // 500
            ec_out = ec_full[::step]
            if ec_out[-1] != ec_full[-1]:
                ec_out.append(ec_full[-1])
        else:
            ec_out = ec_full

        # ── Trade log (serialise dates) ──────────────────────────────────────
        trades_out = [
            {
                'entry_date':  str(t['entry_date']),
                'exit_date':   str(t['exit_date']),
                'symbol':      t['symbol'],
                'score':       round(t['score'], 1),
                'tier':        t['tier'],
                'side':        t.get('side', 'call'),
                'premium':     round(t['premium'], 2),
                'outcome':     t['outcome'],
                'pnl':         round(t['pnl'], 2),
                'pnl_pct':     round(t['pnl_pct'] * 100, 1),
                'stressed':    bool(t.get('stressed', False)),
                'hold_bars':   t['hold_bars'],
                'sigma':       round(t['sigma'], 2),
                'entry_price':     round(t.get('entry_price', 0), 2),
                'exit_price':      round(t.get('exit_price',  0), 2),
                'portfolio_value': round(t.get('portfolio_value', 0)),
            }
            for t in trade_log
        ]

        # ── CAD/USD rate (DB cache → fallback 0.73) ──────────────────────────
        cad_usd_rate = 0.73  # fallback: 1 CAD = 0.73 USD
        try:
            from database.models.core import ExchangeRate
            ExchangeRate.ensure_schema()
            cached_rate = ExchangeRate.get_latest('CAD', 'USD')
            if cached_rate:
                cad_usd_rate = float(cached_rate)
        except Exception:
            pass

        params_payload = {
            'from':             str(from_date),
            'to':               str(to_date),
            'capital':          capital,
            'min_score':        min_score,
            'max_put_score':    max_put_score,
            'calls_only':       calls_only,
            'version':          ver.git_commit[:8] if ver.git_commit else str(ver.id),
            'cad_usd_rate':     round(cad_usd_rate, 4),
            'portfolio_profile': _portfolio_profile_key,
            'portfolio_profile_name': _portfolio_profile.get('name'),
            'portfolio_profile_version': _portfolio_profile.get('version'),
            'portfolio_profile_color': _portfolio_profile.get('color'),
            # Advanced params echoed back
            'tp_pct':           tp_pct,
            'tp_stress_pct':    tp_stress_pct,
            'sl_pct':           sl_pct,
            'sl_stress_pct':    sl_stress_pct,
            'put_tp_pct':       put_tp_pct,
            'put_sl_pct':       put_sl_pct,
            'breadth_adaptive': breadth_adap,
            'hard_sell_day':    hard_sell_day,
            'max_positions':    max_positions,
            'max_positions_call': max_positions_call,
            'max_positions_put': max_positions_put,
            'practical_exposure_enabled': practical_exposure_enabled,
            'practical_capital_ceiling': practical_capital_ceiling,
            'gross_premium_cap': round(gross_premium_cap * 100, 4),
            'call_premium_cap': round(call_premium_cap * 100, 4),
            'put_premium_cap': round(put_premium_cap * 100, 4),
            'opp_sat_call_ref': opp_sat_call_ref,
            'opp_sat_put_ref': opp_sat_put_ref,
            'opp_sat_power': opp_sat_power,
            'opp_sat_floor': opp_sat_floor,
            'dd_soft_lo': dd_soft_lo,
            'dd_soft_hi': dd_soft_hi,
            'dd_soft_floor': dd_soft_floor,
            'rxdd_enabled': rxdd_enabled,
            'rxdd_vix_c': rxdd_vix_c,
            'rxdd_vix_w': rxdd_vix_w,
            'rxdd_depth': rxdd_depth,
            'rxdd_dd_min': rxdd_dd_min,
            'mwdd_enabled': mwdd_enabled,
            'mwdd_mcc_c': mwdd_mcc_c,
            'mwdd_mcc_w': mwdd_mcc_w,
            'mwdd_depth': mwdd_depth,
            'mwdd_dd_min': mwdd_dd_min,
            'mwdd_vix_panic': mwdd_vix_panic,
            'tvdd_enabled': tvdd_enabled,
            'tvdd_trin_c': tvdd_trin_c,
            'tvdd_trin_w': tvdd_trin_w,
            'tvdd_depth': tvdd_depth,
            'tvdd_dd_min': tvdd_dd_min,
            'tvdd_vix_panic': tvdd_vix_panic,
            'bdiv_enabled': bdiv_enabled,
            'bdiv_prox_cut': bdiv_prox_cut,
            'bdiv_prox_full': bdiv_prox_full,
            'bdiv_gap_c': bdiv_gap_c,
            'bdiv_gap_w': bdiv_gap_w,
            'bdiv_depth': bdiv_depth,
            'svr_enabled': svr_enabled,
            'svr_lo_cut': svr_lo_cut,
            'svr_lo_full': svr_lo_full,
            'svr_hi_full': svr_hi_full,
            'svr_hi_cut': svr_hi_cut,
            'svr_floor': svr_floor,
            'spread_tilt_enabled': spread_tilt_enabled,
            'spread_tilt_lo': spread_tilt_lo,
            'spread_tilt_hi': spread_tilt_hi,
            'spread_tilt_depth': spread_tilt_depth,
            'liquidity_floor':  liquidity_floor,
            'dte_strategy':     _dte_strategy,
            # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
            # Reflects shipped strategy_config; not user-tunable in the UI yet.
            # Uses _bcfg (the strategy_config dataclass), not the api-cfg dict.
            'ctsl_enabled':         _bcfg.CTSL_ENABLED,
            'ctsl_call_trend_max':  _bcfg.CTSL_CALL_TREND_MAX,
            'ctsl_call_target':     _bcfg.CTSL_CALL_TARGET,
            'ctsl_call_alpha':      _bcfg.CTSL_CALL_ALPHA,
            'ctsl_call_tier_floor': _bcfg.CTSL_CALL_TIER_FLOOR,
            'ctsl_put_trend_min':   _bcfg.CTSL_PUT_TREND_MIN,
            'ctsl_put_target':      _bcfg.CTSL_PUT_TARGET,
            'ctsl_put_alpha':       _bcfg.CTSL_PUT_ALPHA,
            'ctsl_put_tier_ceiling': _bcfg.CTSL_PUT_TIER_CEILING,
        }
        summary_payload = {
            'initial':       round(initial, 2),
            'final':         round(final, 2),
            'return_pct':    round((final / initial - 1) * 100, 2) if initial else 0,
            'max_dd':        round(max_dd * 100, 2),
            'n_trades':      n_tot,
            'n_calls':       len(calls),
            'n_puts':        len(puts),
            'n_tp':          n_tp,
            'n_sl':          n_sl,
            'n_hard':        n_hard,
            'tp_rate':       round(n_tp / n_tot * 100, 1) if n_tot else None,
            'call_tp_rate':  round(c_tp / len(calls) * 100, 1) if calls else None,
            'put_tp_rate':   round(p_tp / len(puts)  * 100, 1) if puts  else None,
            'be_calm':       round(be_calm * 100, 1),
            'be_stressed':   round(be_stressed * 100, 1),
            'be_put':        round(be_put * 100, 1),
            'n_stressed':    n_stressed,
            'stressed_pct':  round(n_stressed / n_tot * 100, 1) if n_tot else 0,
            'n_open':        len(open_holdings),
            'avg_open_premium_base_pct': round(result.get('avg_open_premium_base_pct', 0.0), 2),
            'max_open_premium_base_pct': round(result.get('max_open_premium_base_pct', 0.0), 2),
            'avg_call_open_premium_base_pct': round(result.get('avg_call_open_premium_base_pct', 0.0), 2),
            'avg_put_open_premium_base_pct': round(result.get('avg_put_open_premium_base_pct', 0.0), 2),
            'avg_saturation_scale': round(result.get('avg_saturation_scale', 1.0), 4),
        }
        open_holdings_out = [
            {
                'entry_date':     str(h['entry_date']),
                'hard_sell_date': str(h['hard_sell_date']) if h['hard_sell_date'] else None,
                'symbol':         h['symbol'],
                'score':          round(h['score'], 1),
                'tier':           h['tier'],
                'side':           h.get('side', 'call'),
                'premium':        round(h['premium'], 2),
                'entry_price':    round(h.get('entry_price', 0), 2),
                'current_price':  round(h.get('current_price', 0), 2) if h.get('current_price') else None,
                'tp_price':       round(h.get('tp_price', 0), 2),
                'sl_price':       round(h.get('sl_price', 0), 2),
                'current_score':  current_scores.get(h['symbol']),
                'sigma':          round(h['sigma'], 2),
                'hold_bars':      h['hold_bars'],
            }
            for h in open_holdings
        ]

        response_payload = {
            'params':         params_payload,
            'summary':        summary_payload,
            'equity_curve':   ec_out,
            'trade_log':      trades_out,
            'open_holdings':  open_holdings_out,
            'tier_stats':     tier_stats,
            'yearly':         temporal.get('yearly', []),
            'monthly':        temporal.get('monthly', []),
            'version':        _version_payload(ver, active_version),
            'active_version_id': active_version.id,
            'portfolio_profile': _portfolio_profile,
            'portfolio_profiles': _portfolio_profile_catalog,
            **_score_version_response_fields(version_catalog),
        }

        # ── Auto-save run (deterministic backtest → upsert on params_hash) ──
        # Run is fully reproducible per (version, dte_strategy, params), so the
        # same params re-run just refreshes run_at on the existing row.
        try:
            import hashlib, json as _json
            from database.models.core import BacktestRun
            BacktestRun.ensure_schema()
            # Canonical params: drop volatile/cosmetic fields (cad rate, version
            # display string) so identical strategy params hash identically
            # regardless of presentation. version_id + dte_strategy are
            # already part of the unique key, so they don't need to enter the hash.
            canon = {k: v for k, v in params_payload.items()
                     if k not in ('cad_usd_rate', 'version', 'dte_strategy')}
            params_hash = hashlib.md5(
                _json.dumps(canon, sort_keys=True, default=str).encode()
            ).hexdigest()
            response_blob = _json.dumps(response_payload, default=str)
            row, created = BacktestRun.get_or_create(
                version=ver, dte_strategy=_dte_strategy, params_hash=params_hash,
                defaults={
                    'params_json':       _json.dumps(canon, default=str),
                    'start_date':        from_date,
                    'end_date':          to_date,
                    'initial_capital':   float(initial),
                    'final_equity':      float(final),
                    'total_return_pct':  summary_payload['return_pct'],
                    'max_dd_pct':        summary_payload['max_dd'],
                    'n_trades':          int(n_tot),
                    'n_tp':              int(n_tp),
                    'n_sl':              int(n_sl),
                    'n_hard':            int(n_hard),
                    'n_open':            len(open_holdings),
                    'response_json':     response_blob,
                },
            )
            if not created:
                # Re-run with same params: refresh run_at + summary (in case
                # the underlying score/price data changed since last save).
                row.run_at            = datetime.now()
                row.params_json       = _json.dumps(canon, default=str)
                row.start_date        = from_date
                row.end_date          = to_date
                row.initial_capital   = float(initial)
                row.final_equity      = float(final)
                row.total_return_pct  = summary_payload['return_pct']
                row.max_dd_pct        = summary_payload['max_dd']
                row.n_trades          = int(n_tot)
                row.n_tp               = int(n_tp)
                row.n_sl               = int(n_sl)
                row.n_hard             = int(n_hard)
                row.n_open             = len(open_holdings)
                row.response_json     = response_blob
                row.save()
            response_payload['run_id']      = row.id
            response_payload['params_hash'] = params_hash
        except Exception as save_exc:
            # Auto-save is best-effort — never fail the request because of it.
            print(f'[backtest auto-save] {save_exc}')

        return jsonify(response_payload)

    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Saved backtest runs — browse / load / label / delete
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/backtest/runs', methods=['GET'])
def list_backtest_runs():
    """List saved backtest runs (summary fields only, no JSON blobs).

    Query params (all optional):
      version  - algorithm version id; default = active scores version
      dte      - '30' or '15'; default = both
      limit    - max rows (default 100)
    Returns rows sorted by run_at desc.
    """
    try:
        from database.models.core import AlgorithmVersion, BacktestRun
        BacktestRun.ensure_schema()

        ver_arg = request.args.get('version')
        if ver_arg:
            ver = _resolve_production_version(ver_arg)
            if ver is None:
                return jsonify({'error': f'Algorithm version {ver_arg} not found'}), 404
        else:
            ver = AlgorithmVersion.get_active_scores_version()

        dte = request.args.get('dte')
        if dte and dte not in ('30', '15'):
            dte = None

        try:
            limit = max(1, min(500, int(request.args.get('limit', 100))))
        except (ValueError, TypeError):
            limit = 100

        q = BacktestRun.select().where(BacktestRun.version == ver)
        if dte:
            q = q.where(BacktestRun.dte_strategy == dte)
        q = q.order_by(BacktestRun.run_at.desc()).limit(limit)

        # Pre-load version metadata for any version_ids we'll surface. Labels
        # use the production sequence, which skips inactive staging rows.
        version_ids = {r.version_id for r in q}
        ver_meta = {
            v.id: {
                'id':         v.id,
                'label':      v.production_label,
                'git_commit': v.git_commit,
            }
            for v in AlgorithmVersion.select().where(AlgorithmVersion.id.in_(list(version_ids)))
        } if version_ids else {}

        runs = []
        for r in q:
            try:
                r_params = json.loads(r.params_json or '{}')
            except Exception:
                r_params = {}
            runs.append({
                'id':               r.id,
                'version_id':       r.version_id,
                'version':          ver_meta.get(r.version_id, {'id': r.version_id, 'label': f'db:{r.version_id}', 'git_commit': None}),
                'dte_strategy':     r.dte_strategy,
                'portfolio_profile': r_params.get('portfolio_profile') or 'sentinel',
                'portfolio_profile_name': r_params.get('portfolio_profile_name') or 'Sentinel',
                'params_hash':      r.params_hash,
                'run_at':           r.run_at.isoformat() if r.run_at else None,
                'start_date':       str(r.start_date) if r.start_date else None,
                'end_date':         str(r.end_date) if r.end_date else None,
                'initial_capital':  r.initial_capital,
                'final_equity':     r.final_equity,
                'total_return_pct': r.total_return_pct,
                'max_dd_pct':       r.max_dd_pct,
                'n_trades':         r.n_trades,
                'n_tp':             r.n_tp,
                'n_sl':             r.n_sl,
                'n_hard':           r.n_hard,
                'n_open':           r.n_open,
                'label':            r.label,
            })
        return jsonify({
            'version_id':    ver.id,
            'active_version': {
                'id':         ver.id,
                'label':      ver.production_label,
                'git_commit': ver.git_commit,
            },
            'dte':           dte,
            'count':         len(runs),
            'runs':          runs,
        })
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/backtest/runs/<int:run_id>', methods=['GET'])
def get_backtest_run(run_id):
    """Return full saved backtest run (params + summary + equity curve + trades)."""
    try:
        from database.models.core import BacktestRun
        BacktestRun.ensure_schema()
        try:
            r = BacktestRun.get(BacktestRun.id == run_id)
        except Exception:
            return jsonify({'error': f'Run {run_id} not found'}), 404

        # Return the full saved response payload — same shape as the live
        # /api/backtest/run response, so the frontend can drop it into the
        # same state as a freshly-run backtest.
        payload = r.response()
        # Surface the row id + label + params_hash so the panel can locate it
        payload['run_id']      = r.id
        payload['run_at']      = r.run_at.isoformat() if r.run_at else None
        payload['label']       = r.label
        payload['params_hash'] = r.params_hash
        return jsonify(payload)
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/backtest/runs/<int:run_id>', methods=['DELETE'])
def delete_backtest_run(run_id):
    """Delete a saved backtest run."""
    try:
        from database.models.core import BacktestRun
        BacktestRun.ensure_schema()
        n = BacktestRun.delete().where(BacktestRun.id == run_id).execute()
        if n == 0:
            return jsonify({'error': f'Run {run_id} not found'}), 404
        return jsonify({'deleted': run_id})
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/backtest/runs/<int:run_id>/label', methods=['POST'])
def set_backtest_run_label(run_id):
    """Set/clear the user-provided label on a saved run.

    Body (JSON): {"label": "..."} — pass empty string or null to clear.
    """
    try:
        from database.models.core import BacktestRun
        BacktestRun.ensure_schema()
        try:
            r = BacktestRun.get(BacktestRun.id == run_id)
        except Exception:
            return jsonify({'error': f'Run {run_id} not found'}), 404

        body = request.get_json(silent=True) or {}
        label = body.get('label')
        if label is not None:
            label = str(label).strip()[:120] or None
        r.label = label
        r.save()
        return jsonify({'id': r.id, 'label': r.label})
    except Exception as exc:
        print(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@app.route('/api/queue/status', methods=['GET'])
def get_queue_status():
    """Live task-queue state for the dashboard: daemon health, resource budget +
    utilization, live machine CPU/RAM, and the active + recent task rows."""
    try:
        import time as _time

        from task_queue import api as queue_lib
        from task_queue.model import ACTIVE_STATES, TERMINAL_STATES, priority_name
        from task_queue.store import QueueStore

        st = QueueStore()
        try:
            base = queue_lib.status(store=st)
            active = st.list_tasks(states=list(ACTIVE_STATES))
            recent = st.list_tasks(states=list(TERMINAL_STATES), limit=25)
        finally:
            st.close()

        # Best-effort live RAM (RSS of each running task's supervisor process tree).
        live_ram = {}
        try:
            import psutil
            for t in active:
                if t.pid and t.state in ('running', 'suspended'):
                    try:
                        p = psutil.Process(int(t.pid))
                        rss = 0
                        for q in [p] + p.children(recursive=True):
                            try:
                                rss += q.memory_info().rss
                            except Exception:
                                pass
                        live_ram[t.id] = round(rss / (1024 * 1024), 1)
                    except Exception:
                        pass
        except Exception:
            pass

        def ser(t):
            return {
                'id': t.id,
                'state': t.state,
                'priority': t.priority,
                'priority_name': priority_name(t.priority),
                'cpu_request': t.cpu_request,
                'cpu_grant': t.cpu_grant,
                'db_class': t.db_class,
                'io_class': t.io_class,
                'throttled': bool(t.throttled),
                'held': bool(t.held),
                'pid': t.pid,
                'command': ' '.join(t.argv),
                'reason': t.reason,
                'created_by': t.created_by,
                'created_at': t.created_at,
                'started_at': t.started_at,
                'finished_at': t.finished_at,
                'attempts': t.attempts,
                'max_attempts': t.max_attempts,
                'exit_code': t.exit_code,
                'error': t.error,
                'dedup_key': t.dedup_key,
                'ram_mb': live_ram.get(t.id),
            }

        machine = {}
        try:
            import psutil
            vm = psutil.virtual_memory()
            machine = {
                'cpu_count': psutil.cpu_count(),
                'cpu_percent': psutil.cpu_percent(interval=0.1),
                'ram_total_gb': round(vm.total / (1024 ** 3), 1),
                'ram_used_gb': round(vm.used / (1024 ** 3), 1),
                'ram_percent': vm.percent,
            }
        except Exception:
            pass

        return jsonify({
            'daemon': base.get('daemon'),
            'resources': base.get('resources'),
            'counts': base.get('counts'),
            'machine': machine,
            'active': [ser(t) for t in active],
            'recent': [ser(t) for t in recent],
            'server_now': _time.time(),
        })
    except Exception as e:
        print(f"ERROR in /api/queue/status: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    DB.connect()
    from database.models.core import ScoreAssessmentMeta, ScoreAssessmentRun, ScoreAssessmentBandIC, BacktestRun
    DB.create_tables([EarningsDate, ScoreAssessmentMeta, ScoreAssessmentBandIC])
    Stock.ensure_schema()
    EarningsDate.ensure_schema()
    Score.ensure_schema()
    ScoreAssessmentRun.ensure_schema()
    from database.models.core import ScoreAssessmentResult
    ScoreAssessmentResult.ensure_schema()
    from database.models.options import Option, OptionPrice
    Option.ensure_schema()
    OptionPrice.ensure_schema()
    BacktestRun.ensure_schema()
    if not DB.is_closed():
        DB.close()
    app.run(debug=True, host='0.0.0.0', port=5000)
