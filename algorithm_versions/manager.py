from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

# Files copied into each scoring silo. This list is deliberately broader than
# compute_overall_score: context builders and score-row callers affect whether a
# copied scoring function is truly reproducible.
SCORING_SOURCE_FILES: tuple[str, ...] = (
    "ALGORITHM_VERSION",
    "strategy_config.py",
    "database/utils/scoring.py",
    "database/utils/sector_breadth_wave.py",
    "database/models/core.py",
    "database/models/technical.py",
    "database/project_root.py",
    "market_breadth.py",
    "market_regime.py",
    "volume_amplifier.py",
    "simulator.py",
    "recalculate_scores.py",
)

PORTFOLIO_SOURCE_FILES: tuple[str, ...] = (
    "strategy_config.py",
    "mechanism_registry.py",
    "monte_carlo.py",
    "monte_carlo_15dte.py",
    "backtest_cascade.py",
    "backtest_cascade_15dte.py",
    "api.py",
    "trader.py",
)

PORTFOLIO_CONFIG_SCHEMA_VERSION = 1

STRATEGY_KEYS: tuple[str, ...] = ("30dte", "15dte")

LEGACY_PORTFOLIO_STRATEGY_SOURCES: dict[str, tuple[str, ...]] = {
    "30dte": (
        "monte_carlo.py",
        "backtest_cascade.py",
        "trader.py",
        "api.py",
    ),
    "15dte": (
        "monte_carlo_15dte.py",
        "backtest_cascade_15dte.py",
    ),
}

PORTFOLIO_CONSTANT_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "DELTA",
        "DTE",
        "INITIAL_CAPITAL",
        "MIN_VOL_BARS",
        "N_ITER",
        "RANDOM_SEED",
        "STARTING_CASH",
        "WINDOWS",
    }
)

PORTFOLIO_CONSTANT_PREFIXES: tuple[str, ...] = (
    "ALLOC_",
    "BREADTH_",
    "COLLAPSE_",
    "CT_",
    "CTSL_",
    "DD_",
    "DEAD_HOLD_",
    "EARN_",
    "F3F_",
    "HARD_SELL_",
    "HOLD_",
    "MAX_POSITIONS",
    "NET_",
    "OVERFLOW_",
    "PREMIUM_",
    "PRIMARY_",
    "PUT_",
    "REGIME_",
    "SAW_",
    "SL_",
    "SLIP_",
    "TIER_",
    "TP_",
    "VOL_",
    "WEAK_WEEKLY_",
)

SNAPSHOT_STATUSES = (
    "candidate",
    "ship_candidate",
    "shipped",
    "legacy",
    "legacy_metadata_only",
)


def project_root() -> Path:
    from database.project_root import get_trader_project_root

    return get_trader_project_root()


def silo_root(root: Path | None = None) -> Path:
    return (root or project_root()) / "algorithm_versions"


def cache_root(root: Path | None = None) -> Path:
    return (root or project_root()) / ".cache" / "algorithm_versions"


def registry_path(root: Path | None = None) -> Path:
    return silo_root(root) / "registry.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(data), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(data: Any) -> str:
    return hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()


def run_git(root: Path, *args: str, allow_fail: bool = False) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).strip()
    except Exception:
        if allow_fail:
            return ""
        raise


def run_git_bytes(root: Path, *args: str, allow_fail: bool = False) -> bytes:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        if allow_fail:
            return b""
        raise


def git_ref_file_bytes(root: Path, ref: str, rel_path: str) -> bytes | None:
    data = run_git_bytes(root, "show", f"{ref}:{rel_path}", allow_fail=True)
    return data if data else None


def resolve_commit(root: Path, ref: str | None) -> str | None:
    if not ref:
        return None
    commit = run_git(root, "rev-parse", f"{ref}^{{commit}}", allow_fail=True)
    return commit or None


def json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def normalize_version_key(value: str | int) -> str:
    s = str(value).strip()
    if not s:
        raise ValueError("empty version key")
    if s.startswith("v"):
        return s
    if s.isdigit():
        return f"v{s}"
    return s


def is_portfolio_constant_name(name: str) -> bool:
    return (
        name in PORTFOLIO_CONSTANT_EXACT_NAMES
        or name.startswith(PORTFOLIO_CONSTANT_PREFIXES)
    )


def load_registry(root: Path | None = None) -> dict[str, Any]:
    path = registry_path(root)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": None,
            "snapshots": {},
            "portfolio_snapshots": {},
            "followups": [],
        }
    return read_json(path)


def save_registry(registry: dict[str, Any], root: Path | None = None) -> None:
    registry["schema_version"] = SCHEMA_VERSION
    registry["updated_at"] = utc_now()
    write_json(registry_path(root), registry)


def git_state(root: Path) -> dict[str, Any]:
    status = run_git(root, "status", "--short", allow_fail=True).splitlines()
    return {
        "head": run_git(root, "rev-parse", "HEAD", allow_fail=True),
        "head_short": run_git(root, "rev-parse", "--short", "HEAD", allow_fail=True),
        "branch": run_git(root, "branch", "--show-current", allow_fail=True),
        "dirty": bool(status),
        "dirty_status": status,
    }


def active_algorithm_version(create_current: bool = False):
    from database.models.core import AlgorithmVersion

    if create_current:
        return AlgorithmVersion.get_or_create_current()
    return AlgorithmVersion.get_active_scores_version()


def resolve_algorithm_version(token: str | None, create_current: bool = False):
    if token:
        from assess_scores import resolve_algorithm_version

        return resolve_algorithm_version(token)
    return active_algorithm_version(create_current=create_current)


def algorithm_version_payload(version) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": int(version.id),
        "label": version.production_label,
        "git_commit": version.git_commit,
        "git_message": version.git_message,
        "notes": version.notes,
        "created_at": json_safe(version.created_at),
    }


def scoring_config_payload() -> dict[str, Any]:
    import strategy_config

    return {
        "CALIBRATION_CUTOFF_DATE": strategy_config.CALIBRATION_CUTOFF_DATE,
        "SCORING": json_safe(strategy_config.SCORING),
    }


def portfolio_payload() -> dict[str, Any]:
    import mechanism_registry
    import portfolio_profiles
    import strategy_config

    registry_specs = [
        json_safe(spec) for spec in getattr(mechanism_registry, "REGISTRY", ())
    ]
    return {
        "strategies": {
            "30dte": strategy_config.to_json_dict(strategy_config.STRATEGY_30DTE),
            "15dte": strategy_config.to_json_dict(strategy_config.STRATEGY_15DTE),
        },
        "assess_combos": strategy_config.assess_combos(),
        "mechanism_registry": registry_specs,
        "portfolio_profiles": portfolio_profiles.payload(project_root()),
    }


