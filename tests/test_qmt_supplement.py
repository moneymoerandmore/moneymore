import pandas as pd

from moneymore.data.qmt_supplement import normalize_qmt_supplement


def test_qmt_supplement_preserves_announcement_dates_and_aggregates_holders():
    payload = {
        "600036.SH": {
            "PershareIndex": pd.DataFrame(
                [
                    {
                        "m_timetag": "20260630",
                        "m_anntime": "20260829",
                        "du_return_on_equity": 5.84,
                        "gear_ratio": 90.18,
                    }
                ]
            ),
            "HolderNum": pd.DataFrame(
                [
                    {
                        "declareDate": "20260829",
                        "endDate": "20260630",
                        "shareholder": 734097,
                    }
                ]
            ),
            "Top10FlowHolder": pd.DataFrame(
                [
                    {"declareDate": "20260829", "endDate": "20260630", "ratio": 8.0},
                    {"declareDate": "20260829", "endDate": "20260630", "ratio": 7.0},
                ]
            ),
        }
    }
    result = normalize_qmt_supplement(payload)
    financial = result["qmt_financial_indicator"].iloc[0]
    assert financial["ann_date"] == "20260829"
    assert financial["qmt_roe"] == 5.84
    assert result["qmt_holder_count"].iloc[0]["shareholder_count"] == 734097
    assert result["qmt_top10_concentration"].iloc[0]["top10_ratio"] == 15.0
