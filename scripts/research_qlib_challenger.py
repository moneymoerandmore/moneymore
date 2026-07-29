from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime
from pathlib import Path

import qlib
import torch
import yaml
from qlib.config import REG_CN
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.model.pytorch_gru import GRU
from qlib.workflow import R

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
MODELS.mkdir(parents=True, exist_ok=True)

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
provider_dir = ARTIFACTS / "qlib-provider"
provider_dir.mkdir(parents=True, exist_ok=True)
tracking_db = (ARTIFACTS / "mlflow.db").resolve().as_posix()
qlib.init(
    provider_uri=str(provider_dir),
    region=REG_CN,
    exp_manager={
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {
            "uri": f"sqlite:///{tracking_db}",
            "default_exp_name": "moneymore_qlib_challenger",
        },
    },
)

def build_gru(seed: int) -> GRU:
    return GRU(
        d_feat=int(CONFIG["model"]["d_feat"]),
        hidden_size=int(CONFIG["model"]["hidden_size"]),
        num_layers=int(CONFIG["model"]["num_layers"]),
        dropout=float(CONFIG["model"]["dropout"]),
        n_epochs=int(CONFIG["model"]["n_epochs"]),
        lr=float(CONFIG["model"]["learning_rate"]),
        batch_size=int(CONFIG["model"]["batch_size"]),
        early_stop=int(CONFIG["model"]["early_stop"]),
        GPU=0 if torch.cuda.is_available() else -1,
        seed=seed,
    )


def fit_and_evaluate(model_id: str, model: object) -> tuple[dict[str, object], object]:
    with R.start(experiment_name="moneymore_qlib_challenger"):
        save_path = MODELS / f"{model_id}.bin"
        if isinstance(model, GRU):
            model.fit(dataset, save_path=str(save_path))
        else:
            model.fit(dataset)
        predictions = model.predict(dataset, "test")
        labels = dataset.prepare("test", "label").iloc[:, 0]
        result = evaluate_predictions(
            predictions,
            labels,
            model_id,
            "test",
            int(CONFIG["top_k_per_sector"]) * 5,
            universe,
            rebalance_interval=int(CONFIG["rebalance_interval"]),
            exit_rank_per_sector=int(CONFIG["exit_rank_per_sector"]),
        )
        with (MODELS / f"{model_id}.pkl").open("wb") as handle:
            pickle.dump(model, handle)
    return metrics_payload(result), predictions


metrics = []
lightgbm = LGBModel(
    loss="mse",
    num_leaves=64,
    learning_rate=0.03,
    n_estimators=500,
    colsample_bytree=0.8,
    subsample=0.8,
    reg_lambda=1.0,
    n_jobs=-1,
)
lightgbm_metrics, _ = fit_and_evaluate(
    "qlib_lightgbm_alpha360_v1",
    lightgbm,
)
metrics.append(lightgbm_metrics)

base_model_id = str(CONFIG["model_id"])
seed_metrics = []
seed_predictions = []
deployment_models = []
for seed in CONFIG["model"]["seeds"]:
    seed_model_id = f"{base_model_id}_seed{int(seed)}"
    result, predictions = fit_and_evaluate(
        seed_model_id,
        build_gru(int(seed)),
    )
    metrics.append(result)
    seed_metrics.append(result)
    seed_predictions.append(predictions.rename(str(seed)))
    deployment_models.append(f"{seed_model_id}.pkl")

ensemble_predictions = sum(seed_predictions) / len(seed_predictions)
labels = dataset.prepare("test", "label").iloc[:, 0]
ensemble_metrics = metrics_payload(
    evaluate_predictions(
        ensemble_predictions,
        labels,
        base_model_id,
        "test",
        int(CONFIG["top_k_per_sector"]) * 5,
        universe,
        rebalance_interval=int(CONFIG["rebalance_interval"]),
        exit_rank_per_sector=int(CONFIG["exit_rank_per_sector"]),
    )
)
metrics.append(ensemble_metrics)
positive_seed_count = sum(float(row["rank_ic"]) > 0 for row in seed_metrics)
stability = {
    "seed_count": len(seed_metrics),
    "positive_seed_count": positive_seed_count,
    "positive_seed_ratio": positive_seed_count / len(seed_metrics),
    "rank_ic_mean": sum(float(row["rank_ic"]) for row in seed_metrics)
    / len(seed_metrics),
    "rank_ic_min": min(float(row["rank_ic"]) for row in seed_metrics),
    "rank_ic_max": max(float(row["rank_ic"]) for row in seed_metrics),
}
(MODELS / f"{base_model_id}_ensemble.json").write_text(
    json.dumps({"models": deployment_models}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

payload = {
    "created_at": datetime.now(UTC).isoformat(),
    "data_cutoff": str(frame.index.get_level_values("datetime").max().date()),
    "universe_size": len(universe),
    "samples": len(frame),
    "cuda_available": torch.cuda.is_available(),
    "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    "protocol": CONFIG,
    "metrics": metrics,
    "stability": stability,
}
(ARTIFACTS / "latest-research.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, ensure_ascii=False, indent=2))
