# Real Polygon Contract Prices — ledger, premium calibration, and barrier fidelity

**Date:** 2026-07-25 · **Data:** Polygon Options Developer daily aggregates (real traded OHLC per
contract), pulled for the v74 funded 75+ CALL signal set. Queue task #78, 8,885 API calls, **65.5s**.
**Ledger:** `.cache/polygon_real_premium/real_premium_ledger.parquet` — 3,904 signals attempted,
**3,339 kept (85.5%)**, 583-608 symbols, **2022-08-09 .. 2026-07-24**.
Misses: no_atm_price 350 · no_chain 123 · no_dte_band 60 · too_few_bars 18 · no_forward_bars 14.
Vendor data lives in `.cache/` only; MySQL `option_prices` untouched (repo invariant).

This is the first time this repo has had **real option contract price paths spanning more than the
2025+ yfinance window**. It is a permanent asset independent of the subscription, which is slated for
cancellation ~Aug 6.

---

## 0. A bug found in the process — repo-wide, not local

`trader.py` pulls yfinance with `auto_adjust=True`, so MySQL `price_history.close` is **back-adjusted
for splits AND dividends**. Option strikes are quoted in **as-traded** dollars. The two are not the
same number and drift apart over time:

> MMM, 2022-08-23: adjusted close **103.79** vs as-traded **141.75** (factor 1.365). The naive
> "nearest strike to close" rule selects K=105 — a strike that never traded, when the listed $1
> strikes ran 131-140. Sampled same-date drift: AZN +100%, MO +35%, CALM +27%, PFE +24%, T +23%,
> CVX +15%, MMM +14%, XOM +13%.

