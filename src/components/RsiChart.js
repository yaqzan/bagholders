import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { parseLocalDate } from '../utils/timeUtils';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
);

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';

const buildApiUrl = (endpoint) => {
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;
};

const RsiChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad, scores }) => {
  const [rsiData, setRsiData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const hasCalledOnLoad = React.useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fetchRsiData = async () => {
      try {
        setLoading(true);
        setError(null);
        hasCalledOnLoad.current = false;

        const response = await axios.get(buildApiUrl(`/stocks/${symbol}/indicators?limit=250`));

        if (cancelled) return;

        if (response.data && response.data.indicators) {
          const sortedData = response.data.indicators.sort((a, b) =>
            parseLocalDate(a.date) - parseLocalDate(b.date)
          );
          setRsiData(sortedData);
        } else {
          setRsiData([]);
        }
      } catch (err) {
        if (cancelled) return;
        console.error('Error fetching RSI data:', err);
        setError(err.message);
        setRsiData([]);
      } finally {
        if (cancelled) return;
        setLoading(false);
        if (onLoad && !hasCalledOnLoad.current) {
          hasCalledOnLoad.current = true;
          onLoad();
        }
      }
    };

    if (symbol) {
      fetchRsiData();
    }
    return () => { cancelled = true; };
  }, [symbol, onLoad]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-trading-blue-500"></div>
      </div>
    );
  }

  if (error || !rsiData || rsiData.length === 0) {
    return null;
  }

  // Build RSI score map from scores prop
  const rsiScoreMap = {};
  if (scores && scores.length > 0) {
    scores.forEach(score => {
      const dateKey = parseLocalDate(score.date).toISOString().split('T')[0];
      if (score.rsi != null) rsiScoreMap[dateKey] = score.rsi;
    });
  }

  // Extend data into future with null values for alignment
  const extendedRsiData = [...rsiData];
  const lastData = extendedRsiData[extendedRsiData.length - 1];
  const lastDate = parseLocalDate(lastData.date);
  
  const futureDays = 65;
  for (let i = 1; i <= futureDays; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i);
    
    extendedRsiData.push({
      date: futureDate.toISOString(),
      rsi: null
    });
  }

  // Attach RSI score to each data point by date
  extendedRsiData.forEach(d => {
    const dateKey = parseLocalDate(d.date).toISOString().split('T')[0];
    d.rsiScore = rsiScoreMap[dateKey] ?? null;
  });

  // Plugin to highlight oversold/overbought zones
  const rsiZonesPlugin = {
    id: 'rsiZones',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea || !chart.scales?.y) return;
      
      const { ctx, chartArea, scales } = chart;
      const { top, bottom, left, right } = chartArea;
      const { y } = scales;
      
      ctx.save();
      
      const y70 = y.getPixelForValue(70);
      const y30 = y.getPixelForValue(30);
      const yTop = y.getPixelForValue(100);
      const yBottom = y.getPixelForValue(0);
      
      // Overbought zone (above 70) - red tint
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.fillRect(left, yTop, right - left, y70 - yTop);
      
      // Oversold zone (below 30) - green tint
      ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      ctx.fillRect(left, y30, right - left, yBottom - y30);
      
      ctx.restore();
    }
  };

  // Plugin to add background watermark
  const watermarkPlugin = {
    id: 'watermark',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea) return;
      
      const { ctx, chartArea } = chart;
      const { top, bottom, left, right } = chartArea;
      
      ctx.save();
      ctx.fillStyle = 'rgba(255, 255, 255, 0.12)';
      ctx.font = 'bold 80px system-ui, -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      
      const centerX = left + (right - left) / 2;
      const centerY = top + (bottom - top) / 2;
      
      ctx.fillText('RSI', centerX, centerY);
      ctx.restore();
    }
  };

  // Plugin to draw RSI score arrows at high/low score points
  const rsiArrowsPlugin = {
    id: 'rsiArrows',
    afterDatasetsDraw: (chart) => {
      const { ctx, scales } = chart;
      if (!chart.chartArea || !scales?.x || !scales?.y) return;

      const meta = chart.getDatasetMeta(0);
      ctx.save();

      extendedRsiData.forEach((d, index) => {
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
    labels: extendedRsiData.map(d => {
      const date = parseLocalDate(d.date);
      return date.toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric' 
      });
    }),
    datasets: [
      {
        label: 'RSI',
        data: extendedRsiData.map(d => d.rsi),
        borderColor: '#8b5cf6',
        backgroundColor: 'rgba(139, 92, 246, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 5,
        spanGaps: false,
      },
      {
        label: 'RSI Score',
        data: extendedRsiData.map(d => d.rsiScore),
        borderColor: 'rgba(236, 72, 153, 0.4)',
        backgroundColor: 'rgba(236, 72, 153, 0.1)',
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: false,
      }
    ]
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    layout: {
      padding: {
        left: 20
      }
    },
    plugins: {
      legend: {
        display: false // Hide the legend
      },
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        borderColor: '#374151',
        borderWidth: 1,
        cornerRadius: 8,
        displayColors: true,
        filter: function(tooltipItem) {
          return tooltipItem.parsed.y !== null && tooltipItem.parsed.y !== undefined;
        },
        callbacks: {
          label: function(context) {
            const value = context.parsed.y;
            if (value === null || value === undefined) return null;
            return `${context.dataset.label}: ${value.toFixed(2)}`;
          }
        }
      }
    },
    scales: {
      x: {
        grid: {
          display: false,
          drawBorder: false,
        },
        ticks: {
          color: '#9ca3af',
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 10,
          font: {
            size: 10
          }
        }
      },
      y: {
        min: 0,
        max: 100,
        grid: {
          color: (context) => {
            const value = context?.tick?.value;
            if (value === 70 || value === 30) {
              return '#6b7280';
            }
            if (value === 50) {
              return '#4b5563';
            }
            return '#374151';
          },
          lineWidth: (context) => {
            const value = context?.tick?.value;
            if (value === 70 || value === 30) {
              return 2;
            }
            return 0.5;
          },
          drawBorder: false,
        },
        ticks: {
          color: (context) => {
            const value = context?.tick?.value;
            if (value === 70 || value === 30) {
              return '#d1d5db';
            }
            return '#9ca3af';
          },
          font: (context) => {
            const value = context?.tick?.value;
            if (value === 70 || value === 30) {
              return { size: 11, weight: 'bold' };
            }
            return { size: 10 };
          },
          callback: function(value) {
            if (value === null || value === undefined) return '';
            return value;
          }
        }
      }
    },
    interaction: {
      intersect: false,
      mode: 'index',
    },
    onHover: (event, activeElements) => {
      if (activeElements && activeElements.length > 0) {
        const index = activeElements[0].index;
        onHoverIndex(index);
      } else {
        onHoverIndex(null);
      }
    }
  };

  const highlightedData = {
    ...data,
    datasets: data.datasets.map((dataset, di) => ({
      ...dataset,
      pointRadius: dataset.data.map((_, index) => 
        hoveredIndex === index ? (di === 0 ? 5 : 4) : 0
      ),
      pointBackgroundColor: dataset.data.map((_, index) => 
        hoveredIndex === index ? '#ffffff' : dataset.borderColor
      ),
      pointBorderColor: dataset.data.map((_, index) => 
        hoveredIndex === index ? dataset.borderColor : dataset.borderColor
      ),
      pointBorderWidth: dataset.data.map((_, index) => 
        hoveredIndex === index ? 2 : 0
      ),
    }))
  };

  return (
    <div className="w-full" style={{ height: '22.4vh' }}>
      <Line data={highlightedData} options={options} plugins={[rsiZonesPlugin, watermarkPlugin, rsiArrowsPlugin]} />
    </div>
  );
};

export default RsiChart;


