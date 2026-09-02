# v30 Algorithm Snapshot

- Status: `shipped`
- DB version: `30`
- Commit: `9a9da33`
- Resolved commit: `9a9da33aaf37032baeed97cdd7abcbe3cb01be07`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `no`
- Structured scoring config: `no`

## Intended Difference

AMC-aware earnings effective_date + [low,high] sampling refactor

AlgorithmVersion message: AMC-aware earnings effective_date + [low,high] sampling refactor
Commit subject: AMC-aware earnings effective_date + [low,high] sampling refactor

## Existing Documentation Hint

Source heading: `v30 - AMC-Aware Earnings effective_date (2026-04-29, `9a9da33`)` (from `.claude/docs/version-history-archive.md`)

`EarningsDate.call_time` drives effective-date shift: AMC events (call_time >= 16:00 ET) shift forward to next trading day so the date represents when the price reaction appears.

## Code Delta From Previous Resolved Version

Previous resolved key: `v29`
Diff range: `8473cba1..9a9da33a`

Changed snapshot-tracked paths:
- `ALGORITHM_VERSION`
- `api.py`
- `backtest_cascade.py`
- `backtest_cascade_15dte.py`
- `database/models/core.py`
- `monte_carlo.py`
- `monte_carlo_15dte.py`
- `trader.py`

Git diff stat:

```text
ALGORITHM_VERSION         |    2 +-
api.py                    |  221 +++++--
backtest_cascade.py       |  177 ++++--
backtest_cascade_15dte.py | 1409 ++++++++++++++++++++++++++++++++++++++++++
database/models/core.py   |  178 ++++--
monte_carlo.py            |  577 +++++++++++------
monte_carlo_15dte.py      | 1496 +++++++++++++++++++++++++++++++++++++++++++++
trader.py                 |  368 +++++++++--
8 files changed, 4055 insertions(+), 373 deletions(-)
```

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:1207` - 9a9da33 AMC-aware earnings effective_date + [low,high] sampling refactor
- `.claude/docs/version-history-archive.md:1208` - ff2cb91 Bump ALGORITHM_VERSION to 9a9da33
- `.claude/docs/version-history-archive.md:1784` - ## v30 - AMC-Aware Earnings effective_date (2026-04-29, `9a9da33`)
- `.claude/docs/version-history-archive.md:1790` - Applied at all earnings-loading sites (score-stage AND portfolio-stage).
- `.claude/docs/trading-strategy.md:287` - > **2026-04-29 - option-pricing-aware MC + bounded fill shipped (canonical).** All MC tables below were computed under the static-pricing 3-mode model.
- `.claude/docs/version-history.md:1263` - 9a9da33 AMC-aware earnings effective_date + [low,high] sampling refactor
- `.claude/docs/version-history.md:1264` - ff2cb91 Bump ALGORITHM_VERSION to 9a9da33
- `.claude/docs/version-history.md:1840` - ## v30 - AMC-Aware Earnings effective_date (2026-04-29, `9a9da33`)
- `.claude/docs/version-history.md:1846` - Applied at all earnings-loading sites (score-stage AND portfolio-stage).
- `.claude/docs/trading-strategy.md:216` - > **2026-04-29 - option-pricing-aware MC + bounded fill shipped (canonical).** All MC tables below were computed under the static-pricing 3-mode model.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
