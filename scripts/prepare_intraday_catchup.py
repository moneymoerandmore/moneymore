"""Recreate today's intraday comparison orders after a historical replay."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from moneymore.execution.paper import PaperBroker
from moneymore.intraday_execution import (
    prepare_exposure_intraday_branch_orders,
    prepare_intraday_branch_orders,
)


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", default=datetime.now(SHANGHAI).strftime("%Y%m%d"))
    args = parser.parse_args()
    broker = PaperBroker(ROOT / "state" / "paper_orders.sqlite3")
    print(json.dumps({
        "trade_date": args.trade_date,
        "baseline_orders": prepare_intraday_branch_orders(broker, args.trade_date),
        "controlled_orders": prepare_exposure_intraday_branch_orders(broker, args.trade_date),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
