# Liquidity-Aware Cascade -- DESIGN.md (P3.6 / known-issues.md open-item #10)

> **2026-08-07:** superseded twice over — the 07-14 sweep closed ENGINE-BLOCKED (VERDICT.md),
> the corrected penalized re-run passed (`experiments/flatfile_exploitation/FF3_STAGEB_RESULTS.md`),
> and the full 13-consumer wiring shipped default-OFF that day. The "13-consumer wiring is NOT
> this pass's job" line below is historical.

**Status: DESIGN + feature built, PRE-REGISTERED before Stage-3 queue submission.**
Written 2026-07-14, ENGINE-WINDOW pass. FABLE judges the evidence this produces;
this document only fixes the hypothesis, the feature, and the grid *before* the
result is seen.

## 0. The row

gameplan.md P3.6: "per-signal `option_volume_30d` (real coverage 1.4y+, tagged
fallback before), `LIQUIDITY_FLOOR` skip, Stage-3 T1-T7 N=500x8; the expected
compound regression is the fill-realism fee, DD/collapse primary. Wire the
standard 13-consumer surface." known-issues.md #10 spec: nearest-ATM 30-DTE
call avg daily contract volume (30d), skip if below floor (guessed 100/50),
pre-2025 fallback = `Stock.avg_volume x 0.005`, tag fallback so it can be
excluded from gate evaluation if needed.

Precedent: `experiments/fundability_recost/FINDINGS.md` (2026-06-06) already
did a coarser version of this question using a full-history EQUITY-$-volume
proxy (not real option_prices) and found per-trade EV flat across liquidity
bins -- "Liquidity is NOT the binding constraint" for that proxy, and named
"the open Liquidity-Aware Cascade priority" (this row) as the precise
follow-up once `option_prices` had real coverage. That finding is a prior, not
a substitute -- it used dollar-volume-of-underlying, not contract volume.

## 1. Scope decisions (this pass)

- **CALLS ONLY.** Every live Stage-3 dampener (RXDD/SVR/MWDD/TVDD/BDIV/
  SPREAD_TILT) is calls-only precedent; puts are OFF in the live profile. No
  put-side resolution built.
- **30 DTE only** (`backtest_cascade.py` + `monte_carlo.py`). 15 DTE gets
  `LIQUIDITY_FLOOR=0` (inert) with no 15-DTE wiring this pass -- matches every
  other Stage-3 dampener's initial ship (RXDD/SVR/MWDD/TVDD/BDIV all shipped
  30-DTE-only first, `dte_15_wiring_mode='not_wired'`).
- **Default OFF (`LIQUIDITY_FLOOR=0.0`) = bit-identical production.** Env-gated
  for this A/B only. No `strategy_config.py` value changes -- see mechanism
  section below for the exact wiring shape and why the registry is untouched
  this pass.
- **Full 13-consumer wiring is explicitly NOT this pass's job** (api.py,
  trader.py CLI, frontend, docs, mechanism_registry.py entry) -- only the two
  engines the Stage-3 N=500 MC sweep actually exercises
  (`monte_carlo.py` + `backtest_cascade.py`).

## 2. Feature: `option_volume_30d`

Built by `experiments/liquidity_cascade/build_option_volume.py`. For every
75+ CALL signal (active version, full history 2016-now):

- **REAL** (signal date >= 2025-02-10, confirmed via `MIN(date)` on
  `option_prices`, 2026-07-14 -- table has ~90M rows, 776 symbols, 3.0M listed
  contracts): resolve the nearest-ATM call in the 20-45 DTE band (same band as
  the already-approved `experiments/iv_engine_pertrade/build_ledger.py
  select_contract`), then average that SAME contract's real daily volume over
  the trailing 30 calendar days.
- **FALLBACK** (pre-coverage, OR the real join found no chainable contract
  that day): `Stock.average_volume(to_date=signal_date, last_n_days=30) *
  0.005` -- the spec's rough options-fraction-of-equity-volume proxy. Computed
  via one bulk `price_history` pull + a polars rolling mean, not a per-signal
  ORM call (fallback signals are ~91% of the population -- see coverage below
  -- so this needed to be vectorized, not per-row).
- `is_fallback: bool` tagged on every row. Never silent.

