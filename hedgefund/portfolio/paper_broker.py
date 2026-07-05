"""Paper broker: realistic simulated execution against live prices.

Fills at the latest price plus a slippage haircut. Tracks cash, positions,
NAV history, drawdown state and stop-losses. This is deliberately the ONLY
component that can move money — and it only moves paper money.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..data import MarketData
from ..storage import DB

log = logging.getLogger("hedgefund.broker")


class PaperBroker:
    def __init__(self, db: DB, market: MarketData, capital: float,
                 slippage_bps: float = 10, commission: float = 0.0):
        self.db = db
        self.market = market
        self.slippage = slippage_bps / 10000.0
        self.commission = commission
        db.init_state(capital)

    # ----------------------------------------------------------------- state
    def state(self) -> dict:
        return dict(self.db.get_state())

    def positions(self, with_prices: bool = True) -> list[dict]:
        rows = [dict(r) for r in self.db.query("SELECT * FROM positions")]
        for p in rows:
            price = self.market.last_price(p["ticker"]) if with_prices else None
            p["last_price"] = price or p["avg_cost"]
            p["market_value"] = p["qty"] * p["last_price"]
            p["unrealized_pct"] = p["last_price"] / p["avg_cost"] - 1 if p["avg_cost"] else 0
        return rows

    def nav(self) -> tuple[float, float, float]:
        """(nav, cash, invested)"""
        cash = float(self.state()["cash"])
        invested = sum(p["market_value"] for p in self.positions())
        return cash + invested, cash, invested

    def trades_today(self) -> int:
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                       microsecond=0).timestamp()
        row = self.db.query("SELECT COUNT(*) AS n FROM trades WHERE ts>?", (day_start,))
        return int(row[0]["n"])

    # ------------------------------------------------------------- execution
    def buy(self, ticker: str, sector: str, weight: float, nav: float,
            decision_id: int | None) -> dict | None:
        price = self.market.last_price(ticker)
        if not price:
            log.warning("no price for %s; order dropped", ticker)
            return None
        fill = price * (1 + self.slippage)
        qty = round((weight * nav) / fill, 4)
        cost = qty * fill + self.commission
        state = self.state()
        if cost > state["cash"]:
            qty = round((state["cash"] - self.commission) / fill * 0.999, 4)
            cost = qty * fill + self.commission
        if qty <= 0:
            return None
        self.db.update_state(cash=state["cash"] - cost)
        self.db.insert("positions", ticker=ticker, sector=sector, qty=qty,
                       avg_cost=fill, opened_ts=time.time(), decision_id=decision_id)
        tid = self.db.insert("trades", ts=time.time(), decision_id=decision_id,
                             ticker=ticker, side="buy", qty=qty, price=fill,
                             slippage=fill - price, commission=self.commission)
        trade = {"trade_id": tid, "ticker": ticker, "side": "buy", "qty": qty,
                 "price": fill, "cost": cost}
        self.db.log_event("trade", trade, ticker)
        return trade

    def sell(self, ticker: str, reason: str) -> dict | None:
        pos = self.db.query("SELECT * FROM positions WHERE ticker=?", (ticker,))
        if not pos:
            return None
        pos = dict(pos[0])
        price = self.market.last_price(ticker)
        if not price:
            log.warning("no price for %s; sell deferred", ticker)
            return None
        fill = price * (1 - self.slippage)
        proceeds = pos["qty"] * fill - self.commission
        state = self.state()
        self.db.update_state(cash=state["cash"] + proceeds)
        with self.db.conn() as c:
            c.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        tid = self.db.insert("trades", ts=time.time(), decision_id=pos.get("decision_id"),
                             ticker=ticker, side="sell", qty=pos["qty"], price=fill,
                             slippage=price - fill, commission=self.commission)
        realized = fill / pos["avg_cost"] - 1 if pos["avg_cost"] else 0
        if pos.get("decision_id"):
            with self.db.conn() as c:
                c.execute("UPDATE decisions SET status='closed' WHERE id=?",
                          (pos["decision_id"],))
        trade = {"trade_id": tid, "ticker": ticker, "side": "sell", "qty": pos["qty"],
                 "price": fill, "realized_pct": round(realized, 4), "reason": reason}
        self.db.log_event("trade", trade, ticker)
        return trade

    # ---------------------------------------------------------- housekeeping
    def mark_to_market(self, benchmark: str = "SPY") -> dict:
        nav, cash, invested = self.nav()
        bench = self.market.last_price(benchmark)
        self.db.insert("nav_history", ts=time.time(), nav=nav, cash=cash,
                       invested=invested, benchmark_price=bench)
        state = self.state()
        updates: dict = {}
        if nav > float(state["peak_nav"] or 0):
            updates["peak_nav"] = nav
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if state.get("day_open_date") != today:
            updates["day_open_nav"] = nav
            updates["day_open_date"] = today
        if updates:
            self.db.update_state(**updates)
        return {"nav": nav, "cash": cash, "invested": invested, "benchmark": bench}

    def enforce_stops(self, stop_loss_pct: float, max_holding_days: int) -> list[dict]:
        """Mechanical exits that require no LLM: stop-loss and max holding period."""
        actions = []
        for p in self.positions():
            if p["unrealized_pct"] <= -abs(stop_loss_pct):
                t = self.sell(p["ticker"], f"stop-loss {p['unrealized_pct']:.1%}")
                if t:
                    actions.append(t)
            elif (time.time() - p["opened_ts"]) / 86400 > max_holding_days:
                t = self.sell(p["ticker"], "max holding period reached")
                if t:
                    actions.append(t)
        return actions
