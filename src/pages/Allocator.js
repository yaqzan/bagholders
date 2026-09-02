import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import {
  AlertTriangle,
  Calculator,
  CircleDollarSign,
  Clock,
  RefreshCw,
  Target,
  Wallet,
} from 'lucide-react';
import ScoreVersionSelector from '../components/ScoreVersionSelector';
import PortfolioProfileToggle, { DEFAULT_PORTFOLIO_PROFILES, normalizeProfiles } from '../components/PortfolioProfileToggle';

ChartJS.register(ArcElement, Tooltip, Legend);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';
const PORTFOLIO_PROFILE_STORAGE_KEY = 'allocatorPortfolioProfile20260523';

// pie slices: size gradient (heaviest = dark, lightest = pale) — green calls / red puts; cash = slate.
const CASH_COLOR = '#334155';
const GREEN_DARK = '#065f46';
const GREEN_LIGHT = '#bbf7d0';
const RED_DARK = '#7f1d1d';
const RED_LIGHT = '#fecaca';

function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function rgbToHex(r, g, b) { const c = (x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, '0'); return `#${c(r)}${c(g)}${c(b)}`; }
function lerpHex(a, b, t) { const A = hexToRgb(a); const B = hexToRgb(b); return rgbToHex(A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t); }
function sizeShade(side, t) { return side === 'put' ? lerpHex(RED_DARK, RED_LIGHT, t) : lerpHex(GREEN_DARK, GREEN_LIGHT, t); }

function num(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value);
}

function fmtMoney(amount, currency = 'USD', digits) {
  const n = num(amount);
  if (n === null) return '-';
  return n.toLocaleString(undefined, {
    style: 'currency',
    currency,
    maximumFractionDigits: digits != null ? digits : (n >= 1000 ? 0 : 2),
  });
}

function fmtPct(value, digits = 1) {
  const n = num(value);
  if (n === null) return '-';
  return `${n.toFixed(digits)}%`;
}

function fmtNumber(value, digits = 0) {
  const n = num(value);
  if (n === null) return '-';
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtDate(value) {
  if (!value) return '-';
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function SummaryMetric({ label, value, sub, icon, tone = 'neutral' }) {
  const toneClass = tone === 'green' ? 'text-trading-green-300' : tone === 'red' ? 'text-trading-red-300' : 'text-gray-100';
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">{label}</div>
        <div className="text-gray-600">{icon}</div>
      </div>
      <div className={`mt-2 font-mono text-xl font-semibold ${toneClass}`}>{value}</div>
      {sub && <div className="mt-1 text-xs leading-5 text-gray-500">{sub}</div>}
    </div>
  );
}

function SegmentButton({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border px-3 py-2 text-sm transition-colors ${
        active
          ? 'border-trading-green-300/50 bg-trading-green-300/10 text-trading-green-200'
          : 'border-white/[0.08] bg-white/[0.02] text-gray-400 hover:bg-white/[0.05] hover:text-gray-200'
      }`}
    >
      {children}
    </button>
  );
}

function DteBadge({ row }) {
  const routed = row.routed_15dte;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] ${
        routed
          ? 'border-trading-blue-400/40 bg-trading-blue-500/10 text-trading-blue-300'
          : 'border-white/[0.1] bg-white/[0.03] text-gray-300'
      }`}
      title={routed ? 'Routed to 15 DTE (bull-tape sleeve)' : '30 DTE base'}
    >
      {row.dte_label || '30 DTE'}
      {routed && <span className="text-[9px] uppercase tracking-wide opacity-80">router</span>}
    </span>
  );
}

