---
name: portfolio-ops
description: Operate the live Portfolio tracker — the persisted forward ledger that materializes a real portfolio profile against real scores (portfolio_engine.py + database/models/portfolio.py). Covers looking up which profile is actually live (never assume), the sync/reset/status/pending/notify CLI, the auto re-qualification sweep (version_sweep vs strategy_sweep) that fires on any ship, execution-timing canon (BUY-window gating, SELL always-live), the Allocator as the primary execution surface, and how to switch the live profile safely. Use when the user asks "what's the live portfolio profile", "why did SYM get sold/swept", "run a portfolio sync/reset", "is a buy pending right now", "switch the live run to Core/Apex", "why are the pushes late", or anything about `trader portfolio ...` / `/api/portfolio/*` / the Portfolio or Allocator pages.
---

# /portfolio-ops — operate the live Portfolio tracker

The live **Portfolio** page is not a dashboard — it is a **persisted forward
ledger**: every `trader update` re-runs the deterministic cascade engine
(`backtest_cascade`-equivalent logic inside `portfolio_engine.py`) from
`start_date` to the last completed session and materializes exactly what a
real account would be holding. There is no order book, no pending-order
state — only realized-and-frozen history plus one open ledger that adopts
whatever scoring version / portfolio config is currently active. Read
[frontend.md](../../docs/frontend.md) "Portfolio.js" + "Portfolio backend"
and [deploy.md](../../docs/deploy.md) "Live Portfolio — auto
re-qualification on a ship" before operating on it; skim
`portfolio_engine.py` (source of truth — 1626 lines, no CLI parser beyond
`_cli` at the bottom) and `database/models/portfolio.py` for the schema.

## GUARDS

1. **PROFILE IS A LOOKUP — never assume which one is live.** The default
   profile key (`portfolio_profiles.DEFAULT_PROFILE_KEY`, `"core"` as of
   2026-07) is what a *brand-new* run would start as
   (`portfolio_engine.get_or_create_run()` only reads
   `portfolio_engine.DEFAULT_PROFILE` when NO active `PortfolioRun` row
   exists yet). It is **not** what the existing live run is actually on.
   Query the truth directly:
   ```bash
   curl -s http://127.0.0.1:5000/api/portfolio/state | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('run',{}).get('profile'))"
   # or: python trader.py portfolio status   (prints "Run: <name> (<profile>) | ...")
   ```
   **As of 2026-07 the live run's profile is `apex`** — switched from
   `core` by commit `3a585c2ed` (2026-06-22), which ALSO re-defined what
   "apex" even means: it re-set the Apex profile to the **15-DTE
   risk-budget elbow** (15 DTE / TP+30 / SL−85 / 90% gross / 4 names / hold
   13 calendar days, ~2.4% collapse budget, user-approved) via new
   profile-overridable engine keys (`PROFILE_OPTION_ATTRS`,
   `_derived_engine_pricing` in `portfolio_profiles.py`). Meanwhile
   `algorithm_versions/portfolio_profiles.json`'s own `apex` description
   still reads *"NOT the default (live ledger is Core / 30 DTE)"* — that
   line is stale prose describing a pre-2026-06-22 world; the JSON's
   numeric `params` block for `apex` IS current (15 DTE/SL−85/etc.), only
   the "not the default" sentence is wrong. known-issues.md's "CURRENT SHIP
   STATE — verified 2026-06-17" header is also one ship behind (still says
   "default profile = Core"). **Trust `/api/portfolio/state` or `trader
   portfolio status` over any doc prose.**
2. **A STAGED reversal is sitting un-applied.** `experiments/apex_dte_dd/`
   (`FINDINGS.md` + `SHIP_HANDOFF.md`, 2026-06-30) found the live 15-DTE
   Apex sprint is **dominated** by a 30-DTE version (median compound
   +4%→+50% or +108%, worst DD 88%→82% or 76%, collapse 1.3%→0%) — a
   4-field (Option A) or 4-field-plus-8-more/12-field-total (Option B) edit
   to `portfolio_profiles.json`'s `apex.params` block (see
   `SHIP_HANDOFF.md` "How to apply"), explicitly **"not
   auto-applied — apply at the user's green-light"** because it touches
   the real-money tracker. Do not silently apply it; surface it if asked
   "should I improve the sprint" and confirm with the user first.
