import React, { createContext, useContext, useReducer, useEffect, useCallback } from 'react';
import axios from 'axios';
import { calculateGrowthInterpretation, calculateGrowthRateInterpretation, getGrowthScoreInterpretation, formatEarningsDays, formatTargetPercentage } from '../utils/stockUtils';

const StockContext = createContext();

// API base URL - can be configured
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';

// Determine if we're running locally or in production
const isLocalDevelopment = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

// API path prefix - local development uses /api/, production uses no prefix
const API_PATH_PREFIX = '/api';

// Helper function to build API URLs.
// Appends a 30-second cache-bust bucket so CDN/browser caches don't serve
// stale data after a backend pull. Bucketing dedupes burst requests within
// the same window while still bypassing typical CDN cache lifetimes.
const buildApiUrl = (endpoint) => {
  const sep = endpoint.includes('?') ? '&' : '?';
  const bucket = Math.floor(Date.now() / 30000);
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}${sep}_t=${bucket}`;
};

// Initial state
const initialState = {
  stocks: [],
  loading: false,
  error: null,
  selectedStock: null,
  stockDetail: null,
  stockScores: [],
  historicPeaks: [],
  historicPeaksLoading: false,
  assessmentData: null,
  selectedScoreVersionId: null,
  scoreVersion: null,
  activeScoreVersionId: null,
  availableScoreVersions: [],
  legacyScoreVersions: [],
  scoreDate: null,
  stockCounts: {
    total: 0,
    withRevenue: 0,
    withScores: 0,
    coverageTotal: 0,
    coverageWithScores: 0,
    scoreCoverageDate: null,
    highScore: 0,
    lowScore: 0,
    avgScore: 0,
    positiveChange: 0,
    negativeChange: 0,
    neutralChange: 0
  },
  filters: {
    search: '',
    sortBy: 'default',
    sortOrder: 'desc',
    flaggedOnly: false,
    callsOnly: true,
    putsOnly: false
  }
};

// Action types
const ACTIONS = {
  SET_LOADING: 'SET_LOADING',
  SET_ERROR: 'SET_ERROR',
  SET_STOCKS: 'SET_STOCKS',
  SET_SELECTED_STOCK: 'SET_SELECTED_STOCK',
  SET_STOCK_DETAIL: 'SET_STOCK_DETAIL',
  SET_STOCK_SCORES: 'SET_STOCK_SCORES',
  SET_STOCK_COUNTS: 'SET_STOCK_COUNTS',
  SET_FILTERS: 'SET_FILTERS',
  CLEAR_ERROR: 'CLEAR_ERROR',
  SET_HISTORIC_PEAKS: 'SET_HISTORIC_PEAKS',
  SET_HISTORIC_PEAKS_LOADING: 'SET_HISTORIC_PEAKS_LOADING',
  SET_ASSESSMENT_DATA: 'SET_ASSESSMENT_DATA',
  UPDATE_SINGLE_STOCK: 'UPDATE_SINGLE_STOCK',
  SET_SCORE_VERSION: 'SET_SCORE_VERSION',
  SET_DASHBOARD_SCORE_META: 'SET_DASHBOARD_SCORE_META',
};

// Reducer
function stockReducer(state, action) {
  switch (action.type) {
    case ACTIONS.SET_LOADING:
      return { ...state, loading: action.payload };
    case ACTIONS.SET_ERROR:
      return { ...state, error: action.payload, loading: false };
    case ACTIONS.SET_STOCKS:
      return { ...state, stocks: action.payload || [], loading: false };
    case ACTIONS.SET_SELECTED_STOCK:
      return { ...state, selectedStock: action.payload };
    case ACTIONS.SET_STOCK_DETAIL:
      return { ...state, stockDetail: action.payload, loading: false };
    case ACTIONS.SET_STOCK_SCORES:
      return { ...state, stockScores: action.payload || [], loading: false };
    case ACTIONS.SET_STOCK_COUNTS:
      return { ...state, stockCounts: action.payload };
    case ACTIONS.SET_FILTERS:
      return { ...state, filters: { ...state.filters, ...action.payload } };
    case ACTIONS.CLEAR_ERROR:
      return { ...state, error: null };
    case ACTIONS.SET_HISTORIC_PEAKS:
      return { ...state, historicPeaks: action.payload || [], historicPeaksLoading: false };
    case ACTIONS.SET_HISTORIC_PEAKS_LOADING:
      return { ...state, historicPeaksLoading: action.payload };
    case ACTIONS.SET_ASSESSMENT_DATA:
      return { ...state, assessmentData: action.payload };
    case ACTIONS.UPDATE_SINGLE_STOCK:
      return { ...state, stocks: state.stocks.map(s => s.symbol === action.payload.symbol ? action.payload.stock : s) };
    case ACTIONS.SET_SCORE_VERSION:
      return { ...state, selectedScoreVersionId: action.payload };
    case ACTIONS.SET_DASHBOARD_SCORE_META:
      return {
        ...state,
        scoreVersion: action.payload.scoreVersion !== undefined ? action.payload.scoreVersion : state.scoreVersion,
        activeScoreVersionId: action.payload.activeScoreVersionId !== undefined ? action.payload.activeScoreVersionId : state.activeScoreVersionId,
        availableScoreVersions: action.payload.availableScoreVersions !== undefined ? action.payload.availableScoreVersions : state.availableScoreVersions,
        legacyScoreVersions: action.payload.legacyScoreVersions !== undefined ? action.payload.legacyScoreVersions : state.legacyScoreVersions,
        scoreDate: action.payload.scoreDate !== undefined ? action.payload.scoreDate : state.scoreDate,
      };
    default:
      return state;
  }
}

// Provider component
export function StockProvider({ children }) {
  const [state, dispatch] = useReducer(stockReducer, initialState);

  const dashboardVersionParam = state.selectedScoreVersionId
    ? `version=${encodeURIComponent(state.selectedScoreVersionId)}`
    : '';

  const addDashboardVersion = useCallback((endpoint) => {
    if (!dashboardVersionParam) return endpoint;
    const sep = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${sep}${dashboardVersionParam}`;
  }, [dashboardVersionParam]);

  const updateDashboardScoreMeta = useCallback((data) => {
    if (!data) return;
    dispatch({
      type: ACTIONS.SET_DASHBOARD_SCORE_META,
      payload: {
        scoreVersion: data.version !== undefined ? data.version : undefined,
        activeScoreVersionId: data.active_version_id !== undefined ? data.active_version_id : undefined,
        availableScoreVersions: data.available_versions !== undefined ? data.available_versions : undefined,
        legacyScoreVersions: data.legacy_versions !== undefined ? data.legacy_versions : undefined,
        scoreDate: data.score_coverage_date ?? data.date,
      },
    });
  }, []);

  // Fetch basic stock counts (fast)
  const fetchBasicStockCounts = useCallback(async () => {
    try {
      const response = await axios.get(buildApiUrl(addDashboardVersion('/stocks/count')));
      updateDashboardScoreMeta(response.data);
      dispatch({ type: ACTIONS.SET_STOCK_COUNTS, payload: {
        total: response.data.total_stocks,
        withRevenue: response.data.stocks_with_revenue,
        withScores: response.data.stocks_with_scores,
        coverageTotal: response.data.score_coverage_total_stocks ?? response.data.total_stocks,
        coverageWithScores: response.data.score_coverage_stocks_with_scores ?? response.data.stocks_with_scores,
        scoreCoverageDate: response.data.score_coverage_date || null,
        highScore: 0, // Will be loaded separately if needed
        lowScore: 0,  // Will be loaded separately if needed
        avgScore: 0,  // Will be loaded separately if needed
        positiveChange: 0, // Will be loaded separately if needed
        negativeChange: 0, // Will be loaded separately if needed
        neutralChange: 0   // Will be loaded separately if needed
      }});
    } catch (error) {
      console.error('Error fetching basic stock counts:', error);
    }
  }, [addDashboardVersion, updateDashboardScoreMeta]);

  // Fetch comprehensive statistics (slower, only when needed)
  const fetchComprehensiveStats = useCallback(async () => {
    try {
      const response = await axios.get(buildApiUrl(addDashboardVersion('/stocks/count')));
      updateDashboardScoreMeta(response.data);
      dispatch({ type: ACTIONS.SET_STOCK_COUNTS, payload: {
        total: response.data.total_stocks,
        withRevenue: response.data.stocks_with_revenue,
        withScores: response.data.stocks_with_scores,
        coverageTotal: response.data.score_coverage_total_stocks ?? response.data.total_stocks,
        coverageWithScores: response.data.score_coverage_stocks_with_scores ?? response.data.stocks_with_scores,
        scoreCoverageDate: response.data.score_coverage_date || null,
        highScore: response.data.high_score_stocks,
        lowScore: response.data.low_score_stocks,
        avgScore: response.data.avg_score,
        positiveChange: response.data.positive_change_stocks,
        negativeChange: response.data.negative_change_stocks,
        neutralChange: response.data.neutral_change_stocks,
        percentageUp: response.data.percentage_up,
        index_funds_avg_change: response.data.index_funds_avg_change,
        major_indices: response.data.major_indices,
        lastUpdated: response.data.last_updated,
        highLowRatio: response.data.high_low_ratio || 0,
        // Day-over-day changes
        highScoreChange: response.data.high_score_change || 0,
        lowScoreChange: response.data.low_score_change || 0,
        avgScoreChange: response.data.avg_score_change || 0,
        totalStocksChange: response.data.total_stocks_change || 0
      }});
    } catch (error) {
      console.error('Error fetching comprehensive stats:', error);
    }
  }, [addDashboardVersion, updateDashboardScoreMeta]);

  // Fetch stock counts (deprecated - use specific functions above)
  const fetchStockCounts = useCallback(async () => {
    await fetchBasicStockCounts();
  }, [fetchBasicStockCounts]);

  // Fetch all stocks
  const fetchStocks = useCallback(async () => {
    try {
      dispatch({ type: ACTIONS.SET_LOADING, payload: true });
      
      // Use the all-stocks endpoint which now returns all stocks with scores (no filtering)
      const response = await axios.get(buildApiUrl(addDashboardVersion('/stocks/all?limit=1000')));
      updateDashboardScoreMeta(response.data);
      
             // Transform the data to match expected format
               let stocksData = [];
        if (response.data && Array.isArray(response.data.stocks)) {
          stocksData = response.data.stocks.map(stock => {
            const growthData = calculateGrowthInterpretation(stock.eps, stock.forward_eps);
            const growthRateData = calculateGrowthRateInterpretation(stock.pe, stock.forward_pe);
            const earningsDays = formatEarningsDays(stock.next_earnings, stock.next_earnings_call_time);
            const targetData = formatTargetPercentage(stock.price_target_growth);
            
            return {
              symbol: stock.symbol,
              name: stock.name,
              current_price: stock.current_price,
              daily_percentage_change: stock.daily_percentage_change || 0,
              market_cap: stock.market_cap,
              pe: stock.pe,
              forward_pe: stock.forward_pe,
              eps: stock.eps,
              forward_eps: stock.forward_eps,
              growth_score: stock.growth_score,
              growth_score_interpretation: getGrowthScoreInterpretation(stock.growth_score),
              growth_interpretation: growthData.interpretation,
              growth_metric_raw: growthData.metric,
              growth_rate_interpretation: growthRateData.interpretation,
              growth_rate_metric_raw: growthRateData.metric,
              target_percentage: targetData.percentage,
              target_percentage_raw: targetData.raw,
              earnings_days: earningsDays,
              next_earnings: stock.next_earnings,
              next_earnings_call_time: stock.next_earnings_call_time || null,
              updated_at: stock.updated_at,
              flagged: stock.flagged,
              realized_vol_60d: stock.realized_vol_60d,
              default_sort_value: stock.default_sort_value,
              ct_tag: stock.ct_tag || null,
              intraday_swing: stock.intraday_swing || null,
              latest_score: {
                OVR: stock.overall_score,
                W: stock.weekly_composite,
                TREND: stock.trend_score,
                RSI: stock.rsi_score,
                MACD: stock.macd_score,
                BB: stock.bb_score,
                X: stock.technical_alignment_score,
                VOL: stock.volume_score,
                VOL_SIG: stock.volume_signal,
                weights: stock.score_weights || null,
                DTE_THESIS: stock.dte_thesis || null,
                DTE_TARGET: stock.dte_target || null,
                DTE_MIN: stock.dte_min || null,
                DTE_MAX: stock.dte_max || null,
                DTE_CONF: stock.dte_confidence || null,
                DTE_TRADEABLE: !!stock.dte_tradeable,
                DTE_FILTER_SIDE: stock.dte_filter_side || null,
              },
            };
          });
        }
      
      dispatch({ type: ACTIONS.SET_STOCKS, payload: stocksData });
    } catch (error) {
      console.error('Error fetching stocks:', error);
      dispatch({ type: ACTIONS.SET_ERROR, payload: error.message });
      // Set empty array on error to prevent iteration issues
      dispatch({ type: ACTIONS.SET_STOCKS, payload: [] });
    }
  }, [addDashboardVersion, updateDashboardScoreMeta]);

  // Fetch stock detail
  const fetchStockDetail = useCallback(async (symbol) => {
    try {
      dispatch({ type: ACTIONS.SET_LOADING, payload: true });
      const response = await axios.get(buildApiUrl(`/stocks/${symbol}`));
      dispatch({ type: ACTIONS.SET_STOCK_DETAIL, payload: response.data });
    } catch (error) {
      console.error('Error fetching stock detail:', error);
      dispatch({ type: ACTIONS.SET_ERROR, payload: error.message });
    }
  }, []);

  // Fetch stock scores
  const fetchStockScores = useCallback(async (symbol, days = 30) => {
    try {
      dispatch({ type: ACTIONS.SET_LOADING, payload: true });
      const response = await axios.get(buildApiUrl(`/stocks/${symbol}/scores?limit=${days}`));
      
      // Ensure we have an array of scores
      const scoresData = Array.isArray(response.data.scores) ? response.data.scores : [];
      dispatch({ type: ACTIONS.SET_STOCK_SCORES, payload: scoresData });
    } catch (error) {
      console.error('Error fetching stock scores:', error);
      dispatch({ type: ACTIONS.SET_ERROR, payload: error.message });
      // Set empty array on error to prevent iteration issues
      dispatch({ type: ACTIONS.SET_STOCK_SCORES, payload: [] });
    }
  }, []);

  // Pull a new stock or update existing stock
  const pullStock = useCallback(async (symbol) => {
    try {
      dispatch({ type: ACTIONS.SET_LOADING, payload: true });
      
      // Validate symbol
      const cleanSymbol = symbol.trim().toUpperCase();
      if (!cleanSymbol || cleanSymbol.length > 10) {
        throw new Error('Invalid symbol format');
      }
      
      // Only allow alphanumeric characters
      if (!/^[A-Z0-9]+$/.test(cleanSymbol)) {
        throw new Error('Symbol can only contain letters and numbers');
      }
      
      const response = await axios.post(buildApiUrl(`/stocks/${cleanSymbol}/update`));
      
      if (response.data.success) {
        // Refresh the stocks list to include the new stock
        await fetchStocks();
        
        const action = response.data.is_new_stock ? 'pulled' : 'updated';
        return { 
          success: true, 
          message: `Successfully ${action} ${cleanSymbol}`,
          is_new_stock: response.data.is_new_stock
        };
      } else {
        throw new Error(response.data.error || 'Failed to pull/update stock');
      }
    } catch (error) {
      console.error('Error pulling/updating stock:', error);
      const errorMessage = error.response?.data?.error || error.message || 'Failed to pull/update stock';
      dispatch({ type: ACTIONS.SET_ERROR, payload: errorMessage });
      return { success: false, error: errorMessage };
    } finally {
      dispatch({ type: ACTIONS.SET_LOADING, payload: false });
    }
  }, [fetchStocks]);

  // Fetch historic peaks
  const fetchHistoricPeaks = useCallback(async () => {
    try {
      dispatch({ type: ACTIONS.SET_HISTORIC_PEAKS_LOADING, payload: true });
      const response = await axios.get(buildApiUrl(addDashboardVersion('/market/historic-peaks')));
      updateDashboardScoreMeta(response.data);
      dispatch({ type: ACTIONS.SET_HISTORIC_PEAKS, payload: response.data.peaks || [] });
      return response.data;
    } catch (error) {
      console.error('Error fetching historic peaks:', error);
      dispatch({ type: ACTIONS.SET_HISTORIC_PEAKS, payload: [] });
      return null;
    }
  }, [addDashboardVersion, updateDashboardScoreMeta]);

  // Fetch assessment data (win rates per score band per period)
  const fetchAssessmentData = useCallback(async () => {
    try {
      const response = await axios.get(buildApiUrl('/assessment'));
      dispatch({ type: ACTIONS.SET_ASSESSMENT_DATA, payload: response.data });
    } catch (error) {
      console.error('Error fetching assessment data:', error);
    }
  }, []);

  // Set filters
  const setFilters = useCallback((filters) => {
    dispatch({ type: ACTIONS.SET_FILTERS, payload: filters });
  }, []);

  const setScoreVersion = useCallback((versionId) => {
    const numeric = versionId == null || versionId === '' ? null : Number(versionId);
    dispatch({
      type: ACTIONS.SET_SCORE_VERSION,
      payload: Number.isFinite(numeric) ? numeric : null,
    });
  }, []);

  // Clear error
  const clearError = useCallback(() => {
    dispatch({ type: ACTIONS.CLEAR_ERROR });
  }, []);

  // Filter and sort stocks
  const getFilteredStocks = useCallback(() => {
    // Ensure stocks is always an array
    const stocksArray = Array.isArray(state.stocks) ? state.stocks : [];
    let filtered = [...stocksArray];

    // Apply search filter
    if (state.filters.search) {
      filtered = filtered.filter(stock => 
        stock.symbol.toLowerCase().includes(state.filters.search.toLowerCase()) ||
        stock.name.toLowerCase().includes(state.filters.search.toLowerCase())
      );
    }

    // Apply flagged filter
    if (state.filters.flaggedOnly) {
      filtered = filtered.filter(stock => stock.flagged);
    }

    // Calls/Puts pills act as sort modifiers, not filters, and override any column sort:
    //   only calls  → OVR desc (highest score first)
    //   only puts   → OVR asc  (lowest score first)
    //   both/neither → OVR desc (highest score first)
    const callsActive = !!state.filters.callsOnly;
    const putsActive = !!state.filters.putsOnly;
    const pillExclusive = callsActive !== putsActive;

    if (pillExclusive) {
      const ovrOf = (s) => s.latest_score?.OVR ?? 50;
      if (callsActive) {
        filtered.sort((a, b) => ovrOf(b) - ovrOf(a));
      } else {
        filtered.sort((a, b) => ovrOf(a) - ovrOf(b));
      }
    } else if (!state.filters.sortBy || state.filters.sortBy === 'default') {
      const ovrOf = (s) => s.latest_score?.OVR ?? -1;
      filtered.sort((a, b) => ovrOf(b) - ovrOf(a));
    } else {
      // Apply explicit sorting for other fields
      filtered.sort((a, b) => {
        let aValue, bValue;

        if (state.filters.sortBy === 'updated_at') {
          const av = a.updated_at ? new Date(a.updated_at).getTime() : 0;
          const bv = b.updated_at ? new Date(b.updated_at).getTime() : 0;
          return state.filters.sortOrder === 'asc' ? av - bv : bv - av;
        }

        // Earnings days: sooner-first for desc, later-first for asc; stocks
        // with no upcoming earnings (null/negative) always pushed to the end.
        if (state.filters.sortBy === 'earnings') {
          const aRaw = a.next_earnings;
          const bRaw = b.next_earnings;
          const aInvalid = aRaw === null || aRaw === undefined || aRaw < 0;
          const bInvalid = bRaw === null || bRaw === undefined || bRaw < 0;
          if (aInvalid && bInvalid) return 0;
          if (aInvalid) return 1;
          if (bInvalid) return -1;
          if (aRaw !== bRaw) {
            return state.filters.sortOrder === 'desc' ? aRaw - bRaw : bRaw - aRaw;
          }
          // Same day: tie-break on call_time so BMO sorts before AMC under asc.
          // Stored as 'HH:MM:SS' strings so lex compare works. Missing time
          // falls to the end of its day group.
          const aCT = a.next_earnings_call_time || '99:99:99';
          const bCT = b.next_earnings_call_time || '99:99:99';
          if (aCT === bCT) return 0;
          return state.filters.sortOrder === 'desc'
            ? (aCT < bCT ? -1 : 1)
            : (aCT < bCT ? 1 : -1);
        }

        // Handle different field types
        if (['OVR', 'TREND', 'RSI', 'MACD', 'BB', 'X', 'VOL'].includes(state.filters.sortBy)) {
          // Score fields
          aValue = a.latest_score?.[state.filters.sortBy] || 0;
          bValue = b.latest_score?.[state.filters.sortBy] || 0;
        } else if (state.filters.sortBy === 'symbol') {
          // Symbol field (string)
          aValue = (a.symbol || '').toLowerCase();
          bValue = (b.symbol || '').toLowerCase();
        } else if (state.filters.sortBy === 'current_price') {
          // Price field
          aValue = a.current_price || 0;
          bValue = b.current_price || 0;
        } else if (state.filters.sortBy === 'pe') {
          // PE ratio field
          aValue = a.pe || 0;
          bValue = b.pe || 0;
        } else if (state.filters.sortBy === 'growth') {
          aValue = a.growth_metric_raw ?? -999;
          bValue = b.growth_metric_raw ?? -999;
        } else if (state.filters.sortBy === 'growth_rate') {
          aValue = a.growth_rate_metric_raw ?? -999;
          bValue = b.growth_rate_metric_raw ?? -999;
        } else if (state.filters.sortBy === 'target') {
          // Target percentage field (numeric)
          aValue = a.target_percentage_raw || 0;
          bValue = b.target_percentage_raw || 0;
        } else {
          // Default to score fields
          aValue = a.latest_score?.[state.filters.sortBy] || 0;
          bValue = b.latest_score?.[state.filters.sortBy] || 0;
        }
        
        if (state.filters.sortBy === 'symbol') {
          // String comparison for symbol only
          if (state.filters.sortOrder === 'desc') {
            return bValue.localeCompare(aValue);
          } else {
            return aValue.localeCompare(bValue);
          }
        } else {
          // Numeric comparison for all other fields
          if (state.filters.sortOrder === 'desc') {
            return bValue - aValue;
          } else {
            return aValue - bValue;
          }
        }
      });
    }

    return filtered;
  }, [state.stocks, state.filters]);

  // Load comprehensive stats first (fast), then stocks (slower)
  useEffect(() => {
    fetchComprehensiveStats();
    fetchAssessmentData();
    const timer = setTimeout(() => {
      fetchStocks();
    }, 100);
    return () => clearTimeout(timer);
  }, [fetchComprehensiveStats, fetchStocks, fetchAssessmentData]);

  // Update a single stock
  const updateStock = useCallback(async (symbol) => {
    try {
      const response = await axios.post(buildApiUrl(`/stocks/${symbol}/update`));

      if (response.data && response.data.stock) {
        const updatedStock = response.data.stock;
        dispatch({ type: ACTIONS.UPDATE_SINGLE_STOCK, payload: { symbol, stock: updatedStock } });
        return updatedStock;
      }
    } catch (error) {
      console.error(`Error updating stock ${symbol}:`, error);
      throw error;
    }
  }, []);

  const value = {
    ...state,
    fetchStocks,
    fetchStockDetail,
    fetchStockScores,
    fetchBasicStockCounts,
    fetchComprehensiveStats,
    fetchHistoricPeaks,
    fetchAssessmentData,
    setFilters,
    setScoreVersion,
    clearError,
    getFilteredStocks,
    updateStock,
    pullStock
  };

  return (
    <StockContext.Provider value={value}>
      {children}
    </StockContext.Provider>
  );
}

// Custom hook to use the stock context
export function useStock() {
  const context = useContext(StockContext);
  if (!context) {
    throw new Error('useStock must be used within a StockProvider');
  }
  return context;
} 
