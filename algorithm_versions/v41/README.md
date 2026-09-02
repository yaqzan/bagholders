# v41 Algorithm Snapshot

- Status: `shipped`
- DB version: `41`
- Commit: `917659c`
- Resolved commit: `917659cbbacb17d8022cb92db0f2c5acc31dfa21`
- Category: `scoring_algorithm`
- Scoring algorithm snapshot: `yes`
- Runtime-loadable for `trader update --score-versions`: `yes`
- Structured scoring config: `yes`

## Intended Difference

v40 scoring: SVD (Score Velocity Dampener)

AlgorithmVersion message: v40 scoring: SVD (Score Velocity Dampener)
Commit subject: v40 scoring: SVD (Score Velocity Dampener)

## Existing Documentation Hint

No dedicated section was found in `.claude/docs/version-history.md` or `.claude/docs/version-history-archive.md`; use commit metadata and diffs below.

## Code Delta From Previous Resolved Version

Previous resolved key: `v40`
Diff range: `917659cb..917659cb`

No changed snapshot-tracked paths recorded.

### Structured Scoring Variable Delta

No structured changes detected.

### Structured Portfolio Variable Delta

No structured changes detected.

## Source References

- `.claude/docs/version-history-archive.md:968` - ## v40 (`917659c`) - 2026-05-06 (REVERTED 2026-05-07)
- `.claude/docs/version-history-archive.md:1016` - git cherry-pick 917659c 146c3cd
- `.claude/docs/version-history-archive.md:1275` - f89dde0 Revert "Bump ALGORITHM_VERSION to 917659c (v40 SVD)"
- `.claude/docs/version-history-archive.md:1291` - **SHIPPED then REVERTED 2026-05-07.
- `.claude/docs/known-issues.md:200` - **v40 SVD also REVERTED on 2026-05-07** (after v42 revert, user-directed).
- `.claude/docs/known-issues.md:869` - **Status: REVERTED.** v40 SVD was active for ~24h.
- `.claude/docs/version-history.md:1024` - ## v40 (`917659c`) - 2026-05-06 (REVERTED 2026-05-07)
- `.claude/docs/version-history.md:1072` - git cherry-pick 917659c 146c3cd
- `.claude/docs/version-history.md:1331` - f89dde0 Revert "Bump ALGORITHM_VERSION to 917659c (v40 SVD)"
- `.claude/docs/version-history.md:1347` - **SHIPPED then REVERTED 2026-05-07.
- `.claude/docs/known-issues.md:83` - **v40 SVD also REVERTED on 2026-05-07** (after v42 revert, user-directed).
- `.claude/docs/known-issues.md:611` - **Status: REVERTED.** v40 SVD was active for ~24h.
- `.claude/docs/known-issues.md:827` - \\| ~~v40 scoring: SVD~~ \\| ~~v40 / 2026-05-06~~ \\| **REVERTED 2026-05-07** along with v42 rolling weekly.
- `.claude/docs/known-issues.md:914` - \\| v40 SVD (Score Velocity Dampener) \\| REVERTED 2026-05-07 in concert with v42 rollback to a known-stable v39 baseline.

## Agent Pointers

- `manifest.json` - snapshot metadata and source fingerprints.
- `diff_from_previous.json` - source-file delta against the previous resolved version.
- `scoring_config.json` - structured scoring variables when available.
- `portfolio_snapshot.json` - portfolio/run variables captured with this algorithm.
- `scoring/` - copied scoring source files.
- `portfolio_sources/` - copied portfolio source files.
