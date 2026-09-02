---
name: research
description: Autonomous, time- and date-aware overnight alpha-research run that aims to SHIP and VALIDATE a new/improved scoring version OR portfolio mechanism by the next market open — weekend/holiday aware (markets closed Sat/Sun + US holidays = more runway). Use when the user invokes /research, or asks for an overnight / unattended research-and-ship run, or "find and ship a better version by open." Orients on the market calendar + task queue, picks the highest-probability gate-aligned hypothesis, mines evidence, sweeps via the queue, validates to the ship gate (collapse-safe; DD- or WR15-primary), and ships-or-stages with a handoff. This skill SELF-HEALS: it appends new gotchas to itself.
---

# /research — overnight: find, validate, and ship a better version by market open

A self-driving research loop. Goal: by the **next market open**, have a **new improvement
shipped AND validated as "better"** — either a Stage-3 **portfolio** mechanism (no version
bump, no recalc; the usual high-probability win) or a Stage-1 **scoring** version (new
`ALGORITHM_VERSION`, needs recalc+assess). "Better" = clears the ship gate: cut 5y WorstDD with
compound flat-or-up and **collapse=0 on every window incl. 2020-COVID** (portfolio), or WR15-up
with the growth gate SHIP/FLAG (scoring).

**Read `## LIVING GOTCHAS` and `## SELF-UPDATE PROTOCOL` before starting.** They are the
hard-won frictions; this skill is meant to improve every run.

**Operating stance:** fully autonomous (the user is asleep). Queue ALL heavy compute. Keep every
change reversible. **Stage > rush** — if a correct, fully-wired, gate-validated ship won't finish
with margin before open, stage a clean candidate + handoff instead of shipping half-wired.

---

## Phase 0 — Orient & budget (do this first, every run)

1. **Clock + deadline (date/weekend/holiday aware).** Box is US/Eastern; market opens 09:30,
   closes 16:00 ET. Compute the **next market open** with the project's trading calendar (skips
   weekends AND US holidays):
   ```bash
   python -c "from database.utils.trading_calendar import is_trading_day; from datetime import date,datetime,timedelta; \
   d=date.today(); h=datetime.now().hour; start=0 if (is_trading_day(d) and h<9) else 1; \
   nxt=next(d+timedelta(n) for n in range(start,7) if is_trading_day(d+timedelta(n))); \
   print('next market open:', nxt, '09:30 ET')"
   ```
   (Verified working 2026-06-04. If the signature ever differs, read `database/utils/trading_calendar.py`.) **Budget =
   next_open − now.** Friday-evening/Sat/Sun → next open is Monday → ~50-60h (do the thorough/
   multi-hypothesis thing). Weeknight → ~8-14h. Map budget → plan with `## BUDGET → PLAN` below.
2. **Queue daemon health.** `trader queue status`. If `daemon: down`, start it: `trader queue daemon`
   (background) or `trader queue tick` per step. Heavy compute is USELESS if the daemon is down.
3. **State.** `trader algorithm active` (or read `ALGORITHM_VERSION`); `git rev-parse --abbrev-ref HEAD`;
   `git status --short | wc -l`. Note the active version + that the tree may have many pre-existing
   dirty files (commit only YOUR files later).
4. **TaskCreate** a short tracklist for the run (orient → context → hypothesis → evidence → implement
   → sweep → gate → ship/stage → docs).

## Phase 1 — Load context & pick the hypothesis

**Read (in this order):** `CLAUDE.md` index → `.claude/docs/known-issues.md` (CURRENT SHIP STATE,
**WHAT NOT TO DO**, **CLOSED — NULL RESULTS**) → `alpha_mining/NEW_LEADS.md` +
`MISS_CANDIDATES.md` → `.claude/docs/deploy.md` + `.claude/docs/assessment-backtest.md` (the gates)
→ scan `~/.claude/projects/.../memory/MEMORY.md` for the honest-frontier + null entries.

**Pick the path & hypothesis:**
- **The honest frontier (G14):** directional re-shaping of `Score.overall` is largely exhausted
  (opt15 WR ~47% for every price partition). The live levers are **portfolio-structural**
  (regime/VIX/DD-aware *sizing*) and **option-pricing** signals (skew/semivol). Default to a
  **Stage-3 portfolio** hypothesis unless a fresh NEW_LEADS scoring lead with a real cohort-z exists.
- **Stage-3 portfolio** (preferred; no recalc, fits any budget): mine for a **low-EV cohort that
  contributes to drawdown** and trim its sizing → the Pareto angle (DD↓, compound flat/up). This is
  the RXDD precedent (VIX-band call dampener, shipped 2026-06-04). Gate = Stage-3 T1–T7 (DD-primary).
- **Stage-1 scoring** (only if budget ≥ ~6h AND a real scoring lead): **invoke the
  `find-and-ship-alpha` skill** — it owns the Stage-1 traps (look-ahead, OPTION-TP, real-supply gate,
  growth gate). Needs `recalculate --force` + `assess --force` (heavy). Gate = W1–W6 + growth gate.
- **NEVER re-run a documented NULL** (known-issues WHAT NOT TO DO / NULL RESULTS). Examples already
  closed: regime-direction flips, put-DD cuts, MACD-gradient, per-stock score normalization,
  divergence dampeners, blunt entry_dd×breadth contraction (crash-overfit).

## Phase 2 — Evidence (mine the MC tape)

The reusable harness is `experiments/regime_dd_v70/{mine.py,sweep.py}` — clone/adapt it.

1. **Generate the trade-tape** (queue; the run ALSO prints the live per-window baseline you must beat):
   ```bash
   trader queue submit --priority high --db light --cpu 6 --restartable --timeout 2h \
     --dedup mc-tape --reason "research tape" \
     --env MC_TRADE_TAPE=1 --env N_ITER_OVERRIDE=300 --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
     -- python -u monte_carlo.py
   trader queue wait <id> --timeout 2h     # run THIS with the harness run_in_background flag
   ```
   Emits `.cache/dd_ledger/tape_*.parquet` (+ `episodes_*.parquet`, per-seed `final_value`).
2. **Mine** (calls-only for Apex): join market-context (breadth/regime/VIX) by `entry_date` from
   `MarketBreadth`/`MarketRegime`; compute cohort loser-rate **lift/z** + episode DD-concentration over
   `entry_dd × breadth × regime × VIX × concur × tier`; **and** per-window robustness. Also mine the
   *explosion* side (rank seeds by `final_value`). **WAIT FOR THE FULL TAPE before designing (G3).**

## Phase 3 — Implement env-gated + VERIFY (do not skip the verify)

1. Add the mechanism to the engine (`monte_carlo.py`) as module-level env-overridable constants that
   read `getattr(_cfg, 'X', default)` (G10), **default OFF / baseline byte-identical**. Add a smooth
   gradient helper (prefer gradient over threshold per CLAUDE.md ethos). Add the knobs to
   `experiments/v69_portfolio_retune/driver.py` `ENV_MAP`.
