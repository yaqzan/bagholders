---
name: debug-scores
description: Score forensics — diagnose why a specific score is what it is, trace intraday swings/fakeouts to a root cause in the scoring pipeline, and resolve version confusion between the HEAD, active-scores, and dashboard-selected AlgorithmVersion. Use when the user asks "why did SYM score X", "why did this flip from 92 to 60 today", "is this a fakeout", "which version am I looking at", or wants to run `trader explain-scores` / `trader intraday-swings` / `trader intraday-drill` / `trader simulate --diff-assess`.
---

# /debug-scores — score forensics: explain, drill, and version-check

Diagnoses a score's *cause*, not its portfolio consequence (for Stage-1/Stage-3
ship gates see [/ship-gates](../ship-gates/SKILL.md); for a general symptom
index — update failures, MySQL zombies, queue health — see
[/debug-pipeline](../debug-pipeline/SKILL.md)). Three tool families, pick by
question shape:

| Question | Tool |
|---|---|
| "Was this signal historically CORRECT?" (forward-return audit) | `trader explain-scores` |
| "Why did this score move a lot TODAY with little price change?" (intraday) | `trader intraday-swings` / `trader intraday-drill` |
| "Does my code change move the score the way I expect?" (dev-loop diff) | `trader simulate --diff-assess` / `--compare` |

## GUARDS (read before drilling anything)

1. **`ScoreIntradayLog` is the ONLY intraday record.** `Score` is keyed
   `(symbol, date, version)` and **overwritten in place** on every
   `trader update` — the DB never retains how today's score evolved across the
   day. `score_intraday_logs` is the append-only companion (one row per
   symbol per update run); it is the entire evidentiary basis for any
   "why did this flip mid-day" question. **Data exists only from 2026-05-27**
   (ship `c0bfd1d67`) — a swing you're asked about before that date has no
   intraday trail, only the final `Score` row.
2. **Three different "which version" resolvers — they disagree on a dirty
   tree.** `explain-scores` and the `/intraday-swing` API use
   `AlgorithmVersion.get_active_scores_version()` (the **ALGORITHM_VERSION
   file pointer** — same one `trader revert` flips). `trader assess` uses
   `get_or_create_current()` (**HEAD-ish**: matches the *working-tree* commit,
   creating a new row if none exists yet). On a clean, just-shipped checkout
   these are identical; mid-refactor or on a candidate worktree they are not.
   Always check `python trader.py algorithm active` first and pass an
   explicit `--version vNN` to any tool if there's any doubt which one you're
   reading. See [assessment-backtest.md](../../docs/assessment-backtest.md)
   "Version resolution in assess".
3. **The captured `weight_info` can be AHEAD of the silo you think is
   active.** `score_intraday_logs` records whatever scoring code the
   **production scheduler** ran at capture time. A row tagged with an old
   version id can carry `weight_info` keys that version's frozen silo doesn't
   emit (e.g. old v60-tagged rows carrying weekly-envelope keys only the
   v65/v66 silos define). **Trust the captured `weight_info` JSON over the
   silo source when attributing a swing** — it's ground truth for what
   actually ran.
4. **A `fakeout_family` tag can be historical, not live.** `intraday_diagnostics.py`
   tags `wcf_boundary` whenever `changed_dampener` contains `wcf_lift` — but
   `wcf_lift` only fires when `WCF_LIFT_K != 0`. **As of 2026-07 the active
   version is v74 and `strategy_config.py` has `WCF_LIFT_K=0.0`** (WCF
   retired in v73) — so `wcf_boundary` will never tag on current scores; it
   only explains rows captured before the v73 retirement. Verify a family's
   liveness against the *active* constant, not the tag's historical
   reputation. Same caution applies to any dampener/boost key you don't
   recognize — check `scoring-algorithm.md`'s ⚠ RETIRED banners before
   assuming a stage is live.
