from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelVersion:
    version_id: str
    model_id: str
    lifecycle: str
    evidence_stage: str
    data_cutoff: str
    universe_hash: str
    config_hash: str
    code_hash: str
    manifest_path: str
    created_at: str


class ModelRegistry:
    """Immutable model manifests and links from evidence to decisions."""

    def __init__(self, database: str | Path, manifest_dir: str | Path) -> None:
        self.database = Path(database)
        self.manifest_dir = Path(manifest_dir)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_versions (
                    version_id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    evidence_stage TEXT NOT NULL,
                    data_cutoff TEXT NOT NULL,
                    universe_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    evidence_stage TEXT NOT NULL,
                    artifact_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(version_id, artifact_type, artifact_key)
                );
                CREATE TABLE IF NOT EXISTS model_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL,
                    binding_type TEXT NOT NULL,
                    binding_key TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    symbol TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(binding_type, binding_key)
                );
                """
            )

    def register(
        self,
        *,
        model_id: str,
        lifecycle: str,
        evidence_stage: str,
        data_cutoff: str,
        universe: list[str],
        config_files: list[Path],
        code_files: list[Path],
        metadata: dict[str, Any] | None = None,
    ) -> ModelVersion:
        universe = sorted(set(universe))
        config_hashes = _file_hashes(config_files)
        code_hashes = _file_hashes(code_files)
        identity = {
            "model_id": model_id,
            "lifecycle": lifecycle,
            "evidence_stage": evidence_stage,
            "data_cutoff": data_cutoff,
            "universe": universe,
            "config_hashes": config_hashes,
            "code_hashes": code_hashes,
            "metadata": metadata or {},
        }
        digest = _digest(identity)
        version_id = f"{model_id}@{digest[:12]}"
        created_at = datetime.now(UTC).isoformat()
        manifest_path = self.manifest_dir / f"{version_id.replace('@', '__')}.json"
        manifest = {
            **identity,
            "version_id": version_id,
            "universe_hash": _digest(universe),
            "config_hash": _digest(config_hashes),
            "code_hash": _digest(code_hashes),
            "created_at": created_at,
        }
        content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["created_at"] = existing["created_at"]
            content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            if manifest_path.read_text(encoding="utf-8") != content:
                raise FileExistsError(f"immutable model manifest differs: {manifest_path}")
        else:
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(manifest_path)
        version = ModelVersion(
            version_id=version_id,
            model_id=model_id,
            lifecycle=lifecycle,
            evidence_stage=evidence_stage,
            data_cutoff=data_cutoff,
            universe_hash=manifest["universe_hash"],
            config_hash=manifest["config_hash"],
            code_hash=manifest["code_hash"],
            manifest_path=str(manifest_path),
            created_at=manifest["created_at"],
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO model_versions(
                    version_id, model_id, lifecycle, evidence_stage, data_cutoff,
                    universe_hash, config_hash, code_hash, manifest_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(asdict(version).values()),
            )
        return version

    def bind(
        self,
        version_id: str,
        binding_type: str,
        binding_key: str,
        trade_date: str,
        symbol: str | None = None,
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO model_bindings(
                    version_id, binding_type, binding_key, trade_date,
                    symbol, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    binding_type,
                    binding_key,
                    trade_date,
                    symbol,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def add_artifact(
        self,
        version_id: str,
        artifact_type: str,
        evidence_stage: str,
        artifact_key: str,
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO model_artifacts(
                    version_id, artifact_type, evidence_stage,
                    artifact_key, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    artifact_type,
                    evidence_stage,
                    artifact_key,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def snapshot(self, limit: int = 30) -> dict[str, list[dict[str, Any]]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            versions = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_versions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
            artifacts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_artifacts ORDER BY id DESC LIMIT ?",
                    (limit * 5,),
                )
            ]
            bindings = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_bindings ORDER BY id DESC LIMIT ?",
                    (limit * 10,),
                )
            ]
        return {"versions": versions, "artifacts": artifacts, "bindings": bindings}


def _file_hashes(paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in sorted(paths, key=lambda item: item.as_posix()):
        result[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _digest(value: Any) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(content).hexdigest()
