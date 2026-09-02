from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from algorithm_versions.manager import normalize_version_key, project_root, read_json, snapshot_dir


def parse_version_tokens(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace(";", ",").split(",")
    else:
        parts = []
        for item in raw:
            parts.extend(str(item).replace(";", ",").split(","))
    tokens = []
    seen = set()
    for part in parts:
        token = part.strip()
        if not token:
            continue
        key = normalize_version_key(token)
        if key not in seen:
            seen.add(key)
            tokens.append(key)
    return tokens


def _load_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _set_parent_attr(fullname: str, module: Any | None) -> tuple[Any | None, bool]:
    parent_name, _, child_name = fullname.rpartition(".")
    if not parent_name:
        return None, False
    parent = sys.modules.get(parent_name)
    if parent is None:
        return None, False
    had_attr = hasattr(parent, child_name)
    previous = getattr(parent, child_name, None)
    if module is None:
        if had_attr:
            delattr(parent, child_name)
    else:
        setattr(parent, child_name, module)
    return previous, had_attr


def _load_silo_module(
    *,
    stable_name: str,
    canonical_name: str,
    path: Path,
    temporary_modules: dict[str, Any],
) -> Any:
    previous_modules = {name: sys.modules.get(name) for name in temporary_modules}
    previous_target = sys.modules.get(canonical_name)
    previous_parent_attrs: dict[str, tuple[Any | None, bool]] = {}
    try:
        for name, module in temporary_modules.items():
            sys.modules[name] = module
            previous_parent_attrs[name] = _set_parent_attr(name, module)
        module = _load_module_from_path(stable_name, path)
        return module
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
            old_attr, had_attr = previous_parent_attrs.get(name, (None, False))
            if had_attr:
                _set_parent_attr(name, old_attr)
            else:
                _set_parent_attr(name, None)
        if previous_target is None:
            sys.modules.pop(canonical_name, None)
        else:
            sys.modules[canonical_name] = previous_target


def _normalize_compute_result(result: Any) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
    if isinstance(result, tuple):
        if len(result) == 3:
            overall, weight_info, vol_update = result
        elif len(result) == 2:
            overall, weight_info = result
            vol_update = None
        elif len(result) == 1:
            overall = result[0]
            weight_info = {}
            vol_update = None
        else:
            overall = result[0]
            weight_info = {"extra_result_values": list(result[1:])}
            vol_update = None
    else:
        overall = result
        weight_info = {}
        vol_update = None
    if not isinstance(weight_info, dict):
        weight_info = {"raw_weight_info": weight_info}
    return overall, weight_info, vol_update


@dataclass(frozen=True)
class ScoringEngine:
    key: str
    version_id: int
    manifest: dict[str, Any]
    module: Any
    accepted_kwargs: frozenset[str]
    accepts_var_kwargs: bool

    def compute_overall_score(self, *args: Any, **kwargs: Any) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
        if not self.accepts_var_kwargs:
            kwargs = {k: v for k, v in kwargs.items() if k in self.accepted_kwargs}
        result = self.module.compute_overall_score(*args, **kwargs)
        overall, weight_info, vol_update = _normalize_compute_result(result)
        weight_info = dict(weight_info)
        weight_info.setdefault(
            "algorithm_silo",
            {
                "key": self.key,
                "version_id": self.version_id,
                "source_commit": self.manifest.get("source", {}).get("commit_short"),
            },
        )
        return overall, weight_info, vol_update


_ENGINE_CACHE: dict[str, ScoringEngine] = {}
_ALL_RUNTIME_VERSION_TOKENS = {
    "all",
    "all-production",
    "all-runtime",
    "production",
    "runtime",
}


def _is_all_runtime_versions_token(token: str) -> bool:
    return str(token).strip().lower() in _ALL_RUNTIME_VERSION_TOKENS


def load_scoring_engine(version) -> ScoringEngine:
    key = f"v{int(version.id)}"
    cached = _ENGINE_CACHE.get(key)
    if cached is not None:
        return cached

    root = project_root()
    snap = snapshot_dir(key, root)
    manifest_path = snap / "manifest.json"
    scoring_path = snap / "scoring" / "database" / "utils" / "scoring.py"
    strategy_path = snap / "scoring" / "strategy_config.py"
    sector_path = snap / "scoring" / "database" / "utils" / "sector_breadth_wave.py"

    if not manifest_path.exists():
        raise RuntimeError(f"{key} has no algorithm silo at {snap}")
    if not scoring_path.exists():
        raise RuntimeError(f"{key} has no copied scoring.py in its silo")
    if not strategy_path.exists():
        raise RuntimeError(f"{key} has no strategy_config.py; older snapshot cannot be runtime-loaded")

    manifest = read_json(manifest_path)
    stable_prefix = f"_algorithm_silo_{key}"
    strategy_module = _load_module_from_path(f"{stable_prefix}_strategy_config", strategy_path)
    temporary = {"strategy_config": strategy_module}
    if sector_path.exists():
        sector_module = _load_module_from_path(f"{stable_prefix}_sector_breadth_wave", sector_path)
        temporary["database.utils.sector_breadth_wave"] = sector_module

    scoring_module = _load_silo_module(
        stable_name=f"{stable_prefix}_scoring",
        canonical_name="database.utils.scoring",
        path=scoring_path,
        temporary_modules=temporary,
    )
    fn = getattr(scoring_module, "compute_overall_score", None)
    if fn is None:
        raise RuntimeError(f"{key} scoring.py does not define compute_overall_score")
    sig = inspect.signature(fn)
    accepted = {
        name
        for name, param in sig.parameters.items()
        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    }
    accepts_var_kwargs = any(
        param.kind == param.VAR_KEYWORD for param in sig.parameters.values()
    )
    engine = ScoringEngine(
        key=key,
        version_id=int(version.id),
        manifest=manifest,
        module=scoring_module,
        accepted_kwargs=frozenset(accepted),
        accepts_var_kwargs=accepts_var_kwargs,
    )
    _ENGINE_CACHE[key] = engine
    return engine


def runtime_loadable_production_versions(*, include_current: bool = False):
    from database.models.core import AlgorithmVersion

    current_version = AlgorithmVersion.get_or_create_current()
    versions = []
    seen_ids = set()
    for version in AlgorithmVersion.production_versions():
        version_id = int(version.id)
        if version_id in seen_ids:
            continue
        seen_ids.add(version_id)
        if not include_current and version_id == int(current_version.id):
            continue
        try:
            load_scoring_engine(version)
        except Exception:
            continue
        versions.append(version)
    return versions


def resolve_versions(tokens: list[str]):
    from assess_scores import resolve_algorithm_version

    versions = []
    seen_ids = set()
    for token in tokens:
        if _is_all_runtime_versions_token(token):
            expanded = runtime_loadable_production_versions()
        else:
            expanded = [resolve_algorithm_version(token)]
        for version in expanded:
            version_id = int(version.id)
            if version_id in seen_ids:
                continue
            seen_ids.add(version_id)
            versions.append(version)
    return versions


def validate_runtime_versions(version_tokens: list[str]) -> list[str]:
    from database.models.core import AlgorithmVersion

    tokens = parse_version_tokens(version_tokens)
    versions = resolve_versions(tokens)
    current_version = AlgorithmVersion.get_or_create_current()
    usable = []
    for version in versions:
        key = f"v{int(version.id)}"
        if int(version.id) != int(current_version.id):
            load_scoring_engine(version)
        usable.append(key)
    return usable


def _copy_primary_score_fields(primary, target) -> None:
    fields = (
        "bb",
        "trend",
        "volume",
        "rsi",
        "macd",
        "stoch",
        "ma20",
        "high_30",
        "high_60",
        "high_90",
        "high_30_days",
        "high_60_days",
        "high_90_days",
        "high_30_biggest_drop",
        "high_60_biggest_drop",
        "high_90_biggest_drop",
        "technical_alignment",
        "daily_change",
        "price",
        "price_target_growth",
        "next_earnings",
        "price_target",
        "growth_score",
        "volume_signal",
        "volume_magnitude",
        "pct_from_ema50",
        "pct_from_ema200",
        "bb_position",
        "score_velocity_7d",
        "regime_composite",
        "regime_multiplier",
    )
    for field in fields:
        setattr(target, field, getattr(primary, field))
    target.updated_at = datetime.now()


def _append_runtime_metadata(score, engine: ScoringEngine) -> None:
    try:
        info = json.loads(score.weight_info or "{}")
        if not isinstance(info, dict):
            info = {"raw_weight_info": info}
    except Exception:
        info = {}
    info.setdefault(
        "runtime_silo_score",
        {
            "key": engine.key,
            "version_id": engine.version_id,
            "source_commit": engine.manifest.get("source", {}).get("commit_short"),
        },
    )
    score.weight_info = json.dumps(info)


def score_primary_for_engines(
    primary_score,
    engines: list[ScoringEngine],
    *,
    score_args: tuple[Any, ...] | None = None,
    score_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from database.models import core as core_models
    from database.models.core import AlgorithmVersion, Score

    current_version = AlgorithmVersion.get_or_create_current()
    summary = {"written": [], "skipped": [], "errors": []}
    for engine in engines:
        key = engine.key
        if int(engine.version_id) == int(current_version.id):
            summary["skipped"].append({"key": key, "reason": "current version already scored"})
            continue
        try:
            alt_score, _ = Score.get_or_create(
                symbol=primary_score.symbol_id,
                date=primary_score.date,
                version=engine.version_id,
            )
            _copy_primary_score_fields(primary_score, alt_score)
            if score_args is not None:
                overall, weight_info, vol_update = engine.compute_overall_score(
                    *score_args,
                    **(score_kwargs or {}),
                )
                alt_score.weight_info = json.dumps(weight_info)
                if vol_update and (
                    vol_update["volume_signal"] != "NEUTRAL"
                    or not alt_score.volume_signal
                    or alt_score.volume_signal == "NEUTRAL"
                ):
                    alt_score.volume = vol_update["volume"]
                    alt_score.volume_signal = vol_update["volume_signal"]
                    alt_score.volume_magnitude = vol_update["volume_magnitude"]
            else:
                original = core_models.compute_overall_score
                try:
                    core_models.compute_overall_score = engine.compute_overall_score
                    regime_multiplier = (
                        float(primary_score.regime_multiplier)
                        if primary_score.regime_multiplier is not None
                        else None
                    )
                    overall = alt_score.calculate_overall_score(regime_multiplier=regime_multiplier)
                finally:
                    core_models.compute_overall_score = original
            if overall is None:
                alt_score.delete_instance()
                summary["skipped"].append({"key": key, "reason": "overall returned None"})
                continue
            alt_score.overall = overall
            alt_score.updated_at = datetime.now()
            _append_runtime_metadata(alt_score, engine)
            alt_score.save()
            summary["written"].append({"key": key, "date": primary_score.date.isoformat()})
        except Exception as exc:
            summary["errors"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
    return summary


def score_primary_for_versions(primary_score, versions) -> dict[str, Any]:
    from database.models.core import AlgorithmVersion

    current_version = AlgorithmVersion.get_or_create_current()
    summary = {"written": [], "skipped": [], "errors": []}
    engines = []
    for version in versions:
        key = f"v{int(version.id)}"
        if int(version.id) == int(current_version.id):
            summary["skipped"].append({"key": key, "reason": "current version already scored"})
            continue
        try:
            engines.append(load_scoring_engine(version))
        except Exception as exc:
            summary["errors"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
    engine_summary = score_primary_for_engines(primary_score, engines)
    for key in summary:
        summary[key].extend(engine_summary.get(key, []))
    return summary


def score_latest_for_versions(symbol: str, version_tokens: list[str]) -> dict[str, Any]:
    from database.models.core import AlgorithmVersion, Score
    from database.models.technical import Indicator
    from peewee import fn

    tokens = parse_version_tokens(version_tokens)
    if not tokens:
        return {"written": [], "skipped": [], "errors": []}
    versions = resolve_versions(tokens)
    current_version = AlgorithmVersion.get_or_create_current()
    target_date = (
        Indicator
        .select(fn.MAX(Indicator.date))
        .where(Indicator.symbol == symbol)
        .scalar()
    )
    if not target_date:
        return {"written": [], "skipped": [{"reason": "no indicator date"}], "errors": []}
    primary = Score.get_or_none(
        (Score.symbol == symbol)
        & (Score.date == target_date)
        & (Score.version == current_version)
    )
    if primary is None or primary.overall is None:
        return {
            "written": [],
            "skipped": [{"reason": "current version score missing", "date": str(target_date)}],
            "errors": [],
        }
    return score_primary_for_versions(primary, versions)