5. **Regime reapply can move `overall` after every other stage already ran.**
   `trader update` scores each stock against the **last-available** regime
   row, then after ALL stocks are scored, computes a fresh regime and calls
   `reapply_regime_today()`, which patches every symbol's *already-written*
   score atomically via `weight_info['pre_regime']`. This itself writes a
   second `score_intraday_logs` row per symbol with
   `source='regime_reapply_today'` — if you see exactly two snapshots for a
   symbol and the second has a different `regime_multiplier` but identical
   `pre_regime`, that's the reapply, not a data refetch.
6. **Assessment (`assess_scores.py`) never sees Stage-3 filters.** It computes
   barrier-touch WR on the **full population** of qualifying scores, with no
   F3F breadth knob, no cascade promotion, no put-suppression filter applied.
   If a score "should have been filtered out" by a portfolio-stage rule, that
   won't show up in `explain-scores`/`assess` output at all — check
   `monte_carlo.py`/`backtest_cascade.py` (or portfolio-ops) instead. This is
   by design (keeps Stage 1 barrier-independent of Stage 3 params), not a bug.

## 1. `trader explain-scores` — was this signal historically right?

Run this FIRST whenever asked to review/audit a signal or a batch of misses.

```bash
trader explain-scores [SYM ...] [days] [--jsonl PATH] [--csv PATH] [--no-text] [--wide] [--high-min N] [--low-max N]
```

- `[SYM ...]` — zero or more symbols (default: all). `[days]` — bare int or
  `parse_lookback_arg` form (`40d`/`3y`); default `assess_scores.DEFAULT_LOOKBACK`
  = **365**.
- `--wide` sets `high_min=70, low_max=30` in one flag (else default `75`/`25`).
  `--high-min`/`--low-max` set them individually.
- `--no-text` suppresses the printed per-signal block (useful when you only
  want `--jsonl`/`--csv`).
- Resolves via `AlgorithmVersion.get_active_scores_version()` — **not** a
  `--version` flag; if you need a different version's peaks, there's no
  override here (use `assess --version vNN` for the barrier-touch numbers
  instead).
- Verdict logic per peak/trough (momentum frame, not the assessment barrier):
  **CORRECT** (window peak/trough touched the ±1% direction), **BAD_LUCK**
  (wrong outcome, indicators still confirmed), **MISS** (wrong outcome AND
  indicators were neutral/contradicting), **PENDING** (insufficient forward
  history yet). Per-signal output shows components, volume signal, weekly
  composite, raw indicators, and forward 7d/15d/30d returns.

## 2. Intraday swings & fakeouts — `score_intraday_logs`

### 2.1 Rank the day's/week's biggest swings

```bash
trader intraday-swings                          # last 7 days, swings >= 10 pts
trader intraday-swings 3                        # last 3 days
trader intraday-swings --fakeouts                # only score-moved / price-flat swings
trader intraday-swings --min-swing 20 --limit 40 --version v60
```

`[days]` default **7**. `--min-swing N` default **10** (point spread). `--limit N`
default **30** (not in CLAUDE.md's CLI table — verify with `--help`-equivalent
source read if this drifts). `--version V`/`-v V`. `--fakeouts`/`--fakeout` →
only rows the tool classifies as a fakeout (large score spread, small price
range). Alias: `intraday-swing`.

### 2.2 Drill one symbol-day: the pipeline attribution

```bash
trader intraday-drill PWR                        # most recent multi-snapshot date
trader intraday-drill AMSC 2026-05-29
trader intraday-drill PWR --version v60
```

`SYMBOL` positional required (usage+return if omitted). `[YYYY-MM-DD]` positional
optional, detected by exact 10-char dash-pattern. `--version V`/`-v V`. Alias:
`intraday-drill` / `intraday`.

Prints the ordered snapshot timeline for `(symbol, date, version)` (`ORDER BY
logged_at`), diffs the highest-`overall` snapshot against the lowest, and
attributes the delta down the **actual scoring order**:

```
overall = regime_mult( boosts( dampeners( weighted_sum(components) + weekly_adjustment ) ) )
```

