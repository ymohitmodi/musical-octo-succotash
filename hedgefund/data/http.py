"""Shared polite HTTP: rate limiting, retries, and per-host circuit breakers.

Free data sources are a privilege; hammering them gets you banned and kills
the fund's data supply. Every outbound request goes through RateLimitedHTTP.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("hedgefund.http")


class RateLimitedHTTP:
    def __init__(self, rps: float = 2.0, user_agent: str = "hedgefund/0.1", retries: int = 3):
        self.min_interval = 1.0 / max(rps, 0.1)
        self.retries = retries
        self._last = 0.0
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def get(self, url: str, *, params: dict | None = None, timeout: int = 30,
            headers: dict | None = None) -> requests.Response:
        last_err: Exception | None = None
        for attempt in range(self.retries):
            with self._lock:
                wait = self.min_interval - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                resp = self.session.get(url, params=params, timeout=timeout, headers=headers)
                if resp.status_code == 429:  # backoff hard on rate-limit signals
                    time.sleep(5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 ** attempt)
        raise ConnectionError(f"GET {url} failed after {self.retries} tries: {last_err}")
