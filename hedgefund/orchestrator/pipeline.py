"""The dark-factory pipeline: screen -> dossier -> committee -> PM -> constitution -> execute.

Every stage is individually fault-isolated: a failure is journaled and the
belt keeps moving. No human touches any stage; humans read the daily report.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..agents import (ANALYST_CLASSES, MacroStrategist, PortfolioManager, Screener)
from ..config import Config, ROOT
from ..constitution import ConstitutionEngine
from ..data import EdgarClient, MacroData, MarketData, NewsFeed
from ..llm import LLMClient
from ..portfolio import PaperBroker
from ..storage import DB

log = logging.getLogger("hedgefund.pipeline")


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        s = cfg.settings
        self.db = DB(cfg.data_dir / "fund.db")
        self.llm = LLMClient(
            host=s["llm"]["host"], models=cfg.models, api_key=s["llm"].get("api_key", ""),
            timeout=int(s["llm"]["request_timeout_sec"]), max_retries=int(s["llm"]["max_retries"]))
        self.edgar = EdgarClient(self.db, s["data"]["edgar_user_agent"],
                                 rps=float(s["data"]["edgar_rps"]))
        self.market = MarketData(self.db, rps=float(s["data"]["market_rps"]))
        self.macro_data = MacroData(self.db, api_key=str(s["data"].get("fred_api_key", "")))
        self.news = NewsFeed(self.db)
        self.broker = PaperBroker(self.db, self.market, cfg.capital,
                                  slippage_bps=float(s["pipeline"]["slippage_bps"]),
                                  commission=float(s["pipeline"]["commission_per_trade"]))
        self.constitution = ConstitutionEngine(self.llm, self.db, cfg.constitution)
        self.screener = Screener(self.db, self.edgar, self.market, cfg.hard_limits)
        self.analysts = [cls(self.llm, self.db) for cls in ANALYST_CLASSES]
        self.macro_agent = MacroStrategist(self.llm, self.db)
        self.pm = PortfolioManager(self.llm, self.db)

    # ------------------------------------------------------------- stage: screen
    def run_screen(self, weekend: bool = False) -> list[dict]:
        universe = list(self.cfg.universe.get("seeds", []))
        if weekend and self.cfg.universe.get("auto_expand"):
            universe = self.screener.expanded_universe(
                universe, int(self.cfg.universe.get("auto_expand_sample", 100)))
        log.info("screening %d tickers (weekend=%s)", len(universe), weekend)
        return self.screener.screen_universe(universe)

    # ------------------------------------------------------------ stage: dossier
    def build_dossier(self, candidate: dict) -> dict:
        ticker = candidate["ticker"]
        f = self.edgar.fundamentals(ticker) or {}
        summary = {concept: [{"end": r["end"], "val": r["val"]} for r in series]
                   for concept, series in f.items()}
        return {
            **candidate,
            "fundamentals_summary": summary,
            "filings": self.edgar.recent_filings(ticker),
            "news": self.news.for_ticker(ticker),
        }

    # ----------------------------------------------------------- stage: committee
    def deep_dive(self, candidate: dict, regime: dict) -> dict | None:
        """Full committee + PM + constitution for one candidate. Returns decision."""
        ticker = candidate["ticker"]
        dossier = self.build_dossier(candidate)
        reports: dict[str, dict] = {}
        genome_ids: dict[str, int | None] = {"screener": self.screener.genome_id}
        for agent in self.analysts:
            try:
                reports[agent.name] = agent.analyze(ticker, dossier)
                genome_ids[agent.name] = agent.genome_id
            except Exception as e:  # noqa: BLE001 - one analyst down != no decision
                log.warning("agent %s failed on %s: %s", agent.name, ticker, e)
                self.db.log_event("error", {"stage": f"agent:{agent.name}",
                                            "error": str(e)}, ticker)
        if "forensic" not in reports:
            # P5: no forensic clearance, no trade. Fail closed.
            self.db.log_event("veto", {"reason": "forensic agent unavailable"}, ticker)
            return None
        if reports["forensic"].get("verdict") == "bearish":
            self.db.log_event("veto", {"reason": "forensic disqualification",
                                       "report": reports["forensic"]}, ticker)
            return None
        if len(reports) < 3:
            self.db.log_event("veto", {"reason": "quorum not met"}, ticker)
            return None

        min_conv = float(self.cfg.settings["pipeline"]["min_conviction"])
        try:
            decision = self.pm.decide(ticker, dossier, reports, regime, min_conv)
        except Exception as e:  # noqa: BLE001
            log.error("PM failed on %s: %s", ticker, e)
            self.db.log_event("error", {"stage": "pm", "error": str(e)}, ticker)
            return None
        genome_ids["pm"] = self.pm.genome_id

        review = {"approved": False}
        if decision.get("action") == "buy":
            review = self.constitution.critique(ticker, decision, dossier)
            if not review.get("approved"):
                decision["action"] = "reject"
            elif review.get("revised_weight") is not None:
                decision["target_weight"] = min(float(decision["target_weight"]),
                                                float(review["revised_weight"]))

        decision_id = self.db.insert(
            "decisions", ts=time.time(), ticker=ticker,
            action=decision.get("action", "reject"),
            conviction=float(decision.get("conviction", 0)),
            target_weight=float(decision.get("target_weight", 0)),
            thesis=str(decision.get("thesis", "")),
            constitution_review=json.dumps(review, default=str),
            genome_ids=json.dumps(genome_ids),
            status="open" if decision.get("action") == "buy" else "closed")
        decision["decision_id"] = decision_id
        decision["ticker"] = ticker
        decision["sector"] = candidate.get("sector", "unknown")
        self.db.log_event("decision", decision, ticker)
        return decision

    # ------------------------------------------------------------ stage: execute
    def execute(self, decision: dict) -> dict | None:
        if decision.get("action") != "buy":
            return None
        ticker = decision["ticker"]
        snapshot = self.market.snapshot(ticker) or {}
        nav, cash, _ = self.broker.nav()
        positions = self.broker.positions()
        state = self.broker.state()
        allowed, reasons = self.constitution.enforce_hard_limits(
            ticker, decision.get("sector", "unknown"),
            float(decision.get("target_weight", 0)), nav, cash, positions,
            snapshot, self.broker.trades_today(), state)
        if reasons:
            self.db.log_event("risk_clamp", {"allowed": allowed, "reasons": reasons}, ticker)
        if allowed <= 0:
            with self.db.conn() as c:
                c.execute("UPDATE decisions SET status='closed', action='reject' WHERE id=?",
                          (decision["decision_id"],))
            return None
        return self.broker.buy(ticker, decision.get("sector", "unknown"),
                               allowed, nav, decision["decision_id"])

    # -------------------------------------------------------------- full cycles
    def research_cycle(self, weekend: bool = False) -> dict:
        """The evening deep-dive: the core money-seeking loop."""
        started = time.time()
        regime = self.macro_agent.regime(self.macro_data.snapshot(),
                                         self.news.market_wide())
        survivors = self.run_screen(weekend=weekend)
        held = {p["ticker"] for p in self.broker.positions(with_prices=False)}
        batch = [c for c in survivors if c["ticker"] not in held]
        batch = batch[:int(self.cfg.settings["pipeline"]["deep_dive_batch"])]
        decisions, trades = [], []
        for candidate in batch:
            try:
                d = self.deep_dive(candidate, regime)
            except Exception as e:  # noqa: BLE001
                log.error("deep dive crashed on %s: %s", candidate["ticker"], e)
                self.db.log_event("error", {"stage": "deep_dive", "error": str(e)},
                                  candidate["ticker"])
                continue
            if d:
                decisions.append(d)
                t = self.execute(d)
                if t:
                    trades.append(t)
        summary = {"duration_sec": round(time.time() - started),
                   "regime": regime.get("regime"), "screened_survivors": len(survivors),
                   "deep_dives": len(batch),
                   "buys": [t["ticker"] for t in trades],
                   "rejects": [d["ticker"] for d in decisions if d.get("action") != "buy"]}
        self.db.log_event("research_cycle", summary)
        return summary

    def monitor_cycle(self) -> dict:
        """Intraday: mark to market, mechanical stops, thesis reviews."""
        mtm = self.broker.mark_to_market(self.cfg.settings["fund"]["benchmark"])
        L = self.cfg.hard_limits
        stops = self.broker.enforce_stops(float(L["stop_loss_pct"]),
                                          int(L["max_holding_days"]))
        # LLM thesis review for the position with the stalest review (one per
        # cycle keeps token spend bounded while covering the book continuously)
        sells = []
        positions = self.broker.positions()
        if positions:
            positions.sort(key=lambda p: self._last_review_ts(p["ticker"]))
            p = positions[0]
            if time.time() - self._last_review_ts(p["ticker"]) > 86400:
                thesis = self._original_thesis(p)
                snapshot = self.market.snapshot(p["ticker"]) or {}
                verdict = self.pm.review_position(p["ticker"], p, snapshot, thesis)
                if verdict.get("action") == "sell":
                    t = self.broker.sell(p["ticker"], f"thesis review: {verdict.get('reason')}")
                    if t:
                        sells.append(t)
        out = {**mtm, "stop_exits": stops, "review_exits": sells}
        self.db.log_event("monitor", out)
        return out

    def _last_review_ts(self, ticker: str) -> float:
        rows = self.db.query(
            "SELECT MAX(ts) AS ts FROM analyses WHERE ticker=? AND agent='pm_review'",
            (ticker,))
        return float(rows[0]["ts"] or 0)

    def _original_thesis(self, position: dict) -> str:
        if position.get("decision_id"):
            rows = self.db.query("SELECT thesis FROM decisions WHERE id=?",
                                 (position["decision_id"],))
            if rows:
                return rows[0]["thesis"]
        return "(thesis not found)"

    # --------------------------------------------------------------- reporting
    def daily_report(self) -> str:
        nav, cash, invested = self.broker.nav()
        state = self.broker.state()
        positions = self.broker.positions()
        recent = self.db.query(
            "SELECT kind, ticker, payload, ts FROM events "
            "WHERE ts > ? ORDER BY ts DESC LIMIT 40", (time.time() - 86400,))
        peak = float(state.get("peak_nav") or nav)
        lines = [
            f"# Daily Report — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
            "",
            f"**NAV:** ${nav:,.0f}  |  cash ${cash:,.0f} ({cash / nav:.0%})  |  "
            f"invested ${invested:,.0f}  |  drawdown from peak {nav / peak - 1:.1%}",
            "",
            "## Positions",
        ]
        if positions:
            lines.append("| Ticker | Sector | Qty | Cost | Last | P&L |")
            lines.append("|---|---|---|---|---|---|")
            for p in sorted(positions, key=lambda x: -x["market_value"]):
                lines.append(f"| {p['ticker']} | {p.get('sector', '')} | {p['qty']:,.0f} "
                             f"| ${p['avg_cost']:.2f} | ${p['last_price']:.2f} "
                             f"| {p['unrealized_pct']:+.1%} |")
        else:
            lines.append("_No open positions._")
        lines += ["", "## Last 24h activity"]
        for r in recent[:20]:
            payload = json.loads(r["payload"])
            brief = {k: payload[k] for k in list(payload)[:4]}
            lines.append(f"- `{r['kind']}` {r['ticker'] or ''} — "
                         f"{json.dumps(brief, default=str)[:220]}")
        report = "\n".join(lines)
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        path = reports_dir / f"daily_{time.strftime('%Y%m%d')}.md"
        Path(path).write_text(report)
        return report
