import React, { useMemo, useState } from 'react';
import { useStock } from '../context/StockContext';

// ── DTE calibration from monte carlo analysis ────────────────────────────────
// Option DTE ≈ 2× assessment window (BS premium scaling: W ≈ 0.49 × DTE)
const DTE_OPTIONS = [
  { dte: 15, period: '7d',  label: '15d' },
  { dte: 30, period: '15d', label: '30d' },
  { dte: 60, period: '30d', label: '60d' },
];

// Score → tightest cumulative assessment bucket
function scoreToBucket(score) {
  if (score == null) return null;
  if (score >= 99) return '99+';
  if (score >= 95) return '95+';
  if (score >= 90) return '90+';
  if (score >= 85) return '85+';
  if (score >= 80) return '80+';
  if (score >= 75) return '75+';
  if (score >= 70) return '70+';
  if (score <= 5)  return '<5';
  if (score <= 10) return '<10';
  if (score <= 15) return '<15';
  if (score <= 20) return '<20';
  if (score <= 25) return '<25';
  if (score <= 30) return '<30';
  return null;
}

function isCallSide(score) {
  return score >= 50;
}

function winRateColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 75) return 'text-trading-green-300';
  if (v >= 65) return 'text-trading-green-400';
  if (v >= 55) return 'text-amber-300';
  if (v >= 45) return 'text-amber-400';
  return 'text-trading-red-400';
}

function winRateBg(v) {
  if (v == null) return '';
  if (v >= 75) return '';
  if (v >= 65) return '';
  if (v >= 55) return '';
  return '';
}

// RTR = (win% − 71%) / 29% — edge above random walk
function rtrColor(v) {
  if (v == null) return 'text-gray-600';
  if (v > 30)  return 'text-trading-green-300';
  if (v > 10)  return 'text-trading-green-400';
  if (v > 0)   return 'text-amber-300';
  return 'text-trading-red-400';
}

function rtrBg(v) {
  if (v == null) return '';
  return '';
}

// ── Component ────────────────────────────────────────────────────────────────

