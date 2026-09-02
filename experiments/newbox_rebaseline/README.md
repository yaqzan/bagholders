# newbox_rebaseline

This directory holds the runners for the ratified 2026-H2 first-week compute
program (`.claude/docs/gameplan-2026H2-DRAFT.md` section 5): the MC-determinism
parity gate that must clear before any new-box number is compared cross-box
against the old-box corpus, the P1.A E-tier certificates, the P1.E noise-floor
measurement, the P1.D 10y refresh chain, and the three licensed power re-runs
of compute-truncated decisions (pessimism certification, deep crash screens,
and the v74 whole-tail ablation).

| Runner | Purpose |
|---|---|
| `fingerprint.py` | Captures the environment snapshot (host, Python, numpy/BLAS, git commit+dirty) every other runner embeds into its summary artifact. |
| `run_parity_gate.py` | P0.B: re-runs one archived task-610 arm and diffs it against `experiments/apex_dte_dd/results_p03_evidence/summary.json` to certify (or clean-break) new-box MC determinism. Blocks every cross-box comparison until it renders a verdict. |
| `run_ecert.py` | P1.A: E-tier certificates (N=2000 x 12 standard windows + N=1000 x 4 deep screens) for the Core / Apex-live / Apex-n10 / Sentinel portfolio profiles. |
| `run_noise_floor.py` | P1.E: measures seed-noise dispersion (worst_dd / mean_ret / med_ret / p_coll) across independent RNG batches at N in {300,500,1000,2000}, superseding the inherited Phase-v32 noise figures. |
| `run_refresh_10y.py` | P1.D: the sequential recalc -> assess -> research-pack chain plus a measured per-step runtime table. |
| `run_pessimism_n1000.py` | P1.C-1: re-certifies the execution-pessimism robustness matrix (7 arms x 2 profiles x 9 windows) at N=1000. |
| `run_deep_screen_n1000.py` | P1.C-2: re-runs the 4 deep-crash SCREEN windows (Core + Apex-held) at N=1000. |
| `run_tail_ablation_n1000.py` | P1.C-3: re-ablates the v74 whole score-stage tail on the v73 substrate at N=1000, in-sample windows only -- pre-arms the December 2026 H5 stage-2 read. |
| `recipes.py` | Single source of truth for every arm/profile MC env recipe the runners above use; every recipe is cross-checked against its canonical source file. |
| `_common.py` | Shared DB-free helpers: source-of-truth window/threshold parsers, aggregate-math replication, the subprocess driver base, and small IO/safety utilities. |

All real (non-`--selftest`) runs go through `trader queue submit` box-side --
see `RUNBOOK.md` (authored separately) for the exact per-night invocations and
reading rules. This directory never runs compute directly on the orchestrator's
own machine.

**Selftest convention:** every runner supports `python
experiments/newbox_rebaseline/<runner>.py --selftest`, which is fully
DB-free/offline (no MySQL, no peewee, no MC subprocess launch) and exits 0 on
pass / 1 on fail with one PASS/FAIL line per check. Run this after any edit to
this package before trusting a queued invocation.