def copy_snapshot_files(root: Path, dest: Path, files: tuple[str, ...]) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    for rel in files:
        src = root / rel
        if not src.exists():
            copied.append({"source": rel, "missing": True})
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(
            {
                "source": rel,
                "path": str(dst.relative_to(dest).as_posix()),
                "sha256": sha256_file(dst),
                "bytes": dst.stat().st_size,
            }
        )
    return {
        "files": copied,
        "fingerprint": sha256_json(
            {row["source"]: row.get("sha256") for row in copied if not row.get("missing")}
        ),
    }


def copy_snapshot_files_from_git_ref(
    root: Path,
    dest: Path,
    files: tuple[str, ...],
    ref: str,
) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    for rel in files:
        data = git_ref_file_bytes(root, ref, rel)
        if data is None:
            copied.append({"source": rel, "missing": True})
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        copied.append(
            {
                "source": rel,
                "path": str(dst.relative_to(dest).as_posix()),
                "sha256": sha256_file(dst),
                "bytes": dst.stat().st_size,
            }
        )
    return {
        "files": copied,
        "fingerprint": sha256_json(
            {row["source"]: row.get("sha256") for row in copied if not row.get("missing")}
        ),
    }


def payloads_from_git_ref(root: Path, ref: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="trader-algorithm-silo-") as tmp:
        tmp_root = Path(tmp)
        for rel in ("strategy_config.py", "mechanism_registry.py"):
            data = git_ref_file_bytes(root, ref, rel)
            if data is not None:
                (tmp_root / rel).write_bytes(data)
        if not (tmp_root / "strategy_config.py").exists():
            return {
                "scoring_config": {
                    "CALIBRATION_CUTOFF_DATE": None,
                    "SCORING": None,
                    "extraction_error": "strategy_config.py is not present at this git ref",
                },
                "portfolio": {
                    "strategies": {"30dte": None, "15dte": None},
                    "assess_combos": None,
                    "mechanism_registry": [],
                    "extraction_error": "strategy_config.py is not present at this git ref",
                },
            }

        script = r'''
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path

def json_safe(value):
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)

import strategy_config

def config_json(name):
    value = getattr(strategy_config, name, None)
    if value is None:
        return None
    if hasattr(strategy_config, "to_json_dict"):
        return json_safe(strategy_config.to_json_dict(value))
    return json_safe(value)

scoring_config = {
    "CALIBRATION_CUTOFF_DATE": getattr(strategy_config, "CALIBRATION_CUTOFF_DATE", None),
    "SCORING": json_safe(getattr(strategy_config, "SCORING", None)),
}

portfolio = {
    "strategies": {
        "30dte": config_json("STRATEGY_30DTE"),
        "15dte": config_json("STRATEGY_15DTE"),
    },
    "assess_combos": (
        json_safe(strategy_config.assess_combos())
        if hasattr(strategy_config, "assess_combos")
        else None
    ),
    "mechanism_registry": [],
}

try:
    import mechanism_registry
    portfolio["mechanism_registry"] = [
        json_safe(spec) for spec in getattr(mechanism_registry, "REGISTRY", ())
    ]
except Exception as exc:
    portfolio["mechanism_registry_error"] = repr(exc)

print(json.dumps({"scoring_config": scoring_config, "portfolio": portfolio}, sort_keys=True))
'''
        try:
            out = subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=str(tmp_root),
                text=True,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            return json.loads(out)
        except Exception as exc:
            return {
                "scoring_config": {
                    "CALIBRATION_CUTOFF_DATE": None,
                    "SCORING": None,
                    "extraction_error": repr(exc),
                },
                "portfolio": {
                    "strategies": {"30dte": None, "15dte": None},
                    "assess_combos": None,
                    "mechanism_registry": [],
                    "extraction_error": repr(exc),
                },
            }


class StaticEvalError(ValueError):
    pass


