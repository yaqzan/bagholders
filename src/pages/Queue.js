import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, CircleDot, Cpu, Database, Gauge, HardDrive, Layers, RefreshCw,
} from 'lucide-react';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';
const POLL_MS = 3000;

// --- semantic color tokens (match the rest of the app) ----------------------
const PRIORITY_STYLE = {
  critical: { rgb: [201, 132, 132], cls: 'text-trading-red-300 bg-trading-red-500/[0.15] border-trading-red-400/45' },
  high:     { rgb: [198, 156, 86],  cls: 'text-amber-300 bg-amber-500/[0.12] border-amber-400/40' },
  scheduled:{ rgb: [125, 164, 196], cls: 'text-trading-blue-300 bg-trading-blue-500/[0.14] border-trading-blue-400/40' },
  normal:   { rgb: [161, 161, 170], cls: 'text-gray-300 bg-white/[0.05] border-white/[0.12]' },
  low:      { rgb: [143, 178, 134], cls: 'text-trading-green-300 bg-trading-green-500/[0.13] border-trading-green-400/40' },
  idle:     { rgb: [113, 113, 122], cls: 'text-gray-500 bg-white/[0.03] border-white/[0.08]' },
};

const STATE_STYLE = {
  running:   { rgb: [143, 178, 134], glow: true,  cls: 'text-trading-green-200 bg-trading-green-500/[0.16] border-trading-green-300/45' },
  launching: { rgb: [125, 164, 196], glow: false, cls: 'text-trading-blue-300 bg-trading-blue-500/[0.14] border-trading-blue-400/40' },
  suspended: { rgb: [198, 156, 86],  glow: false, cls: 'text-amber-300 bg-amber-500/[0.12] border-amber-400/40' },
  queued:    { rgb: [161, 161, 170], glow: false, cls: 'text-gray-300 bg-white/[0.04] border-white/[0.10]' },
  done:      { rgb: [143, 178, 134], glow: false, cls: 'text-trading-green-300/80 bg-trading-green-500/[0.08] border-trading-green-400/25' },
  failed:    { rgb: [201, 132, 132], glow: true,  cls: 'text-trading-red-300 bg-trading-red-500/[0.14] border-trading-red-400/45' },
  cancelled: { rgb: [113, 113, 122], glow: false, cls: 'text-gray-500 bg-white/[0.03] border-white/[0.08]' },
};

const DB_STYLE = {
  heavy: 'text-amber-300 bg-amber-500/[0.12] border-amber-400/40',
  light: 'text-trading-blue-300 bg-trading-blue-500/[0.12] border-trading-blue-400/35',
  none:  'text-gray-600 bg-white/[0.02] border-white/[0.06]',
};

const glow = (rgb, a1 = 0.5, a2 = 0.26) => {
  const [r, g, b] = rgb;
  return `0 0 6px rgba(${r},${g},${b},${a1}), 0 0 15px rgba(${r},${g},${b},${a2})`;
};

// utilization → color (green calm → amber pressure → red saturated)
const utilRgb = (pct) => (pct >= 90 ? [201, 132, 132] : pct >= 65 ? [198, 156, 86] : [143, 178, 134]);

const fmtAge = (s) => {
  if (s == null || !Number.isFinite(s)) return '—';
  s = Math.max(0, Math.floor(s));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
};

const fmtRam = (mb) => {
  if (mb == null) return '—';
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
};

function Chip({ children, cls, glowRgb, title }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase tracking-wider leading-none border ${cls}`}
      style={glowRgb ? { boxShadow: glow(glowRgb, 0.4, 0.2) } : undefined}
    >
      {children}
    </span>
  );
}

function GaugeCard({ icon: Icon, label, value, sub, pct }) {
  const clamped = Math.max(0, Math.min(100, pct ?? 0));
  const rgb = utilRgb(clamped);
  return (
    <div className="min-w-0 rounded-md border border-white/[0.06] bg-trading-dark-900 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-600">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1.5 font-mono text-[20px] font-semibold leading-none tabular-nums text-gray-100">{value}</div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.05]">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${clamped}%`,
            backgroundColor: `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`,
            boxShadow: clamped > 4 ? glow(rgb, 0.5, 0.0) : undefined,
          }}
        />
      </div>
      {sub != null && <div className="mt-1.5 text-[11px] leading-tight text-gray-500">{sub}</div>}
    </div>
  );
}

