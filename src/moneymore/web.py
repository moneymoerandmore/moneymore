from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .bank_daily import BANK_ACCOUNT, run_bank_daily_pipeline
from .config import BacktestConfig
from .data.fundamental_sync import sync_daily_basic_universe
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .data.tushare_provider import TushareProvider
from .execution.paper import PaperBroker
from .factors import build_default_registry
from .long_term_review import evaluate_long_term_review
from .model_registry import ModelRegistry
from .monthly_acceptance import evaluate_monthly_cycle
from .multi_sector_daily import (
    MULTI_SECTOR_ACCOUNT,
    run_multi_sector_daily,
)
from .point_in_time import materialize_point_in_time_store
from .qlib_challenger_daily import (
    QLIB_CHALLENGER_ACCOUNT,
    run_qlib_challenger_daily,
)
from .qlib_governance import (
    bootstrap_qlib_release,
    evaluate_qlib_drift,
)
from .research.adaptive import research_weighted_strategies
from .research.detail import (
    allocation_comparison,
    annual_comparison,
    candidate_result,
    trade_ledger,
)
from .research.governance import evaluate_bank_model_promotion
from .research.single_stock import research_single_stock, robustness_single_stock
from .signals import trend_decision
from .strategy_comparison import build_fair_comparison

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state"
DATA = ROOT / "data"
PAPER_DATABASE = STATE / "paper_orders.sqlite3"
SERVICE_DATABASE = STATE / "service.sqlite3"
SYMBOL = "600036.SH"
ACCOUNT = "default"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _composite_universe_symbols(store: ParquetStore) -> list[str]:
    sector_config = yaml.safe_load(
        (ROOT / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
    )
    symbols = {
        str(symbol)
        for universe in sector_config["universes"].values()
        for symbol in universe["holdings"]
    }
    membership = store.read(
        "universe_membership",
        columns=["date", "ts_code"],
        filters=[("universe", "==", "bank_cn")],
    )
    membership["date"] = membership["date"].astype(str)
    latest_date = membership["date"].max()
    symbols.update(
        membership.loc[membership["date"] == latest_date, "ts_code"].astype(str)
    )
    return sorted(symbols)


class TaskService:
    def __init__(self, database: Path = SERVICE_DATABASE) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    report_path TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL,
                    hour INTEGER NOT NULL,
                    minute INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO task_config(id, enabled, hour, minute, updated_at)
                VALUES (1, 1, 18, 30, ?)
                """,
                (datetime.now(SHANGHAI).isoformat(),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    target_weight REAL NOT NULL,
                    research_status TEXT NOT NULL,
                    added_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_step_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    trade_date TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT,
                    UNIQUE(run_id, step_name, attempt)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    trade_date TEXT,
                    run_id INTEGER,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    acknowledged_at TEXT
                )
                """
            )
            for candidate in (
                (
                    "600036.SH",
                    "招商银行",
                    "cmb_ma_120_250_v1",
                    1,
                    0.10,
                    "CANDIDATE",
                ),
                (
                    "600900.SH",
                    "长江电力",
                    "cypc_ma_120_250_v1",
                    1,
                    0.10,
                    "WATCH",
                ),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO candidates(
                        symbol, name, strategy_id, enabled, target_weight,
                        research_status, added_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*candidate, datetime.now(SHANGHAI).isoformat()),
                )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="moneymore-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def trigger(self, trade_date: str, source: str = "MANUAL") -> int:
        run_id = self._create_run("daily_pipeline", trade_date, source)
        threading.Thread(
            target=self._execute,
            args=(run_id, trade_date),
            name=f"daily-run-{run_id}",
            daemon=True,
        ).start()
        return run_id

    def trigger_recovery(self, as_of_date: str, source: str = "MANUAL_RECOVERY") -> int:
        run_id = self._create_run("recovery_pipeline", as_of_date, source)
        threading.Thread(
            target=self._execute_recovery,
            args=(run_id, as_of_date),
            name=f"recovery-run-{run_id}",
            daemon=True,
        ).start()
        return run_id

    def _create_run(self, task_name: str, trade_date: str, source: str) -> int:
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_runs(
                    task_name, trade_date, source, status, started_at
                ) VALUES (?, ?, ?, 'QUEUED', ?)
                """,
                (task_name, trade_date, source, datetime.now(SHANGHAI).isoformat()),
            )
            return int(cursor.lastrowid)

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM task_runs ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in rows]

    def steps(
        self, limit: int = 100, run_id: int | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM task_step_runs"
        params: list[object] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params)]

    def notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
            )
            result = [dict(row) for row in rows]
        for row in result:
            row["acknowledged"] = bool(row["acknowledged"])
        return result

    def acknowledge_notification(self, notification_id: int) -> dict[str, Any]:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE notifications
                SET acknowledged = 1, acknowledged_at = ?
                WHERE id = ?
                """,
                (datetime.now(SHANGHAI).isoformat(), notification_id),
            )
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        if row is None:
            raise KeyError(notification_id)
        result = dict(row)
        result["acknowledged"] = bool(result["acknowledged"])
        return result

    def preview(self, trade_date: str) -> dict[str, object]:
        step_names = [
            "bank_pipeline",
            "composite_daily_basic",
            "sector_research",
            "multi_sector_execution",
            "qlib_challenger_execution",
        ]
        with sqlite3.connect(self.database) as connection:
            completed = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT step_name FROM task_step_runs
                    WHERE trade_date = ? AND status = 'COMPLETED'
                    """,
                    (trade_date,),
                )
            }
            prior_runs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM task_runs WHERE trade_date = ?",
                    (trade_date,),
                ).fetchone()[0]
            )
        return {
            "trade_date": trade_date,
            "prior_run_count": prior_runs,
            "steps": [
                {
                    "step_name": name,
                    "action": "SKIP_COMPLETED" if name in completed else "RUN",
                }
                for name in step_names
            ],
            "mutates_state": False,
        }

    def _notify(
        self,
        severity: str,
        code: str,
        title: str,
        message: str,
        *,
        trade_date: str | None = None,
        run_id: int | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        key = dedupe_key or f"{trade_date}:{run_id}:{code}"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO notifications(
                    created_at, severity, code, title, message,
                    trade_date, run_id, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(SHANGHAI).isoformat(),
                    severity,
                    code,
                    title,
                    message,
                    trade_date,
                    run_id,
                    key,
                ),
            )

    def _run_step(
        self,
        run_id: int,
        trade_date: str,
        step_name: str,
        operation: Any,
        max_attempts: int = 2,
    ) -> tuple[str, Any]:
        with sqlite3.connect(self.database) as connection:
            completed = connection.execute(
                """
                SELECT 1
                FROM task_step_runs
                WHERE trade_date = ? AND step_name = ? AND status = 'COMPLETED'
                LIMIT 1
                """,
                (trade_date, step_name),
            ).fetchone()
            if completed:
                now = datetime.now(SHANGHAI).isoformat()
                connection.execute(
                    """
                    INSERT INTO task_step_runs(
                        run_id, trade_date, step_name, attempt, status,
                        started_at, finished_at
                    ) VALUES (?, ?, ?, 0, 'SKIPPED_COMPLETED', ?, ?)
                    """,
                    (run_id, trade_date, step_name, now, now),
                )
                return "SKIPPED_COMPLETED", None

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            started_at = datetime.now(SHANGHAI).isoformat()
            with sqlite3.connect(self.database) as connection:
                connection.execute(
                    """
                    INSERT INTO task_step_runs(
                        run_id, trade_date, step_name, attempt, status, started_at
                    ) VALUES (?, ?, ?, ?, 'RUNNING', ?)
                    """,
                    (run_id, trade_date, step_name, attempt, started_at),
                )
            try:
                result = operation()
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        """
                        UPDATE task_step_runs
                        SET status = 'COMPLETED', finished_at = ?
                        WHERE run_id = ? AND step_name = ? AND attempt = ?
                        """,
                        (
                            datetime.now(SHANGHAI).isoformat(),
                            run_id,
                            step_name,
                            attempt,
                        ),
                    )
                return "COMPLETED", result
            except Exception as error:  # noqa: BLE001 - retry boundary
                last_error = error
                detail = f"{type(error).__name__}: {error}"
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        """
                        UPDATE task_step_runs
                        SET status = 'FAILED', finished_at = ?, error = ?
                        WHERE run_id = ? AND step_name = ? AND attempt = ?
                        """,
                        (
                            datetime.now(SHANGHAI).isoformat(),
                            detail,
                            run_id,
                            step_name,
                            attempt,
                        ),
                    )
        assert last_error is not None
        self._notify(
            "ERROR",
            "PIPELINE_STEP_FAILED",
            f"流水线步骤失败：{step_name}",
            f"自动重试 {max_attempts} 次后仍失败：{type(last_error).__name__}: {last_error}",
            trade_date=trade_date,
            run_id=run_id,
            dedupe_key=f"{trade_date}:{run_id}:step:{step_name}",
        )
        raise last_error

    def config(self) -> dict[str, object]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT enabled, hour, minute, updated_at FROM task_config WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("task configuration is missing")
            result = dict(row)
            result["enabled"] = bool(result["enabled"])
            result["schedule"] = (
                f"每日 {int(result['hour']):02d}:{int(result['minute']):02d}"
            )
            result["timezone"] = "Asia/Shanghai"
            return result

    def update_config(self, enabled: bool, hour: int, minute: int) -> dict[str, object]:
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("invalid scheduler time")
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE task_config
                SET enabled = ?, hour = ?, minute = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    int(enabled),
                    hour,
                    minute,
                    datetime.now(SHANGHAI).isoformat(),
                ),
            )
        return self.config()

    def candidates(self, enabled_only: bool = False) -> list[dict[str, object]]:
        query = "SELECT * FROM candidates"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY added_at, symbol"
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(query)]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        return rows

    def add_candidate(self, symbol: str, name: str) -> dict[str, object]:
        normalized = symbol.strip().upper()
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", normalized):
            raise ValueError("symbol must look like 600900.SH")
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 30:
            raise ValueError("name must contain 1-30 characters")
        store = ParquetStore(DATA)
        if store.read(
            "daily",
            columns=["ts_code"],
            filters=[("ts_code", "==", normalized)],
        ).empty:
            raise ValueError(f"symbol is not available in local data: {normalized}")
        strategy_id = "stock_ma_120_250_v1"
        with sqlite3.connect(self.database) as connection:
            exists = connection.execute(
                "SELECT 1 FROM candidates WHERE symbol = ?", (normalized,)
            ).fetchone()
            if not exists:
                gross = float(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(target_weight), 0)
                        FROM candidates WHERE enabled = 1
                        """
                    ).fetchone()[0]
                )
                max_gross = BacktestConfig.from_yaml(
                    ROOT / "configs" / "default.yaml"
                ).max_gross_exposure
                if gross + 0.10 > max_gross + 1e-9:
                    raise ValueError("candidate pool would exceed gross exposure limit")
            connection.execute(
                """
                INSERT INTO candidates(
                    symbol, name, strategy_id, enabled, target_weight,
                    research_status, added_at
                ) VALUES (?, ?, ?, 1, 0.10, 'UNREVIEWED', ?)
                ON CONFLICT(symbol) DO UPDATE SET name = excluded.name
                """,
                (
                    normalized,
                    clean_name,
                    strategy_id,
                    datetime.now(SHANGHAI).isoformat(),
                ),
            )
        return next(row for row in self.candidates() if row["symbol"] == normalized)

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(20):
            now = datetime.now(SHANGHAI)
            config = self.config()
            if not config["enabled"]:
                continue
            if (now.hour, now.minute) < (int(config["hour"]), int(config["minute"])):
                continue
            trade_date = now.strftime("%Y%m%d")
            with sqlite3.connect(self.database) as connection:
                exists = connection.execute(
                    """
                    SELECT 1 FROM task_runs
                    WHERE task_name = 'daily_pipeline' AND trade_date = ?
                      AND source = 'SCHEDULED'
                    """,
                    (trade_date,),
                ).fetchone()
            if not exists:
                self.trigger_recovery(trade_date, "SCHEDULED")

    def _recovery_dates(
        self, store: ParquetStore, as_of_date: str
    ) -> list[str]:
        daily = store.read("daily", columns=["trade_date"])
        if daily.empty:
            raise RuntimeError("cannot recover without locally stored daily history")
        latest_data_date = str(daily["trade_date"].astype(str).max())
        calendar = store.read("trade_calendar", columns=["cal_date", "is_open"])
        calendar["cal_date"] = calendar["cal_date"].astype(str)
        dates = calendar.loc[
            (calendar["cal_date"] > latest_data_date)
            & (calendar["cal_date"] <= as_of_date)
            & (calendar["is_open"].astype(int) == 1),
            "cal_date",
        ].sort_values().tolist()
        # Data can be present while the service was offline before the
        # downstream strategy/execution stages ran.  The recovery watermark is
        # therefore the latest *completed workflow*, not only the latest bar.
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """
                SELECT MAX(trade_date) FROM task_runs
                WHERE task_name = 'daily_pipeline' AND status = 'COMPLETED'
                  AND trade_date <= ?
                """,
                (latest_data_date,),
            ).fetchone()
        latest_completed_date = str(row[0]) if row and row[0] else ""
        workflow_gap = calendar.loc[
            (calendar["cal_date"] > latest_completed_date)
            & (calendar["cal_date"] <= latest_data_date)
            & (calendar["is_open"].astype(int) == 1),
            "cal_date",
        ].tolist()
        dates = sorted(set(dates) | set(workflow_gap))
        # Intraday vendors do not guarantee a complete daily bar.  A manual
        # recovery before the configured end-of-day schedule must leave the
        # current session pending instead of creating an empty-data failure.
        now = datetime.now(SHANGHAI)
        config = self.config()
        if (
            as_of_date == now.strftime("%Y%m%d")
            and (now.hour, now.minute) < (int(config["hour"]), int(config["minute"]))
        ):
            dates = [item for item in dates if item != as_of_date]
        return dates

    def _execute_recovery(self, run_id: int, as_of_date: str) -> None:
        try:
            self._set_running(run_id)
            store = ParquetStore(DATA)
            dates = self._recovery_dates(store, as_of_date)
            if not dates:
                self._defer_empty_daily_failures(as_of_date)
                self._finish(
                    run_id,
                    "WAITING_MARKET_DATA",
                )
                self._notify(
                    "INFO",
                    "MARKET_DATA_PENDING",
                    "等待行情发布",
                    "当日尚无完整日线；已保留上一交易日的 T+1 待撮合委托，行情发布后将自动恢复执行。",
                    trade_date=as_of_date,
                    run_id=run_id,
                    dedupe_key=f"{as_of_date}:market-data-pending",
                )
                return
            completed: list[str] = []
            for trade_date in dates:
                child_run_id = self._create_run(
                    "daily_pipeline", trade_date, "RECOVERY"
                )
                self._execute(child_run_id, trade_date)
                child = next(row for row in self.runs(100) if row["id"] == child_run_id)
                if child["status"] != "COMPLETED":
                    raise RuntimeError(
                        f"recovery stopped at {trade_date}: {child['status']} {child.get('error') or ''}"
                    )
                completed.append(trade_date)
            self._finish(run_id, "COMPLETED", report_path=",".join(completed))
        except Exception as error:  # noqa: BLE001 - recovery must remain observable
            detail = f"{type(error).__name__}: {error}"
            self._finish(run_id, "FAILED", error=detail)
            self._notify(
                "ERROR",
                "RECOVERY_FAILED",
                "恢复流水线失败",
                detail,
                trade_date=as_of_date,
                run_id=run_id,
            )

    def _defer_empty_daily_failures(self, trade_date: str) -> None:
        """Resolve obsolete same-day empty-bar failures into a retryable wait state."""
        now = datetime.now(SHANGHAI).isoformat()
        marker = f"daily[{trade_date}] is empty"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE task_runs
                SET status = 'WAITING_MARKET_DATA', error = NULL, finished_at = ?
                WHERE task_name = 'daily_pipeline' AND trade_date = ?
                  AND status = 'FAILED' AND error LIKE ?
                """,
                (now, trade_date, f"%{marker}%"),
            )
            connection.execute(
                """
                UPDATE notifications
                SET acknowledged = 1, acknowledged_at = ?
                WHERE trade_date = ? AND acknowledged = 0
                  AND code IN ('PIPELINE_FAILED', 'PIPELINE_STEP_FAILED')
                  AND message LIKE ?
                """,
                (now, trade_date, f"%{marker}%"),
            )

    def _execute(self, run_id: int, trade_date: str) -> None:
        if not self._run_lock.acquire(blocking=False):
            self._finish(run_id, "SKIPPED_BUSY", error="another pipeline is running")
            return
        try:
            self._set_running(run_id)
            load_dotenv(ROOT / ".env")
            provider = TushareProvider()
            store = ParquetStore(DATA)
            broker = PaperBroker(PAPER_DATABASE)
            config = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
            _, result = self._run_step(
                run_id,
                trade_date,
                "bank_pipeline",
                lambda: run_bank_daily_pipeline(
                    provider=provider,
                    store=store,
                    broker=broker,
                    config=config,
                    trade_date=trade_date,
                    signal_dir=STATE / "bank-signals",
                    report_dir=STATE / "bank-shadow",
                ),
            )
            if result is not None and result.status == "SKIPPED_MARKET_CLOSED":
                self._finish(run_id, "COMPLETED", report_path=result.report_path)
                return
            if result is not None and result.status != "COMPLETED":
                raise RuntimeError(f"bank pipeline status: {result.status}")

            self._run_step(
                run_id,
                trade_date,
                "composite_daily_basic",
                lambda: sync_daily_basic_universe(
                    provider,
                    store,
                    _composite_universe_symbols(store),
                    trade_date,
                ),
            )
            self._run_step(
                run_id,
                trade_date,
                "point_in_time_refresh",
                lambda: materialize_point_in_time_store(
                    ROOT,
                    captured_date=trade_date,
                    feature_start=pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                ),
            )
            self._run_step(
                run_id,
                trade_date,
                "sector_research",
                lambda: subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "research_sector_portfolio.py"),
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            )
            def execute_multi_sector() -> Any:
                value = run_multi_sector_daily(
                    store=store,
                    broker=broker,
                    config=config,
                    trade_date=trade_date,
                    signal_dir=STATE / "multi-sector-signals",
                    report_dir=STATE / "multi-sector-shadow",
                )
                if value.status != "COMPLETED":
                    code = {
                        "BLOCKED_DATA_QUALITY": "DATA_QUALITY_BLOCKED",
                        "SUSPENDED_RISK": "RISK_SUSPENDED",
                        "FAILED_RECONCILIATION": "RECONCILIATION_FAILED",
                    }.get(value.status, "PIPELINE_STATUS_BLOCKED")
                    self._notify(
                        "ERROR",
                        code,
                        "综合组合流水线未完成",
                        f"状态：{value.status}",
                        trade_date=trade_date,
                        run_id=run_id,
                    )
                    raise RuntimeError(
                        f"multi-sector pipeline status: {value.status}"
                    )
                return value

            _, multi_result = self._run_step(
                run_id,
                trade_date,
                "multi_sector_execution",
                execute_multi_sector,
            )
            deferred = (
                []
                if multi_result is None
                else [
                    row
                    for row in multi_result.executions
                    if str(row.get("status", "")).upper()
                    in {"DEFERRED", "REJECTED", "BLOCKED"}
                ]
            )
            if deferred:
                self._notify(
                    "WARN",
                    "ORDER_DEFERRED",
                    "存在延迟或受阻订单",
                    f"本次流水线共有 {len(deferred)} 条执行未即时完成，请在撮合明细复核。",
                    trade_date=trade_date,
                    run_id=run_id,
                )
            try:
                self._run_step(
                    run_id,
                    trade_date,
                    "qlib_challenger_execution",
                    lambda: run_qlib_challenger_daily(
                        root=ROOT,
                        store=store,
                        broker=broker,
                        config=config,
                        trade_date=trade_date,
                        signal_dir=STATE / "qlib-challenger-signals",
                        report_dir=STATE / "qlib-challenger-shadow",
                    ),
                )
                self._run_step(
                    run_id,
                    trade_date,
                    "qlib_drift_monitoring",
                    lambda: evaluate_qlib_drift(
                        ROOT,
                        store,
                        broker,
                        trade_date,
                    ),
                )
                self._run_step(
                    run_id,
                    trade_date,
                    "qlib_long_term_review",
                    lambda: evaluate_long_term_review(
                        ROOT,
                        store,
                        broker,
                        trade_date,
                    ),
                )
            except Exception as challenger_error:  # noqa: BLE001
                self._notify(
                    "WARN",
                    "QLIB_CHALLENGER_FAILED",
                    "Qlib挑战账户运行失败",
                    (
                        f"{type(challenger_error).__name__}: "
                        f"{challenger_error}; 原因子账户不受影响"
                    ),
                    trade_date=trade_date,
                    run_id=run_id,
                )
            prior_failure = any(
                row["id"] != run_id
                and row["trade_date"] == trade_date
                and row["status"] == "FAILED"
                for row in self.runs(100)
            )
            if prior_failure:
                self._notify(
                    "INFO",
                    "PIPELINE_RECOVERED",
                    "流水线恢复成功",
                    "失败任务已从完成步骤后续跑通，无需重复执行已完成步骤。",
                    trade_date=trade_date,
                    run_id=run_id,
                )
            report_path = None if result is None else result.report_path
            self._finish(run_id, "COMPLETED", report_path=report_path)
        except Exception as error:  # noqa: BLE001 - task failures must stay observable
            detail = f"{type(error).__name__}: {error}"
            self._finish(run_id, "FAILED", error=detail)
            self._notify(
                "ERROR",
                "PIPELINE_FAILED",
                "每日流水线失败",
                detail,
                trade_date=trade_date,
                run_id=run_id,
            )
        finally:
            self._run_lock.release()

    def _set_running(self, run_id: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE task_runs SET status = 'RUNNING' WHERE id = ?", (run_id,)
            )

    def _finish(
        self,
        run_id: int,
        status: str,
        report_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE task_runs
                SET status = ?, finished_at = ?, report_path = ?, error = ?
                WHERE id = ?
                """,
                (
                    status,
                    datetime.now(SHANGHAI).isoformat(),
                    report_path,
                    error,
                    run_id,
                ),
            )


