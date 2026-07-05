"""Offline tests: benchmark scoring rules and semantic-memory retrieval."""

from __future__ import annotations

import unittest

from hedgefund import benchmark as bm
from hedgefund.llm.embeddings import Embedder, cosine, relevant_lessons
from hedgefund.storage import DB


class TestBenchmarkScoring(unittest.TestCase):
    def test_extraction_scoring(self):
        perfect = dict(bm.EXTRACTION_KEY)
        self.assertEqual(bm.score_extraction(perfect), 1.0)
        wrong = dict(perfect, revenue_musd=9999)
        self.assertAlmostEqual(bm.score_extraction(wrong), 5 / 6)
        self.assertEqual(bm.score_extraction({}), 0.0)

    def test_nofab_rewards_honest_nulls_punishes_invention(self):
        honest = {"revenue_musd": 8214, "goodwill_musd": None,
                  "shares_outstanding_m": None, "capex_musd": 397}
        self.assertEqual(bm.score_nofab(honest), 1.0)
        # inventing a plausible goodwill figure is the cardinal sin
        invented = dict(honest, goodwill_musd=1200.0)
        self.assertAlmostEqual(bm.score_nofab(invented), 0.75)

    def test_needle_scoring(self):
        full = {"warranty_reserve_musd": 88, "auditor_name": "Hollis & Marsh LLP",
                "ceo_share_purchase_shares": 25000}
        self.assertEqual(bm.score_needle(full), 1.0)
        self.assertAlmostEqual(bm.score_needle({"auditor_name": "hollis & marsh"}), 1 / 3)

    def test_needle_dossier_contains_planted_facts(self):
        doc = bm._needle_dossier()
        self.assertIn("Hollis & Marsh", doc)
        self.assertIn("25000 shares", doc)
        self.assertGreater(len(doc), 8000)   # genuinely long context

    def test_contradiction_scoring(self):
        good = {"claim_supported": False,
                "evidence": ["receivables growing 2.7x while CFO turned negative"]}
        self.assertEqual(bm.score_contradiction(good), 1.0)
        weak = {"claim_supported": False, "evidence": ["seems off"]}
        self.assertEqual(bm.score_contradiction(weak), 0.7)
        fooled = {"claim_supported": True, "evidence": ["revenue is growing"]}
        self.assertEqual(bm.score_contradiction(fooled), 0.0)

    def test_consistency_scoring(self):
        self.assertEqual(bm.score_consistency(["bullish"] * 3), 1.0)
        self.assertAlmostEqual(bm.score_consistency(["bullish", "bullish", "bearish"]), 2 / 3)


class FakeEmbedder(Embedder):
    """Deterministic 3-dim embeddings keyed on topic words."""

    def __init__(self):
        pass  # no db/network

    def embed(self, text: str) -> list[float] | None:
        t = text.lower()
        return [1.0 if "retail" in t else 0.0,
                1.0 if ("debt" in t or "lever" in t) else 0.0,
                1.0 if "airline" in t else 0.0]


class TestSemanticMemory(unittest.TestCase):
    def test_cosine(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(cosine([], [1]), 0.0)

    def _db_with_lessons(self) -> DB:
        db = DB(":memory:")
        import time
        db.insert("lessons", ts=time.time() - 100, ticker="KSS", decision_id=None,
                  outcome_pct=-0.3, category="thesis",
                  lesson="Levered retail turnarounds need a balance sheet that survives two bad years; debt kills the option value.")
        db.insert("lessons", ts=time.time(), ticker="UAL", decision_id=None,
                  outcome_pct=-0.2, category="timing",
                  lesson="Airline cycles turn faster than fleet plans; never annualize peak load factors.")
        return db

    def test_relevant_beats_recent(self):
        db = self._db_with_lessons()
        out = relevant_lessons(db, FakeEmbedder(), "XYZ retail chain heavy debt load",
                               limit=1)
        self.assertIn("Levered retail", out)        # semantic match wins...
        self.assertNotIn("Airline", out)            # ...over the more recent lesson

    def test_fallback_to_recency_when_embedder_fails(self):
        class DeadEmbedder(FakeEmbedder):
            def embed(self, text):
                return None

        db = self._db_with_lessons()
        out = relevant_lessons(db, DeadEmbedder(), "retail debt", limit=1)
        self.assertIn("Airline", out)               # recency fallback: newest first

    def test_no_embedder_means_recency(self):
        db = self._db_with_lessons()
        out = relevant_lessons(db, None, "retail debt", limit=1)
        self.assertIn("Airline", out)


if __name__ == "__main__":
    unittest.main()
