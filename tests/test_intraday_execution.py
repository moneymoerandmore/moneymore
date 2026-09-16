import sqlite3
import json
from datetime import time

from moneymore.execution.paper import PaperBroker
from moneymore.execution.risk import OrderIntent, RiskResult
from moneymore.intraday_execution import (
    INTRADAY_ACCOUNT,
    affordable_buy_quantity,
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


def test_compensation_executes_promptly_and_respects_spread_guard() -> None:
    common = dict(symbol="600036.SH", side="BUY", price=40, open_price=40,
                  vwap=39, bid1=39.99, ask1=40.01, compensation=True)
    fast = decide_intraday_execution(**common, now_time=time(9, 35))
    assert (fast.action, fast.reason) == ("EXECUTE", "POSITION_GAP_COMPENSATION")
    wide = decide_intraday_execution(**{**common, "bid1": 39.8, "ask1": 40.2}, now_time=time(9, 36))
    assert wide.reason == "COMPENSATION_SPREAD_GUARD"
    fallback = decide_intraday_execution(**{**common, "bid1": 39.8, "ask1": 40.2}, now_time=time(9, 45))
    assert fallback.action == "EXECUTE"


def test_compensation_buy_is_clipped_to_funded_whole_lots() -> None:
    from moneymore.config import BacktestConfig
    config = BacktestConfig.from_yaml("configs/default.yaml")
    assert affordable_buy_quantity(500, 8_004, 40, config) == 100
    assert affordable_buy_quantity(500, 3_000, 40, config) == 0


def test_intraday_order_mirroring_recovers_parent_already_filled_today(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    report_dir = state / "multi-sector-shadow"
    report_dir.mkdir()
    (report_dir / "20260908.json").write_text(json.dumps({
        "status": "COMPLETED", "target_weights": {"600036.SH": 0.1},
    }), encoding="utf-8")
    broker = PaperBroker(state / "paper.sqlite3")
    broker.initialize_account(100_000, MULTI_SECTOR_ACCOUNT)
    # The branch starts from the baseline's pre-trade portfolio.
    assert prepare_intraday_branch_orders(broker, "20260909") == 0
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
            """INSERT INTO positions(account_id,symbol,quantity,available_quantity,avg_cost,last_buy_date)
               VALUES (?, ?, 100, 0, 40, '20260909')""",
            (MULTI_SECTOR_ACCOUNT, intent.symbol),
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
    assert mirrored[0]["reason_code"] == "SHARED_TARGET_REBALANCE"
    assert prepare_intraday_branch_orders(broker, "20260909") == 0


def test_intraday_rebalances_to_shared_target_after_prior_miss(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    report_dir = state / "multi-sector-shadow"
    report_dir.mkdir()
    (report_dir / "20260915.json").write_text(json.dumps({
        "status": "COMPLETED", "target_weights": {"600036.SH": 0.1},
    }), encoding="utf-8")
    broker = PaperBroker(state / "paper.sqlite3")
    broker.initialize_account(100_000, MULTI_SECTOR_ACCOUNT)
    broker.initialize_account(100_000, INTRADAY_ACCOUNT)
    with sqlite3.connect(broker.database) as connection:
        connection.execute(
            """INSERT INTO positions(account_id,symbol,quantity,available_quantity,avg_cost,last_buy_date)
               VALUES (?, '600036.SH', 300, 300, 40, '20260914')""",
            (MULTI_SECTOR_ACCOUNT,),
        )
        connection.execute(
            """INSERT INTO positions(account_id,symbol,quantity,available_quantity,avg_cost,last_buy_date)
               VALUES (?, '600036.SH', 100, 100, 40, '20260914')""",
            (INTRADAY_ACCOUNT,),
        )
        connection.execute(
            """INSERT INTO orders(idempotency_key,created_at,status,strategy_id,symbol,side,quantity,signal_date,reason_code,account_id)
               VALUES ('old-missed','2026-09-15','PENDING','old','600036.SH','BUY',100,'20260914','OLD',?)""",
            (INTRADAY_ACCOUNT,),
        )
    assert prepare_intraday_branch_orders(broker, "20260916") == 1
    rows = [row for row in broker.orders() if row["account_id"] == INTRADAY_ACCOUNT]
    assert [(row["status"], row["quantity"]) for row in rows if row["signal_date"] == "20260915"] == [("PENDING", 200)]
    assert next(row for row in rows if row["signal_date"] == "20260915")["reason_code"] == "POSITION_GAP_COMPENSATION"
    assert any(row["idempotency_key"] == "old-missed" and row["status"] == "CANCELLED" for row in rows)
    assert prepare_intraday_branch_orders(broker, "20260916") == 0