def _static_eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise StaticEvalError(f"unresolved name {node.id}")
    if isinstance(node, ast.UnaryOp):
        value = _static_eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Not):
            return not value
    if isinstance(node, ast.BinOp):
        left = _static_eval(node.left, env)
        right = _static_eval(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
    if isinstance(node, ast.Dict):
        return {
            json_safe(_static_eval(k, env)): json_safe(_static_eval(v, env))
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    if isinstance(node, ast.List):
        return [json_safe(_static_eval(v, env)) for v in node.elts]
    if isinstance(node, ast.Tuple):
        return [json_safe(_static_eval(v, env)) for v in node.elts]
    raise StaticEvalError(f"unsupported expression {type(node).__name__}")


def _normalize_static_value(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 12)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {k: _normalize_static_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_static_value(v) for v in value]
    return value


def _assignment_targets(stmt: ast.stmt) -> tuple[list[ast.expr], ast.AST] | None:
    if isinstance(stmt, ast.Assign):
        return list(stmt.targets), stmt.value
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target], stmt.value
    return None


def extract_static_uppercase_constants(path: Path) -> dict[str, Any]:
    """Extract top-level uppercase constants that can be statically evaluated.

    This is intentionally conservative: it captures the shipped magic-number
    knobs from old portfolio engines without importing old modules against the
    current database/runtime environment.
    """
    rel = path.name
    if not path.exists():
        return {
            "source": rel,
            "missing": True,
            "constants": {},
            "unsupported": [],
        }
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        return {
            "source": rel,
            "missing": False,
            "parse_error": repr(exc),
            "constants": {},
            "unsupported": [],
        }

    constants: dict[str, Any] = {}
    unsupported: list[dict[str, Any]] = []
    env: dict[str, Any] = {}
    for stmt in tree.body:
        parsed = _assignment_targets(stmt)
        if parsed is None:
            continue
        targets, value_node = parsed
        if value_node is None:
            continue
        for target in targets:
            if (
                not isinstance(target, ast.Name)
                or not target.id.isupper()
                or not is_portfolio_constant_name(target.id)
            ):
                continue
            try:
                value = json_safe(_normalize_static_value(_static_eval(value_node, env)))
            except Exception as exc:
                unsupported.append(
                    {
                        "name": target.id,
                        "line": getattr(stmt, "lineno", None),
                        "reason": str(exc),
                    }
                )
                continue
            constants[target.id] = value
            env[target.id] = value

    return {
        "source": rel,
        "missing": False,
        "constants": constants,
        "unsupported": unsupported,
    }


def _structured_strategy_config(
    *,
    strategy_key: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "available",
        "source_mode": "strategy_config",
        "strategy_key": strategy_key,
        "fields": json_safe(fields),
        "field_sources": {
            name: f"portfolio_snapshot.json:strategies.{strategy_key}"
            for name in sorted(fields)
        },
        "source_files": ["portfolio_snapshot.json"],
        "field_count": len(fields),
        "unsupported": [],
        "conflicts": [],
    }


def _legacy_strategy_config(snapshot_dir: Path, strategy_key: str) -> dict[str, Any]:
    source_rows: list[dict[str, Any]] = []
    fields: dict[str, Any] = {}
    field_sources: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    sources = LEGACY_PORTFOLIO_STRATEGY_SOURCES[strategy_key]
    source_root = snapshot_dir / "portfolio_sources"
    for rel in sources:
        path = source_root / rel
        row = extract_static_uppercase_constants(path)
        row["source"] = f"portfolio_sources/{rel}"
        source_rows.append(row)
        for name, value in row.get("constants", {}).items():
            if name not in fields:
                fields[name] = value
                field_sources[name] = row["source"]
            elif fields[name] != value:
                conflicts.append(
                    {
                        "name": name,
                        "chosen": fields[name],
                        "chosen_source": field_sources[name],
                        "ignored": value,
                        "ignored_source": row["source"],
                    }
                )

    if not fields:
        return {
            "status": "unavailable",
            "source_mode": "legacy_source_constants",
            "strategy_key": strategy_key,
            "fields": {},
            "field_sources": {},
            "source_files": [
                row["source"] for row in source_rows if not row.get("missing")
            ],
            "field_count": 0,
            "unsupported": [
                {"source": row["source"], **item}
                for row in source_rows
                for item in row.get("unsupported", [])
            ],
            "conflicts": conflicts,
            "reason": "No statically evaluable legacy portfolio constants found for this strategy.",
        }

    return {
        "status": "available",
        "source_mode": "legacy_source_constants",
        "strategy_key": strategy_key,
        "fields": fields,
        "field_sources": field_sources,
        "source_files": [
            row["source"] for row in source_rows if row.get("constants")
        ],
        "field_count": len(fields),
        "unsupported": [
            {"source": row["source"], **item}
            for row in source_rows
            for item in row.get("unsupported", [])
        ],
        "conflicts": conflicts,
        "source_constants": {
            row["source"]: row.get("constants", {})
            for row in source_rows
            if row.get("constants")
        },
    }


def build_portfolio_config(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    snapshot_path = snapshot_dir / "portfolio_snapshot.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    portfolio_snapshot = read_json(snapshot_path) if snapshot_path.exists() else {}
    snapshot_strategies = portfolio_snapshot.get("strategies") or {}

    strategies: dict[str, Any] = {}
    for key in STRATEGY_KEYS:
        raw = snapshot_strategies.get(key)
        if isinstance(raw, dict):
            strategies[key] = _structured_strategy_config(
                strategy_key=key,
                fields=raw,
            )
        else:
            strategies[key] = _legacy_strategy_config(snapshot_dir, key)

    return {
        "schema_version": PORTFOLIO_CONFIG_SCHEMA_VERSION,
        "silo_key": manifest.get("silo_key") or snapshot_dir.name,
        "algorithm_version": manifest.get("algorithm_version"),
        "source": {
            "manifest": "manifest.json" if manifest_path.exists() else None,
            "portfolio_snapshot": "portfolio_snapshot.json"
            if snapshot_path.exists()
            else None,
            "portfolio_snapshot_sha256": sha256_file(snapshot_path)
            if snapshot_path.exists()
            else None,
            "portfolio_source_files_fingerprint": (
                manifest.get("fingerprints", {}).get("portfolio_source_files")
            ),
        },
        "strategies": strategies,
        "assess_combos": portfolio_snapshot.get("assess_combos"),
        "mechanism_registry": portfolio_snapshot.get("mechanism_registry") or [],
        "portfolio_snapshot_extraction_error": portfolio_snapshot.get("extraction_error"),
    }


def write_portfolio_config(snapshot_dir: Path, *, update_manifest: bool = True) -> Path:
    config = build_portfolio_config(snapshot_dir)
    path = snapshot_dir / "portfolio_config.json"
    write_json(path, config)
    if update_manifest and (snapshot_dir / "manifest.json").exists():
        manifest = read_json(snapshot_dir / "manifest.json")
        manifest.setdefault("paths", {})["portfolio_config"] = "portfolio_config.json"
        manifest.setdefault("fingerprints", {})["portfolio_config"] = sha256_json(config)
        write_json(snapshot_dir / "manifest.json", manifest)
    return path


def snapshot_key_for(version, explicit_key: str | None) -> str:
    if explicit_key:
        return normalize_version_key(explicit_key)
    if version is not None:
        return f"v{int(version.id)}"
    return "candidate-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def cache_path_for(key: str, root: Path | None = None) -> Path:
    return cache_root(root) / normalize_version_key(key)


def snapshot_current(
    *,
    version_token: str | None = None,
    key: str | None = None,
    label: str | None = None,
    status: str = "candidate",
    force: bool = False,
    create_current: bool = False,
) -> Path:
    root = project_root()
    version = resolve_algorithm_version(version_token, create_current=create_current)
    snap_key = snapshot_key_for(version, key)
    dest = silo_root(root) / snap_key
    if dest.exists() and not force:
        raise SystemExit(f"{dest} already exists; pass --force to replace it")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    scoring = copy_snapshot_files(root, dest / "scoring", SCORING_SOURCE_FILES)
    portfolio_sources = copy_snapshot_files(root, dest / "portfolio_sources", PORTFOLIO_SOURCE_FILES)
    scoring_config = scoring_config_payload()
    portfolio = portfolio_payload()
    source_state = git_state(root)
    cache_dir = cache_path_for(snap_key, root)
    cache_dir.mkdir(parents=True, exist_ok=True)

    write_json(dest / "scoring_config.json", scoring_config)
    write_json(dest / "portfolio_snapshot.json", portfolio)
    evidence_manifest = {
        "schema_version": SCHEMA_VERSION,
        "cache_root": str(cache_dir.relative_to(root).as_posix()),
        "policy": "Large experiment datasets stay in .cache/algorithm_versions/<key>; tracked manifests store paths, commands, and checksums.",
        "artifacts": [],
        "backfill_status": "not_started",
    }
    write_json(dest / "evidence_manifest.json", evidence_manifest)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "silo_key": snap_key,
        "status": status,
        "label": label,
        "captured_at": utc_now(),
        "algorithm_version": algorithm_version_payload(version),
        "source": {
            "mode": "working_tree",
            "algorithm_version_file": (root / "ALGORITHM_VERSION").read_text(encoding="utf-8").strip()
            if (root / "ALGORITHM_VERSION").exists()
            else None,
            **source_state,
        },
        "paths": {
            "root": str(dest.relative_to(root).as_posix()),
            "scoring": "scoring",
            "scoring_config": "scoring_config.json",
            "portfolio_snapshot": "portfolio_snapshot.json",
            "portfolio_sources": "portfolio_sources",
            "evidence_manifest": "evidence_manifest.json",
            "cache_root": str(cache_dir.relative_to(root).as_posix()),
        },
        "fingerprints": {
            "scoring_files": scoring["fingerprint"],
            "portfolio_source_files": portfolio_sources["fingerprint"],
            "scoring_config": sha256_json(scoring_config),
            "portfolio_snapshot": sha256_json(portfolio),
            "manifest_inputs": sha256_json(
                {
                    "scoring": scoring["fingerprint"],
                    "portfolio_sources": portfolio_sources["fingerprint"],
                    "scoring_config": scoring_config,
                    "portfolio": portfolio,
                    "algorithm_version": algorithm_version_payload(version),
                }
            ),
        },
        "files": {
            "scoring": scoring["files"],
            "portfolio_sources": portfolio_sources["files"],
        },
        "notes": [
            "This snapshot is additive infrastructure. Runtime scoring still imports the checkout code until the injected loader phase lands.",
            "Historical backfill is intentionally deferred because reconstructing old silos is resource-intensive during trading hours.",
        ],
    }
    write_json(dest / "manifest.json", manifest)
    write_portfolio_config(dest)
    manifest = read_json(dest / "manifest.json")

    registry = load_registry(root)
    registry.setdefault("snapshots", {})[snap_key] = {
        "path": str(dest.relative_to(root).as_posix()),
        "status": status,
        "label": label,
        "captured_at": manifest["captured_at"],
        "algorithm_version": manifest["algorithm_version"],
        "fingerprints": manifest["fingerprints"],
    }
    if "legacy_silo_backfill" not in {f.get("id") for f in registry.get("followups", [])}:
        registry.setdefault("followups", []).append(
            {
                "id": "legacy_silo_backfill",
                "status": "deferred",
                "reason": "Resource-intensive; run off-hours in reverse version order.",
            }
        )
    save_registry(registry, root)
    return dest


def snapshot_git_ref(
    *,
    version_token: str | None = None,
    git_ref: str | None = None,
    key: str | None = None,
    label: str | None = None,
    status: str = "shipped",
    force: bool = False,
    create_current: bool = False,
) -> Path:
    root = project_root()
    version = resolve_algorithm_version(version_token, create_current=create_current)
    ref = git_ref or (version.git_commit if version is not None else "HEAD")
    if not ref:
        raise SystemExit("No git ref was provided and the algorithm version has no git_commit")
    commit = resolve_commit(root, ref)
    if not commit:
        raise SystemExit(f"Git ref does not resolve to a commit: {ref}")
    commit_short = run_git(root, "rev-parse", "--short", commit)
    commit_subject = run_git(root, "log", "-1", "--format=%s", commit, allow_fail=True)
    commit_body = run_git(root, "log", "-1", "--format=%b", commit, allow_fail=True)
    snap_key = snapshot_key_for(version, key)
    dest = silo_root(root) / snap_key
    if dest.exists() and not force:
        raise SystemExit(f"{dest} already exists; pass --force to replace it")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    scoring = copy_snapshot_files_from_git_ref(root, dest / "scoring", SCORING_SOURCE_FILES, commit)
    portfolio_sources = copy_snapshot_files_from_git_ref(
        root, dest / "portfolio_sources", PORTFOLIO_SOURCE_FILES, commit
    )
    payloads = payloads_from_git_ref(root, commit)
    scoring_config = payloads["scoring_config"]
    portfolio = payloads["portfolio"]
    cache_dir = cache_path_for(snap_key, root)
    cache_dir.mkdir(parents=True, exist_ok=True)

    write_json(dest / "scoring_config.json", scoring_config)
    write_json(dest / "portfolio_snapshot.json", portfolio)
    evidence_manifest = {
        "schema_version": SCHEMA_VERSION,
        "cache_root": str(cache_dir.relative_to(root).as_posix()),
        "policy": "Large experiment datasets stay in .cache/algorithm_versions/<key>; tracked manifests store paths, commands, and checksums.",
        "artifacts": [],
        "backfill_status": "not_started",
    }
    write_json(dest / "evidence_manifest.json", evidence_manifest)

    av_payload = algorithm_version_payload(version)
    algorithm_version_file = git_ref_file_bytes(root, commit, "ALGORITHM_VERSION")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "silo_key": snap_key,
        "status": status,
        "label": label,
        "captured_at": utc_now(),
        "algorithm_version": av_payload,
        "source": {
            "mode": "git_ref",
            "requested_ref": ref,
            "commit": commit,
            "commit_short": commit_short,
            "commit_subject": commit_subject,
            "commit_body": commit_body,
            "algorithm_version_file": (
                algorithm_version_file.decode("utf-8", errors="replace").strip()
                if algorithm_version_file is not None
                else None
            ),
        },
        "paths": {
            "root": str(dest.relative_to(root).as_posix()),
            "scoring": "scoring",
            "scoring_config": "scoring_config.json",
            "portfolio_snapshot": "portfolio_snapshot.json",
            "portfolio_sources": "portfolio_sources",
            "evidence_manifest": "evidence_manifest.json",
            "cache_root": str(cache_dir.relative_to(root).as_posix()),
        },
        "fingerprints": {
            "scoring_files": scoring["fingerprint"],
            "portfolio_source_files": portfolio_sources["fingerprint"],
            "scoring_config": sha256_json(scoring_config),
            "portfolio_snapshot": sha256_json(portfolio),
            "manifest_inputs": sha256_json(
                {
                    "scoring": scoring["fingerprint"],
                    "portfolio_sources": portfolio_sources["fingerprint"],
                    "scoring_config": scoring_config,
                    "portfolio": portfolio,
                    "algorithm_version": av_payload,
                    "commit": commit,
                }
            ),
        },
        "files": {
            "scoring": scoring["files"],
            "portfolio_sources": portfolio_sources["files"],
        },
        "notes": [
            "This snapshot was copied from the git commit recorded by AlgorithmVersion rather than from the current worktree.",
            "Runtime scoring still imports the checkout code until the injected loader phase lands.",
        ],
    }
    write_json(dest / "manifest.json", manifest)
    write_portfolio_config(dest)
    manifest = read_json(dest / "manifest.json")

    registry = load_registry(root)
    registry.setdefault("snapshots", {})[snap_key] = {
        "path": str(dest.relative_to(root).as_posix()),
        "status": status,
        "label": label,
        "captured_at": manifest["captured_at"],
        "algorithm_version": av_payload,
        "source": manifest["source"],
        "fingerprints": manifest["fingerprints"],
    }
    save_registry(registry, root)
    return dest


