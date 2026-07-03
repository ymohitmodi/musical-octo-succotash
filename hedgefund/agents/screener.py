"""Quantitative deep-value screener — pure math, no LLM tokens spent.

The screener is the factory's intake conveyor: it grinds through the universe
nightly and only the highest-composite-score names earn expensive LLM
deep-dives. Its scoring weights are an evolvable genome.
"""

from __future__ import annotations

import json
import logging
import random
import time

from .. import quant
from ..data import EdgarClient, MarketData
from ..storage import DB
from .base import load_active_genome

log = logging.getLogger("hedgefund.screener")


class Screener:
    name = "screener"
    default_genome = {
        "w_valuation": 0.45, "w_quality": 0.30, "w_safety": 0.15, "w_contrarian": 0.10,
        "min_composite": 55.0,
    }

    def __init__(self, db: DB, edgar: EdgarClient, market: MarketData, hard_limits: dict):
        self.db = db
        self.edgar = edgar
        self.market = market
        self.limits = hard_limits
        self.genome_id, self.genome = load_active_genome(db, self.name, self.default_genome)

    # -------------------------------------------------------------------------
    def screen_universe(self, universe: list[dict]) -> list[dict]:
        """Score every ticker; persist and return survivors sorted best-first."""
        survivors = []
        for entry in universe:
            ticker, sector = entry["ticker"], entry.get("sector", "unknown")
            try:
                result = self.screen_one(ticker, sector)
            except Exception as e:  # noqa: BLE001 - one bad ticker never stops the belt
                log.warning("screen failed for %s: %s", ticker, e)
                self.db.log_event("error", {"stage": "screen", "error": str(e)}, ticker)
                continue
            if result:
                survivors.append(result)
        survivors.sort(key=lambda r: r["score"], reverse=True)
        self.db.log_event("screen", {"universe": len(universe), "survivors": len(survivors),
                                     "top": [s["ticker"] for s in survivors[:10]]})
        return survivors

    def screen_one(self, ticker: str, sector: str) -> dict | None:
        snap = self.market.snapshot(ticker)
        if not snap:
            return None
        # Constitution hard gates first — cheap rejections before EDGAR calls.
        if snap["price"] < self.limits["min_price"]:
            return None
        if snap["adv_dollars"] < self.limits["min_avg_dollar_volume"]:
            return None

        f = self.edgar.fundamentals(ticker)
        if not f:
            return None
        metrics = quant.valuation_metrics(f, snap["price"], None)
        if not metrics or (metrics.get("market_cap") or 0) < self.limits["min_market_cap"]:
            return None

        fscore = quant.piotroski_f(f)
        z = quant.altman_z(f, metrics.get("market_cap"))
        m = quant.beneish_m(f)

        # Hard quality floors from the constitution.
        if z is not None and z < self.limits["min_altman_z"]:
            return None
        if m is not None and m > self.limits["max_beneish_m"]:
            self.db.log_event("veto", {"reason": "beneish_m_flag", "m": m}, ticker)
            return None

        weights = {"valuation": self.genome["w_valuation"], "quality": self.genome["w_quality"],
                   "safety": self.genome["w_safety"], "contrarian": self.genome["w_contrarian"]}
        score = quant.composite_value_score(metrics, fscore, z, m, snap, weights)
        if score < float(self.genome.get("min_composite", 55)):
            return None

        record = {"ticker": ticker, "sector": sector, "score": score,
                  "metrics": metrics, "f_score": fscore, "altman_z": z, "beneish_m": m,
                  "snapshot": snap}
        self.db.insert("candidates", ticker=ticker, ts=time.time(), sector=sector,
                       score=score, metrics=json.dumps(record, default=str))
        return record

    # -------------------------------------------------------------------------
    def expanded_universe(self, base: list[dict], sample_n: int) -> list[dict]:
        """Weekend mode: sample extra names from the full SEC ticker file."""
        try:
            tmap = self.edgar.ticker_map()
        except Exception as e:  # noqa: BLE001
            log.warning("SEC ticker map unavailable: %s", e)
            return base
        known = {e["ticker"] for e in base}
        pool = [t for t in tmap if t.isalpha() and t not in known and len(t) <= 5]
        rng = random.Random(int(time.time() // 604800))  # stable within a week
        extra = [{"ticker": t, "sector": "unknown"} for t in rng.sample(pool, min(sample_n, len(pool)))]
        return base + extra
