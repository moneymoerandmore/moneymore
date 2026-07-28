from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


class ParquetStore:
    """Immutable raw snapshots plus deduplicated curated tables."""

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.curated = self.root / "processed"

    def save_snapshot(
        self,
        table: str,
        frame: pd.DataFrame,
        provider: str,
        key_columns: list[str],
        snapshot_key: str,
    ) -> Path:
        self.save_snapshots(
            table,
            [(snapshot_key, frame)],
            provider,
            key_columns,
        )
        return self.snapshot_path(table, provider, snapshot_key)

    def save_snapshots(
        self,
        table: str,
        snapshots: Iterable[tuple[str, pd.DataFrame]],
        provider: str,
        key_columns: list[str],
    ) -> list[Path]:
        """Persist many raw snapshots and update the curated table once."""
        captured_at = datetime.now(UTC)
        target_dir = self.raw / provider / table
        target_dir.mkdir(parents=True, exist_ok=True)
        new_frames: list[pd.DataFrame] = []
        paths: list[Path] = []
        for snapshot_key, frame in snapshots:
            snapshot = self.snapshot_path(table, provider, snapshot_key)
            paths.append(snapshot)
            if snapshot.exists():
                existing = pd.read_parquet(snapshot)
                if not _equivalent(existing, frame):
                    raise FileExistsError(
                        f"immutable snapshot already exists with different data: {snapshot}"
                    )
                continue
            _atomic_parquet_write(frame, snapshot)
            new_frames.append(frame)
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            manifest = {
                "table": table,
                "provider": provider,
                "snapshot_key": snapshot_key,
                "captured_at_utc": captured_at.isoformat(),
                "rows": len(frame),
                "sha256": digest,
                "file": snapshot.as_posix(),
            }
            snapshot.with_suffix(".json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        if new_frames:
            self.merge_curated(table, new_frames, key_columns)
        return paths

    def merge_curated(
        self,
        table: str,
        frames: Iterable[pd.DataFrame],
        key_columns: list[str],
    ) -> None:
        additions = list(frames)
        if not additions:
            return
        curated_path = self.curated / f"{table}.parquet"
        curated_path.parent.mkdir(parents=True, exist_ok=True)
        parts = additions
        if curated_path.exists():
            parts = [pd.read_parquet(curated_path), *additions]
        normalized = [
            part.dropna(axis=1, how="all") for part in parts if not part.empty
        ]
        if not normalized:
            return
        combined = pd.concat(normalized, ignore_index=True)
        combined = combined.drop_duplicates(key_columns, keep="last").sort_values(
            key_columns
        )
        _atomic_parquet_write(combined, curated_path)

    def curated_values(self, table: str, column: str) -> set[str]:
        path = self.curated / f"{table}.parquet"
        if not path.exists():
            return set()
        return set(pd.read_parquet(path, columns=[column])[column].astype(str))

    def snapshot_path(self, table: str, provider: str, snapshot_key: str) -> Path:
        return self.raw / provider / table / f"{snapshot_key}.parquet"

    def has_snapshot(self, table: str, provider: str, snapshot_key: str) -> bool:
        return self.snapshot_path(table, provider, snapshot_key).exists()

    def read_snapshot(
        self, table: str, provider: str, snapshot_key: str
    ) -> pd.DataFrame:
        path = self.snapshot_path(table, provider, snapshot_key)
        if not path.exists():
            raise FileNotFoundError(f"raw snapshot does not exist: {path}")
        return pd.read_parquet(path)

    def read(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: list[tuple[str, str, object]] | None = None,
    ) -> pd.DataFrame:
        path = self.curated / f"{table}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"curated table does not exist: {path}")
        return pd.read_parquet(path, columns=columns, filters=filters)


def _atomic_parquet_write(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _equivalent(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    return left.reset_index(drop=True).equals(right.reset_index(drop=True))