def classify_snapshot_message(message: str | None, git_commit: str | None) -> dict[str, Any]:
    text = (message or "").strip()
    lower = text.lower()
    if not git_commit or git_commit.lower() == "baseline":
        return {
            "category": "legacy_metadata_only",
            "scoring_algorithm_snapshot": False,
            "reason": "No resolvable git commit is recorded for this version.",
        }
    if "revert" in lower:
        return {
            "category": "scoring_revert",
            "scoring_algorithm_snapshot": True,
            "reason": "Commit message indicates a scoring revert.",
        }
    scoring_terms = (
        "scoring",
        "score",
        "algo",
        "algorithm",
        "dampener",
        "boost",
        "gate",
        "floor",
        "regime",
        "breadth",
        "volume",
        "weekly",
        "trend",
        "macd",
        "stoch",
        "earn",
        "entry_filter",
        "market wave",
        "mcap",
        "ichimoku",
        "ema50",
        "continuation",
        "counter-trend",
    )
    context_terms = (
        "effective_date",
        "earnings semantics",
        "temporal echo",
        "recalc priors",
        "production breadth universe",
    )
    if any(term in lower for term in scoring_terms):
        return {
            "category": "scoring_algorithm",
            "scoring_algorithm_snapshot": True,
            "reason": "Commit message names score-stage or score-context behavior.",
        }
    if any(term in lower for term in context_terms):
        return {
            "category": "scoring_context",
            "scoring_algorithm_snapshot": True,
            "reason": "Commit message names score inputs or temporal score context.",
        }
    if "dashboard" in lower or "ui" in lower:
        return {
            "category": "mixed_or_ui",
            "scoring_algorithm_snapshot": False,
            "reason": "Commit message does not clearly identify a scoring algorithm change.",
        }
    return {
        "category": "db_linked_snapshot",
        "scoring_algorithm_snapshot": True,
        "reason": "AlgorithmVersion row points at this commit; message is not specific.",
    }


