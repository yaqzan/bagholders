import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, ChevronsUpDown, RefreshCw, Shield } from 'lucide-react';
import { getProfilePresentation, normalizeProfiles } from '../components/PortfolioProfileToggle';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const DEFAULT_WINDOW = '5y';

const RISK_STYLE = {
  green: {
    dot: 'bg-trading-green-300',
    text: 'text-trading-green-300',
    border: 'border-trading-green-300/30',
    bg: 'bg-trading-green-300/[0.055]',
    soft: 'bg-trading-green-300/10 text-trading-green-200 border-trading-green-300/30',
  },
  yellow: {
    dot: 'bg-amber-300',
    text: 'text-amber-300',
    border: 'border-amber-300/30',
    bg: 'bg-amber-300/[0.055]',
    soft: 'bg-amber-300/10 text-amber-200 border-amber-300/30',
  },
  red: {
    dot: 'bg-trading-red-300',
    text: 'text-trading-red-300',
    border: 'border-trading-red-300/30',
    bg: 'bg-trading-red-300/[0.055]',
    soft: 'bg-trading-red-300/10 text-trading-red-200 border-trading-red-300/30',
  },
};

function num(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value);
}

function metrics(row, windowName) {
  return row?.windows?.[windowName]?.metrics || {};
}

function fmtNumber(value, digits = 0) {
  const n = num(value);
  if (n === null) return '-';
  return n.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtPct(value, digits = 1, signed = false) {
  const n = num(value);
  if (n === null) return '-';
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
}

function fmtPp(value, digits = 1) {
  const n = num(value);
  if (n === null) return '-';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}pp`;
}

function fmtCompactPct(value) {
  const n = num(value);
  if (n === null) return '-';
  const abs = Math.abs(n);
  const sign = n >= 0 ? '+' : '-';
  if (abs >= 1e9) return `${sign}${abs.toExponential(2)}%`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M%`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}k%`;
  return fmtPct(n, 1, true);
}

function fmtMoneyCompact(value) {
  const n = num(value);
  if (n === null) return '-';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}k`;
  return `${sign}$${Math.round(abs).toLocaleString()}`;
}

function fmtCap(value) {
  const n = num(value);
  if (n === null || n <= 0) return 'off';
  return fmtMoneyCompact(n);
}

function valueClass(value, inverse = false) {
  const n = num(value);
  if (n === null || n === 0) return 'text-gray-400';
  const good = inverse ? n < 0 : n > 0;
  return good ? 'text-trading-green-300' : 'text-trading-red-300';
}

function SortHeader({ label, sortKey, sort, setSort, align = 'right' }) {
  const active = sort.key === sortKey;
  const nextDirection = active && sort.direction === 'desc' ? 'asc' : 'desc';
  const SortIcon = active ? (sort.direction === 'desc' ? ArrowDown : ArrowUp) : ChevronsUpDown;
  return (
    <button
      type="button"
      onClick={() => setSort({ key: sortKey, direction: nextDirection })}
      className={`group flex w-full items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-200 ${
        align === 'right' ? 'justify-end text-right' : 'justify-start text-left'
      }`}
    >
      <span>{label}</span>
      <SortIcon className={`h-3 w-3 ${active ? 'text-trading-green-300' : 'text-gray-700 group-hover:text-gray-500'}`} />
    </button>
  );
}

function buildAccessors(windowName) {
  return {
    profile: (row) => row.version || 0,
    final: (row) => metrics(row, windowName).mean_final,
    return: (row) => metrics(row, windowName).return_pct,
    dd: (row) => metrics(row, windowName).worst_dd_pct,
    ddWorse: (row) => metrics(row, windowName).dd_worse_pp,
    coll: (row) => metrics(row, windowName).p_coll_pct,
    open: (row) => metrics(row, windowName).avg_open_premium_base_pct,
    trades: (row) => (num(metrics(row, windowName).call_trades) || 0) + (num(metrics(row, windowName).put_trades) || 0),
  };
}

