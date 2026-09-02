import React, { useState, useEffect, useMemo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Activity, Cpu, Menu, PieChart, Shield, Wallet, X } from 'lucide-react';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.bagholders.ai';

const TAGLINES = [
  'Precision-engineered options signals for maximum portfolio collapse velocity',
  'Quantitatively rigorous pathways to financial humility',
  'Statistically validated routes to early retirement (the wrong kind)',
  'Institutional-grade infrastructure for retail-grade outcomes',
  'Algorithmically optimized for tax-loss harvesting season',
  'Backtested to perfection, deployed to disappointment',
  'Machine-learned signals, hand-delivered losses',
  'Proprietary edge for the rest of your savings',
  'Six-sigma discipline applied to three-sigma drawdowns',
  'Where conviction meets capitulation',
  'Turning your TFSA into a learning experience since 2025',
  'Outperforming SPY in only the most specific time windows',
  'The institutional toolkit your portfolio survived without',
  'Disciplined exits, undisciplined entries',
  'Now with 47% more drawdown',
  'Risk-adjusted returns, unadjusted regret',
  'A data-driven approach to wishing you had bought SPY',
  'The DD your wife\'s boyfriend warned you about',
  'Weaponized confirmation bias as a service',
  'Smarter than r/wallstreetbets, dumber than index funds',
  'Crayons not included',
  'A loss porn generator with extra steps',
  'Theta gang\'s worst nightmare and best customer',
  'Now featuring math',
  'Diamond hands, paper portfolio',
  'Algorithmic conviction for the chronically online',
  'The terminal your broker doesn\'t want you to have (because they make money either way)',
  'Volatility, harnessed responsibly (terms and conditions apply)',
  'A disciplined framework for undisciplined behavior',
  'Empirically validated, emotionally devastating',
  'Rigorous methodology. Inevitable outcome.',
  'For traders who read the prospectus and ignored it anyway',
  'It backtests great',
  'The numbers were higher in simulation',
  'Past performance is the only thing we have',
  'Your edge, until it isn\'t',
  'Sharpe ratio sold separately',
  'Survivorship bias, professionally applied',
  'One backtest away from being right',
  'Your money, our hypotheses',
  'Compounding losses since first deployment',
  'The strategy your therapist will hear about',
  'Eventually correct',
  'Built for the chart, broken by the tape',
  'Powered by hopium, deployed on margin',
  'The leading cause of "we need to talk"',
  'Your spouse will find out eventually',
  'Joint accounts, single regrets',
  'Cited in 73% of recent divorce filings',
  'The conversation you\'re rehearsing in the shower',
  'Statistically correlated with sleeping on the couch',
  'Where marriages come to be stress-tested',
  'Chapter 7 as a feature, not a bug',
  'The fastest path from solvency to forbearance',
  'Now accepting credit cards (you\'ll need them)',
  'A rigorous framework for negative net worth',
  'Licensed Insolvency Trustee referral program available',
  'Turning equity into liability with mathematical precision',
  'Compounding interest, decompounding savings',
  'Your collection agency\'s favorite analytics platform',
  'Margin calls answered with characteristic optimism',
  'Your broker\'s most profitable customer',
  'Liquidation, automated for your convenience',
  'The reason your account got flagged as "pattern day loser"',
  'Wealthsimple\'s employee of the month',
  'Where good collateral goes to die',
  'Pioneering new lows in account equity since launch',
  'Generating capital losses the CRA will reluctantly acknowledge in 2032',
  'Your TFSA contribution room, redefined as a regret quota',
  'Turning your RRSP into a cautionary tale',
  'The CRA already received your T5008 and they\'re concerned',
  'Loss carryforwards as a long-term financial planning tool',
  'Your accountant has questions about box 21',
  'Schedule 3 will not fit in the margin this year',
  'Now generating superficial loss violations at scale',
  'Your TFSA is no longer a tax shelter, it\'s a memorial',
  'Engineered to test the limits of capital loss carryback (3 years isn\'t enough)',
  'Filing season just got more interesting',
  'The CRA\'s most studied retail trader',
  'A disciplined approach to wasting RRSP contribution room',
  'Your FHSA was supposed to buy a house, not lottery tickets',
  'Day-trader reassessment letter incoming',
  'Adjusted cost base: complicated. Outcome: simple.',
  'Your portfolio\'s going to zero, but slowly',
  'A statistically significant decline in life satisfaction',
  'The terminal you\'ll remember during the layoffs',
  'Compounding regret since deployment',
  'Where ambition goes to be quantitatively measured',
  'Disappointment, but make it data-driven',
  'The market is efficient. You are not.',
  'Now accepting your last $5,000 with the same enthusiasm as your first',
  'Inevitability, dressed up as edge',
  'Your future self is already disappointed',
  'Drawdowns are just unrealized opportunities',
  'Down bad, but methodically',
  'The strategy is fine, the market is wrong',
  'Holding strong (out of options)',
  'Statistically, you will eventually be right',
  'Conviction, validated by the inability to sell',
  'Loss porn at industrial scale',
  'Engineered by autists, deployed by degenerates',
  'The DD that put your DD to shame',
  'Your wife\'s boyfriend uses it too (he\'s profitable)',
  'Smooth-brained efficiency',
  'Crayons sold separately, available in tax-deductible boxes',
  '0DTE not as a strategy but as a worldview',
  'The terminal r/wallstreetbets deserves but won\'t appreciate',
  'Built different (broker-flagged different)',
  'Returns simulated, losses authentic',
  'Backtested on data that is no longer relevant',
  'For accredited fools and qualified disasters only',
  'Not CDIC insured, not bank guaranteed, may lose value (will)',
  'Side effects include: insomnia, irritability, refreshing the chart at 3am',
  'Use as directed by no one in particular',
  'This is not financial advice (it is, however, a financial decision)',
  'The terminal you\'ll be using when your friends stop returning your calls',
  'A reminder that every chart eventually goes left to right',
  'Your equity curve, but make it autobiographical',
  'Where hope becomes data',
  'Calibrated to the precise pace of your dwindling enthusiasm',
  'The exact velocity at which optimism becomes cope',
  'It gets worse',
  'And then it gets worse',
  'The losses are the point',
  'Eventually flat',
  'Built to lose efficiently',
  'A long-term hold (no choice)',
  'Down only',
];

