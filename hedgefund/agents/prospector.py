"""The Prospector: 24/7 idea sourcing beyond the static universe.

World-class small funds don't wait for ideas to arrive — they run standing
hunts across the whole market: the 52-week-low list, insider-buying
clusters, spinoffs, post-crash quality, Graham net-nets. Each hunt is a
CHANNEL here; the scheduler rotates through them overnight, every night,
so the fund is exploring while the market sleeps.

Channels are pure quant + EDGAR (no LLM tokens). Their output is `leads`:
tickers with a stated reason, scored by the screener. Strong leads join the
evening deep-dive queue; committee-endorsed-but-expensive names go to the
watchlist and get stalked for their price.
"""

from __future__ import annotations

import logging
import time

from ..data import EdgarClient, MarketData
from ..storage import DB
from .screener import Screener

log = logging.getLogger("hedgefund.prospector")

CHANNELS = ("deep_drawdown", "net_nets", "crash_with_quality",
            "insider_activity", "spinoffs")


class Prospector:
    def __init__(self, db: DB, edgar: EdgarClient, market: MarketData,
                 screener: Screener, universe: list[dict]):
        self.db = db
        self.edgar = edgar
        self.market = market
        self.screener = screener
        self.universe = universe

    # ------------------------------------------------------------- channels
    def _pool(self, extra_n: int = 60) -> list[dict]:
        """Universe seeds + a rotating sample of the whole SEC ticker file."""
        return self.screener.expanded_universe(list(self.universe), extra_n)

    def ch_deep_drawdown(self) -> list[dict]:
        """The 52-week-low list: names 40%+ off their high with real liquidity."""
        leads = []
        for entry in self._pool():
            snap = self._snap(entry["ticker"])
            if snap and snap["pct_off_high"] <= -0.40 and snap["adv_dollars"] > 2e6:
                leads.append({"ticker": entry["ticker"],
                              "note": f"{snap['pct_off_high']:.0%} off 52w high"})
        return leads

    def ch_net_nets(self) -> list[dict]:
        """Graham net-nets: price below net current asset value."""
        from .. import quant
        leads = []
        for entry in self._pool(40):
            snap = self._snap(entry["ticker"])
            if not snap:
                continue
            f = self._facts(entry["ticker"])
            if not f:
                continue
            m = quant.valuation_metrics(f, snap["price"], None)
            p_ncav = m.get("price_to_ncav")
            if p_ncav is not None and p_ncav < 1.0:
                leads.append({"ticker": entry["ticker"],
                              "note": f"net-net: P/NCAV {p_ncav:.2f}"})
        return leads

    def ch_crash_with_quality(self) -> list[dict]:
        """Recent sharp fallers whose fundamentals are IMPROVING (F >= 6)."""
        from .. import quant
        leads = []
        for entry in self._pool(40):
            snap = self._snap(entry["ticker"])
            if not snap or snap.get("ret_6m") is None or snap["ret_6m"] > -0.25:
                continue
            f = self._facts(entry["ticker"])
            if not f:
                continue
            fs = quant.piotroski_f(f)
            if fs["f_max"] >= 6 and fs["f_score"] >= 6:
                leads.append({"ticker": entry["ticker"],
                              "note": f"6m {snap['ret_6m']:.0%} but F={fs['f_score']}/{fs['f_max']}"})
        return leads

    def ch_insider_activity(self) -> list[dict]:
        """Clusters of insider Form 4 filings on beaten-down names (a costly
        signal: insiders buy for one reason)."""
        leads = []
        for entry in self.universe:  # per-ticker EDGAR calls: seeds only
            snap = self._snap(entry["ticker"])
            if not snap or snap["pct_off_high"] > -0.20:
                continue
            try:
                form4 = self.edgar.recent_filings(entry["ticker"], forms=("4",), limit=12)
            except Exception:  # noqa: BLE001
                continue
            cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
            recent = [f for f in form4 if f["filed"] >= cutoff]
            if len(recent) >= 3:
                leads.append({"ticker": entry["ticker"],
                              "note": f"{len(recent)} Form 4 filings in 30d, "
                                      f"{snap['pct_off_high']:.0%} off high"})
        return leads

    def ch_spinoffs(self) -> list[dict]:
        """Fresh spinoffs (Form 10 registrations) — classic neglected-asset pond."""
        leads = []
        try:
            hits = self.edgar.full_text_search("spin-off", forms="10-12B", limit=10)
        except Exception:  # noqa: BLE001
            return []
        tmap = {v["name"].upper(): t for t, v in self.edgar.ticker_map().items()}
        for h in hits:
            company = (h.get("company") or "").upper()
            for name, ticker in tmap.items():
                if company and (company in name or name in company):
                    leads.append({"ticker": ticker, "note": f"Form 10 spinoff: {company}"})
                    break
        return leads

    # -------------------------------------------------------------- plumbing
    def _snap(self, ticker: str):
        try:
            return self.market.snapshot(ticker)
        except Exception:  # noqa: BLE001
            return None

    def _facts(self, ticker: str):
        try:
            return self.edgar.fundamentals(ticker)
        except Exception:  # noqa: BLE001
            return None

    def explore(self, channel: str) -> dict:
        """Run one channel, score its leads with the real screener, persist."""
        fn = getattr(self, f"ch_{channel}", None)
        if fn is None:
            return {"channel": channel, "error": "unknown channel"}
        started = time.time()
        raw = fn()
        scored = 0
        for lead in raw:
            score = None
            try:
                result = self.screener.screen_one(lead["ticker"], "unknown")
                score = result["score"] if result else None
            except Exception:  # noqa: BLE001
                pass
            self.db.insert("leads", ts=time.time(), ticker=lead["ticker"],
                           source=channel, note=lead["note"], score=score)
            scored += 1 if score is not None else 0
        summary = {"channel": channel, "raw_leads": len(raw),
                   "passed_screen": scored,
                   "duration_sec": round(time.time() - started)}
        self.db.log_event("exploration", summary)
        log.info("exploration %s: %d leads, %d screened in", channel, len(raw), scored)
        return summary

    def fresh_lead_candidates(self, days: float = 3.0) -> list[dict]:
        """Screened leads from recent exploration, best-first, for deep dives."""
        rows = self.db.query(
            "SELECT ticker, source, note, MAX(score) AS score FROM leads "
            "WHERE ts > ? AND score IS NOT NULL GROUP BY ticker "
            "ORDER BY score DESC LIMIT 20", (time.time() - days * 86400,))
        return [dict(r) for r in rows]
