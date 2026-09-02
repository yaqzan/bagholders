---
name: run-assessment
description: Run and interpret barrier-touch assessment (trader assess), score-simulator diffs, and post-recalc research packs — the Stage 1 WR15-primary evidence layer behind every scoring ship decision. Use when the user asks to "assess scores", "check WR15", "run the assessment", "build a research pack", "check signal supply", "is this version accretive", or wants band/bucket win-rate tables read correctly (cumulative vs discrete, WR15 vs option-TP vs hold-TP).
---

# /run-assessment — assessment + research-pack evidence layer

`trader assess` (via `assess_scores.py`) computes vol-adjusted barrier-touch
outcomes per score bucket — the barrier-independent Stage 1 substrate every
`stage1_growth_gate.py` verdict and every ship claim is built on. This skill
covers running it correctly, reading its tables honestly, and building the
research-pack + supply/PRF trio that makes a version comparable/gateable.
**Read [assessment-backtest.md](../../docs/assessment-backtest.md) fully before
a real ship decision** — this skill is the runbook, that doc is the spec.

## GUARDS (read before running anything)

1. **`trader assess` takes POSITIONAL lookback only — there is no `--days`
   flag.** `trader assess --days 30` is silently parsed as symbol `"--DAYS"`
   (uppercased) + lookback `30`, finds 0 peaks for that "symbol", and looks
   like a clean run. Use `trader assess --force 30d` or `trader assess --force
   1095` (raw days also work; `Nd`/`Nw`/`Ny` suffix forms too). Confirmed in
   `assess_scores.py` "IMPORTANT: `--days` is NOT a valid CLI flag" note and in
   `trader.py`'s hand-rolled parser (any unrecognized token that fails
   `parse_lookback_arg` becomes `symbol = arg.upper()`).
2. **Version-resolution trap: `assess` targets HEAD, not the served version.**
   `assess_scores.run()` resolves the version via
   `AlgorithmVersion.get_or_create_current()` — the `ALGORITHM_VERSION`
   **file**/git HEAD — NOT `get_active_scores_version()` (what the API/dashboard
   serve). On a clean checkout at the shipped commit these are the same row.
   On a dirty tree, a new uncommitted commit, or an algorithm-refinement
   worktree, they can diverge — you'll assess the wrong version and not know
   it. **Pass `--version vNN` explicitly whenever the tree isn't guaranteed
   clean-at-HEAD**, or confirm first with `python trader.py algorithm active`.
   `--version` accepts `vNN` (production label), bare digits (legacy DB id),
   `db:N` (explicit DB id), or a git hash/unique prefix — resolved by
   `assess_scores.resolve_algorithm_version`.
