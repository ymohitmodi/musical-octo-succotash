"""Fundamental analyst: intrinsic value from primary-source financials."""

from __future__ import annotations

import json

from .base import BaseAgent


class FundamentalAnalyst(BaseAgent):
    name = "fundamental"
    role = ("Senior fundamental analyst specializing in deep value. You estimate "
            "intrinsic value from audited financials (SEC XBRL data provided), "
            "insisting on a margin of safety of at least 30% before calling "
            "anything bullish. You think in owner earnings, normalized margins, "
            "and balance-sheet strength — never in stories.")
    default_genome = {"temperature": 0.25, "skepticism": 0.5, "min_margin_of_safety": 0.30}

    def user_prompt(self, ticker: str, dossier: dict) -> str:
        return f"""Analyze {ticker} as a potential deep-value LONG position.

REQUIRED MARGIN OF SAFETY: {self.genome.get('min_margin_of_safety', 0.30):.0%} below your intrinsic value estimate.

VALUATION & QUALITY METRICS (computed from SEC XBRL filings):
{json.dumps(dossier.get('metrics', {}), indent=2, default=str)}

PIOTROSKI F-SCORE: {json.dumps(dossier.get('f_score', {}), default=str)}
ALTMAN Z: {dossier.get('altman_z')}   BENEISH M: {dossier.get('beneish_m')}

4-YEAR FUNDAMENTAL SERIES (newest first, from EDGAR):
{json.dumps(dossier.get('fundamentals_summary', {}), indent=2, default=str)}

MARKET SNAPSHOT: {json.dumps(dossier.get('snapshot', {}), default=str)}

RECENT SEC FILINGS: {json.dumps(dossier.get('filings', [])[:6], default=str)}

Estimate intrinsic value per share, state whether the current price offers the
required margin of safety, and give your verdict."""
