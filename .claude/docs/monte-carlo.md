> **CANON NOTE (2026-08-10): this file is a HISTORICAL REFERENCE archive.** Every
> TP/SL/alloc/slippage number below describes the era it was measured in (many predate
> the seeded-fill rewrite, the asymmetric cost canon, and the honest substrate). Current
> canon: `strategy_config.py` + `algorithm_versions/portfolio_profiles.json` (2026-08-10
> tpsl_refine ship: Core TP+10/SL-100, Apex TP+10/SL-60 pinned) — see known-issues.md
> CURRENT SHIP STATE and `experiments/tpsl_refine_2026_08/FINDINGS.md`. Never copy a
> parameter out of this file as if it were live.

## Monte Carlo Options Portfolio Simulation

> **MEASUREMENT DOCTRINE — MC-REALISM DEFAULT FLIP (2026-08-12,
> `experiments/realism_default_flip_2026_08/`).** The engine's DEFAULT fill model is now
> CALIBRATED: `TP_FILL_MISS_P=0.15` + `TP_FILL_GAP_AWARE=1` (measured against real OPRA
> prints — default intrabar fills credited ~1.5× real limit mechanics; ~15% of declared
> TPs never fill economically). Every MC absolute quoted anywhere defaults to calibrated;
> the optimistic legacy lens (`TP_FILL_MISS_P=0`/`TP_FILL_GAP_AWARE=0`) is a labeled
> robustness arm only. Cert numbers before 2026-08-12 are canon-era. R2 ranking-stability
> audit confirmed no TP10-era ship decision inverts under the calibrated lens (all were
> gated on fill-model-robust differentials). Re-baseline tables (3 profiles×12
> windows×both lenses, N=500): `experiments/realism_default_flip_2026_08/out/flipR_r1.csv`.

> **REFERENCE SECTION — historical findings preserved for parameter justification.** All sweeps below ran under the single-outcome collision model (precursor to seeded-RNG canonical MC). Optimal params established here (SL=25%, TP=+30%, cascade 15/12/10/10/5, MaxPos=10, day-15 hard sell) were the locked-in defaults at the time; subsequent re-sweeps evolved them — see `strategy_config.py` for current live values. Script references name historical `monte_carlo_*.py` tools; many have since been removed/consolidated into `monte_carlo.py`.

### Methodology (historical single-outcome model)

- Entry: ATM call on any stock with score≥`score_threshold` on signal date.
- Option pricing: 15 DTE premium≈1.29×σ, 30 DTE≈1.82×σ (% of stock price).
- Exit (applied to underlying via intraday high/low): TP at +0.645σ(15D)/+0.910σ(30D)→+25% option gain; SL at −1.548σ(15D)/−2.184σ(30D)→−60% loss; hard sell day7(15D)/day15(30D) untriggered→−50%.
- Slippage (historical, `monte_carlo_optimal_sl_5y.py`, since removed): entry −1.0%, TP 0% (limit), SL −1.3%, hard −0.5%; round-trip ~1.0-2.3%. Net: TP+29.0%, SL−27.3%, Hard−51.5%. (The baseline sim used a legacy flat −5%.)
- Position sizing: `position_alloc_pct`×portfolio value; capacity = top-score-first when signals > max_positions; collapse = portfolio <20% start; starting capital $100,000.

### Backtest Run 1 — Jan 1 2025 → Apr 15 2026 (327 trading days, v `410a055`)

Signal pool (≥70): 5,930 across 531 symbols. Outcome: 30DTE 77.6%TP/20.2%SL/2.1%Hard; 15DTE 75.1%TP/23.0%SL/1.9%Hard. ~7.7 signals/day at 75+ (rarely saturates 20-slot capacity). Zero collapses across 90 combos×500 iters.

**Safe configs (P(collapse)<5%, worst DD<80%):** 30dte/8%alloc/20pos/75+ → +7,146%/$7.37M/57%DD (max return); 30dte/8%/20pos/80+ → +1,385%/39%DD (tighter DD); **30dte/8%/5pos/80+ → +584%/22%DD (lowest-DD safe config)**.

**Key findings:** 30 DTE dominates 15 DTE ~2× return at matched DD (extra 8 hold days = alpha). 75+ is the sweet spot (70+ dilutes at 26.5/day high-variance saturation; 80+ under-deploys at ~2/day; 75+ averages 7.7/day). Avg winner MAE (−0.51σ@7d, −0.74σ@15d) aligns with −50% option loss — don't use −50% as a hard SL, it cuts winners at their average trough; use only as midway hard-sell. Compounding dominates over 15 months (8%×20 slots×77.6% TP ≈ near-continuous positive-EV deployment). March 2026 −38% drawdown from peak — all configs recovered, fewer-position configs recovered faster. Zero collapses reflects a favorable call environment (Jan25-Apr26) — do not assume it generalizes to bear markets.

