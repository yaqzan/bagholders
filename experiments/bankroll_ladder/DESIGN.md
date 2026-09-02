# LADDER_MC -- design and modeling record

**Built:** 2026-07-25 | **Implements:** `CHARTER.md` section 4 (engine spec) and section 6 step S2
(engine build + validation arm). **Status:** engine + validation arm + selftest GREEN, one smoke
config run end-to-end. **No sweep has been run.** Nothing here changes the live book, the profiles,
or any scoring version.

Entry point: `experiments/bankroll_ladder/ladder_mc.py`

---

## 1. What this is

A replenishing-bankroll Monte Carlo: a $2,000 account receiving +$2,000 every month, trading the
v74 75+ CALL signals with **integer option contracts**, asking *how many months until it reaches
$20,000, and does that beat just saving the money?*

The objective is the CHARTER's, not the main book's, and is **pre-registered -- not re-derived here**:

| | |
|---|---|
| primary | distribution of months-to-$20,000, with P(reach within T) curves |
| null | the **savings stream** (same contribution schedule, zero trading), **computed**, never hardcoded |
| FUNDABLE | median months <= **0.70 x** null months **AND** P(beat null) >= **60%** |
| ruin | **non-absorbing** and **priced**: equity ~ 0 costs one month of waiting, not the program |
| drawdown | a **DIAGNOSTIC**. `collapse=0` is deliberately NOT imported. |

---

## 2. Architecture -- what is reused vs what is new

**`monte_carlo.py` is treated as READ-ONLY and was never edited.** (Windows MP workers re-import
modules from disk on every pool creation; a worker dying on a mid-edit file makes `pool.map` block
forever with a 0-byte log.)

Reused verbatim -- *all* trade physics:

| Imported | Role |
|---|---|
| `load_signals`, `load_put_signals`, `load_price_history`, `load_breadth_map` | data |
| `compute_trade_outcome` (via `precompute_outcomes`) | sigma-barrier detection, premium model, DTE scaling, dead-hold pre-walk |
| `resolve(outcome, rng)` | **the P&L physics**: seeded bounded random fill, bimodal SL fill, vega sampling, dead-hold override, asymmetric percentage slippage |
| `_prepare_window` | the entire ctx build |
| `_stable_label_seed` | seeding, so the validation arm can run on exactly monte_carlo's seeds |

New here -- **only the capital loop**: integer contracts, affordability + cascade-down, the monthly
contribution stream and its pause rule, the cheap-end dollar cost overlay, non-absorbing ruin
accounting, stop-at-checkpoint, the in-calendar savings null, and pooled monthly-roll starts.

### 2.1 One prepare for the whole history

`call_outcomes` is keyed `(symbol_id, signal_date)` and is window-independent, so **one**
`_prepare_window` over 2016-06-01 .. 2026-04-15 serves every monthly-roll start; a start is just a
slice of `trading_days`. That is ~82x fewer precomputes than preparing per start
(measured: prepare 22.8 s, simulate 1.3 ms/path).

Puts are stripped (`load_put_signals` patched to `[]` for the duration of the prepare, restored in a
`finally`). v74 is puts-off (`MAX_POSITIONS_PUT=0`, put tier allocs 0), so this is behaviourally
neutral; it also keeps the rng stream free of the put-sort draws, which matters for bit-exactness.

### 2.2 Module-level constants (trap #4)

`monte_carlo` reads its config as module-level constants **at import time**. Every knob the ladder
varies (DTE, TP, SL, dead-hold) is therefore written to `os.environ` in `bootstrap_env()` **before**
`import monte_carlo`. The validation arm additionally patches module attributes in-process and
restores every one of them in a `finally` block. Nothing is left dangling.

Consequence for sweeps: `(dte, tp, sl, sl_dead_hold, hold_cal_days)` are import-time. A `--mode screen`
process may only contain cells that agree on those five; the CLI refuses otherwise. `n_slots`,
`slot_frac`, `spread_floor`, and `instrument` are runtime and can be swept freely within one process
against a single prepare.

---

## 3. The ladder capital loop

Day order mirrors `run_single_sim` so the two remain comparable:

1. settle every position whose exit bar has arrived
2. **(new)** at a month boundary: credit +$2,000 (subject to the pause rule); roll the burned-month
   and tranche-ruin counters
3. mark equity, update peak/DD, **checkpoint test** (stop) -- collapse-break is OFF by design
4. rank the day's candidates and fill slots

**Equity is marked at cost** (`cash + sum(position basis)`), exactly as `run_single_sim` does. An
open winner therefore does not count toward $20k until it closes -- conservative and comparable.

### 3.1 Integer contracts and the cascade (CHARTER 4.1)

