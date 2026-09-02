# FINDINGS — The CTSL vehicle study

**STATUS: COMPLETE 2026-08-12.** All six stages run. **130 MC cells** at N=500 paired
seeds, **0 tainted cells** across every battery, identity anchor bit-exact vs
`frontier_2026_08` on 12/12 windows. PREREG locked `3e2adc9f`, AMENDMENT-1 (pre-outcome) `1acca0aa`.
Raw tables only; every verdict below is read off the PREREG's own locked rules.
Evidence: `out/ctsl_*.csv`, `out/tapes/`, `logs/`.

## Headline

**The carrier is not what the campaign was named for; the vehicle is not as liquid as the
prior campaign believed; most of its trades cannot be executed at the owner's account size;
no lever survives survivorship; and its index-competitive claim dies under a 50% supply
haircut.** Not one of those changes a simulated return — every one changes what those
returns mean.

The one result that got *stronger*: it was positive through both dot-com and the GFC while
the index lost ~37%, on a survivorship-honest universe. That is the shape of an insurance
sleeve, not a compounder.

---

## V0 — mechanism audit (source-traced; no compute)

Full dossier: `V0_DOSSIER.md`. Three results:

1. **The +57.4/+60.9pp `frontier_2026_08` attributed to "CTSL" is CT_PROMOTE.** Two
   counter-trend mechanisms ship together; frontier ablated the cascade-stage one
   (`CT_PROMOTE=0`) and left the score-stage lift (`CTSL_ENABLED`) ON in both arms.
   The promoted set is provably CTSL-invariant: `ct_tag` fires on `{loaded calls,
   trend <= 20}`, `load_signals` already floors at 70, and the lift is monotone
   non-decreasing with a 74.7 floor. CTSL's own eligible set (`trend <= 15`) is a strict
   subset, so it cannot add one name to the funded ultra slot.
2. **"Promotions-only" has no clean config expression** (ultra is co-reachable by raw
   95+ = 10.3% of rows). Ultra-only stands as the vehicle at ~90% CT purity, per the
   PREREG's own fallback clause. No substitute arm invented.
3. **~1 trade in 5 is a 15-DTE router trade wearing an ultra-sized coat.** `DTE_ROUTER`
   routes <=1 signal/day to a 15-DTE outcome and zeroes its alloc score to size it down
   (`monte_carlo.py:4424`); the `ct == 'ct_call'` tier override at `:3453` runs first and
   restores full ultra funding. The vehicle is **~80/20 30-DTE/15-DTE**, carrying 18-21%
   of rows and 20-22% of PnL. Disclosed and carried, not fixed.

## V1 — vehicle definition battery (48 cells, N=500, 0 tainted, fingerprints flat)

Identity anchor: the calibrated/full arm reproduces `frontier`'s `f1p` ultra-only rows
**bit-exactly on all 12 windows** — the harness is verified before anything new is read.

| lens / universe | 22-now med / DD | 5y med / DD | **10y med / DD** | SPY 22-now / 5y / 10y |
|---|---|---|---|---|
| calibrated, full | +159.9 / 25.1 | +204.2 / 24.1 | **+110.2 / 58.9** | +58.5 / +103.9 / **+292.1** |
| buffer (MISS_P .20) | +153.3 / 25.1 | +189.7 / 25.7 | +96.5 / 60.5 | " |
| **survivor** | **+120.8 / 23.8** | **+144.9 / 25.3** | **+59.3 / 57.5** | " |
| canon (LABELED ref only) | +312.3 / 19.1 | +434.8 / 18.5 | +877.9 / 55.5 | " |

Collapse = **0.0% on every cell of every lens**, including 2020_crash.

**The 10y window is the new fact.** Applying the §0 index-competitive rule inherited from
`frontier` (survivor medians beat SPY on both decision windows), ultra-only passes on
22-now and 5y — and **fails 10y by 5x** (survivor +59.3% vs SPY +292.1%). The
"index-competitive" label was never wrong; it was scoped to two windows that happen to
begin at a market top. Over the longest window measured, the vehicle loses to buying SPY.

