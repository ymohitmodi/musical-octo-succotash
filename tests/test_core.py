"""Offline unit tests: quant math, constitution vetoes, evolution bounds, DB.

No network, no LLM — these must pass anywhere.
Run: python -m pytest tests/ -q   (or python -m unittest discover tests)
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hedgefund import quant
from hedgefund.config import Config
from hedgefund.evolution.evolver import GENE_BOUNDS, Evolver
from hedgefund.storage import DB


def fake_fundamentals() -> dict:
    """Two clean fiscal years for a cheap, improving business."""
    def series(new: float, old: float) -> list[dict]:
        return [{"fy": 2025, "end": "2025-12-31", "val": new},
                {"fy": 2024, "end": "2024-12-31", "val": old}]
    return {
        "revenue": series(10_000e6, 9_000e6),
        "net_income": series(900e6, 700e6),
        "ebit": series(1_200e6, 1_000e6),
        "gross_profit": series(4_200e6, 3_600e6),
        "assets": series(12_000e6, 12_500e6),
        "current_assets": series(5_000e6, 4_800e6),
        "current_liabilities": series(2_000e6, 2_100e6),
        "liabilities": series(6_000e6, 6_800e6),
        "equity": series(6_000e6, 5_700e6),
        "cfo": series(1_400e6, 1_100e6),
        "capex": series(400e6, 450e6),
        "long_term_debt": series(2_000e6, 2_600e6),
        "cash": series(1_500e6, 1_200e6),
        "receivables": series(1_000e6, 950e6),
        "retained_earnings": series(3_000e6, 2_400e6),
        "ppe_net": series(4_000e6, 4_100e6),
        "depreciation": series(420e6, 430e6),
        "sga": series(1_800e6, 1_700e6),
        "shares": series(500e6, 505e6),
    }


class TestQuant(unittest.TestCase):
    def setUp(self):
        self.f = fake_fundamentals()

    def test_valuation_metrics(self):
        m = quant.valuation_metrics(self.f, price=20.0, shares=None)
        self.assertAlmostEqual(m["market_cap"], 10_000e6)
        self.assertAlmostEqual(m["pe"], 10_000e6 / 900e6, places=3)
        self.assertGreater(m["fcf_yield"], 0.09)          # (1400-400)/10000
        self.assertGreater(m["earnings_yield_ev"], 0.10)  # 1200 / 10500 EV

    def test_piotroski_high_for_improving_firm(self):
        f = quant.piotroski_f(self.f)
        self.assertEqual(f["f_max"], 9)
        self.assertGreaterEqual(f["f_score"], 8)

    def test_altman_z_safe(self):
        m = quant.valuation_metrics(self.f, price=20.0, shares=None)
        z = quant.altman_z(self.f, m["market_cap"])
        self.assertGreater(z, 2.6)  # comfortably above the 1.81 distress floor

    def test_beneish_m_clean(self):
        m = quant.beneish_m(self.f)
        self.assertIsNotNone(m)
        self.assertLess(m, -1.78)  # clean books should not flag

    def test_composite_score_range(self):
        m = quant.valuation_metrics(self.f, price=20.0, shares=None)
        fs = quant.piotroski_f(self.f)
        z = quant.altman_z(self.f, m["market_cap"])
        mb = quant.beneish_m(self.f)
        snap = {"pct_off_high": -0.35}
        score = quant.composite_value_score(m, fs, z, mb, snap)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertGreater(score, 60)  # this synthetic firm is genuinely cheap+good


class TestConstitutionHardLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "t.db")
        self.cfg = Config.load()
        # constitution engine without an LLM (hard limits are pure code)
        from hedgefund.constitution.engine import ConstitutionEngine
        self.engine = ConstitutionEngine(llm=None, db=self.db,
                                         constitution=self.cfg.constitution)
        self.snapshot = {"price": 25.0, "adv_shares": 5_000_000, "adv_dollars": 125e6}
        self.state = {"halted_until": 0, "peak_nav": 1_500_000, "day_open_nav": 1_500_000}

    def tearDown(self):
        self.tmp.cleanup()

    def test_clamps_oversized_position(self):
        w, reasons = self.engine.enforce_hard_limits(
            "XYZ", "energy", 0.10, nav=1_500_000, cash=1_500_000, positions=[],
            snapshot=self.snapshot, trades_today=0, state=self.state)
        self.assertLessEqual(w, 0.05)
        self.assertTrue(any("max position" in r for r in reasons))

    def test_drawdown_breaker_vetoes(self):
        state = dict(self.state, peak_nav=2_000_000)  # NAV 1.5M = -25% drawdown
        w, reasons = self.engine.enforce_hard_limits(
            "XYZ", "energy", 0.03, nav=1_500_000, cash=1_500_000, positions=[],
            snapshot=self.snapshot, trades_today=0, state=state)
        self.assertEqual(w, 0.0)
        self.assertTrue(any("drawdown" in r for r in reasons))

    def test_sector_concentration_veto(self):
        positions = [{"ticker": f"E{i}", "sector": "energy", "market_value": 100_000}
                     for i in range(3)]  # 300k / 1.5M = 20% already
        w, _ = self.engine.enforce_hard_limits(
            "XYZ", "energy", 0.03, nav=1_500_000, cash=1_000_000, positions=positions,
            snapshot=self.snapshot, trades_today=0, state=self.state)
        self.assertEqual(w, 0.0)

    def test_cash_floor_clamp(self):
        w, reasons = self.engine.enforce_hard_limits(
            "XYZ", "tech", 0.05, nav=1_500_000, cash=160_000, positions=[],
            snapshot=self.snapshot, trades_today=0, state=self.state)
        # only ~10k above the 150k cash floor is spendable (weight rounding tolerance)
        self.assertLessEqual(w * 1_500_000, 10_100)

    def test_illiquid_name_vetoed(self):
        snap = dict(self.snapshot, adv_dollars=500_000)
        w, _ = self.engine.enforce_hard_limits(
            "XYZ", "tech", 0.03, nav=1_500_000, cash=1_500_000, positions=[],
            snapshot=snap, trades_today=0, state=self.state)
        self.assertEqual(w, 0.0)


class TestEvolution(unittest.TestCase):
    def test_mutation_respects_bounds(self):
        ev = Evolver(DB(":memory:"), {"mutation_rate": 1.0})
        params = {"temperature": 0.7, "skepticism": 0.95, "base_position_pct": 0.05}
        for seed in range(50):
            child = ev.mutate_params(params, seed=seed)
            for gene, val in child.items():
                lo, hi = GENE_BOUNDS[gene]
                self.assertGreaterEqual(val, lo, gene)
                self.assertLessEqual(val, hi, gene)

    def test_mutation_deterministic_with_seed(self):
        ev = Evolver(DB(":memory:"), {"mutation_rate": 1.0})
        p = {"temperature": 0.4}
        self.assertEqual(ev.mutate_params(p, seed=7), ev.mutate_params(p, seed=7))


class TestDB(unittest.TestCase):
    def test_state_and_cache_roundtrip(self):
        db = DB(":memory:")
        db.init_state(1_500_000)
        self.assertEqual(db.get_state()["cash"], 1_500_000)
        db.update_state(cash=1_400_000)
        self.assertEqual(db.get_state()["cash"], 1_400_000)
        db.cache_set("k", {"a": 1}, ttl_sec=60)
        self.assertEqual(db.cache_get("k"), {"a": 1})
        db.log_event("test", {"x": 1}, "AAPL")
        rows = db.query("SELECT * FROM events")
        self.assertEqual(json.loads(rows[0]["payload"])["x"], 1)


if __name__ == "__main__":
    unittest.main()
