# short_premium — PREREGISTRATION (locked 2026-07-26, before any data pull)

**Question (user, /research 2026-07-26):** what does SELLING options look like on this system's
signals — naked short puts/calls and the poor-man's-covered-call (PMCC) — and what is the optimum
return-vs-risk frontier across strikes/DTE/exit policies?

**Study class:** exploration report (G35-class: deliver the insight + frontier + stage leads).
**NOT a ship candidate from this study alone** — the real-price window (2022-08..now) contains no
COVID-class crash, and short premium's defining risk is exactly that tail. Any "ship" claim is
structurally blocked the same way OSK was (G25b); the deliverable is the honest in-era frontier +
a model-based crash stress + a data-conditioned verdict.

**Why the axis is open (null-ledger triage, G17):** the anti-goal bans FUNDING (buying) puts;
G39's null was BUYING protective puts — its negative carry is the SELLER's positive carry. Prior
evidence tailwinds for a seller: iv_rv median 1.08 (options priced ~8% over realized vol = variance
risk premium on our universe); short-DTE puts print at 1.21x model (gamma/event premium); the real
call ledger shows funded 75+ per-trade EV slightly NEGATIVE on real contracts (−0.016) — the
counterparty of a marginal-EV buyer is a marginal-EV seller PLUS the spread. Headwinds: median
volume 0 on short-DTE puts (wide spreads — we RECEIVE the haircut now), assignment/margin path
risk, unbounded call tails, momentum universe = high realized vol.

---

## Hypotheses

- **H1 (bull put):** selling OTM/ATM puts at 75+ signals has EV-on-margin > 0 (pre-cutoff,
  date-clustered t ≥ 2) in at least one pre-registered grid cell, with a frontier point whose
  per-trade P05 and portfolio DD compare favorably to the long-call baseline.
- **H2 (bear call):** selling ATM/OTM calls at ≤30 signals has EV-on-margin > 0 (same bar).
  Prior: weak — put-BUYING on these signals was null, so the directional edge is unproven; any EV
  may be pure theta/VRP.
- **H3 (PMCC):** long deep-ITM LEAPS + short OTM ~30-DTE call at 75+ signals improves
  return-per-capital-at-risk vs (a) the long LEAPS alone and (b) the plain ~30-DTE long call.
- **H4 (control / attribution):** the neutral-signal (40-60) short-premium EV separates
  signal-driven edge from universal VRP: signal_lift = EV(75+ put cell) − EV(ctrl put cell),
  clustered t. If H1 holds but lift ≈ 0, the finding is "VRP harvest works", not "the score adds
  short-side alpha" — different claim, stated as such.

**Falsification:** H1/H2 fail if NO grid cell clears EV-on-margin > 0 at |t_clust| ≥ 2 pre-cutoff.
H3 fails if PMCC's return-per-capital is ≤ the better of its two baselines at matched risk. The
metric that must move for any future ship interest: portfolio-level return-vs-DD frontier point not
dominated by Core/Apex (Core anchor: 5y +1,248% / DD 61.7%). Evaluation: this run (in-era), plus
Dec-2026 OOS re-read for anything parked.

## Data & arms (single pull, Polygon Options Developer, real daily aggregates)

Signal sets (v74, 2022-08-01..today): BULL = all 3,904 overall≥75 rows. BEAR = stratified sample
of overall≤30 (cap ~85/month, seed 42, from 111k rows; monthly raw counts recorded for reweighting).
CTRL = 2,000 rows sampled 40≤overall≤60 date-matched to BULL's monthly distribution (seed 42).

| arm | signals | type | target moneyness (K / spot_unadj) | DTE bands |
|---|---|---|---|---|
| bull_put | BULL | put | 1.00, 0.95, 0.90 | d15 [7,18]→12 · d30 [21,45]→30 · d60 [46,75]→60 |
| bear_call | BEAR | call | 1.00, 1.05, 1.10 | d15, d30, d60 |
| pmcc_long | BULL | call | 0.75 (deep ITM) | leaps [180,420]→270 (walk capped signal+60cal) |
| pmcc_short | BULL | call | 1.05 | d30 |
| ctrl_put | CTRL | put | 1.00, 0.95 | d30 |
| ctrl_call | CTRL | call | 1.00 | d30 |

