from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import BacktestConfig
from .costs import execution_price, transaction_fee
from .models import Fill, Side


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    fills: list[Fill]

    @property
    def total_return(self) -> float:
        return float(self.equity["equity"].iloc[-1] / self.equity["equity"].iloc[0] - 1)

    @property
    def max_drawdown(self) -> float:
        values = self.equity["equity"]
        return float((values / values.cummax() - 1).min())


def run_daily_backtest(signals: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
    """Long-only next-open simulator for one or more symbols.

    Required fields: date, symbol, open, close, target. Optional tradable defaults True.
    `target` observed on T is acted on at T+1 open.
    """
    required = {"date", "symbol", "open", "close", "target"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    frame = signals.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "symbol"])
    if "tradable" not in frame:
        frame["tradable"] = True

    dates = frame["date"].drop_duplicates().sort_values().tolist()
    symbols = sorted(frame["symbol"].unique())
    previous_targets = {symbol: 0.0 for symbol in symbols}
    executed_targets = {symbol: 0.0 for symbol in symbols}
    positions = {symbol: 0 for symbol in symbols}
    cash = config.initial_cash
    peak = config.initial_cash
    halted = False
    fills: list[Fill] = []
    equity_rows: list[dict[str, object]] = []

    for date in dates:
        day = frame[frame["date"] == date].set_index("symbol")
        opening_equity = cash + sum(
            positions[s] * float(day.loc[s, "open"])
            for s in symbols
            if s in day.index
        )

        for symbol in symbols:
            if symbol not in day.index or not bool(day.loc[symbol, "tradable"]):
                continue
            desired_weight = 0.0 if halted else min(
                float(previous_targets[symbol]),
                config.max_position_weight,
            )
            open_price = float(day.loc[symbol, "open"])
            current_qty = positions[symbol]
            if abs(desired_weight - executed_targets[symbol]) < 1e-12:
                continue
            if desired_weight <= 0 and current_qty:
                if "can_sell" in day.columns and not bool(day.loc[symbol, "can_sell"]):
                    continue
                price = execution_price(open_price, Side.SELL, config.slippage_bps)
                notional = current_qty * price
                fee = transaction_fee(notional, Side.SELL, config)
                cash += notional - fee
                fills.append(Fill(symbol, Side.SELL, current_qty, price, fee, str(date.date())))
                positions[symbol] = 0
                executed_targets[symbol] = 0.0
            elif desired_weight > 0:
                invested_at_open = sum(
                    positions[s] * float(day.loc[s, "open"])
                    for s in symbols
                    if s in day.index and s != symbol
                )
                gross_budget_remaining = max(
                    0.0, opening_equity * config.max_gross_exposure - invested_at_open
                )
                target_value = min(
                    opening_equity * desired_weight,
                    gross_budget_remaining,
                )
                target_quantity = (
                    int(target_value / open_price / config.lot_size) * config.lot_size
                )
                difference = target_quantity - current_qty
                if difference > 0:
                    if "can_buy" in day.columns and not bool(day.loc[symbol, "can_buy"]):
                        continue
                    price = execution_price(open_price, Side.BUY, config.slippage_bps)
                    quantity = difference
                    while quantity > 0:
                        notional = quantity * price
                        fee = transaction_fee(notional, Side.BUY, config)
                        if notional + fee <= cash:
                            break
                        quantity -= config.lot_size
                    if quantity <= 0:
                        continue
                    cash -= notional + fee
                    positions[symbol] += quantity
                    fills.append(Fill(symbol, Side.BUY, quantity, price, fee, str(date.date())))
                elif difference < 0:
                    if "can_sell" in day.columns and not bool(day.loc[symbol, "can_sell"]):
                        continue
                    quantity = abs(difference)
                    price = execution_price(open_price, Side.SELL, config.slippage_bps)
                    notional = quantity * price
                    fee = transaction_fee(notional, Side.SELL, config)
                    cash += notional - fee
                    positions[symbol] -= quantity
                    fills.append(
                        Fill(symbol, Side.SELL, quantity, price, fee, str(date.date()))
                    )
                executed_targets[symbol] = desired_weight

        equity = cash + sum(
            positions[s] * float(day.loc[s, "close"])
            for s in symbols
            if s in day.index
        )
        peak = max(peak, equity)
        drawdown = equity / peak - 1
        halted = halted or drawdown <= -config.max_drawdown
        equity_rows.append({"date": date, "equity": equity, "drawdown": drawdown})
        for symbol, target in day["target"].to_dict().items():
            previous_targets[symbol] = (
                config.max_position_weight if isinstance(target, bool) and target
                else float(target)
            )

    return BacktestResult(pd.DataFrame(equity_rows), fills)