task_service = TaskService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    task_service.start()
    yield
    task_service.stop()


app = FastAPI(title="MoneyMore API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "PAPER_ONLY",
        "scheduler": bool(task_service._thread and task_service._thread.is_alive()),
        "server_time": datetime.now(SHANGHAI).isoformat(),
    }


@app.get("/api/factors")
def factors() -> dict[str, object]:
    registry = build_default_registry()
    catalog = registry.catalog()
    return {
        "count": len(catalog),
        "categories": sorted({str(item["category"]) for item in catalog}),
        "items": catalog,
    }


@app.get("/api/factor-research")
def factor_research(universe: str = "bank_cn") -> dict[str, object]:
    if not re.fullmatch(r"[a-z0-9_]{1,40}", universe):
        raise HTTPException(status_code=400, detail="invalid universe")
    store = ParquetStore(DATA)
    try:
        membership = store.read(
            "universe_membership", filters=[("universe", "==", universe)]
        )
        ic = store.read("factor_ic_report")
        period_ic = store.read("factor_ic_period_report")
        quantiles = store.read("factor_quantile_report")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    latest_date = membership["date"].max()
    return {
        "universe": universe,
        "membership_rows": len(membership),
        "latest_date": str(latest_date)[:10],
        "latest_members": int((membership["date"] == latest_date).sum()),
        "ic": _records(ic),
        "period_ic": _records(period_ic),
        "quantiles": _records(quantiles),
    }


