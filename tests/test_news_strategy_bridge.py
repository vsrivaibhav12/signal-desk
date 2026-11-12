from datetime import date, datetime
from pathlib import Path

import yaml

from engine.clock import IST
from engine.strategies.desk import StrategyDesk


def test_structured_news_catalyst_is_attached_to_stock_watchlist(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    catalysts = [{
        "symbol": "IOC",
        "why": "News catalyst · Geopolitics / energy supply · mixed scenario: higher crude raises input costs",
        "event_id": "iran-energy-1",
        "impact_score": "88",
    }]
    desk = StrategyDesk(
        cfg, broker=object(), notify=lambda *_: None, folder=str(tmp_path), universe=["IOC"],
        master_markets=cfg["markets"], watch=None, catalysts=lambda: catalysts,
    )
    now = datetime(2026, 11, 12, 10, 0, tzinfo=IST)
    hot, rest, play = desk._plan(now, date(2026, 11, 12))
    symbol = "NSE:IOC-EQ"
    assert symbol in hot
    assert "higher crude raises input costs" in play[symbol]
    row = next(x for x in desk.state["watchlist"] if x["symbol"] == symbol)
    assert row["why"] == catalysts[0]["why"]
