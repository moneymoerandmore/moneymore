import json
from pathlib import Path

import pandas as pd
import yaml

from moneymore.config import BacktestConfig
from moneymore.data.store import ParquetStore
from moneymore.research.sector_model import (
    SectorDefinition,
    inverse_volatility_allocation,
    research_sector,
)

ROOT = Path(__file__).resolve().parents[1]
STORE = ParquetStore(ROOT / "data")
CONFIG = yaml.safe_load(
    (ROOT / "configs" / "sector_models.yaml").read_text(encoding="utf-8")
)
BACKTEST = BacktestConfig.from_yaml(ROOT / "configs" / "default.yaml")

reports = []
scores = []
targets = []
equities = []
for sector_id, item in CONFIG["universes"].items():
    definition = SectorDefinition(
        sector_id=sector_id,
        name=item["name"],
        etf_code=item["etf_code"],
        style=item["style"],
        symbols=tuple(item["holdings"]),
        source_weights=item["holdings"],
        factor_weights=item["factors"],
        top_k=item["top_k"],
        exit_rank=item["exit_rank"],
        max_replacements=item["max_replacements"],
        risk_target=item["risk_target"],
    )
    report, score, target, equity = research_sector(STORE, definition, BACKTEST)
    reports.append(report)
    scores.append(score)
    targets.append(target)
    equities.append(equity)

report_frame = pd.concat(reports, ignore_index=True)
score_frame = pd.concat(scores, ignore_index=True)
target_frame = pd.concat(targets, ignore_index=True)
equity_frame = pd.concat(equities, ignore_index=True)
bank_equity = STORE.read("bank_timing_equity")
bank_equity = bank_equity.loc[bank_equity["strategy"] == "vol_target_12"].copy()
bank_degrees = STORE.read("bank_timing_degrees")
bank_degrees = bank_degrees.loc[
    bank_degrees["strategy"] == "vol_target_12", ["date", "risk_degree"]
]
bank_equity = bank_equity.merge(
    bank_degrees, on="date", how="left", validate="one_to_one"
)
bank_equity["sector"] = "bank"
equity_frame = pd.concat(
    [
        equity_frame,
        bank_equity[["date", "equity", "drawdown", "sector", "risk_degree"]],
    ],
    ignore_index=True,
)
return_panel = (
    equity_frame.pivot(index="date", columns="sector", values="equity")
    .pct_change(fill_method=None)
    .tail(60)
)
allocation = inverse_volatility_allocation(
    return_panel,
    CONFIG["allocation"]["max_sleeve_weight"],
    CONFIG["allocation"]["minimum_sleeve_weight"],
)
latest_degrees = (
    equity_frame.sort_values("date").groupby("sector", as_index=False).tail(1)
)
recommendations = []
for sector, budget in allocation.items():
    row = latest_degrees.loc[latest_degrees["sector"] == sector].iloc[0]
    if sector == "bank":
        reports = sorted((ROOT / "state" / "bank-shadow").glob("*.json"))
        selected = (
            json.loads(reports[-1].read_text(encoding="utf-8"))["holdings"]
            if reports
            else []
        )
    else:
        latest_target_date = target_frame.loc[
            target_frame["sector"] == sector, "date"
        ].max()
        selected = target_frame.loc[
            (target_frame["sector"] == sector)
            & (target_frame["date"] == latest_target_date)
            & target_frame["selected"],
            "symbol",
        ].tolist()
    recommendations.append(
        {
            "sector": sector,
            "budget_weight": budget,
            "risk_degree": float(row["risk_degree"]),
            "target_weight": budget * float(row["risk_degree"]),
            "cash_weight": budget * (1 - float(row["risk_degree"])),
            "selected": ",".join(selected),
            "evidence_status": "CURRENT_CONSTITUENT_BIASED",
        }
    )

STORE.merge_curated("sector_model_report", [report_frame], ["sector", "period"])
STORE.merge_curated("sector_model_scores", [score_frame], ["sector", "date", "symbol"])
STORE.merge_curated("sector_model_targets", [target_frame], ["sector", "date", "symbol"])
STORE.merge_curated("sector_model_equity", [equity_frame], ["sector", "date"])
STORE.merge_curated(
    "sector_portfolio_recommendation",
    [pd.DataFrame(recommendations)],
    ["sector"],
)
print(report_frame.to_string(index=False))
print()
print(pd.DataFrame(recommendations).to_string(index=False))
