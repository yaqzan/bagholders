# Intraday vs Overnight — NULL on shippable alpha + a quantified backtest-realism haircut (2026-06-08)

**User question:** the "SPY gains more during market hours than off-hours" stat (the overnight/intraday
return puzzle) — is there a real arb between buy-open/sell-close (intraday) vs buy-close/sell-open
(overnight), and can our strategy exploit it?

**Verdict: NO arb for our 30-DTE options strategy. Two clean nulls + one honesty finding.**
1. On OUR universe the anomaly is an **OVERNIGHT** premium (our stocks gain at night), the *opposite* of
   the user's SPY-intraday stat — which is index-specific and recent.
2. The directional-feature bridge (trailing intraday/overnight tilt as a scoring signal) is **flat on the
   option barrier** — another price partition at ~70% apex15, exactly as `rel_strength` predicted.
3. The only execution touchpoint (entry timing) is **unfavorable**: our call signals gap UP +17.5 bps
   overnight (t=+6.2), so the live-attainable next-open entry is **−1.22 pp of funded win-rate worse**
   than the backtest's signal-close entry. A realism cost, not an opportunity.

No `ALGORITHM_VERSION` bump, no portfolio mechanism, no recalc. Holdout-era ledger (≤2026-05-15).

---

## The structural reason it can't be a tweak
We trade **30-DTE ATM options held ~1–3 weeks** (Apex: calls, TP+30%/SL−70%/HOLD-15). The overnight/
intraday anomaly is about holding the **underlying stock** across a single day's session windows. A
multi-week option hold spans *all* the intraday and *all* the overnight segments of its life — it cannot
selectively harvest one. So a literal "buy-open/sell-close" overlay is a **separate intraday/overnight
equity sleeve** (different instrument, different infra), not a modification of the current strategy. Only
two legitimate bridges touch us: a directional **scoring feature** (Bridge C) and **entry/exit timing**
(Bridge A). Both tested below.

## Data
- `build_prices.py` → `.cache/intraday_overnight/ohlc.parquet` (1.93M rows, 810 symbols, 2015→2026).
  **The only price cache in the repo with `open`** (every other one pulls close+high/low only), which the
  prevclose→open / open→close decomposition requires.
- Base call ledger: `.cache/rel_strength/rs_ledger.parquet` (v70 honest, 60-99 calls, barrier-agnostic
  `t_up`/`t_dn` forward outcomes, `vol_pct`). 75+ cohort = **4,699** signals (honest-v70 thins 75+ supply).

---

## PART 1 — Premise (mean per-bar log-return, bps): intraday (open→close) vs overnight (prevclose→open)

| series | ALL intra | ALL over | intra−over | pre-2020 intra−over | 2020+ intra−over |
|---|---:|---:|---:|---:|---:|
| SPY | +1.86 | +2.58 | **−0.72** | −1.31 | −0.26 |
| QQQ | +2.46 | +4.26 | **−1.81** | −2.72 | −1.09 |
| IWM | −1.36 | +4.87 | **−6.23** | −3.53 | −8.33 |
| **UNIVERSE (810 syms pooled)** | **−2.06** | **+4.97** | **−7.04** | −2.67 | **−9.84** |

- **On our stock universe, OVERNIGHT dominates intraday by ~7 bps/bar (−9.8 post-2020).** This is the
  classic Lou-Polk-Skouras overnight premium, and it got *stronger* post-2020, not weaker. Worst intraday
  years for the universe: 2018 (−13.4), 2020 (−18.0), 2021 (−17.9), 2024 (−18.8).
- **SPY itself is ~flat** (slight overnight edge). The user's "intraday > overnight" is an **index-level,
  recent-era** truth that does NOT generalize to a broad single-stock universe (small/mid caps especially
  — IWM −6.2 — gain at night). Only 2023 and 2026 are mildly intraday-positive for the universe.
- **Implication:** if anything, our universe says *hold overnight*, not intraday. The user's framing is
  inverted for our names.

## PART 2 — Bridge A: entry-timing on CALL signals (overall≥75)
`gap = next_open / signal_close − 1` = the realism delta between the live-attainable next-open entry and
the backtest's signal-close entry.

