# Trend/Oscillator Divergence Dampener — W1 PRE-FLIGHT NULL (2026-06-03)

**Status: NULL at W1 + rescue probe also NULL → thread FULLY CLOSED. No sweep, no scoring
edit, no version bump.** The hypothesis (seeded by the NVDA single-stock analysis in
`experiments/nvda_analysis/`) does not survive the universe-scale, holdout-locked,
**option-barrier** W1 gate, and a 10-feature "reverts-vs-continues" rescue probe found no
separator. Documented so it is not re-tested without a genuinely new mechanism family.

## Hypothesis
On strong trenders, `trend` saturates (~95-100) and masks oscillator reversals, so:
- **Dampener (top-fade):** calls with `trend_comp≥90 ∧ stoch_comp≤15` (overbought-masked-by-trend)
  should *underperform* → dampen them.
- **Lift (dip-buy):** bars with `trend_comp≤45 ∧ stoch_comp≥80` (trend-break + oversold reversion)
  should *outperform if entered as calls* → lift them toward 75+. (NVDA showed 72% up @15d.)

## Method
- Reused the holdout-locked v70 universe ledger (`experiments/component_reweight/`, ≤2026-05-15)
  with the **barrier-agnostic** forward-path capture → win/loss derivable for any (TP,SL,W) via
  `label.derive`. Evaluated on 4 barriers: **opt15** (growth-gate / option-aligned, PRIMARY),
  **apex15** (funded HOLD TP30/SL70), hold15 (TP-reach proxy), gen15 (dashboard generic, the trap).
- Dampener tested on the existing 60-99 call ledger (N=394k). Lift tested on a purpose-built
  targeted ledger `trend≤45 ∧ stoch≥80 ∧ overall≥30` (N=310k), compared vs the 75+ call base.
- Two-proportion z. W1 needs **|z|≥3 in the proposed direction on the OPTION barrier** (not gen15).

## Result — both halves fail

### Dampener (`w1_results.txt`)
| cohort (within 75+) | N | opt15 z | apex15 z | gen15 z |
|---|---:|---:|---:|---:|
| trend≥90 ∧ stoch≤15 | 915 | **+0.16** | −1.26 | −2.32 |
| trend≥95 ∧ stoch≤10 | 519 | **+1.80 (BETTER)** | +0.27 | −1.00 |
| trend≥90 ∧ stoch≤20 ∧ rsi≤45 | 128 | −2.84 | −2.08 | −2.44 |

- No effect (or *positive*) on the option barrier. Tighter saturation → cohort wins MORE (momentum).
- Only z≤−3 anywhere = **gen15** (wide dashboard barrier) or tiny-N hold15 → the **SVD/v42 generic-vs-option trap**.
- Gradient of opt15 WR vs stoch (within trend≥90) is **non-monotone**; deepest-overbought bucket (stoch 0-10, N=811) = 48.8%, *above* the 47.5% base. Confirms NVDA "shorting tops is a trap" universe-wide: overbought-in-uptrend calls continue.

### Lift / dip-buy (`lift_w1_results.txt`)
| cohort (N=310173) | opt15 | apex15 | hold15 |
|---|---:|---:|---:|
| dipWR | 46.0% | 68.4% | 77.7% |
| 75+ base | 47.5% | 70.0% | 78.9% |
| Δ / z | −1.5pp / **−2.08** | −1.6pp / **−2.29** | −1.2pp / −2.04 |

- The capitulation cohort is **BELOW the existing 75+ calls on every barrier** → promoting it would
  *dilute* the call pool (the v42 volume-dump trap), not add alpha.
- Gradient flat (stoch 95-100 = 46.4% vs 80-84 = 45.9% opt15; deeper trend-break slightly *worse*).
- Tighteners go more negative (stoch≥90 ∧ macd≤35 → opt15 z=−4.08; stoch≥90 ∧ bb≤40 → apex15 z=−3.76).
- **The NVDA 72% was a generic-barrier (price-up @15d), single-stock, small-N artifact.** On the
  real option exit (tight SL) the volatile capitulation bounce trips SL before recovery → no edge.

