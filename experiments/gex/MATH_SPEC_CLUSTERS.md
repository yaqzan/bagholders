# GEX Cluster-Segmentation Math Specification -- MATH_SPEC_CLUSTERS.md

**Authority:** This file governs the EXACT algorithm, constants, degenerate cases, and locked unit
tests for GAMMA CLUSTER SEGMENTATION, the "Shared: cluster segmentation" deliverable of
`experiments/gex/DESIGN_CLUSTER.md`. It EXTENDS `experiments/gex/MATH_SPEC.md` (the per-strike
dollar-gamma data model) -- it does not restate or change any MATH_SPEC.md rule. The Sonnet implementer
makes ZERO math decisions: every constant, rule, tie-break, tolerance, and degenerate output is pinned
here. Every numeric reference value in Sections 4 was computed on the installed toolchain
(numpy 1.26.4, scipy 1.17.1, polars 1.40.1) and is reproducible.

**Where this code lives:** these features are computed inside `dealer_gex.py`'s existing
`features_for_group(...)` (MATH_SPEC.md Section 4), appended to the Section 4.9 output row. The
implementer adds a `segment_clusters(...)` helper and a `cluster_features(...)` helper, calls them once
per (symbol, date) group after the walls block, and emits the new columns. No new module, no new CLI.

**Environment / constraints (binding, inherited from MATH_SPEC.md):**
- Run under `PYTHONUTF8=1`. Console is Windows cp1252 -- **plain ASCII only** in all source, prints, and
  this file. No Unicode math glyphs (write `sigma`, `gamma`, `>=`, `+/-`, `ln`, not the symbols).
- **numpy-only math.** No scipy anywhere in this feature (not even in the self-test -- MATH_SPEC.md
  already cross-checks `norm_pdf` against scipy; clustering needs no such check). **No
  `scipy.signal.find_peaks`** and no scipy at all: the segmentation is a hand-rolled single left-to-right
  pass whose every branch is pinned by the Section 4 self-tests.
- No statsmodels. READ-ONLY experiment. Never touches MySQL or scoring code.
- Deterministic: identical input array => byte-identical output. No randomness, no data-dependent
  iteration counts beyond a single O(n_strikes) forward scan (Section 5).

