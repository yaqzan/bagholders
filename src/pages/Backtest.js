import React, { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { Play, Activity, BarChart2, Clock, ChevronDown, ChevronUp, Trash2, Pencil, Check, X, RefreshCw, Save } from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  LogarithmicScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip as ChartTooltip,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import ScoreVersionSelector from '../components/ScoreVersionSelector';
import PortfolioProfileToggle, { DEFAULT_PORTFOLIO_PROFILES, normalizeProfiles } from '../components/PortfolioProfileToggle';
import { shuffledPunchlineLoadingPhases, PUNCHLINE_LOADING_PHASES } from '../utils/punchlineLoadingText';

ChartJS.register(CategoryScale, LinearScale, LogarithmicScale, PointElement, LineElement, Filler, ChartTooltip);

const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const DEFAULT_CAD_PER_USD = 1.37;
const PORTFOLIO_PROFILE_STORAGE_KEY = 'backtestPortfolioProfile20260523';
const CALL_BE = 56.3;
const PUT_BE  = 43.5;

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

// ── Default advanced params (optimal strategy) ────────────────────────────────
// Updated 2026-04-28 (v27 H5_HOLD15_H40 winner): 22-now +55B%, DD-C 73.5%
// after path-dependent SL P&L bug fix and joint Bayesian optimization.
// See experiments/v27_optimization/FINAL_RECOMMENDATION.md for details.
// Fallback defaults — used until /api/strategy/config returns. Mirror current
// production values so the UI is functional pre-fetch and if the endpoint is
// unreachable. The defaultsFromCfg() helper below replaces these with values
// fetched from strategy_config.py — single source of truth.
// FALLBACK ONLY — used when /api/strategy/config fetch fails. Live values
// come from defaultsFromCfg(j['30dte']) below. Keep these synced with
// strategy_config.py STRATEGY_30DTE so a fetch failure doesn't show wildly
// stale defaults. Last updated 2026-06-02 for v70 Apex HOLD portfolio config.
const DEFAULT_ADVANCED = {
  tp_pct:               10,        // tpsl_refine ship 2026-08-10 (scalp-and-dead-hold, base==stress)
  tp_stress_pct:        30,        // v70 Apex
  sl_pct:               100,       // tpsl_refine ship 2026-08-10 (disaster stop; deep fires reroute to dead-hold)
  sl_stress_pct:        70,        // v70 Apex
  breadth_thresh:       40,        // inert now (base==stress)
  put_tp_pct:           35,
  put_sl_pct:           20,
  put_sl_hold_default:  0,
  put_sl_hold_monday:   0,
  breadth_adaptive:     true,
  hard_sell_day:        15,
  hard_sell_loss_pct:   40,
  max_positions:        14,        // v70 Apex (full pool, calls-only)
  max_positions_call:   14,
  max_positions_put:    0,
  practical_exposure_enabled: true,
  practical_capital_ceiling: 0,    // v70 Apex UNCAPPED (max compounding; Core/Sentinel cap)
  gross_premium_cap:    50,        // v70 Apex exposure peak (50% > 65% > off)
  call_premium_cap:     50,
  put_premium_cap:      0,         // puts off
  opp_sat_call_ref:     16,
  opp_sat_put_ref:      4,
  opp_sat_power:        0.50,
  opp_sat_floor:        0.55,
  alloc_95plus:         20,        // v70 Apex 75+ full cascade (HOLD makes 75+ tradeable)
  alloc_85_94:          15,
  alloc_80_84:          10,
  alloc_75_79:          10,
  alloc_70_74:          0,
  alloc_p15:            0,         // puts OFF (net-negative on honest scores)
  alloc_p16_20:         0,
  alloc_p21_25:         0,
  breadth_alloc_enabled: true,
  f3f_call_thresh:      50,
  f3f_call_floor:       0.50,
  f3f_call_low:         30,        // was 20; H3 ship Phase 8/9
  f3f_put_thresh:       75,
  f3f_put_floor:        0.50,
  f3f_put_high:         95,
  dd_soft_lo:           0.35,      // v60 recalibration (30 DTE)
  dd_soft_hi:           0.55,
  dd_soft_floor:        0.40,
  rxdd_enabled:         true,      // RXDD VIX-band call dampener (30 DTE, ship 2026-06-04)
  rxdd_vix_c:           22.701,
  rxdd_vix_w:           3.14,
  rxdd_depth:           0.447,
  rxdd_dd_min:          0.077,
  mwdd_enabled:         true,      // MWDD McClellan flat-band call dampener (30 DTE, ship 2026-06-05)
  mwdd_mcc_c:           -0.336,
  mwdd_mcc_w:           22.185,
  mwdd_depth:           0.337,
  mwdd_dd_min:          0.128,
  mwdd_vix_panic:       28.0,
  tvdd_enabled:         true,      // TVDD TRIN volume-flow neutral-band call dampener (30 DTE, ship 2026-06-07)
  tvdd_trin_c:          1.042,
  tvdd_trin_w:          0.268,
  tvdd_depth:           0.426,
  tvdd_dd_min:          0.291,
  tvdd_vix_panic:       28.0,
  bdiv_enabled:         true,      // BDIV pre-top breadth-divergence-at-highs call dampener (30 DTE, ship 2026-06-10)
  bdiv_prox_cut:        0.0198,
  bdiv_prox_full:       0.0075,
  bdiv_gap_c:           7.716,
  bdiv_gap_w:           3.4571,
  bdiv_depth:           0.53,
  svr_enabled:          true,      // SVR semivol_r skew-bridge entry filter (30 DTE, ship 2026-06-05)
  svr_lo_cut:           0.50,
  svr_lo_full:          0.70,
  svr_hi_full:          1.25,
  svr_hi_cut:           1.65,
  svr_floor:            0.50,
  spread_tilt_enabled:  true,      // SPREAD_TILT 75-79 component-spread call alloc haircut (30 DTE, ship 2026-06-15)
  spread_tilt_lo:       26.9,
  spread_tilt_hi:       31.3,
  spread_tilt_depth:    0.40,
  liquidity_floor:      0,         // LIQUIDITY_FLOOR option-volume admission filter (staged ship-candidate 2026-08-07; 0=OFF, Core-evidence-only)
  // CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08)
  // Read-only display; backend reads from strategy_config.
  ctsl_enabled:         true,
  ctsl_call_trend_max:  15,
  ctsl_call_target:     98.4,
  ctsl_call_alpha:      0.56,
  ctsl_call_tier_floor: 74.7,
  ctsl_put_trend_min:   76,
  ctsl_put_target:      -0.13,
  ctsl_put_alpha:       0.83,
  ctsl_put_tier_ceiling: 27.9,
};

// Map /api/strategy/config response (one DTE) to DEFAULT_ADVANCED shape.
// `cfg` is `j['30dte']` or `j['15dte']` from the endpoint — see api.py
// get_strategy_config() and strategy_config.to_json_dict().
function defaultsFromCfg(cfg) {
  if (!cfg) return DEFAULT_ADVANCED;
  const opt = cfg.option;
  return {
    tp_pct:               Math.round(opt.TP_BASE * 100),
    tp_stress_pct:        Math.round(opt.TP_STRESS * 100),
    sl_pct:               Math.round(Math.abs(opt.SL_BASE) * 100),
    sl_stress_pct:        Math.round(Math.abs(opt.SL_STRESS) * 100),
    breadth_thresh:       opt.BREADTH_THRESHOLD,
    put_tp_pct:           Math.round(opt.PUT_TP * 100),
    put_sl_pct:           Math.round(Math.abs(opt.PUT_SL) * 100),
    put_sl_hold_default:  opt.PUT_SL_HOLD_BARS_DEFAULT,
    put_sl_hold_monday:   opt.PUT_SL_HOLD_BARS_MONDAY,
    breadth_adaptive:     true,
    hard_sell_day:        cfg.HOLD_DAYS,
    hard_sell_loss_pct:   Math.round(Math.abs(cfg.HARD_SELL_LOSS) * 100),
    max_positions:        cfg.MAX_POSITIONS,
    max_positions_call:   cfg.MAX_POSITIONS_CALL,
    max_positions_put:    cfg.MAX_POSITIONS_PUT,
    practical_exposure_enabled: cfg.PRACTICAL_EXPOSURE_ENABLED,
    practical_capital_ceiling: cfg.PRACTICAL_CAPITAL_CEILING,
    gross_premium_cap:    Math.round((cfg.GROSS_PREMIUM_CAP || 0) * 100),
    call_premium_cap:     Math.round((cfg.CALL_PREMIUM_CAP || 0) * 100),
    put_premium_cap:      Math.round((cfg.PUT_PREMIUM_CAP || 0) * 100),
    opp_sat_call_ref:     cfg.OPP_SAT_CALL_REF,
    opp_sat_put_ref:      cfg.OPP_SAT_PUT_REF,
    opp_sat_power:        cfg.OPP_SAT_POWER,
    opp_sat_floor:        cfg.OPP_SAT_FLOOR,
    alloc_95plus:         Math.round(cfg.TIER_ALLOC.ultra    * 100),
    alloc_85_94:          Math.round(cfg.TIER_ALLOC.top      * 100),
    alloc_80_84:          Math.round(cfg.TIER_ALLOC.mid      * 100),
    alloc_75_79:          Math.round(cfg.TIER_ALLOC.low      * 100),
    alloc_70_74:          Math.round(cfg.TIER_ALLOC.overflow * 100),
    alloc_p15:            Math.round(cfg.PUT_TIER_ALLOC.put_top * 100),
    alloc_p16_20:         Math.round(cfg.PUT_TIER_ALLOC.put_mid * 100),
    alloc_p21_25:         Math.round(cfg.PUT_TIER_ALLOC.put_low * 100),
    breadth_alloc_enabled: cfg.BREADTH_ALLOC_ENABLED,
    f3f_call_thresh:      cfg.F3F_CALL_THRESH,
    f3f_call_floor:       cfg.F3F_CALL_FLOOR,
    f3f_call_low:         cfg.F3F_CALL_LOW,
    f3f_put_thresh:       cfg.F3F_PUT_THRESH,
    f3f_put_floor:        cfg.F3F_PUT_FLOOR,
    f3f_put_high:         cfg.F3F_PUT_HIGH,
    dd_soft_lo:           cfg.DD_SOFT_BAND_LO,
    dd_soft_hi:           cfg.DD_SOFT_BAND_HI,
    dd_soft_floor:        cfg.DD_SOFT_CALL_FLOOR,
    rxdd_enabled:         cfg.RXDD_ENABLED,
    rxdd_vix_c:           cfg.RXDD_VIX_C,
    rxdd_vix_w:           cfg.RXDD_VIX_W,
    rxdd_depth:           cfg.RXDD_DEPTH,
    rxdd_dd_min:          cfg.RXDD_DD_MIN,
    mwdd_enabled:         cfg.MWDD_ENABLED,
    mwdd_mcc_c:           cfg.MWDD_MCC_C,
    mwdd_mcc_w:           cfg.MWDD_MCC_W,
    mwdd_depth:           cfg.MWDD_DEPTH,
    mwdd_dd_min:          cfg.MWDD_DD_MIN,
    mwdd_vix_panic:       cfg.MWDD_VIX_PANIC,
    tvdd_enabled:         cfg.TVDD_ENABLED,
    tvdd_trin_c:          cfg.TVDD_TRIN_C,
    tvdd_trin_w:          cfg.TVDD_TRIN_W,
    tvdd_depth:           cfg.TVDD_DEPTH,
    tvdd_dd_min:          cfg.TVDD_DD_MIN,
    tvdd_vix_panic:       cfg.TVDD_VIX_PANIC,
    bdiv_enabled:         cfg.BDIV_ENABLED,
    bdiv_prox_cut:        cfg.BDIV_PROX_CUT,
    bdiv_prox_full:       cfg.BDIV_PROX_FULL,
    bdiv_gap_c:           cfg.BDIV_GAP_C,
    bdiv_gap_w:           cfg.BDIV_GAP_W,
    bdiv_depth:           cfg.BDIV_DEPTH,
    svr_enabled:          cfg.SVR_ENABLED,
    svr_lo_cut:           cfg.SVR_LO_CUT,
    svr_lo_full:          cfg.SVR_LO_FULL,
    svr_hi_full:          cfg.SVR_HI_FULL,
    svr_hi_cut:           cfg.SVR_HI_CUT,
    svr_floor:            cfg.SVR_FLOOR,
    spread_tilt_enabled:  cfg.SPREAD_TILT_ENABLED,
    spread_tilt_lo:       cfg.SPREAD_TILT_LO,
    spread_tilt_hi:       cfg.SPREAD_TILT_HI,
    spread_tilt_depth:    cfg.SPREAD_TILT_DEPTH,
    liquidity_floor:      cfg.LIQUIDITY_FLOOR,
    // CTSL — Stage 1 winner shipped 2026-05-08; read-only display.
    ctsl_enabled:         cfg.CTSL_ENABLED,
    ctsl_call_trend_max:  cfg.CTSL_CALL_TREND_MAX,
    ctsl_call_target:     cfg.CTSL_CALL_TARGET,
    ctsl_call_alpha:      cfg.CTSL_CALL_ALPHA,
    ctsl_call_tier_floor: cfg.CTSL_CALL_TIER_FLOOR,
    ctsl_put_trend_min:   cfg.CTSL_PUT_TREND_MIN,
    ctsl_put_target:      cfg.CTSL_PUT_TARGET,
    ctsl_put_alpha:       cfg.CTSL_PUT_ALPHA,
    ctsl_put_tier_ceiling: cfg.CTSL_PUT_TIER_CEILING,
  };
}

function applyPortfolioProfileToAdv(base, profile) {
  const params = profile?.params || {};
  const next = { ...base };
  const pct = (value) => Math.round(Number(value || 0) * 100);
  if (params.max_positions != null) next.max_positions = params.max_positions;
  if (params.call_max != null) next.max_positions_call = params.call_max;
  if (params.put_max != null) next.max_positions_put = params.put_max;
  if (params.practical_enabled != null) next.practical_exposure_enabled = !!params.practical_enabled;
  if (params.capital_ceiling != null) next.practical_capital_ceiling = params.capital_ceiling;
  if (params.gross_cap != null) next.gross_premium_cap = pct(params.gross_cap);
  if (params.call_cap != null) next.call_premium_cap = pct(params.call_cap);
  if (params.put_cap != null) next.put_premium_cap = pct(params.put_cap);
  if (params.call_ref != null) next.opp_sat_call_ref = params.call_ref;
  if (params.put_ref != null) next.opp_sat_put_ref = params.put_ref;
  if (params.sat_power != null) next.opp_sat_power = params.sat_power;
  if (params.sat_floor != null) next.opp_sat_floor = params.sat_floor;
  if (params.dd_lo != null) next.dd_soft_lo = params.dd_lo;
  if (params.dd_hi != null) next.dd_soft_hi = params.dd_hi;
  if (params.dd_floor != null) next.dd_soft_floor = params.dd_floor;
  if (params.tier_ultra != null) next.alloc_95plus = pct(params.tier_ultra);
  if (params.tier_top != null) next.alloc_85_94 = pct(params.tier_top);
  if (params.tier_mid != null) next.alloc_80_84 = pct(params.tier_mid);
  if (params.tier_low != null) next.alloc_75_79 = pct(params.tier_low);
  if (params.tier_overflow != null) next.alloc_70_74 = pct(params.tier_overflow);
  if (params.put_top != null) next.alloc_p15 = pct(params.put_top);
  if (params.put_mid != null) next.alloc_p16_20 = pct(params.put_mid);
  if (params.put_low != null) next.alloc_p21_25 = pct(params.put_low);
  return next;
}

// Derive the main-form signal params a profile implies from its (profiled) adv:
// puts-off (no put allocation) => Calls Only; the min call score = the lowest
// FUNDED call tier (Sentinel zeroes the 75-79/80-84 tiers => 85+-only => min 85).
function signalParamsFromAdv(adv) {
  const callsOnly = !(
    Number(adv.alloc_p15) || Number(adv.alloc_p16_20) || Number(adv.alloc_p21_25)
  );
  const minCall = Number(adv.alloc_75_79) > 0 ? 75
                : Number(adv.alloc_80_84) > 0 ? 80
                : Number(adv.alloc_85_94) > 0 ? 85
                : Number(adv.alloc_95plus) > 0 ? 95 : 75;
  return { calls_only: callsOnly, min_score: minCall };
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtMoney(v, inCad, cadPerUsd = DEFAULT_CAD_PER_USD) {
  if (v == null) return '—';
  const display = inCad ? v * cadPerUsd : v;
  const suf = inCad ? ' CAD' : ' USD';
  const a = Math.abs(display);
  if (a >= 1e12) return `${v >= 0 ? '' : '-'}$${(Math.abs(display) / 1e12).toFixed(2)}T${suf}`;
  if (a >= 1e9)  return `${v >= 0 ? '' : '-'}$${(Math.abs(display) / 1e9 ).toFixed(2)}B${suf}`;
  if (a >= 1e6)  return `${v >= 0 ? '' : '-'}$${(Math.abs(display) / 1e6 ).toFixed(2)}M${suf}`;
  if (a >= 1e3)  return `${v >= 0 ? '' : '-'}$${(Math.abs(display) / 1e3 ).toFixed(0)}k${suf}`;
  return `${display >= 0 ? '' : '-'}$${Math.abs(Math.round(display)).toLocaleString()}${suf}`;
}

function fmtPnl(pnl, inCad, cadPerUsd) {
  if (pnl == null) return '—';
  const display = inCad ? pnl * cadPerUsd : pnl;
  const suf = inCad ? ' CAD' : ' USD';
  const sign = display >= 0 ? '+' : '-';
  const a = Math.abs(display);
  if (a >= 1e6)  return `${sign}$${(a / 1e6).toFixed(2)}M${suf}`;
  if (a >= 1e3)  return `${sign}$${(a / 1e3).toFixed(0)}k${suf}`;
  return `${sign}$${Math.round(a).toLocaleString()}${suf}`;
}

function fmtPct(v, d = 1) {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(d) + '%';
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}
function startOfYearStr() {
  return `${new Date().getFullYear()}-01-01`;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function SegmentedDateInput({ value, onChange, className }) {
  const yyRef = useRef(null);
  const mmRef = useRef(null);
  const ddRef = useRef(null);

  const parse = (v) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '');
    if (!m) return { yy: '', mm: '', dd: '' };
    return { yy: m[1].slice(2), mm: m[2], dd: m[3] };
  };

  const [seg, setSeg] = useState(() => parse(value));

  useEffect(() => {
    setSeg(parse(value));
  }, [value]);

  const emit = (next) => {
    const { yy, mm, dd } = next;
    if (yy.length === 2 && mm.length === 2 && dd.length === 2) {
      const iso = `20${yy}-${mm}-${dd}`;
      const d = new Date(iso);
      if (!isNaN(d.getTime())) {
        onChange({ target: { value: iso } });
      }
    }
  };

  const focusSelect = (ref) => {
    const el = ref?.current;
    if (el) {
      el.focus();
      el.select();
    }
  };

  const handleChange = (key, max, nextRef) => (e) => {
    let v = e.target.value.replace(/\D/g, '');
    if (v.length > 2) v = v.slice(-2);

    if (v.length === 1 && key !== 'yy') {
      const d = parseInt(v, 10);
      const tensMax = Math.floor(max / 10);
      if (d > tensMax) v = '0' + v;
    }

    let advance = false;
    if (v.length === 2) {
      let n = parseInt(v, 10);
      if (n > max) v = String(max).padStart(2, '0');
      else if (key !== 'yy' && n < 1) v = '01';
      advance = true;
    }

    const next = { ...seg, [key]: v };
    setSeg(next);
    if (advance && nextRef) focusSelect(nextRef);
    emit(next);
  };

  const handleKeyDown = (key, prevRef, nextRef) => (e) => {
    if (e.key === 'Backspace' && seg[key] === '' && prevRef) {
      e.preventDefault();
      focusSelect(prevRef);
    } else if (e.key === '-' || e.key === '/' || e.key === ' ') {
      e.preventDefault();
      if (nextRef) focusSelect(nextRef);
    } else if (e.key === 'ArrowLeft' && prevRef && e.target.selectionStart === 0) {
      e.preventDefault();
      focusSelect(prevRef);
    } else if (e.key === 'ArrowRight' && nextRef && e.target.selectionEnd === seg[key].length) {
      e.preventDefault();
      focusSelect(nextRef);
    }
  };

  const segCls = 'bg-transparent text-gray-200 outline-none text-center p-0 m-0 border-0';
  const containerCls = (className || '') + ' flex items-center font-mono';

  return (
    <div className={containerCls}>
      <span className="text-gray-500 select-none">20</span>
      <input
        ref={yyRef}
        type="text"
        inputMode="numeric"
        value={seg.yy}
        onChange={handleChange('yy', 99, mmRef)}
        onFocus={(e) => e.target.select()}
        onKeyDown={handleKeyDown('yy', null, mmRef)}
        placeholder="YY"
        aria-label="Year (last two digits)"
        className={segCls}
        style={{ width: '2.2ch' }}
      />
      <span className="text-gray-500 select-none">-</span>
      <input
        ref={mmRef}
        type="text"
        inputMode="numeric"
        value={seg.mm}
        onChange={handleChange('mm', 12, ddRef)}
        onFocus={(e) => e.target.select()}
        onKeyDown={handleKeyDown('mm', yyRef, ddRef)}
        placeholder="MM"
        aria-label="Month"
        className={segCls}
        style={{ width: '2.2ch' }}
      />
      <span className="text-gray-500 select-none">-</span>
      <input
        ref={ddRef}
        type="text"
        inputMode="numeric"
        value={seg.dd}
        onChange={handleChange('dd', 31, null)}
        onFocus={(e) => e.target.select()}
        onKeyDown={handleKeyDown('dd', mmRef, null)}
        placeholder="DD"
        aria-label="Day"
        className={segCls}
        style={{ width: '2.2ch' }}
      />
    </div>
  );
}

function StatCard({ label, value, sub, valueClass = 'text-gray-100' }) {
  return (
    <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] px-3 py-2.5 min-w-0">
      <p className="text-[10px] font-medium tracking-wider uppercase text-gray-500 mb-1">{label}</p>
      <p className={`text-[15px] font-semibold font-mono leading-none ${valueClass}`}>{value}</p>
      {sub && <p className="text-[10px] text-gray-600 mt-1 leading-tight">{sub}</p>}
    </div>
  );
}

