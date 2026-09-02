import React, { useState, useEffect } from 'react';
import axios from 'axios';

// API base URL - can be configured
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';

// Determine if we're running locally or in production
const isLocalDevelopment = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

// API path prefix - local development uses /api/, production uses no prefix
const API_PATH_PREFIX = '/api'

// Helper function to build API URLs
const buildApiUrl = (endpoint) => {
  return `${API_BASE_URL}${API_PATH_PREFIX}${endpoint}`;
};

const ApiTest = () => {
  const [apiStatus, setApiStatus] = useState('Testing...');
  const [response, setResponse] = useState(null);
  const [error, setError] = useState(null);
  
  // Trends endpoint testing
  const [trendsResults, setTrendsResults] = useState({});
  const [testingTrends, setTestingTrends] = useState(false);

  useEffect(() => {
    const testApi = async () => {
      try {
        setApiStatus('Testing API connection...');
        const response = await axios.get(buildApiUrl('/analysis/top-stocks?limit=10'));
        setResponse(response.data);
        setApiStatus('API Connected Successfully');
        console.log('API Response:', response.data);
      } catch (err) {
        setError(err.message);
        setApiStatus('API Connection Failed');
        console.error('API Error:', err);
      }
    };

    testApi();
  }, []);

  // Test individual trends endpoints with timing
  const testTrendsEndpoint = async (endpoint, name) => {
    const startTime = Date.now();
    try {
      console.log(`Testing ${name}...`);
      const response = await axios.get(buildApiUrl(endpoint), { timeout: 30000 });
      const endTime = Date.now();
      const duration = endTime - startTime;
      
      setTrendsResults(prev => ({
        ...prev,
        [name]: {
          status: 'success',
          duration: duration,
          data: response.data,
          timestamp: new Date().toLocaleTimeString()
        }
      }));
      
      console.log(`${name} completed in ${duration}ms`);
    } catch (err) {
      const endTime = Date.now();
      const duration = endTime - startTime;
      
      setTrendsResults(prev => ({
        ...prev,
        [name]: {
          status: 'error',
          duration: duration,
          error: err.message,
          timestamp: new Date().toLocaleTimeString()
        }
      }));
      
      console.error(`${name} failed after ${duration}ms:`, err.message);
    }
  };

  const testAllTrendsEndpoints = async () => {
    setTestingTrends(true);
    setTrendsResults({});
    
    // Test each endpoint individually
    await testTrendsEndpoint('/market/trends?days=90', 'Market Trends (90 days)');
    await testTrendsEndpoint('/stocks/SPY/price-history?limit=90', 'SPY Price History (90 days)');
    await testTrendsEndpoint('/stocks/QQQ/price-history?limit=90', 'QQQ Price History (90 days)');
    
    setTestingTrends(false);
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'success': return 'text-trading-green-400';
      case 'error': return 'text-trading-red-400';
      default: return 'text-gray-400';
    }
  };

  const getDurationColor = (duration) => {
    if (duration < 5000) return 'text-trading-green-400';
    if (duration < 15000) return 'text-yellow-400';
    return 'text-trading-red-400';
  };

  return (
    <div className="space-y-6">
      {/* Basic API Test */}
      <div className="p-4 bg-trading-dark-800 rounded-lg">
        <h3 className="text-lg font-semibold text-white mb-2">API Connection Test</h3>
        <p className="text-sm text-gray-300 mb-2">Status: {apiStatus}</p>
        
        {error && (
          <div className="text-trading-red-400 text-sm mb-2">
            Error: {error}
          </div>
        )}
        
        {response && (
          <div className="text-sm text-gray-300">
            <p>Response type: {typeof response}</p>
            <p>Is Array: {Array.isArray(response) ? 'Yes' : 'No'}</p>
            <p>Length: {Array.isArray(response) ? response.length : 'N/A'}</p>
            <details className="mt-2">
              <summary className="cursor-pointer text-trading-blue-400">View Response</summary>
              <pre className="text-xs bg-trading-dark-900 p-2 rounded mt-1 overflow-auto">
                {JSON.stringify(response, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </div>

      {/* Trends Endpoints Test */}
      <div className="p-4 bg-trading-dark-800 rounded-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-white">Trends Endpoints Performance Test</h3>
          <button
            onClick={testAllTrendsEndpoints}
            disabled={testingTrends}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              testingTrends
                ? 'bg-gray-600 text-gray-400 cursor-not-allowed'
                : 'bg-trading-blue-600 text-white hover:bg-trading-blue-700'
            }`}
          >
            {testingTrends ? 'Testing...' : 'Test All Endpoints'}
          </button>
        </div>
        
        <p className="text-sm text-gray-400 mb-4">
          Test individual trends endpoints to identify performance bottlenecks
        </p>

        {/* Results */}
        <div className="space-y-3">
          {Object.entries(trendsResults).map(([name, result]) => (
            <div key={name} className="p-3 bg-trading-dark-900 rounded-lg border border-trading-dark-700">
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-medium text-white">{name}</h4>
                <div className="flex items-center space-x-3">
                  <span className={`text-sm font-medium ${getStatusColor(result.status)}`}>
                    {result.status === 'success' ? '✓ Success' : '✗ Error'}
                  </span>
                  <span className={`text-sm font-mono ${getDurationColor(result.duration)}`}>
                    {result.duration}ms
                  </span>
                  <span className="text-xs text-gray-500">{result.timestamp}</span>
                </div>
              </div>
              
              {result.status === 'error' && (
                <div className="text-trading-red-400 text-sm mb-2">
                  Error: {result.error}
                </div>
              )}
              
              {result.status === 'success' && result.data && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-trading-blue-400 text-sm">
                    View Data ({result.data.count || 'N/A'} items)
                  </summary>
                  <pre className="text-xs bg-trading-dark-950 p-2 rounded mt-1 overflow-auto max-h-40">
                    {JSON.stringify(result.data, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          ))}
        </div>

        {/* Individual Test Buttons */}
        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-2">
          <button
            onClick={() => testTrendsEndpoint('/market/trends?days=90', 'Market Trends (90 days)')}
            disabled={testingTrends}
            className="px-3 py-2 bg-trading-dark-700 text-white rounded text-sm hover:bg-trading-dark-600 disabled:opacity-50"
          >
            Test Market Trends
          </button>
          <button
            onClick={() => testTrendsEndpoint('/stocks/SPY/price-history?limit=90', 'SPY Price History (90 days)')}
            disabled={testingTrends}
            className="px-3 py-2 bg-trading-dark-700 text-white rounded text-sm hover:bg-trading-dark-600 disabled:opacity-50"
          >
            Test SPY Data
          </button>
          <button
            onClick={() => testTrendsEndpoint('/stocks/QQQ/price-history?limit=90', 'QQQ Price History (90 days)')}
            disabled={testingTrends}
            className="px-3 py-2 bg-trading-dark-700 text-white rounded text-sm hover:bg-trading-dark-600 disabled:opacity-50"
          >
            Test QQQ Data
          </button>
        </div>

        {/* Performance Guidelines */}
        <div className="mt-4 p-3 bg-trading-dark-900 rounded-lg border border-trading-dark-700">
          <h4 className="text-sm font-medium text-white mb-2">Performance Guidelines</h4>
          <div className="text-xs text-gray-400 space-y-1">
            <div className="flex items-center space-x-2">
              <span className="text-trading-green-400">●</span>
              <span>&lt; 5s: Good</span>
            </div>
            <div className="flex items-center space-x-2">
              <span className="text-yellow-400">●</span>
              <span>5-15s: Acceptable</span>
            </div>
            <div className="flex items-center space-x-2">
              <span className="text-trading-red-400">●</span>
              <span>&gt; 15s: Needs optimization</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ApiTest; 