### 2.1 Two resolution bugs found and fixed during validation

Both found by manually tracing specific (symbol, date) rows after the first
smoke run produced an implausible result (large, liquid names showing
`option_volume_30d == 0`), both would have silently biased the feature toward
near-zero on names that are NOT actually illiquid:

1. **Single combined strike+DTE distance let a same-strike cross-expiry tie
   resolve arbitrarily.** Weekly + monthly expiries commonly share the same
   round strike within a 20-45 DTE band. Picking "closest strike across ALL
   expiries" with an `option_id` tie-break could select a near-dead weekly
   over an actively-traded monthly at the identical strike. Confirmed on SCHW
   2025-02-13: strike=82 existed at DTE=22 (volume 0 every day in the trailing
   window, LOWER option_id, won the naive tie-break), DTE=29 (volume 23), and
   DTE=43 (volume 10). **Fix: resolve the expiry first, the strike second**
   (two-stage, not one combined metric).
2. **Nearest-calendar-DTE-to-30 is also the wrong expiry criterion.** Real
   options liquidity concentrates in the standard monthly cycle, not
   whichever expiry is numerically closest to a 30-day target. Confirmed on
   RTX 2025-02-27: the Mar-21 monthly (DTE=22) had open_interest in the
   hundreds/thousands per strike; the numerically-closer Mar-28 weekly
   (DTE=29) and Apr-4 (DTE=36) had single-digit open_interest and zero volume
   (freshly-listed, essentially untraded). **Fix: pick the expiry with the
   MOST total open_interest** (an empirical liquidity signal), tie-broken by
   DTE-nearness to 30 -- not assume calendar-nearness implies liquidity.

Both fixes are in `build_option_volume.py::_resolve_real`'s current code +
docstring. Reported numbers below are POST-fix.

### 2.2 Cross-check against already-approved production data

Independent validation, not just self-consistency: `.cache/iv_engine_pertrade/
ledger_v1.parquet` (2,900 rows, DESIGN.md "APPROVED AS AMENDED 2026-07-12,
FABLE", `selection_rule='nearest_moneyness_call_dte20_45'` -- same DTE band,
independently built resolver) shows **median single-day `entry_volume` = 0,
only 25.6% of resolved contracts have ANY volume > 0, only 22.1% have
volume >= 5**. This independently confirms that near-zero single-day/contract
volume for nearest-ATM 20-45-DTE individual-equity calls is a genuine,
already-established market characteristic in this exact universe -- not an
artifact of my resolver. (Their metric is single-day volume; mine is a
30-day trailing average, which is expected to run somewhat less zero-heavy
than a single day, matching what was actually observed.)

## 3. Coverage: real vs tagged-fallback

Full population: 9,209 75+ CALL signal-days, active version, 2016-01-06 to
2026-07-14. Of the 1,751 that fall on/after 2025-02-10 (real-coverage
window), only **851 resolved to an actual chainable contract** (the other 900
real-era-*dated* signals -- 51.4% of that window -- found no 20-45-DTE call
meeting the price/OI/IV sanity filters that day, typically newer/thinner
small-caps with sparse listed chains, and fall through to the fallback proxy
same as pre-2025 signals).

| | count | % of full population |
|---|---|---|
| **REAL** (resolved contract) | 851 | **9.2%** |
| **FALLBACK** (pre-coverage OR unresolved-in-window) | 8,358 | **90.8%** |

This is materially lower real-coverage than the naive "1.4y of 10y ~ 13%"
framing implies, because roughly half of even the in-window candidates don't
chain to a listed 20-45-DTE contract that day. Both numbers are exact counts
from a full (non-sampled) run of the real-era resolution path (1,751/1,751
processed) plus the full signal-date split (9,209 total, chunked-by-year
query, exact).

## 4. Distribution -- `option_volume_30d` (contracts/day), full real-era run (N=851)

| slice | n | p10 | p25 | median | <10 | <25 | <50 | <100 | <250 | <500 |
|---|---|---|---|---|---|---|---|---|---|---|
| real-only | 851 | 0.0 | 0.0 | 1.8 | 60% | 65% | 68% | 74% | 82% | 88% |
| fallback-only (N=2,651 partial sample, see note) | 2,651 | 1,314 | 3,580 | 9,613 | 1% | 1% | 1% | 1% | 3% | 4% |

