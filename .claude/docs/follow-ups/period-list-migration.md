# Follow-up: Period List Migration (drop 150d, add WR3/WR5)

**Status:** SPEC ONLY. Not yet implemented. Documentation updated 2026-05-08 to
reflect the new period list and three-stage framework; the code change below
must land before any new Stage 1 sweep can validate against WR3/WR5/WR7.

**Scope:** drop the 150d barrier window; add 3d and 5d barrier windows;
vol-adjust the 1d window (was direction-only).

**Why:** the three-stage calibration framework (assessment-backtest.md) makes
WR7 the Stage 1 primary metric and uses WR3/WR5/WR7/WR15/WR30 as the
multi-barrier-window directional consistency check (W2). 150d is used by no
ship gate and was diagnostic-only.

---

## Files affected

### Required (without all of these, the migration breaks)

1. **`assess_scores.py`** — `PERIODS` constant + `_swing_walk` per-period
   loop. Drop 150 from PERIODS. Add 3, 5. Vol-adjust 1d branch (currently
   direction-only) to use K=2σ × √(1/30) ≈ 0.37σ target / M=5σ × √(1/30) ≈
   0.91σ stop on calls; mirror for puts.

2. **`database/models/core.py:ScoreAssessmentResult`** — schema migration in
   `ensure_schema()`. Adds 26 columns × 2 (3d, 5d) = 52 new columns.
   Drops 26 (150d) columns. Net +26.

   Per period, the columns are:
   ```
   avg_mae_{p}, avg_mae_loser_{p}, avg_mae_loser_sigma_{p},
   avg_mae_winner_{p}, avg_mae_winner_sigma_{p}, avg_mfe_{p},
   avg_mfe_sigma_{p}, avg_peak_{p}, avg_return_{p}, capture_ratio_{p},
   median_mae_{p}, median_mfe_{p}, median_mfe_sigma_{p},
   median_peak_{p}, median_return_{p}, mfe_p25_{p}, mfe_p75_{p},
   mfe_p90_{p}, mfe_sigma_p25_{p}, mfe_sigma_p75_{p},
   swing_avg_stop_pnl_{p}, swing_avg_win_pnl_{p}, swing_p_expire_{p},
   swing_p_stop_{p}, win_rate_{p}, win_rate_unscaled_{p}
   ```

   Migration safety: `ensure_schema()` already does ADD COLUMN IF NOT EXISTS
   pattern; extend it to also DROP COLUMN IF EXISTS for the 150d set. Wrap
   in transaction.

3. **`historic_peaks.py`** — `win_{p}` columns. Currently has 1d/7d/15d/30d/60d/90d.
   Add `win_3d`, `win_5d`. The 150d window isn't in HistoricPeak (already aligned).

4. **`database/barrier_cache.py`** — `barrier_outcomes` SQLite cache schema.
   Cache rows per (sym, peak_date, K, M, W). Adding W=3 and W=5 means
   re-running `refresh_recent` to populate. Old W=150 rows can stay (just
   become unused) or get a one-time DELETE. Also: DuckDB read mirror needs
   rebuild after SQLite is updated.

### Optional (cosmetic; assessment will work without these)

5. **`src/pages/Assessment.js`** — Win Rates tab renders period columns.
   Currently shows {1d, 7d, 15d, 30d, 60d, 90d, 150d}. Update to
   {1d, 3d, 5d, 7d, 15d, 30d, 60d, 90d}.

6. **`src/pages/Historic.js`** — bridge analysis active-period selector.
   Currently {15d, 30d, 60d, 90d}. Adding 3d/5d/7d as active periods would
   require new pill semantics for sub-7d rollup/reentry (currently
   transitions are 7→15→30→60→90). **Recommend: leave Historic.js alone.**
   Sub-7d windows are too short for the rollup/reentry diagnostic to be
   meaningful at signal-event scale.

7. **`/api/assessment` and `/api/stocks/<sym>/assessment`** — JSON
   serialization will pick up new columns automatically since it's a generic
   bucket dict. No code change needed unless we want to deprecate 150d in
   API contract.

### NOT affected

- `monte_carlo.py`, `backtest_cascade.py`, portfolio engines: they use the
  option-aligned barrier set (`30dte_opt`), not the generic K=2σ/M=5σ
  barriers. PERIODS doesn't apply.
- `cross_window_bridge.py`: uses 7→15, 15→30, 30→60, 60→90 transitions only.
  150d wasn't part of bridge analysis.

---

## Migration sequence

1. **Edit code** (assess_scores.py + core.py + historic_peaks.py +
   barrier_cache.py if needed). Single commit, no version bump.
2. **Run schema migration** via `python -c "from database.models.core import
   ScoreAssessmentResult; ScoreAssessmentResult.ensure_schema()"`. Adds new
   columns, drops 150d.
3. **Re-run barrier_cache backfill** for W=3 and W=5: extend
   `database/barrier_cache.py:backfill_sets()` to include W ∈ {3, 5} for
   both `30dte_generic` and `30dte_opt` barrier sets, then run
   `python -m database.barrier_cache backfill`. Estimated ~20 min for both
   new windows on full 5y universe.
4. **Run `trader assess --force`** to populate all new columns on every
   active version. ~10 min.
5. **Frontend rebuild** if `src/pages/Assessment.js` updated.

## Backward compatibility

- Old `ScoreAssessmentResult` rows with NULL in the new columns will display
  as `--` in the dashboard until re-assessed. Pre-existing rows for v18..v45
  won't be re-populated — only newly-run assessments get the WR3/WR5 columns.
  This is acceptable; cross-version comparison defaults to v46+ data going
  forward.
- 150d columns dropping means historical assessments that had 150d data
  lose it. No active reader; safe to drop.

## Validation after migration

```bash
python -c "
from database.models.core import ScoreAssessmentResult
from peewee import fn
# Confirm new columns exist
fields = ScoreAssessmentResult._meta.fields.keys()
required = ['win_rate_3d', 'win_rate_5d', 'avg_mae_3d', 'avg_mae_5d']
banned = ['win_rate_150d', 'avg_mae_150d']
print('NEW COLUMNS PRESENT:', all(f in fields for f in required))
print('OLD 150D COLUMNS DROPPED:', not any(f in fields for f in banned))
"

PYTHONIOENCODING=utf-8 python trader.py assess --force 5y --dte 30
# Confirm WR3, WR5 populated for 95+ bucket
```

## Estimated effort

- Code changes: ~2 hours
- Schema migration + re-assess: ~30 min
- Barrier cache backfill: ~20 min
- Frontend update (optional): ~30 min
- Total: ~3-4 hours wall clock

## Risk

- Low. The change is additive (new columns + new period iterations) plus a
  drop of unused columns. No portfolio/scoring logic touched.
- Rollback: re-add 150d columns and re-run assess. Loss only of WR3/WR5
  rows populated since migration; trivial to re-populate.
