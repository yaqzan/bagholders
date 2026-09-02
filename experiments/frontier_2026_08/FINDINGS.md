# FINDINGS — The selectivity frontier under honest fills

**STATUS: COMPLETE 2026-08-12. HEADLINE: the frontier question dissolved under measurement.
The edge is not a score threshold — it is CTSL (Counter-Trend Score Lift, shipped
2026-05-08), and its purest tested expression (ultra-only funding, ~90% CTSL by trade
composition) is the FIRST configuration to beat the SPY yardstick under calibrated fills,
across every gate lens run.** Prereg `a095d2ae` + AMENDMENT-1 `f02a6005` (mechanism
corrected to tier-native funding after the ctx-removal filter failed its identity anchor
by design). Queue #499-512. Evidence: `out/frontier_*.csv`.

## The attribution (the campaign's decisive table; additive, sum-checked exact)

| arm (22-now / 5y median, calibrated) | 22-now | 5y |
|---|---|---|
| Sentinel as shipped (full ctx + CTSL) — bit-exact R1 anchor | +37.4 | +45.2 |
| Sentinel with CTSL OFF (`CT_PROMOTE=0`) | **−20.0** | **−15.8** |
| Pure ≥85 picks, no density ctx, no CTSL (retired F1, labeled) | −13.4 | −5.1 |
| **CTSL contribution** | **+57.4pp** | **+60.9pp** |
| Density/pressure contribution | −6.6pp | −10.7pp |

**Without CTSL, every high-score book tested is NEGATIVE under honest fills** — fund-all,
zero-low, CTSL-off Sentinel, pure-85+ picks. The 85-94 band is negative-contribution
(adding it to the ultra book cuts returns ~4x). The "extreme selectivity works" story from
the R1 discovery was a costume: CTSL promotions (~23% of Sentinel's distinct trades, ~30%
row-weighted) were the payload. F0 rerun: CTSL trades are deep counter-trend (mean trend
11.2), mean raw score 62.9 (as re-tiered), and MORE liquid than any score band (54.2%
high-tier) — the fill-realism haircuts touch them least.

## Ultra-only — the first index-competitive config under honest fills (ALL gates green)

Tier funding = ultra slot only (min-score 95 + CTSL promotions; measured composition
~90% CTSL rows). N=500 paired, full ctx, collapse 0 on every cell of every lens:

| lens | 22-now med / DD | 5y med / DD | SPY yardstick |
|---|---|---|---|
| calibrated (MISS_P .15) | +159.9 / 25.1 | +204.2 / 24.2 | +58.5 / +103.9 |
| survivor (delisted-excluded) | **+120.8 / 23.8** | **+144.9 / 25.3** | beats both ✓ |
| buffer (MISS_P .20) | +153.3 / 25.1 | +189.7 / 25.7 | beats both ✓ |

**INDEX-COMPETITIVE per the locked §0 rule (survivor medians > SPY on both decision
windows) — the only config in the book to earn the label.** Sentinel (zero-low+mid)
passes all guards but is NOT index-competitive (survivor +24.5/+17.7 vs SPY +58.5/+103.9).
Character: ~30-40 trades/yr (median 136-159/path over the windows), DD ~24-26 across all
lenses, breathes with volatility (2024 grind-up: 15 trades, +1.4%; 2023 chop: +66%).

## Winner's-curse discipline (why this is a CANDIDATE, not a conclusion)

Dozens of configs were measured this week; ultra-only is an extreme cell. Its protections:
paired seeds at N=500; survivor + buffer arms green; collapse 0; DD stable ~25 under every
lens; and — unlike a mined cell — a NAMED causal mechanism with measured composition (90%
CTSL) and a liquidity profile that explains its fill robustness. Its exposures: the
calibrated fill model is 2022-26-era; CTSL itself shipped 2026-05-08, so windows before
May 2026 are in-sample for its design; D1 double-touch optimism applies to barrier
metrics; and the 2026-12-15 OOS window (Jun→Dec 2026) is the first genuinely virgin data
for the mechanism. **December now has a sharper job: re-grade CTSL specifically, not just
score bands.** No ship, no plan change, no capital implication from this campaign.

## The rest of the answer sheet (owner's five questions)

- **Q1 (index bar):** structural now — SPY/HISA columns on every table; Sentinel fails it,
  ultra-only passes it (pending December).
- **Q2 (30y liquidity proxy):** volume-rank proxy DEAD (ρ=0.154 vs 0.6 bar; range-
  restriction caveat recorded). Revived in rate-form per owner: the era-honesty cube
  (blanket drop% × MISS_P bands × PIT-mcap existence floor) is the enumerated follow-up,
  to run on the surviving configs only.
- **Q3 (gradient + ablation):** no smooth gradient — a cliff. Ablation complete: the
  ingredient is CTSL, not the threshold. "Dilution" downward is measured lethal.
- **Q4 (sparseness levers):** F3 raw table banked. Sentinel-shape: dte45/60 and both gross
  moves read raw-REAL (labels NOT stuck — survivor checks not run; deprioritized because
  lever optimization belongs to the pure-CTSL vehicle, not to shapes whose edge was just
  re-attributed). Ultra-only: gross 0.45 raw-REAL (+24/+21pp, DD flat) — same pending
  status. Heavier sizing beyond 0.45 stays banned.
- **Q5 (puts):** OBLITERATED — both put-tier constructions: medians ≈ −81%, collapse
  87-100% everywhere, incl. 100% on both decision windows. Hedge-not-edge stands, now
  under honest fills. Axis closed. (Two driver bugs found+fixed en route are documented
  in the run report: PUT_TIER_* env naming; MAX_POSITIONS_PUT=0 hard gate.)

## Disposition

1. **Next campaign (own prereg): the pure-CTSL vehicle** — CTSL-only funding expression,
   lever sweep (gross/DTE) with survivor checks, capacity/clip modeling at owner scale,
   era-honesty cube (Q2 rate-form) on it, and an explicit CTSL mechanism audit (its
   knobs, caps, and what December must show). Owner decision point before any ship talk.
2. F3 lever survivor checks fold into that campaign (not run here; labels pending).
3. Docs/plan updated: capital-plan + known-issues name CTSL as the identified carrier;
   the live ledger already runs CTSL (shipped since May) — the forward test is already
   accumulating the right evidence.
4. Identity-anchor bit-exactness (10/10 fields), fingerprints flat across every battery,
   0 tainted cells session-wide.
