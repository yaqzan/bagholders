# V0 — CTSL mechanism dossier (recon, no compute)

PREREG lock `3e2adc9f`. Source-traced against `monte_carlo.py`, `strategy_config.py`,
`mechanism_registry.py`, `backtest_cascade.py` at HEAD (v74 `f9fb7b934` active).
Composition/economics measured from the **existing** `frontier_2026_08` tapes,
read-only — no simulation run for this stage.

---

## V0.1 THE NAME IS WRONG. The vehicle's carrier is CT_PROMOTE, not CTSL.

Two distinct counter-trend mechanisms ship together. `frontier_2026_08` ablated ONE and
labelled the result "CTSL".

| | **CT_PROMOTE** (cascade-stage) | **CTSL** (score-stage lift) |
|---|---|---|
| what it does | tags a call `ct_call`, forces `tier='ultra'`, fills it AHEAD of the score-sorted queue | rewrites `signal.overall` upward toward a target |
| gate | `overall >= 70` AND `trend <= CT_CALL_TREND_MAX=20` | `trend <= CTSL_CALL_TREND_MAX=15` |
| code | `ct_tag()` `monte_carlo.py:1546`; tier override `monte_carlo.py:3453` | `_ctsl_call_lift()` `:1637`, applied at load `:2237` |
| env | `CT_PROMOTE` (default 1) | `CTSL_ENABLED` (default 1, 30-DTE only; **15-DTE = False**) |
| frontier's ablation arm | **`CT_PROMOTE=0`** — this is what F1″ turned off | **stayed ON in both arms** |

**Therefore the measured +57.4pp / +60.9pp is 100% CT_PROMOTE's contribution, measured
with CTSL held ON.** frontier's FINDINGS.md calls this "CTSL contribution" throughout;
that is a misnomer this campaign carries forward corrected.

**Proof that CTSL cannot be the carrier under ultra-only funding** (source, not simulation):

1. `load_signals()` (`:2214`) floors the call population at `overall >= OVERFLOW_THRESHOLD = 70`.
2. `_ctsl_call_lift` is monotone non-decreasing for `overall <= CTSL_CALL_TARGET = 98.4`, and
   applies a FLOOR at `CTSL_CALL_TIER_FLOOR = 74.7`. It can never lower a score below 70.
3. `ct_tag` therefore fires on **exactly** `{loaded calls with trend <= 20}` — the promoted
   SET is invariant to whether CTSL ran at all.
4. Every CTSL-eligible signal (`trend <= 15`) is a strict subset of the CT_PROMOTE-eligible
   set (`trend <= 20`), so CTSL **cannot add a single name** to the funded ultra slot. Its
   only residual channels are intra-ultra queue ORDER (`monte_carlo.py:3587`, sorts `-score`
   inside the ct_call block) and the 75-79 `_spread_tilt_scale` band (`:993`), which a lift
   generally moves a signal OUT of.

Corroboration from the repo's own comment (`strategy_config.py:1477-1479`), written when the
CTSL-as-substitute path was rejected: *"CT_PROMOTE earns its keep via accidental ULTRA-slot
capping that CTSL alone cannot replicate."* The frontier attribution independently
re-measured exactly that sentence at portfolio scale.

## V0.2 Knobs, caps, and what the "cap" actually is

`CT_CALL_TREND_MAX=20`, `CT_CALL_TIER='ultra'`, `CT_PUT_TREND_MIN=80`, `CT_PUT_TIER='put_top'`.
CTSL call side: `TREND_MAX=15`, `TARGET=98.4`, `ALPHA=0.56`, `TREND_POWER=2.82`,
`TIER_FLOOR=74.7`, `SCORE_NORM_WEIGHT=0.75`, `SCORE_NORM_POWER=2.27`.

There is **no per-day cap on the number of promotions**. The only binding caps are the
generic portfolio ones: `MAX_POSITIONS=14`, `GROSS_PREMIUM_CAP=0.30`, and the per-fill
allocation `TIER_ALLOC['ultra']=0.20` of the allocation base. "ULTRA-slot capped" in prior
notes means *promoted trades are sized at the ultra rate*, not that their count is limited.

**Neither mechanism is baked into `Score.overall`.** Both are portfolio-stage overlays
(`monte_carlo.py` + `backtest_cascade.py`); `database/utils/scoring.py` contains no CTSL and
no CT tagging. Consequence: the whole vehicle can be swept and re-shaped with **no
ALGORITHM_VERSION bump** — it is Stage-3 territory throughout.

## V0.3 Calibration provenance — both carriers are calibrated on stale, in-sample substrate

- **CT_PROMOTE**: `experiments/x_conf_counter_trend.py`, a **v22-era** "Path B follow-up to
  the v22 revert"; the cited evidence is a CT-PUT bucket WR15=81.5% on n=232 over **2y**.
  That is a put-side statistic used to justify a mechanism whose call side now carries the
  entire book.
