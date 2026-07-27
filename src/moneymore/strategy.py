from __future__ import annotations

import pandas as pd


def moving_average_trend(
    bars: pd.DataFrame,
    fast: int = 20,
    slow: int = 60,
    price_column: str = "close",
) -> pd.DataFrame:
    """Return end-of-day target flags. Orders may execute no earlier than next open."""
    if fast <= 0 or slow <= 0 or fast >= slow:
        raise ValueError("moving-average windows require 0 < fast < slow")
    required = {"date", "symbol", price_column}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    frame = bars.sort_values(["symbol", "date"]).copy()
    grouped = frame.groupby("symbol", group_keys=False)[price_column]
    frame["fast_ma"] = grouped.transform(lambda values: values.rolling(fast).mean())
    frame["slow_ma"] = grouped.transform(lambda values: values.rolling(slow).mean())
    frame["target"] = (frame[price_column] > frame["slow_ma"]) & (
        frame["fast_ma"] > frame["slow_ma"]
    )
    return frame