function LoadingPanel({ phase }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
      <div className="relative w-10 h-10">
        <div className="absolute inset-0 rounded-full border-2 border-trading-green-400/20" />
        <div className="absolute inset-0 rounded-full border-2 border-t-trading-green-400 border-r-transparent border-b-transparent border-l-transparent animate-spin" />
      </div>
      <div>
        <p className="text-[13px] text-gray-200 font-medium">{phase}</p>
        <p className="text-[11px] text-gray-600 mt-1">This may take 10 – 40 seconds depending on date range</p>
      </div>
    </div>
  );
}

function EquityCurveChart({ equityCurve, inCad, cadPerUsd }) {
  const [logScale, setLogScale] = useState(false);

  const labels = useMemo(() => equityCurve.map(p => p.date), [equityCurve]);
  const values = useMemo(
    () => equityCurve.map(p => inCad ? p.equity * cadPerUsd : p.equity),
    [equityCurve, inCad, cadPerUsd],
  );

  const data = {
    labels,
    datasets: [{
      data:            values,
      borderColor:     '#8FB286',
      borderWidth:     1.5,
      pointRadius:     0,
      pointHoverRadius: 3,
      fill:            true,
      backgroundColor: (ctx) => {
        const chart = ctx.chart;
        const { ctx: c, chartArea } = chart;
        if (!chartArea) return 'transparent';
        const grad = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        grad.addColorStop(0, 'rgba(143,178,134,0.18)');
        grad.addColorStop(1, 'rgba(143,178,134,0.01)');
        return grad;
      },
      tension: 0.2,
    }],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#111113',
        borderColor: 'rgba(255,255,255,0.08)',
        borderWidth: 1,
        titleColor: '#9CA3AF',
        bodyColor: '#E5E7EB',
        titleFont: { size: 10, family: 'JetBrains Mono, monospace' },
        bodyFont:  { size: 11, family: 'JetBrains Mono, monospace' },
        padding: 8,
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed.y;
            return ` ${fmtMoney(inCad ? v / cadPerUsd : v, inCad, cadPerUsd)}`;
          },
        },
      },
    },
    scales: {
      x: {
        ticks: {
          color: '#4B5563',
          font: { size: 9, family: 'JetBrains Mono, monospace' },
          maxTicksLimit: 8,
          maxRotation: 0,
        },
        grid:   { color: 'rgba(255,255,255,0.04)', drawBorder: false },
        border: { display: false },
      },
      y: {
        type: logScale ? 'logarithmic' : 'linear',
        ticks: {
          color: '#4B5563',
          font: { size: 9, family: 'JetBrains Mono, monospace' },
          maxTicksLimit: 5,
          callback: (v) => fmtMoney(inCad ? v / cadPerUsd : v, inCad, cadPerUsd),
        },
        grid:   { color: 'rgba(255,255,255,0.04)', drawBorder: false },
        border: { display: false },
      },
    },
  };

  return (
    <div>
      <div className="flex items-center justify-end mb-1.5">
        <button
          onClick={() => setLogScale(s => !s)}
          className={`text-[9px] font-mono px-2 py-0.5 rounded border transition-colors ${
            logScale
              ? 'border-trading-green-400/40 text-trading-green-300 bg-trading-green-400/10'
              : 'border-white/[0.08] text-gray-600 hover:text-gray-400'
          }`}
        >
          {logScale ? 'LOG' : 'LIN'}
        </button>
      </div>
      <div style={{ height: 220 }}>
        <Line data={data} options={options} />
      </div>
    </div>
  );
}

