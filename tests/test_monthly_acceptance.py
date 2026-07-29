import json
from datetime import date, timedelta
from pathlib import Path

from moneymore.monthly_acceptance import (
    evaluate_monthly_cycle,
    freeze_passed_report,
)


def _daily_rows(count: int) -> list[dict[str, object]]:
    start = date(2026, 7, 27)
    return [
        {
            "trade_date": (start + timedelta(days=index)).strftime("%Y%m%d"),
            "gross_exposure": 0.4,
            "reconciled": True,
        }
        for index in range(count)
    ]


def test_complete_monthly_cycle_passes_and_freezes_report(tmp_path: Path):
    result = evaluate_monthly_cycle(
        account_daily=_daily_rows(20),
        fills=[
            {"trade_date": "20260727", "side": "BUY", "symbol": "A"},
            {"trade_date": "20260805", "side": "SELL", "symbol": "A"},
            {"trade_date": "20260805", "side": "BUY", "symbol": "B"},
        ],
        model_versions=[
            {
                "data_cutoff": "20260727",
                "config_hash": "config-v1",
                "code_hash": "code-v1",
            },
            {
                "data_cutoff": "20260805",
                "config_hash": "config-v1",
                "code_hash": "code-v1",
            },
        ],
        start_date="20260727",
        observation_date="20260815",
    )

    assert result.status == "PASSED"
    assert result.progress == 1.0
    frozen = freeze_passed_report(result, tmp_path / "reports")
    repeated = freeze_passed_report(result, tmp_path / "reports")
    assert frozen.report_path == repeated.report_path
    payload = json.loads(Path(frozen.report_path).read_text(encoding="utf-8"))
    assert payload["status"] == "PASSED"


def test_incomplete_cycle_remains_collecting_without_report(tmp_path: Path):
    result = evaluate_monthly_cycle(
        account_daily=_daily_rows(2),
        fills=[],
        model_versions=[],
        start_date="20260727",
        observation_date="20260728",
    )

    assert result.status == "COLLECTING_EVIDENCE"
    assert result.progress < 1
    assert freeze_passed_report(result, tmp_path / "reports").report_path is None
    checks = {row["code"]: row for row in result.checks}
    assert checks["MINIMUM_TRADING_DAYS"]["passed"] is False
    assert checks["MODEL_FROZEN"]["passed"] is False
