from __future__ import annotations

import json
import math
import sqlite3
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ACCOUNT_ID = "tqqq_sqqq_v5a_paper"
STRATEGY_ID = "v5a_vix_spike"
INITIAL_CASH = 100_000.0
TRADE_SYMBOLS = ("TQQQ", "SQQQ")
DATA_SYMBOLS = ("QQQ", "TQQQ", "SQQQ", "^VIX")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Allocation:
    signal_date: str
    regime: str
    tqqq: float
    sqqq: float
    cash: float
    score: float
    qqq_close: float
    ma180: float
    vix: float
    vix_sma20: float
    annualized_volatility: float
    macd_histogram: float
    ma_slope: float


class YahooDailyClient:
    """Small, dependency-free Yahoo chart client used only by this US ETF sleeve."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        period1 = int(pd.Timestamp(start, tz="UTC").timestamp())
        period2 = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
        encoded = urllib.parse.quote(symbol, safe="")
        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
            f"?period1={period1}&period2={period2}&interval=1d&events=div%2Csplits"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MoneyMore/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        result = payload["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(result["timestamp"], unit="s", utc=True)
                .tz_convert("America/New_York")
                .strftime("%Y-%m-%d"),
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            }
        )
        return frame.dropna(subset=["open", "close"]).drop_duplicates("trade_date", keep="last")


class LeveragedEtfPaper:
    """Fully isolated TQQQ/SQQQ/Cash paper account and V5A-style strategy."""

    def __init__(self, database: str | Path, cache_dir: str | Path) -> None:
        self.database = Path(database)
        self.cache_dir = Path(cache_dir)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS account (
                    account_id TEXT PRIMARY KEY, initial_cash REAL NOT NULL,
                    cash REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL,
                    avg_cost REAL NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_date TEXT PRIMARY KEY, regime TEXT NOT NULL,
                    tqqq REAL NOT NULL, sqqq REAL NOT NULL, cash REAL NOT NULL,
                    score REAL NOT NULL, indicators_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, signal_date TEXT NOT NULL,
                    execution_date TEXT, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    quantity INTEGER NOT NULL, status TEXT NOT NULL, target_weight REAL NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(signal_date, symbol, side)
                );
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE,
                    signal_date TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
                    side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL,
                    fee REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equity_daily (
                    trade_date TEXT PRIMARY KEY, cash REAL NOT NULL, market_value REAL NOT NULL,
                    equity REAL NOT NULL, tqqq_quantity INTEGER NOT NULL,
                    sqqq_quantity INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO account VALUES (?, ?, ?, ?, ?)",
                (ACCOUNT_ID, INITIAL_CASH, INITIAL_CASH, now, now),
            )

    def refresh_data(self, client: YahooDailyClient | None = None) -> dict[str, pd.DataFrame]:
        client = client or YahooDailyClient()
        end = datetime.now(UTC).date().isoformat()
        start = (datetime.now(UTC).date() - timedelta(days=900)).isoformat()
        result: dict[str, pd.DataFrame] = {}
        for symbol in DATA_SYMBOLS:
            cache = self.cache_dir / f"{symbol.replace('^', '')}.csv"
            try:
                fresh = client.history(symbol, start, end)
                if fresh.empty:
                    raise RuntimeError(f"empty Yahoo history: {symbol}")
                fresh.to_csv(cache, index=False)
                result[symbol] = fresh
            except Exception:
                if not cache.exists():
                    raise
                result[symbol] = pd.read_csv(cache)
        return self._align(result)

    @staticmethod
    def _align(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        common = set(data["QQQ"]["trade_date"].astype(str))
        for symbol in DATA_SYMBOLS:
            common &= set(data[symbol]["trade_date"].astype(str))
        dates = sorted(common)
        if len(dates) < 200:
            raise RuntimeError("TQQQ/SQQQ strategy requires at least 200 aligned sessions")
        return {
            symbol: frame.assign(trade_date=frame["trade_date"].astype(str))
            .loc[lambda item: item["trade_date"].isin(dates)]
            .sort_values("trade_date")
            .reset_index(drop=True)
            for symbol, frame in data.items()
        }

    @staticmethod
    def allocation(data: dict[str, pd.DataFrame], index: int = -1) -> Allocation:
        qqq = data["QQQ"].copy()
        close = qqq["close"].astype(float)
        returns = close.pct_change()
        ma180 = close.rolling(180).mean()
        volatility = returns.rolling(20).std() * math.sqrt(252)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        histogram = macd - macd.ewm(span=9, adjust=False).mean()
        slope = ma180.pct_change(20) * 100
        vix = data["^VIX"]["close"].astype(float)
        vix_sma20 = vix.rolling(20).mean()
        position = index if index >= 0 else len(qqq) + index
        values = [ma180.iloc[position], volatility.iloc[position], histogram.iloc[position], slope.iloc[position], vix_sma20.iloc[position]]
        if any(pd.isna(value) for value in values):
            raise RuntimeError("strategy indicators are not ready")
        price = float(close.iloc[position])
        long_ma = float(ma180.iloc[position])
        vol = float(volatility.iloc[position])
        hist = float(histogram.iloc[position])
        ma_slope = float(slope.iloc[position])
        vix_value = float(vix.iloc[position])
        vix_average = float(vix_sma20.iloc[position])
        below_ma = price <= long_ma
        spike = vix_value > vix_average * 1.20
        genuine_bear = below_ma and (
            (vix_value > 25.0 and ma_slope < -0.04) or (spike and ma_slope < -0.04)
        )
        score = 0.0
        if vol <= 0.20:
            score += 20
        elif vol < 0.35:
            score += 20 * (1 - (vol - 0.20) / 0.15)
        if vix_value <= 15:
            score += 20
        elif vix_value < 35:
            score += 20 * (1 - (vix_value - 15) / 20)
        score += 20 if hist > 0 else (10 if hist > -1 else 0)
        score += 20 if ma_slope > 0.05 else (10 if ma_slope > -0.02 else 0)
        if not below_ma:
            score += 20
        if genuine_bear:
            sqqq = min(0.20, max(0.0, (long_ma - price) / long_ma * 2.5)) if hist < -1 and ma_slope < -0.10 else 0.0
            tqqq, regime = 0.0, "BEAR_SQQQ" if sqqq else "BEAR_CASH"
        elif below_ma and hist > 0 and ma_slope > -0.04:
            tqqq, sqqq, regime = 0.50, 0.0, "EARLY_REENTRY"
        else:
            if score >= 80:
                tqqq = 1.0
            elif score >= 60:
                tqqq = 0.70 + 0.30 * (score - 60) / 20
            elif score >= 40:
                tqqq = 0.30 + 0.40 * (score - 40) / 20
            else:
                tqqq = 0.0
            sqqq, regime = 0.0, "BULL" if tqqq else "CASH"
        return Allocation(
            signal_date=str(qqq.iloc[position]["trade_date"]), regime=regime,
            tqqq=round(tqqq, 6), sqqq=round(sqqq, 6), cash=round(1 - tqqq - sqqq, 6),
            score=round(score, 4), qqq_close=price, ma180=long_ma, vix=vix_value,
            vix_sma20=vix_average, annualized_volatility=vol,
            macd_histogram=hist, ma_slope=ma_slope,
        )

    def run(self, client: YahooDailyClient | None = None) -> dict[str, object]:
        data = self.refresh_data(client)
        now = datetime.now(NEW_YORK)
        today = now.date().isoformat()
        latest_date = str(data["QQQ"].iloc[-1]["trade_date"])
        # Yahoo exposes today's still-forming daily candle during the session.
        # Startup recovery must never turn that partial candle into a close signal.
        if latest_date == today and (now.hour, now.minute) < (16, 15):
            data = {
                symbol: frame.loc[frame["trade_date"].astype(str) < today]
                .reset_index(drop=True)
                for symbol, frame in data.items()
            }
        return self._run_aligned(data)

    def _run_aligned(self, data: dict[str, pd.DataFrame]) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(signal_date) FROM signals").fetchone()
            last_signal_date = None if row is None else row[0]
        indices = [
            index
            for index, trade_date in enumerate(data["QQQ"]["trade_date"].astype(str))
            if last_signal_date is None or trade_date > str(last_signal_date)
        ]
        # A new account starts from the latest completed session; an existing
        # account replays every missing session so orders, holdings and NAV do
        # not jump across an outage.
        if last_signal_date is None and indices:
            indices = indices[-1:]
        for index in indices:
            trade_date = str(data["QQQ"].iloc[index]["trade_date"])
            opens = {symbol: float(data[symbol].iloc[index]["open"]) for symbol in TRADE_SYMBOLS}
            closes = {symbol: float(data[symbol].iloc[index]["close"]) for symbol in TRADE_SYMBOLS}
            self._execute_pending(trade_date, opens)
            self._record_equity(trade_date, closes)
            self._queue(self.allocation(data, index), closes)
        closes = {symbol: float(data[symbol].iloc[-1]["close"]) for symbol in TRADE_SYMBOLS}
        return self.snapshot(closes)

    def run_open(self, client: YahooDailyClient | None = None) -> dict[str, object]:
        """Execute prior-close orders using today's official session open.

        This deliberately does not calculate a new signal from the incomplete
        intraday daily bar returned shortly after the US open.
        """
        data = self.refresh_data(client)
        trade_date = str(data["QQQ"].iloc[-1]["trade_date"])
        expected_date = datetime.now(NEW_YORK).date().isoformat()
        if trade_date != expected_date:
            raise RuntimeError(
                f"US session open bar is not available yet: expected {expected_date}, got {trade_date}"
            )
        opens = {symbol: float(data[symbol].iloc[-1]["open"]) for symbol in TRADE_SYMBOLS}
        closes = {symbol: float(data[symbol].iloc[-1]["close"]) for symbol in TRADE_SYMBOLS}
        self._execute_pending(trade_date, opens)
        self._record_equity(trade_date, closes)
        return self.snapshot(closes)

    def run_close(self, client: YahooDailyClient | None = None) -> dict[str, object]:
        """Mark the account and create the next-session target after US close."""
        payload = self.run(client)
        expected_date = datetime.now(NEW_YORK).date().isoformat()
        latest_signal = payload.get("latest_signal")
        actual_date = None if latest_signal is None else latest_signal.get("signal_date")
        if actual_date != expected_date:
            raise RuntimeError(
                f"US session close bar is not available yet: expected {expected_date}, got {actual_date}"
            )
        return payload

    def _execute_pending(self, trade_date: str, opens: dict[str, float]) -> None:
        with self._connect() as connection:
            pending = connection.execute(
                "SELECT * FROM orders WHERE status = 'PENDING' AND signal_date < ? ORDER BY CASE side WHEN 'SELL' THEN 0 ELSE 1 END, id",
                (trade_date,),
            ).fetchall()
            for order in pending:
                symbol, side, requested = str(order["symbol"]), str(order["side"]), int(order["quantity"])
                raw_price = float(opens[symbol])
                price = raw_price * (1.0005 if side == "BUY" else 0.9995)
                account = connection.execute("SELECT cash FROM account WHERE account_id = ?", (ACCOUNT_ID,)).fetchone()
                position = connection.execute("SELECT quantity, avg_cost FROM positions WHERE symbol = ?", (symbol,)).fetchone()
                held = int(position["quantity"]) if position else 0
                quantity = min(requested, int(float(account["cash"]) // price)) if side == "BUY" else min(requested, held)
                if quantity <= 0:
                    connection.execute("UPDATE orders SET status='CANCELLED', execution_date=? WHERE id=?", (trade_date, order["id"]))
                    continue
                fee = quantity * price * 0.0001
                cash_delta = -(quantity * price + fee) if side == "BUY" else quantity * price - fee
                new_quantity = held + quantity if side == "BUY" else held - quantity
                old_cost = float(position["avg_cost"]) if position else 0.0
                avg_cost = ((held * old_cost + quantity * price) / new_quantity) if side == "BUY" and new_quantity else (old_cost if new_quantity else 0.0)
                now = datetime.now(UTC).isoformat()
                connection.execute("UPDATE account SET cash=cash+?, updated_at=? WHERE account_id=?", (cash_delta, now, ACCOUNT_ID))
                connection.execute(
                    "INSERT INTO positions VALUES (?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity, avg_cost=excluded.avg_cost, updated_at=excluded.updated_at",
                    (symbol, new_quantity, avg_cost, now),
                )
                connection.execute("UPDATE orders SET status='FILLED', execution_date=? WHERE id=?", (trade_date, order["id"]))
                connection.execute(
                    "INSERT INTO fills(order_id, signal_date, trade_date, symbol, side, quantity, price, fee, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (order["id"], order["signal_date"], trade_date, symbol, side, quantity, price, fee, now),
                )

    def _queue(self, allocation: Allocation, closes: dict[str, float]) -> None:
        weights = {"TQQQ": allocation.tqqq, "SQQQ": allocation.sqqq}
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM signals WHERE signal_date=?", (allocation.signal_date,)).fetchone():
                return
            account = connection.execute("SELECT cash FROM account WHERE account_id=?", (ACCOUNT_ID,)).fetchone()
            positions = {row["symbol"]: int(row["quantity"]) for row in connection.execute("SELECT * FROM positions")}
            equity = float(account["cash"]) + sum(positions.get(symbol, 0) * closes[symbol] for symbol in TRADE_SYMBOLS)
            now = datetime.now(UTC).isoformat()
            indicators = asdict(allocation)
            connection.execute(
                "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (allocation.signal_date, allocation.regime, allocation.tqqq, allocation.sqqq,
                 allocation.cash, allocation.score, json.dumps(indicators), now),
            )
            for symbol in TRADE_SYMBOLS:
                target = int(equity * weights[symbol] // closes[symbol])
                current = positions.get(symbol, 0)
                difference = target - current
                if difference == 0:
                    continue
                side = "BUY" if difference > 0 else "SELL"
                connection.execute(
                    "INSERT OR IGNORE INTO orders(signal_date, symbol, side, quantity, status, target_weight, reason, created_at) VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?)",
                    (allocation.signal_date, symbol, side, abs(difference), weights[symbol], allocation.regime, now),
                )

    def _record_equity(self, trade_date: str, closes: dict[str, float]) -> None:
        with self._connect() as connection:
            cash = float(connection.execute("SELECT cash FROM account WHERE account_id=?", (ACCOUNT_ID,)).fetchone()[0])
            positions = {row["symbol"]: int(row["quantity"]) for row in connection.execute("SELECT * FROM positions")}
            market_value = sum(positions.get(symbol, 0) * closes[symbol] for symbol in TRADE_SYMBOLS)
            connection.execute(
                "INSERT OR REPLACE INTO equity_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trade_date, cash, market_value, cash + market_value, positions.get("TQQQ", 0),
                 positions.get("SQQQ", 0), datetime.now(UTC).isoformat()),
            )

    def snapshot(self, mark_prices: dict[str, float] | None = None) -> dict[str, object]:
        mark_prices = mark_prices or self._cached_marks()
        with self._connect() as connection:
            account = dict(connection.execute("SELECT * FROM account WHERE account_id=?", (ACCOUNT_ID,)).fetchone())
            positions = [dict(row) for row in connection.execute("SELECT * FROM positions WHERE quantity != 0 ORDER BY symbol")]
            signals = [dict(row) for row in connection.execute("SELECT * FROM signals ORDER BY signal_date DESC LIMIT 30")]
            orders = [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100")]
            fills = [dict(row) for row in connection.execute("SELECT * FROM fills ORDER BY id DESC LIMIT 100")]
            history = [dict(row) for row in connection.execute("SELECT * FROM equity_daily ORDER BY trade_date")]
        for position in positions:
            price = float(mark_prices.get(str(position["symbol"]), position["avg_cost"]))
            position["mark_price"] = price
            position["market_value"] = int(position["quantity"]) * price
            position["unrealized_pnl"] = int(position["quantity"]) * (price - float(position["avg_cost"]))
        market_value = sum(float(row["market_value"]) for row in positions)
        equity = float(account["cash"]) + market_value
        latest_signal = signals[0] if signals else None
        if latest_signal:
            latest_signal["indicators"] = json.loads(str(latest_signal.pop("indicators_json")))
        peak = max([INITIAL_CASH, *(float(row["equity"]) for row in history)])
        return {
            "account_id": ACCOUNT_ID, "strategy_id": STRATEGY_ID,
            "initial_cash": INITIAL_CASH, "currency": "USD", "status": "PAPER_ONLY",
            "cash": float(account["cash"]), "market_value": market_value, "equity": equity,
            "total_return": equity / INITIAL_CASH - 1, "drawdown": equity / peak - 1,
            "positions": positions, "latest_signal": latest_signal,
            "orders": orders, "fills": fills, "history": history,
            "data_source": "Yahoo Finance daily chart; cached independently",
            "execution_policy": "close signal / next session open / 5 bps slippage / 1 bp fee",
        }

    def _cached_marks(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for symbol in TRADE_SYMBOLS:
            path = self.cache_dir / f"{symbol}.csv"
            if path.exists():
                frame = pd.read_csv(path)
                if not frame.empty:
                    result[symbol] = float(frame.iloc[-1]["close"])
        return result


class LeveragedEtfScheduler:
    """US-session scheduler, independent from MoneyMore's A-share timetable."""

    def __init__(self, paper: LeveragedEtfPaper, poll_seconds: int = 30) -> None:
        self.paper = paper
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_open_run: str | None = None
        self.last_close_run: str | None = None
        self.last_startup_run: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="moneymore-us-etf-paper", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def status(self) -> dict[str, object]:
        now = datetime.now(NEW_YORK)
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "timezone": "America/New_York",
            "now": now.isoformat(),
            "open_schedule": "09:35",
            "close_schedule": "16:15",
            "last_open_run": self.last_open_run,
            "last_close_run": self.last_close_run,
            "last_startup_run": self.last_startup_run,
            "last_error": self.last_error,
        }

    def _loop(self) -> None:
        # Every service start immediately repairs missed completed sessions.
        # The normal open/close clocks below then take over for the live session.
        try:
            self.paper.run()
            self.last_startup_run = datetime.now(NEW_YORK).isoformat()
            self.last_error = None
        except Exception as error:  # noqa: BLE001 - retry on the timed loop
            self.last_error = f"{type(error).__name__}: {error}"
        while not self._stop.is_set():
            now = datetime.now(NEW_YORK)
            session = now.date().isoformat()
            weekday = now.weekday() < 5
            try:
                if weekday and (now.hour, now.minute) >= (9, 35) and self.last_open_run != session:
                    self.paper.run_open()
                    self.last_open_run = session
                    self.last_error = None
                if weekday and (now.hour, now.minute) >= (16, 15) and self.last_close_run != session:
                    self.paper.run_close()
                    self.last_close_run = session
                    self.last_error = None
            except Exception as error:  # noqa: BLE001 - retry transient quote/feed failures
                self.last_error = f"{type(error).__name__}: {error}"
            self._stop.wait(self.poll_seconds)
