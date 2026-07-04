"""Free market data with redundant sources (resilience through fallback).

Primary: Yahoo Finance public chart API (no key).
Fallback: Stooq daily CSV (no key).
Both are cached in SQLite so a total outage still leaves us with recent data.
"""

from __future__ import annotations

import csv
import io
import logging
import statistics

from ..storage import DB
from .http import RateLimitedHTTP

log = logging.getLogger("hedgefund.market")

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
STOOQ_URL = "https://stooq.com/q/d/l/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class MarketData:
    def __init__(self, db: DB, rps: float = 2.0):
        self.db = db
        self.http = RateLimitedHTTP(rps=rps, user_agent=UA)

    # ------------------------------------------------------------------ bars
    def daily_bars(self, ticker: str, days: int = 260) -> list[dict]:
        """Newest-last [{date, open, high, low, close, volume}]."""
        key = f"mkt:bars:{ticker}:{days}"
        cached = self.db.cache_get(key)
        if cached:
            return cached
        bars = self._yahoo_bars(ticker, days) or self._stooq_bars(ticker, days)
        if bars:
            self.db.cache_set(key, bars, ttl_sec=3600)
            # long-TTL emergency copy so outages degrade instead of blinding us
            self.db.cache_set(f"mkt:bars:stale:{ticker}", bars, ttl_sec=14 * 86400)
            return bars
        stale = self.db.cache_get(f"mkt:bars:stale:{ticker}")
        if stale:
            log.warning("using stale bars for %s (all live sources down)", ticker)
            return stale
        return []

    def _yahoo_bars(self, ticker: str, days: int) -> list[dict] | None:
        try:
            rng = "1y" if days <= 260 else "2y"
            data = self.http.get(YAHOO_URL.format(ticker=ticker),
                                 params={"range": rng, "interval": "1d"}).json()
            result = data["chart"]["result"][0]
            ts = result["timestamp"]
            q = result["indicators"]["quote"][0]
            bars = []
            for i, t in enumerate(ts):
                if q["close"][i] is None:
                    continue
                from datetime import datetime, timezone
                d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                bars.append({"date": d, "open": q["open"][i], "high": q["high"][i],
                             "low": q["low"][i], "close": q["close"][i],
                             "volume": q["volume"][i] or 0})
            return bars[-days:] if bars else None
        except Exception as e:  # noqa: BLE001
            log.warning("yahoo bars failed for %s: %s", ticker, e)
            return None

    def _stooq_bars(self, ticker: str, days: int) -> list[dict] | None:
        try:
            resp = self.http.get(STOOQ_URL, params={"s": f"{ticker.lower()}.us", "i": "d"})
            rows = list(csv.DictReader(io.StringIO(resp.text)))
            bars = [{"date": r["Date"], "open": float(r["Open"]), "high": float(r["High"]),
                     "low": float(r["Low"]), "close": float(r["Close"]),
                     "volume": float(r.get("Volume") or 0)} for r in rows if r.get("Close")]
            return bars[-days:] if bars else None
        except Exception as e:  # noqa: BLE001
            log.warning("stooq bars failed for %s: %s", ticker, e)
            return None

    def full_history(self, ticker: str) -> list[dict]:
        """Complete daily history (Stooq first — decades of data — Yahoo 2y
        as fallback). Cached 24h; used by the backtester."""
        key = f"mkt:hist:{ticker}"
        cached = self.db.cache_get(key)
        if cached:
            return cached
        bars = self._stooq_bars(ticker, days=20000) or self._yahoo_bars(ticker, days=500)
        if bars:
            self.db.cache_set(key, bars, ttl_sec=24 * 3600)
        return bars or []

    # ----------------------------------------------------------------- quotes
    def last_price(self, ticker: str) -> float | None:
        bars = self.daily_bars(ticker, days=30)
        return bars[-1]["close"] if bars else None

    def snapshot(self, ticker: str) -> dict | None:
        """Price + liquidity + momentum stats used by screener and risk engine."""
        bars = self.daily_bars(ticker, days=260)
        if len(bars) < 30:
            return None
        closes = [b["close"] for b in bars]
        last = closes[-1]
        recent = bars[-20:]
        adv_shares = statistics.mean(b["volume"] for b in recent)
        adv_dollars = statistics.mean(b["volume"] * b["close"] for b in recent)
        year_high, year_low = max(closes), min(closes)
        ret_6m = last / closes[-126] - 1 if len(closes) >= 126 else None
        ret_1y = last / closes[0] - 1 if len(closes) >= 250 else None
        # annualized daily vol
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        vol = statistics.stdev(rets) * (252 ** 0.5) if len(rets) > 20 else None
        return {"ticker": ticker, "price": last, "adv_shares": adv_shares,
                "adv_dollars": adv_dollars, "year_high": year_high, "year_low": year_low,
                "pct_off_high": last / year_high - 1, "ret_6m": ret_6m, "ret_1y": ret_1y,
                "ann_vol": vol}
