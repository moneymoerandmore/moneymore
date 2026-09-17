from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from moneymore.execution_continuity import (
    BrokerOrder, Position, Quote, RecoveryJournal, RecoverySnapshot,
    build_recovery_plan,
)

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 18, 10, 0, tzinfo=SH)


def snapshot(**overrides) -> RecoverySnapshot:
    values = dict(
        account_id="paper-a", now=NOW,
        broker_observed_at=NOW - timedelta(seconds=2),
        target_version="target-20260917-v1",
        target_observed_at=NOW - timedelta(hours=16),
        target_quantities={"600036.SH": 500},
        positions={"600036.SH": Position(300, 300)},
        cash=100_000, orders=(),
        quotes={"600036.SH": Quote(40, NOW - timedelta(seconds=2))},
        broker_connected=True, reconciled=True,
        confirmed_snapshot_count=2, is_trading_day=True,
    )
    values.update(overrides)
    return RecoverySnapshot(**values)


def test_recovery_uses_current_target_gap_and_is_deterministic() -> None:
    plan = build_recovery_plan(snapshot())
    assert plan.status == "BUY_PHASE"
    assert [(x.symbol, x.side, x.quantity) for x in plan.intents] == [
        ("600036.SH", "BUY", 200)
    ]
    assert build_recovery_plan(snapshot()).intents[0].intent_id == plan.intents[0].intent_id
    assert build_recovery_plan(snapshot(positions={"600036.SH": Position(500, 500)})).status == "CONVERGED"


def test_recovery_fails_closed_on_uncertain_broker_state() -> None:
    order = BrokerOrder("qmt-1", "600036.SH", "BUY", 200, "SUBMITTED")
    assert build_recovery_plan(snapshot(orders=(order,))).status == "WAIT_OPEN_ORDERS"
    assert build_recovery_plan(snapshot(confirmed_snapshot_count=1)).status == "WAIT_BROKER_CONFIRMATION"
    assert build_recovery_plan(snapshot(reconciled=False)).status == "BLOCKED_RECONCILIATION"
    assert build_recovery_plan(snapshot(is_trading_day=False)).status == "WAIT_MARKET_SESSION"
    assert build_recovery_plan(snapshot(quotes={})).status == "BLOCKED_QUOTES"
    assert build_recovery_plan(snapshot(
        broker_observed_at=NOW - timedelta(minutes=5)
    )).status == "BLOCKED_STALE_BROKER_STATE"


def test_sells_first_and_t_plus_one_never_sells_unavailable_shares() -> None:
    plan = build_recovery_plan(snapshot(
        target_quantities={"600036.SH": 0, "601318.SH": 100},
        positions={"600036.SH": Position(300, 100)},
        quotes={
            "600036.SH": Quote(40, NOW), "601318.SH": Quote(50, NOW),
        },
    ))
    assert plan.status == "SELL_PHASE"
    assert [(x.side, x.quantity) for x in plan.intents] == [("SELL", 100)]
    assert plan.residual_gaps == {"600036.SH": -200, "601318.SH": 100}


def test_uncertain_submission_cannot_be_sent_twice(tmp_path) -> None:
    plan = build_recovery_plan(snapshot())
    journal = RecoveryJournal(tmp_path / "continuity.sqlite3")
    intent = plan.intents[0]
    assert journal.reserve(plan, intent) is True
    journal.mark_unknown(intent.intent_id)
    assert journal.reserve(plan, intent) is False
    assert journal.state(intent.intent_id) == "UNKNOWN"
    journal.record_ack(intent.intent_id, "broker-123")
    assert journal.state(intent.intent_id) == "ACKNOWLEDGED"


def test_cash_shortage_needs_strategy_priority_not_symbol_order() -> None:
    values = dict(
        target_quantities={"600036.SH": 500, "601318.SH": 500},
        positions={}, cash=20_000,
        quotes={"600036.SH": Quote(40, NOW), "601318.SH": Quote(40, NOW)},
    )
    assert build_recovery_plan(snapshot(**values)).status == "BLOCKED_ALLOCATION"
    plan = build_recovery_plan(snapshot(**values, buy_priority={"601318.SH": 2.0,
                                                                 "600036.SH": 1.0}))
    assert plan.status == "BUY_PHASE"
    assert plan.intents[0].symbol == "601318.SH"
