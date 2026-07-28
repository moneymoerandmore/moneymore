from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .config import BacktestConfig
from .data.provider import MarketDataProvider
from .data.quality import validate_calendar, validate_daily_basic
from .data.research import load_point_in_time_features, load_total_return_stock_bars
from .data.store import ParquetStore
from .data.sync import sync_daily_history
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .factors import PreprocessConfig, build_default_registry, preprocess_cross_section
from .research.bank_model import (
    BankFactorModel,
    rebalance_topk_holdings,
    score_bank_factors,
)
from .research.bank_timing import (
    VolatilityTargetRiskDegree,
    build_bank_market_index,
)
from .signals import SignalDecision, write_signal_artifact

BANK_ACCOUNT = "bank_shadow"
BANK_STRATEGY = "bank_multifactor_no_quality_candidate"


@dataclass(frozen=True)
class BankDailyResult:
    mode: str
    trade_date: str
    status: str
    universe_size: int
    holdings: list[str]
    timing: dict[str, object]
    executions: list[dict[str, object]]
    orders: list[dict[str, object]]
    portfolio: dict[str, object]
    reconciliation: dict[str, object]
    report_path: str


def run_bank_daily_pipeline(
    provider: MarketDataProvider,
    store: ParquetStore,
    broker: PaperBroker,
    config: BacktestConfig,
    trade_date: str,
    signal_dir: str | Path,
    report_dir: str | Path,
) -> BankDailyResult:
    broker.initialize_account(config.initial_cash, BANK_ACCOUNT)
    calendar = provider.trading_calendar(trade_date, trade_date)
    validate_calendar(calendar)
    if not bool((calendar["is_open"].astype(str) == "1").any()):
        return _finish(
            trade_date,
            "SKIPPED_MARKET_CLOSED",
            [],
            {},
            [],
            [],
            broker,
            {},
            report_dir,
        )
    sync_daily_history(provider, store, trade_date, trade_date)
    membership = store.read(
        "universe_membership", filters=[("universe", "==", "bank_cn")]
    ).copy()
    membership["date"] = pd.to_datetime(membership["date"])
    as_of = pd.Timestamp(trade_date)
    membership = membership.loc[membership["date"] <= as_of]
    latest_membership = membership.loc[
        membership["date"] == membership["date"].max()
    ]
    symbols = sorted(latest_membership["ts_code"].unique())
    _sync_daily_basic(provider, store, symbols, trade_date)

    registry = build_default_registry()
    factor_names = list(registry.names())
    factor_panels = []
    timing_panels = []
    marks: dict[str, float] = {}
    executions: list[dict[str, object]] = []
    for symbol in symbols:
        bars = load_total_return_stock_bars(store, symbol)
        dates = pd.to_datetime(bars["date"])
        history = bars.loc[dates <= as_of]
        if history.empty:
            continue
        row = history.iloc[-1]
        timing_panels.append(history[["date", "symbol", "signal_close"]].copy())
        marks[symbol] = float(row["raw_close"])
        if pd.Timestamp(row["date"]).strftime("%Y%m%d") == trade_date:
            executions.extend(
                broker.execute_pending(
                    ExecutionBar(
                        symbol=symbol,
                        trade_date=trade_date,
                        open=float(row["raw_open"]),
                        close=float(row["raw_close"]),
                        can_buy=bool(row.get("can_buy", True)),
                        can_sell=bool(row.get("can_sell", True)),
                    ),
                    config,
                    BANK_ACCOUNT,
                )
            )
        features = load_point_in_time_features(store, symbol)
        history_features = features.loc[
            pd.to_datetime(features["date"]) <= as_of
        ]
        computed = registry.compute(history_features, factor_names).tail(1)
        controls = history_features[["date", "symbol", "total_mv"]].tail(1)
        factor_panels.append(
            computed.merge(
                controls, on=["date", "symbol"], how="left", validate="one_to_one"
            )
        )

    factors = pd.concat(factor_panels, ignore_index=True)
    for name in factor_names:
        if registry.get(name).direction.value == "low_is_better":
            factors[name] = -factors[name]
    processed = preprocess_cross_section(
        factors,
        factor_names,
        PreprocessConfig(industry_column=None, minimum_assets=10),
    )
    model = BankFactorModel(
        model_id=BANK_STRATEGY,
        value_weight=0.470588,
        defensive_weight=0.294118,
        momentum_weight=0.235294,
        quality_weight=0.0,
    )
    scores = score_bank_factors(processed, model)
    previous = _previous_holdings(report_dir, trade_date)
    holdings, ranking = rebalance_topk_holdings(scores, previous)
    timing_strategy = VolatilityTargetRiskDegree()
    market = build_bank_market_index(pd.concat(timing_panels, ignore_index=True))
    timing_degrees = timing_strategy.risk_degrees(market)
    risk_degree = float(timing_degrees.iloc[-1]["risk_degree"])
    timing = {
        "strategy": timing_strategy.strategy_id,
        "interface": "qlib_risk_degree",
        "risk_degree": risk_degree,
        "base_gross_exposure": 0.8,
        "bank_budget_fraction": risk_degree / 0.8,
        "status": "PROSPECTIVE_CANDIDATE",
    }
    account = broker.account_snapshot(marks, BANK_ACCOUNT)
    position_map = {
        str(position["symbol"]): position
        for position in account["positions"]  # type: ignore[union-attr]
    }
    orders: list[dict[str, object]] = []
    target_symbols = sorted(set(symbols) | set(position_map))
    for symbol in sorted(target_symbols, key=lambda item: item in holdings):
        close = marks.get(symbol)
        if close is None:
            continue
        position = position_map.get(symbol, {})
        decision = SignalDecision(
            strategy_id=BANK_STRATEGY,
            symbol=symbol,
            as_of_date=trade_date,
            target_weight=(
                0.10 * risk_degree / 0.8 if symbol in holdings else 0.0
            ),
            action="HOLD" if symbol in holdings else "EXIT",
            reason_code="BANK_TOPK_SELECTED" if symbol in holdings else "BANK_TOPK_EXIT",
            close=close,
            fast_ma=None,
            slow_ma=None,
            history_bars=0,
        )
        risk = create_order_intent(
            decision,
            PortfolioSnapshot(
                cash=float(account["cash"]),
                equity=float(account["equity"]),
                position_quantity=int(position.get("quantity", 0)),
                reference_price=close,
            ),
            lot_size=config.lot_size,
            max_symbol_weight=config.max_position_weight,
        )
        write_signal_artifact(decision, signal_dir)
        decision_status = broker.record_decision(decision)
        submit_status = (
            broker.submit(risk, BANK_ACCOUNT)
            if decision_status == "RECORDED"
            else "DUPLICATE_DECISION"
        )
        orders.append(
            {
                "symbol": symbol,
                "target": decision.target_weight,
                "decision_status": decision_status,
                "submit_status": submit_status,
                "rejection_code": risk.rejection_code,
            }
        )
    reconciliation = asdict(broker.reconcile(BANK_ACCOUNT))
    portfolio = broker.account_snapshot(marks, BANK_ACCOUNT)
    result = _finish(
        trade_date,
        "COMPLETED" if reconciliation["matched"] else "FAILED_RECONCILIATION",
        sorted(holdings),
        timing,
        executions,
        orders,
        broker,
        portfolio,
        report_dir,
        ranking,
    )
    if not reconciliation["matched"]:
        raise RuntimeError(f"bank shadow reconciliation failed: {result.report_path}")
    return result


