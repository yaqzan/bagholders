# MASTER FINDINGS — The v69 Honest-Substrate Reckoning (2026-05-31)

The definitive record of what we learned when the weekly look-ahead was removed. Read
top to bottom; the conclusion **changed twice** as evidence came in.

---

## TL;DR (the conclusion that actually matters)

1. **The weekly look-ahead bug inflated every backtest by ~12pp** (75+ optTP15 63-67% → 51% honest). Live trading was always honest; only the recalc/calibration was contaminated.
2. ~~**The OPTIONS strategy does not beat SPY after realistic costs.**~~ **CORRECTED 2026-06-06 — that cost-based verdict was a 6%-spread artifact; OVERTURNED.** The original claim modeled a symmetric ~6% round-trip on the v69-hygiene 85+-only config. Re-cost at the realistic **1.5-3%** band — both the 2026-06-02 asymmetric canon (only forced exits pay the half-spread) and a conservative symmetric round-trip bound — on the **live v70 Apex** (75+ cascade + overflow + dead-hold + RXDD + SVR + MWDD): it beats buy-and-hold SPY by **~100-1000×** on compound across EVERY window incl the 2022 bear, **collapse=0**, even at the worst point (sym 3% round-trip: **5y +9,859% vs SPY +89.8%; 2022 +38.6% vs −19.9%**); the realistic asym end is ~5,000× SPY. Even the *old v69 85+-only* config beats SPY at 3% (+4,810% 5y / 40% DD). **Cost is NOT the binding constraint** — the binding constraints are drawdown tolerance (Apex ~62% 5y / ~80% 10y vs SPY ~25-34%) and liquidity/capacity on rare high-tier signals (the unmodeled drag; Liquidity-Aware Cascade priority). The 85+-only / Sentinel-style configs are the genuine lower-DD (~39-40%) frontier point. Record: `experiments/fundability_recost/recost.py` (`recost_n200.jsonl`).
3. **BUT the underlying DIRECTIONAL SIGNAL has real, statistically-significant stock-selection alpha** (+1.38%/15d market-adjusted, t=5.69 at 75+). **The options wrapper was destroying a real edge.** Whether it's a fundable *stock* strategy (no option spread) is the open, well-motivated question — needs factor-adjustment + out-of-sample + cost/tax test.
4. **The weekly cannot be "reverse-derived."** The honest weekly carries zero incremental edge (corr -0.016); it's fully baked into the score. The look-ahead's 12pp was the first ~3 days of each trade's own forward window (early resolution), not a recoverable unknown.

---

## APEX OPTIONS-STRATEGY BUILD — BUILT & SHIPPED 2026-06-02 (record; live in portfolio_profiles.json)

The investigation pivoted from "is honest-v69 fundable as-shipped" to **"build the optimal portfolio strategy ON the honest-v69 substrate, tuned for explosive early-buildout growth."** Risk framing is the **risk-budget ethos** now in [process.md](../../.claude/docs/process.md): DD is a *budget* scaled to portfolio maturity, not a universal gate. **Apex is the default** (small book + regenerating income → high *recoverable* DD is an acceptable price for compounding off a small base). The one hard floor for every profile incl. Apex: **collapse-rate = 0** — ruin is unrecoverable at any portfolio size; an 85% *recoverable* DD is not.

**Primary goal: 75+ calls, deploy at EVERY 75+ signal, explosive compounding.** Not 85+. 85+ is the Sentinel/low-DD point; **75+ (richest signal count) is the Apex point** — high DD accepted as long as collapse=0. We *want* to fill every 75+ slot; that is the user's stated optimal-compounding thesis.

