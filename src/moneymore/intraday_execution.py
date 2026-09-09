from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import BacktestConfig
from .corporate_actions import process_corporate_actions
from .data.qmt_provider import QmtTushareProvider
from .data.store import ParquetStore
from .execution.paper import ExecutionBar, PaperBroker
from .multi_sector_daily import MULTI_SECTOR_ACCOUNT

SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_ID = "adaptive_vwap_sniper_v1"
INTRADAY_ACCOUNT = "multi_sector_intraday_shadow"


def _ensure_comparison_account_and_orders(broker: PaperBroker, trade_date: str) -> int:
    """Clone the baseline once, then mirror eligible parent orders exactly."""
    with sqlite3.connect(broker.database) as connection:
        source = connection.execute(
            "SELECT initial_cash, cash, updated_at FROM accounts WHERE account_id = ?",
            (MULTI_SECTOR_ACCOUNT,),
        ).fetchone()
        if source is None:
            raise RuntimeError("baseline paper account is not initialized")
        exists = connection.execute(
            "SELECT 1 FROM accounts WHERE account_id = ?", (INTRADAY_ACCOUNT,)
        ).fetchone()
        if exists is None:
            connection.execute(
                "INSERT INTO accounts(account_id, initial_cash, cash, updated_at) VALUES (?, ?, ?, ?)",
                (INTRADAY_ACCOUNT, *source),
            )
            connection.execute(
                """
                INSERT INTO positions(
                    account_id, symbol, quantity, available_quantity, avg_cost, last_buy_date
                )
                SELECT ?, symbol, quantity, available_quantity, avg_cost, last_buy_date
                FROM positions WHERE account_id = ?
                """,
                (INTRADAY_ACCOUNT, MULTI_SECTOR_ACCOUNT),
            )
        parents = connection.execute(
            """
            SELECT o.* FROM orders o
            WHERE o.account_id = ? AND o.signal_date < ?
              AND (
                o.status = 'PENDING'
                OR EXISTS (
                    SELECT 1 FROM fills f
                    WHERE f.idempotency_key = o.idempotency_key
                      AND f.trade_date = ?
                )
              )
            ORDER BY o.created_at, o.idempotency_key
            """,
            (MULTI_SECTOR_ACCOUNT, trade_date, trade_date),
        ).fetchall()
        mirrored = 0
        for row in parents:
            key = f"{INTRADAY_ACCOUNT}:{row[0]}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO orders(
                    idempotency_key, created_at, status, strategy_id, symbol,
                    side, quantity, signal_date, reason_code, account_id
                ) VALUES (?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    datetime.now(UTC).isoformat(),
                    f"{POLICY_ID}:{row[3]}",
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    INTRADAY_ACCOUNT,
                ),
            )
            mirrored += int(cursor.rowcount > 0)
    return mirrored


def prepare_intraday_branch_orders(broker: PaperBroker, trade_date: str) -> int:
    """Mirror parent orders before open execution; recover same-day races safely."""
    return _ensure_comparison_account_and_orders(broker, trade_date)


@dataclass(frozen=True)
class IntradayDecision:
    symbol: str
    side: str
    action: str
    reason: str
    price: float
    vwap: float
    spread_bps: float
    urgency: float


def decide_intraday_execution(
    *,
    symbol: str,
    side: str,
    price: float,
    open_price: float,
    vwap: float,
    bid1: float,
    ask1: float,
    now_time: time,
) -> IntradayDecision:
    start = time(9, 35)
    deadline = time(14, 50)
    if price <= 0 or open_price <= 0 or vwap <= 0:
        return IntradayDecision(symbol, side, "WAIT", "INVALID_QUOTE", price, vwap, 0, 0)
    spread_bps = (ask1 - bid1) / price * 10_000 if ask1 > 0 and bid1 > 0 and ask1 >= bid1 else 0
    if now_time < start:
        return IntradayDecision(
            symbol, side, "WAIT", "OPEN_NOISE_WINDOW", price, vwap, spread_bps, 0
        )
    minutes = max(0, (now_time.hour * 60 + now_time.minute) - (9 * 60 + 35))
    urgency = min(1.0, minutes / 315)
    if now_time >= deadline:
        return IntradayDecision(
            symbol, side, "EXECUTE", "DEADLINE_FALLBACK", price, vwap, spread_bps, 1
        )
    if spread_bps > 30:
        return IntradayDecision(
            symbol, side, "WAIT", "SPREAD_TOO_WIDE", price, vwap, spread_bps, urgency
        )
    if side == "BUY":
        favorable = price <= vwap * (1.0005 + urgency * 0.0015) and price <= open_price * 1.005
    else:
        favorable = price >= vwap * (0.9995 - urgency * 0.0015) and price >= open_price * 0.995
    return IntradayDecision(
        symbol,
        side,
        "EXECUTE" if favorable else "WAIT",
        "VWAP_SNIPER_TRIGGER" if favorable else "WAIT_FOR_PRICE_IMPROVEMENT",
        price,
        vwap,
        spread_bps,
        urgency,
    )


