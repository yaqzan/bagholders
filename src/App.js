import React, { useState } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Dashboard from './pages/Dashboard';
import StockDetail from './pages/StockDetail';
import Historic from './pages/Historic';
import MarketTrends from './pages/MarketTrends';
import Assessment from './pages/Assessment';
import Backtest from './pages/Backtest';
import VersionCompare from './pages/VersionCompare';
import Allocator from './pages/Allocator';
import Portfolio from './pages/Portfolio';
import PortfolioProfiles from './pages/PortfolioProfiles';
import Queue from './pages/Queue';
import ErrorFallback from './components/ErrorFallback';
import { StockProvider } from './context/StockContext';

// Simple Error Boundary Component
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return <ErrorFallback error={this.state.error} resetErrorBoundary={() => this.setState({ hasError: false })} />;
    }

    return this.props.children;
  }
}

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 1024
  );

  const toggleSidebar = () => {
    setSidebarCollapsed(!sidebarCollapsed);
  };

  return (
    <ErrorBoundary>
      <StockProvider>
        <Router>
          <div className="flex h-[100dvh] box-border overflow-hidden bg-trading-dark-950 pb-[env(safe-area-inset-bottom)]">
            <Sidebar isCollapsed={sidebarCollapsed} onToggle={toggleSidebar} />
            <main className="min-w-0 flex-1 overflow-hidden pt-12 lg:pt-0">
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/stock/:symbol" element={<StockDetail />} />
                <Route path="/historic" element={<Historic />} />
                <Route path="/market-trends" element={<MarketTrends />} />
                <Route path="/assessment" element={<Assessment />} />
                <Route path="/assessment/:tab" element={<Assessment />} />
                <Route path="/allocator" element={<Allocator />} />
                <Route path="/portfolio" element={<Portfolio />} />
                <Route path="/backtest" element={<Backtest />} />
                <Route path="/versions" element={<VersionCompare />} />
                <Route path="/portfolio-profiles" element={<PortfolioProfiles />} />
                <Route path="/queue" element={<Queue />} />
              </Routes>
            </main>
          </div>
        </Router>
      </StockProvider>
    </ErrorBoundary>
  );
}

export default App; 
