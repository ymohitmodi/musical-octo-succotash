"""Portfolio Manager: synthesizes the committee debate into a decision.

Believability-weighted synthesis (idea meritocracy): each analyst's vote is
weighted by their genome's realized fitness when available. The PM's output
then faces the Constitution engine before any order exists."""

from __future__ import annotations

import json
import time

from ..llm import LLMClient
from ..storage import DB
from .base import DISCLAIMER, load_active_genome


class PortfolioManager:
    name = "pm"
    default_genome = {"temperature": 0.25, "base_position_pct": 0.03,
                      "conviction_scaling": 0.6}

    def __init__(self, llm: LLMClient, db: DB):
        self.llm = llm
        self.db = db
        self.genome_id, self.genome = load_active_genome(db, self.name, self.default_genome)

    # ------------------------------------------------------------------------
    def _believability(self, agent: str) -> float:
        """Track-record weight from genome fitness history (1.0 = neutral)."""
        rows = self.db.query(
            "SELECT fitness FROM genomes WHERE agent=? AND fitness IS NOT NULL "
            "ORDER BY created_ts DESC LIMIT 3", (agent,))
        if not rows:
            return 1.0
        avg = sum(r["fitness"] for r in rows) / len(rows)
        return max(0.5, min(1.5, 1.0 + avg))  # fitness is excess return; clamp

    def decide(self, ticker: str, dossier: dict, reports: dict[str, dict],
               regime: dict, min_conviction: float) -> dict:
        """Debate synthesis. Returns decision dict (action may be 'reject')."""
        weighted = {}
        for agent, rep in reports.items():
            w = self._believability(agent)
            sign = {"bullish": 1, "neutral": 0, "bearish": -1}.get(rep.get("verdict"), 0)
            weighted[agent] = {"verdict": rep.get("verdict"), "confidence": rep.get("confidence"),
                               "believability_weight": round(w, 2),
                               "weighted_vote": round(sign * float(rep.get("confidence", 50)) * w, 1),
                               "thesis": rep.get("thesis", ""),
                               "red_flags": rep.get("red_flags", [])}

        from .base import recent_lessons
        from .. import doctrine
        decision = self.llm.chat_json(
            system=(DISCLAIMER + "\n\nROLE: Portfolio manager of a deep-value fund. "
                    "You weigh the committee's believability-weighted votes, take the "
                    "bear case seriously (a lethal bear attack is disqualifying), and "
                    "size positions conservatively. Rejecting is always acceptable; "
                    "there is no quota of trades (constitution P7)."
                    + doctrine.for_agent("pm") + recent_lessons(self.db)),
            user=f"""Committee reports for {ticker}:
{json.dumps(weighted, indent=2, default=str)}

Valuation metrics: {json.dumps(dossier.get('metrics', {}), default=str)}
Macro regime: {json.dumps(regime, default=str)}
Base position size: {self.genome.get('base_position_pct', 0.03):.1%} of NAV, scale up to
1.6x for extraordinary conviction, down to 0.5x for marginal ideas.
Minimum conviction to act: {min_conviction}.

Decide one of:
- "buy": undervalued now with the required margin of safety.
- "watch": a genuinely good business the committee endorses, but the price
  lacks the margin of safety. Set target_entry — the price at which the
  margin would exist. The fund will stalk it patiently (doctrine: patience).
- "reject": flawed idea or unresolvable bear case.

If buy: conviction (0-100), target_weight (fraction of NAV), a 3-sentence
plain-language thesis (constitution P3), and the specific invalidation
triggers (exit conditions).""",
            schema_hint=('{"action": "buy|watch|reject", "conviction": 0-100, '
                         '"target_weight": 0.0-0.05, "target_entry": number|null, '
                         '"thesis": "...", "invalidation_triggers": ["..."]}'),
            temperature=float(self.genome.get("temperature", 0.25)),
        )
        decision.setdefault("action", "reject")
        decision.setdefault("conviction", 0)
        decision.setdefault("target_weight", 0.0)
        # regime scales size, never conviction (P6)
        decision["target_weight"] = round(
            float(decision.get("target_weight", 0.0)) * float(regime.get("risk_multiplier", 1.0)), 5)
        if decision["action"] == "buy" and float(decision["conviction"]) < min_conviction:
            decision["action"] = "reject"
        decision["committee"] = weighted
        return decision

    # ------------------------------------------------------------------------
    def pre_mortem(self, ticker: str, dossier: dict, decision: dict) -> dict:
        """Mandatory pre-mortem (doctrine: process). Fail-closed: if the
        pre-mortem can't run or deems the size unsurvivable, no buy."""
        try:
            out = self.llm.chat_json(
                system=(DISCLAIMER + "\n\nROLE: You are running the fund's mandatory "
                        "pre-mortem. Assume it is TWO YEARS LATER and this position "
                        "has LOST 40%. Work backwards: what killed it?"),
                user=f"""Proposed position: {ticker}, {decision.get('target_weight', 0):.1%} of NAV.
Thesis: {decision.get('thesis', '')}
Key metrics: {json.dumps(dossier.get('metrics', {}), default=str)}
Bear case on record: {json.dumps(decision.get('committee', {}).get('bear', {}), default=str)}

Give the three most probable failure causes, each with an observable
kill-criterion (an event that means 'you are in the failure case — exit').
Then judge: at this size, is the most likely failure survivable for a fund
that must never lose more than it can recover from?""",
                schema_hint=('{"failure_causes": [{"cause": "...", "probability": '
                             '"high|medium|low", "kill_criterion": "..."}], '
                             '"survivable": true|false, "reasoning": "..."}'),
                temperature=0.3,
            )
        except Exception as e:  # noqa: BLE001
            out = {"failure_causes": [], "survivable": False,
                   "reasoning": f"pre-mortem unavailable ({e}); failing closed"}
        out.setdefault("survivable", False)
        return out

    def post_mortem(self, ticker: str, entry_thesis: str, outcome_pct: float,
                    exit_reason: str) -> dict | None:
        """Post-mortem on a closed position; the lesson becomes institutional
        memory injected into all future analyses. Best-effort (never blocks)."""
        try:
            out = self.llm.chat_json(
                system=(DISCLAIMER + "\n\nROLE: You are writing the fund's post-mortem "
                        "on a CLOSED position. Judge the PROCESS, not just the outcome: "
                        "a win from a broken process is the most dangerous result in "
                        "investing. Be specific and merciless; this lesson will be "
                        "read before every future decision."),
                user=f"""Closed position: {ticker}
Entry thesis (immutable record): {entry_thesis}
Outcome: {outcome_pct:+.1%}   Exit reason: {exit_reason}

Classify (process error / thesis error / timing / luck-good / luck-bad) and
state ONE transferable lesson in a single sentence a future analyst must
not forget.""",
                schema_hint='{"category": "process|thesis|timing|luck", "lesson": "..."}',
                temperature=0.3,
            )
        except Exception:  # noqa: BLE001 - memory is valuable, never critical
            return None
        if not out.get("lesson"):
            return None
        row = {"ticker": ticker, "category": str(out.get("category", "process"))[:20],
               "lesson": str(out["lesson"])[:400]}
        self.db.insert("lessons", ts=time.time(), ticker=ticker, decision_id=None,
                       outcome_pct=outcome_pct, category=row["category"],
                       lesson=row["lesson"])
        return row

    # ------------------------------------------------------------------------
    def review_position(self, ticker: str, position: dict, snapshot: dict,
                        original_thesis: str) -> dict:
        """Periodic hold/sell review of an open position against its thesis."""
        try:
            out = self.llm.chat_json(
                system=(DISCLAIMER + "\n\nROLE: Portfolio manager reviewing an open "
                        "position. Sell when the thesis is broken, the invalidation "
                        "triggers fired, or price reached fair value — not because of "
                        "ordinary volatility (constitution P9: never average down to "
                        "get even, never panic on noise)."),
                user=f"""Position: {json.dumps(position, default=str)}
Current market: {json.dumps(snapshot, default=str)}
Original thesis (recorded at entry, immutable): {original_thesis}

Decide hold or sell.""",
                schema_hint='{"action": "hold|sell", "reason": "..."}',
                temperature=0.2,
            )
        except Exception:  # noqa: BLE001 - on LLM failure, default to hold (do nothing)
            out = {"action": "hold", "reason": "LLM unavailable; defaulting to hold"}
        out.setdefault("action", "hold")
        self.db.insert("analyses", ts=time.time(), ticker=ticker, agent="pm_review",
                       genome_id=self.genome_id, verdict=out["action"],
                       confidence=50.0, report=json.dumps(out, default=str))
        return out