3. **Band-marginal vs cumulative is not the same number — don't compare across
   the wrong one.** `assess_scores.py`'s cumulative buckets (`75+`, `80+`, …)
   pool every score above the floor; a promoted-into-75-79 candidate must beat
   the **75-79 discrete-band marginal**, not the `75+` cumulative pool (the
   cumulative number is diluted by 80-99 which never changes). Cumulative
   `75+` WR15/optTP sits well above the 75-79 marginal — e.g. `find-and-ship-alpha`'s
   GUARD 1 stamps `75-79 marginal ~0.61` vs `75+ cumulative ~0.81` as the
   canonical illustration of the gap (verify current numbers against the
   active version's research pack, not this timeless-looking pair). The old
   cumulative-only "H1" gate is **retired** for exactly this reason — it
   masked the ICH put-`<10` tail-tier loss under a flat-or-positive `<25`
   cumulative reading. Always read the **discrete `IC_BANDS`** row (every 5
   points: `95-100/90-94/85-89/80-84/75-79/70-74/…`) for a boundary decision.
4. **`print_diff_assessment` already prints WR15 as its first column** —
   `assess_scores.py:1093`'s `metrics` list has `('WR15', 'win_rate_15d', '.1f',
   '%')` as the leading entry, alongside WR30/Ret30/MAE30/MFE30/Cap30. No
   extension needed. If a future refactor drops it, `bucketed_stats[bucket]
   ['win_rate_15d']` is the fallback field to read directly from the returned
   dict. Judging a sweep/ablation by the printed WR30 column alone (ignoring
   the WR15 column that's already there) is the "old short-horizon overfit
   class" trap the doc calls out by name.
5. **Four different "win rate" surfaces answer different questions — don't
   conflate them.** (a) **WR15 generic** (K=2.0σ/M=5.0σ calls, K=1.0σ/M=2.0σ
   puts, DTE-agnostic) — the Stage 1 barrier-independent target; (b)
   **optTP15 / `30dte_opt`** (option-aligned TP+30%/SL-70% barrier,
   `SWING_K_LOW=1.274`/`M_LOW=1.092`-style monkeypatch in mining scripts) — the
   Stage 2/gate PRIMARY tradable metric, what `stage1_growth_gate.py`'s
   `g_option` actually gates on; (c) **holdTP15 / `30dte_apex`** — the funded
   Apex payoff predictand from `weatherization.md`'s Verification Substrate
   (skill-vs-baseline 0d gate); (d) **generic vs option BE differ**: call
   break-even ≈45.0%, put break-even ≈36.4% (held CONSTANT inside the growth
   gate's `ebar` so a sweep can't tune a barrier to win — verify current BE
   against `experiments/version_scorecard/STAGE1_GROWTH_GATE.md` before citing).
   A Stage 1 hypothesis is validated on (a); a shippability claim needs (b);
   a portfolio-scale skill claim needs (c). Mixing them is the "SVD
   generic-vs-option divergence" class of false-positive.
6. **`trader assess` with `--dte` omitted runs 30 DTE only** (`--dte` choices
   are exactly `15`/`30`/`both`; invalid value → red error + return, does NOT
   silently default). If you need 15 DTE research-path numbers, pass
   `--dte 15` or `--dte both` explicitly — the no-flag default will look
   complete (prints tables, exits 0) while quietly skipping 15 DTE.
7. **The version-scorecard triage tool (`score_versions.py`) has a HARDCODED
   version list (`v44…v66` as of 2026-07) — it will not show v70+ unless you
   pass real coverage.** It's a triage/leaderboard convenience, **not a ship
   gate** — never cite its ranking as evidence in a Stage 1 verdict. For a real
   gate decision use `stage1_growth_gate.py --baseline <b> --candidate <c>`
   with explicit version tokens (also has no live-auto-discovery default;
   pass your actual base/candidate).
8. **`signal_supply.py` must run BEFORE `stage1_growth_gate.py`, every time.**
   Without a supply row for the candidate, the growth gate falls back to
   `FALLBACK_COVERAGE=0.92` (rosy) and can **false-SHIP** an N-cutting
   candidate (the historical v63/BBLT false-SHIP). The gate now neutralizes +
   refuses to auto-SHIP on approximated supply, but still always run supply
   first — don't rely on the refusal as your only safety net.
9. **A version isn't comparable/gateable from the pack alone.** `signal_supply`
   and the PRF portfolio-response materialize now run **automatically inside**
   `build_research_pack.py` (default `--comparability` ON since 2026-06-15) —
   its final printed line is `comparability_unit=COMPLETE|INCOMPLETE`. If it
   prints `INCOMPLETE`, re-run the two named scripts it prints before trusting
   any downstream VersionCompare/gate reading for that version.

## 1. Running `trader assess`

Exact positional/flag grammar (verified against `trader.py`'s `assess` branch,
`~5096-5298`, and `assess_scores.py`):

```bash
# Full 5-window sweep (1y/2y/3y/5y/10y), 30 DTE, all profiles' temporal stats
python trader.py assess --force

# Single window only — always prefer this for a quick check; the no-lookback
# form does a single-pass 10y peak extraction then SLICES for the other 4
# windows (efficient), but still costs a full forward walk.
python trader.py assess --force 5y          # 5y, 30 DTE
python trader.py assess --force 1825        # same as above, raw days
python trader.py assess --force 3y --dte 15 # 3y, 15 DTE research path

