"""The Bear Raider: institutionalized dissent (red team).

Bridgewater-style idea meritocracy requires stress-testing every thesis.
This agent is REWARDED for killing bad ideas — its fitness in the evolution
engine rises when positions it attacked go on to lose money."""

from __future__ import annotations

import json

from .base import BaseAgent


class BearRaider(BaseAgent):
    name = "bear"
    role = ("Dedicated devil's advocate. Construct the strongest possible SHORT "
            "thesis against this stock, as if you managed a short book and your "
            "bonus depended on it. Attack the valuation assumptions, the "
            "business, the balance sheet, and the incentives of management. "
            "Your verdict is 'bearish' when your attack is strong, 'bullish' "
            "only when you genuinely cannot build a credible attack.")
    default_genome = {"temperature": 0.45, "skepticism": 0.9}

    def user_prompt(self, ticker: str, dossier: dict) -> str:
        return f"""Build the strongest bear case against {ticker}.

The bulls say it is cheap: {json.dumps(dossier.get('metrics', {}), default=str)}
Quality scores: F={dossier.get('f_score', {}).get('f_score')} Z={dossier.get('altman_z')} M={dossier.get('beneish_m')}

FUNDAMENTALS (4y, newest first): {json.dumps(dossier.get('fundamentals_summary', {}), default=str)}
PRICE ACTION: {json.dumps(dossier.get('snapshot', {}), default=str)}
NEWS: {json.dumps(dossier.get('news', []), default=str)}

Attack angles to consider: value trap / melting ice cube, hidden leverage,
cyclical peak earnings, secular disruption, capital misallocation, dilution,
customer concentration, regulatory exposure. Rate the LETHALITY of your best
attack in 'confidence' (100 = thesis-killing)."""
