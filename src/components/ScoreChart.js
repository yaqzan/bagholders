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

const SCORE_COMPONENTS = [
  ['BB', 'bb'],
  ['RSI', 'rsi'],
  ['MACD', 'macd'],
  ['Trend', 'trend'],
  ['Volume', 'volume'],
  ['Stoch', 'stoch'],
  ['TA', 'technical_alignment'],
];

const PRIMARY_WEIGHT_KEYS = ['trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'td'];
const FLOW_WEIGHT_KEYS = ['pre_regime', 'pre_boost', 'w_comp', 'w_adj', 'w_bias', 'w_mom'];
const CALL_SIGNAL_MIN = 70;
const CALL_STRONG_MIN = 75;
const PUT_SIGNAL_MAX = 25;

const LABEL_OVERRIDES = {
  bb: 'BB',
  rsi: 'RSI',
  macd: 'MACD',
  ta: 'TA w',
  td: 'Trend dom',
  w_comp: 'W comp',
  w_adj: 'W adj',
  w_bias: 'W bias',
  w_mom: 'W mom',
  pre_regime: 'Pre-reg',
  pre_boost: 'Pre-boost',
  put_regime_mult: 'Put mult',
  mis_stress: 'Mis stress',
  cap_dampened: 'Cap damp',
  exh_damp: 'Exh damp',
  ext_damp: 'Ext damp',
  wcf_lift: 'WCF lift',
  cwcf_dampen: 'CWCF damp',
  cwwd_dampen: 'CWWD damp',
  pess_lift: 'Pess lift',
  cswc_dampen: 'CSWC damp',
  scw_dampen: 'SCW damp',
  scw_base_dampen: 'SCW base',
  scw_scalar: 'SCW scalar',
  scw_conf: 'SCW conf',
  scw_raw_stoch: 'SCW stoch',
  scw_ext_idx: 'SCW ext',
  scw_ext_taper: 'SCW taper',
  cont_lift: 'Cont lift',
  cont_raw_lift: 'Cont raw',
  cont_sig: 'Cont sig',
  days_to_ern: 'D to ern',
  ern_boost: 'Ern boost',
  sector_breadth_wave: 'Sector wave',
  daily_volume_authority_wave: 'DV authority',
  pcd_active: 'PCD',
  pcd_r10sigma: 'PCD r10s',
  mcd_dampen: 'MCD damp',
  mcd_mcap_b: 'MCD mcap',
  ich_call_dampen: 'ICH call',
  ich_put_lift: 'ICH put',
  kijun_pct: 'Kijun pct',
  wvd_lift: 'WVD lift',
  wvd_dampen: 'WVD damp',
  wv_force1: 'WV force',
};

const formatKeyLabel = (key) => {
  const shortKey = String(key).split('.').pop();
  return LABEL_OVERRIDES[key] || LABEL_OVERRIDES[shortKey] || shortKey
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
};

const formatBreakdownValue = (value) => {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    if (Math.abs(value) >= 1000) return value.toFixed(0);
    if (Number.isInteger(value)) return String(value);
    return Math.abs(value) < 1 ? value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  }
  if (typeof value === 'string') return value;
  return null;
};

const flattenObject = (obj, prefix = '') => {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return [];
  return Object.entries(obj).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return flattenObject(value, path);
    }
    const formatted = Array.isArray(value)
      ? value.map(formatBreakdownValue).filter(Boolean).join(',')
      : formatBreakdownValue(value);
    return formatted == null ? [] : [[path, formatted]];
  });
};

const section = (title, rows) => {
  const filtered = rows.filter(([, value]) => value !== null && value !== undefined && value !== '');
  return filtered.length ? { title, rows: filtered } : null;
};

export const buildScoreBreakdownSections = (score) => {
  if (!score) return [];
  const weights = score.weights || {};
  const usedWeightKeys = new Set([...PRIMARY_WEIGHT_KEYS, ...FLOW_WEIGHT_KEYS]);
  const sections = [
    section('Components', SCORE_COMPONENTS.map(([label, key]) => [label, formatBreakdownValue(score[key])])),
    section('Volume', [
      ['Signal', score.volume_signal && score.volume_signal !== 'NEUTRAL' ? score.volume_signal : null],
      ['Magnitude', formatBreakdownValue(score.volume_magnitude)],
    ]),
    section('Weights', PRIMARY_WEIGHT_KEYS.map((key) => [formatKeyLabel(key), formatBreakdownValue(weights[key])])),
    section('Flow', FLOW_WEIGHT_KEYS.map((key) => [formatKeyLabel(key), formatBreakdownValue(weights[key])])),
  ].filter(Boolean);

  const modifierRows = flattenObject(weights)
    .filter(([key]) => !usedWeightKeys.has(key))
    .map(([key, value]) => [formatKeyLabel(key), value]);
  const modifierSection = section('Modifiers', modifierRows);
  if (modifierSection) sections.push(modifierSection);

  const dte = score.dte_recommendation;
  if (dte) {
    const dteInputs = flattenObject(dte.inputs || {}).map(([key, value]) => [formatKeyLabel(key), value]);
    const dteRows = [
      ['Thesis', formatBreakdownValue(dte.thesis)],
      ['Confidence', formatBreakdownValue(dte.confidence)],
      ['Tradeable', formatBreakdownValue(dte.tradeable)],
      ['DTE', dte.dte_min != null || dte.dte_max != null ? `${dte.dte_min ?? '--'}-${dte.dte_max ?? '--'}` : null],
      ['Reason', formatBreakdownValue(dte.filter_reason)],
      ...dteInputs,
    ];
    const dteSection = section('DTE', dteRows);
    if (dteSection) sections.push(dteSection);
  }

  return sections;
};