| Stage | `weight_info` keys to compare | fakeout signature |
|---|---|---|
| Daily components | `trend/bb/rsi/macd/stoch/ta` + weights | components flip while price barely moves (data-refresh oscillation) |
| Weekly adjustment | `w_comp`, `w_adj`, `wadj_partial`, `wadj_completed`, `weekly_adj_gap` | `w_adj` jumps when the resolver flips partial-week ↔ completed-week (the v69-era COHR/VICR whiplash — CLOSED 2026-06-16, known-issues Priority #7/#9). Note: `intraday_diagnostics.py`'s own weekly_flip detector currently keys off `weekly_gap_flag`, which the live `database/utils/scoring.py` never sets (only the retired v65/v66 silos did) — that detector branch is dead against current data; derive the flip yourself from `weekly_adj_gap > 10.0` (the old silo's threshold) until it's fixed. |
| Dampeners (post-regime) | `cap_dampened`, `exh_damp`, `ext_damp`, `wcf_lift`, `cwcf_dampen`, `cwwd_dampen`, `cswc_dampen` | a dampener toggles on/off across runs while weekly+components stay flat |
| Volume amp (pre-regime) | `volume_signal`, `volume_magnitude` | a CONVICTION lift present in most runs vanishes for one run then recovers |
| Boosts (post-`pre_boost`) | `pcd_active`, `mcd_dampen`, `ich_call_dampen`, `ich_put_lift`, `wvd_lift`, `wvd_dampen`, `pess_lift`, `ern_boost`, `scw_dampen`, `cont_lift`, `sector_breadth_wave`, `daily_volume_authority_wave`, `pci_boost` | a hard cliff gate (e.g. 70/25) flips off on a 1-pt shift |
| Regime | `regime_multiplier`, `pre_regime` | changes when `reapply_regime_today` runs (see GUARD 5) |

**Live-vs-retired status per key:** only `cwwd_dampen` and `pcd_active` are enabled on v74; `cwcf_dampen` (⚠ RETIRED 2026-06-12 in v73, `CWCF_DAMPEN_K=0.0` — same dormancy as `wcf_lift`, GUARD 4), `wcf_lift`, `scw_dampen`, `mcd_dampen`, `ich_call_dampen`/`ich_put_lift`, `cont_lift`, `wvd_lift`/`wvd_dampen`, `ern_boost`, `sector_breadth_wave`, `daily_volume_authority_wave` are all disabled (`strategy_config.py` ⚠ RETIRED flags) — check [scoring-algorithm.md](../../docs/scoring-algorithm.md) before assuming any of these can fire.

`intraday_diagnostics.py` (`drill`/`rank_swings`/`symbol_day_diagnostic`, shared
by the CLI and API) sorts the candidate stage-deltas by magnitude and reports
the **dominant** one plus a `blurb`. It recognizes one named family today:
`wcf_boundary` (see GUARD 4 — dormant on the active version). **v74 has NO
post-`pre_boost` tail** (continuation-echo/WVD/DVAW/EARN_BOOST retired
2026-06-15) — on v74+ rows the `boost` stage candidates that can actually fire
are narrower than the historical key list above; check
[scoring-algorithm.md](../../docs/scoring-algorithm.md)'s ⚠ RETIRED banners for
which keys are live before chasing a dead one.

### 2.3 API surface (same underlying diagnostic)

`GET /api/stocks/<sym>/intraday-swing?date=YYYY-MM-DD` — returns the same
timeline+attribution JSON `symbol_day_diagnostic` produces (404 if no
snapshots for that symbol/date). The Dashboard `StockTable` shows a compact
`SwingBadge` (↕N, amber when fakeout) sourced from the `intraday_swing` field
on `GET /api/stocks/all`.

### 2.4 Known fakeout families — status as of 2026-07

| Family | Root cause | Status |
|---|---|---|
| WCF 27/28 cliff | binary score-gate toggled the full ~21.85-pt lift on a 1-pt wobble at 27/28 | **RAMPED v72** (`WCF_LIFT_RAMP_TOP=33`, smooth fade to 33) then the underlying lift **RETIRED v73** (`WCF_LIFT_K=0.0`) — dormant, not just smoothed, on the active version (GUARD 4) |
| Volume-amp ATI (CONVICTION↔THIN_AIR flip) | low-volume intraday rally toggled the volume-type classifier, moving `pre_regime` ~18.6pts | **RESOLVED**: `INTRADAY_TYPE_CONF_GATE=0.5` holds NEUTRAL until confidence is banked (`tests/test_volume_intraday_stability.py`) |
| Weekly transition whiplash (partial↔completed week) | mid-week recalc/live-update saw different weekly-envelope state | **CLOSED 2026-06-16** (known-issues Priority #7/#9) — tamed by the v69 PIT transition blend; residual instability ~5.4% recent / ~3% severe, per weatherization.md |

## 3. `trader simulate` — the fast dev-loop diff

```bash
trader simulate [SYM ...] [days] [--assess] [--compare] [--diff-assess]
```

`[SYM ...]` positional (default all). `[days]` bare int, default
`recalculate_scores.DEFAULT_LOOKBACK` = **365**. In-memory only, no DB writes —
this is `ScoreSimulator` under the hood (see
[/run-experiment](../run-experiment/SKILL.md) for the broader
no-env-gate/staging-native experiment discipline this tool belongs to).

- `--compare` → score-level diff vs DB (mean/median/max delta).
- `--assess` → runs assessment on the simulated scores.
- `--diff-assess` → **side-by-side assessment diff vs DB scores** — the
  fastest formula-change feedback loop.

**Metric display:** `--diff-assess` pipes through `print_diff_assessment`,
which already prints **WR15 as its first column**, alongside WR30/Ret30/
MAE30/MFE30/Cap30 (`assess_scores.py` `metrics` list, fixed 2026-04-17 in
`33353a3e1`) — read both columns directly from CLI output; no code edit
needed to see WR15.

## Version confusion — the reflex checklist

Run this whenever a number looks wrong and you're not 100% sure which
version produced it:

1. `python trader.py algorithm active` — prints what the pointer resolves to
   right now.
2. Which resolver did the tool you're reading use? `explain-scores`/
   `/intraday-swing` → active-scores pointer (GUARD 2). `assess` → HEAD-ish
   `get_or_create_current()`. Both accept `--version vNN`/`db:N`/git-hash via
   `assess_scores.resolve_algorithm_version` where the flag exists — pass it
   explicitly rather than trusting the default on anything but a clean,
   just-shipped checkout.
3. Dashboard/API consumer confusion: `GET /api/score/versions` returns the
   full version catalog + `active_version_id` for the version-selector
   dropdown (`ScoreVersionSelector`, wired on Dashboard/Allocator/Backtest/
   Assessment) — a user staring at unexpected numbers on the frontend is
   often just looking at a non-default entry in that dropdown, not a bug.
4. If a captured `score_intraday_logs` row's `weight_info` keys don't match
   what you expect for its tagged version, trust the JSON (GUARD 3) — don't
   "correct" it against the silo source.

## Evidence / see also

- [assessment-backtest.md](../../docs/assessment-backtest.md) — "Intraday
  Score Audit Log" (full schema/semantics), "Version resolution in assess",
  Score Simulator section (the metric-display gotcha in full).
- [scoring-algorithm.md](../../docs/scoring-algorithm.md) — every
  dampener/boost's live-vs-⚠RETIRED status, weekly adjustment mechanics, the
  `overall = regime_mult(boosts(dampeners(weighted_sum + weekly))))` pipeline
  in context.
- [known-issues.md](../../docs/known-issues.md) — Priority #7/#9 (weekly
  whiplash, CLOSED), the CURRENT SHIP STATE table for what's active right now.
- [/debug-pipeline](../debug-pipeline/SKILL.md) — update/recalc/MySQL/queue
  symptom index (this skill is score-content forensics, not pipeline health).
- [/run-assessment](../run-assessment/SKILL.md) — `trader assess` in depth
  (the positional-lookback trap, research packs, band-marginal-vs-cumulative
  reading).

## Self-update

If you hit a trap this skill missed, append it here (GUARDS or the fakeout
table) AND to [.claude/docs/traps.md](../../docs/traps.md) in the same
session.
