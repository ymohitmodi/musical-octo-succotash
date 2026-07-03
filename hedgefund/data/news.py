"""Free news via RSS (stdlib XML parsing, no extra dependency)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from ..storage import DB
from .http import RateLimitedHTTP

log = logging.getLogger("hedgefund.news")

TICKER_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
]
MARKET_FEEDS = [
    "https://www.sec.gov/news/pressreleases.rss",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]


class NewsFeed:
    def __init__(self, db: DB):
        self.db = db
        self.http = RateLimitedHTTP(rps=1.0, user_agent="hedgefund/0.1 rss reader")

    def _parse(self, url: str, limit: int) -> list[dict]:
        try:
            resp = self.http.get(url, timeout=20)
            root = ET.fromstring(resp.content)
            items = []
            for item in root.iter("item"):
                items.append({
                    "title": (item.findtext("title") or "").strip(),
                    "date": (item.findtext("pubDate") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                })
                if len(items) >= limit:
                    break
            return items
        except Exception as e:  # noqa: BLE001 - news is best-effort
            log.warning("rss failed %s: %s", url, e)
            return []

    def for_ticker(self, ticker: str, limit: int = 8) -> list[dict]:
        key = f"news:{ticker}"
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        items: list[dict] = []
        for tpl in TICKER_FEEDS:
            items.extend(self._parse(tpl.format(ticker=ticker), limit))
        self.db.cache_set(key, items[:limit], ttl_sec=3600)
        return items[:limit]

    def market_wide(self, limit: int = 10) -> list[dict]:
        cached = self.db.cache_get("news:market")
        if cached is not None:
            return cached
        items: list[dict] = []
        for url in MARKET_FEEDS:
            items.extend(self._parse(url, limit // 2 + 1))
        self.db.cache_set("news:market", items[:limit], ttl_sec=1800)
        return items[:limit]