const scoreBreakdownTooltipLines = (score) => (
  buildScoreBreakdownSections(score).flatMap((group, index) => [
    index === 0 ? `-- ${group.title} --` : `-- ${group.title} --`,
    ...group.rows.map(([label, value]) => `${label}: ${value}`),
  ])
);

const ScoreChart = ({ scores, onHoverIndex, hoveredIndex, onLoad, height, showTooltip = true }) => {
  const hasCalledOnLoad = React.useRef(false);
  
  // Notify parent that score chart is ready (only once)
  React.useEffect(() => {
    if (scores && scores.length > 0 && onLoad && !hasCalledOnLoad.current) {
      hasCalledOnLoad.current = true;
      onLoad();
    }
  }, [scores, onLoad]);

  if (!scores || scores.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        No score data available
      </div>
    );
  }

  // Reverse scores to show oldest to newest
  const reversedScores = [...scores].reverse();

  // Extend data into future with null values for alignment
  const lastScore = reversedScores[reversedScores.length - 1];
  const lastDate = parseLocalDate(lastScore.date);
  
  // Add approximately 3 months of future data points (about 63 trading days)
  const futureDays = 65;
  for (let i = 1; i <= futureDays; i++) {
    const futureDate = new Date(lastDate);
    futureDate.setDate(futureDate.getDate() + i);
    
    reversedScores.push({
      date: futureDate.toISOString(),
      overall: null,
      bb: null,
      rsi: null,
      macd: null,
      trend: null,
      volume: null
    });
  }

  // Function to get color based on score value (gradient from red to green)
  const getScoreGradientColor = (score) => {
    if (score === null || score === undefined) return '#6b7280'; // gray
    
    if (score <= 20) {
      // Below 20: muted trading-red (put opportunity)
      return '#C98484'; // trading-red-400
    } else if (score < 35) {
      // 20-35: Transition from muted red to orange
      const ratio = (score - 20) / 15;
      return interpolateColor('#C98484', '#f97316', ratio);
    } else if (score <= 65) {
      // 35-65: Neutral band (orange → yellow)
      const ratio = (score - 35) / 30;
      return interpolateColor('#f97316', '#eab308', ratio);
    } else if (score < CALL_SIGNAL_MIN) {
      // 65-70: Neutral yellow easing toward the call transition band
      const ratio = (score - 65) / (CALL_SIGNAL_MIN - 65);
      return interpolateColor('#eab308', '#c6b85e', ratio);
    } else if (score < CALL_STRONG_MIN) {
      // 70-75: Transitional call signal, not yet high-conviction green
      const ratio = (score - CALL_SIGNAL_MIN) / (CALL_STRONG_MIN - CALL_SIGNAL_MIN);
      return interpolateColor('#c6b85e', '#7FBFA0', ratio);
    } else if (score < 80) {
      // 75-80: Strong call signal ramp
      const ratio = (score - CALL_STRONG_MIN) / (80 - CALL_STRONG_MIN);
      return interpolateColor('#7FBFA0', '#10b981', ratio);
    } else {
      // 80+: Strong green (excellent call opportunity)
      return '#10b981'; // green-500
    }
  };

  // Helper function to interpolate between two hex colors
  const interpolateColor = (color1, color2, ratio) => {
    const hex = (c) => {
      const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(c);
      return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
      } : null;
    };
    
    const c1 = hex(color1);
    const c2 = hex(color2);
    
    if (!c1 || !c2) return color1;
    
    const r = Math.round(c1.r + (c2.r - c1.r) * ratio);
    const g = Math.round(c1.g + (c2.g - c1.g) * ratio);
    const b = Math.round(c1.b + (c2.b - c1.b) * ratio);
    
    return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
  };

  // Plugin to draw shaded zones for good opportunity areas
  const opportunityZonesPlugin = {
    id: 'opportunityZones',
    beforeDatasetsDraw: (chart) => {
      if (!chart.chartArea || !chart.scales?.y) return;
      
      const { ctx, chartArea, scales } = chart;
      const { top, bottom, left, right } = chartArea;
      const { y } = scales;
      
      ctx.save();
      
      // Calculate y positions for call transition, strong-call, and put zones
      const yCallStart = y.getPixelForValue(CALL_SIGNAL_MIN);
      const yCallStrong = y.getPixelForValue(CALL_STRONG_MIN);
      const yPut = y.getPixelForValue(PUT_SIGNAL_MAX);
      const yTop = y.getPixelForValue(100);
      const yBottom = y.getPixelForValue(0);
      
      // Strong-call green tint above 75, then a smooth 75 -> 70 fade. The green
      // ramps from full strength at 75 down to fully transparent at 70, so the
      // 70-74 transition band reads as a soft gradient with NO hard line/edge at
      // either boundary — the gradient itself is the only marker of the floor.
      ctx.fillStyle = 'rgba(16, 185, 129, 0.09)';
      ctx.fillRect(left, yTop, right - left, yCallStrong - yTop);

      const transitionGradient = ctx.createLinearGradient(0, yCallStrong, 0, yCallStart);
      transitionGradient.addColorStop(0, 'rgba(16, 185, 129, 0.09)'); // 75: matches the strong zone
      transitionGradient.addColorStop(1, 'rgba(16, 185, 129, 0)');    // 70: dissolves into the background
      ctx.fillStyle = transitionGradient;
      ctx.fillRect(left, yCallStrong, right - left, yCallStart - yCallStrong);
      
      // Red zone below 25 (put opportunities)
      ctx.fillStyle = 'rgba(239, 68, 68, 0.08)';
      ctx.fillRect(left, yPut, right - left, yBottom - yPut);
      
      ctx.restore();
    }
  };

  // Plugin to add glow effect to Overall Score line with dynamic color
  const glowPlugin = {
    id: 'glowEffect',
    beforeDatasetsDraw: (chart) => {
      if (!chart.ctx) return;
      
      const ctx = chart.ctx;
      ctx.save();
      
      // Apply subtle white glow to Overall Score line (works with all colors)
      const meta = chart.getDatasetMeta(0);
      if (meta && !meta.hidden) {
        ctx.shadowColor = 'rgba(255, 255, 255, 0.4)';
        ctx.shadowBlur = 10;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
      }
    },
    afterDatasetsDraw: (chart) => {
      if (!chart.ctx) return;
      const ctx = chart.ctx;
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
      
      ctx.fillText('OVR', centerX, centerY);
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
        label: 'Overall Score',
        data: reversedScores.map(score => score.overall),
        borderWidth: 3.5,
        fill: false,
        tension: 0.3,
        pointRadius: 3.5,
        pointHoverRadius: 6,
        pointBorderWidth: 0,
        pointHoverBorderWidth: 2,
        spanGaps: false, // Don't draw line through null values
        segment: {
          borderColor: (ctx) => {
            // Color each segment based on the average of the two points
            if (ctx.p0DataIndex !== undefined && ctx.p1DataIndex !== undefined) {
              const score1 = reversedScores[ctx.p0DataIndex]?.overall;
              const score2 = reversedScores[ctx.p1DataIndex]?.overall;
              
              // Don't draw segments into null future data
              if (score1 === null || score2 === null) {
                return 'transparent';
              }
              
              const avgScore = (score1 + score2) / 2;
              return getScoreGradientColor(avgScore);
            }
            return '#6b7280';
          }
        },
        pointBackgroundColor: (ctx) => {
          const score = reversedScores[ctx.dataIndex]?.overall;
          if (score === null) return 'transparent';
          return getScoreGradientColor(score);
        },
        pointBorderColor: (ctx) => {
          const score = reversedScores[ctx.dataIndex]?.overall;
          if (score === null) return 'transparent';
          return '#ffffff';
        },
      },
      {
        label: 'BB Score',
        data: reversedScores.map(score => score.bb),
        borderColor: 'rgba(139, 92, 246, 0.35)', // Purple, translucent
        legendColor: '#8b5cf6', // Solid purple for legend
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: 'RSI Score',
        data: reversedScores.map(score => score.rsi),
        borderColor: 'rgba(236, 72, 153, 0.35)', // Pink, translucent
        legendColor: '#ec4899', // Solid pink for legend
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: 'MACD Score',
        data: reversedScores.map(score => score.macd),
        borderColor: 'rgba(251, 146, 60, 0.35)', // Orange, translucent
        legendColor: '#fb923c', // Solid orange for legend
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: 'Trend Score',
        data: reversedScores.map(score => score.trend),
        borderColor: 'rgba(34, 197, 94, 0.35)', // Green, translucent
        legendColor: '#22c55e', // Solid green for legend
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
      },
      {
        label: 'Volume Score',
        data: reversedScores.map(score => score.volume),
        borderColor: 'rgba(59, 130, 246, 0.35)', // Blue, translucent
        legendColor: '#3b82f6', // Solid blue for legend
        borderWidth: 1.5,
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
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
        display: false // Hide the legend
      },
      tooltip: {
        enabled: showTooltip,
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        borderColor: '#374151',
        borderWidth: 1,
        cornerRadius: 8,
        displayColors: true,
        callbacks: {
          label: function(context) {
            const val = context.parsed.y;
            if (val === null || val === undefined) return null;
            return null;
          },
          afterBody: function(contexts) {
            if (!contexts.length) return [];
            const score = reversedScores[contexts[0].dataIndex];
            return scoreBreakdownTooltipLines(score);
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
          display: false // Hide date labels
        }
      },
      y: {
        min: 0,
        max: 100,
        grid: {
          color: (context) => {
            const value = context?.tick?.value;
            // 75 (strong-call) and 25 (put) stay as subtle reference lines. 70 is
            // intentionally NOT a line — the green gradient fill conveys the floor,
            // so the chart body has no hard edge at the 70 transition boundary.
            if (value === PUT_SIGNAL_MAX || value === CALL_STRONG_MIN) {
              return '#6b7280'; // More visible gray for thresholds
            }
            if (value === 0 || value === 50 || value === 100) {
              return '#374151'; // Subtle gray for reference lines
            }
            return 'transparent'; // Hide other gridlines (incl. the 70 floor)
          },
          lineWidth: (context) => {
            const value = context?.tick?.value;
            if (value === PUT_SIGNAL_MAX || value === CALL_STRONG_MIN) {
              return 1.5;
            }
            return 0.5;
          },
          drawBorder: false,
        },
        ticks: {
          color: (context) => {
            // Highlight signal reference tick labels
            if (
              context?.tick?.value === PUT_SIGNAL_MAX ||
              context?.tick?.value === CALL_SIGNAL_MIN ||
              context?.tick?.value === CALL_STRONG_MIN
            ) {
              return '#d1d5db';
            }
            return '#9ca3af';
          },
          font: (context) => {
            // 70's tick label stays brighter (via the color callback) but is no
            // longer bold — only the strong 25/75 thresholds get heavy emphasis,
            // so nothing reads as a hard marker at the 70 floor.
            if (
              context?.tick?.value === PUT_SIGNAL_MAX ||
              context?.tick?.value === CALL_STRONG_MIN
            ) {
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
        if (showTooltip) {
          onHoverIndex(null);
        }
      }
    },
    onClick: (event, activeElements) => {
      if (activeElements && activeElements.length > 0) {
        onHoverIndex(activeElements[0].index);
      }
    }
  };

  // Highlight points based on hoveredIndex
  const highlightedData = {
    ...data,
    datasets: data.datasets.map((dataset, datasetIndex) => {
      if (datasetIndex === 0) {
        // Overall Score - use gradient colors
        return {
          ...dataset,
          pointRadius: dataset.data.map((_, index) => 
            hoveredIndex === index ? 7 : 3.5
          ),
          pointBackgroundColor: dataset.data.map((_, index) => {
            if (hoveredIndex === index) return '#ffffff';
            const score = reversedScores[index]?.overall;
            return getScoreGradientColor(score);
          }),
          pointBorderColor: dataset.data.map((_, index) => {
            if (hoveredIndex === index) {
              const score = reversedScores[index]?.overall;
              return getScoreGradientColor(score);
            }
            return '#ffffff';
          }),
          pointBorderWidth: dataset.data.map((_, index) => 
            hoveredIndex === index ? 3 : 0
          ),
        };
      } else {
        // Other scores - only show points on hover
        return {
          ...dataset,
          pointRadius: dataset.data.map((_, index) => 
            hoveredIndex === index ? 4 : 0
          ),
          pointBackgroundColor: dataset.data.map((_, index) => 
            hoveredIndex === index ? '#ffffff' : dataset.borderColor
          ),
          pointBorderColor: dataset.data.map((_, index) => {
            if (hoveredIndex === index) {
              return dataset.borderColor.replace('0.35', '1');
            }
            return dataset.borderColor;
          }),
          pointBorderWidth: dataset.data.map((_, index) => 
            hoveredIndex === index ? 2 : 0
          ),
        };
      }
    })
  };

  return (
    <div className="w-full" style={{ height: height || '28vh' }}>
      <Line data={highlightedData} options={options} plugins={[opportunityZonesPlugin, glowPlugin, watermarkPlugin]} />
    </div>
  );
};

export default ScoreChart; 