def write_legacy_metadata_snapshot(
    *,
    version,
    key: str,
    reason: str,
    force: bool,
) -> Path:
    root = project_root()
    dest = silo_root(root) / key
    if dest.exists() and not force:
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    av_payload = algorithm_version_payload(version)
    scoring_config = {
        "CALIBRATION_CUTOFF_DATE": None,
        "SCORING": None,
        "extraction_error": reason,
    }
    portfolio = {
        "strategies": {"30dte": None, "15dte": None},
        "assess_combos": None,
        "mechanism_registry": [],
        "extraction_error": reason,
    }
    cache_dir = cache_path_for(key, root)
    cache_dir.mkdir(parents=True, exist_ok=True)

    write_json(dest / "scoring_config.json", scoring_config)
    write_json(dest / "portfolio_snapshot.json", portfolio)
    write_json(
        dest / "evidence_manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "cache_root": str(cache_dir.relative_to(root).as_posix()),
            "policy": "Large experiment datasets stay in .cache/algorithm_versions/<key>; tracked manifests store paths, commands, and checksums.",
            "artifacts": [],
            "backfill_status": "metadata_only",
            "reason": reason,
        },
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "silo_key": key,
        "status": "legacy_metadata_only",
        "label": av_payload.get("label") if av_payload else key,
        "captured_at": utc_now(),
        "algorithm_version": av_payload,
        "source": {
            "mode": "unresolved_git_ref",
            "requested_ref": av_payload.get("git_commit") if av_payload else None,
            "reason": reason,
        },
        "paths": {
            "root": str(dest.relative_to(root).as_posix()),
            "scoring": "scoring",
            "scoring_config": "scoring_config.json",
            "portfolio_snapshot": "portfolio_snapshot.json",
            "portfolio_sources": "portfolio_sources",
            "evidence_manifest": "evidence_manifest.json",
            "cache_root": str(cache_dir.relative_to(root).as_posix()),
        },
        "fingerprints": {
            "scoring_files": sha256_json({}),
            "portfolio_source_files": sha256_json({}),
            "scoring_config": sha256_json(scoring_config),
            "portfolio_snapshot": sha256_json(portfolio),
            "manifest_inputs": sha256_json(
                {
                    "algorithm_version": av_payload,
                    "reason": reason,
                }
            ),
        },
        "files": {"scoring": [], "portfolio_sources": []},
        "classification": classify_snapshot_message(
            av_payload.get("git_message") if av_payload else None,
            av_payload.get("git_commit") if av_payload else None,
        ),
        "notes": [
            "This version could not be reconstructed from git and is recorded as metadata only.",
        ],
    }
    write_json(dest / "manifest.json", manifest)
    write_portfolio_config(dest)
    manifest = read_json(dest / "manifest.json")

    registry = load_registry(root)
    registry.setdefault("snapshots", {})[key] = {
        "path": str(dest.relative_to(root).as_posix()),
        "status": "legacy_metadata_only",
        "label": manifest["label"],
        "captured_at": manifest["captured_at"],
        "algorithm_version": av_payload,
        "source": manifest["source"],
        "fingerprints": manifest["fingerprints"],
        "classification": manifest["classification"],
    }
    save_registry(registry, root)
    return dest


def git_diff_summary(root: Path, left_commit: str, right_commit: str) -> dict[str, Any]:
    paths = sorted(set(SCORING_SOURCE_FILES + PORTFOLIO_SOURCE_FILES))
    name_status = run_git(
        root,
        "diff",
        "--name-status",
        f"{left_commit}..{right_commit}",
        "--",
        *paths,
        allow_fail=True,
    )
    stat = run_git(
        root,
        "diff",
        "--stat",
        f"{left_commit}..{right_commit}",
        "--",
        *paths,
        allow_fail=True,
    )
    rows = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append({"status": parts[0], "path": parts[-1]})
    return {
        "range": f"{left_commit[:8]}..{right_commit[:8]}",
        "name_status": rows,
        "stat": stat.splitlines(),
        "changed_paths": [row["path"] for row in rows],
    }


