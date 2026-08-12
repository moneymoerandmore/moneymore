from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    args = parser.parse_args()
    args.log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(SHANGHAI).isoformat()
    with sqlite3.connect(args.database) as connection:
        connection.execute(
            "UPDATE weekly_training_runs SET status='RUNNING', pid=?, started_at=? WHERE id=?",
            (os.getpid(), started_at, args.run_id),
        )
    exit_code = 1
    error = None
    try:
        with args.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{started_at}] weekly candidate training started\n")
            log.flush()
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "research_qlib_challenger.py")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "MONEYMORE_CANDIDATE_TAG": datetime.fromisoformat(
                        started_at
                    ).strftime("%Y%m%d"),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            exit_code = completed.returncode
            if exit_code:
                error = f"research process exited with code {exit_code}"
    except Exception as exc:  # noqa: BLE001 - durable job boundary
        error = f"{type(exc).__name__}: {exc}"
    finished_at = datetime.now(SHANGHAI).isoformat()
    status = "COMPLETED" if exit_code == 0 and error is None else "FAILED"
    with sqlite3.connect(args.database) as connection:
        connection.execute(
            """
            UPDATE weekly_training_runs
            SET status=?, finished_at=?, exit_code=?, error=? WHERE id=?
            """,
            (status, finished_at, exit_code, error, args.run_id),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
