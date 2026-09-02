# Frontend — React Component Details

## Component Map

```
src/
├── context/StockContext.js     # Global state, API calls, filters
├── pages/Dashboard.js          # Stats, ScoreVersionSelector, FilterBar, EarningsCalendar, StockTable
├── pages/StockDetail.js        # Per-stock analysis, tabbed (daily/weekly)
├── pages/Assessment.js         # Backtest results, DTE toggle
├── pages/Historic.js           # Peak signal events, roll-up/re-entry pills
├── pages/MarketTrends.js       # Regime + breadth time-series charts
├── pages/Backtest.js           # Deterministic backtest runner
├── pages/Allocator.js          # Portfolio allocation table (symbol/score/effective/scale), version-selectable
├── pages/Portfolio.js          # Live v70-Apex portfolio tracker (holdings pie + growth chart + open/closed tables)
├── pages/PortfolioProfiles.js  # Sentinel/Core/Apex profile metrics & comparison
├── pages/VersionCompare.js     # Cross-version score / assessment / portfolio comparison
├── pages/Debug.js              # Debug console / diagnostics
└── components/
    ├── Sidebar.js               # Left nav: Dashboard → Historic → Assessment
    ├── StockTable.js            # Sortable table + mobile cards
    ├── ScoreBadge.js            # Score pill styling
    ├── DteRecommendation.js     # Thesis + DTE panel
    ├── PriceChart.js            # Candlestick + BB + Volume + TP/SL overlays
    └── *Chart.js               # RSI, MACD, BB, Trend, Weekly charts
```

---

## Dashboard.js

**Default sort** uses `default_sort_value` from API, tiered to match cascade fill order:
- Calls ≥75 → `200 + overall` (275-300, top tier)
- Puts ≤25 → `200 − overall` (175-200, second tier)
- Neutral → distance from 50 (≤74, bottom tier)

A score-75 call (sort value 275) always outranks the deepest put (~175-199).

`MarketPanel`/`MobileMarketStrip` use `useStrategyCfg30()` (module-level promise cache); `getStrategyTips(opt)` templates tooltips from `/api/strategy/config`.

---

## StockDetail.js

- Highest score in last 60 days: calls → MAX score (≥70), puts → MIN score (≤25), ties broken by distance from 50.
- Passes `signalDate + assessmentData` to PriceChart. Tabbed daily/weekly.

---

## Assessment.js

**12 buckets (symmetric 6/6):** Calls 95+/90+/85+/80+/75+/70+; Puts <30/<25/<20/<15/<10/<5.

**30/15 DTE toggle:** persists in `localStorage`, re-fetches `/api/assessment?dte=15` + `/api/backtest/temporal?dte=15`. Controls which periods highlight (30 DTE: WR15+WR30; 15 DTE: WR7+WR15) and the "best bucket" period.

**Window selector** (All/1y/3y/5y) in Win Rates toolbar. **Scaled/Unscaled toggle** shows barrier-touch WR with/without √(W/30) scaling.

**Portfolio profile toggle:** Calendar tab has Sentinel/Core/Apex selection, colors are risk semantics only (Sentinel green, Core yellow, Apex red). Sent as `/api/backtest/temporal?version=vNN&profile=<key>`, applies only to portfolio-stage backtest stats, not `Score.overall`.

**Calendar stress windows:** reads `portfolio_windows.windows` from the same endpoint above the year/month tables. Windows: Mar 2020 crash, 2020-2021, 2020-now, 22-now. Table: return, max DD, trades, TP%, call/put counts, source, active-version deltas. Source `pack` = exact research-pack stress metrics; `monthly` = only monthly temporal rows, DD unavailable.

---

## Historic.js

Slider defaults 60d, max 1yr. Pill filters: Calls/Puts/Flagged.

Per-period delta sub-row: `fwd_peak_delta_{p}` gain vs prior period; sky-glow on best-delta cell.

**Roll-up pill (↗ WR%):** shows when prior window won and next is still open (calls + puts). **Re-entry pill (↺ WR%):** shows when prior window failed and next is still open (calls only; puts 96-99% stopped). Pill renders left of the delta pct in the active-window cell.

`reentry` tags need `days_ago` bounds to avoid stale-data false positives:

