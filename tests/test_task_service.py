from pathlib import Path

from moneymore.web import TaskService


def test_task_configuration_is_persistent(tmp_path: Path):
    database = tmp_path / "service.sqlite3"
    service = TaskService(database)

    initial = service.config()
    assert initial["enabled"] is True
    assert initial["schedule"] == "每日 18:30"
    assert [row["symbol"] for row in service.candidates()] == [
        "600036.SH",
        "600900.SH",
    ]

    updated = service.update_config(False, 19, 5)
    reloaded = TaskService(database).config()

    assert updated["schedule"] == "每日 19:05"
    assert reloaded["enabled"] is False
    assert reloaded["hour"] == 19
    assert reloaded["minute"] == 5


def test_task_step_retries_and_resumes_completed_work(tmp_path: Path):
    service = TaskService(tmp_path / "service.sqlite3")
    calls = 0

    def flaky_operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return "ready"

    status, result = service._run_step(1, "20260729", "research", flaky_operation)

    assert status == "COMPLETED"
    assert result == "ready"
    assert [row["status"] for row in reversed(service.steps(run_id=1))] == [
        "FAILED",
        "COMPLETED",
    ]

    status, result = service._run_step(
        2,
        "20260729",
        "research",
        lambda: (_ for _ in ()).throw(AssertionError("must not run again")),
    )
    assert status == "SKIPPED_COMPLETED"
    assert result is None
    assert service.steps(run_id=2)[0]["attempt"] == 0


def test_notifications_are_deduplicated_and_acknowledged(tmp_path: Path):
    service = TaskService(tmp_path / "service.sqlite3")
    for _ in range(2):
        service._notify(
            "ERROR",
            "PIPELINE_FAILED",
            "failed",
            "failure detail",
            trade_date="20260729",
            run_id=7,
        )

    notifications = service.notifications()
    assert len(notifications) == 1
    assert notifications[0]["acknowledged"] is False

    acknowledged = service.acknowledge_notification(notifications[0]["id"])
    assert acknowledged["acknowledged"] is True
    assert acknowledged["acknowledged_at"]


def test_daily_run_preview_is_read_only_and_marks_resume_steps(tmp_path: Path):
    service = TaskService(tmp_path / "service.sqlite3")
    service._run_step(11, "20260729", "bank_pipeline", lambda: "done")

    preview = service.preview("20260729")

    assert preview["mutates_state"] is False
    assert preview["prior_run_count"] == 0
    assert preview["steps"] == [
        {"step_name": "bank_pipeline", "action": "SKIP_COMPLETED"},
        {"step_name": "composite_daily_basic", "action": "RUN"},
        {"step_name": "sector_research", "action": "RUN"},
        {"step_name": "multi_sector_execution", "action": "RUN"},
        {"step_name": "qlib_challenger_execution", "action": "RUN"},
    ]
