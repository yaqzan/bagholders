# Bagholders

A technical analysis platform. It scores about 74,000 stocks from 0 to 100 every night and turns the strongest scores into ATM options entries for a live portfolio.

Live: https://trader.yaqzan.dev (also bagholders.ai; moving to quant.yaqzan.dev)

The code, the `trader.py` CLI and the database still use the project's original name, Trader. Bagholders is the public name.

## Why I built it

I wanted a repeatable answer to "is this stock set up for a move" instead of a gut call, and I wanted proof before I traded on it. So the scoring model is only half the project. The other half is the gate: a change to the formula does not reach the portfolio until it has survived five years of real price history in a barrier-touch backtest and a 500-run Monte Carlo drawdown check.

## How it works

1. `trader.py update` pulls daily OHLCV for the universe and runs the scoring pass.
2. `database/utils/scoring.py` builds the composite: six weighted components (trend, Bollinger position, RSI, MACD, stochastic, alignment), then a weekly bias adjustment, an asymmetric MACD gate, a volume multiplier, and a regime multiplier, in that order.
3. `market_regime.py` and `market_breadth.py` turn VIX and breadth (McClellan, TRIN, advance-decline, 52-week highs and lows, Zweig, Hindenburg) into the regime multiplier every score carries.
4. Scores of 70 and up are call candidates, 25 and under are put candidates. `dte_recommendation.py` classifies the thesis and picks a DTE range for each.
5. Before a change ships, `assess_scores.py` runs the vol-adjusted barrier-touch backtest and `monte_carlo.py` runs 500 iterations across three collision modes and six historical windows.
6. `api.py` (Flask) serves scores and results to the React frontend in `src/`: dashboard, per-stock detail, regime trends, assessment pages.
7. Anything heavy (recalcs, Monte Carlo sweeps) goes through my own priority job queue in `task_queue/`, so a sweep can never starve the nightly update.

`.claude/` holds the guidance I give Claude Code when it works in this repo. I develop with agents heavily, and those docs are the project's memory.

## Decisions I'd defend

- **Every formula change gets a frozen copy of the scoring code.** Changing `scoring.py` bumps `ALGORITHM_VERSION` and snapshots the scorer into `algorithm_versions/vNN/` (v27 through v74 so far). Scores are keyed by symbol, date and version, so any historical score can be reproduced against the exact code that made it. A git tag would not give me that at query time.
- **The MACD gate is asymmetric on purpose.** When the pre-MACD score is under 45, MACD's weight goes to zero and is spread over the other components. MACD lags, and on bearish setups that lag was suppressing real signals. The call side never needed it, so I left it alone.
- **One index, twenty minutes to one millisecond.** The `MAX(date)` lookup that every scoring pass depends on was taking up to 20 minutes under contention, and I raised the timeout twice before finding the real cause and adding `scores_version_date_IDX`. The timeout is back to 30 seconds and the query runs in 1 ms. I kept the whole history in a comment next to the setting so I never loosen that timeout again.
- **Capacity is measured, not assumed.** I checked historical entries against real daily volume. At my deployment size, 53.5 to 75.2 percent of entries could not have absorbed a 5 to 20 contract clip within a quarter of that day's volume. That number shapes position sizing more than any backtest return does.
- **The capital plan rebuilds itself from results.** `tools/capital_plan_refresh.py` regenerates the plan from the current backtest and Monte Carlo output and prints a banner when a planning fact flips sign. The plan cannot drift away from what the numbers say.

## Running it

This is a personal, self-hosted tool. It runs on Windows against my own MySQL database with paths and queue sizing tuned for my machine. It is not turnkey. What it needs:

Python 3.11+, Node.js 18+, MySQL (SQLite works for local development).

```bash
pip install -r trader_api_requirements.txt
npm install

python trader.py update          # pull today's data and score everything
python api.py                    # Flask API on :5000
npm start                        # React dev server on :3000
```

Configuration is environment variables only:

- `POLYGON_API_KEY`, `SHARADAR_API_KEY` and the related `POLYGON_*` vars: paid market-data vendors, read from `.env` by the ingest scripts in `experiments/data_ingest/`.
- `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`: push notifications for live signals.
- `REACT_APP_API_URL` in the frontend `.env`: where the dashboard finds the API.

`experiments/` includes the code and notes for every sweep referenced above but not the result data those runs produced. Scheduled-task run logs under `.codex/` are excluded as well.

## Layout

```
database/            Peewee models, scoring engine, DB utilities
experiments/         Research: sweeps, backtests, data-ingest scripts (code and notes, no result data)
algorithm_versions/  Frozen scoring code per algorithm version
task_queue/          Priority job queue for long-running compute
src/                 React frontend
scripts/             Backup and maintenance
tools/               Capital-plan refresh and one-off utilities
.claude/             Docs and guidance for the agents I develop with
```

## Status

Live and traded daily, still under active development. This repo is a snapshot of my private working repo; the commit history is not published.

## License

MIT