3. **STOP-AT-2x is a MANUAL discipline, not an engine feature.** The Apex
   sprint's whole edge is P(2x)-in-bounded-time; the engine has **no
   auto-stop / auto-rotate-at-2x**. Held continuously past 2x, the sprint
   config is **negative-compound** (documented: −37.1% 5y return / 79.6%
   5y DD at the pre-elbow config; the risk-budget elbow's own held-forever
   number is the ~2.4% collapse budget accepted for the STOP-AT-2x use
   case, not for indefinite holding). If the user asks about "the sprint
   just keeps running," this is the answer — a human must decide to skim
   profits and rotate; an "auto-rotate-at-2x" feature was **considered and
   explicitly deferred** (known-issues.md 2026-06-17 entry).
4. **Sweep exits are not a bug.** Any scoring ship (`ALGORITHM_VERSION`
   bump + recalc) or ANY portfolio-stage edit (`strategy_config.py` /
   `portfolio_profiles.json`) changes `_strategy_fingerprint()` and/or
   `AlgorithmVersion.get_active_scores_version()`. The **very next**
   `trader update` runs a re-qualification sweep: every open position's
   entry-date score (or latest completed-session score) is re-checked
   against `_min_call_threshold()` under the NEW rules; survivors ride,
   disqualified holdings exit tagged `version_sweep` (scoring ship) or
   `strategy_sweep` (portfolio-only ship) — see GUARD 6 for the timing
   rule. **This is EXPECTED after every ship** — do not treat sweep exits
   as a defect unless the sweep count is surprising given what actually
   changed (a sizing-only mechanism like SPREAD_TILT is
   qualification-neutral by construction and should sweep ~nobody).
5. **Missing score rows DEFER, never force-exit.** If the new version has
   no `Score` row yet for a held `(symbol, entry_date)` (mid-recalc
   backfill), `_requalify_position()` returns `'unknown'` — the position
   stays untouched and the transition **re-fires every sync** until
   resolvable. It will never silently exit on missing data. If a backfilled
   score later re-qualifies a pending sweep, it is **rescinded** (a
   `✅ Keep` notice fires) — this is the "graceful sweep" behavior shipped
   2026-06-11.
6. **Exit timing is never backdated.** A disqualifying sweep exits at the
   close of the **first session that completes AFTER detection** — never
   backdated onto an already-closed bar (the pre-2026-06-11 behavior
   recorded a hard 0% P/L on same-day entries and is retired). A genuine
   barrier touch (TP/SL/hard-sell/dead-hold) on the SAME day as a due sweep
   takes precedence — the barrier fired intraday, before the close the
   sweep would have filled at.
7. **Execution timing is asymmetric — BUY is gated, SELL is always live.**
   Per the Execution Timing Canon (`trading-strategy.md`, wired
   2026-06-12): provisional BUY pushes only fire inside
   `BUY_ALERT_FROM_ET` (15:25 ET) → close (`portfolio_engine.py` constant;
   ~26% of morning-qualified signals fade by the close, the ~15:45 read is
   ~92% close-faithful). SELL pushes (barrier touches, sweeps, dead-hold
   pops) fire live throughout market hours — they're price events, not
   score reads, so there's no "provisional" fade risk. Never expect a buy
   push before 15:25 ET; do expect sell pushes any time.
