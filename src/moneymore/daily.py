from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .config import BacktestConfig
from .data.provider import MarketDataProvider
from .data.quality import validate_calendar, validate_stock_limits
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .data.sync import sync_daily_history
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .signals import trend_decision, write_signal_artifact


@dataclass(frozen=True)
class DailyRunResult:
    mode: str
    trade_date: str
    symbol: str
    status: str
    sync: dict[str, int]
    execution: list[dict[str, object]]
    decision: dict[str, object] | None
    decision_status: str | None
    paper_status: str | None
    reconciliation: dict[str, object]
    report_path: str


def run_daily_pipeline(
    provider: MarketDataProvider,
    store: ParquetStore,
    broker: PaperBroker,
    config: BacktestConfig,
    symbol: str,
    trade_date: str,
    account_id: str = "default",
    signal_dir: str | Path = "state/signals",
    report_dir: str | Path = "state/daily-runs",
    strategy_id: str | None = None,
    active_weight: float = 0.10,
) -> DailyRunResult:
    _validate_date(trade_date)
    calendar = provider.trading_calendar(trade_date, trade_date)
    validate_calendar(calendar)
    is_open = bool((calendar["is_open"].astype(str) == "1").any())
    if not is_open:
        return _finish(
            trade_date=trade_date,
            symbol=symbol,
            status="SKIPPED_MARKET_CLOSED",
            sync={"open_days": 0, "daily_rows": 0, "factor_rows": 0},
            execution=[],
            decision=None,
            decision_status=None,
            paper_status=None,
            reconciliation=asdict(broker.reconcile(account_id)),
            report_dir=report_dir,
        )

    summary = sync_daily_history(provider, store, trade_date, trade_date)
    sync = asdict(summary)
    suspension = provider.suspensions(symbol, trade_date, trade_date)
    is_suspended = suspension is not None and not suspension.empty
    _sync_stock_limit(provider, store, symbol, trade_date)

    bars = load_total_return_stock_bars(store, symbol)
    normalized_dates = bars["date"].astype(str).str.replace("-", "", regex=False)
    selected = bars.loc[normalized_dates == trade_date]
    if len(selected) != 1:
        if is_suspended:
            return _finish(
                trade_date=trade_date,
                symbol=symbol,
                status="SKIPPED_SUSPENDED",
                sync=sync,
                execution=[],
                decision=None,
                decision_status=None,
                paper_status=None,
                reconciliation=asdict(broker.reconcile(account_id)),
                report_dir=report_dir,
            )
        raise RuntimeError(
            f"expected exactly one bar for {symbol} on {trade_date}, "
            f"found {len(selected)}"
        )

    row = selected.iloc[0]
    execution = broker.execute_pending(
        ExecutionBar(
            symbol=symbol,
            trade_date=trade_date,
            open=float(row["raw_open"]),
            close=float(row["raw_close"]),
            can_buy=bool(row.get("can_buy", True)),
            can_sell=bool(row.get("can_sell", True)),
        ),
        config,
        account_id,
    )
    decision = trend_decision(
        bars,
        trade_date,
        active_weight=active_weight,
        strategy_id=strategy_id,
    )
    if decision.as_of_date != trade_date:
        raise RuntimeError(
            f"latest signal bar {decision.as_of_date} != requested {trade_date}"
        )
    portfolio = broker.portfolio(decision.close, symbol, account_id)
    risk = create_order_intent(
        decision,
        PortfolioSnapshot(
            cash=float(portfolio["cash"]),
            equity=float(portfolio["equity"]),
            position_quantity=int(portfolio["position_quantity"]),
            reference_price=decision.close,
        ),
        lot_size=config.lot_size,
        max_symbol_weight=config.max_position_weight,
    )
    write_signal_artifact(decision, signal_dir)
    decision_status = broker.record_decision(decision)
    paper_status = (
        broker.submit(risk, account_id)
        if decision_status == "RECORDED"
        else "DUPLICATE_DECISION"
    )
    reconciliation = asdict(broker.reconcile(account_id))
    status = "COMPLETED" if reconciliation["matched"] else "FAILED_RECONCILIATION"
    result = _finish(
        trade_date=trade_date,
        symbol=symbol,
        status=status,
        sync=sync,
        execution=execution,
        decision=decision.to_dict(),
        decision_status=decision_status,
        paper_status=paper_status,
        reconciliation=reconciliation,
        report_dir=report_dir,
    )
    if not reconciliation["matched"]:
        raise RuntimeError(f"paper reconciliation failed; report={result.report_path}")
    return result


def _sync_stock_limit(
    provider: MarketDataProvider,
    store: ParquetStore,
    symbol: str,
    trade_date: str,
) -> None:
    limits = provider.stock_limits(symbol, trade_date, trade_date)
    if limits is None or limits.empty:
        return
    validate_stock_limits(limits)
    key = f"{symbol.replace('.', '_')}_{trade_date}"
    store.save_snapshot(
        "stock_limits",
        limits,
        provider.name,
        ["ts_code", "trade_date"],
        key,
    )


def _finish(
    *,
    trade_date: str,
    symbol: str,
    status: str,
    sync: dict[str, int],
    execution: list[dict[str, object]],
    decision: dict[str, object] | None,
    decision_status: str | None,
    paper_status: str | None,
    reconciliation: dict[str, object],
    report_dir: str | Path,
) -> DailyRunResult:
    target_dir = Path(report_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = target_dir / f"{trade_date}_{symbol.replace('.', '_')}_{run_id}.json"
    payload = {
        "mode": "PAPER_ONLY",
        "trade_date": trade_date,
        "symbol": symbol,
        "status": status,
        "sync": sync,
        "execution": execution,
        "decision": decision,
        "decision_status": decision_status,
        "paper_status": paper_status,
        "reconciliation": reconciliation,
        "report_path": str(target),
    }
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return DailyRunResult(**payload)


def _validate_date(value: str) -> None:
    try:
        if len(value) != 8 or not value.isdigit():
            raise ValueError
        parsed = date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError as error:
        raise ValueError("trade_date must use YYYYMMDD") from error
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("trade_date must use YYYYMMDD")