**Findings (honest v70, calls-only, ~3% spread, MC w/ collapse over full 10y incl 2020-COVID):**
- **SHIPPED Apex call config: 75+ · SL −70% · TP 30% · 50% exposure cap · HOLD-to-day-15 · UNCAPPED base.** N=300 validated: **+17,026% / 10y (≈+16,953% at N=250), 86% DD, 0% collapse on every window incl 2020-COVID.** Now the DEFAULT profile in `strategy_config.py` + `portfolio_profiles.json`. N=300 lock + put-tail DONE.
- **HOLD ≫ CUT for calls** — don't early-stop; the early cut bleeds the ~68% of losers that recover to TP. "Capital velocity / recycling" was a frictionless-era fiction; at real spread, high turnover *is* the bleed (CUT-at-70+ collapsed 100%). "HOLD" = wide SL + **still sell at the day-15 WR15 barrier** (riding to 30-DTE expiry was strictly worse).
- **Optimal call SL is wide-but-finite, and THRESHOLD-dependent:** at **dense 75+, SL −70% wins** (cut losers, recycle into abundant signals → +12,848% vs SL-100 +6,264%, *double*, lower DD); at **sparse 85+, SL −100% wins** (nothing to recycle into → hold the winners). SL-85 is the dangerous middle (6% collapse in COVID). TP30 ≫ TP40/50.
- **Exposure peaks at ~50%, NOT maximal — over-deployment HURTS** (capital-velocity law: bigger sizing → deeper drawdowns → less survives to compound). 50% (+15,323%) > 55% > 65% (+12,848%) > 100%=off (+6,365%). **Apex's explosiveness is the 75+ density + SL/TP, NOT cranking exposure** — this CORRECTS the earlier "allocation is Apex's lever" claim (it was wrong). Exposure is instead a clean profile dial: Apex 50%, Core ~40% (+9,704% / 77% crash DD), Sentinel lower / 85+.
- **Capacity ceiling is a per-profile dial; the ceiling curve proved it's a pure growth-vs-realism knob** — DD/collapse are scale-invariant (flat ~86% / 0% across $250k→$500k→$1M→$2M→$5M→uncapped; only MedRet moves: +3,623% → +16,953%). **User decision: APEX RUNS UNCAPPED** (max compounding — the cap never binds while the book is small, and you migrate to Core/Sentinel before it would matter). **Core/Sentinel apply caps** ($2M / $1M) for realism at larger books.
- **3-PROFILE FRONTIER SHIPPED** (`portfolio_profiles.json`, base v70, all puts-off, collapse=0, default=apex). **Selectivity (85+) is the real DD lever, NOT exposure** — Sentinel's 85+ cuts 5y DD 84%→37%; Core's exposure cut only trims to 78.6%:
  - **Apex** 75+ / 50% exp / uncapped → **+16,953% 10y / 86% DD** (explosive, DEFAULT)
  - **Core** 75+ / 40% exp / $2M → **+7,422% 10y / 78.6% 5y-DD** (balanced)
  - **Sentinel** 85+ / 30% exp / $1M → **+3,371% 10y / 37% 5y-DD** (preservation)

**PUT REHABILITATION — CLOSED 2026-06-02 (rigorously tested to the smallest sleeve; NOT lazily discarded).**

The full path below WAS executed — slot-filler / idle-gated, CUT *and* HOLD, put-specific TP/SL, judged on hedge value — down to the **smallest possible sleeve (<15-only, cap 5%, 1 slot).** Verdict: **puts are net-harmful even as a 1-slot tail-fill on honest v70.** `put_tail_tiny.py` (N=200, Apex uncapped base): every sliver **introduces collapse** (0.5–8%, baseline 0%), **worsens 2022 DD +5 to +8pp**, and **guts 10y return 33–52%** (+16,953% → +8,140–11,338%). Even the HOLD variant with a **63.6% put TP** collapsed 0.5% and worsened 2022 DD +5.1pp. **Mechanism (resolves the paradox below):** puts win often, but their losses **cluster in the same stress where the calls bleed** and consume the **cash buffer** that is the survival mechanism — so they are net-harmful, NOT anti-correlated. The ~68% generic WR is real directional signal; it is **not convertible into portfolio hedge value at any sleeve size tested.** Do not re-open without a *fundamentally different* mechanism (e.g. a separate reserved-cash put ledger that cannot touch the call buffer — untested, needs its own plumbing). The original rationale + path is preserved below as the record of what was tried and falsified.

- **The paradox to respect:** <15 puts have ~68% *generic-barrier* WR (real directional signal — the stocks do drop), yet are option-net-negative as tuned. Resolution: the option's **tight ~0.73σ upside stop** gets shaken out by bull-market upward drift *before* the ~1.27σ down-move arrives. The signal is real; the tight stop in an up-market is what bleeds it (same generic-vs-option gap as calls, worse because puts fight the drift).
- **The path (mechanically sound, pursue it):**
  1. **Slot-filler only** — puts deploy on *idle* slots. This SELF-GATES on regime: idle slots open exactly when 75+ *call* signals are scarce = weak/bear tape = put-favorable. The call-signal density *is* the regime gate — no VIX threshold needed.
  2. **CUT, not HOLD** — puts are the mirror of calls; the stop is their *protection* in bull drift. HOLD-puts ride losses to the floor.
  3. **Put-specific TP/SL/regime knobs** sculpted around the put distribution (wider TP for the asymmetric crash payoff; upside-stop tuned to survive normal drift without bleeding).
  4. **Judged on HEDGE VALUE, not standalone return** — a put tail that is ~flat alone but **cuts portfolio DD in 2018/2020/2022 (when the calls bleed)** is *optimal* in the book. Negative correlation with the call sleeve during crashes is the entire point of a hedge.