**The real-only row is the full 851-contract population (authoritative).**
The fallback-only row is from a 2,651-signal partial sample (smoke-capped
during distribution-shape validation, not the full 8,358) -- adequate to
show the SHAPE and, critically, the SCALE gap, but not quoted as an exact
population statistic.

**Critical, pre-registered design fact: real contract-volume and the
fallback equity-proxy are ~3-4 orders of magnitude apart in scale** (real
median ~2 contracts/day; fallback median ~9,600 "contracts/day equivalent").
This is expected -- they are fundamentally different units dressed up as the
same feature name (true option contract count vs. 0.5% of equity share
volume) -- but it has a direct, important consequence for the floor grid and
the A/B's expected shape, below.

## 5. Floor grid (pre-registered)

Chosen from the REAL-only distribution (section 4), since that is the only
population a "contracts/day" floor is denominated in a way that means what it
says:

- **F0 = 0 (OFF)** -- baseline, bit-identical to production. Required arm.
- **F1 = 5** -- gentle. Excludes true near-zero/dead contracts only (between
  the p10=0 and p25=0 points and the visible mass at <10=60%; roughly
  mid-50s% of real-tagged signals by interpolation).
- **F2 = 20** -- moderate. Close to the <25 quantile point (65% excluded).
- **F3 = 100** -- severe, and the original known-issues #10 anchor value
  (its own guess of "100 contracts/day for 30 DTE"). On the ACTUAL measured
  distribution this excludes 74% of real-tagged signals -- far more
  aggressive than the row's own original "20-40% of the 95+ tier" expectation
  (that number came from `fundability_recost`'s EQUITY-volume proxy on the
  95+ subtier specifically, a different population and a different unit --
  not directly comparable, and superseded here by the real measurement).

F3 is kept in the grid deliberately despite being severe, for continuity with
the row's own anchor and so the A/B spans gentle -> severe rather than only
gentle/moderate.

### 5.1 Pre-registered expectation (per the row's own framing)

**"The expected compound regression is the fill-realism fee; DD/collapse is
primary."** Additionally, specific to this mechanism's coverage reality:

- Because the floor applies the SAME numeric threshold to both real and
  fallback-tagged values, and those are ~3-4 OOM apart in scale (section 4),
  **the floor will be near-a-no-op on fallback-tagged signals at every grid
  value** (F3=100 is still ~13-130x below the fallback distribution's p10 of
  1,314) and **will bind almost entirely on the ~9.2% of signals carrying a
  real resolved contract.**
- Consequence: **per-window effect should concentrate in whichever windows
  overlap 2025-02+** (the tail of "5y" and "22-now", and any window entirely
  in 2025+) and show **minimal-to-no effect on windows entirely before
  2025** (2021, 2022, 2023, 2024, dip, 2020_crash) -- this is an EXPECTED
  property of the coverage reality, not a mechanism failure, and should not
  be mistaken for "the floor doesn't work" when reading per-window results.
- Supply-cut magnitude at the level of ALL 75+ signals (not just the
  real-tagged 9.2%) is therefore small in aggregate terms even at F3: at most
  ~9.2% x 74% =~ 6.8% of all 75+ call signal-days could be touched by the
  severe arm, concentrated entirely in the most recent ~1.4y of coverage.
  This is a real, expected LIMITATION of what this pass's evidence can show
  cleanly across the full 9-window set -- it is not a full-history liquidity
  gate, it is a real-evidence-window gate with a fallback pass-through
  elsewhere. Flagged as a follow-up question in section 8, not solved here.

## 6. Mechanism wiring (env-gated, default inert)

Following the RXDD/SVR idiom exactly (`getattr(_cfg, 'NAME', literal_default)`
inside an `os.environ.get('NAME', ...)` read), in BOTH `monte_carlo.py` and
`backtest_cascade.py` (the two engines the Stage-3 MC sweep exercises):

```python
LIQUIDITY_FLOOR = float(os.environ.get('LIQUIDITY_FLOOR',
                        str(getattr(_cfg, 'LIQUIDITY_FLOOR', 0.0))))
```