The correction used here is `spot_unadj = yfinance Close(auto_adjust=False) x prod(forward split
ratios)`, validated against NVDA (17.18 x 10 = 171.81), MMM (118.52 x 1.196 = 141.75, which matches
the option chain's own 134C print) and AZN.

**This affects prior work.** `experiments/data_ingest/polygon_iv_ingest.py` uses the adjusted close
both to pick the "ATM" strike (`priced(calls, close, ...)`, ~line 199) and as the spot in the
Black-Scholes solve (`implied_vol(..., close, strike, ...)`, ~line 203). That panel
(`.cache/polygon_iv/iv_ledger_polygon.parquet`) is the substrate for `osk_validation`, `osk_era`,
`gamma_iv_phaseb`, `vega_state` and `era_conditioning`. A quantified audit is running separately
(`PANEL_AUDIT.md`); the affected-fraction and strike-error numbers belong there, not here.

Worth flagging for that audit: the documented reason the gamma+IV per-trade gate failed M1 was that
panel BS-IV and real contract premiums "systematically disagree on **calm** names". Dividend-paying
large caps are simultaneously the calmest names and the highest-adjustment-drift names. That is a
hypothesis the audit tests, not a conclusion drawn here.

---

## 1. Premium model — VALIDATED

`real_model_ratio = entry_premium_real / model_premium_abs`, where the model premium is the engine's
`PREMIUM_MULT x vol` priced against the **as-traded** spot.

| population | N | p05 | p25 | **median** | p75 | p95 |
|---|---|---|---|---|---|---|
| all kept | 3,339 | 0.558 | 0.830 | **1.029** | 1.255 | 1.727 |
| DTE 25-38 | 2,338 | 0.598 | 0.845 | **1.022** | 1.225 | 1.638 |
| DTE 25-38 + liquid | 1,809 | 0.615 | 0.845 | **1.019** | 1.218 | 1.606 |

Per-year median: 2022 0.993 · 2023 0.998 · 2024 0.974 · 2025 1.073 · 2026 1.048.

**The engine's realized-vol premium model is within ~2% of real traded ATM premiums at the median,
and stable across four years.** This is a genuinely reassuring result for the cost side of every
return calculation in the system, and it is the first out-of-sample confirmation of `PREMIUM_MULT`
against real prints spanning a bear year.

---

## 2. Barrier fidelity — the apex15 predictand is EV-optimistic by ~3pp

The apex15 assessment barrier declares a WIN when the **underlying** touches +1.092 sigma within 15
trading days and maps that to a +30% option gain; a −2.548 sigma touch maps to −70%. Every Stage-1
number (WR15, apex-EV, the 0d skill gate) rides on that mapping. It has never been checkable against
a traded option price before, because the option panel did not reach back far enough.

Matched on (symbol, signal_date); real label is path-resolved first-touch on the **contract's own**
price (TP = intraday high >= 1.30x entry, a limit fill per house canon; SL = daily close <= 0.30x, an
EOD forced exit).

| control set | N | model win | real win | real stop | real expire | model EV | **real EV** | delta EV | agreement |
|---|---|---|---|---|---|---|---|---|---|
| all matched | 3,196 | 0.6981 | 0.6649 | 0.2572 | 0.0779 | +0.0222 | −0.0117 | **−0.0339** | 0.834 |
| + liquid | 2,426 | 0.6871 | 0.6731 | 0.2688 | 0.0581 | +0.0111 | −0.0094 | **−0.0206** | 0.852 |
| **+ DTE 25-38 (primary)** | **1,714** | **0.6902** | **0.6674** | **0.2783** | **0.0543** | **+0.0154** | **−0.0163** | **−0.0317** | **0.856** |

**Per-year delta EV: 2022 −0.0323 · 2023 −0.0895 · 2024 −0.0503 · 2025 −0.0017 · 2026 −0.0188 —
negative in all five covered years.** Not a single-window artifact.

Confusion (primary set): of 1,183 modeled wins, 101 really stopped and 42 really expired (12.1% of
modeled wins are real losses); of 531 modeled non-wins, 104 really won (19.6%). Labels agree 85.6%.

**Read plainly: on real contracts, the funded 75+ call cohort's per-trade apex EV is slightly
NEGATIVE (−0.016) where the barrier says positive (+0.015).**

### What this does and does not mean — scope limits

**It does NOT say the portfolio loses money — but the scope is narrower than "assessment only", and
the distinction needs stating precisely because the test conflates two separable errors.**

There are two links in the chain, and they are used by different consumers:

1. **The trigger level.** Both the assessment AND the Stage-3 MC declare an exit when the
   *underlying* touches +1.092 sigma / −2.548 sigma. Those numbers are themselves derived from the
   option mapping (`TP_SIGMA = TP x PREMIUM_MULT / DELTA = 0.30 x 1.82 / 0.5 = 1.092`).
2. **The payoff given a trigger.** The assessment applies a FIXED ±0.30/−0.70/−0.40 EV map. The MC
   does NOT — it prices each exit through `option_pricing.option_pnl_pct` with theta and vega at a
   seeded random fill, so it can realize more or less than +30%.

This test asked "did the real CONTRACT reach +30% / −70%", which is a joint test of (1) and (2)
together. Therefore:

- The **assessment predictand** is indicted on both links: its EV map is ~3.2pp optimistic.
- The **MC** does not inherit the EV-map error (link 2), but it DOES inherit any error in the
  sigma trigger levels themselves (link 1), because it fires on the same barriers.

**That decomposition has now been run** (`trigger_vs_payoff.py`), and it changes the read materially.

### Link 1 — the sigma-to-option mapping is FAITHFUL

When the **underlying** actually touches +1.092 sigma, what is the real contract worth?
(Both layers assume that moment is worth 1.300x.)

| population | N | contract CLOSE mult at touch | contract HIGH mult at touch | share HIGH >= 1.30x |
|---|---|---|---|---|
| all kept | 2,269 | p25 1.053 / **MED 1.250** / p75 1.451 | p25 1.184 / **MED 1.346** / p75 1.552 | 0.575 |
| liquid + DTE 25-38 | 1,299 | p25 1.071 / **MED 1.250** / p75 1.441 | p25 1.210 / **MED 1.359** / p75 1.552 | 0.597 |

A TP is a **limit** order, so the relevant column is the HIGH: **realized median 1.359x against an
assumed 1.300x.** On the down leg, when the underlying touches −2.548 sigma the contract's median
multiple is **0.341** against an assumed 0.300 — i.e. slightly *better* than assumed.

**`TP_SIGMA = TP x PREMIUM_MULT / DELTA` is accurate to within a few percent at the median, in both
directions.** This is a load-bearing modeling assumption that had never been checked against a traded
price, and it holds. That is a genuinely good result for the engine.

### So where does the 3.2pp come from? Theta the barrier cannot see

Underlying trigger rates on the same rows: +1.092 sigma touched **78.1%**, −2.548 sigma touched
**43.3%** (first-touch resolution gives the 69.0% modeled win). The real contract stops at −70%
**27.8%** of the time vs 23.2% modeled. The extra stops are **time decay**: an option can shed 70% via
theta plus a modest adverse move without the underlying ever reaching −2.548 sigma. A sigma barrier on
the underlying is structurally blind to that.

### Revised scope — and a reason the MC is largely insulated

- The **assessment predictand** is indicted: its fixed EV map misses theta-driven collapses, worth
  ~3.2pp of EV.
- The **MC is largely insulated on both links**: link 1 is faithful, and the MC already reprices each
  exit through `option_pnl_pct` *with theta*, so it does not inherit the EV-map error.
- **And the −0.70 "stop" is a modeling convention, not the live policy.** The shipped book
  **dead-holds** losers to expiry (documented as free collapse insurance, with a measurable `dh_pop`
  recovery rate). So "the real contract touched −70% intraday" is not automatically a realized loss
  for us — it is an event the live strategy deliberately rides through.

Net: this is a **measurement-layer** finding about Stage-1 evidence quality, not a portfolio-layer
alarm. It should change how absolute per-trade EV claims are stated; it does not, on this evidence,
change any Stage-3 gate decision.

**It does NOT invalidate cross-version comparisons.** The bias is common-mode: every version, every
mechanism A/B, and every gate has been measured on the same barrier. Relative reads (v73 vs v74, a
dampener ablation, the 0d skill gate's version ranking) are largely unaffected.

**It DOES mean absolute per-trade EV claims are ~3pp optimistic**, and that "the 75+ cohort has a
positive per-trade edge" is not supported on real contracts in 2022-2026. That is consistent with the
existing verification verdict — the score is a FLAG-grade *risk-shaper*, marginal vs climatology, and
the portfolio's returns come from cascade structure, sizing and capital velocity rather than from a
fat per-trade edge.

**Coherence check with prior work:** `real_priced_replay` (2026-07-18, yfinance lastPrice, 2025-02+)
found real prices *better* than model. Note this study's **2025 delta is −0.0017 — essentially zero**.
The gap lives in 2022-2024, exactly the era no prior real-price study could see. The two results
agree where they overlap.

**The whole edge rests on the limit TP filling.** Sensitivity: if the position is instead held to day
15 with no TP, the real win rate collapses from 66.7% to **28.6%**, with median d15 P&L **−0.4181**
(mean +0.0664 — violently right-skewed). Taking the touch is what converts that skew into a win rate.
The asymmetric-cost canon ("limit TP is free") is therefore not a minor modeling convenience; it is
load-bearing for the entire result, and P3.7's real-fill loop is the only thing that can test it.

Caveat in the honest direction: `real_pnl` is a raw contract close ratio with **no spread and no
fees** applied. The real gap is therefore at least this large, not smaller.

---

## 3. MFE — the +30% TP leaves a lot on the table (observation, not a recommendation)

Real contract maximum multiple over the hold (primary control set):

| p25 | p50 | p75 | p90 |
|---|---|---|---|
| 1.277x | **1.804x** | 2.898x | 4.444x |

Share reaching 1.3x = 0.737 · 1.5x = 0.625 · **2.0x = 0.437** · 3.0x = 0.232.

The median contract's best moment is **1.80x entry**, and 43.7% touch 2x, against a TP set at 1.30x.
This is recorded as an observation only. TP placement has been swept extensively at Stage-3 and
TP+30 won on drawdown grounds, and `concentration_2x` found the held book's optimum there; a higher
TP trades win rate for tail capture and must be judged by the T1-T7 gate, not by an MFE table.
The relevant new fact is that the **real** MFE distribution is fatter than the modeled one, which
means the trade-off has never been evaluated on true prices.

---

## 4. Affordability (input to the bankroll ladder)

Real contract cost, 1 contract = 100 shares, N=3,339:

| p05 | p25 | median | p75 | p95 |
|---|---|---|---|---|
| $57 | $180 | **$380** | $880 | $3,052 |

Affordable share by slot budget: **$250 -> 36.8% · $500 -> 58.7% · $1,000 -> 78.0% · $2,000 -> 90.6%.**

This supersedes the CHARTER 4.4 first read (median $535, 49% at $500), which rested on 785 matched
signals from the 1.4-year yfinance panel. The four-year real-price picture is **cheaper and better
covered**: a $500 slot reaches ~59% of the funded signal flow. Stage-1 of the ladder is not
supply-starved at ATM 30-DTE.

---

## 5. Files

| path | contents |
|---|---|
| `pull.py` | resumable ingest (selftest 13 offline blocks; `--consolidate-only` offline rebuild) |
| `DESIGN.md` | contract-selection rule, TP/SL conventions and why, coverage boundary, limitations |
| `calibrate.py` | L1 cost / L2 payoff / L3 horizon + affordability |
| `barrier_fidelity.py` | the matched apex15-vs-real comparison with progressive controls |
| `.cache/polygon_real_premium/real_premium_ledger.parquet` | 3,339 real contract paths |