- **Win condition:** explosive 75+ calls + a put tail that *earns its slots* by hedging the crashes. Do NOT discard puts on a standalone-return test.

---

## Part 1 — The look-ahead bug

The batch/recalc scoring path keyed the weekly composite on the CURRENT week and read
the stored COMPLETE Mon-Fri `WeeklyScore` for a mid-week historical signal. For a Tuesday
signal it saw Wed/Thu/Fri bars that hadn't happened yet = **look-ahead**. Live `trader
update` was always honest (it recomputed the current-week-partial weekly from bars-so-far).

**Detector:** day-of-week test — the weekly "edge" vanished on Fridays (when the week is
~complete, minimal hidden future) but was large Mon-Tue. That's the signature of look-ahead,
not real edge.

**Why it was worth ~12pp:** the complete-week weekly contains the first ~3 days of the
15-day forward window. Since the option strategy resolves most trades in 1-3 bars (fast TP),
seeing those 3 days ≈ seeing early trade resolution for a large fraction of trades.

## Part 2 — v69 (the honest fix, shipped `8b59206c3`)

Replaced the single weekly adjustment with a **point-in-time blend**: `wadj_effective =
(1-t)·comp_adj + t·pit_adj`, where `comp_adj` = last-completed week (W-1 vs W-2), `pit_adj`
= partial current week reconstructed from daily bars Mon→date (vs W-1), `t = bars-elapsed/5`
(Mon~0.2 → Fri=1.0). Helper `database/utils/weekly_pit.py`. First honest scoring version.

**First honest 5y backtest (option-TP15):** 90+ 60.5%, 85+ 54.5%, 80+ 52.0%, 75+ 51.2%,
<25put 41.7%. ~12pp of the apparent edge was look-ahead; every tradeable tier still clears
break-even (call 45 / put 36) but thinly. 75+ supply -27% (look-ahead false positives removed).

## Part 3 — The portfolio retune (shipped `b85c514bb`, Stage 2+3, hygiene)

Old params COLLAPSE on honest scores (5y/22-now 100% collapse, 92% DD). Sweep:
- **Stage A (sizing):** calls-lean downsized survives; puts net-negative (a 10% put sleeve made everything worse — the bear-hedge value was itself look-ahead).
- **Stage B (barriers):** TP_BASE 0.33→0.28 (capital velocity) took 5y -38%→-1%; wider SL HURT. Puts unrescuable.
- **Selectivity (decisive):** edge lives entirely in **85+** (TP 52.2% > 49.1% BE); 80-84 (48%) / 75-79 (~46%) drag below BE. Dropping 75-84 rescues every losing year.
- **Shipped C2_cs65:** 85+-only, ULTRA 0.13/TOP 0.0975, puts off, MaxPos 8/7/2, TP0.28. N=500: 5y +130%/35%DD, 0 collapse, 7/8 windows +.

## Part 4 — Why the OPTIONS strategy doesn't beat SPY (3 diagnostics)

SPY actual: **5y +101% (~14%/yr, ~25% DD); 22-now +56%.**

- **D2 (cost) is the killer:** per-trade gross edge ~+1.7% premium. Modeling round-trip option spread: 0%→+212%, 2.5%→+42%, **5%→-45%**, 10%→-67% (5y). Break-even ~3%. 25% of 85+ signals are small/micro-cap (~10-15% spreads); blended ~6% → strategy LOSES. Even liquid-only (2.5%) ≈ 7%/yr at 59% DD, worse than SPY.
- **D3 (2022):** the "+25% crisis alpha" was option convexity on ~3 names (AR +64%×4, SQM, LABD 3x-inverse); the underlying 2022 signal LOST money (-1.06%/15d). Convexity luck, not skill.
- **D1 (factor):** at 85+ the market-adj excess looked insignificant (t=1.25) — but that was **small N (237)**; see Part 6.

## Part 5 — Cross-version comparison (honest)

v60→v69 scoring diff is **purely the weekly blend** — same SCW/Market-Wave/echo/MCD/ICH
stack. So **v60's true honest WR15 IS v69's**: 63.3%→51.2% (-12pp), N 2482→1799 (-27%).
That -12pp/-27% is the look-ahead, cleanly isolated.

| version | 75+ WR15 (option-TP) | N75 | note |
|---|---|---|---|
| v57 | 63.2%* | 4124 | *look-ahead inflated |
| v58 | 61.6%* | 5119 | *(reverted) |
| v60 | 63.3%* | 2482 | *(was active) = v69 honestly |
| v63 | 64.3%* | 3493 | *best-looking, still look-ahead |
| **v69** | **51.2%** | **1799** | **honest** |

Every version backtested 61-64% (a ~3pp spread) on a common ~12pp look-ahead. The
version-to-version tuning was optimizing differences smaller than the artifact. Honestly
they're all the same ~51% thin edge.

## Part 6 — Can the weekly edge be "reverse-derived"? (the sharp question) — NO

Empirical test (`diag4_weekly_conditional.py`) on v69 honest scores:

```
75+ honest (N=2755): market-adj 15d excess +1.38%  t=5.69   <-- SIGNIFICANT alpha
  weak weekly:     +1.18% (t=3.24)
  neutral weekly:  +1.77% (t=3.69)
  strong weekly:   +1.16% (t=2.70)
  corr(honest w_adj, fwd excess) = -0.016   <-- ZERO incremental weekly edge
