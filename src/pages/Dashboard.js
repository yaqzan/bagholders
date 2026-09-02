import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useStock } from '../context/StockContext';
import { shouldAutoRefresh, shouldRefreshOnResume } from '../utils/timeUtils';
import StockTable from '../components/StockTable';
import FilterBar from '../components/FilterBar';
import ScoreVersionSelector from '../components/ScoreVersionSelector';

/** Weights for regime composite — must match market_regime.SIGNAL_WEIGHTS */
const REGIME_W = { breadth: 0.35, vix: 0.35, trend: 0.3 };

const DEFAULT_API_BASE_URL = 'https://api.bagholders.ai';
const LOCAL_API_BASE_URL = 'http://localhost:5000';

const getApiBaseUrl = () => {
  if (process.env.REACT_APP_API_URL) return process.env.REACT_APP_API_URL;
  if (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)) {
    return LOCAL_API_BASE_URL;
  }
  return DEFAULT_API_BASE_URL;
};

const DEFAULT_30DTE_OPTION = {
  // Fallback only (shown before /api/strategy/config resolves) — keep in sync
  // with strategy_config.py OPT_30DTE. 2026-08-10 tpsl_refine ship.
  TP_BASE: 0.10,
  TP_STRESS: 0.10,
  SL_BASE: -1.00,
  SL_STRESS: -1.00,
  BREADTH_THRESHOLD: 40,
  PUT_TP: 0.35,
  PUT_SL: -0.20,
};

const BREADTH_TIPS = {
  ad: 'Advancing vs declining: how many tracked stocks closed up vs down from the prior close. Broad advances with few declines mean wide participation (risk-on); the opposite warns of defensive positioning.',
  trin: 'Arms Index (TRIN): (advances÷declines) ÷ (up-volume÷down-volume). Below ~1 means advancing stocks are getting more volume (bullish flow). Above ~1 means losers are trading heavier (bearish). Extreme spikes often mark exhaustion or washouts.',
  mcclellan: 'McClellan Oscillator: short-term breadth momentum (19-day EMA minus 39-day EMA of daily advance−decline). Strongly positive = thrust; deeply negative = broad weakness. Often leads swing turns.',
  hl52: 'New 52-week highs vs lows (within ~3% of 252-day extremes). Many highs with few lows = healthy leadership; elevated lows = stress under the index.',
  ema50: '% of names above their 50-day EMA — intermediate trend health. Well above ~60% usually aligns with uptrends; sustained sub-~40% is structurally weak.',
  ema200: '% above the 200-day EMA — slow "tape" trend. High readings mean most stocks remain in long-term uptrends; low readings mean broad bearish structure.',
  adiff: "Today's advances minus declines (one-day breadth). Feeds the cumulative A-D line and McClellan; positive = more stocks up than down today.",
  breadthScore: 'Composite 0–100 built from McClellan, summation trend, 52w highs/lows, TRIN, % above EMA50/200, plus decaying Zweig thrust boost and Hindenburg penalty when active. This value is fed into regime at 35% weight.',
  marketWave: 'Market Wave: one sector ETF breadth health score calibrated from full available sector history. Low values mark breadth stress and wider drawdown-tail risk; high values mark breadth repair and healthier participation.',
  sectorEtf50: 'Sector ETF breadth input: percent of available sector ETF proxies above their 50-day EMA. The denominator is 9, 10, or 11 sectors depending on which ETFs existed on that date.',
  sectorEtf1d: '1-day change in available sector ETF breadth. Negative values mean sectors are falling below their 50-day EMA; positive values mean sector breadth is repairing.',
  sectorEtf5d: '5-day change in available sector ETF breadth. Large negative values are breadth cliffs; large positive values show fast repair.',
  sectorEtf15d: '15-day change in available sector ETF breadth, aligned to the 15 DTE strategy window.',
  sectorEtfPosition: 'Rolling breadth position. Positive means current sector breadth is nearer the top of its recent range; negative means it is nearer the bottom.',
  sectorEtf200: 'Sector ETF breadth: percent of sector ETF proxies above their 200-day EMA. High readings mean most sectors are still in long-term uptrends.',
  sectorEtfRsi: 'Average RSI across the sector ETF proxy set. Around 50 is neutral; elevated readings show broad sector momentum, while low readings show sector-level pressure.',
  sectorEtfDate: 'As-of date for the sector ETF breadth cache. It uses the latest available ETF breadth row on or before the market breadth date.',
  zweig: 'Zweig Breadth Thrust: rare momentum signal when a 10-day EMA of A÷(A+D) jumps from very weak to strong within about 10 sessions. Historically associated with durable bull legs.',
  hindenburg: 'Hindenburg Omen (confirmed): elevated new highs and new lows together with weak McClellan and still-high % above EMA50 — a volatility warning, not a timer.',
};

// Strategy-aware tooltips — interpolate live values from /api/strategy/config
// so the displayed numbers match what the live MC and backtest engines use.
// Single source of truth: strategy_config.py.
function fmtStrategyPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return `${Math.round(Number(value) * 100)}%`;
}

function fmtStrategyMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  const n = Number(value);
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}k`;
  return `$${Math.round(n).toLocaleString()}`;
}

function getPracticalExposureTip(cfg) {
  if (!cfg?.PRACTICAL_EXPOSURE_ENABLED) return null;
  const gross = fmtStrategyPct(cfg.GROSS_PREMIUM_CAP) || '-';
  const call = fmtStrategyPct(cfg.CALL_PREMIUM_CAP) || '-';
  const put = fmtStrategyPct(cfg.PUT_PREMIUM_CAP) || '-';
  const ceiling = fmtStrategyMoney(cfg.PRACTICAL_CAPITAL_CEILING) || '-';
  const callRef = cfg.OPP_SAT_CALL_REF ?? '-';
  const putRef = cfg.OPP_SAT_PUT_REF ?? '-';
  const floor = fmtStrategyPct(cfg.OPP_SAT_FLOOR) || '-';
  return [
    'Practical exposure saturation (30 DTE portfolio layer).',
    `Allocation base caps at ${ceiling}; open premium caps: gross ${gross}, calls ${call}, puts ${put}.`,
    `Crowded daily opportunity sets scale smoothly after ${callRef} call candidates or ${putRef} put candidates, with a ${floor} floor.`,
    `Concurrent slots: ${cfg.MAX_POSITIONS ?? '-'} total, ${cfg.MAX_POSITIONS_CALL ?? '-'} calls, ${cfg.MAX_POSITIONS_PUT ?? '-'} puts.`,
  ].join('\n');
}

function getStrategyTips(opt) {
  const option = opt || DEFAULT_30DTE_OPTION;
  const tpBase    = Math.round(option.TP_BASE * 100);
  const tpStress  = Math.round(option.TP_STRESS * 100);
  const slBase    = Math.round(Math.abs(option.SL_BASE) * 100);
  const slStress  = Math.round(Math.abs(option.SL_STRESS) * 100);
  const putTp     = Math.round(option.PUT_TP * 100);
  const putSl     = Math.round(Math.abs(option.PUT_SL) * 100);
  const brdThresh = option.BREADTH_THRESHOLD;
  return {
    recSL: slBase === slStress
      ? `CALL option stop-loss: ${slBase}% of premium (base==stress; breadth-conditioning re-tested and rejected at N=500, 2026-08-10). At 100% this is a deep disaster stop — losers are held (dead-hold doctrine) rather than stopped on noise.`
      : `Recommended CALL option stop-loss based on breadth. When breadth ≤ ${brdThresh} (weak participation), SL widens from ${slBase}% to ${slStress}% to protect against shakeouts during broad market weakness. MAE-anchored.`,
    recTP: tpBase === tpStress
      ? `CALL option take-profit: ${tpBase}% of premium (base==stress; scalp-and-dead-hold canon 2026-08-10 — take small wins fast via resting limit, recycle capital).`
      : `Recommended CALL option take-profit based on breadth. When breadth ≤ ${brdThresh} (weak participation / elevated fear), TP widens from ${tpBase}% to ${tpStress}% to capture more of the larger σ-move distribution priced into option premiums.`,
    putTpSl: `PUT option TP/SL — not breadth-adaptive. TP=${putTp}%, SL=−${putSl}%. Tight SL produces positive EV across all regimes (post bug-fix MC, wider PUT_SL becomes catastrophic). Unlike calls, puts do NOT widen on weak breadth: breadth ≤ ${brdThresh} is a TAILWIND for puts (thesis confirmed by participation collapse). PUT_SL_HOLD removed in Phase H5 ship.`,
  };
}

// Module-level promise cache so MarketPanel + MobileMarketStrip share one fetch.
let _strategyCfg30Promise = null;
function fetchStrategyCfg30() {
  if (!_strategyCfg30Promise) {
    _strategyCfg30Promise = fetch(`${getApiBaseUrl()}/api/strategy/config?strategy=30dte`)
      .then(r => r.ok ? r.json() : null)
      .then(j => j ? j['30dte'] : null)
      .catch(() => null);
  }
  return _strategyCfg30Promise;
}

function useStrategyCfg30() {
  const [cfg, setCfg] = useState(null);
  useEffect(() => {
    let alive = true;
    fetchStrategyCfg30().then(c => { if (alive) setCfg(c); });
    return () => { alive = false; };
  }, []);
  return cfg;
}

// Ordered by relevance to the strategy: broad US first (regime input), then
// sectors driving signal density, then risk-regime / rates / vol proxies,
// then leveraged broad-market vol tells, then asset hedges, then international
// breadth, then niche thematics.
const INDEX_ORDER = [
  // US broad market — primary regime read
  'SPY', 'QQQ', 'DRAM', 'ARKQ', 'ARKX', 'SOXX', 'PINV.TO', 'IWM',
  // Sector breadth — where signals concentrate
  'SMH', 'IGV', 'XLF', 'XLE', 'XLC', 'XLP', 'XLU',
  // Risk regime / rates / vol
  'TLT', 'HYG', 'IEF', 'SVIX',
  // Leveraged broad-market — speculative-flow tell
  'TQQQ', 'SOXL', 'TNA',
  // Asset-class hedges
  'GLD', 'SLV', 'IAU', 'IBIT', 'FBTC',
  // International
  'EEM', 'FXI', 'KWEB', 'ASHR', 'EWZ', 'EWY',
  // Niche / thematic
  'LABD', 'BOIL', 'URA', 'UFO',
];

const INDEX_LABELS = {
  SPY: 'SPDR S&P 500 — US large-cap',
  QQQ: 'Invesco QQQ — Nasdaq-100',
  ARKQ: 'ARK Autonomous Tech & Robotics ETF',
  ARKX: 'ARK Space Exploration & Innovation ETF',
  SOXX: 'iShares Semiconductor ETF',
  'PINV.TO': 'Purpose Global Innovators Fund ETF',
  IWM: 'iShares Russell 2000 — US small-cap',
  TQQQ: 'ProShares UltraPro 3x QQQ',
  SMH: 'VanEck Semiconductors',
  SOXL: 'Direxion 3x Semiconductor Bull',
  TNA: 'Direxion 3x Small-Cap Bull (Russell 2000)',
  XLC: 'Communication Services Sector SPDR',
  XLE: 'Energy Sector SPDR',
  XLF: 'Financial Sector SPDR',
  XLP: 'Consumer Staples Sector SPDR',
  XLU: 'Utilities Sector SPDR',
  IGV: 'iShares Expanded Tech-Software',
  GLD: 'SPDR Gold Shares',
  SLV: 'iShares Silver Trust',
  IBIT: 'iShares Bitcoin Trust',
  FBTC: 'Fidelity Wise Origin Bitcoin Fund',
  TLT: 'iShares 20+ Year Treasury Bonds',
  IEF: 'iShares 7-10 Year Treasury Bonds',
  HYG: 'iShares iBoxx High-Yield Corporate Bonds',
  EEM: 'iShares MSCI Emerging Markets',
  FXI: 'iShares China Large-Cap',
  EWZ: 'iShares MSCI Brazil',
  EWY: 'iShares MSCI South Korea',
  KWEB: 'KraneShares CSI China Internet',
  ASHR: 'Xtrackers Harvest CSI 300 China A-Shares',
  SVIX: '-1x Short VIX Futures ETF',
  BOIL: 'ProShares Ultra 2x Bloomberg Natural Gas',
  LABD: 'Direxion 3x S&P Biotech Bear',
  IAU: 'iShares Gold Trust',
  DRAM: 'Themes Generative AI / DRAM exposure',
  UFO: 'Procure Space ETF',
  URA: 'Global X Uranium ETF',
};
const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

/* ── Pure helpers (module-level, no state dependency) ── */

const scoreTextColor = (score) => {
  if (score == null) return 'text-gray-600';
  if (score >= 75) return 'text-trading-green-400';
  if (score >= 70) return 'text-trading-green-500';
  if (score <= 25) return 'text-trading-red-400';
  return 'text-gray-500';
};

const pctColor = (pct) => {
  if (pct == null) return 'text-gray-600';
  if (pct > 0) return 'text-trading-green-400';
  if (pct < 0) return 'text-trading-red-400';
  return 'text-gray-400';
};

const trinColor = (trin) => {
  if (trin == null) return 'text-gray-600';
  if (trin < 0.8) return 'text-trading-green-400';
  if (trin < 1.2) return 'text-amber-300';
  return 'text-trading-red-400';
};

const mcColor = (osc) => {
  if (osc == null) return 'text-gray-600';
  if (osc > 50) return 'text-trading-green-400';
  if (osc > 0) return 'text-trading-green-500';
  if (osc > -50) return 'text-amber-300';
  return 'text-trading-red-400';
};

const pctMAColor = (pct) => {
  if (pct == null) return 'text-gray-600';
  if (pct > 60) return 'text-trading-green-400';
  if (pct > 40) return 'text-amber-300';
  return 'text-trading-red-400';
};

const marketWaveColor = (score) => {
  if (score == null || Number.isNaN(Number(score))) return 'text-gray-600';
  const n = Number(score);
  if (n >= 65) return 'text-trading-green-400';
  if (n >= 45) return 'text-gray-300';
  if (n >= 35) return 'text-amber-300';
  return 'text-trading-red-400';
};

const signalPillClass = (tone, active = true) => {
  if (!active) return 'border-white/[0.08] bg-white/[0.03] text-gray-600';
  if (tone === 'green') return 'border-trading-green-300/25 bg-trading-green-300/10 text-trading-green-300';
  if (tone === 'red') return 'border-trading-red-400/25 bg-trading-red-400/10 text-trading-red-400';
  if (tone === 'amber') return 'border-amber-300/25 bg-amber-300/10 text-amber-300';
  return 'border-white/[0.08] bg-white/[0.04] text-gray-400';
};

const vixColor = (vix) => {
  if (vix == null) return 'text-gray-600';
  if (vix < 20) return 'text-trading-green-400';
  if (vix < 30) return 'text-amber-300';
  return 'text-trading-red-400';
};

const regimeColor = (composite) => {
  if (composite == null) return 'text-gray-500';
  if (composite >= 60) return 'text-trading-green-300';
  if (composite >= 40) return 'text-amber-300';
  return 'text-trading-red-400';
};

const regimeLabel = (composite) => {
  if (composite == null) return '—';
  if (composite >= 75) return 'PANIC';
  if (composite >= 60) return 'STRESS';
  if (composite >= 45) return 'NEUTRAL';
  if (composite >= 30) return 'CAUTION';
  return 'CALM';
};

const regimeSubtitle = (label) => {
  switch (label) {
    case 'PANIC':   return ['High vol', 'calls amplified'];
    case 'STRESS':  return ['Elevated vol', 'calls boosted'];
    case 'NEUTRAL': return ['Near-raw', 'scores'];
    case 'CAUTION': return ['Low vol', 'calls reduced'];
    case 'CALM':    return ['Complacent', 'calls suppressed'];
    default:        return ['', ''];
  }
};

const regimeRail = (label) => ({
  PANIC:   'bg-trading-green-300',
  STRESS:  'bg-trading-green-400/70',
  NEUTRAL: 'bg-amber-300/60',
  CAUTION: 'bg-amber-400/70',
  CALM:    'bg-trading-red-400',
}[label] || 'bg-gray-600');

const scoreSentimentClass = (v) => {
  if (v == null || Number.isNaN(Number(v))) return 'text-gray-600';
  const n = Number(v);
  if (n >= 60) return 'text-trading-green-400';
  if (n <= 40) return 'text-trading-red-400';
  return 'text-gray-300';
};

const multSentimentClass = (m) => {
  if (m == null || Number.isNaN(Number(m))) return 'text-gray-600';
  const x = Number(m);
  if (x > 1.0001) return 'text-trading-green-400';
  if (x < 0.9999) return 'text-trading-red-400';
  return 'text-gray-300';
};

const formatDateLabel = (dateStr) => {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}`;
};

