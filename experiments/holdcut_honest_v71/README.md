# HOLD vs CUT — honest-theta re-test on the v71 substrate

**Status:** harness staged 2026-06-10. **NOT yet run** (needs the compute box — MySQL + queue).
**Gate owner:** Stage-3 portfolio (T1–T7), no `ALGORITHM_VERSION` bump.

## Why this exists

`HOLD ≫ CUT for calls` is the **foundational axiom of the Apex architecture** (wide SL −0.70,
ride to the day-N hard-sell, dead-hold). It was decided in `experiments/v69_portfolio_retune/`
on the **theta-optimistic, trading-bar** MC engine.

The 2026-06-09 calendar-hold + honest-theta standardization (`experiments/calendar_hold/FINDINGS.md`)
showed that engine **under-charged theta ~16pp on slow trades**, and its own conclusion (point 3)
flagged the open follow-up verbatim:

> "the HOLD edge is partly a theta-accounting artifact. **Follow-up: re-test HOLD-vs-CUT under
> honest theta** (CUT exits faster = less theta exposure → honest theta narrows, maybe flips, the
> HOLD≫CUT conclusion)."

That re-test has **never been run** — not on the honest engine, and not on **v71** (current).
v71 makes it *more* live, not less: the integrity ship roughly **doubled 75+ supply**, so a
CUT/fast-recycle strategy now has ~2× as many waiting signals to redeploy into — exactly the
dimension that could tip HOLD≫CUT toward CUT, on top of the honest-theta penalty.

## Substrate (must be v71-honest, NOT v70)

- Scoring: **v71** `04044b21b` (active `ALGORITHM_VERSION`).
- Hold/theta: **honest calendar** — `CALENDAR_HOLD=True, HOLD_CAL_DAYS=27, NOMINAL_CAL_DTE=30`.
- Cascade: **v71-retuned** — `TIER_LOW 0.05`, overflow 0 (selectivity-on-doubled-supply ship).
- SL −0.70, dead-hold −0.40/−0.15, puts off, uncapped (`GROSS/CALL_PREMIUM_CAP 0.50`).

`c_base` carries **zero overrides** — it IS the live v71 Apex config from `strategy_config.py`.

## The HOLD↔CUT axis (existing knobs only — no engine change)

| Axis | Knob | HOLD end | CUT end |
|---|---|---|---|
| Stop width | `SL_BASE` (`SL_BASE_OV`) | −0.70 (ride losers) | −0.30 … −0.50 (cut losers) |
| Hold length | `HOLD_CAL_DAYS` (env) | 27 cal | 14 … 21 cal |
| Loss deferral | `DEAD_HOLD_ENABLED` (env) | on (collapse-preventing) | off = collapse probe only |

**Dead-hold stays ON for every shippable candidate** — `dh_off` is documented as 100% collapse
(realizing deep losses simultaneously in a crash). One `probe_dh_off` cell is included *only* to
re-confirm that collapse under honest theta; it is expected to be vetoed by the collapse floor.

The blunt SL-tighten is itself a known collapse trap (CUT-at-70+ = 100% collapse). The genuinely
new idea — a **theta-aware mid-life time-stop on the slow-bleed cohort** (exit trades >X% underwater
and *not* in dead-hold by cal-day {14,18,21}, keeping the dead-hold for crash deferral) — needs a
small `monte_carlo.py` mechanism and is **Phase 2**, gated on Phase-1 showing CUT has any life.

## Candidate matrix (Phase B)

`c_base`, `cut_sl50/40/30`, `cut_hold21/18/14`, `cut_sl40_h18`, `cut_sl30_h14`, `probe_dh_off`.

## Gate (Stage-3 T1–T7)

1. **collapse = 0 mandatory** on every (window × cell) incl `2020_crash` — hard veto.
2. **DD-primary:** rank by mean(5y, 22-now) WorstDD vs `c_base`.
3. **Honest-compound guard:** 5y MedRet ≥ 0.8× `c_base` (honest numbers, ÷~200 of the old engine).
4. Pareto (DD↓ AND compound↑) preferred. Phase B N=100×6 → C N=300×8 → D N=500×10.

**Decision:** if no CUT cell beats `c_base` on DD without breaking the compound guard, HOLD≫CUT is
**re-confirmed on the honest v71 substrate** (close the follow-up, keep current config). If a CUT
cell Pareto-wins, escalate to Phase C/D and the Phase-2 theta-time-stop.

## Run (on the compute box)

```bash
# Phase B (N=100 x 6 windows incl 2020_crash); high pri, off-market, light DB
trader queue submit --priority high --window off_market --db light --cpu 6 --restartable \
  --dedup holdcut-honest-v71-B --reason "HOLD-vs-CUT honest-theta re-test (v71 substrate)" \
  -- python experiments/holdcut_honest_v71/sweep.py --phase B --workers 6
trader queue wait <id> --timeout 3h    # run with run_in_background=true to be alerted

python experiments/holdcut_honest_v71/sweep.py --report B
```

Raw: `.cache/holdcut_honest_v71/phase_B.jsonl`; child logs in `experiments/v69_portfolio_retune/_childlogs/`.
