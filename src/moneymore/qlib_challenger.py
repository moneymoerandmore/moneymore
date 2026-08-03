from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .data.research import load_total_return_stock_bars
from .data.store import ParquetStore


@dataclass(frozen=True)
class ChallengerMetrics:
    model_id: str
    segment: str
    samples: int
    rank_ic: float
    rank_ic_ir: float
    top_k_excess_return: float
    average_turnover: float
    cost_adjusted_top_k_excess_return: float


class QlibPanelDataset:
    """Minimal DatasetH-compatible adapter backed by point-in-time local data."""

    def __init__(
        self, frame: pd.DataFrame, segments: dict[str, tuple[str, str]]
    ) -> None:
        self.frame = frame.sort_index()
        self.segments = {
            name: slice(pd.Timestamp(start), pd.Timestamp(end))
            for name, (start, end) in segments.items()
        }

    def prepare(
        self,
        segment: str | slice,
        col_set: str | list[str] | None = None,
        data_key: str | None = None,
    ) -> pd.DataFrame:
        del data_key
        selected = self.segments[segment] if isinstance(segment, str) else segment
        dates = self.frame.index.get_level_values("datetime")
        result = self.frame.loc[
            (dates >= selected.start) & (dates <= selected.stop)
        ]
        if col_set is None:
            return result
        return result.loc[:, col_set]


def build_challenger_dataset(
    store: ParquetStore,
    symbol_sectors: dict[str, str],
    sequence_length: int = 60,
    label_horizon: int = 5,
    require_label: bool = True,
    feature_count: int = 6,
) -> pd.DataFrame:
    panels = []
    for symbol, sector in sorted(symbol_sectors.items()):
        bars = load_total_return_stock_bars(store, symbol).sort_values("date").copy()
        close = bars["raw_close"].astype(float)
        previous = close.shift(1)
        feature_frame = pd.DataFrame(
            {
                "open_gap": bars["raw_open"].astype(float) / previous - 1,
                "high_gap": bars["raw_high"].astype(float) / previous - 1,
                "low_gap": bars["raw_low"].astype(float) / previous - 1,
                "close_return": close / previous - 1,
                "volume_change": np.log1p(bars["vol"].astype(float)).diff(),
                "amount_change": np.log1p(bars["amount"].astype(float)).diff(),
                "return_5": close.pct_change(5),
                "return_20": close.pct_change(20),
                "price_to_ma_20": close / close.rolling(20).mean() - 1,
                "price_to_ma_60": close / close.rolling(60).mean() - 1,
                "volatility_20": close.pct_change().rolling(20).std(),
                "drawdown_60": close / close.rolling(60).max() - 1,
            }
        ).replace([np.inf, -np.inf], np.nan)
        if feature_count not in {6, 12}:
            raise ValueError("feature_count must be 6 or 12")
        feature_frame = feature_frame.iloc[:, :feature_count]
        feature_columns: dict[str, pd.Series] = {}
        for feature in feature_frame:
            for lag in range(sequence_length - 1, -1, -1):
                feature_columns[f"{feature}_lag{lag:02d}"] = feature_frame[
                    feature
                ].shift(lag)
        future_return = close.shift(-label_horizon) / close - 1
        panel = pd.DataFrame(feature_columns)
        panel["raw_label"] = future_return
        panel["datetime"] = pd.to_datetime(bars["date"])
        panel["instrument"] = symbol
        panel["sector"] = sector
        panels.append(panel)
    combined = pd.concat(panels, ignore_index=True)
    combined["label"] = combined["raw_label"] - combined.groupby(
        ["datetime", "sector"]
    )["raw_label"].transform("mean")
    feature_names = [
        column
        for column in combined.columns
        if "_lag" in column
    ]
    required = [*feature_names, *(["label"] if require_label else [])]
    combined = combined.dropna(subset=required)
    combined = combined.set_index(["datetime", "instrument"]).sort_index()
    features = combined[feature_names].astype("float32")
    labels = combined[["label"]].astype("float32")
    features.columns = pd.MultiIndex.from_product([["feature"], feature_names])
    labels.columns = pd.MultiIndex.from_product([["label"], ["LABEL0"]])
    return pd.concat([features, labels], axis=1)