```

**Two findings:**
1. **The honest weekly has NO incremental conditional edge** (corr -0.016; strong-confirming
   weeks do NOT forecast better). The weekly is already fully baked into the score. There is
   nothing left in the weekly swing to "sample out."
2. **The look-ahead 12pp can't be recovered** because it was the first ~3 days of forward
   price action (early trade resolution). To recover it you'd have to predict those days —
   which IS the price-prediction problem (ceiling ~51%). It was never a "small unknown"; it
   was a big chunk of the answer.

## Part 7 — THE TWIST: the signal has real alpha; the OPTIONS wrapper killed it

The same probe showed **75+ market-adj 15d excess = +1.38%, t=5.69** — statistically
significant underlying stock-selection alpha. The score genuinely picks stocks that beat the
market over 15 days. **The options bid-ask spread (~6%) was eating a real +1.38% edge.**

Caveats before celebrating:
- Overlapping 15d windows inflate t; effective significance lower but likely still real.
- Names are high-momentum (+34.8% trailing 6m) → part of +1.38% is the momentum factor
  (buyable via MTUM for free). True alpha vs momentum is smaller, possibly ~+0.5-1%/15d.
- In-sample (score developed on this data); the holdout (post 2026-05-15) is the real test.
- Short-term-gain tax applies to 15-day stock holds too.

**Implication:** the OPTIONS strategy is dead (spreads), but a **STOCK-based** version of the
signal (long 75+ names, ~15-day hold; or a tilt) faces ~0.1% costs instead of ~6% and might
clear. That is the highest-value open question.

## Part 8 — Recommendations / open work

1. **Do NOT trade the options strategy.** Spreads kill the thin per-trade option edge.
2. **The live system is shipped as 85+-only hygiene** (`b85c514bb`) — coherent, not a funding call.
3. **HIGHEST-VALUE NEXT TEST (well-motivated):** a stock-based long/tilt backtest of the 75+
   signal — factor-adjusted (vs SPY + MTUM momentum), with realistic stock costs + short-term
   tax, validated on the post-2026-05-15 holdout, vs SPY/MTUM. If the alpha survives
   factor-adjustment + holdout + cost, THIS is the fundable vehicle (not options).
4. **Reusable infra:** `experiments/v69_portfolio_retune/` — driver (subprocess MC),
   diag1_3 (factor/2022/liquidity), diag2 (cost), diag4 (weekly-conditional). Vet any future
   idea through these before risking capital.
5. **Weekly dampeners (WCF/CWCF/CWWD) are suspect** — they were calibrated on look-ahead-
   inflated weekly cohort z-scores (z=+9 to +10). Their honest value is unverified.

## Part 9 — Stock-vehicle test RESULT (`stock_vehicle_test.py`)

Calendar-time long portfolio of 75+ names (15-day holds, ~18 names avg, 5.4y):

```
                ann.ret  Sharpe  maxDD   total
