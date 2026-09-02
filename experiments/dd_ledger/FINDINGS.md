# DD-Ledger Findings — v32, 5y N=300×8 windows (2026-05-03)

## Setup

- **Algorithm**: v32 (`43eecea`)
- **Windows**: 2021, 2022, 2023, 2024, dip, 22-now, 2025, 5y
- **Iterations**: N=300 per window (seeded MC, single mode, bounded-fill option pricing)
- **Total trades**: 4.83M closed positions across 2,400 (window × seed) paths
- **DD attribution**: proportional — for each seed's worst peak-to-trough episode, every trade open during [start, trough] gets `pnl_dollars / sum(in_episode_pnl_dollars)` share. v1 attributes only to rank-0 episode per seed; rank-1/2 captured but unused.

**Key metric definitions:**
- **DD-loser rate** = `P(trade in worst-DD episode AND pnl_dollars < 0)`
- **lift** = `cohort_loser_rate / baseline_loser_rate`
- **z** = standardized deviation under H0 = baseline rate
- **DD-conc** = `cohort's_DD$_share / cohort's_trade_count_share`. >1.0× = cell carries more DD weight than its size.

**Caveat — DD$ totals are window-biased.** Late-window (5y, 22-now) dollar totals dominate because compounding makes premium_cost grow ~10⁶× over the run. **Rank by lift/z and DD-conc, not DD$ total.** Where a pattern shows up consistently across multiple windows AND has high lift+conc, the signal is real.

---

## Headline patterns (across all CALL + PUT cohorts)

### Top 5 single-feature patterns by |z|

| # | feature × cell | cohort | N | DD-loser rate | lift | z | DD-conc |
|--:|---|---|---:|---:|---:|---:|---:|
| 1 | `entry_dd=deep` (≥40% running DD) | PUT all ≤25 | 54k | 13.8% | 5.93 | +177 | 18.4× |
| 2 | `entry_dd=deep` | CALL all 75+ | 91k | 8.6% | 5.21 | +165 | 2.07× |
| 3 | `brd=vhi` (≥80) | PUT all ≤25 | 28k | 16.9% | 7.26 | +160 | 1.86× |
| 4 | `entry_dd=deep` | CALL 75-79 | 45k | 9.2% | 5.98 | +132 | 3.69× |
| 5 | `brd=vhi` | PUT 16-20 | 12k | 22.6% | 8.24 | +135 | 1.53× |

### Top 5 two-feature patterns by |z|

| # | pattern | cohort | N | DD-loser rate | lift | z | DD-conc |
|--:|---|---|---:|---:|---:|---:|---:|
| 1 | `brd=vhi & window=2023` | PUT 16-20 | 3,061 | 67.2% | 24.5 | +218 | — |
| 2 | `brd=vhi & window=2023` | PUT all ≤25 | 5,547 | 61.1% | 26.3 | +291 | — |
| 3 | `entry_dd=deep & wadj=neg` | PUT all ≤25 | 44,623 | 13.9% | 5.97 | +162 | **21.3×** |
| 4 | `entry_dd=mid & regime=HEALTHY` | CALL 75-79 | 30,075 | 6.9% | 4.48 | +76 | **19.7×** |
| 5 | `entry_dd=mid & regime=HEALTHY` | CALL 75+ | 62,252 | 6.5% | 3.95 | +95 | **10.2×** |

---

## Ranked Hypotheses (MC-testable)

The "MC sim cost" column estimates compute on `sim_mc_bridge.py` (~10 min) for per-trade pre-screen + canonical N=300 × 8-window for ship gate.

### H1 [HIGH] — Block PUT entries when `breadth_score ≥ 80`

**Evidence:**
- PUT 16-20 × `brd=vhi`: 22.6% DD-loser rate (lift 8.24, z=+135) on N=12,239
- PUT 21-25 × `brd=vhi`: 29.2% DD-loser rate (lift 9.43, z=+104) on N=4,812
- PUT ≤15 × `brd=vhi`: 4.5% (less concentrated — keep)
- Concentrated in 2023 narrow-bull window: PUT 16-20 × brd=vhi × 2023 = **67.2% loser rate** (lift 24.5, N=3,061)

