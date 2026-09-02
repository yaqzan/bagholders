"""Write-time scoring-version integrity guard (v2 — config/formula lock).

Purpose
-------
Prevent the *version/config-drift contamination* class: writing Score rows tagged
as AlgorithmVersion X while the live process would actually compute *different*
scores than version X defines.

This is the class that corrupted v60 (2026-05-27/28): `ALGORITHM_VERSION` pointed
at v60 (`d4a3e9fec`) but a routine recalc wrote v60-tagged rows whose components
were byte-identical yet whose final `overall` differed near tier boundaries —
silently. (main's current code computes v60-identical scores, verified 0/5036;
the trigger was transient intermediate code that main has since advanced past.)

Architecture note — why a file-byte fingerprint is the WRONG check
-----------------------------------------------------------------
Production runs a **superset-of-versions + feature-flags** model: one working
tree carries code for many versions, `*_ENABLED` flags / constants in
`strategy_config` select which mechanisms are active, and `algorithm_versions/vNN/`
silos archive snapshots. So the live `strategy_config.py` / `database/models/core.py`
ALWAYS differ from an old version's exact committed files (the superset has extra
fields; core.py also carries non-scoring infra like ScoreIntradayLog). A raw-byte
fingerprint vs the version's commit therefore false-flags every legitimate write.
(That was the v1 guard's flaw; this v2 replaces it.)

The check that matches behavior is a **scoring lock**: a fingerprint of the
RESOLVED scoring config + the formula file, captured from the live tree when a
version is declared the active production config.

Fingerprint = sha256 of:
  * resolved `strategy_config.SCORING` (all flags+constants, via asdict), PLUS
    the EFFECTIVE `database/utils/scoring.py` module constants (post-env, so env
    overrides are caught), PLUS `CALIBRATION_CUTOFF_DATE`; and
  * the `database/utils/scoring.py` formula bytes (line-ending normalized; this
    file is infra-free, so its bytes mean "the formula").

`database/models/core.py` is deliberately NOT byte-fingerprinted (it interleaves
scoring orchestration with infra). Flag/constant-gated scoring behavior is caught
via the resolved-SCORING fingerprint.

Residual gap (documented): an *un-flagged* change to scoring orchestration in
core.py that alters scores without changing any SCORING value or scoring.py would
not be caught here. That narrower class is covered by the post-absorb closeout
assertion + code review.

Usage
-----
* `verify_active_before_write(context=...)` at every score-WRITE entry point
  (lives OUTSIDE the fingerprinted files — no self-reference).
* `capture_lock(version)` when declaring a version the active production config.
* Override `TRADER_DEFINE_VERSION=1` (ship path). Hatch `TRADER_SCORING_GUARD_DISABLE=1`.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

# scoring.py is infra-free, so byte-fingerprinting it == fingerprinting the
# formula. core.py is NOT byte-fingerprinted (mixes scoring + infra); its
# flag/constant-gated scoring behavior is captured via the resolved-SCORING hash.
SCORING_CODE_FILES: tuple[str, ...] = ("database/utils/scoring.py",)
LOCKS_REL = "algorithm_versions/scoring_locks.json"

ENV_DEFINE = "TRADER_DEFINE_VERSION"          # ship path: minting/redefining a version
ENV_DISABLE = "TRADER_SCORING_GUARD_DISABLE"  # emergency escape hatch (logged)


class ScoringVersionMismatch(RuntimeError):
    """Raised when live scoring config/formula != the active version's lock."""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _root():
    from database.project_root import get_trader_project_root
    return get_trader_project_root()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _norm(b: bytes | None) -> bytes:
    """Normalize line endings (CRLF/CR -> LF) so a Windows autocrlf checkout
    hashes identically to LF blobs."""
    if not b:
        return b""
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _resolved_scoring_sha() -> str:
    """sha256 over the RESOLVED strategy_config.SCORING (flags+constants) +
    EFFECTIVE scoring-module constants (post-env) + CALIBRATION_CUTOFF_DATE."""
    import strategy_config
    scoring = strategy_config.SCORING
    try:
        scoring_dict = dataclasses.asdict(scoring)
    except Exception:
        scoring_dict = {
            k: getattr(scoring, k) for k in dir(scoring)
            if k.isupper() or not k.startswith("_")
        }
    # Effective scoring-module constants (post _envb/_envi/_envf) so env
    # overrides are caught, not just strategy_config defaults.
    effective: dict = {}
    try:
        import database.utils.scoring as _sc
        for name in dir(_sc):
            if not name.isupper():
                continue
            val = getattr(_sc, name, None)
            if isinstance(val, (bool, int, float, str)):
                effective[name] = val
    except Exception:
        effective = {}
    payload = {
        "SCORING": scoring_dict,
        "EFFECTIVE_SCORING_CONSTANTS": effective,
        "CALIBRATION_CUTOFF_DATE": getattr(strategy_config, "CALIBRATION_CUTOFF_DATE", None),
    }
    return _sha(json.dumps(payload, sort_keys=True, default=str).encode())


