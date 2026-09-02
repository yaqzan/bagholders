import algorithm_versions.runtime as runtime
from database.models.core import AlgorithmVersion


class FakeVersion:
    def __init__(self, version_id):
        self.id = version_id
        self.production_label = f"v{version_id}"


def test_all_token_expands_to_runtime_loadable_production_versions(monkeypatch):
    versions = [FakeVersion(1), FakeVersion(2), FakeVersion(3), FakeVersion(4)]
    loaded = []

    monkeypatch.setattr(
        AlgorithmVersion,
        "production_versions",
        classmethod(lambda cls: versions),
    )
    monkeypatch.setattr(
        AlgorithmVersion,
        "get_or_create_current",
        classmethod(lambda cls: FakeVersion(4)),
    )

    def fake_load_scoring_engine(version):
        loaded.append(version.id)
        if version.id == 2:
            raise RuntimeError("metadata-only version")
        return object()

    monkeypatch.setattr(runtime, "load_scoring_engine", fake_load_scoring_engine)

    assert [v.id for v in runtime.resolve_versions(["ALL"])] == [1, 3]
    assert loaded == [1, 2, 3]


def test_all_token_dedupes_explicit_versions(monkeypatch):
    versions = [FakeVersion(1), FakeVersion(3), FakeVersion(4)]

    monkeypatch.setattr(
        runtime,
        "runtime_loadable_production_versions",
        lambda: versions,
    )

    import assess_scores

    monkeypatch.setattr(
        assess_scores,
        "resolve_algorithm_version",
        lambda token: FakeVersion(int(str(token).lstrip("v"))),
    )

    assert [v.id for v in runtime.resolve_versions(["all", "v3"])] == [1, 3, 4]