/* Inline brand mark — matches public/favicon.svg.
   Terminal window, sage green, >$ with retro highlighted cursor block on $. */
const BrandMark = ({ className = 'w-5 h-5' }) => (
  <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
    <rect
      x="4" y="6" width="24" height="20" rx="2.2"
      stroke="#A7C4A0"
      strokeOpacity="0.55"
      strokeWidth="1.4"
      fill="none"
    />
    <line
      x1="4" y1="11.2" x2="28" y2="11.2"
      stroke="#A7C4A0"
      strokeOpacity="0.45"
      strokeWidth="1.4"
    />
    <circle cx="7"   cy="8.6" r="0.75" fill="#A7C4A0" fillOpacity="0.5" />
    <circle cx="9.5" cy="8.6" r="0.75" fill="#A7C4A0" fillOpacity="0.5" />
    <circle cx="12"  cy="8.6" r="0.75" fill="#A7C4A0" fillOpacity="0.5" />
    <path
      d="M8.5 15.5 L11.5 18.5 L8.5 21.5"
      stroke="#A7C4A0"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
    {/* Highlighted character block (retro terminal cursor over $) */}
    <rect x="13.5" y="13" width="8" height="10" fill="#A7C4A0" />
    {/* $ inverted: dark text on green block */}
    <text
      x="17.5" y="21.5"
      textAnchor="middle"
      fontFamily="'Courier New', Courier, monospace"
      fontSize="10"
      fontWeight="bold"
      fill="#0A0A0B"
    >$</text>
  </svg>
);

