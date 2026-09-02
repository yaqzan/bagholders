import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, GitCommit, List } from 'lucide-react';

export function formatScoreVersionDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}`.includes('T') ? iso : `${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function versionLabel(version) {
  if (!version) return '';
  return version.label || `v${version.id}`;
}

function VersionOption({ version, selected, active, onSelect }) {
  const description = version.display_description || version.selector_description || version.git_message;
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={() => onSelect(active ? null : version.id)}
      className={`w-full px-3 py-2 text-left flex items-center gap-2 hover:bg-white/[0.03] transition-colors ${
        selected ? 'bg-white/[0.04]' : ''
      }`}
    >
      <Check className={`w-3.5 h-3.5 flex-shrink-0 ${selected ? 'text-trading-green-300' : 'text-transparent'}`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-mono text-white truncate tabular-nums">
          {versionLabel(version)}
          {version.git_commit && (
            <span className="text-gray-500 ml-1">{version.git_commit.slice(0, 7)}</span>
          )}
          {version.latest_score_date && (
            <span className="text-[11px] text-gray-600 ml-2 font-sans">{formatScoreVersionDate(version.latest_score_date)}</span>
          )}
          {active && (
            <span className="text-[10px] uppercase tracking-[0.14em] text-trading-green-300/90 ml-2">active</span>
          )}
        </p>
        {description && (
          <p className="text-xs text-gray-500 truncate">{description}</p>
        )}
      </div>
    </button>
  );
}

export default function ScoreVersionSelector({
  versions,
  legacyVersions,
  currentVersion,
  activeVersionId,
  scoreDate,
  selectedVersionId,
  onSelect,
  align = 'right',
  ariaLabel = 'Select score version',
  titlePrefix = 'Scores',
  compact = false,
}) {
  const [open, setOpen] = useState(false);
  const [showLegacy, setShowLegacy] = useState(false);
  const ref = useRef(null);
  const primary = Array.isArray(versions) ? versions : [];
  const legacy = Array.isArray(legacyVersions) ? legacyVersions : [];
  const all = [...primary, ...legacy];
  const effectiveActiveId = activeVersionId ?? all.find(v => v.is_active)?.id ?? null;
  const selected = selectedVersionId != null
    ? all.find(v => v.id === selectedVersionId)
    : null;
  const current = selected
    || currentVersion
    || all.find(v => v.id === effectiveActiveId)
    || primary[0]
    || legacy[0];

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!current) return null;

  const isHistorical = effectiveActiveId != null && current.id !== effectiveActiveId;
  const dateLabel = formatScoreVersionDate(scoreDate || current.latest_score_date);
  const title = [
    `${titlePrefix}: ${versionLabel(current)}`,
    current.display_description || current.selector_description || null,
    current.git_commit ? `Commit: ${current.git_commit.slice(0, 7)}` : null,
    scoreDate || current.latest_score_date ? `Score date: ${scoreDate || current.latest_score_date}` : null,
  ].filter(Boolean).join('\n');
  const menuAlign = align === 'left' ? 'left-0' : 'right-0';

  const choose = (versionId) => {
    onSelect(versionId);
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        title={title}
        className={`${compact ? 'h-7 px-2' : 'h-7 sm:h-8 px-2 sm:px-2.5'} rounded-md border flex items-center gap-1.5 transition-colors ${
          isHistorical
            ? 'bg-amber-500/[0.08] border-amber-500/25 text-amber-200'
            : 'bg-white/[0.03] border-white/[0.08] text-gray-300 hover:border-white/[0.14]'
        }`}
      >
        <GitCommit className={`${compact ? '' : 'hidden sm:block'} w-3.5 h-3.5 text-gray-500 flex-shrink-0`} />
        <span className="font-mono text-[12px] tabular-nums leading-none">{versionLabel(current)}</span>
        {dateLabel && (
          <span className="hidden lg:inline text-[11px] text-gray-600 font-sans leading-none">{dateLabel}</span>
        )}
        <ChevronDown className={`w-3.5 h-3.5 text-gray-500 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div
          role="listbox"
          className={`absolute ${menuAlign} top-full mt-1 z-30 w-[min(88vw,340px)] max-h-96 overflow-y-auto bg-trading-dark-900 border border-white/[0.08] rounded-md shadow-xl py-1`}
        >
          {primary.map(v => (
            <VersionOption
              key={v.id}
              version={v}
              selected={v.id === current.id}
              active={effectiveActiveId != null && v.id === effectiveActiveId}
              onSelect={choose}
            />
          ))}

          {legacy.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setShowLegacy(v => !v)}
                className="w-full px-3 py-2 text-left flex items-center gap-2 hover:bg-white/[0.03] transition-colors border-t border-white/[0.06]"
              >
                <List className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-gray-300">More versions</p>
                  <p className="text-[11px] text-gray-600">{legacy.length} legacy versions</p>
                </div>
                <ChevronDown className={`w-3.5 h-3.5 text-gray-500 flex-shrink-0 transition-transform ${showLegacy ? 'rotate-180' : ''}`} />
              </button>

              {showLegacy && legacy.map(v => (
                <VersionOption
                  key={v.id}
                  version={v}
                  selected={v.id === current.id}
                  active={effectiveActiveId != null && v.id === effectiveActiveId}
                  onSelect={choose}
                />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