2. **Verify (G4):** run `sweep.py --mode verify` (OFF vs ON on 1-2 windows at N=300). Confirm **OFF
   reproduces the baseline byte-identically** (same blake2b seeds) AND **ON changes a window where it
   should fire** (and is ~no-op where it shouldn't). If OFF≠baseline, the plumbing is wrong — fix
   before sweeping.

## Phase 4 — Sweep B → C → D (queue; DD- or WR15-primary)

Clone `experiments/regime_dd_v70/sweep.py` (uses `driver.run_candidate`, env-overrides via ENV_MAP,
parses the stdout SUMMARY, deterministic paired seeds). One queued job per phase; candidates run
sequentially each at `MC_WORKERS = --cpu` (no core oversubscription):
- **Phase B** — LHS ~16 @ **N=100 × ~6 windows**, rank by DD-focus reduction (collapse=0, compound
  not down). ~30-60 min.
- **Phase C** — top-3 @ **N=300 × 8 windows incl. COVID** (`2020,2020_crash,2022,2023,2024,dip,22-now,5y`).
- **Phase D (ship-gate)** — winner @ **N=500 × 8 windows**.

Rank **DD primary, compound secondary** (compound is theoretical above ~1e10%). Pin
`ALGORITHM_VERSION_PIN=<active>`. Off-market → `--priority high`; market hours → `--priority normal`
or `--window off_market` (G1). N≥300 for reliable DD signal; N=500 for the gate.

**Seed sizing retunes with PRF (2026-06-12).** Before any Stage-3 sizing/cascade sweep (especially the
"portfolio re-tune on the new substrate" follow-up after a scoring ship), run
`python experiments/version_scorecard/portfolio_response.py --derive vNN`: F(phi) maps the version's
WR15/supply/hydration to a matched sizing config (tier ladder + overflow + threshold) — it reproduces
both hand-tuned honest-engine winners (v70 Apex, v71 c14) exactly. Use the derived config as the seeded
LHS center / first candidate; if F-derived ≈ current canon, a full retune is probably unnecessary.
F proposes, MC disposes — a derived config still needs the T1-T7 confirm before live use.

## Phase 5 — Gate

- **Stage-3 (T1–T7, assessment-backtest.md):** T4 5y WorstDD ≤ baseline +1.0pp · T5 no annual DD
  regress >5pp · **T6 collapse=0 every window incl 2020-COVID (hard floor)** · T7 compound within ±3 OOM.
- **Stage-1 (W1–W6 + growth gate, REFORMED 2026-06-11):** see `find-and-ship-alpha` +
  assessment-backtest.md "Stage 1". W2/W3 are CI-based (a contradicting window counts only at z≥2 ∧
  N≥100 — don't litigate thin-window sign flips); W6 is noise-aware FLAG-only inside the growth gate
  (95-100 pools into 90-94; only candidate-INTRODUCED z≤−2 inversions escalate); the N-floor table is
  REPORT-ONLY (W5's binding window owns droughts). Mandatory: real-supply gate, holdout lock
  (RE-LOCKED at 2026-06-15), OPTION-TP (not cumulative WR15), deterministic DD sanity after a SHIP.
  **FLAG ships** need a justification + named post-ship watch metric + downstream confirmation.
  **Scoring-neutral ships** (stability/leak-fix, the v69/v71/v72 class) use the **Stage 1-N
  neutrality track** instead (N1 zero-tradable-diff sim A/B, N2 bit-exactness, N3 value metric).
  Any gate you waive goes in the **waiver ledger** (assessment-backtest.md; 3 same-class strikes →
  fix the gate). Regression-test the growth gate with `--selftest`, never `--replay` (anchors dead).
- Pick the winner that best satisfies the user's intent (cut DD **without** cutting compound = Pareto).

## Phase 6 — Ship OR stage (the decision)

**Decision rule (G13):** can you complete a CORRECT, fully-wired, gate-validated ship with ≥~30-45 min
margin before open? **Yes → ship. No / anything marginal → stage.**

**SHIP — Stage-3 portfolio (13-consumer wiring, G11; mirror DD_SOFT_BAND end-to-end):**
1. `mechanism_registry.py` (Step 0) — `MechanismSpec`, declare BOTH DTE statuses + `wiring_mode` +
   non-blank `reason` for the disabled side.
2. `strategy_config.py` — add fields to `DteStrategyConfig`, set on **STRATEGY_30DTE** (enabled) AND
   **STRATEGY_15DTE** (disabled — no-default dataclass, G8).
3. `monte_carlo.py` — flip module reads to `getattr(_cfg, ...)`.
4. `backtest_cascade.py` — module consts + helper; thread any per-signal feature (e.g. vix) into
   `run_backtest` like `regime_map`; **handle BOTH** the main call AND `compute_and_store_temporal`'s
   per-month vacuum call (G9); `cfg.get(...)` so `/api/backtest/run` can override.
5. `trader.py` `_cmd_backtest` (own inline loop) + `_cmd_alloc` (display); `api.py` `/api/backtest/run`
   (cfg keys) + `/api/trader/simulate` (own inline loop); `src/pages/Backtest.js` (4 blocks). Inline
   loops need the feature threaded separately (no run_cascade_backtest).
6. `tests/test_strategy_config_drift.py` — add pairs to `pairs_mc` + `pairs_bc`.
7. **Gate:** `python tests/test_strategy_config_drift.py && python tests/test_mechanism_registry.py &&
   python experiments/_dte_audit/audit.py` — all green.
8. Functional smoke: `trader backtest --from 2022-01-01` (exercises the inline loop) + confirm
   `trader alloc 50000` shows the new mechanism line.
9. `trader queue submit ... -- python trader.py temporal-refresh --profiles all` (calendar refresh
   + functional smoke of the deterministic engine). **No `ALGORITHM_VERSION` bump, no recalc.**
10. Commit (scoped `git add`; pre-commit hook re-runs drift+registry) + push. **Tell the user to
    RESTART the backend** (G10).

**SHIP — Stage-1 scoring:** follow `find-and-ship-alpha` end-to-end (bump → recalc in market-hours
order → assess → read the WR table → commit/push).

**STAGE (if you can't finish safely):** commit the env-gated mechanism (default OFF, baseline-identical,
tests green) + write `experiments/<exp>/SHIP_HANDOFF.md` (winner params, B/C/D evidence, the exact
remaining 13-consumer steps) + `FINDINGS.md`. Leave for the user's one-step enable.

**Always (ship or stage) update docs/memory** (per `process.md` stale-data checklist, bundled at the
END to minimize approval prompts): `known-issues.md` CURRENT SHIP STATE + SHIPPED timeline (or NULL
RESULTS), `version-history.md`, the auto-`MEMORY.md`, and `alpha_mining/NEW_LEADS.md`.

**Per-version comparability is a THREE-PART unit (2026-06-12).** After any scoring ship's recalc +
assess, a version is not comparable/gateable until ALL of: (1) research pack built
(`tools/build_research_pack.py --version vNN --run-portfolio-windows`), (2) supply/hydration row
(`python experiments/version_scorecard/signal_supply.py --versions <base>,<cand>`), (3) PRF matched
sizing (`python experiments/version_scorecard/portfolio_response.py --materialize vNN`). Missing (2)
silently runs the growth gate on APPROXIMATED supply (the documented false-SHIP trap); missing (1) or
(3) leaves VersionCompare rows null. v70-v72 all shipped without (2) until 2026-06-12 — don't repeat.

---

## BUDGET → PLAN

| Budget to open | Plan |
|---|---|
| **≥ ~24h** (Fri-eve / Sat / Sun / holiday eve) | Thorough Stage-3 (B→C→D + full ship) **and/or** a Stage-1 scoring version (recalc+assess fits). Can pursue a 2nd hypothesis. |
| **~6–14h** (typical weeknight) | One Stage-3 hypothesis, B→C→D, full ship. A Stage-1 is possible if recalc (~25min) + assess fit. |
| **~2–5h** | One focused Stage-3, compressed (B→C, D only if time). Ship-if-clean else STAGE. |
| **< ~2h** | Mine + quick screen only. **STAGE** with handoff — do not rush a ship. |

Market-hours note: if compute would run during 09:30–16:00 ET, use `--priority normal` or
`--window off_market` so it never outranks the scheduled `trader update` (tier `scheduled`).

---

## LIVING GOTCHAS  (append new ones — see SELF-UPDATE PROTOCOL)

1. **Queue ALL heavy compute (`trader queue submit`).** Never raw-background MC/recalc/assess/sweeps/
   tape — it bypasses CPU/MySQL admission and collides with the scheduled `trader update`. Bridge
   completion with `trader queue wait <id>` run under the harness `run_in_background` flag
   (re-invokes you on terminal state — that IS the overnight loop driver).
2. **Don't panic on wall-clock — check job timestamps.** `trader queue show <id>` gives started/finished.
   A job can FINISH and the elapsed wall-clock is just the user being away (a 66-min job was misread
   as 5h). Always confirm `exit=` + `finished:` before assuming a hang.
3. **Wait for the FULL tape before designing.** Crash-only / partial-window cohorts mislead: the
   `entry_dd × breadth` "lethal pocket" looked lethal on 2018/2020 partials, then DISSOLVED on the full
   6.4M-trade tape and INVERTED by regime (positive-EV in bull years). Designing on partial = overfit.
4. **The no-op verify is mandatory.** Before any sweep, confirm mechanism-OFF reproduces baseline
   byte-identically (same seeds) AND ON changes a window where it should fire. Catches plumbing bugs in
   minutes instead of after an hour of sweeping.
5. **Windows console encoding.** Set `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` on every Python invocation; a
   `Δ`/unicode char crashes cp1252 (`UnicodeEncodeError 'Δ'`). Use ASCII in print one-liners.
6. **Edit needs an in-conversation Read.** Edit-tracking ages out; re-Read the exact file region right
   before Edit if earlier reads have scrolled away (3 strategy_config edits failed "File has not been
   read yet" until re-read).
7. **`_dump_trade_tape` polars schema crash (class recurs).** `pl.DataFrame(rows, schema=names,
   orient='row')` infers dtypes from the first ~100 rows; a column None early + a string later (e.g.
   `ct`→`ct_call` past row 100, on CT-tagged configs) crashes. Use `infer_schema_length=None`. Applies
   to any tape/parquet build over heterogeneous rows.
8. **No-default dataclass parity.** `DteStrategyConfig` fields have NO defaults → a new field must be
   added to BOTH `STRATEGY_30DTE` and `STRATEGY_15DTE` (registry `_check_schema` + every instantiation
   require it). 15-DTE side is typically disabled (`not_wired` in the registry).
9. **`run_backtest` lives in TWO functions.** A feature threaded into `run_cascade_backtest`'s main call
   must ALSO be handled in `compute_and_store_temporal`'s per-month vacuum `run_backtest` call (~L2477,
   a DIFFERENT scope). Referencing a var defined only in run_cascade_backtest there = NameError that
   **silently fails the 30-DTE calendar refresh while temporal-refresh still exits 0**. Define/load it
   there too, or drop the kwargs (vacuum effect is usually negligible — single-month fresh-start rarely
   hits a DD gate). Always read the surrounding `def` before threading a `run_backtest` call.
10. **Stale long-running dashboard server.** A Flask backend that imported `strategy_config` before a
    new field existed crashes `/api/backtest/run` with `'DteStrategyConfig' object has no attribute X`
    on lazy import of `backtest_cascade` (module-level `_cfg.X`). Two fixes: (a) **use
    `getattr(_cfg, 'X', default)` for new module-level reads** so it degrades to off-until-restart
    instead of crashing; (b) **after any strategy_config field ship, tell the user to RESTART the
    backend** — the CLI/tests run on fresh processes and look fine while the server is stale.
11. **13-consumer portfolio wiring + inline loops.** Mirror DD_SOFT_BAND end-to-end (Phase 6 list).
    `trader.py _cmd_backtest` and `api.py /api/trader/simulate` have their OWN inline allocation loops
    (NOT `run_cascade_backtest`) — any per-signal feature (VIX) must be threaded into each separately.
    `/api/backtest/run` inherits via `run_cascade_backtest` (only needs cfg override keys). Gate with
    drift-guard + registry + dte-audit; the registry `not_wired` 15-DTE choice avoids touching mc15/bc15.
12. **gitnexus_detect_changes may be unavailable** (MCP not connected this session). Substitute
    `git status --short` / `git diff --cached --stat` to verify scope; commit only YOUR files (tree has
    pre-existing dirty files). Pre-commit hook runs drift-guard + registry — never `--no-verify`.
13. **Ship-vs-stage by margin, not optimism.** A rushed, half-wired, pre-open multi-engine ship is worse
    than a clean STAGE (deploy.md "silent divergence"). If not fully wired + gate-validated + tested with
    margin, stage the env-gated (OFF) candidate + SHIP_HANDOFF.
14. **The honest frontier.** Re-shaping `overall` is exhausted; mine portfolio-structural (regime/VIX/
    DD-aware sizing) or option-pricing (skew/semivol) signals. The Pareto move: find a **low-EV cohort
    that drives DD** and trim its SIZING (DD↓, compound flat/up) — blunt DD contraction is a structural
    null.
15. **Holdout lock — RE-LOCKED 2026-06-11 at cutoff `2026-06-15` (was briefly disabled
    2026-06-04, user-directed).** `strategy_config.py`'s `CALIBRATION_CUTOFF_DATE` is authoritative
    — read it directly rather than trusting a remembered value (as of this writing it is
    `"2026-06-15"`). `experiments/_holdout.py` (`assert_no_holdout_leak`, `pre_cutoff_filter`)
    enforces it in every calibration sweep; `HOLDOUT_DISABLE=1` is the explicit
    live-trading-evaluation bypass, never for calibration. First OOS re-eval ≈ 2026-12-15.
16. **Capital-velocity is the dominant law for the Apex sleeve — any mechanism that ADDS
    exposure or HOLDS positions longer tends to null or collapse.** Confirmed exhaustively
    (2026-06-05, `experiments/apex_speed_v70/`): static 100% cap collapses 10%; size+VIX+DD
    -gated hot exposure (EXR) null; trailing winners (TSL) = 100% collapse on the bulk + comp
    −6 (held winners cluster and crash together); wider stress-TP backfires; the 50% cap +
    flat TP0.30/SL−0.70 + dead-hold are near-optimal. "Close fast, redeploy" + collapse=0
    govern. Before building an exit/hold/exposure mechanism, expect to fight this — and ALWAYS
    include `2020_crash` in the screen (a Phase-B screen WITHOUT COVID hid a collapse that only
    showed at C/D; the N=500 ship-gate even downgraded a Phase-C "winner" that collapsed 0.2%
    — run the gate at N=500 with COVID AND close neighbors; the winner may sit on a boundary).
17. **Triage a user's idea against the documented-null ledger FIRST** (known-issues WHAT NOT
    TO DO / NULL RESULTS + `alpha_mining/NEW_LEADS.md` traps + MEMORY). Many "fresh" ideas are
    already closed (reallocation/displacement, sector-ETF, trailing stops, regime-direction
    flips). State the prior result + the *new condition* that would justify a retry (e.g.
    "honest-v70 changed the substrate") before spending compute — it saves hours and is the
    codebase's superpower. A read-only re-eval (e.g. `regime_ab_test.py` for the regime
    multiplier) often answers "is X still well-calibrated?" without a recalc — it auto-
    backgrounds for long windows; await the notification.
18. **Intrinsic per-signal feature → stamp on the OUTCOME, don't thread a map.** RXDD's feature
    (VIX) is a market series, so it threaded a `vix_map` into `run_backtest` (and hit G9 — the
    two-`run_backtest` vacuum-call problem). SVR's feature (`semivol_r`) is computed from the
    signal's OWN price history, so it's cleaner to compute it in the build loop and **stamp it onto
    the `TradeOutcome`** (add a field; in trader.py/api.py dict-outcomes, set `o['feat']`). It then
    travels WITH the outcome, so the fill loop reads `outcome.feat` and `compute_and_store_temporal`
    applies it consistently with ZERO extra plumbing — G9 sidestepped entirely. Cache `(closes,
    {date:idx})` per symbol so repeat signals don't rebuild. Keep the live engine computing inline
    (works for live/future dates); a static parquet cache is only for the MC sweep's speed.
19. **A user's named regime signal can be directionally INVERTED — mine EV-by-band FIRST; the shippable
    DD lever is the FLAT/topping MID-band, not the crash extreme.** MWDD (2026-06-05): "use the Market
    Wave to protect drawdown" — but the Market-Wave/breadth *crash* cohorts (low wave / −McClellan /
    high crash_echo) are mean-reversion WINNERS (contracting = crash-artifact trap, G3/RXDD Finding 1;
    the Apex edge IS buying weakness, so crash-window DD is irreducible by call contraction). The low-EV
    + DD-concentrated cohort is the *flat/topping* mid-band (McClellan≈0) — RXDD's VIX-20-28 'slow-bleed'
    GENERALIZES to any regime axis; always pair the mid-band contraction with a panic/extreme EXCLUSION
    (VIX≥28) so the crash windows stay collapse-safe. Data: `sector_etf_market_wave_score` DB col is only
    2024-07+ → rebuild full 1999+ history via `market_breadth._load_sector_etf_breadth_rows()`, or use
    full-history `MarketBreadth.mcclellan_oscillator` (breadth momentum). "Shuffle the regime weights" is
    a no-op: the regime `SIGNAL_WEIGHTS` dict is vestigial (live composite = dynamic VIX+inverted-breadth,
    trend=0) AND the regime amplifies on weak breadth (WR-reliability, not DD) — the DD lever is sizing.
20. **"Cut the loser to redeploy" (reallocation/displacement) is a hard NULL — and the dead-hold cohort's
    recovery rate is NOT flat: bags recover MOST exactly where the fresh high-score signal fires.** CDR
    (2026-06-06): user's "cut the bag down 60% for the 88 instead of riding to −70%/day-15". It's the
    documented v32 REALLOC `current_pnl_low` null; re-confirmed on honest-v70 across the full gate space
    (blunt + 11 regime-gated configs + ablations) — DD-neutral, compound sign-flips across N=100/300/500
    (= seed-noise, NOT a win), dip DD +4.5pp WORSE, collapse 0. **Mine the dead-hold OUTCOME distribution
    BEFORE building any cut/hold mechanism**: dead-held bags (option pnl ≤ trigger) split dh_pop (recover)
    vs dh_expiry (die); recovery clusters in panic-VIX / oversold / high-conviction tape (pop 0.51-0.58) —
    i.e. exactly where the "88" fires — so a cut disproportionately kills recoverers, and in a crash it's
    the `dh_off`=100%-collapse failure mode. The loser's slot also frees naturally in ~3 extra days (dead
    bags hold ~7.6d vs ~4.4d for TPs), so displacement buys almost no turnover. The reclaimable inefficiency
    (~19% capital-time in deep bags) is real but UNRECLAIMABLE by displacement (recoverers/expirers aren't
    separable at the decision point). Reinforces G16 (capital-velocity / HOLD≫CUT). Harness:
    `experiments/dead_hold_realloc_v70/{mine.py,sweep_cdr.py}`.
21. **For a NEW DD-sizing lever after N levers ship, mine the DD-ACTIVE subset (dd≥DD_MIN), not the whole
    tape — and the well is NOT necessarily dry.** TVDD shipped 2026-06-07 as the *4th* orthogonal Apex DD
    lever (after RXDD/SVR/MWDD) precisely because of this. A lever only *acts* when running dd≥DD_MIN (the
    DD-gate is what makes RXDD/MWDD Pareto — no-op in calm), so the residual it can harvest lives in the
    DD-active subset. The whole-tape mine DILUTED the signal (the TRIN neutral-band trough was mpnl +0.041
    whole-tape vs **−0.060 in the DD-active clean slice**) — I almost wrongly concluded "dry well." Add a
    `DD_FILTER` to the mine and re-run at dd≥0.13. **A Pareto lever needs low-EV AND high-dd_conc to
    COINCIDE on an orthogonal, crash-robust cohort** — after several levers most axes fail this (low-EV
    cohorts become low-dd_conc; high-dd_conc cohorts are decent-EV — the EV/dd_conc *anti-align*, which IS
    the dry-well signal). Prove orthogonality in the **all-shipped-levers-off slice** (e.g. VIX<20 AND
    |McClellan|>30 AND breadth≥40): if the new axis's low-EV cohort survives there, it's genuinely
    independent. TVDD won because neutral-TRIN (volume FLOW) is a flow-vs-breadth-momentum *divergence* that
    survives |McClellan|>30 (the "TRIN≈McClellan-redundant" prior was refuted *for that band*). The
    RXDD/MWDD inverted-U generalizes to any regime axis: mid/neutral band = low-EV DD-driver, both extremes
    = mean-reversion/momentum winners → a Gaussian bump centered in the trough auto-excludes them.
    Crash-artifact guard per-window on the *exact* candidate band (not a merged superset — merging the
    low-EV neutral band with a high-EV adjacent band muddies it). Harness: `experiments/dd_residual_v70/`.
22. **VIX-velocity is a clean monotonic EV signal but a POOR standalone DD lever (anti-aligned).** Within
    the VIX band, VIX *rising* at entry = high-EV (buy-the-dip bounce already underway), VIX *falling* =
    low-EV (post-spike complacent drift). But the low-EV (falling) side has dd_conc ~1.0 (not concentrated)
    and the high-dd_conc (rising) side is high-EV → contracting either is anti-Pareto. It is, however, a
    plausible **refinement of RXDD** (gate RXDD's level-band contraction by velocity — contract less when
    VIX is rising = the high-EV recovery). Treat as an RXDD modification, validate carefully (could regress
    RXDD), not a parallel lever. Also re-confirmed: **concurrency/book-crowding DD is CAPTURED** by
    RXDD+MWDD+DD_SOFT+MAX_POS (the old dd_ledger concur=hi 5.95× → most-crowded band now LOWEST dd_conc
    0.68) — dead as a residual lever. A-D-line-slope is McClellan-redundant.
23. **After 4 DD-sizing levers the well is DRY — the fresh-axis hunt AND the conviction-tier residual both
    null; pivot to a NEW mechanism CLASS, don't keep mining sizing levers.** 2026-06-08: screened the
    DD-active tape for a 5th axis and ruled out the whole remaining layer — **NH/NL** participation-quality
    FAILS the all-levers-off orthogonal slice (a "broad-highs = low-EV" signal that DISSOLVES in the slice
    is a regime re-label, not a residual — the decisive test, cheap and definitive); **breadth-velocity** is
    McClellan-redundant (corr +0.72, run the 2-min correlation check before building any breadth-momentum
    variant); **%above-EMA** diffuse; **VIX-velocity** anti-aligned (G22). The one genuinely-orthogonal
    residual is the per-signal **conviction tier** (the LOW 75-79 tier = 44% of DD-dollars at low EV) — but
    a DD-gated contraction of it (DQT) **NULLED at N=300**: the low tier IS the capital-velocity volume
    engine (G16) AND its low-EV-in-drawdown is **crash-artifact-coupled** (low-EV in bear drawdowns,
    HIGH-EV in bull drawdowns) — the DD-gate can't separate them (fire early→hits bull drawdowns, fire
    late→after the drawdown), so it's not Pareto-extractable. **Sub-lesson:** for a MARGINAL lever, a
    Phase-B (N=100) "Pareto" can be pure MC noise that **flips sign at N=300** (DQT's "−1.4pp 5y DD" →
    "−1.1pp WORSE") — the strong levers (RXDD/SVR/MWDD/TVDD) cleared the N=100 noise band by 5×, so a weak
    N=100 signal is a red flag, not a win. The orthogonal seams that remain are **option-pricing** (direct
    option-chain skew — top NEW_LEAD, data-blocked at 1.3y `option_prices`, needs an IV-aware MC) and
    **model-fidelity** (historical-IV reprice of forced exits), NOT another sizing lever. Harness:
    `experiments/dd_residual2_v70/`.