```
contract_cost_total = 100 * (premium_per_share + entry_spread_overlay) + fee_per_contract
slot_budget         = slot_frac * equity_at_day_start          (capped by cash)
n_contracts         = floor(slot_budget / contract_cost_total)
n_contracts == 0    -> UNAFFORDABLE: count it, skip it, CASCADE DOWN to the next-best signal
```

`unaffordable_skip_rate` is reported per cell. In the smoke (2 slots, 50% each, from $2k) **~34% of
ranked 75+ candidates were unbuyable** -- the core new physics the existing fractional MC cannot see.

**The allocation base is fixed at day-start equity for every fill that day**, exactly as monte_carlo
does. Re-deriving it after each fill is algebraically identical but not bit-identical
(`cash + sum(basis)` re-rounds), and that single ulp flips the boundary case where a fill exactly
exhausts cash. It silently dropped trades monte_carlo takes -- see section 6.

`budget_mode`: the ladder **clamps** the slot budget to available cash (a small account buys fewer
contracts); the validation arm uses `'skip'`, monte_carlo's rule (an unaffordable slot is skipped
outright, never sized down).

### 3.2 Cheap-end execution model (CHARTER 4.3)

```
spread_dollars_per_share = max(PCT_SPREAD * premium_per_share, FLOOR_DOLLARS)
fees                     = $0.65 per contract, per leg (entry AND exit)
```

with the repo's **asymmetric cost canon** deciding how much of the pct spread each leg crosses:

| leg | pct spread crossed | floor | fee |
|---|---|---|---|
| entry (mid fill) | 0.0 | yes | yes |
| exit via limit TP (or dead-hold popout) | 0.0 | yes | yes |
| exit FORCED (SL / hard / expiry) | 0.5 | yes | yes |

**No double counting.** `resolve()` already returns pnl net of `SLIP_ENTRY + SLIP_{TP|SL|HARD}`,
which *is* the house percentage spread (`SLIP_SL = SLIP_HARD = -0.015 = 0.5 x 0.03`). The ladder
therefore charges only the **excess**:

```
overlay_per_share = max(0, target_spread_dollars - |SLIP_leg| * premium_per_share)
```

Properties, each asserted in `--selftest`:
* with `floor = 0` the overlay is identically 0 on both legs -> the validation arm stays bit-exact;
* with a binding floor (`$0.05` on a `$0.40` contract = 12.5%) the floor dominates and is charged;
* the TP leg's overlay is the **full** floor (SLIP_TP charges nothing), the forced leg's is the floor
  **minus what SLIP already took** -- the asymmetry survives the overlay.

`PCT_SPREAD` defaults to `0.03` because that is the full spread the shipped `SLIP_SL = -0.015`
half-spread implies. `FLOOR_DOLLARS` is the swept axis {0.03, 0.05, 0.10}, **per share** (so x100 per
contract). A contract whose exit value is under $1.00 is treated as expiring, not sold: no exit
spread, no exit fee.

`proceeds_floor_zero` (default **on**): a long option cannot return negative cash. `resolve()` can
emit `pnl = -1.015` once SLIP is added to a total loss; monte_carlo lets that go negative. Honest for
a $2k account, so the ladder floors it -- and the validation arm turns it off to match monte_carlo.

### 3.3 Contributions and the pause rule (CHARTER section 5)

+$2,000 at each month boundary, **paused while equity >= `stage_ceiling`** (default = the $20,000
checkpoint). The **savings null uses the identical rule on the identical calendar** -- fairness
symmetry, pinned before any sweep.

The null is computed by `savings_null_on_calendar()` **outside** the trading loop. This is
deliberate: the traded arm stops the moment it hits the checkpoint, so tracking the null inline
censors it exactly when the strategy wins, silently turning "beat savings by one month" into "savings
never got there". (That bug was live in the first smoke; the 2020_crash equity row showed a blank
null.) A pure `savings_null_schedule()` is also exposed and cross-checked against the in-path value
in `--selftest`.

**Beating the null** = reaching the checkpoint *strictly* sooner. Matching it is not beating it. A
censored strategy never beats. A strategy that reaches while the null is still censored always beats.

### 3.4 Non-absorbing ruin

The run never terminates on equity. Two diagnostics:

* **`months_burned`** -- a month with zero fills *and* at least one unaffordable skip: the account
  could not buy anything it wanted and simply waited for the next tranche. (Rare in practice: with
  2 slots and ~30k signals there is nearly always *something* affordable.)
* **`tranche_loss_rate`** -- the share of months that **opened with less equity than one $2,000
  contribution**, i.e. the previous tranche was effectively consumed. This is the CHARTER's "priced
  input" read and it is far more informative than the burned-month counter: the smoke option cell
  shows **36.9%** of months opening below one tranche vs **4.2%** for the equity arm, while both
  report `months_burned = 0`.