function CoresCell({ task, budgetCores }) {
  const grant = task.cpu_grant ?? task.cpu_request ?? 1;
  const pct = budgetCores ? Math.min(100, (grant / budgetCores) * 100) : 0;
  const rgb = task.throttled ? [113, 113, 122] : utilRgb(pct);
  return (
    <div className="flex items-center gap-2">
      <span className={`font-mono text-sm font-semibold tabular-nums ${task.throttled ? 'text-gray-500' : 'text-gray-200'}`}>{grant}</span>
      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-white/[0.05]">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` }} />
      </div>
      {task.throttled ? <Chip cls="text-amber-300 bg-amber-500/[0.12] border-amber-400/40" title="throttled to IDLE to yield cores">thr</Chip> : null}
    </div>
  );
}

function ActiveRow({ task, now, budgetCores }) {
  const ps = PRIORITY_STYLE[task.priority_name] || PRIORITY_STYLE.normal;
  const ss = STATE_STYLE[task.state] || STATE_STYLE.queued;
  const ref = task.state === 'running' || task.state === 'suspended' ? task.started_at : task.created_at;
  const runtime = ref ? fmtAge(now - ref) : '—';
  return (
    <tr className="border-b border-white/[0.04] hover:bg-white/[0.035]">
      <td className="px-3 py-2.5 font-mono text-xs text-gray-500 tabular-nums">#{task.id}</td>
      <td className="px-3 py-2.5"><Chip cls={ps.cls} title={`'${task.priority_name}' is the priority TIER (set at submit, never changes - 'scheduled' is the trader-update tier). Progress is the State column.`}>{task.priority_name}</Chip></td>
      <td className="px-3 py-2.5"><Chip cls={ss.cls} glowRgb={ss.glow ? ss.rgb : null}>{task.state}</Chip></td>
      <td className="px-3 py-2.5"><CoresCell task={task} budgetCores={budgetCores} /></td>
      <td className="px-3 py-2.5"><Chip cls={DB_STYLE[task.db_class] || DB_STYLE.none}>{task.db_class}</Chip></td>
      <td className="px-3 py-2.5 font-mono text-xs text-gray-400 tabular-nums">{fmtRam(task.ram_mb)}</td>
      <td className="px-3 py-2.5 font-mono text-xs text-gray-400 tabular-nums">{runtime}</td>
      <td className="px-3 py-2.5 min-w-0">
        <div className="truncate text-[13px] text-gray-300" title={task.reason || ''}>{task.reason || <span className="text-gray-600">—</span>}</div>
        <div className="truncate font-mono text-[11px] text-gray-600" title={task.command}>{task.command}</div>
      </td>
    </tr>
  );
}

