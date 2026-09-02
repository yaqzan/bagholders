# Trend MA-Lattice Study — overnight gameplan (2026-07-13 → open Tue 2026-07-14 09:30 ET)

**Status:** READY TO RUN · **Author:** Fable (brain, Archivist session recon 2026-07-13 22:00) ·
**Execution:** /research-style, subagent-tiered (orchestrator = brain, Sonnet agents = hands, queue = compute)
**Budget:** ~11h weeknight (22:xx start → 09:30 ET open). Fits: Phase A mine → Gate A → Phase B ReSim arms → Gate B → STAGE (ship only if exceptionally clean with ≥45min margin, G13).

---

## 0. The ask (user, verbatim intent)

> Explore the EMA and SMA across different time horizons and gauge their interactions in relation
> to the trend score. Trend is an integral part of our strategy but it's narrow — we can benefit
> from MC Bayesian runs of different time horizons or cross points, above/below behaviour.

Mapping onto repo discipline: **the horizon/cross/kernel grid is judged on the trade ledger
(read-only, decisive, minutes)** with a **Bayesian posterior layer** replacing eyeballed
window-stability; **paired-seed MC** is reserved for the portfolio confirm of a surviving Stage-1
candidate only. A full MC sweep over MA grids would be the wrong tool (MC prices sizing
consequences; a scoring-feature grid resolves on the ledger — G24/G27 precedents).

## 1. Null-ledger triage (G17 — DONE 2026-07-13, don't re-do)

**Genuinely untrodden:** the trend component's INTERNAL construction. No experiment, version, or
sweep has ever varied its MA periods, kernel (EMA vs SMA), momentum lookbacks, or tested
cross/above-below state signals on it. No `trend_*/ema_*/sma_*/ma_cross` experiment dir exists.
All prior "trend" work treated `Score.trend` as a black box.

**Adjacent CLOSED work that CONSTRAINS design (never re-run):**
- SuperTrend/PSAR trend-flip markers — full-universe gate −1.2pp, dominated by score (2026-07-01).
- Regime-conditioned / dynamic component REWEIGHTING (de-weight trend in bear/chop) — null, trend flattest ±0.8pp (2026-06-25).
- Trend/oscillator divergence dampening/lifting — null both halves (2026-06-03).
- MACD-gate gradients ×2 (widen 2026-05-05; call-preserving replacement 2026-05-24) — null.
- VIX weekly-MACD/velocity (VXMD 2026-06-09) — null; beware the weekly-resample LOOK-AHEAD trap (G28).
- 50W SMA / 52W high-low: weak (1-2pp at 75+, `experiments/weekly_avwap`); Ichimoku shipped v44 → retired v73.
- A-MKT SPY-flat contraction — tested-null, MWDD-redundant (2026-06-25).
- MISS_CANDIDATES table — INVALIDATED on live apex15 barrier; ignore its trend rows.

**Priors AGAINST a win (be honest):** A0/`weather_components` — trend drives `overall` most
(corr +0.72) but has ~ZERO per-trade resolution at the funded gate and is regime-HARMFUL in
bear/chop (2022 −2.42 / 2023 −1.81 ΔEV). G14 honest frontier: every price partition → opt15 ~47%.
**Expected outcome = a decisive, permanent closure of the trend-horizons/cross axis** (high value
in this codebase), with three live long-shots:
1. **Kernel divergence** (EMA−SMA spread = recent-acceleration signal — never tested anywhere);
2. **Cross freshness** (days-since-cross; G27 says winners are direct-continuation, so hypothesize
   fresh-cross = higher EV, stale-cross = decayed);
3. **Sub-term decomposition** — WHICH of trend's 4 sub-scores is dead weight / carries the
   bear-chop harm → a LEAN retune (drop/reweight a sub-term) is a clean, targeted Stage-1.
   This is the sharpest "trend is narrow" attack and the centerpiece of Phase A.

## 2. Current trend component — anchors (verify on read; recon 2026-07-13)

- `Stock.calculate_trend_score` — `database/models/core.py:6029-6114`. All **EMA** (talib), periods
  **21/50/200**, `lookback=20` bars. Four sub-scores, each `50+50*tanh(k·x)`:
  1. **position 30%** — price vs EMA50 (60%) + price vs EMA200 (40%), k=0.3/0.15
  2. **alignment 25%** — (EMA21−EMA50)/EMA50, k=0.4
  3. **momentum 20%** — EMA21 slope over 10 bars (k=0.5) + EMA50 slope over 20 bars (k=0.8)
  4. **macro 25%** — (EMA50−EMA200)/EMA200, k=0.2
