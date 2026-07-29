from __future__ import annotations

import json
import pickle
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

from .config import BacktestConfig
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .qlib_challenger import (
    QlibPanelDataset,
    build_challenger_dataset,
    challenger_universe,
    evaluate_forward_observations,
)
from .signals import SignalDecision, write_signal_artifact

QLIB_CHALLENGER_ACCOUNT = "qlib_gru_shadow"
QLIB_CHALLENGER_STRATEGY = "qlib_gru_alpha360_v1"


@dataclass(frozen=True)
class ChallengerDailyResult:
    trade_date: str
    status: str
    model_id: str
    selected: list[str]
    scores: list[dict[str, object]]
    executions: list[dict[str, object]]
    orders: list[dict[str, object]]
    portfolio: dict[str, object]
    reconciliation: dict[str, object]
    report_path: str


def run_qlib_challenger_daily(
    *,
    root: Path,
    store: ParquetStore,
    broker: PaperBroker,
    config: BacktestConfig,
    trade_date: str,
    signal_dir: Path,
    report_dir: Path,
) -> ChallengerDailyResult:
    challenger_config = yaml.safe_load(
        (root / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
    )
    model_id = str(challenger_config["model_id"])
    model_path = root / "state" / "qlib-challenger" / "models" / f"{model_id}.pkl"
    ensemble_path = (
        root
        / "state"
        / "qlib-challenger"
        / "models"
        / f"{model_id}_ensemble.json"
    )
    research_path = root / "state" / "qlib-challenger" / "latest-research.json"
    broker.initialize_account(config.initial_cash, QLIB_CHALLENGER_ACCOUNT)
    if not (model_path.exists() or ensemble_path.exists()) or not research_path.exists():
        return _finish(
            trade_date,
            "AWAITING_TRAINING",
            model_id,
            [],
            [],
            [],
            [],
            broker.account_snapshot({}, QLIB_CHALLENGER_ACCOUNT),
            broker,
            report_dir,
        )
    research = json.loads(research_path.read_text(encoding="utf-8"))
    gru_metrics = next(
        (row for row in research["metrics"] if row["model_id"] == model_id),
        None,
    )
    gate = challenger_config["research_gate"]
    stability = research.get("stability", {})
    gate_passed = (
        gru_metrics is not None
        and int(gru_metrics["samples"]) >= int(gate["minimum_samples"])
        and float(gru_metrics["rank_ic"]) >= float(gate["minimum_rank_ic"])
        and float(gru_metrics["rank_ic_ir"]) >= float(gate["minimum_rank_ic_ir"])
        and float(gru_metrics.get("cost_adjusted_top_k_excess_return", -1))
        > float(gate["minimum_cost_adjusted_excess_return"])
        and int(stability.get("seed_count", 0)) >= int(gate["minimum_seed_count"])
        and float(stability.get("positive_seed_ratio", 0))
        >= float(gate["minimum_positive_seed_ratio"])
    )
    universe = challenger_universe(root, store)
    frame = build_challenger_dataset(
        store,
        universe,
        sequence_length=int(challenger_config["sequence_length"]),
        label_horizon=int(challenger_config["label_horizon"]),
        require_label=False,
        feature_count=int(challenger_config["model"]["d_feat"]),
    )
    cutoff = pd.Timestamp(trade_date)
    frame = frame.loc[
        frame.index.get_level_values("datetime") <= cutoff
    ]
    latest_date = frame.index.get_level_values("datetime").max()
    live_dataset = QlibPanelDataset(
        frame,
        {"live": (str(latest_date.date()), str(latest_date.date()))},
    )
    if ensemble_path.exists():
        manifest = json.loads(ensemble_path.read_text(encoding="utf-8"))
        ensemble_predictions = []
        for filename in manifest["models"]:
            with (ensemble_path.parent / filename).open("rb") as handle:
                model = pickle.load(handle)
            ensemble_predictions.append(model.predict(live_dataset, "live"))
        predictions = sum(ensemble_predictions) / len(ensemble_predictions)
    else:
        with model_path.open("rb") as handle:
            model = pickle.load(handle)
        predictions = model.predict(live_dataset, "live")
    score_frame = predictions.rename("score").reset_index()
    score_frame["datetime"] = pd.to_datetime(score_frame["datetime"]).dt.strftime(
        "%Y%m%d"
    )
    score_frame["sector"] = score_frame["instrument"].map(universe)
    top_k = int(challenger_config["top_k_per_sector"])
    selected, selection_metadata = _scheduled_selection(
        score_frame,
        model_id,
        trade_date,
        report_dir,
        top_k,
        int(challenger_config["exit_rank_per_sector"]),
        int(challenger_config["rebalance_interval"]),
    )
    if not gate_passed:
        result = _finish(
            trade_date,
            "OBSERVATION_ONLY",
            model_id,
            selected,
            score_frame.sort_values("score", ascending=False).to_dict("records"),
            [],
            [],
            broker.account_snapshot({}, QLIB_CHALLENGER_ACCOUNT),
            broker,
            report_dir,
            selection_metadata,
        )
        evaluate_forward_observations(
            root,
            store,
            int(challenger_config["label_horizon"]),
        )
        return result
    marks: dict[str, float] = {}
    bars: dict[str, ExecutionBar] = {}
    account_before = broker.account_snapshot({}, QLIB_CHALLENGER_ACCOUNT)
    held = {
        str(row["symbol"])
        for row in account_before["positions"]  # type: ignore[index]
    }
    for symbol in sorted(set(universe) | held):
        history = load_total_return_stock_bars(store, symbol)
        eligible = history.loc[
            pd.to_datetime(history["date"]) <= cutoff
        ]
        if eligible.empty:
            continue
        row = eligible.iloc[-1]
        marks[symbol] = float(row["raw_close"])
        if pd.Timestamp(row["date"]).strftime("%Y%m%d") == trade_date:
            bars[symbol] = ExecutionBar(
                symbol=symbol,
                trade_date=trade_date,
                open=float(row["raw_open"]),
                close=float(row["raw_close"]),
                can_buy=bool(row.get("can_buy", True)),
                can_sell=bool(row.get("can_sell", True)),
            )
    executions = []
    for symbol, bar in sorted(bars.items()):
        executions.extend(
            broker.execute_pending(bar, config, QLIB_CHALLENGER_ACCOUNT)
        )
    account = broker.account_snapshot(marks, QLIB_CHALLENGER_ACCOUNT)
    positions = {
        str(row["symbol"]): row for row in account["positions"]  # type: ignore[index]
    }
    target_weight = float(challenger_config["target_gross_exposure"]) / max(
        len(selected), 1
    )
    orders = []
    for symbol in sorted(set(selected) | set(positions)):
        close = marks.get(symbol)
        if close is None:
            continue
        target = target_weight if symbol in selected else 0.0
        decision = SignalDecision(
            strategy_id=model_id,
            symbol=symbol,
            as_of_date=trade_date,
            target_weight=target,
            action="HOLD" if target else "EXIT",
            reason_code="QLIB_GRU_TOPK" if target else "QLIB_GRU_EXIT",
            close=close,
            fast_ma=None,
            slow_ma=None,
            history_bars=int(challenger_config["sequence_length"]),
        )
        risk = create_order_intent(
            decision,
            PortfolioSnapshot(
                cash=float(account["cash"]),
                equity=float(account["equity"]),
                position_quantity=int(positions.get(symbol, {}).get("quantity", 0)),
                reference_price=close,
            ),
            lot_size=config.lot_size,
            max_symbol_weight=config.max_position_weight,
        )
        write_signal_artifact(decision, signal_dir)
        decision_status = broker.record_decision(decision)
        submit_status = (
            broker.submit(risk, QLIB_CHALLENGER_ACCOUNT)
            if decision_status in {"RECORDED", "DUPLICATE"}
            else "DUPLICATE_DECISION"
        )
        orders.append(
            {
                "symbol": symbol,
                "target_weight": target,
                "decision_status": decision_status,
                "submit_status": submit_status,
                "rejection_code": risk.rejection_code,
            }
        )
    portfolio = broker.account_snapshot(marks, QLIB_CHALLENGER_ACCOUNT)
    _record_account_history(store, trade_date, portfolio)
    reconciliation = asdict(broker.reconcile(QLIB_CHALLENGER_ACCOUNT))
    status = (
        "EXPERIMENTAL_PAPER"
        if reconciliation["matched"]
        else "FAILED_RECONCILIATION"
    )
    return _finish(
        trade_date,
        status,
        model_id,
        selected,
        score_frame.sort_values("score", ascending=False).to_dict("records"),
        executions,
        orders,
        portfolio,
        broker,
        report_dir,
        selection_metadata,
    )


def _scheduled_selection(
    score_frame: pd.DataFrame,
    model_id: str,
    trade_date: str,
    report_dir: Path,
    top_k: int,
    exit_rank: int,
    interval: int,
) -> tuple[list[str], dict[str, object]]:
    prior_reports = []
    if report_dir.exists():
        for path in sorted(report_dir.glob("*.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            if (
                report.get("model_id") == model_id
                and str(report.get("trade_date", "")) < trade_date
                and report.get("selected")
            ):
                prior_reports.append(report)
    previous = prior_reports[-1] if prior_reports else None
    days_since_rebalance = (
        int(previous.get("days_since_rebalance", 0)) + 1 if previous else interval
    )
    if previous and days_since_rebalance < interval:
        return sorted(previous["selected"]), {
            "rebalanced": False,
            "days_since_rebalance": days_since_rebalance,
            "days_until_rebalance": interval - days_since_rebalance,
        }
    incumbents = set(previous.get("selected", [])) if previous else set()
    selected = []
    for _, sector_frame in score_frame.groupby("sector"):
        ranking = sector_frame.sort_values("score", ascending=False).copy()
        ranking["rank"] = range(1, len(ranking) + 1)
        retained = ranking.loc[
            ranking["instrument"].isin(incumbents)
            & (ranking["rank"] <= exit_rank)
        ].head(top_k)
        additions = ranking.loc[
            ~ranking["instrument"].isin(set(retained["instrument"]))
        ].head(top_k - len(retained))
        selected.extend(pd.concat([retained, additions])["instrument"].astype(str))
    return sorted(selected), {
        "rebalanced": True,
        "days_since_rebalance": 0,
        "days_until_rebalance": interval,
    }


def _record_account_history(
    store: ParquetStore,
    trade_date: str,
    portfolio: dict[str, object],
) -> None:
    row = pd.DataFrame(
        [
            {
                "trade_date": trade_date,
                "account_id": QLIB_CHALLENGER_ACCOUNT,
                "cash": float(portfolio["cash"]),
                "market_value": float(portfolio["market_value"]),
                "equity": float(portfolio["equity"]),
            }
        ]
    )
    store.merge_curated(
        "qlib_challenger_account_daily",
        [row],
        ["trade_date", "account_id"],
    )


def _finish(
    trade_date: str,
    status: str,
    model_id: str,
    selected: list[str],
    scores: list[dict[str, object]],
    executions: list[dict[str, object]],
    orders: list[dict[str, object]],
    portfolio: dict[str, object],
    broker: PaperBroker,
    report_dir: Path,
    selection_metadata: dict[str, object] | None = None,
) -> ChallengerDailyResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{trade_date}.json"
    payload = {
        "trade_date": trade_date,
        "status": status,
        "model_id": model_id,
        "selected": selected,
        "scores": scores,
        "executions": executions,
        "orders": orders,
        "portfolio": portfolio,
        "reconciliation": asdict(broker.reconcile(QLIB_CHALLENGER_ACCOUNT)),
        "created_at": datetime.now(UTC).isoformat(),
        "report_path": str(target),
        **(selection_metadata or {}),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("status") != status:
            target = report_dir / f"{trade_date}_{status}.json"
            payload["report_path"] = str(target)
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not target.exists():
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    return ChallengerDailyResult(
        **{key: payload[key] for key in ChallengerDailyResult.__dataclass_fields__}
    )
