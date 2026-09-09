import sqlite3
from datetime import time

from moneymore.execution.paper import PaperBroker
from moneymore.execution.risk import OrderIntent, RiskResult
from moneymore.intraday_execution import (
    INTRADAY_ACCOUNT,
    decide_intraday_execution,
    prepare_intraday_branch_orders,
)
from moneymore.models import Side
from moneymore.multi_sector_daily import MULTI_SECTOR_ACCOUNT


def test_intraday_policy_avoids_open_noise_and_wide_spreads() -> None:
    opening = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40,
        open_price=40,
        vwap=40,
        bid1=39.99,
        ask1=40.01,
        now_time=time(9, 32),
    )
    wide = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40,
        open_price=40,
        vwap=40,
        bid1=39.9,
        ask1=40.1,
        now_time=time(10, 0),
    )
    assert opening.reason == "OPEN_NOISE_WINDOW"
    assert wide.reason == "SPREAD_TOO_WIDE"


def test_intraday_policy_snipes_favorable_vwap_and_has_deadline() -> None:
    favorable = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=39.9,
        open_price=40,
        vwap=40,
        bid1=39.89,
        ask1=39.91,
        now_time=time(10, 30),
    )
    deadline = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40.5,
        open_price=40,
        vwap=40,
        bid1=40.49,
        ask1=40.51,
        now_time=time(14, 50),
    )
    assert favorable.action == "EXECUTE"
    assert favorable.reason == "VWAP_SNIPER_TRIGGER"
    assert deadline.action == "EXECUTE"
    assert deadline.reason == "DEADLINE_FALLBACK"


def test_intraday_order_mirroring_recovers_parent_already_filled_today(tmp_path) -> None:
    broker = PaperBroker(tmp_path / "paper.sqlite3")
    broker.initialize_account(100_000, MULTI_SECTOR_ACCOUNT)
    intent = OrderIntent(
        idempotency_key="baseline-order",
        strategy_id="baseline",
        symbol="600036.SH",
        side=Side.BUY,
        quantity=100,
        signal_date="20260908",
        reason_code="TEST",
    )
    broker.submit(RiskResult(True, intent, None), MULTI_SECTOR_ACCOUNT)
    with sqlite3.connect(broker.database) as connection:
        connection.execute(
            "UPDATE orders SET status = 'FILLED' WHERE idempotency_key = ?",
            (intent.idempotency_key,),
        )
        connection.execute(
            """
            INSERT INTO fills(
                account_id, idempotency_key, symbol, side, quantity,
                price, fee, trade_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                MULTI_SECTOR_ACCOUNT,
                intent.idempotency_key,
                intent.symbol,
                intent.side.value,
                intent.quantity,
                40.0,
                5.0,
                "20260909",
            ),
        )

    assert prepare_intraday_branch_orders(broker, "20260909") == 1
    mirrored = [row for row in broker.orders() if row["account_id"] == INTRADAY_ACCOUNT]
    assert len(mirrored) == 1
    assert mirrored[0]["status"] == "PENDING"
    assert prepare_intraday_branch_orders(broker, "20260909") == 0