- **OVERALL: mean gap = +17.5 bps, t_vs0 = +6.19, N=4699** (+0.0557σ in vol units). Uniform across tiers
  (75-79 +17.3, 80-84 +17.9, 85+ +18.3). Consistently positive in the modern era (2021 +28, 2024 +32,
  2025 +24, 2026 +36); only 2019/2023 mildly negative on small N.
- **Next-day intraday (open→close) = +2.8 bps, t=+0.68 (insignificant).** The drift on our signals is
  concentrated *overnight*; after the gap-up the next session is flat. So buying the open to "ride the
  intraday" captures noise.
- **Reading:** our bullish signals gap UP overnight → (a) there is **no "buy cheaper at the open" arb**
  (the reverse), and (b) the backtest's signal-close entry is **optimistic** — you can't buy a close you
  just used to decide.

### Bridge A, quantified (`realism_walk.py`, funded apex15 re-walk under both entry anchors)

| cohort | win@close (backtest) | win@open (live next-open) | haircut |
|---|---:|---:|---:|
| **ALL 75+** | **73.01%** | **71.79%** | **−1.22 pp** |
| 75-79 | 72.70% | 71.50% | −1.21 |
| 80-84 | 71.58% | 69.80% | −1.78 |
| 85+ | 78.04% | 77.51% | −0.53 |

