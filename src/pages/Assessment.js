import React, { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { TrendingUp, TrendingDown, Target, Activity, GitCommit, ChevronDown, ChevronLeft, ChevronRight, Check } from 'lucide-react';
import { shouldRefreshOnResume } from '../utils/timeUtils';
import PortfolioProfileToggle, { DEFAULT_PORTFOLIO_PROFILES, normalizeProfiles } from '../components/PortfolioProfileToggle';

const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5000';
const cacheBustBucket = () => String(Math.floor(Date.now() / 30000));

// ── Constants ─────────────────────────────────────────────────────────────────

const ASSESSMENT_PERIODS = ['1d', '3d', '5d', '7d', '15d', '30d', '60d', '90d'];
const IC_PERIODS = ASSESSMENT_PERIODS;

const CALL_BUCKETS = new Set(['95+', '90+', '85+', '80+', '75+', '70+']);
const PUT_BUCKETS  = new Set(['<25', '<20', '<15', '<10', '<5']);

const TABS = [
  { id: 'monte_carlo', label: 'Monte Carlo' },
  { id: 'win_rate',    label: 'Win Rates' },
  { id: 'take_profit', label: 'Take Profit %' },
  { id: 'windows',     label: 'Windows' },
  { id: 'returns',     label: 'Returns & Peak' },
  { id: 'excursion',   label: 'Excursion' },
  { id: 'ic',          label: 'IC Bands' },
  { id: 'pearson',     label: 'Pearson' },
  { id: 'calendar',    label: 'Calendar' },
];

// ── Take Profit % thresholds (Phase 17, 2026-04-29) ──
// Option-aligned barriers from shipped Phase H5/15B C1 (TP=+0.35/SL=−0.30 calls,
// TP=+0.35/SL=−0.20 puts). Realistic-slippage break-even per side:
//   Call: net_TP=+0.34 / net_SL=−0.323 → BE = 0.323 / 0.663 ≈ 48.7%
//   Put:  net_TP=+0.34 / net_SL=−0.223 → BE = 0.223 / 0.563 ≈ 39.6%
// Identical % across 30 DTE and 15 DTE because TP/SL ratios are preserved.
const TP_CALL_BE = 48.7;
const TP_PUT_BE  = 39.6;

// ── Calendar helpers ──────────────────────────────────────────────────────────

const CALL_BE = 56.3;  // break-even TP rate for calls
const PUT_BE  = 43.5;  // break-even TP rate for puts

// For the "combined" (All) view, BE depends on the call/put mix in each row.
// A single fixed threshold mislabels rows where both sides individually clear
// their own BE but the blended tp_rate lands between CALL_BE and PUT_BE.
function blendedBE(row) {
  if (!row) return CALL_BE;
  const cn = row.call_n || 0;
  const pn = row.put_n  || 0;
  if (cn + pn === 0) return CALL_BE;
  if (pn === 0)      return CALL_BE;
  if (cn === 0)      return PUT_BE;
  return (cn * CALL_BE + pn * PUT_BE) / (cn + pn);
}

// DTE strategy variants — the dashboard toggle switches the "primary" period anchors.
// 30 DTE: hard-sell at bar 15, full hold ends at 30 cal days. Primary periods: 15d, 30d.
// 15 DTE: hard-sell at bar 7,  full hold ends at 15 cal days. Primary periods: 7d, 15d.
const DTE_STRATEGIES = {
  '30': { label: '30 DTE', hardPeriod: '15d', holdPeriod: '30d', primaries: ['15d', '30d'] },
  '15': { label: '15 DTE', hardPeriod: '7d',  holdPeriod: '15d', primaries: ['7d', '15d'] },
};
const DTE_STRATEGY_STORAGE_KEY = 'assessmentDteStrategyPrimary20260515';
const PORTFOLIO_PROFILE_STORAGE_KEY = 'assessmentPortfolioProfile20260523';
// TP% anchor variant (2026-08-10 dual-anchor reanchor — experiments/assess_reanchor_2026_08/).
// 'tp' = frozen Phase H5/15B anchors (feeds W1-W6 ship gates, kept byte-identical).
// 'tp26' = live 2026-08-10 TP/SL retune canon, 30 DTE only, measurement-only (additive).
const TP_ANCHOR_STORAGE_KEY = 'assessmentTpAnchor20260810';
const TP_ANCHORS = {
  tp:   { label: 'Legacy',     title: 'Frozen Phase H5 (30 DTE) / 15B (15 DTE) anchors — feeds the W1-W6 ship gates' },
  tp26: { label: '2026 Canon', title: 'Live 2026-08-10 TP/SL retune canon (calls TP+10%/SL-100%), 30 DTE only — measurement instrument, gates still read Legacy' },
};
const MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function heatColor(tp, side, row) {
  if (tp == null) return { bg: 'bg-white/[0.02]', text: 'text-gray-700' };
  const be = side === 'put'  ? PUT_BE
           : side === 'call' ? CALL_BE
           : blendedBE(row);
  const delta = tp - be;
  if (tp >= 75)                  return { bg: 'bg-trading-green-400/25', text: 'text-trading-green-200' };
  if (tp >= 65)                  return { bg: 'bg-trading-green-400/15', text: 'text-trading-green-300' };
  if (delta >= 5)                return { bg: 'bg-trading-green-400/10', text: 'text-trading-green-400' };
  if (delta >= 0)                return { bg: 'bg-white/[0.04]',         text: 'text-gray-300' };
  if (delta >= -8)               return { bg: 'bg-amber-400/8',          text: 'text-amber-300' };
  return                                { bg: 'bg-trading-red-400/12',   text: 'text-trading-red-400' };
}

function fmtReturn(pct) {
  if (pct == null) return '—';
  return fmtSignedMagnitude(pct, {
    decimals: 0,
    suffix: '%',
    compactSuffix: 'k',
    forcePlusZero: true,
  }) || '—';
}

// ── Delta helpers (for historical-version comparison) ──
const COMPACT_METRIC_THRESHOLD = 10000;
const SCIENTIFIC_METRIC_THRESHOLD = 1000000;

function fmtSignedMagnitude(
  value,
  {
    decimals = 1,
    suffix = '',
    compactSuffix = null,
    compactThreshold = COMPACT_METRIC_THRESHOLD,
    scientificThreshold = SCIENTIFIC_METRIC_THRESHOLD,
    forcePlusZero = false,
  } = {}
) {
  if (value == null || !isFinite(value)) return null;
  const abs = Math.abs(value);
  const sign = value > 0 ? '+' : value < 0 ? '-' : (forcePlusZero ? '+' : '');

  if (abs >= scientificThreshold) {
    return `${sign}${abs.toExponential(2)}${suffix}`;
  }

  if (compactSuffix && abs >= compactThreshold) {
    const scaled = abs / 1000;
    const compactDecimals = decimals === 0 || scaled >= 100 ? 0 : 1;
    return `${sign}${scaled.toFixed(compactDecimals)}${compactSuffix}${suffix}`;
  }

  const regularDecimals = abs >= 100 ? 0 : decimals;
  return `${sign}${abs.toFixed(regularDecimals)}${suffix}`;
}

function fmtPpDelta(v) {
  return fmtSignedMagnitude(v, { decimals: 1, suffix: 'pp', compactSuffix: 'k' });
}

function fmtReturnDelta(v) {
  return fmtSignedMagnitude(v, { decimals: 0, suffix: 'pp', compactSuffix: 'k' });
}

function deltaColor(v, invert = false) {
  if (v == null || !isFinite(v) || v === 0) return 'text-gray-500';
  const good = invert ? v < 0 : v > 0;
  return good ? 'text-trading-green-400' : 'text-trading-red-400';
}

/**
 * Inline delta chip shown next to a value when a historical version is being
 * compared against the active version. `kind`: 'pp' (percentage-point, default)
 * or 'return' (large-magnitude return diff). `invert=true` for metrics where
 * lower is better (e.g. drawdown).
 */
function Delta({ value, kind = 'pp', invert = false, className = '' }) {
  if (value == null || !isFinite(value)) return null;
  const label = kind === 'return' ? fmtReturnDelta(value) : fmtPpDelta(value);
  if (label == null) return null;
  return (
    <span className={`text-[10px] tabular-nums font-mono ${deltaColor(value, invert)} ${className}`}>
      {label}
    </span>
  );
}

function CalendarTab({ apiBase, versionId, activeVersionId, dteStrategy = '30', portfolioProfile = 'sentinel', refreshNonce = 0, onProfiles }) {
  const [tData, setTData]   = useState(null);
  const [tLoad, setTLoad]   = useState(true);
  const [tErr,  setTErr]    = useState(null);
  const [view,  setView]    = useState('combined'); // 'combined' | 'calls' | 'puts'
  const [activeData, setActiveData] = useState(null);

  useEffect(() => {
    setTLoad(true);
    setTErr(null);
    setTData(null);
    const params = new URLSearchParams();
    if (versionId) params.set('version', versionId);
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('profile', portfolioProfile);
    params.set('_t', cacheBustBucket());
    const url = `${apiBase}/api/backtest/temporal${params.toString() ? '?' + params.toString() : ''}`;
    fetch(url)
      .then(r => r.json().then(d => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (!ok) throw new Error(d.error || `HTTP error`);
        if (Array.isArray(d.portfolio_profiles?.profiles)) onProfiles?.(d.portfolio_profiles.profiles);
        setTData(d);
        setTLoad(false);
      })
      .catch(e => { setTErr(e.message); setTLoad(false); });
  }, [apiBase, versionId, dteStrategy, portfolioProfile, refreshNonce, onProfiles]);

  // When a historical version is selected, fetch the active version's temporal
  // stats so each value can show a delta vs active.
  useEffect(() => {
    if (!activeVersionId || !versionId || activeVersionId === versionId) {
      setActiveData(null);
      return;
    }
    let cancelled = false;
    const params = new URLSearchParams();
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('profile', portfolioProfile);
    params.set('_t', cacheBustBucket());
    fetch(`${apiBase}/api/backtest/temporal?${params.toString()}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!cancelled && d && !d.error) setActiveData(d); })
      .catch(() => { if (!cancelled) setActiveData(null); });
    return () => { cancelled = true; };
  }, [apiBase, versionId, activeVersionId, dteStrategy, portfolioProfile, refreshNonce]);

  if (tLoad) return (
    <div className="flex items-center justify-center py-20 text-gray-500 text-sm gap-2">
      <Activity className="w-4 h-4 animate-spin" />
      Loading…
    </div>
  );
  if (tErr) return (
    <div className="py-10 text-center">
      <p className="text-trading-red-400 font-medium mb-1">No backtest data available</p>
      <p className="text-gray-500 text-sm">{tErr}</p>
      <p className="text-gray-600 text-xs mt-2">Run <code className="bg-white/[0.06] px-1 py-0.5 rounded font-mono">trader temporal-refresh --profile {portfolioProfile}</code> to generate.</p>
    </div>
  );

  if (!tData) return null;
  const { yearly = [], monthly = [], monthly_avg = [], summary = {} } = tData;
  const portfolioWindows = tData.portfolio_windows?.windows || [];
  const profileLabel = tData.portfolio_profile?.name || portfolioProfile;

  // Average return per year across the yearly rows (ignoring nulls)
  const yearlyReturns = yearly.map(y => y.return_pct).filter(v => v != null);
  const avgReturnPerYear = yearlyReturns.length
    ? yearlyReturns.reduce((s, v) => s + v, 0) / yearlyReturns.length
    : null;

  // Build month grid: {year -> {month -> row}}
  const monthByYearMo = {};
  monthly.forEach(m => {
    if (!monthByYearMo[m.year]) monthByYearMo[m.year] = {};
    monthByYearMo[m.year][m.month] = m;
  });
  const gridYears = [...new Set(monthly.map(m => m.year))].sort((a, b) => b - a);
  // monthly_avg keyed by calendar month (1-12)
  const avgByMonth = {};
  monthly_avg.forEach(a => { avgByMonth[a.month] = a; });

  // Active-version lookup maps for delta comparison (only set when viewing
  // a non-active version and the active fetch completed).
  const showDelta = !!activeData;
  const activeYearByYear = {};
  const activeMonthByYM  = {};
  const activeAvgByMonth = {};
  let activeAvgReturnPerYear = null;
  let activeSummary = {};
  const activeWindowByName = {};
  if (showDelta) {
    activeSummary = activeData.summary || {};
    (activeData.yearly || []).forEach(y => { activeYearByYear[y.year] = y; });
    (activeData.monthly || []).forEach(m => {
      activeMonthByYM[`${m.year}-${m.month}`] = m;
    });
    (activeData.monthly_avg || []).forEach(a => { activeAvgByMonth[a.month] = a; });
    (activeData.portfolio_windows?.windows || []).forEach(w => { activeWindowByName[w.name] = w; });
    const activeReturns = (activeData.yearly || [])
      .map(y => y.return_pct).filter(v => v != null);
    activeAvgReturnPerYear = activeReturns.length
      ? activeReturns.reduce((s, v) => s + v, 0) / activeReturns.length
      : null;
  }
  // Pick the active equivalent of `tpForView` for arbitrary rows
  function activeTpForView(row) {
    if (!row) return null;
    if (view === 'calls') return row.call_tp_rate;
    if (view === 'puts')  return row.put_tp_rate;
    return row.tp_rate;
  }
  const diff = (a, b) => (a == null || b == null ? null : a - b);

  // Determine bar scale for yearly chart
  const allRates = yearly.flatMap(y => [y.tp_rate, y.call_tp_rate, y.put_tp_rate].filter(Boolean));
  const maxRate  = allRates.length ? Math.max(...allRates) : 100;

  function barPct(val) {
    return val != null ? Math.min(100, (val / Math.max(maxRate, 80)) * 100) : 0;
  }

  function tpForView(row) {
    if (view === 'calls') return row.call_tp_rate;
    if (view === 'puts')  return row.put_tp_rate;
    return row.tp_rate;
  }
  function beForRow(row) {
    if (view === 'puts')  return PUT_BE;
    if (view === 'calls') return CALL_BE;
    return blendedBE(row);
  }
  function windowRangeLabel(row) {
    const start = row.start || row.window?.start;
    const end = row.end || row.window?.end;
    return `${start || '—'} → ${end || 'now'}`;
  }
  function sourceLabel(source) {
    if (source === 'research_pack') return 'pack';
    if (source === 'temporal_monthly') return 'monthly';
    return 'pending';
  }

  return (
    <div className="space-y-8">
      {/* ── Methodology banner — single-path replay disclaimer ─────────────── */}
      <div className="bg-amber-500/[0.06] border border-amber-500/30 rounded px-4 py-3 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium uppercase tracking-[0.18em] text-amber-300">
            Deterministic single-path replay
          </span>
          <span className="text-[10px] text-gray-600 font-mono">backtest_cascade.py</span>
        </div>
        <p className="text-[12px] text-gray-400 leading-relaxed">
          These numbers come from <span className="text-gray-200 font-medium">one chronological replay</span> through real OHLCV
          with score-descending tiebreaks — a single realization, not an MC distribution. The "avg return/yr" and "DD" figures
          below are useful as a "the math compounds" sanity check, but they
          {' '}<span className="text-gray-200 font-medium">do not match the ship-gate metrics</span> in version-history.md
          (which use N=500 × 8-window MC). At the compound scale this strategy produces, single-path return numbers can vary
          by orders of magnitude across paths that have similar MC distributions.
        </p>
        <p className="text-[11px] text-gray-500 leading-relaxed">
          For decision-making, prefer <span className="text-gray-300">5y MC WorstDD</span> (Stage 3 T4 ship gate) and
          {' '}<span className="text-gray-300">per-trade TP% / WR15 by tier</span> (Stage 1 W1 ship gate) over compound returns at this scale.
        </p>
      </div>
      {showDelta && (
        <p className="text-[11px] text-gray-500 -mt-2">
          Δ values shown next to each metric are <span className="text-gray-300">this version − active version</span>;
          green = better, red = worse (drawdown inverted).
        </p>
      )}
      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Side filter */}
        <div className="flex items-center border border-white/[0.06] rounded-md overflow-hidden">
          {[['combined','All'],['calls','Calls'],['puts','Puts']].map(([v, lbl], i) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2.5 py-1 text-[11px] font-medium transition-colors whitespace-nowrap ${
                i > 0 ? 'border-l border-white/[0.06]' : ''
              } ${v === view ? 'bg-white/[0.06] text-white' : 'text-gray-500 hover:text-gray-300'}`}
            >
              {lbl}
            </button>
          ))}
        </div>
        {/* Summary pills */}
        {summary.n_trades != null && (
          <div className="flex items-center gap-3 ml-auto text-[11px] text-gray-500">
            <span>{summary.n_trades?.toLocaleString()} trades</span>
            <span className="text-gray-700">·</span>
            {avgReturnPerYear != null && (
              <>
                <span className="text-gray-400" title="Single-path replay — one realization of MC noise. Not the ship-gate metric. See banner above.">
                  avg return/yr (1-path)
                </span>
                <span className={avgReturnPerYear >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'}>
                  {fmtReturn(avgReturnPerYear)}
                </span>
                {showDelta && (
                  <Delta
                    value={diff(avgReturnPerYear, activeAvgReturnPerYear)}
                    kind="return"
                  />
                )}
                <span className="text-gray-700">·</span>
              </>
            )}
            <span title="Max drawdown of the single replay path. DD survives translation to real execution; compound does not. Use 5y MC WorstDD as the ship-gate metric.">
              DD (1-path) {summary.max_dd?.toFixed(1)}%
            </span>
            {showDelta && (
              <Delta
                value={diff(summary.max_dd, activeSummary.max_dd)}
                invert
              />
            )}
            <span className="text-gray-700">·</span>
            <span className="text-gray-400">
              profile {profileLabel}
            </span>
            {tData.computed_at && (
              <>
                <span className="text-gray-700">·</span>
                <span title={`Backtest temporal stats last refreshed ${new Date(tData.computed_at).toLocaleString()}`}>
                  refreshed {new Date(tData.computed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                  {' '}{new Date(tData.computed_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── Named portfolio windows ──────────────────────────────────────── */}
      <div>
        <SectionHeader>Portfolio stress windows</SectionHeader>
        <ScrollTable minWidth={760}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.08]">
                <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Window</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Return</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Max DD</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Trades</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">TP%</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Calls</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Puts</th>
                <th className="text-right py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Source</th>
              </tr>
            </thead>
            <tbody>
              {portfolioWindows.map(w => {
                const m = w.metrics || {};
                const activeW = showDelta ? activeWindowByName[w.name] : null;
                const activeM = activeW?.metrics || {};
                const dRet = showDelta ? diff(m.total_return_pct, activeM.total_return_pct) : null;
                const dDd = showDelta ? diff(m.max_dd_pct, activeM.max_dd_pct) : null;
                const dTp = showDelta ? diff(m.tp_rate, activeM.tp_rate) : null;
                const status = w.ready ? 'ready' : 'waiting';
                const statusClass = w.ready
                  ? 'bg-trading-green-400/10 text-trading-green-300 border-trading-green-400/20'
                  : 'bg-amber-400/8 text-amber-300 border-amber-400/20';
                const title = (w.missing || []).join('; ') || w.description || '';
                return (
                  <tr key={w.name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="py-2 px-3">
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-[12px] font-medium text-gray-200 truncate">{w.label || w.name}</span>
                          <span
                            title={title}
                            className={`inline-flex items-center border rounded px-1.5 py-0.5 text-[9px] uppercase tracking-[0.12em] ${statusClass}`}
                          >
                            {status}
                          </span>
                        </div>
                        <span className="text-[10px] text-gray-600 font-mono tabular-nums">{windowRangeLabel(w)}</span>
                      </div>
                    </td>
                    <td className={`py-2 px-2 text-right text-[12px] font-mono ${m.total_return_pct != null ? returnColor(m.total_return_pct) : 'text-gray-600'}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.total_return_pct != null ? fmtReturn(m.total_return_pct) : '—'}
                        {showDelta && <Delta value={dRet} kind="return" />}
                      </span>
                    </td>
                    <td className={`py-2 px-2 text-right text-[12px] font-mono ${m.max_dd_pct != null ? (m.max_dd_pct <= 70 ? 'text-trading-green-400' : m.max_dd_pct <= 80 ? 'text-amber-300' : 'text-trading-red-400') : 'text-gray-600'}`}>
                      <span className="inline-flex items-center gap-1 justify-end" title={m.max_dd_peak_date && m.max_dd_trough_date ? `${m.max_dd_peak_date} → ${m.max_dd_trough_date}` : m.max_dd_source || ''}>
                        {m.max_dd_pct != null ? `${m.max_dd_pct.toFixed(1)}%` : '—'}
                        {showDelta && <Delta value={dDd} invert />}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-right text-[12px] text-gray-500 tabular-nums">{m.n_trades?.toLocaleString?.() || '—'}</td>
                    <td className={`py-2 px-2 text-right text-[12px] font-mono ${winRateColor(m.tp_rate)}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.tp_rate != null ? `${m.tp_rate.toFixed(1)}%` : '—'}
                        {showDelta && <Delta value={dTp} />}
                      </span>
                    </td>
                    <td className="py-2 px-2 text-right text-[12px] text-gray-500 tabular-nums">{m.call_trades?.toLocaleString?.() || '—'}</td>
                    <td className="py-2 px-2 text-right text-[12px] text-gray-500 tabular-nums">{m.put_trades?.toLocaleString?.() || '—'}</td>
                    <td className="py-2 px-3 text-right text-[10px] uppercase tracking-[0.12em] text-gray-600">{sourceLabel(w.metric_source)}</td>
                  </tr>
                );
              })}
              {portfolioWindows.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-sm text-gray-600">No portfolio windows available</td>
                </tr>
              )}
            </tbody>
          </table>
        </ScrollTable>
      </div>

      {/* ── Year-by-year chart ────────────────────────────────────────────── */}
      <div>
        <SectionHeader>Year-by-year TP rate</SectionHeader>
        <div className="space-y-1.5">
          {yearly.map(yr => {
            const tp = tpForView(yr);
            const be = beForRow(yr);
            const bePct = barPct(be);
            const activeYr = showDelta ? activeYearByYear[yr.year] : null;
            const tpDelta  = showDelta ? diff(tp, activeTpForView(activeYr)) : null;
            const retDelta = showDelta ? diff(yr.return_pct, activeYr?.return_pct) : null;
            return (
              <div key={yr.year} className="flex items-center gap-3">
                <span className="text-[12px] font-mono tabular-nums text-gray-400 w-12 text-right flex-shrink-0">
                  {yr.year}
                </span>
                {/* Bar track */}
                <div className="relative flex-1 h-6 bg-white/[0.03] rounded overflow-hidden">
                  {/* Break-even marker */}
                  <div
                    className="absolute top-0 bottom-0 w-px bg-white/20"
                    style={{ left: `${bePct}%` }}
                    title={view === 'combined'
                      ? `BE ${be.toFixed(1)}% (blended: ${yr.call_n || 0} calls @ ${CALL_BE}% + ${yr.put_n || 0} puts @ ${PUT_BE}%)`
                      : `BE ${be}%`}
                  />
                  {/* TP bar */}
                  <div
                    className={`absolute left-0 top-0 bottom-0 rounded transition-all ${
                      tp != null && tp >= be ? 'bg-trading-green-400/40' : 'bg-trading-red-400/40'
                    }`}
                    style={{ width: `${barPct(tp)}%` }}
                  />
                  {/* TP label */}
                  {tp != null && (
                    <span className={`absolute left-2 top-1/2 -translate-y-1/2 text-[11px] font-mono tabular-nums font-medium flex items-center gap-1.5 ${
                      tp >= be ? 'text-trading-green-300' : 'text-trading-red-400'
                    }`}>
                      {tp.toFixed(1)}%
                      {showDelta && <Delta value={tpDelta} />}
                    </span>
                  )}
                </div>
                {/* Meta */}
                <div className="flex items-center gap-2 text-[11px] tabular-nums w-36 flex-shrink-0 justify-end">
                  <span className="text-gray-600">{yr.n_trades.toLocaleString()}t</span>
                  <span className={`${yr.return_pct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'} font-mono`}>
                    {fmtReturn(yr.return_pct)}
                  </span>
                  {showDelta && <Delta value={retDelta} kind="return" />}
                </div>
              </div>
            );
          })}
          {yearly.length === 0 && (
            <p className="text-gray-600 text-sm text-center py-6">No data</p>
          )}
        </div>
        <p className="mt-2 text-[11px] text-gray-600">
          White line = break-even TP rate ({view === 'puts' ? `${PUT_BE}%` : view === 'calls' ? `${CALL_BE}%` : `${PUT_BE}%/${CALL_BE}% blended per row by call/put mix`}).
          Bars left of the line are below break-even.
        </p>
      </div>

      {/* ── Monthly heatmap ───────────────────────────────────────────────── */}
      <div>
        <SectionHeader>Monthly TP rate heatmap</SectionHeader>
        <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
          <table className="text-[11px] tabular-nums border-separate border-spacing-[2px]" style={{ minWidth: 560 }}>
            <thead>
              <tr>
                <th className="text-right pr-2 pb-1 font-normal text-gray-600 w-12">Year</th>
                {MONTH_ABBR.map(m => (
                  <th key={m} className="text-center pb-1 font-normal text-gray-600 w-10">{m}</th>
                ))}
                <th className="text-right pl-2 pb-1 font-normal text-gray-600 w-14">Return</th>
              </tr>
            </thead>
            <tbody>
              {gridYears.map(yr => {
                const yrRow = yearly.find(y => y.year === yr);
                const activeYr = showDelta ? activeYearByYear[yr] : null;
                const retDelta = showDelta ? diff(yrRow?.return_pct, activeYr?.return_pct) : null;
                return (
                  <tr key={yr}>
                    <td className="text-right pr-2 font-mono text-gray-500">{yr}</td>
                    {Array.from({ length: 12 }, (_, idx) => {
                      const mo  = idx + 1;
                      const row = monthByYearMo[yr]?.[mo];
                      const tp  = row ? tpForView(row) : null;
                      const n   = row?.n_trades ?? 0;
                      const { bg, text } = heatColor(tp, view === 'puts' ? 'put' : view === 'calls' ? 'call' : 'combined', row);
                      const activeRow = showDelta ? activeMonthByYM[`${yr}-${mo}`] : null;
                      const tpDelta = showDelta ? diff(tp, activeTpForView(activeRow)) : null;
                      const titleBase = row
                        ? `${MONTH_ABBR[idx]} ${yr}: ${tp != null ? tp.toFixed(1) + '% TP' : 'n/a'} · ${n} trades`
                        : `${MONTH_ABBR[idx]} ${yr}: no data`;
                      const titleText = showDelta && tpDelta != null
                        ? `${titleBase} · vs active ${fmtPpDelta(tpDelta)}`
                        : titleBase;
                      return (
                        <td key={mo} className="p-0">
                          <div
                            className={`flex flex-col items-center justify-center rounded w-10 h-9 ${bg} cursor-default`}
                            title={titleText}
                          >
                            {tp != null ? (
                              <>
                                <span className={`${text} font-medium leading-none`}>{tp.toFixed(0)}%</span>
                                {showDelta && tpDelta != null ? (
                                  <span className={`${deltaColor(tpDelta)} text-[8px] leading-none mt-0.5 font-mono tabular-nums`}>
                                    {fmtPpDelta(tpDelta)}
                                  </span>
                                ) : (
                                  <span className="text-gray-600 text-[9px] leading-none mt-0.5">{n}</span>
                                )}
                              </>
                            ) : (
                              <span className="text-gray-800 text-[9px]">—</span>
                            )}
                          </div>
                        </td>
                      );
                    })}
                    <td className="text-right pl-2 font-mono">
                      {yrRow ? (
                        <span className="inline-flex items-center gap-1">
                          <span className={yrRow.return_pct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'}>
                            {fmtReturn(yrRow.return_pct)}
                          </span>
                          {showDelta && <Delta value={retDelta} kind="return" />}
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                );
              })}
              {gridYears.length === 0 && (
                <tr>
                  <td colSpan={14} className="text-center text-gray-600 py-8">No monthly data</td>
                </tr>
              )}
              {/* ── Cross-year average row ── */}
              {monthly_avg.length > 0 && (
                <tr className="border-t border-white/[0.08]">
                  <td className="text-right pr-2 font-medium text-[11px] text-gray-500 pt-1">Avg</td>
                  {Array.from({ length: 12 }, (_, idx) => {
                    const mo  = idx + 1;
                    const row = avgByMonth[mo];
                    const tp  = row ? tpForView(row) : null;
                    const n   = row?.n_trades ?? 0;
                    const { bg, text } = heatColor(tp, view === 'puts' ? 'put' : view === 'calls' ? 'call' : 'combined');
                    const activeAvg = showDelta ? activeAvgByMonth[mo] : null;
                    const tpDelta = showDelta ? diff(tp, activeTpForView(activeAvg)) : null;
                    const titleBase = row
                      ? `${MONTH_ABBR[idx]} avg (${row.years_sampled}y): ${tp != null ? tp.toFixed(1) + '% TP' : 'n/a'} · ${n} trades`
                      : `${MONTH_ABBR[idx]}: no data`;
                    const titleText = showDelta && tpDelta != null
                      ? `${titleBase} · vs active ${fmtPpDelta(tpDelta)}`
                      : titleBase;
                    return (
                      <td key={mo} className="p-0 pt-1">
                        <div
                          className={`flex flex-col items-center justify-center rounded w-10 h-9 ${bg} cursor-default ring-1 ring-white/[0.06]`}
                          title={titleText}
                        >
                          {tp != null ? (
                            <>
                              <span className={`${text} font-semibold leading-none`}>{tp.toFixed(0)}%</span>
                              {showDelta && tpDelta != null ? (
                                <span className={`${deltaColor(tpDelta)} text-[8px] leading-none mt-0.5 font-mono tabular-nums`}>
                                  {fmtPpDelta(tpDelta)}
                                </span>
                              ) : (
                                <span className="text-gray-500 text-[9px] leading-none mt-0.5">{row.years_sampled}y</span>
                              )}
                            </>
                          ) : (
                            <span className="text-gray-800 text-[9px]">—</span>
                          )}
                        </div>
                      </td>
                    );
                  })}
                  <td className="text-right pl-2 font-mono text-gray-600 text-[11px] pt-1">—</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center gap-4 mt-3 flex-wrap">
          <span className="text-[10px] uppercase tracking-[0.14em] text-gray-600">Legend</span>
          {[
            { bg: 'bg-trading-green-400/25', label: '≥75%' },
            { bg: 'bg-trading-green-400/15', label: '65–75%' },
            { bg: 'bg-trading-green-400/10', label: `BE+5 – 65%` },
            { bg: 'bg-white/[0.04]',         label: `~BE` },
            { bg: 'bg-amber-400/8',          label: 'Below BE' },
            { bg: 'bg-trading-red-400/12',   label: 'Well below BE' },
          ].map(({ bg, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-3 h-3 rounded-sm ${bg} border border-white/[0.06]`} />
              <span className="text-[11px] text-gray-500">{label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Monthly detail table ─────────────────────────────────────────── */}
      <div>
        <SectionHeader>Month-by-month detail</SectionHeader>
        <ScrollTable minWidth={520}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.08]">
                <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Month</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Trades</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">TP%</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Call TP%</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Put TP%</th>
                <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600">Return</th>
              </tr>
            </thead>
            <tbody>
              {[...monthly].reverse().map(m => {
                const label = `${MONTH_ABBR[m.month - 1]} ${m.year}`;
                const a = showDelta ? activeMonthByYM[`${m.year}-${m.month}`] : null;
                const dTp     = showDelta ? diff(m.tp_rate,      a?.tp_rate)      : null;
                const dCallTp = showDelta ? diff(m.call_tp_rate, a?.call_tp_rate) : null;
                const dPutTp  = showDelta ? diff(m.put_tp_rate,  a?.put_tp_rate)  : null;
                const dRet    = showDelta ? diff(m.return_pct,   a?.return_pct)   : null;
                return (
                  <tr key={label} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="py-1.5 px-3 font-mono tabular-nums text-gray-400 text-[12px]">{label}</td>
                    <td className="py-1.5 px-2 text-right text-gray-500 text-[12px]">{m.n_trades}</td>
                    <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(m.tp_rate)}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.tp_rate != null ? `${m.tp_rate.toFixed(1)}%` : '—'}
                        {showDelta && <Delta value={dTp} />}
                      </span>
                    </td>
                    <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(m.call_tp_rate)}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.call_tp_rate != null ? `${m.call_tp_rate.toFixed(1)}%` : '—'}
                        {showDelta && <Delta value={dCallTp} />}
                      </span>
                    </td>
                    <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(m.put_tp_rate)}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.put_tp_rate != null ? `${m.put_tp_rate.toFixed(1)}%` : '—'}
                        {showDelta && <Delta value={dPutTp} />}
                      </span>
                    </td>
                    <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${m.return_pct != null ? returnColor(m.return_pct) : 'text-gray-600'}`}>
                      <span className="inline-flex items-center gap-1 justify-end">
                        {m.return_pct != null ? fmtReturn(m.return_pct) : '—'}
                        {showDelta && <Delta value={dRet} kind="return" />}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {/* ── Per-calendar-month averages ── */}
              {monthly_avg.length > 0 && (
                <>
                  <tr className="border-t border-white/[0.10]">
                    <td colSpan={6} className="py-1.5 px-3 text-[10px] uppercase tracking-[0.14em] text-gray-600 font-medium">
                      Average by calendar month
                    </td>
                  </tr>
                  {monthly_avg.map(a => {
                    const av = showDelta ? activeAvgByMonth[a.month] : null;
                    const dTp     = showDelta ? diff(a.tp_rate,      av?.tp_rate)      : null;
                    const dCallTp = showDelta ? diff(a.call_tp_rate, av?.call_tp_rate) : null;
                    const dPutTp  = showDelta ? diff(a.put_tp_rate,  av?.put_tp_rate)  : null;
                    return (
                      <tr key={`avg-${a.month}`} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                        <td className="py-1.5 px-3 font-mono tabular-nums text-gray-400 text-[12px]">
                          {MONTH_ABBR[a.month - 1]}
                          <span className="text-gray-700 ml-1 text-[10px]">({a.years_sampled}y)</span>
                        </td>
                        <td className="py-1.5 px-2 text-right text-gray-500 text-[12px]">{a.n_trades ?? '—'}</td>
                        <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(a.tp_rate)}`}>
                          <span className="inline-flex items-center gap-1 justify-end">
                            {a.tp_rate != null ? `${a.tp_rate.toFixed(1)}%` : '—'}
                            {showDelta && <Delta value={dTp} />}
                          </span>
                        </td>
                        <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(a.call_tp_rate)}`}>
                          <span className="inline-flex items-center gap-1 justify-end">
                            {a.call_tp_rate != null ? `${a.call_tp_rate.toFixed(1)}%` : '—'}
                            {showDelta && <Delta value={dCallTp} />}
                          </span>
                        </td>
                        <td className={`py-1.5 px-2 text-right text-[12px] font-mono ${winRateColor(a.put_tp_rate)}`}>
                          <span className="inline-flex items-center gap-1 justify-end">
                            {a.put_tp_rate != null ? `${a.put_tp_rate.toFixed(1)}%` : '—'}
                            {showDelta && <Delta value={dPutTp} />}
                          </span>
                        </td>
                        <td className="py-1.5 px-2 text-right text-gray-600 text-[12px]">—</td>
                      </tr>
                    );
                  })}
                </>
              )}
            </tbody>
          </table>
        </ScrollTable>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtVersionDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}

function fmtPct(v, decimals = 1) {
  if (v == null) return '—';
  return fmtSignedMagnitude(v, { decimals, suffix: '%', compactSuffix: 'k' }) || '—';
}

// Sample-count floor: below this, WR/return cells render `—` regardless of value.
// Single-digit buckets are statistically meaningless and mislead at a glance.
const MIN_RELIABLE_N = 10;

function winRateColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 90) return 'text-trading-green-300 font-semibold';
  if (v >= 80) return 'text-trading-green-400 font-medium';
  if (v >= 70) return 'text-trading-green-400';
  return 'text-amber-300';
}

// Take Profit % color: green when comfortably above side BE, amber near BE,
// red when below BE. Side-specific thresholds (Phase 17).
function tpRateColor(v, side = 'call') {
  if (v == null) return 'text-gray-600';
  const be = side === 'put' ? TP_PUT_BE : TP_CALL_BE;
  const margin = v - be;
  if (margin >= 20) return 'text-trading-green-300 font-semibold';
  if (margin >= 10) return 'text-trading-green-400 font-medium';
  if (margin >=  0) return 'text-trading-green-400';
  if (margin >= -8) return 'text-amber-300';
  return 'text-trading-red-400';
}

// Amber-glow box-shadow matching ScoreBadge's super-high treatment. Ramps 90→100.
const WINRATE_GLOW_RGB = [198, 156, 86];
function winRateGlowStyle(v) {
  if (v == null || v < 90) return undefined;
  const ex = Math.min(1, (v - 90) / 10);
  if (ex <= 0) return undefined;
  const [r, g, b] = WINRATE_GLOW_RGB;
  const k = (mult) => (ex * mult).toFixed(3);
  return {
    boxShadow: [
      `0 0 0 1px rgba(${r},${g},${b},${k(0.3)})`,
      `inset 0 1px 0 0 rgba(${r},${g},${b},${k(0.1)})`,
      `0 0 6px rgba(${r},${g},${b},${k(0.48)})`,
      `0 0 14px rgba(${r},${g},${b},${k(0.3)})`,
      `0 0 26px rgba(${r},${g},${b},${k(0.16)})`,
    ].join(', '),
  };
}

function returnColor(v) {
  if (v == null) return 'text-gray-600';
  if (v > 2)  return 'text-trading-green-300';
  if (v > 0)  return 'text-trading-green-400';
  if (v > -1) return 'text-amber-300';
  return 'text-trading-red-400';
}

function captureColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 0.5) return 'text-trading-green-400';
  if (v >= 0.3) return 'text-amber-300';
  return 'text-trading-red-400';
}

function icColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 0.1) return 'text-trading-green-400';
  if (v >= 0)   return 'text-amber-300';
  return 'text-trading-red-400';
}

function corrColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 0.1) return 'text-trading-green-400';
  if (v >= 0)   return 'text-amber-300';
  return 'text-trading-red-400';
}

function rtrColor(v) {
  if (v == null) return 'text-gray-600';
  if (v > 20)  return 'text-trading-green-300';
  if (v > 0)   return 'text-trading-green-400';
  if (v > -20) return 'text-amber-300';
  return 'text-trading-red-400';
}

// ── Shared primitives ─────────────────────────────────────────────────────────

function BucketPill({ bucket }) {
  const isCall = CALL_BUCKETS.has(bucket);
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono text-[11px] font-semibold px-2 py-0.5 rounded whitespace-nowrap ${
      isCall ? 'text-trading-green-300' : 'text-trading-red-300'
    }`}>
      <span
        className={`w-1 h-1 rounded-full ${isCall ? 'bg-trading-green-300' : 'bg-trading-red-300'}`}
        aria-hidden="true"
      />
      {bucket}
    </span>
  );
}

function SectionHeader({ children }) {
  return (
    <div className="mb-3">
      <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-500">
        {children}
      </span>
    </div>
  );
}

// Scrollable table wrapper — ensures horizontal scroll on small screens
function ScrollTable({ children, minWidth = 480 }) {
  return (
    <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
      <div style={{ minWidth }}>
        {children}
      </div>
    </div>
  );
}

// ── Sub-tables ────────────────────────────────────────────────────────────────

function DeltaBadge({ curr, prev, decimals = 1, suffix = '%', invert = false }) {
  if (curr == null || prev == null) return null;
  const d = curr - prev;
  const minDelta = decimals >= 3 ? 0.0005 : 0.05;
  if (Math.abs(d) < minDelta) return null;
  const good = invert ? d < 0 : d > 0;
  const label = fmtSignedMagnitude(d, { decimals, suffix, compactSuffix: 'k' });
  if (label == null) return null;
  return (
    <span className={`ml-1 font-mono tabular-nums text-[10px] ${good ? 'text-trading-green-400/80' : 'text-trading-red-400/80'}`}>
      ({label})
    </span>
  );
}

function bucketsForSelectedWindow(dataset, selectedWindow) {
  if (!dataset) return [];
  if (selectedWindow) {
    const win = (dataset.windows || []).find(w => w.label === selectedWindow);
    if (win) return win.buckets || [];
  }
  return dataset.buckets || [];
}

function bucketMapForSelectedWindow(dataset, selectedWindow) {
  const map = {};
  bucketsForSelectedWindow(dataset, selectedWindow).forEach(b => { map[b.bucket] = b; });
  return map;
}

function pearsonFromPairs(pairs) {
  if (pairs.length < 3) return null;
  const n = pairs.length;
  const meanX = pairs.reduce((sum, [x]) => sum + x, 0) / n;
  const meanY = pairs.reduce((sum, [, y]) => sum + y, 0) / n;
  let cov = 0;
  let varX = 0;
  let varY = 0;
  pairs.forEach(([x, y]) => {
    const dx = x - meanX;
    const dy = y - meanY;
    cov += dx * dy;
    varX += dx * dx;
    varY += dy * dy;
  });
  const denom = Math.sqrt(varX * varY);
  if (!denom) return null;
  return Math.round((cov / denom) * 1000) / 1000;
}

function computeRtrCorrelationsFromBuckets(buckets) {
  const byBucket = {};
  (buckets || []).forEach(b => { byBucket[b.bucket] = b; });
  const result = {};
  ASSESSMENT_PERIODS.forEach(p => {
    const highPairs = [95, 90, 85, 80, 75, 70]
      .map(t => [t, byBucket[`${t}+`]?.rtr_win_rate?.[p]])
      .filter(([, v]) => v != null);
    const lowPairs = [30, 25, 20, 15, 10, 5]
      .map(t => [50 - t, byBucket[`<${t}`]?.rtr_win_rate?.[p]])
      .filter(([, v]) => v != null);
    result[p] = {
      high: pearsonFromPairs(highPairs),
      low: pearsonFromPairs(lowPairs),
    };
  });
  return result;
}

function WinRateTable({ buckets, useUnscaled = false, prevByBucket = {}, primaryPeriods = [], colorFn = null, glowFn = null }) {
  const displayPeriods = ASSESSMENT_PERIODS;
  const primarySet = new Set(primaryPeriods);
  const cellColor = colorFn || ((v, _side) => winRateColor(v));
  const cellGlow  = glowFn  || ((v, _side) => winRateGlowStyle(v));
  return (
    <ScrollTable minWidth={520}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Bucket</th>
            <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-12">N</th>
            {displayPeriods.map(p => {
              const isPrimary = primarySet.has(p);
              return (
                <th key={p} className={`text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] min-w-[58px] ${
                  isPrimary ? 'text-trading-blue-300 bg-trading-blue-500/10' : 'text-gray-600'
                }`}>{p}</th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {buckets.map((row) => {
            const prev = prevByBucket[row.bucket];
            return (
            <tr
              key={row.bucket}
              className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
            >
              <td className="py-2 px-3"><BucketPill bucket={row.bucket} /></td>
              <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] whitespace-nowrap">
                {row.sample_count}
                <DeltaBadge curr={row.sample_count} prev={prev?.sample_count} decimals={0} suffix="" />
              </td>
              {displayPeriods.map(p => {
                const vRaw = useUnscaled ? row.win_rate_unscaled?.[p] : row.win_rate?.[p];
                const pv   = useUnscaled ? prev?.win_rate_unscaled?.[p] : prev?.win_rate?.[p];
                const lowN = row.sample_count < MIN_RELIABLE_N;
                const v    = lowN ? null : vRaw;
                const glow = cellGlow(v, row.side);
                const isPrimary = primarySet.has(p);
                return (
                  <td key={p} className={`py-2 px-2 text-right align-middle whitespace-nowrap ${
                    isPrimary ? 'bg-trading-blue-500/[0.05]' : ''
                  }`}>
                    <span
                      className={`inline-block px-1.5 py-0.5 rounded-md font-mono tabular-nums text-[12px] ${cellColor(v, row.side)}`}
                      style={glow}
                    >
                      {v != null ? `${v.toFixed(1)}%` : '—'}
                    </span>
                    <DeltaBadge curr={v} prev={pv} />
                  </td>
                );
              })}
            </tr>
          );
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

function ReturnsTable({ buckets, prevByBucket = {} }) {
  const displayPeriods = ['3d', '5d', '7d', '15d', '30d', '60d', '90d'];
  return (
    <ScrollTable minWidth={620}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Bucket</th>
            <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-12">N</th>
            {displayPeriods.map(p => (
              <th key={p} colSpan={2} className="text-center py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 border-l border-white/[0.06]">
                {p}
              </th>
            ))}
          </tr>
          <tr className="border-b border-white/[0.06]">
            <th className="py-1 px-3" />
            <th className="py-1 px-2" />
            {displayPeriods.map(p => (
              <React.Fragment key={p}>
                <th className="py-1 px-2 text-right text-gray-700 text-[10px] font-normal border-l border-white/[0.04] min-w-[52px]">Ret</th>
                <th className="py-1 px-2 text-right text-gray-700 text-[10px] font-normal min-w-[52px]">MFE</th>
              </React.Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((row) => {
            const prev = prevByBucket[row.bucket];
            return (
              <tr
                key={row.bucket}
                className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
              >
                <td className="py-2 px-3"><BucketPill bucket={row.bucket} /></td>
                <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] whitespace-nowrap">
                  {row.sample_count}
                  <DeltaBadge curr={row.sample_count} prev={prev?.sample_count} decimals={0} suffix="" />
                </td>
                {displayPeriods.map(p => {
                  const lowN = row.sample_count < MIN_RELIABLE_N;
                  const ret  = lowN ? null : row.avg_return?.[p];
                  const peak = lowN ? null : row.avg_peak?.[p];
                  const prevRet = prev?.avg_return?.[p];
                  const prevPeak = prev?.avg_peak?.[p];
                  return (
                    <React.Fragment key={p}>
                      <td className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] border-l border-white/[0.04] whitespace-nowrap ${returnColor(ret)}`}>
                        {ret != null ? fmtPct(ret) : '—'}
                        <DeltaBadge curr={ret} prev={prevRet} />
                      </td>
                      <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-gray-500 whitespace-nowrap">
                        {peak != null ? fmtPct(peak) : '—'}
                        <DeltaBadge curr={peak} prev={prevPeak} />
                      </td>
                    </React.Fragment>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

function ExcursionTable({ buckets, prevByBucket = {} }) {
  return (
    <div>
      <p className="text-[12px] text-gray-500 mb-3 leading-relaxed">
        MAE = max adverse excursion from entry. MAE Win/Loss = MAE split by trade outcome.
        Cap = capture ratio (avg return ÷ avg MFE). Shakeout = WR(7d) − WR(60d).
      </p>
      <ScrollTable minWidth={640}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.08]">
              <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Bucket</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-12">N</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[68px]">MAE 30d</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">MAE Win</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">MAE Loss</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">MFE 30d</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[56px]">Cap</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">Shakeout</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[60px]">Recovery</th>
            </tr>
          </thead>
          <tbody>
          {buckets.map((row) => {
            const prev = prevByBucket[row.bucket];
            const lowN = row.sample_count < MIN_RELIABLE_N;
            const shakeout = lowN ? null : row.shakeout_depth;
            const prevShakeout = prev?.shakeout_depth;
            const shakeoutColor = shakeout == null ? 'text-gray-600'
              : shakeout < -5 ? 'text-trading-red-400'
              : shakeout < 0  ? 'text-amber-300'
              : 'text-gray-400';
            const cap = lowN ? null : row.capture_ratio?.['30d'];
            const prevCap = prev?.capture_ratio?.['30d'];
            return (
                <tr
                  key={row.bucket}
                  className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-2 px-3"><BucketPill bucket={row.bucket} /></td>
                  <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] whitespace-nowrap">
                    {row.sample_count}
                    <DeltaBadge curr={row.sample_count} prev={prev?.sample_count} decimals={0} suffix="" />
                  </td>
                  <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-trading-red-400 whitespace-nowrap">
                    {!lowN && row.avg_mae?.['30d'] != null ? fmtPct(row.avg_mae['30d']) : '—'}
                    <DeltaBadge curr={!lowN ? row.avg_mae?.['30d'] : null} prev={prev?.avg_mae?.['30d']} />
                  </td>
                  <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-amber-300 whitespace-nowrap">
                    {!lowN && row.avg_mae_winner_30d != null ? fmtPct(row.avg_mae_winner_30d) : '—'}
                    <DeltaBadge curr={!lowN ? row.avg_mae_winner_30d : null} prev={prev?.avg_mae_winner_30d} />
                  </td>
                  <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-trading-red-300 whitespace-nowrap">
                    {!lowN && row.avg_mae_loser_30d != null ? fmtPct(row.avg_mae_loser_30d) : '—'}
                    <DeltaBadge curr={!lowN ? row.avg_mae_loser_30d : null} prev={prev?.avg_mae_loser_30d} />
                  </td>
                  <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-trading-green-400 whitespace-nowrap">
                    {!lowN && row.avg_mfe?.['30d'] != null ? fmtPct(row.avg_mfe['30d']) : '—'}
                    <DeltaBadge curr={!lowN ? row.avg_mfe?.['30d'] : null} prev={prev?.avg_mfe?.['30d']} />
                  </td>
                  <td className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] whitespace-nowrap ${captureColor(cap)}`}>
                    {cap != null ? cap.toFixed(2) : '—'}
                    <DeltaBadge curr={cap} prev={prevCap} decimals={2} suffix="" />
                  </td>
                  <td className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] whitespace-nowrap ${shakeoutColor}`}>
                    {shakeout != null ? fmtPct(shakeout) : '—'}
                    <DeltaBadge curr={shakeout} prev={prevShakeout} />
                  </td>
                  <td className="py-2 px-2 text-right font-mono tabular-nums text-[12px] text-gray-500">
                    {row.shakeout_recovery != null ? `${row.shakeout_recovery}d` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollTable>
    </div>
  );
}

function IcTable({ bandIcs, prevByBand = {} }) {
  if (!bandIcs || bandIcs.length === 0) {
    return <p className="text-gray-500 text-sm">No IC band data available.</p>;
  }
  return (
    <div>
      <p className="text-[12px] text-gray-500 mb-3 leading-relaxed">
        IC = Pearson correlation of score vs return within each non-overlapping score band.
        Positive = higher score within band predicts better outcome. Computed per assessment run, sample-weighted.
      </p>
      <ScrollTable minWidth={480}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.08]">
              <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Band</th>
              <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-12">N</th>
              {IC_PERIODS.map(p => (
                <th key={p} className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[58px]">{p}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bandIcs.map((row) => {
              const prev = prevByBand[row.band];
              return (
                <tr
                  key={row.band}
                  className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-2 px-3 font-mono text-[12px] text-gray-300 font-medium tabular-nums">{row.band}</td>
                  <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] whitespace-nowrap">
                    {row.sample_count}
                    <DeltaBadge curr={row.sample_count} prev={prev?.sample_count} decimals={0} suffix="" />
                  </td>
                  {IC_PERIODS.map(p => {
                    const v = row.ic?.[p];
                    return (
                      <td key={p} className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] whitespace-nowrap ${icColor(v)}`}>
                        {v != null ? (v >= 0 ? `+${v.toFixed(3)}` : v.toFixed(3)) : '—'}
                        <DeltaBadge curr={v} prev={prev?.ic?.[p]} decimals={3} suffix="" />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollTable>
    </div>
  );
}

// ── RTR Win Rates table ───────────────────────────────────────────────────────

function RtrWinRateTable({ buckets, prevByBucket = {} }) {
  const displayPeriods = ASSESSMENT_PERIODS;
  return (
    <ScrollTable minWidth={520}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Bucket</th>
            <th className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-12">N</th>
            {displayPeriods.map(p => (
              <th key={p} className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">{p}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((row) => {
            const prev = prevByBucket[row.bucket];
            return (
              <tr
                key={row.bucket}
                className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
              >
                <td className="py-2 px-3"><BucketPill bucket={row.bucket} /></td>
                <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] whitespace-nowrap">
                  {row.sample_count}
                  <DeltaBadge curr={row.sample_count} prev={prev?.sample_count} decimals={0} suffix="" />
                </td>
                {displayPeriods.map(p => {
                  const v = row.rtr_win_rate?.[p];
                  return (
                    <td key={p} className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] whitespace-nowrap ${rtrColor(v)}`}>
                      {v != null ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—'}
                      <DeltaBadge curr={v} prev={prev?.rtr_win_rate?.[p]} />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

// ── Cross-bucket RTR Pearson table ────────────────────────────────────────────

function RtrPearsonTable({ rtrCorrelations, prevCorrelations = null }) {
  const displayPeriods = ASSESSMENT_PERIODS;
  const sides = [
    { key: 'high', label: 'Calls (HIGH)', sublabel: 'bucket score vs RTR win%', color: 'text-trading-green-300' },
    { key: 'low',  label: 'Puts (LOW)',   sublabel: 'put strength vs RTR win%',  color: 'text-trading-red-300' },
  ];
  return (
    <ScrollTable minWidth={480}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-32">Side</th>
            {displayPeriods.map(p => (
              <th key={p} className="text-right py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 min-w-[64px]">{p}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sides.map(({ key, label, sublabel, color }) => (
            <tr key={key} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
              <td className="py-2 px-3">
                <span className={`text-[12px] font-medium ${color}`}>{label}</span>
                <span className="block text-[11px] text-gray-600 mt-0.5">{sublabel}</span>
              </td>
              {displayPeriods.map(p => {
                const v = rtrCorrelations?.[p]?.[key];
                const pv = prevCorrelations?.[p]?.[key];
                return (
                  <td key={p} className={`py-2 px-2 text-right font-mono tabular-nums text-[12px] whitespace-nowrap ${corrColor(v)}`}>
                    {v != null ? (v >= 0 ? `+${v.toFixed(3)}` : v.toFixed(3)) : '—'}
                    <DeltaBadge curr={v} prev={pv} decimals={3} suffix="" />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollTable>
  );
}

// ── Windows breakdown table ───────────────────────────────────────────────────

function WindowsBreakdownTable({ windows, activeWindows = [], showCalls, showPuts }) {
  const keyPeriods = ['3d', '5d', '7d', '30d', '60d'];
  if (!windows || windows.length === 0) {
    return <p className="text-gray-500 text-sm text-center py-8">No window data available. Run <code className="font-mono text-xs bg-white/[0.04] text-gray-300 px-1 py-0.5 rounded">trader assess --force</code> to generate.</p>;
  }
  const activeWindowByLabel = {};
  (activeWindows || []).forEach(w => { activeWindowByLabel[w.label] = w; });

  const BUCKET_ORDER = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<20', '<15', '<10', '<5'];
  const allBuckets = BUCKET_ORDER.filter(bk => {
    const side = CALL_BUCKETS.has(bk) ? 'call' : 'put';
    if (side === 'call' && !showCalls) return false;
    if (side === 'put'  && !showPuts)  return false;
    return windows.some(w => w.buckets.some(b => b.bucket === bk));
  });

  return (
    <ScrollTable minWidth={500}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08]">
            <th className="text-left py-2 px-3 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 w-20">Bucket</th>
            {windows.map(w => (
              <th key={w.label} colSpan={keyPeriods.length + 1}
                  className="text-center py-2 px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 border-l border-white/[0.06]">
                {w.label}
                <span className="block text-[10px] text-gray-600 font-normal font-mono tabular-nums normal-case tracking-normal">{w.total_peaks.toLocaleString()} peaks</span>
              </th>
            ))}
          </tr>
          <tr className="border-b border-white/[0.08]">
            <th className="py-1 px-3" />
            {windows.map(w => (
              <React.Fragment key={w.label}>
                <th className="py-1 px-2 text-right text-gray-600 text-[10px] font-normal border-l border-white/[0.06] min-w-[28px]">N</th>
                {keyPeriods.map(p => (
                  <th key={p} className="py-1 px-2 text-right text-gray-600 text-[10px] font-normal min-w-[46px]">WR{p}</th>
                ))}
              </React.Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {allBuckets.map((bk) => (
            <tr key={bk} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
              <td className="py-2 px-3"><BucketPill bucket={bk} /></td>
              {windows.map(w => {
                const brow = w.buckets.find(b => b.bucket === bk);
                const activeBrow = activeWindowByLabel[w.label]?.buckets?.find(b => b.bucket === bk);
                return (
                  <React.Fragment key={w.label}>
                    <td className="py-2 px-2 text-right text-gray-500 font-mono tabular-nums text-[11px] border-l border-white/[0.04] whitespace-nowrap">
                      {brow ? brow.sample_count.toLocaleString() : '—'}
                      <DeltaBadge curr={brow?.sample_count} prev={activeBrow?.sample_count} decimals={0} suffix="" />
                    </td>
                    {keyPeriods.map(p => {
                      const vRaw = brow?.win_rate?.[p];
                      const lowN = !brow || brow.sample_count < MIN_RELIABLE_N;
                      const v = lowN ? null : vRaw;
                      const pv = activeBrow?.win_rate?.[p];
                      const glow = winRateGlowStyle(v);
                      return (
                        <td key={p} className="py-2 px-2 text-right align-middle whitespace-nowrap">
                          <span
                            className={`inline-block px-1.5 py-0.5 rounded-md font-mono tabular-nums text-[12px] ${winRateColor(v)}`}
                            style={glow}>
                            {v != null ? `${v.toFixed(1)}%` : '—'}
                          </span>
                          <DeltaBadge curr={v} prev={pv} />
                        </td>
                      );
                    })}
                  </React.Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollTable>
  );
}

// ── Monte Carlo tab ────────────────────────────────────────────────────────
// Surfaces the canonical N-iteration MC run table for the active version
// (or a selected historical version). DD-primary view with a 5y row pinned
// at the top; per-window WorstDD color-coded against the 80% ship floor.
// Data comes from /api/mc/results, populated by monte_carlo.py upserts.

const MC_WINDOW_ORDER = ['5y', '22-now', '2025', 'dip', '2024', '2023', '2022', '2021'];

function fmtRetMC(v) {
  if (v == null || !isFinite(v)) return '—';
  return fmtSignedMagnitude(v, {
    decimals: 1,
    suffix: '%',
    compactSuffix: 'k',
    forcePlusZero: true,
  }) || '—';
}

function fmtMcPct(v, dp = 1) {
  if (v == null || !isFinite(v)) return '—';
  return `${v.toFixed(dp)}%`;
}

function fmtMcDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return '—'; }
}

function ddColor(pct) {
  if (pct == null) return 'text-gray-700';
  if (pct >= 80) return 'text-trading-red-300 font-semibold';      // floor breach
  if (pct >= 75) return 'text-amber-300';
  if (pct >= 70) return 'text-amber-200';
  if (pct >= 60) return 'text-gray-200';
  return 'text-trading-green-300';
}

function collapseColor(pct) {
  if (pct == null) return 'text-gray-700';
  if (pct === 0)   return 'text-trading-green-300';
  if (pct < 5)     return 'text-amber-300';
  return 'text-trading-red-300 font-semibold';
}

function tpColor(pct, be) {
  if (pct == null) return 'text-gray-700';
  if (pct < be)        return 'text-trading-red-300';
  if (pct < be + 5)    return 'text-amber-200';
  if (pct < be + 10)   return 'text-gray-200';
  return 'text-trading-green-300';
}

function MonteCarloTab({ apiBase, dteStrategy = '30', versionId, refreshNonce = 0 }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr]         = useState(null);
  const [nIterMin, setNIterMin] = useState(0);  // 0 = all, 300/500 filters

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setData(null);
    const params = new URLSearchParams();
    params.set('dte', dteStrategy);
    if (versionId) params.set('version', versionId);
    if (nIterMin > 0) params.set('n_iter', String(nIterMin));
    params.set('_t', cacheBustBucket());
    fetch(`${apiBase}/api/mc/results?${params.toString()}`)
      .then(r => r.json().then(d => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (!ok) throw new Error(d.error || 'fetch failed');
        setData(d);
        setLoading(false);
      })
      .catch(e => { setErr(e.message); setLoading(false); });
  }, [apiBase, dteStrategy, versionId, nIterMin, refreshNonce]);

  if (loading) return (
    <div className="flex items-center justify-center py-16 text-gray-500 text-sm gap-2">
      <Activity className="w-4 h-4 animate-spin" /> Loading…
    </div>
  );
  if (err) return (
    <div className="py-10 text-center space-y-2">
      <p className="text-trading-red-400 font-medium">Failed to load MC results</p>
      <p className="text-gray-500 text-sm">{err}</p>
    </div>
  );

  const runs    = data?.runs || [];
  const summary = data?.summary || {};
  const order   = data?.window_order || MC_WINDOW_ORDER;

  // Sort runs by canonical window order, then n_iter desc, then run_at desc
  const orderedRuns = [...runs].sort((a, b) => {
    const ai = order.indexOf(a.window_label);
    const bi = order.indexOf(b.window_label);
    if (ai !== bi) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
    if ((a.n_iter || 0) !== (b.n_iter || 0)) return (b.n_iter || 0) - (a.n_iter || 0);
    return (b.run_at || '').localeCompare(a.run_at || '');
  });

  // Headline 5y summary for the stat card
  const fiveY = summary['5y'];

  // Empty state
  if (orderedRuns.length === 0) {
    return (
      <div className="space-y-4">
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded p-6 space-y-3">
          <h3 className="text-[14px] font-medium text-white">No Monte Carlo data yet for this version</h3>
          <p className="text-[12px] text-gray-400 leading-relaxed">
            MC results are persisted automatically when <span className="font-mono text-gray-300">monte_carlo.py</span> or
            {' '}<span className="font-mono text-gray-300">monte_carlo_15dte.py</span> runs to completion.
            Each window upserts on (version × dte × params_hash) so re-runs replace the prior row.
          </p>
          <div className="bg-black/40 border border-white/[0.04] rounded px-3 py-2 font-mono text-[11px] text-gray-300 space-y-1">
            <div># 30 DTE, full N=500 × 8 windows (canonical ship gate)</div>
            <div className="text-trading-blue-300">PYTHONIOENCODING=utf-8 python -u monte_carlo.py</div>
            <div className="pt-1"># 15 DTE</div>
            <div className="text-trading-blue-300">PYTHONIOENCODING=utf-8 python -u monte_carlo_15dte.py</div>
            <div className="pt-1"># Smoke (N=100, single window)</div>
            <div className="text-trading-blue-300">N_ITER_OVERRIDE=100 WINDOWS_OVERRIDE=22-now python -u monte_carlo.py</div>
          </div>
          <p className="text-[10px] text-gray-600">
            Results land in the <span className="font-mono">monte_carlo_run</span> table.
            Set <span className="font-mono">MC_NO_DB_PERSIST=1</span> on a sweep run to skip persistence.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Headline stat row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded px-4 py-3">
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-600 mb-1">5y WorstDD</p>
          <p className={`text-[16px] font-semibold font-mono tabular-nums ${ddColor(fiveY?.worst_dd_pct)}`}>
            {fiveY?.worst_dd_pct != null ? `${fiveY.worst_dd_pct.toFixed(1)}%` : '—'}
          </p>
          <p className="text-[10px] text-gray-600 mt-0.5">primary ship gate · floor 80%</p>
        </div>
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded px-4 py-3">
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-600 mb-1">5y MedRet</p>
          <p className="text-[16px] font-semibold font-mono tabular-nums text-gray-200">
            {fmtRetMC(fiveY?.med_ret_pct)}
          </p>
          <p className="text-[10px] text-gray-600 mt-0.5">non-regression sanity · ±3 OOMs</p>
        </div>
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded px-4 py-3">
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-600 mb-1">5y P(collapse)</p>
          <p className={`text-[16px] font-semibold font-mono tabular-nums ${collapseColor(fiveY?.collapse_rate_pct)}`}>
            {fmtMcPct(fiveY?.collapse_rate_pct)}
          </p>
          <p className="text-[10px] text-gray-600 mt-0.5">Stage 3 T6 · must be 0%</p>
        </div>
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded px-4 py-3">
          <p className="text-[10px] uppercase tracking-[0.14em] text-gray-600 mb-1">N (5y)</p>
          <p className="text-[16px] font-semibold font-mono tabular-nums text-gray-200">
            {fiveY?.n_iter ?? '—'}
          </p>
          <p className="text-[10px] text-gray-600 mt-0.5">
            {fiveY?.engine_version ?? 'no run'} · {fmtMcDate(fiveY?.run_at)}
          </p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 text-[11px]">
        <span className="text-gray-500 uppercase tracking-[0.14em]">Min N</span>
        <div className="flex items-center border border-white/[0.06] rounded-md overflow-hidden">
          {[
            { k: 0,   lbl: 'All' },
            { k: 100, lbl: '≥100' },
            { k: 300, lbl: '≥300' },
            { k: 500, lbl: '≥500' },
          ].map(({ k, lbl }, i) => (
            <button
              key={k}
              onClick={() => setNIterMin(k)}
              className={`px-2.5 py-1 font-medium transition-colors whitespace-nowrap ${
                i > 0 ? 'border-l border-white/[0.06]' : ''
              } ${nIterMin === k ? 'bg-white/[0.06] text-white' : 'text-gray-500 hover:text-gray-300'}`}
            >
              {lbl}
            </button>
          ))}
        </div>
        <span className="text-gray-600 ml-auto">
          {orderedRuns.length} run{orderedRuns.length === 1 ? '' : 's'}
          {' · '}engine: {orderedRuns[0]?.engine_version || '—'}
        </span>
      </div>

      {/* Per-window MC table */}
      <ScrollTable>
        <table className="w-full text-[12px] tabular-nums font-mono">
          <thead className="bg-white/[0.02] border-b border-white/[0.06]">
            <tr className="text-gray-500 text-[10px] uppercase tracking-[0.14em]">
              <th className="text-left px-3 py-2 font-medium">Window</th>
              <th className="text-right px-2 py-2 font-medium">N</th>
              <th className="text-right px-2 py-2 font-medium" title="Median return across iterations (P50). Robust against lognormal skew.">
                MedRet
              </th>
              <th className="text-right px-2 py-2 font-medium text-gray-700"
                  title="Mean return across iterations. Skewed by best tails on lognormal distribution. Reference only.">
                MeanRet
              </th>
              <th className="text-right px-2 py-2 font-medium text-trading-red-300/80"
                  title="Worst DD across iterations — Stage 3 T4 ship gate. Floor at 80%.">
                WorstDD
              </th>
              <th className="text-right px-2 py-2 font-medium" title="Mean DD across iterations.">MeanDD</th>
              <th className="text-right px-2 py-2 font-medium text-trading-red-300/80"
                  title="P(collapse) — fraction of iterations where portfolio < 20% of starting cash. Stage 3 T6: must be 0%.">
                Coll%
              </th>
              <th className="text-right px-2 py-2 font-medium" title="Mean call TP rate across iterations.">CallTP</th>
              <th className="text-right px-2 py-2 font-medium" title="Mean put TP rate across iterations.">PutTP</th>
              <th className="text-right px-2 py-2 font-medium" title="Mean # of call trades per iteration.">CTrd</th>
              <th className="text-right px-2 py-2 font-medium" title="Mean # of put trades per iteration.">PTrd</th>
              <th className="text-right px-2 py-2 font-medium">Run</th>
            </tr>
          </thead>
          <tbody>
            {orderedRuns.map(r => {
              const isFiveY = r.window_label === '5y';
              const callBE = 46.2;   // current 30 DTE call BE
              const putBE  = 36.4;   // current put BE
              return (
                <tr key={r.id} className={`border-b border-white/[0.03] hover:bg-white/[0.02] ${
                  isFiveY ? 'bg-trading-blue-500/[0.04]' : ''
                }`}>
                  <td className={`px-3 py-2 font-sans ${isFiveY ? 'text-white font-medium' : 'text-gray-300'}`}>
                    {r.window_label}
                    {isFiveY && <span className="ml-1.5 text-[9px] text-trading-blue-300 uppercase tracking-[0.14em]">primary</span>}
                  </td>
                  <td className="text-right px-2 py-2 text-gray-400">{r.n_iter}</td>
                  <td className="text-right px-2 py-2 text-gray-200">{fmtRetMC(r.med_ret_pct)}</td>
                  <td className="text-right px-2 py-2 text-gray-600">{fmtRetMC(r.mean_ret_pct)}</td>
                  <td className={`text-right px-2 py-2 ${ddColor(r.worst_dd_pct)}`}>{fmtMcPct(r.worst_dd_pct)}</td>
                  <td className="text-right px-2 py-2 text-gray-400">{fmtMcPct(r.mean_dd_pct)}</td>
                  <td className={`text-right px-2 py-2 ${collapseColor(r.collapse_rate_pct)}`}>
                    {fmtMcPct(r.collapse_rate_pct)}
                  </td>
                  <td className={`text-right px-2 py-2 ${tpColor(r.call_tp_rate_pct, callBE)}`}>
                    {fmtMcPct(r.call_tp_rate_pct)}
                  </td>
                  <td className={`text-right px-2 py-2 ${tpColor(r.put_tp_rate_pct, putBE)}`}>
                    {fmtMcPct(r.put_tp_rate_pct)}
                  </td>
                  <td className="text-right px-2 py-2 text-gray-400">{r.n_call_trades?.toFixed(0) ?? '—'}</td>
                  <td className="text-right px-2 py-2 text-gray-400">{r.n_put_trades?.toFixed(0) ?? '—'}</td>
                  <td className="text-right px-2 py-2 text-gray-500 font-sans"
                      title={r.run_at ? new Date(r.run_at).toLocaleString() : ''}>
                    {fmtMcDate(r.run_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollTable>

      {/* Methodology footer */}
      <div className="bg-trading-dark-900 border border-white/[0.06] rounded px-4 py-3 space-y-2">
        <p className="text-[11px] text-gray-400 leading-relaxed">
          <span className="text-gray-200 font-medium">How this table is populated:</span>{' '}
          <span className="font-mono">monte_carlo.py</span> and <span className="font-mono">monte_carlo_15dte.py</span> upsert one row per
          {' '}<span className="font-mono">(version × dte × window × params_hash)</span> at the end of each window's iteration loop.
          Re-running the same config replaces the row; changing any param (N, TP/SL, cascade, etc.) produces a new row.
          See <span className="font-mono">database/utils/mc_persist.py</span>.
        </p>
        <p className="text-[11px] text-gray-400 leading-relaxed">
          <span className="text-gray-200 font-medium">Reading the colors:</span>{' '}
          <span className="text-trading-green-300">green</span> = pass / safe ·
          {' '}<span className="text-amber-300">amber</span> = approaching threshold ·
          {' '}<span className="text-trading-red-300">red</span> = floor breach. WorstDD floor = 80% (Stage 3 T4).
          P(collapse) must be 0% (Stage 3 T6). Call TP BE ≈ 46.2%, Put TP BE ≈ 36.4% under current shipped TP/SL.
        </p>
      </div>
    </div>
  );
}


// ── Metric tier guide — reliability ranking, real-world relevance ─────────────
// Collapsible info card surfaced at the top of the Assessment page so users
// understand which metrics drive ship-gate decisions vs which ones are
// theoretical / single-realization. Closes by default; persists open/closed
// state in localStorage so power users can dismiss it permanently.

function MetricTierGuide() {
  const [open, setOpen] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage?.getItem('assessmentMetricGuideOpen') === '1';
  });
  const toggle = () => {
    setOpen(prev => {
      const next = !prev;
      try { window.localStorage?.setItem('assessmentMetricGuideOpen', next ? '1' : '0'); } catch (e) {}
      return next;
    });
  };
  const tiers = [
    {
      name: 'S',
      color: 'text-trading-green-300 border-trading-green-400/40 bg-trading-green-400/[0.06]',
      header: 'Use for ship decisions',
      items: [
        { label: 'MC 5y WorstDD (N≥500)', detail: 'Real DD floor; survives translation to live execution. Stage 3 T4 ship gate.' },
        { label: 'MC P(collapse) per cell',  detail: 'Existential check — must be 0% across all (window × mode). Stage 3 T6 ship gate.' },
        { label: 'Per-trade TP% / WR15 by tier', detail: 'Hard to fake, doesn\'t compound. Found on the Win Rates and TP% tabs. Stage 1 W1 ship gate.' },
      ],
    },
    {
      name: 'A',
      color: 'text-sky-300 border-sky-400/30 bg-sky-400/[0.05]',
      header: 'Essential supporting',
      items: [
        { label: 'MC per-window MedRet (per year)', detail: 'One-year compound is bounded enough to be realizable. Detects regime-dependent failures.' },
        { label: 'MC per-window WorstDD',           detail: 'Stage 3 T5 — catches DD asymmetries the 5y aggregate hides.' },
        { label: 'Per-trade avg_option_pnl (TP%)',  detail: 'Theta-adjusted realized P&L. Catches "TP fires late under heavy decay" cases.' },
        { label: 'Per-tier N stability',            detail: 'A scoring change that "improves" WR by collapsing N is filtering, not alpha.' },
      ],
    },
    {
      name: 'B',
      color: 'text-gray-300 border-white/[0.08] bg-white/[0.02]',
      header: 'Useful, secondary',
      items: [
        { label: 'MC P25 / P75 compound', detail: 'Distribution shape. At long horizons, tails extend past realizable execution cap.' },
        { label: 'Deterministic 1-path WorstDD', detail: 'One realized path. DD survives execution, but single-sample variance is large at this compound scale.' },
        { label: 'MC 22-now compound', detail: 'Confirmation only. N=150 22-now wins reverse at 5y N=300 (Phase OP1 lesson).' },
      ],
    },
    {
      name: 'F',
      color: 'text-trading-red-300 border-trading-red-400/30 bg-trading-red-400/[0.05]',
      header: 'Misleading — don\'t ship on these',
      items: [
        { label: 'Calendar tab "avg return/yr"', detail: 'Single realized path, single realization of MC noise. At 1e15+%/yr it\'s past execution cap.' },
        { label: 'Mean of MC compound paths', detail: 'Lognormal skew — mean dominated by best tails, orders of magnitude above where 90% of paths land.' },
        { label: 'Cross-version compound w/o DD', detail: 'Compound is theoretical above 1e10%. Two versions can show 5× compound differences that are pure path noise.' },
      ],
    },
  ];
  return (
    <div className="bg-trading-dark-900 border border-white/[0.06] rounded-md overflow-hidden">
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-white/[0.02] transition-colors text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[10px] font-medium uppercase tracking-[0.18em] text-gray-400">
            Metric tier guide
          </span>
          <span className="text-[11px] text-gray-600 truncate">
            which numbers actually drive ship decisions vs which are theoretical
          </span>
        </div>
        <ChevronDown className={`w-4 h-4 text-gray-500 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="border-t border-white/[0.06] px-4 py-3 space-y-3">
          <p className="text-[12px] text-gray-400 leading-relaxed">
            Two distinct measurement systems live in this codebase: the per-trade
            barrier-touch assessment (cheap, deterministic, decomposes by tier — what these
            tabs show) and the canonical Monte Carlo at N=500 × 8 windows
            (expensive, distributional — only persisted in <span className="font-mono text-gray-300">version-history.md</span>).
            Both are valid; they answer different questions.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {tiers.map(t => (
              <div key={t.name} className={`border ${t.color} rounded px-3 py-2.5 space-y-1.5`}>
                <div className="flex items-center gap-2">
                  <span className={`text-[12px] font-bold font-mono w-5 text-center`}>{t.name}</span>
                  <span className="text-[11px] font-medium uppercase tracking-[0.14em]">{t.header}</span>
                </div>
                <ul className="space-y-1 pl-1">
                  {t.items.map(it => (
                    <li key={it.label} className="text-[11px] leading-relaxed">
                      <span className="text-gray-200 font-medium">{it.label}</span>
                      <span className="text-gray-500"> — {it.detail}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-gray-600 leading-relaxed pt-1 border-t border-white/[0.04]">
            <span className="text-gray-500">Practical workflow:</span> for day-to-day reading, use Win Rates / TP% per
            tier (S-tier per-trade quality). For ship decisions, run <span className="font-mono text-gray-400">monte_carlo.py</span>
            {' '}at N=500 × 8 windows and read 5y WorstDD + P(collapse) from stdout. The Calendar tab is informational only.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub }) {
  return (
    <div className="bg-trading-dark-900 border border-white/[0.06] rounded-md px-4 py-3 flex items-start gap-3">
      <div className="mt-0.5 flex-shrink-0">
        <Icon className="w-3.5 h-3.5 text-gray-500" strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-600 mb-1">{label}</p>
        <p className="text-[15px] font-semibold text-white font-mono tabular-nums">{value}</p>
        {sub && <p className="text-[11px] text-gray-500 mt-0.5 truncate">{sub}</p>}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const TAB_IDS = TABS.map(t => t.id);

const Assessment = () => {
  const { tab: tabParam } = useParams();
  const navigate = useNavigate();
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  // Take Profit % data (Phase 17, 2026-04-29) — separate fetch so toggling
  // tabs doesn't refetch the WR data. Cached per (dte, versionId) so toggling
  // DTE on the TP% tab swaps instantly without a loading flash.
  const [tpCache, setTpCache]     = useState({});  // { 'dte_versionId': data }
  const [tpLoading, setTpLoading] = useState(false);
  const [tpError, setTpError]     = useState(null);
  const activeTab = TAB_IDS.includes(tabParam) ? tabParam : 'win_rate';
  const setActiveTab = (id) => navigate(id === 'win_rate' ? '/assessment' : `/assessment/${id}`);
  const [showCalls, setShowCalls] = useState(true);
  const [showPuts, setShowPuts]   = useState(true);
  const [showScaled, setShowScaled] = useState(true);
  const [dteStrategy, setDteStrategy] = useState(() => {
    if (typeof window === 'undefined') return '30';
    return window.localStorage?.getItem(DTE_STRATEGY_STORAGE_KEY) === '15' ? '15' : '30';
  });
  const [portfolioProfile, setPortfolioProfile] = useState(() => {
    if (typeof window === 'undefined') return 'sentinel';
    const stored = window.localStorage?.getItem(PORTFOLIO_PROFILE_STORAGE_KEY);
    return ['sentinel', 'core', 'apex'].includes(stored) ? stored : 'sentinel';
  });
  const [portfolioProfiles, setPortfolioProfiles] = useState(DEFAULT_PORTFOLIO_PROFILES);
  const dteCfg = DTE_STRATEGIES[dteStrategy] || DTE_STRATEGIES['30'];
  const setDteStrategyPersist = (val) => {
    setDteStrategy(val);
    try { window.localStorage?.setItem(DTE_STRATEGY_STORAGE_KEY, val); } catch (e) { /* ignore */ }
  };
  // TP% anchor variant — default 'tp' (legacy) so existing users see no UI change.
  const [tpAnchor, setTpAnchor] = useState(() => {
    if (typeof window === 'undefined') return 'tp';
    return window.localStorage?.getItem(TP_ANCHOR_STORAGE_KEY) === 'tp26' ? 'tp26' : 'tp';
  });
  const setTpAnchorPersist = (val) => {
    setTpAnchor(val);
    try { window.localStorage?.setItem(TP_ANCHOR_STORAGE_KEY, val); } catch (e) { /* ignore */ }
  };
  const setPortfolioProfilePersist = (val) => {
    setPortfolioProfile(val);
    try { window.localStorage?.setItem(PORTFOLIO_PROFILE_STORAGE_KEY, val); } catch (e) { /* ignore */ }
  };
  const updatePortfolioProfiles = useCallback((profiles) => {
    setPortfolioProfiles(normalizeProfiles(profiles));
  }, []);
  const [selectedWindow, setSelectedWindow] = useState(null); // set once data loads → widest window
  const [selectedVersionId, setSelectedVersionId] = useState(null);
  const [availableVersions, setAvailableVersions] = useState([]);
  // tpCacheKey depends on selectedVersionId + dteStrategy + tpAnchor — must be
  // declared AFTER all three states are initialized to avoid a temporal-dead-zone
  // error. tpAnchor MUST be in this key: two anchors can be fetched at the same
  // (dte, version) and must not read each other's cached response.
  const tpCacheKey = `${dteStrategy}_${tpAnchor}_${selectedVersionId ?? ''}`;
  const tpData = tpCache[tpCacheKey] || null;
  const [versionMenuOpen, setVersionMenuOpen] = useState(false);
  const [activeData, setActiveData] = useState(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const assessmentUpdatedRef = useRef(null);
  const lastAssessmentResumeRefreshRef = useRef(0);
  const versionMenuRef = useRef(null);

  assessmentUpdatedRef.current = data?.version?.updated_at || null;

  useEffect(() => {
    if (!versionMenuOpen) return;
    const onDocClick = (e) => {
      if (versionMenuRef.current && !versionMenuRef.current.contains(e.target)) {
        setVersionMenuOpen(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') setVersionMenuOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [versionMenuOpen]);

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/portfolio/profiles/compare?_t=${cacheBustBucket()}`)
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!alive || !Array.isArray(j?.rows)) return;
        setPortfolioProfiles(normalizeProfiles(j.rows));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const refreshAssessmentIfStale = useCallback(() => {
    const now = Date.now();
    if (now - lastAssessmentResumeRefreshRef.current < 60000) return;
    if (!shouldRefreshOnResume(assessmentUpdatedRef.current)) return;

    lastAssessmentResumeRefreshRef.current = now;
    setTpCache({});
    setRefreshNonce(n => n + 1);
  }, []);

  useEffect(() => {
    const onResume = () => {
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      refreshAssessmentIfStale();
    };
    document.addEventListener('visibilitychange', onResume);
    window.addEventListener('focus', onResume);
    window.addEventListener('pageshow', onResume);
    return () => {
      document.removeEventListener('visibilitychange', onResume);
      window.removeEventListener('focus', onResume);
      window.removeEventListener('pageshow', onResume);
    };
  }, [refreshAssessmentIfStale]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (selectedVersionId) params.set('version', selectedVersionId);
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('_t', cacheBustBucket());
    const url = `${API_BASE}/api/assessment${params.toString() ? '?' + params.toString() : ''}`;
    fetch(url)
      .then(r => {
        if (!r.ok) return r.json().then(e => {
          if (e.available_versions) setAvailableVersions(e.available_versions);
          throw new Error(e.error || `HTTP ${r.status}`);
        });
        return r.json();
      })
      .then(d => {
        setData(d);
        if (d?.available_versions) setAvailableVersions(d.available_versions);
        if (selectedVersionId == null && d?.version?.id) setSelectedVersionId(d.version.id);
        // Default to 5y window only on first load; preserve the user's selection
        // across DTE toggles and version switches.
        if (d?.windows?.length) {
          setSelectedWindow(prev => {
            if (prev && d.windows.some(w => w.label === prev)) return prev;
            const preferred = d.windows.find(w => w.label === '5y');
            const fallback  = d.windows.reduce(
              (best, w) => (w.lookback_days > (best?.lookback_days ?? -1) ? w : best),
              null,
            );
            return (preferred ?? fallback)?.label ?? null;
          });
        }
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [selectedVersionId, dteStrategy, refreshNonce]);

  // When viewing a historical (non-active) version, fetch active version's
  // full response so each cell can show a delta vs active (incl. per-window).
  useEffect(() => {
    const activeId = data?.active_version_id;
    const currId   = data?.version?.id;
    if (!activeId || !currId || activeId === currId) {
      setActiveData(null);
      return;
    }
    let cancelled = false;
    const params = new URLSearchParams();
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('_t', cacheBustBucket());
    fetch(`${API_BASE}/api/assessment?${params.toString()}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (!cancelled && d) setActiveData(d); })
      .catch(() => { if (!cancelled) setActiveData(null); });
    return () => { cancelled = true; };
  }, [data, dteStrategy, refreshNonce]);

  // Fetch TP% data only when the user actually opens the tab (lazy).
  // Cache by (dte, versionId) so toggling DTE shows cached data immediately
  // (no loading flash) while a refetch confirms in the background.
  useEffect(() => {
    if (activeTab !== 'take_profit') return;
    if (tpCache[tpCacheKey]) return;  // already cached for this combo
    setTpLoading(true);
    setTpError(null);
    const params = new URLSearchParams();
    params.set('metric', tpAnchor);
    if (selectedVersionId) params.set('version', selectedVersionId);
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('_t', cacheBustBucket());
    const keyAtFetch = tpCacheKey;
    fetch(`${API_BASE}/api/assessment?${params.toString()}`)
      .then(r => {
        if (!r.ok) return r.json().then(e => { throw new Error(e.error || `HTTP ${r.status}`); });
        return r.json();
      })
      .then(d => {
        setTpCache(prev => ({ ...prev, [keyAtFetch]: d }));
        setTpLoading(false);
      })
      .catch(e => { setTpError(e.message); setTpLoading(false); });
  }, [activeTab, dteStrategy, tpAnchor, selectedVersionId, tpCacheKey, tpCache]);

  // TP% is a separate metric fetch, so historical TP% needs its own active
  // comparison payload instead of reusing the WR payload in activeData.
  useEffect(() => {
    if (activeTab !== 'take_profit') return;
    const activeId = data?.active_version_id;
    const currId = data?.version?.id;
    if (!activeId || !currId || activeId === currId) return;
    const activeTpKey = `${dteStrategy}_${tpAnchor}_${activeId}`;
    if (tpCache[activeTpKey]) return;

    const params = new URLSearchParams();
    params.set('metric', tpAnchor);
    params.set('version', activeId);
    if (dteStrategy === '15') params.set('dte', '15');
    params.set('_t', cacheBustBucket());
    let cancelled = false;
    fetch(`${API_BASE}/api/assessment?${params.toString()}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!cancelled && d) setTpCache(prev => ({ ...prev, [activeTpKey]: d }));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeTab, dteStrategy, tpAnchor, data?.active_version_id, data?.version?.id, tpCache]);

  // tp26 is 30-DTE-only (no canon values exist for 15 DTE — see PREREG §1.2) — fall
  // back to the legacy anchor rather than fetch a combo that can never be populated.
  useEffect(() => {
    if (dteStrategy === '15' && tpAnchor === 'tp26') setTpAnchorPersist('tp');
  }, [dteStrategy, tpAnchor]);

  const filteredBuckets = useMemo(() => {
    if (!data?.buckets) return [];
    return data.buckets.filter(b => {
      if (!CALL_BUCKETS.has(b.bucket) && !PUT_BUCKETS.has(b.bucket)) return false;
      if (b.side === 'call' && !showCalls) return false;
      if (b.side === 'put'  && !showPuts)  return false;
      return true;
    });
  }, [data, showCalls, showPuts]);

  // Window-selected buckets — used by every tab that respects the window
  // toggle (win_rate, take_profit, returns, excursion, pearson). Falls back
  // to aggregated meta (`filteredBuckets`) when no window is selected or the
  // selected window has no bucket rows. Take Profit % uses tpData windows;
  // every other tab uses `data.windows`.
  const winRateBuckets = useMemo(() => {
    if (!selectedWindow) return filteredBuckets;
    const win = (data?.windows || []).find(w => w.label === selectedWindow);
    if (!win) return filteredBuckets;
    return (win.buckets || []).filter(b => {
      if (!CALL_BUCKETS.has(b.bucket) && !PUT_BUCKETS.has(b.bucket)) return false;
      if (b.side === 'call' && !showCalls) return false;
      if (b.side === 'put'  && !showPuts)  return false;
      return true;
    });
  }, [data, selectedWindow, filteredBuckets, showCalls, showPuts]);

  // Generic window-selected buckets respecting both side toggles.
  // Used by Returns / Excursion / Pearson tabs.
  const selectedWindowBuckets = winRateBuckets;

  // Comparison bucket lookup: shows delta vs the ACTIVE version when a
  // historical version is selected. Empty (no deltas) when viewing active.
  // When a window is selected, compare against the SAME window on active.
  const prevByBucket = useMemo(() => {
    if (!activeData) return {};
    return bucketMapForSelectedWindow(activeData, selectedWindow);
  }, [activeData, selectedWindow]);

  const activeTpData = tpCache[`${dteStrategy}_${data?.active_version_id ?? ''}`] || null;
  const activeTpByBucket = useMemo(() => (
    bucketMapForSelectedWindow(activeTpData, selectedWindow)
  ), [activeTpData, selectedWindow]);

  const activeBandIcByBand = useMemo(() => {
    const map = {};
    (activeData?.band_ics || []).forEach(row => { map[row.band] = row; });
    return map;
  }, [activeData]);

  const selectedRtrCorrelations = useMemo(() => (
    computeRtrCorrelationsFromBuckets(selectedWindowBuckets)
  ), [selectedWindowBuckets]);

  const activeRtrCorrelations = useMemo(() => (
    computeRtrCorrelationsFromBuckets(Object.values(prevByBucket))
  ), [prevByBucket]);

  const versionMenuEl = availableVersions.length > 0 && (
    <>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setVersionMenuOpen(o => !o); }}
        className="absolute inset-0 w-full h-full cursor-pointer"
        aria-label="Select algorithm version"
        aria-haspopup="listbox"
        aria-expanded={versionMenuOpen}
      />
      <ChevronDown className={`w-4 h-4 text-gray-500 flex-shrink-0 transition-transform ${versionMenuOpen ? 'rotate-180' : ''}`} />
      {versionMenuOpen && (
        <div
          role="listbox"
          className="absolute right-0 top-full mt-1 z-20 min-w-[260px] max-h-80 overflow-y-auto bg-trading-dark-900 border border-white/[0.08] rounded-md shadow-xl py-1"
          onClick={(e) => e.stopPropagation()}
        >
          {availableVersions.map(v => {
            const isSelected = v.id === selectedVersionId;
            return (
              <button
                key={v.id}
                role="option"
                aria-selected={isSelected}
                onClick={() => { setSelectedVersionId(v.id); setVersionMenuOpen(false); }}
                className={`w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-white/[0.03] transition-colors ${
                  isSelected ? 'bg-white/[0.04]' : ''
                }`}
              >
                <Check className={`w-3.5 h-3.5 flex-shrink-0 ${isSelected ? 'text-trading-green-300' : 'text-transparent'}`} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-mono text-white truncate tabular-nums">
                    v{v.id}
                    {v.git_commit && (
                      <span className="text-gray-500 ml-1">{v.git_commit.slice(0, 7)}</span>
                    )}
                    {v.created_at && (
                      <span className="text-[11px] text-gray-600 ml-2 font-sans">{fmtVersionDate(v.created_at)}</span>
                    )}
                    {v.is_active && (
                      <span className="text-[10px] uppercase tracking-[0.14em] text-trading-green-300/90 ml-2">active</span>
                    )}
                  </p>
                  {v.git_message && (
                    <p className="text-xs text-gray-500 truncate">{v.git_message}</p>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </>
  );

  // Only show full-page spinner on the very first load (no data yet).
  // Subsequent refetches (e.g. DTE toggle) keep the previous data visible
  // so there's no flash — the values just swap when the new fetch lands.
  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <Activity className="w-5 h-5 animate-spin mr-2" />
        Loading assessment data…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4 gap-3">
        <p className="text-trading-red-400 font-medium">Failed to load assessment</p>
        <p className="text-gray-500 text-sm">{error}</p>
        {availableVersions.length > 0 && (
          <div className="flex flex-col items-center gap-2 mt-2">
            <p className="text-xs text-gray-500">Try a different version:</p>
            <div ref={versionMenuRef} className="relative bg-trading-dark-900 border border-white/[0.08] hover:border-white/[0.14] rounded-md px-4 py-2 text-sm text-white font-mono tabular-nums flex items-center gap-3 transition-colors">
              Select version
              {versionMenuEl}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (!data) return null;

  const { version, buckets, band_ics } = data;
  const totalCallPeaks = buckets.filter(b => b.side === 'call').reduce((s, b) => s + b.sample_count, 0);
  const totalPutPeaks  = buckets.filter(b => b.side === 'put').reduce((s, b) => s + b.sample_count, 0);

  const holdP = dteCfg.holdPeriod;
  const bestCall = buckets.filter(b => b.side === 'call').reduce((best, b) => {
    const wr = b.win_rate?.[holdP];
    if (wr == null) return best;
    return (!best || wr > best.win_rate?.[holdP]) ? b : best;
  }, null);
  const bestPut = buckets.filter(b => b.side === 'put').reduce((best, b) => {
    const wr = b.win_rate?.[holdP];
    if (wr == null) return best;
    return (!best || wr > best.win_rate?.[holdP]) ? b : best;
  }, null);

  return (
    <div className="h-full overflow-y-auto bg-trading-dark-950">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-5">

        {/* Page header */}
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="min-w-0">
            <h1 className="text-[11px] font-medium uppercase tracking-[0.18em] text-gray-500">Assessment</h1>
            <p className="text-[22px] font-semibold text-white tracking-tight mt-1">
              Backtest outcomes
            </p>
            <p className="text-[12px] text-gray-500 mt-1">
              {version.id === data.active_version_id ? 'Active' : 'Selected'} algorithm version
            </p>
          </div>

          {/* DTE strategy toggle — fixed-width buttons + nowrap to prevent any layout shift */}
          <div className="flex items-center bg-trading-dark-900 border border-white/[0.08] rounded-md p-0.5">
            {Object.entries(DTE_STRATEGIES).map(([k, cfg]) => (
              <button
                key={k}
                type="button"
                onClick={() => setDteStrategyPersist(k)}
                className={`px-3 py-1.5 text-[11px] font-medium rounded w-[72px] text-center whitespace-nowrap ${
                  dteStrategy === k
                    ? 'bg-trading-blue-600/20 text-trading-blue-300'
                    : 'text-gray-500'
                }`}
                title={`Show ${cfg.label} strategy anchors (WR${cfg.hardPeriod.replace('d','')} hard sell + WR${cfg.holdPeriod.replace('d','')} end hold)`}
              >
                {cfg.label}
              </button>
            ))}
          </div>

          {/* Version badges */}
          {(() => {
            // availableVersions is sorted by id desc. Find neighbors of current.
            const idx = availableVersions.findIndex(v => v.id === version.id);
            const older = idx >= 0 ? availableVersions[idx + 1] : null;
            const newer = idx > 0 ? availableVersions[idx - 1] : null;
            const navBadge = (v, direction) => (
              <button
                key={direction}
                type="button"
                onClick={() => setSelectedVersionId(v.id)}
                className="bg-trading-dark-900 border border-white/[0.06] hover:border-white/[0.14] rounded-md px-3 py-2 flex items-center gap-3 min-w-0 opacity-60 hover:opacity-100 transition-colors text-left"
                title={`Go to v${v.id}`}
              >
                {direction === 'next' && <ChevronLeft className="w-3.5 h-3.5 text-gray-600 flex-shrink-0" />}
                <div className="min-w-0">
                  <p className="text-[10px] uppercase tracking-[0.14em] text-gray-600">{direction === 'prev' ? 'prev' : 'next'} v{v.id}</p>
                  <p className="text-[12px] font-mono tabular-nums text-gray-400 mt-0.5">
                    {v.git_commit?.slice(0, 7) ?? '—'}
                    {v.created_at && (
                      <span className="text-[11px] text-gray-600 ml-2 font-sans">{fmtVersionDate(v.created_at)}</span>
                    )}
                  </p>
                  {v.git_message && (
                    <p className="text-[11px] text-gray-600 truncate max-w-[180px] mt-0.5">{v.git_message}</p>
                  )}
                </div>
                {direction === 'prev' && <ChevronRight className="w-3.5 h-3.5 text-gray-600 flex-shrink-0" />}
              </button>
            );
            return (
              <div className="flex items-stretch gap-2 flex-wrap justify-end ml-auto">
                {newer && navBadge(newer, 'next')}
                <div ref={versionMenuRef} className="relative bg-trading-dark-900 border border-white/[0.08] hover:border-white/[0.14] rounded-md px-3 py-2 flex items-center gap-3 min-w-0 transition-colors">
                  <GitCommit className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
                  <div className="min-w-0">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-gray-500">
                      {version.id === data.active_version_id ? 'Active version' : 'Historical version'}
                    </p>
                    <p className="text-[12px] font-mono tabular-nums text-white mt-0.5">
                      v{version.id}
                      {version.git_commit && (
                        <span className="text-gray-500 ml-1">{version.git_commit.slice(0, 7)}</span>
                      )}
                      {version.created_at && (
                        <span className="text-[11px] text-gray-500 ml-2 font-sans">{fmtVersionDate(version.created_at)}</span>
                      )}
                    </p>
                    {version.git_message && (
                      <p className="text-[11px] text-gray-500 truncate max-w-[220px] mt-0.5">{version.git_message}</p>
                    )}
                  </div>
                  {versionMenuEl}
                </div>
                {older && navBadge(older, 'prev')}
              </div>
            );
          })()}
        </div>

        {data.active_version_id != null && version.id !== data.active_version_id && (
          <p className="text-[11px] text-amber-300/80 -mt-2 pl-2 border-l-2 border-amber-500/40">
            Viewing a historical version — some metrics may be missing if they weren't computed at that time.
          </p>
        )}

        {/* ── Metric tier-list info — what's reliable vs theoretical ─────── */}
        <MetricTierGuide />

        {/* Stat cards — 2-col on mobile, 4-col on md+
            Lead with per-trade quality (S-tier metric) instead of raw peak counts.
            Cumulative call 75+ and put <25 WR at the strategy's hold period are
            the headline per-trade signals; everything else is supporting. */}
        {(() => {
          // Compute cumulative WR15 (or WR-holdP) for the cascade-relevant tiers.
          // Calls: 75+ is the cascade qualifying universe. Puts: <25 is qualifying.
          const cumCall = buckets.find(b => b.bucket === '75+' && b.side === 'call');
          const cumPut  = buckets.find(b => b.bucket === '<25' && b.side === 'put');
          const callWR  = cumCall?.win_rate?.[holdP];
          const putWR   = cumPut?.win_rate?.[holdP];
          const callN   = cumCall?.sample_count ?? 0;
          const putN    = cumPut?.sample_count ?? 0;
          const wrLabel = `WR${holdP.replace('d','')}`;
          return (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                icon={TrendingUp}
                label={`Call 75+ ${wrLabel}`}
                value={callWR != null ? `${callWR.toFixed(1)}%` : '—'}
                sub={callN
                  ? `${callN.toLocaleString()} signals · best ${bestCall?.bucket} @ ${bestCall?.win_rate?.[holdP]?.toFixed(1)}%`
                  : 'no qualifying signals'}
              />
              <StatCard
                icon={TrendingDown}
                label={`Put <25 ${wrLabel}`}
                value={putWR != null ? `${putWR.toFixed(1)}%` : '—'}
                sub={putN
                  ? `${putN.toLocaleString()} signals · best ${bestPut?.bucket} @ ${bestPut?.win_rate?.[holdP]?.toFixed(1)}%`
                  : 'no qualifying signals'}
              />
              <StatCard
                icon={Activity}
                label="Total peaks"
                value={version.total_peaks?.toLocaleString() ?? '—'}
                sub={`${version.run_count} run${version.run_count !== 1 ? 's' : ''} · ${totalCallPeaks.toLocaleString()} calls / ${totalPutPeaks.toLocaleString()} puts`}
              />
              <StatCard
                icon={Target}
                label="Updated"
                value={version.updated_at
                  ? new Date(version.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                  : '—'}
                sub={version.updated_at
                  ? new Date(version.updated_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
                  : undefined}
              />
            </div>
          );
        })()}

        {/* Main table section */}
        <div className="bg-trading-dark-900 border border-white/[0.06] rounded-md overflow-hidden">

          {/* Toolbar — tabs on top, filters below on mobile; single row on sm+ */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 px-4 py-3 border-b border-white/[0.06]">
            {/* Tabs — underlined bar style, no pill chrome */}
            <div className="overflow-x-auto -mx-1 px-1">
              <div className="flex items-center gap-4 w-max">
                {TABS.map(tab => {
                  const active = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={`relative py-1.5 text-[12px] transition-colors whitespace-nowrap ${
                        active
                          ? 'text-white font-medium'
                          : 'text-gray-500 hover:text-gray-300 font-normal'
                      }`}
                    >
                      {tab.label}
                      {active && (
                        <span className="absolute left-0 right-0 -bottom-[13px] h-[1px] bg-white" aria-hidden />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Side filters + win rate controls */}
            <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
              {/* Window selector — visible on every tab that consumes per-window
                  bucket data. Hidden on tabs that show all windows at once
                  (`windows`) or use a separate dataset (`calendar`, `ic`). */}
              {(data?.windows?.length > 0)
                && !['windows', 'calendar', 'ic', 'monte_carlo'].includes(activeTab) && (
                <div className="flex items-center border border-white/[0.06] rounded-md overflow-hidden">
                  {data.windows.map((w, i) => (
                    <button
                      key={w.label}
                      onClick={() => setSelectedWindow(w.label)}
                      className={`px-2.5 py-1 text-[11px] font-medium transition-colors whitespace-nowrap ${
                        i > 0 ? 'border-l border-white/[0.06]' : ''
                      } ${
                        w.label === selectedWindow
                          ? 'bg-white/[0.06] text-white'
                          : 'text-gray-500 hover:text-gray-300'
                      }`}
                    >
                      {w.label}
                    </button>
                  ))}
                </div>
              )}
              {/* Scaled / Unscaled toggle — only meaningful for win_rate */}
              {activeTab === 'win_rate' && (
                <button
                  onClick={() => setShowScaled(v => !v)}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                    showScaled
                      ? 'border-white/[0.12] text-gray-200'
                      : 'border-amber-500/30 text-amber-300'
                  }`}
                >
                  {showScaled ? 'Scaled' : 'Unscaled'}
                </button>
              )}
              <button
                onClick={() => setShowCalls(v => !v)}
                className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                  showCalls
                    ? 'border-white/[0.12] text-gray-200'
                    : 'border-white/[0.04] text-gray-600 hover:text-gray-400'
                }`}
              >
                <span className={`w-1 h-1 rounded-full ${showCalls ? 'bg-trading-green-400' : 'bg-gray-700'}`} />
                Calls
                {showCalls && <span className="absolute left-0 right-0 bottom-0 h-[2px] bg-trading-green-400/80" aria-hidden />}
              </button>
              <button
                onClick={() => setShowPuts(v => !v)}
                className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium border transition-colors ${
                  showPuts
                    ? 'border-white/[0.12] text-gray-200'
                    : 'border-white/[0.04] text-gray-600 hover:text-gray-400'
                }`}
              >
                <span className={`w-1 h-1 rounded-full ${showPuts ? 'bg-trading-red-400' : 'bg-gray-700'}`} />
                Puts
                {showPuts && <span className="absolute left-0 right-0 bottom-0 h-[2px] bg-trading-red-400/80" aria-hidden />}
              </button>
            </div>
          </div>

          {/* Table content */}
          <div className="p-4">
            {activeTab === 'win_rate' && (
              <>
                <SectionHeader>
                  Win Rate by bucket × period{selectedWindow ? ` — ${selectedWindow} window` : ''}
                </SectionHeader>
                <div className="mb-4 space-y-2">
                  <p className="text-[12px] text-gray-400 leading-relaxed">
                    Win rates use <span className="text-gray-200 font-medium">volatility-adjusted barriers</span> — each trade is measured against targets scaled to that stock's own 60-day realized volatility (σ).
                    Toggle between <span className="text-gray-200 font-medium">Scaled</span> and <span className="text-amber-300 font-medium">Unscaled</span> using the button in the toolbar.
                  </p>
                  <div className="bg-trading-dark-900 border border-white/[0.06] rounded-md px-4 py-3 font-mono text-[11px] text-gray-400 space-y-2 tabular-nums">
                    {showScaled ? (
                      <>
                        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-300 font-sans">Scaled — barrier grows with window: K·σ·√(W/30)</div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-green-300">Calls</span>
                          <span>win if high ≥ <span className="text-gray-100">entry × (1 + 2σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if low ≤ <span className="text-gray-400">entry × (1 − 5σ·√(W/30))</span></span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-red-300">Puts</span>
                          <span>win if low ≤ <span className="text-gray-100">entry × (1 − 1σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if high ≥ <span className="text-gray-400">entry × (1 + 2σ·√(W/30))</span></span>
                        </div>
                        <div className="text-gray-500 pt-1 border-t border-white/[0.06] leading-relaxed">
                          <span className="text-gray-400">Scale √(W/30):</span>
                          {' '}3d ≈ 0.32×{' · '}5d ≈ 0.41×{' · '}7d ≈ 0.48×{' · '}15d ≈ 0.71×{' · '}30d = 1.0× (anchor){' · '}60d ≈ 1.41×{' · '}90d ≈ 1.73×
                          <br />1d = direction only (no barrier)
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-amber-300 font-sans">Unscaled — fixed 30d barrier for all windows: K·σ</div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-green-300">Calls</span>
                          <span>win if high ≥ <span className="text-gray-100">entry × (1 + 2σ)</span></span>
                          <span className="text-gray-600">stop if low ≤ <span className="text-gray-400">entry × (1 − 5σ)</span></span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-red-300">Puts</span>
                          <span>win if low ≤ <span className="text-gray-100">entry × (1 − 1σ)</span></span>
                          <span className="text-gray-600">stop if high ≥ <span className="text-gray-400">entry × (1 + 2σ)</span></span>
                        </div>
                        <div className="text-gray-500 pt-1 border-t border-white/[0.06] leading-relaxed">
                          Same absolute target at every window — shorter periods are harder to reach, longer periods are easier.
                          <br />1d = direction only (no barrier)
                        </div>
                      </>
                    )}
                  </div>
                </div>
                {winRateBuckets.length === 0
                  ? <p className="text-gray-500 text-sm text-center py-8">No buckets to display.</p>
                  : <WinRateTable buckets={winRateBuckets} useUnscaled={!showScaled}
                      prevByBucket={prevByBucket} primaryPeriods={dteCfg.primaries} />
                }
              </>
            )}

            {activeTab === 'take_profit' && (
              <>
                <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                  <SectionHeader>
                    Take Profit % by bucket × period — {dteCfg.label}{selectedWindow ? ` · ${selectedWindow} window` : ' · all data'}
                  </SectionHeader>
                  {/* Anchor toggle (2026-08-10 dual-anchor reanchor) — legacy vs live canon */}
                  <div className="flex items-center bg-trading-dark-900 border border-white/[0.08] rounded-md p-0.5">
                    {Object.entries(TP_ANCHORS).map(([k, cfg]) => {
                      const isDisabled = k === 'tp26' && dteStrategy === '15';
                      return (
                        <button
                          key={k}
                          type="button"
                          disabled={isDisabled}
                          onClick={() => setTpAnchorPersist(k)}
                          className={`px-3 py-1.5 text-[11px] font-medium rounded whitespace-nowrap ${
                            isDisabled
                              ? 'text-gray-700 cursor-not-allowed'
                              : tpAnchor === k
                                ? 'bg-trading-blue-600/20 text-trading-blue-300'
                                : 'text-gray-500'
                          }`}
                          title={isDisabled ? '2026 Canon is 30 DTE only — no canon values exist for 15 DTE' : cfg.title}
                        >
                          {cfg.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="mb-4 space-y-3">
                  <div className="p-3 bg-trading-blue-500/[0.04] border border-trading-blue-500/20 rounded text-[12px] text-gray-400 leading-relaxed">
                    <div className="text-trading-blue-300 font-medium mb-1">What this measures</div>
                    Probability the option <span className="text-gray-200 font-medium">hits TP before SL</span> within W days,
                    using the assessment&apos;s <span className="text-gray-200 font-medium">fixed option-anchor barriers</span>{' '}
                    (<span className="text-trading-blue-300 font-medium">{dteStrategy === '15' ? '15 DTE C1' : tpAnchor === 'tp26' ? '30 DTE 2026 canon' : '30 DTE H5'}</span> era:{' '}
                    {dteStrategy === '15' ? 'TP=+35%/SL=−30%' : tpAnchor === 'tp26' ? 'TP=+10%/SL=−100%' : 'TP=+35%/SL=−30%'} calls, TP=+35%/SL=−20% puts on premium).
                    {tpAnchor === 'tp26' ? (
                      <span className="text-trading-green-300/80"> This anchor matches the live 2026-08-10 Core/Apex canon —
                      puts are shown for reference only (unchanged, still off portfolio-wide). Switch to{' '}
                      <span className="text-gray-300">Legacy</span> for cross-version comparability (it feeds the W1-W6 ship
                      gates and is kept byte-identical).</span>
                    ) : (
                      <span className="text-amber-300/80"> These anchors are deliberately frozen for cross-version comparability —
                      they are a measurement instrument, not the live strategy (which moved to TP+10% calls on 2026-08-10;
                      switch to <span className="text-gray-300">2026 Canon</span> above to measure at the live barriers).</span>
                    )}
                    <br />
                    <span className="text-gray-500">
                      Win Rate uses generic K=2σ/M=5σ directional barriers (does the underlying move enough?);
                      TP% uses tighter option-aligned barriers (would the option have actually profited?).
                      Same peaks, two different questions. Cells are most accurate at the highlighted period
                      ({dteStrategy === '15' ? 'WR15' : 'WR30'}); shorter periods slightly under-state realized
                      option gain because the √-scaled barrier-touch model ignores theta decay.
                    </span>
                  </div>
                  <div className="bg-trading-dark-900 border border-white/[0.06] rounded-md px-4 py-3 font-mono text-[11px] text-gray-400 space-y-2 tabular-nums min-h-[360px]">
                    {dteStrategy === '15' ? (
                      <>
                        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-300 font-sans">
                          15 DTE C1 assessment anchor <span className="text-gray-500 normal-case tracking-normal">(Phase 15B era, frozen — not the live strategy)</span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-green-300">Calls — TP +35% / SL −30% on premium</span>
                          <span>win if high ≥ <span className="text-gray-100">entry × (1 + 0.903σ·√(W/15))</span></span>
                          <span className="text-gray-600">stop if low ≤ <span className="text-gray-400">entry × (1 − 0.774σ·√(W/15))</span></span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-red-300">Puts — TP +35% / SL −20% on premium</span>
                          <span>win if low ≤ <span className="text-gray-100">entry × (1 − 0.903σ·√(W/15))</span></span>
                          <span className="text-gray-600">stop if high ≥ <span className="text-gray-400">entry × (1 + 0.516σ·√(W/15))</span></span>
                        </div>
                        <div className="text-gray-500 pt-1 border-t border-white/[0.06] leading-relaxed">
                          σ targets derive from <span className="text-gray-400">TP × PREMIUM_MULT / Δ</span> with PREMIUM_MULT = 1.29 (15 DTE ATM ≈ 1.29×σ_daily) and Δ = 0.5.
                          Hold = 7 trading bars; no portfolio drawdown entry halt is applied.
                          <br />Anchor ref = 15 cal days. Scale √(W/15): 3d ≈ 0.45×{' · '}5d ≈ 0.58×{' · '}7d ≈ 0.68×{' · '}15d = 1.0× (anchor){' · '}30d ≈ 1.41×.
                          <br />1d = direction only (no barrier).
                        </div>
                      </>
                    ) : tpAnchor === 'tp26' ? (
                      <>
                        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-300 font-sans">
                          30 DTE 2026 canon assessment anchor <span className="text-gray-500 normal-case tracking-normal">(2026-08-10 TP/SL retune era — matches the live Core/Apex strategy)</span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-green-300">Calls — TP_BASE +10% / SL_BASE −100% on premium</span>
                          <span>win if high ≥ <span className="text-gray-100">entry × (1 + 0.364σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if low ≤ <span className="text-gray-400">entry × (1 − 3.64σ·√(W/30))</span></span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-red-300">Puts — TP +35% / SL −20% on premium <span className="text-gray-600 normal-case">(unchanged — not retuned, off portfolio-wide)</span></span>
                          <span>win if low ≤ <span className="text-gray-100">entry × (1 − 1.274σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if high ≥ <span className="text-gray-400">entry × (1 + 0.728σ·√(W/30))</span></span>
                        </div>
                        <div className="text-gray-500 pt-1 border-t border-white/[0.06] leading-relaxed">
                          σ targets derive from <span className="text-gray-400">TP × PREMIUM_MULT / Δ</span> with PREMIUM_MULT = 1.82 (30 DTE ATM ≈ 1.82×σ_daily) and Δ = 0.5.
                          Hold = 15 trading bars; base==stress (breadth-conditional TP/SL tested + rejected at N=500, 2026-08-10 — see tpsl_refine_2026_08).
                          <br />Anchor ref = 30 cal days. Scale √(W/30): 3d ≈ 0.32×{' · '}5d ≈ 0.41×{' · '}7d ≈ 0.48×{' · '}15d ≈ 0.71×{' · '}30d = 1.0× (anchor){' · '}60d ≈ 1.41×.
                          <br />1d = direction only (no barrier).
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-300 font-sans">
                          30 DTE H5 assessment anchor <span className="text-gray-500 normal-case tracking-normal">(Phase H5_HOLD15_H40 era, frozen — not the live strategy; live = TP+10/SL−100 since 2026-08-10)</span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-green-300">Calls — TP_BASE +35% / SL_BASE −30% on premium</span>
                          <span>win if high ≥ <span className="text-gray-100">entry × (1 + 1.274σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if low ≤ <span className="text-gray-400">entry × (1 − 1.092σ·√(W/30))</span></span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-trading-red-300">Puts — TP +35% / SL −20% on premium</span>
                          <span>win if low ≤ <span className="text-gray-100">entry × (1 − 1.274σ·√(W/30))</span></span>
                          <span className="text-gray-600">stop if high ≥ <span className="text-gray-400">entry × (1 + 0.728σ·√(W/30))</span></span>
                        </div>
                        <div className="text-gray-500 pt-1 border-t border-white/[0.06] leading-relaxed">
                          σ targets derive from <span className="text-gray-400">TP × PREMIUM_MULT / Δ</span> with PREMIUM_MULT = 1.82 (30 DTE ATM ≈ 1.82×σ_daily) and Δ = 0.5.
                          Hold = 15 trading bars; breadth-adaptive widens to TP +42% / SL −40% when breadth_score ≤ 40 (30 DTE post-v32_optim; 15 DTE: +40%/−35% @ ≤50). BASE shown here.
                          <br />Anchor ref = 30 cal days. Scale √(W/30): 3d ≈ 0.32×{' · '}5d ≈ 0.41×{' · '}7d ≈ 0.48×{' · '}15d ≈ 0.71×{' · '}30d = 1.0× (anchor){' · '}60d ≈ 1.41×.
                          <br />1d = direction only (no barrier).
                        </div>
                      </>
                    )}
                    <div className="pt-1 border-t border-white/[0.06]">
                      <span className="text-gray-400">Break-even (with realistic per-exit slippage):</span>{' '}
                      <span className="text-trading-green-300">calls {TP_CALL_BE}%</span>{' · '}
                      <span className="text-trading-red-300">puts {TP_PUT_BE}%</span>.
                      Cell color = margin above the side's BE.
                    </div>
                  </div>
                </div>
                {tpLoading && !tpData
                  ? <p className="text-gray-500 text-sm text-center py-8">Loading…</p>
                  : tpError && !tpData
                    ? <p className="text-trading-red-400 text-sm text-center py-8 whitespace-pre-line">{tpError}</p>
                    : !tpData?.buckets?.length
                      ? <p className="text-gray-500 text-sm text-center py-8">No TP% data yet — run <span className="font-mono text-gray-400">trader assess --force</span>.</p>
                      : <WinRateTable
                          buckets={(() => {
                            const win = (tpData?.windows || []).find(w => w.label === selectedWindow);
                            const src = win?.buckets || tpData.buckets || [];
                            return src.filter(b => {
                              if (!CALL_BUCKETS.has(b.bucket) && !PUT_BUCKETS.has(b.bucket)) return false;
                              if (b.side === 'call' && !showCalls) return false;
                              if (b.side === 'put'  && !showPuts)  return false;
                              return true;
                            });
                          })()}
                          primaryPeriods={dteCfg.primaries}
                          prevByBucket={activeTpByBucket}
                          colorFn={tpRateColor}
                          glowFn={(v, side) => {
                            // Mirror winRateGlowStyle but anchor the glow ramp to a high TP%
                            // (≥ BE + 30pp). Visually tells you when a bucket is exceptional.
                            if (v == null) return undefined;
                            const be = side === 'put' ? TP_PUT_BE : TP_CALL_BE;
                            const trigger = be + 30;
                            if (v < trigger) return undefined;
                            const ex = Math.min(1, (v - trigger) / 10);
                            const [r, g, b] = WINRATE_GLOW_RGB;
                            const k = (mult) => (ex * mult).toFixed(3);
                            return {
                              boxShadow: [
                                `0 0 0 1px rgba(${r},${g},${b},${k(0.3)})`,
                                `inset 0 1px 0 0 rgba(${r},${g},${b},${k(0.1)})`,
                                `0 0 6px rgba(${r},${g},${b},${k(0.48)})`,
                                `0 0 14px rgba(${r},${g},${b},${k(0.3)})`,
                                `0 0 26px rgba(${r},${g},${b},${k(0.16)})`,
                              ].join(', '),
                            };
                          }}
                        />
                }
              </>
            )}

            {activeTab === 'windows' && (
              <>
                <SectionHeader>Win rates by assessment window (WR7d / WR30d / WR60d)</SectionHeader>
                <p className="text-[12px] text-gray-500 mb-4 leading-relaxed">
                  Each column group shows results for that lookback window. Wider windows include more history but may reflect older market regimes. WR = scaled barrier-touch win rate.
                </p>
                <WindowsBreakdownTable
                  windows={data.windows}
                  activeWindows={activeData?.windows || []}
                  showCalls={showCalls}
                  showPuts={showPuts}
                />
              </>
            )}

            {activeTab === 'returns' && (
              <>
                <SectionHeader>
                  Side-adjusted EV (exit return) and MFE per period
                  {selectedWindow ? ` — ${selectedWindow} window` : ''}
                </SectionHeader>
                <p className="text-[12px] text-gray-500 mb-3 leading-relaxed">
                  Return = avg close-to-exit % (direction-adjusted: positive = trade worked). MFE = avg max favorable excursion within the window.
                </p>
                {selectedWindowBuckets.length === 0
                  ? <p className="text-gray-500 text-sm text-center py-8">No buckets to display.</p>
                  : <ReturnsTable buckets={selectedWindowBuckets} prevByBucket={prevByBucket} />
                }
              </>
            )}

            {activeTab === 'excursion' && (
              <>
                <SectionHeader>
                  Excursion profile at 30d horizon
                  {selectedWindow ? ` — ${selectedWindow} window` : ''}
                </SectionHeader>
                {selectedWindowBuckets.length === 0
                  ? <p className="text-gray-500 text-sm text-center py-8">No buckets to display.</p>
                  : <ExcursionTable buckets={selectedWindowBuckets} prevByBucket={prevByBucket} />
                }
              </>
            )}

            {activeTab === 'ic' && (
              <>
                <SectionHeader>Intra-band information coefficient</SectionHeader>
                <IcTable bandIcs={band_ics} prevByBand={activeBandIcByBand} />
              </>
            )}

            {activeTab === 'calendar' && (
              <>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <SectionHeader>Backtest calendar — cascade allocation strategy</SectionHeader>
                  <PortfolioProfileToggle
                    profiles={portfolioProfiles}
                    value={portfolioProfile}
                    onChange={setPortfolioProfilePersist}
                    compact
                  />
                </div>
                <CalendarTab
                  apiBase={API_BASE}
                  versionId={selectedVersionId}
                  activeVersionId={data?.active_version_id}
                  dteStrategy={dteStrategy}
                  portfolioProfile={portfolioProfile}
                  onProfiles={updatePortfolioProfiles}
                  refreshNonce={refreshNonce}
                />
              </>
            )}

            {activeTab === 'monte_carlo' && (
              <>
                <SectionHeader>
                  Monte Carlo — N-iteration distribution per window — {dteCfg.label}
                </SectionHeader>
                <p className="text-[12px] text-gray-500 mb-4 leading-relaxed">
                  Canonical ship-gate metrics. Populated automatically when{' '}
                  <span className="font-mono text-gray-300">monte_carlo.py</span> /{' '}
                  <span className="font-mono text-gray-300">monte_carlo_15dte.py</span> run to completion. Each row is one
                  upsert per (version × dte × window × params_hash) — re-running the same config replaces the row.
                </p>
                <MonteCarloTab
                  apiBase={API_BASE}
                  dteStrategy={dteStrategy}
                  versionId={selectedVersionId}
                  refreshNonce={refreshNonce}
                />
              </>
            )}

            {activeTab === 'pearson' && (
              <>
                <SectionHeader>
                  RTR win rates — relative to 71% random baseline
                  {selectedWindow ? ` — ${selectedWindow} window` : ''}
                </SectionHeader>
                <p className="text-[12px] text-gray-400 mb-4 leading-relaxed">
                  RTR win% = <span className="font-mono text-gray-200 tabular-nums">(win% − floor) / (100% − floor)</span>.
                  Zero = matches a random walk. Positive = beating random. Negative = worse than random.
                  Floor is side-specific — derived from gambler's ruin <span className="font-mono text-gray-300 tabular-nums">P = M/(K+M)</span>:{' '}
                  <span className="text-trading-green-300 font-mono tabular-nums">calls 71.4%</span> (K=2σ/M=5σ),{' '}
                  <span className="text-trading-red-300 font-mono tabular-nums">puts 66.7%</span> (K=1σ/M=2σ).
                </p>
                {selectedWindowBuckets.length === 0
                  ? <p className="text-gray-500 text-sm text-center py-8">No buckets to display.</p>
                  : <RtrWinRateTable buckets={selectedWindowBuckets} prevByBucket={prevByBucket} />
                }

                <div className="mt-6">
                  <SectionHeader>Cross-bucket RTR Pearson</SectionHeader>
                  <p className="text-[12px] text-gray-400 mb-4 leading-relaxed">
                    Pearson correlation of <span className="text-gray-200">bucket threshold score</span> vs <span className="text-gray-200">RTR win%</span> across the 7 call buckets and 3 put buckets.
                    Computed from existing stored data — no reassessment needed.
                    Positive = higher-scored buckets beat random more than lower-scored ones.
                    More separable than per-trade Pearson because trade-level noise is averaged out before correlating.
                  </p>
                  <RtrPearsonTable
                    rtrCorrelations={selectedRtrCorrelations || version.rtr_correlations}
                    prevCorrelations={activeRtrCorrelations}
                  />
                </div>

              </>
            )}

          </div>
        </div>

      </div>
    </div>
  );
};

export default Assessment;