**Sparsity is structural.** Median trades/path: 136 (22-now), 159 (5y), but 15 (2024),
19 (dip), 23 (2021), 28 (2025) — **4 of 12 windows are THIN (<30) and the survivor 2024
cell is an ANECDOTE (9)** under the PREREG's own rule.

### Sleeve composites (arithmetic, same window; no correlation modeling — stated limitation)

Calibrated/full, median return of {100% SPY} vs {85/15} vs {70/30}:

| window | 100% SPY | 85/15 | 70/30 | reads as |
|---|---|---|---|---|
| 2018 | −5.2 | −1.0 | **+3.3** | sleeve rescues a down year |
| 2022 | −18.6 | −11.9 | **−5.1** | sleeve cuts the bear-year loss ~3x |
| 2023 | +26.7 | +32.9 | **+39.1** | additive in chop |
| 22-now | +58.5 | +73.7 | **+88.9** | additive |
| 5y | +103.9 | +119.0 | **+134.0** | additive |
| 2020_crash | −9.8 | −14.8 | **−19.7** | sleeve doubles the crash loss |
| 2021 | +30.5 | +28.4 | **+26.4** | drag in a melt-up |
| 2024 | +25.6 | +21.9 | **+18.2** | drag in a melt-up (THIN) |
| **10y** | **+292.1** | +264.8 | **+237.5** | **drag over the long run** |

The anti-correlated-chop-harvester thesis in the PREREG survives on its own terms —
positive exactly in SPY's bad years, negative in melt-ups and waterfall crashes. But the
composite over 10y is **worse than owning the index alone**, so the sleeve case rests on
the investor's ability to hold it through the years it drags.

## V1 AMENDMENT-1 diagnostic — CTSL is NOT inert, and my V0 prediction was wrong

V0 argued structurally that CTSL could not matter under ultra-only funding, because it
cannot add a name to the funded set. The measurement says otherwise, decisively:

| | 22-now | 5y |
|---|---|---|
| vehicle (both mechanisms) | +159.9 | +204.2 |
| **`CTSL_ENABLED=0`** (CT_PROMOTE still on) | **+70.8** | **+98.3** |
| delta | **−89.0pp** | **−105.9pp** |

**MATERIAL** by AMENDMENT-1's locked bar (|Δ| ≥ 5.0pp on both decision windows), by ~18×.

The structural argument was right about *eligibility* and wrong about *consequence*. The
traded name sets are **97-98% identical** (Jaccard 0.97/0.98; 138 vs 138 distinct trades).
CTSL changes almost nothing about *who* is bought — and roughly halves the return anyway.
Tracing where it actually goes:

| arm | base 30-DTE rows / mean ret | router-15 rows / mean ret |
|---|---|---|
| CTSL on | 78.8% / **+0.1000** | **21.2%** / **+0.1624** |
| CTSL off | 95.7% / +0.0849 | **4.3%** / +0.0790 |

**CTSL's dominant channel is the DTE router.** `_dte_router_call_eligible` gates on
`overall >= 80`, and `_prepare_window` hands the day's single routed slot
(`DTE_ROUTER_DAY_CAP = 1`) to the highest-scoring eligible signal — it iterates
`sorted(call_sigs, key=(date, -overall, symbol))` (`monte_carlo.py:4399`). CTSL lifts deep
counter-trend signals both *over* that 80 gate and *to the front* of that sort, so the
routed slot goes to a CT name. And because CT names carry the `ct_call` tier override, the
router's alloc-score-zeroing — the thing meant to size a routed trade *down* — is bypassed,
and the trade is funded at the full ultra 0.20. The 15-DTE sleeve grows from 4.3% to 21.2%
of rows, and the trades CTSL steers into it earn **+0.162 vs +0.079** for the ones the
router picks without it.

