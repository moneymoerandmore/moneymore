from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from .backtest import run_daily_backtest
from .config import BacktestConfig
from .daily import run_daily_pipeline
from .data.fundamental_sync import sync_stock_fundamentals
from .data.research import load_adjusted_stock_bars, load_total_return_stock_bars
from .data.research_sync import sync_research_reference
from .data.store import ParquetStore
from .data.sync import (
    sync_daily_history,
    sync_etf_history,
    sync_etf_reference_data,
    sync_reference_data,
)
from .data.tushare_provider import TushareProvider
from .execution.paper import ExecutionBar, PaperBroker
from .execution.risk import PortfolioSnapshot, create_order_intent
from .research.detail import (
    allocation_comparison,
    annual_comparison,
    benchmark_metrics,
    candidate_result,
    trade_ledger,
)
from .research.single_stock import research_single_stock, robustness_single_stock
from .signals import trend_decision, write_signal_artifact
from .strategy import moving_average_trend


def demo() -> None:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-04", periods=500)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, len(dates))))
    bars = pd.DataFrame(
        {
            "date": dates,
            "symbol": "DEMO",
            "open": close * (1 + rng.normal(0, 0.002, len(dates))),
            "close": close,
            "tradable": True,
        }
    )
    signals = moving_average_trend(bars)
    root = Path(__file__).resolve().parents[2]
    config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
    result = run_daily_backtest(signals, config)
    print(f"bars={len(bars)} fills={len(result.fills)}")
    print(f"total_return={result.total_return:.2%} max_drawdown={result.max_drawdown:.2%}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="moneymore")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo")
    sync_parser = subparsers.add_parser("sync-tushare")
    sync_parser.add_argument("--start", required=True, help="YYYYMMDD")
    sync_parser.add_argument("--end", required=True, help="YYYYMMDD")
    sync_parser.add_argument("--data-dir", default="data")
    sync_parser.add_argument("--batch-days", type=int, default=20)
    sync_parser.add_argument(
        "--asset", choices=["stocks", "etfs", "both"], default="stocks"
    )
    backtest_parser = subparsers.add_parser("backtest-stock")
    backtest_parser.add_argument("--symbol", required=True, help="e.g. 000001.SZ")
    backtest_parser.add_argument("--fast", type=int, default=20)
    backtest_parser.add_argument("--slow", type=int, default=60)
    backtest_parser.add_argument("--data-dir", default="data")
    research_parser = subparsers.add_parser("research-stock")
    research_parser.add_argument("--symbol", required=True, help="e.g. 600036.SH")
    research_parser.add_argument("--data-dir", default="data")
    reference_parser = subparsers.add_parser("sync-research-reference")
    reference_parser.add_argument("--symbol", required=True)
    reference_parser.add_argument("--start", required=True)
    reference_parser.add_argument("--end", required=True)
    reference_parser.add_argument("--data-dir", default="data")
    fundamental_parser = subparsers.add_parser("sync-fundamentals")
    fundamental_parser.add_argument("--symbol", action="append", required=True)
    fundamental_parser.add_argument("--start", required=True)
    fundamental_parser.add_argument("--end", required=True)
    fundamental_parser.add_argument("--data-dir", default="data")
    paper_parser = subparsers.add_parser("paper-signal")
    paper_parser.add_argument("--symbol", required=True)
    paper_parser.add_argument("--as-of", required=True, help="YYYYMMDD")
    paper_parser.add_argument("--cash", type=float, default=1_000_000)
    paper_parser.add_argument("--equity", type=float, default=1_000_000)
    paper_parser.add_argument("--position", type=int, default=0)
    paper_parser.add_argument("--data-dir", default="data")
    paper_parser.add_argument("--database", default="state/paper_orders.sqlite3")
    paper_parser.add_argument("--signal-dir", default="state/signals")
    paper_parser.add_argument("--account", default="default")
    paper_parser.add_argument(
        "--account-state",
        action="store_true",
        help="derive cash/equity/position from the initialized paper account",
    )
    paper_init_parser = subparsers.add_parser("paper-init")
    paper_init_parser.add_argument("--cash", type=float, default=1_000_000)
    paper_init_parser.add_argument("--account", default="default")
    paper_init_parser.add_argument("--database", default="state/paper_orders.sqlite3")
    paper_execute_parser = subparsers.add_parser("paper-execute")
    paper_execute_parser.add_argument("--symbol", required=True)
    paper_execute_parser.add_argument("--trade-date", required=True, help="YYYYMMDD")
    paper_execute_parser.add_argument("--account", default="default")
    paper_execute_parser.add_argument("--data-dir", default="data")
    paper_execute_parser.add_argument(
        "--database", default="state/paper_orders.sqlite3"
    )
    paper_reconcile_parser = subparsers.add_parser("paper-reconcile")
    paper_reconcile_parser.add_argument("--account", default="default")
    paper_reconcile_parser.add_argument(
        "--database", default="state/paper_orders.sqlite3"
    )
    daily_parser = subparsers.add_parser("daily-run")
    daily_parser.add_argument("--symbol", default="600036.SH")
    daily_parser.add_argument("--trade-date", required=True, help="YYYYMMDD")
    daily_parser.add_argument("--account", default="default")
    daily_parser.add_argument("--data-dir", default="data")
    daily_parser.add_argument("--database", default="state/paper_orders.sqlite3")
    daily_parser.add_argument("--signal-dir", default="state/signals")
    daily_parser.add_argument("--report-dir", default="state/daily-runs")
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    elif args.command == "sync-tushare":
        load_dotenv()
        provider = TushareProvider()
        store = ParquetStore(args.data_dir)
        if args.asset in {"stocks", "both"}:
            sync_reference_data(provider, store, args.start, args.end)
            def report_progress(done, total, trade_date, daily_rows, factor_rows):
                print(
                    f"progress={done}/{total} trade_date={trade_date} "
                    f"new_daily_rows={daily_rows} new_factor_rows={factor_rows}",
                    flush=True,
                )

            summary = sync_daily_history(
                provider,
                store,
                args.start,
                args.end,
                batch_days=args.batch_days,
                progress=report_progress,
            )
            print(
                f"asset=stocks open_days={summary.open_days} "
                f"daily_rows={summary.daily_rows} factor_rows={summary.factor_rows}"
            )
        if args.asset in {"etfs", "both"}:
            sync_etf_reference_data(provider, store)
            summary = sync_etf_history(provider, store, args.start, args.end)
            print(
                f"asset=etfs open_days={summary.open_days} "
                f"daily_rows={summary.daily_rows} factor_rows={summary.factor_rows}"
            )
    elif args.command == "backtest-stock":
        store = ParquetStore(args.data_dir)
        bars = load_adjusted_stock_bars(store, args.symbol)
        signals = moving_average_trend(
            bars,
            fast=args.fast,
            slow=args.slow,
            price_column="signal_close",
        )
        root = Path(__file__).resolve().parents[2]
        config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
        result = run_daily_backtest(signals, config)
        print(
            f"symbol={args.symbol} bars={len(bars)} fills={len(result.fills)} "
            f"total_return={result.total_return:.2%} "
            f"max_drawdown={result.max_drawdown:.2%}"
        )
    elif args.command == "research-stock":
        root = Path(__file__).resolve().parents[2]
        config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
        store = ParquetStore(args.data_dir)
        table = research_single_stock(store, args.symbol, config)
        display = _format_research_table(table)
        print("PRIMARY COMPARISON")
        print(display.to_string(index=False))
        robustness = robustness_single_stock(store, args.symbol, config)
        print("\nROBUSTNESS - OUT OF SAMPLE")
        print(_format_research_table(robustness).to_string(index=False))
        print("\nBENCHMARKS - OUT OF SAMPLE (PRICE INDEX)")
        print(_format_research_table(benchmark_metrics(store)).to_string(index=False))
        print("\nACCOUNT WEIGHT - OUT OF SAMPLE")
        print(
            _format_research_table(
                allocation_comparison(store, args.symbol, config)
            ).to_string(index=False)
        )
        print("\nANNUAL RETURNS")
        annual = annual_comparison(store, args.symbol, config)
        annual_display = annual.copy()
        for column in annual.columns:
            if column != "year":
                annual_display[column] = annual[column].map(
                    lambda value: f"{value:.2%}"
                )
        print(annual_display.to_string(index=False))
        ledger = trade_ledger(candidate_result(store, args.symbol, config, 0.8))
        print("\nCLOSED TRADES (WORST FIRST)")
        ledger_display = ledger.sort_values("net_return").copy()
        ledger_display["net_return"] = ledger_display["net_return"].map(
            lambda value: f"{value:.2%}"
        )
        print(ledger_display.to_string(index=False))
    elif args.command == "sync-research-reference":
        load_dotenv()
        counts = sync_research_reference(
            TushareProvider(),
            ParquetStore(args.data_dir),
            args.symbol,
            ("000300.SH", "399986.SZ"),
            args.start,
            args.end,
        )
        print(" ".join(f"{key}={value}" for key, value in counts.items()))
    elif args.command == "sync-fundamentals":
        load_dotenv()
        provider = TushareProvider()
        store = ParquetStore(args.data_dir)
        for symbol in args.symbol:
            counts = sync_stock_fundamentals(
                provider, store, symbol, args.start, args.end
            )
            print(symbol, " ".join(f"{key}={value}" for key, value in counts.items()))
    elif args.command == "paper-signal":
        bars = load_total_return_stock_bars(
            ParquetStore(args.data_dir), args.symbol
        )
        decision = trend_decision(bars, args.as_of)
        if decision.as_of_date != args.as_of:
            raise RuntimeError(
                f"requested as-of {args.as_of}, latest available bar is "
                f"{decision.as_of_date}; refusing stale signal"
            )
        broker = PaperBroker(args.database)
        if args.account_state:
            account = broker.portfolio(decision.close, args.symbol, args.account)
            portfolio = PortfolioSnapshot(
                cash=float(account["cash"]),
                equity=float(account["equity"]),
                position_quantity=int(account["position_quantity"]),
                reference_price=decision.close,
            )
        else:
            portfolio = PortfolioSnapshot(
                cash=args.cash,
                equity=args.equity,
                position_quantity=args.position,
                reference_price=decision.close,
            )
        risk = create_order_intent(decision, portfolio)
        artifact = write_signal_artifact(decision, args.signal_dir)
        decision_status = broker.record_decision(decision)
        status = (
            broker.submit(risk, args.account)
            if decision_status == "RECORDED"
            else "DUPLICATE_DECISION"
        )
        print(
            json.dumps(
                {
                    "mode": "PAPER_ONLY",
                    "decision": decision.to_dict(),
                    "risk": {
                        "accepted": risk.accepted,
                        "rejection_code": risk.rejection_code,
                        "intent": (
                            {
                                **risk.intent.__dict__,
                                "side": risk.intent.side.value,
                            }
                            if risk.intent
                            else None
                        ),
                    },
                    "signal_artifact": str(artifact),
                    "decision_status": decision_status,
                    "paper_status": status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "paper-init":
        broker = PaperBroker(args.database)
        print(
            json.dumps(
                {
                    "mode": "PAPER_ONLY",
                    "account": args.account,
                    "status": broker.initialize_account(args.cash, args.account),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "paper-execute":
        bars = load_total_return_stock_bars(
            ParquetStore(args.data_dir), args.symbol
        )
        dates = bars["date"].astype(str).str.replace("-", "", regex=False)
        selected = bars.loc[dates == args.trade_date]
        if len(selected) != 1:
            raise RuntimeError(
                f"expected exactly one bar for {args.symbol} on "
                f"{args.trade_date}, found {len(selected)}"
            )
        row = selected.iloc[0]
        bar = ExecutionBar(
            symbol=args.symbol,
            trade_date=args.trade_date,
            open=float(row["raw_open"]),
            close=float(row["raw_close"]),
            can_buy=bool(row.get("can_buy", True)),
            can_sell=bool(row.get("can_sell", True)),
        )
        root = Path(__file__).resolve().parents[2]
        config = BacktestConfig.from_yaml(root / "configs" / "default.yaml")
        broker = PaperBroker(args.database)
        outcomes = broker.execute_pending(bar, config, args.account)
        print(
            json.dumps(
                {
                    "mode": "PAPER_ONLY",
                    "bar": asdict(bar),
                    "outcomes": outcomes,
                    "portfolio": broker.portfolio(
                        bar.close, args.symbol, args.account
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "paper-reconcile":
        broker = PaperBroker(args.database)
        result = broker.reconcile(args.account)
        print(
            json.dumps(
                {"mode": "PAPER_ONLY", **asdict(result)},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "daily-run":
        load_dotenv()
        root = Path(__file__).resolve().parents[2]
        result = run_daily_pipeline(
            provider=TushareProvider(),
            store=ParquetStore(args.data_dir),
            broker=PaperBroker(args.database),
            config=BacktestConfig.from_yaml(root / "configs" / "default.yaml"),
            symbol=args.symbol,
            trade_date=args.trade_date,
            account_id=args.account,
            signal_dir=args.signal_dir,
            report_dir=args.report_dir,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


def _format_research_table(table: pd.DataFrame) -> pd.DataFrame:
    display = table.copy()
    for column in (
        "total_return",
        "cagr",
        "volatility",
        "max_drawdown",
        "average_exposure",
    ):
        if column in display:
            display[column] = display[column].map(lambda value: f"{value:.2%}")
    for column in ("sharpe", "calmar"):
        if column in display:
            display[column] = display[column].map(lambda value: f"{value:.2f}")
    return display


if __name__ == "__main__":
    main()
