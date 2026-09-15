from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import BacktestConfig
from .data.store import ParquetStore
from .execution.paper import PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .signals import SignalDecision, write_signal_artifact

BASELINE_EXPOSURE_ACCOUNT = "multi_sector_pysystemtrade_shadow"
BASELINE_EXPOSURE_STRATEGY = "multi_sector_dynamic_v1_pysystemtrade"
BASELINE_EXPOSURE_HISTORY = "multi_sector_pysystemtrade_account_daily"


@dataclass(frozen=True)
class BaselineExposureDailyResult:
    trade_date: str
    status: str
    target_exposure: float
    target_weights: dict[str, float]
    orders: list[dict[str, object]]
    portfolio: dict[str, object]
    reconciliation: dict[str, object]
    report_path: str


def run_baseline_exposure_daily(
    *, store: ParquetStore, broker: PaperBroker, config: BacktestConfig,
    trade_date: str, baseline_report: dict[str, object], target_exposure: float,
    signal_dir: Path, report_dir: Path,
) -> BaselineExposureDailyResult:
    """Run an isolated paper account using baseline picks scaled by PST exposure."""
    broker.initialize_account(config.initial_cash, BASELINE_EXPOSURE_ACCOUNT)
    base = {
        str(symbol): float(weight)
        for symbol, weight in dict(baseline_report.get("theoretical_target_weights") or baseline_report.get("target_weights") or {}).items()
    }
    exposure = min(1.0, max(0.0, float(target_exposure)))
    targets = {symbol: weight * exposure for symbol, weight in base.items()}
    before = broker.account_snapshot({}, BASELINE_EXPOSURE_ACCOUNT)
    held = {str(row["symbol"]) for row in before["positions"]}
    symbols = sorted(set(targets) | held)
    daily = store.read(
        "daily", columns=["ts_code", "trade_date", "close"],
        filters=[("trade_date", "<=", trade_date), ("ts_code", "in", symbols)],
    ) if symbols else pd.DataFrame()
    latest = daily.sort_values("trade_date").groupby("ts_code", as_index=False).tail(1) if not daily.empty else daily
    marks = {str(row["ts_code"]): float(row["close"]) for row in latest.to_dict("records")}
    account = broker.account_snapshot(marks, BASELINE_EXPOSURE_ACCOUNT)
    positions = {str(row["symbol"]): row for row in account["positions"]}
    orders: list[dict[str, object]] = []
    for symbol in symbols:
        price = marks.get(symbol)
        if not price:
            continue
        target = float(targets.get(symbol, 0.0))
        position = positions.get(symbol, {})
        decision = SignalDecision(
            strategy_id=BASELINE_EXPOSURE_STRATEGY, symbol=symbol,
            as_of_date=trade_date, target_weight=target,
            action="HOLD" if target > 0 else "EXIT",
            reason_code="BASELINE_PYSYSTEMTRADE_TARGET" if target > 0 else "PORTFOLIO_EXIT",
            close=price, fast_ma=None, slow_ma=None, history_bars=0,
        )
        risk = create_order_intent(
            decision,
            PortfolioSnapshot(
                cash=float(account["cash"]), equity=float(account["equity"]),
                position_quantity=int(position.get("quantity", 0)), reference_price=price,
            ),
            lot_size=config.lot_size, max_symbol_weight=config.max_position_weight,
        )
        write_signal_artifact(decision, signal_dir)
        decision_status = broker.record_decision(decision)
        submit_status = broker.submit(risk, BASELINE_EXPOSURE_ACCOUNT) if decision_status in {"RECORDED", "DUPLICATE"} else "DUPLICATE_DECISION"
        orders.append({"symbol": symbol, "target_weight": target, "decision_status": decision_status, "submit_status": submit_status, "rejection_code": risk.rejection_code})
    portfolio = broker.account_snapshot(marks, BASELINE_EXPOSURE_ACCOUNT)
    reconciliation = broker.reconcile(BASELINE_EXPOSURE_ACCOUNT).__dict__
    previous = None
    try:
        history = store.read(BASELINE_EXPOSURE_HISTORY).sort_values("trade_date")
        prior = history.loc[history["trade_date"].astype(str) < trade_date]
        if not prior.empty:
            previous = float(prior.iloc[-1]["equity"])
    except FileNotFoundError:
        pass
    equity = float(portfolio["equity"])
    store.merge_curated(
        BASELINE_EXPOSURE_HISTORY,
        [pd.DataFrame([{
            "trade_date": trade_date, "status": "COMPLETED", "equity": equity,
            "cash": float(portfolio["cash"]), "market_value": float(portfolio["market_value"]),
            "gross_exposure": float(portfolio["market_value"]) / equity if equity else 0.0,
            "target_exposure": exposure, "daily_return": equity / previous - 1 if previous else 0.0,
            "reconciled": bool(reconciliation["matched"]),
        }])], ["trade_date"],
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"{trade_date}.json"
    payload = {"trade_date": trade_date, "status": "COMPLETED", "target_exposure": exposure, "target_weights": targets, "orders": orders, "portfolio": portfolio, "reconciliation": reconciliation, "report_path": str(target)}
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return BaselineExposureDailyResult(**payload)