@app.get("/api/bank-model")
def bank_model() -> dict[str, object]:
    store = ParquetStore(DATA)
    try:
        scores = store.read("bank_model_scores")
        targets = store.read("bank_model_targets")
        report = store.read("bank_model_report")
        robustness = store.read("bank_model_robustness")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    latest_date = scores["date"].max()
    latest_scores = scores.loc[scores["date"] == latest_date].sort_values(
        "score", ascending=False
    )
    latest_targets = targets.loc[
        (targets["date"] == targets["date"].max()) & targets["selected"]
    ].sort_values("rank")
    return {
        "model_id": "bank_multifactor_v1",
        "status": "RESEARCH_ONLY",
        "warning": (
            "The 2022+ period has been inspected during model development and is "
            "validation evidence, not a pristine untouched test set."
        ),
        "latest_date": str(latest_date)[:10],
        "report": _records(report),
        "robustness": _records(robustness),
        "promotion": evaluate_bank_model_promotion(
            report, robustness, has_pristine_forward_period=False
        ),
        "latest_scores": _records(
            latest_scores[
                [
                    "symbol", "score", "value_score", "defensive_score",
                    "momentum_score", "quality_score",
                ]
            ].head(20)
        ),
        "latest_holdings": _records(
            latest_targets[["symbol", "rank", "target"]]
        ),
    }


