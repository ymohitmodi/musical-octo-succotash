"""Macro regime data from FRED (free API key) with graceful degradation."""

from __future__ import annotations

import logging

from ..storage import DB
from .http import RateLimitedHTTP

log = logging.getLogger("hedgefund.macro")

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "fed_funds": "DFF",
    "ten_year_yield": "DGS10",
    "two_year_yield": "DGS2",
    "cpi_yoy": "CPIAUCSL",
    "unemployment": "UNRATE",
    "hy_spread": "BAMLH0A0HYM2",
    "vix": "VIXCLS",
}


class MacroData:
    def __init__(self, db: DB, api_key: str = ""):
        self.db = db
        self.api_key = api_key
        self.http = RateLimitedHTTP(rps=1.0, user_agent="hedgefund/0.1")

    def snapshot(self) -> dict:
        """Latest macro readings; empty-but-valid dict when FRED key is absent."""
        if not self.api_key:
            return {"available": False,
                    "note": "FRED_API_KEY not set; macro agent runs on price data only"}
        cached = self.db.cache_get("macro:snapshot")
        if cached:
            return cached
        out: dict = {"available": True}
        for name, series in SERIES.items():
            try:
                data = self.http.get(FRED_URL, params={
                    "series_id": series, "api_key": self.api_key, "file_type": "json",
                    "sort_order": "desc", "limit": 14,
                }).json()
                obs = [o for o in data.get("observations", []) if o.get("value") not in (".", None)]
                if obs:
                    out[name] = float(obs[0]["value"])
                    if name == "cpi_yoy" and len(obs) >= 13:
                        out[name] = float(obs[0]["value"]) / float(obs[12]["value"]) - 1
            except Exception as e:  # noqa: BLE001
                log.warning("FRED %s failed: %s", series, e)
        if out.get("ten_year_yield") is not None and out.get("two_year_yield") is not None:
            out["yield_curve_2s10s"] = out["ten_year_yield"] - out["two_year_yield"]
        self.db.cache_set("macro:snapshot", out, ttl_sec=6 * 3600)
        return out