/* Minimal icons — hand-tuned stroke weights for this design. */
const icons = {
  dashboard: (
    <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="5" height="6" rx="1" />
      <rect x="9" y="2" width="5" height="9" rx="1" />
      <rect x="2" y="10" width="5" height="4" rx="1" />
      <rect x="9" y="13" width="5" height="1" rx="0.5" />
    </svg>
  ),
  historic: (
    <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5 V8 L10.5 10" />
    </svg>
  ),
  assessment: (
    <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 13 L6 8 L9 10.5 L14 4" />
      <path d="M11 4 H14 V7" />
    </svg>
  ),
  backtest: (
    <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="12" height="10" rx="1.2" />
      <path d="M5 7 L7.5 9.5 L11 5.5" />
    </svg>
  ),
  versions: (
    <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12 V4" />
      <path d="M8 12 V3" />
      <path d="M13 12 V6" />
      <path d="M2 12.5 H14" />
      <path d="M3 7 H8" />
      <path d="M8 9 H13" />
    </svg>
  ),
  marketTrends: <Activity className="w-4 h-4" />,
  allocator: <PieChart className="w-4 h-4" />,
  portfolio: <Wallet className="w-4 h-4" />,
  profiles: <Shield className="w-4 h-4" />,
  queue: <Cpu className="w-4 h-4" />,
};