| Active period | Pill | Condition |
|---|---|---|
| 15d | rollup7 | `win_7d === 1 && win_15d === null` |
| 15d | reentry7d | `win_7d === 0 && win_15d === null && days_ago ∈ [7,15)` |
| 30d | rollup15 | `win_15d === 1 && win_30d === null` |
| 30d | reentry15d | `win_15d === 0 && win_30d === null && days_ago ∈ [15,30)` |
| 60d | rollup30 | `win_30d === 1 && win_60d === null` |
| 60d | reentry30d | `win_30d === 0 && win_60d === null && days_ago ∈ [30,60)` |
| 90d | rollup60 | `win_60d === 1 && win_90d === null` |
| 90d | reentry60d | `win_60d === 0 && win_90d === null && days_ago ∈ [60,90)` |

`isReentry` on the peak object drives the "Flagged" filter.

---

## ScoreBadge.js

- Active (colored + glow): calls ≥70, puts ≤25. Quiet (neutral): 26-69. Puts 16-25 = `weak`, ≤15 = `veryWeak` (mirrors 75/80 call split).
- Put/call symmetry restored 2026-04-17 (dashboard sort, badge styling, assessment bucket granularity) after asymmetric weekly 1.5x-puts and asymmetric MACD gate shipped in v18.

---

## PriceChart.js

Projects TP/SL from highest score in last 60 days. Entry price = close on signal date.

```
TP (calls) = entry × (1 + mfe_sigma_p25 × √(days/30) × realized_vol / 100)
SL         = entry × (1 ± abs(mae_sigma × √(days/30) × realized_vol) / 100)
```

Projection lines: dashed horizontal at 7d/15d/30d/60d DTEs, opacity 0.35/0.25/0.18/0.10. Signal dot: green=call, red=put, at signal date + close price. Render order: lines → price labels → signal dot (top).

---

## Backtest.js

**30/15 DTE toggle** adds `dte=15` to run query; `DEFAULT_ADVANCED`+`FIELD_TIPS` fetch `/api/strategy/config` on mount and dte toggle.

**Portfolio profile toggle:** sends `profile=sentinel|core|apex` to `/api/backtest/run`, updates advanced knobs from `/api/portfolio/profiles/compare`. Saved runs persist `profile` in `params_json`.

## Allocator.js (`/allocator` — primary execution surface since 2026-06-12)

Per the Execution Timing Canon ([trading-strategy.md](trading-strategy.md)). Sidebar lists second, under Dashboard (`/`; `/dashboard` aliased — user preference 2026-06-12).