# Explicit DTE / metric / profile / version control
python trader.py assess --force --dte both              # refresh 30 AND 15 DTE
python trader.py assess --force --metric tp              # option-TP only (skip wr pass)
python trader.py assess --force --profile core           # one portfolio profile's temporal rows
python trader.py assess --force --profiles all            # every Sentinel/Core/Apex profile
python trader.py assess --force --version v73             # target an explicit version, not HEAD
python trader.py assess AAPL 730                          # single symbol, no --force (read-only)
```

- `--dte` choices are **exactly** `('15','30','both')` — invalid value prints
  a red error and returns (no crash, no silent default).
- `--metric` choices are exactly `('wr','tp','both')`; omitted → runs both.
- `--profile`/`--profiles` normalized via `portfolio_profiles.normalize_profile_key`;
  `all` expands to every registered profile. When `--force` + no explicit
  lookback + no `--regime-adjust`: defaults to **all** profiles automatically;
  otherwise just the default profile. See [algorithm-version-silos.md](../../docs/algorithm-version-silos.md)
  for what "the default profile" resolves to today (teach the lookup, don't
  hardcode — it has moved before).
- Without `--force`, `assess` reads existing rows / runs a lighter pass; use
  `--force` whenever you need fresh numbers after a recalc.
- `--compare`, `--meta`, `--regime-adjust` are flags (see `trader.py` for full
  behavior); not covered further here — they're diagnostic, not gate inputs.
- **`--dte`/`--metric` build a combo list** via `strategy_config.assess_combos()`
  with dedup logic — `('15','tp')` may alias `('30','tp')` internally and get
  skipped; this is expected, not a bug.
- When `--force` and no explicit lookback and no `--regime-adjust`: backtest
  temporal stats are ALSO refreshed per DTE × profile after the assess loop
  (so a full `--force` run is effectively `assess` + `temporal-refresh`
  combined — you don't need to run `temporal-refresh` separately after it,
  only after a portfolio-stage-only change where you skip the score assess).

**Cost note:** a `--dte both` run does 30 DTE first (cache-served, ~3 min),
then 15 DTE (cache-bypassed since K/M differ from the cached barriers, ~30-60
min full forward walk). **Queue it** — this is minutes+ compute per
CLAUDE.md's queue-everything rule:

```bash
python trader.py queue submit --priority high --db heavy --cpu 4 \
  --dedup assess-v74-5y --reason "Stage 1 WR15 validation post-recalc" \
  -- python trader.py assess --force 5y --version v74
