import json
from pathlib import Path

from moneymore.model_registry import ModelRegistry


def test_model_version_is_deterministic_and_binds_evidence(tmp_path: Path):
    config = tmp_path / "config.yaml"
    code = tmp_path / "model.py"
    config.write_text("top_k: 5\n", encoding="utf-8")
    code.write_text("MODEL = 'stable'\n", encoding="utf-8")
    registry = ModelRegistry(tmp_path / "registry.sqlite3", tmp_path / "manifests")

    first = registry.register(
        model_id="portfolio_v1",
        lifecycle="SHADOW",
        evidence_stage="FORWARD_PAPER",
        data_cutoff="20260729",
        universe=["B", "A"],
        config_files=[config],
        code_files=[code],
    )
    second = registry.register(
        model_id="portfolio_v1",
        lifecycle="SHADOW",
        evidence_stage="FORWARD_PAPER",
        data_cutoff="20260729",
        universe=["A", "B"],
        config_files=[config],
        code_files=[code],
    )
    registry.add_artifact(
        first.version_id,
        "RESEARCH_TABLE",
        "HISTORICAL_DIAGNOSTIC",
        "report:20260729",
    )
    registry.bind(
        first.version_id,
        "SIGNAL",
        "portfolio_v1:A:20260729",
        "20260729",
        "A",
    )

    assert first.version_id == second.version_id
    manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
    assert manifest["universe"] == ["A", "B"]
    snapshot = registry.snapshot()
    assert len(snapshot["versions"]) == 1
    assert snapshot["artifacts"][0]["evidence_stage"] == "HISTORICAL_DIAGNOSTIC"
    assert snapshot["bindings"][0]["version_id"] == first.version_id


def test_code_change_creates_new_model_version(tmp_path: Path):
    config = tmp_path / "config.yaml"
    code = tmp_path / "model.py"
    config.write_text("top_k: 5\n", encoding="utf-8")
    code.write_text("MODEL = 1\n", encoding="utf-8")
    registry = ModelRegistry(tmp_path / "registry.sqlite3", tmp_path / "manifests")
    first = registry.register(
        model_id="portfolio_v1",
        lifecycle="SHADOW",
        evidence_stage="FORWARD_PAPER",
        data_cutoff="20260729",
        universe=["A"],
        config_files=[config],
        code_files=[code],
    )
    code.write_text("MODEL = 2\n", encoding="utf-8")
    second = registry.register(
        model_id="portfolio_v1",
        lifecycle="SHADOW",
        evidence_stage="FORWARD_PAPER",
        data_cutoff="20260729",
        universe=["A"],
        config_files=[config],
        code_files=[code],
    )

    assert first.version_id != second.version_id
