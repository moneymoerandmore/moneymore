from __future__ import annotations

import pandas as pd

from ..data.research import load_point_in_time_features
from ..data.store import ParquetStore
from .base import FactorDirection, FactorRegistry


def compute_universe_factor_panel(
    store: ParquetStore,
    universe: str,
    registry: FactorRegistry,
    factor_names: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute factors only for members known at each rebalance date."""
    names = factor_names or list(registry.names())
    membership = store.read(
        "universe_membership", filters=[("universe", "==", universe)]
    ).copy()
    if membership.empty:
        raise ValueError(f"universe has no membership rows: {universe}")
    membership["date"] = pd.to_datetime(membership["date"])
    panels: list[pd.DataFrame] = []
    prices: list[pd.DataFrame] = []
    for symbol in sorted(membership["ts_code"].unique()):
        features = load_point_in_time_features(store, symbol)
        computed = registry.compute(features, names)
        metadata_columns = [
            column
            for column in ("date", "symbol", "total_mv", "industry")
            if column in features
        ]
        metadata = features[metadata_columns].copy()
        metadata["date"] = pd.to_datetime(metadata["date"])
        computed = computed.merge(
            metadata, on=["date", "symbol"], how="left", validate="one_to_one"
        )
        selected_dates = membership.loc[
            membership["ts_code"] == symbol, ["date", "industry"]
        ].drop_duplicates("date")
        selected = computed.merge(
            selected_dates,
            on="date",
            how="inner",
            suffixes=("", "_membership"),
            validate="one_to_one",
        )
        selected["universe"] = universe
        if "industry_membership" in selected:
            selected["industry"] = selected["industry_membership"]
            selected = selected.drop(columns=["industry_membership"])
        panels.append(selected)
        prices.append(
            features[["date", "symbol", "signal_close"]].copy()
        )
    panel = pd.concat(panels, ignore_index=True).sort_values(
        ["date", "symbol"]
    )
    for name in names:
        if registry.get(name).direction == FactorDirection.LOW:
            panel[name] = -panel[name]
    price_panel = pd.concat(prices, ignore_index=True).sort_values(
        ["symbol", "date"]
    )
    return panel.reset_index(drop=True), price_panel.reset_index(drop=True)
