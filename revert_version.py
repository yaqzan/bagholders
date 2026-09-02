"""Point ALGORITHM_VERSION at a prior AlgorithmVersion's commit hash.

The DB already holds scores for every past version keyed by commit. Rewriting
millions of score rows to a new hash would take hours; instead we just flip
the ALGORITHM_VERSION file so `get_or_create_current()` resolves to the
existing version's commit. Code changes to match that version are made
separately by the user.
"""
from __future__ import annotations

from pathlib import Path

from colorama import Fore, Style

from database.project_root import get_trader_project_root


def run(version_token: str) -> None:
    if not version_token:
        print(f"{Fore.RED}Usage: trader revert <version-id-or-githash>{Style.RESET_ALL}")
        return

    from assess_scores import resolve_algorithm_version
    target = resolve_algorithm_version(version_token)
    if target is None:
        return

    version_file = Path(get_trader_project_root()) / "ALGORITHM_VERSION"
    current = version_file.read_text().strip() if version_file.exists() else "(missing)"
    target_sha = target.git_commit
    msg = target.git_message or "(no message)"

    print()
    print(f"{Fore.CYAN}=== Revert ALGORITHM_VERSION ==={Style.RESET_ALL}")
    print(f"  Current: {current}")
    print(f"  Target:  {target.production_label} (db:{target.id})  {target_sha}  \"{msg}\"")
    print()

    if target_sha == current:
        print(f"{Fore.YELLOW}ALGORITHM_VERSION already points at {target_sha} - nothing to do.{Style.RESET_ALL}")
        return

    version_file.write_text(f"{target_sha}\n")
    print(f"{Fore.GREEN}ALGORITHM_VERSION -> {target_sha} ({target.production_label}, db:{target.id}){Style.RESET_ALL}")

    # Demote any newer AlgorithmVersion rows so the reader path
    # (get_active_scores_version: highest id with non-empty git_message) resolves
    # back to the target. Otherwise the writer points at the target but the
    # assessment tab / API keep surfacing the newer version.
    from database.models.core import AlgorithmVersion
    newer = list(
        AlgorithmVersion.select()
        .where(
            (AlgorithmVersion.id > target.id)
            & (AlgorithmVersion.git_message.is_null(False))
            & (AlgorithmVersion.git_message != '')
            & (~(AlgorithmVersion.git_commit.startswith('shadow/')))
        )
        .order_by(AlgorithmVersion.id)
    )
    if newer:
        for row in newer:
            prior_msg = row.git_message
            row.git_message = None
            row.save()
            print(f"{Fore.GREEN}Demoted {row.production_label} db:{row.id} ({row.git_commit}) - cleared git_message \"{prior_msg}\"{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}  (score rows under those versions are preserved - only the reader pointer moved){Style.RESET_ALL}")

    active_now = AlgorithmVersion.get_active_scores_version()
    print()
    print(f"  Reader (get_active_scores_version) now resolves to: {active_now.production_label} db:{active_now.id}  {active_now.git_commit}")
    print()
    print(f"{Fore.YELLOW}Reminder:{Style.RESET_ALL} this flips the reader pointer only — the API "
          f"now serves {target.production_label}'s existing score rows. The primary `trader update` "
          f"writer still scores from the working-tree code under the current HEAD version, not the "
          f"{target.production_label} silo. To keep writing {target.production_label}-equivalent rows, "
          f"include its silo in the scoring cadence (runtime loads it as a sidecar writer) — no "
          f"git-checkout of scoring files is needed.")