def _sync_daily_basic(
    provider: MarketDataProvider,
    store: ParquetStore,
    symbols: list[str],
    trade_date: str,
) -> None:
    snapshots = []
    for symbol in symbols:
        frame = provider.daily_basic(symbol, trade_date, trade_date)
        if frame is None or frame.empty:
            continue
        validate_daily_basic(frame)
        snapshots.append((f"{symbol.replace('.', '_')}_{trade_date}", frame))
    store.save_snapshots(
        "daily_basic", snapshots, provider.name, ["ts_code", "trade_date"]
    )


def _previous_holdings(report_dir: str | Path, trade_date: str) -> set[str]:
    directory = Path(report_dir)
    if not directory.exists():
        return set()
    candidates = sorted(
        path for path in directory.glob("*.json") if path.stem < trade_date
    )
    if not candidates:
        return set()
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return set(payload.get("holdings", []))


def _finish(
    trade_date: str,
    status: str,
    holdings: list[str],
    timing: dict[str, object],
    executions: list[dict[str, object]],
    orders: list[dict[str, object]],
    broker: PaperBroker,
    portfolio: dict[str, object],
    report_dir: str | Path,
    ranking: pd.DataFrame | None = None,
) -> BankDailyResult:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{trade_date}.json"
    reconciliation = asdict(broker.reconcile(BANK_ACCOUNT))
    payload = {
        "mode": "PAPER_ONLY",
        "trade_date": trade_date,
        "status": status,
        "universe_size": len(ranking) if ranking is not None else 0,
        "holdings": holdings,
        "timing": timing,
        "executions": executions,
        "orders": orders,
        "portfolio": portfolio,
        "reconciliation": reconciliation,
        "ranking": (
            json.loads(ranking.to_json(orient="records")) if ranking is not None else []
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "report_path": str(target),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        payload["created_at"] = existing["created_at"]
        payload["report_path"] = existing["report_path"]
    else:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    return BankDailyResult(
        **{key: payload[key] for key in BankDailyResult.__dataclass_fields__}
    )
