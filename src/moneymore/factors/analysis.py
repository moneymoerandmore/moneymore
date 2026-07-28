from __future__ import annotations

import math

import pandas as pd


def attach_forward_returns(
    factors: pd.DataFrame,
    prices: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 20),
    price_column: str = "signal_close",
) -> pd.DataFrame:
    """Attach future returns for research labels only, never for live features."""
    required = {"date", "symbol", price_column}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"price data missing columns: {missing}")
    ordered = prices[["date", "symbol", price_column]].copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    ordered = ordered.sort_values(["symbol", "date"])
    grouped = ordered.groupby("symbol", sort=False)[price_column]
    for horizon in horizons:
        if horizon < 1:
            raise ValueError("forward-return horizons must be positive")
        ordered[f"forward_return_{horizon}"] = (
            grouped.shift(-horizon) / ordered[price_column] - 1
        )
    base = factors.copy()
    base["date"] = pd.to_datetime(base["date"])
    return base.merge(
        ordered.drop(columns=[price_column]),
        on=["date", "symbol"],
        how="left",
        validate="one_to_one",
    )


def factor_ic_report(
    labeled: pd.DataFrame,
    factor_names: list[str],
    horizons: tuple[int, ...] = (1, 5, 20),
    minimum_assets: int = 10,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for factor in factor_names:
        for horizon in horizons:
            target = f"forward_return_{horizon}"
            _require_columns(labeled, {factor, target})
            daily = labeled.groupby("date").apply(
                lambda group, factor=factor, target=target: _rank_ic(
                    group[factor], group[target], minimum_assets
                ),
                include_groups=False,
            ).dropna()
            mean_ic = float(daily.mean()) if not daily.empty else math.nan
            std_ic = float(daily.std(ddof=1)) if len(daily) > 1 else math.nan
            rows.append(
                {
                    "factor": factor,
                    "horizon": horizon,
                    "observations": len(daily),
                    "coverage": float(labeled[[factor, target]].notna().all(axis=1).mean()),
                    "mean_rank_ic": mean_ic,
                    "rank_ic_std": std_ic,
                    "rank_icir": mean_ic / std_ic if std_ic > 0 else math.nan,
                    "positive_rate": float((daily > 0).mean()) if not daily.empty else math.nan,
                }
            )
    return pd.DataFrame(rows)


def factor_ic_period_report(
    labeled: pd.DataFrame,
    factor_names: list[str],
    periods: dict[str, tuple[str, str]],
    horizons: tuple[int, ...] = (1, 5, 20),
    minimum_assets: int = 10,
) -> pd.DataFrame:
    dates = pd.to_datetime(labeled["date"])
    reports = []
    for period, (start_date, end_date) in periods.items():
        selected = labeled.loc[
            (dates >= pd.Timestamp(start_date))
            & (dates <= pd.Timestamp(end_date))
        ]
        report = factor_ic_report(
            selected, factor_names, horizons, minimum_assets
        )
        report.insert(0, "period", period)
        reports.append(report)
    return pd.concat(reports, ignore_index=True)


def quantile_return_report(
    labeled: pd.DataFrame,
    factor_names: list[str],
    horizon: int = 20,
    quantiles: int = 5,
    minimum_assets: int = 10,
) -> pd.DataFrame:
    target = f"forward_return_{horizon}"
    rows: list[dict[str, object]] = []
    for factor in factor_names:
        _require_columns(labeled, {factor, target})
        samples: list[pd.DataFrame] = []
        for _, group in labeled[["date", factor, target]].dropna().groupby("date"):
            if len(group) < max(minimum_assets, quantiles):
                continue
            ranked = group[factor].rank(method="first")
            bucket = pd.qcut(ranked, quantiles, labels=False) + 1
            sample = group.assign(quantile=bucket)
            samples.append(sample)
        if not samples:
            continue
        combined = pd.concat(samples, ignore_index=True)
        means = combined.groupby("quantile")[target].mean()
        for quantile, value in means.items():
            rows.append(
                {
                    "factor": factor,
                    "horizon": horizon,
                    "quantile": str(int(quantile)),
                    "mean_forward_return": float(value),
                    "samples": int((combined["quantile"] == quantile).sum()),
                }
            )
        rows.append(
            {
                "factor": factor,
                "horizon": horizon,
                "quantile": "top_minus_bottom",
                "mean_forward_return": float(means.iloc[-1] - means.iloc[0]),
                "samples": len(combined),
            }
        )
    return pd.DataFrame(rows)


def factor_correlation(
    factors: pd.DataFrame, factor_names: list[str], minimum_assets: int = 10
) -> pd.DataFrame:
    correlations = []
    for _, group in factors.groupby("date"):
        if len(group) < minimum_assets:
            continue
        correlations.append(group[factor_names].corr(method="spearman"))
    if not correlations:
        return pd.DataFrame(index=factor_names, columns=factor_names, dtype=float)
    return sum(correlations) / len(correlations)


def _rank_ic(
    factor: pd.Series, target: pd.Series, minimum_assets: int
) -> float:
    valid = factor.notna() & target.notna()
    if valid.sum() < minimum_assets:
        return math.nan
    left = factor.loc[valid].rank(method="average")
    right = target.loc[valid].rank(method="average")
    if left.nunique() < 2 or right.nunique() < 2:
        return math.nan
    return float(left.corr(right))


def _require_columns(frame: pd.DataFrame, required: set[str]) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"factor analysis missing columns: {missing}")
