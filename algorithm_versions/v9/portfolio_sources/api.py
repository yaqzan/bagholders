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
from database import Stock, PriceHistory, Indicator, Score, Option, OptionPrice, Position, Trend, WeeklyPriceHistory, WeeklyIndicator, WeeklyScore, EarningsDate, DteRecommendation
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

@app.route('/api/market/trends', methods=['GET'])
def get_market_trends():
    """
    Market trends time-series — joins Trend (score distribution), MarketBreadth
    (price/volume breadth indicators), and MarketRegime (macro context) by date.

    Query params:
        days  int  default=90   How many calendar days of history to return.

    Response shape per day entry:
        score_distribution  — how our universe is distributed across score bands
        price_breadth       — advancing/declining issues, volume flow, new highs/lows
        momentum            — McClellan, Zweig, cumulative A-D line
        regime              — VIX, SPY trend, composite regime score
        signals             — rare event flags (Zweig thrust, Hindenburg omen)
    """
    try:
        days = request.args.get('days', type=int, default=90)
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # ── Pull all three tables in one pass each, index by date ──────────────
        trend_rows = {
            r.date: r for r in
            Trend.select().where(Trend.date.between(start_date, end_date))
        }
        breadth_rows = {
            r.date: r for r in
            MarketBreadth.select().where(MarketBreadth.date.between(start_date, end_date))
        }
        regime_rows = {
            r.date: r for r in
            MarketRegime.select().where(MarketRegime.date.between(start_date, end_date))
        }

        # Union of all dates that appear in at least one table
        all_dates = sorted(
            trend_rows.keys() | breadth_rows.keys() | regime_rows.keys()
        )

        def pct(num, denom, dp=1):
            if not num or not denom:
                return None
            return round(num / denom * 100, dp)

        def f(v, dp=2):
            return round(float(v), dp) if v is not None else None

        def regime_label(composite):
            if composite is None:
                return None
            if composite >= 75:
                return 'BULL'
            if composite >= 60:
                return 'HEALTHY'
            if composite >= 45:
                return 'NEUTRAL'
            if composite >= 30:
                return 'CAUTION'
            return 'STRESS'

        entries = []
        for d in all_dates:
            tr = trend_rows.get(d)
            br = breadth_rows.get(d)
            re = regime_rows.get(d)

            total = tr.total_stocks if tr else None

            # ── Score distribution (from Trend cache) ──────────────────────
            score_dist = None
            if tr:
                ob75 = tr.over_75_count or 0
                ob80 = tr.over_80_count or 0
                os25 = tr.under_25_count or 0
                os20 = tr.under_20_count or 0
                score_dist = {
                    # avg_score: mean daily score across all tracked stocks (0–100,
                    # 50 = neutral; ≥75 = strong call setup; ≤25 = put setup)
                    'avg_score': f(tr.avg_score),
                    'total_stocks': total,

                    # stocks_up / stocks_down: count with positive / negative daily price change
                    'stocks_up': tr.stocks_up,
                    'stocks_down': tr.stocks_down,
                    'stocks_neutral': tr.stocks_neutral,
                    'adv_pct': pct(tr.stocks_up, total),
                    'dec_pct': pct(tr.stocks_down, total),

                    # overbought_75/80: stocks with score ≥75/80 = strong call setups
                    # (high score = bullish directional signal: bounce, breakout, or trend continuation)
                    'overbought_75': ob75,
                    'overbought_80': ob80,
                    'overbought_75_pct': pct(ob75, total),
                    'overbought_80_pct': pct(ob80, total),

                    # oversold_25/20: stocks with score ≤25/20 = put setups
                    # (low score = bearish directional signal: breakdown, reversal, or downtrend continuation)
                    'oversold_25': os25,
                    'oversold_20': os20,
                    'oversold_25_pct': pct(os25, total),
                    'oversold_20_pct': pct(os20, total),

                    # skew: positive = more call setups than put setups (market broadly bullish)
                    # negative = put-setup dominance (broad weakness / broad bearish signal)
                    'signal_skew': (ob75 - os25) if (ob75 is not None and os25 is not None) else None,
                }

            # ── Price breadth (from MarketBreadth) ─────────────────────────
            price_breadth = None
            if br:
                adv = br.advancing
                dec = br.declining
                tot_issues = br.total_issues
                price_breadth = {
                    # advancing/declining: raw count of stocks that closed up/down vs prior day
                    'advancing': adv,
                    'declining': dec,
                    'unchanged': br.unchanged,
                    'adv_pct': pct(adv, tot_issues),

                    # ad_diff: single-day net (advancing minus declining);
                    # positive = more stocks participated in an up move
                    'ad_diff': br.ad_diff,

                    # ad_line: cumulative sum of daily A-D diff going back to the start of
                    # our data.  Rising line = broad participation; divergence from price
                    # = warning sign
                    'ad_line': f(br.ad_line),

                    # new_highs_52w / new_lows_52w: stocks within 3% of their 52-week
                    # high or low.  Healthy bull markets have highs >> lows.
                    'new_highs_52w': br.new_highs_52w,
                    'new_lows_52w': br.new_lows_52w,
                    'hl_ratio': (
                        round(br.new_highs_52w / (br.new_lows_52w or 1), 2)
                        if br.new_highs_52w is not None else None
                    ),

                    # trin (Arms Index): (A/D ratio) ÷ (advancing vol / declining vol).
                    # <1.0 = bullish (volume flowing into winners), >1.0 = bearish.
                    # Extreme <0.5 = panic buying; extreme >2.0 = capitulation selling.
                    'trin': f(br.trin, 4),

                    # pct_above_ema50/200: what fraction of the universe is trading above
                    # its 50 or 200-day EMA.  >60% EMA50 = healthy trend backdrop.
                    'pct_above_ema50': f(br.pct_above_ema50),
                    'pct_above_ema200': f(br.pct_above_ema200),
                }

            # ── Momentum indicators (from MarketBreadth) ───────────────────
            momentum = None
            if br:
                momentum = {
                    # mcclellan_oscillator: EMA19(A-D) − EMA39(A-D).  Acts like an
                    # RSI for the whole market.  >0 = short-term breadth expanding;
                    # <0 = contracting.  Extreme readings (±100) often precede turns.
                    'mcclellan_oscillator': f(br.mcclellan_oscillator, 1),

                    # mcclellan_summation: running total of the oscillator.
                    # Think of it as a slow-moving trend of market health.
                    # Crossing above/below ±500 is a meaningful regime shift signal.
                    'mcclellan_summation': f(br.mcclellan_summation, 0),

                    # ema10_ad_ratio: 10-day EMA of advancing / (advancing + declining).
                    # The raw input for the Zweig Breadth Thrust.
                    # 0.615 = 61.5% threshold; above this = strong internal momentum.
                    'ema10_ad_ratio': f(br.ema10_ad_ratio, 4),

                    # breadth_score: composite 0–100 fed into regime engine.
                    # Combines TRIN, McClellan, new H/L, % above EMAs, and event flags.
                    'breadth_score': f(br.breadth_score),
                }

            # ── Macro regime (from MarketRegime) ───────────────────────────
            regime = None
            if re:
                spy_vs_ema50 = (
                    round((float(re.spy_close) - float(re.spy_ema50)) / float(re.spy_ema50) * 100, 2)
                    if re.spy_close and re.spy_ema50 else None
                )
                spy_vs_ema200 = (
                    round((float(re.spy_close) - float(re.spy_ema200)) / float(re.spy_ema200) * 100, 2)
                    if re.spy_close and re.spy_ema200 else None
                )
                comp = f(re.regime_composite)
                regime = {
                    # regime_composite: weighted blend of internal breadth, market
                    # breadth, VIX, and SPY trend.  Drives the per-stock score multiplier.
                    'composite': comp,
                    'label': regime_label(comp),

                    # regime_multiplier: what the composite translates to as a score scalar.
                    # 0.70 (STRESS) to 1.10 (BULL).  Applied symmetrically around 50.
                    'multiplier': f(re.regime_multiplier, 4),

                    # vix_close: CBOE Volatility Index.  <20 = calm, 20-30 = elevated,
                    # >30 = fear.  Used to scale down signal confidence in stressed markets.
                    'vix_close': f(re.vix_close),

                    # vix_10d_change: rolling 10-day % change in VIX.
                    # Positive = fear rising; negative = fear unwinding (often bullish for mean-rev).
                    'vix_10d_change_pct': f(re.vix_10d_change),

                    # SPY position relative to its EMAs — the market-trend sub-score inputs.
                    # Positive = SPY trading above the EMA (healthy), negative = below (stressed).
                    'spy_vs_ema50_pct': spy_vs_ema50,
                    'spy_vs_ema200_pct': spy_vs_ema200,

                    # Sub-scores that fed into the composite that day (each 0-100)
                    'sub_scores': {
                        'internal_breadth': f(re.internal_breadth_score),
                        'vix': f(re.vix_score),
                        'market_trend': f(re.market_trend_score),
                    },
                }

            # ── Rare event signals ──────────────────────────────────────────
            signals = None
            if br:
                signals = {
                    # zweig_thrust_active: Zweig Breadth Thrust fired — 10-day EMA of
                    # A/(A+D) moved from <40% to >61.5% within 10 days.  Historically
                    # one of the most reliable bull-market initiation signals (~17 days avg).
                    'zweig_thrust_active': bool(br.zweig_thrust_active),
                    'zweig_thrust_date': (
                        br.zweig_thrust_date.isoformat() if br.zweig_thrust_date else None
                    ),

                    # hindenburg_omen: rare warning signal — simultaneous new highs AND
                    # new lows both exceed 2.2% of issues while McClellan is negative.
                    # A single omen is noise; confirmed (2+ in 30 days) raises caution.
                    'hindenburg_omen': bool(br.hindenburg_omen),
                    'hindenburg_confirmed': bool(br.hindenburg_confirmed),
                }

            entries.append({
                'date': d.isoformat(),
                'score_distribution': score_dist,
                'price_breadth': price_breadth,
                'momentum': momentum,
                'regime': regime,
                'signals': signals,
            })

        # ── Summary: latest available data across each section ──────────────
        def latest_section(key):
            for entry in reversed(entries):
                if entry.get(key):
                    return entry[key]
            return None

        summary = {
            'as_of': entries[-1]['date'] if entries else None,
            'score_distribution': latest_section('score_distribution'),
            'price_breadth': latest_section('price_breadth'),
            'momentum': latest_section('momentum'),
            'regime': latest_section('regime'),
            'signals': latest_section('signals'),
        }

        return jsonify({
            'trends': entries,
            'summary': summary,
            'meta': {
                'days_requested': days,
                'entries': len(entries),
                'date_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat(),
                },
            },
        })

    except Exception as e:
        print(f"ERROR in /api/market/trends: {str(e)}")
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

        for row in rows:
            idx = date_to_idx.get(row.date)
            if idx is None:
                continue
            timing_key = get_timing_key(row.call_time)
            earnings_by_day[idx][timing_key].append({
                'symbol': row.symbol.symbol,
                'name': row.symbol.name,
                'flagged': row.symbol.flagged
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
