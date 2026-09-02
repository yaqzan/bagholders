# GEX Phase 2 — Cluster-Escape Dynamics (Pre-Registration)

**Owner:** FABLE · **Date locked: 2026-07-07, before any cluster feature was computed.**
**Status: CLOSED — NULL both tracks, 2026-07-07. See VERDICT_CLUSTER.md.** Per-name GEX closed
permanently (one-shot spent); market-level DD-lever closed at current data (re-open = deep index
option history). One robust but non-actionable observation recorded (cohort_net_path timing, money-dead).
**Parent:** DESIGN.md (all hard constraints inherited: read-only, holdout cutoff 2026-06-15, ASCII,
no statsmodels, queue for long compute) and VERDICT.md (per-name linear/static GEX = NULL).
**Hypothesis (user-originated):** dealer gamma concentrates in strike CLUSTERS; price inside a cluster
is pinned (hedging friction); once price ESCAPES a cluster it fast-tracks through the low-gamma gap to
the next cluster. Two tracks, each with its own bar. **Track B is a ONE-SHOT re-entry into a closed
axis under the new-mechanism-class exception: any result below bar closes per-name GEX permanently,
including conditional forms.**

## Shared: cluster segmentation (MATH_SPEC_CLUSTERS.md, Opus-owned)
Segment the per-strike dollar-gamma profile (at current spot, feature window DTE 1-90) into clusters =
contiguous local-maxima regions. Opus decides: smoothing, prominence/mass thresholds, cluster edges,
gap definition, degenerate rules. Requirements: deterministic, degenerate-safe, self-tested with locked
synthetic references (a hand-built 3-cluster chain must segment exactly).

## Track B — per-name conditional escape test (existing data only)
- **Panel:** the 26,582-signal feature panel (rebuilt with cluster columns), joined to rs_ledger as in
  DESIGN.md. Event availability measured 2026-07-07: 27.0% of signals (7,181) sit above their call wall.
- **PRIMARY features (only these count):**
  1. `esc_above` — spot above the upper edge of the dominant gamma cluster (binary; the "escaped" state)
  2. `gap_room_up` — ln(next-cluster-above center / spot); NaN if no cluster above
  3. `local_density` — gamma mass within ±2% of spot / total gamma mass (friction at spot)
- **Labels (speed-primary, the fast-track claim):** primary = t_up from rs_ledger (implementer MUST
  read build_ledger.py to determine its exact threshold and document it; 999 = censored → primary
  regression label is the binary reached-within-window (LPM), t_up rank/spearman secondary);
  mfe15 secondary; mae15 for the risk side.
- **Controls (momentum confound is THE threat — an escaped stock is a stock that just ran):**
  stock_r20, stock_r60, overall, vol_pct. The decisive claim is escape/gap adds over momentum, not
  that escape correlates with continuation.
- **Bar (pre-registered):** date-CLUSTERED |t| ≥ 3 on a primary feature with all controls, full panel;
  sub-period thirds sign-stable (pooled AND constant-coverage — a flip in constant-coverage kills);
  75+ subset direction consistent; event-cohort N ≥ 1,000. Plain t reported but NOT sufficient —
  VERDICT.md showed plain t manufactures significance on this panel.
- **Redundancy check:** escape features vs opt_skew/semivol_r and vs raw momentum (r20/r60) —
  a feature that is just re-badged momentum fails regardless of t.

## Track A — SPY market-level GEX regime / escape (the better-prior track)
- **Data:** SPY full chains, ALL trading days 2025-02-10 → 2026-07-06 (~350), DTE 1-180, same filters,
  same builder fetch path → .cache/experiment_data/spy_gex_chain.parquet (queued pull; probe SPY
  coverage first). Daily dealer_gex + cluster features on each date.
- **Series (primary):** `spy_gex_ratio`, `spy_flip_dist` = ln(spot/flip), `spy_gap_below` = ln(spot /
  next-cluster-below center) — the crash-runway, `spy_esc_below` — spot below the dominant cluster's
  lower edge (binary). Exploratory: netgex magnitude, wall distances.
- **Labels:** SPY forward 5d and 10d realized vol; SPY forward 10d max drawdown; DATE-AGGREGATED cohort
  outcomes: mean pnl15 (ortho panel) and mean net_path (rs_ledger) of signals fired that day.
  Date-aggregation sidesteps per-name clustering entirely; honest N ≈ 350 daily obs.
- **Controls (must beat the shipped DD-lever inputs, else redundant):** VIX level (RXDD's input),
  VIX 5d change, McClellan oscillator (MWDD's), TRIN (TVDD's), breadth EMA% — from market_regime /
  market_breadth tables (one light query).
- **Inference:** OLS with WEEK-clustered SEs (pre-registered; N≈350 → ~70 weekly clusters).
- **Bar (LEAD QUALIFIER, explicitly NOT a ship gate):** week-clustered |t| ≥ 2.5 on a primary series
  with all controls, AND sign-stable across date halves, AND economically material (top-vs-bottom
  quintile forward-10d-DD spread ≥ 1.5×). PASS ⇒ "advance to Stage-3 mechanism_registry + N=300 MC
  route" (the real gate); FAIL ⇒ market-level GEX lead closed too. Single 1.3y window, one selloff —
  even PASS cannot distinguish regime luck; say so in the memo.

## AMENDMENT 2026-07-07 (post SPY-coverage probe, BEFORE any Track A number was computed)
Probe result: SPY option_prices coverage = **2025-06-25 → 2026-07-06 only, 147 usable dates (~58%
day-fill, raw rows 337–11,577/date)** — the window EXCLUDES the 2025 spring selloff, and N≈147 →
~30 weekly clusters. Track A as registered is materially degraded for a DD-lever qualifier. Response:
- **A1 (SPY chains, as registered, caveated):** runs on the 147 dates with a chain-quality gate —
  a date is excluded unless ≥50 distinct strikes AND ≥500 filtered rows (exclusions reported).
  Explicitly labeled "coverage-limited first read; no crash regime in window."
- **A2 (cross-sectional aggregate, pre-registered here):** daily market-level series from the EXISTING
  per-name signal-date features: `mkt_gex_ratio` = mean gex_ratio of that day's signals,
  `mkt_neg_share` = share of that day's signals with gex_regime = −1; days with <30 signals excluded.
  Window = full 2025-02-10 → 2026-07-06 INCLUDING the spring selloff. Same labels, controls, bar,
  and week-clustered inference as A1. Known bias, stated up front: composition is conditioned on the
  70+ signal cohort (breadth-of-momentum selection) — this is "dealer positioning of the momentum
  cohort," not the whole market; a PASS must survive that framing.
- **Verdict rule:** market-level lead closes only if BOTH A1 and A2 fail their bars; either passing
  advances the lead (with the other's read reported alongside).

## Sequencing
1. SPY chain pull (queued, ~30-60 min db-heavy) — starts immediately, independent of segmentation spec.
2. MATH_SPEC_CLUSTERS.md (Opus) → dealer_gex.py extension (Sonnet, selftest incl. new locked refs)
   → feature rebuild on existing cache (queued, ~10 min) + SPY daily features.
3. Track B harness (gex_cluster_test.py) and Track A harness (spy_gex_test.py) — Sonnet, reusing
   gex_test.py's OLS/clustered-SE machinery.
4. FABLE reads → VERDICT_CLUSTER.md. Adversarial verify panel fires ONLY on a positive.
