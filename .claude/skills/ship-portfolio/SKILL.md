---
name: ship-portfolio
description: Ship a portfolio-stage change (Stage 2 or Stage 3) end-to-end — TP/SL/cascade tiers/MaxPos/DD bands/dampener knobs (RXDD/SVR/MWDD/TVDD/BDIV/SPREAD_TILT)/profiles/hold semantics — with NO ALGORITHM_VERSION bump. Covers the mechanism_registry precondition, the 13-consumer engine-wiring checklist, N=500 T1-T7 evidence, temporal-refresh + research-pack propagation, live-Portfolio parity, and docs. Use when a Stage-3 (or Stage-2 barrier) hypothesis has cleared its gate and you are editing strategy_config.py / portfolio_profiles.json to ship it, or the user says "ship this portfolio change", "add a new DD lever", or "edit a profile".
---

# /ship-portfolio — ship a portfolio-stage change (no version bump)

Elevates a gated Stage-2 (TP/SL/HOLD/PREMIUM_MULT/BREADTH_THRESHOLD) or Stage-3
(F3F, MaxPos, cascade alloc, DD soft-band, dampener knobs, profiles, hold
semantics) change into `strategy_config.py` / `algorithm_versions/portfolio_profiles.json`.
**This path never touches `ALGORITHM_VERSION` and never runs `trader recalculate`**
— `Score.overall` is untouched, so per-bucket assess WR/TP% is invariant. If your
change modifies `Score.overall` (formula/dampener/weight/lift/gate) you're on the
**Stage 1** path instead — stop and use [/ship-version](../ship-version/SKILL.md).
Decision rule: [deploy.md](../../docs/deploy.md) "Decision: which stage applies?".

Precondition: gate evidence is complete per [/ship-gates](../ship-gates/SKILL.md)
— Stage 2 B1-B5 or Stage 3 T1-T7 at the required N, evaluated on a **frozen**
scoring stack (T1/B1: Stage 1 must not have moved under you mid-sweep). If
evidence is incomplete, stop and go sweep/gate first. For live-tracker
day-to-day ops (sync/reset/status/pending/notify, the Allocator, execution
timing) see [/portfolio-ops](../portfolio-ops/SKILL.md) instead — this skill is
the one-time ship, that one is the everyday surface.

## GUARDS

1. **Never chain Stage 1 -> 2 -> 3 in one ship.** Each stage optimizes on a
   frozen output of the prior stage; ship one at a time
   ([deploy.md](../../docs/deploy.md)).
2. **No ALGORITHM_VERSION bump, no `trader recalculate`, ever, for this path.**
   If you find yourself editing `database/utils/scoring.py` or bumping the
   version file mid-portfolio-ship, you've drifted onto Stage 1 — stop.
3. **Step 0 for a NEW mechanism is `mechanism_registry.REGISTRY` — BEFORE
   touching any engine file.** Both DTE statuses (`enabled`/`disabled`/`n/a`);
   `disabled` needs a non-empty `reason` AND a `wiring_mode`
   (`not_wired` = engine must have ZERO trace of the config fields, e.g. SAW;
   `wired_neutral` = engine wires the fields at a no-op value, e.g.
   `DD_SOFT_CALL_FLOOR=1.0`). Run `python tests/test_mechanism_registry.py`
   before editing engines — the pre-commit hook refuses commits that fail
   drift-guard OR the registry test; never `--no-verify` around a mechanism
   ship. See `mechanism_registry.py`'s own module docstring for the two
   wiring-mode semantics in full.
4. **Drift-guard checks VALUES, not WIRING.** A mechanism can pass
   `test_strategy_config_drift.py` while still being invisible in
   `trader.py`'s deterministic backtest or the frontend. The registry test
   (guard 3) closes the *engine*-wiring gap; the 13-consumer checklist below
   still has to be walked by hand for CLI/UI/docs.
5. **Every portfolio change needs a research-pack rebuild — no exceptions.**
   Skipping it is the exact gap that left VersionCompare's Apex/Core stale +
   RXDD-inert for weeks (2026-06-04) and silently dropped the 70-74 overflow
   band from the Backtest page. `trader temporal-refresh` refreshes a
   *different* surface (the Assessment calendar/monthly tabs) — you need
   BOTH, not one instead of the other.
6. **Lock decisions on 5y; 22-now is confirmation only.** 22-now-only wins
   reverse at 5y (documented repeatedly — Phase OP1, the realloc Stage-2→3
   reversal). Never use N=150 4-window MC as a ship gate; N=300 single-window
   swings 1.6-1.8x on compound — screen at N=300, ship at N=500.
