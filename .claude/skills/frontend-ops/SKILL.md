---
name: frontend-ops
description: React 18 + Tailwind + Chart.js dashboard and Flask API feature work — starting/restarting the dev server, adding an API endpoint or a new page, wiring the version selector and profile toggles, and knowing which edits need a backend restart vs which hot-reload for free. Use when the user asks to add a chart/page/endpoint, wants the dashboard running to eyeball a change, reports the frontend showing stale data after a backend edit, or asks "why isn't my change showing up".
---

# /frontend-ops — React dashboard + Flask API feature work

Covers the two halves of this app's presentation layer: the Flask REST API
(`api.py`, port 5000) and the React 18 + Tailwind + Chart.js dashboard
(`src/`, port 3000). Most tasks here are additive UI/API work, not scoring or
portfolio-mechanism changes — if you're touching `Score.overall` or a cascade
knob instead, this is the wrong skill (see [scoring-algorithm.md](../../docs/scoring-algorithm.md) /
[trading-strategy.md](../../docs/trading-strategy.md) and the `ship-version` /
`ship-portfolio` skills).

## GUARDS

1. **Restart the backend BACKGROUNDED, or it hangs the agent forever.**
   `api.py` (and everything it imports — `strategy_config.py`,
   `backtest_cascade.py`, `portfolio_param_manifest.py`) is loaded once at
   process start; editing those files does nothing to the running server
   until it restarts. `server.bat restart trader-api` launches the service as
   a detached child holding a captured stdout pipe open — a human's
   interactive terminal has a real console stdout and returns fine, but an
   agent's foreground tool call never sees EOF and blocks until timeout. The
   fix is **always** to run it via the PowerShell tool with
   `run_in_background: true`:
   ```powershell
   & C:\Development\server.bat restart -Service trader-api
   ```
   Never route this through the Bash tool's `cmd.exe /c "server.bat ..."` —
   in this environment that prints the cmd banner and **silently no-ops**
   (exit 0 in under a second, nothing actually restarts), leaving stale code
   served with no error to tell you. `server.bat` itself is **not in this
   repo** — it's `C:\Development\server.bat`, one directory above the Trader
   checkout, a thin `pwsh -File server.ps1` dispatcher. Verify the restart
   actually happened:
   ```bash
   curl -s http://127.0.0.1:5000/health
   ```
   Expect `{"status": "healthy", ...}` with a fresh `timestamp`. Full
   root-cause writeup: `.claude/docs/process.md` "Restarting the dev server"
   + auto-memory `feedback_server_restart.md`.

