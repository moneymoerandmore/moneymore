from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PreprocessConfig:
    mad_clip: float = 5.0
    neutralize_size: bool = True
    industry_column: str | None = "industry"
    minimum_assets: int = 10


def preprocess_cross_section(
    factors: pd.DataFrame,
    factor_names: list[str],
    config: PreprocessConfig | None = None,
) -> pd.DataFrame:
    """Apply robust, date-local processing without using future observations."""
    config = config or PreprocessConfig()
    required = {"date", "symbol", *factor_names}
    missing = sorted(required - set(factors.columns))
    if missing:
        raise ValueError(f"cross-sectional factors missing columns: {missing}")
    frame = factors.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    for name in factor_names:
        frame[name] = frame.groupby("date", group_keys=False)[name].transform(
            lambda values: _mad_winsorize(values, config.mad_clip)
        )
        neutralized = frame[name].copy()
        for indices in frame.groupby("date").groups.values():
            group = frame.loc[indices]
            neutralized.loc[indices] = _neutralize(group, name, config)
        frame[name] = neutralized
        frame[name] = frame.groupby("date", group_keys=False)[name].transform(
            _rank_normalize
        )
    return frame


def _mad_winsorize(values: pd.Series, width: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() < 2:
        return numeric
    median = numeric.median()
    mad = (numeric - median).abs().median()
    if pd.isna(mad) or mad <= 0:
        return numeric
    robust_sigma = 1.4826 * mad
    return numeric.clip(median - width * robust_sigma, median + width * robust_sigma)


def _rank_normalize(values: pd.Series) -> pd.Series:
    valid = values.notna().sum()
    if valid < 2:
        return pd.Series(np.nan, index=values.index)
    percentile = values.rank(method="average", pct=True)
    return (percentile - 0.5) * 2.0


def _neutralize(
    group: pd.DataFrame, factor_name: str, config: PreprocessConfig
) -> pd.Series:
    result = group[factor_name].copy()
    controls: list[pd.Series] = []
    if config.neutralize_size and "total_mv" in group:
        size = pd.to_numeric(group["total_mv"], errors="coerce")
        controls.append(np.log(size.where(size > 0)).rename("log_size"))
    if config.industry_column and config.industry_column in group:
        industries = pd.get_dummies(
            group[config.industry_column], prefix="industry", dtype=float
        )
        controls.extend(industries[column] for column in industries.columns[:-1])
    if not controls:
        return result
    design = pd.concat(controls, axis=1)
    valid = result.notna() & design.notna().all(axis=1)
    parameter_count = design.shape[1] + 1
    if valid.sum() < max(config.minimum_assets, parameter_count + 2):
        return result
    matrix = np.column_stack(
        [np.ones(int(valid.sum())), design.loc[valid].to_numpy(dtype=float)]
    )
    target = result.loc[valid].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    result.loc[valid] = target - matrix @ coefficients
    return result