**Mechanism:** When market participation is wildly broad (breadth ≥80), put signals are fighting the entire tape. The earnings-suppression and weekly-confirmation lifts already filter some, but the brd=vhi cell still has ~12k trades/path that consistently lose.

**Existing infrastructure:** `PUT_TIGHTEN_BREADTH_GE` is wired in `monte_carlo.py` and `backtest_cascade.py` but currently `=0` (disabled).

**Proposed config edit:**
```python
PUT_TIGHTEN_BREADTH_GE = 80      # (currently 0 / disabled)
PUT_TIGHTEN_THRESH = 15          # only filter puts with overall in (15, 25] — keep deep ≤15 puts
```

**MC test plan:** sim_mc_bridge then N=300 × 8 windows. Expected effect: removes ~16k puts/year, freeing slot capacity. **Expected to improve 2023 + 22-now DD without much call upside lost** (puts and calls compete for the 14-slot pool).

**Ship gate:** P3 5y ≥ baseline AND P4 no annual >25% regression. **Risk:** could hurt deep-bear scenarios where vhi breadth → reversal (e.g. 2022 Q1 → Q2 transition). Watch 2022 specifically.

---

### H2 [HIGH] — Drop PUT entries when `breadth_5d_chg ≥ +10` (surge)

**Evidence:**
- PUT all ≤25 × `brd5d=surge`: 4.5% loser rate (lift 1.94, z=+77, N=277,644)
- PUT 16-20 × `brd5d=surge`: 8.3% loser rate (lift 3.04, z=+88, N=66,116)
- PUT 21-25 × `brd5d=surge`: 6.2% loser rate (lift 2.00, z=+41)
- Combined with brd=vhi: PUT 16-20 × `brd=vhi & brd5d=surge` = 21.7% (z=+123, N=11,151)
- 2023 amplifies: PUT 16-20 × `window=2023 & brd5d=surge` = **36.8%** loser rate (z=+201, N=9,344)

**Mechanism:** A breadth surge over the prior 5 days means broad participation has already turned UP — puts entered into that flow are catching falling knives in the wrong direction.

**Distinct from H1:** brd_now level vs brd 5d trajectory. They overlap (~50% co-occurrence) but the trajectory captures puts entered during a *just-recovered* tape that the level would have admitted (e.g. brd recovers from 60→78, level gate at ≥80 misses it; trajectory gate at +10 catches it).

**Proposed config edit:**
```python
# New constants in monte_carlo.py + strategy_config.py:
PUT_BRD5D_SURGE_DROP = True
PUT_BRD5D_SURGE_THRESH = 10.0   # drop puts when breadth_5d_chg >= +10
PUT_BRD5D_SURGE_MIN_OV = 16     # only filter puts with overall in [16, 25]
PUT_BRD5D_SURGE_MAX_OV = 25
```

**MC test plan:** Same as H1. Test as alternative to H1, then jointly. Joint signal might be over-restrictive — only one needed.

---

### H3 [HIGH] — Add `entry_dd ≥ 0.20` cap on call cascade

**Evidence:**
- CALL 75-79 × `entry_dd=mid (0.20-0.40) & regime=HEALTHY`: 6.9% loser rate (lift 4.48, z=+76, N=30,075, **DD-conc 19.7×** — 36% of cohort DD$ from 1.8% of trades)
- CALL 75+ × `entry_dd=mid & regime=HEALTHY`: DD-conc 10.2× (z=+95)
- CALL 80-84 × `entry_dd=deep & regime=HEALTHY`: 13.5% loser rate (lift 7.50, z=+66, DD-conc 3.66×)
- Stronger when combined with `brd=mid_lo`: CALL 75-79 × `entry_dd=mid & brd=mid_lo` = 5.1% loser rate, DD-conc 7.16× (N=115,248)

**Mechanism:** When the portfolio is already in mid-range DD (20-40%) AND the market is *healthy* (regime composite says BULL/HEALTHY), the cascade keeps firing because neither the DD circuit breaker (60% threshold) nor F3F (breadth-driven) sees a reason to stop. But the mid-DD state means recovery requires a string of wins — and the HEALTHY regime is structurally where mean-reversion catches calls (per H4 below).

