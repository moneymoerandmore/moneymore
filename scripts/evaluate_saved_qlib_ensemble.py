from __future__ import annotations

import argparse
import json
import pickle
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import torch
import yaml

from moneymore.data.store import ParquetStore
from moneymore.portfolio_constructor import adjusted_close_panel, trailing_return_correlation
from moneymore.qlib_challenger import (
    QlibPanelDataset,
    build_challenger_dataset,
    challenger_universe,
    evaluate_predictions,
    metrics_payload,
    rank_blend_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
PARSER = argparse.ArgumentParser()
PARSER.add_argument("--candidate-tag")
ARGS = PARSER.parse_args()
CONFIG = yaml.safe_load(
    (ROOT / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
)
ARTIFACTS = ROOT / "state" / "qlib-challenger"
if ARGS.candidate_tag:
    ARTIFACTS = ARTIFACTS / "candidates" / str(ARGS.candidate_tag)
MODELS = ARTIFACTS / "models"
saved_research = ARTIFACTS / ("research.json" if ARGS.candidate_tag else "latest-research.json")
if saved_research.exists():
    CONFIG = json.loads(saved_research.read_text(encoding="utf-8")).get("protocol") or CONFIG

store = ParquetStore(ROOT / "data")
universe = challenger_universe(ROOT, store)
price_panel = adjusted_close_panel(store, list(universe))
frame = build_challenger_dataset(
    store,
    universe,
    sequence_length=int(CONFIG["sequence_length"]),
    label_horizon=int(CONFIG["label_horizon"]),
    feature_count=int(CONFIG["model"]["d_feat"]),
)
segments = {
    name: (values["start"], values["end"])
    for name, values in CONFIG.items()
    if name in {"train", "valid", "test", "forward"}
}
dataset = QlibPanelDataset(frame, segments)
test_dates = frame.loc[(slice(segments["test"][0], segments["test"][1]), slice(None)), :].index.get_level_values("datetime").unique()
evaluation_correlations = {
    pd.Timestamp(date): trailing_return_correlation(
        price_panel, date, int(CONFIG["portfolio_policy"]["correlation_lookback"])
    )
    for date in test_dates
}
base_model_id = str(CONFIG["model_id"])
manifest_path = MODELS / f"{base_model_id}_ensemble.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
seed_metrics = []
model_metrics = []
weighted_predictions = []
for item in manifest["models"]:
    filename = item["filename"] if isinstance(item, dict) else item
    weight = float(item.get("weight", 1.0)) if isinstance(item, dict) else 1.0
    with (MODELS / filename).open("rb") as handle:
        model = pickle.load(handle)
    predictions = model.predict(dataset, "test")
    weighted_predictions.append((predictions, weight))
    metric_payload = metrics_payload(
            evaluate_predictions(
                predictions,
                dataset.prepare("test", "label").iloc[:, 0],
                Path(filename).stem,
                "test",
                int(CONFIG["portfolio_policy"]["top_k"]),
                None,
                rebalance_interval=int(CONFIG["rebalance_interval"]),
                exit_rank_per_sector=int(CONFIG["portfolio_policy"]["exit_rank"]),
                max_replacements=int(CONFIG["portfolio_policy"]["max_replacements"]),
                minimum_weight=float(CONFIG["portfolio_policy"]["minimum_weight"]),
                maximum_weight=float(CONFIG["portfolio_policy"]["maximum_weight"]),
                price_panel=price_panel,
                correlation_lookback=int(CONFIG["portfolio_policy"]["correlation_lookback"]),
                correlation_penalty=float(CONFIG["portfolio_policy"]["correlation_penalty"]),
                cluster_correlation_threshold=float(CONFIG["portfolio_policy"]["cluster_correlation_threshold"]),
                maximum_cluster_members=int(CONFIG["portfolio_policy"]["maximum_cluster_members"]),
                weighting_method=str(CONFIG["portfolio_policy"].get("weighting_method", "rank_linear")),
                confidence_temperature=float(CONFIG["portfolio_policy"].get("confidence_temperature", 1.0)),
                confidence_shrinkage=float(CONFIG["portfolio_policy"].get("confidence_shrinkage", 0.35)),
                correlations=evaluation_correlations,
            )
        )
    if "_seed" in filename:
        seed_metrics.append(metric_payload)
    model_metrics.append(metric_payload)
ensemble_predictions = (
    rank_blend_predictions(weighted_predictions)
    if manifest.get("method") == "daily_rank_blend"
    else sum(series for series, _ in weighted_predictions) / len(weighted_predictions)
)
ensemble_metrics = metrics_payload(
    evaluate_predictions(
        ensemble_predictions,
        dataset.prepare("test", "label").iloc[:, 0],
        base_model_id,
        "test",
        int(CONFIG["portfolio_policy"]["top_k"]),
        None,
        rebalance_interval=int(CONFIG["rebalance_interval"]),
        exit_rank_per_sector=int(CONFIG["portfolio_policy"]["exit_rank"]),
        max_replacements=int(CONFIG["portfolio_policy"]["max_replacements"]),
        minimum_weight=float(CONFIG["portfolio_policy"]["minimum_weight"]),
        maximum_weight=float(CONFIG["portfolio_policy"]["maximum_weight"]),
        price_panel=price_panel,
        correlation_lookback=int(CONFIG["portfolio_policy"]["correlation_lookback"]),
        correlation_penalty=float(CONFIG["portfolio_policy"]["correlation_penalty"]),
        cluster_correlation_threshold=float(CONFIG["portfolio_policy"]["cluster_correlation_threshold"]),
        maximum_cluster_members=int(CONFIG["portfolio_policy"]["maximum_cluster_members"]),
        weighting_method=str(CONFIG["portfolio_policy"].get("weighting_method", "rank_linear")),
        confidence_temperature=float(CONFIG["portfolio_policy"].get("confidence_temperature", 1.0)),
        confidence_shrinkage=float(CONFIG["portfolio_policy"].get("confidence_shrinkage", 0.35)),
        correlations=evaluation_correlations,
    )
)
positive_seed_count = sum(float(row["rank_ic"]) > 0 for row in seed_metrics)
payload = {
    "created_at": datetime.now(UTC).isoformat(),
    "data_cutoff": str(frame.index.get_level_values("datetime").max().date()),
    "universe_size": len(universe),
    "samples": len(frame),
    "cuda_available": torch.cuda.is_available(),
    "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    "protocol": CONFIG,
    "metrics": [*model_metrics, ensemble_metrics],
    "stability": {
        "seed_count": len(seed_metrics),
        "positive_seed_count": positive_seed_count,
        "positive_seed_ratio": positive_seed_count / len(seed_metrics),
        "rank_ic_mean": sum(float(row["rank_ic"]) for row in seed_metrics)
        / len(seed_metrics),
        "rank_ic_min": min(float(row["rank_ic"]) for row in seed_metrics),
        "rank_ic_max": max(float(row["rank_ic"]) for row in seed_metrics),
    },
}
output_path = ARTIFACTS / ("research.json" if ARGS.candidate_tag else "latest-research.json")
output_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, ensure_ascii=False, indent=2))
