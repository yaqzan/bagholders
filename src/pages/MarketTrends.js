import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, BarChart2, ChevronLeft, ChevronRight, LineChart, RefreshCw } from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  BarController,
  LineController,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Chart, Line } from 'react-chartjs-2';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  BarController,
  LineController,
  Tooltip,
  Legend,
  Filler
);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';

const buildApiUrl = (endpoint) => {
  const sep = endpoint.includes('?') ? '&' : '?';
  const bucket = Math.floor(Date.now() / 30000);
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}${sep}_t=${bucket}`;
};

const FALLBACK_PARAMS = {
  level50_weight: 0.40,
  level200_weight: 0.15,
  d1_weight: 1.00,
  d5_weight: 0.55,
  d15_weight: 0.35,
  range10_weight: 0.35,
  range30_weight: 0.35,
  center: 0.425,
  scale: 1.024,
};

const TERM_DEFS = [
  {
    key: 'level50',
    label: 'ETF above EMA50',
    source: (r) => pct(r?.pct_above_ema50, 0),
    tip: 'Current sector ETF participation above intermediate trend.',
  },
  {
    key: 'level200',
    label: 'ETF above EMA200',
    source: (r) => pct(r?.pct_above_ema200, 0),
    tip: 'Longer-term sector trend participation.',
  },
  {
    key: 'd1',
    label: '1D breadth shift',
    source: (r) => signedNumber(r?.breadth_1d_change, 0),
    tip: 'One-session sector participation change.',
  },
  {
    key: 'd5',
    label: '5D breadth shift',
    source: (r) => signedNumber(r?.breadth_5d_change, 0),
    tip: 'One-week sector participation change.',
  },
  {
    key: 'd15',
    label: '15D breadth shift',
    source: (r) => signedNumber(r?.breadth_15d_change, 0),
    tip: 'Three-week sector participation change.',
  },
  {
    key: 'range10',
    label: '10D range position',
    source: (r) => signedNumber(r?.breadth_10d_position, 0),
    tip: 'Where current sector breadth sits inside its 10-day range.',
  },
  {
    key: 'range30',
    label: '30D range position',
    source: (r) => signedNumber(r?.breadth_30d_position, 0),
    tip: 'Where current sector breadth sits inside its 30-day range.',
  },
];

function num(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
  return Number(v);
}

function fixed(v, dp = 1) {
  const n = num(v);
  return n === null ? '-' : n.toFixed(dp);
}

function pct(v, dp = 1) {
  const n = num(v);
  return n === null ? '-' : `${n.toFixed(dp)}%`;
}

function signedNumber(v, dp = 1) {
  const n = num(v);
  if (n === null) return '-';
  return `${n >= 0 ? '+' : ''}${n.toFixed(dp)}`;
}

function signedPct(v, dp = 1) {
  const n = num(v);
  if (n === null) return '-';
  return `${n >= 0 ? '+' : ''}${n.toFixed(dp)}%`;
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatAxisDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function waveTextClass(score) {
  const n = num(score);
  if (n === null) return 'text-gray-600';
  if (n >= 65) return 'text-trading-green-300';
  if (n >= 45) return 'text-gray-200';
  if (n >= 35) return 'text-amber-300';
  return 'text-trading-red-400';
}

function pctTextClass(value, inverse = false) {
  const n = num(value);
  if (n === null) return 'text-gray-600';
  if (Math.abs(n) < 0.01) return 'text-gray-400';
  const good = inverse ? n < 0 : n > 0;
  return good ? 'text-trading-green-400' : 'text-trading-red-400';
}

function marketWaveState(score) {
  const n = num(score);
  if (n === null) return '-';
  if (n < 20) return 'Severe Stress';
  if (n < 35) return 'Stress';
  if (n < 45) return 'Weak';
  if (n < 55) return 'Neutral';
  if (n < 65) return 'Firm';
  if (n < 80) return 'Repair';
  return 'Strong Repair';
}

function compactYearWindow(years, selectedYear, limit = 5) {
  if (!years.length) return [];
  const idx = Math.max(0, years.indexOf(selectedYear));
  const leftPad = Math.floor(limit / 2);
  const start = Math.max(0, Math.min(idx - leftPad, years.length - limit));
  return years.slice(start, start + limit);
}

function buildContributions(row, params) {
  if (!row) return [];
  const terms = row.market_wave_terms || {};
  const base = TERM_DEFS.map((term) => ({
    ...term,
    contribution: num(terms[term.key]) ?? 0,
    sourceText: term.source(row),
  }));
  return [
    ...base,
    {
      key: 'center',
      label: 'Calibration center',
      contribution: -Math.abs(num(params.center) ?? FALLBACK_PARAMS.center),
      sourceText: fixed(params.center, 3),
      tip: 'Fixed center subtracted before tanh scaling.',
      isCenter: true,
    },
  ];
}

const waveZonesPlugin = {
  id: 'marketWaveZones',
  beforeDatasetsDraw(chart) {
    const y = chart.scales?.y;
    if (!chart.chartArea || !y) return;
    const { ctx, chartArea } = chart;
    const { left, right } = chartArea;
    ctx.save();
    const y35 = y.getPixelForValue(35);
    const y65 = y.getPixelForValue(65);
    const y0 = y.getPixelForValue(0);
    const y100 = y.getPixelForValue(100);
    ctx.fillStyle = 'rgba(201, 132, 132, 0.08)';
    ctx.fillRect(left, y35, right - left, y0 - y35);
    ctx.fillStyle = 'rgba(143, 178, 134, 0.08)';
    ctx.fillRect(left, y100, right - left, y65 - y100);
    ctx.restore();
  },
};

const commonLegend = {
  labels: {
    color: '#a1a1aa',
    boxWidth: 10,
    boxHeight: 2,
    usePointStyle: true,
    pointStyle: 'line',
    font: { size: 11 },
  },
};

function MetricCard({ label, value, sub, tone = 'text-gray-100', icon: Icon }) {
  return (
    <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] uppercase text-gray-600">{label}</div>
          <div className={`mt-1 text-[20px] font-semibold leading-none tabular-nums ${tone}`}>{value}</div>
          {sub && <div className="mt-1 text-[11px] leading-tight text-gray-500">{sub}</div>}
        </div>
        {Icon && <Icon className="mt-0.5 h-4 w-4 shrink-0 text-gray-600" />}
      </div>
    </div>
  );
}

function YearCycler({ years, selectedYear, setSelectedYear }) {
  const isNarrow = typeof window !== 'undefined' && window.innerWidth < 420;
  const visibleYears = compactYearWindow(years, selectedYear, isNarrow ? 3 : 5);
  const idx = years.indexOf(selectedYear);
  const canPrev = idx > 0;
  const canNext = idx >= 0 && idx < years.length - 1;

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        aria-label="Previous year"
        disabled={!canPrev}
        onClick={() => canPrev && setSelectedYear(years[idx - 1])}
        className="rounded-md border border-white/[0.08] bg-white/[0.03] p-2 text-gray-400 transition-colors hover:bg-white/[0.06] hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-35"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <div className="flex overflow-hidden rounded-md border border-white/[0.08] bg-white/[0.03]">
        {visibleYears.map((year) => (
          <button
            key={year}
            type="button"
            onClick={() => setSelectedYear(year)}
            className={`px-3 py-2 text-[12px] tabular-nums transition-colors ${
              year === selectedYear
                ? 'bg-trading-green-300/12 text-trading-green-300'
                : 'text-gray-500 hover:bg-white/[0.04] hover:text-gray-200'
            }`}
          >
            {year}
          </button>
        ))}
      </div>
      <button
        type="button"
        aria-label="Next year"
        disabled={!canNext}
        onClick={() => canNext && setSelectedYear(years[idx + 1])}
        className="rounded-md border border-white/[0.08] bg-white/[0.03] p-2 text-gray-400 transition-colors hover:bg-white/[0.06] hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-35"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

function WaveVsSpyChart({ rows, selectedIndex, setSelectedIndex }) {
  const labels = rows.map((row) => formatAxisDate(row.date));
  const data = {
    labels,
    datasets: [
      {
        label: 'Market Wave',
        data: rows.map((row) => row.market_wave_score),
        yAxisID: 'y',
        borderColor: '#A7C4A0',
        backgroundColor: 'rgba(167, 196, 160, 0.12)',
        borderWidth: 2.5,
        tension: 0.25,
        fill: true,
        pointRadius: rows.map((_, i) => (i === selectedIndex ? 4 : 0)),
        pointHoverRadius: 5,
        pointBackgroundColor: '#F4F4F5',
        pointBorderColor: '#A7C4A0',
        pointBorderWidth: 2,
      },
      {
        label: 'SPY YTD %',
        data: rows.map((row) => row.spy_ytd_pct),
        yAxisID: 'y1',
        borderColor: '#7DA4C4',
        backgroundColor: 'transparent',
        borderWidth: 1.7,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: true,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: commonLegend,
      tooltip: {
        backgroundColor: '#111113',
        borderColor: 'rgba(255,255,255,0.10)',
        borderWidth: 1,
        titleColor: '#f4f4f5',
        bodyColor: '#d4d4d8',
        cornerRadius: 6,
        callbacks: {
          title: (items) => {
            const row = rows[items?.[0]?.dataIndex];
            return row ? `${formatDate(row.date)} | ${row.market_wave_state || marketWaveState(row.market_wave_score)}` : '';
          },
          label: (ctx) => {
            const row = rows[ctx.dataIndex];
            if (ctx.dataset.label === 'Market Wave') {
              return `Market Wave: ${fixed(row?.market_wave_score, 1)}`;
            }
            return `SPY YTD: ${signedPct(row?.spy_ytd_pct, 1)}`;
          },
          afterBody: (items) => {
            const row = rows[items?.[0]?.dataIndex];
            if (!row) return [];
            return [
              `ETF > EMA50: ${pct(row.pct_above_ema50, 0)}`,
              `5D breadth: ${signedNumber(row.breadth_5d_change, 0)}`,
              `SPY 1D: ${signedPct(row.spy_1d_pct, 2)}`,
            ];
          },
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          color: '#71717a',
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
          font: { size: 10 },
        },
      },
      y: {
        min: 0,
        max: 100,
        position: 'left',
        grid: {
          color: (ctx) => ([35, 50, 65].includes(ctx.tick.value) ? '#3f3f46' : 'rgba(255,255,255,0.03)'),
          drawBorder: false,
        },
        ticks: {
          color: '#a1a1aa',
          stepSize: 25,
          font: { size: 10 },
        },
      },
      y1: {
        position: 'right',
        grid: { drawOnChartArea: false, drawBorder: false },
        ticks: {
          color: '#7DA4C4',
          callback: (value) => `${value}%`,
          font: { size: 10 },
        },
      },
    },
    onHover: (_event, active) => {
      if (active?.length) setSelectedIndex(active[0].index);
    },
    onClick: (_event, active) => {
      if (active?.length) setSelectedIndex(active[0].index);
    },
  };

  return (
    <div className="h-[300px] min-h-[260px]">
      <Line data={data} options={options} plugins={[waveZonesPlugin]} />
    </div>
  );
}

function InputsChart({ rows, selectedIndex, setSelectedIndex }) {
  const data = {
    labels: rows.map((row) => formatAxisDate(row.date)),
    datasets: [
      {
        type: 'line',
        label: 'ETF > EMA50',
        data: rows.map((row) => row.pct_above_ema50),
        borderColor: '#A7C4A0',
        backgroundColor: 'transparent',
        borderWidth: 1.8,
        tension: 0.25,
        pointRadius: 0,
      },
      {
        type: 'line',
        label: 'ETF > EMA200',
        data: rows.map((row) => row.pct_above_ema200),
        borderColor: '#D6B362',
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        tension: 0.25,
        pointRadius: 0,
        spanGaps: true,
      },
      {
        type: 'line',
        label: 'Avg RSI',
        data: rows.map((row) => row.avg_rsi),
        borderColor: '#7DA4C4',
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        tension: 0.25,
        pointRadius: 0,
        spanGaps: true,
      },
      {
        type: 'line',
        label: 'Market Wave',
        data: rows.map((row) => row.market_wave_score),
        borderColor: 'rgba(244,244,245,0.55)',
        backgroundColor: 'transparent',
        borderWidth: 1,
        borderDash: [4, 4],
        tension: 0.25,
        pointRadius: rows.map((_, i) => (i === selectedIndex ? 3 : 0)),
        pointBackgroundColor: '#f4f4f5',
        pointBorderColor: '#f4f4f5',
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: commonLegend,
      tooltip: {
        backgroundColor: '#111113',
        borderColor: 'rgba(255,255,255,0.10)',
        borderWidth: 1,
        titleColor: '#f4f4f5',
        bodyColor: '#d4d4d8',
        cornerRadius: 6,
        callbacks: {
          title: (items) => formatDate(rows[items?.[0]?.dataIndex]?.date),
          label: (ctx) => `${ctx.dataset.label}: ${fixed(ctx.parsed.y, 1)}${ctx.dataset.label.includes('ETF') ? '%' : ''}`,
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          color: '#71717a',
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
          font: { size: 10 },
        },
      },
      y: {
        min: 0,
        max: 100,
        grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
        ticks: { color: '#a1a1aa', stepSize: 25, font: { size: 10 } },
      },
    },
    onHover: (_event, active) => {
      if (active?.length) setSelectedIndex(active[0].index);
    },
    onClick: (_event, active) => {
      if (active?.length) setSelectedIndex(active[0].index);
    },
  };

  return (
    <div className="h-[230px] min-h-[210px]">
      <Chart type="line" data={data} options={options} />
    </div>
  );
}

function Decomposition({ row, params }) {
  const contributions = buildContributions(row, params);
  const maxAbs = Math.max(0.08, ...contributions.map((item) => Math.abs(item.contribution)));
  const centered = num(row?.market_wave_centered);
  const scale = num(params.scale) ?? FALLBACK_PARAMS.scale;
  const tanhInput = centered === null ? null : centered / scale;

  return (
    <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] pb-3">
        <div>
          <div className="text-[10px] uppercase text-gray-600">Wave decomposition</div>
          <div className="mt-1 text-sm font-medium text-gray-100">{formatDate(row?.date)}</div>
        </div>
        <div className="flex gap-4 text-right font-mono text-[11px] tabular-nums">
          <div>
            <div className="text-gray-600">raw</div>
            <div className="text-gray-200">{fixed(row?.market_wave_raw, 3)}</div>
          </div>
          <div>
            <div className="text-gray-600">centered</div>
            <div className={pctTextClass(centered)}>{signedNumber(centered, 3)}</div>
          </div>
          <div>
            <div className="text-gray-600">tanh x</div>
            <div className={pctTextClass(tanhInput)}>{signedNumber(tanhInput, 3)}</div>
          </div>
        </div>
      </div>

      <div className="mt-3 space-y-2.5">
        {contributions.map((item) => {
          const width = Math.min(48, (Math.abs(item.contribution) / maxAbs) * 48);
          const positive = item.contribution >= 0;
          const barClass = item.isCenter
            ? 'bg-white/25'
            : positive
              ? 'bg-trading-green-300/70'
              : 'bg-trading-red-400/70';
          return (
            <div key={item.key} title={item.tip}>
              <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
                <span className="min-w-0 truncate text-gray-400">{item.label}</span>
                <span className="shrink-0 font-mono tabular-nums text-gray-500">
                  {item.sourceText} | {signedNumber(item.contribution, 3)}
                </span>
              </div>
              <div className="relative h-5 overflow-hidden rounded bg-white/[0.035]">
                <span className="absolute bottom-0 left-1/2 top-0 w-px bg-white/[0.12]" aria-hidden />
                <span
                  className={`absolute bottom-1 top-1 rounded-sm ${barClass}`}
                  style={positive ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                  aria-hidden
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SelectedContext({ row }) {
  const fields = [
    ['Market Wave', fixed(row?.market_wave_score, 1), waveTextClass(row?.market_wave_score)],
    ['SPY YTD', signedPct(row?.spy_ytd_pct, 1), pctTextClass(row?.spy_ytd_pct)],
    ['SPY 1D', signedPct(row?.spy_1d_pct, 2), pctTextClass(row?.spy_1d_pct)],
    ['ETF > EMA50', pct(row?.pct_above_ema50, 0), waveTextClass(row?.pct_above_ema50)],
    ['ETF > EMA200', pct(row?.pct_above_ema200, 0), waveTextClass(row?.pct_above_ema200)],
    ['ETF RSI', fixed(row?.avg_rsi, 1), waveTextClass(row?.avg_rsi)],
    ['1D breadth', signedNumber(row?.breadth_1d_change, 0), pctTextClass(row?.breadth_1d_change)],
    ['5D breadth', signedNumber(row?.breadth_5d_change, 0), pctTextClass(row?.breadth_5d_change)],
    ['15D breadth', signedNumber(row?.breadth_15d_change, 0), pctTextClass(row?.breadth_15d_change)],
    ['30D position', signedNumber(row?.breadth_30d_position, 0), pctTextClass(row?.breadth_30d_position)],
    ['Crash echo', fixed(row?.effective_crash_echo, 3), pctTextClass(row?.effective_crash_echo, true)],
    ['Bull wave', fixed(row?.bull_wave, 3), pctTextClass(row?.bull_wave)],
  ];

  return (
    <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase text-gray-600">Selected session</div>
          <div className="mt-1 text-sm font-medium text-gray-100">{formatDate(row?.date)}</div>
        </div>
        <div className={`text-right text-[18px] font-semibold leading-none tabular-nums ${waveTextClass(row?.market_wave_score)}`}>
          {fixed(row?.market_wave_score, 1)}
          <div className="mt-1 text-[10px] font-normal text-gray-500">{row?.market_wave_state || marketWaveState(row?.market_wave_score)}</div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 md:grid-cols-3">
        {fields.map(([label, value, cls]) => (
          <div key={label} className="flex items-center justify-between gap-2 border-t border-white/[0.04] pt-2 text-[11px]">
            <span className="truncate text-gray-600">{label}</span>
            <span className={`shrink-0 font-mono tabular-nums ${cls}`}>{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const MarketTrends = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedYear, setSelectedYear] = useState(null);
  const [selectedIndex, setSelectedIndex] = useState(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(buildApiUrl('/market/trends?days=10000'));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      setData(payload);
      const latestYear = payload.latest?.year || payload.available_years?.[payload.available_years.length - 1] || new Date().getFullYear();
      setSelectedYear((current) => current || latestYear);
    } catch (err) {
      console.error('Error loading market trends:', err);
      setError(err.message || 'Failed to load market trends');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const years = useMemo(() => data?.available_years || [], [data]);
  const params = data?.params || FALLBACK_PARAMS;
  const history = useMemo(() => data?.history || [], [data]);
  const yearRows = useMemo(
    () => history.filter((row) => Number(row.year) === Number(selectedYear)),
    [history, selectedYear]
  );

  useEffect(() => {
    setSelectedIndex(yearRows.length ? yearRows.length - 1 : null);
  }, [selectedYear, yearRows.length]);

  const selectedRow = selectedIndex === null ? null : (yearRows[selectedIndex] || yearRows[yearRows.length - 1]);
  const latest = data?.latest;
  const latestYearRows = latest ? history.filter((row) => Number(row.year) === Number(latest.year)) : [];
  const latestYearStart = latestYearRows[0];
  const latestWave = num(latest?.market_wave_score);
  const latestStartWave = num(latestYearStart?.market_wave_score);
  const latestWaveDelta = latestWave !== null && latestStartWave !== null
    ? latestWave - latestStartWave
    : null;

  if (loading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-trading-dark-950">
        <div className="h-6 w-6 animate-spin rounded-full border-b border-trading-green-300" />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-trading-dark-950 px-4">
        <div className="rounded-md border border-trading-red-400/20 bg-trading-red-400/10 px-4 py-3 text-sm text-trading-red-300">
          Error loading market trends: {error}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto bg-trading-dark-950">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-4 px-4 py-4 sm:px-5 lg:px-6">
        <div className="flex flex-col gap-3 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[11px] uppercase text-gray-600">
              <Activity className="h-3.5 w-3.5 text-trading-green-300" />
              Market Trends
            </div>
            <h1 className="mt-1 text-[24px] font-semibold leading-tight text-gray-100 sm:text-[30px]">
              Market Wave
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <YearCycler years={years} selectedYear={selectedYear} setSelectedYear={setSelectedYear} />
            <button
              type="button"
              onClick={loadData}
              disabled={loading}
              aria-label="Refresh market trends"
              className="rounded-md border border-white/[0.08] bg-white/[0.03] p-2 text-gray-400 transition-colors hover:bg-white/[0.06] hover:text-gray-100 disabled:cursor-wait disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Latest wave"
            value={fixed(latest?.market_wave_score, 1)}
            sub={`${latest?.market_wave_state || marketWaveState(latest?.market_wave_score)} | ${formatDate(latest?.date)}`}
            tone={waveTextClass(latest?.market_wave_score)}
            icon={Activity}
          />
          <MetricCard
            label="SPY YTD"
            value={signedPct(latest?.spy_ytd_pct, 1)}
            sub={`SPY ${latest?.spy_close ? `$${fixed(latest.spy_close, 2)}` : '-'}`}
            tone={pctTextClass(latest?.spy_ytd_pct)}
            icon={LineChart}
          />
          <MetricCard
            label="Sector breadth"
            value={pct(latest?.pct_above_ema50, 0)}
            sub={`${latest?.issues || '-'} sector ETFs | EMA50`}
            tone={waveTextClass(latest?.pct_above_ema50)}
            icon={BarChart2}
          />
          <MetricCard
            label="YTD wave change"
            value={signedNumber(latestWaveDelta, 1)}
            sub={`${latest?.year || ''} first session to latest`}
            tone={pctTextClass(latestWaveDelta)}
            icon={LineChart}
          />
        </div>

        {yearRows.length === 0 ? (
          <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 p-6 text-sm text-gray-500">
            No Market Wave rows are available for {selectedYear || 'this year'}.
          </div>
        ) : (
          <>
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
              <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[10px] uppercase text-gray-600">Market Wave vs SPY</div>
                    <div className="mt-0.5 text-[12px] text-gray-500">{selectedYear} trading sessions</div>
                  </div>
                  <div className="text-right text-[11px] font-mono tabular-nums text-gray-500">
                    {yearRows.length} rows
                  </div>
                </div>
                <WaveVsSpyChart rows={yearRows} selectedIndex={selectedIndex} setSelectedIndex={setSelectedIndex} />
              </div>
              <SelectedContext row={selectedRow} />
            </div>

            <div className="grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(420px,1.05fr)]">
              <div className="rounded-md border border-white/[0.06] bg-trading-dark-900 p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[10px] uppercase text-gray-600">Sector health inputs</div>
                    <div className="mt-0.5 text-[12px] text-gray-500">EMA participation, RSI, and wave overlay</div>
                  </div>
                </div>
                <InputsChart rows={yearRows} selectedIndex={selectedIndex} setSelectedIndex={setSelectedIndex} />
              </div>
              <Decomposition row={selectedRow} params={params} />
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default MarketTrends;