**Distinct from existing DD breaker:** Current breaker fires at 60% DD. H3 fires at 20-40% — much earlier intervention.

**Proposed config edit (in strategy_config.py):**
```python
# Soft cascade contraction in mid-DD (independent of F3F + DD breaker):
DD_SOFT_BAND_LO = 0.20       # start scaling down call alloc at 20% DD
DD_SOFT_BAND_HI = 0.40       # full contraction at 40%
DD_SOFT_CALL_FLOOR = 0.50    # alloc multiplier at HI (matches F3F floor for symmetry)
# Applied: alloc *= 1.0 if dd<LO else linear(LO→HI: 1.0→FLOOR)
```

**MC test plan:** sim_mc_bridge for cohort effect, then N=300 × 8 windows. Watch 5y compound — this is an overlay on the existing breaker, so the danger is over-restricting call deployment in years like 2024 where DD oscillates 25-35% but the strategy still compounds well.

---

### H4 [MEDIUM] — Concurrent-call cap when `concur_calls ≥ 8`

**Evidence:**
- CALL 80-84 × `concur_calls=hi (8-12)`: 3.0% loser rate (lift 1.66, z=+27, **DD-conc 5.95×** — 65% of cohort DD$ from 11% of trades)
- CALL 75-79 × `concur_calls=hi`: DD-conc 2.60× (52.6% of DD$)
- CALL 85-94 × `concur_calls=hi`: DD-conc 3.09×
- 2-feature: CALL 80-84 × `entry_dd=deep & concur_calls=hi` = 13.6% loser rate (z=+51, N=3,347)

**Mechanism:** With 8+ concurrent calls open, a single market-wide selloff hits all of them — correlated drawdown is the killer. MAX_POSITIONS=14 is currently the ceiling; F3F doesn't see *what's already open*, only *current breadth*.

**Existing infrastructure:** `MAX_POSITIONS_CALL` exists as a separate side cap (currently None — uses MAX_POSITIONS). H4 is a *concurrent-side cap* that activates the side-capped path.

**Proposed config edit:**
```python
MAX_POSITIONS_CALL = 8       # cap calls at 8 (currently uses 14 shared)
MAX_POSITIONS_PUT  = 8       # symmetric cap
```

**Trade-off:** This explicitly forfeits the 5+ extra call slots that compound during pure-bull years (2024 = +685% under current config benefits from full deployment). Likely a net DD-vs-return trade. **Could combine with H3 — H3 contracts allocation in mid-DD; H4 caps count regardless.**

**MC test plan:** N=300 × 8 windows. Test 8/8, 10/10, 12/12 split. Lock decision on 5y; 22-now is confirmation.

---

### H5 [MEDIUM] — Soft-block dip-window-style cascades via `concur_calls=hi & window=dip`

**Evidence:**
- CALL 85-94 × `window=dip & concur_calls=hi`: **54.8% DD-loser rate**, lift 31.1, z=+140, N=1,210
- This is the single most extreme cell in the entire mining run.
- "dip" window = Nov 2025–Apr 2026 (the 5-month flat-to-down tape that exposed the F3F-pre-fix composite inversion bug)

**Why it's interesting:** dip is *already addressed* by F3F (shipped 2026-04-24, +18% on dip alone), yet trades still cluster at 55% loser rate when 8+ calls are concurrent. The F3F call floor (0.50 at brd=30) restricts size per trade but doesn't stop *count* — and dip's brd_score frequently sits in the 35-50 mid_lo band where F3F is mid-strength.

**This is a confirming case for H4** rather than a separate hypothesis. If H4 ships with `MAX_POSITIONS_CALL=8`, H5's signal disappears mechanically.

**Standalone alternative:** `dip_aware = (rolling 60d portfolio_value below 80% of 90d peak)` — count cap = 6 only when this is true. More complex, weaker test. Defer unless H4 by itself loses dip.

---

### H6 [LOW] — `wadj=neg` × `entry_dd=deep` PUT filter

**Evidence:**
- PUT all ≤25 × `entry_dd=deep & wadj=neg`: 13.9% loser rate (lift 5.97, z=+162, N=44,623, **DD-conc 21.3×**)
- 52% of cohort DD$ from 2.4% of trades