@app.get("/api/bank-dashboard")
def bank_dashboard() -> dict[str, object]:
    model = bank_model()
    store = ParquetStore(DATA)
    equity = store.read("bank_model_equity").copy()
    equity["date"] = equity["date"].astype(str).str[:10]
    sampled = equity.iloc[::20]
    shadow = _latest_bank_shadow()
    return {
        **model,
        "mode": "PAPER_ONLY",
        "shadow": shadow,
        "timing": bank_timing(),
        "scheduler": task_service.config(),
        "recent_runs": task_service.runs(10),
        "equity_curve": _records(sampled[["date", "equity", "drawdown"]]),
        "symbol_names": _instrument_names(store),
    }


@app.get("/api/bank-timing")
def bank_timing() -> dict[str, object]:
    store = ParquetStore(DATA)
    try:
        report = store.read("bank_timing_report")
        degrees = store.read("bank_timing_degrees")
        equity = store.read("bank_timing_equity")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    latest = (
        degrees.sort_values("date")
        .groupby("strategy", as_index=False)
        .tail(1)
        .sort_values("strategy")
    )
    active = "vol_target_12"
    current = latest.loc[latest["strategy"] == active].iloc[0]
    active_equity = equity.loc[equity["strategy"] == active].copy()
    active_equity["date"] = active_equity["date"].astype(str).str[:10]
    return {
        "interface": "qlib_risk_degree",
        "active_strategy": active,
        "status": "PROSPECTIVE_CANDIDATE",
        "risk_degree": float(current["risk_degree"]),
        "base_gross_exposure": 0.8,
        "bank_budget_fraction": float(current["risk_degree"]) / 0.8,
        "latest_date": str(current["date"])[:10],
        "report": _records(report),
        "current": _records(latest),
        "equity_curve": _records(
            active_equity.iloc[::20][["date", "equity", "drawdown"]]
        ),
        "decision": (
            "FULL_BANK_BUDGET"
            if float(current["risk_degree"]) >= 0.8
            else "REDUCED_BANK_BUDGET"
        ),
        "evidence_note": (
            "The timing candidates were selected using already inspected history. "
            "Only observations after 2026-07-27 are pristine forward evidence."
        ),
    }


