import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, BarElement, BarController, LineController, Title, Tooltip, Legend } from 'chart.js';
import { Chart } from 'react-chartjs-2';
import { parseLocalDate } from '../utils/timeUtils';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, BarController, LineController, Title, Tooltip, Legend);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';
const buildApiUrl = (endpoint) => `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;

const WeeklyMacdChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad }) => {
  const [macdData, setMacdData] = useState(null);
  const [weeklyScores, setWeeklyScores] = useState([]);
  const [loading, setLoading] = useState(true);
  const hasCalledOnLoad = React.useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        hasCalledOnLoad.current = false;
        const [indicatorsRes, scoresRes] = await Promise.all([
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-indicators?limit=250`)),
          axios.get(buildApiUrl(`/stocks/${symbol}/weekly-scores?limit=250`)).catch(() => ({ data: {} })),
        ]);
        if (cancelled) return;
        if (indicatorsRes.data?.indicators) {
          setMacdData(indicatorsRes.data.indicators.sort((a, b) => parseLocalDate(a.date) - parseLocalDate(b.date)));
        } else {
          setMacdData([]);
        }
        setWeeklyScores(scoresRes.data?.scores || []);
      } catch (err) {
        if (cancelled) return;
        setMacdData([]);
        setWeeklyScores([]);
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
  if (!macdData || macdData.length === 0) return null;

  // Build MACD score map from weekly scores
  const macdScoreMap = {};
  weeklyScores.forEach(score => {
    const dateKey = parseLocalDate(score.date).toISOString().split('T')[0];
    if (score.macd != null) macdScoreMap[dateKey] = score.macd;
  });

  const extendedData = [...macdData];
  const lastDate = parseLocalDate(extendedData[extendedData.length - 1].date);
  for (let i = 1; i <= 65; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i * 7);
    extendedData.push({ date: futureDate.toISOString(), macd: null, macd_signal: null, macd_hist: null });
  }

  // Attach MACD score to each data point by date
  extendedData.forEach(d => {
    const dateKey = parseLocalDate(d.date).toISOString().split('T')[0];
    d.macdScore = macdScoreMap[dateKey] ?? null;
  });

  const data = {
    labels: extendedData.map(d => parseLocalDate(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })),
    datasets: [
      {
        type: 'bar', label: 'MACD Histogram', data: extendedData.map(d => d.macd_hist),
        backgroundColor: extendedData.map((d, i) => {
          const hist = d.macd_hist || 0;
          const prev = i > 0 ? (extendedData[i - 1].macd_hist || 0) : 0;
          if (hist >= 0) return hist >= prev ? '#10b981' : '#bbf7d0';
          return hist <= prev ? '#ef4444' : '#fca5a5';
        }),
        borderWidth: 0, barThickness: 3, order: 3,
      },
      { type: 'line', label: 'MACD Line', data: extendedData.map(d => d.macd), borderColor: '#3b82f6', borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0, spanGaps: false, order: 1 },
      { type: 'line', label: 'Signal Line', data: extendedData.map(d => d.macd_signal), borderColor: '#f59e0b', borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0, spanGaps: false, order: 2 },
      {
        type: 'line', label: 'MACD Score', data: extendedData.map(d => d.macdScore),
        borderColor: 'rgba(251, 146, 60, 0.4)', backgroundColor: 'rgba(251, 146, 60, 0.1)',
        borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0, spanGaps: false,
        yAxisID: 'y1', order: 0,
      }
    ]
  };

  const macdArrowsPlugin = {
    id: 'macdArrows',
    afterDatasetsDraw: (chart) => {
      const { ctx, scales } = chart;
      if (!chart.chartArea || !scales?.x || !scales?.y) return;
      const meta = chart.getDatasetMeta(1); // MACD Line dataset
      ctx.save();
      extendedData.forEach((d, index) => {
        if (d.macdScore == null || d.macd == null) return;
        const point = meta.data[index];
        if (!point) return;
        const x = point.x;
        const macdY = scales.y.getPixelForValue(d.macd);
        if (d.macdScore >= 70) {
          const strength = (d.macdScore - 50) / 50;
          const size = 2 + strength * 4;
          const opacity = 0.2 + strength * 0.5;
          const tipY = macdY + 12 + size;
          ctx.globalAlpha = opacity;
          ctx.fillStyle = '#10b981';
          ctx.beginPath();
          ctx.moveTo(x, tipY - size * 2);
          ctx.lineTo(x - size, tipY);
          ctx.lineTo(x + size, tipY);
          ctx.closePath();
          ctx.fill();
        } else if (d.macdScore <= 30) {
          const strength = (50 - d.macdScore) / 50;
          const size = 2 + strength * 4;
          const opacity = 0.2 + strength * 0.5;
          const tipY = macdY - 12 - size;
          ctx.globalAlpha = opacity;
          ctx.fillStyle = '#ef4444';
          ctx.beginPath();
          ctx.moveTo(x, tipY + size * 2);
          ctx.lineTo(x - size, tipY);
          ctx.lineTo(x + size, tipY);
          ctx.closePath();
          ctx.fill();
        }
      });
      ctx.globalAlpha = 1;
      ctx.restore();
    }
  };

  const options = {
    responsive: true, maintainAspectRatio: false,
    layout: { padding: { left: 20 } },
    plugins: {
      legend: { position: 'bottom', labels: { color: '#d1d5db', usePointStyle: true, padding: 10, font: { size: 10 }, boxWidth: 15, boxHeight: 2 } },
      tooltip: {
        backgroundColor: '#1f2937', titleColor: '#f9fafb', bodyColor: '#d1d5db',
        borderColor: '#374151', borderWidth: 1, cornerRadius: 8,
        filter: (item) => item.parsed.y != null,
        callbacks: {
          label: (ctx) => {
            if (ctx.parsed.y == null) return null;
            if (ctx.dataset.label === 'MACD Score') return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}`;
            return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(3)}`;
          }
        }
      }
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#9ca3af', maxRotation: 0, autoSkip: true, maxTicksLimit: 10, font: { size: 10 } } },
      y: {
        grid: { color: (ctx) => ctx?.tick?.value === 0 ? '#6b7280' : '#374151', lineWidth: (ctx) => ctx?.tick?.value === 0 ? 2 : 0.5 },
        ticks: { color: '#9ca3af', font: { size: 10 }, callback: (v) => v?.toFixed(2) || '' }
      },
      y1: { position: 'right', min: 0, max: 100, display: false, grid: { display: false } }
    },
    interaction: { intersect: false, mode: 'index' },
    onHover: (_e, el) => onHoverIndex(el?.length ? el[0].index : null)
  };

  const highlightedData = {
    ...data,
    datasets: data.datasets.map(ds => ({
      ...ds,
      pointRadius: ds.data.map((_, i) => hoveredIndex === i ? 5 : 0),
      pointBackgroundColor: ds.data.map((_, i) => hoveredIndex === i ? '#ffffff' : ds.borderColor),
      pointBorderWidth: ds.data.map((_, i) => hoveredIndex === i ? 2 : 0),
    }))
  };

  return <div className="w-full" style={{ height: '22.4vh' }}><Chart type='bar' data={highlightedData} options={options} plugins={[macdArrowsPlugin]} /></div>;
};

export default WeeklyMacdChart;