### TP Optimization Findings (2026-04-15, `monte_carlo_tp_optimize.py`)

Setup: Jan2025-Apr2026, $75k, 30dte/8%alloc/20maxpos/75+, N=500. Trade-level EV rises monotonically with TP (larger reward outpaces WR drop): TP25%→WR77.6%/EV+0.062/ret+7,146%; **TP30%→WR74.3%/EV+0.071/ret+8,515% (best portfolio result)**; TP35%→WR70.8%/EV+0.076/ret+4,343%; TP50%→WR61.8%/EV+0.087/ret+1,473%. Portfolio result is non-monotonic despite monotonic trade EV — **capital velocity**: higher TP locks capital longer (avg exit bar 2.9→5.0 at 30dte), slowing the compounding cycle; TP30% gets +14% EV for only +15% longer hold — the sweet spot. **Recommendation: TP_OPTION_GAIN=0.30** (+8,515% vs +7,146%, DD 52.8% vs 57.2%). MFE gap: theoretical median option gain during hold ≈88%, fixed TP captures only 25-30% — real but not exploitable via trailing stops (see next section); the gap is the deliberate cost of capital velocity, worth far more in compounding than the retained MFE. MFE-by-day is roughly uniform bars 1-15 with a last-bar spike (~16% on bar 15) — no exploitable time-based exit pattern.

### Trailing Stop Findings (2026-04-15, `monte_carlo_trail.py`) — KILLED

