import React from 'react';
import clsx from 'clsx';

/**
 * OVR: tinted bg + border + optional amber glow on extremes (flagged-ticker palette).
 */
const TONE = {
  excellent: {
    body: 'text-[#7FBFA0] bg-trading-green-500/[0.22]',
    border: 'border border-[#7FBFA0]/55',
  },
  strong: {
    body: 'text-trading-green-400 bg-trading-green-500/[0.15]',
    border: 'border border-trading-green-400/45',
  },
  promising: {
    body: 'text-trading-green-500 bg-trading-green-500/[0.08]',
    border: 'border border-trading-green-500/30',
  },
  neutral: {
    body: 'text-gray-400 bg-white/[0.03]',
    border: 'border border-white/[0.08]',
  },
  weak: {
    body: 'text-trading-red-400 bg-trading-red-500/[0.15]',
    border: 'border border-trading-red-400/45',
  },
  veryWeak: {
    body: 'text-trading-red-300 bg-trading-red-500/[0.16]',
    border: 'border border-trading-red-400/50',
  },
};

const MID_RANGE_BORDER = 'border border-trading-dark-600/55';

const toneShell = (key, rawScore) => {
  const t = TONE[key];
  const s = Math.round(Number(rawScore));
  const mid = Number.isFinite(s) && s >= 26 && s <= 79;
  return clsx(t.body, mid ? MID_RANGE_BORDER : t.border);
};

/** Muted gold (softer than flagged ticker accent); 0 on [21,79]; 0→1 from 80→100 and 20→0. */
const AMBER_RGB = [198, 156, 86];

const glowIntensity = (value) => {
  const s = Math.round(Number(value));
  if (!Number.isFinite(s)) return 0;
  if (s >= 26 && s <= 79) return 0;
  if (s >= 80) return Math.min(1, (s - 80) / 20);
  if (s <= 25) return Math.min(1, (25 - s) / 20);
  return 0;
};

function proportionalGlowBoxShadow(score) {
  const ex = glowIntensity(score);
  if (ex <= 0) return 'none';
  const [r, g, b] = AMBER_RGB;
  const k = (v) => (ex * v).toFixed(3);
  return [
    `0 0 0 1px rgba(${r},${g},${b},${k(0.3)})`,
    `inset 0 1px 0 0 rgba(${r},${g},${b},${k(0.1)})`,
    `0 0 6px rgba(${r},${g},${b},${k(0.48)})`,
    `0 0 14px rgba(${r},${g},${b},${k(0.3)})`,
    `0 0 26px rgba(${r},${g},${b},${k(0.16)})`,
  ].join(', ');
}

const toneKey = (value) => {
  if (value >= 80) return 'excellent';
  if (value >= 75) return 'strong';
  if (value >= 70) return 'promising';
  if (value >= 26) return 'neutral';
  if (value <= 15) return 'veryWeak';
  return 'weak';
};

/** 2ch + horizontal padding matches a double-digit tabular width; single digits align to that. */
const SCORE_BADGE_BOX =
  'inline-flex items-center justify-center aspect-square shrink-0 font-mono tabular-nums font-semibold leading-none rounded-md box-border px-2 py-1.5 min-w-[calc(2ch+1rem)]';

const formatScoreInt = (value) => {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? String(n) : '';
};

const PLAIN_TEXT = {
  excellent: 'text-[#7FBFA0]',
  strong: 'text-trading-green-400',
  promising: 'text-trading-green-500',
  neutral: 'text-gray-400',
  weak: 'text-trading-red-400',
  veryWeak: 'text-trading-red-300',
};

const ScoreBadge = ({ score, className = '', showLabel = false, plain = false }) => {
  if (score === null || score === undefined) {
    if (plain) {
      return (
        <span
          className={clsx(SCORE_BADGE_BOX, 'text-xs text-gray-500', className)}
        >
          N/A
        </span>
      );
    }
    return (
      <span
        className={clsx(
          SCORE_BADGE_BOX,
          'text-xs text-gray-400 border border-trading-dark-600/55 bg-trading-dark-800/55 ring-1 ring-trading-dark-700/35 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)]',
          className
        )}
      >
        N/A
      </span>
    );
  }

  const getScoreLabel = (value) => {
    if (value >= 80) return 'Excellent';
    if (value >= 75) return 'Strong';
    if (value >= 70) return 'Promising';
    if (value >= 26) return 'Neutral';
    if (value <= 15) return 'Very Weak';
    return 'Weak';
  };

  if (plain) {
    return (
      <span
        className={clsx(
          'inline-flex items-center justify-center gap-1 text-sm',
          className
        )}
      >
        <span className={clsx(SCORE_BADGE_BOX, 'text-sm', PLAIN_TEXT[toneKey(score)])}>
          {formatScoreInt(score)}
        </span>
        {showLabel && (
          <span className="text-xs opacity-75 shrink-0">
            ({getScoreLabel(score)})
          </span>
        )}
      </span>
    );
  }

  const key = toneKey(score);
  const boxShadow = proportionalGlowBoxShadow(score);

  const squareShell = clsx(SCORE_BADGE_BOX, 'text-sm', toneShell(key, score));

  if (showLabel) {
    return (
      <span className={clsx('inline-flex items-center gap-1', className)}>
        <span className={squareShell} style={{ boxShadow }}>
          {formatScoreInt(score)}
        </span>
        <span className="text-xs opacity-75 shrink-0">
          ({getScoreLabel(score)})
        </span>
      </span>
    );
  }

  return (
    <span className={clsx(squareShell, className)} style={{ boxShadow }}>
      {formatScoreInt(score)}
    </span>
  );
};

export default ScoreBadge;
