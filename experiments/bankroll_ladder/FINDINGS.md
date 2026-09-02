# Replenishing-Bankroll Ladder ($2k/mo → $20k) — FINDINGS

**Date:** 2026-07-25 · **Origin:** user directive — *"explore the incremental explosion from low
portfolio value to empirically optimized portfolio checkpoints... the 2k per month allowance and the
goal to reach 20k sooner than accumulation."*
**Contract:** `CHARTER.md` §1 bars, set pre-peek 2026-07-18 and NOT amended.
**Engine:** `ladder_mc.py` (new), `DESIGN.md`. `monte_carlo.py` / `strategy_config.py` untouched.

> **STATUS: COMPLETE.** All 18 barrier groups ran (task #80, 26.0 min, **0 failures**), 171 cells at
> N=300 x 82 pooled monthly-roll starts = 24,600 paths per cell.
>
> **HEADLINE: 171 cells, ZERO FUNDABLE.** Nothing beats simply saving the money.

## The complete grid

| | n cells | best ratio vs null | best medNET$ | median-of-cells medNET$ | best medDD |
|---|---|---|---|---|---|
| **equity** | 9 | **1.01** | **+$437** | +$269 | 0.099 |
| **option, 30 DTE** | 81 | 1.23 | −$1,577 | — | 0.493 |
| **option, 15 DTE** | 81 | 1.41 | −$11,054 | — | 1.000 |
| all options | 162 | 1.23 | −$1,577 | **−$32,816** | 0.493 |

Worst cell: `opt_d15_tp30_sl50_s3_f33` at ratio 2.92, **−$70,889** net of contributions, 99.1% median
drawdown, 72.5% tranche-loss rate.

**Correction to a mid-run read.** On the partial grid (9 of 18 groups) 30-DTE appeared to rank no
better than 15-DTE. On the complete grid it is clearly better on every axis that matters — best ratio
1.23 vs 1.41, best medNET −$1,577 vs −$11,054, and median drawdown **0.493 vs 1.000**. The earlier
observation was an artifact of reading a truncated grid.

**The best option cell is the most CONSERVATIVE one, which corroborates the cost diagnosis.**
`opt_d30_tp100_dh_s1_f33` = 30 DTE, TP **+100%**, dead-hold, **1 slot at 33%** — i.e. the lowest-
turnover, longest-dated, highest-premium-per-contract configuration in the entire grid. Since friction
scales with turnover and with the ratio of a fixed dollar floor to premium, the ranking is exactly
what the Result-2 mechanism predicts. It still loses to a savings account.

---

## The bar (pre-registered, CHARTER §1)

A stage config is **FUNDABLE** only if BOTH hold against the savings null (the same $2k/month
schedule, zero trading):
- median time-to-$20k **<= 0.7 x null**, and
- **P(beating the null) >= 60%**.

The null is computed inside the harness on the same calendar under the same contribution-pause rule
(fairness symmetry), not hardcoded. It lands at **8.97 months**.

## Engine validation (do not skip — this is why the numbers are trustworthy)

The ladder runner reuses `monte_carlo`'s outcome-precompute and `resolve()` P&L physics rather than
forking them. A degenerate arm (large capital, contributions off, zero floor/fees, fractional-
equivalent sizing) reproduces `monte_carlo.run_single_sim` **bit-exactly: 100/100 seeds,
0.000e+00 deviation, 100/100 trade-count match**, including a 195-row per-trade tape diff.

That validation caught **three real bugs**, each invisible in aggregates (which agreed to ~1% while
per-seed paths differed completely): monte_carlo liquidates the open book even after a collapse-break;
a `basis` computation landing 1 ulp above budget and dropping the fill that exactly exhausts cash; and
recomputing the allocation base after each fill (algebraically identical, not bit-identical to
monte_carlo's fixed day-start `portfolio_value`).

**Integrity note — RESOLVED.** `ladder_mc.py` was edited mid-run while the grid driver was spawning
subprocesses from it (the documented Windows-MP hazard class). The edit was additive — later groups
carry 39 metric keys vs 36. A parity re-run of an early group (`d15_tp50_sl50`, queue task #83) under
the current code was diffed against its original output: **9 cells x 7 headline metrics = 63
comparisons, max absolute deviation 0 — BIT-EXACT.** The grid is internally comparable and the mid-run
edit changed no simulation number.

---

## Result 1 — the 15-DTE ladder loses to a savings account, decisively

Every 15-DTE cell fails both bars. Representative (`opt_d15_tp30_sl70_s1_f33`, 24,600 paths):

| regime | P(reach) | med months | null | ratio | P(beat) | medNET$ | tranche-ruin | med DD |
|---|---|---|---|---|---|---|---|---|
| all | 68.6% | **21.19** | 8.97 | **2.36** | 13.3% | **−35,221** | 10.8% | 84.4% |
| 2020_crash start | 100.0% | 15.67 | 9.00 | 1.74 | 8.4% | −10,276 | 8.9% | 77.9% |
| 2022 start | 94.8% | 28.02 | 8.97 | **3.12** | 1.9% | −36,503 | 4.6% | 84.2% |
| bull start | 62.5% | 18.92 | 8.97 | 2.11 | 15.6% | −37,048 | 12.0% | 85.0% |

**`medNET$` — median terminal equity net of all contributions — is the number that matters.** At
−$35,221 the account reaches $20k *because $2,000 arrives every month*, not because the strategy
worked. Median final equity $20,521 against ~$56k contributed.

## Result 2 — cost attribution: a third outcome, not either expected branch

Zero-cost control on the best 15-DTE cell (`opt_d15_tp50_sl50_s3_f100`), identical seeds and starts:

| arm | floor | fee | SLIP | total cost/path | **medNET$** | med months | **ratio** | **P(beat)** | FUND |
|---|---|---|---|---|---|---|---|---|---|
| 1 as-shipped | $0.05 | $0.65 | on | $26,898 | **−9,205** | 13.21 | 1.47 | 29.1% | no |
| 2 fees only | 0 | $0.65 | on | $4,519 | +3,986 | 8.97 | 1.00 | 47.6% | no |
| 3 **frictionless** | 0 | 0 | off | **$0** (verified) | +5,816 | 8.25 | **0.92** | **51.4%** | **no** |

Both readings I pre-committed to were wrong, and the truth is more useful than either:

- **The strategy is wrong for a $2k account.** Even at *literally zero friction* the ladder is a
  **coin flip against a savings account** — ratio 0.92 vs a 0.70 bar, P(beat) 51.4% vs a 60% bar —
  carrying **100% median drawdown**. Fixing microstructure does not rescue it.
- **But cost is nonetheless the dominant lever**, not a secondary aggravator: it is the entire
  distance from coin-flip (0.92) to clear loss (1.47), and it swings `medNET$` by **$15,021**.

**Mechanism — adverse selection into cheap contracts.** Mean premium actually traded is
**$0.87/share ($87/contract)**. A $0.05/share floor is **5.7% per leg, ~11.5% round trip**; measured
friction is **11.84% of all premium traded**. And it is not an average effect: when the top-ranked
signal is unaffordable the ladder **cascades down to a cheaper contract**, so the account
systematically ends up holding exactly the contracts where a fixed dollar floor bites hardest. Turnover
compounds it — $227k of premium traded against ~$37k of lifetime contributions (6.1x) turns 11.8%
round-trip friction into **72.6% of every dollar deposited**. This is CHARTER §2.3 confirmed more
sharply than it was stated.

**What cost does NOT explain:** tranche-ruin is 80.0% / 80.6% / 83.5% and median DD is **100.0% in all
three arms**. Removing 100% of friction changes speed and the sign of `medNET$` but leaves path
destructiveness untouched — independent evidence that 3 slots x 100% at 15 DTE is the wrong *shape*,
not merely expensive.

## Result 3 — equities are the honest comparison, and they roughly tie the null

Same 75+ v74 signal set, same slot cadence, same barrier events (the equity arm reads
`fire_tp_level`/`fire_sl_level` straight off the same outcome dict, so it cannot drift; the mapping is
`TP_SIGMA = TP x PREMIUM_MULT / DELTA = 0.30 x 1.82 / 0.5 = 1.092σ`).

Verified parity: fills/path 346.7 (equity) vs 337.4 (option) — 2.7% apart; TP rates 70.1% vs 71.5%.
The option arm must screen **549** candidates to fill 337 because **38.5% are unaffordable**; the
equity arm's unaffordable rate is **0.0%**. That gap is the entire structural story of a $2k account.

Equity smoke: **9.00 months vs the 8.97 null**, P(beat) 31.4%, tranche-ruin 4.2%, median DD **14.5%**,
`medNET$` **+$22 to +$438** across cells. Equities neither beat nor meaningfully lag the savings
stream — they roughly track it at ~1/6th the drawdown. Not FUNDABLE either, but a completely
different risk object.

## Caveat that must travel with any regime read

2022 starts clear both bars even as-shipped (ratio 0.70, P(beat) 72%, +$10,337). **This is not a
bear-market edge.** A 36-month horizon from a 2022 start spans the 2023-24 recovery — it means
"started near a bottom." Symmetrically, the `bull` bucket contains 2021 starts whose horizon runs into
the 2022 bear (ratio 1.81). Over a 36-month horizon the start label is a weak lens and should not
carry a conclusion.

## Verdict

**The $2k/month allowance reaches $20k fastest by being deposited, not traded.** Across 171 configs
spanning DTE {15,30} x TP {+30,+50,+100} x SL {−50,−70,dead-hold} x slots {1,2,3} x per-slot
{33%,50%,100%}, **nothing clears the pre-registered bar**, and only the equity arm even reaches
break-even net of contributions.

Three findings, in order of how much they should change behaviour:

1. **Options are structurally wrong at this account size, and it is not primarily a cost problem.**
   The frictionless control (Result 2, arm 3) still fails: ratio 0.92 against a 0.70 bar, P(beat)
   51.4% against a 60% bar, **100% median drawdown**. A zero-cost ladder is a coin flip against a
   savings account. Cost then converts the coin flip into a clear loss.
2. **Cost is nevertheless the dominant controllable lever, and it is self-inflicted by the
   affordability cascade.** Mean premium actually traded is $0.87/share, against which a $0.05
   floor is ~11.5% round trip; turnover of 6.1x lifetime contributions turns that into **72.6% of
   every dollar deposited**. The cascade *selects into* cheap contracts — when the top-ranked signal
   is unaffordable the ladder buys a cheaper one, systematically landing on exactly the contracts a
   fixed dollar floor punishes most. The grid's own ranking confirms this: the best option cell is
   the lowest-turnover, longest-dated, highest-premium one in the entire grid.
3. **Equities are a genuinely different risk object and roughly tie the null.** Best cell: median
   9.03 months vs the 8.97 null, `medNET` **+$437** (~0.8% of contributions), **P(reach $20k) = 1.000
   by month 18**, median drawdown 14.6%, worst 56%, tranche-loss 4.2%, unaffordable-skip **0.01%**.
   Not FUNDABLE — but "indistinguishable from saving, at 15% drawdown" is a very different sentence
   from the option arm's "−$32,816 median at ~100% drawdown".

**What would have to change for options to earn a place here.** Not a parameter — the physics. The
binding constraints are integer contracts, a dollar-denominated spread floor at sub-$1 premiums, and
an affordability cascade that correlates position selection with the worst friction. A config that
attacked all three (very few, very large, long-dated positions in high-priced underlyings) is
directionally what the grid's own winner already is, and it still loses. The honest conclusion is that
the $2k stage does not have an options answer; the ladder's value is in identifying **where** the
handoff to the main book becomes real, and that is above this checkpoint.

Pre-existing park that this does not disturb: the OTM cheap-explosive ladder (2026-07-20) is parked to
Dec-2026 for collapsing out-of-sample, so ATM-only is the correct scope here. Note the two studies now
agree from opposite directions — OTM cheapening fails out-of-sample, and ATM affordability fails on
friction.
