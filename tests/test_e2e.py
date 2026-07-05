"""End-to-end integration test of the dark factory — fully offline.

Drives the REAL pipeline, broker, constitution engine, evolution engine and
dashboard API through a complete fund lifecycle using a deterministic fake
LLM and fake data sources:

  1. research cycle: screen -> committee -> PM -> constitution -> BUY executed
  2. forensic veto path: bearish forensic report blocks a trade (fail-closed)
  3. monitor cycle: mark-to-market, then a 30% price drop triggers the
     mechanical stop-loss and closes the decision
  4. evolution: fitness computed from the closed round-trip, genome gen-1 bred
  5. daily report + dashboard API summary render the whole history

Only the LLM and the network are faked — every decision, veto, clamp, fill,
stop, fitness computation and journal row is produced by production code.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hedgefund.config import Config
from hedgefund.evolution import Evolver
from hedgefund.orchestrator.pipeline import Pipeline

try:
    from test_core import fake_fundamentals          # discover mode (cwd=tests)
except ImportError:
    from tests.test_core import fake_fundamentals    # package mode


# --------------------------------------------------------------------- fakes
class FakeLLM:
    """Deterministic stand-in for LLMClient; routes on role text in prompts."""

    def __init__(self):
        self.forensic_verdict = "bullish"
        self.pm_action = "buy"
        self.target_entry = None
        self.survivable = True
        self.checklist_pass = True
        self.calls = 0

    def chat(self, messages, **kw):
        return "ready"

    def health(self):
        return {"ok": True, "models_installed": ["fake"]}

    def chat_json(self, system: str, user: str, **kw) -> dict:
        self.calls += 1
        if "PRE-BUY CHECKLIST" in system:
            import re
            ids = re.findall(r"\[(CL\d+\w*)\]", user)
            ans = "pass" if self.checklist_pass else "fail"
            return {"answers": {i: {"answer": ans, "evidence": "per committee data"}
                                for i in ids}}
        if "mandatory pre-mortem" in system:
            return {"failure_causes": [
                        {"cause": "margin collapse", "probability": "medium",
                         "kill_criterion": "two quarters of gross margin < 30%"}],
                    "survivable": self.survivable, "reasoning": "sized modestly"}
        if "post-mortem" in system:
            return {"category": "thesis",
                    "lesson": "Do not trust peak-cycle margins in commodity businesses."}
        if "CONSTITUTIONAL CRITIC" in system:
            return {"approved": True, "revised_weight": None, "violations": [],
                    "reasoning": "complies with all principles"}
        if "Portfolio manager of a deep-value fund" in system:
            return {"action": self.pm_action, "conviction": 82, "target_weight": 0.03,
                    "target_entry": self.target_entry,
                    "thesis": "Simple business trading far below intrinsic value.",
                    "invalidation_triggers": ["margin collapse"]}
        if "Macro strategist" in system:
            return {"regime": "expansion", "risk_multiplier": 1.0,
                    "cash_target_pct": 0.10, "rationale": "benign"}
        if "reviewing an open position" in system:
            return {"action": "hold", "reason": "thesis intact"}
        if "Forensic accountant" in system:
            return {"verdict": self.forensic_verdict, "confidence": 80,
                    "thesis": "books look clean", "key_risks": [], "red_flags": []}
        return {"verdict": "bullish", "confidence": 75,
                "thesis": "cheap and durable", "key_risks": ["cyclicality"],
                "red_flags": [], "estimated_upside_pct": 45}


class FakeMarket:
    """Same interface as MarketData; price is mutable to simulate moves."""

    def __init__(self):
        self.prices = {"TEST": 20.0, "SPY": 500.0}
        self.snap_off_high = -0.375

    def last_price(self, ticker):
        return self.prices.get(ticker)

    def snapshot(self, ticker):
        p = self.prices.get(ticker)
        if p is None:
            return None
        return {"ticker": ticker, "price": p, "adv_shares": 4_000_000,
                "adv_dollars": 4_000_000 * p, "year_high": p * 1.6, "year_low": p * 0.9,
                "pct_off_high": self.snap_off_high, "ret_6m": -0.2, "ret_1y": -0.3,
                "ann_vol": 0.3}

    def daily_bars(self, ticker, days=260):
        p = self.prices.get(ticker, 0)
        return [{"date": "2026-07-01", "open": p, "high": p, "low": p,
                 "close": p, "volume": 4_000_000}] * min(days, 260)


class FakeEdgar:
    def fundamentals(self, ticker, years=4):
        return fake_fundamentals() if ticker == "TEST" else None

    def recent_filings(self, ticker, **kw):
        return [{"form": "10-K", "filed": "2026-02-15", "url": "https://example"}]


class FakeNews:
    def for_ticker(self, ticker, limit=8):
        return [{"title": f"{ticker} announces buyback", "date": "", "link": ""}]

    def market_wide(self, limit=10):
        return [{"title": "Markets steady", "date": "", "link": ""}]


class FakeMacroData:
    def snapshot(self):
        return {"available": False, "note": "test"}


def build_pipeline(tmpdir: str) -> tuple[Pipeline, FakeLLM, FakeMarket]:
    cfg = Config.load()
    cfg.settings["data"]["cache_dir"] = tmpdir           # isolate DB
    cfg.universe = {"seeds": [{"ticker": "TEST", "sector": "technology"}],
                    "auto_expand": False}
    pipe = Pipeline(cfg)
    fake_llm, fake_mkt = FakeLLM(), FakeMarket()
    fake_edgar, fake_news, fake_macro = FakeEdgar(), FakeNews(), FakeMacroData()
    pipe.llm = fake_llm
    pipe.market = fake_mkt
    pipe.edgar = fake_edgar
    pipe.news = fake_news
    pipe.macro_data = fake_macro
    pipe.broker.market = fake_mkt
    pipe.screener.edgar = fake_edgar
    pipe.screener.market = fake_mkt
    pipe.prospector.edgar = fake_edgar
    pipe.prospector.market = fake_mkt
    pipe.constitution.llm = fake_llm
    pipe.pm.llm = fake_llm
    pipe.macro_agent.llm = fake_llm
    for a in pipe.analysts:
        a.llm = fake_llm
    return pipe, fake_llm, fake_mkt


# ---------------------------------------------------------------------- tests
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.pipe, self.llm, self.mkt = build_pipeline(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_lifecycle(self):
        pipe, db = self.pipe, self.pipe.db

        # 1 ---- research cycle produces an executed, constitution-approved buy
        summary = pipe.research_cycle()
        self.assertEqual(summary["buys"], ["TEST"])
        positions = pipe.broker.positions()
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        nav, cash, invested = pipe.broker.nav()
        self.assertAlmostEqual(nav, 1_500_000, delta=1_500_000 * 0.001)  # only slippage lost
        # hard 5% cap respected (PM asked 3%)
        self.assertLessEqual(pos["market_value"] / nav, 0.05)
        decision = dict(db.query("SELECT * FROM decisions WHERE action='buy'")[0])
        self.assertEqual(decision["status"], "open")
        self.assertIn("intrinsic value", decision["thesis"])
        review = json.loads(decision["constitution_review"])
        self.assertTrue(review["approved"])
        genome_ids = json.loads(decision["genome_ids"])
        self.assertIn("forensic", genome_ids)          # full committee on record
        self.assertIn("pm", genome_ids)
        # journal captured every stage
        kinds = {r["kind"] for r in db.query("SELECT DISTINCT kind FROM events")}
        self.assertTrue({"screen", "decision", "trade", "research_cycle"} <= kinds)

        # idempotence: second cycle must NOT buy the same name again
        summary2 = pipe.research_cycle()
        self.assertEqual(summary2["buys"], [])
        self.assertEqual(len(pipe.broker.positions()), 1)

        # decision memo carries the full paper trail: pre-mortem + checklist
        self.assertTrue(review["premortem"]["survivable"])
        self.assertTrue(review["checklist"]["passed"])
        self.assertIn("CL2_MARGIN", review["checklist"]["answers"])

        # 2 ---- monitor: mark-to-market then stop-loss on a 30% drop
        pipe.monitor_cycle()
        self.assertGreater(len(db.query("SELECT * FROM nav_history")), 0)
        self.mkt.prices["TEST"] = 20.0 * 0.70
        out = pipe.monitor_cycle()
        self.assertEqual(len(out["stop_exits"]), 1)
        self.assertEqual(pipe.broker.positions(), [])
        # the exit produced a post-mortem lesson (institutional memory)...
        lessons = db.query("SELECT * FROM lessons")
        self.assertEqual(len(lessons), 1)
        self.assertIn("peak-cycle margins", lessons[0]["lesson"])
        # ...which is injected into every future analyst prompt
        self.assertIn("peak-cycle margins", pipe.analysts[0].system_prompt())
        self.assertEqual(db.query("SELECT status FROM decisions WHERE action='buy'")[0]["status"],
                         "closed")
        nav2, cash2, _ = pipe.broker.nav()
        self.assertLess(nav2, 1_500_000)               # loss realized honestly
        self.assertAlmostEqual(nav2, cash2)            # fully back in cash

        # 3 ---- evolution: fitness from the closed round-trip, gen-1 bred
        ev = Evolver(db, {"min_closed_decisions_for_fitness": 1, "mutation_rate": 1.0})
        fitness = ev.compute_fitness()
        self.assertTrue(fitness)                       # some genome got scored
        losing = [f for gid, f in fitness.items()
                  if gid == genome_ids["fundamental"]]
        self.assertTrue(losing and losing[0] < 0)      # bull agent penalized for loss
        bear_fit = fitness.get(genome_ids["bear"])
        if bear_fit is not None:
            self.assertGreater(bear_fit, 0)            # bear rewarded (inverted score)
        evolved = ev.evolve_all(["fundamental", "pm"])
        self.assertEqual(len(evolved), 2)
        gen1 = db.query("SELECT * FROM genomes WHERE agent='fundamental' AND active=1")
        self.assertEqual(gen1[0]["generation"], 1)

        # 4 ---- reporting surfaces the history
        report = pipe.daily_report()
        self.assertIn("TEST", report)
        self.assertIn("NAV", report)

        # 5 ---- dashboard API returns a coherent snapshot of the same DB
        from hedgefund.dashboard.server import _summary
        s = _summary(db)
        self.assertEqual(len(s["decisions"]), 1)       # held names never re-enter committee
        self.assertEqual(len(s["trades"]), 2)          # buy + stop-loss sell
        self.assertTrue(s["nav_history"])
        self.assertTrue(any(g["generation"] == 1 for g in s["genomes"]))

    def test_forensic_veto_blocks_trade_fail_closed(self):
        self.llm.forensic_verdict = "bearish"
        summary = self.pipe.research_cycle()
        self.assertEqual(summary["buys"], [])
        self.assertEqual(self.pipe.broker.positions(), [])
        vetoes = self.pipe.db.query("SELECT * FROM events WHERE kind='veto'")
        self.assertTrue(any("forensic" in r["payload"] for r in vetoes))

    def test_doctrine_reaches_every_agent_prompt(self):
        # the encoded teaching must actually be in front of the models
        for agent in self.pipe.analysts:
            self.assertIn("FUND DOCTRINE", agent.system_prompt())
        self.assertIn("margin of safety", self.pipe.analysts[0].system_prompt().lower())

    def test_watch_then_stalk_then_buy(self):
        # PM endorses the business but not the price -> watchlist with target
        self.llm.pm_action = "watch"
        self.llm.target_entry = 15.0
        summary = self.pipe.research_cycle()
        self.assertEqual(summary["buys"], [])
        row = self.pipe.db.query("SELECT * FROM watchlist")[0]
        self.assertEqual(row["ticker"], "TEST")
        self.assertEqual(row["target_entry"], 15.0)
        # price stays above target: nothing triggers
        self.pipe.monitor_cycle()
        self.assertEqual(self.pipe.db.query(
            "SELECT * FROM watchlist WHERE triggered=1"), [])
        # Mr. Market finally quotes the committee's price
        self.mkt.prices["TEST"] = 14.5
        out = self.pipe.monitor_cycle()
        self.assertEqual(out["watchlist_triggers"], ["TEST"])
        # next research cycle: the triggered name jumps the queue and is bought
        self.llm.pm_action = "buy"
        summary2 = self.pipe.research_cycle()
        self.assertEqual(summary2["buys"], ["TEST"])
        self.assertEqual(self.pipe.db.query("SELECT * FROM watchlist"), [])

    def test_checklist_failure_vetoes_buy(self):
        self.llm.checklist_pass = False
        summary = self.pipe.research_cycle()
        self.assertEqual(summary["buys"], [])
        vetoes = self.pipe.db.query(
            "SELECT payload FROM events WHERE kind='veto'")
        self.assertTrue(any("checklist" in r["payload"] for r in vetoes))

    def test_premortem_unsurvivable_vetoes_buy(self):
        self.llm.survivable = False
        summary = self.pipe.research_cycle()
        self.assertEqual(summary["buys"], [])
        vetoes = self.pipe.db.query("SELECT payload FROM events WHERE kind='veto'")
        self.assertTrue(any("premortem" in r["payload"] for r in vetoes))

    def test_prospector_channel_feeds_leads(self):
        self.mkt.snap_off_high = -0.55          # deep-drawdown territory
        out = self.pipe.explore("deep_drawdown")
        self.assertEqual(out["raw_leads"], 1)
        leads = self.pipe.prospector.fresh_lead_candidates()
        self.assertEqual(leads[0]["ticker"], "TEST")
        self.assertIsNotNone(leads[0]["score"])

    def test_drawdown_breaker_blocks_all_buying(self):
        # simulate a fund already 20% off peak: breaker must veto the buy
        self.pipe.db.update_state(peak_nav=1_875_000)   # NAV 1.5M => -20%
        summary = self.pipe.research_cycle()
        self.assertEqual(summary["buys"], [])
        clamps = self.pipe.db.query("SELECT payload FROM events WHERE kind='risk_clamp'")
        self.assertTrue(any("drawdown" in r["payload"] for r in clamps))


if __name__ == "__main__":
    unittest.main()
