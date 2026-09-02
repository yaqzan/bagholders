# Algorithm Version Index

Agent entrypoint for algorithm-version comparison.

- Full guide: [algorithm_versions/VERSION_GUIDE.md](../../algorithm_versions/VERSION_GUIDE.md)
- Per-version docs: `algorithm_versions/vNN/README.md`
- Runtime compare/update hook: `trader update  # active v74 already covers the latest shipped snapshot`

## Recent Versions

| Version | Category | Runtime | Intent |
|---|---|---:|---|
| [v74](../../algorithm_versions/v74/README.md) | scoring_algorithm | yes | v74 lean scoring: retire post-pre_boost dampener tail (cont-echo, WVD, daily-volume, EARN_BOOST) |
| [v73](../../algorithm_versions/v73/README.md) | scoring_algorithm | yes | Merge algo-exp/v73-dampener-retire: retire WCF+ICH+CWCF+CSWC+SCW (honest ablation) |
| [v72](../../algorithm_versions/v72/README.md) | scoring_algorithm | yes | Merge algo-exp/wcf-score-ramp: WCF score-gate ramp (27/28 cliff smooth) |
| [v71](../../algorithm_versions/v71/README.md) | scoring_algorithm | yes | v71 scoring: integrity-audit honest fixes (F1-F4) + 4 mechanism retirements |
| [v70](../../algorithm_versions/v70/README.md) | scoring_algorithm | yes | v70 scoring: honest EARN_BOOST recalibration (pre7-weighted, both-barrier-gated) |
| [v69](../../algorithm_versions/v69/README.md) | scoring_algorithm | yes | scoring: weekly transition blend (point-in-time honest weekly, removes recalc look-ahead + smooths fakeout) |
| [v68](../../algorithm_versions/v68/README.md) | scoring_algorithm | no | VCBW scoring on TRUE v60 base (v68 candidate): Vol-Confidence Boundary Wave |
| [v67](../../algorithm_versions/v67/README.md) | scoring_algorithm | no | VCBW scoring candidate: Vol-Confidence Boundary Wave (Stage 1, pre-bump) |
| [v66](../../algorithm_versions/v66/README.md) | scoring_algorithm | yes | v66 scoring: apply weekly momentum envelope to v60 |
| [v65](../../algorithm_versions/v65/README.md) | scoring_algorithm | yes | v65 scoring: unify weekly partial context |
| [v64](../../algorithm_versions/v64/README.md) | db_linked_snapshot | yes | Fix v64 recalc signal sigma map |
| [v63](../../algorithm_versions/v63/README.md) | scoring_algorithm | yes | v63 scoring: add BB location taper candidate |
| [v62](../../algorithm_versions/v62/README.md) | scoring_algorithm | yes | Snapshot v62 MACD put wave candidate |
| [v61](../../algorithm_versions/v61/README.md) | scoring_algorithm | yes | v61 scoring: add weekly mature call guard |
| [v60](../../algorithm_versions/v60/README.md) | db_linked_snapshot | yes | Ship v60 r054 SCW and DD call cap candidate |
| [v59](../../algorithm_versions/v59/README.md) | scoring_algorithm | yes | v59 scoring: daily volume authority wave |
| [v58](../../algorithm_versions/v58/README.md) | scoring_algorithm | yes | Retune continuation echo weights for v57 WR7 utility |
| [v57](../../algorithm_versions/v57/README.md) | scoring_algorithm | yes | Ship direct Market Wave score transform |

Older versions are in the full guide. Early versions may be code-only snapshots when structured config files did not exist yet.
