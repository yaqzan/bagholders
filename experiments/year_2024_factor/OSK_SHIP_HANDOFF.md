# OSK — direct option-skew "buy-the-cheap-call" entry filter — STAGED (not shipped)

**Date:** 2026-06-09 (overnight /research). **Status: STAGED — confirmed real edge, data-blocked from the gate.**
Continuation of the 2024-IT-factor deep dive (`FINDINGS.md`) + the iv_skew lead (`experiments/iv_skew/`).
No code shipped, no `ALGORITHM_VERSION` bump. This is the "Stage > rush" outcome: the edge is genuine and
orthogonal to everything shipped, but it **cannot pass the standard Stage-3 COVID-inclusive gate** for
structural reasons (premium-dominated + option-data-locked), so it is documented + specced, not forced.

---

## Why this was the target

The 2024-factor deep dive showed the strategy's entire 10y edge is catching the rare **low-momentum-crash**
year; the off-years (2021/2023/2025) bleed because the 75+ leaders reverse into −90% dead-hold-expiry bags.
The realizable lever is a **persist-vs-crash discriminator** on the 75+ call cohort. The shipped SVR
(`semivol_r` band-pass) is the 10y-computable piece. The open question: is there a STRONGER discriminator?

## The finding — direct option-skew is a CONFIRMED residual to SVR (on the TRADABLE cohort)

`skew = put_iv(10%-OTM) − call_iv(10%-OTM)` from `option_prices` (point-in-time, same-day chain).
Mechanism (timeless): low/negative skew = call-euphoria/squeeze (you overpay for a crowded call → it
reverts); high put-skew = call is cheap/un-crowded → better outcomes. "Buy the cheap leg."

Evidence on the **75+ tradable cohort**, covered window 2025-02-13 → 2026-05-15, N=430, actual fwd option P&L
(`experiments/year_2024_factor/skew_residual.py`, `skew_winrate.py`, `skew_robust.py`):

| test | result |
|---|---|
| opt_skew → win, univariate | **t=+3.22** |
| **opt_skew \| semivol_r (the shipped SVR feature)** | **t=+3.16** while **semivol_r\|opt_skew collapses to t=+0.15** |
| opt_skew \| overall (price score) | t=+3.21 — orthogonal to the technical score |
| opt_skew \| stock_r20,r60 | t=+3.52 — NOT a recent-return/oversold proxy; strongest in down-r20 tercile (t=+3.07) |
| sign-stability by quarter | every quarter +0.85 … +1.88, **never flips** (the test that killed `iv_rv`) |
| win-rate by opt_skew quintile | **36% → 59%** (23pp spread); drop-bottom-50% lifts 75+ WR 46.7→52.8% (keep 50%) |

**Key correction to the lore:** the shipped SVR was branded "the skew bridge," but `semivol_r` is a *weak
proxy* for the real option-skew edge (semivol_r|opt_skew t=+0.15). SVR works for its OWN reason (a
directional downside-vol-asymmetry signal, apex15 z=+11.57), which only correlates +0.14 with opt_skew. So
the direct option-skew edge is **genuinely unshipped**, not a re-discovery of SVR.

## Why it CANNOT ship via the standard gate (the structural blockers)

1. **Premium-dominated → standard MC is BLIND.** The edge is in what you PAY (implied vol), not the
   underlying move: opt_skew → underlying barrier is only z≈+1.8 (weak), → actual option P&L is t=+4.0. The
   realized-vol MC (`monte_carlo.py`) prices premium from realized vol, so it literally cannot see most of
   this. (This is exactly why the directional-signal hunt kept landing at ~47% and this one hit.)
2. **Option-data-locked (~1.3y).** `option_prices` coverage starts 2025-02-10. There is NO option skew for
   2024/2022/2021/COVID — so the Stage-3 **collapse=0 on every window incl. 2020-COVID** floor (T6) is
   unreachable. You cannot evaluate the mechanism on the windows the gate requires.
3. **The only covered window is net-negative.** 2025-02 → 2026-04 is selloff-heavy; the 75+ book is −22%
   to −24% either way. Premium-aware sim (`isolate_osk_vs_svr.py`): OSK on top of SVR improves per-trade
   +0.6 to +1.5pp but portfolio ret/DD is flat-to-−1.2pp here. It "makes a losing book less-losing" — you
   cannot demonstrate a *winner* on 1.3y of one regime, and you can't test the regime where it'd shine.

