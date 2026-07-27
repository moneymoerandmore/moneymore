from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class BacktestConfig(BaseModel):
    initial_cash: float = Field(gt=0)
    commission_rate: float = Field(ge=0)
    minimum_commission: float = Field(ge=0)
    stamp_duty_rate: float = Field(ge=0)
    transfer_fee_rate: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    lot_size: int = Field(gt=0)
    max_position_weight: float = Field(gt=0, le=1)
    max_gross_exposure: float = Field(gt=0, le=1)
    max_drawdown: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def validate_position_limits(self) -> "BacktestConfig":
        if self.max_position_weight > self.max_gross_exposure:
            raise ValueError("max_position_weight cannot exceed max_gross_exposure")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BacktestConfig":
        with Path(path).open(encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))