def snapshot_versions_from_git_history(*, force: bool = False) -> dict[str, Any]:
    from database.models.core import AlgorithmVersion

    root = project_root()
    rows = list(AlgorithmVersion.select().order_by(AlgorithmVersion.id.desc()))
    results: list[dict[str, Any]] = []
    for version in rows:
        key = f"v{int(version.id)}"
        ref = version.git_commit
        commit = resolve_commit(root, ref)
        if not commit:
            path = write_legacy_metadata_snapshot(
                version=version,
                key=key,
                reason=f"git_commit does not resolve to a commit: {ref}",
                force=force,
            )
            results.append({"key": key, "status": "legacy_metadata_only", "path": str(path)})
            continue
        path = snapshot_git_ref(
            version_token=key,
            key=key,
            status="shipped",
            label=version.production_label,
            force=force,
        )
        manifest = read_json(path / "manifest.json")
        message = (
            (version.git_message or "").strip()
            or manifest.get("source", {}).get("commit_subject")
        )
        manifest["classification"] = classify_snapshot_message(message, version.git_commit)
        write_json(path / "manifest.json", manifest)

        registry = load_registry(root)
        registry.setdefault("snapshots", {}).setdefault(key, {})["classification"] = manifest["classification"]
        save_registry(registry, root)
        results.append({"key": key, "status": "shipped", "path": str(path)})

    build_git_snapshot_index()
    return {"captured": results, "count": len(results)}


def build_git_snapshot_index() -> dict[str, Any]:
    from database.models.core import AlgorithmVersion

    root = project_root()
    rows = list(AlgorithmVersion.select().order_by(AlgorithmVersion.id.asc()))
    entries: list[dict[str, Any]] = []
    previous_resolved: dict[str, Any] | None = None

    for version in rows:
        key = f"v{int(version.id)}"
        manifest_path = snapshot_dir(key, root) / "manifest.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        source = manifest.get("source", {})
        commit = source.get("commit") or resolve_commit(root, version.git_commit)
        commit_subject = source.get("commit_subject") or run_git(
            root, "log", "-1", "--format=%s", commit, allow_fail=True
        ) if commit else ""
        message = (version.git_message or "").strip() or commit_subject
        classification = manifest.get("classification") or classify_snapshot_message(
            message, version.git_commit
        )
        entry = {
            "key": key,
            "version_id": int(version.id),
            "label": version.production_label,
            "git_commit": version.git_commit,
            "resolved_commit": commit,
            "commit_subject": commit_subject,
            "algorithm_version_message": version.git_message,
            "status": manifest.get("status", "missing"),
            "path": str(manifest_path.parent.relative_to(root).as_posix())
            if manifest_path.exists()
            else None,
            "classification": classification,
            "previous_resolved_key": previous_resolved["key"] if previous_resolved else None,
        }
        if commit and previous_resolved and previous_resolved.get("commit") != commit:
            diff = git_diff_summary(root, previous_resolved["commit"], commit)
            entry["diff_from_previous"] = diff
            write_json(manifest_path.parent / "diff_from_previous.json", diff)
        elif commit and previous_resolved and previous_resolved.get("commit") == commit:
            diff = {
                "range": f"{commit[:8]}..{commit[:8]}",
                "name_status": [],
                "stat": [],
                "changed_paths": [],
                "note": "Same resolved git commit as previous AlgorithmVersion row.",
            }
            entry["diff_from_previous"] = diff
            write_json(manifest_path.parent / "diff_from_previous.json", diff)
        else:
            entry["diff_from_previous"] = None

        if manifest_path.exists():
            manifest["classification"] = classification
            manifest["previous_resolved_key"] = entry["previous_resolved_key"]
            write_json(manifest_path, manifest)

        entries.append(entry)
        if commit:
            previous_resolved = {"key": key, "commit": commit}

    index = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now(),
        "sort": "version_id_ascending_for_diffs",
        "entries": entries,
    }
    write_json(silo_root(root) / "snapshot_index.json", index)
    write_snapshot_index_markdown(entries, root)
    return index


def write_snapshot_index_markdown(entries: list[dict[str, Any]], root: Path) -> None:
    lines = [
        "# Algorithm Snapshot Index",
        "",
        "Generated from `AlgorithmVersion` rows and git-history snapshots.",
        "No score rows, assessment rows, parquet caches, or experiment datasets are backfilled here.",
        "",
        "| Version | Status | Commit | Category | Scoring Snapshot | Diff vs Prior | Message |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for entry in sorted(entries, key=lambda row: row["version_id"], reverse=True):
        classification = entry.get("classification") or {}
        diff = entry.get("diff_from_previous") or {}
        changed = len(diff.get("changed_paths") or []) if isinstance(diff, dict) else ""
        message = (
            entry.get("algorithm_version_message")
            or entry.get("commit_subject")
            or ""
        )
        message = str(message).replace("|", "\\|").replace("\n", " ")
        lines.append(
            "| {key} | {status} | {commit} | {category} | {scoring} | {changed} | {message} |".format(
                key=entry["key"],
                status=entry.get("status") or "",
                commit=(entry.get("git_commit") or "")[:8],
                category=classification.get("category", ""),
                scoring="yes" if classification.get("scoring_algorithm_snapshot") else "no",
                changed=changed,
                message=message,
            )
        )
    write_path = silo_root(root) / "SNAPSHOT_INDEX.md"
    write_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def portfolio_snapshot_current(
    *,
    key: str | None = None,
    label: str | None = None,
    version_token: str | None = None,
    force: bool = False,
) -> Path:
    root = project_root()
    version = resolve_algorithm_version(version_token, create_current=False)
    if not key:
        version_part = f"v{version.id}" if version is not None else "unknown"
        key = f"{version_part}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    key = normalize_version_key(key) if str(key).isdigit() else str(key).strip()
    dest = silo_root(root) / "portfolio" / f"{key}.json"
    if dest.exists() and not force:
        raise SystemExit(f"{dest} already exists; pass --force to replace it")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "key": key,
        "label": label,
        "captured_at": utc_now(),
        "algorithm_version": algorithm_version_payload(version),
        "source": git_state(root),
        "portfolio": portfolio_payload(),
    }
    payload["fingerprint"] = sha256_json(payload["portfolio"])
    write_json(dest, payload)

    registry = load_registry(root)
    registry.setdefault("portfolio_snapshots", {})[key] = {
        "path": str(dest.relative_to(root).as_posix()),
        "label": label,
        "captured_at": payload["captured_at"],
        "algorithm_version": payload["algorithm_version"],
        "fingerprint": payload["fingerprint"],
    }
    save_registry(registry, root)
    return dest


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, child))
        return out
    if isinstance(data, list):
        if all(not isinstance(v, (dict, list)) for v in data):
            return {prefix: data}
        out = {}
        for i, v in enumerate(data):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: data}


