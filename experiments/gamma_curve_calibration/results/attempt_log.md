# gamma_curve_calibration -- attempt log (append-only, rail SS S.1)

Every driver invocation (build_panel.py, fit_curve.py), including dry
runs and aborts, appends one line here: timestamp, git HEAD, exact
command, outcome. This file is NEVER overwritten, only appended to.

- [2026-07-18T06:24:05] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:25:06] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=CRASHED detail="OperationalError(3024, 'Query execution was interrupted, maximum statement execution time exceeded')"
- [2026-07-18T06:29:07] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:30:55] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=CRASHED detail="ColumnNotFoundError('unable to find column "w_d"; valid columns: ["option_id", "date_d", "V_d", "volume_d", "oi_d", "iv_raw_d", "date_dprime", "V_dprime", "g", "symbol", "strike", "expiration_date", "side", "S_d", "S_dprime", "tau_d", "r_S", "r_V", "m_d", "m_dprime", "excl_stage"]')"
- [2026-07-18T06:31:24] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:31:28] git=5fbef3e6fc67c581a5ffc868d7f5ee51a9826250 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=INSUFFICIENT_N detail="train_bars_n=0 floor=50000"
- [2026-07-18T06:34:44] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:34:48] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=INSUFFICIENT_N detail="train_bars_n=0 floor=50000"
- [2026-07-18T06:35:39] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:35:43] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=INSUFFICIENT_N detail="train_bars_n=0 floor=50000"
- [2026-07-18T06:36:01] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:36:05] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="build_panel.py --smoke" outcome=CENTROID_STOP detail="flagged_fraction=0.3510%"
- [2026-07-18T06:36:47] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:36:51] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=INSUFFICIENT_N detail="train_bars_n=0 floor=50000"
- [2026-07-18T06:39:57] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T06:39:57] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_FAIL
- [2026-07-18T06:42:19] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T06:42:19] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_FAIL
- [2026-07-18T06:45:33] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T06:45:33] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_FAIL
- [2026-07-18T06:46:54] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T06:46:54] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_FAIL
- [2026-07-18T06:48:25] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=STARTED detail="smoke=True"
- [2026-07-18T06:48:29] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py --smoke" outcome=INSUFFICIENT_N detail="train_bars_n=0 floor=50000"
- [2026-07-18T06:49:39] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="fit_curve.py" outcome=INSUFFICIENT_N_AT_FIT detail="bars_n=0"
- [2026-07-18T06:50:22] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="fit_curve.py" outcome=SUCCESS detail="k_pooled=0.85 train_sha256=63ddfaace2578ce0aca029cd2e79e3851a5cd14a502f535dcc1a72ffdecdc30a"
- [2026-07-18T06:51:15] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T06:51:15] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_FAIL
- [2026-07-18T06:51:44] git=8bab41b52dc72ff38a1cc9bd0a861320742387e4 cmd="experiments/gamma_curve_calibration/build_panel.py" outcome=STARTED detail="smoke=False"
- [2026-07-18T07:11:32] git=1c15953660c79885cc5d74250d97762715637cae cmd="experiments/gamma_curve_calibration/build_panel.py" outcome=SUCCESS detail="train_n=1505983 oos_n=165125 train_sha256=72ad204e1024abbe9b7da22311834f142c66371f2f3bf3297a44ab9b120be2a6 oos_sha256=26f5575c0006322c0a9ce1b7417846122aa4e6419fa956370d7e9f00846655f6"
- [2026-07-18T07:12:44] git=1c15953660c79885cc5d74250d97762715637cae cmd="experiments/gamma_curve_calibration/fit_curve.py" outcome=STARTED_FULL_FIT
- [2026-07-18T07:12:44] git=1c15953660c79885cc5d74250d97762715637cae cmd="experiments/gamma_curve_calibration/fit_curve.py" outcome=SELFTEST_FAILED_ABORT_FIT
- [2026-07-18T07:15:42] git=1c15953660c79885cc5d74250d97762715637cae cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_STARTED
- [2026-07-18T07:15:42] git=1c15953660c79885cc5d74250d97762715637cae cmd="experiments/gamma_curve_calibration/fit_curve.py --selftest" outcome=SELFTEST_PASS
- [2026-07-18T07:16:05] git=1e448eb194a5d2cbfe01c33d013fb685b6ac1b09 cmd="experiments/gamma_curve_calibration/fit_curve.py" outcome=STARTED_FULL_FIT
- [2026-07-18T07:16:36] git=1e448eb194a5d2cbfe01c33d013fb685b6ac1b09 cmd="experiments/gamma_curve_calibration/fit_curve.py" outcome=SUCCESS detail="k_pooled=0.91 train_sha256=72ad204e1024abbe9b7da22311834f142c66371f2f3bf3297a44ab9b120be2a6"
