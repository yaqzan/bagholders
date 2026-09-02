from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from algorithm_versions.manager import (
    active_algorithm_version,
    diff_dicts,
    load_registry,
    normalize_version_key,
    read_json,
    silo_root,
    snapshot_dir,
)


DOC_HINT_FILES = (
    ".claude/docs/version-history.md",
    ".claude/docs/version-history-archive.md",
    ".claude/docs/scoring-algorithm.md",
    ".claude/docs/known-issues.md",
    ".claude/docs/assessment-backtest.md",
    ".claude/docs/trading-strategy.md",
    ".claude/docs/monte-carlo.md",
    ".claude/docs/monte-carlo-sweeps.md",
    ".claude/docs/active-investigations/continuation-boost.md",
)

UNICODE_REPLACEMENTS = str.maketrans(
    {
        "\u2014": "-",
        "\u2013": "-",
        "\u2192": "->",
        "\u2265": ">=",
        "\u2264": "<=",
        "\u0394": "Delta",
        "\u2713": "PASS",
        "\u2717": "FAIL",
        "\u00d7": "x",
        "\u00b1": "+/-",
        "\u03c3": "sigma",
    }
)


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.translate(UNICODE_REPLACEMENTS)
    text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("|", "\\|").strip()


def _first_sentence(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", clean_text(text)).strip()
    if not text:
        return ""
    m = re.search(r"(?<=[.!?])\s+", text)
    if m and m.start() <= limit:
        text = text[:m.start()].strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


_VERSION_HEADING_RE = re.compile(
    r"^##\s+(?:Active Version:\s+)?(v\d+)\b([^\n]*)$",
    re.MULTILINE,
)


def _parse_version_heading_sections(text: str, source_doc: str) -> dict[str, dict[str, str]]:
    matches = list(_VERSION_HEADING_RE.finditer(text))
    sections: dict[str, dict[str, str]] = {}
    for i, match in enumerate(matches):
        key = match.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        paragraphs = [
            p.strip()
            for p in re.split(r"\n\s*\n", body)
            if p.strip() and not p.strip().startswith("|")
        ]
        sections[key] = {
            "heading": clean_text(match.group(0).lstrip("# ").strip()),
            "excerpt": clean_text(paragraphs[0]) if paragraphs else "",
            "source_doc": source_doc,
        }
    return sections


def _load_version_history_sections(root: Path) -> dict[str, dict[str, str]]:
    # Versions v60-and-earlier were moved out of version-history.md into
    # version-history-archive.md to keep the primary doc lean (they now live
    # under one "Older Versions (v60 and earlier) - Archived" heading in the
    # primary doc, which does not match the per-version heading pattern).
    # Both docs are parsed so archived versions still get a real "Existing
    # Documentation Hint" instead of falling back to the no-content stub.
    # The archive is loaded first and the primary doc last, so the primary
    # doc wins on any key collision.
    sections: dict[str, dict[str, str]] = {}
    archive_path = root / ".claude" / "docs" / "version-history-archive.md"
    if archive_path.exists():
        archive_text = archive_path.read_text(encoding="utf-8", errors="replace")
        sections.update(
            _parse_version_heading_sections(
                archive_text, ".claude/docs/version-history-archive.md"
            )
        )
    primary_path = root / ".claude" / "docs" / "version-history.md"
    if primary_path.exists():
        primary_text = primary_path.read_text(encoding="utf-8", errors="replace")
        sections.update(
            _parse_version_heading_sections(primary_text, ".claude/docs/version-history.md")
        )
    return sections


def _doc_hints(root: Path, key: str, commit: str | None, limit: int = 8) -> list[dict[str, Any]]:
    patterns = []
    if commit and len(commit) >= 7 and commit.lower() != "baseline":
        patterns.append(re.escape(commit[:8]))
    if not patterns:
        return []
    rx = re.compile("|".join(patterns), re.IGNORECASE)
    hints: list[dict[str, Any]] = []
    for rel in DOC_HINT_FILES:
        path = root / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, start=1):
            if rx.search(line):
                hints.append(
                    {
                        "path": rel.replace("\\", "/"),
                        "line": i,
                        "snippet": _first_sentence(line, 220),
                    }
                )
                if len(hints) >= limit:
                    return hints
    return hints


def _load_index(root: Path) -> list[dict[str, Any]]:
    path = silo_root(root) / "snapshot_index.json"
    if not path.exists():
        return []
    data = read_json(path)
    return data.get("entries", [])


