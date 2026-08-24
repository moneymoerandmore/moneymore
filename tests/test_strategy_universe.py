import pandas as pd

from moneymore.strategy_universe import build_market_cap_universe


def test_market_cap_universe_filters_boards_and_ranks() -> None:
    instruments = pd.DataFrame(
        [
            {"ts_code": "600000.SH", "name": "沪主", "industry": "银行", "list_status": "L", "list_date": "20000101"},
            {"ts_code": "000001.SZ", "name": "深主", "industry": "银行", "list_status": "L", "list_date": "20000101"},
            {"ts_code": "300001.SZ", "name": "创业", "industry": "软件", "list_status": "L", "list_date": "20000101"},
            {"ts_code": "688001.SH", "name": "科创", "industry": "半导体", "list_status": "L", "list_date": "20000101"},
            {"ts_code": "920001.BJ", "name": "北交", "industry": "软件", "list_status": "L", "list_date": "20000101"},
            {"ts_code": "600002.SH", "name": "ST风险", "industry": "其他", "list_status": "L", "list_date": "20000101"},
        ]
    )
    basics = pd.DataFrame(
        [
            {"ts_code": row.ts_code, "trade_date": "20260824", "close": 1.0,
             "pb": 1.0, "dv_ttm": 0.0, "total_mv": 1000 - index,
             "circ_mv": 500.0}
            for index, row in enumerate(instruments.itertuples())
        ]
    )
    result = build_market_cap_universe(instruments, basics, "20260824", size=4)
    assert result["symbol"].tolist() == [
        "600000.SH", "000001.SZ", "300001.SZ", "688001.SH"
    ]
    assert result["board"].tolist() == ["沪市主板", "深市主板", "创业板", "科创板"]
    assert result["rank"].tolist() == [1, 2, 3, 4]
