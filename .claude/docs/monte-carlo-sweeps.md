> **CANON NOTE (2026-08-10): this file is a HISTORICAL REFERENCE archive.** Every
> TP/SL/alloc/slippage number below describes the era it was measured in (many predate
> the seeded-fill rewrite, the asymmetric cost canon, and the honest substrate). Current
> canon: `strategy_config.py` + `algorithm_versions/portfolio_profiles.json` (2026-08-10
> tpsl_refine ship: Core TP+10/SL-100, Apex TP+10/SL-60 pinned) — see known-issues.md
> CURRENT SHIP STATE and `experiments/tpsl_refine_2026_08/FINDINGS.md`. Never copy a
> parameter out of this file as if it were live.

### MC Methodology Limitations & Historical Backtest

The MC portfolio sims (`monte_carlo_options_sim.py`, `monte_carlo_cascade_alloc.py`, etc.) use real scored signals + real price walks — win rates/return distributions measured from actual price data, valid for the questions they answer.

`monte_carlo_70plus_15d.py` differs — fully synthetic signals (Poisson-sampled from 5y average λ) and synthetic win/loss draws. Answers "given these statistical properties, what allocation maximizes long-run growth," not whether scoring actually works. Limitations: (1) synthetic arrival rates use a 5y average λ/day that misrepresents any given year (actual density varies 3-4× YoY: 4.5/day 2022 bear vs 16.7/day 2025 choppy); (2) `p_tp` inputs use the 5y assessment `win_rate_15d` (wide M=5.0σ stops), not the live SL=−25% option rule (0.910σ) — different barriers, different win rates. Refactored 2026-04-15 to retain **Phase 1 only** (TP sigma-mult sweep via MFE percentile anchors); Phase 2 (allocation optimizer) and `--year` empirical mode removed — the optimizer produced non-monotone year-specific results (look-ahead: optimizing against a year's own realized TP rate, only known after the year ends). Cascade allocation is validated by the deterministic backtest family instead.

**Preferred validation tool: `backtest_cascade.py`** — deterministic historical backtest: loads every actual score≥70 from DB (active version), walks real OHLCV forward per signal for TP/SL/hard-sell outcome, replays the portfolio day-by-day in chronological order. No MC randomness — same equity curve every run; TP/SL rates emerge from real price data.
```bash
python backtest_cascade.py                 # full history
python backtest_cascade.py --from 2022-01-01   # bear year only
python backtest_cascade.py --min-score 75      # 75+ only
python backtest_cascade.py --capital 100000
```
Data barrier: signals before 2016-01-01 excluded (pre-2016 score coverage is index-tickers-only sparse; floor gives the calendar assessment a clean 10y window). Tiebreak: score descending, symbol ascending — deterministic. Re-entry block: open position on a symbol blocks new signals for it until close (eliminates the same-stock re-entry artifact present in earlier MC sims). Use the deterministic backtest to validate whether scoring predicts outcomes at assumed win rates; use MC for sensitivity analysis (win rate ±5pp).

**v19 deterministic backtest (6656daa, `trader backtest --from 2021-01-01`, $25k start):** $25k→$2,019,684,754,535,288 (+8,078,739,018,041%), Max DD 61.3%. 4,391 closed (2,469 calls/1,922 puts): TP 2,573, SL 1,776, Hard 42. Overall TP 58.6% (BE 56.3%, +2.3pp); calls 66.2% TP (+9.9pp above BE), puts 48.9% TP (+5.4pp above BE). Confirms MC Realistic-mode TP rates (calls ~66%, puts ~47-49%) without MC randomness. Max DD 61.3% < MC Conservative DD 74.7% because the deterministic replay uses chronological tiebreak rather than worst-case collision assumptions.

---

### MaxPos Sweep Findings (2026-04-16, `monte_carlo_maxpos_sweep.py`)

**Q:** does raising MaxPos from 10 improve returns without breaching DD safety? **Setup:** $50k, all annual windows 2021-2025 + 5y + 22-now (bear-start stress test), MaxPos∈{8,10,12,13,14,15,20}, cascade 15/12/12/5, TP=+30%/SL=−35%, N=500, 3 collision modes.

**14 wins or ties Realistic in every year/window** (e.g. 22-now: MaxPos10 +25.9M% → MaxPos14 +81.3M%; 5y +9.4B%→+27.4B%). MaxPos15 marginally beats 14 on 22-now (+83.0M%) but breaches the 2025 Realistic WorstDD floor (80.1% > 80%, vs 14's 79.1%). MaxPos13's Conservative 2022-isolated DD hits 81.2% (fails) though its 22-now Conservative (79.6%) passes — boundary noise. **Settled: MaxPos=14**, 0% collapse confirmed all windows/modes.

---

### VIX vs Breadth Decomposition Findings (2026-04-16, `monte_carlo_vix_breadth_decomp.py`)

**Q:** does the h35→40 regime SL switch work because of VIX or breadth? **Setup:** $50k, N=500, 3 modes, h35→40 structure fixed, only signal source/threshold varies — 28 rules across composite(45/50/55/60), VIX score(40/50/60/70), raw VIX(18/20/22/25/28), inverted breadth(40/50/55/60/70), reweighted blends, fixed-35 reference; 5 windows (2022→now, 2022-2025).

**Breadth alone (`brd_inv@50`) beats production composite by +20%** on 2022→now Realistic (+38.8M% vs +32.4M%; lower DD 76.8% vs 77.6%). VIX dilutes the breadth signal monotonically (100% breadth +38.8M% → 100% VIX +19.7M%, more VIX weight = worse in every blend). Breadth rules dominate the worst-DD leaderboard (77.1-77.6% vs VIX's 79.4-83.8%). VIX-only is inert in calm years (2024 bull VIX mean=15.5 → triggers 0-2% of the time; breadth still triggers 8-33% via sector rotation). In the 2022 bear, VIX wins on return but breadth wins on DD (vix_sc@70: +521%/75.7%DD vs brd_inv@55: +435%/73.9%DD — lowest 2022 DD of any rule). Mechanism: breadth measures participation directly and degrades gradually; VIX measures expected-vol spikes and is binary/noisier. **Settled: `brd_inv@50`** (breadth_score≤50 → SL=40%, else 35%) replaces `comp@50`.

---

### Regime-Conditioned TP Findings (2026-04-16, `monte_carlo_regime_tp_sweep.py`)

**Q:** does conditioning TP on breadth/VIX/composite beat fixed TP=+30%? **Setup:** $50k, N=500, 3 modes, 2022→now primary; MaxPos=14, cascade 15/12/12/5, breadth SL h35→40, hard=−50%@day15 fixed; 13 rules (base 30% always, stress TP∈{20,25,35,40}% at threshold).

**`brd_TP30/35` (breadth_inv≥50→TP=35%) wins: +188.7M% vs fixed's +126.5M% (+49%), WorstDD 81.6%** (fixed 84.0%). Tightening stress TP is catastrophic (−60 to −96%: net reward shrinks faster than hit-rate rises). Breadth > composite > VIX as signal source (same ranking as SL decomposition; VIX-only fires only 21% of days vs breadth's 49%). 2022 isolated is the only window fixed wins (+638% vs +465%) — 66% of 2022 days are stressed so switching flips almost every trade, but 2023-2025 compounding more than recovers the shortfall. One signal (`breadth_score≤50`) now drives both SL and TP switching — no new data source. **Settled: breadth-adaptive TP (30% base, 35% at breadth≤50)** replaces fixed 30%.

### Regime-Conditioned TP — INVERSE (2026-04-16, `monte_carlo_regime_tp_inverse.py`)

**Q:** forward sweep locked base=30%, varied stress TP. This locks stress=30%, varies calm TP to isolate which side drove the +49%. **Verdict: hypothesis "loosen when stress arrives" confirmed — calm TP=30% is already optimal.** No inverse rule beats fixed_TP30 on cross-window AvgRk; tightening calm TP is catastrophic (vix_TP20/30 loses 98% of baseline, +121.9M%→+2.3M% — strips the compounding engine during the majority of non-stressed days); loosening calm TP is marginally worse or tied (vix_TP35/30 ≈ −0.7%). Confirms the entire +49% uplift of brd_TP30/35 came from loosening TP during stress specifically — don't add calm-side variation.

---

### Bayesian Put Optimization — NULL RESULT (2026-04-17, `experiments/bayes_phase*.py`)

**Q:** with v18 puts shipping alongside calls, do Bayesian-optimal put params (TP/SL, breadth-adaptive, cascade, MaxPos split) beat production? Five-phase adaptive Bayesian optimizer, utility = geometric-mean log return across 6 windows (2× weight 22-now) with DD/collapse penalties.

Phase 1-4 "winner": `PUT_TP=+40%/PUT_SL=−10%`, breadth "same" mode threshold 50 with stress +45%/−10%, put cascade `≤15:20%/16-20:8%/21-25:5%`, `MAX_POSITIONS=20` split 10/10. Phase 5 showed 16× uplift (+14.86B% vs +940M% production) on 22-now.

**Phase 5b isolation (reverted PUT_TP/SL + breadth back to production, kept cascade+MaxPos winners): the entire 16× uplift was the `PUT_SL=−10%` simulation artifact** — raw put TP rate at SL=−10% collapsed to 28-31% (vs 46-50% at −20%), far below the ~43.5% BE — same-stock re-entry cycling (see the SL=5%/10% call artifact). With the artifact removed, the structural winners (cascade+MaxPos split) LOSE to production on 4/6 windows incl. 22-now (−19%) and 2022; 2025 Conservative WorstDD hits 79.8% (at the floor); the 10/10 split hurts both return and safety vs the shared 14-slot pool. **Settled: v18 production (`PUT_TP=+30%`, `PUT_SL=−20%`, breadth OFF for puts, shared 14-slot pool, symmetric 15/12/12 put cascade) is already near Pareto-optimal — no change.**

---

### Ultra Split + Cascade Flatten Findings (2026-04-17, Bayesian phases 6-8) — SHIPPED

**Q:** post-v18, does the 95+ bucket (WR15=90%) deserve its own tier? Is 70-74 overflow still useful? Is 15/12/12 the right call cascade shape? Phase 6/6b/6c found: overflow 5%→0% wins; flattening 80-84/75-79 from 12%→15% wins; MaxPos irrelevant when overflow=0 (cash binds first). Phase 7 (95+ split sweep, 6 variants): `ultra=25/top=15/mid=15/low=15/overflow=0 @ MaxPos=14` lifted 22-now Realistic +3.87×. Phase 8 fine-tune confirmed ultra∈[22,25] sweet spot; any overflow≥2% re-breaches the 80% Conservative DD floor.

**Shipped `ULTRA=25/85-94=15/80-84=15/75-79=15/OVR=0 @ MaxPos=14`:** 22-now Real +944M%→+3,636M% (+3.9×), 22-now DD-C 83.3%✗→75.2%✓ (−8.1pp), 2025 DD-C 83.0%✗→74.1%✓ (−8.9pp). 2021 +2.7×, 2022 +3.9×, 2024 +3.2×. Accepted trade-off: single-year regressions 2023 (−38%) and 2025 (−62%) — lower-call-density years that would normally benefit from overflow/fatter mid-low, but correlated-DD reduction + bull-year uplift more than compensate on 22-now. **Hard bounds:** overflow≥2% breaches the floor; ultra≥28% breaches the floor (ceiling 25-27%); MaxPos 12 vs 14 is irrelevant at overflow=0 (kept 14 for headroom). Shipped to `monte_carlo.py`, `backtest_cascade.py`, `trader.py`, `api.py`, `CLAUDE.md` — no scoring change, no version bump.

---

### Regime-Aware Allocation Findings (2026-04-17, `experiments/bayes_phase9_regime_alloc.py`) — NOT SHIPPED

**Q:** should allocation scale with `MarketRegime.regime_multiplier` (more capital in BULL, less in STRESS)? `alloc_scale = 1.0 + slope×(regime_mult−1.0)`, clamp [0.25,1.75], on top of the shipped ultra-cascade. 9 variants × call/put slope, N=250.

`CALL_PRO_ONLY` (slope_c=+1.0, slope_p=0) is Pareto-optimal: +23% 22-now Real vs baseline, DD-C +0.9pp only (76.5%→77.4%), no breach (2022+14%, 2023+18%, 2025+80%, but 2021−21%/2024−25%). Put-side pro-cyclical scaling always hurts (puts are counter-trend by nature — amplifying in BULL puts capital on worse bets). Counter-cyclical scaling breaches the DD floor (COUNTER_FULL 87.4%). Pro-cyclical at slope 1.5 also breaches (ceiling ~1.25). Mechanism: in low-density years (2022/2023/2025, not slot-bound) scaling deploys more capital per quality signal for free; in high-density bull years (2021/2024, MaxPos-bound) it consumes cash on early signals at the expense of later ones. **Decision: don't ship yet** — utility margin (59.46 vs 58.71) is small relative to N=250 noise; Phase 10 fine-tunes slope_c.

### Regime Slope Fine-Tune (2026-04-17, Phase 10) → **Regime Slope Validation (Phase 11) — SHIPPED**

Phase 10 (N=400): SC75 (slope_c=0.75) and SC100 tie at util 59.43, both +1.05 logret over baseline; put-side slope modification only hurts (keep slope_p=0); SC125 overreaches (ceiling ~1.0). Phase 11 (N=1000 to resolve the tie): **SC100 wins clearly** (util 59.553 vs SC75's 59.426 vs baseline 58.445) — 22-now +44% over baseline (+5.09B% vs +3.53B%), DD-C improves too (77.4% vs 79.9% baseline). No breach at slope∈[0,1.0].

**Shipped (superseded by Phase 13 below):** `REGIME_SLOPE=1.0` (call), `REGIME_SLOPE_PUT=0.0`, `ALLOC_SCALE_FLOOR=0.25`, `ALLOC_SCALE_CEIL=1.75`. Mechanism: for each call signal, `alloc_scale = 1.0 + 1.0×(regime_mult−1.0)`, clamped [0.25,1.75], multiplies the cascade tier allocation.

### Asymmetric Regime Slope (2026-04-17, Phase 12+13, `bayes_phase13_cutonly_validation.py`) — SHIPPED, supersedes SC100

**Q:** does the slope need to be symmetric, or does treating BULL/STRESS with different slopes gain alpha? Phase 12 (N=400) swept `REGIME_SLOPE_UP`(mult≥1.0) vs `REGIME_SLOPE_DOWN`(mult<1.0) pairs. Phase 13 (N=1000) validated CUT_ONLY (up=0,down=1) and stress-depth variants.

**`CUT_ONLY` wins: 22-now Real +8.28B% vs SYM100's +5.25B% (+58%), DD-C 74.4% vs 77.4% (also lower).** slope_down=0.75 trails (util −0.21); 1.25/1.50 overreach (−0.57/−2.02). Bull-side boost was actively hurting (BOOST_ONLY was worst in Phase 12) — removing it delivers both more return and lower DD. Mechanism: stress-regime contraction preserves capital for redeployment after the regime improves; bull-side boost added risk without capturing extra edge (baseline already captures it).

**Shipped (applied across `monte_carlo.py`, `backtest_cascade.py`, `api.py`, `trader.py`):**
```python
REGIME_SLOPE_UP = 0.0    # BULL (regime_mult >= 1.0): no boost
REGIME_SLOPE_DOWN = 1.0  # STRESS (regime_mult < 1.0): full cut
REGIME_SLOPE_PUT_UP = None
REGIME_SLOPE_PUT_DOWN = None
# plus REGIME_SLOPE=1.0 (symmetric fallback, unused), REGIME_SLOPE_PUT=0.0,
# ALLOC_SCALE_FLOOR=0.25, ALLOC_SCALE_CEIL=1.75
```
Puts always unscaled at this stage.

### Asymmetric Put Regime Slope (2026-04-17, Phase 14+15, `bayes_phase15_pcut_validation.py`) — SHIPPED

**Q:** Phase 11 ruled out symmetric put slope — does asymmetric put decomposition (cut in BULL only, or boost in STRESS only) add alpha? Phase 14 (N=500, 8 variants): boosting puts in STRESS is strictly bad (−76% of 22-now compound — puts/calls share MaxPos=14, boosting puts crowds out calls whose TP rate dominates); cutting puts in BULL helps (`PCUT_BULL_05`, slope_put_up=−0.5, #1). Phase 15 (N=1000 validation): clean inverted-U peaking at slope_put_up=−0.5, **+18% compound over call-only CUT_ONLY** (22-now Real +9.11B% vs +7.70B%) — **+74% total over the pre-Phase-11 baseline**, DD-C also slightly lower (74.5% vs 74.8%). Mechanism: in BULL (mult~1.10), puts deploy `1.0+(-0.5)(0.10)=0.95` of tier alloc — small per-trade reduction compounds via fewer losing put trades + more call slot capacity.

**Shipped (stacked on Phase 13 CUT_ONLY, applied across `monte_carlo.py`/`backtest_cascade.py`/`trader.py`/`api.py`):**
```python
REGIME_SLOPE_UP = 0.0        # calls: no BULL boost
REGIME_SLOPE_DOWN = 1.0      # calls: full STRESS cut
REGIME_SLOPE_PUT_UP = -0.5   # puts: mild BULL cut
REGIME_SLOPE_PUT_DOWN = None # puts: STRESS unchanged (boosting crowds calls)
# plus REGIME_SLOPE=1.0, REGIME_SLOPE_PUT=0.0, ALLOC_SCALE_FLOOR=0.25, ALLOC_SCALE_CEIL=1.75
```

---

### Hard Sell Timing Findings (2026-04-15, `monte_carlo_hard_sell_timing.py`)

**Q:** for a 30 DTE call, day 7 or day 15 hard-sell when neither TP nor SL fired? Setup: 2021-2026, $50k, TP=+30%/SL=−25%, cascade 15/12/10/10, MaxPos 10, 75+, N=500. Hard-sell P&L via theta scaling (√(remaining/total DTE)): day 15 ≈ −50% (empirical, includes IV crush); day 7 derived ≈ −21%.

Day 7: TP 56.2%/SL 42.3%/Hard 1.5%, WorstDD 50.5%. Day 15: TP 56.8%/SL 42.9%/Hard 0.3%, WorstDD 54.8% but +62% higher mean return. **Hard sell almost never fires at SL=−25%** (98.5-99.7% resolve via TP/SL before it) — the day-7-vs-15 choice barely matters to capital velocity. Day 15 wins because the extra 8 days lets borderline positions recover to TP (+0.6pp TP rate), outweighing the extra theta cost on the ~1.5% still open. Day 7 would win only if SL were widened a lot (e.g. −60%), where many more positions survive to hard-sell. Crossing point for the day-7 P&L assumption is near −15%. **Recommendation: keep day 15** — timing isn't a meaningful lever at tight SL.

---

### 2022 Bear Market Stress Test (2026-04-15, `monte_carlo_2022_bear.py`)

Setup: 2022 full year, $50k, cascade 15/12/10/10, TP=+30%/SL=−25%/hard=−50%@day15, MaxPos 10, 75+, N=500. Signal pool 1,143 (4.55/day — lower density than bull periods). TP 51.6% (−5.2pp vs bull baseline 56.8%), SL 48.0% (+5.1pp), hard 0.3%. Mean return +439% (SPY was −19.4%), worst return +308% (floor, no collapse), worst DD 50.5% (zero variance — same-stock re-entry artifact at low signal density), P(collapse)=0%. With realistic per-exit slippage, BE rises to 48.5% (net TP+29.0%/net SL−27.3%); 2022's 51.6% TP clears it by 3.1pp — EV stays positive; the earlier "flat −5% slippage" collapse finding was a model artifact, not a real strategy failure.

#### 2022/2024/2025 With Realistic Slippage (2026-04-15, `monte_carlo_optimal_sl_5y.py`)

Per-exit slippage (entry −1%, TP 0% limit, SL −1.3%, hard −0.5%), SL swept 20-60%, each calendar year isolated, $50k, same cascade.

- **2022 (bear):** zero collapses at SL≤35%. SL=35% is optimal-safe (TP 60.5%, mean return +133%, worst DD 78.2%). SL=40%+ borderline/breaches (80.8%+ DD); SL=45%+ collapses (2.6%+). BE at SL=25% ≈48.5%; 2022's 51.6% TP clears by 3.1pp.
- **2024 (SPY+23.3%):** zero collapses, all SL safe. SL=25% best: TP 59.7%, mean +4,984%, mean DD 36.0% (lowest), worst DD 42.2%. Vs the old flat-slippage model's +555%, the realistic model unlocks the full compounding effect in a bull year.
- **2025 (choppy, violent Q1 selloff):** zero collapses all SL. SL=20% wins on return (+1,732%, TP 51.0%, worst DD 52.1%) — tighter stop recycles capital faster through many small adverse moves. SL=25% still solid (+1,477%, worst DD 62.4%, 6.4pp above BE). Vs old flat model's +99%, confirms the prior model severely under-stated a year where the strategy performs well.

---

### 2022-2024 Recovery Sequence (2026-04-15, `monte_carlo_2022_2024_recovery.py`)

**Q:** does the strategy compound back after the 2022 bear, or does the drawdown permanently impair it? Setup: Jan 2022→Jan 2025, $50k, cascade 15/12/10/10, TP=+30%/SL=−25%/hard=−50%@day15, MaxPos 10, 75+, N=500. Signal pool 2,969 (471 symbols).

TP rate recovers year over year: 2022 bear 51.6%(4.55/day) → 2023 recovery 55.4%(2.32/day) → 2024 bull 59.7%(4.95/day). Equity: $50k(Jan22)→$321,922(Jan23,+544%)→$2,849,787(Jan24,+5,600%)→$235,224,549(Jan25,+470,349%). 3y: mean +470,349%, worst +304,267% ($152M floor), worst DD 56.2% (zero variance — re-entry artifact), P(collapse)=0%. The bear year barely slows compounding (still 6.4×'d by Jan23); 2024's explosion (density 4.95/day + 59.7% TP + large capital base) is the dominant driver. 2023's anomalously low 2.32/day density (vs 4.55/4.95 the other years) despite a bullish tape was flagged as worth investigating (regime filters/score calibration possibly suppressing signals in moderate-trend environments).

---

### Daily Reposition vs Hold-to-Exit (2026-04-15, `monte_carlo_daily_reposition.py`)

**Q:** hold to TP/SL/hard-sell, or liquidate all positions every morning and re-enter fresh top signals? Setup: 2022 bear year, $50k, cascade 15/12/10/10, TP=+30%/SL=−25%/hard=−50%@day15, MaxPos 10, 75+, N=500. Mark-to-model for forced daily closes: `theta_factor=√(remaining_bars/hold_bars)` + `delta×price_return/premium_pct`; already-resolved positions keep actual P&L.

**HOLD wins ~2× on every return metric.** Mean return: HOLD +436% vs Daily-reposition +218%; worst DD 50.5% vs 52.4%; mean final $268k vs $159k; both 0% collapse. At SL=−25% (0.910σ), 98%+ of positions resolve via TP/SL within bars 2-7 — daily force-closing crystallizes theta losses just before the trade would have resolved (median MTM P&L snaps to +0.300 by bar 3, since ~half the population has already hit TP). What fresh scores ARE useful for: a continuation filter (hold only if today's score still ≥75), not a wholesale exit trigger — untested variant.

---

## Monte Carlo — Call/Put Ratio Sweep (Backtest Run 2)

`monte_carlo_call_put_ratio.py` (**deleted**, along with other sweep scripts). **Q:** does allocating slots to puts (score≤25) hedge drawdown vs calls-only? Setup: $75k, Jan2025→Apr2026, 30dte, N=500, 60 combos; put ratios 0/10/20/33/50% of slots.

Put outcome distribution: calls TP 77.6%/SL 20.2%/Hard 2.1%; puts TP 65.6%/SL 32.1%/Hard 2.3% (puts 12pp below calls). At 8% alloc/20 pos/thresh 75, each 10% shift toward puts costs roughly half the return AND increases DD (0% puts: +7,146%/57.2%DD → 50% puts: +172%/91.2%DD) — opposite of hedging. Only exception: at conservative 5% alloc, 20% puts cuts mean DD 8.5pp (58.9%→50.4%) but costs 387pp of return.

**Verdict (v14, 2025-2026 window — SUPERSEDED):** puts are not a DD hedge in that window — 2025 was broadly call-favorable so puts had no offsetting cushion period; adding puts just consumes slots that compound faster as calls.

**2026-04-17 refinement (not reversal), v17 + realistic slippage + breadth-adaptive TP/SL + 3-mode MC (`monte_carlo_puts_2022.py`):** 2022 (bear) full put cascade adds +246pp to Realistic mean; puts≤15@5% adds +133pp. 2025 (choppy) same cascade LOSES −2,868pp; puts≤15@5% loses −865pp and breaches the 80% WorstDD floor. **Conclusion: puts are regime-conditional alpha, not a standing policy** — don't ship as-is; see regime-gated alternatives under Open Optimization Priorities.

**Re-run note:** to re-test under the 3-mode canonical model, extend `monte_carlo.py` with a `PUT_RATIO` param + LOW-bucket signal path (score≤25, win=underlying falls). The "calls-only dominates 2025-2026" finding stands as reference; re-validating on a 2022 bear window under 3-mode is the priority if puts are reconsidered.
