# PREREG — Assessment TP% dual-anchor instrument (2026-08-10)

STATUS: **LOCKED 2026-08-10** (design decided from source recon before any code edited).
This is a mini-PREREG for an instrumentation change, not an alpha-mining sweep — there is
no cohort/WR hypothesis under test. What's "pre-registered" is the WIRING DESIGN (metric
key, scope, anchor values, what stays untouched), so a future reader can verify the ship
matches the plan instead of re-deriving it from a diff.

## 0. What & why

The 2026-08-10 TP/SL ship (`experiments/tpsl_refine_2026_08/`) moved the LIVE Core/Apex
canon to calls TP+10%/SL−100% ("scalp-and-dead-hold"). `assess_scores.py`'s option-aligned
`('30','tp')` preset was deliberately left at its Phase-H5-era anchors (TP+35%/SL−30% →
k_low=1.274σ/m_low=1.092σ) because that family feeds the W1-W6 Stage-1 ship gates and ~40
closed-axis verdicts in `known-issues.md`/memory cite it at those anchors — moving it out
from under those citations would silently invalidate historical evidence. `src/pages/
Assessment.js` was patched same-day (`5745fcde`) to label those anchors as a frozen
measurement instrument, not the live strategy, and flagged the re-anchor question as OPEN
(`known-issues.md` lines ~76-83).

This ships that open question: a NEW, ADDITIVE metric variant that measures option-TP% at
the CURRENT live canon, alongside the untouched legacy anchor — dual-anchor, not
replace-anchor.

## 1. Design decisions (locked)

1. **New metric key: `tp26`.** `ScoreAssessmentRun.metric` is `CharField(max_length=4)`
   (`database/models/core.py:1943`) — a naive `'tp2026'` key is 6 chars and would silently
   truncate/collide at the DB layer (MySQL truncates a too-long VARCHAR insert rather than
   erroring, unless strict mode is on — either way, not something to discover post-hoc).
   `tp26` fits the ceiling exactly and reads unambiguously next to `wr`/`tp`.
2. **Scope: 30-DTE only, active version only.** The formula given is 30-DTE (`calls: TP
   0.10 × PREMIUM_MULT 1.82 / DELTA 0.5 = 0.364σ`); both LIVE profiles (Core, Apex) are
   30-DTE as of the 2026-08-02 P0.3 switch, so this is the only surface that needs a new
   anchor. `STRATEGY_15DTE`'s option params were NOT touched by the 2026-08-10 ship (still
   Phase 15B C1 — TP=0.35/SL=−0.30), so a `('15','tp26')` entry would be either fabricated
   or byte-identical to `('15','tp')` — not added. No historical-version backfill: `tp26`
   is computed for v74 only, matching the task's "recompute for the active version only."
