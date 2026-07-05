"""Agent base class with an evolvable genome.

A genome is a small dict of numeric/style parameters that shapes an agent's
behavior (thresholds, weightings, temperature, prompt persona intensity).
Genomes live in the DB with lineage; the evolution engine breeds them against
realized paper-trading fitness. Code never changes — only parameters do —
which keeps evolution safe and auditable.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .. import doctrine
from ..llm import LLMClient
from ..storage import DB

DISCLAIMER = ("You are one specialist on an autonomous investment committee "
              "running a PAPER-TRADING research fund. Be rigorous, cite the "
              "provided data only, and never fabricate numbers.")


def recent_lessons(db: DB, limit: int = 5) -> str:
    """Institutional memory: the latest post-mortem lessons, injected into
    every analysis so the fund never re-learns the same tuition twice."""
    rows = db.query(
        "SELECT ticker, category, lesson FROM lessons ORDER BY ts DESC LIMIT ?",
        (limit,))
    if not rows:
        return ""
    lines = [f"- [{r['category']}/{r['ticker']}] {r['lesson']}" for r in rows]
    return ("\n\n===== RECENT LESSONS FROM THE FUND'S OWN CLOSED POSITIONS "
            "(do not repeat these mistakes) =====\n" + "\n".join(lines))


def load_active_genome(db: DB, agent_name: str, defaults: dict) -> tuple[int, dict]:
    """Fetch the active genome for an agent, creating gen-0 from defaults."""
    row = db.query(
        "SELECT id, params FROM genomes WHERE agent=? AND active=1 "
        "ORDER BY created_ts DESC LIMIT 1", (agent_name,))
    if row:
        return int(row[0]["id"]), json.loads(row[0]["params"])
    gid = db.insert("genomes", agent=agent_name, params=json.dumps(defaults),
                    parent_id=None, generation=0, active=1, fitness=None,
                    created_ts=time.time())
    return gid, dict(defaults)


class BaseAgent:
    name: str = "base"
    role: str = ""
    default_genome: dict[str, Any] = {"temperature": 0.3, "skepticism": 0.5}
    preferred_model: str | None = None   # committee diversity (set by pipeline)

    def __init__(self, llm: LLMClient, db: DB):
        self.llm = llm
        self.db = db
        self.genome_id, self.genome = load_active_genome(db, self.name, self.default_genome)

    # ------------------------------------------------------------------------
    embedder = None   # optional semantic-memory Embedder (set by pipeline)

    def system_prompt(self, context: str = "") -> str:
        skept = float(self.genome.get("skepticism", 0.5))
        tone = ("Default to rejecting ideas unless the evidence is overwhelming."
                if skept > 0.66 else
                "Weigh evidence impartially; require a clear margin of safety."
                if skept > 0.33 else
                "Look actively for overlooked upside, but never ignore red flags.")
        if self.embedder is not None and context:
            from ..llm.embeddings import relevant_lessons
            memory = relevant_lessons(self.db, self.embedder, context)
        else:
            memory = recent_lessons(self.db)
        return (f"{DISCLAIMER}\n\nROLE: {self.role}\nDISPOSITION: {tone}"
                + doctrine.for_agent(self.name)
                + memory)

    def analyze(self, ticker: str, dossier: dict) -> dict:
        """Run the LLM analysis and journal it. Returns the structured report."""
        context = (f"{ticker} {dossier.get('sector', '')} "
                   f"{json.dumps(dossier.get('metrics', {}), default=str)[:400]}")
        report = self.llm.chat_json(
            system=self.system_prompt(context=context),
            user=self.user_prompt(ticker, dossier),
            schema_hint=('{"verdict": "bullish|neutral|bearish", "confidence": 0-100, '
                         '"thesis": "...", "key_risks": ["..."], "red_flags": ["..."], '
                         '"estimated_upside_pct": number|null}'),
            temperature=float(self.genome.get("temperature", 0.3)),
            model=self.preferred_model,
        )
        report.setdefault("verdict", "neutral")
        report.setdefault("confidence", 50)
        self.db.insert("analyses", ts=time.time(), ticker=ticker, agent=self.name,
                       genome_id=self.genome_id, verdict=str(report["verdict"]),
                       confidence=float(report["confidence"]),
                       report=json.dumps(report, default=str))
        return report

    def user_prompt(self, ticker: str, dossier: dict) -> str:  # pragma: no cover
        raise NotImplementedError
