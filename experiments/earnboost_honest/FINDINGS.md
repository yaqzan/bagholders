# EARN_BOOST — honest (v69) recalibration & verdict

**Date:** 2026-06-01 · **Scoring version:** v69 (`8b59206c3`, honest/look-ahead-free)
**Holdout:** all calibration on `date <= 2026-05-15` (CALIBRATION_CUTOFF). CALLS only.
**Method:** ledger of 42,979 `pre_boost>=70` call signals (1,653 near-earnings), earnings
proximity from authoritative `effective_date` (calendar days, strictly-future, cohorts
pre1=1 / pre3=2-3 / pre7=4-7), wins via version-independent in-memory swing walk on
generic (2σ/5σ) and option-aligned (1.274σ/1.092σ) barriers. Scripts in this dir.

## Verdict: EARN_BOOST earns a place — but ONLY recalibrated, and PRE7-weighted (not pre1)

The earnings premium **survives look-ahead removal as a relative effect**. What the
look-ahead inflated was the *absolute* win level (v60 near-75+ 82%g/66%o vs honest v69
64%g/53%o), NOT the *relative* near-vs-baseline premium, which is intact and
sign-consistent across 1y/3y/5y/10y.

But the honest premium lives in a **different cohort than v34 encoded**:

| cohort (days before earnings) | honest generic lift | honest opt lift | v34 emphasis |
|---|---|---|---|
| **pre1 (day before)** | ~0 / negative | **negative** (-3 to -5pp) | **strongest in v34 (+29pp!)** ← look-ahead artifact |
| pre3 (2-3 days) | mixed (+2 to +7) | mixed (75-79 = **-10pp**) | strong |
| **pre7 (4-7 days)** | **+5 to +19pp** | **+5 to +18pp** | weak in v34 |

The **run-up (pre7), not the day before (pre1), carries the edge.** v34's pre1-heavy
table was the look-ahead reading the post-earnings reaction. Honestly, pre1 is dead.

### Stage-1 gate (WR15-primary, generic barrier = canonical)
- **W1 cohort z≥+3: PASS** — pooled near-70+ z=+4.9; boundary-admission z=+4.2.
- **W3 multi-window: PASS** — 75+ lift +7.2/+8.7/+8.6/+7.9 (1y/3y/5y/10y), all positive both barriers.
- **Boundary admission accretive (not diluting):** 70-74 near promoted into 75+ win
  69.2%g (z+4.2) / 51.2%o vs the 75+ baseline 63.3%g / 48.6%o.
- **Tradable (opt) cross-check: positive but thin** — pooled near-75+ opt z=+1.5 (small N).
  pre3 75-79 is opt-negative (-10pp) → a generic-derived table includes a tradable-harmful cell.

### Sweep (analytical, fixed outcomes; SCW-after-boost is a small boundary effect)
| config | 75+ N | 75+ WR15o | admit N | admit WR15o |
|---|--:|--:|--:|--:|
| OFF | 6986 | 48.8% | 0 | — |
| CURRENT v34 (0.55/14/5) | 7685 | 48.8% | 699 | 49.2% (≈baseline) |
| honest gen (0.55/14/5) | 7471 | 49.0% | 485 | **51.3%** |
| honest gen (0.35/14/5) | 7266 | 49.0% | 280 | 52.9% |
| honest gen (0.55/14/7) | 7749 | 49.1% | 763 | 51.4% |
| honest opt (0.55/14/5) | 7117 | 49.0% | 131 | **58.0%** |

Honest table admits **fewer, better** signals than v34 (51-53% vs 49.2%). But the 75+
tradable WR needle barely moves (48.8 → 49.0-49.1). **This is a hygiene/correctness ship,
not an alpha ship** — its value is removing the look-ahead artifact and fixing the
pre1→pre7 emphasis, not a WR jump.

## Recommendation
1. **Most defensible: recalibrate to a tradable-robust honest table** = boost only cells
   positive on BOTH barriers (essentially **pre7 70-89**, plus pre3 70-74). Excludes the
   opt-negative pre3 75-79 cell. Effectively a pre7-dominant boost.
2. Keep `EARN_BOOST_MAX` moderate (0.35-0.55) and consider `WINDOW=7` (admits the pre7
   cohort the honest edge lives in; current WINDOW=5 truncates pre7 to 4-5 days).
3. **Before ship:** build the analogous honest PUT ledger (≤25) — the ship-candidate table
   currently keeps v34 PUT cells unchanged.
4. Ship = Stage 1: replace lift table → bump ALGORITHM_VERSION → recalc → assess. WR impact
   is sub-1pp; the win is correctness.

Alternative (also defensible): **disable EARN_BOOST.** The tradable edge is thin; but the
data argues against it — admits are accretive and the generic premium is real, so disabling
discards a small real signal.

## Pre7-only shape probe (2026-06-01, `probe_pre7.py`)

The production proximity `log(W+1-d)/log(W+1)` peaks at d=1 (pre1, the DEAD cohort)
and is 0 at the window edge — backwards for the honest signal. Probe: re-shape
proximity (flat / ramp-up / pre7-peak) × WINDOW=7 × both-barrier-gated table.
Evaluated on the tradable (opt) barrier. OFF 75+ WR15o baseline = 48.8%.

| config | affected WR15o (N) | admit WR15o (N) | 75+ WR15o | 80+ WR15o |
|---|--:|--:|--:|--:|
| OFF | — | — | 48.8% | 46.8% |
| table-swap (cur prox W5) | 48.7% (873) | 51.3% (485) | 49.0% | 47.1% |
| table-swap (cur prox W7) | 50.8% (1302) | 51.4% (763) | 49.1% | 47.5% |
| **pre7 flat W7 both** | **53.5% (1279)** | **53.9% (864)** | **49.4%** | 48.2% |
| pre7 ramp-up W7 both | **54.4% (1075)** | **54.6% (738)** | 49.4% | 47.7% |
| pre7 peak W7 both | 54.5% (1128) | 53.9% (807) | 49.3% | 47.7% |
| pre7 flat W7 both MAX.75 | 53.5% (1279) | 53.9% (930) | 49.4% | **49.2%** |
| pre7 flat W7 generic | 51.7% (1612) | 52.5% (1191) | 49.3% | 48.6% |

**Answer: YES — a pre7-weighted shape is materially better on the tradable edge.**
The boosted cohort's WR15o jumps from 48.7-50.8% (plain table-swap) to **53.5-54.5%**;
admits win **53.9-54.6%** vs the 48.8% baseline (a real ~+5pp tradable edge on the
promoted signals). Re-shaping proximity to weight the run-up (ramp-up/peak) beats
flat; both-barrier gate beats generic. **But tier-level leverage is capped:** 75+
moves only +0.6pp (near-earnings is ~11% of the 75+ pool); 80+ moves +1.4 to +2.4pp
(smaller pool, and pre7 80-84 is the strongest honest cell).

**Best design: pre7-weighted proximity (flat or ramp-up), WINDOW=7, both-barrier-gated
table, MAX 0.55-0.75.** Strictly better than v34 (look-ahead/pre1) AND the plain honest
table-swap. Confirm via real recalc+assess before ship (analytical probe ignores the
small SCW-after-boost boundary effect).

## Artifacts
- `call_ledger_v69_holdout.parquet` (in `.cache/earnboost_honest/`)
- `lift_table_v69_calls_generic.json`, `_opt.json`, `_ship_candidate.json`
- `build_ledger.py`, `analyze_lift.py`, `sweep.py`, `dump_honest_table.py`
