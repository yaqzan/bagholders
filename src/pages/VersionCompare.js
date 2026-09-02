import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, BarChart2, CheckCircle, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import PortfolioProfileToggle from '../components/PortfolioProfileToggle';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const WINDOW_LABELS = {
  '22_now': '22-now',
  '2020_now': '2020-now',
  covid_crash_2020: '2020 crash',
  covid_cycle_2020_2021: '2020-2021',
  year_2020: '2020',
  year_2021: '2021',
  year_2022: '2022',
  year_2023: '2023',
  year_2024: '2024',
  year_2025: '2025',
  year_2026_ytd: '2026 YTD',
};

const DEFAULT_WINDOW = '22_now';

function num(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value);
}

function metric(row, windowName, key) {
  return row?.windows?.[windowName]?.metrics?.[key] ?? null;
}

function scoring(row, key) {
  return row?.scoring?.[key] ?? null;
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

function fmtPp(value, digits = 1, signed = true) {
  const n = num(value);
  if (n === null) return '-';
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}pp`;
}

function fmtLogMultiple(value) {
  const n = num(value);
  if (n === null) return '-';
  if (Math.abs(n) < 4) return `${Math.pow(10, n).toFixed(2)}x`;
  return `10^${n.toFixed(2)}x`;
}

function fmtCompactPct(value) {
  const n = num(value);
  if (n === null) return '-';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${n >= 0 ? '+' : ''}${n.toExponential(2)}%`;
  if (abs >= 1e6) return `${n >= 0 ? '+' : '-'}${(abs / 1e6).toFixed(2)}M%`;
  if (abs >= 1e3) return `${n >= 0 ? '+' : '-'}${(abs / 1e3).toFixed(1)}k%`;
  return fmtPct(n, 1, true);
}

