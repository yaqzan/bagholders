import React, { useEffect, useState, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useStock } from '../context/StockContext';
import { parseLocalDate, shouldRefreshOnResume } from '../utils/timeUtils';
import ScoreChart, { buildScoreBreakdownSections } from '../components/ScoreChart';
import PriceChart from '../components/PriceChart';
import RsiChart from '../components/RsiChart';
import MacdChart from '../components/MacdChart';
import WeeklyPriceChart from '../components/WeeklyPriceChart';
import WeeklyMacdChart from '../components/WeeklyMacdChart';
import WeeklyRsiChart from '../components/WeeklyRsiChart';
import WeeklyScoreChart from '../components/WeeklyScoreChart';
import DteRecommendation from '../components/DteRecommendation';
import BucketWinRates from '../components/BucketWinRates';

// Width shared by all mobile charts so their x-axes stay aligned.
const MOBILE_CHART_W = 1800;
const MOBILE_CHART_POINTS = 120;
const MOBILE_PRICE_PANEL_FLEX = '0 0 60%';
const MOBILE_SCORE_PANEL_FLEX = '1 1 40%';
const CALL_SIGNAL_MIN = 70;
const CALL_STRONG_MIN = 75;
const PUT_SIGNAL_MAX = 25;

const fmtMobilePx = (px) => {
  const n = Number(px);
  if (!Number.isFinite(n)) return '--';
  return n < 10 ? n.toFixed(3) : n.toFixed(2);
};

