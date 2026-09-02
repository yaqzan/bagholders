import csv

import portfolio_profiles


def test_compare_payload_reads_registry_and_phase_results(tmp_path):
    run_dir = tmp_path / ".codex" / "runs" / "profile_run"
    run_dir.mkdir(parents=True)
    rows = [
        {
            "candidate": "sentinel_v1_current",
            "window": "5y",
            "mean_final": "1000000",
            "worst_dd": "50",
            "mean_dd": "25",
            "p_coll": "0",
            "call_trades": "100",
            "put_trades": "20",
            "avg_open_premium_base_pct": "60",
        },
        {
            "candidate": "core_v1",
            "window": "5y",
            "mean_final": "2000000",
            "worst_dd": "55",
            "mean_dd": "30",
            "p_coll": "0",
            "call_trades": "120",
            "put_trades": "22",
            "avg_open_premium_base_pct": "70",
        },
    ]
    with (run_dir / "phase3_results.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with (run_dir / "phase3_summary.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["candidate", "core_score"])
        writer.writeheader()
        writer.writerow({"candidate": "core_v1", "core_score": "12.3"})

    portfolio_profiles.save_registry(
        {
            "schema_version": 1,
            "base_score_version": "v60",
            "generated_at": "2026-05-23T00:00:00Z",
            "evidence": {"run_dir": str(run_dir), "phase": "phase3"},
            "profiles": [
                {
                    "key": "sentinel",
                    "name": "Sentinel",
                    "version": 1,
                    "risk": "safe",
                    "color": "green",
                    "candidate": "sentinel_v1_current",
                    "status": "active",
                    "description": "",
                    "params": {},
                },
                {
                    "key": "core",
                    "name": "Core",
                    "version": 1,
                    "risk": "balanced",
                    "color": "yellow",
                    "candidate": "core_v1",
                    "status": "active",
                    "description": "",
                    "params": {},
                },
            ],
        },
        root=tmp_path,
    )

    payload = portfolio_profiles.compare_payload(tmp_path)
    core = next(row for row in payload["rows"] if row["key"] == "core")

    assert payload["base_score_version"] == "v60"
    assert payload["evidence"]["results_rows"] == 2
    assert core["windows"]["5y"]["metrics"]["mean_final_delta"] == 1_000_000.0
    assert core["windows"]["5y"]["metrics"]["dd_worse_pp"] == 5.0


def test_profile_config_overrides_maps_registry_params(tmp_path):
    portfolio_profiles.save_registry(
        {
            "schema_version": 1,
            "profiles": [
                {
                    "key": "core",
                    "name": "Core",
                    "version": 2,
                    "risk": "balanced",
                    "color": "yellow",
                    "candidate": "core_v2",
                    "status": "active",
                    "description": "",
                    "params": {
                        "gross_cap": 0.85,
                        "call_cap": 0.70,
                        "put_cap": 0.25,
                        "max_positions": 14,
                        "call_max": 12,
                        "put_max": 8,
                        "dd_lo": 0.40,
                        "dd_hi": 0.55,
                        "dd_floor": 0.65,
                        "tier_ultra": 0.20,
                        "tier_top": 0.15,
                        "tier_mid": 0.10,
                        "tier_low": 0.10,
                        "tier_overflow": 0.0,
                        "put_top": 0.12,
                        "put_mid": 0.10,
                        "put_low": 0.08,
                    },
                },
                {
                    "key": "apex",
                    "name": "Apex",
                    "version": 2,
                    "risk": "explosive",
                    "color": "red",
                    "candidate": "apex_fullportfolio_v2",
                    "status": "active",
                    "description": "",
                    "params": {
                        "practical_enabled": True,
                        "capital_ceiling": 0.0,
                        "gross_cap": 0.90,
                    },
                }
            ],
        },
        root=tmp_path,
    )

    cfg = portfolio_profiles.profile_config_overrides("balanced", tmp_path)

    assert cfg["gross_premium_cap"] == 0.85
    assert cfg["call_premium_cap"] == 0.70
    assert cfg["max_positions_call"] == 12
    assert cfg["dd_soft_call_floor"] == 0.65
    assert cfg["tier_alloc"]["95+"] == 0.20
    assert cfg["tier_alloc"]["p21-25"] == 0.08

    apex_cfg = portfolio_profiles.profile_config_overrides("explosive", tmp_path)
    assert apex_cfg["practical_exposure_enabled"] is True
    assert apex_cfg["practical_capital_ceiling"] == 0.0
    assert apex_cfg["gross_premium_cap"] == 0.90
