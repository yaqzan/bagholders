# Phase Call/Put Split — NULL RESULT

## Question

Does pre-reserving call/put slots (vs current calls-first shared-14 pool) improve portfolio outcomes?

## Stage 1 (only stage run, killed before Stage 2/3)

**Setup:** N=80 × 7 variants × 2 windows (22-now, 5y). Algorithm version v32 (`43eecea`). All other strategy params at production. Bounded-fill option-pricing-aware MC.

**Variants tested:**

| Name | `MAX_POSITIONS_CALL` | `MAX_POSITIONS_PUT` | Effect |
|---|---|---|---|
| B (baseline) | None | None | Current production: calls-first, shared 14 |
| V1_RP2 | 12 | None | Reserves 2 put slots |
| V2_RP4 | 10 | None | Reserves 4 put slots |
| V3_RP6 | 8 | None | Reserves 6 put slots |
| V4_5050 | 7 | None | Forces 50/50 split |
| V5_PutCap4 | None | 4 | Caps puts at 4 (free for calls) |
| V6_PutCap2 | None | 2 | Caps puts at 2 |

## Results

| Variant | 22-now Δ% medRet | 5y Δ% medRet | 22-now ΔDD | 5y ΔDD |
|---|---:|---:|---:|---:|
| V1_RP2 | +27.0% | **-71.7%** | -0.0pp | +0.1pp |
| V2_RP4 | -30.4% | **-76.0%** | -0.5pp | +0.0pp |
| V3_RP6 | +12.0% | **-81.3%** | -0.7pp | -0.3pp |
| V4_5050 | -57.6% | **-93.5%** | -0.4pp | -0.2pp |
| V5_PutCap4 | **-94.4%** | **-92.5%** | -6.0pp | -4.9pp |
| V6_PutCap2 | **-99.8%** | **-99.8%** | +0.7pp | -9.1pp |

**Per-trade quality** identical across variants (Call TP 59.3-59.7%, Put TP 45.1-46.3%). Reservation only changes slot-mix, not per-trade math.

## Diagnosis

The static reservation hypothesis is structurally falsified.

1. **"Force more puts" (V1-V4):** 5y compound collapses 71-94% with DD essentially unchanged (±0.7pp). Mechanism: calls drive compounding via faster cycling and higher per-trade TP rate; forcing more puts at the cost of calls reduces capital velocity without commensurate DD reduction.

2. **"Cap puts" (V5/V6):** Real DD signal (-5 to -9pp on 5y) but at 90-99% compound loss. Same trade-off shape as the prior put-DD investigation null results (Phases 1C, 4, 6, 7b). Cutting puts reduces correlated DD but eliminates the put-side hedge contribution.

3. **No Pareto-improvement candidate exists** in the static reservation space.

## Decision rule applied

Per `.claude/docs/known-issues.md` "Lock decisions on 5y; treat 22-now as confirmation":

- Both windows agree directionally for V2-V6 (compound loss).
- V1 and V3 had positive 22-now but heavy 5y losses → 22-now-biased noise per Phase OP1 lesson.
- DD improvements only on V5/V6 with catastrophic compound cost.

**No Stage 2 / Stage 3 promotion warranted.** Static reservation does not improve on the production calls-first shared-14 pool.

## What was NOT tested (open follow-up if revisited)

- **Regime-conditional reservation:** caps shift based on `breadth_score` (e.g., reserve 4 puts in stress, cap puts at 4 in bull). Adjacent mechanism not tested here. Would require code changes to make caps regime-aware (not env-static). Given prior null history of put-cut levers in any conditional form, low priority.

## Files

- Harness: `experiments/bayes_phase_callput_split/stage1_smoke.py`
- Logs: `experiments/bayes_phase_callput_split/logs/stage1_n80.jsonl`, `stage1_n80_console.log`

## What NOT to retry

- Static call/put pool reservation in any combination of `(MAX_POSITIONS_CALL, MAX_POSITIONS_PUT)` at v32 + bounded-fill MC.