python trader.py queue wait <id>   # run with harness run_in_background:true
```

## 2. What `assess` computes (the barrier methodology, briefly)

Per peak, a single forward walk checks periods `{1d,3d,5d,7d,15d,30d,60d,90d}`
(**calendar days**, not trading bars) for whether intraday high/low touches a
vol-scaled target before a vol-scaled stop. σ = 60-day realized daily stdev.

| Cell | K (target) | M (stop) | Code constant name (inverted!) |
|---|---:|---:|---|
| HIGH / calls | 2.0σ | 5.0σ | `SWING_K_LOW` / `SWING_M_LOW` |
| LOW / puts | 1.0σ | 2.0σ | `SWING_K_HIGH` / `SWING_M_HIGH` |

**Naming caveat:** `assess_scores.py` names calls' constants `..._LOW` and
puts' `..._HIGH` — the inverse of the HIGH/LOW cell they belong to. Match by
*value* (2.0/5.0 = calls), never by the constant name.

At WR15 (the current primary target), effective barriers are K_eff=1.41σ /
M_eff=3.54σ (scaled `K·σ·√(W/30)` off the 30d reference). Two parallel views
exist per period: **scaled** (default, target grows with W) and **unscaled**
(fixed target, monotonically non-decreasing WR across W — a monotonicity
violation flags a stale/partial assessment run, not a code bug).

**Score bucketing**: 12 cumulative buckets — calls `95+/90+/85+/80+/75+/70+`,
puts `<30/<25/<20/<15/<10/<5`; plus non-overlapping discrete `IC_BANDS` every
5 points for intra-band Pearson IC and per-bucket non-regression (W4). Full
metric glossary (avg_mae/mfe, capture_ratio, shakeout_depth, MFE-percentile TP
anchors) is in assessment-backtest.md — don't duplicate it here; look it up
when you need a specific column's definition.

## 3. Score-Simulator diffs (`ScoreSimulator` / `trader simulate`)

For a fast in-memory formula-change feedback loop without touching the DB:

```bash
trader simulate [SYMBOL ...] [days] [--assess] [--compare] [--diff-assess]
```

`ScoreSimulator(symbols, lookback_days, scoring_fn)` bulk-loads once and runs
the scoring pipeline in memory (`scoring_fn=None` replays the checkout's real
formula — the "staging-native" pattern; pass a callable for a quick probe).
`.diff_assess(sim_scores, symbol)` is the fastest side-by-side formula-change
signal — it already prints WR15 as its first column (GUARD 4), no extension
needed. Full pattern catalog (worktree vs staging-native vs
`ScoringFn` override vs runtime monkey-patch with `try/finally`) is in
assessment-backtest.md "Testing scoring hypotheses without polluting
production" — read it before building a new sweep harness; most of the
boilerplate (LHS sampler, holdout gate, JSONL logging) is already solved in
an exemplar script under `experiments/`.

## 4. Research packs — the comparability unit

A version is not comparable across the dashboard's VersionCompare view or
gateable by `stage1_growth_gate.py` until three things exist together
(automated as of 2026-06-15 — the pack build now runs the other two itself):

```bash
python tools/build_research_pack.py --version vNN --run-portfolio-windows
```

This is a 15-line shim (`tools/build_research_pack.py`) around the real
argparse in `algorithm_versions/research_pack.py:main` — confirmed flags:
`--version` (default active), `--out-dir`, `--lookbacks` (default
`365,730,1095,1825,3650`), `--periods` (default `3d,5d,7d,15d,30d,60d,90d`),
`--dte` (default `30`), `--profiles` (default `all` — deliberately, so the
version shows up under every profile toggle, not just the profile the last
builder happened to pass), `--portfolio-windows` (default all named windows),
`--run-portfolio-windows` (flag, needed for the stress-window JSONs),
`--comparability`/`--no-comparability` (default **ON**).

With `--comparability` ON (the default — don't pass `--no-comparability`
unless bulk-backfilling many versions where you'll run supply/PRF separately
in batch), the pack build **auto-chains**:
1. `signal_supply.py --versions vNN` (writes the recycle_coverage row this
   version needs — GUARD 8)
2. `portfolio_response.py --materialize vNN` (PRF matched-sizing — the
   supply-conditioned portfolio instrument, and the seeded first candidate for
   any post-ship Stage-3 retune)

Its **last printed line** is the thing to check:
```
comparability_unit=COMPLETE (pack=ok supply=ok prf=ok)
```
If it prints `INCOMPLETE`, the line names exactly which of the two follow-up
scripts to re-run — do that before treating the version as gateable for
anything (VersionCompare will show it blank/wrong on other profile toggles
otherwise — the historical "v70-v72 VersionCompare-blank gap").

**Layout** (confirmed under `.cache/algorithm_versions/v74/research_pack/`,
as of 2026-07) splits into two groups:
- **auto-produced by `build_research_pack.py` + `--comparability`:**
  `manifest.json`, `assessment_coverage.json`, `score_coverage.json`,
  `parquet_manifest.json`, `utility_5y_wr15.json`, `utility_5y_wr7.json`,
  `utility_by_horizon.json`, `temporal_summary.json`, `derived_portfolio.json`
  (the PRF output), `stress_windows.json` + per-profile
  `stress_windows_{apex,core,sentinel}.json`.
- **produced SEPARATELY by `python experiments/skill_vs_baseline/verify_scorecard.py`**
  (not auto-chained by the pack build — its absence does NOT trip the
  `INCOMPLETE` check): `verify_scorecard.json` (the weatherization 0d
  skill-vs-baseline gate output). Run this manually for a new version before
  citing the funded `30dte_apex` predictand.

Filenames are stable across versions; contents are not — always read the
target version's own directory, never assume a cross-version diff without
re-checking both exist.

**Backfilling packs across many versions** without recalculating anything:
```bash
python tools/backfill_research_packs.py --versions v70,v71,v72 --run-portfolio-windows
# or a range:
python tools/backfill_research_packs.py --min-version 70 --max-version 74 \
  --run-temporal --run-portfolio-windows