**Mechanism:** Puts opened during deep portfolio DD with bearish weekly drag (`wadj < 0`) are double-discounted by the v27 WCF lift mechanism — overall is lifted *toward* 50, so they qualify at the boundary, then concentrate losses when entry_dd is deep.

**Why LOW:** This cohort is largely covered by H3's DD-aware contraction (deep entry_dd applies to puts too). Worth a dedicated filter only if H3 + H1/H2 together don't capture it.

**Test:** Defer until H1-H4 are settled; re-mine residual.

---

### H7 [LOW] — `b_ta=lo` filter on PUT ≤15

**Evidence:**
- PUT ≤15 × `b_ta=lo`: 7.9% loser rate (lift 4.26, z=+39, **DD-conc 67.2×** — 50% of cohort DD$ from 0.7% of trades)
- Tiny N (7,454) but extreme concentration

**Caveat:** small absolute count means impact is bounded. Worth a per-trade filter test if other hypotheses don't reduce DD enough.

---

## Anti-patterns (DO NOT pursue)

These showed up but are either already-shipped levers or known dead ends from past sweeps:

| Pattern | Why skip |
|---|---|
| `mis_stress=on` is DD-protective (DD-conc 0.33×) | This is the **v25 mis-stress softener doing its job** — it correctly identifies and softens the cohort that would otherwise contribute to DD. No new mechanism needed. |
| `regime=CAUTION` is DD-protective (lift 0.61, z=−50) | F3F + regime slope already captures this. Not actionable. |
| `b_concur_calls=lo` for puts is DD-amplifying (lift 1.42, z=+62) | Inverse causality: when concurrent calls are low, the fill order admits more puts which then carry the DD. Filtering "low concur calls" would just shift the DD elsewhere. |
| `b_brd5d=fall` for puts is DD-amplifying | Catches puts entering in already-collapsing tape. These are the *correctly-positioned* puts that get caught in a Fed-pivot bounce. Removing them would hurt 2022 bear-year performance. |

---

## Ship gate / N considerations

Per [known-issues.md](../../.claude/docs/known-issues.md):
- **N=300 single-window** has 1.6-1.8× compound noise floor; 5y compound can swing 4 orders of magnitude. **DD signal is reliable at N≥300 (±3pp inter-seed).**
- **Lock decisions on 5y compound; treat 22-now as confirmation.** N=150 4-window screening is insufficient (Phase OP1 lesson).
- **All hypotheses here are portfolio-stage** (no `Score.overall` change) → P1-P6 gate from [assessment-backtest.md](../../.claude/docs/assessment-backtest.md). No `ALGORITHM_VERSION` bump.

## Recommended order to test

1. **H1 (PUT_TIGHTEN_BREADTH_GE = 80, MIN_OV=15)** — single-axis flag, existing infrastructure, biggest single |z| signal. ~30 min.
2. **H2 (PUT_BRD5D_SURGE_DROP)** — new flag, new constants. ~1h to wire + 30 min N=300. Test as alternative to H1, then jointly.
3. **H4 (MAX_POSITIONS_CALL = 8)** — single config flip, no new code. ~30 min.
4. **H3 (DD_SOFT_BAND)** — new mechanism, ~30 LOC for the alloc multiplier hook. Test in isolation, then stacked on top of best of {H1, H2}.
5. **H6/H7** — only if 1-4 don't move 5y DD-C below 75%.

## Investigation infrastructure

```
experiments/dd_ledger/
├── build_dd_ledger.py     # joins MC tape (.cache/dd_ledger/tape_*.parquet) with miss-ledger features
├── analyze_dd_ledger.py   # cohort lift/z mining → report.md
├── report.md              # auto-generated raw cohort tables (every cohort, top |z| + top DD-conc)
└── FINDINGS.md            # this file — synthesized hypotheses

# Re-build:
MC_TRADE_TAPE=1 N_ITER_OVERRIDE=300 python -u monte_carlo.py
python -u experiments/dd_ledger/build_dd_ledger.py --version 32
python -u experiments/dd_ledger/analyze_dd_ledger.py --version 32
```

The trade tape is env-flagged (`MC_TRADE_TAPE=1`) — zero overhead when off, ~50MB/window when on.