@app.get("/api/sector-portfolio")
def sector_portfolio() -> dict[str, object]:
    store = ParquetStore(DATA)
    try:
        recommendation = store.read("sector_portfolio_recommendation")
        report = store.read("sector_model_report")
        scores = store.read("sector_model_scores")
        targets = store.read("sector_model_targets")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    config = yaml.safe_load(
        (ROOT / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
    )
    latest_date = scores["date"].max()
    latest_scores = scores.loc[scores["date"] == latest_date].copy()
    latest_targets = targets.loc[targets["date"] == targets["date"].max()].copy()
    ranked = latest_scores.merge(
        latest_targets[["sector", "symbol", "rank", "selected", "target"]],
        on=["sector", "symbol"],
        how="left",
    ).sort_values(["sector", "rank", "score"], ascending=[True, True, False])

    universes = []
    for sector, definition in config["universes"].items():
        universes.append(
            {
                "sector": sector,
                "name": definition["name"],
                "fund_code": definition["etf_code"],
                "style": definition["style"],
                "factor_weights": definition["factors"],
                "constituents": definition["holdings"],
                "ranking": _records(ranked.loc[ranked["sector"] == sector]),
            }
        )
    return {
        "status": "PAPER_RESEARCH_ONLY",
        "latest_date": str(latest_date)[:10],
        "disclosure_date": str(config["disclosure_date"]),
        "evidence_status": config["evidence_status"],
        "warning": (
            "Tushare 当前权限不含历史 ETF 持仓。行业池来自 2026-06-30 "
            "披露快照，历史结果存在当前成分回看偏差，不属于无偏回测。"
        ),
        "allocation_method": config["allocation"]["method"],
        "allocation": _records(recommendation.sort_values("budget_weight", ascending=False)),
        "report": _records(report),
        "universes": universes,
        "symbol_names": _instrument_names(store),
    }


@app.get("/api/bank-execution")
def bank_execution() -> dict[str, object]:
    broker = PaperBroker(PAPER_DATABASE)
    broker.initialize_account(1_000_000, BANK_ACCOUNT)
    with sqlite3.connect(PAPER_DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        fills = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM fills WHERE account_id = ? ORDER BY id DESC LIMIT 100",
                (BANK_ACCOUNT,),
            )
        ]
        positions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM positions WHERE account_id = ? AND quantity != 0
                ORDER BY symbol
                """,
                (BANK_ACCOUNT,),
            )
        ]
    orders = [
        row for row in reversed(broker.orders())
        if row.get("account_id") == BANK_ACCOUNT
    ][:100]
    latest = _latest_bank_shadow()
    return {
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "portfolio": latest.get("portfolio"),
        "reconciliation": asdict(broker.reconcile(BANK_ACCOUNT)),
    }


@app.get("/api/multi-sector-execution")
def multi_sector_execution() -> dict[str, object]:
    broker = PaperBroker(PAPER_DATABASE)
    broker.initialize_account(1_000_000, MULTI_SECTOR_ACCOUNT)
    store = ParquetStore(DATA)
    with sqlite3.connect(PAPER_DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        fills = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM fills WHERE account_id = ? ORDER BY id DESC LIMIT 100",
                (MULTI_SECTOR_ACCOUNT,),
            )
        ]
        positions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM positions WHERE account_id = ? AND quantity != 0
                ORDER BY symbol
                """,
                (MULTI_SECTOR_ACCOUNT,),
            )
        ]
    orders = [
        row
        for row in reversed(broker.orders())
        if row.get("account_id") == MULTI_SECTOR_ACCOUNT
    ][:100]
    latest = _latest_multi_sector_shadow()
    position_symbols = [str(row["symbol"]) for row in positions]
    live_marks: dict[str, float] = {}
    if position_symbols:
        daily = store.read(
            "daily",
            columns=["ts_code", "trade_date", "close"],
            filters=[("ts_code", "in", position_symbols)],
        )
        latest_daily = (
            daily.sort_values("trade_date").groupby("ts_code", as_index=False).tail(1)
        )
        live_marks = {
            str(row["ts_code"]): float(row["close"])
            for row in latest_daily.to_dict("records")
        }
    live_portfolio = broker.account_snapshot(live_marks, MULTI_SECTOR_ACCOUNT)
    catch_up_dir = STATE / "catch-up"
    catch_up_reports = (
        sorted(catch_up_dir.glob("*.json")) if catch_up_dir.exists() else []
    )
    latest_catch_up = (
        json.loads(catch_up_reports[-1].read_text(encoding="utf-8"))
        if catch_up_reports
        else None
    )
    analytics: dict[str, list[dict[str, object]]] = {}
    for api_key, table in {
        "history": "multi_sector_account_daily",
        "deviations": "multi_sector_deviation_daily",
        "risk_alerts": "multi_sector_risk_alerts",
    }.items():
        try:
            frame = store.read(table)
            if api_key != "history" and not frame.empty:
                frame = frame.loc[frame["trade_date"] == frame["trade_date"].max()]
            analytics[api_key] = _records(frame)
        except FileNotFoundError:
            analytics[api_key] = []
    return {
        "account_id": MULTI_SECTOR_ACCOUNT,
        "status": (
            "COMPLETED_CATCH_UP"
            if latest_catch_up
            and latest_catch_up.get("trade_date") == latest.get("trade_date")
            else latest.get("status", "AWAITING_FIRST_RUN")
        ),
        "trade_date": latest.get("trade_date"),
        "target_weights": latest.get("target_weights", {}),
        "symbol_sectors": latest.get("symbol_sectors", {}),
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "portfolio": live_portfolio,
        "latest_catch_up": latest_catch_up,
        "attribution": latest.get("attribution", []),
        "return_attribution": latest.get("return_attribution", []),
        "risk_attribution": latest.get("risk_attribution", []),
        "execution_attribution": latest.get("execution_attribution", []),
        "attribution_reconciliation": latest.get(
            "attribution_reconciliation", {}
        ),
        "corporate_actions_today": latest.get(
            "corporate_actions", {"registered": [], "settled": []}
        ),
        "corporate_action_ledger": broker.corporate_actions(MULTI_SECTOR_ACCOUNT),
        "data_health": latest.get("data_health", {}),
        "risk_state": broker.risk_state(MULTI_SECTOR_ACCOUNT),
        "risk_transitions": broker.risk_transitions(MULTI_SECTOR_ACCOUNT)[:100],
        "model_version": latest.get("model_version", {}),
        "metrics": latest.get("metrics", {}),
        **analytics,
        "reconciliation": asdict(broker.reconcile(MULTI_SECTOR_ACCOUNT)),
        "symbol_names": _instrument_names(store),
    }


@app.post("/api/risk-state/recover")
def recover_risk_state() -> dict[str, object]:
    broker = PaperBroker(PAPER_DATABASE)
    broker.initialize_account(1_000_000, MULTI_SECTOR_ACCOUNT)
    return broker.approve_risk_recovery(MULTI_SECTOR_ACCOUNT)


@app.get("/api/data-quality")
def data_quality() -> dict[str, object]:
    store = ParquetStore(DATA)
    try:
        history = store.read("data_health_daily").sort_values("trade_date")
        checks = store.read("data_health_checks")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    latest_date = str(history.iloc[-1]["trade_date"])
    latest_checks = checks.loc[checks["trade_date"].astype(str) == latest_date]
    return {
        "latest": _records(history.tail(1))[0],
        "checks": _records(latest_checks.sort_values(["status", "category", "code"])),
        "history": _records(history.tail(90)),
        "symbol_names": _instrument_names(store),
    }


@app.get("/api/model-registry")
def model_registry() -> dict[str, list[dict[str, Any]]]:
    registry = ModelRegistry(
        STATE / "model_registry.sqlite3",
        STATE / "model-versions",
    )
    return registry.snapshot()


