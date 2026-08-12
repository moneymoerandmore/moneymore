from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def latest_due_slot(now: datetime, weekday: int = 5, hour: int = 9) -> datetime:
    """Return the most recent weekly execution slot (Monday=0)."""
    local = now.astimezone(SHANGHAI)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    slot = start + timedelta(days=weekday)
    return slot if local >= slot else slot - timedelta(days=7)


class WeeklyTrainingService:
    def __init__(
        self,
        root: Path,
        database: Path | None = None,
        *,
        weekday: int = 5,
        hour: int = 9,
        poll_seconds: int = 60,
        retry_interval: timedelta = timedelta(hours=6),
    ) -> None:
        self.root = root
        self.database = database or root / "state" / "qlib-training.sqlite3"
        self.weekday = weekday
        self.hour = hour
        self.poll_seconds = poll_seconds
        self.retry_interval = retry_interval
        self.log_dir = root / "logs" / "training"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_training_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_key TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    pid INTEGER,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    log_path TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_weekly_training_key "
                "ON weekly_training_runs(schedule_key, id)"
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="moneymore-weekly-training", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        self.check_and_launch(datetime.now(SHANGHAI), source="STARTUP_RECOVERY")
        while not self._stop.wait(self.poll_seconds):
            self.check_and_launch(datetime.now(SHANGHAI), source="SCHEDULED_CHECK")

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        if sys.platform == "win32":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _latest(self, schedule_key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM weekly_training_runs WHERE schedule_key = ? "
                "ORDER BY id DESC LIMIT 1",
                (schedule_key,),
            ).fetchone()
        return dict(row) if row else None

    def _completed(self, schedule_key: str) -> bool:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT 1 FROM weekly_training_runs "
                "WHERE schedule_key = ? AND status = 'COMPLETED' LIMIT 1",
                (schedule_key,),
            ).fetchone()
        return row is not None

    def check_and_launch(self, now: datetime, source: str) -> int | None:
        due = latest_due_slot(now, self.weekday, self.hour)
        schedule_key = due.strftime("%Y-%m-%d")
        if self._completed(schedule_key):
            return None
        latest = self._latest(schedule_key)
        if latest and latest["status"] in {"FAILED", "INTERRUPTED"}:
            finished_at = latest.get("finished_at")
            if finished_at and now.astimezone(SHANGHAI) - datetime.fromisoformat(
                str(finished_at)
            ) < self.retry_interval:
                return None
        if latest and latest["status"] in {"QUEUED", "RUNNING"}:
            if self._pid_alive(latest.get("pid")):
                return None
            with sqlite3.connect(self.database) as connection:
                connection.execute(
                    "UPDATE weekly_training_runs SET status='INTERRUPTED', "
                    "finished_at=?, error=? WHERE id=?",
                    (
                        now.astimezone(SHANGHAI).isoformat(),
                        "recorded training process is no longer running",
                        latest["id"],
                    ),
                )

        log_path = self.log_dir / f"{schedule_key}.log"
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """
                INSERT INTO weekly_training_runs(
                    schedule_key, due_at, status, source, log_path
                ) VALUES (?, ?, 'QUEUED', ?, ?)
                """,
                (schedule_key, due.isoformat(), source, str(log_path)),
            )
            run_id = int(cursor.lastrowid)

        command = [
            sys.executable,
            str(self.root / "scripts" / "run_weekly_training.py"),
            "--run-id",
            str(run_id),
            "--database",
            str(self.database),
            "--log-path",
            str(log_path),
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        process = subprocess.Popen(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE weekly_training_runs SET pid=? WHERE id=?",
                (process.pid, run_id),
            )
        return run_id

    def status(self, limit: int = 10) -> dict[str, Any]:
        now = datetime.now(SHANGHAI)
        due = latest_due_slot(now, self.weekday, self.hour)
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM weekly_training_runs ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]
        return {
            "enabled": True,
            "schedule": "每周六 09:00",
            "timezone": "Asia/Shanghai",
            "latest_due_key": due.strftime("%Y-%m-%d"),
            "catch_up": True,
            "automatic_promotion": False,
            "runs": rows,
        }
