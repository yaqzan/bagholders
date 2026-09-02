import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler } from 'chart.js';
import { Line } from 'react-chartjs-2';
import { parseLocalDate } from '../utils/timeUtils';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';
const buildApiUrl = (endpoint) => `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;

const WeeklyScoreChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad }) => {
  const [scores, setScores] = useState(null);
  // ICH alignment: { date_iso: { kijun_sen, ich_alignment_score } } from kijun_pct.
  // ich_alignment_score = clip(50 + (close - kijun)/kijun * 50/30, 0, 100).
  // 50 = at kijun (neutral), >50 = bullish weekly state (price above kijun),
  // <50 = bearish weekly state (price below kijun = v44 ICH dampener fires).
  const [ichByDate, setIchByDate] = useState({});
  const [loading, setLoading] = useState(true);
  const hasCalledOnLoad = React.useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        hasCalledOnLoad.current = false;
        // Fetch scores, indicators (for kijun_sen), and price history (for close)
        // in parallel so the chart can derive ICH alignment per weekly bar.
        const [scoreResp, indResp, priceResp] = await Promise.all([
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-scores?limit=250`)),
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-indicators?limit=250`)),
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-price-history?limit=250`)),
        ]);
        if (cancelled) return;

        if (scoreResp.data?.scores) {
          setScores(scoreResp.data.scores.sort((a, b) => parseLocalDate(a.date) - parseLocalDate(b.date)));
        } else {
          setScores([]);
        }

        // Build ICH alignment map keyed by ISO date string.
        const closeMap = {};
        (priceResp.data?.weekly_price_history || []).forEach(p => {
          const k = (p.date || '').split('T')[0];
          if (k && p.close != null) closeMap[k] = parseFloat(p.close);
        });
        const m = {};
        (indResp.data?.indicators || []).forEach(ind => {
          const k = (ind.date || '').split('T')[0];
          const close = closeMap[k];
          if (k && ind.kijun_sen != null && close != null && ind.kijun_sen > 0) {
            const kijun_pct = (close - ind.kijun_sen) / ind.kijun_sen * 100;
            const ich_alignment_score = Math.max(0, Math.min(100, 50 + (kijun_pct / 30) * 50));
            m[k] = { kijun_sen: ind.kijun_sen, kijun_pct, ich_alignment_score };
          }
        });
        setIchByDate(m);
      } catch (err) {
        if (cancelled) return;
        setScores([]);
      } finally {
        if (cancelled) return;
        setLoading(false);
        if (onLoad && !hasCalledOnLoad.current) {
          hasCalledOnLoad.current = true;
          onLoad();
        }
      }
    };
    if (symbol) fetchData();
    return () => { cancelled = true; };
  }, [symbol, onLoad]);

  if (loading) return <div className="flex items-center justify-center h-48"><div className="animate-spin rounded-full h-6 w-6 border-b-2 border-trading-blue-500"></div></div>;
  if (!scores || scores.length === 0) return <div className="flex items-center justify-center h-64 text-gray-400">No weekly score data available</div>;

  const extendedScores = [...scores];
  const lastDate = parseLocalDate(extendedScores[extendedScores.length - 1].date);
  for (let i = 1; i <= 65; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i * 7);
    extendedScores.push({ date: futureDate.toISOString(), composite: null, rsi: null, macd: null });
  }

  const getScoreColor = (score) => {
    if (score == null) return '#6b7280';
    if (score <= 20) return '#ef4444';
    if (score < 35) return interpolate('#ef4444', '#f97316', (score - 20) / 15);
    if (score <= 65) return interpolate('#f97316', '#eab308', (score - 35) / 30);
    if (score < 80) return interpolate('#eab308', '#10b981', (score - 65) / 15);
    return '#10b981';
  };

  const interpolate = (c1, c2, r) => {
    const hex = (c) => { const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(c); return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : null; };
    const h1 = hex(c1), h2 = hex(c2);
    if (!h1 || !h2) return c1;
    const rgb = (v) => Math.round(h1[v] + (h2[v] - h1[v]) * r);
    return `#${((1 << 24) + (rgb('r') << 16) + (rgb('g') << 8) + rgb('b')).toString(16).slice(1)}`;
  };

  const zonesPlugin = {
    id: 'zones',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea || !chart.scales?.y) return;
      const { ctx, chartArea, scales } = chart;
      const { left, right } = chartArea;
      const { y } = scales;
      ctx.save();
      ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      ctx.fillRect(left, y.getPixelForValue(100), right - left, y.getPixelForValue(75) - y.getPixelForValue(100));
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.fillRect(left, y.getPixelForValue(25), right - left, y.getPixelForValue(0) - y.getPixelForValue(25));
      ctx.restore();
    }
  };

  const watermarkPlugin = {
    id: 'watermark',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea) return;
      const { ctx, chartArea } = chart;
      ctx.save();
      ctx.fillStyle = 'rgba(255, 255, 255, 0.12)';
      ctx.font = 'bold 80px system-ui';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('COMPOSITE', chartArea.left + (chartArea.right - chartArea.left) / 2, chartArea.top + (chartArea.bottom - chartArea.top) / 2);
      ctx.restore();
    }
  };

  // Build ICH alignment series matching extendedScores order.
  // 50 = neutral (at Kijun-sen); >50 bullish weekly; <50 bearish (v44 ICH fires).
  const ichSeries = extendedScores.map(s => {
    const k = (s.date || '').split('T')[0];
    const e = ichByDate[k];
    return e?.ich_alignment_score ?? null;
  });

  const data = {
    labels: extendedScores.map(s => parseLocalDate(s.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })),
    datasets: [
      {
        label: 'Composite', data: extendedScores.map(s => s.composite), borderWidth: 3.5, fill: false, tension: 0.3, pointRadius: 3.5, spanGaps: false,
        segment: { borderColor: (ctx) => {
          const s1 = extendedScores[ctx.p0DataIndex]?.composite;
          const s2 = extendedScores[ctx.p1DataIndex]?.composite;
          if (s1 == null || s2 == null) return 'transparent';
          return getScoreColor((s1 + s2) / 2);
        }},
        pointBackgroundColor: (ctx) => { const s = extendedScores[ctx.dataIndex]?.composite; return s == null ? 'transparent' : getScoreColor(s); },
        pointBorderColor: (ctx) => extendedScores[ctx.dataIndex]?.composite == null ? 'transparent' : '#ffffff',
      },
      { label: 'RSI Score', data: extendedScores.map(s => s.rsi), borderColor: 'rgba(236, 72, 153, 0.35)', borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0 },
      { label: 'MACD Score', data: extendedScores.map(s => s.macd), borderColor: 'rgba(251, 146, 60, 0.35)', borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0 },
      { label: 'ICH Align', data: ichSeries, borderColor: 'rgba(6, 182, 212, 0.55)', borderWidth: 1.8, borderDash: [4, 3], fill: false, tension: 0.25, pointRadius: 0, spanGaps: true },
    ]
  };

  const options = {
    responsive: true, maintainAspectRatio: false,
    layout: { padding: { left: 20 } },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1f2937', titleColor: '#f9fafb', bodyColor: '#d1d5db',
        borderColor: '#374151', borderWidth: 1, cornerRadius: 8,
        // Append ICH state line to tooltip when kijun data is available for the
        // hovered date — surfaces v44 ICH dampener visibility.
        callbacks: {
          afterBody: (items) => {
            if (!items?.length) return null;
            const idx = items[0].dataIndex;
            const k = (extendedScores[idx]?.date || '').split('T')[0];
            const e = ichByDate[k];
            if (!e) return null;
            const state = e.kijun_pct >= 0 ? 'bullish' : 'bearish';
            const sign = e.kijun_pct >= 0 ? '+' : '';
            return [
              `Kijun-sen: $${e.kijun_sen.toFixed(2)} (${sign}${e.kijun_pct.toFixed(1)}%)`,
              `ICH state: ${state}` + (state === 'bearish' ? ' (v44 dampener fires)' : ''),
            ];
          }
        }
      }
    },
    scales: {
      x: { grid: { display: false }, ticks: { display: false } },
      y: {
        min: 0, max: 100,
        grid: { color: (ctx) => [25, 75].includes(ctx?.tick?.value) ? '#6b7280' : [0, 50, 100].includes(ctx?.tick?.value) ? '#374151' : 'transparent', lineWidth: (ctx) => [25, 75].includes(ctx?.tick?.value) ? 2 : 0.5 },
        ticks: { color: (ctx) => [25, 75].includes(ctx?.tick?.value) ? '#d1d5db' : '#9ca3af', font: (ctx) => [25, 75].includes(ctx?.tick?.value) ? { size: 12, weight: 'bold' } : { size: 11 } }
      }
    },
    interaction: { intersect: false, mode: 'index' },
    onHover: (e, el) => onHoverIndex(el?.length ? el[0].index : null)
  };

  const highlightedData = {
    ...data,
    datasets: data.datasets.map((ds, di) => di === 0 ? {
      ...ds,
      pointRadius: ds.data.map((_, i) => hoveredIndex === i ? 7 : 3.5),
      pointBackgroundColor: ds.data.map((_, i) => hoveredIndex === i ? '#ffffff' : getScoreColor(extendedScores[i]?.composite)),
      pointBorderColor: ds.data.map((_, i) => hoveredIndex === i ? getScoreColor(extendedScores[i]?.composite) : '#ffffff'),
      pointBorderWidth: ds.data.map((_, i) => hoveredIndex === i ? 3 : 0),
    } : {
      ...ds,
      pointRadius: ds.data.map((_, i) => hoveredIndex === i ? 4 : 0),
      pointBackgroundColor: ds.data.map((_, i) => hoveredIndex === i ? '#ffffff' : ds.borderColor),
      pointBorderWidth: ds.data.map((_, i) => hoveredIndex === i ? 2 : 0),
    })
  };

  return <div className="w-full" style={{ height: '28vh' }}><Line data={highlightedData} options={options} plugins={[zonesPlugin, watermarkPlugin]} /></div>;
};

export default WeeklyScoreChart;



