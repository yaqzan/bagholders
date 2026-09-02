# GEX Experiment — Design & Architecture Decisions

**Owner:** FABLE (architect). All implementation agents read this file first and conform to it.
**Status:** CLOSED — NULL 2026-07-07. See VERDICT.md. Per-trade GEX re-mining is closed; one
follow-up lead documented (market-level SPY-GEX as Stage-3 DD lever). Conditional OOS re-read
~2026-07-29 only if gex_ratio is re-opened.
**Mission:** Test whether dealer-GEX / gamma-positioning features add ORTHOGONAL predictive power
(win-rate + forward P/L) over the directional score AND the confirmed opt_skew edge, on the 1.3y
in-sample option panel, with a first OOS read on 2026-05-16 → 2026-07-06. Kill-or-stage; no ship claim.

## Hard constraints (non-negotiable)
- READ-ONLY offline experiment. New code ONLY under `experiments/gex/`. Never touch
  scoring.py / core.py / simulator.py / api.py / strategy_config.py. No env-gates / PHASE_X flags.
- MySQL: materialize ONCE to parquet; never iterate queries against it. Set SESSION MAX_EXECUTION_TIME.
  Template query shape: `experiments/iv_skew/build_option_slice.py` + `experiments/iv_skew/build_iv.py`.
