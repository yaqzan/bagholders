from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence


def _empty_version_summary() -> dict[str, list[dict[str, Any]]]:
    return {"written": [], "skipped": [], "errors": []}


@dataclass(frozen=True)
class ScoreCalculationResult:
    version_summary: dict[str, list[dict[str, Any]]] = field(default_factory=_empty_version_summary)

    @property
    def version_errors(self) -> list[dict[str, Any]]:
        return self.version_summary.get("errors", [])


@dataclass(frozen=True)
class UpdateScoreVersionArgs:
    raw_versions: list[str]
    unknown_args: list[str]


def parse_update_score_version_args(
    tokens: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> UpdateScoreVersionArgs:
    raw_versions: list[str] = []
    unknown: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--score-versions", "--versions"):
            if i + 1 >= len(tokens):
                unknown.append(token)
                i += 1
                continue
            raw_versions.append(tokens[i + 1])
            i += 2
            continue
        if token == "--score-version":
            if i + 1 >= len(tokens):
                unknown.append(token)
                i += 1
                continue
            raw_versions.append(tokens[i + 1])
            i += 2
            continue
        if token.startswith("--score-versions="):
            raw_versions.append(token.split("=", 1)[1])
            i += 1
            continue
        if token.startswith("--versions="):
            raw_versions.append(token.split("=", 1)[1])
            i += 1
            continue
        if token.startswith("--score-version="):
            raw_versions.append(token.split("=", 1)[1])
            i += 1
            continue
        unknown.append(token)
        i += 1

    env_versions = (env if env is not None else os.environ).get("TRADER_SCORE_VERSIONS")
    if env_versions:
        raw_versions.append(env_versions)

    return UpdateScoreVersionArgs(raw_versions=raw_versions, unknown_args=unknown)


def _parse_version_tokens(raw_versions: Sequence[str] | str | None) -> list[str]:
    from algorithm_versions.runtime import parse_version_tokens

    return parse_version_tokens(raw_versions)


def _validate_runtime_versions(version_tokens: Sequence[str]) -> list[str]:
    from algorithm_versions.runtime import validate_runtime_versions

    return validate_runtime_versions(list(version_tokens))


def _resolve_versions(version_tokens: Sequence[str]) -> list[Any]:
    from algorithm_versions.runtime import resolve_versions

    return resolve_versions(list(version_tokens))


def _cadence_version_tokens() -> list[str]:
    from database.models.core import AlgorithmVersion

    return AlgorithmVersion.cadence_score_version_tokens()


def _score_latest_for_versions(symbol: str, version_tokens: Sequence[str]) -> dict[str, Any]:
    from algorithm_versions.runtime import score_latest_for_versions

    return score_latest_for_versions(symbol, list(version_tokens))


def _score_primary_for_versions(primary_score: Any, versions: Sequence[Any]) -> dict[str, Any]:
    from algorithm_versions.runtime import score_primary_for_versions

    return score_primary_for_versions(primary_score, list(versions))


def _record_intraday_score(
    symbol: str,
    *,
    source: str = "trader_update",
    run_key: str | None = None,
    target_date: date | None = None,
) -> bool:
    try:
        from database.models.core import ScoreIntradayLog

        return ScoreIntradayLog.record_latest(
            symbol,
            target_date=target_date or date.today(),
            source=source,
            run_key=run_key,
        )
    except Exception:
        return False


def _load_scoring_engines(versions: Sequence[Any]) -> tuple[list[Any], list[dict[str, str]]]:
    from algorithm_versions.runtime import load_scoring_engine
    from database.models.core import AlgorithmVersion

    current_version = AlgorithmVersion.get_or_create_current()
    engines = []
    skipped = []
    for version in versions:
        key = f"v{int(version.id)}"
        if int(version.id) == int(current_version.id):
            skipped.append({"key": key, "reason": "current version already scored"})
            continue
        engines.append(load_scoring_engine(version))
    return engines, skipped


def _latest_current_score(symbol: str) -> Any | None:
    from database.models.core import AlgorithmVersion, Score
    from peewee import fn

    current_version = AlgorithmVersion.get_or_create_current()
    target_date = (
        Score
        .select(fn.MAX(Score.date))
        .where(
            Score.symbol == symbol,
            Score.version == current_version,
            Score.overall.is_null(False),
        )
        .scalar()
    )
    if target_date is None:
        return None
    return Score.get_or_none(
        (Score.symbol == symbol)
        & (Score.date == target_date)
        & (Score.version == current_version)
    )


class ScoreCalculationService:
    def __init__(
        self,
        algorithm_versions: Sequence[str] | str | None = None,
        *,
        validate_versions: bool = True,
        use_cadence_defaults: bool = False,
        audit_intraday_scores: bool = True,
    ) -> None:
        parsed = _parse_version_tokens(algorithm_versions)
        self.update_run_key = f"update-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}"
        self.audit_intraday_scores = audit_intraday_scores
        self.version_source = "explicit" if parsed else "none"
        if not parsed and use_cadence_defaults:
            parsed = _cadence_version_tokens()
            self.version_source = "cadence" if parsed else "none"
        self.algorithm_versions = (
            _validate_runtime_versions(parsed)
            if validate_versions and parsed
            else parsed
        )
        self._resolved_algorithm_versions = (
            _resolve_versions(self.algorithm_versions)
            if validate_versions and self.algorithm_versions
            else None
        )
        try:
            if self._resolved_algorithm_versions:
                self._score_engines, self._inline_version_skips = _load_scoring_engines(
                    self._resolved_algorithm_versions
                )
            else:
                self._score_engines, self._inline_version_skips = [], []
        except Exception:
            self._score_engines = []
            self._inline_version_skips = []

    @classmethod
    def from_update_args(
        cls,
        tokens: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        validate_versions: bool = True,
        use_cadence_defaults: bool = False,
        audit_intraday_scores: bool = True,
    ) -> tuple["ScoreCalculationService", list[str]]:
        parsed = parse_update_score_version_args(tokens, env=env)
        service = cls(
            parsed.raw_versions,
            validate_versions=validate_versions,
            use_cadence_defaults=use_cadence_defaults,
            audit_intraday_scores=audit_intraday_scores,
        )
        return service, parsed.unknown_args

    @property
    def has_algorithm_versions(self) -> bool:
        return bool(self.algorithm_versions)

    def register_update_line_steps(self, line: Any) -> None:
        line.register("scores", "scores")
        if self.has_algorithm_versions:
            line.register("versions", "version scores")

    def calculate_current_scores_for_stock(self, stock: Any, *, full: bool) -> dict[str, Any]:
        stock.calculate_scores(full=full, weekly=True, silent=True)
        if full:
            stock.calculate_scores_batched(silent=True)
            return _empty_version_summary()
        if self._score_engines:
            summary = stock.calculate_scores(
                full=False,
                silent=True,
                extra_score_engines=self._score_engines,
            ) or _empty_version_summary()
            summary["skipped"].extend(self._inline_version_skips)
            return summary
        stock.calculate_scores(full=False, silent=True)
        return _empty_version_summary()

    def calculate_algorithm_versions_for_symbol(self, symbol: str) -> dict[str, Any]:
        if not self.has_algorithm_versions:
            return _empty_version_summary()
        if self._resolved_algorithm_versions is not None:
            primary_score = _latest_current_score(symbol)
            if primary_score is None:
                return {
                    "written": [],
                    "skipped": [{"reason": "current version score missing"}],
                    "errors": [],
                }
            return _score_primary_for_versions(primary_score, self._resolved_algorithm_versions)
        return _score_latest_for_versions(symbol, self.algorithm_versions)

    def calculate_for_update_stock(
        self,
        stock: Any,
        *,
        full: bool,
        line: Any | None = None,
    ) -> ScoreCalculationResult:
        inline_version_scoring = bool(self._score_engines) and not full
        if line is not None:
            line.start("scores")
            if inline_version_scoring:
                line.start("versions")
        version_summary = self.calculate_current_scores_for_stock(stock, full=full)
        if line is not None:
            line.complete("scores")
            if inline_version_scoring:
                if version_summary.get("errors"):
                    line.error("versions")
                else:
                    line.complete("versions")

        if self.has_algorithm_versions and not inline_version_scoring:
            if line is not None:
                line.start("versions")
            try:
                version_summary = self.calculate_algorithm_versions_for_symbol(stock.symbol)
                if line is not None:
                    line.complete("versions")
            except Exception as exc:
                if line is not None:
                    line.error("versions")
                version_summary["errors"].append({
                    "key": "version-service",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if self.audit_intraday_scores:
            try:
                _record_intraday_score(
                    stock.symbol,
                    source="trader_update",
                    run_key=self.update_run_key,
                )
            except Exception:
                pass
        return ScoreCalculationResult(version_summary=version_summary)