@app.get("/api/qlib-challenger")
def qlib_challenger() -> dict[str, object]:
    broker = PaperBroker(PAPER_DATABASE)
    broker.initialize_account(1_000_000, QLIB_CHALLENGER_ACCOUNT)
    research_path = STATE / "qlib-challenger" / "latest-research.json"
    forward_path = STATE / "qlib-challenger" / "forward-evaluation.json"
    historical_path = STATE / "historical-pk" / "latest.json"
    reports_dir = STATE / "qlib-challenger-shadow"
    reports = sorted(reports_dir.glob("*.json")) if reports_dir.exists() else []
    latest = (
        max(
            (
                json.loads(path.read_text(encoding="utf-8"))
                for path in reports
            ),
            key=lambda report: str(report.get("created_at", "")),
        )
        if reports
        else {
            "status": "AWAITING_TRAINING",
            "selected": [],
            "scores": [],
            "portfolio": {
                "cash": 1_000_000,
                "market_value": 0,
                "equity": 1_000_000,
                "positions": [],
            },
        }
    )
    research = (
        json.loads(research_path.read_text(encoding="utf-8"))
        if research_path.exists()
        else {
            "status": "AWAITING_TRAINING",
            "metrics": [],
            "cuda_available": False,
        }
    )
    forward_evaluation = (
        json.loads(forward_path.read_text(encoding="utf-8"))
        if forward_path.exists()
        else {"matured_days": 0, "rank_ic": None, "rank_ic_ir": None, "daily": []}
    )
    historical_execution = (
        json.loads(historical_path.read_text(encoding="utf-8"))
        if historical_path.exists()
        else {
            "status": "AWAITING_REPLAY",
            "evidence_status": "NO_EXECUTION_EVIDENCE",
            "strategies": [],
        }
    )
    point_in_time_path = STATE / "point-in-time" / "latest.json"
    point_in_time = (
        json.loads(point_in_time_path.read_text(encoding="utf-8"))
        if point_in_time_path.exists()
        else {"status": "AWAITING_MATERIALIZATION"}
    )
    governance = bootstrap_qlib_release(ROOT)
    drift_path = STATE / "qlib-governance" / "latest-drift.json"
    drift = (
        json.loads(drift_path.read_text(encoding="utf-8"))
        if drift_path.exists()
        else {"status": "AWAITING_OBSERVATIONS"}
    )
    review_path = STATE / "qlib-governance" / "long-term-review.json"
    long_term_review = (
        json.loads(review_path.read_text(encoding="utf-8"))
        if review_path.exists()
        else {"status": "COLLECTING_EVIDENCE", "criteria": {}, "evaluation": {}}
    )
    challenger_config = yaml.safe_load(
        (ROOT / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
    )
    gate_config = challenger_config["research_gate"]
    model_metrics = next(
        (
            row
            for row in research.get("metrics", [])
            if row.get("model_id") == challenger_config["model_id"]
        ),
        None,
    )
    research["gate"] = {
        **gate_config,
        "passed": bool(
            model_metrics
            and int(model_metrics["samples"]) >= int(gate_config["minimum_samples"])
            and float(model_metrics["rank_ic"])
            >= float(gate_config["minimum_rank_ic"])
            and float(model_metrics["rank_ic_ir"])
            >= float(gate_config["minimum_rank_ic_ir"])
            and float(
                model_metrics.get("cost_adjusted_top_k_excess_return", -1)
            )
            > float(gate_config["minimum_cost_adjusted_excess_return"])
            and int(research.get("stability", {}).get("seed_count", 0))
            >= int(gate_config["minimum_seed_count"])
            and float(
                research.get("stability", {}).get("positive_seed_ratio", 0)
            )
            >= float(gate_config["minimum_positive_seed_ratio"])
        ),
    }
    promotion_gate = challenger_config["promotion_gate"]
    research["promotion_gate"] = {
        **promotion_gate,
        "passed": bool(
            model_metrics
            and float(model_metrics["rank_ic"])
            >= float(promotion_gate["minimum_rank_ic"])
            and float(model_metrics["rank_ic_ir"])
            >= float(promotion_gate["minimum_rank_ic_ir"])
            and int(forward_evaluation.get("matured_days", 0))
            >= int(promotion_gate["minimum_forward_days"])
            and float(forward_evaluation.get("rank_ic") or -1)
            > float(promotion_gate["minimum_forward_rank_ic"])
        ),
    }
    orders = [
        row
        for row in reversed(broker.orders())
        if row.get("account_id") == QLIB_CHALLENGER_ACCOUNT
    ][:100]
    comparison_sources: dict[str, pd.DataFrame] = {}
    for account_id, table in (
        (MULTI_SECTOR_ACCOUNT, "multi_sector_account_daily"),
        (QLIB_CHALLENGER_ACCOUNT, "qlib_challenger_account_daily"),
    ):
        try:
            history = ParquetStore(DATA).read(table).sort_values("trade_date")
        except FileNotFoundError:
            history = pd.DataFrame()
        comparison_sources[account_id] = history
    fair_comparison = build_fair_comparison(
        comparison_sources,
        minimum_observation_days=20,
        target_volatility=0.10,
    )
    return {
        "account_id": QLIB_CHALLENGER_ACCOUNT,
        "research": research,
        "forward": forward_evaluation,
        "latest": latest,
        "orders": orders,
        "fills": broker.fills(QLIB_CHALLENGER_ACCOUNT),
        "reconciliation": asdict(broker.reconcile(QLIB_CHALLENGER_ACCOUNT)),
        "comparison": fair_comparison,
        "historical_execution": historical_execution,
        "point_in_time": point_in_time,
        "governance": governance,
        "drift": drift,
        "long_term_review": long_term_review,
        "baseline": {
            "account_id": MULTI_SECTOR_ACCOUNT,
            "latest": _latest_multi_sector_shadow(),
        },
    }


def _account_performance(account_id: str, history: pd.DataFrame) -> dict[str, object]:
    if history.empty or "equity" not in history:
        return {
            "account_id": account_id,
            "observation_days": 0,
            "total_return": None,
            "annualized_volatility": None,
            "sharpe": None,
            "max_drawdown": None,
        }
    equity = pd.to_numeric(history["equity"], errors="coerce").dropna()
    returns = equity.pct_change().dropna()
    drawdown = equity / equity.cummax() - 1
    volatility = float(returns.std(ddof=1) * (252**0.5)) if len(returns) > 1 else 0.0
    return {
        "account_id": account_id,
        "observation_days": len(equity),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1),
        "annualized_volatility": volatility,
        "sharpe": (
            float(returns.mean() / returns.std(ddof=1) * (252**0.5))
            if len(returns) > 1 and returns.std(ddof=1)
            else None
        ),
        "max_drawdown": float(drawdown.min()),
    }


@app.get("/api/monthly-acceptance")
def monthly_acceptance() -> dict[str, object]:
    store = ParquetStore(DATA)
    try:
        account_daily = store.read("multi_sector_account_daily")
        records = _records(account_daily)
        observation_date = str(account_daily["trade_date"].astype(str).max())
    except FileNotFoundError:
        records = []
        observation_date = datetime.now(SHANGHAI).strftime("%Y%m%d")
    broker = PaperBroker(PAPER_DATABASE)
    registry = ModelRegistry(
        STATE / "model_registry.sqlite3",
        STATE / "model-versions",
    )
    result = evaluate_monthly_cycle(
        account_daily=records,
        fills=broker.fills(MULTI_SECTOR_ACCOUNT),
        model_versions=registry.snapshot(100)["versions"],
        start_date="20260727",
        observation_date=observation_date,
    )
    payload = asdict(result)
    report = STATE / "monthly-acceptance" / f"{result.cycle_id}.json"
    payload["report_path"] = str(report) if report.exists() else None
    return payload


def _latest_bank_shadow() -> dict[str, object]:
    directory = STATE / "bank-shadow"
    reports = sorted(directory.glob("*.json")) if directory.exists() else []
    if not reports:
        return {
            "status": "AWAITING_FIRST_RUN",
            "holdings": [],
            "timing": {},
            "ranking": [],
            "orders": [],
            "executions": [],
            "portfolio": {
                "cash": 1_000_000,
                "market_value": 0,
                "equity": 1_000_000,
                "positions": [],
            },
        }
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def _latest_multi_sector_shadow() -> dict[str, object]:
    directory = STATE / "multi-sector-shadow"
    reports = sorted(directory.glob("*.json")) if directory.exists() else []
    if not reports:
        return {
            "status": "AWAITING_FIRST_RUN",
            "target_weights": {},
            "symbol_sectors": {},
            "attribution": [],
            "portfolio": {
                "cash": 1_000_000,
                "market_value": 0,
                "equity": 1_000_000,
                "positions": [],
            },
        }
    return json.loads(reports[-1].read_text(encoding="utf-8"))