A second, smaller channel is real too: the base 30-DTE arm's own mean return rises
(+0.085 → +0.100) and its realized allocation fraction rises (0.0718 → 0.0779), consistent
with CTSL lifting signals out of the 75-79 `_spread_tilt_scale` down-weight band.

**So the carrier is neither mechanism alone — it is a three-way interaction that nobody
designed:** `CTSL` (manufactures router eligibility) × `DTE_ROUTER` (converts to 15-DTE)
× `CT_PROMOTE`'s tier override (restores the funding the router tried to remove). Note the
sharpest edge of this: `STRATEGY_15DTE` ships with `CTSL_ENABLED=False` and the comment
"calibration owed before enable" — yet through the 30-DTE path CTSL is manufacturing
15-DTE trades and sizing them at the ultra rate.

## V2 — the vehicle's own fill honesty (measurement; the PREREG's sign was wrong)

The PREREG expected the vehicle's liquidity mix to be favourable (54% high-tier) and its
effective never-fill rate to land at **0.09-0.12**, a tailwind vs the 0.15 engine default.

Measured on the honest variable — `opt_vol_30d_atm`, the same quantity the FF-4 tier
edges `[320, 1191, 3486, 14524]` quantise, with the arm30/arm15 never-fill rates read
live from `tp_fill_fidelity_30dte`'s matched-filter block:

| arm | share of joined rows | tier mix | never-fill |
|---|---|---|---|
| 30-DTE | 78.0% | **t1 69.4%**, t2 11.1, t3 8.7, t4 8.7, t5 2.2 | 0.1792 |
| router-15 | 22.0% | t1 46.2%, t2 30.8, t3 15.4, t4 7.7 | 0.1494 |
| **composition-weighted** | | | **0.1726** |

**The vehicle is a t1 book — the LEAST liquid quintile — and its honest never-fill rate is
0.173, a HEADWIND vs the 0.15 default, not the tailwind assumed.** Both decision windows
agree to four decimal places.

Why the inherited number was backwards: frontier's "54.2% high-tier" was computed on the
**underlying dollar-volume tercile proxy** — the very proxy frontier's own F5 then killed
(Spearman 0.154 against measured option liquidity vs a 0.6 bar). On the dead proxy the
vehicle looks liquid; on real option volume it is not.

Limitations, stated: 43.5%/37.2% of tape rows join (61 of 138/161 distinct trades); the
liquidity map starts 2022-08-05 and covers only `overall >= 75`, so 70-74 CT trades cannot
join at all. That conditioning looks benign — Spearman(overall, opt_vol) = **−0.031**
inside the map — but it is a conditioning nonetheless. Whole-tape sensitivity with the
unjoined remainder assumed at pooled / t1 / t4: **0.164 / 0.190 / 0.119**.

## V4 — capacity at owner scale (real Polygon contract ledger)

Real per-contract cost and real entry-day contract volume for the vehicle's own names;
allocation fraction taken from the tape's **realized** `premium_cost / entry_value`
(median **0.0737** — the shipped dampener stack cuts the nominal ultra 0.20 to 37% of
nominal before the fill). Clip cap = 25% of that contract-day's volume (G3(b) convention).

| book | tradable | unaffordable | **clip-capped** | deployment | effective trades/yr |
|---|---|---|---|---|---|
| $25,000 | 52.1% | 8.3% | **39.6%** | 53.1% | 5.8 |
| $50,000 | 37.5% | 0.0% | **62.5%** | 47.5% | 4.2 |
| $100,000 | 31.2% | 0.0% | **68.8%** | 39.0% | 3.5 |

**Capacity, not affordability, is what breaks this vehicle.** Above $25k nothing is
unaffordable and *more than half the trades still cannot be filled* without taking a
quarter of the day's volume in that contract. Deployment falls to 39% of intended at
$100k, and the vehicle degrades from a modelled ~30-40 trades/yr to **3.5-5.8**.

