from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..backtest import run_daily_backtest
from ..config import BacktestConfig
from ..data.research import load_point_in_time_features
from ..data.store import ParquetStore
from ..factors import PreprocessConfig, build_default_registry, preprocess_cross_section
from .bank_model import (
    apply_bank_targets,
    load_bank_backtest_bars,
    topk_dropout_targets,
)
from .bank_timing import (
    VolatilityTargetRiskDegree,
    apply_risk_degree,
    build_bank_market_index,
)
from .metrics import performance_metrics, slice_equity


@dataclass(frozen=True)
class SectorDefinition:
    sector_id: str
    name: str
    etf_code: str
    style: str
    symbols: tuple[str, ...]
    source_weights: dict[str, float]
    factor_weights: dict[str, float]
    top_k: int
    exit_rank: int
    max_replacements: int
    risk_target: float


def build_sector_factor_panel(
    store: ParquetStore,
    definition: SectorDefinition,
) -> pd.DataFrame:
    registry = build_default_registry()
    names = list(definition.factor_weights)
    panels = []
    for symbol in definition.symbols:
        features = load_point_in_time_features(store, symbol)
        computed = registry.compute(features, names)
        computed["date"] = pd.to_datetime(computed["date"])
        computed["month"] = computed["date"].dt.to_period("M")
        computed = (
            computed.sort_values("date")
            .groupby("month", as_index=False)
            .tail(1)
            .drop(columns="month")
        )
        panels.append(computed)
    factors = pd.concat(panels, ignore_index=True)
    for name in names:
        if registry.get(name).direction.value == "low_is_better":
            factors[name] = -factors[name]
    processed = preprocess_cross_section(
        factors,
        names,
        PreprocessConfig(industry_column=None, minimum_assets=5),
    )
    processed["score"] = 0.0
    processed["active_weight"] = 0.0
    for name, weight in definition.factor_weights.items():
        available = processed[name].notna()
        processed.loc[available, "score"] += processed.loc[available, name] * weight
        processed.loc[available, "active_weight"] += weight
    processed["score"] /= processed["active_weight"].replace(0, pd.NA)
    processed["sector"] = definition.sector_id
    processed["style"] = definition.style
    return processed


def research_sector(
    store: ParquetStore,
    definition: SectorDefinition,
    config: BacktestConfig,
    start_date: str = "2015-01-01",
    end_date: str = "2026-07-27",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    scores = build_sector_factor_panel(store, definition)
    targets = topk_dropout_targets(
        scores[["date", "symbol", "score"]],
        top_k=definition.top_k,
        exit_rank=definition.exit_rank,
        max_replacements=definition.max_replacements,
        position_weight=0.75 / definition.top_k,
    )
    targets["sector"] = definition.sector_id
    bars = load_bank_backtest_bars(
        store, list(definition.symbols), start_date, end_date
    )
    base = apply_bank_targets(bars, targets)
    market = build_bank_market_index(bars)
    timing = VolatilityTargetRiskDegree(
        annual_target=definition.risk_target,
        minimum_degree=0.15,
        maximum_degree=0.75,
        strategy_id=f"{definition.sector_id}_vol_target",
    )
    degrees = timing.risk_degrees(market)
    signals = apply_risk_degree(base, degrees, base_gross_exposure=0.75)
    result = run_daily_backtest(
        signals, config.model_copy(update={"max_drawdown": 0.99})
    )
    rows = []
    for period, start, end in (
        ("sample_in", "2015-01-01", "2021-12-31"),
        ("sample_out", "2022-01-01", end_date),
    ):
        try:
            metrics = performance_metrics(slice_equity(result.equity, start, end))
        except ValueError:
            continue
        rows.append(
            {
                "sector": definition.sector_id,
                "period": period,
                **metrics,
                "fills": len(result.fills),
                "evidence_status": "CURRENT_CONSTITUENT_BIASED",
            }
        )
    equity = result.equity.copy()
    equity["sector"] = definition.sector_id
    return pd.DataFrame(rows), scores, targets, equity.merge(
        degrees, on="date", how="left", validate="one_to_one"
    )


def inverse_volatility_allocation(
    returns: pd.DataFrame,
    maximum_weight: float = 0.35,
    minimum_weight: float = 0.10,
) -> dict[str, float]:
    volatility = returns.std(ddof=1) * np.sqrt(242)
    raw = 1 / volatility.replace(0, np.nan)
    weights = (raw / raw.sum()).fillna(0.0)
    for _ in range(10):
        weights = weights.clip(lower=minimum_weight, upper=maximum_weight)
        weights /= weights.sum()
        if bool(
            ((weights >= minimum_weight - 1e-9) & (weights <= maximum_weight + 1e-9)).all()
        ):
            break
    return {str(key): float(value) for key, value in weights.items()}
