# weekly_pit — point-in-time partial-week weekly reconstruction

Reconstructs the weekly composite / `w_adj` / `w_mom` / `w_bias` that the v60
scorer SHOULD see at each `(symbol, signal_date)`, using ONLY daily
`PriceHistory` bars up to and INCLUDING `signal_date` (no future bars). Built to
diagnose and quantify the weekly recalc **look-ahead** and to test for real
(non-look-ahead) weekly alpha.

## The three weekly states (the key finding)

There are THREE distinct weekly contexts in the v60 code, NOT two. The
task brief assumed "live = current-partial"; that is wrong. Verified empirically
against `score_intraday_logs` (live production v60, 2026-05-27..29):

| State | Convention | Where used | Look-ahead? |
|---|---|---|---|
| **1. last-completed week** | `date - (weekday()+7)` | `Score.weekly_score` property → **single-row `calculate_overall_score`** = the LIVE `trader update` daily path | NO (point-in-time-safe, but ~1 week stale) |
| **2. current-partial week** | `date - weekday()`, week truncated Mon→signal_date | nothing today (this is what a transition-blend would converge toward) | NO (freshest point-in-time) |
| **3. current-complete week** | `date - weekday()`, full Mon-Fri WeeklyScore row | `calculate_scores_batched` / `recalculate_scores_batched` (line ~4058) = the **recalc path that generated the ledger** | **YES** (reads future bars for a mid-week signal) |

So the contamination is recalc(state 3, look-ahead) vs live(state 1,
last-completed). They differ by ~1 full week of weekly context — not by
partial-vs-complete. The recalc batched path uses the stored complete-week
`WeeklyScore`; the live single-row path lags one week via the `weekly_score`
property.

## Faithfulness method (no re-implementation of score internals)

We do NOT re-derive the breakout-push / divergence / MACD-phase logic. We:
1. Aggregate daily bars ≤ `signal_date` into weekly OHLCV (current week truncated
   to Mon→signal_date): `open=first, high=max, low=min, close=last, volume=sum`,
   `week_start=Monday` — identical to `technical.py _refresh_weekly_aggregates`.
2. Run the IDENTICAL talib calls production uses (`RSI` 14, `MACD` 12/26/9, `EMA`
   21/50/200) over the weekly close series, with the SAME column-gating indices as
   `core.calculate_indicators(weekly=True)`.
3. Wrap results in indicator-/price-like rows and call the REAL production score
   functions `Stock.calculate_{trend,rsi,macd}_score(..., weekly=True, _ind_cache=, _ph_cache=)`
   through their injection ports.
4. Feed the (trend, rsi, macd) into the REAL `calculate_weekly_adjustment` to get
   `w_comp / w_bias / w_mom / w_adj`.

`pit_*` columns = state 2 (current-partial). `comp_*` columns = state 1
(last-completed = live convention).

## Phase 2 validation result (the look-ahead safety net)

`comp_*` (state 1) reconstruction vs LIVE `score_intraday_logs` `w_comp/w_adj`,
N=2294 across 765 symbols (Wed/Thu/Fri 2026-05-27..29):

| field | mean\|Δ\| | median | %≤2 | %≤5 |
|---|---|---|---|---|
| w_comp | **0.277** | 0 | 97.5% | 99.3% |
| w_adj  | **0.227** | 0 | 98.4% | 99.6% |
| w_mom  | **0.101** | 0 | 99.7% | 100% |

The reconstruction is **faithful** — it reproduces live to within rounding /
EMA-warmup-truncation noise. The residual ~0.28 on w_comp is the talib
EMA200 recursive-warmup difference from tail-truncating the weekly series
(`MAX_WEEKS_TAIL=320`), not a logic bug.

`pit_*` (state 2, current-partial) vs live differs by mean\|Δ\|~4.8 (w_comp) /
~5.6 (w_adj) — as expected, since live is the lagged last-completed week.

## Files

- `build_pit_weekly.py` — reconstruction + Phase-1 build + Phase-2 validate.
  - `python build_pit_weekly.py validate [--syms N]` → match-vs-live report +
    `.cache/weekly_pit/validation_vs_live.parquet` + `validation_summary.json`.
  - `python build_pit_weekly.py build [--workers N] [--force]` →
    `.cache/weekly_pit/pit_weekly_5y.parquet`, keyed `(symbol, date)` with
    `pit_*` (partial) and `comp_*` (completed) columns. Holdout-locked ≤ 2026-05-15.
- `remine_pit.py` — Phase 3: joins PIT to `.cache/rqc_v60/ledger_5y.parquet`,
  runs the day-of-week flatness test (look-ahead detector) and re-mines the
  70-74 / 75-79 boundary on PIT features at option-TP (`opt15`).
  - `python remine_pit.py` → `.cache/weekly_pit/remine_5y.json`.

Set `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` on every run.

Experiment/analysis only — no production scoring edits, no DB writes.
