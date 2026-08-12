from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import BacktestConfig
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .execution_replay import ExecutionReplayResult, run_execution_replay
from .portfolio_constructor import (
    adjusted_close_panel,
    global_topk_portfolio,
    trailing_return_correlation,
)
from .qlib_challenger import QlibPanelDataset, build_challenger_dataset, challenger_universe
from .qlib_exposure import dynamic_target_exposure
from .research.sector_model import build_global_factor_panel


def run_historical_pk(
    root: Path,
    *,
    start_date: str = "2025-01-01",
    end_date: str = "2026-07-22",
) -> dict[str, object]:
    store = ParquetStore(root / "data")
    config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
    universe = challenger_universe(root, store)
    bars = build_execution_bars(store, universe, start_date, end_date)
    market_data_coverage = dict(bars.attrs.get("data_coverage", {}))
    factor_targets = build_factor_targets(store, start_date, end_date)
    qlib_targets = build_qlib_targets(root, store, universe, start_date, end_date)
    actions = build_corporate_actions(store, set(universe), start_date, end_date)

    output = root / "state" / "historical-pk"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for strategy_id, targets in (
        ("factor_champion_historical", factor_targets),
        ("qlib_gru_historical", qlib_targets),
    ):
        database = output / f"{strategy_id}.sqlite3"
        if database.exists():
            database.unlink()
        result = run_execution_replay(
            bars=bars,
            targets=targets,
            config=config,
            database=database,
            strategy_id=strategy_id,
            account_id=strategy_id,
            corporate_actions=actions,
        )
        _save_replay_frames(output, result)
        results.append(
            {
                "strategy_id": strategy_id,
                "summary": result.summary,
                "reconciliation": result.reconciliation,
                "equity_file": f"{strategy_id}-equity.parquet",
                "fills_file": f"{strategy_id}-fills.parquet",
            }
        )
    payload = {
        "status": "COMPLETED",
        "evidence_status": "CURRENT_CONSTITUENT_BIASED",
        "start_date": start_date,
        "end_date": end_date,
        "execution_engine": "PaperBroker",
        "data_coverage": {
            **market_data_coverage,
            "corporate_action_count": len(actions),
        },
        "rules": [
            "NEXT_OPEN",
            "ROUND_LOT",
            "MINIMUM_COMMISSION",
            "STAMP_DUTY",
            "TRANSFER_FEE",
            "SLIPPAGE",
            "T_PLUS_ONE",
            "SUSPENSION",
            "PRICE_LIMIT",
            "CASH_CONSTRAINT",
            "CORPORATE_ACTION",
        ],
        "strategies": results,
    }
    target = output / "latest.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return payload