function ProfileCard({ row, selectedWindow }) {
  const style = RISK_STYLE[row.color] || RISK_STYLE.green;
  const presentation = getProfilePresentation(row);
  const Icon = presentation.icon || Shield;
  const m = metrics(row, selectedWindow);
  const p = row.params || {};
  return (
    <section className={`min-w-0 rounded-md border ${style.border} ${style.bg} p-4`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${style.dot}`} />
            <h2 className="text-lg font-semibold text-gray-100">{row.name}</h2>
            <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${style.soft}`}>
              {presentation.label || row.risk}
            </span>
          </div>
          <div className="mt-1 text-xs font-medium text-gray-300">v{row.version} profile</div>
          <div className="mt-1 truncate text-xs text-gray-500">{row.candidate}</div>
        </div>
        <Icon className={`h-5 w-5 ${style.text}`} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-gray-500">Mean Final</div>
          <div className="mt-1 font-mono text-lg text-gray-100">{fmtMoneyCompact(m.mean_final)}</div>
        </div>
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-gray-500">Worst DD</div>
          <div className={`mt-1 font-mono text-lg ${valueClass(m.worst_dd_pct, true)}`}>{fmtPct(m.worst_dd_pct)}</div>
        </div>
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-gray-500">DD vs Sentinel</div>
          <div className={`mt-1 font-mono text-sm ${valueClass(m.dd_worse_pp, true)}`}>{fmtPp(m.dd_worse_pp)}</div>
        </div>
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-gray-500">Open/Base</div>
          <div className="mt-1 font-mono text-sm text-gray-200">{fmtPct(m.avg_open_premium_base_pct)}</div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 border-t border-white/[0.06] pt-3 text-xs">
        <div>
          <div className="text-gray-600">Slots</div>
          <div className="mt-1 font-mono text-gray-300">{p.max_positions ?? '-'} / {p.call_max ?? '-'} / {p.put_max ?? '-'}</div>
        </div>
        <div>
          <div className="text-gray-600">Caps</div>
          <div className="mt-1 font-mono text-gray-300">{fmtPct((p.gross_cap || 0) * 100, 0)} / {fmtPct((p.call_cap || 0) * 100, 0)}</div>
        </div>
        <div>
          <div className="text-gray-600">Base</div>
          <div className="mt-1 font-mono text-gray-300">{fmtCap(p.capital_ceiling)}</div>
        </div>
      </div>
    </section>
  );
}