function RecentRow({ task, now }) {
  const ps = PRIORITY_STYLE[task.priority_name] || PRIORITY_STYLE.normal;
  const ss = STATE_STYLE[task.state] || STATE_STYLE.done;
  const dur = task.started_at && task.finished_at ? fmtAge(task.finished_at - task.started_at) : '—';
  return (
    <tr className="border-b border-white/[0.04] hover:bg-white/[0.03]">
      <td className="px-3 py-2 font-mono text-xs text-gray-600 tabular-nums">#{task.id}</td>
      <td className="px-3 py-2"><Chip cls={ss.cls} glowRgb={ss.glow ? ss.rgb : null}>{task.state}</Chip></td>
      <td className="px-3 py-2"><Chip cls={ps.cls} title={`'${task.priority_name}' is the priority TIER it ran at (set at submit, never changes). The outcome is the State column.`}>{task.priority_name}</Chip></td>
      <td className="px-3 py-2 font-mono text-xs text-gray-500 tabular-nums">{dur}</td>
      <td className="px-3 py-2 font-mono text-xs tabular-nums">
        <span className={task.exit_code === 0 ? 'text-trading-green-300/70' : task.exit_code == null ? 'text-gray-600' : 'text-trading-red-300'}>
          {task.exit_code == null ? '—' : task.exit_code}
        </span>
      </td>
      <td className="px-3 py-2 min-w-0">
        <div className="truncate font-mono text-[11px] text-gray-500" title={task.command}>{task.command}</div>
        {task.error ? <div className="truncate text-[11px] text-trading-red-300/80" title={task.error}>{task.error}</div> : null}
      </td>
    </tr>
  );
}

