# Phase Reallocation — NULL RESULT

## Question

When the slot pool (MAX_POSITIONS=14) is full and a new high-conviction signal arrives, should we displace an existing held position to admit the new one? If so, which position should we close, and at what conviction-edge threshold?

## Mechanism (now fully implemented in `monte_carlo.py`)

The `REALLOC_STRATEGY` env var was previously a placeholder with no implementation. This phase implemented it:

- `Position` class extended with 3 fields: `entry_idx`, `entry_underlying`, `premium_pct` (always populated; needed for mark-to-model)
- `close_by_sym_idx[(sym_id, day_idx)]` lookup built once per window when `REALLOC_STRATEGY!=''`
- Plumbed through pool initargs to MP workers
- `_mark_position()` helper: closed-form `option_pricing.option_pnl_pct` at today's close (vega=1.0)
- `_try_realloc(side, new_score)`: same-side displacement with strategy-based target picker

**5 strategies tested:**
- `entry_score_low`: displace lowest-conviction held same-side
- `current_pnl_high`: lock in highest realized gain
- `current_pnl_low`: cut deepest current loser
- `days_held_high`: recycle longest-held (most theta drag)
- `pnl_high_or_score_low`: hybrid — lock gains>+20%, fall back to lowest score

**Knobs:** `REALLOC_MIN_ADVANTAGE` (score-edge gate), `REALLOC_MIN_HOLD_BARS`, `REALLOC_MAX_PER_DAY`. All env-gated; default OFF (`REALLOC_STRATEGY=''`).

## Bug fixed during implementation

Initial Stage 1 N=80 produced byte-identical results to baseline across all 6 variants. Root cause: `_mc_init_worker` and `_mc_iter_worker` weren't passing `close_by_sym_idx` to MP workers, so workers always had `close_by_sym_idx=None`, mark-to-model returned None, and every realloc attempt failed silently. Fixed by plumbing through pool initargs (commit deferred — code currently in working tree).

After fix, realloc fires correctly under MP (verified via debug count at ~5-20 successful displacements per simulation year).

## Stage 1 (N=80, 22-now + 5y, all 5 strategies)

After the MP fix, real signal emerged. `R_pnl_high` and `R_score_low` showed marginal positives; the other 3 strategies showed clear compound regressions. Promoted top 2 to Stage 2.

## Stage 2 (N=150, 22-now + 5y, MIN_ADV ∈ {0, 5, 10})

Tested whether higher `MIN_ADV` improved selectivity:

| Variant | 5y Δ% | 22-now Δ% | 5y ΔDD | 22-now ΔDD |
|---|---:|---:|---:|---:|
| **R_score_low_adv10** | **+11.1%** | -3.8% | **-1.3pp** | -0.3pp |
| R_score_low_adv0 | -21.0% | +6.6% | -2.2pp | -1.4pp |
| R_score_low_adv5 | -9.5% | -43% | -0.9pp | -0.5pp |
| R_pnl_high_adv0 | +1.1% | -49% | flat | -0.5pp |
| R_pnl_high_adv5 | -38% | -13% | -1.3pp | -0.5pp |
| R_pnl_high_adv10 | +1.5% | -2.2% | -1.1pp | -0.4pp |

`R_score_low_adv10` was the only candidate with positive 5y compound AND DD improvement. Promoted to Stage 3.

The `R_score_low` compound non-monotonicity across `MIN_ADV` (-21%/-9.5%/+11.1% across adv=0/5/10) was already a noise warning, but the consistent DD signal across MIN_ADV (-2.2/-0.9/-1.3pp on 5y) suggested it could survive at higher N.

## Stage 3 (N=300, 8 canonical windows) — KILL

| Window | Δ% medRet | ΔDD pp |
|---|---:|---:|
| 2021 | -3.7% | +0.0 |
| 2022 | +13.2% | +0.0 |
| 2023 | -8.8% | +0.0 |
| 2024 | -1.7% | +0.0 |
| dip | +5.7% | **+6.5pp** |
| **22-now** | **-11.3%** | +0.3 |
| 2025 | +6.8% | +0.0 |
| **5y** | **+31.6%** | +0.9 |