/* The pie: one slice per filled position (by USD), plus a Cash slice. */
function AllocationPie({ calls, puts, cashUsd }) {
  const positions = [
    ...calls.map((r) => ({ label: r.symbol, usd: num(r.allocation?.usd) || 0, side: 'call' })),
    ...puts.map((r) => ({ label: `${r.symbol} (put)`, usd: num(r.allocation?.usd) || 0, side: 'put' })),
  ]
    .filter((p) => p.usd > 0)
    .sort((a, b) => b.usd - a.usd)
    .map((p, i, arr) => ({ ...p, color: sizeShade(p.side, arr.length > 1 ? i / (arr.length - 1) : 0) }));
  const cash = Math.max(0, num(cashUsd) || 0);
  const labels = [...positions.map((p) => p.label), 'Cash'];
  const values = [...positions.map((p) => p.usd), cash];
  const colors = [...positions.map((p) => p.color), CASH_COLOR];
  const total = values.reduce((a, b) => a + b, 0) || 1;

  const chartData = {
    labels,
    datasets: [{ data: values, backgroundColor: colors, borderColor: '#0b0f17', borderWidth: 2, hoverOffset: 6 }],
  };
  const options = {
    cutout: '62%',
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed || 0;
            return ` ${ctx.label}: ${fmtMoney(v, 'USD')} (${((v / total) * 100).toFixed(1)}%)`;
          },
        },
      },
    },
    maintainAspectRatio: false,
  };
  const deployed = total - cash;
  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Allocation</div>
      {positions.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-500">No positions to allocate</div>
      ) : (
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
          <div className="relative h-44 w-44 shrink-0">
            <Doughnut data={chartData} options={options} />
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <div className="font-mono text-lg font-semibold text-trading-green-300">{((deployed / total) * 100).toFixed(0)}%</div>
              <div className="text-[10px] uppercase tracking-wide text-gray-500">deployed</div>
            </div>
          </div>
          <div className="grid w-full grid-cols-1 gap-1 sm:max-h-44 sm:overflow-y-auto">
            {positions.map((p) => (
              <div key={p.label} className="flex items-center justify-between gap-2 text-xs">
                <span className="flex items-center gap-2 truncate">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: p.color }} />
                  <span className="truncate font-mono text-gray-200">{p.label}</span>
                </span>
                <span className="font-mono text-gray-400">{((p.usd / total) * 100).toFixed(1)}%</span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-2 border-t border-white/[0.06] pt-1 text-xs">
              <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: CASH_COLOR }} /><span className="text-gray-400">Cash</span></span>
              <span className="font-mono text-gray-500">{((cash / total) * 100).toFixed(1)}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* The "what to buy" list — the primary deliverable. */
