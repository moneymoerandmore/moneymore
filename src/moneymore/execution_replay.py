from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .signals import SignalDecision


@dataclass(frozen=True)
class ExecutionReplayResult:
    strategy_id: str
    account_id: str
    equity: pd.DataFrame
    orders: list[dict[str, object]]
    fills: list[dict[str, object]]
    attempts: list[dict[str, object]]
    reconciliation: dict[str, object]
    summary: dict[str, object]


def run_execution_replay(
    *,
    bars: pd.DataFrame,
    targets: pd.DataFrame,
    config: BacktestConfig,
    database: str | Path,
    strategy_id: str,
    account_id: str,
    corporate_actions: pd.DataFrame | None = None,
) -> ExecutionReplayResult:
    """Replay historical targets through the production PaperBroker.

    Required bar fields are date, symbol, open and close. Optional can_buy and
    can_sell fields model suspension and price-limit constraints. Targets are
    sparse rebalance instructions and require date, symbol and target_weight.
    """
    market = _prepare_bars(bars)
    instructions = _prepare_targets(targets)
    actions = _prepare_actions(corporate_actions)
    database = Path(database)
    if database.exists():
        raise FileExistsError(f"replay database already exists: {database}")
    broker = PaperBroker(database)
    broker.initialize_account(config.initial_cash, account_id)
    target_state: dict[str, float] = {}
    equity_rows: list[dict[str, object]] = []
    previous_equity = config.initial_cash

    for trade_date, day in market.groupby("date", sort=True):
        date_key = pd.Timestamp(trade_date).strftime("%Y%m%d")
        day = day.set_index("symbol")
        marks = {str(symbol): float(row["close"]) for symbol, row in day.iterrows()}

        broker.settle_corporate_actions(account_id, date_key)
        pending_sells = {
            str(row["symbol"])
            for row in broker.orders()
            if row["account_id"] == account_id
            and row["status"] == "PENDING"
            and row["side"] == "SELL"
        }
        execution_symbols = sorted(day.index.astype(str), key=lambda s: s not in pending_sells)
        for symbol in execution_symbols:
            row = day.loc[symbol]
            broker.execute_pending(
                ExecutionBar(
                    symbol=symbol,
                    trade_date=date_key,
                    open=float(row["open"]),
                    close=float(row["close"]),
                    can_buy=bool(row["can_buy"]),
                    can_sell=bool(row["can_sell"]),
                ),
                config,
                account_id,
            )

        if not actions.empty:
            today_actions = actions.loc[actions["record_date"] == pd.Timestamp(trade_date)]
            for action in today_actions.to_dict("records"):
                broker.register_corporate_entitlement(
                    account_id,
                    str(action["symbol"]),
                    str(action["action_key"]),
                    date_key,
                    float(action["cash_per_share"]),
                    float(action["stock_ratio"]),
                    _date_key(action["cash_pay_date"]),
                    _date_key(action["stock_list_date"]),
                )

        snapshot = broker.account_snapshot(marks, account_id)
        positions = {
            str(row["symbol"]): row for row in snapshot["positions"]  # type: ignore[index]
        }
        today_targets = instructions.loc[
            instructions["date"] == pd.Timestamp(trade_date)
        ]
        if not today_targets.empty:
            target_state.update(
                {
                    str(row["symbol"]): float(row["target_weight"])
                    for row in today_targets.to_dict("records")
                }
            )
            for symbol in sorted(set(target_state) | set(positions)):
                if symbol not in marks:
                    continue
                position = positions.get(symbol, {})
                decision = SignalDecision(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    as_of_date=date_key,
                    target_weight=float(target_state.get(symbol, 0.0)),
                    action="HOLD" if target_state.get(symbol, 0.0) > 0 else "EXIT",
                    reason_code="HISTORICAL_REPLAY_TARGET",
                    close=marks[symbol],
                    fast_ma=None,
                    slow_ma=None,
                    history_bars=0,
                )
                risk = create_order_intent(
                    decision,
                    PortfolioSnapshot(
                        cash=float(snapshot["cash"]),
                        equity=float(snapshot["equity"]),
                        position_quantity=int(position.get("quantity", 0)),
                        reference_price=marks[symbol],
                    ),
                    lot_size=config.lot_size,
                    max_symbol_weight=config.max_position_weight,
                )
                broker.record_decision(decision)
                broker.submit(risk, account_id)

        closing = broker.account_snapshot(marks, account_id)
        equity = float(closing["equity"])
        market_value = float(closing["market_value"])
        equity_rows.append(
            {
                "trade_date": date_key,
                "equity": equity,
                "cash": float(closing["cash"]),
                "market_value": market_value,
                "gross_exposure": market_value / equity if equity else 0.0,
                "daily_return": equity / previous_equity - 1.0,
            }
        )
        previous_equity = equity

    equity_frame = pd.DataFrame(equity_rows)
    if not equity_frame.empty:
        equity_frame["normalized_nav"] = equity_frame["equity"] / float(
            equity_frame["equity"].iloc[0]
        )
        equity_frame["drawdown"] = (
            equity_frame["normalized_nav"]
            / equity_frame["normalized_nav"].cummax()
            - 1.0
        )
    fills = broker.fills(account_id)
    orders = [
        row for row in broker.orders() if str(row["account_id"]) == account_id
    ]
    attempts = broker.execution_attempts(account_id=account_id)
    reconciliation = asdict(broker.reconcile(account_id))
    summary = _summary(equity_frame, orders, fills, attempts, reconciliation)
    return ExecutionReplayResult(
        strategy_id,
        account_id,
        equity_frame,
        orders,
        fills,
        attempts,
        reconciliation,
        summary,
    )