## The one signal that *didn't* die (sub-threshold, not ship-worthy)
`CONFIRM: trend≥90 ∧ stoch≥40` (trend-confirmed, oscillator NOT exhausted) → opt15 **+3.6pp z=+2.32**
on 75+. Real but (a) below the +3 gate, (b) tautological selection ("the good calls are good"; they
already qualify), (c) no clean Stage-1 mechanism. Possible *future* lead: a Stage-3 cascade-priority
tilt (prefer non-exhausted-stoch within a tier), NOT a Stage-1 score change. Not pursued.

## Conclusion / what NOT to retry
1. The trend/oscillator-divergence cohort (either direction) does **not** beat the option barrier
   universe-wide. The component scores faithfully measure the technicals; the engine is correctly
   calibrated for portfolio WR15/DD. The NVDA mis-fit is a known, accepted trade-off (saturated trend
   on a secular mega-cap), not a shippable defect.
2. **Do not re-test divergence dampening/lifting without a NEW discriminator** that separates
   "overbought-that-reverts" from "overbought-that-continues" — raw stoch/rsi/trend do not.
3. Methodology win (re-confirmed): **gate Stage-1 on the OPTION barrier (opt15/apex15), never gen15.**
   Both halves had gen15/generic signals that vanished on option exits — the SVD/v42 trap.

## (b) Rescue probe — "reverts vs continues" discriminator (2026-06-03) — ALSO NULL
Tested the overbought-in-uptrend CALL cohort (`trend≥85 ∧ stoch≤25 ∧ overall≥70`, N=12,268,
holdout-locked) for ANY orthogonal feature that splits option-loss (reverts) from option-win
(continues). Cohort base: opt15 46.6% / apex15 67.8% (≈ the 75+ base; the cohort itself is not
materially worse — re-confirming the dampener null). Per-feature quintile gradients on opt15/apex15:

| feature | result |
|---|---|
| **overextension** `pct_from_ema50` (recomputed via JOIN, 100% cov) | FLAT — quintiles 45.1→48.0→46.3%, top-vs-bottom z=+0.86 (stretched mildly *better*); blow-off tail pe50≥19% z=−0.09 |
| overextension `pct_from_ema200` | flat, z=+0.70 |
| regime_composite (44% cov) | z=−2.95 (sub-threshold; high-bull → mild revert — directionally sensible but doesn't clear +3) |
| vol_mag | z=−2.28 |
| w_adj (weekly confirm), c_macd, c_bb | flat |
| vol_signal | CLIMAX +2.63 (N=58 noise), THIN_AIR +2.02; nothing ≥3 |

**Nothing splits the cohort at \|z\|≥3 on the option barrier.** The classic "overextended-overbought
reverts" intuition is false universe-wide — momentum dominates the option-TP outcome regardless of
stretch/regime/volume. The cohort is genuinely unseparable by these features.

## (c) Per-stock variants (2026-06-03) — overfit trap + per-type null
**Per-ticker (NVDA, train 2024-25 / test 2026)** — `experiments/nvda_analysis/perstock_test.py`:
dip-buy rule on NVDA: 2025 price-up 63% (N=19) → 2026 **100% (N=6), +17.1% mean** — looks like genius,
but option-call-TP = **47% (2025) / 50% (2026)** = coin flip both years. The "100%/+17%" is N=6 path-luck
on NVDA's Mar-2026 bottom; the tradeable barrier is base-rate. Fade rule is WRONG every year (price-down
21%/27%/11%). → per-ticker tuning overfits the generic metric to one path; option reality is base-rate.

