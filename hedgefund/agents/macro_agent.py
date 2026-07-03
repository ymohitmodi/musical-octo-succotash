"""Macro strategist: regime awareness for sizing and cash levels only.

Constitution P6: macro informs position sizing and cash buffers — it never
creates single-name conviction and never triggers market-timing trades."""

from __future__ import annotations

import json
import time

from ..llm import LLMClient
from ..storage import DB
from .base import DISCLAIMER, load_active_genome


class MacroStrategist:
    name = "macro"
    default_genome = {"temperature": 0.3, "caution": 0.5}

    def __init__(self, llm: LLMClient, db: DB):
        self.llm = llm
        self.db = db
        self.genome_id, self.genome = load_active_genome(db, self.name, self.default_genome)

    def regime(self, macro_snapshot: dict, market_news: list[dict]) -> dict:
        """Return {regime, risk_multiplier (0.5-1.0), cash_target_pct, rationale}."""
        cache = self.db.cache_get("macro:regime")
        if cache:
            return cache
        try:
            out = self.llm.chat_json(
                system=(DISCLAIMER + "\n\nROLE: Macro strategist. Classify the current "
                        "regime and recommend a portfolio risk posture. You NEVER pick "
                        "stocks and NEVER recommend going to zero equities; your "
                        "risk_multiplier is bounded [0.5, 1.0]."),
                user=f"""MACRO DATA (FRED): {json.dumps(macro_snapshot, default=str)}
MARKET HEADLINES: {json.dumps(market_news, default=str)}
Caution disposition: {self.genome.get('caution', 0.5)} (0=aggressive, 1=defensive)

Classify the regime (expansion / late-cycle / contraction / crisis / recovery)
and set risk posture.""",
                schema_hint=('{"regime": "...", "risk_multiplier": 0.5-1.0, '
                             '"cash_target_pct": 0.10-0.35, "rationale": "..."}'),
                temperature=float(self.genome.get("temperature", 0.3)),
            )
        except Exception:  # noqa: BLE001 - macro is advisory; default = neutral
            out = {"regime": "unknown", "risk_multiplier": 0.8,
                   "cash_target_pct": 0.15, "rationale": "LLM unavailable; neutral default"}
        out["risk_multiplier"] = min(1.0, max(0.5, float(out.get("risk_multiplier", 0.8))))
        out["cash_target_pct"] = min(0.35, max(0.10, float(out.get("cash_target_pct", 0.15))))
        self.db.cache_set("macro:regime", out, ttl_sec=6 * 3600)
        self.db.insert("analyses", ts=time.time(), ticker="_MACRO_", agent=self.name,
                       genome_id=self.genome_id, verdict=str(out.get("regime", "unknown")),
                       confidence=50.0, report=json.dumps(out, default=str))
        return out
