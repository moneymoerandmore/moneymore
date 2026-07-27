import pandas as pd

from moneymore.backtest import BacktestResult
from moneymore.models import Fill, Side
from moneymore.research.detail import trade_ledger


def test_trade_ledger_pairs_fills_and_calculates_net_return():
    fills = [
        Fill("AAA", Side.BUY, 100, 10.0, 5.0, "2025-01-02"),
        Fill("AAA", Side.SELL, 100, 11.0, 6.0, "2025-01-12"),
    ]
    result = BacktestResult(pd.DataFrame(), fills)
    ledger = trade_ledger(result)
    assert len(ledger) == 1
    assert ledger.iloc[0]["holding_days"] == 10
    assert ledger.iloc[0]["net_return"] == (1094 / 1005 - 1)