- Run with `PYTHONUTF8=1`. No statsmodels (numpy OLS). polars 1.40 (`group_by`, not `groupby`).
- Holdout: `CALIBRATION_CUTOFF_DATE = "2026-06-15"` (authoritative: strategy_config.py:61 — the
  mission brief's 2026-05-15 was stale; re-locked 2026-06-15). The guard
  `from experiments._holdout import assert_no_holdout_leak; assert_no_holdout_leak(df, 'gex')`
  raises on any date > cutoff — call it on every in-sample analysis input, NEVER on the forward-window
  frame, and NEVER set HOLDOUT_DISABLE. The CACHE may and should include dates through 2026-07-06
  (do NOT "helpfully" filter them out); leak enforcement happens at ANALYSIS time.
- Always report N. Single-regime 1.3y panel → per-trade t-stat is the ceiling. No portfolio claims.

## D1 — LABEL (decision)
Dual-track, roles pre-assigned:
- **Mechanism-primary (does the physics work?):** underlying-path labels from
  `.cache/rel_strength/rs_ledger.parquet` — `mfe15`, `mae15`, `t_up`, `t_dn`, and derived
  `net_path = mfe15 - mae15`. **Convention RESOLVED (scout 2026-07-06):** labels are built by
  `experiments/component_reweight/build_ledger.py` — mfe15/mae15 = max favorable/adverse excursion
  over 15 CALENDAR days (high/low vs entry = signal-date close, forward starts next calendar day),
  sigma-normalized by 60d realized daily vol (`vol_pct` = that sigma x100); **both stored POSITIVE**
  (mae15 is |adverse|). t_up/t_dn = calendar days for cumulative-max excursion to reach sigma
  thresholds, 999 = untouched within MAXW=40. mfe15/mae15 are None when the forward window is
  shorter than 15 cal days. The forward-window extension MUST reuse this exact logic
  (import or copy from build_ledger.py:90-162).
- **Value-decisive (does it pay, net of what we already have?):** `pnl15` from
  `.cache/iv_skew/proxy_ledger.parquet` (opt_skew NOT NULL panel), orthogonalized against the full
  control set: `opt_skew + semivol_r + overall + stock_r20`.
Justification: GEX physics acts on the underlying via dealer hedge flow, but the fundable claim is
option P/L net of the already-confirmed opt_skew edge. Underlying-only risks an edge that dies in
premium (theta/IV crush); pnl15-only risks mistaking an opt_skew echo for new physics. PASS needs both.

## D2 — DEALER-SIGN CONVENTION (decision)
Standard industry naive proxy (SqueezeMetrics-style): investors net-long puts / net-short calls →
dealers are LONG call gamma, SHORT put gamma:

  netGEX(S_eval) = Σ_contracts γ_BS(S_eval, K, T, iv) · OI · 100 · S_eval² · 0.01 · sign
  sign = +1 for calls, −1 for puts       (units: dollar gamma per 1% underlying move)

- Levels: **flip/zeroGEX** = zero-crossing of netGEX over a spot grid (gamma RECOMPUTED at each grid
  spot, not rescaled); **call wall** = strike of max positive per-strike GEX; **put wall** = strike of
  max put-side |GEX|; **COTMC/COTMP** = OI-weighted mean call/put strikes.
- Their "db_change" is NOT reproducible from raw OI → DROPPED in v1 (day-over-day OI delta is a
  possible v2 proxy, out of scope now).
- r = 0.04, q = 0, T = calendar days/365. Documented limitation: OI carries no side info — this is
  the standard proxy assumption; we test it empirically rather than argue it.
- Opus math spec (`MATH_SPEC.md`) governs exact formulas, grid, degenerate cases, and unit tests.

## D3 — CHAIN SCOPE (decision)
- Cache: full strike chain, DTE 1–180, filters OI > 0 and iv ∈ (0.01, 5.0) — report drop rates.
- Features computed on DTE 1–90 (a-priori: hedging-relevant gamma mass concentrates < 90d).
  Cache is wider than the feature window so expiry-range sensitivity can be tested without re-hitting MySQL.

## D4 — FEATURES (pre-registered; multiplicity control)
**PRIMARY (only these three count toward PASS/WEAK/NULL):**
1. `gex_regime` = sign(netGEX @ spot), plus continuous `gex_ratio` = netGEX/Σ|GEX| ∈ [−1,1]
2. `flip_dist` = ln(spot / flip)   (signed distance above the gamma flip)
3. `callwall_dist` = ln(callwall / spot)   (upside room to the call wall)

**EXPLORATORY (direction-setting only, never a PASS on their own):**
`putwall_dist`, `cotmp_dist`, `netgex_slope` at spot, `log10(Σ|GEX|)`.
No interactions in v1 — interactions are round 2 only if a primary shows life.

## D5 — PANEL (revised after recon 2026-07-06)
Recon facts: rs_ledger date ≥ 2025-01-01 → **59,875 signals, 729 symbols, 2025-01-02 → 2026-05-15**;
proxy_ledger opt_skew NOT NULL → **1,998 rows, 2025-02-11 → 2026-05-15**; inner join = 1,998.
**Both ledgers END at 2026-05-15 — they contain zero post-cutoff rows.**
- **In-sample mechanism panel:** all rs_ledger signals ≥ 2025-01-01 with non-empty chains (N up to ~60k;
  underlying labels mfe15/mae15/t_up exist for all). All dates ≤ 2026-05-15 < cutoff → leak-guard passes.
- **In-sample orthogonalization panel (pre-registered 2026-07-06, BEFORE any GEX feature was computed):**
  POOLED = proxy join 1,998 rows (2025-02-11 → 2026-05-15) + iv_ledger_ext gap slice 542 rows
  (2026-05-18 → 2026-06-15, pnl15 fully resolved, all ≤ cutoff) ≈ 2,540 rows. The decisive OLS runs on
  the POOLED panel, with the 1,998-only result reported as secondary (comparability with the original
  opt_skew confirmation). **Pooling VERIFIED 2026-07-06 (code-read) — POOLED PANEL IS PRIMARY,
  unconditionally:**
  (1) skew parity SAME: ext imports the identical `otm_iv` function (build_iv_ext.py:36 from build_iv);
  skew = put_iv − call_iv, legs = nearest strike to close×(1±0.10), DTE 20–45, iv∈(0.05,5), OI>0.
  (2) pnl15 parity SAME: shared `fwd_pnl` (build_iv.py:58-73) — nearest-ATM call DTE 20–45,
  entry = op.price, exit = 15th TRADING bar within signal+24 cal days, no barriers, no cost haircut.
  **CAUTION: pnl15 horizon = 15 TRADING days (≈23 calendar); mfe15/mae15 = 15 CALENDAR days —
  different label families, never conflate.**
  (3) Controls recomputable purely from price_history: `semivol_r` = std(dn)/std(up) over trailing
  60-trading-day daily returns ending at signal date, needs ≥3 up AND ≥3 dn days else None
  (build_proxy.py:41-59); `stock_r20` = close[d]/close[d−20 bars] − 1 (build_rs.py:47-54,71).
  ext facts (recon 2026-07-06): 845 rows total, 238 symbols, cols incl. skew/pnl15/pnl_max/pnl_min/
  is_oos/pnl15_resolved/version_id; >6/15 slice = 303 rows, skew present, pnl15 all-null (unripe).
  `pnl15_resolved` = signal date ≤ (scores-table end − 23 cal days); true-holdout rows resolve
  progressively from ~2026-07-09, fully by ~2026-07-29. **The mid-July OOS re-read requires
  re-running build_iv_ext.py first** to populate newly-resolved pnl15.
- **Forward window (2026-05-16 → 2026-07-06): not in the ledgers — built as a thin extension.**
  Signals = scores table, overall ≥ 70, ACTIVE scores version (`AlgorithmVersion.get_active_scores_version()`,
  = version_id 74). Labels = underlying-path (mfe15/mae15 recomputed from price_history with the EXACT
  build_ledger.py convention — see D1). Report in TWO slices, never pooled silently:
  (a) 2026-05-16 → 2026-06-15 **pre-lock gap** (~540 signals, in-sample-ELIGIBLE — may be added to the
  mechanism panel as extra N, flagged as a separate slice; NEVER into the orthogonalization panel unless
  iv_ledger_ext recon (below) provides honest pnl15+opt_skew for it);
  (b) 2026-06-16 → 2026-07-06 **true holdout** — direction-only, no calibration/selection ever.
- **Data-currency facts (parallel arm, 2026-07-06):** the ledgers stop at 2026-05-15 only because
  `build_iv.py` hardcodes a stale CUTOFF — option_prices and price_history are CURRENT to 2026-07-06.
  `.cache/iv_skew/iv_ledger_ext.parquet` may already carry post-5/15 rows (recon pending — schema/
  coverage decides whether the orthogonalization panel extends into the gap).
- **Label ripeness:** labels are 15 CALENDAR days (D1), so as of 2026-07-06 they are ripe only for
  signals ≤ 2026-06-21. True-holdout ripe slice today = 6/16→6/21 (tiny N — report N + direction,
  explicitly underpowered). **Scheduled re-read ~2026-07-15+** when the holdout slice fattens; the
  verdict memo must state this timing rather than overclaim.
- **OOS upgrade discovered in recon:** iv_ledger_ext already tracks the true-holdout cohort
  (303 rows > 6/15 with skew, pnl15 pending, pnl15_resolved flag). When those resolve (~mid-late July),
  the re-read is a REAL orthogonalized OOS money test on ~300 rows (controls recomputed as above) —
  not merely an underlying-direction glance. The verdict memo schedules this explicitly.

## D6 — VERDICT BAR
- Univariate: quintile WR + monotonicity + spearman, full AND 75+ (overall ≥ 75).
- Decisive: numpy OLS `label ~ gex_feature + opt_skew + semivol_r + overall + stock_r20`
  (standardize, lstsq, t = beta/se). In-sample pnl15 |t| ≥ 3 on a PRIMARY feature → PASS candidate
  (then mandatory adversarial verify by an independent agent; one sustained refutation kills it).
  2.0 ≤ |t| < 3 → WEAK. Else NULL.
- Sub-period sign stability across 1.3y thirds: a sign flip on the claimed feature kills it.
  **Composition-drift control (added post-coverage-recon):** per-symbol option coverage starts at
  different dates (some symbols 2025-02, some only 2026-05), so sub-periods differ in symbol mix.
  Run the stability test twice: (a) pooled, (b) restricted to the constant-coverage symbol subset
  (symbols covered across the full window, from the build report's coverage map). A flip that appears
  only in (a) is composition artifact, not signal instability; a flip in (b) kills.
- OOS: significant opposite direction kills; same-direction = corroboration, not proof.
- Source system's claimed numbers (e.g. "db_change ≥ 0.50 → 100% WR") = zero evidence; we test mechanism only.

## Deliverables / pipeline
| # | Artifact | Tier | Status |
|---|----------|------|--------|
| 1 | `build_gex_cache.py` → `.cache/experiment_data/gex_chain.parquet` + `gex_build_report.json` | Sonnet impl, smoke-verified, full build via task queue | in flight |
| 2 | `MATH_SPEC.md` (Opus) → `dealer_gex.py` (Sonnet) → per-(symbol,date) features parquet | Opus spec / Sonnet impl | spec in flight |
| 3 | `gex_test.py` → `gex_results.txt` (univariate + orthogonalized + sub-period + OOS) | Sonnet | pending |
| 4 | `VERDICT.md` | FABLE | pending |

## Cache schema (deliverable 1, fixed)
One row per contract per signal date:
`symbol (str), date (date), spot (float64), expiration (date), dte (int32), strike (float64),
option_type ('call'/'put'), open_interest (int64), iv (float64)`
Path: `.cache/experiment_data/gex_chain.parquet`. Build report: signals requested / signals with
chains / total rows / per-symbol counts / date range / filter drop rates → `gex_build_report.json`.