def evaluate_predictions(
    predictions: pd.Series,
    labels: pd.Series,
    model_id: str,
    segment: str,
    top_k: int,
    symbol_sectors: dict[str, str] | None = None,
    round_trip_cost_rate: float = 0.00106,
    rebalance_interval: int = 1,
    exit_rank_per_sector: int | None = None,
) -> ChallengerMetrics:
    aligned = pd.concat(
        [predictions.rename("score"), labels.rename("label")], axis=1
    ).dropna()
    daily_ic = aligned.groupby(level="datetime").apply(
        lambda frame: frame["score"].corr(frame["label"], method="spearman"),
        include_groups=False,
    ).dropna()
    selections: list[set[str]] = []
    top_returns: list[float] = []
    held: set[str] = set()
    for day_number, (_, day) in enumerate(aligned.groupby(level="datetime")):
        instruments = day.index.get_level_values("instrument").astype(str)
        working = day.assign(instrument=instruments)
        if day_number % rebalance_interval != 0 and held:
            selected = working.loc[working["instrument"].isin(held)]
        elif symbol_sectors:
            working["sector"] = working["instrument"].map(symbol_sectors)
            sector_count = max(int(working["sector"].nunique()), 1)
            per_sector = max(top_k // sector_count, 1)
            selected_parts = []
            for _, sector_frame in working.groupby("sector"):
                ranking = sector_frame.sort_values("score", ascending=False).copy()
                ranking["rank"] = range(1, len(ranking) + 1)
                retained = ranking.loc[
                    ranking["instrument"].isin(held)
                    & (
                        ranking["rank"]
                        <= int(exit_rank_per_sector or per_sector)
                    )
                ].head(per_sector)
                additions = ranking.loc[
                    ~ranking["instrument"].isin(set(retained["instrument"]))
                ].head(per_sector - len(retained))
                selected_parts.append(pd.concat([retained, additions]))
            selected = pd.concat(selected_parts)
        else:
            selected = working.nlargest(top_k, "score")
        held = set(selected["instrument"])
        selections.append(held)
        top_returns.append(float(selected["label"].mean()))
    turnovers = [
        len(current.symmetric_difference(previous))
        / max(len(current) + len(previous), 1)
        for previous, current in pairwise(selections)
    ]
    average_turnover = float(np.mean(turnovers)) if turnovers else 0.0
    top_k_excess_return = float(np.mean(top_returns)) if top_returns else 0.0
    return ChallengerMetrics(
        model_id=model_id,
        segment=segment,
        samples=len(aligned),
        rank_ic=float(daily_ic.mean()) if not daily_ic.empty else 0.0,
        rank_ic_ir=(
            float(daily_ic.mean() / daily_ic.std(ddof=1))
            if len(daily_ic) > 1 and daily_ic.std(ddof=1)
            else 0.0
        ),
        top_k_excess_return=top_k_excess_return,
        average_turnover=average_turnover,
        cost_adjusted_top_k_excess_return=(
            top_k_excess_return - average_turnover * round_trip_cost_rate
        ),
    )


def challenger_universe(root: Path, store: ParquetStore) -> dict[str, str]:
    config = yaml.safe_load(
        (root / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
    )
    result: dict[str, str] = {}
    for sector, item in config["universes"].items():
        for symbol in item["holdings"]:
            result.setdefault(symbol, sector)
    membership = store.read(
        "universe_membership",
        filters=[("universe", "==", "bank_cn")],
    )
    latest = membership.loc[
        pd.to_datetime(membership["date"])
        == pd.to_datetime(membership["date"]).max()
    ]
    result.update({str(symbol): "bank" for symbol in latest["ts_code"]})
    return result


def save_challenger_artifact(
    payload: dict[str, Any], target: Path
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"immutable challenger artifact differs: {target}")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return target


def metrics_payload(metrics: ChallengerMetrics) -> dict[str, object]:
    return asdict(metrics)


def evaluate_forward_observations(
    root: Path,
    store: ParquetStore,
    label_horizon: int,
) -> dict[str, object]:
    """Mature immutable daily predictions once their future label is observable."""
    report_dir = root / "state" / "qlib-challenger-shadow"
    daily_results: list[dict[str, object]] = []
    latest_reports: dict[str, dict[str, object]] = {}
    for report_path in sorted(report_dir.glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        trade_date = str(report.get("trade_date", ""))
        current = latest_reports.get(trade_date)
        if current is None or str(report.get("created_at", "")) > str(
            current.get("created_at", "")
        ):
            latest_reports[trade_date] = report
    for report in latest_reports.values():
        scores = report.get("scores", [])
        if not scores:
            continue
        observation_date = pd.Timestamp(str(report["trade_date"]))
        rows: list[dict[str, object]] = []
        mature = True
        for score in scores:
            symbol = str(score["instrument"])
            bars = load_total_return_stock_bars(store, symbol).sort_values("date")
            dates = pd.to_datetime(bars["date"])
            locations = np.flatnonzero(dates.to_numpy() == observation_date.to_datetime64())
            if not len(locations) or int(locations[-1]) + label_horizon >= len(bars):
                mature = False
                break
            start = int(locations[-1])
            future_return = (
                float(bars.iloc[start + label_horizon]["raw_close"])
                / float(bars.iloc[start]["raw_close"])
                - 1
            )
            rows.append(
                {
                    "instrument": symbol,
                    "sector": str(score["sector"]),
                    "score": float(score["score"]),
                    "raw_return": future_return,
                    "maturity_date": pd.Timestamp(
                        bars.iloc[start + label_horizon]["date"]
                    ),
                }
            )
        if not mature or not rows:
            continue
        frame = pd.DataFrame(rows)
        frame["label"] = frame["raw_return"] - frame.groupby("sector")[
            "raw_return"
        ].transform("mean")
        rank_ic = frame["score"].corr(frame["label"], method="spearman")
        selected = set(report.get("selected", []))
        selected_excess = frame.loc[
            frame["instrument"].isin(selected), "label"
        ].mean()
        daily_results.append(
            {
                "observation_date": str(report["trade_date"]),
                "maturity_date": str(
                    max(row["maturity_date"] for row in rows).date()
                ),
                "rank_ic": 0.0 if pd.isna(rank_ic) else float(rank_ic),
                "selected_excess_return": (
                    0.0 if pd.isna(selected_excess) else float(selected_excess)
                ),
                "sample_count": len(frame),
            }
        )
    rank_ics = pd.Series([row["rank_ic"] for row in daily_results], dtype=float)
    payload = {
        "updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "matured_days": len(daily_results),
        "rank_ic": float(rank_ics.mean()) if len(rank_ics) else None,
        "rank_ic_ir": (
            float(rank_ics.mean() / rank_ics.std(ddof=1))
            if len(rank_ics) > 1 and rank_ics.std(ddof=1)
            else None
        ),
        "daily": daily_results,
    }
    target = root / "state" / "qlib-challenger" / "forward-evaluation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return payload
