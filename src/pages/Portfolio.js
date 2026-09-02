import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Chart as ChartJS,
  ArcElement,
  CategoryScale,
  LinearScale,
  LogarithmicScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
} from 'chart.js';
import { Doughnut, Line } from 'react-chartjs-2';
import { AlertTriangle, Flame, RefreshCw, Target, TrendingUp, Wallet } from 'lucide-react';

ChartJS.register(
  ArcElement, CategoryScale, LinearScale, LogarithmicScale,
  PointElement, LineElement, Filler, Tooltip, Legend,
);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const CASH_COLOR = '#334155';
// Size gradient: heaviest position = dark, lightest = pale (green calls / red puts).
const GREEN_DARK = '#065f46';
const GREEN_LIGHT = '#bbf7d0';
const RED_DARK = '#7f1d1d';
const RED_LIGHT = '#fecaca';

// ── formatters ──────────────────────────────────────────────────────────────
function num(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value);
}

// Plain USD (positions are always shown in native USD).
function usd(amount, signed = false) {
  const n = num(amount);
  if (n === null) return '—';
  const sign = n < 0 ? '-' : (signed ? '+' : '');
  const a = Math.abs(n);
  const body = a >= 1000 ? Math.round(a).toLocaleString() : a.toFixed(a >= 100 ? 0 : 2);
  return `${sign}$${body}`;
}

// Toggled currency for the headline total / chart.
function money(amountUsd, inCad, fx, signed = false) {
  const n = num(amountUsd);
  if (n === null) return '—';
  const v = inCad ? n * fx : n;
  const sign = v < 0 ? '-' : (signed ? '+' : '');
  const a = Math.abs(v);
  const body = a >= 1000 ? Math.round(a).toLocaleString() : a.toFixed(2);
  return `${sign}$${body} ${inCad ? 'CAD' : 'USD'}`;
}

