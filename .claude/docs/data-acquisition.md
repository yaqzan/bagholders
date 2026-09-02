# Data Acquisition — Pricing Barriers & Buy Plan

Two recurring blockers on scoring/portfolio work are data gaps, not ideas — both retail-affordable. Pricing + sources: memory `reference_data_buy_pricing`.

## The two barriers

**1. Historical options + IV/greeks.** Blocks:
- The gamma engine (`option_pricing.py` `bs_*` + default-off `GAMMA_AWARE`). Its MC A/B exploded (+1754% median compound) because MC prices premium as `PREMIUM_MULT × realized_vol` — too cheap for big movers, masked by the old const-delta linearity. Needs real IV-based premium. See `project_option_model_fidelity_gaps` (memory) + [known-issues.md](known-issues.md).
- OSK / option-skew leads ([alpha_mining/NEW_LEADS.md](../../alpha_mining/NEW_LEADS.md)) — confirmed put-skew edge on 75+, option-data-locked.

**2. Survivorship-bias-free (delisted) equity.** Blocks honest crash-DD tests. Deep backfills (v74, 1995→2026) are survivor-only — today's universe misses dead dot-coms/2008 casualties, so crash DD reads optimistically. Needs point-in-time constituents with delisted names.

## Pricing (2026-07)

**Options + IV:** Polygon Options Developer ~$79/mo (free tier to prototype, ~4yr 2021-25, IV+greeks — the trial vehicle) | historicaloptiondata.com L3 ~$2,035 one-time (to 2002, IV+surface, owned forever — the buy) | ORATS $99/mo (to 2007) | OptionMetrics IvyDB ~$20k+/yr institutional (to 1996, gated).

**Delisted equity (1d):** Tiingo Power ~$30 one-month grab (~1996) | Sharadar ~$40 one-month grab — **PURCHASED+PULLED 2026-07-29** (1997-12-31, survivorship-free, served from `api.sharadar.com` not Nasdaq Data Link) | Norgate Platinum no monthly (6mo/12mo ~$350/$630, trial caps at 2yr) | CRSP ~$60-100k/yr institutional (1925, gated).

Static one-time need → subscribe one month, bulk-download, cancel (Tiingo/Sharadar). Norgate has no cheap one-off.

## Phased plan (executed)

1. Trial Polygon → feed real ATM IV into MC premium, re-run gamma A/B. Test: does real IV collapse the +1754% explosion? Ingest built+offline-verified 2026-07-06: `experiments/data_ingest/polygon_{validate,iv_ingest,client}.py`.
2. Only if (1) validates → buy L3 to extend through 2008/2020 + validate OSK across a full bull+2022.
3. Delisted equity (cheap, independent): one-month Sharadar/Tiingo → `experiments/data_ingest/ingest_delisted_equity.py` (creates delisted `Stock` rows with `delisted_date`) → re-run breadth/regime/recalc + research pack.

## Status snapshot

Gamma engine: built+validated+parked (default-off), awaits IV-premium. Deep backfill v74 1995→2026 DONE (survivor-discounted). Polygon PURCHASED 2026-07-07; Sharadar PURCHASED+PULLED 2026-07-29.

## Sharadar pull — PURCHASED + PULLED (2026-07-29)

Vendor host was stale: Sharadar is not `SHARADAR/SEP` on Nasdaq Data Link anymore — it serves its own API at `https://api.sharadar.com/v1.0/data/<table>` (docs: sharadar.com/docs/stocks). Endpoint names/params/bulk mechanism all differ (`stocks` not `SEP`; `ticker`/`from`/`to`/`fields`/`years`; `years=full` 302-redirects to a time-limited URL, not an async export job). The old host doesn't reject an unknown key — returns `429 QELx06` (reads as throttling, invites a pointless backoff loop). Both this and the Cloudflare `Python-urllib` UA ban are in [traps.md](traps.md).