def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing bar columns: {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"])
    result["symbol"] = result["symbol"].astype(str)
    result["open"] = pd.to_numeric(result["open"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    for column in ("can_buy", "can_sell"):
        if column not in result:
            result[column] = True
        result[column] = result[column].astype("boolean").fillna(True).astype(bool)
    return result.dropna(subset=["date", "open", "close"]).sort_values(["date", "symbol"])


def _prepare_targets(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "target_weight"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing target columns: {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"])
    result["symbol"] = result["symbol"].astype(str)
    result["target_weight"] = pd.to_numeric(result["target_weight"], errors="coerce")
    if (result["target_weight"].dropna() < 0).any():
        raise ValueError("target weights must be non-negative")
    daily_gross = result.groupby("date")["target_weight"].sum()
    if (daily_gross > 1.0 + 1e-9).any():
        raise ValueError("daily target gross exposure cannot exceed 1")
    return result.dropna(subset=["date", "target_weight"]).sort_values(["date", "symbol"])


def _prepare_actions(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "action_key",
                "record_date",
                "cash_per_share",
                "stock_ratio",
                "cash_pay_date",
                "stock_list_date",
            ]
        )
    required = {
        "symbol",
        "action_key",
        "record_date",
        "cash_per_share",
        "stock_ratio",
        "cash_pay_date",
        "stock_list_date",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing corporate action columns: {sorted(missing)}")
    result = frame.copy()
    for column in ("record_date", "cash_pay_date", "stock_list_date"):
        result[column] = pd.to_datetime(result[column], errors="coerce")
    return result


def _date_key(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime("%Y%m%d")


def _summary(
    equity: pd.DataFrame,
    orders: list[dict[str, object]],
    fills: list[dict[str, object]],
    attempts: list[dict[str, object]],
    reconciliation: dict[str, Any],
) -> dict[str, object]:
    if equity.empty:
        return {"observation_days": 0, "reconciled": reconciliation["matched"]}
    returns = equity["equity"].pct_change().dropna()
    volatility = (
        float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else None
    )
    return {
        "start_date": str(equity.iloc[0]["trade_date"]),
        "end_date": str(equity.iloc[-1]["trade_date"]),
        "observation_days": len(equity),
        "total_return": float(equity.iloc[-1]["normalized_nav"] - 1.0),
        "annualized_volatility": volatility,
        "sharpe": (
            float(returns.mean() / returns.std(ddof=1) * np.sqrt(252))
            if len(returns) > 1 and returns.std(ddof=1) > 0
            else None
        ),
        "max_drawdown": float(equity["drawdown"].min()),
        "average_gross_exposure": float(equity["gross_exposure"].mean()),
        "order_count": len(orders),
        "fill_count": len(fills),
        "deferred_count": sum(
            str(row.get("outcome")) == "DEFERRED" for row in attempts
        ),
        "transaction_cost": float(sum(float(row["fee"]) for row in fills)),
        "reconciled": bool(reconciliation["matched"]),
    }
