import React from 'react';
import ApiTest from '../components/ApiTest';
import StockDebugInfo from '../components/StockDebugInfo';

const Debug = () => {
  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="bg-trading-dark-900 border-b border-trading-dark-800 p-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Debug Console</h1>
            <p className="text-gray-400 mt-1">
              API testing and debugging tools
            </p>
          </div>
        </div>
      </div>

      {/* Debug Content */}
      <div className="flex-1 overflow-auto">
        <div className="p-6 space-y-4">
          <ApiTest />
          <StockDebugInfo />
        </div>
      </div>
    </div>
  );
};

export default Debug; 