def diff_dicts(a: Any, b: Any) -> dict[str, list[dict[str, Any]]]:
    fa = flatten(a)
    fb = flatten(b)
    added = [{"key": k, "value": fb[k]} for k in sorted(fb.keys() - fa.keys())]
    removed = [{"key": k, "value": fa[k]} for k in sorted(fa.keys() - fb.keys())]
    changed = [
        {"key": k, "from": fa[k], "to": fb[k]}
        for k in sorted(fa.keys() & fb.keys())
        if fa[k] != fb[k]
    ]
    return {"added": added, "removed": removed, "changed": changed}


def snapshot_dir(key: str, root: Path | None = None) -> Path:
    return silo_root(root) / normalize_version_key(key)


def load_snapshot(key: str, root: Path | None = None) -> dict[str, Any]:
    path = snapshot_dir(key, root) / "manifest.json"
    if not path.exists():
        raise SystemExit(f"Snapshot not found: {normalize_version_key(key)} ({path})")
    return read_json(path)


def compare_snapshots(left: str, right: str) -> dict[str, Any]:
    root = project_root()
    a_key = normalize_version_key(left)
    b_key = normalize_version_key(right)
    a_dir = snapshot_dir(a_key, root)
    b_dir = snapshot_dir(b_key, root)
    a = load_snapshot(a_key, root)
    b = load_snapshot(b_key, root)
    a_files = {row["source"]: row for row in a.get("files", {}).get("scoring", [])}
    b_files = {row["source"]: row for row in b.get("files", {}).get("scoring", [])}
    file_changes = {
        "added": sorted(set(b_files) - set(a_files)),
        "removed": sorted(set(a_files) - set(b_files)),
        "changed": sorted(
            k for k in set(a_files) & set(b_files)
            if a_files[k].get("sha256") != b_files[k].get("sha256")
        ),
    }
    scoring_config_diff = diff_dicts(
        read_json(a_dir / "scoring_config.json"),
        read_json(b_dir / "scoring_config.json"),
    )
    portfolio_diff = diff_dicts(
        read_json(a_dir / "portfolio_snapshot.json"),
        read_json(b_dir / "portfolio_snapshot.json"),
    )
    portfolio_config_diff = None
    if (a_dir / "portfolio_config.json").exists() and (b_dir / "portfolio_config.json").exists():
        portfolio_config_diff = diff_dicts(
            read_json(a_dir / "portfolio_config.json"),
            read_json(b_dir / "portfolio_config.json"),
        )
    return {
        "left": a_key,
        "right": b_key,
        "file_changes": file_changes,
        "scoring_config_diff": scoring_config_diff,
        "portfolio_diff": portfolio_diff,
        "portfolio_config_diff": portfolio_config_diff,
        "fingerprints": {
            "left": a.get("fingerprints", {}),
            "right": b.get("fingerprints", {}),
        },
    }


def load_portfolio_snapshot(key: str) -> dict[str, Any]:
    root = project_root()
    path = silo_root(root) / "portfolio" / f"{key}.json"
    if not path.exists():
        raise SystemExit(f"Portfolio snapshot not found: {key}")
    return read_json(path)


def compare_portfolio_snapshots(left: str, right: str) -> dict[str, Any]:
    a = load_portfolio_snapshot(left)
    b = load_portfolio_snapshot(right)
    return {
        "left": left,
        "right": right,
        "portfolio_diff": diff_dicts(a.get("portfolio", {}), b.get("portfolio", {})),
        "fingerprints": {"left": a.get("fingerprint"), "right": b.get("fingerprint")},
    }


def backfill_portfolio_configs(*, force: bool = False) -> dict[str, Any]:
    root = project_root()
    algo_root = silo_root(root)
    registry = load_registry(root)
    updated: list[dict[str, Any]] = []
    skipped: list[str] = []
    for snapshot_dir_path in sorted(algo_root.iterdir(), key=lambda p: p.name):
        if not snapshot_dir_path.is_dir():
            continue
        if not (snapshot_dir_path / "manifest.json").exists():
            continue
        if not (snapshot_dir_path / "portfolio_snapshot.json").exists():
            continue
        key = snapshot_dir_path.name
        config_path = snapshot_dir_path / "portfolio_config.json"
        if config_path.exists() and not force:
            skipped.append(key)
            continue
        write_portfolio_config(snapshot_dir_path)
        config = read_json(config_path)
        manifest = read_json(snapshot_dir_path / "manifest.json")
        registry.setdefault("snapshots", {}).setdefault(key, {})["fingerprints"] = manifest.get(
            "fingerprints", {}
        )
        registry["snapshots"][key]["paths"] = manifest.get("paths", {})
        updated.append(
            {
                "key": key,
                "path": str(config_path.relative_to(root).as_posix()),
                "30dte": config.get("strategies", {}).get("30dte", {}).get("source_mode"),
                "15dte": config.get("strategies", {}).get("15dte", {}).get("source_mode"),
                "30dte_status": config.get("strategies", {}).get("30dte", {}).get("status"),
                "15dte_status": config.get("strategies", {}).get("15dte", {}).get("status"),
            }
        )
    if updated:
        save_registry(registry, root)
    return {
        "updated": updated,
        "updated_count": len(updated),
        "skipped_existing": skipped,
        "skipped_existing_count": len(skipped),
    }


def print_json(data: Any) -> None:
    print(stable_json(data), end="")


def cmd_list(_: argparse.Namespace) -> None:
    registry = load_registry(project_root())
    snapshots = registry.get("snapshots", {})
    if not snapshots:
        print("No algorithm version snapshots found.")
        return
    for key, row in sorted(snapshots.items(), key=lambda kv: kv[0]):
        av = row.get("algorithm_version") or {}
        label = row.get("label") or ""
        print(
            f"{key:<8} {row.get('status',''):<14} db:{av.get('id','?')!s:<4} "
            f"{(av.get('git_commit') or '')[:8]:<8} {row.get('captured_at','')} {label}"
        )


def cmd_active(_: argparse.Namespace) -> None:
    root = project_root()
    version = active_algorithm_version(create_current=False)
    key = f"v{version.id}"
    manifest = snapshot_dir(key, root) / "manifest.json"
    print(f"active={version.production_label} db:{version.id} commit={version.git_commit}")
    print(f"silo={manifest.parent.relative_to(root).as_posix()} exists={manifest.exists()}")
    print(f"cache={cache_path_for(key, root).relative_to(root).as_posix()}")


