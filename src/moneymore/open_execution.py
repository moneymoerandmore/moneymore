from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .config import BacktestConfig
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .execution.paper import ExecutionBar, PaperBroker


def execute_accounts_at_open(
    *,
    store: ParquetStore,
    broker: PaperBroker,
    config: BacktestConfig,
    trade_date: str,
    account_ids: list[str],
    audit_dir: str | Path,
) -> dict[str, object]:
    """Execute prior-session pending orders before expensive daily research."""
    accounts = sorted(set(account_ids))
    pending = [
        row
        for row in broker.orders()
        if str(row.get("account_id")) in accounts
        and str(row.get("status")) == "PENDING"
        and str(row.get("signal_date", "")) < trade_date
    ]
    symbols = sorted({str(row["symbol"]) for row in pending})
    bars: dict[str, ExecutionBar] = {}
    missing: list[str] = []
    for symbol in symbols:
        history = load_total_return_stock_bars(store, symbol)
        current = history.loc[
            history["date"].astype(str).str.replace("-", "").str[:8]
            == trade_date
        ]
        if current.empty:
            missing.append(symbol)
            continue
        row = current.iloc[-1]
        bars[symbol] = ExecutionBar(
            symbol=symbol,
            trade_date=trade_date,
            open=float(row["raw_open"]),
            close=float(row["raw_close"]),
            can_buy=bool(row.get("can_buy", True)),
            can_sell=bool(row.get("can_sell", True)),
        )

    executions: list[dict[str, object]] = []
    account_results: list[dict[str, object]] = []
    for account_id in accounts:
        account_pending = [
            row for row in pending if str(row.get("account_id")) == account_id
        ]
        pending_sells = {
            str(row["symbol"])
            for row in account_pending
            if str(row.get("side")) == "SELL"
        }
        account_symbols = {str(row["symbol"]) for row in account_pending}
        execution_order = sorted(
            account_symbols & set(bars),
            key=lambda symbol: (symbol not in pending_sells, symbol),
        )
        account_executions: list[dict[str, object]] = []
        for symbol in execution_order:
            account_executions.extend(
                broker.execute_pending(bars[symbol], config, account_id)
            )
        executions.extend(account_executions)
        account_results.append(
            {
                "account_id": account_id,
                "pending_before": len(account_pending),
                "executions": len(account_executions),
                "reconciliation": asdict(broker.reconcile(account_id)),
            }
        )

    payload: dict[str, object] = {
        "trade_date": trade_date,
        "status": "COMPLETED" if not missing else "PARTIAL_MISSING_BARS",
        "pending_orders": len(pending),
        "execution_events": len(executions),
        "missing_symbols": missing,
        "accounts": account_results,
        "executions": executions,
        "created_at": datetime.now(UTC).isoformat(),
    }
    target_dir = Path(audit_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{trade_date}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return payload
