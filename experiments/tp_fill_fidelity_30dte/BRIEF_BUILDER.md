# BRIEF — builder contract (Sonnet), TP-fill fidelity 30-DTE

You are the implementation builder for this experiment. The spec is LOCKED:
read `TASK.md` and `PREREG.md` in this directory FIRST — they define every event,
metric, slice, and assert. This brief adds implementation mechanics, traps, and the
report-back contract. You implement, smoke, submit the full run to the queue, and
report; you do NOT adjudicate findings, edit any production file, or change PREREG.

## Deliverables

```
experiments/tp_fill_fidelity_30dte/
├── driver/
│   ├── run.py            # single entrypoint: --selftest | --smoke N | --full | --analyze
│   ├── state/            # per-stage state JSON (atomic tmp+os.replace writes)
├── out/
│   ├── declarations_arm30.parquet / declarations_arm15.parquet   # P1 census
│   ├── events_arm30.parquet / events_arm15.parquet               # P2 joined events
│   ├── tp_fill_optimism_by_tier.md + .csv    # THE table (PREREG §4 cells)
│   ├── knob_calibration_draft.md             # PREREG §5 numbers only, no verdicts
│   ├── dose_accounting.md                    # every filter step, counts in/out
│   ├── bindings_echo.json                    # tier edges, env pins, asserts, arm params
│   └── smoke_report.md                       # N=25 run + ONE fully-worked example
└── logs/                 # bulk stdout; never read into context
```

## Implementation order (do not reorder)

1. **Schema recon (read-only, foreground):** print (to smoke_report prep notes)
   the schemas + 3 sample rows of: signals parquet, ledger.parquet, one paths
   year partition, signal_liquidity.parquet, underlying_ohlc_2022_2026.parquet,
   FF-4 bindings.json. Map PREREG's semantic names -> actual column names in a
   COLMAP dict at the top of run.py (single place to fix).
2. **Underlying OHLC gate (PREREG §1):** verify the cached parquet has full OHLC
   (open/high/low/close) per (symbol,date) and spot-audit 5 symbols × 3 dates
   against MySQL `price_history` (adjusted engine convention: `close` column, NOT
   close_unadj). Values must match to float tolerance. If the parquet is
   close-only or mismatches: STOP, report; fallback is ONE bulk pull
   (2022-04-01..2026-08-08, ledger symbols, columns symbol/date/open/high/low/
   close) -> `.cache/tp_fill_fidelity_30dte/underlying_ohlc.parquet`, submitted as
   `trader queue submit --priority high --db heavy --window off_market` — never
   foreground, never raw background.
3. **Selftests (--selftest, >=12, all offline/synthetic):** including at minimum —
   sym_bars tuple ORDER (date, close, high, low, open) fed to a synthetic
   compute_trade_outcome case with hand-computed tp_level; set_tpsl arm divergence
   on a synthetic path (TP15 touches a bar TP30 does not); join classifier on
   synthetic paths covering FILL / traded-below / no-print / unjoinable /
   gap-open / late-fill / never-fill; Wilson CI function vs a known value;
   nearest-rank percentile vs known values; COLMAP presence check against the real
   files; deadline arithmetic (signal_date + 27 cal days, weekend-spanning case);
   'both'-kind exclusion from primary; N<30 quantile suppression.
4. **P1 declarations:** per PREREG §2. Import order: env pins FIRST, then
   `import monte_carlo as mc`, then per-arm `set_tpsl(mc, tp, sl)` from
   `experiments/tpsl_refine_2026_08/driver/mc_patch.py` (import it by path;
   sys.path append its dir). Run ARM-30 fully, then ARM-15 (set_tpsl is
   stateful on the module — never interleave arms). All PREREG §2 asserts hard-fail.
5. **P2 join + metrics:** per PREREG §3-§4. Barrier from REAL entry premium
   (contract-multiple space). Never use the ledger's own tp30/sl70 touch columns —
   they are CLOSE-ONLY by an accepted FF-1 deviation and are not the engine's
   convention; classify from raw path OHLC yourself.
6. **--smoke 25:** 25 deterministic signals (first 25 kept ledger rows by
   (signal_date, symbol) sort), end-to-end through P1+P2+table build, write
   smoke_report.md with the ONE fully-worked example (PREREG §6.4 fields). STOP
   after smoke: report to orchestrator and WAIT for the go/no-go on the worked
   example before --full.