/* ── Shared sub-components ── */

const Row = ({ label: lbl, tip, children }) => (
  <div className="flex items-center justify-between gap-2" title={tip}>
    <span className="text-[10px] uppercase tracking-wider text-gray-600 whitespace-nowrap">{lbl}</span>
    <span className="text-[12px] font-mono font-medium tabular-nums leading-tight text-right">{children}</span>
  </div>
);

const metricNumber = (value, digits = 0) => {
  if (value === null || value === undefined || value === '') return '\u2014';
  const n = Number(value);
  return Number.isNaN(n) ? '\u2014' : n.toFixed(digits);
};

const signedMetricNumber = (value, digits = 0) => {
  if (value === null || value === undefined || value === '') return '\u2014';
  const n = Number(value);
  if (Number.isNaN(n)) return '\u2014';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`;
};

const pctMetric = (value, digits = 0) => {
  const n = metricNumber(value, digits);
  return n === '\u2014' ? n : `${n}%`;
};

const BreadthSummaryChip = ({ label, value, toneClass, title }) => (
  <span
    title={title}
    className="inline-flex h-5 shrink-0 cursor-help items-center gap-0.5 whitespace-nowrap rounded-sm border border-white/[0.06] bg-white/[0.03] px-1.5 font-mono tabular-nums leading-none"
  >
    <span className="text-[9px] uppercase text-gray-600">{label}</span>
    <span className={`text-[11px] font-semibold ${toneClass}`}>{value}</span>
  </span>
);

const StockSymbol = ({ stock, isNextWeek, align }) => (
  <div
    className={`leading-tight text-[11px] ${align === 'right' ? 'text-right' : ''} ${
      isNextWeek ? 'text-gray-600' : 'text-gray-400'
    } ${stock.flagged ? 'text-amber-300 drop-shadow-[0_0_4px_rgba(251,191,36,0.5)]' : ''}`}
    title={stock.name}
  >
    <span className="truncate font-mono">{stock.symbol}</span>
    {stock.score != null && (
      <span className={`ml-0.5 font-mono tabular-nums ${scoreTextColor(stock.score)} ${isNextWeek ? 'opacity-60' : ''}`}>
        {stock.score}
      </span>
    )}
  </div>
);

const SubCols = ({ stocks, isNextWeek }) => {
  const calls = stocks.filter(s => s.score != null && s.score > 50)
    .sort((a, b) => b.score - a.score);
  const puts = stocks.filter(s => s.score != null && s.score < 50)
    .sort((a, b) => a.score - b.score);
  const neutrals = stocks.filter(s => s.score == null || s.score === 50);
  for (const s of neutrals) {
    if (calls.length <= puts.length) calls.push(s);
    else puts.push(s);
  }
  return (
    <div className="flex flex-1 min-w-0 gap-px">
      <div className="flex-1 min-w-0">
        {calls.map(s => <StockSymbol key={s.symbol} stock={s} isNextWeek={isNextWeek} />)}
      </div>
      {puts.length > 0 && (
        <div className="flex-1 min-w-0">
          {puts.map(s => <StockSymbol key={s.symbol} stock={s} isNextWeek={isNextWeek} />)}
        </div>
      )}
    </div>
  );
};

/* ── Market Panel ── */

const MarketPanel = ({ breadthData, regimeData, stats, collapsed, toggleCollapsed }) => {
  const b = breadthData;
  const r = regimeData;

  const composite = r?.regime_composite;
  const mult = r?.regime_multiplier;
  const label = regimeLabel(composite);
  const vix = r?.vix_close;
  const vixChg = r?.vix_10d_change;

  const bScore = r?.market_breadth_score ?? b?.breadth_score;
  const vxScore = r?.vix_score;
  const trScore = r?.market_trend_score;
  const sectorEtf = b?.sector_etf_breadth;
  const sectorEtfStale = sectorEtf?.days_stale != null && Number(sectorEtf.days_stale) > 3;
  const marketWaveScore = sectorEtf?.market_wave_score;
  const marketWaveState = sectorEtf?.market_wave_state;
  const sectorAbove50Count = sectorEtf?.pct_above_ema50 != null && sectorEtf?.issues
    ? Math.round(Number(sectorEtf.pct_above_ema50) * Number(sectorEtf.issues) / 100)
    : null;
  const marketWaveTitle = sectorEtf ? [
    BREADTH_TIPS.marketWave,
    marketWaveState ? `State: ${marketWaveState}` : null,
    sectorEtf.pct_above_ema50 != null ? `Sector breadth: ${Number(sectorEtf.pct_above_ema50).toFixed(0)}% (${sectorAbove50Count ?? '?'} / ${sectorEtf.issues || '?'} sectors)` : null,
    sectorEtf.breadth_1d_change != null ? `1D change: ${Number(sectorEtf.breadth_1d_change) >= 0 ? '+' : ''}${Number(sectorEtf.breadth_1d_change).toFixed(0)} pts` : null,
    sectorEtf.breadth_15d_change != null ? `15D change: ${Number(sectorEtf.breadth_15d_change) >= 0 ? '+' : ''}${Number(sectorEtf.breadth_15d_change).toFixed(0)} pts` : null,
    sectorEtf.market_wave_signed != null ? `Signed wave: ${Number(sectorEtf.market_wave_signed) >= 0 ? '+' : ''}${Number(sectorEtf.market_wave_signed).toFixed(0)}` : null,
  ].filter(Boolean).join('\n') : undefined;

  // Live strategy values from /api/strategy/config (Phase A.4 — single source
  // of truth = strategy_config.py). Falls back to last-known values until the
  // module-cached fetch resolves (~one HTTP call total across both panels).
  const strategyCfg = useStrategyCfg30();
  const opt = strategyCfg?.option || DEFAULT_30DTE_OPTION;
  const tips = getStrategyTips(opt);
  const practicalTip = getPracticalExposureTip(strategyCfg);
  const brdThresh = opt.BREADTH_THRESHOLD;
  const tpBaseStr   = `${Math.round(opt.TP_BASE * 100)}%`;
  const tpStressStr = `${Math.round(opt.TP_STRESS * 100)}%`;
  const slBaseStr   = `${Math.round(Math.abs(opt.SL_BASE) * 100)}%`;
  const slStressStr = `${Math.round(Math.abs(opt.SL_STRESS) * 100)}%`;
  const putTpStr    = `${Math.round(opt.PUT_TP * 100)}%`;
  const putSlStr    = `${Math.round(Math.abs(opt.PUT_SL) * 100)}%`;
  const grossCapStr = fmtStrategyPct(strategyCfg?.GROSS_PREMIUM_CAP);
  const callCapStr = fmtStrategyPct(strategyCfg?.CALL_PREMIUM_CAP);
  const putCapStr = fmtStrategyPct(strategyCfg?.PUT_PREMIUM_CAP);
  const callExitTip = [tips.recTP, tips.recSL, practicalTip].filter(Boolean).join('\n\n');
  const putExitTip = [tips.putTpSl, practicalTip].filter(Boolean).join('\n\n');

  const isStressed = bScore != null && Number(bScore) <= brdThresh;
  const callTP = isStressed ? tpStressStr : tpBaseStr;
  const callSL = isStressed ? slStressStr : slBaseStr;

  const m = mult != null ? Number(mult) : null;
  const exCallAdj = m != null ? (50 + (80 - 50) * m).toFixed(0) : null;
  const exPutAdj  = m != null ? (50 + (20 - 50) * (2.0 - m)).toFixed(0) : null;
  const multLine = m != null
    ? `Score Multiplier: ${m.toFixed(3)}\u00d7  (call 80\u2192~${exCallAdj} · put 20\u2192~${exPutAdj})`
    : `Score Multiplier: \u2014`;

  const impactLine = {
    PANIC:   'VIX elevated, breadth weak — fear environment. Call scores amplified, put scores suppressed. Highest-conviction setup for call entries.',
    STRESS:  'VIX somewhat elevated or breadth softening. Call scores lightly boosted.',
    NEUTRAL: 'Minimal adjustment — scores reflect near-raw indicator output.',
    CAUTION: 'Low VIX or healthy breadth — quiet market. Call scores moderately pulled toward 50.',
    CALM:    'Low VIX, healthy breadth — complacent market. Call scores compressed toward 50 (e.g. raw 80 → ~' + (exCallAdj ?? '?') + '). Poor options environment.',
  }[label] ?? '';

  const regimeTip = [
    `Regime: ${label}`,
    impactLine,
    '',
    `Composite: ${composite != null ? Number(composite).toFixed(1) : '\u2014'}`,
    multLine,
    '',
    `Breadth: ${bScore != null ? Number(bScore).toFixed(0) : '\u2014'} \u00d7 ${(REGIME_W.breadth * 100).toFixed(0)}% = ${bScore != null ? (Number(bScore) * REGIME_W.breadth).toFixed(1) : '\u2014'}`,
    `VIX Score: ${vxScore != null ? Number(vxScore).toFixed(0) : '\u2014'} \u00d7 ${(REGIME_W.vix * 100).toFixed(0)}% = ${vxScore != null ? (Number(vxScore) * REGIME_W.vix).toFixed(1) : '\u2014'}`,
    `SPY Trend: ${trScore != null ? Number(trScore).toFixed(0) : '\u2014'} \u00d7 ${(REGIME_W.trend * 100).toFixed(0)}% = ${trScore != null ? (Number(trScore) * REGIME_W.trend).toFixed(1) : '\u2014'}`,
  ].join('\n');

  const adv = b?.advancing ?? stats.positiveChange;
  const dec = b?.declining ?? stats.negativeChange;
  const total = adv + dec;
  const advPct = total > 0 ? (adv / total) * 100 : 0;
  const sectorDateLabel = sectorEtf?.date
    ? `${formatDateLabel(sectorEtf.date)}${Number(sectorEtf.days_stale || 0) > 0 ? ` -${Number(sectorEtf.days_stale)}d` : ''}`
    : null;
  const summaryChips = [
    {
      label: 'B',
      value: metricNumber(bScore),
      toneClass: scoreSentimentClass(bScore),
      title: BREADTH_TIPS.breadthScore,
    },
  ];
  if (marketWaveScore != null) {
    summaryChips.push({
      label: 'W',
      value: metricNumber(marketWaveScore),
      toneClass: marketWaveColor(marketWaveScore),
      title: marketWaveTitle || BREADTH_TIPS.marketWave,
    });
  }
  if (sectorEtf?.pct_above_ema50 != null) {
    summaryChips.push({
      label: 'ETF',
      value: pctMetric(sectorEtf.pct_above_ema50),
      toneClass: pctMAColor(sectorEtf.pct_above_ema50),
      title: BREADTH_TIPS.sectorEtf50,
    });
  }
  const activeBreadthSignals = [
    { key: 'hindenburg', label: 'Hind', fullLabel: 'Hindenburg', tone: 'red', active: b?.hindenburg_confirmed, title: BREADTH_TIPS.hindenburg },
    { key: 'zweig', label: 'Zweig', fullLabel: 'Zweig', tone: 'green', active: b?.zweig_thrust_active, title: BREADTH_TIPS.zweig },
  ].filter(signal => signal.active);

  return (
    <div className="flex w-full shrink-0 flex-col gap-2 lg:w-[248px]">

      {/* ── Regime card ── */}
      <div
        className={`relative overflow-hidden rounded-md bg-trading-dark-900 border border-white/[0.06] px-3 py-2.5 ${r ? 'cursor-help' : ''}`}
        title={r ? regimeTip : undefined}
      >
        <span className={`absolute left-0 top-0 bottom-0 w-[2px] ${regimeRail(label)}`} aria-hidden="true" />

        {/* Row 1: Regime label (left) + VIX (right) */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-col min-w-0">
            <span className={`text-[15px] font-semibold tracking-wide leading-none ${regimeColor(composite)}`}>
              {label}
            </span>
            <span className="text-[10px] text-gray-500 leading-tight mt-1">
              {regimeSubtitle(label)[0]}
            </span>
            <span className="text-[10px] text-gray-500 leading-tight">
              {regimeSubtitle(label)[1]}
            </span>
            <div className="flex items-center gap-1.5 mt-1 text-[11px] font-mono tabular-nums">
              <span className={regimeColor(composite)}>
                {composite != null ? Number(composite).toFixed(0) : '\u2014'}
              </span>
              <span className="text-gray-700">&middot;</span>
              <span className={multSentimentClass(mult)}>
                {mult != null ? `${Number(mult).toFixed(2)}\u00d7` : '\u2014'}
              </span>
            </div>
          </div>
          <div className="flex flex-col items-end shrink-0 font-mono tabular-nums">
            <div className="flex items-baseline gap-1">
              <span className="text-[9px] uppercase tracking-wider text-gray-600">VIX</span>
              <span className={`text-[14px] font-semibold leading-tight ${vixColor(vix)}`}>
                {vix != null ? Number(vix).toFixed(1) : '\u2014'}
              </span>
              {vixChg != null && (
                <span className={`text-[10px] leading-none ${
                  Number(vixChg) > 0 ? 'text-trading-red-400' : Number(vixChg) < 0 ? 'text-trading-green-400' : 'text-gray-600'
                }`}>
                  {Number(vixChg) >= 0 ? '\u25b2' : '\u25bc'}{Math.abs(Number(vixChg)).toFixed(1)}%
                </span>
              )}
            </div>
            <div className="flex flex-col items-end gap-0.5 mt-1.5 text-[11px]">
              <div className="flex items-center gap-1" title={callExitTip}>
                <span className="text-[9px] text-gray-600">C</span>
                <span className={isStressed ? 'text-trading-green-300 font-semibold' : 'text-trading-green-400/70'}>{callTP}</span>
                <span className="text-gray-700">/</span>
                <span className={isStressed ? 'text-amber-300 font-semibold' : 'text-trading-red-400/70'}>{callSL}</span>
              </div>
              <div className="flex items-center gap-1" title={putExitTip}>
                <span className="text-[9px] text-gray-600">P</span>
                <span className="text-trading-green-400/70">{putTpStr}</span>
                <span className="text-gray-700">/</span>
                <span className="text-trading-red-400/70">{putSlStr}</span>
              </div>
              {practicalTip && (
                <div className="flex items-center gap-1" title={practicalTip}>
                  <span className="text-[9px] text-gray-600">PX</span>
                  <span className="text-gray-400/80">{grossCapStr}</span>
                  <span className="text-gray-700">/</span>
                  <span className="text-trading-green-400/70">{callCapStr}</span>
                  <span className="text-gray-700">/</span>
                  <span className="text-trading-red-400/70">{putCapStr}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Breadth panel — header always visible, content collapses ── */}
      <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 px-3 py-2.5">
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          className="flex w-full flex-col items-stretch gap-1.5 text-left hover:opacity-80 transition-opacity"
        >
          <div className="flex w-full items-center justify-between gap-2">
            <span className="shrink-0 text-[10px] uppercase tracking-wider text-gray-600">Market Breadth</span>
            {collapsed
              ? <ChevronDown className="h-3 w-3 shrink-0 text-gray-600" />
              : <ChevronUp className="h-3 w-3 shrink-0 text-gray-600" />
            }
          </div>
          <div className="flex w-full flex-wrap items-center gap-1">
            {summaryChips.map(chip => (
              <BreadthSummaryChip
                key={chip.label}
                label={chip.label}
                value={chip.value}
                toneClass={chip.toneClass}
                title={chip.title}
              />
            ))}
            {activeBreadthSignals.map(signal => (
              <span
                key={signal.key}
                title={signal.title}
                className={`inline-flex h-5 shrink-0 cursor-help items-center whitespace-nowrap rounded-sm border px-1.5 text-[9px] font-semibold uppercase tracking-[0.08em] leading-none ${signalPillClass(signal.tone)}`}
              >
                {signal.label}
              </span>
            ))}
          </div>
        </button>
        {!collapsed && (
          <div className="mt-2 border-t border-white/[0.06] pt-2">
            <div className="grid grid-cols-2 gap-x-3">
              <div className="min-w-0">
                <div className="mb-1.5 text-[10px] uppercase tracking-wider text-gray-500">All Stocks</div>
                <div className="flex flex-col gap-1">
                  <Row label="Score" tip={BREADTH_TIPS.breadthScore}>
                    <span className={scoreSentimentClass(bScore)}>{metricNumber(bScore)}</span>
                  </Row>
                  <Row label="A/D" tip={BREADTH_TIPS.ad}>
                    <span className="text-trading-green-400">{adv}</span>
                    <span className="mx-0.5 text-gray-700">/</span>
                    <span className="text-trading-red-400">{dec}</span>
                    <span className={`ml-1 text-[10px] font-normal ${
                      advPct >= 60 ? 'text-trading-green-400' : advPct <= 40 ? 'text-trading-red-400' : 'text-gray-500'
                    }`}>
                      {advPct.toFixed(0)}%
                    </span>
                  </Row>
                  <Row label="McC" tip={BREADTH_TIPS.mcclellan}>
                    <span className={mcColor(b?.mcclellan_oscillator)}>{signedMetricNumber(b?.mcclellan_oscillator)}</span>
                  </Row>
                  <Row label=">50/200" tip={`${BREADTH_TIPS.ema50}\n\n${BREADTH_TIPS.ema200}`}>
                    <span className={pctMAColor(b?.pct_above_ema50)}>{pctMetric(b?.pct_above_ema50)}</span>
                    <span className="mx-0.5 text-gray-700">/</span>
                    <span className={pctMAColor(b?.pct_above_ema200)}>{pctMetric(b?.pct_above_ema200)}</span>
                  </Row>
                  <Row label="52w" tip={BREADTH_TIPS.hl52}>
                    <span className="text-trading-green-400">{b?.new_highs_52w ?? '\u2014'}</span>
                    <span className="mx-0.5 text-gray-700">/</span>
                    <span className="text-trading-red-400">{b?.new_lows_52w ?? '\u2014'}</span>
                  </Row>
                  <Row label="TRIN" tip={BREADTH_TIPS.trin}>
                    <span className={trinColor(b?.trin)}>{metricNumber(b?.trin, 2)}</span>
                  </Row>
                </div>
              </div>

              <div className="min-w-0 border-l border-white/[0.06] pl-3">
                <div className="mb-1.5 flex items-baseline justify-between gap-1">
                  <span className="text-[10px] uppercase tracking-wider text-gray-500">Sector ETFs</span>
                  {sectorDateLabel && (
                    <span
                      title={BREADTH_TIPS.sectorEtfDate}
                      className={`text-[9px] font-mono tabular-nums ${sectorEtfStale ? 'text-amber-300' : 'text-gray-600'}`}
                    >
                      {sectorDateLabel}
                    </span>
                  )}
                </div>
                <div className="flex flex-col gap-1">
                  <Row label="Wave" tip={marketWaveTitle || BREADTH_TIPS.marketWave}>
                    <span className={marketWaveColor(marketWaveScore)}>{metricNumber(marketWaveScore)}</span>
                  </Row>
                  <Row label=">50" tip={BREADTH_TIPS.sectorEtf50}>
                    <span className={pctMAColor(sectorEtf?.pct_above_ema50)}>
                      {sectorEtf?.pct_above_ema50 != null
                        ? `${pctMetric(sectorEtf.pct_above_ema50)} (${sectorAbove50Count ?? '\u2014'}/${sectorEtf.issues || '\u2014'})`
                        : '\u2014'}
                    </span>
                  </Row>
                  <Row label="5/15" tip={`${BREADTH_TIPS.sectorEtf5d}\n\n${BREADTH_TIPS.sectorEtf15d}`}>
                    <span className={pctColor(sectorEtf?.breadth_5d_change)}>{signedMetricNumber(sectorEtf?.breadth_5d_change)}</span>
                    <span className="mx-0.5 text-gray-700">/</span>
                    <span className={pctColor(sectorEtf?.breadth_15d_change)}>{signedMetricNumber(sectorEtf?.breadth_15d_change)}</span>
                  </Row>
                  <Row label="Pos" tip={BREADTH_TIPS.sectorEtfPosition}>
                    <span className={pctColor(sectorEtf?.breadth_30d_position)}>{signedMetricNumber(sectorEtf?.breadth_30d_position)}</span>
                  </Row>
                  <Row label=">200" tip={BREADTH_TIPS.sectorEtf200}>
                    <span className={pctMAColor(sectorEtf?.pct_above_ema200)}>{pctMetric(sectorEtf?.pct_above_ema200)}</span>
                  </Row>
                  <Row label="RSI" tip={BREADTH_TIPS.sectorEtfRsi}>
                    <span className={scoreSentimentClass(sectorEtf?.avg_rsi)}>{metricNumber(sectorEtf?.avg_rsi, 1)}</span>
                  </Row>
                </div>
              </div>
            </div>

            {(activeBreadthSignals.length > 0 || sectorEtfStale) && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-white/[0.06] pt-2">
                {activeBreadthSignals.map(signal => (
                  <span
                    key={signal.key}
                    title={signal.title}
                    className={`cursor-help rounded-sm border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] ${signalPillClass(signal.tone)}`}
                  >
                    {signal.fullLabel}
                  </span>
                ))}
                {sectorEtfStale && (
                  <span
                    title={BREADTH_TIPS.sectorEtfDate}
                    className="cursor-help rounded-sm border border-amber-300/25 bg-amber-300/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.08em] text-amber-300"
                  >
                    ETF stale {sectorDateLabel}
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

/* ── Earnings Calendar ── */

const EarningsCalendar = ({ earningsData }) => {
  const todayIndex = earningsData?.today_index;
  const days = dayNames.map((dayName, idx) => {
    const raw = earningsData?.earnings?.[idx] || { before: [], during: [], after: [] };
    return {
      dayName,
      ...raw,
      after: [...(raw.after || []), ...(raw.during || [])],
      during: [],
    };
  });

  const now = new Date();
  const etParts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(now);
  const etHour = parseInt(etParts.find((p) => p.type === 'hour').value, 10);
  const etMinute = parseInt(etParts.find((p) => p.type === 'minute').value, 10);
  const etMinutes = etHour * 60 + etMinute;
  const RTH_OPEN = 9 * 60 + 30;
  const RTH_CLOSE = 16 * 60;
  const isWeekdayColumn = todayIndex != null && todayIndex >= 0 && todayIndex <= 4;
  const isBeforeRth = isWeekdayColumn && etMinutes < RTH_OPEN;
  const isAfterRth = isWeekdayColumn && etMinutes >= RTH_CLOSE;
  const isRth = isWeekdayColumn && etMinutes >= RTH_OPEN && etMinutes < RTH_CLOSE;

  const highlightBMO = (idx) => {
    if (!isWeekdayColumn) return idx === 0;
    if (isBeforeRth && idx === todayIndex) return true;
    if (isRth || isAfterRth) {
      if (todayIndex < 4) return idx === todayIndex + 1;
      return idx === 0;
    }
    return false;
  };
  const highlightAMC = (idx) => {
    if (!isWeekdayColumn) return false;
    if (isRth || isAfterRth) return idx === todayIndex;
    return false;
  };

  return (
    <div className="flex flex-col h-full w-full min-w-0">
      {/* Day + date inline header row */}
      <div className="flex w-full shrink-0 pb-1 mb-1 border-b border-white/[0.04]">
        {days.map((dayData, idx) => {
          const isFriday = idx === 4;
          const bmoHighlight = highlightBMO(idx);
          const amcHighlight = highlightAMC(idx);
          const anyHighlight = bmoHighlight || amcHighlight;
          return (
            <div key={idx} className={`${isFriday ? 'flex-[0.65_0_0]' : 'flex-1'} flex items-center justify-center min-w-0 ${dayData.is_next_week ? 'opacity-40' : ''}`}>
              <div className="flex items-baseline gap-1">
                <span className={`text-[9px] tracking-wide ${bmoHighlight ? 'text-amber-400/50' : 'text-gray-800'}`}>before</span>
                <span className={`text-[11px] font-semibold tracking-wide ${anyHighlight ? 'text-amber-400' : 'text-gray-400'}`}>
                  {dayData.dayName}
                </span>
                <span className={`text-[10px] font-mono tabular-nums ${dayData.is_next_week ? 'text-gray-700' : 'text-gray-600'}`}>
                  {formatDateLabel(dayData.date)}
                </span>
                {!isFriday && (
                  <span className={`text-[9px] tracking-wide ${amcHighlight ? 'text-amber-400/50' : 'text-gray-800'}`}>after</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Per-day columns: BMO left | divider | AMC right (Friday: BMO only) */}
      <div className="flex flex-1 min-h-0 gap-1">
        {days.map((dayData, idx) => {
          const isFriday = idx === 4;
          const isNextWeek = dayData.is_next_week;
          const bmoHighlight = highlightBMO(idx);
          const amcHighlight = highlightAMC(idx);
          const anyHighlight = bmoHighlight || amcHighlight;
          const before = dayData.before || [];
          const after = isFriday ? [] : (dayData.after || []);

          return (
            <div key={idx} className={`${isFriday ? 'flex-[0.65_0_0]' : 'flex-1'} flex min-w-0 rounded px-0.5 py-0.5 ${
              anyHighlight
                ? 'bg-trading-dark-800/60 border border-amber-500/40 shadow-[0_0_15px_rgba(251,191,36,0.2)]'
                : isNextWeek ? 'opacity-40' : ''
            }`}>
              {before.length > 0 && (
                <div className={`flex flex-1 min-w-0 rounded-sm ${bmoHighlight ? 'bg-amber-500/[0.06]' : ''}`}>
                  <SubCols stocks={before} isNextWeek={isNextWeek} />
                </div>
              )}
              {before.length > 0 && after.length > 0 && (
                <div className="w-px self-stretch bg-white/[0.06] mx-0.5 shrink-0" />
              )}
              {after.length > 0 && (
                <div className={`flex flex-1 min-w-0 rounded-sm ${amcHighlight ? 'bg-amber-500/[0.06]' : ''}`}>
                  <SubCols stocks={after} isNextWeek={isNextWeek} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ── Mobile compact market strip ── */

const MobileMarketStrip = ({ breadthData, regimeData }) => {
  const b = breadthData;
  const r = regimeData;
  const composite = r?.regime_composite;
  const mult = r?.regime_multiplier;
  const label = regimeLabel(composite);
  const m = mult != null ? Number(mult) : null;
  const bScore = r?.market_breadth_score ?? b?.breadth_score;

  const strategyCfg = useStrategyCfg30();
  const opt = strategyCfg?.option || DEFAULT_30DTE_OPTION;
  const tips = getStrategyTips(opt);
  const practicalTip = getPracticalExposureTip(strategyCfg);
  const brdThresh  = opt.BREADTH_THRESHOLD;
  const putTpStr   = `${Math.round(opt.PUT_TP * 100)}%`;
  const putSlStr   = `${Math.round(Math.abs(opt.PUT_SL) * 100)}%`;
  const tpBaseStr   = `${Math.round(opt.TP_BASE * 100)}%`;
  const tpStressStr = `${Math.round(opt.TP_STRESS * 100)}%`;
  const slBaseStr   = `${Math.round(Math.abs(opt.SL_BASE) * 100)}%`;
  const slStressStr = `${Math.round(Math.abs(opt.SL_STRESS) * 100)}%`;

  const isStressed = bScore != null && Number(bScore) <= brdThresh;
  const callTP = isStressed ? tpStressStr : tpBaseStr;
  const callSL = isStressed ? slStressStr : slBaseStr;
  const callExitTip = [tips.recTP, tips.recSL, practicalTip].filter(Boolean).join('\n\n');
  const putExitTip = [tips.putTpSl, practicalTip].filter(Boolean).join('\n\n');
  const regimeStripTip = [regimeSubtitle(label).join(' · '), practicalTip].filter(Boolean).join('\n\n');

  return (
    <div className="flex-1 min-w-0 flex items-center justify-between gap-2 text-[12px] font-mono tabular-nums">
      <div className="relative min-w-0 pl-2.5 flex items-center gap-1.5" title={regimeStripTip}>
        <span className={`absolute left-0 top-0 bottom-0 w-[2px] rounded-full ${regimeRail(label)}`} aria-hidden="true" />
        <span className={`font-semibold tracking-wide truncate ${regimeColor(composite)}`}>{label}</span>
        <span className={`text-[12px] ${multSentimentClass(mult)}`}>
          {m != null ? `${m.toFixed(2)}\u00d7` : '\u2014'}
        </span>
      </div>

      <div className="shrink-0 flex items-center gap-1.5" title={[callExitTip, putExitTip].filter(Boolean).join('\n\n')}>
        <span className="text-[10px] uppercase tracking-wider text-gray-500">TP/SL</span>
        <span className="flex items-center gap-0.5" title={callExitTip}>
          <span className="text-gray-500">C</span>
          <span className={isStressed ? 'text-trading-green-300 font-semibold' : 'text-trading-green-400'}>{callTP}</span>
          <span className="text-gray-600">/</span>
          <span className={isStressed ? 'text-amber-300 font-semibold' : 'text-trading-red-300'}>{callSL}</span>
        </span>
        <span className="flex items-center gap-0.5" title={putExitTip}>
          <span className="text-gray-500">P</span>
          <span className="text-trading-green-400">{putTpStr}</span>
          <span className="text-gray-600">/</span>
          <span className="text-trading-red-300">{putSlStr}</span>
        </span>
      </div>
    </div>
  );
};

/* ── Coverage warning (visible when >5% of stocks are missing scores) ── */
// Empirical: ~8 seconds per stock pull during `trader update`.
const SEC_PER_STOCK = 8;
const COVERAGE_THRESHOLD = 0.95;

const LoadingWarning = ({ loaded, total, hasLoadedStocks }) => {
  if (!hasLoadedStocks || !total || total <= 0) return null;
  const coverage = loaded / total;
  if (coverage >= COVERAGE_THRESHOLD) return null;

  const missing = Math.max(0, total - loaded);
  const etaSec = missing * SEC_PER_STOCK;
  const h = Math.floor(etaSec / 3600);
  const m = Math.floor((etaSec % 3600) / 60);
  const s = etaSec % 60;
  const durationStr = h > 0
    ? `${h}h ${m}m`
    : m > 0
      ? `${m}m ${s}s`
      : `${s}s`;
  const eta = new Date(Date.now() + etaSec * 1000);
  const etaClock = eta.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
  const pctStr = (coverage * 100).toFixed(0);

  return (
    <div className="px-3 sm:px-4 py-2 bg-amber-500/10 border-y border-amber-500/30 flex items-center gap-2 text-[11px] sm:text-xs">
      <span className="relative flex h-2 w-2 shrink-0">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-400" />
      </span>
      <span className="font-medium text-amber-300">Pull in progress…</span>
      <span className="text-amber-300/70 truncate">
        {loaded.toLocaleString()} / {total.toLocaleString()} stocks loaded ({pctStr}%) — ~{durationStr} remaining (done by {etaClock})
      </span>
    </div>
  );
};

/* ── Dashboard ── */

const PULL_THRESHOLD = 64; // px of overscroll before refresh fires

const getLatestScoreUpdatedAt = (stocks) => {
  let latest = 0;
  for (const stock of stocks || []) {
    const updatedAt = stock?.updated_at ? new Date(stock.updated_at).getTime() : 0;
    if (!Number.isNaN(updatedAt) && updatedAt > latest) {
      latest = updatedAt;
    }
  }
  return latest ? new Date(latest).toISOString() : null;
};

const Dashboard = () => {
  const {
    stocks,
    stockCounts,
    fetchStocks,
    fetchComprehensiveStats,
    availableScoreVersions,
    legacyScoreVersions,
    scoreVersion,
    activeScoreVersionId,
    selectedScoreVersionId,
    setScoreVersion,
    scoreDate,
  } = useStock();
  const [earningsData, setEarningsData] = useState(null);
  const [regimeData, setRegimeData] = useState(null);
  const [breadthData, setBreadthData] = useState(null);
  const latestScoreUpdatedAt = React.useMemo(() => getLatestScoreUpdatedAt(stocks), [stocks]);
  const latestScoreUpdatedRef = React.useRef(latestScoreUpdatedAt);

  const [collapsed, setCollapsed] = useState(() => {
    const stored = localStorage.getItem('dashboardCollapsed');
    return stored === null ? true : stored === 'true';
  });

  const toggleCollapsed = () => setCollapsed(v => {
    const next = !v;
    localStorage.setItem('dashboardCollapsed', next);
    return next;
  });

  // ── Pull-to-refresh (mobile) ──────────────────────────────────────────────
  // pullYRef holds the live pull distance; pullDisplay mirrors it into state
  // for rendering only.  onTouchEnd reads the ref directly so it always sees
  // the current value rather than a stale closure over the last render's state.
  const pullYRef = useRef(0);
  const [pullDisplay, setPullDisplay] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const mobileScrollRef = useRef(null);
  const touchStartRef = useRef(null);

  const onTouchStart = useCallback((e) => {
    // Only activate when the list is truly at the top.
    if ((mobileScrollRef.current?.scrollTop ?? 1) === 0) {
      touchStartRef.current = e.touches[0].clientY;
    } else {
      touchStartRef.current = null;
    }
  }, []);

  const onTouchMove = useCallback((e) => {
    if (touchStartRef.current === null) return;
    // If the user scrolled away from the top mid-gesture, cancel.
    if ((mobileScrollRef.current?.scrollTop ?? 1) > 0) {
      touchStartRef.current = null;
      pullYRef.current = 0;
      setPullDisplay(0);
      return;
    }
    const dy = e.touches[0].clientY - touchStartRef.current;
    if (dy > 0 && e.cancelable) {
      e.preventDefault();
    }
    // Always update — including reset to 0 when the finger moves back up past
    // the start point (dy ≤ 0).  Without this the value stays stuck at the
    // last positive dy and onTouchEnd fires a false refresh on release.
    const next = dy > 0 ? Math.min(dy * 0.45, PULL_THRESHOLD * 1.5) : 0;
    pullYRef.current = next;
    setPullDisplay(next);
  }, []);

  const resetPull = useCallback(() => {
    touchStartRef.current = null;
    pullYRef.current = 0;
    setPullDisplay(0);
  }, []);

  const onTouchEnd = useCallback(async () => {
    const pulled = pullYRef.current;  // read ref, not stale closure state
    resetPull();
    if (pulled >= PULL_THRESHOLD && !isRefreshing) {
      setIsRefreshing(true);
      try {
        await Promise.all([fetchComprehensiveStats(), fetchStocks()]);
      } finally {
        setIsRefreshing(false);
      }
    }
  }, [isRefreshing, fetchComprehensiveStats, fetchStocks, resetPull]);

  useEffect(() => {
    const apiBase = getApiBaseUrl();
    const bust = Math.floor(Date.now() / 30000);
    fetch(`${apiBase}/api/earnings/weekly?_t=${bust}`)
      .then(res => res.json())
      .then(data => setEarningsData(data))
      .catch(err => console.error('Error fetching earnings:', err));
    fetch(`${apiBase}/api/market/regime?_t=${bust}`)
      .then(res => res.json())
      .then(data => setRegimeData(data))
      .catch(err => console.error('Error fetching regime:', err));
    fetch(`${apiBase}/api/market/breadth?_t=${bust}`)
      .then(res => res.json()  )
      .then(data => setBreadthData(data))
      .catch(err => console.error('Error fetching breadth:', err));
  }, []);

  latestScoreUpdatedRef.current = latestScoreUpdatedAt;
  const autoRefreshInProgress = useRef(false);
  const resumeRefreshInProgress = useRef(false);
  const lastResumeRefreshRef = useRef(0);
  useEffect(() => {
    const interval = setInterval(async () => {
      if (autoRefreshInProgress.current) return;
      if (shouldAutoRefresh(latestScoreUpdatedRef.current)) {
        autoRefreshInProgress.current = true;
        try {
          await fetchStocks();
        } finally {
          autoRefreshInProgress.current = false;
        }
      }
    }, 2 * 60 * 1000);
    return () => clearInterval(interval);
  }, [fetchStocks]); // score timestamp read via ref — no need to recreate interval on each fetch

  const refreshDashboardIfStale = useCallback(async () => {
    const now = Date.now();
    if (resumeRefreshInProgress.current || now - lastResumeRefreshRef.current < 60000) return;
    if (!shouldRefreshOnResume(latestScoreUpdatedRef.current)) return;

    resumeRefreshInProgress.current = true;
    lastResumeRefreshRef.current = now;
    try {
      await Promise.all([fetchComprehensiveStats(), fetchStocks()]);
    } finally {
      resumeRefreshInProgress.current = false;
    }
  }, [fetchComprehensiveStats, fetchStocks]);

  useEffect(() => {
    const onResume = () => {
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      void refreshDashboardIfStale();
    };
    document.addEventListener('visibilitychange', onResume);
    window.addEventListener('focus', onResume);
    window.addEventListener('pageshow', onResume);
    return () => {
      document.removeEventListener('visibilitychange', onResume);
      window.removeEventListener('focus', onResume);
      window.removeEventListener('pageshow', onResume);
    };
  }, [refreshDashboardIfStale]);

  const stats = React.useMemo(() => ({
    positiveChange: stockCounts.positiveChange || 0,
    negativeChange: stockCounts.negativeChange || 0,
    percentageUp: parseFloat(stockCounts.percentageUp) || 0,
  }), [stockCounts]);

  const majorIndices = stockCounts.major_indices || {};
  const coverageTotal = stockCounts.coverageTotal || stockCounts.total;
  const hasLoadedStocks = stocks.length > 0;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Mobile: top bar */}
      <div className="sm:hidden fixed top-0 left-0 right-0 z-30 h-12 flex items-center pt-2 pl-14 pr-3 border-b border-white/[0.06] bg-trading-dark-950">
        <MobileMarketStrip breadthData={breadthData} regimeData={regimeData} />
      </div>

      {/* sm+: market panel + earnings calendar */}
      <div className="hidden sm:block bg-trading-dark-950 border-b border-white/[0.06] p-3 lg:p-4">
        <div className="flex w-full min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:gap-4">

          {/* Left column: regime card (always) + breadth panel (when expanded) */}
          <MarketPanel
            breadthData={breadthData}
            regimeData={regimeData}
            stats={stats}
            collapsed={collapsed}
            toggleCollapsed={toggleCollapsed}
          />

          {/* Right column: when collapsed, capped to ~regime-card height via max-h
              (CSS-only — JS measurement of the left column was unreliable on
              Safari PWA, leaving a stale-height block at the bottom). */}
          <div
            className={`flex min-w-0 flex-1 flex-col overflow-hidden ${
              collapsed ? 'max-h-[170px]' : ''
            }`}
          >
            {/* Toggle header */}
            <button
              onClick={toggleCollapsed}
              className="flex items-center gap-3 w-full mb-1.5 pb-1.5 border-b border-white/[0.06] hover:opacity-80 transition-opacity shrink-0"
            >
              <div
                className={`flex items-center gap-x-3 gap-y-1 flex-1 font-mono tabular-nums text-[11px] ${
                  collapsed ? 'flex-nowrap overflow-hidden' : 'flex-wrap'
                }`}
              >
                {INDEX_ORDER.map(sym => {
                  const chg = majorIndices[sym];
                  if (chg == null) return null;
                  const label = INDEX_LABELS[sym];
                  const display = sym.replace(/\.TO$/, '');
                  return (
                    <span
                      key={sym}
                      className="flex items-center gap-1"
                      title={label ? `${display} — ${label}` : display}
                    >
                      <span className="text-gray-600">{display}</span>
                      <span className={pctColor(chg)}>{chg >= 0 ? '+' : ''}{chg.toFixed(2)}%</span>
                    </span>
                  );
                })}
              </div>
              <span className="text-[10px] uppercase tracking-wider text-gray-600 shrink-0">Earnings Calendar</span>
              {collapsed
                ? <ChevronDown className="w-3 h-3 text-gray-600 shrink-0" />
                : <ChevronUp className="w-3 h-3 text-gray-600 shrink-0" />
              }
            </button>

            {/* Collapsed: fill the constrained height and clip overflow.
                Expanded: natural height — calendar renders its full content. */}
            {collapsed
              ? <div onClick={toggleCollapsed} className="flex-1 min-h-0 overflow-hidden cursor-pointer"><EarningsCalendar earningsData={earningsData} /></div>
              : <div onClick={toggleCollapsed} className="cursor-pointer"><EarningsCalendar earningsData={earningsData} /></div>
            }
          </div>

        </div>
      </div>

      {/* Mobile */}
      <div className="flex sm:hidden flex-1 min-h-0 flex-col overflow-hidden">
        <FilterBar
          rightControl={(
            <ScoreVersionSelector
              versions={availableScoreVersions}
              legacyVersions={legacyScoreVersions}
              currentVersion={scoreVersion}
              activeVersionId={activeScoreVersionId}
              scoreDate={scoreDate}
              selectedVersionId={selectedScoreVersionId}
              onSelect={setScoreVersion}
              ariaLabel="Select dashboard score version"
              titlePrefix="Dashboard scores"
            />
          )}
        />
        <LoadingWarning loaded={stocks.length} total={coverageTotal} hasLoadedStocks={hasLoadedStocks} />
        <div
          ref={mobileScrollRef}
          className="flex-1 min-h-0 overflow-y-scroll"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehaviorY: 'contain', touchAction: 'pan-y' }}
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
          onTouchCancel={resetPull}
        >
          {/* Pull-to-refresh indicator */}
          <div
            style={{
              height: isRefreshing ? 48 : pullDisplay,
              transition: (isRefreshing || pullDisplay === 0) ? 'height 0.25s ease' : 'none',
              overflow: 'hidden',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {isRefreshing ? (
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-trading-blue-400" />
            ) : pullDisplay > 8 ? (
              <svg
                style={{
                  opacity: Math.min(pullDisplay / PULL_THRESHOLD, 1),
                  transform: `rotate(${Math.min(pullDisplay / PULL_THRESHOLD, 1) * 180}deg)`,
                  transition: 'transform 0.1s',
                }}
                className="w-5 h-5 text-gray-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            ) : null}
          </div>
          <StockTable layoutVersion={collapsed ? 'collapsed' : 'expanded'} />
        </div>
      </div>

      {/* Desktop */}
      <div className="hidden sm:flex sm:flex-1 sm:flex-col sm:overflow-hidden">
        <FilterBar
          rightControl={(
            <ScoreVersionSelector
              versions={availableScoreVersions}
              legacyVersions={legacyScoreVersions}
              currentVersion={scoreVersion}
              activeVersionId={activeScoreVersionId}
              scoreDate={scoreDate}
              selectedVersionId={selectedScoreVersionId}
              onSelect={setScoreVersion}
              ariaLabel="Select dashboard score version"
              titlePrefix="Dashboard scores"
            />
          )}
        />
        <LoadingWarning loaded={stocks.length} total={coverageTotal} hasLoadedStocks={hasLoadedStocks} />
        <div className="flex-1 min-h-0 overflow-auto">
          <div className="px-4 py-3 lg:px-6 lg:py-4">
            <StockTable layoutVersion={collapsed ? 'collapsed' : 'expanded'} />
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
