# Short-dated protective PUT against the call book — NULL (2026-06-22)

**User hypothesis:** the algorithm has bad 1-day call outcomes; buy a short-dated put
(≤3 DTE, probe 1/2/3/5) *against the call we're buying* as a tail/whiplash hedge to cap
the bad-day downside. Test for **both Apex and Core**.

**Verdict: NULL — net-harmful at realistic pricing, for both profiles. No MC needed.**
At the **real** ≤5-DTE ATM put premium the overlay bleeds **8.5–11.7% of deployed call
premium per cycle** over 5y/10y/22-now, net-negative in **every window except the isolated
2020-COVID crash** (and that's still *gross* of a large short-DTE bid-ask spread). The crash
payoff does not pay for the carry, and the collapse it would insure is already prevented by
the dead-hold for free.

This is the **reserved-cash protective-put door** (the one explicitly-untested put framing in
known-issues) — now tested for the short-dated overlay and closed. Distinct from the
already-null directional put *signal* (score ≤25).

---

## What was tested

A co-bought ATM protective put at each real 75+ call entry, matched underlying notional,
DTE ∈ {1,2,3,5}, walked day-1..DTE against real OHLC with the engine's **honest option model**
(`option_pricing.option_pnl_pct`: delta + √-theta). Population = **33,653 unique 75+ call
signals** from the MC trade-tape × 12 windows (incl `2020_crash`, `2022`, `5y`, `10y`,
`22-now`). Three exit policies, two of which are achievable and one of which is a look-ahead
ceiling:

| exit | achievable? | what it is |
|---|---|---|
| `fantasy` (intraday low) | **NO** (foresight) | sell at the most favorable intraday low — the MFE-gap / trailing-stop trap |
| `EOD` (best daily close) | **NO** (foresight) | sell at the best daily close over the window — still picks the best day with hindsight |
| `causal*` (fixed-T target) | **YES** | each day at close, if the put is up ≥T, sell; best single T chosen at aggregate |
| `insure` (hold to expiry) | **YES** | pure insurance — settle at expiry / exercise only on a real crash |

Harness: `overlay_ledger.py` (`overlay_ledger.parquet`, `overlay_report.json`).

## The result (achievable exits only; returns = fraction of PUT premium)

Net-negative in every window except 2020_crash, at every DTE, for both profiles. Headline
(**DTE=3, real 1.21× premium** — see calibration below):

| window | causal* | insure | Core book-net% | Apex book-net% |
|---|---:|---:|---:|---:|
| **10y (incl COVID)** | −0.31 | −0.52 | **−8.50** | **−9.24** |
| 2020_crash | +0.39 | +0.44 | +32.9 | +24.5 |
| 2022 (bear) | −0.22 | −0.44 | −7.4 | −5.9 |
| 2024 (bull) | −0.34 | −0.55 | −15.7 | −16.6 |
| 5y | −0.31 | −0.52 | **−10.9** | **−11.7** |
| 22-now | −0.30 | −0.51 | −9.9 | −10.1 |

`book-net%` = the hedge's net $ contribution as % of deployed call premium (put cost = √(DTE/30)·premium per
position). NEGATIVE = the hedge bleeds the book. DTE 1/2/5 are all the same sign; shorter = worse (more theta).

## Why it's null (five independent reasons)

1. **Gross-theta floor (G24).** A ≤3 DTE ATM put needs a **0.21–0.67σ DOWN move per night**
   just to overcome theta (`theta_pnl` = −18.4% of premium after night-1 for 3-DTE, −42% after
   night-2, −100% at expiry). But **75+ calls gap UP +7.7 bps overnight** (mean; P(gap<0)=0.436)
   — the expected move is the *wrong direction*. You pay huge theta to insure a tail that, in
   expectation, doesn't fire.

2. **The premium pivot is closed by REAL options data.** The only way the gross overlay flips
   positive is if the real short-DTE put premium is ≤0.71× the engine model (the documented
   "short-DTE overshoots 29%" note). The real options data says the **opposite**:
   - 30 DTE on this exact universe (cached `iv_ledger`, N=2018): **real/model = 1.035×**,
     `iv_rv` median **1.08** (P(IV>RV)=63% → options priced *above* realized vol = variance risk premium).
   - **≤5 DTE ATM put** on the actual 75+ signal dates (`build_short_put_prem.py`, N=224, ≥2025-02-10):
     **real/model median = 1.21×, mean 1.35×, P(real≥model)=66.5%** (dte=1..4 medians 1.16–1.28).
     Short-DTE IV carries the gamma/event premium → real short puts are MORE expensive, not cheaper.

3. **Liquidity / spread.** The ≤5-DTE ATM put on a 75+ signal day has **median volume = 0** at
   every DTE (median OI 53–186). The bid-ask spread on these is large and is **on top of** the
   gross bleed above — none of the −8.5% numbers include it.

4. **Redundant with the dead-hold.** The collapse a tail put would insure is **already prevented
   by the dead-hold** (`dh_off` = 100% collapse; the dead-hold defers/spreads the crash-day loss
   realization). The hedge buys, at negative carry, insurance the book already has for free.

5. **It DOES pay on the bad-call days — but that's swamped.** On the call-loser subset
   (call_pnl < −0.30) the causal put returns +0.38 (DTE=3) — the hedge correctly fires on bad days.
   But the premium is bled on the ~65% of *winning* positions (calls gap up, win ~65%), so the
   book net is −8.5%. Textbook negative-carry tail insurance: positive payoff conditional on the
   tail, negative carry unconditionally.

**Apex vs Core:** negative for both; marginally worse for Apex in most windows (concentration
doesn't help). No profile, no DTE, no exit policy where it's a net win outside 2020-COVID.

**The look-ahead trap:** `fantasy`/`EOD` exits ARE positive (+0.6 to +2.7), but they require
knowing the intraday/daily bottom in advance — the same uncapturable favorable-excursion that
killed trailing stops (monte-carlo.md). The achievable exits (`causal*`,`insure`) are the verdict.

## The one theoretical door (and why it's closed)

A *conditional* hedge that only fires on crash-onset days would be net-positive (2020_crash is the
only positive window). But that reduces to **"predict the crash,"** which the codebase has
repeatedly shown is unsolvable — drawdowns start at tops, omens don't predict (BDIV/Hindenburg,
research-skill G32). And even there the dead-hold already handles the crash collapse.

## Do-not-retry condition

Re-open only with a *fundamentally different* structure than "buy a short-dated put against the
call": e.g. a structurally-free hedge (a collar that sells an OTM call to fund the put — but
that caps the call upside, which IS the strategy's whole edge), or a genuine causal crash-onset
predictor that BDIV-class work hasn't found. A straight protective/insurance put — any DTE, any
sizing (smaller/OTM only worsens the carry) — is closed: real short-DTE puts are expensive,
illiquid, and insure a collapse the dead-hold already prevents.

## Files
- `overlay_ledger.py` — the overlay walk (3 exit policies, `PUT_PREM_HAIRCUT` knob), `overlay_ledger.parquet`, `overlay_report.json`
- `build_short_put_prem.py` — real ≤5-DTE put premium calibration → `.cache/short_put_hedge/short_put_prem.parquet`
- 30 DTE anchor reused from `.cache/iv_skew/iv_ledger.parquet`