**P1-P6 gate result:**
- P1 (N=300+): PASS
- P2 (8 windows): PASS
- **P3 (5y AND 22-now compound ≥ baseline): FAIL** — 22-now regresses -11.3%
- P4 (no annual <-25%): PASS
- P5 (collapse=0%): PASS
- P6 (DD improvement): only 5/8 windows; 5y DD +0.9pp WORSE; dip +6.5pp WORSE

Stage 2 DD signal (-1.3pp on 5y) reversed at N=300 to +0.9pp. Stage 2 was within MC noise floor.

## Diagnosis

1. **Per-trade quality is invariant** under realloc — CTr/PTr essentially identical across baseline and candidate (e.g., 5y: 3646.7 vs 3647.0). Realloc only changes WHICH positions get carried to natural exit, not per-trade math.

2. **Mark-to-model exit ≈ natural exit on average** — when realloc closes a position via mark, the realized P&L is similar to what natural exit would produce, so net cash flow per trade differs by small amounts.

3. **Slot competition cost = displacement gain** in the strategies tested — a displaced 75-79 call may have eventually TP'd; the new 80+ call may TP later or differently. At v32's existing scoring quality (call TP ~58%, put TP ~46%), the conviction-edge swap is roughly neutral on average, with high noise in any given path.

4. **Stage 2 → Stage 3 reversal pattern matches Phase OP1** — the N=150 4-window-style "win" was 22-now-biased noise that didn't transfer to 8 windows. Per memory: "lock decisions on 5y; treat 22-now as confirmation only" works the OTHER way too — 5y can win on noise, the 22-now confirmation step catches it.

## Decision

**Kill Phase 2.** No realloc strategy ships at v32.

## Implementation status (left in working tree, gated OFF by default)

- `Position` has `entry_idx`, `entry_underlying`, `premium_pct` fields (small overhead; useful general-purpose metadata)
- `close_by_sym_idx` is built lazily only when `REALLOC_STRATEGY!=''`
- `_try_realloc` is a no-op when `REALLOC_STRATEGY=''` (production default)
- All env-gated, follows the existing pattern of research knobs in `monte_carlo.py` (e.g., `MAX_PUTS_PER_DAY`, `PUT_TIGHTEN_BREADTH_LE`, `WEAK_WEEKLY_PUT_DROP`)

No production behavior change. The mechanism is available for any future revisit.

## What was NOT tested

- **Cross-side realloc** (e.g., new put displaces low-conviction call) — same-side only here. Cross-side adds conviction comparability complexity.
- **Trim 50% instead of full close** — partial-close mechanism not implemented.
- **Regime-conditional realloc** — only fire in stress/bull tape; could capture asymmetric DD pattern.
- **Slot-occupancy-weighted picks** — combine multiple metrics (e.g., `0.5 × pnl_norm + 0.5 × score_norm`).

## What NOT to retry without justification

- **Static REALLOC_STRATEGY in {entry_score_low, current_pnl_high, current_pnl_low, days_held_high, pnl_high_or_score_low}** at any tested MIN_ADV (0/5/10). Comprehensively falsified at v32 + bounded-fill MC.
- **N=150 4-window evaluation as a ship gate** — Stage 2 → Stage 3 reversal repeats the Phase OP1 lesson. Future portfolio retunes need N≥300 × 8 windows.

## Files

- Mechanism: `monte_carlo.py` (Position class lines ~1199-1232; `_try_realloc` + `_mark_position` lines ~1340-1410; close_by_sym_idx build lines ~1893-1907; MP plumbing in `_mc_init_worker`/`_mc_iter_worker`)
- Stage 1 harness: `experiments/bayes_phase_realloc/stage1_smoke.py`
- Stage 2 harness: `experiments/bayes_phase_realloc/stage2_minadv.py`
- Stage 3 harness: `experiments/bayes_phase_realloc/stage3_validate.py`
- Logs: `experiments/bayes_phase_realloc/logs/`
