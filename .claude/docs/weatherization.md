# Weatherization — Verification-First Scoring Doctrine

Origin (June 2026): apply forecast-verification discipline to options scoring. Complements
[process.md](process.md) (mechanism shape) — this doc is verification discipline.

## Ethos → mapped disciplines

1. **Skill vs baseline** — must beat CLIMATOLOGY (base rate) AND PERSISTENCE (momentum). Positive EV isn't skill; beating dumb baselines is.
2. **Verify the predictand, not a proxy** — score the real option payoff (TP/SL asymmetry), not a generic barrier that hides stop risk.
3. **Reliability/calibration** — a 70%-confidence signal should verify ~70% of the time.
4. **Forward (OOS) verification** — hindcast skill != forecast skill; hold out a forward window.
5. **Parsimony** — fewer well-understood terms beat many fitted knobs.

## Verification Substrate

`experiments/skill_vs_baseline/` + `30dte_apex` barrier set in `database/barrier_cache.py`.

- **Predictand:** `30dte_apex` = funded Apex option payoff (call TP +30% -> 1.092sigma win / SL -70% -> 2.548sigma stop, 30d). Per-trade option-EV under +0.30/-0.70/-0.40 win/stop/expire is the headline metric — exposes the -70%-stop asymmetry a generic 2sigma/5sigma barrier hides (root cause behind SVD/v42/divergence/volume failures).
- **0d skill gate:** `verify_scorecard.run_scorecard(vid)` scores every version's 75+ apex-EV vs climatology AND momentum -> SHIP/FLAG/BLOCK (t>=2 floors). Artifact: `.../research_pack/verify_scorecard.json` (auto-built post-recalc, holdout-split) + VersionCompare 0d-verdict chip.
- **Honest-era verdict: FLAG = "risk-shaper".** v69-v74 beat momentum (t~+2.9) but only marginal vs climatology — it's a risk-shaper/leveraged-momentum selector, not directional alpha. FLAG is the accepted profile — never auto-revert on it.
- **Calibration:** `calibration_tail.py` (reliability diagram + spread-skill) built; dashboard panel not wired (deprioritized further, see below).
- **Forward/OOS:** `CALIBRATION_CUTOFF_DATE = 2026-06-15`; first true OOS read ~2026-12-15. Live Portfolio (started 2026-06-01) is the complementary real-money forward test.

## 6 audited flaws — scorecard

| # | Flaw | Status |
|---|---|---|
| 1 | Dampeners baked into scoring CORE (no MOS separation; look-ahead-leak, e.g. old EARN_BOOST) | RESOLVED — `compute_overall_score` split into `_compute_core_score` + `apply_bias_corrections` (`MECHANISM_REGISTRY`), bit-exact (183,024/183,024). `LiftTableMechanism` requires a `<table>.meta.json` holdout stamp so the leak class can't recur. |
| 2 | Verifying a proxy (generic 2sigma/5sigma barrier) not the predictand | RESOLVED — `30dte_apex` predictand + apex option-EV headline. |
| 3 | No reliability/skill-vs-baseline | RESOLVED — climatology+momentum baselines, 0d SHIP/FLAG/BLOCK gate, standing artifact + dashboard chip. |
| 4 | Five Stage-3 DD levers (RXDD/SVR/MWDD/TVDD/BDIV) should be one regularized regime index | DEFERRED (SKIP-default) — each passed Stage-3 T1-T7 independently and is orthogonal (survives all-other-levers-off slice). Forced consolidation has a dry-well prior; its bear/chop rider collides with two documented nulls (DD-sizing dry; VXMD crash-artifact coupling). Revisit only if `tier_drift.py` shows a lever gone redundant. |
| 5 | Sequential residual-mining vs joint regularized fitting | DEFERRED with #4 — same call. |
| 6 | No data assimilation (slow score-error feedback) | PARTIAL -> mostly NULL. Sector-error DA tested decisively NULL (within-bucket corr ~-0.01, era sign-flip, score-norm trap). Broader per-sector/regime feedback is scoped but overfit-prone; substrate-gated revisit only. |

## Parsimony — the v74 "lean"

Phase-3d per-member apex-EV attribution retired the net-dilutive post-`pre_boost` tail (continuation-echo NEUTRAL, EARN_BOOST NEUTRAL/N=77-noise, daily-volume NEGATIVE-EV, WVD HARMFUL): whole-tail ablation MC -10.8pp 5y DD at comparable compound, collapse=0, 4 fewer mechanisms. Each retirement documented with its measured effect + revisit gate in [scoring-algorithm.md](scoring-algorithm.md). Remaining candidate: put-side tail (PCD/weekly-put, funded-irrelevant since puts are OFF) — not yet done.

## Standing discipline

- Verify on the predictand (apex option-EV), not the generic barrier — a generic-WR win that dies on option payoff is the documented serial-killer (SVD/v42/divergence/volume).
- Beat climatology AND momentum (0d gate) before claiming alpha; the score is a risk-shaper — weight DD/survival, not raw return.
- New mechanisms go in the MOS bias layer (`apply_bias_corrections` registry), declared in `mechanism_registry.py`, gated on predictand + forward holdout. Fitted tables must carry a holdout stamp (`LiftTableMechanism`).
- Parsimony: a mechanism earns its keep on the funded book (apex-EV at portfolio scale), not generic WR/signal supply. Net-dilutive supply gets removed.
- Comparability is automatic (`build_research_pack` builds pack + supply-row + PRF, prints `comparability_unit=COMPLETE`, `--profiles all` default) — a version isn't gateable until comparable.

