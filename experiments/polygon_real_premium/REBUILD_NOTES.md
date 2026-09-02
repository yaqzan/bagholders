# Corrected Polygon BS-IV panel — rebuild notes

Script : `experiments/polygon_real_premium/rebuild_iv_panel.py`
Output : `.cache/polygon_real_premium/iv_panel_corrected.parquet` (+ `_meta.json`)
Journal: `.cache/polygon_real_premium/_iv_panel_progress.jsonl` (resumable, keyed `(symbol, date)`)
Fixes  : `.cache/polygon_iv/iv_ledger_polygon.parquet` (READ-ONLY here — never modified)
Audit  : `PANEL_AUDIT.md`

The old panel used MySQL `price_history.close` — split AND dividend back-adjusted
(`trader.py` pulls yfinance with `auto_adjust=True`) — as the underlying spot,
while option strikes are quoted in **as-traded** dollars. That wrong price was
used BOTH to pick the "ATM" strike AND as `S` in the Black-Scholes solve, so on
drifted rows the derived IV is unreliable. This rebuild changes the spot and
nothing else.

---

## 1. Every place the corrected spot is used

`spot = sig["spot_unadj"]` is resolved once per row in the single-threaded phase
and is the ONLY price `process_one` reads. The back-adjusted close is carried as
`spot_adjusted` purely for the `adj_factor` audit column and is never read again.

| # | Site (`rebuild_iv_panel.py`) | Old ingest used | This rebuild uses |
|---|---|---|---|
| 1 | ATM strike selection — `priced(calls, spot, fwd_end)` | `close` (adjusted) | `spot_unadj` |
| 2 | ATM `implied_vol("call", entry, spot, ...)` — the `S` argument | `close` | `spot_unadj` |
| 3 | OTM put **target strike** — `spot * (1 + otm_frac_put)` | `close * 0.90` | `spot_unadj * 0.90` |
| 4 | OTM call **target strike** — `spot * (1 + otm_frac_call)` | `close * 1.10` | `spot_unadj * 1.10` |
| 5 | OTM put `implied_vol("put", ..., spot, ...)` — the `S` argument | `close` | `spot_unadj` |
| 6 | OTM call `implied_vol("call", ..., spot, ...)` — the `S` argument | `close` | `spot_unadj` |
| 7 | `moneyness_true = atm_strike / spot` (new column) | n/a | `spot_unadj` |
| 8 | Guard `spot is None or spot <= 0 -> miss:no_underlying` | `close` | `spot_unadj` |

The selftest asserts this directly (`[6] ANTI-LEAK`): running a synthetic MMM
2022-08-23 row (as-traded 141.75 / adjusted 103.79, `adj_factor` 1.366) through
`process_one` against a recording fake client, **no bar is ever requested for a
strike within $5 of the adjusted close**, and at least one is requested within
$1.50 of the as-traded close. The same row re-run with `spot_unadj` set to the
adjusted value picks strike 104 instead of 142 — proof the anchor is the only
thing driving the difference.

### How `spot_unadj` is produced

`pull.load_unadj_spots` / `pull.apply_forward_splits`, imported and reused
**verbatim** — there is no second spot implementation in this repo, and the
selftest asserts `load_unadj_spots.__module__ == "pull"`.

```
spot_unadj(t) = yfinance auto_adjust=False Close(t) * PROD(split_ratio(s) for s > t)
```

