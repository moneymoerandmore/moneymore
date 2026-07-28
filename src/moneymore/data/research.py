from __future__ import annotations

import pandas as pd

from .store import ParquetStore


def load_adjusted_stock_bars(store: ParquetStore, symbol: str) -> pd.DataFrame:
    """Build research bars with raw execution prices and adjusted signal prices."""
    filters = [("ts_code", "==", symbol)]
    bars = store.read("daily", filters=filters)
    factors = store.read(
        "adj_factor",
        columns=["ts_code", "trade_date", "adj_factor"],
        filters=filters,
    )[["trade_date", "adj_factor"]]
    if bars.empty:
        raise ValueError(f"symbol not found in local daily data: {symbol}")
    merged = bars.merge(
        factors,
        on="trade_date",
        how="left",
        validate="one_to_one",
    )
    if merged["adj_factor"].isna().any():
        missing = merged.loc[merged["adj_factor"].isna(), "trade_date"].astype(str).tolist()
        raise ValueError(f"missing adjustment factors for {symbol}: {missing[:5]}")
    latest_factor = float(
        merged.sort_values("trade_date", ascending=True)["adj_factor"].iloc[-1]
    )
    merged["signal_close"] = merged["close"] * merged["adj_factor"] / latest_factor
    merged = merged.rename(columns={"trade_date": "date", "ts_code": "symbol"})
    merged["tradable"] = True
    return merged.sort_values("date").reset_index(drop=True)


def load_total_return_stock_bars(store: ParquetStore, symbol: str) -> pd.DataFrame:
    """Return adjusted OHLC bars for total-return research.

    These synthetic prices are appropriate for historical return comparisons.
    Live orders must continue to use the unadjusted exchange prices.
    """
    bars = load_adjusted_stock_bars(store, symbol)
    ratio = bars["adj_factor"] / float(bars["adj_factor"].iloc[-1])
    for column in ("open", "high", "low", "close"):
        bars[f"raw_{column}"] = bars[column]
        bars[column] = bars[column] * ratio
    bars["signal_close"] = bars["close"]
    try:
        limits = store.read(
            "stock_limits",
            columns=["ts_code", "trade_date", "up_limit", "down_limit"],
            filters=[("ts_code", "==", symbol)],
        )
    except FileNotFoundError:
        bars["can_buy"] = True
        bars["can_sell"] = True
        return bars
    limits = limits.rename(columns={"trade_date": "date"})
    bars = bars.merge(
        limits[["date", "up_limit", "down_limit"]],
        on="date",
        how="left",
        validate="one_to_one",
    )
    tolerance = 1e-8
    bars["can_buy"] = bars["up_limit"].isna() | (
        bars["raw_open"] < bars["up_limit"] - tolerance
    )
    bars["can_sell"] = bars["down_limit"].isna() | (
        bars["raw_open"] > bars["down_limit"] + tolerance
    )
    return bars


def load_point_in_time_features(store: ParquetStore, symbol: str) -> pd.DataFrame:
    """Daily features using only information available before each trading day.

    Daily valuation data is lagged one trading observation. Financial statements
    become visible strictly after their Tushare announcement date.
    """
    bars = load_total_return_stock_bars(store, symbol)
    basic = store.read(
        "daily_basic", filters=[("ts_code", "==", symbol)]
    ).rename(columns={"trade_date": "date"})
    basic["date"] = pd.to_datetime(basic["date"])
    basic = basic.sort_values("date")
    market_columns = (
        "dv_ttm", "pb", "pe_ttm", "turnover_rate", "volume_ratio",
        "total_mv", "circ_mv",
    )
    for column in market_columns:
        if column not in basic:
            basic[column] = pd.NA
    basic[list(market_columns)] = basic[list(market_columns)].shift(1)

    result = bars.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.merge(
        basic[["date", *market_columns]],
        on="date",
        how="left",
        validate="one_to_one",
    )

    financial = store.read(
        "fina_indicator", filters=[("ts_code", "==", symbol)]
    ).copy()
    financial["available_date"] = pd.to_datetime(
        financial["ann_date"], format="%Y%m%d"
    ) + pd.offsets.Day(1)
    financial = financial.sort_values(["available_date", "end_date"]).drop_duplicates(
        "available_date", keep="last"
    )
    financial_columns = (
        "roe", "ocfps", "debt_to_assets", "netprofit_yoy", "q_sales_yoy",
    )
    selected = ["available_date"] + [
        column for column in financial_columns if column in financial
    ]
    result = pd.merge_asof(
        result.sort_values("date"),
        financial[selected].sort_values("available_date"),
        left_on="date",
        right_on="available_date",
        direction="backward",
    )
    return result.drop(columns=["available_date"])
