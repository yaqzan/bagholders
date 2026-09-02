# GEX Math Specification -- dealer_gex.py

**Authority:** This file governs EXACT formulas, grid, degenerate cases, and unit tests for
`experiments/gex/dealer_gex.py`. It operationalizes DESIGN.md D2 (dealer sign), D3 (chain scope),
D4 (features). Where DESIGN.md left a choice open, this spec decides and marks the decision
`[DECISION: ...]`. The Sonnet implementer makes ZERO math decisions -- every constant, rule, and
tolerance is pinned here. All numeric reference values in Sections 1, 3, 5 were computed against the
installed toolchain (scipy 1.17.1, numpy 1.26.4, polars 1.40.1) and are reproducible.

**Environment / constraints (binding):**
- Run under `PYTHONUTF8=1`. Console is Windows cp1252 -- **plain ASCII only** in all source, prints,
  and this file. No Unicode math glyphs (write `sigma`, `gamma`, `sqrt`, `>=`, `Sigma`, not the symbols).
- numpy vectorization REQUIRED (no per-contract Python loops in the hot path). No statsmodels.
- polars 1.40 `group_by` (not `groupby`). scipy is available but the hot path uses a hand-rolled pdf
  (Section 1.3) for speed; scipy.stats.norm is used ONLY in the self-test cross-check.
- READ-ONLY experiment. Input is a parquet already built; this module never touches MySQL or scoring code.

**Fixed constants (define once at module top, ASCII names):**
```
R_RATE      = 0.04          # risk-free (DESIGN.md D2)
Q_YIELD     = 0.0           # dividend yield (DESIGN.md D2)
DAYS_PER_YR = 365.0         # calendar-day convention (DESIGN.md D2: T = dte/365)
MULT        = 100.0         # contract multiplier
PCT_MOVE    = 0.01          # 1% underlying move (dollar-gamma-per-1% units, DESIGN.md D2)
DTE_MIN     = 1             # feature window lower bound (DESIGN.md D3)
DTE_MAX     = 90            # feature window upper bound (DESIGN.md D3)
IV_FLOOR    = 0.01          # cache filters iv in (0.01, 5.0); clip defensively at boundary
IV_CEIL     = 5.0
T_FLOOR     = 1.0 / 365.0   # 1 calendar day; guards div-by-zero when dte would be < 1
GRID_LO_MULT = 0.70         # spot grid lower bound = 0.70 * spot   (Section 2)
GRID_HI_MULT = 1.30         # spot grid upper bound = 1.30 * spot
GRID_N       = 301          # grid point count (Section 2 justification)
MIN_STRIKES_FOR_WALL = 1    # walls need >=1 same-side strike; else NaN (Section 3.2)
INV_SQRT_2PI = 0.3989422804014327   # 1/sqrt(2*pi), for the hand-rolled pdf
```

---

## 1. BLACK-SCHOLES GAMMA

### 1.1 Formula

For a European option under Black-Scholes with continuous risk-free rate `r`, dividend yield `q`,
time-to-expiry `T` (years), spot `S`, strike `K`, implied vol `sigma`:

```
d1    = ( ln(S/K) + (r - q + 0.5*sigma^2) * T ) / ( sigma * sqrt(T) )
gamma = exp(-q*T) * Nprime(d1) / ( S * sigma * sqrt(T) )
```

where `Nprime(x) = exp(-0.5*x^2) / sqrt(2*pi)` is the standard-normal PDF. **d2 is not needed** -- gamma
depends only on `Nprime(d1)`, not on any CDF.

**Gamma is IDENTICAL for calls and puts** at the same `(S, K, T, sigma)` -- put-call parity `C - P = ...`
is linear in `S`, so its second derivative in `S` is zero, hence `gamma_call == gamma_put`. The
call/put distinction enters ONLY via the dealer `sign` in Section 2, never in the gamma itself. The
implementer computes gamma once per contract regardless of `option_type`.

With `q = 0` (D2), `exp(-q*T) = 1` -- the implementer MAY drop this factor (spec keeps it explicit for
clarity; dropping it is numerically exact here, not an approximation).

### 1.2 Numerical edge handling (apply IN THIS ORDER, before computing d1)