@app.get("/api/overview")
def overview(symbol: str = SYMBOL) -> dict[str, object]:
    candidates = task_service.candidates()
    candidate = next(
        (item for item in candidates if item["symbol"] == symbol),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    broker = PaperBroker(PAPER_DATABASE)
    store = ParquetStore(DATA)
    bars = load_total_return_stock_bars(store, symbol)
    latest = bars.iloc[-1]
    latest_date = str(latest["date"]).replace("-", "")[:8]
    decision = trend_decision(
        bars,
        latest_date,
        active_weight=float(candidate["target_weight"]),
        strategy_id=str(candidate["strategy_id"]),
    )
    portfolio = broker.portfolio(decision.close, symbol, ACCOUNT)
    reconciliation = asdict(broker.reconcile(ACCOUNT))
    return {
        "mode": "PAPER_ONLY",
        "symbol": symbol,
        "symbol_name": candidate["name"],
        "latest_data_date": decision.as_of_date,
        "latest_price": decision.close,
        "decision": decision.to_dict(),
        "portfolio": portfolio,
        "reconciliation": reconciliation,
        "recent_runs": task_service.runs(6),
        "scheduler": task_service.config(),
    }


@app.get("/api/candidates")
def candidates() -> dict[str, object]:
    store = ParquetStore(DATA)
    broker = PaperBroker(PAPER_DATABASE)
    rows = []
    for candidate in task_service.candidates():
        symbol = str(candidate["symbol"])
        bars = load_total_return_stock_bars(store, symbol)
        latest_date = str(bars.iloc[-1]["date"]).replace("-", "")[:8]
        decision = trend_decision(
            bars,
            latest_date,
            active_weight=float(candidate["target_weight"]),
            strategy_id=str(candidate["strategy_id"]),
        )
        position = broker.portfolio(decision.close, symbol, ACCOUNT)
        rows.append(
            {
                **candidate,
                "latest_date": decision.as_of_date,
                "latest_price": decision.close,
                "action": decision.action,
                "reason_code": decision.reason_code,
                "signal_weight": decision.target_weight,
                "position_quantity": position["position_quantity"],
                "market_value": int(position["position_quantity"]) * decision.close,
            }
        )
    return {
        "items": rows,
        "active_count": sum(bool(row["enabled"]) for row in rows),
        "target_gross_weight": sum(
            float(row["target_weight"]) for row in rows if row["enabled"]
        ),
        "max_gross_weight": BacktestConfig.from_yaml(
            ROOT / "configs" / "default.yaml"
        ).max_gross_exposure,
    }


@app.post("/api/candidates")
def add_candidate(payload: dict[str, str]) -> dict[str, object]:
    try:
        return task_service.add_candidate(payload["symbol"], payload["name"])
    except (KeyError, ValueError, sqlite3.IntegrityError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/orders")
def orders() -> list[dict[str, object]]:
    database = PaperBroker(PAPER_DATABASE)
    return list(reversed(database.orders()))[:50]


@app.get("/api/fills")
def fills() -> list[dict[str, object]]:
    if not PAPER_DATABASE.exists():
        return []
    with sqlite3.connect(PAPER_DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM fills ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/execution")
def execution() -> dict[str, object]:
    broker = PaperBroker(PAPER_DATABASE)
    with sqlite3.connect(PAPER_DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        fills_rows = connection.execute(
            "SELECT * FROM fills ORDER BY id DESC LIMIT 100"
        ).fetchall()
        attempt_rows = connection.execute(
            "SELECT * FROM execution_attempts ORDER BY id DESC LIMIT 100"
        ).fetchall()
        position_rows = connection.execute(
            "SELECT * FROM positions ORDER BY symbol"
        ).fetchall()
    return {
        "orders": list(reversed(broker.orders()))[:100],
        "fills": [dict(row) for row in fills_rows],
        "attempts": [dict(row) for row in attempt_rows],
        "positions": [dict(row) for row in position_rows],
        "reconciliation": asdict(broker.reconcile(ACCOUNT)),
    }


@app.get("/api/research")
def research(symbol: str = SYMBOL) -> dict[str, object]:
    candidate = next(
        (item for item in task_service.candidates() if item["symbol"] == symbol),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return _research_payload(
        symbol,
        str(candidate["name"]),
        str(candidate["research_status"]),
    )


@app.get("/api/tasks")
def tasks() -> list[dict[str, Any]]:
    return task_service.runs()


@app.get("/api/task-operations")
def task_operations() -> dict[str, object]:
    return {
        "runs": task_service.runs(30),
        "steps": task_service.steps(100),
        "notifications": task_service.notifications(100),
    }


@app.get("/api/operations-center")
def operations_center() -> dict[str, object]:
    now = datetime.now(SHANGHAI)
    config = task_service.config()
    scheduled = now.replace(
        hour=int(config["hour"]),
        minute=int(config["minute"]),
        second=0,
        microsecond=0,
    )
    if scheduled <= now:
        scheduled += timedelta(days=1)
    broker = PaperBroker(PAPER_DATABASE)
    pending_orders = [
        row
        for row in reversed(broker.orders())
        if row.get("account_id") == MULTI_SECTOR_ACCOUNT
        and row.get("status") == "PENDING"
    ]
    with sqlite3.connect(PAPER_DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        attempts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT a.*, o.account_id, o.symbol, o.side, o.quantity
                FROM execution_attempts a
                JOIN orders o ON o.idempotency_key = a.idempotency_key
                WHERE o.account_id = ?
                ORDER BY a.id DESC LIMIT 100
                """,
                (MULTI_SECTOR_ACCOUNT,),
            )
        ]
    today = now.strftime("%Y%m%d")
    preview = _daily_run_preview(today)
    return {
        "server_time": now.isoformat(),
        "scheduler": {
            **config,
            "next_run": scheduled.isoformat(),
            "seconds_to_next_run": max(
                0, int((scheduled - now).total_seconds())
            ),
        },
        "pending_orders": pending_orders,
        "deferred_attempts": [
            row for row in attempts if row["outcome"] != "FILLED"
        ],
        "recent_attempts": attempts,
        "task_runs": task_service.runs(30),
        "task_steps": task_service.steps(100),
        "notifications": task_service.notifications(100),
        "preview": preview,
        "readiness": _operational_readiness(today),
    }


@app.get("/api/operational-readiness")
def operational_readiness() -> dict[str, object]:
    return _operational_readiness(
        datetime.now(SHANGHAI).strftime("%Y%m%d")
    )


def _operational_readiness(trade_date: str) -> dict[str, object]:
    store = ParquetStore(DATA)
    checks = []
    try:
        calendar = store.read("trade_calendar")
        current = calendar.loc[
            calendar["cal_date"].astype(str) == trade_date
        ]
        covered = len(current) == 1
        session = (
            "OPEN"
            if covered and int(current.iloc[0]["is_open"]) == 1
            else "CLOSED"
            if covered
            else "UNKNOWN"
        )
    except FileNotFoundError:
        covered = False
        session = "UNKNOWN"
    checks.append(
        {
            "code": "OFFICIAL_CALENDAR",
            "status": "PASS" if covered else "REPAIR",
            "message": (
                f"正式日历确认当日{session}"
                if covered
                else "日度流水线启动后必须先同步正式交易日历"
            ),
        }
    )
    config = task_service.config()
    scheduler_alive = bool(
        task_service._thread and task_service._thread.is_alive()
    )
    checks.extend(
        [
            {
                "code": "SCHEDULER_ENABLED",
                "status": "PASS" if config["enabled"] else "BLOCK",
                "message": "服务端日度调度已启用" if config["enabled"] else "调度已暂停",
            },
            {
                "code": "SCHEDULER_THREAD",
                "status": "PASS" if scheduler_alive else "BLOCK",
                "message": (
                    "调度线程正在运行"
                    if scheduler_alive
                    else "调度线程未运行，需要重启API服务"
                ),
            },
        ]
    )
    blocking = sum(row["status"] == "BLOCK" for row in checks)
    repair = sum(row["status"] == "REPAIR" for row in checks)
    return {
        "trade_date": trade_date,
        "status": (
            "BLOCKED" if blocking else "REQUIRES_SYNC" if repair else "READY"
        ),
        "market_session": session,
        "blocking_count": blocking,
        "repair_count": repair,
        "checks": checks,
    }


@app.get("/api/tasks/daily-run/preview")
def preview_daily_run(trade_date: str | None = None) -> dict[str, object]:
    selected = trade_date or datetime.now(SHANGHAI).strftime("%Y%m%d")
    if len(selected) != 8 or not selected.isdigit():
        raise HTTPException(status_code=400, detail="trade_date must use YYYYMMDD")
    try:
        datetime.strptime(selected, "%Y%m%d").replace(tzinfo=SHANGHAI)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid trade_date") from error
    return _daily_run_preview(selected)


def _daily_run_preview(trade_date: str) -> dict[str, object]:
    preview = task_service.preview(trade_date)
    store = ParquetStore(DATA)
    market_session = "UNKNOWN"
    calendar_source = "LOCAL_TRADE_CALENDAR"
    try:
        calendar = store.read("trade_calendar")
        row = calendar.loc[calendar["cal_date"].astype(str) == trade_date]
        if not row.empty:
            market_session = "OPEN" if int(row.iloc[-1]["is_open"]) == 1 else "CLOSED"
        else:
            calendar_source = "WEEKDAY_ESTIMATE"
            market_session = (
                "OPEN_ESTIMATED"
                if datetime.strptime(trade_date, "%Y%m%d")
                .replace(tzinfo=SHANGHAI)
                .weekday()
                < 5
                else "CLOSED_ESTIMATED"
            )
    except FileNotFoundError:
        calendar_source = "WEEKDAY_ESTIMATE"
        market_session = (
            "OPEN_ESTIMATED"
            if datetime.strptime(trade_date, "%Y%m%d")
            .replace(tzinfo=SHANGHAI)
            .weekday()
            < 5
            else "CLOSED_ESTIMATED"
        )
    broker = PaperBroker(PAPER_DATABASE)
    pending = [
        row
        for row in broker.orders()
        if row.get("account_id") == MULTI_SECTOR_ACCOUNT
        and row.get("status") == "PENDING"
    ]
    eligible = [
        row for row in pending if str(row["signal_date"]) < trade_date
    ]
    latest_shadow = _latest_multi_sector_shadow()
    return {
        **preview,
        "market_session": market_session,
        "calendar_source": calendar_source,
        "pending_order_count": len(pending),
        "eligible_for_execution_count": len(eligible),
        "eligible_buy_count": sum(row["side"] == "BUY" for row in eligible),
        "eligible_sell_count": sum(row["side"] == "SELL" for row in eligible),
        "latest_shadow_date": latest_shadow.get("trade_date"),
        "risk_state": broker.risk_state(MULTI_SECTOR_ACCOUNT),
        "expected_effects": [
            "同步并校验指定交易日数据",
            f"最多尝试撮合 {len(eligible)} 笔此前待执行订单",
            "重新计算五行业目标权重并生成幂等信号",
            "更新模拟持仓、现金、归因、风险状态和对账",
        ],
        "warnings": (
            ["本地交易日历未覆盖该日期，开闭市状态仅按工作日估计"]
            if calendar_source == "WEEKDAY_ESTIMATE"
            else []
        ),
        "mutates_state": False,
    }


@app.post("/api/notifications/{notification_id}/acknowledge")
def acknowledge_notification(notification_id: int) -> dict[str, Any]:
    try:
        return task_service.acknowledge_notification(notification_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="notification not found") from error


@app.get("/api/task-config")
def task_config() -> dict[str, object]:
    return task_service.config()


@app.post("/api/task-config")
def update_task_config(payload: dict[str, object]) -> dict[str, object]:
    try:
        enabled = bool(payload["enabled"])
        hour = int(payload["hour"])
        minute = int(payload["minute"])
        return task_service.update_config(enabled, hour, minute)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/tasks/daily-run")
def trigger_daily(payload: dict[str, str] | None = None) -> dict[str, object]:
    trade_date = (payload or {}).get(
        "trade_date", datetime.now(SHANGHAI).strftime("%Y%m%d")
    )
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise HTTPException(status_code=400, detail="trade_date must use YYYYMMDD")
    return {
        "run_id": task_service.trigger_recovery(trade_date),
        "status": "QUEUED_RECOVERY",
        "mode": "RECOVER_MISSING_TRADING_DAYS_THEN_RECALCULATE",
    }


@app.get("/api/reports/{filename}")
def report(filename: str) -> dict[str, object]:
    safe_name = Path(filename).name
    target = STATE / "daily-runs" / safe_name
    if not target.exists() or target.suffix != ".json":
        raise HTTPException(status_code=404, detail="report not found")
    return json.loads(target.read_text(encoding="utf-8"))


@lru_cache(maxsize=16)
def _research_payload(symbol: str, name: str, research_status: str) -> dict[str, object]:
    store = ParquetStore(DATA)
    config = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
    primary = research_single_stock(store, symbol, config)
    robustness = robustness_single_stock(store, symbol, config)
    weighted = research_weighted_strategies(store, symbol, config)
    allocations = allocation_comparison(store, symbol, config)
    annual = annual_comparison(store, symbol, config)
    result = candidate_result(store, symbol, config, 0.8)
    ledger = trade_ledger(result)
    curve = result.equity.copy()
    curve["date"] = curve["date"].astype(str).str[:10]
    sampled = curve.iloc[::20].copy()
    if sampled.iloc[-1]["date"] != curve.iloc[-1]["date"]:
        sampled = sampled._append(curve.iloc[-1], ignore_index=True)
    coverage = {}
    for table in ("daily_basic", "dividend", "fina_indicator"):
        try:
            frame = store.read(table, filters=[("ts_code", "==", symbol)])
            coverage[table] = {"rows": len(frame)}
            date_column = "trade_date" if table == "daily_basic" else "ann_date"
            dates = frame[date_column].dropna().astype(str)
            coverage[table].update(
                {
                    "first_date": dates.min() if not dates.empty else None,
                    "last_date": dates.max() if not dates.empty else None,
                }
            )
        except FileNotFoundError:
            coverage[table] = {"rows": 0, "first_date": None, "last_date": None}
    return {
        "symbol": symbol,
        "symbol_name": name,
        "strategy": {
            "id": "cmb_ma_120_250_v1",
            "fast": 120,
            "slow": 250,
            "account_weight": 0.10,
            "research_sleeve_weight": 0.80,
            "execution": "收盘信号，下一交易日开盘成交",
            "status": research_status,
        },
        "fundamental_context": _fundamental_context(symbol),
        "primary": _records(primary),
        "robustness": _records(robustness),
        "weighted_strategies": _records(weighted),
        "data_coverage": coverage,
        "allocations": _records(allocations),
        "annual": _records(annual),
        "trades": _records(ledger),
        "equity_curve": _records(sampled[["date", "equity", "drawdown"]]),
    }


def _instrument_names(store: ParquetStore) -> dict[str, str]:
    instruments = store.read("instruments", columns=["ts_code", "name"])
    return dict(
        zip(
            instruments["ts_code"].astype(str),
            instruments["name"].astype(str),
            strict=True,
        )
    )


def _records(frame: Any) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _fundamental_context(symbol: str) -> dict[str, object] | None:
    if symbol != "600900.SH":
        return None
    return {
        "summary": (
            "公司以大型水电运营为核心，运行管理长江干流六座梯级电站；"
            "量化趋势策略仅刻画价格路径，不替代对水文、电价和安全生产的判断。"
        ),
        "strengths": [
            "六座梯级电站联合调度形成规模和运营能力",
            "大型水电兼具电能量、调节与绿色环境价值",
            "现金分红特征突出，适合与价格趋势模型分开评估",
        ],
        "risks": [
            "长江中上游来水不确定性直接影响发电量",
            "大型机组、大坝及极端天气带来安全生产风险",
            "统一电力市场改革使电量、电价和辅助服务收益面临变化",
            "境内外投资面临市场、政策、汇率与地缘政治风险",
        ],
        "source": "中国长江电力股份有限公司2025年年度报告",
        "source_url": (
            "https://www.cypc.com.cn/cypc/attachDir/2026/05/"
            "2026051815345898336.pdf"
        ),
    }