def cmd_snapshot(args: argparse.Namespace) -> None:
    path = snapshot_current(
        version_token=args.version,
        key=args.key,
        label=args.label,
        status=args.status,
        force=args.force,
        create_current=args.create_db_version,
    )
    print(f"snapshot={path}")


def cmd_snapshot_git_ref(args: argparse.Namespace) -> None:
    path = snapshot_git_ref(
        version_token=args.version,
        git_ref=args.git_ref,
        key=args.key,
        label=args.label,
        status=args.status,
        force=args.force,
        create_current=args.create_db_version,
    )
    print(f"snapshot={path}")


def cmd_backfill_git_snapshots(args: argparse.Namespace) -> None:
    result = snapshot_versions_from_git_history(force=args.force)
    print_json(result)


def cmd_backfill_portfolio_configs(args: argparse.Namespace) -> None:
    print_json(backfill_portfolio_configs(force=args.force))


def cmd_rebuild_index(_: argparse.Namespace) -> None:
    index = build_git_snapshot_index()
    print(f"snapshot_index={silo_root(project_root()) / 'snapshot_index.json'}")
    print(f"entries={len(index.get('entries', []))}")


def cmd_document_snapshots(_: argparse.Namespace) -> None:
    from algorithm_versions.documentation import document_snapshots

    print_json(document_snapshots())


def cmd_diff(args: argparse.Namespace) -> None:
    print_json(compare_snapshots(args.left, args.right))


def cmd_cache_root(args: argparse.Namespace) -> None:
    root = project_root()
    print(str(cache_path_for(args.key, root)))


def cmd_promote_candidate(args: argparse.Namespace) -> None:
    root = project_root()
    key = normalize_version_key(args.key)
    manifest_path = snapshot_dir(key, root) / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Snapshot not found: {key}")
    manifest = read_json(manifest_path)
    manifest["status"] = args.status
    manifest["promoted_at"] = utc_now()
    if args.label:
        manifest["label"] = args.label
    write_json(manifest_path, manifest)

    registry = load_registry(root)
    if key in registry.get("snapshots", {}):
        registry["snapshots"][key]["status"] = args.status
        registry["snapshots"][key]["label"] = manifest.get("label")
    save_registry(registry, root)
    print(f"{key} -> {args.status}")


def cmd_portfolio_current(args: argparse.Namespace) -> None:
    path = portfolio_snapshot_current(
        key=args.key,
        label=args.label,
        version_token=args.version,
        force=args.force,
    )
    print(f"portfolio_snapshot={path}")


def cmd_portfolio_list(_: argparse.Namespace) -> None:
    registry = load_registry(project_root())
    rows = registry.get("portfolio_snapshots", {})
    if not rows:
        print("No portfolio snapshots found.")
        return
    for key, row in sorted(rows.items(), key=lambda kv: kv[0]):
        av = row.get("algorithm_version") or {}
        print(
            f"{key:<24} db:{av.get('id','?')!s:<4} {(av.get('git_commit') or '')[:8]:<8} "
            f"{row.get('captured_at','')} {row.get('label') or ''}"
        )


def cmd_portfolio_diff(args: argparse.Namespace) -> None:
    print_json(compare_portfolio_snapshots(args.left, args.right))


def build_algorithm_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader algorithm")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("active")
    p.set_defaults(func=cmd_active)

    p = sub.add_parser("snapshot-current")
    p.add_argument("--version", help="version token to attach, defaults to active version")
    p.add_argument("--key", help="snapshot directory key, defaults to v<db id>")
    p.add_argument("--label")
    p.add_argument("--status", default="candidate", choices=SNAPSHOT_STATUSES)
    p.add_argument("--force", action="store_true")
    p.add_argument("--create-db-version", action="store_true")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("snapshot-staging")
    p.add_argument("--version", help="version token to attach, defaults to active version")
    p.add_argument("--key", help="snapshot directory key, defaults to v<db id>")
    p.add_argument("--label")
    p.add_argument("--force", action="store_true")
    p.add_argument("--create-db-version", action="store_true")
    p.set_defaults(func=lambda args: cmd_snapshot(argparse.Namespace(**{**vars(args), "status": "candidate"})))

    p = sub.add_parser("snapshot-git-ref")
    p.add_argument("--version", help="version token to attach, defaults to active version")
    p.add_argument("--git-ref", help="git ref to copy, defaults to the version git_commit")
    p.add_argument("--key", help="snapshot directory key, defaults to v<db id>")
    p.add_argument("--label")
    p.add_argument("--status", default="shipped", choices=SNAPSHOT_STATUSES)
    p.add_argument("--force", action="store_true")
    p.add_argument("--create-db-version", action="store_true")
    p.set_defaults(func=cmd_snapshot_git_ref)

    p = sub.add_parser("backfill-git-snapshots")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_backfill_git_snapshots)

    p = sub.add_parser("backfill-portfolio-configs")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_backfill_portfolio_configs)

    p = sub.add_parser("rebuild-index")
    p.set_defaults(func=cmd_rebuild_index)

    p = sub.add_parser("document-snapshots")
    p.set_defaults(func=cmd_document_snapshots)

    p = sub.add_parser("promote-candidate")
    p.add_argument("key")
    p.add_argument("--status", default="ship_candidate", choices=("ship_candidate", "shipped"))
    p.add_argument("--label")
    p.set_defaults(func=cmd_promote_candidate)

    p = sub.add_parser("diff")
    p.add_argument("left")
    p.add_argument("right")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("cache-root")
    p.add_argument("key")
    p.set_defaults(func=cmd_cache_root)

    return parser


def build_portfolio_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trader portfolio-snapshot")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("current")
    p.add_argument("--key")
    p.add_argument("--label")
    p.add_argument("--version")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_portfolio_current)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_portfolio_list)

    p = sub.add_parser("diff")
    p.add_argument("left")
    p.add_argument("right")
    p.set_defaults(func=cmd_portfolio_diff)

    return parser


def main(argv: str | list[str] | None = None) -> None:
    parser = build_algorithm_parser()
    tokens = shlex.split(argv) if isinstance(argv, str) else argv
    args = parser.parse_args(tokens)
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


def portfolio_main(argv: str | list[str] | None = None) -> None:
    parser = build_portfolio_parser()
    tokens = shlex.split(argv) if isinstance(argv, str) else argv
    args = parser.parse_args(tokens)
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
