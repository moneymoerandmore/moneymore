"""Fail-closed continuity planning for broker-backed trading.

No function here sends an order.  The broker's fresh positions, cash, orders and
fills must be queried and reconciled before a plan can be submitted by a future
broker adapter.  Missed sessions are never backdated into a real account.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class Position:
    quantity: int
    available_quantity: int


@dataclass(frozen=True)
class Quote:
    price: float
    observed_at: datetime
    up_limit: float = 0.0
    down_limit: float = 0.0


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    symbol: str
    side: str
    remaining_quantity: int
    status: str


@dataclass(frozen=True)
class RecoverySnapshot:
    account_id: str
    now: datetime
    broker_observed_at: datetime
    target_version: str
    target_observed_at: datetime
    target_quantities: dict[str, int]
    positions: dict[str, Position]
    cash: float
    orders: tuple[BrokerOrder, ...]
    quotes: dict[str, Quote]
    broker_connected: bool
    reconciled: bool
    is_trading_day: bool = False
    risk_state: str = "NORMAL"
    confirmed_snapshot_count: int = 1
    cash_reserve: float = 0.0
    buy_cost_buffer_bps: float = 0.0
    lot_size: int = 100
    buy_priority: dict[str, float] | None = None


@dataclass(frozen=True)
class RecoveryIntent:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    target_quantity: int
    broker_quantity: int
    reason: str


@dataclass(frozen=True)
class RecoveryPlan:
    plan_id: str
    account_id: str
    target_version: str
    status: str
    reasons: tuple[str, ...]
    intents: tuple[RecoveryIntent, ...]
    residual_gaps: dict[str, int]
    generated_at: str
    provenance: str = "BROKER_SNAPSHOT_LATEST_TARGET"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("recovery timestamps must be timezone-aware")
    return value.astimezone(SHANGHAI)


def _id(*parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def build_recovery_plan(snapshot: RecoverySnapshot) -> RecoveryPlan:
    """Compute executable *current* residuals; never replay old desired orders."""
    now = _local(snapshot.now)
    broker_at = _local(snapshot.broker_observed_at)
    target_at = _local(snapshot.target_observed_at)
    if not snapshot.account_id or not snapshot.target_version:
        raise ValueError("account and target version are required")
    if snapshot.lot_size <= 0 or snapshot.cash < 0 or snapshot.cash_reserve < 0:
        raise ValueError("invalid cash or lot configuration")
    if any(q < 0 for q in snapshot.target_quantities.values()):
        raise ValueError("negative target quantity")
    if any(p.quantity < 0 or p.available_quantity < 0 or
           p.available_quantity > p.quantity for p in snapshot.positions.values()):
        raise ValueError("invalid broker position")
    plan_id = _id(snapshot.account_id, snapshot.target_version,
                  now.strftime("%Y%m%d"), snapshot.target_quantities)

    def blocked(status: str, *reasons: str,
                gaps: dict[str, int] | None = None) -> RecoveryPlan:
        return RecoveryPlan(plan_id, snapshot.account_id, snapshot.target_version,
                            status, tuple(reasons), (), gaps or {}, now.isoformat())

    if not snapshot.broker_connected:
        return blocked("BLOCKED_BROKER_DISCONNECTED", "broker_disconnected")
    if not snapshot.reconciled:
        return blocked("BLOCKED_RECONCILIATION", "positions_orders_fills_not_reconciled")
    if snapshot.confirmed_snapshot_count < 2:
        return blocked("WAIT_BROKER_CONFIRMATION", "need_two_confirmed_snapshots")
    if broker_at > now or (now - broker_at).total_seconds() > 30:
        return blocked("BLOCKED_STALE_BROKER_STATE", "broker_snapshot_older_than_30s")
    if target_at > now or not snapshot.target_version:
        return blocked("BLOCKED_TARGET", "target_not_effective")
    if snapshot.risk_state == "SUSPENDED":
        return blocked("BLOCKED_RISK", "account_suspended")
    if snapshot.risk_state not in {"NORMAL", "REDUCE_ONLY", "SELL_ONLY"}:
        return blocked("BLOCKED_RISK", "unknown_risk_state")
    if any(order.remaining_quantity > 0 and order.status not in
           {"FILLED", "CANCELLED", "REJECTED"} for order in snapshot.orders):
        return blocked("WAIT_OPEN_ORDERS", "query_cancel_and_confirm_all_open_orders")
    if not snapshot.is_trading_day or now.weekday() >= 5 or not (
        time(9, 35) <= now.time() <= time(11, 30) or
        time(13, 0) <= now.time() <= time(14, 50)
    ):
        return blocked("WAIT_MARKET_SESSION", "no_backdated_or_after_hours_orders")

    gaps = {
        symbol: target - snapshot.positions.get(symbol, Position(0, 0)).quantity
        for symbol, target in snapshot.target_quantities.items()
    }
    for symbol, position in snapshot.positions.items():
        if symbol not in gaps and position.quantity:
            gaps[symbol] = -position.quantity
    gaps = {symbol: difference for symbol, difference in gaps.items() if difference}
    if not gaps:
        return blocked("CONVERGED", "broker_positions_match_target")
    missing_quotes = [symbol for symbol in gaps if symbol not in snapshot.quotes]
    stale_quotes = [symbol for symbol in gaps if symbol in snapshot.quotes and (
        snapshot.quotes[symbol].price <= 0 or
        _local(snapshot.quotes[symbol].observed_at) > now or
        (now - _local(snapshot.quotes[symbol].observed_at)).total_seconds() > 30
    )]
    if missing_quotes or stale_quotes:
        return blocked("BLOCKED_QUOTES", "missing_or_stale_quotes",
                       gaps=gaps)

    intents: list[RecoveryIntent] = []
    residual: dict[str, int] = {}
    reasons: list[str] = []
    for symbol in sorted(gaps):
        difference = gaps[symbol]
        if difference >= 0:
            continue
        position = snapshot.positions[symbol]
        quote = snapshot.quotes[symbol]
        if quote.down_limit and quote.price <= quote.down_limit:
            residual[symbol] = difference
            reasons.append(f"{symbol}:limit_down")
            continue
        quantity = min(-difference, position.available_quantity)
        if quantity != position.quantity:
            quantity = quantity // snapshot.lot_size * snapshot.lot_size
        if quantity <= 0:
            residual[symbol] = difference
            reasons.append(f"{symbol}:t_plus_one_or_lot_block")
            continue
        intents.append(RecoveryIntent(
            _id(plan_id, symbol, "SELL", quantity), symbol, "SELL", quantity,
            snapshot.target_quantities.get(symbol, 0), position.quantity,
            "LATEST_TARGET_GAP",
        ))
        if quantity < -difference:
            residual[symbol] = difference + quantity
            reasons.append(f"{symbol}:available_shares_short")
    if any(gap < 0 for gap in gaps.values()):
        # Never fund a buy with an assumed sale. Re-query broker after sells.
        residual.update({symbol: gap for symbol, gap in gaps.items() if gap > 0})
        if residual:
            reasons.append("requery_after_sell_fills_before_buying")
        status = "SELL_PHASE" if intents else "BLOCKED_SELL_PHASE"
        return RecoveryPlan(plan_id, snapshot.account_id, snapshot.target_version,
                            status, tuple(reasons), tuple(intents), residual,
                            now.isoformat())

    if snapshot.risk_state != "NORMAL":
        return blocked("BLOCKED_RISK", "risk_state_forbids_increasing_exposure",
                       gaps=gaps)
    cash = max(0.0, snapshot.cash - snapshot.cash_reserve)
    total_buy_cost = sum(
        (gap // snapshot.lot_size * snapshot.lot_size)
        * snapshot.quotes[symbol].price
        * (1 + snapshot.buy_cost_buffer_bps / 10_000)
        for symbol, gap in gaps.items() if gap > 0
    )
    if total_buy_cost > cash and not snapshot.buy_priority:
        return blocked("BLOCKED_ALLOCATION", "insufficient_cash_requires_strategy_priority",
                       gaps=gaps)
    for symbol in sorted(gaps, key=lambda item: (
        -(snapshot.buy_priority or {}).get(item, 0.0), item
    )):
        difference = gaps[symbol]
        quote = snapshot.quotes[symbol]
        if quote.up_limit and quote.price >= quote.up_limit:
            residual[symbol] = difference
            reasons.append(f"{symbol}:limit_up")
            continue
        unit_cost = quote.price * (1 + snapshot.buy_cost_buffer_bps / 10_000)
        affordable = int(cash / unit_cost) // snapshot.lot_size * snapshot.lot_size
        quantity = min(difference // snapshot.lot_size * snapshot.lot_size,
                       affordable)
        if quantity:
            current = snapshot.positions.get(symbol, Position(0, 0)).quantity
            intents.append(RecoveryIntent(
                _id(plan_id, symbol, "BUY", quantity), symbol, "BUY", quantity,
                snapshot.target_quantities[symbol], current, "LATEST_TARGET_GAP",
            ))
            cash -= quantity * unit_cost
        if quantity < difference:
            residual[symbol] = difference - quantity
            reasons.append(f"{symbol}:cash_or_lot_short")
    return RecoveryPlan(plan_id, snapshot.account_id, snapshot.target_version,
                        "BUY_PHASE" if intents else "BLOCKED_BUY_PHASE",
                        tuple(reasons), tuple(intents), residual,
                        now.isoformat())


class RecoveryJournal:
    """Durable once-only reservation before a future live adapter sends an order.

    An uncertain submission remains RESERVED/UNKNOWN. It must be reconciled by
    querying the broker with the client reference; it is never blindly resent.
    """

    def __init__(self, database: Path):
        self.database = Path(database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS recovery_intents (
                intent_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                account_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                quantity INTEGER NOT NULL, state TEXT NOT NULL,
                broker_order_id TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

    def reserve(self, plan: RecoveryPlan, intent: RecoveryIntent) -> bool:
        if plan.status not in {"SELL_PHASE", "BUY_PHASE"} or intent not in plan.intents:
            raise ValueError("intent is not executable in this plan")
        timestamp = datetime.now(SHANGHAI).isoformat()
        with sqlite3.connect(self.database, timeout=30, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO recovery_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
                (intent.intent_id, plan.plan_id, plan.account_id, intent.symbol,
                 intent.side, intent.quantity, "RESERVED", None, timestamp, timestamp),
            )
            connection.commit()
        return cursor.rowcount == 1

    def record_ack(self, intent_id: str, broker_order_id: str) -> None:
        if not broker_order_id:
            raise ValueError("broker order id required")
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                "UPDATE recovery_intents SET state = 'ACKNOWLEDGED', "
                "broker_order_id = ?, updated_at = ? WHERE intent_id = ? "
                "AND state IN ('RESERVED', 'UNKNOWN')",
                (broker_order_id, datetime.now(SHANGHAI).isoformat(), intent_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("intent not reserved or already acknowledged")

    def mark_unknown(self, intent_id: str) -> None:
        """A timed-out submit is not permission to submit the same intent again."""
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                "UPDATE recovery_intents SET state = 'UNKNOWN', updated_at = ? "
                "WHERE intent_id = ? AND state = 'RESERVED'",
                (datetime.now(SHANGHAI).isoformat(), intent_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("intent not reserved")

    def state(self, intent_id: str) -> str | None:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT state FROM recovery_intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        return str(row[0]) if row else None
