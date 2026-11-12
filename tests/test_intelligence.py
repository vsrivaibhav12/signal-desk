from datetime import datetime, timedelta
from email.utils import format_datetime

from engine.clock import IST
from engine.intelligence import NewsImpactDesk, equity_catalysts


def fixed_now():
    return datetime(2026, 11, 12, 19, 0, tzinfo=IST)


def rss(title, pub=None, desc=""):
    pub = pub or format_datetime(fixed_now().astimezone(__import__('datetime').timezone.utc))
    return f'''<?xml version="1.0"?><rss><channel><item><title>{title}</title><link>https://news.example.com/story</link><description>{desc}</description><source>Example News</source><pubDate>{pub}</pubDate></item></channel></rss>'''


def test_iran_escalation_maps_conditional_energy_and_india_equity_paths():
    html = rss("Iran war escalation threatens Strait of Hormuz tanker route", desc="Military escalation raises concern over oil exports.")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "iran", "query": "iran"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    state = desk.scan()
    assert len(state["events"]) == 1
    event = state["events"][0]
    impacts = {x["asset"]: x for x in event["impacts"]}
    assert impacts["CRUDE"]["bias"] == "bullish"
    assert impacts["GOLD"]["bias"] == "bullish"
    assert impacts["NATGAS"]["bias"] == "bullish"
    assert impacts["IOC"]["bias"] == "mixed"
    assert event["probability"] is None
    assert event["decision_status"] == "CONTEXT_ONLY"
    assert event["market_confirmation"] == "not_checked"
    assert "probability" in state["disclaimer"].lower()


def test_deescalation_has_opposite_conditional_energy_direction():
    html = rss("Iran and Israel agree ceasefire as talks resume", desc="A ceasefire agreement was announced.")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "iran", "query": "iran"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    event = desk.scan()["events"][0]
    impacts = {x["asset"]: x for x in event["impacts"]}
    assert impacts["CRUDE"]["bias"] == "bearish"
    assert impacts["GOLD"]["bias"] == "bearish"


def test_unclassified_story_does_not_invent_instrument_impact():
    html = rss("Local cricket team wins regional final")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "other", "query": "other"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    event = desk.scan()["events"][0]
    assert event["impacts"] == []
    assert event["overall_bias"] == "unknown"


def test_repeated_exact_headline_merges_sources_without_duplicate_event():
    html = rss("Iran oil tanker shipping route disrupted after attack")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "a", "query": "a"}, {"id": "b", "query": "b"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    state = desk.scan()
    assert len(state["events"]) == 1
    assert state["status"]["source_health"]["unique_headlines"] == 1
    assert state["events"][0]["evidence_count"] == 1


def test_only_fresh_high_relevance_equity_impacts_enter_nifty_universe_watchlist():
    now = fixed_now()
    state = {"events": [
        {"id": "fresh", "impact_score": 90, "published_at": (now - timedelta(hours=1)).isoformat(),
         "category": "Geopolitics / energy supply", "impacts": [{"asset": "IOC", "bias": "mixed", "why": "Input-cost channel"},
                                                                   {"asset": "CRUDE", "bias": "bullish", "why": "Supply risk"}]},
        {"id": "old", "impact_score": 99, "published_at": (now - timedelta(days=3)).isoformat(),
         "impacts": [{"asset": "ONGC", "bias": "bullish", "why": "Old story"}]},
        {"id": "low", "impact_score": 40, "published_at": now.isoformat(),
         "impacts": [{"asset": "BPCL", "bias": "mixed", "why": "Weak signal"}]},
    ]}
    out = equity_catalysts(state, ["IOC", "ONGC", "BPCL"], now=now)
    assert [x["symbol"] for x in out] == ["IOC"]
    assert "Input-cost channel" in out[0]["why"]
    assert "CRUDE" not in [x["symbol"] for x in out]


def test_company_specific_order_win_maps_only_the_named_company():
    html = rss("Tata Motors wins major export order from global automaker", desc="The company said it secured a long-term vehicle supply contract.")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "corporate", "query": "corporate"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    event = desk.scan()["events"][0]
    assert [(x["asset"], x["bias"]) for x in event["impacts"]] == [("TATAMOTORS", "bullish")]
    assert event["decision_status"] == "CONTEXT_ONLY"


def test_company_specific_regulatory_probe_maps_bearish_but_not_tradeable():
    html = rss("SEBI launches investigation into Hindustan Unilever accounting irregularities")
    desk = NewsImpactDesk({"news_intelligence": {"queries": [{"id": "corporate", "query": "corporate"}], "ai": {"enabled": False}}},
                          fetch=lambda url: html, now_fn=fixed_now)
    event = desk.scan()["events"][0]
    assert event["impacts"][0]["asset"] == "HINDUNILVR"
    assert event["impacts"][0]["bias"] == "bearish"
    assert event["probability"] is None
