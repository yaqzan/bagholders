import React from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
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
  Legend,
  Filler
);

const TrendScoreChart = ({ scores, onHoverIndex, hoveredIndex }) => {
  if (!scores || scores.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        No Trend score data available
      </div>
    );
  }

  const reversedScores = [...scores].reverse();

  // Extend data into future with null values for alignment
  const lastScore = reversedScores[reversedScores.length - 1];
  const lastDate = parseLocalDate(lastScore.date);
  
  const futureDays = 65;
  for (let i = 1; i <= futureDays; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i);
    
    reversedScores.push({
      date: futureDate.toISOString(),
      trend: null
    });
  }

  const getScoreColor = (score) => {
    if (score === null || score === undefined) return '#6b7280';
    if (score <= 20) return '#ef4444';
    if (score < 35) return '#f97316';
    if (score <= 65) return '#eab308';
    if (score < 80) return '#84cc16';
    return '#10b981';
  };

  const opportunityZonesPlugin = {
    id: 'opportunityZonesTrend',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea || !chart.scales?.y) return;
      
      const { ctx, chartArea, scales } = chart;
      const { top, bottom, left, right } = chartArea;
      const { y } = scales;
      
      ctx.save();
      
      const y75 = y.getPixelForValue(75);
      const y25 = y.getPixelForValue(25);
      const yTop = y.getPixelForValue(100);
      const yBottom = y.getPixelForValue(0);
      
      ctx.fillStyle = 'rgba(16, 185, 129, 0.08)';
      ctx.fillRect(left, yTop, right - left, y75 - yTop);
      
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.fillRect(left, y25, right - left, yBottom - y25);
      
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
      
      ctx.fillText('TREND SCORE', centerX, centerY);
      ctx.restore();
    }
  };

  const data = {
    labels: reversedScores.map(score => {
      const date = parseLocalDate(score.date);
      return date.toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric' 
      });
    }),
    datasets: [
      {
        label: 'Trend Score',
        data: reversedScores.map(score => score.trend),
        borderWidth: 3,
        fill: false,
        tension: 0.3,
        pointRadius: 3,
        pointHoverRadius: 6,
        pointBorderWidth: 0,
        pointHoverBorderWidth: 2,
        spanGaps: false,
        segment: {
          borderColor: (ctx) => {
            if (ctx.p0DataIndex !== undefined && ctx.p1DataIndex !== undefined) {
              const score1 = reversedScores[ctx.p0DataIndex]?.trend;
              const score2 = reversedScores[ctx.p1DataIndex]?.trend;
              
              if (score1 === null || score2 === null) {
                return 'transparent';
              }
              
              const avgScore = (score1 + score2) / 2;
              return getScoreColor(avgScore);
            }
            return '#6b7280';
          }
        },
        pointBackgroundColor: (ctx) => {
          const score = reversedScores[ctx.dataIndex]?.trend;
          if (score === null) return 'transparent';
          return getScoreColor(score);
        },
        pointBorderColor: (ctx) => {
          const score = reversedScores[ctx.dataIndex]?.trend;
          if (score === null) return 'transparent';
          return '#ffffff';
        },
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
        display: false
      },
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        borderColor: '#374151',
        borderWidth: 1,
        cornerRadius: 8,
        displayColors: true,
        callbacks: {
          label: function(context) {
            return `${context.dataset.label}: ${context.parsed.y}`;
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
          display: false
        }
      },
      y: {
        min: 0,
        max: 100,
        grid: {
          color: (context) => {
            const value = context?.tick?.value;
            if (value === 25 || value === 75) {
              return '#6b7280';
            }
            if (value === 0 || value === 50 || value === 100) {
              return '#374151';
            }
            return 'transparent';
          },
          lineWidth: (context) => {
            const value = context?.tick?.value;
            if (value === 25 || value === 75) {
              return 2;
            }
            return 0.5;
          },
          drawBorder: false,
        },
        ticks: {
          color: (context) => {
            if (context?.tick?.value === 25 || context?.tick?.value === 75) {
              return '#d1d5db';
            }
            return '#9ca3af';
          },
          font: (context) => {
            if (context?.tick?.value === 25 || context?.tick?.value === 75) {
              return { size: 12, weight: 'bold' };
            }
            return { size: 11 };
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
    elements: {
      point: {
        hoverBackgroundColor: '#ffffff',
        hoverBorderColor: '#000000',
        hoverBorderWidth: 2,
      }
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
    datasets: data.datasets.map((dataset) => ({
      ...dataset,
      pointRadius: dataset.data.map((_, index) => 
        hoveredIndex === index ? 7 : 3
      ),
      pointBackgroundColor: dataset.data.map((_, index) => {
        if (hoveredIndex === index) return '#ffffff';
        const score = reversedScores[index]?.trend;
        return getScoreColor(score);
      }),
      pointBorderColor: dataset.data.map((_, index) => {
        if (hoveredIndex === index) {
          const score = reversedScores[index]?.trend;
          return getScoreColor(score);
        }
        return '#ffffff';
      }),
      pointBorderWidth: dataset.data.map((_, index) => 
        hoveredIndex === index ? 3 : 0
      ),
    }))
  };

  return (
    <div className="w-full" style={{ height: '20vh' }}>
      <Line data={highlightedData} options={options} plugins={[opportunityZonesPlugin, watermarkPlugin]} />
    </div>
  );
};

export default TrendScoreChart;











