"""Constitutional AI engine: two independent layers of restraint.

Layer 1 — HARD LIMITS: deterministic code. No model output can bypass these;
          they run last and their veto is final.
Layer 2 — PRINCIPLE CRITIQUE: a critic LLM reviews each PM decision against
          the written principles and may veto or demand a smaller size.
          (Self-critique catches reasoning failures; hard limits catch
          everything else.)
"""

from __future__ import annotations

import json
import logging
import time

from ..llm import LLMClient
from ..storage import DB

log = logging.getLogger("hedgefund.constitution")


class ConstitutionEngine:
    def __init__(self, llm: LLMClient, db: DB, constitution: dict,
                 checklist: list[dict] | None = None):
        self.llm = llm
        self.db = db
        self.limits = constitution["hard_limits"]
        self.principles = constitution["principles"]
        self.checklist = checklist or []

    # -------------------------------------------- pre-buy checklist (enforced)
    def run_checklist(self, ticker: str, decision: dict, dossier: dict,
                      premortem: dict) -> dict:
        """Munger-style pre-buy checklist. Every item answered with evidence;
        deterministic enforcement: any critical item not 'pass' => reject.
        Returns {passed: bool, answers: {...}, failed: [ids]}."""
        if not self.checklist:
            return {"passed": True, "answers": {}, "failed": []}
        items_text = "\n".join(f"[{i['id']}]{' (CRITICAL)' if i.get('critical') else ''} "
                               f"{i['question'].strip()}" for i in self.checklist)
        try:
            out = self.llm.chat_json(
                system=("You are administering the fund's mandatory PRE-BUY CHECKLIST "
                        "— the distillation of every previous disaster. Answer each "
                        "item strictly from the evidence provided. An item you cannot "
                        "support with evidence is 'fail', not 'unknown-but-probably-"
                        "fine'. Pilots run the checklist every flight; so do you."),
                user=f"""CHECKLIST ITEMS:
{items_text}

CANDIDATE: {ticker}
PROPOSED DECISION: {json.dumps({k: decision.get(k) for k in
    ('action', 'conviction', 'target_weight', 'thesis',
     'invalidation_triggers')}, default=str)}
COMMITTEE REPORTS: {json.dumps(decision.get('committee', {}), default=str)[:6000]}
METRICS: {json.dumps(dossier.get('metrics', {}), default=str)}
FORENSIC SCORES: Z={dossier.get('altman_z')} M={dossier.get('beneish_m')} F={dossier.get('f_score', {}).get('f_score')}
PRE-MORTEM: {json.dumps(premortem, default=str)}

Answer every item.""",
                schema_hint=('{"answers": {"CL1_UNDERSTAND": {"answer": "pass|fail", '
                             '"evidence": "..."}, ...}}'),
                temperature=0.15,
            )
        except Exception as e:  # noqa: BLE001 - fail closed
            log.error("checklist unavailable, failing closed: %s", e)
            return {"passed": False, "answers": {},
                    "failed": ["SYSTEM"], "error": str(e)}
        answers = out.get("answers", {})
        failed = []
        for item in self.checklist:
            if not item.get("critical"):
                continue
            ans = answers.get(item["id"], {})
            if str(ans.get("answer", "fail")).lower() != "pass":
                failed.append(item["id"])
        result = {"passed": not failed, "answers": answers, "failed": failed}
        if failed:
            self.db.log_event("veto", {"layer": "checklist", "failed": failed}, ticker)
        return result

    # ------------------------------------------------- layer 2: LLM critique
    def critique(self, ticker: str, decision: dict, dossier: dict) -> dict:
        """Returns {approved: bool, revised_weight: float|None, violations: [...]}."""
        principles_text = "\n".join(f"[{p['id']}] {p['text'].strip()}" for p in self.principles)
        try:
            out = self.llm.chat_json(
                system=("You are the CONSTITUTIONAL CRITIC of an autonomous paper-trading "
                        "fund. You do not care about returns; you care only about whether "
                        "this decision complies with the fund constitution. You have veto "
                        "power and a duty to use it. Cite principle IDs for violations."),
                user=f"""FUND CONSTITUTION:
{principles_text}

PROPOSED DECISION for {ticker}:
{json.dumps({k: decision.get(k) for k in ('action', 'conviction', 'target_weight',
                                          'thesis', 'invalidation_triggers')}, indent=2, default=str)}

COMMITTEE DEBATE SUMMARY:
{json.dumps(decision.get('committee', {}), indent=2, default=str)}

KEY METRICS: {json.dumps(dossier.get('metrics', {}), default=str)}

Review: Does the thesis rest on primary-source evidence (P4)? Is the margin of
safety real (P2)? Was the bear case actually engaged with, or waved away (P1)?
Is the business explainable in plain language (P3)? Approve, approve with a
reduced weight, or veto with cited violations.""",
                schema_hint=('{"approved": true|false, "revised_weight": number|null, '
                             '"violations": [{"principle": "P1_...", "detail": "..."}], '
                             '"reasoning": "..."}'),
                temperature=0.15,
            )
        except Exception as e:  # noqa: BLE001
            # Fail CLOSED: if the critic is unreachable, nothing gets approved.
            log.error("constitutional critic unavailable, failing closed: %s", e)
            out = {"approved": False, "revised_weight": None,
                   "violations": [{"principle": "SYSTEM", "detail": f"critic offline: {e}"}],
                   "reasoning": "fail-closed"}
        if not out.get("approved"):
            self.db.log_event("veto", {"layer": "principles", "review": out}, ticker)
        return out

    # ------------------------------------------------ layer 1: hard limits
    def enforce_hard_limits(self, ticker: str, sector: str, target_weight: float,
                            nav: float, cash: float, positions: list[dict],
                            snapshot: dict, trades_today: int,
                            state: dict) -> tuple[float, list[str]]:
        """Clamp/veto a proposed buy. Returns (allowed_weight, reasons).
        allowed_weight == 0 means full veto."""
        reasons: list[str] = []
        L = self.limits
        w = max(0.0, float(target_weight))

        # circuit breakers ---------------------------------------------------
        if time.time() < float(state.get("halted_until") or 0):
            return 0.0, [f"trading halted: {state.get('halt_reason')}"]
        peak = float(state.get("peak_nav") or nav)
        if peak > 0 and (nav / peak - 1) < -float(L["max_drawdown_halt"]):
            return 0.0, [f"drawdown breaker: NAV {nav / peak - 1:.1%} off peak"]
        day_open = float(state.get("day_open_nav") or nav)
        if day_open > 0 and (nav / day_open - 1) < -float(L["daily_loss_halt"]):
            return 0.0, [f"daily loss breaker: {nav / day_open - 1:.1%} today"]
        if trades_today >= int(L["max_trades_per_day"]):
            return 0.0, ["max trades per day reached"]

        # portfolio construction ---------------------------------------------
        if len(positions) >= int(L["max_positions"]):
            return 0.0, [f"max positions ({L['max_positions']}) reached"]
        if any(p["ticker"] == ticker for p in positions):
            return 0.0, ["already hold this name (no averaging in autonomously)"]

        if w > float(L["max_position_pct_nav"]):
            reasons.append(f"clamped to max position {L['max_position_pct_nav']:.0%}")
            w = float(L["max_position_pct_nav"])

        sector_val = sum(p["market_value"] for p in positions if p.get("sector") == sector)
        room = float(L["max_sector_pct_nav"]) - (sector_val / nav if nav else 0)
        if room <= 0:
            return 0.0, [f"sector {sector} at concentration limit"]
        if w > room:
            reasons.append(f"clamped by sector limit to {room:.1%}")
            w = room

        # cash floor ----------------------------------------------------------
        spend = w * nav
        min_cash = float(L["min_cash_pct_nav"]) * nav
        if cash - spend < min_cash:
            w = max(0.0, (cash - min_cash) / nav)
            reasons.append(f"clamped by {L['min_cash_pct_nav']:.0%} cash floor")
        if w * nav < 1000:
            return 0.0, reasons + ["resulting order below $1k minimum"]

        # liquidity / market-impact -------------------------------------------
        price = snapshot.get("price") or 0
        adv_shares = snapshot.get("adv_shares") or 0
        if price < float(L["min_price"]):
            return 0.0, [f"price ${price:.2f} below ${L['min_price']} floor"]
        if (snapshot.get("adv_dollars") or 0) < float(L["min_avg_dollar_volume"]):
            return 0.0, ["insufficient liquidity"]
        if price > 0 and adv_shares > 0:
            max_shares = adv_shares * float(L["max_pct_of_adv"])
            if (w * nav) / price > max_shares:
                w = (max_shares * price) / nav
                reasons.append(f"clamped to {L['max_pct_of_adv']:.0%} of ADV")

        return round(w, 5), reasons