**Per-stock-TYPE (vol-conditioned, the legitimate form)** — `het_test.py`: dip cohort option-WR vs 75+ base,
by realized-vol quintile, gets MONOTONICALLY WORSE with vol — at NVDA's own vol (~3.8%/d, 214 NVDA rows
in-cohort) it's **opt15 z=−8.4 / apex15 z=−7.3 below base**; only the LOWEST-vol (stable) bucket is ≈base.
So a generic vol-conditioned knob would have to *dampen* NVDA-like names, the opposite of the hypothesis.
Fade cohort is flat across vol (z −2.4..+0.3). → per-type also null, and inverts for high-vol names.

Lesson: stock heterogeneity is real, but the only form that survives is conditioning on a GENERIC,
universe-validated attribute (already shipped: MCD=log-mcap, ICH=Ichimoku state, σ-normalized barriers).
Per-ticker knobs can't clear the gate (tiny per-stock N, no holdout/validation surface) and overfit path.

## Artifacts
- `w1_preflight.py` + `w1_results.txt` (dampener), `lift_w1.py` + `lift_w1_results.txt` (lift)
- `build_ledger_wide.py` (targeted dip ledger builder; path-bug fixed post-run)
- dip ledger: `.cache/divergence_dampener/ledger_v70_wide.parquet`
- seed analysis: `experiments/nvda_analysis/out/FINAL_REPORT.md`

### Cache note
The dip-ledger build initially clobbered `.cache/component_reweight/ledger_v70_5y.parquet`
(a `sed` 2-line path-match miss). The dip data was relocated to `.cache/divergence_dampener/`
and the 60-99 base ledger was rebuilt via `trader queue` (restore task). Builder path bug fixed.

## (d) Run-up VELOCITY seam — ALSO NULL (2026-06-08, the NET re-open)
Seeded by NET (Cloudflare) Mar–Jun 2026: 3 call episodes firing on +9/+12% vertical spike
days at the local top, then −9/−23% reverts. The 2026-06-03 rescue probe used the *level*
`pct_from_ema50` (flat); this tests the one axis it missed — the **σ-normalized run-up VELOCITY
into the signal** `v_N = (close/close[-N] - 1)/(vol_frac·√N)` (the call-side mirror of v37 PCD's
ret_10d_sigma), N∈{1,3,5,10}, joined to the same holdout-locked v70 ledger (N=394k, opt15 barrier).
Harness: `velocity_w1.py` (queue task #87).

**Verdict: NULL — nothing clears opt15 z≤−3; the near-miss is a tight-stop artifact; and within
NET's own cohort velocity INVERTS to positive on the funded barrier.**

- 75+ universe opt15 WR by v5 quintile is **FLAT/non-monotone**: Q0 47.3% → Q2 48.9% → Q4 45.9%.
  Same for v3 (46.8→46.2) and v10. v1 (signal-day move) is a symmetric inverted-U — Q0 (big DOWN
  day) 43.8% AND Q4 (big UP day) 43.3% both below the ~50% middle ⇒ "extreme-day entry is noisier,"
  NOT "up-spike reverts."
- Best near-miss: `v1≥2.0σ` (huge single-day spike, N=629) opt15 d=−6.4pp **z=−2.99** — but it
  fails: (a) below the −3 gate, (b) **gen15 z=+0.26 / apex15 z=−0.88** ⇒ the SVD/v42 tight-stop
  trap (loses only on opt15's 0.772σ stop, neutral/positive on the wider funded/generic stops —
  a stop-fragility artifact, not directional reversion), (c) symmetric (down-spikes equally bad).
- **Within NET's exact cohort `trend≥90 ∧ stoch≤15` (N=915): velocity is flat-to-POSITIVE.**
  v5≥1.5 → opt15 z=+0.07 / **apex15 z=+1.84**; v3≥1.5 → opt15 +1.08 / **apex15 z=+2.65**;
  v5≥2.0 → apex15 z=+1.56. On the **funded apex15 (TP30/SL70 HOLD)** barrier the live strategy
  actually trades, high-velocity overbought-uptrend calls win MORE — the opposite of the fade.
- NET's own pre-cutoff signals: 4W/2L on opt15; the two high-velocity losses (5/05 v3=+2.57,
  4/07 v5=+1.24) are matched by a high-velocity WIN (5/06 v3=+1.91). Velocity doesn't separate
  even within NET.

