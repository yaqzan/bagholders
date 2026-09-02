# Short Premium (Option SELLING) — comprehensive study: naked puts/calls + PMCC

**Date:** 2026-07-26 (Sunday /research run) · **Prereg:** `PREREGISTRATION.md` (locked before any pull)
**Data:** REAL Polygon daily contract prints, 2022-08-09..2026-07-24 — **56,160+ kept contract paths**
(22.5k bull-signal puts, 24.9k bear-signal calls, 1.2k LEAPS, 3.1k PMCC short legs, 4.4k+ neutral
controls; ~894k daily bars; tasks #88/#92). Permanent asset banked before the ~Aug-6 Polygon
cancellation. Vendor data in `.cache/short_premium/` only; MySQL untouched.

---

## 0. TL;DR

| Hypothesis | Verdict |
|---|---|
| **H1 — sell puts at 75+ signals** | **Real positive carry in-era** (best cell +4.5%/trade on margin, t=+4.3, WR 84%) — but **capital-inefficient at portfolio level** (best realistic config ≈ **+5.7% CAGR at 12.6% DD** in a bull-heavy era) and **crash-catastrophic** (modeled COVID/GFC/dot-com DD 50-82%). Mostly VRP, not score alpha (lift vs control t≈1.4-1.7, ns). |
| **H2 — sell calls at ≤30 signals** | **KILLED.** Negative EV (t≈−1.8) AND worst-trade −37× margin. Momentum universe rips through short calls. |
| **H3 — PMCC at 75+ signals** | **KILLED at this data's fidelity.** t=−9.1: deep-ITM LEAPS legs print 30% of the time, 53% stale, and their wide-spread entry cost bleeds more than the short call collects. |
| **H4 — signal vs VRP attribution** | Lift over neutral-signal control = +2.2-2.8pp/trade but **not significant** (bootstrap clustered t 1.37-1.69, CI spans 0). The carry is variance-risk-premium + era, not score alpha. |
| **Overall** | **Short premium is strictly dominated by the long-call cascade on this system.** No ship path. Two staged leads (§9): the put-overlay diversifier and the Dec-2026 OOS re-read. |

**The one-line physics:** selling options on this universe earns a real ~4%/trade carry with a
−1× to −6× margin left tail that **clusters in time** (7 concurrent signals/day, 23-day overlap).
Cluster-Kelly caps deployable size at ~1-2% equity per trade, which caps the whole strategy near
~6%/yr — while the same signals expressed as long calls compound hundreds of percent at comparable
drawdown because their tail is bounded at −1× premium and their upside is unbounded. The asymmetry
argument is decisive and structural, not parametric.

---

## 1. Method (see PREREGISTRATION.md for the locked spec)

Real contract selection at as-traded spot (G51 `spot_unadj`), entry = signal-date close print,
premium received ×0.90 haircut primary (sellers cross spread), exits: TP=buyback limit at 50/75%
capture (LOW-based, free), SL=EOD close ≥2×/3× premium (pays half-spread), else expiry intrinsic
settle from the as-traded daily series. Margin = standard broker formula
(max(20%·S − OTM, 10%·K) + prem); returns on margin AND cash-secured. CR1 date-clustered t.
Holdout: ranking pre-2026-06-15 only. 792 cells (arm × moneyness × DTE × policy × haircut).

## 2. H1 — bull-signal short puts (the only surviving arm)

Top cells (pre-cutoff, haircut 0.90, |t|≥2, d30 = 21-45 DTE):

| cell | N | WR | EV/margin | t | ann-ROI proxy | P05 | P01 | worst |
|---|---|---|---|---|---|---|---|---|
| m0.90 d30 hold-to-expiry | 4,890 | 84% | **+4.5%** | +4.3 | +49% | −0.57 | −1.44 | −6.2 |
| m0.95 d30 + SL3x | 5,674 | 78% | +4.0% | +4.7 | +46% | — | — | — |
| m1.00 d30 hold-to-expiry | 5,059 | 74% | +3.9% | +4.0 | +42% | −0.53 | −1.04 | −3.0 |
| m0.95 d15 + SL2x | 3,589 | 76% | +2.7% | +4.6 | +83% | — | — | — |

- **Hold-to-expiry beats every buyback policy** — for shorts the edge IS the full capture; TP
  buybacks give back carry, SLs realize vol spikes.
- **Per-year: positive 4/5 years, STRONGEST in 2022** (+9.6..+11.6%/trade — bear-market IV
  richness + the buy-weakness bounce; the G19 law again). 2023 mildly negative. 2026 OOS sliver
  negative (see §6).
- **Haircut sensitivity is load-bearing:** EV/margin +6.4% (1.00) → +4.5% (0.90) → +3.5% (0.85,
  still t=3.4) → **+1.6% (0.75, t=1.5 — FAILS)**. The edge is ~2.5× the plausible spread-cost band
  on contracts that frequently print zero volume. Realizable capture is the biggest unknown.
- Cash-secured basis: EVs ÷ ~5 (margin ≈ 18-22% of strike) — ~0.8%/trade on secured cash.

## 3. H2 — bear-signal short calls: KILLED

All 9 cells negative-to-flat (best t +0.9 vs bar 2; d30 hold-to-expiry cells t −1.6..−1.9), and the
tails are unacceptable in principle: worst trade **−37× margin** (control-call arm −57×). A
momentum-scored universe is exactly where short calls die: the ≤30 cohort still contains names that
double. Unbounded upside tails + no significant bearish edge = closed axis.

## 4. H3 — PMCC: KILLED at this data's fidelity

Paired 1,060 cycles: EV/capital −5.0%/cycle, t=−9.1. Root cause is measurable, not subtle: the
deep-ITM (0.75 moneyness) 180-420d LEAPS leg **kept only 30% of attempts** (deep-ITM LEAPS rarely
print), entered at wide-spread prints ×1.10 haircut, marked 53%-stale. The short 1.05 d30 call
collects far less than the long leg bleeds. Caveat carried honestly: this indicts PMCC **as priced
on real daily prints for this universe** — a quote-level (NBBO) dataset could revise it; daily
trade prints cannot.

## 5. H4 — attribution: it's VRP, not the score

Matched-cell lift (bull_put − ctrl_put, d30 hold-to-expiry, pre-cutoff, date-bootstrap clustered):
m1.00 +2.8pp t=+1.69 CI[−0.002,+0.064]; m0.95 +2.2pp t=+1.37 CI[−0.008,+0.056]. Neutral-signal
short puts also carry (+1.1-1.5%/trade). **The premium is the universe's variance risk premium
(iv_rv 1.08 anchor) plus era, with at most a modest unproven score tilt.** Consistent with the
verification-arc verdict that the score is a risk-shaper, not a per-trade edge source.

## 6. Portfolio frontier — the decisive layer

Deterministic replay of real paths, margin marked daily with real spots, margin-call machinery,
jackknife CIs (task #90, 180 configs):

- **Best realistic config: budget 50% × per-trade cap 2% × maxpos 14 → +5.7% CAGR, worst DD 12.6%**
  (JK p05 +9.3k..p95 +29k on 100k over ~4y). 2022-08..2026-07 is a *favorable* era for this (rich
  IV, mostly rising tape).
- **Sizing is a cliff, not a knob: per-trade cap 5% → −9%/yr; 10% → −17..−28%/yr** despite the
  +4% per-trade mean. Fat-left-tail geometric drag.
- **Why (analytic, sim-independent):** single-bet Kelly says f*≈10-12%, but signals arrive ~7.1/day
  with 23-day holds. Day-cluster Kelly → f*≈5%; **23-day-overlap cluster Kelly → f*≈1%**, growth
  turns negative at f≥3% (16 ruin-windows). Worst real 23-day cluster = **−45× the per-trade
  fraction** (at 2% sizing ≈ −45% equity month). Correlation, not mean, binds.
- **The June-2026 OOS sliver is a live demonstration:** entries 2026-06-16..06-26 lost −0.24..−0.44×
  margin day after day (overall OOS −0.093, N=596) — one bad fortnight erasing ~10 weeks of carry,
  in real prints, inside our own window.

## 7. Modeled crash stress (task #91, 23,328 rows — MODELED region, stated plainly)

BS-premium (validated ×1.022 anchor) + IV-explosion scenario marks on v74 signals in-window:

| window | scenario | frontier-cell DD range (cap 2%) |
|---|---|---|
| COVID 2020 | flat / ramp2x / ramp3x | 54% / 55% / **71%** |
| GFC 2008-09 | flat / ramp2x / ramp3x | 21% / 29% / **51%** |
| dot-com grind | flat / plateau1.5x | 23% / **82% (SL policies)** |

Zero formal ruin (margin machinery survives), but −30..−66% final equity per crash episode against
a +5.7%/yr carry engine. **Structural inversion of the house dead-hold law: for SHORTS, stop-losses
are the crash killer** (forced buyback at IV-peak marks — dot-com: hold-to-expiry DD 23% vs SL2x
82%), while hold-to-expiry rides the marks but carries full assignment risk. There is no policy that
avoids both. This region is modeled, not measured — the real window contains no COVID-class crash;
that asymmetry alone blocks any liveability claim (same structural block as OSK/G25b).

## 8. Optimization round — deep-OTM ladder (m 0.85 / 0.80; tasks #92-95)

**Per-trade, deep-OTM + SL2x is the study's best cell family — the classic premium-seller
configuration emerges from the data:**

| cell (d30, hc 0.90) | N | WR | EV/margin | t | P01 | annROI | per-year | OOS |
|---|---|---|---|---|---|---|---|---|
| m0.80 + SL2x | 4,684 | 81% | **+7.0%** | +7.2 | −0.69 | +84% | **5/5 positive** | +0.12 |
| m0.85 + SL2x | 6,386 | 81% | +6.9% | **+8.0** | −0.79 | +83% | **5/5 positive** | +0.065 |
| m0.80 hold-to-exp | 5,412 | 90% | +6.6% | +4.7 | −1.57 | +71% | 4/5 | +0.02 |

m0.85 hold-to-expiry survives even the 0.75 haircut (+2.9%, t=2.3) where m0.90 failed — deep-OTM
is more spread-robust. The SL2x truncates the left tail *idiosyncratically* (P01 −0.69 vs −1.6)
and even the June-2026 OOS sliver flips positive (strikes sat below the drop).

**But neither advantage survives the portfolio layer or the crash layer:**

- **Portfolio replay (real era): deep-OTM configs earn only +1.2-2.2% CAGR at 17-24% DD** — WORSE
  than the m0.90 frontier (+5.7%). Deep-OTM dollar premiums are tiny; whole-contract flooring,
  80-89% skip rates and margin consumed at ~10%·K per contract eat the carry. **The per-trade
  optimum is not the portfolio optimum** — deployment friction, not EV, decides.
- **Crash stress: the SL2x that wins in-era is still the systemic killer** — COVID ramp3x −75%
  equity, dot-com plateau −91% (all positions stop simultaneously at IV-inflated marks). Deep-OTM
  hold-to-expiry rides GFC/dot-com nearly FLAT (−0.2%/−0.9% at cap 2%) but still takes −31..−45%
  in COVID's gap (DD 54-58%). No policy avoids both failure modes; they are complementary tails.

## 9. Staged leads (not ships)

1. **PUT_OVERLAY diversifier:** monthly correlation short-put sleeve vs the real long-call book is
   only **+0.33**; puts were POSITIVE in 4 of the 6 worst call months and made +10%/mo through 2022
   while calls bled. A small (≤1% per-trade, cap-2%-class) put sleeve on idle margin capacity is a
   genuine carry diversifier **in-era** — but shares the crash tail (2023-09, June-2026, and every
   modeled crash window hit both books). If ever pursued: VIX-band gated, panic-excluded, sized by
   cluster-Kelly, and only after a real-crash-spanning premium dataset exists. Parked.
2. **Dec-2026 OOS re-read:** re-run `short_ledger.py --run` on post-2026-06-15 entries at the
   December unlock (the June-2026 tail sliver is the watch item).
3. **NBBO-grade PMCC re-read** only if a quote-level source ever lands (P3.7-class data).

## 10. Caveats (honest-direction)

Prints-not-fills (daily aggregates, no NBBO — the 0.90 haircut is an estimate; §2 shows the verdict
band); early assignment ignored (favors seller); expiry settle uses last-available spot (delisting
edge); liquid_entry ~66-79% (results are the liquid-ish subset); deep-OTM/LEAPS coverage biased
toward weekly-listed large names (miss taxonomy quantified in meta.json); the crash region is
modeled; the era (2022-08+) excludes any true crash; multiplicity — 792 cells, findings required
|t|≥2 + year-sign-stability + 0.85-haircut survival per prereg.

## Files

| path | contents |
|---|---|
| `PREREGISTRATION.md` | locked design (pre-data) |
| `pull_short.py` | resumable Polygon pull, 6 arms, 13 selftests; spot-trap-correct |
| `short_ledger.py` | P&L/margin/exit-grid/frontier engine, 12 selftests |
| `_sim_core.py` / `portfolio_sim.py` | daily-mark portfolio replay + margin calls + jackknife |
| `crash_stress.py` | modeled COVID/GFC/dot-com stress, BS+IV-scenario marks |
| `.cache/short_premium/*` | contracts/paths/spots/cells/trades/portfolio/crash parquets + meta |
