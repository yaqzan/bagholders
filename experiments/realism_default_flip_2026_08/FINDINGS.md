# FINDINGS — MC-realism default flip (calibrated fills become the shop default)

**STATUS: COMPLETE 2026-08-12. R2 CLEAN → R4 SHIPPED (defaults flipped in monte_carlo.py).
R3 NULL (gross 0.50 confirmed). R1 banked as the official two-lens baseline, including
Sentinel's first calibrated read — a calibrated-POSITIVE discovery held at UNVERIFIED
pending its guard battery.** Prereg `5a9b4949`; amendments `e379a4ca` (population/windows),
`023334b6` (identity gate → same-run determinism + drift band), `b686a0db` (substrate
investigation resolved). Queue #485-490; evidence `out/flipR_{identity_r8,twin_r8_*,r1,r3}.csv`.

## What shipped (R4)

`TP_FILL_MISS_P` default 0.0 → **0.15**; `TP_FILL_GAP_AWARE` default off → **ON**
(both = the measured values from `experiments/tp_fill_fidelity_30dte/`). Canon lens =
`0 / 0`, quote-only-when-labeled. Verified: import-check defaults engaged; canon escape
hatch works; drift-guard suite green (655 constants). Docs re-anchored: monte-carlo.md
doctrine banner, assessment-backtest.md Stage-3 lens note, known-issues CURRENT SHIP
STATE (escalation CLOSED), CLAUDE.md labeling sentence, capital-plan bullet. Scope
boundary held: Stage-1/2 measurement (WR15, barrier cache, D1 double-touch) untouched;
portfolio params untouched; paper-ledger engine unaffected.

## Gates (all green before R4)

- Twin cells (same-run determinism): bit-exact, 28/28 fields, N=500.
- Substrate fingerprint: per-window ≥70 loaded counts flat across every battery
  (19,261 / 25,703 / 42,607 etc.), zero tainted cells; close-boundary guard flat.
- Cross-day drift band vs Aug-10 references: Core within band everywhere (max 0.48pp).
  **Apex 22-now worst_dd −1.29pp (outside the 1.0pp band) — ACCEPTED WITH DOCUMENTATION,
  ruling recorded pre-R1-results:** (1) same-run twins bit-exact, (2) fingerprints
  stable, (3) favorable direction, (4) worst_dd is a tail-max statistic over a candidate
  set that legitimately gained 12 MNST signals, (5) the band was calibrated on Core-only
  drift. Consequence: future cross-day bands should be per-profile and wider for
  tail statistics.
- R2 ranking-stability: no TP10-era ordering inverts under calibrated reading —
  Core TP10 > TP30 base and > TP20 alternates (margins 5-16pp), Apex TP10/SL−60 >
  alternates (collapse-dominance), Apex n12×6% > n10×10% (~12pp) — vs observed
  cross-substrate drift <1.3pp. The flip re-baselines measurement without re-litigating
  any decision.

## R1 — the official two-lens baseline (3 profiles × 12 windows × 2 lenses, N=500)

Full tables: `out/flipR_r1.csv`. Headlines:
- The two-worldview chasm at full width: Core canon median positive in 10/12 windows,
  calibrated positive in 1/12 (2024, +68.1%). Canon aggregate windows print absurdities
  (5y +139,077%) — unrealizable, per long-standing doctrine; calibrated is the lens.
- **Sentinel (85+, cascade 0.2/0.15/0/0, mp14): calibrated median POSITIVE in 5/12
  windows — 22-now +37.4% (DD 43.5), 5y +45.2% (DD 46.2), 2023 +52.6%, collapse 0
  everywhere except 10y=0 too (10y med −16.3%).** First profile-level
  calibrated-positive in the book. UNVERIFIED: N=500 single battery, no survivor arm,
  no MISS_P-0.20 buffer, no per-window trade-count floors (85+ supply is thin), and the
  S9 precedent (calibrated-positive killed by delisted-cohort dependence) sets the
  prior. Guard battery: `experiments/sentinel_guards_2026_08/`. Until it reports,
  the standing claim remains "no VERIFIED calibrated-positive config."
- Apex v6 calibrated: negative every window except 2024; DD 32-82; collapse 0 except
  10y 1.0%. Consistent with its sprint-not-hold role.

## R3 — Core gross re-read under calibrated defaults: NULL

Locked lanes (≥8/12 windows better DD AND mean ≥2.0pp; compound guard; collapse 0;
2020_crash not worse; survivor+canon guards on any passer):

| candidate | DD better | mean ΔDD | verdict |
|---|---|---|---|
| gross 0.30 / mp14 | 9/12 ✓ | **+1.95pp** | FAILS mean bar (by 0.05pp — recorded, not rounded up) |
| gross 0.40 / mp14 | 5/12 | +0.71pp | FAILS breadth |
| gross 0.40 / mp10 | 8/12 ✓ | +1.62pp | FAILS mean bar |

Gross 0.50 CONFIRMED. Mechanism note from the engagement columns: at ~9-10% average
realized deployment the gross cap binds rarely — it is a tail-only lever (and realized
deployment is even non-monotonic in the cap on 22-now; 2018/2020/2023 non-monotonic at
the middle point). The g0.30 crash-window gains (2020: −10.1pp DD) are real but paid in
harvest/aggregate compound (2024 68→33; 5y −29→−40) — the same trade the locked lanes
exist to price. No guard arms run (nothing passed). The exposure axis under calibrated
defaults is now MEASURED and closed at current sizing; revisit only with new data or a
changed book.

## Also banked en route (cross-referenced)

- The identity-gate saga: MNST split-correction re-backfill (+4/+2 substrate drift,
  closed row-level vs the Aug-2 weekly dump; backup-side closes exactly 2.0× live —
  the Jul-29 scoring ran on pre-split-recognition prices; today's values are MORE
  correct). Traps banked: mutable score history; replace-semantics blinds timestamp
  forensics; per-window fingerprints required.
- Backup outage (Aug 8-11) found+fixed (hidden_run.vbs cwd anchor); 03:00 scheduled run
  = end-to-end verification. Ops follow-ups: option_prices dump duration vs the 04:00
  update window (collision brewing as the table grows), heartbeat backup-staleness
  alert, hermes user-PATH cleanup, scores-in-dailies design review, NTFS stale-size
  lesson for redirected files.
- PCH artifact-vs-engine coverage asymmetry (ledger includes engine-unwalkable symbols).

## Disposition

- Calibrated is the default lens everywhere; canon quotes must be labeled.
- Sentinel guard battery decides whether the one positive survives scrutiny; its result
  updates capital-plan language either way (current plan text already carries the
  guarded version).
- capital_plan_refresh tool: R1's CSV is not yet in its evidence-source schema (profile/
  lens columns vs tp_sl/cell_name matchers) — extend the harvester to read
  `flipR_r1.csv` so Sentinel's NO-CALIBRATED-READ flag clears mechanically (follow-up).