function BuyList({ title, rows, side, currency }) {
  const accent = side === 'put' ? 'text-trading-red-300' : 'text-trading-green-300';
  return (
    <section className="overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02]">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <div className={`text-[11px] font-semibold uppercase tracking-[0.16em] ${accent}`}>{title}</div>
        <div className="text-xs text-gray-500">{rows.length} {rows.length === 1 ? 'position' : 'positions'}</div>
      </div>
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-white/[0.06] bg-trading-dark-900/60 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
                <th className="px-4 py-2.5 text-left">Symbol</th>
                <th className="px-3 py-2.5 text-left">DTE</th>
                <th className="px-3 py-2.5 text-right">Score</th>
                <th className="px-4 py-2.5 text-right">Allocation</th>
                <th className="px-4 py-2.5 text-right">Exit (TP / SL)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.side}-${row.symbol}`} className="border-b border-white/[0.04] hover:bg-white/[0.035]">
                  <td className="px-4 py-3">
                    <Link to={`/stock/${row.symbol}`} className="font-mono font-semibold text-gray-100 transition-colors hover:text-trading-blue-400">
                      {row.symbol}
                    </Link>
                    {row.ct_promoted && <span className="ml-2 rounded bg-trading-blue-500/10 px-1 text-[9px] uppercase text-trading-blue-300">CT</span>}
                  </td>
                  <td className="px-3 py-3"><DteBadge row={row} /></td>
                  <td className="px-3 py-3 text-right">
                    <span className={`inline-flex min-w-[2.75rem] justify-center rounded border px-1.5 py-1 font-mono text-xs ${side === 'put' ? 'border-trading-red-400/20 bg-trading-red-500/10 text-trading-red-300' : 'border-trading-green-400/20 bg-trading-green-500/10 text-trading-green-300'}`}>
                      {fmtNumber(row.score, 0)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="font-mono text-sm font-semibold text-gray-100">{fmtMoney(row.allocation?.usd, 'USD')}</div>
                    <div className="font-mono text-[11px] text-gray-500">{fmtMoney(row.allocation?.cad, 'CAD')}</div>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-gray-400">
                    +{fmtPct(row.exits?.tp_pct, 0)} / -{fmtPct(row.exits?.sl_pct, 0)}
                    <div className="text-[10px] text-gray-600">hold {row.exits?.hold_days || '-'}d</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-4 py-6 text-center text-sm text-gray-500">No {side} positions today</div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------------
 * Execution-timing canon (entry_timing_v71, 2026-06-11): scores finalize at
 * the close; morning reads fade 26% of the time; the last-30-min read is 92%
 * close-faithful; next-open entry costs ~1.3pp WR. The banner makes the
 * Allocator the primary "when to buy" surface; the pending card mirrors the
 * live ledger's would-open/would-exit picture from /api/portfolio/pending.
 * ---------------------------------------------------------------------- */

function fmtDur(secs) {
  const s = num(secs);
  if (s === null || s <= 0) return '';
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function useExecutionPending() {
  const [data, setData] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(null);
  const [, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch(`${API_BASE_URL}/api/portfolio/pending`)
        .then((r) => (r.ok ? r.json() : null))
        .then((p) => { if (alive && p && !p.error) { setData(p); setFetchedAt(Date.now()); } })
        .catch(() => {});
    };
    load();
    const poll = setInterval(load, 120000);          // refresh the engine view
    const tick = setInterval(() => setTick((t) => t + 1), 30000); // countdown tick
    return () => { alive = false; clearInterval(poll); clearInterval(tick); };
  }, []);
  const elapsed = fetchedAt ? Math.floor((Date.now() - fetchedAt) / 1000) : 0;
  return { data, elapsed };
}

const WINDOW_COPY = {
  window: {
    cls: 'border-trading-green-300/40 bg-trading-green-300/10 text-trading-green-100',
    title: 'EXECUTION WINDOW — buy now',
    detail: (w, e) => `Scores are near-final (92% hold to the close, ~0.4% px drift). The model fills at ${w.window_to} ET${w.seconds_to_close ? ` — closes in ${fmtDur(w.seconds_to_close - e)}` : ''}.`,
  },
  provisional: {
    cls: 'border-amber-300/30 bg-amber-400/10 text-amber-100',
    title: 'Scores are provisional',
    detail: (w, e) => `Intraday reads are partial-day — ~26% of morning signals fade by the close. Execution window opens at ${w.window_from} ET${w.seconds_to_window ? ` (in ${fmtDur(w.seconds_to_window - e)})` : ''}.`,
  },
  pre_open: {
    cls: 'border-trading-blue-400/30 bg-trading-blue-500/10 text-trading-blue-100',
    title: 'Pre-open',
    detail: (w) => `Carry-over buys from the last close fill at the open (~1.3pp below the model's close fill, still +EV). New signals firm up in the ${w.window_from}–${w.window_to} ET window.`,
  },
  closed: {
    cls: 'border-white/[0.1] bg-white/[0.03] text-gray-300',
    title: 'Market closed — scores are final',
    detail: (w) => `Next execution window: ${fmtDate(w.window_date)} ${w.window_from}–${w.window_to} ET.`,
  },
};

function ExecutionBanner({ windowState, elapsed }) {
  if (!windowState) return null;
  const copy = WINDOW_COPY[windowState.state] || WINDOW_COPY.closed;
  return (
    <div className={`flex items-start gap-3 rounded-md border px-4 py-3 ${copy.cls}`}>
      <Clock className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <div className="text-sm font-semibold tracking-wide">{copy.title}</div>
        <div className="mt-0.5 text-xs leading-5 opacity-80">{copy.detail(windowState, elapsed)}</div>
      </div>
    </div>
  );
}

