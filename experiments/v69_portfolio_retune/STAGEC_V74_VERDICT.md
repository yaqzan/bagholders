# Core tier/overflow/MaxPos re-sweep on v74 supply (P3.2) — VERDICT (FABLE, 2026-07-14)

**CLOSED — shipped Core CONFIRMED at the frontier. Stage D not licensed.**
Stage B: 81 cells, N=100 (task 612, `stageB_v74_p32_n100.jsonl`). Stage C: survivors {mp12, mp16}
vs shipped control (mid08_low03_ovf000_mp14, verified byte-identical to `strategy_config.py`),
N=300, stage-B windows + 2020_crash + 2020, paired seeds (task 629, `stageC_v74_p32_n300.jsonl`).

## Stage C (N=300)
| cell | 5y DD / med comp | 2020_crash DD | 22-now DD / comp | max coll |
|---|---|---|---|---|
| mp12 | 48.4 / +2,392.6 | 69.9 | 48.8 / +787.5 | 0.0 |
| **mp14 (shipped)** | 49.6 / +3,050.1 | 69.7 | 48.8 / +893.0 | 0.0 |
| mp16 | 48.0 / +3,210.7 | **71.4** | 49.1 / +910.1 | 0.0 |

- mp16: 5y DD −1.6pp / comp +5% vs shipped, BUT 2020_crash DD +1.7pp WORSE and 22-now DD +0.3
  worse — sign flips across windows; nothing consistent; all deltas inside the N=300 noise floor
  (±3pp DD; the "real signal" bar is 2-3pp consistent across ≥5 windows — not met anywhere).
- mp12: −1.2pp 5y DD (noise-level) at a real cost (−22% 5y comp, −12% 22-now comp) — not the
  c04 targeted-selectivity pattern (which improved DD at flat-or-better compound).
- Collapse 0.0 everywhere including the crash windows, all three cells.

## Rulings
1. **P3.2 CLOSED.** The v71/v73 retunes' endpoint (mid 0.08 / low 0.03 / overflow 0 / MaxPos 14)
   is the frontier of this grid on v74's deflated supply: stage B showed every overflow>0 and
   fatter-tier family dominated; stage C shows the two MaxPos neighbors are noise-or-worse.
2. Stage D (N=500×10) NOT run — escalating noise-level, non-Pareto deltas would spend two queue
   nights to confirm a null (MC-noise-floor doctrine).
3. Do not re-run this grid on v74; re-open only on a supply-shifting scoring ship (the v71
   "supply-density-conditional" clause) or a changed cost model from the P3.7 real-fill loop.

## Artifacts
retune_stageB_v74_p32.py / retune_stageC_v74_p32.py · stageB_v74_p32_n100.jsonl (81 cells) ·
stageC_v74_p32_n300.jsonl · per-cell logs · queue tasks 612, 629. (Note: `stageC_n300.jsonl` /
`retune_stageC.py` in this dir are an UNRELATED May-2026 experiment — different cell taxonomy.)
