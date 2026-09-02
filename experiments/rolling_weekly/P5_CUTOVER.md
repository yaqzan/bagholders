# P5 — Rolling Weekly Cutover Procedure

**Status:** **DO NOT RUN.** This document is a recipe for the eventual flip
once you've decided to ship rolling weekly to production. P1-P4 are already
merged into the branch and SAFE — production behavior is unchanged because
`WEEKLY_MODE` defaults to `'calendar'`. P5 is the moment of behavior change.

## Pre-flight checklist

Before starting P5, verify:

- [ ] Branch `rolling-weekly-p1-p4` is merged to main (or you're on it locally)
- [ ] `tests/test_strategy_config_drift.py` passes (130 + WEEKLY_MODE check)
- [ ] `tests/test_rolling_weekly.py` passes (5/5)
- [ ] `RollingWeeklyIndicator` table is populated for the full 5y window:
  ```bash
  PYTHONIOENCODING=utf-8 python -c "from database.models.technical import RollingWeeklyIndicator; print(RollingWeeklyIndicator.select().count(), 'rows')"
  ```
  Expected: ~850K+ rows. Re-run `python experiments/rolling_weekly/07_run_backfill.py` if missing.
- [ ] Live data path is fresh — last `trader update` should have populated
  today's rolling indicators automatically (P3 wiring). Verify:
  ```bash
  PYTHONIOENCODING=utf-8 python -c "from database.models.technical import RollingWeeklyIndicator; from datetime import date; n = RollingWeeklyIndicator.select().where(RollingWeeklyIndicator.date == date.today()).count(); print(f'{n} rows for today')"
  ```
- [ ] Current `ALGORITHM_VERSION` file is captured for revert. Note the commit hash.

## Cutover sequence — Saturday morning, after market close

This is a **one-config-line change** that activates rolling weekly + a
recalc to populate fresh score rows for the new version.

### Step 1 — Bump ALGORITHM_VERSION (10 sec)

```bash
# Capture current version for revert
cat ALGORITHM_VERSION  # save this somewhere

# Bump to a new commit hash representing this ship.
# Use git rev-parse on a fresh commit that includes the WEEKLY_MODE flip:
git rev-parse HEAD > ALGORITHM_VERSION
git diff ALGORITHM_VERSION
```

### Step 2 — Set WEEKLY_MODE to rolling (10 sec)

In production environment (whatever process supervises `trader update`),
set the env var:

```bash
export WEEKLY_MODE=rolling
```

Or equivalently, edit `strategy_config.py` line ~640 to change the default:

```python
WEEKLY_MODE = os.environ.get('WEEKLY_MODE', 'rolling')  # was 'calendar'
```

(Editing the file is more durable than env var — the env var requires
restarting any long-running process. Editing `strategy_config.py` and
restarting once is cleaner.)

### Step 3 — Recalculate scores under new mode (~25 min)

```bash
PYTHONIOENCODING=utf-8 python trader.py recalculate --force --full
```

This will:
- Detect the new `ALGORITHM_VERSION` and create a fresh AlgorithmVersion row
- Recompute all 5y of scores using `WEEKLY_MODE='rolling'`
- Auto-trigger `historic-update` and `assess` after recalc

### Step 4 — Verify (5 min)

```bash
# 1. Score count for new version
PYTHONIOENCODING=utf-8 python -c "
from database.models.core import Score, AlgorithmVersion
v = AlgorithmVersion.get_active_scores_version()
print(f'Active: v{v.id}/{v.git_commit[:8]}')
print(f'Total scores: {Score.select().where(Score.version == v).count():,}')
"

# 2. COHR Monday whiplash sanity (the case study)
PYTHONIOENCODING=utf-8 python -c "
from datetime import date, timedelta
from database.models.core import Score, AlgorithmVersion
import json
v = AlgorithmVersion.get_active_scores_version()
target = date.today()
for d_off in range(7):
    d = target - timedelta(days=d_off)
    if d.weekday() > 4: continue
    s = Score.get_or_none((Score.symbol == 'COHR') & (Score.version == v) & (Score.date == d))
    if s:
        wi = s.weight_info if isinstance(s.weight_info, dict) else json.loads(s.weight_info or '{}')
        print(f'  {d} ({[\"Mon\",\"Tue\",\"Wed\",\"Thu\",\"Fri\"][d.weekday()]}): overall={s.overall} w_adj={wi.get(\"w_adj\")}')
"

# 3. Per-trade per-tier WR15 (sanity check the deltas match P0 prediction)
PYTHONIOENCODING=utf-8 python trader.py assess --force 5y
# Compare 80+ / 85+ tiers vs prior version. P0 predicted +3-5pp on these.

# 4. Live cron sanity — wait for next trader update, verify it produces scores
```

### Step 5 — Live observation (3-5 trading days)

For the next 3-5 trading days, monitor:
- Per-day score volatility on Monday (should be reduced ~30%)
- Per-trade WR15 on top tiers (should match or exceed prior version)
- COHR-class intra-day swings (should be eliminated)

Use the dashboard's score history charts or:

```bash
# Compare Monday |Δoverall| under new mode vs prior week
PYTHONIOENCODING=utf-8 python experiments/weekly_proximity/04_stability_breakdown.py
```

## Revert procedure (if needed)

**Cost: ~30 min.** No data loss — old version's scores still in DB.

### Step R1 — Restore ALGORITHM_VERSION

```bash
echo "<old commit hash>" > ALGORITHM_VERSION
```

(Or use `trader revert <old_version>`.)

### Step R2 — Set WEEKLY_MODE back to calendar

```bash
unset WEEKLY_MODE  # if using env var
# OR edit strategy_config.py back to default 'calendar'
```

### Step R3 — Recalc (or skip if v40 scores are still in DB)

```bash
# If old version's scores were preserved (default behavior — Score table
# is keyed on version), API will instantly serve old version. No recalc
# needed.

# If for some reason old scores were dropped, recalculate:
PYTHONIOENCODING=utf-8 python trader.py recalculate --force --full
```

## What stays unchanged

These tables are still populated and consulted under both modes:
- `WeeklyScore` — written every `trader update`, read by API endpoints
- `WeeklyIndicator` — written every `trader update`
- `WeeklyPriceHistory` — written every `trader update`

Only `Score.calculate_overall_score`'s WEEKLY INPUT FOR SCORING changes when
WEEKLY_MODE='rolling'. Display, historic peaks, and legacy display API
endpoints continue to read calendar weekly values.

## Files touched in this ship (P1-P4)

```
database/models/__init__.py        — export RollingWeeklyIndicator
database/__init__.py                — export RollingWeeklyIndicator
database/models/technical.py        — model + ensure_schema + bulk_build
database/utils/rolling_weekly.py    — compute, backfill, incremental, helpers (NEW)
database/models/core.py             — Score.calculate_overall_score routed via WEEKLY_MODE
                                     calculate_scores_batched routed via WEEKLY_MODE
                                     recalculate_scores_batched routed via WEEKLY_MODE
strategy_config.py                  — WEEKLY_MODE flag added (default 'calendar')
simulator.py                        — StockContext.weekly_mode + scoring routes
trader.py                           — incremental update wired into update_stocks cron
api.py                              — /api/stocks/<sym>/rolling-weekly-indicators endpoint
tests/test_strategy_config_drift.py — WEEKLY_MODE check added
tests/test_rolling_weekly.py        — 5 smoke tests (NEW)
```

## Why this ship is safe to merge before flipping

The branch `rolling-weekly-p1-p4` can sit on main indefinitely with
`WEEKLY_MODE` defaulting to `'calendar'`. In that state:
- All scoring code paths route through the calendar branch (legacy behavior)
- `RollingWeeklyIndicator` table populates daily but isn't read by scoring
- Tests pass (drift-guard + 5/5 rolling tests)
- API has one new endpoint (no existing endpoints modified)

The flip itself is 10 seconds + a 25-min recalc. The revert is 10 seconds
+ no recalc (old scores still in DB).
