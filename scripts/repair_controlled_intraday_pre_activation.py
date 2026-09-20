"""Remove the accidental pre-activation controlled-intraday replay atomically."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from moneymore.execution.paper import PaperBroker
from moneymore.intraday_execution import (
    EXPOSURE_INTRADAY_ACCOUNT,
    prepare_exposure_intraday_branch_orders,
)

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "state" / "paper_orders.sqlite3"
BAD_SIGNAL_DATE = "20260907"
BAD_TRADE_DATE = "20260908"


def main() -> None:
    account_id = EXPOSURE_INTRADAY_ACCOUNT
    with sqlite3.connect(DATABASE, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        bad_orders = [dict(row) for row in connection.execute(
            "SELECT rowid, * FROM orders WHERE account_id = ? AND signal_date = ? "
            "AND strategy_id LIKE '%historical_replay%'",
            (account_id, BAD_SIGNAL_DATE),
        )]
        keys = [str(row["idempotency_key"]) for row in bad_orders]
        bad_fills = [] if not keys else [dict(row) for row in connection.execute(
            f"SELECT * FROM fills WHERE idempotency_key IN ({','.join('?' for _ in keys)})",
            keys,
        )]
        if not bad_orders:
            raise RuntimeError("no targeted pre-activation replay rows found")
        connection.execute("BEGIN IMMEDIATE")
        for key in keys:
            connection.execute("DELETE FROM execution_attempts WHERE idempotency_key = ?", (key,))
            connection.execute("DELETE FROM fills WHERE idempotency_key = ?", (key,))
            connection.execute("DELETE FROM orders WHERE idempotency_key = ?", (key,))

        initial_cash = float(connection.execute(
            "SELECT initial_cash FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()[0])
        remaining = [dict(row) for row in connection.execute(
            "SELECT * FROM fills WHERE account_id = ? ORDER BY trade_date, id", (account_id,)
        )]
        cash = initial_cash
        positions: dict[str, dict[str, object]] = {}
        current_date = ""
        for fill in remaining:
            trade_date = str(fill["trade_date"])
            if trade_date != current_date:
                for position in positions.values():
                    position["available_quantity"] = position["quantity"]
                current_date = trade_date
            symbol = str(fill["symbol"])
            position = positions.setdefault(symbol, {
                "quantity": 0, "available_quantity": 0, "avg_cost": 0.0,
                "last_buy_date": None,
            })
            quantity = int(fill["quantity"])
            price = float(fill["price"])
            fee = float(fill["fee"])
            held = int(position["quantity"])
            if str(fill["side"]) == "BUY":
                new_quantity = held + quantity
                position["avg_cost"] = (
                    held * float(position["avg_cost"]) + quantity * price + fee
                ) / new_quantity
                position["quantity"] = new_quantity
                position["last_buy_date"] = trade_date
                cash -= quantity * price + fee
            else:
                position["quantity"] = held - quantity
                position["available_quantity"] = int(position["available_quantity"]) - quantity
                if int(position["quantity"]) == 0:
                    position["avg_cost"] = 0.0
                    position["last_buy_date"] = None
                cash += quantity * price - fee
        connection.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        for symbol, position in positions.items():
            if int(position["quantity"]) <= 0:
                continue
            connection.execute(
                "INSERT INTO positions(account_id,symbol,quantity,available_quantity,avg_cost,last_buy_date) "
                "VALUES (?,?,?,?,?,?)",
                (account_id, symbol, position["quantity"], position["available_quantity"],
                 position["avg_cost"], position["last_buy_date"]),
            )
        connection.execute(
            "UPDATE accounts SET cash = ?, updated_at = ? WHERE account_id = ?",
            (cash, datetime.now(UTC).isoformat(), account_id),
        )

    audit_path = ROOT / "state" / "baseline-pysystemtrade-intraday-execution" / f"{BAD_TRADE_DATE}.json"
    audit_path.unlink(missing_ok=True)
    daily_path = ROOT / "data" / "processed" / "baseline_pysystemtrade_intraday_account_daily.parquet"
    daily = pd.read_parquet(daily_path)
    daily = daily.loc[daily["trade_date"].astype(str) != BAD_TRADE_DATE]
    temporary = daily_path.with_suffix(".parquet.repair.tmp")
    daily.to_parquet(temporary, index=False)
    temporary.replace(daily_path)

    restored = prepare_exposure_intraday_branch_orders(PaperBroker(DATABASE), "20260921")
    report = {
        "repaired_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "removed_signal_date": BAD_SIGNAL_DATE,
        "removed_trade_date": BAD_TRADE_DATE,
        "removed_orders": bad_orders,
        "removed_fills": bad_fills,
        "rebuilt_cash": cash,
        "restored_next_session_orders": restored,
    }
    target = ROOT / "state" / "catch-up" / "controlled_intraday_pre_activation_repair.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"removed_orders", "removed_fills"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
