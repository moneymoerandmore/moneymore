from __future__ import annotations

import pandas as pd


class DataQualityError(ValueError):
    pass


def validate_instruments(frame: pd.DataFrame) -> None:
    _require_columns(frame, {"ts_code", "list_status", "list_date"}, "instruments")
    _require_nonempty(frame, "instruments")
    _require_unique(frame, ["ts_code"], "instruments")


def validate_calendar(frame: pd.DataFrame) -> None:
    _require_columns(frame, {"cal_date", "is_open"}, "trade_calendar")
    _require_nonempty(frame, "trade_calendar")
    _require_unique(frame, ["cal_date"], "trade_calendar")
    invalid = ~frame["is_open"].astype(str).isin({"0", "1"})
    if invalid.any():
        raise DataQualityError("trade_calendar contains invalid is_open values")


def validate_daily_bars(frame: pd.DataFrame, trade_date: str) -> None:
    required = {
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
    }
    _require_columns(frame, required, "daily")
    _require_nonempty(frame, f"daily[{trade_date}]")
    _require_unique(frame, ["ts_code", "trade_date"], "daily")
    dates = set(frame["trade_date"].astype(str))
    if dates != {trade_date}:
        raise DataQualityError(f"daily response dates {sorted(dates)} != {trade_date}")
    prices = frame[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise DataQualityError("daily contains null or non-positive prices")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise DataQualityError("daily contains high below OHLC values")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise DataQualityError("daily contains low above OHLC values")
    if (frame[["vol", "amount"]] < 0).any().any():
        raise DataQualityError("daily contains negative volume or amount")


def validate_adjustment_factors(frame: pd.DataFrame, trade_date: str) -> None:
    _require_columns(frame, {"ts_code", "trade_date", "adj_factor"}, "adj_factor")
    _require_nonempty(frame, f"adj_factor[{trade_date}]")
    _require_unique(frame, ["ts_code", "trade_date"], "adj_factor")
    if set(frame["trade_date"].astype(str)) != {trade_date}:
        raise DataQualityError("adj_factor contains an unexpected trade_date")
    if frame["adj_factor"].isna().any() or (frame["adj_factor"] <= 0).any():
        raise DataQualityError("adj_factor contains null or non-positive values")


def validate_range_bars(frame: pd.DataFrame, table: str) -> None:
    required = {"ts_code", "trade_date", "open", "high", "low", "close"}
    _require_columns(frame, required, table)
    _require_nonempty(frame, table)
    _require_unique(frame, ["ts_code", "trade_date"], table)
    prices = frame[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise DataQualityError(f"{table} contains null or non-positive prices")


def validate_stock_limits(frame: pd.DataFrame) -> None:
    required = {"ts_code", "trade_date", "up_limit", "down_limit"}
    _require_columns(frame, required, "stock_limits")
    _require_nonempty(frame, "stock_limits")
    _require_unique(frame, ["ts_code", "trade_date"], "stock_limits")
    if (frame["up_limit"] <= frame["down_limit"]).any():
        raise DataQualityError("stock_limits contains up_limit <= down_limit")


def validate_daily_basic(frame: pd.DataFrame) -> None:
    required = {"ts_code", "trade_date", "close", "pb", "dv_ttm", "total_mv"}
    _require_columns(frame, required, "daily_basic")
    _require_nonempty(frame, "daily_basic")
    _require_unique(frame, ["ts_code", "trade_date"], "daily_basic")
    for column in ("close", "pb", "dv_ttm", "total_mv"):
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if (values < 0).any() or (column != "dv_ttm" and (values == 0).any()):
            raise DataQualityError(f"daily_basic contains invalid {column}")


def validate_dividends(frame: pd.DataFrame) -> None:
    required = {
        "ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax", "ex_date"
    }
    _require_columns(frame, required, "dividend")
    _require_nonempty(frame, "dividend")


def validate_financial_indicators(frame: pd.DataFrame) -> None:
    required = {"ts_code", "ann_date", "end_date", "roe", "ocfps"}
    _require_columns(frame, required, "fina_indicator")
    _require_nonempty(frame, "fina_indicator")
    if frame["ann_date"].isna().any():
        raise DataQualityError("fina_indicator contains null announcement dates")


def _require_columns(frame: pd.DataFrame, required: set[str], table: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise DataQualityError(f"{table} missing columns: {sorted(missing)}")


def _require_nonempty(frame: pd.DataFrame, table: str) -> None:
    if frame.empty:
        raise DataQualityError(f"{table} is empty")


def _require_unique(frame: pd.DataFrame, keys: list[str], table: str) -> None:
    if frame.duplicated(keys).any():
        raise DataQualityError(f"{table} contains duplicate keys: {keys}")
