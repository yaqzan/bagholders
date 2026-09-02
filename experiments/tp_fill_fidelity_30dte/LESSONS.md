# LESSONS — append-only

- 2026-08-10 (recon): the archive is LOCAL (B:\) and the real side of this
  measurement never touches raw flat files — FF-1's ledger v2 `paths\year=*`
  already extracted per-contract traded-day OHLC for exactly the signal set we
  need. S3/entitlement traps are moot here; T5/T6 (traded-days-only, extended
  hours) still apply because paths inherit day_aggs semantics.
- 2026-08-10 (recon): engine TP declaration = intrabar `high >= tp_level` on
  ADJUSTED underlying bars (mc:2494), fill = Uniform(tp_level, high) mapped
  through option_pnl_pct (mc:3006-3007, minus theta at fill). The three inert
  knobs decompose it exactly: MISS_P (touch without fill), AT_BARRIER (kill
  overshoot credit), GAP_AWARE (credit gap-open only). The measurement was
  designed knob-first so calibration is mechanical (PREREG §5).
- 2026-08-10 (recon): `compute_trade_outcome` is pure given bars — no
  _prepare_window, no DB, no RNG needed for declarations. The entire pipeline is
  three parquets + an engine import; minutes of compute. Queued anyway (repo
  canon: if in doubt, queue).
- 2026-08-10 (design): TP15 arm runs at SL−0.90 (the leading tpsl_refine Phase-A
  candidate cell), not SL−0.70, so the fidelity numbers attach to the exact cell
  the live decision cares about. SL choice only shifts the TP-event population via
  SL-first preemption.
- 2026-08-10 (design): economic vs mechanical miss — a same-day miss that fills
  later at the resting limit exits at the SAME premium; the true loss event is
  never-fill-by-deadline. Both rates are prereg'd (§5); knob calibration reads the
  economic one, pessimism probes read the mechanical bound. Decided BEFORE
  outcomes, recorded here so a future reader knows it wasn't chosen to flatter.
- 2026-08-10 (builder gate): a prereg that lists INPUT FILES but never column-audits
  them can still be blocked by one missing column — BOTH locked inputs lacked an
  `open` (underlying parquet: ff_signals.py never SELECTed it; ledger paths:
  PATH_COLUMNS chosen in the close-only-touch era). Next prereg: enumerate
  metric -> column -> file for every metric BEFORE locking. Resolved pre-outcome via
  PREREG §9 A1 (fallback pull #342 + FF-0 contract_day_index as the contract-open
  source).
- 2026-08-10 (builder selftest catch): checking walk_end before expiry made the
  "contract expired before T" unjoinable reason dead code (walk_end = min(expiry,
  +45d) is always <= expiry). Selftest-first discipline caught a classifier bug
  before any real row was processed.
- 2026-08-10 (ops): `--window off_market` gates on being OUTSIDE
  TRADER_QUEUE_MARKET_OPEN..CLOSE — pre-market counts as off-market; the docs phrase
  "defer until after close" describes the submitted-during-hours case, not the gate.
  A pre-open submission runs immediately (subject to db slots).
- 2026-08-10 (adjudication): a sanity band must NAME ITS POPULATION. Tripwire 6.2
  said "median high_mult on touch days"; the runner computed it on FILLS ONLY —
  truncated >=1.30 by construction — and manufactured a FAIL (1.511). The honest
  unconditional statistic is 1.3519 = PASS, and a dead-on reproduce of FF-1
  Link-1's 1.352 from an independent pipeline. Next prereg: write every tripwire
  as statistic + population + N, not a phrase.
- 2026-08-10 (adjudication): registered priors were WRONG in two places, honestly
  logged: same-day miss measured 51% (registered 15-40%) and gap-open gamma 46.5%
  (registered "low single digits" — anchored on FF-4's 3.6% UNDERLYING SL-gap
  incidence, a different object; contract-side first-print gaps are driven by
  overnight premium moves + sparse printing). The economic never-fill (15.8%)
  is what saved the engine's honor, exactly via the §3-registered late-fill
  mechanism (median 4 cal days late, p90 15).
