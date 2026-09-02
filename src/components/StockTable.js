import React, { useState, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { List } from 'react-window';
import { ChevronUp, ChevronDown, TrendingUp, Star } from 'lucide-react';
import { useStock } from '../context/StockContext';
import ScoreBadge from './ScoreBadge';
import { formatUpdatedTime } from '../utils/timeUtils';

/* Volume-signal SVG glyphs — rendered in muted tones (no opacity tricks). */
const SIGNAL_ICONS = {
  CONVICTION: {
    tooltip: 'Conviction — Strong directional move on high volume',
    render: (color) => (
      <svg width="12" height="16" viewBox="0 0 14 18">
        <rect x="2" y="1" width="10" height="16" rx="1" fill={color} />
      </svg>
    ),
  },
  THIN_AIR: {
    tooltip: 'Thin Air — Large move on very low volume (unreliable)',
    render: (color) => (
      <svg width="12" height="16" viewBox="0 0 14 18">
        <rect x="5" y="1" width="4" height="16" rx="1" fill={color} />
      </svg>
    ),
  },
  ABSORPTION: {
    tooltip: 'Absorption — High volume, tiny body (doji). Reversal setup.',
    render: (color) => (
      <svg width="12" height="16" viewBox="0 0 14 18">
        <line x1="7" y1="1" x2="7" y2="17" stroke={color} strokeWidth="1" opacity="0.5" />
        <rect x="2" y="8" width="10" height="2" rx="0.5" fill={color} />
      </svg>
    ),
  },
  REJECTION: {
    tooltip: 'Rejection — Price pushed hard then reversed on high volume',
    render: (color) => (
      <svg width="12" height="16" viewBox="0 0 14 18">
        <line x1="7" y1="1" x2="7" y2="10" stroke={color} strokeWidth="1.5" opacity="0.55" />
        <rect x="3" y="11" width="8" height="5" rx="1" fill={color} />
      </svg>
    ),
  },
  CLIMAX: {
    tooltip: 'Climax — Extreme volume, potential exhaustion/reversal',
    render: (color) => (
      <svg width="12" height="16" viewBox="0 0 14 18">
        <rect x="1" y="1" width="12" height="16" rx="1" fill={color} />
        <line x1="1" y1="9" x2="13" y2="9" stroke="#0A0A0B" strokeWidth="1" opacity="0.5" />
      </svg>
    ),
  },
};

// eslint-disable-next-line no-unused-vars
const VolumeSignalIcon = ({ signal, volScore }) => {
  if (!signal || signal === 'NEUTRAL') return null;
  const cfg = SIGNAL_ICONS[signal];
  if (!cfg) return null;
  const dir = volScore != null && volScore !== 50 ? (volScore > 50 ? 'up' : 'down') : null;
  const color = dir === 'up' ? '#8FB286' : dir === 'down' ? '#C98484' : '#71717A';
  const dirLabel = dir === 'up' ? ' (Bullish)' : dir === 'down' ? ' (Bearish)' : '';
  return (
    <span title={cfg.tooltip + dirLabel} className="inline-flex items-center cursor-help">
      {cfg.render(color)}
    </span>
  );
};

/* Thesis indicator — flat colored dot + label, no glow. */
const DTE_THESIS = {
  BOUNCE:   { label: 'BNCE', color: 'text-amber-400',         dot: 'bg-amber-400' },
  REVERSAL: { label: 'RVSL', color: 'text-trading-red-400',   dot: 'bg-trading-red-400' },
  MOMENTUM: { label: 'MOM',  color: 'text-trading-blue-400',  dot: 'bg-trading-blue-400' },
  TREND:    { label: 'TRND', color: 'text-trading-green-400', dot: 'bg-trading-green-400' },
};

// eslint-disable-next-line no-unused-vars
const DteBadge = ({ thesis, target, min, max, confidence, tradeable, filterSide }) => {
  if (!thesis || target == null) {
    return <span className="text-gray-700 text-sm font-mono tabular-nums">—</span>;
  }
  const cfg = DTE_THESIS[thesis] || { label: thesis, color: 'text-gray-400', dot: 'bg-gray-400' };
  const norm = (confidence || 'MEDIUM').toString().toUpperCase();
  const isHigh = norm === 'HIGH' || tradeable;
  const isLow = norm === 'LOW' && !tradeable;

  const tooltip = `${thesis}: ${min}–${max} DTE, target ${target}d (${confidence || '?'} confidence)${
    tradeable ? ` · entry_filter ${filterSide === 'long' ? 'CALL' : 'PUT'} cleared` : ''
  }`;

  return (
    <div className="flex flex-col items-center leading-none" title={tooltip}>
      <span className={`inline-flex items-center gap-1 text-[11px] font-mono font-semibold tracking-wider ${
        isLow ? 'text-gray-600' : isHigh ? cfg.color : `${cfg.color} opacity-80`
      }`}>
        <span className={`w-1 h-1 rounded-full ${isLow ? 'bg-gray-600' : cfg.dot}`} aria-hidden="true" />
        {cfg.label}
        {tradeable && <span className="text-trading-green-300" aria-label="entry_filter cleared">·</span>}
      </span>
      <span className={`text-[11px] font-mono mt-0.5 tabular-nums ${isLow ? 'text-gray-700' : 'text-gray-500'}`}>
        {target}d
      </span>
    </div>
  );
};

/* Counter-trend tag badge.
 * Surfaces signals that the optimal-strategy cascade promotes to a higher
 * allocation tier (CT V2, shipped 2026-04-21):
 *   ct_call (overall ≥ 70 ∧ TREND ≤ 20)  → 25% ultra tier (vs ≤15% baseline)
 *   ct_put  (overall ≤ 25 ∧ TREND ≥ 80)  → 15% put_top tier
 * Validation: CT-PUT WR15=81.5% vs put baseline 66.7% (5y diff-assess);
 * CT-CALL similar pattern. */
const CT_BADGE = {
  ct_call: {
    label: 'CT↑',
    cls: 'text-trading-green-300 bg-trading-green-500/10 border border-trading-green-500/30',
    tip:
      'COUNTER-TREND CALL — score ≥ 70 with deeply bearish TREND (≤ 20).\n' +
      'Bullish reversal setup against an entrenched downtrend.\n' +
      'INCREASED CONVICTION: optimal strategy promotes to the 25% ultra tier ' +
      '(vs ≤15% normal cascade). CT-CALL historically shows the cleanest WR ' +
      'in the call population.',
  },
  ct_put: {
    label: 'CT↓',
    cls: 'text-trading-red-300 bg-trading-red-500/10 border border-trading-red-500/30',
    tip:
      'COUNTER-TREND PUT — score ≤ 25 with deeply bullish TREND (≥ 80).\n' +
      'Bearish reversal setup against an entrenched uptrend.\n' +
      'INCREASED CONVICTION: optimal strategy promotes to the 15% put_top tier. ' +
      'CT-PUT WR15 ≈ 81.5% (5y) vs put baseline 66.7% — the highest-quality ' +
      'sub-bucket on the put side.',
  },
};

const CtBadge = ({ tag }) => {
  if (!tag) return null;
  const cfg = CT_BADGE[tag];
  if (!cfg) return null;
  return (
    <span
      title={cfg.tip}
      className={`inline-flex items-center px-1 rounded text-[9px] font-mono font-semibold tracking-wider leading-none h-[14px] cursor-help ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
};

/* SkipBadge — surfaces cascade-stage filters that drop a signal from the
   shipped cascade despite the score showing on the dashboard.  The strategy
   does NOT trade these signals, so we mark them visually so the user knows
   not to deploy capital against them.
   Shipped 2026-05-06 after audit revealed EARN_SUPP_PUT (10 days live) and
   WEAK_WEEKLY_CALL_DROP (1 day live) were silently filtering signals. */
const SKIP_BADGE = {
  // EARN_SUPP_PUT — RETIRED 2026-05-06.  Replaced by score-stage PESS in v39;
  // signals in [16,20] with earnings ≤5 trd days now drift to ~28 in the score
  // itself (out of put-qualifying universe).  No API emits this reason now.
  // WEAK_WEEKLY_CALL_DROP — RETIRED 2026-05-06.  Replaced by score-stage CWWD
  // in v38; signals in [70,84] with wadj<0 ∧ stoch≥35 now drift below 70 in
  // the score itself.  No API emits this reason now.
};

const SkipBadge = ({ skip }) => {
  if (!skip || !skip.reason) return null;
  const cfg = SKIP_BADGE[skip.reason];
  if (!cfg) return null;
  const fullTip = cfg.tip + (skip.detail ? `\n\nDetail: ${skip.detail}` : '');
  return (
    <span
      title={fullTip}
      className={`inline-flex items-center px-1 rounded text-[9px] font-mono font-semibold tracking-wider leading-none h-[14px] cursor-help ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
};

/* SwingBadge — flags intraday score instability for TODAY from score_intraday_logs.
   `Score` overwrites today's row on every `trader update`; this surfaces how far
   the score swung across the day's update runs. A "fakeout" (amber) = big score
   swing on a small price move — the score moved but the stock didn't, usually a
   scoring artifact (weekly partial→completed flip, regime shift, or volume-amplifier
   reclassification of the evolving intraday candle) rather than a real change in
   the setup. A large swing where price also moved (gray) may be genuine repricing.
   Shipped 2026-05-29.  Diagnose the cause: `trader intraday-drill SYM`. */
const SWING_NOTABLE = 15;   // pts — show the badge at/above this intraday spread
const SwingBadge = ({ swing }) => {
  if (!swing || swing.spread < SWING_NOTABLE) return null;
  const fake = swing.is_fakeout;
  const cls = fake
    ? 'text-amber-300 bg-amber-500/10 border border-amber-500/30'
    : 'text-gray-400 bg-gray-500/10 border border-gray-500/30';
  const tip =
    `INTRADAY SCORE SWING today (${swing.n} updates): ${swing.lo} → ${swing.hi} ` +
    `(${swing.spread} pts) on ${swing.price_move_pct}% price move.\n\n` +
    (fake
      ? 'FAKEOUT — the score swung but the price barely moved, so the swing is a ' +
        'scoring artifact (weekly partial→completed flip / regime / volume-amplifier ' +
        'reclassification), not a real change in the setup. Be cautious before ' +
        'deploying capital against a high reading here.'
      : 'Price also moved — this may be a genuine intraday repricing.') +
    (swing.symbol ? `\n\nDiagnose:  trader intraday-drill ${swing.symbol}` : '');
  return (
    <span
      title={tip}
      className={`inline-flex flex-shrink-0 items-center px-1 rounded text-[9px] font-mono font-semibold tracking-wider leading-none h-[14px] cursor-help ${cls}`}
    >
      {`↕${swing.spread}`}
    </span>
  );
};

/* WouldTradeBadge — surfaces `would_trade` from /api/stocks/all (gameplan P4
   tradability pill, known-issues.md OPEN WORK #0, reduced scope; shipped
   2026-07-13). The score badge shows a signal; this shows whether the live
   portfolio engine's dry-run would actually ACT on it today. Deliberately
   quiet like SkipBadge/CtBadge above: only the two "something unexpected is
   happening" reasons get a visible pill. `held` (already a position — the
   Star icon already covers this) and `puts_off` (a blanket, universal fact
   true for every put signal in the live profile, not a per-signal nuance)
   are intentionally unmapped so they render nothing, same mechanism as
   SkipBadge's retired reasons above. `would_open` (the boring, expected
   case — score qualifies and nothing is blocking it) is also unmapped for
   the same reason SkipBadge/CtBadge only flag exceptions, not defaults. */
const WOULD_TRADE_BADGE = {
  filtered: {
    label: 'NO-FILL',
    cls: 'text-trading-red-300 bg-trading-red-500/10 border border-trading-red-500/30',
  },
  halted: {
    label: 'HALTED',
    cls: 'text-amber-300 bg-amber-500/10 border border-amber-500/30',
  },
};

const WouldTradeBadge = ({ verdict }) => {
  if (!verdict || !verdict.reason) return null;
  const cfg = WOULD_TRADE_BADGE[verdict.reason];
  if (!cfg) return null;
  const tip =
    (verdict.eligible ? 'WOULD TRADE — ' : 'WOULD NOT TRADE TODAY — ') +
    (verdict.detail || verdict.reason) +
    '\n\nThe score shown here does not reflect this — the live portfolio engine\'s ' +
    'own dry-run (same view as the Allocator) does. Diagnose further on the Allocator page.';
  return (
    <span
      title={tip}
      className={`inline-flex flex-shrink-0 items-center px-1 rounded text-[9px] font-mono font-semibold tracking-wider leading-none h-[14px] cursor-help ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
};

const DESKTOP_COL = {
  ovr: { flex: '0 0 64px' },
  symbol: { flex: '0 0 112px' },
  price: { flex: '0 0 142px' },
  company: { flex: '1 1 0%', minWidth: 0 },
  score: { flex: '0 0 58px' },
  earnings: { flex: '0 0 66px' },
  valuation: { flex: '0 0 56px' },
  growth: { flex: '0 0 96px' },
  updated: { flex: '0 0 104px' },
};

/* Inline add-symbol button for empty-search state. */
const PullStockButton = ({ symbol, onSuccess }) => {
  const { pullStock } = useStock();
  const [isPulling, setIsPulling] = useState(false);
  const [pullResult, setPullResult] = useState(null);

  const handlePullStock = async () => {
    if (!symbol || symbol.trim().length === 0) return;
    setIsPulling(true);
    setPullResult(null);
    try {
      const result = await pullStock(symbol);
      setPullResult(result);
      if (result.success) {
        if (onSuccess) onSuccess();
        setTimeout(() => setPullResult(null), 3000);
      }
    } catch (error) {
      setPullResult({ success: false, error: error.message });
    } finally {
      setIsPulling(false);
    }
  };

  const isValidSymbol =
    symbol &&
    symbol.trim().length > 0 &&
    symbol.trim().length <= 10 &&
    /^[A-Za-z0-9]+$/.test(symbol.trim());

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        onClick={handlePullStock}
        disabled={!isValidSymbol || isPulling}
        className={`h-8 px-4 rounded-md text-[12px] font-medium transition-colors ${
          isValidSymbol && !isPulling
            ? 'text-trading-dark-950 bg-trading-green-300 hover:bg-trading-green-200'
            : 'text-gray-600 bg-white/[0.04] cursor-not-allowed'
        }`}
      >
        {isPulling ? (
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            <span>Processing</span>
          </div>
        ) : (
          <span>Add {symbol.trim().toUpperCase()}</span>
        )}
      </button>
      {pullResult && (
        <div className={`text-[11px] px-2.5 py-1.5 rounded-md ${
          pullResult.success
            ? 'bg-trading-green-500/10 text-trading-green-300 border border-trading-green-500/20'
            : 'bg-trading-red-500/10 text-trading-red-300 border border-trading-red-500/20'
        }`}>
          {pullResult.success ? pullResult.message : pullResult.error}
        </div>
      )}
    </div>
  );
};

/* Desktop virtualized row. Kept outside so react-window memoization holds. */
const DesktopRow = ({
  index, style, filteredStocks, formatPrice, formatPercentage,
  getGrowthColor, getGrowthRateColor, getEarningsGlowStyle,
  getUpdatedColor, isUpdateNeeded, handleUpdateStock, updatingStocks,
}) => {
  const stock = filteredStocks[index];
  if (!stock) return null;
  const score = stock.latest_score;
  const priceChange = stock.daily_percentage_change || 0;
  const isPositive = priceChange > 0;

  return (
    <div
      style={style}
      className={`
        relative flex items-center w-full overflow-hidden whitespace-nowrap text-[13px]
        border-b border-white/[0.04] group pr-3
        transition-colors
        ${stock.flagged
          ? 'bg-amber-500/[0.025] hover:bg-amber-500/[0.045]'
          : 'hover:bg-white/[0.02]'}
      `}
    >
      {stock.flagged && (
        <span
          className="absolute left-0 top-0 bottom-0 w-[2px] bg-amber-400/70 z-10"
          aria-hidden="true"
        />
      )}

      {/* OVR */}
      <div
        style={{ ...DESKTOP_COL.ovr, height: 'calc(100% + 1px)' }}
        className="min-w-0 flex items-center justify-center border-r border-white/[0.04]"
      >
        <ScoreBadge score={score?.OVR} />
      </div>

      {/* SYMBOL */}
      <div style={DESKTOP_COL.symbol} className="min-w-0 overflow-hidden pl-3">
        <Link
          to={`/stock/${stock.symbol}`}
          className="inline-flex w-full min-w-0 items-center gap-1.5 overflow-hidden hover:text-trading-blue-400 transition-colors"
        >
          <span className={`max-w-[4.5rem] flex-shrink-0 font-mono font-semibold tracking-tight ${
            stock.flagged ? 'text-amber-200 drop-shadow-[0_0_8px_rgba(251,191,36,0.7)]' : 'text-gray-100'
          } truncate`}>
            {stock.symbol}
          </span>
          {stock.held_position && <Star className="w-3.5 h-3.5 flex-shrink-0 text-trading-blue-400" fill="currentColor" />}
          <CtBadge tag={stock.ct_tag} />
          <SkipBadge skip={stock.cascade_skip} />
          <SwingBadge swing={stock.intraday_swing} />
          <WouldTradeBadge verdict={stock.would_trade} />
        </Link>
      </div>

      {/* PRICE */}
      <div style={DESKTOP_COL.price} className="min-w-0 overflow-hidden pr-3">
        <div className="flex items-center justify-end gap-2 font-mono tabular-nums">
          <span className="text-gray-500">{formatPrice(stock.current_price)}</span>
          <span className={`font-mono text-[12px] ${
            isPositive ? 'text-trading-green-400' : priceChange < 0 ? 'text-trading-red-400' : 'text-gray-500'
          }`}>
            {formatPercentage(priceChange)}
          </span>
        </div>
      </div>

      {/* COMPANY */}
      <div style={DESKTOP_COL.company} className="min-w-0 overflow-hidden hidden sm:block pl-3">
        <div className="truncate text-gray-500" title={stock.name}>{stock.name || '—'}</div>
      </div>

      {/* W */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden lg:flex items-center justify-center">
        <ScoreBadge plain score={score?.W} />
      </div>
      {/* TREND */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden lg:flex items-center justify-center">
        <ScoreBadge plain score={score?.TREND} />
      </div>
      {/* BB */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        <ScoreBadge plain score={score?.BB} />
      </div>
      {/* RSI */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        <ScoreBadge plain score={score?.RSI} />
      </div>
      {/* MACD */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        <ScoreBadge plain score={score?.MACD} />
      </div>
      {/* VOL */}
      <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        {score?.VOL != null ? <ScoreBadge plain score={score.VOL} /> : <span className="text-gray-700 text-xs">—</span>}
      </div>

      {/* EARNINGS */}
      <div style={DESKTOP_COL.earnings} className="min-w-0 text-center hidden md:flex items-center justify-center">
        <span className="text-[11px] font-mono tabular-nums">
          {stock.earnings_days ? (
            <span style={getEarningsGlowStyle(stock.next_earnings) || undefined}>
              {stock.earnings_days}
            </span>
          ) : <span className="text-gray-700">—</span>}
        </span>
      </div>

      {/* P/E */}
      <div style={DESKTOP_COL.valuation} className="min-w-0 text-center hidden lg:flex items-center justify-center">
        <span className="text-[11px] text-gray-400 font-mono tabular-nums">
          {stock.pe ? stock.pe.toFixed(1) : <span className="text-gray-700">—</span>}
        </span>
      </div>
      {/* FWD P/E */}
      <div style={DESKTOP_COL.valuation} className="min-w-0 text-center hidden lg:flex items-center justify-center">
        <span className="text-[11px] text-gray-400 font-mono tabular-nums">
          {stock.forward_pe ? stock.forward_pe.toFixed(1) : <span className="text-gray-700">—</span>}
        </span>
      </div>

      {/* GROWTH */}
      <div style={DESKTOP_COL.growth} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        <span className="text-[11px] truncate">
          {stock.growth_metric_raw != null ? (
            <span className={getGrowthColor(stock.growth_interpretation)}>
              {stock.growth_metric_raw.toFixed(0)}%{' '}
              <span className="text-gray-600">({stock.growth_interpretation})</span>
            </span>
          ) : <span className="text-gray-700">—</span>}
        </span>
      </div>
      {/* RATE */}
      <div style={DESKTOP_COL.growth} className="min-w-0 text-center hidden xl:flex items-center justify-center">
        <span className="text-[11px] truncate">
          {stock.growth_rate_metric_raw != null ? (
            <span className={getGrowthRateColor(stock.growth_rate_interpretation)}>
              {stock.growth_rate_metric_raw.toFixed(0)}%{' '}
              <span className="text-gray-600">({stock.growth_rate_interpretation})</span>
            </span>
          ) : <span className="text-gray-700">—</span>}
        </span>
      </div>

      {/* UPDATED */}
      <div style={DESKTOP_COL.updated} className="min-w-0 hidden lg:flex items-center justify-start relative">
        <div className="inline-flex items-center gap-1.5">
          <span className={`text-[11px] font-mono tabular-nums ${
            stock.updated_at ? getUpdatedColor(stock.updated_at) : 'text-gray-700'
          }`}>
            {stock.updated_at ? formatUpdatedTime(stock.updated_at) : '—'}
          </span>
          {stock.updated_at && isUpdateNeeded(stock.updated_at) && (
            <button
              onClick={() => handleUpdateStock(stock.symbol)}
              disabled={updatingStocks.includes(stock.symbol)}
              className="opacity-0 group-hover:opacity-100 transition-opacity
                         h-6 px-2 rounded text-[10px] font-medium tracking-wide
                         text-gray-100 bg-white/[0.06] hover:bg-white/[0.1] border border-white/[0.08]"
              title="Update stock data"
            >
              {updatingStocks.includes(stock.symbol) ? (
                <div className="w-2.5 h-2.5 border border-current border-t-transparent rounded-full animate-spin" />
              ) : 'Update'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

/* Mobile virtualized row. Module-scope so react-window's `rowComponent`
 * reference stays stable across renders — otherwise its internal
 * callback-ref `useState` setter loops via safelyDetachRef → dispatchSetState
 * ("Maximum update depth exceeded"). Same pattern as DesktopRow. */
const MobileRow = ({ index, style, filteredStocks }) => {
  const stock = filteredStocks[index];
  if (!stock) return null;
  const score = stock.latest_score;
  const rawChange = stock.daily_percentage_change;
  const priceChange = Number.isFinite(rawChange)
    ? rawChange
    : (typeof rawChange === 'string' ? parseFloat(rawChange) : 0);
  const safeChange = Number.isFinite(priceChange) ? priceChange : 0;
  const isPositive = safeChange > 0;
  const priceNum = typeof stock.current_price === 'number'
    ? stock.current_price
    : parseFloat(stock.current_price);

  const updatedColor = (() => {
    if (!stock.updated_at) return 'text-gray-700';
    const ageMin = (Date.now() - new Date(stock.updated_at).getTime()) / 60000;
    if (ageMin < 60) return 'text-trading-green-400';
    if (ageMin < 60 * 24) return 'text-gray-400';
    if (ageMin < 60 * 24 * 7) return 'text-gray-500';
    return 'text-trading-red-400';
  })();

  return (
    <div style={style}>
      <Link
        to={`/stock/${stock.symbol}`}
        className={`
          relative mx-1 mb-2 px-3 py-2
          bg-trading-dark-900 border border-white/[0.06] rounded-md
          flex items-center gap-2.5 overflow-hidden whitespace-nowrap
          ${stock.flagged ? 'border-l-2 border-l-amber-400/70 bg-amber-500/[0.025]' : ''}
        `}
      >
        <span className="flex-shrink-0">
          <ScoreBadge score={score?.OVR} />
        </span>

        <div className="flex items-center gap-1 flex-shrink-0 min-w-0 max-w-[116px] overflow-hidden">
          <span className={`max-w-[4.5rem] flex-shrink-0 font-mono font-semibold text-[15px] leading-tight ${
            stock.flagged ? 'text-amber-200 drop-shadow-[0_0_4px_rgba(251,191,36,0.5)]' : 'text-gray-100'
          } truncate`}>
            {stock.symbol}
          </span>
          {stock.held_position && (
            <Star className="w-3 h-3 text-trading-blue-400 flex-shrink-0" fill="currentColor" />
          )}
          <CtBadge tag={stock.ct_tag} />
          <SkipBadge skip={stock.cascade_skip} />
          <SwingBadge swing={stock.intraday_swing} />
          <WouldTradeBadge verdict={stock.would_trade} />
        </div>

        <div className="flex-1 min-w-0 text-[12px] text-gray-600 truncate leading-tight">
          {stock.name || '—'}
        </div>

        <div className="flex-shrink-0 flex w-[116px] flex-col items-end">
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[14px] text-gray-100 leading-tight">
              {Number.isFinite(priceNum) ? `$${priceNum.toFixed(2)}` : '$0.00'}
            </span>
            <span className={`font-mono text-[12px] leading-tight ${
              isPositive ? 'text-trading-green-400' : safeChange < 0 ? 'text-trading-red-400' : 'text-gray-500'
            }`}>
              {safeChange >= 0 ? '+' : ''}{safeChange.toFixed(2)}%
            </span>
          </div>
          <div className={`font-mono text-[11px] tabular-nums leading-tight mt-0.5 ${updatedColor}`}>
            {stock.updated_at ? formatUpdatedTime(stock.updated_at) : '—'}
          </div>
        </div>
      </Link>
    </div>
  );
};

const StockTable = ({ layoutVersion = 0 }) => {
  const { stocks, loading, error, filters, setFilters, getFilteredStocks, updateStock } = useStock();
  const [sortConfig, setSortConfig] = useState({ key: 'OVR', direction: 'desc' });

  React.useEffect(() => {
    setFilters({ sortBy: 'default', sortOrder: 'desc' });
  }, [setFilters]);

  const [updatingStocks, setUpdatingStocks] = useState([]);

  const handleSort = (key) => {
    let newDirection;
    if (key === 'OVR') {
      // OVR is governed by the Calls/Puts pills. Cycle: calls only → puts only → both.
      const callsActive = !!filters.callsOnly;
      const putsActive = !!filters.putsOnly;
      const bothActive = callsActive && putsActive;
      const onlyCalls = callsActive && !putsActive;
      if (bothActive) {
        setFilters({ sortBy: 'default', sortOrder: 'desc', callsOnly: true, putsOnly: false });
        newDirection = 'desc';
      } else if (onlyCalls) {
        setFilters({ sortBy: 'default', sortOrder: 'asc', callsOnly: false, putsOnly: true });
        newDirection = 'asc';
      } else {
        setFilters({ sortBy: 'default', sortOrder: 'desc', callsOnly: true, putsOnly: true });
        newDirection = null;
      }
    } else if (key === 'updated_at') {
      if (filters.sortBy !== 'updated_at') { newDirection = 'desc'; setFilters({ sortBy: 'updated_at', sortOrder: 'desc' }); }
      else if (filters.sortOrder === 'desc') { newDirection = 'asc'; setFilters({ sortBy: 'updated_at', sortOrder: 'asc' }); }
      else { newDirection = null; setFilters({ sortBy: 'default', sortOrder: 'desc' }); }
    } else {
      if (sortConfig.key === key && sortConfig.direction === 'desc') {
        newDirection = null; setFilters({ sortBy: 'default', sortOrder: 'desc' });
      } else {
        newDirection = 'desc'; setFilters({ sortBy: key, sortOrder: 'desc' });
      }
    }
    setSortConfig({ key: newDirection ? key : null, direction: newDirection });
  };

  const isColumnActive = (columnKey) =>
    filters.sortBy === columnKey || (columnKey === 'OVR' && filters.sortBy === 'default');

  const getHeaderLabelClass = (columnKey) =>
    isColumnActive(columnKey) ? 'text-gray-200' : 'text-gray-600';

  const SortIcon = ({ columnKey, small }) => {
    const sz = small ? 'w-3 h-3' : 'w-3.5 h-3.5';
    const active = isColumnActive(columnKey);
    if (!active) return <ChevronUp className={`${sz} text-gray-700`} />;
    if (columnKey === 'OVR') {
      const callsActive = !!filters.callsOnly;
      const putsActive = !!filters.putsOnly;
      if (callsActive && !putsActive) return <ChevronDown className={`${sz} text-gray-200`} />;
      if (!callsActive && putsActive) return <ChevronUp className={`${sz} text-gray-200`} />;
      return <TrendingUp className={`${sz} text-trading-green-300`} />;
    }
    return filters.sortOrder === 'asc'
      ? <ChevronUp className={`${sz} text-gray-200`} />
      : <ChevronDown className={`${sz} text-gray-200`} />;
  };

  const getGrowthColor = (interpretation) => {
    switch (interpretation) {
      case 'Explosive':  return 'text-trading-green-300 font-medium';
      case 'Strong':     return 'text-trading-green-400';
      case 'Moderate':   return 'text-trading-green-500';
      case 'Steady':     return 'text-amber-300';
      case 'Stagnant':   return 'text-amber-400';
      case 'Declining':  return 'text-trading-red-400 font-medium';
      default:           return 'text-gray-500';
    }
  };

  const getGrowthRateColor = (interpretation) => {
    switch (interpretation) {
      case 'Accelerating': return 'text-trading-green-300 font-medium';
      case 'Strong Accel': return 'text-trading-green-400';
      case 'Mild Accel':   return 'text-trading-green-500';
      case 'Stable':       return 'text-amber-300';
      case 'Decelerating': return 'text-amber-400';
      case 'Contracting':  return 'text-trading-red-400 font-medium';
      default:             return 'text-gray-500';
    }
  };

  const getEarningsGlowStyle = (earningsDaysRaw) => {
    if (earningsDaysRaw === null || earningsDaysRaw === undefined || earningsDaysRaw < 0) return null;
    const proximity = Math.max(0, Math.min(1, (60 - earningsDaysRaw) / 60));
    const isUrgent = earningsDaysRaw <= 5;
    const opacity = (0.4 + proximity * 0.55).toFixed(3);
    return {
      color: isUrgent
        ? `rgba(201,132,132,${opacity})`
        : `rgba(228,228,231,${opacity})`,
    };
  };

  const getUpdatedColor = (updatedAt) => {
    if (!updatedAt) return 'text-gray-700';
    const diffHours = (new Date() - new Date(updatedAt)) / 3600000;
    if (diffHours > 24) return 'text-trading-red-400';
    if (diffHours > 3)  return 'text-trading-red-400';
    if (diffHours > 1)  return 'text-amber-400';
    return 'text-trading-green-400';
  };

  const isUpdateNeeded = (updatedAt) => {
    if (!updatedAt) return false;
    return (new Date() - new Date(updatedAt)) / 3600000 > 3;
  };

  const handleUpdateStock = async (symbol) => {
    if (updatingStocks.includes(symbol)) return;
    setUpdatingStocks(prev => [...prev, symbol]);
    try {
      await updateStock(symbol);
    } catch (e) {
      console.error(`Error updating stock ${symbol}:`, e);
    } finally {
      setUpdatingStocks(prev => prev.filter(s => s !== symbol));
    }
  };

  const formatPercentage = (value) => {
    const num = typeof value === 'string' ? parseFloat(value) : value;
    if (!Number.isFinite(num)) return '0.00%';
    const sign = num > 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  };

  const formatPrice = (price) => (price ? `$${price.toFixed(2)}` : '$0.00');

  // Sorting/filtering is owned by StockContext.getFilteredStocks().
  const filteredStocks = React.useMemo(() => getFilteredStocks(), [getFilteredStocks]);

  const ROW_HEIGHT = 44;
  const MOBILE_ROW_HEIGHT = 62;

  const containerRef = useRef(null);
  const [containerHeight, setContainerHeight] = useState(600);
  const [scrollbarWidth, setScrollbarWidth] = useState(0);
  const desktopListRef = useRef(null);
  const mobileListRef = useRef(null);

  React.useEffect(() => {
    const outer = document.createElement('div');
    outer.style.cssText = 'visibility:hidden;overflow:scroll;position:absolute;top:-9999px;width:100px;height:100px;';
    document.body.appendChild(outer);
    setScrollbarWidth(outer.offsetWidth - outer.clientWidth);
    document.body.removeChild(outer);
  }, []);

  // Mobile PWA: 100dvh shifts when the iOS address bar collapses and after
  // safe-area-inset resolves, without firing window 'resize'. ResizeObserver
  // catches those layout settlements; visibilitychange/pageshow re-measures
  // when the user returns to the tab. The prev===next guard breaks the
  // RO -> setContainerHeight -> List resize -> RO feedback loop that
  // previously hit "Maximum update depth exceeded".
  React.useEffect(() => {
    let rafId = null;
    const readPx = (value) => {
      const n = Number.parseFloat(value || '0');
      return Number.isFinite(n) ? n : 0;
    };
    const measure = () => {
      const node = containerRef.current;
      if (!node) return;
      const directParent = node.parentElement;
      const scrollParent = directParent?.parentElement;
      let next;
      if (window.innerWidth < 640) {
        if (!directParent) return;
        const parentRect = directParent.getBoundingClientRect();
        next = Math.max(300, Math.floor(parentRect.height || directParent.offsetHeight || 0));
      } else {
        const rect = node.getBoundingClientRect();
        const boundary = scrollParent?.getBoundingClientRect?.().bottom || window.innerHeight;
        const parentStyle = directParent ? window.getComputedStyle(directParent) : null;
        const parentBottomPad = parentStyle ? readPx(parentStyle.paddingBottom) : 0;
        next = Math.max(300, Math.floor(boundary - rect.top - parentBottomPad));
      }
      setContainerHeight(prev => (prev === next ? prev : next));
    };
    const scheduleMeasure = () => {
      if (rafId != null) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        rafId = null;
        measure();
      });
    };
    measure();

    // Observe BOTH the immediate parent and the scroll container one level up.
    // On desktop the immediate parent (.px-4 py-3 padding wrapper) sizes to the
    // List's containerHeight, so it never resizes from external layout changes
    // (e.g. earnings panel expand/collapse). The scroll container above it
    // (.flex-1 .overflow-auto) is the one that actually shrinks/grows when
    // sibling sections change height — observe that too so measure() re-fires.
    const directParent = containerRef.current?.parentElement;
    const scrollParent = directParent?.parentElement;
    let ro;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(scheduleMeasure);
      if (directParent) ro.observe(directParent);
      if (scrollParent && scrollParent !== directParent) ro.observe(scrollParent);
    }

    const onVisible = () => { if (!document.hidden) scheduleMeasure(); };
    window.addEventListener('resize', scheduleMeasure);
    window.addEventListener('orientationchange', scheduleMeasure);
    window.addEventListener('pageshow', scheduleMeasure);
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      if (rafId != null) cancelAnimationFrame(rafId);
      ro?.disconnect();
      window.removeEventListener('resize', scheduleMeasure);
      window.removeEventListener('orientationchange', scheduleMeasure);
      window.removeEventListener('pageshow', scheduleMeasure);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [layoutVersion]);

  React.useEffect(() => {
    desktopListRef.current?.scrollToIndex?.(0);
    mobileListRef.current?.scrollToIndex?.(0);
  }, [filters.sortBy, filters.sortOrder, filters.search, filters.flaggedOnly, filters.callsOnly, filters.putsOnly]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-6 h-6 border border-gray-700 border-t-trading-green-300 rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-trading-red-400 text-sm">Error loading stocks: {error}</p>
      </div>
    );
  }

  const headerBtn =
    'flex items-center justify-center gap-1 hover:text-gray-200 transition-colors ' +
    'text-[10px] font-medium tracking-[0.14em] uppercase';

  return (
    <div ref={containerRef}>
      {/* Mobile */}
      <div className="block sm:hidden">
        {filteredStocks.length > 0 ? (
          <List
            listRef={mobileListRef}
            rowCount={filteredStocks.length}
            rowHeight={MOBILE_ROW_HEIGHT}
            rowComponent={MobileRow}
            rowProps={{ filteredStocks }}
            overscanCount={5}
            style={{ height: containerHeight, width: '100%' }}
          />
        ) : (
          <div className="text-center py-10 text-gray-500">
            <div className="mb-4 text-sm">No stocks match your criteria.</div>
            {filters.search && (
              <div className="flex flex-col items-center gap-3">
                <div className="text-[12px] text-gray-600">
                  Add "{filters.search.toUpperCase()}" to your dashboard?
                </div>
                <PullStockButton
                  symbol={filters.search}
                  onSuccess={() => setFilters({ search: '' })}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Desktop */}
      <div className="hidden sm:block">
        <div
          style={{ paddingRight: `calc(0.75rem + ${scrollbarWidth}px)` }}
          className="flex items-center w-full overflow-hidden whitespace-nowrap border-b border-white/[0.08] py-2 text-gray-600"
        >
          <div style={DESKTOP_COL.ovr} className="min-w-0 flex items-center justify-center border-r border-white/[0.04]">
            <button onClick={() => handleSort('OVR')} className={`${headerBtn} w-full`} title="Overall score 0-100. Cycles the Calls/Puts sort mode: calls first, puts first, both score-desc.">
              <span className={getHeaderLabelClass('OVR')}>OVR</span><SortIcon columnKey="OVR" />
            </button>
          </div>
          <div style={DESKTOP_COL.symbol} className="min-w-0 overflow-hidden pl-3">
            <button onClick={() => handleSort('symbol')} className={`${headerBtn} !justify-start`} title="Stock ticker symbol">
              <span className={getHeaderLabelClass('symbol')}>SYM</span><SortIcon columnKey="symbol" />
            </button>
          </div>
          <div style={DESKTOP_COL.price} className="min-w-0 overflow-hidden pr-3">
            <button onClick={() => handleSort('current_price')} className={`${headerBtn} w-full`} title="Current price + daily change">
              <span className={getHeaderLabelClass('current_price')}>Price</span><SortIcon columnKey="current_price" />
            </button>
          </div>
          <div style={DESKTOP_COL.company} className="min-w-0 overflow-hidden hidden sm:block pl-3 text-[10px] font-medium tracking-[0.14em] uppercase text-gray-700">Company</div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden lg:flex items-center justify-center">
            <button onClick={() => handleSort('W')} className={`${headerBtn} w-full`} title="Weekly composite">
              <span className={getHeaderLabelClass('W')}>W</span><SortIcon columnKey="W" small />
            </button>
          </div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden lg:flex items-center justify-center">
            <button onClick={() => handleSort('TREND')} className={`${headerBtn} w-full`} title="Trend component">
              <span className={getHeaderLabelClass('TREND')}>Trend</span><SortIcon columnKey="TREND" small />
            </button>
          </div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('BB')} className={`${headerBtn} w-full`} title="Bollinger band position">
              <span className={getHeaderLabelClass('BB')}>BB</span><SortIcon columnKey="BB" small />
            </button>
          </div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('RSI')} className={`${headerBtn} w-full`} title="RSI">
              <span className={getHeaderLabelClass('RSI')}>RSI</span><SortIcon columnKey="RSI" small />
            </button>
          </div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('MACD')} className={`${headerBtn} w-full`} title="MACD">
              <span className={getHeaderLabelClass('MACD')}>MACD</span><SortIcon columnKey="MACD" small />
            </button>
          </div>
          <div style={DESKTOP_COL.score} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('VOL')} className={`${headerBtn} w-full`} title="Volume signal">
              <span className={getHeaderLabelClass('VOL')}>Vol</span><SortIcon columnKey="VOL" small />
            </button>
          </div>
          <div style={DESKTOP_COL.earnings} className="min-w-0 text-center hidden md:flex items-center justify-center">
            <button onClick={() => handleSort('earnings')} className={`${headerBtn} w-full`} title="Days to next earnings">
              <span className={getHeaderLabelClass('earnings')}>Earn</span><SortIcon columnKey="earnings" />
            </button>
          </div>
          <div style={DESKTOP_COL.valuation} className="min-w-0 text-center hidden lg:flex items-center justify-center">
            <button onClick={() => handleSort('pe')} className={`${headerBtn} w-full`} title="Price-to-earnings">
              <span className={getHeaderLabelClass('pe')}>P/E</span><SortIcon columnKey="pe" />
            </button>
          </div>
          <div style={DESKTOP_COL.valuation} className="min-w-0 text-center hidden lg:flex items-center justify-center">
            <button onClick={() => handleSort('forward_pe')} className={`${headerBtn} w-full`} title="Forward P/E">
              <span className={getHeaderLabelClass('forward_pe')}>FWD</span><SortIcon columnKey="forward_pe" />
            </button>
          </div>
          <div style={DESKTOP_COL.growth} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('growth')} className={`${headerBtn} w-full`} title="EPS growth">
              <span className={getHeaderLabelClass('growth')}>Growth</span><SortIcon columnKey="growth" />
            </button>
          </div>
          <div style={DESKTOP_COL.growth} className="min-w-0 text-center hidden xl:flex items-center justify-center">
            <button onClick={() => handleSort('growth_rate')} className={`${headerBtn} w-full`} title="Growth rate">
              <span className={getHeaderLabelClass('growth_rate')}>Rate</span><SortIcon columnKey="growth_rate" />
            </button>
          </div>
          <div style={DESKTOP_COL.updated} className="min-w-0 hidden lg:flex items-center justify-start">
            <button onClick={() => handleSort('updated_at')} className={`${headerBtn} w-full !justify-start`} title="Last updated">
              <span className={getHeaderLabelClass('updated_at')}>Updated</span><SortIcon columnKey="updated_at" />
            </button>
          </div>
        </div>

        {filteredStocks.length > 0 ? (
          <List
            listRef={desktopListRef}
            rowCount={filteredStocks.length}
            rowHeight={ROW_HEIGHT}
            rowComponent={DesktopRow}
            overscanCount={8}
            rowProps={{
              filteredStocks, filters, formatPrice, formatPercentage,
              getGrowthColor, getGrowthRateColor, getEarningsGlowStyle,
              getUpdatedColor, isUpdateNeeded, handleUpdateStock, updatingStocks,
            }}
            style={{ height: containerHeight - 36, width: '100%', scrollbarGutter: 'stable' }}
          />
        ) : (
          <div className="text-center py-10 text-gray-500">
            <div className="mb-4 text-sm">No stocks match your criteria.</div>
            {filters.search && (
              <div className="flex flex-col items-center gap-3">
                <div className="text-[12px] text-gray-600">
                  Add "{filters.search.toUpperCase()}" to your dashboard?
                </div>
                <PullStockButton
                  symbol={filters.search}
                  onSuccess={() => setFilters({ search: '' })}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default StockTable;