```
Own 400-line argparse (`--profiles` here defaults to resolving to `sentinel`
alone, NOT `all` like the single-pack builder — pass `--profiles all`
explicitly if you want full-profile coverage across the backfill range).
Weekend-safe; never recalculates scores itself — it only hydrates assessment
rows + packs for versions whose score rows already exist.

## 5. Reading the tables honestly

- **Cumulative vs discrete** — see GUARD 3. A promotion/demotion decision at a
  band boundary (e.g. "should this cohort move from 70-74 into 75-79")
  compares against the **discrete** band it would land in, never the
  cumulative pool above it.
- **Which WR/TP column** — see GUARD 5. Stage 1 hypothesis validation reads
  generic WR15; a shippability/gate claim reads option-TP (`optTP15` /
  `30dte_opt`); a portfolio-skill claim reads the funded `30dte_apex`
  predictand via `verify_scorecard.json`.
- **Pre-v69 numbers are look-ahead-inflated by ~12pp.** Any assessment table
  or archived FINDINGS.md from before the 2026-05-31 v69 weekly-transition-blend
  ship measured a version with a look-ahead weekly-feature bug; if you're
  quoting a historical WR table for context, say so explicitly — do not treat
  pre-v69 numbers as comparable to current honest-era numbers.
- **Break-evens**: call ≈45.0%, put ≈36.4% (the constants the growth gate
  holds fixed) — verify current values against
  `experiments/version_scorecard/STAGE1_GROWTH_GATE.md` before quoting; they
  are calibration constants, not physical laws, and could be recalibrated.
- **`explain-scores` output** (`trader explain-scores [SYM...] [days]`) is the
  per-signal drill-down companion to the aggregate tables — 2 lines per
  signal (overall+components+verdict, then raw indicator values). Filters by
  the *active* version (same as API/dashboard), not HEAD — the opposite
  convention from `assess` (GUARD 2); if you see duplicate rows for the same
  date, that's a scoring-pipeline bug (multiple score rows for the active
  version), not a display quirk. Verdicts: `CORRECT` / `BAD_LUCK` (wrong
  outcome, indicators confirmed) / `MISS` (wrong outcome, indicators neutral
  or contradicting) / `PENDING` (insufficient forward data).

## 6. Version-scorecard triage (score_versions.py) — triage, NOT a gate

```bash
python experiments/version_scorecard/signal_supply.py --versions v60,v74
python experiments/version_scorecard/score_versions.py
```

Produces a 5-pillar z-scored leaderboard (`Q` per-trade WR15 quality, `H`
hydration, `G` growth, `R` recency, `S` stability) plus per-profile composite
rankings. **Useful for "which of these N versions looks healthiest at a
glance," never for "should I ship."** GUARD 7: its `VERSIONS` list is a
hardcoded literal (`v44` through `v66` as of 2026-07) — it silently omits
every version shipped after that unless you edit the list or pass coverage
some other way; don't read an absence from the leaderboard as "this version
is worse," it may just not be in the list.

For an actual gate verdict, always go to `stage1_growth_gate.py` with explicit
tokens (see GUARDS 7-8 and `/ship-gates`):
```bash
python experiments/version_scorecard/signal_supply.py --versions v73,v74
python experiments/version_scorecard/stage1_growth_gate.py --baseline v73 --candidate v74
```
`--selftest` regression-tests the gate itself (synthesizes SHIP/FLAG/BLOCK/W4
cases from any live pack); **never `--replay`** — the documented historical
replay anchors (e.g. v40→v42 BLOCK) no longer exist on disk, overwritten by
the 2026-06 honest-era recalcs.

## Evidence / see also

- [assessment-backtest.md](../../docs/assessment-backtest.md) — full barrier
  methodology, metric glossary, Three-Stage Calibration Framework (this
  skill's GUARDS are the operational distillation, not a replacement).
- [weatherization.md](../../docs/weatherization.md) — the `30dte_apex`
  Verification Substrate, 0d skill-vs-baseline gate (`verify_scorecard.py`),
  why FLAG='risk-shaper' is the accepted honest-era verdict.
- `/ship-gates` — the full W1-W6/B1-B5/T1-T7 gate operationalization this
  skill's evidence feeds into.
- `/find-and-ship-alpha` GUARD 1-2 — the option-TP-vs-cumulative and
  real-supply traps in their Stage 1 mining context.
- `experiments/version_scorecard/STAGE1_GROWTH_GATE.md` — growth-gate
  mechanics + current calibration constants (BE, demand, saturation).
- `experiments/rqc_v60/eval.py` — worked example of building a synthetic
  discrete-band-replacement ledger for a boundary-promotion candidate.

## Self-update

If you hit a trap this skill missed while running an assessment, diff-reading
a table, or building a research pack, append it here (as a new numbered
GUARD) and to [traps.md](../../docs/traps.md) in the same session.