8. **Real pushes come from the scheduled `notify` pass, not the heavy
   update.** The ~20-min `trader update` finishes its alert pass at an
   unpredictable time relative to the 15:25 window (a 15:00 run finishes
   too early, a 15:45 run finishes after the close). The precise-timed
   pushes are `trader portfolio notify`, scheduled via Task Scheduler at
   **08:45 ET** (`TraderPortfolioNotifyMorning`) and **15:30 ET**
   (`TraderPortfolioNotifyClose`) — installer
   `scripts/install_portfolio_notify.ps1`. If pushes are late/missing,
   check these two scheduled tasks are registered and firing before
   assuming an engine bug (route to `/debug-pipeline` for the queue/daemon
   angle).
9. **A known fill-timing race exists (open chip, not yet fixed).** The
   engine's session-completion gate uses `MARKET_CLOSE_HOUR_ET = 16`
   (16:00 ET), but the separate `close-update` phase that pulls fresh
   option chains / finalizes fakeout-prone scores typically lands
   ~16:20–16:30 ET. A position can be filled by the engine's 16:00-gated
   sync using a score that a same-day intraday fakeout later corrects by
   the ~16:30 close-update — documented incident: ADUR entered on a
   79→38 same-day fakeout, finalized by close-update AFTER the engine's
   fill (known-issues.md 2026-06-11 BDIV entry, "the engine's 16:00 session
   gate vs ~16:30 close-update" — a spawned-but-still-open follow-up chip).
   If a fill looks wrong in hindsight, check `trader intraday-drill SYM
   <date>` (`/debug-scores`) before assuming the ledger itself is broken.
10. **The MTM equity CURVE has a known ~$295 (~0.7%) display divergence**
    from `run_cascade_backtest`'s curve, dated to ~2026-06-08 — NOT a
    parity bug (positions/closes/pnl_pct/final-cost-equity are validated
    bit-exact; only the intraday MTM *marking* nuance on the curve differs,
    likely from the 2026-06-11 graceful-sweep `pending_requal` mark path).
    Known, spawned as a separate chip, safe to ignore when reconciling
    position-level P&L; only relevant if asked specifically why the curve
    doesn't match the backtest curve to the penny.

## 1. Look up the live state (always do this first)

```bash
curl -s http://127.0.0.1:5000/api/portfolio/state          # full payload: run + positions + equity_curve_mtm + summary
curl -s http://127.0.0.1:5000/api/portfolio/pending         # execution_window + would_open/would_exit/carryover (read-only, no writes)
python trader.py portfolio status                           # one-screen text summary (profile, equity, DD, open/closed counts)
```
`/api/portfolio/state` and `GET|POST /api/portfolio/sync` both force
`Cache-Control: no-store` (`api.py` `_NO_STORE_PREFIXES`) — you will never
read a stale cached copy of live portfolio data through the API.
`/api/portfolio/pending` is served from a 75s TTL cache with a single-flight
lock (a heavy dry-run sync, ~5-60s cold) so concurrent polls never stack —
the `execution_window` block inside it is always recomputed fresh even on a
cache hit.

## 2. `trader portfolio [sync|reset|status|pending|notify]`

Dispatches to `portfolio_engine._cli` (`sub` positional, default `sync`;
validated against exactly this 5-tuple — verified in `portfolio_engine.py`
`_cli()`). Each writes `run/positions/snapshots` **except** `pending`, which
is a pure dry-run (`sync(..., dry_run=True)`, returns before printing the
summary block).

| Command | What it does | Sends pushes? | Writes? |
|---|---|---|---|
| `sync` | Deterministic re-materialization from last-processed-date to today's last completed session | No (`send_notifications=False`) | Yes |
| `reset` | **Wipes** `PortfolioPosition` + `PortfolioEquitySnapshot` + `PortfolioRun`, then re-syncs fresh from `START_DATE` (2026-06-01) | No | Yes — destructive |
| `status` | Loads the active run, prints one summary block (no sync) | N/A | No |
| `pending` | Dry-run preview of the live action pass — prints would-be closes/opens/sweeps/alerts **without writing or pushing anything** | No | **No** |
| `notify` | Scheduled lightweight alert pass — full sync **WITH** pushes; this is what the 08:45/15:30 ET Task Scheduler jobs actually run | **Yes** | Yes |

`reset` is destructive and rebuilds the entire realized history from
`START_DATE` — only use it to deliberately start the tracked portfolio over
(it does not preserve frozen closed-trade history). Never run `reset` to
"fix" a display glitch; use `sync` first.

`GET|POST /api/portfolio/sync` is the API-level equivalent of `trader
portfolio sync` (`send_notifications=False` — the API path never pushes).

## 3. Reading the re-qualification sweep (version_sweep vs strategy_sweep)

`_advance()` computes two independent booleans every sync:
- `version_changed` — `run.version_id != AlgorithmVersion.get_active_scores_version().id`
- `strategy_changed` — `run.strategy_fingerprint != _strategy_fingerprint(cfg, run.profile)` (a SHA1 over cascade tiers, TP/SL, dead-hold knobs, MAX_POSITIONS, and every Stage-3 dampener's enabled-flag + constants: RXDD/SVR/MWDD/TVDD/BDIV/SPREAD_TILT)

Either sets `transition = True` and the sweep_reason is
`'version_sweep' if version_changed else 'strategy_sweep'`. Per open
position, `_requalify_position(version_id, p, min_thr, D)` returns:
- **`'hold'`** — entry-date score OR latest completed-session score under the
  new version still clears `_min_call_threshold` → keep riding.
- **`'sweep'`** — affirmatively disqualified on both reads → marked
  `sweep_pending=True`, exits at the close of the next actionable session
  (GUARD 6).
- **`'unknown'`** — no score row yet under the new version for that
  `(symbol, entry_date)` → deferred, re-checked every sync, never force-exited
  (GUARD 5).

**Diagnosing a specific sweep:** query
`PortfolioPosition.select().where(status='closed', exit_reason.in_(['version_sweep','strategy_sweep']))`
for the position, then compare `entry_score` (score at entry under the OLD
version) to what `Score.get(symbol=X, date=entry_date, version=NEW_version_id)`
resolves to — if the new row is below `_min_call_threshold()` for the run's
current `cfg`, that's the sweep cause. Cross-reference the ship that
triggered it against [known-issues.md](../../docs/known-issues.md)'s CURRENT
SHIP STATE block (each ship entry ends with an EXPECTED-sweep note, e.g. "the
first `trader update` under v71 auto-runs the re-qualification sweep …
EXPECTED").

## 4. Execution-timing states (the Allocator banner)

`portfolio_engine.execution_window_status()` returns one of four `state`
values, consumed by the Allocator page banner (`GET /api/portfolio/pending`,
polled every 120s + a 30s countdown tick per `frontend.md`):

| State | When | Meaning |
|---|---|---|
| `closed` | non-trading day, or `et.hour >= 16` | Scores are FINAL for the day; shows the next window date |
| `pre_open` | trading day, before 09:30 ET | Carry-over buys fill at the open (~−1.3pp haircut vs the model's close fill) |
| `provisional` | 09:30 ET → 15:25 ET | Amber — partial-day scores, ~26% of morning signals fade by the close |
| `window` | 15:25 ET → 16:00 ET | Green — scores near-final, buy now; countdown to close |

`build_pending_payload()` is what actually answers "is a buy pending right
now": it runs `sync(dry_run=True)` and returns `execution_window` +
`pending.would_open` / `pending.would_exit` / `pending.carryover` — read
this, not the equity snapshot, when the user asks "what will fire at the
close today."

## 5. Switching the live profile

There is **no CLI flag** for this — `PortfolioRun.profile` is a plain DB
column, and the only precedent is a direct row edit (the 2026-06-17
Apex→Core restructure: "the live `PortfolioRun` DB row was migrated
apex→core"). Procedure:

```bash
python -c "
from database.models.portfolio import PortfolioRun
run = PortfolioRun.get_active()
print('before:', run.profile)
run.profile = 'core'          # or 'apex' / 'sentinel' — must be a valid portfolio_profiles.json key
run.save()
"
python trader.py portfolio sync      # re-materialize under the new profile; fires strategy_sweep next
```
Before switching, confirm the target key is qualification-neutral or expect
a `strategy_sweep` — a profile swap almost always changes
`_strategy_fingerprint` (different tiers/TP/SL/MaxPos), so the next sync
will re-qualify every open position against the new profile's
`_min_call_threshold`. This is real trading behavior (the strategy actually
changed), not a bug — warn the user before doing it on the real-money
tracker, and confirm which profile they actually want (GUARD 1's lookup) so
you don't flip it and then discover it was already the one they wanted.

## 6. Post-ship reconciliation checklist

After ANY scoring ship or portfolio-stage ship that touches the live
tracker (see [ship-version](../ship-version/SKILL.md) /
[ship-portfolio](../ship-portfolio/SKILL.md)), verify:
1. `python trader.py portfolio status` — profile still what you expect,
   equity/DD numbers sane.
2. Next `trader update` (or manual `trader portfolio sync`) ran the sweep —
   check for `version_sweep`/`strategy_sweep` closes if the ship was
   expected to disqualify anything; check for ZERO sweeps if the ship was
   sizing-only/qualification-neutral (e.g. SPREAD_TILT swept ~nobody by
   design).
3. If touching sizing/dampener math specifically, re-run the parity harness:
   ```bash
   python experiments/portfolio_engine_parity/validate.py
   ```
   (read-only fresh replay vs `run_cascade_backtest`, `_persist`
   monkeypatch-captured — the standard bit-exactness re-validation used
   after every Stage-3 dampener ship: RXDD/SVR/MWDD/TVDD/BDIV/SPREAD_TILT
   all cite this harness in their known-issues.md ship entries).
4. Confirm the backend was restarted (`& C:\Development\server.bat restart
   -Service trader-api` via PowerShell **backgrounded**, `run_in_background:
   true` — `server.bat` lives one directory ABOVE this repo, not inside it;
   see [frontend-ops](../frontend-ops/SKILL.md)) if
   `api.py`/`strategy_config.py`/`portfolio_param_manifest.py` changed, or
   `/api/portfolio/state` will keep serving stale config.
5. `trader temporal-refresh --profiles all` if the ship was portfolio-stage
   (never a full `trader assess` for a portfolio-only change — see
   [run-assessment](../run-assessment/SKILL.md)).

## Evidence / see also

- [frontend.md](../../docs/frontend.md) "Portfolio.js" + "Allocator.js" — page-level behavior, the pending-actions card, notification message formats.
- [deploy.md](../../docs/deploy.md) "Live Portfolio — auto re-qualification on a ship" — the canonical short version of section 3 above.
- [trading-strategy.md](../../docs/trading-strategy.md) "Execution Timing Canon" — the full BUY/SELL asymmetry rationale and the −1.2 to −1.4pp next-open cost evidence.
- `known-issues.md` CURRENT SHIP STATE — each dated ship block ends with the sweep-type it caused and a one-line revert; cross-reference here when diagnosing an unexpected sweep. **Header says "verified 2026-06-17" and is stale on the live profile — see GUARD 1.**
- `experiments/apex_dte_dd/{FINDINGS,SHIP_HANDOFF}.md` — the staged 30-DTE sprint upgrade (GUARD 2).
- `experiments/portfolio_engine_parity/validate.py` — the live-engine-vs-backtest bit-exactness harness.
- [debug-pipeline](../debug-pipeline/SKILL.md) — if pushes are late/missing and the scheduled-task angle (GUARD 8) doesn't explain it.
- [ship-portfolio](../ship-portfolio/SKILL.md) — shipping a new dampener/knob that this tracker must also apply (the 13-consumer wiring list includes `portfolio_engine.py` sizing + `_strategy_fingerprint`).

## Self-update

If you hit a trap this skill missed, append it to GUARDS here AND to
[.claude/docs/traps.md](../../docs/traps.md) in the same session.
