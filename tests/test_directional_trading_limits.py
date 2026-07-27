import pandas as pd

from moneymore.backtest import run_daily_backtest
from moneymore.config import BacktestConfig
from moneymore.models import Side


def _config():
    return BacktestConfig(
        initial_cash=100_000,
        commission_rate=0,
        minimum_commission=0,
        stamp_duty_rate=0,
        transfer_fee_rate=0,
        slippage_bps=0,
        lot_size=100,
        max_position_weight=0.8,
        max_gross_exposure=0.8,
        max_drawdown=0.99,
    )


def test_limit_up_rejects_buy_until_next_tradable_open():
    bars = pd.DataFrame(
        [
            ["2025-01-02", "AAA", 10, 10, True, True, True],
            ["2025-01-03", "AAA", 11, 11, True, False, True],
            ["2025-01-06", "AAA", 10, 10, True, True, True],
        ],
        columns=["date", "symbol", "open", "close", "target", "can_buy", "can_sell"],
    )
    result = run_daily_backtest(bars, _config())
    assert result.fills[0].side is Side.BUY
    assert result.fills[0].trade_date == "2025-01-06"


def test_limit_down_rejects_sell_until_next_tradable_open():
    bars = pd.DataFrame(
        [
            ["2025-01-02", "AAA", 10, 10, True, True, True],
            ["2025-01-03", "AAA", 10, 10, False, True, True],
            ["2025-01-06", "AAA", 9, 9, False, True, False],
            ["2025-01-07", "AAA", 8, 8, False, True, True],
        ],
        columns=["date", "symbol", "open", "close", "target", "can_buy", "can_sell"],
    )
    result = run_daily_backtest(bars, _config())
    assert result.fills[-1].side is Side.SELL
    assert result.fills[-1].trade_date == "2025-01-07"