def _load_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return read_json(path)


def _diff_summary(left: Any, right: Any, limit: int = 24) -> dict[str, Any]:
    if left is None or right is None:
        return {
            "available": False,
            "added": [],
            "removed": [],
            "changed": [],
            "truncated": 0,
        }
    diff = diff_dicts(left, right)
    added = diff["added"][:limit]
    removed = diff["removed"][:limit]
    changed = diff["changed"][:limit]
    total = len(diff["added"]) + len(diff["removed"]) + len(diff["changed"])
    shown = len(added) + len(removed) + len(changed)
    return {
        "available": True,
        "added": added,
        "removed": removed,
        "changed": changed,
        "truncated": max(0, total - shown),
    }


def _runtime_loadable(snapshot: Path) -> bool:
    return (
        (snapshot / "scoring" / "strategy_config.py").exists()
        and (snapshot / "scoring" / "database" / "utils" / "scoring.py").exists()
    )


def _format_value(value: Any) -> str:
    text = clean_text(json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value)
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


def _format_diff_lines(title: str, diff: dict[str, Any]) -> list[str]:
    lines = [f"### {title}", ""]
    if not diff.get("available"):
        lines.append("Structured diff unavailable for this version.")
        lines.append("")
        return lines
    if not diff["added"] and not diff["removed"] and not diff["changed"]:
        lines.append("No structured changes detected.")
        lines.append("")
        return lines
    for row in diff["changed"]:
        lines.append(
            f"- `{clean_text(row['key'])}`: `{_format_value(row['from'])}` -> `{_format_value(row['to'])}`"
        )
    for row in diff["added"]:
        lines.append(f"- Added `{clean_text(row['key'])}` = `{_format_value(row['value'])}`")
    for row in diff["removed"]:
        lines.append(f"- Removed `{clean_text(row['key'])}` = `{_format_value(row['value'])}`")
    if diff.get("truncated"):
        lines.append(f"- ... {diff['truncated']} additional structured changes omitted.")
    lines.append("")
    return lines


def _derive_intent(entry: dict[str, Any], section: dict[str, str] | None) -> str:
    message = entry.get("algorithm_version_message") or entry.get("commit_subject") or ""
    if message:
        return _first_sentence(message, 220)
    if section and section.get("heading"):
        heading = section["heading"]
        m = re.search(r"\(([^()]*)\)\s*$", heading)
        if m:
            return _first_sentence(m.group(1), 220)
    if section and section.get("excerpt"):
        return _first_sentence(section["excerpt"], 220)
    return "No intent text found; inspect diff and commit metadata."


_README_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Shared prefix of both the old and new "no fresh content" hint stub wording,
# so a README written before the archive-parsing fix (below) is still
# recognized as a stub and not mistaken for real content worth preserving.
_HINT_STUB_PREFIX = "No dedicated section was found in `.claude/docs/version-history.md`"
_REFS_STUB_LINE = "- No extra doc references found."


