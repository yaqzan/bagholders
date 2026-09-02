import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  BarController,
  LineController,
  Title,
  Tooltip,
  Legend
} from 'chart.js';
import { Chart } from 'react-chartjs-2';
import { parseLocalDate } from '../utils/timeUtils';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  BarController,
  LineController,
  Title,
  Tooltip,
  Legend
);

// API base URL
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const API_PATH_PREFIX = '/api';

const buildApiUrl = (endpoint) => {
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;
};

const MacdChart = ({ symbol, onHoverIndex, hoveredIndex, onLoad, scores }) => {
  const [macdData, setMacdData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const hasCalledOnLoad = React.useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fetchMacdData = async () => {
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
          setMacdData(sortedData);
        } else {
          setMacdData([]);
        }
      } catch (err) {
        if (cancelled) return;
        console.error('Error fetching MACD data:', err);
        setError(err.message);
        setMacdData([]);
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
      fetchMacdData();
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

  if (error || !macdData || macdData.length === 0) {
    return null; // Don't show if no data
  }

  // Build MACD score map from scores prop
  const macdScoreMap = {};
  if (scores && scores.length > 0) {
    scores.forEach(score => {
      const dateKey = parseLocalDate(score.date).toISOString().split('T')[0];
      if (score.macd != null) macdScoreMap[dateKey] = score.macd;
    });
  }

  // Extend data into future with null values for alignment
  const extendedMacdData = [...macdData];
  const lastData = extendedMacdData[extendedMacdData.length - 1];
  const lastDate = parseLocalDate(lastData.date);
  
  const futureDays = 65;
  for (let i = 1; i <= futureDays; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i);
    
    extendedMacdData.push({
      date: futureDate.toISOString(),
      macd: null,
      macd_signal: null,
      macd_hist: null
    });
  }

  // Attach MACD score to each data point by date
  extendedMacdData.forEach(d => {
    const dateKey = parseLocalDate(d.date).toISOString().split('T')[0];
    d.macdScore = macdScoreMap[dateKey] ?? null;
  });

  const data = {
    labels: extendedMacdData.map(d => {
      const date = parseLocalDate(d.date);
      return date.toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric' 
      });
    }),
    datasets: [
      {
        type: 'bar',
        label: 'MACD Histogram',
        data: extendedMacdData.map(d => d.macd_hist),
        backgroundColor: extendedMacdData.map((d, index) => {
          const hist = d.macd_hist || 0;
          const prevHist = index > 0 ? (extendedMacdData[index - 1].macd_hist || 0) : 0;
          
          if (hist >= 0) {
            // Positive histogram
            if (hist >= prevHist) {
              // Increasing momentum (stronger) - full green
              return '#10b981';
            } else {
              // Decreasing momentum (weakening) - very light mint green
              return '#bbf7d0';
            }
          } else {
            // Negative histogram
            if (hist <= prevHist) {
              // More negative (stronger bearish) - full red
              return '#ef4444';
            } else {
              // Less negative (weakening) - light pastel red
              return '#fca5a5';
            }
          }
        }),
        borderColor: extendedMacdData.map((d, index) => {
          const hist = d.macd_hist || 0;
          const prevHist = index > 0 ? (extendedMacdData[index - 1].macd_hist || 0) : 0;
          
          if (hist >= 0) {
            return hist >= prevHist ? '#10b981' : '#bbf7d0';
          } else {
            return hist <= prevHist ? '#ef4444' : '#fca5a5';
          }
        }),
        borderWidth: 0,
        barThickness: 3,
        maxBarThickness: 4,
        order: 3,
      },
      {
        type: 'line',
        label: 'MACD Line',
        data: extendedMacdData.map(d => d.macd),
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: false,
        order: 1,
      },
      {
        type: 'line',
        label: 'Signal Line',
        data: extendedMacdData.map(d => d.macd_signal),
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: false,
        order: 2,
      },
      {
        type: 'line',
        label: 'MACD Score',
        data: extendedMacdData.map(d => d.macdScore),
        borderColor: 'rgba(251, 146, 60, 0.4)',
        backgroundColor: 'rgba(251, 146, 60, 0.1)',
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: false,
        yAxisID: 'y1',
        order: 0,
      }
    ]
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    layout: {
      padding: {
        left: 20 // Add left padding to align with price chart
      }
    },
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          color: '#d1d5db',
          usePointStyle: true,
          padding: 10,
          font: {
            size: 10,
            weight: '500'
          },
          boxWidth: 15,
          boxHeight: 2,
        }
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
            if (context.dataset.label === 'MACD Score') return `${context.dataset.label}: ${value.toFixed(1)}`;
            return `${context.dataset.label}: ${value.toFixed(3)}`;
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
        grid: {
          color: (context) => {
            if (context?.tick?.value === 0) {
              return '#6b7280';
            }
            return '#374151';
          },
          lineWidth: (context) => {
            if (context?.tick?.value === 0) {
              return 2;
            }
            return 0.5;
          },
          drawBorder: false,
        },
        ticks: {
          color: '#9ca3af',
          font: {
            size: 10
          },
          callback: function(value) {
            if (value === null || value === undefined) return '';
            return value.toFixed(2);
          }
        }
      },
      y1: {
        position: 'right',
        min: 0,
        max: 100,
        display: false,
        grid: { display: false },
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

  // Plugin to draw MACD score arrows at high/low score points
  const macdArrowsPlugin = {
    id: 'macdArrows',
    afterDatasetsDraw: (chart) => {
      const { ctx, scales } = chart;
      if (!chart.chartArea || !scales?.x || !scales?.y) return;

      const meta = chart.getDatasetMeta(1); // MACD Line dataset
      ctx.save();

      extendedMacdData.forEach((d, index) => {
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

  // Highlight based on hoveredIndex
  const highlightedData = {
    ...data,
    datasets: data.datasets.map((dataset, datasetIndex) => ({
      ...dataset,
      pointRadius: dataset.data.map((_, index) => 
        hoveredIndex === index ? 5 : dataset.pointRadius || 0
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
      <Chart type='bar' data={highlightedData} options={options} plugins={[macdArrowsPlugin]} />
    </div>
  );
};

export default MacdChart;

