"""Always-on headline intake and market-impact intelligence.

This worker is deliberately separate from exchange-session scanners. It can run
24/7, including weekends and exchange holidays. It never creates trade signals
or places orders. It publishes conditional impact hypotheses for later market
confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import signal
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from .clock import IST

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "_data"
STATE_FILE = DATA_DIR / "intelligence.json"
GOOGLE_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
UA = {"User-Agent": "SignalDesk/4.1 (+market-intelligence; RSS reader)"}
STOP = False

# A controlled instrument catalogue prevents a language model from inventing
# symbols. Directions are conditional hypotheses, not recommendations.
ASSETS = {
    "CRUDE": {"label": "MCX Crude Oil", "kind": "commodity"},
    "NATGAS": {"label": "MCX Natural Gas", "kind": "commodity"},
    "GOLD": {"label": "MCX Gold", "kind": "commodity"},
    "SILVER": {"label": "MCX Silver", "kind": "commodity"},
    "NIFTY": {"label": "Nifty 50", "kind": "index"},
    "BANKNIFTY": {"label": "Bank Nifty", "kind": "index"},
    "ONGC": {"label": "ONGC", "kind": "equity"},
    "OIL": {"label": "Oil India", "kind": "equity"},
    "IOC": {"label": "Indian Oil", "kind": "equity"},
    "BPCL": {"label": "BPCL", "kind": "equity"},
    "HPCL": {"label": "HPCL", "kind": "equity"},
    "INDIGO": {"label": "InterGlobe Aviation", "kind": "equity"},
    "GESHIP": {"label": "Great Eastern Shipping", "kind": "equity"},
    "SCI": {"label": "Shipping Corporation of India", "kind": "equity"},
    "TITAN": {"label": "Titan Company", "kind": "equity"},
    "MUTHOOTFIN": {"label": "Muthoot Finance", "kind": "equity"},
    "HDFCBANK": {"label": "HDFC Bank", "kind": "equity"},
    "ICICIBANK": {"label": "ICICI Bank", "kind": "equity"},
    "SBIN": {"label": "State Bank of India", "kind": "equity"},
    "INFY": {"label": "Infosys", "kind": "equity"},
    "TCS": {"label": "TCS", "kind": "equity"},
    "TATAMOTORS": {"label": "Tata Motors", "kind": "equity"},
    "HINDALCO": {"label": "Hindalco", "kind": "equity"},
    "TATASTEEL": {"label": "Tata Steel", "kind": "equity"},
    "VEDL": {"label": "Vedanta", "kind": "equity"},
    "UPL": {"label": "UPL", "kind": "equity"},
    "PIIND": {"label": "PI Industries", "kind": "equity"},
    "DABUR": {"label": "Dabur", "kind": "equity"},
    "HINDUNILVR": {"label": "Hindustan Unilever", "kind": "equity"},
    "MARUTI": {"label": "Maruti Suzuki", "kind": "equity"},
}

COMPANY_ALIASES = {
    "ONGC": ("oil and natural gas corporation", "ongc"),
    "OIL": ("oil india", "oil india limited"),
    "IOC": ("indian oil corporation", "indian oil"),
    "BPCL": ("bharat petroleum", "bpcl"),
    "HPCL": ("hindustan petroleum", "hpcl"),
    "INDIGO": ("interglobe aviation", "indigo airlines"),
    "GESHIP": ("great eastern shipping",),
    "SCI": ("shipping corporation of india",),
    "TITAN": ("titan company", "titan company limited"),
    "MUTHOOTFIN": ("muthoot finance",),
    "HDFCBANK": ("hdfc bank",),
    "ICICIBANK": ("icici bank",),
    "SBIN": ("state bank of india", "sbi"),
    "INFY": ("infosys",),
    "TCS": ("tata consultancy services", "tcs"),
    "TATAMOTORS": ("tata motors",),
    "HINDALCO": ("hindalco",),
    "TATASTEEL": ("tata steel",),
    "VEDL": ("vedanta",),
    "UPL": ("upl limited",),
    "PIIND": ("pi industries",),
    "DABUR": ("dabur",),
    "HINDUNILVR": ("hindustan unilever", "hul"),
    "MARUTI": ("maruti suzuki",),
}

CORPORATE_POSITIVE = ("wins order", "secures order", "bags order", "wins contract", "secures contract",
                      "receives order", "profit rises", "profit jumps", "beats estimates", "revenue beats",
                      "gets approval", "regulatory approval", "license granted", "production starts",
                      "capacity expansion", "record quarterly profit")
CORPORATE_NEGATIVE = ("profit falls", "loss widens", "misses estimates", "downgrade", "fraud",
                      "investigation", "probe", "sebi action", "penalty", "ban", "recall",
                      "plant shutdown", "default", "debt crisis", "accounting irregularities",
                      "license suspended", "regulatory action")


DEFAULT_QUERIES = [
    {"id": "geopolitics-energy", "query": "Iran Israel Middle East Strait of Hormuz Red Sea oil shipping conflict"},
    {"id": "oil-gas-supply", "query": "OPEC crude oil production supply disruption LNG natural gas prices"},
    {"id": "metals-safe-haven", "query": "gold silver copper metals safe haven central bank prices"},
    {"id": "rates-fx", "query": "Federal Reserve interest rates RBI inflation rupee dollar India"},
    {"id": "china-trade", "query": "China stimulus economy tariffs trade restrictions India exports"},
    {"id": "india-policy-weather", "query": "India budget government policy monsoon weather agriculture regulation"},
    {"id": "corporate-catalysts", "query": "India listed companies major order earnings merger regulatory action"},
]

RULES = [
    {
        "id": "middle-east-energy-risk",
        "terms": ("iran", "strait of hormuz", "persian gulf", "red sea", "middle east", "israel", "houthi", "tanker", "oil terminal"),
        "supply_terms": ("war", "warfare", "escalation", "airstrike", "air strike", "missile", "bombing", "invasion", "invades", "retaliation", "military escalation", "troops", "combat", "attack", "strike", "blockade", "closure", "closed", "disruption", "sanction", "seizure", "tanker", "pipeline", "export halt", "shipping route"),
        "deescalation_terms": ("ceasefire", "peace deal", "de-escalation", "talks resume", "truce", "deal reached"),
        "category": "Geopolitics / energy supply",
        "base_score": 72,
        "paths": [
            {"asset": "CRUDE", "bias": "mixed", "why": "A conflict near major oil export or shipping routes can add a geopolitical risk premium; an actual supply or transit disruption matters more than rhetoric."},
            {"asset": "GOLD", "bias": "mixed", "why": "Escalation can increase safe-haven demand, but the US dollar and real yields can offset that effect."},
            {"asset": "NATGAS", "bias": "mixed", "why": "LNG shipping risk can matter if routes or export infrastructure are affected; the link is less direct than crude."},
            {"asset": "ONGC", "bias": "mixed", "why": "Higher realised crude prices can help upstream producers, subject to policy, production and currency factors."},
            {"asset": "OIL", "bias": "mixed", "why": "Higher realised crude prices can help upstream producers, subject to policy and operational factors."},
            {"asset": "IOC", "bias": "mixed", "why": "Higher crude raises input costs; inventory effects and retail-price pass-through can offset or reverse the impact."},
            {"asset": "BPCL", "bias": "mixed", "why": "Higher crude raises input costs; inventory effects and retail-price pass-through can offset or reverse the impact."},
            {"asset": "HPCL", "bias": "mixed", "why": "Higher crude raises input costs; inventory effects and retail-price pass-through can offset or reverse the impact."},
            {"asset": "INDIGO", "bias": "mixed", "why": "A sustained rise in crude-derived aviation fuel can pressure airline costs, subject to fuel-price pass-through and currency."},
            {"asset": "GESHIP", "bias": "mixed", "why": "Route disruption can affect freight rates, vessel utilisation, insurance and operating costs in opposing directions."},
            {"asset": "SCI", "bias": "mixed", "why": "Route disruption can affect freight rates, vessel utilisation, insurance and operating costs in opposing directions."},
        ],
        "confirm": ["Verified disruption to oil or LNG exports/transit", "MCX crude futures hold a breakout with abnormal volume", "Related shipping or energy instruments confirm the move"],
        "invalidate": ["Diplomatic de-escalation or reopening of disrupted routes", "No sustained change in physical supply, freight or insurance", "Crude price fails to confirm the headline after liquidity returns"],
    },
    {
        "id": "central-bank-rates",
        "terms": ("federal reserve", "fed rate", "interest rate", "rate cut", "rate hike", "rbi policy", "monetary policy", "inflation print", "cpi inflation", "bond yield"),
        "supply_terms": ("rate cut", "cuts rates", "dovish", "easing", "lower inflation", "inflation cools"),
        "deescalation_terms": ("rate hike", "raises rates", "hawkish", "higher for longer", "inflation surges"),
        "category": "Central banks / rates / inflation",
        "base_score": 68,
        "paths": [
            {"asset": "GOLD", "bias": "mixed", "why": "Lower real yields can support gold, while a stronger dollar or risk-on positioning can offset it."},
            {"asset": "NIFTY", "bias": "mixed", "why": "Rate expectations affect discount rates, liquidity and the rupee; sector effects differ."},
            {"asset": "BANKNIFTY", "bias": "mixed", "why": "Rate changes can alter loan growth, deposit costs, bond portfolios and net interest margins."},
            {"asset": "HDFCBANK", "bias": "mixed", "why": "Funding costs, loan repricing and credit growth determine the bank-specific response."},
            {"asset": "ICICIBANK", "bias": "mixed", "why": "Funding costs, loan repricing and credit growth determine the bank-specific response."},
            {"asset": "SBIN", "bias": "mixed", "why": "Funding costs, loan repricing and credit growth determine the bank-specific response."},
            {"asset": "INFY", "bias": "mixed", "why": "Global demand and currency moves can matter more than the rate headline itself."},
            {"asset": "TCS", "bias": "mixed", "why": "Global demand and currency moves can matter more than the rate headline itself."},
        ],
        "confirm": ["Policy statement and forward guidance agree", "Bond yields and USD/INR confirm the repricing", "Sector/index price action confirms after the announcement"],
        "invalidate": ["Policy language contradicts the headline interpretation", "Bond yields reverse the initial move", "Markets had fully priced the outcome before the announcement"],
    },
    {
        "id": "china-metals",
        "terms": ("china", "beijing", "stimulus", "property sector", "steel demand", "copper demand", "iron ore", "rare earth"),
        "supply_terms": ("stimulus", "eases restrictions", "support package", "infrastructure spending", "demand recovery"),
        "deescalation_terms": ("slowdown", "weak demand", "property slump", "factory activity falls", "deflation"),
        "category": "China demand / industrial commodities",
        "base_score": 62,
        "paths": [
            {"asset": "SILVER", "bias": "mixed", "why": "Silver has both precious-metal and industrial-demand drivers."},
            {"asset": "HINDALCO", "bias": "mixed", "why": "Industrial-metal prices and end-market demand can affect realisations and input costs."},
            {"asset": "TATASTEEL", "bias": "mixed", "why": "Chinese steel supply and demand influence global steel spreads and export competition."},
            {"asset": "VEDL", "bias": "mixed", "why": "Metal prices and Chinese demand can alter commodity realisations."},
            {"asset": "TATAMOTORS", "bias": "mixed", "why": "A China demand or tariff shock can affect automotive supply chains and export competition."},
            {"asset": "NIFTY", "bias": "mixed", "why": "Global growth expectations, commodity prices and trade risk feed into broader risk appetite."},
        ],
        "confirm": ["Relevant LME/MCX metal contracts confirm", "Chinese activity and demand data corroborate the story", "Indian metal names show relative-strength confirmation"],
        "invalidate": ["Policy announcement is smaller than headlines imply", "Physical demand indicators remain weak", "Metal futures reject the initial move"],
    },
    {
        "id": "india-monsoon-agri",
        "terms": ("monsoon", "rainfall", "drought", "flood", "heatwave", "reservoir", "crop sowing", "el nino", "la nina"),
        "supply_terms": ("deficit", "below normal", "drought", "crop damage", "heatwave", "flood"),
        "deescalation_terms": ("above normal", "surplus rainfall", "reservoirs recover", "normal monsoon"),
        "category": "India weather / agriculture",
        "base_score": 58,
        "paths": [
            {"asset": "UPL", "bias": "mixed", "why": "Crop mix, acreage, farm incomes and input demand can affect agrochemical demand."},
            {"asset": "PIIND", "bias": "mixed", "why": "Crop mix, acreage, farm incomes and input demand can affect agrochemical demand."},
            {"asset": "DABUR", "bias": "mixed", "why": "Rural demand and input costs may respond, usually with a lag."},
            {"asset": "HINDUNILVR", "bias": "mixed", "why": "Rural demand and input costs may respond, usually with a lag."},
            {"asset": "NIFTY", "bias": "mixed", "why": "Food inflation, rural demand and policy responses can affect broader markets."},
        ],
        "confirm": ["IMD data and reservoir/crop sowing figures corroborate", "Relevant agri-input and rural-demand shares confirm", "Food inflation or policy action changes expectations"],
        "invalidate": ["Short-term weather event does not alter seasonal rainfall totals", "Crop damage or sowing data remain normal", "No price or volume confirmation in related shares"],
    },
]


def _now() -> datetime:
    return datetime.now(IST)


def _safe_text(value: str, limit: int = 500) -> str:
    # RSS descriptions frequently contain CDATA HTML; strip markup before it
    # is displayed or sent as bounded context to the optional model.
    plain = html.unescape(str(value or ""))
    plain = re.sub(r"<[^>]*>", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()[:limit]


def _domain(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _fingerprint(title: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:20]


class NewsImpactDesk:
    def __init__(self, cfg: dict[str, Any] | None = None, fetch=None, ai_request=None, now_fn=None):
        self.cfg = cfg or {}
        self.news_cfg = self.cfg.get("news_intelligence") or {}
        self.fetch = fetch or self._download
        self.ai_request = ai_request or self._openai_assess
        self.now_fn = now_fn or _now

    @staticmethod
    def _download(url: str) -> str:
        response = requests.get(url, headers=UA, timeout=18)
        response.raise_for_status()
        return response.text

    def queries(self):
        return self.news_cfg.get("queries") or DEFAULT_QUERIES

    def _read_feed(self, item: dict[str, Any], max_age_hours: int) -> list[dict[str, Any]]:
        query = urllib.parse.quote(str(item.get("query") or ""))
        url = str(item.get("url") or GOOGLE_RSS.format(query=query))
        try:
            root = ET.fromstring(self.fetch(url))
        except Exception as exc:
            print(f"News intelligence feed {item.get('id', 'feed')} unavailable: {type(exc).__name__}")
            return []
        now = self.now_fn()
        found = []
        for node in root.iter():
            if node.tag.split("}")[-1] != "item":
                continue
            title = _safe_text(node.findtext("title"), 220)
            link = _safe_text(node.findtext("link"), 1000)
            desc = _safe_text(node.findtext("description"), 600)
            source_node = next((x for x in node if x.tag.split("}")[-1] == "source"), None)
            source = _safe_text(source_node.text if source_node is not None else "News", 100)
            pub = node.findtext("pubDate")
            try:
                stamp = parsedate_to_datetime(pub).astimezone(IST)
            except Exception:
                continue
            if not title or now - stamp > timedelta(hours=max_age_hours) or stamp > now + timedelta(minutes=10):
                continue
            parsed = urllib.parse.urlparse(link)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                link = ""
            link_domain = _domain(link)
            # Google News RSS uses a redirect domain; the outlet label is the
            # best available source identity until the source article is opened.
            source_identity = source.lower().strip().replace(" ", "-")
            domain = source_identity if not link_domain or link_domain.endswith("news.google.com") else link_domain
            found.append({"title": title, "description": desc, "source": source,
                          "domain": domain, "link": link,
                          "published_at": stamp.isoformat(timespec="seconds"), "query_id": item.get("id", "feed")})
        return found

    def fetch_headlines(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        max_age = int(self.news_cfg.get("max_age_hours", 48))
        items, errors, good = [], 0, 0
        for query in self.queries():
            rows = self._read_feed(query, max_age)
            if rows:
                good += 1
            else:
                errors += 1
            items.extend(rows)
        # Exact-title dedupe across overlapping RSS searches. Keep all source evidence.
        grouped: dict[str, dict[str, Any]] = {}
        for row in items:
            key = _fingerprint(row["title"])
            if key not in grouped:
                row["id"] = key
                row["sources"] = [row["source"]]
                row["source_domains"] = [row["domain"]] if row["domain"] else []
                grouped[key] = row
            else:
                cur = grouped[key]
                if row["source"] and row["source"] not in cur["sources"]:
                    cur["sources"].append(row["source"])
                if row["domain"] and row["domain"] not in cur["source_domains"]:
                    cur["source_domains"].append(row["domain"])
                if len(row.get("description", "")) > len(cur.get("description", "")):
                    cur["description"] = row["description"]
                if not cur.get("link") and row.get("link"):
                    cur["link"] = row["link"]
        return sorted(grouped.values(), key=lambda x: x["published_at"], reverse=True), {
            "queries": len(self.queries()), "queries_with_results": good, "query_errors_or_empty": errors,
            "headlines_received": len(items), "unique_headlines": len(grouped),
        }

    @staticmethod
    def _rule_for(text: str):
        low = text.lower()
        for rule in RULES:
            if any(term in low for term in rule["terms"]):
                return rule
        return None

    @staticmethod
    def _company_hit(text: str):
        # Match longer legal/company names first; ticker aliases use word boundaries.
        for asset, aliases in COMPANY_ALIASES.items():
            for alias in sorted(aliases, key=len, reverse=True):
                if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text, flags=re.I):
                    return asset
        return None

    def _company_assessment(self, headline: dict[str, Any], text: str, asset: str) -> dict[str, Any]:
        pos = (any(term in text for term in CORPORATE_POSITIVE) or
               bool(re.search(r"\b(win|wins|won|secure|secures|secured|bag|bags|bagged)\b.{0,45}\b(order|contract)\b", text)))
        neg = any(term in text for term in CORPORATE_NEGATIVE)
        bias = "mixed" if pos == neg else "bullish" if pos else "bearish"
        action = "positive company-specific catalyst" if pos and not neg else "negative company-specific catalyst" if neg and not pos else "company-specific event with an uncertain direction"
        score = min(86, 58 + (8 if headline.get("description") else 0) + 4 * max(0, len(headline.get("source_domains") or []) - 1))
        return {
            "category": "Listed-company news / corporate catalyst", "severity": "high" if score >= 82 else "medium",
            "impact_score": score, "overall_bias": bias,
            "summary": f"A company-specific headline was matched to {ASSETS[asset]['label']}: {action}. Verify the filing, order value, terms and materiality before interpreting the price impact.",
            "impacts": [{"asset": asset, "label": ASSETS[asset]["label"], "bias": bias,
                         "why": "Company-specific news can affect expectations, earnings or risk premia; the headline alone does not establish financial materiality."}],
            "confirm": ["Company exchange filing or official announcement confirms the facts", "Price and relative volume respond in the direction of the event", "Subsequent management or regulatory disclosure supports materiality"],
            "invalidate": ["Company filing contradicts or clarifies the headline", "Order/event is immaterial relative to revenue or already priced in", "Price and volume fail to confirm after the market opens"],
            "horizon": "intraday" if pos or neg else "1-20 sessions", "confidence": "moderate" if score >= 66 else "low",
            "confidence_note": "Confidence in entity matching and transmission mapping only; not a probability of a price move.",
            "analysis_method": "company-rules", "analysis_version": 1,
        }

    def _rule_assessment(self, headline: dict[str, Any]) -> dict[str, Any]:
        text = f"{headline.get('title', '')} {headline.get('description', '')}".lower()
        rule = self._rule_for(text)
        if not rule:
            company = self._company_hit(text)
            if company:
                return self._company_assessment(headline, text, company)
            return {
                "category": "Unclassified market news", "severity": "low", "impact_score": 15,
                "overall_bias": "unknown", "summary": "Headline captured; no sufficiently specific market transmission rule matched.",
                "impacts": [], "confirm": ["Identify the affected industry or asset and wait for market confirmation"],
                "invalidate": ["No relevant asset or price response emerges"], "horizon": "unknown",
                "confidence": "low", "confidence_note": "No specific causal rule matched; this is not an estimated probability.",
                "analysis_method": "rules", "analysis_version": 1,
            }
        is_supply = any(t in text for t in rule["supply_terms"])
        is_deescalation = any(t in text for t in rule["deescalation_terms"])
        paths = []
        for path in rule["paths"]:
            bias = path["bias"]
            if rule["id"] == "middle-east-energy-risk":
                if is_deescalation and is_supply:
                    bias = "mixed"   # competing developments in one headline: avoid choosing a side
                elif is_deescalation:
                    bias = "bearish" if path["asset"] in ("CRUDE", "GOLD", "NATGAS") else "mixed"
                elif is_supply:
                    if path["asset"] in ("CRUDE", "NATGAS", "GOLD"):
                        bias = "bullish"
                    else:
                        bias = "mixed"
            elif rule["id"] == "central-bank-rates":
                if is_supply:
                    bias = "bullish" if path["asset"] == "GOLD" else "mixed"
                elif is_deescalation:
                    bias = "bearish" if path["asset"] == "GOLD" else "mixed"
            elif rule["id"] == "china-metals":
                if is_supply:
                    bias = "bullish" if path["asset"] in ("SILVER", "HINDALCO", "TATASTEEL", "VEDL") else "mixed"
                elif is_deescalation:
                    bias = "bearish" if path["asset"] in ("SILVER", "HINDALCO", "TATASTEEL", "VEDL") else "mixed"
            paths.append({**path, "bias": bias, "label": ASSETS[path["asset"]]["label"]})
        source_n = max(1, len(headline.get("source_domains") or headline.get("sources") or []))
        score = rule["base_score"] + min(12, (source_n - 1) * 4) + (10 if is_supply else 0) + (3 if headline.get("description") else 0)
        score = min(95, score)
        confidence = "moderate" if source_n >= 2 and (is_supply or is_deescalation) else "low"
        summary = ("Conflict escalation can add a geopolitical risk premium, but a sustained crude move generally needs evidence of supply, export or transit disruption. " if is_supply and rule["id"] == "middle-east-energy-risk"
                   else "A de-escalation headline could reduce some event risk premium if confirmed. " if is_deescalation and rule["id"] == "middle-east-energy-risk"
                   else "The headline may transmit through the macro/industry channel described below. ")
        summary += "The asset-level direction remains conditional and needs price/volume confirmation."
        return {
            "category": rule["category"], "severity": "high" if score >= 82 else "medium" if score >= 62 else "low",
            "impact_score": score, "overall_bias": "conditional", "summary": summary,
            "impacts": paths, "confirm": rule["confirm"], "invalidate": rule["invalidate"],
            "horizon": "intraday to 2 weeks" if is_supply else "1 to 20 sessions",
            "confidence": confidence,
            "confidence_note": f"Confidence in the relevance mapping only; {source_n} distinct source/domain entries. Not a probability of a price move.",
            "analysis_method": "rules", "analysis_version": 1,
        }

    def _openai_assess(self, headline: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any] | None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        ai_cfg = self.news_cfg.get("ai") or {}
        if not api_key or not ai_cfg.get("enabled", True):
            return None
        allowed = [
            {"id": k, "name": v["label"], "kind": v["kind"]}
            for k, v in ASSETS.items()
        ]
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "category": {"type": "string"},
                "summary": {"type": "string"},
                "overall_bias": {"type": "string", "enum": ["bullish", "bearish", "mixed", "unknown"]},
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "horizon": {"type": "string", "enum": ["intraday", "1-3 sessions", "1-4 weeks", "unknown"]},
                "confidence": {"type": "string", "enum": ["low", "moderate", "high"]},
                "confidence_note": {"type": "string"},
                "impacts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "properties": {"asset": {"type": "string", "enum": list(ASSETS)},
                                   "bias": {"type": "string", "enum": ["bullish", "bearish", "mixed", "unknown"]},
                                   "why": {"type": "string"}},
                    "required": ["asset", "bias", "why"]}},
                "confirm": {"type": "array", "items": {"type": "string"}},
                "invalidate": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["category", "summary", "overall_bias", "severity", "horizon", "confidence",
                         "confidence_note", "impacts", "confirm", "invalidate"],
        }
        system = (
            "You are an event-driven market research analyst for Indian listed markets and MCX. "
            "Analyse only the supplied headline and summary. Do not browse or invent facts. Separate confirmed facts from conditional mechanisms. "
            "Use only asset IDs from the supplied catalogue. Do not give trade instructions, target prices, or numerical probabilities. "
            "A confidence label describes confidence in the causal mapping, NOT the probability that price will move. "
            "When a headline is vague or its mechanism is indirect, return unknown/mixed and low confidence. Consider both first- and second-order effects, "
            "including who may benefit and who may be hurt. Output only the requested schema."
        )
        payload = {
            "model": str(ai_cfg.get("model") or "gpt-4o-mini"),
            "store": False,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps({
                    "headline": headline.get("title"), "summary": headline.get("description"),
                    "published_at": headline.get("published_at"), "available_assets": allowed,
                    "deterministic_rule_context": fallback,
                }, ensure_ascii=False)}]},
            ],
            "text": {"format": {"type": "json_schema", "name": "market_impact_assessment", "strict": True, "schema": schema}},
        }
        try:
            response = requests.post("https://api.openai.com/v1/responses", headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload, timeout=int(ai_cfg.get("timeout_seconds", 24)))
            response.raise_for_status()
            body = response.json()
            texts = []
            for out in body.get("output") or []:
                for content in out.get("content") or []:
                    if content.get("type") == "output_text" and content.get("text"):
                        texts.append(content["text"])
            if not texts:
                return None
            parsed = json.loads("\n".join(texts))
            impacts = []
            for item in parsed.get("impacts", []):
                asset = item.get("asset")
                if asset not in ASSETS:
                    continue
                impacts.append({"asset": asset, "label": ASSETS[asset]["label"],
                                "bias": item.get("bias", "unknown"), "why": _safe_text(item.get("why"), 420)})
            # The relevance score remains the deterministic score; LLM output never
            # upgrades a weak headline into a high-confidence trade idea.
            return {
                "category": _safe_text(parsed.get("category"), 100) or fallback["category"],
                "severity": parsed["severity"], "impact_score": fallback["impact_score"],
                "overall_bias": parsed["overall_bias"], "summary": _safe_text(parsed["summary"], 700),
                "impacts": impacts, "confirm": [_safe_text(x, 220) for x in parsed["confirm"][:6]],
                "invalidate": [_safe_text(x, 220) for x in parsed["invalidate"][:6]],
                "horizon": parsed["horizon"], "confidence": parsed["confidence"],
                "confidence_note": _safe_text(parsed["confidence_note"], 300) + " Model interpretation; not a calibrated probability.",
                "analysis_method": "ai+rules", "analysis_version": 1,
            }
        except Exception as exc:
            print("AI impact assessment unavailable; retaining rules-only assessment:", type(exc).__name__)
            return None

    def assess(self, headline: dict[str, Any]) -> dict[str, Any]:
        base = self._rule_assessment(headline)
        enhanced = self.ai_request(headline, base)
        return enhanced or base

    def scan(self, prior: dict[str, Any] | None = None) -> dict[str, Any]:
        prior = prior or {}
        events = {e.get("id"): e for e in prior.get("events", []) if e.get("id")}
        headlines, stats = self.fetch_headlines()
        now = self.now_fn()
        new_count = 0
        ai_limit = max(0, int((self.news_cfg.get("ai") or {}).get("max_new_per_scan", 4)))
        ai_used = 0
        for headline in headlines:
            old = events.get(headline["id"])
            if old:
                old["last_seen_at"] = now.isoformat(timespec="seconds")
                old["sources"] = sorted(set(old.get("sources", []) + headline.get("sources", [])))
                old["source_domains"] = sorted(set(old.get("source_domains", []) + headline.get("source_domains", [])))
                old["evidence_count"] = len(old["source_domains"] or old["sources"])
                continue
            fallback = self._rule_assessment(headline)
            if ai_used < ai_limit and os.environ.get("OPENAI_API_KEY") and (self.news_cfg.get("ai") or {}).get("enabled", True):
                assessment = self.assess(headline)
                ai_used += 1
            else:
                assessment = fallback
            event = {
                **headline, **assessment,
                "first_seen_at": now.isoformat(timespec="seconds"), "last_seen_at": now.isoformat(timespec="seconds"),
                "evidence_count": max(1, len(headline.get("source_domains") or headline.get("sources") or [])),
                "market_confirmation": "not_checked",
                "decision_status": "CONTEXT_ONLY",
                "probability": None,
            }
            events[headline["id"]] = event
            new_count += 1
        retention = int(self.news_cfg.get("retention_days", 21))
        cutoff = now - timedelta(days=retention)
        kept = [e for e in events.values() if self._parse_time(e.get("published_at")) >= cutoff]
        kept.sort(key=lambda e: (int(e.get("impact_score", 0)), e.get("published_at", "")), reverse=True)
        kept = kept[:int(self.news_cfg.get("max_events", 180))]
        return {
            "schema": 1,
            "status": {"running": True, "heartbeat": now.isoformat(timespec="seconds"),
                       "last_scan": now.isoformat(timespec="seconds"),
                       "next_scan_seconds": int(self.news_cfg.get("poll_seconds", 300)),
                       "source_health": stats, "new_events": new_count,
                       "analysis_mode": "AI-assisted" if os.environ.get("OPENAI_API_KEY") and (self.news_cfg.get("ai") or {}).get("enabled", True) else "Rules-based fallback",
                       "market_data_confirmation": "Not yet connected to event cards; all impacts remain context-only"},
            "events": kept,
            "disclaimer": "Impact scores rank headline relevance; they are not a calibrated probability estimate. Directional effects are conditional hypotheses until corroborated by market data.",
        }

    @staticmethod
    def _parse_time(value: str | None) -> datetime:
        try:
            d = datetime.fromisoformat(str(value))
            return d.astimezone(IST) if d.tzinfo else d.replace(tzinfo=IST)
        except Exception:
            return datetime.min.replace(tzinfo=IST)


def equity_catalysts(state: dict[str, Any] | None, universe, now: datetime | None = None,
                     min_score: int = 65, max_age_hours: int = 36) -> list[dict[str, str]]:
    """Return recent, high-relevance listed-equity impacts as *watchlist reasons*.

    This function never creates a trade. It only promotes mapped equities into
    the strategy desk's ordinary stock-in-play queue, where all price, volume,
    freshness, risk and strategy gates still apply.
    """
    now = now or _now()
    allowed = {str(x).upper() for x in (universe or [])}
    events = (state or {}).get("events") or []
    out: dict[str, dict[str, str]] = {}
    def _score(event):
        try:
            return int(event.get("impact_score", 0))
        except (TypeError, ValueError, AttributeError):
            return 0
    ordered = sorted((e for e in events if isinstance(e, dict)),
                     key=lambda e: (_score(e), str(e.get("published_at", ""))), reverse=True)
    for event in ordered:
        try:
            score = _score(event)
            published = NewsImpactDesk._parse_time(event.get("published_at"))
            age = (now - published).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if score < min_score or age < -0.17 or age > max_age_hours:
            continue
        for impact in event.get("impacts") or []:
            asset = str(impact.get("asset") or "").upper()
            if asset not in allowed or ASSETS.get(asset, {}).get("kind") != "equity":
                continue
            why = (f"News catalyst · {event.get('category') or 'market event'} · "
                   f"{impact.get('bias', 'conditional')} scenario: {impact.get('why') or event.get('title', '')}")
            # Preserve the strongest/most recent explanation for each share.
            if asset not in out:
                out[asset] = {"symbol": asset, "why": _safe_text(why, 300),
                              "event_id": str(event.get("id") or ""), "impact_score": str(score)}
    return list(out.values())


class IntelligenceService:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.worker_mode = "scheduled"
        self.data_dir = self.root / "_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "intelligence.json"
        self.config = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8")) or {}
        self.desk = NewsImpactDesk(self.config)

    def _load(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, value):
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def _notify(self, state):
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if token and not chat:
            # Reuse the already-paired Telegram destination where possible.
            # secure.bin is encrypted using the broker secret plus bot token.
            try:
                from .store import Store
                material = os.environ.get("FYERS_SECRET_KEY") or os.environ.get("KOTAK_CONSUMER_KEY") or ""
                secure = Store(str(self.data_dir), material + token).load_secure()
                chat = str(secure.get("chat_id") or "")
            except Exception:
                chat = ""
        if not (token and chat):
            return
        try:
            chat = int(chat)
        except ValueError:
            return
        sent = set(state.get("alerted_ids", []))
        threshold = int((self.config.get("news_intelligence") or {}).get("telegram_min_impact_score", 82))
        eligible = [e for e in state.get("events", []) if int(e.get("impact_score", 0)) >= threshold and e.get("id") not in sent]
        for event in eligible[:3]:
            paths = ", ".join(f"{p['label']} ({p['bias']})" for p in event.get("impacts", [])[:4]) or "No specific instrument mapped"
            msg = ("🧭 <b>Signal Desk Impact Radar</b>\n" + html.escape(event.get("title", "")) +
                   "\n\n" + html.escape(event.get("summary", "")) + "\n\nPotentially affected: " + html.escape(paths) +
                   "\n\nContext only — wait for price/volume confirmation. This is not a trade instruction.")
            try:
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=12).raise_for_status()
                sent.add(event["id"])
            except Exception as exc:
                print("Telegram impact alert failed:", type(exc).__name__)
        state["alerted_ids"] = sorted(sent)[-500:]

    def _publish(self):
        # If run in a checkout whose _data directory has an origin, publish the
        # intelligence file without overwriting trade-engine-owned data. A
        # GitHub token is injected through process-local Git config, never saved
        # into the repository's remote URL.
        try:
            from .store import Store
            store = Store(str(self.data_dir), os.environ.get("FYERS_SECRET_KEY") or os.environ.get("KOTAK_CONSUMER_KEY") or "")
            preserve = ()
            if os.environ.get("SIGNAL_DESK_SHARED_DATA") == "1":
                preserve = {"feed.json", "secure.bin", "strategies.json", "results.json", "volbase.json",
                            "holidays.json", "options_tape.jsonl", "supervisor.json"}
            prefixes = ("chartlive_",) if preserve else ()
            store.publish(own={"intelligence.json"}, own_prefix=prefixes, preserve_local=preserve)
        except Exception as exc:
            print("Intelligence publish skipped:", type(exc).__name__)

    def once(self):
        previous = self._load()
        if not (self.config.get("news_intelligence") or {}).get("enabled", True):
            previous.setdefault("status", {})
            previous["status"].update({"running": False, "disabled": True,
                                       "heartbeat": _now().isoformat(timespec="seconds")})
            self._save(previous)
            self._publish()
            print("Impact radar is disabled by config.yaml.")
            return previous
        state = self.desk.scan(previous)
        state["status"]["worker_mode"] = self.worker_mode
        state["status"]["running"] = self.worker_mode == "continuous"
        state["status"].pop("last_error", None)
        # Keep alert dedupe state separate from the public event list.
        state["alerted_ids"] = previous.get("alerted_ids", [])
        self._notify(state)
        self._save(state)
        self._publish()
        status = state["status"]
        print(f"Impact radar: {len(state['events'])} retained events, {status['new_events']} new, "
              f"{status['source_health']['unique_headlines']} unique headlines; {status['analysis_mode']}.")
        return state

    def daemon(self):
        self.worker_mode = "continuous"
        pid_path = self.data_dir / "intelligence.lock"
        lock = open(pid_path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                if not lock.read(1):
                    lock.write(" ")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock.seek(0)
            lock.write(str(os.getpid()).ljust(32))
            lock.flush()
        except (ImportError, OSError, BlockingIOError):
            print("Another Signal Desk intelligence worker appears to be running; exiting.")
            lock.close()
            return
        interval = max(60, int((self.config.get("news_intelligence") or {}).get("poll_seconds", 300)))
        print(f"Starting 24/7 news intelligence worker; poll interval {interval}s.")
        try:
            while not STOP:
                started = time.monotonic()
                try:
                    self.once()
                except Exception as exc:
                    print("Intelligence cycle failed; retaining previous events:", repr(exc))
                    state = self._load()
                    state.setdefault("status", {}).update({
                        "running": True, "heartbeat": _now().isoformat(timespec="seconds"),
                        "last_error": f"{type(exc).__name__}: {str(exc)[:240]}",
                    })
                    try:
                        self._save(state)
                        self._publish()
                    except Exception as save_exc:
                        print("Could not publish intelligence failure heartbeat:", type(save_exc).__name__)
                elapsed = time.monotonic() - started
                remaining = max(5, interval - elapsed)
                for _ in range(int(remaining)):
                    if STOP:
                        break
                    time.sleep(1)
        finally:
            state = self._load()
            state.setdefault("status", {}).update({
                "running": False, "worker_mode": "stopped",
                "heartbeat": _now().isoformat(timespec="seconds"),
                "stopped_at": _now().isoformat(timespec="seconds"),
            })
            try:
                self._save(state)
                self._publish()
            except Exception as exc:
                print("Could not publish stopped status:", type(exc).__name__)
            lock.close()
        print("News intelligence worker stopped cleanly.")


def _stop(_signum, _frame):
    global STOP
    STOP = True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Signal Desk 24/7 news impact intelligence")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one news scan and exit")
    mode.add_argument("--daemon", action="store_true", help="run continuously until stopped")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    service = IntelligenceService()
    if args.daemon:
        service.daemon()
    else:
        service.once()


if __name__ == "__main__":
    main()