24. **A user's famous-anomaly idea must be (a) measured on OUR universe — it can be index-specific and
    REVERSE on our stocks — and (b) mapped to what we actually trade; for the weeks-long options sleeve an
    "entry/exit timing" idea is a backtest-REALISM question, not an arb.** 2026-06-08, the "SPY gains in
    market hours" overnight/intraday request: (1) on SPY it's ~flat, but on our 810-stock universe OVERNIGHT
    dominates (intra−over −7.04 bps/bar, −9.84 post-2020) — the OPPOSITE of the user's stat; always confirm
    the premise on our own data/era before chasing it. (2) We hold 30-DTE options for ~weeks, spanning all
    intraday+overnight segments → a buy-open/sell-close overlay is a SEPARATE equity sleeve, not a tweak;
    the only execution touchpoint is entry timing. (3) Our backtest/MC + the live Portfolio engine anchor
    entry at the SIGNAL-DATE CLOSE — but you can't buy a close you just used to decide; the earliest live
    fill is the NEXT OPEN, and bullish signals gap UP +17.5 bps overnight (t=+6.2) → a re-walk shows the
    funded apex15 WR is 73.0%@close vs 71.8%@next-open = **−1.22pp realism HAIRCUT** (the backtest is
    ~1.2pp optimistic), not an arb. Method note: this whole study needed ONE queued OHLC pull (the only
    repo price cache WITHOUT `open` is none — every existing one pulls close+high/low only, so OPEN needs a
    fresh pull) + in-memory analysis on the existing `rel_strength`/`iv_skew` barrier-agnostic ledger — NO
    MC. The rel_strength meta-lesson (every price partition → opt15 ~47%) correctly pre-called the
    directional-feature null before any compute (G17 triage works). Harness: `experiments/intraday_overnight/`.
    **Follow-up (user push "lever the overnight with SHORT-DTE 3/5/7DTE calls, day-of-week aware"):
    short-DTE to lever a small edge is a THETA TRAP — modeling real option P&L (`option_pricing.option_pnl_pct`,
    `premium_mult=1.82·√(DTE/30)`, `theta=√((DTE−τ)/DTE)−1`, τ=calendar days), the overnight-capture sleeve
    is net-negative at EVERY DTE even GROSS (30DTE-ov −4.3%, 5DTE −22.1%, 3DTE −41.8%) and MONOTONICALLY
    worse for shorter DTE (theta-per-night ∝1/√DTE grows faster than the gap-leverage); Friday/weekend is
    the worst cell (3× theta swamps the bigger weekend gap); 0/75 (DTE×exit×weekday) cells net-positive.
    Generalizable: when a user proposes shorter DTE to amplify a sub-σ edge, run the gross theta check first —
    if the edge < one night of the shortest tradeable-DTE theta, it's dead before spread, and shorter is worse.**