Conventions inherited verbatim from `experiments/polygon_real_premium/DESIGN.md`: as_of chain
lookup (alone, never with expired=true), strike = nearest to target×**spot_unadj** (G51; 2-strike
fallback), adjusted-contract/dedupe hygiene, entry = signal-date contract CLOSE print, offsets =
market trading days, miss taxonomy, resumable JSONL journal, no MySQL writes. NEW vs prior pull:
store **raw per-bar paths** (paths.parquet) so exit grids apply post-hoc, and build
`_unadj_daily.parquet` (full as-traded daily close series per involved symbol, yf auto_adjust=False
+ forward-split un-apply) for expiry settlement, margin marking and moneyness.

## Ledger: P&L, margin, exit policies (all applied to the same paths)

Short leg cashflows: receive `entry_premium_real × HAIRCUT` at entry; close leg at exit price.
**HAIRCUT primary = 0.90** (sellers cross the spread too; these names print wide markets),
sensitivities {1.00, 0.85, 0.75} always reported. PMCC long leg PAYS `entry × (2 − HAIRCUT)`.

Exit-policy grid (9 = TP × SL): TP ∈ {none, buyback at 50% capture (bar LOW ≤ 0.50×entry, resting
limit — free per canon), 75% capture (LOW ≤ 0.25×entry)} × SL ∈ {none, 2×, 3× premium (bar CLOSE ≥
mult×entry — EOD forced exit, pays half-spread per canon)}. No exit → settle at expiry INTRINSIC
from `_unadj_daily` (robust to illiquid no-print tails). Early assignment ignored (favors seller
slightly; caveat carried). PMCC: short cycle exits per grid; long leg marked at last print ≤ exit
date (staleness flagged); PMCC P&L = both legs over the short cycle, capital = long-leg debit.

Margin (locked, standard broker formula, per-share ×100):
`put: max(0.20·S₀ − max(S₀−K,0), 0.10·K) + prem` · `call: max(0.20·S₀ − max(K−S₀,0), 0.10·S₀) + prem`.
Puts ALSO reported on cash-secured basis (K). Daily margin marked along the path with S_t
(peak-margin per trade recorded — portfolio phase uses it).

**Metrics per cell** (arm × moneyness × band × policy × haircut): N, coverage (kept/attempted,
liquid share, stale_frac), win rate, mean/median P&L on margin + on cash-secured + on premium,
P05/P01/worst per-trade, mean days held, EV/day, annualized return-on-margin proxy, date-clustered
t (CR1), per-year table. **Holdout discipline (G15):** cell selection/ranking on entries ≤
CALIBRATION_CUTOFF_DATE (2026-06-15); post-cutoff reported separately (thin, ~1.4mo).
**Multiplicity:** the grid is ~54 base cells/arm; a "finding" needs |t|≥2 pre-cutoff AND per-year
sign stability (≥4/5 years) AND survives the 0.85 haircut. Report expected-by-chance count.

## Portfolio phase (best cells only)

Sequential-capital sim on real trade P&L: position sizing (margin budget % of equity ∈
{10,20,33,50}), concurrency cap, mark-to-market from real paths, forced-liquidation flag when
margin > equity. Deliverable: return-vs-DD/collapse frontier vs Core/Apex anchors. Then the
**model-based crash stress**: replay best short-put cells through 2020-02..2020-04 (and 2008 if
deep history suffices) using underlying paths from price_history + entry premium from the validated
model (real/model 1.022) with an IV-expansion mark penalty scenario grid (marks at intrinsic +
{1×, 2×, 3×} modeled extrinsic). State plainly: this region is MODELED, not measured.

## Traps forwarded (builders receive this list)

G51 spot trap (use spot_unadj for strikes/moneyness/settlement; entry_price stays adjusted for
vol-model fidelity) · G5 PYTHONIOENCODING on every python invocation · G7/G47 polars
infer_schema_length=None + fill_nan(None) choke point + finite-X masks before any regression ·
G54 queued drivers in Python, check stderr.log · G49 CR1 date-clustered z; rank by gated legs never
raw t; bucket-share [5,95]% guard · G52 event windows from entry+NOMINAL hold · DB reads
single-threaded before any thread pool (PyMySQL) · as_of alone in chain calls · queue everything
heavy (`trader queue submit`), pulls are --db light --cpu 2-4 io-bound.