Limitation: 48 of 138 distinct trades (34.8%) join the real-contract ledger, which is the
same `overall >= 75`, 2022-08+ population as V2.

## V3 — levers: three pass raw, **none survives survivorship**

Anchor = the V2 honest lens (gross 0.30 / DTE 30): 22-now +156.2 / DD 25.1, 5y +197.0 /
DD 24.3. Lane (LOCKED): median +5.0pp on **both** decision windows, DD not worse by >2.0pp,
collapse 0, then survivor-robust before any label sticks.

| arm | raw Δmed 22-now / 5y | raw lane | **survivor Δmed 22-now / 5y** | **verdict** |
|---|---|---|---|---|
| g0.45 / DTE30 | **+24.3 / +23.2** | PASS | **−1.7 / −4.2** | **FAIL** |
| g0.60 / DTE30 | **+40.4 / +42.8** | PASS | **+5.5 / +3.7** | **FAIL** (5y under the bar) |
| g0.60 / DTE45 | +13.7 / +11.7 | PASS | **−11.2 / −16.7** | **FAIL** |
| g0.30 / DTE45 | −50.2 / −61.3 | FAIL | — | FAIL |
| g0.45 / DTE45 | −8.8 / −15.6 | FAIL | — | FAIL |

Collapse 0 on every cell. Survivor anchor = +118.6 (22-now) / +142.1 (5y).

**No lever survives.** The gross axis looked like the campaign's one clean win at raw —
g0.45/DTE30's +24.3/+23.2pp independently reproduces `frontier` F3's +24/+21pp raw read —
and **the entire gain is delisted names.** Removing them takes +24.3pp to −1.7pp. This is
why the PREREG made survivor a precondition for the label rather than a footnote, and it
is the third time this repo has caught a lever this way.

DTE 45 is separately and clearly bad at anchor gross (−50.2/−61.3pp), which is consistent
with the V6 horizon finding below: this book's edge is *short*-dated, so lengthening it hurts.

## V5 — era-honesty cube: the vehicle's best result in the whole campaign

