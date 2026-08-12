import pytest

from moneymore.trade_cycles import analyze_trade_cycles


def test_trade_cycles_handle_scale_in_partial_exit_fees_and_dividend() -> None:
    fills = [
        {"id": 1, "symbol": "A", "side": "BUY", "quantity": 100, "price": 10, "fee": 5, "trade_date": "20260101"},
        {"id": 2, "symbol": "A", "side": "BUY", "quantity": 100, "price": 11, "fee": 5, "trade_date": "20260102"},
        {"id": 3, "symbol": "A", "side": "SELL", "quantity": 100, "price": 12, "fee": 6, "trade_date": "20260103"},
        {"id": 4, "symbol": "A", "side": "SELL", "quantity": 100, "price": 13, "fee": 6, "trade_date": "20260104"},
    ]
    actions = [{"id": 1, "symbol": "A", "action_type": "CASH_DIVIDEND", "cash_amount": 20, "share_quantity": 0, "trade_date": "20260102"}]
    result = analyze_trade_cycles("test", fills, actions)
    cycle = result["closed_cycles"][0]
    assert cycle["pnl"] == pytest.approx(398.0)
    assert cycle["dividend_income"] == 20
    assert result["summary"]["win_rate"] == 1.0


def test_trade_cycles_exclude_open_positions_from_win_rate() -> None:
    result = analyze_trade_cycles(
        "test",
        [{"id": 1, "symbol": "A", "side": "BUY", "quantity": 100, "price": 10, "fee": 5, "trade_date": "20260101"}],
        marks={"A": 12},
    )
    assert result["summary"]["closed_cycles"] == 0
    assert result["summary"]["win_rate"] is None
    assert result["by_symbol"][0]["open_cycle"] is True
    assert result["by_symbol"][0]["open_estimated_pnl"] == pytest.approx(195.0)


def test_payoff_ratio_uses_average_win_over_absolute_average_loss() -> None:
    fills = [
        {"id": 1, "symbol": "A", "side": "BUY", "quantity": 100, "price": 10, "fee": 0, "trade_date": "20260101"},
        {"id": 2, "symbol": "A", "side": "SELL", "quantity": 100, "price": 12, "fee": 0, "trade_date": "20260102"},
        {"id": 3, "symbol": "A", "side": "BUY", "quantity": 100, "price": 10, "fee": 0, "trade_date": "20260103"},
        {"id": 4, "symbol": "A", "side": "SELL", "quantity": 100, "price": 9, "fee": 0, "trade_date": "20260104"},
    ]
    metrics = analyze_trade_cycles("test", fills)["by_symbol"][0]
    assert metrics["win_rate"] == 0.5
    assert metrics["payoff_ratio"] == 2.0
    assert metrics["profit_factor"] == 2.0
