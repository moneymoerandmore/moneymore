from pathlib import Path

from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.research.bank_model import (
    load_bank_backtest_bars,
    topk_dropout_targets,
)
from moneymore.research.bank_timing import research_bank_timing

ROOT = Path(__file__).resolve().parents[1]
store = ParquetStore(ROOT / "data")
scores = store.read("bank_model_scores").copy()
scores["score"] = (
    scores["value_score"] * 0.470588
    + scores["defensive_score"] * 0.294118
    + scores["momentum_score"] * 0.235294
)
targets = topk_dropout_targets(scores)
bars = load_bank_backtest_bars(
    store,
    sorted(targets["symbol"].unique()),
    "2015-04-30",
    "2026-07-27",
)
report, degrees, equity = research_bank_timing(
    bars, targets, BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")
)
store.merge_curated("bank_timing_report", [report], ["strategy", "period"])
store.merge_curated("bank_timing_degrees", [degrees], ["strategy", "date"])
store.merge_curated("bank_timing_equity", [equity], ["strategy", "date"])
print(report.to_string(index=False))