- **Execution-window banner** (`GET /api/portfolio/pending`, 120s poll + 30s tick): `closed` (final; next window date) · `pre_open` (carry-over fills at open, ~−1.3pp vs model close fill) · `provisional` (amber, partial-day scores, ~26% of morning signals fade, countdown to 15:25 ET) · `window` (green, 15:25-16:00 ET, near-final, buy now).
- **Pending actions card**: *Sell now* (barrier touches/sweeps, est P&L), *Buy now/Forming* (fills at today's close if signals hold, labeled provisional outside window), *Carry-over* (filled at last completed close).
- Live form sends `profile=sentinel|core|apex` to `/api/allocation/live`, same profile toggle as Assessment/Backtest.

---

## Portfolio.js (`/portfolio`)

Persisted realization of the **v70 Apex** strategy, live counterpart to deterministic Backtest. Started **2026-06-01** with 50,000 CAD; **2026-06-05** go-live (Jun 1-4 silent history). Blends Allocator (holdings pie) + Backtest (growth line).

- CAD/USD total toggle (default CAD; individual positions always USD). CAD pinned to fixed inception FX (`cad_per_usd`) so the curve shows strategy growth, not FX drift.
- Holdings pie: one slice/open position by USD value, size-gradient colored (heaviest=dark green→pale; red ramp for puts) + cash slice, multi-column legend. Same `sizeShade()` gradient on Allocator pie.
- Growth chart: daily MTM equity (LIN/LOG toggle) from `equity_curve_mtm`.
- Open Positions/Closed Trades: Backtest-styled tables. Open emphasizes contract (SYMBOL→strike→expiry→side) + Days-Held; closed mirrors Backtest log (TP/SL/HARD dots, colored reason).
- `GET /api/portfolio/state` (lazy re-sync if stale; `?sync=1` forces fresh re-eval).

### Portfolio backend (engine + models + notifications)

Persisted forward ledger: realized history never rewritten, no queued/pending-order state.

- **`portfolio_engine.py`** — each `trader update` calls `run_update()` → `sync()` → `_advance(run, D)`, a stateful forward simulator (not stateless re-derive): carries cash + open positions forward to the last completed session `D`. Closed trades + past equity are frozen. Per session: (1) resolves open positions under CURRENT strategy barriers via `compute_outcome` (dead-hold/fills/vega exact; a strategy change = adopt-new-barriers), (2) opens new entries via a faithful cascade port (tier × F3F × DD-soft × RXDD × SVR × MWDD × TVDD × BDIV × saturation × caps — all five Stage-3 call-alloc dampeners since 2026-06-11; SVR = `compute_semivol_r`; MWDD/TVDD/BDIV per-date maps loaded once/sync; cost-basis equity drives sizing, MTM drives snapshot curve). Validated bit-exact against `run_cascade_backtest` for a single-version window (re-validated 2026-06-11 with dampeners; harness `experiments/portfolio_engine_parity/validate.py`).
- **Version/strategy adaptation** — `_advance` fingerprints active `version_id` + Apex config hash (`_strategy_fingerprint`). On change, runs a re-qualification sweep: each open position's entry-date score is re-checked under the new version; if it no longer clears the cascade threshold (`_min_call_threshold`), the position exits at session close tagged `version_sweep`/`strategy_sweep` + close notification. Survivors ride; new entries use new version/strategy. Automatic.
- **Market hours:** `last_completed_session` finalizes today only after 16:00 ET (else prior day) so entries use that session's finalized close score.
- **Models** (`database/models/portfolio.py`): `portfolio_runs` (start/capital/FX/`version_id`/`strategy_fingerprint`/cash/last-processed), `portfolio_positions` (per-option open&closed; `entry_version_id`, `stressed`, `premium_mult`/`delta`, `dead_hold_active`, `last_open_date`, marks, P/L, notification flags, `sweep_pending`/`sweep_reason`/`sweep_action_date`), `portfolio_equity_snapshots` (daily MTM), `portfolio_pending_alerts` (de-dup ledger for live pushes).
- **API**: `GET /api/portfolio/state`, `GET|POST /api/portfolio/sync` (both `no-store`). **CLI**: `trader portfolio [sync|reset|status|pending]` (`reset` wipes+reseeds from start date; `pending` dry-run prints would-be live alerts/closes/opens/sweeps).
- **Notifications replaced 2026-06-11 (`0ecfa141f`)**: the engine is sole source of update-time Pushover messages.
  - **Live action alerts** fire on every `trader update` during market hours/pre-open, ahead of close-of-session actioning: `🔴 Sell SYM now|at the open` on current-bar barrier fires or sweep-pending; `🟢 Buy SYM` provisional entries via same cascade path (`⚪ cancelled` if fades by close); `🔔 N buys at the open` morning digest at 9:00 pre-open run for carry-over entries. De-duped for life via `portfolio_pending_alerts`; post-close confirms suppressed for already-alerted positions.
  - **Open/close confirms** at post-close actioning: Open → `🟢 Opened SYM` / `<score> · <DTE> · $<strike> <Side>` / `$<amt> (<alloc>%)`. Close → `💰/🛑 Closed SYM` / `<DTE> · $<strike> <Side>` / `<reason> · ±$<pnl> (±<pct>%)`.
  - Old score-list digest + ghost opportunity-exit alerts (`notifications.py` `process_score_notifications`/`process_opportunity_exits`, `notify-scores`/`notify-exits` CLI, `BuyingOpportunity` ledger) no longer fire from `trader update` — kept but dormant. `update_health` alerts unchanged.
- **Re-qualification sweep (graceful since 2026-06-11):** holding kept when entry-date score OR latest completed-session score under the new version clears threshold ("if the new strat would hold it, don't sell"); missing score rows (recalc backfill in flight) defer — re-fires each sync until resolvable, pending sweeps rescinded (`✅ Keep`) if backfilled scores re-qualify. Disqualified holdings exit at close of the FIRST session completing after detection with honest intrinsic+theta mark — never backdated to an already-closed bar (old backdating recorded 0% P/L on same-day entries and churned re-qualifying names).
- **`backtest_cascade.run_backtest`** additive observability: `equity_curve_mtm` plus `entry_eq`/`tp_price`/`sl_price`/`deadline` on trade-log/open-holdings dicts (non-behavioral).

---

## CT Tag (`ct_tag` field)

`/api/stocks/all` and `/api/stocks/<sym>` expose `ct_tag` (`'ct_call' | 'ct_put' | null`), computed server-side from `(overall, trend)` using cascade allocator thresholds. `StockTable` renders `CT↑` (green)/`CT↓` (red) pill with tooltip on the counter-trend setup + tier promotion. Default sort unchanged — tags are visual only.