const Sidebar = ({ isCollapsed, onToggle }) => {
  const location = useLocation();
  const [apiConnected, setApiConnected] = useState(null);
  const tagline = useMemo(() => TAGLINES[Math.floor(Math.random() * TAGLINES.length)], []);

  useEffect(() => {
    const checkPing = async () => {
      try {
        // 10s tolerance: update/recalc runs can starve the API for a few seconds —
        // that's load, not an outage (the badge was false-flagging OFFLINE at 5s).
        const res = await fetch(`${API_BASE_URL}/api/ping`, { signal: AbortSignal.timeout(10000) });
        setApiConnected(res.ok);
      } catch {
        setApiConnected(false);
      }
    };
    checkPing();
    const interval = setInterval(checkPing, 30000);
    // Re-check immediately when the tab becomes visible again
    const onVisible = () => { if (document.visibilityState === 'visible') checkPing(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  const navItems = [
    { name: 'Dashboard', icon: icons.dashboard,  path: '/' },
    // Allocator = the primary execution surface (entry-timing canon 2026-06-11)
    { name: 'Allocator', icon: icons.allocator, path: '/allocator' },
    { name: 'Historic',  icon: icons.historic,   path: '/historic' },
    { name: 'Market Trends', icon: icons.marketTrends, path: '/market-trends' },
    { name: 'Assessment',icon: icons.assessment, path: '/assessment' },
    // { name: 'Portfolio', icon: icons.portfolio, path: '/portfolio' },  // hidden 2026-06-22 (route + page kept; still reachable directly at /portfolio)
    { name: 'Profiles',  icon: icons.profiles,   path: '/portfolio-profiles' },
    { name: 'Backtest',  icon: icons.backtest,   path: '/backtest' },
    { name: 'Versions',  icon: icons.versions,   path: '/versions' },
    { name: 'Task Queue', icon: icons.queue, path: '/queue' },
  ];

  return (
    <>
      {/* Mobile overlay */}
      {!isCollapsed && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden"
          onClick={onToggle}
        />
      )}

      {/* Floating hamburger — mobile only */}
      {isCollapsed && (
        <button
          onClick={onToggle}
          aria-label="Open menu"
          className="fixed top-3 left-3 z-40 lg:hidden p-2 bg-trading-dark-900/90 border border-white/10 hover:bg-trading-dark-800 rounded-md backdrop-blur-sm transition-colors"
        >
          <Menu className="w-4 h-4 text-gray-300" />
        </button>
      )}

      <aside
        className={`
          fixed lg:static inset-y-0 left-0 z-50
          bg-trading-dark-950 border-r border-white/[0.06]
          flex flex-col transition-[width,transform] duration-200 ease-out
          ${isCollapsed ? 'w-14' : 'w-60'}
          ${!isCollapsed ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        `}
      >
        {/* Brand lockup */}
        <div className="px-4 pt-5 pb-4 border-b border-white/[0.06]">
          {isCollapsed ? (
            <button
              onClick={onToggle}
              className="flex items-center justify-center w-full py-1.5 text-gray-500 hover:text-gray-200 transition-colors"
              aria-label="Expand sidebar"
            >
              <BrandMark className="w-5 h-5" />
            </button>
          ) : (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex items-start gap-2.5">
                <BrandMark className="w-[26px] h-[26px] mt-[1px] flex-shrink-0 text-gray-400" />
                <div className="min-w-0">
                  <h1 className="uppercase leading-none">
                    <span className="block text-[15px] font-black tracking-[0.02em] text-gray-100">
                      Bagholder
                    </span>
                    <span className="block text-[10px] font-semibold tracking-[0.38em] text-trading-green-300 mt-[6px] pl-[1px]">
                      Terminal
                    </span>
                  </h1>
                  <p className="text-[11px] italic text-gray-500 leading-snug mt-2">
                    {tagline}
                  </p>
                </div>
              </div>
              <button
                onClick={onToggle}
                className="p-1 -m-1 text-gray-600 hover:text-gray-300 transition-colors flex-shrink-0"
                aria-label="Collapse sidebar"
              >
                <X className="w-4 h-4 lg:hidden" />
                <Menu className="w-4 h-4 hidden lg:block" />
              </button>
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 px-2">
          {!isCollapsed && (
            <div className="px-2 pb-2 text-[10px] font-medium tracking-[0.14em] uppercase text-gray-600">
              Workspace
            </div>
          )}
          <div className="space-y-0.5">
            {navItems.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.name}
                  to={item.path}
                  onClick={() => { if (window.innerWidth < 1024) onToggle(); }}
                  className={`
                    relative flex items-center rounded-md transition-colors
                    ${isCollapsed ? 'justify-center py-2.5' : 'gap-3 px-3 py-2'}
                    ${isActive
                      ? 'text-gray-100 bg-white/[0.04]'
                      : 'text-gray-500 hover:text-gray-200 hover:bg-white/[0.02]'}
                  `}
                >
                  {isActive && (
                    <span
                      className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-trading-green-300"
                      aria-hidden="true"
                    />
                  )}
                  <span className={`flex-shrink-0 ${isActive ? 'text-trading-green-300' : ''}`}>
                    {item.icon}
                  </span>
                  {!isCollapsed && (
                    <span className={`text-[13px] leading-none ${isActive ? 'font-medium' : 'font-normal'}`}>
                      {item.name}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        </nav>

        {/* Footer — status + copyright */}
        <div className="px-4 py-3 border-t border-white/[0.06]">
          <div className={`flex items-center ${isCollapsed ? 'justify-center' : 'justify-between'}`}>
            <div className="flex items-center gap-2">
              <span
                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                  apiConnected === null
                    ? 'bg-gray-600'
                    : apiConnected
                      ? 'bg-trading-green-400'
                      : 'bg-trading-red-400'
                }`}
                aria-hidden="true"
              />
              {!isCollapsed && (
                <span className="text-[10px] tracking-wider uppercase text-gray-500">
                  {apiConnected === null ? 'Connecting' : apiConnected ? 'Live' : 'Offline'}
                </span>
              )}
            </div>
          </div>
          {!isCollapsed && (
            <p className="text-[10px] text-gray-700 tracking-wide leading-tight mt-2">
              © {new Date().getFullYear()} Bagholders Capital LLC
            </p>
          )}
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
