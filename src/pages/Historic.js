import React, { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Clock, ArrowUp, ArrowDown, RefreshCw, TrendingUp } from 'lucide-react';
import { useStock } from '../context/StockContext';
import ScoreBadge from '../components/ScoreBadge';
import ScoreVersionSelector from '../components/ScoreVersionSelector';
import { shuffledPunchlineLoadingPhases, PUNCHLINE_LOADING_PHASES } from '../utils/punchlineLoadingText';

function winRateColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 75) return 'text-trading-green-300';
  if (v >= 65) return 'text-trading-green-400';
  if (v >= 55) return 'text-amber-300';
  return 'text-gray-500';
}

const MAX_DAYS = 365;
const CALL_SCORE_MIN = 75;
const CALL_SCORE_MAX = 100;
const PUT_SCORE_MIN = 0;
const PUT_SCORE_MAX = 25;
// Snap points — equal visual spacing between each stop
const SNAP_DAYS = [15, 30, 60, 90, 365];
const SNAP_THRESHOLD = 0.18; // fraction of one segment; snap when this close to an integer stop

// Map continuous slider position [0, 4] → calendar days (linear interpolation between stops)
function sliderToDays(pos) {
  const lo = Math.min(Math.floor(pos), SNAP_DAYS.length - 2);
  const frac = pos - lo;
  return Math.round(SNAP_DAYS[lo] * (1 - frac) + SNAP_DAYS[lo + 1] * frac);
}

