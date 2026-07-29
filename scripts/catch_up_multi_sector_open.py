from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from moneymore.config import BacktestConfig
from moneymore.data.research import load_total_return_stock_bars
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import ExecutionBar, PaperBroker
from moneymore.multi_sector_daily import (
    MULTI_SECTOR_ACCOUNT,
    run_multi_sector_daily,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Catch up orders that were eligible at a missed market open.",
    )
    parser.add_argument("--signal-date", required=True, help="Original YYYYMMDD signal")
    parser.add_argument("--trade-date", required=True, help="Missed YYYYMMDD open")
    args = parser.parse_args()
    if args.signal_date >= args.trade_date:
        raise ValueError("signal-date must be earlier than trade-date")

    store = ParquetStore(ROOT / "data")
    broker = PaperBroker(ROOT / "state" / "paper_orders.sqlite3")
    config = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
    risk_state = broker.risk_state(MULTI_SECTOR_ACCOUNT)
    if risk_state["effective_state"] != "NORMAL":
        raise RuntimeError(f"account risk state is not NORMAL: {risk_state}")

    cancelled_current = broker.cancel_pending(
        MULTI_SECTOR_ACCOUNT,
        "SUPERSEDED_BY_OPEN_CATCH_UP",
        signal_date=args.trade_date,
    )
    restored = broker.restore_cancelled(
        MULTI_SECTOR_ACCOUNT,
        args.signal_date,
        "DATA_INCIDENT_RESOLVED",
    )
    if restored == 0:
        raise RuntimeError("no cancelled orders are eligible for catch-up")

    eligible_symbols = {
        str(row["symbol"])
        for row in broker.orders()
        if row["account_id"] == MULTI_SECTOR_ACCOUNT
        and row["signal_date"] == args.signal_date
        and row["status"] == "PENDING"
    }
    executions: list[dict[str, object]] = []
    for symbol in sorted(eligible_symbols):
        history = load_total_return_stock_bars(store, symbol)
        row = history.loc[
            history["date"].astype(str).str.replace("-", "").str[:8]
            == args.trade_date
        ]
        if row.empty:
            raise RuntimeError(f"missing execution bar: {symbol} {args.trade_date}")
        bar = row.iloc[-1]
        executions.extend(
            broker.execute_pending(
                ExecutionBar(
                    symbol=symbol,
                    trade_date=args.trade_date,
                    open=float(bar["raw_open"]),
                    close=float(bar["raw_close"]),
                    can_buy=bool(bar.get("can_buy", True)),
                    can_sell=bool(bar.get("can_sell", True)),
                ),
                config,
                MULTI_SECTOR_ACCOUNT,
            )
        )

    current_result = run_multi_sector_daily(
        store=store,
        broker=broker,
        config=config,
        trade_date=args.trade_date,
        signal_dir=ROOT / "state" / "multi-sector-signals",
        report_dir=ROOT / "state" / "multi-sector-shadow",
    )
    reconciliation = asdict(broker.reconcile(MULTI_SECTOR_ACCOUNT))
    if not reconciliation["matched"]:
        raise RuntimeError(f"catch-up reconciliation failed: {reconciliation}")
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "account_id": MULTI_SECTOR_ACCOUNT,
        "signal_date": args.signal_date,
        "trade_date": args.trade_date,
        "cancelled_current_orders": cancelled_current,
        "restored_orders": restored,
        "executions": executions,
        "current_signal_status": current_result.status,
        "portfolio": current_result.portfolio,
        "reconciliation": reconciliation,
    }
    target_dir = ROOT / "state" / "catch-up"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{args.signal_date}_{args.trade_date}.json"
    if target.exists():
        raise FileExistsError(f"catch-up audit already exists: {target}")
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
