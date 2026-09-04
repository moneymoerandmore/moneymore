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
        capture.strftime("%Y-%m-%d"),
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
    try:
        from .strategy_universe import active_strategy_universe

        active = active_strategy_universe(store, captured_at.strftime("%Y%m%d"))
        for row in active.to_dict("records"):
            rows.append(
                {
                    "universe": str(row["universe"]),
                    "symbol": str(row["symbol"]),
                    "source": "MARKET_CAP_TOP1000",
                    "source_fund": None,
                    "source_weight": None,
                    "disclosure_date": str(row["market_data_date"]),
                    "captured_at": captured_at.strftime("%Y%m%d"),
                    "available_from": captured_at.strftime("%Y%m%d"),
                    "valid_to": None,
                    "point_in_time": True,
                    "evidence_status": "FORWARD_VALID_FROM_CAPTURE",
                }
            )
        return pd.DataFrame(rows)
    except (FileNotFoundError, ValueError):
        pass
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
    end_date: str | None = None,
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
        "qmt_ocfps",
        "qmt_bps",
        "qmt_eps",
        "qmt_roe",
        "qmt_gross_margin",
        "qmt_revenue_growth",
        "qmt_net_profit_growth",
        "qmt_sales_cash_flow",
        "qmt_gear_ratio",
        "qmt_inventory_turnover",
        "shareholder_count",
        "shareholder_count_change",
        "top10_float_ratio",
    ]
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) if end_date is not None else None
    if end is not None and start == end:
        return _build_feature_snapshot(store, symbols, start, columns)
    panels = []
    for symbol in symbols:
        features = load_point_in_time_features(store, symbol)
        selected = features.loc[
            (pd.to_datetime(features["date"]) >= start)
            & (
                True
                if end is None
                else pd.to_datetime(features["date"]) <= end
            ),
            [column for column in columns if column in features],
        ].copy()
        selected["as_of_date"] = pd.to_datetime(selected.pop("date")).dt.strftime(
            "%Y%m%d"
        )
        selected["market_available_rule"] = "T_PLUS_ONE"
        selected["financial_available_rule"] = "ANNOUNCEMENT_T_PLUS_ONE"
        panels.append(selected)
    return pd.concat(panels, ignore_index=True).sort_values(["as_of_date", "symbol"])


def _build_feature_snapshot(
    store: ParquetStore,
    symbols: list[str],
    as_of: pd.Timestamp,
    columns: list[str],
) -> pd.DataFrame:
    """Materialize one live cross-section with one read per source table."""
    requested = sorted(set(symbols))
    date_key = as_of.strftime("%Y%m%d")
    daily = store.read(
        "daily",
        columns=["ts_code", "trade_date", "close"],
        filters=[("ts_code", "in", requested), ("trade_date", "=", date_key)],
    ).rename(
        columns={"ts_code": "symbol", "trade_date": "as_of_date", "close": "signal_close"}
    )
    if daily.empty:
        return pd.DataFrame(columns=["as_of_date", *columns[1:]])

    basic_columns = [
        "dv_ttm", "pb", "pe_ttm", "turnover_rate", "volume_ratio", "total_mv", "circ_mv"
    ]
    basic = store.read(
        "daily_basic",
        columns=["ts_code", "trade_date", *basic_columns],
        filters=[("ts_code", "in", requested)],
    ).rename(columns={"ts_code": "symbol"})
    basic = (
        basic.loc[basic["trade_date"].astype(str) < date_key]
        .sort_values(["symbol", "trade_date"])
        .groupby("symbol", as_index=False)
        .tail(1)
    )
    result = daily.merge(basic[["symbol", *basic_columns]], on="symbol", how="left")

    financial_columns = ["roe", "ocfps", "debt_to_assets", "netprofit_yoy", "q_sales_yoy"]
    financial = store.read(
        "fina_indicator",
        columns=["ts_code", "ann_date", "end_date", *financial_columns],
        filters=[("ts_code", "in", requested)],
    ).rename(columns={"ts_code": "symbol"})
    financial["available_date"] = pd.to_datetime(
        financial["ann_date"], format="%Y%m%d"
    ) + pd.offsets.Day(1)
    financial = (
        financial.loc[financial["available_date"] <= as_of]
        .sort_values(["symbol", "available_date", "end_date"])
        .groupby("symbol", as_index=False)
        .tail(1)
    )
    result = result.merge(
        financial[["symbol", *financial_columns]], on="symbol", how="left"
    )
    result = _merge_latest_qmt_features(store, result, requested, as_of)
    result["market_available_rule"] = "T_PLUS_ONE"
    result["financial_available_rule"] = "ANNOUNCEMENT_T_PLUS_ONE"
    return result.sort_values(["as_of_date", "symbol"]).reset_index(drop=True)


def _merge_latest_qmt_features(
    store: ParquetStore,
    frame: pd.DataFrame,
    symbols: list[str],
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    result = frame
    specifications = (
        (
            "qmt_financial_indicator",
            [
                "qmt_ocfps", "qmt_bps", "qmt_eps", "qmt_roe",
                "qmt_gross_margin", "qmt_revenue_growth", "qmt_net_profit_growth",
                "qmt_sales_cash_flow", "qmt_gear_ratio", "qmt_inventory_turnover",
            ],
        ),
        ("qmt_holder_count", ["shareholder_count"]),
        ("qmt_top10_concentration", ["top10_ratio"]),
    )
    for table, value_columns in specifications:
        try:
            source = store.read(table, filters=[("ts_code", "in", symbols)]).copy()
        except FileNotFoundError:
            continue
        if table == "qmt_top10_concentration":
            source = source.loc[source["holder_type"].astype(str) == "float"].copy()
            source = source.rename(columns={"top10_ratio": "top10_float_ratio"})
            value_columns = ["top10_float_ratio"]
        source["available_date"] = pd.to_datetime(
            source["ann_date"], format="%Y%m%d"
        ) + pd.offsets.Day(1)
        source = source.loc[source["available_date"] <= as_of]
        source = (
            source.sort_values(["ts_code", "available_date", "end_date"])
            .groupby("ts_code", as_index=False)
            .tail(1)
            .rename(columns={"ts_code": "symbol"})
        )
        if table == "qmt_holder_count":
            history = store.read(table, filters=[("ts_code", "in", symbols)]).copy()
            history["available_date"] = pd.to_datetime(
                history["ann_date"], format="%Y%m%d"
            ) + pd.offsets.Day(1)
            history = history.loc[history["available_date"] <= as_of].sort_values(
                ["ts_code", "available_date", "end_date"]
            )
            history["shareholder_count_change"] = history.groupby("ts_code")[
                "shareholder_count"
            ].pct_change(fill_method=None)
            source = (
                history.groupby("ts_code", as_index=False)
                .tail(1)
                .rename(columns={"ts_code": "symbol"})
            )
            value_columns = ["shareholder_count", "shareholder_count_change"]
        result = result.merge(
            source[["symbol", *value_columns]], on="symbol", how="left"
        )
    return result


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