25. **An option-IMPLIED signal (skew/IV) can be a CONFIRMED real per-trade edge yet be STRUCTURALLY
    un-shippable by any overnight run — and the shipped semivol_r/SVR is a WEAK PROXY for it, not the edge
    itself; don't conflate the brand.** 2026-06-09 (2024-IT-factor follow-on): direct option-chain skew
    (`put_iv−call_iv`, "buy the cheap call") is a real residual on the TRADABLE 75+ cohort — opt_skew→win
    **t=+3.16 controlling for the shipped semivol_r, which collapses to t=+0.15** (corr only +0.14; SVR works
    for its OWN directional reason, NOT as the "skew bridge" it was branded). Orthogonal to score (t=+3.21) &
    return (t=+3.52), sign-stable every quarter, 23pp WR quintile spread. BUT it can't pass the gate for THREE
    structural reasons, none fixable by compute: (a) **premium-dominated → the realized-vol MC is BLIND**
    (opt_skew→underlying barrier only z≈+1.8 — the standard `monte_carlo.py` prices premium from realized vol,
    so it literally can't see the edge; this is WHY the directional hunt kept hitting ~47% and skew hit); (b)
    **option-data-locked** (`option_prices` starts 2025-02-10, ~1.3y → NO COVID/2022/2021 → the Stage-3
    collapse=0-on-every-window floor T6 is UNREACHABLE); (c) **the one covered window is net-NEGATIVE**
    (2025-selloff-heavy → you can't demonstrate a *winner* on it, and can't test the bull regime where it'd
    shine — OSK-on-top-of-SVR is +0.6-1.5pp per-trade but flat-to-−1.2pp portfolio there). The decisive cheap
    test is the **partial regression of option P&L (use WIN-rate, the reconstructed pnl is too noisy at N~400)
    on the new feature controlling for the shipped one** — it tells you in 5 min whether a "new" option signal
    is genuinely unshipped or already captured. STAGE such a lead (mechanism = SVR-clone keyed on the new
    feature, one-sided cheap-side cut; ship-path = data depth ≥~2.5-3y spanning a bull stretch + a
    **premium-aware covered-window cascade validator** (the IV-aware infra, shared with the historical-IV-
    reprice lead) + a framework decision that option-implied signals need a separate interim gate than the
    realized-vol Stage-3 COVID gate). Don't force-ship it un-gated; don't keep re-mining it overnight — it's
    blocked on `option_prices` coverage, not ideas. Harness: `experiments/year_2024_factor/{skew_residual,
    skew_winrate,skew_robust,isolate_osk_vs_svr}.py` + `OSK_SHIP_HANDOFF.md`; ledger `.cache/iv_skew/`.
26. **The off-year drawdowns are a DIFFUSE cross-sector momentum-factor reversal, NOT a sector/timing/
    crowding crash — the sector-concentration exposure cap (NEW_LEADS #12) is NULL.** 2026-06-09: user
    asked to "miss-catalog the non-2024 years and pre-empt drawdowns" via sector-correlated clustering. The
    cheap tape-mine (no new compute, `experiments/offyear_dd_catalog/`) killed it 3 ways: (a) the −90%
    `dh_expiry` bags are ~sector-distributed like the book (bag-Herfindahl ≈ book-Herf, top-sector Δ +3-6pp
    — and tech is 23% of the book so ANY bag concentration must be measured RELATIVE to the book mix, not
    absolute); (b) bags enter AND exit across 39-47 distinct weeks (worst week 6-15%) = a steady drip, not a
    synchronized burst; (c) the decisive orthogonality test — same-sector-concurrency → bag-rate
    controlling for TOTAL concurrency — **FLIPS sign by year** (+3.0/+4.7 in reversal years 2021/2023,
    −5.1/−5.6 in momentum-persistent 2024/2025; crowded-sector is the BEST cohort in 2024, bagrate 5.6%).
    A crowded sector is good when it trends and bad when it reverses = the persist-vs-crash unpredictability
    (G14/G20) wearing a sector hat; a static/DD-gated cap would HURT the 2024-type years → fails the gate.
    The book's only common factor is the 75+ momentum score itself (definitional exposure, not a
    diversifiable sub-factor); total-concurrency DD is already captured (G22/MAX_POS). **Generalizable: a
    "correlated-crash / concentration" DD idea must pass (i) concentration RELATIVE to the book baseline,
    (ii) the orthogonality test vs the already-captured total-concurrency, and (iii) sign-stability across
    reversal AND persistent years — most concentration signals fail (iii) because concentration is good in
    the trend and bad in the reversal.** Harness: `experiments/offyear_dd_catalog/{cheap_first_cut,
    sector_concur_mine}.py` + `FINDINGS.md`. NEW_LEADS #12 → tested-null.
27. **A user's ENTRY/EXIT-TIMING idea ("wait for the pullback" / "delay the entry on extended calls") →
    the cheap DECISIVE premise test is path-shape + missed-winner rate on the EXISTING barrier-agnostic
    ledger (t_up/t_dn), NOT an MC; and a delayed/retracement entry is structurally ANTI-SELECTIVE.**
    2026-06-09 (NET/RKLB/IREN "extended/late-in-run calls win via pullback-then-resweep, so wait for
    the dip"): refuted with ZERO new compute on the holdout ledger (N=4699). Winners win by DIRECT
    CONTINUATION (~88%; only ~12% dip ≥0.5σ before the TP touch, IDENTICAL in the late&extended bucket
    12.0% vs base 12.2%; run-position opt15 WR flat 47.3/47.4/48.5). The killer: **winners dip LESS than
    losers** — 38% of winners vs 69% of all signals dip 0.5σ-in-5d → dipping is a LOSER signal (losers
    dip toward the SL) → "wait for the dip" enters LOSERS and MISSES ~60% of winners (the runners: RKLB
    May calls ran +47-68%/7d). A dip can't separate resweeper-vs-runner-vs-start-of-crash. **Generalizable:
    for ANY "better entry timing" idea, build run-position (consecutive-signal cluster ordinal) +
    path-shape labels (dip-before-TP, missed-winner-rate) from the ledger FIRST — if the win-set and the
    trigger-set are negatively correlated, it's dead pre-MC.** Extends G24 (timing = realism/structural,
    not alpha) + G16 (waiting kills velocity). Harness: `experiments/retrace_entry_v70/mine.py`.
28. **A user's "VIX (or any indicator) MOMENTUM/velocity/acceleration, not just level" idea → mine the
    EXACT feature POINT-IN-TIME, because the naive weekly/rolling resample carries WITHIN-PERIOD
    LOOK-AHEAD that fakes a signal; and the honest VIX-momentum cohort is HIGH-EV (the bounce), not a
    DD lever.** 2026-06-09 (VXMD, user asked for "VIX weekly-MACD crossover as a moving velocity/accel
    reading"): the standard weekly-MACD resample (`dt.truncate("1w")` + `vix.last()`) maps a *Monday*
    signal to its week's *Friday* close = look-ahead → it inflated "rising weekly-MACD" into a fake
    low-EV cohort holding 73% of DD-dollars (a tautology: "weeks VIX rose by Friday → calls bought that
    week lost"). The FIX is an engine-faithful **point-in-time** feature: daily-equivalent weekly MACD
    = recursive EMA(60)−EMA(130), signal EMA(45) on the *daily* series (12/26/9 weeks ≈ 60/130/45
    trading days; recursive EMA uses only past → no look-ahead, one-pass in-engine), OR a strict
    *last-completed-week* resample. Re-mined honestly, the rising-VIX-momentum cohort is **HIGH-EV
    (mpnl +0.08..+0.10)** — it's where the dead-hold mean-reversion *runners* cluster (calls bought as
    VIX rises catch the bounce) — and a **crash-artifact** (bear-2022 −0.17 / bull-2024 +0.09), so the
    MC screen finds no Pareto (cutting it costs −17% compound, concentrated in bull-2024 = cutting
    winners; the DQT/G23 DD-gate-can't-separate mode). This is the **whole VIX-momentum axis** (G22/G23
    daily velocity, now weekly-MACD + acceleration) confirmed dead as a portfolio DD lever — only the
    VIX *LEVEL* band (RXDD 20-28) works. Generalizable: (a) ALWAYS mine the engine-faithful point-in-time
    feature, never a look-ahead resample (a "73% of DD-dollars" cohort that EVAPORATES point-in-time is
    the look-ahead tell); (b) a user's "X spikes → my calls collapse" pain is an OPEN-position effect that
    entry-sizing cannot fix (the dead-hold does) — don't conflate it with an entry-sizing lever. Built
    VXMD env-gated OFF as reversible null-infra. (c) The user's ACTUAL ask turned out to be a WR15
    *scoring* lever (fold the VIX weekly reading into the regime/post multiplier), not DD — ALSO null:
    read-only screen (`wr15_regime.py`, reuses `regime_ab_test`; barrier outcome is multiplier-invariant
    so walk ONCE, W1 + variant-sweep = re-aggregation, no recalc). Decisive because **the production
    composite already uses VIX level + 10d-velocity** (`_vix_gradient`), so weekly-MACD is redundant
    (orthogonal control at vix<20 z≈0 for calls) AND for calls rising-VIX is the HIGHER-WR15 state
    (suppressing it hurts, 90+ −5.72pp). Generalizable: before testing a "new regime reading into the
    multiplier" WR idea, CHECK WHAT THE COMPOSITE ALREADY INGESTS (`_vix_gradient`/`compute_regime_composite`)
    — and run the W1 split CONTROLLING FOR THE EXISTING INPUT (within-VIX-level bands here) to isolate
    orthogonal signal from the confound. Harness: `experiments/vix_weekly_v70/{mine.py,sweep.py,wr15_regime.py}`.
29. **Worktree runs: three traps that all route work to the WRONG checkout/queue.** (a) The
    `if _ROOT not in sys.path: sys.path.insert(0, _ROOT)` idiom SILENTLY SKIPS in a worktree — the global
    PYTHONPATH puts the MAIN checkout (`C:\Development\Trader`) ahead of any later worktree entry, so the
    script imports main's modules while appearing to run worktree code (symptom: AttributeError on a
    just-added attribute + edits "not taking effect"). FIX: `while _ROOT in sys.path: remove` then
    `insert(0, _ROOT)` — and print `module.__file__` in the queued log as proof. (b) `trader queue submit`
    run with cwd=worktree writes to the WORKTREE's own empty `.cache/task_queue.db` (no daemon!) — always
    submit from the MAIN checkout, point the job at the worktree with `--cwd`. (c) `--cwd C:\\path` in
    Git-Bash gets its backslashes eaten ("[WinError 267] directory name is invalid") — use FORWARD slashes.
30. **Honest mechanism A/B at full fidelity is CHEAP with sharded ReSim** (2026-06-10 v71 precedent):
    `experiments/integrity_audit_2026_06/ab_eval.py` — build ScoreSimulator per symbol-SHARD (7 shards ×
    all arms in-process via module-constant patches + try/finally restore; arms within a shard share one
    bulk-load), join (sym,date,overall) per arm to the barrier_outcomes DuckDB mirror, judge by
    delta-cohort WR (the mechanism's ADMITS/REMOVES vs the shared cohort — far more decisive than
    bucket-table diffs). Full universe × 5y × 7 arms ≈ 35 min wall on 7 cores. Validation arm (replica of
    stored rows) is mandatory: 98.4% exact match proved the harness before any verdict was trusted.
    Mechanisms keyed on a CURRENT-snapshot attribute (mcap/sector/shares) applied to history need a
    point-in-time proxy before their cohort evidence can be trusted (MCD's 8.2pp ladder → 2.6pp on PIT).
31. **NEVER edit engine/config files while a queued sweep that subprocess-imports them is running —
    and after a mechanism ships enabled, sweep baselines need an explicit `*_ENABLED=0`.** BDIV Phase D
    attempt 1 (2026-06-10) hung for its FULL 5h timeout with a 0-byte log: the ship wiring edits to
    monte_carlo.py/strategy_config.py landed mid-run; Windows MP workers re-import modules FROM DISK at
    every per-window pool creation, a worker died importing a mid-edit file state, and `pool.map` blocked
    forever (classic Windows MP: initializer/import death = silent parent hang, not a crash). Corollary:
    once strategy_config ships `X_ENABLED=True`, a bare `run_candidate({})` baseline silently runs X-ON —
    make the baseline explicit (`{'X_ENABLED': 0}`). Pre-stage wiring in a worktree or wait for the sweep;
    docs/FINDINGS edits are safe during runs, engine/config edits are not.
32. **"Omen predicts drawdown" asks → run the EPISODE-ONSET event study + the per-trade event-state EV
    with the G26/G21 kill-tests; expect the omen to be a WINNER cohort and the PRE-TOP divergence to be
    the real signal.** BDIV ship (2026-06-11): Hindenburg omen = 0/24 days preceding a major onset,
    omen-day entries mpnl +0.079 (buy-weakness winners, G19); every discrete omen flag (Zweig, churn,
    NL-spikes, summation-div) per-year sign-flips; pre-onset lookbacks show strength/complacency —
    drawdowns start at TOPS. The survivor: SPY-near-60d-high + breadth rolling over (the divergence the
    omens gesture at, WITHOUT the panic conditions that make omen-days winners). A LEADING lever may
    correctly have NO DD-gate (dd≈0 at the top — the gate would neuter it) IF a structural condition
    (SPY-near-highs cannot fire mid-crash; 2022 delta exactly 0.0) replaces the crash guard. Method:
    episodes parquet start_dates clustered across seeds = the canonical onsets; onset_frac forward event
    study; tape EV by event-state. Harness: `experiments/dd_onset_omens/{mine_onset,mine2_refine}.py`.
33. **The comparison data layer rots silently — verify pack hydration + REAL supply before gating or
    comparing, and never trust pre-v69 packs as documented history.** Found 2026-06-12: (a)
    `supply_burstiness.json` stopped at v63 — v70/v71/v72 (the entire production lineage) had NO
    hydration rows, so any growth-gate run used FALLBACK coverage (the false-SHIP trap; the gate
    prints `~` and demotes SHIP→FLAG, but only if you read it); (b) the v72 pack was 2/10 bands
    (partial assess at build time) — `tier_drift.py` shows `--` cells for partial packs; (c) the
    2026-06 honest recalcs OVERWROTE pre-v69 score rows, so old packs (e.g. v40) no longer contain
    the inflated-era values documented in FINDINGS/replay anchors — the growth gate's `--replay` is
    historical-only, regression-test with `--selftest`. Fix ritual = the three-part unit in Phase 6.
34. **Sharded-ReSim sizing + resume: a full-universe ScoreSimulator arm costs ~18 min/shard (NOT the
    ~5 min/arm the G30 "35 min wall" note implies) — budget timeout ≈ build + 20min × n_arms, and give
    the harness a skip-if-parquet-exists resume guard BEFORE the first launch.** 2026-06-12: the 8-arm
    ablation hit its 2h queue timeout ONE arm short (8 arms ≈ 2h20m); the guard (skip arm if
    `arm_{name}{SHARD_TAG}.parquet` exists) made the resume re-run only the missing shard-arms (~1h
    instead of 2h20m). Two sub-traps: (a) `trader queue wait N | tail` reports the PIPELINE's exit
    code (tail's 0) — a timeout looks like success; read the wait's OUTPUT text ("still [running]
    after timeout"), or drop the pipe; (b) the queue KILLS at timeout (exit 15) — the parent's merge
    step never ran, so merge shard parquets manually (6-line polars concat) before analyzing.
35. **A user "measure how the components work / which signs don't work combined" ask = a forecast-
    verification STUDY (deliver the insight + STAGE leads), NOT a forced ship — and the reusable harness
    is cheap (no MC, ~5 min).** 2026-06-17 (`experiments/weather_components/`): ledger the stored Score
    component columns (`trend,bb,rsi,macd,stoch,technical_alignment`) + apex join (reuse
    skill_vs_baseline's `peaks_to_swing_results(..., barrier_set="30dte_apex")`), then run 7 ensemble-
    member metrics on a 300k universe sample + the full ≥75 funded book: (1) univariate member apex-skill
    ΔEV+t, (2) corr-matrix + **effective-ensemble-size** = participation ratio of corr eigenvalues, (3)
    **pairwise tandem lift** = EV(both≥70) − max(EV A-only, EV B-only) (<0 = the "signs that don't work
    combined"), (4) **multivariate OLS** of apex_win on standardized members → **suppressor detection**
    (univar≈0/+ but conditional β strongly − = a redundant meta-feature borrowing skill, e.g. the
    "agreement" component TA: β −0.63 t −3.59), (5) reliability/resolution by band, (6) **ensemble-vs-best-
    member** (overall≥75 vs top-K-by-best-member matched count — the joint threshold can BEAT the single
    best member via consensus even when no member dominates per-trade; don't conclude "machinery worthless"
    from a ≥70-EV tie), (7) per-window stability (window EVERYTHING — the substrate's law). Two structural
    findings that recur and are worth checking on any score: the **DOMINANT score-driver (corr-with-overall)
    need NOT be the SKILLFUL member** (TREND corr +0.72 yet zero resolution + regime-harmful in bear/chop =
    the mechanistic root of the score's bear/chop weakness); and the cleanest leads are usually **Stage-1
    score-formula** (a suppressor lives in the *weighting*, not in funded sizing — its funded-book signal is
    often non-monotonic, so NO clean Stage-3 tilt) → stage, don't force an MC by a same-day open.
    **PROBE a suppressor before treating it as a v74-lean target — a univariate/multivariate suppressor
    (β<0) is NOT a removable component (2026-06-17 TA probe → tested-NULL).** "Holding the other members
    fixed, higher X → worse" (the OLS β) is a DIFFERENT operation from "remove X's weight from the score"
    (which changes which names clear the threshold + sheds X's genuine confirmation). The decisive read-
    only probe (~1h): re-score a 250-300-sym sample twice via `rescore_dump.py` (baseline env vs the
    member's weight zeroed via the existing `W_*_BASE/W_*_SLOPE` env knobs — `_envf`, read at import, so a
    subprocess-env set works with NO patch), then `apex_ev_of_parquet.py zero.parquet base.parquet`
    (ADDED/REMOVED/COMMON apex-EV + supply Δ). **Read it PER-WINDOW, not pooled** — TA zeroing looked
    pooled-accretive (+3.09<+4.08) but was per-window DILUTIVE 4/5 (Simpson, 2024-bull-weighted): supply
    −11%, dropped HIGHER-EV names than it kept. RE-CONFIRMS the reweight-null on the apex predictand. The
    probe killed a "primary lead" in ~1h = exactly why you probe before shipping.
36. **Queue ops during a multi-phase sweep (the cluster from 2026-06-18).** (a) The bridge-wait
    `--timeout` is consumed by the QUEUE-WAIT, not just the run — a high-pri sweep submitted behind the
    exclusive heavy-DB **production close pipeline** (`post_market_daily.ps1`, holds the single heavy-DB
    slot) waited ~2.5h before starting; a 3h bridge timed out with the job barely begun. Size the bridge
    timeout for (queue-wait + run), and just re-bridge if it times out with the job still `running`
    (`queue show` → started/exit; G2). (b) The daemon can **auto-restart mid-sweep** (pid changes),
    which **kill+requeues running jobs** (attempts++, restartable resumes per-candidate via the jsonl)
    and can leave transient core **oversubscription** (`cores 12/7, -5 free` — two grant-6 jobs on a
    7-budget box). Restart-resilient sweeps (the run_phase jsonl-skip) survive it; watch `attempts` (a
    flapping daemon → 3/3 cap = fail). (c) A job submitted `--priority high` can show/behave as `low`
    behind aged low-pri leftovers — verify `queue show`, re-assert `queue priority <id> high`. (d)
    Oversubscription does NOT corrupt sweep numbers (MC is seed-deterministic, PYTHONHASHSEED=0), only
    wall-clock → the gate stays valid; don't kill co-runners for correctness, only for speed.
37. **PRF "size-up" extrapolation can be falsified by the MC on a saturated/cap-bound substrate (refines
    Phase 4's PRF seed).** `portfolio_response.py --derive` coefficients are v70/v71-fit. When it flags a
    SIZE-UP divergence (v74-lean: F mid .10/low .051 vs live c04 .08/.03, because supply dropped −38%),
    VALIDATE THE DIRECTION before trusting it: a size-up at high recycle_coverage (≈0.97 = book
    saturated) + a binding gross cap (50%) is the **G16 over-deployment trap** (bigger slugs CONCENTRATE
    under the cap, no compound recapture). The full B→C→D fell to **validate-c04** — every size-up failed
    T4 (c04_mid10 +1.7pp 5y DD, CONVERGED across N=100/300/500 = real, not noise; +2.2% compound
    insufficient). PRF is a good seed for IDLE/denser substrates (low coverage, e.g. v70's 0.66), NOT
    saturated ones — "F proposes, MC disposes," and on a new-supply substrate the MC can dispose against
    F's *direction*, not just its magnitude. `experiments/v74_cascade_retune/`.
38. **An honest NO-SHIP (close the freshest leads cleanly) is a valid /research outcome — don't force a
    marginal ship.** 2026-06-18 closed all 3 freshest leads (trend-confirm×VIX tilt → G26 reversal-trap
    precondition-null; v74 cascade retune → validate-c04; forward-vol VIX-term/VVIX/VRP → G28
    level-control null). The regime-signal families are now EXHAUSTED (forward-vol was the last untested
    one), directional re-shaping is exhausted (G14), the DD-sizing well is dry (G23 + the 6 shipped
    levers). The genuine next-ship path is the **option/IV data-unblock (NEW_LEADS N3)** — a
    data-acquisition task, not a research probe. When the explorable leads are tapped, the highest-EV
    move is to close them with do-not-retry evidence (NEW_LEADS + memory + FINDINGS) + a handoff pointing
    at the data-unblock — NOT a rushed/marginal ship (Stage > rush, G13).
39. **A "buy a put to hedge the calls" idea is a NEGATIVE-CARRY tail-insurance null — kill it with a cheap
    overlay ledger + a REAL-premium calibration, and the verdict pivots on the real short-DTE premium (NOT
    a guessed haircut).** 2026-06-22, user's "short-dated (≤3 DTE) protective put against the call book for
    the bad-1-day outcomes, Apex + Core." Method (no MC, ~30 min): co-buy an ATM put at each real 75+ call
    entry (dedup the MC tape to unique (sym_id=symbol, entry_date); `sym_id` IS the symbol string, joins
    straight to a close+high+low+**open** OHLC parquet — `.cache/intraday_overnight/ohlc.parquet` is the only
    cached price set WITH open), walk days 1..DTE with `option_pricing.option_pnl_pct` (honest delta+√-theta).
    **THE decisive distinction: three exit policies, only two are achievable.** `fantasy`(sell at intraday
    low) and `EODclose`(sell at the best daily close over the window) are BOTH look-ahead (max-over-future =
    the MFE-gap/trailing-stop trap) and came out POSITIVE — a trap that would have FALSE-shipped the hedge.
    The achievable exits — a CAUSAL fixed-T target ("sell when the put is up ≥T *that day*", single best T
    chosen at aggregate, NOT per-signal max-over-T = also foresight) and hold-to-expiry insurance — are the
    verdict: net-negative every window except the isolated 2020_crash, incl 10y-with-COVID. **The result
    pivots on the put premium**, so DON'T guess a haircut: a `PUT_PREM_HAIRCUT` knob showed it flips POSITIVE
    at ≤0.71×, so I CALIBRATED the real premium from `option_prices` (cached `iv_skew/iv_ledger` for the 30DTE
    `iv_rv` anchor = 1.08 median, options ABOVE realized vol; a queued db=heavy ≤5DTE-ATM-put point-lookup =
    real/model median **1.21×**, the gamma/event premium, **median volume 0** = wide spread). Real short puts
    are MORE expensive + illiquid → the cheaper-premium door is closed. Generalizable: (a) a protective put is
    insurance the **dead-hold already provides for free** (`dh_off`=100% collapse) — always note that
    redundancy; (b) the put PAYS on the loser subset but is swamped by premium bled on the ~65% winners
    (calls gap UP) = negative carry; (c) for any "is the option cheaper than my realized-vol model" question,
    the `iv_skew` ledgers (`iv_rv`, real `entry_premium`) are the cached anchor + a tiny queued point-lookup
    pins the short-DTE term-structure — never assume the formula's premium. Harness: `experiments/short_put_hedge/`.
40. **Triage a user's "use [named indicator] to detect [crash/collapse] and cut calls" idea against the
    SHIPPED-MECHANISM list FIRST, not just the null ledger — the idea is very often ALREADY LIVE under a
    different name, and the literal "collapse → cut calls" core is almost always the crash-artifact trap.**
    2026-06-23, user's "use the marketwave / find the correlation with SPY to detect breadth collapses
    where it's a bad time to hold calls." This decomposed cleanly into TWO already-shipped v74 levers:
    **MWDD** (2026-06-05 — its FINDINGS literally open with the near-identical "use the market wave to
    protect against the pullback" ask; crash-state contraction FALSIFIED there, flat-band shipped) and
    **BDIV** (2026-06-11 — the literal "SPY-near-high × breadth-rolling-over" divergence). The named
    sector-ETF "Market Wave" score transform (v57) was already RETIRED (crash-artifact). So the highest-EV
    move was a G17 triage + ONE cheap **read-only** confirmation, NOT an overnight B→C→D. The confirmation
    needs **no new tape**: the existing `.cache/dd_ledger` tapes + the `experiments/market_wave_dd_v70/mine.py`
    pattern give a ~30-min residual mine (bucket 75+ call EV by the one untested formulation — here a rolling
    SPY↔breadth Pearson correlation + divergence — and check the **all-shipped-levers-off slice**). Result
    was the textbook trap: every "breadth collapse" cohort is a mean-reversion WINNER (collapse_flag +0.274
    in the levers-off slice, conc 0.01), the Apex edge IS buying weakness (G19/G3). Generalizable: when a
    user names an indicator + "crash detection" + "cut calls," (a) check whether a shipped lever already
    keys on it (grep strategy_config `*_ENABLED` + read that lever's FINDINGS), (b) the literal contraction
    is the documented trap, (c) the cheap orthogonal-slice mine on existing tapes confirms in <1h. Don't
    burn a tape + B→C→D on a closed axis. Harness: `experiments/spy_breadth_corr_dd/`.
41. **A "cut the dead machinery" / parsimony hypothesis is answered by the ACTIVE-MECHANISM CONFIG +
    the funded-relevance rule, NOT a sweep — and a verify/lean lead can resolve to "already-done."**
    2026-06-24 (verify_value gate-vs-gradient audit, A2): hypothesis = "retire pure-gradient score-stage
    mechanisms that don't move the funded payoff." The decisive evidence is `strategy_config.SCORING`
    itself: enumerate active mechanisms (`*_ENABLED`/`K=0` states) and classify each by **its GATE vs the
    traded threshold** — Apex trades 75+, so a mechanism gated **below 75** (or put-side ≤25, puts off) is
    **funded-irrelevant BY CONSTRUCTION**, regardless of its per-trade WR/z. The v71/v73/v74 lean campaign
    had already retired every 75+-reaching gradient-shaper (MCD/ICH/SCW/CWCF/CSWC/continuation/WVD/daily-
    volume/EARN_BOOST), so v74 was ALREADY at the optimum; the lone active call-side dampener CWWD is gated
    [70,75) (one 25-sym rescore A/B confirmed 0/569 75+ rows change, 75+ set byte-identical). Generalizable:
    (a) for a subtractive-parsimony lead READ THE CONFIG FIRST (gates + enabled states) — often resolves it
    in <1h with zero compute; (b) a score-stage mechanism's funded-relevance = whether its gate reaches the
    traded threshold, not its WR/z; (c) an honest "already-lean, no cut" is a valid outcome (G38) that
    retroactively validates prior lean ships. The tiny rescore only confirms a near-tautology (a sub-gate,
    lower-only dampener can't change the traded band). Harness: `experiments/verify_value/{GATE_AUDIT.md,
    audit_cwwd.py}`.
42. **A component-score "discontinuity/cliff" that feeds the 75+ gate is often a LOAD-BEARING SIGNAL, not a
    measurement artifact — the decisive test is the apex WR of the rows the fix DROPS from the gate, NOT the
    cliff's existence or a stability metric.** 2026-06-24 (score-fidelity audit, `experiments/score_fidelity/`):
    an analysis-quality study ("does the score faithfully READ its indicators?", complementing verify_value's
    forecast-skill). Method: join stored component scores to the raw `indicators` table, scan each component's
    raw→score mapping (monotonicity/saturation/discontinuity/gate-wobble), then READ each component fn in
    core.py to find the mechanism. The one real cliff (MACD 4-branch momentum-PHASE, ~11 macd-pts from noise
    at velocity=0 — the WCF-27/28 class the codebase smoothed for rsi but NOT macd) LOOKED like a clean
    v72-class stability fix. The smooth-phase A/B (soft-gate blend of the same 4 branch formulas, ScoreSimulator
    monkey-patch on `calculate_macd_score`, W∈{0.05,0.08,0.15}) was NET-DILUTIVE: at every W it DROPPED
    above-average-WR rows from 75+ (apex WR 75-80% > stable 73% — the `vel>0` peaking/building branch is the
    "front-run the histogram peak" SIGNAL) and shrank supply, for a marginal stability gain (the macd is
    inherently volatile, mean |Δmacd| 8.16/day; the cliff is a small contributor). Generalizable: (a) for ANY
    "smooth a component cliff / fidelity fix" idea the decisive read is the apex WR of the DROPPED rows —
    >stable ⇒ signal, do-not-smooth; (b) the codebase already embodies this (smoothed the rsi range-GATES,
    kept the rsi breakout/divergence PUSHES = v42-load-bearing); (c) this is the v42 push-band lesson +
    verify_value gradient-inert finding in the fidelity domain — there is NO fidelity fix that improves gate
    SELECTION (nothing predicts apex above the gate), so the scoring layer is at its funded optimum given the
    inputs. The audit is a valid DIAGNOSTIC; it found the one real discontinuity and the gate-impact test
    correctly killed it. Harness: `experiments/score_fidelity/{fidelity_audit,rescore_macd,macd_compare}.py`.
43. **A "dynamic / regime-conditioned component WEIGHTING" idea → the decisive cheap pre-test is the
    component-tercile-vs-regime-base apex EV AT THE GATE, checked for sign-consistency across the regime
    classifiers a mechanism can actually use (regime_composite AND VIX) — a sign-flip across classifiers is
    noise, not a regime signal; kills it in ~10 min with NO rescore/sweep/recalc.** 2026-06-25
    (`experiments/regime_reweight/`, user's "different score combinations across regimes/market-structures").
    A reweight can only help by changing 75+ GATE membership (verify_value: nothing predicts apex above the
    gate), so test whether ANY component's hi-tercile has a LARGE apex-EV spread vs the regime base that holds
    across BOTH composite AND vix. Result comprehensively NULL: across 6 components × 5 classifiers no cell is
    large+consistent; TREND (the weather_components "harmful-in-bear/chop" axis) is the FLATTEST (±0.8pp — the
    2022/2023 component-EV harm does NOT survive to the gate; only the 2023 year-label shows it = single-window,
    and you can't key a mechanism on "it's 2023"); the noisiest cell (bb) FLIPS SIGN across composite-vs-VIX in
    the same regime (±5pp = the 30-cell multiple-comparison noise floor — use it to calibrate "what's real").
    The reweight-null (G35) + G26 reversal-trap, now at the gate per-component-per-regime. Two reusable
    sub-lessons: (a) the score ALREADY has the only robust dynamic weighting (the `d` sideways↔trending blend);
    "make it regime-dynamic" is mostly already-done. (b) when a regime weakness IS real (the chop negative-EV),
    it's a Stage-3 SIZING lever (a SPY-flat call-alloc contraction), NOT a Stage-1 reweight — selection inside
    the weak regime is unpredictable (chop winners aren't feature-separable) but its SIZE is dialable. Harness:
    `experiments/regime_reweight/{pretest,pretest2}.py`.
44. **A user's "new regime / market-structure axis" sizing idea must pass the PER-LEVER orthogonality 2×2
    vs EACH shipped lever's FIRING BAND — the tell for REDUNDANCY is that the candidate's low-EV INVERTS to
    good where the overlapping lever is OFF.** 2026-06-25 (`experiments/regime_call_alpha/orthogonality.py`,
    the A-MKT SPY-flat-chop contraction, the redirected form of the user's regime-weighting idea). FLAT_chop
    (SPY ±0.5%/20d) IS a real negative-EV regime, but NOT a 6th axis: the decisive 2×2 (FLAT_chop × MWDD's
    McClellan-flat firing band) showed FLAT_chop is bad ONLY where MWDD fires (WR 63.9%) and ABOVE base where
    MWDD is OFF (84.2%) — SPY-flat ≡ directionless-breadth ≡ McClellan-flat = MWDD's state; the system ALREADY
    contracts it. The all-levers-OFF slice (G21/G23) is the right idea but goes too thin on a RARE regime
    (N=13); the cheaper, more decisive read is the 2×2 vs the ONE most-overlapping lever — if the candidate's
    low-EV survives that lever's OFF region it's orthogonal, if it inverts to good it's that lever's axis. Two
    reusable lessons: (a) a user's regime-aware instinct is often ALREADY SHIPPED under a different feature
    name (chop→MWDD) — triage vs the SHIPPED-lever list (G40) AND decompose the candidate axis against each
    lever's band before any MC; (b) the leftover is usually a thin INTERACTION (deepen the existing lever in
    the both-conditions cell) = a G22 parameter-refinement of the shipped lever, NOT a parallel lever —
    overfit-prone, validate-carefully, rarely worth an overnight MC. (Note: `apex` in the regime_call ledger
    is a ±1 win/loss flag = WR proxy, not the +0.30/−0.70 EV — fine for relative cohort reads.) Harness:
    `experiments/regime_call_alpha/orthogonality.py`.
45. **A "% of stocks at highs / narrow-breadth = de-rate calls" feedback is the G19 buy-weakness
    inversion + a G40 already-shipped axis — the EV-by-breadth-extreme band settles it in ~10 min on
    the existing tape, and the user's directional premise is usually BACKWARDS for our sleeve.**
    2026-06-26 (`experiments/breadth_ath_dd/`, Bagholder "~4% at highs + 85% underperforming murders
    capture; want breadth+VIX de-rating long calls until breadth broadens"). The metric (% at ALL-TIME-
    HIGH) is genuinely not in the breadth system (built it from PriceHistory expanding-max; ATH ⊋ the
    52w-high NH/NL that already failed G23) — but the result triple-inverts the ask: full-tape call
    mean-opt-pnl is MONOTONIC DECREASING in %-at-ATH (`<2%` at ATH = +0.091 BEST, `10-18%` = −0.017
    worst). Few-at-ATH = oversold/beaten-down = our buy-weakness calls mean-revert and WIN (G19/G3); a
    discretionary equity holder's "narrow breadth = topping = sell" intuition is INVERTED for a buy-
    weakness leveraged-call sleeve. G44 confirmed redundancy (the pct_ath low-EV concentrates where MWDD
    fires, inverts to good where MWDD is off). Generalizable: for ANY "froth/narrow-breadth → cut calls"
    feedback, (a) triage vs the SHIPPED levers (BDIV+MWDD+F3F+RXDD already de-rate on breadth/froth), (b)
    the regime-MULTIPLIER de-rating it names is the documented no-op/inversion (weak breadth is the
    higher-EV call state → the regime amplifying on weak breadth is CORRECT), (c) the cheap kill is
    EV-by-breadth-extreme-band on the existing tape — the buy-weakness inversion is substrate-robust
    (v70→v74, z=−65 monotone every window) so no fresh tape needed.
46. **A "optimize my fast-2x SPRINT against DD" ask → the DD↔compound dial is the NUMBER OF NAMES,
    NOT a sizing lever; and 30-DTE >> 15-DTE for the sprint (premium cushion).** 2026-06-30
    (`experiments/apex_dte_dd/`): runs ENTIRELY through the `concentration_2x` harness — any DTE via
    `NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS` env (premium+sigma+theta auto-scale √(DTE/30); 15-DTE faithful,
    NO mc15 port), `--cells flat_nN_aXX --tag`, metric = pooled monthly-roll P(2x)/days/DD/collapse/
    compound incl COVID+2022 starts (the sprint-aligned objective; no new harness). **30-DTE strictly
    dominates the live 15-DTE sprint** at every concentration (4×25%: compound +4→+50%, DD 88→82%,
    collapse 1.3→0%, P2x 57→72%; the pure-DTE control confirms it's the premium cushioning gap-downs —
    the documented "30-DTE is the definitive primary instrument" law, now on the sprint config). The DD
    lever is **diversification** (n4 fast/82% → n10 +108%/76% Pareto-best, slower), NOT a knob: a
    stronger RXDD is a NO-OP (the VIX-20-28 band is too small a slice when the book is fully-deployed →
    the level lever SATURATES) and DD-soft-band cuts DD only ~1:1 for compound (G16 capital-velocity).
    A user collapse-tolerance RELAXATION (≤10% here) is usually a non-event — the aggressive 2-3-name
    cells stay traps (neg/low compound, 90%+ DD) even when *allowed*; the optimum stays in the
    collapse-free band. Generalizable: for a concentrated-sprint DD ask, sweep **n-names + DTE** (the
    structural dials) FIRST, not the alloc-dampener params (they saturate/trade-off). The pre-run VIX
    recon also re-confirmed VXMD/G28: VIX-TREND/BB-magnetism is NULL (rising-VIX is the better call
    state), only VIX-LEVEL (RXDD) is real — and it's saturated for the sprint.
47. **Subagent-built harnesses: forward the relevant LIVING GOTCHAS into the agent brief (G5/G7 at
    minimum), and expect a green small-N smoke to hide a FULL-SCALE-ONLY failure class whose root is
    young listings (<200 bars) absent from any smoke sample.** 2026-07-14 (trend_ma_lattice): a Sonnet
    agent delivered a 1,400-line self-testing mine (excellent — orchestrator audits only the
    point-in-time loop + SUMMARY), but the full run failed 3× in sequence on that one root: (a) G7
    recurrence (`pl.DataFrame(list-of-dicts)` schema inference — the brief didn't forward it); (b)
    NaN-as-sentinel ≠ null in polars (NaN passes `is_not_null()` → strict-cast crash; fix = one
    `fill_nan(None)` choke point post-selftests); (c) **the dangerous silent one: NaN regressors
    poisoned the controlled logit/OLS inside try/except → t_controlled=NaN → the pre-registered gate
    auto-fails as a FALSE-NULL with no error** (fix = mask non-finite X rows, `finX`). After the first
    full-scale crash, sweep the pipeline for the whole missing-data CLASS, not the single crash line —
    and any gate whose failure mode is "NaN counts as a null-verdict" must prove its inputs finite.
48. **A CHANGELOG/orientation line that says "next ship = option/IV (N3)" is STALE as of
    2026-07-14 — the whole option/IV data-unblock arc RESOLVED that week.** OSK (P2.4) FAILED
    cross-era validation 2026-07-07 (regime-conditional, not universal); the score-modifier form
    BLOCKED 2026-07-08; the Stage-3 alloc-tilt form PARKED 2026-07-10 (re-read ~2026-10). The
    gamma+IV calibrated-premium model PASSED its MC A/B 2026-07-12 (adversarially verified) but
    the per-trade adoption gate FAILED M1 2026-07-13 → (GAMMA_AWARE+IV_MODEL) PARKED A/B-only
    default-OFF. VEGA_STATE is CALIBRATION-BLOCKED 2026-07-13 (no VIX-stress episode in the
    panel). The $2,035 L3 buy is OFF permanently at current evidence. Before trusting ANY "next
    ship" line anywhere in this repo's docs (including older CHANGELOG entries below, which stay
    as dated historical record, not live guidance) — read `.claude/docs/gameplan.md` §2b/4/6b for
    the actual current live frontier (Core tier re-sweep, lifecycle-policy MC, P3.6, the
    Dec-2026-12-15 OOS unlock).
49. **A user's "signals at the top that then plunge are filterable fakeouts" ask → run the
    INTERACTION-level mine (peak-state × feature) on the lattice-ledger substrate — it's a ~1.5h
    close with the now-existing harness (`experiments/peak_fakeout/mine.py`, adds the
    date-cluster-robust CR1 z the trend_ma_lattice machinery LACKED — its z_raw is naive
    binomial).** Expect: peak-state ≈ base WR (3rd confirmation 2026-07-15), NO below-BE cohort
    (worst texture ~65% vs call BE ~45% — nothing to gate out; the remembered plunges are the left
    tail + salience), and near-misses that fail window-stability. Three reusable stat traps:
    (a) near-constant buckets DEGENERATE the CR1 sandwich (|t|~20 on ~0pp effects) — rank cells by
    gated legs, never raw t, and require bucket share ∈[5,95]% of subset; (b) a raw "N cells at
    z≥3 vs 0.35 expected" excess is NOT N findings — nested conditioning states duplicate rows ×4
    and WR/plunge outcomes mirror; count independent loci; (c) the fix for prevalence-dependent
    peak-state thresholds is a DISCRETION-FREE ladder locked in the prereg, auto-applied in code
    before any outcome column is read. Also: G5 applies to YOUR OWN post-run polars one-liners
    (a `pl.Config` table print crashed cp1252 mid-audit — set PYTHONIOENCODING on analysis
    snippets too, or print ASCII rows).

50. **Any calendar/trading-day feature study must derive its trading-day index EMPIRICALLY from
    observed bars (date is a trading day iff ≥50 distinct symbols have a bar), never from
    `database/utils/trading_calendar.py` — the static NYSE_HOLIDAYS table covers only 2023-2026 AND
    is missing a real in-window closure (2025-01-09 Carter mourning; pre-2023 it misses every
    holiday incl. 2018-12-05 and the April-2019 Good-Friday-on-3rd-Friday OPEX shift).** Stitch the
    static function only for the future tail past OHLC coverage; add an overlap-agreement assert
    between the two sources (it caught the Carter gap on first run); unit-test era anchors
    (2019-04-18 OPEX, 2018-12-05 closed). Pattern: `experiments/wave_cycle_mine/calendar_features.py`.
    Corollary: date-level features (calendar cells) make DATE-CLUSTERED z MANDATORY — naive binomial
    z on ~5.8k trades is really ~1.4k date clusters; CR1 clustering deflated every naive-looking
    W-cell to null. And when a builder patch changes a prereg-named SOURCE (not a bar), record a
    dated pre-run AMENDMENT in PREREGISTRATION.md before reading any outcome.


51. **`price_history.close` is SPLIT+DIVIDEND ADJUSTED; option strikes are AS-TRADED — and this
    silently contaminated a panel that two KILL verdicts rest on.** 2026-07-25. `trader.py` pulls
    yfinance with `auto_adjust=True`. Any code comparing that close to a listed strike (picking an
    "ATM" contract, or passing a spot to a BS solve) mixes two price spaces. It survived because the
    drift is NOT uniform: a no-dividend/no-split name has factor exactly 1.0, so **68.6% of rows are
    clean and every median-based sanity check passes**; the damage is a one-sided right tail. Two
    structural consequences make it lethal for research: it is **worst in the OLDEST data** (drift =
    cumulative dividends/splits since the signal date; 2022 = 44.5% of rows drift >2% vs 2026 = 0.6%)
    i.e. exactly the backward-OOS slices used to KILL leads, and it **concentrates in CALM names**
    (symbol-level rho(vol, adj_factor) = −0.40; top-drift names are dividend payers) so any finding
    phrased as "the model disagrees with reality on calm names" must rule this out FIRST. Both apply:
    OSK was killed on its 2022 read, and gamma+IV failed M1 on the calm-name diagnosis. Fix:
    `spot_unadj = yfinance Close(auto_adjust=False) x prod(FORWARD split ratios)`
    (`experiments/polygon_real_premium/pull.py: load_unadj_spots`, validated NVDA/MMM/AZN).
    **Generalizable:** before trusting any cross-source join, ask what price SPACE each side is in.
52. **A cohort flag defined over a REALIZED holding window is outcome-conditioned and manufactures a
    huge fake effect.** Exit date IS an outcome (TP winners exit ~4d, dead bags run to the ~15d
    expiry), so "event X fell inside the holding window" preferentially selects long holds =
    losers. Measured: ex-post flag gave EV −0.3419 vs +0.0375 base (gap **−0.3794**), dd_conc 2.40 —
    a textbook Pareto-lever signature; the ex-ante flag (entry + NOMINAL hold) gives **+0.0118**, a
    97% collapse AND a sign flip. **Tell:** an effect ~10x anything else in the system that dies when
    the window is redefined. In a mature repo a huge effect is a bug until proven otherwise. Rule:
    define event-window flags from ENTRY + NOMINAL hold, and report the hold-duration composition of
    both cohorts as standing proof.
53. **Pre-register the MECHANISM, not just the effect — it is what makes a surprise adjudicable.**
    `prior_earn_jump_pct` T3_high was real (dEV −4.38pp, CR1 t −3.33/−4.41, PIT-clean, 5/6 years).
    The prereg also fixed the physical story (jumpy names hit the −2.548σ STOP) and declared in
    advance that a falsified mechanism = PARK. The decomposition came back **stops flat (+0.29pp),
    expiries +5.85pp** — the names go nowhere, they don't crash. Without the locked prediction that
    surprise is trivially re-narrated into a supporting story. Also useful here: a real effect that
    lives ONLY in the marginal band (75-79 −6.20pp t −4.12 / 80+ **+0.32pp t +0.14**) is an
    interaction with the gate, not a risk factor; combine with a cohort share drifting 23%→44% across
    the sample and you have the profile of something that fails OOS.
54. **The task queue resolves a bare `bash` to WSL — a `.sh` driver dies instantly with
    `execvpe(/bin/bash) failed`.** 2026-07-25: a multi-group sweep driver written as a shell script
    failed in 5 seconds with exit 1 and an empty run.log; the stderr.log held the real message. Same
    class as the Task-Scheduler `python`-resolves-to-the-WindowsApps-stub trap. **For any queued
    fan-out driver, write it in Python** (`subprocess` + `ThreadPoolExecutor`) — no shell-resolution
    dependency, and you get the resume guard and per-group logging for free. Always check
    `stderr.log` in the run dir, not just `run.log`, when a job fails fast with no output.

55. **A "sell options / short premium" ask → per-trade EV is NOT the verdict layer; cluster-Kelly is.**
    2026-07-26: bull-signal short puts showed a REAL +4-7%/trade EV on margin (t 4-8, real Polygon
    prints) yet the portfolio tops out at +5.7% CAGR — signals arrive ~7/day with ~23-day holds, so
    the fat left tail (−1..−6x margin) clusters; single-bet Kelly said f*≈10-12% but the
    23d-overlap-cluster Kelly says f*≈1% (worst real 23d window −45x the per-trade fraction). For ANY
    correlated-signal sleeve, compute E[log(1+f·S)] on date-cluster + hold-overlap SUMS before
    trusting a per-trade edge; also expect per-trade optimum ≠ portfolio optimum (whole-contract
    flooring + margin friction inverted the deep-OTM ranking). And the dead-hold law INVERTS for
    shorts: SLs excellent idiosyncratically (P01 −1.6→−0.7) but the SYSTEMIC killer in crashes
    (simultaneous forced buyback at IV-peak marks: dotcom −91%). Harness: `experiments/short_premium/`.
56. **Sunday full-budget runs: the 3-parallel-Sonnet-builder pipeline (pull / ledger / portfolio+stress
    briefed against a LOCKED schema in the prereg) converts a novel data-class study into ~4h wall.**
    Lock the output parquet schemas in the PREREGISTRATION and brief the ledger/portfolio builders
    against them WHILE the pull builder works — zero integration rework. Extend-and-resume pulls
    (journal keyed by cell) make optimization rounds ~free: only new cells fetch. Bank vendor pulls
    BEFORE a sub cancels regardless of study outcome.
57. **A program/tracker row marked NEXT-TO-DISPATCH can rest on a premise that went stale the DAY
    AFTER its source doc was written — re-read the target axis's OWN experiment dir
    (VERDICT/FINDINGS + git log) before dispatching ANY pre-planned package.** 2026-08-06: FF-3
    ("the one surviving HIGH; sweep and ship if PASS", PROGRAM.md 08-02) inherited known-issues
    #10's "not yet swept" (written 07-13); the sweep RAN 07-14 and closed ENGINE-BLOCKED with a
    standing ban ("no further floor sweeps on this engine" — concentration artifact, traps.md
    ~L942). The doc chain rots at any link (known-issues → gameplan → program → tracker) and the
    NEWEST doc inherits the OLDEST error, so G17/G40 triage must include the axis's experiment
    dir even when a fresh program doc says open. Salvage shape when a planned package dies at
    triage: SPLIT it — the measurement stage (map) usually stays licensed; replace the banned
    stage with the verdict's own re-open recipe (here: spread-penalty IN the engine grounded by
    FF-2's trades-tape Roll curves, + the verification's random-equal-count-drop control
    promoted to a primary arm so the artifact is netted out by construction).
58. **Market-level (date-only) cohort features PARTITION calendar days — a per-date PAIRED cluster
    test is degenerate by construction (every date is wholly cohort or wholly rest); use a
    two-sample Welch t over per-date means.** 2026-08-10 spy_ma_overlay: the standard "aggregate
    per-date diffs" recipe returned z=n/a on all 96 cohorts until fixed. Companion trap:
    freeze-forward padding for delisted/terminated series must take the UNIVERSE calendar as an
    explicit parameter — deriving it from the input frame silently emits zero padding on narrow
    inputs (multi-symbol frames mask it; a single-symbol selftest catches it). And the famous-rule
    claim-audit template is now standing: prereg a canonical cell + descriptive grid + NW-t on
    monthly excess + sign-flip max-|t| multiplicity + a survivorship-honest universe leg with the
    delisted-cohort tail decomposition — a "saves you from zeros" claim is adjudicable ONLY with
    delisted names present (`experiments/ptj_trend_audit/` extends `vix_cycle_claim`;
    `.cache/ptj_trend_audit/universe_1997.parquet` is the reusable 1997+ substrate).

---

## SELF-UPDATE PROTOCOL  (this skill heals + improves itself)

Before you finish a `/research` run (shipped, staged, or aborted), improve THIS file so the next agent
is faster:

1. **New gotcha?** If you hit any friction/bug/trap NOT in `## LIVING GOTCHAS` that cost real time or a
   failed iteration, APPEND a numbered entry: **symptom → root cause → fix/avoidance** (≤4 lines).
2. **Stale step?** If a path moved, a command changed, a line number drifted, or a gate threshold
   shifted, FIX it in place.
3. **Faster way?** If you found a cleaner way to do a phase, update that phase.
4. **Bump the CHANGELOG** (one terse line: date + change).
5. Commit the SKILL.md change with your ship/stage commit (or a small `skill(research): ...` commit).
   Editing under `.claude/` may prompt for approval — bundle it at the END with the other doc updates.

Rule of thumb: **if the next agent would benefit from knowing it, it belongs here.** Prefer appending a
gotcha over letting the next run rediscover it.

---

## CHANGELOG
- 2026-08-10 — PTJ 200-DMA theory + "other EMAs/overlaps" audit (user-directed, Sun-night ~8h
  budget, ~2h wall + queued) → **comprehensive AUDITED-NO-SHIP**, prereg committed pre-outcome
  (`41081764`). Leg-3 sleeve overlay: 0/96 escalations on 4.15M v74 trades x 12 windows —
  below-200DMA = the sleeve's BEST cohort (buy-weakness inversion, 3rd market-state confirm);
  SPY 0/408 + survivorship-honest universe 0/96 cells positive NW excess (defense REAL:
  DD −18/−20pp, crash captures, delisted ≤−80% tail 14.8→3.6% death-specific; offense FALSE:
  CAGR −3.7/−4.8pp/yr, median delisted outcome WORSENS — M&A-dominated); "be with the trend"
  spread t 0.16. Axis closed at all 3 layers (selection/exits/overlay) in known-issues.
  2x Sonnet builders vs locked prereg (G56): Builder B caught the paired-t degeneracy, Builder
  A's single-symbol selftest caught the padding-calendar bug pre-run. Banked
  `universe_1997.parquet` (1,626/588/7.1M). Added G58. Harnesses:
  `experiments/{ptj_trend_audit,spy_ma_overlay}/`.
- 2026-08-07 — Stage B′ (penalized liquidity floor, the corrected FF-3) executed end-to-end
  in-session: prereg drafted → user-blessed → committed BEFORE build; hooks env-gated into
  monte_carlo.py with the G4 OFF-byte-identical proof as the hard gate over CRITICAL-fan-out
  impact flags; B→C→D (#305-307, N=100/300/500, paired seeds) with matched random-drop
  controls → **Core floor-150 STAGED SHIP-CANDIDATE (7/8 windows control-net positive,
  collapse 0 everywhere); Apex T4-fail; 2024 the named counter-window**. Pattern worth
  keeping: the builder's NotImplementedError stub on adjudicated phases (C/D un-runnable
  until the orchestrator names survivors in code) cleanly enforces the judge/builder split;
  and a pre-registered failure EXPECTATION being wrong is fine when the prereg's decision
  rule still adjudicates cleanly — record the surprise, don't re-narrate it.
- 2026-08-06 — weekday pre-open `/research` (~2h50m budget, FF-program step) → **pre-dispatch
  triage caught the queued FF-3 package as a BANNED re-run** (P3.6 floor swept + closed
  ENGINE-BLOCKED 2026-07-14; the 08-02 program docs had inherited stale known-issues #10 text)
  → FF3_AMENDMENT.md corrected path, executed same-run with 2 parallel Sonnet builders (G56
  locked-schema pattern): FF-3′ Stage A real-4y liquidity map BUILT + READ pre-open (#291, 8
  min: coverage 100%, Spearman .380 vs single-day entry_volume / .536 vs median_path_volume,
  monotone at every split, deciles banked for grid derivation) + FF-2 trades_v1 Roll-spread
  scan run + READ same morning (#292 scan 21.4 min/0 errors/166.55M rows; curves #294; first
  curves attempt failed CLEAN on the builder's guarded schema check — map `symbol` vs expected
  `underlying` — exactly what the guard was for). **FF-2 R6 verdict: DOES NOT FIRE** — cascade-
  slice median Roll full spread 2.84%→1.04% monotone by map tier, all ≤ modeled 3% (the
  asymmetric-cost canon's first wide-N empirical citation) — but 75.8% of bottom-tier ATM
  contract-days have <6 prints ⇒ thin-name risk is FILL-PROBABILITY, not spread ⇒ Stage B′
  penalty must be two-component; pre-expectation recorded that the floor may fail B′ on
  measurement (a licensed closing outcome). Stage B′ staged, prereg next session
  (FF2_RESULTS.md). Doc-rot fixed at all four links (known-issues #10, PROGRAM, TRACKER,
  NEW_LEADS P3.6). No banned sweep, no forced ship (G38/G13). Added G57. Builder catch of
  record: `pl.cov` returns 0.0 (not null) on 1-print tickers — an ungated Roll estimator
  silently emits fake zero spreads (n_prints≥6 gate).
- 2026-07-26 (b) — user-directed `/research` on option SELLING (naked puts/calls + PMCC, "optimum
  curve / max return vs % risk", Sunday ~23.5h budget, ~5h wall used) → **comprehensive NO-SHIP
  with the axis fully mapped and CLOSED.** Banked 61k+ real Polygon put/call/LEAPS paths (perma-
  asset, pre-cancellation). H1 bull puts: real carry (+7%/trade deep-OTM+SL2x, t 8, 5/5yr) but
  ~all VRP (lift vs control ns) and cluster-Kelly-capped at ~1%/trade → +5.7% CAGR @ 12.6% DD
  best realistic; H2 bear calls killed (−37x tails); H3 PMCC killed on real prints (t −9);
  crashes −30..−66% modeled with complementary exit-family failures (SL = systemic killer).
  Long-call cascade strictly dominates (structural asymmetry). PARKED: PUT_OVERLAY diversifier
  (corr +0.33, positive in 4/6 worst call months), Dec-OOS re-read of the June-2026 tail sliver.
  Added G55 (cluster-Kelly is the verdict layer; per-trade optimum ≠ portfolio optimum; dead-hold
  law inverts for shorts) + G56 (locked-schema 3-builder pipeline; extend-and-resume pulls).
  Harness: `experiments/short_premium/` (5 modules, 25+ selftests, 792-cell grid).
- 2026-07-26 — viral-claim triage (user: analyze "undefeated: buy stocks VIX 30/45+, sell VIX 14, repeat →
  outperforms S&P every year") → **CLAIM FALSE, ~15 min, read-only, no MC.** The "undefeated" kernel is real
  but near-tautological (8/8 cycles positive at N=8 — VIX≤14 only occurs after recovery, the G52 class);
  the outperformance claim INVERTS: x5.58 vs x27.95 B&H (CAGR 5.60% vs 11.13%), maxDD −60.4% WORSE than B&H
  −55.2% (buys crashes early at VIX 30, rides the full collapse), 17/31 years underperform, and the fatal
  cost is calm-grind years missed in cash (2013/2017/1996/2006). Consistent with G19 (buy-panic is
  per-trade +EV) + G16/G22/G28 (low-VIX exit surrenders the best compounding state; VIX timing beyond
  level-band sizing closed). Reusable fast-kill for any "VIX threshold cycle" claim:
  `experiments/vix_cycle_claim/backtest.py` (MarketRegime vix_close/spy_close is a self-contained
  1995+ series for market-timing claim triage; rerun with new thresholds ~30s).
- 2026-07-25 — weekend `/research` (user: "find and improve algorithmic returns... don't stop until
  you have a new version that verifiably better", plus two named hypotheses and a Polygon directive;
  ~55h budget to Monday open, used ~1.5h wall + queued compute) → **NO SHIP on the returns axis, but
  the run's value is a DATA-INTEGRITY find that un-adjudicates two prior KILL verdicts.**
  Five pre-registered legs null: the noise constant is real but negligible (jumps = **32% of return
  variance, 3.4% of outcome labels** — `nu_flip` 0.0339, CI widening 0.02pp); the "event noise masks
  a real gradient" hypothesis is FALSIFIED (CYCLICAL Spearman 0.0055, t +0.40) and independently
  re-confirmed a third time on REAL option prices (corr +0.003); the binary-checklist premise is
  INVERTED (6/6 unanimity EV −0.0055 < 4/6 +0.0361); ex-ante earnings sizing null. One real cell
  (`prior_earn_jump_pct` T3_high, t −3.33/−4.41, PIT-clean, 5/6 years) PARKED to Dec-OOS on 3 of 5
  locked kill-tests failing. **The finds:** (a) `price_history.close` is split+DIVIDEND adjusted while
  option strikes are as-traded — contaminating the Polygon BS-IV panel worst in **2022 (44.5%)** and in
  **CALM/dividend names (rho −0.40)**, i.e. exactly the slices OSK and gamma+IV were killed on;
  (b) a 4-year REAL contract ledger (3,339 paths, 65s) that VALIDATES the premium model (real/model
  1.022) and the sigma→option mapping (1.359x vs assumed 1.300x) but shows the apex15 assessment
  barrier is ~3.2pp EV-optimistic because it is THETA-BLIND. Added G51-G54. Harnesses:
  `experiments/{event_noise_decomp, polygon_real_premium, bankroll_ladder}/`.
- 2026-07-16 — wave/cycle mine (user-steered after a Voynich-entropy discussion: "find wave-like
  patterns we can statistically benefit from"; weeknight ~11h budget, used ~3.5h) → **W calendar
  family (OPEX/TOM/DOW/month-half/quarter-end) COMPREHENSIVELY NULL (17 cells, fresh 2016-2020 era
  mirror); S path-structure family produced the July mines' only 5/5-leg survivor — S1_vr5
  (variance-ratio path-persistence; plunge z_clust −3.96/+3.53 sign-stable 5/5, orthogonal to
  controls) — REAL but BELOW the locked actionability floor (d_ev +0.0298 vs 0.03; WR 2022-inverts
  G26) → PARKED to Dec-2026 OOS with bars locked; honest NO-SHIP (G38).** Discipline notes: the
  actionability adjudication was settled by the prereg's literal text (per-cell "vs rest", not
  tercile spread — no post-hoc redefinition of a 0.0002 miss); calendar-source fix recorded as a
  dated pre-run Amendment. Fable-architect/Sonnet-implementer tiering per the new process.md
  section (~630k subagent tokens, 2 rounds; orchestrator audited PIT loop, calendar core, verdict).
  Added G50 (empirical trading-day index; static holiday table wrong in-window; date-clustering
  mandatory for date-level features; amendment rule). Harness: `experiments/wave_cycle_mine/`.
- 2026-07-15 — peak-fakeout discriminator (user: "buy signals at rally tops that plunged —
  find the metric (mcap/vol/ema/ma) that separates fakeouts from breakouts") → **COMPREHENSIVE
  NULL at the pre-registered 6-leg interaction bar, axis closed; honest NO-SHIP** (weeknight
  ~7h budget, used ~1.5h; queue task #634, ~2min run). First interaction-level test (priors all
  marginal): 131 cells × 12 features × 3 peak-states on the funded v74 ledger N=5,810. Peak
  STATE ≈ base (67.5-68.8 vs 70.1% WR); no below-BE cohort exists; call-side earnings proximity
  (the one genuinely-open spitball-adjacent feature) decisively null; mcap/vol year-flip or flat.
  TEXTURE near-miss (P_run ∧ climax/parabolic T3, all-window-negative signs, fails Psign; losses
  via expiry not SL) locked for Dec-2026 OOS in FINDINGS §3. Orchestrator(Fable)+Sonnet-builder
  tiering: ~35min harness build, 5 self-tests green first full run; ~580k subagent tokens.
  Added G49 (interaction-mine recipe + CR1 degenerate-bucket trap + nested-cell z-inflation +
  prevalence ladder + G5-on-analysis-snippets). Harness: `experiments/peak_fakeout/`.
- 2026-07-14 (b) — doc-sync (not a `/research` run): `alpha_mining/NEW_LEADS.md` +
  `MISS_CANDIDATES.md` + `known-issues.md` + `gameplan.md` corrected to mark the option/IV arc
  (old NEW_LEADS N3) RESOLVED — OSK dead/parked, gamma+IV pair PARKED A/B-only, L3 buy OFF,
  VEGA_STATE calibration-blocked. Added G48 (stale "next ship = option/IV" orientation lines).
  No code/experiments touched.
- 2026-07-14 — trend MA-lattice study (pre-built gameplan, executed as written; weeknight ~8.8h
  budget, used ~2.5h) → **COMPREHENSIVE NULL, axis closed at Gate A** — the expected branch and a
  full success. 349-cell EMA/SMA horizons×crosses×above/below×kernel-div×sub-term lattice on the
  funded v74 75+ ledger (N=5,854; self-tested no-look-ahead, trend recomposition ±1, trend+pct-EMA
  controls, Jeffreys+hierarchical-pooling Bayesian layer): zero cells clear the pre-registered bar;
  observed z≥3 = chance (2 vs 0.9 expected); sub-terms decisively flat (no LEAN retune; kills the
  "trend is narrow" attack); kernel EMA-vs-SMA funded-irrelevant; fresh-cross hypothesis INVERTED
  (fresh = negative-EV — G27/G19 again). One near-miss (cdays_SMA_8_21=16-30, z 2.9/t 3.3/Psign
  0.85/replicates=True) parked for the Dec-2026 OOS re-read ONLY. Phase B cancelled (nothing to
  ReSim); honest NO-SHIP (G38). Orchestrator+Sonnet tiering worked well (~39min harness build; ~440k
  subagent tokens). Added G47 (forward G5/G7 into agent briefs; the full-scale-only missing-data
  class incl. the silent false-NULL t_controlled trap). Harness: `experiments/trend_ma_lattice/`.
- 2026-06-30 — Apex fast-2x SPRINT DD/DTE optimization (user-directed, collapse-≤10% relaxed) → STAGED,
  NO ship (weeknight ~8h budget; portfolio-only). **30-DTE strictly dominates the live 15-DTE sprint**
  (4×25%: compound +4→+50%, DD 88→82%, collapse 1.3→0%, P2x 57→72%); the DD↔compound dial is **#names**
  (n10 +108%/76% Pareto-best) NOT a lever (arm-2 RXDD/DD-soft retune NULL — RXDD saturates fully-deployed,
  DD-soft 1:1 trade); collapse-relax a non-event (n2/n3 traps). N=100/300/500-stable via the
  `concentration_2x` harness (any DTE via `NOMINAL_CAL_DTE` env). Pre-run VIX-trend/BB hypothesis NULL
  (VXMD). Added G46 (concentrated-sprint DD ask → sweep n-names+DTE not the alloc-dampener params).
  Staged: `experiments/apex_dte_dd/{FINDINGS,SHIP_HANDOFF}.md` (4-field profile edit, user green-lights
  the live real-money sprint). MEMORY.md also compacted 38.4→20KB (over-size, pre-existing).
- 2026-06-26 — "% of stocks at ATH / narrow-breadth as a log predictor of drawdown/call-risk → de-rate
  long calls" (Bagholder feedback) → **NO SHIP, NULL (inverted + redundant)**; read-only EV-by-band mine
  on the existing tape, no MC (~10 min; Fri pre-open ~3.5h budget). The feedback's directional premise is
  BACKWARDS for our buy-weakness sleeve — low %-at-ATH (narrow leadership) is our BEST call EV (+0.091,
  z−65 monotone every window), and what weak-EV exists is redundant with the shipped MWDD lever (G44).
  Already addressed by BDIV+MWDD+F3F+RXDD; the regime-multiplier de-rating it names is the documented
  no-op/inversion. Added G45 (a "froth/narrow-breadth → cut calls" feedback = G19 buy-weakness inversion
  + G40 already-shipped; EV-by-breadth-extreme-band kills it in ~10 min, substrate-robust). Harness:
  `experiments/breadth_ath_dd/`.
- 2026-06-25 — dynamic/regime-conditioned component WEIGHTING (user: "different score combinations across
  regimes/market-structures") → **NO SHIP, comprehensive NULL** (2 read-only pre-tests, no rescore/sweep,
  ~10 min; ~6.5h pre-open budget). G17 triage flagged it as heavily-documented (reweight-trap G35 +
  regime-flip nulls + G26); the one open form (regime-conditioned bear/chop trend de-weight, NEW_LEADS A0)
  killed by the gate-level test: no component has a regime-separable apex-gate signal consistent across the
  classifiers a mechanism can use (composite AND VIX); TREND is the flattest, bb sign-flips across classifiers
  (the noise floor). Added G43 (the component-tercile-vs-regime-base apex-EV-at-the-gate cross-classifier-
  consistency test; the score already has the `d` dynamic blend; a real regime weakness is a Stage-3 sizing
  lever not a Stage-1 reweight). NEW_LEADS A0 Stage-1 reweight half closed-null; chop weakness redirected to
  the open A-MKT sizing lever. Harness: `experiments/regime_reweight/`. FOLLOW-ON: ran the A-MKT
  orthogonality gate (the redirected SIZING form) → **also NULL, redundant with the shipped MWDD lever**
  (FLAT_chop's negative-EV is the McClellan-flat band MWDD already contracts; inverts to good where MWDD is
  off). The regime-aware-chop idea is now closed at BOTH layers (Stage-1 reweight + Stage-3 sizing). Added
  G44 (the per-lever orthogonality 2×2; the candidate's low-EV inverting to good where the overlapping lever
  is OFF = redundancy; a user's regime instinct is often already-shipped under another feature name).
  Harness: `experiments/regime_call_alpha/orthogonality.py`.
- 2026-06-24 (b) — score-fidelity audit (user: "measure how accurately our scores READ the indicators →
  ship if genuinely meaningful"). Autonomous pre-open → **NO SHIP (honest net-dilutive null)**. Audited all
  6 component→indicator mappings; the ONE real discontinuity (MACD 4-branch phase, ~11 macd-pts at
  velocity=0) smoothed NET-DILUTIVE (drops 80%-WR boundary rows = the front-run-the-peak signal; −1-3.4%
  supply; marginal stability — the macd is inherently volatile). Added G42 (a component cliff feeding the
  gate is often signal not artifact; the dropped-rows-apex-WR test is decisive; v42 push-band + verify_value
  gradient-inert in the fidelity domain). Both the forecast-skill (verify_value) and analysis-quality
  (fidelity) angles converge: the scoring layer is at its funded optimum given the inputs; next ship = a new
  INPUT (option/IV, N3). Harness: `experiments/score_fidelity/`.
- 2026-06-24 — verify_value gate-vs-gradient parsimony audit (A2; user invoked /research pre-open, ~7h
  budget) → **NO SHIP, v74 already at the optimum** (config catalog + one 25-sym CWWD rescore A/B, <1h, no
  recalc/tape/sweep). The principle (established read-only this session in `experiments/verify_value/`:
  on the apex predictand the score gradient above the 75 gate is per-trade-inert — calibration no-op,
  lineage ensemble stability-only, MFE-σ flat Spearman −0.011) yields no funded cut: every 75+ gradient-
  shaper was already retired by the v71/v73/v74 lean campaign; CWWD (lone active call dampener) is gated
  [70,75) → funded-irrelevant (0/569 75+ rows change, 75+ set byte-identical). Residual = put-side/70-74
  hygiene (funded-byte-identical but changes dashboard + needs recalc → product-decision STAGE). Added
  G41 (parsimony lead → config + gate-vs-traded-threshold, not a sweep; verify leads can resolve
  "already-done"). A2 closed. Records: `experiments/verify_value/{GATE_AUDIT,FINDINGS}.md`.
- 2026-06-23 — user's "use the marketwave / SPY-breadth correlation to detect breadth collapses → cut
  calls" → **CLOSED AXIS, NO SHIP** (G17 triage + ~30-min read-only confirmation probe, no MC, no
  recalc; weeknight ~11h budget). The idea was already operationalized in TWO live v74 levers (MWDD
  2026-06-05 = "market wave protection"; BDIV 2026-06-11 = "SPY-near-high × breadth divergence"), the v57
  sector-ETF Market Wave was retired, and the literal "collapse → cut calls" core is the crash-artifact
  trap (every collapse cohort is a mean-reversion WINNER in the all-5-levers-off slice; collapse_flag
  +0.274). Added G40 (triage against the SHIPPED-MECHANISM list first, not just the null ledger; the
  confirmation needs no new tape — existing tapes + the mine.py pattern). Harness:
  `experiments/spy_breadth_corr_dd/`. A clean documented closed-axis NO-SHIP (Stage > rush, G13/G17).
- 2026-06-22 — user's short-dated protective-PUT-against-the-call-book hedge (≤3 DTE, Apex + Core) →
  **NULL, no MC** (cheap real-data ledger, ~1.5h, weeknight ~14h budget). Negative-carry tail insurance:
  net-negative every window but the isolated 2020_crash incl 10y-with-COVID (DTE=3 book-net −8.5%/−9.2%
  Core/Apex at the real premium); the cheaper-premium door that flips it positive is CLOSED by real
  `option_prices` (30DTE iv_rv 1.08, ≤5DTE put real/model 1.21×, median vol 0). Added G39 (kill a
  "hedge the calls with a put" idea via overlay ledger + REAL short-DTE premium calibration; the
  achievable-vs-look-ahead exit distinction is the trap; the dead-hold is the free collapse insurance).
  Harness: `experiments/short_put_hedge/`.
- 2026-06-18 — v74-lean cascade retune (the "re-tune on the new substrate" follow-up) → **VALIDATE-c04,
  NO SHIP**, after cheap-null'ing the trend-confirm×VIX tilt (G26 reversal-trap) and the forward-vol
  VVIX/VRP/VIX-term-structure family (G28 level-control). Weeknight budget (~14h). Three leads closed,
  no ship — the honest mature-frontier outcome (regime-signal families now exhausted; next ship = the
  option/IV data-unblock, NEW_LEADS N3). Added G36 (queue ops during a multi-phase sweep:
  bridge-timeout-consumed-by-queue-wait, daemon auto-restart kill+requeue + oversubscription, priority
  anomaly, seed-determinism means oversubscription doesn't corrupt the gate), G37 (PRF size-up
  extrapolation falsified by MC on a saturated/cap-bound substrate), G38 (honest no-ship is valid).
  Harnesses: experiments/{bearchop_trend_dd, v74_cascade_retune, vix_term_vrp}/.
- 2026-06-17 — weatherization component-ensemble verification (user asked to "measure how components
  work in tandem / which signs don't work combined"). DIAGNOSTIC-primary run, ~7h same-day-open budget:
  delivered a forecast-verification scorecard of the 6 components on the apex payoff (TREND dominant-but-
  unskilled + regime-harmful; MACD/RSI skillful, RSI bear-robust; TA a suppressor β−0.63; eff ensemble
  size 3.5/6; trend+macd anti-synergy; ensemble beats best member via the joint 75+ threshold). No
  production change (no clean Stage-3 lever — component-disagreement axis thin on v74-lean; TA is Stage-1-
  only). Leads STAGED (TA-suppressor v75-lean, regime trend/rsi defense, 85-89 trim). Added G35 (the
  ensemble-member verification harness + "dominant ≠ skillful" + suppressor detection + ensemble-vs-best).
  Committed `1d70f9ede`. Harness: `experiments/weather_components/`.
- 2026-06-17 (b) — user pushed "are you sure it can't finish, 6h away" → ran the decisive read-only PROBE
  of the staged TA-suppressor v75-lean (2× `rescore_dump.py` ScoreSimulator arms, baseline vs `W_TA=0`,
  298-sym stride-3, queue #240/#241; `probe_analyze.py`). Result: TESTED-NULL — zeroing TA's weight is a
  dilutive reweight-trap (75+ supply −11%, dropped higher-EV signals than kept in 4/5 windows; pooled
  "accretive" = 2024 Simpson). A multivariate suppressor is NOT a removable component; reweight-null
  RE-CONFIRMED on apex. No ship (correct outcome: probe killed the lead in ~1h). Extended G35 with the
  suppressor-probe lesson. Lesson on the clock: the work fit in 6h — evidence quality was the binding
  constraint, not time.
- 2026-06-12 (b) — N1 dampener ablation EXECUTED + v73 candidate STAGED (3h pre-open budget — Stage >
  rush honored). All 7 remaining pre-v69 dampeners ablated on v72 via sharded ReSim (940k-row baseline
  98.54% exact): WCF + ICH = clear RETIRE (WCF zero put discrimination z=−0.43 — v27 evidence was
  look-ahead artifact; ICH inert on calls ≥75 removes N=1/5y + wrong-way on puts), CWCF/CSWC/SCW =
  marginal retire-leaning (trio union +61% 75+ supply at 50.7% ≫ BE — growth gate decides), CWWD +
  WVD = KEEP (WVD's deleted cohort 41.6% z=−3.32, below BE — the one clear earner). Handoff:
  `experiments/dampener_ablation_v72/SHIP_HANDOFF.md`. Added G34 (ReSim arm ≈18 min/shard — size
  timeouts + add the resume guard up front; `wait | tail` masks timeout exit; killed parent = manual
  shard merge). Third confirmation of the honest-era law: pre-v69 mechanisms mostly don't survive
  honest re-measurement.
- 2026-06-12 — Ship-gate reform + PRF instrument integrated: Phase 5 Stage-1 bullet rewritten for the
  2026-06-11 reform (CI-based W2/W3, FLAG-only pooled W6, report-only N-floor, FLAG teeth, Stage 1-N
  neutrality track, waiver ledger, holdout RE-LOCKED 2026-06-15, gate `--selftest`); Phase 4 gains
  PRF-seeded sizing retunes (`portfolio_response.py --derive`); Phase 6 gains the three-part
  per-version data unit (pack + supply row + PRF materialize). Added G33 (comparison data layer rots:
  missing supply rows, partial packs, honest-recalc-overwritten pre-v69 packs).
- 2026-06-11 — BDIV SHIPPED (`3505c8770`, Stage-3, 5th DD lever + FIRST leading one): user's "Hindenburg
  omen before drawdowns" ask → episode-onset mine on the FRESH v71 tape killed the literal omen family
  (0/24 omen days precede onsets; omen-day entries are winners; all discrete flags sign-flip) and shipped
  the survivor: pre-top breadth divergence (SPY near 60d high + breadth rolling over), prox-ramp ×
  gap-Gaussian, NO DD-gate (structural crash guard instead — 2022 delta exactly 0.0). Phase D N=500×10
  T1-T7 PASS: 5y WorstDD −3.0pp AND compound +21%; dip DD −14.0pp at +49%; collapse=0. Added G31
  (mid-run engine edits hang Windows-MP sweeps; explicit `*_ENABLED=0` baselines post-ship) + G32
  (omen asks → onset event study; omens are winner-cohorts; leading levers may skip the DD-gate when a
  structural condition replaces it). Review WARN logged: portfolio_engine missing SVR/MWDD/TVDD/BDIV
  live sizing (chip spawned). Harness: `experiments/dd_onset_omens/`.
- 2026-06-10 — v71 SHIPPED (`04044b21b`): the integrity-audit fix campaign (handoff-driven run). F2 SPY-weekly
  look-ahead fix + F4 PIT-mcap + F1 wave guards + F3 barrier-cache rebuild (83M rows), and FOUR honest-A/B
  retirements (mis_stress, JA4, MCD, Sector Market Wave) → 75+ supply +83% at WR15 +1.9pp (5y assess), full
  10y recalc + assess + pack + N=300 smoke (collapse=0 incl 2020_crash). Added G29 (worktree sys.path /
  queue-DB / --cwd traps) + G30 (sharded ReSim honest A/B pattern + PIT-proxy rule for snapshot attributes).
  Harness: `experiments/integrity_audit_2026_06/`.
- 2026-06-09 (d) — VIX weekly-MACD / velocity / acceleration as a portfolio DD lever (VXMD, user-requested)
  = NULL, MC-confirmed (built env-gated OFF null-infra + verify + N=100×8 screen, no ship). The naive
  weekly-MACD resample is WITHIN-WEEK LOOK-AHEAD (fakes a 73%-of-DD-dollars low-EV cohort); point-in-time
  (daily-equiv EMA60/130/45 or last-completed-week) = HIGH-EV (the dead-hold bounce runners) + crash-artifact
  (bear −0.17 / bull +0.09) → no Pareto. Whole VIX-momentum axis dead as a DD lever; only the VIX LEVEL band
  (RXDD) works. **Follow-up (same run): the user clarified they wanted a WR15 *scoring* improvement (VIX
  weekly reading → regime/post multiplier), not DD — ALSO NULL** (read-only `wr15_regime.py`, no recalc):
  the production composite already uses VIX level + 10d-velocity, so weekly-MACD is redundant (vix<20
  orthogonal control z≈0), and for calls rising-VIX is HIGHER-WR15 (suppressing hurts, 90+ −5.72pp). Added
  G28 (mine the engine-faithful POINT-IN-TIME feature, never a look-ahead resample; an "X spikes→calls
  collapse" pain is an open-position/dead-hold problem, not an entry-sizing lever; and check what the regime
  composite ALREADY ingests + control for it in W1 before testing a "new reading into the multiplier"). NOTE:
  monte_carlo.py VXMD left UNCOMMITTED (entangled with a concurrent calendar_hold ship). Harness/record:
  `experiments/vix_weekly_v70/`. A clean documented NULL ("Stage > rush").
- 2026-06-09 (c) — retracement-conditioned / delayed CALL entry on extended/late-in-run signals
  (NET/RKLB/IREN) = PREMISE NULL, killed cheap (no MC, no ship/stage). Winners win by direct continuation
  (~88%; only ~12% dip-then-resweep, identical in late&extended); "wait for the dip" is anti-selective
  (winners dip 38% vs all 69% → enters losers, misses ~60% of runners); run-position WR flat. Added G27
  (entry-timing premise test = path-shape + missed-winner on the ledger, pre-MC; delayed entry is
  anti-selective). Clean documented NULL ("Stage > rush"). Harness `experiments/retrace_entry_v70/`.
- 2026-06-09 (b) — off-year drawdown miss-catalog → sector-concentration exposure cap = NULL (no ship/stage,
  cheap tape-mine only). The off-year DD is a diffuse cross-sector momentum-factor reversal, not a sector/
  timing/crowding crash: bags ~book-sector-distributed, temporally diffuse (39-47 wks), and same-sector-
  concurrency→bag-rate FLIPS sign by year (+4.7 reversal yrs / −5 persistent yrs). Added G26 (concentration
  DD ideas must pass concentration-vs-book-baseline + orthogonality-vs-total-concurrency + sign-stability-
  across-regimes; most fail the last because concentration is good in the trend, bad in the reversal).
  NEW_LEADS #12 → tested-null. Harness `experiments/offyear_dd_catalog/`. Clean documented NULL.
- 2026-06-09 — 2024-IT-factor deep-dive → mined the persist-vs-crash discriminator. Decisive finding (no ship):
  2024 is explosive because it's a LOW-MOMENTUM-CRASH year (the off-years bleed via −90% dead-hold bags; the
  edge is 70% LOSS-side avoidance), and the realizable scoring-side lever = direct option-skew, which is a
  CONFIRMED residual to shipped SVR on the tradable 75+ cohort (opt_skew|semivol_r t=+3.16 vs +0.15) but
  STRUCTURALLY un-shippable (premium-dominated MC-blind + 1.3y option-data-locked + covered window net-negative).
  STAGED with mechanism design + ship-path. Added G25 (option-implied signals can be real-yet-ungate-able;
  SVR/semivol_r is a weak proxy not the edge; the partial-regression-on-win-rate is the 5-min decisive test).
  Harness `experiments/year_2024_factor/` + `OSK_SHIP_HANDOFF.md`. A clean documented STAGE ("Stage > rush").
- 2026-06-08 (b) — intraday-vs-overnight ("SPY gains in market hours" arb) = NULL for the options strategy
  (no ship; documented null + a quantified backtest-realism finding). Premise is index/era-specific and
  INVERTS on our universe (overnight premium, not intraday); directional-feature bridge flat on apex15
  (rel_strength ~47% trap); entry-timing is a −1.22pp realism HAIRCUT (signals gap up +17.5bps overnight),
  not an arb. Added G24 (measure a famous anomaly on OUR universe + map it to what we trade; "entry timing"
  on a weeks-long options sleeve = realism question; one OHLC pull + in-memory on the existing ledger, no MC).
  Harness `experiments/intraday_overnight/`. A clean documented NULL ("Stage > rush").
- 2026-06-08 — 5th DD-sizing lever hunt = NULL (no ship; staged DQT env-gated OFF + closed the seam). The
  DD-sizing well is DRY after RXDD/SVR/MWDD/TVDD + F3F: fresh-axis screen ruled out NH/NL (fails orthogonal
  slice), breadth-velocity (McClellan-redundant), %above-EMA, VIX-velocity; the conviction-tier residual
  (DQT, LOW 75-79 = 44% of DD-dollars) verify-passed but NULLED at N=300×10 (velocity-engine + crash-artifact
  -coupled, N=100 "Pareto" flipped sign at N=300). Added G23 (well-is-dry-after-4-levers + orthogonal-slice
  is the decisive cheap test + marginal-N=100-Pareto-is-noise; pivot to a NEW class: option-pricing /
  model-fidelity). A clean documented NULL (cf. the 2026-06-06 CDR null) — "Stage > rush" over a forced
  marginal/non-default ship. Harness: `experiments/dd_residual2_v70/`.
- 2026-06-07 — TVDD TRIN volume-flow neutral-band CALL DD dampener SHIPPED (4th orthogonal Apex DD lever
  on RXDD+SVR+MWDD+F3F; N=500×10 incl COVID Stage-3 T1-T7: 5y WorstDD −3.1pp AND compound +17%,
  2020_crash −8.3pp, collapse=0; a Pareto). Added G21 (mine the DD-ACTIVE subset dd≥DD_MIN for a new
  DD-sizing lever — the whole-tape mine diluted TRIN's −0.060 trough to +0.041; the residual well is NOT
  necessarily dry after N levers; need low-EV AND high-dd_conc COINCIDING, proven in the all-levers-off
  slice) + G22 (VIX-velocity is monotonic-EV but anti-aligned for contraction → RXDD-refinement micro-lead;
  concurrency-DD now captured). Confirms G14/G19. Harness: `experiments/dd_residual_v70/`.
- 2026-06-06 — cut-to-redeploy / CDR investigation (user-requested "cut the down-60% bag for the 88"):
  re-confirmed the v32 REALLOC null on honest-v70 across blunt + 11 regime-gated configs + N=100/300/500
  (DD-neutral, compound = seed-noise, dip DD +4.5pp worse, collapse 0). NOT shipped; CDR kept env-gated OFF
  as null-infra. Added G20 (displacement is a hard null + the dead-hold recovery-clusters-where-conviction-
  fires insight + mine the dh_pop/dh_expiry outcome split before any cut/hold mechanism). Reinforces G16/G17.
  Harness: `experiments/dead_hold_realloc_v70/`.
- 2026-06-05 (c) — MWDD McClellan breadth-momentum flat-band call dampener SHIPPED (2nd orthogonal DD
  lever on v70 Apex; N=500×10 incl COVID: −2.6pp 5y / −5.5pp 22-now WorstDD, every window DD down,
  collapse=0, compound flat; Stage-3 T1-T7). Added G19 (a user's named regime signal can be directionally
  inverted — mine EV-by-band, ship the FLAT mid-band not the crash extreme + a panic-exclusion; RXDD
  VIX-20-28 pattern generalizes). Confirms G14/G17. Harness: `experiments/market_wave_dd_v70/`.
- 2026-06-05 (b) — SVR semivol_r skew-bridge SHIPPED (the option-pricing win from the apex-speed
  overnight; Stage-3, 5y WorstDD −5.8pp AND compound +28.6%, collapse=0 incl COVID). Added G18
  (stamp an intrinsic per-signal feature on the outcome instead of threading a map → sidesteps G9).
  Confirms G14 (the live lever is option-pricing/skew, not sizing). Harness: `experiments/apex_speed_v70/`.
- 2026-06-05 — apex-speed overnight (comprehensive sizing/exit/regime null on v70 Apex): added G16
  (capital-velocity is the dominant law; always screen with COVID; N=500 gate downgraded a 0.2%-collapse
  Phase-C winner) + G17 (triage user ideas vs the documented-null ledger first; read-only re-eval for
  regime mult). Fixed G15 (holdout removed → None). EXR/TSL built env-gated OFF as validated-null infra.
- 2026-06-04 — created from the RXDD overnight run (VIX-band call dampener, Stage-3, shipped v70 Apex:
  5y WorstDD −5.6pp AND compound +9.4%, collapse=0). Seeded LIVING GOTCHAS 1–15 + the self-update
  protocol. Harness: `experiments/regime_dd_v70/{mine,sweep}.py`; precedent record:
  `experiments/regime_dd_v70/{FINDINGS,SHIP_HANDOFF}.md`.
