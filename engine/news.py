"""Free news scan: finds which stocks are in today's headlines and why.

Sources are public RSS feeds (Google News and Business Standard). Only the
headline, source and link are kept. If a feed is unreachable the tool carries on
without it; news never blocks a trade check.
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta
from email.utils import parsedate_to_datetime

import requests

from . import clock

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
GOOGLE = "https://news.google.com/rss/search?q={}&hl=en-IN&gl=IN&ceid=IN:en"
FEEDS = [
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("", GOOGLE.format(urllib.parse.quote("stocks to watch today when:1d"))),
    ("", GOOGLE.format(urllib.parse.quote("shares in focus results order win when:1d"))),
    ("", GOOGLE.format(urllib.parse.quote("quarterly results net profit shares NSE when:1d"))),
]
# Names the papers use that differ from the official company name.
EXTRA = {
    "TCS": ["TCS"], "SBIN": ["SBI", "State Bank of India"], "HINDUNILVR": ["HUL", "Hindustan Unilever"],
    "LT": ["L&T", "Larsen & Toubro", "Larsen and Toubro"], "M&M": ["M&M", "Mahindra & Mahindra", "Mahindra and Mahindra"],
    "RELIANCE": ["Reliance Industries", "RIL"], "INFY": ["Infosys"], "BHARTIARTL": ["Bharti Airtel", "Airtel"],
    "MARUTI": ["Maruti Suzuki", "Maruti"], "ITC": ["ITC"], "ONGC": ["ONGC"], "NTPC": ["NTPC"], "BEL": ["BEL"],
    "HAL": ["HAL"], "BPCL": ["BPCL"], "IOC": ["IOC", "Indian Oil"], "HDFCBANK": ["HDFC Bank"],
    "ICICIBANK": ["ICICI Bank"], "KOTAKBANK": ["Kotak Mahindra Bank", "Kotak Bank"], "AXISBANK": ["Axis Bank"],
    "TATAMOTORS": ["Tata Motors"], "TMPV": ["Tata Motors"], "ULTRACEMCO": ["UltraTech Cement", "UltraTech"],
    "SUNPHARMA": ["Sun Pharma"], "DRREDDY": ["Dr Reddy's", "Dr Reddy’s", "Dr. Reddy's"], "JSWSTEEL": ["JSW Steel"],
    "ADANIENT": ["Adani Enterprises"], "ADANIPORTS": ["Adani Ports"], "BAJFINANCE": ["Bajaj Finance"],
    "BAJAJFINSV": ["Bajaj Finserv"], "HCLTECH": ["HCL Tech", "HCLTech", "HCL Technologies"], "TECHM": ["Tech Mahindra"],
    "POWERGRID": ["Power Grid"], "COALINDIA": ["Coal India"], "ETERNAL": ["Eternal", "Zomato"], "DMART": ["DMart"],
    "BSE": ["BSE Ltd", "BSE shares", "BSE share", "BSE stock"], "PAYTM": ["Paytm"], "NYKAA": ["Nykaa"], "IRCTC": ["IRCTC"], "LICI": ["LIC"], "PNB": ["PNB", "Punjab National Bank"],
    "BANKBARODA": ["Bank of Baroda"], "VEDL": ["Vedanta"], "INDIGO": ["IndiGo", "InterGlobe Aviation"],
}
SKIP_AS_NAME = {"BSE", "OIL", "IDEA"}   # too easily confused with ordinary words
JUNK = ("share price today", "stock price live", "live updates", "live blog", "horoscope")
MONEY_WORDS = ("share", "stock", "q1", "q2", "q3", "q4", "result", "profit", "order", "target", "crore", "%",
               "sensex", "nifty", "rating", "dividend", "stake", "brokerage", "investor", "revenue", "deal")
TAGS = [
    ("Results", ["q1", "q2", "q3", "q4", "results", "net profit", "earnings", "profit rises", "profit falls",
                 "profit jumps", "profit drops", "revenue"]),
    ("Order win", ["order", "contract", "bags", "wins "]),
    ("Broker view", ["upgrade", "downgrade", "target price", "brokerage", "buy rating", "sell rating", "initiates"]),
    ("Deal", ["acquire", "acquisition", "stake", "merger", "block deal", "bulk deal", "buyback", "dividend",
              "bonus", "stock split", "fundraise", "qip"]),
    ("Regulatory", ["sebi", "rbi ", "penalty", "tax notice", "gst ", "probe", "court", "ban "]),
]


def clean_name(company):
    n = re.sub(r"\s*\((India|I)\)", "", company)
    n = re.sub(r"\b(Ltd\.?|Limited)\s*$", "", n.strip()).strip(" .,")
    return n


def tag_for(title):
    low = " " + title.lower() + " "
    for tag, words in TAGS:
        if any(w in low for w in words):
            return tag
    return "News"


class NewsDesk:
    def __init__(self, universe, fetch=None):
        """universe: list of (symbol, company name)."""
        self._fetch = fetch or self._download
        self.names = {s: clean_name(c) for s, c in universe}
        alias = []
        for sym, company in universe:
            words = set(EXTRA.get(sym, []))
            if self.names[sym] not in SKIP_AS_NAME:
                words.add(self.names[sym])
            if sym.isalpha() and len(sym) >= 3 and sym not in SKIP_AS_NAME:
                words.add(sym)      # tickers the papers use directly, like BHEL or PNB
            for w in words:
                if len(w) >= 3:
                    alias.append((w, sym))
        alias.sort(key=lambda a: -len(a[0]))
        self._alias = [(re.compile(r"(?<![\w&])" + re.escape(a) + r"(?![\w&])"), s) for a, s in alias]

    @staticmethod
    def _download(url):
        r = requests.get(url, headers=UA, timeout=12)
        r.raise_for_status()
        return r.text

    def _items(self, label, url, max_age_hours):
        out = []
        try:
            root = ET.fromstring(self._fetch(url))
        except Exception as e:
            print("News feed skipped:", url[:60], type(e).__name__)
            return out
        now = clock.now()
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            source = label or (it.findtext("source") or "").strip()
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3]
            try:
                when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(clock.IST)
            except Exception:
                continue
            if any(j in title.lower() for j in JUNK):
                continue
            if not title or now - when > timedelta(hours=max_age_hours) or when > now + timedelta(minutes=10):
                continue
            out.append({"title": title[:180], "source": source or "News", "link": (it.findtext("link") or "").strip(),
                        "t": when.isoformat(timespec="minutes")})
        return out

    def match(self, title):
        """Which universe stocks does a headline name? Longest names win, so
        'Union Bank of India' is not also read as 'Bank of India'."""
        taken, found = [], []
        for rx, sym in self._alias:
            for m in rx.finditer(title):
                if all(m.end() <= a or m.start() >= b for a, b in taken):
                    taken.append((m.start(), m.end()))
                    if sym not in found:
                        found.append(sym)
        return found

    def refresh(self, max_age_hours=18):
        """Headlines from the general feeds that name a stock in the universe."""
        out, seen = [], set()
        for label, url in FEEDS:
            for it in self._items(label, url, max_age_hours):
                for sym in self.match(it["title"])[:3]:
                    key = (sym, it["title"][:50].lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(dict(it, symbol=sym, name=self.names[sym], tag=tag_for(it["title"])))
        out.sort(key=lambda x: x["t"], reverse=True)
        return out

    def for_stock(self, symbol, limit=2):
        """Latest headlines about one stock, used to explain why it is moving."""
        name = self.names.get(symbol, symbol)
        url = GOOGLE.format(urllib.parse.quote(f'"{name}" share when:1d'))
        items = [it for it in self._items("", url, 20) if symbol in self.match(it["title"])
                 and any(w in it["title"].lower() for w in MONEY_WORDS)]
        items.sort(key=lambda x: x["t"], reverse=True)
        return [dict(it, symbol=symbol, name=name, tag=tag_for(it["title"])) for it in items[:limit]]
