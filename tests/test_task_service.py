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
