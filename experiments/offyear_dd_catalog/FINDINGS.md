# Off-year drawdown miss-catalog → sector-clustering pre-emption = NULL (2026-06-09)

**/research run 2 this session** (after the option-skew STAGE). User ask: "miss-catalog the non-2024
years and find ways to pre-empt drawdowns." Hypothesis: the off-year (2021/2023/2025/2018) drawdowns are
**sector/factor-correlated crashes** → a **sector-concentration exposure cap** (un-shipped Stage-3 lever
flagged in assessment-backtest.md) could pre-empt them. **Result: NULL** — the off-year DD is NOT a
sector crash; it's a diffuse cross-sector momentum-factor reversal with no cappable sub-structure.
Pure tape mining on the existing live-Apex MC tapes (`.cache/dd_ledger/tape_*.parquet`), no new compute.

---

## What the off-year drawdown actually IS (the catalog)

The drawdown driver is the **−90% `dh_expiry` bag** (confirmed in the 2024-factor deep-dive: off-years
bleed via these, 2024's edge was 70% avoiding them). Cataloguing the bags across 2021/2023/2025/2024:

| signature tested | result | verdict |
|---|---|---|
| **sector concentration** of bags vs book baseline | bag-Herfindahl ≈ book-Herfindahl (0.143→0.153, 0.171→0.199, 0.141→0.143); top bag-sector Δ only **+3 to +6pp** | bags are ~sector-distributed like the book — **NOT a single-sector crash** |
| **temporal clustering** (entry weeks) | bags enter across **40-47 distinct weeks**, worst week 6-9%, top-3 15-20% | diffuse, not a burst |
| **synchronized death** (exit weeks) | bags die across **39-46 weeks**, worst-exit-week 6-15%, top-3 17-26% | a steady drip, **not a synchronized crash** |
| **same-sector concurrency → bags** (the cap's rationale, controlling for total concurrency) | **sign FLIPS by year**: t=+3.0 (2021), +4.7 (2023), **−5.6 (2025), −5.1 (2024)** | regime artifact — NOT a consistent DD signal |

The same-sector-concurrency sign-flip is the decisive kill. High same-sector crowding is a **high-bag**
cohort in reversal years (2023: ssc 6+ bagrate 28.6% vs 16.8% at ssc 0-1) but the **BEST** cohort in
momentum-persistent years (2024: ssc 6+ bagrate 5.6%, mean pnl +0.169 — the richest bucket). corr(same-
sector, total concurrency) is low (+0.02 to +0.12), so it's not redundant with the captured total-
concurrency — it's just **not a stable signal**. A static sector cap would HURT the 2024-type years
(capping the winning crowded sector) and only help the 2023-type — net sign-unstable → fails the gate.

## Why (the mechanism)

The off-year crashes are **momentum-FACTOR reversals, not SECTOR crashes**: the high-momentum names the
75+ scorer holds span all sectors, and when the momentum factor turns they reverse together but
*cross-sector* and *spread over weeks* (a drip, not a burst). The book's only real common factor is "high
75+ momentum score" — which is the strategy's **definitional exposure**, not a diversifiable sub-factor.
Whether a crowded sector's momentum **persists (2024) or reverses (2023/2025)** is the same persist-vs-
crash distinction that is unpredictable ex-ante (2024-factor §4: H1 doesn't forecast H2; G20: dead-hold
recoverers/expirers not separable at entry; G14: any price partition → opt15 ~47%).

## Conclusion — no un-exploited structured DD signature to pre-empt

The off-year bleed has **no cappable sub-cohort** (sector, timing, or crowding). It is the momentum
factor's inherent, diffuse, unpredictable crash risk, and it is **already maximally mitigated** by the
shipped stack:
- **dead-hold** defers each −70% SL bag into a deferred-recovery (the per-name loss-side lever);
- **RXDD/MWDD/SVR/TVDD/F3F** contract sizing on the broad-market crash symptoms;
- **MAX_POS** caps total concurrency (and the sector cap adds nothing consistent beyond it).

The two real forward paths to "pre-empt off-year drawdowns" are BOTH already on the board, neither a
sector cap:
1. **Real-time momentum-regime de-risking** — that IS what the shipped market-context levers do; the
   residual is the unpredictability wall (can't tell a 2024 from a 2023 early), and the DD-sizing well is
   documented dry (G23).
2. **A per-name persist-vs-crash discriminator** — the **option-skew** lead (staged this session,
   `experiments/year_2024_factor/OSK_SHIP_HANDOFF.md`, data-locked); price-based discriminators are
   nulled (G14/G20).

**No ship, no stage of a mechanism** — a clean documented NULL + this catalog. Added to NEW_LEADS
do-not-retry traps + research skill G26.

## Artifacts
`experiments/offyear_dd_catalog/cheap_first_cut.py` (sector + temporal clustering) ·
`sector_concur_mine.py` (same-sector-concurrency orthogonality + synchronized-death). Tapes:
`.cache/dd_ledger/tape_{2021,2023,2024,2025}.parquet`.
