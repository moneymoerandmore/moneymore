from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.database) as connection:
        row = connection.execute(
            "SELECT schedule_key FROM weekly_training_runs WHERE id=?", (args.run_id,)
        ).fetchone()
    schedule_key = str(row[0]) if row else "unknown"
    tag = schedule_key.replace("-", "")
    started_at = datetime.now(SHANGHAI).isoformat()
    args.log_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.database) as connection:
        connection.execute(
            "UPDATE weekly_training_runs SET status='RUNNING',pid=?,started_at=? WHERE id=?",
            (os.getpid(), started_at, args.run_id),
        )
    error = None
    exit_code = 1
    try:
        with args.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{started_at}] weekly DeepDow exposure training started\n")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "train_deepdow_exposure.py"),
                 "--tag", tag, "--epochs", "100"],
                cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"},
                stdout=log, stderr=subprocess.STDOUT, text=True, check=False,
            )
            exit_code = result.returncode
        artifact = ROOT / "state" / "exposure-league" / "deepdow" / "candidates" / tag
        metadata_path, model_path = artifact / "metadata.json", artifact / "model.pt"
        if exit_code == 0 and metadata_path.exists() and model_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("status") != "COMPLETED" or metadata.get("epochs") != 100:
                error, exit_code = "DeepDow artifact validation failed", 1
        elif exit_code == 0:
            error, exit_code = "DeepDow model or metadata artifact is missing", 1
        if exit_code and error is None:
            error = f"DeepDow trainer exited with code {exit_code}"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finished_at = datetime.now(SHANGHAI).isoformat()
    status = "COMPLETED" if exit_code == 0 and error is None else "FAILED"
    with sqlite3.connect(args.database) as connection:
        connection.execute(
            "UPDATE weekly_training_runs SET status=?,finished_at=?,exit_code=?,error=? WHERE id=?",
            (status, finished_at, exit_code, error, args.run_id),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