### 3.5 Stop at checkpoint / censoring

A path ends when equity >= $20,000 (record the month) or when the horizon (36 months) is exhausted
(censored). Months are reported as continuous calendar months (`days / 30.4375`) for both the
strategy and the null, computed on the same calendar, so the ratio is well-defined.

---

## 4. Start sampling

Pooled **monthly-roll** starts (the `experiments/concentration_2x/` pattern): every month-start in
the available history is a separate run and results pool, so the metric means "starting at a random
time, how long", not "starting in 2024, how long".

A start is kept only if it has the **full horizon of runway** (`--min-runway-months`, default = the
horizon). Mixing in starts with 8 months of tape left censors them *mechanically* and inflates the
censoring rate. Over 2016-06-01 .. 2026-04-15 with a 36-month horizon that yields **82 starts**
(path counts per bucket = starts x `--n-iter`):

| bucket | definition | starts |
|---|---|---|
| `2020_crash` | start in 2020-02-01 .. 2020-04-30 | 3 |
| `2022` | start year 2022 | 12 |
| `bull` | everything else | 67 |

Results are reported **pooled and split by start-regime** -- the ugly tapes are where ruin gets
priced. `--max-starts` (smoke only) samples by **even stride, never the head**: a head slice of a
monthly roll is all 2016-2018 bull tape and would hide 2020_crash and 2022 entirely.

---

## 5. Sweep axes

Exposed as config fields: `dte` {15, 30} primary and {7} as a probe arm, `n_slots` {1, 2, 3},
`slot_frac` {0.33, 0.50, 1.00}, `tp` {+0.30, +0.50, +1.00}, `sl` {-0.50, -0.70, dead-hold-analog},
`spread_floor` {0.03, 0.05, 0.10}.

**DTE scaling is not re-implemented.** It rides monte_carlo's own `CALENDAR_HOLD` +
`NOMINAL_CAL_DTE` mechanism inside `compute_trade_outcome` (ATM premium ~ `sqrt(DTE/30)`, and the
sigma barriers scale identically), set via env before import. `hold_cal_days` scales proportionally
from the shipped 30-DTE value of 27 calendar days, and is overridable.

**dead-hold-analog SL:** `DEAD_HOLD_ENABLED=1` with `DEAD_HOLD_TRIGGER_PNL=0.0`, so the SL barrier
still fires but the position is never liquidated into it -- every SL fire becomes the pre-computed
popout-or-expiry walk. That is literally the dead-hold analog, using the shipped mechanism.

### 5.1 EXCLUDED by documented prior result -- implemented nowhere

* **OTM strikes are OUT -- ATM only.** The OTM cheap-explosive ladder was tested 2026-07-20
  (`experiments/bankroll_ladder/otm_replay/`) on real prints and **PARKED**: slightly-OTM
  (5-10%) / 16-30 DTE / +400% TP cuts time-to-tier from ~10 to ~6-7 months **in sample** and
  **collapses out of sample**, including on a clean walk-forward. The honesty rails caught an
  in-sample-selected tail. Re-read is scheduled for Dec-2026, not here.
* **45-DTE is OUT.** The DTE axis above 30 was closed permanently 2026-07-14
  (`experiments/apex_dte_dd/DTE45_VERDICT.md`).

---

## 6. Validation arm -- how the harness was proved

`--mode validate` runs a **degenerate** ladder: $500,000 start, contributions OFF, spread floor $0,
fees $0, flat slot sizing, `budget_mode='skip'`, `proceeds_floor_zero=False`.

The reference is **`monte_carlo.run_single_sim` itself**, on the *same ctx* and the *same seeds*
(`1000 * _stable_label_seed(window) + it`), with the sizing stack neutralised down to
`premium_cost = equity x slot_frac`. Every dampener is a multiplicative scale on `alloc_frac`, so each
is patched to 1.0 in-process and restored in a `finally`: `alloc_scale_for` (F3F/regime), the DD soft
band, opportunity saturation, the practical-exposure caps, RXDD / MWDD / TVDD / DQT / VXMD / BDIV,
SVR, SPREAD_TILT, the aggression wave, realloc, and the collapse break. **`monte_carlo.py` itself is
never touched.**

Two arms:

* **arm A -- ladder FRACTIONAL:** must be **bit-exact**. This is the gate. It isolates the new capital
  loop from the physics: identical eligibility filter, identical primary/overflow split, identical
  sort keys *including the per-candidate `rng.random()` tiebreak draws*, identical cash tests,
  identical exit bars, identical end-of-window liquidation.
* **arm B -- ladder INTEGER contracts:** informational, quantifies the granularity effect at large
  capital.

**Result (2026-07-25):**

