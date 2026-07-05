"""Integration contract tests — offline, but over a REAL local HTTP server.

Each data client (EDGAR, Yahoo, Stooq, FRED, RSS) is pointed at a local
server that replays realistic wire-format payloads, including each source's
known quirks:

  * EDGAR companyfacts: mixed 10-K/10-Q datapoints, tag fallbacks, dei shares
  * Yahoo chart JSON: null closes on halted days
  * Stooq CSV: '-' volumes and junk rows that must be skipped, not fatal
  * FRED: "." placeholders for missing observations
  * RSS 2.0 with missing links + Atom feeds
  * HTTP: 429 throttling responses and hard failures (fallback + stale cache)

Also verifies the polite-rate-limiter actually spaces requests, so free-tier
sources are never hammered. This is the closest to live validation that can
run anywhere; `python -m hedgefund.main validate` runs the same assertions
against the real endpoints on the production machine.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hedgefund.data import edgar as edgar_mod
from hedgefund.data import macro as macro_mod
from hedgefund.data import market as market_mod
from hedgefund.data import news as news_mod
from hedgefund.data.http import RateLimitedHTTP
from hedgefund.storage import DB


# ------------------------------------------------------------ fixture server
class FixtureServer:
    """Local HTTP server that serves canned (status, content_type, body) by path
    prefix and counts hits."""

    def __init__(self):
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        self.hits: dict[str, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                for prefix, (status, ctype, body) in outer.routes.items():
                    if self.path.startswith(prefix):
                        outer.hits[prefix] = outer.hits.get(prefix, 0) + 1
                        # emulate 429-then-OK sequences
                        if status == 429 and outer.hits[prefix] > 1:
                            status2, body2 = 200, body
                        else:
                            status2, body2 = status, (b"slow down" if status == 429 else body)
                        self.send_response(status2)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(body2)))
                        self.end_headers()
                        self.wfile.write(body2)
                        return
                self.send_error(404)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()


# ------------------------------------------------------- realistic payloads
TICKERS_JSON = json.dumps({
    "0": {"cik_str": 50863, "ticker": "INTC", "title": "INTEL CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}).encode()

SUBMISSIONS_JSON = json.dumps({
    "cik": "50863", "name": "INTEL CORP",
    "filings": {"recent": {
        "form": ["10-K", "8-K", "10-Q", "4"],
        "filingDate": ["2026-01-30", "2026-01-15", "2025-10-28", "2025-10-01"],
        "accessionNumber": ["0000050863-26-000010", "0000050863-26-000005",
                            "0000050863-25-000090", "0000050863-25-000080"],
        "primaryDocument": ["intc-20251227.htm", "intc-8k.htm",
                            "intc-20250927.htm", "form4.xml"],
    }},
}).encode()

# true-to-shape companyfacts: FY + quarterly datapoints mixed, dei shares
COMPANYFACTS_JSON = json.dumps({
    "cik": 50863, "entityName": "INTEL CORP",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2025-12-27", "val": 4_300_000_000, "form": "10-K",
             "fy": 2025, "fp": "FY"},
            {"end": "2024-12-28", "val": 4_250_000_000, "form": "10-K",
             "fy": 2024, "fp": "FY"},
        ]}}},
        "us-gaap": {
            # primary tag missing -> client must fall back to the contract tag
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                {"start": "2025-01-01", "end": "2025-12-27", "val": 55_000_000_000,
                 "form": "10-K", "fy": 2025, "fp": "FY"},
                {"start": "2025-07-01", "end": "2025-09-27", "val": 14_000_000_000,
                 "form": "10-Q", "fy": 2025, "fp": "Q3"},   # must be excluded
                {"start": "2024-01-01", "end": "2024-12-28", "val": 53_000_000_000,
                 "form": "10-K", "fy": 2024, "fp": "FY"},
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                {"start": "2025-01-01", "end": "2025-12-27", "val": 4_000_000_000,
                 "form": "10-K", "fy": 2025, "fp": "FY"},
                {"start": "2024-01-01", "end": "2024-12-28", "val": 1_500_000_000,
                 "form": "10-K", "fy": 2024, "fp": "FY"},
            ]}},
            "Assets": {"units": {"USD": [
                {"end": "2025-12-27", "val": 200_000_000_000, "form": "10-K",
                 "fy": 2025, "fp": "FY"},
                {"end": "2024-12-28", "val": 195_000_000_000, "form": "10-K",
                 "fy": 2024, "fp": "FY"},
            ]}},
        },
    },
}).encode()

YAHOO_CHART_JSON = json.dumps({"chart": {"result": [{
    "meta": {"symbol": "SPY"},
    "timestamp": [1751347800, 1751434200, 1751520600],
    "indicators": {"quote": [{
        "open": [618.2, 620.1, None],
        "high": [620.0, 622.4, None],
        "low": [616.9, 618.8, None],
        "close": [619.5, 621.7, None],      # None = halted/holiday row
        "volume": [41000000, 39000000, None],
    }]},
}], "error": None}}).encode()

STOOQ_CSV = (b"Date,Open,High,Low,Close,Volume\n"
             b"2026-06-30,617.10,619.30,615.80,618.90,52000000\n"
             b"2026-07-01,619.00,621.00,617.50,620.40,-\n"      # '-' volume: skip
             b"No data\n"                                        # junk row: skip
             b"2026-07-02,620.50,623.10,619.90,622.80,48000000\n")

FRED_JSON = json.dumps({"observations": [
    {"date": "2026-07-02", "value": "4.33"},
    {"date": "2026-07-01", "value": "."},          # FRED's missing marker
    {"date": "2026-06-30", "value": "4.33"},
]}).encode()

RSS_XML = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
           b"<title>Feed</title>"
           b"<item><title>Intel announces buyback</title>"
           b"<pubDate>Fri, 03 Jul 2026 12:00:00 GMT</pubDate>"
           b"<link>https://example.com/1</link></item>"
           b"<item><title>Item without link</title>"
           b"<pubDate>Fri, 03 Jul 2026 11:00:00 GMT</pubDate></item>"
           b"</channel></rss>")

ATOM_XML = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            b"<title>SEC</title>"
            b"<entry><title>Press release A</title><updated>2026-07-03</updated>"
            b'<link href="https://sec.gov/a"/></entry></feed>')


# ---------------------------------------------------------------------- tests
class TestEdgarContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FixtureServer()
        cls.srv.routes["/tickers"] = (200, "application/json", TICKERS_JSON)
        cls.srv.routes["/subs"] = (200, "application/json", SUBMISSIONS_JSON)
        cls.srv.routes["/facts"] = (200, "application/json", COMPANYFACTS_JSON)
        cls._saved = (edgar_mod.TICKERS_URL, edgar_mod.SUBMISSIONS_URL, edgar_mod.FACTS_URL)
        edgar_mod.TICKERS_URL = cls.srv.url("/tickers")
        edgar_mod.SUBMISSIONS_URL = cls.srv.url("/subs?cik={cik:010d}")
        edgar_mod.FACTS_URL = cls.srv.url("/facts?cik={cik:010d}")

    @classmethod
    def tearDownClass(cls):
        edgar_mod.TICKERS_URL, edgar_mod.SUBMISSIONS_URL, edgar_mod.FACTS_URL = cls._saved
        cls.srv.close()

    def setUp(self):
        self.edgar = edgar_mod.EdgarClient(DB(":memory:"), "test test@example.com", rps=100)

    def test_ticker_map_and_cik(self):
        self.assertEqual(self.edgar.cik_for("intc"), 50863)   # case-insensitive
        self.assertIsNone(self.edgar.cik_for("NOPE"))

    def test_recent_filings_shape_and_filter(self):
        filings = self.edgar.recent_filings("INTC")
        self.assertEqual([f["form"] for f in filings], ["10-K", "8-K", "10-Q"])  # form 4 excluded
        for f in filings:
            self.assertRegex(f["filed"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(f["url"].startswith("https://www.sec.gov/Archives/"))

    def test_fundamentals_normalization(self):
        f = self.edgar.fundamentals("INTC")
        # tag fallback: Revenues absent, contract-revenue tag used instead
        self.assertEqual(f["revenue"][0]["val"], 55_000_000_000)
        # quarterly (10-Q) datapoint excluded from annual series
        self.assertEqual([r["fy"] for r in f["revenue"]], [2025, 2024])
        # newest-first ordering and dei shares pickup
        self.assertGreater(f["revenue"][0]["end"], f["revenue"][1]["end"])
        self.assertEqual(f["shares"][0]["val"], 4_300_000_000)

    def test_facts_cached_after_first_fetch(self):
        self.edgar.fundamentals("INTC")
        before = self.srv.hits.get("/facts", 0)
        self.edgar.fundamentals("INTC")
        self.assertEqual(self.srv.hits.get("/facts", 0), before)  # served from cache


class TestMarketContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FixtureServer()
        cls._saved = (market_mod.YAHOO_URL, market_mod.STOOQ_URL)

    @classmethod
    def tearDownClass(cls):
        market_mod.YAHOO_URL, market_mod.STOOQ_URL = cls._saved
        cls.srv.close()

    def setUp(self):
        self.srv.routes.clear()
        self.srv.hits.clear()
        self.db = DB(":memory:")
        self.md = market_mod.MarketData(self.db, rps=100)

    def test_yahoo_null_rows_dropped(self):
        self.srv.routes["/yhoo"] = (200, "application/json", YAHOO_CHART_JSON)
        market_mod.YAHOO_URL = self.srv.url("/yhoo?t={ticker}")
        market_mod.STOOQ_URL = self.srv.url("/nostooq")
        bars = self.md.daily_bars("SPY")
        self.assertEqual(len(bars), 2)                       # None close dropped
        self.assertEqual(bars[-1]["close"], 621.7)
        self.assertRegex(bars[0]["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_stooq_fallback_skips_junk_rows(self):
        self.srv.routes["/yhoo"] = (500, "text/plain", b"upstream error")
        self.srv.routes["/stooq"] = (200, "text/csv", STOOQ_CSV)
        market_mod.YAHOO_URL = self.srv.url("/yhoo?t={ticker}")
        market_mod.STOOQ_URL = self.srv.url("/stooq")
        bars = self.md.daily_bars("SPY")
        self.assertEqual([b["date"] for b in bars], ["2026-06-30", "2026-07-02"])

    def test_stale_cache_when_all_sources_down(self):
        self.srv.routes["/yhoo"] = (200, "application/json", YAHOO_CHART_JSON)
        market_mod.YAHOO_URL = self.srv.url("/yhoo?t={ticker}")
        market_mod.STOOQ_URL = self.srv.url("/nostooq")
        first = self.md.daily_bars("SPY")
        self.assertTrue(first)
        # sources die AND the fresh 1h cache expires -> stale tier must serve
        self.srv.routes["/yhoo"] = (500, "text/plain", b"down")
        with self.db.conn() as c:
            c.execute("UPDATE kv_cache SET expires_at=0 WHERE key LIKE 'mkt:bars:SPY%'")
        self.assertEqual(self.md.daily_bars("SPY"), first)


class TestFredContract(unittest.TestCase):
    def test_missing_value_placeholders_skipped(self):
        srv = FixtureServer()
        srv.routes["/fred"] = (200, "application/json", FRED_JSON)
        saved = macro_mod.FRED_URL
        macro_mod.FRED_URL = srv.url("/fred")
        try:
            m = macro_mod.MacroData(DB(":memory:"), api_key="testkey")
            snap = m.snapshot()
            self.assertTrue(snap["available"])
            self.assertEqual(snap["fed_funds"], 4.33)         # "." rows skipped
        finally:
            macro_mod.FRED_URL = saved
            srv.close()


class TestNewsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FixtureServer()
        cls.srv.routes["/rss"] = (200, "application/xml", RSS_XML)
        cls.srv.routes["/atom"] = (200, "application/xml", ATOM_XML)
        cls.srv.routes["/broken"] = (200, "application/xml", b"<not-xml")

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def _feed(self):
        return news_mod.NewsFeed(DB(":memory:"))

    def test_rss2_including_missing_link(self):
        items = self._feed()._parse(self.srv.url("/rss"), limit=10)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Intel announces buyback")
        self.assertEqual(items[1]["link"], "")               # absent link tolerated

    def test_atom_feed(self):
        items = self._feed()._parse(self.srv.url("/atom"), limit=10)
        self.assertEqual(items[0]["title"], "Press release A")
        self.assertEqual(items[0]["link"], "https://sec.gov/a")

    def test_broken_xml_returns_empty_not_crash(self):
        self.assertEqual(self._feed()._parse(self.srv.url("/broken"), limit=10), [])


class TestThrottleAndRetry(unittest.TestCase):
    def test_rate_limiter_spaces_requests(self):
        srv = FixtureServer()
        srv.routes["/ok"] = (200, "text/plain", b"ok")
        try:
            http = RateLimitedHTTP(rps=10)                    # 100ms spacing
            t0 = time.monotonic()
            for _ in range(4):
                http.get(srv.url("/ok"))
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, 0.28)            # >= 3 gaps of ~100ms
        finally:
            srv.close()

    def test_429_backs_off_then_succeeds(self):
        srv = FixtureServer()
        srv.routes["/limited"] = (429, "text/plain", b"ok-after-backoff")
        try:
            http = RateLimitedHTTP(rps=100, retries=3)
            t0 = time.monotonic()
            resp = http.get(srv.url("/limited"))
            self.assertEqual(resp.text, "ok-after-backoff")
            self.assertGreaterEqual(time.monotonic() - t0, 4.5)  # observed the 5s backoff
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
