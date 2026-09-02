import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { parseLocalDate } from '../utils/timeUtils';

// API base URL - can be configured
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';

// Determine if we're running locally or in production
const isLocalDevelopment = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

// API path prefix - local development uses /api/, production uses no prefix
const API_PATH_PREFIX = '/api';
const CALL_SIGNAL_MIN = 70;
const CALL_STRONG_MIN = 75;
const PUT_SIGNAL_MAX = 25;

// Helper function to build API URLs
const buildApiUrl = (endpoint) => {
  const sep = endpoint.includes('?') ? '&' : '?';
  const bucket = Math.floor(Date.now() / 30000);
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}${sep}_t=${bucket}`;
};

const PriceChart = ({
  symbol,
  stockName,
  onHoverIndex,
  hoveredIndex,
  scores,
  stockDetail,
  onLoad,
  onData,
  projections,
  externalScroll = false,
  height,
  refreshNonce = 0,
  historyLimit = 250,
  showInlineTooltip = true,
}) => {
  const [priceData, setPriceData] = useState(null);
  const [earningsDates, setEarningsDates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [containerDimensions, setContainerDimensions] = useState({ width: 1400, height: 400 });
  const [hoveredConeKey, setHoveredConeKey] = useState(null);
  const hasCalledOnLoad = React.useRef(false);
  const containerRef = React.useRef(null);
  const conesRef = React.useRef([]);

  useEffect(() => {
    let cancelled = false;
    const fetchPriceData = async () => {
      try {
        setLoading(true);
        setError(null);
        hasCalledOnLoad.current = false;

        const [priceRes, earningsRes] = await Promise.all([
          axios.get(buildApiUrl(`/stocks/${symbol}/price-history?limit=${historyLimit}`)),
          axios.get(buildApiUrl(`/stocks/${symbol}/earnings-dates`)).catch(() => ({ data: { dates: [] } }))
        ]);

        if (cancelled) return;

        if (priceRes.data && priceRes.data.price_history) {
          const sortedData = priceRes.data.price_history.sort((a, b) =>
            parseLocalDate(a.date) - parseLocalDate(b.date)
          );
          setPriceData(sortedData);
          onData?.(sortedData);
        } else {
          setPriceData([]);
          onData?.([]);
        }
        setEarningsDates(earningsRes.data?.dates || []);
      } catch (err) {
        if (cancelled) return;
        console.error('Error fetching price data:', err);
        setError(err.message);
        setPriceData([]);
        onData?.([]);
      } finally {
        if (cancelled) return;
        setLoading(false);
        if (onLoad && !hasCalledOnLoad.current) {
          hasCalledOnLoad.current = true;
          onLoad();
        }
      }
    };

    if (symbol) {
      fetchPriceData();
    }
    return () => { cancelled = true; };
  }, [symbol, onLoad, onData, refreshNonce, historyLimit]);

  // Handle window resize to keep charts aligned
  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        const w = containerRef.current.offsetWidth;
        const h = containerRef.current.offsetHeight;
        setContainerDimensions(prev => (prev.width === w && prev.height === h ? prev : { width: w, height: h }));
      }
    };

    // Small delay to ensure container has rendered
    const timer = setTimeout(updateDimensions, 100);
    updateDimensions();
    
    window.addEventListener('resize', updateDimensions);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', updateDimensions);
    };
  }, [priceData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-trading-blue-500"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-trading-red-400">
        <div className="text-center">
          <p className="mb-2">Error loading price data:</p>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (!priceData || priceData.length === 0) {
    return (
      <div className="h-80 flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 bg-trading-blue-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-trading-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-white mb-2">No Price Data</h3>
          <p className="text-gray-400 text-sm">
            No price history available for {symbol}
          </p>
        </div>
      </div>
    );
  }

  // On mobile (<640px), render a wider fixed-width chart so candles aren't
  // compressed.  Use window.innerWidth rather than containerDimensions.width so
  // this stays true even when the chart is nested inside StockDetail's 1800 px
  // single-scroll wrapper (where containerDimensions.width would read 1800).
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640;
  const MOBILE_CHART_W = 1800; // wide enough for ~4px candles on 250-bar chart
  const MOBILE_PRICE_DOMAIN_POINTS = 22;

  // Create candlestick chart using SVG
  // Use viewBox that matches container width so text renders 1:1 on narrow screens.
  // On desktops we still cap at 1400 to preserve candle spacing over 250 days.
  const viewBoxWidth = isMobile
    ? MOBILE_CHART_W
    : Math.max(320, Math.min(1400, containerDimensions.width || 1400));
  // Scale oversized watermark text relative to the 1400-wide desktop reference.
  const chartScale = viewBoxWidth / 1400;
  // Aspect ratio uses the actual SVG render width (MOBILE_CHART_W on mobile, container
  // width on desktop) so the viewBox height matches the physical pixel height exactly
  // and nothing gets stretched vertically.
  const svgRenderWidth = isMobile ? MOBILE_CHART_W : (containerDimensions.width || 1400);
  const containerAspectRatio = svgRenderWidth / (containerDimensions.height || 400);
  const viewBoxHeight = viewBoxWidth / containerAspectRatio;
  const padding = 60; // Left padding for axis labels
  
  // Calculate future padding to match other charts (65 future days)
  const futureDays = 65;
  const totalDataPoints = priceData.length + futureDays;
  const dataPointsRatio = priceData.length / totalDataPoints; // Proportion of actual data vs total
  const rightPadding = (viewBoxWidth - padding) * (1 - dataPointsRatio); // Dynamic padding for future projection
  
  const chartWidth = viewBoxWidth;
  const chartHeight = viewBoxHeight;
  
  const dates = priceData.map(d => parseLocalDate(d.date));
  
  // On mobile, scale to the most recent trading month so the current candles
  // stay readable in the compact installed-app viewport.
  const priceDomainData = isMobile ? priceData.slice(-MOBILE_PRICE_DOMAIN_POINTS) : priceData;
  const priceDomainValues = priceDomainData
    .flatMap(d => [Number(d.high), Number(d.low)])
    .filter(Number.isFinite);
  const fallbackPriceValues = priceData
    .flatMap(d => [Number(d.high), Number(d.low)])
    .filter(Number.isFinite);
  const domainValues = priceDomainValues.length > 0
    ? priceDomainValues
    : (fallbackPriceValues.length > 0 ? fallbackPriceValues : [0, 1]);
  const minPrice = Math.min(...domainValues);
  const maxPrice = Math.max(...domainValues);
  const priceRange = maxPrice > minPrice
    ? maxPrice - minPrice
    : Math.max(Math.abs(maxPrice) * 0.01, 0.01);

  // Add some padding to the price range for better visualization
  const pricePadding = priceRange * 0.05;
  const paddedMinPrice = minPrice - pricePadding;
  const paddedMaxPrice = maxPrice + pricePadding;
  const paddedPriceRange = paddedMaxPrice - paddedMinPrice;
  
  const chartAreaWidth = chartWidth - padding - rightPadding;
  const chartAreaHeight = chartHeight - 2 * padding;

  // Reserve a fixed slab at the bottom of the chart for the volume overlay so
  // the volume footprint stays constant; any extra container height flows into
  // the price candles instead of scaling volume up with it.
  const volumeZoneHeight = isMobile ? 56 : 120;
  const volumeGap = isMobile ? 6 : 10;
  const priceZoneHeight = chartAreaHeight - volumeZoneHeight - volumeGap;
  const priceBottom = chartHeight - padding - volumeZoneHeight - volumeGap;

  const candleWidth = Math.max(2, Math.min(12, chartAreaWidth / priceData.length * 0.7));
  const candleSpacing = chartAreaWidth / priceData.length;

  const yScale = priceZoneHeight / paddedPriceRange;

  const handlePointerSelect = (event) => {
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();

    // Get mouse position relative to SVG
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;

    // Account for SVG viewBox scaling
    const scaleX = chartWidth / rect.width;
    const scaleY = chartHeight / rect.height;
    const scaledMouseX = mouseX * scaleX;
    const scaledMouseY = mouseY * scaleY;

    // Calculate which candle we're hovering over
    const relativeX = scaledMouseX - padding;
    const index = Math.floor(relativeX / candleSpacing);

    if (index >= 0 && index < priceData.length) {
      onHoverIndex(index);
    } else {
      onHoverIndex(null);
    }

    // Point-in-polygon hit test against cone vertices (ray-casting).
    // Cones have pointerEvents="none" so we detect via stored geometry.
    // Skip when hovering the most recent candle so its OHLC tooltip reads cleanly.
    const onLatestCandle = index === priceData.length - 1;
    const cones = conesRef.current;
    let matchedKey = null;
    if (!onLatestCandle) {
      for (let i = 0; i < cones.length; i++) {
        const verts = cones[i].vertices;
        if (!verts || verts.length < 3) continue;
        let inside = false;
        for (let a = 0, b = verts.length - 1; a < verts.length; b = a++) {
          const [xa, ya] = verts[a];
          const [xb, yb] = verts[b];
          const intersects = ((ya > scaledMouseY) !== (yb > scaledMouseY)) &&
            (scaledMouseX < ((xb - xa) * (scaledMouseY - ya)) / ((yb - ya) || 1e-9) + xa);
          if (intersects) inside = !inside;
        }
        if (inside) { matchedKey = cones[i].key; break; }
      }
    }
    setHoveredConeKey(prev => (prev === matchedKey ? prev : matchedKey));
  };

  const handleMouseLeave = () => {
    if (!isMobile) {
      onHoverIndex(null);
    }
    setHoveredConeKey(null);
  };

  // Helper to get Y coordinate for a price
  const getY = (price) => {
    return priceBottom - (price - paddedMinPrice) * yScale;
  };

  // Calculate Bollinger Bands (20-period SMA with 2 standard deviations)
  const calculateBollingerBands = () => {
    const period = 20;
    const multiplier = 2;
    const bands = [];

    for (let i = 0; i < priceData.length; i++) {
      if (i < period - 1) {
        bands.push({ middle: null, upper: null, lower: null });
        continue;
      }

      // Calculate SMA for the period
      let sum = 0;
      for (let j = 0; j < period; j++) {
        sum += priceData[i - j].close;
      }
      const sma = sum / period;

      // Calculate standard deviation
      let squaredDiffSum = 0;
      for (let j = 0; j < period; j++) {
        const diff = priceData[i - j].close - sma;
        squaredDiffSum += diff * diff;
      }
      const stdDev = Math.sqrt(squaredDiffSum / period);

      bands.push({
        middle: sma,
        upper: sma + (multiplier * stdDev),
        lower: sma - (multiplier * stdDev)
      });
    }

    return bands;
  };

  const bollingerBands = calculateBollingerBands();

  // Place watermark on the left side, choosing top vs bottom based on where the
  // oldest (~1 year ago) price sits relative to the year's range. If the oldest
  // price is closer to the low, candles start low on the left → watermark goes
  // top-left (away from the data). If the oldest price is closer to the high,
  // watermark goes bottom-left.
  const findBestWatermarkCorner = () => {
    if (!priceData || priceData.length === 0) return 'top-left';
    const oldestPrice = priceData[0].close ?? priceData[0].open;
    if (oldestPrice == null) return 'top-left';
    const oldestPosition = (oldestPrice - paddedMinPrice) / paddedPriceRange;
    return oldestPosition < 0.5 ? 'top-left' : 'bottom-left';
  };

  const watermarkCorner = findBestWatermarkCorner();

  // Get watermark position based on corner
  const getWatermarkPosition = () => {
    const symbolSize = 120 * chartScale;
    const nameSize = 24 * chartScale;
    const margin = 40 * chartScale;
    
    switch (watermarkCorner) {
      case 'top-left':
        return {
          symbolX: padding + margin,
          symbolY: padding + symbolSize,
          nameX: padding + margin,
          nameY: padding + symbolSize + nameSize + 10,
          anchor: 'start'
        };
      case 'top-right':
        return {
          symbolX: chartWidth - rightPadding - margin,
          symbolY: padding + symbolSize,
          nameX: chartWidth - rightPadding - margin,
          nameY: padding + symbolSize + nameSize + 10,
          anchor: 'end'
        };
      case 'bottom-left':
        return {
          symbolX: padding + margin,
          symbolY: priceBottom - nameSize - 20,
          nameX: padding + margin,
          nameY: priceBottom - 10,
          anchor: 'start'
        };
      case 'bottom-right':
        return {
          symbolX: chartWidth - rightPadding - margin,
          symbolY: priceBottom - nameSize - 20,
          nameX: chartWidth - rightPadding - margin,
          nameY: priceBottom - 10,
          anchor: 'end'
        };
      default:
        return {
          symbolX: padding + margin,
          symbolY: padding + symbolSize,
          nameX: padding + margin,
          nameY: padding + symbolSize + nameSize + 10,
          anchor: 'start'
        };
    }
  };

  const watermarkPos = getWatermarkPosition();

  // Create a map of dates to scores for efficient lookup
  const scoreMap = {};
  const volumeMap = {};
  if (scores && scores.length > 0) {
    scores.forEach(score => {
      const dateKey = score.date.split('T')[0];
      scoreMap[dateKey] = score.overall;
      if (score.volume != null) {
        volumeMap[dateKey] = { vol: score.volume, signal: score.volume_signal };
      }
    });
  }

  // Identify opportunity zones (calls start at 70; 70-74 remains transitional)
  const opportunityZones = [];
  priceData.forEach((candle, index) => {
    const dateKey = candle.date.split('T')[0];
    const score = scoreMap[dateKey];
    
    if (score !== undefined) {
      if (score >= CALL_SIGNAL_MIN) {
        opportunityZones.push({ index, type: 'call', score });
      } else if (score <= PUT_SIGNAL_MAX) {
        opportunityZones.push({ index, type: 'put', score });
      }
    }
  });

  // Identify strong volume signal days (>70 bullish, <30 bearish)
  const volumeMarkers = [];
  priceData.forEach((candle, index) => {
    const dateKey = candle.date.split('T')[0];
    const vData = volumeMap[dateKey];
    if (!vData) return;
    if (vData.vol >= 70) {
      volumeMarkers.push({ index, type: 'bullish', vol: vData.vol, signal: vData.signal });
    } else if (vData.vol <= 30) {
      volumeMarkers.push({ index, type: 'bearish', vol: vData.vol, signal: vData.signal });
    }
  });

  // Calculate month dividers (historical + future)
  const monthDividers = [];
  let lastMonth = null;
  const currentYear = new Date().getFullYear();
  
  // Add historical month dividers
  priceData.forEach((candle, index) => {
    const date = parseLocalDate(candle.date);
    const monthKey = `${date.getFullYear()}-${date.getMonth()}`;
    
    if (lastMonth !== monthKey && index > 0) {
      const x = padding + index * candleSpacing;
      const isCurrentYear = date.getFullYear() === currentYear;
      
      // Only show year if it's not the current year
      const label = isCurrentYear 
        ? date.toLocaleDateString('en-US', { month: 'short' })
        : date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
      
      monthDividers.push({
        x,
        index,
        label,
        isFuture: false
      });
    }
    lastMonth = monthKey;
  });
  
  // Add future month dividers (next 3 months)
  const lastDate = parseLocalDate(priceData[priceData.length - 1].date);
  const endOfDataX = padding + priceData.length * candleSpacing;
  
  // Calculate average historical month width from actual data
  const historicalMonthDividers = monthDividers.filter(d => !d.isFuture);
  let avgMonthWidth = 100; // Default fallback
  if (historicalMonthDividers.length >= 2) {
    const widths = [];
    for (let i = 1; i < historicalMonthDividers.length; i++) {
      widths.push(historicalMonthDividers[i].x - historicalMonthDividers[i-1].x);
    }
    avgMonthWidth = widths.reduce((a, b) => a + b, 0) / widths.length;
  }
  
  // Calculate how far into the current month we are
  const currentDay = lastDate.getDate();
  const daysInCurrentMonth = new Date(lastDate.getFullYear(), lastDate.getMonth() + 1, 0).getDate();
  const monthCompletionRatio = currentDay / daysInCurrentMonth;
  
  // Offset for the remaining portion of the current month
  const remainingCurrentMonthWidth = avgMonthWidth * (1 - monthCompletionRatio);
  
  for (let i = 1; i <= 4; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setMonth(futureDate.getMonth() + i);
    
    // Position: end of data + remaining current month + (i-1) full future months
    const x = endOfDataX + remainingCurrentMonthWidth + ((i - 1) * avgMonthWidth);
    const futureYear = futureDate.getFullYear();
    
    // Only add if it fits within the visible area with margin
    if (x < chartWidth - 80) {
      const label = futureYear !== currentYear
        ? futureDate.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
        : futureDate.toLocaleDateString('en-US', { month: 'short' });
      
      monthDividers.push({
        x,
        index: -1,
        label,
        isFuture: true
      });
    }
  }

  // Build per-signal glow + cone geometry for every extreme score in the last
  // 60 days. Cones widen as the time horizon grows (sqrt(W/30)); the combined
  // region is illuminated via a mask that dims everything outside.
  const signalRender = (() => {
    if (!projections || !Array.isArray(projections.signals) || projections.signals.length === 0) {
      return { signalIndices: new Map(), cones: [] };
    }

    const realizedVol = projections.realizedVol;
    const buckets = projections.assessmentData;
    if (!realizedVol || !Array.isArray(buckets)) {
      return { signalIndices: new Map(), cones: [] };
    }

    const getBucket = (score) => {
      if (score >= 99) return '99+';
      if (score >= 95) return '95+';
      if (score >= 90) return '90+';
      if (score >= 85) return '85+';
      if (score >= 80) return '80+';
      if (score >= CALL_STRONG_MIN) return '75+';
      if (score >= CALL_SIGNAL_MIN) return '70+';
      if (score <= 5) return '<5';
      if (score <= 10) return '<10';
      if (score <= 15) return '<15';
      if (score <= 20) return '<20';
      if (score <= PUT_SIGNAL_MAX) return '<25';
      if (score <= 30) return '<30';
      return null;
    };

    const periods = [7, 15, 30, 60];
    const dateToIndex = {};
    priceData.forEach((c, i) => { dateToIndex[c.date.split('T')[0]] = i; });

    const signalIndices = new Map();
    const cones = [];

    projections.signals.forEach((sig, idx) => {
      const sigIndex = dateToIndex[sig.signalDate];
      if (sigIndex === undefined) return;
      const sigCandle = priceData[sigIndex];
      if (!sigCandle || sigCandle.close == null) return;

      const entry = sigCandle.close;
      const isCall = sig.signalScore >= 50;
      const bucket = getBucket(sig.signalScore);
      const bucketData = buckets.find(b => b.bucket === bucket);
      if (!bucketData) return;

      const sigX = padding + sigIndex * candleSpacing + candleSpacing / 2;
      const isWeakCall = isCall && sig.signalScore < CALL_STRONG_MIN;

      signalIndices.set(sigIndex, { isCall, isWeakCall, score: sig.signalScore });

      // Build cone polygon: start at entry, expand upper/lower edges through periods.
      const upperPts = [[sigX, getY(entry)]];
      const lowerPts = [[sigX, getY(entry)]];
      const periodPoints = [];
      let hasAny = false;

      periods.forEach((days) => {
        const periodKey = `${days}d`;
        const periodScale = Math.sqrt(days / 30);
        const mfeSigma = bucketData.mfe_sigma_p25?.[periodKey];
        const maeSigma = bucketData.avg_mae_winner_sigma?.[periodKey];
        if (mfeSigma == null && maeSigma == null) return;

        const tpPct = mfeSigma != null ? mfeSigma * periodScale * realizedVol : null;
        const slPct = maeSigma != null ? Math.abs(maeSigma * periodScale * realizedVol) : null;

        let upperPrice = null;
        let lowerPrice = null;
        let tpPrice = null;
        let slPrice = null;
        if (isCall) {
          if (tpPct != null) { upperPrice = entry * (1 + tpPct / 100); tpPrice = upperPrice; }
          if (slPct != null) { lowerPrice = entry * (1 - slPct / 100); slPrice = lowerPrice; }
        } else {
          if (slPct != null) { upperPrice = entry * (1 + slPct / 100); slPrice = upperPrice; }
          if (tpPct != null) { lowerPrice = entry * (1 - tpPct / 100); tpPrice = lowerPrice; }
        }

        const x = sigX + days * candleSpacing;
        if (upperPrice != null) upperPts.push([x, getY(upperPrice)]);
        if (lowerPrice != null) lowerPts.push([x, getY(lowerPrice)]);
        const winRate = bucketData.win_rate?.[periodKey];
        periodPoints.push({
          days,
          x,
          tpPrice,
          slPrice,
          winRate: winRate != null ? winRate : null,
          tpY: tpPrice != null ? getY(tpPrice) : null,
          slY: slPrice != null ? getY(slPrice) : null,
        });
        hasAny = true;
      });

      if (!hasAny) return;

      const vertices = upperPts.concat(lowerPts.slice().reverse());
      const upperStr = upperPts.map(p => `${p[0]},${p[1]}`).join(' ');
      const lowerStr = lowerPts.map(p => `${p[0]},${p[1]}`).join(' ');

      // Score-strength scaling: |score - 50| / 50 in [0.5, 1] for extreme scores.
      // Drives stroke opacity on the two cone edges. Overlapping edges compound
      // via straight alpha compositing; a modest per-cone peak preserves headroom.
      const strength = Math.min(1, Math.abs(sig.signalScore - 50) / 50);
      const transitionStrength = Math.max(0, Math.min(1, (sig.signalScore - CALL_SIGNAL_MIN) / (CALL_STRONG_MIN - CALL_SIGNAL_MIN)));
      const peakOpacity = isWeakCall ? 0.26 + 0.18 * transitionStrength : 0.55 + 0.40 * strength;

      cones.push({
        key: `cone-${idx}-${sig.signalDate}`,
        gradId: `coneGrad-${idx}-${symbol}`,
        upperPath: upperStr,
        lowerPath: lowerStr,
        vertices,
        periodPoints,
        peakOpacity,
        gradStart: sigX,
        gradEnd: sigX + 60 * candleSpacing,
        isCall,
        isWeakCall,
      });
    });

    return { signalIndices, cones };
  })();

  conesRef.current = signalRender.cones;
  const hoveredCone = signalRender.cones.find(c => c.key === hoveredConeKey) || null;

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{
        height: height || '72vh',
        // When a parent container owns the horizontal scroll (externalScroll),
        // this div must NOT clip or scroll itself — the parent does it.
        overflowX: (isMobile && !externalScroll) ? 'auto' : 'hidden',
        overflowY: 'hidden',
        WebkitOverflowScrolling: (isMobile && !externalScroll) ? 'touch' : undefined,
      }}
    >
      <svg
        width={isMobile ? MOBILE_CHART_W : '100%'}
        height="100%"
        viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
        preserveAspectRatio="none"
        className={isMobile ? 'h-full' : 'w-full h-full'}
        onPointerMove={handlePointerSelect}
        onPointerDown={handlePointerSelect}
        onMouseLeave={handleMouseLeave}
      >
          <defs>
            {/* Tight gold luster hugging the candle silhouette — not a floating halo. */}
            <filter id={`candleLuster-${symbol}`} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur in="SourceAlpha" stdDeviation="2.2" result="blur" />
              <feFlood floodColor="#fbbf24" floodOpacity="0.95" result="gold" />
              <feComposite in="gold" in2="blur" operator="in" result="sheen" />
              <feGaussianBlur in="SourceAlpha" stdDeviation="0.8" result="tightBlur" />
              <feFlood floodColor="#fde68a" floodOpacity="1" result="bright" />
              <feComposite in="bright" in2="tightBlur" operator="in" result="brightSheen" />
              <feMerge>
                <feMergeNode in="sheen" />
                <feMergeNode in="brightSheen" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id={`signalArrowGlow-${symbol}`} x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur in="SourceAlpha" stdDeviation="1.8" result="blur" />
              <feFlood floodColor="#34d399" floodOpacity="0.70" result="glow" />
              <feComposite in="glow" in2="blur" operator="in" result="softGlow" />
              <feMerge>
                <feMergeNode in="softGlow" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            {/* Soft-edge blur applied to all cone polygons so the hard TP/SL
                boundaries feather into the background instead of reading as drawn
                shapes. stdDeviation is generous — the cone becomes ambient glow. */}
            <filter id={`coneBlur-${symbol}`} x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="14" />
            </filter>
            {/* Per-cone log-falloff gradient along the time axis. Offsets map to
                entry/7d/15d/30d/60d ≈ 0 / 0.117 / 0.25 / 0.5 / 1.0; opacity follows
                a log-like decay so distant horizons contribute diminishing light. */}
            {signalRender.cones.map(c => (
              <linearGradient
                key={`grad-${c.key}`}
                id={c.gradId}
                gradientUnits="userSpaceOnUse"
                x1={c.gradStart}
                y1="0"
                x2={c.gradEnd}
                y2="0"
              >
                <stop offset="0" stopColor="#4a5568" stopOpacity={c.peakOpacity} />
                <stop offset="0.117" stopColor="#4a5568" stopOpacity={c.peakOpacity * 0.72} />
                <stop offset="0.25" stopColor="#4a5568" stopOpacity={c.peakOpacity * 0.49} />
                <stop offset="0.5" stopColor="#4a5568" stopOpacity={c.peakOpacity * 0.26} />
                <stop offset="1" stopColor="#4a5568" stopOpacity={c.peakOpacity * 0.06} />
              </linearGradient>
            ))}
          </defs>

          {/* Background */}
          <rect width="100%" height="100%" fill="#0A0A0B" />

          {/* Cone background illumination — lifts the near-black base toward grey
              inside TP/SL projection cones. Rendered before candles so it sits as
              a true background layer; a shared gaussian blur feathers the polygon
              edges so cones read as ambient light, not drawn shapes. Overlapping
              cones compound via standard alpha compositing. */}
          {signalRender.cones.length > 0 && (
            <g filter={`url(#coneBlur-${symbol})`}>
              {signalRender.cones.map(c => (
                <g key={`cone-${c.key}`}>
                  <polyline
                    points={c.upperPath}
                    fill="none"
                    stroke={`url(#${c.gradId})`}
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    pointerEvents="none"
                  />
                  <polyline
                    points={c.lowerPath}
                    fill="none"
                    stroke={`url(#${c.gradId})`}
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    pointerEvents="none"
                  />
                </g>
              ))}
            </g>
          )}

          {/* Stock Symbol and Name Watermark */}
          <g opacity="0.12">
            {/* Symbol */}
            <text
              x={watermarkPos.symbolX}
              y={watermarkPos.symbolY}
              textAnchor={watermarkPos.anchor}
              fill="#ffffff"
              fontSize={120 * chartScale}
              fontWeight="900"
              fontFamily="system-ui, -apple-system, sans-serif"
              letterSpacing={-3 * chartScale}
            >
              {symbol}
            </text>
            {/* Name - Smaller below */}
            {stockName && (
              <text
                x={watermarkPos.nameX}
                y={watermarkPos.nameY}
                textAnchor={watermarkPos.anchor}
                fill="#ffffff"
                fontSize={24 * chartScale}
                fontWeight="500"
                fontFamily="system-ui, -apple-system, sans-serif"
              >
                {stockName}
              </text>
            )}
          </g>
          
          {/* Y-axis labels - only top and bottom */}
          {[0, 1].map((ratio, index) => {
            const price = paddedMinPrice + ratio * paddedPriceRange;
            if (!price && price !== 0) return null;
            
            const y = priceBottom - ratio * priceZoneHeight;

            // Bottom label should align with month labels
            const labelY = ratio === 0 ? priceBottom + 14 : y + 4;
            
            return (
              <g key={index}>
                <text 
                  x={padding - 10} 
                  y={labelY} 
                  textAnchor="end" 
                  fill="#9CA3AF" 
                  fontSize="12"
                  fontWeight="600"
                >
                  ${price.toFixed(2)}
                </text>
              </g>
            );
          })}
          
          {/* Month dividers */}
          {monthDividers.map((divider, index) => (
            <g key={`month-${index}`}>
              <text
                x={divider.x + 5}
                y={chartHeight - padding + 20}
                fill={divider.isFuture ? "#6b7280" : "#9CA3AF"}
                fontSize="10"
                fontWeight={divider.isFuture ? "500" : "600"}
                opacity={divider.isFuture ? "0.7" : "1"}
              >
                {divider.label}
              </text>
            </g>
          ))}
          
          {/* Bollinger Bands */}
          {(() => {
            // Create path strings for each band
            const upperPath = [];
            const middlePath = [];
            const lowerPath = [];
            
            bollingerBands.forEach((band, index) => {
              if (band.upper !== null) {
                const x = padding + index * candleSpacing + candleSpacing / 2;
                const upperY = getY(band.upper);
                const middleY = getY(band.middle);
                const lowerY = getY(band.lower);
                
                upperPath.push(`${upperPath.length === 0 ? 'M' : 'L'} ${x} ${upperY}`);
                middlePath.push(`${middlePath.length === 0 ? 'M' : 'L'} ${x} ${middleY}`);
                lowerPath.push(`${lowerPath.length === 0 ? 'M' : 'L'} ${x} ${lowerY}`);
              }
            });
            
            return (
              <g>
                {/* Upper Band */}
                <path
                  d={upperPath.join(' ')}
                  stroke="#8b5cf6"
                  strokeWidth="1"
                  fill="none"
                  opacity="0.3"
                />
                {/* Middle Band (SMA) */}
                <path
                  d={middlePath.join(' ')}
                  stroke="#8b5cf6"
                  strokeWidth="1.5"
                  fill="none"
                  opacity="0.4"
                />
                {/* Lower Band */}
                <path
                  d={lowerPath.join(' ')}
                  stroke="#8b5cf6"
                  strokeWidth="1"
                  fill="none"
                  opacity="0.3"
                />
              </g>
            );
          })()}
          
          {/* Volume bars overlay (bottom 30% of chart) */}
          {(() => {
            const volumes = priceData.map(d => d.volume || 0);
            const volZoneHeight = isMobile ? volumeZoneHeight : chartAreaHeight * 0.30;
            const volBottom = chartHeight - padding;

            // 50-day moving average
            const volMA = [];
            for (let i = 0; i < volumes.length; i++) {
              if (i < 49) { volMA.push(null); continue; }
              let sum = 0;
              for (let j = 0; j < 50; j++) sum += volumes[i - j];
              volMA.push(sum / 50);
            }

            // Cap at 3x the latest MA so spikes don't flatten everything
            const latestMA = volMA.filter(v => v !== null).slice(-1)[0] || 1;
            const volCap = latestMA * 3;
            const getVolY = (v) => volBottom - (Math.min(v, volCap) / volCap) * volZoneHeight;

            const maPath = [];
            volMA.forEach((v, i) => {
              if (v === null) return;
              const x = padding + i * candleSpacing + candleSpacing / 2;
              maPath.push(`${maPath.length === 0 ? 'M' : 'L'} ${x} ${getVolY(v)}`);
            });

            const volScoreSegs = [];
            let volScoreSeg = [];
            priceData.forEach((candle, index) => {
              const dateKey = candle.date.split('T')[0];
              const vs = volumeMap[dateKey]?.vol;
              if (vs == null || Number.isNaN(vs)) {
                if (volScoreSeg.length) {
                  volScoreSegs.push(volScoreSeg);
                  volScoreSeg = [];
                }
                return;
              }
              const x = padding + index * candleSpacing + candleSpacing / 2;
              const clamped = Math.max(0, Math.min(100, vs));
              const y = volBottom - (clamped / 100) * volZoneHeight;
              volScoreSeg.push({ x, y });
            });
            if (volScoreSeg.length) volScoreSegs.push(volScoreSeg);

            return (
              <g>
                {priceData.map((candle, index) => {
                  const x = padding + index * candleSpacing + candleSpacing / 2;
                  const vol = candle.volume || 0;
                  const barH = (Math.min(vol, volCap) / volCap) * volZoneHeight;
                  const isGreen = candle.close >= candle.open;
                  return (
                    <rect
                      key={`vol-${index}`}
                      x={x - candleWidth / 2}
                      y={volBottom - barH}
                      width={candleWidth}
                      height={Math.max(1, barH)}
                      fill={isGreen ? '#10b981' : '#ef4444'}
                      opacity={hoveredIndex === index ? 0.4 : 0.18}
                    />
                  );
                })}
                {volScoreSegs.map((seg, si) =>
                  seg.length > 1 ? (
                    <path
                      key={`vol-score-${si}`}
                      d={seg.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')}
                      stroke="#94a3b8"
                      strokeWidth="1"
                      fill="none"
                      opacity="0.38"
                      strokeLinejoin="round"
                    />
                  ) : null
                )}
                {maPath.length > 1 && (
                  <path d={maPath.join(' ')} stroke="#f59e0b" strokeWidth="1.2" fill="none" opacity="0.5" />
                )}
              </g>
            );
          })()}

          {/* Candlesticks */}
          {priceData.map((candle, index) => {
            const x = padding + index * candleSpacing + candleSpacing / 2;
            const isGreen = candle.close >= candle.open;
            const isHovered = hoveredIndex === index;
            const signalMeta = signalRender.signalIndices.get(index);
            const isSignal = Boolean(signalMeta);

            const highY = getY(candle.high);
            const lowY = getY(candle.low);
            const openY = getY(candle.open);
            const closeY = getY(candle.close);

            const bodyTop = Math.min(openY, closeY);
            const bodyBottom = Math.max(openY, closeY);
            const bodyHeight = Math.max(1, bodyBottom - bodyTop);

            const candleColor = isGreen ? '#10b981' : '#ef4444';
            const wickColor = isGreen ? '#10b981' : '#ef4444';

            const candleBody = (
              <>
                <line
                  x1={x}
                  y1={highY}
                  x2={x}
                  y2={lowY}
                  stroke={wickColor}
                  strokeWidth={isHovered ? 2 : 1}
                  opacity={isHovered ? 1 : 0.8}
                />
                <rect
                  x={x - candleWidth / 2}
                  y={bodyTop}
                  width={candleWidth}
                  height={bodyHeight}
                  fill={candleColor}
                  stroke={isHovered ? '#ffffff' : candleColor}
                  strokeWidth={isHovered ? 1.5 : 0}
                  opacity={isHovered ? 1 : 0.9}
                />
              </>
            );

            if (isSignal) {
              return (
                <g
                  key={index}
                  opacity={isHovered ? 1 : signalMeta.isWeakCall ? 0.9 : 0.95}
                  filter={signalMeta.isWeakCall ? undefined : `url(#candleLuster-${symbol})`}
                >
                  {candleBody}
                </g>
              );
            }

            return (
              <g key={index} opacity={isHovered ? 1 : 0.9}>
                {candleBody}
              </g>
            );
          })}

          {/* Score-opportunity arrows. Calls start at 70; 70-74 is intentionally
              softer than the high-conviction 75+ arrows. */}
          {opportunityZones.map((zone, idx) => {
            const x = padding + zone.index * candleSpacing + candleSpacing / 2;
            const candle = priceData[zone.index];
            if (zone.type === 'call') {
              const isWeakCall = zone.score < CALL_STRONG_MIN;
              const transitionStrength = Math.max(0, Math.min(1, (zone.score - CALL_SIGNAL_MIN) / (CALL_STRONG_MIN - CALL_SIGNAL_MIN)));
              const strongStrength = Math.max(0, Math.min(1, (zone.score - CALL_STRONG_MIN) / (100 - CALL_STRONG_MIN)));
              const size = isWeakCall ? 2.8 + transitionStrength * 0.7 : 4.2 + strongStrength * 2.6;
              const opacity = isWeakCall ? 0.28 + transitionStrength * 0.16 : 0.74 + strongStrength * 0.20;
              const tipY = getY(candle.low) + 14 + size;
              return (
                <g
                  key={`opp-m-${idx}`}
                  opacity={opacity}
                  filter={isWeakCall ? undefined : `url(#signalArrowGlow-${symbol})`}
                >
                  {!isWeakCall && (
                    <line
                      x1={x}
                      y1={tipY + 1}
                      x2={x}
                      y2={tipY + 7 + strongStrength * 3}
                      stroke="#34d399"
                      strokeWidth={Math.max(1, size * 0.32)}
                      strokeLinecap="round"
                      opacity="0.72"
                    />
                  )}
                  <polygon
                    points={`${x},${tipY - size * 2} ${x - size},${tipY} ${x + size},${tipY}`}
                    fill={isWeakCall ? '#7FBFA0' : '#10b981'}
                    fillOpacity={isWeakCall ? 0.42 : 1}
                    stroke={isWeakCall ? '#d1fae5' : '#a7f3d0'}
                    strokeOpacity={isWeakCall ? 0.58 : 0.90}
                    strokeWidth={isWeakCall ? 0.65 : 0.75}
                    strokeDasharray={isWeakCall ? '1.5 1.5' : undefined}
                  />
                </g>
              );
            } else {
              const strength = Math.min(1, Math.abs(zone.score - 50) / 50);
              const size = 2 + strength * 4;
              const opacity = 0.2 + strength * 0.6;
              const tipY = getY(candle.high) - 14 - size;
              return (
                <g key={`opp-m-${idx}`} opacity={opacity}>
                  <polygon
                    points={`${x},${tipY + size * 2} ${x - size},${tipY} ${x + size},${tipY}`}
                    fill="#ef4444"
                  />
                </g>
              );
            }
          })}

          {/* Earnings date markers */}
          {(() => {
            const earningsMarkers = [];
            const dateToIndex = {};
            priceData.forEach((candle, i) => { dateToIndex[candle.date.split('T')[0]] = i; });

            // Dedupe by date string, then keep at most ONE future earnings marker
            // (the nearest upcoming). Past markers all kept as historical context.
            const seen = new Set();
            const uniqueDates = earningsDates.filter(d => {
              if (seen.has(d)) return false;
              seen.add(d);
              return true;
            });
            const lastTime = parseLocalDate(priceData[priceData.length - 1].date).getTime();
            const futureSorted = uniqueDates
              .filter(d => parseLocalDate(d).getTime() > lastTime)
              .sort((a, b) => parseLocalDate(a).getTime() - parseLocalDate(b).getTime());
            const nearestFuture = futureSorted[0];
            const filteredDates = uniqueDates.filter(d => {
              const t = parseLocalDate(d).getTime();
              return t <= lastTime || d === nearestFuture;
            });

            filteredDates.forEach(dateStr => {
              const idx = dateToIndex[dateStr];
              if (idx !== undefined) {
                earningsMarkers.push({ idx, dateStr, isFuture: false });
              } else {
                const earningsTime = parseLocalDate(dateStr).getTime();
                if (earningsTime > lastTime) {
                  const daysDiff = (earningsTime - lastTime) / (1000 * 60 * 60 * 24);
                  earningsMarkers.push({ daysDiff, dateStr, isFuture: true });
                }
              }
            });

            return earningsMarkers.map((m, i) => {
              const x = m.isFuture
                ? padding + (priceData.length - 1 + m.daysDiff) * candleSpacing + candleSpacing / 2
                : padding + m.idx * candleSpacing + candleSpacing / 2;
              if (x > chartWidth - 20) return null;
              const bottomY = chartHeight - padding;
              return (
                <g key={`earn-${i}`}>
                  <line
                    x1={x} y1={padding} x2={x} y2={bottomY}
                    stroke="#f59e0b" strokeWidth="1" opacity="0.2"
                  />
                  <text
                    x={x} y={bottomY + 36}
                    textAnchor="middle" fill="#f59e0b" fontSize="12" fontWeight="700"
                  >
                    E
                  </text>
                </g>
              );
            });
          })()}

          {/* Current Price Marker */}
          {(() => {
            const lastCandle = priceData[priceData.length - 1];
            if (!lastCandle || lastCandle.close === null || lastCandle.close === undefined) {
              return null;
            }

            const currentPrice = lastCandle.close;
            const x = padding + (priceData.length - 1) * candleSpacing + candleSpacing / 2;
            const y = getY(currentPrice);

            return (
              <g>
                {/* Full-width horizontal line at current price */}
                <line
                  x1={padding}
                  y1={y}
                  x2={chartWidth - rightPadding}
                  y2={y}
                  stroke="#6b7280"
                  strokeWidth="1"
                  strokeDasharray="3,3"
                  opacity="0.5"
                />
              </g>
            );
          })()}

          {/* Current price label */}
          {(() => {
            const currentPrice = stockDetail?.current_price ?? stockDetail?.latest_price_data?.close;
            if (currentPrice == null || !priceData.length) return null;
            const lastCandleIndex = priceData.length - 1;
            const lastCandleX = padding + lastCandleIndex * candleSpacing + candleSpacing / 2;
            return (
              <text
                x={lastCandleX + candleWidth / 2 + 6}
                y={getY(currentPrice) + 4}
                textAnchor="start"
                fill="#ffffff"
                fontSize="11"
                fontWeight="700"
                opacity="0.95"
              >
                ${currentPrice.toFixed(2)}
              </text>
            );
          })()}
          
          {/* TP/SL horizontal markers revealed while hovering a projection cone.
              One thin green line per timeframe for TP, one red for SL, with
              the exact dollar value labeled at the projection x-coordinate. */}
          {hoveredCone && (() => {
            const CHAR_W = 4.0;
            const LINE_H = 8;
            const BASE_OFFSET = 3;
            const tpAboveLine = hoveredCone.isCall;
            const slAboveLine = !hoveredCone.isCall;
            const placeLabels = (items, aboveLine) => {
              const sorted = [...items].sort((a, b) => a.x - b.x);
              const placed = [];
              for (const it of sorted) {
                let step = 0;
                while (true) {
                  const y = aboveLine
                    ? it.anchorY - BASE_OFFSET - step * LINE_H
                    : it.anchorY + BASE_OFFSET + LINE_H - 2 + step * LINE_H;
                  const halfW = (it.text.length * CHAR_W) / 2;
                  const xMin = it.x - halfW;
                  const xMax = it.x + halfW;
                  const collides = placed.some(q =>
                    !(xMax < q.xMin - 2 || xMin > q.xMax + 2) &&
                    Math.abs(y - q.y) < LINE_H - 1
                  );
                  if (!collides) {
                    placed.push({ ...it, y, xMin, xMax });
                    break;
                  }
                  step += 1;
                  if (step > 6) { placed.push({ ...it, y, xMin, xMax }); break; }
                }
              }
              return placed;
            };
            const tpItems = hoveredCone.periodPoints
              .filter(p => p.tpY != null)
              .map(p => ({
                key: `tp-${p.days}`,
                x: p.x,
                anchorY: p.tpY,
                text: `$${p.tpPrice.toFixed(2)}${p.winRate != null ? ` (${p.winRate.toFixed(1)}%)` : ''}`,
              }));
            const slItems = hoveredCone.periodPoints
              .filter(p => p.slY != null)
              .map(p => ({
                key: `sl-${p.days}`,
                x: p.x,
                anchorY: p.slY,
                text: `$${p.slPrice.toFixed(2)}${p.winRate != null ? ` (${(100 - p.winRate).toFixed(1)}%)` : ''}`,
              }));
            const tpPlaced = placeLabels(tpItems, tpAboveLine);
            const slPlaced = placeLabels(slItems, slAboveLine);
            return (
              <g pointerEvents="none">
                {hoveredCone.periodPoints.map((p) => (
                  <g key={`hl-${hoveredCone.key}-${p.days}`}>
                    {p.tpY != null && (
                      <line
                        x1={p.x - candleSpacing * 1.5}
                        x2={p.x + candleSpacing * 1.5}
                        y1={p.tpY}
                        y2={p.tpY}
                        stroke="#10b981"
                        strokeWidth="0.6"
                        opacity="0.95"
                      />
                    )}
                    {p.slY != null && (
                      <line
                        x1={p.x - candleSpacing * 1.5}
                        x2={p.x + candleSpacing * 1.5}
                        y1={p.slY}
                        y2={p.slY}
                        stroke="#ef4444"
                        strokeWidth="0.6"
                        opacity="0.95"
                      />
                    )}
                  </g>
                ))}
                {tpPlaced.map((l) => (
                  <text
                    key={`hlbl-${hoveredCone.key}-${l.key}`}
                    x={l.x}
                    y={l.y}
                    textAnchor="middle"
                    fill="#10b981"
                    fontSize="7"
                    fontWeight="600"
                    opacity="0.95"
                  >
                    {l.text}
                  </text>
                ))}
                {slPlaced.map((l) => (
                  <text
                    key={`hlbl-${hoveredCone.key}-${l.key}`}
                    x={l.x}
                    y={l.y}
                    textAnchor="middle"
                    fill="#ef4444"
                    fontSize="7"
                    fontWeight="600"
                    opacity="0.95"
                  >
                    {l.text}
                  </text>
                ))}
              </g>
            );
          })()}

          {/* Hover indicator line and tooltip */}
          {showInlineTooltip && hoveredIndex !== null && hoveredIndex >= 0 && hoveredIndex < priceData.length && (
            <>
              <line
                x1={padding + hoveredIndex * candleSpacing + candleSpacing / 2}
                y1={padding}
                x2={padding + hoveredIndex * candleSpacing + candleSpacing / 2}
                y2={chartHeight - padding}
                stroke="#ffffff"
                strokeWidth="1"
                opacity="0.3"
                strokeDasharray="4,4"
              />

              {/* Hover data box */}
              {(() => {
                const hoveredCandle = priceData[hoveredIndex];
                if (!hoveredCandle || hoveredCandle.open === null || hoveredCandle.close === null) {
                  return null;
                }

                // Check if today
                const hoveredDate = parseLocalDate(hoveredCandle.date);
                const today = new Date();
                today.setHours(0, 0, 0, 0);
                const isToday = hoveredDate.getTime() === today.getTime();

                const x = padding + hoveredIndex * candleSpacing + candleSpacing / 2;
                const formatVol = (v) => v >= 1e9 ? (v/1e9).toFixed(1)+'B' : v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v.toString();
                const hoveredDateKey = hoveredCandle.date.split('T')[0];
                const hoveredScore = scoreMap[hoveredDateKey];
                const scoreColor = hoveredScore == null
                  ? '#9ca3af'
                  : hoveredScore >= 70
                    ? '#7FBFA0'
                    : hoveredScore <= 25
                      ? '#f87171'
                      : '#d1d5db';
                const scoreBgFill = hoveredScore == null
                  ? 'rgba(156,163,175,0.10)'
                  : hoveredScore >= 70
                    ? 'rgba(127,191,160,0.18)'
                    : hoveredScore <= 25
                      ? 'rgba(248,113,113,0.18)'
                      : 'rgba(156,163,175,0.10)';
                const scoreBorderStroke = hoveredScore == null
                  ? 'rgba(156,163,175,0.30)'
                  : hoveredScore >= 70
                    ? 'rgba(127,191,160,0.50)'
                    : hoveredScore <= 25
                      ? 'rgba(248,113,113,0.50)'
                      : 'rgba(156,163,175,0.30)';

                const boxWidth = 164;
                const boxHeight = 114;
                const boxX = x + 10 > chartWidth - boxWidth - padding ? x - boxWidth - 10 : x + 10;
                // Place tooltip above candle when price sits closer to the yearly
                // low, below candle when closer to the yearly high — keeps the
                // tooltip on the opposite side of the price range from the candle
                // so it never overlaps the bar.
                const priceRange = maxPrice - minPrice;
                const closerToHigh = priceRange > 0
                  ? (hoveredCandle.close - minPrice) / priceRange > 0.5
                  : false;
                const boxY = closerToHigh
                  ? Math.min(priceBottom - boxHeight - 4, chartHeight - padding - boxHeight)
                  : padding + 10;

                const chgPct = hoveredCandle.open
                  ? ((hoveredCandle.close - hoveredCandle.open) / hoveredCandle.open * 100)
                  : 0;
                const chgColor = chgPct >= 0 ? '#7FBFA0' : '#f87171';

                const pillW = 32;
                const pillH = 18;
                const pillX = boxX + boxWidth - pillW - 10;
                const pillY = boxY + 7;

                const mono = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' };

                return (
                  <g>
                    <rect
                      x={boxX}
                      y={boxY}
                      width={boxWidth}
                      height={boxHeight}
                      fill="#0b0f17"
                      stroke="rgba(255,255,255,0.10)"
                      strokeWidth="1"
                      rx="6"
                      opacity="0.98"
                    />

                    <text
                      x={boxX + 12}
                      y={boxY + 21}
                      fill="#e5e7eb"
                      fontSize="11"
                      fontWeight="600"
                      style={{ letterSpacing: '0.02em' }}
                    >
                      {isToday ? 'Today' : hoveredDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </text>

                    <rect
                      x={pillX}
                      y={pillY}
                      width={pillW}
                      height={pillH}
                      fill={scoreBgFill}
                      stroke={scoreBorderStroke}
                      strokeWidth="1"
                      rx="4"
                    />
                    <text
                      x={pillX + pillW / 2}
                      y={pillY + 13}
                      textAnchor="middle"
                      fill={scoreColor}
                      fontSize="11"
                      fontWeight="700"
                      style={mono}
                    >
                      {hoveredScore != null ? hoveredScore.toFixed(0) : '—'}
                    </text>

                    <line
                      x1={boxX + 10}
                      y1={boxY + 34}
                      x2={boxX + boxWidth - 10}
                      y2={boxY + 34}
                      stroke="rgba(255,255,255,0.06)"
                      strokeWidth="1"
                    />

                    <text x={boxX + 12} y={boxY + 50} fill="#9ca3af" fontSize="10">Open</text>
                    <text x={boxX + boxWidth - 12} y={boxY + 50} textAnchor="end" fill="#e5e7eb" fontSize="10" fontWeight="600" style={mono}>
                      ${(hoveredCandle.open || 0).toFixed(2)}
                    </text>

                    <text x={boxX + 12} y={boxY + 64} fill="#9ca3af" fontSize="10">High</text>
                    <text x={boxX + boxWidth - 12} y={boxY + 64} textAnchor="end" fill="#7FBFA0" fontSize="10" fontWeight="600" style={mono}>
                      ${(hoveredCandle.high || 0).toFixed(2)}
                    </text>

                    <text x={boxX + 12} y={boxY + 78} fill="#9ca3af" fontSize="10">Low</text>
                    <text x={boxX + boxWidth - 12} y={boxY + 78} textAnchor="end" fill="#f87171" fontSize="10" fontWeight="600" style={mono}>
                      ${(hoveredCandle.low || 0).toFixed(2)}
                    </text>

                    <text x={boxX + 12} y={boxY + 92} fill="#9ca3af" fontSize="10">Close</text>
                    <text x={boxX + boxWidth - 12} y={boxY + 92} textAnchor="end" fill="#e5e7eb" fontSize="10" fontWeight="600" style={mono}>
                      ${(hoveredCandle.close || 0).toFixed(2)}
                    </text>

                    <text x={boxX + 12} y={boxY + 106} fill={chgColor} fontSize="10" fontWeight="600" style={mono}>
                      {chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%
                    </text>
                    <text x={boxX + boxWidth - 12} y={boxY + 106} textAnchor="end" fill="#9ca3af" fontSize="10" style={mono}>
                      {formatVol(hoveredCandle.volume || 0)}
                    </text>
                  </g>
                );
              })()}
            </>
          )}

        </svg>
    </div>
  );
};

export default PriceChart; 