| window | config | arm A bit-exact | arm A trade-count match | arm B median per-seed deviation |
|---|---|---|---|---|
| 2024 | 4 slots x 25% (100% gross) | **100/100** | 100/100 | 24.7% (artifact -- see below) |
| 2024 | 4 slots x 15% (60% gross) | **100/100** | 100/100 | **0.37%** (worst 1.1%) |
| 2022 | 4 slots x 15% (60% gross) | **100/100** | 100/100 | **0.24%** |

Arm A reproduces monte_carlo **exactly** -- same finals to 0.000e+00 relative deviation, same trade
counts, and (verified separately by a per-trade tape diff) the same 195-row trade tape.

**The 4 x 25% arm-B artifact is a config knife-edge, not a granularity effect.** At exactly 100%
gross the 4th slot needs cash equal to the last cent of the budget; the fractional reference fails
that test about as often as it passes it, while integer flooring always leaves headroom, so the
integer arm gets *systematically more* 4th fills. Drop gross to 60% and the deviation collapses to
0.37%. `--validate-slot-frac` therefore defaults to 0.15.

### 6.1 Bugs the validation arm caught (all were real)

1. **Collapse-break liquidation.** `run_single_sim` liquidates the open book even when it breaks
   early on the collapse rule; the first ladder implementation did not.
2. **Float round-trip on the fractional premium.** Computing `basis = (budget/(100*prem))*100*prem`
   can land 1 ulp **above** `budget`, failing the `> cash` test on the fill that exactly exhausts
   cash and silently dropping a trade monte_carlo takes. Fixed by assigning `basis = budget`.
3. **Per-fill equity recomputation.** Re-deriving the allocation base after each fill is
   algebraically identical to monte_carlo's fixed day-start `portfolio_value` but not bit-identical;
   the same knife-edge flipped. Fixed by hoisting `alloc_base`.

Each was found by diffing per-trade tapes (`MC_TRADE_TAPE` on the reference vs a tape hook on the
ladder) down to the first divergent row, not by eyeballing aggregates. Aggregates agreed to ~1% while
the per-seed paths were completely different -- worth remembering.

---

## 7. The EQUITY arm (buy shares, not calls)

Added as the affordability control and the honest risk framing: at $2,000 the integer-**contract**
granularity that makes options awkward largely disappears with **shares**.

**Same signal set, same entry dates, same underlying sigma barriers, same max hold.** Only the
instrument changes.

| | option arm | equity arm |
|---|---|---|
| unit | 1 contract = 100 shares | 1 share |
| size | `floor(slot_budget / contract_cost_total)` | `floor(slot_budget / share_price)` |
| P&L | `monte_carlo.resolve` (delta + theta + vega, dead-hold) | pure underlying return |
| TP exit | limit at the sigma TP barrier, option P&L | resting limit fills **at** the sigma TP barrier |
| SL exit | bimodal stop fill, then option P&L | same bimodal stop: intraday trigger fills at the barrier, gap-through fills `Uniform(low, open)` |
| `both` | `Uniform(low, high)` | `Uniform(low, high)` |
| time stop | day-`HOLD_CAL_DAYS` close, hard-sell mark | the same deadline close -- **no expiry, no theta** |
| costs | dollar spread floor + $0.65/contract/leg | **$0 commission** + `equity_slippage_bps` per leg (default **5 bps** of notional) |

**Barrier mapping (the important bit):** monte_carlo derives `tp_level` / `sl_level` as
`entry * (1 +/- SIGMA * realized_vol * sqrt(DTE/30))`, where `TP_SIGMA = TP * PREMIUM_MULT / DELTA`.
Those levels **are** the underlying moves the option TP/SL correspond to, and they are already in the
precomputed outcome dict. The equity arm reads the *same* levels off the *same* outcome, so a "+30%
option TP" and the equity TP are the same underlying event by construction -- which is what makes the
two arms apples-to-apples rather than two different strategies.

`resolve_equity()` reuses monte_carlo's **calibrated** bounded-fill semantics (they were fitted on
124,872 real underlying bars, so they transfer to shares directly) and draws the same number of rng
values in the corresponding branch, so paired seeds keep the arms comparable instead of drifting on
stream alignment. Dead-hold does not apply (no expiry). Positions still open at the horizon settle at
their resolved barrier/deadline return rather than at the option hard-sell mark.

### 7.1 Apples-to-apples: verified, not assumed

**Barrier mapping.** monte_carlo derives the underlying trigger levels as

```
TP_SIGMA = TP x PREMIUM_MULT / DELTA = 0.30 x 1.82 / 0.5 = 1.092 sigma
SL_SIGMA = |SL| x PREMIUM_MULT / DELTA = 0.70 x 1.82 / 0.5 = 2.548 sigma
tp_level = entry x (1 + TP_SIGMA x realized_vol x sqrt(DTE/30))
sl_level = entry x (1 - SL_SIGMA x realized_vol x sqrt(DTE/30))
```

