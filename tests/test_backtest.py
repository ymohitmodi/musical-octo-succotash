"""Offline tests for the backtest harness: point-in-time filtering (the
look-ahead-bias guard), price-book lookups, and performance statistics."""

from __future__ import annotations

import unittest

from hedgefund.backtest import perf_stats, point_in_time
from hedgefund.backtest.engine import _PriceBook


class TestPointInTime(unittest.TestCase):
    def setUp(self):
        self.f = {"revenue": [
            {"fy": 2025, "end": "2025-12-31", "val": 300},
            {"fy": 2024, "end": "2024-12-31", "val": 200},
            {"fy": 2023, "end": "2023-12-31", "val": 100},
        ]}

    def test_filing_lag_hides_unfiled_year(self):
        # On Feb 1 2026, FY2025 (ended Dec 31) is NOT yet public with 90d lag.
        pit = point_in_time(self.f, "2026-02-01", filing_lag_days=90)
        self.assertEqual(pit["revenue"][0]["fy"], 2024)

    def test_after_lag_year_becomes_visible(self):
        pit = point_in_time(self.f, "2026-04-15", filing_lag_days=90)
        self.assertEqual(pit["revenue"][0]["fy"], 2025)

    def test_early_date_returns_only_old_years(self):
        pit = point_in_time(self.f, "2024-06-01", filing_lag_days=90)
        self.assertEqual([r["fy"] for r in pit["revenue"]], [2023])

    def test_keep_caps_series_length(self):
        pit = point_in_time(self.f, "2026-12-31", filing_lag_days=90, keep=2)
        self.assertEqual(len(pit["revenue"]), 2)


class TestPriceBook(unittest.TestCase):
    def setUp(self):
        self.book = _PriceBook()
        self.book.load("XYZ", [
            {"date": "2025-01-02", "close": 10.0},
            {"date": "2025-01-03", "close": 11.0},
            {"date": "2025-01-06", "close": 12.0},
        ])

    def test_exact_and_carry_forward(self):
        self.assertEqual(self.book.price_at("XYZ", "2025-01-03"), 11.0)
        # Jan 4-5 is a weekend: carry forward Friday's close
        self.assertEqual(self.book.price_at("XYZ", "2025-01-05"), 11.0)

    def test_before_history_is_none(self):
        self.assertIsNone(self.book.price_at("XYZ", "2024-12-31"))

    def test_stale_price_treated_as_delisted(self):
        self.assertIsNone(self.book.price_at("XYZ", "2025-03-01"))


class TestPerfStats(unittest.TestCase):
    def test_flat_curve(self):
        s = perf_stats([100.0] * 253,
                       [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 22)][:253])
        self.assertEqual(s["total_return"], 0.0)
        self.assertEqual(s["max_drawdown"], 0.0)

    def test_doubling_over_two_years(self):
        navs = [100.0, 150.0, 200.0]
        s = perf_stats(navs, ["2024-01-01", "2025-01-01", "2026-01-01"])
        self.assertAlmostEqual(s["total_return"], 1.0)
        self.assertAlmostEqual(s["cagr"], 2 ** 0.5 - 1, places=2)

    def test_drawdown_captured(self):
        navs = [100.0, 120.0, 90.0, 110.0]
        s = perf_stats(navs, ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"])
        self.assertAlmostEqual(s["max_drawdown"], 90 / 120 - 1, places=4)

    def test_benchmark_comparison(self):
        navs = [100.0, 120.0]
        bench = [400.0, 440.0]
        s = perf_stats(navs, ["2025-01-01", "2026-01-01"], bench)
        self.assertAlmostEqual(s["total_return"], 0.20)
        self.assertAlmostEqual(s["benchmark_total_return"], 0.10)
        self.assertGreater(s["excess_cagr"], 0)


if __name__ == "__main__":
    unittest.main()