By year, the haircut is −1.7 to −3.7 pp across the modern era (2021/2022/2025/2026), positive only in the
low-N 2016/2019/2023. Net 55 trades flip from win→lose. **The funded backtest/MC is ~1.2 pp optimistic on
call win-rate** because it assumes an unattainable entry price. Because the funded barrier is asymmetric
(TP+30% / SL−70%), edge is win-rate-driven, so −1.2pp WR is a **non-trivial fraction of the per-trade
edge** — the live edge is materially thinner than the headline backtest (cushioned somewhat by the
dead-hold, which this raw re-walk doesn't model). This is a **realism haircut, not an improvement** — it
makes the numbers worse/honest, so it is NOT an autonomous overnight ship; it's a user-decision
methodology question (see below).

## PART 3 — Bridge C: trailing intraday/overnight tilt as a directional feature (NULL)
Feature (strictly-prior trailing-20-bar, ending at signal date): `io_tilt = mean(intra20) − mean(over20)`,
plus `intra20`, `over20` separately. Cohort test on funded apex15 (baseline 70.0%):

| feature | Q5−Q1 win% Δ | two-prop z |
|---|---:|---:|
| io_tilt | +0.1 pp | +0.05 |
| intra20 | −2.9 pp | −1.36 |
| over20 | −3.9 pp | −1.83 |

All flat, none near +3. **Confirms the `rel_strength` meta-lesson**: every price-technical partition lands
~flat on the 15-d option barrier (dominated by realized-vol *path*, not signal direction). Another closed
directional door.

---

## The one real, harvestable effect (out of scope) → parked lead
The +17.5 bps overnight drift on signal stocks (t=6.2) IS real and tradeable — but only by **holding the
underlying stock overnight** (buy at close, sell at open). That is a **separate equity sleeve**, not the
options strategy: ~1.8 signals/day on the 75+ cohort, +17.5 bps gross/overnight, ~+11–15 bps net of a
liquid round-trip spread. A genuine but thin, new product (needs its own sizing/capacity/engine).
Parked in `alpha_mining/NEW_LEADS.md`, not built — orthogonal to Apex.

## Recommendation (user decision)
**Backtest entry realism.** Our MC/backtest (and the live Portfolio engine) anchor call entry at the
signal-date close, which overstates the funded win-rate by ~1.2 pp. Options:
1. **Leave as-is** (treat the ~1.2pp as a known optimism buffer; the relative ranking of mechanisms is
   unaffected since it applies uniformly). Simplest; honest if documented.
2. **Model next-open entry** (env-gated entry-realism haircut ≈ +0.056σ, or shift the entry anchor) and
   re-run a smoke MC to size the compound/DD impact. This is an invasive change to the whole validation
   stack and shifts every documented number — a deliberate user call, not an overnight auto-ship.

I recommend option 1 + this documented note, unless you want the next-open entry modeled (I can wire it
env-gated and MC it as a clean follow-up).

## FOLLOW-UP (user push, 2026-06-08): "arb the overnight via SHORT-DTE calls (3/5/7DTE/WR7), buy before
## close, day-of-week aware" — DECISIVE NULL (theta wall)
The reframe: instead of treating the +17.5bps overnight gap as a cost, HOLD it — buy a short-dated call
at the close (MOC, so no realism gap), let shorter DTE lever the pop, be weekday-aware. `overnight_sleeve.py`
models the real option P&L (`option_pricing.option_pnl_pct`: delta+theta+vega) generalized to short DTE
(`premium_mult = 1.82·√(DTE/30)`, `theta = √((DTE−τ)/DTE)−1`, τ = CALENDAR days held so Fri→Mon pays 3
days of weekend theta), entry = signal close, spread = round-trip % of premium.

**Result: net-negative everywhere, even GROSS (0% spread), and MONOTONICALLY WORSE for shorter DTE:**

| DTE | overnight (buy close→sell open) gross | win% | net @6% spread |
|---|---:|---:|---:|
| 3 | **−41.8%** | 17.3% | −47.8% |
| 5 | −22.1% | 22.6% | −28.1% |
| 7 | −15.7% | 25.1% | −21.7% |
| 15 | −7.7% | 29.5% | −13.7% |
| 30 | −4.3% | 33.1% | −10.3% |

The user's "shorter DTE = more leverage on the gap" is right on the leverage axis but **theta-per-night
(∝ ~1/√DTE) grows faster** and dominates. The +17.5 bps gap (×0.5 delta) doesn't cover even **one night of
30-DTE theta** — so the most-favorable case (30DTE) is already −4.3% gross. Confirms the documented
30>15>shorter DTE ordering (15DTE underperforms 30DTE; shorter is worse still).

**Day-of-week (Table 2, 5DTE overnight):** gaps by entry weekday Mon +7.0 / Tue +34.9 / Wed +2.4 /
Thu +10.8 / **Fri +25.0** bps. Friday (the weekend-gap capture) is the **WORST** P&L (−39.7% gross) — 3
calendar-days of weekend theta swamps the bigger gap. **Best-case scan: 0 of 75 (DTE×exit×weekday) cells
net-positive at 6% spread.** Day-of-week does not rescue it.

**Is there a theta/gamma sweet-spot DTE? (user follow-up) — NO interior optimum; the limit IS the stock.**
Extended the DTE scan to 45/60/90/180 + a STOCK benchmark (`overnight_sleeve.py`). The capture/theta
trade-off (as % of premium): gap-capture ∝ 1/√DTE, theta/night ∝ 1/DTE → theta shrinks *faster* as DTE
grows, so the sweet-spot direction is LONGER DTE, not shorter. Empirically the option overnight P&L climbs
monotonically toward zero but **never crosses positive (gross) and never beats the stock**:

| DTE | 3 | 5 | 7 | 15 | 30 | 45 | 60 | 90 | 180 | STOCK (DTE→∞) |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| gross overnight P&L | −41.8% | −22.1% | −15.7% | −7.7% | −4.3% | −3.1% | −2.5% | −1.8% | −1.1% | **+0.175%** |

(option rows = return on PREMIUM, already leveraged; STOCK = return on CAPITAL, unlevered, 55.5% win,
+0.115% net of 6bps; +0.246% to next close, +0.543% over 3 days.) The option ASYMPTOTES to the stock from
below: at long DTE the premium is so large the +0.056σ gap is a tiny % of it (leverage gone) while a tiny
theta + a wider spread remain. **An option = stock-delta − theta − wider-spread → strictly dominated at
every DTE for capturing a small directional drift.** The "sweet spot" resolves to *just hold the stock*
(or margin it for leverage — not options). **Economic core:** buying any tradeable-DTE option to hold
across the overnight = paying theta to rent gamma over a low-realized-move window (+0.056σ) — a structural loss. The only vehicle with ~zero overnight
carry + tiny spread is the UNDERLYING (the parked equity sleeve). The *structural inverse* (SELLING
short-DTE options overnight to harvest theta) is the side with the edge — but that's a short-gamma strategy
with unbounded gap-up risk, outside the long-calls mandate, NOT pursued. WR7/multi-day holds (`d3` column)
are even worse (more theta, and Part 2 showed post-signal intraday is flat). **Thread fully closed.**

## EQUITY SLEEVE vs v70 APEX (user follow-up): does holding the signal STOCK compound better? — NO
`equity_sleeve.py`: deterministic chronological portfolio sim on the SAME 4,699 75+ v70 signals, same
cascade (20/15/10/10), MaxPos=14, $50k, 10y. Entry = signal close (MOC — realistic for this sleeve, no
entry-gap). Stock spread 6bps round-trip. Leverage-invariant metrics (Sharpe, Calmar=CAGR/maxDD) decide
"better compounding at matched risk"; a leverage sweep shows where levered equity COLLAPSES.

| strategy (10y, $50k) | total | CAGR | maxDD | Sharpe | Calmar | collapse |
|---|--:|--:|--:|--:|--:|:--:|
| SPY buy-hold | +261% | 13.7% | 34.1% | 0.81 | 0.40 | no |
| equity overnight (L=1) | +55% | 4.5% | 14.3% | 0.83 | 0.31 | no |
| equity 1d (L=1) | +112% | 7.8% | 24.0% | 0.86 | 0.33 | no |
| equity 3d (L=1) | +342% | 16.0% | 33.4% | 1.23 | 0.48 | no |
| **equity 5d (L=1)** | +500% | 19.6% | 38.3% | **1.25** | 0.51 | no |
| equity 5d @ L=3 (DD-matched to Apex) | **+7,039%** | 53.2% | 82.5% | 1.20 | 0.64 | no |
| equity 5d @ L=5 | +9,267% | 57.4% | 96.2% | 1.03 | 0.60 | no |
| equity 5d @ L>=8 | −100% | — | 100% | — | — | **YES** |
| **v70 Apex (documented full engine, leveraged options)** | **+16,953%** | ~67% | ~84% | — | **~0.80** | **no** |

**Verdict — Apex wins compounding, both raw and risk-adjusted, AND is collapse-proof:**
1. At matched ~84% DD (equity L=3), Apex compounds **~2.4× more** (+16,953% vs +7,039%) and has higher
   Calmar (~0.80 vs ~0.64). The hypothesis "equity sleeve compounds better than Apex" is **FALSE**.
2. **Structural reason = defined risk.** Levered equity COLLAPSES at L>=8 (gap-downs wipe the levered
   book); Apex never collapses (max loss/trade = premium). Options are the better *survivable*-leverage
   vehicle — the "leveraged-momentum, collapse=0 hard floor" thesis, quantified.
3. The in-harness SIMPLIFIED apex (no dead-hold) COLLAPSED (−100%) — independently re-confirming the
   dead-hold is collapse-preventing (documented `dh_off`=100% collapse); the real engine is the +16,953% ref.

**Silver lining — the equity sleeve IS a genuine SPY-beater (standalone):** unlevered 5d hold = Sharpe
1.25 / Calmar 0.51 / CAGR 19.6% vs SPY 0.81 / 0.40 / 13.7%, at SPY-like DD, 0 collapse. The PURE OVERNIGHT
version (the original idea) is weak (+4.5%/yr) — the edge is in the 3-5d hold, i.e. short-horizon equity
MOMENTUM on the signals (so the honest benchmark is MTUM, not just SPY — per the v69 fundability finding
that the edge is largely momentum-beta; MTUM-relative alpha is the untested follow-up). **CAVEATS:**
deterministic single path (not MC); momentum-beta not proven-alpha; no margin-borrow cost on the levered
rows (would shave levered CAGR); MTUM benchmark not yet run. **Not a replacement for Apex; at most a
separate, lower-DD diversifier book — and it's correlated to Apex's momentum exposure.**

## Artifacts
- `build_prices.py` — OHLC pull (the open-bearing cache).
- `analyze.py` — Parts 1/2/3 (premise, entry-gap, directional-feature cohort-z).
- `realism_walk.py` — funded apex15 re-walk under close vs next-open anchors (the −1.22pp number).
- `overnight_sleeve.py` — short-DTE overnight-capture P&L sim (DTE×exit×weekday, real theta+spread) → null.
- `equity_sleeve.py` — equity-sleeve vs Apex portfolio sim (holds × leverage, Sharpe/Calmar/collapse) → Apex wins.
