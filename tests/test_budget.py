"""Budget governor tests: 24/7 autonomy on a usage-capped Ollama plan must
degrade fail-closed (no new buys) while deterministic rails keep running."""

from __future__ import annotations

import unittest

from hedgefund.llm import BudgetExceededError, BudgetGovernor, LLMClient
from hedgefund.storage import DB


class TestBudgetGovernor(unittest.TestCase):
    def setUp(self):
        self.db = DB(":memory:")

    def test_spend_and_exhaust(self):
        g = BudgetGovernor(self.db, daily_units=7)
        g.spend("heavy")   # 3
        g.spend("heavy")   # 6
        g.spend("light")   # 7 — exactly at the cap is allowed
        self.assertEqual(g.remaining(), 0)
        with self.assertRaises(BudgetExceededError):
            g.spend("light")

    def test_heavy_costs_more_than_light(self):
        g = BudgetGovernor(self.db, daily_units=6)
        g.spend("light"); g.spend("light"); g.spend("light")  # 3 units
        g.spend("heavy")                                      # 6 units
        with self.assertRaises(BudgetExceededError):
            g.spend("light")

    def test_zero_budget_disables_governing(self):
        g = BudgetGovernor(self.db, daily_units=0)
        for _ in range(100):
            g.spend("heavy")   # never raises: self-hosted / unlimited mode
        self.assertEqual(g.status()["used_today"], 0)

    def test_client_charges_budget_before_calling(self):
        g = BudgetGovernor(self.db, daily_units=3)
        client = LLMClient(host="http://127.0.0.1:1", models=["m"], budget=g,
                           max_retries=1, timeout=1)
        # first call: budget charged (3/3), then network fails -> LLMError
        with self.assertRaises(Exception):
            client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(g.remaining(), 0)
        # second call: blocked by budget BEFORE any network attempt
        with self.assertRaises(BudgetExceededError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_light_tier_uses_light_chain(self):
        calls = []

        class Probe(LLMClient):
            def _call(self, model, messages, temperature):
                calls.append(model)
                return "ok"

        c = Probe(host="http://x", models=["heavy-a", "heavy-b"],
                  light_models=["light-a"])
        c.chat([{"role": "user", "content": "hi"}], tier="light")
        self.assertEqual(calls, ["light-a"])
        c.chat([{"role": "user", "content": "hi"}], tier="heavy")
        self.assertEqual(calls[-1], "heavy-a")

    def test_preferred_model_tried_first_with_fallback(self):
        calls = []

        class Flaky(LLMClient):
            def _call(self, model, messages, temperature):
                calls.append(model)
                if model == "bear-model":
                    raise RuntimeError("down")
                return "ok"

        c = Flaky(host="http://x", models=["heavy-a"], max_retries=1)
        out = c.chat([{"role": "user", "content": "hi"}], model="bear-model")
        self.assertEqual(out, "ok")
        self.assertEqual(calls, ["bear-model", "heavy-a"])  # diversity + resilience


if __name__ == "__main__":
    unittest.main()