75+ NET cost    +40.5%    1.38   28.8%   +522%     <-- CRUSHES SPY
SPY             +14.3%    0.87   25.4%   +105%
momentum factor +46.4%    1.46   32.9%   +677%     <-- but momentum alone did MORE
factor reg:  alpha +11.4%/yr  t=1.35   mkt-beta 0.42 (t9.3)  mom-beta 0.49 (t19)  R2 0.54
cost drag 1.86%/yr (tiny — confirms stocks, not options, is the right vehicle)
```

**Verdict — nuanced, honest:**
- **The stock vehicle DOES beat SPY** (+40.5% vs +14.3%/yr, Sharpe 1.38 vs 0.87, similar DD).
  Costs are trivial (1.86%/yr) — the option spread was the whole problem, confirmed.
- **BUT it's ~mostly the MOMENTUM FACTOR.** Momentum beta 0.49 (t=19); an aggressive
  equal-weight momentum portfolio returned MORE (+46.4%). The score's alpha *beyond*
  market+momentum is **+11.4%/yr but t=1.35 — NOT statistically significant** (p~0.18).
- **So:** the edge that beats SPY is real but it's **momentum** (a known, cheap, buyable
  factor). The score *might* add ~11%/yr on top, but it's unproven (could be noise). To
  capture the bulk you'd run momentum (MTUM / equal-weight momentum) — less effort, lower
  turnover, ETF tax efficiency. The score's marginal contribution is the open question;
  only the post-2026-05-15 holdout (more time) can confirm whether it's real skill.

**Bottom line of the whole investigation:** the options strategy is dead (costs); the signal's
apparent edge is mostly the momentum factor (free via MTUM); the score's true alpha beyond
momentum is positive but unproven. Pragmatic play = a momentum tilt, not this options system.
The one live thread is whether the score's +11%/yr marginal alpha survives out-of-sample.

## Part 10 — Alpha-beyond-momentum exploration sweep (`stock_alpha_sweep.py`) → NO significant alpha

Swept score-tier × hold-period × component-conditioning × long/short, each factor-regressed on
SPY+momentum. **NOTHING clears t=2 — no statistically significant alpha beyond momentum anywhere:**
- Tier: alpha t maxes at 75+ (t=1.58); 80+/90+/95+ all t<0.9. NOT concentrated in top tiers.
- Hold: edge is FRONT-LOADED — 75+ h5 alpha +27.5% (t=1.50), h10 +19% (t=1.70), decaying to ~0 by
  h20-30. Short-term drift, strongest in first 5-10 days.
- Trend split: HIGH-trend signals have the highest raw alpha (+33%) AND highest mom-beta (0.76) —
  the "alpha" IS momentum, not a non-momentum pocket. LOW-trend (mom-beta 0.32) alpha only +9.5%.
- **Long/short market-neutral (long 75+ / short ≤25): alpha -2.5%, t=-0.46 — ZERO.** The cleanest
  proof: strip market+momentum beta and there is no selection skill left.

**Verdict: the score is a momentum strategy with no demonstrable independent alpha.** Every
"alpha" pocket tracks momentum exposure; the market-neutral version is flat-to-negative. The
+11%/yr long-only residual is momentum/short-drift + noise, not skill. Only the post-2026-05-15
holdout could change this, and the prior is now low.

## Part 11 — Theoretical frictionless options edge (the "0% cost" ceiling)

The MC already assumes $0 commission; "0% cost" = also 0 bid-ask spread = the `spread_0pct` case:
- **Moderate 85+ (shipped C2): 5y +130% / 35% DD. Aggressive 85+ (C4/C5): 5y +212% to +250% / 48-54% DD.**
- Per-trade frictionless EV at honest 52% 85+ TP / TP+28% / SL-27%: `0.52·0.28 − 0.48·0.27 = +1.6%`/trade premium.

**This is a pure ceiling — 100% unrealizable.** At the realistic ~6% spread it's -45%; the entire
+130%→-45% swing is the option friction you'd actually pay. And a subtle theoretical point: even
frictionless, the options DON'T beat the stocks (+130-250% options vs +522% stocks) — the option's
**theta drag + TP-cap (you exit at +28% even when the stock runs +64%) roughly cancel the
leverage.** So the option structure adds no theoretical advantage over stocks for this signal, and
costs make it strictly worse. The options wrapper was always the wrong vehicle.
