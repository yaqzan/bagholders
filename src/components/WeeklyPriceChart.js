import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { parseLocalDate } from '../utils/timeUtils';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';
const buildApiUrl = (endpoint) => `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;

const WeeklyPriceChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad }) => {
  const [priceData, setPriceData] = useState(null);
  const [indicatorData, setIndicatorData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [containerDimensions, setContainerDimensions] = useState({ width: 1400, height: 400 });
  const hasCalledOnLoad = React.useRef(false);
  const containerRef = React.useRef(null);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        hasCalledOnLoad.current = false;
        const [priceResponse, indicatorResponse] = await Promise.all([
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-price-history?limit=250`)),
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-indicators?limit=250`)),
        ]);
        if (cancelled) return;
        if (priceResponse.data?.weekly_price_history) {
          setPriceData(priceResponse.data.weekly_price_history.sort((a, b) => parseLocalDate(a.date) - parseLocalDate(b.date)));
        } else {
          setPriceData([]);
        }
        if (indicatorResponse.data?.indicators) {
          const m = {};
          indicatorResponse.data.indicators.forEach(ind => { m[ind.date] = ind; });
          setIndicatorData(m);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err.message);
        setPriceData([]);
      } finally {
        if (cancelled) return;
        setLoading(false);
        if (onLoad && !hasCalledOnLoad.current) {
          hasCalledOnLoad.current = true;
          onLoad();
        }
      }
    };
    if (symbol) fetchData();
    return () => { cancelled = true; };
  }, [symbol, onLoad]);

  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        const w = containerRef.current.offsetWidth;
        const h = containerRef.current.offsetHeight;
        setContainerDimensions(prev => (prev.width === w && prev.height === h ? prev : { width: w, height: h }));
      }
    };
    const timer = setTimeout(updateDimensions, 100);
    updateDimensions();
    window.addEventListener('resize', updateDimensions);
    return () => { clearTimeout(timer); window.removeEventListener('resize', updateDimensions); };
  }, [priceData]);

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-trading-blue-500"></div></div>;
  }

  if (error || !priceData || priceData.length === 0) {
    return (
      <div className="h-80 flex items-center justify-center">
        <div className="text-center">
          <h3 className="text-lg font-semibold text-white mb-2">No Weekly Data</h3>
          <p className="text-gray-400 text-sm">No weekly price history available for {symbol}</p>
        </div>
      </div>
    );
  }

  const viewBoxWidth = Math.max(320, Math.min(1400, containerDimensions.width || 1400));
  const chartScale = viewBoxWidth / 1400;
  const containerAspectRatio = containerDimensions.width / Math.max(1, containerDimensions.height);
  const viewBoxHeight = viewBoxWidth / containerAspectRatio;

  const padding = 60;
  const futureDays = 65;
  const totalDataPoints = priceData.length + futureDays;
  const rightPadding = (viewBoxWidth - padding) * (1 - priceData.length / totalDataPoints);

  const chartWidth = viewBoxWidth;
  const chartHeight = viewBoxHeight;
  const chartAreaWidth = chartWidth - padding - rightPadding;
  const chartAreaHeight = chartHeight - 2 * padding;

  // Reserve a fixed slab at the bottom for the volume overlay.
  const volumeZoneHeight = 120;
  const volumeGap = 10;
  const priceZoneHeight = chartAreaHeight - volumeZoneHeight - volumeGap;
  const priceBottom = chartHeight - padding - volumeZoneHeight - volumeGap;

  const allPrices = priceData.flatMap(d => [d.high, d.low]);
  const minPrice = Math.min(...allPrices);
  const maxPrice = Math.max(...allPrices);
  const priceRange = maxPrice - minPrice;
  const pricePadding = priceRange * 0.05;
  const paddedMinPrice = minPrice - pricePadding;
  const paddedMaxPrice = maxPrice + pricePadding;
  const paddedPriceRange = paddedMaxPrice - paddedMinPrice;

  const candleWidth = Math.max(2, Math.min(12, chartAreaWidth / priceData.length * 0.7));
  const candleSpacing = chartAreaWidth / priceData.length;
  const yScale = priceZoneHeight / paddedPriceRange;
  const getY = (price) => priceBottom - (price - paddedMinPrice) * yScale;

  const handleMouseMove = (event) => {
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaledX = (event.clientX - rect.left) * (chartWidth / rect.width);
    const index = Math.floor((scaledX - padding) / candleSpacing);
    onHoverIndex(index >= 0 && index < priceData.length ? index : null);
  };

  // Watermark: place away from where the first candle sits in the price range.
  const oldestClose = priceData[0]?.close ?? (paddedMinPrice + paddedPriceRange / 2);
  const placeWatermarkAtTop = (oldestClose - paddedMinPrice) / paddedPriceRange < 0.5;
  const wmX = padding + 40 * chartScale;
  const wmSymY = placeWatermarkAtTop ? padding + 120 * chartScale : priceBottom - 40 * chartScale;
  const wmSubY = placeWatermarkAtTop ? padding + 154 * chartScale : priceBottom - 8 * chartScale;

  // EMA paths
  const buildEmaPath = (key) => {
    if (!indicatorData) return null;
    const pts = [];
    priceData.forEach((c, i) => {
      const ind = indicatorData[c.date.split('T')[0]];
      if (ind && ind[key] != null) {
        const x = padding + i * candleSpacing + candleSpacing / 2;
        pts.push(`${pts.length === 0 ? 'M' : 'L'} ${x} ${getY(ind[key])}`);
      }
    });
    return pts.length > 1 ? pts.join(' ') : null;
  };
  const ema50Path = buildEmaPath('ema_50');
  const ema200Path = buildEmaPath('ema_200');
  // Kijun-sen (Ichimoku 26-week midpoint) — drives v44 ICH dampener.
  // When price is BELOW this line (bearish weekly state), ICH dampens
  // high-conviction calls (75+) and lifts deep puts (≤25).
  const kijunPath = buildEmaPath('kijun_sen');

  // Bollinger Bands
  const bbUpper = [], bbMiddle = [], bbLower = [];
  priceData.forEach((c, i) => {
    const ind = indicatorData?.[c.date.split('T')[0]];
    if (ind?.upper_band != null && ind?.lower_band != null) {
      const x = padding + i * candleSpacing + candleSpacing / 2;
      bbUpper.push({ x, y: getY(ind.upper_band) });
      bbMiddle.push({ x, y: getY(ind.middle_band) });
      bbLower.push({ x, y: getY(ind.lower_band) });
    }
  });
  const bbFill = bbUpper.length >= 2
    ? bbUpper.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
      + ' ' + [...bbLower].reverse().map(p => `L ${p.x} ${p.y}`).join(' ') + ' Z'
    : null;
  const bbUpperPath = bbUpper.length >= 2 ? bbUpper.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ') : null;
  const bbMiddlePath = bbMiddle.length >= 2 ? bbMiddle.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ') : null;
  const bbLowerPath = bbLower.length >= 2 ? bbLower.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ') : null;

  // Quarter dividers — show Jan/Apr/Jul/Oct only so labels don't overlap.
  // Weekly data has ~4 candles/month; showing every month at fontSize=10 gives
  // ~18 SVG units per label but each label needs ~40 units → heavy overlap.
  // Quarterly spacing (~55 units) fits comfortably.
  const monthDividers = [];
  let lastMonth = -1;
  priceData.forEach((c, i) => {
    const d = parseLocalDate(c.date);
    const m = d.getMonth();
    if (m !== lastMonth) {
      lastMonth = m;
      if (m % 3 === 0) { // 0=Jan, 3=Apr, 6=Jul, 9=Oct
        monthDividers.push({
          x: padding + i * candleSpacing,
          label: d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }),
        });
      }
    }
  });

  // Volume bars + 50-period MA
  const volumes = priceData.map(d => d.volume || 0);
  const volBottom = chartHeight - padding;
  const volMA = [];
  for (let i = 0; i < volumes.length; i++) {
    if (i < 49) { volMA.push(null); continue; }
    let s = 0; for (let j = 0; j < 50; j++) s += volumes[i - j];
    volMA.push(s / 50);
  }
  const latestMA = volMA.filter(Boolean).slice(-1)[0] || 1;
  const volCap = Math.max(1, latestMA * 3);
  const getVolY = (v) => volBottom - (Math.min(v, volCap) / volCap) * volumeZoneHeight;
  const volMaPath = volMA.reduce((acc, v, i) => {
    if (v === null) return acc;
    const x = padding + i * candleSpacing + candleSpacing / 2;
    return acc + `${acc === '' ? 'M' : ' L'} ${x} ${getVolY(v)}`;
  }, '');

  const lastCandle = priceData[priceData.length - 1];
  const formatVol = (v) => v >= 1e9 ? (v/1e9).toFixed(1)+'B' : v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : String(v);
  const mono = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' };

  return (
    <div className="w-full" ref={containerRef} style={{ height: '72vh' }}>
      <svg
        width="100%" height="100%"
        viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
        preserveAspectRatio="none"
        className="w-full h-full"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => onHoverIndex(null)}
      >
        <rect width="100%" height="100%" fill="#0a0a0b" />

        {/* Watermark */}
        <g opacity="0.12">
          <text x={wmX} y={wmSymY} fill="#ffffff" fontSize={120 * chartScale} fontWeight="900" fontFamily="system-ui" letterSpacing={-3 * chartScale}>{symbol}</text>
          <text x={wmX} y={wmSubY} fill="#ffffff" fontSize={24 * chartScale} fontWeight="500" fontFamily="system-ui">Weekly</text>
        </g>

        {/* Y-axis price labels */}
        {[0, 1].map((ratio) => {
          const price = paddedMinPrice + ratio * paddedPriceRange;
          const y = priceBottom - ratio * priceZoneHeight;
          return (
            <text key={ratio} x={padding - 10} y={ratio === 0 ? priceBottom + 14 : y + 4} textAnchor="end" fill="#9CA3AF" fontSize="12" fontWeight="600">
              ${price.toFixed(2)}
            </text>
          );
        })}

        {/* Month dividers / x-axis labels */}
        {monthDividers.map((d, i) => (
          <text key={i} x={d.x + 5} y={chartHeight - padding + 20} fill="#9CA3AF" fontSize="10" fontWeight="600">{d.label}</text>
        ))}

        {/* Bollinger Bands */}
        {bbFill && <path d={bbFill} fill="#8b5cf6" opacity="0.08" />}
        {bbUpperPath && <path d={bbUpperPath} stroke="#8b5cf6" strokeWidth="1" fill="none" opacity="0.3" />}
        {bbMiddlePath && <path d={bbMiddlePath} stroke="#8b5cf6" strokeWidth="1.5" fill="none" opacity="0.4" />}
        {bbLowerPath && <path d={bbLowerPath} stroke="#8b5cf6" strokeWidth="1" fill="none" opacity="0.3" />}

        {/* 200 EMA */}
        {ema200Path && <path d={ema200Path} stroke="#f59e0b" strokeWidth="1.5" strokeDasharray="6,4" fill="none" opacity="0.35" />}

        {/* 50 EMA */}
        {ema50Path && <path d={ema50Path} stroke="#3b82f6" strokeWidth="2" strokeDasharray="6,4" fill="none" opacity="0.7" />}

        {/* Kijun-sen (Ichimoku 26w base line) — drives v44 ICH dampener */}
        {kijunPath && <path d={kijunPath} stroke="#06b6d4" strokeWidth="1.8" fill="none" opacity="0.65" />}

        {/* Volume bars */}
        <g>
          {priceData.map((c, i) => {
            const x = padding + i * candleSpacing + candleSpacing / 2;
            const barH = (Math.min(c.volume || 0, volCap) / volCap) * volumeZoneHeight;
            return (
              <rect key={i} x={x - candleWidth / 2} y={volBottom - barH} width={candleWidth} height={Math.max(1, barH)}
                fill={c.close >= c.open ? '#10b981' : '#ef4444'} opacity={hoveredIndex === i ? 0.4 : 0.18} />
            );
          })}
          {volMaPath && <path d={volMaPath} stroke="#f59e0b" strokeWidth="1.2" fill="none" opacity="0.5" />}
        </g>

        {/* Candlesticks */}
        {priceData.map((c, i) => {
          const x = padding + i * candleSpacing + candleSpacing / 2;
          const isGreen = c.close >= c.open;
          const isHovered = hoveredIndex === i;
          const color = isGreen ? '#10b981' : '#ef4444';
          const bodyTop = Math.min(getY(c.open), getY(c.close));
          const bodyH = Math.max(1, Math.abs(getY(c.close) - getY(c.open)));
          return (
            <g key={i} opacity={isHovered ? 1 : 0.9}>
              <line x1={x} y1={getY(c.high)} x2={x} y2={getY(c.low)} stroke={color} strokeWidth={isHovered ? 2 : 1} opacity={isHovered ? 1 : 0.8} />
              <rect x={x - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyH} fill={color} stroke={isHovered ? '#ffffff' : color} strokeWidth={isHovered ? 1.5 : 0} />
            </g>
          );
        })}

        {/* Current price line + label */}
        {lastCandle?.close != null && (() => {
          const y = getY(lastCandle.close);
          const lastX = padding + (priceData.length - 1) * candleSpacing + candleSpacing / 2;
          return (
            <g>
              <line x1={padding} y1={y} x2={chartWidth - rightPadding} y2={y} stroke="#6b7280" strokeWidth="1" strokeDasharray="3,3" opacity="0.5" />
              <text x={lastX + candleWidth / 2 + 6} y={y + 4} textAnchor="start" fill="#ffffff" fontSize="11" fontWeight="700" opacity="0.95">
                ${lastCandle.close.toFixed(2)}
              </text>
            </g>
          );
        })()}

        {/* EMA / BB / Kijun legend */}
        <g>
          <line x1={chartWidth - rightPadding + 15} y1={padding + 10} x2={chartWidth - rightPadding + 35} y2={padding + 10} stroke="#3b82f6" strokeWidth="2" strokeDasharray="6,4" opacity="0.7" />
          <text x={chartWidth - rightPadding + 40} y={padding + 14} fill="#9ca3af" fontSize="10">50 EMA</text>
          <line x1={chartWidth - rightPadding + 15} y1={padding + 26} x2={chartWidth - rightPadding + 35} y2={padding + 26} stroke="#f59e0b" strokeWidth="1.5" strokeDasharray="6,4" opacity="0.35" />
          <text x={chartWidth - rightPadding + 40} y={padding + 30} fill="#9ca3af" fontSize="10">200 EMA</text>
          <line x1={chartWidth - rightPadding + 15} y1={padding + 42} x2={chartWidth - rightPadding + 35} y2={padding + 42} stroke="#8b5cf6" strokeWidth="1.5" opacity="0.4" />
          <text x={chartWidth - rightPadding + 40} y={padding + 46} fill="#9ca3af" fontSize="10">BB</text>
          <line x1={chartWidth - rightPadding + 15} y1={padding + 58} x2={chartWidth - rightPadding + 35} y2={padding + 58} stroke="#06b6d4" strokeWidth="1.8" opacity="0.65" />
          <text x={chartWidth - rightPadding + 40} y={padding + 62} fill="#9ca3af" fontSize="10">Kijun-sen</text>
        </g>

        {/* Hover crosshair + tooltip */}
        {hoveredIndex !== null && hoveredIndex >= 0 && hoveredIndex < priceData.length && (() => {
          const hc = priceData[hoveredIndex];
          if (!hc || hc.open == null || hc.close == null) return null;
          const x = padding + hoveredIndex * candleSpacing + candleSpacing / 2;
          // Pull kijun-sen for this date so tooltip can show ICH state
          const indForDate = indicatorData?.[hc.date.split('T')[0]];
          const kijun = indForDate?.kijun_sen;
          const showKijun = kijun != null && hc.close != null;
          const kijunPct = showKijun ? ((hc.close - kijun) / kijun) * 100 : null;
          // ICH state (per v44 mechanism): bullish if price >= kijun, bearish if below
          const ichState = !showKijun ? null
            : kijunPct >= 0 ? 'bullish'
            : 'bearish';
          const ichColor = ichState === 'bullish' ? '#7FBFA0'
            : ichState === 'bearish' ? '#f87171' : '#9ca3af';
          const boxWidth = 178;
          const boxHeight = showKijun ? 144 : 114;
          const boxX = x + 10 > chartWidth - boxWidth - padding ? x - boxWidth - 10 : x + 10;
          const closerToHigh = paddedPriceRange > 0 ? (hc.close - paddedMinPrice) / paddedPriceRange > 0.5 : false;
          const boxY = closerToHigh
            ? Math.min(priceBottom - boxHeight - 4, chartHeight - padding - boxHeight)
            : padding + 10;
          const chgPct = hc.open ? ((hc.close - hc.open) / hc.open * 100) : 0;
          const chgColor = chgPct >= 0 ? '#7FBFA0' : '#f87171';
          const hovDate = parseLocalDate(hc.date);
          return (
            <g>
              <line x1={x} y1={padding} x2={x} y2={chartHeight - padding} stroke="#ffffff" strokeWidth="1" opacity="0.3" strokeDasharray="4,4" />
              <rect x={boxX} y={boxY} width={boxWidth} height={boxHeight} fill="#0b0f17" stroke="rgba(255,255,255,0.10)" strokeWidth="1" rx="6" opacity="0.98" />
              <text x={boxX + 12} y={boxY + 21} fill="#e5e7eb" fontSize="11" fontWeight="600" style={{ letterSpacing: '0.02em' }}>
                {hovDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })}
              </text>
              <line x1={boxX + 10} y1={boxY + 34} x2={boxX + boxWidth - 10} y2={boxY + 34} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
              <text x={boxX + 12} y={boxY + 50} fill="#9ca3af" fontSize="10">Open</text>
              <text x={boxX + boxWidth - 12} y={boxY + 50} textAnchor="end" fill="#e5e7eb" fontSize="10" fontWeight="600" style={mono}>${(hc.open || 0).toFixed(2)}</text>
              <text x={boxX + 12} y={boxY + 64} fill="#9ca3af" fontSize="10">High</text>
              <text x={boxX + boxWidth - 12} y={boxY + 64} textAnchor="end" fill="#7FBFA0" fontSize="10" fontWeight="600" style={mono}>${(hc.high || 0).toFixed(2)}</text>
              <text x={boxX + 12} y={boxY + 78} fill="#9ca3af" fontSize="10">Low</text>
              <text x={boxX + boxWidth - 12} y={boxY + 78} textAnchor="end" fill="#f87171" fontSize="10" fontWeight="600" style={mono}>${(hc.low || 0).toFixed(2)}</text>
              <text x={boxX + 12} y={boxY + 92} fill="#9ca3af" fontSize="10">Close</text>
              <text x={boxX + boxWidth - 12} y={boxY + 92} textAnchor="end" fill="#e5e7eb" fontSize="10" fontWeight="600" style={mono}>${(hc.close || 0).toFixed(2)}</text>
              <text x={boxX + 12} y={boxY + 106} fill={chgColor} fontSize="10" fontWeight="600" style={mono}>{chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%</text>
              <text x={boxX + boxWidth - 12} y={boxY + 106} textAnchor="end" fill="#9ca3af" fontSize="10" style={mono}>{formatVol(hc.volume || 0)}</text>
              {showKijun && (
                <>
                  <line x1={boxX + 10} y1={boxY + 116} x2={boxX + boxWidth - 10} y2={boxY + 116} stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
                  <text x={boxX + 12} y={boxY + 130} fill="#06b6d4" fontSize="10">Kijun</text>
                  <text x={boxX + boxWidth - 12} y={boxY + 130} textAnchor="end" fill="#06b6d4" fontSize="10" fontWeight="600" style={mono}>
                    ${kijun.toFixed(2)} ({kijunPct >= 0 ? '+' : ''}{kijunPct.toFixed(1)}%)
                  </text>
                  <text x={boxX + boxWidth - 12} y={boxY + 138} textAnchor="end" fill={ichColor} fontSize="9" fontWeight="600" style={mono}>
                    ICH: {ichState}
                  </text>
                </>
              )}
            </g>
          );
        })()}
      </svg>
    </div>
  );
};

export default WeeklyPriceChart;