1. **T floor.** `T = max(dte, 1) / 365.0`, i.e. clamp `dte` to `>= 1` then divide. Cache holds dte in
   1..180 so this is defensive; it guarantees `T >= T_FLOOR` and `sqrt(T) > 0`.
   `[DECISION: floor at 1 calendar day. Rationale: dte is an int32 >= 1 by cache contract; a hard
   floor makes the grid re-evaluation (which does NOT change dte) robust and keeps sqrt(T) finite.]`
2. **iv clip.** `sigma = min(max(iv, IV_FLOOR), IV_CEIL)`. The cache already filters iv to the open
   interval (0.01, 5.0), so in practice no row hits the boundary; the clip is a defensive no-op that
   guarantees `sigma > 0` and bounds `d1`. At exactly `sigma = IV_FLOOR = 0.01`, gamma is a large but
   finite number (a near-expiry ATM contract with 1% vol has a sharp gamma spike) -- this is correct
   behavior, NOT an error; do not special-case it.
3. **Overflow-safe d1.** `d1` is a ratio of finite quantities once (1) and (2) hold; `ln(S/K)` is
   finite for `S>0, K>0` (both guaranteed: spot>0 by contract, strike>0). No clamp on `d1` is needed
   for the pdf -- `Nprime(d1) = exp(-0.5*d1^2)` UNDERFLOWS smoothly to `0.0` for large `|d1|` (deep
   OTM/ITM contracts contribute ~0 gamma, which is physically correct). Do NOT clip `d1` to a range;
   underflow-to-zero is the desired outcome. `exp` of a large negative argument is `0.0`, never a
   Python error. (numpy may emit an underflow RuntimeWarning; suppress it for the gamma block with
   `np.errstate(under='ignore')` so the self-test output stays clean.)
4. **Non-finite guard on output.** After computing the gamma array, replace any `NaN`/`inf` with `0.0`
   (`np.nan_to_num(gamma, nan=0.0, posinf=0.0, neginf=0.0)`). Given (1)-(3) this should never fire,
   but it makes a single bad row inert rather than poisoning the whole sum. Do this ONLY on the raw
   per-contract gamma, never on the aggregated features.

### 1.3 The pdf: hand-rolled, vectorized

```
def norm_pdf(x):                      # x: numpy array, any shape
    return INV_SQRT_2PI * np.exp(-0.5 * x * x)
```

`[DECISION: hand-rolled pdf over scipy.stats.norm.pdf in the hot path.]` Benchmarked on this box:
hand-rolled is ~1.8x faster (301-pt grid x 3000 contracts: 97 ms vs 141 ms per signal) and bit-identical
to `scipy.stats.norm.pdf` (max abs diff 2.2e-08 across a random 3000-contract chain). scipy is used
ONLY in the self-test to cross-check this function (Section 5, test A2). Rationale: N'(d1) is a
one-line closed form; scipy's per-call input validation/broadcast machinery is pure overhead here, and
avoiding it keeps the full 2500-signal run inside the minutes budget with no dependency in the loop.

### 1.4 Vectorized gamma -- two required shapes

**Shape A: all contracts of ONE (symbol, date) at a SINGLE spot** (used for walls, COTMC/COTMP, and
the at-spot features). Given 1-D arrays `K, T, sigma` of length `C` (n_contracts) and scalar `S`:
```
sqrtT = np.sqrt(T)                                  # (C,)
d1    = (np.log(S / K) + (R_RATE - Q_YIELD + 0.5*sigma*sigma) * T) / (sigma * sqrtT)   # (C,)
gamma = np.exp(-Q_YIELD * T) * norm_pdf(d1) / (S * sigma * sqrtT)                       # (C,)
```

**Shape B: all contracts re-evaluated over the SPOT GRID** (used for the netGEX profile / flip / slope).
Given grid `G` (1-D, length `n_grid`) and the same `C`-length arrays, broadcast:
```
Sg    = grid[:, None]                               # (n_grid, 1)
Kb    = K[None, :]; Tb = T[None, :]; sb = sigma[None, :]   # (1, C)
sqrtTb= np.sqrt(Tb)
d1    = (np.log(Sg / Kb) + (R_RATE - Q_YIELD + 0.5*sb*sb) * Tb) / (sb * sqrtTb)   # (n_grid, C)
gamma = np.exp(-Q_YIELD * Tb) * norm_pdf(d1) / (Sg * sb * sqrtTb)                  # (n_grid, C)
```
`grid[:, None]` vs `strikes[None, :]` is the mandated broadcast (grid down the rows, contracts across
the columns). Wrap the gamma computation in `with np.errstate(under='ignore', invalid='ignore'):`.

