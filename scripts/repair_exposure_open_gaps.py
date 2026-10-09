"""Repair missed open executions for the exposure-controlled paper account.

The repair rebuilds the account from its immutable pre-gap fills, then replays
each missed session in chronological order at the official daily open.  The
current session's plan is deliberately left pending for QMT session-open
execution.  This is a paper-account repair only; it never sends broker orders.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from moneymore.baseline_exposure_daily import BASELINE_EXPOSURE_ACCOUNT
from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import PaperBroker
from moneymore.open_execution import execute_accounts_at_open
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def refresh_daily_history(database: Path, trade_dates: list[str]) -> list[dict[str, object]]:
    """Revalue repaired sessions from the reconstructed fill ledger."""
    account_id = BASELINE_EXPOSURE_ACCOUNT
    store = ParquetStore(ROOT / "data")
    history = store.read("multi_sector_pysystemtrade_account_daily")
    rows: list[dict[str, object]] = []
    with sqlite3.connect(database) as connection:
        initial_cash = float(connection.execute(
            "SELECT initial_cash FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()[0])
        fills = list(connection.execute(
            "SELECT symbol,side,quantity,price,fee,trade_date FROM fills "
            "WHERE account_id = ? ORDER BY trade_date,id", (account_id,)
        ))
        actions = list(connection.execute(
            "SELECT symbol,action_type,cash_amount,share_quantity,trade_date "
            "FROM corporate_action_ledger WHERE account_id = ? ORDER BY trade_date,id",
            (account_id,),
        ))
    previous_rows = history.loc[
        history["trade_date"].astype(str) < min(trade_dates)
    ].sort_values("trade_date")
    previous_equity = (
        float(previous_rows.iloc[-1]["equity"]) if not previous_rows.empty else initial_cash
    )
    for trade_date in trade_dates:
        cash = initial_cash
        quantities: dict[str, int] = {}
        for symbol, side, quantity, price, fee, fill_date in fills:
            if str(fill_date) > trade_date:
                continue
            notional = int(quantity) * float(price)
            quantities[str(symbol)] = quantities.get(str(symbol), 0) + (
                int(quantity) if str(side) == "BUY" else -int(quantity)
            )
            cash += (-notional - float(fee)) if str(side) == "BUY" else (notional - float(fee))
        for symbol, action_type, cash_amount, share_quantity, action_date in actions:
            if str(action_date) > trade_date:
                continue
            if str(action_type) == "CASH_DIVIDEND":
                cash += float(cash_amount)
            elif str(action_type) == "STOCK_DIVIDEND":
                quantities[str(symbol)] = quantities.get(str(symbol), 0) + int(share_quantity)
        symbols = [symbol for symbol, quantity in quantities.items() if quantity]
        daily = store.read(
            "daily", columns=["ts_code", "trade_date", "close"],
            filters=[("trade_date", "==", trade_date), ("ts_code", "in", symbols)],
        ) if symbols else pd.DataFrame()
        closes = dict(zip(daily.get("ts_code", []), daily.get("close", [])))
        missing = sorted(set(symbols) - set(closes))
        if missing:
            raise RuntimeError(f"missing close marks for {trade_date}: {missing}")
        market_value = sum(quantities[symbol] * float(closes[symbol]) for symbol in symbols)
        equity = cash + market_value
        prior = history.loc[history["trade_date"].astype(str) == trade_date]
        target_exposure = float(prior.iloc[-1]["target_exposure"]) if not prior.empty else 1.0
        row = {
            "trade_date": trade_date,
            "status": "COMPLETED",
            "equity": equity,
            "cash": cash,
            "market_value": market_value,
            "gross_exposure": market_value / equity if equity else 0.0,
            "target_exposure": target_exposure,
            "daily_return": equity / previous_equity - 1.0 if previous_equity else 0.0,
            "reconciled": True,
        }
        rows.append(row)
        previous_equity = equity
    store.merge_curated(
        "multi_sector_pysystemtrade_account_daily",
        [pd.DataFrame(rows)],
        ["trade_date"],
    )
    return rows


def rebuild_pre_gap_account(database: Path, first_signal_date: str) -> dict[str, object]:
    account_id = BASELINE_EXPOSURE_ACCOUNT
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        account = connection.execute(
            "SELECT initial_cash FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise RuntimeError(f"missing account: {account_id}")
        keys = [
            str(row[0])
            for row in connection.execute(
                "SELECT idempotency_key FROM orders "
                "WHERE account_id = ? AND signal_date >= ?",
                (account_id, first_signal_date),
            )
        ]
        if not keys:
            raise RuntimeError("no exposure orders found in requested repair range")
        placeholders = ",".join("?" for _ in keys)
        removed_fills = connection.execute(
            f"SELECT COUNT(*) FROM fills WHERE account_id = ? "
            f"AND idempotency_key IN ({placeholders})",
            (account_id, *keys),
        ).fetchone()[0]
        connection.execute(
            f"DELETE FROM fills WHERE account_id = ? "
            f"AND idempotency_key IN ({placeholders})",
            (account_id, *keys),
        )
        connection.execute(
            f"DELETE FROM execution_attempts WHERE idempotency_key IN ({placeholders})",
            keys,
        )
        connection.execute(
            f"UPDATE orders SET status = 'PENDING' "
            f"WHERE account_id = ? AND idempotency_key IN ({placeholders})",
            (account_id, *keys),
        )

        cash = float(account[0])
        positions: dict[str, dict[str, float | int | str | None]] = {}
        for fill in connection.execute(
            "SELECT symbol, side, quantity, price, fee, trade_date FROM fills "
            "WHERE account_id = ? ORDER BY id",
            (account_id,),
        ):
            symbol, side = str(fill[0]), str(fill[1])
            quantity, price, fee = int(fill[2]), float(fill[3]), float(fill[4])
            item = positions.setdefault(
                symbol, {"quantity": 0, "avg_cost": 0.0, "last_buy_date": None}
            )
            held, avg = int(item["quantity"]), float(item["avg_cost"])
            notional = quantity * price
            if side == "BUY":
                new_quantity = held + quantity
                item["avg_cost"] = (held * avg + notional + fee) / new_quantity
                item["quantity"] = new_quantity
                item["last_buy_date"] = str(fill[5])
                cash -= notional + fee
            else:
                new_quantity = held - quantity
                if new_quantity < 0:
                    raise RuntimeError(f"negative rebuilt position: {symbol}")
                item["quantity"] = new_quantity
                item["avg_cost"] = avg if new_quantity else 0.0
                item["last_buy_date"] = item["last_buy_date"] if new_quantity else None
                cash += notional - fee
        for action in connection.execute(
            "SELECT symbol, action_type, cash_amount, share_quantity "
            "FROM corporate_action_ledger WHERE account_id = ? ORDER BY id",
            (account_id,),
        ):
            symbol, action_type = str(action[0]), str(action[1])
            if action_type == "CASH_DIVIDEND":
                cash += float(action[2])
            elif action_type == "STOCK_DIVIDEND":
                item = positions.setdefault(
                    symbol, {"quantity": 0, "avg_cost": 0.0, "last_buy_date": None}
                )
                held, shares = int(item["quantity"]), int(action[3])
                new_quantity = held + shares
                item["avg_cost"] = (
                    held * float(item["avg_cost"]) / new_quantity if new_quantity else 0.0
                )
                item["quantity"] = new_quantity

        connection.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        for symbol, item in positions.items():
            quantity = int(item["quantity"])
            if quantity <= 0:
                continue
            connection.execute(
                "INSERT INTO positions(account_id,symbol,quantity,available_quantity,"
                "avg_cost,last_buy_date) VALUES (?,?,?,?,?,?)",
                (
                    account_id,
                    symbol,
                    quantity,
                    quantity,
                    float(item["avg_cost"]),
                    item["last_buy_date"],
                ),
            )
        connection.execute(
            "UPDATE accounts SET cash = ?, updated_at = ? WHERE account_id = ?",
            (cash, datetime.now(UTC).isoformat(), account_id),
        )
    return {"reset_orders": len(keys), "removed_fills": int(removed_fills), "cash": cash}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--first-signal-date", default="20260928")
    parser.add_argument(
        "--trade-dates", default="20260929,20260930,20261008"
    )
    args = parser.parse_args()
    if not args.commit:
        raise RuntimeError("repair is write-bearing; pass --commit after taking a backup")

    database = ROOT / "state" / "paper_orders.sqlite3"
    backup_dir = ROOT / "state" / "backups" / "20261009-exposure-gap-repair"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "paper_orders.before.sqlite3"
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(database, backup)

    reset = rebuild_pre_gap_account(database, args.first_signal_date)
    store = ParquetStore(ROOT / "data")
    broker = PaperBroker(database)
    config = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
    results = []
    for trade_date in [item for item in args.trade_dates.split(",") if item]:
        result = execute_accounts_at_open(
            store=store,
            broker=broker,
            config=config,
            trade_date=trade_date,
            account_ids=[BASELINE_EXPOSURE_ACCOUNT],
            audit_dir=ROOT / "state" / "open-execution-recovery",
        )
        results.append(result)
    reconciliation = broker.reconcile(BASELINE_EXPOSURE_ACCOUNT)
    if not reconciliation.matched:
        raise RuntimeError(f"repair reconciliation failed: {reconciliation}")
    refreshed_history = refresh_daily_history(database, args.trade_dates.split(","))
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "account_id": BASELINE_EXPOSURE_ACCOUNT,
        "first_signal_date": args.first_signal_date,
        "trade_dates": args.trade_dates.split(","),
        "reset": reset,
        "results": results,
        "reconciliation": reconciliation.__dict__,
        "refreshed_history": refreshed_history,
        "backup": str(backup),
    }
    target = ROOT / "state" / "catch-up" / "20261009_exposure_open_gap_repair.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
