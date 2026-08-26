from pathlib import Path

import pandas as pd

from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import PaperBroker
from moneymore.execution.risk import OrderIntent, RiskResult
from moneymore.models import Side
from moneymore.open_execution import execute_accounts_at_open


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=100_000,
        commission_rate=0.00015,
        minimum_commission=5,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=2,
        lot_size=100,
        max_position_weight=0.2,
        max_gross_exposure=1.0,
        max_drawdown=0.15,
    )


def test_open_execution_fills_all_accounts_once(
    tmp_path: Path, monkeypatch
) -> None:
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    accounts = ["factor", "challenger", "candidate"]
    for index, account in enumerate(accounts):
        broker.initialize_account(100_000, account)
        intent = OrderIntent(
            idempotency_key=f"buy-{index}",
            strategy_id="test",
            symbol="600036.SH",
            side=Side.BUY,
            quantity=100,
            signal_date="20260825",
            reason_code="TEST",
        )
        broker.submit(RiskResult(True, intent, None), account)

    bars = pd.DataFrame(
        [
            {
                "date": "20260826",
                "raw_open": 40.0,
                "raw_close": 40.2,
                "can_buy": True,
                "can_sell": True,
            }
        ]
    )
    monkeypatch.setattr(
        "moneymore.open_execution.load_total_return_stock_bars",
        lambda _store, _symbol: bars,
    )
    arguments = {
        "store": ParquetStore(tmp_path / "data"),
        "broker": broker,
        "config": _config(),
        "trade_date": "20260826",
        "account_ids": accounts,
        "audit_dir": tmp_path / "audit",
    }
    first = execute_accounts_at_open(**arguments)
    second = execute_accounts_at_open(**arguments)

    assert first["execution_events"] == 3
    assert second["execution_events"] == 0
    assert all(broker.reconcile(account).matched for account in accounts)
