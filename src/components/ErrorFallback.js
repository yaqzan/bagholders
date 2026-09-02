import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

function ErrorFallback({ error, resetErrorBoundary }) {
  return (
    <div className="min-h-screen bg-trading-dark-950 flex items-center justify-center p-6">
      <div className="max-w-md w-full">
        <div className="relative bg-trading-dark-900 border border-white/[0.06] rounded-md p-6">
          <span
            className="absolute left-0 top-0 bottom-0 w-[2px] bg-trading-red-400"
            aria-hidden="true"
          />
          <div className="flex items-center gap-3 mb-4">
            <AlertTriangle className="w-5 h-5 text-trading-red-400" />
            <h2 className="text-[14px] font-semibold tracking-wide text-gray-100">
              Something went wrong
            </h2>
          </div>

          <p className="text-[13px] leading-relaxed text-gray-400 mb-5">
            An unexpected error occurred. Try reloading the page — if it keeps happening,
            reach out.
          </p>

          {process.env.NODE_ENV === 'development' && (
            <details className="mb-5 text-left">
              <summary className="cursor-pointer text-[11px] uppercase tracking-[0.14em] text-gray-600 hover:text-gray-400 mb-2">
                Error details
              </summary>
              <pre className="text-[11px] text-trading-red-300 bg-trading-dark-950 border border-white/[0.06] p-3 rounded font-mono overflow-auto max-h-48">
                {error?.message}
                {error?.stack}
              </pre>
            </details>
          )}

          <div className="flex gap-2">
            <button
              onClick={resetErrorBoundary}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-[12px] font-medium
                         text-trading-dark-950 bg-trading-green-300 hover:bg-trading-green-200 transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Try again
            </button>
            <button
              onClick={() => (window.location.href = '/')}
              className="inline-flex items-center h-8 px-3 rounded-md text-[12px]
                         text-gray-300 bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] transition-colors"
            >
              Go home
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ErrorFallback;