const DteRecommendation = ({
  score,
  realizedVol60d = null,
  currentPrice = null,
}) => {
  const { assessmentData } = useStock();
  const [expanded, setExpanded] = useState(false);

  const bucket = useMemo(() => scoreToBucket(score), [score]);

  const bucketData = useMemo(() => {
    if (!assessmentData?.buckets || !bucket) return null;
    return assessmentData.buckets.find(b => b.bucket === bucket);
  }, [assessmentData, bucket]);

  // Build DTE options with win rates and returns from assessment data
  const dteEntries = useMemo(() => {
    if (!bucketData) return [];
    return DTE_OPTIONS.map(opt => ({
      ...opt,
      winRate:        bucketData.win_rate?.[opt.period],
      rtrWinRate:     bucketData.rtr_win_rate?.[opt.period],
      avgReturn:      bucketData.avg_return?.[opt.period],
      avgMfe:         bucketData.avg_mfe?.[opt.period],
      captureRatio:   bucketData.capture_ratio?.[opt.period],
      maeWinnerSigma: bucketData.avg_mae_winner_sigma?.[opt.period],
      mfeSigmaP25:    bucketData.mfe_sigma_p25?.[opt.period],
      medianMfeSigma: bucketData.median_mfe_sigma?.[opt.period],
      mfeSigmaP75:    bucketData.mfe_sigma_p75?.[opt.period],
    }));
  }, [bucketData]);

  // Best DTE = highest RTR win rate (edge above random walk baseline)
  // Falls back to raw win rate if RTR is unavailable.
  const bestDte = useMemo(() => {
    const rtrValid = dteEntries.filter(e => e.rtrWinRate != null);
    if (rtrValid.length)
      return rtrValid.reduce((best, e) => (e.rtrWinRate > best.rtrWinRate ? e : best));
    const wrValid = dteEntries.filter(e => e.winRate != null);
    return wrValid.length ? wrValid.reduce((best, e) => (e.winRate > best.winRate ? e : best)) : null;
  }, [dteEntries]);

  // Winner-MAE → stop: sigma × current realized vol, applied as % off entry.
  // Option estimate uses ~2× delta-equivalent for ATM contracts (crude).
  const stopInfo = useMemo(() => {
    if (!bestDte || bestDte.maeWinnerSigma == null || realizedVol60d == null) return null;
    // MAE is stored as a negative % (drawdown direction). Take magnitude for stop width.
    const stopPct = Math.abs(bestDte.maeWinnerSigma * realizedVol60d);
    if (!isFinite(stopPct) || stopPct <= 0) return null;
    const isCall = isCallSide(score);
    const stopPrice = currentPrice != null
      ? (isCall ? currentPrice * (1 - stopPct / 100) : currentPrice * (1 + stopPct / 100))
      : null;
    const optionPct = stopPct * 2;
    return { stopPct, stopPrice, optionPct, isCall };
  }, [bestDte, realizedVol60d, currentPrice, score]);

  // MFE sigma → TP: de-normalize using current realized vol (same form as win barrier K·σ).
  // Conservative = p25 (75% of similar signals reached this within the holding window).
  // Suppressed for puts — structural calibration gap confirmed; p25=0.57σ at 30d.
  const tpInfo = useMemo(() => {
    if (!bestDte || bestDte.mfeSigmaP25 == null || realizedVol60d == null) return null;
    const isCall = isCallSide(score);
    if (!isCall) return null;   // put TP unreliable — suppress
    const conservativePct = bestDte.mfeSigmaP25 * realizedVol60d;
    const basePct = bestDte.medianMfeSigma != null ? bestDte.medianMfeSigma * realizedVol60d : null;
    const aggressivePct = bestDte.mfeSigmaP75 != null ? bestDte.mfeSigmaP75 * realizedVol60d : null;
    if (!isFinite(conservativePct) || conservativePct <= 0) return null;
    const tpPrice = currentPrice != null ? currentPrice * (1 + conservativePct / 100) : null;
    return { conservativePct, basePct, aggressivePct, tpPrice };
  }, [bestDte, realizedVol60d, currentPrice, score]);

  if (score == null || !bucket) return null;

  const isCall = isCallSide(score);
  const sideLabel = isCall ? 'CALL' : 'PUT';
  const sideColor = isCall ? 'text-trading-green-300' : 'text-trading-red-300';
  const sideRail = isCall ? 'bg-trading-green-400' : 'bg-trading-red-400';

  return (
    <div className="mx-3 mt-2 mb-1">
      <div
        className="relative bg-trading-dark-900 border border-white/[0.06] rounded-md pl-4 pr-3 py-2 cursor-pointer select-none hover:border-white/[0.12] transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`absolute left-0 top-1 bottom-1 w-[2px] ${sideRail}`} aria-hidden />
        {/* Main row */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Side badge */}
          <span className={`text-[11px] font-semibold tracking-[0.18em] font-mono ${sideColor} uppercase`}>
            {sideLabel}
          </span>

          {/* Score band */}
          <span className="text-[11px] font-mono font-medium text-gray-300 px-1.5 py-0.5 rounded bg-white/[0.04] tabular-nums">
            {bucket}
          </span>

          {/* DTE win rates - compact */}
          {dteEntries.length > 0 ? (
            <div className="flex items-center gap-2">
              {dteEntries.map(entry => {
                const isBest = bestDte && entry.dte === bestDte.dte;
                const rtr = entry.rtrWinRate;
                return (
                  <span
                    key={entry.dte}
                    className={`text-xs font-mono px-1.5 py-0.5 rounded ${
                      isBest
                        ? `font-bold ${rtrColor(rtr)} ${rtrBg(rtr)} ring-1 ring-current/30`
                        : `text-gray-400`
                    }`}
                    title={`${entry.label} DTE | win: ${entry.winRate?.toFixed(1) ?? '—'}% | RTR: ${rtr != null ? (rtr > 0 ? '+' : '') + rtr.toFixed(1) + '%' : '—'}`}
                  >
                    {entry.label}:{' '}
                    <span className={isBest ? '' : rtrColor(rtr)}>
                      {rtr != null ? `${rtr > 0 ? '+' : ''}${rtr.toFixed(0)}%` : (entry.winRate != null ? `${entry.winRate.toFixed(0)}%` : '—')}
                    </span>
                  </span>
                );
              })}
              <span className="text-[10px] text-gray-600 font-mono">RTR</span>
            </div>
          ) : (
            <span className="text-xs text-gray-500">No assessment data</span>
          )}

          {/* Winner-MAE stop (best DTE) */}
          {stopInfo && (
            <span
              className="text-[11px] font-mono px-1.5 py-0.5 rounded text-trading-red-300 border border-trading-red-400/30 tabular-nums"
              title={`Winner MAE stop @ best DTE (${bestDte.label}): survivors drew down ~${stopInfo.stopPct.toFixed(1)}% underlying (σ=${realizedVol60d.toFixed(2)}%/d). Option est ≈ ${stopInfo.optionPct.toFixed(0)}% for ATM.`}
            >
              stop −{stopInfo.stopPct.toFixed(1)}%
              {stopInfo.stopPrice != null && (
                <span className="text-gray-500 ml-1">(${stopInfo.stopPrice.toFixed(2)})</span>
              )}
              <span className="text-gray-500 ml-1">· opt ~−{stopInfo.optionPct.toFixed(0)}%</span>
            </span>
          )}

          {/* MFE TP per DTE interval with prices */}
          {isCall && dteEntries.some(e => e.mfeSigmaP25 != null && realizedVol60d != null) && (
            <div className="flex items-baseline gap-1.5 px-1.5 py-0.5 rounded border border-trading-green-400/30">
              {dteEntries.map((entry, idx) => {
                const p25 = entry.mfeSigmaP25;
                if (p25 == null || realizedVol60d == null) return null;
                const pct = (p25 * realizedVol60d).toFixed(1);
                const price = currentPrice != null ? (currentPrice * (1 + p25 * realizedVol60d / 100)).toFixed(2) : null;
                return (
                  <span key={entry.dte} className="text-[11px] font-mono text-trading-green-300 whitespace-nowrap tabular-nums">
                    {idx > 0 && <span className="text-gray-700 mx-0.5">·</span>}
                    <span>{entry.label}: +{pct}%</span>
                    {price && <span className="text-gray-500 ml-0.5">${price}</span>}
                  </span>
                );
              }).filter(Boolean)}
            </div>
          )}

          {/* Sample count */}
          {bucketData && (
            <span className="text-xs text-gray-600 font-mono">
              n={bucketData.sample_count}
            </span>
          )}

          {/* Expand indicator */}
          <span className="text-gray-600 text-xs ml-auto shrink-0">
            {expanded ? '▲' : '▼'}
          </span>
        </div>

        {/* Expanded detail */}
        {expanded && dteEntries.length > 0 && (
          <div className="mt-2 pt-2 border-t border-gray-700/50">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500">
                  <th className="text-left py-1 pr-3 font-medium">Option DTE</th>
                  <th className="text-right py-1 px-2 font-medium">Win%</th>
                  <th className="text-right py-1 px-2 font-medium" title="(win% − 71%) / 29% — edge above random walk">RTR%</th>
                  <th className="text-right py-1 px-2 font-medium">Avg Ret</th>
                  <th className="text-right py-1 px-2 font-medium">MFE</th>
                  <th className="text-right py-1 px-2 font-medium">Cap</th>
                  <th className="text-right py-1 px-2 font-medium" title="Winner MAE stop: avg drawdown survivors endured, de-normalized to current realized vol. Option estimate ~2× for ATM.">Stop</th>
                  <th className="text-right py-1 px-2 font-medium" title="Take profit targets (calls only): p25/p50/p75 of MFE sigma distribution × current realized vol. 75% / 50% / 25% of similar signals reached these levels within the holding window.">TP</th>
                </tr>
              </thead>
              <tbody>
                {dteEntries.map(entry => {
                  const isBest = bestDte && entry.dte === bestDte.dte;
                  const rtr = entry.rtrWinRate;
                  const entryStopPct = (entry.maeWinnerSigma != null && realizedVol60d != null)
                    ? Math.abs(entry.maeWinnerSigma * realizedVol60d) : null;
                  return (
                    <tr
                      key={entry.dte}
                      className={isBest ? 'bg-white/5' : ''}
                    >
                      <td className="py-1 pr-3 font-mono text-gray-300">
                        {entry.label}
                        <span className="text-gray-600 ml-1">({entry.period})</span>
                        {isBest && <span className="text-trading-green-300 ml-1.5 text-[10px] uppercase tracking-[0.14em]">best</span>}
                      </td>
                      <td className={`py-1 px-2 text-right font-mono tabular-nums ${winRateColor(entry.winRate)}`}>
                        {entry.winRate != null ? `${entry.winRate.toFixed(1)}%` : '—'}
                      </td>
                      <td className={`py-1 px-2 text-right font-mono font-medium tabular-nums ${rtrColor(rtr)}`}>
                        {rtr != null ? `${rtr > 0 ? '+' : ''}${rtr.toFixed(1)}%` : '—'}
                      </td>
                      <td className={`py-1 px-2 text-right font-mono tabular-nums ${
                        entry.avgReturn > 0 ? 'text-trading-green-400' : entry.avgReturn < 0 ? 'text-trading-red-400' : 'text-gray-500'
                      }`}>
                        {entry.avgReturn != null ? `${entry.avgReturn > 0 ? '+' : ''}${entry.avgReturn.toFixed(2)}%` : '—'}
                      </td>
                      <td className="py-1 px-2 text-right font-mono text-gray-400 tabular-nums">
                        {entry.avgMfe != null ? `${entry.avgMfe > 0 ? '+' : ''}${entry.avgMfe.toFixed(2)}%` : '—'}
                      </td>
                      <td className={`py-1 px-2 text-right font-mono tabular-nums ${
                        entry.captureRatio >= 0.5 ? 'text-trading-green-300' : entry.captureRatio >= 0.3 ? 'text-amber-300' : 'text-gray-500'
                      }`}>
                        {entry.captureRatio != null ? entry.captureRatio.toFixed(2) : '—'}
                      </td>
                      <td className="py-1 px-2 text-right font-mono text-trading-red-300 tabular-nums"
                          title={entryStopPct != null
                            ? `−${entryStopPct.toFixed(1)}% underlying · ~−${(entryStopPct * 2).toFixed(0)}% option (ATM est.)`
                            : 'No vol or sigma data'}>
                        {entryStopPct != null
                          ? (<>
                              −{entryStopPct.toFixed(1)}%
                              <span className="text-gray-500 ml-1">/ −{(entryStopPct * 2).toFixed(0)}%</span>
                            </>)
                          : '—'}
                      </td>
                      {(() => {
                        if (!isCall) return <td className="py-1 px-2 text-right font-mono text-gray-600" title="Put TP suppressed — structural calibration gap">—</td>;
                        const p25pct = (entry.mfeSigmaP25 != null && realizedVol60d != null) ? entry.mfeSigmaP25 * realizedVol60d : null;
                        const p50pct = (entry.medianMfeSigma != null && realizedVol60d != null) ? entry.medianMfeSigma * realizedVol60d : null;
                        const p75pct = (entry.mfeSigmaP75 != null && realizedVol60d != null) ? entry.mfeSigmaP75 * realizedVol60d : null;
                        const tip = [
                          p25pct != null ? `75% fill: +${p25pct.toFixed(1)}%` : '',
                          p50pct != null ? `50% fill: +${p50pct.toFixed(1)}%` : '',
                          p75pct != null ? `25% fill: +${p75pct.toFixed(1)}%` : '',
                        ].filter(Boolean).join(' · ');
                        return (
                          <td className="py-1 px-2 text-right font-mono text-trading-green-300 tabular-nums" title={tip || 'No TP data'}>
                            {p25pct != null ? (
                              <>
                                +{p25pct.toFixed(1)}%
                                {p50pct != null && <span className="text-gray-500 ml-1">/ +{p50pct.toFixed(1)}%</span>}
                              </>
                            ) : '—'}
                          </td>
                        );
                      })()}
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Shakeout info */}
            {bucketData?.shakeout_depth != null && (
              <div className="mt-1.5 pt-1.5 border-t border-white/[0.04] flex gap-4 text-[11px]">
                <span className="text-gray-500">
                  Shakeout:{' '}
                  <span className={bucketData.shakeout_depth < -5 ? 'text-trading-red-400' : bucketData.shakeout_depth < 0 ? 'text-amber-300' : 'text-gray-400'}>
                    {bucketData.shakeout_depth > 0 ? '+' : ''}{bucketData.shakeout_depth.toFixed(1)}pp
                  </span>
                </span>
                {bucketData.shakeout_recovery && (
                  <span className="text-gray-500">
                    Recovery: <span className="text-gray-300">{bucketData.shakeout_recovery}</span>
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default DteRecommendation;