- **CTSL**: calibrated on **v44/v39** substrate (`experiments/ctsl/FINDINGS.md`), shipped
  2026-05-08. v44 and v39 are **pre-v69**, i.e. inside the weekly-look-ahead-inflated era
  (traps.md "Weekly features in a RECALC are look-ahead"). It was designed as a *replacement*
  for CT_PROMOTE under a "CT_PROMOTE legacy tech debt removal" plan; the substitution
  (C config) was REJECTED at Stage-3 T4, and CTSL was kept stacked instead.
- Holdout lock is `2026-06-15`. Every window this campaign runs is in-sample for both
  mechanisms' design. The Dec-15 OOS window is the first virgin data — V6's job.

## V0.4 NEW FINDING — ~1 trade in 5 is a 15-DTE router trade wearing an ultra-sized coat

While measuring composition I found `score == 0` on 21.2% (22-now) / 18.1% (5y) of the
ultra-only vehicle's tape rows, on signals whose DB scores are 70-85. Traced to source:

`_dte_router_call_eligible()` (`monte_carlo.py:1564`) routes up to `DTE_ROUTER_DAY_CAP=1`
signal per day (score >= 80, trend < 50) onto a **15-DTE** outcome, and then
`monte_carlo.py:4424` deliberately zeroes its allocation score
(`alloc_score = min(score, DTE_ROUTER_ALLOC_SCORE_CAP=0)`) so that `score_to_tier(0)`
= `'overflow'` and the routed trade is sized DOWN.

But `_try_fill_call` (`:3453`) checks `if ct == 'ct_call': tier = CT_CALL_TIER` **before**
consulting the score. Every router-routed signal has trend < 50 and score >= 80, so any with
trend <= 20 is also CT-tagged — and the CT branch **overrides the router's size cap**,
funding it at the full ultra 0.20 instead of the intended overflow rate.

Measured economics of the ultra-only vehicle (frontier `f1p` tapes, N=500 paths):

| group | rows 22-now | premium share | **PnL share** | mean ret/trade | TP rate |
|---|---|---|---|---|---|
| CT promotions, 30-DTE | 68.5% | 69.1% | 73.2% | +0.109 | 88.2% |
| **CT promotions, ROUTED to 15-DTE** | **21.2%** | **22.1%** | **21.7%** | **+0.162** | 62.7% |
| raw 95+ picks, 30-DTE | 10.3% | 8.8% | 5.2% | +0.038 | 100.0% |

(5y: 71.2 / 18.1 / 10.7% of rows; PnL 66.8 / 19.7 / 13.5%.)

Three consequences this campaign must carry:

1. **The "30-DTE vehicle" is really ~80/20 30-DTE/15-DTE.** Any DTE lever in V3 moves only
   the 80%; the routed sleeve stays at 15 regardless of `NOMINAL_CAL_DTE`.
2. **V2's fill-honesty reweighting inherits an untransferred assumption.** `TP_FILL_MISS_P`
   was measured at 30-DTE (`project_tp_fill_fidelity_30dte`). A fifth of this book's premium
   is 15-DTE, where the tier-monotone never-fill rates were never measured.
3. This is a **mechanism-ordering interaction, not a scoring result** — flagged, not
   "fixed": touching it would change the object frontier measured, and no ship is in scope.

## V0.5 Deliverable asked for by the PREREG: can "promotions-only" be expressed via config?

**No.** Enumerated and rejected:

- *Fund ultra only* — the ultra tier is also reachable by raw `score >= 95`; measured
  contamination 10.3% / 10.7% of rows (8.8% / 9.1% of premium). **This is the anchor**, at
  89.7% / 89.3% CT purity by rows.
- *Point `CT_CALL_TIER` at a tier nobody else reaches* — impossible: `TIER_ALLOC` has exactly
  the five keys `score_to_tier` emits. Routing CT to `'overflow'` and funding only overflow
  admits the entire 70-74 band (far larger than the 95+ band) — strictly worse purity.
- *ctx removal of non-CT keys* — this is the mechanism `frontier`'s AMENDMENT-1 retired for
  contaminating `call_pressure` density. Not reintroduced.

Per the PREREG's own fallback clause, **ultra-only stands as the vehicle**, and the
promotions-only arm is DROPPED from V1 (no substitute arm invented).

## V0.6 Reusable levers confirmed present (for V1-V5)

`CT_PROMOTE`, `CTSL_ENABLED`, `CT_CALL_TREND_MAX`, `CT_CALL_TIER`, `TIER_*_OV`,
`NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS`, `GROSS_PREMIUM_CAP`/`CALL_PREMIUM_CAP`,
`TP_FILL_MISS_P`/`TP_FILL_GAP_AWARE`, `DTE_ROUTER_*` — all env-readable at
`monte_carlo` import time. No production edit is needed anywhere in this campaign.