so a "+30% option TP at 30 DTE" IS "underlying +1.092 sigma" (sigma = 60-day realized daily vol),
and "-70% option SL" IS "underlying -2.548 sigma". **The equity arm reads those exact
`fire_tp_level` / `fire_sl_level` values off the same precomputed outcome dict** -- it does not
recompute them and cannot drift from them. The TP/SL config fields therefore mean the same physical
event in both arms; only the payoff attached to that event differs.

**Same signal set, same slot cadence -- measured.** Running both arms with the checkpoint disabled
(so neither stops early and both run all 82 starts x 36 months):

| cell | fills/path | candidates considered | unaffordable | TP% | SL% | HARD% |
|---|---|---|---|---|---|---|
| option, 2 slots x 50% | 337.4 | 549.0 | **38.5%** | 71.5% | 0.0% | 18.3% |
| equity, 2 slots x 50% | 346.7 | 346.7 | **0.0%** | 70.1% | 26.5% | 2.7% |

* **fill counts agree to 2.7%** -> identical slot cadence and identical hold windows (both use the
  same `exit_bar` from the same outcome);
* **TP rates agree to 1.4pp** (71.5% vs 70.1%) -> both arms are triggering on the same barrier
  events, as the shared-levels construction requires;
* the option arm must look at **549** candidates to fill **337** because **38.5% are unbuyable**;
  the equity arm fills essentially everything it looks at. That gap is the finding, not a confound;
* the SL/HARD split differs *only* because of **dead-hold**: in the option arm SL fires are converted
  to `dh_expiry`/`dh_pop` (SL reads 0.0%, HARD 18.3%, the rest popouts), while the equity arm has no
  expiry so its stop fires stay `sl` (26.5%).

**Why the equity arm shows FEWER fills in the headline run** (89.8 vs 157.4): it is not a cadence
difference. The equity arm **reaches $20k and stops at a 9.00-month median** while the option arm
runs 14.72 months (and 8.4% of its paths never reach, running the full 36). Fill ratio 0.57 vs month
ratio 0.61 -- the paths are simply shorter. With the checkpoint disabled the counts converge (table
above).

**So the 9.00-month equity median is on the same 75+ v74 signal set, the same ranking (ct_call first,
then score-descending with the same rng tiebreak), the same slot count, the same hold windows, and
the same barrier events as the option arm.** The only differences are the intended ones: instrument,
sizing granularity, and cost model.

**5 bps** per leg is the chosen slippage: a round trip costs 10 bps of notional, which is a
deliberately generous-but-not-fantasy figure for liquid large-caps at a $2k account size. It is a
parameter (`equity_slippage_bps`), so a pessimism arm is one field away.

---

## 8. Smoke result (N=20, NOT a verdict)

82 pooled monthly-roll starts x 20 iterations = 1,640 paths per cell. **This is a shakedown, not
evidence** -- no N floor, no pessimism cert, one config each.

| instrument | cell | median months | ratio vs null | P(beat null) | P(<=12m) | tranche-ruin | median DD | FUNDABLE |
|---|---|---|---|---|---|---|---|---|
| SAVINGS | null (zero trading) | 8.97 | 1.00 | -- | -- | 0.0% | 0.0% | ref |
| equity | 2 slots x 50% | 9.00 | 1.00 | 31.4% | 94.6% | 4.2% | 14.5% | no |
| option | 2 slots x 50%, 30 DTE, TP+30 / SL-70, floor $0.05 | 14.72 | 1.64 | 26.2% | 36.5% | 36.9% | 98.7% | no |

Both smoke cells **FAIL** the pre-registered FUNDABLE bar (needs ratio <= 0.70 **and** P(beat) >= 60%).

Reading, with the caveat that this is one arbitrary config each:

* the equity arm is **far safer and slightly slower**: it tracks the savings null almost exactly
  (ratio 1.00) with 14.5% median DD and 4.2% tranche-ruin, and 94.6% of paths reach $20k inside 12
  months -- the *distribution* is tight, which is the real difference;
* the option arm is **slower and vastly riskier at this config**: median 14.72 months vs a 8.97-month
  null, 98.7% median drawdown, 36.9% of months opening below one tranche, and a median
  net-of-contributions of **-$11,062** (the account reaches $20k mostly because $2k/month keeps
  arriving);
* the option arm's upside is real but thin at this config: P(reach within 6 months) is 14.9% vs
  **0.0%** for equities and 0.0% for savings -- nothing but options can beat the schedule early;
