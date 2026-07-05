"""MCP server: SEC EDGAR + fund tools over stdio.

Lets any MCP client (Claude Desktop, Claude Code, an Ollama tool-calling
loop, etc.) use the fund's data layer and read its state.

Run:  python -m hedgefund.mcp_server.server
Requires the optional dependency:  pip install mcp
"""

from __future__ import annotations

import json

from ..config import Config
from ..data import EdgarClient, MarketData, NewsFeed
from ..storage import DB

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    raise SystemExit("MCP server requires the 'mcp' package: pip install mcp") from e

cfg = Config.load()
db = DB(cfg.data_dir / "fund.db")
edgar = EdgarClient(db, cfg.settings["data"]["edgar_user_agent"],
                    rps=float(cfg.settings["data"]["edgar_rps"]))
market = MarketData(db, rps=float(cfg.settings["data"]["market_rps"]))
news = NewsFeed(db)

mcp = FastMCP("hedgefund", instructions=(
    "Tools for a deep-value research fund: SEC EDGAR filings and XBRL "
    "fundamentals, market snapshots, quant screens, and the paper "
    "portfolio's live state. All data is from free public sources."))


@mcp.tool()
def edgar_lookup_cik(ticker: str) -> str:
    """Resolve a stock ticker to its SEC CIK number and registrant name."""
    entry = edgar.ticker_map().get(ticker.upper())
    return json.dumps(entry or {"error": f"unknown ticker {ticker}"})


@mcp.tool()
def edgar_recent_filings(ticker: str, forms: str = "10-K,10-Q,8-K", limit: int = 10) -> str:
    """List recent SEC filings for a ticker with direct document URLs.
    forms: comma-separated form types (e.g. '10-K,8-K')."""
    form_tuple = tuple(f.strip() for f in forms.split(",") if f.strip())
    return json.dumps(edgar.recent_filings(ticker, forms=form_tuple, limit=limit), indent=2)


@mcp.tool()
def edgar_fundamentals(ticker: str, years: int = 4) -> str:
    """Normalized annual fundamentals from SEC XBRL (revenue, net income, cash
    flow, balance sheet items, shares), newest first."""
    f = edgar.fundamentals(ticker, years=years)
    return json.dumps(f or {"error": "no XBRL facts found"}, indent=2, default=str)


@mcp.tool()
def edgar_full_text_search(query: str, forms: str = "", limit: int = 10) -> str:
    """Full-text search across SEC filings (EDGAR FTS)."""
    return json.dumps(edgar.full_text_search(query, forms=forms, limit=limit), indent=2)


@mcp.tool()
def market_snapshot(ticker: str) -> str:
    """Price, 52-week range, liquidity (ADV), momentum and volatility stats."""
    return json.dumps(market.snapshot(ticker) or {"error": "no data"}, indent=2, default=str)


@mcp.tool()
def value_scorecard(ticker: str) -> str:
    """Full deep-value scorecard: valuation ratios, Piotroski F, Altman Z,
    Beneish M, and the composite score used by the fund's screener."""
    from .. import quant
    snap = market.snapshot(ticker)
    f = edgar.fundamentals(ticker)
    if not snap or not f:
        return json.dumps({"error": "insufficient data"})
    metrics = quant.valuation_metrics(f, snap["price"], None)
    fscore = quant.piotroski_f(f)
    z = quant.altman_z(f, metrics.get("market_cap"))
    m = quant.beneish_m(f)
    return json.dumps({"ticker": ticker, "metrics": metrics, "piotroski": fscore,
                       "altman_z": z, "beneish_m": m,
                       "composite": quant.composite_value_score(metrics, fscore, z, m, snap)},
                      indent=2, default=str)


@mcp.tool()
def ticker_news(ticker: str, limit: int = 8) -> str:
    """Recent free RSS headlines for a ticker."""
    return json.dumps(news.for_ticker(ticker, limit=limit), indent=2)


@mcp.tool()
def portfolio_state() -> str:
    """The fund's live paper portfolio: NAV, cash, positions with P&L."""
    from ..portfolio import PaperBroker
    broker = PaperBroker(db, market, cfg.capital)
    nav, cash, invested = broker.nav()
    return json.dumps({"nav": nav, "cash": cash, "invested": invested,
                       "positions": broker.positions()}, indent=2, default=str)


@mcp.tool()
def recent_decisions(limit: int = 10) -> str:
    """The committee's most recent buy/reject decisions with theses."""
    rows = db.query("SELECT ts, ticker, action, conviction, target_weight, thesis "
                    "FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


if __name__ == "__main__":
    mcp.run()
