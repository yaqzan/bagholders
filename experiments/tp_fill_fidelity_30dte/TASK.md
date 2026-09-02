# TP-Fill Fidelity, 30-DTE — engine TP declarations vs real flat-file contract prints

Cold-boot doc. A fresh agent resumes from this file + PREREG.md + LESSONS.md alone.
Bulk results live in out/ and logs/ — never read raw logs into context.

## Goal

Measure the ABSOLUTE fidelity of monte_carlo.py's intrabar TP barrier fills for the
30-DTE engine — the assumption the 2026-08-10 tpsl_refine audit showed carries ~all
portfolio EV (close-confirmed TP collapses incumbent AND candidates to -80% / 100%
collapse on 22-now). Only the 15-day assessment barrier had a real-contract fidelity
read (2026-07-25, ~3.2pp optimism); the MC's own TP declaration never has. Three
questions, per the dispatch:

1. On days the engine's daily-bar walk declares a TP touch (TP+15% / TP+30% premium
   levels), did real option prints actually trade at/above the modeled barrier
   premium that day?
2. What is the effective fill-shortfall distribution (real achievable vs modeled
   barrier-level fill)?
3. Per-liquidity-tier breakdown (FF-3' map tiers, analogous to FF-4's SL
   gap-overshoot table).

Output: per-tier TP-fill optimism table + a calibration recommendation for the three
inert monte_carlo.py fill knobs — `TP_FILL_MISS_P` (mc:422), `TP_FILL_AT_BARRIER`
(mc:102), `TP_FILL_GAP_AWARE` (mc:105).

## Consumers

- **experiments/tpsl_refine_2026_08 Phase D §6 probe** — its locked prereg uses an
  arbitrary `TP_FILL_MISS_P=0.10`; this measurement grades that number (tightening-only
  amendment possible if measured miss > 0.10; their prereg cannot be loosened).
- **Every MC absolute claim** — capital-plan gates (G1-G4), Core/Apex certs, the
  in-flight TP/SL ship decision. This hardens the absolute layer; relative rankings
  were already shown differential-neutral to the fill assumption.
- December PL-5 (fill-realism ledger) + the standing-MC-realism candidate registry.

## Assets (all pre-built, read-only; no S3, no fresh vendor pulls)

| Asset | Path | What |
|---|---|---|
| Signals | `.cache/flatfile_exploitation/signals_v74_2022_2026.parquet` | 4,936 v74 overall>=75 signals, 2022-08-01..2026-07-31, close_unadj |
| Real contracts | `B:\polygon_derived\ledger_v2\ledger.parquet` + `paths\year=*` | 4,403 kept nearest-ATM ~30-DTE calls + 71,278 traded contract-day OHLC rows |
| Underlying OHLC | `.cache/flatfile_exploitation/underlying_ohlc_2022_2026.parquet` | engine-convention bars (verify schema; fallback = queued bulk MySQL pull) |
| Liquidity tiers | `B:\polygon_derived\liquidity_map\signal_liquidity.parquet` + FF-4 `bindings.json` | FF-3' option_volume_30d map, FF-4 tier edges t1..t5 |
| Engine | `monte_carlo.py` compute_trade_outcome + realized_vol (imported, never edited) | the oracle for its own TP declarations |
| Patch helpers | `experiments/tpsl_refine_2026_08/driver/mc_patch.py` | proven set_tpsl() re-derivation chain |

## Design (detail locked in PREREG.md)

- **P1 — declarations:** import monte_carlo under production env pins; per ledger
  signal call the engine's own compute_trade_outcome on engine-convention underlying
  bars; two arms: (TP0.30, SL-0.70) incumbent + (TP0.15, SL-0.90) leading candidate.
  Dump per-signal declaration parquet.
- **P2 — join + measure:** kind=='tp' events joined to real contract path rows on the
  touch calendar date; fill/miss/no-print classification, shortfall + overshoot
  distributions, gap-open share, late-fill/never-fill within the 27-cal-day hold.
- **P3 — adjudicate:** orchestrator (Fable) reads out/ tables only, writes
  FINDINGS.md + knob recommendation, propagates pointers (tpsl_refine LESSONS,
  FF TRACKER related-items row, known-issues if warranted).

## Hard rules

- Vendor/derived data stays on B:\ / .cache — never into MySQL.
- Full runs via `trader queue submit` (high, --db light unless the MySQL fallback
  pull fires, then that step is --db heavy --window off_market). Smoke (N=25) may run
  foreground (seconds).
- No production file edits; all engine variation via in-process patch (mc_patch).
- PREREG.md is LOCKED before any outcome is viewed. Metric/cell changes after
  outcomes = new prereg, honestly labeled.
- Token economy: Sonnet builder implements from BRIEF_BUILDER.md; Fable adjudicates.

## Status (overwrite at every stopping point)

**2026-08-10 ~08:15 ET — COMPLETE. Adjudicated in FINDINGS.md; knobs stay
DEFAULT-OFF.** Full run = queue #347 (smoke-audited before go; 29 selftests;
coverage 99.5%; A1 integrity 0/5,822). Headline: same-day fill 48.9% at TP30 /
no-print 17% / economic never-fill 15.8% (late fills median 4 cal days at the same
limit price) / default fill-price credit ~1.5x real / tiers monotone t1 20.4% ->
t4 7.8% never-fill. Calibration: TP_FILL_MISS_P ~= 0.15 economic (0.50 mechanical
bound), GAP_AWARE semantics. Touch-day median 1.3519 reproduces Link-1's 1.352.
Propagated: tpsl_refine LESSONS (Phase D probe graded), FF TRACKER related-items,
memory. Follow-ups (not started, gated): FF-4 minute overlap RTH cross-check (C3);
Stage-3 MC A/B if a default flip is ever proposed.