* by start-regime the option arm is 25.6 months median from a 2022 start (ratio 2.85, P(beat) 1.2%)
  vs 9.4 from a 2020_crash start -- i.e. the ugly-tape penalty is a **grinding bear**, not a crash;
* ~34% of ranked 75+ candidates were **unaffordable** at a $1,000 slot from a $2k account -- the
  physics the fractional MC cannot see, and it costs the option arm its best-ranked names.

The single most likely reason the option arm looks this bad is that the smoke config
(2 slots x 50% = 100% gross, continuously deployed, TP+30/SL-70) is the *funded* book's shape, not a
sprint shape. That is exactly what the S3 grid exists to place.

---

## 9. The LOCKED S3 screen grid

Generated by `make_s3_grid.py` into `experiments/bankroll_ladder/results/s3_grid/`.
**171 cells, no additions:**

| axis | values | kind |
|---|---|---|
| `dte` | 15, 30 | IMPORT-TIME |
| `tp` | +0.30, +0.50, +1.00 | IMPORT-TIME |
| `sl` | -0.50, -0.70, dead-hold-analog | IMPORT-TIME |
| `n_slots` | 1, 2, 3 | free |
| `slot_frac` | 0.33, 0.50, 1.00 | free |
| `spread_floor` | **0.05 fixed** (swept on the winner only, CHARTER 4.3) | free |
| `instrument` | `option` (162 cells) + `equity` (9 cells) | free |

= 2 x 3 x 3 = **18 barrier groups** x 9 option cells = **162 option cells**, plus the **9 equity
cells** (n_slots x slot_frac) carried inside the baseline group `d30_tp30_sl70`. The equity cells
cost no extra prepare: `instrument` is a free axis and the equity arm reads the SAME sigma barriers
off the SAME precomputed outcomes, which is what keeps the comparison honest (section 7.1).

N=300, full-runway starts (82), standard savings null.

**One process per barrier group is mandatory, not stylistic:** `(dte, tp, sl, sl_dead_hold)` are
module-level monte_carlo constants read at import. The `--mode grid` driver re-execs one subprocess
per group and skips groups whose output already exists, so a preemption costs at most one group.

## 9.1 How to run it

Single command, submit-ready (the caller submits; this harness never self-submits):

```bash
python experiments/bankroll_ladder/make_s3_grid.py          # regenerate the 18 group configs

PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python experiments/bankroll_ladder/ladder_mc.py --mode grid     --groups-dir experiments/bankroll_ladder/results/s3_grid     --n-iter 300 --workers 8 --parallel-groups 2
```

It prints a merged, FUNDABLE-first ranked table at the end. To re-merge later without recomputing:

```bash
python experiments/bankroll_ladder/ladder_mc.py     --merge 'experiments/bankroll_ladder/results/s3_grid/out_*.json'
```

Per-group logs land in `results/s3_grid/log_<group>.txt`; per-group results in `out_<group>.json`.

## 9.2 Measured cost (not extrapolated from a smoke)

Timed on this box with a real 18-cell group at N=100, `--workers 8`:

| quantity | measured |
|---|---|
| prepare (once per group process) | **22.7 s** (31,020 call outcomes, full 2016-2026 history) |
| per cell, N=100 (8,200 paths) | **3.7 s** (0.45 ms/path) |
| per cell, N=20 (1,640 paths) | 2.15 s |
| fitted | **1.76 s fixed/cell + 0.239 ms/path** |
| **per cell, N=300 (24,600 paths)** | **~7.6 s** |
| **peak RSS per group process tree** | **1.15 GB** at `--workers 8` (parent + 8 workers, each holding a pickled ctx copy) |

Wall time for the full 171-cell grid at N=300:

| `--parallel-groups` | wall | peak RSS | cores |
|---|---|---|---|
| 1 (serial) | **~29 min** | 1.2 GB | 8 |
| **2 (recommended)** | **~15 min** | **~2.3 GB** | 16 |
| 4 | ~8 min | ~4.6 GB | 32 (whole box) |

Group of 9 cells = 22.7 s prepare + 9 x 7.6 s = **~1.5 min**; the 18-cell baseline group = ~2.7 min.

**This is ~7x cheaper than the 1.7 h estimate derived from the earlier smoke.** That smoke ran at
N=20, where the ~1.76 s/cell fixed pool-startup cost dominates and inflates ms/path by ~5x. At N=300
the fixed cost amortizes and the true marginal rate is 0.24 ms/path.

Queue sizing: `--cpu 16 --db heavy` for `--parallel-groups 2`. `--db heavy` is correct even though
the job is ~90% CPU: each group process opens with a genuine full-history MySQL scan (10y of scores
plus price history for ~700 symbols plus breadth/regime/earnings), and at most 2 of those run
concurrently under this setting.

## 9.3 Cost-control side-run (zero-cost decomposition)

