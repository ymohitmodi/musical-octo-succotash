"""LLM budget governor — 24/7 autonomy on a usage-capped cloud plan.

Ollama cloud plans meter GPU time with session (5-hour) and weekly caps.
An autonomous fund must NEVER silently exhaust its brain mid-week: this
governor tracks daily spend in budget units (heavy model call = 3 units,
light = 1, mirroring Ollama's model usage levels) and cuts off LLM calls
when the daily allowance is gone.

Degradation is fail-closed by design: no LLM => no new buys (the committee
can't convene), while everything deterministic — screening, exploration,
mark-to-market, stop-losses, drawdown breakers — keeps running untouched.
A fund that can't think stops trading; it never stops protecting capital.
"""

from __future__ import annotations

import time

from ..storage import DB

TIER_COST = {"heavy": 3, "light": 1}


class BudgetExceededError(RuntimeError):
    pass


class BudgetGovernor:
    def __init__(self, db: DB, daily_units: int):
        self.db = db
        self.daily_units = int(daily_units)

    def _key(self) -> str:
        return "llm:budget:" + time.strftime("%Y-%m-%d", time.gmtime())

    def used_today(self) -> int:
        return int(self.db.cache_get(self._key()) or 0)

    def remaining(self) -> int:
        return max(0, self.daily_units - self.used_today())

    def spend(self, tier: str = "heavy") -> None:
        """Record one logical LLM call; raise when the day's budget is gone."""
        if self.daily_units <= 0:          # 0 disables governing (self-hosted)
            return
        cost = TIER_COST.get(tier, TIER_COST["heavy"])
        used = self.used_today()
        if used + cost > self.daily_units:
            raise BudgetExceededError(
                f"daily LLM budget exhausted ({used}/{self.daily_units} units); "
                "deterministic safety rails remain active")
        # 48h TTL so yesterday's key ages out on its own
        self.db.cache_set(self._key(), used + cost, ttl_sec=48 * 3600)

    def status(self) -> dict:
        return {"daily_units": self.daily_units, "used_today": self.used_today(),
                "remaining": self.remaining()}