function fmtPct(value, digits = 1) {
  const n = num(value);
  if (n === null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function fmtDate(value) {
  if (!value) return '—';
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtDateShort(value) {
  if (!value) return '—';
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function strikeStr(strike) {
  const n = num(strike);
  return n === null ? '' : String(n);
}

function pnlClass(n) {
  const v = num(n);
  if (v === null || v === 0) return 'text-gray-300';
  return v > 0 ? 'text-trading-green-400' : 'text-trading-red-400';
}

// ── color helpers (size gradient) ────────────────────────────────────────────
function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex(r, g, b) {
  const c = (x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}
function lerpHex(a, b, t) {
  const A = hexToRgb(a);
  const B = hexToRgb(b);
  return rgbToHex(A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t);
}
// t = 0 (heaviest → dark) … 1 (lightest → pale)
function sizeShade(side, t) {
  return side === 'put' ? lerpHex(RED_DARK, RED_LIGHT, t) : lerpHex(GREEN_DARK, GREEN_LIGHT, t);
}

function reasonDot(reason) {
  if (reason === 'tp' || reason === 'dh_pop') return 'bg-trading-green-400';
  if (reason === 'sl' || reason === 'prem') return 'bg-trading-red-400';
  return 'bg-amber-400';
}

// ── small components ─────────────────────────────────────────────────────────
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

// Emphasis ladder on an options contract: SYMBOL (brightest/bold) > strike > expiry > side.
function ContractLabel({ p, link = true }) {
  const side = p.side === 'put' ? 'Put' : 'Call';
  const sym = link ? (
    <Link to={`/stock/${p.symbol}`} className="font-mono text-[13px] font-semibold text-gray-100 transition-colors hover:text-trading-blue-400">
      {p.symbol}
    </Link>
  ) : (
    <span className="font-mono text-[13px] font-semibold text-gray-100">{p.symbol}</span>
  );
  return (
    <span className="flex min-w-0 items-baseline gap-1.5 truncate">
      {sym}
      {p.strike != null && <span className="font-mono text-[12px] text-gray-300">${strikeStr(p.strike)}</span>}
      {p.expiration_date && <span className="font-mono text-[10px] text-gray-500">{fmtDateShort(p.expiration_date)}</span>}
      <span className="font-mono text-[9px] uppercase tracking-wide text-gray-600">{side}</span>
      {p.routed_15dte && (
        <span className="rounded border border-trading-blue-400/40 bg-trading-blue-500/10 px-1 text-[8px] font-medium text-trading-blue-300">15DTE</span>
      )}
    </span>
  );
}

// One slice per open position (by current USD value), sized-gradient + a Cash slice.
function HoldingsPie({ positions, cashUsd }) {
  const cash = Math.max(0, num(cashUsd) || 0);

  const slices = useMemo(() => {
    const items = positions
      .map((p) => ({ label: p.symbol, side: p.side, usd: num(p.current_value_usd) || 0 }))
      .filter((s) => s.usd > 0)
      .sort((a, b) => b.usd - a.usd);
    const n = items.length;
    return items.map((s, i) => ({ ...s, color: sizeShade(s.side, n > 1 ? i / (n - 1) : 0) }));
  }, [positions]);

  const total = slices.reduce((a, s) => a + s.usd, 0) + cash || 1;
  const deployed = total - cash;

  const chartData = {
    labels: [...slices.map((s) => s.label), 'Cash'],
    datasets: [{
      data: [...slices.map((s) => s.usd), cash],
      backgroundColor: [...slices.map((s) => s.color), CASH_COLOR],
      borderColor: '#0b0f17',
      borderWidth: 2,
      hoverOffset: 6,
    }],
  };
  const options = {
    cutout: '62%',
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => ` ${ctx.label}: ${usd(ctx.parsed || 0)} (${(((ctx.parsed || 0) / total) * 100).toFixed(1)}%)`,
        },
      },
    },
    maintainAspectRatio: false,
  };

  // legend entries (holdings + cash), split into 2 aligned columns (no scrollbar).
  const entries = [...slices, { label: 'Cash', usd: cash, color: CASH_COLOR }];
  const perCol = Math.ceil(entries.length / 2) || 1;
  const chunks = [entries.slice(0, perCol), entries.slice(perCol)].filter((c) => c.length);

  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Holdings (USD)</div>
        <div className="font-mono text-[11px] text-gray-500">Cash {usd(cash)}</div>
      </div>
      {slices.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-500">No open positions · {usd(cash)} cash</div>
      ) : (
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div className="relative h-40 w-40 shrink-0">
            <Doughnut data={chartData} options={options} />
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <div className="font-mono text-lg font-semibold text-trading-green-300">{((deployed / total) * 100).toFixed(0)}%</div>
              <div className="text-[10px] uppercase tracking-wide text-gray-500">deployed</div>
            </div>
          </div>
          <div className="grid min-w-0 flex-1 grid-cols-1 gap-x-5 sm:grid-cols-2">
            {chunks.map((chunk, ci) => (
              <div key={ci} className="grid grid-cols-[1fr_auto_auto] content-start items-baseline gap-x-3 gap-y-1">
                {chunk.map((e) => (
                  <React.Fragment key={e.label}>
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: e.color }} />
                      <span className="truncate font-mono text-[11px] text-gray-200">{e.label}</span>
                    </span>
                    <span className="text-right font-mono text-[11px] tabular-nums text-gray-300">{usd(e.usd)}</span>
                    <span className="text-right font-mono text-[10px] tabular-nums text-gray-500">{((e.usd / total) * 100).toFixed(1)}%</span>
                  </React.Fragment>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GrowthChart({ curve, inCad, fx, startLabel }) {
  const [logScale, setLogScale] = useState(false);
  const labels = useMemo(() => curve.map((p) => p.date), [curve]);
  const values = useMemo(() => curve.map((p) => (inCad ? p.equity_usd * fx : p.equity_usd)), [curve, inCad, fx]);
  const cur = inCad ? 'CAD' : 'USD';

  const data = {
    labels,
    datasets: [{
      data: values,
      borderColor: '#34d399',
      borderWidth: 1.5,
      pointRadius: curve.length <= 30 ? 2 : 0,
      pointHoverRadius: 3,
      fill: true,
      backgroundColor: (ctx) => {
        const { ctx: c, chartArea } = ctx.chart;
        if (!chartArea) return 'transparent';
        const grad = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        grad.addColorStop(0, 'rgba(52,211,153,0.18)');
        grad.addColorStop(1, 'rgba(52,211,153,0.01)');
        return grad;
      },
      tension: 0.2,
    }],
  };
  const fmtAxis = (v) => `$${Math.round(v).toLocaleString()}`;
  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#111113',
        borderColor: 'rgba(255,255,255,0.08)',
        borderWidth: 1,
        titleColor: '#9CA3AF',
        bodyColor: '#E5E7EB',
        titleFont: { size: 10 },
        bodyFont: { size: 11, family: 'JetBrains Mono, monospace' },
        padding: 8,
        callbacks: { label: (ctx) => ` ${fmtAxis(ctx.parsed.y)} ${cur}` },
      },
    },
    scales: {
      x: {
        ticks: { color: '#4B5563', font: { size: 9 }, maxTicksLimit: 8, maxRotation: 0 },
        grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
        border: { display: false },
      },
      y: {
        type: logScale ? 'logarithmic' : 'linear',
        ticks: { color: '#4B5563', font: { size: 9 }, maxTicksLimit: 5, callback: fmtAxis },
        grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
        border: { display: false },
      },
    },
  };

  return (
    <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Growth ({cur})</div>
        <button
          onClick={() => setLogScale((s) => !s)}
          className={`rounded border px-2 py-0.5 font-mono text-[9px] transition-colors ${
            logScale
              ? 'border-trading-green-400/40 bg-trading-green-400/10 text-trading-green-300'
              : 'border-white/[0.08] text-gray-600 hover:text-gray-400'
          }`}
        >
          {logScale ? 'LOG' : 'LIN'}
        </button>
      </div>
      {curve.length === 0 ? (
        <div className="py-16 text-center text-sm text-gray-500">No history yet</div>
      ) : (
        <div style={{ height: 232 }}><Line data={data} options={options} /></div>
      )}
      {startLabel && <div className="mt-2 text-[11px] text-gray-600">Started {startLabel}</div>}
    </div>
  );
}

// ── Open positions (Backtest-style flex-column table) ─────────────────────────
const OPEN_COLS = [
  { key: 'pos',   label: 'Position',  flex: '3.4 0 0%', align: 'left' },
  { key: 'held',  label: 'Days Held', flex: '1.0 0 0%', align: 'right' },
  { key: 'alloc', label: 'Alloc',     flex: '1.1 0 0%', align: 'right' },
  { key: 'cost',  label: 'Cost',      flex: '1.3 0 0%', align: 'right' },
  { key: 'value', label: 'Value',     flex: '1.3 0 0%', align: 'right' },
  { key: 'pnl',   label: 'P/L',       flex: '2.0 0 0%', align: 'right' },
];

function OpenPositions({ positions }) {
  return (
    <div className="overflow-hidden rounded-md border border-white/[0.06] bg-trading-dark-900">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
        <p className="text-[11px] font-medium uppercase tracking-wider text-trading-green-300">
          Open Positions
          <span className="ml-2 font-normal normal-case text-gray-600">({positions.length} · amounts in USD)</span>
        </p>
      </div>
      <div className="flex items-center border-b border-white/[0.04] px-2 py-1">
        {OPEN_COLS.map((col) => (
          <div key={col.key} style={{ flex: col.flex }} className={`min-w-0 px-1.5 text-[9px] font-medium uppercase tracking-wider text-gray-600 ${col.align === 'right' ? 'text-right' : ''}`}>
            {col.label}
          </div>
        ))}
      </div>
      {positions.length === 0 ? (
        <div className="px-3 py-8 text-center text-sm text-gray-500">No open positions right now</div>
      ) : (
        <div>
          {positions.map((p) => (
            <div key={p.pos_key} className="flex items-center border-b border-white/[0.03] px-2 py-1 hover:bg-white/[0.02]">
              <div style={{ flex: OPEN_COLS[0].flex }} className="min-w-0 px-1.5"><ContractLabel p={p} /></div>
              <div style={{ flex: OPEN_COLS[1].flex }} className="min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums text-gray-500">{p.hold_bars != null ? p.hold_bars : '—'}</div>
              <div style={{ flex: OPEN_COLS[2].flex }} className="min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums text-gray-400">{p.alloc_pct != null ? `${p.alloc_pct.toFixed(1)}%` : '—'}</div>
              <div style={{ flex: OPEN_COLS[3].flex }} className="min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums text-gray-400">{usd(p.premium_usd)}</div>
              <div style={{ flex: OPEN_COLS[4].flex }} className="min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums text-gray-200">{usd(p.current_value_usd)}</div>
              <div style={{ flex: OPEN_COLS[5].flex }} className={`min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums ${pnlClass(p.pnl_usd)}`}>
                {usd(p.pnl_usd, true)}<span className="ml-1 text-[10px] opacity-80">{fmtPct((p.pnl_pct || 0) * 100, 0)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Closed trades (mirrors the Backtest trade-log framing) ────────────────────
const CLOSED_COLS = [
  { key: 'pos',    label: 'Position',  flex: '3.4 0 0%', align: 'left' },
  { key: 'pnl',    label: 'P/L',       flex: '1.8 0 0%', align: 'right' },
  { key: 'ret',    label: 'Return',    flex: '1.0 0 0%', align: 'right' },
  { key: 'reason', label: 'Exit',      flex: '1.6 0 0%', align: 'left' },
  { key: 'held',   label: 'Days',      flex: '0.9 0 0%', align: 'right' },
  { key: 'entry',  label: 'Entry',     flex: '1.3 0 0%', align: 'right' },
  { key: 'exit',   label: 'Exit Date', flex: '1.3 0 0%', align: 'right' },
];

function ClosedTrades({ closed }) {
  const [show, setShow] = useState(100);
  if (!closed.length) return null;
  const visible = closed.slice(0, show);
  return (
    <div className="overflow-hidden rounded-md border border-white/[0.06] bg-trading-dark-900">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
        <p className="text-[11px] font-medium uppercase tracking-wider text-gray-400">
          Closed Trades
          <span className="ml-2 font-normal normal-case text-gray-600">({closed.length} · newest first)</span>
        </p>
        <div className="flex items-center gap-3">
          {[{ label: 'TP', color: 'bg-trading-green-400' }, { label: 'SL', color: 'bg-trading-red-400' }, { label: 'HARD', color: 'bg-amber-400' }].map(({ label, color }) => (
            <span key={label} className="flex items-center gap-1">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${color}`} />
              <span className="font-mono text-[9px] text-gray-600">{label}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="flex items-center border-b border-white/[0.04] px-2 py-1">
        {CLOSED_COLS.map((col) => (
          <div key={col.key} style={{ flex: col.flex }} className={`min-w-0 px-1.5 text-[9px] font-medium uppercase tracking-wider text-gray-600 ${col.align === 'right' ? 'text-right' : ''}`}>
            {col.label}
          </div>
        ))}
      </div>
      <div>
        {visible.map((p) => (
          <div key={p.pos_key} className="flex items-center border-b border-white/[0.03] px-2 py-1 hover:bg-white/[0.02]">
            <div style={{ flex: CLOSED_COLS[0].flex }} className="min-w-0 px-1.5"><ContractLabel p={p} /></div>
            <div style={{ flex: CLOSED_COLS[1].flex }} className={`min-w-0 px-1.5 text-right font-mono text-[11px] tabular-nums ${pnlClass(p.pnl_usd)}`}>{usd(p.pnl_usd, true)}</div>
            <div style={{ flex: CLOSED_COLS[2].flex }} className={`min-w-0 px-1.5 text-right font-mono text-[10px] tabular-nums ${pnlClass(p.pnl_usd)}`}>{fmtPct((p.pnl_pct || 0) * 100, 0)}</div>
            <div style={{ flex: CLOSED_COLS[3].flex }} className="flex min-w-0 items-center gap-1.5 px-1.5">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${reasonDot(p.exit_reason)}`} />
              <span className="truncate text-[10px] text-gray-400">{p.exit_reason_label}</span>
            </div>
            <div style={{ flex: CLOSED_COLS[4].flex }} className="min-w-0 px-1.5 text-right font-mono text-[10px] tabular-nums text-gray-600">{p.hold_bars != null ? p.hold_bars : '—'}</div>
            <div style={{ flex: CLOSED_COLS[5].flex }} className="min-w-0 px-1.5 text-right font-mono text-[10px] tabular-nums text-gray-600">{fmtDateShort(p.entry_date)}</div>
            <div style={{ flex: CLOSED_COLS[6].flex }} className="min-w-0 px-1.5 text-right font-mono text-[10px] tabular-nums text-gray-500">{fmtDateShort(p.exit_date)}</div>
          </div>
        ))}
      </div>
      {closed.length > show && (
        <button
          onClick={() => setShow((s) => s + 200)}
          className="w-full border-t border-white/[0.04] py-2 text-[11px] text-gray-500 transition-colors hover:bg-white/[0.02] hover:text-gray-300"
        >
          Show more ({(closed.length - show).toLocaleString()} remaining)
        </button>
      )}
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────
export default function Portfolio() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [displayCurrency, setDisplayCurrency] = useState('CAD');
  const inCad = displayCurrency === 'CAD';

  const load = useCallback(async (forceSync = false) => {
    setLoading(true);
    setError(null);
    try {
      const bucket = Math.floor(Date.now() / 30000);
      const url = `${API_BASE_URL}/api/portfolio/state?${forceSync ? 'sync=1&' : ''}_t=${bucket}`;
      const res = await fetch(url);
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
      setData(payload);
    } catch (err) {
      setError(err.message || 'Unable to load portfolio');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = data?.run || {};
  const summary = data?.summary || {};
  const positions = data?.positions || [];
  const closed = data?.closed || [];
  const curve = data?.equity_curve || [];
  const fx = num(run.cad_per_usd) || 1.37;
  const startCad = num(run.starting_capital_cad) || 50000;
  const pnl = num(summary.total_pnl_usd) || 0;
  const tone = pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'neutral';
  const startLabel = `$${Math.round(startCad).toLocaleString()} CAD on ${fmtDate(run.start_date)}`;

  return (
    <div className="h-full overflow-y-auto bg-trading-dark-950 text-gray-100">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        {/* Header */}
        <header className="flex flex-col gap-4 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-trading-green-300">
              <Wallet className="h-4 w-4" /> Live Portfolio
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-gray-100 sm:text-3xl">{run.name || 'Portfolio'}</h1>
            <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-gray-500">
              {(() => {
                const PROFILE_BADGE = {
                  core:     { label: 'Core · Compounder', Icon: Flame,  cls: 'border-yellow-300/40 bg-yellow-300/10 text-yellow-200' },
                  apex:     { label: 'Apex · Sprint',      Icon: Flame,  cls: 'border-trading-red-300/40 bg-trading-red-300/10 text-trading-red-200' },
                  sentinel: { label: 'Sentinel · Safe',    Icon: Target, cls: 'border-trading-green-300/40 bg-trading-green-300/10 text-trading-green-200' },
                };
                const badge = PROFILE_BADGE[run.profile] || PROFILE_BADGE.core;
                const BadgeIcon = badge.Icon;
                return (
                  <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${badge.cls}`}>
                    <BadgeIcon className="h-3 w-3" /> {badge.label}
                  </span>
                );
              })()}
              <span>v70 strategy · started {fmtDate(run.start_date)} from ${Math.round(startCad).toLocaleString()} CAD</span>
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center overflow-hidden rounded border border-white/[0.08] text-[10px] font-medium">
              {['CAD', 'USD'].map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setDisplayCurrency(c)}
                  className={`px-2.5 py-1 transition-colors ${
                    displayCurrency === c ? 'bg-trading-green-400/20 text-trading-green-300' : 'text-gray-600 hover:text-gray-400'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => load(true)}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.02] px-2.5 py-1.5 text-xs text-gray-400 transition-colors hover:bg-white/[0.05] hover:text-gray-200 disabled:opacity-50"
              title="Re-evaluate against the latest completed session"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Sync
            </button>
          </div>
        </header>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-trading-red-400/30 bg-trading-red-500/10 px-3 py-2 text-sm text-trading-red-200">
            <AlertTriangle className="h-4 w-4" /> {error}
          </div>
        )}

        {loading && !data ? (
          <div className="py-24 text-center text-sm text-gray-500">Loading portfolio…</div>
        ) : data ? (
          <>
            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SummaryMetric
                label="Total Value"
                value={money(summary.equity_usd, inCad, fx)}
                sub={`${fmtPct(summary.total_return_pct)} all-time`}
                icon={<Wallet className="h-4 w-4" />}
                tone={tone}
              />
              <SummaryMetric
                label="Total P/L"
                value={money(summary.total_pnl_usd, inCad, fx, true)}
                sub={`${money(summary.realized_pnl_usd, inCad, fx, true)} realized`}
                icon={<TrendingUp className="h-4 w-4" />}
                tone={tone}
              />
              <SummaryMetric
                label="Deployed"
                value={fmtPct(summary.deployed_pct)}
                sub={`${money(summary.cash_usd, inCad, fx)} cash · ${summary.open_count} open`}
                icon={<Target className="h-4 w-4" />}
              />
              <SummaryMetric
                label="Max Drawdown"
                value={fmtPct(-(summary.max_dd_pct || 0))}
                sub={`${summary.closed_count} closed trades`}
              />
            </section>

            <section className="grid gap-4 lg:grid-cols-2">
              <HoldingsPie positions={positions} cashUsd={summary.cash_usd} />
              <GrowthChart curve={curve} inCad={inCad} fx={fx} startLabel={startLabel} />
            </section>

            <OpenPositions positions={positions} />
            <ClosedTrades closed={closed} />

            <div className="pb-2 text-[11px] leading-5 text-gray-600">
              Holdings are re-derived from current data on every update — nothing is queued or pre-committed, so a signal that no longer qualifies by the session close is simply dropped. Entries use that session's finalized score and closing price; intraday you see the last completed session marked to its close.
              {run.last_processed_date && <> Last session processed: <span className="text-gray-500">{fmtDate(run.last_processed_date)}</span>.</>}
              {' '}Positions show representative ATM strikes/expirations; P/L is modeled on the underlying via the v70 option model. 1 USD = {fx.toFixed(4)} CAD (fixed at inception).
            </div>
          </>
        ) : (
          <div className="py-24 text-center text-sm text-gray-500">No portfolio yet.</div>
        )}
      </div>
    </div>
  );
}