def _code_sha() -> str:
    """sha256 over the (line-ending-normalized) scoring formula files."""
    root = _root()
    parts = []
    for rel in SCORING_CODE_FILES:
        p = root / rel
        try:
            b = p.read_bytes() if p.exists() else b""
        except OSError:
            b = b""
        parts.append(rel.encode() + b"\0" + _sha(_norm(b)).encode())
    return _sha(b"\0".join(parts))


def production_scoring_fingerprint() -> str:
    """Combined fingerprint of live resolved scoring config + formula."""
    return _sha((_resolved_scoring_sha() + "|" + _code_sha()).encode())


def _locks_path():
    return _root() / LOCKS_REL


def _load_locks() -> dict:
    p = _locks_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_locks(d: dict) -> None:
    p = _locks_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def capture_lock(version, *, note: str = "") -> dict:
    """Record the live scoring fingerprint as the lock for `version`'s commit.
    Run when declaring a version the active production config."""
    commit = getattr(version, "git_commit", None)
    if not commit:
        raise ValueError("version has no git_commit; cannot capture lock")
    locks = _load_locks()
    entry = {
        "version_id": getattr(version, "id", None),
        "fingerprint": production_scoring_fingerprint(),
        "scoring_config_sha": _resolved_scoring_sha(),
        "scoring_code_sha": _code_sha(),
    }
    if note:
        entry["note"] = note
    locks[commit] = entry
    _save_locks(locks)
    return entry


def verify_scoring_matches_version(version, *, context: str = "") -> None:
    """Raise ScoringVersionMismatch if the live resolved scoring config/formula
    does not match `version`'s recorded lock. No-op under the ship-path override.
    No lock recorded -> warn (cannot verify) but do not block (safe rollout)."""
    if _truthy(ENV_DISABLE) or _truthy(ENV_DEFINE):
        return
    commit = getattr(version, "git_commit", None)
    vid = getattr(version, "id", "?")
    ctx = f" ({context})" if context else ""
    if not commit:
        print(f"[scoring-guard] WARNING{ctx}: AlgorithmVersion v{vid} has no "
              f"git_commit; cannot verify scoring lock.")
        return
    lock = _load_locks().get(commit)
    if lock is None:
        print(f"[scoring-guard] WARNING{ctx}: no scoring lock recorded for v{vid} "
              f"({commit}); cannot verify live scoring config. Run "
              f"capture_lock() / 'trader algorithm lock-scoring' to enable enforcement.")
        return
    if production_scoring_fingerprint() == lock.get("fingerprint"):
        return
    drift = []
    if _resolved_scoring_sha() != lock.get("scoring_config_sha"):
        drift.append("resolved SCORING config (flags/constants)")
    if _code_sha() != lock.get("scoring_code_sha"):
        drift.append("scoring.py formula")
    raise ScoringVersionMismatch(
        f"Refusing score write{ctx}: live scoring config/formula != the lock for "
        f"AlgorithmVersion v{vid} ({commit}). Drift in: {drift or ['unknown']}. "
        f"Writing now would tag rows v{vid} while computing different scores (the "
        f"v60-contamination class — e.g. a scoring flag left flipped). Restore the "
        f"v{vid} scoring config, or set {ENV_DEFINE}=1 if you are intentionally "
        f"(re)defining this version (then re-run capture_lock)."
    )


def verify_active_before_write(context: str = "") -> None:
    """Verify the live scoring config matches the lock for the
    ALGORITHM_VERSION-resolved current version. Call at score-write entry points."""
    from database.models.core import AlgorithmVersion
    verify_scoring_matches_version(
        AlgorithmVersion.get_or_create_current(), context=context
    )
