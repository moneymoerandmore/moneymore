from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .data.research import load_point_in_time_features
from .data.store import ParquetStore


def materialize_point_in_time_store(
    root: Path,
    *,
    captured_date: str | None = None,
    feature_start: str = "2025-01-01",
) -> dict[str, Any]:
    store = ParquetStore(root / "data")
    capture = pd.Timestamp(captured_date or datetime.now(UTC).date())
    snapshots = build_constituent_snapshots(root, store, capture)
    snapshot_updates = _snapshot_updates(store, snapshots, capture)
    if not snapshot_updates.empty:
        store.merge_curated(
            "universe_constituent_snapshots",
            [snapshot_updates],
            ["universe", "symbol", "captured_at"],
        )
    feature_store = build_feature_store(
        store,
        sorted(snapshots["symbol"].unique()),
        feature_start,
    )
    store.merge_curated(
        "point_in_time_features",
        [feature_store],
        ["as_of_date", "symbol"],
    )
    manifest = {
        "status": "READY",
        "captured_at": capture.strftime("%Y%m%d"),
        "universe_snapshot_rows": len(snapshots),
        "feature_rows": len(feature_store),
        "feature_start": feature_start,
        "feature_end": (
            str(feature_store["as_of_date"].max()) if not feature_store.empty else None
        ),
        "source_fingerprint": source_fingerprint(store),
        "availability_rules": {
            "market": "T_PLUS_ONE",
            "financial": "ANNOUNCEMENT_T_PLUS_ONE",
            "constituent": "MAX_DISCLOSURE_AND_CAPTURE_DATE",
            "listing": "LIST_DATE_TO_DELIST_DATE",
        },
        "historical_replay_gate": audit_target_membership(
            root / "state" / "historical-pk", snapshots
        ),
    }
    target = root / "state" / "point-in-time" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return manifest


def build_constituent_snapshots(
    root: Path,
    store: ParquetStore,
    captured_at: pd.Timestamp,
) -> pd.DataFrame:
    config = yaml.safe_load(
        (root / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
    )
    disclosure = pd.Timestamp(str(config["disclosure_date"]))
    available_from = max(disclosure + pd.Timedelta(days=1), captured_at)
    rows: list[dict[str, object]] = []
    for universe, item in config["universes"].items():
        for symbol, weight in item["holdings"].items():
            rows.append(
                {
                    "universe": universe,
                    "symbol": symbol,
                    "source": "MANUAL_ETF_DISCLOSURE",
                    "source_fund": item["etf_code"],
                    "source_weight": float(weight),
                    "disclosure_date": disclosure.strftime("%Y%m%d"),
                    "captured_at": captured_at.strftime("%Y%m%d"),
                    "available_from": available_from.strftime("%Y%m%d"),
                    "valid_to": None,
                    "point_in_time": True,
                    "evidence_status": "FORWARD_VALID_FROM_CAPTURE",
                }
            )
    membership = store.read(
        "universe_membership", filters=[("universe", "==", "bank_cn")]
    )
    latest = membership.loc[
        pd.to_datetime(membership["date"]) == pd.to_datetime(membership["date"]).max()
    ]
    for row in latest.to_dict("records"):
        rows.append(
            {
                "universe": "bank",
                "symbol": str(row["ts_code"]),
                "source": "CURRENT_INDUSTRY_CLASSIFICATION",
                "source_fund": None,
                "source_weight": None,
                "disclosure_date": None,
                "captured_at": captured_at.strftime("%Y%m%d"),
                "available_from": captured_at.strftime("%Y%m%d"),
                "valid_to": None,
                "point_in_time": True,
                "evidence_status": "FORWARD_VALID_FROM_CAPTURE",
            }
        )
    return pd.DataFrame(rows)


def build_feature_store(
    store: ParquetStore,
    symbols: list[str],
    start_date: str,
) -> pd.DataFrame:
    columns = [
        "date",
        "symbol",
        "signal_close",
        "dv_ttm",
        "pb",
        "pe_ttm",
        "turnover_rate",
        "volume_ratio",
        "total_mv",
        "circ_mv",
        "roe",
        "ocfps",
        "debt_to_assets",
        "netprofit_yoy",
        "q_sales_yoy",
    ]
    panels = []
    for symbol in symbols:
        features = load_point_in_time_features(store, symbol)
        selected = features.loc[
            pd.to_datetime(features["date"]) >= pd.Timestamp(start_date),
            [column for column in columns if column in features],
        ].copy()
        selected["as_of_date"] = pd.to_datetime(selected.pop("date")).dt.strftime(
            "%Y%m%d"
        )
        selected["market_available_rule"] = "T_PLUS_ONE"
        selected["financial_available_rule"] = "ANNOUNCEMENT_T_PLUS_ONE"
        panels.append(selected)
    return pd.concat(panels, ignore_index=True).sort_values(["as_of_date", "symbol"])


def audit_target_membership(
    historical_dir: Path,
    snapshots: pd.DataFrame,
) -> dict[str, Any]:
    target_files = sorted(historical_dir.glob("*-orders.parquet"))
    tested = 0
    eligible = 0
    earliest = snapshots["available_from"].astype(str).min()
    intervals = {
        symbol: list(
            zip(
                group["available_from"].astype(str),
                group["valid_to"].fillna("99991231").astype(str),
                strict=False,
            )
        )
        for symbol, group in snapshots.groupby("symbol")
    }
    for path in target_files:
        frame = pd.read_parquet(path, columns=["signal_date", "symbol"])
        tested += len(frame)
        eligible += sum(
            any(start <= str(row.signal_date) <= end for start, end in intervals.get(str(row.symbol), []))
            for row in frame.itertuples()
        )
    coverage = eligible / tested if tested else 0.0
    return {
        "status": "PASS" if tested and eligible == tested else "BLOCKED",
        "tested_orders": tested,
        "eligible_orders": eligible,
        "coverage": coverage,
        "earliest_trustworthy_date": earliest,
        "reason": (
            "Historical orders predate locally captured constituent snapshots."
            if coverage < 1
            else "All orders use constituents known at the time."
        ),
    }


def source_fingerprint(store: ParquetStore) -> str:
    digest = hashlib.sha256()
    for table in (
        "daily",
        "adj_factor",
        "daily_basic",
        "fina_indicator",
        "instruments",
        "universe_membership",
    ):
        path = store.curated / f"{table}.parquet"
        digest.update(table.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _snapshot_updates(
    store: ParquetStore,
    candidate: pd.DataFrame,
    capture: pd.Timestamp,
) -> pd.DataFrame:
    try:
        existing = store.read("universe_constituent_snapshots")
    except FileNotFoundError:
        return candidate
    updates = []
    for universe, proposed in candidate.groupby("universe"):
        history = existing.loc[existing["universe"] == universe].copy()
        if history.empty:
            updates.append(proposed)
            continue
        latest_capture = history["captured_at"].astype(str).max()
        latest = history.loc[history["captured_at"].astype(str) == latest_capture]
        old_signature = set(
            zip(
                latest["symbol"].astype(str),
                latest["source_weight"].fillna(-1).astype(float),
                strict=False,
            )
        )
        new_signature = set(
            zip(
                proposed["symbol"].astype(str),
                proposed["source_weight"].fillna(-1).astype(float),
                strict=False,
            )
        )
        if old_signature == new_signature:
            continue
        closing = latest.copy()
        closing["valid_to"] = (capture - pd.Timedelta(days=1)).strftime("%Y%m%d")
        updates.extend([closing, proposed])
    return pd.concat(updates, ignore_index=True) if updates else candidate.iloc[0:0]
