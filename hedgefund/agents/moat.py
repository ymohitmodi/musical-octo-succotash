"""Moat analyst: is this a durable business or a value trap?"""

from __future__ import annotations

import json

from .base import BaseAgent


class MoatAnalyst(BaseAgent):
    name = "moat"
    role = ("Business-quality analyst. Cheapness alone is not a thesis — half of "
            "all statistically cheap stocks are value traps in dying businesses. "
            "You assess competitive position, pricing power, reinvestment "
            "economics and whether the business will still exist in ten years, "
            "using the financial trajectory and news flow provided.")
    default_genome = {"temperature": 0.35, "skepticism": 0.5, "trap_sensitivity": 0.6}

    def user_prompt(self, ticker: str, dossier: dict) -> str:
        return f"""Assess the business quality and value-trap risk of {ticker}.

TRAJECTORY (4-year series from SEC filings, newest first):
{json.dumps(dossier.get('fundamentals_summary', {}), indent=2, default=str)}

RETURNS ON CAPITAL / MARGIN DATA:
{json.dumps({k: dossier.get('metrics', {}).get(k) for k in
             ('roe', 'ev_ebit', 'fcf_yield', 'debt_to_equity')}, default=str)}

PRICE CONTEXT: {json.dumps(dossier.get('snapshot', {}), default=str)}

RECENT NEWS HEADLINES:
{json.dumps(dossier.get('news', []), indent=2, default=str)}

Value-trap sensitivity setting: {self.genome.get('trap_sensitivity', 0.6)} (0=lenient, 1=brutal).
Key question: is the cheapness cyclical/sentiment-driven (opportunity) or
secular decline (trap)? Verdict bullish only if the business is durable."""
