from __future__ import annotations

import json
from pathlib import Path

from moneymore.exposure_league import build_skfolio_exposure_history
from train_finrl_exposure import proxy_returns

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    dates, returns = proxy_returns(ROOT, lookback=800)
    history = build_skfolio_exposure_history(list(zip(dates, returns, strict=True)))
    target = ROOT / "state" / "exposure-league" / "skfolio" / "history.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    print(f"wrote {len(history)} point-in-time rows to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