3. **Legacy `tp` stays byte-identical.** No edit to the existing `('30','tp')` /
   `('15','tp')` preset entries, no edit to `research_pack.py`'s `for metric in ("wr",
   "tp")` readiness loop (that loop defines the comparability-unit contract the W1-W6 /
   growth-gate tooling reads — `tp26` is deliberately outside it). **Gates keep reading
   LEGACY** until a separate, explicitly ratified gate migration — this task does not
   touch `stage1_growth_gate.py`, `signal_supply.py`, or any W1-W6 threshold.
4. **Puts: anchors kept, unchanged, documented why.** `OPT_30DTE.PUT_TP=0.35` /
   `PUT_SL=−0.20` were NOT touched by the 2026-08-10 ship (puts are off portfolio-wide;
   only calls moved) — so `tp26`'s put side (`k_high`/`m_high`) is IDENTICAL to legacy
   `tp`'s put side. This is not a shortcut, it's the honest anchor: nothing moved.

## 2. Anchor computation (cross-checked against live `strategy_config.py`)

σ target = `TP_or_SL_on_premium × PREMIUM_MULT / DELTA`. Live `OPT_30DTE` (`strategy_config.py:769-821`,
verified by direct read 2026-08-10): `TP_BASE=TP_STRESS=0.10`, `SL_BASE=SL_STRESS=−1.00`,
`PUT_TP=0.35`, `PUT_SL=−0.20`, `PREMIUM_MULT=1.82` (`STRATEGY_30DTE`), `DELTA=0.50`.

| Side | Field | Premium % | σ (= pct × 1.82 / 0.5) | vs legacy `tp` |
|---|---|---:|---:|---|
| CALL (k_low) target | TP_BASE | +10% | **0.364σ** | was 1.274σ — far closer target |
| CALL (m_low) stop | \|SL_BASE\| | 100% | **3.64σ** | was 1.092σ — far wider stop |
| PUT (k_high) target | PUT_TP | 35% | **1.274σ** | unchanged (puts not retuned) |
| PUT (m_high) stop | \|PUT_SL\| | 20% | **0.728σ** | unchanged (puts not retuned) |

New `DTE_METRIC_PRESETS` entry (`assess_scores.py`):
```python
('30', 'tp26'): {  # 30 DTE option TP% — 2026-08-10 TP/SL retune canon (Core/Apex, calls)
    'k_high': 1.274, 'm_high': 0.728,   # PUT TP=0.35 / SL=-0.20 (unchanged — puts off portfolio-wide)
    'k_low':  0.364, 'm_low':  3.64,    # CALL TP_BASE=0.10 / SL_BASE=-1.00 ("scalp-and-dead-hold")
    'reference_days': 30,
},
```

**Sanity/falsification checks to run post-compute** (this is the closest thing this task
has to a hypothesis test — it's an instrument, so the check is "does it measure what it
claims," not "does it find alpha"):
- **Put-side parity**: `tp26` and `tp` put buckets (`<30..<5`) must be **bit-identical** at
  every period/window — same peaks, same barriers (k_high/m_high unchanged), so this is a
  hard invariant, not a noisy comparison. Any divergence = a wiring bug, not signal.
- **Call-side direction**: `tp26`'s call-side win rate MUST be materially HIGHER than
  legacy `tp` at matched periods — the target shrank 3.5× (1.274σ→0.364σ) and the stop
  widened 3.3× (1.092σ→3.64σ), both moves make TP strictly easier to hit first. A tp26
  call win rate at or below legacy `tp` is a broken-wiring signal, not a real result worth
  investigating on its merits.
- **Option P&L populated**: `avg_option_pnl_15d` etc. must be non-null on `tp26` rows (see
  trap below — this needs a code fix, not just a new preset entry).

## 3. Traps caught in recon (would have silently broken the ship if missed)

- **`set_dte_strategy`'s option-P&L gate is a literal `== 'tp'` check**, not membership in
  "any option metric" (`assess_scores.py:182`, as of 2026-08-10): `if str(metric) == 'tp':`
  sets `OPTION_TOTAL_DTE`/`OPTION_HOLD_DAYS`/`OPTION_PREM_MULT`; anything else — including
  a new `'tp26'` — falls to the `else` branch and zeroes them, which silently disables
  `_compute_option_pnl()` (no crash, just null `avg_option_pnl_*` fields forever). Adding
  the preset dict entry alone is NOT sufficient; this line must become
  `if str(metric) in ('tp', 'tp26'):`.
- **`trader.py`'s `--metric` CLI dispatch hardcodes the wr/tp pair twice**: once as the
  input validator (`must be wr, tp, or both`) and once in the combo-builder (~5426-5429:
  `if metric_explicit is None or metric_explicit in ('wr','both'): ... if ... in ('tp',
  'both'): ...`). Passing `--metric tp26` without touching this second block builds an
  EMPTY `_combos` list — the assess loop iterates zero times, exits 0, prints nothing,
  computes nothing. Silent no-op, not an error.
- **Frontend `tpCacheKey` has no metric dimension** (`` `${dteStrategy}_${selectedVersionId
  ?? ''}` ``, `Assessment.js:1905`) because today's fetch always hardcodes `metric=tp`.
  Adding an anchor toggle without adding the anchor to this key means switching anchors at
  fixed (dte, version) reads the OTHER anchor's cached response — a real, user-visible bug,
  not a style nit.
- **`ScoreAssessmentMeta` is NOT a general (dte, metric) cache** — it's a `(version,
  bucket)`-only fast path serving exactly `(dte=30, metric=wr)` (`api.py:2596-2599`
  routing comment). Every other combo, `tp` included, already aggregates fresh from
  `ScoreAssessmentRun`+`ScoreAssessmentResult` filtered by `(dte_strategy, metric)`. This
  means `tp26` needs ZERO new persistence/cache code — it rides the exact code path `tp`
  already uses. The only api.py change is widening the `metric not in ('wr','tp')` guard.

## 4. Wiring checklist

| File | Change |
|---|---|
| `assess_scores.py` | new `('30','tp26')` preset entry; `== 'tp'` → `in ('tp','tp26')` option-P&L gate; comment touch-ups |
| `api.py` | `metric not in ('wr','tp')` → add `'tp26'` (~line 2627) |
| `trader.py` | `--metric` validator + dedicated dispatch branch (bypass the hardcoded wr/tp combo-builder) + help comment |
| `src/pages/Assessment.js` | new `tpAnchor` toggle state (`'tp'`\|`'tp26'`, default `'tp'` — no default-UI change), `tpCacheKey` + the second effect's `activeTpKey` both gain the anchor dimension, both TP fetch call sites pass `tpAnchor` as `metric`, provenance copy extended (not reverted) with parallel `tp26` framing, disabled+auto-reset when DTE=15 (no canon values exist there) |
| `.claude/docs/assessment-backtest.md` | anchor definitions + explicit "gates read legacy" statement |
| `.claude/docs/known-issues.md` | resolve the 2026-08-10 open-question entry (append-dated, don't rewrite) |

**Explicitly NOT touched**: `research_pack.py` (readiness loop stays `("wr","tp")`),
`stage1_growth_gate.py`, `signal_supply.py`, any W1-W6 threshold, `strategy_config.py`
(read-only — this task consumes the live canon, doesn't move it), any `algorithm_versions/*`
historical silo (frozen snapshots; GitNexus's own impact scan on `set_dte_strategy` reports
148 "affected" symbols / risk CRITICAL, but 146 of those are duplicate hits inside frozen
`algorithm_versions/v*/portfolio_sources/trader.py` copies that will never execute again —
the real live blast radius is 2 call sites, both in `assess_scores.py`, both already
generic on the `metric` string).

## 5. Compute plan

Target version: **v74** (`f9fb7b934`, verified live via `trader algorithm active`
2026-08-10 — not assumed from docs, per the HEAD-pointer-vs-active-scores trap).

```bash
trader queue submit --priority high --db heavy --restartable \
  --dedup assess-tp26-v74-2026-08-10 \
  --reason "tp26 dual-anchor instrument recompute, v74, 30 DTE only" \
  -- python trader.py assess --force --metric tp26 --version v74
