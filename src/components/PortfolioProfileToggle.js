import React from 'react';
import { Flame, Shield, Zap } from 'lucide-react';

export const PROFILE_PRESENTATION = {
  sentinel: { label: 'Safe', icon: Shield, risk: 'Safe' },
  core: { label: 'Balanced', icon: Zap, risk: 'Balanced' },
  apex: { label: 'Explosive', icon: Flame, risk: 'Explosive' },
};

export const DEFAULT_PORTFOLIO_PROFILES = [
  { key: 'sentinel', name: 'Sentinel', label: 'Safe', color: 'green', risk: 'Safe' },
  { key: 'core', name: 'Core', label: 'Balanced', color: 'yellow', risk: 'Balanced' },
  { key: 'apex', name: 'Apex', label: 'Explosive', color: 'red', risk: 'Explosive' },
];

const TONES = {
  green: {
    active: 'border-trading-green-300/45 bg-trading-green-300/12 text-trading-green-200',
    dot: 'bg-trading-green-300',
  },
  yellow: {
    active: 'border-amber-300/45 bg-amber-300/12 text-amber-200',
    dot: 'bg-amber-300',
  },
  red: {
    active: 'border-trading-red-300/45 bg-trading-red-300/12 text-trading-red-200',
    dot: 'bg-trading-red-300',
  },
};

export function profileTone(profile) {
  return TONES[profile?.color] || TONES.green;
}

export function getProfilePresentation(profile) {
  const key = typeof profile === 'string' ? profile : profile?.key;
  return PROFILE_PRESENTATION[key] || {};
}

export function normalizeProfiles(profiles) {
  const source = Array.isArray(profiles) && profiles.length ? profiles : DEFAULT_PORTFOLIO_PROFILES;
  return source.map((profile) => {
    const presentation = getProfilePresentation(profile);
    return {
      ...profile,
      key: profile.key,
      name: profile.name || profile.key,
      label: presentation.label || profile.label || profile.risk,
      color: profile.color || (profile.key === 'apex' ? 'red' : profile.key === 'core' ? 'yellow' : 'green'),
      risk: presentation.risk || profile.risk,
      params: profile.params || {},
      version: profile.version,
      candidate: profile.candidate,
    };
  });
}

export default function PortfolioProfileToggle({
  profiles,
  value,
  onChange,
  compact = false,
  className = '',
}) {
  const rows = normalizeProfiles(profiles);
  const selected = rows.some((profile) => profile.key === value) ? value : 'apex';
  return (
    <div className={`inline-flex items-center rounded-md border border-white/[0.08] bg-trading-dark-900 p-0.5 ${className}`}>
      {rows.map((profile) => {
        const tone = profileTone(profile);
        const { icon: Icon = Shield } = getProfilePresentation(profile);
        const active = selected === profile.key;
        return (
          <button
            key={profile.key}
            type="button"
            onClick={() => onChange?.(profile.key)}
            className={`inline-flex h-9 min-w-[96px] items-center justify-center gap-1.5 rounded px-2 text-[11px] font-medium transition-colors ${
              active
                ? tone.active
                : 'border border-transparent text-gray-500 hover:bg-white/[0.04] hover:text-gray-300'
            } ${compact ? 'min-w-[82px] px-1.5' : ''}`}
            title={`${profile.name} portfolio profile${profile.label ? ` (${profile.label})` : ''}`}
          >
            <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? '' : 'opacity-80'}`} />
            <span className="flex min-w-0 flex-col items-start leading-none">
              <span className="max-w-[64px] truncate">{profile.name}</span>
              {profile.label && <span className="mt-0.5 max-w-[64px] truncate text-[9px] opacity-75">{profile.label}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