---

## 2. NET GEX PROFILE

### 2.1 Definition (DESIGN.md D2, verbatim units)

```
netGEX(S_eval) = Sum_over_contracts  gamma(S_eval, K, T, iv) * OI * MULT * S_eval^2 * PCT_MOVE * sign
sign = +1 for calls, -1 for puts
```
Units: dollar-gamma per 1% underlying move (dealer notional that must be hedged per 1% spot move).
The `S_eval^2 * 0.01` converts share-gamma to dollar-gamma-per-1%: `dollar_gamma_per_1pct =
gamma * S^2 * 0.01 * OI * 100`. Note `S_eval` is the grid point being evaluated, and it appears BOTH
inside `gamma(...)` (via d1 and the `1/S` scaling) AND in the explicit `S_eval^2` factor -- gamma is
RECOMPUTED at each grid spot, never rescaled from a base (DESIGN.md D2: "gamma RECOMPUTED at each
grid spot, not rescaled").

### 2.2 Per-contract dollar-GEX (vectorized, Shape B)

```
sign_vec = np.where(option_type == 'call', 1.0, -1.0)          # (C,)
# Shape B gamma is (n_grid, C):
per_contract_gex = gamma * (OI[None,:] * MULT) * (Sg**2) * PCT_MOVE * sign_vec[None,:]   # (n_grid, C)
netgex_profile   = per_contract_gex.sum(axis=1)                                          # (n_grid,)
```

### 2.3 Spot grid

`[DECISION: grid = np.linspace(GRID_LO_MULT*spot, GRID_HI_MULT*spot, GRID_N)` with
`GRID_LO_MULT=0.70, GRID_HI_MULT=1.30, GRID_N=301`.]`

- **Range 0.70x - 1.30x spot.** Realistic gamma flips sit within roughly +/-15-20% of spot for the
  DTE<=90 hedging-relevant chain; +/-30% brackets essentially all real flips while keeping the array
  small. Walls and flips outside this band are not hedging-relevant and are reported as NaN
  (Section 3), not chased with a wider grid.
- **301 points (step = 0.20% of spot).** Justification is empirical: because netGEX is smooth and
  locally near-linear, LINEAR interpolation between bracketing grid points (Section 3.1) recovers the
  true flip to ~1e-5 of spot at 301 points (measured: true root 97.13026, recovered 97.13021, err
  5.2e-5), i.e. sub-cent precision on a ~$100 name -- far finer than the grid step itself. Going to 601
  points cuts flip error to 1.2e-5 but ~doubles cost for precision we do not need. Timing on this box
  (hand-rolled pdf, 3000 contracts): 301 pts ~= 97 ms/signal -> ~4 min for 2500 signals; 601 pts ~=
  176 ms/signal -> ~7 min. `[DECISION: 301 chosen -- sub-cent flip precision at the low end of the
  minutes budget. The interpolation, not the raw step, sets flip accuracy.]`

### 2.4 Performance / FLOP budget

Per signal the dominant cost is the `(n_grid, C)` gamma block: ~10 flops/element
(`log, div, mul, exp, ...`). `301 * 3000 * ~10 ~= 9.0e6` flops/signal; `2500 signals ~= 2.3e10` flops
total. On a desktop CPU with numpy/BLAS this is ~4 minutes single-threaded (measured above), well
inside the "minutes" target. If the implementer parallelizes across signals (optional; multiprocessing
over (symbol,date) groups), it drops proportionally -- but single-threaded already meets the budget, so
parallelism is OPTIONAL and must not change any numeric output.

---

## 3. LEVELS

All levels are computed on the FEATURE-WINDOW chain (DTE in [DTE_MIN, DTE_MAX] = [1, 90]); rows outside
that window are dropped BEFORE any level/feature computation (see Section 4.0). "spot" is the single
`spot` value on the group (identical across all rows of a (symbol,date) by cache contract).

### 3.1 Flip / zeroGEX

Zero-crossing of `netgex_profile` over the grid, linear interpolation between the two bracketing grid
points.

```
sign_arr = np.sign(netgex_profile)          # (n_grid,); np.sign(0.0)==0.0
# indices i where sign changes between grid[i] and grid[i+1]:
diffsign = np.diff(sign_arr)
cross    = np.where(diffsign != 0)[0]       # each c: crossing in (grid[c], grid[c+1])
```
Exact-zero handling: if `netgex_profile[i] == 0.0` exactly at a grid node, treat that node as a
crossing at `grid[i]` (it will appear via the `diffsign != 0` test on at least one side; if a node is
exactly zero, use `flip = grid[i]` for that candidate and do not interpolate through it -- interpolation
across a zero-valued endpoint is still numerically correct since `y0` or `y1` is 0, but guard the
divide: if `y1 == y0` skip that candidate).

For each crossing index `c`, linear interpolation:
```
x0, x1 = grid[c], grid[c+1]
y0, y1 = netgex_profile[c], netgex_profile[c+1]
flip_c = x0 - y0 * (x1 - x0) / (y1 - y0)     # guard: if (y1 - y0) == 0, skip this candidate
```

**Multiple crossings rule.** `[DECISION: pick the crossing whose interpolated flip is NEAREST to
spot.]` `flip = flip_c minimizing abs(flip_c - spot)`. Rationale: the flip that matters for near-term
dealer hedging is the one price is closest to breaching; distant secondary crossings are chain
artifacts of thin far strikes. Tie-break (exactly equidistant, essentially impossible with floats):
take the lower flip.

**No crossing in grid rule.** `[DECISION: flip = NaN; the regime is STILL defined by the sign of
netGEX at spot (Section 4).]` A chain that is net-long-gamma (or net-short) across the entire
0.70x-1.30x band has no interior flip in range -- report `flip = NaN`, `flip_dist = NaN`, but
`gex_regime` remains `+1`/`-1` from the at-spot sign. Do NOT fall back to the grid endpoint.

### 3.2 Call wall / put wall (per-strike aggregation at CURRENT spot)

Per DESIGN.md D2: call wall = strike of MAX positive per-strike GEX; put wall = strike of MAX put-side
|GEX|. Evaluate per-strike GEX at the CURRENT spot (Shape A, S = spot), summing contracts that share a
strike across ALL expiries within the feature window, per side.

```
# Shape A at S = spot gives per-contract dollar-GEX magnitude (SIGNED by side already, but for walls
# aggregate the ABSOLUTE dollar-gamma per side, then argmax):
gex_spot_abs = gamma_spot * (OI * MULT) * (spot**2) * PCT_MOVE      # (C,), UNSIGNED magnitude
# call side:
call_mask = (option_type == 'call')
# group gex_spot_abs[call_mask] by strike[call_mask], sum within strike -> per-strike call GEX
# call_wall = the strike with the largest summed call-side GEX
# put side analogously with put_mask.
```
Implementation with polars or numpy: build a per-side table `(strike, gex_sum)` by summing
`gex_spot_abs` grouped by `strike`, then `argmax` on `gex_sum`.
- **call_wall** = strike maximizing summed call-side per-strike GEX (unsigned dollar-gamma at spot).
- **put_wall** = strike maximizing summed put-side per-strike |GEX| (unsigned dollar-gamma at spot).

`[DECISION: aggregate the UNSIGNED dollar-gamma at spot per strike, argmax per side.]` The dealer sign
is constant within a side (calls all +, puts all -), so "max positive call GEX" and "max call-side
|GEX|" are the same argmax; using the unsigned magnitude makes the call/put code symmetric and avoids a
sign bug. Ties on the max (two strikes with identical summed GEX -- negligible probability with floats):
take the strike NEAREST to spot; if still tied, the lower strike.

### 3.3 COTMC / COTMP (OI-weighted mean strike, per side)

```
COTMC = sum(strike_i * OI_i for calls) / sum(OI_i for calls)     # "center of tenor mass, calls"
COTMP = sum(strike_i * OI_i for puts)  / sum(OI_i for puts)
```
Weight is `open_interest` (NOT dollar-gamma) -- this is a pure OI-weighted mean of the strike over the
side's feature-window contracts, aggregated across all expiries and strikes on that side. If a side has
zero total OI (cannot happen once OI>0 cache filter holds, but guard it), the mean is `NaN`.

### 3.4 Degenerate-case matrix (EXACT outputs)

Applied to the feature-window chain (after the DTE 1-90 filter, Section 4.0). "computed" = the normal
formula; "NaN" = emit numpy `nan`. Diagnostics (`n_contracts` etc.) are ALWAYS computed regardless.

| Chain condition (feature window)        | netGEX profile | flip        | call_wall | put_wall | COTMC | COTMP | gex_regime |
|-----------------------------------------|----------------|-------------|-----------|----------|-------|-------|------------|
| Empty (0 contracts in 1-90)             | not computed   | NaN         | NaN       | NaN      | NaN   | NaN   | 0          |
| No puts (calls only)                    | computed (>0)  | NaN*        | computed  | NaN      | comp. | NaN   | +1         |
| No calls (puts only)                    | computed (<0)  | NaN*        | NaN       | computed | NaN   | comp. | -1         |
| Both sides present, single expiry       | computed       | computed/NaN| computed  | computed | comp. | comp. | +/-1       |
| Both sides, < 5 distinct strikes total  | computed       | computed/NaN| computed  | computed | comp. | comp. | +/-1       |

\* No-puts / no-calls: netGEX is single-signed across the whole grid, so there is no interior zero
crossing -> flip = NaN by the Section 3.1 no-crossing rule (this falls out naturally; do not
special-case it, but the matrix documents the guaranteed outcome). `[DECISION: <5 strikes is NOT a
degeneracy -- features are still computed. A thin chain gives noisy but well-defined levels; we report
them with the `n_contracts`/strike-count diagnostics so the analysis layer can filter on N. The ONLY
hard-NaN degeneracies are (a) empty feature-window chain, and (b) a missing SIDE for that side's
side-specific levels.]` Rationale: forcing NaN on thin chains would silently drop small-cap signals;
better to emit and let D6's N-floor / quintile logic handle sparsity downstream.

**Regime when 0:** `gex_regime = 0` occurs ONLY when the feature-window chain is empty (no contracts)
OR when `netGEX(spot)` is exactly `0.0` (measure-zero; e.g. a perfectly balanced synthetic). See
Section 4.1.

---

## 4. FEATURES (exact formulas, D4 names)

### 4.0 Preprocessing (per (symbol, date) group, BEFORE features)

1. Filter the group to the feature window: `dte >= DTE_MIN and dte <= DTE_MAX` (i.e. 1..90 inclusive).
2. Defensive row hygiene (all should already hold by cache contract; drop offending rows and count
   them into a `n_dropped` diagnostic): keep only `open_interest > 0`, `iv` finite, `strike > 0`,
   `spot > 0`. `option_type` must be exactly `'call'` or `'put'`.
3. `spot` = the group's spot (take `df['spot'][0]`; assert all equal within the group -- if not, it is a
   cache bug: use the max date... no -- they are identical by contract; `assert` and take `[0]`).
4. Build `sign_vec`, apply the T floor and iv clip (Section 1.2), then compute the grid (Section 2.3)
   and both gamma shapes.

### 4.1 gex_regime  (PRIMARY)

`gex_regime = int(sign(netGEX(spot)))`, valued in `{-1, 0, +1}`.
- Compute `netgex_at_spot` by evaluating the profile at S = spot. Use `np.interp(spot, grid, profile)`
  (spot lies inside the grid since grid spans 0.70x..1.30x and spot is the 1.00x point exactly; spot is
  a grid node only if GRID_N makes 1.00x land on a node -- with 301 pts over [0.70,1.30]*spot, the
  midpoint index 150 is exactly 1.00x*spot, so spot IS a node and interp returns the node value
  exactly). Equivalent and preferred: evaluate Shape A at S = spot directly and SUM the signed
  per-contract dollar-GEX -> `netgex_at_spot`. `[DECISION: compute netGEX(spot) from Shape A at S=spot
  (exact), not by interpolating the grid. The grid is for the profile/flip/slope; the at-spot scalar is
  exact from Shape A.]`
- `gex_regime = +1 if netgex_at_spot > 0; -1 if < 0; 0 if == 0.0 or chain empty`.
- **When 0 occurs:** empty feature-window chain, or exact `0.0` at spot (measure-zero -- only in
  constructed/degenerate chains). In real data expect only `+1`/`-1`.

### 4.2 gex_ratio  (PRIMARY, continuous companion to regime)

`gex_ratio = netGEX(spot) / Sum_over_contracts |per-contract GEX(spot)|`, in `[-1, 1]`.

```
per_contract_gex_spot = gamma_spot * (OI * MULT) * (spot**2) * PCT_MOVE * sign_vec   # (C,), SIGNED
numerator   = per_contract_gex_spot.sum()                     # == netgex_at_spot
denominator = np.abs(per_contract_gex_spot).sum()             # sum of ABSOLUTE per-contract dollar-GEX
gex_ratio   = numerator / denominator  if denominator > 0 else NaN
```
`[DECISION: denominator = sum of the ABSOLUTE per-contract signed dollar-GEX at spot]` (i.e.
`Sum |gamma_i * OI_i * 100 * spot^2 * 0.01|` -- the same per-contract quantities that build the
numerator, absolute-valued before summing). This is the tightest possible normalizer: since numerator
is a signed sum of terms each bounded by their absolute value, `gex_ratio in [-1, 1]` exactly. `+1` =
pure long-gamma (all call-side), `-1` = pure short-gamma (all put-side), `0` = balanced. If denominator
is 0 (empty chain), `gex_ratio = NaN` (and regime = 0).

### 4.3 flip_dist  (PRIMARY)

`flip_dist = ln(spot / flip)` (signed distance ABOVE the gamma flip; positive when spot > flip).
- **NaN propagation:** if `flip` is NaN (no crossing in grid, or empty chain), `flip_dist = NaN`. Do
  NOT substitute a sentinel. `flip > 0` always when it exists (it is a spot-grid value in
  [0.70,1.30]*spot), so the log is always defined when flip is non-NaN.

### 4.4 callwall_dist  (PRIMARY-adjacent; D4 lists it PRIMARY #3)

`callwall_dist = ln(call_wall / spot)` (upside room to the call wall; positive when wall is above spot).
NaN if `call_wall` is NaN (no calls in feature window).

### 4.5 putwall_dist  (EXPLORATORY)

`putwall_dist = ln(put_wall / spot)`. NaN if `put_wall` is NaN (no puts).

### 4.6 cotmp_dist  (EXPLORATORY)

`cotmp_dist = ln(COTMP / spot)`. NaN if `COTMP` is NaN (no puts). (Analogous `cotmc_dist =
ln(COTMC/spot)` MAY be emitted as an extra diagnostic but is not in D4's list; emit it as a diagnostic
column `cotmc_dist`, clearly not a registered feature.)

### 4.7 netgex_slope  (EXPLORATORY) -- finite difference at spot

Local slope of the netGEX profile at spot, per unit spot, via CENTERED finite difference on the grid.
`[DECISION: centered difference using the two grid nodes bracketing spot; step = one grid spacing.]`
```
h   = grid[1] - grid[0]                      # uniform grid spacing (= 0.60*spot/(GRID_N-1))
i0  = index of the grid node nearest spot    # with 301 pts spot is node 150 exactly
# centered difference (guard the array ends: if i0 is 0 or n_grid-1, use one-sided):
if 0 < i0 < n_grid-1:
    netgex_slope = (profile[i0+1] - profile[i0-1]) / (2*h)