## What's left

No high-value scoring/portfolio mechanism left to build now — the high-leverage items shipped (substrate, MOS, comparability automation, v74 lean, SPREAD_TILT). Remainder is deferral/accrual/operational:

- **Regime-index consolidation (#4/#5) — do not build.** Conditional revisit only.
- **Data assimilation (#6) — do not build.** Sector-DA NULL; broader feedback overfit-prone/substrate-gated.
- **Forward holdout (0e) — accruing.** First OOS read ~2026-12-15 is the real generalization test.
- **Weekly-adjustment whiplash — CLOSED (tamed) 2026-06-16.** Flip-probe (`experiments/weekly_flip_probe/`): gross 75->{<75} flip rate 58% is boundary jitter (median day-over-day |delta|=5) + genuine de-qualification, not whiplash. Real instability metric (|delta overall|>=15 & <2% price move) is 7.4% full/5.4% recent; the COHR/VICR-magnitude form (|delta|>=20, flat price) ~3%, below the 5% close-floor and decreasing. v69 transition blend + v72 WCF 27/28-cliff ramp + v74 lean tamed severe whiplash; borderline residual has no surviving fix (replace-weekly-with-slow-index falsified — v42 rolling-weekly disaster, v44 substitution fail). Closed NULL, see `experiments/weekly_flip_probe/FINDINGS.md`.
- **Reliability dashboard panel** — low priority; calibration verification (2026-06-24, `experiments/verify_value/`) found the score is a calibration no-op (every band ~73% win, OOF reliability ~0), so a panel would render flat by construction.
- **Operational (open, not weatherization):** `trader update` 30-min-timeout failures (bump timeout/profile slowdown); `portfolio_engine` MTM-curve reconciliation (spawned chip, display-only ~0.7%, pre-existing).

## Resolved log

- **Verification Substrate** — predictand barrier + skill-vs-baseline 0d gate + artifact + dashboard chip (Flaws 2 & 3).
- **MOS core/bias separation** — bit-exact; `LiftTableMechanism` look-ahead guard (Flaw 1).
- **v74 lean** — retired net-dilutive post-`pre_boost` tail; -10.8pp DD, 4 fewer mechanisms.
- **Component-ensemble verification (2026-06-17, diagnostic)** — verified the 6 components as forecast ensemble members on apex payoff (`experiments/weather_components/FINDINGS.md`). Ensemble is back-to-front: **TREND** dominant score-driver (corr +0.72) yet zero per-trade resolution and regime-HARMFUL in bear/chop (2022 -2.42/2023 -1.81 delta-EV) — mechanistic root of the bear/chop weakness. **MACD+RSI** skillful (RSI bear-robust). **TA** is a SUPPRESSOR (multivar beta -0.63, t -3.59). Effective ensemble size 3.5 of 6; trend+macd anti-synergistic. Ensemble still beats its best member via the joint 75+ threshold (consensus-beats-best-member). TA-suppressor->v75-lean lead was probed->tested-NULL (2026-06-17): `W_TA=0` cut 75+ supply -11.2% and dropped higher-EV signals than kept in 4/5 windows (pooled "accretive" = 2024-Simpson artifact) — a universe-multivariate suppressor is NOT removable; reconfirms the reweight-null on the apex predictand. Remaining leads (no ship): regime-conditioned trend-down/rsi-up bear/chop defense (Stage-1, hard); 85-89 cascade trim (Stage-3, 2022-concentrated). SPREAD_TILT's per-trade premise is thin on v74-lean (funded 75-79 spread-skill +0.14pp vs +1.76pp on v73) — a watch metric.
- **Comparability automation** — `build_research_pack` builds the full unit by default + `--profiles all`.
- **SPREAD_TILT (new alpha-shaping win)** — substrate showed the score loses to momentum in bear/chop; kill-test found an orthogonal risk signal there (component disagreement at 75-79); validated on apex predictand + Stage-3 MC (5y DD -4.1pp, collapse=0); shipped 2026-06-15. See [trading-strategy.md](trading-strategy.md) + `mechanism_registry.py`.
- **Value/calibration verification (2026-06-24, diagnostic) — `experiments/verify_value/`.** Murphy/Brier decomposition + REV cost-loss + lineage ensemble + win-magnitude, all on apex predictand. (1) **Calibration is a NO-OP** — OOF reliability ~0, BSS_oof -0.0007 (every band maps to ~73% win) — closes NULL. (2) **Lineage ensemble (v70/v71/v73/v74, corr 0.68-0.89) is a stability/turnover smoother, not skill** — members share ~zero apex resolution. (3) **Score gradient above the 70 gate is per-trade-inert on every axis** — direction (within-70+ potential-BSS 0.0002), 3-outcome EV (flat by band), run-magnitude (MFE-sigma ~4.0sigma across all bands, Spearman -0.011). Entire apex value is the 70-gate selection (+~1pp EV, the only t~2 effect); no cheap model predicts it either. Parsimony principle: magnitude precision above the gate is worthless on the funded payoff — only gate-membership + portfolio sizing matter. Lean candidates = pure-gradient-shaping score-stage mechanisms; gate-acting mechanisms + DD-validated cascade/SPREAD_TILT are not implicated (`alpha_mining/NEW_LEADS.md` A2). Record: `experiments/verify_value/FINDINGS.md`.
- **Sector data-assimilation** — tested -> NULL (closes part of Flaw 6).