export default function Queue() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now() / 1000);
  const seen = useRef(false);

  const fetchData = useCallback(async () => {
    try {
      const bucket = Math.floor(Date.now() / POLL_MS);
      const res = await fetch(`${API_BASE_URL}/api/queue/status?_t=${bucket}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = await res.json();
      if (payload.error) throw new Error(payload.error);
      setData(payload);
      setError(null);
      seen.current = true;
    } catch (err) {
      setError(err.message || 'Unable to reach the queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const poll = setInterval(fetchData, POLL_MS);
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => { clearInterval(poll); clearInterval(tick); };
  }, [fetchData]);

  const daemon = data?.daemon;
  const res = data?.resources;
  const machine = data?.machine;
  const active = data?.active || [];
  const recent = data?.recent || [];
  const budget = res?.budget || {};
  const used = res?.used || {};
  const daemonUp = !!daemon?.alive;

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden bg-trading-dark-950 text-gray-100">
      <div className="mx-auto flex min-w-0 max-w-[1500px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">

        {/* header */}
        <header className="flex flex-col gap-4 border-b border-white/[0.06] pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-trading-green-300">
              <Layers className="h-4 w-4" />
              Task Queue
            </div>
            <h1 className="text-2xl font-semibold tracking-normal text-gray-100 sm:text-3xl">Compute Scheduler</h1>
            <div className="mt-2 flex items-center gap-2 text-sm">
              <span
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12px] font-semibold ${
                  daemonUp ? 'border-trading-green-300/40 bg-trading-green-500/[0.12] text-trading-green-200'
                           : 'border-trading-red-400/40 bg-trading-red-500/[0.12] text-trading-red-300'}`}
                style={daemonUp ? { boxShadow: glow([143, 178, 134], 0.4, 0.2) } : { boxShadow: glow([201, 132, 132], 0.4, 0.2) }}
              >
                <CircleDot className="h-3.5 w-3.5" />
                daemon {daemonUp ? 'online' : 'down'}
              </span>
              <span className="text-gray-500">
                {daemonUp ? `pid ${daemon.pid} · heartbeat ${fmtAge(daemon.heartbeat_age_s)} ago` : (daemon?.reason || 'not running')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[11px] text-gray-600">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-trading-green-300/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-trading-green-300" />
              </span>
              live · {POLL_MS / 1000}s
            </span>
            <button
              type="button"
              onClick={fetchData}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-gray-200 hover:bg-white/[0.06]"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </header>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-trading-red-400/30 bg-trading-red-500/10 px-3 py-2 text-sm text-trading-red-200">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        )}

        {/* resource gauges */}
        <section className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <GaugeCard icon={Cpu} label="CPU cores" value={`${used.cores ?? 0} / ${budget.cores ?? 0}`}
            sub={`${budget.cores != null ? Math.max(0, budget.cores - (used.cores ?? 0)) : 0} free · cap ${res?.low_priority_core_cap ?? '—'} low-pri`}
            pct={budget.cores ? ((used.cores ?? 0) / budget.cores) * 100 : 0} />
          <GaugeCard icon={Database} label="DB slots" value={`${used.db ?? 0} / ${budget.db ?? 0}`}
            sub="≤1 heavy job at a time" pct={budget.db ? ((used.db ?? 0) / budget.db) * 100 : 0} />
          <GaugeCard icon={HardDrive} label="IO slots" value={`${used.io ?? 0} / ${budget.io ?? 0}`}
            sub=" " pct={budget.io ? ((used.io ?? 0) / budget.io) * 100 : 0} />
          <GaugeCard icon={Activity} label="Machine CPU" value={machine?.cpu_percent != null ? `${machine.cpu_percent.toFixed(0)}%` : '—'}
            sub={`${machine?.cpu_count ?? '—'} logical cores`} pct={machine?.cpu_percent ?? 0} />
          <GaugeCard icon={Gauge} label="Machine RAM" value={machine?.ram_percent != null ? `${machine.ram_percent.toFixed(0)}%` : '—'}
            sub={machine?.ram_total_gb != null ? `${machine.ram_used_gb} / ${machine.ram_total_gb} GB` : ' '} pct={machine?.ram_percent ?? 0} />
        </section>

        {/* active tasks */}
        <section className="min-w-0">
          <div className="mb-2 flex items-baseline gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-gray-400">Active</h2>
            <span className="font-mono text-xs text-gray-600">{active.length}</span>
          </div>
          {active.length === 0 ? (
            <div className="flex h-28 items-center justify-center rounded-md border border-white/[0.06] bg-white/[0.02] text-sm text-gray-500">
              {loading && !seen.current ? 'Loading…' : 'Queue is idle — nothing running or waiting.'}
            </div>
          ) : (
            <div className="min-w-0 overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02]">
              <div className="max-w-full overflow-x-auto">
                <table className="w-full min-w-[920px] border-collapse">
                  <thead className="bg-trading-dark-900/80">
                    <tr className="border-b border-white/[0.06] text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
                      <th className="px-3 py-2.5 text-left">ID</th>
                      <th className="px-3 py-2.5 text-left">Priority</th>
                      <th className="px-3 py-2.5 text-left">State</th>
                      <th className="px-3 py-2.5 text-left">Cores</th>
                      <th className="px-3 py-2.5 text-left">DB</th>
                      <th className="px-3 py-2.5 text-left">RAM</th>
                      <th className="px-3 py-2.5 text-left">Runtime</th>
                      <th className="px-3 py-2.5 text-left">Task</th>
                    </tr>
                  </thead>
                  <tbody>
                    {active.map((t) => <ActiveRow key={t.id} task={t} now={now} budgetCores={budget.cores} />)}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        {/* recent */}
        {recent.length > 0 && (
          <section className="min-w-0">
            <div className="mb-2 flex items-baseline gap-2">
              <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-gray-400">Recent</h2>
              <span className="font-mono text-xs text-gray-600">{recent.length}</span>
            </div>
            <div className="min-w-0 overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02]">
              <div className="max-w-full overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse">
                  <thead className="bg-trading-dark-900/80">
                    <tr className="border-b border-white/[0.06] text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">
                      <th className="px-3 py-2 text-left">ID</th>
                      <th className="px-3 py-2 text-left">State</th>
                      <th className="px-3 py-2 text-left">Priority</th>
                      <th className="px-3 py-2 text-left">Duration</th>
                      <th className="px-3 py-2 text-left">Exit</th>
                      <th className="px-3 py-2 text-left">Task</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((t) => <RecentRow key={t.id} task={t} now={now} />)}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        <p className="pb-2 text-[11px] leading-relaxed text-gray-600">
          Submit work with <span className="font-mono text-gray-500">trader queue submit --priority low -- &lt;cmd&gt;</span>.
          Low/idle jobs run on a reduced core grant and yield to higher-priority work automatically.
        </p>
      </div>
    </div>
  );
}
