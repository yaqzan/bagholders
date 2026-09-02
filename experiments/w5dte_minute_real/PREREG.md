# PREREG -- w5dte_minute_real (minute-level realizability of W5DTE TP fills)

Status: LOCKED 2026-08-18 before any minute bar was read. Parent: experiments/w5dte_ev/
(EV PASS on daily-bar fills) and experiments/weekly_5dte_movers/. Owner directive:
"run the minute-realizability study" (2026-08-18, in chat; continuation item #1 in
experiments/w5dte_ev/OWNER_SPEC.md).

## Question

The EV study credited a TP-L fill whenever a later DAILY high reached L x entry_close.
A resting sell-limit only truly fills if the contract actually TRADED at/above that
level with enough prints/volume. Using the minute tape (owned through 2026-08-05,
covering the whole study window): (a) what fraction of claimed fills are realizable at
graded strictness, (b) does the rule's EV survive after gating fills on realizability,
and (c) are rule-hit touches systematically LESS realizable than exposure-matched
control touches (the rule selects violent contracts -- spike risk is directional)?

## Substrate + population

- Minute bars: `B:\polygon_flatfiles\us_options_opra\minute_aggs_v1\YYYY\MM\*.csv.gz`
  via `ff_common.read_flatfile('minute_aggs_v1', date)` / `list_session_dates`
  (schema pinned in ff_common.TIER_SCHEMAS; builder introspects one file first).
- Events: REUSE experiments/w5dte_ev/ev_study.py verbatim (`load_population`,
  `add_rule_masks`, `add_pricing`, `prepare_control_base`/`draw_one` with the same
  SEED_BASE) -- no reimplementation of masks or fills. Arms:
  (i) FAMILY hits with daily TP-5x fill (~11.2k events; TP-10x fills are a subset,
  measured in the same pass), (ii) CONTROL = draws 0, 1, 2 of the EV study's own
  seeded control (same rows the EV study priced), restricted to their daily TP-5x
  fills (~5k events/draw). Holdout: all events expire <= 2026-06-12 by construction;
  assert anyway.

## Measurement (per fill event, level P_L = L x entry_close, L in {5, 10})

Over ALL sessions strictly after entry_date through expiry_day (a resting limit works
the whole window), from that contract's minute bars:
- `mins_at_above` = count of minute bars with high >= P_L
- `vol_at_above` = total volume in those bars  (bar-level; a bar's volume may include
  sub-level prints -- stated as an UPPER-bound convention, symmetric across arms)
- `first_touch_minute` (time of day), `max_run` (longest consecutive-minute run),
  `n_touch_days` (distinct sessions with a touch)
- R0 sanity: >= 1 touching minute bar exists (daily-vs-minute consistency; expected
  ~99%+; misses counted as data-quality, excluded from denominators and reported).

## Realizability tiers (pinned; clip framing follows the G3(b) 1/5/20 precedent)

- **R1 (1-lot realistic):** mins_at_above >= 2 OR vol_at_above >= 5
- **R2 (small-clip, PRIMARY):** mins_at_above >= 5 AND vol_at_above >= 10
- **R3 (20-lot strong):** mins_at_above >= 15 AND vol_at_above >= 50

## Adjudication (pinned before results)

- PRIMARY metric: FAMILY TP-5x **fill-validity rate at R2**, with the arm contrast
  (FAMILY vs each control draw's R2 rate).
- EV re-read: recompute the EV study's TP-5x and TP-10x policy EVs with fills gated
  on R1 and on R2 (gated-out fills fall back to the expiry leg exactly as the EV
  engine prices no-fills). Same gating applied to the 3 control draws.
- **SURVIVES** iff FAMILY TP-5x EV under R2 gating stays > 0 AND exceeds all 3
  R2-gated control-draw EVs. (3 draws = a directional screen, not the 100-draw gate;
  stated as such. A SURVIVES here does not upgrade the EV PASS -- it removes the
  named caveat. A FAIL forces a dated realism-haircut amendment on w5dte_ev
  FINDINGS + a review flag on the paper tape for the owner -- the tape is NOT killed
  autonomously.)
- Secondary reads (reported, non-gating): validity by year and by rule R1..R6;
  TP-10x validity; first-touch time-of-day distribution (open-auction spike share);
  lone-print share (mins_at_above == 1); FAMILY-vs-control validity gap.

## Compute plan

One streaming pass: build the needed (session -> tickers) map from the events (all
arms unioned), then read each needed minute file ONCE filtered to its tickers,
emitting per-event-day aggregates; reduce to per-event metrics. ~1,000 files x ~18 MB
gz. Queued `--db light --cpu 12` (pure B: reads; market-hours safe). Builder smokes on
2 named weeks with hand-checks before the full pass; outputs under
`B:\polygon_derived\weekly_5dte_movers\minute_real\` + RESULTS.md here. ASCII, seeds
pinned, explicit Python311 path in the queue command.

## Falsification

Failure looks like: R2 validity collapses (touches are ghosts) or the R2-gated FAMILY
EV drops under the gated controls -- either is a publishable realism haircut that
changes how the tape's forward evidence must be read. Evaluated immediately after the
queued run; no peeking mid-build.
