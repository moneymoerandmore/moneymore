from __future__ import annotations

from typing import Any


def analyze_trade_cycles(
    account_id: str,
    fills: list[dict[str, object]],
    corporate_actions: list[dict[str, object]] | None = None,
    marks: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build zero-position-to-zero-position cycles from the paper cash ledger."""
    events: list[dict[str, object]] = []
    for fill in fills:
        events.append({**fill, "event_type": "FILL", "sort_id": int(fill.get("id", 0))})
    for action in corporate_actions or []:
        events.append(
            {
                **action,
                "event_type": "CORPORATE_ACTION",
                "sort_id": int(action.get("id", 0)),
            }
        )
    events.sort(
        key=lambda row: (
            str(row.get("trade_date", "")),
            0 if row["event_type"] == "FILL" else 1,
            int(row["sort_id"]),
        )
    )

    states: dict[str, dict[str, Any]] = {}
    cycles: list[dict[str, Any]] = []
    for event in events:
        symbol = str(event["symbol"])
        state = states.setdefault(symbol, _empty_state())
        if event["event_type"] == "FILL":
            quantity = int(event["quantity"])
            notional = quantity * float(event["price"])
            fee = float(event["fee"])
            if str(event["side"]) == "BUY":
                if state["quantity"] == 0:
                    state.update(_empty_state())
                    state["entry_date"] = str(event["trade_date"])
                state["quantity"] += quantity
                state["buy_notional"] += notional
                state["fees"] += fee
                state["cash_flow"] -= notional + fee
                state["fill_count"] += 1
            else:
                if quantity > state["quantity"]:
                    raise ValueError(f"sell exceeds cycle quantity: {account_id} {symbol}")
                state["quantity"] -= quantity
                state["sell_notional"] += notional
                state["fees"] += fee
                state["cash_flow"] += notional - fee
                state["fill_count"] += 1
                if state["quantity"] == 0 and state["entry_date"]:
                    pnl = float(state["cash_flow"])
                    invested = float(state["buy_notional"] + state["fees"])
                    cycles.append(
                        {
                            "account_id": account_id,
                            "symbol": symbol,
                            "entry_date": state["entry_date"],
                            "exit_date": str(event["trade_date"]),
                            "pnl": pnl,
                            "return_rate": pnl / invested if invested else None,
                            "buy_notional": float(state["buy_notional"]),
                            "sell_notional": float(state["sell_notional"]),
                            "dividend_income": float(state["dividend_income"]),
                            "fees": float(state["fees"]),
                            "fill_count": int(state["fill_count"]),
                            "result": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
                        }
                    )
                    states[symbol] = _empty_state()
        elif state["quantity"] > 0:
            action_type = str(event.get("action_type", ""))
            if action_type == "CASH_DIVIDEND":
                amount = float(event.get("cash_amount", 0))
                state["dividend_income"] += amount
                state["cash_flow"] += amount
            elif action_type == "STOCK_DIVIDEND":
                state["quantity"] += int(event.get("share_quantity", 0))

    open_cycles = []
    for symbol, state in sorted(states.items()):
        if state["quantity"] <= 0:
            continue
        mark = float((marks or {}).get(symbol, 0))
        open_cycles.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "entry_date": state["entry_date"],
                "quantity": int(state["quantity"]),
                "mark": mark or None,
                "estimated_pnl": (
                    float(state["cash_flow"]) + int(state["quantity"]) * mark
                    if mark
                    else None
                ),
                "dividend_income": float(state["dividend_income"]),
                "fill_count": int(state["fill_count"]),
            }
        )

    by_symbol = []
    for symbol in sorted({str(row["symbol"]) for row in cycles} | set(states)):
        symbol_cycles = [row for row in cycles if row["symbol"] == symbol]
        open_cycle = next((row for row in open_cycles if row["symbol"] == symbol), None)
        by_symbol.append(_cycle_metrics(account_id, symbol, symbol_cycles, open_cycle))
    return {
        "account_id": account_id,
        "summary": _cycle_metrics(account_id, "ALL", cycles, None),
        "by_symbol": by_symbol,
        "closed_cycles": sorted(cycles, key=lambda row: (row["exit_date"], row["symbol"]), reverse=True),
        "open_cycles": open_cycles,
        "methodology": {
            "cycle_boundary": "POSITION_ZERO_TO_POSITION_ZERO",
            "cost_basis": "ACTUAL_FILL_CASH_FLOW_AFTER_FEES_AND_SLIPPAGE",
            "dividends": "INCLUDED",
            "open_cycles_in_win_rate": False,
            "payoff_ratio": "AVERAGE_WIN_DIVIDED_BY_ABSOLUTE_AVERAGE_LOSS",
        },
    }


def _empty_state() -> dict[str, Any]:
    return {
        "quantity": 0,
        "entry_date": None,
        "cash_flow": 0.0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "dividend_income": 0.0,
        "fees": 0.0,
        "fill_count": 0,
    }


def _cycle_metrics(
    account_id: str,
    symbol: str,
    cycles: list[dict[str, Any]],
    open_cycle: dict[str, Any] | None,
) -> dict[str, Any]:
    wins = [float(row["pnl"]) for row in cycles if float(row["pnl"]) > 0]
    losses = [float(row["pnl"]) for row in cycles if float(row["pnl"]) < 0]
    closed = len(cycles)
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = sum(losses) / len(losses) if losses else None
    return {
        "account_id": account_id,
        "symbol": symbol,
        "closed_cycles": closed,
        "wins": len(wins),
        "losses": len(losses),
        "flats": closed - len(wins) - len(losses),
        "win_rate": len(wins) / closed if closed else None,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": (
            average_win / abs(average_loss)
            if average_win is not None and average_loss not in {None, 0.0}
            else None
        ),
        "profit_factor": (
            sum(wins) / abs(sum(losses)) if wins and losses else None
        ),
        "realized_pnl": sum(float(row["pnl"]) for row in cycles),
        "average_return": (
            sum(float(row["return_rate"]) for row in cycles if row["return_rate"] is not None)
            / len([row for row in cycles if row["return_rate"] is not None])
            if any(row["return_rate"] is not None for row in cycles)
            else None
        ),
        "open_cycle": open_cycle is not None,
        "open_quantity": int(open_cycle["quantity"]) if open_cycle else 0,
        "open_estimated_pnl": open_cycle.get("estimated_pnl") if open_cycle else None,
    }
