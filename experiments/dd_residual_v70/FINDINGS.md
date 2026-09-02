# TVDD — TRIN volume-flow neutral-band CALL-alloc DD dampener (v70 Apex, 2026-06-07)

Overnight `/research` run. Goal: find a **4th orthogonal** Stage-3 call-alloc DD lever on the
live v70 Apex sleeve, AFTER RXDD (VIX), SVR (semivol/skew), MWDD (McClellan) all shipped.

## Method

1. Regenerated the full-lever MC trade-tape (`MC_TRADE_TAPE=1 N_ITER=300`, RXDD+SVR+MWDD all
   LIVE) → `.cache/dd_ledger/tape_*.parquet` (6.9M call trades, 12 windows).
2. Mined residual DD: cohort loser-rate lift/z + episode **dd_conc** (dollar-DD) + **mean_pnl**
   (per-trade EV, NOT changed by the levers — a lever only scales premium_cost) across
   TRIN, VIX-velocity, concurrency, entry_dd, A-D-slope + reference axes (VIX/McClellan/breadth).
3. **Key refinement (the MWDD design insight):** a lever only ACTS when running dd >= DD_MIN
   (~0.08/0.13). So mine the **DD-active subset (dd>=0.13)** — that's where the residual a 4th
   lever could harvest lives. The whole-tape mine DILUTED the signal; the DD-active mine sharpened it.

## Result: TRIN neutral-flow band is a clean orthogonal residual

A lever needs **low mean_pnl AND high dd_conc COINCIDING on an orthogonal, crash-robust cohort.**
After 3 levers most axes fail this (the low-EV cohorts are low-dd-conc; the high-dd-conc cohorts are
decent-EV). The exception is **TRIN (Arms index = volume FLOW)**:

DD-active (dd>=0.13) single-axis EV by TRIN band (base mean_pnl +0.05, base loser-rate 0.305):

| TRIN band | mean_pnl | loser-rate | dd_conc | dd_share | verdict |
|---|---:|---:|---:|---:|---|
| froth <0.7 (heavy up-vol) | +0.085 | 0.272 | 0.39 | 0.06 | high-EV → leave alone |
| bull 0.7-1.0 | +0.029 | 0.318 | 1.73 | 0.52 | low-EV (partly co-captured) |
| **neutral 1.0-1.3** | **+0.025** | 0.342 | 1.25 | 0.23 | **low-EV core** |
| bear 1.3-1.8 | +0.077 | 0.286 | 1.17 | 0.17 | high-EV → leave alone |
| panic >1.8 (capitulation vol) | +0.101 | 0.244 | 0.34 | 0.02 | high-EV → leave alone |

Inverted-U: **mid-band low-EV, both extremes high-EV** — the same shape RXDD (VIX) and MWDD
(McClellan) exploit, now on a volume-flow axis.

### Orthogonality (the decisive test)

In the **all-shipped-levers-off slice** (vix<20 AND |McClellan|>30 AND breadth>=40, dd>=0.13;
7.6% of book) where RXDD+MWDD+F3F are all OFF, **TRIN 1.0-1.3 still runs mean_pnl −0.060,
loser-rate 41.3% (z=+56.9)**. So the cohort is NOT a re-label of McClellan-flat / low-VIX /
low-breadth — it's a genuine **volume-flow-vs-breadth-momentum divergence**: strong breadth
momentum (|mcc|>30) but neutral/distributive volume conviction (TRIN ~1.1) = a weak move = the
call fails. The MWDD note's "TRIN likely McClellan-correlated → redundant" warning is **refuted
for the 1.0-1.3 band specifically.**

### Crash-artifact guard (per-window, DD-active)