7. **Profile-specific knobs live in `portfolio_profiles.json`, shared knobs
   live only in `strategy_config.py`.** Check which is true for your knob
   BEFORE editing — a shared knob edited only in one profile's JSON override
   silently diverges from the other profiles; a profile-specific knob edited
   only in `strategy_config.py` silently applies to every profile.
8. **A new DTE/TP/SL profile override needs `_derived_engine_pricing`, not a
   naive TP/SL swap.** Since `3a585c2ed` (2026-06-22), profiles can override
   `nominal_cal_dte` / `hold_cal_days` / `tp_base` / `tp_stress` / `sl_base` /
   `sl_stress` via `PROFILE_OPTION_ATTRS` in `portfolio_profiles.py` — but the
   premium and sigma barriers must scale honestly with `sqrt(DTE/30)`
   (`_derived_engine_pricing` does this). A profile that sets `tp_base`/
   `sl_base` WITHOUT going through this path will apply the wrong premium
   multiple for its DTE. Verify with `python -c "import portfolio_profiles as pp; print(pp.profile_config_overrides('<key>'))"`
   and confirm `premium_mult`/`tp_sigma_base`/`sl_sigma_base` are present when
   the profile carries a DTE or TP/SL override.
9. **A shared-default edit LEAKS into every profile that doesn't pin that knob —
   audit inheritance BEFORE editing `strategy_config.py`.** Concrete 2026-08-10 case:
   Apex's profile params pinned `sl_base` but NOT `tp_base`, so changing
   `STRATEGY_30DTE.TP_BASE` would have silently changed the LIVE Apex ledger's TP
   before Apex's own gate evidence existed. For each profile, diff its params block
   against the knobs you're changing; pin explicitly (with its own evidence) or the
   default change ships to it implicitly. Inverse of guard 7's divergence trap.
10. **The live Portfolio tracker's `.profile` is a LOOKUP, not a fact you know
   from memory.** `known-issues.md`'s CURRENT SHIP STATE header (last
   "verified 2026-06-17") is not the same thing as what's actually running —
   run `trader portfolio status` (prints `Run: <name> (<profile-key>)`) or
   `GET /api/portfolio/state` before describing "the live profile" in any
   ship note. See "Which profile is actually live" below — the two sources
   have disagreed before.

## Step 1 — Confirm the stage and pin the baseline

```bash
python trader.py algorithm active                 # confirm scoring HEAD == what your gate evidence was measured against
python tests/test_strategy_config_drift.py         # ~1s — must be clean BEFORE you start editing (baseline sanity)
```

If drift-guard is already failing, something is uncommitted/wrong upstream of
your change — fix that first, don't stack your edit on a dirty baseline.

## Step 2 — Edit the single source of truth

`strategy_config.py` is canonical for shared params (`STRATEGY_30DTE` /
`STRATEGY_15DTE`, both `DteStrategyConfig` dataclasses + nested
`OptionStrategyConfig`). `algorithm_versions/portfolio_profiles.json` carries
**per-profile overrides** on top of that base — a profile only needs an entry
for the params it actually overrides; everything else inherits from
`strategy_config`. The override maps live in `portfolio_profiles.py`:
`PROFILE_CONFIG_KEYS` (exposure/sizing), `PROFILE_TIER_KEYS` (cascade tier
alloc), `PROFILE_STRATEGY_ATTRS` (incl. `nominal_cal_dte`/`hold_cal_days`),
`PROFILE_OPTION_ATTRS` (`tp_base`/`tp_stress`/`sl_base`/`sl_stress`, guard 8).

**Adding a NEW mechanism** (RXDD/SVR/MWDD/TVDD/BDIV/SPREAD_TILT are the shipped
precedent — read one entry in known-issues.md CURRENT SHIP STATE for the exact
shape): Step 0 is `mechanism_registry.py` (guard 3), THEN the field on
`DteStrategyConfig`/`OptionStrategyConfig` for both `STRATEGY_30DTE` and
`STRATEGY_15DTE` (15 DTE gets `disabled` + reason if you're not validating it
there — every shipped DD lever above is 30-DTE-only, 15 DTE `not_wired`).
Comment the field with ship date + headline metric.