2. **The frontend (`:3000`) hot-reloads `src/**` — never restart it for a
   React edit.** Only the backend needs the dance above. If a `.js` change
   under `src/` isn't showing up in the browser, that's a CRA/webpack dev-server
   issue (stale browser cache, a syntax error CRA swallowed, or the dev server
   itself not running) — not a "needs restart" issue. Start it with the
   Preview tool, not raw npm:
   ```
   preview_start({ name: "frontend" })
   ```
   `.claude/launch.json` already defines a `frontend` config
   (`npm start`, port 3000) — confirm it before assuming it's missing:
   ```bash
   cat .claude/launch.json
   ```
   If it's ever absent, create it with exactly that shape before calling
   `preview_start` (see the tool's own instructions for the file format).

3. **Which edits need which restart — don't guess, check the import graph.**
   | You edited... | Needs backend restart? | Needs anything else? |
   |---|---|---|
   | `src/**/*.js`, `src/**/*.css` | No — hot reload | — |
   | `api.py` | Yes | — |
   | `strategy_config.py` | Yes (api.py imports it) | + drift-guard (`tests/test_strategy_config_drift.py`) if you changed a value, not just added an endpoint |
   | `backtest_cascade.py` / `backtest_cascade_15dte.py` | Yes | — |
   | `portfolio_param_manifest.py` | Yes | — |
   | any module those files import (indirect) | Yes | trace it — the import graph is the source of truth, not this table |
   | `algorithm_versions/portfolio_profiles.json` | No — read fresh from disk on every request (`portfolio_profiles.py`'s `load_registry` has no caching) | — |
   When unsure whether a module is on api.py's import chain, run
   `gitnexus_impact({target: "<module or function>", direction: "upstream"})`
   before editing — per this repo's CLAUDE.md, that's mandatory before any
   symbol edit anyway, and it will surface `api.py` as a dependent if it's on
   the path.

4. **A new score version needs an `AlgorithmVersion` row before the dropdown
   sees it — this is a data problem, not a frontend one.** `ScoreVersionSelector`
   (`src/components/ScoreVersionSelector.js`) is a dumb presentational
   component; the list it renders comes from `GET /api/score/versions`, which
   is backed by `api.py`'s `_score_version_catalog()` — a **DB-driven query
   over `AlgorithmVersion` rows** (30-second bucketed cache, not a hardcoded
   list). If you just shipped a new version and don't see it in the selector:
   verify the `AlgorithmVersion` row exists and score rows are backfilled
   (`--score-versions vNN`, per [ship-version](../ship-version/SKILL.md)) — do
   not "fix" this by editing `ScoreVersionSelector.js` or `api.py`'s catalog
   function, there's nothing to wire in either file.

5. **Chart signal overlays are arrow markers, not candle recoloring.** New
   price-chart signal visuals (in `PriceChart.js` / `*Chart.js`) follow the
   existing arrow-marker convention (see `PriceChart.js` "Signal indicator
   dot" — green=call/red=put, rendered at signal date + close price) — do
   **not** recolor candles to encode a signal. And gate any new visual
   encoding on an honest forward hit-rate first (per the standards bar) before
   spending UI time on it; don't build the chart before the signal is
   validated. (auto-memory `feedback_arrow_markers_over_candles.md`)

6. **Docs use stale naming for the live tracker — verify the current profile,
   don't parrot the doc.** `frontend.md` describes `Portfolio.js` as "the
   **v70 Apex** portfolio tracker," but the tracker follows whatever
   `PortfolioRun.profile` currently is, and that value has moved before (Apex →
   Core default rename + Apex repurposed as opt-in fast-2x sprint, 2026-06-17
   restructure — see known-issues.md "CURRENT SHIP STATE") and can move again.
   Never hardcode which profile is "the live one" in new code, comments, or
   user-facing copy — look it up:
   ```bash
   curl -s http://127.0.0.1:5000/api/portfolio/state | python -c "import json,sys; print(json.load(sys.stdin).get('profile'))"
   ```
   or read `algorithm_versions/portfolio_profiles.json` for the profile's
   params. Live-tracker semantics (forward ledger, re-qualification sweeps,
   push-notification cadence) belong to the `portfolio-ops` skill — this
   skill only covers the *page/component* layer.

## 1. Stack map

```
Backend:  api.py (Flask, port 5000) — REST only, no server-rendered HTML
Frontend: src/ (React 18 + react-router-dom 6 + Tailwind 3 + Chart.js 4 /
                react-chartjs-2 5 + lucide-react icons + axios)
Dev tooling: react-scripts 5 (Create React App, not Vite) — package.json
             "scripts": start / build / test / eject / generate-icons
```

```
src/
├── context/StockContext.js     # Global state, API calls, filters
├── pages/Dashboard.js          # Stats, ScoreVersionSelector, FilterBar, StockTable
├── pages/StockDetail.js        # Per-stock analysis, tabbed (daily/weekly)
├── pages/Assessment.js         # Backtest results, DTE toggle, profile toggle
├── pages/Historic.js           # Peak signal events, roll-up/re-entry pills
├── pages/MarketTrends.js       # Regime + breadth time-series charts
├── pages/Backtest.js           # Deterministic backtest runner
├── pages/Allocator.js          # Primary execution surface (/allocator)
├── pages/Portfolio.js          # Live portfolio tracker (holdings pie + growth)
├── pages/PortfolioProfiles.js  # Sentinel/Core/Apex profile comparison
├── pages/VersionCompare.js     # Cross-version score/assessment/portfolio compare
├── pages/Debug.js              # Debug console / diagnostics
└── components/
    ├── Sidebar.js               # Left nav
    ├── StockTable.js            # Sortable table + mobile cards
    ├── ScoreBadge.js            # Score pill styling
    ├── ScoreVersionSelector.js  # Version dropdown (used by 4 pages)
    ├── DteRecommendation.js     # Thesis + DTE panel
    ├── PriceChart.js            # Candlestick + BB + Volume + TP/SL overlays
    └── *Chart.js                # RSI, MACD, BB, Trend, Weekly charts
```

Full per-page detail (Dashboard sort tiers, Assessment's 12 buckets, Historic's
pill logic, Allocator's execution-window banner, Portfolio's engine backing)
lives in [frontend.md](../../docs/frontend.md) — read it before touching any
page beyond a small tweak; this skill only covers the ops/wiring layer around
it.

## 2. Running the dashboard to eyeball a change

Use the Preview tool, not a raw terminal `npm start` — it gives you
screenshot/console/network introspection the harness understands, and it
already has a `launch.json` entry:

```
preview_start({ name: "frontend" })
```

This starts (or reuses, if already running) the CRA dev server on `:3000`.
The Flask API on `:5000` is a **separate** process — Preview does not manage
it. If the API isn't already up (check `curl -s http://127.0.0.1:5000/health`),
start it the same way you'd restart it (GUARD 1), just without the `restart`
verb — consult whatever the project's own start mechanism is (this repo's
`server.bat`/`server.ps1` controller, external to this checkout) rather than
inventing a new invocation.

Once the frontend server is up:
```
preview_screenshot({ serverId })         # visual check
preview_snapshot({ serverId })           # accessibility tree — prefer for text/structure checks
preview_console_logs({ serverId })       # runtime JS errors
preview_network({ serverId, filter: "failed" })  # 4xx/5xx API calls
```
If `preview_network` shows API calls failing with connection-refused, that's
the `:5000` backend being down or unreachable — not a frontend bug.

## 3. Adding a new API endpoint (`api.py` pattern)

Follow the existing route style — plain `@app.route` functions, no
blueprints, no class-based views:

```python
@app.route('/api/my-new-thing', methods=['GET'])
def get_my_new_thing():
    try:
        # ... query / compute ...
        return jsonify({...})
    except Exception as e:
        print(f"ERROR in /api/my-new-thing: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
```

Conventions to match (grep nearby routes for a concrete template before
writing a new one — `api.py` is large and has several idiom clusters):
- Every route is wrapped in a bare `try/except Exception`, printing both the
  message and `traceback.format_exc()`, returning `{'error': str(e)}, 500` on
  failure. Don't let an endpoint raise uncaught.
- `app.json_encoder = CustomJSONEncoder` already handles `Decimal` and
  `datetime`/`date` serialization repo-wide — don't hand-roll `.isoformat()`
  calls in every endpoint, the encoder does it.
- Version-resolving endpoints follow the `_resolve_dashboard_score_version()`
  pattern: read `request.args.get('version')`, fall back to
  `get_api_score_version()` (the active version) if absent or unresolvable.
  Reuse this helper rather than re-deriving version resolution per endpoint.
- `no-store` cache headers are used on state-mutating/live-state endpoints
  (`/api/portfolio/state`, `/api/portfolio/sync`) — match that if your new
  endpoint reflects live/mutable state rather than a stable historical query.
- **This is a Flask code change → restart required (GUARD 1)** before the
  frontend can see it, even though the endpoint itself lives in Python, not
  React.

After adding, restart the backend and smoke-test with curl before wiring any
frontend consumer:
```bash
curl -s http://127.0.0.1:5000/api/my-new-thing | python -m json.tool
```

## 4. Adding a new page

1. Create `src/pages/MyPage.js` following an existing page's shape (data
   fetch via `axios` or the shared `StockContext`, Tailwind utility classes,
   no CSS modules/styled-components in this codebase).
2. Wire routing — find where `react-router-dom`'s `<Route>` list lives (the
   app's root router component) and add the path.
3. Add a `Sidebar.js` nav entry. Note the precedent set by the Allocator
   page: it was deliberately placed **second**, right under Dashboard, per
   user preference (2026-06-12) — sidebar ordering is a product decision the
   user has opinions about, not alphabetical; don't just append to the
   bottom without asking if the page is meant to be a primary surface.
4. If the page needs version-awareness, reuse `<ScoreVersionSelector>` (see
   §5) rather than building a new dropdown — it's already wired into 4 pages
   with consistent legacy-version collapsing and active-version highlighting.
5. If the page needs a portfolio-profile toggle, reuse the existing toggle
   component/pattern from Assessment/Backtest/Allocator (see §6) rather than
   inventing a new one — profile keys (`sentinel|core|apex`) are looked up,
   never hardcoded as a fixed 3-option enum in new code (a 4th profile could
   exist by the time you read this).

## 5. Version selector wiring

`ScoreVersionSelector` (used by `Dashboard.js`, `Backtest.js`, `Historic.js`,
`Allocator.js`) is fed entirely by `GET /api/score/versions`, which returns
`available_versions` (primary list) and `legacy_versions` (collapsed behind a
"More versions" toggle in the component). The component is presentational
only — it does no fetching itself; the parent page owns the fetch and passes
`versions`/`legacyVersions`/`currentVersion`/`activeVersionId`/`selectedVersionId`/`onSelect`
as props.

**The list is DB-driven (`_score_version_catalog()` queries `AlgorithmVersion`
rows), not a hardcoded array in `api.py` or the component** — see GUARD 4. To
add a version to every selector on the site, you register it at the data
layer (`ship-version` skill), not here.

## 6. Profile toggle wiring (Backtest.js precedent)

`Backtest.js` is the reference implementation for a profile-aware page: it
fetches `DEFAULT_ADVANCED` + `FIELD_TIPS` from `/api/strategy/config` on
mount AND on every DTE-toggle change, and sends `profile=sentinel|core|apex`
to `/api/backtest/run`, pulling the profile-controlled advanced knobs from
`/api/portfolio/profiles/compare`. Saved runs persist the profile key in
`params_json` so re-opening an old saved run shows the right portfolio layer
— **don't silently re-derive params from the current profile default when
displaying history; a saved run's params are frozen at save time.**

Assessment's Calendar tab and Allocator's live form follow the same
component/pattern (profile → `/api/backtest/temporal?profile=` and
`/api/allocation/live?profile=` respectively) — copy the Backtest.js
implementation rather than reinventing the fetch-on-toggle logic per page.

The profile toggle only affects portfolio-stage stats (TP/SL, cascade,
DD levers) — it never changes which `Score.overall` value is displayed. If a
UI change makes it *look* like selecting a profile changes a stock's score,
that's a bug (scoring and portfolio-stage are orthogonal layers), not a
feature.

## 7. What needs restart vs hot reload — quick reference

See GUARD 3's table. The one-line summary: **anything under `src/` is free
(hot reload); anything `api.py` imports (directly or transitively) needs the
backgrounded restart + `/health` check.** When in doubt, grep for the import:
```bash
grep -n "^import\|^from" api.py | grep -i <suspect_module>
```

## Evidence / see also

- [frontend.md](../../docs/frontend.md) — full per-page/component reference
  (Dashboard sort tiers, Assessment's 12 buckets + scaled/unscaled toggle,
  Historic's pill conditions, Allocator's execution-window banner states,
  Portfolio's engine backing, `ct_tag` field).
- [process.md](../../docs/process.md) — the restart trap in its original
  context, plus doc-update timing and git workflow defaults.
- `portfolio-ops` skill — live-tracker engine semantics (re-qualification
  sweeps, push-notification cadence, execution timing canon) that this skill
  intentionally does not duplicate.
- `ship-version` / `ship-portfolio` skills — how a version/profile actually
  gets registered so the frontend has something to show.
- `.claude/docs/traps.md` — the cross-skill trap registry.

## Self-update

If you hit a trap this skill missed, append it to GUARDS above **and** to
`.claude/docs/traps.md` in the same session.