Puller: `experiments/data_ingest/pull_sharadar.py` — download+verify only, never writes MySQL (same invariant as Polygon). Key = `SHARADAR_API_KEY` in repo-root `.env` (gitignored). Re-runnable, skips size-matched files. Run via `trader queue submit --db light --cpu 1`.

Pulled to `.cache/sharadar/` (manifest.json = per-file sha256 + remote mtime, byte sizes matched vendor exactly):
- `stocks.csv.zip` 998MB, 46.2M rows: `ticker,date,open,high,low,close,volume,closeadj,closeunadj,lastupdated`, 1997-12-31→2026-07-28, 21,936 tickers
- `tickers.csv.zip` 5.3MB, 78,861 rows: `permaticker,isdelisted,firstpricedate,lastpricedate,sector`
- `actions.csv.zip` 9.8MB, 671,371 rows: 19,194 delistings, 3,347 bankruptcy liquidations, 12,902 splits
- `sp500.csv.zip` 253KB, 59,669 rows: PIT membership 1957-03-04→, 1,202 tickers ever

`daily` (fundamentals/marketcap) NOT entitled on this subscription.

Adjustment convention (verified AAPL 2000-01-03): `close`=1.00 split-adj only, `closeadj`=0.838 split+div, `closeunadj`=111.94 as-traded. Our `price_history` is yfinance `auto_adjust=True` (split+div) → `ingest_delisted_equity.py`'s `fac = closeadj/close` on OHLC is the correct map.

Survivorship discount quantified (`verify_sharadar.py` → `PULL_VERIFICATION.json`): 15,627/21,934 equity tickers (71%) delisted. Against our 895-symbol live universe, coverage of tickers that actually traded in-window: ltcm_1998 4.2%, dotcom 4.6%, gfc 7.6%, covid 10.6%. That's the optimism size in every deep-window DD to date.

## price_history REBUILT on one convention (2026-07-29)

Reconciliation gate came back KILL: 14.8% mismatch vs <1% bar — cause was ours, not Sharadar's. Three defects found:

- **D1 mixed adjustment conventions**: 84 backfill seams; deep-backfill batches wrote split-adj-but-not-div-adj bars onto a div-adj rolling window. 334/757 symbols (44.1%) mixed conventions within their own series. Share on wrong convention: 1998-2015 55-66%, 2016-2024 27-39% (reaches into the 5y ship-gating window), 2025 17.3%, 2026 7.2%. Each seam = phantom one-day crash, median -28.9%, worst -95.5%. Example: ADM 2015-12-24→28 read 36.65→26.88, actually closed 36.50.
- **D2 stale split adjustment**: names that split after the last full re-pull kept pre-split scaling across whole stored history (NFLX uniformly 10x, NOW 5x).
- **D3 `DECIMAL(10,2)` quantisation**: 89,771 bars under $1.00, 9,672 under $0.10. Fixing D1 made this worse (div-adjusting pushes deep prices toward the floor) → columns widened to `DECIMAL(18,6)`.

Outcome: mixed-convention symbols 334/757 (44.1%) → 6/1,562 (0.4%); rows/symbols 4.96M/811 → 7.09M/1,626; bars at close=0: 3→0. Both conventions stored (AAPL 2000-01-03: `close` 0.840 adjusted, `close_unadj` 111.94 as-traded), converging at present.

Also landed: 729 new `stocks` rows (465 delisted), `permaticker`+`data_source` provenance, dedup of 12 vendor tickers double-claimed by two symbols (39,845 duplicate bars removed), `--include-delisted` on `trader recalculate` so 600 dead names get scores.

