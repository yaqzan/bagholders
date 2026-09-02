"""
SCAFFOLD / PLAN — Polygon-IV gamma prototype (the ~$79/mo, or free-tier, de-risk
for the gamma engine before the ~$2k historicaloptiondata.com buy).

WHY: the gamma MC A/B exploded (+1754% median compound) because the MC prices
premium as PREMIUM_MULT * realized_vol — too cheap for big movers. The gamma
engine itself is validated per-trade on REAL prices; the failure was the proxy
premium. Hypothesis: a real IV-based premium kills the explosion. This tests it
cheaply on ~4yr of Polygon options data (incl the 2022 bear) before buying depth.

The gamma engine + A/B already exist:
  - option_pricing.py: bs_* funcs + GAMMA_AWARE flag  (built + real-price-validated)
  - experiments/gamma_validation/run_gamma_ab.py: the paired A/B runner

STEPS (complete when you have a Polygon key — free tier is fine to prototype):
  1. INGEST ATM IV + 10%-OTM skew + forward option P&L -> parquet sidecar keyed
     (symbol, date).  ** THIS STEP IS NOW BUILT ** — see:
       - polygon_validate.py    : free-tier go/no-go gate (run FIRST)
       - polygon_iv_ingest.py   : the resumable pull -> .cache/polygon_iv/iv_ledger_polygon.parquet
       - polygon_client.py      : shared client + Black-Scholes IV solver
     Polygon has no per-date historical IV endpoint (the snapshot's
     `implied_volatility` is current-only), so IV is COMPUTED from each option's
     historical daily close via BS. The sidecar mirrors iv_skew/build_iv.py's
     iv_ledger schema so build_proxy.py + the OSK scripts consume it unchanged.
  2. PREMIUM FROM IV (the crux — one formula):
       premium_pct = 0.4 * atm_iv_annual * sqrt(DTE/365)     # 0.4 = ATM BS approx
       Replace the MC's `premium_pct = PREMIUM_MULT * sigma/100` with this where
       IV is available. Plug points: monte_carlo.py (premium calc) +
       backtest_cascade.py (premium_pct). Add an env-gated IV_PREMIUM map, same
       pattern as GAMMA_AWARE, so it's a clean A/B knob (default off = today's proxy).
  3. A/B: re-run run_gamma_ab.py over a 2021-2025 window with GAMMA_AWARE=1 AND the
       IV-premium on. SUCCESS = the +1754% collapses to sane numbers (median
       compound within ~live range, P(2x) not pinned ~100%, DD plausible).
       -> if it holds, the full historicaloptiondata.com L3 buy (~$2k, back to 2002)
          is justified to extend gamma+IV through the 2008/2020 collapse windows.
       -> if it doesn't, we learned it for ~$79 instead of ~$2k.
"""
print(__doc__)
print("Polygon-IV gamma prototype: SCAFFOLD/PLAN — complete steps 1-3 with a Polygon API key.")