Side-run into `results/cost_control/`, separate from the main grid. Cell =
**`opt_d15_tp50_sl50_s3_f100`** (dte 15, TP +50%, SL -50%, 3 slots x 100%), the best-performing
option cell across the first 4 completed S3 groups by BOTH median months and medNET$. Same 82 starts,
same paired seeds, N=300. Arm 1 reproduces the grid cell **exactly** (13.21 / 1.47 / 29.1% /
-$9,205), confirming the side-run is on the same footing.

`zero_slip` zeroes monte_carlo's percentage slippage through its OWN env overrides
(`SLIP_ENTRY_OV` / `SLIP_TP_OV` / `SLIP_SL_OV` / `SLIP_HARD_OV`) -- **monte_carlo.py was not
touched**, so arm 3 was runnable. `NET_HARD_SELL` is derived from those at import, so the
end-of-horizon mark follows automatically. `zero_slip` is an IMPORT-TIME axis and is part of the
process-group key; arm 3 therefore ran in its own process.

Note: zeroing SLIP alone does NOT make a run frictionless -- the ladder's no-double-count overlay
then picks up the full pct spread instead (correct by construction). A true frictionless arm needs
`pct_spread=0`, `spread_floor=0`, `fee_per_contract=0` AND `zero_slip=1`. Arm 3 reports
**TOTAL COST/path = $0 exactly**, which verifies it.

| arm | floor | fee | SLIP | fees$ | overlay$ | slip$ | **TOTAL$** | **medNET$** | medMo | ratio | P(beat) | FUND |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 as-shipped | 0.05 | 0.65 | on | 3,018 | 23,053 | 827 | **26,898** | **-9,205** | 13.21 | 1.47 | 29.1% | no |
| 2 fees only | 0.00 | 0.65 | on | 3,691 | 0 | 828 | **4,519** | **+3,986** | 8.97 | 1.00 | 47.6% | no |
| 3 frictionless | 0.00 | 0.00 | OFF | 0 | 0 | 0 | **0** | **+5,816** | 8.25 | 0.92 | 51.4% | no |

savings null = 8.97 months, ratio 1.00 by definition.

### The verdict is a THIRD outcome, not either pre-stated branch

* **Frictionless does NOT clear the pre-registered bar.** ratio 0.92 (needs <= 0.70) and P(beat null)
  51.4% (needs >= 60%). A zero-cost 15-DTE option ladder is a **coin flip against a savings account**
  -- with 100% median drawdown and 80% of months opening below one tranche. So "the strategy is wrong
  for a $2k account" is **supported**.
* **But cost is NOT a secondary aggravator.** It accounts for the ENTIRE distance between a coin flip
  (0.92) and a clear loss (1.47), and flips medNET$ from +$5,816 to -$9,205. So "the cheap-end
  microstructure is the whole story" is **also partly supported** -- it is the single dominant lever.

Both claims are partly true. The pre-registered bar is the arbiter, and **arm 3 fails it**, so this
study does not license "options work at $2k once microstructure is fixed". Fewer/larger positions in
higher-priced contracts would recover most of the ~$22.4k/path the floor costs, but lands at a coin
flip, not at fundable.

### Where the $23,053 overlay actually comes from

The coordinator's arithmetic was directionally right but ~4x low per contract, because it counted
only the FORCED-exit leg. The brief specifies the floor applies on **both** legs:

* **entry leg**: `SLIP_ENTRY = 0`, so the entry always pays the **full** floor -> 2,614 contracts x
  $5.00 = **$13,070 (57% of the overlay)**;
* **TP-exit leg**: `SLIP_TP = 0` too, so a limit TP also pays the **full** $5.00 -- and 54.8% of exits
  are TP;
* **forced-exit leg**: only here does SLIP pre-pay part of it (`0.015 x premium`), leaving
  `max(0, floor - 0.015 x prem)` -- the ~$2.30/contract the coordinator computed.

Total exit leg = $9,983 (43%). $13,070 + $9,983 = $23,053, reconciling to the reported figure.

### The mechanism is ADVERSE SELECTION INTO CHEAP CONTRACTS