// Pull position toward nearest integer stop if within threshold
function applySnap(pos) {
  const clamped = Math.max(0, Math.min(SNAP_DAYS.length - 1, pos));
  const nearest = Math.round(clamped);
  return Math.abs(clamped - nearest) <= SNAP_THRESHOLD ? nearest : clamped;
}
const FWD_PERIODS = [
  { label: '1d',  fwd: 'fwd_1d',  peak: 'fwd_peak_1d',  days: 'fwd_peak_days_1d',  delta: null,                 winReach: 'reach_1d',  winStop: 'win_1d',  periodDays: 1  },
  { label: '7d',  fwd: 'fwd_7d',  peak: 'fwd_peak_7d',  days: 'fwd_peak_days_7d',  delta: 'fwd_peak_delta_7d',  winReach: 'reach_7d',  winStop: 'win_7d',  periodDays: 7  },
  { label: '15d', fwd: 'fwd_15d', peak: 'fwd_peak_15d', days: 'fwd_peak_days_15d', delta: 'fwd_peak_delta_15d', winReach: 'reach_15d', winStop: 'win_15d', periodDays: 15 },
  { label: '30d', fwd: 'fwd_30d', peak: 'fwd_peak_30d', days: 'fwd_peak_days_30d', delta: 'fwd_peak_delta_30d', winReach: 'reach_30d', winStop: 'win_30d', periodDays: 30 },
  { label: '60d', fwd: 'fwd_60d', peak: 'fwd_peak_60d', days: 'fwd_peak_days_60d', delta: 'fwd_peak_delta_60d', winReach: 'reach_60d', winStop: 'win_60d', periodDays: 60 },
  { label: '90d', fwd: 'fwd_90d', peak: 'fwd_peak_90d', days: 'fwd_peak_days_90d', delta: 'fwd_peak_delta_90d', winReach: 'reach_90d', winStop: 'win_90d', periodDays: 90 },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function fwdBadge(pct, isHigh) {
  if (pct == null) return <span className="text-gray-700 tabular-nums">—</span>;
  const good = isHigh ? pct >= 0 : pct <= 0;
  const cls  = good ? 'text-trading-green-400' : 'text-trading-red-400';
  const sign = pct >= 0 ? '+' : '';
  return <span className={`${cls} tabular-nums`}>{sign}{pct.toFixed(1)}%</span>;
}

// win: 1=won, 0=lost, null=pending or no-data; windowPassed: true when calendar days_ago >= periodDays
function peakBadge(pct, isHigh, period, daysAfter, { glow = false, stale = false, win = undefined, windowPassed = false } = {}) {
  if (pct == null) {
    if (win === undefined || (!windowPassed)) {
      return <span className="text-gray-700 tabular-nums">—</span>;
    }
    return <span className="text-gray-700 tabular-nums" title="Insufficient vol data">—</span>;
  }
  const display = isHigh ? pct : -pct;
  const sign    = display >= 0 ? '+' : '';

  let cls;
  if (win === 1) {
    cls = stale ? 'text-trading-green-500' : 'text-trading-green-300';
  } else if (win === 0) {
    cls = stale ? 'text-trading-red-600' : 'text-trading-red-400';
  } else if (win === null && windowPassed) {
    // Window passed but null = insufficient vol data
    cls = 'text-gray-600';
  } else {
    // Pending — window still open
    cls = stale ? 'text-gray-600' : 'text-gray-500';
  }

  return (
    <span className={`${cls} tabular-nums${glow && win === 1 ? ' drop-shadow-[0_0_6px_rgba(127,191,160,0.7)] font-semibold' : ''}`}>
      {sign}{display.toFixed(1)}%
      {daysAfter != null && (
        <span className="text-gray-600 font-normal"> in {daysAfter}d</span>
      )}
    </span>
  );
}

function deltaBadge(delta, isBest) {
  if (delta == null) return <span className="text-gray-700 tabular-nums text-[10px]">—</span>;
  const isZero = delta <= 0;
  const sign   = delta >= 0 ? '+' : '';
  const cls    = isZero
    ? 'text-gray-700'
    : isBest
      ? 'text-[#7FBFA0] drop-shadow-[0_0_5px_rgba(127,191,160,0.5)]'
      : 'text-gray-500';
  return (
    <span className={`tabular-nums text-[10px] ${cls}`}>
      {sign}{delta.toFixed(1)}%
    </span>
  );
}

function recencyOpacity(daysAgo) {
  if (daysAgo <= 7) return 1.0;
  if (daysAgo >= MAX_DAYS) return 0.35;
  return 1.0 - ((daysAgo - 7) / (MAX_DAYS - 7)) * 0.65;
}

// "2026-04-07" → "Apr 7, 2026"  (T00:00:00 avoids UTC-shift)
function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const ScoreCutoffSlider = ({ side, value, onChange, active = true }) => {
  const isCall = side === 'call';
  const accentColor = isCall ? '#8FB286' : '#C98484';
  const textColor = isCall ? 'text-trading-green-400' : 'text-trading-red-400';
  const label = isCall ? 'Calls' : 'Puts';
  const operator = isCall ? '>=' : '<=';
  const inputValue = isCall ? value : PUT_SCORE_MAX - value;
  const minLabel = isCall ? CALL_SCORE_MIN : PUT_SCORE_MAX;
  const maxLabel = isCall ? CALL_SCORE_MAX : PUT_SCORE_MIN;

  const handleChange = (raw) => {
    const next = Number(raw);
    if (!Number.isFinite(next)) return;
    onChange(isCall ? next : PUT_SCORE_MAX - next);
  };

  const setEndpoint = (endpointValue) => {
    onChange(endpointValue);
  };

  return (
    <div
      aria-hidden={!active}
      className={`flex items-center gap-2 flex-shrink-0 transition-opacity ${active ? 'opacity-100' : 'invisible opacity-0'}`}
    >
      <span className={`text-[10px] font-medium uppercase tracking-[0.14em] whitespace-nowrap ${textColor}`}>
        {label} <span className="font-mono tabular-nums">{operator}{value}</span>
      </span>
      <div className="flex flex-col" style={{ width: 116 }}>
        <input
          type="range"
          min={0}
          max={isCall ? CALL_SCORE_MAX - CALL_SCORE_MIN : PUT_SCORE_MAX - PUT_SCORE_MIN}
          step={1}
          value={isCall ? inputValue - CALL_SCORE_MIN : inputValue}
          onChange={e => handleChange(isCall ? Number(e.target.value) + CALL_SCORE_MIN : Number(e.target.value))}
          aria-label={`${label} score cutoff`}
          disabled={!active}
          tabIndex={active ? 0 : -1}
          className="w-full cursor-pointer"
          style={{ accentColor }}
        />
        <div className="flex justify-between mt-0.5">
          <button
            type="button"
            onClick={() => setEndpoint(minLabel)}
            disabled={!active}
            tabIndex={active ? 0 : -1}
            className="text-[9px] cursor-pointer select-none tabular-nums leading-none text-gray-600 hover:text-gray-400"
          >
            {minLabel}
          </button>
          <button
            type="button"
            onClick={() => setEndpoint(maxLabel)}
            disabled={!active}
            tabIndex={active ? 0 : -1}
            className="text-[9px] cursor-pointer select-none tabular-nums leading-none text-gray-600 hover:text-gray-400"
          >
            {maxLabel}
          </button>
        </div>
      </div>
    </div>
  );
};

const ScoreCutoffColumn = ({
  showCalls,
  showPuts,
  callScoreCutoff,
  putScoreCutoff,
  onCallChange,
  onPutChange,
}) => (
  <div className="flex flex-col justify-center gap-1.5 flex-shrink-0" style={{ width: 190, minHeight: 58 }}>
    <ScoreCutoffSlider
      side="call"
      value={callScoreCutoff}
      onChange={onCallChange}
      active={showCalls}
    />
    <ScoreCutoffSlider
      side="put"
      value={putScoreCutoff}
      onChange={onPutChange}
      active={showPuts}
    />
  </div>
);

function usePunchlineLoadingPhase(active) {
  const [phase, setPhase] = useState(PUNCHLINE_LOADING_PHASES[0]);

  useEffect(() => {
    if (!active) return undefined;
    const shuffled = shuffledPunchlineLoadingPhases();
    let index = 0;
    setPhase(shuffled[0]);
    const timer = setInterval(() => {
      index = (index + 1) % shuffled.length;
      setPhase(shuffled[index]);
    }, 3000);
    return () => clearInterval(timer);
  }, [active]);

  return phase;
}

// ── Component ─────────────────────────────────────────────────────────────────

const Historic = () => {
  const {
    stocks,
    historicPeaks,
    historicPeaksLoading,
    fetchHistoricPeaks,
    availableScoreVersions,
    legacyScoreVersions,
    scoreVersion,
    activeScoreVersionId,
    selectedScoreVersionId,
    setScoreVersion,
    scoreDate,
  } = useStock();

  const [sortMode, setSortMode] = useState(null);
  const [showCalls, setShowCalls] = useState(true);
  const [showPuts, setShowPuts] = useState(false);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [reentryOnly, setReentryOnly] = useState(false);
  const [sliderPos, setSliderPos] = useState(1); // continuous [0, 4]; 1 = 30d default
  const [callScoreCutoff, setCallScoreCutoff] = useState(CALL_SCORE_MIN);
  const [putScoreCutoff, setPutScoreCutoff] = useState(PUT_SCORE_MAX);
  const maxDaysAgo = sliderToDays(sliderPos);
  const loadingPhase = usePunchlineLoadingPhase(historicPeaksLoading);

  useEffect(() => { fetchHistoricPeaks(); }, [fetchHistoricPeaks]);

  const currentBySymbol = useMemo(() => {
    const map = {};
    for (const s of stocks) map[s.symbol] = s;
    return map;
  }, [stocks]);

  const rows = useMemo(() => {
    let list = historicPeaks.filter(p => p.days_ago <= maxDaysAgo);
    if (!showCalls && !showPuts) list = [];
    else {
      list = list.filter(p => {
        if (p.peak_type === 'HIGH') return showCalls && p.peak_score >= callScoreCutoff;
        if (p.peak_type === 'LOW') return showPuts && p.peak_score <= putScoreCutoff;
        return false;
      });
    }
    if (flaggedOnly) list = list.filter(p => p.flagged);
    // Tag re-entry / roll-up opportunities.
    // Roll-up (both calls + puts): direction confirmed at Xd → close ITM, re-open fresh ATM.
    //   Fresh ATM entry WR: 63% (8d), 63% (15d), 64% (30d), 64% (30d from d60) — calls 70+, 5y, N=10k+.
    //   Original position continues in 90-92% of cases (P(win_next|win_prior), same dataset).
    //   Puts nearly identical fresh-entry WR (63-65%, clears put BE 43.5% by 19-21pp).
    // Re-entry (calls only — puts are 97-98% stopped, bridge WR is below put BE of 43.5%):
    //   Xd expired (not stopped) → still in trade → fresh ATM entry from day-X close.
    list = list.map(p => {
      const isCall = p.peak_type === 'HIGH';
      const isPut  = p.peak_type === 'LOW';

      // Bridge runway: how much of the K*σ*√(W/30) target distance remains from current price.
      // Returns null (vol unavailable), { done: true } (target already hit, suppress pill),
      // or { done: false, pctLeft: 0-100 } (pctLeft = % of runway still to go).
      const vol = p.vol_pct; // daily realized vol in %, e.g. 1.5 for 1.5%
      const K = isCall ? 2.0 : 1.0; // call K=2.0σ, put K=1.0σ (from barrier_wins params)
      function calcRunway(fwdPct, bridgeDays) {
        if (!vol || fwdPct == null || !p.price_at_peak || !p.current_price) return null;
        const entryPrice = p.price_at_peak * (1 + fwdPct / 100);
        if (entryPrice <= 0) return null;
        const targetPct = K * vol * Math.sqrt(bridgeDays / 30); // % move needed
        const progress = isCall
          ? (p.current_price / entryPrice - 1) * 100
          : (entryPrice / p.current_price - 1) * 100;
        const runway = targetPct - progress;
        if (runway <= 0) return { done: true };
        return { done: false, pctLeft: Math.min(100, Math.round((runway / targetPct) * 100)) };
      }

      const runway7  = calcRunway(p.fwd_7d,  8);
      const runway15 = calcRunway(p.fwd_15d, 15);
      const runway30 = calcRunway(p.fwd_30d, 30);
      const runway60 = calcRunway(p.fwd_60d, 30);

      // ── Roll-up: Xd won, next window still open ──────────────────────────
      const rollup7  = (isCall || isPut) && p.win_7d  === 1 && p.win_15d === null && !(runway7?.done);
      const rollup15 = (isCall || isPut) && p.win_15d === 1 && p.win_30d === null && !(runway15?.done);
      const rollup30 = (isCall || isPut) && p.win_30d === 1 && p.win_60d === null && !(runway30?.done);
      const rollup60 = (isCall || isPut) && p.win_60d === 1 && p.win_90d === null && !(runway60?.done);
      const rollup   = rollup7 || rollup15 || rollup30 || rollup60;

      // ── Re-entry: Xd failed (stopped/expired), next window still open — calls only ─
      // days_ago bounds ensure the tag only fires when the active period cell has a matching pill.
      // Without bounds, stale price data (win_Xd=null) causes the filter to show signals with no visible pill.
      const reentry7d  = isCall && p.win_7d  === 0 && p.win_15d === null && p.days_ago >= 7  && p.days_ago < 15 &&
                         (p.price_change_pct == null || p.price_change_pct > -8) && !(runway7?.done);
      const reentry15d = isCall && p.win_15d === 0 && p.win_30d === null && p.days_ago >= 15 && p.days_ago < 30 &&
                         (p.price_change_pct == null || p.price_change_pct > -10) && !(runway15?.done);
      const reentry30d = isCall && p.win_30d === 0 && p.win_60d === null && p.days_ago >= 30 && p.days_ago < 60 &&
                         (p.price_change_pct == null || p.price_change_pct > -12) && !(runway30?.done);
      const reentry60d = isCall && p.win_60d === 0 && p.win_90d === null && p.days_ago >= 60 && p.days_ago < 90 &&
                         (p.price_change_pct == null || p.price_change_pct > -14) && !(runway60?.done);

      return {
        ...p,
        rollup7, rollup15, rollup30, rollup60, rollup,
        reentry7d, reentry15d, reentry30d, reentry60d,
        isReentry: rollup || reentry7d || reentry15d || reentry30d || reentry60d,
        runway7, runway15, runway30, runway60,
      };
    });
    if (reentryOnly) list = list.filter(p => p.isReentry);
    if (sortMode === 'days') {
      list = [...list].sort((a, b) => a.days_ago - b.days_ago);
    } else {
      list = [...list].sort((a, b) => Math.abs(b.peak_score - 50) - Math.abs(a.peak_score - 50));
    }
    return list;
  }, [historicPeaks, sortMode, showCalls, showPuts, flaggedOnly, reentryOnly, maxDaysAgo, callScoreCutoff, putScoreCutoff]);

  // Win rates per period computed from visible rows. A row is complete once its
  // outcome is known: wins can resolve before the full calendar window closes.
  const winRates = useMemo(() => {
    const rates = {};
    for (const p of FWD_PERIODS) {
      const winField = p.winReach;
      const resolved = rows.filter(r => r[winField] != null);
      const openOnly = maxDaysAgo < p.periodDays;
      if (resolved.length === 0) { rates[p.label] = { pct: null, wins: 0, n: 0, total: rows.length, openOnly }; continue; }
      const wins = resolved.filter(r => r[winField] === 1).length;
      rates[p.label] = { pct: Math.round((wins / resolved.length) * 100), wins, n: resolved.length, total: rows.length, openOnly };
    }
    return rates;
  }, [rows, maxDaysAgo]);

  const viewingHistorical = activeScoreVersionId != null && scoreVersion?.id != null && scoreVersion.id !== activeScoreVersionId;

  if (historicPeaksLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
        <Clock className="w-5 h-5 text-trading-green-400 animate-spin" />
        <div>
          <p className="text-[13px] text-gray-200 font-medium">{loadingPhase}</p>
          <p className="text-[11px] text-gray-600 mt-1">Loading historic score signals…</p>
        </div>
      </div>
    );
  }

  const TH   = 'px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 whitespace-nowrap';
  const TH_R = TH + ' text-right';

  const PillBtn = ({ active, onClick, dotColor, children }) => (
    <button
      onClick={onClick}
      className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium border transition-colors ${
        active
          ? 'border-white/[0.12] text-gray-200'
          : 'border-white/[0.04] text-gray-600 hover:text-gray-400'
      }`}
    >
      <span className={`w-1 h-1 rounded-full ${active ? dotColor : 'bg-gray-700'}`} />
      {children}
      {active && <span className={`absolute left-0 right-0 bottom-0 h-[2px] ${dotColor}/80`} aria-hidden />}
    </button>
  );

  return (
    <div className="h-full flex flex-col overflow-hidden bg-trading-dark-950">

      <div className="flex-1 overflow-auto">
        <div className="mx-auto w-fit">

        <div className="sticky top-0 z-10 bg-trading-dark-950 border-b border-white/[0.06] py-3">
        <div className="flex items-center gap-5">

          <div className="flex items-center gap-3 flex-shrink-0">
            <div>
              <h1 className="text-[11px] font-medium uppercase tracking-[0.18em] text-gray-500">Historic Scores</h1>
              <p className="text-[16px] font-semibold text-white tracking-tight leading-tight mt-0.5">Peak signal events</p>
              <p className="text-[11px] text-gray-600 mt-0.5">Last {MAX_DAYS} days</p>
            </div>
            <span className="px-2 py-0.5 rounded-sm border border-white/[0.08] text-[11px] text-gray-400 tabular-nums text-center flex-shrink-0 font-mono" style={{ minWidth: '5.5rem' }}>
              {rows.length} signals
            </span>
            <ScoreVersionSelector
              versions={availableScoreVersions}
              legacyVersions={legacyScoreVersions}
              currentVersion={scoreVersion}
              activeVersionId={activeScoreVersionId}
              scoreDate={scoreDate}
              selectedVersionId={selectedScoreVersionId}
              onSelect={setScoreVersion}
              align="left"
              compact
              ariaLabel="Select historic score version"
              titlePrefix="Historic scores"
            />
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="flex items-center gap-2 flex-shrink-0">
              <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 whitespace-nowrap">Within</span>
              <div className="flex flex-col" style={{ width: 200 }}>
                <input
                  type="range" min={0} max={SNAP_DAYS.length - 1} step={0.01} value={sliderPos}
                  onChange={e => setSliderPos(applySnap(Number(e.target.value)))}
                  className="w-full accent-gray-500 cursor-pointer"
                />
                <div className="flex justify-between mt-0.5">
                  {SNAP_DAYS.map((d, i) => (
                    <span
                      key={d}
                      onClick={() => setSliderPos(i)}
                      className={`text-[9px] cursor-pointer select-none tabular-nums leading-none ${
                        Math.abs(sliderPos - i) < 0.15
                          ? 'text-gray-200 font-medium'
                          : 'text-gray-600 hover:text-gray-400'
                      }`}
                    >
                      {d === 365 ? '1yr' : `${d}d`}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <PillBtn active={showCalls} onClick={() => setShowCalls(v => !v)} dotColor="bg-trading-green-400">
                Calls
              </PillBtn>
              <PillBtn active={showPuts} onClick={() => setShowPuts(v => !v)} dotColor="bg-trading-red-400">
                Puts
              </PillBtn>
              <PillBtn active={flaggedOnly} onClick={() => setFlaggedOnly(v => !v)} dotColor="bg-amber-400">
                Flagged
              </PillBtn>
              <PillBtn active={reentryOnly} onClick={() => setReentryOnly(v => !v)} dotColor="bg-sky-400">
                Re-entry / Roll-up
              </PillBtn>
            </div>
            <ScoreCutoffColumn
              showCalls={showCalls}
              showPuts={showPuts}
              callScoreCutoff={callScoreCutoff}
              putScoreCutoff={putScoreCutoff}
              onCallChange={setCallScoreCutoff}
              onPutChange={setPutScoreCutoff}
            />
          </div>

        </div>
        </div>

        {/* Table */}
        <div className="py-4">
        {rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-gray-500">
            <Clock className="w-8 h-8 mb-3 opacity-30" strokeWidth={1.5} />
            <p className="text-sm">
              {viewingHistorical ? 'No cached historic scores for this version/window.' : 'No extreme scores in the selected window.'}
            </p>
            <p className="text-xs mt-1">
              Run <code className="text-gray-400 bg-white/[0.04] px-1 rounded">trader historic-update</code> to populate.
            </p>
          </div>
        ) : (
          <table className="text-sm border-collapse table-fixed">
            <thead className="bg-trading-dark-950 border-b border-white/[0.08]">
              <tr>
                <th className={`${TH} text-left`} style={{ width: 70 }}>Symbol</th>
                <th className={`${TH} text-left hidden md:table-cell`} style={{ width: 150 }}>Name</th>

                <th className={`${TH} text-left`} style={{ width: 160 }}>
                  <span
                    className={`cursor-pointer select-none ${sortMode === null ? 'text-gray-300' : 'hover:text-gray-400'}`}
                    onClick={() => setSortMode(null)} title="Sort: most extreme first"
                  >
                    Score{sortMode === null && <ArrowDown className="inline w-3 h-3 ml-0.5" />}
                  </span>
                  <span className="text-gray-700 mx-1">·</span>
                  <span
                    className={`cursor-pointer select-none ${sortMode === 'days' ? 'text-gray-300' : 'hover:text-gray-400'}`}
                    onClick={() => setSortMode(prev => prev === 'days' ? null : 'days')} title="Sort: most recent first"
                  >
                    Age{sortMode === 'days' && <ArrowUp className="inline w-3 h-3 ml-0.5" />}
                  </span>
                </th>

                {/* Per-period columns — 150px; whitespace-nowrap prevents "+55.3% in 29d" wrapping */}
                {FWD_PERIODS.map(({ label, delta }) => {
                  const wr = winRates[label];
                  const open = wr && wr.total > wr.n ? wr.total - wr.n : 0;
                  return (
                    <th key={label} className={`${TH_R} whitespace-nowrap`} style={{ width: 150 }}>
                      {label}
                      {wr?.pct != null || wr?.openOnly ? (
                        <div
                          className={`text-[10px] font-normal normal-case tracking-normal ${wr.openOnly ? 'text-gray-500' : winRateColor(wr.pct)}`}
                          title={wr.openOnly
                            ? `${wr.wins} early ${label} target hit${wr.wins !== 1 ? 's' : ''}; no ${label} windows can be complete in the selected ${maxDaysAgo}d timeframe${open > 0 ? ` · ${open} pending` : ''}`
                            : `${wr.pct}% win rate: ${wr.wins}/${wr.n} won${open > 0 ? ` · ${open} pending` : ''}`}
                        >
                          {wr.openOnly ? (
                            <>
                              {wr.wins} hit <span className="text-gray-600">· {open} pending</span>
                            </>
                          ) : (
                            <>
                              {open > 0 && <span className="text-gray-600">{open} pending · </span>}<span className="text-gray-600">({wr.wins}/{wr.n}) </span>{wr.pct}%
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="text-[10px] text-gray-600 font-normal normal-case tracking-normal">
                          {delta ? 'Δ prev' : 'peak'}
                        </div>
                      )}
                    </th>
                  );
                })}

                {/* Today's score */}
                <th className={`${TH} text-left`} style={{ width: 65 }}>Now</th>

                {/* Δnow — 96px; wide enough for "−250.0%" */}
                <th className={TH_R} style={{ width: 96 }}>
                  <span className="text-gray-400">Δnow</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((peak) => {
                const opacity    = recencyOpacity(peak.days_ago);
                const isHigh     = peak.peak_type === 'HIGH';
                const isFlagged  = peak.flagged;
                const daysLabel  = peak.days_ago === 0 ? 'Today'
                  : peak.days_ago === 1 ? '1 day ago'
                  : `${peak.days_ago} days ago`;
                const dateTooltip = formatDate(peak.peak_date);
                const curScore   = currentBySymbol[peak.symbol]?.latest_score;

                // Find the period with the highest positive delta for this row
                const bestDeltaLabel = FWD_PERIODS
                  .filter(p => p.delta && peak[p.delta] != null && peak[p.delta] > 0)
                  .reduce((best, p) => {
                    if (!best || peak[p.delta] > peak[best.delta]) return p;
                    return best;
                  }, null)?.label ?? null;

                // Glow: only on the highest-peak winning period per row.
                const bestWinLabel = FWD_PERIODS
                  .filter(p => peak[p.winReach] === 1 && peak[p.peak] != null)
                  .reduce((best, p) => {
                    if (!best || peak[p.peak] > peak[best.peak]) return p;
                    return best;
                  }, null)?.label ?? null;

                // Stale detection: a period is "stale" when its own delta is +0.0
                // (peak didn't grow in that interval). Each period is independent —
                // a later period can resume growing and should not be subdued.
                const staleSet = new Set();
                for (const p of FWD_PERIODS) {
                  if (!p.delta) continue; // 1d — skip
                  const d = peak[p.delta];
                  if (d != null && d <= 0) staleSet.add(p.label);
                }

                return (
                  <tr
                    key={`${peak.symbol}-${peak.peak_date}`}
                    style={{ opacity }}
                    className={`border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors ${
                      isFlagged ? 'bg-amber-500/[0.04]' : ''
                    }`}
                  >
                    <td className="px-3 py-2 align-top">
                      <Link
                        to={`/stock/${peak.symbol}`}
                        className={`font-mono font-semibold hover:underline tabular-nums ${
                          isFlagged
                            ? 'text-amber-300 drop-shadow-[0_0_5px_rgba(251,191,36,0.55)] hover:text-amber-200'
                            : 'text-gray-200 hover:text-white'
                        }`}
                      >
                        {peak.symbol}
                      </Link>
                    </td>

                    {/* Name */}
                    <td className="px-3 py-2 hidden md:table-cell text-gray-400 text-xs align-top max-w-[200px] truncate" title={peak.name}>
                      {peak.name}
                    </td>

                    {/* Score + Age (age has date tooltip) */}
                    <td className="px-3 py-2 align-top whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <ScoreBadge score={peak.peak_score} />
                        <span
                          className="text-gray-400 text-xs tabular-nums cursor-default"
                          title={dateTooltip}
                        >
                          {daysLabel}
                        </span>
                      </div>
                    </td>

                    {/* Per-period: intraday best (inline days) + delta from prev period */}
                    {(() => {
                      // First period whose window hasn't closed yet = the active one
                      const activePeriodLabel = FWD_PERIODS.find(p => peak.days_ago < p.periodDays)?.label ?? null;
                      // Map each period to the pill that belongs in it (from the prior window's result).
                      // Roll-up WR = fresh ATM entry opened AFTER winning (close ITM, new entry at day-X close).
                      // Continuation rate P(win_next|win_prior) = 90-92% (original position holding on).
                      // Re-entry WR = bridge barrier-touch win rate from expire-bar close, calls 70+, 5y.
                      const rw = (runway, staticWr) =>
                        runway && !runway.done ? runway.pctLeft + '% left' : staticWr;
                      const periodPill = {
                        '15d': peak.rollup7   ? { type: 'rollup',  wr: rw(peak.runway7,  '63%'), title: '7d target hit — roll-up: close ITM, open fresh ATM from day-7 close. Fresh 8-day ATM entry WR: 63% (calls 70+, 5y, N=10k+). Original position continues in 90% of cases. Same WR for puts (63%). Clears call BE 56.3% by +7pp. Runway = % of target distance still remaining.' }
                             : peak.reentry7d ? { type: 'reentry', wr: rw(peak.runway7,  '58%'), title: '7d expired without stop — re-enter from day-7 close with fresh ATM. Barrier-touch bridge WR: 58% (calls 70+, 5y, N=2,391). Puts are 97% stopped at 7d — calls only. Runway = % of target distance still remaining.' }
                             : null,
                        '30d': peak.rollup15   ? { type: 'rollup',  wr: rw(peak.runway15, '63%'), title: '15d target hit — roll-up: close ITM, open fresh ATM from day-15 close. Fresh 15-day ATM entry WR: 63% (calls 70+, 5y, N=10k+). Original position continues in 91% of cases. Same WR for puts (65%). Clears call BE 56.3% by +7pp. Runway = % of target distance still remaining.' }
                             : peak.reentry15d ? { type: 'reentry', wr: rw(peak.runway15, '61%'), title: '15d hard sell (no trigger) — re-enter from day-15 close with fresh 30 DTE ATM. Barrier-touch bridge WR: 61% (calls 70+, 5y, N=2,182). Puts are 98% stopped at 15d — calls only. Runway = % of target distance still remaining.' }
                             : null,
                        '60d': peak.rollup30   ? { type: 'rollup',  wr: rw(peak.runway30, '64%'), title: '30d target hit — roll-up: close ITM, open fresh ATM from day-30 close. Fresh 30-day ATM entry WR: 64% (calls 70+, 5y, N=10k+). Original position continues in 88% of cases. Same WR for puts (64%). Clears call BE 56.3% by +8pp. Runway = % of target distance still remaining.' }
                             : peak.reentry30d ? { type: 'reentry', wr: rw(peak.runway30, '60%'), title: '30d expired without stop — re-enter from day-30 close with fresh 30 DTE ATM. Barrier-touch bridge WR: 60% (calls 70+, 5y, N=1,994). Calls only. Runway = % of target distance still remaining.' }
                             : null,
                        '90d': peak.rollup60   ? { type: 'rollup',  wr: rw(peak.runway60, '64%'), title: '60d target hit — roll-up: close ITM, open fresh ATM from day-60 close. Fresh 30-day ATM entry WR: 64% (calls 70+, 5y, N=9.8k+). Original position continues in 92% of cases. Same WR for puts (65%). Clears call BE 56.3% by +8pp. Runway = % of target distance still remaining.' }
                             : peak.reentry60d ? { type: 'reentry', wr: rw(peak.runway60, '59%'), title: '60d expired without stop — re-enter from day-60 close with fresh 30 DTE ATM. Barrier-touch bridge WR: 59% (calls 70+, 5y, N=1,980). Calls only. Runway = % of target distance still remaining.' }
                             : null,
                      };
                      return FWD_PERIODS.map(({ label, peak: pkField, days: dField, delta: deltaField, winReach, periodDays }, idx) => {
                        const winField     = winReach;
                        const isBestDelta  = label === bestDeltaLabel;
                        const isGlow       = label === bestWinLabel;
                        const isStale      = staleSet.has(label);
                        const winVal       = peak[winField];           // 1 | 0 | null
                        const windowPassed = peak.days_ago >= periodDays;
                        const isActive     = label === activePeriodLabel;
                        const isOpenOpportunity = isActive && winVal == null && peak[pkField] != null && !windowPassed;
                        const prevPeriodDays = FWD_PERIODS[idx - 1]?.periodDays ?? 0;
                        const progress = windowPassed
                          ? 1
                          : isActive
                            ? Math.max(0, Math.min(1, (peak.days_ago - prevPeriodDays) / (periodDays - prevPeriodDays)))
                            : 0;
                        const pill         = isActive ? (periodPill[label] ?? null) : null;
                        return (
                          <td key={label} className="px-3 py-2 text-right align-top whitespace-nowrap relative overflow-hidden">
                            {/* Active-window: faint background tint */}
                            {isActive && (
                              <div className={`absolute inset-0 pointer-events-none ${isOpenOpportunity ? 'bg-sky-400/[0.055]' : 'bg-white/[0.025]'}`} />
                            )}
                            {/* Elapsed-window progress: complete windows fill, active window advances. */}
                            {progress > 0 && (
                              <div
                                className="absolute bottom-0 left-0 h-[2px] bg-white/20 transition-none"
                                style={{ width: `${progress * 100}%` }}
                              />
                            )}
                            <div>{peakBadge(peak[pkField], isHigh, label, peak[dField], { glow: isGlow, stale: isStale, win: winVal, windowPassed })}</div>
                            <div className="mt-0.5 flex items-center justify-end gap-1">
                              {pill?.type === 'rollup' && (
                                <span title={pill.title} className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded border border-trading-green-400/40 text-trading-green-400/90 text-[9px] font-medium leading-none cursor-default">
                                  <TrendingUp className="w-2 h-2" />
                                  {pill.wr}
                                </span>
                              )}
                              {pill?.type === 'reentry' && (
                                <span title={pill.title} className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded border border-sky-400/30 text-sky-400/80 text-[9px] font-medium leading-none cursor-default">
                                  <RefreshCw className="w-2 h-2" />
                                  {pill.wr}
                                </span>
                              )}
                              {isOpenOpportunity && !pill && (
                                <span title="Window still open; this is not counted as a loss unless the timeframe closes without a target hit." className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded border border-sky-400/30 text-sky-400/80 text-[9px] font-medium leading-none cursor-default">
                                  <RefreshCw className="w-2 h-2" />
                                  Open
                                </span>
                              )}
                              {deltaField
                                ? deltaBadge(peak[deltaField], isBestDelta)
                                : <span className="text-gray-800 text-[10px]">—</span>
                              }
                            </div>
                          </td>
                        );
                      });
                    })()}

                    {/* Today's score */}
                    <td className="px-3 py-2 align-top">
                      {curScore?.OVR != null
                        ? <ScoreBadge score={curScore.OVR} />
                        : <span className="text-gray-600 text-xs">—</span>
                      }
                    </td>

                    {/* Δnow */}
                    <td className="px-3 py-2 text-right align-top whitespace-nowrap overflow-hidden">
                      {fwdBadge(peak.price_change_pct, isHigh)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        </div>
        </div>
      </div>
    </div>
  );
};

export default Historic;
