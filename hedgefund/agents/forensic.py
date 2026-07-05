"""Forensic accountant: hunts for accounting manipulation and distress."""

from __future__ import annotations

import json

from .base import BaseAgent


class ForensicAccountant(BaseAgent):
    name = "forensic"
    role = ("Forensic accountant. Your only job is to find reasons this company's "
            "reported numbers cannot be trusted: receivables outgrowing revenue, "
            "accrual-heavy earnings, deteriorating asset quality, leverage games, "
            "serial one-time charges. A clean report from you is a prerequisite "
            "for any buy (constitution P5). When in doubt, flag it.")
    default_genome = {"temperature": 0.2, "skepticism": 0.8}

    def user_prompt(self, ticker: str, dossier: dict) -> str:
        return f"""Perform a forensic accounting review of {ticker}.

FORENSIC SCORES (computed, not estimated):
  Beneish M-Score: {dossier.get('beneish_m')}  (> -1.78 suggests possible manipulation)
  Altman Z-Score:  {dossier.get('altman_z')}   (< 1.81 = distress zone)
  Piotroski checks: {json.dumps(dossier.get('f_score', {}).get('checks', {}), default=str)}

4-YEAR FUNDAMENTAL SERIES (newest first, from SEC XBRL):
{json.dumps(dossier.get('fundamentals_summary', {}), indent=2, default=str)}

RECENT FILINGS (watch for 8-Ks: auditor changes, restatements, resignations):
{json.dumps(dossier.get('filings', [])[:8], default=str)}

Cross-check trends: receivables vs revenue growth, cash flow vs net income,
inventory builds, debt migration. Verdict 'bearish' means DISQUALIFY."""