Scripts (all in `experiments/data_ingest/`): `pull_sharadar.py`, `build_sharadar_parquet.py`, `build_symbol_map.py`, `dedupe_symbol_map.py`, `rebuild_price_history.py`, `widen_price_precision.py`, `rebuild_indicators.py`, `map_price_conventions.py` (verification). Pre-rebuild snapshot: `backup_price_history_pre_rebuild.parquet` (4,939,207 rows) on C: and `B:\trader_rebuild_safety_2026-07-29\` (sha256 verified).

**Audit consequence:** absolute-level claims measured before 2026-07-29 ran on the contaminated substrate (WR15 levels, DD levels, compound returns, collapse certificates, newbox_rebaseline artifacts). A/B comparisons where both arms shared the substrate are largely robust; LEVEL claims are not. Re-run priority: near-miss nulls first, then anything on absolute price levels (e.g. `project_bankroll_ladder_2k_to_20k`).

**Step 4 needed THREE arms**: post-rebuild numbers differ from frozen baseline in TWO ways at once — conventions repaired AND universe expanded 895→1,626. A = frozen baseline (contaminated, survivor-only, already frozen); B = middle arm (clean conventions, survivor-only 895); C = final (clean conventions, full PIT universe). A→B = convention-repair effect; B→C = true survivorship discount.

## FINAL STATE (2026-07-29)

Sharadar bulk pull (4 tables+funds, 1.27GB) → 46.2M+15.4M rows, 45s/8s. price_history rebuild (1,622 syms) → 7.1M bars one convention, 3m56s. Precision widening (10,2)→(18,6): 2 tables/8 cols, 2m50s. Rebuild re-run full precision (NVDA 1999 0.03→0.038): 4m11s. Indicators+weekly aggregates 1,633/1,638: 33m18s. Breadth backfill 7,531 days, 7,287 filled: 27m02s. Regime backfill 7,531/7,531: 59m02s. Score recalc 10y+tail: 38m18s. Score recalc 30y+tail (full depth): 65m53s. Orphan-score purge 281,098 rows: 55s. DTE regen (live universe 2y): 27m46s.

Final: price_history 7,101,130 rows/1,626 symbols/1997-12-31→2026-07-29; scores 7,015,386/1,608 symbols; 0 orphans; indicators 8,283,837; stocks 1,638 (600 delisted).

**`--dte` is ~85% of recalc runtime** (full per-date pass, display-only) — excluding it took the 10y pass from projected 6.7h to 38m. Regenerate DTE separately over a short window for the live universe.

**`--full` is only 10y** — the dot-com window needs an explicit `30y` lookback (25y reaches only to 2001-07, after the crash begins). **`--include-delisted` is required** or the 600 dead names get no scores.

Also fixed same pass (see traps.md): `bulk_build`'s `REPLACE INTO` would have nulled `close_unadj` on every update; vendor-ticker translation for `BF.B`/`BRK.B`/`SATS` (had been failing every live pull silently) + a pull-failure ledger + ops-heartbeat check.

**P2.A step 4 (paired report) — DONE 2026-07-29.** Three-arm decomposition: `experiments/survivorship_decomposition/FINDINGS.md` (N=300, v74 pinned, 16 windows × Core/Apex). A→B (substrate repair) dominates level corrections: Core 10y median compound 9,301%→1,752%; Apex 2022-bear collapse probability 0%→48.7% (was masked by contamination). B→C (true survivorship, clean substrate, selection-universe only): Core worst-DD +18.6pp ltcm_1998/~+5.5pp 2020-era/+4.9pp 2007_now, recent compound trimmed ~13-17% relative; dotcom/GFC-era median returns IMPROVE (+54.9pp/+36.7pp) — survivor slice was unrepresentative (39-58% of era signal supply), not uniformly optimistic, so "survivor-only reads optimistically" is era-dependent, not a law. Core p_coll=0 in all 96 cells; Apex survivorship shows up as long-horizon collapse (2007_now 0.3%→69.0%). Arm B ran the frozen 811-symbol pre-rebuild universe via `MC_UNIVERSE_FILE` allow-list hook in `monte_carlo.py` (inert unless set; portfolio-stage only).

Doctrine unchanged: deep windows remain screens, not gates (per known-issues.md WHAT NOT TO DO). This purchase makes the crash-window floor honest; it does not license ship-gating on a deep window.

## Polygon ingest (built 2026-07-06)

Three scripts in `experiments/data_ingest/`, offline-verified (compile, BS/IV round-trip self-test, universe read 16,261 pairs @70+/2,385 @75+ for 2021-25, resume+parquet-schema check). No key needed to build; only `POLYGON_API_KEY` was missing.

- `polygon_client.py` — shared paced/retrying REST client (`expired=true`+`as_of` chain listing, `next_url` pagination) + a Black-Scholes IV solver. Polygon has no per-date historical IV endpoint (snapshot `implied_volatility`/greeks are current-only) — historical IV is computed from each option's daily close via BS. `python polygon_client.py` runs the offline round-trip self-test.
- `polygon_validate.py` — free-tier GO/NO-GO gate (P2.2 step 1). Run first: proves per-date chain + daily option price + sane BS-computed IV, writes `.cache/polygon_iv/validation_report.json` PASS/KILL. Free key defaults to ~90-day-back date inside the 2yr free cap; `--date` for paid-key depth. KILL ⇒ evaluate ORATS $99/mo instead of buying L3 on faith.
- `polygon_iv_ingest.py` — resumable pull (P2.2 step 2). For every 70+ (`--min-overall`) call signal in `rs_ledger.parquet` 2021-25, fetches ~30-DTE ATM call + ±10%-OTM put/call, computes `atm_iv`, `skew`, `iv_rv`, 15-bar forward option P&L. Output `.cache/polygon_iv/iv_ledger_polygon.parquet` — same schema as `experiments/iv_skew/build_iv.py`'s `iv_ledger`, so `build_proxy.py` + OSK scripts swap the source path. Crash-safe resume via append-only `_ingest_progress.jsonl` (`--smoke N`, `--limit N`, `--symbols`, `--consolidate-only`).

Invariant (also traps.md): vendor data is never written to MySQL `option_prices` — ingest only reads `price_history` (Polygon `open-close` fallback) and writes the parquet sidecar.

Run order once key is set: `set POLYGON_API_KEY=…` → `polygon_validate.py` (PASS?) → `polygon_iv_ingest.py --smoke 25` → full `polygon_iv_ingest.py` → re-run `build_proxy.py` + `experiments/year_2024_factor/*`.

## Polygon tier PURCHASED (2026-07-07)

Options Developer purchased. Polygon rebranded to massive.com 2025-10-30 (polygon.io 301-redirects, `api.polygon.io` still live). $79/mo, 4 years history confirmed (REST aggregates + day/minute flat files), unlimited calls (~100 req/s informal ceiling), 15-min delayed, trades included. NBBO quotes NOT included (Advanced $199 only; quote history starts 2022-03-07 even there) — irrelevant to IV/OSK trial. Historical open interest does not exist at any tier (snapshot semantics only) ⇒ dealer-GEX backfill impossible from this source; our own `option_prices` daily OI capture remains the only OI history (stocks 2025-02+, SPY 2025-06+). Flat-file day aggregates at Developer = bulk-backfill accelerator if REST pacing is slow.

## OSK cross-regime validation — KILL (2026-07-07)

Pre-registered trial on the 4y Polygon panel (`experiments/osk_validation/VERDICT.md`): discovery-window (2025-26) edge replicates under an independent recipe (spearman +0.090, clustered t +3.4) — not a measurement artifact. Backward-OOS 2022-08→2025-02 (never-calibrated): univariate edge absent (spearman −0.002; 2022 bear tail −0.107) ⇒ **OSK downgraded to regime-conditional (2025-26-local)**. Per pre-registered rule: do NOT buy L3 to validate OSK — more history can't validate a regime-local edge; ORATS-for-OSK equally moot. Residual L3 case (gamma-curve/MC crash-fidelity across 2008/2020) is separate, still open, decide on model-fidelity grounds. Unverified off-registration observation (low-pri): backward orthogonalized skew-net-of-momentum is positive (clustered t +4.6) — see VERDICT.md. Data hygiene finding filed: build_iv ledgers never persist strike/DTE selection (transient SQL) — cross-source audits need it.

---

# ACTIVE DATA GRAB — 2026-08-02

**Status: POLYGON GRAB COMPLETE + VERIFIED 2026-08-02.** Two paid subscriptions harvested to disk before lapse. Paths below are final and safe to consume — do not re-pull. Re-verified against S3 listings: 1,004 entitled objects per prefix, 0 missing, 0 size mismatches, contiguous 2022-08-01..2026-07-31, 0 stray `.part` files. Total 64GB.

| subscription | lapses | grab status |
|---|---|---|
| Sharadar (~$40, one month) | ~2026-08-29 | COMPLETE (round 2 done 2026-08-02); one incremental re-pull scheduled before lapse |
| Polygon Options Developer ($79/mo) | ~2026-08-06 (safe to cancel, grab verified complete) | flat-file archive COMPLETE: day_aggs+trades+minute_aggs, 64GB |

## Pre-lapse top-ups — AUTOMATED (2026-08-02)

Polygon cancelled 2026-08-02 (non-renewing; access persists until expiry, exact date unknown). Top-ups are recurring with an end boundary so no date has to be guessed:

| task | cadence | until | runs |
|---|---|---|---|
| `TraderPolygonFinalTopup` | daily 09:00 | 2026-09-30 | flat-file sync tiers 1/4/5 + reference pull |
| `TraderSharadarFinalTopup` | weekly Wed 09:15 | 2026-09-09 | `pull_sharadar.py --force`, all 6 entitled tables |

Design: each run idempotent/near-free when nothing new; once entitlement dies run degrades to harmless 403 logging. `-StartWhenAvailable` — a sleeping box runs at next wake. `-DeleteExpiredTaskAfter 1 day` — tasks self-remove after end boundary. Date-stamped queue dedup key (`<vendor>-topup-<yyyymmdd>`) keeps last successful top-up date visible in `trader queue list`.

Runner: `scripts/vendor_final_topup.ps1 -Vendor polygon|sharadar` (safe to run manually). Installer: `scripts/install_vendor_topups.ps1`. Report: `.cache/vendor_topup/<vendor>_LAST_TOPUP.json`. Per-user/`RunLevel Limited`, no elevation needed.

**Top-up runner steps (polygon) — pinned, do not "simplify":**
1. `polygon_flatfile_sync.py --tier 1,4,5 --skip-probe` — main prize (~65MB/new day), ~29s when nothing new.
2. `polygon_short_interest_bulk.py --refresh` — date-pivoted, ~16s.
3. `polygon_reference_pull.py --endpoint financials --no-resume` — `--no-resume` REQUIRED or the progress ledger skips every known ticker; ~97s.

Total ~2.4min/run. **Never call `polygon_reference_pull.py` without `--endpoint`** — defaults to `all`, leads with the rate-capped per-ticker short-interest path (~30h, killed by the 4h task limit). All three failure modes found by dry-running the task; see traps.md "Scheduled-job bugs that report SUCCESS".

**Add both to the P0.A migration inventory** — a migration that drops them silently loses the last days of a subscription that can't be re-bought at this price. Self-delete after Sept 2026.

## Polygon flat files — the actual instrument (supersedes REST ingest for bulk work)

S3-compatible endpoint `https://files.polygon.io`, bucket `flatfiles`, credentials `POLYGON_S3_ACCESS_KEY_ID`/`POLYGON_S3_SECRET_ACCESS_KEY` in repo-root `.env`. Mirror: `experiments/data_ingest/polygon_flatfile_sync.py` (resumable, size-match skip, `.part`→rename, per-tier integrity verify, append-only `MANIFEST.jsonl`). Destination: `B:\polygon_flatfiles\<s3 key path>` — `.csv.gz` as-is, no decompression. C: has no room — don't retarget there.

**READ entitlement is narrower than the bucket listing** (verified 2026-08-02):

| prefix | read | window | on disk |
|---|---|---|---|
| `us_options_opra/day_aggs_v1/` | YES | 2022-08-01→2026-07-31 | 1,004 files/2.7GB — COMPLETE |
| `us_options_opra/trades_v1/` | YES | 2022-08-01→2026-07-31 | 1,004 files/44GB — COMPLETE |
| `us_options_opra/minute_aggs_v1/` | YES | 2022-08-01→2026-07-31 | 1,004 files/18GB — COMPLETE |
| `us_options_opra/quotes_v1/` | 403 | — | NBBO, Advanced-tier only; 116TB total (~105GB/day) — unusable at our scale even if bought |
| `us_stocks_sip/*`, `us_indices/*`, `us_futures_*`, `global_crypto/*`, `global_forex/*` | 403 | — | not entitled (options product only) |

**Coverage is 4 years, not 12.** The bucket LISTS 2014-06-02 onward for every options prefix with real sizes, but `GetObject` 403s on every date ≤2022-07-29. See traps.md "S3 LIST and S3 GET are separate entitlements".

**Consequence:** the deep-crash option-fidelity question is unchanged — no real option prices exist here for 2015-08, 2018-02 (Volmageddon), or 2020-03 (COVID). historicaloptiondata L3 justification not satisfied by this grab; do not cite the flat-file archive as crash-window evidence.

**What this grab does buy:** market-wide completeness (every contract/day 2022-08→now, vs the previous 3,339-path filtered REST sample in `.cache/polygon_real_premium/`) + option availability/liquidity map across the full expansion-candidate universe; `trades_v1` real execution prints (first empirical test of the asymmetric-cost canon — NBBO would be better but isn't affordable); `minute_aggs_v1` intraday option behavior, previously unavailable at any price we'd pay.

## Flat-file derived assets + canonical tooling (2026-08-17)

The raw archive is not the working surface. The FF exploitation program (`experiments/flatfile_exploitation/`, state board `TRACKER.md`) already built the canonical access layer + derived substrates on B:. New experiments reuse these instead of re-scanning 1,007 gzips (the weekly_5dte_movers recon 2026-08-17 nearly commissioned a redundant scanner because this inventory lived only inside the FF package):

- `experiments/flatfile_exploitation/ff_common.py` — the entry point: `parse_opra_ticker`/vectorized `add_opra_columns` (right-anchored fixed-width OCC parse, 0 exceptions across 780k distinct real tickers; `adjusted` flag for digit-suffix non-standard roots — never premium-compare those to standard siblings); `read_flatfile` (pinned schemas); `list_session_dates` (authoritative session enumeration — `MANIFEST.jsonl` is incomplete, e.g. 678/1,004 for trades); `KNOWN_INDEX_ROOTS` (no equity spot exists for these); `FLATFILES_ROOT`/`DERIVED_ROOT`.
- `B:\polygon_derived\contract_day_index\` — FF-0 prebuilt contract-day panel: hive `underlying=SYM/year=YYYY/`, 248M rows, 9,405 underlyings, tape columns + parsed contract fields, sessions 2022-08-01..2026-07-31. Refresh via `build_index.py` (idempotent, manifest-gated) only if sessions past 2026-07-31 needed.
- Other `B:\polygon_derived\` roots: `iv_panel` (154.5M solved IVs, FF-6), `liquidity_map`, `cost_empirics`, `minute_fidelity`, `expansion_map`, `ledger_v2`, `d1_double_touch` — check the owning FF package's RESULTS file before building anything similar.
- `experiments/wave_cycle_mine/calendar_features.py` (`WCalendar`) — empirical trading-day index (bar-existence-derived, no look-ahead) + OPEX/holiday logic; static `database/utils/trading_calendar.py` starts 2023 and has known missing closures. For flat-file-only work, `list_session_dates()` itself is the empirical calendar.

Schema gotchas (traps.md "The LOCAL flat-file mirror carries its own listing lies"): day_aggs CSV column order is `open,close,high,low` — NOT OHLC; `window_start` is int64 epoch nanoseconds; year dirs `2014/..2021/` exist under every options tier but are empty decoys. Per-underlying×year hive partitioning carries ~25% filesystem slack (contract_day_index 8.6GB logical → 11GB on-disk) — budget the on-disk number.

## Polygon reference endpoints — pulled 2026-08-02

Options aggregates are capped at 2022-08, but reference endpoints are not. Both entitled on Options Developer, pulled before lapse:

- **Fundamentals**: `.cache/polygon_reference/financials/` + `financials_all.parquet` (47MB). 3,037 tickers attempted, 2,612 with data (425 empty = ETFs/ADRs with no SEC filings), 4 statements, back to ~fiscal 2009.
- **Short interest**: `.cache/polygon_reference/short_interest_full.parquet` (43MB). 3,188,745 rows, 44,248 tickers, 2017-12-29→2026-07-15, semi-monthly; cols: settlement_date, ticker, short_interest, avg_daily_volume, days_to_cover; zero nulls.

Short interest is a genuinely orthogonal signal class — not price, volume, or breadth (the wall every one of ~40 closed mining axes hit). Whole-market (44k tickers), covers universe-expansion candidates too.

Correction to earlier Sharadar finding: Sharadar SF1 being un-entitled did NOT close the fundamentals axis — Polygon serves fundamentals ~17 years deep, now on disk. The Sharadar-specific statement stands; the broader "fundamentals unavailable" inference was wrong.

Pullers: `experiments/data_ingest/polygon_reference_pull.py` (financials, events, splits, dividends) and `experiments/data_ingest/polygon_short_interest_bulk.py` (short interest — use this one; the per-ticker path is rate-capped into uselessness; see traps.md "A rate-capped endpoint usually has a BULK AXIS").

## Sharadar round 2 — entitlement CLOSED (2026-08-02)

Probe: `.cache/sharadar/ENTITLEMENTS.json`; report: `.cache/sharadar/PULL_ROUND2.md`. Entitled: `stocks`(=`sep`), `funds`(=`sfp`), `tickers`, `actions`, `sp500`, and new `metrics`. NOT entitled (403, both naming variants): `sf1`/`fundamentals`, `sf2`/`insiders`, `sf3`/`holdings`, `daily`, `events` — stop proposing SF1-based feature classes unless a higher tier is bought before 2026-08-29. `metrics` is a snapshot, not a time series (one row per ticker at that ticker's own last date) — joining it into any historical feature build is look-ahead, exactly like `tickers.scalemarketcap`. Present-day screening only.

## REST option-tape pull — built, sized, then correctly abandoned

`experiments/data_ingest/polygon_tape_pull.py` + `.cache/polygon_tape/SIZING.json`. Kept for the record; **do not run its bar pass** — flat files dominate it on the identical window (~3GB vs a projected 37.8-49.6h of REST for 12.3-16.1M contracts). Its listing pass produced a load-bearing result for the universe-expansion lead:

- `.cache/polygon_tape/EXPANSION_CANDIDATES.parquet` — 2,900 tickers passing a liquidity gate (Sharadar common-stock/ADR, ≥$3M median daily dollar volume, ≥$5 median close, priced since 2022-08).
- `.cache/polygon_tape/contracts/{SYM}.parquet` — contract reference listings, 3,037 symbols.
- 1,866/1,956 expansion-only candidates (95%) have listed options, median 1,016 contracts/ticker. Optionable universe is ~2,900 names against the ~1,081 we actually score — the tradability objection to universe expansion is materially weaker than assumed.
</content>
