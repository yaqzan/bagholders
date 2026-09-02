# LESSONS — append-only

- 2026-08-10 (builder recon): GROSS_PREMIUM_CAP/CALL_PREMIUM_CAP are import-time env
  globals read LIVE inside run_single_sim in MP workers (mc:1198-1200, 3319-3321), NOT
  baked into ctx and NOT in _apply_cell_params/_broadcast_cell_params -> a pool that
  outlives a gross change feeds workers a stale cap. Shard: one gross per OS process,
  env set before import. Shape x MaxPos share prepare+pool safely (native cell-params).
- 2026-08-10 (builder catch, trap-class: profile-inheritance leak, RESEARCH-env side):
  mc_patch.apply_profile_env('apex') carries NO TP/SL (tpsl campaign set them per-cell
  via set_tpsl) -> alloc cells would have silently simulated Apex on CORE's defaults
  (TP10/SL-100) instead of Apex's pinned TP10/SL-60. Fixed with frozen env pins in
  allocA_run.py; config echo verified per profile. Same family as ship-portfolio GUARD 9.
- 2026-08-10 (grid mechanics): the locked Apex filter n*frac in [0.60,1.05] yields 9
  cells, not the prereg parenthetical "~16" (n=14 has zero surviving fracs). Mechanical
  consequence of the locked rule -> proceeded; estimate/actual mismatch recorded.
- 2026-08-10 (continuity): N=20 smoke baselines agree tightly with the tpsl Phase D
  N=500 numbers (core 22-now dd 38.3 vs 39.0; apex crash dd 63.3 vs 64.1) -- post-ship
  engine state reproduces the shipping evidence.