TRIN 1.0-1.3 mean_pnl is low-EV in 10y/2020/2021/2025/22-now/5y/dip (vs base) — **NOT a crash
artifact** (it's low-EV in bull/choppy windows, not only crashes). Only 2024 (strong bull) is
above base, and 2024 is low-DD so the DD-gate skips it. 2020_crash is only mildly below the
(negative) crash base, and is VIX-panic-excluded anyway. The extremes (<0.7, >1.8) are high-EV
in EVERY window — robustly left alone.

## Mechanism (TVDD)

Mirror of MWDD: smooth Gaussian bump on TRIN contracts CALL alloc in the neutral-flow trough,
no-op when disabled / TRIN missing / running dd < TVDD_DD_MIN / VIX >= TVDD_VIX_PANIC.
`alloc_frac *= 1 − DEPTH·exp(−0.5·((trin − TRIN_C)/TRIN_W)²)`. Knobs TVDD_TRIN_C / TVDD_TRIN_W /
TVDD_DEPTH / TVDD_DD_MIN / TVDD_VIX_PANIC, env-overridable, default OFF (byte-identical baseline:
off → trin map not loaded → scale ×1.0). TRIN map = `MarketBreadth.trin` (on-or-before lookup),
full history; live `trader update` computes TRIN daily so live coverage is current. 4th orthogonal
Pareto DD lever stacking on RXDD(VIX) + SVR(skew) + MWDD(McClellan count-momentum).

## Nulls / confirmations from the same mine

- **The regime-axis DD-lever well is largely dry after 3 levers.** Most residual low-EV cohorts
  are the diffuse complacent-bull bulk (dd_conc ~1.0-1.2, not sharply concentrated) — contracting
  them = blunt global de-risking (capital-velocity null) or crash-artifact. TRIN-neutral is the one
  axis where low-EV AND dd_conc AND orthogonality AND crash-robustness coincide.
- **VIX-velocity:** clean MONOTONIC EV signal (rising=high-EV buy-the-dip, falling=low-EV
  post-spike drift) BUT anti-aligned for contraction — the low-EV (falling) side has dd_conc ~1.0
  (not concentrated), the high-dd-conc (rising) side is high-EV. Poor DD lever. (A REFINEMENT of
  RXDD's level-band by velocity is a possible future micro-lead.)
- **Concurrency (entry_open_calls):** the old dd_ledger concur=hi DD-conc 5.95× is now CAPTURED —
  the most-crowded band (12+) has the LOWEST dd_conc (0.68). RXDD/MWDD/DD_SOFT + MAX_POS cap
  already manage book-crowding DD. Dead as a residual lever.
- **entry_dd:** DD_SOFT_BAND confirmed working — the 0.35-0.55 band shows dd_conc 0.14 (captured).
- **A-D-line slope (ad4_up):** low-EV but McClellan-correlated (A-D family), less orthogonal than TRIN.

## Validation — SHIPPED 2026-06-07 (Stage-3 T1-T7 ALL PASS)

Stage-3 B→C→D (DD-primary). Winner **c00 = TRIN_C=1.042 / TRIN_W=0.268 / DEPTH=0.426 /
DD_MIN=0.291 / VIX_PANIC=28.0** (sweep.py + phase{B,C,D}_results.json).

- **Phase B** (LHS-16, N=100x6 incl 2020_crash): all clean candidates Pareto; top-3 → Phase C.
- **Phase C** (top-3, N=300x10 incl COVID): all collapse=0 every window; 1.042-config best
  (ddAll +2.24, 5y DD +1.2, crash +8.4, comp +0.064).
- **Phase D ship-gate** (N=500x10 incl COVID), 1.042-config vs baseline:

| window | base WorstDD | TVDD WorstDD | Δ | compound |
|---|---:|---:|---:|---|
| 2020 | 77.1 | 71.6 | +5.5 | flat |
| 2020_crash | 77.5 | 69.2 | **+8.3** | -50%→-41% |
| 2021 | 60.8 | 58.3 | +2.5 | flat |
| 2022 | 61.3 | 57.3 | +4.0 | +125%→+165% |
| 2023 | 56.8 | 51.4 | +5.4 | flat↑ |
| 2024 | 43.4 | 43.4 | +0.0 | -1.5% |
| 2025 | 61.6 | 61.9 | -0.3 | -5.3% |
| dip | 43.1 | 42.9 | +0.2 | flat |
| 22-now | 61.8 | 64.0 | -2.2 | +176k%→+226k% (+28%) |
| **5y** | **64.8** | **61.7** | **+3.1** | **+478k%→+558k% (+17%)** |

T1-T7 ALL PASS: **T4** 5y WorstDD -3.1pp · **T5** worst annual regress -2.2pp (22-now, the
compound-coupling: that window's compound +28% so the bigger curve has a bigger swing; 5y DD,
the primary, improves) · **T6** collapse=0 every window incl 2020 + 2020_crash · **T7** compound
net UP. A genuine Pareto: **-3.1pp 5y DD AND +17% 5y compound**, +8.3pp crash DD — comparable to
RXDD (-5.6pp/+9.4%), SVR (-5.8pp/+28.6%), MWDD (-2.6pp/flat).

Shipped portfolio-only (NO `ALGORITHM_VERSION` bump). Wired across all consumers (monte_carlo,
backtest_cascade, api ×2, trader ×2, strategy_config 30DTE-on/15DTE-off, mechanism_registry,
drift-guard 627, Backtest.js). Reversible: `STRATEGY_30DTE.TVDD_ENABLED=False`.
