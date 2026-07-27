from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import BacktestConfig
from .daily import run_daily_pipeline
from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore
from .data.tushare_provider import TushareProvider
from .execution.paper import PaperBroker
from .research.adaptive import research_weighted_strategies
from .research.detail import (
    allocation_comparison,
    annual_comparison,
    candidate_result,
    trade_ledger,
)
from .research.single_stock import research_single_stock, robustness_single_stock
from .signals import trend_decision

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "state"
DATA = ROOT / "data"
PAPER_DATABASE = STATE / "paper_orders.sqlite3"
SERVICE_DATABASE = STATE / "service.sqlite3"
SYMBOL = "600036.SH"
ACCOUNT = "default"
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_runs(
                    task_name, trade_date, source, status, started_at
                ) VALUES ('daily_pipeline', ?, ?, 'QUEUED', ?)
                """,
                (trade_date, source, datetime.now(SHANGHAI).isoformat()),
            )
            run_id = int(cursor.lastrowid)
        threading.Thread(
            target=self._execute,
            args=(run_id, trade_date),
            name=f"daily-run-{run_id}",
            daemon=True,
        ).start()
        return run_id

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM task_runs ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in rows]

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
                self.trigger(trade_date, "SCHEDULED")

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
            results = []
            for candidate in self.candidates(enabled_only=True):
                results.append(
                    run_daily_pipeline(
                        provider=provider,
                        store=store,
                        broker=broker,
                        config=config,
                        symbol=str(candidate["symbol"]),
                        trade_date=trade_date,
                        account_id=ACCOUNT,
                        signal_dir=STATE / "signals",
                        report_dir=STATE / "daily-runs",
                        strategy_id=str(candidate["strategy_id"]),
                        active_weight=float(candidate["target_weight"]),
                    )
                )
            statuses = {result.status for result in results}
            status = "COMPLETED" if statuses <= {
                "COMPLETED",
                "SKIPPED_MARKET_CLOSED",
                "SKIPPED_SUSPENDED",
            } else "FAILED"
            report_paths = json.dumps(
                [result.report_path for result in results], ensure_ascii=False
            )
            self._finish(run_id, status, report_path=report_paths)
        except Exception as error:  # noqa: BLE001 - task failures must stay observable
            self._finish(run_id, "FAILED", error=f"{type(error).__name__}: {error}")
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
    return {"run_id": task_service.trigger(trade_date), "status": "QUEUED"}


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