def _split_markdown_sections(text: str) -> dict[str, str]:
    """Split a previously-generated README into {level-2 heading: body}.

    Used to recover prior "Existing Documentation Hint" / "Source
    References" content when this run finds no fresh source material for
    them, so a doc restructure elsewhere can never regress a README that
    already had real content down to a no-content stub (structural safety).
    """
    matches = list(_README_SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[match.end() : end].strip("\n")
    return sections


def _write_version_readme(
    *,
    root: Path,
    entry: dict[str, Any],
    entries_by_key: dict[str, dict[str, Any]],
    sections: dict[str, dict[str, str]],
) -> dict[str, Any]:
    key = normalize_version_key(entry["key"])
    snap = snapshot_dir(key, root)
    manifest = _load_json_if_exists(snap / "manifest.json") or {}
    section = sections.get(key)
    existing_readme = snap / "README.md"
    existing_sections = (
        _split_markdown_sections(existing_readme.read_text(encoding="utf-8", errors="replace"))
        if existing_readme.exists()
        else {}
    )
    previous_key = entry.get("previous_resolved_key")
    previous_snap = snapshot_dir(previous_key, root) if previous_key else None
    scoring_config = _load_json_if_exists(snap / "scoring_config.json")
    portfolio = _load_json_if_exists(snap / "portfolio_snapshot.json")
    previous_scoring = (
        _load_json_if_exists(previous_snap / "scoring_config.json")
        if previous_snap is not None
        else None
    )
    previous_portfolio = (
        _load_json_if_exists(previous_snap / "portfolio_snapshot.json")
        if previous_snap is not None
        else None
    )
    scoring_diff = _diff_summary(previous_scoring, scoring_config)
    portfolio_diff = _diff_summary(previous_portfolio, portfolio)
    diff_json = _load_json_if_exists(snap / "diff_from_previous.json") or {}
    classification = entry.get("classification") or manifest.get("classification") or {}
    commit = entry.get("git_commit") or ""
    resolved = entry.get("resolved_commit") or manifest.get("source", {}).get("commit")
    intent = _derive_intent(entry, section)
    hints = _doc_hints(root, key, commit)
    runtime_loadable = _runtime_loadable(snap)
    structured_available = not (
        isinstance(scoring_config, dict) and scoring_config.get("extraction_error")
    )

    lines = [
        f"# {key} Algorithm Snapshot",
        "",
        f"- Status: `{clean_text(entry.get('status'))}`",
        f"- DB version: `{clean_text(entry.get('version_id'))}`",
        f"- Commit: `{clean_text(commit)}`",
        f"- Resolved commit: `{clean_text(resolved)}`" if resolved else "- Resolved commit: unavailable",
        f"- Category: `{clean_text(classification.get('category'))}`",
        f"- Scoring algorithm snapshot: `{'yes' if classification.get('scoring_algorithm_snapshot') else 'no'}`",
        f"- Runtime-loadable for `trader update --score-versions`: `{'yes' if runtime_loadable else 'no'}`",
        f"- Structured scoring config: `{'yes' if structured_available else 'no'}`",
        "",
        "## Intended Difference",
        "",
        intent,
        "",
        f"AlgorithmVersion message: {clean_text(entry.get('algorithm_version_message') or '') or 'none'}",
        f"Commit subject: {clean_text(entry.get('commit_subject') or '') or 'none'}",
        "",
    ]
    existing_hint = existing_sections.get("Existing Documentation Hint")
    existing_hint_is_stub = not existing_hint or existing_hint.startswith(_HINT_STUB_PREFIX)
    if section:
        source_note = f"Source heading: `{clean_text(section.get('heading'))}`"
        if section.get("source_doc"):
            source_note += f" (from `{section['source_doc']}`)"
        lines.extend(
            [
                "## Existing Documentation Hint",
                "",
                source_note,
                "",
                clean_text(section.get("excerpt")) or "No excerpt captured.",
                "",
            ]
        )
    elif not existing_hint_is_stub:
        # Structural safety: no fresh section was found this run (heading
        # renamed/moved/removed upstream), but the README already had real
        # hint content. Preserve it instead of regressing to the stub -- a
        # no-content stub may only ever replace an existing stub or a
        # missing file, never real content.
        lines.extend(["## Existing Documentation Hint", "", existing_hint, ""])
    else:
        lines.extend(
            [
                "## Existing Documentation Hint",
                "",
                _HINT_STUB_PREFIX
                + " or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.",
                "",
            ]
        )

    lines.extend(
        [
            "## Code Delta From Previous Resolved Version",
            "",
            f"Previous resolved key: `{clean_text(previous_key) if previous_key else 'none'}`",
            f"Diff range: `{clean_text(diff_json.get('range')) if diff_json else 'none'}`",
            "",
        ]
    )
    changed_paths = diff_json.get("changed_paths") or []
    if changed_paths:
        lines.append("Changed snapshot-tracked paths:")
        lines.extend(f"- `{clean_text(path)}`" for path in changed_paths)
    else:
        lines.append("No changed snapshot-tracked paths recorded.")
    lines.append("")
    stat_lines = diff_json.get("stat") or []
    if stat_lines:
        lines.append("Git diff stat:")
        lines.append("")
        lines.append("```text")
        lines.extend(clean_text(line).replace("\\|", "|") for line in stat_lines)
        lines.append("```")
        lines.append("")

    lines.extend(_format_diff_lines("Structured Scoring Variable Delta", scoring_diff))
    lines.extend(_format_diff_lines("Structured Portfolio Variable Delta", portfolio_diff))

    existing_refs = existing_sections.get("Source References")
    existing_refs_is_stub = not existing_refs or existing_refs.strip() == _REFS_STUB_LINE
    existing_ref_lines = (
        [line for line in existing_refs.splitlines() if line.strip().startswith("- ")]
        if existing_refs and not existing_refs_is_stub
        else []
    )
    new_ref_lines = [
        f"- `{hint['path']}:{hint['line']}` - {clean_text(hint['snippet'])}" for hint in hints
    ]
    lines.extend(["## Source References", ""])
    if new_ref_lines or existing_ref_lines:
        # Structural safety: a citation the prior run found is never quietly
        # dropped just because this run's scan did not independently
        # rediscover it (e.g. the citing doc was edited elsewhere for
        # unrelated reasons) -- carry forward any old citation not already
        # covered by a fresh one instead of shrinking the list.
        carried_over = [line for line in existing_ref_lines if line not in new_ref_lines]
        lines.extend(new_ref_lines + carried_over)
    else:
        lines.append(_REFS_STUB_LINE)
    lines.append("")
    lines.extend(
        [
            "## Agent Pointers",
            "",
            "- `manifest.json` - snapshot metadata and source fingerprints.",
            "- `diff_from_previous.json` - source-file delta against the previous resolved version.",
            "- `scoring_config.json` - structured scoring variables when available.",
            "- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.",
            "- `scoring/` - copied scoring source files.",
            "- `portfolio_sources/` - copied portfolio source files.",
            "",
        ]
    )

    (snap / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "key": key,
        "status": entry.get("status"),
        "commit": commit,
        "category": classification.get("category"),
        "runtime_loadable": runtime_loadable,
        "structured_config": structured_available,
        "intent": intent,
        "readme": f"{key}/README.md",
        "changed_paths": changed_paths,
    }


_SIMPLE_VERSION_KEY_RE = re.compile(r"^v(\d+)$")


def _resolve_fast_access(root: Path) -> dict[str, str | None]:
    """Ground-truth Fast Access pointers -- no hand-maintenance required.

    Snapshot status/version_id are read from the manager's registry
    (``registry.json``), which every ``snapshot-current``/``snapshot-git-ref``
    capture updates immediately -- unlike ``snapshot_index.json``, which only
    advances when someone runs the separate, periodic ``rebuild-index``/
    ``backfill-git-snapshots`` commands and can lag many shipped versions
    behind. The active scoring version is read straight from
    ``AlgorithmVersion`` so it is correct even when no snapshot has been
    captured for it yet.
    """
    numbered: list[tuple[int, str, str | None]] = []
    try:
        snapshots = load_registry(root).get("snapshots", {})
    except Exception:
        snapshots = {}
    for snap_key, row in snapshots.items():
        match = _SIMPLE_VERSION_KEY_RE.match(snap_key)
        if not match:
            continue
        numbered.append((int(match.group(1)), snap_key, row.get("status")))

    def _latest(statuses: tuple[str, ...]) -> tuple[int, str, str | None] | None:
        candidates = [row for row in numbered if row[2] in statuses]
        return max(candidates, key=lambda row: row[0]) if candidates else None

    latest_shipped = _latest(("shipped",))
    latest_candidate = _latest(("candidate", "ship_candidate"))
    if (
        latest_candidate is not None
        and latest_shipped is not None
        and latest_candidate[0] <= latest_shipped[0]
    ):
        latest_candidate = None

    active_key = None
    try:
        version = active_algorithm_version(create_current=False)
        if version is not None:
            active_key = f"v{int(version.id)}"
    except Exception:
        active_key = None

    return {
        "latest_shipped": latest_shipped[1] if latest_shipped else None,
        "latest_candidate": latest_candidate[1] if latest_candidate else None,
        "active": active_key or (latest_shipped[1] if latest_shipped else None),
    }


def _fast_access_loader_keys(fast: dict[str, str | None]) -> list[str]:
    """Non-active cadence versions worth passing to --score-versions.

    ``trader update --score-versions`` skips whatever equals the current
    active version (see trader.py's post-update score refresh), so the
    active key itself is never useful in this list.
    """
    return [
        key
        for key in (fast["latest_shipped"], fast["latest_candidate"])
        if key and key != fast["active"]
    ]


def _write_version_guide(root: Path, docs: list[dict[str, Any]], fast: dict[str, str | None]) -> None:
    lines = [
        "# Algorithm Version Guide",
        "",
        "Agent-facing index for shipped algorithm silos. Each `vNN/README.md` explains the intended change, source hints, code delta, structured variable delta, and runtime-loadability.",
        "",
        "Coverage: git-history code snapshots are backfilled. Score rows, assessment rows, parquet caches, and experiment datasets are not backfilled here.",
        "",
        "| Version | Commit | Category | Runtime | Structured | Intent |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in sorted(docs, key=lambda r: int(r["key"][1:]), reverse=True):
        lines.append(
            f"| [{row['key']}]({row['readme']}) | `{clean_text(row['commit'])[:8]}` | "
            f"{clean_text(row['category'])} | {'yes' if row['runtime_loadable'] else 'no'} | "
            f"{'yes' if row['structured_config'] else 'no'} | {clean_text(row['intent'])} |"
        )

    lines.extend(["", "## Fast Access", ""])
    if fast["latest_shipped"]:
        lines.append(
            f"- Latest shipped snapshot: [{fast['latest_shipped']}]({fast['latest_shipped']}/README.md)"
        )
    if fast["latest_candidate"]:
        lines.append(
            f"- Latest cadence candidate snapshot: [{fast['latest_candidate']}]({fast['latest_candidate']}/README.md)"
        )
    if fast["active"]:
        lines.append(f"- Active/main scoring snapshot: [{fast['active']}]({fast['active']}/README.md)")
    lines.append("- Machine index: [snapshot_index.json](snapshot_index.json)")
    lines.append("- Compact snapshot table: [SNAPSHOT_INDEX.md](SNAPSHOT_INDEX.md)")
    loader_keys = _fast_access_loader_keys(fast)
    if loader_keys:
        lines.append(
            f"- Runtime scoring loader: `trader update --score-versions {','.join(loader_keys)}`"
        )
    else:
        lines.append(
            "- Runtime scoring loader: no additional cadence versions pending "
            f"(active `{fast['active']}` is already the latest shipped snapshot)."
        )
    lines.append("")
    (silo_root(root) / "VERSION_GUIDE.md").write_text("\n".join(lines), encoding="utf-8")


def _write_claude_index(root: Path, docs: list[dict[str, Any]], fast: dict[str, str | None]) -> None:
    latest = sorted(docs, key=lambda r: int(r["key"][1:]), reverse=True)[:18]
    loader_keys = _fast_access_loader_keys(fast)
    loader_hint = (
        f"trader update --score-versions {','.join(loader_keys)}"
        if loader_keys
        else f"trader update  # active {fast['active']} already covers the latest shipped snapshot"
    )
    lines = [
        "# Algorithm Version Index",
        "",
        "Use this as the agent entrypoint for algorithm-version comparison.",
        "",
        "- Full guide: [algorithm_versions/VERSION_GUIDE.md](../../algorithm_versions/VERSION_GUIDE.md)",
        "- Per-version docs: `algorithm_versions/vNN/README.md`",
        f"- Runtime compare/update hook: `{loader_hint}`",
        "",
        "## Recent Versions",
        "",
        "| Version | Category | Runtime | Intent |",
        "|---|---|---:|---|",
    ]
    for row in latest:
        lines.append(
            f"| [{row['key']}](../../algorithm_versions/{row['readme']}) | "
            f"{clean_text(row['category'])} | {'yes' if row['runtime_loadable'] else 'no'} | "
            f"{clean_text(row['intent'])} |"
        )
    lines.extend(
        [
            "",
            "Older versions are in the full guide. Early versions may be code-only snapshots when structured config files did not exist yet.",
            "",
        ]
    )
    path = root / ".claude" / "docs" / "algorithm-version-index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def document_snapshots() -> dict[str, Any]:
    root = Path.cwd()
    entries = _load_index(root)
    entries_by_key = {normalize_version_key(e["key"]): e for e in entries}
    sections = _load_version_history_sections(root)
    docs = [
        _write_version_readme(
            root=root,
            entry=entry,
            entries_by_key=entries_by_key,
            sections=sections,
        )
        for entry in entries
        if str(entry.get("key", "")).startswith("v")
    ]
    fast = _resolve_fast_access(root)
    _write_version_guide(root, docs, fast)
    _write_claude_index(root, docs, fast)
    return {
        "documented": len(docs),
        "version_guide": str((silo_root(root) / "VERSION_GUIDE.md").relative_to(root).as_posix()),
        "claude_index": ".claude/docs/algorithm-version-index.md",
    }
