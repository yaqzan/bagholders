import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend } from 'chart.js';
import { Line } from 'react-chartjs-2';
import { parseLocalDate } from '../utils/timeUtils';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';
const buildApiUrl = (endpoint) => `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;

const WeeklyRsiChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad }) => {
  const [rsiData, setRsiData] = useState(null);
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
          setRsiData(indicatorsRes.data.indicators.sort((a, b) => parseLocalDate(a.date) - parseLocalDate(b.date)));
        } else {
          setRsiData([]);
        }
        setWeeklyScores(scoresRes.data?.scores || []);
      } catch (err) {
        if (cancelled) return;
        setRsiData([]);
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
  if (!rsiData || rsiData.length === 0) return null;

  // Build RSI score map from weekly scores
  const rsiScoreMap = {};
  weeklyScores.forEach(score => {
    const dateKey = parseLocalDate(score.date).toISOString().split('T')[0];
    if (score.rsi != null) rsiScoreMap[dateKey] = score.rsi;
  });

  const extendedData = [...rsiData];
  const lastDate = parseLocalDate(extendedData[extendedData.length - 1].date);
  for (let i = 1; i <= 65; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i * 7);
    extendedData.push({ date: futureDate.toISOString(), rsi: null });
  }

  // Attach RSI score to each data point by date
  extendedData.forEach(d => {
    const dateKey = parseLocalDate(d.date).toISOString().split('T')[0];
    d.rsiScore = rsiScoreMap[dateKey] ?? null;
  });

  const rsiZonesPlugin = {
    id: 'rsiZones',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea || !chart.scales?.y) return;
      const { ctx, chartArea, scales } = chart;
      const { left, right } = chartArea;
      const { y } = scales;
      ctx.save();
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.fillRect(left, y.getPixelForValue(100), right - left, y.getPixelForValue(70) - y.getPixelForValue(100));
      ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      ctx.fillRect(left, y.getPixelForValue(30), right - left, y.getPixelForValue(0) - y.getPixelForValue(30));
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
      ctx.fillText('RSI', chartArea.left + (chartArea.right - chartArea.left) / 2, chartArea.top + (chartArea.bottom - chartArea.top) / 2);
      ctx.restore();
    }
  };

  const rsiArrowsPlugin = {
    id: 'rsiArrows',
    afterDatasetsDraw: (chart) => {
      const { ctx, scales } = chart;
      if (!chart.chartArea || !scales?.x || !scales?.y) return;
      const meta = chart.getDatasetMeta(0);
      ctx.save();
      extendedData.forEach((d, index) => {
        if (d.rsiScore == null || d.rsi == null) return;
        const point = meta.data[index];
        if (!point) return;
        const x = point.x;
        const rsiY = scales.y.getPixelForValue(d.rsi);
        if (d.rsiScore >= 70) {
          const strength = (d.rsiScore - 50) / 50;
          const size = 2 + strength * 4;
          const opacity = 0.2 + strength * 0.5;
          const tipY = rsiY + 12 + size;
          ctx.globalAlpha = opacity;
          ctx.fillStyle = '#10b981';
          ctx.beginPath();
          ctx.moveTo(x, tipY - size * 2);
          ctx.lineTo(x - size, tipY);
          ctx.lineTo(x + size, tipY);
          ctx.closePath();
          ctx.fill();
        } else if (d.rsiScore <= 30) {
          const strength = (50 - d.rsiScore) / 50;
          const size = 2 + strength * 4;
          const opacity = 0.2 + strength * 0.5;
          const tipY = rsiY - 12 - size;
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

  const data = {
    labels: extendedData.map(d => parseLocalDate(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })),
    datasets: [
      { label: 'RSI', data: extendedData.map(d => d.rsi), borderColor: '#8b5cf6', borderWidth: 2, fill: false, tension: 0.3, pointRadius: 0, spanGaps: false },
      {
        label: 'RSI Score', data: extendedData.map(d => d.rsiScore),
        borderColor: 'rgba(236, 72, 153, 0.4)', backgroundColor: 'rgba(236, 72, 153, 0.1)',
        borderWidth: 1.5, fill: false, tension: 0.3, pointRadius: 0, spanGaps: false,
      }
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
        filter: (item) => item.parsed.y != null,
        callbacks: { label: (ctx) => ctx.parsed.y != null ? `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` : null }
      }
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#9ca3af', maxRotation: 0, autoSkip: true, maxTicksLimit: 10, font: { size: 10 } } },
      y: {
        min: 0, max: 100,
        grid: { color: (ctx) => [70, 30].includes(ctx?.tick?.value) ? '#6b7280' : ctx?.tick?.value === 50 ? '#4b5563' : '#374151', lineWidth: (ctx) => [70, 30].includes(ctx?.tick?.value) ? 2 : 0.5 },
        ticks: { color: (ctx) => [70, 30].includes(ctx?.tick?.value) ? '#d1d5db' : '#9ca3af', font: (ctx) => [70, 30].includes(ctx?.tick?.value) ? { size: 11, weight: 'bold' } : { size: 10 } }
      }
    },
    interaction: { intersect: false, mode: 'index' },
    onHover: (e, el) => onHoverIndex(el?.length ? el[0].index : null)
  };

  const highlightedData = {
    ...data,
    datasets: data.datasets.map((ds, di) => ({
      ...ds,
      pointRadius: ds.data.map((_, i) => hoveredIndex === i ? (di === 0 ? 5 : 4) : 0),
      pointBackgroundColor: ds.data.map((_, i) => hoveredIndex === i ? '#ffffff' : ds.borderColor),
      pointBorderWidth: ds.data.map((_, i) => hoveredIndex === i ? 2 : 0),
    }))
  };

  return <div className="w-full" style={{ height: '22.4vh' }}><Line data={highlightedData} options={options} plugins={[rsiZonesPlugin, watermarkPlugin, rsiArrowsPlugin]} /></div>;
};

export default WeeklyRsiChart;
