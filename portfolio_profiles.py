"""Versioned portfolio-risk profile registry.

Profiles are portfolio-only overlays on top of the active score version. They
do not change ``Score.overall`` and therefore do not require an
``ALGORITHM_VERSION`` bump.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REGISTRY_PATH = Path("algorithm_versions") / "portfolio_profiles.json"
DEFAULT_PROFILE_KEY = "core"   # restructure 2026-06-17: Core (former Apex, the long-run held compounder, uncapped) is the default; Apex is now an opt-in fast-2x SPRINT

PROFILE_ORDER = ("sentinel", "core", "apex")
PROFILE_COLORS = {
    "sentinel": "green",
    "core": "yellow",
    "apex": "red",
}

WINDOW_LABELS = {
    "2020_crash": "2020 crash",
    "covid_peak": "COVID peak",
    "2020_full": "2020 full",
    "2022": "2022",
    "2024": "2024",
    "2025": "2025",
    "2025_dip": "2025 dip",
    "2022_now": "2022-now",
    "5y": "5y",
}

PARAM_KEYS = (
    "practical_enabled",
    "capital_ceiling",
    "gross_cap",
    "call_cap",
    "put_cap",
    "call_ref",
    "put_ref",
    "sat_power",
    "sat_floor",
    "max_positions",
    "call_max",
    "put_max",
    "dd_lo",
    "dd_hi",
    "dd_floor",
    "tier_ultra",
    "tier_top",
    "tier_mid",
    "tier_low",
    "tier_overflow",
    "put_top",
    "put_mid",
    "put_low",
    # option-level (nested in .option) — profile-overridable TP/SL (2026-06-22, Apex elbow)
    "tp_base",
    "tp_stress",
    "sl_base",
    "sl_stress",
    # DTE / honest-calendar (top-level DteStrategyConfig) — lets a profile run a
    # different option tenor (e.g. Apex = honest 15-DTE) on the shared engine.
    "nominal_cal_dte",
    "hold_cal_days",
    # LIQUIDITY_FLOOR — staged ship-candidate 2026-08-07 (mechanism_registry.py;
    # FF3_STAGEB_RESULTS.md). Core-evidence-only (Apex failed T4) — enable plan
    # is Core=150/Apex=0, so this needs to be profile-overridable even though no
    # profile sets a value yet (strategy_config default stays 0.0 = OFF).
    "liquidity_floor",
)

BOOL_PARAM_KEYS = {"practical_enabled"}
INT_PARAM_KEYS = {"max_positions", "call_max", "put_max", "nominal_cal_dte", "hold_cal_days"}

PROFILE_ALIASES = {
    "safe": "sentinel",
    "ddsafe": "sentinel",
    "dd-safe": "sentinel",
    "balanced": "core",
    "explosive": "apex",
    "fire": "apex",
    "high": "apex",
    "highrisk": "apex",
    "high-risk": "apex",
}

PROFILE_CONFIG_KEYS = {
    "practical_enabled": "practical_exposure_enabled",
    "capital_ceiling": "practical_capital_ceiling",
    "gross_cap": "gross_premium_cap",
    "call_cap": "call_premium_cap",
    "put_cap": "put_premium_cap",
    "call_ref": "opp_sat_call_ref",
    "put_ref": "opp_sat_put_ref",
    "sat_power": "opp_sat_power",
    "sat_floor": "opp_sat_floor",
    "max_positions": "max_positions",
    "call_max": "max_positions_call",
    "put_max": "max_positions_put",
    "dd_lo": "dd_soft_band_lo",
    "dd_hi": "dd_soft_band_hi",
    "dd_floor": "dd_soft_call_floor",
    "liquidity_floor": "liquidity_floor",
}

PROFILE_TIER_KEYS = {
    "tier_ultra": "95+",
    "tier_top": "85-94",
    "tier_mid": "80-84",
    "tier_low": "75-79",
    "tier_overflow": "70-74",
    "put_top": "p<=15",
    "put_mid": "p16-20",
    "put_low": "p21-25",
}

PROFILE_STRATEGY_ATTRS = {
    "practical_enabled": "PRACTICAL_EXPOSURE_ENABLED",
    "capital_ceiling": "PRACTICAL_CAPITAL_CEILING",
    "gross_cap": "GROSS_PREMIUM_CAP",
    "call_cap": "CALL_PREMIUM_CAP",
    "put_cap": "PUT_PREMIUM_CAP",
    "call_ref": "OPP_SAT_CALL_REF",
    "put_ref": "OPP_SAT_PUT_REF",
    "sat_power": "OPP_SAT_POWER",
    "sat_floor": "OPP_SAT_FLOOR",
    "max_positions": "MAX_POSITIONS",
    "call_max": "MAX_POSITIONS_CALL",
    "put_max": "MAX_POSITIONS_PUT",
    "dd_lo": "DD_SOFT_BAND_LO",
    "dd_hi": "DD_SOFT_BAND_HI",
    "dd_floor": "DD_SOFT_CALL_FLOOR",
    "nominal_cal_dte": "NOMINAL_CAL_DTE",
    "hold_cal_days": "HOLD_CAL_DAYS",
    "liquidity_floor": "LIQUIDITY_FLOOR",
}

# Option-level (.option sub-config) overrides — applied via dataclasses.replace on
# the frozen OptionStrategyConfig so a profile can carry its own TP/SL.
PROFILE_OPTION_ATTRS = {
    "tp_base": "TP_BASE",
    "tp_stress": "TP_STRESS",
    "sl_base": "SL_BASE",
    "sl_stress": "SL_STRESS",
}

PROFILE_TIER_ATTRS = {
    "tier_ultra": ("TIER_ALLOC", "ultra"),
    "tier_top": ("TIER_ALLOC", "top"),
    "tier_mid": ("TIER_ALLOC", "mid"),
    "tier_low": ("TIER_ALLOC", "low"),
    "tier_overflow": ("TIER_ALLOC", "overflow"),
    "put_top": ("PUT_TIER_ALLOC", "put_top"),
    "put_mid": ("PUT_TIER_ALLOC", "put_mid"),
    "put_low": ("PUT_TIER_ALLOC", "put_low"),
}


@dataclass(frozen=True)
class ProfileSelection:
    key: str
    name: str
    version: int
    risk: str
    color: str
    candidate: str
    status: str
    description: str
    params: dict[str, Any]


def _project_root(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parent).resolve()


def _registry_file(root: Path | None = None) -> Path:
    return _project_root(root) / REGISTRY_PATH


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _fallback_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "base_score_version": "v60",
        "generated_at": None,
        "evidence": {},
        "profiles": [
            {
                "key": "sentinel",
                "name": "Sentinel",
                "version": 1,
                "risk": "safe",
                "color": PROFILE_COLORS["sentinel"],
                "candidate": "sentinel_v1_current",
                "status": "active",
                "description": "Current DD-safe Sentinel portfolio profile on active score rows.",
                "params": {
                    "practical_enabled": True,
                    "capital_ceiling": 25_000_000.0,
                    "gross_cap": 0.80,
                    "call_cap": 0.65,
                    "put_cap": 0.25,
                    "call_ref": 16.0,
                    "put_ref": 4.0,
                    "sat_power": 0.50,
                    "sat_floor": 0.55,
                    "max_positions": 14,
                    "call_max": 12,
                    "put_max": 8,
                    "dd_lo": 0.35,
                    "dd_hi": 0.55,
                    "dd_floor": 0.40,
                    "tier_ultra": 0.20,
                    "tier_top": 0.15,
                    "tier_mid": 0.10,
                    "tier_low": 0.10,
                    "tier_overflow": 0.00,
                    "put_top": 0.12,
                    "put_mid": 0.10,
                    "put_low": 0.08,
                },
            }
        ],
    }


def load_registry(root: Path | None = None) -> dict[str, Any]:
    path = _registry_file(root)
    if not path.exists():
        return _fallback_registry()
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("profiles", [])
    payload.setdefault("evidence", {})
    return payload


def save_registry(payload: dict[str, Any], root: Path | None = None) -> Path:
    path = _registry_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _json_safe(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def profile_map(root: Path | None = None) -> dict[str, dict[str, Any]]:
    registry = load_registry(root)
    out = {row.get("key"): row for row in registry.get("profiles", []) if row.get("key")}
    return {key: out[key] for key in PROFILE_ORDER if key in out}


def normalize_profile_key(key: str | None, root: Path | None = None) -> str:
    token = (key or DEFAULT_PROFILE_KEY).strip().lower().replace("_", "-")
    token = PROFILE_ALIASES.get(token, token)
    profiles = profile_map(root)
    if token not in profiles:
        valid = ", ".join(profiles.keys()) or ", ".join(PROFILE_ORDER)
        raise KeyError(f"Unknown portfolio profile {key!r}; expected one of: {valid}")
    return token


def get_profile(key: str | None = None, root: Path | None = None) -> dict[str, Any]:
    profiles = profile_map(root)
    return profiles[normalize_profile_key(key, root)]


def profile_options(root: Path | None = None) -> list[dict[str, Any]]:
    return [
        {
            "key": row.get("key"),
            "name": row.get("name"),
            "version": row.get("version"),
            "risk": row.get("risk"),
            "color": row.get("color"),
            "status": row.get("status"),
            "description": row.get("description"),
            "params": row.get("params") or {},
        }
        for row in profile_map(root).values()
    ]


def payload(root: Path | None = None) -> dict[str, Any]:
    registry = load_registry(root)
    profiles = profile_map(root)
    return {
        "schema_version": registry.get("schema_version", SCHEMA_VERSION),
        "base_score_version": registry.get("base_score_version"),
        "generated_at": registry.get("generated_at"),
        "default_profile": DEFAULT_PROFILE_KEY,
        "evidence": registry.get("evidence", {}),
        "profiles": [profiles[key] for key in profiles],
    }


def _derived_engine_pricing(params: dict[str, Any]) -> dict[str, Any]:
    """Engine-cfg pricing for a profile that overrides DTE and/or TP/SL.

    Honest-calendar: ATM premium ~ sqrt(DTE), so a 15-DTE profile uses
    ``premium_mult = base * sqrt(15/30)`` and the sigma barriers scale with it
    (tp_sigma = tp_pct * premium_mult / delta). net pnl targets are DTE-invariant
    (just pct + slips). Only fires when the profile carries DTE or TP/SL overrides,
    so Core/Sentinel emit nothing and the engine keeps its module-global defaults
    (bit-identical). Mirrors the honest-calendar engine math validated in the
    concentration_2x risk-budget frontier (NOMINAL_CAL_DTE harness)."""
    has_dte = params.get("nominal_cal_dte") is not None
    has_opt = any(params.get(k) is not None for k in ("tp_base", "tp_stress", "sl_base", "sl_stress"))
    if not (has_dte or has_opt):
        return {}
    import strategy_config as sc
    base = sc.STRATEGY_30DTE
    opt = base.option
    out: dict[str, Any] = {}
    pm = float(base.PREMIUM_MULT)
    if has_dte:
        nominal = int(params["nominal_cal_dte"])
        out["nominal_cal_dte"] = nominal
        out["calendar_hold"] = True
        pm = pm * (nominal / 30.0) ** 0.5
    if params.get("hold_cal_days") is not None:
        out["hold_calendar_days"] = int(params["hold_cal_days"])
    delta = float(opt.DELTA)
    tp_b = float(params.get("tp_base", opt.TP_BASE))
    tp_s = float(params.get("tp_stress", opt.TP_STRESS))
    sl_b = float(params.get("sl_base", opt.SL_BASE))
    sl_s = float(params.get("sl_stress", opt.SL_STRESS))
    out["premium_mult"]    = pm
    out["tp_sigma_base"]   = tp_b * pm / delta
    out["tp_sigma_stress"] = tp_s * pm / delta
    out["sl_sigma_base"]   = abs(sl_b) * pm / delta
    out["sl_sigma_stress"] = abs(sl_s) * pm / delta
    out["net_tp_base"]   = tp_b + opt.SLIP_ENTRY + opt.SLIP_TP
    out["net_tp_stress"] = tp_s + opt.SLIP_ENTRY + opt.SLIP_TP
    out["net_sl_base"]   = sl_b + opt.SLIP_ENTRY + opt.SLIP_SL
    out["net_sl_stress"] = sl_s + opt.SLIP_ENTRY + opt.SLIP_SL
    return out


def profile_config_overrides(key: str | None = None, root: Path | None = None) -> dict[str, Any]:
    profile = get_profile(key, root)
    params = profile.get("params") or {}
    cfg: dict[str, Any] = {}

    for param_key, cfg_key in PROFILE_CONFIG_KEYS.items():
        if param_key in params:
            cfg[cfg_key] = params[param_key]

    tier_alloc = {
        cfg_key: params[param_key]
        for param_key, cfg_key in PROFILE_TIER_KEYS.items()
        if param_key in params and params[param_key] is not None
    }
    if tier_alloc:
        cfg["tier_alloc"] = tier_alloc

    # DTE / TP-SL overriding profiles (e.g. Apex 15-DTE elbow) need the honest-calendar
    # pricing derived for the engine cfg path (run_cascade_backtest / portfolio_engine).
    cfg.update(_derived_engine_pricing(params))

    return cfg


def apply_profile_to_config(
    cfg: dict[str, Any] | None = None,
    key: str | None = None,
    root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = get_profile(key, root)
    merged = dict(cfg or {})
    merged.update(profile_config_overrides(profile["key"], root))
    return merged, profile


class ProfiledStrategyConfig:
    """Read-through strategy config with portfolio-profile overrides."""

    def __init__(self, base: Any, profile: dict[str, Any]):
        self._base = base
        self.profile = profile

        params = profile.get("params") or {}
        self._overrides: dict[str, Any] = {}
        for param_key, attr in PROFILE_STRATEGY_ATTRS.items():
            if param_key in params:
                self._overrides[attr] = params[param_key]

        # Option-level overrides (TP/SL): rebuild the frozen .option sub-config.
        opt_over = {
            attr: params[param_key]
            for param_key, attr in PROFILE_OPTION_ATTRS.items()
            if param_key in params and params[param_key] is not None
        }
        base_opt = getattr(base, "option", None)
        if opt_over and base_opt is not None:
            self._overrides["option"] = replace(base_opt, **opt_over)

        tier_alloc = dict(getattr(base, "TIER_ALLOC", {}) or {})
        put_tier_alloc = dict(getattr(base, "PUT_TIER_ALLOC", {}) or {})
        for param_key, (attr, tier_key) in PROFILE_TIER_ATTRS.items():
            if param_key not in params or params[param_key] is None:
                continue
            if attr == "TIER_ALLOC":
                tier_alloc[tier_key] = params[param_key]
            else:
                put_tier_alloc[tier_key] = params[param_key]
        self._overrides["TIER_ALLOC"] = tier_alloc
        self._overrides["PUT_TIER_ALLOC"] = put_tier_alloc

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def profiled_strategy_config(base: Any, key: str | None = None, root: Path | None = None) -> tuple[ProfiledStrategyConfig, dict[str, Any]]:
    profile = get_profile(key, root)
    return ProfiledStrategyConfig(base, profile), profile


def _float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _row_metrics(row: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    mean_final = _float(row.get("mean_final"))
    baseline_final = _float((baseline or {}).get("mean_final"))
    worst_dd = _float(row.get("worst_dd"))
    baseline_dd = _float((baseline or {}).get("worst_dd"))
    return {
        "mean_final": mean_final,
        "baseline_mean_final": baseline_final,
        "mean_final_delta": (mean_final - baseline_final) if mean_final is not None and baseline_final is not None else None,
        "return_pct": ((mean_final / 50_000.0) - 1.0) * 100.0 if mean_final is not None else None,
        "mean_dd_pct": _float(row.get("mean_dd")),
        "worst_dd_pct": worst_dd,
        "baseline_worst_dd_pct": baseline_dd,
        "dd_worse_pp": (worst_dd - baseline_dd) if worst_dd is not None and baseline_dd is not None else None,
        "p_coll_pct": _float(row.get("p_coll")),
        "call_tp_pct": _float(row.get("call_tp")),
        "put_tp_pct": _float(row.get("put_tp")),
        "call_trades": _float(row.get("call_trades")),
        "put_trades": _float(row.get("put_trades")),
        "avg_open_premium_base_pct": _float(row.get("avg_open_premium_base_pct")),
        "avg_call_open_premium_base_pct": _float(row.get("avg_call_open_premium_base_pct")),
        "avg_put_open_premium_base_pct": _float(row.get("avg_put_open_premium_base_pct")),
        "avg_saturation_scale": _float(row.get("avg_saturation_scale")),
        "n_iter": _float(row.get("n_iter")),
    }


def compare_payload(root: Path | None = None) -> dict[str, Any]:
    project = _project_root(root)
    registry = load_registry(project)
    evidence = registry.get("evidence") or {}
    phase = evidence.get("phase") or "phase3"
    run_dir_raw = evidence.get("run_dir")
    run_dir = Path(run_dir_raw) if run_dir_raw else None
    if run_dir and not run_dir.is_absolute():
        run_dir = project / run_dir

    results_path = run_dir / f"{phase}_results.csv" if run_dir else None
    summary_path = run_dir / f"{phase}_summary.csv" if run_dir else None
    results = _read_csv(results_path) if results_path else []
    summary_rows = _read_csv(summary_path) if summary_path else []
    summary_by_candidate = {row.get("candidate"): row for row in summary_rows}
    by_key = {(row.get("candidate"), row.get("window")): row for row in results}

    profiles = list(profile_map(project).values())
    windows = []
    result_windows = [row.get("window") for row in results if row.get("window")]
    for name in WINDOW_LABELS:
        if not result_windows or name in result_windows:
            windows.append({"name": name, "label": WINDOW_LABELS[name]})

    rows = []
    for profile in profiles:
        candidate = profile.get("candidate")
        profile_windows = {}
        for window in windows:
            row = by_key.get((candidate, window["name"]))
            base = by_key.get(("sentinel_v1_current", window["name"]))
            if row:
                profile_windows[window["name"]] = {
                    "ready": True,
                    "label": window["label"],
                    "metrics": _row_metrics(row, base),
                }
            else:
                profile_windows[window["name"]] = {
                    "ready": False,
                    "label": window["label"],
                    "metrics": None,
                }
        rows.append(
            {
                **profile,
                "summary": summary_by_candidate.get(candidate, {}),
                "windows": profile_windows,
            }
        )

    return {
        "schema_version": registry.get("schema_version", SCHEMA_VERSION),
        "base_score_version": registry.get("base_score_version"),
        "generated_at": registry.get("generated_at"),
        "evidence": {
            **evidence,
            "results_path": str(results_path) if results_path else None,
            "summary_path": str(summary_path) if summary_path else None,
            "results_rows": len(results),
        },
        "windows": windows,
        "rows": rows,
    }


def params_from_sweep_row(row: dict[str, Any]) -> dict[str, Any]:
    params = {}
    for key in PARAM_KEYS:
        if key not in row:
            continue
        value = row.get(key)
        if key in BOOL_PARAM_KEYS:
            if isinstance(value, bool):
                params[key] = value
            else:
                params[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        elif key in INT_PARAM_KEYS:
            if value in (None, "", "None", "none"):
                params[key] = None
            else:
                numeric = _float(value)
                params[key] = int(numeric) if numeric is not None else None
        else:
            params[key] = _float(value)
    return params