Setup: 2022/2024/2025, $50k, cascade 15/12/10/10/5, SL=−25%, N=100, trail σ∈[0.7,0.9,1.1,1.3] ± opportunity-cost swap. **Trail mode is catastrophically worse than baseline in every window/σ**: 2022 baseline+91.5%→best trail−85.3% (−176.8pp); 2024 +8,188%→+8.4% (−8,180pp); 2025 +1,453%→−84.2% (−1,538pp). Two causes: (1) AvgXtra is always negative — trail exits average less than fixed NET_TP(+29.0%) in every window (stock reverses immediately after intraday TP touch); (2) capital velocity destruction — fixed TP closes bars 2-7 freeing capital immediately, trail locks 8-13 extra bars; in 2024 this ate most of the compounding cycles driving +8,188% (not a second-order effect — the strategy's entire return engine). Opportunity-cost swap made it worse (forces conservative-price trail exits). **Root conclusion: the 88% MFE gap is the intentional cost of capital velocity — do not address it via exit-rule changes.** Partial scale-out (sell half at TP, trail the rest) remains theoretically testable but expected to show net drag; low priority.

### Allocation Sweep Findings (2026-04-15, `monte_carlo_alloc_sweep.py`)

Setup: 30dte/75+/maxpos20/TP+30%SL−60%/$75k, N=500, two windows. **New sweet spot: 12%** (up from 8% — the TP=30% change slowed avg exit, shifting Kelly-optimal upward). 2026-only (71 days): 5%→+541%/51.4%DD, 8%→+690%/57.1%, 10%→+706%/58.1%, **12%→+760%/61.3% (sweet spot)**, 15%→+645%/66.5%, 20%→+242%/69.2%, collapse boundary 40% (2026 window). 2025+2026 (327 days): 5%→+24,098%, 8%→+132,960%, 10%→+228,424%, **12%→+243,760% (sweet spot)**, 15%→+90,368%, collapse boundary 30% (stricter — longer windows give stress drawdowns time to compound). Return curve peaks sharply at 12% then collapses (capital-velocity effect — 15%+ locks too much capital for new signals to compound). P(collapse)=0% across all allocs both windows — Jan25-Apr26 was uniformly call-favorable, don't assume it generalizes. **Recommendation: 12% alloc** with 30dte/75+/20pos/TP+30%.

### Score-Tiered Allocation Findings (2026-04-15, `monte_carlo_score_tiered_alloc.py`)

Q: do 90+ scores justify higher per-trade alloc? Setup: $50k, Jan25-Apr26, 30dte, TP+30%, N=500; ≥75 pool 1,593 outcomes across 4 buckets (90+:27 N/0.08/day, 85-89:85/0.27, 80-84:337/1.05, 75-79:1154/3.61 — bulk of flow). Every graduated tier scheme beats flat at the same base alloc, gap grows with steepness: flat(1×)→+3,284%/32.4%DD; mild(2/1.5/1.2/1)→+5,433%; moderate(3/2/1.5/1)→+8,774%; aggressive(4/2.5/1.5/1)→+10,436%; **top_heavy(5/3/1.5/1)→+13,755%/49.6%DD (consistently best across all base allocs/max_pos, 0% collapse)**. E.g. at base=3%: flat +3,284% vs top_heavy +13,755% (+10,471pp).

### Cascade Allocation Findings (2026-04-15, `monte_carlo_cascade_alloc.py`)

Q: with 75-79 anchored at 10%, how much should 90+/85-89 be raised? Algorithm: sort signals score-descending daily, walk top→bottom allocating each its tier's fixed % of current portfolio value, skip if cash<cost. Setup: $50k, Jan25-Apr26, 30dte, TP+30%, N=500. Results (90+/85-89/80-84/75-79, 80-84/75-79 fixed 8%/10%, max_pos=20): **25%/25%→+168,894%/60.1%DD (+45,286pp vs flat)**; 20%/20%→+150,040%/61.4%DD (+26,433pp); 15%/15%→+135,859%; flat 10%/10%/10%/10%→+123,607% (reference); 18%/13% (85-89 dropped) →+92,093% (**−31,514pp, worse than flat**). **Critical: 85-89 must stay paired with 90+** — schemes that raise 90+ but drop 85-89 underperform flat, because 85-89 (0.27/day) provides far more compounding volume than rare 90+ (0.08/day); starving it under-deploys those higher-quality signals. **Practical recommendation: 20%/20%/8%/10%** (+26k%pp over flat, DD 61.4%, 0% collapse). Note: this cascade sim's flat-10% beat flat-12% (contradicting the Allocation Sweep's 12% finding) because it loaded only 75+ signals (4.9/day) vs the sweep's 70+-filtered-to-75+ (7.7/day) — at lower signal density, capital velocity favors 10%; the 12% finding holds at higher density.

### 5-Year Optimal Allocation Findings (2026-04-15, `monte_carlo_5y_optimal.py`)

Setup: $50k, 5y (Jan21-Apr26, 1,325 days), 30dte, TP+30%, 150 combos×N=500, 80-84=75-79 enforced equal; swept 90+∈[8-18%], 85-89 ratio∈[65/80/100%×90+], lower∈[4-10%], max_pos∈[10,20]. Signal density (v17): 90+ 75/0.06day (95+:21/0.02, 90-94:54/0.04), 85-89 228/0.18, 80-84 809/0.62, 75-79 2361/1.82. **Does 90+ warrant more than 85-89?** Partially — the 80%-ratio finding (85-89=0.8×90+) held on v14 but on v17, 85-89 has higher per-trade EV (0.158) than combined 90+ (0.140), driven by 90-94's weak WR15 (67.8%, lowest call tier); 95+ alone is exceptional (WR15=90.0%) but fires only 4×/yr. Kept the 80% ratio (90+=15%, 85-89=12%) pending a full cascade re-sweep. **Is the 80-84/75-79 inversion real? No — dead over 5y**, both tiers perform best equal (the 15-month 8%/10% inversion was noise). Dominant return driver is the LOWER tier alloc — 8%→10% roughly doubles returns at every top-tier level (the 4,768 signals at 75-79, 3.6/day, are the compounding engine; top tiers are the conviction multiplier). Worst DD stays <80% for nearly all combos over 5y.

**Historical reference only (superseded by v17's 85+ merged sweep):** v14-era alloc was 90+=15%/85-89=12%/80-84=10%/75-79=10%, max_pos=10 — later found the merged-85+ + 12%-lower-tier scheme delivers +110% 5y return at the same worst-DD (`monte_carlo_alloc_sweep_85plus.py`). Selection rule: score-descending daily, skip if cash<cost, stop at 14 slots; 70-74 fills only after all 75+ are processed (idle-capital overflow, not a primary entry).

### 70-74 Overflow Tier Findings (2026-04-15, `monte_carlo_7074_tier.py`)

Setup: 2022 bear, $50k, 30dte, TP+30%/SL−25%, cascade 15/12/10/10 fixed for 75+, max10, 70-74 swept 0/3/5/7/10%, N=500. Density/TP in 2022 bear: 90+ 33N/0.13day/45.5%TP (net-negative EV), 85-89 85N/0.34/45.9%TP (also net-negative), 80-84 263N/1.05/54.8%, 75-79 762N/3.04/51.4%, **70-74 2,176N/8.67day/49.4%TP** (just below BE, viable only because it deploys idle slots). Sweep: 0%(baseline)+439%/50.5%DD; 3%+517%/55.1%; **5%+591%/59.8% (ret+152pp/dd+9.3pp — recommended)**; 7%+603%/64.0% (only +1pp more return for +4.1pp more DD, not worth it); 10%+663%/71.7%. All 0% collapse. Works via idle-capital efficiency (75+ fills only ~4.55/day in 2022, leaving most of 10 slots unfilled — 70-74's 8.67/day mops up idle cash). Monitor TP rate; don't force-fill if it falls below ~45% in a deeper bear.

---

### SL Sweep Findings (2026-04-15, `monte_carlo_sl_sweep.py`)

Setup A (1y, Jan25-Apr26): $50k, 30dte, TP+30%, 8% flat, 20maxpos, 75+, N=500, SL 5%→75% by 5%. Setup B (5y, Jan21-Apr26): $50k, 30dte, TP+30%, 12% flat, unlimited positions (cash-only), 75+, N=500, SL 5%→60%.

**Directional finding: tighter SL always wins in simulation** — SL5%(0.182σ)→TP rate 35-37%, 1y+385,891%/13.5%DD, 5y+3.4 quintillion%/21.7%DD; SL10%→1y+66,253%/24.9%DD; SL20%→1y+15,264%/33.9%DD; SL25%(0.910σ)→TP56%/1y+13,249%/42.4%DD, 5y+444 billion%/50.2%DD; SL60% baseline→TP76%/1y+7,954%/52.8%DD, 5y+10 billion%/87.7%DD (breaches safe boundary). Safe boundary: **SL≤50%** on the 5y window. Why tighter wins: capital velocity — SL5% recycles 95% of premium immediately vs SL60%'s 40%; with positive EV and 12% alloc, faster recycling dominates the EV-per-trade advantage of looser stops.

**SL=5% is a simulation artifact — do not use.** 92.8% of SL=5% stops trigger on bar 1 (0.182σ is within normal daily noise; real bid-ask alone is ~3-5% of premium). 64.8% of signals are same-stock repeats within 15 trading days — stopping out AAPL at bar 1 just re-enters AAPL next day at a near-identical signal, riding the same underlying move repeatedly (recycling artifact, not alpha). Signal density is only 5.9/day at 75+, so most "recycled" capital returns to the same names anyway.

**Practical optimal SL: 20-25%** — stop-outs cluster bars 2-7 (genuine adverse moves, not noise), 75-80% of premium recovers, max DD stays <50% both windows, a 0.728-0.910σ adverse move is a real thesis-weakening signal.

**Score-tiered SL alloc (`monte_carlo_tiered_sl.py`)**: tiering (95+=8%→75-79=1%) massively underperforms flat 8% at every SL — 72% of all 75+ signals fall in 75-79, which only gets 1%, starving the deployment engine (max daily exposure 20% vs flat's 160%). To make tiering work, floors must sit where signal volume lives (e.g. 85+=8%, 80-84=5%, 75-79=3%).

**Updated recommended strategy (simulation-only, not live-validated):** 30 DTE, TP+30%, SL−20 to −25% (0.728-0.910σ, triggers bars 2-7), Hard−50%@day15, 12% alloc, 75+ threshold, cash-limited (no hard slot cap). Caveat: assumes zero transaction costs, instant execution, no broker margin/liquidity limits — the $billions-scale 5y numbers are directional only, not achievable returns.

### How to Re-Run

All MC sweep scripts consolidated into one canonical script:
```bash
python monte_carlo.py                       # Canonical MC: per-year + multi-year windows (WINDOWS in monte_carlo.py), seeded-RNG dispersion (3-mode system removed 2026-04-29)
python backtest_cascade.py                  # Deterministic historical backtest (complementary validation)
python monte_carlo_regime_sl_hybrid.py      # Regime-based SL switching sweep (h35→40 variants)
python monte_carlo_vix_breadth_decomp.py    # VIX vs breadth signal decomposition for SL switching

# Deterministic backtest via trader CLI (requires utf-8 console encoding on Windows):
PYTHONIOENCODING=utf-8 python trader.py backtest                          # full history
PYTHONIOENCODING=utf-8 python trader.py backtest --from 2021-01-01        # specific start
PYTHONIOENCODING=utf-8 python trader.py backtest --from 2022-01-01        # bear year only
PYTHONIOENCODING=utf-8 python trader.py backtest --capital 50000          # custom capital (default $25k)
```

`monte_carlo.py` key constants (edit in-file for sweeps): `WINDOWS` (year list + 5y continuous), `STARTING_CASH` ($50k default), `N_ITER` (500 default), `TP_BASE`/`TP_STRESS`/`SL_BASE`/`SL_STRESS`/`HARD_SELL_LOSS` (breadth-adaptive exit P&L; the old `TP_OPTION_GAIN`/`SL_OPTION_LOSS` names no longer exist), `TIER_ALLOC` (cascade % per bucket), `MAX_POSITIONS`/`PRIMARY_THRESHOLD`/`OVERFLOW_THRESHOLD`, `COLLISION_MODES` (back-compat label only — always `['seeded']` now).

For parameter re-validation, duplicate `monte_carlo.py` and modify the swept variable inside a sweep loop. Many individual sweep scripts named above have since been removed/superseded — check the repo root for the current `monte_carlo_*.py` set, and rebuild a sweep on top of `monte_carlo.py` when re-testing any locked-in parameter.