function fmtDate(value) {
  if (!value) return '-';
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtShortDate(value) {
  if (!value) return '-';
  const d = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function fmtMilestone(value) {
  if (!value?.hit_date) return '-';
  return `${fmtNumber(value.calendar_days)}d`;
}

function fmtSidePair(left, right, formatter = fmtNumber, digits = 1) {
  return `C ${formatter(left, digits)} / P ${formatter(right, digits)}`;
}

// Win rate is already a percent; tint vs a rough break-even so the eye can scan quality.
function wrTone(value, breakeven = 50) {
  const n = num(value);
  if (n === null) return 'text-gray-500';
  return n >= breakeven ? 'text-trading-green-300' : 'text-trading-red-300';
}

function valueClass(value, inverse = false) {
  const n = num(value);
  if (n === null) return 'text-gray-500';
  const good = inverse ? n < 0 : n > 0;
  return good ? 'text-trading-green-300' : 'text-trading-red-300';
}

function deltaClass(value) {
  const n = num(value);
  if (n === null) return 'text-gray-500';
  if (Math.abs(n) < 0.05) return 'text-gray-300';
  return n > 0 ? 'text-trading-green-300' : 'text-trading-red-300';
}

function rowLabel(row) {
  return row?.version?.label || row?.version_profile_label || '-';
}

function SortHeader({ label, hint, sortKey, sort, setSort, align = 'right', defaultDirection = 'desc' }) {
  const active = sort.key === sortKey;
  const nextDirection = active ? (sort.direction === 'desc' ? 'asc' : 'desc') : defaultDirection;
  return (
    <button
      type="button"
      onClick={() => setSort({ key: sortKey, direction: nextDirection })}
      title={hint || undefined}
      className={`group flex w-full flex-col gap-0.5 ${align === 'right' ? 'items-end text-right' : 'items-start text-left'}`}
    >
      <span className={`flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.14em] ${active ? 'text-gray-200' : 'text-gray-500 group-hover:text-gray-300'}`}>
        <span>{label}</span>
        <span className={`text-[9px] ${active ? 'text-trading-green-300' : 'text-gray-700 group-hover:text-gray-500'}`}>
          {active ? (sort.direction === 'desc' ? '▼' : '▲') : '⇅'}
        </span>
      </span>
      {hint && <span className="text-[9px] font-normal normal-case tracking-normal text-gray-600">{hint}</span>}
    </button>
  );
}

function GroupHeader({ label, caption, cols, accent }) {
  return (
    <th
      colSpan={cols}
      className={`border-l border-white/[0.06] px-4 pt-3 pb-1 text-left ${accent ? 'bg-trading-green-300/[0.04]' : ''}`}
    >
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-400">{label}</div>
      {caption && <div className="text-[9px] font-normal text-gray-600">{caption}</div>}
    </th>
  );
}

function DetailStat({ label, value, sub }) {
  return (
    <div className="min-w-[120px]">
      <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-gray-600">{label}</div>
      <div className="mt-0.5 text-sm text-gray-200">{value}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

function buildAccessors(windowName, deltas) {
  return {
    version: (row) => row.version?.id ?? 0,
    quality: (row) => scoring(row, 'q_pertrade') ?? scoring(row, 'call_wr75'),
    tierwr: (row) => scoring(row, 'call_wr75'),
    supply: (row) => scoring(row, 'call_n75'),
    delta: (row) => deltas.get(row.row_key)?.wr ?? null,
    compound: (row) => metric(row, windowName, 'log10_equity_multiple'),
    return: (row) => metric(row, windowName, 'total_return_pct'),
    monthly: (row) => metric(row, windowName, 'avg_monthly_return_pct'),
    dd: (row) => metric(row, windowName, 'max_dd_pct'),
    winrate: (row) => metric(row, windowName, 'tp_rate'),
    signals: (row) => metric(row, windowName, 'raw_signals_per_day'),
    hydration: (row) => metric(row, windowName, 'hydration_gap_per_day'),
    time1m: (row) => metric(row, windowName, 'time_to_1m')?.calendar_days,
  };
}

export default function VersionCompare() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedWindow, setSelectedWindow] = useState(DEFAULT_WINDOW);
  // Newest-first reads as a changelog of ships; every metric column re-sorts.
  const [sort, setSort] = useState({ key: 'version', direction: 'desc' });
  const [expanded, setExpanded] = useState(() => new Set());
  const [includeRetired, setIncludeRetired] = useState(false);
  const [profileKey, setProfileKey] = useState(null);

  const toggleRow = useCallback((key) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const fetchData = useCallback(async (withRetired) => {
    setLoading(true);
    setError(null);
    try {
      const bucket = Math.floor(Date.now() / 30000);
      const retired = withRetired ? '&include_retired=1' : '';
      const response = await fetch(`${API_BASE_URL}/api/algorithm/versions/compare?_t=${bucket}${retired}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      setData(payload);
    } catch (err) {
      setError(err.message || 'Unable to load version comparison');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData(includeRetired);
  }, [fetchData, includeRetired]);

  // Default the profile selector to the live default (Apex) once the payload lands.
  useEffect(() => {
    if (!profileKey && data) {
      setProfileKey(data.version_semantics?.default_portfolio_profile || 'apex');
    }
  }, [data, profileKey]);

  const windows = useMemo(() => {
    const byName = new Map();
    for (const window of data?.windows || []) {
      if (window?.name) byName.set(window.name, window);
    }
    for (const row of data?.rows || []) {
      for (const [name, payload] of Object.entries(row.windows || {})) {
        if (!byName.has(name)) {
          byName.set(name, { ...(payload.window || {}), name });
        }
      }
    }
    return Array.from(byName.values());
  }, [data]);

  // One row per version: the page-level profile toggle picks the post-score layer.
  const profileRows = useMemo(() => (
    (data?.rows || []).filter((row) => row.portfolio_profile_key === (profileKey || 'apex'))
  ), [data, profileKey]);

  const activeRow = useMemo(() => (
    profileRows.find((row) => row.version?.id === data?.active_version_id) || null
  ), [profileRows, data]);

  // Δ vs active — the "should I care about this version" column. Per-trade WR15
  // (q_pertrade when the scorecard has it, else 75+ cumulative) + 75+ supply.
  const deltas = useMemo(() => {
    const map = new Map();
    if (!activeRow) return map;
    const aQ = scoring(activeRow, 'q_pertrade');
    const aWr = scoring(activeRow, 'call_wr75');
    const aN = num(scoring(activeRow, 'call_n75'));
    for (const row of profileRows) {
      if (row.version?.id === activeRow.version?.id) continue;
      const q = scoring(row, 'q_pertrade');
      const wr = scoring(row, 'call_wr75');
      const n = num(scoring(row, 'call_n75'));
      const wrDelta = (q !== null && aQ !== null)
        ? q - aQ
        : (wr !== null && aWr !== null ? wr - aWr : null);
      const nDelta = (n !== null && aN) ? ((n / aN) - 1) * 100 : null;
      map.set(row.row_key, {
        wr: wrDelta,
        wrBasis: q !== null && aQ !== null ? 'per-trade WR15' : '75+ WR15',
        n: nDelta,
      });
    }
    return map;
  }, [profileRows, activeRow]);

  useEffect(() => {
    if (!profileRows.length) return;
    const selectedHasMetrics = profileRows.some((row) => metric(row, selectedWindow, 'log10_equity_multiple') !== null);
    if (selectedHasMetrics) return;
    const fallback = windows.find((window) => (
      profileRows.some((row) => metric(row, window.name, 'log10_equity_multiple') !== null)
    ));
    if (fallback && fallback.name !== selectedWindow) {
      setSelectedWindow(fallback.name);
    }
  }, [profileRows, selectedWindow, windows]);

  const rows = useMemo(() => {
    const accessors = buildAccessors(selectedWindow, deltas);
    return [...profileRows].sort((a, b) => {
      const an = num(accessors[sort.key]?.(a));
      const bn = num(accessors[sort.key]?.(b));
      if (an === null && bn === null) {
        return (b.version?.id || 0) - (a.version?.id || 0);
      }
      if (an === null) return 1;
      if (bn === null) return -1;
      return sort.direction === 'desc' ? bn - an : an - bn;
    });
  }, [profileRows, selectedWindow, sort, deltas]);

  const best = useMemo(() => {
    const withMetrics = profileRows.filter((row) => metric(row, selectedWindow, 'log10_equity_multiple') !== null);
    const withQuality = profileRows.filter((row) => num(scoring(row, 'q_pertrade') ?? scoring(row, 'call_wr75')) !== null);
    const with1m = withMetrics.filter((row) => num(metric(row, selectedWindow, 'time_to_1m')?.calendar_days) !== null);
    return {
      quality: withQuality.length ? withQuality.reduce((a, b) => (
        num(scoring(b, 'q_pertrade') ?? scoring(b, 'call_wr75')) > num(scoring(a, 'q_pertrade') ?? scoring(a, 'call_wr75')) ? b : a
      )) : null,
      compound: withMetrics.length ? withMetrics.reduce((a, b) => (
        num(metric(b, selectedWindow, 'log10_equity_multiple')) > num(metric(a, selectedWindow, 'log10_equity_multiple')) ? b : a
      )) : null,
      dd: withMetrics.length ? withMetrics.reduce((a, b) => (
        num(metric(b, selectedWindow, 'max_dd_pct')) < num(metric(a, selectedWindow, 'max_dd_pct')) ? b : a
      )) : null,
      fastest1m: with1m.length ? with1m.reduce((a, b) => (
        num(metric(b, selectedWindow, 'time_to_1m')?.calendar_days) < num(metric(a, selectedWindow, 'time_to_1m')?.calendar_days) ? b : a
      )) : null,
    };
  }, [profileRows, selectedWindow]);

  const overlay = data?.portfolio_overlay;
  const activeLabel = activeRow?.version?.label
    || data?.rows?.find((row) => row.version?.id === data.active_version_id)?.version?.label;
  const selectedProfile = useMemo(() => (
    (data?.portfolio_profiles?.profiles || []).find((p) => p.key === (profileKey || 'apex')) || null
  ), [data, profileKey]);
  const retiredVersionCount = useMemo(() => (
    new Set((data?.rows || []).filter((row) => row.version?.retired).map((row) => row.version.id)).size
  ), [data]);
  const windowLabel = WINDOW_LABELS[selectedWindow] || selectedWindow;
  const COL_COUNT = 12;

  return (
    <div className="h-full overflow-y-auto bg-trading-dark-950 text-gray-100">
      <div className="flex flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-trading-green-300">
              <BarChart2 className="h-4 w-4" />
              Algorithm Versions
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-gray-100 sm:text-3xl">
              Version Compare
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-400">
              Honest-era versions (v69+) vs the active {activeLabel || '-'}. One row per scoring version under the
              selected <span className="text-gray-300">portfolio profile</span>; <span className="text-gray-300">scoring quality</span> is
              version-level (5y WR15), <span className="text-gray-300">portfolio</span> &amp; <span className="text-gray-300">efficiency</span> follow
              the selected window. Pre-v69 versions are retired (score rows purged 2026-06-12; research packs remain).
            </p>
          </div>
          <button
            type="button"
            onClick={() => fetchData(includeRetired)}
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

        <section className="grid gap-3 md:grid-cols-4">
          <div className="rounded-md border border-trading-green-300/25 bg-trading-green-300/[0.05] p-4">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-trading-green-300">Best Scoring · WR15</div>
            <div className="mt-2 text-xl font-semibold text-gray-100">{rowLabel(best.quality)}</div>
            <div className="mt-1 text-sm text-gray-400">
              {scoring(best.quality, 'q_pertrade') != null
                ? `per-trade ${fmtPct(scoring(best.quality, 'q_pertrade'))}`
                : `75+ ${fmtPct(scoring(best.quality, 'call_wr75'))}`}
              <span className="text-gray-600"> · N75 </span>
              {fmtNumber(scoring(best.quality, 'call_n75'))}
            </div>
          </div>
          <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Best Compound · {windowLabel}</div>
            <div className="mt-2 text-xl font-semibold text-trading-green-300">{rowLabel(best.compound)}</div>
            <div className="mt-1 text-sm text-gray-400">{fmtLogMultiple(metric(best.compound, selectedWindow, 'log10_equity_multiple'))}</div>
          </div>
          <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Lowest DD · {windowLabel}</div>
            <div className="mt-2 text-xl font-semibold text-trading-green-300">{rowLabel(best.dd)}</div>
            <div className="mt-1 text-sm text-gray-400">{fmtPct(metric(best.dd, selectedWindow, 'max_dd_pct'))}</div>
          </div>
          <div className="rounded-md border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-500">Fastest 1M · {windowLabel}</div>
            <div className="mt-2 text-xl font-semibold text-trading-green-300">{rowLabel(best.fastest1m)}</div>
            <div className="mt-1 text-sm text-gray-400">
              {fmtMilestone(metric(best.fastest1m, selectedWindow, 'time_to_1m'))}
            </div>
          </div>
        </section>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-600">Profile</span>
            <PortfolioProfileToggle
              profiles={data?.portfolio_profiles?.profiles}
              value={profileKey || 'apex'}
              onChange={setProfileKey}
            />
          </div>
          <span className="h-5 w-px bg-white/[0.08]" />
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-600">Window</span>
            {windows.map((window) => (
              <button
                key={window.name}
                type="button"
                onClick={() => setSelectedWindow(window.name)}
                className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                  selectedWindow === window.name
                    ? 'border-trading-green-300/50 bg-trading-green-300/10 text-trading-green-200'
                    : 'border-white/[0.08] bg-white/[0.02] text-gray-400 hover:bg-white/[0.05] hover:text-gray-200'
                }`}
              >
                {WINDOW_LABELS[window.name] || window.label}
              </button>
            ))}
          </div>
          <span className="h-5 w-px bg-white/[0.08]" />
          <button
            type="button"
            onClick={() => setIncludeRetired((value) => !value)}
            title="Retired = pre-v69. Score rows were purged 2026-06-12 (inflated weekly look-ahead era); comparison data comes from archived research packs."
            className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors ${
              includeRetired
                ? 'border-amber-400/40 bg-amber-400/10 text-amber-200'
                : 'border-white/[0.08] bg-white/[0.02] text-gray-400 hover:bg-white/[0.05] hover:text-gray-200'
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${includeRetired ? 'bg-amber-400' : 'bg-gray-600'}`} />
            {includeRetired ? `Showing retired packs (${retiredVersionCount})` : 'Show retired (pre-v69)'}
          </button>
        </div>

        <section className="overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02]">
          {loading ? (
            <div className="flex h-64 items-center justify-center text-sm text-gray-500">Loading version metrics...</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead className="bg-trading-dark-900/80">
                  <tr className="border-b border-white/[0.04]">
                    <th rowSpan={2} className="w-56 px-4 py-3 align-bottom">
                      <SortHeader label="Version" sortKey="version" sort={sort} setSort={setSort} align="left" />
                    </th>
                    <GroupHeader label="Scoring Quality" caption="version-level · 5y WR15 (Stage 1)" cols={3} accent />
                    <GroupHeader label="Δ vs Active" caption={activeLabel || '-'} cols={1} />
                    <GroupHeader label="Portfolio Outcome" caption={`${windowLabel} · ${selectedProfile?.name || profileKey || ''}`} cols={4} />
                    <GroupHeader label="Capital Efficiency" caption={windowLabel} cols={3} />
                  </tr>
                  <tr className="border-b border-white/[0.06]">
                    <th className="border-l border-white/[0.06] bg-trading-green-300/[0.04] px-4 py-2">
                      <SortHeader label="WR15 Quality" hint="per-trade (scorecard)" sortKey="quality" sort={sort} setSort={setSort} />
                    </th>
                    <th className="bg-trading-green-300/[0.04] px-4 py-2">
                      <SortHeader label="Tier Win%" hint="cumulative WR15" sortKey="tierwr" sort={sort} setSort={setSort} />
                    </th>
                    <th className="bg-trading-green-300/[0.04] px-4 py-2">
                      <SortHeader label="Supply" hint="5y 75+ N" sortKey="supply" sort={sort} setSort={setSort} />
                    </th>
                    <th className="border-l border-white/[0.06] px-4 py-2">
                      <SortHeader label="Δ WR15" hint="vs active · pp" sortKey="delta" sort={sort} setSort={setSort} />
                    </th>
                    <th className="border-l border-white/[0.06] px-4 py-2">
                      <SortHeader label="Compound" sortKey="compound" sort={sort} setSort={setSort} />
                    </th>
                    <th className="px-4 py-2">
                      <SortHeader label="Max DD" sortKey="dd" sort={sort} setSort={setSort} defaultDirection="asc" />
                    </th>
                    <th className="px-4 py-2">
                      <SortHeader label="Monthly" sortKey="monthly" sort={sort} setSort={setSort} />
                    </th>
                    <th className="px-4 py-2">
                      <SortHeader label="Win Rate" hint="realized TP%" sortKey="winrate" sort={sort} setSort={setSort} />
                    </th>
                    <th className="border-l border-white/[0.06] px-4 py-2">
                      <SortHeader label="Signals / Day" sortKey="signals" sort={sort} setSort={setSort} />
                    </th>
                    <th className="px-4 py-2">
                      <SortHeader label="Hydration" hint="unfilled slots/day" sortKey="hydration" sort={sort} setSort={setSort} defaultDirection="asc" />
                    </th>
                    <th className="px-4 py-2">
                      <SortHeader label="1M / 10M" hint="days to milestone" sortKey="time1m" sort={sort} setSort={setSort} defaultDirection="asc" />
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const w = row.windows?.[selectedWindow];
                    const m = w?.metrics || {};
                    const sc = row.scoring || {};
                    const sk = row.skill || null;
                    const isActive = row.version?.id === data?.active_version_id;
                    const ready = row.health?.assessment_complete && row.health?.portfolio_windows_run && w?.ready;
                    const delta = deltas.get(row.row_key);
                    const rowKey = row.row_key || `${row.version?.id}-${row.portfolio_profile_key || 'profile'}`;
                    const isExpanded = expanded.has(rowKey);
                    return (
                      <React.Fragment key={rowKey}>
                        <tr
                          className={`border-b border-white/[0.04] hover:bg-white/[0.035] ${
                            isActive ? 'bg-trading-green-300/[0.045]' : ''
                          } ${isExpanded ? 'bg-white/[0.03]' : ''}`}
                        >
                          <td className="px-4 py-3 align-top">
                            <div className="flex items-start gap-2">
                              <button
                                type="button"
                                onClick={() => toggleRow(rowKey)}
                                aria-label={isExpanded ? 'Collapse details' : 'Expand details'}
                                className="mt-0.5 rounded p-0.5 text-gray-500 hover:bg-white/[0.06] hover:text-gray-200"
                              >
                                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                              </button>
                              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${isActive ? 'bg-trading-green-300' : 'bg-gray-700'}`} />
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold text-gray-100">{rowLabel(row)}</span>
                                  {isActive && (
                                    <span className="rounded border border-trading-green-300/40 bg-trading-green-300/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-trading-green-200">
                                      active
                                    </span>
                                  )}
                                  {row.version?.retired && (
                                    <span
                                      className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] ${
                                        row.version?.honest
                                          ? 'border-white/[0.12] bg-white/[0.04] text-gray-400'
                                          : 'border-amber-400/40 bg-amber-400/10 text-amber-300'
                                      }`}
                                      title={row.version?.honest
                                        ? 'Retired pre-v69 version; metrics from an honest-recalc research pack.'
                                        : 'Retired pre-v69 version; pack metrics carry the weekly look-ahead (~12pp WR overstatement).'}
                                    >
                                      {row.version?.honest ? 'retired' : 'inflated'}
                                    </span>
                                  )}
                                  {ready ? (
                                    <CheckCircle className="h-3.5 w-3.5 text-trading-green-300" />
                                  ) : (
                                    <AlertTriangle className="h-3.5 w-3.5 text-amber-300" title="Pack incomplete for this window (assessment or portfolio windows still pending)" />
                                  )}
                                </div>
                                <div className="mt-1 max-w-[230px] truncate text-xs text-gray-500" title={row.version?.git_message || undefined}>
                                  {row.version?.git_message || '-'}
                                </div>
                                <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-gray-600">
                                  <span className="font-mono">{row.version?.git_commit?.slice(0, 9)}</span>
                                  <span>pack {fmtShortDate(row.generated_at)}</span>
                                  <span>data → {fmtShortDate(row.score_coverage?.max_date)}</span>
                                </div>
                              </div>
                            </div>
                          </td>

                          {/* Scoring Quality */}
                          <td className="border-l border-white/[0.06] bg-trading-green-300/[0.02] px-4 py-3 text-right align-top">
                            <div className={`font-semibold ${wrTone(sc.q_pertrade ?? sc.call_wr75, sc.q_pertrade != null ? 60 : 50)}`}>
                              {sc.q_pertrade != null ? fmtPct(sc.q_pertrade, 1) : fmtPct(sc.call_wr75, 1)}
                            </div>
                            <div className="mt-0.5 text-[10px] text-gray-600">
                              {sc.q_pertrade != null ? 'per-trade WR15' : '75+ WR15 (no scorecard)'}
                            </div>
                            {sc.recency != null && (
                              <div className="mt-1 text-xs text-gray-500">recency {sc.recency.toFixed(2)}</div>
                            )}
                            {sk?.verdict && (() => {
                              const v = sk.verdict;
                              const tone = v === 'SHIP' ? 'bg-trading-green-300/15 text-trading-green-300'
                                : v === 'FLAG' ? 'bg-amber-400/15 text-amber-300'
                                : v === 'BLOCK' ? 'bg-rose-500/15 text-rose-300'
                                : 'bg-white/[0.06] text-gray-400';
                              const tClim = sk.gate?.t_clim, tMom = sk.gate?.t_mom;
                              const tip = '0d verification gate on the live 30dte_apex option payoff. '
                                + 'FLAG = risk-shaper (beats 12-1 momentum, neutral vs climatology) — the known/accepted '
                                + 'profile, NOT a regression. SHIP beats both baselines (t>=2); BLOCK beats neither. '
                                + (sk.oos ? 'Out-of-sample window.' : 'In-sample (forward holdout accumulating).');
                              return (
                                <div className="mt-1.5" title={tip}>
                                  <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide ${tone}`}>
                                    0d {v}
                                  </span>
                                  {(tClim != null || tMom != null) && (
                                    <span className="ml-1 text-[10px] text-gray-500">
                                      vs clim t={tClim != null ? tClim.toFixed(1) : '–'} · mom t={tMom != null ? tMom.toFixed(1) : '–'}
                                    </span>
                                  )}
                                </div>
                              );
                            })()}
                          </td>
                          <td className="bg-trading-green-300/[0.02] px-4 py-3 text-right align-top">
                            <div className={`font-semibold ${wrTone(sc.call_wr75)}`}>
                              75+ {fmtPct(sc.call_wr75)}
                            </div>
                            <div className="mt-1 text-xs text-gray-500">
                              85+ {fmtPct(sc.call_wr85)} · 95+ {fmtPct(sc.call_wr95)}
                            </div>
                            <div className="mt-1 text-xs text-gray-500">
                              P&lt;25 {fmtPct(sc.put_wr_lt25)}
                            </div>
                          </td>
                          <td className="bg-trading-green-300/[0.02] px-4 py-3 text-right align-top">
                            <div className="font-semibold text-gray-200">{fmtNumber(sc.call_n75)}</div>
                            <div className="mt-1 text-xs text-gray-500">
                              85+ {fmtNumber(sc.call_n85)} · 95+ {fmtNumber(sc.call_n95)}
                            </div>
                            {sc.supply_per_day != null && (
                              <div className="mt-1 text-xs text-gray-500">{fmtNumber(sc.supply_per_day, 1)}/d</div>
                            )}
                          </td>

                          {/* Δ vs Active */}
                          <td className="border-l border-white/[0.06] px-4 py-3 text-right align-top">
                            {isActive ? (
                              <div className="text-xs font-semibold uppercase tracking-[0.12em] text-trading-green-300">baseline</div>
                            ) : delta && (delta.wr !== null || delta.n !== null) ? (
                              <>
                                <div className={`font-semibold ${deltaClass(delta.wr)}`}>{fmtPp(delta.wr)}</div>
                                <div className="mt-0.5 text-[10px] text-gray-600">{delta.wrBasis}</div>
                                <div className={`mt-1 text-xs ${deltaClass(delta.n)}`}>
                                  supply {fmtPct(delta.n, 0, true)}
                                </div>
                              </>
                            ) : (
                              <span className="text-gray-600">-</span>
                            )}
                          </td>

                          {/* Portfolio Outcome */}
                          <td className="border-l border-white/[0.06] px-4 py-3 text-right align-top">
                            <div className="font-semibold text-gray-100">{fmtLogMultiple(m.log10_equity_multiple)}</div>
                            <div className={`mt-1 text-xs ${valueClass(m.total_return_pct)}`}>{fmtCompactPct(m.total_return_pct)}</div>
                          </td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.max_dd_pct, true)}`}>
                            <div>{fmtPct(m.max_dd_pct)}</div>
                            {m.max_dd_peak_date && (
                              <div className="mt-1 text-xs font-normal text-gray-500">
                                {fmtShortDate(m.max_dd_peak_date)}&ndash;{fmtShortDate(m.max_dd_trough_date)}
                              </div>
                            )}
                          </td>
                          <td className={`px-4 py-3 text-right align-top font-medium ${valueClass(m.avg_monthly_return_pct)}`}>
                            <div>{fmtCompactPct(m.avg_monthly_return_pct)}</div>
                            <div className="mt-1 text-xs font-normal text-gray-500">
                              med {fmtCompactPct(m.median_monthly_return_pct)}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right align-top">
                            <div className={`font-medium ${wrTone(m.tp_rate)}`}>{fmtPct(m.tp_rate)}</div>
                            <div className="mt-1 text-xs text-gray-500">
                              {fmtSidePair(m.call_tp_rate, m.put_tp_rate, fmtPct)}
                            </div>
                          </td>

                          {/* Capital Efficiency */}
                          <td className="border-l border-white/[0.06] px-4 py-3 text-right align-top text-gray-200">
                            <div>{fmtNumber(m.raw_signals_per_day, 1)}/d</div>
                            <div className="mt-1 text-xs text-gray-500">
                              {fmtSidePair(m.call_raw_signals_per_day, m.put_raw_signals_per_day)}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right align-top text-gray-200">
                            <div>{fmtNumber(m.hydration_gap_per_day, 1)}/d</div>
                            <div className="mt-1 text-xs text-gray-500">
                              tgt {fmtNumber(m.target_entries_per_day, 1)} · got {fmtNumber(m.accepted_entries_per_day, 1)}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right align-top text-gray-200">
                            <div>{fmtMilestone(m.time_to_1m)} <span className="text-gray-600">/</span> {fmtMilestone(m.time_to_10m)}</div>
                            <div className="mt-1 text-xs text-gray-500">
                              {m.time_to_1m?.hit_date ? fmtDate(m.time_to_1m.hit_date) : 'no 1M'}
                            </div>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr className="border-b border-white/[0.06] bg-trading-dark-900/40">
                            <td colSpan={COL_COUNT} className="px-6 py-4">
                              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-gray-600">
                                Portfolio mechanics · {windowLabel}
                              </div>
                              <div className="flex flex-wrap gap-x-8 gap-y-3">
                                <DetailStat
                                  label="Trades"
                                  value={fmtNumber(m.n_trades)}
                                  sub={`C ${fmtNumber(m.call_trades)} / P ${fmtNumber(m.put_trades)}`}
                                />
                                <DetailStat
                                  label="Avg Open Pos"
                                  value={fmtNumber(m.avg_open_positions, 1)}
                                  sub={fmtSidePair(m.avg_call_open_positions, m.avg_put_open_positions)}
                                />
                                <DetailStat
                                  label="Avg Hold Bars"
                                  value={fmtNumber(m.avg_hold_bars, 1)}
                                  sub={fmtSidePair(m.call_avg_hold_bars, m.put_avg_hold_bars)}
                                />
                                <DetailStat
                                  label="Slot Use"
                                  value={fmtPct(m.avg_slot_utilization_pct)}
                                  sub={fmtSidePair(m.call_slot_utilization_pct, m.put_slot_utilization_pct, fmtPct)}
                                />
                                <DetailStat
                                  label="Pool Full Rate"
                                  value={fmtPct(m.pool_full_rate)}
                                  sub={fmtSidePair(m.call_pool_full_rate, m.put_pool_full_rate, fmtPct)}
                                />
                                <DetailStat
                                  label="WR15 Utility (vol)"
                                  value={fmtNumber(sc.utility_wr)}
                                  sub={`N ${fmtNumber(sc.n)} · C ${fmtNumber(sc.call_n)} / P ${fmtNumber(sc.put_n)}`}
                                />
                                <DetailStat
                                  label="Score Coverage"
                                  value={fmtNumber(row.score_coverage?.rows)}
                                  sub={`${fmtDate(row.score_coverage?.min_date)}–${fmtDate(row.score_coverage?.max_date)}`}
                                />
                                {sc.supply_per_day != null && (
                                  <DetailStat
                                    label="Supply / Day"
                                    value={fmtNumber(sc.supply_per_day, 1)}
                                    sub={sc.supply_cv != null ? `CV ${sc.supply_cv.toFixed(2)}` : undefined}
                                  />
                                )}
                                {sc.call_dry_day_rate != null && (
                                  <DetailStat
                                    label="Call Dry Days"
                                    value={fmtPct(sc.call_dry_day_rate * 100, 1)}
                                    sub={sc.recycle_coverage != null ? `recycle-cov ${sc.recycle_coverage.toFixed(2)}` : undefined}
                                  />
                                )}
                                {row.derived_portfolio?.tier_alloc && (
                                  <DetailStat
                                    label="PRF Matched Sizing"
                                    value={`${[row.derived_portfolio.tier_alloc.ultra, row.derived_portfolio.tier_alloc.top, row.derived_portfolio.tier_alloc.mid, row.derived_portfolio.tier_alloc.low].map((a) => (a != null ? Math.round(a * 1000) / 10 : '–')).join('/')}%`}
                                    sub={`ovf ${row.derived_portfolio.tier_overflow != null ? `${Math.round(row.derived_portfolio.tier_overflow * 1000) / 10}%` : '–'} · ${row.derived_portfolio.min_call_threshold != null ? `${row.derived_portfolio.min_call_threshold}+` : 'no tier'} · instrument`}
                                  />
                                )}
                                {row.mc ? (
                                  <>
                                    <DetailStat
                                      label="MC Dominance"
                                      value={`${(row.mc.chosen_dominance * 100).toFixed(0)}% · #${row.mc.rank}`}
                                      sub={`spd ${(row.mc.speed_dominance * 100).toFixed(0)}% / saf ${(row.mc.safety_dominance * 100).toFixed(0)}% · ${(row.mc.reach_fraction * 100).toFixed(0)}% reach`}
                                    />
                                    <DetailStat
                                      label="MC Days→1M / Worst DD"
                                      value={`${row.mc.median_cal_days_to_1m != null ? `${Math.round(row.mc.median_cal_days_to_1m)}d` : '-'} / ${fmtPct(row.mc.dd_worst_pct)}`}
                                      sub={row.mc.collapse_fraction > 0
                                        ? `${(row.mc.collapse_fraction * 100).toFixed(0)}% collapse · N=${fmtNumber(row.mc.n_iter)}`
                                        : `no collapse · N=${fmtNumber(row.mc.n_iter)}`}
                                    />
                                  </>
                                ) : (
                                  <DetailStat label="MC Dominance" value="-" sub="no scoped MC run for this version × profile" />
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
              {!rows.length && (
                <div className="flex h-40 items-center justify-center border-t border-white/[0.06] px-4 text-center text-sm text-gray-500">
                  No versions with research packs under the {selectedProfile?.name || profileKey} profile.
                </div>
              )}
            </div>
          )}
        </section>

        {overlay && (
          <p className="text-xs leading-5 text-gray-600">
            Portfolio profiles (Sentinel/Core/Apex) are a post-score layer — `Score.overall` and `ALGORITHM_VERSION` never
            encode a profile. Active overlay: <span className="text-gray-400">{overlay.label}</span>.
          </p>
        )}
      </div>
    </div>
  );
}