⇒ The run-up-velocity discriminator joins extension/regime/vol/macd/bb/vol_signal as NULL on the
option barrier. **The divergence thread (all axes) is closed.** NET is unseparable single-name
variance; on the funded HOLD barrier the cohort is a momentum WINNER. Do not re-test velocity.

## (e) Left-tail / divergence / earnings separator mine — NULL, now STRUCTURALLY explained (2026-06-08)
Reframe: "tops then crash" is a LEFT-TAIL/DD problem, not mean-WR. Tested the exhaustion/spike/
divergence/earnings cohorts on the FUNDED apex barrier's tail (apex SL-rate, MAE>=2.548σ crash-zone)
+ per-year EV. Harness: `separator_w1.py` (task #90). 75+ base: opt15 47.5% / apex15 70.0% /
apexSL 23.2% / crash 35.7%.

**Three structural findings (why no separator can exist here):**

1. **The exhaustion/spike cohort is NOT DD-concentrated — it crashes LESS, not more.**
   EXH `trend≥90 ∧ stoch≤15` (N=915): apexSL z=−0.3, crash 34.4% (< 35.7% base). EXH & v5≥1.5 (the
   exact NET signature, N=470): apex15 z=**+0.5**, apexSL z=−0.7, crash **34.9%** (< base). On the
   funded barrier these are *better* with a *smaller* tail. **NET is salience bias** — a vivid single
   name, not a worse cohort. Only `v1≥2.0` (single-day-extreme) has a mildly fatter tail (apexSL
   z=+2.2, below +3) and it's the symmetric tight-stop artifact from (d).

2. **The divergence indicators are EMPTY SETS.** `v5≥1 ∧ drsi5≤0` → N=3. `v5≥1 ∧ c_macd≤40` → N=0.
   `v5≥1 ∧ c_rsi≤40` → N=156 (flat, opt15 z=−1.5). "Price spiked but momentum secretly weak" does
   **not occur at the signal bar** — the components faithfully co-move with the spike. There is no
   hidden-weakness flag to find because **the score isn't lying about momentum.** (The
   divergence-push logic in `calculate_rsi_score` already absorbs genuine RSI divergence.)

3. **Per-year = textbook crash-artifact trap.** Every "bad" cohort is bad in exactly ONE year —
   **2023** (EXH 42% vs 63% base; EXH&v5≥1.5 48%) — a bull-recovery tape, and FINE-to-GREAT in the
   real crashes (2020 69-72%, 2022 72-73% > base) and 2024 (81-83%). A single-window −21pp on a 10y
   cohort, bull-concentrated ⇒ contracting it sacrifices bull return for a phantom. Not shippable.

**The one real (sub-gate) signal: EARNINGS-in-hold-window** (N=1165, 24.8% of 75+). Genuinely fatter
tail — **crash 41.8% vs 35.7% (+6.1pp)** — BUT positive mean (opt15 z=+2.5, apex15 z=+2.5: EARN_BOOST
is calibrated), apexSL z only +1.2, and a 2022-BEAR WINNER (78% vs 70%). So it's "higher-mean /
fatter-tail" (gap variance, already IV-crush-modeled in MC), not a clean separator. The only
defensible follow-up is a **Stage-3 earnings-window SIZING contraction** validated on DD directly —
it trades +2.5pp mean WR for tail reduction (net unclear), and it is NOT a NET fix.

⇒ No separator exists — not for lack of looking, but because (1) the cohort isn't statistically worse,
(2) the divergence flags are empty (components don't diverge from price), (3) the badness is one
bull-year. The scorer is well-calibrated; NET is normal momentum-name variance the wide-SL+dead-hold
absorbs by design. **Divergence/exhaustion/velocity thread fully closed on mean AND tail.**
