from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import BacktestConfig
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .signals import SignalDecision, write_signal_artifact

MULTI_SECTOR_ACCOUNT = "multi_sector_shadow"
MULTI_SECTOR_STRATEGY = "multi_sector_dynamic_v1"


@dataclass(frozen=True)
class MultiSectorDailyResult:
    trade_date: str
    status: str
    target_weights: dict[str, float]
    symbol_sectors: dict[str, str]
    executions: list[dict[str, object]]
    orders: list[dict[str, object]]
    portfolio: dict[str, object]
    attribution: list[dict[str, object]]
    reconciliation: dict[str, object]
    report_path: str


def expand_sleeve_targets(recommendation: object) -> tuple[
    dict[str, float], dict[str, str]
]:
    target_weights: dict[str, float] = {}
    symbol_sectors: dict[str, str] = {}
    for row in recommendation.to_dict("records"):  # type: ignore[attr-defined]
        symbols = [item for item in str(row["selected"]).split(",") if item]
        if not symbols:
            continue
        weight = float(row["target_weight"]) / len(symbols)
        for symbol in symbols:
            target_weights[symbol] = weight
            symbol_sectors[symbol] = str(row["sector"])
    return target_weights, symbol_sectors


def run_multi_sector_daily(
    store: ParquetStore,
    broker: PaperBroker,
    config: BacktestConfig,
    trade_date: str,
    signal_dir: str | Path,
    report_dir: str | Path,
) -> MultiSectorDailyResult:
    broker.initialize_account(config.initial_cash, MULTI_SECTOR_ACCOUNT)
    recommendation = store.read("sector_portfolio_recommendation")
    target_weights, symbol_sectors = expand_sleeve_targets(recommendation)

    account_before = broker.account_snapshot({}, MULTI_SECTOR_ACCOUNT)
    position_symbols = {
        str(row["symbol"]) for row in account_before["positions"]  # type: ignore[index]
    }
    symbols = sorted(set(target_weights) | position_symbols)
    bars: dict[str, ExecutionBar] = {}
    marks: dict[str, float] = {}
    for symbol in symbols:
        history = load_total_return_stock_bars(store, symbol)
        eligible = history.loc[
            history["date"].astype(str).str.replace("-", "").str[:8] <= trade_date
        ]
        if eligible.empty:
            continue
        row = eligible.iloc[-1]
        marks[symbol] = float(row["raw_close"])
        if str(row["date"]).replace("-", "")[:8] == trade_date:
            bars[symbol] = ExecutionBar(
                symbol=symbol,
                trade_date=trade_date,
                open=float(row["raw_open"]),
                close=float(row["raw_close"]),
                can_buy=bool(row.get("can_buy", True)),
                can_sell=bool(row.get("can_sell", True)),
            )

    pending_sells = {
        str(row["symbol"])
        for row in broker.orders()
        if row.get("account_id") == MULTI_SECTOR_ACCOUNT
        and row.get("status") == "PENDING"
        and row.get("side") == "SELL"
    }
    execution_order = sorted(bars, key=lambda symbol: symbol not in pending_sells)
    executions = []
    for symbol in execution_order:
        executions.extend(
            broker.execute_pending(
                bars[symbol], config, account_id=MULTI_SECTOR_ACCOUNT
            )
        )

    account = broker.account_snapshot(marks, MULTI_SECTOR_ACCOUNT)
    positions = {
        str(row["symbol"]): row for row in account["positions"]  # type: ignore[index]
    }
    orders = []
    for symbol in sorted(set(target_weights) | set(positions)):
        close = marks.get(symbol)
        if close is None:
            continue
        target = float(target_weights.get(symbol, 0))
        sector = symbol_sectors.get(symbol, "exit")
        decision = SignalDecision(
            strategy_id=MULTI_SECTOR_STRATEGY,
            symbol=symbol,
            as_of_date=trade_date,
            target_weight=target,
            action="HOLD" if target > 0 else "EXIT",
            reason_code=f"{sector.upper()}_TARGET" if target > 0 else "PORTFOLIO_EXIT",
            close=close,
            fast_ma=None,
            slow_ma=None,
            history_bars=0,
        )
        position = positions.get(symbol, {})
        risk = create_order_intent(
            decision,
            PortfolioSnapshot(
                cash=float(account["cash"]),
                equity=float(account["equity"]),
                position_quantity=int(position.get("quantity", 0)),
                reference_price=close,
            ),
            lot_size=config.lot_size,
            max_symbol_weight=config.max_position_weight,
        )
        write_signal_artifact(decision, signal_dir)
        decision_status = broker.record_decision(decision)
        submit_status = (
            broker.submit(risk, MULTI_SECTOR_ACCOUNT)
            if decision_status == "RECORDED"
            else "DUPLICATE_DECISION"
        )
        orders.append(
            {
                "symbol": symbol,
                "sector": sector,
                "target_weight": target,
                "decision_status": decision_status,
                "submit_status": submit_status,
                "rejection_code": risk.rejection_code,
            }
        )

    portfolio = broker.account_snapshot(marks, MULTI_SECTOR_ACCOUNT)
    attribution = _sleeve_attribution(portfolio, marks, symbol_sectors)
    reconciliation = asdict(broker.reconcile(MULTI_SECTOR_ACCOUNT))
    status = "COMPLETED" if reconciliation["matched"] else "FAILED_RECONCILIATION"
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{trade_date}.json"
    payload = {
        "trade_date": trade_date,
        "status": status,
        "target_weights": target_weights,
        "symbol_sectors": symbol_sectors,
        "executions": executions,
        "orders": orders,
        "portfolio": portfolio,
        "attribution": attribution,
        "reconciliation": reconciliation,
        "created_at": datetime.now(UTC).isoformat(),
        "report_path": str(target),
    }
    if not target.exists():
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    else:
        existing = json.loads(target.read_text(encoding="utf-8"))
        payload["created_at"] = existing["created_at"]
    result = MultiSectorDailyResult(
        **{key: payload[key] for key in MultiSectorDailyResult.__dataclass_fields__}
    )
    if not reconciliation["matched"]:
        raise RuntimeError(f"multi-sector reconciliation failed: {result.report_path}")
    return result


def _sleeve_attribution(
    portfolio: dict[str, object],
    marks: dict[str, float],
    symbol_sectors: dict[str, str],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, float]] = {}
    equity = float(portfolio["equity"])
    for position in portfolio["positions"]:  # type: ignore[union-attr]
        symbol = str(position["symbol"])
        sector = symbol_sectors.get(symbol, "unmapped")
        quantity = int(position["quantity"])
        mark = float(marks.get(symbol, 0))
        market_value = quantity * mark
        cost = quantity * float(position["avg_cost"])
        bucket = grouped.setdefault(
            sector, {"market_value": 0.0, "cost": 0.0, "unrealized_pnl": 0.0}
        )
        bucket["market_value"] += market_value
        bucket["cost"] += cost
        bucket["unrealized_pnl"] += market_value - cost
    return [
        {
            "sector": sector,
            **values,
            "actual_weight": values["market_value"] / equity if equity else 0.0,
        }
        for sector, values in sorted(grouped.items())
    ]
