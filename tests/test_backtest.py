import pandas as pd

from moneymore.backtest import run_daily_backtest
from moneymore.config import BacktestConfig
from moneymore.models import Side


def config(**overrides) -> BacktestConfig:
    values = {
        "initial_cash": 100_000,
        "commission_rate": 0,
        "minimum_commission": 0,
        "stamp_duty_rate": 0,
        "transfer_fee_rate": 0,
        "slippage_bps": 0,
        "lot_size": 100,
        "max_position_weight": 0.5,
        "max_gross_exposure": 0.8,
        "max_drawdown": 0.15,
    }
    values.update(overrides)
    return BacktestConfig(**values)


def test_signal_executes_only_at_next_open():
    bars = pd.DataFrame(
        [
            ["2025-01-02", "AAA", 10, 10, True],
            ["2025-01-03", "AAA", 20, 20, False],
            ["2025-01-06", "AAA", 30, 30, False],
        ],
        columns=["date", "symbol", "open", "close", "target"],
    )
    result = run_daily_backtest(bars, config())
    assert result.fills[0].side is Side.BUY
    assert result.fills[0].trade_date == "2025-01-03"
    assert result.fills[0].price == 20
    assert result.fills[1].side is Side.SELL
    assert result.fills[1].trade_date == "2025-01-06"


def test_buy_quantity_respects_board_lot():
    bars = pd.DataFrame(
        [
            ["2025-01-02", "AAA", 11, 11, True],
            ["2025-01-03", "AAA", 11, 11, True],
        ],
        columns=["date", "symbol", "open", "close", "target"],
    )
    result = run_daily_backtest(bars, config())
    assert result.fills[0].quantity % 100 == 0
    assert result.fills[0].quantity == 4500


def test_multiple_positions_respect_portfolio_exposure_limit():
    rows = []
    for date, target in [("2025-01-02", True), ("2025-01-03", True)]:
        for symbol in ["AAA", "BBB", "CCC"]:
            rows.append([date, symbol, 10, 10, target])
    bars = pd.DataFrame(
        rows, columns=["date", "symbol", "open", "close", "target"]
    )
    result = run_daily_backtest(
        bars, config(max_position_weight=0.5, max_gross_exposure=0.8)
    )
    bought_notional = sum(
        fill.quantity * fill.price for fill in result.fills if fill.side is Side.BUY
    )
    assert bought_notional <= 80_000


def test_numeric_target_supports_core_and_tactical_rebalancing():
    bars = pd.DataFrame(
        [
            ["2025-01-02", "AAA", 10, 10, 0.4],
            ["2025-01-03", "AAA", 10, 10, 0.8],
            ["2025-01-06", "AAA", 10, 10, 0.4],
            ["2025-01-07", "AAA", 10, 10, 0.4],
        ],
        columns=["date", "symbol", "open", "close", "target"],
    )
    result = run_daily_backtest(
        bars,
        config(max_position_weight=0.8, max_gross_exposure=0.8),
    )

    assert [fill.side for fill in result.fills] == [Side.BUY, Side.BUY, Side.SELL]
    assert [fill.quantity for fill in result.fills] == [4000, 4000, 4000]