Cube = signal-drop {15/30/50%} × MISS_P {vehicle 0.173, 0.25, 0.40}, plus a no-drop anchor,
on dot-com and the GFC. **The PIT-mcap existence-floor axis of the PREREG's cube is NOT
RUN** — no point-in-time existence filter exists in the engine and approximating one would
have invented an axis. The reading rule ("an era conclusion counts only if invariant across
the cube") is therefore discharged over two of its three dimensions, not three.

| window | vehicle range across all 10 cells | DD range | collapse | **SPY** | sign |
|---|---|---|---|---|---|
| **dot-com** (2000-01→2002-12) | **+75.1 … +97.4%** | 25.5-29.3 | **0.0% every cell** | **−36.9%** | 10/10 positive → **INVARIANT** |
| **GFC** (2007-10→2009-06) | **+5.9 … +50.7%** | 22.6-35.4 | **0.0% every cell** | **−37.9%** | 10/10 positive → **INVARIANT** |

**A call-buying book was strongly positive through both of the worst bear markets on
record, while the index lost ~37%** — and the sign holds at every drop rate and every
fill-pessimism setting tested.

This is survivorship-honest, which is the part that makes it worth taking seriously: the
2026-07-29 Sharadar rebuild put delisted companies back into those eras. **410 of the 942
dot-com symbols are delisted (40.8% of signal rows); 198 of 660 in the GFC (29.8%).** These
cells ran on the full universe, so the dead names are in.

Sleeve arithmetic against the era index (same-window, no correlation modeling):

| window | 100% SPY | 85/15 | **70/30** |
|---|---|---|---|
| dot-com | −36.9 | −20.1 | **−3.3** |
| GFC | −37.9 | −27.7 | **−17.6** |

A 30% sleeve turns a −36.9% dot-com into −3.3%. That is the strongest version of the
PREREG's anti-correlated-harvester thesis anywhere in this campaign.

**Two limitations that must travel with this result, or it will be over-read:**

1. **No real option prices exist before 2022-08.** These era P&Ls are entirely
   model-generated (realized-vol premium × constant-delta), and that model was calibrated
   on modern data. The 2022+ windows can be checked against real contracts; these cannot.
   The known error-cancellation in that model (`GAMMA_AWARE` / IV-premium coupling) is
   unquantified this far back.
2. **Magnitude is fragile even where sign is not.** GFC degrades from +50.7% at 15% drop to
   **+5.9-9.0% at 50% drop** — a ~6× haircut — while dot-com *improves* under the same
   cuts (+75.1 anchor → +96.3-97.4 at 50%). Sign-invariance is what the locked rule asks
   for and what it gets; magnitude is not invariant, and the dot-com "cutting supply helps"
   direction is the same shape as the supply-cutter artifact the liquidity-floor program
   was killed for. It is not evidence of a lever.

### The modern control is the sharpest thing the cube produced

The same cube on 22-now (the decision window) is also 10/10 sign-positive — and that is
exactly why sign-invariance is a weak bar here:

| cell | 22-now median | vs SPY +58.5% |
|---|---|---|
| anchor (no drop, MISS_P 0.173) | **+156.2** | beats |
| 15% drop | +95.9 … +120.8 | beats |
| 30% drop | +71.7 … +84.1 | beats |
| **50% drop** | **+23.2 … +33.9** | **LOSES** |

5y behaves identically — 10/10 sign-positive, +79.3 … +197.0, and at 50% drop it lands at
**+79.3 … +98.4 against SPY's +103.9: below the index on every one of those three cells.**

**So at a 50% signal-availability haircut the vehicle loses to SPY on BOTH decision
windows.** Since the drop axis exists precisely as the rate-form stand-in for the 30-year
honesty filter that `frontier`'s F5 refused to license (the volume proxy failed at ρ=0.154),
this is the most decision-relevant number in V5: if an honest historical availability filter
would remove anything like half the signals, the headline result goes with it. Fill
pessimism alone is mild by comparison (MISS_P 0.40 costs ~15-25% relative); **supply is the
sensitive axis, not fills.**

Guard coverage, stated exactly: 40/40 cells, **0 tainted**, close-boundary clean, and
fingerprints flat on 22-now and 5y. The two era windows were **skipped by the fingerprint
guard** — it runs in the parent orchestrator, which does not install the in-memory era
windows. Immaterial in substance (2000-2002 and 2007-2009 rows cannot move under a live
update, and the close-boundary guard still covered them) but it is a real gap in coverage
and is recorded rather than glossed.

## V6 — December pre-commitment (drafted for ratification, NOT self-approved)

Full document: `V6_DECEMBER_DRAFT.md`. Two results.

**(a) The counter-trend edge is short-dated — which corroborates the router story.**
In-sample (2022-01-01 → the 2026-06-15 cutoff, holdout-filtered and asserted):

| horizon | CT-promoted (N=92) | contrast non-CT (N=16,263) | CT lift |
|---|---|---|---|
| `30dte_opt` @ 30d | 32.61% [23.89, 42.72] | 35.85% [35.11, 36.59] | **−3.24pp** |
| `15dte_opt` @ 15d | **44.57%** [34.83, 54.74] | 39.92% [39.17, 40.67] | **+4.65pp** |

At 30 days CT-tagged calls are *worse* than ordinary 75+ calls; at 15 days they are
*better*. Both gaps sit inside overlapping CIs, so neither is established — but the sign
flip by horizon is exactly what the router finding predicts, arrived at from a completely
independent direction (barrier outcomes, no portfolio simulation involved).

**(b) The December re-grade the PREREG asked for is underpowered by an order of magnitude.**
Only **133** CT-tagged signals exist in 4.4 years of the entire universe (92 with barrier
outcomes). That is ~30/year, so a six-month virgin window yields ~15 signals, ~10 with
outcomes, and a Wilson CI of roughly **±30pp** — three times wider than the effect being
tested. The draft therefore locks a **G-5 power gate** (fewer than 25 outcome-bearing CT
signals → report UNDERPOWERED / NO VERDICT and defer) which, on the in-sample rate, is
**expected to fire.** Committing to that now is the point: it stops a 10-signal December
result from being read as either vindication or death.

---

## What the owner is actually holding

Six stages, and the inherited picture changed at nearly every one. Consolidated:

**The vehicle is real, narrow, and mis-described.** It is not "counter-trend promotion." It
is `CTSL` × `DTE_ROUTER` × `CT_PROMOTE`'s tier override — a three-way interaction no one
designed, whose largest single channel is an undisclosed 15-DTE sleeve funded at the full
ultra rate because a tier override runs before the router's size cap. Turn off the piece the
campaign was named after and half the return goes with it (−89/−106pp).

**Its best evidence and its worst evidence point in opposite directions.**
- *Best*: positive through dot-com (+75…+97%) and the GFC (+5.9…+50.7%) while SPY lost ~37%,
  sign-invariant across every drop and fill-pessimism cell, on a survivorship-honest
  universe. A 30% sleeve turns a −36.9% dot-com into −3.3%.
- *Worst*: it **loses to SPY by 5× over 10y** (+59.3 vs +292.1 survivor), 4 of 12 windows are
  THIN, **no lever survives survivorship**, and at $50k-$100k **62-69% of its trades cannot
  be filled** — leaving 3.5-4.2 effective trades a year.

**The honest reading is that this is a crash/chop hedge that behaves like an insurance
sleeve, not a compounder** — and one whose crash-era evidence rests entirely on a modelled
option-pricing regime with no real contracts before 2022-08.

### Disposition

1. **No ship.** None was in scope, and nothing here would justify one: the levers all failed
   survivor, and the mechanism's own predictand is negative at 30 days.
2. **The naming correction should propagate.** `frontier_2026_08`'s FINDINGS, `known-issues.md`
   and `capital-plan-2026.md` all name "CTSL" as the identified carrier. The carrier is
   CT_PROMOTE, and more precisely the three-way interaction. Left for the owner to approve
   before editing closed-campaign documents.
3. **The router bypass is a live question, not a research artifact.** `monte_carlo.py:3453`
   overrides the tier before `:4424`'s alloc-score cap can size a routed trade down. That is
   running in production today. Whether it is a bug or a happy accident is a decision, not a
   measurement — and it is worth ~20% of this book's premium.
4. **V6's thresholds await ratification**, including a power gate that is expected to fire.
5. **Owner decision on the vehicle itself**: capacity (V4) caps it at a handful of trades a
   year at the owner's scale. That, not return, is the binding constraint.

### Compute ledger

| battery | cells | outcome |
|---|---|---|
| V1 vehicle definition | 48 | 0 tainted, anchor bit-exact 12/12 |
| V1 AMENDMENT-1 (CTSL off) | 12 | 0 tainted |
| V2 honest-lens rerun | 14 | 0 tainted |
| V3 levers | 10 | 0 tainted |
| V3 survivor confirmation | 6 | 0 tainted |
| V5 era cube | 40 | 0 tainted; era windows outside fingerprint coverage (see V5) |
| V0 / V2-derivation / V4 / V6 | — | file + DB analysis, no simulation |
| **total** | **130 MC cells** | plus 44 discarded (the V5 run whose axes were inert) |

All compute went through `trader queue submit --priority high --db light --restartable`
with foreground `trader queue wait`; nothing was run raw or via a background watcher.
One battery (`v1c`) died on cell 1/12 on an argparse `choices` collision and was rerun
after the fix; one (`v5`) was killed mid-run and rerun after the axis bug above.
