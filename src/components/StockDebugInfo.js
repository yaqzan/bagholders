import React from 'react';
import { useStock } from '../context/StockContext';

const StockDebugInfo = () => {
  const { stocks, getFilteredStocks } = useStock();
  const filteredStocks = getFilteredStocks();

  return (
    <div className="p-4 bg-trading-dark-800 rounded-lg mb-4">
      <h3 className="text-lg font-semibold text-white mb-2">Stock Data Debug Info</h3>
      <div className="text-sm text-gray-300 space-y-1">
        <p className="text-gray-400">Total stocks from API: {stocks.length}</p>
        <p className="text-gray-400">Filtered stocks: {filteredStocks.length}</p>
        <p className="text-gray-400">Stocks with scores: {stocks.filter(s => s.latest_score).length}</p>
      </div>
      
      {stocks[0] && (
        <details className="mt-3">
          <summary className="cursor-pointer text-trading-blue-400 text-sm">Sample Stock Data</summary>
          <pre className="text-xs bg-trading-dark-900 p-2 rounded mt-1 overflow-auto">
            {JSON.stringify(stocks[0], null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
};

export default StockDebugInfo; 