```
No explicit lookback → runs the consolidated 1y/2y/3y/5y/10y pass (matches how legacy `tp`
is populated). `--db heavy` / `--priority high` per the standing compute doctrine (past
market hours at ship time → `high` is the default, no `trader update` to protect).

## 6. Evaluation — ACTUAL RESULT (2026-08-10, task #414, exit 0, runtime 6m31s)

Job completed clean. `/api/assessment?metric=tp26&version=v74` returns 12 non-empty
buckets. The three §2 sanity checks were re-run against the DB directly (bypassing the
API's "pick the largest available window" auto-selection, which was initially comparing a
30y `tp` window against a 10y `tp26` window — see below) at the one window both metrics
have freshly and simultaneously (`lookback_days=1825` / 5y, `tp` run_id=726, `tp26`
run_id=738, both computed 2026-08-10 within an 11-minute span):

- **Call-side direction: CONFIRMED, and bigger than predicted.** WR15 jumped from ~50% (legacy
  `tp`) to ~87-90% (`tp26`) on every call bucket. This is correctly explained, not alarming:
  the pure-random-walk baseline for this barrier shape is `M/(K+M) = 3.64/(0.364+3.64) =
  90.9%` — the observed 87-90% sits just BELOW that baseline, exactly as expected for a
  barrier this asymmetric (near target, far stop). `avg_return_15d` flips NEGATIVE under
  `tp26` (e.g. 70+: +0.18 → −0.22) despite the high win rate — a high-hit-rate/thin-or-negative
  per-trade-EV signature that matches `experiments/tpsl_refine_2026_08/LESSONS.md`'s own
  finding for this exact TP10/SL100 config ("per-trade edge is razor-thin (~0.7%/cycle)") —
  an independent cross-check the instrument is measuring the right thing, not a red flag.
- **Put-side "parity": REVISED — not bit-identical, and now understood why.** At the matched
  5y window, N is nearly equal (63,944 vs 63,880 — the ~0.1% gap is ordinary live-data drift
  between two runs 11 minutes apart) but WR15/avg_return differ by a small, consistent
  1.2–2.4pp across every put bucket. Root cause (traced via source, not guessed): `tp26`'s
  full (k_high, m_high, k_low, m_low) tuple doesn't match any of the 4 entries in
  `database/barrier_cache.py`'s `BARRIER_SETS` (`30dte_generic`/`15dte_opt`/`30dte_opt`/
  `30dte_apex`), so `_detect_barrier_set()` returns `None` and `calculate_forward_returns`
  falls back to the uncached fresh forward walk — while legacy `tp` matches `30dte_opt`
  exactly and is served from the SQLite/DuckDB cache (confirmed 27 min old at comparison
  time, i.e. NOT stale by any nightly-rebuild measure). `_swing_walk` itself was read in full
  and confirmed side-independent — `SWING_K_LOW`/`SWING_M_LOW` (the call-side values, the
  only ones that differ between `tp`/`tp26`) are never referenced when walking a put-side
  ('high') peak — so the barrier FORMULA is proven byte-identical for puts; the residual
  1.2-2.4pp is the cached-path vs fresh-walk-path implementations disagreeing slightly on
  the same nominal barriers, a PRE-EXISTING property of the cache/fresh-walk split that
  predates this task and would affect any future custom (uncached) barrier combo, not
  something introduced by `tp26`. Reconciling the two implementations, or adding a 5th
  `BARRIER_SETS` entry so `tp26` rides the cache, is out of scope here (`barrier_cache.py`
  was never on this task's touch list) — flagged as a follow-up, not fixed.
- **`avg_option_pnl`: REVISED — the check itself was wrong, not the code.** Both `tp` and
  `tp26` rows return no `avg_option_pnl_*` value from the API — but this is because
  `ScoreAssessmentResult`'s peewee model (`database/models/core.py`) has NO
  `avg_option_pnl_*` column at all; the value is computed in-memory during the assess run
  (confirmed via the `set_dte_strategy` smoke test in §Traps: `OPTION_TOTAL_DTE`/
  `OPTION_HOLD_DAYS`/`OPTION_PREM_MULT` all correctly populate for `tp26`) but was NEVER
  persisted to the DB or served via this API path for EITHER metric — a pre-existing gap
  in the persistence layer, identical for `tp` and `tp26`, not something the gate fix could
  have "unlocked" at the DB/API level. The gate fix's real, verified effect is keeping
  `_compute_option_pnl` active with the correct DTE/hold/premium context for any in-memory
  consumer (e.g. `assess_peaks_in_memory`, `simulator.py diff_assess`) — proven correct by
  the local smoke test, not by this DB round-trip.

**↳ ADDENDUM 2026-08-10 (follow-up investigation — `CACHE_VS_WALK_FINDINGS.md`).** The
cache-vs-fresh-walk divergence flagged above was traced to its mechanism. Two claims in the
put-side bullet are **corrected**:
- The residual is not diffuse "slight disagreement" — **369 of 369 win-rate mismatches are
  same-bar double touches**, where the cache tests the target first and returns `win`
  unconditionally while `_swing_walk` breaks the tie on close direction. The cache is
  systematically OPTIMISTIC, by +1.87pp / +2.28pp on two disjoint samples. This biases every
  cached `wr`/`tp` number ever produced, not just this comparison.
- The ~0.1% `total_peaks` gap is **not "ordinary live-data drift"** — it is deterministic. The
  fresh walk gates each period on window-reachability *before* walking and so discards
  already-decided outcomes at the trailing edge of every window.

Neither changes this section's SHIP verdict (both are pre-existing properties of the
cache/walk split, orthogonal to the tp26 wiring) — but the "drift" attribution was wrong and
the honest read is that `tp26`'s fresh-walked put side is the *less* biased of the two.

**↳ CORRECTION 2026-08-10 (post-D4 recompute — task #439, exit 0, 5m56s).** The D4 expire
mark-out bug was fixed (`_swing_walk` now prices an expire at the last bar INSIDE the window,
mirroring `barrier_cache._walk_outcome`). Because `tp26` is uncached, **every number in this
section was produced by the buggy walk**, so all five windows were recomputed and diffed
against a snapshot of the pre-fix rows
(`tp26_prefix_baseline_runs735-739.csv`, this directory).

**Result: the figures quoted above stand.** Across all 5 windows × 12 buckets:

| quantity | change |
|---|---|
| `sample_count` (all 60 rows) | **identical** — incl. 5y `<30` N=63,880 and `total_peaks` 78,592 |
| `win_rate_15d` (all 60 rows) | **identical to the stored 0.1pp** — as predicted, an expire is a non-win under both conventions, so D4 cannot move a win rate |
| `avg_return_15d` | **9 of 60 rows moved, every one by exactly ±0.01pp** — the resolution floor, since `assess_scores.py:1250` rounds to 2dp |
| **the 5y window this section cites (run 738)** | **zero change on every bucket.** `70+` stays `−0.22`; the `+0.18 → −0.22` call-side flip quoted above is unaffected |

So the parent FINDINGS' D4 estimate ("moves `avg_return_15d` by ~0.05pp") was **~5× too high** —
it came from a 12-symbol sample; full-universe the effect is at or under the storage floor.
Corroborated independently by `walk_expire_diff.py` (16-symbol attribution harness, tp26 call
side: −0.0015pp, expire share 0.6%). D4 was worth fixing because the fresh walk was pricing a
hard sell one bar outside the window it claimed to measure — **not** because it was moving any
published number materially. Nothing in §6's reasoning or the SHIP verdict changes.

*(D5 does not touch `tp26` at all — `tp26` never reads the cache. D1 remains open and is the
only one of the four that would move these numbers, by design not touched here.)*

**Verdict: SHIP.** The two "FAIL" conditions originally written for this section were built
on an incorrect assumption (that `tp` and `tp26` would traverse the identical cache-backed
code path) — with that corrected, every observation above is fully explained by either (a)
the intended barrier change (calls) or (b) a pre-existing, orthogonal architectural property
of the codebase (puts' cache-bypass; the missing `avg_option_pnl` column) that this task did
not introduce and was never scoped to fix.

## 7. Explicitly out of scope

- Does not change which anchor family any Stage-1 gate reads.
- Does not backfill `tp26` for any version other than the active one.
- Does not add a 15-DTE `tp26` entry (no new information to encode there).
- Does not touch `TP_CALL_BE`/`TP_PUT_BE` break-even constants in `Assessment.js` (shared
  across both DTE toggles already, not metric-specific in the current design — out of
  scope for an anchor-instrument addition).