def run_baseline_intraday_once(
    *,
    root: Path,
    now: datetime | None = None,
) -> dict[str, object]:
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    trade_date = current.strftime("%Y%m%d")
    broker = PaperBroker(root / "state" / "paper_orders.sqlite3")
    mirrored = _ensure_comparison_account_and_orders(broker, trade_date)
    pending = [
        row
        for row in broker.orders()
        if row.get("account_id") == INTRADAY_ACCOUNT
        and row.get("status") == "PENDING"
        and str(row.get("signal_date", "")) < trade_date
    ]
    payload: dict[str, object] = {
        "policy_id": POLICY_ID,
        "trade_date": trade_date,
        "observed_at": current.isoformat(),
        "account_id": INTRADAY_ACCOUNT,
        "mirrored_orders": mirrored,
        "pending_orders": len(pending),
        "decisions": [],
        "executions": [],
        "status": "TRACKING" if not pending else "RUNNING",
    }
    empty_account = broker.account_snapshot({}, INTRADAY_ACCOUNT)
    held_symbols = {str(row["symbol"]) for row in empty_account.get("positions", [])}
    symbols = sorted(held_symbols | {str(row["symbol"]) for row in pending})
    if not symbols:
        payload["account"] = empty_account
        return payload
    provider = QmtTushareProvider(root=root)
    quotes = provider.intraday_quotes(symbols)
    if quotes.empty:
        payload["status"] = "QMT_QUOTES_EMPTY"
        return payload
    store = ParquetStore(root / "data")
    quotes["trade_date"] = trade_date
    quotes["observed_at"] = current.isoformat()
    store.merge_curated(
        "baseline_intraday_snapshots",
        [quotes],
        ["trade_date", "observed_at", "symbol"],
    )
    config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
    quote_map = {str(row["symbol"]): row for row in quotes.to_dict("records")}
    account = broker.account_snapshot(
        {symbol: float(row["last"]) for symbol, row in quote_map.items()},
        INTRADAY_ACCOUNT,
    )
    positions = {str(row["symbol"]): row for row in account.get("positions", [])}
    decisions: list[dict[str, object]] = []
    executions: list[dict[str, object]] = []
    for order in sorted(pending, key=lambda row: (row.get("side") != "SELL", row["symbol"])):
        quote = quote_map.get(str(order["symbol"]))
        if not quote:
            continue
        decision = decide_intraday_execution(
            symbol=str(order["symbol"]),
            side=str(order["side"]),
            price=float(quote["last"]),
            open_price=float(quote["open"]),
            vwap=float(quote["vwap"]),
            bid1=float(quote["bid1"]),
            ask1=float(quote["ask1"]),
            now_time=current.time(),
        )
        detail = asdict(decision) | {
            "idempotency_key": order["idempotency_key"],
            "quantity": int(order["quantity"]),
            "account_cash": float(account["cash"]),
            "held_quantity": int(positions.get(decision.symbol, {}).get("quantity", 0)),
            "available_quantity": int(
                positions.get(decision.symbol, {}).get("available_quantity", 0)
            ),
            "average_cost": float(positions.get(decision.symbol, {}).get("avg_cost", 0)),
        }
        decisions.append(detail)
        if decision.action != "EXECUTE":
            continue
        can_buy = not quote.get("up_limit") or decision.price < float(quote["up_limit"])
        can_sell = not quote.get("down_limit") or decision.price > float(quote["down_limit"])
        executions.extend(
            broker.execute_pending(
                ExecutionBar(
                    symbol=decision.symbol,
                    trade_date=trade_date,
                    open=decision.price,
                    close=decision.price,
                    can_buy=can_buy,
                    can_sell=can_sell,
                ),
                config,
                INTRADAY_ACCOUNT,
            )
        )
    process_corporate_actions(store, broker, INTRADAY_ACCOUNT, trade_date, held_symbols)
    marks = {symbol: float(row["last"]) for symbol, row in quote_map.items()}
    account = broker.account_snapshot(marks, INTRADAY_ACCOUNT)
    equity = float(account["equity"])
    market_value = float(account["market_value"])
    account_row = pd.DataFrame(
        [
            {
                "account_id": INTRADAY_ACCOUNT,
                "trade_date": trade_date,
                "observed_at": current.isoformat(),
                "equity": equity,
                "cash": float(account["cash"]),
                "market_value": market_value,
                "gross_exposure": market_value / equity if equity else 0.0,
            }
        ]
    )
    store.merge_curated(
        "baseline_intraday_account_ticks",
        [account_row],
        ["account_id", "trade_date", "observed_at"],
    )
    store.merge_curated(
        "baseline_intraday_account_daily",
        [account_row],
        ["account_id", "trade_date"],
    )
    payload["decisions"] = decisions
    payload["executions"] = executions
    payload["account"] = account
    payload["status"] = (
        "COMPLETED" if executions else ("WAITING_FOR_TRIGGER" if pending else "TRACKING")
    )
    audit_dir = root / "state" / "intraday-execution"
    audit_dir.mkdir(parents=True, exist_ok=True)
    target = audit_dir / f"{trade_date}.json"
    history = []
    if target.exists():
        history = json.loads(target.read_text(encoding="utf-8")).get("observations", [])
    history.append(payload)
    target.write_text(
        json.dumps(
            {"trade_date": trade_date, "policy_id": POLICY_ID, "observations": history[-600:]},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload
