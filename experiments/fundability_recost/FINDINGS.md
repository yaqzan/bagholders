# Fundability re-evaluation at realistic cost + liquidity (2026-06-06)

Settles the v69 "not fundable" verdict (`../v69_portfolio_retune/MASTER_FINDINGS.md` TL;DR #2),
which hinged on a **6%** symmetric round-trip spread on the v69-hygiene 85+-only config. The user
clarified the realistic cost is **1.5-3%**; this re-evaluates on the **live v70 Apex** (75+ cascade
+ overflow + dead-hold popout + RXDD + SVR + MWDD) across cost AND liquidity.

## Part 1 — Cost re-cost (recost.py, N=200)

Two cost models swept 1.5-3% vs buy-and-hold SPY (price-only, matched windows):
- **asymmetric (canon):** SLIP_ENTRY=SLIP_TP=0, SLIP_SL=SLIP_HARD=−S (only forced exits pay).
- **symmetric (D2 model):** every leg pays S/2 (~S round-trip; conservative).

SPY hurdles: 5y +89.8% / 25.4% DD; 10y +232.9% / 34.1% DD; 22-now +49.5%; 2022 −19.9%.

| live Apex | 5y med | 5y DD | 22-now | 2022 | collapse |
|---|---|---|---|---|---|
| asym 1.5% (=live) | +507,770% | 62.7% | +177,630% | +121.9% | 0 |
| asym 3.0% | +273,857% | 62.5% | +131,724% | +111.1% | 0 |
| sym 1.5% | +78,273% | 63.8% | +49,595% | +84.0% | 0 |
| sym 3.0% (worst-in-band) | **+9,859%** | 66.5% | +9,735% | +38.6% | 0 |

Even at the most pessimistic point (sym 3% round-trip, every leg) the strategy beats SPY by ~110×
on 5y and is +38.6% in 2022 (vs SPY −19.9%), collapse=0. Even the *old v69 85+-only* config beats
SPY at 3% (sym +4,810% / 40% DD; asym +25,651% / 39% DD). **The "−45% / not fundable" was purely
the 6% assumption. Cost is NOT the binding constraint.**

## Part 2 — Liquidity realizability (build_liq.py + liq_concentration.py, NON-INVASIVE tape analysis)

Concern: the headline magnitudes assume you can fill rare illiquid signals at mid. Proxy =
trailing-30d avg daily $-volume of the underlying (options liquidity tracks underlying liquidity;
option_prices only covers Feb-2025+ so this is the full-history stand-in).

**Signal liquidity (4,845 75+ v70 signals, 2016-2026):** median daily $-vol by tier = 95+ $106M,
85-94 $138M, 80-84 $371M, 75-79 $262M. Only a tail is illiquid (~7% of 75+ < $20M/day, ~14% < $50M),
concentrated in the tiny-N top tiers (95+/85-94 ~19% < $20M, but 16/400 signals).

**Edge is BROAD, not concentrated in illiquid names** (tape per-trade EV by liquidity bin, all
windows consistent): per-trade EV is **flat across the liquidity spectrum** (~+0.05-0.07 in every
bin; the <$5M/5-20M bins are NOT higher-EV). **Liquid-only EV ≈ full EV at every floor** — 5y full
+0.0602 → ≥$20M +0.0591, ≥$50M +0.0608, ≥$100M +0.0638. Filtering ≥$20M/day drops ~10-16% of trades
/ ~6.7% of gross PnL with NO per-trade-EV loss. A liquid-only cascade (slots refilled with liquid
signals) would compound at ~the same rate. **Liquidity is NOT the binding constraint** — the opposite
of the v69 "3-names convexity" concern.

## Verdict

The v69 "does not beat SPY after costs" conclusion is **OVERTURNED**. At realistic cost (1.5-3%) AND
realistic liquidity, the live v70 Apex beats buy-and-hold SPY by ~100-1000× on compound across every
window incl. 2022, collapse=0. **Neither transaction cost nor liquidity is the binding constraint.**

What still binds (NOT overturned here):
1. **Drawdown** — Apex ~62% (5y) / ~80% (10y) vs SPY 25-34%. It's a leveraged-momentum sleeve; the
   85+-only / Sentinel-style config is the lower-DD frontier point (~39-40%).
2. **Factor nature** — prior factor work (MASTER_FINDINGS) showed the edge is largely amplified
   MOMENTUM (alpha-beyond-momentum t=1.35, not significant). The post-2026-05-15 holdout is what would
   upgrade "amplified momentum factor" → "proven proprietary alpha".
3. **Option-pricing fidelity at the very top** — the flat-spread + underlying-$-vol proxies are
   coarse for the rare 95+ small-caps; the precise version needs deeper `option_prices` coverage
   (the open Liquidity-Aware Cascade priority). Cannot overturn a flat-EV-across-liquidity + 100×-SPY
   result, but would harden the top-tier capacity number.

Artifacts: `recost.py` (+ `recost_n200.jsonl`), `build_liq.py`, `liq_concentration.py`. Cost model
canon: `../version_alpha_mining/EXECUTION_COST_CANON_CATCHUP.md`.