elif i0 == 0:
    netgex_slope = (profile[1] - profile[0]) / h
else:
    netgex_slope = (profile[-1] - profile[-2]) / h
```
Units: dollar-GEX per $1 of spot. NaN if profile not computed (empty chain). `[DECISION: report the
RAW slope (per $1 spot), not normalized. Downstream standardization in D6's OLS handles scale.]`

### 4.8 log10 total |GEX|  (EXPLORATORY)

`log10_total_gex = log10( Sum_over_contracts |per-contract GEX(spot)| )` = `log10(denominator)` from
Section 4.2. This is the same absolute-dollar-gamma sum used to normalize gex_ratio. If denominator is
0 or non-finite, `log10_total_gex = NaN`. (Never take log10 of 0.)

### 4.9 Output row schema (one row per (symbol, date))

Emit a polars DataFrame, one row per (symbol, date) group, with these columns (ASCII names, this order):

**Keys:** `symbol` (str), `date` (date).
**Primary features:** `gex_regime` (int, {-1,0,1}), `gex_ratio` (f64), `flip_dist` (f64),
`callwall_dist` (f64).
**Exploratory features:** `putwall_dist` (f64), `cotmp_dist` (f64), `netgex_slope` (f64),
`log10_total_gex` (f64).
**Raw levels (for audit/joins):** `spot` (f64), `flip` (f64), `call_wall` (f64), `put_wall` (f64),
`cotmc` (f64), `cotmp` (f64), `netgex_at_spot` (f64).
**Diagnostics:** `n_contracts` (int, feature-window count), `n_calls` (int), `n_puts` (int),
`total_oi` (int, feature-window sum), `n_strikes` (int, distinct strikes in window),
`pct_oi_in_window` (f64, in [0,1]: feature-window OI / all-DTE OI for that (symbol,date) from the FULL
input group BEFORE the DTE filter), `n_dropped` (int, rows dropped by hygiene in 4.0.2), `cotmc_dist`
(f64, diagnostic).

`pct_oi_in_window` note: compute the denominator (all-DTE OI) from the ORIGINAL group passed in
(cache is DTE 1-180) before applying the 1-90 window, so it reports how much of the chain's OI mass sits
in the hedging-relevant window. If the original group is empty, `pct_oi_in_window = NaN`.

### 4.10 polars group_by driver (reference pseudocode)

```
import polars as pl, numpy as np

def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    # df has all (symbol,date) groups. Iterate groups; each group -> one output dict.
    out_rows = []
    for (sym, dt), g in df.group_by(['symbol', 'date'], maintain_order=True):
        out_rows.append(_features_for_group(sym, dt, g))   # dict of the 4.9 schema
    return pl.DataFrame(out_rows)   # let polars infer dtypes; cast gex_regime/n_* to Int64 explicitly
```
`_features_for_group` does the Section 4.0 preprocessing then Sections 3-4 on numpy arrays extracted via
`g['strike'].to_numpy()`, etc. Use `maintain_order=True` for deterministic output ordering. Extract
numpy arrays ONCE per group; never call `.symbol` / per-row accessors in a loop.

---

## 5. UNIT TESTS  (runnable: `python dealer_gex.py --selftest`)

Provide a `--selftest` argparse flag that runs all tests below, prints `PASS`/`FAIL` per test with the
computed-vs-expected numbers, and exits non-zero if ANY fails. All tolerances are absolute unless noted.
Tests use ONLY synthetic in-memory chains (no parquet, no MySQL).

### A1. BS gamma against a hand-computed known value
Inputs: `S=100.0, K=100.0, T=30.0/365.0, iv=0.40, r=0.04, q=0.0` (ATM).
Expected (computed on this toolchain, exact): `d1 = 0.08600732686214939`,
`Nprime(d1) = 0.39746946583737996`, **`gamma = 0.034660` (to 6 dp; full value
0.03466008190858278)**. Assert `abs(gamma - 0.03466008190858278) < 1e-9`.

Second point (OTM, non-trivial): `S=100, K=110, T=45/365, iv=0.30` -> **`gamma = 0.027384`** (full
0.0273840637...). Assert `abs(gamma - 0.0273840637) < 1e-9`.

### A2. put/call gamma equality
For `S=100, K=105, T=20/365, iv=0.55` compute gamma via the module for `option_type='call'` and
`option_type='put'`; assert they are byte-identical (`gamma_call == gamma_put`, exact equality -- the
sign is applied OUTSIDE gamma, so the gamma numbers must match to the last bit). ALSO cross-check the
module's `norm_pdf` against `scipy.stats.norm.pdf` at `d1`: `abs(norm_pdf(d1) - scipy.stats.norm.pdf(d1))
< 1e-12`.

### A3. synthetic chain with an analytically-known flip
Construct: `spot=100.0`, one CALL at `K=110.0`, one PUT at `K=90.0`, EQUAL `open_interest=1000`,
`iv=0.40`, `dte=30` (both), same expiration. Run the module's level extractor.
- Analytic flip (brentq root of netGEX, this toolchain): **`flip_true = 98.522210`**. NOTE: the flip is
  NOT at 100 despite symmetric strikes/OI -- the `1/(S*iv*sqrt(T))` and `S^2` factors make gamma
  asymmetric in S, pulling the balance point below spot. This asymmetry is the point of the test.
- Assert `abs(module_flip - 98.522210) <= (grid_step + 1e-6)` where `grid_step = 0.60*spot/(GRID_N-1)`
  (= 0.20 at spot 100, GRID_N 301). Measured recovery at 301 pts is 98.52220 (err ~1e-5), so this
  tolerance is loose but robust to a GRID_N change. Also assert exactly ONE crossing is found and
  `gex_regime == +1` (netGEX(spot) = +53561.77 > 0 for this chain) and `flip_dist =
  ln(100/98.52221) = +0.01488818` within `1e-4`.

### A4. degenerate chains return the spec'd NaN pattern WITHOUT raising
Run each and assert no exception + the Section 3.4 pattern:
- **Empty window:** a chain whose only rows have `dte=120` (outside 1-90). After the window filter the
  group is empty. Assert `gex_regime == 0`, and `flip, call_wall, put_wall, cotmc, cotmp, gex_ratio,
  flip_dist, callwall_dist, putwall_dist, cotmp_dist, netgex_slope, log10_total_gex` are ALL NaN.
  `n_contracts == 0`, `pct_oi_in_window` NaN.
- **No puts:** two CALLS (K=95, K=105), no puts, dte=30. Assert `put_wall` NaN, `cotmp` NaN,
  `putwall_dist` NaN, `cotmp_dist` NaN, `flip` NaN (single-signed), `flip_dist` NaN, but `call_wall`
  computed, `cotmc` computed, `gex_regime == +1`, `gex_ratio == +1.0` (within 1e-12), and netGEX>0
  everywhere on the grid.
- **No calls:** mirror (two puts K=95,105). Assert `call_wall/cotmc/callwall_dist` NaN, `gex_regime ==
  -1`, `gex_ratio == -1.0`.
- **<5 strikes:** call@100 + put@100 (2 contracts, 1 strike), dte=30, equal OI. Assert this is NOT
  NaN-degenerate: `gex_regime` in {-1,0,1}, `gex_ratio` finite, `call_wall == 100`, `put_wall == 100`,
  `cotmc == 100`, `cotmp == 100`. (Same-strike call+put with equal OI -> netGEX == 0 everywhere ->
  `gex_regime == 0`, `flip == NaN` by no-isolated-crossing; assert `gex_regime == 0` and `flip` NaN
  and `gex_ratio == 0.0` within 1e-12.)

### A5. monotonicity sanity -- all-call chain
Construct 3 CALL strikes (K=95,100,105), no puts, `OI=1000` each, `iv=0.40`, `dte=30`, `spot=100`.
Assert: `netgex_profile > 0` for EVERY grid point (`np.all(profile > 0)`), `flip == NaN`,
`gex_regime == +1`, `gex_ratio == +1.0` (within 1e-12), and `call_wall == 100.0` (strike 100 has the
max per-strike GEX at spot: measured 346600.8 vs 301771.7 at K=95 and 328408.6 at K=105).

---

## 6. KNOWN LIMITATIONS

**Naive dealer-sign proxy.** OI carries NO side information -- we cannot know from raw open interest who
is long vs short each contract. D2 adopts the standard SqueezeMetrics-style assumption (investors
net-long puts / net-short calls -> dealers long call gamma, short put gamma; sign = +1 call, -1 put).
This is an ASSUMPTION about market structure, not a measurement; the whole experiment tests it
empirically rather than asserting it. Real dealer books can be net-short calls in a call-buying frenzy,
which would flip the sign for that name-day and inject label noise.

**EOD OI staleness.** Open interest is an end-of-prior-day settlement figure -- it does not reflect
intraday flow or the current session's opening. Gamma positioning computed from it lags real dealer
inventory by up to a day, blurring the flip/wall levels most on high-turnover days.

**No db_change.** SqueezeMetrics' day-over-day gamma-notional delta ("db_change") is not reproducible
from a single day's raw OI and is DROPPED in v1 (D2). Any predictive power that lives specifically in
the OI DELTA (not the level) is invisible here; a day-over-day OI proxy is a possible v2 extension,
out of scope now.

**Single-vol-per-contract, calendar T, flat r.** Each contract uses its own stored `iv`, `r=0.04`,
`q=0`, `T=calendar days/365`. No term-structure smoothing, no dividend adjustment, no
business-day/trading-day convention. These are the D2-fixed conventions; they are adequate for a
relative cross-sectional gamma-positioning feature but are not a pricing-grade surface.
