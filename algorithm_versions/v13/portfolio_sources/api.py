#!/usr/bin/env python3
"""
Flask REST API for the Trader Program
Provides RESTful endpoints for stock data, technical indicators, scores, options, and portfolio management.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal
import json
import traceback
from peewee import fn

# Add the Trader directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Trader'))

from database.project_root import chdir_trader_project

chdir_trader_project()

from trader import Trader
from database import Stock, PriceHistory, Indicator, Score, Option, OptionPrice, Position, Trend, WeeklyPriceHistory, WeeklyIndicator, WeeklyScore, EarningsDate, DteRecommendation, HistoricPeak
from database.models.core import MarketRegime, MarketBreadth
from database.trader_database import DB
from dte_recommendation import get_signal_context, recommend_dte

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Initialize trader client
trader = Trader()


def get_api_score_version():
    """Same persisted-score version as recalculate / dashboard (see AlgorithmVersion.get_active_scores_version)."""
    from database.models.core import AlgorithmVersion
    return AlgorithmVersion.get_active_scores_version()


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
            .where(AlgorithmVersion.id < current_version.id)
            .order_by(AlgorithmVersion.id.desc())
            .first())


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
            'latest_score': latest_score.output_hash() if latest_score else None
        }

        stock_data['dte_recommendation'] = _load_dte(symbol.upper())

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
    """Get weekly technical indicators for a stock"""
    try:
        limit = request.args.get('limit', type=int, default=250)
        query = WeeklyIndicator.select().where(WeeklyIndicator.symbol == symbol.upper())
        indicators = query.order_by(WeeklyIndicator.date.desc()).limit(limit)
        
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
            })
        
        return jsonify({
            'symbol': symbol.upper(),
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
        version = get_api_score_version()
        target_date = Score.select(fn.MAX(Score.date)).where(Score.version == version).scalar()

        # If the current version has no scores yet, fall back entirely to the previous version.
        if not target_date:
            prev_v = _get_prev_version(version)
            if prev_v:
                version = prev_v
                target_date = Score.select(fn.MAX(Score.date)).where(Score.version == version).scalar()
            if not target_date:
                return jsonify({'error': 'No scores available'}), 404

        active_positions = set(Position.active_positions())

        _score_join_fields = (Score, Stock.name, Stock.symbol, Stock.pe, Stock.forward_pe,
                              Stock.eps, Stock.forward_eps, Stock.market_cap, Stock.flagged)

        scores_list = list(Score
                           .select(*_score_join_fields)
                           .join(Stock, on=(Score.symbol == Stock.symbol))
                           .where((Score.date == target_date) & (Score.version == version) &
                                  (Score.overall.is_null(False)))
                           .limit(limit))

        # Fill any symbols missing from the current version using the previous version.
        primary_symbols = {s.symbol.symbol for s in scores_list}
        prev_v = _get_prev_version(version)
        if prev_v and len(primary_symbols) < limit:
            prev_target = Score.select(fn.MAX(Score.date)).where(Score.version == prev_v).scalar()
            if prev_target:
                fallback = list(Score
                                .select(*_score_join_fields)
                                .join(Stock, on=(Score.symbol == Stock.symbol))
                                .where((Score.date == prev_target) & (Score.version == prev_v) &
                                       Score.overall.is_null(False) &
                                       Score.symbol.not_in(list(primary_symbols)))
                                .limit(limit - len(primary_symbols)))
                scores_list.extend(fallback)

        # Bulk-load persisted DTE rows for the active version on target_date
        # so the per-stock loop is one dict lookup, not 500 recomputes.
        dte_by_sym = {
            r.symbol_id: r for r in DteRecommendation.select().where(
                (DteRecommendation.version == version)
                & (DteRecommendation.date == target_date)
            )
        }

        result = []
        for score in scores_list:
            overall_score = score.overall
            default_sort_value = overall_score if overall_score >= 50 else (100 - overall_score)
            weekly_composite = score.output_hash().get('W')

            dte_thesis = dte_target = dte_min = dte_max = dte_confidence = None
            dte_tradeable = False
            dte_filter_side = None
            dte_row = dte_by_sym.get(score.symbol.symbol)
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
                        symbol=score.symbol.symbol,
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
                'symbol': score.symbol.symbol,
                'name': score.symbol.name,
                'overall_score': overall_score,
                'default_sort_value': default_sort_value,
                'weekly_composite': weekly_composite,
                'rsi_score': score.rsi,
                'macd_score': score.macd,
                'trend_score': score.trend,
                'bb_score': score.bb,
                'stoch_score': score.stoch,
                'ma20_score': score.ma20,
                'technical_alignment_score': score.technical_alignment,
                'current_price': float(score.price) if score.price else None,
                'daily_percentage_change': round(score.daily_change, 2),
                'market_cap': float(score.symbol.market_cap) if score.symbol.market_cap else None,
                'pe': float(score.symbol.pe) if score.symbol.pe else None,
                'forward_pe': float(score.symbol.forward_pe) if score.symbol.forward_pe else None,
                'eps': float(score.symbol.eps) if score.symbol.eps else None,
                'forward_eps': float(score.symbol.forward_eps) if score.symbol.forward_eps else None,
                'volume_score': score.volume,
                'volume_signal': score.volume_signal,
                'score_weights': json.loads(score.weight_info) if score.weight_info else None,
                'growth_score': float(score.growth_score) if score.growth_score else None,
                'price_target_growth': float(score.price_target_growth) if score.price_target_growth else None,
                'next_earnings': score.next_earnings,
                'updated_at': score.updated_at.isoformat() if score.updated_at else None,
                'flagged': score.symbol.flagged,
                'in_portfolio': score.symbol.symbol in active_positions,
                'dte_thesis': dte_thesis,
                'dte_target': dte_target,
                'dte_min': dte_min,
                'dte_max': dte_max,
                'dte_confidence': dte_confidence,
                'dte_tradeable': dte_tradeable,
                'dte_filter_side': dte_filter_side,
            })
        
        result.sort(key=lambda x: x['default_sort_value'], reverse=True)
        
        return jsonify({
            'stocks': result,
            'count': len(result),
            'date': target_date.isoformat()
        })
    
    except Exception as e:
        print(f"ERROR in /api/stocks/all: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/stocks/count', methods=['GET'])
def get_stocks_count():
    """Get total count of stocks in database and comprehensive statistics"""
    try:
        # Optimize: Get all counts in a single query using fn.COUNT
        stock_counts = Stock.select(
            fn.COUNT(Stock.symbol).alias('total_stocks'),
            fn.COUNT(Stock.revenue).alias('stocks_with_revenue')
        ).scalar()
        
        # Get the latest trend data efficiently
        latest_trend = Trend.select().order_by(Trend.date.desc()).first()
        if not latest_trend:
            return jsonify({'error': 'No trend data available'}), 404
            
        # Get previous day's trend data for comparison
        previous_trend = Trend.select().where(Trend.date < latest_trend.date).order_by(Trend.date.desc()).first()
        
        version = get_api_score_version()
        stocks_with_scores = Score.select().where(Score.date == latest_trend.date, Score.version == version).count()
        
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
            'total_stocks': stock_counts[0] if isinstance(stock_counts, tuple) else stock_counts,
            'stocks_with_revenue': stock_counts[1] if isinstance(stock_counts, tuple) else 0,
            'stocks_with_scores': stocks_with_scores,
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
        market_stocks = ['QQQ', 'SPY', 'SMH', 'SOXL', 'TNA']
        yesterday = latest_trend.date - timedelta(days=1)
        
        # Get current and previous prices for all market stocks in one query
        # Note: PriceHistory.symbol_id is the string column, not the foreign key object
        market_data = (PriceHistory
            .select(
                PriceHistory.symbol,
                PriceHistory.close,
                PriceHistory.date
            )
            .where(
                (PriceHistory.symbol_id.in_(market_stocks)) &
                (PriceHistory.date.in_([latest_trend.date, yesterday]))
            )
            .order_by(PriceHistory.symbol, PriceHistory.date.desc())
        )
        
        # Group by symbol and calculate changes
        index_changes = []
        market_prices = {}
        
        for ph in market_data:
            symbol = ph.symbol_id  # Direct access to the symbol string
            if symbol not in market_prices:
                market_prices[symbol] = {}
            market_prices[symbol][ph.date] = float(ph.close)
        
        for stock_symbol in market_stocks:
            if stock_symbol in market_prices:
                current_price = market_prices[stock_symbol].get(latest_trend.date)
                prev_price = market_prices[stock_symbol].get(yesterday)
                
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

        def serialize(row):
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
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            }

        if days == 1:
            return jsonify(serialize(rows[0]))
        return jsonify({'history': [serialize(r) for r in reversed(rows)]})
    except Exception as e:
        print(f"ERROR in /api/market/breadth: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/market/historic-peaks', methods=['GET'])
def get_historic_peaks():
    """Return cached historic peak scores (≥80 or ≤20) per stock over last 60 days.

    Each stock appears once — the single most extreme qualifying score in the window.
    Includes sub-scores at peak, current price, price change since peak, and any
    mixed-signal (opposite direction threshold crossed after the peak).
    """
    try:
        HistoricPeak.ensure_schema()
        today = date.today()

        rows = list(
            HistoricPeak.select(HistoricPeak, Stock)
            .join(Stock, on=(HistoricPeak.symbol == Stock.symbol))
            .order_by(HistoricPeak.peak_score.desc())
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
                'win_1d':  row.win_1d,
                'win_7d':  row.win_7d,
                'win_15d': row.win_15d,
                'win_30d': row.win_30d,
                'win_60d': row.win_60d,
                'win_90d': row.win_90d,
                'mixed_signal_date': row.mixed_signal_date.isoformat() if row.mixed_signal_date else None,
                'mixed_signal_score': row.mixed_signal_score,
                'mixed_signal_days_after': row.mixed_signal_days_after,
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            })

        return jsonify({'peaks': result, 'count': len(result)})

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

    Response shape:
      {
        version: { id, git_commit, git_message, run_count, total_peaks, updated_at, correlations: {1d..150d} },
        buckets: [ { bucket, side, sample_count, avg_score,
                     win_rate: {1d..150d}, avg_return: {..}, avg_peak: {..},
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
        version = AlgorithmVersion.get_active_scores_version()
        if not version:
            return jsonify({'error': 'No active algorithm version found'}), 404

        meta_rows = list(
            ScoreAssessmentMeta.select()
            .where(ScoreAssessmentMeta.version == version)
        )
        if not meta_rows:
            return jsonify({'error': 'No assessment data for active version'}), 404

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

        # ScoreAssessmentMeta only has columns up to 90d; 150d lives on ScoreAssessmentResult.
        PERIODS = ['1d', '7d', '15d', '30d', '60d', '90d', '150d']
        META_PERIODS = ['1d', '7d', '15d', '30d', '60d', '90d']  # columns actually on Meta model
        SELL_BUCKETS = {'<25', '<15', '<5'}

        # Version info from any meta row (they share run-level fields)
        m0 = meta_rows[0]
        correlations = {p: fg(m0, f'correlation_{p}', 4) for p in PERIODS}
        version_info = {
            'id': version.id,
            'git_commit': version.git_commit,
            'git_message': version.git_message if version.git_message else None,
            'run_count': m0.run_count,
            'total_peaks': m0.total_peaks,
            'updated_at': m0.updated_at.isoformat() if m0.updated_at else None,
            'correlations': correlations,
        }

        # Order buckets: calls first (descending threshold), then puts (ascending)
        BUCKET_ORDER = ['99+', '95+', '90+', '85+', '80+', '75+', '70+', '<25', '<15', '<5']
        meta_by_bucket = {r.bucket: r for r in meta_rows}

        buckets = []
        for bucket_key in BUCKET_ORDER:
            r = meta_by_bucket.get(bucket_key)
            if not r or (r.sample_count or 0) == 0:
                continue
            side = 'put' if bucket_key in SELL_BUCKETS else 'call'
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
                'capture_ratio': {p: fg(r, f'capture_ratio_{p}', 3) for p in META_PERIODS},
                'avg_mae_winner_30d': fg(r, 'avg_mae_winner_30d'),
                'avg_mae_loser_30d': fg(r, 'avg_mae_loser_30d'),
                'shakeout_depth': fg(r, 'shakeout_depth'),
                'shakeout_recovery': getattr(r, 'shakeout_recovery', None),
            })

        # Aggregate band ICs across all runs for this version
        runs = list(ScoreAssessmentRun.select().where(ScoreAssessmentRun.version == version))
        run_ids = [r.id for r in runs]
        band_ics = []
        if run_ids:
            all_bic = list(
                ScoreAssessmentBandIC.select()
                .where(ScoreAssessmentBandIC.run.in_(run_ids))
            )
            IC_PERIODS = ['1d', '7d', '15d', '30d', '60d', '90d']
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
        BUY_THRESHOLDS  = [99, 95, 90, 85, 80, 75, 70]
        SELL_THRESHOLDS = [25, 15, 5]

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
                if not bkt_rows:
                    continue
                n    = sum(r.sample_count for r in bkt_rows)
                side = 'put' if bkt_key in SELL_BUCKETS else 'call'
                floor_w = _PUT_FLOOR if side == 'put' else _CALL_FLOOR
                wr   = {p: _wavg(bkt_rows, f'win_rate_{p}') for p in META_PERIODS}
                wr_u = {p: _wavg(bkt_rows, f'win_rate_unscaled_{p}') for p in META_PERIODS}
                win_bkts.append({
                    'bucket':            bkt_key,
                    'side':              side,
                    'sample_count':      n,
                    'win_rate':          wr,
                    'win_rate_unscaled': wr_u,
                    'rtr_win_rate':      {p: _rtr(wr[p], floor_w) for p in META_PERIODS},
                    'avg_return':        {p: _wavg(bkt_rows, f'avg_return_{p}') for p in META_PERIODS},
                })

            windows.append({
                'label':        label,
                'lookback_days': lb,
                'total_peaks':  total_pk,
                'run_count':    len(lb_runs),
                'buckets':      win_bkts,
            })

        return jsonify({
            'version': version_info,
            'buckets': buckets,
            'band_ics': band_ics,
            'windows': windows,
        })
    except Exception as e:
        print(f"ERROR in /api/assessment: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404

if __name__ == '__main__':
    DB.connect()
    from database.models.core import ScoreAssessmentMeta, ScoreAssessmentRun, ScoreAssessmentBandIC
    DB.create_tables([EarningsDate, ScoreAssessmentMeta, ScoreAssessmentBandIC])
    EarningsDate.ensure_schema()
    Score.ensure_schema()
    ScoreAssessmentRun.ensure_schema()
    from database.models.core import ScoreAssessmentResult
    ScoreAssessmentResult.ensure_schema()
    from database.models.options import Option, OptionPrice
    Option.ensure_schema()
    OptionPrice.ensure_schema()
    app.run(debug=True, host='0.0.0.0', port=5000) 