7. **--full via queue:** `trader queue submit --priority high --db light --cpu 2
   --restartable --dedup tp-fill-fidelity-30dte-full --reason "TP-fill fidelity
   measurement (PREREG-locked)" -- python experiments/tp_fill_fidelity_30dte/driver/run.py --full`
   (+ `--window off_market` if submitted during market hours). Expected wall:
   minutes. The job itself must be DB-free if step 2's cached parquet passed.
   Report the task id; the ORCHESTRATOR owns the watch (subagent queue-wait
   orphan trap — do NOT start `trader queue wait` yourself).
8. **--analyze** runs inside --full at the end (single job): builds the §4 tables +
   §5 draft numbers + dose accounting + tripwire checks (§6.1/2/3/5) printed at the
   TOP of tp_fill_optimism_by_tier.md as PASS/FAIL lines.

## Traps forwarded (violating any of these voids the run)

- T1 **Adjusted vs as-traded:** `price_history.close` is split+dividend ADJUSTED;
  option strikes/premiums are as-traded dollars. The engine walk runs on ADJUSTED
  bars (that IS the engine's convention — reproduce it); the model-anchored
  secondary uses `spot_unadj` (ledger column or FF-3' spot_unadj.parquet), NEVER
  the adjusted close. Primary metrics are contract-multiple space and immune.
- T2 **Ledger touch columns are close-only** (FF-1 accepted deviation) — never
  reuse them (step 5).
- T3 **polars all-None float column infers Null dtype** and fill_nan crashes —
  `cast(pl.Float64, strict=False)` first (FF-4 regression).
- T4 **polars .rank() treats NaN as MAX rank** — no NaN-blind ranking.
- T5 **day_aggs carry TRADED days only** — a missing path row means NO TRADES that
  day. Never forward-fill; a missing touch-date row is a no-print MISS (PREREG §3),
  not missing data to repair.
- T6 **Extended-hours prints:** trades_v1 has 03:00-17:01 ET prints; day_aggs highs
  may include them. You do NOT need trades_v1 for v1 of this measurement; document
  the day_aggs caveat in the table footer verbatim: "day-agg highs may include
  non-RTH prints; FF-4 minute overlap cross-check available as follow-up."
- T7 **Queue stdout blindness:** `PYTHONIOENCODING=utf-8` env pin on the queue job
  (already in the pins) or run.log is blind.
- T8 **set_tpsl silent no-op:** patching mc.TP_BASE alone does nothing —
  compute_trade_outcome reads TP_SIGMA_*. Use mc_patch.set_tpsl only; tripwire §6.3
  (ARM-15 declares strictly more 'tp' events) is the guard.
- T9 **Import order:** env pins before `import monte_carlo`; assert
  `mc.__file__` starts with `C:\Development\Trader` (worktree/PYTHONPATH ghost
  guard) — this experiment runs from the MAIN checkout.
- T10 **Engine None-returns** (signal_date not in bars / vol None / short window)
  are UNJOINABLE-census rows, not errors, not silently dropped — dose accounting.
- T11 **No MySQL writes ever; vendor/derived data stays on B:\ / .cache.**
  `MC_NO_DB_PERSIST=1` pinned. The only permitted DB READ is step 2's spot audit
  (and the fallback pull, queued --db heavy). Wrap reads defensively; if MySQL is
  unreachable, report — do not retry-loop (zombie-query trap).
- T12 **.ps1 ASCII-only** if you write any wrapper (you should not need one — the
  queue command runs python directly).
- T13 **Heavy compute goes through the queue**, smoke stays foreground seconds.
  Never harness run_in_background for the full run.
- T14 **`trader` CLI alias may be broken** (Python 3.13 py.exe shebang issue) —
  invoke as `python trader.py queue submit ...` from repo root if `trader` fails.

## Report-back contract (your final message; keep it under ~40 lines)

1. Selftest count PASS/FAIL; schema-gate outcome (cached parquet OK / fallback
   pull queued as task #N).
2. Smoke: declaration census (kind counts both arms on the 25), the worked example
   verbatim, any deviation from PREREG semantics you had to interpret (list each —
   orchestrator adjudicates).
3. Queue task id(s) for --full + expected wall.
4. Files written so far.
STOP conditions (report instead of improvising): schema mismatch beyond COLMAP
renames; asserts failing; coverage obviously broken in smoke (>50% unjoinable);
mc_patch import failure; any need to touch a file outside this experiment dir,
`.cache/tp_fill_fidelity_30dte/`, or the queue.