**Fixed constants (add to `dealer_gex.py`'s top-of-file constant block, ASCII names):**
```
GAP_MASS_FRAC    = 0.01    # a strike is an ACTIVE node iff its per-strike mass >= GAP_MASS_FRAC * total_mass
MIN_CLUSTER_FRAC = 0.05    # a candidate run is a CLUSTER iff its summed mass_share >= MIN_CLUSTER_FRAC
SPACING_GAP      = 0.05    # split between consecutive active strikes when ln(K_hi/K_lo) > SPACING_GAP
LOCAL_PCT        = 0.02    # +/-2% of spot band for local_density
```
Each is justified and marked `[DECISION]` in Section 2.

---

## 1. INPUT -- the per-strike aggregated dollar-gamma profile at current spot

### 1.1 What already exists in `dealer_gex.py`

Inside `features_for_group(...)` (MATH_SPEC.md Section 4), after Section 4.0 preprocessing, the group's
feature-window contracts (DTE in [DTE_MIN, DTE_MAX] = [1, 90]; row hygiene applied; `option_type` in
{'call','put'}) are held as parallel 1-D numpy arrays of length `C` (n_contracts):
```
strike        # (C,) float64, per-contract strike K
option_type   # (C,) object, 'call' / 'put'
K, OI         # (C,) float64: K = strike.astype(f64); OI = open_interest.astype(f64)
spot          # scalar float64, identical across the group by cache contract
```
`compute_walls_at_spot(...)` (MATH_SPEC.md Section 3.2) already evaluates Black-Scholes gamma at
`S = spot` (Shape A) and returns, per contract, the UNSIGNED and SIGNED dollar-gamma at spot:
```
gex_spot_abs    # (C,) float64: gamma_spot * (OI * MULT) * (spot**2) * PCT_MOVE   -- UNSIGNED magnitude
gex_spot_signed # (C,) float64: gex_spot_abs * sign_vec                            -- SIGNED (+call, -put)
```
These are exactly the two arrays defined at MATH_SPEC.md Section 4.2 / lines 175-176 of `dealer_gex.py`.
`gex_spot_abs` is the per-contract dollar-gamma MASS at current spot (not a density -- it is already the
notional to hedge per 1% move; no bin-width normalization is applied or wanted). `_side_wall(...)`
already demonstrates the per-strike aggregation pattern (`np.unique(..., return_inverse=True)` +
`np.add.at`) this spec reuses, but does it PER SIDE for the walls. Cluster segmentation needs the
BOTH-SIDES-COMBINED per-strike profile.

### 1.2 The cluster INPUT: build the combined per-strike profile (Section 3.0 of this spec)

Aggregate `gex_spot_abs` and `gex_spot_signed` by strike across ALL contracts (calls AND puts pooled),
producing three ascending-strike-indexed arrays:
```
uniq, inv = np.unique(K, return_inverse=True)     # uniq: (S,) DISTINCT strikes, STRICTLY ASCENDING
mass  = np.zeros(uniq.shape[0]); np.add.at(mass,  inv, gex_spot_abs)      # (S,) per-strike UNSIGNED mass
smass = np.zeros(uniq.shape[0]); np.add.at(smass, inv, gex_spot_signed)   # (S,) per-strike SIGNED gamma
oistk = np.zeros(uniq.shape[0]); np.add.at(oistk, inv, OI)                # (S,) per-strike OI (diagnostic)
```
- `S = uniq.shape[0]` = number of distinct strikes in the feature window.
- `uniq` is strictly ascending (guaranteed by `np.unique`). All Section 2/3 logic assumes this order.
- `mass[i]` = total UNSIGNED dollar-gamma-per-1% at strike `uniq[i]`, summed over every call and put at
  that strike across all expiries in the window. This is the segmentation input. **Segmentation uses
  `mass` (unsigned) only** -- the call/put sign does NOT enter cluster geometry (a gamma wall is a wall
  regardless of dealer sign; MATH_SPEC.md Section 1.1: gamma is identical for calls and puts). `smass`
  and `oistk` are carried for optional diagnostics, not used by any Section 3 feature.
- `spot` is the same scalar used everywhere else in the group.

**This spec never re-derives gamma.** It consumes `gex_spot_abs` exactly as `compute_walls_at_spot`
produced it. The implementer MUST reuse the already-computed `gex_spot_abs` array, not recompute Shape A.

### 1.3 Why unsigned mass, and why NOT a density / log-moneyness KDE  `[DECISION]`

`[DECISION: segment the RAW per-strike unsigned-mass sequence in strike-ascending order; do NOT smooth
into a density and do NOT resample onto a log-moneyness grid.]` Rationale:
- Dollar-gamma is already a MASS (notional per 1% move), not a density over strike. Converting to a
  density would require dividing by an irregular bin width (strikes are unevenly spaced) -- introducing a
  bin-width convention that is itself a free parameter and a source of non-determinism at the edges. The
  mass at a strike node is the physically meaningful quantity; keep it.
- A kernel/KDE smoothing step introduces a bandwidth as a second free parameter and makes the peak set
  bandwidth-dependent and harder to pin in a self-test. We avoid it entirely.
- Irregular strike SPACING is handled directly and deterministically by the log-moneyness `SPACING_GAP`
  split (Section 2.2), which is the ONE place strike geometry (not just index adjacency) matters. Working
  the gap test in log-moneyness (`ln(K_hi/K_lo)`) makes the gap threshold scale-free across a $30 name
  and a $600 name -- the correct invariance for an options chain.

---

## 2. CLUSTER SEGMENTATION ALGORITHM

Operates on `(uniq, mass, spot)` from Section 1.2. Produces an ORDERED list of clusters, each a dict:
`{lo_idx, hi_idx, lo_strike, hi_strike, center, mass, mass_share}`, sorted STRICTLY ASCENDING by
`lo_idx` (equivalently by `lo_strike`, `hi_strike`, and `center` -- clusters are disjoint contiguous
index blocks so all four orderings agree).

### 2.1 Active nodes (the mass floor)  `[DECISION]`

```
total = float(mass.sum())
low_thresh = GAP_MASS_FRAC * total
active_idx = np.where(mass >= low_thresh)[0]      # ascending indices of ACTIVE strikes
```
A strike node is ACTIVE iff its mass is at least `GAP_MASS_FRAC = 1%` of the window's total gamma mass.
Sub-1% strikes are treated as background/noise "valley" nodes and cannot seed or extend a cluster.
`[DECISION: GAP_MASS_FRAC = 0.01. Rationale: 1% of total gamma mass is a low, physically-inert floor
that strips single deep-OTM thin strikes (chain dust) out of cluster bodies while never erasing a real
concentration -- a genuine wall carries orders of magnitude more than 1%. It is a comparison against a
GLOBAL total, so it is scale-free in dollars. Comparison is `>=` so a node at exactly the floor is
active (deterministic boundary).]`

### 2.2 Splitting active nodes into candidate runs (index-gap OR spacing-gap)  `[DECISION]`

Walk `active_idx` left to right, accumulating a `run` of indices. A BOUNDARY between the current run's
last active index `prev` and the next active index `cur` is declared iff EITHER condition holds:
```
contiguous_index = (cur == prev + 1)                 # no INACTIVE node lies between them
spacing          = float(np.log(uniq[cur] / uniq[prev]))
boundary = (not contiguous_index) or (spacing > SPACING_GAP)
```
- **Index-gap split (`not contiguous_index`):** if one or more INACTIVE (sub-floor) nodes sit between
  two active strikes, that low-mass valley separates two clusters. (This is the classic peak-valley-peak
  split.)
- **Spacing-gap split (`spacing > SPACING_GAP`):** if two active strikes are ADJACENT in the strike grid
  (no node between them) but far apart in log-moneyness -- e.g. a chain that lists strikes 100 then 120
  with nothing at 105/110/115 -- they belong to different clusters even though no valley node exists to
  separate them. Without this test, a sparse grid would merge visibly distinct concentrations.
  `[DECISION: SPACING_GAP = 0.05 (5% log-moneyness). Rationale: 5% is comfortably wider than the
  fraction-of-a-percent spacing between neighboring listed strikes on a liquid chain (so it NEVER splits
  a genuine contiguous cluster body), yet narrow enough that a two-standard-strikes-wide empty gap
  (>= ~5% apart) reads as a real gap between walls. This is THE parameter that makes irregular strike
  spacing deterministic; it is applied only between consecutive ACTIVE strikes.]`
When a boundary is declared, finalize the current `run` and start a new run at `cur`; otherwise append
`cur` to the current run. Finalize the last run at the end.

Because a new index is only ever appended when `cur == prev + 1` (contiguous) AND spacing is small, every
finalized run is a CONTIGUOUS block of indices `[lo_idx, hi_idx]` with no inactive node inside it -- so
the slice `mass[lo_idx : hi_idx+1]` equals the run's members exactly (used below).

### 2.3 Finalizing a run: mass, share, center, edges  `[DECISION]`

For a run spanning indices `[lo, hi]` (inclusive, contiguous):
```
w          = mass[lo : hi+1]
k          = uniq[lo : hi+1]
run_mass   = float(w.sum())
mass_share = run_mass / total
center     = float((w * k).sum() / w.sum())     # MASS-WEIGHTED mean strike
lo_strike  = float(uniq[lo])                    # LOWER EDGE  = first active strike of the run
hi_strike  = float(uniq[hi])                    # UPPER EDGE  = last active strike of the run
```
`[DECISION: cluster CENTER = mass-weighted mean strike over the run's active nodes]` (not the argmax
strike, not the geometric midpoint). The mass-weighted mean is the gamma "center of mass" the escape
hypothesis targets and is robust to a skewed run. `[DECISION: cluster EDGES are the run's FIRST and LAST
active strike]` -- `lo_strike` = lowest active strike in the run, `hi_strike` = highest. **A gap begins
immediately above `hi_strike` and below the next cluster's `lo_strike`.** Edges are strike VALUES on real
listed strikes, never interpolated. The width of the gap between cluster `a` (below) and cluster `b`
(above) is the open strike interval `(a.hi_strike, b.lo_strike)`.

### 2.4 The cluster mass-share floor (which runs survive)  `[DECISION]`

A candidate run is promoted to a CLUSTER iff `mass_share >= MIN_CLUSTER_FRAC`:
```
clusters = [c for c in finalized_runs if c['mass_share'] >= MIN_CLUSTER_FRAC]
```
`[DECISION: MIN_CLUSTER_FRAC = 0.05. A run must hold >= 5% of total window gamma mass to count as a
cluster.]` Rationale: 5% is the prominence/mass threshold the design asks for -- it discards minor
gamma bumps (which are gaps for escape purposes) while keeping every economically material wall. Chosen,
not swept: this is a pre-registered feature-construction constant, not a tuned hyperparameter, so it is
fixed a priori and never fit to the label. Discarded runs are NOT reassigned to a neighbor and their mass
is NOT redistributed -- they simply become part of the low-gamma gap between surviving clusters (their
mass still counts in `total`, hence in every `mass_share` denominator and in `local_density`). Comparison
is `>=` so a run at exactly 5% survives (deterministic). NOTE: it is possible for ALL runs to fall below
5% only if mass is spread ultra-thin across many equal tiny runs; with a real chain at least one run
(the one containing the modal strike) will always exceed 5%. If the filtered list is empty, treat as the
zero-cluster degenerate case (Section 3.3 / matrix row "n_clusters == 0").

### 2.5 The DOMINANT cluster  `[DECISION]`

```
dominant = the cluster with the MAXIMUM run_mass.
tie-break: if two clusters share the exact max mass (measure-zero with floats),
           take the one with the SMALLER center (lower strike).
```
`[DECISION: dominant = MAX-MASS cluster, NOT the spot-containing cluster.]` Rationale: the design's
escape mechanic is "price escapes FROM the dominant gamma concentration." The dominant concentration is
the heaviest wall, which is a stable property of the chain independent of where spot currently sits --
and critically, **spot may sit in a GAP already** (no containing cluster), so a "cluster containing spot"
definition is undefined exactly when the escape signal is most interesting. Max-mass is always defined
whenever >= 1 cluster exists. `esc_above` / `esc_below` (Section 3) are then measured against THIS single
dominant cluster's edges, giving one coherent "have we escaped the main wall" state. We do NOT compute
separate dominant-below / dominant-above objects -- a single global-max dominant is cleaner and is what
the pre-registered `esc_above` / `esc_below` binaries in DESIGN_CLUSTER.md reference ("the dominant gamma
cluster", singular).

### 2.6 Determinism statement

Every step is a single deterministic forward pass over ascending `uniq`: `np.unique` (sorted),
`np.where` (ascending), one `zip(active_idx[:-1], active_idx[1:])` loop with pure comparisons, one list
comprehension, one `max`. No RNG, no set/dict iteration order dependence (dicts are built but only
list-appended and indexed, never iterated for order-sensitive output), no convergence loop, no
platform-variant reduction (sums are plain `np.add.at` / `.sum()` in a fixed order). Identical
`(uniq, mass, spot)` => byte-identical cluster list and features. Verified: two consecutive full runs of
the Section 4 derivation are byte-identical.

---

## 3. OUTPUT FEATURES (exact formulas; names pre-registered in DESIGN_CLUSTER.md)

Given the surviving `clusters` list (ascending), the `dominant` cluster, `uniq`, `mass`, `total`, and
`spot`. Names MUST match DESIGN_CLUSTER.md exactly.

### 3.0 Build order inside `features_for_group`

Call the segmentation AFTER the existing walls/COTM/regime block (so `gex_spot_abs` is available), then:
```
uniq, mass, smass, oistk = build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI)   # Sec 1.2
clusters, total_mass      = segment_clusters(uniq, mass)                                     # Sec 2
feat = cluster_features(clusters, uniq, mass, total_mass, spot)                              # Sec 3.1-3.4
# merge feat's keys into the Section 4.9 output dict.
```
If `total_mass <= 0.0` (empty window / all mass zero), see Section 3.3 (all-NaN / n_clusters=0). Note the
DTE-window empty case is already caught upstream by MATH_SPEC.md's `_nan_row` before this code runs; this
spec's zero handling is the defensive equivalent for the "non-empty chain but zero total gamma" edge.

### 3.1 Track B per-name features (the primary panel)

**`esc_above`** (binary f64 in {0.0, 1.0}) -- "spot has escaped ABOVE the dominant cluster":
```
esc_above = 1.0 if spot > dominant['hi_strike'] else 0.0
```
Strict `>`: spot exactly on the upper edge is NOT escaped (0.0). `[DECISION: measured against the
DOMINANT (max-mass) cluster's upper edge, per Section 2.5.]`

**`gap_room_up`** (f64; NaN if none) -- log-runway to the next cluster strictly above spot:
```
above = [c for c in clusters if c['lo_strike'] > spot]          # clusters entirely above spot
if above:
    nca = the cluster in `above` with the SMALLEST center       # nearest above
    gap_room_up = float(np.log(nca['center'] / spot))
else:
    gap_room_up = np.nan
```
`[DECISION: "next cluster above" = the nearest cluster whose LOWER EDGE is strictly above spot
(`lo_strike > spot`), i.e. a cluster price has not yet entered.]` This EXCLUDES any cluster that CONTAINS
spot (including the dominant one when spot sits inside it) -- so `gap_room_up` is always the runway to the
next distinct wall price would fast-track to, exactly the design's "next cluster through the gap"
semantics. Selection among the `above` set is by nearest CENTER (smallest center); ties (equal centers,
impossible for disjoint clusters) cannot occur. `np.log(nca['center']/spot) > 0` always (center > spot),
so a defined `gap_room_up` is always positive.

**`local_density`** (f64; NaN only if `total_mass <= 0`) -- gamma mass within +/-2% of spot:
```
band = (uniq >= spot*(1.0 - LOCAL_PCT)) & (uniq <= spot*(1.0 + LOCAL_PCT))   # inclusive both ends
local_density = float(mass[band].sum() / total_mass)
```
Fraction of TOTAL window gamma mass sitting on strikes within +/-2% of spot (the hedging friction right
at spot). Uses `mass` and `total` DIRECTLY (independent of the cluster list) -- so it is well-defined even
when no cluster survives the 5% floor, as long as `total_mass > 0`. Inclusive band edges (`>=`, `<=`).
Range [0, 1].

### 3.2 Track A SPY additions (market-level, computed for every name too -- harmless, cheap)

**`esc_below`** (binary f64 in {0.0, 1.0}) -- "spot has escaped BELOW the dominant cluster":
```
esc_below = 1.0 if spot < dominant['lo_strike'] else 0.0
```
Strict `<`; measured against the dominant cluster's LOWER EDGE.

**`gap_below`** (f64; NaN if none) -- log-runway DOWN to the next cluster strictly below spot (the
crash-runway):
```
below = [c for c in clusters if c['hi_strike'] < spot]          # clusters entirely below spot
if below:
    ncb = the cluster in `below` with the LARGEST center         # nearest below
    gap_below = float(np.log(spot / ncb['center']))
else:
    gap_below = np.nan
```
`[DECISION: symmetric mirror of gap_room_up -- "next cluster below" = nearest cluster whose UPPER EDGE is
strictly below spot (`hi_strike < spot`).]` `np.log(spot/ncb['center']) > 0` always (spot > center).

### 3.3 Diagnostics (always emitted)

```
n_clusters                  = int(len(clusters))
dominant_cluster_mass_share = float(dominant['mass_share'])   if n_clusters >= 1 else np.nan
cluster_span_pct            = float((dominant['hi_strike'] - dominant['lo_strike']) / spot)
                                                              if n_clusters >= 1 else np.nan
```
`cluster_span_pct` = dominant cluster width (upper edge minus lower edge) as a fraction of spot; 0.0 for a
single-strike dominant cluster (lo==hi). Additional OPTIONAL diagnostics the implementer MAY emit
(clearly not pre-registered features): `dom_center`, `dom_lo_strike`, `dom_hi_strike` (the dominant
cluster's center and edges, for audit/joins). Emit them as plain extra columns; they are not in Track A/B
feature lists.

### 3.4 Zero-cluster / degenerate outputs (EXACT)  `[DECISION]`

Applied in this order. "NaN" = numpy `nan`. Diagnostics `n_clusters` is ALWAYS an int (never NaN);
`local_density` is computed from `mass`/`total` whenever `total_mass > 0` even with zero clusters.

| Condition (on the per-strike profile of the feature-window chain)                     | n_clusters | esc_above | esc_below | gap_room_up | gap_below | local_density | dom_mass_share | cluster_span_pct |
|----------------------------------------------------------------------------------------|:----------:|:---------:|:---------:|:-----------:|:---------:|:-------------:|:--------------:|:----------------:|
| **Empty window / total_mass <= 0** (0 strikes, or all gamma 0)                          | 0          | NaN       | NaN       | NaN         | NaN       | NaN           | NaN            | NaN              |
| **No run clears 5% floor** (all runs < MIN_CLUSTER_FRAC)                                | 0          | NaN       | NaN       | NaN         | NaN       | computed*     | NaN            | NaN              |
| **Single cluster only, spot INSIDE it** (lo<=spot<=hi)                                  | 1          | 0.0       | 0.0       | NaN         | NaN       | computed      | computed(=1?)  | computed         |
| **Single cluster only, spot ABOVE it** (spot > hi)                                      | 1          | 1.0       | 0.0       | NaN         | computed  | computed      | computed       | computed         |
| **Single cluster only, spot BELOW it** (spot < lo)                                      | 1          | 0.0       | 1.0       | computed    | NaN       | computed      | computed       | computed         |
| **No cluster ABOVE spot** (>=1 cluster, none with lo_strike>spot)                       | >=1        | per rule  | per rule  | NaN         | computed/NaN | computed   | computed       | computed         |
| **No cluster BELOW spot** (>=1 cluster, none with hi_strike<spot)                       | >=1        | per rule  | per rule  | computed/NaN | NaN      | computed      | computed       | computed         |
| **< 3 strikes total** (1 or 2 distinct strikes)                                         | 0 or 1     | per rule  | per rule  | per rule    | per rule  | computed      | per rule       | per rule         |
| **All mass at ONE strike** (S==1)                                                       | 1          | per rule  | per rule  | NaN         | NaN       | 1.0           | 1.0            | 0.0              |
| **All-call OR all-put profile**                                                         | same as unsigned | -- | -- | --          | --        | --            | --             | --               |

\* "No run clears 5% floor": `n_clusters=0` so all cluster-derived features are NaN, but `local_density`
is still `mass[band].sum()/total` (computable, since total>0).

Notes on rows:
- **`< 3 strikes` is NOT a hard degeneracy.** It flows through the normal algorithm. With 2 strikes both
  active and within `SPACING_GAP`, they form one run; if that run clears 5% (it will, being ~all the
  mass) you get `n_clusters=1` and the single-cluster rows above apply. With 1 strike you get the
  "all mass at one strike" row. `[DECISION: never force NaN purely on strike count -- a thin chain gives
  a well-defined (if trivial) cluster; downstream N-floors filter sparsity, matching MATH_SPEC.md's
  Section 3.4 stance on thin chains.]`
- **All-call / all-put:** `mass` is UNSIGNED, so a chain that is entirely calls or entirely puts yields
  the SAME `mass` profile (hence identical cluster geometry) as the sign-flipped chain -- the dealer sign
  never enters segmentation. Segmentation proceeds normally; features are identical to the mixed-sign
  chain with the same per-strike magnitudes. (Verified: Section 4 test D7 == D1.)
- **Single cluster containing spot => `gap_room_up = gap_below = NaN`** because there is no cluster
  strictly above (`lo_strike>spot`) nor strictly below (`hi_strike<spot`) -- the only cluster contains
  spot. This is correct: no distinct wall to fast-track to on either side.

---

## 4. SELF-TESTS (locked reference values -- add to `dealer_gex.py --selftest`)

Add tests `C1`-`C4` to the existing `--selftest` runner, same PASS/FAIL/exit-nonzero convention as
MATH_SPEC.md Section 5. All use synthetic in-memory chains built with the existing `_make_synthetic_group`
helper (or direct numpy arrays). All reference values below were computed on numpy 1.26.4 and are
reproducible bit-for-bit. Tolerances: exact-equality for integer/binary/edge outputs; `< 1e-9` absolute
for masses/shares/centers; `< 1e-6` absolute for the log-ratio feature explicitly locked to 6 dp.

### 4.0 The locked 3-cluster synthetic chain

Fixed contracts (spot varies per scenario; the per-strike PROFILE is RE-EVALUATED at each scenario spot
because gamma depends on S -- do NOT reuse a profile across spots):
```
strikes      = [88, 90, 92,   98, 100, 102,   108, 110, 112]     # L cluster | M cluster | U cluster
option_type  = [put,put,put,  call,call,call, call,call,call]    # sign is irrelevant to mass
dte          = [30]*9
open_interest= [4000,6000,4000,  5000,9000,5000,  4000,6000,4000]
iv           = [0.40]*9
```
Gaps (no listed strikes) at 93..97 and 103..107. OI is chosen so all three clusters clear the 5% floor
at spot=100 and M dominates. dte=30, iv=0.40 throughout.

### 4.1 Test C1 -- segmentation of the 3-cluster chain at spot = 100.0

Build the per-strike profile at `spot = 100.0`. Locked per-strike UNSIGNED mass (dollar-gamma at spot):
```
K= 88 -> mass =  676735.5218514064
K= 90 -> mass = 1259978.2043156684
K= 92 -> mass =  999837.0626397815
K= 98 -> mass = 1680659.3115517092
K=100 -> mass = 3119407.3717724504
K=102 -> mass = 1732904.1507432866
K=108 -> mass = 1172614.8119615735
K=110 -> mass = 1581348.8110176916
K=112 -> mass =  926253.7176200682
total  = 13149738.963473637
```
Every node is ACTIVE (each frac >= 1%: min frac is K=88 at 0.0515). Expected segmentation -- EXACTLY THREE
clusters (index-gap splits at the 92->98 and 102->108 grid gaps; 92->98 spacing ln(98/92)=0.0632 > 0.05
and index-non-contiguous; 102->108 likewise):
```
cluster 0 (L): lo_strike=88.0  hi_strike=92.0   center= 90.22005513544661   mass=2936550.7888068566  mass_share=0.22331628003900217
cluster 1 (M): lo_strike=98.0  hi_strike=102.0  center=100.01599420555166   mass=6532970.834067447   mass_share=0.4968137278020686
cluster 2 (U): lo_strike=108.0 hi_strike=112.0  center=109.86611600808261   mass=3680217.3405993334  mass_share=0.27986999215892927
DOMINANT = cluster 1 (M): center=100.01599420555166, mass_share=0.4968137278020686
```
Assertions (all must hold):
- `n_clusters == 3` (exact).
- `clusters[0].center` within `1e-9` of `90.22005513544661`; `clusters[1].center` within `1e-9` of
  `100.01599420555166`; `clusters[2].center` within `1e-9` of `109.86611600808261`.
- `clusters[i].mass_share` within `1e-9` of `[0.22331628003900217, 0.4968137278020686,
  0.27986999215892927]` respectively.
- edges exact: `clusters == [(88.0,92.0),(98.0,102.0),(108.0,112.0)]` as `(lo_strike, hi_strike)`.
- dominant is `clusters[1]` (max mass); `dominant.center` within `1e-9` of `100.01599420555166`.

### 4.2 Test C2 -- esc_above / esc_below / gaps for THREE spot placements

Re-evaluate the profile at each spot; run full segmentation + features.

**C2a -- spot = 100.0 (INSIDE the dominant M cluster [98,102]):**
```
n_clusters                  = 3
esc_above                   = 0.0        # 100 not > 102
esc_below                   = 0.0        # 100 not < 98
gap_room_up                 = 0.0940923113042002    # ln(U.center 109.86611600808261 / 100)
gap_below                   = 0.10291844288580897   # ln(100 / L.center 90.22005513544661)
local_density               = 0.4968137278020686    # band [98,102] captures M cluster exactly
dominant_cluster_mass_share = 0.4968137278020686
cluster_span_pct            = 0.04        # (102-98)/100
```
Assert: `esc_above == 0.0` and `esc_below == 0.0` exactly; `gap_room_up` within `1e-9` of
`0.0940923113042002`; `gap_below` within `1e-9` of `0.10291844288580897`; `local_density` within `1e-9`
of `0.4968137278020686`; `cluster_span_pct` within `1e-12` of `0.04`.

**C2b -- spot = 105.0 (in the GAP between M and U, 103..107):**
At spot=105 all three clusters still clear 5% (shares 0.1457 / 0.4751 / 0.3792). Dominant is still M.
```
n_clusters                  = 3
esc_above                   = 1.0        # 105 > M.hi 102  (escaped ABOVE the dominant)
esc_below                   = 0.0        # 105 not < M.lo 98
gap_room_up                 = 0.04599851947101873   # ln(U.center 109.94265037707774 / 105)
gap_below                   = 0.04785620621607033   # ln(105 / M.center 100.09343942279011)
local_density               = 0.0        # no strikes in [102.9, 107.1]
dominant_cluster_mass_share = 0.4750896820307355
dom_center                  = 100.09343942279011
```
Assert: `esc_above == 1.0` exactly; `esc_below == 0.0` exactly; `gap_room_up` within `1e-9` of
`0.04599851947101873`; `gap_below` within `1e-9` of `0.04785620621607033`; `local_density == 0.0`
exactly; `n_clusters == 3`.

**C2c -- spot = 115.0 (ESCAPED ABOVE all clusters):**
At spot=115 the L cluster falls below 5% (share 0.033) and is DISCARDED -> only 2 clusters survive;
dominant FLIPS to U.
```
n_clusters                  = 2          # L cluster dropped by the 5% floor at this spot
esc_above                   = 1.0        # 115 > U.hi 112
esc_below                   = 0.0
gap_room_up                 = nan        # no cluster with lo_strike > 115
gap_below                   = 0.043674371933487874  # ln(115 / U.center 110.085546217214)
dominant_cluster_mass_share = 0.5702594619920868    # U now dominant
dom_center                  = 110.085546217214
```
Assert: `n_clusters == 2` exact; `esc_above == 1.0` exact; `gap_room_up` is NaN
(`not np.isfinite(gap_room_up)`); `gap_below` within `1e-9` of `0.043674371933487874`;
`dominant_cluster_mass_share` within `1e-9` of `0.5702594619920868`.

### 4.3 Test C3 -- the 6-decimal gap_room_up lock

From C2b (spot=105): the nearest cluster with `lo_strike > 105` is U (center 109.94265037707774).
```
gap_room_up = ln(109.94265037707774 / 105.0) = 0.045998519471019   (full: 0.04599851947101873)
```
Assert: `abs(gap_room_up - 0.045998) < 1e-6` AND `abs(gap_room_up - 0.04599851947101873) < 1e-12`.
(The 6-dp value is `0.045999` when rounded, `0.045998...` truncated; assert against the full double.)

### 4.4 Test C4 -- every degenerate case (EXACT outputs, no exception)

Each builds a fresh synthetic, runs the full pipeline, asserts no raise + the pinned outputs.

**D1 -- single cluster only** (strikes [98,100,102] all call, OI [5000,9000,5000], iv 0.40, dte 30,
spot 100). Single M-like cluster containing spot (identical strikes+OI to the C1 M cluster, so the
center matches C1's M center exactly):
```
n_clusters=1  esc_above=0.0  esc_below=0.0  gap_room_up=nan  gap_below=nan
local_density=1.0  dominant_cluster_mass_share=1.0  cluster_span_pct=0.04
dom_center=100.01599420555166  lo=98.0  hi=102.0
```
Assert: `n_clusters==1`; `esc_above==0.0` and `esc_below==0.0`; `gap_room_up` NaN; `gap_below` NaN;
`local_density` within `1e-12` of `1.0`; `dominant_cluster_mass_share` within `1e-12` of `1.0`;
`cluster_span_pct` within `1e-12` of `0.04`.

**D2 -- no cluster ABOVE spot** (strikes [78,80,82] all put, OI [1500,3000,1500], iv 0.40, dte 30,
spot 100 -> single cluster far below, spot above it):
```
n_clusters=1  esc_above=1.0  esc_below=0.0  gap_room_up=nan
gap_below=0.21777786355413326   # ln(100 / center 80.4304087075296)
local_density=0.0  dominant_cluster_mass_share=1.0
```
Assert: `n_clusters==1`; `esc_above==1.0`; `esc_below==0.0`; `gap_room_up` NaN; `gap_below` within
`1e-9` of `0.21777786355413326`; `local_density==0.0`.

**D3 -- no cluster BELOW spot** (strikes [118,120,122] all call, OI [1500,3000,1500], iv 0.40, dte 30,
spot 100 -> single cluster far above, spot below it):
```
n_clusters=1  esc_above=0.0  esc_below=1.0
gap_room_up=0.18051486263726815   # ln(center 119.78339243193123 / 100)
gap_below=nan  local_density=0.0
```
Assert: `esc_below==1.0`; `esc_above==0.0`; `gap_below` NaN; `gap_room_up` within `1e-9` of
`0.18051486263726815`.

**D4 -- fewer than 3 strikes** (strikes [99,101], call+put, OI [2000,2000], iv 0.40, dte 30, spot 100):
one 2-strike cluster containing spot.
```
n_clusters=1  esc_above=0.0  esc_below=0.0  gap_room_up=nan  gap_below=nan
local_density=1.0  dominant_cluster_mass_share=1.0  cluster_span_pct=0.02
dom_center=100.00753813123131  lo=99.0  hi=101.0
```
Assert: `n_clusters==1`; both esc flags 0.0; both gaps NaN; `cluster_span_pct` within `1e-12` of `0.02`.

**D5 -- all mass at ONE strike** (strikes [100], single call, OI [5000], iv 0.40, dte 30, spot 100):
```
n_clusters=1  esc_above=0.0  esc_below=0.0  gap_room_up=nan  gap_below=nan
local_density=1.0  dominant_cluster_mass_share=1.0  cluster_span_pct=0.0
dom_center=100.0  lo=100.0  hi=100.0
```
Assert: `n_clusters==1`; `cluster_span_pct == 0.0` exact; `local_density` within `1e-12` of `1.0`.

**D6 -- empty profile** (0 strikes; `uniq=array([])`, `mass=array([])`, spot 100):
```
n_clusters=0  esc_above=nan  esc_below=nan  gap_room_up=nan  gap_below=nan
local_density=nan  dominant_cluster_mass_share=nan  cluster_span_pct=nan
```
Assert: `n_clusters==0`; ALL of esc_above, esc_below, gap_room_up, gap_below, local_density,
dominant_cluster_mass_share, cluster_span_pct are NaN; no exception raised.

**D7 -- all-put profile equals all-call** (strikes [98,100,102] all PUT, OI [5000,9000,5000], iv 0.40,
dte 30, spot 100). Must produce IDENTICAL cluster geometry and features to D1 (which is all-call), because
`mass` is unsigned:
```
n_clusters=1  esc_above=0.0  esc_below=0.0  gap_room_up=nan  gap_below=nan
local_density=1.0  dominant_cluster_mass_share=1.0  cluster_span_pct=0.04
dom_center=100.01599420555166
```
Assert: every feature byte-identical to D1's (the per-strike `signed` array differs in sign, but `mass`,
clusters, and all Section 3 features are identical). Confirms sign-independence of segmentation.

---

## 5. PERFORMANCE

Let `S` = distinct strikes in the feature window for one (symbol, date) group (`S <= C`; empirically a
few tens to low hundreds). Per group:
- Per-strike aggregation (Section 1.2): three `np.add.at` over `C` contracts + one `np.unique` (sort) =
  `O(C log C)`, on arrays already materialized for the walls block. `np.unique(K)` is in fact already
  effectively computed by `_side_wall`; the combined-side aggregation is one extra pass over `C`.
- Segmentation (Section 2): one `np.where` (`O(S)`) + a single forward loop over active strikes
  (`O(S)`, pure Python scalar comparisons, no numpy call inside the loop except two array reads) + one
  list-comprehension filter (`O(S)`) + one `max` over clusters (`O(n_clusters) <= O(S)`). Total `O(S)`.
- Features (Section 3): a handful of list comprehensions over `clusters` (`O(n_clusters)`) and one boolean
  band mask over `uniq` (`O(S)`). Total `O(S)`.

Overall added cost per group is `O(C log C + S)` -- dominated by a sort of a few-thousand-element array,
i.e. sub-millisecond, and NEGLIGIBLE against the existing `(n_grid=301, C~3000)` Shape-B gamma block
(~97 ms/group, MATH_SPEC.md Section 2.4). The forward-scan loop is over DISTINCT STRIKES (tens), not
contracts, so it is not a hot path. For the full workload -- 26,582 Track B signals rerun +
~350 SPY dates -- the added time is well under the noise of the existing feature build (single-threaded
minutes budget unchanged). No new parquet scan, no extra gamma evaluation. If MATH_SPEC.md's optional
per-signal multiprocessing is used, this code parallelizes with it unchanged and must produce identical
numeric output regardless of worker count (it has no cross-group state).

---

## 6. IMPLEMENTATION CHECKLIST (for the Sonnet implementer -- zero math decisions)

1. Add the four constants (`GAP_MASS_FRAC`, `MIN_CLUSTER_FRAC`, `SPACING_GAP`, `LOCAL_PCT`) to the
   top-of-file block.
2. Add `build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI) -> (uniq, mass, smass, oistk)`
   (Section 1.2), reusing the `np.unique(return_inverse=True)` + `np.add.at` pattern from `_side_wall`.
3. Add `segment_clusters(uniq, mass) -> (clusters_list, total)` implementing Section 2.1-2.4 EXACTLY
   (active floor, index-OR-spacing split, mass-weighted center, edge = first/last active strike, 5%
   filter). Sort output ascending by `lo_idx` (it already is by construction).
4. Add `cluster_features(clusters, uniq, mass, total, spot) -> dict` implementing Section 3.1-3.4,
   including the exact zero/degenerate branches (Section 3.4 matrix). Dominant = max-mass (Section 2.5).
5. In `features_for_group`, after the walls/COTM/regime block, build the profile, segment, compute
   features, and merge these keys into the Section 4.9 output dict:
   `esc_above, gap_room_up, local_density, esc_below, gap_below, n_clusters,
    dominant_cluster_mass_share, cluster_span_pct` (+ optional `dom_center, dom_lo_strike, dom_hi_strike`).
   Cast `n_clusters` to Int64 alongside the other int casts in `compute_features` (module-level).
6. In the empty-window `_nan_row(...)` path, ADD the eight new keys as: `n_clusters=0`, everything else
   NaN -- matching Section 3.4 row 1 (keeps the output schema uniform across all rows).
7. Add self-tests C1-C4 (Section 4) to `run_selftest()`; they must PASS with the locked references.
8. Run `python experiments/gex/dealer_gex.py --selftest` -- all existing (A1-A5) AND new (C1-C4) tests
   must pass, exit 0.