def build_execution_bars(
    store: ParquetStore,
    universe: dict[str, str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    panels = []
    for symbol in universe:
        frame = load_total_return_stock_bars(store, symbol)
        panel = frame[["date", "symbol", "raw_open", "raw_close"]].rename(
            columns={"raw_open": "open", "raw_close": "close"}
        )
        panels.append(panel)
    bars = pd.concat(panels, ignore_index=True)
    bars["date"] = pd.to_datetime(bars["date"])
    mask = bars["date"].between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    bars = bars.loc[mask].copy()
    bars["trade_date"] = bars["date"].dt.strftime("%Y%m%d")
    try:
        limits = store.read("stock_limits").rename(
            columns={"trade_date": "limit_date", "ts_code": "symbol"}
        )
        limits["limit_date"] = limits["limit_date"].astype(str)
        bars = bars.merge(
            limits,
            left_on=["trade_date", "symbol"],
            right_on=["limit_date", "symbol"],
            how="left",
        )
    except FileNotFoundError:
        bars["up_limit"] = np.nan
        bars["down_limit"] = np.nan
    bars["can_buy"] = bars["up_limit"].isna() | (
        bars["open"] < bars["up_limit"] - 1e-8
    )
    bars["can_sell"] = bars["down_limit"].isna() | (
        bars["open"] > bars["down_limit"] + 1e-8
    )
    try:
        suspensions = store.read("suspensions")
        suspended = set(
            zip(
                suspensions["ts_code"].astype(str),
                suspensions["trade_date"].astype(str),
                strict=False,
            )
        )
        is_suspended = [
            (symbol, trade_date) in suspended
            for symbol, trade_date in zip(
                bars["symbol"], bars["trade_date"], strict=False
            )
        ]
        bars.loc[is_suspended, ["can_buy", "can_sell"]] = False
    except FileNotFoundError:
        pass
    result = bars[["date", "symbol", "open", "close", "can_buy", "can_sell"]].copy()
    result.attrs["data_coverage"] = {
        "bar_rows": len(result),
        "price_limit_rows": int(bars["up_limit"].notna().sum()),
        "price_limit_coverage": (
            float(bars["up_limit"].notna().mean()) if len(bars) else 0.0
        ),
        "suspension_records": (
            int(sum(is_suspended)) if "is_suspended" in locals() else 0
        ),
    }
    return result


def build_factor_targets(
    store: ParquetStore,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    root = store.root.parent
    config = yaml.safe_load(
        (root / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
    )
    symbols = set(store.read("bank_model_scores")["symbol"].astype(str))
    for definition in config["universes"].values():
        symbols.update(map(str, definition["holdings"]))
    selection = config["global_selection"]
    scores = build_global_factor_panel(store, sorted(symbols), selection["factors"])
    scores["date"] = pd.to_datetime(scores["date"])
    scores = scores.loc[
        scores["date"].between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    ]
    held: set[str] = set()
    rows = []
    universe = sorted(scores["symbol"].astype(str).unique())
    price_panel = adjusted_close_panel(store, universe)
    for date, day in scores.groupby("date", sort=True):
        correlation = trailing_return_correlation(
            price_panel, date, int(selection["correlation_lookback"])
        )
        selected, weights, _ = global_topk_portfolio(
            day,
            held,
            symbol_column="symbol",
            top_k=int(selection["top_k"]),
            exit_rank=int(selection["exit_rank"]),
            max_replacements=int(selection["max_replacements"]),
            minimum_weight=float(selection["minimum_weight"]),
            maximum_weight=float(selection["maximum_weight"]),
            correlation=correlation,
            correlation_penalty=float(selection["correlation_penalty"]),
            cluster_correlation_threshold=float(selection["cluster_correlation_threshold"]),
            maximum_cluster_members=int(selection["maximum_cluster_members"]),
        )
        held = set(selected)
        rows.extend(
            {"date": date, "symbol": symbol, "target_weight": weights.get(symbol, 0.0)}
            for symbol in universe
        )
    return pd.DataFrame(rows)


def build_qlib_targets(
    root: Path,
    store: ParquetStore,
    universe: dict[str, str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    config = yaml.safe_load(
        (root / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
    )
    frame = build_challenger_dataset(
        store,
        universe,
        sequence_length=int(config["sequence_length"]),
        label_horizon=int(config["label_horizon"]),
        require_label=False,
        feature_count=int(config["model"]["d_feat"]),
    )
    dataset = QlibPanelDataset(frame, {"test": (start_date, end_date)})
    model_dir = root / "state" / "qlib-challenger" / "models"
    manifest = json.loads(
        (model_dir / f"{config['model_id']}_ensemble.json").read_text(encoding="utf-8")
    )
    predictions = []
    for filename in manifest["models"]:
        with (model_dir / filename).open("rb") as handle:
            predictions.append(pickle.load(handle).predict(dataset, "test"))
    scores = (sum(predictions) / len(predictions)).rename("score").reset_index()
    scores["sector"] = scores["instrument"].map(universe)
    dates = sorted(pd.to_datetime(scores["datetime"].unique()))
    held: set[str] = set()
    policy = config["portfolio_policy"]
    interval = int(config["rebalance_interval"])
    previous_exposure: float | None = None
    price_panel = adjusted_close_panel(store, list(universe))
    rows = []
    for index, date in enumerate(dates):
        is_rebalance = index % interval == 0
        if is_rebalance:
            day = scores.loc[pd.to_datetime(scores["datetime"]) == date]
            selected, portfolio_weights, _ = global_topk_portfolio(
                day, held, symbol_column="instrument",
                top_k=int(policy["top_k"]), exit_rank=int(policy["exit_rank"]),
                max_replacements=int(policy["max_replacements"]),
                minimum_weight=float(policy["minimum_weight"]),
                maximum_weight=float(policy["maximum_weight"]),
                correlation=trailing_return_correlation(
                    price_panel, date, int(policy["correlation_lookback"])
                ),
                correlation_penalty=float(policy["correlation_penalty"]),
                cluster_correlation_threshold=float(policy["cluster_correlation_threshold"]),
                maximum_cluster_members=int(policy["maximum_cluster_members"]),
            )
            held = set(selected)
        if (
            not is_rebalance
            and config["exposure_policy"].get("method") == "fully_invested"
        ):
            continue
        selected_all = held
        exposure = dynamic_target_exposure(
            store,
            selected_all,
            date,
            config["exposure_policy"],
            previous_exposure=previous_exposure,
        )
        gross = float(exposure["target_gross_exposure"])
        previous_exposure = gross
        for symbol in universe:
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "target_weight": float(portfolio_weights.get(symbol, 0.0)) * gross,
                }
            )
    return pd.DataFrame(rows)


def build_corporate_actions(
    store: ParquetStore,
    symbols: set[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    dividends = store.read("dividend").copy()
    dividends = dividends.loc[dividends["ts_code"].astype(str).isin(symbols)]
    dividends["record_date"] = pd.to_datetime(dividends["record_date"], errors="coerce")
    dividends = dividends.loc[
        dividends["record_date"].between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    ]
    dividends = dividends.sort_values("ann_date").drop_duplicates(
        ["ts_code", "end_date", "record_date"], keep="last"
    )
    return pd.DataFrame(
        {
            "symbol": dividends["ts_code"].astype(str),
            "action_key": (
                dividends["ts_code"].astype(str)
                + ":"
                + dividends["end_date"].astype(str)
                + ":"
                + dividends["record_date"].dt.strftime("%Y%m%d")
            ),
            "record_date": dividends["record_date"],
            "cash_per_share": pd.to_numeric(
                dividends["cash_div_tax"], errors="coerce"
            ).fillna(0.0),
            "stock_ratio": (
                pd.to_numeric(dividends["stk_div"], errors="coerce").fillna(0.0)
                + pd.to_numeric(dividends["stk_bo_rate"], errors="coerce").fillna(0.0)
                + pd.to_numeric(dividends["stk_co_rate"], errors="coerce").fillna(0.0)
            ),
            "cash_pay_date": pd.to_datetime(dividends["pay_date"], errors="coerce"),
            "stock_list_date": pd.to_datetime(
                dividends["div_listdate"], errors="coerce"
            ),
        }
    )


def _save_replay_frames(output: Path, result: ExecutionReplayResult) -> None:
    result.equity.to_parquet(output / f"{result.strategy_id}-equity.parquet", index=False)
    pd.DataFrame(result.fills).to_parquet(
        output / f"{result.strategy_id}-fills.parquet", index=False
    )
    pd.DataFrame(result.orders).to_parquet(
        output / f"{result.strategy_id}-orders.parquet", index=False
    )
    (output / f"{result.strategy_id}-reconciliation.json").write_text(
        json.dumps(result.reconciliation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
