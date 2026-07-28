from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import BacktestConfig
from ..costs import execution_price, transaction_fee
from ..models import Side
from ..signals import SignalDecision
from .risk import OrderIntent, RiskResult


@dataclass(frozen=True)
class ExecutionBar:
    symbol: str
    trade_date: str
    open: float
    close: float
    can_buy: bool = True
    can_sell: bool = True


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    expected_cash: float
    actual_cash: float
    expected_quantity: int
    actual_quantity: int
    cash_difference: float
    quantity_difference: int
    filled_order_count: int
    fill_count: int
    missing_fill_count: int
    orphan_fill_count: int


class PaperBroker:
    """Audit-only order intake. It never connects to a real broker."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def submit(self, result: RiskResult, account_id: str = "default") -> str:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.database) as connection:
            if not result.accepted or result.intent is None:
                connection.execute(
                    "INSERT INTO rejections(created_at, code, payload) VALUES (?, ?, ?)",
                    (now, result.rejection_code, json.dumps(asdict(result))),
                )
                if result.rejection_code == "NO_REBALANCE_REQUIRED":
                    return "NO_ACTION"
                return "REJECTED"
            intent = result.intent
            try:
                connection.execute(
                    """
                    INSERT INTO orders(
                        idempotency_key, created_at, status, strategy_id, symbol,
                        side, quantity, signal_date, reason_code, account_id
                    ) VALUES (?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.idempotency_key,
                        now,
                        intent.strategy_id,
                        intent.symbol,
                        intent.side.value,
                        intent.quantity,
                        intent.signal_date,
                        intent.reason_code,
                        account_id,
                    ),
                )
            except sqlite3.IntegrityError:
                self._record_duplicate(connection, intent, now)
                return "DUPLICATE"
        return "PENDING"

    def initialize_account(
        self, initial_cash: float, account_id: str = "default"
    ) -> str:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        with sqlite3.connect(self.database) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO accounts(account_id, initial_cash, cash, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (account_id, initial_cash, initial_cash, datetime.now(UTC).isoformat()),
                )
            except sqlite3.IntegrityError:
                return "EXISTS"
        return "INITIALIZED"

    def portfolio(
        self, mark_price: float, symbol: str, account_id: str = "default"
    ) -> dict[str, float | int]:
        with sqlite3.connect(self.database) as connection:
            account = connection.execute(
                "SELECT cash FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise ValueError(f"paper account does not exist: {account_id}")
            position = connection.execute(
                """
                SELECT quantity, available_quantity, avg_cost
                FROM positions WHERE account_id = ? AND symbol = ?
                """,
                (account_id, symbol),
            ).fetchone() or (0, 0, 0.0)
        cash = float(account[0])
        quantity = int(position[0])
        return {
            "cash": cash,
            "equity": cash + quantity * mark_price,
            "position_quantity": quantity,
            "available_quantity": int(position[1]),
            "avg_cost": float(position[2]),
        }

    def account_snapshot(
        self, mark_prices: dict[str, float], account_id: str = "default"
    ) -> dict[str, object]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            account = connection.execute(
                "SELECT cash FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise ValueError(f"paper account does not exist: {account_id}")
            positions = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT symbol, quantity, available_quantity, avg_cost
                    FROM positions WHERE account_id = ? AND quantity != 0
                    ORDER BY symbol
                    """,
                    (account_id,),
                )
            ]
        market_value = sum(
            int(position["quantity"]) * float(mark_prices.get(position["symbol"], 0))
            for position in positions
        )
        cash = float(account["cash"])
        return {
            "cash": cash,
            "market_value": market_value,
            "equity": cash + market_value,
            "positions": positions,
        }

    def execute_pending(
        self,
        bar: ExecutionBar,
        config: BacktestConfig,
        account_id: str = "default",
    ) -> list[dict[str, object]]:
        outcomes: list[dict[str, object]] = []
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            account = connection.execute(
                "SELECT cash FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise ValueError(f"paper account does not exist: {account_id}")
            self._settle_positions(connection, account_id, bar.trade_date)
            orders = connection.execute(
                """
                SELECT * FROM orders
                WHERE account_id = ? AND symbol = ? AND status = 'PENDING'
                  AND signal_date < ?
                ORDER BY created_at, idempotency_key
                """,
                (account_id, bar.symbol, bar.trade_date),
            ).fetchall()
            for order in orders:
                side = Side(order["side"])
                if side is Side.BUY and not bar.can_buy:
                    outcomes.append(
                        self._attempt(
                            connection, order, bar.trade_date, "DEFERRED", "LIMIT_UP"
                        )
                    )
                    continue
                if side is Side.SELL and not bar.can_sell:
                    outcomes.append(
                        self._attempt(
                            connection, order, bar.trade_date, "DEFERRED", "LIMIT_DOWN"
                        )
                    )
                    continue
                outcome = self._fill_order(
                    connection, order, bar, config, account_id
                )
                outcomes.append(outcome)
        return outcomes

    def reconcile(self, account_id: str = "default") -> ReconciliationResult:
        with sqlite3.connect(self.database) as connection:
            account = connection.execute(
                "SELECT initial_cash, cash FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if account is None:
                raise ValueError(f"paper account does not exist: {account_id}")
            fills = connection.execute(
                """
                SELECT side, quantity, price, fee FROM fills
                WHERE account_id = ? ORDER BY id
                """,
                (account_id,),
            ).fetchall()
            actual_quantity = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM positions WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
            filled_order_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM orders
                    WHERE account_id = ? AND status = 'FILLED'
                    """,
                    (account_id,),
                ).fetchone()[0]
            )
            fill_count = len(fills)
            missing_fill_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM orders o
                    LEFT JOIN fills f ON f.idempotency_key = o.idempotency_key
                    WHERE o.account_id = ? AND o.status = 'FILLED' AND f.id IS NULL
                    """,
                    (account_id,),
                ).fetchone()[0]
            )
            orphan_fill_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM fills f
                    LEFT JOIN orders o ON o.idempotency_key = f.idempotency_key
                    WHERE f.account_id = ? AND (
                        o.idempotency_key IS NULL OR o.status != 'FILLED'
                    )
                    """,
                    (account_id,),
                ).fetchone()[0]
            )
        expected_cash = float(account[0])
        expected_quantity = 0
        for side, quantity, price, fee in fills:
            notional = int(quantity) * float(price)
            if side == Side.BUY.value:
                expected_cash -= notional + float(fee)
                expected_quantity += int(quantity)
            else:
                expected_cash += notional - float(fee)
                expected_quantity -= int(quantity)
        cash_difference = float(account[1]) - expected_cash
        quantity_difference = int(actual_quantity) - expected_quantity
        return ReconciliationResult(
            matched=(
                abs(cash_difference) < 0.01
                and quantity_difference == 0
                and filled_order_count == fill_count
                and missing_fill_count == 0
                and orphan_fill_count == 0
            ),
            expected_cash=expected_cash,
            actual_cash=float(account[1]),
            expected_quantity=expected_quantity,
            actual_quantity=int(actual_quantity),
            cash_difference=cash_difference,
            quantity_difference=quantity_difference,
            filled_order_count=filled_order_count,
            fill_count=fill_count,
            missing_fill_count=missing_fill_count,
            orphan_fill_count=orphan_fill_count,
        )

    def record_decision(self, decision: SignalDecision) -> str:
        with sqlite3.connect(self.database) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO decisions(
                        strategy_id, symbol, as_of_date, recorded_at, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        decision.strategy_id,
                        decision.symbol,
                        decision.as_of_date,
                        datetime.now(UTC).isoformat(),
                        json.dumps(decision.to_dict(), ensure_ascii=False),
                    ),
                )
            except sqlite3.IntegrityError:
                return "DUPLICATE"
        return "RECORDED"

    def orders(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM orders")]

    def _initialize(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    signal_date TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT 'default'
                );
                CREATE TABLE IF NOT EXISTS rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    code TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, symbol, as_of_date)
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    available_quantity INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    last_buy_date TEXT,
                    PRIMARY KEY(account_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL,
                    trade_date TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason_code TEXT
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(orders)")
            }
            if "account_id" not in columns:
                connection.execute(
                    "ALTER TABLE orders ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'"
                )

    @staticmethod
    def _record_duplicate(
        connection: sqlite3.Connection, intent: OrderIntent, now: str
    ) -> None:
        connection.execute(
            "INSERT INTO rejections(created_at, code, payload) VALUES (?, ?, ?)",
            (now, "DUPLICATE_ORDER", json.dumps(asdict(intent))),
        )

    @staticmethod
    def _settle_positions(
        connection: sqlite3.Connection, account_id: str, trade_date: str
    ) -> None:
        connection.execute(
            """
            UPDATE positions SET available_quantity = quantity
            WHERE account_id = ? AND last_buy_date IS NOT NULL
              AND last_buy_date < ?
            """,
            (account_id, trade_date),
        )

    @staticmethod
    def _attempt(
        connection: sqlite3.Connection,
        order: sqlite3.Row,
        trade_date: str,
        outcome: str,
        reason: str | None,
    ) -> dict[str, object]:
        connection.execute(
            """
            INSERT INTO execution_attempts(
                idempotency_key, trade_date, outcome, reason_code
            ) VALUES (?, ?, ?, ?)
            """,
            (order["idempotency_key"], trade_date, outcome, reason),
        )
        return {
            "idempotency_key": order["idempotency_key"],
            "outcome": outcome,
            "reason_code": reason,
        }

    def _fill_order(
        self,
        connection: sqlite3.Connection,
        order: sqlite3.Row,
        bar: ExecutionBar,
        config: BacktestConfig,
        account_id: str,
    ) -> dict[str, object]:
        side = Side(order["side"])
        quantity = int(order["quantity"])
        price = execution_price(bar.open, side, config.slippage_bps)
        notional = quantity * price
        fee = transaction_fee(notional, side, config)
        cash = float(
            connection.execute(
                "SELECT cash FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
        )
        position = connection.execute(
            """
            SELECT quantity, available_quantity, avg_cost, last_buy_date
            FROM positions WHERE account_id = ? AND symbol = ?
            """,
            (account_id, bar.symbol),
        ).fetchone() or (0, 0, 0.0, None)
        held, available, avg_cost = int(position[0]), int(position[1]), float(position[2])
        prior_last_buy_date = position[3]
        if side is Side.BUY and notional + fee > cash:
            connection.execute(
                "UPDATE orders SET status = 'REJECTED' WHERE idempotency_key = ?",
                (order["idempotency_key"],),
            )
            return self._attempt(
                connection, order, bar.trade_date, "REJECTED", "INSUFFICIENT_CASH"
            )
        if side is Side.SELL and quantity > held:
            connection.execute(
                "UPDATE orders SET status = 'REJECTED' WHERE idempotency_key = ?",
                (order["idempotency_key"],),
            )
            return self._attempt(
                connection, order, bar.trade_date, "REJECTED", "INSUFFICIENT_POSITION"
            )
        if side is Side.SELL and quantity > available:
            return self._attempt(
                connection, order, bar.trade_date, "DEFERRED", "T_PLUS_ONE"
            )
        if side is Side.BUY:
            new_quantity = held + quantity
            new_avg = (held * avg_cost + notional + fee) / new_quantity
            new_available = available
            new_cash = cash - notional - fee
            last_buy_date = bar.trade_date
        else:
            new_quantity = held - quantity
            new_avg = avg_cost if new_quantity else 0.0
            new_available = available - quantity
            new_cash = cash + notional - fee
            last_buy_date = None if new_quantity == 0 else prior_last_buy_date
        connection.execute(
            """
            INSERT INTO positions(
                account_id, symbol, quantity, available_quantity, avg_cost, last_buy_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, symbol) DO UPDATE SET
                quantity=excluded.quantity,
                available_quantity=excluded.available_quantity,
                avg_cost=excluded.avg_cost,
                last_buy_date=excluded.last_buy_date
            """,
            (
                account_id,
                bar.symbol,
                new_quantity,
                new_available,
                new_avg,
                last_buy_date,
            ),
        )
        connection.execute(
            "UPDATE accounts SET cash = ?, updated_at = ? WHERE account_id = ?",
            (new_cash, datetime.now(UTC).isoformat(), account_id),
        )
        connection.execute(
            """
            INSERT INTO fills(
                account_id, idempotency_key, symbol, side, quantity, price, fee, trade_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                order["idempotency_key"],
                bar.symbol,
                side.value,
                quantity,
                price,
                fee,
                bar.trade_date,
            ),
        )
        connection.execute(
            "UPDATE orders SET status = 'FILLED' WHERE idempotency_key = ?",
            (order["idempotency_key"],),
        )
        return self._attempt(
            connection, order, bar.trade_date, "FILLED", None
        ) | {"price": price, "quantity": quantity, "fee": fee}
