import json
from pathlib import Path

from moneymore.qlib_governance import (
    bootstrap_qlib_release,
    promote_qlib_candidate,
    register_qlib_candidate,
    transition_release,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    (tmp_path / "state" / "qlib-challenger" / "models").mkdir(parents=True)
    (tmp_path / "configs" / "qlib_challenger.yaml").write_text(
        """
model_id: model_v1
test:
  end: "2026-07-26"
""",
        encoding="utf-8",
    )
    (tmp_path / "state" / "qlib-challenger" / "models" / "model_v1_ensemble.json").write_text(
        json.dumps({"models": ["one.pkl"]}), encoding="utf-8"
    )
    return tmp_path


def test_bootstrap_release_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = bootstrap_qlib_release(root)
    second = bootstrap_qlib_release(root)
    assert first["release_id"] == second["release_id"]
    assert len(second["releases"]) == 1
    assert second["execution_mode"] == "PAPER_TRADING"


def test_release_can_degrade_to_observation_only(tmp_path: Path) -> None:
    root = _project(tmp_path)
    current = bootstrap_qlib_release(root)
    degraded = transition_release(
        root,
        release_id=current["release_id"],
        lifecycle="DEGRADED",
        execution_mode="OBSERVATION_ONLY",
        reason="DRIFT",
        automatic=True,
    )
    assert degraded["lifecycle"] == "DEGRADED"
    assert degraded["execution_mode"] == "OBSERVATION_ONLY"
    assert degraded["transitions"][0]["transition_type"] == "AUTOMATIC"


def test_candidate_requires_gate_before_atomic_promotion(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bootstrap_qlib_release(root)
    artifact = root / "candidate.json"
    artifact.write_text('{"models":["two.pkl"]}', encoding="utf-8")
    candidate = register_qlib_candidate(
        root, model_id="model_v2", artifact_path=artifact, data_cutoff="20260730"
    )
    try:
        promote_qlib_candidate(
            root, release_id=candidate["release_id"], gate_passed=False, reason="TEST"
        )
    except ValueError:
        pass
    else:
        raise AssertionError("promotion should have been rejected")
    promoted = promote_qlib_candidate(
        root, release_id=candidate["release_id"], gate_passed=True, reason="GATE_PASS"
    )
    assert promoted["release_id"] == candidate["release_id"]
    assert promoted["lifecycle"] == "ACTIVE"
