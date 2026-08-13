from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import qlib
import torch
import yaml
from qlib.config import REG_CN
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.model.pytorch_gru import GRU
from qlib.workflow import R

from moneymore.data.store import ParquetStore
from moneymore.portfolio_constructor import adjusted_close_panel, trailing_return_correlation
from moneymore.qlib_challenger import (
    QlibPanelDataset,
    build_challenger_dataset,
    challenger_universe,
    evaluate_predictions,
    metrics_payload,
)
from moneymore.qlib_governance import register_qlib_candidate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load(
    (ROOT / "configs" / "qlib_challenger.yaml").read_text(encoding="utf-8")
)
ARTIFACTS = ROOT / "state" / "qlib-challenger"
CANDIDATE_TAG = os.environ.get("MONEYMORE_CANDIDATE_TAG", "").strip()
MODELS = (
    ARTIFACTS / "candidates" / CANDIDATE_TAG / "models"
    if CANDIDATE_TAG
    else ARTIFACTS / "models"
)
MODELS.mkdir(parents=True, exist_ok=True)

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
if CANDIDATE_TAG:
    dates = sorted(frame.index.get_level_values("datetime").unique())
    horizon = int(CONFIG["label_horizon"])
    matured_dates = dates[:-horizon] if len(dates) > horizon else []
    if len(matured_dates) < 504:
        raise RuntimeError("weekly training requires at least 504 matured trading days")
    test_dates = matured_dates[-126:]
    valid_dates = matured_dates[-252:-126]
    train_dates = matured_dates[:-252]
    segments = {
        "train": (str(train_dates[0].date()), str(train_dates[-1].date())),
        "valid": (str(valid_dates[0].date()), str(valid_dates[-1].date())),
        "test": (str(test_dates[0].date()), str(test_dates[-1].date())),
        "forward": (str(dates[-horizon].date()), "2099-12-31"),
    }
dataset = QlibPanelDataset(frame, segments)
test_dates = frame.loc[(slice(segments["test"][0], segments["test"][1]), slice(None)), :].index.get_level_values("datetime").unique()
evaluation_correlations = {
    pd.Timestamp(date): trailing_return_correlation(
        price_panel, date, int(CONFIG["portfolio_policy"]["correlation_lookback"])
    )
    for date in test_dates
}
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
            correlations=evaluation_correlations,
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
        correlations=evaluation_correlations,
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
ensemble_artifact = MODELS / f"{base_model_id}_ensemble.json"
ensemble_artifact.write_text(
    json.dumps({"models": deployment_models}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

payload = {
    "created_at": datetime.now(UTC).isoformat(),
    "data_cutoff": (
        str(segments["test"][1])
        if CANDIDATE_TAG
        else str(frame.index.get_level_values("datetime").max().date())
    ),
    "universe_size": len(universe),
    "samples": len(frame),
    "cuda_available": torch.cuda.is_available(),
    "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    "protocol": CONFIG,
    "effective_segments": segments,
    "candidate_tag": CANDIDATE_TAG or None,
    "metrics": metrics,
    "stability": stability,
}
payload["candidate_release"] = register_qlib_candidate(
    ROOT,
    model_id=base_model_id,
    artifact_path=ensemble_artifact,
    data_cutoff=str(payload["data_cutoff"]),
)
research_path = (
    ARTIFACTS / "candidates" / CANDIDATE_TAG / "research.json"
    if CANDIDATE_TAG
    else ARTIFACTS / "latest-research.json"
)
research_path.parent.mkdir(parents=True, exist_ok=True)
research_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
if CANDIDATE_TAG:
    (research_path.parent / "_RESEARCH_COMPLETE").write_text(
        f"completed_at={datetime.now(UTC).isoformat()}\n",
        encoding="utf-8",
    )
print(json.dumps(payload, ensure_ascii=False, indent=2))

# On the Windows CUDA runtime used by the local service, Python's native-library
# teardown can abort in ucrtbase.dll with 0xc0000409 after every artifact has
# already been durably written.  Candidate research runs are isolated child
# processes, so flush their output and avoid that unsafe native teardown path.
if CANDIDATE_TAG and os.name == "nt":
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