function TierTable({ tierStats }) {
  return (
    <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] overflow-hidden">
      <div className="px-3 py-2 border-b border-white/[0.06]">
        <p className="text-[11px] font-medium text-gray-400 uppercase tracking-wider">Tier Breakdown</p>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-white/[0.04]">
            {['Tier', 'Alloc', 'N', 'TP%', 'Avg hold'].map(h => (
              <th key={h} className="px-3 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider text-gray-600">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tierStats.filter(r => r.n > 0).map(row => (
            <tr key={row.tier} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
              <td className="px-3 py-1.5 text-[11px] font-mono text-gray-300">{row.tier}</td>
              <td className="px-3 py-1.5 text-[11px] font-mono text-gray-500">{row.alloc_pct}%</td>
              <td className="px-3 py-1.5 text-[11px] font-mono text-gray-400">{row.n.toLocaleString()}</td>
              <td className={`px-3 py-1.5 text-[11px] font-mono ${
                row.tp_rate == null ? 'text-gray-600'
                  : row.tp_rate >= CALL_BE ? 'text-trading-green-400'
                  : 'text-trading-red-400'
              }`}>
                {row.tp_rate != null ? `${row.tp_rate}%` : '—'}
              </td>
              <td className="px-3 py-1.5 text-[11px] font-mono text-gray-500">
                {row.avg_hold != null ? `${row.avg_hold}d` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Open Holdings ─────────────────────────────────────────────────────────────

// Column alignment contract (both tables):
//   T_o = T_c = 14.6 → every column with the same flex lands at the same pixel X.
//   Shared widths: SYM=2.0, Score/Orig=1.0, P&L/Curr=1.4, Alloc=1.4, Days=1.0,
//                 EntryDate=1.6, Entry$=1.2.  Exit$(3.4)=Curr$+TP$+SL$ in Open.

const OPEN_COL_DEFS = [
  { key: 'sym',      label: 'Sym',         flex: '2.0 0 0%' },
  { key: 'oscore',   label: 'Orig Score',  flex: '1.0 0 0%' },
  { key: 'cscore',   label: 'Curr Score',  flex: '1.4 0 0%' },
  { key: 'alloc',    label: 'Alloc',       flex: '1.4 0 0%' },
  { key: 'days',     label: 'Days Held',   flex: '1.0 0 0%' },
  { key: 'entry',    label: 'Entry Date',  flex: '1.6 0 0%' },
  { key: 'eprice',   label: 'Entry $',     flex: '1.2 0 0%' },
  { key: 'cprice',   label: 'Current $',   flex: '1.2 0 0%' },
  { key: 'tpprice',  label: 'TP $',        flex: '1.1 0 0%' },
  { key: 'slprice',  label: 'SL $',        flex: '1.1 0 0%' },
  { key: 'deadline', label: 'Hard Sell',   flex: '1.6 0 0%' },
];

function OpenHoldings({ holdings, inCad, cadPerUsd }) {
  if (!holdings || holdings.length === 0) return null;
  return (
    <div className="rounded-md bg-trading-dark-900 border border-amber-400/20 overflow-hidden">
      <div className="px-3 py-2 border-b border-amber-400/10">
        <p className="text-[11px] font-medium text-amber-400/80 uppercase tracking-wider">
          Open Holdings
          <span className="ml-2 text-gray-600 font-normal normal-case">
            ({holdings.length} position{holdings.length !== 1 ? 's' : ''} still open · marked at cost)
          </span>
        </p>
      </div>

      <div className="flex items-center border-b border-white/[0.04] px-2 py-1">
        {OPEN_COL_DEFS.map(col => (
          <div
            key={col.key}
            style={{ flex: col.flex }}
            className="px-1.5 text-[9px] font-medium uppercase tracking-wider text-gray-600 min-w-0"
          >
            {col.label}
          </div>
        ))}
      </div>

      <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
        {holdings.map((h, i) => {
          const side     = h.side || 'call';
          const symColor = side === 'put' ? 'text-trading-red-400' : 'text-trading-green-400';
          const symLabel = `${side === 'put' ? 'P' : 'C'}:${h.symbol}`;
          const cur      = h.current_price;
          const curColor = !cur ? 'text-gray-600'
            : side === 'call'
              ? (cur >= h.entry_price ? 'text-trading-green-400' : 'text-trading-red-400')
              : (cur <= h.entry_price ? 'text-trading-green-400' : 'text-trading-red-400');
          const cs = h.current_score;
          const csColor = cs == null ? 'text-gray-600'
            : side === 'call'
              ? (cs >= 70 ? 'text-trading-green-400' : cs >= 50 ? 'text-gray-400' : 'text-trading-red-400/70')
              : (cs <= 25 ? 'text-trading-red-400' : cs <= 50 ? 'text-gray-400' : 'text-trading-green-400/70');
          return (
            <div
              key={i}
              className="flex items-center border-b border-white/[0.03] px-2 py-0.5 hover:bg-white/[0.02]"
            >
              {/* C:SYM / P:SYM */}
              <div style={{ flex: OPEN_COL_DEFS[0].flex }} className={`px-1.5 text-[11px] font-mono font-medium min-w-0 truncate ${symColor}`}>
                {symLabel}
              </div>
              {/* Orig score */}
              <div style={{ flex: OPEN_COL_DEFS[1].flex }} className="px-1.5 text-[11px] font-mono text-gray-400 min-w-0">
                {h.score}
              </div>
              {/* Curr score — wide to match P&L cell in Closed Trades */}
              <div style={{ flex: OPEN_COL_DEFS[2].flex }} className={`px-1.5 text-[11px] font-mono font-medium min-w-0 ${csColor}`}>
                {cs != null ? cs : '—'}
              </div>
              {/* Alloc */}
              <div style={{ flex: OPEN_COL_DEFS[3].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0">
                {fmtMoney(h.premium, inCad, cadPerUsd)}
              </div>
              {/* Days held */}
              <div style={{ flex: OPEN_COL_DEFS[4].flex }} className="px-1.5 text-[10px] font-mono text-gray-600 min-w-0">
                {h.hold_bars}
              </div>
              {/* Entry date */}
              <div style={{ flex: OPEN_COL_DEFS[5].flex }} className="px-1.5 text-[10px] font-mono text-gray-600 min-w-0 truncate">
                {h.entry_date}
              </div>
              {/* Entry $ */}
              <div style={{ flex: OPEN_COL_DEFS[6].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0">
                {h.entry_price > 0 ? `$${h.entry_price.toFixed(2)}` : '—'}
              </div>
              {/* Current $ */}
              <div style={{ flex: OPEN_COL_DEFS[7].flex }} className={`px-1.5 text-[10px] font-mono font-medium min-w-0 ${curColor}`}>
                {cur ? `$${cur.toFixed(2)}` : '—'}
              </div>
              {/* TP $ */}
              <div style={{ flex: OPEN_COL_DEFS[8].flex }} className="px-1.5 text-[10px] font-mono text-trading-green-400/70 min-w-0">
                {h.tp_price > 0 ? `$${h.tp_price.toFixed(2)}` : '—'}
              </div>
              {/* SL $ */}
              <div style={{ flex: OPEN_COL_DEFS[9].flex }} className="px-1.5 text-[10px] font-mono text-trading-red-400/70 min-w-0">
                {h.sl_price > 0 ? `$${h.sl_price.toFixed(2)}` : '—'}
              </div>
              {/* Hard sell date */}
              <div style={{ flex: OPEN_COL_DEFS[10].flex }} className="px-1.5 text-[10px] font-mono text-amber-400/60 min-w-0 truncate">
                {h.hard_sell_date || '—'}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Trade Log ─────────────────────────────────────────────────────────────────
// Alignment contract: T_c = T_o = 14.6 (equal totals → pixel-perfect alignment).
// Open:   SYM(2.0)|Orig(1.0)|Curr(1.4)|Alloc(1.4)|Days(1.0)|ED(1.6)|E$(1.2)|Cur$(1.2)|TP(1.1)|SL(1.1)|Hard(1.6)
// Closed: SYM(2.0)|Scr (1.0)|P&L (1.4)|Alloc(1.4)|Days(1.0)|ED(1.6)|E$(1.2)|Exit$(3.4)         |ExitDate(1.6)
// Exit$(3.4) = Cur$(1.2)+TP(1.1)+SL(1.1), so Exit Date starts exactly where Hard Sell starts.

const COL_DEFS = [
  { key: 'sym',             label: 'Sym',          flex: '2.0 0 0%' },
  { key: 'score',           label: 'Score',        flex: '1.0 0 0%' },
  { key: 'pnl',             label: 'P&L',          flex: '1.32 0 0%' },
  { key: 'alloc',           label: 'Alloc',        flex: '1.4 0 0%' },
  { key: 'hold',            label: 'Days Held',    flex: '1.0 0 0%' },
  { key: 'entry',           label: 'Entry Date',   flex: '1.6 0 0%' },
  { key: 'entry_price',     label: 'Entry $',      flex: '1.2 0 0%' },
  { key: 'exit_price',      label: 'Exit $',       flex: '1.5 0 0%' },
  { key: 'portfolio_value', label: 'Portfolio $',  flex: '2.0 0 0%' },
  { key: 'exit',            label: 'Exit Date',    flex: '1.6 0 0%' },
];

function TradeLog({ trades, inCad, cadPerUsd }) {
  const [show, setShow] = useState(100);
  if (!trades || trades.length === 0) return null;

  const visible = trades.slice().reverse().slice(0, show);

  return (
    <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] overflow-hidden">
      <div className="px-3 py-2 border-b border-white/[0.06] flex items-center justify-between">
        <p className="text-[11px] font-medium text-gray-400 uppercase tracking-wider">
          Closed Trades
          <span className="ml-2 text-gray-600 font-normal normal-case">({trades.length.toLocaleString()} · newest first)</span>
        </p>
        <div className="flex items-center gap-3">
          {[
            { label: 'TP',   color: 'bg-trading-green-400' },
            { label: 'SL',   color: 'bg-trading-red-400'   },
            { label: 'HARD', color: 'bg-amber-400'          },
          ].map(({ label, color }) => (
            <span key={label} className="flex items-center gap-1">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${color}`} />
              <span className="text-[9px] font-mono text-gray-600">{label}</span>
            </span>
          ))}
        </div>
      </div>

      {/* Header row */}
      <div className="flex items-center border-b border-white/[0.04] px-2 py-1">
        {COL_DEFS.map(col => (
          <div
            key={col.key}
            style={{ flex: col.flex }}
            className="px-1.5 text-[9px] font-medium uppercase tracking-wider text-gray-600 min-w-0"
          >
            {col.label}
          </div>
        ))}
      </div>

      <div className="overflow-y-auto" style={{ maxHeight: 400 }}>
        {visible.map((t, i) => {
          const side     = t.side || 'call';
          const pnlColor = t.pnl >= 0 ? 'text-trading-green-400' : 'text-trading-red-400';
          const symColor = side === 'put' ? 'text-trading-red-400' : 'text-trading-green-400';
          const symLabel  = `${side === 'put' ? 'P' : 'C'}:${t.symbol}`;
          return (
            <div
              key={i}
              className="flex items-center border-b border-white/[0.03] px-2 py-0.5 hover:bg-white/[0.02]"
            >
              {/* C:SYM / P:SYM — colored by trade side */}
              <div style={{ flex: COL_DEFS[0].flex }} className={`px-1.5 text-[11px] font-mono font-medium min-w-0 truncate ${symColor}`}>
                {symLabel}
              </div>
              {/* Score */}
              <div style={{ flex: COL_DEFS[1].flex }} className="px-1.5 text-[11px] font-mono text-gray-400 min-w-0">
                {t.score}
              </div>
              {/* P&L — color encodes signed cash result */}
              <div
                style={{ flex: COL_DEFS[2].flex }}
                title={t.outcome ? `Outcome: ${String(t.outcome).toUpperCase()}` : undefined}
                className={`px-1.5 text-[10px] font-mono min-w-0 ${pnlColor}`}
              >
                {fmtPnl(t.pnl, inCad, cadPerUsd)}
              </div>
              {/* Alloc */}
              <div style={{ flex: COL_DEFS[3].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0">
                {fmtMoney(t.premium, inCad, cadPerUsd)}
              </div>
              {/* Days held */}
              <div style={{ flex: COL_DEFS[4].flex }} className="px-1.5 text-[10px] font-mono text-gray-600 min-w-0">
                {t.hold_bars}
              </div>
              {/* Entry date */}
              <div style={{ flex: COL_DEFS[5].flex }} className="px-1.5 text-[10px] font-mono text-gray-600 min-w-0 truncate">
                {t.entry_date}
              </div>
              {/* Entry $ */}
              <div style={{ flex: COL_DEFS[6].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0">
                {t.entry_price > 0 ? `$${t.entry_price.toFixed(2)}` : '—'}
              </div>
              {/* Exit $ */}
              <div style={{ flex: COL_DEFS[7].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0">
                {t.exit_price > 0 ? `$${t.exit_price.toFixed(2)}` : '—'}
              </div>
              {/* Portfolio $ — total portfolio value after this trade closes */}
              <div style={{ flex: COL_DEFS[8].flex }} className="px-1.5 text-[10px] font-mono text-sky-400/70 min-w-0 truncate">
                {t.portfolio_value ? fmtMoney(t.portfolio_value, inCad, cadPerUsd) : '—'}
              </div>
              {/* Exit date */}
              <div style={{ flex: COL_DEFS[9].flex }} className="px-1.5 text-[10px] font-mono text-gray-500 min-w-0 truncate">
                {t.exit_date}
              </div>
            </div>
          );
        })}
      </div>

      {trades.length > show && (
        <button
          onClick={() => setShow(s => s + 200)}
          className="w-full py-2 text-[11px] text-gray-500 hover:text-gray-300 hover:bg-white/[0.02] transition-colors border-t border-white/[0.04]"
        >
          Show more ({(trades.length - show).toLocaleString()} remaining)
        </button>
      )}
    </div>
  );
}

// ── Saved Runs ────────────────────────────────────────────────────────────────
// Browse / load / label / delete runs auto-saved by /api/backtest/run.
// The backtest is fully deterministic per (version, dte_strategy, params_hash),
// so each row is one cached result. Click a row to reload it without re-running.

function fmtRelTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const diffMs = Date.now() - t;
  const m = Math.floor(diffMs / 60000);
  if (m < 1)    return 'just now';
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30)   return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

function SavedRunsPanel({ dte, versionId, currentRunId, onLoad, refreshKey, inCad, cadPerUsd, onActiveVersion }) {
  const [runs,    setRuns]    = useState([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editValue, setEditValue] = useState('');
  const [collapsed, setCollapsed] = useState(true);   // collapsed by default — the list gets long
  const [showAll, setShowAll] = useState(false);       // when expanded, show only the latest few until "show all"
  const [loadingId, setLoadingId] = useState(null);
  const MAX_SHOWN = 6;

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ dte, limit: 50 });
      if (versionId) {
        qs.set('version', versionId);
      }
      const res = await fetch(`${API_BASE}/api/backtest/runs?${qs}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
      setRuns(json.runs || []);
      if (json.active_version && onActiveVersion) {
        onActiveVersion(json.active_version);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [dte, versionId, onActiveVersion]);

  useEffect(() => { fetchRuns(); }, [fetchRuns, refreshKey]);

  const handleLoad = async (id) => {
    setLoadingId(id);
    try {
      const res = await fetch(`${API_BASE}/api/backtest/runs/${id}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
      onLoad(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingId(null);
    }
  };

  const handleDelete = async (id, e) => {
    e.stopPropagation();
    if (!window.confirm('Delete this saved run?')) return;
    try {
      const res = await fetch(`${API_BASE}/api/backtest/runs/${id}`, { method: 'DELETE' });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      setRuns(rs => rs.filter(r => r.id !== id));
    } catch (err) {
      setError(err.message);
    }
  };

  const startEdit = (r, e) => {
    e.stopPropagation();
    setEditingId(r.id);
    setEditValue(r.label || '');
  };

  const cancelEdit = (e) => {
    e?.stopPropagation();
    setEditingId(null);
    setEditValue('');
  };

  const saveEdit = async (id, e) => {
    e?.stopPropagation();
    try {
      const res = await fetch(`${API_BASE}/api/backtest/runs/${id}/label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: editValue }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
      setRuns(rs => rs.map(r => r.id === id ? { ...r, label: json.label } : r));
      cancelEdit();
    } catch (err) {
      setError(err.message);
    }
  };

  const cellCls = 'px-1.5 text-[10px] font-mono min-w-0 truncate';

  return (
    <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] px-3 py-2.5">
      <div className="flex items-center gap-2 mb-2">
        <Save className="w-3 h-3 text-gray-500" />
        <p className="text-[11px] font-medium text-gray-400 uppercase tracking-wider">
          Saved Runs ({dte} DTE)
        </p>
        <span className="text-[10px] text-gray-700">
          {runs.length} run{runs.length === 1 ? '' : 's'} · auto-saved on each backtest
        </span>
        <button
          type="button"
          onClick={fetchRuns}
          disabled={loading}
          className="ml-auto p-1 hover:bg-white/[0.04] rounded transition-colors disabled:opacity-40"
          title="Refresh list"
        >
          <RefreshCw className={`w-3 h-3 text-gray-500 ${loading ? 'animate-spin' : ''}`} />
        </button>
        <button
          type="button"
          onClick={() => setCollapsed(c => !c)}
          className="p-1 hover:bg-white/[0.04] rounded transition-colors"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed
            ? <ChevronDown className="w-3 h-3 text-gray-500" />
            : <ChevronUp className="w-3 h-3 text-gray-500" />}
        </button>
      </div>

      {error && (
        <p className="text-[10px] text-trading-red-400 font-mono mb-2">{error}</p>
      )}

      {!collapsed && (
        <>
          {runs.length === 0 && !loading && !error && (
            <p className="text-[10px] text-gray-700 font-mono py-3 text-center">
              No saved runs yet. Run a backtest to populate this list.
            </p>
          )}

          {runs.length > 0 && (
            <div className="space-y-0.5">
              {/* Header */}
              <div className="flex items-center gap-1 px-1 py-1 text-[9px] uppercase tracking-wider text-gray-700 border-b border-white/[0.04]">
                <div style={{ flex: '1.2 0 0%' }} className={cellCls}>When</div>
                <div style={{ flex: '1.0 0 0%' }} className={cellCls}>Algo</div>
                <div style={{ flex: '2.0 0 0%' }} className={cellCls}>Range</div>
                <div style={{ flex: '1.2 0 0%' }} className={`${cellCls} text-right`}>Return</div>
                <div style={{ flex: '0.9 0 0%' }} className={`${cellCls} text-right`}>Max DD</div>
                <div style={{ flex: '0.6 0 0%' }} className={`${cellCls} text-right`}>N</div>
                <div style={{ flex: '1.4 0 0%' }} className={cellCls}>Final</div>
                <div style={{ flex: '2.5 0 0%' }} className={cellCls}>Label</div>
                <div style={{ flex: '0.7 0 0%' }} />
              </div>
              {(showAll ? runs : runs.slice(0, MAX_SHOWN)).map(r => {
                const isCurrent = currentRunId === r.id;
                const isLoading = loadingId === r.id;
                const isEditing = editingId === r.id;
                const retClass = r.total_return_pct >= 0
                  ? 'text-trading-green-400' : 'text-trading-red-400';
                const ddClass = r.max_dd_pct <= 50 ? 'text-trading-green-400'
                              : r.max_dd_pct <= 75 ? 'text-amber-400'
                              : 'text-trading-red-400';
                return (
                  <div
                    key={r.id}
                    onClick={() => !isEditing && !isLoading && handleLoad(r.id)}
                    className={`flex items-center gap-1 px-1 py-1 rounded cursor-pointer transition-colors
                      ${isCurrent
                        ? 'bg-trading-green-400/10 border border-trading-green-400/30'
                        : 'hover:bg-white/[0.03] border border-transparent'}`}
                  >
                    <div style={{ flex: '1.2 0 0%' }} className={`${cellCls} text-gray-500`}>
                      {isLoading ? '…' : fmtRelTime(r.run_at)}
                    </div>
                    <div
                      style={{ flex: '1.0 0 0%' }}
                      className={`${cellCls} text-gray-400`}
                      title={r.version?.git_commit
                        ? `${r.version.label} · ${r.version.git_commit}`
                        : (r.version?.label || `v${r.version_id}`)}
                    >
                      {r.version?.label || `v${r.version_id}`}
                    </div>
                    <div style={{ flex: '2.0 0 0%' }} className={`${cellCls} text-gray-400`}>
                      {r.start_date} → {r.end_date}
                    </div>
                    <div style={{ flex: '1.2 0 0%' }} className={`${cellCls} text-right ${retClass}`}>
                      {r.total_return_pct >= 0 ? '+' : ''}{fmtPct(r.total_return_pct, 1)}
                    </div>
                    <div style={{ flex: '0.9 0 0%' }} className={`${cellCls} text-right ${ddClass}`}>
                      {r.max_dd_pct.toFixed(1)}%
                    </div>
                    <div style={{ flex: '0.6 0 0%' }} className={`${cellCls} text-right text-gray-500`}>
                      {r.n_trades}
                    </div>
                    <div style={{ flex: '1.4 0 0%' }} className={`${cellCls} text-gray-500`}>
                      {fmtMoney(r.final_equity, inCad, cadPerUsd)}
                    </div>
                    <div
                      style={{ flex: '2.5 0 0%' }}
                      className={`${cellCls} text-sky-400/70`}
                      onClick={isEditing ? (e) => e.stopPropagation() : undefined}
                    >
                      {isEditing ? (
                        <div className="flex items-center gap-1">
                          <input
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') saveEdit(r.id, e);
                              if (e.key === 'Escape') cancelEdit(e);
                            }}
                            autoFocus
                            placeholder="label…"
                            className="bg-trading-dark-800 border border-white/[0.1] rounded text-[10px] text-gray-200 px-1 py-0.5 font-mono w-full focus:outline-none focus:border-trading-green-400/50"
                          />
                          <button
                            type="button"
                            onClick={(e) => saveEdit(r.id, e)}
                            className="p-0.5 hover:bg-white/[0.04] rounded"
                            title="Save"
                          >
                            <Check className="w-3 h-3 text-trading-green-400" />
                          </button>
                          <button
                            type="button"
                            onClick={cancelEdit}
                            className="p-0.5 hover:bg-white/[0.04] rounded"
                            title="Cancel"
                          >
                            <X className="w-3 h-3 text-gray-500" />
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 group">
                          <span className="truncate flex-1">{r.label || <span className="text-gray-700">—</span>}</span>
                          <button
                            type="button"
                            onClick={(e) => startEdit(r, e)}
                            className="p-0.5 hover:bg-white/[0.04] rounded opacity-0 group-hover:opacity-100 transition-opacity"
                            title="Edit label"
                          >
                            <Pencil className="w-2.5 h-2.5 text-gray-500" />
                          </button>
                        </div>
                      )}
                    </div>
                    <div style={{ flex: '0.7 0 0%' }} className="flex items-center justify-end">
                      <button
                        type="button"
                        onClick={(e) => handleDelete(r.id, e)}
                        className="p-1 hover:bg-trading-red-400/10 rounded transition-colors"
                        title="Delete run"
                      >
                        <Trash2 className="w-3 h-3 text-gray-600 hover:text-trading-red-400" />
                      </button>
                    </div>
                  </div>
                );
              })}
              {runs.length > MAX_SHOWN && (
                <button
                  type="button"
                  onClick={() => setShowAll(s => !s)}
                  className="w-full text-[10px] text-gray-500 hover:text-gray-300 font-mono py-1.5 hover:bg-white/[0.02] rounded transition-colors"
                >
                  {showAll ? `Show fewer (latest ${MAX_SHOWN})` : `Show all ${runs.length} runs…`}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Results ───────────────────────────────────────────────────────────────────

function Results({ data, inCad, cadPerUsd }) {
  const { summary: s, equity_curve, tier_stats, yearly, monthly, trade_log, open_holdings, params } = data;
  const [activeTab, setActiveTab] = useState('yearly');

  const fmt = useCallback((v) => fmtMoney(v, inCad, cadPerUsd), [inCad, cadPerUsd]);

  const returnClass = s.return_pct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400';
  const ddClass     = s.max_dd <= 50 ? 'text-trading-green-400'
                    : s.max_dd <= 75 ? 'text-amber-400'
                    : 'text-trading-red-400';
  const tpClass     = s.tp_rate != null && s.tp_rate >= CALL_BE
                      ? 'text-trading-green-400' : 'text-trading-red-400';

  const currLabel = inCad ? 'CAD' : 'USD';

  return (
    <div className="space-y-3">
      {/* Context badges */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-mono text-gray-600 bg-white/[0.03] border border-white/[0.06] rounded px-2 py-0.5">
          {params.from} → {params.to}
        </span>
        <span className="text-[10px] font-mono text-gray-600 bg-white/[0.03] border border-white/[0.06] rounded px-2 py-0.5">
          {fmt(s.initial)} initial · {currLabel}
        </span>
        <span className="text-[10px] font-mono text-gray-600 bg-white/[0.03] border border-white/[0.06] rounded px-2 py-0.5">
          calls ≥ {params.min_score}
          {!params.calls_only && params.max_put_score != null && ` · puts ≤ ${params.max_put_score}`}
        </span>
        <span className="text-[10px] font-mono text-gray-600 bg-white/[0.03] border border-white/[0.06] rounded px-2 py-0.5">
          {params.portfolio_profile_name || params.portfolio_profile || 'Sentinel'} profile
        </span>
        {params.calls_only && (
          <span className="text-[10px] font-mono text-trading-green-400 bg-trading-green-900/20 border border-trading-green-400/20 rounded px-2 py-0.5">
            calls only
          </span>
        )}
        {!params.breadth_adaptive && (
          <span className="text-[10px] font-mono text-amber-400/70 bg-amber-400/5 border border-amber-400/15 rounded px-2 py-0.5">
            breadth adaptive OFF
          </span>
        )}
        {inCad && (
          <span className="text-[10px] font-mono text-amber-400/70 bg-amber-400/5 border border-amber-400/15 rounded px-2 py-0.5">
            1 USD ≈ {cadPerUsd.toFixed(4)} CAD
          </span>
        )}
        <span className="text-[10px] font-mono text-gray-700 ml-auto">v{params.version}</span>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatCard
          label={`Total Return (${currLabel})`}
          value={fmtPct(s.return_pct, 1)}
          sub={`${fmt(s.initial)} → ${fmt(s.final)}`}
          valueClass={returnClass}
        />
        <StatCard
          label="Max Drawdown"
          value={`${s.max_dd.toFixed(1)}%`}
          valueClass={ddClass}
        />
        <StatCard
          label="TP Rate"
          value={s.tp_rate != null ? `${s.tp_rate}%` : '—'}
          sub={`BE ${s.be_calm}% calm · ${s.be_stressed}% stress`}
          valueClass={tpClass}
        />
        <StatCard
          label="Call TP"
          value={s.call_tp_rate != null ? `${s.call_tp_rate}%` : '—'}
          sub={`${s.n_calls.toLocaleString()} trades`}
          valueClass={s.call_tp_rate != null && s.call_tp_rate >= CALL_BE ? 'text-trading-green-400' : 'text-gray-300'}
        />
        {s.n_puts > 0 && (
          <StatCard
            label="Put TP"
            value={s.put_tp_rate != null ? `${s.put_tp_rate}%` : '—'}
            sub={`${s.n_puts.toLocaleString()} trades · BE ${s.be_put}%`}
            valueClass={s.put_tp_rate != null && s.put_tp_rate >= PUT_BE ? 'text-trading-green-400' : 'text-trading-red-400'}
          />
        )}
        <StatCard
          label="Closed Trades"
          value={s.n_trades.toLocaleString()}
          sub={`TP ${s.n_tp} · SL ${s.n_sl} · Hard ${s.n_hard}`}
        />
      </div>

      {/* Equity curve */}
      {equity_curve && equity_curve.length > 1 && (
        <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] px-3 py-2.5">
          <p className="text-[10px] font-medium tracking-wider uppercase text-gray-600 mb-0">
            Portfolio Equity{inCad ? ' (CAD)' : ' (USD)'}
          </p>
          <EquityCurveChart equityCurve={equity_curve} inCad={inCad} cadPerUsd={cadPerUsd} />
        </div>
      )}

      {/* Tier + temporal tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <TierTable tierStats={tier_stats} />

        {/* Tab switcher for year/month */}
        <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] overflow-hidden">
          <div className="px-3 py-2 border-b border-white/[0.06] flex items-center gap-3">
            {['yearly', 'monthly'].map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`text-[10px] font-medium uppercase tracking-wider transition-colors ${
                  activeTab === tab ? 'text-trading-green-300' : 'text-gray-600 hover:text-gray-400'
                }`}
              >
                {tab === 'yearly' ? 'Year-by-Year' : 'Month-by-Month'}
              </button>
            ))}
          </div>
          {activeTab === 'yearly'
            ? <YearTableInner yearly={yearly} />
            : <MonthTableInner monthly={monthly} />
          }
        </div>
      </div>

      {/* Open holdings */}
      <OpenHoldings holdings={open_holdings} inCad={inCad} cadPerUsd={cadPerUsd} />

      {/* Trade log */}
      <TradeLog trades={trade_log} inCad={inCad} cadPerUsd={cadPerUsd} />
    </div>
  );
}

function YearTableInner({ yearly }) {
  if (!yearly || yearly.length === 0) return <p className="px-3 py-4 text-[11px] text-gray-600">No data</p>;
  return (
    <table className="w-full">
      <thead>
        <tr className="border-b border-white/[0.04]">
          {['Year', 'N', 'TP%', 'C TP%', 'P TP%', 'Return'].map(h => (
            <th key={h} className="px-3 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider text-gray-600">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {yearly.map(row => (
          <tr key={row.year} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
            <td className="px-3 py-1.5 text-[11px] font-mono text-gray-300">{row.year}</td>
            <td className="px-3 py-1.5 text-[11px] font-mono text-gray-400">{(row.n_trades || 0).toLocaleString()}</td>
            <td className={`px-3 py-1.5 text-[11px] font-mono ${
              row.tp_rate == null ? 'text-gray-600' : row.tp_rate >= CALL_BE ? 'text-trading-green-400' : 'text-trading-red-400'
            }`}>{row.tp_rate != null ? `${row.tp_rate}%` : '—'}</td>
            <td className="px-3 py-1.5 text-[11px] font-mono text-gray-500">{row.call_tp_rate != null ? `${row.call_tp_rate}%` : '—'}</td>
            <td className="px-3 py-1.5 text-[11px] font-mono text-gray-500">{row.put_tp_rate != null ? `${row.put_tp_rate}%` : '—'}</td>
            <td className={`px-3 py-1.5 text-[11px] font-mono ${
              row.return_pct == null ? 'text-gray-600' : row.return_pct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'
            }`}>{row.return_pct != null ? fmtPct(row.return_pct, 1) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MonthTableInner({ monthly }) {
  const enriched = useMemo(() => (monthly || []).map((row, idx) => {
    const prev = idx > 0 ? monthly[idx - 1] : null;
    let mom_pct = null;
    if (prev && prev.equity_end != null && row.equity_end != null && prev.equity_end > 0) {
      mom_pct = (row.equity_end / prev.equity_end - 1) * 100;
    }
    return { ...row, mom_pct };
  }), [monthly]);

  if (!enriched.length) return <p className="px-3 py-4 text-[11px] text-gray-600">No data</p>;

  return (
    <div className="overflow-y-auto" style={{ maxHeight: 320 }}>
      <table className="w-full">
        <thead className="sticky top-0 bg-trading-dark-900">
          <tr className="border-b border-white/[0.04]">
            {['Month', 'N', 'TP%', 'C TP%', 'P TP%', 'Return'].map(h => (
              <th key={h} className="px-3 py-1.5 text-left text-[9px] font-medium uppercase tracking-wider text-gray-600">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {enriched.map((row, i) => (
            <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
              <td className="px-3 py-1 text-[10px] font-mono text-gray-400">
                {row.year} <span className="text-gray-600">{MONTH_NAMES[row.month - 1]}</span>
              </td>
              <td className="px-3 py-1 text-[10px] font-mono text-gray-400">{row.n_trades}</td>
              <td className={`px-3 py-1 text-[10px] font-mono ${
                row.tp_rate == null ? 'text-gray-600' : row.tp_rate >= CALL_BE ? 'text-trading-green-400' : 'text-trading-red-400'
              }`}>{row.tp_rate != null ? `${row.tp_rate}%` : '—'}</td>
              <td className="px-3 py-1 text-[10px] font-mono text-gray-500">{row.call_tp_rate != null ? `${row.call_tp_rate}%` : '—'}</td>
              <td className="px-3 py-1 text-[10px] font-mono text-gray-500">{row.put_tp_rate != null ? `${row.put_tp_rate}%` : '—'}</td>
              <td className={`px-3 py-1 text-[10px] font-mono ${
                row.mom_pct == null ? 'text-gray-600' : row.mom_pct >= 0 ? 'text-trading-green-400' : 'text-trading-red-400'
              }`}>{row.mom_pct != null ? fmtPct(row.mom_pct, 1) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Advanced Params Panel ─────────────────────────────────────────────────────

// Tooltip strings are templates — "Default: X%" interpolates from the active
// `adv` state so they reflect what the user actually sees in the form, not a
// frozen literal. getFieldTips(adv) is called from AdvancedPanel via prop.
function getFieldTips(adv) {
  return {
    tp_pct:              `Call take-profit: % gain on option premium required to close in a healthy-breadth regime. Default: ${adv.tp_pct}% (v32_optim ship 2026-05-04 for 30 DTE).`,
    tp_stress_pct:       `Call take-profit in a stressed regime (breadth score ≤ ${adv.breadth_thresh ?? 'BREADTH_THRESHOLD'}). Wider target captures larger moves when volatility is elevated. Default: ${adv.tp_stress_pct}%.`,
    sl_pct:              `Call stop-loss: % loss on option premium that triggers an exit in a healthy regime. Default: ${adv.sl_pct}% (MAE-anchored to call MAEw_15d).`,
    sl_stress_pct:       `Call stop-loss in a stressed regime. Wider stop protects against shakeouts during broad market weakness. Default: ${adv.sl_stress_pct}%.`,
    put_tp_pct:          `Put take-profit: % gain on put premium required to close. Fixed — no breadth switching for puts. Default: ${adv.put_tp_pct}%.`,
    put_sl_pct:          `Put stop-loss: % loss on put premium. Kept tight — under bug-fixed MC, wider PUT_SL becomes catastrophic (gap-through losses). Default: ${adv.put_sl_pct}%.`,
    put_sl_hold_default: `Trading bars to suppress the put SL after entry for Tue–Fri entries. Default: ${adv.put_sl_hold_default} (Phase H1/H5 ship: hold mechanic created gap-through window in bug-fixed MC; with hold=0 the stop is active from bar 1).`,
    put_sl_hold_monday:  `Trading bars to suppress the put SL for Monday entries. Default: ${adv.put_sl_hold_monday}.`,
    hard_sell_day:       `Calendar days after entry when any open position is force-closed regardless of TP/SL. Default: ${adv.hard_sell_day} (~half-life of 30 DTE option to recover remaining time value).`,
    hard_sell_loss_pct:  `Hard-sell P&L on option premium when the position hits the hard-sell day with neither TP nor SL fired. Default: ${adv.hard_sell_loss_pct}%.`,
    breadth_adaptive:    `When ON, TP and SL widen in stressed markets (breadth score ≤ ${adv.breadth_thresh ?? 'BREADTH_THRESHOLD'}): call TP ${adv.tp_pct}%→${adv.tp_stress_pct}%, SL ${adv.sl_pct}%→${adv.sl_stress_pct}%. One breadth signal drives both.`,
    max_positions:       `Maximum concurrent open positions across calls and puts combined. Calls fill first each day by conviction. Default: ${adv.max_positions}.`,
    max_positions_call:  `Maximum concurrent call positions inside the shared pool. Default: ${adv.max_positions_call}.`,
    max_positions_put:   `Maximum concurrent put positions inside the shared pool. Default: ${adv.max_positions_put}.`,
    practical_exposure_enabled: `When ON, allocation uses a practical capital base and open-premium caps before deploying each trade. Default: ${adv.practical_exposure_enabled ? 'ON' : 'OFF'}.`,
    practical_capital_ceiling: `Portfolio value ceiling used as allocation base once equity grows beyond it. Default: ${fmtMoney(adv.practical_capital_ceiling || 0, false)}.`,
    gross_premium_cap:   `Maximum total open premium as a percent of the practical allocation base. Default: ${adv.gross_premium_cap}%.`,
    call_premium_cap:    `Maximum open call premium as a percent of the practical allocation base. Default: ${adv.call_premium_cap}%.`,
    put_premium_cap:     `Maximum open put premium as a percent of the practical allocation base. Default: ${adv.put_premium_cap}%.`,
    opp_sat_call_ref:    `Daily call opportunity count where saturation begins. More crowded call days scale smoothly down from full tier size. Default: ${adv.opp_sat_call_ref}.`,
    opp_sat_put_ref:     `Daily put opportunity count where saturation begins. Default: ${adv.opp_sat_put_ref}.`,
    opp_sat_power:       `Saturation curve power. Lower values make the crowding response gentler. Default: ${Number(adv.opp_sat_power).toFixed(2)}.`,
    opp_sat_floor:       `Minimum opportunity saturation scale. Default: ${Number(adv.opp_sat_floor).toFixed(2)}.`,
    alloc_95plus:        `% of current portfolio value allocated per trade when score ≥ 95. Ultra-high conviction tier (WR15 ≈ 90%). Default: ${adv.alloc_95plus}%.`,
    alloc_85_94:         `% allocation for scores 85–94. Strong conviction — 0.27 signals/day average. Default: ${adv.alloc_85_94}%.`,
    alloc_80_84:         `% allocation for scores 80–84. Solid mid-tier — 0.62 signals/day average. Default: ${adv.alloc_80_84}%.`,
    alloc_75_79:         `% allocation for scores 75–79. High-volume compounding engine — ~1.8 signals/day. Drives the bulk of portfolio returns. Default: ${adv.alloc_75_79}%.`,
    alloc_70_74:         `% of base per 70-74 overflow call. Shipped Apex default ${adv.alloc_70_74}% — collapse-safe, fills the idle book inside the 50% gross cap (≤0.040; cliff at 0.045). Loaded only when the score floor includes 70-74 (automatic when this is > 0 and min score ≤ 75).`,
    alloc_p15:           `% allocation for extreme put signals (score ≤ 15). Default: ${adv.alloc_p15}%.`,
    alloc_p16_20:        `% allocation for put scores 16–20. Mid-conviction puts. Default: ${adv.alloc_p16_20}%.`,
    alloc_p21_25:        `% allocation for put scores 21–25. Weakest qualifying puts. Default: ${adv.alloc_p21_25}%.`,
    breadth_alloc_enabled: `F3f breadth-driven allocation scaling (shipped 2026-04-24). When ON, per-trade allocation is scaled by MarketBreadth.breadth_score directly — calls cut in weak breadth, puts cut in strong breadth. Toggle OFF to revert to legacy regime-slope path. Default: ${adv.breadth_alloc_enabled ? 'ON' : 'OFF'}.`,
    f3f_call_thresh:     `Call alloc cap threshold. When breadth_score is at/above this value, calls deploy at full tier % (no scaling). Default: ${adv.f3f_call_thresh}.`,
    f3f_call_floor:      `Minimum call alloc scale factor at the deepest weak-breadth reading. Default: ${adv.f3f_call_floor.toFixed(2)}.`,
    f3f_call_low:        `Breadth_score at/below which the call alloc scale is at the floor. Default: ${adv.f3f_call_low}.`,
    f3f_put_thresh:      `Put alloc cap threshold. When breadth_score is at/below this value, puts deploy at full tier %. Default: ${adv.f3f_put_thresh}.`,
    f3f_put_floor:       `Minimum put alloc scale factor at the highest healthy-breadth reading. Default: ${adv.f3f_put_floor.toFixed(2)}.`,
    f3f_put_high:        `Breadth_score at/above which the put alloc scale is at the floor. Default: ${adv.f3f_put_high}.`,
    dd_soft_lo:          `H3 DD soft-band start (shipped 2026-05-04, 30 DTE). Below this running portfolio DD, no contraction. Set 0 to disable mechanism entirely. Default: ${adv.dd_soft_lo.toFixed(2)}.`,
    dd_soft_hi:          `H3 DD soft-band end. At/above this running DD, call alloc scaled to the floor. Linear interpolation between LO and HI. Default: ${adv.dd_soft_hi.toFixed(2)}.`,
    dd_soft_floor:       `H3 minimum call alloc multiplier at deep DD (≥ HI). E.g. 0.50 means cut alloc to 50% in deep-DD episodes. Calls only; puts unaffected. Default: ${adv.dd_soft_floor.toFixed(2)}.`,
    rxdd_enabled:        `RXDD VIX-band call dampener (shipped 2026-06-04, 30 DTE). Smoothly contracts CALL alloc in the low-EV VIX slow-bleed band, gated to running DD ≥ DD_MIN. Calls only; puts unaffected. Default: ${adv.rxdd_enabled ? 'on' : 'off'}.`,
    rxdd_vix_c:          `RXDD Gaussian band center (VIX level of deepest call contraction). Default: ${adv.rxdd_vix_c.toFixed(2)}.`,
    rxdd_vix_w:          `RXDD Gaussian band width (VIX points). Wider = contraction spans more of the VIX range. Default: ${adv.rxdd_vix_w.toFixed(2)}.`,
    rxdd_depth:          `RXDD max contraction depth at band center. E.g. 0.45 cuts call alloc to ~55% at peak. Default: ${adv.rxdd_depth.toFixed(2)}.`,
    rxdd_dd_min:         `RXDD running-DD gate. No contraction below this drawdown. Default: ${adv.rxdd_dd_min.toFixed(3)}.`,
    mwdd_enabled:        `MWDD McClellan breadth-momentum flat-band call dampener (shipped 2026-06-05, 30 DTE). Smoothly contracts CALL alloc in the low-EV flat/topping McClellan band (~0), gated to running DD ≥ DD_MIN and VIX-panic-excluded (≥ VIX_PANIC left alone, capitulation = mean-reversion winners). Orthogonal to RXDD(VIX)/F3F(breadth level). Calls only; puts unaffected. Default: ${adv.mwdd_enabled ? 'on' : 'off'}.`,
    mwdd_mcc_c:          `MWDD Gaussian band center (McClellan oscillator level of deepest call contraction). Default: ${adv.mwdd_mcc_c.toFixed(2)}.`,
    mwdd_mcc_w:          `MWDD Gaussian band width (McClellan points). Wider = contraction spans more of the McClellan range. Default: ${adv.mwdd_mcc_w.toFixed(2)}.`,
    mwdd_depth:          `MWDD max contraction depth at band center. E.g. 0.34 cuts call alloc to ~66% at peak. Default: ${adv.mwdd_depth.toFixed(2)}.`,
    mwdd_dd_min:         `MWDD running-DD gate. No contraction below this drawdown. Default: ${adv.mwdd_dd_min.toFixed(3)}.`,
    mwdd_vix_panic:      `MWDD VIX-panic exclusion: no contraction when VIX ≥ this (capitulation calls mean-revert and win — leave alone, keeps COVID untouched). Default: ${adv.mwdd_vix_panic.toFixed(1)}.`,
    tvdd_enabled:        `TVDD TRIN volume-flow neutral-band call dampener (shipped 2026-06-07, 30 DTE). Smoothly contracts CALL alloc in the low-EV neutral volume-flow band (TRIN ~1.0-1.3 = balanced/mild-distribution), gated to running DD ≥ DD_MIN and VIX-panic-excluded. TRIN extremes (froth <0.7, panic >1.8) = mean-reversion/momentum winners, left alone. 4th orthogonal DD lever vs RXDD(VIX)/MWDD(McClellan)/F3F(breadth level) — a volume-flow-vs-breadth-momentum divergence. Calls only; puts unaffected. Default: ${adv.tvdd_enabled ? 'on' : 'off'}.`,
    tvdd_trin_c:         `TVDD Gaussian band center (TRIN level of deepest call contraction). Default: ${adv.tvdd_trin_c.toFixed(3)}.`,
    tvdd_trin_w:         `TVDD Gaussian band width (TRIN units). Wider = contraction spans more of the TRIN range. Default: ${adv.tvdd_trin_w.toFixed(3)}.`,
    tvdd_depth:          `TVDD max contraction depth at band center. E.g. 0.43 cuts call alloc to ~57% at peak. Default: ${adv.tvdd_depth.toFixed(3)}.`,
    tvdd_dd_min:         `TVDD running-DD gate. No contraction below this drawdown. Default: ${adv.tvdd_dd_min.toFixed(3)}.`,
    tvdd_vix_panic:      `TVDD VIX-panic exclusion: no contraction when VIX ≥ this (capitulation calls mean-revert and win — leave alone, keeps COVID untouched). Default: ${adv.tvdd_vix_panic.toFixed(1)}.`,
    bdiv_enabled:        `BDIV pre-top breadth-divergence call dampener (shipped 2026-06-10, 30 DTE). Contracts CALL alloc when SPY is near its 60d high WHILE internal breadth is rolling over — the classic pre-top divergence, the strategy's major-DD onset zone (2021-01/2025-02). The first LEADING DD lever: no DD-gate (fires pre-onset); the SPY-near-highs requirement is the structural crash guard (cannot fire mid-crash — 2022/COVID untouched). Calls only; puts unaffected. Default: ${adv.bdiv_enabled ? 'on' : 'off'}.`,
    bdiv_prox_cut:       `BDIV proximity ramp start: contraction begins when SPY is within this fraction below its 60d high (e.g. 0.02 = 2%). Default: ${adv.bdiv_prox_cut.toFixed(4)}.`,
    bdiv_prox_full:      `BDIV proximity ramp end: full proximity weight when SPY is within this fraction of its 60d high. Default: ${adv.bdiv_prox_full.toFixed(4)}.`,
    bdiv_gap_c:          `BDIV Gaussian band center on the 10d breadth deterioration (points of breadth_score drop). Deeper drops (>12) = sharp shakeouts that mean-revert — left alone. Default: ${adv.bdiv_gap_c.toFixed(2)}.`,
    bdiv_gap_w:          `BDIV Gaussian band width (breadth points). Default: ${adv.bdiv_gap_w.toFixed(2)}.`,
    bdiv_depth:          `BDIV max contraction depth at band center and full proximity. E.g. 0.53 cuts call alloc to ~47% at peak. Default: ${adv.bdiv_depth.toFixed(2)}.`,
    svr_enabled:         `SVR semivol_r skew-bridge entry filter (shipped 2026-06-05, 30 DTE). Contracts CALL alloc toward the floor outside the semivol_r (60d downside/upside vol ratio = live cousin of put-skew) sweet spot — downweights the euphoric/expensive-call and crash-mode cohorts. Calls only; puts unaffected. Default: ${adv.svr_enabled ? 'on' : 'off'}.`,
    svr_lo_cut:          `SVR low-side full-contraction point. At/below this semivol_r, call alloc = floor. Default: ${adv.svr_lo_cut.toFixed(2)}.`,
    svr_lo_full:         `SVR low-side sweet-spot edge. semivol_r between this and HI_FULL gets full alloc; linear ramp down to floor at LO_CUT. Default: ${adv.svr_lo_full.toFixed(2)}.`,
    svr_hi_full:         `SVR high-side sweet-spot edge. semivol_r between LO_FULL and this gets full alloc. Default: ${adv.svr_hi_full.toFixed(2)}.`,
    svr_hi_cut:          `SVR high-side full-contraction point. At/above this semivol_r (crash mode), call alloc = floor. Default: ${adv.svr_hi_cut.toFixed(2)}.`,
    svr_floor:           `SVR minimum call alloc multiplier outside the sweet spot. E.g. 0.50 = cut to 50%. Calls only; puts unaffected. Default: ${adv.svr_floor.toFixed(2)}.`,
    spread_tilt_enabled: `SPREAD_TILT 75-79 component-spread call alloc haircut (shipped 2026-06-15, 30 DTE). Down-weights HIGH-disagreement calls (sqrt pop-variance of the 5 component scores trend/bb/rsi/macd/stoch) in the 75-79 band ONLY — the 80-84 band inverts sign, so the band gate is load-bearing. Stage-3 N=500x9: 5y WorstDD 53.5%→49.4% (-4.1pp), DD down on 8/9 windows, collapse=0 incl COVID. Calls only; puts unaffected. Default: ${adv.spread_tilt_enabled ? 'on' : 'off'}.`,
    spread_tilt_lo:      `SPREAD_TILT low edge: spread at/below this gets full alloc (low disagreement = agreement). p50 of the 75-79 spread distribution. Default: ${adv.spread_tilt_lo.toFixed(1)}.`,
    spread_tilt_hi:      `SPREAD_TILT high edge: spread at/above this gets the full haircut. p75 of the 75-79 spread distribution (high-disagreement tercile boundary). Default: ${adv.spread_tilt_hi.toFixed(1)}.`,
    spread_tilt_depth:   `SPREAD_TILT max contraction at/above HI. E.g. 0.40 cuts call alloc to floor 0.60x on the worst-disagreement cohort. Default: ${adv.spread_tilt_depth.toFixed(2)}.`,
    liquidity_floor:     `LIQUIDITY_FLOOR option-volume admission filter (staged ship-candidate 2026-08-07). Drops 75+ CALL signals whose trailing-30d avg option contract volume is below this floor (contracts/day). 0 = off. Core-evidence-only (Stage B' N=500x12 beat its matched random-drop control); Apex failed T4, stays off. Enable gated on P2.B live-fill confirmation. Calls only; puts unaffected. Default: ${adv.liquidity_floor} c/d.`,
  };
}

function Tooltip({ text }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex items-center ml-1" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      <span className="w-3 h-3 rounded-full bg-white/[0.08] text-gray-600 text-[8px] font-bold flex items-center justify-center cursor-default leading-none select-none">?</span>
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-56 bg-trading-dark-800 border border-white/[0.10] rounded px-2.5 py-2 text-[10px] text-gray-300 leading-relaxed shadow-xl z-50 pointer-events-none">
          {text}
          <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-white/[0.10]" />
        </span>
      )}
    </span>
  );
}

// Manifest-driven advanced panel: renders every portfolio knob from
// /api/strategy/param-manifest, grouped. Editable knobs bind to `adv`; the rest
// are read-only ("cfg" badge) showing the selected profile's value — reproducible
// but applied from config. A new manifest knob auto-appears here.
function ManifestAdvancedPanel({ adv, setAdv, manifest, profileDefaults, profileKey }) {
  const [sharedOpen, setSharedOpen] = useState(false);   // the 52 common-across-profiles knobs — collapsed by default
  const inputCls = 'bg-trading-dark-800 border border-white/[0.08] rounded text-[11px] text-gray-200 px-2 py-1 focus:outline-none focus:border-trading-green-400/50 font-mono w-full';
  const roCls = 'bg-trading-dark-900 border border-white/[0.05] rounded text-[11px] text-gray-500 px-2 py-1 font-mono w-full cursor-not-allowed';
  const labelCls = 'text-[9px] font-medium uppercase tracking-wider text-gray-600 mb-0.5 flex items-center gap-1';

  if (!manifest || !manifest.length) {
    return <div className="mt-3 pt-3 border-t border-white/[0.06] text-[11px] text-gray-500">Loading strategy parameters…</div>;
  }
  const pd = (profileDefaults && profileDefaults[profileKey]) || {};

  const setVal = (key, kind) => (e) => {
    let raw;
    if (kind === 'choice') raw = e.target.value;
    else if (e.target.value === '') raw = '';
    else { const f = parseFloat(e.target.value); raw = Number.isNaN(f) ? e.target.value : f; }
    setAdv(a => ({ ...a, [key]: raw }));
  };

  const renderField = (p) => {
    if (!p.editable) {
      let v = pd[p.key];
      if (v === undefined || v === null) v = p.default;
      const disp = (typeof v === 'boolean') ? (v ? 'on' : 'off') : (v === undefined || v === null ? '' : v);
      return (
        <div key={p.key} title={`${p.tip || p.label}\n(applies from config — reproducible, per-run editing coming)`}>
          <label className={labelCls + ' opacity-50'}>
            <span className="truncate">{p.label}{p.unit ? ` ${p.unit}` : ''}</span>
            <span className="text-[7px] text-gray-700 border border-gray-700/60 rounded px-0.5 leading-none">cfg</span>
          </label>
          <input readOnly value={disp} className={roCls} />
        </div>
      );
    }
    const cur = adv[p.key];
    const v = (cur === undefined || cur === null) ? p.default : cur;
    if (p.kind === 'bool') {
      return (
        <div key={p.key} className="flex flex-col">
          <label className={labelCls}><span className="truncate">{p.label}</span>{p.tip && <Tooltip text={p.tip} />}</label>
          <div
            onClick={() => setAdv(a => ({ ...a, [p.key]: !((a[p.key] === undefined ? p.default : a[p.key])) }))}
            className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer mt-0.5 ${v ? 'bg-trading-green-400' : 'bg-white/[0.12]'}`}>
            <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${v ? 'translate-x-4' : 'translate-x-0.5'}`} />
          </div>
        </div>
      );
    }
    if (p.kind === 'choice') {
      return (
        <div key={p.key}>
          <label className={labelCls}><span className="truncate">{p.label}</span>{p.tip && <Tooltip text={p.tip} />}</label>
          <select value={v} onChange={setVal(p.key, 'choice')} className={inputCls}>
            {(p.choices || []).map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      );
    }
    return (
      <div key={p.key}>
        <label className={labelCls}>
          <span className="truncate">{p.label}{p.unit ? ` ${p.unit}` : ''}</span>{p.tip && <Tooltip text={p.tip} />}
        </label>
        <input type="number" min={p.min ?? undefined} max={p.max ?? undefined} step={p.step || 1}
          value={v} onChange={setVal(p.key, p.kind)} className={inputCls} />
      </div>
    );
  };

  const renderGroup = (grp) => (
    <div key={grp.group}>
      <p className="text-[9px] font-medium uppercase tracking-wider text-gray-700 mb-1.5">{grp.group}</p>
      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-8 gap-2">
        {grp.params.map(renderField)}
      </div>
    </div>
  );

  // Split each group's params into profile-specific (visible) vs shared-mechanism
  // (the 52 knobs identical across Apex/Core/Sentinel — CTSL, SAW, DTE router,
  // F3F, dead-hold, regime, slippage). The shared set goes in a collapsed
  // sub-panel so the page isn't a wall of 91 fields.
  const primaryGroups = [];
  const sharedGroups = [];
  let nShared = 0;
  for (const grp of manifest) {
    const prim = grp.params.filter(p => !p.shared);
    const shar = grp.params.filter(p => p.shared);
    if (prim.length) primaryGroups.push({ group: grp.group, params: prim });
    if (shar.length) { sharedGroups.push({ group: grp.group, params: shar }); nShared += shar.length; }
  }

  const nTotal = manifest.reduce((n, g) => n + g.params.length, 0);
  const nEditable = manifest.reduce((n, g) => n + g.params.filter(p => p.editable).length, 0);
  return (
    <div className="mt-3 pt-3 border-t border-white/[0.06] space-y-3">
      <p className="text-[9px] text-gray-600">
        Full <span className="uppercase">{profileKey}</span> strategy — {nTotal} knobs ({nEditable} editable,{' '}
        {nTotal - nEditable} <span className="text-gray-500 border border-gray-700/60 rounded px-0.5">cfg</span>-applied / reproducible).
        Editing a knob overrides it for the next run; profile toggle resets to that profile.
      </p>
      {primaryGroups.map(renderGroup)}

      {sharedGroups.length > 0 && (
        <div className="rounded-md bg-trading-dark-900/60 border border-white/[0.05]">
          <button
            type="button"
            onClick={() => setSharedOpen(o => !o)}
            className="w-full flex items-center gap-2 px-2.5 py-2 hover:bg-white/[0.03] transition-colors text-left"
          >
            {sharedOpen
              ? <ChevronUp className="w-3 h-3 text-gray-500 flex-shrink-0" />
              : <ChevronDown className="w-3 h-3 text-gray-500 flex-shrink-0" />}
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-500">
              Shared mechanism parameters
            </span>
            <span className="text-[9px] text-gray-700 truncate">
              {nShared} knobs · identical across Apex / Core / Sentinel (CTSL · SAW · DTE router · F3F · dead-hold · regime · slippage)
            </span>
          </button>
          {sharedOpen && (
            <div className="px-2.5 pb-3 pt-1 space-y-3 border-t border-white/[0.04]">
              {sharedGroups.map(renderGroup)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AdvancedPanel({ adv, setAdv, defaults }) {
  const inputCls = 'bg-trading-dark-800 border border-white/[0.08] rounded text-[11px] text-gray-200 px-2 py-1 focus:outline-none focus:border-trading-green-400/50 font-mono w-full';
  const labelCls = 'text-[9px] font-medium uppercase tracking-wider text-gray-600 mb-0.5 flex items-center';

  // Tooltip strings template against current adv state — "Default: X%"
  // reflects what's actually in the form field, not a frozen literal.
  const tips = useMemo(() => getFieldTips(adv), [adv]);

  const set = (key) => (e) =>
    setAdv(a => ({ ...a, [key]: e.target.type === 'checkbox' ? e.target.checked : parseFloat(e.target.value) || e.target.value }));

  const numField = (key, min, max, step = 1, label) => (
    <div key={key}>
      <label className={labelCls}>
        {label}
        {tips[key] && <Tooltip text={tips[key]} />}
      </label>
      <input type="number" min={min} max={max} step={step}
        value={adv[key]} onChange={set(key)} className={inputCls} />
    </div>
  );

  return (
    <div className="mt-3 pt-3 border-t border-white/[0.06] space-y-3">
      {/* Exit parameters */}
      <div>
        <p className="text-[9px] font-medium uppercase tracking-wider text-gray-700 mb-2">Exit Parameters</p>
        <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-2">
          {numField('tp_pct',              10, 100, 1, 'Call TP % (base)')}
          {numField('tp_stress_pct',       10, 100, 1, 'Call TP % (stress)')}
          {numField('sl_pct',              5,  100, 1, 'Call SL % (base)')}
          {numField('sl_stress_pct',       5,  100, 1, 'Call SL % (stress)')}
          {numField('put_tp_pct',          10, 100, 1, 'Put TP %')}
          {numField('put_sl_pct',          5,  100, 1, 'Put SL %')}
          {numField('put_sl_hold_default', 0,  15,  1, 'SL hold Tue–Fri')}
          {numField('put_sl_hold_monday',  0,  15,  1, 'SL hold Mon')}
          {numField('hard_sell_day',       5,  60,  1, 'Hard sell day')}
        </div>
        <div className="mt-2 flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-600 flex items-center">
              Breadth-adaptive TP/SL
              <Tooltip text={tips.breadth_adaptive} />
            </span>
            <div
              onClick={() => setAdv(a => ({ ...a, breadth_adaptive: !a.breadth_adaptive }))}
              className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer flex-shrink-0 ${
                adv.breadth_adaptive ? 'bg-trading-green-400' : 'bg-white/[0.12]'
              }`}
            >
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                adv.breadth_adaptive ? 'translate-x-4' : 'translate-x-0.5'
              }`} />
            </div>
          </label>
        </div>
      </div>

      {/* Position sizing */}
      <div>
        <p className="text-[9px] font-medium uppercase tracking-wider text-gray-700 mb-2">Position Sizing</p>
        <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-2">
          {numField('max_positions', 1,  40,  1, 'Max pos')}
          {numField('max_positions_call', 0, 40, 1, 'Max calls')}
          {numField('max_positions_put',  0, 40, 1, 'Max puts')}
          {numField('alloc_95plus',  0,  100, 1, '95+ %')}
          {numField('alloc_85_94',   0,  100, 1, '85-94 %')}
          {numField('alloc_80_84',   0,  100, 1, '80-84 %')}
          {numField('alloc_75_79',   0,  100, 1, '75-79 %')}
          {numField('alloc_70_74',   0,  100, 1, '70-74 %')}
          {numField('alloc_p15',     0,  100, 1, 'P ≤15 %')}
          {numField('alloc_p16_20',  0,  100, 1, 'P 16-20 %')}
          {numField('alloc_p21_25',  0,  100, 1, 'P 21-25 %')}
        </div>
      </div>

      {/* Practical exposure saturation (Sentinel portfolio profile) */}
      <div>
        <p className="text-[9px] font-medium uppercase tracking-wider text-gray-700 mb-1">
          Practical Exposure
        </p>
        <div className="mb-2 flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-600 flex items-center">
              Practical cap
              <Tooltip text={tips.practical_exposure_enabled} />
            </span>
            <div
              onClick={() => setAdv(a => ({ ...a, practical_exposure_enabled: !a.practical_exposure_enabled }))}
              className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer flex-shrink-0 ${
                adv.practical_exposure_enabled ? 'bg-trading-green-400' : 'bg-white/[0.12]'
              }`}
            >
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                adv.practical_exposure_enabled ? 'translate-x-4' : 'translate-x-0.5'
              }`} />
            </div>
          </label>
        </div>
        <div className={`grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-2 ${
            adv.practical_exposure_enabled ? '' : 'opacity-40 pointer-events-none'
          }`}>
          {numField('practical_capital_ceiling', 0, 1000000000, 1000000, 'Base cap $')}
          {numField('gross_premium_cap', 0, 100, 1, 'Gross cap %')}
          {numField('call_premium_cap', 0, 100, 1, 'Call cap %')}
          {numField('put_premium_cap', 0, 100, 1, 'Put cap %')}
          {numField('opp_sat_call_ref', 0, 200, 1, 'Call ref')}
          {numField('opp_sat_put_ref', 0, 200, 1, 'Put ref')}
          {numField('opp_sat_power', 0, 3, 0.05, 'Sat power')}
          {numField('opp_sat_floor', 0, 1, 0.05, 'Sat floor')}
        </div>
      </div>

      {/* F3f breadth-driven allocation knob (shipped 2026-04-24) */}
      <div>
        <p className="text-[9px] font-medium uppercase tracking-wider text-gray-700 mb-1">
          Breadth-Driven Alloc Scaling (F3f)
        </p>
        <p className="text-[10px] text-gray-600 leading-snug mb-2 max-w-3xl">
          One extra multiplier on every per-trade allocation, anchored on{' '}
          <span className="font-mono text-gray-500">MarketBreadth.breadth_score</span> at the signal date.
          Calls deploy at full tier % when breadth ≥ <span className="font-mono">{adv.f3f_call_thresh}</span>{' '}
          and linearly contract to <span className="font-mono">{adv.f3f_call_floor.toFixed(2)}×</span> at breadth ≤{' '}
          <span className="font-mono">{adv.f3f_call_low}</span>. Puts mirror on the other side: full size when
          breadth ≤ <span className="font-mono">{adv.f3f_put_thresh}</span>, contracting to{' '}
          <span className="font-mono">{adv.f3f_put_floor.toFixed(2)}×</span> at breadth ≥{' '}
          <span className="font-mono">{adv.f3f_put_high}</span>. Cost basis becomes{' '}
          <span className="font-mono text-gray-500">tier_alloc × portfolio × scale</span>, clamped
          [0.25, 1.75]. Toggle OFF to use the legacy <span className="font-mono">regime_multiplier</span> slope.
        </p>
        <div className="mb-2 flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-600 flex items-center">
              F3f knob
              <Tooltip text={tips.breadth_alloc_enabled} />
            </span>
            <div
              onClick={() => setAdv(a => ({ ...a, breadth_alloc_enabled: !a.breadth_alloc_enabled }))}
              className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer flex-shrink-0 ${
                adv.breadth_alloc_enabled ? 'bg-trading-green-400' : 'bg-white/[0.12]'
              }`}
            >
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                adv.breadth_alloc_enabled ? 'translate-x-4' : 'translate-x-0.5'
              }`} />
            </div>
          </label>
        </div>
        <div className={`grid grid-cols-3 sm:grid-cols-6 gap-2 ${
            adv.breadth_alloc_enabled ? '' : 'opacity-40 pointer-events-none'
          }`}>
          {numField('f3f_call_thresh', 0,   100, 1,    'Call thresh')}
          {numField('f3f_call_floor',  0.1, 1.0, 0.05, 'Call floor')}
          {numField('f3f_call_low',    0,   100, 1,    'Call low')}
          {numField('f3f_put_thresh',  0,   100, 1,    'Put thresh')}
          {numField('f3f_put_floor',   0.1, 1.0, 0.05, 'Put floor')}
          {numField('f3f_put_high',    0,   100, 1,    'Put high')}
        </div>
      </div>

      {/* Reset button */}
      <div className="flex justify-end">
        <button
          onClick={() => setAdv(defaults)}
          className="text-[10px] font-mono text-gray-600 hover:text-gray-400 border border-white/[0.06] hover:border-white/[0.12] rounded px-2 py-1 transition-colors"
        >
          Reset to optimal
        </button>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function Backtest() {
  const [params, setParams] = useState({
    from:          startOfYearStr(),
    to:            todayStr(),
    capital:       50000,
    min_score:     75,
    max_put_score: 25,
    calls_only:    true,   // default profile (Core) is puts-off; toggle/effect keep this synced

    flagged_only:  false,
    currency:      'CAD',
    dte:           '30',  // Phase 16 (2026-04-28): '30' or '15'
  });
  const [adv,        setAdv]        = useState(DEFAULT_ADVANCED);
  // Defaults derived from /api/strategy/config — held separately from `adv`
  // so the "Reset to defaults" button always restores the live shipped values,
  // not whatever the user happened to land on. Initialized to the fallback
  // literal; replaced when the fetch resolves.
  const [defaults,   setDefaults]   = useState(DEFAULT_ADVANCED);
  const [showAdv,    setShowAdv]    = useState(false);
  const [data,       setData]       = useState(null);
  // Schema-driven portfolio-knob manifest (all 91 knobs + per-profile defaults +
  // editable flags) from /api/strategy/param-manifest. Drives the AdvancedPanel
  // render and the run payload so a newly-surfaced knob auto-appears + auto-sends.
  const [paramManifest, setParamManifest] = useState(null);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState(null);
  const [phase,      setPhase]      = useState(PUNCHLINE_LOADING_PHASES[0]);
  const [cadPerUsd,  setCadPerUsd]  = useState(DEFAULT_CAD_PER_USD);
  // Bumped after each successful backtest run so SavedRunsPanel re-fetches
  // the list (the new run was just auto-saved server-side).
  const [savedRefreshKey, setSavedRefreshKey] = useState(0);
  // Score-version selector metadata is shared with Dashboard/Historic.
  const [activeVersion, setActiveVersion] = useState(null);
  const [availableScoreVersions, setAvailableScoreVersions] = useState([]);
  const [legacyScoreVersions, setLegacyScoreVersions] = useState([]);
  const [selectedScoreVersionId, setSelectedScoreVersionId] = useState(null);
  const [portfolioProfiles, setPortfolioProfiles] = useState(DEFAULT_PORTFOLIO_PROFILES);
  const [portfolioProfile, setPortfolioProfile] = useState(() => {
    if (typeof window === 'undefined') return 'core';
    const stored = window.localStorage?.getItem(PORTFOLIO_PROFILE_STORAGE_KEY);
    return ['sentinel', 'core', 'apex'].includes(stored) ? stored : 'core';
  });
  const timerRef = useRef(null);
  // Raw (un-profiled) shipped base config from /api/strategy/config. Lets the
  // profile toggle snap the advanced params synchronously (set-and-run) without
  // waiting on a re-fetch. Populated by the strategy-config effect below.
  const rawDefaultsRef = useRef(DEFAULT_ADVANCED);

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/score/versions`)
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!alive || !j) return;
        setActiveVersion(j.version || null);
        setAvailableScoreVersions(Array.isArray(j.available_versions) ? j.available_versions : []);
        setLegacyScoreVersions(Array.isArray(j.legacy_versions) ? j.legacy_versions : []);
      })
      .catch(() => { /* SavedRunsPanel will still surface the active version. */ });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/portfolio/profiles/compare?_t=${Math.floor(Date.now() / 30000)}`)
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!alive || !Array.isArray(j?.rows)) return;
        setPortfolioProfiles(normalizeProfiles(j.rows));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const selectedScoreVersion = useMemo(() => {
    const all = [...availableScoreVersions, ...legacyScoreVersions];
    if (selectedScoreVersionId != null) {
      return all.find(v => v.id === selectedScoreVersionId) || null;
    }
    return activeVersion;
  }, [availableScoreVersions, legacyScoreVersions, selectedScoreVersionId, activeVersion]);

  const selectedPortfolioProfile = useMemo(() => {
    return normalizeProfiles(portfolioProfiles).find(profile => profile.key === portfolioProfile) || DEFAULT_PORTFOLIO_PROFILES[0];
  }, [portfolioProfiles, portfolioProfile]);

  const setPortfolioProfilePersist = useCallback((key, clear = true) => {
    setPortfolioProfile(key);
    try { window.localStorage?.setItem(PORTFOLIO_PROFILE_STORAGE_KEY, key); } catch (e) { /* ignore */ }
    if (clear) {
      // User toggle: snap the advanced params to this profile NOW (set-and-run),
      // from the raw shipped base, instead of waiting on the async strategy-config
      // effect. Skipped on programmatic calls (clear=false, e.g. loading a saved
      // run) so that run's own saved params are preserved.
      const profile = normalizeProfiles(portfolioProfiles).find((p) => p.key === key);
      if (profile) {
        const profiled = applyPortfolioProfileToAdv(rawDefaultsRef.current, profile);
        setDefaults(profiled);
        setAdv(profiled);
        // Profile also drives the main-form signal params (Calls Only + min call score).
        setParams(p => ({ ...p, ...signalParamsFromAdv(profiled) }));
      }
      setData(null);
      setError(null);
    }
  }, [portfolioProfiles]);

  const setBacktestScoreVersion = useCallback((versionId) => {
    const numeric = versionId == null || versionId === '' ? null : Number(versionId);
    setSelectedScoreVersionId(Number.isFinite(numeric) ? numeric : null);
    setData(null);
    setError(null);
  }, []);

  const setFallbackActiveVersion = useCallback((version) => {
    setActiveVersion(prev => prev || version);
  }, []);

  // Fetch shipped strategy defaults on mount, and re-fetch on DTE change.
  // strategy_config.py is the single source of truth (replaces the hard-coded
  // DEFAULT_ADVANCED for actual values; the literal stays as fallback so the
  // form is functional pre-fetch / on offline).
  useEffect(() => {
    const which = params.dte === '15' ? '15dte' : '30dte';
    fetch(`/api/strategy/config?strategy=${which}`)
      .then(r => r.ok ? r.json() : null)
      .then(j => {
        if (!j || !j[which]) return;
        const fetched = defaultsFromCfg(j[which]);
        rawDefaultsRef.current = fetched;
        const profiled = applyPortfolioProfileToAdv(fetched, selectedPortfolioProfile);
        setDefaults(profiled);
        setAdv(profiled);
        // Sync the main-form signal params to the active profile (Calls Only +
        // min call score), plus the put threshold from the shipped config.
        setParams(p => ({
          ...p,
          ...signalParamsFromAdv(profiled),
          ...(j[which].PUT_THRESHOLD != null ? { max_put_score: j[which].PUT_THRESHOLD } : {}),
        }));
      })
      .catch(() => { /* keep DEFAULT_ADVANCED fallback on fetch failure */ });
  }, [params.dte, selectedPortfolioProfile]);

  // Schema-driven knob manifest: all portfolio params + per-profile defaults +
  // editable flags. Re-fetched on DTE change. Structure is identical across
  // profiles; only `profile_defaults` differs (the panel snaps on profile toggle).
  useEffect(() => {
    let alive = true;
    const dteq = params.dte === '15' ? '15' : '30';
    fetch(`${API_BASE}/api/strategy/param-manifest?dte=${dteq}`)
      .then(r => r.ok ? r.json() : null)
      .then(j => { if (alive && j && Array.isArray(j.manifest)) setParamManifest(j); })
      .catch(() => { /* AdvancedPanel shows a loading note if absent */ });
    return () => { alive = false; };
  }, [params.dte]);

  const inCad = params.currency === 'CAD';
  const usdCapital = inCad ? Math.round(params.capital / cadPerUsd) : params.capital;

  const runBacktest = useCallback(async () => {
    setLoading(true);
    setError(null);
    setData(null);

    const shuffled = shuffledPunchlineLoadingPhases();
    let pi = 0;
    setPhase(shuffled[0]);
    timerRef.current = setInterval(() => {
      pi = (pi + 1) % shuffled.length;
      setPhase(shuffled[pi]);
    }, 3000);

    try {
      const qs = new URLSearchParams({
        from:       params.from,
        to:         params.to,
        capital:    usdCapital,
        min_score:  params.min_score,
        profile:    portfolioProfile,
        calls_only:   params.calls_only   ? 'true' : 'false',
        flagged_only: params.flagged_only ? 'true' : 'false',
      });
      // Advanced params — driven by the manifest's editable set so a newly-wired
      // editable knob auto-sends. Falls back to the known editable keys if the
      // manifest hasn't loaded (the endpoint then uses the profile/config defaults).
      const _editableKeys = (paramManifest?.manifest || [])
        .flatMap(g => g.params).filter(p => p.editable).map(p => p.key);
      const _fallbackKeys = [
        'tp_pct','tp_stress_pct','sl_pct','sl_stress_pct','put_tp_pct','put_sl_pct',
        'put_sl_hold_default','put_sl_hold_monday','breadth_adaptive','hard_sell_day','hard_sell_loss',
        'max_positions','max_positions_call','max_positions_put','practical_exposure_enabled',
        'practical_capital_ceiling','gross_premium_cap','call_premium_cap','put_premium_cap',
        'opp_sat_call_ref','opp_sat_put_ref','opp_sat_power','opp_sat_floor',
        'alloc_95plus','alloc_85_94','alloc_80_84','alloc_75_79','alloc_70_74',
        'alloc_p15','alloc_p16_20','alloc_p21_25','dd_soft_lo','dd_soft_hi','dd_soft_floor',
        'rxdd_enabled','rxdd_vix_c','rxdd_vix_w','rxdd_depth','rxdd_dd_min',
        'mwdd_enabled','mwdd_mcc_c','mwdd_mcc_w','mwdd_depth','mwdd_dd_min','mwdd_vix_panic',
        'tvdd_enabled','tvdd_trin_c','tvdd_trin_w','tvdd_depth','tvdd_dd_min','tvdd_vix_panic',
        'bdiv_enabled','bdiv_prox_cut','bdiv_prox_full','bdiv_gap_c','bdiv_gap_w','bdiv_depth',
        'svr_enabled','svr_lo_cut','svr_lo_full','svr_hi_full','svr_hi_cut','svr_floor',
        'spread_tilt_enabled','spread_tilt_lo','spread_tilt_hi','spread_tilt_depth',
      ];
      const _sendKeys = _editableKeys.length ? _editableKeys : _fallbackKeys;
      for (const k of _sendKeys) {
        let v = adv[k];
        if (v === undefined || v === null || v === '') continue;
        if (typeof v === 'boolean') v = v ? 'true' : 'false';
        qs.set(k, String(v));
      }
      if (!params.calls_only) {
        qs.set('max_put_score', params.max_put_score);
      }
      if (params.dte === '15') {
        qs.set('dte', '15');
      }
      if (selectedScoreVersionId) {
        qs.set('version', selectedScoreVersionId);
      }
      const res  = await fetch(`${API_BASE}/api/backtest/run?${qs}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
      setData(json);
      if (json.available_versions) {
        setAvailableScoreVersions(json.available_versions);
      }
      if (json.legacy_versions) {
        setLegacyScoreVersions(json.legacy_versions);
      }
      if (json.params?.cad_usd_rate) {
        setCadPerUsd(parseFloat((1 / json.params.cad_usd_rate).toFixed(4)));
      }
      // Tell SavedRunsPanel to re-fetch — the new run was auto-saved server-side.
      setSavedRefreshKey(k => k + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      clearInterval(timerRef.current);
      setLoading(false);
    }
  }, [params, usdCapital, adv, selectedScoreVersionId, portfolioProfile]);

  // Load a previously-saved run from /api/backtest/runs/<id>. The saved
  // payload has the same shape as a live /api/backtest/run response, so we
  // just slot it into `data`.
  const handleLoadSavedRun = useCallback((json) => {
    setData(json);
    setError(null);
    if (json.version?.id) {
      setSelectedScoreVersionId(
        activeVersion?.id && json.version.id === activeVersion.id
          ? null
          : Number(json.version.id)
      );
    }
    if (json.params?.cad_usd_rate) {
      setCadPerUsd(parseFloat((1 / json.params.cad_usd_rate).toFixed(4)));
    }
    if (json.params?.portfolio_profile) {
      setPortfolioProfilePersist(json.params.portfolio_profile, false);
    }
  }, [activeVersion, setPortfolioProfilePersist]);

  const set = (key) => (e) =>
    setParams(p => ({ ...p, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  const inputCls = 'bg-trading-dark-900 border border-white/[0.08] rounded text-[12px] text-gray-200 px-2 py-1.5 focus:outline-none focus:border-trading-green-400/50 font-mono w-full';
  const labelCls = 'text-[10px] font-medium uppercase tracking-wider text-gray-600 mb-1 block';

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1200px] mx-auto px-4 py-4 space-y-4">

        {/* Page header */}
        <div className="flex items-center border-b border-white/[0.06] pb-3">
          <div className="flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-trading-green-300 flex-shrink-0" />
            <h2 className="text-[13px] font-semibold text-gray-200">Backtest</h2>
            <span className="text-[11px] text-gray-600 ml-1">
              Cascade allocation strategy · ATM options
            </span>
          </div>

          {/* DTE strategy toggle — floats centered between title and version */}
          <div
            className="mx-auto flex items-center rounded overflow-hidden border border-white/[0.08] text-[11px] font-medium leading-none"
            title="Strategy: 30 DTE primary / 15 DTE research"
          >
            {[{key: '30', label: '30 DTE Primary'}, {key: '15', label: '15 DTE Research'}].map(({key, label}) => (
              <button
                key={key}
                onClick={() => setParams(p => ({ ...p, dte: key }))}
                className={`px-3 py-1 transition-colors ${
                  params.dte === key
                    ? 'bg-trading-blue-400/20 text-trading-blue-300'
                    : 'text-gray-600 hover:text-gray-400'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <PortfolioProfileToggle
            profiles={portfolioProfiles}
            value={portfolioProfile}
            onChange={setPortfolioProfilePersist}
            compact
          />

          <ScoreVersionSelector
            versions={availableScoreVersions}
            legacyVersions={legacyScoreVersions}
            currentVersion={selectedScoreVersion}
            activeVersionId={activeVersion?.id}
            selectedVersionId={selectedScoreVersionId}
            onSelect={setBacktestScoreVersion}
            ariaLabel="Select backtest score version"
            titlePrefix="Backtest scores"
          />
        </div>

        {/* Params form */}
        <div className="rounded-md bg-trading-dark-900 border border-white/[0.06] px-3 py-3">
          {/* Basic params row */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3 items-end">

            <div>
              <label className={labelCls}>From</label>
              <SegmentedDateInput value={params.from} onChange={set('from')} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>To</label>
              <SegmentedDateInput value={params.to} onChange={set('to')} className={inputCls} />
            </div>

            {/* Capital */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className={labelCls + ' mb-0'}>Capital</label>
                <div className="flex items-center rounded overflow-hidden border border-white/[0.08] text-[9px] font-medium leading-none">
                  {['CAD', 'USD'].map(cur => (
                    <button
                      key={cur}
                      onClick={() => setParams(p => ({ ...p, currency: cur }))}
                      className={`px-1.5 py-0.5 transition-colors ${
                        params.currency === cur
                          ? 'bg-trading-green-400/20 text-trading-green-300'
                          : 'text-gray-600 hover:text-gray-400'
                      }`}
                    >
                      {cur}
                    </button>
                  ))}
                </div>
              </div>
              <div className={inputCls + ' flex items-center'}>
                <span className="text-gray-500 select-none">{inCad ? 'C$' : '$'}</span>
                <input
                  type="number" min="1" step="1"
                  value={params.capital ? params.capital / 1000 : ''}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10);
                    setParams(p => ({ ...p, capital: isNaN(v) ? 0 : v * 1000 }));
                  }}
                  onFocus={(e) => e.target.select()}
                  className="bg-transparent text-gray-200 outline-none text-right p-0 m-0 border-0 flex-1 min-w-0 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
                <span className="text-gray-500 select-none">,000</span>
              </div>
            </div>

            <div>
              <label className={labelCls}>Min Call Score</label>
              <input type="number" min="50" max="99" step="1"
                value={params.min_score} onChange={set('min_score')} className={inputCls} />
            </div>

            {!params.calls_only ? (
              <div>
                <label className={labelCls}>Max Put Score</label>
                <input type="number" min="1" max="49" step="1"
                  value={params.max_put_score} onChange={set('max_put_score')} className={inputCls} />
              </div>
            ) : (
              <div />
            )}

            <div className="flex flex-col justify-end pb-0.5 gap-2">
              {[
                { key: 'calls_only',   label: 'Calls Only'   },
                { key: 'flagged_only', label: 'Flagged Only' },
              ].map(({ key, label }) => (
                <label key={key} className="flex items-center justify-between gap-2 cursor-pointer select-none w-full">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-gray-600">{label}</span>
                  <div
                    onClick={() => setParams(p => ({ ...p, [key]: !p[key] }))}
                    className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer flex-shrink-0 ${
                      params[key] ? 'bg-trading-green-400' : 'bg-white/[0.12]'
                    }`}
                  >
                    <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                      params[key] ? 'translate-x-4' : 'translate-x-0.5'
                    }`} />
                  </div>
                </label>
              ))}
            </div>

            <div className="flex items-end">
              <button
                onClick={runBacktest}
                disabled={loading}
                className={`w-full flex items-center justify-center gap-2 px-3 py-1.5 rounded text-[12px] font-medium transition-colors ${
                  loading
                    ? 'bg-trading-green-400/10 text-trading-green-400/40 cursor-not-allowed'
                    : 'bg-trading-green-400/15 text-trading-green-300 hover:bg-trading-green-400/25 border border-trading-green-400/20'
                }`}
              >
                {loading
                  ? <><Activity className="w-3.5 h-3.5 animate-pulse" /> Running…</>
                  : <><Play className="w-3.5 h-3.5" /> Run</>
                }
              </button>
            </div>
          </div>

          {/* Advanced accordion toggle */}
          <button
            onClick={() => setShowAdv(s => !s)}
            className="mt-3 flex items-center gap-1.5 text-[10px] font-medium text-gray-600 hover:text-gray-400 transition-colors"
          >
            {showAdv ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            Advanced parameters
          </button>

          {showAdv && <ManifestAdvancedPanel adv={adv} setAdv={setAdv}
            manifest={paramManifest?.manifest}
            profileDefaults={paramManifest?.profile_defaults}
            profileKey={portfolioProfile} />}

          {/* Strategy reminder (collapsed summary) */}
          {!showAdv && (
            <div className="mt-2.5 pt-2.5 border-t border-white/[0.04] flex flex-wrap gap-x-4 gap-y-1">
              {[
                `TP +${adv.tp_pct}/${adv.tp_stress_pct}% · SL −${adv.sl_pct}/${adv.sl_stress_pct}%${adv.breadth_adaptive ? ' (breadth-adaptive)' : ' (fixed)'}`,
                `Hard sell day ${adv.hard_sell_day}`,
                `Max ${adv.max_positions} positions · C/P ${adv.max_positions_call}/${adv.max_positions_put}`,
                `95+→${adv.alloc_95plus}% · 85-94/80-84/75-79→${adv.alloc_85_94}/${adv.alloc_80_84}/${adv.alloc_75_79}%`,
                `Puts ≤15→${adv.alloc_p15}% · 16-20→${adv.alloc_p16_20}% · 21-25→${adv.alloc_p21_25}%`,
                adv.practical_exposure_enabled
                  ? `PX cap ${fmtMoney(adv.practical_capital_ceiling || 0, false)} · gross/call/put ${adv.gross_premium_cap}/${adv.call_premium_cap}/${adv.put_premium_cap}% · refs ${adv.opp_sat_call_ref}/${adv.opp_sat_put_ref}`
                  : `PX cap OFF`,
                adv.breadth_alloc_enabled
                  ? `F3f alloc scale: calls ${adv.f3f_call_floor.toFixed(2)}–1.0 (brd ${adv.f3f_call_low}-${adv.f3f_call_thresh}) · puts ${adv.f3f_put_floor.toFixed(2)}–1.0 (brd ${adv.f3f_put_thresh}-${adv.f3f_put_high})`
                  : `F3f alloc scale OFF — legacy regime path`,
              ].map(s => (
                <span key={s} className="text-[9px] text-gray-700 font-mono">{s}</span>
              ))}
            </div>
          )}
        </div>

        {/* Saved runs (auto-saved on every successful backtest) */}
        <SavedRunsPanel
          dte={params.dte}
          versionId={selectedScoreVersionId}
          currentRunId={data?.run_id}
          onLoad={handleLoadSavedRun}
          refreshKey={savedRefreshKey}
          inCad={inCad}
          cadPerUsd={cadPerUsd}
          onActiveVersion={selectedScoreVersionId ? null : setFallbackActiveVersion}
        />

        {/* Loading */}
        {loading && <LoadingPanel phase={phase} />}

        {/* Error */}
        {!loading && error && (
          <div className="rounded-md bg-trading-red-400/5 border border-trading-red-400/20 px-4 py-3">
            <p className="text-[12px] text-trading-red-400 font-medium">{error}</p>
          </div>
        )}

        {/* Results */}
        {!loading && data && <Results data={data} inCad={inCad} cadPerUsd={cadPerUsd} />}

        {/* Empty state */}
        {!loading && !data && !error && (
          <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
            <Clock className="w-8 h-8 text-gray-700" />
            <p className="text-[13px] text-gray-600">Configure parameters above and click Run to start the backtest.</p>
          </div>
        )}

      </div>
    </div>
  );
}
