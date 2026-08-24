from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.execution.paper import PaperBroker
from moneymore.multi_sector_daily import MULTI_SECTOR_ACCOUNT, run_multi_sector_daily
from moneymore.qlib_candidate_observer import (
    candidate_account_id,
    candidate_catalog,
    run_candidate_queue_daily,
)
from moneymore.qlib_challenger_daily import (
    QLIB_CHALLENGER_ACCOUNT,
    run_qlib_challenger_daily,
)
from moneymore.strategy_universe import active_strategy_universe, universe_summary

ROOT = Path(__file__).resolve().parents[1]


def _history_digest(store: ParquetStore, table: str, cutoff: str) -> str:
    frame = store.read(table)
    frame = frame.loc[frame["trade_date"].astype(str) < cutoff].copy()
    content = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--effective-date", required=True)
    args = parser.parse_args()
    trade_date = args.effective_date
    store = ParquetStore(ROOT / "data")
    broker = PaperBroker(ROOT / "state" / "paper_orders.sqlite3")
    config = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
    universe = active_strategy_universe(store, trade_date)
    summary = universe_summary(universe)
    histories = [
        "multi_sector_account_daily",
        "qlib_challenger_account_daily",
        "qlib_candidate_account_daily",
    ]
    before = {table: _history_digest(store, table, trade_date) for table in histories}
    accounts = [MULTI_SECTOR_ACCOUNT, QLIB_CHALLENGER_ACCOUNT]
    accounts.extend(candidate_account_id(str(row["candidate_tag"])) for row in candidate_catalog(ROOT))
    cancelled = {
        account: broker.cancel_pending(
            account, "STRATEGY_UNIVERSE_MIGRATION", signal_date=trade_date
        )
        for account in accounts
    }
    factor = run_multi_sector_daily(
        store, broker, config, trade_date,
        ROOT / "state" / "multi-sector-signals",
        ROOT / "state" / "multi-sector-shadow",
    )
    challenger = run_qlib_challenger_daily(
        root=ROOT, store=store, broker=broker, config=config, trade_date=trade_date,
        signal_dir=ROOT / "state" / "qlib-challenger-signals",
        report_dir=ROOT / "state" / "qlib-challenger-shadow",
    )
    candidates = run_candidate_queue_daily(
        root=ROOT, store=store, broker=broker, config=config, trade_date=trade_date
    )
    after = {table: _history_digest(store, table, trade_date) for table in histories}
    if before != after:
        raise RuntimeError("pre-migration account history changed")
    payload = {
        "status": "COMPLETED",
        "effective_date": trade_date,
        "strategy_universe": summary,
        "cancelled_pending_orders": cancelled,
        "factor": {"status": factor.status, "selected": list(factor.target_weights)},
        "challenger": {"status": challenger.status, "selected": challenger.selected},
        "candidates": [
            {
                "account_id": candidate_account_id(str(candidate["candidate_tag"])),
                "status": row.status,
                "selected": row.selected,
            }
            for candidate, row in zip(candidate_catalog(ROOT), candidates, strict=True)
        ],
        "history_before": before,
        "history_after": after,
        "historical_returns_preserved": before == after,
        "created_at": datetime.now(UTC).isoformat(),
    }
    target = ROOT / "state" / "universe-migrations" / f"{trade_date}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
