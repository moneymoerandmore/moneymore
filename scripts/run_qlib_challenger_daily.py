from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import PaperBroker
from moneymore.qlib_challenger_daily import run_qlib_challenger_daily

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the isolated Qlib challenger paper account.",
    )
    parser.add_argument("--trade-date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    result = run_qlib_challenger_daily(
        root=ROOT,
        store=ParquetStore(ROOT / "data"),
        broker=PaperBroker(ROOT / "state" / "paper_orders.sqlite3"),
        config=BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml"),
        trade_date=args.trade_date,
        signal_dir=ROOT / "state" / "qlib-challenger-signals",
        report_dir=ROOT / "state" / "qlib-challenger-shadow",
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
