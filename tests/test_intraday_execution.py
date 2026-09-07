from datetime import time

from moneymore.intraday_execution import decide_intraday_execution


def test_intraday_policy_avoids_open_noise_and_wide_spreads() -> None:
    opening = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40,
        open_price=40,
        vwap=40,
        bid1=39.99,
        ask1=40.01,
        now_time=time(9, 32),
    )
    wide = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40,
        open_price=40,
        vwap=40,
        bid1=39.9,
        ask1=40.1,
        now_time=time(10, 0),
    )
    assert opening.reason == "OPEN_NOISE_WINDOW"
    assert wide.reason == "SPREAD_TOO_WIDE"


def test_intraday_policy_snipes_favorable_vwap_and_has_deadline() -> None:
    favorable = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=39.9,
        open_price=40,
        vwap=40,
        bid1=39.89,
        ask1=39.91,
        now_time=time(10, 30),
    )
    deadline = decide_intraday_execution(
        symbol="600036.SH",
        side="BUY",
        price=40.5,
        open_price=40,
        vwap=40,
        bid1=40.49,
        ask1=40.51,
        now_time=time(14, 50),
    )
    assert favorable.action == "EXECUTE"
    assert favorable.reason == "VWAP_SNIPER_TRIGGER"
    assert deadline.action == "EXECUTE"
    assert deadline.reason == "DEADLINE_FALLBACK"
