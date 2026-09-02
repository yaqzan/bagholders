import React from 'react';
import { Search, X } from 'lucide-react';
import { useStock } from '../context/StockContext';

/* Pill — subtle tinted fill + colored border when active; muted gray when not. */
const Pill = ({ active, onClick, accent = 'sage', children, ariaLabel }) => {
  const activeClass = {
    sage:  'text-trading-green-300 bg-trading-green-500/[0.08] border-trading-green-500/25',
    coral: 'text-trading-red-300 bg-trading-red-500/[0.08] border-trading-red-500/25',
    amber: 'text-amber-300 bg-amber-500/[0.08] border-amber-500/25',
  }[accent] || 'text-trading-green-300 bg-trading-green-500/[0.08] border-trading-green-500/25';

  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      aria-label={ariaLabel}
      className={`
        inline-flex items-center gap-1.5
        h-7 px-3 rounded-md text-[11px] font-medium tracking-wide
        border transition-colors
        ${active
          ? activeClass
          : 'text-gray-500 bg-transparent border-white/[0.06] hover:text-gray-300 hover:border-white/[0.12]'}
      `}
    >
      {children}
    </button>
  );
};

const FilterBar = ({ rightControl = null }) => {
  const { filters, setFilters, stocks } = useStock();

  const handleSearchChange = (e) => setFilters({ search: e.target.value });
  const clearSearch = () => setFilters({ search: '' });

  return (
    <div className="bg-trading-dark-950 border-b border-white/[0.06] px-3 sm:px-4 py-2">
      <div className="flex items-center gap-1.5 sm:gap-3 min-w-0">
        <div className="flex items-center gap-1 sm:gap-1.5 flex-shrink-0">
          <Pill
            active={filters.callsOnly}
            onClick={() => setFilters({ callsOnly: !filters.callsOnly })}
            accent="sage"
            ariaLabel="Toggle call signals"
          >
            Calls
          </Pill>

          <Pill
            active={filters.putsOnly}
            onClick={() => setFilters({ putsOnly: !filters.putsOnly })}
            accent="coral"
            ariaLabel="Toggle put signals"
          >
            Puts
          </Pill>

          <Pill
            active={filters.flaggedOnly}
            onClick={() => setFilters({ flaggedOnly: !filters.flaggedOnly })}
            accent="amber"
            ariaLabel="Toggle flagged only"
          >
            Flagged
          </Pill>
        </div>

        {/* Search */}
        <div className="flex-1 min-w-[72px]">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-600" />
            <input
              type="text"
              placeholder="Search"
              value={filters.search}
              onChange={handleSearchChange}
              className="w-full h-7 sm:h-8 pl-8 pr-7 bg-transparent border border-white/[0.08]
                         rounded-md text-[12px] text-gray-100 placeholder:text-gray-600
                         focus:outline-none focus:border-white/20 transition-colors"
            />
            {filters.search && (
              <button
                onClick={clearSearch}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
          {rightControl}
          <span className="hidden sm:inline text-[11px] text-gray-600 font-mono tabular-nums">
            {stocks.length}
          </span>
        </div>
      </div>
    </div>
  );
};

export default FilterBar;
