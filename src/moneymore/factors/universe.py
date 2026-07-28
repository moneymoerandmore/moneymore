from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class UniverseDefinition:
    name: str
    industries: tuple[str, ...]
    minimum_listing_days: int = 120
    minimum_average_amount: float = 100_000.0
    liquidity_lookback: int = 20


def monthly_rebalance_dates(
    bars: pd.DataFrame, start_date: str, end_date: str
) -> pd.DatetimeIndex:
    dates = pd.to_datetime(bars["trade_date"].drop_duplicates())
    selected = dates[
        (dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))
    ]
    if selected.empty:
        return pd.DatetimeIndex([])
    series = pd.Series(selected.sort_values())
    return pd.DatetimeIndex(series.groupby(series.dt.to_period("M")).max())


def build_historical_membership(
    instruments: pd.DataFrame,
    bars: pd.DataFrame,
    rebalance_dates: pd.DatetimeIndex,
    definition: UniverseDefinition,
) -> pd.DataFrame:
    """Build listing-safe membership using only liquidity known by each date.

    Tushare stock_basic industry is a current classification, not a historical
    constituent table. The output explicitly records that limitation.
    """
    required_instruments = {
        "ts_code", "industry", "list_date", "delist_date", "list_status"
    }
    required_bars = {"ts_code", "trade_date", "amount"}
    _require_columns(instruments, required_instruments, "instruments")
    _require_columns(bars, required_bars, "daily bars")
    selected = instruments.loc[
        instruments["industry"].isin(definition.industries)
    ].copy()
    selected["list_date"] = pd.to_datetime(
        selected["list_date"], format="%Y%m%d", errors="coerce"
    )
    selected["delist_date"] = pd.to_datetime(
        selected["delist_date"], format="%Y%m%d", errors="coerce"
    )

    history = bars.loc[bars["ts_code"].isin(selected["ts_code"])].copy()
    history["date"] = pd.to_datetime(history["trade_date"], format="%Y%m%d")
    history = history.sort_values(["ts_code", "date"])
    history["average_amount"] = history.groupby("ts_code", sort=False)[
        "amount"
    ].transform(
        lambda values: values.rolling(
            definition.liquidity_lookback,
            min_periods=definition.liquidity_lookback,
        ).mean()
    )
    liquidity = history[["ts_code", "date", "average_amount"]]
    rows: list[pd.DataFrame] = []
    for date in rebalance_dates:
        age_cutoff = date - pd.offsets.Day(definition.minimum_listing_days)
        active = selected.loc[
            (selected["list_date"] <= age_cutoff)
            & (selected["delist_date"].isna() | (selected["delist_date"] > date))
        ].copy()
        if active.empty:
            continue
        known = liquidity.loc[liquidity["date"] <= date].groupby(
            "ts_code", as_index=False
        ).tail(1)
        active = active.merge(
            known[["ts_code", "average_amount"]],
            on="ts_code",
            how="left",
            validate="one_to_one",
        )
        active = active.loc[
            active["average_amount"] >= definition.minimum_average_amount
        ]
        if active.empty:
            continue
        active["date"] = date
        active["universe"] = definition.name
        active["classification_point_in_time"] = False
        rows.append(
            active[
                [
                    "date",
                    "universe",
                    "ts_code",
                    "industry",
                    "average_amount",
                    "list_status",
                    "classification_point_in_time",
                ]
            ]
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "date", "universe", "ts_code", "industry", "average_amount",
                "list_status", "classification_point_in_time",
            ]
        )
    return pd.concat(rows, ignore_index=True).sort_values(
        ["date", "ts_code"]
    ).reset_index(drop=True)


def _require_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")