`_cfg` (=`strategy_config.STRATEGY_30DTE`) has NO `LIQUIDITY_FLOOR` field this
pass -- `getattr`'s hardcoded literal fallback (`0.0`) degrades gracefully
exactly like the RXDD comment describes for a stale in-memory config, so this
line is forward-compatible with the eventual ship (add the field + a real
value to `strategy_config.py` later; this line does not change). **No
`strategy_config.py` edit this pass** -- satisfies "the floor ships OFF" via
the purest form: the config field doesn't exist yet, so there is nothing to
accidentally leave non-zero.

**Filter shape: a DROP from the signal list inside `load_signals`, not a
continuous alloc scale** -- mirrors the (retired but still-present-as-pattern)
`WEAK_WEEKLY_CALL_DROP` filter exactly (gated boolean, drop from `sigs`,
print a "dropped N/M" summary), matching the spec's literal language ("skip
signal"), not a soft de-weight. A dropped signal frees its cascade slot for
the next-ranked candidate that day, same as any other pre-filter.

The `option_volume_30d` lookup itself is a lazy memoized `{(symbol, date):
(value, is_fallback)}` map loaded from the parquet cache -- mirrors
`backtest_cascade.py`'s existing `_spread_tilt_load()` (SPREAD_TILT) exactly
for that file; `monte_carlo.py` needs a symbol->sym_id remap on load since its
signal objects carry `sym_id` not the string symbol (mirrors `_svr_load()`
there).

**`mechanism_registry.py` NOT touched this pass** (task's explicit
preference). This means `tests/test_mechanism_registry.py`'s soft
`_check_field_coverage` warning may fire once `LIQUIDITY_FLOOR` is wired as a
module constant with no registry entry -- confirmed non-fatal (warnings do
not fail the test, only `errors` do; see the test's own `main()`). If it
fires, it is expected and can be registered as `candidate`/`not_wired` +
reason at ship time, or immediately if it turns out to be needed to keep the
test's output clean.

## 7. Stage-3 evidence run

- **Shape:** T1-T7-style DD-primary A/B, N=500, paired seeds.
- **Windows:** the 9-window ship-validation set (2021, 2022, 2023, 2024, 2025
  dip, 22-now, 5y, plus 2020_crash as the mandatory screen).
- **Profiles:** Core + Apex.
- **Arms:** F0 (OFF) x F1(5) x F2(20) x F3(100) = 4 arms x 9 windows x 2
  profiles = 72 cells x N=500.
- **Primary reads:** 5y WorstDD delta (T4), per-window DD stability (T5),
  collapse rate (T6, must stay 0 given section 5.1's small aggregate
  supply-cut), compound OOM sanity (T7) -- NOT a WR/EV claim (Stage 1/2 are
  frozen; this is Stage 3 portfolio-only).
- **Given section 5.1:** do not expect a uniform per-window signal. A
  near-zero delta on pre-2025 windows is the PREDICTED result, not a null
  finding about the mechanism -- the honest read is on whichever
  window/profile cells actually have real-tagged supply in them.

## 8. Open questions / explicit follow-ups (not solved this pass)

- Should the floor apply a DIFFERENT (much smaller) threshold to
  fallback-tagged values, given the ~3-4 OOM scale gap, so the mechanism has
  a full-history economic effect rather than being concentrated in the
  2025+ tail? Not built this pass (task scope = single threshold concept);
  flagged as the most likely next design question if this pass's per-window
  read shows the effect too thin to evaluate cleanly.
- The 51.4% real-era-dated-but-unresolved rate (900/1,751) is itself
  interesting and not explained here beyond "sparser chains on
  newer/smaller names" -- not investigated further; the fallback proxy
  already covers it honestly (tagged), so it does not block this pass.
- 15 DTE coverage: not built. Follow the same `not_wired` pattern as every
  other Stage-3 dampener if/when the 15-DTE router needs it.

## 9. Artifacts

- `experiments/liquidity_cascade/build_option_volume.py` -- feature builder
  (this doc's sections 2-4 numbers).
- `.cache/experiment_data/liquidity_option_volume_30d*.parquet` -- cached
  output (gitignored; rebuild via the script, full run is DB-heavy, queue it).
- `experiments/liquidity_cascade/run_ab.py` -- the Stage-3 sweep runner
  (section 7).