export default function PortfolioProfiles() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedWindow, setSelectedWindow] = useState(DEFAULT_WINDOW);
  const [sort, setSort] = useState({ key: 'profile', direction: 'asc' });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const bucket = Math.floor(Date.now() / 30000);
      const response = await fetch(`${API_BASE_URL}/api/portfolio/profiles/compare?_t=${bucket}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      setData(payload);
      if (payload.windows?.length && !payload.windows.find((w) => w.name === selectedWindow)) {
        setSelectedWindow(payload.windows[payload.windows.length - 1].name);
      }
    } catch (err) {
      setError(err.message || 'Unable to load portfolio profiles');
    } finally {
      setLoading(false);
    }
  }, [selectedWindow]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const profileRows = useMemo(() => normalizeProfiles(data?.rows), [data]);

  const rows = useMemo(() => {
    const source = profileRows;
    const accessors = buildAccessors(selectedWindow);
    const sorted = [...source].sort((a, b) => {
      const av = accessors[sort.key]?.(a);
      const bv = accessors[sort.key]?.(b);
      const an = num(av);
      const bn = num(bv);
      if (an === null && bn === null) return (a.version || 0) - (b.version || 0);
      if (an === null) return 1;
      if (bn === null) return -1;
      return sort.direction === 'desc' ? bn - an : an - bn;
    });
    return sorted;
  }, [profileRows, selectedWindow, sort]);

  const windows = data?.windows || [];

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden bg-trading-dark-950 text-gray-100">
      <div className="mx-auto flex min-w-0 max-w-[1500px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-trading-green-300">
              <Shield className="h-4 w-4" />
              Portfolio Profiles
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-gray-100 sm:text-3xl">
              Sentinel / Core / Apex
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-400">
              Active scores {data?.base_score_version || '-'} / {data?.evidence?.phase || '-'} / {fmtNumber(data?.evidence?.results_rows)} profile-window cells.
            </p>
          </div>
          <button
            type="button"
            onClick={fetchData}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-gray-200 hover:bg-white/[0.06] disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </header>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-trading-red-400/30 bg-trading-red-500/10 px-3 py-2 text-sm text-trading-red-200">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        )}

        <div className="grid min-w-0 grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
          {windows.map((window) => (
            <button
              key={window.name}
              type="button"
              onClick={() => setSelectedWindow(window.name)}
              className={`w-full rounded-md border px-2 py-2 text-sm transition-colors sm:w-auto sm:px-3 ${
                selectedWindow === window.name
                  ? 'border-trading-green-300/50 bg-trading-green-300/10 text-trading-green-200'
                  : 'border-white/[0.08] bg-white/[0.02] text-gray-400 hover:bg-white/[0.05] hover:text-gray-200'
              }`}
            >
              {window.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex h-64 items-center justify-center text-sm text-gray-500">Loading portfolio profiles...</div>
        ) : (
          <>
            <section className="grid min-w-0 gap-3 lg:grid-cols-3">
              {profileRows.map((row) => (
                <ProfileCard key={row.key} row={row} selectedWindow={selectedWindow} />
              ))}
            </section>

            <section className="min-w-0 overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02]">
              <div className="max-w-full overflow-x-auto">
                <table className="min-w-[1180px] w-full border-collapse">
                  <thead className="bg-trading-dark-900/80">
                    <tr className="border-b border-white/[0.06]">
                      <th className="w-56 px-4 py-3">
                        <SortHeader label="Profile" sortKey="profile" sort={sort} setSort={setSort} align="left" />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="Mean Final" sortKey="final" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="Return" sortKey="return" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="Worst DD" sortKey="dd" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="DD vs Sentinel" sortKey="ddWorse" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="P(Coll)" sortKey="coll" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="Trades" sortKey="trades" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3">
                        <SortHeader label="Open/Base" sortKey="open" sort={sort} setSort={setSort} />
                      </th>
                      <th className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
                        Shape
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const style = RISK_STYLE[row.color] || RISK_STYLE.green;
                      const presentation = getProfilePresentation(row);
                      const Icon = presentation.icon || Shield;
                      const m = metrics(row, selectedWindow);
                      const p = row.params || {};
                      return (
                        <tr key={row.key} className="border-b border-white/[0.04] hover:bg-white/[0.035]">
                          <td className="px-4 py-3 align-top">
                            <div className="flex items-start gap-2">
                              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${style.text}`} />
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold text-gray-100">{row.name}</span>
                                  <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${style.soft}`}>
                                    {presentation.label || row.risk}
                                  </span>
                                </div>
                                <div className="mt-1 max-w-[260px] truncate text-xs text-gray-500">{row.candidate}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right align-top font-semibold text-gray-100">{fmtMoneyCompact(m.mean_final)}</td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.return_pct)}`}>{fmtCompactPct(m.return_pct)}</td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.worst_dd_pct, true)}`}>{fmtPct(m.worst_dd_pct)}</td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.dd_worse_pp, true)}`}>{fmtPp(m.dd_worse_pp)}</td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.p_coll_pct, true)}`}>{fmtPct(m.p_coll_pct)}</td>
                          <td className="px-4 py-3 text-right align-top text-gray-200">
                            <div>{fmtNumber((num(m.call_trades) || 0) + (num(m.put_trades) || 0))}</div>
                            <div className="mt-1 text-xs text-gray-500">C {fmtNumber(m.call_trades)} / P {fmtNumber(m.put_trades)}</div>
                          </td>
                          <td className="px-4 py-3 text-right align-top text-gray-200">
                            <div>{fmtPct(m.avg_open_premium_base_pct)}</div>
                            <div className="mt-1 text-xs text-gray-500">C {fmtPct(m.avg_call_open_premium_base_pct)} / P {fmtPct(m.avg_put_open_premium_base_pct)}</div>
                          </td>
                          <td className="px-4 py-3 align-top text-xs text-gray-500">
                            <div>slots {p.max_positions ?? '-'} / calls {p.call_max ?? '-'} / puts {p.put_max ?? '-'}</div>
                            <div className="mt-1">caps {fmtPct((p.gross_cap || 0) * 100, 0)} / {fmtPct((p.call_cap || 0) * 100, 0)} / {fmtPct((p.put_cap || 0) * 100, 0)}</div>
                            <div className="mt-1">refs {p.call_ref ?? '-'} / {p.put_ref ?? '-'} / floor {fmtPct((p.sat_floor || 0) * 100, 0)}</div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
