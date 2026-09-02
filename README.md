# Bagholders

Scores about 74,000 stocks from 0 to 100 every night and turns the strongest scores into ATM options entries for a live portfolio.

![The Bagholders dashboard](docs/screenshot.webp)

Live at https://quant.yaqzan.dev (also bagholders.ai). The code, the `trader.py` CLI and the database still go by the project's original name, Trader.

## The nightly score

Every evening the updater pulls the day's OHLCV for the whole universe and rescores it. A score is a weighted blend of six technical reads on the chart: trend, position inside the Bollinger bands, RSI, MACD, stochastic, and how well those signals agree with each other. On top of that sit four adjustments applied in a fixed order: a weekly bias, a MACD gate, a volume multiplier, and a market-regime multiplier built from VIX and breadth (McClellan, TRIN, advance-decline, 52-week highs and lows, Zweig, Hindenburg).

The MACD gate is asymmetric. When a stock's score before MACD is under 45, MACD's weight goes to zero and is spread over the other components. MACD lags, and on bearish setups that lag kept suppressing real signals. The call side never had the problem, so it keeps the full weight.

Scores of 70 and up are call candidates and 25 and under are put candidates. A separate classifier reads the shape of the thesis and picks a days-to-expiry range for each one, and the strongest signals go out as push notifications.

Every formula change freezes a full copy of the scoring code under its own version number (v27 through v74 so far), and scores are stored keyed by symbol, date and version. A score from last spring can be recomputed against the exact code that produced it, at query time, without checking anything out.

## Scored like a weather forecast

In June 2026 I rebuilt the verification layer around the discipline meteorologists use to grade forecasts, because the way I had been grading the model was letting bad mechanisms through. The rules are simple to state and unforgiving to pass.

A signal has to show skill against two dumb baselines before it counts as anything: climatology (the base rate of the market) and persistence (plain momentum). Positive expected value is not skill. Beating a coin you could have flipped for free is.

It has to be verified on the real payoff. The thing I actually trade is an option with a +30 percent take-profit and a -70 percent stop over 30 days, so that asymmetric payoff is the predictand. Earlier versions were graded on a generic barrier-touch test that hid the stop risk, and four separate mechanisms passed that proxy and then lost money on the real payoff. Every version now gets a scorecard against the option payoff with a SHIP, FLAG or BLOCK verdict, built automatically after each recalc on a holdout split.

The current lineage's honest verdict is FLAG. Versions 69 through 74 beat momentum with a t-statistic near 2.9 but are only marginal against climatology, which makes the score a risk-shaper, a leveraged momentum selector, rather than directional alpha. I accepted that profile instead of tuning until the number looked better, and the gate is set so a FLAG never triggers an automatic revert.

The model is also split into a core score and a separate bias-correction layer, the way model output statistics sit on top of a numerical forecast. Any fitted correction table has to carry a stamp proving it was built on a holdout window, so the look-ahead leak that once crept into an earnings boost cannot recur. And parsimony is enforced with measurements: the v74 "lean" retired four mechanisms that were adding signal supply but diluting the funded book, and the whole-tail ablation cut the five-year Monte Carlo drawdown by 10.8 points with no collapse.

Forward verification is still accruing. The calibration cutoff is 2026-06-15, the first true out-of-sample read lands around 2026-12-15, and the live portfolio that started 2026-06-01 is the real-money version of the same test.

## Nothing ships without proof

Before the verification layer even sees a change, it has to survive the older gates: a vol-adjusted barrier-touch backtest over five years of real price history, then a Monte Carlo run of 500 iterations across three collision modes and six historical windows measuring drawdown. A formula change that loses any of these stays in the experiments folder.

Capacity gets the same treatment. I checked historical entries against real daily volume, and at my deployment size 53.5 to 75.2 percent of them could not have absorbed even a 5 to 20 contract clip within a quarter of that day's volume. That number shapes position sizing more than any backtest return does.

## The job queue

Recalcs, Monte Carlo sweeps, research-pack builds and reruns of old versions all want the same 32 threads and the same MySQL server, which has a tight read timeout. Before the queue they collided, and a heavy sweep could stall the nightly update. So there is a single-node priority scheduler, `trader queue`, that every long job goes through.

Its state lives in SQLite on purpose, since the whole point is to protect MySQL bandwidth. Jobs are admitted by priority across six tiers, from critical down to idle, with aging so a low job is never starved forever. The scarce resource is not CPU but database connections: at most two heavy-DB jobs run at once, ever, and that limit is not tunable by design. Cores are oversubscribed and left to the OS to time-slice, because boxing each job into its own reservation left most of the machine idle while the DB cap held everything else back.

When a high-priority job is starved, the daemon preempts. The default is a throttle that drops the victim's process tree to idle priority and shrinks its affinity, which is reversible and safe even for a job holding MySQL locks. Pure-compute jobs can be suspended outright. A job that holds a DB slot the high-priority work needs gets killed and requeued, without spending one of its retry attempts. Everything restores when cores free up, and nothing stays stuck across a daemon crash because admission is recomputed from the task rows every tick.

The nightly update runs through the queue at its own tier, but Windows Task Scheduler stays the timekeeper and the wrapper falls back to running the update inline if the daemon is down, so the trading-day pipeline never depends on the queue being alive. A global named mutex stops two updates from ever overlapping regardless. Heavy jobs set their worker counts from environment variables the queue injects, so none of them needed changes to become schedulable.

## Running it

This is a personal, self-hosted tool. It runs on Windows against my own MySQL database, with data paths and queue sizing tuned for one machine. It is not turnkey, but the pieces are ordinary: Python 3.11, Node 18, MySQL (SQLite works for local development).

```bash
pip install -r trader_api_requirements.txt
npm install

python trader.py update    # pull today's data and score everything
python api.py              # Flask API on :5000
npm start                  # React dashboard on :3000
trader queue daemon        # scheduler for recalcs, sweeps, Monte Carlo
```

Configuration is environment variables only. `POLYGON_API_KEY` and `SHARADAR_API_KEY` for the paid market-data vendors, `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN` for notifications, and `REACT_APP_API_URL` in the frontend `.env` so the dashboard can find the API.

`experiments/` has the code and notes for every sweep mentioned above, without the result data those runs produced. `.claude/` holds the guidance I give Claude Code when it works in this repo; I develop with agents heavily and those docs are the project's memory.

```
database/            models, scoring engine, DB utilities
algorithm_versions/  frozen scoring code per version
experiments/         research sweeps and backtests (code and notes)
task_queue/          priority job queue
src/                 React frontend
```

This repo is a snapshot of my private working repo; the commit history is not published.

## License

MIT
