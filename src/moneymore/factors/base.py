from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

FactorCalculator = Callable[[pd.DataFrame], pd.Series]


class FactorCategory(str, Enum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    LIQUIDITY = "liquidity"
    VALUE = "value"
    QUALITY = "quality"
    GROWTH = "growth"


class FactorDirection(str, Enum):
    HIGH = "high_is_better"
    LOW = "low_is_better"


class Availability(str, Enum):
    MARKET_T_PLUS_1 = "market_t_plus_1"
    ANNOUNCEMENT_T_PLUS_1 = "announcement_t_plus_1"


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    version: int
    category: FactorCategory
    direction: FactorDirection
    description: str
    inputs: tuple[str, ...]
    lookback: int
    availability: Availability
    calculate: FactorCalculator
    reference: str = ""

    @property
    def identity(self) -> str:
        return f"{self.name}@v{self.version}"

    def catalog_record(self) -> dict[str, object]:
        return {
            "name": self.name,
            "identity": self.identity,
            "version": self.version,
            "category": self.category.value,
            "direction": self.direction.value,
            "description": self.description,
            "inputs": list(self.inputs),
            "lookback": self.lookback,
            "availability": self.availability.value,
            "reference": self.reference,
        }


class FactorRegistry:
    """Versioned, auditable factor definitions with deterministic computation."""

    def __init__(self) -> None:
        self._definitions: dict[str, FactorDefinition] = {}

    def register(self, definition: FactorDefinition) -> None:
        if definition.name in self._definitions:
            existing = self._definitions[definition.name]
            raise ValueError(
                f"factor {definition.name!r} is already registered as "
                f"{existing.identity}"
            )
        if definition.version < 1:
            raise ValueError("factor version must be positive")
        if definition.lookback < 1:
            raise ValueError("factor lookback must be positive")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> FactorDefinition:
        try:
            return self._definitions[name]
        except KeyError as error:
            raise KeyError(f"unknown factor: {name}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def catalog(self) -> list[dict[str, object]]:
        return [
            definition.catalog_record()
            for definition in self._definitions.values()
        ]

    def compute(
        self, frame: pd.DataFrame, names: Iterable[str] | None = None
    ) -> pd.DataFrame:
        requested = list(names) if names is not None else list(self.names())
        unknown = sorted(set(requested) - set(self._definitions))
        if unknown:
            raise KeyError(f"unknown factors: {unknown}")
        required_keys = {"date", "symbol"}
        missing_keys = required_keys - set(frame.columns)
        if missing_keys:
            raise ValueError(f"factor input missing keys: {sorted(missing_keys)}")

        ordered = frame.copy()
        ordered["date"] = pd.to_datetime(ordered["date"])
        ordered = ordered.sort_values(["symbol", "date"]).reset_index(drop=True)
        output = ordered[["date", "symbol"]].copy()
        for name in requested:
            definition = self.get(name)
            missing = sorted(set(definition.inputs) - set(ordered.columns))
            if missing:
                raise ValueError(f"factor {name} missing inputs: {missing}")
            values = definition.calculate(ordered)
            if len(values) != len(ordered):
                raise ValueError(
                    f"factor {name} returned {len(values)} rows for "
                    f"{len(ordered)} inputs"
                )
            output[name] = pd.to_numeric(values, errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
        return output