These are **structural, not effort** — no amount of overnight compute unblocks a premium signal on 1.3y.

## The mechanism design (drop-in when unblocked) — OSK = SVR-clone keyed on opt_skew

Identical shape/wiring to the shipped SVR (`experiments/apex_speed_v70/SVR_SHIP_HANDOFF.md`), swapping the
feature `semivol_r` → `opt_skew`. Calls-only, portfolio-stage (entry filter, score frozen):

- **Feature (live):** `opt_skew(sym, date) = put_iv(10%-OTM) − call_iv(10%-OTM)` from the current chain
  (live-computable going forward; `experiments/iv_skew/build_iv.py` is the batch extractor). Per-signal,
  stamp onto `TradeOutcome.opt_skew` (the G18 pattern SVR used — travels with the outcome, sidesteps the
  two-`run_backtest` gotcha). Null (pre-2025-02 / illiquid chain) → scale 1.0 (no-op).
- **Scale (band-pass / cheap-side cut):** the data favors a **one-sided cut of the euphoric (low-skew)
  tail** rather than SVR's two-sided band — the high-skew side is monotonically good (Q4 59%). Candidate:
  `OSK_LO_CUT` (skew below which → `OSK_FLOOR`), `OSK_LO_FULL` (full weight above), linear between; no
  high-side cut. Tune `OSK_FLOOR∈[0.5,0]`, cut depth on the covered window (drop-bottom-33%→50% region).
- **Consumers (13, when shipping):** mirror SVR exactly — `strategy_config` (both DTEs; 15 disabled),
  `mechanism_registry`, `monte_carlo`/`backtest_cascade` (`_osk_scale` + outcome stamp), `trader.py`
  (_cmd_backtest inline + _cmd_alloc), `api.py` (/simulate inline + /backtest cfg), `Backtest.js`,
  drift-guard pairs. **Do NOT commit this until the validation path below exists** (un-gateable code rots).

## The ship-path (what actually unblocks it) — for the next agent / future-me

1. **DATA (the gating dependency):** wait for `option_prices` to span a fuller cycle — critically a
   non-selloff / bull stretch so the covered window isn't 100% drawdown. Re-pull + rebuild the iv ledger
   (`experiments/iv_skew/build_iv.py`) and re-run `skew_winrate.py` when coverage ≥ ~2.5-3y. The per-trade
   edge is already confirmed; the missing piece is a *representative* portfolio window.
2. **VALIDATOR (the tooling, build once, reused by lead A too):** a **premium-aware covered-window cascade
   backtest** — real Apex day-by-day mechanics (concurrent positions, cash, dead-hold) but with entry
   premium + forward exit P&L priced from the ACTUAL `option_prices` chain instead of the closed-form
   realized-vol model. v0 probes exist (`premium_sim.py`, `isolate_osk_vs_svr.py`, monthly-compound). v1 =
   faithful day-by-day. This is the SAME IV-aware infrastructure as NEW_LEADS lead A (historical-IV reprice
   of forced exits) — build it once, it unblocks both.
3. **GATE adaptation (framework tension to resolve with the user):** the Stage-3 T1-T7 gate REQUIRES
   collapse=0 on COVID/2022 — impossible for an option-implied signal that doesn't exist pre-2025. Option-
   implied mechanisms need a **separate interim validation regime**: covered-window premium-aware DD/return
   + per-trade WR sign-stability, with the explicit caveat that it is NOT the full COVID gate. Decide this
   before shipping any option-pricing mechanism (affects lead A too).

## Bottom line

Direct option-skew is the **first confirmed per-trade edge orthogonal to BOTH the price score AND the
shipped portfolio levers** — the genuine "make off-years behave more like 2024" discriminator the 2024
deep-dive pointed to. It is **not shippable tonight (or by any overnight run) for structural reasons**:
premium-dominated (MC-blind), option-data-locked (1.3y), covered window net-negative. The shipped SVR is a
weak 10y proxy, not this. The path is data depth + a premium-aware validator + a framework decision on how
option-implied signals are gated. **Staged, not forced.**

## Artifacts
`experiments/year_2024_factor/{skew_residual,skew_winrate,skew_robust,isolate_osk_vs_svr}.py` ·
`experiments/iv_skew/` (build/recon/premium_sim + `.cache/iv_skew/*.parquet`) ·
ledger `.cache/iv_skew/proxy_ledger.parquet` (opt_skew + semivol_r + fwd option P&L, 10y where computable).