function PendingGroup({ title, tone, rows, render }) {
  if (!rows?.length) return null;
  const toneClass = tone === 'red' ? 'text-trading-red-300' : tone === 'amber' ? 'text-amber-300' : 'text-trading-green-300';
  return (
    <div>
      <div className={`mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] ${toneClass}`}>{title}</div>
      <div className="space-y-1">
        {rows.map((r, i) => (
          <div key={`${r.symbol}-${i}`} className="flex items-center justify-between gap-3 text-xs">
            <span className="flex items-center gap-2 truncate">
              <Link to={`/stock/${r.symbol}`} className="font-mono font-semibold text-gray-100 hover:text-trading-blue-400">{r.symbol}</Link>
              <span className="text-gray-500">{r.entry_score != null ? `${r.entry_score} · ` : ''}{r.dte || 30}DTE{r.strike != null ? ` · $${r.strike}` : ''}{r.reason ? ` · ${r.reason}` : ''}</span>
            </span>
            <span className="font-mono text-gray-300">{render(r)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* The live ledger's pending picture — what the engine itself will do. */
function EnginePendingCard({ pending, windowState }) {
  if (!pending) return null;
  const wouldOpen = pending.would_open || [];
  const wouldExit = pending.would_exit || [];
  const carryover = pending.carryover || [];
  if (!wouldOpen.length && !wouldExit.length && !carryover.length) return null;
  const inWindow = windowState?.state === 'window';
  return (
    <section className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Live portfolio — pending actions</div>
        <Link to="/portfolio" className="text-[11px] text-trading-blue-300 hover:underline">open portfolio →</Link>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <PendingGroup title="Sell now" tone="red" rows={wouldExit}
          render={(r) => `${r.est_pnl_pct != null ? `${r.est_pnl_pct >= 0 ? '+' : ''}${r.est_pnl_pct.toFixed(0)}% · ` : ''}${fmtMoney(r.premium_usd, 'USD')}`} />
        <PendingGroup title={inWindow ? 'Buy now — fills at the close' : 'Forming — fills at the close if held'}
          tone={inWindow ? 'green' : 'amber'} rows={wouldOpen}
          render={(r) => `${fmtMoney(r.premium_usd, 'USD')}${r.alloc_pct != null ? ` (${Number(r.alloc_pct).toFixed(1)}%)` : ''}`} />
        <PendingGroup title="Carry-over (filled at last close)" tone="green" rows={carryover}
          render={(r) => fmtMoney(r.premium_usd, 'USD')} />
      </div>
    </section>
  );
}

function InfoRow({ label, value, tone }) {
  const toneClass = tone === 'green' ? 'text-trading-green-300' : tone === 'red' ? 'text-trading-red-300' : tone === 'amber' ? 'text-amber-300' : 'text-gray-100';
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/[0.04] py-2 last:border-b-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={`text-right font-mono text-sm ${toneClass}`}>{value}</span>
    </div>
  );
}

export default function Allocator() {
  const initialProfile = (() => {
    if (typeof window === 'undefined') return 'core';
    const stored = window.localStorage?.getItem(PORTFOLIO_PROFILE_STORAGE_KEY);
    return ['sentinel', 'core', 'apex'].includes(stored) ? stored : 'core';
  })();
  const [form, setForm] = useState({ amount: 75000, currency: 'CAD', strategy: '30dte', profile: initialProfile });
  const [query, setQuery] = useState({ amount: 75000, currency: 'CAD', strategy: '30dte', profile: initialProfile, version: null });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeVersion, setActiveVersion] = useState(null);
  const [availableScoreVersions, setAvailableScoreVersions] = useState([]);
  const [legacyScoreVersions, setLegacyScoreVersions] = useState([]);
  const [selectedScoreVersionId, setSelectedScoreVersionId] = useState(null);
  const [portfolioProfiles, setPortfolioProfiles] = useState(DEFAULT_PORTFOLIO_PROFILES);
  const { data: pendingData, elapsed: pendingElapsed } = useExecutionPending();

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE_URL}/api/score/versions`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!alive || !payload) return;
        setActiveVersion(payload.version || null);
        setAvailableScoreVersions(Array.isArray(payload.available_versions) ? payload.available_versions : []);
        setLegacyScoreVersions(Array.isArray(payload.legacy_versions) ? payload.legacy_versions : []);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE_URL}/api/portfolio/profiles/compare?_t=${Math.floor(Date.now() / 30000)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!alive || !Array.isArray(payload?.rows)) return;
        setPortfolioProfiles(normalizeProfiles(payload.rows));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const selectedScoreVersion = useMemo(() => {
    const all = [...availableScoreVersions, ...legacyScoreVersions];
    if (selectedScoreVersionId != null) {
      return all.find((version) => version.id === selectedScoreVersionId) || null;
    }
    return activeVersion;
  }, [availableScoreVersions, legacyScoreVersions, selectedScoreVersionId, activeVersion]);

  const loadPlan = useCallback(async (params) => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        amount: params.amount,
        currency: params.currency,
        strategy: params.strategy,
        profile: params.profile,
        _t: String(Math.floor(Date.now() / 30000)),
      });
      if (params.version) qs.set('version', params.version);
      const response = await fetch(`${API_BASE_URL}/api/allocation/live?${qs.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      setData(payload);
    } catch (err) {
      setError(err.message || 'Unable to load allocation plan');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadPlan(query); }, [loadPlan, query]);

  const setAllocatorScoreVersion = useCallback((versionId) => {
    const numeric = versionId == null || versionId === '' ? null : Number(versionId);
    const nextVersion = Number.isFinite(numeric) ? numeric : null;
    setSelectedScoreVersionId(nextVersion);
    setQuery((prev) => ({ ...prev, version: nextVersion }));
  }, []);

  const submit = (event) => {
    event.preventDefault();
    setQuery({ amount: Number(form.amount) || 0, currency: form.currency, strategy: form.strategy, profile: form.profile, version: selectedScoreVersionId });
  };

  const callRows = data?.allocations?.calls || [];
  const putRows = data?.allocations?.puts || [];
  const summary = data?.summary || {};
  const market = data?.market || {};
  const guidelines = data?.guidelines || {};
  const practical = guidelines.practical_exposure || {};
  const selectedFormProfile = portfolioProfiles.find((profile) => profile.key === form.profile);
  const activeProfile = data?.portfolio_profile?.key === form.profile
    ? data.portfolio_profile
    : selectedFormProfile || portfolioProfiles.find((profile) => profile.key === query.profile);
  const profileColor = activeProfile?.color === 'red' ? 'text-trading-red-300' : activeProfile?.color === 'yellow' ? 'text-amber-300' : 'text-trading-green-300';

  const versionLabel = data?.version?.source === 'staging'
    ? `staging s${data.version?.staging?.id}`
    : data?.version?.selected?.label || selectedScoreVersion?.label || data?.version?.active?.label;

  const allocationCount = callRows.length + putRows.length;
  const routed15 = callRows.filter((r) => r.routed_15dte).length;
  const callExit = guidelines.exits?.call || {};

  return (
    <div className="h-full overflow-y-auto bg-trading-dark-950 text-gray-100">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-trading-green-300">
              <Calculator className="h-4 w-4" /> Portfolio Allocator
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-gray-100 sm:text-3xl">What to buy today</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-400">
              <span className={`font-semibold ${profileColor}`}>{activeProfile?.name || query.profile}</span> profile ·
              {' '}scores {versionLabel || '-'} · {fmtDate(data?.signals_date)}
              {routed15 > 0 && <span className="text-trading-blue-300"> · {routed15} routed to 15 DTE</span>}
            </p>
          </div>

          <form onSubmit={submit} className="flex flex-col gap-3 lg:items-end">
            <div className="flex flex-wrap items-end gap-2">
              <div className="block">
                <div className="mb-1 flex items-center justify-between gap-3">
                  <label className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">Starting Amount</label>
                  <div className="flex items-center overflow-hidden rounded border border-white/[0.08] text-[9px] font-medium leading-none">
                    {['CAD', 'USD'].map((currency) => (
                      <button key={currency} type="button" onClick={() => setForm((prev) => ({ ...prev, currency }))}
                        className={`px-1.5 py-0.5 transition-colors ${form.currency === currency ? 'bg-trading-green-400/20 text-trading-green-300' : 'text-gray-600 hover:text-gray-400'}`}>
                        {currency}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex h-10 w-40 items-center rounded-md border border-white/[0.08] bg-white/[0.04] px-3 font-mono text-sm text-gray-100 outline-none focus-within:border-trading-green-300/50">
                  <span className="select-none text-gray-500">{form.currency === 'CAD' ? 'C$' : '$'}</span>
                  <input type="number" min="1" step="1" value={form.amount ? form.amount / 1000 : ''}
                    onChange={(event) => { const value = parseInt(event.target.value, 10); setForm((prev) => ({ ...prev, amount: Number.isNaN(value) ? 0 : value * 1000 })); }}
                    onFocus={(event) => event.target.select()}
                    className="m-0 min-w-0 flex-1 border-0 bg-transparent p-0 text-right text-gray-100 outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none" />
                  <span className="select-none text-gray-500">,000</span>
                </div>
              </div>
              <div className="flex gap-1">
                <SegmentButton active={form.strategy === '30dte'} onClick={() => setForm((prev) => ({ ...prev, strategy: '30dte' }))}>30 DTE</SegmentButton>
                <SegmentButton active={form.strategy === '15dte'} onClick={() => setForm((prev) => ({ ...prev, strategy: '15dte' }))}>15 DTE</SegmentButton>
              </div>
              <PortfolioProfileToggle
                profiles={portfolioProfiles}
                value={form.profile}
                onChange={(profile) => {
                  setForm((prev) => {
                    const next = { ...prev, profile };
                    setQuery({ amount: Number(next.amount) || 0, currency: next.currency, strategy: next.strategy, profile, version: selectedScoreVersionId });
                    return next;
                  });
                  try { window.localStorage?.setItem(PORTFOLIO_PROFILE_STORAGE_KEY, profile); } catch (e) { /* ignore */ }
                }}
                compact
              />
              <ScoreVersionSelector
                versions={availableScoreVersions} legacyVersions={legacyScoreVersions} currentVersion={selectedScoreVersion}
                activeVersionId={activeVersion?.id} selectedVersionId={selectedScoreVersionId} onSelect={setAllocatorScoreVersion}
                ariaLabel="Select allocator score version" titlePrefix="Allocator scores" />
              <button type="submit" disabled={loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-trading-green-300/35 bg-trading-green-300/10 px-3 text-sm font-medium text-trading-green-100 hover:bg-trading-green-300/15 disabled:cursor-wait disabled:opacity-60">
                <Calculator className="h-4 w-4" /> Generate
              </button>
              <button type="button" onClick={() => loadPlan(query)} disabled={loading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-white/[0.08] bg-white/[0.03] px-3 text-sm text-gray-200 hover:bg-white/[0.06] disabled:cursor-wait disabled:opacity-60">
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
              </button>
            </div>
          </form>
        </header>

        <ExecutionBanner windowState={pendingData?.execution_window} elapsed={pendingElapsed} />
        <EnginePendingCard pending={pendingData?.pending} windowState={pendingData?.execution_window} />

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-trading-red-400/30 bg-trading-red-500/10 px-3 py-2 text-sm text-trading-red-200">
            <AlertTriangle className="h-4 w-4" /> {error}
          </div>
        )}
        {(data?.warnings || []).map((warning) => (
          <div key={warning} className="flex items-center gap-2 rounded-md border border-amber-300/25 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            <AlertTriangle className="h-4 w-4" /> {warning}
          </div>
        ))}

        <section className="grid gap-3 sm:grid-cols-3">
          <SummaryMetric label="Deployed" value={fmtMoney(summary.deployed?.usd, 'USD')} sub={`${fmtMoney(summary.deployed?.cad, 'CAD')} · ${fmtPct(summary.open_base_pct)} of base`} icon={<CircleDollarSign className="h-4 w-4" />} tone="green" />
          <SummaryMetric label="Cash Remaining" value={fmtMoney(summary.remaining?.usd, 'USD')} sub={`${fmtMoney(summary.remaining?.cad, 'CAD')} undeployed`} icon={<Wallet className="h-4 w-4" />} />
          <SummaryMetric label="Positions" value={`${summary.call_positions || 0}C${(summary.put_positions || 0) > 0 ? ` + ${summary.put_positions}P` : ''}`} sub={`${summary.positions_used || 0} / ${summary.max_positions || '-'} max slots`} icon={<Target className="h-4 w-4" />} />
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <AllocationPie calls={callRows} puts={putRows} cashUsd={summary.remaining?.usd} />

          <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Strategy</div>
            <InfoRow label="Instrument" value={`${guidelines.dte_label || '30 DTE'} calls`} tone="green" />
            <InfoRow label="Exit" value={`TP +${fmtPct(callExit.tp_pct, 0)} / SL -${fmtPct(callExit.sl_pct, 0)}`} tone="green" />
            <InfoRow label="Hard sell" value={`day ${guidelines.hold_days || '-'} · -${fmtPct(guidelines.hard_sell_pct, 0)}`} />
            <InfoRow label="Exposure cap" value={fmtPct(practical.gross_premium_cap_pct, 0)} />
            <InfoRow label="Breadth" value={`${fmtNumber(market.breadth_score, 0)} ${market.breadth_state || ''}`} tone={market.breadth_state === 'stressed' ? 'amber' : 'green'} />
            <InfoRow label="Regime" value={`${fmtNumber(market.regime_multiplier, 2)}x ${market.regime_state || ''}`} />
          </div>
        </section>

        <BuyList title="Calls to buy" rows={callRows} side="call" currency={form.currency} />
        {putRows.length > 0 && <BuyList title="Puts to buy" rows={putRows} side="put" currency={form.currency} />}

        {!loading && !error && allocationCount === 0 && (
          <div className="rounded-md border border-white/[0.06] bg-white/[0.025] px-4 py-8 text-center text-sm text-gray-500">
            No positions for the current amount and score set — the book is fully in cash today.
          </div>
        )}
      </div>
    </div>
  );
}
