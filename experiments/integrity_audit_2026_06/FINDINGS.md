# Integrity Audit Fix Campaign — Findings (2026-06-09/10)

Executes [AUDIT_FIX_HANDOFF.md](AUDIT_FIX_HANDOFF.md). Worktree `algo-exp/integrity-audit`
off main `d21cea14d` (calendar-hold included). Ship target: **v71** (one bundled honest
scoring version).

## Fixes implemented (all in this worktree)

| Defect | Fix | Where |
|---|---|---|
| **F2** spy_wk current-week look-ahead (mis_stress + JA4) | `_spy_wk_last_completed()` (lookup at target−7d, the kijun/wv_force convention); ALL 5 per-date call sites switched (single-row, mis-stress map, reapply_regime_today, batched, recalc) + the simulator's own `_build_mis_stress_map` | `database/models/core.py`, `simulator.py` |
| **F4** static-mcap survivorship leak (MCD) | PIT proxy `mcap_t ≈ mcap_latest × close_t / anchor_close` (anchor = symbol's true latest close — recalc slices make "last loaded row" unsafe); `compute_pit_mcap_b`/`build_pit_mcap_map` shared by single-row/batched/recalc/simulator | `database/utils/scoring.py`, `core.py`, `simulator.py` |
| **F1** wave silently inert (missing source CSV) | Loud once-per-process stderr guard (missing source when enabled; >7d-stale forward-fill) — never raises; + self-contained source rebuilder from SPDR DB history (1999-03-05 → present, 6,858 rows) | `database/utils/sector_breadth_wave.py`, `build_market_wave_source.py` |
| **F3** truncated barrier cache → silent cont-echo zeroing | 10y backfill completed (queue #101, 2h10m: **2016-04-26 → 2026-06-09**, 83.3M rows, 801 syms, duck mirror fresh) + loud coverage guard in `_load_cont_barrier_wins` | cache + `core.py` |
| regression tests | F1/F2/F3/F4 unit+guard tests, all green | `tests/test_integrity_guards.py` |

Also fixed en route: simulator now passes `put_regime_multiplier` (JA4) via an injectable
`_put_mult_map` (it never had JA4 — a sim/production put-side divergence).

## Cleared suspicions (no code change; docs updated in Phase F)

- **C1**: ICH `kijun_pct` + WVD `wv_force1` do NOT have a residual weekly look-ahead —
  both builders already use the deliberate last-completed-week (−7d) lookup, wired
  identically in all three scoring paths. The CLAUDE.md/known-issues note is retired.
- **C2**: continuation-echo outcome timing is PIT-safe (`gap >= W` gating). F3 was about
  cache *coverage*, not timing.

## A/B evaluation method (honest substrate)

ReSim pattern, full universe, 5y (`EVAL_START=2021-06-01`), 7 symbol-shards × 6 arms
(`ab_eval.py`); arms differ only by module-constant / map patches; outcomes joined to the
REBUILT barrier cache (DuckDB mirror) at W=15: **option-aligned `30dte_opt` primary**,
`30dte_generic` sanity. Verdict doctrine per handoff §6: bias to RETIREMENT when marginal.

Arms: `legacy` (pre-fix replica; validation vs stored v70), `fixed` (v71 core),
`ms_off`, `ja4_off`, `mcd_off`, `wave_on` (rebuilt source — NOT the lost v57 file).

### Validation (legacy arm vs stored v70 rows)

**98.43% exact match** over 936,580 common (sym,date) rows; mean |Δ| 0.049; only
0.57% of rows differ by ≥3 pts (4.7% of those carry stored `cont_lift` — the
simulator has no continuation echo; the rest are minor env diffs). The harness
faithfully reproduces production — arm deltas are trustworthy.

### Verdicts (all four: RETIRE — full tables in `analysis_report.txt` / `verdict_data.json`)

**mis_stress (CALL softener) → RETIRE** (`MIS_STRESS_CALL_DAMPEN=0.0`).
With the F2 lag fix in place, the softener's admits at the tradable ≥75 gate run
BELOW the shared-cohort baseline: N=138 @ 50.7% vs shared 55.2% optWR15
(z=−1.03); at ≥70: N=1,646 @ 51.0% vs 52.0%. Every bucket flat (|z|≤0.21). The
original ship evidence (+0.2pp, +5.6% N) was measured WITH the leak and
calibrated on the 2026-04-09 composite-inversion thesis. Marginal-negative →
retirement per doctrine.

**JA4 (SPY-wk put-regime blend) → RETIRE** (`_JA4_SPY_WK_WEIGHT=0.0` →
`_compute_put_regime_mult` returns None → standard regime mult).
Lag-fixed A/B: wash at ≤25 (admits 45.0% z=+0.95 / removals 43.6% z=+0.92 —
both sides above shared baseline = no selective value); mildly negative at ≤30
(admits 39.6% vs shared 41.0%, z=−2.48). Puts are OFF in every v70 profile;
the put-assessment surface does not clearly benefit. Also removes a live
sim/production divergence (the simulator never had JA4).

**MCD (mcap dampener) → RETIRE** (`MCD_ENABLED=False`).
The decisive finding: with point-in-time mcap (F4), the 8.2pp monotonic
mcap↔TP ladder **collapses to 2.6pp, z=+2.61** (below the W1 z≥3 bar) and goes
non-monotonic (micro 51.2% > small 49.8%; mid 52.8% > large 51.7%; N=15,389
cohort). The original gradient was substantially survivorship: stocks that
GREW into large caps escaped historical dampening. MCD's removals are
near-baseline quality (N=1,449 @ 53.0% vs shared 55.0%, z=−1.16) — it was
consuming ~42% of 75+ N to filter noise. Retirement = the largest honest
N-recovery available (75-79: 1,580→2,643 +67%; 80-84: 269→632 +135%) with no
statistically-real per-bucket WR cost (W4 clean: worst bucket z=+0.61).
Recalibration was considered and rejected: you don't recalibrate a z=2.61
signal (W1 pre-flight fails).

**Sector Market Wave → RETIRE formally** (`SECTOR_BREADTH_WAVE_ENABLED=False`).
It has been inert in EVERY stored row set (v60/v69/v70 — all recalced after the
source CSV vanished; Q1 confirmed zero `sector_breadth_wave` weight_info keys
on deep-stress dates). The honest A/B on the rebuilt source: the wave removes
ABOVE-baseline call winners (571 removals @ 56.4% vs shared 54.4%, −28% N at
75+) — exactly the breadth-crash-artifact trap (MWDD lesson: crash cohorts are
mean-reversion winners). Put side: removes at-baseline puts (41.7 vs 41.6).
Retirement makes the config tell the truth; the new loud guard prevents the
silent-inert failure class from recurring.

W6 gradient: preserved on the fixed arm (calls 90-94 67.6 ≥ 85-89 55.2 ≈ 80-84
55.4 ≥ 75-79 54.4 ≥ 70-74 51.4; puts monotonic).

Adversarial verification (3-agent workflow): **0 blockers**; warns were
by-design fail-softs (documented).

## Ship decision

**v71 = F2 + F4 leak fixes (unconditional) + four mechanism retirements**
(mis_stress, JA4, MCD, wave) + F1/F3 loud guards + regression tests. One
bundled scoring ship per the handoff; `ALGORITHM_VERSION` bump + full recalc
required (Score.overall changes). A `bundle` arm (= exact ship state) was run
to validate the combined surface before elevation — see below.

### Bundle arm (exact v71 ship state) vs legacy (= stored v70), optWR15

| bucket | legacy N / WR | bundle N / WR | read |
|---|---|---|---|
| 95+ | 6 / 83.3 | 8 / 75.0 | tiny-N noise |
| 90-94 | 37 / 67.6 | 44 / 68.2 | +19% N, WR up |
| 85-89 | 136 / 55.9 | 132 / 54.5 | flat (z≈−0.2) |
| 80-84 | 292 / 55.1 | **592 / 53.4** | **+103% N**, −1.7pp = z≈−0.5 noise |
| 75-79 | 1,710 / 54.0 | **2,479 / 54.0** | **+45% N at FLAT WR** |
| 70-74 | 13,359 / 51.5 | 11,546 / 51.2 | −14% (promoted upward) |
| puts ≤30 | — | — | unchanged (40.6→40.9 on 26-30) |

**75+ tradable supply +49% (2,181 → 3,255) at flat honest WR** — quality
promoted upward out of 70-74, not diluted downward. W4 noise-aware: no real
regression anywhere. For the supply-starved Apex HOLD book (hydration was the
June headline win), this is the substantive payoff of the audit beyond
correctness.

NOTE the v71 recalc will additionally carry the continuation echo on the full
rebuilt barrier cache (the simulator arms exclude it equally on all sides), so
stored v71 rows will differ slightly from the bundle arm — in the cont-echo
direction only.
