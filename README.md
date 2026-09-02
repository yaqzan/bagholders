# Trader

A technical analysis platform that scores stocks 0-100 every night and maps the strongest scores to ATM options entries.

Live: https://trader.yaqzan.dev

## Why I built it

I wanted a systematic answer to "is this stock set up for a move" instead of a gut call. So I built a scorer that combines six technical indicators with regime and volume context, then I refused to trust it until it survived five years of real price history in barrier-touch backtests and Monte Carlo simulation. No scoring change reaches the live portfolio until it clears those gates.

## How it works

1. `trader.py update` pulls daily OHLCV for the scored universe and runs the scoring pass.
2. `database/utils/scoring.py` computes the composite score: six weighted components (trend, Bollinger position, RSI, MACD, stochastic, technical alignment), then a weekly bias adjustment, an asymmetric MACD gate, a volume multiplier, and a regime multiplier, applied in that order.
3. `market_regime.py` and `market_breadth.py` turn VIX plus market breadth (McClellan, TRIN, A-D line, 52-week highs/lows, Zweig, Hindenburg) into the regime multiplier baked into every score.
4. Scores of 70+ are call candidates, 25 and under are put candidates. `dte_recommendation.py` classifies the thesis and picks a DTE range per signal.
5. Before anything ships, `assess_scores.py` runs a vol-adjusted barrier-touch backtest and `monte_carlo.py` runs 500 iterations across three collision modes and six historical windows.
6. `api.py` (Flask) serves scores and backtest results to the React frontend (`src/`), which renders the dashboard, per-stock detail, regime trends, and assessment pages.
7. Long-running compute (recalcs, Monte Carlo sweeps) goes through a homegrown priority job queue (`task_queue/`) instead of running ad hoc, so a heavy sweep can't starve the daily update.

`.claude/` holds the guidance I give Claude Code when it works in this repo. I develop with agents heavily, and the docs there are the project's memory.

## What I think is interesting

- **Every scoring-formula change gets its own frozen code silo.** A commit that changes `database/utils/scoring.py` bumps `ALGORITHM_VERSION` and `algorithm_versions/vNN/` freezes a full copy of the scoring code at that version (v27 through v74 on disk). Scores are keyed by `(symbol, date, version)`, so a historical score stays reproducible against the exact code that produced it, not just a git tag.
- **The scoring formula has an asymmetric MACD gate that only fires on the put side.** When the pre-MACD score drops below 45, MACD's weight is zeroed and redistributed to the other components. That removes a lagging-signal suppression effect on bearish setups while leaving the call side untouched.
- **A DB timeout saga is documented in the code that fixed it.** `database/trader_database.py` carries a multi-paragraph comment tracing a 30s to 180s to 30s timeout history back to one missing MySQL index (`scores_version_date_IDX`, added 2026-05-08), which took a `MAX(date)` query from up to 20+ minutes under contention down to 1ms.
- **The strategy's capacity was measured, not assumed.** A liquidity floor stress test (`experiments/liquidity_floor_2026_08/`) checked real historical entries against real volume data and found that at deployment size, 53.5-75.2% of entries could not have absorbed even a 5-20 contract clip within 25% of that day's volume.
- **The capital-allocation plan regenerates itself from live results.** `tools/capital_plan_refresh.py` rebuilds the planning doc from current backtest and Monte Carlo output and prints a verdict-delta banner when a planning-relevant fact flips sign, so the plan can't quietly drift out of sync with what the backtests actually show.

## Running it

This is a personal, self-hosted tool. It runs against my own MySQL database, on Windows, with data paths and a task queue tuned for my machine. It is not turnkey, but here is what it needs.

Prerequisites: Python 3.11+, Node.js 18+, MySQL (or SQLite for local dev).

```bash
pip install -r trader_api_requirements.txt
npm install

python trader.py update          # pull today's data and score everything
python api.py                    # Flask API on :5000
npm start                        # React dev server on :3000
```

Config comes from environment variables, not a committed file:

- `POLYGON_API_KEY`, `SHARADAR_API_KEY` (and related `POLYGON_*` vars) — paid market-data vendors (Polygon.io, Sharadar/Nasdaq Data Link), read from `.env` by the ingest scripts under `experiments/data_ingest/`.
- `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN` — push notifications for live signals (`notifications.py`).
- `REACT_APP_API_URL` — frontend's `.env`, points the dashboard at the Flask API.

`experiments/` ships the research code and notes for every sweep and investigation referenced above, but not the result data files those runs produced. `.codex/` scheduled-task run logs are excluded too.

## Layout

```
database/          Peewee models, scoring engine, DB utilities
experiments/        Research: sweeps, backtests, data-ingest scripts (code + notes, no result data)
algorithm_versions/ Frozen scoring-code snapshot per algorithm version
task_queue/          Priority job queue for long-running compute
src/                 React frontend
scripts/             Backup and maintenance scripts
tools/               Capital-plan refresh and other one-off utilities
.claude/             Docs and guidance for the agents I develop with
```

## Status

Live at trader.yaqzan.dev, actively traded and actively developed. This is a snapshot of my private working repo; its commit history isn't published.

## License

MIT