yfinance's unadjusted Close is split-adjusted but NOT dividend-adjusted;
un-applying the forward split factors recovers the as-traded print exactly
(validated in pull.py's selftest against NVDA / MMM / AZN).

Cache: `.cache/polygon_real_premium/_iv_panel_spot.parquet`, seeded on first run
from the audit's already-resolved `_panel_audit_spot.parquet` (699 symbols) so a
rebuild does not re-hammer yfinance. 16 of the 701 universe symbols are not in
that seed and are fetched on the first full run.

Fallback: when yfinance has no series for a symbol (delisted / renamed — 3
symbols, ~1.3% of the old panel's rows) `spot_unadj` degrades to the adjusted
close and `spot_source` records `ph_adjusted` so those rows can be excluded.

---

## 2. Schema mapping

The first 15 columns **are** the old panel's columns, in the old panel's order,
so a downstream consumer swaps the parquet path and nothing else. The selftest
asserts this against the live old parquet.

| column | source | changed vs old panel? |
|---|---|---|
| `symbol`, `date` | rs_ledger universe row | no |
| `overall`, `vol_pct` | rs_ledger universe row | no |
| `atm_iv` | `implied_vol("call", entry, spot_unadj, K, T)` | **YES — S and K both corrected** |
| `entry_premium` | ATM contract close on the signal date | **YES — different contract on drifted rows** |
| `iv_rv` | `atm_iv / (vol_pct/100 * sqrt(252))` | **YES via `atm_iv`**; the RV leg is unchanged (see below) |
| `skew` | `otm_put_iv - otm_call_iv` | **YES — both legs corrected** |
| `pnl15`, `pnl_max`, `pnl_min` | ATM forward closes in `(d, d+24cal]`, 15th bar | **YES — different contract on drifted rows, AND the truncate-substitution defect below** |
| `otm_put_iv`, `otm_call_iv` | ±10% legs, targets and `S` both `spot_unadj` | **YES** |
| `atm_strike` | selected ATM strike | **YES** |
| `dte` | `expiration - date` in calendar days | only where the selected contract changed expiry |

New audit columns (appended, never reordered):

| column | meaning |
|---|---|
| `spot_unadj` | as-traded close — the anchor actually used |
| `spot_adjusted` | MySQL `price_history.close` (back-adjusted) — audit only |
| `adj_factor` | `spot_unadj / spot_adjusted`; `1.0` == uncontaminated row |
| `spot_source` | `yf_unadj` or `ph_adjusted` (delisted fallback) |
| `moneyness_true` | `atm_strike / spot_unadj`; `<1` == ITM for a call |
| `strike_rank` | 0-based rank of the chosen strike in the `|K - spot|` walk (how many nearer strikes were dry) |
| `entry_volume`, `entry_trades` | Polygon `v` / `n` on the entry bar |
| `liquid_entry` | `entry_volume >= 5 AND entry_trades >= 1` (`pull.is_liquid_entry`) — screens stale-quote artifacts |
| `option_ticker`, `expiration_date` | the exact contract, for reproduction |
| `std_contract` | 100-share deliverable + standard OCC root. **A column, not a filter** — the old panel did not filter these and adding a filter would confound the comparison |
| `fwd_bars` | forward bars actually available; `< 15` means `pnl15` is unresolved |
| `n_api_calls` | logical Polygon requests for this row (chain counted once) |

`vol_pct` and therefore the RV leg of `iv_rv` still come from the rs_ledger and
are computed on the **adjusted** close series. That is deliberate and unchanged:
it is the engine's own realized vol, and returns are near-invariant to
back-adjustment. Only the OPTION side of the ratio is corrected.

Only `status == 'kept'` rows are written to the parquet, exactly like the old
ingest. Miss accounting lives in the journal and `_meta.json`.

---

## 3. Everything held identical (so the panels stay comparable)

Mirrors `experiments/data_ingest/polygon_iv_ingest.py` exactly:

* universe = `.cache/rel_strength/rs_ledger.parquet`, `overall >= 70`
  (**70, not 75** — the old panel's floor), unique `(symbol, date)`, sorted by
  date then symbol. Window default `2022-08-01 .. 2026-05-15`, which reproduces
  the old panel's journal exactly (10,793 pairs).
* chain query: `list_option_contracts(sym, as_of=d, exp_gte=d+20, exp_lte=d+45)`
  — `as_of` used ALONE (combining it with `expired=true` returns zero rows).
* ATM: nearest **priced** strike across the whole expiry window (not per-expiry),
  plain stable sort on `|K - target|` so ties keep chain order; 6 tries.
* OTM: `otm_frac_put = -0.10`, `otm_frac_call = +0.10`, bars fetched over the
  single day `d`; 6 tries each.
* BS: `r = 0.04`, `q = 0.0`, `T = calendar days / 365`, bisection solver from
  `polygon_client`.
* forward P&L window `(d, d + 24 calendar days]`, 15th forward bar; rounding to
  4 dp on `atm_iv` / `skew` / `pnl*` / `iv_rv`, `entry_premium` to 4 dp.
* forward-P&L offsets count **bar index**, not market trading days — a known,
  deliberately preserved caveat (Polygon omits no-trade days, so on an illiquid
  contract the "15th bar" can be more than 15 trading days out). Screen with
  `liquid_entry` / `fwd_bars`.

---

## 4. Second defect found during the smoke (NOT the spot bug)

The shipped panel's `pnl15` / `pnl_max` / `pnl_min` are **truncate-substituted**.

`.cache/polygon_iv/iv_ledger_polygon.parquet` was built **2026-07-07**; commit
`ebca1e1b` (**2026-07-08**) removed `fwd[min(14, len(fwd)-1)]` from
`polygon_iv_ingest.process_one` and replaced it with the honest
`if len(fwd) >= 15` guard. The panel was never rebuilt, so wherever the 15-bar
horizon was unripe the stored value is the **last available price**, not a
15-bar outcome.

Verified exactly: ELF 2022-08-04, entry 2.45, only 12 forward bars exist;
`fwd[11]/2.45 - 1 = 0.1020` == the stored `pnl15`.

Consequence: the old panel's `pnl15` null rate is 0.7%; an honest rebuild's is
~50% (51.6% of the 62 smoke overlap rows have `fwd_bars < 15`, and the old panel
reports a non-null `pnl15` for **100%** of them). This rebuild follows the fixed
convention and records `fwd_bars` so ripeness is visible per row. Any downstream
result that leaned on the old panel's `pnl15` coverage should be re-read.

---

## 5. Known coverage limit (reproduced, not introduced)

`miss:no_chain` fires when the `[d+20, d+45]` expiry window falls entirely
between two monthly expirations. Verified: GEN 2025-06-02 window `06-22..07-17`
straddles the 06-20 and 07-18 monthlies and returns zero contracts; GEN
2025-06-03 (`06-23..07-18`) returns 54. Same for GIS 2022-08-01 vs 2022-08-02.

This costs the old panel 1,021 / 10,793 pairs (9.5%) and biases coverage toward
weekly-listed (large, liquid) names. It is reproduced **identically** here (9/9
of the smoke's `no_chain` pairs match the old panel) because the brief requires
mirroring the selection rule. `pull.py` solved the same problem by widening to
`[18, 50]`; that is available via `--dte-lo 18 --dte-hi 50` at the cost of exact
row-for-row comparability with the old panel.

---

## 6. Invariants honored

* `.cache/polygon_iv/*` is opened read-only (schema check + comparison). Nothing
  under it is written. Purely additive.
* Vendor data never enters MySQL. The only DB read is `price_history` closes for
  the `adj_factor` audit column.
* PyMySQL is not thread-safe → universe load, `price_history` preload, yfinance
  spot resolution and `enrich_universe` all run single-threaded **before** the
  thread pool. Workers do Polygon HTTP only.
* polars: `infer_schema_length=None` on every frame built from row dicts; a
  single `fill_nan(None)` choke point before strict casts.
* ASCII-only stdout; `PYTHONIOENCODING` / `PYTHONUTF8` set defensively at import.
* The API key is never printed. `--selftest`, `--consolidate-only` and
  `--compare-old` need no key, no network and no DB.
* Polygon aggregates are a ~4-year rolling window; a chain can resolve while
  every bar walk 403s. Classified `miss:timeframe_403`, never a crash. The
  earliest signals (2022-08) sit ~7 days inside the boundary as of 2026-07-25
  and will start 403-ing as it rolls.
* The script does NOT self-submit to the task queue.

---

## 7. Commands

```bash
# offline, no key
python experiments/polygon_real_premium/rebuild_iv_panel.py --selftest
python experiments/polygon_real_premium/rebuild_iv_panel.py --consolidate-only
python experiments/polygon_real_premium/rebuild_iv_panel.py --compare-old

# smoke
python experiments/polygon_real_premium/rebuild_iv_panel.py --limit 40 --workers 4

# full run -- QUEUE THIS, do not run raw
trader queue submit --priority high --db light --cpu 4 --restartable \
  --dedup polygon_iv_panel_corrected --reason "corrected as-traded-spot IV panel before Polygon cancellation" \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  -- python experiments/polygon_real_premium/rebuild_iv_panel.py --workers 8
```

Resumable: kill and restart freely; the journal is append-only and keyed
`(symbol, date)`, and `consolidate()` takes last-write-wins.