- Weight in overall: `W_TREND_BASE=18 + W_TREND_SLOPE=10 · trend_dominance` (`strategy_config.py:355-364`,
  `database/utils/scoring.py:1090-1119`) → 18% sideways → 28% trending. Trend also feeds RSI & BB
  via `trend_bias=tanh((trend−50)*0.06)` (`core.py:5597`, `:5834`) — any arm changing trend ripples
  into 3 components + weights; ReSim full-recompute captures this automatically.
- `Indicator` table stores **both** `ema_9/21/50/200` AND `ma_9/21/50/200` (SMA) per (symbol,date)
  (`database/models/technical.py:~230`) — SMA columns computed but NEVER consumed. Canonical-period
  EMA-vs-SMA comparison is a free join.
- `Score.trend` persisted per (symbol,date,version) (`core.py:412`); `recalculate
  --reuse-components-from VERSION` clones components and recomputes overall-only (fast path).
- Closes: `PriceHistory` (`technical.py:47-55`), full yfinance-max depth.
- Active version **v74 LEAN** (`ALGORITHM_VERSION=f9fb7b934`). Live profile = apex (verify
  `GET /api/portfolio/state`). Holdout lock: `CALIBRATION_CUTOFF_DATE="2026-06-15"` — **every mine
  must `pre_cutoff_filter` (`experiments/_holdout.py`)**; OOS re-eval not before 2026-12-15.

## 3. Study design

### Phase A — read-only point-in-time mine (the decisive cheap test; NO recalc, NO MC)

**A1. Feature grid** (per symbol × entry_date, strictly point-in-time from `PriceHistory` closes;
recursive one-pass EMAs/SMAs, cache `(closes, {date:idx})` per symbol — G18/G28):
- Kernels: **EMA and SMA** × periods **P = {8, 13, 21, 34, 50, 100, 150, 200}** (300 optional).
- Per (kernel, period): `above` (close>MA), `dist` = (close−MA)/MA signed %, slope sign over 10 bars.
- Pair crosses, per kernel, pairs {(8,21),(13,34),(21,50),(21,100),(50,100),(50,200),(100,200)}:
  `cross_state` (fast>slow), `days_since_cross` (cap 60), `fresh_golden`/`fresh_death` (≤5d).
- **Stack alignment**: ordering state over {21,50,200} + price (0-4 aligned count).
- **Kernel divergence (novel)**: sign + magnitude of (EMA_p − SMA_p)/SMA_p at p ∈ {21,50,200}.
- **Trend-score interaction lattice**: bucket stored `Score.trend` {<40, 40-55, 55-70, ≥70} ×
  selected MA states — where the score and the lattice DISAGREE (e.g. trend≥70 but close<SMA200),
  does the disagreement cell carry EV?

**A2. Sub-term decomposition:** recompute trend's four sub-scores per (symbol, entry_date) from the
mined EMAs + close (formula in §2). **Correctness anchor: the recombined int must reproduce stored
`Score.trend` (±1) on a ≥500-row sample — if it doesn't, STOP and fix.** Then run the same cohort
EV analysis per sub-term (and per horizon-variant of each sub-term) to find dead weight and the
bear-chop-harm carrier.

**A3. Judgment surface:** funded **75+ calls, apex option EV + apexWR primary** (clone ledger
loading from `experiments/retrace_entry_v70/mine.py`, N≈4,699 holdout ledger), WR15 secondary;
full-universe 75+ `barrier_outcomes` DuckDB mirror as the replication surface. Per-window splits
(2021/2022/2023/2024/2025). **Partial regression controls (G25/G28c): every candidate feature's
effect on win must survive controlling for stored `Score.trend` AND `pct_from_ema50/200`** (the
composite already ingests those — a feature that collapses to t≈0 controlled is already captured.
Logit for WR, OLS for EV.)