Mean premium actually traded: **$227,141 / 2,614 contracts = $86.9/contract = $0.87 per share.**
(The coordinator's p25 estimate of ~$1.80/share is ~2x too high.) At $0.87/share:

* a $0.05 floor is **5.7% per leg / ~11.5% round trip**;
* measured total cost is **11.84% of all premium traded** (fees add ~1.5%).

This is **CHARTER 2.3's hypothesis confirmed more sharply than stated** -- "a $0.05 spread on a $0.40
contract is 12.5%". And it is not an average effect: **the affordability cascade actively selects
into it.** When the top-ranked signal is unbuyable the ladder cascades DOWN to a cheaper contract, so
a $2k account systematically ends up holding exactly the contracts where a fixed dollar floor is most
punitive. Turnover compounds it: $227k of premium traded against $37k of lifetime contributions
(6.1x), so an 11.8% round-trip friction becomes **72.6% of every dollar ever deposited**.

### What is NOT explained by cost

`trnchRn` is 80.0% / 80.6% / 83.5% and median DD is 100.0% in **all three** arms. Removing 100% of
friction changes the speed and the sign of medNET$ but leaves the path destructiveness untouched.
That is independent evidence that the config shape (3 slots x 100% of equity, 15 DTE) is wrong for
this account, not merely expensive.

### Caveat on the regime split -- do NOT read it as a bear-market edge

| arm | 2020_crash starts | 2022 starts | bull starts |
|---|---|---|---|
| 1 as-shipped | 9.5 / 1.06 / 27% / +$2,801 | 6.3 / **0.70** / **72%** / +$10,337 | 16.3 / 1.81 / 22% / -$15,861 |
| 3 frictionless | 9.2 / 1.03 / 33% / +$4,457 | 5.2 / **0.58** / **77%** / +$12,214 | 9.3 / 1.03 / 48% / +$3,523 |

(median months / ratio / P(beat) / medNET$)

2022 starts clear both bars even AS-SHIPPED. This is a **start-timing artifact, not a regime edge**:
a 36-month horizon from a 2022 start spans the 2023-24 recovery, so "2022 start" means "started near
a bottom", not "traded through a bear". Symmetrically, the `bull` bucket contains 2021 starts whose
horizon runs straight into the 2022 bear. Over a 36-month horizon the START label is a weak lens and
should not be quoted as a regime finding.

## 10. Traps observed / honoured

1. **ASCII only.** No unicode anywhere in the source or the output. Run with
   `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 PYTHONHASHSEED=0`.
2. **`monte_carlo.py` / `strategy_config.py` never edited.** No hook was needed.
3. **Module-level constants** set via env *before* import; in-process patches always restored in a
   `finally`.
4. **Determinism / paired seeds.** `path_seed(label, it) = 1000 * blake2b(label) + it` depends only on
   the window label and the iteration index, **never** on cell params, so an A/B delta is not seed
   noise. Byte-identical to `monte_carlo._stable_label_seed`.
5. **peewee FK-per-row.** `.symbol_id` throughout; the ladder never touches a peewee row in a loop
   (it consumes the precomputed outcome dict).
6. **polars not used.** Output is JSON, so the `infer_schema_length=None` / `fill_nan(None)` traps do
   not arise.
7. **Never self-submitted to the task queue.** The CLI is clean; the caller submits.
8. **Worktree PYTHONPATH trap.** `import_mc()` asserts `monte_carlo.__file__` resolves inside this
   repo, and the repo root is pinned to the front of `sys.path`.

---

## 11. CLI

```bash
# offline assertions (no DB, no monte_carlo import)
python experiments/bankroll_ladder/ladder_mc.py --selftest

# prove the harness against monte_carlo itself
python experiments/bankroll_ladder/ladder_mc.py --mode validate --window 2024 --n-iter 100

# one config, pooled monthly-roll starts
python experiments/bankroll_ladder/ladder_mc.py --mode single --config cell.json \
    --n-iter 300 --workers 8 --out results/cell.json

# several runtime-axis cells against one prepare (option + equity together is fine)
python experiments/bankroll_ladder/ladder_mc.py --mode screen --config grid.json \
    --n-iter 300 --workers 8 --out results/grid.json
```

Other flags: `--horizon-months` (36), `--start-capital` (2000), `--contribution` (2000),
`--checkpoint` (20000), `--hist-start` / `--hist-end`, `--min-runway-months` (0 = full horizon),
`--step-months`, `--max-starts` (smoke stride cap), `--validate-capital` / `--validate-slots` /
`--validate-slot-frac`.

---

## 12. Open items for the sweep owner

* The FUNDABLE bar and the objective are the CHARTER's and were not touched. Amending them requires a
  logged section-7 amendment **before** S3 runs.
* The equity arm's exit policy mirrors the option arm's barriers by construction. If the sweep wants
  an equity-native policy (e.g. a trailing stop, or no time stop at all), that is a **new arm** and
  should be pre-registered, not slipped in.
* `pct_spread = 0.03` is inferred from the shipped `SLIP_SL = -0.015`. If the real cheap-end spread is
  wider, raising `pct_spread` makes the forced-exit overlay bite above the floor; the mechanism is
  already there.
* Real-premium substitution for 2025+ (CHARTER 4.2, the `real_priced_replay` ledger) is **not** wired.
  All premiums here are the model premium (`1.82 * sigma * sqrt(DTE/30)`), same as the main book.