const fmtMobileDate = (dateStr) => {
  if (!dateStr) return '--';
  return parseLocalDate(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

const fmtMobileVol = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toFixed(0);
};

const mobileScoreClass = (score) => {
  if (score == null) return 'text-gray-400';
  if (score >= CALL_STRONG_MIN) return 'text-trading-green-300';
  if (score >= CALL_SIGNAL_MIN) return 'text-trading-green-500';
  if (score <= PUT_SIGNAL_MAX) return 'text-trading-red-300';
  return 'text-gray-300';
};

const MobileInfoRail = ({ children }) => (
  <div className="w-[96px] shrink-0 border-l border-white/[0.06] bg-trading-dark-950/95 px-2 py-1.5 overflow-hidden">
    {children}
  </div>
);

const MobilePriceReadout = ({ candle, score }) => {
  const chgPct = candle?.open ? ((candle.close - candle.open) / candle.open) * 100 : null;
  const chgClass = chgPct == null ? 'text-gray-500' : chgPct >= 0 ? 'text-trading-green-300' : 'text-trading-red-300';
  return (
    <MobileInfoRail>
      <div className="text-[10px] font-semibold text-gray-200 leading-none">{fmtMobileDate(candle?.date)}</div>
      <div className={`mt-1 text-[18px] font-semibold leading-none tabular-nums ${mobileScoreClass(score?.overall)}`}>
        {score?.overall != null ? Math.round(score.overall) : '--'}
      </div>
      <div className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-1 gap-y-0.5 text-[9px] leading-tight tabular-nums">
        <span className="text-gray-600">O</span><span className="text-right text-gray-300">{fmtMobilePx(candle?.open)}</span>
        <span className="text-gray-600">H</span><span className="text-right text-trading-green-300">{fmtMobilePx(candle?.high)}</span>
        <span className="text-gray-600">L</span><span className="text-right text-trading-red-300">{fmtMobilePx(candle?.low)}</span>
        <span className="text-gray-600">C</span><span className="text-right text-gray-100">{fmtMobilePx(candle?.close)}</span>
      </div>
      <div className={`mt-1 text-[10px] leading-none tabular-nums ${chgClass}`}>
        {chgPct == null ? '--' : `${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(2)}%`}
      </div>
      <div className="mt-1 text-[9px] leading-none tabular-nums text-gray-500">{fmtMobileVol(candle?.volume)}</div>
    </MobileInfoRail>
  );
};

const MobileScoreReadout = ({ score }) => {
  const sections = buildScoreBreakdownSections(score);

  return (
    <div className="w-[132px] shrink-0 border-l border-white/[0.06] bg-trading-dark-950/95 px-2 py-1.5 overflow-y-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
      <div className="sticky top-0 -mx-2 -mt-1.5 mb-1 border-b border-white/[0.06] bg-trading-dark-950/95 px-2 py-1.5 text-[10px] font-semibold text-gray-200 leading-none">
        {fmtMobileDate(score?.date)}
      </div>
      <div className="space-y-1.5 pb-2">
        {sections.map((group) => (
          <div key={group.title}>
            <div className="mb-0.5 text-[8px] font-semibold uppercase tracking-wide text-gray-500">{group.title}</div>
            <div className="grid grid-cols-[auto_1fr] gap-x-1.5 gap-y-0.5 text-[8.5px] leading-tight tabular-nums">
              {group.rows.map(([label, value]) => (
                <React.Fragment key={`${group.title}-${label}`}>
                  <span className="min-w-0 truncate text-gray-600">{label}</span>
                  <span className="min-w-0 truncate text-right text-gray-300">{value}</span>
                </React.Fragment>
              ))}
            </div>
          </div>
        ))}
        {sections.length === 0 && (
          <div className="text-[9px] leading-tight text-gray-500">No scoring breakdown</div>
        )}
      </div>
    </div>
  );
};

const MobileDailyChartPanel = ({
  symbol,
  stockDetail,
  displayedScores,
  hoveredIndex,
  setHoveredIndex,
  handleChartScroll,
  handlePriceLoad,
  handleMobilePriceData,
  priceScrollRef,
  scoreScrollRef,
  projections,
  chartRefreshNonce,
  selectedPriceCandle,
  selectedScoreRow,
}) => (
  <div className="flex h-full min-h-0 flex-col overflow-hidden border-y border-white/[0.06]">
    <div
      className="min-h-0 flex border-b border-white/[0.06]"
      style={{ flex: displayedScores.length > 0 ? MOBILE_PRICE_PANEL_FLEX : '1 1 100%' }}
    >
      <div
        ref={priceScrollRef}
        className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
        style={{
          WebkitOverflowScrolling: 'touch',
          touchAction: 'pan-x',
          overscrollBehaviorX: 'contain',
        }}
        onScroll={handleChartScroll}
      >
        <div style={{ width: MOBILE_CHART_W, minWidth: MOBILE_CHART_W, height: '100%' }}>
          <PriceChart
            symbol={symbol}
            stockName={stockDetail?.name}
            onHoverIndex={setHoveredIndex}
            hoveredIndex={hoveredIndex}
            scores={displayedScores}
            stockDetail={stockDetail}
            onLoad={handlePriceLoad}
            onData={handleMobilePriceData}
            projections={projections}
            externalScroll
            height="100%"
            refreshNonce={chartRefreshNonce}
            historyLimit={MOBILE_CHART_POINTS}
            showInlineTooltip={false}
          />
        </div>
      </div>
      <MobilePriceReadout candle={selectedPriceCandle} score={selectedScoreRow} />
    </div>
    {displayedScores.length > 0 && (
      <div className="min-h-0 flex" style={{ flex: MOBILE_SCORE_PANEL_FLEX }}>
        <div
          ref={scoreScrollRef}
          className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
          style={{
            WebkitOverflowScrolling: 'touch',
            touchAction: 'pan-x',
            overscrollBehaviorX: 'contain',
          }}
          onScroll={handleChartScroll}
        >
          <div style={{ width: MOBILE_CHART_W, minWidth: MOBILE_CHART_W, height: '100%' }}>
            <ScoreChart
              scores={displayedScores}
              onHoverIndex={setHoveredIndex}
              hoveredIndex={hoveredIndex}
              height="100%"
              showTooltip={false}
            />
          </div>
        </div>
        <MobileScoreReadout score={selectedScoreRow} />
      </div>
    )}
  </div>
);

const StockDetail = () => {
  const { symbol } = useParams();
  const {
    stockDetail,
    stockScores,
    stocks,
    loading,
    error,
    fetchStockDetail,
    fetchStockScores,
    assessmentData
  } = useStock();
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const [weeklyHoveredIndex, setWeeklyHoveredIndex] = useState(null);
  const [activeTab, setActiveTab] = useState('daily');
  const [chartsLoaded, setChartsLoaded] = useState({
    price: false,
    macd: false,
    rsi: false
  });
  const [weeklyChartsLoaded, setWeeklyChartsLoaded] = useState({
    price: false,
    macd: false,
    rsi: false
  });
  const [weeklyTabAccessed, setWeeklyTabAccessed] = useState(false);
  const [chartRefreshNonce, setChartRefreshNonce] = useState(0);
  const [mobilePriceData, setMobilePriceData] = useState([]);

  // Each daily chart on mobile gets its own overflow-x:auto container so the
  // page can scroll vertically between them.  A single shared container blocked
  // vertical touch scroll because overflow-x:auto implicitly sets overflow-y to
  // auto too, confusing iOS touch routing even with touchAction:'pan-x'.
  // We keep x-axes aligned by syncing all containers' scrollLeft positions.
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640;
  const priceScrollRef = React.useRef(null);
  const scoreScrollRef = React.useRef(null);
  const macdScrollRef  = React.useRef(null);
  const rsiScrollRef   = React.useRef(null);
  const isSyncingScroll = React.useRef(false);

  const mobileScrollRefs = React.useRef([priceScrollRef, scoreScrollRef, macdScrollRef, rsiScrollRef]);
  const displayedScores = React.useMemo(
    () => (isMobile ? (stockScores || []).slice(0, MOBILE_CHART_POINTS) : (stockScores || [])),
    [isMobile, stockScores]
  );
  const displayedScoresChronological = React.useMemo(
    () => [...displayedScores].reverse(),
    [displayedScores]
  );
  const mobileSeriesLengths = [mobilePriceData.length, displayedScoresChronological.length].filter(n => n > 0);
  const maxMobileSelectedIndex = mobileSeriesLengths.length > 0 ? Math.min(...mobileSeriesLengths) - 1 : -1;
  const selectedMobileIndex = maxMobileSelectedIndex >= 0
    ? Math.max(0, Math.min(hoveredIndex ?? maxMobileSelectedIndex, maxMobileSelectedIndex))
    : 0;
  const selectedPriceCandle = mobilePriceData[selectedMobileIndex] || mobilePriceData[mobilePriceData.length - 1] || null;
  const selectedScoreRow = displayedScoresChronological[selectedMobileIndex] || displayedScoresChronological[displayedScoresChronological.length - 1] || null;

  // When the user scrolls any chart, mirror the position to all others.
  const handleChartScroll = React.useCallback((e) => {
    if (isSyncingScroll.current) return;
    const sl = e.currentTarget.scrollLeft;
    isSyncingScroll.current = true;
    mobileScrollRefs.current.forEach(r => {
      if (r.current && r.current !== e.currentTarget) r.current.scrollLeft = sl;
    });
    isSyncingScroll.current = false;
  }, []);

  const handlePriceLoad = React.useCallback(() => {
    setChartsLoaded(prev => ({ ...prev, price: true }));
    if (isMobile && priceScrollRef.current) {
      requestAnimationFrame(() => {
        const el = priceScrollRef.current;
        if (!el) return;
        // Center on the last real candle (PriceChart reserves 65 future days).
        const leftPad = 60;
        const futureDays = 65;
        const approxDataLen = MOBILE_CHART_POINTS;
        const rightPad = Math.round(
          (MOBILE_CHART_W - leftPad) * futureDays / (approxDataLen + futureDays)
        );
        const lastCandleX = MOBILE_CHART_W - rightPad;
        const target = Math.max(0, lastCandleX - el.clientWidth / 2);
        el.scrollLeft = target;
        // Sync any already-mounted sibling containers.
        mobileScrollRefs.current.forEach(r => {
          if (r.current && r.current !== el) r.current.scrollLeft = target;
        });
      });
    }
  }, [isMobile]);

  const handleMacdLoad = React.useCallback(() => {
    setChartsLoaded(prev => ({ ...prev, macd: true }));
    // Sync MACD container's scroll to match PriceChart when it first mounts.
    if (isMobile) {
      requestAnimationFrame(() => {
        if (priceScrollRef.current && macdScrollRef.current)
          macdScrollRef.current.scrollLeft = priceScrollRef.current.scrollLeft;
      });
    }
  }, [isMobile]);

  const handleRsiLoad = React.useCallback(() => {
    setChartsLoaded(prev => ({ ...prev, rsi: true }));
    if (isMobile) {
      requestAnimationFrame(() => {
        if (priceScrollRef.current && rsiScrollRef.current)
          rsiScrollRef.current.scrollLeft = priceScrollRef.current.scrollLeft;
      });
    }
  }, [isMobile]);

  const handleMobilePriceData = React.useCallback((data) => {
    setMobilePriceData(data || []);
  }, []);

  // Sync ScoreChart container when it becomes visible (no onLoad prop on ScoreChart).
  React.useEffect(() => {
    if (!isMobile || !chartsLoaded.price) return;
    let timeoutId = null;
    const syncScoreScroll = () => {
      if (priceScrollRef.current && scoreScrollRef.current) {
        scoreScrollRef.current.scrollLeft = priceScrollRef.current.scrollLeft;
      }
    };
    const rafId = requestAnimationFrame(() => {
      syncScoreScroll();
      timeoutId = setTimeout(syncScoreScroll, 120);
    });
    return () => {
      cancelAnimationFrame(rafId);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [chartsLoaded.price, displayedScores.length, isMobile, mobilePriceData.length]);

  React.useEffect(() => {
    if (!isMobile || activeTab !== 'daily') return;
    const lengths = [mobilePriceData.length, displayedScoresChronological.length].filter(n => n > 0);
    const maxIndex = lengths.length > 0 ? Math.min(...lengths) - 1 : -1;
    if (maxIndex < 0) return;
    if (hoveredIndex == null || hoveredIndex > maxIndex) {
      setHoveredIndex(maxIndex);
    }
  }, [activeTab, displayedScoresChronological.length, hoveredIndex, isMobile, mobilePriceData.length]);

  const handleWeeklyPriceLoad = React.useCallback(() => {
    setWeeklyChartsLoaded(prev => ({ ...prev, price: true }));
  }, []);

  const handleWeeklyMacdLoad = React.useCallback(() => {
    setWeeklyChartsLoaded(prev => ({ ...prev, macd: true }));
  }, []);

  const handleWeeklyRsiLoad = React.useCallback(() => {
    setWeeklyChartsLoaded(prev => ({ ...prev, rsi: true }));
  }, []);

  useEffect(() => {
    if (symbol) {
      setChartsLoaded({ price: false, macd: false, rsi: false });
      setWeeklyChartsLoaded({ price: false, macd: false, rsi: false });
      setHoveredIndex(null);
      setMobilePriceData([]);
      setWeeklyTabAccessed(false);
      setActiveTab('daily');
      fetchStockDetail(symbol);
      fetchStockScores(symbol, 250);
    }
  }, [symbol, fetchStockDetail, fetchStockScores]);

  // Gather every signal score (>=70 call, <=20 put) from the last 60 days.
  // PriceChart uses these to draw glow halos on the signal candles and a
  // combined cone-of-opportunity illumination across all signals.
  const projections = useMemo(() => {
    if (!stockScores || stockScores.length === 0 || !assessmentData?.buckets) {
      return null;
    }

    const today = parseLocalDate(new Date().toISOString().split('T')[0]);
    const sixtyDaysAgo = new Date(today);
    sixtyDaysAgo.setDate(sixtyDaysAgo.getDate() - 60);

    const signals = stockScores
      .filter((s) => {
        const scoreDate = parseLocalDate(s.date);
        return scoreDate >= sixtyDaysAgo && scoreDate <= today;
      })
      .filter((s) => s.overall >= CALL_SIGNAL_MIN || s.overall <= 20)
      .map((s) => ({
        signalDate: s.date.split('T')[0],
        signalScore: s.overall,
      }));

    if (signals.length === 0) {
      return null;
    }

    return {
      signals,
      assessmentData: assessmentData.buckets,
      realizedVol: stockDetail?.realized_vol_60d,
    };
  }, [stockScores, assessmentData, stockDetail]);

  const dashboardStock = useMemo(
    () => (stocks || []).find((stock) => stock.symbol === symbol),
    [stocks, symbol]
  );
  const stockFreshnessUpdatedAt =
    stockScores?.[0]?.updated_at
    || stockDetail?.latest_score?.updated_at
    || dashboardStock?.updated_at
    || null;
  const stockFreshnessRef = React.useRef(stockFreshnessUpdatedAt);
  const lastStockRefreshRef = React.useRef(Date.now());
  const stockResumeRefreshInProgress = React.useRef(false);
  stockFreshnessRef.current = stockFreshnessUpdatedAt;

  React.useEffect(() => {
    lastStockRefreshRef.current = Date.now();
  }, [symbol, stockFreshnessUpdatedAt]);

  const refreshStockIfStale = React.useCallback(async () => {
    if (!symbol || stockResumeRefreshInProgress.current) return;

    const hasStaleTimestamp = shouldRefreshOnResume(stockFreshnessRef.current);
    const hasAgedWithoutTimestamp =
      !stockFreshnessRef.current && Date.now() - lastStockRefreshRef.current > 5 * 60 * 1000;
    if (!hasStaleTimestamp && !hasAgedWithoutTimestamp) return;

    stockResumeRefreshInProgress.current = true;
    lastStockRefreshRef.current = Date.now();
    setChartsLoaded({ price: false, macd: false, rsi: false });
    setWeeklyChartsLoaded({ price: false, macd: false, rsi: false });
    setChartRefreshNonce(n => n + 1);
    try {
      await Promise.all([
        fetchStockDetail(symbol),
        fetchStockScores(symbol, 250),
      ]);
    } finally {
      stockResumeRefreshInProgress.current = false;
    }
  }, [symbol, fetchStockDetail, fetchStockScores]);

  React.useEffect(() => {
    const onResume = () => {
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      void refreshStockIfStale();
    };
    document.addEventListener('visibilitychange', onResume);
    window.addEventListener('focus', onResume);
    window.addEventListener('pageshow', onResume);
    return () => {
      document.removeEventListener('visibilitychange', onResume);
      window.removeEventListener('focus', onResume);
      window.removeEventListener('pageshow', onResume);
    };
  }, [refreshStockIfStale]);

  // Enable weekly tab loading when daily charts are loaded or when weekly tab is accessed
  const shouldLoadWeekly = weeklyTabAccessed || chartsLoaded.rsi;

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    if (tab === 'weekly') {
      setWeeklyTabAccessed(true);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-6 w-6 border-b border-gray-500"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-trading-red-400">Error loading stock: {error}</p>
      </div>
    );
  }

  if (!stockDetail) {
    return (
      <div className="text-center py-8">
        <p className="text-gray-500">Stock not found</p>
      </div>
    );
  }

  const latestScore = stockScores[0];
  const currentPrice = stockDetail?.current_price ?? stockDetail?.latest_price_data?.close;
  const dayChangePct = stockDetail?.daily_percentage_change;

  // TP/SL underlying price levels for mobile header (30DTE strategy, v39 ship state).
  // Sigma thresholds: underlying move in σ_daily units that triggers TP or SL.
  let tpPrice = null;
  let slPrice = null;
  const vol60d = stockDetail?.realized_vol_60d; // 60-day realized daily stdev in %
  if (vol60d != null && currentPrice != null && latestScore?.overall != null) {
    const v = vol60d / 100;
    if (latestScore.overall >= CALL_SIGNAL_MIN) {
      tpPrice = currentPrice * (1 + 1.201 * v); // call TP: +1.201σ
      slPrice = currentPrice * (1 - 0.983 * v); // call SL: −0.983σ
    } else if (latestScore.overall <= PUT_SIGNAL_MAX) {
      tpPrice = currentPrice * (1 - 1.274 * v); // put TP: −1.274σ (price falls)
      slPrice = currentPrice * (1 + 0.728 * v); // put SL: +0.728σ (price rises)
    }
  }

  const fmtPx = (px) => px == null ? null : (px < 10 ? px.toFixed(3) : px.toFixed(2));

  const delistedDate = stockDetail?.delisted_date;

  return (
    <div className="h-full min-h-0 flex flex-col bg-trading-dark-950">
      {/* Mobile: stock identity in the fixed hamburger row.
          pl-14 clears the hamburger.
          Layout (L→R): score · symbol+name · price+change · TP/SL */}
      <div className="sm:hidden fixed top-0 left-0 right-0 z-30 h-12 flex items-center pt-2 pl-14 pr-3 gap-2 border-b border-white/[0.06] bg-trading-dark-950">

        {/* Score — left of symbol, color-coded by signal strength */}
        {latestScore?.overall != null && (
          <span className={`text-[12px] font-semibold tabular-nums shrink-0 ${
            latestScore.overall >= CALL_STRONG_MIN ? 'text-trading-green-400' :
            latestScore.overall >= CALL_SIGNAL_MIN ? 'text-trading-green-500' :
            latestScore.overall <= PUT_SIGNAL_MAX ? 'text-trading-red-400' :
            'text-gray-500'
          }`}>
            {latestScore.overall}
          </span>
        )}

        {/* Symbol + Name stacked, takes remaining space */}
        <div className="flex flex-col justify-center min-w-0 flex-1">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-bold text-white text-[13px] leading-none">{symbol}</span>
            {delistedDate && (
              <span
                className="text-[8px] uppercase tracking-wide font-semibold text-amber-300/90 bg-amber-500/15 border border-amber-500/30 rounded px-1 py-px leading-none shrink-0"
                title={`Delisted ${delistedDate}. Historical data only — no live updates.`}
              >
                Delisted
              </span>
            )}
          </div>
          {stockDetail?.name && (
            <span className="text-gray-500 text-[10px] truncate leading-tight mt-px">{stockDetail.name}</span>
          )}
        </div>

        {/* Current price + day change inline — most prominent element */}
        {currentPrice != null && (
          <div className="flex items-baseline gap-1 shrink-0">
            <span className="text-white text-[13px] tabular-nums font-semibold leading-none">
              ${fmtPx(currentPrice)}
            </span>
            {dayChangePct != null && (
              <span className={`text-[10px] tabular-nums font-medium leading-none ${
                dayChangePct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'
              }`}>
                {dayChangePct >= 0 ? '+' : ''}{dayChangePct.toFixed(2)}%
              </span>
            )}
          </div>
        )}

        {/* TP / SL price levels — muted, right-aligned, kept present in the mobile header */}
        <div className="flex flex-col items-end shrink-0 gap-px">
          <span className="text-[9px] tabular-nums text-trading-green-400/60 leading-none">
            TP {tpPrice != null ? `$${fmtPx(tpPrice)}` : '--'}
          </span>
          <span className="text-[9px] tabular-nums text-trading-red-400/60 leading-none">
            SL {slPrice != null ? `$${fmtPx(slPrice)}` : '--'}
          </span>
        </div>

      </div>

      {/* Delisted banner — historical-data-only notice for delisted symbols */}
      {delistedDate && (
        <div className="px-4 py-1.5 bg-amber-500/10 border-b border-amber-500/30 text-[11px] text-amber-200/90 flex items-center gap-2">
          <span className="font-semibold uppercase tracking-wide text-amber-300 text-[10px]">Delisted {delistedDate}</span>
          <span className="text-amber-200/70">— historical data only. No live price or score updates.</span>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="hidden sm:flex border-b border-white/[0.06] px-4">
        <button
          onClick={() => handleTabChange('daily')}
          className={`relative px-5 py-2.5 text-[12px] transition-colors ${
            activeTab === 'daily'
              ? 'text-white font-medium'
              : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Daily
          {activeTab === 'daily' && (
            <span className="absolute left-3 right-3 -bottom-[1px] h-[1px] bg-white" aria-hidden />
          )}
        </button>
        <button
          onClick={() => handleTabChange('weekly')}
          className={`relative px-5 py-2.5 text-[12px] transition-colors ${
            activeTab === 'weekly'
              ? 'text-white font-medium'
              : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Weekly
          {activeTab === 'weekly' && (
            <span className="absolute left-3 right-3 -bottom-[1px] h-[1px] bg-white" aria-hidden />
          )}
        </button>
      </div>

      {/* Charts Section */}
      <div
        className={`flex-1 min-h-0 ${isMobile ? 'overflow-hidden' : 'overflow-y-auto'}`}
        style={{ WebkitOverflowScrolling: 'touch', overscrollBehaviorY: 'contain' }}
      >
        <div className="h-full min-h-0 bg-trading-dark-950">
          {/* Daily Tab */}
          {activeTab === 'daily' && (
            <div className="h-full min-h-0">
              {!isMobile && <BucketWinRates symbol={symbol} />}

              {/* Mobile: price and OVR share the first viewport as stacked panels,
                  with horizontal scrollLeft mirrored across charts. */}
              {isMobile ? (
                <MobileDailyChartPanel
                  symbol={symbol}
                  stockDetail={stockDetail}
                  displayedScores={displayedScores}
                  hoveredIndex={hoveredIndex}
                  setHoveredIndex={setHoveredIndex}
                  handleChartScroll={handleChartScroll}
                  handlePriceLoad={handlePriceLoad}
                  handleMobilePriceData={handleMobilePriceData}
                  priceScrollRef={priceScrollRef}
                  scoreScrollRef={scoreScrollRef}
                  projections={projections}
                  chartRefreshNonce={chartRefreshNonce}
                  selectedPriceCandle={selectedPriceCandle}
                  selectedScoreRow={selectedScoreRow}
                />
              ) : (
                <>
                  <PriceChart
                    symbol={symbol}
                    stockName={stockDetail?.name}
                    onHoverIndex={setHoveredIndex}
                    hoveredIndex={hoveredIndex}
                    scores={displayedScores}
                    stockDetail={stockDetail}
                    onLoad={handlePriceLoad}
                    projections={projections}
                    refreshNonce={chartRefreshNonce}
                  />
                  <DteRecommendation
                    score={latestScore?.overall}
                    realizedVol60d={stockDetail?.realized_vol_60d}
                    currentPrice={stockDetail?.current_price ?? stockDetail?.latest_price_data?.close}
                  />
                  {chartsLoaded.price && stockScores.length > 0 && (
                    <div className="-mt-4 pt-0">
                      <ScoreChart
                        scores={displayedScores}
                        onHoverIndex={setHoveredIndex}
                        hoveredIndex={hoveredIndex}
                      />
                    </div>
                  )}
                  {chartsLoaded.price && (
                    <div className="-mt-4 pt-0">
                      <MacdChart
                        symbol={symbol}
                        onHoverIndex={setHoveredIndex}
                        hoveredIndex={hoveredIndex}
                        onLoad={handleMacdLoad}
                        scores={displayedScores}
                      />
                    </div>
                  )}
                  {chartsLoaded.macd && (
                    <div className="-mt-4 pt-0">
                      <RsiChart
                        symbol={symbol}
                        onHoverIndex={setHoveredIndex}
                        hoveredIndex={hoveredIndex}
                        onLoad={handleRsiLoad}
                        scores={displayedScores}
                      />
                    </div>
                  )}
                </>
              )}

            </div>
          )}

          {/* Weekly Tab — order mirrors daily: Price → Composite (OVR) → MACD → RSI */}
          {activeTab === 'weekly' && shouldLoadWeekly && (
            <>
              <WeeklyPriceChart
                symbol={symbol}
                stockName={stockDetail?.name}
                onHoverIndex={setWeeklyHoveredIndex}
                hoveredIndex={weeklyHoveredIndex}
                onLoad={handleWeeklyPriceLoad}
              />

              {/* Composite score directly below price chart — acts as the weekly OVR */}
              {weeklyChartsLoaded.price && (
                <div className="-mt-4 pt-0">
                  <WeeklyScoreChart
                    symbol={symbol}
                    onHoverIndex={setWeeklyHoveredIndex}
                    hoveredIndex={weeklyHoveredIndex}
                  />
                </div>
              )}

              {weeklyChartsLoaded.price && (
                <div className="-mt-4 pt-0">
                  <WeeklyMacdChart
                    symbol={symbol}
                    onHoverIndex={setWeeklyHoveredIndex}
                    hoveredIndex={weeklyHoveredIndex}
                    onLoad={handleWeeklyMacdLoad}
                  />
                </div>
              )}

              {weeklyChartsLoaded.macd && (
                <div className="-mt-4 pt-0">
                  <WeeklyRsiChart
                    symbol={symbol}
                    onHoverIndex={setWeeklyHoveredIndex}
                    hoveredIndex={weeklyHoveredIndex}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default StockDetail;