**A4. Bayesian layer (the user's "MC Bayesian", formalized):** per cohort × window, Jeffreys
beta-binomial posteriors on WR (and normal posteriors on EV); hierarchical pooling across windows
(numpy Monte Carlo over posterior draws, ~10k, no new deps). Report per feature-cohort:
**P(pooled edge > 0)**, **P(sign consistent across ≥4/5 windows)**, and the posterior pooled Δ with
a heterogeneity penalty. This replaces eyeballed sign-stability and directly guards the G23 trap
(N=100 Phase-B artifacts that flip at N=300).

**Multiplicity discipline (pre-registered, don't let a 2am agent get excited):** the grid is
~hundreds of cells; at z≥3 expect ~1 false hit by chance. PRIMARY families (judged at z≥3 raw +
t≥2.5 controlled + posterior P(sign-consistent)>0.90): kernel-divergence, cross-freshness,
sub-term decomposition. EVERYTHING else is exploratory: needs z≥4 OR independent replication on
both surfaces (funded ledger AND full-universe mirror). Report expected-false-hits alongside hits.

**GATE A (decide before building anything):**
- **No cell clears** → the axis is DEAD. Write FINDINGS, add the WHAT-NOT-TO-DO entry, close the
  lead honestly. This is the EXPECTED branch and a full success. Stop or spend remaining budget
  reading NEW_LEADS for a Stage-3 pick per /research Phase 1.
- **Sub-term insight only** (e.g. macro term carries the bear-chop harm, alignment carries signal)
  → Phase B with LEAN-retune arms.
- **Feature residual clears** (kernel-divergence / cross-freshness real, controlled, window-stable)
  → Phase B with augment arms.

**Scope guard:** sizing/DD-lever framing is OUT (G23 dry well; A-MKT null). If the mine
incidentally surfaces a monster orthogonal DD cohort, log it to NEW_LEADS — do NOT build tonight.

### Phase B — sharded ReSim A/B arms (only on a Gate-A pass)

Clone `experiments/integrity_audit_2026_06/ab_eval.py` (G30): ScoreSimulator per symbol-shard,
all arms in-process via monkeypatch of `Stock.calculate_trend_score` (try/finally restore),
join (sym,date,overall) per arm to the barrier_outcomes mirror, judge by **delta-cohort funded WR/EV
(admits/removes vs shared cohort)**. **Validation arm (replica of stored v74 rows, ≥98% exact) is
mandatory before any verdict.** Budget per G34: ~18min/shard-arm → cap **≤6 arms ≈ 2-2.5h queued**;
give the harness the skip-if-parquet-exists resume guard BEFORE first launch; merge shards manually
if the queue kills at timeout.

Arm menu (pick ≤6 by Gate-A evidence; each = one coherent hypothesis, no kitchen sinks):
- **ARM-K** kernel swap EMA→SMA at 21/50/200 (isolates kernel).
- **ARM-Hf / ARM-Hs** horizon shift: {13,34,100} / {34,100,200} period sets.
- **ARM-X** fold best cross-freshness feature into the momentum sub-score.
- **ARM-D** kernel-divergence as the acceleration term (replace/augment slopes).
- **ARM-L** LEAN retune: drop/reweight the dead sub-term found in A2 (e.g. macro 25%→0, renormalize).
- **ARM-W** sub-weight retune toward the strongest family.

**GATE B:** an arm must improve funded-cohort EV/WR with window stability (same Bayesian layer),
WITHOUT gutting supply (report admits/removes balance + 75+ cohort size; real-supply gate proper
runs post-recalc). Weak N=100-style margins = null (G23: strong levers cleared noise by 5×).

### Phase C — stage or ship (G13)

Default **STAGE**: commit the winning arm env-gated OFF + `SHIP_HANDOFF.md` (winner params, A/B
evidence, exact Stage-1 steps). Ship tonight ONLY if: Gate B clean by ~05:00, then
`find-and-ship-alpha` end-to-end (bump → staged recalc → assess → W1-W6 + growth gate) with ≥45min
margin — remember the three-part comparability unit (research pack + supply/hydration row + PRF)
after any scoring ship. A scoring change ships via Stage-1, NOT via portfolio MC; run one N=300
paired-seed MC confirm on the winner only if time allows (nice-to-have, not the gate).

## 4. Execution protocol (tonight, token-optimal)

**Tiering: the orchestrator NEVER writes the analysis code and NEVER reads raw parquet.** Sonnet
subagents implement from the briefs below; queue runs compute; every script ends with a delimited
`==== SUMMARY ====` block (≤80 ASCII lines) — the orchestrator greps THAT, decides, moves on.

**Phase 0 (orchestrator, ~10min):** /research Phase-0 verbatim — next-open calc, `trader queue
status` (daemon UP or start it), `trader algorithm active`, `git status --short | wc -l`,
TaskCreate tracklist. Then `mkdir experiments/trend_ma_lattice` and copy this gameplan in as
`GAMEPLAN.md`.

**AGENT-1 (Sonnet, ~40min): implement `experiments/trend_ma_lattice/mine.py` (+`features.py`).**
Brief: clone ledger-loading + cohort-stats scaffolding from `experiments/retrace_entry_v70/mine.py`
and the point-in-time recursive-EMA pattern from `experiments/vix_weekly_v70/mine.py`. Implement
§A1-A4 exactly (grid, sub-term decomposition, controls, Bayesian layer — numpy only). Mandatory
self-tests at top of run: (1) recomputed EMA/SMA at 21/50/200 matches stored `Indicator` columns on
a sample (tolerance 1e-3 rel); (2) **look-ahead test: features at date d identical when computed on
data truncated at d** (sample 50 symbol-dates); (3) trend recomposition matches stored `Score.trend`
±1 on ≥500 rows; (4) `assert_no_holdout_leak` / `pre_cutoff_filter` applied. Output:
`.cache/trend_ma_lattice/mine_*.parquet` + the SUMMARY block (ranked cohort table: family, cell,
N, apexEV Δ, apexWR Δ, z_raw, t_controlled, P_pooled, P_sign_consistent, per-window signs).
`PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, ASCII prints only (G5). Smoke on 20 symbols before full run.

**AGENT-2 (Sonnet, ~40min, fire only on Gate-A pass): implement
`experiments/trend_ma_lattice/resim_arms.py`.** Brief: clone
`experiments/integrity_audit_2026_06/ab_eval.py` sharding + validation-arm pattern; arms = §B menu
(orchestrator supplies the ≤6 chosen arms + params as a literal dict); monkeypatch
`Stock.calculate_trend_score` per arm with try/finally restore; skip-if-parquet-exists resume
guard; per-arm SUMMARY (delta-cohort funded WR/EV vs baseline + validation match %, admits/removes).

**Queue commands (verbatim skeletons):**
```bash
# Phase A mine (light DB, market closed → high priority fine)
trader queue submit --priority high --db light --cpu 4 --restartable --timeout 45m \
  --dedup tml-mine --reason "trend MA lattice mine" \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  -- python -u experiments/trend_ma_lattice/mine.py
trader queue wait <id> --timeout 1h   # under run_in_background; NO pipe (G34b — pipe eats exit code)

# Phase B ReSim (heavier; budget = build + 20min × n_arms, +30% headroom)
trader queue submit --priority high --db light --cpu 7 --restartable --timeout 3h \
  --dedup tml-resim --reason "trend MA lattice ReSim arms" \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  -- python -u experiments/trend_ma_lattice/resim_arms.py
```

**Orchestrator rules:** verify agent output by running the script's self-tests (smoke mode), not by
line-reading the diff — EXCEPT eyeball one thing personally: the point-in-time feature loop (the
look-ahead trap is the one bug that silently fakes a result, G28). Read SUMMARY blocks only. Apply
gates as written — the multiplicity bar is pre-registered, don't soften it at 3am. G31: no engine
edits while a queued job that imports them runs (Phase B monkeypatches at runtime — safe; but do NOT
ship-wire strategy_config/core.py mid-sweep). G29: run from the MAIN checkout, not a worktree.
G2: check `trader queue show <id>` timestamps before declaring a hang.

## 5. Deliverables by morning (all branches)

1. `experiments/trend_ma_lattice/FINDINGS.md` — verdict per family (kernel / horizon / cross /
   above-below / divergence / sub-term), the ranked-cohort table, per-window + posterior evidence,
   and the explicit "what would change this verdict" line.
2. Ledger updates: `known-issues.md` (WHAT-NOT-TO-DO entry on a null: "Never re-mine trend-component
   MA horizons/kernels/crosses without a new discriminator — <evidence>"; or CURRENT SHIP STATE on a
   ship), `alpha_mining/NEW_LEADS.md` (close/A0-append; log any incidental DD-cohort observation),
   auto-memory, `version-history.md` if shipped.
3. On Gate-B pass without ship: env-gated arm committed OFF + `SHIP_HANDOFF.md` (one-step enable).
4. `/research` SELF-UPDATE: append any new gotcha hit tonight (e.g. ReSim monkeypatch-of-method
   frictions) to LIVING GOTCHAS.
5. Handoff note in `.claude/handoffs/` if anything is left half-open (use the handoff skill).

## 6. Kickoff (paste into a Trader-project session tonight)

```
/research Run tonight's pre-built study: .claude/handoffs/2026-07-13-2210_trend-ma-lattice-gameplan.md
— trend MA-lattice (EMA/SMA horizons × crosses × above/below vs Score.trend). Execute it AS WRITTEN:
Phase A read-only mine → Gate A → Phase B ReSim arms → Gate B → stage-or-ship per G13. Triage is
already done (§1) — do not re-scout the null ledger. Use Sonnet subagents for all script
implementation per §4 briefs; you are the orchestrator/brain: read SUMMARY blocks, apply the
pre-registered gates, never soften the multiplicity bar. Expected outcome is an honest decisive
closure; ship only if exceptionally clean with margin.
```
