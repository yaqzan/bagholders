# Glide Path — DESIGN (pre-registered BEFORE compute)

**Owner:** GLIDE-PATH (gameplan P3.4 / alpha_mining N2). **Status: DESIGN ONLY.** This document
pre-registers a plan. **No harness code has been written** (no `envs.py`/`panels.py`/`policy.py`
under this directory yet), **no queue job has been submitted**, and **nothing has been committed**
beyond this file. Per the task that produced it: **STOP here for FABLE's approval** before writing
any harness code or submitting any compute. Portfolio-stage research only — no `ALGORITHM_VERSION`
bump, no scoring change, no edits to `strategy_config.py` / `monte_carlo.py` /
`portfolio_profiles.json`.

This file follows the same pre-registration discipline as
`experiments/lifecycle_mc/DESIGN.md` (written before that experiment's compute ran) and is asked
to **reuse that experiment's composition harness** (`experiments/lifecycle_mc/{envs,panels,
_rolling_runner,policy}.py`) rather than build a new engine. Every number cited below is read
directly from a named source file — nothing is retyped from memory.

---

## 1. Motivation

### 1.1 The row, verbatim

Gameplan `P3.4` (`.claude/docs/gameplan.md` §5, P3 table):

> **N2 equity-milestone glide path** (Apex→Core→Sentinel keyed on OWN equity; sweep thresholds +
> glide shape vs static profiles, N=500×10 incl COVID; sweep start-capital too; ablate vs DD-soft-band
> interaction). Ship only if it Pareto-dominates static on terminal-wealth-at-bounded-late-DD; else
> close the lead (static + manual migration is the answer).
>
> Why: Selectivity is the proven DD lever — own-equity keying stays out of the dry market-context
> well (concentration_2x Track A already validated raw equity-DD *scaling* as a Sentinel-style
> DD-shaver at a speed cost — P3.4 tests the milestone/transition framing, which is new).

### 1.2 The original N2 lead, and a terminology trap it walks straight into

The lead was filed **2026-06-09** (`alpha_mining/NEW_LEADS.md` §N2):

> Build it INTO the MC/engine as an equity-indexed interpolation: below $X stay Apex (uncapped,
> 75+), between $X..$Y glide exposure cap 50→40→30% and zero out the 75-79/80-84 tiers
> progressively (selectivity is THE proven DD lever — Sentinel 85+ cut 5y DD 84%→37%), above $Y run
> Sentinel... Hypothesis: near-Apex early compounding with Sentinel-class drawdowns at scale, i.e.
> max terminal wealth s.t. late-stage dollar-DD bounded — the actual user objective.

**This predates the 2026-06-17 Apex/Core rename.** Per `experiments/concentration_2x/FINDINGS.md`
"SHIPPED 2026-06-17": *"Core = the former Apex (long-run held compounder), the NEW DEFAULT... Apex
= `flat_n4_a25` fast-2x SPRINT... OPT-IN aggressive, NOT default... The old balanced Core ($2M-cap)
was removed."* At N2's writing time, "Apex" meant today's **Core** — the row's own "below $X stay
Apex... above $Y run Sentinel" language, read in today's names, is already **"below $X stay Core...
above $Y run Sentinel"** with a glide band in between. Today's Apex (the fast-2x sprint) did not
exist as a separate named concept when N2 was filed. This dissolves what looks like a conflict
between N2's title ("Apex→Core→Sentinel") and the task's instruction to consider dropping the
sprint leg — **there was never a 3-profile sprint-inclusive design in the original hypothesis**;
the apparent 3-tier framing is a naming artifact of reading old text with new names.

### 1.3 Fresh evidence resolves the sprint-leg question independently

`experiments/lifecycle_mc/RESULTS.md` (Screen 1, N=100, task 623, landed 2026-07-14 — the row this
task was told to read as "the adjacent result that just closed"):

| arm | median terminal | p10 | worst DD | P(collapse) |
|---|---:|---:|---:|---:|
| `core_only` | $207.7k | $106.8k | 71.9% | 0.0% |
| `sprint_rotate_core` (today's Apex, stop-at-2x, rotate to Core) | $228.2k | $33.0k (**3.2x worse**) | 91.6% | 0.0% |
| `ladder_sprint_core` (repeat-forever) | $257.5k | — | 98.5% | **1.95% (BREACH)** |

Verdict there: *"The rotate policy does NOT Pareto-dominate Core-only... Core-only remains the
small-capital answer on current evidence."* Independently of §1.2's naming resolution, this is a
**fresh, on-point, N=100-screen-level negative** on routing today's Apex-sprint through any
automated equity-keyed transition — the sprint's own tail (p10 3.2x worse) is precisely the kind of
mass a milestone glide would inherit if a sprint leg were spliced in front of it.

**Conclusion — the primary policy family excludes today's Apex-sprint entirely and starts at
Core.** §3c below defines a sprint-inclusive variant, explicitly out-of-primary-scope, contingent on
a condition stated there (mirroring `lifecycle_mc/DESIGN.md` §11's own treatment of Option B/n10 as
"a natural follow-up... if FABLE wants it").

### 1.4 Distinguishing this from the two adjacent "own-state" mechanisms already explored

Three mechanisms now key off the account's **own** state (not market context) rather than the dry
5th-sizing-lever well (`traps.md` "DD-sizing well is DRY": *"the well is DRY after 5 levers... the
remaining seams are option-pricing signals and model fidelity, not another sizing axis"* — that
rule is about **market-context** levers; RXDD/SVR/MWDD/TVDD/BDIV all key off VIX/breadth/TRIN, not
the account):

1. **`concentration_2x` Track A's equity-curve scaler** (`FINDINGS.md` Track A) — a *continuous*
   multiplicative dampener on aggression, keyed on the account's own **drawdown-from-peak**,
   tested for the *speed* objective and found NULL there (*"halves return and slows time-to-2x
   1.5-4x... Equity scaler survives only as a Sentinel DD-minimization lever"* — i.e. parked, never
   shipped).
2. **`DD_SOFT_BAND` / "H3"** (shipped, live) — a *continuous* linear ramp on CALL allocation, keyed
   on the account's own **drawdown-from-peak** (`strategy_config.py:898-906`, `dd_lo=0.35,
   dd_hi=0.55, dd_floor=0.40` uniformly on Core/Apex/Sentinel today — see §8). Recalibrated via a
   v60 sweep, N=500 5y DD −4.4pp at zero per-trade quality cost
   (`mechanism_registry.py:307-347`).
3. **This experiment (N2/P3.4)** — a *discrete-or-blended structural profile migration*, keyed on
   the account's own **absolute equity level** (not drawdown-from-peak). Mechanically different in
   kind from (1)/(2): it doesn't scale one dial, it changes *which cascade/exposure/selectivity
   regime* the book runs under.

(1) is unshipped precedent that "equity-based dampening" as a *class* has partial history here; (2)
is the live mechanism this design must prove itself **orthogonal to**, not a re-derivation of — this
is exactly what §8's ablation is for, and exactly what the row's own text demands ("ablate vs
DD-soft-band interaction").

### 1.5 Standards this design stays inside

- **Capital-velocity law** (`traps.md` §4): *"drawdown-avoidance IS return-maximization for this
  book... anything that ADDS exposure or HOLDS longer tends to null or collapse."* A migration
  *toward* Sentinel (less exposure, more selectivity) moves with this law, not against it — unlike
  the sprint-rotate leg this design excludes.
- **MC noise floor** (`traps.md` §2): N=100/300 are screens; only N=500 with 2020-COVID represented
  licenses a ship/close decision. Staged exactly as `lifecycle_mc` staged its own screen.
- **DD-sizing well is DRY** (`traps.md` §4): reason this axis is still open is stated explicitly in
  the row itself — "own-equity keying stays out of the dry market-context well." §8's ablation is
  the mechanical proof that this design doesn't quietly reopen a related, already-dry well by a
  side door (DD_SOFT_BAND).
- Every window screen **must include `2020_crash`** — structural here since the pooled-start grid
  (§5) already puts COVID inside the horizon of 16/20 pooled starts by construction, not as an
  extra step.

---

## 2. The question

**Does an equity-keyed, one-way Core→Sentinel migration Pareto-dominate holding a single static
profile (Core-only or Sentinel-only) forever, on terminal-wealth-at-bounded-*late*-drawdown, across
a swept grid of {transition threshold, glide shape, starting capital}, at zero collapse including
every path whose horizon contains 2020-COVID?**

"Late-DD" (not just "DD") is deliberate — see §7.2; it is the row's own phrase and is a different,
more specific bar than `lifecycle_mc`'s plain whole-horizon `WorstDD%`.

---

## 3. Policy family / arms

### 3a. Static comparators (baselines — reused, near-zero new logic)

- **`core_only`** — $50k-nominal Core panel, held for the entire lifecycle horizon, no migration,
  ever. This is **`lifecycle_mc/policy.py::simulate_core_only`** verbatim (same function, same
  panel) — zero new code for this arm beyond swapping in the starting-capital sweep (§6.3).
- **`sentinel_only`** — same mechanic, rooted in a **new** Sentinel panel (§9) instead of Core's.
  Structurally identical to `simulate_core_only`, parametrized on which panel it loads.

### 3b. Primary policy family — `core_to_sentinel_glide` (the live hypothesis)

Starts 100% in Core. Tracks the account's own **running-maximum equity** (not current equity — see
§6.2 for why). At the swept threshold `T`, migrates toward Sentinel, either as a hard switch or a
linear blend (§3b.1). **No leg ever returns to Core** (§6.2 ratchet rule) and **no sprint leg is
included** (§1.3/§3c).

**Config — Core leg:** reused verbatim from `experiments/lifecycle_mc/envs.py::CORE_ENV` (already
source-verified there against `strategy_config.py`/`portfolio_profiles.json`'s `"core"` entry) —
`TIER_ULTRA/TOP/MID/LOW_OV = 0.20/0.15/0.08/0.03`, `MAX_POSITIONS=14`, `GROSS/CALL_PREMIUM_CAP=0.50`,
`DD_SOFT_BAND_LO/HI/FLOOR = 0.35/0.55/0.40`, `PRACTICAL_CAPITAL_CEILING=0.0` (disabled), puts off.

**Config — Sentinel leg (NEW — §9):** built the same way `CORE_ENV` was — every field copied from
`algorithm_versions/portfolio_profiles.json`'s `"sentinel"` entry (`sentinel_v70_85plus_exp30_1m`)
via the same `PROFILE_STRATEGY_ATTRS`/`PROFILE_TIER_ATTRS` name mapping `portfolio_profiles.py`
itself uses (`portfolio_profiles.py:100-146`), so this is a mechanical transcription, not a
hand-derived guess:

```
SENTINEL_ENV = {
    'TIER_ULTRA_OV': '0.20', 'TIER_TOP_OV': '0.15', 'TIER_MID_OV': '0.0', 'TIER_LOW_OV': '0.0',
    'TIER_OVERFLOW_OV': '0.0',
    'PUT_TIER_TOP_OV': '0.0', 'PUT_TIER_MID_OV': '0.0', 'PUT_TIER_LOW_OV': '0.0',
    'MAX_POSITIONS_OVERRIDE': '14', 'MAX_POSITIONS_CALL': '14', 'MAX_POSITIONS_PUT': '0',
    'GROSS_PREMIUM_CAP': '0.30', 'CALL_PREMIUM_CAP': '0.30', 'PUT_PREMIUM_CAP': '0.0',
    'OPP_SAT_CALL_REF': '16.0', 'OPP_SAT_PUT_REF': '4.0',
    'OPP_SAT_POWER': '0.50', 'OPP_SAT_FLOOR': '0.55',
    'PRACTICAL_EXPOSURE_ENABLED': '1', 'PRACTICAL_CAPITAL_CEILING': '1000000.0',
    'DD_SOFT_BAND_LO': '0.35', 'DD_SOFT_BAND_HI': '0.55', 'DD_SOFT_CALL_FLOOR': '0.40',
}
```

(`nominal_cal_dte`/`hold_cal_days` are absent from Sentinel's `params` block in
`portfolio_profiles.json`, so both legs inherit `STRATEGY_30DTE`'s shipped 30-DTE/HOLD_CAL_DAYS=27
default identically — no DTE axis in this design, matching both `lifecycle_mc` and Core/Sentinel's
own shipped configuration.)

**Sentinel differs from Core on exactly three axes**: exposure (gross/call cap 0.30 vs 0.50),
selectivity (85+-only: mid/low tiers zeroed vs Core's 0.08/0.03), and the **$1,000,000 absolute
capital ceiling** (Core's is disabled). `DD_SOFT_BAND` is identical on both (§8) — this is the
ablation's point.

#### 3b.1 Glide shape

Both variants ratchet on **running-maximum equity** `M(t) = max(equity(0..t))`, never on
instantaneous equity (§6.2):

- **`hard`**: Sentinel weight `p(t) = 1` if `M(t) >= T`, else `0`. A single, permanent, irreversible
  switch the first time running-max equity crosses `T`.
- **`blend`**: a linear band `[0.8·T, 1.2·T]` (±20%, a fixed design choice for this pre-registration,
  not itself swept — see §6.4): `p(t) = clip((M(t) - 0.8T) / (0.4T), 0, 1)`. `p(t)` is
  monotonically non-decreasing by construction (since `M(t)` is), so the blend cannot flap back
  toward Core once it starts moving toward Sentinel — the continuous analogue of the hard switch's
  ratchet, and structurally the same *shape* as `DD_SOFT_BAND`'s own LO/HI/floor linear ramp, just
  keyed on equity instead of drawdown (worth noting for the reader: the row is proposing a familiar
  shape on an unfamiliar axis, not a new mechanism-shape).

Dollar composition of a blended step (weight `p`, block start equity `E`): the Sentinel-leg return
applies to `min(E, ceiling)` and the excess `max(0, E - ceiling)` earns 0% (idle, uninvested cash;
see §6.3) blended at weight `p` against the Core-leg return on the full `E` at weight `(1-p)`. This
mirrors `portfolio_engine.py:1082`'s own `base_value = min(equity, ceiling) if (practical_enabled
and ceiling > 0) else equity` line — the composer must replicate that split, not apply the Sentinel
panel's return uniformly across all of `E` (§6.3 explains why that distinction matters here and
didn't need to for `lifecycle_mc`).

### 3c. Explicitly considered, explicitly NOT primary — a sprint-inclusive variant

A `sprint_then_glide` arm (today's Apex 30-DTE sprint → Core → Sentinel) is **not** part of the
primary sweep. Motivation for exclusion is §1.2 (naming) + §1.3 (fresh N=100 negative) combined —
both independently point the same way. It is **not deleted from consideration**: `lifecycle_mc`
already has a built, N=100-populated `sprint` panel (`experiments/lifecycle_mc/results/panels/
sprint/*.json`, the STAGED 30-DTE Option A recipe) that this design could read directly with zero
new MC if FABLE wants a secondary read. Per `lifecycle_mc/DESIGN.md` §11's own precedent for
Option B, this is named here as a **coherent follow-up only if** either (a) FABLE wants to see the
number despite §1.3's negative (e.g. to quantify exactly how much of the tail damage a *milestone*
framing — as opposed to lifecycle_mc's pure first-passage-then-rotate framing — recovers), or (b)
the user selects Option B (n10) in P0.3, which would somewhat de-fang the sprint's tail risk and is
worth a fresh look. **Not built, not costed further, unless FABLE asks for it.**

---

## 4. Pooled starts and the horizon question (a real constraint, not a free choice)

**Grid: reused verbatim from `lifecycle_mc`**, itself imported from
`experiments/concentration_2x/sweep.py`: `monthly_windows(hist_start=2016-06-01,
hist_end=2026-04-15, horizon_days=730, step_months=3, min_horizon_days=180)` — the same ~38-40
quarterly-rolled 2-year panel cells `lifecycle_mc`'s `core`/`sprint` panels already use.

**Primary lifecycle horizon: 5 years**, matching `lifecycle_mc` and the sitewide Core comparison
unit. Verified directly (not assumed) against this grid: **20 pooled quarterly starts have a full
5y of runway to `HIST_END`, of which 16 include 2020-COVID inside their 5y window** — this
reproduces `lifecycle_mc/DESIGN.md` §5's own count almost exactly (it says "~19-20... ~16") and
confirms the harness reuse is faithful.

### 4.1 A 10-year horizon is NOT a free composer-parameter upgrade here — checked, not assumed

`lifecycle_mc/DESIGN.md` §11 notes its composer "accepts an arbitrary horizon parameter so a 10y
cut is a cheap re-run... if wanted later." That's true of the *code path*, but **the grid itself
does not contain a single pooled start with a full 10-year runway**: `HIST_END − HIST_START ≈ 9.87
years` (2016-06-01 to 2026-04-15), i.e. **strictly less than 10 years total** — no start, not even
the earliest, has 10y of room before `HIST_END`. A ~9-year (3,285-day) horizon fares only slightly
better: **4 pooled starts** (2016-06-01 through 2017-03-01), all trivially COVID-inclusive, but a
sample this thin cannot carry an N=500 ship-quality read. **This is flagged as an open question for
FABLE in §15, not silently resolved** — the honest options are (a) 5y only, accept the reachability
consequence below, or (b) a thin ~9y/4-start *diagnostic-only* supplementary cut, clearly labeled
non-gating.

### 4.2 Reachability arithmetic — several sweep cells will show "the glide never engages" in the median case, by construction, not by bug

Using `lifecycle_mc`'s own **pooled-start** Core-only N=100 result (`RESULTS.md`: $50k → median
$207.7k / p10 $106.8k over the pooled 5y horizon — a **4.15x** median multiple, **2.14x** at p10;
deliberately NOT the higher fixed-single-window "Core 5y +1,247.9%" sitewide figure, which is a
different measurement on a different window convention and would overstate reachability here):

| starting capital | median 5y (pooled-start) | p10 5y (pooled-start) |
|---:|---:|---:|
| $25,000 | $103,850 | $53,400 |
| $50,000 | $207,700 | $106,800 |
| $100,000 | $415,400 | $213,600 |

**None of the three starting capitals reach even the $500k threshold in the pooled-start median
case within 5 years** — let alone $1M or $2M. This means a large share of the 3×2×3 sweep grid
(§5) will show "the glide essentially never fires" as the median/typical outcome for that cell —
**this is expected and informative, not a harness defect**, but it means the read must foreground
the tail (P75/P90) and the diagnostic metrics of §7.3, not the median terminal-wealth column alone,
for the higher-threshold × lower-starting-capital cells. §7.3's "time to milestone" /
"%-ever-crossed" diagnostics exist specifically to make this legible rather than let a flat median
be misread as "the policy does nothing."

---

## 5. Sweep axes

| Axis | Values | Free composition? |
|---|---|---|
| Transition threshold `T` | $500k / $1M / $2M | Yes (§6.3) |
| Glide shape | `hard` / `blend` (±20% band, §3b.1) | Yes |
| Starting capital | $25k / $50k / $100k | Yes, contingent on the ceiling dollar-split (§6.3) |

3 × 2 × 3 = **18 primary glide cells**, plus 2 static comparators (`core_only`, `sentinel_only`,
each at all 3 starting capitals = 6 more cells) = **24 cells total** per stage. All 24 are
post-processing composition over exactly **two** underlying MC panels (Core, reused; Sentinel,
new — §9) at whatever N-tier the stage runs, mirroring `lifecycle_mc/policy.py`'s own "2 panels, 3
arms via composition" economy, extended to "2 panels, 24 cells via composition." No panel-count
scaling with the sweep grid — this is the crux efficiency argument for the compute plan (§14).

Cell naming convention (for the eventual output JSON, not yet implemented):
`glide_T{500k|1M|2M}_{hard|blend}_cap{25k|50k|100k}`, `core_only_cap{...}`, `sentinel_only_cap{...}`.

---

## 6. Composition seams (disclosed approximations)

Items 1-6 below are **inherited unchanged** from `lifecycle_mc/DESIGN.md` §6 (calendar-nearest-
window chaining + per-day log-return proration; first-passage-at-exact-value convention;
regime-calendar pairing not path identity; common-random-numbers-per-(start,rep,arm) seeding, looser
once structures diverge; conservative running-peak DD bound via worst-point approximation; holdout
lock does not apply — portfolio-stage backtest on frozen v74 rows, not a scoring-lift fit). They are
not re-derived here; see that document for the full justification of each. **New seams specific to
this design:**

### 6.1 Ratchet-on-running-maximum, not path identity

The glide's Sentinel weight `p(t)` is a function of `M(t) = max(equity(0..t))`, not of instantaneous
equity. This is a **deliberate design choice**, not a discovered constraint (unlike `lifecycle_mc`'s
fallback rule, which was forced by what the engine returns): an equity-keyed policy that could
migrate back toward Core on a down-tick would (a) flap under bounded-fill MC noise, and (b) fight
the very reason a "milestone" framing was proposed in the first place — N2's own language is a
"graduation," not a thermostat. The ratchet is the cheapest, most defensible choice that avoids
building oscillation-cost modeling this pre-registration does not attempt.

### 6.2 Capital-ceiling non-scale-invariance — the one seam that is actually load-bearing

`lifecycle_mc`'s composer treats every panel as a **scale-invariant multiple generator**: it reads
`log(final/START)` from a $50k-nominal run and reapplies that rate to whatever dollar equity the
composed lifecycle currently holds (`core_block_return`), which is valid **as long as nothing inside
the panel's own mechanics is denominated in absolute dollars**. Core has no such mechanic
(`PRACTICAL_CAPITAL_CEILING=0.0`, disabled) — scale invariance was free there. **Sentinel does**:
`PRACTICAL_CAPITAL_CEILING=$1,000,000` caps *deployed* capital regardless of account equity
(`portfolio_engine.py:1082`, `base_value = min(equity, ceiling)`). A Sentinel panel built at $50k
nominal will essentially never have its own ceiling bind internally (Sentinel's conservative 30%-cap
sizing makes a >20x move within one 2-year window practically impossible) — so the panel's raw
`finals[]` distribution describes "Sentinel when never ceiling-constrained," which is silently wrong
for exactly the highest-value part of this sweep: an account transitioning at $2M is **already**
double the ceiling on day one.

**Resolution adopted:** keep Sentinel's ceiling **fixed at its shipped $1,000,000** (do not
re-couple it to the swept threshold `T` — that would require one Sentinel panel per threshold,
tripling MC cost for a coupling this design doesn't have evidence to justify). Apply the ceiling
**in the composer**, explicitly, at every step of a Sentinel-state block: the deployed portion
`min(E, 1{,}000{,}000)` grows at the panel's own per-day rate; any excess `max(0, E − 1{,}000{,}000)`
is carried as idle cash (0% nominal — no T-bill credit modeled; a disclosed, deliberately
conservative simplification, same spirit as `lifecycle_mc`'s own "2x-not-overshoot" simplification).
Consequence, and the actual point of the threshold sweep: **transitioning at $500k means the
ceiling doesn't bind for a while; transitioning at $2M means it binds from day one.** This was
initially worried to be a confound; on reflection it is the experiment's own subject matter, not
noise to remove.

This also means the **starting-capital sweep is free in exactly the same sense** — a different
dollar seed fed into the same composer logic — *provided* the ceiling-aware split above is
implemented once, correctly. No additional panels are implied by the starting-capital axis.

### 6.3 Blend-band width is fixed, not swept

±20% around `T` (§3b.1) is a design choice made to bound the grid at 18 primary cells, analogous to
`lifecycle_mc` fixing `SPRINT_HORIZON_CAL_DAYS=730` as "the existing convention" rather than a swept
parameter. A band-width sweep is a cheap, same-panels follow-up if the `blend` shape looks
promising and FABLE wants to tune it — not part of this pre-registration.

### 6.4 Blend-state drawdown bound

For a blended step, the worst-point-within-segment approximation (`lifecycle_mc`'s own item 5,
`DDTracker.visit_segment`) is extended to a `p`-weighted combination of each leg's own worst-point
fraction. This is a plain linear approximation of a jointly-drawn worst point, not a rigorous bound
— flagged as looser than the single-profile case, in the same spirit `lifecycle_mc` flagged its own
ladder-arm "simultaneous-trough" bound as conservative-but-approximate.

### 6.5 Fill realism at large notional is out of scope here

A $2M-deployed Sentinel book trades the same underlying signal universe (same small/mid-cap names)
as a $50k book; this design inherits the panels' existing fill-realism assumptions uniformly across
starting capitals, the same way every other MC in this codebase does today. Liquidity-aware sizing
at scale is `P3.6`'s job (gameplan, "Liquidity-aware cascade"), not this row's — noted as a cross-
reference, not solved here.

---

## 7. Metrics

### 7.1 Standard set (inherited from `lifecycle_mc` §7)

Per cell, pooled across (start × replication) draws: terminal wealth distribution (median, P10/25/
75/90, mean, as dollars and as a multiple of the cell's own starting capital); whole-horizon
WorstDD% (max drawdown-from-peak fraction, matches sitewide `worst_dd` convention); Collapse =
`P(terminal <= 0.20 * starting_capital)`.

### 7.2 NEW — "late-DD-dollars" (operationalizes the row's own "bounded LATE DD" bar)

The row's bar is **"terminal-wealth-at-bounded-*late*-DD,"** not the generic "bounded DD"
`lifecycle_mc` used — and N2's own founding hypothesis text is explicit: *"max terminal wealth s.t.
late-stage **dollar**-DD bounded — the actual user objective"* (§1.2). A whole-horizon %-DD metric
conflates a 70%-DD-on-$60k ($42k lost, early, recoverable) with a 70%-DD-on-$1.8M ($1.26M lost,
late) — exactly the distinction a milestone glide exists to make, and exactly what plain `WorstDD%`
cannot see.

**Definition:** for every cell, fix a reference level equal to that cell's own swept threshold `T`
(for glide cells) or, for the static comparators, the SAME `T` applied post-hoc for
comparability (i.e. "what is Core-only's own worst *dollar* drawdown after equity first crossed
$500k/$1M/$2M, even though Core-only never changes its own risk posture there"). `late-DD-dollars`
= the largest peak-to-trough **dollar** drawdown observed only over the portion of the path from the
first time running-max equity crosses the reference level onward. Paths that never cross the
reference level contribute no observation to this metric (see the %-ever-crossed diagnostic below,
which is what makes that non-contribution visible rather than silently averaged away).

This is the metric the BARS (§10) actually reads on the DD axis — `WorstDD%` (§7.1) is reported
alongside for sitewide comparability and the collapse check only.

### 7.3 Diagnostics (not gating, but mandatory to report — makes §4.2's reachability concern legible)

- **Time-to-milestone**: median/P90 calendar days from lifecycle start to first `M(t) >= T`
  crossing, among paths that ever cross it (mirrors `lifecycle_mc`'s own "time-in-sprint" metric).
- **%-ever-crossed**: fraction of pooled draws whose running-max equity reaches `T` at all within
  the 5y horizon. Expected to be well under 50% for several of the higher-threshold ×
  lower-starting-capital cells per §4.2 — reported explicitly so a low number reads as "horizon too
  short for this cell," not "policy failed."

### 7.4 SECONDARY read — pre-registered per FABLE ruling 2a (2026-07-14)

Per-cell **transition rate** plus **CONDITIONAL terminal-wealth and late-DD-dollars on transitioned
paths only** (paths whose running-max equity crossed the cell's own `T`; static comparators use the
same post-hoc reference crossing). Every conditional figure is **N-labeled**; a cell with **fewer
than 30 transitioned paths reports `SKIPPED_N_LT_30`** instead of numbers. The PRIMARY bar (§10)
remains the row's **UNCONDITIONAL** Pareto test — the conditional read informs interpretation (it
isolates "what the glide does when it actually engages" from §4.2's reachability dilution) but
**cannot license** an escalation or a ship on its own.

---

## 8. The DD-soft-band interaction ablation (the row demands this explicitly)

**Question:** is any apparent benefit of the milestone glide actually just DD_SOFT_BAND
re-detected via a different route, given both key off the account's own state (§1.4)?

**Design:** re-run the **finalist** glide cell(s) surviving the N=100/N=300 screens (§11) with
`DD_SOFT_BAND` forced to its documented no-op state — `DD_SOFT_BAND_LO=0.0, DD_SOFT_BAND_HI=0.0,
DD_SOFT_CALL_FLOOR=1.0` (`mechanism_registry.py:308-309`: *"all zero defaults / floor=1.0 = mechanism
disabled"*, the same 15-DTE `wired_neutral` state already used elsewhere in this codebase) — on
**both** legs (`CORE_ENV_NO_DDSB`, `SENTINEL_ENV_NO_DDSB`, each the primary dict with those 3 fields
overridden), and compare the full metric set (§7) against the shipped-DD_SOFT_BAND-ON primary run.

**Reading:** if the glide's Pareto-dominance verdict (§10) is **unchanged** with DD_SOFT_BAND off,
the milestone mechanism is additive/orthogonal — the intended, clean result. If the verdict
**flips** (glide only wins because DD_SOFT_BAND happens to bind harder inside Sentinel's tighter
exposure, or some other confound), that is itself the finding, and per the sitewide convention for a
lever-keep-decision flip (`gameplan.md` P1.5's own rule: *"Any flip -> re-validate that lever at
N=500"*) the ablation escalates to N=500 alongside the primary gate rather than being reported as an
N=300-only aside.

**Cost:** 2 extra panel builds (`core_noddsb`, `sentinel_noddsb`) at the N=300 stage (mandatory);
2 more at N=500 (conditional on a flip at N=300) — costed in §14.

> **EXECUTION AMENDMENT (FABLE, 2026-07-14):** FABLE's capped execute order pulls the ablation pair
> **forward into Screen 1 at N=100** (the pair's panels are built alongside the Sentinel panel and
> all 24 cells are composed against both panel sets in the same screen pass), because under the
> spend cap (§15 ruling 2c) Screen 1 is likely the ONLY stage that runs — an ablation scheduled for
> a stage that never fires would silently skip the row's own explicit demand. The "finalist cells
> only" narrowing above applies to the (conditional) N=300/N=500 stages, not to Screen 1, where
> composition over all cells is free.

**Why DD_SOFT_BAND and not the 6-lever market-context stack:** the row's own text draws this
boundary ("own-equity keying stays out of the **dry market-context** well") — RXDD/SVR/MWDD/TVDD/
BDIV/SPREAD_TILT all key off VIX/breadth/TRIN/volume-flow, a different axis already proven dry
(`traps.md` §4) and not the one this design risks re-deriving. DD_SOFT_BAND is the one *other*
shipped mechanism that, like this one, keys off the account's own state — it is the only ablation
this row's own reasoning actually calls for.

---

## 9. The Sentinel-panel pre-step — mandatory, not avoidable, and costed

**Every existing Sentinel number is incompatible with this composition harness.** Two sources exist
today:

1. `portfolio_profiles.json`'s `sentinel_v70_85plus_exp30_1m.selection_metrics` — an **N=250 MC**
   from **2026-06-02**, on **v70** scoring (not v74), over a `{2020, 2022, 5y, 10y}` **fixed-window**
   grid (`profile_frontier.py`), not the quarterly-rolled pooled-start grid this design needs.
2. `experiments/sentinel_v74_check/RESULTS.md` (P3.5, 2026-07-13) — v74, but a **single
   deterministic backtest path** (`run_cascade_backtest`), not a Monte Carlo; it has no per-path
   `finals[]`/`dds[]` arrays at all, and explicitly says so: *"a single deterministic path vs an
   N=250 stochastic bounded-fill MC point estimate is not apples-to-apples... the observed side has
   no distributional collapse concept."*

Neither has per-path arrays on the quarterly grid the composer requires (`load_panel` needs `paths.
{finals, dds, t2x_bars}` per window cell, exactly as `lifecycle_mc/policy.py::load_panel` reads
them). **A fresh Sentinel panel build, on v74, over the same ~38-40 quarterly cells `lifecycle_mc`'s
Core/sprint panels already use, `MC_RETURN_PATHS=1`, is a hard pre-step for any real Core→Sentinel
composition — there is no way to avoid it and still answer this row's question.** This is not new
compute *invented* by this design; it is the same panel-build machinery `lifecycle_mc/panels.py`
already runs for Core and sprint, applied to a third, not-yet-built arm.

Favorable framing: because `_rolling_runner.run_one_rolling_window` is fully arm-name-agnostic (it
takes an `env_overrides: dict` and shells out — verified by reading `_rolling_runner.py` directly,
no arm-specific logic anywhere in it) and `panels.py`'s `ARMS` dict is just `{name: env_dict}`,
adding Sentinel is genuinely a **drop-in**: one new `SENTINEL_ENV` dict (§3b, already
source-verified above) plus one more `ARMS` entry — no new engine code, no `_rolling_runner.py`
changes, matching the task's instruction to "leverage the harness, not build a new engine."

---

## 10. BARS

**Verbatim, from the row (§1.1):** *"Ship only if it Pareto-dominates static on
terminal-wealth-at-bounded-late-DD; else close the lead (static + manual migration is the answer)."*

**Operational reading (this design's own interpretation, kept separate from the quote, per
`lifecycle_mc/DESIGN.md` §8's own discipline):** for each (starting capital, threshold) pair, the
best glide shape (`hard` or `blend`) at that cell must show, against **both** `core_only` and
`sentinel_only` at the same starting capital:

1. `late-DD-dollars(glide) <= late-DD-dollars(best static)` at the same reference level `T` (not
   worse on the bounded-late-DD axis) — this is the axis the row actually names, not plain WorstDD%.
2. Median (and ideally P25) terminal wealth `>= ` the better static comparator's.
3. **Collapse = 0** for the glide cell, across every pooled-start/replication draw, including every
   draw whose 5y horizon contains 2020-COVID (16 of 20 pooled starts, structural per §4).
4. §8's ablation verdict does not flip when DD_SOFT_BAND is forced off.

Per `traps.md`'s MC noise floor: **N=100 and N=300 are screens only.** A win at N=100/300 is a
reason to escalate, not a reason to recommend shipping. `sprint_then_glide` (§3c) is out of primary
scope and is not held to this bar unless FABLE separately asks for it.

**SPEND CAP — pre-registered per FABLE ruling 2c (2026-07-14), supersedes the unconditional
escalation ladder above:** run **Screen 1 (N=100) only**. Escalate to Screen 2 **ONLY if some glide
cell beats its static comparator OUTSIDE Screen-1 noise on the primary bar**. On across-the-board
ties/losses, **close the lead at Screen 1** — the N=500 gate is **explicitly skipped on a tie**
(the row's else-clause "static + manual migration is the answer" does not require an N=500-grade
demonstration of a tie). **No same-pass re-cuts** — no post-hoc cell additions, band retunes, or
threshold adjustments inside the same screen pass.

**HONEST EXPECTATION — pre-registered per FABLE ruling 2b (2026-07-14), written BEFORE Screen 1
runs:** given §4.2's reachability arithmetic, at $25k-$100k starting capitals **most cells are
expected to be unconditional ties** (the glide never engages on most paths, so the unconditional
distributions nearly coincide with `core_only`'s). Per the row's own else-clause, **the likely
endpoint of this experiment is CLOSE THE LEAD** ("static + manual migration is the answer"), with a
**REAL-WORLD re-open trigger — live account equity approaching a transition threshold — not a
backtest trigger**. Writing this down now is what makes a later close honest rather than
retro-fitted.

**"×10" in the row's phrasing:** resolved by FABLE ruling 1 — see §15.

---

## 11. Staging plan

| Stage | New panel builds | Tier | Who decides to escalate |
|---|---|---|---|
| 0 — synthetic unit tests | none (zero MC) | — | this task, before any queue submission |
| Screen 1 | Sentinel N=100 + `core_noddsb`/`sentinel_noddsb` N=100 (ablation pair pulled forward per §8 amendment) + gap-fill of any missing Core N=100 cells (Core otherwise reused in place from `lifecycle_mc`) | screen — **the only funded stage under the §10 spend cap** | FABLE, after reading Screen 1; escalation ONLY on a beat-outside-noise per ruling 2c |
| Screen 2 (conditional) | Core N=300 (fresh — N=100 tier doesn't carry over) + Sentinel N=300 + `core_noddsb`/`sentinel_noddsb` N=300 (finalist cells only, §8) | screen | FABLE, after reading Screen 1 |
| Gate (conditional) | Core N=500 + Sentinel N=500, **+ the §8 ablation pair at N=500 only if Screen 2's ablation flipped** | **the only tier that licenses a SHIP under §10** (a CLOSE is licensed at Screen 1 on a tie, per ruling 2c) | FABLE, after reading Screen 2 |

Renders no verdict below the Gate tier — same discipline as `lifecycle_mc/policy.py`'s `bars_check`
block: plain arithmetic against §10, not a recommendation, at Screen 1/2. (Under ruling 2c the
CLOSE decision on an across-the-board tie is FABLE's to make at Screen 1 — the harness still only
reports arithmetic.)

---

## 12. Explicitly out of scope

- **`sprint_then_glide`** (§3c) — named, not built, contingent on FABLE asking or a P0.3 Option-B
  selection.
- **Re-coupling Sentinel's `PRACTICAL_CAPITAL_CEILING` to the swept threshold** (§6.2) — kept fixed
  at the shipped $1,000,000; a per-threshold-ceiling variant is a coherent, costed-if-wanted
  follow-up, not part of this grid.
- **Blend-band width as a swept axis** (§6.3) — fixed at ±20%.
- **A true 10-year (or longer) pooled-start horizon** (§4.1) — the current grid cannot support it;
  extending `HIST_START` backward (v74 has deep history to 1995, gameplan §2) to build a
  longer-horizon grid is real, uncosted future work, not this design's to fund.
- **Liquidity/fill-realism scaling at large notional** (§6.5) — `P3.6`'s territory.
- **Any engine file edit** — `monte_carlo.py`, `strategy_config.py`, `portfolio_profiles.json`
  untouched; every knob is env-var-driven via subprocess, matching `lifecycle_mc`'s own constraint.
- ~~**Writing the harness code itself**~~ — superseded 2026-07-14: FABLE's approval + capped execute
  order authorizes the Screen-1 harness build (§13). The original design-only stop applied to the
  pre-approval state of this document.

---

## 13. Harness architecture

> **As-landed note (2026-07-14):** the harness landed as `envs.py` + `panels.py` + `policy.py` +
> `test_glide_policy.py` — no separate `run_screen.py` wrapper (`panels.py` is the queue entry
> point, `policy.py` is the composer CLI; a wrapper adding nothing over those two entries was
> dropped). Everything else below is as planned.

```
experiments/glide_path/
  DESIGN.md          this file.
  envs.py             SENTINEL_ENV (new, source-verified above) + CORE_ENV/CORE_ENV_NO_DDSB
                       imported from experiments.lifecycle_mc.envs where possible (CORE_ENV as-is;
                       a *_NO_DDSB variant adds the 3-field DD_SOFT_BAND override on top). New:
                       SENTINEL_ENV_NO_DDSB (same 3-field override on SENTINEL_ENV).
  panels.py           thin extension of lifecycle_mc's pattern: generates ONLY the arms that don't
                       already exist at the needed N-tier. At the N=100 stage this is Sentinel
                       alone (Core N=100 read directly from
                       experiments/lifecycle_mc/results/panels/core/*.json, not copied). At
                       N=300/N=500 this experiment builds its OWN Core (+ Sentinel, + the two
                       _NO_DDSB arms as needed) under experiments/glide_path/results/panels/,
                       since lifecycle_mc has not built those tiers -- still importing CORE_ENV,
                       never retyping it. Reuses _rolling_runner.run_one_rolling_window and
                       concentration_2x.sweep.monthly_windows directly (no changes to either).
  policy.py           new simulate_milestone_glide(threshold, shape, band_frac, starting_cash,
                       ceiling, rng) generalizing lifecycle_mc.policy's simulate_sprint_rotate_core
                       shape (first-passage-then-permanent-switch logic already exists there for a
                       DIFFERENT trigger -- 2x first-passage on the sprint panel -- this reuses the
                       same chaining skeleton against a running-max-equity trigger instead).
                       Reuses load_panel, nearest_window, core_block_return (renamed/generalized
                       to profile_block_return -- nothing in its body is Core-specific), DDTracker,
                       pooled_starts, _covers_covid, pct, summarize verbatim from
                       experiments.lifecycle_mc.policy via import, not copy-paste, per this
                       codebase's own "import, don't retype" discipline (envs.py's own docstring
                       states this norm explicitly). Adds: late_dd_dollars() (S7.2), a
                       pct_ever_crossed()/time_to_milestone() diagnostic pair (S7.3), and the
                       capital-ceiling-aware dollar split (S6.2) inside the Sentinel-state block.
  test_glide_policy.py  synthetic, zero-MC unit tests BEFORE any queue submission (Stage 0):
                       ratchet never decreases; blend weight clipped to [0,1] and monotonic;
                       ceiling dollar-split correct on a hand-built equity>ceiling case;
                       late-DD-dollars computed correctly on a hand-built path with a known
                       peak/trough; collapse counting. Mirrors lifecycle_mc/test_policy.py's own
                       discipline (hand-built panels where the answer is known by construction).
  run_screen.py       CLI entry point, same shape as lifecycle_mc's own: generate panels (resume-
                       safe) -> compose all 24 cells -> print + write results/screen_n<N>.json.
```

---

## 14. Compute plan

**Empirically measured precedent** (not estimated): `lifecycle_mc`'s own N=100 screen — 2 arms
(`sprint`, `core`) × ~39 quarterly window cells = 78 panel-cell subprocess runs — completed in
**~27 minutes wall-clock** on the queue (task 623; measured directly from the panel JSON/log file
mtimes, earliest to latest, this session). That is the per-arm-build cost unit used below (~13-14
min/arm at N=100, run concurrently across windows at `--cpu 4`, per `panels.py`'s own default).

| Stage | New panel-arm builds | Est. wall-clock (single queue job, `--cpu 4-8`) | Priority (per CLAUDE.md market-hours rule) |
|---|---|---:|---|
| 0 | none | seconds (inline, no queue) | — |
| Screen 1 (N=100) | Sentinel only (Core reused) | ~15 min | `high` off-market / `normal`+`--window off_market` in-market |
| Screen 2 (N=300) | Core + Sentinel + `core_noddsb` + `sentinel_noddsb` (4 arms) | rough order ~1-2h (N scaling is sub-linear — fixed per-window signal-precompute cost doesn't grow with N, only the inner simulation loop does) | same |
| Gate (N=500) | Core + Sentinel (2 arms; +2 more only if Screen 2's ablation flipped) | rough order ~2-4h for the mandatory 2; +similar again if the conditional 2 fire | same, `--db light` (this harness never touches MySQL beyond `resolve_pinned_version`'s one read) |

**Total new MC compute across all 3 stages, worst case (ablation flips and escalates): 9 panel-arm
builds** (Sentinel×3 tiers, Core×2 tiers reusing the existing N=100, `*_noddsb`×2 arms×2 tiers) —
**vs `lifecycle_mc`'s own 2 arms at N=100 only so far.** This is a real, non-trivial-but-modest
ask; the grid's 24-cells-at-every-stage is the part that stays free (§5), which is the load-bearing
efficiency claim of this whole design. Every stage is a single `trader queue submit`, resumable
(per-cell skip-if-exists, inherited from `_rolling_runner.py` unchanged), `--dedup` per stage per
this codebase's queue convention.

---

## 15. Open questions for FABLE — ALL RULED (FABLE, 2026-07-14; design APPROVED WITH RULINGS)

1. **"×10" in the row's "N=500×10 incl COVID" phrasing is read here as "the pooled-quarterly-start
   grid, ~16-20 of which include COVID"** (matching `lifecycle_mc`'s own precedent and vocabulary),
   **not** as "10 independent seed replicates" or "the canonical 12-fixed-window T1-T7 gate"
   (`monte_carlo.py:1515-1528` has 12 rows, not 10 — checked directly, not assumed).
   **RULING (FABLE, 2026-07-14): APPROVED** — pooled-quarterly-start convention; fixed-window gates
   are for stationary configs, not path-dependent policies. Recorded as interpretive.
2. **Is 5y the right primary horizon** given §4.2's reachability arithmetic (none of the 3 starting
   capitals reach even the $500k threshold in the pooled-start *median* case within 5y)?
   **RULING (FABLE, 2026-07-14)** — three parts, §4.2 acknowledged as the load-bearing fact:
   **(a)** add the pre-registered SECONDARY read (now §7.4): per-cell transition rate + CONDITIONAL
   terminal-wealth/late-DD on transitioned paths only, N-labeled, `SKIPPED_N_LT_30` below 30
   transitioned paths per cell; the PRIMARY bar stays the row's UNCONDITIONAL Pareto test.
   **(b)** the honest expectation is pre-registered (now in §10): at $25-100k starts most cells are
   unconditional ties, so the likely endpoint is CLOSE THE LEAD per the row's own else-clause, with
   a REAL-WORLD re-open trigger (live equity approaching a threshold), not a backtest one.
   **(c)** SPEND CAP (now in §10/§11): Screen 1 (N=100) only; escalate ONLY on a glide cell beating
   its static comparator outside Screen-1 noise on the primary bar; across-the-board ties/losses
   close the lead at Screen 1; the N=500 gate is explicitly skipped on a tie; no same-pass re-cuts.
3. **Is the fixed $1,000,000 Sentinel ceiling (not re-coupled to the swept threshold) the right
   call** (§6.2)?
   **RULING (FABLE, 2026-07-14): APPROVED** — the fixed ceiling is subject matter, not confound.
4. **Should `sprint_then_glide` (§3c) be built at all?**
   **RULING (FABLE, 2026-07-14): NOT BUILT** — `lifecycle_mc` answered it; the naming-artifact
   archaeology (§1.2: the N2 lead's "Apex" predates the 2026-06-17 rename and means today's Core,
   so no 3-profile sprint-inclusive design ever existed in the original hypothesis) **seals it and
   corrects the lead's own text** — recorded prominently there and here at FABLE's direction.
5. Confirm the **±20% blend-band width** (§3b.1/§6.3) as a fixed default.
   **RULING (FABLE, 2026-07-14): APPROVED** as default.