**Editing an EXISTING knob's value:** just change the value in
`strategy_config.py` (+ the profile JSON if it's profile-specific); no registry
edit needed unless you're changing the mechanism's enabled/disabled shape.

## Step 3 — Drift-guard + registry

```bash
python tests/test_strategy_config_drift.py     # ~1s. Current tally as of 2026-07: "653 strategy constants
                                                # (146 mc + 123 bc + 82 mc15 + 71 bc15 + 231 scoring) + 145 schema-parity
                                                # + 15 source-code scans + 24490 symbol-surface scans" -- read the
                                                # printed tally yourself, don't hardcode this number in a ship note.
python tests/test_mechanism_registry.py        # <1s -- only if you touched the registry
python experiments/_dte_audit/audit.py         # ~5s -- structural drift between engines and tracking infra
```

If a **new** mechanism, wire it into the 13 consumers below BEFORE these will
pass cleanly (the registry test fails loudly naming the gap).

## Step 4 — The 13-consumer checklist (new mechanism only)

Editing an *existing* knob's value skips straight to Step 5 — you only need
this table when adding a mechanism the engines don't yet know about. Verbatim
from [deploy.md](../../docs/deploy.md) "Adding a NEW Portfolio Mechanism":

| # | File | What to update |
|---|------|---------------|
| 0 | `mechanism_registry.py` | **PRECONDITION** (guard 3) |
| 1 | `strategy_config.py` | Field on `DteStrategyConfig`/`OptionStrategyConfig`; set on both `STRATEGY_30DTE` and `STRATEGY_15DTE` |
| 2 | `monte_carlo.py` | Module-level `NAME = float(os.environ.get('NAME', str(_cfg.NAME)))`; apply in `_try_fill_call`/`_try_fill_put`/resolve loop; add to `main()` print |
| 3 | `monte_carlo_15dte.py` | Same (or wire disabled if not validated for 15 DTE) |
| 4 | `backtest_cascade.py` | Module-level constants + apply in the deterministic entry loop (mirror the MC mechanism) |
| 5 | `backtest_cascade_15dte.py` | Same |
| 6 | `api.py` | Pull from `_cfg`; accept query-param override in `/api/backtest/run`; apply in entry loop; expose in `params` payload |
| 7 | `trader.py` `_cmd_backtest` | Pull from `_scfg_f3f`; apply in entry loop; add to summary print block |
| 8 | `trader.py` `_cmd_alloc` | Pull from `_alloc_cfg`; add a display line in the GUIDELINE section |
| 9 | `tests/test_strategy_config_drift.py` | Add to `pairs_mc`/`pairs_bc`/`pairs_mc15`/`pairs_bc15` per the registry's wiring declarations |
| 10 | `src/pages/Backtest.js` | `DEFAULT_ADVANCED`, `buildAdvancedFromConfig`, `FIELD_TIPS` |
| 11 | `.claude/docs/known-issues.md` | CURRENT SHIP STATE + CLOSED-SHIPPED timeline + WHAT NOT TO DO if a null was ruled out along the way |
| 12 | `.claude/docs/trading-strategy.md` | New mechanism section + authoritative snapshot |
| 13 | `.claude/docs/version-history.md` | New section: mechanism description + N=500 validation table + commit reference |

Smoke after wiring:
```bash
python tests/test_strategy_config_drift.py
python tests/test_mechanism_registry.py
PYTHONIOENCODING=utf-8 python trader.py alloc 50000
PYTHONIOENCODING=utf-8 python trader.py backtest --from 2025-01-01 --capital 50000 2>&1 | grep -iE "your_field"
```

Also do the **schema-driven manifest wiring** if the knob should be
UI-editable: `portfolio_param_manifest.py` needs either a `Param` (surfaces it
in the Backtest advanced-params UI + `/api/backtest/run` overrides) or an
`EXCLUDED` entry with a reason — `tests/test_portfolio_param_manifest.py` FAILS
until classified. For an *editable-per-run* (not just display-only) knob,
`run_backtest` must read it via `cfg.get('your_key', MODULE_DEFAULT)` and
`your_key` must be added to `ENGINE_CFG_KEYS` (`test_engine_cfg_keys_in_sync`
enforces the pair). The Backtest UI default and the Allocator both auto-update
from the live config/profile — no manual frontend edit needed for an existing
knob's value change, only for a brand-new one.

## Step 5 — Post-ship audit (drift-guard blind spots)

Drift-guard catches numeric mismatches in `monte_carlo.py`/`backtest_cascade.py`/
`api.py`. It does **not** catch display strings, docstrings, tooltips, or
fallback constants baked into formatted text:

```bash
PYTHONIOENCODING=utf-8 python trader.py alloc 50000 2>&1 | grep -E "Call exits|Put exits|F3f|DD soft|cascade|Hard sell"
PYTHONIOENCODING=utf-8 python trader.py tp                          # if TP changed
PYTHONIOENCODING=utf-8 python trader.py stop                        # if SL changed
PYTHONIOENCODING=utf-8 python trader.py backtest --help 2>&1 | grep -iE "default|widens|breadth|tp|sl"
grep -nE "DEFAULT_ADVANCED" src/pages/Backtest.js | head -3
grep -nE "breadth score <= [0-9]+|TP \\$\{" src/pages/Backtest.js | head -10
# Dashboard MarketPanel -- visual check on load (fetches /api/strategy/config)
```

Known stale-string blind spots (verbatim file list from
[deploy.md](../../docs/deploy.md)): `trader.py` `_cmd_tp_stop` / `_cmd_alloc` /
`_cmd_backtest` docstrings; `src/pages/Backtest.js` `DEFAULT_ADVANCED` +
`getFieldTips()`/`FIELD_TIPS`; `api.py` parameter docstrings. Pattern: prefer
runtime-formatted strings (`f"...{opt.TP_STRESS}..."`) over baked literals.

## Step 6 — Propagate (research pack + temporal + parity)

**These are two DIFFERENT surfaces — do both, every portfolio ship (guard 5):**

```bash
# 1. VersionCompare / research-pack stress windows (Mar-2020, 2020-2021, 2020-now, 22-now)
trader queue submit --priority high --db heavy --cpu 6 --restartable \
  --dedup "research_pack_<vNN>" --reason "portfolio ship: rebuild VersionCompare windows" \
  -- python tools/build_research_pack.py --version <vNN> --profiles all --run-portfolio-windows

# 2. Assessment calendar/monthly tabs (a SEPARATE surface from #1)
PYTHONIOENCODING=utf-8 python trader.py temporal-refresh --profiles all
#   (also accepts --dte 15|30|both, default both; --profile is an alias for --profiles)
```

`<vNN>` is the *scoring* version currently active (unchanged by this ship —
you're rebuilding the pack under the SAME version, just with your new
portfolio params baked into its portfolio-window computation). Command #1
covers profiles only, DTE=30 by default — pass `--dte 15` as a second
invocation if your change is 15-DTE-relevant. Command #2 (`temporal-refresh`)
loops both DTEs by default.

**If you touched sizing math (cascade alloc, DD soft-band, dampener scales,
premium/DTE pricing) and the live Portfolio tracker consumes the same
mechanism** — RXDD/SVR/MWDD/TVDD/BDIV all mirror into
`portfolio_engine._open_entries_for_day` bit-exact vs `backtest_cascade.py` —
re-validate parity:

```bash
PYTHONIOENCODING=utf-8 python experiments/portfolio_engine_parity/validate.py [END_ISO]
```

Read-only, fresh stateless replay (`_persist` monkeypatched to capture, nothing
written); tolerances are cents-level (premium rounds to cents at open —
`<=$0.005`, equity accumulation `$1.00`, pnl_pct exact `1e-9`). A silent
mismatch here means your mechanism shipped in `backtest_cascade.py` but was
never mirrored into `portfolio_engine.py` — the live tracker would silently
diverge from what you validated.

## Step 7 — N-floor sanity (Stage 3 only, ~5 sec)

```bash
PYTHONIOENCODING=utf-8 python -u experiments/n_floor_v46/check_signals.py
```

Stage 3 doesn't touch `Score.overall`, so offered counts per tier are
invariant — output should read **SAFE on every tier**. A non-SAFE result here
means something OTHER than your intended change moved (investigate before
declaring shipped). The static v46-era floor table itself is
**report-only since 2026-06-11** (not a veto) — re-run
`experiments/n_floor_v46/run.py` + `reaggregate.py` only if this ship touches
MaxPos / cascade alloc / DD-soft band / F3F floors / dead-hold / SAW U-curve
(mechanics the floor is calibrated against).

## Step 8 — Live Portfolio auto re-qualification (expect this, don't fight it)

`portfolio_engine.py` fingerprints the active `version_id` + the live profile's
config (`_strategy_fingerprint`). The FIRST `trader update` after your
portfolio edit lands runs a re-qualification **sweep** automatically: each open
position's *entry-date* score is re-looked-up under the (unchanged) scoring
version but the NEW portfolio config; positions that no longer clear
`_min_call_threshold` exit at the next session close tagged `strategy_sweep`
and fire a close notification. This is EXPECTED — no manual step required.
Realized history is frozen and never recomputed. A sizing-only mechanism (a TP/
SL tweak, a dampener scale change) is typically **qualification-neutral** — the
sweep still fires (fingerprint changed) but exits nobody, because
`_min_call_threshold` membership didn't move. If you shipped something that
DOES move the threshold (e.g. widening F3F or changing MaxPos), expect real
exits and say so in the ship note.

## Step 9 — Docs + restart + commit

1. `.claude/docs/known-issues.md` CURRENT SHIP STATE (new dated block, revert
   line) + CLOSED-SHIPPED timeline; add to WHAT NOT TO DO if a null was ruled
   out en route.
2. `.claude/docs/trading-strategy.md` authoritative snapshot (it explicitly
   defers to `strategy_config.py` + `portfolio_profiles.json` as canonical —
   keep the prose in sync anyway, it's what a human reads first).
3. `.claude/docs/version-history.md` new dated section (mechanism + N=500
   table + commit ref).
4. Restart the API so `/api/strategy/config` serves the new values —
   **backgrounded, or it hangs the agent shell:**
   ```powershell
   # PowerShell tool, run_in_background: true (Bash cmd.exe silently no-ops):
   & C:\Development\server.bat restart -Service trader-api
   ```
   Then verify `GET http://127.0.0.1:5000/health` -> 200.
5. Commit + push. No `ALGORITHM_VERSION` change, no recalc, in this diff.
6. Run `python tools/capital_plan_refresh.py`, review the VERDICT DELTA banner, and update `.claude/docs/capital-plan-2026.md` if it fires (after any portfolio-profile ship).

## Which profile is actually live — a lookup, not a fact (guard 9)

Three sources exist and have DISAGREED in this repo's own history:
`known-issues.md`'s header (dated, goes stale), `portfolio_profiles.json`'s
free-text `description`/`selection_metrics.note` fields (aspirational —
they can describe what SHOULD be live, not what IS), and the actual DB row.
Only the last one is truth. Resolve it with:

```bash
python trader.py portfolio status        # prints "Run: <name> (<profile-key>) | ..."
# or: GET /api/portfolio/state -> .profile
```

As of this writing that command reports the live run IS `apex` — even though
`portfolio_profiles.json`'s own Apex description says "NOT the default (live
ledger is Core / 30 DTE)" and `known-issues.md`'s 2026-06-17 header says
"default profile = Core." **Do not trust either doc for this fact — run the
command.** If your ship changes which profile is live (a profile-switch, not a
param edit), that's a `PortfolioRun.profile` update + `trader portfolio sync`
— see [/portfolio-ops](../portfolio-ops/SKILL.md) for the switch procedure and
qualification-neutral verification.

## Revert

Value-only change: restore the prior value(s) in `strategy_config.py` (+
profile JSON if profile-specific), re-run Steps 3-8. New mechanism: flip its
`_ENABLED` flag to `False` (every shipped DD lever — RXDD/SVR/MWDD/TVDD/BDIV/
SPREAD_TILT — is a single boolean no-op switch, byte-identical to the
mechanism never having existed) and re-run Steps 3, 6, 7. A profile-definition
change (not just a value): restore the prior profile block in
`portfolio_profiles.json`.

## Evidence / see also

- [/ship-gates](../ship-gates/SKILL.md) — the B1-B5 / T1-T7 thresholds this
  skill assumes you already cleared, plus the holdout lock and waiver ledger.
- [/ship-version](../ship-version/SKILL.md) — the sibling Stage-1 path (bumps
  `ALGORITHM_VERSION`); use it instead if `Score.overall` changed.
- [/portfolio-ops](../portfolio-ops/SKILL.md) — day-to-day live-tracker
  operations (sync/reset/status/pending/notify, profile switches, execution
  timing) once this ship has landed.
- [/run-monte-carlo](../run-monte-carlo/SKILL.md) — how to actually run the
  N=500x8 T1-T7 sweep this skill's precondition assumes is done.
- [deploy.md](../../docs/deploy.md) — full prose version of Steps 2-6 plus the
  site-wide propagation table for editing an existing knob vs adding a new one.
- [trading-strategy.md](../../docs/trading-strategy.md) — current shipped
  params (header is known one-ship-stale re: active scoring version; trust
  `strategy_config.py` over the doc's tables).
- `mechanism_registry.py` module docstring — the two `wiring_mode` semantics
  (`not_wired` vs `wired_neutral`) in full, with the SAW/DD_SOFT_BAND examples.
- Pre-v69 WR/DD numbers anywhere in `version-history-archive.md` or
  `known-issues-archive.md` are look-ahead-inflated (~12pp) — never cite them
  as a Stage-3 baseline; use post-v69 (2026-05-31+) evidence only.

## Self-update

If you hit a trap this skill missed, append it to GUARDS here AND to
`.claude/docs/traps.md` in the same session.
