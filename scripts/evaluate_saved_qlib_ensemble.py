from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime
from pathlib import Path

import torch
import yaml

from moneymore.data.store import ParquetStore
from moneymore.qlib_challenger import (
    QlibPanelDataset,
    build_challenger_dataset,
    challenger_universe,
    evaluate_predictions,
    metrics_payload,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load(
    (ROOT / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
)
ARTIFACTS = ROOT / "state" / "qlib-challenger"
MODELS = ARTIFACTS / "models"

store = ParquetStore(ROOT / "data")
universe = challenger_universe(ROOT, store)
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
base_model_id = str(CONFIG["model_id"])
manifest_path = MODELS / f"{base_model_id}_ensemble.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
seed_metrics = []
seed_predictions = []
for filename in manifest["models"]:
    with (MODELS / filename).open("rb") as handle:
        model = pickle.load(handle)
    predictions = model.predict(dataset, "test")
    seed_predictions.append(predictions)
    seed_metrics.append(
        metrics_payload(
            evaluate_predictions(
                predictions,
                dataset.prepare("test", "label").iloc[:, 0],
                Path(filename).stem,
                "test",
                int(CONFIG["top_k_per_sector"]) * 5,
                universe,
                rebalance_interval=int(CONFIG["rebalance_interval"]),
                exit_rank_per_sector=int(CONFIG["exit_rank_per_sector"]),
            )
        )
    )
ensemble_predictions = sum(seed_predictions) / len(seed_predictions)
ensemble_metrics = metrics_payload(
    evaluate_predictions(
        ensemble_predictions,
        dataset.prepare("test", "label").iloc[:, 0],
        base_model_id,
        "test",
        int(CONFIG["top_k_per_sector"]) * 5,
        universe,
        rebalance_interval=int(CONFIG["rebalance_interval"]),
        exit_rank_per_sector=int(CONFIG["exit_rank_per_sector"]),
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
    "metrics": [*seed_metrics, ensemble_metrics],
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
(ARTIFACTS / "latest-research.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, ensure_ascii=False, indent=2))
