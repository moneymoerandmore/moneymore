from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from moneymore.weekly_training import (
    WeeklyTrainingService,
    latest_due_slot,
)

TZ = ZoneInfo("Asia/Shanghai")


def test_latest_due_slot_catches_up_previous_saturday_before_this_week_slot():
    monday = datetime(2026, 8, 10, 8, tzinfo=TZ)
    assert latest_due_slot(monday).strftime("%Y-%m-%d %H:%M") == "2026-08-08 09:00"


def test_latest_due_slot_uses_current_saturday_after_schedule():
    saturday = datetime(2026, 8, 15, 10, tzinfo=TZ)
    assert latest_due_slot(saturday).strftime("%Y-%m-%d %H:%M") == "2026-08-15 09:00"


def test_current_process_is_detected_as_alive():
    import os

    assert WeeklyTrainingService._pid_alive(os.getpid()) is True


def test_completed_due_week_is_not_launched_again(tmp_path: Path, monkeypatch):
    service = WeeklyTrainingService(tmp_path, tmp_path / "training.sqlite3")
    now = datetime(2026, 8, 12, 12, tzinfo=TZ)
    due = latest_due_slot(now)
    with __import__("sqlite3").connect(service.database) as connection:
        connection.execute(
            """
            INSERT INTO weekly_training_runs(
                schedule_key, due_at, status, source, finished_at
            ) VALUES (?, ?, 'COMPLETED', 'TEST', ?)
            """,
            (due.strftime("%Y-%m-%d"), due.isoformat(), now.isoformat()),
        )
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    assert service.check_and_launch(now, "TEST") is None


def test_recent_failure_is_throttled(tmp_path: Path, monkeypatch):
    service = WeeklyTrainingService(tmp_path, tmp_path / "training.sqlite3")
    now = datetime(2026, 8, 12, 12, tzinfo=TZ)
    due = latest_due_slot(now)
    with __import__("sqlite3").connect(service.database) as connection:
        connection.execute(
            """
            INSERT INTO weekly_training_runs(
                schedule_key, due_at, status, source, finished_at
            ) VALUES (?, ?, 'FAILED', 'TEST', ?)
            """,
            (due.strftime("%Y-%m-%d"), due.isoformat(), now.isoformat()),
        )
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    assert service.check_and_launch(now + timedelta(hours=1), "TEST") is None
