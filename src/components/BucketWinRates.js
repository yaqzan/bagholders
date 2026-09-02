import React, { useEffect, useState } from 'react';
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';
const buildApiUrl = (endpoint) => `${API_BASE_URL}/api${endpoint}`;

function wrColor(v) {
  if (v == null) return 'text-gray-600';
  if (v >= 75) return 'text-trading-green-300';
  if (v >= 65) return 'text-trading-green-400';
  if (v >= 55) return 'text-amber-300';
  if (v >= 45) return 'text-amber-400';
  return 'text-trading-red-400';
}

const BUCKET_ORDER = ['95+', '90-94', '85-89', '80-84', '75-79',
                      '21-25', '16-20', '11-15', '6-10', '<5'];

const BucketWinRates = ({ symbol }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    axios
      .get(buildApiUrl(`/stocks/${symbol}/bucket-winrates?days=365`))
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Failed to load');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const wrap = (content) => (
    <div className="mx-3 mt-3 mb-2 px-3 py-1.5 bg-trading-dark-900 border border-white/[0.06] rounded-md text-[11px] flex items-center gap-3 whitespace-nowrap overflow-x-auto">
      {content}
    </div>
  );

  if (loading) return wrap(<span className="text-gray-500">Loading 1y bucket win rates…</span>);
  if (error)   return wrap(<span className="text-trading-red-400">Bucket win rates: {error}</span>);

  const buckets = data?.buckets || [];
  if (buckets.length === 0) {
    return wrap(
      <span className="text-gray-500">
        No qualifying signals (score ≥75 / ≤25) in the last year for {symbol}.
      </span>
    );
  }

  // Order by canonical bucket sequence (calls high→low, puts shallow→deep)
  const ordered = [...buckets].sort(
    (a, b) => BUCKET_ORDER.indexOf(a.bucket) - BUCKET_ORDER.indexOf(b.bucket)
  );

  // Pick the best WR window for this bucket — prefer 15d (primary target),
  // fall back to 30d then 7d. Show N for that window.
  const pickCell = (b) => {
    for (const p of ['15d', '30d', '7d']) {
      const wr = b[`wr_${p}`];
      const n = b[`n_${p}`];
      if (wr != null) return { wr, n, p };
    }
    return null;
  };

  return wrap(
    <>
      <span className="text-[10px] uppercase tracking-[0.16em] text-gray-400 font-medium shrink-0">
        1y WR · {symbol}
      </span>
      {ordered.map((b) => {
        const cell = pickCell(b);
        if (!cell) return null;
        const sideColor = b.side === 'call' ? 'text-trading-green-300' : 'text-trading-red-300';
        return (
          <span key={b.bucket} className="flex items-baseline gap-1 font-mono tabular-nums shrink-0">
            <span className={`text-[10px] ${sideColor}`}>{b.bucket}</span>
            <span className={wrColor(cell.wr)}>{cell.wr.toFixed(0)}%</span>
            <span className="text-gray-600 text-[10px]">n={cell.n}</span>
          </span>
        );
      })}
      <span className="text-[10px] text-gray-600 ml-auto shrink-0">
        15d · {data.signal_count} sig
      </span>
    </>
  );
};

export default BucketWinRates;
